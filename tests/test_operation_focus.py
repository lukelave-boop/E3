"""Raster surface focus, mixed jobs, and rejection paths with fake hardware."""
import copy
import uuid

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.gcode.job_plan import build_job_plan, restart_program_from_move
from laser_aligner.machine.service import MachineService
from laser_aligner.operation_focus import CAPABILITY, mode, target_z
from laser_aligner.project import LayerMode, generate_project_gcode
from tests import test_laser_focus as helpers
from tests import test_remote_laser_focus as remote_helpers
from tests.test_job_focus import PROGRAM, stop_async, wait
from tests.test_thickness_focus import automatic
from tests.test_toolpath import make_document

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe
remote_focus = remote_helpers.remote_focus


def program_for(*modes):
    lines = ["G21", "G90"]
    for index, focus_mode in enumerate(modes):
        lines.extend(("M5", f"E3OPFOCUS {focus_mode}",
                      "G0 X100 Y100 F1000", f"M4 S{100 + index}",
                      "G1 X105 Y100 F500"))
    return "\n".join((*lines, "M5"))


@pytest.mark.parametrize("thickness,spacers", [(0, 0), (1.5, 0), (4, 0), (6, 0), (4, 5)])
def test_raster_gap_tracks_surface_and_preserves_cut_plan(focus_machine, thickness, spacers):
    machine, focus, _ = focus_machine
    plan = automatic(machine, focus, thickness=thickness, spacers=spacers)["job_focus"]
    original = copy.deepcopy(plan)
    raster_z = target_z(plan, "RASTER")
    assert raster_z == pytest.approx(plan["contact_z_mm"] + plan["calibration"]["focus_offset_mm"])
    assert target_z(plan, "CUT") == original["target_z_mm"]
    assert plan == original
    program = machine.preflight_program(program_for("RASTER"))
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    assert f"G1 Z{raster_z:.3f} F600" in focus.serial.writes
    assert focus.state.job_plan["target_z_mm"] == original["target_z_mm"]
    assert focus.state.job_plan["gap_mm"] == original["gap_mm"]


@pytest.mark.parametrize("modes", [("CUT", "RASTER", "CUT"), ("RASTER", "CUT", "RASTER")])
def test_mixed_jobs_drain_lift_approach_and_refocus(focus_machine, monkeypatch, modes):
    machine, focus, primary = focus_machine
    plan = automatic(machine, focus)["job_focus"]
    events = []
    for device, label in ((primary, "xy"), (focus.serial, "z")):
        original = device.write_line

        def write(line, original=original, label=label):
            events.append((label, line))
            return original(line)

        monkeypatch.setattr(device, "write_line", write)
    program = machine.preflight_program(program_for(*modes))
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    previous_power = -1
    barrier = machine._session.dialect.motion_barrier_command
    for index, focus_mode in enumerate(modes):
        power_index = events.index(("xy", f"M4 S{100 + index}"))
        interval = events[previous_power + 1:power_index]
        lift = interval.index(("z", "G1 Z30.000 F1200"))
        approach = interval.index(("xy", "G0 X100 Y100 F1000"))
        descent = interval.index(("z", f"G1 Z{target_z(plan, focus_mode):.3f} F600"))
        assert lift < approach < descent
        assert ("xy", "M5") in interval[:lift]
        if index:
            assert ("xy", barrier) in interval[:lift]
        assert ("xy", barrier) in interval[approach:descent]
        previous_power = power_index
    assert not any(line.startswith("E3OPFOCUS") for _, line in events)


@pytest.mark.parametrize("fault", ["stop", "lift_failure", "descent_failure"])
def test_transition_failure_never_enables_next_operation(focus_machine, fault):
    machine, focus, primary = focus_machine
    plan = automatic(machine, focus)["job_focus"]
    original = primary.write_line
    writes = []

    def write(line):
        writes.append(line)
        result = original(line)
        if line == "M4 S100":
            if fault == "stop":
                focus.serial.on_write = lambda line: stop_async(machine) if line == "G1 Z30.000 F1200" else None
            else:
                target = 30. if fault == "lift_failure" else target_z(plan, "RASTER")
                feed = 1200 if fault == "lift_failure" else 600
                focus.serial.overrides[f"G1 Z{target:.3f} F{feed}"] = ["Error:motion rejected", "ok"]
        return result

    primary.write_line = write
    program = machine.preflight_program(program_for("CUT", "RASTER"))
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error
    assert "M4 S101" not in writes


