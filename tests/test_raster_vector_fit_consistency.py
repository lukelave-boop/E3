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


def _options() -> raster_vectorize.RasterVectorizationOptions:
    return raster_vectorize.RasterVectorizationOptions(
        detection_mode=raster_vectorize.RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=127,
        minimum_feature_area_mm2=0.0,
        smoothing_mm=0.0,
        simplification_tolerance_mm=0.10,
        contour_output=raster_vectorize.RasterContourOutput.ALL_CONTOURS,
    )


def _capture_segments(
    points: np.ndarray,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, ...]:
    captured: list[object] = []
    original = raster_vectorize._flatten_segment

    def capture(
        segment: object,
        tolerance_mm: float,
        budget: object,
        output: list[np.ndarray],
        depth: int = 0,
    ) -> float:
        if depth == 0:
            captured.append(segment)
        return original(segment, tolerance_mm, budget, output, depth)

    with monkeypatch.context() as context:
        context.setattr(raster_vectorize, "_flatten_segment", capture)
        raster_vectorize._fit_and_flatten_contour(
            points,
            _options(),
            20.0,
            20.0,
            raster_vectorize._ComplexityBudget(),
        )
    return tuple(captured)


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
        raster_vectorize._fit_and_flatten_contour(
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
        raster_vectorize._fit_and_flatten_contour(
            _stadium(angle_radians=0.41),
            _options(),
            20.0,
            20.0,
            raster_vectorize._ComplexityBudget(),
        )


def test_arbitrary_angle_closed_fit_is_cyclically_identical_and_g1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _stadium(angle_radians=0.61)
    offsets = (0, 1, len(raw) // 13, len(raw) // 4, len(raw) // 2)
    canonical = [
        raster_vectorize._canonicalize_closed_contour(
            np.roll(raw, -offset, axis=0)
        )
        for offset in offsets
    ]
    fits = [_capture_segments(points, monkeypatch) for points in canonical]

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
