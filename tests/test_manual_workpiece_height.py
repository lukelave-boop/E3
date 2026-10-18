"""Manual dimensions retain machine datum, focus, session and output guards."""
import copy
from types import SimpleNamespace

import pytest

from laser_aligner.errors import CalibrationError, MachineError, SafetyError
from laser_aligner.machine.laser_focus import MANUAL_HEIGHT_CAPABILITY, THICKNESS_CAPABILITY, validate_request
from tests import test_laser_focus as helpers
from tests import test_remote_laser_focus as remote_helpers
from tests.test_job_focus import PROGRAM, select, wait

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe
focus_server = remote_helpers.focus_server
server_harness = remote_helpers.server_harness
remote_focus = remote_helpers.remote_focus


def dimensions(thickness=4., spacers=0.):
    return dict(material_thickness_mm=thickness, spacer_thickness_mm=spacers)


def apply(machine, **kwargs):
    return machine.focus_control("set_workpiece_height", confirmed=True, value=dimensions(**kwargs))


@pytest.mark.parametrize("spacers", [0., 5.])
def test_manual_height_no_probe_or_motion_then_guarded_job(focus_machine, spacers):
    machine, focus, primary = focus_machine
    select(machine)
    before = len(focus.serial.writes)
    result = apply(machine, spacers=spacers)
    plan = result["job_focus"]
    assert plan["height_source"] == result["surface"]["height_source"] == "manual"
    assert "measured_at" not in result["surface"]
    assert plan["material_thickness_mm"] == 4
    assert plan["surface_elevation_mm"] == 2.5 + spacers
    assert plan["target_z_mm"] == pytest.approx(19.8333333333 + spacers)
    assert not any(line.startswith(("G", "M4", "M3")) for line in focus.serial.writes[before:])
    assert not primary.laser_on
    program = machine.preflight_program(PROGRAM)
    assert program.lines[0] == "E3FOCUS " + plan["id"]
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    assert f"G1 Z{plan['target_z_mm']:.3f} F600" in focus.serial.writes[before:]
    assert focus.serial.z == 30 and not primary.laser_on


@pytest.mark.parametrize("value", [None, {}, True, "4", dimensions(-1), dimensions(float("nan")),
                                  dimensions(float("inf")), dimensions(True), dimensions(spacers=-1),
                                  dimensions(spacers=81), dict(dimensions(), extra=1)])
def test_bad_dimensions_rejected_before_serial(focus_machine, value):
    machine, focus, _ = focus_machine
    before = list(focus.serial.writes)
    with pytest.raises(SafetyError):
        machine.focus_control("set_workpiece_height", confirmed=True, value=value)
    assert focus.serial.writes == before


def test_confirmation_required():
    with pytest.raises(SafetyError, match="Confirm"):
        validate_request("set_workpiece_height", value=dimensions())


def test_manual_height_cannot_be_camera_calibration_evidence():
    from laser_aligner.material_workspace import MaterialWorkspaceMixin
    context = SimpleNamespace(machine=SimpleNamespace(
        material_surface_snapshot=lambda: {"height_source": "manual"},
    ))
    with pytest.raises(CalibrationError, match="Probe the calibration target"):
        MaterialWorkspaceMixin._surface_acquisition_measurement(context, 4., .1)


@pytest.mark.parametrize("failure", ["reference", "unknown_z", "gauge", "clearance", "too_tall",
                                    "raster_clearance", "recovery", "motion_disabled", "armed"])
def test_manual_height_rejects_missing_authority_without_motion(focus_machine, failure):
    machine, focus, primary = focus_machine
    select(machine)
    if failure == "reference":
        focus.state.drop_z_reference()
    elif failure == "unknown_z":
        focus.serial.homed = False
    elif failure == "gauge":
        focus.state.calibration = None
    elif failure == "clearance":
        focus.serial.z = 29
    elif failure == "recovery":
        focus.state.xy_recovery_pending_reference = True
    elif failure == "motion_disabled":
        machine.settings.allow_motion = False
    elif failure == "armed":
        machine.arm(machine.ARM_PHRASE)
    before = len(focus.serial.writes)
    with pytest.raises((SafetyError, MachineError)):
        apply(machine, thickness=40 if failure == "too_tall" else 13 if failure == "raster_clearance" else 4)
    assert not any(line.startswith(("G", "M4", "M3")) for line in focus.serial.writes[before:])
    assert not primary.laser_on


