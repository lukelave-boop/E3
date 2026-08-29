from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.vision.object_trace import (
    TraceOptions,
    _grid_cell_review_evidence,
    _long_axis_rect,
    _machine_geometry,
    _rounded_fit,
    _rounded_mask,
    _straight_edge_angle,
    _straight_edge_center,
    auto_target_hue,
    detect_objects,
    sample_color,
)


def test_grid_review_flags_damage_and_textured_open_cells_conservatively() -> None:
    image = np.full((240, 360, 3), 105, dtype=np.uint8)
    centers = [(70, 70), (180, 70), (290, 70), (70, 170), (180, 170), (290, 170)]
    candidates = []
    for index, center in enumerate(centers):
        candidates.append(
            {
                "grid_row": index // 3,
                "grid_column": index % 3,
                "center_px": np.asarray(center, dtype=np.float64),
                "width_px": 80.0,
                "height_px": 42.0,
                "angle_image_deg": 0.0,
                "observed_width_mm": 20.0,
                "observed_height_mm": 10.5,
                "observed_rotation_deg": 3.2 if index == 1 else 0.0,
                "repaired_center_axes": [],
            }
        )
    checker = np.random.default_rng(7).integers(35, 220, size=(50, 88), dtype=np.uint8)
    for center in centers[-2:]:
        x, y = center
        image[y - 25 : y + 25, x - 44 : x + 44] = checker[:, :, None]

    _grid_cell_review_evidence(
        image,
        candidates,
        {"rotation_deg": 0.0, "cell_width_mm": 20.0, "cell_height_mm": 10.5},
    )

    assert candidates[1]["damage_suspected"] is True
    assert "rotation differs" in candidates[1]["damage_reasons"][0]
    assert all(item["damage_suspected"] is False for item in candidates[2:])
    assert [item.get("likely_open_cell", False) for item in candidates] == [
        False, False, False, False, True, True
    ]


def test_grid_review_uses_bound_honeycomb_background_to_reject_open_cells() -> None:
    background = np.full((240, 360, 3), 45, dtype=np.uint8)
    current = np.full_like(background, 225)
    centers = [(70, 70), (180, 70), (290, 70), (70, 170), (180, 170), (290, 170)]
    candidates = []
    for index, center in enumerate(centers):
        candidates.append(
            {
                "grid_row": index // 3,
                "grid_column": index % 3,
                "center_px": np.asarray(center, dtype=np.float64),
                "width_px": 80.0,
                "height_px": 42.0,
                "angle_image_deg": 0.0,
                "observed_width_mm": 20.0,
                "observed_height_mm": 10.5,
                "observed_rotation_deg": 0.0,
                "repaired_center_axes": [],
            }
        )
    for index in (0, 5):
        x, y = centers[index]
        current[y - 25 : y + 25, x - 44 : x + 44] = background[
            y - 25 : y + 25,
            x - 44 : x + 44,
        ]

    _grid_cell_review_evidence(
        current,
        candidates,
        {"rotation_deg": 0.0, "cell_width_mm": 20.0, "cell_height_mm": 10.5},
        background,
    )

    assert [item["likely_open_cell"] for item in candidates] == [
        True,
        False,
        False,
        False,
        False,
        True,
    ]
    assert candidates[0]["open_cell_evidence"] == "accepted_honeycomb_background"
    assert candidates[1]["background_match_fraction"] == pytest.approx(0.0)


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


def _partial_grid_scene(
    *,
    evidence: bool = True,
    distracting_text: bool = False,
) -> tuple[np.ndarray, np.ndarray, tuple[float, float]]:
    """A 2 × 4 label grid with one deliberately absent color-mask cell."""
    image = np.full((520, 760, 3), 225, dtype=np.uint8)
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    centers = [
        (160 + column * 390, 95 + row * 115)
        for row in range(4)
        for column in range(2)
    ]
    missing = (1, 1)
    for index, center in enumerate(centers):
        row, column = divmod(index, 2)
        label_mask = np.zeros_like(mask)
        cv2.rectangle(
            label_mask,
            (center[0] - 66, center[1] - 35),
            (center[0] + 66, center[1] + 35),
            255,
            -1,
        )
        cv2.rectangle(
            label_mask,
            (center[0] - 78, center[1] - 23),
            (center[0] + 78, center[1] + 23),
            255,
            -1,
        )
        for corner in ((-66, -23), (66, -23), (66, 23), (-66, 23)):
            cv2.circle(label_mask, (center[0] + corner[0], center[1] + corner[1]), 12, 255, -1)
        if (row, column) != missing:
            image[label_mask > 0] = (42, 42, 165)
            mask[label_mask > 0] = 255
        elif evidence:
            image[label_mask > 0] = (42, 42, 165)
            # Simulated glare removes the upper half, but leaves both lower
            # side runs, the lower corners, and the bottom edge visible.
            image[center[1] - 38:center[1] - 2, center[0] - 92:center[0] + 93] = 225
        elif distracting_text:
            cv2.putText(
                image, "TEXT", (center[0] - 42, center[1] + 6),
                cv2.FONT_HERSHEY_SIMPLEX, .8, (45, 45, 45), 2, cv2.LINE_AA,
            )
    return image, mask, centers[3]


