from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .import_manifest import ImportScanManifest

SUPPORTED_RASTER_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")
RASTER_FILE_DIALOG_FILTER = "Images (*.png *.jpg *.jpeg *.bmp)"
MAX_RASTER_ENCODED_BYTES = 32 * 1024 * 1024
MAX_RASTER_DECODED_BYTES = 16 * 1024 * 1024
MAX_RASTER_DIMENSION = 8192
_MAX_RASTER_HEADER_BYTES = 1024 * 1024
_DECODED_BYTES_PER_PIXEL = 4


@dataclass(frozen=True, slots=True)
class RasterAssetMetadata:
    path: str
    format: str
    width: int
    height: int
    raw_width: int
    raw_height: int
    bit_depth: int
    channels: int
    orientation: int
    encoded_bytes: int
    decoded_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class RasterAssetIdentity:
    path: str
    sha256: str
    encoded_bytes: int
    mtime_ns: int
    format: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class RasterAssetPayload:
    metadata: RasterAssetMetadata
    identity: RasterAssetIdentity
    encoded: bytes


def _absolute_path(path: str | Path) -> Path:
    return Path(path).expanduser().absolute()


def _bounded_dimensions(
    path: Path,
    width: int,
    height: int,
) -> tuple[int, int, int]:
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError(f"Raster image reports invalid dimensions: {path}")
    if width > MAX_RASTER_DIMENSION or height > MAX_RASTER_DIMENSION:
        raise ValueError(
            f"Raster image {path.name} is {width} x {height}, exceeding the "
            f"{MAX_RASTER_DIMENSION}-pixel dimension limit; resize the source image first"
        )
    decoded_bytes = width * height * _DECODED_BYTES_PER_PIXEL
    if decoded_bytes > MAX_RASTER_DECODED_BYTES:
        raise ValueError(
            f"Raster image {path.name} needs up to {decoded_bytes:,} decoded bytes, "
            f"exceeding the {MAX_RASTER_DECODED_BYTES:,}-byte decode limit; "
            "resize the source image first"
        )
    return width, height, decoded_bytes


def _exif_orientation(payload: bytes) -> int:
    if not payload.startswith(b"Exif\x00\x00"):
        return 1
    tiff = payload[6:]
    if len(tiff) < 8:
        return 1
    if tiff[:2] == b"II":
        endian = "<"
    elif tiff[:2] == b"MM":
        endian = ">"
    else:
        return 1
    try:
        if struct.unpack_from(endian + "H", tiff, 2)[0] != 42:
            return 1
        offset = int(struct.unpack_from(endian + "I", tiff, 4)[0])
        if offset < 8 or offset + 2 > len(tiff):
            return 1
        count = int(struct.unpack_from(endian + "H", tiff, offset)[0])
        if count > 256 or offset + 2 + count * 12 > len(tiff):
            return 1
        for index in range(count):
            entry = offset + 2 + index * 12
            tag, field_type, value_count = struct.unpack_from(
                endian + "HHI", tiff, entry
            )
            if tag != 0x0112 or field_type != 3 or value_count != 1:
                continue
            orientation = int(struct.unpack_from(endian + "H", tiff, entry + 8)[0])
            return orientation if 1 <= orientation <= 8 else 1
    except (struct.error, ValueError):
        return 1
    return 1


