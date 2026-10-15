"""Source transparency remains separate from composited engraving brightness."""
from __future__ import annotations

import struct
import zlib

import cv2
import numpy as np
import pytest

from laser_aligner.project.raster_asset import (
    decode_raster_payload_grayscale,
    decode_raster_payload_planes,
    read_raster_asset_payload,
)


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(path, width, height, depth, color, rows, extra=b""):
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, 0))
        + extra + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )
    return read_raster_asset_payload(path)


def test_rgba_planes_decode_once_and_preserve_partial_alpha(tmp_path, monkeypatch):
    path = tmp_path / "alpha.png"
    original = np.array([
        [[0, 0, 0, 0], [40, 40, 40, 128], [90, 90, 90, 255]],
        [[80, 80, 80, 1], [0, 0, 0, 255], [255, 255, 255, 0]],
    ], dtype=np.uint8)
    assert cv2.imwrite(str(path), original)
    payload = read_raster_asset_payload(path)
    decode = cv2.imdecode
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return decode(*args, **kwargs)

    monkeypatch.setattr(cv2, "imdecode", counted)
    gray, allowed = decode_raster_payload_planes(payload)
    assert len(calls) == 1
    assert gray.dtype == np.uint8 and allowed.dtype == bool
    assert allowed.tolist() == [[False, True, True], [True, True, False]]
    opacity = original[:, :, 3].astype(np.float32) / 255
    expected = np.rint(original[:, :, 0].astype(np.float32) * opacity + 255 * (1 - opacity)).astype(np.uint8)
    assert np.array_equal(gray, expected)
    legacy = decode_raster_payload_grayscale(payload)
    assert len(calls) == 2  # The compatibility wrapper also performs one decode.
    assert np.array_equal(legacy, gray)
    assert np.array_equal(cv2.imread(str(path), cv2.IMREAD_UNCHANGED), original)


def test_grayscale_alpha_png_retains_eligibility_and_composited_brightness(tmp_path):
    payload = png(tmp_path / "gray-alpha.png", 4, 1, 8, 4,
                  b"\x00" + bytes([17, 0, 30, 128, 90, 255, 70, 1]))
    gray, allowed = decode_raster_payload_planes(payload)
    assert allowed.tolist() == [[False, True, True, True]]
    assert gray.tolist() == [[255, 142, 90, 254]]


@pytest.mark.parametrize("depth,width,packed,sample,expected_allowed", [
    (8, 3, b"\x00\x80\xff", 0, [False, True, True]),
    (1, 8, b"\x55", 0, [False, True, False, True, False, True, False, True]),
    (2, 4, b"\x1b", 1, [True, False, True, True]),
    (4, 4, b"\x05\xaf", 5, [True, False, True, True]),
])
def test_grayscale_trns_has_hard_eligibility(tmp_path, depth, width, packed, sample, expected_allowed):
    payload = png(tmp_path / "gray-trns.png", width, 1, depth, 0, b"\x00" + packed,
                  chunk(b"tRNS", struct.pack(">H", sample)))
    gray, allowed = decode_raster_payload_planes(payload)
    assert allowed.tolist() == [expected_allowed]
    assert np.all(gray[~allowed] == 255)
    assert np.array_equal(decode_raster_payload_grayscale(payload), gray)


def test_palette_trns_and_partial_alpha_are_decoded_together(tmp_path):
    extras = (chunk(b"PLTE", bytes([0, 0, 0, 30, 30, 30, 70, 70, 70]))
              + chunk(b"tRNS", bytes([0, 128, 255])))
    payload = png(tmp_path / "palette.png", 3, 1, 8, 3, b"\x00\x00\x01\x02", extras)
    gray, allowed = decode_raster_payload_planes(payload)
    assert allowed.tolist() == [[False, True, True]]
    assert gray.tolist() == [[255, 142, 70]]


def test_truecolor_trns_is_excluded(tmp_path):
    payload = png(tmp_path / "rgb-trns.png", 2, 1, 8, 2,
                  b"\x00" + bytes([20, 30, 40, 90, 90, 90]),
                  chunk(b"tRNS", struct.pack(">HHH", 20, 30, 40)))
    gray, allowed = decode_raster_payload_planes(payload)
    assert allowed.tolist() == [[False, True]]
    assert gray.tolist() == [[255, 90]]


@pytest.mark.parametrize("orientation", [2, 3, 4, 5, 6, 7, 8])
def test_oriented_jpeg_planes_keep_matching_verified_dimensions(tmp_path, orientation):
    raw = np.full((8, 12, 3), 127, dtype=np.uint8)
    path = tmp_path / "oriented.jpg"
    assert cv2.imwrite(str(path), raw)
    jpeg = path.read_bytes()
    exif = (b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x01\x00"
            b"\x12\x01\x03\x00\x01\x00\x00\x00" + struct.pack("<H", orientation)
            + b"\x00\x00\x00\x00\x00\x00")
    marker = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    path.write_bytes(jpeg[:2] + marker + jpeg[2:])
    payload = read_raster_asset_payload(path)
    gray, allowed = decode_raster_payload_planes(payload)
    assert gray.shape == allowed.shape == (payload.identity.height, payload.identity.width)
    assert np.all(allowed)
    assert gray.shape == ((12, 8) if orientation >= 5 else (8, 12))


@pytest.mark.parametrize("inverted", [False, True])
def test_transparent_pixels_cannot_be_restored_or_powered_in_generated_output(tmp_path, inverted):
    from laser_aligner.config import LaserSettings
    from laser_aligner.project import (
        LayerMode,
        ProjectDocument,
        SceneObject,
        Transform,
        generate_project_gcode,
    )
    from laser_aligner.project.image_edit import default_edit, process_image

    path = tmp_path / "transparent-output.png"
    pixels = np.full((32, 48, 4), 100, dtype=np.uint8)
    pixels[:, :, 3] = 255
    pixels[:, :20, 3] = 0
    assert cv2.imwrite(str(path), pixels)
    payload = read_raster_asset_payload(path)
    gray, allowed = decode_raster_payload_planes(payload)
    settings = default_edit(payload.identity.sha256, [48, 32])
    settings["invert"] = inverted
    settings["regions"] = [{
        "operation": "restore", "kind": "rectangle",
        "points": [[-.5, -.5], [.5, .5]], "radius": 0,
    }]
    geometry = {"asset": str(path), "image_edit": settings}
    output, retained = process_image(gray, geometry, source_allowed=allowed)
    assert not np.any(retained[:, :20])
    assert np.all(output[:, :20] == 255)
    document = ProjectDocument.new()
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 0.5
    layer.overscan_percent = 0
    document.add_object(SceneObject(
        kind="image", layer_id=layer.id, geometry=geometry,
        transform=Transform(60, 60, 24, 16),
    ))
    job = generate_project_gcode(document, LaserSettings())
    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    # Pixel 20 begins at X=48 + 20 * 0.5 = 58 mm in this source frame.
    assert all(min(move.start_x, move.end_x) >= 58 - 1e-9 for move in powered)
    assert all(not move.laser_on for move in job.plan.moves if move.rapid)
