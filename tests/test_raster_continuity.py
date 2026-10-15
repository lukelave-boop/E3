from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.air_assist import AirAssistCommands, AirAssistMode, AirAssistSettings
from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.gcode.job_plan import build_job_plan, restart_program_from_move
from laser_aligner.gcode.preview import parse_gcode_segments
from laser_aligner.machine.service import MachineService
from laser_aligner.project import Bounds, LayerMode, ProjectDocument, SceneObject, generate_project_gcode


@pytest.mark.parametrize("power_mode", ["M3", "M4"])
@pytest.mark.parametrize("overscan", [0, 25])
@pytest.mark.parametrize("correction", [0, -100, 100])
@pytest.mark.parametrize("air", [False, True])
def test_raster_and_restart_preserve_powered_spans_without_mid_row_stops(
    tmp_path: Path, power_mode: str, overscan: int, correction: int, air: bool,
) -> None:
    image = tmp_path / "islands.png"
    assert cv2.imwrite(str(image), np.array([[0, 255, 0, 255]] * 2, dtype=np.uint8))
    doc = ProjectDocument.new("Continuous raster", Bounds(0, 0, 100, 100))
    layer = doc.layers[0]
    layer.mode = LayerMode.RASTER
    layer.speed_mm_min = 600
    layer.line_interval_mm = 1
    layer.overscan_percent = overscan
    layer.raster_power_correction = correction
    layer.passes = 2
    layer.air_assist = air
    doc.add_object(SceneObject(
        name="Islands", kind="image", layer_id=layer.id,
        geometry={"asset": str(image)},
        transform={"x_mm": 50, "y_mm": 50, "width_mm": 4, "height_mm": 2},
    ))
    laser = LaserSettings(power_max=1000, power_mode=power_mode)
    commands = AirAssistCommands(
        mode=AirAssistMode.GRBL_COOLANT, protocol="grbl", fan_index=None,
        on_commands=("M8",), off_commands=("M9",),
    ) if air else None
    job = generate_project_gcode(doc, laser, air_assist_commands=commands)
    machine = MachineService(MachineSettings(
        backend="serial", protocol="grbl", allow_motion=True,
        air_assist=AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT) if air else AirAssistSettings(),
    ), laser, hardware_enabled=True)
    assert machine.preflight_program(job.text).requires_laser_authorization
    assert job.plan.cut_distance_mm == pytest.approx(8)
    # Restart from both lead-in and a white gap, preserving exact suffix geometry/power.
    for index in (0, next(m.index for m in job.plan.moves if not m.rapid and not m.laser_on)):
        text, plan = restart_program_from_move(job.plan, index, power_mode=power_mode)
        assert machine.preflight_program(text).requires_laser_authorization
        expected = job.plan.moves[index:]
        assert [(m.end_x, m.end_y, m.power) for m in plan.moves] == [
            (m.end_x, m.end_y, m.power) for m in expected
        ]
        assert_continuous_rows(text, power_mode)
    assert_continuous_rows(job.text, power_mode)
    assert [s.laser_on for s in parse_gcode_segments(job.text)] == [
        m.laser_on for m in job.plan.moves
    ]


def assert_continuous_rows(text: str, mode: str) -> None:
    row_started = False
    row_ended = False
    last_executable = ""
    for raw in text.splitlines():
        line = raw.partition(";")[0].strip()
        if not line:
            continue
        if line.startswith("G0 "):
            assert not row_started or row_ended
            if row_started:
                assert last_executable == "M5"
            row_started = row_ended = False
        elif line == f"{mode} S0":
            assert not row_started
            row_started = True
        elif line == "M5" and row_started:
            row_ended = True
        elif line.startswith("G1 "):
            assert not row_ended
            assert "F600" in line
        last_executable = line


def test_inline_zero_power_tracks_output_and_preserves_rejection_guards() -> None:
    prefix = "G21\nG90\nM5\nG0 X10 Y10 F600\nM4 S0\n"
    body = "G1 X20 Y10 F600 S100\nG1 X30 Y10 F600 S0\nG1 X40 Y10 F600 S100\n"
    program = prefix + body + "M5\n"
    machine = MachineService(MachineSettings(backend="serial", allow_motion=True), LaserSettings(power_max=1000), hardware_enabled=True)
    assert machine.preflight_program(program).requires_laser_authorization
    assert [m.laser_on for m in build_job_plan(program, power_max=1000).moves] == [False, True, False, True]
    assert [m.laser_on for m in parse_gcode_segments(program)] == [False, True, False, True]
    for invalid in (
        program.replace("M4 S0\n", ""),
        program.replace("S100", "S1001"),
        prefix + body + "G0 X50 Y10 F600\nM5\n",
        prefix + body + "G1 X50 Y10 F600 S0\n",
        program.replace("X40", "X99999"),
    ):
        with pytest.raises(SafetyError):
            machine.preflight_program(invalid)
    # A power word after M5 cannot make preview imply that the mode is enabled.
    disabled = "G21\nG90\nM5\nG1 X10 Y10 F600 S100\n"
    assert not build_job_plan(disabled, power_max=1000).powered
    assert not parse_gcode_segments(disabled)[0].laser_on


def test_start_here_zero_power_raster_never_enables_laser() -> None:
    plan = build_job_plan(
        'G21\nG90\nM5\n; @E3_LAYER {"mode":"raster"}\n'
        'G0 X10 Y10 F600\nG1 X20 Y10 F600\nM5\n', power_max=1000
    )
    text, restarted = restart_program_from_move(plan, 0)
    assert "M3" not in text and "M4" not in text
    assert not restarted.powered
