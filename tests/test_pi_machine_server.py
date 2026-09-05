from __future__ import annotations

import logging
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.app import AppContext
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.service import FrameBurst
from laser_aligner.config import (
    LaserSettings,
    MachineSettings,
    PrecisionCaptureSettings,
    WorkArea,
)
from laser_aligner.errors import MachineError
from laser_aligner.machine.controller_dialects import resolve_air_assist_commands
from laser_aligner.machine.pi_job_protocol import (
    ACTION_JOB_ACTIVE,
    ACTION_JOB_BEGIN,
    ACTION_JOB_CHUNK,
    ACTION_JOB_FINALIZE,
    ACTION_JOB_RESULT,
    ACTION_JOB_START,
    ACTION_JOB_STATUS,
    ACTION_JOB_STOP,
    CAPABILITY_PI_COHERENT_STATUS,
    CAPABILITY_PI_CONTROLLER_SESSION,
    CAPABILITY_PI_EXECUTION_POLICY_DIAGNOSTICS,
    CAPABILITY_PI_STRUCTURED_ERRORS,
    ERROR_CONTROLLER_BUSY,
    ERROR_CONTROLLER_REJECTED,
    ERROR_CONTROLLER_STALE_SESSION,
    ERROR_SERVICE_SHUTTING_DOWN,
    authenticate_client,
    encode_upload_chunk,
)
from laser_aligner.machine.pi_job_service import (
    EXECUTION_POLICY_FIELD_LABELS,
    EXECUTION_POLICY_MISMATCH_ERROR,
    PiJobService,
    PiJobServiceError,
    canonical_program_bytes,
    execution_policy_diagnostic_profile,
    execution_policy_digest,
    execution_policy_mismatch_labels,
)
from laser_aligner.machine.pi_job_store import PiJobStore
from laser_aligner.machine.pi_machine_server import (
    ACTION_JOB_LATEST,
    ACTION_MACHINE_COMMAND,
    ACTION_MACHINE_CONNECT,
    ACTION_MACHINE_DISCONNECT,
    ACTION_MACHINE_JOG,
    ACTION_MACHINE_PREPARE_JOB_START,
    ACTION_MACHINE_PREPARE_PHOTO_POSITION,
    ACTION_MACHINE_REPLACE_CONNECTION,
    ACTION_MACHINE_STATUS,
    ACTION_MACHINE_STEPPER_HOLD,
    ACTION_MACHINE_STEPPER_HOLD_RELEASE,
    ACTION_SERVICE_CAPABILITIES,
    SERVER_CAPABILITIES,
    PiMachineServer,
)
from laser_aligner.machine.remote_service import RemoteMachineService
from laser_aligner.machine.service import MachineService, ValidatedProgram
from tests.fakes.simulator_transport import SimulatedTransport

_TOKEN = "pi-machine-test-token-0123456789abcdef"
_POWERED_PROGRAM = "\n".join(
    (
        "G21",
        "G90",
        "M5",
        "G0 X10 Y10 F1000",
        "M4 S5",
        "G1 X20 Y20 F500",
        "G1 X30 Y30 F500",
        "M5",
    )
)
_GATED_COMMAND = "G1 X20 Y20 F500"
_LATER_COMMAND = "G1 X30 Y30 F500"


class RecordingGatedTransport(SimulatedTransport):
    def __init__(self, *, fail_gated_write: bool = False) -> None:
        super().__init__()
        self.commands: list[str] = []
        self.raw_writes: list[bytes] = []
        self.gated = threading.Event()
        self._lock = threading.Lock()
        self._pending: str | None = None
        self._fail_gated_write = fail_gated_write
        self._failure_release = threading.Event()

    def write_line(self, line: str) -> None:
        command = line.strip().upper()
        self.commands.append(command)
        if command == _GATED_COMMAND:
            self.gated.set()
            if self._fail_gated_write:
                self._failure_release.wait(timeout=2.0)
                raise MachineError("simulated Pi-to-controller write failure")
            with self._lock:
                self._pending = line
            return
        super().write_line(line)

    def write_raw(self, data: bytes) -> None:
        self.raw_writes.append(bytes(data))
        super().write_raw(data)

    def release(self, *, execute: bool = True) -> None:
        self._failure_release.set()
        with self._lock:
            pending = self._pending
            self._pending = None
        if execute and pending is not None:
            super().write_line(pending)


class JobFailureTransport(RecordingGatedTransport):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode
        self._fail_next_read = False

    def write_line(self, line: str) -> None:
        command = line.strip().upper()
        if command != _GATED_COMMAND:
            super().write_line(line)
            return
        self.commands.append(command)
        self.gated.set()
        self._failure_release.wait(timeout=2.0)
        if self.mode == "write":
            raise MachineError("simulated Pi-to-controller write failure")
        if self.mode == "read":
            self._fail_next_read = True
        elif self.mode == "error":
            self._queue.put("error:9")
        elif self.mode == "alarm":
            self._queue.put("ALARM:1")
        elif self.mode != "timeout":
            raise AssertionError(f"unsupported failure mode: {self.mode}")

    def read_line(self, timeout: float = 1.0) -> str | None:
        if self._fail_next_read:
            self._fail_next_read = False
            raise MachineError("simulated Pi-to-controller read failure")
        return super().read_line(timeout)


@dataclass(slots=True)
class ServerHarness:
    machine: MachineService
    service: PiJobService
    server: PiMachineServer
    transport: RecordingGatedTransport
    thread: threading.Thread


def _wait_until(predicate: Any, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "condition did not become true before timeout"


def _machine(
    transport: SimulatedTransport,
    *,
    powered_completion: bool = False,
) -> MachineService:
    return MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            port="test-controller",
            baudrate=115200,
            read_timeout=0.1,
            work_area=WorkArea(0, 220, 0, 220),
            photo_x=110,
            photo_y=110,
            home_before_photo=True,
            home_and_release_after_powered_job=powered_completion,
            allow_motion=True,
            controller_startup_delay=0.0,
            max_travel_feed_mm_min=6000,
            max_work_feed_mm_min=6000,
        ),
        LaserSettings(arm_timeout_seconds=60),
        hardware_enabled=True,
        laser_lockout=False,
    )


@pytest.fixture
def server_harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ServerHarness:
    transport = RecordingGatedTransport()
    harness = _new_harness(tmp_path, monkeypatch, transport)
    try:
        yield harness
    finally:
        _close_harness(harness)


