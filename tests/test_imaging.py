from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner import imaging


def test_atomic_png_roundtrip_is_pixel_exact_on_unicode_path(tmp_path: Path) -> None:
    image = np.arange(18 * 24 * 3, dtype=np.uint8).reshape(18, 24, 3)
    path = tmp_path / "calibration-évidence.png"

    assert imaging.write_image_atomic(path, image) == path

    loaded = imaging.read_image(path)
    assert loaded is not None
    assert np.array_equal(loaded, image)


def test_failed_atomic_image_write_keeps_previous_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "reference.png"
    original = np.full((8, 8, 3), 25, dtype=np.uint8)
    imaging.write_image_atomic(path, original)

    def fail(_path: Path, _data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(imaging, "atomic_write_bytes", fail)
    with pytest.raises(OSError, match="disk full"):
        imaging.write_image_atomic(path, np.full_like(original, 220))

    assert np.array_equal(imaging.read_image(path), original)


def test_image_quality_reports_focus_contrast_and_clipping() -> None:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[:, 50:] = 255

    result = imaging.image_quality(image)

    assert result.width == 100
    assert result.height == 100
    assert result.sharpness > 0.0
    assert result.contrast_span == pytest.approx(255.0)
    assert result.shadow_clip_percent == pytest.approx(50.0)
    assert result.highlight_clip_percent == pytest.approx(50.0)


def test_image_helpers_reject_empty_or_unsupported_images(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        imaging.image_quality(np.empty((0, 0), dtype=np.uint8))
    with pytest.raises(ValueError, match="Unsupported"):
        imaging.write_image_atomic(tmp_path / "capture.bmp", np.zeros((4, 4), dtype=np.uint8))


def test_stable_payload_digest_dimensions_and_pixels_come_from_the_same_bytes(
    tmp_path: Path,
) -> None:
    image = np.arange(120 * 160 * 3, dtype=np.uint8).reshape(120, 160, 3)
    path = tmp_path / "evidence.png"
    imaging.write_image_atomic(path, image)

    payload = imaging.read_encoded_image_payload(path)
    decoded = imaging.decode_image_payload(payload, max_width=80, max_height=60)

    assert payload.content_sha256 == decoded.content_sha256
    assert payload.source_size == decoded.source_size == (160, 120)
    assert decoded.image.shape[:2] == (60, 80)
    assert decoded.encoded_size == path.stat().st_size


def test_stable_payload_enforces_encoded_byte_cap(tmp_path: Path) -> None:
    path = tmp_path / "evidence.png"
    ok, encoded = cv2.imencode(".png", np.zeros((20, 20, 3), dtype=np.uint8))
    assert ok
    path.write_bytes(encoded.tobytes())

    with pytest.raises(ValueError, match="byte limit"):
        imaging.read_encoded_image_payload(path, max_encoded_bytes=16)
