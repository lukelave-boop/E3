"""Saved bed datum: persistence, admission, rejection and authenticated transport."""
import copy
import json

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.laser_focus import THICKNESS_CAPABILITY, LaserFocus, thickness_focus, validate_thickness_plan
from tests import test_remote_laser_focus as remote_helpers
from tests import test_thickness_focus as helpers
from tests.test_job_focus import PROGRAM, select, wait
from tests.test_thickness_focus import automatic

focus = helpers.focus
focus_machine = helpers.focus_machine
focus_server = helpers.focus_server
machine_probe = helpers.machine_probe
remote_focus = helpers.remote_focus
server_harness = helpers.server_harness


def test_save_preserves_reference_and_gauge_but_requires_new_measurement(focus_machine):
    machine, focus, primary = focus_machine
    automatic(machine, focus)
    calibration = copy.deepcopy(focus.state.calibration)
    reference = copy.deepcopy(focus.state.reference)
    before = len(focus.serial.writes)
    saved = machine.focus_control("set_honeycomb_height", confirmed=True, value=-1.6)
    assert saved["honeycomb_height_mm"] == -1.6
    assert saved["honeycomb_height_persistent"] is True
    assert saved["surface"] is saved["preview"] is saved["job_focus"] is None
    assert focus.state.reference == reference and focus.state.calibration == calibration
    assert not any(s.startswith(("G", "M280", "M112")) for s in focus.serial.writes[before:])
    with pytest.raises(SafetyError, match="measure the workpiece again"):
        machine.preflight_program(PROGRAM)
    restored = LaserFocus(focus.state.path, "ender")
    assert restored.honeycomb_height_mm == -1.6
    assert restored.calibration == calibration and restored.job_plan is None
    focus.serial.contacts = [-1.532]
    measured = machine.focus_control("measure_workpiece", confirmed=True, value=0)
    plan = measured["job_focus"]
    assert plan["material_thickness_mm"] == pytest.approx(.068)
    assert plan["gap_mm"] == pytest.approx(7-.068*2/3)
    assert plan["target_z_mm"] == pytest.approx(-1.532 + calibration["focus_offset_mm"] + plan["gap_mm"] - 7)
    program = machine.preflight_program(PROGRAM)
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None and focus.serial.z == 30
    assert not primary.laser_on


@pytest.mark.parametrize("value", [None, True, "-1.6", float("nan"), float("inf"), -10.001, 10.001])
def test_invalid_save_rejected_without_io_or_state_change(focus_machine, value):
    machine, focus, _ = focus_machine
    automatic(machine, focus)
    previous = copy.deepcopy(focus.state.job_plan)
    before = list(focus.serial.writes)
    with pytest.raises(SafetyError):
        machine.focus_control("set_honeycomb_height", confirmed=True, value=value)
    assert focus.serial.writes == before
    assert focus.state.job_plan == previous
    assert not focus.state.honeycomb_path.exists()


@pytest.mark.parametrize("height", [-10., -1.6, 0., 10.])
def test_saved_datum_range_and_default_migration(focus, height):
    assert focus.state.honeycomb_height_mm == -1.5
    focus.run("set_honeycomb_height", value=height)
    restored = LaserFocus(focus.state.path, "ender")
    assert restored.honeycomb_height_mm == height
    assert restored.job_focus_required and restored.job_plan is None


@pytest.mark.parametrize("block", ["unconfirmed", "armed", "motion_disabled", "hardware_disabled", "stop"])
def test_save_cannot_bypass_service_guards(focus_machine, block):
    machine, focus, _ = focus_machine
    automatic(machine, focus)
    if block == "armed":
        machine.arm(machine.ARM_PHRASE)
    elif block == "motion_disabled":
        machine.settings.allow_motion = False
    elif block == "hardware_disabled":
        machine.hardware_enabled = False
    elif block == "stop":
        machine.request_stop(_recover=False)
    with pytest.raises(MachineError):
        machine.focus_control("set_honeycomb_height", confirmed=block != "unconfirmed", value=-1.6)
    assert not focus.state.honeycomb_path.exists()
    assert focus.state.honeycomb_height_mm == -1.5


