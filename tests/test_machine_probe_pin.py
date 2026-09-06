from __future__ import annotations

import json
import socket
import threading
import time
import uuid

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.pi_job_protocol import authenticate_client
from laser_aligner.machine.pi_machine_server import (
    ACTION_MACHINE_PROBE_PIN,
    ACTION_MACHINE_PROBE_Z,
    SERVER_ACTION_SCHEMAS,
)
from laser_aligner.machine.probe_pin import run_pin_diagnostic
from laser_aligner.machine.z_probe import CrealityZProbe, ProbeReference
from tests import test_pi_machine_server as helpers
from tests.test_probe_pin import PinSerial
from tests.test_secondary_controller import _controller

server_harness = helpers.server_harness


@pytest.fixture
def no_pin_wait(monkeypatch):
    def diagnostic(*args, **kwargs):
        return run_pin_diagnostic(*args, **kwargs, sleep=lambda _seconds: None)

    monkeypatch.setattr("laser_aligner.machine.service.run_pin_diagnostic", diagnostic)


@pytest.fixture
def machine_pin(monkeypatch, no_pin_wait):
    serial = PinSerial()
    owner, fan = _controller([serial])
    fan.initialize_off()
    primary = helpers.RecordingGatedTransport()
    monkeypatch.setattr("laser_aligner.machine.service.create_machine_transport", lambda *_args: primary)
    machine = helpers._machine(primary)
    machine._secondary_air_assist = fan
    machine._z_probe = CrealityZProbe(owner, lambda: None)
    machine.connect()
    serial.writes.clear()
    primary.commands.clear()
    primary.raw_writes.clear()
    yield machine, serial, owner, primary
    machine._job.running = False
    machine.hardware_enabled = True
    machine.settings.allow_motion = True
    machine._armed_until_monotonic = 0.0
    machine._armed_until = 0.0
    machine.disconnect()


@pytest.mark.parametrize(("action", "servo"), [
    ("inspect", []), ("deploy", ["M280 P0 S10"]), ("stow", ["M280 P0 S90"]),
])
def test_service_pin_action_has_only_explicit_servo_and_no_axis_command(machine_pin, action, servo):
    machine, serial, owner, primary = machine_pin
    owner._secondary_fan_enabled = True
    # Pin diagnostics deliberately need no axis homing or trusted Z position.
    assert not machine._coordinate_reference_ready
    before_xy = primary.x, primary.y
    result = machine.probe_pin(action, confirmed=action != "inspect")
    assert serial.writes == ["M106 S0", "M115", *servo, "M119", "M114"]
    assert primary.commands == ["M5"]
    assert primary.raw_writes == []
    assert (primary.x, primary.y) == before_xy
    assert serial.open_calls == 1
    assert owner.ready
    assert result["completed"] is True
    assert result["action"] == action
    assert result["physical_pin_state"] == "unknown"
    assert result["operator_observation_required"] is True
    assert result["off_commands_acknowledged"] == ["M5", "M106 S0"]
    assert machine._secondary_air_assist.status.enabled is False
    assert not machine._coordinate_reference_ready


def test_inspect_can_read_with_motion_disabled_but_does_not_actuate_pin(machine_pin):
    machine, serial, _, _ = machine_pin
    machine.settings.allow_motion = False
    result = machine.probe_pin("inspect")
    assert result["completed"]
    assert serial.writes == ["M106 S0", "M115", "M119", "M114"]


@pytest.mark.parametrize("change", [
    "hardware_false", "hardware_one", "motion_false", "motion_one", "armed", "job", "disconnected",
])
def test_service_actuation_rejected_before_secondary_writes(machine_pin, change):
    machine, serial, _, _ = machine_pin
    if change.startswith("hardware_"):
        machine.hardware_enabled = 1 if change.endswith("one") else False
    elif change.startswith("motion_"):
        machine.settings.allow_motion = 1 if change.endswith("one") else False
    elif change == "armed":
        machine._armed_until_monotonic = time.monotonic() + 60
        machine._armed_until = time.time() + 60
    elif change == "job":
        machine._job.running = True
    elif change == "disconnected":
        machine.disconnect()
    before = list(serial.writes)
    with pytest.raises(MachineError):
        machine.probe_pin("deploy", confirmed=True)
    assert serial.writes == before


