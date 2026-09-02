from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from laser_aligner.project.primitive_recovery import (
    CircularArcHypothesis,
    PrimitiveErrorBudget,
    canonical_cubic_arc_spans,
    canonical_cubic_radial_error,
    fit_circular_arc_hypothesis,
    fit_line_hypothesis,
    line_intersection,
    primitive_error_budget,
)

_SOURCE_PITCH_MM = 0.2


def _quantized_line_points(
    angle_degrees: float,
    *,
    length_mm: float = 40.0,
    sample_count: int = 401,
) -> tuple[np.ndarray, np.ndarray]:
    angle = math.radians(angle_degrees)
    direction = np.asarray((math.cos(angle), math.sin(angle)), dtype=np.float64)
    distances = np.linspace(-length_mm / 2.0, length_mm / 2.0, sample_count)
    points = distances[:, None] * direction
    points = np.round(points / _SOURCE_PITCH_MM) * _SOURCE_PITCH_MM
    keep = np.concatenate(
        (
            np.asarray((True,)),
            np.any(np.diff(points, axis=0) != 0.0, axis=1),
        )
    )
    return points[keep], direction


def _exact_line_points(
    angle_degrees: float,
    *,
    length_mm: float = 20.0,
    origin: tuple[float, float] = (0.0, 0.0),
    sample_count: int = 81,
) -> np.ndarray:
    angle = math.radians(angle_degrees)
    direction = np.asarray((math.cos(angle), math.sin(angle)), dtype=np.float64)
    distances = np.linspace(0.0, length_mm, sample_count)
    return np.asarray(origin, dtype=np.float64) + distances[:, None] * direction


def _arc_points(
    *,
    center: tuple[float, float] = (2.5, -1.25),
    radius_mm: float = 10.0,
    start_degrees: float = 0.0,
    sweep_degrees: float = 360.0,
    sample_count: int = 161,
    radial_noise_mm: float = 0.0,
) -> np.ndarray:
    phase = np.linspace(0.0, 1.0, sample_count)
    angles = math.radians(start_degrees) + math.radians(sweep_degrees) * phase
    # The deterministic perturbation is zero at both endpoints, so an open arc
    # retains its observed endpoints and a closed circle retains exact closure.
    radial_noise = radial_noise_mm * np.sin(6.0 * math.pi * phase)
    radii = radius_mm + radial_noise
    return np.column_stack(
        (
            center[0] + radii * np.cos(angles),
            center[1] + radii * np.sin(angles),
        )
    )


def _angle_difference_radians(first: float, second: float) -> float:
    return abs(math.atan2(math.sin(first - second), math.cos(first - second)))


def _unit(vector: np.ndarray) -> np.ndarray:
    return vector / np.linalg.norm(vector)


@pytest.mark.parametrize("angle_degrees", (0.0, 90.0, 1.7, 13.0, 45.0))
def test_tls_line_recovers_quantized_arbitrary_angles(angle_degrees: float) -> None:
    points, expected_direction = _quantized_line_points(angle_degrees)

    hypothesis = fit_line_hypothesis(
        points,
        tolerance_mm=0.35,
        source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
    )

    assert hypothesis is not None
    assert float(np.dot(hypothesis.direction, expected_direction)) > math.cos(math.radians(0.15))
    assert hypothesis.sample_count == len(points)
    assert hypothesis.inlier_count >= math.ceil(0.9 * len(points))
    assert hypothesis.support_length_mm > 0.95 * 40.0
    assert hypothesis.maximum_residual_mm <= hypothesis.error_budget.maximum_mm
    assert hypothesis.rms_residual_mm <= hypothesis.error_budget.rms_mm
    assert hypothesis.maximum_endpoint_adjustment_mm <= hypothesis.error_budget.endpoint_adjustment_mm
    assert np.dot(hypothesis.end_projection - hypothesis.start_projection, hypothesis.direction) > 0


