from __future__ import annotations

import math

import numpy as np
import pytest

from laser_aligner.project.arc_fit import MAX_ARC_FIT_SEGMENTS, arc_fit_vertices, fit_arc_span
from laser_aligner.project.path_geometry import (
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    flatten_native_path,
)


def _arc(*, defect: float = 0.0, defect_half_width: int = 1, closed: bool = False) -> NativePathGeometry:
    angles = np.linspace(0, math.pi, 201)
    radii = np.full(len(angles), 10.0)
    for delta in range(-defect_half_width, defect_half_width + 1):
        radii[100 + delta] += defect * (1 - abs(delta) / (defect_half_width + 1))
    points = tuple((float(radius * math.cos(angle)), float(radius * math.sin(angle)))
                   for radius, angle in zip(radii, angles, strict=True))
    return NativePathGeometry((PathSubpath(points[0], tuple(PathLineSegment(p) for p in points[1:]), closed),))


@pytest.mark.parametrize("defect", [0.0, 0.22, -0.22])
def test_arc_replaces_only_supported_span_across_small_edge_defects(defect: float) -> None:
    source = _arc(defect=defect)
    before = source.to_dict()
    result = fit_arc_span(source, 0, 0, 200)
    assert result.radius_mm == pytest.approx(10, abs=0.003)
    assert result.sweep_degrees == pytest.approx(180, abs=0.01)
    assert 2 <= result.arc_segment_count <= 4
    assert result.replaced_segment_count == 200
    assert result.arc_subpath.start == source.subpaths[0].start
    assert result.arc_subpath.segments[-1].to == source.subpaths[0].segments[-1].to
    assert all(isinstance(segment, PathCubicSegment) for segment in result.arc_subpath.segments)
    flattened = flatten_native_path(result.geometry, 0.001)[0]
    assert max(abs(math.hypot(*point) - 10) for point in flattened) < 0.003
    assert source.to_dict() == before


def test_physical_fit_of_nonuniformly_scaled_native_path() -> None:
    source = _arc(defect=0.22)
    path = source.subpaths[0]
    normalized = NativePathGeometry((PathSubpath(
        (path.start[0] / 80, path.start[1] / 20),
        tuple(PathLineSegment((s.to[0] / 80, s.to[1] / 20)) for s in path.segments),
    ),))
    result = fit_arc_span(normalized, 0, 0, 200, width_mm=80, height_mm=20)
    assert result.radius_mm == pytest.approx(10, abs=0.003)
    physical = flatten_native_path(result.geometry, 0.001, transform=PathAffineTransform(m11=80, m22=20))[0]
    assert max(abs(math.hypot(*point) - 10) for point in physical) < 0.003


def test_closed_contour_retains_real_corners_unselected_segments_holes_and_fill_rule() -> None:
    outer = _arc(defect=0.22, closed=True).subpaths[0]
    hole = PathSubpath((-1, 3), (PathLineSegment((1, 3)), PathLineSegment((1, 5)), PathLineSegment((-1, 5))), True)
    source = NativePathGeometry((outer, hole), PathFillRule.NONZERO)
    result = fit_arc_span(source, 0, 0, 200)
    assert result.geometry.subpaths[1] is hole
    assert result.geometry.fill_rule == PathFillRule.NONZERO
    assert result.geometry.path_version == source.path_version
    assert result.geometry.subpaths[0].closed
    assert result.geometry.subpaths[0].segments[-1] == PathLineSegment(outer.start)
    assert result.geometry.subpaths[0].start == outer.start


def test_closed_span_wraps_across_stored_origin_and_retains_other_segments() -> None:
    original = _arc(defect=0.22, closed=True).subpaths[0]
    explicit = (*original.segments, PathLineSegment(original.start))
    # Move the stored origin into the middle of the arc to exercise wrap-around.
    rotated = PathSubpath(explicit[79].to, (*explicit[80:], *explicit[:80]), True)
    source = NativePathGeometry((rotated,))
    result = fit_arc_span(source, 0, 121, 120)
    assert result.radius_mm == pytest.approx(10, abs=0.003)
    assert result.replaced_segment_count == 200
    assert result.geometry.subpaths[0].start == original.start
    assert result.geometry.subpaths[0].segments[-1] is explicit[-1]


