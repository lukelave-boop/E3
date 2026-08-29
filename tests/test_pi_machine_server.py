from __future__ import annotations

import logging
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

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
from laser_aligner.machine.pi_job_protocol import (
    ACTION_JOB_ACTIVE,
    ACTION_JOB_BEGIN,
    ACTION_JOB_CHUNK,
    ACTION_JOB_FINALIZE,
    ACTION_JOB_RESULT,
    ACTION_JOB_START,
    ACTION_JOB_STATUS,
    ACTION_JOB_STOP,
    authenticate_client,
    encode_upload_chunk,
)
from laser_aligner.machine.pi_job_service import (
    PiJobService,
    canonical_program_bytes,
    execution_policy_digest,
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


def _upload(
    harness: ServerHarness,
    *,
    job_id: str | None = None,
) -> tuple[str, ValidatedProgram, dict[str, Any]]:
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
    assert server_harness.transport.commands == []


def test_prepared_disconnect_and_unknown_start_remain_inert(
    server_harness: ServerHarness,
) -> None:
    job_id, program, _ = _upload(server_harness)
    time.sleep(0.05)
    prepared = _rpc(server_harness, ACTION_JOB_STATUS, job_id=job_id)
    assert prepared["job"]["state"] == "prepared"
    assert _rpc(server_harness, ACTION_JOB_ACTIVE)["job"] is None
    assert _rpc(server_harness, ACTION_JOB_LATEST)["job"] is None
    assert server_harness.machine.status()["connected"] is False
    assert server_harness.transport.commands == []

    unknown = _rpc(
        server_harness,
        ACTION_JOB_START,
        **_start_fields(str(uuid.uuid4()), program),
    )
    assert unknown["ok"] is False
    assert server_harness.machine.status()["connected"] is False
    assert server_harness.transport.commands == []


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
    assert server_harness.transport.commands == []


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
            }
        )
        held = channel.receive_json()
        assert held == {
            "ok": True,
            "request_id": enter_request_id,
            "state": "held",
            "lease_id": held["lease_id"],
        }
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


def test_pi_rpc_ordinary_operation_waits_for_stepper_hold_release(
    server_harness: ServerHarness,
) -> None:
    assert _rpc(server_harness, ACTION_MACHINE_CONNECT)["ok"] is True
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
        ordinary.join(timeout=0.1)
        assert ordinary.is_alive(), (
            "The Pi ordinary-operation lock must not permit Home / park inside "
            "another session's active stepper hold"
        )

        channel.send_json(
            {
                "action": ACTION_MACHINE_STEPPER_HOLD_RELEASE,
                "request_id": release_request_id,
                "lease_id": held["lease_id"],
            }
        )
        assert channel.receive_json()["state"] == "released"
        ordinary.join(timeout=2.0)

    assert not ordinary.is_alive()
    assert ordinary_result["ok"] is True


def test_stepper_hold_does_not_block_pi_stop_authority(
    server_harness: ServerHarness,
) -> None:
    assert _rpc(server_harness, ACTION_MACHINE_CONNECT)["ok"] is True
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
        assert release["ok"] is False
        assert "STOP" in release["error"]


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
        *, capture_home_position: bool = False
    ) -> dict[str, Any]:
        events.append("pi:prepare:start")
        result = original_prepare(capture_home_position=capture_home_position)
        events.append("pi:prepare:complete")
        return result

    @contextmanager
    def observed_stepper_hold():
        nonlocal pi_hold_active
        events.append("pi:hold:request")
        with original_hold():
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


def test_powered_pi_completion_homes_parks_restores_hold_and_releases(
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

        assert transport.commands[-11:] == [
            "M5",
            "G4 P0.01",
            "$H",
            "G21",
            "G90",
            "G0 X110.000 Y110.000 F3000.000",
            "G4 P0.01",
            "$$",
            "M5",
            "$1=250",
            "$MD",
        ]
        result = _rpc(harness, ACTION_JOB_RESULT, job_id=job_id)
        assert result["job"]["state"] == "complete"
        assert result["job"]["completed_lines"] == len(program.lines)
        assert transport.step_idle_delay_ms == 250
        assert transport.x == pytest.approx(110.0)
        assert transport.y == pytest.approx(110.0)
    finally:
        _close_harness(harness)
