from __future__ import annotations

import socket
import threading
import uuid
from contextlib import contextmanager

import pytest

from laser_aligner.machine.pi_job_protocol import (
    ACTION_JOB_START,
    ACTION_JOB_STOP,
    CAPABILITY_PI_CONTROL_OWNER,
    ERROR_CONTROLLER_IN_USE,
    ERROR_CONTROLLER_STALE_SESSION,
    ERROR_INVALID_REQUEST,
    authenticate_client,
)
from laser_aligner.machine.pi_machine_server import (
    ACTION_MACHINE_COMMAND,
    ACTION_MACHINE_CONNECT,
    ACTION_MACHINE_CONTROL_RELEASE,
    ACTION_MACHINE_DISCONNECT,
    ACTION_MACHINE_FOCUS,
    ACTION_MACHINE_REPLACE_CONNECTION,
    ACTION_MACHINE_STATUS,
    ACTION_MACHINE_STEPPER_HOLD,
    ACTION_MACHINE_STEPPER_HOLD_RELEASE,
    ACTION_SERVICE_CAPABILITIES,
)
from tests.test_pi_machine_server import (
    _GATED_COMMAND,
    _TOKEN,
    RecordingGatedTransport,
    _close_harness,
    _new_harness,
    _rpc,
    _session_fields,
    _start_fields,
    _upload,
    _wait_until,
)

OWNER = "00000000-0000-4000-8000-000000000001"
OBSERVER = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    value = _new_harness(tmp_path, monkeypatch, RecordingGatedTransport())
    try:
        yield value
    finally:
        _close_harness(value)


@pytest.fixture
def control_clock(harness):
    now = [100.0]
    harness.server._control_clock = lambda: now[0]
    return now


def _release(harness, client_id=OWNER, **fields):
    fields.setdefault("expected_control_owner_revision", harness.server._control_metadata()["control_owner_revision"])
    return _rpc(harness, ACTION_MACHINE_CONTROL_RELEASE,
                client_id=client_id, expected_boot_id=harness.service.boot_id, **fields)


def test_control_contract_and_unclaimed_status_do_not_open_controller(harness):
    capabilities = _rpc(harness, ACTION_SERVICE_CAPABILITIES)
    assert CAPABILITY_PI_CONTROL_OWNER in capabilities["capabilities"]
    assert capabilities["control_owner_client_id"] is None
    assert capabilities["control_owner_leased"] is False
    assert capabilities["control_owner_lease_seconds"] == 30.0
    status = _rpc(harness, ACTION_MACHINE_STATUS, client_id=OBSERVER)
    assert status["ok"] is True
    assert status["control_owner_client_id"] is None
    assert harness.transport.commands == []


@pytest.mark.parametrize("action", [ACTION_MACHINE_CONNECT, ACTION_MACHINE_REPLACE_CONNECTION,
                                   ACTION_MACHINE_DISCONNECT, ACTION_MACHINE_COMMAND])
def test_second_app_cannot_interrupt_first_app_controller(harness, action):
    connected = _rpc(harness, ACTION_MACHINE_CONNECT)
    commands = list(harness.transport.commands)
    raw_writes = list(harness.transport.raw_writes)
    fields = {"line": "M5"} if action == ACTION_MACHINE_COMMAND else {}
    denied = _rpc(harness, action, client_id=OBSERVER, **fields)
    assert denied["ok"] is False
    assert denied["error_code"] == ERROR_CONTROLLER_IN_USE
    assert denied["control_owner_client_id"] == OWNER
    assert denied["controller_session_generation"] == connected["controller_session_generation"]
    assert harness.machine.connected
    assert harness.transport.commands == commands
    assert harness.transport.raw_writes == raw_writes


@pytest.mark.parametrize("action", [ACTION_MACHINE_DISCONNECT, ACTION_MACHINE_COMMAND])
def test_unclaimed_cleanup_or_diagnostic_requires_connect_and_never_claims(harness, action):
    fields = {"line": "M115"} if action == ACTION_MACHINE_COMMAND else {}
    result = _rpc(harness, action, **fields)
    assert result["error_code"] == ERROR_CONTROLLER_IN_USE
    assert result["control_owner_client_id"] is None
    assert harness.transport.commands == []


def test_background_focus_read_never_claims_an_unowned_controller(harness):
    response = _rpc(harness, ACTION_MACHINE_FOCUS, **_session_fields(harness),
                    control="status", confirmed=False, value=None, clearance_z_mm=30.0,
                    gap_mm=7.0, measurement_id=None, preview_id=None)
    assert response["error_code"] == ERROR_CONTROLLER_IN_USE
    assert response["control_owner_client_id"] is None
    assert harness.transport.commands == []


