from __future__ import annotations

import hashlib
import math
import os
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

if os.name == "nt":  # pragma: no cover - exercised by Windows CI
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _WindowsFileTime(ctypes.Structure):
        _fields_ = [
            ("low", wintypes.DWORD),
            ("high", wintypes.DWORD),
        ]

    class _WindowsHandleInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", _WindowsFileTime),
            ("last_access_time", _WindowsFileTime),
            ("last_write_time", _WindowsFileTime),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    class _WindowsBasicFileInformation(ctypes.Structure):
        _fields_ = [
            ("creation_time", ctypes.c_longlong),
            ("last_access_time", ctypes.c_longlong),
            ("last_write_time", ctypes.c_longlong),
            ("change_time", ctypes.c_longlong),
            ("attributes", wintypes.DWORD),
        ]

    _WINDOWS_FILE_BASIC_INFO = 0
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _get_file_information_by_handle = _kernel32.GetFileInformationByHandle
    _get_file_information_by_handle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsHandleInformation),
    ]
    _get_file_information_by_handle.restype = wintypes.BOOL
    _get_file_information_by_handle_ex = _kernel32.GetFileInformationByHandleEx
    _get_file_information_by_handle_ex.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _get_file_information_by_handle_ex.restype = wintypes.BOOL

from .storage import atomic_write_bytes

_MAX_IMAGE_HEADER_BYTES = 1024 * 1024
MAX_STABLE_IMAGE_BYTES = 64 * 1024 * 1024
MAX_DECODED_IMAGE_PIXELS = 64_000_000


class ImageEvidenceChangedError(ValueError):
    """Raised when a path no longer names the bytes selected for analysis."""


def _windows_handle_version(
    descriptor: int,
) -> tuple[tuple[int, int] | None, int | None]:
    """Return stable Windows handle identity and change-time tokens when available."""

    if os.name != "nt":
        return None, None
    try:  # pragma: no cover - exercised by Windows CI
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
        identity = _WindowsHandleInformation()
        basic = _WindowsBasicFileInformation()
        if not _get_file_information_by_handle(handle, ctypes.byref(identity)):
            return None, None
        if not _get_file_information_by_handle_ex(
            handle,
            _WINDOWS_FILE_BASIC_INFO,
            ctypes.byref(basic),
            ctypes.sizeof(basic),
        ):
            return None, None
        file_index = (int(identity.file_index_high) << 32) | int(
            identity.file_index_low
        )
        return (int(identity.volume_serial_number), file_index), int(
            basic.change_time
        )
    except (OSError, ValueError):
        return None, None


@dataclass(frozen=True, slots=True)
class ImageFileIdentity:
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    windows_file_key: tuple[int, int] | None = None
    windows_change_time: int | None = None

    @classmethod
    def from_stat(cls, value: os.stat_result) -> ImageFileIdentity:
        return cls(
            size=int(value.st_size),
            mtime_ns=int(value.st_mtime_ns),
            ctime_ns=int(value.st_ctime_ns),
            device=int(value.st_dev),
            inode=int(value.st_ino),
        )

    @classmethod
    def from_descriptor(cls, descriptor: int) -> ImageFileIdentity:
        value = os.fstat(descriptor)
        windows_file_key, windows_change_time = _windows_handle_version(descriptor)
        return cls(
            size=int(value.st_size),
            mtime_ns=int(value.st_mtime_ns),
            ctime_ns=int(value.st_ctime_ns),
            device=int(value.st_dev),
            inode=int(value.st_ino),
            windows_file_key=windows_file_key,
            windows_change_time=windows_change_time,
        )

    def same_version(self, other: ImageFileIdentity) -> bool:
        """Compare one opened file version without mixing path/stat semantics."""

        if self.windows_file_key is not None or other.windows_file_key is not None:
            return (
                self.windows_file_key is not None
                and other.windows_file_key is not None
                and self.windows_change_time is not None
                and other.windows_change_time is not None
                and self.windows_file_key == other.windows_file_key
                and self.windows_change_time == other.windows_change_time
                and self.size == other.size
                and self.mtime_ns == other.mtime_ns
            )
        return (
            self.size,
            self.mtime_ns,
            self.ctime_ns,
            self.device,
            self.inode,
        ) == (
            other.size,
            other.mtime_ns,
            other.ctime_ns,
            other.device,
            other.inode,
        )


@dataclass(frozen=True, slots=True)
class EncodedImagePayload:
    source: Path
    encoded: bytes
    content_sha256: str
    source_size: tuple[int, int] | None
    file_identity: ImageFileIdentity | None = None


@dataclass(frozen=True, slots=True)
class DecodedImagePayload:
    image: np.ndarray
    content_sha256: str
    source_size: tuple[int, int]
    encoded_size: int
    file_identity: ImageFileIdentity | None


