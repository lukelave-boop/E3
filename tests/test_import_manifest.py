from dataclasses import FrozenInstanceError

import pytest

from laser_aligner.project import (
    DEFAULT_IMPORTER_REGISTRY,
    GCODE_IMPORTER_SPEC,
    LIGHTBURN_IMPORTER_SPEC,
    MAX_GCODE_FILE_BYTES,
    MAX_LIGHTBURN_FILE_BYTES,
    MAX_RASTER_ENCODED_BYTES,
    MAX_SVG_FILE_BYTES,
    RASTER_IMPORTER_SPEC,
    SUPPORTED_GCODE_SUFFIXES,
    SUPPORTED_LIGHTBURN_SUFFIXES,
    SUPPORTED_RASTER_SUFFIXES,
    SUPPORTED_SVG_SUFFIXES,
    SVG_IMPORTER_SPEC,
    ImportCapability,
    ImporterRegistry,
    ImporterSpec,
    ImportLayerManifest,
    ImportScanManifest,
)


def _spec(
    importer_id: str,
    suffix: str,
    *,
    display_name: str | None = None,
) -> ImporterSpec:
    return ImporterSpec(
        importer_id=importer_id,
        display_name=display_name or importer_id.title(),
        suffixes=(suffix,),
        capabilities=frozenset({ImportCapability.VECTOR_GEOMETRY}),
        max_file_bytes=1024,
        file_dialog_filter=f"{importer_id.title()} (*{suffix})",
    )


def test_default_registry_describes_supported_importers() -> None:
    assert GCODE_IMPORTER_SPEC.suffixes == tuple(sorted(SUPPORTED_GCODE_SUFFIXES))
    assert GCODE_IMPORTER_SPEC.max_file_bytes == MAX_GCODE_FILE_BYTES
    assert LIGHTBURN_IMPORTER_SPEC.suffixes == tuple(
        sorted(SUPPORTED_LIGHTBURN_SUFFIXES)
    )
    assert LIGHTBURN_IMPORTER_SPEC.max_file_bytes == MAX_LIGHTBURN_FILE_BYTES
    assert SVG_IMPORTER_SPEC.suffixes == SUPPORTED_SVG_SUFFIXES
    assert SVG_IMPORTER_SPEC.max_file_bytes == MAX_SVG_FILE_BYTES
    assert RASTER_IMPORTER_SPEC.suffixes == SUPPORTED_RASTER_SUFFIXES
    assert RASTER_IMPORTER_SPEC.max_file_bytes == MAX_RASTER_ENCODED_BYTES

    assert ImportCapability.ARC_GEOMETRY in GCODE_IMPORTER_SPEC.capabilities
    assert ImportCapability.SOURCE_LAYERS not in GCODE_IMPORTER_SPEC.capabilities
    assert ImportCapability.SOURCE_LAYERS in LIGHTBURN_IMPORTER_SPEC.capabilities
    assert ImportCapability.GROUPING in LIGHTBURN_IMPORTER_SPEC.capabilities
    assert ImportCapability.VECTOR_GEOMETRY in SVG_IMPORTER_SPEC.capabilities
    assert ImportCapability.GRAYSCALE_RASTER in RASTER_IMPORTER_SPEC.capabilities


@pytest.mark.parametrize(
    ("path", "expected_id"),
    [
        ("job.GCODE", "gcode"),
        ("job.tap", "gcode"),
        ("layout.LBRN", "lightburn"),
        ("layout.lbrn2", "lightburn"),
        ("artwork.SVG", "svg"),
        ("photo.PNG", "raster"),
        ("photo.jpg", "raster"),
        ("photo.JPEG", "raster"),
        ("photo.bmp", "raster"),
    ],
)
def test_default_registry_resolves_paths_case_insensitively(
    path: str,
    expected_id: str,
) -> None:
    spec = DEFAULT_IMPORTER_REGISTRY.for_path(path)
    assert spec is not None
    assert spec.importer_id == expected_id