def test_owner_disconnect_releases_control_for_second_app(harness):
    _rpc(harness, ACTION_MACHINE_CONNECT)
    released = _rpc(harness, ACTION_MACHINE_DISCONNECT)
    assert released["ok"] is True
    assert released["control_owner_client_id"] is None
    assert released["status"]["control_owner_client_id"] is None
    connected = _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)
    assert connected["ok"] is True
    assert connected["control_owner_client_id"] == OBSERVER


def test_explicit_detach_changes_no_controller_state_or_commands(harness):
    connected = _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    before = list(harness.transport.commands)
    assert _release(harness, OBSERVER)["error_code"] == ERROR_CONTROLLER_IN_USE
    detached = _release(harness)
    assert detached["released"] is True
    assert detached["control_owner_client_id"] is None
    assert detached["controller_session_generation"] == connected["controller_session_generation"]
    assert harness.transport.commands == before
    assert harness.machine.connected
    second = _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER, control_lease=True)
    assert second["ok"] is True
    assert second["controller_session_generation"] == connected["controller_session_generation"]
    assert harness.transport.commands == before
    assert _rpc(harness, ACTION_MACHINE_DISCONNECT)["error_code"] == ERROR_CONTROLLER_IN_USE


@pytest.mark.parametrize("action", [ACTION_MACHINE_CONNECT, ACTION_MACHINE_REPLACE_CONNECTION])
def test_late_release_cannot_clear_new_same_owner_connection_claim(harness, action):
    original = _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    current = _rpc(harness, action, control_lease=True)
    commands = list(harness.transport.commands)
    raw_writes = list(harness.transport.raw_writes)
    assert current["control_owner_revision"] > original["control_owner_revision"]
    stale = _release(harness, expected_control_owner_revision=original["control_owner_revision"])
    assert stale["error_code"] == ERROR_CONTROLLER_STALE_SESSION
    assert stale["control_owner_revision"] == current["control_owner_revision"]
    assert stale["control_owner_client_id"] == OWNER
    assert harness.machine.connected
    assert harness.transport.commands == commands
    assert harness.transport.raw_writes == raw_writes
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["error_code"] == ERROR_CONTROLLER_IN_USE
    assert _release(harness, expected_control_owner_revision=current["control_owner_revision"])["released"] is True


def test_late_release_cannot_clear_same_client_after_expiry_and_reclaim(harness, control_clock):
    original = _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    control_clock[0] += 31
    current = _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    assert current["control_owner_revision"] > original["control_owner_revision"]
    stale = _release(harness, expected_control_owner_revision=original["control_owner_revision"])
    assert stale["error_code"] == ERROR_CONTROLLER_STALE_SESSION
    assert stale["control_owner_client_id"] == OWNER
    assert stale["control_owner_revision"] == current["control_owner_revision"]


def test_late_disconnect_cannot_close_new_same_owner_idempotent_connection(harness):
    original = _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    current = _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    assert current["controller_session_generation"] == original["controller_session_generation"]
    assert current["control_owner_revision"] > original["control_owner_revision"]
    commands = list(harness.transport.commands)
    raw_writes = list(harness.transport.raw_writes)
    stale = _rpc(harness, ACTION_MACHINE_DISCONNECT,
                 expected_session_generation=original["controller_session_generation"],
                 expected_control_owner_revision=original["control_owner_revision"])
    assert stale["error_code"] == ERROR_CONTROLLER_STALE_SESSION
    assert stale["control_owner_revision"] == current["control_owner_revision"]
    assert stale["control_owner_client_id"] == OWNER
    assert harness.machine.connected
    assert harness.transport.commands == commands
    assert harness.transport.raw_writes == raw_writes
    released = _rpc(harness, ACTION_MACHINE_DISCONNECT,
                    expected_control_owner_revision=current["control_owner_revision"])
    assert released["ok"] is True
    assert released["control_owner_client_id"] is None


@pytest.mark.parametrize("revision", [None, True, -1, 1.0, "1", []])
def test_supplied_disconnect_revision_must_be_exact_nonnegative_integer(harness, revision):
    original = _rpc(harness, ACTION_MACHINE_CONNECT)
    commands = list(harness.transport.commands)
    response = _rpc(harness, ACTION_MACHINE_DISCONNECT, expected_control_owner_revision=revision)
    assert response["error_code"] == ERROR_INVALID_REQUEST
    assert response["control_owner_revision"] == original["control_owner_revision"]
    assert harness.machine.connected
    assert harness.transport.commands == commands


def test_replayed_connect_does_not_change_control_claim_incarnation(harness):
    _rpc(harness, ACTION_MACHINE_CONNECT)
    request_id = str(uuid.uuid4())
    connected = _rpc(harness, ACTION_MACHINE_CONNECT, request_id=request_id)
    replay = _rpc(harness, ACTION_MACHINE_CONNECT, request_id=request_id)
    assert replay == connected
    assert _release(harness, expected_control_owner_revision=connected["control_owner_revision"])["released"] is True


