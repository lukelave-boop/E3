from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from laser_aligner.project import (
    LayerMode,
    LightBurnImportError,
    load_lightburn_project,
    object_polylines,
    parse_lightburn_project,
)


def _bounds(item) -> tuple[float, float, float, float]:
    points = np.vstack([line.points for line in object_polylines(item)])
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return minimum[0], minimum[1], maximum[0], maximum[1]


def test_imports_ellipse_with_conservative_fallback_layer() -> None:
    result = parse_lightburn_project(
        """<?xml version="1.0"?>
        <LightBurnProject AppVersion="1.7.08" FormatVersion="1">
          <Shape Type="Ellipse" CutIndex="0" Rx="5" Ry="3">
            <XForm>1 0 0 1 55 42</XForm>
          </Shape>
        </LightBurnProject>
        """,
        source_name="circle.lbrn2",
        center=(100.0, 80.0),
    )

    assert result.app_version == "1.7.08"
    assert result.format_version == "1"
    assert len(result.layers) == 1
    assert result.layers[0].mode is LayerMode.LINE
    assert result.layers[0].output_enabled is False
    assert result.layers[0].power_percent == pytest.approx(10.0)
    assert len(result.objects) == 1
    assert _bounds(result.objects[0]) == pytest.approx((95.0, 77.0, 105.0, 83.0), abs=0.01)
    assert result.objects[0].metadata["lightburn_settings_review_required"] is True
    assert result.objects[0].metadata["lightburn_source"] == "circle.lbrn2"
    assert any("conservative defaults" in warning for warning in result.warnings)


def test_imports_cut_setting_values_and_disables_output() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject AppVersion="1.6.00" FormatVersion="1">
          <CutSetting type="Scan">
            <index Value="3"/>
            <name Value="Photo engraving"/>
            <speed Value="25"/>
            <maxPower Value="72.5"/>
            <passCount Value="2"/>
            <interval Value="0.08"/>
            <scanAngle Value="45"/>
            <overscan Value="4.5"/>
            <airAssist Value="1"/>
            <doOutput Value="1"/>
          </CutSetting>
          <Shape Type="Rect" CutIndex="3" W="20" H="10" Cr="2">
            <XForm>1 0 0 1 10 20</XForm>
          </Shape>
        </LightBurnProject>
        """,
    )

    layer = result.layers[0]
    assert layer.name == "LightBurn · Photo engraving"
    assert layer.mode is LayerMode.RASTER
    assert layer.speed_mm_min == pytest.approx(1500.0)
    assert layer.power_percent == pytest.approx(72.5)
    assert layer.passes == 2
    assert layer.line_interval_mm == pytest.approx(0.08)
    assert layer.scan_angle_deg == pytest.approx(45.0)
    assert layer.overscan_percent == pytest.approx(4.5)
    assert layer.air_assist is True
    assert layer.output_enabled is False
    assert len(object_polylines(result.objects[0])[0].points) > 20


def test_fill_and_outline_reports_review_warning() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <CutSetting type="Cut">
            <index Value="0"/><fill Value="1"/><line Value="1"/>
          </CutSetting>
          <Shape Type="Rect" CutIndex="0" W="4" H="2"><XForm>1 0 0 1 0 0</XForm></Shape>
        </LightBurnProject>
        """
    )

    assert result.layers[0].mode is LayerMode.FILL
    assert any("both fill and outline" in warning for warning in result.warnings)


