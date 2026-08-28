from __future__ import annotations

import math

import numpy as np
import pytest

import laser_aligner.project.raster_vectorize as fitter

FIT_TOLERANCE_MM = 0.01


def _unit(vector: np.ndarray) -> np.ndarray:
    result = fitter._unit_direction(np.asarray(vector, dtype=np.float64))
    assert result is not None
    return result


def _fit_open(
    points: np.ndarray,
    start_tangent: np.ndarray,
    end_tangent: np.ndarray,
    *,
    tolerance_mm: float = FIT_TOLERANCE_MM,
) -> tuple[list[object], fitter._ComplexityBudget]:
    budget = fitter._ComplexityBudget()
    segments = fitter._fit_span(
        points,
        tolerance_mm,
        budget,
        start_tangent=_unit(start_tangent),
        end_tangent=_unit(end_tangent),
        prefer_cubic_leaves=True,
        control_minimum=np.min(points, axis=0) - 10.0,
        control_maximum=np.max(points, axis=0) + 10.0,
    )
    return segments, budget


def _segment_start_tangent(segment: object) -> np.ndarray:
    if isinstance(segment, fitter._LineSegment):
        return segment.end - segment.start
    assert isinstance(segment, fitter._CubicSegment)
    return segment.control_1 - segment.start


def _segment_end_tangent(segment: object) -> np.ndarray:
    if isinstance(segment, fitter._LineSegment):
        return segment.end - segment.start
    assert isinstance(segment, fitter._CubicSegment)
    return segment.end - segment.control_2


def _piece(segment: object) -> fitter._FittedPiece:
    start = segment.start
    end = segment.end
    forward = _unit(end - start)
    return fitter._FittedPiece(
        segment=segment,
        target_points=np.vstack((start, end)),
        target_parameters=np.asarray((0.0, 1.0)),
        start_tangent=forward,
        end_tangent=-forward,
        hard_start=False,
        hard_end=False,
        sample_error_sum_mm=0.0,
        sample_squared_error_sum_mm2=0.0,
        sample_count=2,
    )


def test_newton_reparameterization_reconstructs_one_exact_cubic() -> None:
    controls = np.asarray(((0.0, 0.0), (2.0, 4.0), (4.0, -4.0), (6.0, 0.0)))
    source_parameters = np.linspace(0.0, 1.0, 301)
    points = fitter._cubic_values(*controls, source_parameters)

    chord_parameters = fitter._chord_parameters(points)
    initial_controls = fitter._generate_bezier_controls(
        points,
        chord_parameters,
        _unit(controls[1] - controls[0]),
        _unit(controls[2] - controls[3]),
        FIT_TOLERANCE_MM,
        np.asarray((-10.0, -10.0)),
        np.asarray((10.0, 10.0)),
    )
    initial_error = float(
        np.max(
            np.linalg.norm(
                fitter._cubic_values(*initial_controls, chord_parameters) - points,
                axis=1,
            )
        )
    )
    assert initial_error > FIT_TOLERANCE_MM

    segments, budget = _fit_open(
        points,
        controls[1] - controls[0],
        controls[2] - controls[3],
    )

    assert len(segments) == 1
    assert isinstance(segments[0], fitter._CubicSegment)
    assert segments[0].fitting_error_mm <= FIT_TOLERANCE_MM
    assert budget.fit_validation_steps > 0


def test_quarter_circle_is_one_long_validated_cubic() -> None:
    angles = np.linspace(0.0, math.pi / 2.0, 201)
    points = np.column_stack((10.0 * np.cos(angles), 10.0 * np.sin(angles)))

    segments, _budget = _fit_open(
        points,
        np.asarray((0.0, 1.0)),
        np.asarray((1.0, 0.0)),
    )

    assert len(segments) == 1
    assert isinstance(segments[0], fitter._CubicSegment)
    assert segments[0].fitting_error_mm <= FIT_TOLERANCE_MM


def test_continuous_validation_rejects_between_sample_s_lobes() -> None:
    target = np.asarray(((0.0, 0.0), (0.5, 0.0), (1.0, 0.0)))
    parameters = np.asarray((0.0, 0.5, 1.0))
    controls = np.asarray(
        ((0.0, 0.0), (1.0 / 3.0, 1.0), (2.0 / 3.0, -1.0), (1.0, 0.0))
    )
    assigned = fitter._cubic_values(*controls, parameters)
    assert assigned == pytest.approx(target)

    validation = fitter._validate_curve_fit(
        target,
        parameters,
        controls,
        FIT_TOLERANCE_MM,
        fitter._ComplexityBudget(),
    )

    assert not validation.accepted
    assert validation.max_error_mm > FIT_TOLERANCE_MM
    assert validation.split_index == 1