def _jpeg_header(payload: bytes, path: Path) -> tuple[int, int, int, int, int]:
    if not payload.startswith(b"\xff\xd8"):
        raise ValueError(f"Raster JPEG has an invalid signature: {path}")
    offset = 2
    orientation = 1
    supported_sof = {0xC0, 0xC1, 0xC2}
    all_sof = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while offset < len(payload):
        if payload[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(payload) and payload[offset] == 0xFF:
            offset += 1
        if offset >= len(payload):
            break
        marker = payload[offset]
        offset += 1
        if marker in {0x00, 0x01} or 0xD0 <= marker <= 0xD9:
            continue
        if offset + 2 > len(payload):
            break
        segment_length = int(struct.unpack_from(">H", payload, offset)[0])
        if segment_length < 2:
            break
        segment_start = offset + 2
        segment_end = offset + segment_length
        if segment_end > len(payload):
            break
        segment = payload[segment_start:segment_end]
        if marker == 0xE1 and segment.startswith(b"Exif\x00\x00"):
            orientation = _exif_orientation(segment)
        if marker in all_sof:
            if marker not in supported_sof:
                raise ValueError(
                    f"Raster JPEG uses an unsupported coding process: {path}"
                )
            if len(segment) < 6:
                break
            bit_depth = int(segment[0])
            height, width = struct.unpack_from(">HH", segment, 1)
            channels = int(segment[5])
            if bit_depth != 8 or channels not in {1, 3}:
                raise ValueError(
                    f"Raster JPEG must use 8-bit grayscale or RGB pixels: {path}"
                )
            return int(width), int(height), bit_depth, channels, orientation
        if marker == 0xDA:
            break
        offset = segment_end
    raise ValueError(
        f"Raster JPEG metadata could not be read within "
        f"{_MAX_RASTER_HEADER_BYTES:,} header bytes: {path}"
    )


def _png_header(payload: bytes, path: Path) -> tuple[int, int, int, int, int]:
    if len(payload) < 29 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"Raster PNG header is truncated or invalid: {path}")
    if payload[12:16] != b"IHDR":
        raise ValueError(f"Raster PNG does not begin with an IHDR chunk: {path}")
    width, height = struct.unpack(">II", payload[16:24])
    bit_depth = int(payload[24])
    color_type = int(payload[25])
    compression, filter_method, interlace = payload[26:29]
    allowed_depths = {
        0: {1, 2, 4, 8},
        2: {8},
        3: {1, 2, 4, 8},
        4: {8},
        6: {8},
    }
    # Palette PNGs can carry transparency in a later tRNS chunk, so budget
    # them as four-channel output even though the IHDR stores one index.
    decoded_channels = {0: 1, 2: 3, 3: 4, 4: 2, 6: 4}
    channels = decoded_channels.get(color_type)
    if channels is None or bit_depth not in allowed_depths[color_type]:
        raise ValueError(
            "Raster PNG must use up-to-8-bit grayscale or palette pixels, or "
            f"8-bit grayscale-alpha, RGB, or RGBA pixels: {path}"
        )
    if compression != 0 or filter_method != 0 or interlace not in {0, 1}:
        raise ValueError(f"Raster PNG uses unsupported encoding metadata: {path}")
    return int(width), int(height), bit_depth, channels, 1


def _png_transparent_gray(payload: bytes, path: Path) -> int | None:
    """Return a grayscale tRNS sample in OpenCV's decoded 8-bit domain."""

    if len(payload) < 29 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    bit_depth = int(payload[24])
    color_type = int(payload[25])
    if color_type != 0:
        return None
    offset = 8
    while offset + 12 <= len(payload):
        length = int(struct.unpack_from(">I", payload, offset)[0])
        chunk_end = offset + 12 + length
        if chunk_end > len(payload):
            raise ValueError(f"Raster PNG contains a truncated chunk: {path}")
        chunk_type = payload[offset + 4 : offset + 8]
        chunk_data = payload[offset + 8 : offset + 8 + length]
        if chunk_type == b"tRNS":
            if length != 2:
                raise ValueError(
                    f"Raster grayscale PNG has an invalid tRNS chunk: {path}"
                )
            sample = int(struct.unpack(">H", chunk_data)[0])
            maximum = (1 << bit_depth) - 1
            if sample > maximum:
                raise ValueError(
                    f"Raster grayscale PNG tRNS sample exceeds its bit depth: {path}"
                )
            return int(round(sample * 255.0 / maximum))
        if chunk_type in {b"IDAT", b"IEND"}:
            return None
        offset = chunk_end
    return None


