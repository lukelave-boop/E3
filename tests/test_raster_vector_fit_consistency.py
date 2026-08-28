from __future__ import annotations

import math

import numpy as np
import pytest

import laser_aligner.project.raster_vectorize as raster_vectorize


def _rotation(angle_radians: float) -> np.ndarray:
    cosine = math.cos(angle_radians)
    sine = math.sin(angle_radians)
    return np.asarray(((cosine, -sine), (sine, cosine)))


def _stadium(
    *,
    angle_radians: float = 0.0,
    perturb_cap: bool = False,
    anisotropic: bool = False,
) -> np.ndarray:
    radius = 1.2
    half_straight = 3.6
    lower_angles = np.linspace(math.pi, 2.0 * math.pi, 97, endpoint=False)
    upper_angles = np.linspace(0.0, math.pi, 97, endpoint=False)
    upward = np.linspace(-half_straight, half_straight, 241, endpoint=False)
    downward = np.linspace(half_straight, -half_straight, 241, endpoint=False)
    points = np.vstack(
        (
            np.column_stack(
                (
                    radius * np.cos(lower_angles),
                    -half_straight + radius * np.sin(lower_angles),
                )
            ),
            np.column_stack((np.full_like(upward, radius), upward)),
            np.column_stack(
                (
                    radius * np.cos(upper_angles),
                    half_straight + radius * np.sin(upper_angles),
                )
            ),
            np.column_stack((np.full_like(downward, -radius), downward)),
        )
    )
    if perturb_cap:
        points[int(np.argmin(points[:, 1])), 1] -= 0.10
    if anisotropic:
        points = points @ np.asarray(((2.0, 0.0), (0.0, 0.5))).T
    return points @ _rotation(angle_radians).T


def _dense_polygon(vertices: np.ndarray, points_per_side: int = 100) -> np.ndarray:
    fractions = np.arange(points_per_side, dtype=np.float64)[:, None] / points_per_side
    return np.vstack(
        [
            start + (end - start) * fractions
            for start, end in zip(vertices, np.roll(vertices, -1, axis=0), strict=True)
        ]
    )


_BLOCK_E_VERTICES = np.asarray(
    (
        (-4.0, -5.0),
        (4.0, -5.0),
        (4.0, -3.0),
        (-1.0, -3.0),
        (-1.0, -1.0),
        (3.0, -1.0),
        (3.0, 1.0),
        (-1.0, 1.0),
        (-1.0, 3.0),
        (4.0, 3.0),
        (4.0, 5.0),
        (-4.0, 5.0),
    )
)


def _block_e(points_per_side: int = 100) -> np.ndarray:
    return _dense_polygon(_BLOCK_E_VERTICES, points_per_side)


def _fit_with_native(
    points: np.ndarray,
    *,
    source_pixel_spacing_mm: tuple[float, float] = (0.10, 0.10),
) -> tuple[object, object, np.ndarray]:
    canonical = raster_vectorize._canonicalize_closed_contour(points)
    fitted = raster_vectorize._fit_contour(
        canonical,
        _options(),
        20.0,
        20.0,
        raster_vectorize._ComplexityBudget(),
        source_pixel_spacing_mm=source_pixel_spacing_mm,
    )
    native = raster_vectorize._native_subpath_from_fitted_contour(
        fitted,
        20.0,
        20.0,
    )
    return fitted, native, canonical


def _assert_native_line_for_edge(
    fitted: object,
    native: object,
    raw_points: np.ndarray,
    expected_start: np.ndarray,
    expected_end: np.ndarray,
    *,
    line_distance_mm: float = 1e-8,
) -> None:
    expected_delta = expected_end - expected_start
    expected_length = float(np.linalg.norm(expected_delta))
    expected_direction = expected_delta / expected_length
    for segment, native_segment in zip(
        fitted.segments,
        native.segments,
        strict=True,
    ):
        if not isinstance(segment, raster_vectorize._LineSegment):
            continue
        endpoint_distances = raster_vectorize._point_to_corresponding_lines(
            np.vstack((segment.start, segment.end)),
            np.vstack((expected_start, expected_start)),
            np.vstack((expected_end, expected_end)),
        )
        coverage = abs(float(np.dot(segment.end - segment.start, expected_direction)))
        if (
            float(np.max(endpoint_distances)) > line_distance_mm
            or coverage < 0.70 * expected_length
        ):
            continue
        assert isinstance(native_segment, raster_vectorize.PathLineSegment)
        start_index = int(
            np.argmin(np.linalg.norm(raw_points - segment.start, axis=1))
        )
        end_index = int(np.argmin(np.linalg.norm(raw_points - segment.end, axis=1)))
        assert float(np.linalg.norm(raw_points[start_index] - segment.start)) < 1e-8
        assert float(np.linalg.norm(raw_points[end_index] - segment.end)) < 1e-8
        target = raw_points[
            raster_vectorize._circular_indices(
                start_index,
                end_index,
                len(raw_points),
            )
        ]
        assert float(
            np.max(
                raster_vectorize._distance_to_segment(
                    target,
                    segment.start,
                    segment.end,
                )
            )
        ) <= _options().simplification_tolerance_mm * 0.80 + 1e-12
        return
    pytest.fail("Expected source edge was not persisted as a validated native line")