def test_recursive_smooth_cubics_share_exact_g1_tangents() -> None:
    angles = np.linspace(0.0, math.pi, 401)
    points = np.column_stack((10.0 * np.cos(angles), 10.0 * np.sin(angles)))
    segments, budget = _fit_open(
        points,
        np.asarray((0.0, 1.0)),
        np.asarray((0.0, 1.0)),
        tolerance_mm=0.005,
    )

    assert 2 <= len(segments) <= 4
    assert budget.recursive_splits > 0
    assert all(isinstance(segment, fitter._CubicSegment) for segment in segments)
    for first, second in zip(segments[:-1], segments[1:], strict=True):
        incoming = _segment_end_tangent(first)
        outgoing = _segment_start_tangent(second)
        cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
        assert cross == pytest.approx(0.0, abs=1e-12)
        assert float(np.dot(incoming, outgoing)) > 0.0


def test_verified_merging_reduces_a_smooth_closed_circle() -> None:
    angles = np.linspace(0.0, 2.0 * math.pi, 801, endpoint=False)
    points = np.column_stack((10.0 * np.cos(angles), 10.0 * np.sin(angles)))
    options = fitter.RasterVectorizationOptions(
        simplification_tolerance_mm=0.05,
    )
    budget = fitter._ComplexityBudget()

    fitted = fitter._fit_contour(points, options, 30.0, 30.0, budget)

    assert fitter._corner_indices(points, 0.05 * 0.65) == []
    assert len(fitted.segments) == 4
    assert fitted.merged_segment_count > 0
    assert fitted.longest_smooth_span_segment_count == len(fitted.segments)
    assert fitted.mean_fitting_error_mm <= fitted.rms_fitting_error_mm
    assert fitted.rms_fitting_error_mm <= fitted.max_fitting_error_mm
    assert fitted.fitting_error_sample_count > 0
    assert fitted.max_fitting_error_mm <= 0.05 * 0.80
    for segment in fitted.segments:
        assert isinstance(segment, fitter._CubicSegment)
        for point in (segment.control_1, segment.control_2):
            assert np.all(point >= -15.0)
            assert np.all(point <= 15.0)


def test_verified_merge_rejects_current_main_adjacent_arc_ambiguity() -> None:
    start = np.asarray((0.0, 0.0))
    shared = np.asarray((1.0, 0.0))
    merged = _piece(
        fitter._CubicSegment(
            start,
            np.asarray((1.0 / 3.0, 0.0)),
            np.asarray((2.0 / 3.0, 0.0)),
            shared,
            0.0,
        )
    )
    crossing_following = _piece(
        fitter._CubicSegment(
            shared,
            np.asarray((2.0 / 3.0, 1.0)),
            np.asarray((1.0 / 3.0, 1.0)),
            np.asarray((0.0, -1.0)),
            0.0,
        )
    )
    pieces = [
        _piece(fitter._LineSegment(start, np.asarray((0.5, 0.0)), 0.0)),
        _piece(fitter._LineSegment(np.asarray((0.5, 0.0)), shared, 0.0)),
        crossing_following,
        _piece(
            fitter._LineSegment(
                np.asarray((0.0, -1.0)),
                np.asarray((-1.0, -1.0)),
                0.0,
            )
        ),
        _piece(
            fitter._LineSegment(
                np.asarray((-1.0, -1.0)),
                start,
                0.0,
            )
        ),
    ]

    assert not fitter._merge_preserves_adjacent_topology(pieces, 0, merged)


def test_current_physical_corner_classifier_remains_authoritative() -> None:
    edge = np.linspace(-4.0, 4.0, 81, endpoint=False)
    points = np.vstack(
        (
            np.column_stack((edge, np.full_like(edge, -4.0))),
            np.column_stack((np.full_like(edge, 4.0), edge)),
            np.column_stack((-edge, np.full_like(edge, 4.0))),
            np.column_stack((np.full_like(edge, -4.0), -edge)),
        )
    )
    options = fitter.RasterVectorizationOptions(
        simplification_tolerance_mm=0.10,
    )

    corners = fitter._corner_indices(points, 0.065)
    fitted = fitter._fit_contour(
        points,
        options,
        10.0,
        10.0,
        fitter._ComplexityBudget(),
    )

    assert corners == [0, 81, 162, 243]
    assert fitted.hard_corner_count == 4
    assert fitted.merged_segment_count == 0
    assert all(isinstance(segment, fitter._LineSegment) for segment in fitted.segments)


def test_fit_validation_has_an_independent_work_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = np.asarray(((0.0, 0.0), (0.5, 0.0), (1.0, 0.0)))
    parameters = np.asarray((0.0, 0.5, 1.0))
    controls = np.asarray(
        ((0.0, 0.0), (1.0 / 3.0, 0.0), (2.0 / 3.0, 0.0), (1.0, 0.0))
    )
    monkeypatch.setattr(
        fitter,
        "MAX_RASTER_VECTORIZATION_FIT_VALIDATION_STEPS",
        1,
    )

    with pytest.raises(
        fitter.RasterVectorizationComplexityError,
        match="bounded curve-fit validation steps",
    ):
        fitter._validate_curve_fit(
            target,
            parameters,
            controls,
            FIT_TOLERANCE_MM,
            fitter._ComplexityBudget(),
        )