def _bmp_header(payload: bytes, path: Path) -> tuple[int, int, int, int, int]:
    if len(payload) < 54 or not payload.startswith(b"BM"):
        raise ValueError(f"Raster BMP header is truncated or invalid: {path}")
    dib_size = int(struct.unpack_from("<I", payload, 14)[0])
    if dib_size < 40:
        raise ValueError(f"Raster BMP uses an unsupported DIB header: {path}")
    width, signed_height = struct.unpack_from("<ii", payload, 18)
    planes, bit_depth = struct.unpack_from("<HH", payload, 26)
    compression = int(struct.unpack_from("<I", payload, 30)[0])
    if planes != 1 or bit_depth not in {8, 24, 32} or compression != 0:
        raise ValueError(
            f"Raster BMP must use uncompressed 8-bit, 24-bit, or 32-bit pixels: {path}"
        )
    channels = 1 if bit_depth == 8 else bit_depth // 8
    return int(width), abs(int(signed_height)), int(bit_depth), channels, 1


def _raster_header(
    payload: bytes,
    source: Path,
) -> tuple[str, int, int, int, int, int]:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", *_png_header(payload, source)
    if payload.startswith(b"\xff\xd8"):
        return "jpeg", *_jpeg_header(payload, source)
    if payload.startswith(b"BM"):
        return "bmp", *_bmp_header(payload, source)
    raise ValueError(
        f"Unsupported raster image format: {source}; use PNG, JPEG, or BMP"
    )


def _metadata_from_encoded(
    source: Path,
    payload: bytes,
    *,
    mtime_ns: int,
) -> RasterAssetMetadata:
    (
        format_name,
        raw_width,
        raw_height,
        bit_depth,
        channels,
        orientation,
    ) = _raster_header(payload[:_MAX_RASTER_HEADER_BYTES], source)
    width, height = (
        (raw_height, raw_width)
        if orientation in {5, 6, 7, 8}
        else (raw_width, raw_height)
    )
    width, height, decoded_bytes = _bounded_dimensions(source, width, height)
    return RasterAssetMetadata(
        path=str(source),
        format=format_name,
        width=width,
        height=height,
        raw_width=int(raw_width),
        raw_height=int(raw_height),
        bit_depth=int(bit_depth),
        channels=int(channels),
        orientation=int(orientation),
        encoded_bytes=len(payload),
        decoded_bytes=decoded_bytes,
        mtime_ns=int(mtime_ns),
    )


def _read_bounded_stable_encoded(
    path: str | Path,
    *,
    max_file_bytes: int = MAX_RASTER_ENCODED_BYTES,
) -> tuple[Path, bytes, int]:
    """Read stable bounded bytes without interpreting raster content."""

    source = _absolute_path(path)
    limit = min(int(max_file_bytes), MAX_RASTER_ENCODED_BYTES)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")
    try:
        before = source.stat()
    except OSError as exc:
        raise ValueError(
            f"Raster image asset does not exist or cannot be read: {source}"
        ) from exc
    if not source.is_file():
        raise ValueError(f"Raster image asset is not a regular file: {source}")
    encoded_bytes = int(before.st_size)
    if encoded_bytes > limit:
        raise ValueError(
            f"Raster image {source.name} is {encoded_bytes:,} encoded bytes, "
            f"exceeding the {limit:,}-byte file limit"
        )
    try:
        with source.open("rb") as stream:
            payload = stream.read(limit + 1)
        after = source.stat()
    except OSError as exc:
        raise ValueError(f"Raster image asset could not be read: {source}: {exc}") from exc
    if len(payload) > limit:
        raise ValueError(
            f"Raster image {source.name} exceeds the {limit:,}-byte file limit"
        )
    if (
        len(payload) != encoded_bytes
        or int(after.st_size) != encoded_bytes
        or int(after.st_mtime_ns) != int(before.st_mtime_ns)
    ):
        raise ValueError(f"Raster image changed while it was being read: {source}")
    return source, payload, int(after.st_mtime_ns)