def test_partial_span_preserves_existing_cubics_and_lines_exactly() -> None:
    source = _arc()
    first = fit_arc_span(source, 0, 0, 80)
    count = first.arc_segment_count
    second = fit_arc_span(first.geometry, 0, count, len(first.geometry.subpaths[0].segments))
    for prior, current in zip(first.geometry.subpaths[0].segments[:count], second.geometry.subpaths[0].segments[:count], strict=True):
        assert prior is current


@pytest.mark.parametrize("defect,width", [(0.6, 1), (-0.6, 1), (0.22, 14), (-0.22, 14)])
def test_real_deep_or_wide_notch_and_protrusion_are_rejected(defect: float, width: int) -> None:
    with pytest.raises(ValueError, match="deep|wide|non-circular|radii"):
        fit_arc_span(_arc(defect=defect, defect_half_width=width), 0, 0, 200)


def test_square_corner_and_straight_span_are_not_arcs() -> None:
    for points in (((0, 0), (10, 0), (10, 10)), ((0, 0), (5, 0), (10, 0))):
        source = NativePathGeometry((PathSubpath(points[0], tuple(PathLineSegment(p) for p in points[1:])),))
        with pytest.raises(ValueError):
            fit_arc_span(source, 0, 0, 2)


def test_ellipse_and_mixed_radius_curve_are_rejected() -> None:
    angles = np.linspace(0, math.pi, 201)
    points = [(10 * math.cos(angle), 8 * math.sin(angle)) for angle in angles]
    source = NativePathGeometry((PathSubpath(points[0], tuple(PathLineSegment(p) for p in points[1:])),))
    with pytest.raises(ValueError, match="deep|wide|radii|non-circular"):
        fit_arc_span(source, 0, 0, 200)


def test_tiny_defect_cannot_erase_or_move_past_a_hole() -> None:
    outer = _arc(defect=0.22, closed=True).subpaths[0]
    hole = PathSubpath((-0.015, 10.12), (
        PathLineSegment((0.015, 10.12)), PathLineSegment((0.015, 10.15)), PathLineSegment((-0.015, 10.15)),
    ), True)
    with pytest.raises(ValueError, match="containment"):
        fit_arc_span(NativePathGeometry((outer, hole)), 0, 0, 200)


def test_proposed_arc_cannot_cross_other_path() -> None:
    outer = _arc(defect=0.22).subpaths[0]
    other = PathSubpath((-0.015, 9.99), (PathLineSegment((0.015, 10.08)),))
    with pytest.raises(ValueError, match="intersect|touch"):
        fit_arc_span(NativePathGeometry((outer, other)), 0, 0, 200)


def test_closed_vertex_selection_handles_implicit_and_explicit_close() -> None:
    implicit = _arc(closed=True).subpaths[0]
    explicit = PathSubpath(implicit.start, (*implicit.segments, PathLineSegment(implicit.start)), True)
    assert arc_fit_vertices(implicit) == arc_fit_vertices(explicit)
    assert len(arc_fit_vertices(implicit)) == 201
    assert len(arc_fit_vertices(_arc().subpaths[0])) == 201


@pytest.mark.parametrize("kwargs", [
    {"subpath_index": True}, {"subpath_index": 1}, {"start_vertex": -1},
    {"end_vertex": 201}, {"start_vertex": 200}, {"end_vertex": 0},
    {"width_mm": 0}, {"height_mm": math.inf}, {"tolerance_mm": math.nan}, {"tolerance_mm": True},
])
def test_invalid_span_and_parameters_fail_without_mutation(kwargs: dict) -> None:
    source = _arc()
    before = source.to_dict()
    arguments = {"subpath_index": 0, "start_vertex": 0, "end_vertex": 200, **kwargs}
    with pytest.raises(ValueError):
        fit_arc_span(source, **arguments)
    assert source.to_dict() == before


def test_sample_work_is_bounded() -> None:
    with pytest.raises(ValueError, match="sample limit"):
        fit_arc_span(_arc(), 0, 0, 200, tolerance_mm=1e-5)


