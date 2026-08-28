from __future__ import annotations

import base64
import hashlib
import math
import zlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

import laser_aligner.project.raster_vectorize as fitter
from laser_aligner.project.raster_asset import read_raster_asset_payload

_COLEMAN_SOURCE_SHA256 = (
    "e72143e3b6ef3bd7ae2ff03bf49c90130317a194b69620a69b493667b1acc786"
)
_COLEMAN_P_ROI = (148, 315, 207, 386)
_COLEMAN_P_PIXELS_SHA256 = (
    "7f3cf7a2d80845557d75a7370b8abe879fcac976db5734d3b30b25bf257caf84"
)
_COLEMAN_P_COMPRESSED = (
    "c-rlj+YZ7Y42Ca=zJm8Yg75!Y^8_spwz&<~@<U=Wll_S%%zd=<fc1Va;`qhfKn9ALXx"
    "f6|Z6eiwXn@E$LfQCoR79P%IBH*yuDd_2b-`c_dZ;K9<cDJDt7=$&>!jCPCC8WNWxp"
    "PhT(UW{#w;H=YD1%C&8&uo2q>(I8nuHGG+{5HGf*_4Z{sZhqDh=llvDP!x`I@zEO}"
    "Ez=Xg%f&mg)?g|w%P@-|8>=ryh!5GKK6N9DW<9s^b-)aHkUQRZ?<NDea|ru8pP$uU"
    "?-h(-a_v))qWrdzh1Z|zru-iSF_iJDf_fMkxsWLh(@w>e54CR2wA%IJ2bk_W-f<Y1"
    "ui$B_-5cr~r2NBFE^xa8HmilddIk9HUA8?7JnevW3R!Hwo)5KPJw)S*LvqYvU`SX%"
)
_COLEMAN_P_SHAPE = (71, 59)
_COLEMAN_PITCH_MM = 80.0 / 1170.0
_FIT_TOLERANCE_MM = 0.10


@dataclass(frozen=True, slots=True)
class _ContourFit:
    raw: np.ndarray
    fitted: fitter._FittedContour


@dataclass(frozen=True, slots=True)
class _ErrorMetrics:
    maximum_mm: float
    rms_mm: float
    signed_mean_mm: float
    signed_min_mm: float
    signed_max_mm: float
    same_side_fraction: float


def _options(*, threshold: int = 122) -> fitter.RasterVectorizationOptions:
    return fitter.RasterVectorizationOptions(
        detection_mode=fitter.RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=threshold,
        minimum_feature_area_mm2=0.05,
        smoothing_mm=0.0,
        simplification_tolerance_mm=_FIT_TOLERANCE_MM,
        contour_output=fitter.RasterContourOutput.ALL_CONTOURS,
    )


def _coleman_p_crop() -> np.ndarray:
    payload = zlib.decompress(base64.b85decode(_COLEMAN_P_COMPRESSED))
    assert hashlib.sha256(payload).hexdigest() == _COLEMAN_P_PIXELS_SHA256
    return np.frombuffer(payload, dtype=np.uint8).reshape(_COLEMAN_P_SHAPE).copy()


def _fit_raster_outer_contours(
    path: Path,
    pixels: np.ndarray,
    *,
    pitch_mm: float,
    threshold: int = 122,
) -> list[_ContourFit]:
    assert cv2.imwrite(str(path), pixels)
    payload = read_raster_asset_payload(path)
    source = fitter.prepare_raster_vectorization_source(payload)
    width_mm = source.width_px * pitch_mm
    height_mm = source.height_px * pitch_mm
    options = _options(threshold=threshold)
    masks = fitter._prepare_vectorization_masks(
        source,
        options,
        width_mm,
        height_mm,
    )
    contours, hierarchy = fitter._extract_vectorization_contours(
        masks.working_mask,
        cv2.CHAIN_APPROX_NONE,
    )
    parents = hierarchy[0, :, 3]
    results: list[_ContourFit] = []
    for index, contour in enumerate(contours):
        if int(parents[index]) >= 0:
            continue
        physical = fitter._physical_contour(
            contour,
            source.width_px,
            source.height_px,
            width_mm,
            height_mm,
        )
        if fitter._signed_area(physical) < 0.0:
            physical = physical[::-1].copy()
        raw = fitter._canonicalize_closed_contour(physical)
        fitted = fitter._fit_contour(
            raw,
            options,
            width_mm,
            height_mm,
            fitter._ComplexityBudget(),
            source_pixel_spacing_mm=(pitch_mm, pitch_mm),
        )
        results.append(_ContourFit(raw, fitted))
    return results