def _new_harness(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: RecordingGatedTransport,
    *,
    powered_completion: bool = False,
) -> ServerHarness:
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args, **_kwargs: transport,
    )
    machine = _machine(transport, powered_completion=powered_completion)
    service = PiJobService(
        machine,
        PiJobStore(root / "jobs"),
        watch_interval_seconds=0.01,
    )
    server = PiMachineServer(
        service,
        host="127.0.0.1",
        port=0,
        token=_TOKEN,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until(lambda: server._bound_port is not None)
    return ServerHarness(machine, service, server, transport, thread)


def _close_harness(harness: ServerHarness) -> None:
    harness.server.stop()
    harness.transport.release(execute=False)
    harness.service.shutdown(stop_machine=True)
    harness.thread.join(timeout=2.0)


def _rpc(
    harness: ServerHarness,
    action: str,
    *,
    request_id: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    session_actions = {
        ACTION_MACHINE_CONNECT,
        ACTION_MACHINE_REPLACE_CONNECTION,
        ACTION_MACHINE_DISCONNECT,
        ACTION_MACHINE_PREPARE_PHOTO_POSITION,
        ACTION_MACHINE_PREPARE_JOB_START,
        ACTION_MACHINE_JOG,
        ACTION_MACHINE_COMMAND,
        ACTION_MACHINE_STEPPER_HOLD,
        ACTION_JOB_START,
    }
    if action in session_actions:
        fields.setdefault("client_id", "00000000-0000-4000-8000-000000000001")
        fields.setdefault("expected_boot_id", harness.service.boot_id)
        fields.setdefault(
            "expected_session_generation",
            harness.machine.status()["controller_session_generation"],
        )
    with socket.create_connection(
        ("127.0.0.1", harness.server.bound_port),
        timeout=3.0,
    ) as sock:
        sock.settimeout(5.0)
        channel = authenticate_client(sock, _TOKEN)
        channel.send_json(
            {
                "action": action,
                "request_id": request_id or str(uuid.uuid4()),
                **fields,
            }
        )
        return channel.receive_json()


def _binding(program: ValidatedProgram) -> dict[str, Any]:
    return {
        "program_digest": program.digest,
        "requires_laser_authorization": program.requires_laser_authorization,
        "requires_motion": program.requires_motion,
        "guarded_output_polygon_mm": (
            None
            if program.guarded_output_polygon_mm is None
            else [list(point) for point in program.guarded_output_polygon_mm]
        ),
        "execution_policy_digest": execution_policy_digest(program),
    }


def _service_binding(program: ValidatedProgram) -> dict[str, Any]:
    binding = _binding(program)
    binding["policy_digest"] = binding.pop("execution_policy_digest")
    return binding


def _upload_without_finalize(
    harness: ServerHarness,
    program: ValidatedProgram,
) -> str:
    identifier = str(uuid.uuid4())
    payload = canonical_program_bytes(program)
    assert _rpc(
        harness,
        ACTION_JOB_BEGIN,
        job_id=identifier,
        name="policy-diagnostic-test.gcode",
        expected_size=len(payload),
        expected_sha256=program.digest,
        guarded_output_polygon_mm=None,
    )["ok"] is True
    assert _rpc(
        harness,
        ACTION_JOB_CHUNK,
        job_id=identifier,
        offset=0,
        data_b64=encode_upload_chunk(payload),
    )["ok"] is True
    return identifier


def _upload(
    harness: ServerHarness,
    *,
    job_id: str | None = None,
) -> tuple[str, ValidatedProgram, dict[str, Any]]:
    status = harness.machine.status()
    if not status["connected"]:
        assert _rpc(harness, ACTION_MACHINE_CONNECT)["ok"] is True
        status = harness.machine.status()
    if status["controller_state"] == "READY_HOME_REQUIRED":
        assert _rpc(harness, ACTION_MACHINE_PREPARE_JOB_START)["ok"] is True
    identifier = job_id or str(uuid.uuid4())
    program = harness.machine.preflight_program(_POWERED_PROGRAM)
    payload = canonical_program_bytes(program)
    begin = _rpc(
        harness,
        ACTION_JOB_BEGIN,
        job_id=identifier,
        name="pi-owned-test.gcode",
        expected_size=len(payload),
        expected_sha256=program.digest,
        guarded_output_polygon_mm=None,
    )
    assert begin["ok"] is True
    chunk = _rpc(
        harness,
        ACTION_JOB_CHUNK,
        job_id=identifier,
        offset=0,
        data_b64=encode_upload_chunk(payload),
    )
    assert chunk["ok"] is True
    finalized = _rpc(
        harness,
        ACTION_JOB_FINALIZE,
        job_id=identifier,
        **_binding(program),
    )
    assert finalized["ok"] is True
    assert finalized["ready"] is True
    assert finalized["job"]["state"] == "prepared"
    assert finalized["verification_seconds"] >= 0.0
    return identifier, program, finalized


def _start_fields(job_id: str, program: ValidatedProgram) -> dict[str, Any]:
    return {
        "job_id": job_id,
        **_binding(program),
        "authorization_phrase": MachineService.ARM_PHRASE,
    }


def test_execution_policy_diagnostic_manifest_has_fixed_labels_and_shape() -> None:
    assert len(EXECUTION_POLICY_FIELD_LABELS) == 25
    assert EXECUTION_POLICY_FIELD_LABELS[8] == "machine.work_area.x_max"
    assert EXECUTION_POLICY_FIELD_LABELS[24] == "air_assist.mapping"

    profile_24 = tuple(range(24))
    profile_25 = tuple(range(25))
    assert execution_policy_mismatch_labels(profile_24, profile_25) == (
        "air_assist.mapping",
    )

    changed = list(profile_25)
    changed[8] = "different"
    changed[24] = None
    assert execution_policy_mismatch_labels(profile_25, changed) == (
        "machine.work_area.x_max",
        "air_assist.mapping",
    )


def test_execution_policy_diagnostic_labels_follow_machine_safety_profile_order() -> None:
    program_text = "\n".join(("G21", "G90", "M5"))
    guarded_polygon = (
        (10.0, 10.0),
        (210.0, 10.0),
        (210.0, 210.0),
        (10.0, 210.0),
    )
    mutations = (
        ("machine.protocol", "settings.protocol", "marlin"),
        ("machine.allow_motion", "settings.allow_motion", False),
        ("process.hardware_enabled", "hardware_enabled", False),
        ("process.laser_lockout", "laser_lockout", True),
        ("machine.home_before_photo", "settings.home_before_photo", False),
        (
            "machine.home_and_release_after_powered_job",
            "settings.home_and_release_after_powered_job",
            True,
        ),
        ("machine.work_area.x_min", "settings.work_area.x_min", 1.0),
        ("machine.work_area.x_max", "settings.work_area.x_max", 219.0),
        ("machine.work_area.y_min", "settings.work_area.y_min", 2.0),
        ("machine.work_area.y_max", "settings.work_area.y_max", 218.0),
        (
            "laser.boundary_margin_mm",
            "laser_settings.boundary_margin_mm",
            1.0,
        ),
        (
            "laser.spot_offset_x_mm",
            "laser_settings.spot_offset_x_mm",
            1.5,
        ),
        (
            "laser.spot_offset_y_mm",
            "laser_settings.spot_offset_y_mm",
            2.5,
        ),
        ("laser.power_max", "laser_settings.power_max", 999),
        (
            "machine.max_travel_feed_mm_min",
            "settings.max_travel_feed_mm_min",
            5900.0,
        ),
        (
            "machine.max_work_feed_mm_min",
            "settings.max_work_feed_mm_min",
            5800.0,
        ),
        (
            "laser.travel_feed_mm_min",
            "laser_settings.travel_feed_mm_min",
            2900.0,
        ),
        (
            "laser.arm_timeout_seconds",
            "laser_settings.arm_timeout_seconds",
            61,
        ),
        ("machine.photo_x", "settings.photo_x", 111.0),
        ("machine.photo_y", "settings.photo_y", 112.0),
        ("machine.photo_z", "settings.photo_z", 12.0),
        (
            "laser.configured_guarded_output_polygon",
            "laser_settings.guarded_output_polygon_mm",
            guarded_polygon,
        ),
        (
            "air_assist.mapping",
            "settings.air_assist",
            AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT),
        ),
    )
    mutation_labels = tuple(label for label, _path, _value in mutations)
    assert EXECUTION_POLICY_FIELD_LABELS == (
        "machine.backend",
        *mutation_labels[:-1],
        "job.guarded_output_polygon",
        mutation_labels[-1],
    )

    baseline = _machine(SimulatedTransport()).preflight_program(
        program_text
    ).safety_profile
    backend_index = EXECUTION_POLICY_FIELD_LABELS.index("machine.backend")
    assert baseline[backend_index] == "serial"
    assert tuple(index for index, value in enumerate(baseline) if value == "serial") == (
        backend_index,
    )

    for expected_label, attribute_path, replacement in mutations:
        changed_machine = _machine(SimulatedTransport())
        target: object = changed_machine
        path_parts = attribute_path.split(".")
        for part in path_parts[:-1]:
            target = getattr(target, part)
        setattr(target, path_parts[-1], replacement)
        changed = changed_machine.preflight_program(program_text).safety_profile
        assert execution_policy_mismatch_labels(baseline, changed) == (
            expected_label,
        )

    polygon_machine = _machine(SimulatedTransport())
    polygon_machine.laser_settings.guarded_output_polygon_mm = guarded_polygon
    configured_only = polygon_machine.preflight_program(program_text).safety_profile
    configured_and_requested = polygon_machine.preflight_program(
        program_text,
        guarded_output_polygon_mm=guarded_polygon,
    ).safety_profile
    assert execution_policy_mismatch_labels(
        configured_only,
        configured_and_requested,
    ) == ("job.guarded_output_polygon",)


def test_server_advertises_policy_diagnostics_capability() -> None:
    assert CAPABILITY_PI_EXECUTION_POLICY_DIAGNOSTICS in SERVER_CAPABILITIES


def test_server_advertises_strict_session_contract_and_response_metadata(
    server_harness: ServerHarness,
) -> None:
    response = _rpc(server_harness, ACTION_SERVICE_CAPABILITIES)

    assert response["ok"] is True
    assert CAPABILITY_PI_CONTROLLER_SESSION in response["capabilities"]
    assert CAPABILITY_PI_STRUCTURED_ERRORS in response["capabilities"]
    assert CAPABILITY_PI_COHERENT_STATUS in response["capabilities"]
    assert response["boot_id"] == server_harness.service.boot_id
    assert set(response["build"]) == {"version", "revision"}
    assert isinstance(response["state_revision"], int)
    assert isinstance(response["controller_session_generation"], int)
    assert response["controller_state"].isupper()

    snapshot = _rpc(server_harness, ACTION_MACHINE_STATUS)
    assert set(snapshot) >= {
        "status",
        "active_job",
        "latest_job",
        "boot_id",
        "state_revision",
        "controller_session_generation",
        "controller_state",
    }


def _session_fields(harness: ServerHarness) -> dict[str, Any]:
    return {
        "client_id": "00000000-0000-4000-8000-000000000001",
        "expected_boot_id": harness.service.boot_id,
        "expected_session_generation": harness.machine.status()[
            "controller_session_generation"
        ],
    }


def test_two_clients_share_one_replacement_and_stale_disconnect_is_rejected(
    server_harness: ServerHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = _rpc(server_harness, ACTION_MACHINE_CONNECT)
    initial_generation = connected["controller_session_generation"]
    server_harness.machine.request_stop(emergency=False, _recover=False)
    server_harness.service._refresh_machine_status()
    original_replace = server_harness.machine.replace_connection
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def blocking_replace() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2.0)
        return original_replace()

    monkeypatch.setattr(
        server_harness.machine,
        "replace_connection",
        blocking_replace,
    )
    results: list[dict[str, Any]] = []

    def replace_from_client() -> None:
        results.append(
            _rpc(
                server_harness,
                ACTION_MACHINE_REPLACE_CONNECTION,
                client_id=str(uuid.uuid4()),
                expected_boot_id=server_harness.service.boot_id,
                expected_session_generation=initial_generation,
            )
        )

    first = threading.Thread(target=replace_from_client)
    second = threading.Thread(target=replace_from_client)
    first.start()
    assert entered.wait(1.0)
    flight = server_harness.service._lifecycle_flight
    assert flight is not None
    second.start()
    assert flight.join_observed.wait(1.0)
    release.set()
    first.join(3.0)
    second.join(3.0)

    assert not first.is_alive() and not second.is_alive()
    assert calls == 1
    assert len(results) == 2
    generations = {item["controller_session_generation"] for item in results}
    assert len(generations) == 1
    successor_generation = generations.pop()
    assert successor_generation > initial_generation

    stale = _rpc(
        server_harness,
        ACTION_MACHINE_DISCONNECT,
        client_id=str(uuid.uuid4()),
        expected_boot_id=server_harness.service.boot_id,
        expected_session_generation=initial_generation,
    )
    assert stale["ok"] is False
    assert stale["error_code"] == ERROR_CONTROLLER_STALE_SESSION
    assert stale["retryable"] is True
    current = _rpc(server_harness, ACTION_MACHINE_STATUS)
    assert current["status"]["connected"] is True
    assert current["controller_session_generation"] == successor_generation

    stale_boot = _rpc(
        server_harness,
        ACTION_MACHINE_DISCONNECT,
        client_id=str(uuid.uuid4()),
        expected_boot_id=str(uuid.uuid4()),
        expected_session_generation=successor_generation,
    )
    assert stale_boot["ok"] is False
    assert stale_boot["error_code"] == ERROR_CONTROLLER_STALE_SESSION
    assert _rpc(server_harness, ACTION_MACHINE_STATUS)["status"]["connected"] is True


def test_shutdown_publishes_terminal_state_and_rejects_new_physical_work(
    server_harness: ServerHarness,
) -> None:
    assert _rpc(server_harness, ACTION_MACHINE_CONNECT)["ok"] is True

    server_harness.service.shutdown(stop_machine=True)

    snapshot = _rpc(server_harness, ACTION_MACHINE_STATUS)
    assert snapshot["controller_state"] == "SHUTTING_DOWN"
    assert snapshot["status"]["controller_state"] == "SHUTTING_DOWN"
    rejected = _rpc(server_harness, ACTION_MACHINE_CONNECT)
    assert rejected["ok"] is False
    assert rejected["error_code"] == ERROR_SERVICE_SHUTTING_DOWN
    assert rejected["action_required"] == "restart_service"


def test_reserved_admission_remains_available_for_stop(
    server_harness: ServerHarness,
) -> None:
    acquired = 0
    try:
        for _ in range(16):
            assert server_harness.server._slots.acquire(blocking=False)
            acquired += 1
        stopped = _rpc(server_harness, ACTION_JOB_STOP)
        assert stopped["ok"] is True
    finally:
        for _ in range(acquired):
            server_harness.server._slots.release()


def test_two_clients_share_one_lifecycle_failure(
    server_harness: ServerHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected = _rpc(server_harness, ACTION_MACHINE_CONNECT)
    initial_generation = connected["controller_session_generation"]
    server_harness.machine.request_stop(emergency=False, _recover=False)
    server_harness.service._refresh_machine_status()
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def failing_replace() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(2.0)
        raise MachineError("shared replacement failure")

    monkeypatch.setattr(
        server_harness.machine,
        "replace_connection",
        failing_replace,
    )
    results: list[dict[str, Any]] = []

    def replace_from_client() -> None:
        results.append(
            _rpc(
                server_harness,
                ACTION_MACHINE_REPLACE_CONNECTION,
                client_id=str(uuid.uuid4()),
                expected_boot_id=server_harness.service.boot_id,
                expected_session_generation=initial_generation,
            )
        )

    first = threading.Thread(target=replace_from_client)
    second = threading.Thread(target=replace_from_client)
    first.start()
    assert entered.wait(1.0)
    flight = server_harness.service._lifecycle_flight
    assert flight is not None
    second.start()
    assert flight.join_observed.wait(1.0)
    release.set()
    first.join(3.0)
    second.join(3.0)

    assert calls == 1
    assert len(results) == 2
    assert {item["error"] for item in results} == {"shared replacement failure"}
    assert {item["error_code"] for item in results} == {
        ERROR_CONTROLLER_REJECTED
    }


def test_stop_tombstones_prepared_job_before_delayed_start(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _finalized = _upload(server_harness)

    stopped = _rpc(
        server_harness,
        ACTION_JOB_STOP,
        job_id=job_id,
    )
    assert stopped["ok"] is True
    assert stopped["job"]["state"] == "failed"
    assert "STOP" in stopped["job"]["error"]

    delayed_start = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(job_id, program),
    )
    assert delayed_start["ok"] is True
    assert delayed_start["accepted"] is False
    assert delayed_start["duplicate"] is True
    assert delayed_start["job"]["state"] == "failed"
    assert not server_harness.transport.gated.is_set()


def test_finalize_policy_mismatch_logs_only_fixed_field_labels(
    server_harness: ServerHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    program = server_harness.machine.preflight_program(_POWERED_PROGRAM)
    job_id = _upload_without_finalize(server_harness, program)
    foreign_profile = list(program.safety_profile)
    foreign_profile[8] = float(foreign_profile[8]) + 1.0
    foreign_program = replace(program, safety_profile=tuple(foreign_profile))
    binding = _binding(foreign_program)
    binding["execution_policy_diagnostic"] = execution_policy_diagnostic_profile(
        foreign_program
    )
    caplog.set_level(logging.WARNING, logger="laser_aligner.machine.pi_job_service")

    rejected = _rpc(
        server_harness,
        ACTION_JOB_FINALIZE,
        job_id=job_id,
        **binding,
    )

    assert rejected["ok"] is False
    assert rejected["error"] == EXECUTION_POLICY_MISMATCH_ERROR
    mismatch_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("execution policy mismatch:")
    ]
    assert mismatch_messages == [
        "execution policy mismatch: machine.work_area.x_max"
    ]
    assert server_harness.machine.status()["connected"] is False
    assert server_harness.transport.commands == []


@pytest.mark.parametrize("malformation", ("unbound", "short", "client_label"))
def test_finalize_rejects_malformed_or_unbound_policy_diagnostic_without_leakage(
    server_harness: ServerHarness,
    caplog: pytest.LogCaptureFixture,
    malformation: str,
) -> None:
    program = server_harness.machine.preflight_program(_POWERED_PROGRAM)
    job_id = _upload_without_finalize(server_harness, program)
    diagnostic = execution_policy_diagnostic_profile(program)
    secret = "DO-NOT-LOG-DIAGNOSTIC-VALUE"
    if malformation == "unbound":
        diagnostic["profile"][0] = secret
    elif malformation == "short":
        diagnostic["profile"].pop()
    else:
        diagnostic[f"untrusted-{secret}"] = "untrusted"
    binding = _binding(program)
    binding["execution_policy_diagnostic"] = diagnostic
    caplog.set_level(logging.DEBUG)

    rejected = _rpc(
        server_harness,
        ACTION_JOB_FINALIZE,
        job_id=job_id,
        **binding,
    )

    assert rejected["ok"] is False
    assert rejected["error"] == (
        "Execution-policy diagnostic profile is malformed or is not bound "
        "to its digest"
    )
    assert secret not in rejected["error"]
    assert secret not in caplog.text
    assert "execution policy mismatch:" not in caplog.text
    assert server_harness.machine.status()["connected"] is False
    assert server_harness.transport.commands == []


def test_start_policy_drift_logs_field_label_before_any_controller_write(
    server_harness: ServerHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job_id, program, _ = _upload(server_harness)
    server_harness.machine.settings.max_work_feed_mm_min += 1.0
    fields = _start_fields(job_id, program)
    fields["execution_policy_diagnostic"] = execution_policy_diagnostic_profile(
        program
    )
    caplog.set_level(logging.WARNING, logger="laser_aligner.machine.pi_job_service")

    commands_before = list(server_harness.transport.commands)
    rejected = _rpc(server_harness, ACTION_JOB_START, **fields)

    assert rejected["ok"] is False
    assert rejected["error"] == EXECUTION_POLICY_MISMATCH_ERROR
    mismatch_messages = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("execution policy mismatch:")
    ]
    assert mismatch_messages == [
        "execution policy mismatch: machine.max_work_feed_mm_min"
    ]
    assert server_harness.service.get(job_id)["state"] == "failed"
    assert server_harness.machine.status()["connected"] is True
    assert server_harness.transport.commands == commands_before


def test_start_rejects_unbound_policy_diagnostic_before_controller_write(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _ = _upload(server_harness)
    fields = _start_fields(job_id, program)
    diagnostic = execution_policy_diagnostic_profile(program)
    diagnostic["profile"][0] = "unbound-client-value"
    fields["execution_policy_diagnostic"] = diagnostic

    commands_before = list(server_harness.transport.commands)
    rejected = _rpc(server_harness, ACTION_JOB_START, **fields)

    assert rejected["ok"] is False
    assert rejected["error"] == (
        "Execution-policy diagnostic profile is malformed or is not bound "
        "to its digest"
    )
    assert server_harness.service.get(job_id)["state"] == "prepared"
    assert server_harness.machine.status()["connected"] is True
    assert server_harness.transport.commands == commands_before


def test_disconnect_mid_upload_remains_receiving_inert_and_unstartable(
    server_harness: ServerHarness,
) -> None:
    program = server_harness.machine.preflight_program(_POWERED_PROGRAM)
    payload = canonical_program_bytes(program)
    job_id = str(uuid.uuid4())
    assert _rpc(
        server_harness,
        ACTION_JOB_BEGIN,
        job_id=job_id,
        name="partial.gcode",
        expected_size=len(payload),
        expected_sha256=program.digest,
    )["ok"] is True

    # Each authenticated upload operation has its own socket. Closing after a
    # partial chunk must not turn that durable partial artifact into authority.
    assert _rpc(
        server_harness,
        ACTION_JOB_CHUNK,
        job_id=job_id,
        offset=0,
        data_b64=encode_upload_chunk(payload[:5]),
    )["ok"] is True
    status = _rpc(server_harness, ACTION_JOB_STATUS, job_id=job_id)
    assert status["job"]["state"] == "receiving"
    assert status["job"]["received_size"] == 5

    rejected = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(job_id, program),
    )
    assert rejected["ok"] is False
    assert server_harness.machine.status()["connected"] is False


def test_prepared_disconnect_and_unknown_start_remain_inert(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _ = _upload(server_harness)
    time.sleep(0.05)
    prepared = _rpc(server_harness, ACTION_JOB_STATUS, job_id=job_id)
    assert prepared["job"]["state"] == "prepared"
    assert _rpc(server_harness, ACTION_JOB_ACTIVE)["job"] is None
    assert _rpc(server_harness, ACTION_JOB_LATEST)["job"] is None
    assert server_harness.machine.status()["connected"] is True
    commands_before = list(server_harness.transport.commands)

    unknown = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(str(uuid.uuid4()), program),
    )
    assert unknown["ok"] is False
    assert server_harness.machine.status()["connected"] is True
    assert server_harness.transport.commands == commands_before


def test_authenticated_client_disconnect_does_not_stop_accepted_powered_job(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _ = _upload(server_harness)

    # _rpc closes the authenticated START socket immediately after the ACK.
    started = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(job_id, program),
    )
    assert started["ok"] is True
    assert started["accepted"] is True
    assert started["execution_owner"] == "pi"
    assert started["job"]["ownership_accepted"] is True
    assert started["start_latency_seconds"] >= 0.0
    assert server_harness.transport.gated.wait(timeout=2.0)
    _wait_until(
        lambda: server_harness.service.get(job_id)["completed_lines"] == 5
    )

    reattached = _rpc(server_harness, ACTION_JOB_ACTIVE)
    assert reattached["ok"] is True
    assert reattached["job"]["job_id"] == job_id
    assert reattached["job"]["program_digest"] == program.digest
    assert reattached["job"]["state"] == "running"
    assert reattached["job"]["completed_lines"] == 5
    assert reattached["job"]["total_lines"] == len(program.lines)
    assert b"!\x18" not in server_harness.transport.raw_writes
    assert server_harness.transport.commands[-1] == _GATED_COMMAND

    status = _rpc(server_harness, ACTION_MACHINE_STATUS)
    assert status["status"]["execution_owner"] == "pi"
    assert status["status"]["boot_id"] == server_harness.service.boot_id
    assert status["status"]["monitoring_requests_in_flight"] >= 1
    assert "log" not in status["status"]
    assert "arm_phrase" not in status["status"]
    assert server_harness.transport.commands[-1] == _GATED_COMMAND

    server_harness.transport.release()
    _wait_until(lambda: server_harness.service.get(job_id)["state"] == "complete")
    result = _rpc(server_harness, ACTION_JOB_RESULT, job_id=job_id)
    assert result["ok"] is True
    assert result["job"]["state"] == "complete"
    latest = _rpc(server_harness, ACTION_JOB_LATEST)
    assert latest["job"]["job_id"] == job_id
    assert latest["job"]["state"] == "complete"
    assert latest["job"]["ownership_accepted"] is True
    assert result["job"]["completed_lines"] == len(program.lines)
    assert result["job"]["total_lines"] == len(program.lines)
    gated_index = server_harness.transport.commands.index(_GATED_COMMAND)
    assert server_harness.transport.commands[gated_index:] == [
        _GATED_COMMAND,
        _LATER_COMMAND,
        "M5",
        "M5",
        "G4 P0.01",
    ]
    assert b"!\x18" not in server_harness.transport.raw_writes


def test_stop_after_reattach_bypasses_job_ack_wait(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _ = _upload(server_harness)
    started = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(job_id, program),
    )
    assert started["accepted"] is True
    assert server_harness.transport.gated.wait(timeout=2.0)
    assert _rpc(server_harness, ACTION_JOB_ACTIVE)["job"]["job_id"] == job_id

    stopped = _rpc(
        server_harness,
        ACTION_JOB_STOP,
        job_id=job_id,
        emergency=True,
    )
    assert stopped["ok"] is True
    assert stopped["job"]["state"] in {"running", "stopping", "stopped"}
    _wait_until(lambda: server_harness.service.get(job_id)["state"] == "stopped")
    assert b"!\x18" in server_harness.transport.raw_writes
    assert "M5" in server_harness.transport.commands
    assert _LATER_COMMAND not in server_harness.transport.commands


def test_high_level_job_logs_are_bounded_and_omit_authorization_and_gcode(
    server_harness: ServerHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(
        logging.INFO,
        logger="laser_aligner.machine.pi_job_service",
    ):
        job_id, program, _ = _upload(server_harness)
        assert _rpc(
            server_harness,
            ACTION_JOB_START,
            **_start_fields(job_id, program),
        )["accepted"] is True
        assert server_harness.transport.gated.wait(timeout=2.0)
        _rpc(server_harness, ACTION_JOB_STOP)
        _wait_until(
            lambda: server_harness.service.get(job_id)["state"] == "stopped"
        )

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "laser_aligner.machine.pi_job_service"
    ]
    assert any(job_id[:8] in message for message in messages)
    assert all(len(message) <= 256 for message in messages)
    assert all(MachineService.ARM_PHRASE not in message for message in messages)
    assert all("G1 X" not in message and _TOKEN not in message for message in messages)


def test_duplicate_start_never_reruns_and_replay_cache_echoes_request_id(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _ = _upload(server_harness)
    request_id = str(uuid.uuid4())
    first = _rpc(
        server_harness,
        ACTION_JOB_START,
        request_id=request_id,
        **_start_fields(job_id, program),
    )
    replay = _rpc(
        server_harness,
        ACTION_JOB_START,
        request_id=request_id,
        **_start_fields(job_id, program),
    )
    duplicate = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(job_id, program),
    )

    assert replay == first
    assert replay["request_id"] == request_id
    assert duplicate["ok"] is True
    assert duplicate["accepted"] is True
    assert duplicate["duplicate"] is True
    assert server_harness.transport.gated.wait(timeout=2.0)
    assert server_harness.transport.commands.count(_GATED_COMMAND) == 1
    server_harness.transport.release()
    _wait_until(lambda: server_harness.service.get(job_id)["state"] == "complete")


def test_failed_start_before_local_acceptance_is_not_misreported_on_retry(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _ = _upload(server_harness)
    program_path = server_harness.service.store.programs_dir / f"{job_id}.gcode"
    program_path.write_bytes(canonical_program_bytes(program) + b"\n")

    commands_before = list(server_harness.transport.commands)
    rejected = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(job_id, program),
    )
    assert rejected["ok"] is False
    assert server_harness.service.get(job_id)["state"] == "failed"

    retry = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(job_id, program),
    )
    assert retry["ok"] is True
    assert retry["duplicate"] is True
    assert retry["accepted"] is False
    assert retry["job"].get("ownership_accepted") is not True
    assert server_harness.transport.commands == commands_before


def test_running_job_blocks_interfering_operations_and_second_start(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _ = _upload(server_harness)
    second_job_id, second_program, _ = _upload(server_harness)
    assert _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(job_id, program),
    )["accepted"]
    assert server_harness.transport.gated.wait(timeout=2.0)

    second_start = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(second_job_id, second_program),
    )
    jog = _rpc(
        server_harness,
        ACTION_MACHINE_JOG,
        dx_mm=1.0,
        dy_mm=0.0,
        feed_mm_min=100.0,
    )
    command = _rpc(server_harness, ACTION_MACHINE_COMMAND, line="?")
    connect = _rpc(server_harness, ACTION_MACHINE_CONNECT)
    replace_connection = _rpc(
        server_harness,
        ACTION_MACHINE_REPLACE_CONNECTION,
    )
    disconnect = _rpc(server_harness, ACTION_MACHINE_DISCONNECT)
    prepare_start = _rpc(server_harness, ACTION_MACHINE_PREPARE_JOB_START)
    home_and_park = _rpc(
        server_harness,
        ACTION_MACHINE_PREPARE_PHOTO_POSITION,
    )
    with socket.create_connection(
        ("127.0.0.1", server_harness.server.bound_port),
        timeout=3.0,
    ) as sock:
        sock.settimeout(5.0)
        channel = authenticate_client(sock, _TOKEN)
        channel.send_json(
            {
                "action": ACTION_MACHINE_STEPPER_HOLD,
                "request_id": str(uuid.uuid4()),
            }
        )
        stepper_hold = channel.receive_json()
    status = _rpc(server_harness, ACTION_MACHINE_STATUS)

    assert second_start["ok"] is False
    assert jog["ok"] is False
    assert command["ok"] is False
    assert connect["ok"] is False
    assert replace_connection["ok"] is False
    assert disconnect["ok"] is False
    assert prepare_start["ok"] is False
    assert home_and_park["ok"] is False
    assert stepper_hold["ok"] is False
    assert status["ok"] is True
    assert status["status"]["job"]["running"] is True
    _rpc(server_harness, ACTION_JOB_STOP)
    _wait_until(lambda: server_harness.service.get(job_id)["state"] == "stopped")


