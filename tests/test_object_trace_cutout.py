from __future__ import annotations

import cv2
import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.vision.object_trace import (
    TraceOptions,
    detect_objects,
    detect_seeded_cutouts,
)


def _area(image: np.ndarray, pixels_per_mm: float = 4.0) -> WorkArea:
    return WorkArea(
        0.0,
        image.shape[1] / pixels_per_mm,
        0.0,
        image.shape[0] / pixels_per_mm,
    )


def _machine_seed(
    image: np.ndarray,
    x_px: float,
    y_px: float,
    pixels_per_mm: float = 4.0,
) -> tuple[float, float]:
    return x_px / pixels_per_mm, (image.shape[0] - y_px) / pixels_per_mm


def _options(*, output_mode: str = "exact") -> TraceOptions:
    return TraceOptions(detection_mode="cutout", output_mode=output_mode)


def _distracting_scene() -> np.ndarray:
    height, width = 320, 560
    gradient = np.linspace(205, 242, width, dtype=np.uint8)
    image = np.repeat(gradient[None, :, None], height, axis=0)
    image = np.repeat(image, 3, axis=2)
    first = np.asarray(
        [[45, 75], [175, 55], [205, 130], [165, 245], [55, 230]],
        dtype=np.int32,
    )
    second = np.asarray(
        [[330, 70], [455, 82], [500, 155], [440, 250], [325, 225]],
        dtype=np.int32,
    )
    cv2.fillPoly(image, [first], (36, 40, 44))
    cv2.fillPoly(image, [second], (48, 44, 38))
    for y, text in ((38, "E3 CUT"), (292, "SERIAL 8274"), (165, "NOT THIS")):
        cv2.putText(
            image,
            text,
            (215 if y != 292 else 110, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (18, 18, 18),
            2,
            cv2.LINE_AA,
        )
    return image


def test_cutout_clicks_select_only_desired_regions_amid_distracting_text() -> None:
    image = _distracting_scene()
    first_seed = _machine_seed(image, 105, 150)
    second_seed = _machine_seed(image, 405, 160)

    capture = detect_objects(image, _options(), _area(image), 4.0)
    assert capture.detections == []
    assert "Unrelated contrast is not selected" in capture.message

    first = detect_seeded_cutouts(
        image, [first_seed], _options(), _area(image), 4.0, fit_native=False
    )
    second = detect_seeded_cutouts(
        image, [second_seed], _options(), _area(image), 4.0, fit_native=False
    )
    both = detect_seeded_cutouts(
        image,
        [first_seed, second_seed],
        _options(),
        _area(image),
        4.0,
        fit_native=False,
    )

    assert len(first.detections) == 1
    assert len(second.detections) == 1
    assert len(both.detections) == 2
    assert first.detections[0].center_mm[0] < 60.0
    assert second.detections[0].center_mm[0] > 75.0
    assert all(item.source == "seeded_cutout" for item in both.detections)
    assert all(item.selected_default for item in both.detections)
    assert sum(item.area_mm2 for item in both.detections) > 1_500.0
    # None of the disconnected high-contrast lettering is globally promoted.
    assert max(len(item.raw_contours_mm) for item in both.detections) == 1


def test_cutout_mixed_straight_and_curved_shape_uses_native_lines_and_cubics() -> None:
    image = np.full((300, 500, 3), 230, dtype=np.uint8)
    cv2.rectangle(image, (40, 70), (140, 220), (30, 30, 30), -1)
    cv2.circle(image, (140, 145), 75, (30, 30, 30), -1)
    cv2.rectangle(image, (40, 120), (90, 170), (230, 230, 230), -1)

    result = detect_seeded_cutouts(
        image,
        [_machine_seed(image, 100, 180)],
        _options(),
        _area(image),
        4.0,
    )

    detection = result.detections[0]
    sequence = detection.diagnostics["native_sequences"][0]
    assert detection.native_verified is True
    assert detection.native_path is not None
    assert "L" in sequence
    assert "C" in sequence
    assert detection.diagnostics["raw_contour_point_count"] > 1_000
    assert detection.diagnostics["fit_input_point_count"] < 50
    assert detection.diagnostics["fitted_segment_count"] < 40


@pytest.mark.parametrize(
    ("axes", "expected_shape"),
    [((68, 68), "circle"), ((88, 54), "ellipse")],
)
def test_cutout_preserves_analytic_circle_and_ellipse_fast_paths(
    axes: tuple[int, int],
    expected_shape: str,
) -> None:
    image = np.full((280, 420, 3), 225, dtype=np.uint8)
    cv2.ellipse(image, (205, 140), axes, 27, 0, 360, (32, 32, 32), -1)

    result = detect_seeded_cutouts(
        image,
        [_machine_seed(image, 205, 140)],
        _options(output_mode="rounded"),
        _area(image),
        4.0,
    )

    detection = result.detections[0]
    assert detection.shape == expected_shape
    assert detection.native_verified is True
    assert detection.diagnostics["native_fit_status"] == "analytic"


def test_cutout_retains_internal_hole_and_stencil_island_hierarchy() -> None:
    image = np.full((340, 460, 3), 230, dtype=np.uint8)
    outer = np.asarray(
        [[55, 55], [390, 70], [410, 275], [70, 290]], dtype=np.int32
    )
    cv2.fillPoly(image, [outer], (34, 34, 34))
    cv2.ellipse(image, (230, 170), (105, 68), 18, 0, 360, (230, 230, 230), -1)
    cv2.ellipse(image, (230, 170), (36, 22), 18, 0, 360, (34, 34, 34), -1)

    result = detect_seeded_cutouts(
        image,
        [_machine_seed(image, 100, 120)],
        _options(),
        _area(image),
        4.0,
    )

    detection = result.detections[0]
    assert len(detection.raw_contours_mm) == 3
    assert detection.native_path is not None
    assert len(detection.native_path["subpaths"]) == 3
    assert detection.native_path["fill_rule"] == "evenodd"
    assert detection.diagnostics["contour_parents"] == [None, 0, 1]
    assert detection.diagnostics["contour_depths"] == [0, 1, 2]


def test_cutout_preserves_analytic_washer_as_compound_native_cubics() -> None:
    image = np.full((300, 420, 3), 225, dtype=np.uint8)
    cv2.circle(image, (210, 150), 82, (30, 30, 30), -1)
    cv2.circle(image, (210, 150), 31, (225, 225, 225), -1)

    result = detect_seeded_cutouts(
        image,
        [_machine_seed(image, 210, 205)],
        _options(output_mode="rounded"),
        _area(image),
        4.0,
    )

    detection = result.detections[0]
    assert detection.shape == "washer"
    assert detection.native_verified is True
    assert detection.native_path is not None
    assert len(detection.native_path["subpaths"]) == 2
    assert detection.diagnostics["native_sequences"] == ["CCCC", "CCCC"]


def test_cutout_handles_rotated_shape_without_grid_normalization() -> None:
    image = np.full((300, 440, 3), 220, dtype=np.uint8)
    box = cv2.boxPoints(((220.0, 150.0), (185.0, 92.0), 31.0)).astype(np.int32)
    cv2.fillPoly(image, [box], (28, 28, 28))

    result = detect_seeded_cutouts(
        image,
        [_machine_seed(image, 220, 150)],
        _options(output_mode="rounded"),
        _area(image),
        4.0,
    )

    detection = result.detections[0]
    assert detection.shape == "rounded_rectangle"
    assert abs(detection.rotation_deg) == pytest.approx(31.0, abs=2.0)
    assert result.grid is None
    assert result.inferred_count == 0


def test_cutout_tolerates_uneven_illumination_and_interior_camera_noise() -> None:
    rng = np.random.default_rng(8274)
    height, width = 300, 500
    background = np.linspace(175, 245, width, dtype=np.float32)
    image = np.repeat(background[None, :, None], height, axis=0)
    image = np.repeat(image, 3, axis=2)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.ellipse(mask, (250, 150), (125, 78), -19, 0, 360, 255, -1)
    object_gradient = np.linspace(38, 92, width, dtype=np.float32)[None, :]
    noise = rng.normal(0.0, 3.0, (height, width))
    values = np.clip(object_gradient + noise, 0, 255)
    for channel in range(3):
        image[:, :, channel][mask > 0] = values[mask > 0]
    image = image.astype(np.uint8)

    result = detect_seeded_cutouts(
        image,
        [_machine_seed(image, 250, 150)],
        _options(output_mode="rounded"),
        _area(image),
        4.0,
        fit_native=False,
    )

    detection = result.detections[0]
    assert detection.width_mm == pytest.approx(62.5, abs=2.0)
    assert detection.height_mm == pytest.approx(39.0, abs=2.0)
    assert detection.diagnostics["boundary_contrast"] > 35.0


def _scaled_physical_scene(pixels_per_mm: float) -> tuple[np.ndarray, tuple[float, float]]:
    width_mm, height_mm = 110.0, 70.0
    image = np.full(
        (round(height_mm * pixels_per_mm), round(width_mm * pixels_per_mm), 3),
        225,
        dtype=np.uint8,
    )
    center = (round(52.0 * pixels_per_mm), round(35.0 * pixels_per_mm))
    axes = (round(22.0 * pixels_per_mm), round(13.0 * pixels_per_mm))
    cv2.ellipse(image, center, axes, 23, 0, 360, (32, 32, 32), -1)
    return image, (52.0, 35.0)


def test_cutout_physical_scale_tracks_corrected_camera_resolution() -> None:
    detections = []
    for pixels_per_mm in (2.0, 8.0):
        image, center_mm = _scaled_physical_scene(pixels_per_mm)
        result = detect_seeded_cutouts(
            image,
            [center_mm],
            _options(),
            _area(image, pixels_per_mm),
            pixels_per_mm,
        )
        detections.append(result.detections[0])

    low, high = detections
    assert low.width_mm == pytest.approx(high.width_mm, abs=0.8)
    assert low.height_mm == pytest.approx(high.height_mm, abs=0.8)
    assert low.diagnostics["physical_pixel_pitch_mm"] == pytest.approx(0.5)
    assert high.diagnostics["physical_pixel_pitch_mm"] == pytest.approx(0.125)
    assert low.diagnostics["native_fitting_tolerance_mm"] >= 0.5
    assert high.diagnostics["native_fitting_tolerance_mm"] >= 0.125


def test_cutout_rejects_outside_seed_and_unselects_output_overrun() -> None:
    image = np.full((240, 360, 3), 225, dtype=np.uint8)
    cv2.rectangle(image, (12, 55), (145, 190), (28, 28, 28), -1)
    area = _area(image)

    with pytest.raises(ValueError, match="outside the corrected camera image"):
        detect_seeded_cutouts(
            image,
            [(-1.0, 10.0)],
            _options(),
            area,
            4.0,
        )

    result = detect_seeded_cutouts(
        image,
        [_machine_seed(image, 80, 120)],
        _options(),
        area,
        4.0,
        output_work_area=WorkArea(10.0, area.x_max, 0.0, area.y_max),
    )
    detection = result.detections[0]
    assert detection.selected_default is False
    assert detection.diagnostics["within_work_area"] is False
    assert detection.diagnostics["work_area_overruns_mm"]["left"] > 0.0