def _segment_controls(segment: object) -> np.ndarray:
    if isinstance(segment, fitter._LineSegment):
        delta = segment.end - segment.start
        return np.vstack(
            (
                segment.start,
                segment.start + delta / 3.0,
                segment.start + 2.0 * delta / 3.0,
                segment.end,
            )
        )
    assert isinstance(segment, fitter._CubicSegment)
    return np.vstack(
        (segment.start, segment.control_1, segment.control_2, segment.end)
    )


def _project_parameter(
    point: np.ndarray,
    controls: np.ndarray,
    initial: float,
) -> float:
    parameter = initial
    for _iteration in range(12):
        value = fitter._cubic_values(*controls, np.asarray((parameter,)))[0]
        first, second = fitter._cubic_derivatives(controls, parameter)
        delta = value - point
        denominator = float(np.dot(first, first) + np.dot(delta, second))
        if abs(denominator) <= 1e-15:
            break
        refined = float(np.dot(delta, first)) / denominator
        candidate = min(1.0, max(0.0, parameter - refined))
        if abs(candidate - parameter) <= 1e-13:
            return candidate
        parameter = candidate
    return parameter


def _segment_target(
    raw: np.ndarray,
    segment: object,
) -> tuple[np.ndarray, np.ndarray]:
    start_index = int(np.argmin(np.linalg.norm(raw - segment.start, axis=1)))
    end_index = int(np.argmin(np.linalg.norm(raw - segment.end, axis=1)))
    indices = fitter._circular_indices(start_index, end_index, len(raw))
    np.testing.assert_allclose(raw[start_index], segment.start, atol=1e-12, rtol=0.0)
    np.testing.assert_allclose(raw[end_index], segment.end, atol=1e-12, rtol=0.0)
    return raw[indices], indices


def _error_metrics(target: np.ndarray, segment: object) -> _ErrorMetrics:
    controls = _segment_controls(segment)
    dense_parameters = np.linspace(0.0, 1.0, 1025)
    dense_values = fitter._cubic_values(*controls, dense_parameters)
    errors: list[float] = []
    signed_errors: list[float] = []
    for point in target:
        nearest = int(np.argmin(np.linalg.norm(dense_values - point, axis=1)))
        parameter = _project_parameter(
            point,
            controls,
            float(dense_parameters[nearest]),
        )
        value = fitter._cubic_values(*controls, np.asarray((parameter,)))[0]
        tangent, _second = fitter._cubic_derivatives(controls, parameter)
        normal = np.asarray((tangent[1], -tangent[0])) / float(
            np.linalg.norm(tangent)
        )
        error = value - point
        errors.append(float(np.linalg.norm(error)))
        signed_errors.append(float(np.dot(error, normal)))
    absolute = np.asarray(errors)
    signed = np.asarray(signed_errors)
    return _ErrorMetrics(
        maximum_mm=float(np.max(absolute)),
        rms_mm=math.sqrt(float(np.mean(absolute**2))),
        signed_mean_mm=float(np.mean(signed)),
        signed_min_mm=float(np.min(signed)),
        signed_max_mm=float(np.max(signed)),
        same_side_fraction=float(max(np.mean(signed > 0.0), np.mean(signed < 0.0))),
    )