def _options() -> raster_vectorize.RasterVectorizationOptions:
    return raster_vectorize.RasterVectorizationOptions(
        detection_mode=raster_vectorize.RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=127,
        minimum_feature_area_mm2=0.0,
        smoothing_mm=0.0,
        simplification_tolerance_mm=0.10,
        contour_output=raster_vectorize.RasterContourOutput.ALL_CONTOURS,
    )


def _capture_segments(points: np.ndarray) -> tuple[object, ...]:
    fitted = raster_vectorize._fit_contour(
        points,
        _options(),
        20.0,
        20.0,
        raster_vectorize._ComplexityBudget(),
    )
    return tuple(fitted.segments)


def _segment_code(segment: object) -> str:
    return "L" if isinstance(segment, raster_vectorize._LineSegment) else "C"


def _assert_g1_closed(segments: tuple[object, ...]) -> None:
    for first, second in zip(segments, (*segments[1:], segments[0]), strict=True):
        incoming = (
            first.end - first.start
            if isinstance(first, raster_vectorize._LineSegment)
            else first.end - first.control_2
        )
        outgoing = (
            second.end - second.start
            if isinstance(second, raster_vectorize._LineSegment)
            else second.control_1 - second.start
        )
        incoming /= np.linalg.norm(incoming)
        outgoing /= np.linalg.norm(outgoing)
        cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
        assert abs(float(cross)) <= 1e-9
        assert float(np.dot(incoming, outgoing)) > 0.0


def test_canonicalization_uses_full_cycle_to_break_repeated_minimum_ties() -> None:
    probe = np.asarray(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0), (0.0, 1.0)))
    expected = np.asarray(((0.0, 0.0), (0.0, 1.0), (0.0, 0.0), (1.0, 0.0), (1.0, 1.0)))

    for offset in range(len(probe)):
        actual = raster_vectorize._canonicalize_closed_contour(
            np.roll(probe, -offset, axis=0)
        )
        np.testing.assert_array_equal(actual, expected)


def test_nondegenerate_closed_span_is_split_instead_of_collapsed() -> None:
    square_loop = np.asarray(
        ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0))
    )

    cubic = raster_vectorize._fit_cubic(square_loop, 0.01)
    assert cubic.fitting_error_mm > 0.0

    segments = raster_vectorize._fit_span(
        square_loop,
        0.01,
        raster_vectorize._ComplexityBudget(),
        start_tangent=np.asarray((1.0, 0.0)),
        end_tangent=np.asarray((0.0, 1.0)),
    )
    assert len(segments) == 4
    assert all(
        isinstance(segment, raster_vectorize._LineSegment) for segment in segments
    )
    fitted_vertices = np.vstack(
        (segments[0].start, *(segment.end for segment in segments))
    )
    np.testing.assert_array_equal(fitted_vertices, square_loop)


def test_anchor_lower_bound_rejects_before_contour_spans_are_allocated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> list[np.ndarray]:
        pytest.fail("contour spans were allocated before the anchor budget check")

    monkeypatch.setattr(
        raster_vectorize,
        "MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS",
        3,
    )
    monkeypatch.setattr(raster_vectorize, "_contour_spans", fail_if_called)
    monkeypatch.setattr(raster_vectorize, "_closed_contour_tangent", fail_if_called)

    with pytest.raises(
        raster_vectorize.RasterVectorizationComplexityError,
        match="fitted segments",
    ):
        raster_vectorize._fit_contour(
            _stadium(angle_radians=0.41),
            _options(),
            20.0,
            20.0,
            raster_vectorize._ComplexityBudget(),
        )


def test_corner_lower_bound_rejects_before_smoothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_args: object, **_kwargs: object) -> tuple[np.ndarray, float]:
        pytest.fail("smoothing ran before the hard-corner anchor budget check")

    monkeypatch.setattr(
        raster_vectorize,
        "MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS",
        3,
    )
    monkeypatch.setattr(
        raster_vectorize,
        "_corner_indices",
        lambda *_args, **_kwargs: [0, 100, 200, 300],
    )
    monkeypatch.setattr(raster_vectorize, "_smooth_contour", fail_if_called)

    with pytest.raises(
        raster_vectorize.RasterVectorizationComplexityError,
        match="fitted segments",
    ):
        raster_vectorize._fit_contour(
            _stadium(angle_radians=0.41),
            _options(),
            20.0,
            20.0,
            raster_vectorize._ComplexityBudget(),
        )


