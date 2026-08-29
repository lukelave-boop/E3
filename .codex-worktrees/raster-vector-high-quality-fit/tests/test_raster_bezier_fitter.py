from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

import laser_aligner.project.raster_vectorize as fitter
from laser_aligner.project import (
    PathCubicSegment,
    PathLineSegment,
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationComplexityError,
    RasterVectorizationOptions,
    read_raster_asset_payload,
    vectorize_raster_payload,
)

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
) -> list[fitter._FittedPiece]:
    return fitter._fit_span(
        points,
        tolerance_mm,
        _unit(start_tangent),
        _unit(end_tangent),
        np.min(points, axis=0) - 10.0,
        np.max(points, axis=0) + 10.0,
        fitter._ComplexityBudget(),
        allow_line=True,
        hard_start=False,
        hard_end=False,
    )


def _manual_options() -> RasterVectorizationOptions:
    return RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=127,
        minimum_feature_area_mm2=0.0,
        smoothing_mm=0.0,
        simplification_tolerance_mm=FIT_TOLERANCE_MM,
        contour_output=RasterContourOutput.ALL_CONTOURS,
    )


def _write_payload(path: Path, pixels: np.ndarray):
    assert cv2.imwrite(str(path), pixels)
    return read_raster_asset_payload(path)


def test_quarter_circle_is_one_long_cubic_at_strict_tolerance() -> None:
    angles = np.linspace(0.0, math.pi / 2.0, 201)
    points = np.column_stack((10.0 * np.cos(angles), 10.0 * np.sin(angles)))

    pieces = _fit_open(points, np.asarray((0.0, 1.0)), np.asarray((1.0, 0.0)))

    assert len(pieces) == 1
    assert isinstance(pieces[0].segment, fitter._CubicSegment)
    assert pieces[0].segment.fitting_error_mm <= FIT_TOLERANCE_MM


def test_semicircle_uses_only_a_handful_of_long_cubics() -> None:
    angles = np.linspace(0.0, math.pi, 401)
    points = np.column_stack((10.0 * np.cos(angles), 10.0 * np.sin(angles)))

    pieces = _fit_open(points, np.asarray((0.0, 1.0)), np.asarray((0.0, 1.0)))

    assert 2 <= len(pieces) <= 4
    assert all(isinstance(piece.segment, fitter._CubicSegment) for piece in pieces)
    assert max(piece.segment.fitting_error_mm for piece in pieces) <= FIT_TOLERANCE_MM


def test_exact_s_curve_is_reconstructed_as_one_cubic() -> None:
    controls = np.asarray(((0.0, 0.0), (2.0, 4.0), (4.0, -4.0), (6.0, 0.0)))
    parameters = np.linspace(0.0, 1.0, 301)
    points = fitter._cubic_values(*controls, parameters)

    pieces = _fit_open(
        points,
        controls[1] - controls[0],
        controls[2] - controls[3],
    )

    assert len(pieces) == 1
    assert isinstance(pieces[0].segment, fitter._CubicSegment)
    assert pieces[0].segment.fitting_error_mm <= FIT_TOLERANCE_MM


def test_adaptive_validation_rejects_between_sample_s_lobes() -> None:
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


def test_recursive_smooth_fit_preserves_g1_at_every_join() -> None:
    x_values = np.linspace(0.0, 12.0, 601)
    points = np.column_stack((x_values, 2.0 * np.sin(x_values)))
    pieces = _fit_open(
        points,
        np.asarray((1.0, 2.0)),
        -np.asarray((1.0, 2.0 * math.cos(12.0))),
        tolerance_mm=0.005,
    )

    assert len(pieces) > 1
    assert all(isinstance(piece.segment, fitter._CubicSegment) for piece in pieces)
    for first, second in zip(pieces[:-1], pieces[1:], strict=True):
        first_segment = first.segment
        second_segment = second.segment
        assert isinstance(first_segment, fitter._CubicSegment)
        assert isinstance(second_segment, fitter._CubicSegment)
        incoming = first_segment.end - first_segment.control_2
        outgoing = second_segment.control_1 - second_segment.start
        cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
        assert cross == pytest.approx(0.0, abs=1e-12)
        assert float(np.dot(incoming, outgoing)) > 0.0


def test_true_long_diagonal_remains_one_native_line() -> None:
    parameters = np.linspace(0.0, 1.0, 501)
    start = np.asarray((-10.0, -4.0))
    end = np.asarray((10.0, 4.0))
    points = start + parameters[:, None] * (end - start)

    pieces = _fit_open(points, end - start, start - end)

    assert len(pieces) == 1
    assert isinstance(pieces[0].segment, fitter._LineSegment)
    assert pieces[0].segment.fitting_error_mm <= FIT_TOLERANCE_MM


