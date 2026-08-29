from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

import laser_aligner.project.raster_vectorize as raster_vectorize_module
from laser_aligner.project import (
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationComplexityError,
    RasterVectorizationError,
    RasterVectorizationOptions,
    flatten_native_path,
    native_path_bounds,
    prepare_raster_vectorization_source,
    raster_payload_has_usable_alpha,
    read_raster_asset_payload,
    vectorize_prepared_raster,
    vectorize_raster_payload,
)


def _write_payload(path: Path, pixels: np.ndarray):
    assert cv2.imwrite(str(path), pixels)
    return read_raster_asset_payload(path)


def _manual_options(**changes: object) -> RasterVectorizationOptions:
    values: dict[str, object] = {
        "detection_mode": RasterDetectionMode.MANUAL_THRESHOLD,
        "threshold": 127,
        "minimum_feature_area_mm2": 0.0,
        "smoothing_mm": 0.0,
        "simplification_tolerance_mm": 0.10,
        "contour_output": RasterContourOutput.ALL_CONTOURS,
    }
    values.update(changes)
    return RasterVectorizationOptions(**values)


def _vectorize(
    payload,
    options: RasterVectorizationOptions | None = None,
    *,
    width_mm: float = 64.0,
    height_mm: float = 64.0,
):
    return vectorize_raster_payload(
        payload,
        options or _manual_options(),
        displayed_width_mm=width_mm,
        displayed_height_mm=height_mm,
    )


def _normalized_bounds(contour) -> tuple[float, float, float, float]:
    points = np.asarray(contour.preview_points, dtype=np.float64)
    return (
        float(np.min(points[:, 0])),
        float(np.min(points[:, 1])),
        float(np.max(points[:, 0])),
        float(np.max(points[:, 1])),
    )


def _closed_line_subpath(
    points: tuple[tuple[float, float], ...],
) -> PathSubpath:
    return PathSubpath(
        start=points[0],
        segments=tuple(
            PathLineSegment(to=point) for point in (*points[1:], points[0])
        ),
        closed=True,
    )


def _line_contour(
    points: tuple[tuple[float, float], ...],
    *,
    parent_index: int | None = None,
    depth: int = 0,
) -> raster_vectorize_module.RasterVectorizedContour:
    return raster_vectorize_module.RasterVectorizedContour(
        native_subpath=_closed_line_subpath(points),
        preview_points=points,
        parent_index=parent_index,
        depth=depth,
        is_hole=bool(depth % 2),
        raw_point_count=len(points),
        fitted_segment_count=len(points),
        preview_flattened_point_count=len(points),
        max_fitting_error_mm=0.0,
        smoothing_displacement_mm=0.0,
        max_estimated_deviation_mm=0.0,
    )


def _curved_frame_case(case: str) -> np.ndarray:
    pixels = np.full((64, 64, 3), 255, dtype=np.uint8)
    ellipses = {
        "left": ((20, 32), (20, 13), 0.0),
        "right": ((43, 32), (20, 13), 0.0),
        "top": ((32, 13), (20, 13), 0.0),
        "bottom": ((32, 50), (20, 13), 0.0),
        "corner": ((18, 18), (18, 18), 0.0),
        "excursion": ((-1, 18), (39, 23), 20.704841),
    }
    if case == "near-edge-hole":
        pixels[:] = 0
        cv2.ellipse(pixels, (10, 32), (9, 12), 0.0, 0.0, 360.0, 255, -1)
        return pixels
    center, axes, angle = ellipses[case]
    cv2.ellipse(pixels, center, axes, angle, 0.0, 360.0, 0, -1)
    return pixels


def _assert_authoritative_preview_and_planning(
    result,
    *,
    width_mm: float,
    height_mm: float,
    preview_tolerance_mm: float,
) -> None:
    transform = PathAffineTransform.from_components(
        scale_x=width_mm,
        scale_y=height_mm,
    )
    for contour in result.contours:
        geometry = NativePathGeometry((contour.native_subpath,))
        preview = np.asarray(
            flatten_native_path(
                geometry,
                preview_tolerance_mm,
                transform=transform,
            )[0],
            dtype=np.float64,
        )
        actual = np.asarray(contour.preview_points, dtype=np.float64) * (
            width_mm,
            height_mm,
        )
        assert actual == pytest.approx(preview[:-1])
        planning = np.asarray(
            flatten_native_path(geometry, 0.025, transform=transform)[0],
            dtype=np.float64,
        )
        assert planning[0] == pytest.approx(preview[0])
        assert planning[-1] == pytest.approx(planning[0])
        x_min, y_min, x_max, y_max = native_path_bounds(geometry, transform)
        assert -width_mm / 2.0 <= x_min <= x_max <= width_mm / 2.0
        assert -height_mm / 2.0 <= y_min <= y_max <= height_mm / 2.0
        assert np.all(np.abs(planning[:, 0]) <= width_mm / 2.0 + 1e-9)
        assert np.all(np.abs(planning[:, 1]) <= height_mm / 2.0 + 1e-9)


