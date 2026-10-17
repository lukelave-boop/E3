from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from laser_aligner.project.native_contour_fit import fit_physical_contours_to_native_path
from laser_aligner.project.path_geometry import PathCubicSegment, PathLineSegment
from laser_aligner.project.primitive_recovery import (
    circular_arc_support_inliers,
    fit_circular_arc_hypothesis,
)
from laser_aligner.project.raster_vectorize import (
    RasterDetectionMode,
    RasterVectorizationOptions,
    prepare_pixel_vectorization_source,
    vectorize_pixel_source,
)


def _arc(*, height: float = 0.14, samples: int = 5) -> np.ndarray:
    angles = np.linspace(-math.pi / 2, math.pi / 2, 401)
    radii = np.full(401, 8.0)
    radii[180 : 180 + samples] += height
    return np.column_stack((radii * np.cos(angles), radii * np.sin(angles)))


@pytest.mark.parametrize("height", (0.14, -0.14))
def test_isolated_small_defect_does_not_veto_supported_arc(height: float) -> None:
    points = _arc(height=height)
    assert fit_circular_arc_hypothesis(points, 0.24, (0.1, 0.1)) is None
    arc = fit_circular_arc_hypothesis(points, 0.24, (0.1, 0.1), allow_small_defects=True)
    assert arc is not None
    np.testing.assert_allclose(arc.center, (0, 0), atol=0.001)
    assert arc.radius_mm == pytest.approx(8, abs=0.001)
    assert abs(arc.sweep_radians) == pytest.approx(math.pi)
    assert arc.maximum_residual_mm == pytest.approx(abs(height), abs=0.001)
    assert arc.defect_count == 1


@pytest.mark.parametrize("height,samples", ((0.24, 3), (-0.24, 3), (0.14, 20), (-0.14, 20)))
def test_resolved_notch_or_protrusion_is_not_removed(height: float, samples: int) -> None:
    assert fit_circular_arc_hypothesis(
        _arc(height=height, samples=samples), 0.5, (0.1, 0.1), allow_small_defects=True,
    ) is None


def test_small_defect_still_obeys_native_tolerance() -> None:
    assert fit_circular_arc_hypothesis(_arc(), 0.1, (0.1, 0.1), allow_small_defects=True) is None


def test_many_small_defects_cannot_replace_consistent_arc_support() -> None:
    points = _arc(height=0)
    for start in range(25, 380, 30):
        points[start : start + 4] *= 1.0175
    assert fit_circular_arc_hypothesis(points, 0.3, (0.1, 0.1), allow_small_defects=True) is None


def test_endpoint_defect_cannot_move_a_real_join() -> None:
    points = _arc(height=0)
    points[0] *= 1.0175
    assert fit_circular_arc_hypothesis(points, 0.3, (0.1, 0.1), allow_small_defects=True) is None


@pytest.mark.parametrize("shift", (0, 100, 719))
def test_closed_circle_defect_is_independent_of_contour_seam(shift: int) -> None:
    angles = np.linspace(0, 2 * math.pi, 720, endpoint=False)
    radii = np.full(720, 10.0)
    radii[0] += 0.12
    points = np.column_stack((radii * np.cos(angles), radii * np.sin(angles)))
    hypothesis = fit_circular_arc_hypothesis(
        np.roll(points, shift, axis=0), 0.2, (0.1, 0.1), closed=True, allow_small_defects=True,
    )
    assert hypothesis is not None
    assert hypothesis.radius_mm == pytest.approx(10, abs=0.001)
    assert abs(hypothesis.sweep_radians) == pytest.approx(2 * math.pi)


@pytest.mark.parametrize("tolerance", (0.0, -0.1, math.inf, math.nan))
def test_support_validator_rejects_invalid_budget(tolerance: float) -> None:
    with pytest.raises(ValueError):
        circular_arc_support_inliers(_arc(), np.zeros(2), 8, tolerance, (0.1, 0.1))


def test_fine_source_axis_cannot_borrow_coarse_axis_defect_allowance() -> None:
    points = _arc(height=0)
    points[198:203] *= 1.0175
    assert fit_circular_arc_hypothesis(points, 0.3, (0.04, 0.4), allow_small_defects=True) is None


@pytest.mark.parametrize("kind", ("ellipse", "wavy", "corner"))
def test_noncircular_support_is_not_regularized(kind: str) -> None:
    points = _arc(height=0)
    if kind == "ellipse":
        points[:, 0] *= 1.2
    elif kind == "wavy":
        points *= (1 + 0.04 * np.sin(np.linspace(0, 8 * math.pi, len(points))))[:, None]
    else:
        points[200:, 0] += np.linspace(0, 1.5, len(points) - 200)
    assert fit_circular_arc_hypothesis(points, 0.5, (0.1, 0.1), allow_small_defects=True) is None