@dataclass(frozen=True, slots=True)
class ImageQuality:
    width: int
    height: int
    sharpness: float
    luminance_mean: float
    luminance_p01: float
    luminance_p99: float
    contrast_span: float
    shadow_clip_percent: float
    highlight_clip_percent: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _gray(image: np.ndarray) -> np.ndarray:
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("Image is empty")
    if image.dtype != np.uint8:
        raise ValueError("Image must use 8-bit pixels")
    if image.ndim == 2:
        return image
    if image.ndim != 3 or image.shape[2] not in {3, 4}:
        raise ValueError("Image must be grayscale, BGR, or BGRA")
    conversion = cv2.COLOR_BGRA2GRAY if image.shape[2] == 4 else cv2.COLOR_BGR2GRAY
    return cv2.cvtColor(image, conversion)


def sharpness_score(image: np.ndarray, *, crop_fraction: float = 0.0) -> float:
    """Return a comparable variance-of-Laplacian score for one fixed scene."""

    gray = _gray(image)
    try:
        crop = float(crop_fraction)
    except (TypeError, ValueError) as exc:
        raise ValueError("Sharpness crop fraction must be finite") from exc
    if type(crop_fraction) is bool or not math.isfinite(crop):
        raise ValueError("Sharpness crop fraction must be finite")
    crop = max(0.0, min(0.45, crop))
    if crop > 0.0 and min(gray.shape[:2]) >= 40:
        margin_y = max(1, int(round(gray.shape[0] * crop)))
        margin_x = max(1, int(round(gray.shape[1] * crop)))
        region = gray[margin_y : gray.shape[0] - margin_y, margin_x : gray.shape[1] - margin_x]
        if region.size:
            gray = region
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def image_quality(image: np.ndarray, *, sharpness_crop_fraction: float = 0.1) -> ImageQuality:
    """Measure focus and exposure evidence without applying camera-specific gates."""

    gray = _gray(image)
    p01, p99 = np.percentile(gray, (1.0, 99.0))
    return ImageQuality(
        width=int(gray.shape[1]),
        height=int(gray.shape[0]),
        sharpness=sharpness_score(image, crop_fraction=sharpness_crop_fraction),
        luminance_mean=float(np.mean(gray)),
        luminance_p01=float(p01),
        luminance_p99=float(p99),
        contrast_span=float(p99 - p01),
        shadow_clip_percent=float(np.mean(gray <= 2) * 100.0),
        highlight_clip_percent=float(np.mean(gray >= 253) * 100.0),
    )


def encode_image(image: np.ndarray, suffix: str, params: list[int] | None = None) -> bytes:
    extension = suffix.lower()
    if not extension.startswith("."):
        extension = f".{extension}"
    if extension not in {".png", ".jpg", ".jpeg"}:
        raise ValueError(f"Unsupported image format: {suffix}")
    _gray(image)
    ok, encoded = cv2.imencode(extension, image, params or [])
    if not ok:
        raise RuntimeError(f"Could not encode {extension} image")
    return encoded.tobytes()


def write_image_atomic(path: str | Path, image: np.ndarray, params: list[int] | None = None) -> Path:
    destination = Path(path)
    atomic_write_bytes(destination, encode_image(image, destination.suffix, params))
    return destination


def read_image(path: str | Path) -> np.ndarray | None:
    """Decode through bytes so non-ASCII paths behave consistently on every OS."""

    try:
        return decode_image_payload(read_encoded_image_payload(path)).image
    except (ImageEvidenceChangedError, ValueError):
        return None


def _jpeg_orientation(payload: bytes) -> int:
    if not payload.startswith(b"Exif\x00\x00"):
        return 1
    tiff = payload[6:]
    if len(tiff) < 8:
        return 1
    endian = "<" if tiff[:2] == b"II" else ">" if tiff[:2] == b"MM" else ""
    if not endian:
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
            if tag == 0x0112 and field_type == 3 and value_count == 1:
                orientation = int(
                    struct.unpack_from(endian + "H", tiff, entry + 8)[0]
                )
                return orientation if 1 <= orientation <= 8 else 1
    except (struct.error, ValueError):
        return 1
    return 1


