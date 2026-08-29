from __future__ import annotations

import base64
import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from laser_aligner.app import AppContext
from laser_aligner.config import LaserSettings, MachineSettings, load_settings
from laser_aligner.core.runtime import CoreRuntime
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine import remote_service as remote_service_module
from laser_aligner.machine.pi_job_protocol import (
    ACTION_JOB_ACTIVE,
    ACTION_JOB_BEGIN,
    ACTION_JOB_CHUNK,
    ACTION_JOB_FINALIZE,
    ACTION_JOB_RESULT,
    ACTION_JOB_START,
    ACTION_JOB_STATUS,
    ACTION_JOB_STOP,
    CAPABILITY_PI_OWNED_JOBS,
    PROTOCOL_VERSION,
    PiJobProtocolError,
)
from laser_aligner.machine.pi_job_service import (
    canonical_program_bytes,
    execution_policy_digest,
)
from laser_aligner.machine.pi_machine_server import (
    ACTION_JOB_LATEST,
    ACTION_MACHINE_CONNECT,
    ACTION_MACHINE_DISCONNECT,
    ACTION_MACHINE_STATUS,
    ACTION_SERVICE_CAPABILITIES,
    SERVER_ACTION_SCHEMAS,
)
from laser_aligner.machine.remote_service import RemoteMachineService
from laser_aligner.machine.service import MachineService

_TOKEN = "remote-machine-test-token-value"
_POWERED_GCODE = "\n".join(
    (
        "G21",
        "G90",
        "M5",
        "G0 X10 Y10 F1000",
        "M4 S10",
        "G1 X20 Y20 F500",
        "M5",
    )
)
_UNPOWERED_GCODE = "\n".join(
    (
        "G21",
        "G90",
        "M5",
        "G0 X10 Y10 F1000",
        "M5",
    )
)


@pytest.fixture(autouse=True)
def _bridge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E3_BRIDGE_TOKEN", _TOKEN)


def _machine_settings(
    *,
    backend: str = "serial",
    port: str = "e3bridge://pi.test:9876",
) -> MachineSettings:
    return MachineSettings(
        backend=backend,
        protocol="grbl",
        port=port,
        allow_motion=True,
        controller_startup_delay=0.0,
    )


def _service(
    *,
    hardware_enabled: bool = True,
    arm_timeout_seconds: int = 60,
) -> RemoteMachineService:
    return RemoteMachineService(
        _machine_settings(),
        LaserSettings(arm_timeout_seconds=arm_timeout_seconds),
        hardware_enabled=hardware_enabled,
        monitor_interval_seconds=0.02,
    )


