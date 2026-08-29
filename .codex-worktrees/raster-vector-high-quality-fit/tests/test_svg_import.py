from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import laser_aligner.project.svg_import as svg_import_module
from laser_aligner.errors import SvgError
from laser_aligner.project.import_manifest import ImportCapability
from laser_aligner.project.svg_import import (
    load_svg_project,
    scan_svg_file,
    scan_svg_project,
)

_VALID_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="25.4mm" height="12.7mm" '
    'viewBox="0 0 100 50"><rect width="100" height="50"/></svg>'
)


def test_svg_project_scan_is_deterministic_and_reports_bounded_vector_facts() -> None:
    first = scan_svg_project(_VALID_SVG, source_name="fixture.svg")
    second = scan_svg_project(_VALID_SVG, source_name="fixture.svg")

    assert first == second
    assert first.ready_for_parse
    assert first.importer_id == "svg"
    assert first.source_size_bytes == len(_VALID_SVG.encode("utf-8"))
    assert first.source_sha256 == hashlib.sha256(
        _VALID_SVG.encode("utf-8")
    ).hexdigest()
    assert ImportCapability.VECTOR_GEOMETRY in first.capabilities
    assert first.natural_size_mm == pytest.approx((25.4, 12.7))
    assert first.layers[0].source_key == "svg:artwork"
    assert first.layers[0].object_count == 1
    assert any("vector path" in fact for fact in first.source_facts)
    assert any("flattened vector point" in fact for fact in first.source_facts)
    assert any("physical millimetres" in fact for fact in first.coordinate_facts)
    assert any("viewBox" in fact for fact in first.coordinate_facts)


def test_svg_file_scan_hashes_the_exact_source_bytes(tmp_path: Path) -> None:
    source = tmp_path / "exact.SVG"
    payload = ("\ufeff" + _VALID_SVG + "\n").encode("utf-8")
    source.write_bytes(payload)

    manifest = scan_svg_file(source)

    assert manifest.ready_for_parse
    assert manifest.source_name == "exact.SVG"
    assert manifest.source_suffix == ".svg"
    assert manifest.source_size_bytes == len(payload)
    assert manifest.source_sha256 == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<rect width="10" height="10"/><text>label</text></svg>',
            "Ignored unsupported elements: text",
        ),
        (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<style>.cut { transform: scale(0.5); }</style>'
            '<rect class="cut" width="10" height="10"/></svg>',
            "unsupported rendering semantics",
        ),
    ),
)
def test_svg_scan_preserves_fail_closed_unsupported_content(
    content: str,
    expected: str,
) -> None:
    manifest = scan_svg_project(content, source_name="incomplete.svg")

    assert not manifest.ready_for_parse
    assert any(expected in item for item in manifest.unsupported_features)
    assert manifest.errors == ()


def test_svg_scan_reports_malformed_and_non_utf8_sources_as_errors() -> None:
    malformed = scan_svg_project("<svg>", source_name="malformed.svg")
    payload = b"\xff\xfe\x00bad-svg"
    non_utf8 = scan_svg_project(payload, source_name="encoded.svg")

    assert not malformed.ready_for_parse
    assert any("Invalid SVG XML" in error for error in malformed.errors)
    assert not non_utf8.ready_for_parse
    assert non_utf8.errors == ("SVG file is not valid UTF-8 text",)
    assert non_utf8.source_size_bytes == len(payload)
    assert non_utf8.source_sha256 == hashlib.sha256(payload).hexdigest()


def test_svg_scan_applies_limits_before_parsing_or_large_text_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_parse(_text: str):
        raise AssertionError("An over-limit SVG must not reach the strict parser")

    monkeypatch.setattr(svg_import_module, "MAX_SVG_TEXT_CHARACTERS", 10)
    monkeypatch.setattr(svg_import_module, "parse_svg", unexpected_parse)

    text_manifest = scan_svg_project("x" * 11, source_name="large.svg")
    byte_manifest = scan_svg_project(
        _VALID_SVG.encode("utf-8"),
        source_name="large.svg",
        max_file_bytes=10,
    )

    assert not text_manifest.ready_for_parse
    assert text_manifest.errors == ("SVG is larger than the 10 MB parser limit",)
    assert byte_manifest.errors
    assert "10-byte import limit" in byte_manifest.errors[0]


def test_svg_scan_rejects_over_limit_bytes_before_hashing_or_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        svg_import_module.hashlib,
        "sha256",
        lambda _payload: pytest.fail("over-limit SVG must not be hashed"),
    )
    monkeypatch.setattr(
        svg_import_module,
        "parse_svg",
        lambda _text: pytest.fail("over-limit SVG must not be parsed"),
    )

    manifest = scan_svg_project(
        b"x" * 11,
        source_name="large.svg",
        max_file_bytes=10,
    )

    assert not manifest.ready_for_parse
    assert manifest.source_sha256 == ""
    assert "10-byte import limit" in manifest.errors[0]


