from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

import laser_aligner.vision.object_trace as object_trace_module
from laser_aligner.config import WorkArea
from laser_aligner.project import (
    NativePathGeometry,
    PathAffineTransform,
    PathFillRule,
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationOptions,
    native_path_bounds,
    read_raster_asset_payload,
    transform_native_path,
    vectorize_raster_payload,
)
from laser_aligner.vision.camera_raster_normalization import (
    normalize_camera_trace_frame,
)
from laser_aligner.vision.object_trace import (
    TraceDetectionCancelledError,
    TraceOptions,
    _normalized_raster_points_to_camera,
    detect_objects,
)


def _glyph_scene() -> np.ndarray:
    height, width = 300, 620
    gradient = np.linspace(212, 244, width, dtype=np.float32)[None, :, None]
    image = np.repeat(gradient, height, axis=0)
    image = np.repeat(image, 3, axis=2).astype(np.uint8)
    background = image.copy()

    dark = (35, 35, 35)
    cv2.rectangle(image, (40, 55), (120, 175), dark, -1)
    image[85:145, 65:96] = background[85:145, 65:96]
    cv2.rectangle(image, (123, 55), (190, 175), dark, -1)

    cv2.circle(image, (285, 115), 62, dark, -1, lineType=cv2.LINE_AA)
    circle_mask = np.zeros((height, width), dtype=np.uint8)
    cv2.circle(circle_mask, (285, 115), 28, 255, -1, lineType=cv2.LINE_AA)
    image[circle_mask > 0] = background[circle_mask > 0]

    cv2.putText(
        image,
        "S",
        (380, 175),
        cv2.FONT_HERSHEY_SIMPLEX,
        4.2,
        dark,
        18,
        cv2.LINE_AA,
    )
    cv2.rectangle(image, (75, 235), (510, 247), dark, -1)
    image[25, 25] = dark
    image[28:30, 540:542] = dark
    return image


def _trace_options() -> TraceOptions:
    return TraceOptions(
        detection_mode="contrast",
        contrast_threshold_mode="manual",
        contrast_threshold=128,
        regular_grid=False,
        output_mode="native",
        min_area_mm2=0.50,
        max_area_mm2=20_000.0,
        min_width_mm=0.1,
        min_height_mm=0.1,
        confidence_threshold=0.0,
        native_fitting_tolerance_mm=0.25,
    )


def _root_groups(contours: Sequence[Any]) -> tuple[tuple[int, ...], ...]:
    groups: list[tuple[int, ...]] = []
    for root_index, root in enumerate(contours):
        if root.parent_index is not None:
            continue
        descendants: list[int] = []
        for index, _contour in enumerate(contours):
            ancestor = index
            while contours[ancestor].parent_index is not None:
                ancestor = int(contours[ancestor].parent_index)
            if ancestor == root_index:
                descendants.append(index)
        groups.append(tuple(descendants))
    return tuple(groups)