@pytest.mark.parametrize("revision", [None, True, -1, 1.0, "1", []])
def test_release_requires_exact_nonnegative_owner_revision(harness, revision):
    original = _rpc(harness, ACTION_MACHINE_CONNECT)
    rejected = _release(harness, expected_control_owner_revision=revision)
    assert rejected["error_code"] == ERROR_INVALID_REQUEST
    assert rejected["control_owner_revision"] == original["control_owner_revision"]
    assert rejected["control_owner_client_id"] == OWNER


def test_lease_expires_without_hardware_cleanup_and_needs_explicit_reclaim(harness, control_clock):
    _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    commands = list(harness.transport.commands)
    control_clock[0] += 31
    status = _rpc(harness, ACTION_MACHINE_STATUS, client_id=OWNER)
    assert status["control_owner_client_id"] is None
    assert harness.transport.commands == commands
    assert harness.machine.connected
    assert _rpc(harness, ACTION_MACHINE_DISCONNECT)["error_code"] == ERROR_CONTROLLER_IN_USE
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["ok"] is True


def test_only_owner_status_extends_lease(harness, control_clock):
    _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    control_clock[0] += 20
    assert _rpc(harness, ACTION_MACHINE_STATUS, client_id=OWNER)["control_owner_client_id"] == OWNER
    control_clock[0] += 20
    assert _rpc(harness, ACTION_MACHINE_STATUS, client_id=OBSERVER)["control_owner_client_id"] == OWNER
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["error_code"] == ERROR_CONTROLLER_IN_USE
    control_clock[0] += 11
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["ok"] is True


def test_legacy_owner_never_expires_from_anonymous_monitoring(harness, control_clock):
    _rpc(harness, ACTION_MACHINE_CONNECT)
    control_clock[0] += 100000
    assert _rpc(harness, ACTION_MACHINE_STATUS)["control_owner_client_id"] == OWNER
    # A later request cannot silently change the legacy owner's renewal contract.
    assert _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)["control_owner_leased"] is False
    control_clock[0] += 100000
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["error_code"] == ERROR_CONTROLLER_IN_USE


@pytest.mark.parametrize("value", [None, 0, 1, "true", []])
def test_invalid_lease_never_claims_or_touches_controller(harness, value):
    result = _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=value)
    assert result["error_code"] == ERROR_INVALID_REQUEST
    assert result["control_owner_client_id"] is None
    assert harness.transport.commands == []


def test_stale_initial_request_does_not_strand_owner(harness):
    result = _rpc(harness, ACTION_MACHINE_CONNECT, expected_session_generation=500)
    assert result["ok"] is False
    assert result["error_code"] == ERROR_CONTROLLER_STALE_SESSION
    assert result["control_owner_client_id"] is None
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["ok"] is True


def test_shutdown_rejection_does_not_claim_controller(harness):
    harness.service.begin_shutdown()
    response = _rpc(harness, ACTION_MACHINE_CONNECT)
    assert response["ok"] is False
    assert response["control_owner_client_id"] is None


def test_first_connect_reserves_priority_before_slow_controller_open(harness, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    original = harness.service.connect

    def blocked(**kwargs):
        entered.set()
        assert finish.wait(3.0)
        return original(**kwargs)

    monkeypatch.setattr(harness.service, "connect", blocked)
    results = []
    worker = threading.Thread(target=lambda: results.append(_rpc(harness, ACTION_MACHINE_CONNECT)))
    worker.start()
    try:
        assert entered.wait(2.0)
        denied = _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)
        assert denied["error_code"] == ERROR_CONTROLLER_IN_USE
        assert harness.transport.commands == []
    finally:
        finish.set()
        worker.join(4.0)
    assert results[0]["ok"] is True
    assert results[0]["control_owner_client_id"] == OWNER


@pytest.mark.parametrize("release_owner", [True, False])
def test_detach_or_lease_expiry_preserves_accepted_pi_job(harness, control_clock, release_owner):
    _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    job_id, program, _ = _upload(harness)
    accepted = _rpc(harness, ACTION_JOB_START, **_start_fields(job_id, program))
    assert accepted["accepted"] is True
    assert harness.transport.gated.wait(2.0)
    commands = list(harness.transport.commands)
    raw_writes = list(harness.transport.raw_writes)
    if release_owner:
        assert _release(harness)["released"] is True
    else:
        control_clock[0] += 31
    status = _rpc(harness, ACTION_MACHINE_STATUS, client_id=OBSERVER)
    assert status["control_owner_client_id"] is None
    assert status["active_job"]["job_id"] == job_id
    assert status["active_job"]["ownership_accepted"] is True
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["ok"] is False
    assert harness.transport.commands == commands
    assert harness.transport.raw_writes == raw_writes
    assert harness.transport.commands[-1] == _GATED_COMMAND
    harness.transport.release()
    _wait_until(lambda: harness.service.get(job_id)["state"] == "complete")
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["ok"] is True