def test_raster_circle_at_zero_user_smoothing_uses_long_cubics(
    tmp_path: Path,
) -> None:
    pixels = np.full((256, 256, 3), 255, dtype=np.uint8)
    cv2.circle(pixels, (128, 128), 85, (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "circle.png", pixels)

    result = vectorize_raster_payload(
        payload,
        _manual_options(),
        displayed_width_mm=40.0,
        displayed_height_mm=40.0,
    )

    contour = result.contours[0]
    assert 4 <= contour.fitted_segment_count <= 16
    assert all(
        isinstance(segment, PathCubicSegment)
        for segment in contour.native_subpath.segments
    )
    assert contour.hard_corner_count == 0
    assert contour.max_fitting_error_mm <= FIT_TOLERANCE_MM
    assert contour.smoothing_displacement_mm == 0.0
    assert contour.trace_cleanup_deviation_mm > 0.0
    assert contour.trace_cleanup_deviation_mm <= (
        40.0 / 256.0 + 0.2 * FIT_TOLERANCE_MM + 1e-12
    )
    assert (
        contour.mean_fitting_error_mm
        <= contour.rms_fitting_error_mm
        <= contour.max_fitting_error_mm
    )
    assert contour.max_estimated_deviation_mm == pytest.approx(
        contour.trace_cleanup_deviation_mm + contour.max_fitting_error_mm
    )
    metadata = result.metadata()
    assert metadata["raster_vectorization_max_fitting_error_mm"] <= FIT_TOLERANCE_MM
    assert metadata["raster_vectorization_max_trace_cleanup_deviation_mm"] > 0.0


def test_mixed_capsule_preserves_lines_curves_frame_and_determinism(
    tmp_path: Path,
) -> None:
    pixels = np.full((128, 256, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (64, 32), (192, 96), (0, 0, 0), thickness=-1)
    cv2.circle(pixels, (64, 64), 32, (0, 0, 0), thickness=-1)
    cv2.circle(pixels, (192, 64), 32, (0, 0, 0), thickness=-1)
    payload = _write_payload(tmp_path / "capsule.png", pixels)

    first = vectorize_raster_payload(
        payload,
        _manual_options(),
        displayed_width_mm=40.0,
        displayed_height_mm=20.0,
    )
    second = vectorize_raster_payload(
        payload,
        _manual_options(),
        displayed_width_mm=40.0,
        displayed_height_mm=20.0,
    )

    assert first.project_path_geometry() == second.project_path_geometry()
    contour = first.contours[0]
    assert sum(
        isinstance(segment, PathLineSegment)
        for segment in contour.native_subpath.segments
    ) == 2
    assert sum(
        isinstance(segment, PathCubicSegment)
        for segment in contour.native_subpath.segments
    ) == 2
    for segment in contour.native_subpath.segments:
        if isinstance(segment, PathCubicSegment):
            assert all(
                -0.5 <= coordinate <= 0.5
                for point in (segment.control_1, segment.control_2)
                for coordinate in point
            )


def test_square_corners_remain_hard_and_are_not_forced_g1() -> None:
    edge = np.linspace(-4.0, 4.0, 81, endpoint=False)
    points = np.vstack(
        (
            np.column_stack((edge, np.full_like(edge, -4.0))),
            np.column_stack((np.full_like(edge, 4.0), edge)),
            np.column_stack((-edge, np.full_like(edge, 4.0))),
            np.column_stack((np.full_like(edge, -4.0), -edge)),
        )
    )
    corners = fitter._corner_indices(points, FIT_TOLERANCE_MM)
    fitted = fitter._fit_contour(
        points,
        _manual_options(),
        fitter._ComplexityBudget(),
        10.0,
        10.0,
    )

    assert len(corners) == 4
    assert fitted.hard_corner_count == 4
    assert len(fitted.segments) == 4
    assert all(isinstance(segment, fitter._LineSegment) for segment in fitted.segments)
    directions = [segment.end - segment.start for segment in fitted.segments]
    for first, second in zip(directions, directions[1:] + directions[:1], strict=True):
        assert float(np.dot(_unit(first), _unit(second))) == pytest.approx(0.0)


def test_continuous_fit_validation_has_an_independent_complexity_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = np.asarray(((0.0, 0.0), (1.0, 0.0)))
    controls = np.asarray(
        ((0.0, 0.0), (1.0 / 3.0, 0.0), (2.0 / 3.0, 0.0), (1.0, 0.0))
    )
    monkeypatch.setattr(fitter, "MAX_RASTER_VECTORIZATION_FIT_VALIDATION_STEPS", 1)

    with pytest.raises(
        RasterVectorizationComplexityError,
        match="bounded curve-fit validation steps",
    ):
        fitter._validate_curve_fit(
            target,
            np.asarray((0.0, 1.0)),
            controls,
            FIT_TOLERANCE_MM,
            fitter._ComplexityBudget(),
        )


def test_default_fitting_tolerance_is_one_hundredth_millimetre() -> None:
    assert RasterVectorizationOptions().simplification_tolerance_mm == 0.01