def _contour_tree_shape(mask: np.ndarray) -> tuple[list[int], list[int]]:
    contours, hierarchy = cv2.findContours(
        mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    assert hierarchy is not None
    parents = hierarchy[0, :, 3].tolist()
    depths = []
    for index in range(len(contours)):
        depth = 0
        parent = parents[index]
        while parent >= 0:
            depth += 1
            parent = parents[parent]
        depths.append(depth)
    return parents, depths


def _wrench_hole_scene() -> np.ndarray:
    image = np.full((320, 480, 3), 228, dtype=np.uint8)
    dark = (28, 28, 28)
    background = (228, 228, 228)

    cv2.rectangle(image, (70, 120), (355, 200), dark, thickness=-1)
    cv2.circle(image, (370, 160), 75, dark, thickness=-1, lineType=cv2.LINE_8)
    cv2.fillPoly(
        image,
        [
            np.asarray(
                ((35, 105), (95, 125), (95, 195), (35, 215), (55, 175), (55, 145)),
                dtype=np.int32,
            )
        ],
        dark,
    )

    # Two sub-minimum reflection patches and one legitimate enclosed hole.
    cv2.rectangle(image, (125, 142), (128, 145), background, thickness=-1)
    cv2.rectangle(image, (155, 174), (159, 178), background, thickness=-1)
    cv2.rectangle(image, (215, 135), (234, 154), background, thickness=-1)

    # One over-maximum hole contains a foreground island large enough to pass
    # the object minimum. Filling that hole intentionally absorbs the island.
    cv2.rectangle(image, (330, 125), (410, 195), background, thickness=-1)
    cv2.rectangle(image, (350, 145), (389, 174), dark, thickness=-1)

    # This independent foreground speck is below the 50 mm2 object minimum.
    cv2.rectangle(image, (15, 15), (30, 30), dark, thickness=-1)
    return image


def _wrench_hole_options(
    *,
    max_area_mm2: float = 8_000.0,
    max_hole_area_mm2: float | None = 30.0,
) -> TraceOptions:
    return TraceOptions(
        detection_mode="contrast",
        contrast_threshold_mode="manual",
        contrast_threshold=128,
        regular_grid=False,
        output_mode="native",
        min_area_mm2=50.0,
        max_area_mm2=max_area_mm2,
        min_hole_area_mm2=2.0,
        max_hole_area_mm2=max_hole_area_mm2,
        min_width_mm=1.0,
        min_height_mm=1.0,
        confidence_threshold=0.0,
        native_fitting_tolerance_mm=0.25,
    )


def _auto_hole_scene(*, light_foreground: bool) -> np.ndarray:
    background = 28 if light_foreground else 228
    foreground = 228 if light_foreground else 28
    image = np.full((160, 240, 3), background, dtype=np.uint8)
    foreground_bgr = (foreground,) * 3
    background_bgr = (background,) * 3
    cv2.rectangle(image, (20, 25), (105, 135), foreground_bgr, thickness=-1)
    cv2.circle(
        image,
        (175, 80),
        38,
        foreground_bgr,
        thickness=-1,
        lineType=cv2.LINE_8,
    )
    cv2.rectangle(image, (35, 40), (38, 43), background_bgr, thickness=-1)
    cv2.rectangle(image, (65, 60), (76, 71), background_bgr, thickness=-1)
    cv2.circle(
        image,
        (175, 80),
        16,
        background_bgr,
        thickness=-1,
        lineType=cv2.LINE_8,
    )
    return image


def test_non_grid_contrast_preserves_literal_components_holes_and_lines() -> None:
    result = detect_objects(
        _glyph_scene(),
        _trace_options(),
        WorkArea(10.0, 165.0, 20.0, 95.0),
        4.0,
    )

    assert result.grid is None
    assert result.direct_count == 5
    assert result.inferred_count == 0
    assert all(item.diagnostics["mask_source"] == "raster_non_grid" for item in result.detections)
    assert all(item.native_verified for item in result.detections)
    assert all(item.selected_default for item in result.detections)
    assert sum(len(item.raw_contours_mm) == 2 for item in result.detections) == 2
    assert any(item.width_mm > 100.0 and item.height_mm < 5.0 for item in result.detections)
    assert any(item.width_mm < 21.0 for item in result.detections)
    sequences = [
        sequence
        for item in result.detections
        for sequence in item.diagnostics["native_sequences"]
    ]
    assert any("L" in sequence for sequence in sequences)
    assert any("C" in sequence for sequence in sequences)
    assert {item.diagnostics["connected_component_count"] for item in result.detections} == {5}
    assert {item.diagnostics["root_tree_count"] for item in result.detections} == {5}


def test_manual_contrast_wrench_uses_independent_object_and_hole_ranges() -> None:
    image = _wrench_hole_scene()
    previews = []
    result = detect_objects(
        image,
        _wrench_hole_options(),
        WorkArea(0.0, 120.0, 0.0, 80.0),
        4.0,
        raster_preview_callback=previews.append,
    )

    assert result.direct_count == 1
    assert result.options.min_area_mm2 == 50.0
    assert result.options.max_area_mm2 == 8_000.0
    assert result.options.min_hole_area_mm2 == 2.0
    assert result.options.max_hole_area_mm2 == 30.0
    assert result.diagnostics["hole_cleanup"] == {
        "raw_hole_count": 4,
        "preserved_hole_count": 1,
        "filled_below_min_count": 2,
        "filled_above_max_count": 1,
        "minimum_hole_area_mm2": 2.0,
        "maximum_hole_area_mm2": 30.0,
    }
    assert len(previews) == 1
    preview = previews[0]
    assert preview.contour_mask.shape == (image.shape[0] * 4, image.shape[1] * 4)

    def production_value(x: int, y: int) -> int:
        return int(preview.contour_mask[y * 4 + 2, x * 4 + 2])

    assert production_value(126, 143) == 255
    assert production_value(157, 176) == 255
    assert production_value(224, 144) == 0
    assert production_value(340, 135) == 255
    assert production_value(370, 160) == 255
    assert production_value(22, 22) == 0

    parents, depths = _contour_tree_shape(preview.contour_mask)
    assert parents == [-1, 0]
    assert depths == [0, 1]
    detection = result.detections[0]
    assert 50.0 < detection.area_mm2 < 8_000.0
    assert detection.native_verified
    assert detection.diagnostics["contour_parents"] == [None, 0]
    assert detection.diagnostics["contour_depths"] == [0, 1]
    native = NativePathGeometry.from_dict(detection.native_path or {})
    assert native.fill_rule is PathFillRule.EVENODD
    assert len(native.subpaths) == 2

    unbounded = detect_objects(
        image,
        _wrench_hole_options(max_hole_area_mm2=None),
        WorkArea(0.0, 120.0, 0.0, 80.0),
        4.0,
    )
    assert unbounded.direct_count == 1
    unbounded_detection = unbounded.detections[0]
    assert unbounded.diagnostics["hole_cleanup"] == {
        "raw_hole_count": 4,
        "preserved_hole_count": 2,
        "filled_below_min_count": 2,
        "filled_above_max_count": 0,
        "minimum_hole_area_mm2": 2.0,
        "maximum_hole_area_mm2": None,
    }
    assert sorted(unbounded_detection.diagnostics["contour_depths"]) == [0, 1, 1, 2]
    assert len(
        NativePathGeometry.from_dict(unbounded_detection.native_path or {}).subpaths
    ) == 4

    object_maximum = detect_objects(
        image,
        _wrench_hole_options(max_area_mm2=1_000.0),
        WorkArea(0.0, 120.0, 0.0, 80.0),
        4.0,
    )
    assert object_maximum.direct_count == 0


def test_manual_contrast_outer_silhouette_flattens_synthetic_wrench_tree() -> None:
    image = _wrench_hole_scene()
    work_area = WorkArea(0.0, 120.0, 0.0, 80.0)
    full_options = _wrench_hole_options(max_hole_area_mm2=None)
    full = detect_objects(image, full_options, work_area, 4.0)
    outer = detect_objects(
        image,
        TraceOptions(
            **{
                **full_options.to_dict(),
                "trace_detail": "outer_silhouette",
            }
        ),
        work_area,
        4.0,
    )

    assert full.direct_count == outer.direct_count == 1
    full_detection = full.detections[0]
    outer_detection = outer.detections[0]
    assert sorted(full_detection.diagnostics["contour_depths"]) == [0, 1, 1, 2]
    assert len(NativePathGeometry.from_dict(full_detection.native_path or {}).subpaths) == 4
    assert outer_detection.diagnostics["contour_parents"] == [None]
    assert outer_detection.diagnostics["contour_depths"] == [0]
    assert outer_detection.diagnostics["root_contour_count"] == 1
    assert outer_detection.diagnostics["output_contour_count"] == 1
    assert outer_detection.diagnostics["internal_contours_enumerated"] is False
    assert outer_detection.diagnostics["ignored_internal_contour_count"] is None
    assert len(outer_detection.vector_contours_mm) == 1
    assert outer_detection.native_verified
    assert len(NativePathGeometry.from_dict(outer_detection.native_path or {}).subpaths) == 1

    exterior = np.asarray(outer_detection.vector_contours_mm[0], dtype=np.float32)
    # The open left jaw remains concave; Outer fills only enclosed topology.
    assert cv2.pointPolygonTest(exterior, (10.0, 40.0), False) < 0
    assert cv2.pointPolygonTest(exterior, (20.0, 40.0), False) > 0


@pytest.mark.parametrize(
    ("light_foreground", "expected_strategy", "expected_polarity"),
    (
        (False, "raster_dark", "dark"),
        (True, "raster_light", "light"),
    ),
)
def test_non_grid_auto_winner_applies_explicit_hole_range_to_production_native(
    light_foreground: bool,
    expected_strategy: str,
    expected_polarity: str,
) -> None:
    previews = []
    result = detect_objects(
        _auto_hole_scene(light_foreground=light_foreground),
        TraceOptions(
            detection_mode="auto",
            regular_grid=False,
            min_area_mm2=2.0,
            max_area_mm2=2_000.0,
            min_hole_area_mm2=2.0,
            max_hole_area_mm2=30.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.20,
        ),
        WorkArea(0.0, 60.0, 0.0, 40.0),
        4.0,
        raster_preview_callback=previews.append,
    )

    assert result.diagnostics["auto"]["selected_strategy"] == expected_strategy
    selected_attempt = next(
        attempt
        for attempt in result.diagnostics["auto"]["attempts"]
        if attempt["name"] == expected_strategy
    )
    assert selected_attempt["hole_cleanup"] == {
        "raw_hole_count": 3,
        "preserved_hole_count": 1,
        "filled_below_min_count": 1,
        "filled_above_max_count": 1,
        "minimum_hole_area_mm2": 2.0,
        "maximum_hole_area_mm2": 30.0,
    }
    assert result.diagnostics["hole_cleanup"] == selected_attempt["hole_cleanup"]
    selected_preview = previews[-1]
    assert selected_preview.selected_strategy
    assert selected_preview.strategy == expected_strategy
    assert selected_preview.polarity == expected_polarity
    assert int(selected_preview.contour_mask[41 * 4 + 2, 36 * 4 + 2]) == 255
    assert int(selected_preview.contour_mask[65 * 4 + 2, 70 * 4 + 2]) == 0
    assert int(selected_preview.contour_mask[80 * 4 + 2, 175 * 4 + 2]) == 255
    parents, depths = _contour_tree_shape(selected_preview.contour_mask)
    assert parents.count(-1) == 2
    assert sorted(depths) == [0, 0, 1]
    assert result.direct_count == 2
    assert sorted(
        len(NativePathGeometry.from_dict(item.native_path or {}).subpaths)
        for item in result.detections
    ) == [1, 2]
    assert result.options.min_hole_area_mm2 == 2.0
    assert result.options.max_hole_area_mm2 == 30.0


@pytest.mark.parametrize(
    ("light_foreground", "expected_strategy"),
    [(False, "raster_dark"), (True, "raster_light")],
)
def test_non_grid_auto_outer_silhouette_propagates_to_selected_raster_strategy(
    light_foreground: bool,
    expected_strategy: str,
) -> None:
    previews = []
    result = detect_objects(
        _auto_hole_scene(light_foreground=light_foreground),
        TraceOptions(
            detection_mode="auto",
            regular_grid=False,
            trace_detail="outer_silhouette",
            min_area_mm2=2.0,
            max_area_mm2=2_000.0,
            min_hole_area_mm2=2.0,
            max_hole_area_mm2=30.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.20,
        ),
        WorkArea(0.0, 60.0, 0.0, 40.0),
        4.0,
        raster_preview_callback=previews.append,
    )

    auto = result.diagnostics["auto"]
    assert auto["selected_strategy"] == expected_strategy
    assert auto["requested_options"]["trace_detail"] == "outer_silhouette"
    assert auto["effective_options"]["trace_detail"] == "outer_silhouette"
    assert result.options.trace_detail == "outer_silhouette"
    assert result.diagnostics["outer_only"] is True
    assert previews[-1].selected_strategy is True
    assert previews[-1].strategy == expected_strategy
    _parents, depths = _contour_tree_shape(previews[-1].contour_mask)
    assert sorted(depths) == [0, 0, 1]
    assert all(len(item.vector_contours_mm) == 1 for item in result.detections)
    assert all(
        len(NativePathGeometry.from_dict(item.native_path or {}).subpaths) == 1
        for item in result.detections
    )


def test_raster_to_camera_affine_preserves_pixel_center_semantics() -> None:
    width_px, height_px = 620, 300
    pixels_per_mm = 4.0
    width_mm = width_px / pixels_per_mm
    height_mm = height_px / pixels_per_mm
    x_min, y_max = 10.0, 95.0
    center = (x_min + width_mm / 2.0, y_max - height_mm / 2.0)
    quarter_pixel_x = 0.5 / (4.0 * width_px)
    quarter_pixel_y = 0.5 / (4.0 * height_px)
    points = [
        (-0.5, 0.5),
        (0.5, 0.5),
        (-0.5, -0.5),
        (0.5, -0.5),
        (0.0, 0.0),
        (-0.5 + quarter_pixel_x, 0.5 - quarter_pixel_y),
        (-0.5 + quarter_pixel_x + 1.0 / width_px, 0.5 - quarter_pixel_y),
        (-0.5 + quarter_pixel_x, 0.5 - quarter_pixel_y - 1.0 / height_px),
    ]

    mapped = _normalized_raster_points_to_camera(
        points,
        raster_width_mm=width_mm,
        raster_height_mm=height_mm,
        camera_center_mm=center,
    )

    assert np.asarray(mapped[:5]) == pytest.approx(
        np.asarray(
            [
                [x_min, y_max],
                [x_min + width_mm, y_max],
                [x_min, y_max - height_mm],
                [x_min + width_mm, y_max - height_mm],
                [center[0], center[1]],
            ]
        )
    )
    assert mapped[5] == pytest.approx(
        [x_min + 0.5 / (4.0 * pixels_per_mm), y_max - 0.5 / (4.0 * pixels_per_mm)]
    )
    assert mapped[6][0] - mapped[5][0] == pytest.approx(1.0 / pixels_per_mm)
    assert mapped[7][1] - mapped[5][1] == pytest.approx(-1.0 / pixels_per_mm)


def test_imported_and_camera_pixels_produce_equivalent_native_geometry(
    tmp_path: Path,
) -> None:
    pixels = _glyph_scene()
    source_path = tmp_path / "camera-equivalence.png"
    assert cv2.imwrite(str(source_path), pixels)
    payload = read_raster_asset_payload(source_path)
    width_mm = pixels.shape[1] / 4.0
    height_mm = pixels.shape[0] / 4.0
    raster_options = RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=128,
        invert=False,
        minimum_feature_area_mm2=0.50,
        smoothing_mm=0.0,
        simplification_tolerance_mm=0.25,
        contour_output=RasterContourOutput.ALL_CONTOURS,
    )
    imported = vectorize_raster_payload(
        payload,
        raster_options,
        displayed_width_mm=width_mm,
        displayed_height_mm=height_mm,
    )
    work_area = WorkArea(10.0, 165.0, 20.0, 95.0)
    previews = []
    camera = detect_objects(
        pixels,
        _trace_options(),
        work_area,
        4.0,
        raster_preview_callback=previews.append,
    )
    camera_center = (
        work_area.x_min + width_mm / 2.0,
        work_area.y_max - height_mm / 2.0,
    )

    imported_groups = _root_groups(imported.contours)
    imported_paths = []
    imported_hierarchies = []
    for group in imported_groups:
        imported_paths.append(
            transform_native_path(
                NativePathGeometry(
                    tuple(imported.contours[index].native_subpath for index in group),
                    fill_rule=PathFillRule.EVENODD,
                ),
                PathAffineTransform.from_components(
                    scale_x=width_mm,
                    scale_y=height_mm,
                ),
            )
        )
        index_map = {original: local for local, original in enumerate(group)}
        imported_hierarchies.append(
            [
                index_map.get(imported.contours[index].parent_index)
                for index in group
            ]
        )
    imported_order = sorted(
        range(len(imported_paths)),
        key=lambda index: (
            -sum(native_path_bounds(imported_paths[index])[1::2]) / 2.0,
            sum(native_path_bounds(imported_paths[index])[0::2]) / 2.0,
        ),
    )
    imported_paths = [imported_paths[index] for index in imported_order]
    imported_hierarchies = [imported_hierarchies[index] for index in imported_order]

    camera_source_keys = {
        item.diagnostics["pixel_source_key"] for item in camera.detections
    }
    assert len(camera_source_keys) == 1
    assert camera_source_keys != {imported.source_key}
    normalization_key = camera.diagnostics["camera_raster"]["normalization_key"]
    assert {
        item.diagnostics["camera_normalization_key"]
        for item in camera.detections
    } == {normalization_key}
    assert len(previews) == 1
    camera_mask = previews[0].foreground_mask > 0
    imported_mask = imported.foreground_mask > 0
    assert previews[0].contour_mask.shape == (
        pixels.shape[0] * 4,
        pixels.shape[1] * 4,
    )
    assert not previews[0].contour_mask.flags.writeable
    with pytest.raises(ValueError):
        previews[0].contour_mask.setflags(write=True)
    intersection = np.count_nonzero(camera_mask & imported_mask)
    union = np.count_nonzero(camera_mask | imported_mask)
    assert intersection / union > 0.98
    expected_mask_digest = hashlib.sha256(
        previews[0].foreground_mask.tobytes(order="C")
    ).hexdigest()
    assert {
        item.diagnostics["foreground_mask_sha256"] for item in camera.detections
    } == {expected_mask_digest}
    assert {
        item.diagnostics["foreground_pixel_count"] for item in camera.detections
    } == {int(np.count_nonzero(previews[0].foreground_mask))}
    assert imported.connected_component_count == camera.direct_count == 5
    assert len(imported_groups) == camera.direct_count
    assert len(imported.contours) == camera.detections[0].diagnostics["raw_contour_count"]
    timing = camera.diagnostics["timing"]
    assert timing["camera_normalization"]["background_estimation"]["calls"] == 1
    for stage in (
        "mask_preparation_total",
        "component_cleanup",
        "raster_4x_preparation",
        "contour_extraction",
        "native_fitting",
    ):
        assert timing["raster_vectorization"][stage]["calls"] >= 1
    assert timing["trace_detection_total_seconds"] > 0.0
    camera_candidates = []
    for detection in camera.detections:
        camera_path = transform_native_path(
            NativePathGeometry.from_dict(detection.native_path or {}),
            PathAffineTransform.from_components(
                scale_x=float(detection.native_width_mm),
                scale_y=float(detection.native_height_mm),
                translate_x=float(detection.native_center_mm[0]),
                translate_y=float(detection.native_center_mm[1]),
            ),
        )
        camera_local = transform_native_path(
            camera_path,
            PathAffineTransform.from_components(
                translate_x=-camera_center[0],
                translate_y=-camera_center[1],
            ),
        )
        camera_candidates.append((camera_local, detection))

    for imported_path, imported_parents in zip(
        imported_paths,
        imported_hierarchies,
        strict=True,
    ):
        imported_bounds = native_path_bounds(imported_path)
        imported_center = (
            (imported_bounds[0] + imported_bounds[2]) / 2.0,
            (imported_bounds[1] + imported_bounds[3]) / 2.0,
        )
        match_index = min(
            range(len(camera_candidates)),
            key=lambda index: math.dist(
                imported_center,
                (
                    sum(native_path_bounds(camera_candidates[index][0])[0::2]) / 2.0,
                    sum(native_path_bounds(camera_candidates[index][0])[1::2]) / 2.0,
                ),
            ),
        )
        camera_local, detection = camera_candidates.pop(match_index)
        assert native_path_bounds(camera_local) == pytest.approx(
            imported_bounds,
            abs=0.25,
        )
        assert imported_parents == detection.diagnostics["contour_parents"]
    assert not camera_candidates