def test_changed_height_rejects_previously_generated_job(focus_machine):
    machine, focus, primary = focus_machine
    select(machine)
    apply(machine)
    program = machine.preflight_program(PROGRAM)
    apply(machine, thickness=2)
    with pytest.raises(SafetyError, match="stale"):
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    assert not primary.laser_on


@pytest.mark.parametrize("action", ["teach", "preview", "jog"])
def test_manual_entry_cannot_be_used_as_probe_evidence(focus_machine, action):
    machine, focus, _ = focus_machine
    select(machine)
    result = apply(machine)
    with pytest.raises(SafetyError, match="Probe the surface"):
        machine.focus_control(action, confirmed=True, measurement_id=result["surface"]["id"],
                              **({"value": -1.} if action == "jog" else {}))


def test_remote_missing_capability_rejects_before_rpc(remote_focus):
    service, pi, _ = remote_focus
    pi.capabilities = [c for c in pi.capabilities if c != MANUAL_HEIGHT_CAPABILITY]
    service._require_capabilities()
    before = copy.deepcopy(pi.requests)
    with pytest.raises(MachineError, match="manual workpiece height"):
        apply(service)
    assert pi.requests == before


def test_authenticated_manual_height(focus_server):
    harness, focus = focus_server
    select(harness.machine)
    response = remote_helpers.rpc(harness, "set_workpiece_height", value=dimensions(spacers=5))
    assert response["ok"], response
    plan = response["result"]["job_focus"]
    assert plan["height_source"] == "manual" and plan["entered_height"] == dimensions(spacers=5)


def test_remote_accepts_manual_focus_and_binds_program(remote_focus, focus_machine):
    machine, _, _ = focus_machine
    select(machine)
    payload = apply(machine)
    service, pi, result = remote_focus
    pi.capabilities.extend([MANUAL_HEIGHT_CAPABILITY, THICKNESS_CAPABILITY])
    result.update(payload)
    service.focus_control("set_workpiece_height", confirmed=True, value=dimensions())
    assert pi.requests[-1]["value"] == dimensions()
    assert service.preflight_program(PROGRAM).lines[0] == "E3FOCUS " + payload["job_focus"]["id"]


@pytest.mark.parametrize("border", [.003, -.137])
def test_manual_height_with_nonzero_border_has_consistent_surface_snapshot(focus_machine, border):
    machine, focus, _ = focus_machine
    select(machine)
    focus.state.reference["border_z_mm"] = border
    focus.state._z_reference["border_z_mm"] = border
    apply(machine, thickness=.2, spacers=5.1)
    snapshot = machine.material_surface_snapshot()
    assert snapshot["height_source"] == "manual"
    assert snapshot["material_thickness_mm"] == pytest.approx(.2)


@pytest.mark.parametrize("field,value", [("surface_elevation_mm", 3), ("contact_z_mm", 3),
                                        ("entered_height", dimensions(2))])
def test_manual_plan_tampering_rejected(focus_machine, field, value):
    machine, focus, primary = focus_machine
    select(machine)
    apply(machine)
    focus.state.job_plan[field] = value
    program = machine.preflight_program(PROGRAM)
    with pytest.raises(SafetyError, match="Manual workpiece height"):
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    assert not primary.laser_on


@pytest.mark.parametrize("event", ["stop", "disconnect", "clear", "honeycomb"])
def test_manual_height_invalidated(focus_machine, event):
    machine, focus, primary = focus_machine
    select(machine)
    apply(machine)
    if event == "stop":
        machine.request_stop(_recover=False)
    elif event == "disconnect":
        machine.disconnect()
    elif event == "clear":
        machine.focus_control("clear_surface", confirmed=True)
    else:
        machine.focus_control("set_honeycomb_height", confirmed=True, value=-1.)
    assert focus.state.job_plan is None
    assert not primary.laser_on