def _longest_cubic(fit: _ContourFit) -> tuple[object, np.ndarray]:
    candidates: list[tuple[int, object, np.ndarray]] = []
    for segment in fit.fitted.segments:
        if not isinstance(segment, fitter._CubicSegment):
            continue
        target, _indices = _segment_target(fit.raw, segment)
        candidates.append((len(target), segment, target))
    _count, segment, target = max(candidates, key=lambda value: value[0])
    return segment, target


def _segment_sequence(fit: _ContourFit) -> str:
    return "".join(
        "L" if isinstance(segment, fitter._LineSegment) else "C"
        for segment in fit.fitted.segments
    )


def _d_bowl(
    *,
    center: tuple[float, float],
    angle_radians: float = 0.0,
    size: int = 180,
    outer_radius_px: float = 48.0,
    inner_radius_px: float = 30.0,
    supersample: int = 8,
) -> np.ndarray:
    angles = np.linspace(-math.pi / 2.0, math.pi / 2.0, 513)
    outer = np.column_stack(
        (outer_radius_px * np.cos(angles), outer_radius_px * np.sin(angles))
    )
    inner = np.column_stack(
        (
            inner_radius_px * np.cos(angles[::-1]),
            inner_radius_px * np.sin(angles[::-1]),
        )
    )
    local = np.vstack((outer, inner))
    cosine = math.cos(angle_radians)
    sine = math.sin(angle_radians)
    rotation = np.asarray(((cosine, -sine), (sine, cosine)))
    points = local @ rotation.T + np.asarray(center)
    high = np.full((size * supersample, size * supersample), 255, dtype=np.uint8)
    cv2.fillPoly(
        high,
        [np.rint(points * supersample).astype(np.int32)],
        0,
        lineType=cv2.LINE_8,
    )
    return cv2.resize(high, (size, size), interpolation=cv2.INTER_AREA)


def _sample_segments(segments: tuple[object, ...], count: int = 65) -> np.ndarray:
    parameters = np.linspace(0.0, 1.0, count, endpoint=False)
    return np.vstack(
        [fitter._cubic_values(*_segment_controls(segment), parameters) for segment in segments]
    )


def _ideal_d_distance_mm(
    physical_points: np.ndarray,
    *,
    image_size: int,
    pitch_mm: float,
    center: tuple[float, float],
    angle_radians: float,
    outer_radius_px: float = 48.0,
    inner_radius_px: float = 30.0,
) -> np.ndarray:
    image_points = np.column_stack(
        (
            physical_points[:, 0] / pitch_mm + image_size / 2.0,
            image_size / 2.0 - physical_points[:, 1] / pitch_mm,
        )
    )
    cosine = math.cos(angle_radians)
    sine = math.sin(angle_radians)
    rotation = np.asarray(((cosine, -sine), (sine, cosine)))
    local = (image_points - np.asarray(center)) @ rotation
    radius = np.linalg.norm(local, axis=1)
    outer = np.where(
        local[:, 0] >= 0.0,
        np.abs(radius - outer_radius_px),
        np.inf,
    )
    inner = np.where(
        local[:, 0] >= 0.0,
        np.abs(radius - inner_radius_px),
        np.inf,
    )
    top_y = np.clip(local[:, 1], -outer_radius_px, -inner_radius_px)
    bottom_y = np.clip(local[:, 1], inner_radius_px, outer_radius_px)
    top = np.hypot(local[:, 0], local[:, 1] - top_y)
    bottom = np.hypot(local[:, 0], local[:, 1] - bottom_y)
    return np.min(np.column_stack((outer, inner, top, bottom)), axis=1) * pitch_mm