def test_imported_and_normalized_camera_pixels_share_homogeneous_4x_guard(
    tmp_path: Path,
) -> None:
    grayscale = np.full((16, 16), 220, dtype=np.uint8)
    grayscale[2:9, 2:9] = 30
    grayscale[12:15, 12:15] = np.asarray(
        ((132, 108, 109), (113, 147, 132), (136, 144, 137)),
        dtype=np.uint8,
    )
    pixels = cv2.cvtColor(grayscale, cv2.COLOR_GRAY2BGR)
    normalization = normalize_camera_trace_frame(pixels, 4.0)
    normalized_pixels = cv2.cvtColor(
        normalization.dark_raster,
        cv2.COLOR_GRAY2BGR,
    )
    source_path = tmp_path / "camera-degenerate-equivalence.png"
    assert cv2.imwrite(str(source_path), normalized_pixels)
    payload = read_raster_asset_payload(source_path)
    raster_options = RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=225,
        invert=False,
        minimum_feature_area_mm2=0.05,
        smoothing_mm=0.0,
        simplification_tolerance_mm=0.10,
        contour_output=RasterContourOutput.ALL_CONTOURS,
    )
    imported = vectorize_raster_payload(
        payload,
        raster_options,
        displayed_width_mm=4.0,
        displayed_height_mm=4.0,
    )
    previews = []
    camera = detect_objects(
        pixels,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="manual",
            contrast_threshold=225,
            regular_grid=False,
            output_mode="native",
            min_area_mm2=0.05,
            max_area_mm2=100.0,
            min_width_mm=0.1,
            min_height_mm=0.1,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.10,
        ),
        WorkArea(0.0, 4.0, 0.0, 4.0),
        4.0,
        raster_preview_callback=previews.append,
    )

    assert imported.pruned_contour_count == 0
    assert imported.degenerate_contour_count == 0
    assert imported.rejected_contour_tree_count == 0
    assert imported.connected_component_count == camera.direct_count == 2
    assert len(imported.contours) == 2
    camera_source_keys = {
        item.diagnostics["pixel_source_key"] for item in camera.detections
    }
    assert camera_source_keys == {imported.source_key}
    normalization_key = camera.diagnostics["camera_raster"]["normalization_key"]
    assert normalization_key == normalization.diagnostics.normalization_key
    assert {
        item.diagnostics["camera_normalization_key"]
        for item in camera.detections
    } == {normalization_key}
    assert len(previews) == 1
    assert np.array_equal(previews[0].foreground_mask, imported.foreground_mask)
    assert len(
        {
            item.diagnostics["foreground_mask_sha256"]
            for item in camera.detections
        }
    ) == 1
    assert {
        item.diagnostics["pruned_contour_count"] for item in camera.detections
    } == {0}
    assert {
        item.diagnostics["degenerate_contour_count"] for item in camera.detections
    } == {0}
    assert [
        item.diagnostics["contour_parents"] for item in camera.detections
    ] == [[None], [None]]


