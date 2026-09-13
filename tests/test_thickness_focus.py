"""Thickness policy and guarded job execution, with fake controller I/O."""
import copy

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.laser_focus import (
    THICKNESS_CAPABILITY,
    thickness_focus,
    validate_thickness_plan,
)
from tests import test_laser_focus as helpers
from tests import test_remote_laser_focus as remote_helpers
from tests.test_job_focus import PROGRAM, select, wait

remote_focus = remote_helpers.remote_focus
focus_server = remote_helpers.focus_server
server_harness = remote_helpers.server_harness
focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe


@pytest.mark.parametrize("thickness,gap", [(0, 7), (.2, 6.8666666667), (1.5, 6), (3, 5),
                                         (4, 4.3333333333), (6, 3), (9, 3)])
def test_linear_gap_and_spacer_subtraction(thickness, gap):
    for spacers in (0, 5, 12):
        derived = thickness_focus(thickness + spacers - 1.5, spacers)
        assert derived["material_thickness_mm"] == pytest.approx(thickness)
        assert derived["gap_mm"] == pytest.approx(gap)
        validate_thickness_plan(derived)


@pytest.mark.parametrize("height,spacer", [(-1.501, 0), (0, 2), (float("nan"), 0),
                                         (float("inf"), 0), (0, -1), (0, True), (0, 81)])
def test_invalid_thickness_rejected(height, spacer):
    with pytest.raises(SafetyError):
        thickness_focus(height, spacer)


def automatic(machine, focus, *, thickness=4., spacers=0.):
    select(machine)
    focus.serial.contacts = [thickness + spacers - 1.5]
    return machine.focus_control("measure_workpiece", confirmed=True, value=spacers)


@pytest.mark.parametrize("spacers", [0., 5.])
def test_automatic_fractional_focus_precedes_output_for_repeated_jobs(focus_machine, monkeypatch, spacers):
    machine, focus, primary = focus_machine
    result = automatic(machine, focus, spacers=spacers)
    plan = result["job_focus"]
    assert result["action"] == "measure_workpiece"
    assert plan["material_thickness_mm"] == 4
    assert plan["gap_mm"] == pytest.approx(13 / 3)
    assert plan["target_z_mm"] == pytest.approx(19.8333333333 + spacers)
    events = []
    for device, label in ((primary, "xy"), (focus.serial, "z")):
        original = device.write_line

        def write(line, original=original, label=label):
            events.append((label, line))
            return original(line)

        monkeypatch.setattr(device, "write_line", write)
    for _ in range(2):
        events.clear()
        program = machine.preflight_program(PROGRAM)
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
        wait(machine)
        assert machine._job.error is None
        move = ("z", f"G1 Z{plan['target_z_mm']:.3f} F600")
        assert events.index(("xy", "G0 X100 Y100 F1000")) < events.index(move) < events.index(("xy", "M4 S100"))
        assert focus.serial.z == 30
        assert focus.state.job_plan["id"] == plan["id"]


def test_failed_thickness_cannot_reuse_previous_focus(focus_machine):
    machine, focus, primary = focus_machine
    automatic(machine, focus)
    focus.serial.contacts = [-1.6]
    with pytest.raises(SafetyError, match="thickness"):
        machine.focus_control("measure_workpiece", confirmed=True, value=0)
    assert focus.state.job_plan is None
    with pytest.raises(MachineError):
        machine.preflight_program(PROGRAM)
    assert not primary.laser_on


def test_manual_gap_cannot_override_automatic_workpiece(focus_machine):
    machine, focus, _ = focus_machine
    automatic(machine, focus)
    with pytest.raises(SafetyError, match="calculated from thickness"):
        machine.focus_control("set_job_gap", confirmed=True, gap_mm=7)


@pytest.mark.parametrize("field,value", [("gap_mm", 7), ("material_thickness_mm", 2),
                                        ("spacer_thickness_mm", 1), ("target_z_mm", 2),
                                        ("focus_policy", "unknown")])
def test_altered_derived_plan_rejects_before_power(focus_machine, field, value):
    machine, focus, primary = focus_machine
    automatic(machine, focus)
    focus.state.job_plan[field] = value
    program = machine.preflight_program(PROGRAM)
    with pytest.raises(SafetyError, match="Thickness-derived"):
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    assert not primary.laser_on


def test_remote_requires_capability_before_sending_measurement(remote_focus):
    service, pi, _ = remote_focus
    pi.capabilities = [c for c in pi.capabilities if c != THICKNESS_CAPABILITY]
    service._require_capabilities()
    before = copy.deepcopy(pi.requests)
    with pytest.raises(MachineError, match="thickness-based"):
        service.focus_control("measure_workpiece", confirmed=True, value=0)
    assert pi.requests == before


def test_remote_accepts_and_binds_fractional_thickness_focus(remote_focus):
    service, pi, result = remote_focus
    pi.capabilities.append(THICKNESS_CAPABILITY)
    from tests.test_remote_workpiece_focus import measured
    plan = measured(result)
    plan.update(thickness_focus(2.5, 0))
    result.update(action="measure_workpiece", thickness_focus_available=True)
    service.focus_control("measure_workpiece", confirmed=True, value=0)
    assert pi.requests[-1]["value"] == 0
    assert service.preflight_program(PROGRAM).lines[0] == "E3FOCUS " + plan["id"]


def test_authenticated_pi_calculates_gap_from_probe_and_spacers(focus_server):
    harness, focus = focus_server
    select(harness.machine)
    focus.serial.contacts = [7.5]  # 4 mm sheet on 5 mm spacers, bed at -1.5.
    response = remote_helpers.rpc(harness, "measure_workpiece", value=5.)
    assert response["ok"], response
    plan = response["result"]["job_focus"]
    assert plan["material_thickness_mm"] == 4
    assert plan["gap_mm"] == pytest.approx(13/3)
    assert plan["target_z_mm"] == pytest.approx(24.8333333333)


@pytest.mark.parametrize("value", [None, True, -1., 81., "5"])
def test_authenticated_pi_rejects_invalid_spacers_before_serial(focus_server, value):
    harness, focus = focus_server
    before = list(focus.serial.writes)
    response = remote_helpers.rpc(harness, "measure_workpiece", value=value)
    assert not response["ok"]
    assert focus.serial.writes == before
