from pathlib import Path

import pytest

import laser_aligner.project.lightburn as lightburn_module
from laser_aligner.project import (
    ImportCapability,
    LightBurnImportError,
    load_lightburn_project,
    scan_lightburn_file,
    scan_lightburn_project,
)


def test_lightburn_scan_reports_versions_layers_and_review_facts() -> None:
    manifest = scan_lightburn_project(
        """
        <LightBurnProject AppVersion="1.7.08" FormatVersion="1">
          <CutSetting type="Scan">
            <index Value="3"/>
            <name Value="Photo engraving"/>
            <priority Value="1"/>
          </CutSetting>
          <CutSetting type="Cut">
            <index Value="8"/>
            <name Value="Outline"/>
            <priority Value="2"/>
          </CutSetting>
          <Shape Type="Ellipse" CutIndex="8" Rx="5" Ry="3"/>
          <Shape Type="Rect" CutIndex="3" W="20" H="10" Cr="2"/>
        </LightBurnProject>
        """,
        source_name="fixture.LBRN2",
    )

    assert manifest.ready_for_parse
    assert manifest.source_suffix == ".lbrn2"
    assert manifest.format_version == "1"
    assert ImportCapability.SOURCE_LAYERS in manifest.capabilities
    assert [
        (layer.source_key, layer.name, layer.mode_hint, layer.object_count)
        for layer in manifest.layers
    ] == [
        ("cut:3", "Photo engraving", "raster", 1),
        ("cut:8", "Outline", "line", 1),
    ]
    assert any("output-disabled" in warning for warning in manifest.warnings)
    assert any("ellipses/circles" in item for item in manifest.approximations)
    assert any(
        "Rounded LightBurn rectangles" in item
        for item in manifest.approximations
    )


@pytest.mark.parametrize(
    ("fragment", "message"),
    [
        ('<Shape Type="Text" CutIndex="0"/>', "no vector BackupPath"),
        (
            '<Shape Type="Bitmap" CutIndex="0" W="10" H="10"><Data>AA==</Data></Shape>',
            "vector-only",
        ),
        ('<Shape Type="QRCode" CutIndex="0"/>', "unsupported shape type"),
    ],
)
def test_lightburn_scan_blocks_known_unsupported_content(
    fragment: str,
    message: str,
) -> None:
    manifest = scan_lightburn_project(
        f"<LightBurnProject>{fragment}</LightBurnProject>",
        source_name="blocked.lbrn2",
    )

    assert not manifest.ready_for_parse
    assert any(message in item for item in manifest.unsupported_features)


def test_lightburn_scan_does_not_vectorize_shapes(monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("strict vector parser must not run during scan")

    monkeypatch.setattr(lightburn_module, "_parse_shape", fail_if_called)

    manifest = lightburn_module.scan_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Ellipse" CutIndex="0" Rx="5" Ry="3"/>
          <Shape Type="Path" CutIndex="0">
            <VertList>V0 0V10 0V10 5</VertList>
            <PrimList>B0 1L1 2</PrimList>
          </Shape>
        </LightBurnProject>
        """,
        source_name="scan-only.lbrn2",
    )

    assert manifest.ready_for_parse
    assert manifest.layers[0].object_count == 2
    assert any("Bezier" in item for item in manifest.approximations)


def test_lightburn_scan_allows_shared_path_list_ids() -> None:
    manifest = scan_lightburn_project(
        """
        <LightBurnProject>
          <Shape Type="Path" CutIndex="0" VertID="12" PrimID="8">
            <VertList>V0 0V4 0V4 2V0 2</VertList>
            <PrimList>LineClosed</PrimList>
          </Shape>
          <Shape Type="Path" CutIndex="0" VertID="12" PrimID="8"/>
        </LightBurnProject>
        """,
        source_name="shared.lbrn2",
    )

    assert manifest.ready_for_parse
    assert manifest.layers[0].object_count == 2


def test_lightburn_loader_blocks_manifest_before_strict_parse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "bitmap.lbrn2"
    path.write_text(
        '<LightBurnProject><Shape Type="Bitmap" CutIndex="0"/></LightBurnProject>',
        encoding="utf-8",
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("strict parser must not run after a blocking scan")

    monkeypatch.setattr(
        lightburn_module,
        "parse_lightburn_project",
        fail_if_called,
    )

    with pytest.raises(LightBurnImportError, match="vector-only"):
        lightburn_module.load_lightburn_project(path)


def test_scan_lightburn_file_reports_wrong_extension_and_size_limit(
    tmp_path: Path,
) -> None:
    wrong = tmp_path / "sample.xml"
    wrong.write_text("<LightBurnProject/>", encoding="utf-8")
    wrong_manifest = scan_lightburn_file(wrong)
    assert not wrong_manifest.ready_for_parse
    assert any(".lbrn" in error for error in wrong_manifest.errors)

    large = tmp_path / "large.lbrn2"
    large.write_text("<LightBurnProject/>", encoding="utf-8")
    large_manifest = scan_lightburn_file(large, max_file_bytes=8)
    assert not large_manifest.ready_for_parse
    assert any("import limit" in error for error in large_manifest.errors)


def test_lightburn_scan_returns_errors_for_malformed_xml() -> None:
    manifest = scan_lightburn_project(
        "<LightBurnProject><Shape",
        source_name="broken.lbrn2",
    )

    assert not manifest.ready_for_parse
    assert manifest.errors
    assert not manifest.unsupported_features


def test_existing_lightburn_loader_still_imports_after_nonblocking_scan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ellipse.lbrn2"
    path.write_text(
        """
        <LightBurnProject AppVersion="1.7.08" FormatVersion="1">
          <Shape Type="Ellipse" CutIndex="0" Rx="5" Ry="3"/>
        </LightBurnProject>
        """,
        encoding="utf-8",
    )

    result = load_lightburn_project(path)

    assert result.app_version == "1.7.08"
    assert result.format_version == "1"
    assert len(result.objects) == 1