def probe_raster_asset(path: str | Path) -> RasterAssetMetadata:
    """Read bounded metadata and reject decode-bomb dimensions before any decode."""

    source = _absolute_path(path)
    try:
        stat = source.stat()
    except OSError as exc:
        raise ValueError(f"Raster image asset does not exist or cannot be read: {source}") from exc
    if not source.is_file():
        raise ValueError(f"Raster image asset is not a regular file: {source}")
    encoded_bytes = int(stat.st_size)
    if encoded_bytes <= 0:
        raise ValueError(f"Raster image asset is empty: {source}")
    if encoded_bytes > MAX_RASTER_ENCODED_BYTES:
        raise ValueError(
            f"Raster image {source.name} is {encoded_bytes:,} encoded bytes, exceeding "
            f"the {MAX_RASTER_ENCODED_BYTES:,}-byte file limit"
        )
    try:
        with source.open("rb") as stream:
            payload = stream.read(min(encoded_bytes, _MAX_RASTER_HEADER_BYTES))
    except OSError as exc:
        raise ValueError(f"Raster image metadata could not be read: {source}: {exc}") from exc

    (
        format_name,
        raw_width,
        raw_height,
        bit_depth,
        channels,
        orientation,
    ) = _raster_header(payload, source)
    width, height = (
        (raw_height, raw_width)
        if orientation in {5, 6, 7, 8}
        else (raw_width, raw_height)
    )
    width, height, decoded_bytes = _bounded_dimensions(source, width, height)
    return RasterAssetMetadata(
        path=str(source),
        format=format_name,
        width=width,
        height=height,
        raw_width=int(raw_width),
        raw_height=int(raw_height),
        bit_depth=int(bit_depth),
        channels=int(channels),
        orientation=int(orientation),
        encoded_bytes=encoded_bytes,
        decoded_bytes=decoded_bytes,
        mtime_ns=int(stat.st_mtime_ns),
    )


def _read_stable_payload(metadata: RasterAssetMetadata) -> bytes:
    source = Path(metadata.path)
    try:
        before = source.stat()
        if (
            int(before.st_size) != metadata.encoded_bytes
            or int(before.st_mtime_ns) != metadata.mtime_ns
        ):
            raise ValueError(f"Raster image changed after metadata inspection: {source}")
        with source.open("rb") as stream:
            payload = stream.read(MAX_RASTER_ENCODED_BYTES + 1)
        after = source.stat()
    except OSError as exc:
        raise ValueError(f"Raster image asset could not be read: {source}: {exc}") from exc
    if len(payload) > MAX_RASTER_ENCODED_BYTES:
        raise ValueError(
            f"Raster image {source.name} exceeds the "
            f"{MAX_RASTER_ENCODED_BYTES:,}-byte file limit"
        )
    if (
        len(payload) != metadata.encoded_bytes
        or int(after.st_size) != int(before.st_size)
        or int(after.st_mtime_ns) != int(before.st_mtime_ns)
    ):
        raise ValueError(f"Raster image changed while it was being read: {source}")
    return payload


def _identity(
    metadata: RasterAssetMetadata,
    payload: bytes,
    source_sha256: str,
) -> RasterAssetIdentity:
    return RasterAssetIdentity(
        path=metadata.path,
        sha256=source_sha256,
        encoded_bytes=len(payload),
        mtime_ns=metadata.mtime_ns,
        format=metadata.format,
        width=metadata.width,
        height=metadata.height,
    )


def capture_raster_asset_identity(path: str | Path) -> RasterAssetIdentity:
    return read_raster_asset_payload(path).identity