def _neutral_dark_label_seam_scene(
    *,
    missing: set[tuple[int, int]] | None = None,
    alternating_y_offset_px: int = 0,
    internal_highlight_height_mm: float = 0.0,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Neutral labels whose bright inter-row seams form a false clean grid."""

    pixels_per_mm = 4.0
    size_px = 880
    y, x = np.indices((size_px, size_px))
    base = (
        225.0
        + 18.0 * x / (size_px - 1)
        - 24.0 * y / (size_px - 1)
        - 12.0
        * ((x - size_px * 0.55) ** 2 + (y - size_px * 0.45) ** 2)
        / (size_px * size_px)
    )
    image = np.stack((base - 3.0, base, base + 3.0), axis=2)

    expected_centers: list[tuple[float, float]] = []
    x_centers = (64.05, 155.95)
    y_centers = tuple(199.25 - row * 25.5 for row in range(8))
    for row, center_y_mm in enumerate(y_centers):
        for column, center_x_mm in enumerate(x_centers):
            if missing and (row, column) in missing:
                continue
            center_x_px = int(center_x_mm * pixels_per_mm)
            center_y_px = int((220.0 - center_y_mm) * pixels_per_mm)
            center_y_px += alternating_y_offset_px if column == 0 else -alternating_y_offset_px
            width_px = int(81.7 * pixels_per_mm)
            height_px = int(21.5 * pixels_per_mm)
            radius_px = 14

            mask = np.zeros((size_px, size_px), dtype=np.uint8)
            cv2.rectangle(
                mask,
                (
                    center_x_px - width_px // 2 + radius_px,
                    center_y_px - height_px // 2,
                ),
                (
                    center_x_px + width_px // 2 - radius_px,
                    center_y_px + height_px // 2,
                ),
                255,
                -1,
            )
            cv2.rectangle(
                mask,
                (
                    center_x_px - width_px // 2,
                    center_y_px - height_px // 2 + radius_px,
                ),
                (
                    center_x_px + width_px // 2,
                    center_y_px + height_px // 2 - radius_px,
                ),
                255,
                -1,
            )
            for corner in (
                (
                    center_x_px - width_px // 2 + radius_px,
                    center_y_px - height_px // 2 + radius_px,
                ),
                (
                    center_x_px + width_px // 2 - radius_px,
                    center_y_px - height_px // 2 + radius_px,
                ),
                (
                    center_x_px + width_px // 2 - radius_px,
                    center_y_px + height_px // 2 - radius_px,
                ),
                (
                    center_x_px - width_px // 2 + radius_px,
                    center_y_px + height_px // 2 - radius_px,
                ),
            ):
                cv2.circle(mask, corner, radius_px, 255, -1)

            value = 62 + row * 4 + column * 2
            image[mask > 0] = (value - 2, value, value + 3)
            cv2.putText(
                image,
                "WARNING",
                (center_x_px - 55, center_y_px - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (35, 36, 38),
                1,
                cv2.LINE_AA,
            )
            for offset_y in (2, 13):
                cv2.line(
                    image,
                    (center_x_px - 85, center_y_px + offset_y),
                    (center_x_px + 85, center_y_px + offset_y),
                    (42, 43, 46),
                    1,
                    cv2.LINE_AA,
                )
            if internal_highlight_height_mm > 0.0:
                half_height = max(
                    1,
                    int(round(internal_highlight_height_mm * pixels_per_mm / 2.0)),
                )
                cv2.rectangle(
                    image,
                    (center_x_px - 136, center_y_px - half_height),
                    (center_x_px + 136, center_y_px + half_height),
                    (220, 223, 226),
                    -1,
                )
            expected_centers.append(
                (
                    center_x_mm,
                    center_y_mm
                    - (
                        alternating_y_offset_px
                        if column == 0
                        else -alternating_y_offset_px
                    )
                    / pixels_per_mm,
                )
            )

    rng = np.random.default_rng(2)
    image = np.clip(
        image + rng.normal(0.0, 2.0, image.shape),
        0,
        255,
    ).astype(np.uint8)
    return cv2.GaussianBlur(image, (5, 5), 0.9), expected_centers


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


def _seam_scene_options() -> TraceOptions:
    return TraceOptions(
        detection_mode="auto",
        hue_tolerance=14.0,
        min_saturation=45,
        min_area_mm2=30.0,
        max_area_mm2=20_000.0,
        min_width_mm=4.0,
        min_height_mm=3.0,
        regular_grid=True,
        infer_missing=True,
        normalize_grid=True,
        snap_grid_cells=False,
        output_mode="rounded",
    )


def test_auto_neutral_grid_prefers_full_bodies_over_bright_seams() -> None:
    image, expected_centers = _neutral_dark_label_seam_scene()
    result = detect_objects(
        image,
        _seam_scene_options(),
        WorkArea(0.0, 220.0, 0.0, 220.0),
        4.0,
    )

    assert result.grid is not None
    assert result.grid["rows"] == 8
    assert result.grid["columns"] == 2
    assert result.grid["observed_cells"] == 16
    assert result.grid["mask_source"] in {
        "global_dark",
        "clahe_dark",
        "closed_outline",
    }
    assert result.direct_count == 16
    assert result.inferred_count == 0
    assert len(result.detections) == 16
    assert all(item.height_mm > 14.0 for item in result.detections)
    assert np.median([item.width_mm for item in result.detections]) == pytest.approx(
        81.5,
        abs=0.75,
    )
    assert np.median([item.height_mm for item in result.detections]) == pytest.approx(
        21.5,
        abs=0.5,
    )
    observed_centers = [item.center_mm for item in result.detections]
    assert len(observed_centers) == len(expected_centers)
    for observed, expected in zip(observed_centers, expected_centers, strict=True):
        assert math.dist(observed, expected) <= 0.75


def test_auto_neutral_non_grid_prefers_full_bodies_over_internal_bands() -> None:
    image, _ = _neutral_dark_label_seam_scene()
    options = _seam_scene_options()
    options.regular_grid = False
    result = detect_objects(
        image,
        options,
        WorkArea(0.0, 220.0, 0.0, 220.0),
        4.0,
    )

    assert result.grid is None
    assert result.direct_count == 16
    assert result.inferred_count == 0
    assert all(item.height_mm > 14.0 for item in result.detections)
    assert np.median([item.width_mm for item in result.detections]) == pytest.approx(
        81.5,
        abs=0.75,
    )
    assert np.median([item.height_mm for item in result.detections]) == pytest.approx(
        21.5,
        abs=0.5,
    )


@pytest.mark.parametrize("regular_grid", [False, True], ids=["without-grid", "with-grid"])
def test_auto_prefers_full_bodies_over_opposite_polarity_internal_highlights(
    regular_grid: bool,
) -> None:
    image, _ = _neutral_dark_label_seam_scene(
        internal_highlight_height_mm=3.8,
    )
    options = _seam_scene_options()
    options.regular_grid = regular_grid
    result = detect_objects(
        image,
        options,
        WorkArea(0.0, 220.0, 0.0, 220.0),
        4.0,
    )

    assert result.direct_count == 16
    assert all(item.height_mm > 14.0 for item in result.detections)
    assert np.median([item.width_mm for item in result.detections]) == pytest.approx(
        81.5,
        abs=0.75,
    )
    if regular_grid:
        assert result.grid is not None
        assert (result.grid["columns"], result.grid["rows"]) == (2, 8)
    else:
        assert result.grid is None


def test_auto_neutral_grid_infers_only_the_missing_full_bodies() -> None:
    image, _ = _neutral_dark_label_seam_scene(missing={(0, 0), (1, 0)})
    result = detect_objects(
        image,
        _seam_scene_options(),
        WorkArea(0.0, 220.0, 0.0, 220.0),
        4.0,
    )

    assert result.grid is not None
    assert (result.grid["columns"], result.grid["rows"]) == (2, 8)
    assert result.grid["observed_cells"] == 14
    assert result.grid["missing_cells_total"] == 2
    assert result.direct_count == 14
    assert result.inferred_count == 2
    assert len(result.detections) == 16
    assert all(item.height_mm > 14.0 for item in result.detections)


def test_grid_numbering_stays_row_major_with_small_within_row_y_offsets() -> None:
    image, _ = _neutral_dark_label_seam_scene(alternating_y_offset_px=2)
    result = detect_objects(
        image,
        _seam_scene_options(),
        WorkArea(0.0, 220.0, 0.0, 220.0),
        4.0,
    )

    assert result.grid is not None
    assert (result.grid["columns"], result.grid["rows"]) == (2, 8)
    assert [
        (
            item.index,
            item.diagnostics["grid_row"],
            item.diagnostics["grid_column"],
        )
        for item in result.detections
    ] == [
        (row * 2 + column + 1, row, column)
        for row in range(8)
        for column in range(2)
    ]


@pytest.mark.parametrize("angle_deg", [-8.0, -4.0, 2.0, 5.0, 8.0])
def test_rotated_grid_keeps_angle_and_stable_row_major_numbering(
    angle_deg: float,
) -> None:
    pixels_per_mm = 4.0
    size_px = 880
    image = np.full((size_px, size_px, 3), 220, dtype=np.uint8)
    for center_y_mm in (170.0, 130.0, 90.0, 50.0):
        for center_x_mm in (70.0, 150.0):
            center_x_px = int(center_x_mm * pixels_per_mm)
            center_y_px = int((220.0 - center_y_mm) * pixels_per_mm)
            width_px = int(60.0 * pixels_per_mm)
            height_px = int(18.0 * pixels_per_mm)
            radius_px = 12
            cv2.rectangle(
                image,
                (
                    center_x_px - width_px // 2 + radius_px,
                    center_y_px - height_px // 2,
                ),
                (
                    center_x_px + width_px // 2 - radius_px,
                    center_y_px + height_px // 2,
                ),
                (55, 58, 62),
                -1,
            )
            cv2.rectangle(
                image,
                (
                    center_x_px - width_px // 2,
                    center_y_px - height_px // 2 + radius_px,
                ),
                (
                    center_x_px + width_px // 2,
                    center_y_px + height_px // 2 - radius_px,
                ),
                (55, 58, 62),
                -1,
            )
            for corner in (
                (
                    center_x_px - width_px // 2 + radius_px,
                    center_y_px - height_px // 2 + radius_px,
                ),
                (
                    center_x_px + width_px // 2 - radius_px,
                    center_y_px - height_px // 2 + radius_px,
                ),
                (
                    center_x_px + width_px // 2 - radius_px,
                    center_y_px + height_px // 2 - radius_px,
                ),
                (
                    center_x_px - width_px // 2 + radius_px,
                    center_y_px + height_px // 2 - radius_px,
                ),
            ):
                cv2.circle(image, corner, radius_px, (55, 58, 62), -1)
    matrix = cv2.getRotationMatrix2D(
        (size_px / 2.0, size_px / 2.0),
        angle_deg,
        1.0,
    )
    image = cv2.warpAffine(
        image,
        matrix,
        (size_px, size_px),
        flags=cv2.INTER_LINEAR,
        borderValue=(220, 220, 220),
    )

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            min_area_mm2=200.0,
            max_area_mm2=2_000.0,
            min_width_mm=20.0,
            min_height_mm=8.0,
            regular_grid=True,
            infer_missing=True,
            normalize_grid=True,
        ),
        WorkArea(0.0, 220.0, 0.0, 220.0),
        pixels_per_mm,
    )

    assert result.grid is not None
    assert (result.grid["columns"], result.grid["rows"]) == (2, 4)
    assert result.grid["rotation_deg"] == pytest.approx(angle_deg, abs=0.1)
    assert [
        (
            item.index,
            item.diagnostics["grid_row"],
            item.diagnostics["grid_column"],
        )
        for item in result.detections
    ] == [
        (row * 2 + column + 1, row, column)
        for row in range(4)
        for column in range(2)
    ]


def test_auto_neutral_grid_survives_severe_exposure_gradient() -> None:
    image, _ = _neutral_dark_label_seam_scene()
    exposure = np.linspace(0.45, 1.45, image.shape[1], dtype=np.float32)[
        None,
        :,
        None,
    ]
    image = np.clip(image.astype(np.float32) * exposure, 0, 255).astype(np.uint8)
    result = detect_objects(
        image,
        _seam_scene_options(),
        WorkArea(0.0, 220.0, 0.0, 220.0),
        4.0,
    )

    assert result.grid is not None
    assert (result.grid["columns"], result.grid["rows"]) == (2, 8)
    assert result.grid["mask_source"] in {"adaptive_dark", "closed_outline"}
    assert result.direct_count == 16
    assert result.inferred_count == 0
    assert all(item.height_mm > 14.0 for item in result.detections)


def test_auto_grid_detects_hollow_label_borders_with_dense_interior_text() -> None:
    image = np.full((800, 800, 3), 215, dtype=np.uint8)
    for row in range(6):
        for column in range(2):
            x = 45 + column * 370
            y = 40 + row * 120
            cv2.rectangle(
                image,
                (x, y),
                (x + 320, y + 82),
                (55, 55, 55),
                2,
                cv2.LINE_AA,
            )
            for text_y in range(y + 18, y + 70, 10):
                cv2.line(
                    image,
                    (x + 25, text_y),
                    (x + 290, text_y),
                    (80, 80, 80),
                    1,
                    cv2.LINE_AA,
                )

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="auto",
            min_area_mm2=1_000.0,
            max_area_mm2=20_000.0,
            min_width_mm=4.0,
            min_height_mm=3.0,
            regular_grid=True,
            infer_missing=True,
            normalize_grid=True,
        ),
        WorkArea(0.0, 200.0, 0.0, 200.0),
        4.0,
    )

    assert result.grid is not None
    assert (result.grid["columns"], result.grid["rows"]) == (2, 6)
    assert result.direct_count == 12
    assert result.inferred_count == 0
    assert all(item.width_mm == pytest.approx(81.0, abs=1.0) for item in result.detections)
    assert all(item.height_mm == pytest.approx(21.5, abs=1.0) for item in result.detections)


def test_normalized_grid_retains_and_flags_cells_crossing_work_area() -> None:
    image, _ = _neutral_dark_label_seam_scene()
    options = _seam_scene_options()
    options.border_offset_mm = 16.0
    result = detect_objects(
        image,
        options,
        WorkArea(0.0, 220.0, 0.0, 220.0),
        4.0,
    )

    assert result.grid is not None
    assert result.grid["observed_cells"] == 16
    assert result.grid["direct_cells"] == 16
    assert result.direct_count == 16
    assert len(result.detections) == 16
    outside = [
        item
        for item in result.detections
        if not item.diagnostics["within_work_area"]
    ]
    assert outside
    assert result.grid["outside_cells"] == len(outside)
    assert all(not item.selected_default for item in outside)
    assert "outside the work area" in result.message


def test_edge_cropped_observation_is_flagged_and_not_preselected() -> None:
    image = np.full((400, 400, 3), 230, dtype=np.uint8)
    cv2.rectangle(image, (300, 120), (399, 220), (35, 35, 35), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            min_area_mm2=100.0,
            min_width_mm=10.0,
            min_height_mm=10.0,
            regular_grid=False,
        ),
        WorkArea(0.0, 100.0, 0.0, 100.0),
        4.0,
    )

    assert result.direct_count == 1
    detection = result.detections[0]
    assert detection.diagnostics["touches_image_edge"] is True
    assert detection.diagnostics["image_edge_sides"] == ["right"]
    assert detection.selected_default is False
    assert "may be cropped" in result.message


def test_non_grid_concentric_foreground_preserves_one_raster_contour_tree() -> None:
    image = np.full((400, 400, 3), 230, dtype=np.uint8)
    cv2.circle(image, (200, 200), 40, (30, 30, 30), -1)
    cv2.circle(image, (200, 200), 16, (230, 230, 230), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="manual",
            contrast_threshold=128,
            regular_grid=False,
            output_mode="native",
            min_area_mm2=10.0,
            min_width_mm=2.0,
            min_height_mm=2.0,
        ),
        WorkArea(0.0, 100.0, 0.0, 100.0),
        4.0,
    )

    assert result.direct_count == 1
    detection = result.detections[0]
    assert detection.shape == "contour"
    assert len(detection.vector_contours_mm) == 2
    assert detection.width_mm == pytest.approx(20.0, abs=0.5)
    assert detection.diagnostics["contour_parents"] == [None, 0]
    assert detection.diagnostics["contour_depths"] == [0, 1]
    assert detection.diagnostics["mask_source"] == "raster_non_grid"


@pytest.mark.parametrize("inner_center", ((203, 200), (200, 206)))
def test_off_center_nested_circle_is_not_forced_to_washer(inner_center) -> None:
    image = np.full((400, 400, 3), 230, dtype=np.uint8)
    cv2.circle(image, (200, 200), 40, (30, 30, 30), -1)
    cv2.circle(image, inner_center, 16, (230, 230, 230), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="manual",
            contrast_threshold=128,
            regular_grid=False,
            min_area_mm2=10.0,
            min_width_mm=2.0,
            min_height_mm=2.0,
        ),
        WorkArea(0.0, 100.0, 0.0, 100.0),
        4.0,
    )

    assert all(item.shape != "washer" for item in result.detections)


def test_multiple_annuli_remain_separate_logical_washers() -> None:
    image = np.full((400, 400, 3), 230, dtype=np.uint8)
    for center in ((120, 200), (280, 200)):
        cv2.circle(image, center, 40, (30, 30, 30), -1)
        cv2.circle(image, center, 16, (230, 230, 230), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            regular_grid=True,
            min_area_mm2=10.0,
            min_width_mm=2.0,
            min_height_mm=2.0,
        ),
        WorkArea(0.0, 100.0, 0.0, 100.0),
        4.0,
    )

    assert result.direct_count == 2
    assert all(item.shape == "washer" for item in result.detections)
    assert all(len(item.vector_contours_mm) == 2 for item in result.detections)


def test_circular_outer_with_square_hole_is_not_forced_to_washer() -> None:
    image = np.full((400, 400, 3), 230, dtype=np.uint8)
    cv2.circle(image, (200, 200), 40, (30, 30, 30), -1)
    cv2.rectangle(image, (184, 184), (216, 216), (230, 230, 230), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="manual",
            contrast_threshold=128,
            regular_grid=False,
            min_area_mm2=10.0,
            min_width_mm=2.0,
            min_height_mm=2.0,
        ),
        WorkArea(0.0, 100.0, 0.0, 100.0),
        4.0,
    )

    assert all(item.shape != "washer" for item in result.detections)


def test_guarded_output_area_is_distinct_from_camera_work_area() -> None:
    image = np.full((400, 400, 3), 230, dtype=np.uint8)
    cv2.rectangle(image, (328, 150), (380, 250), (35, 35, 35), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            min_area_mm2=50.0,
            min_width_mm=8.0,
            min_height_mm=8.0,
            regular_grid=False,
        ),
        WorkArea(0.0, 100.0, 0.0, 100.0),
        4.0,
        output_work_area=WorkArea(5.0, 85.0, 5.0, 95.0),
    )

    assert result.direct_count == 1
    detection = result.detections[0]
    assert detection.diagnostics["within_camera_work_area"] is True
    assert detection.diagnostics["within_work_area"] is False
    assert detection.diagnostics["work_area_overruns_mm"]["right"] > 0.0
    assert detection.selected_default is False
    assert "guarded output area" in result.message
    assert "right by" in result.message
    payload = result.to_dict()
    assert payload["camera_work_area"] == {
        "x_min": 0.0,
        "x_max": 100.0,
        "y_min": 0.0,
        "y_max": 100.0,
    }
    assert payload["output_work_area"] == {
        "x_min": 5.0,
        "x_max": 85.0,
        "y_min": 5.0,
        "y_max": 95.0,
    }


def test_output_work_area_must_stay_inside_camera_work_area() -> None:
    with pytest.raises(ValueError, match="must lie inside"):
        detect_objects(
            np.zeros((20, 20, 3), dtype=np.uint8),
            TraceOptions(),
            WorkArea(0.0, 20.0, 0.0, 20.0),
            1.0,
            output_work_area=WorkArea(-1.0, 20.0, 0.0, 20.0),
        )


def test_contrast_grid_retains_light_objects_on_dark_background() -> None:
    image = np.full((500, 600, 3), 45, dtype=np.uint8)
    for row in range(3):
        for column in range(2):
            x, y = 60 + column * 280, 50 + row * 140
            cv2.rectangle(
                image,
                (x + 15, y),
                (x + 205, y + 80),
                (220, 220, 220),
                -1,
            )
            cv2.rectangle(
                image,
                (x, y + 15),
                (x + 220, y + 65),
                (220, 220, 220),
                -1,
            )
            for center in (
                (x + 15, y + 15),
                (x + 205, y + 15),
                (x + 205, y + 65),
                (x + 15, y + 65),
            ):
                cv2.circle(image, center, 15, (220, 220, 220), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            min_area_mm2=100.0,
            min_width_mm=20.0,
            min_height_mm=10.0,
            regular_grid=True,
        ),
        WorkArea(0.0, 150.0, 0.0, 125.0),
        4.0,
    )

    assert result.grid is not None
    assert (result.grid["columns"], result.grid["rows"]) == (2, 3)
    assert result.direct_count == 6
    assert result.inferred_count == 0
    assert all(item.height_mm > 15.0 for item in result.detections)


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


def test_grid_gap_partial_boundary_evidence_refines_inferred_cell() -> None:
    image, mask, expected_center = _partial_grid_scene(evidence=True)
    options = TraceOptions(
        detection_mode="color", min_area_mm2=30, min_width_mm=20,
        min_height_mm=8, regular_grid=True, infer_missing=True,
        output_mode="rounded",
    )
    result = detect_objects(
        image,
        options,
        WorkArea(0.0, 190.0, 0.0, 130.0), 4.0, mask_override=mask,
    )

    assert result.grid is not None
    assert result.direct_count == 7
    recovered = next(item for item in result.detections if item.source == "inferred")
    assert recovered.selected_default is False
    assert recovered.diagnostics["evidence_supported"] is True
    assert {"left", "right", "bottom"} <= set(recovered.diagnostics["supported_sides"])
    assert recovered.diagnostics["supported_side_count"] >= 3
    assert recovered.diagnostics["evidence_score"] > 0.45
    expected_mm = (expected_center[0] / 4.0, 130.0 - expected_center[1] / 4.0)
    assert recovered.center_mm == pytest.approx(expected_mm, abs=0.55)
    direct = [item for item in result.detections if item.source == "direct"]
    assert len({item.width_mm for item in direct}) == 1
    assert len({item.height_mm for item in direct}) == 1
    assert len({item.rotation_deg for item in result.detections}) == 1
    baseline = detect_objects(
        image,
        TraceOptions(**{**options.to_dict(), "infer_missing": False}),
        WorkArea(0.0, 190.0, 0.0, 130.0), 4.0, mask_override=mask,
    )
    assert [item.center_mm for item in direct] == pytest.approx(
        [item.center_mm for item in baseline.detections]
    )


def test_grid_gap_without_boundary_evidence_stays_blind_inference() -> None:
    image, mask, _ = _partial_grid_scene(evidence=False)
    result = detect_objects(
        image,
        TraceOptions(detection_mode="color", min_area_mm2=30, min_width_mm=20,
                     min_height_mm=8, regular_grid=True, infer_missing=True),
        WorkArea(0.0, 190.0, 0.0, 130.0), 4.0, mask_override=mask,
    )
    inferred = next(item for item in result.detections if item.source == "inferred")
    assert inferred.diagnostics["evidence_supported"] is False
    assert inferred.diagnostics["supported_side_count"] == 0
    assert inferred.diagnostics["recovery_shift_mm"] == pytest.approx([0.0, 0.0])


def test_grid_gap_internal_text_cannot_make_large_center_correction() -> None:
    image, mask, _ = _partial_grid_scene(evidence=False, distracting_text=True)
    result = detect_objects(
        image,
        TraceOptions(detection_mode="color", min_area_mm2=30, min_width_mm=20,
                     min_height_mm=8, regular_grid=True, infer_missing=True),
        WorkArea(0.0, 190.0, 0.0, 130.0), 4.0, mask_override=mask,
    )
    inferred = next(item for item in result.detections if item.source == "inferred")
    assert inferred.diagnostics["evidence_supported"] is False
    assert np.linalg.norm(inferred.diagnostics["recovery_shift_mm"]) < 0.25


def test_grid_gap_partial_recovery_is_not_run_when_gap_inference_is_off() -> None:
    image, mask, _ = _partial_grid_scene(evidence=True)
    result = detect_objects(
        image,
        TraceOptions(detection_mode="color", min_area_mm2=30, min_width_mm=20,
                     min_height_mm=8, regular_grid=True, infer_missing=False),
        WorkArea(0.0, 190.0, 0.0, 130.0), 4.0, mask_override=mask,
    )
    assert result.direct_count == 7
    assert result.inferred_count == 0
    assert all("evidence_supported" not in item.diagnostics for item in result.detections)


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


def test_repeated_grid_repairs_one_weak_bottom_edge_without_normalizing_cells() -> None:
    image = _label_scene(obscure=False)
    # Remove only the lower edge of one label. Its top, left, and right edges
    # remain valid, so repeated-cell consensus may restore only the bottom.
    x = 42
    y = 18 + 7 * 91
    image[y + 60 : y + 74, x - 2 : x + 303] = image[
        y + 74 : y + 88,
        x - 2 : x + 303,
    ]
    common = dict(
        detection_mode="color",
        target_hue=0,
        min_saturation=35,
        min_area_mm2=20,
        min_width_mm=20,
        min_height_mm=8,
        regular_grid=True,
        infer_missing=False,
        normalize_grid=False,
        snap_grid_cells=False,
        output_mode="rounded",
    )

    unrepaired = detect_objects(
        image,
        TraceOptions(**common, repair_grid_edges=False),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )
    repaired = detect_objects(
        image,
        TraceOptions(**common, repair_grid_edges=True),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )

    def target(result):
        return next(
            item
            for item in result.detections
            if item.diagnostics.get("grid_row") == 7
            and item.diagnostics.get("grid_column") == 0
        )

    before = target(unrepaired)
    after = target(repaired)
    assert repaired.grid is not None
    assert repaired.grid["repaired_edges"] == 1
    assert repaired.grid["repaired_cells"] == 1
    assert after.diagnostics["grid_edge_repairs"] == ["bottom"]
    assert any(
        "weak bottom edge repaired" in reason
        for reason in after.diagnostics["damage_reasons"]
    )
    assert not after.selected_default
    assert before.height_mm == pytest.approx(
        before.diagnostics["observed_height_mm"]
    )
    assert after.diagnostics["observed_height_mm"] == pytest.approx(
        before.height_mm
    )
    assert after.height_mm >= before.height_mm + 1.5
    assert after.height_mm == pytest.approx(
        repaired.grid["observed_cell_height_mm"],
        abs=0.6,
    )
    assert "repaired 1 weak grid edge" in repaired.message


@pytest.mark.parametrize("edge", ("left", "right", "top", "bottom"))
def test_repeated_grid_edge_consensus_handles_each_side(edge: str) -> None:
    image = _label_scene(obscure=False)
    x = 42
    y = 18 + 7 * 91
    if edge == "left":
        image[y - 2 : y + 73, x : x + 12] = image[
            y - 2 : y + 73,
            x - 16 : x - 4,
        ]
    elif edge == "right":
        image[y - 2 : y + 73, x + 289 : x + 303] = image[
            y - 2 : y + 73,
            x + 305 : x + 319,
        ]
    elif edge == "top":
        image[y : y + 11, x - 2 : x + 303] = image[
            y - 14 : y - 3,
            x - 2 : x + 303,
        ]
    else:
        image[y + 60 : y + 74, x - 2 : x + 303] = image[
            y + 74 : y + 88,
            x - 2 : x + 303,
        ]

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
            infer_missing=False,
            normalize_grid=False,
            snap_grid_cells=False,
            repair_grid_edges=True,
            output_mode="rounded",
        ),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )

    assert result.grid is not None
    assert result.grid["repaired_edges"] == 1
    target = next(
        item
        for item in result.detections
        if item.diagnostics.get("grid_row") == 7
        and item.diagnostics.get("grid_column") == 0
    )
    assert target.diagnostics["grid_edge_repairs"] == [edge]


def test_grid_edge_repair_does_not_force_a_centered_smaller_cell() -> None:
    image = _label_scene(obscure=False)
    x = 42
    y = 18 + 7 * 91
    # Remove equal material from top and bottom. This represents a genuinely
    # shorter centered cell, not one missing boundary, so neither side is
    # eligible for one-sided repair.
    image[y : y + 6, x - 2 : x + 303] = image[
        y - 14 : y - 8,
        x - 2 : x + 303,
    ]
    image[y + 64 : y + 72, x - 2 : x + 303] = image[
        y + 74 : y + 82,
        x - 2 : x + 303,
    ]
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
            infer_missing=False,
            normalize_grid=False,
            snap_grid_cells=False,
            repair_grid_edges=True,
            output_mode="rounded",
        ),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )

    assert result.grid is not None
    assert result.grid["repaired_edges"] == 0
    target = next(
        item
        for item in result.detections
        if item.diagnostics.get("grid_row") == 7
        and item.diagnostics.get("grid_column") == 0
    )
    assert target.diagnostics["grid_edge_repairs"] == []
    assert target.height_mm == pytest.approx(
        target.diagnostics["observed_height_mm"]
    )


def test_loose_grid_repairs_center_on_only_the_truncated_size_axis() -> None:
    image = _label_scene(obscure=False)
    # Remove the left edge of one cell. Its fitted width and center are both
    # biased right, while its independently observed vertical pose is valid.
    image[291:362, 418:438] = image[291:362, 400:420]
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
            snap_grid_cells=False,
            output_mode="rounded",
        ),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )

    repaired = [
        item
        for item in result.detections
        if item.diagnostics.get("repaired_center_axes")
    ]
    assert len(repaired) == 1
    item = repaired[0]
    assert item.diagnostics["repaired_center_axes"] == ["width"]
    observed_center = item.diagnostics["observed_center_mm"]
    assert abs(item.center_mm[0] - observed_center[0]) >= 1.0
    assert item.center_mm[1] == pytest.approx(observed_center[1], abs=0.15)


def test_loose_normalized_grid_keeps_direct_cell_pose_but_shares_dimensions() -> None:
    result = detect_objects(
        _label_scene(obscure=False),
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
            snap_grid_cells=False,
            output_mode="rounded",
        ),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )

    assert result.grid is not None
    assert result.grid["normalized"] is True
    assert result.grid["cells_snapped"] is False
    direct = [item for item in result.detections if item.source == "direct"]
    assert len({item.width_mm for item in direct}) == 1
    assert len({item.height_mm for item in direct}) == 1
    assert len({item.corner_radius_mm for item in direct}) == 1
    for item in direct:
        assert item.center_mm == pytest.approx(item.diagnostics["observed_center_mm"])
        assert item.rotation_deg == pytest.approx(
            item.diagnostics["observed_rotation_deg"]
        )


def test_loose_normalized_grid_can_preserve_each_detected_top_edge() -> None:
    result = detect_objects(
        _label_scene(obscure=False),
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
            snap_grid_cells=False,
            normalize_anchor="top",
            output_mode="rounded",
        ),
        WorkArea(0.0, 190.0, 0.0, 190.0),
        4.0,
    )

    direct = [item for item in result.detections if item.source == "direct"]
    assert direct
    assert result.grid["normalization_anchor"] == "top"
    for item in direct:
        image_angle = math.radians(-item.rotation_deg)
        image_down = np.array([-math.sin(image_angle), math.cos(image_angle)])
        output_center_px = np.array(
            [item.center_mm[0] * 4.0, (190.0 - item.center_mm[1]) * 4.0]
        )
        observed_center_mm = item.diagnostics["observed_center_mm"]
        observed_center_px = np.array(
            [observed_center_mm[0] * 4.0, (190.0 - observed_center_mm[1]) * 4.0]
        )
        output_top = float(output_center_px @ image_down - item.height_mm * 2.0)
        observed_top = float(
            observed_center_px @ image_down
            - float(item.diagnostics["observed_height_mm"]) * 2.0
        )
        assert output_top == pytest.approx(observed_top, abs=1e-6)


def test_trace_options_reject_unknown_identical_cell_anchor() -> None:
    with pytest.raises(ValueError, match="identical-cell anchor"):
        TraceOptions(normalize_anchor="bottom")


def test_trace_options_require_boolean_grid_edge_repair() -> None:
    with pytest.raises(ValueError, match="repair_grid_edges must be a JSON boolean"):
        TraceOptions(repair_grid_edges="yes")


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


def test_custom_top_offset_moves_only_rotated_top_edge_and_corners():
    rectangle = {
        "center": np.array([400.0, 400.0]),
        "width": 320.0,
        "height": 80.0,
        "angle_image_deg": -17.0,
        "radius_px": 16.0,
    }
    area = WorkArea(0.0, 200.0, 0.0, 200.0)
    base = _machine_geometry(rectangle, area, 4.0, 0.0)
    trimmed = _machine_geometry(
        rectangle,
        area,
        4.0,
        0.0,
        edge_offsets_mm={
            "top": -2.0,
            "right": 0.0,
            "bottom": 0.0,
            "left": 0.0,
        },
    )

    rotation = math.radians(base["rotation_deg"])
    local_x = np.array([math.cos(rotation), math.sin(rotation)])
    local_y = np.array([-local_x[1], local_x[0]])

    def bounds(geometry: dict[str, object], axis: np.ndarray) -> tuple[float, float]:
        points = np.asarray(geometry["box_mm"], dtype=np.float64)
        projected = points @ axis
        return float(projected.min()), float(projected.max())

    assert trimmed["width_mm"] == pytest.approx(base["width_mm"])
    assert trimmed["height_mm"] == pytest.approx(base["height_mm"] - 2.0)
    assert trimmed["corner_radius_mm"] == pytest.approx(base["corner_radius_mm"])
    assert bounds(trimmed, local_x) == pytest.approx(bounds(base, local_x))
    base_bottom, base_top = bounds(base, local_y)
    trimmed_bottom, trimmed_top = bounds(trimmed, local_y)
    assert trimmed_bottom == pytest.approx(base_bottom)
    assert trimmed_top == pytest.approx(base_top - 2.0)


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


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_hue": float("nan")},
        {"hue_tolerance": float("inf")},
        {"min_area_mm2": float("nan")},
        {"target_bgr": [0, float("nan"), 0]},
        {"border_offset_top_mm": float("nan")},
    ],
)
def test_trace_options_reject_nonfinite_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="finite"):
        TraceOptions(**overrides)


def test_trace_options_reject_string_booleans() -> None:
    with pytest.raises(ValueError, match="JSON boolean"):
        TraceOptions(regular_grid="false")  # type: ignore[arg-type]


def test_trace_options_reject_unknown_border_offset_mode() -> None:
    with pytest.raises(ValueError, match="border offset mode"):
        TraceOptions(border_offset_mode="diagonal")


def test_trace_rejects_nonfinite_scale_and_malformed_images() -> None:
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    area = WorkArea(0.0, 20.0, 0.0, 20.0)

    with pytest.raises(ValueError, match="finite"):
        detect_objects(image, TraceOptions(), area, float("nan"))
    with pytest.raises(ValueError, match="uint8 BGR"):
        detect_objects(np.zeros((20, 20), dtype=np.uint8), TraceOptions(), area, 1.0)
    with pytest.raises(ValueError, match="finite"):
        sample_color(image, float("nan"), 5.0)



def test_straight_edge_rotation_improves_corner_biased_label_angle() -> None:
    target_angle = 5.0
    local = np.asarray(
        [
            (-180.0, -55.0),
            (135.0, -55.0),
            (155.0, -67.0),  # damaged/protruding corner biases the hull
            (180.0, -55.0),
            (180.0, 55.0),
            (-180.0, 55.0),
        ],
        dtype=np.float64,
    )
    angle = math.radians(target_angle)
    rotation = np.asarray(
        [
            [math.cos(angle), -math.sin(angle)],
            [math.sin(angle), math.cos(angle)],
        ],
        dtype=np.float64,
    )
    polygon = np.round(
        local @ rotation.T + np.asarray((350.0, 210.0))
    ).astype(np.int32)
    mask = np.zeros((450, 750), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon], 255)
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    contour = max(contours, key=cv2.contourArea)
    rectangle = _long_axis_rect(contour)
    assert rectangle is not None

    refined, diagnostics = _straight_edge_angle(contour, rectangle)

    assert refined is not None
    assert diagnostics["source"] == "long_edges_crosschecked"
    assert abs(refined - target_angle) < abs(
        float(rectangle["angle_image_deg"]) - target_angle
    )
    assert refined == pytest.approx(target_angle, abs=0.15)


def test_straight_edge_rotation_rejects_nearly_square_geometry() -> None:
    mask = np.zeros((160, 160), dtype=np.uint8)
    cv2.rectangle(mask, (20, 25), (120, 115), 255, -1)
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    contour = contours[0]
    rectangle = _long_axis_rect(contour)
    assert rectangle is not None

    refined, diagnostics = _straight_edge_angle(contour, rectangle)

    assert refined is None
    assert diagnostics["reason"] == "shape_not_elongated"



def _corner_biased_rotated_rectangle(extra_px: float) -> np.ndarray:
    center = np.asarray((350.0, 210.0), dtype=np.float64)
    width = 360.0
    height = 110.0
    angle_deg = 5.0
    mask = np.zeros((500, 800), dtype=np.uint8)
    box = np.round(
        cv2.boxPoints(((float(center[0]), float(center[1])), (width, height), angle_deg))
    ).astype(np.int32)
    cv2.fillPoly(mask, [box], 255)
    angle = math.radians(angle_deg)
    u = np.asarray((math.cos(angle), math.sin(angle)), dtype=np.float64)
    v = np.asarray((-u[1], u[0]), dtype=np.float64)
    protrusion_center = center + u * (width * 0.42) + v * (-height * 0.5 - extra_px)
    cv2.circle(
        mask,
        tuple(np.round(protrusion_center).astype(int)),
        max(2, int(round(extra_px))),
        255,
        -1,
    )
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    return max(contours, key=cv2.contourArea)


def test_straight_edge_center_corrects_small_corner_biased_label_center() -> None:
    expected = np.asarray((350.0, 210.0), dtype=np.float64)
    contour = _corner_biased_rotated_rectangle(4.0)
    rectangle = _long_axis_rect(contour)
    assert rectangle is not None
    initial = np.asarray(rectangle["center"], dtype=np.float64).copy()
    initial_error = float(np.linalg.norm(initial - expected))
    assert initial_error > 2.0

    refined_angle, _ = _straight_edge_angle(contour, rectangle)
    assert refined_angle is not None
    rectangle["angle_image_deg"] = refined_angle
    refined, diagnostics = _straight_edge_center(contour, rectangle)

    assert refined is not None
    assert diagnostics["accepted"] is True
    assert diagnostics["offset_v_px"] is not None
    assert float(np.linalg.norm(refined - expected)) < 0.20
    assert float(np.linalg.norm(refined - expected)) < initial_error * 0.10


def test_straight_edge_center_rejects_large_one_sided_vertical_damage() -> None:
    contour = _corner_biased_rotated_rectangle(8.0)
    rectangle = _long_axis_rect(contour)
    assert rectangle is not None
    initial = np.asarray(rectangle["center"], dtype=np.float64).copy()

    refined_angle, _ = _straight_edge_angle(contour, rectangle)
    assert refined_angle is not None
    rectangle["angle_image_deg"] = refined_angle
    refined, diagnostics = _straight_edge_center(contour, rectangle)

    # X can still use its independent left/right evidence, but the damaged
    # top/bottom pair must not be allowed to drag the Y center.
    assert refined is not None
    assert diagnostics["offset_v_px"] is None
    assert diagnostics["y_rejection_reason"] in {
        "edge_separation_mismatch",
        "center_shift_too_large",
        "edge_fit_unavailable",
    }
    assert abs(float(refined[1] - initial[1])) < 0.10
