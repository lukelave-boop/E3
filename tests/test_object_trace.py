from __future__ import annotations

import cv2
import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.vision.object_trace import (
    TraceOptions,
    _long_axis_rect,
    _rounded_fit,
    _rounded_mask,
    auto_target_hue,
    detect_objects,
    sample_color,
)


def _label_scene(*, obscure: bool = True) -> np.ndarray:
    height = width = 760
    x_gradient = np.linspace(0, 28, width, dtype=np.float32)[None, :]
    y_gradient = np.linspace(12, -18, height, dtype=np.float32)[:, None]
    base = np.full((height, width, 3), 205, dtype=np.float32)
    base[:, :, 0] += x_gradient + y_gradient
    base[:, :, 1] += x_gradient * 0.4 + y_gradient * 0.3
    base[:, :, 2] += x_gradient * 0.2
    image = np.clip(base, 0, 255).astype(np.uint8)

    x_positions = (42, 418)
    y_positions = tuple(18 + row * 91 for row in range(8))
    for row, y in enumerate(y_positions):
        for column, x in enumerate(x_positions):
            # Vary brightness substantially to exercise hue-based segmentation.
            red = int(145 + row * 13 + column * 3)
            red = min(red, 245)
            color = (35, 42, red)
            cv2.rectangle(image, (x + 14, y), (x + 286, y + 70), color, -1)
            cv2.rectangle(image, (x, y + 14), (x + 300, y + 56), color, -1)
            for center in (
                (x + 14, y + 14),
                (x + 286, y + 14),
                (x + 286, y + 56),
                (x + 14, y + 56),
            ):
                cv2.circle(image, center, 14, color, -1)
            cv2.putText(
                image,
                "WARNING",
                (x + 92, y + 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (25, 25, 35),
                1,
                cv2.LINE_AA,
            )
            cv2.line(image, (x + 38, y + 38), (x + 258, y + 38), (40, 35, 45), 1)
            cv2.line(image, (x + 52, y + 49), (x + 244, y + 49), (40, 35, 45), 1)

    if obscure:
        cv2.rectangle(image, (565, 0), (759, 166), (28, 31, 34), -1)
        cv2.rectangle(image, (548, 20), (640, 150), (48, 52, 55), -1)
    return image


def test_auto_hue_finds_red_labels():
    hue = auto_target_hue(_label_scene(), min_saturation=40)
    assert hue is not None
    assert hue <= 5 or hue >= 175


def test_color_sample_returns_red_hue():
    image = _label_scene(obscure=False)
    sample = sample_color(image, 150, 45)
    assert sample["saturation"] > 100
    assert sample["hue"] <= 5 or sample["hue"] >= 175
    assert sample["rgb"][0] > sample["rgb"][1]


def _neutral_wood_scene() -> tuple[np.ndarray, tuple[int, int, int]]:
    height, width = 600, 900
    rng = np.random.default_rng(20260806)
    x_gradient = np.linspace(-10, 12, width, dtype=np.float32)[None, :]
    y_gradient = np.linspace(8, -7, height, dtype=np.float32)[:, None]
    image = np.empty((height, width, 3), dtype=np.float32)
    image[:, :, 0] = 174 + x_gradient * 0.35 + y_gradient * 0.2
    image[:, :, 1] = 194 + x_gradient * 0.55 + y_gradient * 0.35
    image[:, :, 2] = 218 + x_gradient + y_gradient
    image += rng.normal(0.0, 2.2, image.shape)
    for x in range(25, width, 43):
        cv2.line(image, (x, 0), (x + 5, height - 1), (160, 183, 211), 2)
    image = np.clip(image, 0, 255).astype(np.uint8)

    x, y, object_width, object_height, radius = 250, 220, 360, 100, 14
    fill = (142, 148, 153)
    cv2.rectangle(
        image,
        (x + radius, y),
        (x + object_width - radius, y + object_height),
        fill,
        -1,
    )
    cv2.rectangle(
        image,
        (x, y + radius),
        (x + object_width, y + object_height - radius),
        fill,
        -1,
    )
    for center in (
        (x + radius, y + radius),
        (x + object_width - radius, y + radius),
        (x + object_width - radius, y + object_height - radius),
        (x + radius, y + object_height - radius),
    ):
        cv2.circle(image, center, radius, fill, -1)
    # Real coloring is neither flat nor clean; retain enough internal texture
    # to ensure the detector does not depend on a synthetic uniform fill.
    for offset in range(12, object_width - 8, 31):
        cv2.line(
            image,
            (x + offset, y + 8),
            (x + offset - 5, y + object_height - 8),
            (135, 141, 147),
            1,
        )
    return image, fill


def test_sampled_neutral_color_detects_object_on_textured_wood():
    image, target_bgr = _neutral_wood_scene()
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_hue=0,
            target_bgr=target_bgr,
            min_saturation=45,
            min_area_mm2=100,
            min_width_mm=20,
            min_height_mm=10,
            regular_grid=False,
        ),
        WorkArea(0.0, 225.0, 0.0, 150.0),
        4.0,
    )

    assert result.direct_count == 1
    detection = result.detections[0]
    assert detection.width_mm == pytest.approx(90.0, abs=1.5)
    assert detection.height_mm == pytest.approx(25.0, abs=1.5)