@pytest.mark.parametrize(("action", "confirmed"), [
    ("deploy", False), ("stow", False), ("deploy", 1), ("inspect", 0),
    ("inspect", None), ("stow", "true"), ("G28", True), ("DEPLOY", True), (None, True),
])
def test_invalid_pin_request_rejected_before_controller_writes(machine_pin, action, confirmed):
    machine, serial, _, primary = machine_pin
    with pytest.raises(SafetyError):
        machine.probe_pin(action, confirmed=confirmed)
    assert serial.writes == []
    assert primary.commands == []
    assert primary.raw_writes == []


@pytest.mark.parametrize("action", ["inspect", "deploy"])
def test_missing_secondary_owner_is_rejected_before_primary_command(machine_pin, action):
    machine, serial, _, primary = machine_pin
    machine._secondary_air_assist = None
    with pytest.raises(MachineError, match="not configured"):
        machine.probe_pin(action, confirmed=True)
    assert serial.writes == []
    assert primary.commands == []


def test_closed_secondary_owner_is_not_reopened_by_diagnostic(machine_pin):
    machine, serial, owner, primary = machine_pin
    owner.close()
    with pytest.raises(MachineError, match="initialization"):
        machine.probe_pin("deploy", confirmed=True)
    assert serial.open_calls == 1
    assert serial.writes == []
    assert primary.commands == ["M5"]


@pytest.mark.parametrize("failure", [None, "M119", "M106 S0"])
def test_pin_diagnostic_invalidates_existing_height_even_if_exchange_fails(machine_pin, failure):
    machine, serial, owner, _ = machine_pin
    machine._z_probe.reference = ProbeReference(
        controller_generation=owner.generation,
        primary_generation=machine._session.generation,
        stop_epoch=machine._operation_stop_epoch(),
        border_z_mm=0.0,
        border_samples_mm=(0.0, 0.0, 0.0),
        clearance_z_mm=20.0,
        support_height_mm=-1.5,
        carriage_xy_mm=(110.0, 110.0),
        firmware="Synthetic Marlin Ender-3 S1 Pro",
        measured_at=time.time(),
    )
    machine._z_probe_result = {"kind": "surface_measurement", "surface_height_mm": 3.0}
    if failure is not None:
        serial.overrides[failure] = ["Error:synthetic diagnostic failure"]
        with pytest.raises(MachineError, match="synthetic diagnostic failure"):
            machine.probe_pin("inspect")
    else:
        machine.probe_pin("inspect")
    assert machine._z_probe.reference is None
    assert machine._z_probe_result is None
    assert not machine.status()["z_probe"]["reference_ready"]
    assert not any(line.startswith(("G", "M280", "M112")) for line in serial.writes)


@pytest.mark.parametrize("operation", ["reference", "measure"])
def test_pin_diagnostic_does_not_enable_withdrawn_full_probe_workflow(machine_pin, operation):
    machine, serial, _, primary = machine_pin
    machine.probe_pin("inspect")
    before = list(serial.writes)
    primary.commands.clear()
    with pytest.raises(SafetyError, match="Material probing is suspended"):
        machine.probe_z(operation, confirmed=True, support_height_mm=-1.5)
    assert serial.writes == before
    assert primary.commands == []


@pytest.fixture
def pin_rpc_harness(server_harness, no_pin_wait):
    harness = server_harness
    serial = PinSerial()
    owner, fan = _controller([serial])
    fan.initialize_off()
    harness.machine._secondary_air_assist = fan
    harness.machine._z_probe = CrealityZProbe(owner, lambda: None)
    harness.service.connect()
    serial.writes.clear()
    harness.transport.commands.clear()
    return harness, serial