def test_arbitrary_angle_closed_fit_is_cyclically_identical_and_g1() -> None:
    raw = _stadium(angle_radians=0.61)
    offsets = (0, 1, len(raw) // 13, len(raw) // 4, len(raw) // 2)
    canonical = [
        raster_vectorize._canonicalize_closed_contour(
            np.roll(raw, -offset, axis=0)
        )
        for offset in offsets
    ]
    fits = [_capture_segments(points) for points in canonical]

    for points in canonical[1:]:
        np.testing.assert_allclose(points, canonical[0], atol=0.0, rtol=0.0)
    assert raster_vectorize._corner_indices(canonical[0], 0.065) == []
    assert {tuple(_segment_code(segment) for segment in fit) for fit in fits} == {
        tuple(_segment_code(segment) for segment in fits[0])
    }
    assert {len(fit) for fit in fits} == {len(fits[0])}
    assert {"L", "C"} <= {_segment_code(segment) for segment in fits[0]}
    for fit in fits:
        _assert_g1_closed(fit)


def test_persistent_straight_run_anchors_are_rotation_and_count_agnostic() -> None:
    vertices = np.asarray(
        ((-3.0, -2.0), (2.0, -3.0), (4.0, 1.0), (0.0, 4.0), (-4.0, 1.0))
    )
    base = _dense_polygon(vertices)
    anchor_sets = []
    for angle in (0.0, 0.43, 1.17):
        points = raster_vectorize._canonicalize_closed_contour(
            base @ _rotation(angle).T
        )
        anchor_sets.append(
            raster_vectorize._long_straight_run_anchors(points, 0.08)
        )

    # Five independent straight runs contribute endpoints, and each sharp gap
    # receives a physical arc midpoint. This explicitly guards against pairing
    # two extrema or assuming horizontal/vertical sides.
    assert [len(anchors) for anchors in anchor_sets] == [15, 15, 15]


def test_multiscale_corners_reject_smooth_noise_but_keep_real_notches() -> None:
    for smooth in (
        _stadium(angle_radians=0.37),
        _stadium(angle_radians=0.37, perturb_cap=True),
        _stadium(angle_radians=0.37, anisotropic=True),
    ):
        canonical = raster_vectorize._canonicalize_closed_contour(smooth)
        assert raster_vectorize._corner_indices(canonical, 0.065) == []

    rectangle = _dense_polygon(
        np.asarray(((-3.0, -2.0), (3.0, -2.0), (3.0, 2.0), (-3.0, 2.0)))
    )
    rectangle = raster_vectorize._canonicalize_closed_contour(rectangle)
    assert len(raster_vectorize._corner_indices(rectangle, 0.065)) == 4

    notch = _dense_polygon(
        np.asarray(
            (
                (-4.0, -3.0),
                (4.0, -3.0),
                (4.0, -1.0),
                (2.0, 0.0),
                (4.0, 1.0),
                (4.0, 3.0),
                (-4.0, 3.0),
            )
        )
    )
    notch = raster_vectorize._canonicalize_closed_contour(notch)
    corner_indices = raster_vectorize._corner_indices(notch, 0.065)
    assert len(corner_indices) == 7
    corner_points = notch[np.asarray(corner_indices, dtype=np.int64)]
    assert float(np.min(np.linalg.norm(corner_points - (2.0, 0.0), axis=1))) < 1e-9


def test_collinear_block_e_outer_edges_persist_as_native_lines() -> None:
    fitted, native, raw = _fit_with_native(_block_e())

    for start, end in (
        ((-4.0, -5.0), (4.0, -5.0)),
        ((4.0, 5.0), (-4.0, 5.0)),
        ((-4.0, 5.0), (-4.0, -5.0)),
    ):
        _assert_native_line_for_edge(
            fitted,
            native,
            raw,
            np.asarray(start),
            np.asarray(end),
        )


def test_rotated_block_e_receives_the_same_native_line_protection() -> None:
    rotation = _rotation(0.43)
    fitted, native, raw = _fit_with_native(_block_e() @ rotation.T)

    for start, end in (
        ((-4.0, -5.0), (4.0, -5.0)),
        ((4.0, 5.0), (-4.0, 5.0)),
        ((-4.0, 5.0), (-4.0, -5.0)),
    ):
        _assert_native_line_for_edge(
            fitted,
            native,
            raw,
            np.asarray(start) @ rotation.T,
            np.asarray(end) @ rotation.T,
        )


def test_shallow_arc_with_line_fit_error_below_tolerance_remains_cubic() -> None:
    bottom = np.column_stack(
        (np.linspace(-4.0, 4.0, 161, endpoint=False), np.full(161, -1.0))
    )
    right = np.column_stack(
        (np.full(40, 4.0), np.linspace(-1.0, 1.0, 40, endpoint=False))
    )
    arc_x = np.linspace(4.0, -4.0, 321, endpoint=False)
    shallow_arc = np.column_stack(
        (arc_x, 1.0 + 0.05 * (1.0 - (arc_x / 4.0) ** 2))
    )
    left = np.column_stack(
        (np.full(40, -4.0), np.linspace(1.0, -1.0, 40, endpoint=False))
    )
    points = np.vstack((bottom, right, shallow_arc, left))
    fitted, native, _raw = _fit_with_native(
        points,
        source_pixel_spacing_mm=(0.02, 0.02),
    )

    assert float(
        np.max(
            raster_vectorize._distance_to_segment(
                shallow_arc,
                shallow_arc[0],
                shallow_arc[-1],
            )
        )
    ) < _options().simplification_tolerance_mm * 0.80
    top_segments = [
        (segment, native_segment)
        for segment, native_segment in zip(
            fitted.segments,
            native.segments,
            strict=True,
        )
        if min(float(segment.start[1]), float(segment.end[1])) >= 0.99
    ]
    assert top_segments
    assert any(
        isinstance(segment, raster_vectorize._CubicSegment)
        and isinstance(native_segment, raster_vectorize.PathCubicSegment)
        for segment, native_segment in top_segments
    )


def test_quantized_rotated_straight_edges_still_persist_as_lines() -> None:
    rotation = _rotation(0.37)
    exact = _block_e(points_per_side=400) @ rotation.T
    quantized = np.round(exact / 0.025) * 0.025
    keep = np.ones(len(quantized), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(quantized, axis=0), axis=1) > 1e-12
    fitted, native, raw = _fit_with_native(quantized[keep])

    for start, end in (
        ((-4.0, -5.0), (4.0, -5.0)),
        ((4.0, 5.0), (-4.0, 5.0)),
        ((-4.0, 5.0), (-4.0, -5.0)),
    ):
        _assert_native_line_for_edge(
            fitted,
            native,
            raw,
            np.asarray(start) @ rotation.T,
            np.asarray(end) @ rotation.T,
            line_distance_mm=0.04,
        )


def test_true_rounded_corners_remain_cubic_between_straight_runs() -> None:
    def line(
        start: tuple[float, float],
        end: tuple[float, float],
        count: int = 80,
    ) -> np.ndarray:
        fractions = np.arange(count, dtype=np.float64)[:, None] / count
        return np.asarray(start) + (np.asarray(end) - start) * fractions

    def arc(
        center: tuple[float, float],
        start_angle: float,
        end_angle: float,
        count: int = 60,
    ) -> np.ndarray:
        angles = np.linspace(start_angle, end_angle, count, endpoint=False)
        return np.column_stack(
            (
                center[0] + np.cos(angles),
                center[1] + np.sin(angles),
            )
        )

    points = np.vstack(
        (
            line((-3.0, -3.0), (3.0, -3.0)),
            arc((3.0, -2.0), -math.pi / 2.0, 0.0),
            line((4.0, -2.0), (4.0, 2.0)),
            arc((3.0, 2.0), 0.0, math.pi / 2.0),
            line((3.0, 3.0), (-3.0, 3.0)),
            arc((-3.0, 2.0), math.pi / 2.0, math.pi),
            line((-4.0, 2.0), (-4.0, -2.0)),
            arc((-3.0, -2.0), math.pi, 3.0 * math.pi / 2.0),
        )
    )
    fitted, native, _raw = _fit_with_native(
        points,
        source_pixel_spacing_mm=(0.05, 0.05),
    )
    native_is_line = [
        isinstance(segment, raster_vectorize.PathLineSegment)
        for segment in native.segments
    ]

    assert all(
        isinstance(fitted_segment, raster_vectorize._LineSegment)
        == is_line
        for fitted_segment, is_line in zip(
            fitted.segments,
            native_is_line,
            strict=True,
        )
    )
    assert any(native_is_line)
    assert any(not is_line for is_line in native_is_line)
    assert any(
        not native_is_line[index]
        and native_is_line[index - 1]
        and native_is_line[(index + 1) % len(native_is_line)]
        for index in range(len(native_is_line))
    )