def read_raster_asset_payload(
    path: str | Path,
    *,
    metadata: RasterAssetMetadata | None = None,
    expected_source_sha256: str | None = None,
) -> RasterAssetPayload:
    """Return bounded encoded bytes and their identity from one stable read."""

    if expected_source_sha256 is not None:
        source, encoded, mtime_ns = _read_bounded_stable_encoded(path)
        source_sha256 = hashlib.sha256(encoded).hexdigest()
        if source_sha256 != str(expected_source_sha256).strip().casefold():
            raise ValueError(
                "Raster image source changed after import review; "
                "select and review the file again"
            )
        if metadata is not None and str(source) != metadata.path:
            raise ValueError("Raster metadata does not belong to the requested asset")
        inspected = _metadata_from_encoded(
            source,
            encoded,
            mtime_ns=mtime_ns,
        )
        return RasterAssetPayload(
            metadata=inspected,
            identity=_identity(inspected, encoded, source_sha256),
            encoded=encoded,
        )

    inspected = metadata or probe_raster_asset(path)
    source = _absolute_path(path)
    if str(source) != inspected.path:
        raise ValueError("Raster metadata does not belong to the requested asset")
    encoded = _read_stable_payload(inspected)
    source_sha256 = hashlib.sha256(encoded).hexdigest()
    (
        format_name,
        raw_width,
        raw_height,
        bit_depth,
        channels,
        orientation,
    ) = _raster_header(encoded, source)
    width, height = (
        (raw_height, raw_width)
        if orientation in {5, 6, 7, 8}
        else (raw_width, raw_height)
    )
    width, height, decoded_bytes = _bounded_dimensions(source, width, height)
    observed = (
        format_name,
        width,
        height,
        raw_width,
        raw_height,
        bit_depth,
        channels,
        orientation,
        decoded_bytes,
    )
    expected = (
        inspected.format,
        inspected.width,
        inspected.height,
        inspected.raw_width,
        inspected.raw_height,
        inspected.bit_depth,
        inspected.channels,
        inspected.orientation,
        inspected.decoded_bytes,
    )
    if observed != expected:
        raise ValueError(f"Raster image changed after metadata inspection: {source}")
    return RasterAssetPayload(
        metadata=inspected,
        identity=_identity(inspected, encoded, source_sha256),
        encoded=encoded,
    )


def _raster_scan_manifest(
    *,
    source_name: str,
    source_suffix: str,
    source_size_bytes: int,
    source_sha256: str,
    format_name: str,
    width: int,
    height: int,
    raw_width: int,
    raw_height: int,
    bit_depth: int,
    channels: int,
    orientation: int,
    decoded_bytes: int,
) -> ImportScanManifest:
    from .import_manifest import (
        RASTER_IMPORTER_SPEC,
        ImportLayerManifest,
        ImportScanManifest,
    )

    display_format = {"png": "PNG", "jpeg": "JPEG", "bmp": "BMP"}.get(
        format_name,
        format_name.upper(),
    )
    expected_suffixes = {
        "png": frozenset({".png"}),
        "jpeg": frozenset({".jpg", ".jpeg"}),
        "bmp": frozenset({".bmp"}),
    }.get(format_name, frozenset())
    warnings = (
        (
            f"The {source_suffix} extension contains {display_format} image data; "
            "review the source format before import"
        ),
    ) if expected_suffixes and source_suffix not in expected_suffixes else ()

    if orientation in {5, 6, 7, 8}:
        orientation_fact = (
            f"EXIF orientation {orientation} maps the raw {raw_width:,} x "
            f"{raw_height:,} pixels to {width:,} x {height:,} display pixels"
        )
    elif orientation != 1:
        orientation_fact = (
            f"EXIF orientation {orientation} preserves the {width:,} x "
            f"{height:,} display dimensions"
        )
    else:
        orientation_fact = "Source pixel orientation is used as encoded"

    return ImportScanManifest(
        importer_id=RASTER_IMPORTER_SPEC.importer_id,
        source_name=source_name,
        source_suffix=source_suffix,
        source_size_bytes=source_size_bytes,
        source_sha256=source_sha256,
        capabilities=RASTER_IMPORTER_SPEC.capabilities,
        layers=(
            ImportLayerManifest(
                source_key="image:0",
                name=Path(source_name).stem or "Raster image",
                mode_hint="raster",
                object_count=1,
            ),
        ),
        source_facts=(
            f"Encoded format: {display_format}",
            f"Oriented pixel dimensions: {width:,} x {height:,}",
            f"Source pixel metadata: {bit_depth}-bit, {channels} channel(s)",
            f"Bounded decoded-memory estimate: {decoded_bytes:,} bytes",
        ),
        coordinate_facts=(
            orientation_fact,
            "Raster pixels do not declare a trusted physical size; desktop "
            "import applies its existing work-area fit while preserving aspect ratio",
        ),
        warnings=warnings,
        approximations=(
            "Color and transparency are converted to grayscale, then output uses "
            "deterministic ordered dithering",
        ),
    )


