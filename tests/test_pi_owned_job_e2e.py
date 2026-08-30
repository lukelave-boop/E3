from __future__ import annotations

import copy
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.config import LaserSettings, MachineSettings, WorkArea
from laser_aligner.machine.controller_dialects import resolve_air_assist_commands
from laser_aligner.machine.pi_job_protocol import (
    ACTION_JOB_BEGIN,
    ACTION_JOB_CHUNK,
    ACTION_JOB_FINALIZE,
    ACTION_JOB_START,
)
from laser_aligner.machine.pi_job_service import (
    PiJobService,
    canonical_program_bytes,
    execution_policy_digest,
)
from laser_aligner.machine.pi_job_store import (
    SECONDARY_RECOVERY_BINDING_FIELD,
    PiJobStore,
)
from laser_aligner.machine.pi_machine_server import PiMachineServer
from laser_aligner.machine.remote_service import RemoteMachineService
from laser_aligner.machine.secondary_controller import (
    CrealityControllerOwner,
    SecondaryMarlinFanController,
)
from laser_aligner.machine.service import MachineService
from tests.fakes.simulator_transport import SimulatedTransport

_TOKEN = "pi-owned-e2e-token-0123456789abcdef"
_GATED_COMMAND = "G1 X20 Y20 F500"
_POWERED_PROGRAM = "\n".join(
    (
        "G21",
        "G90",
        "M5",
        "M9",
        "G0 X10 Y10 F1000",
        "M8",
        "M4 S10",
        _GATED_COMMAND,
        "G1 X30 Y30 F500",
        "M5",
        "M9",
        "M5",
    )
)
_OUTPUT_POLYGON = ((0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0))
_SECONDARY_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"


class GatedRecordingTransport(SimulatedTransport):
    """Deterministic controller peer that holds one normal command ACK."""

    def __init__(self) -> None:
        super().__init__()
        self.commands: list[str] = []
        self.gated = threading.Event()
        self._pending_lock = threading.Lock()
        self._pending_line: str | None = None

    def write_line(self, line: str) -> None:
        command = line.strip().upper()
        self.commands.append(command)
        if command == _GATED_COMMAND:
            with self._pending_lock:
                self._pending_line = line
            self.gated.set()
            return
        super().write_line(line)

    def release(self, *, execute: bool = True) -> None:
        with self._pending_lock:
            pending = self._pending_line
            self._pending_line = None
        if execute and pending is not None:
            super().write_line(pending)