def test_registry_lookup_normalizes_ids_and_suffixes() -> None:
    registry = ImporterRegistry((_spec("vector", ".vec"),))

    assert registry.get("VECTOR") is registry.specs[0]
    assert registry.for_suffix("VEC") is registry.specs[0]
    assert registry.for_path("C:/designs/PART.VEC") is registry.specs[0]
    assert registry.for_path("part.unknown") is None


def test_registry_rejects_duplicate_ids_and_suffix_claims() -> None:
    with pytest.raises(ValueError, match="Duplicate importer_id"):
        ImporterRegistry((_spec("same", ".one"), _spec("same", ".two")))

    with pytest.raises(ValueError, match="claimed by both"):
        ImporterRegistry((_spec("first", ".mix"), _spec("second", ".MIX")))


def test_importer_specs_are_normalized_and_immutable() -> None:
    spec = ImporterSpec(
        importer_id="  SAMPLE  ",
        display_name=" Sample Importer ",
        suffixes=("ABC", ".abc"),
        capabilities=frozenset({"vector_geometry"}),
        max_file_bytes=4096,
        file_dialog_filter="Sample (*.abc)",
    )

    assert spec.importer_id == "sample"
    assert spec.display_name == "Sample Importer"
    assert spec.suffixes == (".abc",)
    assert spec.capabilities == frozenset({ImportCapability.VECTOR_GEOMETRY})
    with pytest.raises(FrozenInstanceError):
        spec.display_name = "Changed"  # type: ignore[misc]


def test_scan_manifest_carries_nonblocking_review_facts() -> None:
    layer = ImportLayerManifest(
        source_key="cut:2",
        name="Cut 02",
        mode_hint="line",
        object_count=4,
    )
    manifest = ImportScanManifest(
        importer_id="lightburn",
        source_name="fixture.LBRN2",
        source_suffix="LBRN2",
        source_size_bytes=12345,
        capabilities=LIGHTBURN_IMPORTER_SPEC.capabilities,
        format_version="1",
        natural_width_mm=120.0,
        natural_height_mm=80.0,
        layers=(layer,),
        coordinate_facts=("Source geometry uses document-local millimetres",),
        warnings=("Output will remain disabled after import",),
        approximations=("Bezier curves will be flattened to bounded polylines",),
    )

    assert manifest.source_suffix == ".lbrn2"
    assert manifest.natural_size_mm == pytest.approx((120.0, 80.0))
    assert manifest.layers == (layer,)
    assert manifest.ready_for_parse
    with pytest.raises(FrozenInstanceError):
        manifest.source_name = "other.lbrn2"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("unsupported_features", "errors"),
    [
        (("Embedded bitmap",), ()),
        ((), ("Malformed header",)),
        (("Unsupported text object",), ("Malformed layer table",)),
    ],
)
def test_scan_manifest_blocks_known_unsupported_or_invalid_sources(
    unsupported_features: tuple[str, ...],
    errors: tuple[str, ...],
) -> None:
    manifest = ImportScanManifest(
        importer_id="lightburn",
        source_name="blocked.lbrn2",
        source_suffix=".lbrn2",
        source_size_bytes=100,
        unsupported_features=unsupported_features,
        errors=errors,
    )

    assert not manifest.ready_for_parse


def test_scan_manifest_requires_complete_natural_size_pair() -> None:
    with pytest.raises(ValueError, match="must be supplied together"):
        ImportScanManifest(
            importer_id="gcode",
            source_name="job.gcode",
            source_suffix=".gcode",
            source_size_bytes=100,
            natural_width_mm=50.0,
        )


def test_scan_manifest_normalizes_and_validates_optional_source_sha256() -> None:
    manifest = ImportScanManifest(
        importer_id="gcode",
        source_name="job.gcode",
        source_suffix=".gcode",
        source_size_bytes=100,
        source_sha256="AB" * 32,
    )

    assert manifest.source_sha256 == "ab" * 32

    with pytest.raises(ValueError, match="source_sha256"):
        ImportScanManifest(
            importer_id="gcode",
            source_name="job.gcode",
            source_suffix=".gcode",
            source_size_bytes=100,
            source_sha256="not-a-sha256",
        )