def test_non_grid_contrast_supports_otsu_and_light_foreground() -> None:
    image = np.full((200, 200, 3), 30, dtype=np.uint8)
    cv2.circle(image, (100, 100), 45, (220, 220, 220), -1, lineType=cv2.LINE_AA)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="auto",
            contrast_invert=True,
            regular_grid=False,
            output_mode="native",
            min_area_mm2=1.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
            confidence_threshold=0.0,
        ),
        WorkArea(0.0, 50.0, 0.0, 50.0),
        4.0,
    )

    assert result.direct_count == 1
    assert result.detections[0].diagnostics["threshold_mode"] == "auto"
    assert result.detections[0].diagnostics["threshold_used"] is not None
    assert result.detections[0].diagnostics["invert"] is True


def test_bounded_auto_contrast_outer_silhouette_keeps_mask_holes_only_as_evidence() -> None:
    previews = []
    result = detect_objects(
        _auto_hole_scene(light_foreground=True),
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="auto",
            contrast_invert=True,
            regular_grid=False,
            output_mode="native",
            trace_detail="outer_silhouette",
            min_area_mm2=2.0,
            max_area_mm2=2_000.0,
            min_hole_area_mm2=2.0,
            max_hole_area_mm2=30.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.20,
        ),
        WorkArea(0.0, 60.0, 0.0, 40.0),
        4.0,
        raster_preview_callback=previews.append,
    )

    assert result.direct_count == 2
    assert result.options.trace_detail == "outer_silhouette"
    assert result.diagnostics["trace_detail"] == "outer_silhouette"
    assert result.diagnostics["outer_only"] is True
    assert len(previews) == 1
    _parents, depths = _contour_tree_shape(previews[0].contour_mask)
    assert sorted(depths) == [0, 0, 1]
    assert all(len(item.vector_contours_mm) == 1 for item in result.detections)
    assert all(
        len(NativePathGeometry.from_dict(item.native_path or {}).subpaths) == 1
        for item in result.detections
    )
    assert all(item.diagnostics["threshold_mode"] == "auto" for item in result.detections)
    assert all(item.diagnostics["threshold_used"] is not None for item in result.detections)


