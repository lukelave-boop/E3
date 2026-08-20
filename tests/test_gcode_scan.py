import hashlib
from pathlib import Path

import pytest

import laser_aligner.project.gcode_import as gcode_module
from laser_aligner.project import (
    GcodeImportError,
    ImportCapability,
    load_gcode_project,
    scan_gcode_file,
    scan_gcode_project,
)


def test_gcode_scan_reports_operations_modal_facts_and_counts() -> None:
    manifest = scan_gcode_project(
        """
        ; S-value max: 1000
        G21 G90 M4
        G0 X0 Y0
        G1 F1000 S250 X10 Y0
        G0 X20 Y0
        G1 F2000 S750 X30 Y0
        M5
        """,
        source_name="panel.GCODE",
    )

    assert manifest.ready_for_parse
    assert manifest.source_suffix == ".gcode"
    assert ImportCapability.ARC_GEOMETRY in manifest.capabilities
    assert [
        (layer.name, layer.mode_hint, layer.object_count)
        for layer in manifest.layers
    ] == [
        ("1000 mm/min · S250 · M4", "line", 1),
        ("2000 mm/min · S750 · M4", "line", 1),
    ]
    assert any("powered moves" in fact for fact in manifest.source_facts)
    assert any("travel/unpowered" in fact for fact in manifest.source_facts)
    assert any("Power scale for review: 1000" == fact for fact in manifest.source_facts)
    assert any("absolute" in fact for fact in manifest.coordinate_facts)
    assert any("stated S maximum" in warning for warning in manifest.warnings)


def test_gcode_scan_reports_units_relative_mode_and_arc_approximation() -> None:
    manifest = scan_gcode_project(
        """
        G20 G91 M3 S255
        G1 F10 X1 Y0
        G3 X0 Y1 I0 J0.5
        M5
        """,
        source_name="arc.nc",
    )

    assert manifest.ready_for_parse
    assert manifest.layers[0].name.startswith("254 mm/min")
    assert any("inch units" in fact for fact in manifest.coordinate_facts)
    assert any("relative positioning" in fact for fact in manifest.coordinate_facts)
    assert any("G17 arcs" in fact for fact in manifest.coordinate_facts)
    assert any("2 powered moves" in fact for fact in manifest.source_facts)
    assert any("G2/G3 arcs" in item for item in manifest.approximations)
    assert any("inferred" in item for item in manifest.approximations)


@pytest.mark.parametrize(
    ("program", "message"),
    [
        ("G21 G90\nG92 X0\n", "G92"),
        ("G21 G90 M4 S100\nG1 F1000 X10 Z2\n", "Z-axis"),
        ("G21 G90 M4 S100\nG1 X10\n", "no positive modal F"),
        ("G21 G90 M4 S100\nG18 G2 F1000 X10 Y0 I5\n", "XY-plane"),
        ("/G1 X10\n", "block-delete"),
        ("N1 G1 X10*45\n", "checksummed"),
    ],
)
def test_gcode_scan_blocks_known_unsupported_or_invalid_programs(
    program: str,
    message: str,
) -> None:
    manifest = scan_gcode_project(program, source_name="blocked.gcode")

    assert not manifest.ready_for_parse
    combined = manifest.errors + manifest.unsupported_features
    assert any(message in item for item in combined)