class FakePi:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.jobs: dict[str, dict[str, Any]] = {}
        self.uploads: dict[str, bytearray] = {}
        self.active_job_id: str | None = None
        self.connected = True
        self.before_request: Any = None
        self.raise_for_action: dict[str, Exception] = {}

    def _response(self, request: dict[str, Any], **body: Any) -> dict[str, Any]:
        return {"ok": True, "request_id": request["request_id"], **body}

    def _machine_status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "connecting": False,
            "backend": "serial",
            "hardware_enabled": True,
            "laser_lockout": False,
            "protocol": "grbl",
            "port": "/dev/ttyUSB0",
            "baudrate": 115200,
            "allow_motion": True,
            "coordinate_reference_ready": True,
            "coordinate_state_reference": None,
            "jog_position_mm": {"x": 0.0, "y": 0.0},
            "jog_ready": True,
            "max_travel_feed_mm_min": 6000.0,
            "controller_reconnect_required": False,
            "armed": False,
            "armed_until": None,
            "job": {
                "running": False,
                "phase": "idle",
                "total_lines": 0,
                "completed_lines": 0,
            },
            "last_successful_job": None,
        }

    def __call__(
        self,
        host: str,
        port: int,
        token: str,
        request: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        assert (host, port) == ("pi.test", 9876)
        assert token == _TOKEN
        assert timeout > 0.0
        request = copy.deepcopy(request)
        self.requests.append(request)
        action = request["action"]
        if self.before_request is not None:
            self.before_request(action, request)
        error = self.raise_for_action.get(action)
        if error is not None:
            raise error

        if action == ACTION_SERVICE_CAPABILITIES:
            return self._response(
                request,
                protocol_version=PROTOCOL_VERSION,
                capabilities=[CAPABILITY_PI_OWNED_JOBS],
                actions=copy.deepcopy(SERVER_ACTION_SCHEMAS),
            )
        if action == ACTION_MACHINE_STATUS:
            return self._response(request, status=self._machine_status())
        if action == ACTION_MACHINE_CONNECT:
            self.connected = True
            return self._response(request, status=self._machine_status())
        if action == ACTION_MACHINE_DISCONNECT:
            self.connected = False
            return self._response(request, status=self._machine_status())
        if action == ACTION_JOB_BEGIN:
            job_id = request["job_id"]
            self.uploads[job_id] = bytearray()
            self.jobs[job_id] = {
                "job_id": job_id,
                "name": request["name"],
                "state": "receiving",
                "expected_size": request["expected_size"],
                "expected_sha256": request["expected_sha256"],
                "received_size": 0,
                "guarded_output_polygon_mm": request.get(
                    "guarded_output_polygon_mm"
                ),
            }
            return self._response(request, job=copy.deepcopy(self.jobs[job_id]))
        if action == ACTION_JOB_CHUNK:
            job_id = request["job_id"]
            upload = self.uploads[job_id]
            assert request["offset"] == len(upload)
            upload.extend(base64.b64decode(request["data_b64"], validate=True))
            self.jobs[job_id]["received_size"] = len(upload)
            return self._response(request, job=copy.deepcopy(self.jobs[job_id]))
        if action == ACTION_JOB_FINALIZE:
            job_id = request["job_id"]
            job = self.jobs[job_id]
            job.update(
                {
                    "state": "prepared",
                    "phase": "prepared",
                    "program_digest": request["program_digest"],
                    "requires_laser_authorization": request[
                        "requires_laser_authorization"
                    ],
                    "requires_motion": request["requires_motion"],
                    "execution_policy_digest": request[
                        "execution_policy_digest"
                    ],
                    "verification_seconds": 0.001,
                }
            )
            return self._response(
                request,
                job=copy.deepcopy(job),
                ready=True,
                verification_seconds=0.001,
            )
        if action == ACTION_JOB_START:
            job_id = request["job_id"]
            job = self.jobs[job_id]
            job.update(
                {
                    "state": "running",
                    "phase": "streaming",
                    "started_at": time.time(),
                    "completed_lines": 0,
                    "total_lines": 7,
                    "powered": bool(job["requires_laser_authorization"]),
                    "protocol": "grbl",
                    "ownership_accepted": True,
                    "start_accepted_at": time.time(),
                }
            )
            self.active_job_id = job_id
            return self._response(
                request,
                accepted=True,
                duplicate=False,
                execution_owner="pi",
                job=copy.deepcopy(job),
            )
        if action == ACTION_JOB_ACTIVE:
            job = (
                None
                if self.active_job_id is None
                else copy.deepcopy(self.jobs[self.active_job_id])
            )
            return self._response(request, job=job)
        if action == ACTION_JOB_LATEST:
            terminals = [
                job
                for job in self.jobs.values()
                if job.get("state")
                in {"complete", "failed", "stopped", "interrupted"}
                and job.get("ownership_accepted") is True
            ]
            latest = terminals[-1] if terminals else None
            return self._response(
                request,
                job=None if latest is None else copy.deepcopy(latest),
            )
        if action == ACTION_JOB_STATUS:
            return self._response(
                request,
                job=copy.deepcopy(self.jobs[request["job_id"]]),
            )
        if action == ACTION_JOB_RESULT:
            return self._response(
                request,
                job=copy.deepcopy(self.jobs[request["job_id"]]),
            )
        if action == ACTION_JOB_STOP:
            job = None
            if self.active_job_id is not None:
                current = self.jobs[self.active_job_id]
                current.update({"state": "stopping", "phase": "stopping"})
                job = copy.deepcopy(current)
            return self._response(request, job=job)
        raise AssertionError(f"Unexpected fake action: {action}")


def _install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakePi) -> None:
    monkeypatch.setattr(remote_service_module, "request_response", fake)