def _raster_scan_error(
    *,
    source_name: str,
    source_suffix: str,
    source_size_bytes: int,
    error: str,
    source_sha256: str = "",
) -> ImportScanManifest:
    from .import_manifest import RASTER_IMPORTER_SPEC, ImportScanManifest

    return ImportScanManifest(
        importer_id=RASTER_IMPORTER_SPEC.importer_id,
        source_name=source_name,
        source_suffix=source_suffix,
        source_size_bytes=max(0, source_size_bytes),
        source_sha256=source_sha256,
        capabilities=RASTER_IMPORTER_SPEC.capabilities,
        errors=(error,),
    )


def scan_raster_project(
    payload: bytes,
    *,
    source_name: str = "untitled.png",
    source_suffix: str | None = None,
    source_size_bytes: int | None = None,
    max_file_bytes: int = MAX_RASTER_ENCODED_BYTES,
    source_sha256: str | None = None,
) -> ImportScanManifest:
    """Return deterministic bounded raster facts without decoding image pixels."""

    suffix = (
        Path(source_name).suffix.casefold()
        if source_suffix is None
        else str(source_suffix).strip().casefold()
    )
    if not suffix:
        suffix = ".png"
    name = str(source_name).strip() or "untitled.png"
    requested_limit = int(max_file_bytes)
    if requested_limit < 1:
        raise ValueError("max_file_bytes must be positive")
    limit = min(requested_limit, MAX_RASTER_ENCODED_BYTES)

    is_bytes = isinstance(payload, bytes)
    encoded_size = len(payload) if is_bytes else 0
    size = encoded_size if source_size_bytes is None else int(source_size_bytes)

    if not is_bytes:
        return _raster_scan_error(
            source_name=name,
            source_suffix=suffix,
            source_size_bytes=size,
            error="Raster image input must be bytes",
        )
    if size < 0:
        return _raster_scan_error(
            source_name=name,
            source_suffix=suffix,
            source_size_bytes=size,
            source_sha256=source_sha256 or "",
            error="Raster image source size must not be negative",
        )
    if size > limit or encoded_size > limit:
        measured = max(size, encoded_size)
        return _raster_scan_error(
            source_name=name,
            source_suffix=suffix,
            source_size_bytes=size,
            source_sha256=source_sha256 or "",
            error=(
                f"Raster image is {measured:,} encoded bytes, exceeding the "
                f"{limit:,}-byte file limit"
            ),
        )

    digest = (
        hashlib.sha256(payload).hexdigest()
        if source_sha256 is None
        else source_sha256
    )
    if suffix not in SUPPORTED_RASTER_SUFFIXES:
        return _raster_scan_error(
            source_name=name,
            source_suffix=suffix,
            source_size_bytes=size,
            source_sha256=digest,
            error="Raster import accepts PNG, JPEG, and BMP files",
        )
    if not payload:
        return _raster_scan_error(
            source_name=name,
            source_suffix=suffix,
            source_size_bytes=0,
            source_sha256=digest,
            error="Raster image asset is empty",
        )

    source = Path(name)
    try:
        (
            format_name,
            raw_width,
            raw_height,
            bit_depth,
            channels,
            orientation,
        ) = _raster_header(payload[:_MAX_RASTER_HEADER_BYTES], source)
        width, height = (
            (raw_height, raw_width)
            if orientation in {5, 6, 7, 8}
            else (raw_width, raw_height)
        )
        width, height, decoded_bytes = _bounded_dimensions(source, width, height)
    except ValueError as exc:
        return _raster_scan_error(
            source_name=name,
            source_suffix=suffix,
            source_size_bytes=size,
            source_sha256=digest,
            error=str(exc),
        )

    return _raster_scan_manifest(
        source_name=name,
        source_suffix=suffix,
        source_size_bytes=size,
        source_sha256=digest,
        format_name=format_name,
        width=width,
        height=height,
        raw_width=raw_width,
        raw_height=raw_height,
        bit_depth=bit_depth,
        channels=channels,
        orientation=orientation,
        decoded_bytes=decoded_bytes,
    )