def pin_fields(harness, **changes):
    return {
        "client_id": "00000000-0000-4000-8000-000000000001",
        "expected_boot_id": harness.service.boot_id,
        "expected_session_generation": harness.machine.status()["controller_session_generation"],
        "pin_action": "deploy", "confirmed": True, **changes,
    }


def test_rpc_pin_schema_requires_action_confirmation_and_current_session():
    assert set(SERVER_ACTION_SCHEMAS[ACTION_MACHINE_PROBE_PIN]["required"]) == {
        "pin_action", "confirmed", "client_id", "expected_boot_id", "expected_session_generation",
    }
    assert SERVER_ACTION_SCHEMAS[ACTION_MACHINE_PROBE_PIN]["optional"] == ()


@pytest.mark.parametrize("action", ["inspect", "deploy", "stow"])
def test_authenticated_rpc_replay_never_repeats_pin_action(pin_rpc_harness, action):
    harness, serial = pin_rpc_harness
    request_id = str(uuid.uuid4())
    fields = pin_fields(harness, pin_action=action, confirmed=action != "inspect")
    response = helpers._rpc(harness, ACTION_MACHINE_PROBE_PIN, request_id=request_id, **fields)
    assert response["ok"], response
    assert response["result"]["action"] == action
    assert response["result"]["physical_pin_state"] == "unknown"
    assert response["result"]["off_commands_acknowledged"] == ["M5", "M106 S0"]
    assert harness.machine._secondary_air_assist.status.enabled is False
    before = list(serial.writes)
    repeated = helpers._rpc(harness, ACTION_MACHINE_PROBE_PIN, request_id=request_id, **fields)
    assert repeated == response
    assert serial.writes == before
    expected_servo = {"inspect": [], "deploy": ["M280 P0 S10"], "stow": ["M280 P0 S90"]}[action]
    assert serial.writes == ["M106 S0", "M115", *expected_servo, "M119", "M114"]
    assert harness.transport.commands == ["M5"]


@pytest.mark.parametrize("changes", [
    {"confirmed": False}, {"confirmed": 1}, {"pin_action": "G28"},
    {"expected_boot_id": str(uuid.uuid4())}, {"expected_session_generation": 9999},
    {"expected_session_generation": True}, {"client_id": "bad"}, {"unexpected": True},
])
def test_invalid_rpc_pin_request_never_writes(pin_rpc_harness, changes):
    harness, serial = pin_rpc_harness
    response = helpers._rpc(harness, ACTION_MACHINE_PROBE_PIN, **pin_fields(harness, **changes))
    assert not response["ok"]
    assert serial.writes == []
    assert harness.transport.commands == []


def test_missing_rpc_confirmation_never_writes(pin_rpc_harness):
    harness, serial = pin_rpc_harness
    fields = pin_fields(harness)
    fields.pop("confirmed")
    response = helpers._rpc(harness, ACTION_MACHINE_PROBE_PIN, **fields)
    assert not response["ok"]
    assert serial.writes == []


def test_rpc_pin_replay_with_changed_payload_is_rejected(pin_rpc_harness):
    harness, serial = pin_rpc_harness
    request_id = str(uuid.uuid4())
    response = helpers._rpc(harness, ACTION_MACHINE_PROBE_PIN, request_id=request_id, **pin_fields(harness))
    assert response["ok"], response
    before = list(serial.writes)
    conflicting = helpers._rpc(
        harness, ACTION_MACHINE_PROBE_PIN, request_id=request_id, **pin_fields(harness, pin_action="stow"),
    )
    assert not conflicting["ok"]
    assert serial.writes == before


