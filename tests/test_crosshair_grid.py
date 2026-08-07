import cv2
import numpy as np
import pytest

from laser_aligner.vision.fiducials import detect_crosshair_grid, detect_crosshairs_near


def test_detect_crosshair_grid_on_synthetic_plate():
    image = np.full((900, 1200, 3), 190, dtype=np.uint8)
    plate = np.full((700, 700, 3), 225, dtype=np.uint8)
    coordinates = [32, 191, 350, 509, 668]
    for y in coordinates:
        for x in coordinates:
            cv2.line(plate, (x - 14, y), (x + 14, y), (35, 35, 35), 3)
            cv2.line(plate, (x, y - 14), (x, y + 14), (35, 35, 35), 3)
            cv2.circle(plate, (x, y), 8, (35, 35, 35), 2)
    cv2.rectangle(plate, (1, 1), (698, 698), (45, 45, 45), 4)
    source = np.float32([[0, 0], [699, 0], [699, 699], [0, 699]])
    destination = np.float32([[230, 90], [1000, 130], [940, 830], [160, 790]])
    transform = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(plate, transform, (1200, 900), borderValue=(190, 190, 190))
    mask = cv2.warpPerspective(np.full((700, 700), 255, np.uint8), transform, (1200, 900))
    image[mask > 0] = warped[mask > 0]
    result = detect_crosshair_grid(image)
    assert result['detected'] is True
    assert len(result['points']) == 25
    assert result['points'][0]['machine_x'] == 10.0
    assert result['points'][0]['machine_y'] == 10.0
    assert result['points'][-1]['machine_x'] == 210.0
    assert result['points'][-1]['machine_y'] == 210.0


def test_detect_crosshairs_near_accepts_sparse_fine_registration_targets():
    image = np.full((600, 800, 3), 220, dtype=np.uint8)
    centers = (
        (100, 100),
        (700, 100),
        (300, 250),
        (700, 250),
        (100, 400),
        (500, 400),
        (100, 520),
        (700, 520),
    )
    expected = []
    for index, (x, y) in enumerate(centers, start=1):
        cv2.line(image, (x - 15, y), (x + 15, y), (25, 25, 25), 3)
        cv2.line(image, (x, y - 15), (x, y + 15), (25, 25, 25), 3)
        expected.append(
            {
                "id": index,
                "image_x": x - 4,
                "image_y": y + 3,
                "machine_x": float(index * 10),
                "machine_y": float(index * 12),
            }
        )

    result = detect_crosshairs_near(image, expected, search_radius_px=25)

    assert result["detected"] is True
    assert len(result["points"]) == 8
    for point, center in zip(result["points"], centers, strict=True):
        assert point["image_x"] == pytest.approx(center[0], abs=1.5)
        assert point["image_y"] == pytest.approx(center[1], abs=1.5)