@pytest.mark.parametrize("change", [dict(schema_version=2), dict(schema_version=True),
                                  dict(controller_port="other"), dict(honeycomb_height_mm=None),
                                  dict(honeycomb_height_mm=-11), dict(extra=1)])
def test_invalid_saved_datum_never_silently_defaults(focus, change):
    data = dict(schema_version=1, controller_port="ender", honeycomb_height_mm=-1.6)
    data.update(change)
    focus.state.honeycomb_path.write_text(json.dumps(data))
    with pytest.raises(MachineError, match="Invalid saved honeycomb"):
        LaserFocus(focus.state.path, "ender")


def test_failed_atomic_save_keeps_existing_setting_and_revokes_selection(focus_machine, monkeypatch):
    machine, focus, _ = focus_machine
    automatic(machine, focus)
    def failed(*args, **kwargs):
        raise OSError("disk failure")
    monkeypatch.setattr("laser_aligner.machine.laser_focus.atomic_write_json", failed)
    with pytest.raises(MachineError, match="Cannot save honeycomb"):
        machine.focus_control("set_honeycomb_height", confirmed=True, value=-1.6)
    assert focus.state.honeycomb_height_mm == -1.5
    assert focus.state.job_plan is None


def test_datum_change_cannot_reuse_stale_job_even_with_injected_plan(focus_machine):
    machine, focus, _ = focus_machine
    automatic(machine, focus)
    focus.state.honeycomb_height_mm = -1.6
    with pytest.raises(SafetyError, match="Honeycomb height changed"):
        from laser_aligner.machine.job_focus import selection_snapshot
        selection_snapshot(machine, focus.state.job_plan["id"])


def test_custom_datum_does_not_clamp_negative_or_relax_target_validation():
    with pytest.raises(SafetyError):
        thickness_focus(-1.601, 0, -1.6)
    plan = thickness_focus(-1.532, 0, -1.6)
    validate_thickness_plan(plan)
    plan["honeycomb_height_mm"] = -1.5
    with pytest.raises(SafetyError):
        validate_thickness_plan(plan)
    plan = thickness_focus(0, 0)
    plan["focus_policy"] = "linear-0-6mm-v1"
    with pytest.raises(SafetyError):
        validate_thickness_plan(plan)


def test_authenticated_save_then_measure_uses_pi_saved_value(focus_server):
    harness, focus = focus_server
    select(harness.machine)
    reply = remote_helpers.rpc(harness, "set_honeycomb_height", value=-1.6)
    assert reply["ok"], reply
    assert reply["result"]["honeycomb_height_mm"] == -1.6
    focus.serial.contacts = [-1.532]
    reply = remote_helpers.rpc(harness, "measure_workpiece", value=0)
    assert reply["ok"], reply
    assert reply["result"]["job_focus"]["material_thickness_mm"] == pytest.approx(.068)


def test_remote_old_companion_rejects_save_before_rpc(remote_focus):
    service, pi, _ = remote_focus
    pi.capabilities.append("pi-thickness-focus-v1")
    service._require_capabilities()
    before = copy.deepcopy(pi.requests)
    with pytest.raises(MachineError, match="Update the Pi"):
        service.focus_control("set_honeycomb_height", confirmed=True, value=-1.6)
    assert pi.requests == before


@pytest.mark.parametrize("bad", [False, True])
def test_remote_save_requires_acknowledged_value_and_revoked_selection(remote_focus, bad):
    service, pi, payload = remote_focus
    pi.capabilities.append(THICKNESS_CAPABILITY)
    payload.update(action="set_honeycomb_height", honeycomb_height_mm=-1.5 if bad else -1.6,
                   honeycomb_height_persistent=True, job_focus_required=True,
                   surface=None, preview=None, job_focus=None)
    if bad:
        with pytest.raises(MachineError, match="saved honeycomb"):
            service.focus_control("set_honeycomb_height", confirmed=True, value=-1.6)
    else:
        assert service.focus_control("set_honeycomb_height", confirmed=True, value=-1.6)["honeycomb_height_mm"] == -1.6