def test_exact_upload_finalize_start_lifecycle_and_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    monkeypatch.setattr(remote_service_module, "MAX_UPLOAD_CHUNK_BYTES", 16)
    service = _service()
    program = service.preflight_program(_POWERED_GCODE)

    result = service.start_preflighted_program(
        program,
        "exact-job.gcode",
        authorization_phrase=service.ARM_PHRASE,
    )

    actions = [request["action"] for request in fake.requests]
    assert actions[0] == ACTION_SERVICE_CAPABILITIES
    assert actions[1] == ACTION_JOB_BEGIN
    assert actions[-2:] == [ACTION_JOB_FINALIZE, ACTION_JOB_START]
    chunks = [
        request for request in fake.requests if request["action"] == ACTION_JOB_CHUNK
    ]
    assert len(chunks) > 1
    assert all(
        1 <= len(base64.b64decode(request["data_b64"])) <= 16
        for request in chunks
    )
    job_id = fake.requests[1]["job_id"]
    assert bytes(fake.uploads[job_id]) == canonical_program_bytes(program)

    begin = fake.requests[1]
    finalize = fake.requests[-2]
    start = fake.requests[-1]
    assert begin["expected_size"] == len(canonical_program_bytes(program))
    assert begin["expected_sha256"] == program.digest
    for key in (
        "program_digest",
        "requires_laser_authorization",
        "requires_motion",
        "guarded_output_polygon_mm",
        "execution_policy_digest",
    ):
        assert start[key] == finalize[key]
    assert finalize["execution_policy_digest"] == execution_policy_digest(program)
    assert "authorization_phrase" not in finalize
    assert start["authorization_phrase"] == service.ARM_PHRASE
    assert result["accepted"] is True
    assert result["execution_owner"] == "pi"
    assert result["upload_bytes"] == len(canonical_program_bytes(program))
    assert result["upload_seconds"] >= 0.0
    assert result["throughput_bps"] > 0.0
    assert result["finalize_seconds"] >= 0.0
    assert result["start_latency_seconds"] >= 0.0
    assert service.armed is False
    assert service.pi_owned_job_active is True


def test_start_response_loss_queries_same_job_without_restarting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()

    def lose_start(action: str, request: dict[str, Any]) -> None:
        if action != ACTION_JOB_START:
            return
        job = fake.jobs[request["job_id"]]
        job.update(
            {
                "state": "running",
                "phase": "streaming",
                "program_digest": request["program_digest"],
                "total_lines": 7,
                "completed_lines": 1,
                "ownership_accepted": True,
                "start_accepted_at": time.time(),
            }
        )
        fake.active_job_id = request["job_id"]
        fake.raise_for_action[ACTION_JOB_START] = PiJobProtocolError(
            "connection closed after START"
        )

    fake.before_request = lose_start
    _install_fake(monkeypatch, fake)
    service = _service()
    program = service.preflight_program(_POWERED_GCODE)

    result = service.start_preflighted_program(
        program,
        authorization_phrase=service.ARM_PHRASE,
    )

    actions = [request["action"] for request in fake.requests]
    assert actions.count(ACTION_JOB_START) == 1
    assert actions[-1] == ACTION_JOB_STATUS
    assert result["accepted"] is True
    assert result["duplicate"] is True
    assert service.pi_owned_job_active is True


def test_arm_expiry_during_upload_leaves_prepared_job_inert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    clock = {"now": 10.0}
    monkeypatch.setattr(
        remote_service_module.time,
        "monotonic",
        lambda: clock["now"],
    )

    def expire_after_finalize(action: str, _request: dict[str, Any]) -> None:
        if action == ACTION_JOB_FINALIZE:
            clock["now"] = 12.0

    fake.before_request = expire_after_finalize
    _install_fake(monkeypatch, fake)
    service = _service(arm_timeout_seconds=1)
    program = service.preflight_program(_POWERED_GCODE)

    with pytest.raises(SafetyError, match="expired or was revoked during upload"):
        service.start_preflighted_program(
            program,
            authorization_phrase=service.ARM_PHRASE,
        )

    actions = [request["action"] for request in fake.requests]
    assert ACTION_JOB_FINALIZE in actions
    assert ACTION_JOB_START not in actions
    job = next(iter(fake.jobs.values()))
    assert job["state"] == "prepared"
    assert service.pi_owned_job_active is False
    assert service.armed is False


def test_cached_status_never_performs_network_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = _service()
    service._refresh_once()
    calls = len(fake.requests)

    first = service.status()
    second = service.status()

    assert len(fake.requests) == calls
    assert first == second
    assert first["monitor_connected"] is True


