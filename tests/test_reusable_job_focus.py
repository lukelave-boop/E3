from __future__ import annotations

import copy

import pytest

from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.gcode.preview import parse_words
from laser_aligner.machine.laser_focus import LaserFocus
from tests import test_laser_focus as helpers
from tests import test_secondary_controller as secondary_helpers
from tests.test_job_focus import PROGRAM, select, wait

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe

FRAME = "G21\nG90\nM5\nG0 X100 Y100 F1000\nG1 X105 Y100 F500\nM5"


def measure_workpiece(machine, focus):
    select(machine)  # Establish the saved gauge calibration once.
    machine.focus_control("set_xy_offset", confirmed=True, value=[3, -4])
    machine.focus_control("position_probe", confirmed=True, value=[80, 80])
    focus.serial.contacts = [8.0]
    return machine.focus_control("measure", confirmed=True)


@pytest.mark.parametrize("home_after", [True, False])
def test_measure_in_probe_phase_automatically_focuses_every_job(focus_machine, monkeypatch, home_after):
    machine, focus, primary = focus_machine
    result = measure_workpiece(machine, focus)
    plan = result["job_focus"]
    assert result["xy_sequence"]["phase"] == "probe"
    assert result["job_focus_required"] and plan["reusable"]
    assert plan["laser_target_xy_mm"] == [80, 80]
    assert plan["xy"] == [77, 84]
    assert plan["target_z_mm"] == 28
    assert focus.serial.z == 30
    machine.settings.home_and_release_after_powered_job = home_after
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
        assert program.lines[0] == "E3FOCUS " + plan["id"]
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
        wait(machine)
        assert machine._job.error is None
        assert events.index(("xy", "G0 X100 Y100 F1000")) < events.index(("z", "G1 Z28.000 F600"))
        assert events.index(("z", "G1 Z28.000 F600")) < events.index(("xy", "M4 S100"))
        assert focus.state.job_plan["id"] == plan["id"]
        assert focus.serial.z == 30


def test_probe_datum_survives_home_status_preview_and_same_spot_laser_transfer(focus_machine):
    machine, focus, _ = focus_machine
    result = measure_workpiece(machine, focus)
    plan = result["job_focus"]
    surface = result["surface"]["id"]
    moved = machine.focus_control("align_laser", confirmed=True, measurement_id=surface)
    assert moved["job_focus"]["id"] == plan["id"]
    assert moved["job_focus"]["xy"] == [80, 80]
    machine.focus_control("preview", confirmed=True, measurement_id=surface, gap_mm=3)
    assert focus.state.job_plan["gap_mm"] == 7
    machine.prepare_photo_position()
    status = machine.focus_control("status", gap_mm=3, clearance_z_mm=45)
    assert status["surface"] is None
    assert status["job_focus"]["id"] == plan["id"]
    assert status["job_focus"]["gap_mm"] == 7
    assert status["job_focus"]["clearance_z_mm"] == 30


