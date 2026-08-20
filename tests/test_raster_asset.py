import hashlib
import os
import struct
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest

import laser_aligner.project.raster_asset as raster_asset_module
from laser_aligner.project import (
    MAX_RASTER_DECODED_BYTES,
    RASTER_FILE_DIALOG_FILTER,
    capture_raster_asset_identity,
    decode_raster_grayscale,
    probe_raster_asset,
    read_raster_asset_payload,
    scan_raster_file,
    scan_raster_project,
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


@pytest.mark.parametrize(
    ("suffix", "format_name"),
    [(".png", "PNG"), (".jpg", "JPEG"), (".jpeg", "JPEG"), (".bmp", "BMP")],
)
def test_raster_file_scan_reports_bounded_metadata_and_exact_digest_without_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    format_name: str,
) -> None:
    path = tmp_path / f"review{suffix}"
    assert cv2.imwrite(str(path), np.full((7, 11, 3), 93, dtype=np.uint8))
    encoded = path.read_bytes()
    monkeypatch.setattr(
        cv2,
        "imdecode",
        lambda *_args, **_kwargs: pytest.fail("raster scan must not decode pixels"),
    )

    manifest = scan_raster_file(path)

    assert manifest.ready_for_parse
    assert manifest.importer_id == "raster"
    assert manifest.source_name == path.name
    assert manifest.source_suffix == suffix
    assert manifest.source_size_bytes == len(encoded)
    assert manifest.source_sha256 == hashlib.sha256(encoded).hexdigest()
    assert manifest.natural_size_mm is None
    assert {value.value for value in manifest.capabilities} == {"grayscale_raster"}
    assert [(layer.source_key, layer.name, layer.mode_hint, layer.object_count) for layer in manifest.layers] == [
        ("image:0", "review", "raster", 1)
    ]
    assert f"Encoded format: {format_name}" in manifest.source_facts
    assert "Oriented pixel dimensions: 11 x 7" in manifest.source_facts
    assert any("physical size" in fact for fact in manifest.coordinate_facts)
    assert any("grayscale" in fact for fact in manifest.approximations)


def test_raster_project_scan_is_deterministic_and_does_not_construct_or_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "memory.png"
    assert cv2.imwrite(str(path), np.zeros((3, 5, 4), dtype=np.uint8))
    encoded = path.read_bytes()
    monkeypatch.setattr(
        cv2,
        "imdecode",
        lambda *_args, **_kwargs: pytest.fail("in-memory scan must not decode pixels"),
    )

    first = scan_raster_project(encoded, source_name="memory.png")
    second = scan_raster_project(encoded, source_name="memory.png")

    assert first == second
    assert first.ready_for_parse
    assert first.source_size_bytes == len(encoded)
    assert first.source_sha256 == hashlib.sha256(encoded).hexdigest()
    assert first.layers[0].object_count == 1


def test_raster_project_scan_rejects_over_limit_bytes_before_hash_or_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        raster_asset_module.hashlib,
        "sha256",
        lambda _payload: pytest.fail("over-limit raster must not be hashed"),
    )
    monkeypatch.setattr(
        raster_asset_module,
        "_raster_header",
        lambda *_args: pytest.fail("over-limit raster must not be inspected"),
    )

    manifest = scan_raster_project(
        b"x" * 11,
        source_name="large.png",
        max_file_bytes=10,
    )

    assert not manifest.ready_for_parse
    assert manifest.source_sha256 == ""
    assert "10-byte file limit" in manifest.errors[0]


@pytest.mark.parametrize("source_name", ["blocked.tif", "blocked.unknown"])
def test_raster_file_scan_hashes_readable_wrong_suffix_before_blocking(
    tmp_path: Path,
    source_name: str,
) -> None:
    source = tmp_path / "source.png"
    assert cv2.imwrite(str(source), np.zeros((2, 4, 3), dtype=np.uint8))
    path = tmp_path / source_name
    encoded = source.read_bytes()
    path.write_bytes(encoded)

    manifest = scan_raster_file(path)

    assert not manifest.ready_for_parse
    assert manifest.source_sha256 == hashlib.sha256(encoded).hexdigest()
    assert "accepts PNG, JPEG, and BMP" in manifest.errors[0]


def test_raster_file_scan_hashes_malformed_bounded_payload_and_reports_limits(
    tmp_path: Path,
) -> None:
    malformed = tmp_path / "malformed.png"
    encoded = b"not a raster image"
    malformed.write_bytes(encoded)

    malformed_manifest = scan_raster_file(malformed)
    limited_manifest = scan_raster_file(malformed, max_file_bytes=len(encoded) - 1)

    assert not malformed_manifest.ready_for_parse
    assert malformed_manifest.source_sha256 == hashlib.sha256(encoded).hexdigest()
    assert "Unsupported raster image format" in malformed_manifest.errors[0]
    assert not limited_manifest.ready_for_parse
    assert limited_manifest.source_sha256 == ""
    assert "file limit" in limited_manifest.errors[0]


def test_reviewed_raster_digest_is_verified_without_pixel_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "reviewed.png"
    assert cv2.imwrite(str(path), np.full((4, 6, 3), 17, dtype=np.uint8))
    manifest = scan_raster_file(path)
    monkeypatch.setattr(
        cv2,
        "imdecode",
        lambda *_args, **_kwargs: pytest.fail(
            "strict source verification must not decode pixels"
        ),
    )
    monkeypatch.setattr(
        raster_asset_module,
        "probe_raster_asset",
        lambda *_args, **_kwargs: pytest.fail(
            "reviewed bytes must be verified before the strict header probe"
        ),
    )

    payload = read_raster_asset_payload(
        path,
        expected_source_sha256=manifest.source_sha256.upper(),
    )

    assert payload.identity.sha256 == manifest.source_sha256
    assert (payload.metadata.width, payload.metadata.height) == (6, 4)


def test_reviewed_raster_digest_rejects_valid_same_dimension_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "changed.png"
    assert cv2.imwrite(str(path), np.full((4, 6, 3), 17, dtype=np.uint8))
    manifest = scan_raster_file(path)
    assert cv2.imwrite(str(path), np.full((4, 6, 3), 221, dtype=np.uint8))

    with pytest.raises(ValueError, match="changed after import review"):
        read_raster_asset_payload(
            path,
            expected_source_sha256=manifest.source_sha256,
        )


def test_reviewed_raster_digest_rejects_malformed_replacement_before_header_parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "malformed-after-review.png"
    assert cv2.imwrite(str(path), np.full((4, 6, 3), 17, dtype=np.uint8))
    manifest = scan_raster_file(path)
    path.write_bytes(b"replacement is not a raster image")
    monkeypatch.setattr(
        raster_asset_module,
        "_raster_header",
        lambda *_args: pytest.fail(
            "changed reviewed bytes must be rejected before header parsing"
        ),
    )

    with pytest.raises(ValueError, match="changed after import review"):
        read_raster_asset_payload(
            path,
            expected_source_sha256=manifest.source_sha256,
        )