def test_light_two_level_camera_adapter_keeps_exact_hole_tree() -> None:
    image = np.full((400, 600, 3), 35, dtype=np.uint8)
    cv2.rectangle(image, (60, 70), (190, 180), (215, 215, 215), thickness=-1)
    cv2.circle(image, (360, 125), 60, (215, 215, 215), thickness=-1)
    cv2.circle(image, (360, 125), 25, (35, 35, 35), thickness=-1)
    previews = []

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="auto",
            contrast_invert=True,
            regular_grid=False,
            output_mode="native",
            min_area_mm2=50.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.25,
        ),
        WorkArea(0.0, 150.0, 0.0, 100.0),
        4.0,
        raster_preview_callback=previews.append,
    )

    assert result.direct_count == 2
    assert len(previews) == 1
    preview = previews[0]
    assert preview.polarity == "light"
    assert preview.threshold_used == 2
    assert preview.connected_component_count == 2
    contours, hierarchy = cv2.findContours(
        preview.contour_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    assert len(contours) == 3
    assert hierarchy is not None
    parents = hierarchy[0, :, 3].tolist()
    assert parents.count(-1) == 2
    assert sum(parent >= 0 for parent in parents) == 1


def test_max_area_is_a_post_vector_candidate_filter() -> None:
    image = np.full((220, 320, 3), 225, dtype=np.uint8)
    cv2.rectangle(image, (25, 25), (145, 145), (25, 25, 25), -1)
    cv2.rectangle(image, (230, 80), (270, 120), (25, 25, 25), -1)

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="manual",
            contrast_threshold=128,
            regular_grid=False,
            output_mode="native",
            min_area_mm2=1.0,
            max_area_mm2=150.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
            confidence_threshold=0.0,
        ),
        WorkArea(0.0, 80.0, 0.0, 55.0),
        4.0,
    )

    assert result.direct_count == 1
    assert result.detections[0].diagnostics["connected_component_count"] == 2
    assert result.detections[0].area_mm2 < 150.0


