from unittest.mock import patch

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.lens import LensModel
from laser_aligner.errors import CalibrationError


def _model(width: int = 320, height: int = 180) -> LensModel:
    return LensModel(
        camera_matrix=np.array(
            [[260.0, 0.0, width / 2.0], [0.0, 258.0, height / 2.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        ),
        distortion=np.array([[0.08, -0.025, 0.001, -0.002, 0.0]], dtype=np.float64),
        image_width=width,
        image_height=height,
        rms_error=0.2,
        mean_reprojection_error=0.15,
        images_used=12,
        created_at=1.0,
    )


def test_undistort_reuses_precomputed_maps_for_a_resolution() -> None:
    model = _model()
    image = np.arange(180 * 320 * 3, dtype=np.uint8).reshape(180, 320, 3)

    with patch(
        "laser_aligner.calibration.lens.cv2.initUndistortRectifyMap",
        wraps=cv2.initUndistortRectifyMap,
    ) as initialize:
        first = model.undistort(image)
        second = model.undistort(image)

    assert initialize.call_count == 1
    assert np.array_equal(first, second)


def test_cached_remap_matches_opencv_undistort() -> None:
    model = _model()
    image = np.zeros((180, 320, 3), dtype=np.uint8)
    image[30:150:8, :, :] = 255
    image[:, 20:300:10, 1] = 180

    expected = cv2.undistort(image, model.camera_matrix, model.distortion)
    actual = model.undistort(image)

    difference = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
    assert float(np.mean(difference)) < 0.05
    assert int(np.max(difference)) <= 7


def test_uniformly_scaled_resolution_is_supported_and_cached_separately() -> None:
    model = _model()
    image = np.zeros((360, 640, 3), dtype=np.uint8)

    output = model.undistort(image)

    assert output.shape == image.shape
    assert (640, 360) in model._map_cache


def test_aspect_ratio_change_is_rejected_instead_of_silently_warped() -> None:
    model = _model()

    with pytest.raises(CalibrationError, match="aspect ratio"):
        model.undistort(np.zeros((240, 320, 3), dtype=np.uint8))


def test_distort_points_inverts_opencv_undistorted_pixel_coordinates() -> None:
    model = _model()
    raw_points = np.asarray(
        [
            [0.0, 0.0],
            [319.0, 0.0],
            [319.0, 179.0],
            [0.0, 179.0],
            [160.0, 90.0],
            [50.0, 130.0],
            [290.0, 50.0],
        ],
        dtype=np.float64,
    )
    undistorted = cv2.undistortPoints(
        raw_points.reshape(-1, 1, 2),
        model.camera_matrix,
        model.distortion,
        P=model.camera_matrix,
    ).reshape(raw_points.shape)

    recovered = model.distort_points(undistorted.reshape(1, -1, 2))

    assert recovered.shape == (1, len(raw_points), 2)
    assert np.allclose(recovered.reshape(-1, 2), raw_points, atol=1e-5, rtol=0.0)


@pytest.mark.parametrize(
    "points",
    (
        np.empty((0, 2), dtype=np.float64),
        np.asarray([[np.nan, 0.0]], dtype=np.float64),
    ),
)
def test_distort_points_rejects_empty_or_nonfinite_inputs(points: np.ndarray) -> None:
    with pytest.raises(CalibrationError, match="finite"):
        _model().distort_points(points)


def test_distort_points_rejects_nonfinite_projection_results() -> None:
    with pytest.raises(CalibrationError, match="produced non-finite"):
        _model().distort_points(np.asarray([[1e100, 1e100]], dtype=np.float64))
