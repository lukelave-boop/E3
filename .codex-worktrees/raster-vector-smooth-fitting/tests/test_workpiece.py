import cv2
import numpy as np
import pytest

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


@pytest.mark.parametrize(
    "image",
    (
        np.zeros((20, 20, 3), dtype=np.float32),
        np.zeros((20, 20, 2), dtype=np.uint8),
    ),
)
def test_detect_workpiece_rejects_malformed_images(image: np.ndarray) -> None:
    with pytest.raises(ValueError, match="uint8 grayscale or BGR"):
        detect_workpiece(image)


@pytest.mark.parametrize("minimum", [float("nan"), 0.0, 0.92, True])
def test_detect_workpiece_rejects_invalid_area_ratio(minimum: object) -> None:
    with pytest.raises(ValueError, match="area ratio"):
        detect_workpiece(
            np.zeros((20, 20, 3), dtype=np.uint8),
            min_area_ratio=minimum,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("low, high", [(-1, 100), (100, 100), (100, 256), (True, 100)])
def test_detect_workpiece_rejects_invalid_canny_thresholds(
    low: object,
    high: object,
) -> None:
    with pytest.raises(ValueError, match="Canny thresholds"):
        detect_workpiece(
            np.zeros((20, 20, 3), dtype=np.uint8),
            canny_low=low,  # type: ignore[arg-type]
            canny_high=high,  # type: ignore[arg-type]
        )