def test_explicit_color_retains_specialized_holes_despite_raster_hole_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("explicit Color must not enter shared raster cleanup")

    monkeypatch.setattr(
        "laser_aligner.vision.object_trace.vectorize_pixel_source_forest",
        fail,
    )
    background = (128, 128, 128)
    target = (220, 100, 161)
    image = np.full((180, 240, 3), background, dtype=np.uint8)
    cv2.rectangle(image, (45, 30), (195, 150), target, thickness=-1)
    cv2.rectangle(image, (90, 60), (150, 120), background, thickness=-1)
    previews = []

    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_bgr=target,
            target_hue=150.0,
            hue_tolerance=16.0,
            regular_grid=False,
            output_mode="native",
            min_area_mm2=5.0,
            max_area_mm2=3_000.0,
            min_hole_area_mm2=0.0,
            max_hole_area_mm2=1.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.20,
        ),
        WorkArea(0.0, 60.0, 0.0, 45.0),
        4.0,
        raster_preview_callback=previews.append,
    )

    assert result.mode_used == "color"
    assert result.direct_count == 1
    assert len(previews) == 1
    preview = previews[0]
    assert preview.strategy == "color"
    assert preview.contour_mask.shape == image.shape[:2]
    assert int(preview.contour_mask[90, 120]) == 0
    parents, depths = _contour_tree_shape(preview.contour_mask)
    assert parents == [-1, 0]
    assert depths == [0, 1]
    detection = result.detections[0]
    assert detection.native_verified
    assert detection.diagnostics["contour_parents"] == [None, 0]
    assert len(NativePathGeometry.from_dict(detection.native_path or {}).subpaths) == 2
    assert result.options.min_hole_area_mm2 == 0.0
    assert result.options.max_hole_area_mm2 == 1.0