def test_background_monitor_refreshes_and_detaches_without_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    refreshed = threading.Event()
    calls: list[str] = []

    def refresh() -> None:
        calls.append("refresh")
        refreshed.set()

    monkeypatch.setattr(service, "_refresh_once", refresh)
    service.start_monitoring()
    assert refreshed.wait(1.0)
    service.detach()

    assert calls
    assert service.status()["monitor_connected"] is False


def test_monitor_reattaches_to_an_existing_pi_owned_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    job_id = str(uuid.uuid4())
    fake.jobs[job_id] = {
        "job_id": job_id,
        "name": "reattach.gcode",
        "state": "running",
        "phase": "streaming",
        "program_digest": "a" * 64,
        "completed_lines": 4,
        "total_lines": 10,
        "powered": True,
        "ownership_accepted": True,
        "start_accepted_at": time.time(),
    }
    fake.active_job_id = job_id
    _install_fake(monkeypatch, fake)
    service = _service()

    service._refresh_once()
    status = service.status()

    assert status["job"]["job_id"] == job_id
    assert status["job"]["progress"] == pytest.approx(0.4)
    assert status["job"]["execution_owner"] == "pi"
    assert service.pi_owned_job_active is True


def test_starting_without_acceptance_marker_is_not_displayed_as_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    job_id = str(uuid.uuid4())
    fake.jobs[job_id] = {
        "job_id": job_id,
        "name": "final-checks.gcode",
        "state": "starting",
        "phase": "starting",
        "program_digest": "b" * 64,
        "completed_lines": 0,
        "total_lines": 7,
        "powered": True,
    }
    fake.active_job_id = job_id
    _install_fake(monkeypatch, fake)
    service = _service()
    service._refresh_once()
    fake.requests.clear()

    status = service.status()
    assert status["job"]["running"] is False
    assert status["job"]["execution_owner"] == "uncertain"
    assert status["job"]["ownership_uncertain"] is True
    # Cleanup must still avoid destructive disconnect while START is unresolved.
    assert service.pi_owned_job_active is True
    service.disconnect()
    assert fake.requests == []


def test_lost_start_terminal_failure_without_marker_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()

    def fail_before_acceptance(action: str, request: dict[str, Any]) -> None:
        if action != ACTION_JOB_START:
            return
        fake.jobs[request["job_id"]].update(
            {
                "state": "failed",
                "phase": "failed",
                "error": "controller did not open",
                "ownership_accepted": False,
            }
        )
        fake.raise_for_action[ACTION_JOB_START] = PiJobProtocolError(
            "START response lost"
        )

    fake.before_request = fail_before_acceptance
    _install_fake(monkeypatch, fake)
    service = _service()
    program = service.preflight_program(_POWERED_GCODE)

    with pytest.raises(MachineError, match="did not accept START"):
        service.start_preflighted_program(
            program,
            authorization_phrase=service.ARM_PHRASE,
        )

    actions = [request["action"] for request in fake.requests]
    assert actions.count(ACTION_JOB_START) == 1
    assert actions[-1] == ACTION_JOB_STATUS
    assert service.pi_owned_job_active is False
    assert service.status()["job"]["state"] == "failed"


def test_network_loss_keeps_accepted_job_running_in_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = _service()
    program = service.preflight_program(_POWERED_GCODE)
    service.start_preflighted_program(
        program,
        authorization_phrase=service.ARM_PHRASE,
    )

    service._mark_monitor_disconnected("Wi-Fi unavailable")
    status = service.status()

    assert status["monitor_connected"] is False
    assert status["status_stale"] is True
    assert status["job"]["running"] is True
    assert status["job"]["status_stale"] is True
    assert status["job"]["execution_owner"] == "pi"


def test_terminal_receipt_uses_local_identity_despite_pi_clock_skew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = _service()
    program = service.preflight_program(_POWERED_GCODE)
    not_before = time.time() - 1.0
    result = service.start_preflighted_program(
        program,
        "receipt.gcode",
        authorization_phrase=service.ARM_PHRASE,
    )
    job_id = result["job_id"]
    fake.jobs[job_id].update(
        {
            "state": "complete",
            "phase": "complete",
            # Pi wall time is not comparable to the Windows calibration clock.
            "finished_at": not_before - 3600.0,
            "completed_lines": 7,
            "total_lines": 7,
            "error": None,
        }
    )
    fake.active_job_id = None

    service._refresh_once()
    receipt = service.successful_job_receipt(
        program.digest,
        not_before=not_before,
    )

    assert service.status()["job"]["state"] == "complete"
    assert service.pi_owned_job_active is False
    assert receipt is not None
    assert receipt["job_id"] == job_id
    assert receipt["program_digest"] == program.digest
    assert receipt["completed_lines"] == receipt["total_lines"] == 7
    assert receipt["execution_owner"] == "pi"