def test_native_result_round_trips_without_new_schema() -> None:
    result = fit_arc_span(_arc(defect=0.22), 0, 0, 200)
    assert NativePathGeometry.from_dict(result.geometry.to_dict()) == result.geometry


def test_clockwise_arc_retains_direction_and_exact_anchors() -> None:
    points = tuple(reversed(arc_fit_vertices(_arc(defect=0.22).subpaths[0])))
    source = NativePathGeometry((PathSubpath(points[0], tuple(PathLineSegment(p) for p in points[1:])),))
    result = fit_arc_span(source, 0, 0, 200)
    assert result.sweep_degrees == pytest.approx(-180, abs=0.01)
    assert result.arc_subpath.start == points[0]
    assert result.arc_subpath.segments[-1].to == points[-1]


def test_excessive_total_defect_support_is_rejected() -> None:
    source = _arc()
    points = list(arc_fit_vertices(source.subpaths[0]))
    for index in range(10, 195, 10):
        points[index] = tuple(value * 1.022 for value in points[index])
    noisy = NativePathGeometry((PathSubpath(points[0], tuple(PathLineSegment(p) for p in points[1:])),))
    with pytest.raises(ValueError, match="Too much|non-circular|radii"):
        fit_arc_span(noisy, 0, 0, 200)


def test_existing_native_cubics_can_be_fitted_without_new_path_kind() -> None:
    initial = fit_arc_span(_arc(), 0, 0, 200)
    result = fit_arc_span(initial.geometry, 0, 0, initial.arc_segment_count)
    assert result.radius_mm == pytest.approx(10, abs=0.003)
    assert result.arc_subpath.start == initial.arc_subpath.start
    assert result.arc_subpath.segments[-1].to == initial.arc_subpath.segments[-1].to


def test_segment_work_is_bounded_before_fitting() -> None:
    path = PathSubpath((0, 0), tuple(PathLineSegment((float(index), 0)) for index in range(1, MAX_ARC_FIT_SEGMENTS + 2)))
    with pytest.raises(ValueError, match="segment limit"):
        fit_arc_span(NativePathGeometry((path,)), 0, 0, 10)


def test_extreme_scale_is_rejected_before_integer_sampling_overflow() -> None:
    with pytest.raises(ValueError, match="sample limit"):
        fit_arc_span(_arc(), 0, 0, 200, width_mm=1e6, height_mm=1e6, tolerance_mm=1e-9)


def test_reported_maximum_covers_native_flattening_and_arc_representation() -> None:
    source = fit_arc_span(_arc(), 0, 0, 200).geometry
    result = fit_arc_span(source, 0, 0, len(source.subpaths[0].segments))
    assert result.maximum_deviation_mm >= 0.001
    assert result.maximum_deviation_mm <= 0.3


def test_edge_near_limit_leaves_allowance_for_complete_cubic_representation() -> None:
    with pytest.raises(ValueError, match="complete fitted curve"):
        fit_arc_span(_arc(defect=0.299), 0, 0, 200)


def test_hole_between_true_cubic_and_flattening_chord_is_not_reclassified() -> None:
    from laser_aligner.project.raster_vectorize import _point_inside_closed_polyline

    source = fit_arc_span(_arc(closed=True), 0, 0, 200).geometry
    end = len(source.subpaths[0].segments) - 1
    candidate = fit_arc_span(source, 0, 0, end).geometry
    hole = PathSubpath((9.08704810756018, 4.1739826715407204), (
        PathLineSegment((9.087062404889181, 4.1739826715407204)),
        PathLineSegment((9.08704810756018, 4.173996968869721)),
    ), True)
    # This tiny hole lies between two disjoint complete native curves, within
    # the coarse display-flattening envelope. A coarse containment signature
    # alone falsely classified it identically before and after the edit.
    before = np.asarray(flatten_native_path(source, 1e-7)[0])
    after = np.asarray(flatten_native_path(candidate, 1e-7)[0])
    assert _point_inside_closed_polyline(np.asarray(hole.start), before)
    assert not _point_inside_closed_polyline(np.asarray(hole.start), after)
    with pytest.raises(ValueError, match="too close.*containment"):
        fit_arc_span(NativePathGeometry((*source.subpaths, hole)), 0, 0, end)
