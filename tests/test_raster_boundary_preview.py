from dataclasses import FrozenInstanceError

import pytest

from laser_aligner.errors import SafetyError
from laser_aligner.gcode.job_plan import build_job_plan, restart_program_from_move
from laser_aligner.gcode.preview import parse_gcode_segments
from laser_aligner.raster_safety import RasterBoundary

BOUNDARY = RasterBoundary(
    engraving_polygon_mm=((0.0, 0.0), (191.0, 0.0), (191.0, 191.0), (0.0, 191.0)),
    travel_bounds_mm=(-10.0, 200.0, -10.0, 200.0),
    travel_envelope_digest="a" * 64,
)


def _program() -> str:
    return "\n".join([
        "G21", "G90", "M5", BOUNDARY.directive,
        '; @E3_LAYER {"id":"raster","name":"Raster","mode":"raster"}',
        "E3OPFOCUS RASTER",
        "; Laser spot offset (spot = controller + offset): X-2 Y3",
        "G0 X-0.106 Y0 F6000",
        "M4 S0",
        "G1 X4.413 Y0 F1200 S0",
        "G1 X185.165 Y0 F1200 S200",
        "G1 X189.684 Y0 F1200 S0",
        "M5",
        "G0 X189.684 Y181.6 F6000",
        "M4 S0",
        "G1 X185.165 Y181.6 F1200 S0",
        "G1 X4.413 Y181.6 F1200 S200",
        "G1 X-0.106 Y181.6 F1200 S0",
        "M5",
    ])


def test_exact_preview_separates_powered_and_all_movement_bounds_with_offset() -> None:
    plan = build_job_plan(_program(), power_max=1000, start_position=(-0.106, 0.0))

    assert plan.raster_boundary == BOUNDARY
    assert plan.powered_bounds_mm == pytest.approx((2.413, 3.0, 183.165, 184.6))
    assert plan.bounds_mm == pytest.approx((-2.106, 3.0, 187.684, 184.6))
    transfer = next(move for move in plan.moves if move.rapid)
    assert transfer.start_x == pytest.approx(187.684)
    assert transfer.end_y == pytest.approx(184.6)
    assert not transfer.laser_on
    raw = parse_gcode_segments(_program())
    assert [(move.end_x, move.end_y, move.power) for move in raw[-7:]] == [
        (move.end_x, move.end_y, move.power) for move in plan.moves[-7:]
    ]
    with pytest.raises(FrozenInstanceError):
        plan.raster_boundary.travel_bounds_mm = (0.0, 1000.0, 0.0, 1000.0)


def test_start_here_preserves_exact_boundary_offset_focus_and_raster_suffix() -> None:
    plan = build_job_plan(_program(), power_max=1000, start_position=(-0.106, 0.0))
    text, restarted = restart_program_from_move(plan, 0, start_position=(50.0, 50.0))

    assert text.count("E3RASTER ") == 1
    assert text.index(BOUNDARY.directive) < text.index("G0 ")
    assert restarted.raster_boundary == BOUNDARY
    assert restarted.moves[0].rapid and not restarted.moves[0].laser_on
    assert [(move.start_x, move.start_y, move.end_x, move.end_y, move.power, move.focus_mode)
            for move in restarted.moves[1:]] == [
        (move.start_x, move.start_y, move.end_x, move.end_y, move.power, move.focus_mode)
        for move in plan.moves
    ]
    assert text.rstrip().endswith("; End of reviewed Start Here job")
    assert text.splitlines()[-2] == "M5"


@pytest.mark.parametrize("index", [1, 2, 5, 6])
def test_raster_start_here_rejects_powered_spans_or_leadout_without_runup(index: int) -> None:
    plan = build_job_plan(_program(), power_max=1000, start_position=(-0.106, 0.0))
    with pytest.raises(SafetyError, match="retain the complete run-up"):
        restart_program_from_move(plan, index)


@pytest.mark.parametrize("index", [0, 3, 4])
def test_raster_start_here_accepts_only_row_approach_or_complete_leadin(index: int) -> None:
    plan = build_job_plan(_program(), power_max=1000, start_position=(-0.106, 0.0))
    _, restarted = restart_program_from_move(plan, index)
    assert restarted.raster_boundary == BOUNDARY
    feed = next(move for move in restarted.moves if not move.rapid)
    assert not feed.laser_on
    assert feed.distance_mm == pytest.approx(4.519)


@pytest.mark.parametrize("directive", ["E3RASTER", "E3RASTER {}", "E3RASTER []"])
def test_preview_rejects_malformed_raster_boundary(directive: str) -> None:
    with pytest.raises(SafetyError):
        build_job_plan(directive + "\nG0 X5 Y5\n", power_max=1000)
    with pytest.raises(SafetyError):
        parse_gcode_segments(directive + "\nG0 X5 Y5\n")


@pytest.mark.parametrize("prefix", [BOUNDARY.directive, "G0 X1 Y1"])
def test_preview_rejects_duplicate_or_late_raster_boundary(prefix: str) -> None:
    program = prefix + "\n" + BOUNDARY.directive
    with pytest.raises(SafetyError, match="once before motion"):
        build_job_plan(program, power_max=1000)
    with pytest.raises(ValueError, match="once before motion"):
        parse_gcode_segments(program)


def test_no_powered_motion_has_no_powered_bounds() -> None:
    plan = build_job_plan("G21\nG90\nM5\nG0 X4 Y8\nM5", power_max=1000)
    assert plan.powered_bounds_mm is None
    assert plan.bounds_mm == (0.0, 0.0, 4.0, 8.0)
