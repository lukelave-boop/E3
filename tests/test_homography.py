from pathlib import Path

import cv2
import numpy as np

from laser_aligner.calibration.bed import BedMapper, BedPoint
from laser_aligner.config import BedCalibrationSettings, WorkArea


def test_bed_homography_round_trip(tmp_path: Path) -> None:
    mapper = BedMapper(tmp_path, BedCalibrationSettings(ransac_threshold_mm=0.2), WorkArea())
    image_points = np.array(
        [[100, 90], [900, 120], [950, 720], [70, 690], [500, 400], [300, 250], [730, 560]],
        dtype=np.float32,
    )
    known_h = np.array([[0.27, -0.01, -20.0], [0.005, -0.36, 250.0], [0.00002, -0.00003, 1.0]])
    machine_points = cv2.perspectiveTransform(image_points.reshape(-1, 1, 2), known_h).reshape(-1, 2)
    for index, (image, machine) in enumerate(zip(image_points, machine_points, strict=True)):
        mapper.add_point(BedPoint(float(image[0]), float(image[1]), float(machine[0]), float(machine[1]), str(index)))
    calibration = mapper.solve(1024, 768)
    assert calibration.rms_error_mm < 0.01
    x, y = mapper.image_to_mm(500, 400)
    expected = cv2.perspectiveTransform(np.array([[[500, 400]]], dtype=np.float32), known_h)[0, 0]
    assert np.allclose([x, y], expected, atol=0.01)
    u, v = mapper.mm_to_image(x, y)
    assert np.allclose([u, v], [500, 400], atol=0.01)

    test_image = np.zeros((768, 1024, 3), dtype=np.uint8)
    rectified = mapper.rectify(test_image, pixels_per_mm=2)
    assert rectified.shape[:2] == (440, 440)