def test_same_channel_stepper_hold_releases_on_client_request(
    server_harness: ServerHarness,
) -> None:
    assert _rpc(server_harness, "machine.connect")["ok"] is True
    assert _rpc(server_harness, ACTION_MACHINE_PREPARE_JOB_START)["ok"] is True
    enter_request_id = str(uuid.uuid4())
    release_request_id = str(uuid.uuid4())
    with socket.create_connection(
        ("127.0.0.1", server_harness.server.bound_port),
        timeout=3.0,
    ) as sock:
        sock.settimeout(5.0)
        channel = authenticate_client(sock, _TOKEN)
        channel.send_json(
            {
                "action": ACTION_MACHINE_STEPPER_HOLD,
                "request_id": enter_request_id,
                **_session_fields(server_harness),
            }
        )
        held = channel.receive_json()
        assert held["ok"] is True
        assert held["request_id"] == enter_request_id
        assert held["state"] == "held"
        assert isinstance(held["lease_id"], str)
        assert isinstance(held["state_revision"], int)
        assert isinstance(held["controller_session_generation"], int)
        assert server_harness.transport.step_idle_delay_ms == 255
        channel.send_json(
            {
                "action": ACTION_MACHINE_STEPPER_HOLD_RELEASE,
                "request_id": release_request_id,
                "lease_id": held["lease_id"],
            }
        )
        released = channel.receive_json()
        assert released["ok"] is True
        assert released["request_id"] == release_request_id
        assert released["state"] == "released"
    assert server_harness.transport.step_idle_delay_ms == 250