@pytest.mark.parametrize(
    "points",
    (
        np.column_stack(
            (
                np.linspace(-30.0, 30.0, 241),
                0.35 * np.sin(2.0 * math.pi * np.linspace(-30.0, 30.0, 241) / 8.0),
            )
        ),
        np.column_stack(
            (
                np.linspace(-20.0, 20.0, 201),
                0.012 * np.linspace(-20.0, 20.0, 201) ** 2,
            )
        ),
    ),
    ids=("wavy", "curved"),
)
def test_tls_line_rejects_non_straight_support(points: np.ndarray) -> None:
    assert (
        fit_line_hypothesis(
            points,
            tolerance_mm=0.4,
            source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
        )
        is None
    )


def test_tls_line_rejects_insufficient_samples_and_physical_support() -> None:
    too_few_samples = _exact_line_points(13.0, sample_count=11)
    too_short = _exact_line_points(13.0, length_mm=1.0, sample_count=40)

    assert (
        fit_line_hypothesis(
            too_few_samples,
            tolerance_mm=0.2,
            source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
        )
        is None
    )
    assert (
        fit_line_hypothesis(
            too_short,
            tolerance_mm=0.2,
            source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
        )
        is None
    )


def test_oversized_tolerance_does_not_hallucinate_a_line() -> None:
    x_coordinates = np.linspace(-60.0, 60.0, 481)
    curved_points = np.column_stack((x_coordinates, 0.0005 * x_coordinates**2))

    budget = primitive_error_budget(
        5.0,
        (_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
        normal=np.asarray((0.0, 1.0)),
    )

    assert budget.maximum_mm == pytest.approx(0.12)
    assert budget.rms_mm == pytest.approx(0.06)
    assert (
        fit_line_hypothesis(
            curved_points,
            tolerance_mm=5.0,
            source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
        )
        is None
    )


def test_error_budget_projects_anisotropic_source_pitch_onto_the_normal() -> None:
    horizontal_normal = primitive_error_budget(
        1.0,
        (0.1, 0.3),
        normal=np.asarray((1.0, 0.0)),
    )
    vertical_normal = primitive_error_budget(
        1.0,
        (0.1, 0.3),
        normal=np.asarray((0.0, 1.0)),
    )
    diagonal_normal = primitive_error_budget(
        1.0,
        (0.1, 0.3),
        normal=np.asarray((1.0, 1.0)),
    )

    assert horizontal_normal.source_normal_pitch_mm == pytest.approx(0.1)
    assert vertical_normal.source_normal_pitch_mm == pytest.approx(0.3)
    assert diagonal_normal.source_normal_pitch_mm == pytest.approx(
        0.4 / math.sqrt(2.0)
    )
    assert horizontal_normal.maximum_mm == pytest.approx(0.06)
    assert vertical_normal.maximum_mm == pytest.approx(0.18)


def test_line_intersection_returns_a_nearby_perpendicular_corner() -> None:
    first = fit_line_hypothesis(
        _exact_line_points(0.0, origin=(-20.0, 0.0)),
        tolerance_mm=0.3,
        source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
    )
    second = fit_line_hypothesis(
        _exact_line_points(90.0),
        tolerance_mm=0.3,
        source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
    )
    assert first is not None
    assert second is not None

    result = line_intersection(
        first,
        second,
        observed_corner=np.asarray((0.04, -0.03)),
    )

    assert result is not None
    intersection, adjustment_mm = result
    np.testing.assert_allclose(intersection, (0.0, 0.0), atol=1e-12)
    assert adjustment_mm == pytest.approx(0.05)
    assert (
        line_intersection(
            first,
            second,
            observed_corner=np.asarray((0.3, 0.0)),
        )
        is None
    )


def test_line_intersection_rejects_near_parallel_hypotheses() -> None:
    first = fit_line_hypothesis(
        _exact_line_points(0.0),
        tolerance_mm=0.3,
        source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
    )
    second = fit_line_hypothesis(
        _exact_line_points(4.0),
        tolerance_mm=0.3,
        source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
    )
    assert first is not None
    assert second is not None

    assert (
        line_intersection(
            first,
            second,
            observed_corner=np.asarray((0.0, 0.0)),
        )
        is None
    )


@pytest.mark.parametrize("radial_noise_mm", (0.0, 0.015), ids=("exact", "noisy"))
def test_circle_hypothesis_recovers_exact_and_noisy_closed_circles(
    radial_noise_mm: float,
) -> None:
    expected_center = np.asarray((2.5, -1.25))
    points = _arc_points(radial_noise_mm=radial_noise_mm)

    hypothesis = fit_circular_arc_hypothesis(
        points,
        tolerance_mm=0.2,
        source_pixel_spacing_mm=(0.1, 0.1),
        closed=True,
    )

    assert hypothesis is not None
    np.testing.assert_allclose(hypothesis.center, expected_center, atol=0.025)
    assert hypothesis.radius_mm == pytest.approx(10.0, abs=0.02)
    assert hypothesis.sweep_radians == pytest.approx(2.0 * math.pi, abs=1e-12)
    assert hypothesis.arc_length_mm == pytest.approx(20.0 * math.pi, rel=0.003)
    assert hypothesis.maximum_residual_mm <= hypothesis.error_budget.maximum_mm
    assert hypothesis.rms_residual_mm <= hypothesis.error_budget.rms_mm


@pytest.mark.parametrize("radial_noise_mm", (0.0, 0.012), ids=("exact", "noisy"))
def test_partial_arc_hypothesis_recovers_center_radius_and_sweep(
    radial_noise_mm: float,
) -> None:
    expected_center = np.asarray((-3.0, 4.0))
    expected_start = math.radians(-50.0)
    expected_sweep = math.radians(135.0)
    points = _arc_points(
        center=(-3.0, 4.0),
        radius_mm=8.0,
        start_degrees=-50.0,
        sweep_degrees=135.0,
        sample_count=101,
        radial_noise_mm=radial_noise_mm,
    )

    hypothesis = fit_circular_arc_hypothesis(
        points,
        tolerance_mm=0.2,
        source_pixel_spacing_mm=(0.1, 0.1),
    )

    assert hypothesis is not None
    np.testing.assert_allclose(hypothesis.center, expected_center, atol=0.03)
    assert hypothesis.radius_mm == pytest.approx(8.0, abs=0.03)
    assert _angle_difference_radians(
        hypothesis.start_angle_radians,
        expected_start,
    ) < math.radians(0.2)
    assert hypothesis.sweep_radians == pytest.approx(
        expected_sweep,
        abs=math.radians(0.3),
    )
    assert hypothesis.angular_backtrack_radians <= math.radians(0.05)


def test_circle_hypothesis_rejects_insufficient_samples() -> None:
    points = _arc_points(
        radius_mm=8.0,
        sweep_degrees=120.0,
        sample_count=19,
    )

    assert (
        fit_circular_arc_hypothesis(
            points,
            tolerance_mm=0.2,
            source_pixel_spacing_mm=(0.1, 0.1),
        )
        is None
    )


def test_circle_hypothesis_rejects_ellipse_and_freeform_curve() -> None:
    phase = np.linspace(0.0, 2.0 * math.pi, 161)
    ellipse = np.column_stack((12.0 * np.cos(phase), 7.0 * np.sin(phase)))
    x_coordinates = np.linspace(-9.0, 9.0, 181)
    freeform = np.column_stack((x_coordinates, 2.2 * np.sin(x_coordinates / 2.4)))

    assert (
        fit_circular_arc_hypothesis(
            ellipse,
            tolerance_mm=0.2,
            source_pixel_spacing_mm=(0.1, 0.1),
            closed=True,
        )
        is None
    )
    # Even a tolerance much larger than the source pitch remains capped by
    # resolved evidence and must not promote the ellipse to a circle.
    assert (
        fit_circular_arc_hypothesis(
            ellipse,
            tolerance_mm=5.0,
            source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
            closed=True,
        )
        is None
    )
    assert (
        fit_circular_arc_hypothesis(
            freeform,
            tolerance_mm=0.2,
            source_pixel_spacing_mm=(0.1, 0.1),
        )
        is None
    )


def test_open_arc_rejects_backtracking_and_more_than_one_turn() -> None:
    backtracking = _arc_points(
        radius_mm=8.0,
        sweep_degrees=120.0,
        sample_count=61,
    )
    backtracking[[20, 30]] = backtracking[[30, 20]]
    multi_turn = _arc_points(
        radius_mm=8.0,
        sweep_degrees=700.0,
        sample_count=241,
    )

    for points in (backtracking, multi_turn):
        assert (
            fit_circular_arc_hypothesis(
                points,
                tolerance_mm=0.2,
                source_pixel_spacing_mm=(0.1, 0.1),
            )
            is None
        )


def _canonical_hypothesis(sweep_radians: float) -> CircularArcHypothesis:
    return CircularArcHypothesis(
        center=np.asarray((1.5, -2.0), dtype=np.float64),
        radius_mm=10.0,
        start_angle_radians=math.radians(17.0),
        sweep_radians=sweep_radians,
        support_length_mm=abs(sweep_radians) * 10.0,
        maximum_residual_mm=0.0,
        rms_residual_mm=0.0,
        signed_mean_residual_mm=0.0,
        maximum_endpoint_adjustment_mm=0.0,
        angular_backtrack_radians=0.0,
        stability_center_mm=0.0,
        stability_radius_mm=0.0,
        error_budget=PrimitiveErrorBudget(
            maximum_mm=0.1,
            rms_mm=0.05,
            endpoint_adjustment_mm=0.1,
            source_normal_pitch_mm=0.2,
            representation_mm=0.01,
        ),
    )


@pytest.mark.parametrize(
    ("sweep_radians", "expected_count"),
    (
        (math.pi / 2.0, 1),
        (math.pi, 2),
        (-3.0 * math.pi / 2.0, 3),
        (2.0 * math.pi, 4),
    ),
)
def test_canonical_cubic_arc_uses_bounded_quarter_circle_spans(
    sweep_radians: float,
    expected_count: int,
) -> None:
    hypothesis = _canonical_hypothesis(sweep_radians)

    spans = canonical_cubic_arc_spans(hypothesis)

    assert len(spans) == expected_count
    assert sum(span.sweep_radians for span in spans) == pytest.approx(sweep_radians)
    for previous, current in zip(spans[:-1], spans[1:], strict=True):
        np.testing.assert_allclose(previous.end, current.start, atol=1e-12)


def test_full_circle_canonical_cubics_close_with_tangent_continuity_and_bounded_error() -> None:
    hypothesis = _canonical_hypothesis(2.0 * math.pi)

    spans = canonical_cubic_arc_spans(hypothesis)

    assert len(spans) == 4
    np.testing.assert_allclose(spans[-1].end, spans[0].start, atol=1e-12)
    for index, span in enumerate(spans):
        following = spans[(index + 1) % len(spans)]
        np.testing.assert_allclose(span.end, following.start, atol=1e-12)
        outgoing_tangent = _unit(span.end - span.control_2)
        incoming_tangent = _unit(following.control_1 - following.start)
        np.testing.assert_allclose(outgoing_tangent, incoming_tangent, atol=1e-12)

        controls = np.vstack((span.start, span.control_1, span.control_2, span.end))
        exact_error = canonical_cubic_radial_error(
            controls,
            hypothesis.center,
            hypothesis.radius_mm,
        )
        assert span.maximum_representation_error_mm == pytest.approx(
            exact_error,
            abs=1e-14,
        )
        assert exact_error < 0.0028
        assert exact_error <= hypothesis.error_budget.representation_mm


def test_canonical_arc_rejects_when_the_bounded_span_cap_is_insufficient() -> None:
    hypothesis = _canonical_hypothesis(math.pi / 2.0)
    hypothesis = replace(
        hypothesis,
        error_budget=replace(
            hypothesis.error_budget,
            representation_mm=1e-6,
        ),
    )

    with pytest.raises(ValueError, match="bounded canonical-cubic span limit"):
        canonical_cubic_arc_spans(hypothesis, maximum_segments=1)