def _native_join_alignments(subpath: PathSubpath) -> list[float]:
    tangents: list[tuple[np.ndarray, np.ndarray]] = []
    start = np.asarray(subpath.start, dtype=np.float64)
    for segment in subpath.segments:
        end = np.asarray(segment.to, dtype=np.float64)
        if isinstance(segment, PathCubicSegment):
            outgoing = np.asarray(segment.control_1, dtype=np.float64) - start
            incoming = end - np.asarray(segment.control_2, dtype=np.float64)
        else:
            outgoing = end - start
            incoming = outgoing
        assert np.linalg.norm(outgoing) > 1e-12
        assert np.linalg.norm(incoming) > 1e-12
        tangents.append((outgoing, incoming))
        start = end
    alignments: list[float] = []
    for index, (_outgoing, incoming) in enumerate(tangents):
        following = tangents[(index + 1) % len(tangents)][0]
        alignments.append(
            float(
                np.dot(incoming, following)
                / (np.linalg.norm(incoming) * np.linalg.norm(following))
            )
        )
    return alignments


def test_solid_rectangle_uses_corner_preserving_few_point_geometry(
    tmp_path: Path,
) -> None:
    pixels = np.full((64, 64, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (12, 16), (51, 47), (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "rectangle.png", pixels)

    result = _vectorize(payload)

    assert len(result.contours) == 1
    contour = result.contours[0]
    assert contour.parent_index is None
    assert not contour.is_hole
    assert contour.native_subpath.closed
    assert all(
        isinstance(segment, PathLineSegment)
        for segment in contour.native_subpath.segments
    )
    assert 4 <= contour.preview_flattened_point_count <= 12
    assert contour.raw_point_count > contour.preview_flattened_point_count
    x_min, y_min, x_max, y_max = _normalized_bounds(contour)
    assert (x_min, x_max) == pytest.approx((-0.3125, 0.3125), abs=0.012)
    assert (y_min, y_max) == pytest.approx((-0.25, 0.25), abs=0.012)
    actual_points = np.asarray(contour.preview_points, dtype=np.float64)
    for expected_corner in (
        (-0.3125, -0.25),
        (0.3125, -0.25),
        (0.3125, 0.25),
        (-0.3125, 0.25),
    ):
        distances = np.linalg.norm(actual_points - expected_corner, axis=1)
        assert float(np.min(distances)) < 0.02
    assert result.raw_contour_point_count == contour.raw_point_count
    assert result.fitted_segment_count == contour.fitted_segment_count
    assert (
        result.preview_flattened_point_count
        == contour.preview_flattened_point_count
    )
    assert result.source_rgba.shape == (64, 64, 4)
    assert result.foreground_mask.shape == (64, 64)
    assert result.overlay_rgba.shape == (64, 64, 4)
    assert np.any(result.overlay_rgba != result.source_rgba)


def test_auto_threshold_reports_the_otsu_value_and_foreground(tmp_path: Path) -> None:
    pixels = np.full((56, 72, 3), 235, dtype=np.uint8)
    cv2.rectangle(pixels, (16, 12), (55, 43), (24, 24, 24), thickness=-1)
    payload = _write_payload(tmp_path / "auto-threshold.png", pixels)

    result = _vectorize(
        payload,
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.AUTO_THRESHOLD,
            minimum_feature_area_mm2=0.0,
            simplification_tolerance_mm=0.1,
        ),
        width_mm=36.0,
        height_mm=28.0,
    )

    assert result.threshold_used is not None
    assert 24 <= result.threshold_used < 235
    assert result.connected_component_count == 1
    assert len(result.contours) == 1


def test_transparent_background_silhouette_uses_exact_alpha_payload(
    tmp_path: Path,
) -> None:
    pixels = np.zeros((48, 64, 4), dtype=np.uint8)
    pixels[8:40, 14:50, :3] = (40, 120, 210)
    pixels[8:40, 14:50, 3] = 255
    payload = _write_payload(tmp_path / "alpha-silhouette.png", pixels)
    options = RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.ALPHA,
        alpha_cutoff=128,
        minimum_feature_area_mm2=0.0,
        simplification_tolerance_mm=0.08,
    )

    assert raster_payload_has_usable_alpha(payload)
    source = prepare_raster_vectorization_source(payload)
    result = vectorize_prepared_raster(
        source,
        options,
        displayed_width_mm=64.0,
        displayed_height_mm=48.0,
    )

    assert source.has_usable_alpha
    assert result.has_usable_alpha
    assert result.threshold_used is None
    assert len(result.contours) == 1
    assert result.source_sha256 == payload.identity.sha256


def test_inverted_black_white_artwork_selects_light_foreground(
    tmp_path: Path,
) -> None:
    pixels = np.zeros((60, 80, 3), dtype=np.uint8)
    cv2.rectangle(pixels, (20, 15), (59, 44), (255, 255, 255), thickness=-1)
    payload = _write_payload(tmp_path / "inverted.png", pixels)

    result = _vectorize(
        payload,
        _manual_options(invert=True),
        width_mm=80.0,
        height_mm=60.0,
    )

    assert len(result.contours) == 1
    x_min, y_min, x_max, y_max = _normalized_bounds(result.contours[0])
    assert x_min == pytest.approx(-0.25, abs=0.012)
    assert x_max == pytest.approx(0.25, abs=0.012)
    assert y_min == pytest.approx(-0.25, abs=0.012)
    assert y_max == pytest.approx(0.25, abs=0.012)