def test_sampled_neutral_color_still_respects_maximum_area_rejection():
    image, target_bgr = _neutral_wood_scene()
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_hue=0,
            target_bgr=target_bgr,
            min_area_mm2=100,
            max_area_mm2=1_000,
            min_width_mm=20,
            min_height_mm=10,
            regular_grid=False,
        ),
        WorkArea(0.0, 225.0, 0.0, 150.0),
        4.0,
    )

    assert not result.detected
    assert result.direct_count == 0


def test_contrast_region_detects_fill_instead_of_expanded_edge_halo():
    image, _ = _neutral_wood_scene()
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            min_area_mm2=100,
            min_width_mm=20,
            min_height_mm=10,
            regular_grid=False,
        ),
        WorkArea(0.0, 225.0, 0.0, 150.0),
        4.0,
    )

    close_matches = [
        item
        for item in result.detections
        if abs(item.width_mm - 90.0) <= 2.0
        and abs(item.height_mm - 25.0) <= 2.0
    ]
    assert close_matches


def test_repeated_label_grid_detects_and_infers_occluded_objects():
    options = TraceOptions(
        detection_mode="color",
        min_saturation=35,
        min_area_mm2=20,
        min_width_mm=20,
        min_height_mm=8,
        regular_grid=True,
        infer_missing=True,
        output_mode="rounded",
    )
    result = detect_objects(
        _label_scene(),
        options,
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )

    assert result.detected
    assert result.grid is not None
    assert result.grid["rows"] == 8
    assert result.grid["columns"] == 2
    assert result.grid["normalized"] is True
    assert len(result.detections) == 16
    assert result.direct_count >= 12
    assert result.inferred_count >= 2
    assert all(not item.selected_default for item in result.detections if item.source == "inferred")
    assert all(item.vector_contour_mm for item in result.detections)
    assert all(
        item.vector_contour_mm == item.contour_mm
        for item in result.detections
        if item.source == "inferred"
    )
    assert len({item.width_mm for item in result.detections}) == 1
    assert len({item.height_mm for item in result.detections}) == 1
    assert len({item.corner_radius_mm for item in result.detections}) == 1
    assert len({item.rotation_deg for item in result.detections}) == 1
    assert all(
        item.diagnostics["grid_normalized"] for item in result.detections
    )

    direct = [item for item in result.detections if item.source == "direct"]
    median_width = float(np.median([item.width_mm for item in direct]))
    median_height = float(np.median([item.height_mm for item in direct]))
    assert 72.0 <= median_width <= 78.0
    assert 16.0 <= median_height <= 20.0
    assert max(abs(item.rotation_deg) for item in direct) < 2.0


def test_repeated_grid_repairs_one_malformed_direct_cell() -> None:
    image = _label_scene(obscure=False)
    # Extend one label with same-hue material. It remains recognizable as a
    # grid member but its raw fitted width is substantially wrong.
    cv2.rectangle(image, (718, 578), (748, 620), (35, 42, 225), -1)
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_hue=0,
            min_saturation=35,
            min_area_mm2=20,
            min_width_mm=20,
            min_height_mm=8,
            regular_grid=True,
            infer_missing=True,
            normalize_grid=True,
            output_mode="rounded",
        ),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )

    assert result.grid is not None
    assert result.grid["rows"] == 8
    assert result.grid["columns"] == 2
    assert len(result.detections) == 16
    assert len({item.width_mm for item in result.detections}) == 1
    observed_widths = [
        float(item.diagnostics["observed_width_mm"])
        for item in result.detections
        if item.source == "direct"
    ]
    assert max(observed_widths) - min(observed_widths) >= 5.0