def test_fresh_client_displays_latest_terminal_but_cannot_claim_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    job_id = str(uuid.uuid4())
    digest = "d" * 64
    fake.jobs[job_id] = {
        "job_id": job_id,
        "name": "completed-offline.gcode",
        "state": "complete",
        "phase": "complete",
        "program_digest": digest,
        "expected_sha256": digest,
        "started_at": 100.0,
        "finished_at": 110.0,
        "completed_lines": 5,
        "total_lines": 5,
        "ownership_accepted": True,
        "start_accepted_at": 100.0,
        "error": None,
    }
    _install_fake(monkeypatch, fake)
    service = _service()

    service._refresh_once()

    assert service.status()["job"]["job_id"] == job_id
    assert service.status()["job"]["state"] == "complete"
    assert ACTION_JOB_LATEST in [request["action"] for request in fake.requests]
    # A new Windows process did not create this exact UUID, so it cannot use a
    # Pi terminal record as proof for a calibration run started in this process.
    assert service.successful_job_receipt(digest, not_before=0.0) is None


def test_new_upload_monitoring_supersedes_prior_terminal_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = _service()
    program = service.preflight_program(_UNPOWERED_GCODE)
    first = service.start_validated_program(program, "first.gcode")
    first_job_id = first["job_id"]
    fake.jobs[first_job_id].update(
        {
            "state": "complete",
            "phase": "complete",
            "finished_at": time.time(),
            "completed_lines": 5,
            "total_lines": 5,
            "error": None,
        }
    )
    fake.active_job_id = None
    service._refresh_once()

    entered_finalize = threading.Event()
    release_finalize = threading.Event()

    def block_second_finalize(action: str, request: dict[str, Any]) -> None:
        if action == ACTION_JOB_FINALIZE and request["job_id"] != first_job_id:
            entered_finalize.set()
            assert release_finalize.wait(2.0)

    fake.before_request = block_second_finalize
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def start_second() -> None:
        try:
            results.append(service.start_validated_program(program, "second.gcode"))
        except BaseException as exc:  # captured for the test thread
            errors.append(exc)

    worker = threading.Thread(target=start_second)
    worker.start()
    assert entered_finalize.wait(2.0)
    second_job_id = next(job_id for job_id in fake.jobs if job_id != first_job_id)
    try:
        service._refresh_once()
        status_requests = [
            request
            for request in fake.requests
            if request["action"] == ACTION_JOB_STATUS
        ]
        assert status_requests[-1]["job_id"] == second_job_id
        assert service.status()["job"]["job_id"] == second_job_id
    finally:
        release_finalize.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert errors == []
    assert results[0]["accepted"] is True
    assert results[0]["job_id"] == second_job_id


@pytest.mark.parametrize(
    ("blocked_action", "inert_state"),
    (
        (ACTION_JOB_CHUNK, "receiving"),
        (ACTION_JOB_FINALIZE, "prepared"),
    ),
)
def test_disconnect_cancels_unpowered_prestart_upload_without_remote_stop(
    monkeypatch: pytest.MonkeyPatch,
    blocked_action: str,
    inert_state: str,
) -> None:
    fake = FakePi()
    entered = threading.Event()
    release = threading.Event()

    def block_request(action: str, _request: dict[str, Any]) -> None:
        if action == blocked_action:
            entered.set()
            assert release.wait(2.0)

    fake.before_request = block_request
    _install_fake(monkeypatch, fake)
    service = _service()
    program = service.preflight_program(_UNPOWERED_GCODE)
    assert program.requires_laser_authorization is False
    errors: list[BaseException] = []

    def start() -> None:
        try:
            service.start_validated_program(program, "unpowered.gcode")
        except BaseException as exc:  # captured for the test thread
            errors.append(exc)

    worker = threading.Thread(target=start)
    worker.start()
    assert entered.wait(2.0)

    # Disconnect during upload is a local detach/cancel boundary. It must not
    # release the Pi controller or send STOP, even for an unpowered program.
    service.disconnect()
    release.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], MachineError)
    assert "cancelled" in str(errors[0]).lower()
    actions = [request["action"] for request in fake.requests]
    assert ACTION_JOB_START not in actions
    assert ACTION_JOB_STOP not in actions
    assert ACTION_MACHINE_DISCONNECT not in actions
    assert next(iter(fake.jobs.values()))["state"] == inert_state
    assert service.pi_owned_job_active is False
    assert service.status()["status_stale"] is True