def test_pi_rpc_conflicting_operation_is_busy_during_stepper_hold(
    server_harness: ServerHarness,
) -> None:
    assert _rpc(server_harness, ACTION_MACHINE_CONNECT)["ok"] is True
    assert _rpc(server_harness, ACTION_MACHINE_PREPARE_JOB_START)["ok"] is True
    enter_request_id = str(uuid.uuid4())
    release_request_id = str(uuid.uuid4())
    ordinary_started = threading.Event()
    ordinary_result: dict[str, Any] = {}

    with socket.create_connection(
        ("127.0.0.1", server_harness.server.bound_port),
        timeout=3.0,
    ) as sock:
        sock.settimeout(5.0)
        channel = authenticate_client(sock, _TOKEN)
        channel.send_json(
            {
                "action": ACTION_MACHINE_STEPPER_HOLD,
                "request_id": enter_request_id,
                **_session_fields(server_harness),
            }
        )
        held = channel.receive_json()
        assert held["state"] == "held"

        def prepare_photo_position() -> None:
            ordinary_started.set()
            ordinary_result.update(
                _rpc(server_harness, ACTION_MACHINE_PREPARE_PHOTO_POSITION)
            )

        ordinary = threading.Thread(target=prepare_photo_position, daemon=True)
        ordinary.start()
        assert ordinary_started.wait(timeout=1.0)
        ordinary.join(timeout=1.0)
        assert not ordinary.is_alive()
        assert ordinary_result["ok"] is False
        assert ordinary_result["error_code"] == ERROR_CONTROLLER_BUSY
        assert ordinary_result["retryable"] is True

        channel.send_json(
            {
                "action": ACTION_MACHINE_STEPPER_HOLD_RELEASE,
                "request_id": release_request_id,
                "lease_id": held["lease_id"],
            }
        )
        assert channel.receive_json()["state"] == "released"
    assert not ordinary.is_alive()


