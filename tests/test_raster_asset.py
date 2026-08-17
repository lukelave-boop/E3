import os
import struct
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.project import (
    MAX_RASTER_DECODED_BYTES,
    RASTER_FILE_DIALOG_FILTER,
    capture_raster_asset_identity,
    decode_raster_grayscale,
    probe_raster_asset,
    read_raster_asset_payload,
    verify_raster_asset_identity,
)


@pytest.mark.parametrize(
    ("suffix", "format_name"),
    [(".png", "png"), (".jpg", "jpeg"), (".bmp", "bmp")],
)
def test_shared_raster_probe_reports_bounded_decode_metadata(
    tmp_path: Path,
    suffix: str,
    format_name: str,
) -> None:
    path = tmp_path / f"source{suffix}"
    assert cv2.imwrite(str(path), np.zeros((7, 11, 3), dtype=np.uint8))

    metadata = probe_raster_asset(path)

    assert metadata.format == format_name
    assert (metadata.width, metadata.height) == (11, 7)
    assert metadata.bit_depth in {8, 24}
    assert metadata.channels in {3, 4}
    assert metadata.decoded_bytes == 11 * 7 * 4
    assert metadata.decoded_bytes <= MAX_RASTER_DECODED_BYTES
    assert suffix in RASTER_FILE_DIALOG_FILTER


@pytest.mark.parametrize("xmp_position", ["before", "after"])
def test_shared_jpeg_probe_and_decode_apply_the_same_exif_orientation(
    tmp_path: Path,
    xmp_position: str,
) -> None:
    raw = tmp_path / "raw.jpg"
    assert cv2.imwrite(str(raw), np.full((2, 3, 3), 127, dtype=np.uint8))
    jpeg = raw.read_bytes()
    exif = (
        b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x01\x00"
        b"\x12\x01\x03\x00\x01\x00\x00\x00\x06\x00\x00\x00"
        b"\x00\x00\x00\x00"
    )
    exif_marker = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    xmp = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>"
    xmp_marker = b"\xff\xe1" + (len(xmp) + 2).to_bytes(2, "big") + xmp
    oriented = tmp_path / "oriented.jpg"
    markers = (
        xmp_marker + exif_marker
        if xmp_position == "before"
        else exif_marker + xmp_marker
    )
    oriented.write_bytes(jpeg[:2] + markers + jpeg[2:])

    metadata = probe_raster_asset(oriented)
    gray, identity = decode_raster_grayscale(oriented, metadata=metadata)

    assert metadata.orientation == 6
    assert (metadata.width, metadata.height) == (2, 3)
    assert gray.shape == (3, 2)
    assert identity.width == 2 and identity.height == 3


def test_unsupported_png_depth_is_rejected_before_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sixteen-bit.png"
    assert cv2.imwrite(str(path), np.zeros((8, 8), dtype=np.uint16))
    monkeypatch.setattr(
        cv2,
        "imdecode",
        lambda *_args, **_kwargs: pytest.fail(
            "unsupported header metadata must be rejected before decode"
        ),
    )

    with pytest.raises(ValueError, match="must use up-to-8-bit"):
        decode_raster_grayscale(path)


def test_identity_verification_hashes_content_not_only_file_stat(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity.bmp"
    assert cv2.imwrite(str(path), np.zeros((3, 3, 3), dtype=np.uint8))
    identity = capture_raster_asset_identity(path)
    original_stat = path.stat()
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    with pytest.raises(ValueError, match="changed on disk"):
        verify_raster_asset_identity(identity)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


@pytest.mark.parametrize(
    ("bit_depth", "width", "packed", "transparent_sample", "expected"),
    [
        (8, 3, b"\x00\x80\xff", 0, [255, 128, 255]),
        (1, 8, b"\x55", 0, [255] * 8),
        (2, 4, b"\x1b", 1, [0, 255, 170, 255]),
        (4, 4, b"\x05\xaf", 5, [0, 255, 170, 255]),
    ],
)
def test_grayscale_png_transparency_is_composited_on_white(
    tmp_path: Path,
    bit_depth: int,
    width: int,
    packed: bytes,
    transparent_sample: int,
    expected: list[int],
) -> None:
    path = tmp_path / f"gray-trns-{bit_depth}.png"
    header = struct.pack(">IIBBBBB", width, 1, bit_depth, 0, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"tRNS", struct.pack(">H", transparent_sample))
        + _png_chunk(b"IDAT", zlib.compress(b"\x00" + packed))
        + _png_chunk(b"IEND", b"")
    )

    gray, _identity = decode_raster_grayscale(path)

    assert gray.tolist() == [expected]


def test_shared_payload_binds_metadata_identity_and_exact_encoded_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "payload.png"
    assert cv2.imwrite(str(path), np.full((4, 6, 3), 83, dtype=np.uint8))

    payload = read_raster_asset_payload(path)
    gray, decoded_identity = decode_raster_grayscale(
        path,
        metadata=payload.metadata,
    )

    assert payload.encoded == path.read_bytes()
    assert payload.identity == capture_raster_asset_identity(path)
    assert decoded_identity == payload.identity
    assert gray.shape == (4, 6)


def test_shared_payload_rejects_header_change_with_restored_file_stat(
    tmp_path: Path,
) -> None:
    path = tmp_path / "header-race.bmp"
    assert cv2.imwrite(str(path), np.zeros((1, 2, 3), dtype=np.uint8))
    metadata = probe_raster_asset(path)
    original = path.stat()
    assert cv2.imwrite(str(path), np.zeros((2, 1, 3), dtype=np.uint8))
    assert path.stat().st_size == original.st_size
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))

    with pytest.raises(ValueError, match="changed after metadata inspection"):
        read_raster_asset_payload(path, metadata=metadata)
