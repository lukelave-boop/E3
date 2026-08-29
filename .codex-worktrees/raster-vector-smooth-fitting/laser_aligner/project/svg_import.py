"""Bounded SVG discovery and exact-source strict import adapters."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import SvgError
from ..geometry.svg import MAX_SVG_TEXT_CHARACTERS, SvgGeometry, parse_svg

if TYPE_CHECKING:
    from .import_manifest import ImportScanManifest

SUPPORTED_SVG_SUFFIXES = (".svg",)
SVG_FILE_DIALOG_FILTER = "Scalable Vector Graphics (*.svg)"
MAX_SVG_FILE_BYTES = MAX_SVG_TEXT_CHARACTERS


@dataclass(slots=True, frozen=True)
class SvgImportResult:
    """Detached authoritative SVG parse result ready for desktop conversion."""

    geometry: SvgGeometry
    source_text: str
    source_name: str
    source_sha256: str


def _suffix(source_name: str, source_suffix: str | None) -> str:
    suffix = (
        Path(source_name).suffix.casefold()
        if source_suffix is None
        else str(source_suffix).strip().casefold()
    )
    return suffix or ".svg"


def _incomplete_svg_error(warnings: tuple[str, ...] | list[str]) -> SvgError:
    return SvgError(
        "SVG import stopped because conversion would be incomplete: "
        + "; ".join(warnings)
        + ". Convert unsupported content to explicit vector paths and retry."
    )


def scan_svg_project(
    source: str | bytes,
    *,
    source_name: str = "untitled.svg",
    source_suffix: str | None = None,
    source_size_bytes: int | None = None,
    max_file_bytes: int = MAX_SVG_FILE_BYTES,
    source_sha256: str | None = None,
) -> ImportScanManifest:
    """Return bounded SVG facts without constructing native project objects."""

    from .import_manifest import (
        SVG_IMPORTER_SPEC,
        ImportLayerManifest,
        ImportScanManifest,
    )

    suffix = _suffix(source_name, source_suffix)
    limit = int(max_file_bytes)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")

    reported_size = (
        None if source_size_bytes is None else int(source_size_bytes)
    )
    if reported_size is not None and reported_size < 0:
        return ImportScanManifest(
            importer_id=SVG_IMPORTER_SPEC.importer_id,
            source_name=source_name,
            source_suffix=suffix,
            source_size_bytes=0,
            capabilities=SVG_IMPORTER_SPEC.capabilities,
            source_sha256=source_sha256 or "",
            errors=("SVG source size must not be negative",),
        )

    if not isinstance(source, (str, bytes)):
        return ImportScanManifest(
            importer_id=SVG_IMPORTER_SPEC.importer_id,
            source_name=source_name,
            source_suffix=suffix,
            source_size_bytes=reported_size or 0,
            capabilities=SVG_IMPORTER_SPEC.capabilities,
            source_sha256=source_sha256 or "",
            errors=("SVG input must be text or bytes",),
        )

    # Bound direct text scans before making an encoded copy. File scans arrive as
    # bytes and are bounded by ``_read_svg_file`` before decoding.
    if isinstance(source, str) and len(source) > MAX_SVG_TEXT_CHARACTERS:
        return ImportScanManifest(
            importer_id=SVG_IMPORTER_SPEC.importer_id,
            source_name=source_name,
            source_suffix=suffix,
            source_size_bytes=(
                reported_size if reported_size is not None else len(source)
            ),
            capabilities=SVG_IMPORTER_SPEC.capabilities,
            source_sha256=source_sha256 or "",
            errors=("SVG is larger than the 10 MB parser limit",),
        )

    if isinstance(source, str) and len(source) > limit:
        return ImportScanManifest(
            importer_id=SVG_IMPORTER_SPEC.importer_id,
            source_name=source_name,
            source_suffix=suffix,
            source_size_bytes=(
                reported_size if reported_size is not None else len(source)
            ),
            capabilities=SVG_IMPORTER_SPEC.capabilities,
            source_sha256=source_sha256 or "",
            errors=(f"SVG source exceeds the {limit:,}-byte import limit",),
        )

    payload = source.encode("utf-8") if isinstance(source, str) else source
    size = len(payload) if reported_size is None else reported_size
    if size > limit or len(payload) > limit:
        measured = max(size, len(payload))
        return ImportScanManifest(
            importer_id=SVG_IMPORTER_SPEC.importer_id,
            source_name=source_name,
            source_suffix=suffix,
            source_size_bytes=max(0, size),
            capabilities=SVG_IMPORTER_SPEC.capabilities,
            source_sha256=source_sha256 or "",
            errors=(
                f"SVG file exceeds the {limit:,}-byte import limit "
                f"({measured:,} bytes)",
            ),
        )

    digest = (
        hashlib.sha256(payload).hexdigest()
        if source_sha256 is None
        else source_sha256
    )
    base = {
        "importer_id": SVG_IMPORTER_SPEC.importer_id,
        "source_name": source_name,
        "source_suffix": suffix,
        "source_size_bytes": max(0, size),
        "capabilities": SVG_IMPORTER_SPEC.capabilities,
        "source_sha256": digest,
    }
    if suffix not in SUPPORTED_SVG_SUFFIXES:
        return ImportScanManifest(
            **base,
            errors=("SVG import requires the .svg extension",),
        )

    if isinstance(source, bytes):
        try:
            svg_text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return ImportScanManifest(
                **base,
                errors=("SVG file is not valid UTF-8 text",),
            )
    else:
        svg_text = source

    try:
        geometry = parse_svg(svg_text)
    except SvgError as exc:
        message = str(exc)
        if "unsupported" in message.casefold() or "not supported" in message.casefold():
            return ImportScanManifest(**base, unsupported_features=(message,))
        return ImportScanManifest(**base, errors=(message,))

    tags = {line.source_tag for line in geometry.polylines}
    approximations: list[str] = []
    if "path" in tags:
        approximations.append(
            "SVG path curves and arcs, when present, are flattened to bounded polylines"
        )
    if tags.intersection({"circle", "ellipse"}):
        approximations.append(
            "SVG circles and ellipses are flattened to bounded polylines"
        )
    if "rect" in tags:
        approximations.append(
            "Rounded SVG rectangle corners, when present, are flattened to bounded polylines"
        )

    coordinate_facts = [
        "Desktop import converts SVG source coordinates to physical millimetres, flips Y into E3 coordinates, and centers the result",
    ]
    if geometry.view_box is not None:
        coordinate_facts.append(
            "SVG viewBox: " + " ".join(f"{value:g}" for value in geometry.view_box)
        )

    return ImportScanManifest(
        **base,
        natural_width_mm=geometry.intrinsic_width_mm,
        natural_height_mm=geometry.intrinsic_height_mm,
        layers=(
            ImportLayerManifest(
                source_key="svg:artwork",
                name="SVG vector artwork",
                mode_hint="line",
                object_count=1,
            ),
        ),
        source_facts=(
            f"{len(geometry.polylines):,} vector path(s)",
            f"{geometry.point_count:,} flattened vector point(s)",
        ),
        coordinate_facts=tuple(coordinate_facts),
        approximations=tuple(approximations),
        unsupported_features=tuple(dict.fromkeys(geometry.warnings)),
    )


def _read_svg_file(
    path: str | Path,
    *,
    max_file_bytes: int,
) -> tuple[Path, bytes]:
    source = Path(path)
    limit = int(max_file_bytes)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise SvgError(f"Could not inspect SVG file: {exc}") from exc
    if size > limit:
        raise SvgError(f"SVG file exceeds the {limit:,}-byte import limit")
    try:
        with source.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError as exc:
        raise SvgError(f"Could not read SVG file: {exc}") from exc
    if len(payload) > limit:
        raise SvgError(f"SVG file exceeds the {limit:,}-byte import limit")
    return source, payload


def scan_svg_file(
    path: str | Path,
    *,
    max_file_bytes: int = MAX_SVG_FILE_BYTES,
) -> ImportScanManifest:
    """Read one bounded SVG payload and return its detached review manifest."""

    from .import_manifest import SVG_IMPORTER_SPEC, ImportScanManifest

    source = Path(path)
    suffix = source.suffix.casefold() or ".svg"
    limit = int(max_file_bytes)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")
    try:
        inspected_source, payload = _read_svg_file(
            source,
            max_file_bytes=limit,
        )
    except SvgError as exc:
        try:
            size = max(0, int(source.stat().st_size))
        except OSError:
            size = 0
        return ImportScanManifest(
            importer_id=SVG_IMPORTER_SPEC.importer_id,
            source_name=source.name or "untitled.svg",
            source_suffix=suffix,
            source_size_bytes=size,
            capabilities=SVG_IMPORTER_SPEC.capabilities,
            errors=(str(exc),),
        )

    digest = hashlib.sha256(payload).hexdigest()
    return scan_svg_project(
        payload,
        source_name=inspected_source.name,
        source_suffix=suffix,
        source_size_bytes=len(payload),
        max_file_bytes=limit,
        source_sha256=digest,
    )


def load_svg_project(
    path: str | Path,
    *,
    expected_source_sha256: str | None = None,
    max_file_bytes: int = MAX_SVG_FILE_BYTES,
) -> SvgImportResult:
    """Strictly parse one SVG file after optional reviewed-byte verification."""

    source_path = Path(path)
    if source_path.suffix.casefold() not in SUPPORTED_SVG_SUFFIXES:
        raise SvgError("SVG import requires the .svg extension")
    source, payload = _read_svg_file(path, max_file_bytes=max_file_bytes)
    digest = hashlib.sha256(payload).hexdigest()
    if (
        expected_source_sha256 is not None
        and digest != str(expected_source_sha256).strip().casefold()
    ):
        raise SvgError(
            "SVG source changed after import review; select and review the file again"
        )
    try:
        svg_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SvgError("SVG file is not valid UTF-8 text") from exc
    geometry = parse_svg(svg_text)
    if geometry.warnings:
        raise _incomplete_svg_error(geometry.warnings)
    return SvgImportResult(
        geometry=geometry,
        source_text=svg_text,
        source_name=source.name,
        source_sha256=digest,
    )


__all__ = [
    "MAX_SVG_FILE_BYTES",
    "SUPPORTED_SVG_SUFFIXES",
    "SVG_FILE_DIALOG_FILTER",
    "SvgImportResult",
    "load_svg_project",
    "scan_svg_file",
    "scan_svg_project",
]
