from __future__ import annotations

from contextlib import nullcontext

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.mainboard import CAPABILITY, control, parse_status, validate_control
from tests import test_machine_z_probe as helpers
from tests.test_z_probe import ready_probe

machine_probe = helpers.machine_probe


def mainboard_serial(serial):
    original = serial.write_line
    state = {"fan1": 0, "fan2": 0, "known": True}
    serial.overrides["M119"] = ["z_min: TRIGGERED", "ok"]

    def write(line):
        if line in serial.overrides:
            return original(line)
        if line == "M115":
            serial.writes.append(line)
            serial.responses.extend(["FIRMWARE_NAME:Marlin MACHINE_TYPE:Ender-3 S1 Pro", CAPABILITY, "ok"])
        elif line == "M123":
            serial.writes.append(line)
            serial.responses.extend([f"E3MB:1 FAN1:{state['fan1']} FAN2:{state['fan2']} Z_KNOWN:{int(state['known'])}", "ok"])
        elif line.startswith("M106"):
            serial.writes.append(line)
            fan = "fan1" if "P1" in line else "fan2"
            state[fan] = int(line.split("S")[1])
            serial.responses.append("ok")
        else:
            original(line)
    serial.write_line = write
    return state


@pytest.mark.parametrize("args", [
    ("fan1", -1, True), ("fan2", 101, True), ("fan1", True, True),
    ("fan1", 1.5, True), ("fan1", 1, False), ("fan1", 1, 1),
    ("z", 19, True), ("z", 81, True), ("z", float("nan"), True),
    ("z", 21, False), ("status", 1, False), ("M106", 50, True),
])
def test_bad_controls_reject_before_transport(args):
    with pytest.raises(MachineError):
        validate_control(*args)


@pytest.mark.parametrize("lines", [
    ("ok",), ("E3MB:1 FAN1:256 FAN2:0 Z_KNOWN:1",),
    ("E3MB:2 FAN1:0 FAN2:0 Z_KNOWN:1",),
    ("E3MB:1 FAN1:0 FAN2:0 Z_KNOWN:1",) * 2,
])
def test_bad_status_rejects(lines):
    with pytest.raises(MachineError):
        parse_status(lines)


def test_independent_fans_and_cleanup(machine_probe):
    machine, serial, owner, _ = machine_probe
    state = mainboard_serial(serial)
    result = machine.mainboard_control("fan1", 50, confirmed=True)
    assert result["fan1_pwm"] == 128 and result["fan2_pwm"] == 0
    result = machine.mainboard_control("fan2", 100, confirmed=True)
    assert result["fan1_pwm"] == 128 and result["fan2_pwm"] == 255
    assert owner._secondary_fan_enabled is True
    result = machine.mainboard_control("fan2", 0)
    assert result["fan1_pwm"] == 128 and result["fan2_pwm"] == 0
    machine.disconnect()
    assert state["fan1"] == state["fan2"] == 0
    assert "M106 P1 S0" in serial.writes


@pytest.mark.parametrize("change", ["hardware", "motion", "armed", "job"])
def test_service_admission_before_secondary_write(machine_probe, change):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    if change == "hardware":
        machine.hardware_enabled = False
    elif change == "motion":
        machine.settings.allow_motion = False
    elif change == "armed":
        import time
        machine._armed_until_monotonic = time.monotonic() + 60
    else:
        machine._job.running = True
    before = list(serial.writes)
    try:
        with pytest.raises(MachineError):
            machine.mainboard_control("z", 21, confirmed=True)
        assert serial.writes == before
    finally:
        machine._job.running = False


def test_verified_bounded_z_move_invalidates_material_reference(machine_probe):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    result = machine.mainboard_control("z", 25, confirmed=True)
    assert result["z_mm"] == 25
    assert "G1 Z25.000 F300" in serial.writes
    assert machine._z_probe.reference is None
    assert "G28" not in serial.writes


@pytest.mark.parametrize("change", ["unhomed", "too_far", "deployed", "wrong_identity"])
def test_z_rejects_before_movement(change):
    serial, owner, _, _, _ = ready_probe()
    state = mainboard_serial(serial)
    target = 21
    if change == "unhomed":
        state["known"] = False
    elif change == "too_far":
        target = 26
    elif change == "deployed":
        serial.overrides["M119"] = ["z_min: open", "ok"]
    else:
        serial.overrides["M115"] = ["FIRMWARE_NAME:Marlin", "ok"]
    with pytest.raises(MachineError):
        control(owner, "z", target, confirmed=True, guard=nullcontext, on_failure=lambda: None)
    assert not any(s.startswith("G1") for s in serial.writes)


def test_capability_duplicates_reject_fan_output():
    serial, owner, _, _, _ = ready_probe()
    mainboard_serial(serial)
    serial.overrides["M115"] = ["FIRMWARE_NAME:Marlin", CAPABILITY, CAPABILITY, "ok"]
    before = list(serial.writes)
    with pytest.raises(MachineError):
        control(owner, "fan1", 80, confirmed=True, guard=nullcontext, on_failure=lambda: None)
    assert serial.writes[len(before):] == ["M115"]


def test_lost_connection_rejects_without_positive_output(machine_probe):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    with pytest.raises(MachineError):
        machine.mainboard_control("fan1", 100, confirmed=True, _connection_alive=lambda: False)
    assert "M106 P1 S255" not in serial.writes


def test_cli_requires_confirmation_before_network(monkeypatch, capsys):
    from laser_aligner import mainboard_control
    monkeypatch.setattr(mainboard_control, "_exchange", lambda *a, **k: pytest.fail("Unexpected RPC"))
    assert mainboard_control.main(["fan1", "50"]) == 1
    assert "Confirm" in capsys.readouterr().out


@pytest.mark.parametrize("operation", ["stop", "disarm"])
def test_idle_manual_fans_off_without_job_mapping(machine_probe, operation):
    import time
    machine, serial, _, _ = machine_probe
    state = mainboard_serial(serial)
    machine.mainboard_control("fan1", 100, confirmed=True)
    machine.mainboard_control("fan2", 100, confirmed=True)
    if operation == "stop":
        machine.request_stop(_recover=False)
    else:
        machine.disarm()
    deadline = time.monotonic() + 3
    while any(state[k] for k in ("fan1", "fan2")) and time.monotonic() < deadline:
        time.sleep(.01)
    assert state["fan1"] == state["fan2"] == 0


def test_uncertain_fan_cleanup_attempts_native_kill():
    serial, owner, fan, _, _ = ready_probe()
    mainboard_serial(serial)
    owner._mainboard_fan1_used = True
    serial.overrides["M106 P1 S0"] = ["Error: failed", "ok"]
    fan.best_effort_off()
    assert "M106 S0" in serial.writes and "M106 P1 S0" in serial.writes
    assert "M112" in serial.writes
