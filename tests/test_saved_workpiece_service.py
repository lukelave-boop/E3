from __future__ import annotations

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.laser_focus import thickness_focus
from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_FOCUS
from tests import test_machine_z_retention, test_remote_laser_focus
from tests.test_job_focus import wait

rig = test_machine_z_retention.rig
remote_focus = test_remote_laser_focus.remote_focus


def restart_with_material(rig, *, manual=False):
    machine = rig.machine
    surface = machine.focus_control("measure", confirmed=True)["surface"]["id"]
    machine.focus_control("jog", confirmed=True, value=-5, measurement_id=surface)
    machine.focus_control("teach", confirmed=True, measurement_id=surface)
    machine.focus_control("clearance", confirmed=True)
    rig.serial.contacts = [2.]
    if manual:
        original = machine.focus_control("set_workpiece_height", confirmed=True,
                                         value=dict(material_thickness_mm=3., spacer_thickness_mm=1.))["job_focus"]
    else:
        original = machine.focus_control("measure_workpiece", confirmed=True, value=1.)["job_focus"]
    machine.disconnect()
    rig.serial.z, rig.serial.homed = 0., False
    restarted = rig.create()
    restarted.connect()
    assert restarted._laser_focus.saved_workpiece is not None
    assert restarted.status()["controller_state"] == "READY_HOME_REQUIRED"
    before_home = len(rig.serial.writes)
    with pytest.raises(MachineError, match="Home / park"):
        restarted.focus_control("reuse_workpiece", confirmed=True)
    assert len(rig.serial.writes) == before_home
    return restarted, original


@pytest.mark.parametrize("manual", [False, True])
def test_clean_restart_automatically_reuses_after_home(rig, manual):
    restarted, original = restart_with_material(rig, manual=manual)
    before = len(rig.serial.writes)
    restarted.prepare_photo_position()
    result = restarted.focus_control("status")
    assert result["job_focus"]["target_z_mm"] == original["target_z_mm"]
    assert result["job_focus"]["id"] != original["id"]
    assert result["surface"] is None
    assert result["job_focus"]["height_source"] == ("manual" if manual else "measured")
    assert not any(c.startswith(("G1", "G28", "G39")) for c in rig.serial.writes[before:])
    program = restarted.preflight_program("G21\nG90\nM5\nG0 X100 Y100 F1000\nM4 S100\nG1 X105 Y100 F500\nM5")
    assert program.lines[0] == "E3FOCUS " + result["job_focus"]["id"]
    restarted.start_preflighted_program(program, authorization_phrase=restarted.ARM_PHRASE)
    wait(restarted)
    assert restarted._job.error is None and rig.serial.z == 30
    restarted.disconnect()


def test_home_without_verified_park_defers_reuse(rig):
    restarted, _ = restart_with_material(rig)
    restarted.prepare_job_start()
    assert restarted._laser_focus.job_plan is None
    assert restarted._laser_focus.saved_workpiece is not None
    restarted.prepare_photo_position()
    assert restarted._laser_focus.job_plan is not None


@pytest.mark.parametrize("change", ["calibration", "maximum", "unknown_z", "stopped", "home_failed", "clear"])
def test_automatic_reuse_never_bypasses_changed_state(rig, monkeypatch, change):
    restarted, _ = restart_with_material(rig)
    if change == "clear":
        restarted.focus_control("clear_surface", confirmed=True)
    elif change == "calibration":
        restarted._laser_focus.calibration["focus_offset_mm"] += 1
    elif change == "maximum":
        restarted.settings.mainboard_max_z_mm = 70
    elif change == "unknown_z":
        rig.serial.homed = False
    elif change == "stopped":
        restarted.request_stop(_recover=False)
    elif change == "home_failed":
        def fail(**kwargs):
            raise MachineError("Home failed")
        monkeypatch.setattr(restarted, "_prepare_photo_position_locked", fail)
    before = len(rig.serial.writes)
    if change == "clear":
        restarted.prepare_photo_position()
    else:
        with pytest.raises(MachineError):
            restarted.prepare_photo_position()
    assert restarted._laser_focus.job_plan is None
    assert not any(c.startswith(("G1", "G28", "G39")) for c in rig.serial.writes[before:])


def test_stop_between_home_and_reuse_cannot_create_new_authority(rig, monkeypatch):
    restarted, _ = restart_with_material(rig)
    original = restarted.focus_control

    def stop_before_selection(action, **kwargs):
        if action == "reuse_workpiece":
            restarted.request_stop(_recover=False)
        return original(action, **kwargs)

    monkeypatch.setattr(restarted, "focus_control", stop_before_selection)
    with pytest.raises(MachineError):
        restarted.prepare_photo_position()
    assert restarted._laser_focus.job_plan is None


def test_remote_reuse_requires_new_companion_before_sending_action(remote_focus):
    service, pi, _ = remote_focus
    with pytest.raises(MachineError, match="Pi companion"):
        service.focus_control("reuse_workpiece", confirmed=True)
    assert not any(r["action"] == ACTION_MACHINE_FOCUS for r in pi.requests)


@pytest.mark.parametrize("selected", [True, False])
def test_remote_reuse_requires_a_valid_selected_plan(remote_focus, selected):
    import uuid

    service, pi, result = remote_focus
    pi.capabilities.extend(["pi-saved-workpiece-v1", "pi-thickness-focus-v2"])
    result.update(action="reuse_workpiece", thickness_focus_available=True,
                  job_focus_required=True, job_focus=None)
    if selected:
        result["job_focus"] = dict(thickness_focus(2., 1.), id=str(uuid.uuid4()),
                                  target_z_mm=15.333333333333334, clearance_z_mm=30., reusable=True)
        accepted = service.focus_control("reuse_workpiece", confirmed=True)
        assert accepted["job_focus"]["id"] == result["job_focus"]["id"]
        assert accepted["saved_workpiece_available"]
    else:
        with pytest.raises(MachineError, match="did not select"):
            service.focus_control("reuse_workpiece", confirmed=True)
        assert service._selected_job_focus is None