def test_donut_preserves_hole_hierarchy_and_outer_only_choice(
    tmp_path: Path,
) -> None:
    pixels = np.full((96, 96, 3), 255, dtype=np.uint8)
    cv2.circle(pixels, (48, 48), 34, (0, 0, 0), thickness=-1)
    cv2.circle(pixels, (48, 48), 15, (255, 255, 255), thickness=-1)
    payload = _write_payload(tmp_path / "donut.png", pixels)

    all_contours = _vectorize(
        payload,
        _manual_options(simplification_tolerance_mm=0.18),
        width_mm=48.0,
        height_mm=48.0,
    )
    outer_only = _vectorize(
        payload,
        _manual_options(
            contour_output=RasterContourOutput.OUTER_ONLY,
            simplification_tolerance_mm=0.18,
        ),
        width_mm=48.0,
        height_mm=48.0,
    )

    assert len(all_contours.contours) == 2
    outer, hole = all_contours.contours
    assert outer.parent_index is None and outer.depth == 0 and not outer.is_hole
    assert hole.parent_index == 0 and hole.depth == 1 and hole.is_hole
    project_path = all_contours.project_path_geometry()
    assert isinstance(project_path, NativePathGeometry)
    assert project_path.fill_rule is PathFillRule.EVENODD
    assert len(project_path.subpaths) == 2
    assert all(subpath.closed for subpath in project_path.subpaths)
    assert all_contours.metadata()["raster_vectorization_hierarchy"] == [
        {"parent_index": None, "depth": 0, "is_hole": False},
        {"parent_index": 0, "depth": 1, "is_hole": True},
    ]
    assert len(outer_only.contours) == 1
    assert not outer_only.contours[0].is_hole