def test_svg_file_scan_returns_blockers_for_suffix_size_and_read_errors(
    tmp_path: Path,
) -> None:
    wrong_suffix = tmp_path / "drawing.txt"
    wrong_suffix.write_text(_VALID_SVG, encoding="utf-8")
    over_limit = tmp_path / "large.svg"
    over_limit.write_text(_VALID_SVG, encoding="utf-8")

    suffix_manifest = scan_svg_file(wrong_suffix)
    size_manifest = scan_svg_file(over_limit, max_file_bytes=10)
    missing_manifest = scan_svg_file(tmp_path / "missing.svg")

    assert suffix_manifest.errors == ("SVG import requires the .svg extension",)
    assert suffix_manifest.source_size_bytes == len(_VALID_SVG.encode("utf-8"))
    assert suffix_manifest.source_sha256 == hashlib.sha256(
        _VALID_SVG.encode("utf-8")
    ).hexdigest()
    assert "10-byte import limit" in size_manifest.errors[0]
    assert "Could not inspect SVG file" in missing_manifest.errors[0]
    assert not suffix_manifest.ready_for_parse
    assert not size_manifest.ready_for_parse
    assert not missing_manifest.ready_for_parse


def test_svg_strict_loader_accepts_the_reviewed_bytes_and_preserves_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "approved.svg"
    source.write_text(_VALID_SVG, encoding="utf-8")
    manifest = scan_svg_file(source)

    result = load_svg_project(
        source,
        expected_source_sha256=manifest.source_sha256.upper(),
    )
    unbound_result = load_svg_project(source)

    assert result.source_text == _VALID_SVG
    assert result.source_name == "approved.svg"
    assert result.source_sha256 == manifest.source_sha256
    assert result.geometry.intrinsic_width_mm == pytest.approx(25.4)
    assert unbound_result.source_sha256 == manifest.source_sha256


def test_svg_strict_loader_rejects_changed_bytes_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "changed.svg"
    original = _VALID_SVG
    changed = _VALID_SVG.replace('width="100"', 'width="090"')
    assert len(original.encode("utf-8")) == len(changed.encode("utf-8"))
    source.write_text(original, encoding="utf-8")
    manifest = scan_svg_file(source)
    source.write_text(changed, encoding="utf-8")

    def unexpected_parse(_text: str):
        raise AssertionError("Digest mismatch must be checked before SVG parsing")

    monkeypatch.setattr(svg_import_module, "parse_svg", unexpected_parse)

    with pytest.raises(SvgError, match="changed after import review"):
        load_svg_project(
            source,
            expected_source_sha256=manifest.source_sha256,
        )


def test_svg_strict_parser_remains_authoritative_after_a_successful_scan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "strict.svg"
    source.write_text(_VALID_SVG, encoding="utf-8")
    manifest = scan_svg_file(source)
    assert manifest.ready_for_parse

    def reject_during_strict_parse(_text: str):
        raise SvgError("Strict SVG parser rejected the approved source")

    monkeypatch.setattr(svg_import_module, "parse_svg", reject_during_strict_parse)

    with pytest.raises(SvgError, match="Strict SVG parser rejected"):
        load_svg_project(
            source,
            expected_source_sha256=manifest.source_sha256,
        )


def test_svg_strict_loader_rejects_lossy_parser_warnings(tmp_path: Path) -> None:
    source = tmp_path / "warning.svg"
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect width="10" height="10"/><image href="part.png"/></svg>',
        encoding="utf-8",
    )
    manifest = scan_svg_file(source)
    assert not manifest.ready_for_parse

    with pytest.raises(SvgError, match="conversion would be incomplete"):
        load_svg_project(
            source,
            expected_source_sha256=manifest.source_sha256,
        )


def test_svg_scan_rejects_invalid_limit_configuration(tmp_path: Path) -> None:
    source = tmp_path / "fixture.svg"
    source.write_text(_VALID_SVG, encoding="utf-8")

    with pytest.raises(ValueError, match="max_file_bytes must be positive"):
        scan_svg_project(_VALID_SVG, max_file_bytes=0)
    with pytest.raises(ValueError, match="max_file_bytes must be positive"):
        scan_svg_file(source, max_file_bytes=0)
    with pytest.raises(ValueError, match="max_file_bytes must be positive"):
        load_svg_project(source, max_file_bytes=0)