def test_gcode_scan_does_not_sample_or_translate_motion(monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("geometry translation must not run during scan")

    monkeypatch.setattr(gcode_module, "_motion_points", fail_if_called)
    monkeypatch.setattr(gcode_module, "_append_move", fail_if_called)

    manifest = gcode_module.scan_gcode_project(
        """
        G21 G90 M4 S100
        G1 F600 X10 Y0
        G3 X0 Y10 I-10 J0
        M5
        """,
        source_name="scan-only.gcode",
    )

    assert manifest.ready_for_parse
    assert manifest.layers[0].object_count == 1
    assert any("G2/G3 arcs" in item for item in manifest.approximations)


def test_gcode_scan_blocks_conflicting_or_exceeded_s_scale() -> None:
    conflicting = scan_gcode_project(
        "; S-value max: 1000\n; $30=255\n"
        "G21 G90 M4 S100\nG1 F1000 X1\n",
        source_name="conflict.gc",
    )
    assert not conflicting.ready_for_parse
    assert any("conflicting" in error for error in conflicting.errors)

    exceeded = scan_gcode_project(
        "; S-value max: 255\n"
        "G21 G90 M4 S500\nG1 F1000 X1\n",
        source_name="exceeded.gc",
    )
    assert not exceeded.ready_for_parse
    assert any("exceeds" in error for error in exceeded.errors)


def test_gcode_loader_blocks_manifest_before_strict_translation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "blocked.gcode"
    path.write_text("G21 G90\nG92 X0\n", encoding="utf-8")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("strict translator must not run after blocking scan")

    monkeypatch.setattr(gcode_module, "parse_gcode_project", fail_if_called)

    with pytest.raises(GcodeImportError, match="G92"):
        gcode_module.load_gcode_project(path)


def test_scan_gcode_file_reports_extension_size_and_encoding_errors(
    tmp_path: Path,
) -> None:
    wrong = tmp_path / "sample.txt"
    wrong.write_text("G1 X1", encoding="utf-8")
    wrong_manifest = scan_gcode_file(wrong)
    assert not wrong_manifest.ready_for_parse
    assert any(".gc" in error for error in wrong_manifest.errors)

    large = tmp_path / "large.gcode"
    large.write_text("G21 G90 M4 S100\nG1 F1000 X10\n", encoding="utf-8")
    large_manifest = scan_gcode_file(large, max_file_bytes=8)
    assert not large_manifest.ready_for_parse
    assert any("import limit" in error for error in large_manifest.errors)

    binary = tmp_path / "binary.gcode"
    binary.write_bytes(b"\xff\xfe\x00")
    binary_manifest = scan_gcode_file(binary)
    assert not binary_manifest.ready_for_parse
    assert any("UTF-8" in error for error in binary_manifest.errors)


def test_gcode_file_scan_digest_binds_exact_raw_bytes_and_memory_scan_is_deterministic(
    tmp_path: Path,
) -> None:
    payload = b"\xef\xbb\xbfG21 G90 M4 S100\nG1 F600 X10 Y0\nM5\n"
    path = tmp_path / "digest.gcode"
    path.write_bytes(payload)

    file_manifest = scan_gcode_file(path)
    decoded = payload.decode("utf-8-sig")
    first_memory_manifest = scan_gcode_project(decoded, source_name=path.name)
    second_memory_manifest = scan_gcode_project(decoded, source_name=path.name)

    assert file_manifest.ready_for_parse
    assert file_manifest.source_size_bytes == len(payload)
    assert file_manifest.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert first_memory_manifest == second_memory_manifest
    assert first_memory_manifest.source_sha256 == hashlib.sha256(
        decoded.encode("utf-8")
    ).hexdigest()
    assert file_manifest.source_sha256 != first_memory_manifest.source_sha256


def test_gcode_loader_rejects_content_changed_after_review_before_translation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "changed.gcode"
    path.write_text("G21 G90 M4 S100\nG1 F600 X10 Y0\nM5\n", encoding="utf-8")
    manifest = scan_gcode_file(path)
    path.write_text("G21 G90 M4 S100\nG1 F600 X20 Y0\nM5\n", encoding="utf-8")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("changed reviewed source must not reach translation")

    monkeypatch.setattr(gcode_module, "parse_gcode_project", fail_if_called)

    with pytest.raises(GcodeImportError, match="changed after import review"):
        load_gcode_project(path, expected_source_sha256=manifest.source_sha256)


def test_existing_gcode_loader_still_imports_after_nonblocking_scan(
    tmp_path: Path,
) -> None:
    path = tmp_path / "panel.gc"
    path.write_text(
        "; S-value max: 1000\n"
        "G21 G90 M4 S500\n"
        "G1 F1200 X20 Y0\n"
        "M5\n",
        encoding="utf-8",
    )

    result = load_gcode_project(path)

    assert len(result.objects) == 1
    assert result.powered_move_count == 1
    assert result.power_scale == pytest.approx(1000.0)