def _jpeg_dimensions(payload: bytes, path: Path) -> tuple[int, int]:
    if not payload.startswith(b"\xff\xd8"):
        raise ValueError(f"JPEG has an invalid signature: {path}")
    offset = 2
    orientation = 1
    start_of_frame = {
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
            orientation = _jpeg_orientation(segment)
        if marker in start_of_frame:
            if len(segment) < 5:
                break
            height, width = struct.unpack_from(">HH", segment, 1)
            dimensions = int(width), int(height)
            return dimensions[::-1] if orientation in {5, 6, 7, 8} else dimensions
        if marker == 0xDA:
            break
        offset = segment_end
    raise ValueError(
        f"JPEG dimensions could not be read within {_MAX_IMAGE_HEADER_BYTES:,} bytes: {path}"
    )


def probe_encoded_image_dimensions(
    payload: bytes,
    *,
    source: str | Path = "<encoded image>",
) -> tuple[int, int]:
    """Read dimensions from the exact encoded bytes selected for decoding."""

    path = Path(source)
    header = payload[:_MAX_IMAGE_HEADER_BYTES]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(header) < 24 or header[12:16] != b"IHDR":
            raise ValueError(f"PNG header is truncated or invalid: {path}")
        width, height = struct.unpack(">II", header[16:24])
        dimensions = int(width), int(height)
    elif header.startswith(b"\xff\xd8"):
        dimensions = _jpeg_dimensions(header, path)
    else:
        raise ValueError(f"Unsupported image header: {path}")
    if dimensions[0] <= 0 or dimensions[1] <= 0:
        raise ValueError(f"Image reports invalid dimensions: {path}")
    return dimensions


def probe_image_dimensions(path: str | Path) -> tuple[int, int]:
    """Read PNG/JPEG dimensions from bounded header bytes without decoding pixels."""

    source = Path(path)
    try:
        with source.open("rb") as stream:
            payload = stream.read(_MAX_IMAGE_HEADER_BYTES)
    except OSError as exc:
        raise ValueError(f"Image metadata could not be read: {source}: {exc}") from exc
    return probe_encoded_image_dimensions(payload, source=source)


def encoded_image_payload(
    payload: bytes,
    *,
    source: str | Path = "<encoded image>",
    max_encoded_bytes: int = MAX_STABLE_IMAGE_BYTES,
    allow_invalid: bool = False,
) -> EncodedImagePayload:
    """Freeze and identify one bounded encoded image body."""

    if type(max_encoded_bytes) is not int or max_encoded_bytes <= 0:
        raise ValueError("Encoded image byte limit must be a positive integer")
    encoded = bytes(payload)
    if not encoded:
        raise ValueError(f"Image payload is empty: {source}")
    if len(encoded) > max_encoded_bytes:
        raise ValueError(
            f"Encoded image exceeds the {max_encoded_bytes:,}-byte limit: {source}"
        )
    path = Path(source)
    try:
        source_size = probe_encoded_image_dimensions(encoded, source=path)
    except ValueError:
        if not allow_invalid:
            raise
        source_size = None
    return EncodedImagePayload(
        source=path,
        encoded=encoded,
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        source_size=source_size,
    )


def read_encoded_image_payload(
    path: str | Path,
    *,
    max_encoded_bytes: int = MAX_STABLE_IMAGE_BYTES,
    allow_invalid: bool = False,
) -> EncodedImagePayload:
    """Read one stable, size-capped file body and its exact-byte identity."""

    source = Path(path)
    if type(max_encoded_bytes) is not int or max_encoded_bytes <= 0:
        raise ValueError("Encoded image byte limit must be a positive integer")
    try:
        with source.open("rb") as stream:
            descriptor_before = ImageFileIdentity.from_descriptor(stream.fileno())
            if descriptor_before.size <= 0:
                raise ValueError(f"Image payload is empty: {source}")
            if descriptor_before.size > max_encoded_bytes:
                raise ValueError(
                    f"Encoded image exceeds the {max_encoded_bytes:,}-byte limit: {source}"
                )
            chunks: list[bytes] = []
            total = 0
            while chunk := stream.read(min(1024 * 1024, max_encoded_bytes + 1 - total)):
                chunks.append(chunk)
                total += len(chunk)
                if total > max_encoded_bytes:
                    raise ValueError(
                        f"Encoded image exceeds the {max_encoded_bytes:,}-byte limit: {source}"
                    )
            descriptor_after = ImageFileIdentity.from_descriptor(stream.fileno())
        with source.open("rb") as current_stream:
            path_after = ImageFileIdentity.from_descriptor(current_stream.fileno())
    except OSError as exc:
        raise ValueError(f"Image payload could not be read: {source}: {exc}") from exc
    if not (
        descriptor_before.same_version(descriptor_after)
        and descriptor_after.same_version(path_after)
        and total == descriptor_before.size
    ):
        raise ImageEvidenceChangedError(
            f"Image evidence changed while its encoded bytes were being read: {source}"
        )
    encoded = b"".join(chunks)
    frozen = encoded_image_payload(
        encoded,
        source=source,
        max_encoded_bytes=max_encoded_bytes,
        allow_invalid=allow_invalid,
    )
    return EncodedImagePayload(
        source=frozen.source,
        encoded=frozen.encoded,
        content_sha256=frozen.content_sha256,
        source_size=frozen.source_size,
        file_identity=path_after,
    )


def assert_image_payload_current(payload: EncodedImagePayload) -> None:
    """Reject a path swap or in-place rewrite around payload analysis."""

    expected = payload.file_identity
    if expected is None:
        return
    try:
        with payload.source.open("rb") as stream:
            current = ImageFileIdentity.from_descriptor(stream.fileno())
    except OSError as exc:
        raise ImageEvidenceChangedError(
            f"Image evidence disappeared during analysis: {payload.source}"
        ) from exc
    if not current.same_version(expected):
        raise ImageEvidenceChangedError(
            f"Image evidence changed during analysis: {payload.source}"
        )


def decode_image_payload(
    payload: EncodedImagePayload,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
    max_decoded_pixels: int = MAX_DECODED_IMAGE_PIXELS,
) -> DecodedImagePayload:
    """Decode pixels from the same immutable bytes used for the content digest."""

    if (max_width is None) != (max_height is None):
        raise ValueError("Both bounded image dimensions must be provided together")
    if (
        max_width is not None
        and (
            type(max_width) is not int
            or type(max_height) is not int
            or max_width <= 0
            or max_height is None
            or max_height <= 0
        )
    ):
        raise ValueError("Bounded image dimensions must be positive")
    if type(max_decoded_pixels) is not int or max_decoded_pixels <= 0:
        raise ValueError("Decoded image pixel limit must be a positive integer")
    assert_image_payload_current(payload)
    if not isinstance(payload.encoded, bytes) or not payload.encoded:
        raise ValueError(f"Image payload is empty or invalid: {payload.source}")
    if len(payload.encoded) > MAX_STABLE_IMAGE_BYTES:
        raise ValueError(
            f"Encoded image exceeds the {MAX_STABLE_IMAGE_BYTES:,}-byte decode limit: "
            f"{payload.source}"
        )
    actual_digest = hashlib.sha256(payload.encoded).hexdigest()
    if actual_digest != payload.content_sha256:
        raise ImageEvidenceChangedError(
            f"Image payload digest does not match its encoded bytes: {payload.source}"
        )
    actual_size = probe_encoded_image_dimensions(
        payload.encoded,
        source=payload.source,
    )
    if payload.source_size != actual_size:
        raise ImageEvidenceChangedError(
            f"Image payload dimensions do not match its encoded bytes: {payload.source}"
        )
    if payload.source_size is None:
        raise ValueError(f"Image dimensions could not be decoded: {payload.source}")
    width, height = payload.source_size
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError(f"Image dimensions are invalid: {payload.source}")
    if width * height > max_decoded_pixels:
        raise ValueError(
            f"Image reports {width * height:,} pixels, exceeding the "
            f"{max_decoded_pixels:,}-pixel decode limit: {payload.source}"
        )
    reduction = 1
    if max_width is not None and max_height is not None:
        scale = max(width / max_width, height / max_height)
        for candidate in (2, 4, 8):
            if candidate <= scale:
                reduction = candidate
    flag = {
        1: cv2.IMREAD_COLOR,
        2: cv2.IMREAD_REDUCED_COLOR_2,
        4: cv2.IMREAD_REDUCED_COLOR_4,
        8: cv2.IMREAD_REDUCED_COLOR_8,
    }[reduction]
    try:
        image = cv2.imdecode(np.frombuffer(payload.encoded, dtype=np.uint8), flag)
        if image is None or image.size == 0:
            raise ValueError(f"Image pixels could not be decoded: {payload.source}")
        if max_width is not None and max_height is not None:
            decoded_height, decoded_width = image.shape[:2]
            resize_scale = min(
                max_width / decoded_width,
                max_height / decoded_height,
                1.0,
            )
            if resize_scale < 1.0:
                image = cv2.resize(
                    image,
                    (
                        max(1, int(round(decoded_width * resize_scale))),
                        max(1, int(round(decoded_height * resize_scale))),
                    ),
                    interpolation=cv2.INTER_AREA,
                )
    finally:
        assert_image_payload_current(payload)
    return DecodedImagePayload(
        image=image,
        content_sha256=payload.content_sha256,
        source_size=payload.source_size,
        encoded_size=len(payload.encoded),
        file_identity=payload.file_identity,
    )


def read_image_bounded(
    path: str | Path,
    *,
    max_width: int,
    max_height: int,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Decode one PNG/JPEG near a bounded working size, then enforce that bound."""

    decoded = decode_image_payload(
        read_encoded_image_payload(path),
        max_width=max_width,
        max_height=max_height,
    )
    return decoded.image, decoded.source_size
