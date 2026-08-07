import pytest

from laser_aligner.config import WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.gcode.generator import DesignPlacement, ToolpathOptions, generate_frame_gcode, generate_vector_gcode
from laser_aligner.gcode.preview import parse_gcode_segments
from laser_aligner.geometry.svg import parse_svg


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
    program = generate_frame_gcode((10, 20, 30, 40), ToolpathOptions(power=99), WorkArea(), laser_enabled=False)
    assert "M4" not in program.text
    assert "M3" not in program.text
    assert "S99" not in program.text
    assert program.text.rstrip().endswith("M5")


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