def test_stepper_hold_does_not_block_pi_stop_authority(
    server_harness: ServerHarness,
) -> None:
    assert _rpc(server_harness, ACTION_MACHINE_CONNECT)["ok"] is True
    assert _rpc(server_harness, ACTION_MACHINE_PREPARE_JOB_START)["ok"] is True
    with socket.create_connection(
        ("127.0.0.1", server_harness.server.bound_port),
        timeout=3.0,
    ) as sock:
        sock.settimeout(5.0)
        channel = authenticate_client(sock, _TOKEN)
        channel.send_json(
            {
                "action": ACTION_MACHINE_STEPPER_HOLD,
                "request_id": str(uuid.uuid4()),
                **_session_fields(server_harness),
            }
        )
        held = channel.receive_json()
        assert held["state"] == "held"

        stop_started = time.perf_counter()
        stopped = _rpc(server_harness, ACTION_JOB_STOP)
        stop_elapsed = time.perf_counter() - stop_started

        assert stopped["ok"] is True
        assert stop_elapsed < 1.0
        channel.send_json(
            {
                "action": ACTION_MACHINE_STEPPER_HOLD_RELEASE,
                "request_id": str(uuid.uuid4()),
                "lease_id": held["lease_id"],
            }
        )
        release = channel.receive_json()
        assert release["ok"] is True
        assert release["state"] == "released"


