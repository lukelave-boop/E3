"""Job Stop keeps independent stationary Z only across verified recovery."""
import threading

import pytest

from laser_aligner.errors import MachineError
from tests.test_job_focus import PROGRAM, select, wait
from tests.test_machine_z_retention import rig  # noqa: F401


def running(rig, monkeypatch):  # noqa: F811
    machine = rig.machine
    rig.serial.contacts = [0., 5.]
    plan = select(machine)
    primary = rig.primaries[-1]
    write = primary.write_line
    pending = threading.Event()
    raw = []
    raw_write = primary.write_raw

    def line(command):
        if command.startswith("G1 X105"):
            pending.set()  # Accepted movement has no ACK; no buffer drain can finish.
            return
        return write(command)

    def realtime(data):
        raw.append(data)
        return raw_write(data)

    monkeypatch.setattr(primary, "write_line", line)
    monkeypatch.setattr(primary, "write_raw", realtime)
    program = machine.preflight_program(PROGRAM)
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    assert pending.wait(4)
    assert machine._active_job_context.verified_z == {"z_mm": 25.}
    rig.serial.writes.clear()
    return plan, program, raw


@pytest.mark.parametrize("automatic_recovery", [False, True])
def test_stop_retains_z_then_lifts_and_homes_xy_without_border_probe(rig, monkeypatch, automatic_recovery):  # noqa: F811
    machine = rig.machine
    _, old_program, raw = running(rig, monkeypatch)
    if automatic_recovery:
        monkeypatch.setattr(rig.primaries[-1], "synchronize_input", lambda **kwargs: None, raising=False)
    generation = rig.owner.generation
    machine.request_job_stop()
    wait(machine)
    if automatic_recovery:
        recovery = machine._recovery_thread
        if recovery is not None:
            recovery.join(4)
            assert not recovery.is_alive()
        assert machine.status()["controller_state"] == "READY_HOME_REQUIRED"
    assert b"!\x18" in raw and b"~" not in raw
    assert not machine.armed and not machine._coordinate_reference_ready
    assert machine._laser_focus.job_plan is None
    assert rig.owner.generation == generation and rig.owner.ready
    assert not any(s.startswith(("M112", "G1", "G28", "G39", "M124")) for s in rig.serial.writes)
    machine.connect()
    assert machine.status()["z_retention"]["restored"], machine.status()["z_retention"]
    assert rig.serial.z == 25 and machine._laser_focus.requires_clearance
    with pytest.raises(MachineError):
        machine.start_preflighted_program(old_program, authorization_phrase=machine.ARM_PHRASE)
    machine.prepare_photo_position()
    assert rig.serial.z == 30 and not machine._laser_focus.requires_clearance
    assert machine.focus_control("status")["reference_ready"]
    assert not any(s.startswith(("G28", "G39", "M124", "M112")) for s in rig.serial.writes)
    result = machine.focus_control("set_workpiece_height", confirmed=True,
                                   value={"material_thickness_mm": 4., "spacer_thickness_mm": 0.})
    assert result["job_focus"] is not None
    assert result["job_focus"]["id"] not in old_program.lines[0]


@pytest.mark.parametrize("change", ["position", "unknown", "firmware", "pin", "known_flag", "generation", "configuration", "red_stop"])
def test_changed_ender_or_red_stop_cannot_restore_z(rig, monkeypatch, change):  # noqa: F811
    machine = rig.machine
    running(rig, monkeypatch)
    machine.request_job_stop()
    wait(machine)
    if change == "position":
        rig.serial.z += 1
    elif change == "unknown":
        rig.serial.homed = False
    elif change == "firmware":
        rig.serial.overrides["M115"] = ["Different firmware", "ok"]
    elif change == "pin":
        rig.serial.overrides["M119"] = ["z_min: open", "ok"]
    elif change == "known_flag":
        rig.serial.overrides["M119"] = ["z_min: triggered", "test_axis_known_z_flag = false", "ok"]
    elif change == "generation":
        rig.owner._generation += 1
    elif change == "configuration":
        machine.settings.mainboard_max_z_mm -= 1
    else:
        machine.request_stop(emergency=True, _recover=False)
    machine.connect()
    assert not machine.status()["z_retention"]["restored"]
    assert machine._laser_focus.retain_z_reference(rig.owner.generation) is None
    assert machine._laser_focus.job_plan is None
    assert not any(s.startswith(("G1", "G28", "G39", "M124")) for s in rig.serial.writes)


@pytest.mark.parametrize("busy", ["z_motion", "focus", "no_readback", "stale_reference"])
def test_stop_during_uncertain_z_discards_reference_but_still_aborts_xy(rig, monkeypatch, busy):  # noqa: F811
    machine = rig.machine
    _, _, raw = running(rig, monkeypatch)
    if busy == "z_motion":
        machine._z_probe_active = True
    elif busy == "focus":
        machine._focus_operation_active = True
    elif busy == "no_readback":
        machine._active_job_context.verified_z.clear()
    else:
        machine._laser_focus.drop_z_reference()
    machine.request_job_stop()
    wait(machine)
    assert b"!\x18" in raw
    assert machine._stopped_z is None
    assert machine._laser_focus._z_reference is None
    machine._z_probe_active = machine._focus_operation_active = False


def test_red_stop_during_recovery_readback_cannot_publish_z(rig, monkeypatch):  # noqa: F811
    machine = rig.machine
    running(rig, monkeypatch)
    machine.request_job_stop()
    wait(machine)
    read = rig.owner._execute_acknowledged

    def interrupted(command, **kwargs):
        result = read(command, **kwargs)
        if command == "M114":
            machine.request_stop(emergency=True, _recover=False)
        return result

    monkeypatch.setattr(rig.owner, "_execute_acknowledged", interrupted)
    try:
        machine.connect()
    except MachineError:
        pass
    assert machine._laser_focus._z_reference is None
    assert not machine._z_retention_restored