def test_disconnect_detaches_active_job_but_explicit_stop_uses_priority_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = _service()
    program = service.preflight_program(_POWERED_GCODE)
    service.start_preflighted_program(
        program,
        authorization_phrase=service.ARM_PHRASE,
    )
    fake.requests.clear()

    service.disconnect()

    assert fake.requests == []
    assert service.pi_owned_job_active is True
    assert service.status()["status_stale"] is True

    service.request_stop(emergency=True)

    assert [request["action"] for request in fake.requests] == [ACTION_JOB_STOP]
    assert fake.requests[0]["emergency"] is True


def test_idle_disconnect_may_release_the_pi_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = _service()

    service.disconnect()

    assert [request["action"] for request in fake.requests] == [
        ACTION_SERVICE_CAPABILITIES,
        ACTION_MACHINE_DISCONNECT,
    ]
    assert service.connected is False


def test_disconnect_revocation_keeps_stale_ordinary_scopes_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = _service()
    stale_generation = service.operation_generation()

    with service.operation_scope(stale_generation):
        service.disconnect()
    fake.requests.clear()

    with service.operation_scope(stale_generation):
        for operation in (
            service.connect,
            service.replace_connection,
            service.prepare_photo_position,
            lambda: service.jog(1.0, 0.0, 100.0),
            lambda: service.send_command("?"),
        ):
            with pytest.raises(MachineError, match="cancelled by software STOP"):
                operation()

    assert fake.requests == []


def test_stop_during_idle_disconnect_still_invalidates_cleanup_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    entered_disconnect = threading.Event()
    release_disconnect = threading.Event()

    def block_disconnect(action: str, _request: dict[str, Any]) -> None:
        if action == ACTION_MACHINE_DISCONNECT:
            entered_disconnect.set()
            assert release_disconnect.wait(2.0)

    fake.before_request = block_disconnect
    _install_fake(monkeypatch, fake)
    service = _service()
    requested_generation = service.operation_generation()
    errors: list[BaseException] = []

    def disconnect() -> None:
        try:
            with service.operation_scope(requested_generation):
                service.disconnect()
        except BaseException as exc:  # captured for the test thread
            errors.append(exc)

    worker = threading.Thread(target=disconnect)
    worker.start()
    assert entered_disconnect.wait(2.0)

    service.request_stop(emergency=True)
    release_disconnect.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], MachineError)
    assert "cancelled by software STOP" in str(errors[0])
    actions = [request["action"] for request in fake.requests]
    assert actions == [
        ACTION_SERVICE_CAPABILITIES,
        ACTION_MACHINE_DISCONNECT,
        ACTION_JOB_STOP,
    ]


def test_process_hardware_gate_blocks_remote_controller_actions_before_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = _service(hardware_enabled=False)

    with pytest.raises(SafetyError, match="not granted hardware authority"):
        service.connect()
    with pytest.raises(SafetyError, match="not granted hardware authority"):
        service.prepare_job_start()
    with pytest.raises(SafetyError, match="not granted hardware authority"):
        service.send_command("?")
    assert fake.requests == []

    service.request_stop(emergency=True)
    assert [request["action"] for request in fake.requests] == [ACTION_JOB_STOP]