def test_trace_capture_prepares_before_remote_hold_without_lease_timeout(
    server_harness: ServerHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_BRIDGE_TOKEN", _TOKEN)
    remote_settings = MachineSettings(
        backend="serial",
        protocol="grbl",
        port=f"e3bridge://127.0.0.1:{server_harness.server.bound_port}",
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
    )
    remote = RemoteMachineService(
        remote_settings,
        LaserSettings(arm_timeout_seconds=60),
        hardware_enabled=True,
        laser_lockout=False,
        monitor_interval_seconds=0.01,
    )
    events: list[str] = []
    pi_hold_active = False
    original_prepare = server_harness.service.prepare_photo_position
    original_hold = server_harness.service.temporary_stepper_hold

    def observed_prepare_photo_position(
        *,
        capture_home_position: bool = False,
        expected_session_generation: int | None = None,
    ) -> dict[str, Any]:
        events.append("pi:prepare:start")
        result = original_prepare(
            capture_home_position=capture_home_position,
            expected_session_generation=expected_session_generation,
        )
        events.append("pi:prepare:complete")
        return result

    @contextmanager
    def observed_stepper_hold(*, expected_session_generation: int | None = None):
        nonlocal pi_hold_active
        events.append("pi:hold:request")
        with original_hold(
            expected_session_generation=expected_session_generation,
        ):
            pi_hold_active = True
            events.append("pi:hold:active")
            try:
                yield
            finally:
                pi_hold_active = False
                events.append("pi:hold:release")

    monkeypatch.setattr(
        server_harness.service,
        "prepare_photo_position",
        observed_prepare_photo_position,
    )
    monkeypatch.setattr(
        server_harness.service,
        "temporary_stepper_hold",
        observed_stepper_hold,
    )

    frame = np.full((8, 8, 3), 73, dtype=np.uint8)
    burst = FrameBurst(
        frames=(frame,),
        sequence_numbers=(1,),
        discarded_frames=2,
        settle_seconds=0.1,
        elapsed_seconds=0.2,
        sharpness_scores=(),
        controls=ControlResult({}, {}, {}),
    )

    def capture_burst(_profile: object, *, score_frames: bool) -> FrameBurst:
        assert score_frames is False
        assert pi_hold_active
        assert server_harness.transport.step_idle_delay_ms == 255
        events.append("camera:burst")
        return burst

    context = SimpleNamespace(
        _require_valid_bed_calibration=lambda: events.append("calibration:valid"),
        machine=remote,
        settings=SimpleNamespace(
            machine=remote_settings,
            camera=SimpleNamespace(precision_capture=PrecisionCaptureSettings()),
        ),
        camera=SimpleNamespace(
            capture_burst=capture_burst,
            _sharpness_score=lambda _image: 4.0,
        ),
        lens=SimpleNamespace(model=None),
        _rectify_camera_image=lambda image: image.copy(),
        _cache_workspace=lambda _image: None,
        _persist_workspace=lambda _image: None,
    )
    context._stable_camera_burst = lambda: AppContext._stable_camera_burst(context)
    context._prepare_camera_burst = lambda value, *, undistort: (
        AppContext._prepare_camera_burst(context, value, undistort=undistort)
    )
    result: dict[str, Any] = {}
    timing: dict[str, float] = {}

    def capture_trace() -> None:
        try:
            result["image"] = AppContext.capture_parked_trace_frame(
                context,
                timing=timing,
            )
        except BaseException as exc:
            result["error"] = exc

    worker = threading.Thread(target=capture_trace, daemon=True)
    try:
        remote.connect()
        total_started = time.perf_counter()
        worker.start()
        worker.join(timeout=2.0)
        total_elapsed = time.perf_counter() - total_started
        if worker.is_alive():
            server_harness.server.stop()
            worker.join(timeout=2.0)
            pytest.fail(
                "Trace precision capture blocked behind the Pi stepper-hold lease"
            )
        if "error" in result:
            raise result["error"]

        assert np.array_equal(result["image"], frame)
        assert total_elapsed < 2.0
        assert events.index("pi:prepare:complete") < events.index("pi:hold:active")
        assert events.index("pi:hold:active") < events.index("camera:burst")
        assert events.index("camera:burst") < events.index("pi:hold:release")
        assert server_harness.transport.step_idle_delay_ms == 250
        assert timing["prepare_photo_seconds"] < 2.0
        assert timing["hold_acquisition_seconds"] < 2.0
        assert timing["camera_burst_seconds"] < 2.0
        assert timing["precision_capture_total_seconds"] < 2.0
    finally:
        remote.detach()


def test_stepper_hold_client_disconnect_unwinds_local_context(
    server_harness: ServerHarness,
) -> None:
    assert _rpc(server_harness, "machine.connect")["ok"] is True
    assert _rpc(server_harness, ACTION_MACHINE_PREPARE_JOB_START)["ok"] is True
    sock = socket.create_connection(
        ("127.0.0.1", server_harness.server.bound_port),
        timeout=3.0,
    )
    sock.settimeout(5.0)
    channel = authenticate_client(sock, _TOKEN)
    channel.send_json(
        {
            "action": ACTION_MACHINE_STEPPER_HOLD,
            "request_id": str(uuid.uuid4()),
            **_session_fields(server_harness),
        }
    )
    assert channel.receive_json()["state"] == "held"
    assert server_harness.transport.step_idle_delay_ms == 255

    sock.close()
    _wait_until(lambda: server_harness.transport.step_idle_delay_ms == 250)
    assert _rpc(server_harness, ACTION_MACHINE_STATUS)["ok"] is True


def test_pi_restart_marks_persisted_running_job_interrupted_without_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args, **_kwargs: pytest.fail("restart must not open a controller"),
    )
    root = tmp_path / "restart-jobs"
    first_machine = _machine(SimulatedTransport())
    first = PiJobService(first_machine, PiJobStore(root))
    program = first_machine.preflight_program(_POWERED_PROGRAM)
    payload = canonical_program_bytes(program)
    job_id = str(uuid.uuid4())
    first.begin_upload(job_id, "restart.gcode", len(payload), program.digest)
    first.append_upload_chunk(job_id, 0, payload)
    first.finalize_upload(job_id, **_service_binding(program))
    first.store.update_state(job_id, "starting", phase="starting")
    first.store.update_state(
        job_id,
        "running",
        phase="streaming",
        ownership_accepted=True,
        start_accepted_at=time.time(),
    )

    restarted_machine = _machine(SimulatedTransport())
    restarted = PiJobService(restarted_machine, PiJobStore(root))
    try:
        record = restarted.get(job_id)
        assert record["state"] == "interrupted"
        assert restarted.active() is None
        assert restarted_machine.status()["connected"] is False
        retry = restarted.start(
            job_id,
            authorization_phrase=MachineService.ARM_PHRASE,
            **_service_binding(program),
        )
        assert retry["duplicate"] is True
        assert retry["accepted"] is False
    finally:
        first.shutdown(stop_machine=False)
        restarted.shutdown(stop_machine=False)