def test_letter_like_shape_preserves_multiple_counters(tmp_path: Path) -> None:
    pixels = np.full((120, 90, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (15, 10), (74, 109), (0, 0, 0), thickness=-1)
    cv2.rectangle(pixels, (35, 25), (61, 48), (255, 255, 255), thickness=-1)
    cv2.rectangle(pixels, (35, 69), (61, 94), (255, 255, 255), thickness=-1)
    payload = _write_payload(tmp_path / "letter-b.png", pixels)

    result = _vectorize(
        payload,
        width_mm=45.0,
        height_mm=60.0,
    )

    assert len(result.contours) == 3
    assert [contour.depth for contour in result.contours] == [0, 1, 1]
    assert [contour.parent_index for contour in result.contours] == [None, 0, 0]
    assert [contour.is_hole for contour in result.contours] == [False, True, True]


@pytest.mark.parametrize(
    ("glyph", "expected_depths"),
    [
        ("A", [0, 1]),
        ("O", [0, 1]),
        ("P", [0, 1]),
        ("R", [0, 1]),
        ("8", [0, 1, 1]),
    ],
)
def test_letter_like_native_paths_preserve_counter_topology(
    tmp_path: Path,
    glyph: str,
    expected_depths: list[int],
) -> None:
    pixels = np.full((128, 128, 3), 255, dtype=np.uint8)
    if glyph == "A":
        cv2.fillPoly(
            pixels,
            [np.asarray([[64, 8], [112, 116], [16, 116]], dtype=np.int32)],
            (0, 0, 0),
        )
        cv2.fillPoly(
            pixels,
            [np.asarray([[64, 42], [78, 78], [50, 78]], dtype=np.int32)],
            (255, 255, 255),
        )
    elif glyph == "O":
        cv2.circle(pixels, (64, 64), 51, (0, 0, 0), thickness=-1)
        cv2.circle(pixels, (64, 64), 27, (255, 255, 255), thickness=-1)
    elif glyph in {"P", "R"}:
        cv2.rectangle(pixels, (24, 12), (94, 72), (0, 0, 0), thickness=-1)
        cv2.rectangle(pixels, (24, 12), (45, 116), (0, 0, 0), thickness=-1)
        cv2.rectangle(pixels, (48, 29), (76, 53), (255, 255, 255), thickness=-1)
        if glyph == "R":
            cv2.line(pixels, (66, 66), (104, 116), (0, 0, 0), thickness=20)
    else:
        cv2.circle(pixels, (64, 38), 34, (0, 0, 0), thickness=-1)
        cv2.circle(pixels, (64, 90), 34, (0, 0, 0), thickness=-1)
        cv2.rectangle(pixels, (30, 38), (98, 90), (0, 0, 0), thickness=-1)
        cv2.circle(pixels, (64, 38), 15, (255, 255, 255), thickness=-1)
        cv2.circle(pixels, (64, 90), 15, (255, 255, 255), thickness=-1)
    payload = _write_payload(tmp_path / f"glyph-{glyph}.png", pixels)

    result = _vectorize(
        payload,
        _manual_options(
            smoothing_mm=0.08,
            simplification_tolerance_mm=0.12,
        ),
        width_mm=64.0,
        height_mm=64.0,
    )

    assert [contour.depth for contour in result.contours] == expected_depths
    assert [contour.parent_index for contour in result.contours] == [
        None,
        *([0] * (len(expected_depths) - 1)),
    ]
    assert [contour.is_hole for contour in result.contours] == [
        False,
        *([True] * (len(expected_depths) - 1)),
    ]
    geometry = result.project_path_geometry()
    assert geometry.fill_rule is PathFillRule.EVENODD
    assert len(geometry.subpaths) == len(expected_depths)
    assert all(subpath.closed for subpath in geometry.subpaths)


def test_multiple_nested_islands_preserve_three_level_hierarchy(
    tmp_path: Path,
) -> None:
    pixels = np.full((128, 128, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (8, 8), (119, 119), (0, 0, 0), thickness=-1)
    cv2.rectangle(pixels, (28, 28), (99, 99), (255, 255, 255), thickness=-1)
    cv2.rectangle(pixels, (48, 48), (79, 79), (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "nested-island.png", pixels)

    result = _vectorize(payload, width_mm=64.0, height_mm=64.0)

    assert [contour.depth for contour in result.contours] == [0, 1, 2]
    assert [contour.parent_index for contour in result.contours] == [None, 0, 1]
    assert [contour.is_hole for contour in result.contours] == [False, True, False]
    assert len(result.project_path_geometry().subpaths) == 3


def test_minimum_feature_area_removes_isolated_speck(tmp_path: Path) -> None:
    pixels = np.full((100, 100, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (20, 20), (79, 79), (0, 0, 0), thickness=-1)
    pixels[4, 4] = 0
    payload = _write_payload(tmp_path / "speck.png", pixels)

    unfiltered = _vectorize(
        payload,
        _manual_options(minimum_feature_area_mm2=0.0),
        width_mm=100.0,
        height_mm=100.0,
    )
    filtered = _vectorize(
        payload,
        _manual_options(minimum_feature_area_mm2=2.0),
        width_mm=100.0,
        height_mm=100.0,
    )

    assert unfiltered.connected_component_count == 2
    assert len(unfiltered.contours) == 2
    assert filtered.connected_component_count == 1
    assert len(filtered.contours) == 1


def test_minimum_feature_area_removes_tiny_pinhole_but_keeps_larger_hole(
    tmp_path: Path,
) -> None:
    pixels = np.full((100, 100, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (10, 10), (89, 89), (0, 0, 0), thickness=-1)
    cv2.rectangle(pixels, (23, 23), (24, 24), (255, 255, 255), thickness=-1)
    cv2.circle(pixels, (62, 55), 12, (255, 255, 255), thickness=-1)
    payload = _write_payload(tmp_path / "pinholes.png", pixels)

    unfiltered = _vectorize(
        payload,
        _manual_options(minimum_feature_area_mm2=0.0),
        width_mm=40.0,
        height_mm=40.0,
    )
    filtered = _vectorize(
        payload,
        _manual_options(minimum_feature_area_mm2=1.0),
        width_mm=40.0,
        height_mm=40.0,
    )

    assert len(unfiltered.contours) == 3
    assert len(filtered.contours) == 2
    assert [contour.is_hole for contour in filtered.contours] == [False, True]


def test_smoothing_and_tolerance_reduce_points_while_curves_remain_adaptive(
    tmp_path: Path,
) -> None:
    pixels = np.full((128, 128, 3), 255, dtype=np.uint8)
    center = np.array([64.0, 64.0])
    polygon = []
    for index in range(96):
        angle = 2.0 * np.pi * index / 96.0
        radius = 42.0 + (2.0 if index % 2 else -2.0)
        point = center + radius * np.array([np.cos(angle), np.sin(angle)])
        polygon.append([int(round(point[0])), int(round(point[1]))])
    cv2.fillPoly(pixels, [np.asarray(polygon, dtype=np.int32)], (0, 0, 0))
    payload = _write_payload(tmp_path / "rough-circle.png", pixels)

    detailed = _vectorize(
        payload,
        _manual_options(simplification_tolerance_mm=0.03),
        width_mm=64.0,
        height_mm=64.0,
    )
    smoothed = _vectorize(
        payload,
        _manual_options(
            smoothing_mm=0.35,
            simplification_tolerance_mm=0.30,
        ),
        width_mm=64.0,
        height_mm=64.0,
    )

    assert (
        smoothed.preview_flattened_point_count
        < detailed.preview_flattened_point_count
    )
    assert smoothed.raw_contour_point_count == detailed.raw_contour_point_count
    assert smoothed.fitted_segment_count <= detailed.fitted_segment_count
    assert smoothed.preview_flattened_point_count > 8
    assert smoothed.max_estimated_deviation_mm >= 0.0


def test_curved_silhouette_retains_native_cubics_with_fewer_segments(
    tmp_path: Path,
) -> None:
    pixels = np.full((128, 128, 3), 255, dtype=np.uint8)
    cv2.circle(pixels, (64, 64), 43, (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "native-circle.png", pixels)

    result = _vectorize(
        payload,
        _manual_options(
            smoothing_mm=0.10,
            simplification_tolerance_mm=0.12,
        ),
        width_mm=64.0,
        height_mm=64.0,
    )

    contour = result.contours[0]
    assert any(
        isinstance(segment, PathCubicSegment)
        for segment in contour.native_subpath.segments
    )
    assert (
        contour.fitted_segment_count * 4
        < contour.preview_flattened_point_count * 3
    )
    assert result.project_path_geometry().segment_count == contour.fitted_segment_count


def test_zero_smoothing_circle_suppresses_false_corners_and_keeps_g1_joins(
    tmp_path: Path,
) -> None:
    pixels = np.full((128, 128, 3), 255, dtype=np.uint8)
    cv2.circle(
        pixels,
        (64, 64),
        43,
        (0, 0, 0),
        thickness=-1,
        lineType=cv2.LINE_AA,
    )
    payload = _write_payload(tmp_path / "zero-smoothing-circle.png", pixels)

    result = _vectorize(
        payload,
        _manual_options(
            smoothing_mm=0.0,
            simplification_tolerance_mm=0.12,
        ),
        width_mm=64.0,
        height_mm=64.0,
    )

    contour = result.contours[0]
    line_count = sum(
        isinstance(segment, PathLineSegment)
        for segment in contour.native_subpath.segments
    )
    cubic_count = sum(
        isinstance(segment, PathCubicSegment)
        for segment in contour.native_subpath.segments
    )
    assert line_count <= 4
    assert cubic_count > line_count * 10
    assert min(_native_join_alignments(contour.native_subpath)) > 1.0 - 1e-12


def test_raster_contour_allows_cubic_controls_outside_visible_frame() -> None:
    preview = ((-0.4, -0.3), (0.4, -0.3), (0.4, 0.3), (-0.4, 0.3))
    native = PathSubpath(
        start=(-0.4, 0.0),
        segments=(
            PathCubicSegment(
                control_1=(0.75, -0.5),
                control_2=(0.75, 0.5),
                to=(-0.4, 0.0),
            ),
        ),
        closed=True,
    )

    contour = raster_vectorize_module.RasterVectorizedContour(
        native_subpath=native,
        preview_points=preview,
        parent_index=None,
        depth=0,
        is_hole=False,
        raw_point_count=4,
        fitted_segment_count=1,
        preview_flattened_point_count=4,
        max_fitting_error_mm=0.0,
        smoothing_displacement_mm=0.0,
        max_estimated_deviation_mm=0.0,
    )

    segment = contour.native_subpath.segments[0]
    assert isinstance(segment, PathCubicSegment)
    assert segment.control_1[0] > 0.5
    raster_vectorize_module._validate_native_subpath_in_frame(native, 100.0, 20.0)
    assert native_path_bounds(
        NativePathGeometry((native,)),
        PathAffineTransform.from_components(scale_x=100.0, scale_y=20.0),
    )[2] < 50.0
    raster_vectorize_module._validate_authoritative_native_topology(
        (contour,),
        100.0,
        20.0,
    )


def test_tangent_constrained_fit_avoids_anisotropic_frame_excursion(
    tmp_path: Path,
) -> None:
    pixels = np.full((64, 64, 3), 255, dtype=np.uint8)
    cv2.ellipse(
        pixels,
        (-1, 18),
        (39, 23),
        20.704841,
        0.0,
        360.0,
        (0, 0, 0),
        thickness=-1,
    )
    payload = _write_payload(tmp_path / "edge-excursion.png", pixels)

    options = _manual_options(
        smoothing_mm=1.5,
        simplification_tolerance_mm=2.0,
    )
    result = _vectorize(payload, options, width_mm=96.0, height_mm=24.0)

    _assert_authoritative_preview_and_planning(
        result,
        width_mm=96.0,
        height_mm=24.0,
        preview_tolerance_mm=options.simplification_tolerance_mm * 0.35,
    )


def test_edge_touching_preview_is_authoritative_native_flattening(
    tmp_path: Path,
) -> None:
    pixels = np.full((64, 64, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (0, 8), (42, 55), (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "edge-touch.png", pixels)
    options = _manual_options(simplification_tolerance_mm=0.1)

    result = _vectorize(payload, options, width_mm=96.0, height_mm=24.0)

    contour = result.contours[0]
    transform = PathAffineTransform.from_components(scale_x=96.0, scale_y=24.0)
    expected = np.asarray(
        flatten_native_path(
            NativePathGeometry((contour.native_subpath,)),
            options.simplification_tolerance_mm * 0.35,
            transform=transform,
        )[0][:-1],
        dtype=np.float64,
    )
    actual = np.asarray(contour.preview_points, dtype=np.float64) * (96.0, 24.0)
    assert actual == pytest.approx(expected)
    x_min, y_min, x_max, y_max = native_path_bounds(
        NativePathGeometry((contour.native_subpath,)),
        transform,
    )
    assert x_min >= -48.0
    assert x_max <= 48.0
    assert y_min >= -12.0
    assert y_max <= 12.0


@pytest.mark.parametrize(
    ("case", "smoothing_mm", "tolerance_mm"),
    [
        ("left", 0.05, 0.25),
        ("right", 0.05, 0.25),
        ("top", 0.05, 0.25),
        ("bottom", 0.05, 0.25),
        ("corner", 0.05, 0.25),
        ("near-edge-hole", 0.15, 0.75),
        ("excursion", 1.5, 2.0),
    ],
)
def test_curved_frame_positions_use_one_authoritative_geometry_sequence(
    tmp_path: Path,
    case: str,
    smoothing_mm: float,
    tolerance_mm: float,
) -> None:
    payload = _write_payload(tmp_path / f"curved-{case}.png", _curved_frame_case(case))
    options = _manual_options(
        smoothing_mm=smoothing_mm,
        simplification_tolerance_mm=tolerance_mm,
    )
    result = _vectorize(payload, options, width_mm=96.0, height_mm=24.0)
    _assert_authoritative_preview_and_planning(
        result,
        width_mm=96.0,
        height_mm=24.0,
        preview_tolerance_mm=tolerance_mm * 0.35,
    )


def test_tangent_constrained_fit_avoids_fitted_hole_excursion(
    tmp_path: Path,
) -> None:
    pixels = np.full((64, 64, 3), 255, dtype=np.uint8)
    outer = np.asarray(
        [
            [58, 39],
            [43, 54],
            [31, 62],
            [29, 57],
            [24, 62],
            [24, 58],
            [16, 51],
            [13, 53],
            [3, 31],
            [44, 5],
            [60, 21],
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(pixels, [outer], (0, 0, 0))
    cv2.ellipse(
        pixels,
        (36, 31),
        (16, 17),
        130.65926726215258,
        0.0,
        360.0,
        (255, 255, 255),
        thickness=-1,
    )
    payload = _write_payload(tmp_path / "hole-excursion.png", pixels)

    options = _manual_options(
        smoothing_mm=5.0,
        simplification_tolerance_mm=5.0,
    )
    result = _vectorize(payload, options, width_mm=96.0, height_mm=24.0)

    assert len(result.contours) == 2
    assert result.contours[1].parent_index == 0
    assert result.contours[1].is_hole
    _assert_authoritative_preview_and_planning(
        result,
        width_mm=96.0,
        height_mm=24.0,
        preview_tolerance_mm=options.simplification_tolerance_mm * 0.35,
    )


@pytest.mark.parametrize(
    "contours",
    [
        (
            _line_contour(
                ((-0.4, -0.4), (0.4, 0.4), (-0.4, 0.4), (0.4, -0.4))
            ),
        ),
        (
            _line_contour(((-0.4, -0.4), (0.1, -0.4), (0.1, 0.1), (-0.4, 0.1))),
            _line_contour(((-0.1, -0.1), (0.4, -0.1), (0.4, 0.4), (-0.1, 0.4))),
        ),
    ],
    ids=("self-crossing", "overlapping-siblings"),
)
def test_authoritative_topology_rejects_self_and_sibling_ambiguity(
    contours: tuple[raster_vectorize_module.RasterVectorizedContour, ...],
) -> None:
    with pytest.raises(RasterVectorizationError, match="topology.*ambiguous"):
        raster_vectorize_module._validate_authoritative_native_topology(
            contours,
            100.0,
            100.0,
        )


def test_authoritative_topology_rejects_self_loop_inside_one_flatten_leaf() -> None:
    start = (-0.4, -0.4)
    physical_scale_mm = 0.0005
    normalized_scale = physical_scale_mm / 100.0
    loop = PathCubicSegment(
        control_1=(
            start[0] - 0.25333333333333335 * normalized_scale,
            start[1] - normalized_scale / 3.0,
        ),
        control_2=(
            start[0] - 0.5066666666666667 * normalized_scale,
            start[1] - normalized_scale / 3.0,
        ),
        to=(start[0] + 0.24 * normalized_scale, start[1]),
    )
    native = PathSubpath(
        start=start,
        segments=(
            loop,
            PathLineSegment((0.4, -0.4)),
            PathLineSegment((0.4, 0.4)),
            PathLineSegment((-0.4, 0.4)),
            PathLineSegment(start),
        ),
        closed=True,
    )
    contour = dataclasses.replace(
        _line_contour((start, (0.4, -0.4), (0.4, 0.4), (-0.4, 0.4))),
        native_subpath=native,
        fitted_segment_count=len(native.segments),
    )
    transform = PathAffineTransform.from_components(scale_x=100.0, scale_y=100.0)
    flattened = flatten_native_path(
        NativePathGeometry((native,)),
        raster_vectorize_module._NATIVE_TOPOLOGY_MIN_TOLERANCE_MM,
        transform=transform,
    )[0]
    assert len(flattened) == 6  # The tiny loop was accepted as one flat chord.

    with pytest.raises(RasterVectorizationError, match="within a cubic"):
        raster_vectorize_module._validate_authoritative_native_topology(
            (contour,),
            100.0,
            100.0,
        )


def test_authoritative_topology_uses_float64_for_large_coordinate_containment() -> None:
    outer_points = ((-0.49, -0.49), (0.49, -0.49), (0.49, 0.49), (-0.49, 0.49))
    hole_points = (
        (0.4899999998, -0.00000000005),
        (0.4899999999, -0.00000000005),
        (0.4899999999, 0.00000000005),
        (0.4899999998, 0.00000000005),
    )
    dimension_mm = 1_000_000_000_000.0
    assert np.float32(outer_points[1][0] * dimension_mm) == np.float32(
        hole_points[0][0] * dimension_mm
    )

    raster_vectorize_module._validate_authoritative_native_topology(
        (
            _line_contour(outer_points),
            _line_contour(hole_points, parent_index=0, depth=1),
        ),
        dimension_mm,
        dimension_mm,
    )


def test_authoritative_topology_rejects_hidden_adjacent_cubic_crossing() -> None:
    start = (-0.4, -0.4)
    width_mm = height_mm = 100.0
    horizontal_mm = 0.0005
    vertical_mm = 0.0002
    dx = horizontal_mm / width_mm
    dy = vertical_mm / height_mm
    shared = (start[0] + dx, start[1])
    native = PathSubpath(
        start=start,
        segments=(
            PathCubicSegment(
                (start[0] + dx / 3.0, start[1]),
                (start[0] + 2.0 * dx / 3.0, start[1]),
                shared,
            ),
            PathCubicSegment(
                (shared[0] - dx / 3.0, shared[1] + dy),
                (shared[0] - 2.0 * dx / 3.0, shared[1] + dy),
                (start[0], start[1] - dy),
            ),
            PathLineSegment((0.4, start[1] - dy)),
            PathLineSegment((0.4, 0.4)),
            PathLineSegment((-0.4, 0.4)),
            PathLineSegment(start),
        ),
        closed=True,
    )
    contour = dataclasses.replace(
        _line_contour((start, (0.4, -0.4), (0.4, 0.4), (-0.4, 0.4))),
        native_subpath=native,
        fitted_segment_count=len(native.segments),
    )
    flattened = flatten_native_path(
        NativePathGeometry((native,)),
        raster_vectorize_module._NATIVE_TOPOLOGY_MIN_TOLERANCE_MM,
        transform=PathAffineTransform.from_components(
            scale_x=width_mm,
            scale_y=height_mm,
        ),
    )[0]
    assert len(flattened) == 7  # Both crossing cubics were accepted as one chord.

    with pytest.raises(RasterVectorizationError, match="between adjacent native arcs"):
        raster_vectorize_module._validate_authoritative_native_topology(
            (contour,),
            width_mm,
            height_mm,
        )


def test_vectorization_does_not_import_or_depend_on_camera_trace_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pixels = np.full((40, 40, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (8, 9), (31, 30), (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "independent.png", pixels)
    module_name = "laser_aligner.vision.object_trace"
    monkeypatch.delitem(sys.modules, module_name, raising=False)

    first = _vectorize(payload, width_mm=20.0, height_mm=20.0)
    second = _vectorize(payload, width_mm=20.0, height_mm=20.0)

    assert module_name not in sys.modules
    assert first.contours == second.contours
    assert first.threshold_used == second.threshold_used


def test_source_identity_mismatch_is_rejected_before_decode(tmp_path: Path) -> None:
    path = tmp_path / "changed.png"
    first = np.full((32, 32, 3), 255, dtype=np.uint8)
    cv2.rectangle(first, (6, 6), (25, 25), (0, 0, 0), thickness=-1)
    payload = _write_payload(path, first)
    replacement = np.zeros((32, 32, 3), dtype=np.uint8)
    assert cv2.imwrite(str(path), replacement)

    with pytest.raises(RasterVectorizationError, match="identity mismatch"):
        _vectorize(payload, width_mm=32.0, height_mm=32.0)


def test_bounded_component_complexity_recommends_actionable_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pixels = np.full((60, 60, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (5, 5), (15, 15), (0, 0, 0), thickness=-1)
    cv2.rectangle(pixels, (40, 40), (50, 50), (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "components.png", pixels)
    monkeypatch.setattr(
        raster_vectorize_module,
        "MAX_RASTER_VECTORIZATION_CONNECTED_COMPONENTS",
        1,
    )

    with pytest.raises(RasterVectorizationComplexityError) as captured:
        _vectorize(payload, width_mm=30.0, height_mm=30.0)

    message = str(captured.value).casefold()
    assert "connected foreground components" in message
    assert "increase the minimum feature size" in message
    assert "increase simplification" in message
    assert "adjust the threshold" in message
    assert "cleaner source artwork" in message


@pytest.mark.parametrize(
    ("constant_name", "limit", "message"),
    [
        ("MAX_RASTER_VECTORIZATION_OVERSAMPLED_PIXELS", 100, "internal limit"),
        ("MAX_RASTER_VECTORIZATION_CONTOURS", 1, "contour limit"),
        (
            "MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION",
            100,
            "pre-simplification limit",
        ),
        ("MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS", 1, "fitted segments"),
        (
            "MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION",
            2,
            "preview-flattened points",
        ),
    ],
)
def test_each_geometry_complexity_budget_rejects_with_cleanup_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant_name: str,
    limit: int,
    message: str,
) -> None:
    pixels = np.full((64, 64, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (5, 7), (24, 29), (0, 0, 0), thickness=-1)
    cv2.rectangle(pixels, (37, 34), (57, 56), (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / f"limit-{constant_name}.png", pixels)
    monkeypatch.setattr(raster_vectorize_module, constant_name, limit)

    with pytest.raises(RasterVectorizationComplexityError) as captured:
        _vectorize(payload, width_mm=32.0, height_mm=32.0)

    text = str(captured.value).casefold()
    assert message.casefold() in text
    assert "increase the minimum feature size" in text
    assert "increase simplification" in text
    assert "adjust the threshold" in text
    assert "cleaner source artwork" in text


def test_raw_edge_preflight_rejects_before_oversampled_contour_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pixels = np.full((80, 80, 3), 255, dtype=np.uint8)
    for row in range(8, 72, 4):
        end = 70 if (row // 4) % 2 else 10
        cv2.line(pixels, (10, row), (end, row), (0, 0, 0), thickness=2)
        if row < 68:
            cv2.line(pixels, (end, row), (end, row + 4), (0, 0, 0), thickness=2)
    payload = _write_payload(tmp_path / "single-maze.png", pixels)
    monkeypatch.setattr(
        raster_vectorize_module,
        "MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION",
        500,
    )
    monkeypatch.setattr(
        raster_vectorize_module,
        "_oversampled_mask",
        lambda *_args, **_kwargs: pytest.fail(
            "base edge budget must reject before the 4x allocation"
        ),
    )

    with pytest.raises(RasterVectorizationComplexityError, match="4x contour"):
        _vectorize(payload, width_mm=40.0, height_mm=40.0)


def test_corner_suppression_rejects_a_large_regular_staircase() -> None:
    count = 20_000
    points = np.column_stack(
        (
            np.arange(count, dtype=np.float64),
            np.arange(count, dtype=np.float64) % 2.0,
        )
    )

    corners = raster_vectorize_module._corner_indices(points)

    assert corners == []


def test_rasterized_topology_validation_rejects_a_hole_crossing_its_parent() -> None:
    contour_type = raster_vectorize_module.RasterVectorizedContour
    outer_points = ((-0.4, -0.4), (0.4, -0.4), (0.4, 0.4), (-0.4, 0.4))
    outer = contour_type(
        native_subpath=_closed_line_subpath(outer_points),
        preview_points=outer_points,
        parent_index=None,
        depth=0,
        is_hole=False,
        raw_point_count=4,
        fitted_segment_count=4,
        preview_flattened_point_count=4,
        max_fitting_error_mm=0.0,
        smoothing_displacement_mm=0.0,
        max_estimated_deviation_mm=0.0,
    )
    hole_points = ((-0.2, -0.2), (0.2, -0.2), (0.2, 0.2), (-0.2, 0.2))
    valid_hole = contour_type(
        native_subpath=_closed_line_subpath(hole_points),
        preview_points=hole_points,
        parent_index=0,
        depth=1,
        is_hole=True,
        raw_point_count=4,
        fitted_segment_count=4,
        preview_flattened_point_count=4,
        max_fitting_error_mm=0.0,
        smoothing_displacement_mm=0.0,
        max_estimated_deviation_mm=0.0,
    )
    crossing_points = (
        (-0.2, -0.2),
        (0.48, -0.2),
        (0.48, 0.2),
        (-0.2, 0.2),
    )
    crossing_hole = dataclasses.replace(
        valid_hole,
        native_subpath=_closed_line_subpath(crossing_points),
        preview_points=crossing_points,
    )

    raster_vectorize_module._validate_rasterized_topology(
        (outer, valid_hole),
        (256, 256),
    )
    with pytest.raises(RasterVectorizationError, match="topology"):
        raster_vectorize_module._validate_rasterized_topology(
            (outer, crossing_hole),
            (256, 256),
        )


@pytest.mark.parametrize("suffix", [".jpg", ".bmp"])
def test_vectorization_uses_the_shared_bounded_payload_for_other_raster_formats(
    tmp_path: Path,
    suffix: str,
) -> None:
    pixels = np.full((40, 56, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (12, 9), (43, 30), (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / f"format{suffix}", pixels)

    result = _vectorize(payload, width_mm=28.0, height_mm=20.0)

    assert len(result.contours) == 1
    assert result.source_identity.format in {"jpeg", "bmp"}


def test_partial_alpha_cutoff_selects_only_sufficiently_opaque_features(
    tmp_path: Path,
) -> None:
    pixels = np.zeros((48, 72, 4), dtype=np.uint8)
    pixels[:, :, :3] = 20
    cv2.rectangle(pixels, (7, 10), (27, 37), (20, 20, 20, 80), thickness=-1)
    cv2.rectangle(pixels, (44, 10), (64, 37), (20, 20, 20, 220), thickness=-1)
    payload = _write_payload(tmp_path / "partial-alpha.png", pixels)

    result = _vectorize(
        payload,
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.ALPHA,
            alpha_cutoff=128,
            minimum_feature_area_mm2=0.0,
            simplification_tolerance_mm=0.1,
        ),
        width_mm=36.0,
        height_mm=24.0,
    )

    assert result.has_usable_alpha
    assert result.connected_component_count == 1
    assert len(result.contours) == 1


def test_options_payload_and_result_preview_storage_are_not_mutated(
    tmp_path: Path,
) -> None:
    pixels = np.full((48, 48, 3), 255, dtype=np.uint8)
    cv2.circle(pixels, (24, 24), 14, (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "immutable.png", pixels)
    options = _manual_options(
        smoothing_mm=0.1,
        simplification_tolerance_mm=0.12,
    )
    options_before = dataclasses.asdict(options)
    encoded_before = payload.encoded
    identity_before = payload.identity
    metadata_before = payload.metadata

    result = _vectorize(payload, options, width_mm=24.0, height_mm=24.0)

    assert dataclasses.asdict(options) == options_before
    assert payload.encoded is encoded_before
    assert payload.encoded == encoded_before
    assert payload.identity == identity_before
    assert payload.metadata == metadata_before
    assert not result.source_rgba.flags.writeable
    assert not result.foreground_mask.flags.writeable
    assert not result.overlay_rgba.flags.writeable
    with pytest.raises(dataclasses.FrozenInstanceError):
        options.threshold = 200  # type: ignore[misc]
    with pytest.raises(ValueError, match="counts are inconsistent"):
        dataclasses.replace(
            result,
            preview_flattened_point_count=(
                result.preview_flattened_point_count + 1
            ),
        )
    with pytest.raises(ValueError, match="image frame"):
        dataclasses.replace(
            result.contours[0],
            preview_points=(
                (0.75, 0.0),
                *result.contours[0].preview_points[1:],
            ),
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"threshold": -1}, "0 through 255"),
        ({"alpha_cutoff": 256}, "0 through 255"),
        ({"invert": 1}, "JSON boolean"),
        ({"minimum_feature_area_mm2": -0.1}, "cannot be negative"),
        ({"smoothing_mm": float("nan")}, "finite number"),
        ({"simplification_tolerance_mm": 0.0}, "must be positive"),
    ],
)
def test_options_reject_invalid_values(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RasterVectorizationOptions(**changes)


def test_alpha_mode_rejects_opaque_source(tmp_path: Path) -> None:
    pixels = np.full((32, 32, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (8, 8), (23, 23), (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "opaque.png", pixels)

    with pytest.raises(RasterVectorizationError, match="alpha tracing is unavailable"):
        _vectorize(
            payload,
            RasterVectorizationOptions(
                detection_mode=RasterDetectionMode.ALPHA,
                minimum_feature_area_mm2=0.0,
            ),
        )