def test_legacy_raw_bridge_is_incompatible_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def incompatible(
        _host: str,
        _port: int,
        _token: str,
        request: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        assert timeout > 0.0
        calls.append(request["action"])
        raise PiJobProtocolError(
            "The endpoint speaks legacy E3BRIDGE/1 raw serial, which is incompatible"
        )

    monkeypatch.setattr(remote_service_module, "request_response", incompatible)
    service = _service()

    with pytest.raises(PiJobProtocolError, match="E3BRIDGE/1"):
        service.connect()

    assert calls == [ACTION_SERVICE_CAPABILITIES]
    assert not hasattr(service, "_transport")


def test_remote_service_rejects_auto_protocol_binding_before_remote_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _machine_settings()
    settings.protocol = "auto"
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    service = RemoteMachineService(
        settings,
        LaserSettings(),
        hardware_enabled=True,
    )

    with pytest.raises(MachineError, match="explicit matching.*grbl or marlin"):
        service.connect()

    assert fake.requests == []


def test_nested_stepper_hold_uses_one_outer_same_channel_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    opened: list[str] = []
    released: list[str] = []

    class FakeSocket:
        def close(self) -> None:
            opened.append("closed")

    def open_hold() -> tuple[FakeSocket, object, str]:
        opened.append("opened")
        return FakeSocket(), object(), "lease-id"

    monkeypatch.setattr(service, "_open_stepper_hold", open_hold)
    monkeypatch.setattr(
        service,
        "_release_stepper_hold",
        lambda _channel, lease_id: released.append(lease_id),
    )

    with service.temporary_stepper_hold():
        with service.temporary_stepper_hold():
            assert opened == ["opened"]

    assert opened == ["opened", "closed"]
    assert released == ["lease-id"]


def _app_settings(tmp_path: Path, *, backend: str, port: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / f"{backend}-{port.replace(':', '_').replace('/', '_')}.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": str(tmp_path / f"data-{backend}"),
                    "open_browser": False,
                },
                "camera": {"autostart": False},
                "machine": {
                    "backend": backend,
                    "protocol": "grbl",
                    "port": port,
                },
            }
        ),
        encoding="utf-8",
    )
    return load_settings(config_path)


def test_app_context_selects_remote_only_for_serial_bridge_uri(
    tmp_path: Path,
) -> None:
    remote = AppContext(
        _app_settings(
            tmp_path / "remote",
            backend="serial",
            port="e3bridge://127.0.0.1:9",
        ),
        hardware_enabled=True,
    )
    direct = AppContext(
        _app_settings(
            tmp_path / "direct",
            backend="serial",
            port="COM9",
        ),
        hardware_enabled=True,
    )
    # The current loader rejects the retired simulator backend. Mutate a loaded
    # settings object to exercise the selection boundary against a stale legacy
    # profile without weakening that separate configuration rejection.
    simulation_settings = _app_settings(
        tmp_path / "simulation",
        backend="serial",
        port="e3bridge://stale-node:9876",
    )
    simulation_settings.machine.backend = "simulator"
    simulation = AppContext(simulation_settings)
    try:
        assert type(remote.machine) is RemoteMachineService
        assert type(direct.machine) is MachineService
        assert type(simulation.machine) is MachineService
        assert simulation.machine.settings.backend == "simulator"
    finally:
        remote.machine.detach()
        remote.camera.stop()
        direct.stop()
        simulation.stop()


def test_runtime_stop_disconnects_idle_remote_machine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    runtime = CoreRuntime(
        _app_settings(
            tmp_path,
            backend="serial",
            port="e3bridge://pi.test:9876",
        ),
        hardware_enabled=True,
    )
    assert isinstance(runtime.context.machine, RemoteMachineService)
    monkeypatch.setattr(runtime.context.machine, "start_monitoring", lambda: None)
    runtime.start()
    fake.requests.clear()

    runtime.stop()

    assert [request["action"] for request in fake.requests] == [
        ACTION_SERVICE_CAPABILITIES,
        ACTION_MACHINE_DISCONNECT,
    ]


def test_runtime_stop_detaches_accepted_remote_job_without_rpc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePi()
    _install_fake(monkeypatch, fake)
    runtime = CoreRuntime(
        _app_settings(
            tmp_path,
            backend="serial",
            port="e3bridge://pi.test:9876",
        ),
        hardware_enabled=True,
    )
    machine = runtime.context.machine
    assert isinstance(machine, RemoteMachineService)
    monkeypatch.setattr(machine, "start_monitoring", lambda: None)
    runtime.start()
    machine._cache_job_record(
        {
            "job_id": str(uuid.uuid4()),
            "name": "accepted.gcode",
            "state": "running",
            "phase": "streaming",
            "program_digest": "c" * 64,
            "ownership_accepted": True,
            "start_accepted_at": time.time(),
            "total_lines": 10,
            "completed_lines": 2,
        },
        accepted=True,
    )
    fake.requests.clear()

    runtime.stop()

    assert fake.requests == []
    assert machine.status()["status_stale"] is True