def test_start_is_blocked_while_prior_secondary_recovery_is_unresolved(
    tmp_path: Path,
) -> None:
    machine = _machine(SimulatedTransport())
    store = PiJobStore(tmp_path / "pending-secondary")
    service = PiJobService(machine, store)
    binding = resolve_air_assist_commands(
        AirAssistSettings(
            mode=AirAssistMode.SECONDARY_MARLIN_FAN,
            port="/dev/serial/by-id/unresolved-fan",
            baudrate=115200,
        ),
        protocol="grbl",
    )
    assert binding is not None

    def prepare(job_id: str) -> ValidatedProgram:
        program = machine.preflight_program(_POWERED_PROGRAM)
        payload = canonical_program_bytes(program)
        service.begin_upload(job_id, "pending.gcode", len(payload), program.digest)
        service.append_upload_chunk(job_id, 0, payload)
        service.finalize_upload(job_id, **_service_binding(program))
        return program

    pending_id = str(uuid.uuid4())
    prepare(pending_id)
    store.begin_execution(
        pending_id,
        secondary_recovery_binding=binding,
    )
    store.update_state(pending_id, "failed", error="restart OFF unavailable")
    next_id = str(uuid.uuid4())
    next_program = prepare(next_id)

    try:
        with pytest.raises(PiJobServiceError, match="unresolved secondary"):
            service.start(
                next_id,
                authorization_phrase=MachineService.ARM_PHRASE,
                **_service_binding(next_program),
            )
        assert machine.status()["connected"] is False
        assert store.get(next_id)["state"] == "prepared"
    finally:
        service.shutdown(stop_machine=False)