@pytest.mark.parametrize("text", [
    "G21\nG90\nE3OPFOCUS RASTER\nM5",
    "E3OPFOCUS RASTER\nG21\nG90\nM5",
    "G21\nG90\nM5\nE3OPFOCUS RASTER",
    PROGRAM.replace("G1 X105 Y100 F500", "E3OPFOCUS RASTER\nG1 X105 Y100 F500"),
    PROGRAM + "\nE3OPFOCUS RASTER\nM5",
    program_for("RASTER M3 S100"),
    program_for("UNKNOWN"),
])
def test_unsafe_directives_rejected(text):
    machine = MachineService(MachineSettings(allow_motion=True), LaserSettings(), hardware_enabled=True)
    with pytest.raises(SafetyError, match="focus|M5"):
        machine.preflight_program(text)


@pytest.mark.parametrize("field,value", [
    ("target_z_mm", float("nan")), ("gap_mm", float("inf")),
    ("gap_mm", True), ("gap_mm", 8), ("target_z_mm", -8),
    ("clearance_z_mm", 81), ("maximum", 20),
])
def test_invalid_focus_target_metadata_rejected(field, value):
    plan = {"target_z_mm": 20., "gap_mm": 3., "clearance_z_mm": 30., "maximum": 40.}
    plan[field] = value
    with pytest.raises(SafetyError):
        target_z(plan, "RASTER")


def test_raster_above_clearance_rejected_before_motion(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    # Cut focus fits beneath clearance but the 7 mm raster gap does not.
    plan = automatic(machine, focus, thickness=6, spacers=6)["job_focus"]
    assert plan is not None and plan["target_z_mm"] < 30
    assert plan["target_z_mm"] + 7 - plan["gap_mm"] >= 30
    writes = []
    original = primary.write_line
    monkeypatch.setattr(primary, "write_line", lambda line: (writes.append(line), original(line))[1])
    before = list(focus.serial.writes)
    program = machine.preflight_program(program_for("RASTER"))
    with pytest.raises(SafetyError, match="clearance"):
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    assert not writes
    assert before == focus.serial.writes


def test_raster_requires_measurement_even_before_focus_setup(focus_machine):
    machine, focus, _ = focus_machine
    assert focus.state.job_plan is None
    program = machine.preflight_program(program_for("RASTER"))
    with pytest.raises(SafetyError, match="Measure the workpiece"):
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)


def test_generation_and_start_here_keep_operation_focus():
    document = make_document()
    cut = generate_project_gcode(document, LaserSettings())
    assert "E3OPFOCUS" not in cut.text
    document.layers[0].mode = LayerMode.FILL
    assert "E3OPFOCUS" not in generate_project_gcode(document, LaserSettings()).text
    document.layers[0].mode = LayerMode.RASTER
    document.layers[0].line_interval_mm = 2
    raster = generate_project_gcode(document, LaserSettings())
    assert "M5\nE3OPFOCUS RASTER" in raster.text
    powered = [move for move in raster.plan.moves if move.laser_on and move.power]
    assert powered and all(move.focus_mode == "RASTER" for move in powered)
    restarted, plan = restart_program_from_move(raster.plan, powered[len(powered) // 2].index)
    assert "E3OPFOCUS RASTER" in restarted
    assert all(move.focus_mode == "RASTER" for move in plan.moves if move.laser_on)
    MachineService(MachineSettings(allow_motion=True), LaserSettings(), hardware_enabled=True).preflight_program(restarted)


def test_start_here_preserves_mixed_mode_transitions():
    plan = build_job_plan(program_for("CUT", "RASTER", "CUT"), power_max=1000)
    selected = next(move for move in plan.moves if move.focus_mode == "RASTER" and move.laser_on)
    text, restarted = restart_program_from_move(plan, selected.index)
    assert [mode(line) for line in text.splitlines() if mode(line)] == ["RASTER", "CUT"]
    assert [move.focus_mode for move in restarted.moves if move.laser_on] == ["RASTER", "CUT"]
    MachineService(MachineSettings(allow_motion=True), LaserSettings(), hardware_enabled=True).preflight_program(text)


@pytest.mark.parametrize("capable", [False, True])
def test_remote_requires_matching_capability_and_valid_target(remote_focus, capable):
    service, pi, result = remote_focus
    if capable:
        pi.capabilities.append(CAPABILITY)
    result.update(job_focus_required=True, job_focus={
        "id": str(uuid.uuid4()), "target_z_mm": 20., "gap_mm": 3.,
        "clearance_z_mm": 30., "reusable": True,
    })
    service.focus_control("status")
    if not capable:
        with pytest.raises(SafetyError, match="Update the Pi"):
            service.preflight_program(program_for("RASTER"))
    else:
        program = service.preflight_program(program_for("RASTER"))
        service._require_current_program(program)
        service._selected_job_focus["target_z_mm"] = 29.
        with pytest.raises(MachineError, match="clearance"):
            service._require_current_program(program)
