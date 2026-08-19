from dataclasses import FrozenInstanceError

import pytest

import laser_aligner.gcode.job_plan as job_plan_module
from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.gcode.job_plan import (
    build_job_plan,
    e3_metadata_line,
    restart_program_from_move,
)
from laser_aligner.machine.service import MachineService


def test_job_plan_preserves_exact_move_context_and_timing() -> None:
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            e3_metadata_line("job", {"planner": "nearest path"}),
            e3_metadata_line(
                "planner",
                {"source_order_travel_mm": 12.5, "savings_mm": 2.5},
            ),
            e3_metadata_line(
                "layer",
                {"id": "line-01", "name": "Line 01", "color": "#185CFF"},
            ),
            e3_metadata_line("pass", {"index": 2, "count": 3}),
            e3_metadata_line("path", {"name": "Label R2 C4"}),
            "G0 X10 Y20 F600",
            "M4 S200",
            "G1 X40 Y20 F1200",
            "M5",
        ]
    )

    plan = build_job_plan(
        text,
        power_max=1000,
        start_position=(5.0, 20.0),
    )

    assert len(plan.moves) == 2
    rapid, cut = plan.moves
    assert rapid.rapid and not rapid.laser_on
    assert rapid.distance_mm == pytest.approx(5.0)
    assert rapid.duration_seconds == pytest.approx(0.5)
    assert cut.laser_on and not cut.rapid
    assert cut.power == pytest.approx(200.0)
    assert cut.feed_mm_min == pytest.approx(1200.0)
    assert cut.duration_seconds == pytest.approx(1.5)
    assert cut.layer_id == "line-01"
    assert cut.layer_name == "Line 01"
    assert cut.layer_color == "#185CFF"
    assert (cut.pass_index, cut.pass_count) == (2, 3)
    assert cut.source_name == "Label R2 C4"
    assert plan.total_seconds == pytest.approx(2.0)
    assert plan.maximum_power == pytest.approx(200.0)
    assert plan.powered
    assert plan.planner_mode == "nearest path"
    assert plan.source_order_travel_mm == pytest.approx(12.5)
    assert plan.planner_savings_mm == pytest.approx(2.5)
    with pytest.raises(FrozenInstanceError):
        cut.power = 500.0  # type: ignore[misc]


def test_job_plan_warns_about_but_does_not_hide_powered_rapid() -> None:
    plan = build_job_plan(
        "G90\nM4 S100\nG0 X10 Y10 F1000\nM5\n",
        power_max=1000,
    )

    assert plan.warnings == (
        "Line 3: rapid motion requested while laser is on",
    )
    assert len(plan.moves) == 1
    assert plan.moves[0].rapid
    assert not plan.moves[0].laser_on


def test_job_plan_applies_spot_offset_to_physical_preview_coordinates() -> None:
    plan = build_job_plan(
        "; Laser spot offset (spot = controller + offset): X-28 Y-8\n"
        "G90\nM5\nG0 X38 Y28 F1000\n",
        power_max=1000,
        start_position=(10.0, 20.0),
    )

    move = plan.moves[0]
    assert (move.end_x, move.end_y) == pytest.approx((10.0, 20.0))


def test_job_plan_timing_can_model_acceleration_and_command_latency() -> None:
    constant = build_job_plan(
        "G90\nG0 X10 Y0 F6000\n",
        power_max=1000,
    )
    modeled = build_job_plan(
        "G90\nG0 X10 Y0 F6000\n",
        power_max=1000,
        acceleration_mm_s2=500.0,
        command_delay_ms=25.0,
    )

    assert constant.total_seconds == pytest.approx(0.1)
    assert modeled.total_seconds > constant.total_seconds
    assert modeled.total_seconds == pytest.approx(2 * (10 / 500) ** 0.5 + 0.025)


def test_start_here_rebuilds_guarded_program_at_move_boundary() -> None:
    original = build_job_plan(
        "; Laser spot offset (spot = controller + offset): X-2 Y-3\n"
        "G21\nG90\nM5\nG0 X12 Y13 F1000\nM4 S200\n"
        "G1 X22 Y13 F600\nG1 X22 Y23 F600\nM5\n",
        power_max=1000,
    )

    text, restarted = restart_program_from_move(original, 2)

    assert text.index("M5 ; laser off before positioning") < text.index("G0 X22.000 Y13.000")
    assert "M4 S200" in text
    assert restarted.powered
    assert restarted.moves[0].start_x == pytest.approx(20.0)
    assert restarted.moves[-1].end_y == pytest.approx(20.0)


def test_start_here_records_park_pose_and_previews_exact_spot_approach() -> None:
    original = build_job_plan(
        "; Laser spot offset (spot = controller + offset): X-2 Y-3\n"
        "G21\nG90\nM5\nG0 X12 Y13 F1000\nM4 S200\n"
        "G1 X22 Y13 F600\nG1 X22 Y23 F600\nM5\n",
        power_max=1000,
    )

    text, restarted = restart_program_from_move(
        original,
        2,
        start_position=(50.0, 40.0),
    )

    assert '; @E3_JOB {"start_x":50.0,"start_y":40.0}' in text
    approach, selected = restarted.moves[:2]
    assert approach.rapid and not approach.laser_on
    assert (approach.start_x, approach.start_y) == pytest.approx((48.0, 37.0))
    assert (approach.end_x, approach.end_y) == pytest.approx((20.0, 10.0))
    assert selected.laser_on
    assert (selected.start_x, selected.start_y) == pytest.approx((20.0, 10.0))
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(spot_offset_x_mm=-2.0, spot_offset_y_mm=-3.0),
        hardware_enabled=False,
    )
    preflight = machine.preflight_program(text)
    assert preflight.requires_motion
    assert preflight.requires_laser_authorization


def test_start_here_rejects_nonfinite_controller_start_pose() -> None:
    plan = build_job_plan("G90\nG0 X1 Y1\n", power_max=1000)

    with pytest.raises(ValueError, match="must be finite"):
        restart_program_from_move(plan, 0, start_position=(float("nan"), 0.0))


def test_start_here_rejects_unknown_move() -> None:
    plan = build_job_plan("G90\nG0 X1 Y1\n", power_max=1000)
    with pytest.raises(ValueError, match="outside"):
        restart_program_from_move(plan, 5)

def test_build_job_plan_parses_each_executable_line_once(monkeypatch) -> None:
    text = "\n".join(
        [
            "; comment-only line",
            "G21",
            "G90",
            "M5",
            '; @E3_LAYER {"id":"line-01","name":"Line 01"}',
            "G0 X10 Y0 F600",
            "M4 S200",
            "G1X20Y0F1200",
            "M5",
            "",
        ]
    )
    original_parse_words = job_plan_module.parse_words
    parsed_lines: list[str] = []

    def counted_parse_words(line: str):
        parsed_lines.append(line)
        return original_parse_words(line)

    monkeypatch.setattr(job_plan_module, "parse_words", counted_parse_words)

    plan = job_plan_module.build_job_plan(
        text,
        power_max=1000,
        start_position=(0.0, 0.0),
    )

    assert len(plan.moves) == 2
    assert parsed_lines == [
        "G21",
        "G90",
        "M5",
        "G0 X10 Y0 F600",
        "M4 S200",
        "G1X20Y0F1200",
        "M5",
    ]