def test_failed_pin_rpc_returns_bounded_attempted_commands_and_never_automatically_stows(pin_rpc_harness):
    harness, serial = pin_rpc_harness
    serial.overrides["M119"] = ["Error:" + "synthetic endstop failure " * 100]
    request_id = str(uuid.uuid4())
    fields = pin_fields(harness)
    response = helpers._rpc(harness, ACTION_MACHINE_PROBE_PIN, request_id=request_id, **fields)
    assert not response["ok"]
    assert response["retryable"] is False
    diagnostic = response["diagnostic"]
    assert diagnostic["action"] == "deploy"
    assert diagnostic["completed"] is False
    assert diagnostic["physical_pin_state"] == "unknown"
    assert diagnostic["operator_observation_required"] is True
    assert [entry["command"] for entry in diagnostic["transcript"]] == ["M115", "M280 P0 S10", "M119"]
    assert diagnostic["transcript"][1]["responses"] == ["ok"]
    assert "M119" in diagnostic["transcript"][2]["error"]
    assert "synthetic endstop failure" in diagnostic["error"]
    assert len(diagnostic["error"]) <= 512
    assert len(json.dumps(diagnostic, allow_nan=False)) < 5000
    assert serial.writes == ["M106 S0", "M115", "M280 P0 S10", "M119"]
    repeated = helpers._rpc(harness, ACTION_MACHINE_PROBE_PIN, request_id=request_id, **fields)
    assert repeated == response
    assert serial.writes == ["M106 S0", "M115", "M280 P0 S10", "M119"]


def test_pin_rpc_does_not_bypass_full_probe_suspension(pin_rpc_harness):
    harness, serial = pin_rpc_harness
    fields = pin_fields(harness)
    fields.pop("pin_action")
    response = helpers._rpc(
        harness, ACTION_MACHINE_PROBE_Z, **fields,
        operation="reference", clearance_z_mm=20, support_height_mm=-1.5,
    )
    assert not response["ok"]
    assert "Material probing is suspended" in str(response)
    assert serial.writes == []


def test_entire_pin_rpc_blocks_competing_motion_and_job_start(pin_rpc_harness, monkeypatch):
    harness, serial = pin_rpc_harness
    job_id, program, _ = helpers._upload(harness)
    entered = threading.Event()
    release = threading.Event()
    response = []

    def wait_for_operator(_delay):
        entered.set()
        assert release.wait(3)

    def diagnostic(*args, **kwargs):
        return run_pin_diagnostic(*args, **kwargs, sleep=wait_for_operator)

    monkeypatch.setattr("laser_aligner.machine.service.run_pin_diagnostic", diagnostic)
    worker = threading.Thread(target=lambda: response.append(
        helpers._rpc(harness, ACTION_MACHINE_PROBE_PIN, **pin_fields(harness)),
    ))
    worker.start()
    try:
        assert entered.wait(3)
        blocked_jog = helpers._rpc(harness, helpers.ACTION_MACHINE_JOG, dx_mm=1, dy_mm=0, feed_mm_min=300)
        assert not blocked_jog["ok"]
        assert "active" in str(blocked_jog)
        blocked_start = helpers._rpc(harness, helpers.ACTION_JOB_START, **helpers._start_fields(job_id, program))
        assert not blocked_start["ok"]
        assert serial.writes == ["M106 S0", "M115", "M280 P0 S10"]
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert response and response[0]["ok"], response


def test_closed_rpc_during_pin_ack_stops_queries_without_retry_or_reset(pin_rpc_harness):
    harness, serial = pin_rpc_harness
    serial.overrides["M280 P0 S10"] = []
    entered = threading.Event()
    serial.on_write = lambda command: entered.set() if command == "M280 P0 S10" else None
    with socket.create_connection(("127.0.0.1", harness.server.bound_port), timeout=3) as sock:
        channel = authenticate_client(sock, helpers._TOKEN)
        channel.send_json({
            "action": ACTION_MACHINE_PROBE_PIN,
            "request_id": str(uuid.uuid4()),
            **pin_fields(harness),
        })
        assert entered.wait(3)
    helpers._wait_until(lambda: serial.close_calls > 0, timeout=3)
    assert serial.writes == ["M106 S0", "M115", "M280 P0 S10"]
    assert harness.transport.commands == ["M5"]