def test_inflight_work_pins_owner_and_defers_explicit_release(harness, control_clock, monkeypatch):
    _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    entered, finish = threading.Event(), threading.Event()
    original = harness.service.manual_command

    def blocked(*args, **kwargs):
        entered.set()
        assert finish.wait(3.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(harness.service, "manual_command", blocked)
    results = []
    worker = threading.Thread(target=lambda: results.append(_rpc(harness, ACTION_MACHINE_COMMAND, line="M115")))
    worker.start()
    try:
        assert entered.wait(2.0)
        control_clock[0] += 60
        assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["error_code"] == ERROR_CONTROLLER_IN_USE
        assert _release(harness)["released"] is False
        assert _rpc(harness, ACTION_MACHINE_CONNECT)["error_code"] == ERROR_CONTROLLER_IN_USE
        assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["error_code"] == ERROR_CONTROLLER_IN_USE
    finally:
        finish.set()
        worker.join(4.0)
    assert results[0]["ok"] is True
    assert results[0]["control_owner_client_id"] is None
    assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["ok"] is True


def test_global_stop_keeps_owner_while_admitted_work_is_blocked(harness, monkeypatch):
    _rpc(harness, ACTION_MACHINE_CONNECT)
    entered, finish = threading.Event(), threading.Event()
    original = harness.service.manual_command

    def blocked(*args, **kwargs):
        entered.set()
        assert finish.wait(3.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(harness.service, "manual_command", blocked)
    results = []
    worker = threading.Thread(target=lambda: results.append(_rpc(harness, ACTION_MACHINE_COMMAND, line="M115")))
    worker.start()
    try:
        assert entered.wait(2.0)
        stop = _rpc(harness, ACTION_JOB_STOP, emergency=True)
        assert stop["ok"] is True
        assert stop["control_owner_client_id"] == OWNER
        assert b"!\x18" in harness.transport.raw_writes
    finally:
        finish.set()
        worker.join(4.0)


def test_stepper_channel_requires_owner_and_pins_lease(harness, control_clock, monkeypatch):
    _rpc(harness, ACTION_MACHINE_CONNECT, control_lease=True)
    events = []

    @contextmanager
    def hold(**kwargs):
        events.append("held")
        try:
            yield
        finally:
            events.append("released")

    monkeypatch.setattr(harness.service, "temporary_stepper_hold", hold)
    rejected = _rpc(harness, ACTION_MACHINE_STEPPER_HOLD, client_id=OBSERVER)
    assert rejected["error_code"] == ERROR_CONTROLLER_IN_USE
    assert events == []
    with socket.create_connection(("127.0.0.1", harness.server.bound_port), timeout=3.0) as sock:
        sock.settimeout(3.0)
        channel = authenticate_client(sock, _TOKEN)
        channel.send_json({"action": ACTION_MACHINE_STEPPER_HOLD, "request_id": str(uuid.uuid4()),
                           **_session_fields(harness), "control_lease": True})
        held = channel.receive_json()
        assert held["state"] == "held"
        control_clock[0] += 60
        assert _rpc(harness, ACTION_MACHINE_CONNECT, client_id=OBSERVER)["error_code"] == ERROR_CONTROLLER_IN_USE
        channel.send_json({"action": ACTION_MACHINE_STEPPER_HOLD_RELEASE,
                           "request_id": str(uuid.uuid4()), "lease_id": held["lease_id"]})
        assert channel.receive_json()["state"] == "released"
    assert events == ["held", "released"]
    assert _rpc(harness, ACTION_MACHINE_STATUS)["control_owner_client_id"] == OWNER


@pytest.mark.parametrize("action,fields", [
    (ACTION_MACHINE_STATUS, {"expected_session_generation": 1}),
    (ACTION_MACHINE_STATUS, {"expected_boot_id": "00000000-0000-4000-8000-000000000003"}),
    (ACTION_MACHINE_CONTROL_RELEASE, {"expected_session_generation": 1}),
])
def test_observation_and_release_reject_invalid_session_fields(harness, action, fields):
    _rpc(harness, ACTION_MACHINE_CONNECT)
    result = (_release(harness, **fields) if action == ACTION_MACHINE_CONTROL_RELEASE
              else _rpc(harness, action, client_id=OWNER, **fields))
    assert result["error_code"] == ERROR_INVALID_REQUEST
    assert result["control_owner_client_id"] == OWNER