def test_group_transform_and_project_recentering_preserve_relative_placement() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Group" CutIndex="0">
            <XForm>1 0 0 1 20 30</XForm>
            <Children>
              <Shape Type="Rect" W="10" H="4"><XForm>1 0 0 1 -10 0</XForm></Shape>
              <Shape Type="Ellipse" Rx="2" Ry="2"><XForm>1 0 0 1 10 0</XForm></Shape>
            </Children>
          </Shape>
        </LightBurnProject>
        """,
        center=(50.0, 60.0),
    )

    assert len(result.objects) == 2
    assert result.objects[0].group_id is not None
    assert result.objects[0].group_id == result.objects[1].group_id
    left_center = result.objects[0].transform.x_mm
    right_center = result.objects[1].transform.x_mm
    assert right_center - left_center == pytest.approx(20.0, abs=0.01)
    combined = np.vstack(
        [line.points for item in result.objects for line in object_polylines(item)]
    )
    assert (combined[:, 0].min() + combined[:, 0].max()) / 2.0 == pytest.approx(50.0)
    assert (combined[:, 1].min() + combined[:, 1].max()) / 2.0 == pytest.approx(60.0)


def test_lineclosed_path_imports_as_closed_polyline() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Path" CutIndex="0">
            <XForm>1 0 0 1 5 7</XForm>
            <VertList>V0 0V10 0V10 5V0 5</VertList>
            <PrimList>LineClosed</PrimList>
          </Shape>
        </LightBurnProject>
        """
    )

    lines = object_polylines(result.objects[0])
    assert len(lines) == 1
    assert lines[0].closed is True
    assert _bounds(result.objects[0]) == pytest.approx((-5.0, -2.5, 5.0, 2.5), abs=0.01)


def test_explicit_line_and_bezier_path_are_sampled() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Path" CutIndex="0">
            <XForm>1 0 0 1 0 0</XForm>
            <VertList>V0 0c0x5c0y10V10 0c1x5c1y10V20 0</VertList>
            <PrimList>B0 1 L1 2</PrimList>
          </Shape>
        </LightBurnProject>
        """
    )

    line = object_polylines(result.objects[0])[0]
    assert line.closed is False
    assert len(line.points) > 10
    assert float(line.points[:, 1].max()) > 3.5
    assert _bounds(result.objects[0]) == pytest.approx((-10.0, -3.75, 10.0, 3.75), abs=0.15)


def test_missing_bezier_controls_fall_back_to_line_with_warning() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Path" CutIndex="0">
            <XForm>1 0 0 1 0 0</XForm>
            <VertList>V10 10V90 90c0x70.7c0y0</VertList>
            <PrimList>B0 1</PrimList>
          </Shape>
        </LightBurnProject>
        """
    )

    assert len(object_polylines(result.objects[0])[0].points) == 2
    assert any("missing Bezier controls" in warning for warning in result.warnings)


def test_reuses_vertex_and_primitive_lists_by_id() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Path" CutIndex="0" VertID="12" PrimID="8">
            <XForm>1 0 0 1 0 0</XForm>
            <VertList>V0 0V4 0V4 2V0 2</VertList>
            <PrimList>LineClosed</PrimList>
          </Shape>
          <Shape Type="Path" CutIndex="0" VertID="12" PrimID="8">
            <XForm>1 0 0 1 10 0</XForm>
          </Shape>
        </LightBurnProject>
        """
    )

    assert len(result.objects) == 2
    assert result.objects[1].transform.x_mm - result.objects[0].transform.x_mm == pytest.approx(10.0)


def test_text_backup_path_is_imported_as_native_vector() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Text" CutIndex="2" HasBackupPath="1">
            <XForm>1 0 0 1 100 100</XForm>
            <BackupPath>
              <Shape Type="Path">
                <XForm>1 0 0 1 30 40</XForm>
                <VertList>V0 0V8 0V4 10</VertList>
                <PrimList>LineClosed</PrimList>
              </Shape>
            </BackupPath>
          </Shape>
        </LightBurnProject>
        """,
        source_name="label.lbrn2",
    )

    assert len(result.objects) == 1
    assert result.objects[0].metadata["lightburn_shape_type"] == "Text (vector backup)"
    assert result.objects[0].metadata["lightburn_cut_index"] == 2


@pytest.mark.parametrize(
    ("fragment", "message"),
    [
        ('<Shape Type="Text" CutIndex="0"/>', "no vector BackupPath"),
        ('<Shape Type="Bitmap" CutIndex="0" W="10" H="10"><Data>AA==</Data></Shape>', "vector-only"),
        ('<Shape Type="QRCode" CutIndex="0"/>', "unsupported shape type"),
    ],
)
def test_rejects_content_that_would_be_silently_lost(fragment: str, message: str) -> None:
    with pytest.raises(LightBurnImportError, match=message):
        parse_lightburn_project(f"<LightBurnProject>{fragment}</LightBurnProject>")


