import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.errors import SafetyError, SvgError
from laser_aligner.gcode.generator import DesignPlacement, ToolpathOptions, generate_frame_gcode, generate_vector_gcode
from laser_aligner.gcode.job_plan import build_job_plan
from laser_aligner.gcode.preview import parse_gcode_segments
from laser_aligner.geometry.svg import Polyline, SvgGeometry, parse_svg


def test_vector_gcode_has_safe_laser_sequence() -> None:
    geometry = parse_svg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect x="0" y="0" width="10" height="10"/></svg>')
    program = generate_vector_gcode(
        geometry,
        DesignPlacement(110, 110, 40, 30, rotation_deg=15),
        ToolpathOptions(power=50),
        WorkArea(),
    )
    assert program.text.count("M5") >= 3
    assert "M4 S50" in program.text
    assert program.bounds_mm[0] > 80
    assert program.bounds_mm[2] < 140
    assert program.cut_length_mm > 100


@pytest.mark.parametrize("requested_power", [0])
def test_zero_effective_vector_power_counts_all_motion_as_travel(
    requested_power: float,
) -> None:
    geometry = parse_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect x="0" y="0" width="10" height="10"/></svg>'
    )
    options = ToolpathOptions(power=requested_power)

    program = generate_vector_gcode(
        geometry,
        DesignPlacement(110, 110, 40, 30),
        options,
        WorkArea(),
    )
    plan = build_job_plan(
        program.text,
        power_max=options.power_max,
        default_feed_mm_min=options.travel_feed_mm_min,
    )

    assert "M3 S" not in program.text and "M4 S" not in program.text
    assert not plan.powered
    assert program.cut_length_mm == pytest.approx(0.0)
    assert program.travel_length_mm == pytest.approx(plan.travel_distance_mm)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("travel_feed_mm_min", float("nan")),
        ("travel_feed_mm_min", float("inf")),
        ("travel_feed_mm_min", 0.0),
        ("travel_feed_mm_min", -1.0),
        ("engrave_feed_mm_min", float("nan")),
        ("engrave_feed_mm_min", float("inf")),
        ("engrave_feed_mm_min", 0.0),
        ("engrave_feed_mm_min", -1.0),
    ],
)
def test_vector_generator_rejects_invalid_feed_before_emitting_gcode(
    field: str,
    value: float,
) -> None:
    geometry = parse_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    )
    options = ToolpathOptions(power=10)
    setattr(options, field, value)

    with pytest.raises(ValueError, match=field):
        generate_vector_gcode(
            geometry,
            DesignPlacement(110, 110, 10, 10),
            options,
            WorkArea(),
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0, -1.0])
def test_frame_generator_rejects_invalid_travel_feed(value: float) -> None:
    with pytest.raises(ValueError, match="travel_feed_mm_min"):
        generate_frame_gcode(
            (10, 20, 30, 40),
            ToolpathOptions(power=0, travel_feed_mm_min=value),
            WorkArea(),
        )


@pytest.mark.parametrize("power", [True, 0.9, "10"])
def test_generator_rejects_noninteger_power(power: object) -> None:
    geometry = parse_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    )

    with pytest.raises(ValueError, match="must be integers"):
        generate_vector_gcode(
            geometry,
            DesignPlacement(110, 110, 10, 10),
            ToolpathOptions(power=power),  # type: ignore[arg-type]
            WorkArea(),
        )


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf")])
def test_generator_rejects_nonfinite_path_coordinates(coordinate: float) -> None:
    geometry = SvgGeometry(
        [Polyline(np.array([[coordinate, 0.0], [1.0, 1.0]]))],
        (0.0, 0.0, 1.0, 1.0),
    )

    with pytest.raises(ValueError, match="Path coordinates must be finite"):
        generate_vector_gcode(
            geometry,
            DesignPlacement(110, 110, 10, 10),
            ToolpathOptions(power=10),
            WorkArea(),
        )


@pytest.mark.parametrize(
    "work_area",
    [
        WorkArea(x_min=float("nan")),
        WorkArea(x_max=float("inf")),
        WorkArea(x_min=True),
        WorkArea(x_min=10.0, x_max=10.0),
        WorkArea(x_min=20.0, x_max=10.0),
        WorkArea(y_min=10.0, y_max=10.0),
        WorkArea(y_min=20.0, y_max=10.0),
    ],
)
def test_vector_generator_rejects_invalid_work_area(work_area: WorkArea) -> None:
    geometry = parse_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    )

    with pytest.raises(ValueError, match="work_area"):
        generate_vector_gcode(
            geometry,
            DesignPlacement(110, 110, 10, 10),
            ToolpathOptions(power=10),
            work_area,
        )


def test_frame_generator_rejects_invalid_work_area() -> None:
    with pytest.raises(ValueError, match="work_area"):
        generate_frame_gcode(
            (10, 20, 30, 40),
            ToolpathOptions(power=0),
            WorkArea(y_max=float("inf")),
        )


@pytest.mark.parametrize("field", ["mirror_x", "mirror_y"])
@pytest.mark.parametrize("value", [0, 1, "false"])
def test_generator_rejects_coerced_placement_mirror_flags(
    field: str,
    value: object,
) -> None:
    geometry = parse_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    )
    placement = DesignPlacement(110, 110, 10, 10)
    setattr(placement, field, value)

    with pytest.raises(SvgError, match="mirror flags must be booleans"):
        generate_vector_gcode(
            geometry,
            placement,
            ToolpathOptions(power=10),
            WorkArea(),
        )