def scan_raster_file(
    path: str | Path,
    *,
    max_file_bytes: int = MAX_RASTER_ENCODED_BYTES,
) -> ImportScanManifest:
    """Return a bounded review manifest for one stable raster file payload."""

    source = Path(path)
    name = source.name or "untitled.png"
    suffix = source.suffix.casefold() or ".png"
    requested_limit = int(max_file_bytes)
    if requested_limit < 1:
        raise ValueError("max_file_bytes must be positive")
    limit = min(requested_limit, MAX_RASTER_ENCODED_BYTES)

    try:
        _inspected_source, payload, _mtime_ns = _read_bounded_stable_encoded(
            source,
            max_file_bytes=limit,
        )
    except ValueError as exc:
        try:
            size = max(0, int(source.stat().st_size))
        except OSError:
            size = 0
        return _raster_scan_error(
            source_name=name,
            source_suffix=suffix,
            source_size_bytes=size,
            error=str(exc),
        )

    return scan_raster_project(
        payload,
        source_name=name,
        source_suffix=suffix,
        source_size_bytes=len(payload),
        max_file_bytes=limit,
    )


def decode_raster_grayscale(
    path: str | Path,
    *,
    metadata: RasterAssetMetadata | None = None,
) -> tuple[np.ndarray, RasterAssetIdentity]:
    """Decode one bounded 8-bit source and return pixels plus exact content identity."""

    import cv2

    payload = read_raster_asset_payload(path, metadata=metadata)
    inspected = payload.metadata
    source = Path(inspected.path)
    flags = cv2.IMREAD_COLOR if inspected.format == "jpeg" else cv2.IMREAD_UNCHANGED
    image = cv2.imdecode(np.frombuffer(payload.encoded, dtype=np.uint8), flags)
    if image is None or image.size == 0:
        raise ValueError(f"Raster image asset could not be decoded: {source}")
    if image.dtype != np.uint8:
        raise ValueError(f"Raster image must decode to 8-bit pixels: {source}")
    decoded_height, decoded_width = image.shape[:2]
    if (decoded_width, decoded_height) != (inspected.width, inspected.height):
        raise ValueError(
            f"Raster image dimensions do not match the bounded header metadata: {source}"
        )
    if image.ndim == 2:
        gray = image
        alpha = None
    elif image.ndim == 3 and image.shape[2] == 2:
        gray = image[:, :, 0]
        alpha = image[:, :, 1]
    elif image.ndim == 3 and image.shape[2] == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        alpha = None
    elif image.ndim == 3 and image.shape[2] == 4:
        gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2GRAY)
        alpha = image[:, :, 3]
    else:
        raise ValueError(f"Raster image has an unsupported channel layout: {source}")
    if alpha is None:
        output = gray.copy()
    else:
        opacity = alpha.astype(np.float32) / 255.0
        output = np.rint(gray.astype(np.float32) * opacity + 255.0 * (1.0 - opacity))
        output = output.astype(np.uint8)
    transparent_gray = _png_transparent_gray(payload.encoded, source)
    if transparent_gray is not None:
        output[gray == transparent_gray] = 255
    return output, payload.identity


def verify_raster_asset_identity(identity: RasterAssetIdentity) -> None:
    try:
        current = capture_raster_asset_identity(identity.path)
    except ValueError as exc:
        raise ValueError(
            f"Prepared raster asset is unavailable or no longer valid: {identity.path}: {exc}"
        ) from exc
    if current.sha256 != identity.sha256:
        raise ValueError(
            f"Prepared raster asset changed on disk: {identity.path}. "
            "Regenerate the toolpath and review its Preview again."
        )


def verify_raster_asset_identities(
    identities: tuple[RasterAssetIdentity, ...],
) -> None:
    seen: set[tuple[str, str]] = set()
    for identity in identities:
        key = (identity.path, identity.sha256)
        if key in seen:
            continue
        verify_raster_asset_identity(identity)
        seen.add(key)
