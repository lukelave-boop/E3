from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from laser_aligner.calibration.lens import LensCalibrator
from laser_aligner.config import LensCalibrationSettings


def test_capture_caches_detection_and_status_does_not_redetect(tmp_path: Path) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=7, rows=7, square_size_mm=35.0),
    )
    image = np.zeros((120, 160, 3), dtype=np.uint8)

    with patch.object(calibrator, "detect_corners", return_value=(True, np.zeros((49, 1, 2)))) as detect:
        result = calibrator.capture(image)
        assert result["found"] is True
        assert detect.call_count == 1

    with patch.object(calibrator, "detect_corners", side_effect=AssertionError("cache was bypassed")):
        first = calibrator.status()
        second = calibrator.status()

    assert first["image_count"] == 1
    assert first["usable_image_count"] == 1
    assert second["usable_image_count"] == 1


def test_existing_image_is_scanned_once_then_cached(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=7, rows=7))
    path = calibrator.image_dir / "existing.jpg"
    assert cv2.imwrite(str(path), np.zeros((80, 100, 3), dtype=np.uint8))

    with patch.object(calibrator, "detect_corners", return_value=(False, None)) as detect:
        assert calibrator.status()["usable_image_count"] == 0
        assert detect.call_count == 1
        assert calibrator.status()["usable_image_count"] == 0
        assert detect.call_count == 1


def test_pattern_change_invalidates_detection_cache(tmp_path: Path) -> None:
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    first = LensCalibrator(tmp_path, LensCalibrationSettings(columns=7, rows=7))
    with patch.object(first, "detect_corners", return_value=(True, np.zeros((49, 1, 2)))):
        first.capture(image)

    changed = LensCalibrator(tmp_path, LensCalibrationSettings(columns=9, rows=6))
    with patch.object(changed, "detect_corners", return_value=(False, None)) as detect:
        status = changed.status()

    assert detect.call_count == 1
    assert status["usable_image_count"] == 0
