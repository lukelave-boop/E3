from __future__ import annotations

import cv2
import numpy as np

from laser_aligner.config import WorkArea
from laser_aligner.vision.object_trace import (
    TraceOptions,
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
    assert len(result.detections) == 16
    assert result.direct_count >= 12
    assert result.inferred_count >= 2
    assert all(not item.selected_default for item in result.detections if item.source == "inferred")

    direct = [item for item in result.detections if item.source == "direct"]
    median_width = float(np.median([item.width_mm for item in direct]))
    median_height = float(np.median([item.height_mm for item in direct]))
    assert 72.0 <= median_width <= 78.0
    assert 16.0 <= median_height <= 20.0
    assert max(abs(item.rotation_deg) for item in direct) < 2.0


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