def test_actual_coleman_p_bowl_refines_biased_long_cubic(
    tmp_path: Path,
) -> None:
    assert _COLEMAN_SOURCE_SHA256.startswith("e72143e3")
    assert _COLEMAN_P_ROI == (148, 315, 207, 386)
    fits = _fit_raster_outer_contours(
        tmp_path / "coleman-p-roi.png",
        _coleman_p_crop(),
        pitch_mm=_COLEMAN_PITCH_MM,
    )
    assert len(fits) == 2
    bowl = max(fits, key=lambda fit: float(np.mean(fit.raw[:, 0])))

    assert len(bowl.raw) == 392
    assert len(fitter._corner_indices(bowl.raw, 0.065)) == 4
    assert not fitter._persistent_straight_runs(
        bowl.raw,
        0.08,
        source_pixel_spacing_mm=(_COLEMAN_PITCH_MM, _COLEMAN_PITCH_MM),
    )
    assert _segment_sequence(bowl) == "LLCCCCLLCLLCLLC"

    outer_curve, target = _longest_cubic(bowl)
    assert len(target) == 147
    metrics = _error_metrics(target, outer_curve)
    assert outer_curve.fitting_error_mm <= 0.068
    assert metrics.maximum_mm <= 0.068
    assert metrics.rms_mm <= 0.034
    assert abs(metrics.signed_mean_mm) <= 0.006
    assert metrics.same_side_fraction <= 0.56


def test_analytic_d_bowl_tracks_threshold_and_known_geometry(tmp_path: Path) -> None:
    pitch_mm = 0.08
    center = (70.25, 90.5)
    pixels = _d_bowl(center=center)
    fit = _fit_raster_outer_contours(
        tmp_path / "analytic-d-bowl.png",
        pixels,
        pitch_mm=pitch_mm,
    )[0]

    assert any(isinstance(segment, fitter._CubicSegment) for segment in fit.fitted.segments)
    assert fit.fitted.max_fitting_error_mm <= 0.08
    for segment in fit.fitted.segments:
        target, _indices = _segment_target(fit.raw, segment)
        metrics = _error_metrics(target, segment)
        assert metrics.maximum_mm <= 0.08
        if len(target) >= 20:
            assert metrics.rms_mm <= 0.04
            assert abs(metrics.signed_mean_mm) <= 0.012
    ideal_errors = _ideal_d_distance_mm(
        _sample_segments(fit.fitted.segments),
        image_size=180,
        pitch_mm=pitch_mm,
        center=center,
        angle_radians=0.0,
    )
    assert float(np.max(ideal_errors)) <= 0.13
    assert math.sqrt(float(np.mean(ideal_errors**2))) <= 0.055


def test_analytic_d_bowl_is_invariant_to_one_pixel_translation(tmp_path: Path) -> None:
    pitch_mm = 0.08
    first = _fit_raster_outer_contours(
        tmp_path / "d-bowl-base.png",
        _d_bowl(center=(70.25, 90.5)),
        pitch_mm=pitch_mm,
    )[0]
    translated = _fit_raster_outer_contours(
        tmp_path / "d-bowl-translated.png",
        _d_bowl(center=(71.25, 91.5)),
        pitch_mm=pitch_mm,
    )[0]

    assert _segment_sequence(first) == _segment_sequence(translated)
    delta = np.asarray((pitch_mm, -pitch_mm))
    for first_segment, translated_segment in zip(
        first.fitted.segments,
        translated.fitted.segments,
        strict=True,
    ):
        np.testing.assert_allclose(
            _segment_controls(translated_segment) - _segment_controls(first_segment),
            np.broadcast_to(delta, (4, 2)),
            atol=1e-11,
            rtol=0.0,
        )


def test_rotated_analytic_d_bowl_remains_centered(tmp_path: Path) -> None:
    pitch_mm = 0.08
    center = (90.25, 90.5)
    angle = 0.53
    fit = _fit_raster_outer_contours(
        tmp_path / "rotated-d-bowl.png",
        _d_bowl(center=center, angle_radians=angle),
        pitch_mm=pitch_mm,
    )[0]

    assert any(isinstance(segment, fitter._CubicSegment) for segment in fit.fitted.segments)
    assert fit.fitted.max_fitting_error_mm <= 0.08
    long_curve_metrics = [
        _error_metrics(target, segment)
        for segment in fit.fitted.segments
        if isinstance(segment, fitter._CubicSegment)
        for target, _indices in [_segment_target(fit.raw, segment)]
        if len(target) >= 20
    ]
    assert long_curve_metrics
    assert max(metric.rms_mm for metric in long_curve_metrics) <= 0.04
    assert max(abs(metric.signed_mean_mm) for metric in long_curve_metrics) <= 0.0125
    ideal_errors = _ideal_d_distance_mm(
        _sample_segments(fit.fitted.segments),
        image_size=180,
        pitch_mm=pitch_mm,
        center=center,
        angle_radians=angle,
    )
    assert float(np.max(ideal_errors)) <= 0.14


