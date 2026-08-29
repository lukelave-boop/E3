from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

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
from laser_aligner.vision.object_trace import (
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


def _numeric_path_values(value: object) -> list[float | str | bool]:
    if isinstance(value, Mapping):
        output: list[float | str | bool] = []
        for key in sorted(value):
            output.append(str(key))
            output.extend(_numeric_path_values(value[key]))
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        output = []
        for item in value:
            output.extend(_numeric_path_values(item))
        return output
    if isinstance(value, bool):
        return [value]
    if isinstance(value, (int, float)):
        return [float(value)]
    return [str(value)]


def _assert_paths_close(first: NativePathGeometry, second: NativePathGeometry) -> None:
    first_values = _numeric_path_values(first.to_dict())
    second_values = _numeric_path_values(second.to_dict())
    assert len(first_values) == len(second_values)
    for left, right in zip(first_values, second_values, strict=True):
        if isinstance(left, float) and isinstance(right, float):
            assert left == pytest.approx(right, abs=1e-9)
        else:
            assert left == right


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
    camera = detect_objects(pixels, _trace_options(), work_area, 4.0)
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

    assert {item.diagnostics["pixel_source_key"] for item in camera.detections} == {
        imported.source_key
    }
    expected_mask_digest = hashlib.sha256(
        imported.foreground_mask.tobytes(order="C")
    ).hexdigest()
    assert {
        item.diagnostics["foreground_mask_sha256"] for item in camera.detections
    } == {expected_mask_digest}
    assert {
        item.diagnostics["foreground_pixel_count"] for item in camera.detections
    } == {int(np.count_nonzero(imported.foreground_mask))}
    assert imported.connected_component_count == camera.direct_count == 5
    assert len(imported_groups) == camera.direct_count
    assert len(imported.contours) == camera.detections[0].diagnostics["raw_contour_count"]
    for imported_path, imported_parents, detection in zip(
        imported_paths,
        imported_hierarchies,
        camera.detections,
        strict=True,
    ):
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
        _assert_paths_close(imported_path, camera_local)
        assert imported_parents == detection.diagnostics["contour_parents"]
        assert [
            "".join(
                "L" if segment.__class__.__name__ == "PathLineSegment" else "C"
                for segment in subpath.segments
            )
            for subpath in imported_path.subpaths
        ] == detection.diagnostics["native_sequences"]


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


def test_grid_contrast_does_not_enter_raster_pixel_vectorizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("grid contrast must retain specialized object detection")

    monkeypatch.setattr(
        "laser_aligner.vision.object_trace.vectorize_pixel_source",
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
            min_area_mm2=40.0,
            min_width_mm=10.0,
            min_height_mm=8.0,
        ),
        WorkArea(0.0, 120.0, 0.0, 90.0),
        4.0,
    )

    assert result.grid is not None
    assert result.direct_count == 6
    assert all(item.diagnostics["mask_source"] != "raster_non_grid" for item in result.detections)