def test_gap_change_after_job_rebinds_datum_without_motion(focus_machine):
    machine, focus, primary = focus_machine
    plan = measure_workpiece(machine, focus)["job_focus"]
    previous = machine.preflight_program(PROGRAM)
    machine.start_preflighted_program(previous, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    before = list(focus.serial.writes)
    result = machine.focus_control("set_job_gap", confirmed=True, gap_mm=5)
    assert result["surface"] is None
    assert result["job_focus"]["id"] != plan["id"]
    assert result["job_focus"]["target_z_mm"] == 26
    assert not any(line.startswith(("G1", "G28", "G39")) for line in focus.serial.writes[len(before):])
    with pytest.raises(MachineError, match="stale"):
        machine.start_preflighted_program(previous, authorization_phrase=machine.ARM_PHRASE)


def test_laser_off_frame_retains_workpiece_without_z_motion(focus_machine):
    machine, focus, _ = focus_machine
    plan = measure_workpiece(machine, focus)["job_focus"]
    before = list(focus.serial.writes)
    machine.start_preflighted_program(machine.preflight_program(FRAME))
    wait(machine)
    assert machine._job.error is None
    assert focus.state.job_plan["id"] == plan["id"]
    assert focus.state.job_plan["xy"] == [105, 100]
    assert not any(line.startswith(("G1", "G28", "G39")) for line in focus.serial.writes[len(before):])
    machine.start_preflighted_program(machine.preflight_program(PROGRAM), authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None


@pytest.mark.parametrize("laser", ["M3S100", "M4S100"])
def test_compact_gcode_cannot_skip_focus_and_partial_axis_endpoint_is_retained(focus_machine, monkeypatch, laser):
    machine, focus, primary = focus_machine
    measure_workpiece(machine, focus)
    machine.settings.home_and_release_after_powered_job = False
    powered_z = []
    original = primary.write_line

    def write(line):
        if line == laser:
            powered_z.append(focus.serial.z)
        return original(line)

    monkeypatch.setattr(primary, "write_line", write)
    text = f"G21\nG90\nM5\nG0X100Y100F1000\n{laser}\nG1X105F500\nM5"
    machine.start_preflighted_program(machine.preflight_program(text), authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    assert powered_z == [28]
    assert focus.state.job_plan["xy"] == [105, 100]


def test_new_measurement_replaces_prior_focus_and_old_program(focus_machine):
    machine, focus, _ = focus_machine
    plan = measure_workpiece(machine, focus)["job_focus"]
    previous = machine.preflight_program(PROGRAM)
    focus.serial.contacts = [6.0]
    changed = machine.focus_control("measure", confirmed=True)["job_focus"]
    assert changed["id"] != plan["id"]
    assert changed["target_z_mm"] == 26
    with pytest.raises(MachineError, match="stale"):
        machine.start_preflighted_program(previous, authorization_phrase=machine.ARM_PHRASE)


def test_zero_power_mode_keeps_xy_approach_at_clearance_until_first_positive_inline_power(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    measure_workpiece(machine, focus)
    observed = []
    original = primary.write_line

    def write(line):
        observed.append((line, focus.serial.z))
        return original(line)

    monkeypatch.setattr(primary, "write_line", write)
    text = "G21\nG90\nM5\nG0X100Y100F1000\nM4S0\nG0X110Y100F1000\nG1X115F500S100\nM5"
    machine.start_preflighted_program(machine.preflight_program(text), authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    assert ("M4S0", 30) in observed
    assert ("G0X110Y100F1000", 30) in observed
    assert ("G1X115F500S100", 28) in observed


def test_measurement_without_taught_focus_blocks_powered_jobs_but_not_frame(focus_machine):
    machine, _, _ = focus_machine
    ordinary = machine.preflight_program(PROGRAM)
    machine.focus_control("reference", confirmed=True)
    result = machine.focus_control("measure", confirmed=True)
    assert result["surface"] and result["job_focus"] is None
    with pytest.raises(SafetyError, match="Teach"):
        machine.preflight_program(PROGRAM)
    with pytest.raises(SafetyError, match="Teach"):
        machine.start_preflighted_program(ordinary, authorization_phrase=machine.ARM_PHRASE)
    assert not machine.preflight_program(FRAME).requires_laser_authorization


@pytest.mark.parametrize("change", ["stop", "invalidate", "calibration", "reference", "firmware", "unknown_z"])
def test_lost_or_changed_workpiece_cannot_emit_positive_output(focus_machine, monkeypatch, change):
    machine, focus, primary = focus_machine
    measure_workpiece(machine, focus)
    events = []
    original = primary.write_line
    monkeypatch.setattr(primary, "write_line", lambda line: (events.append(line), original(line))[1])
    if change == "stop":
        machine.request_stop(_recover=False)
    elif change == "invalidate":
        focus.state.invalidate()
    elif change == "calibration":
        focus.state.calibration = copy.deepcopy(focus.state.calibration)
        focus.state.calibration["focus_offset_mm"] += 1
    elif change == "reference":
        focus.state.drop_z_reference()
    elif change == "firmware":
        focus.serial.overrides["M115"] = ["changed", "ok"]
    else:
        focus.serial.homed = False
    try:
        machine.start_preflighted_program(machine.preflight_program(PROGRAM), authorization_phrase=machine.ARM_PHRASE)
    except MachineError:
        pass
    else:
        wait(machine)
        assert machine._job.error
    assert not any(line.startswith(("M3 ", "M4 ")) for line in events)
    assert focus.state.job_focus_required


def test_calibrated_restart_requires_new_measurement_and_status_is_detached(focus_machine):
    machine, focus, _ = focus_machine
    measure_workpiece(machine, focus)
    status = machine.status()["workpiece_focus"]
    status["job_focus"]["target_z_mm"] = -100
    assert focus.state.job_plan["target_z_mm"] == 28
    restarted = LaserFocus(focus.state.path, "ender")
    assert restarted.job_focus_required and restarted.job_plan is None
    with pytest.raises(SafetyError, match="Measure"):
        restarted.require_job_focus()


def test_air_assist_digest_is_not_parsed_as_gcode_during_focus_or_retention(request, monkeypatch):
    # This real, valid mapping hashes to a digest containing an exponent-like
    # substring; parsing the opaque SHA256 as G-code overflows a numeric word.
    monkeypatch.setattr(secondary_helpers, "_PORT", "COM1")
    machine, focus, _ = request.getfixturevalue("focus_machine")
    plan = measure_workpiece(machine, focus)["job_focus"]
    focus.serial.overrides["M106 S255"] = ["ok"]
    machine.settings.air_assist = AirAssistSettings(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN, port="COM1", baudrate=115200,
    )
    machine.settings.home_and_release_after_powered_job = False
    commands = machine._resolved_air_assist_commands()
    off, on = commands.program_lines(False)[0], commands.program_lines(True)[0]
    assert commands.kind_for_program_line(off) == "off"
    with pytest.raises(ValueError, match="finite"):
        parse_words(off)
    text = f"G21\nG90\nM5\n{off}\nG0X100Y100F1000\n{on}\nM4S100\nG1X105F500\nM5\n{off}\nM5"
    machine.start_preflighted_program(machine.preflight_program(text), authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    assert focus.state.job_plan["id"] == plan["id"]
    assert focus.state.job_plan["xy"] == [105, 100]
