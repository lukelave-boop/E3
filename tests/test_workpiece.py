import cv2
import numpy as np

from laser_aligner.vision.workpiece import detect_workpiece


def test_detect_rotated_rectangular_workpiece() -> None:
    image = np.full((500, 600, 3), 55, dtype=np.uint8)
    box = cv2.boxPoints(((310, 245), (260, 150), 13)).astype(np.int32)
    cv2.fillConvexPoly(image, box, (190, 190, 190))
    cv2.polylines(image, [box], True, (230, 230, 230), 4)
    detection = detect_workpiece(image, min_area_ratio=0.02)
    assert detection is not None
    assert abs(detection.center_px[0] - 310) < 8
    assert abs(detection.center_px[1] - 245) < 8
    assert detection.area_ratio > 0.1