def test_border_offset_expands_fitted_output():
    image = _label_scene(obscure=False)
    base = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_hue=0,
            min_saturation=35,
            min_width_mm=20,
            min_height_mm=8,
            regular_grid=False,
            border_offset_mm=0.0,
        ),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )
    expanded = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_hue=0,
            min_saturation=35,
            min_width_mm=20,
            min_height_mm=8,
            regular_grid=False,
            border_offset_mm=1.0,
        ),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )
    assert len(base.detections) == len(expanded.detections) == 16
    base_width = float(np.median([item.width_mm for item in base.detections]))
    expanded_width = float(np.median([item.width_mm for item in expanded.detections]))
    assert abs((expanded_width - base_width) - 2.0) < 0.2


def test_rounded_fit_supports_near_capsule_corner_radii():
    image = np.full((400, 400, 3), (210, 210, 210), dtype=np.uint8)
    x, y, width, height, radius = 100, 150, 200, 80, 38
    color = (35, 55, 220)
    cv2.rectangle(
        image,
        (x + radius, y),
        (x + width - radius, y + height),
        color,
        -1,
    )
    cv2.rectangle(
        image,
        (x, y + radius),
        (x + width, y + height - radius),
        color,
        -1,
    )
    for center in (
        (x + radius, y + radius),
        (x + width - radius, y + radius),
        (x + width - radius, y + height - radius),
        (x + radius, y + height - radius),
    ):
        cv2.circle(image, center, radius, color, -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_hue=0,
            min_saturation=50,
            min_area_mm2=10,
            min_width_mm=10,
            min_height_mm=10,
            regular_grid=False,
            output_mode="rounded",
        ),
        WorkArea(0.0, 100.0, 0.0, 100.0),
        4.0,
    )

    assert result.direct_count == 1
    assert result.detections[0].corner_radius_mm == pytest.approx(9.5, abs=0.75)


@pytest.mark.parametrize(
    ("width", "height", "radius"),
    ((312, 84, 12), (314, 86, 12), (315, 87, 37)),
)
def test_rounded_fit_recovers_discrete_mask_radius_without_off_by_one(
    width: int,
    height: int,
    radius: int,
) -> None:
    mask = _rounded_mask(width, height, radius)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    assert len(contours) == 1
    rectangle = _long_axis_rect(contours[0])
    assert rectangle is not None

    fitted_radius, fitted_iou = _rounded_fit(contours[0], rectangle)

    assert fitted_radius == pytest.approx(radius)
    assert fitted_iou == pytest.approx(1.0)


def test_non_grid_mode_accepts_mixed_sizes():
    image = np.full((600, 800, 3), (210, 210, 210), dtype=np.uint8)
    for x, y, width, height in (
        (60, 80, 180, 70),
        (340, 100, 250, 90),
        (170, 330, 120, 120),
    ):
        cv2.rectangle(image, (x + 12, y), (x + width - 12, y + height), (40, 60, 220), -1)
        cv2.rectangle(image, (x, y + 12), (x + width, y + height - 12), (40, 60, 220), -1)
        for center in (
            (x + 12, y + 12),
            (x + width - 12, y + 12),
            (x + width - 12, y + height - 12),
            (x + 12, y + height - 12),
        ):
            cv2.circle(image, center, 12, (40, 60, 220), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_hue=0,
            min_saturation=50,
            min_area_mm2=10,
            min_width_mm=10,
            min_height_mm=10,
            regular_grid=False,
        ),
        WorkArea(0.0, 200.0, 0.0, 150.0),
        4.0,
    )
    assert result.direct_count == 3
    assert result.inferred_count == 0


def test_non_grid_mode_preserves_irregular_colored_silhouette():
    image = np.full((500, 500, 3), (210, 210, 210), dtype=np.uint8)
    polygon = np.array(
        [[70, 70], [280, 70], [280, 155], [190, 155], [190, 330], [70, 330]],
        dtype=np.int32,
    )
    cv2.fillPoly(image, [polygon], (35, 55, 220))
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_hue=0,
            min_saturation=50,
            min_area_mm2=20,
            min_width_mm=5,
            min_height_mm=5,
            regular_grid=False,
            output_mode="smoothed",
        ),
        WorkArea(0.0, 125.0, 0.0, 125.0),
        4.0,
    )
    assert result.direct_count == 1
    detection = result.detections[0]
    assert detection.shape == "contour"
    assert len(detection.contour_mm) >= 6
    assert detection.vector_contour_mm == detection.contour_mm
