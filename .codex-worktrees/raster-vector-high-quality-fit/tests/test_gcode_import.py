from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from laser_aligner.project import (
    GcodeImportError,
    load_gcode_project,
    object_polylines,
    parse_gcode_project,
)


def _bounds(item) -> tuple[float, float, float, float]:
    points = np.vstack([line.points for line in object_polylines(item)])
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return minimum[0], minimum[1], maximum[0], maximum[1]


def test_imports_modal_grbl_vectors_as_output_disabled_speed_power_layer() -> None:
    result = parse_gcode_project(
        """
        ; S-value max: 1000
        G21 G90
        M4
        G0 X0 Y0
        G1 F1200 S500 X20 Y0
        Y10
        X0
        Y0
        M5
        """,
        source_name="panel.gc",
        center=(100.0, 80.0),
    )

    assert len(result.layers) == 1
    layer = result.layers[0]
    assert layer.output_enabled is False
    assert layer.speed_mm_min == pytest.approx(1200.0)
    assert layer.power_percent == pytest.approx(50.0)
    assert result.power_scale == pytest.approx(1000.0)
    assert result.powered_move_count == 4
    assert len(result.objects) == 1
    assert _bounds(result.objects[0]) == pytest.approx((90.0, 75.0, 110.0, 85.0))
    assert result.objects[0].metadata["gcode_import_review_required"] is True
    assert result.objects[0].metadata["gcode_power_s"] == pytest.approx(500.0)


def test_reconstructs_distinct_layers_from_speed_and_power() -> None:
    result = parse_gcode_project(
        """
        ; S-value max: 1000
        G21 G90 M4
        G1 F1000 S250 X10 Y0
        G0 X20 Y0
        G1 F2000 S750 X30 Y0
        M5
        """
    )

    assert len(result.layers) == 2
    assert result.layers[0].speed_mm_min == pytest.approx(1000.0)
    assert result.layers[0].power_percent == pytest.approx(25.0)
    assert result.layers[1].speed_mm_min == pytest.approx(2000.0)
    assert result.layers[1].power_percent == pytest.approx(75.0)
    assert all(layer.output_enabled is False for layer in result.layers)
    assert len(result.objects) == 2
    assert result.travel_move_count == 1


def test_inch_and_relative_modes_convert_to_mm() -> None:
    result = parse_gcode_project(
        """
        G20 G91 M3 S255
        G1 F10 X1 Y0
        X0 Y1
        M5
        """
    )

    assert result.layers[0].speed_mm_min == pytest.approx(254.0)
    assert _bounds(result.objects[0]) == pytest.approx((-12.7, -12.7, 12.7, 12.7))
    assert any("inferred an S scale of 255" in warning for warning in result.warnings)


def test_samples_ij_and_radius_arcs() -> None:
    ij = parse_gcode_project(
        """
        G21 G90 M4 S100
        G1 F600 X10 Y0
        G3 X0 Y10 I-10 J0
        M5
        """
    )
    ij_points = object_polylines(ij.objects[0])[0].points
    assert len(ij_points) > 10
    assert float(ij_points[:, 0].max()) == pytest.approx(5.0)
    assert float(ij_points[:, 1].max()) == pytest.approx(5.0)

    radius = parse_gcode_project(
        """
        G21 G90 M4 S1000
        G2 F1000 X10 Y0 R5
        M5
        """
    )
    radius_points = object_polylines(radius.objects[0])[0].points
    assert len(radius_points) > 10


def test_g0_is_never_imported_as_powered_geometry_even_with_m4_and_s() -> None:
    result = parse_gcode_project(
        """
        G21 G90 M4 S1000
        G0 X50 Y50
        G1 F1000 X60 Y50
        M5
        """
    )
    assert result.travel_move_count == 1
    assert _bounds(result.objects[0]) == pytest.approx((-5.0, 0.0, 5.0, 0.0), abs=0.001)


@pytest.mark.parametrize(
    ("program", "message"),
    [
        ("G21 G90\nG92 X0\n", "G92"),
        ("G21 G90 M4 S100\nG1 F1000 X10 Z2\n", "Z-axis"),
        ("G21 G90 M4 S100\nG1 X10\n", "no positive modal F"),
        ("G21 G90\nG1 F1000 X10 Y0\n", "no powered"),
        ("G21 G90 M4 S100\nG2 F1000 X10 Y0\n", "requires I/J"),
        ("G21 G90 M4 S100\nG18 G2 F1000 X10 Y0 I5\n", "XY-plane"),
    ],
)
def test_rejects_geometry_or_state_that_cannot_be_translated_safely(
    program: str,
    message: str,
) -> None:
    with pytest.raises(GcodeImportError, match=message):
        parse_gcode_project(program)


def test_load_accepts_gc_and_common_gcode_extensions_and_enforces_size(tmp_path: Path) -> None:
    payload = "G21 G90 M4 S1000\nG1 F1000 X10\nM5\n"
    for suffix in (".gc", ".GC", ".gcode", ".nc", ".tap"):
        path = tmp_path / f"sample{suffix}"
        path.write_text(payload, encoding="utf-8")
        assert len(load_gcode_project(path).objects) == 1

    path = tmp_path / "too-large.gc"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(GcodeImportError, match="import limit"):
        load_gcode_project(path, max_file_bytes=8)


def test_wrong_extension_and_conflicting_or_exceeded_s_scale_are_rejected(tmp_path: Path) -> None:
    wrong = tmp_path / "sample.txt"
    wrong.write_text("G1 X1", encoding="utf-8")
    with pytest.raises(GcodeImportError, match=".gc"):
        load_gcode_project(wrong)

    with pytest.raises(GcodeImportError, match="conflicting"):
        parse_gcode_project(
            "; S-value max: 1000\n; $30=255\nG21 G90 M4 S100\nG1 F1000 X1\n"
        )
    with pytest.raises(GcodeImportError, match="exceeds"):
        parse_gcode_project(
            "; S-value max: 255\nG21 G90 M4 S500\nG1 F1000 X1\n"
        )


def test_accepts_g40_but_rejects_active_cutter_compensation() -> None:
    result = parse_gcode_project(
        "G21 G90 G40 M4 S100\n"
        "G1 F600 X10 Y0\n"
        "M5\n"
    )
    assert len(result.objects) == 1

    for code in (41, 42):
        with pytest.raises(GcodeImportError, match=rf"G{code}"):
            parse_gcode_project(
                f"G21 G90 G{code} M4 S100\n"
                "G1 F600 X10 Y0\n"
                "M5\n"
            )