def test_explicit_color_outer_silhouette_skips_washer_child_enumeration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_extract = object_trace_module.extract_foreground_contours
    retrieval_modes: list[int] = []

    def recording_extract(*args: object, **kwargs: object) -> object:
        retrieval_modes.append(int(kwargs.get("retrieval_mode", cv2.RETR_TREE)))
        return original_extract(*args, **kwargs)

    monkeypatch.setattr(object_trace_module, "extract_foreground_contours", recording_extract)
    background = (128, 128, 128)
    target = (220, 100, 161)
    image = np.full((300, 300, 3), background, dtype=np.uint8)
    cv2.circle(image, (150, 150), 60, target, thickness=-1, lineType=cv2.LINE_8)
    cv2.circle(image, (150, 150), 30, background, thickness=-1, lineType=cv2.LINE_8)
    common = {
        "detection_mode": "color",
        "target_bgr": target,
        "target_hue": 150.0,
        "hue_tolerance": 16.0,
        "regular_grid": False,
        "output_mode": "native",
        "min_area_mm2": 5.0,
        "max_area_mm2": 3_000.0,
        "min_width_mm": 1.0,
        "min_height_mm": 1.0,
        "confidence_threshold": 0.0,
        "native_fitting_tolerance_mm": 0.20,
    }
    work_area = WorkArea(0.0, 75.0, 0.0, 75.0)
    full = detect_objects(image, TraceOptions(**common), work_area, 4.0)
    retrieval_modes.clear()

    def fail_washer_enumeration(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Outer Color must not enumerate washer children")

    monkeypatch.setattr(
        object_trace_module,
        "_washer_candidates",
        fail_washer_enumeration,
    )
    previews = []
    outer = detect_objects(
        image,
        TraceOptions(
            **{
                **common,
                "trace_detail": "outer_silhouette",
                "border_offset_mm": 8.0,
            }
        ),
        work_area,
        4.0,
        raster_preview_callback=previews.append,
    )

    assert full.direct_count == outer.direct_count == 1
    full_washer = full.detections[0]
    outer_shape = outer.detections[0]
    assert full_washer.shape == "washer"
    assert outer_shape.shape != "washer"
    assert "hole_ratio" in full_washer.diagnostics
    assert "hole_ratio" not in outer_shape.diagnostics
    assert len(full_washer.vector_contours_mm) == 2
    assert len(NativePathGeometry.from_dict(full_washer.native_path or {}).subpaths) == 2
    assert len(outer_shape.vector_contours_mm) == 1
    assert len(NativePathGeometry.from_dict(outer_shape.native_path or {}).subpaths) == 1
    assert outer_shape.diagnostics["contour_parents"] == [None]
    assert outer_shape.diagnostics["contour_depths"] == [0]
    assert outer_shape.diagnostics["root_contour_count"] == 1
    assert outer_shape.diagnostics["output_contour_count"] == 1
    assert outer_shape.diagnostics["internal_contours_enumerated"] is False
    assert outer_shape.diagnostics["ignored_internal_contour_count"] is None
    assert retrieval_modes == [cv2.RETR_EXTERNAL]
    assert len(previews) == 1
    assert int(previews[0].contour_mask[150, 150]) == 0


def test_explicit_color_outer_silhouette_cancels_after_external_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = np.full((180, 240, 3), (128, 128, 128), dtype=np.uint8)
    target = (220, 100, 161)
    cv2.rectangle(image, (45, 30), (195, 150), target, thickness=-1)
    original_extract = object_trace_module.extract_foreground_contours
    extraction_finished = False

    def cancelling_extract(*args: object, **kwargs: object) -> object:
        nonlocal extraction_finished
        assert kwargs.get("retrieval_mode") == cv2.RETR_EXTERNAL
        result = original_extract(*args, **kwargs)
        extraction_finished = True
        return result

    monkeypatch.setattr(object_trace_module, "extract_foreground_contours", cancelling_extract)
    previews = []
    with pytest.raises(TraceDetectionCancelledError):
        detect_objects(
            image,
            TraceOptions(
                detection_mode="color",
                target_bgr=target,
                target_hue=150.0,
                hue_tolerance=16.0,
                regular_grid=False,
                output_mode="native",
                trace_detail="outer_silhouette",
                min_area_mm2=5.0,
                max_area_mm2=3_000.0,
                min_width_mm=1.0,
                min_height_mm=1.0,
                confidence_threshold=0.0,
            ),
            WorkArea(0.0, 60.0, 0.0, 45.0),
            4.0,
            raster_preview_callback=previews.append,
            cancel_check=lambda: extraction_finished,
        )

    assert extraction_finished is True
    assert previews
    assert all(preview.native_fitting_completed is False for preview in previews)


def test_grid_contrast_does_not_acquire_shared_raster_hole_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("grid contrast must retain specialized object detection")

    monkeypatch.setattr(
        "laser_aligner.vision.object_trace.vectorize_pixel_source_forest",
        fail,
    )
    image = np.full((360, 480, 3), 225, dtype=np.uint8)
    for row in range(2):
        for column in range(3):
            x, y = 30 + column * 145, 45 + row * 145
            cv2.rectangle(image, (x, y), (x + 100, y + 70), (35, 35, 35), -1)
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            regular_grid=True,
            trace_detail="outer_silhouette",
            min_area_mm2=40.0,
            min_hole_area_mm2=0.0,
            max_hole_area_mm2=1.0,
            min_width_mm=10.0,
            min_height_mm=8.0,
        ),
        WorkArea(0.0, 120.0, 0.0, 90.0),
        4.0,
    )

    assert result.grid is not None
    assert result.direct_count == 6
    assert result.options.min_hole_area_mm2 == 0.0
    assert result.options.max_hole_area_mm2 == 1.0
    assert result.options.trace_detail == "full"
    assert result.diagnostics["trace_detail"] == "full"
    assert result.diagnostics["outer_only"] is False
    assert all(item.diagnostics["mask_source"] != "raster_non_grid" for item in result.detections)