class ObservingPiMachineServer(PiMachineServer):
    """Capture non-secret RPC boundaries while using the production dispatcher."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.actions: list[str] = []
        self.begin_request: dict[str, Any] | None = None
        self.finalize_request: dict[str, Any] | None = None
        self.finalize_response: dict[str, Any] | None = None
        self.start_response: dict[str, Any] | None = None
        self._observation_lock = threading.Lock()

    def _dispatch(
        self,
        request: Mapping[str, Any],
        action: str,
    ) -> dict[str, Any]:
        response = super()._dispatch(request, action)
        with self._observation_lock:
            self.actions.append(action)
            if action == ACTION_JOB_BEGIN:
                self.begin_request = copy.deepcopy(dict(request))
            elif action == ACTION_JOB_FINALIZE:
                self.finalize_request = copy.deepcopy(dict(request))
                self.finalize_response = copy.deepcopy(response)
            elif action == ACTION_JOB_START:
                self.start_response = copy.deepcopy(response)
        return response


class RecordingSecondarySerial:
    """Persistent acknowledged secondary peer for Pi-ownership E2E coverage."""

    def __init__(self) -> None:
        self.open_calls = 0
        self.close_calls = 0
        self.writes: list[str] = []

    def open(self) -> None:
        self.open_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def drain(self) -> list[str]:
        return []

    def write_line(self, line: str) -> None:
        self.writes.append(line)

    def read_line(self, timeout: float = 1.0) -> str:
        assert timeout > 0.0
        return "ok"


def _machine_settings(port: str) -> MachineSettings:
    return MachineSettings(
        backend="serial",
        protocol="grbl",
        port=port,
        baudrate=115200,
        read_timeout=0.1,
        work_area=WorkArea(0, 220, 0, 220),
        photo_x=110,
        photo_y=110,
        home_before_photo=True,
        allow_motion=True,
        controller_startup_delay=0.0,
        max_travel_feed_mm_min=6000,
        max_work_feed_mm_min=6000,
        air_assist=AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT),
    )


def _wait_until(predicate: Any, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "condition did not become true before timeout"


def test_remote_service_to_pi_controller_owns_and_completes_exact_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = GatedRecordingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args, **_kwargs: transport,
    )
    monkeypatch.setenv("E3_BRIDGE_TOKEN", _TOKEN)

    laser_settings = LaserSettings(
        arm_timeout_seconds=60,
        guarded_output_polygon_mm=_OUTPUT_POLYGON,
    )
    local_machine = MachineService(
        _machine_settings("test-controller"),
        laser_settings,
        hardware_enabled=True,
        laser_lockout=False,
    )
    store = PiJobStore(tmp_path / "pi-jobs")
    job_service = PiJobService(
        local_machine,
        store,
        watch_interval_seconds=0.01,
    )
    server = ObservingPiMachineServer(
        job_service,
        host="127.0.0.1",
        port=0,
        token=_TOKEN,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    _wait_until(lambda: server._bound_port is not None)

    remote_settings = _machine_settings(
        f"e3bridge://127.0.0.1:{server.bound_port}"
    )
    remote = RemoteMachineService(
        remote_settings,
        laser_settings,
        hardware_enabled=True,
        laser_lockout=False,
        monitor_interval_seconds=0.01,
    )
    fresh_remote: RemoteMachineService | None = None
    try:
        remote.connect()
        program = remote.preflight_program(
            _POWERED_PROGRAM,
            guarded_output_polygon_mm=_OUTPUT_POLYGON,
        )
        canonical = canonical_program_bytes(program)
        policy_digest = execution_policy_digest(program)
        started = remote.start_preflighted_program(
            program,
            "e2e-powered.gcode",
            authorization_phrase=remote.ARM_PHRASE,
        )
        job_id = started["job_id"]

        assert started["accepted"] is True
        assert started["duplicate"] is False
        assert started["execution_owner"] == "pi"
        assert started["ownership_accepted"] is True
        assert isinstance(started["start_accepted_at"], float)
        assert transport.gated.wait(timeout=2.0)
        assert b"M8\n" in canonical
        assert canonical.endswith(b"M9\nM5")

        # The accepted job is Pi-owned. Losing/detaching the Windows monitor
        # after Air ON must not inject STOP or OFF into the Pi controller stream.
        air_on_index = transport.commands.index("M8")
        remote.detach()
        time.sleep(0.05)
        assert transport.commands[air_on_index:] == [
            "M8",
            "M4 S10",
            _GATED_COMMAND,
        ]
        assert b"!\x18" not in getattr(transport, "raw_writes", [])

        record = store.get(job_id)
        assert store.read_program_bytes(job_id) == canonical
        assert record["expected_size"] == len(canonical)
        assert record["received_size"] == len(canonical)
        assert record["expected_sha256"] == program.digest
        assert record["program_digest"] == program.digest
        assert record["execution_policy_digest"] == policy_digest
        assert record["requires_laser_authorization"] is True
        assert record["requires_motion"] is True
        assert record["guarded_output_polygon_mm"] == [
            [x, y] for x, y in _OUTPUT_POLYGON
        ]

        assert server.begin_request is not None
        assert server.begin_request["expected_size"] == len(canonical)
        assert server.begin_request["expected_sha256"] == program.digest
        assert server.finalize_request is not None
        assert server.finalize_request["program_digest"] == program.digest
        assert (
            server.finalize_request["execution_policy_digest"] == policy_digest
        )
        assert server.finalize_response is not None
        assert server.finalize_response["ready"] is True
        assert server.finalize_response["job"]["state"] == "prepared"
        assert server.start_response is not None
        assert server.start_response["accepted"] is True
        assert server.start_response["execution_owner"] == "pi"
        assert server.start_response["job"]["ownership_accepted"] is True
        assert isinstance(
            server.start_response["job"]["start_accepted_at"], float
        )
        assert server.actions.index(ACTION_JOB_BEGIN) < server.actions.index(
            ACTION_JOB_CHUNK
        )
        assert server.actions.index(ACTION_JOB_CHUNK) < server.actions.index(
            ACTION_JOB_FINALIZE
        )
        assert server.actions.index(ACTION_JOB_FINALIZE) < server.actions.index(
            ACTION_JOB_START
        )

        fresh_remote = RemoteMachineService(
            remote_settings,
            laser_settings,
            hardware_enabled=True,
            laser_lockout=False,
            monitor_interval_seconds=0.01,
        )
        fresh_remote.start_monitoring()
        _wait_until(
            lambda: fresh_remote.status()["job"].get("job_id") == job_id
            and fresh_remote.status()["job"]["state"] == "running"
        )

        transport.release()
        _wait_until(lambda: store.get(job_id)["state"] == "complete")
        assert "M9" in transport.commands[air_on_index:]
        assert transport.commands.index("M9", air_on_index) > transport.commands.index(
            _GATED_COMMAND,
            air_on_index,
        )
        _wait_until(
            lambda: fresh_remote.status()["job"].get("job_id") == job_id
            and fresh_remote.status()["job"]["state"] == "complete"
        )
        discovered = fresh_remote.status()["job"]
        assert discovered["ownership_accepted"] is True
        assert discovered["execution_owner"] == "pi"
        assert discovered["program_digest"] == program.digest
        assert (
            fresh_remote.successful_job_receipt(
                program.digest,
                not_before=0.0,
            )
            is None
        )
    finally:
        if fresh_remote is not None:
            fresh_remote.detach()
        remote.detach()
        server.stop()
        transport.release(execute=False)
        job_service.shutdown(stop_machine=True)
        server_thread.join(timeout=2.0)


def test_windows_detach_does_not_change_pi_owned_secondary_fan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = GatedRecordingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args, **_kwargs: primary,
    )
    monkeypatch.setenv("E3_BRIDGE_TOKEN", _TOKEN)

    air_settings = AirAssistSettings(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN,
        port=_SECONDARY_PORT,
        baudrate=115200,
    )
    binding = resolve_air_assist_commands(air_settings, protocol="grbl")
    assert binding is not None
    secondary_serial = RecordingSecondarySerial()
    owner = CrealityControllerOwner(
        _SECONDARY_PORT,
        115200,
        serial_factory=lambda _port, _baudrate: secondary_serial,
        sleep=lambda _delay: None,
        startup_delay_seconds=0.0,
        read_timeout_seconds=0.1,
    )
    fan = SecondaryMarlinFanController(owner, binding)
    secondary_program = "\n".join(
        (
            "G21",
            "G90",
            "M5",
            *binding.program_lines(False),
            "G0 X10 Y10 F1000",
            *binding.program_lines(True),
            "M4 S10",
            _GATED_COMMAND,
            "G1 X30 Y30 F500",
            "M5",
            *binding.program_lines(False),
            "M5",
        )
    )
    laser_settings = LaserSettings(
        arm_timeout_seconds=60,
        guarded_output_polygon_mm=_OUTPUT_POLYGON,
    )
    local_settings = _machine_settings("test-controller")
    local_settings.air_assist = air_settings
    local_machine = MachineService(
        local_settings,
        laser_settings,
        hardware_enabled=True,
        laser_lockout=False,
        secondary_air_assist=fan,
    )
    job_service = PiJobService(
        local_machine,
        PiJobStore(tmp_path / "pi-secondary-jobs"),
        watch_interval_seconds=0.01,
    )
    server = PiMachineServer(
        job_service,
        host="127.0.0.1",
        port=0,
        token=_TOKEN,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    _wait_until(lambda: server._bound_port is not None)

    remote_settings = _machine_settings(
        f"e3bridge://127.0.0.1:{server.bound_port}"
    )
    remote_settings.air_assist = AirAssistSettings(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN,
        port=_SECONDARY_PORT,
        baudrate=115200,
    )
    remote = RemoteMachineService(
        remote_settings,
        laser_settings,
        hardware_enabled=True,
        laser_lockout=False,
        monitor_interval_seconds=0.01,
    )
    try:
        remote.connect()
        program = remote.preflight_program(
            secondary_program,
            guarded_output_polygon_mm=_OUTPUT_POLYGON,
        )
        started = remote.start_preflighted_program(
            program,
            "e2e-secondary-air.gcode",
            authorization_phrase=remote.ARM_PHRASE,
        )
        job_id = started["job_id"]
        assert started["accepted"] is True
        assert SECONDARY_RECOVERY_BINDING_FIELD not in started
        assert primary.gated.wait(timeout=2.0)
        assert (
            job_service.store.get(job_id)[SECONDARY_RECOVERY_BINDING_FIELD]
            is not None
        )
        assert secondary_serial.writes == ["M106 S0", "M106 S255"]
        assert all(not line.startswith("E3AIRASSIST") for line in primary.commands)
        assert all("M106" not in line and "M107" not in line for line in primary.commands)

        primary_before_detach = list(primary.commands)
        secondary_before_detach = list(secondary_serial.writes)
        remote.detach()
        time.sleep(0.05)
        assert primary.commands == primary_before_detach
        assert secondary_serial.writes == secondary_before_detach
        assert b"!\x18" not in getattr(primary, "raw_writes", [])

        primary.release()
        _wait_until(lambda: job_service.store.get(job_id)["state"] == "complete")
        _wait_until(
            lambda: job_service.store.get(job_id)[
                SECONDARY_RECOVERY_BINDING_FIELD
            ]
            is None
        )
        assert secondary_serial.writes == [
            "M106 S0",
            "M106 S255",
            "M106 S0",
        ]
    finally:
        remote.detach()
        server.stop()
        primary.release(execute=False)
        job_service.shutdown(stop_machine=True)
        fan.close()
        server_thread.join(timeout=2.0)