@pytest.mark.parametrize(
    "payload",
    [
        '<!DOCTYPE x [<!ENTITY a "boom">]><LightBurnProject>&a;</LightBurnProject>',
        "<notLightBurn/>",
        "<LightBurnProject><Shape",
    ],
)
def test_rejects_unsafe_or_invalid_xml(payload: str) -> None:
    with pytest.raises(LightBurnImportError):
        parse_lightburn_project(payload)


def test_rejects_invalid_primitive_indices() -> None:
    with pytest.raises(LightBurnImportError, match="outside the VertList"):
        parse_lightburn_project(
            """
            <LightBurnProject>
              <Shape Type="Path" CutIndex="0">
                <VertList>V0 0V1 1</VertList><PrimList>L0 9</PrimList>
              </Shape>
            </LightBurnProject>
            """
        )


def test_load_accepts_both_extensions_and_enforces_size_limit(tmp_path: Path) -> None:
    payload = (
        '<LightBurnProject><Shape Type="Ellipse" CutIndex="0" Rx="1" Ry="1"/>'
        "</LightBurnProject>"
    )
    for extension in (".lbrn", ".lbrn2", ".LBRN", ".LBRN2"):
        path = tmp_path / f"sample{extension}"
        path.write_text(payload, encoding="utf-8")
        assert len(load_lightburn_project(path).objects) == 1

    too_large = tmp_path / "large.lbrn2"
    too_large.write_text(payload, encoding="utf-8")
    with pytest.raises(LightBurnImportError, match="import limit"):
        load_lightburn_project(too_large, max_file_bytes=8)


def test_load_rejects_wrong_extension(tmp_path: Path) -> None:
    path = tmp_path / "sample.xml"
    path.write_text("<LightBurnProject/>", encoding="utf-8")
    with pytest.raises(LightBurnImportError, match=".lbrn"):
        load_lightburn_project(path)


def test_imports_real_world_compact_primitive_syntax_and_shared_prim_id() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <CutSetting type="Scan"><index Value="8"/><priority Value="2"/><speed Value="800"/></CutSetting>
          <CutSetting type="Scan"><index Value="3"/><priority Value="1"/><speed Value="700"/></CutSetting>
          <Shape Type="Path" CutIndex="8" VertID="182800" PrimID="0">
            <XForm>0.11374768 0 0 0.11374768 98.250885 125.8541</XForm>
            <VertList>V50.293007 -2.093605c0x36.642982c0y-6.7903824c1x61.960434c1y1.92099V34.517433 -18.113327c0x1c1x32.879803c1y-15.281212V72.952171 -71.142242c0x1c1x1V72.952171 -5.6299133c0x1c1x1V72.881287 -5.6535416c0x72.80883c0y-1.5877991c1x1</VertList>
            <PrimList>B0 1L1 2L2 3L3 4B4 0</PrimList>
          </Shape>
          <Shape Type="Path" CutIndex="3" VertID="72250" PrimID="0">
            <XForm>0.11374768 0 0 0.11374768 98.250885 125.8541</XForm>
            <VertList>V14.034615 -28.59903c0x5.7522087c0y-40.422081c1x21.114044c1y-18.49321V10.688068 -50.831909c0x1c1x7.6985283c1y-49.50325V72.952042 -71.14183c0x1c1x1V34.444862 -18.141235c0x1c1x1V34.401405 -18.202026c0x31.952999c0y-14.955353c1x1</VertList>
          </Shape>
        </LightBurnProject>
        """
    )

    assert len(result.objects) == 2
    assert len(result.layers) == 2
    assert result.layers[0].speed_mm_min == pytest.approx(42_000.0)
    assert result.layers[1].speed_mm_min == pytest.approx(48_000.0)
    assert all(len(object_polylines(item)[0].points) > 5 for item in result.objects)


def test_lineopen_path_stays_open() -> None:
    result = parse_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Path" CutIndex="0">
            <VertList>V0 0V2 0V2 1</VertList><PrimList>LineOpen</PrimList>
          </Shape>
        </LightBurnProject>
        """
    )
    assert object_polylines(result.objects[0])[0].closed is False