def test_grid_auto_does_not_acquire_shared_raster_hole_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("grid Auto must not enter the raster Auto forest")

    monkeypatch.setattr(
        "laser_aligner.vision.object_trace.vectorize_pixel_source_forest",
        fail,
    )
    image = np.full((360, 480, 3), 225, dtype=np.uint8)
    for row in range(2):
        for column in range(3):
            x, y = 30 + column * 145, 45 + row * 145
            cv2.rectangle(image, (x, y), (x + 100, y + 70), (35, 35, 35), -1)
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="auto",
            regular_grid=True,
            trace_detail="outer_silhouette",
            min_area_mm2=40.0,
            min_hole_area_mm2=0.0,
            max_hole_area_mm2=1.0,
            min_width_mm=10.0,
            min_height_mm=8.0,
        ),
        WorkArea(0.0, 120.0, 0.0, 90.0),
        4.0,
    )

    assert result.grid is not None
    assert result.direct_count == 6
    assert result.grid["observed_cells"] == 6
    assert result.options.min_hole_area_mm2 == 0.0
    assert result.options.max_hole_area_mm2 == 1.0
    assert result.options.trace_detail == "full"
    assert result.diagnostics["trace_detail"] == "full"
    assert result.diagnostics["outer_only"] is False
    assert all(item.diagnostics["mask_source"] != "raster_non_grid" for item in result.detections)