def test_genuine_shallow_arc_is_not_promoted_to_a_line() -> None:
    x_values = np.linspace(-4.0, 4.0, 321)
    arc = np.column_stack(
        (x_values, 1.0 + 0.05 * (1.0 - (x_values / 4.0) ** 2))
    )
    pieces = fitter._fit_span_pieces(
        arc,
        0.08,
        fitter._ComplexityBudget(),
        start_tangent=arc[1] - arc[0],
        end_tangent=arc[-2] - arc[-1],
        prefer_cubic_leaves=True,
        allow_unconstrained_line=False,
        allow_tangent_line=False,
        control_minimum=np.asarray((-5.0, -1.0)),
        control_maximum=np.asarray((5.0, 2.0)),
    )
    segments = [piece.segment for piece in pieces]

    assert segments
    assert all(isinstance(segment, fitter._CubicSegment) for segment in segments)
    assert max(segment.fitting_error_mm for segment in segments) <= 0.08


def _dense_polygon(vertices: np.ndarray, points_per_edge: int = 100) -> np.ndarray:
    return np.vstack(
        [
            start
            + (end - start)
            * (np.arange(points_per_edge, dtype=np.float64)[:, None] / points_per_edge)
            for start, end in zip(vertices, np.roll(vertices, -1, axis=0), strict=True)
        ]
    )


def test_e_style_straight_edges_remain_native_lines() -> None:
    points = _dense_polygon(
        np.asarray(
            (
                (-4.0, -5.0),
                (4.0, -5.0),
                (4.0, -3.5),
                (-1.5, -3.5),
                (-1.5, -0.75),
                (3.0, -0.75),
                (3.0, 0.75),
                (-1.5, 0.75),
                (-1.5, 3.5),
                (4.0, 3.5),
                (4.0, 5.0),
                (-4.0, 5.0),
            )
        )
    )
    fitted = fitter._fit_contour(
        points,
        _options(),
        12.0,
        12.0,
        fitter._ComplexityBudget(),
        source_pixel_spacing_mm=(0.025, 0.025),
    )

    long_lines = [
        segment
        for segment in fitted.segments
        if isinstance(segment, fitter._LineSegment)
        and float(np.linalg.norm(segment.end - segment.start)) >= 4.0
    ]
    assert len(long_lines) >= 3
    assert all(segment.fitting_error_mm <= 0.08 for segment in long_lines)


def test_rounded_c_and_o_regions_remain_cubic() -> None:
    angles = np.linspace(0.0, 2.0 * math.pi, 801, endpoint=False)
    circle = np.column_stack((4.0 * np.cos(angles), 4.0 * np.sin(angles)))
    outer_angles = np.linspace(0.7, 2.0 * math.pi - 0.7, 500, endpoint=False)
    inner_angles = np.linspace(2.0 * math.pi - 0.7, 0.7, 350, endpoint=False)
    letter_c = np.vstack(
        (
            np.column_stack((4.0 * np.cos(outer_angles), 4.0 * np.sin(outer_angles))),
            np.column_stack((2.5 * np.cos(inner_angles), 2.5 * np.sin(inner_angles))),
        )
    )

    for points in (circle, letter_c):
        fitted = fitter._fit_contour(
            points,
            _options(),
            12.0,
            12.0,
            fitter._ComplexityBudget(),
            source_pixel_spacing_mm=(0.025, 0.025),
        )
        assert any(
            isinstance(segment, fitter._CubicSegment)
            for segment in fitted.segments
        )
        assert fitted.max_fitting_error_mm <= 0.08