def test_long_arc_bridges_defect_corners_but_preserves_chord_corners() -> None:
    points = _arc()
    chord = np.linspace(points[-1], points[0], 100)[1:-1]
    result = fit_physical_contours_to_native_path(
        (np.vstack((points, chord)),), (None,), source_pixel_spacing_mm=(0.1, 0.1),
        fitting_tolerance_mm=0.3, frame_bounds_mm=(-1, -9, 9, 9),
    )
    metrics = result.primitive_recovery
    segments = result.geometry.subpaths[0].segments
    assert metrics.baseline_segment_count >= 9
    assert metrics.recovered_arc_count == 1
    assert metrics.recovered_arc_length_mm == pytest.approx(8 * math.pi, abs=0.01)
    assert metrics.recovered_line_count == 1
    assert metrics.recovered_line_length_mm == pytest.approx(16)
    assert sum(isinstance(segment, PathLineSegment) for segment in segments) == 1
    assert metrics.freeform_cubic_count == 0
    assert metrics.maximum_residual_mm == pytest.approx(0.14, abs=0.001)


def test_local_arc_recovery_among_many_real_corners_preserves_every_corner() -> None:
    arc = _arc()
    corners = [arc[-1], np.asarray((0.0, 6.0))]
    corners.extend(np.asarray((-2.0 if index % 2 == 0 else -1.0, y)) for index, y in enumerate(np.linspace(6, -6, 13)))
    corners.extend((np.asarray((0.0, -6.0)), arc[0]))
    edges = [np.linspace(start, end, 31, endpoint=False) for start, end in zip(corners[:-1], corners[1:], strict=True)]
    result = fit_physical_contours_to_native_path(
        (np.vstack((arc[:-1], *edges)),), (None,), source_pixel_spacing_mm=(0.1, 0.1),
        fitting_tolerance_mm=0.3, frame_bounds_mm=(-3, -9, 9, 9),
    )
    metrics = result.primitive_recovery
    assert metrics.baseline_segment_count >= 50
    assert metrics.recovered_arc_count == 1
    assert metrics.recovered_arc_length_mm == pytest.approx(8 * math.pi, abs=0.01)
    assert metrics.hypothesis_count <= 256
    subpath = result.geometry.subpaths[0]
    endpoints = np.asarray([subpath.start, *(segment.to for segment in subpath.segments)])
    endpoints = endpoints * (result.width_mm, result.height_mm) + result.center_mm
    for corner in corners:
        assert float(np.min(np.linalg.norm(endpoints - corner, axis=1))) < 1e-8


def _raster_circle(kind: str) -> np.ndarray:
    pixels = np.full((220, 220, 4), 255, dtype=np.uint8)
    cv2.circle(pixels, (110, 110), 80, (0, 0, 0, 255), -1)
    if kind == "defect":
        cv2.rectangle(pixels, (190, 110), (191, 110), (0, 0, 0, 255), -1)
    elif kind == "notch":
        cv2.rectangle(pixels, (184, 108), (195, 113), (255, 255, 255, 255), -1)
    elif kind == "protrusion":
        cv2.rectangle(pixels, (187, 107), (194, 113), (0, 0, 0, 255), -1)
    elif kind == "broad":
        cv2.rectangle(pixels, (187, 106), (191, 114), (0, 0, 0, 255), -1)
    return pixels


def _vectorize(pixels: np.ndarray, *, recover: bool = True):
    return vectorize_pixel_source(
        prepare_pixel_vectorization_source(pixels),
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.MANUAL_THRESHOLD, threshold=127,
            minimum_feature_area_mm2=0, smoothing_mm=0, simplification_tolerance_mm=0.3,
        ),
        displayed_width_mm=22, displayed_height_mm=22, recover_primitives=recover,
    )


def test_raster_spike_returns_canonical_circle_after_topology_validation() -> None:
    result = _vectorize(_raster_circle("defect"))
    assert len(result.contours) == 1
    contour = result.contours[0]
    metrics = contour.primitive_recovery
    assert metrics.recovered_arc_count == 1
    assert 0.13 < metrics.maximum_residual_mm < 0.15
    assert metrics.canonical_arc_cubic_count == 4
    assert metrics.freeform_cubic_count == 0
    assert all(isinstance(segment, PathCubicSegment) for segment in contour.native_subpath.segments)
    assert contour.max_fitting_error_mm <= 0.3 * 0.8
    baseline = _vectorize(_raster_circle("defect"), recover=False)
    assert baseline.contours[0].native_subpath != contour.native_subpath


@pytest.mark.parametrize("kind", ("notch", "protrusion", "broad"))
def test_raster_real_features_survive_the_complete_pipeline(kind: str) -> None:
    result = _vectorize(_raster_circle(kind))
    contour = result.contours[0]
    assert contour.primitive_recovery.recovered_arc_count == 0
    assert contour.primitive_recovery.freeform_cubic_count > 0
    # Final returned geometry equals recovery-disabled geometry: failed arc
    # hypotheses cannot silently remove the real feature before topology checks.
    baseline = _vectorize(_raster_circle(kind), recover=False)
    assert contour.native_subpath == baseline.contours[0].native_subpath


def test_raster_defect_recovery_retains_a_real_hole() -> None:
    pixels = _raster_circle("none")
    cv2.rectangle(pixels, (166, 166), (167, 167), (0, 0, 0, 255), -1)
    cv2.circle(pixels, (110, 110), 30, (255, 255, 255, 255), -1)
    result = _vectorize(pixels)
    assert len(result.contours) == 2
    assert [(item.depth, item.is_hole) for item in result.contours] == [(0, False), (1, True)]
    assert result.contours[0].primitive_recovery.recovered_arc_count == 1
    assert result.contours[0].primitive_recovery.maximum_residual_mm > 0.10
    assert result.contours[1].parent_index == 0