def test_out_of_bounds_design_is_blocked() -> None:
    geometry = parse_svg('<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>')
    with pytest.raises(SafetyError):
        generate_vector_gcode(
            geometry,
            DesignPlacement(215, 110, 30, 30),
            ToolpathOptions(power=10),
            WorkArea(),
        )


def test_dry_frame_uses_zero_power() -> None:
    options = ToolpathOptions(power=99)
    program = generate_frame_gcode(
        (10, 20, 30, 40),
        options,
        WorkArea(),
        laser_enabled=False,
    )
    plan = build_job_plan(
        program.text,
        power_max=options.power_max,
        default_feed_mm_min=options.travel_feed_mm_min,
    )
    assert "M4" not in program.text
    assert "M3" not in program.text
    assert "S99" not in program.text
    assert program.text.rstrip().endswith("M5")
    assert program.cut_length_mm == pytest.approx(0.0)
    assert program.travel_length_mm == pytest.approx(plan.travel_distance_mm)


def test_dry_frame_applies_spot_offset_and_preview_recovers_spot_path() -> None:
    program = generate_frame_gcode(
        (10, 20, 30, 40),
        ToolpathOptions(
            power=0,
            spot_offset_x_mm=-28,
            spot_offset_y_mm=-8,
        ),
        WorkArea(),
        laser_enabled=False,
    )

    assert "spot = controller + offset): X-28 Y-8" in program.text
    assert "G0 X38 Y28" in program.text
    segments = parse_gcode_segments(program.text)
    assert segments[0].end_x == pytest.approx(10.0)
    assert segments[0].end_y == pytest.approx(20.0)
    assert segments[-1].end_x == pytest.approx(10.0)
    assert segments[-1].end_y == pytest.approx(20.0)


def test_segment_preview_uses_recorded_job_start_and_spot_offset() -> None:
    segments = parse_gcode_segments(
        "; Laser spot offset (spot = controller + offset): X-2 Y3\n"
        '; @E3_JOB {"start_x":50,"start_y":40}\n'
        "G21\nG90\nM5\nG0 X55 Y60 F1000\nM5\n"
    )

    assert len(segments) == 1
    assert segments[0].start_x == pytest.approx(48.0)
    assert segments[0].start_y == pytest.approx(43.0)
    assert segments[0].end_x == pytest.approx(53.0)
    assert segments[0].end_y == pytest.approx(63.0)


def test_generated_browser_vector_and_frame_record_exact_parked_start_pose() -> None:
    geometry = parse_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    )
    options = ToolpathOptions(
        power=0,
        optimize_order=False,
        spot_offset_x_mm=-2.0,
        spot_offset_y_mm=3.0,
        start_x_mm=50.0,
        start_y_mm=40.0,
    )
    vector = generate_vector_gcode(
        geometry,
        DesignPlacement(80.0, 70.0, 10.0, 10.0),
        options,
        WorkArea(),
    )
    frame = generate_frame_gcode(
        (75.0, 65.0, 85.0, 75.0),
        options,
        WorkArea(),
        laser_enabled=False,
    )

    for program in (vector, frame):
        segments = parse_gcode_segments(program.text)
        plan = build_job_plan(
            program.text,
            power_max=options.power_max,
            default_feed_mm_min=options.travel_feed_mm_min,
        )
        assert segments[0].start_x == pytest.approx(48.0)
        assert segments[0].start_y == pytest.approx(43.0)
        assert program.travel_length_mm == pytest.approx(plan.travel_distance_mm)


def test_vector_offset_rejects_controller_motion_outside_work_area() -> None:
    geometry = parse_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
    )
    with pytest.raises(SafetyError):
        generate_vector_gcode(
            geometry,
            DesignPlacement(190, 110, 20, 20),
            ToolpathOptions(
                power=10,
                spot_offset_x_mm=-28,
                spot_offset_y_mm=-8,
            ),
            WorkArea(),
        )


def test_large_browser_vector_job_retains_source_order_without_quadratic_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [
        Polyline(
            np.array([[float(index), 0.0], [float(index), 1.0]]),
            source_tag=f"line-{index}",
        )
        for index in range(513)
    ]
    geometry = SvgGeometry(paths, (0.0, 0.0, 512.0, 1.0))

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("quadratic nearest-path planner must be skipped")

    monkeypatch.setattr(
        "laser_aligner.gcode.generator._nearest_order",
        fail_if_called,
    )
    program = generate_vector_gcode(
        geometry,
        DesignPlacement(110.0, 110.0, 100.0, 1.0),
        ToolpathOptions(power=10),
        WorkArea(),
    )

    assert program.path_count == 513
    assert program.warnings == [
        "Nearest-path optimization was skipped for 513 paths to keep planning "
        "responsive; source order was retained."
    ]
    assert program.text.index("Path 1: line-0") < program.text.index("Path 2: line-1")


def test_browser_vector_command_budget_rejects_before_geometry_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = SvgGeometry(
        [Polyline(np.zeros((249_999, 2), dtype=np.float64))],
        (0.0, 0.0, 1.0, 1.0),
    )

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("geometry expansion must not run past the command budget")

    monkeypatch.setattr(
        "laser_aligner.gcode.generator.place_geometry",
        fail_if_called,
    )
    with pytest.raises(ValueError, match="250,005 streamed commands"):
        generate_vector_gcode(
            geometry,
            DesignPlacement(110.0, 110.0, 1.0, 1.0),
            ToolpathOptions(power=10),
            WorkArea(),
        )