@pytest.mark.parametrize(
    ("failure_mode", "error_fragment"),
    [
        ("write", "write failure"),
        ("read", "read failure"),
        ("timeout", "did not acknowledge"),
        ("error", "error:9"),
        ("alarm", "ALARM:1"),
    ],
)
def test_pi_local_controller_failure_persists_failed_without_auto_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
    error_fragment: str,
) -> None:
    transport = JobFailureTransport(failure_mode)
    monkeypatch.setattr(
        "laser_aligner.machine.service._JOB_COMMAND_ACK_TIMEOUT_SECONDS",
        0.05,
    )
    harness = _new_harness(tmp_path / failure_mode, monkeypatch, transport)
    try:
        job_id, program, _ = _upload(harness)
        started = _rpc(
            harness,
            ACTION_JOB_START,
            **_start_fields(job_id, program),
        )
        assert started["accepted"] is True
        assert transport.gated.wait(timeout=2.0)
        transport.release()
        _wait_until(lambda: harness.service.get(job_id)["state"] == "failed")
        assert error_fragment in harness.service.get(job_id)["error"]
        assert "M5" in transport.commands
        retry = _rpc(
            harness,
            ACTION_JOB_START,
            **_start_fields(job_id, program),
        )
        assert retry["duplicate"] is True
        assert retry["accepted"] is True
        assert transport.commands.count(_GATED_COMMAND) == 1
    finally:
        _close_harness(harness)


def test_powered_pi_completion_homes_parks_and_retains_held_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = RecordingGatedTransport()
    harness = _new_harness(
        tmp_path / "completion",
        monkeypatch,
        transport,
        powered_completion=True,
    )
    try:
        job_id, program, _ = _upload(harness)
        started = _rpc(
            harness,
            ACTION_JOB_START,
            **_start_fields(job_id, program),
        )
        assert started["accepted"] is True
        assert transport.gated.wait(timeout=2.0)
        transport.step_idle_delay_ms = 255
        transport.release()
        _wait_until(lambda: harness.service.get(job_id)["state"] == "complete")

        assert "$H" in transport.commands
        assert "G0 X110.000 Y110.000 F3000.000" in transport.commands
        assert transport.commands[-3:] == ["$$", "$G", "$#"]
        assert "$MD" not in transport.commands
        assert "$SLP" not in transport.commands
        result = _rpc(harness, ACTION_JOB_RESULT, job_id=job_id)
        assert result["job"]["state"] == "complete"
        assert result["job"]["completed_lines"] == len(program.lines)
        assert transport.step_idle_delay_ms == 255
        assert harness.machine.status()["controller_state"] == "READY_MOTION"
        assert harness.machine.status()["coordinate_reference_ready"] is True
        assert transport.x == pytest.approx(110.0)
        assert transport.y == pytest.approx(110.0)
    finally:
        _close_harness(harness)

@pytest.mark.parametrize("external_stop,cleanup_failure", [(False, False), (False, True), (True, False)])
def test_start_exception_preserved_before_cleanup_generation_change(
    server_harness: ServerHarness, monkeypatch: pytest.MonkeyPatch,
    external_stop: bool, cleanup_failure: bool,
) -> None:
    job_id, program, _ = _upload(server_harness)
    machine = server_harness.machine
    original_stop = machine.request_stop
    before = list(server_harness.transport.commands)

    def fail_start(*args, **kwargs):
        if external_stop:
            server_harness.service.stop()
        raise RuntimeError("secondary OFF failed: original diagnostic")

    def cleanup(*args, **kwargs):
        original_stop(*args, **kwargs)
        if cleanup_failure:
            raise RuntimeError("cleanup must not replace original")

    monkeypatch.setattr(machine, "start_preflighted_program", fail_start)
    monkeypatch.setattr(machine, "request_stop", cleanup)
    response = _rpc(server_harness, ACTION_JOB_START, **_start_fields(job_id, program))
    monkeypatch.setattr(machine, "request_stop", original_stop)
    assert response["ok"] is False
    record = server_harness.service.get(job_id)
    assert record["state"] == ("stopped" if external_stop else "failed")
    assert record["error"] == (None if external_stop else "secondary OFF failed: original diagnostic")
    assert server_harness.service.active() is None
    assert _GATED_COMMAND not in server_harness.transport.commands[len(before):]


def test_secondary_prestart_off_failure_is_durable_pi_failure(
    server_harness: ServerHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_secondary_controller import FakeSerial, _controller

    serial = FakeSerial(["ok", "error: physical secondary diagnostic"])
    fresh = FakeSerial([])
    _, fan = _controller([serial, fresh], read_timeout_seconds=0.005)
    fan.initialize_off()  # Same persistent owner that acknowledged restart OFF.
    job_id, program, _ = _upload(server_harness)
    monkeypatch.setattr(server_harness.machine, "_secondary_controller_for", lambda _: fan)
    before = len(server_harness.transport.commands)
    response = _rpc(server_harness, ACTION_JOB_START, **_start_fields(job_id, program))
    assert response["ok"] is False
    record = server_harness.service.get(job_id)
    assert record["state"] == "failed"
    assert "physical secondary diagnostic" in record["error"]
    assert "fresh-session OFF retry failed" in record["error"]
    assert fresh.open_calls == 1
    assert fresh.writes == ["M106 S0"]
    assert "acknowledged OFF" in record["error"]
    assert serial.writes[:2] == ["M106 S0", "M106 S0"]
    assert _GATED_COMMAND not in server_harness.transport.commands[before:]
    assert not server_harness.machine.status()["job"]["running"]
