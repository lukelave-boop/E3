from __future__ import annotations

import base64
import hashlib
import math
import zlib
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest

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
_COLEMAN_GLYPH_CROPS = {
    "A": (
        (71, 45),
        "0cbcedeca62501e1328d635a0d09ef80a6c4da4182e207fbc3e2b714d15d6891",
        "c-rmN%M!yN2nEo^{r?|s1>0#dO?+?#)2u4HIA@3=Avv#D5exwL;aV%Rm#{EfYcXErz_tDA-flxUTR6LLOwVA|+fx4yUp%5q?~myyh(@aCz|vxvI<*NAIpV2QLtNCg(n<$Fg@u39Q+Qi?@&QodG?*5wnZQ$zy0Tc&Sqy?P#(;&X{VXyJ56%{re&Qqz#`y}%qbS9_&DA>5i#0GDUC?6IUb>GCIGXW|W!Sh!n{QYjd|)E<+h6gA@dDqA89D",
    ),
    "E": (
        (71, 45),
        "a532911240e09306d173afcc950efc481d2a905fdc4c383b54795c6e129536af",
        "c-rmOy$*mN3<Thz@Bh9I-qjd?1{A!iNKCke!4HE)i|*wR%QymlnGFMUL9c^iXGYofypz4!md$aJnXb(OTE(n}ub1(p7+$Js9%ruOhuCXj${1;jDd9sqy0?SMEQ@H1=#McX4>&SVx`Ag$hPo;1nWb%Ddgrg-=QbD-%;syp`3)-lxz*8D8O!Bh_^+U+C&>iz3OLX",
    ),
    "S": (
        (71, 56),
        "331c4774887da48a202795eb1f6fb4322a8109b578997e557bd1fe4b399a6ed8",
        "c-rlk+meDX42CbVPvD*V{tvlA4i@M_18GRnQO6-Og3x><rGH`-`CQ}iNI}2lk~#8lLZs1|XN-f15ogO>AjRbJ*9{yIrc=t9sZRs9o1E4p2$*cTQ+^l0^y!H$4rVK!k;!2_$ifhy!ofe6g~LjJQ0GADRCC~QP?I+cY~9qIX==QhA%mHfD2~T~io2Rrxf(g%3aw*#Ow6|#;)a4+S0U_xvYndL-nnc#%oe=*ATn1v40s^rZewu29*8uU-O<1U6NOmmgy50HwRj>wK3MO&7dND5he3IJ$nOD@B!W6$bE=vfKwT=wruZCCUIRWJP@W7jS)<*4z;bwANTPm+D~FdJo22J*tnyLYxnR8Ur>AJ@9Gu?_8u{;*+_KvZte?9tXpw)Q0FjKW8p^2j>zcHw9FIx*H1nU-X)m)oQDmjQ",
    ),
}
_COLEMAN_PITCH_MM = 80.0 / 1170.0
_FIT_TOLERANCE_MM = 0.10


@dataclass(frozen=True, slots=True)
class _ContourFit:
    threshold_raw: np.ndarray
    raw: np.ndarray
    signed_source_displacements_mm: np.ndarray
    source_edge: fitter._SourceEdgeRefinement
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


def _coleman_glyph_crop(glyph: str) -> np.ndarray:
    shape, expected_sha256, compressed = _COLEMAN_GLYPH_CROPS[glyph]
    payload = zlib.decompress(base64.b85decode(compressed))
    assert hashlib.sha256(payload).hexdigest() == expected_sha256
    return np.frombuffer(payload, dtype=np.uint8).reshape(shape).copy()


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
        source_edge = fitter._refine_contour_source_edges(
            physical,
            source,
            options,
            masks.threshold_used,
            width_mm,
            height_mm,
        )
        canonical_start = fitter._minimal_cyclic_rotation_index(physical)
        threshold_raw = np.roll(physical, -canonical_start, axis=0).copy()
        raw = np.roll(source_edge.points, -canonical_start, axis=0).copy()
        signed_displacements = np.roll(
            source_edge.signed_displacements_mm,
            -canonical_start,
        ).copy()
        fitted = fitter._fit_contour(
            raw,
            options,
            width_mm,
            height_mm,
            fitter._ComplexityBudget(),
            source_pixel_spacing_mm=(pitch_mm, pitch_mm),
            classification_points=threshold_raw,
        )
        results.append(
            _ContourFit(
                threshold_raw,
                raw,
                signed_displacements,
                source_edge,
                fitted,
            )
        )
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


def _fit_errors_against_points(
    points: np.ndarray,
    segments: tuple[object, ...],
) -> np.ndarray:
    parameters = np.linspace(0.0, 1.0, 129)
    sampled = np.stack(
        [
            fitter._cubic_values(*_segment_controls(segment), parameters)
            for segment in segments
        ]
    )
    errors: list[float] = []
    for point in points:
        distances = np.linalg.norm(sampled - point[None, None, :], axis=2)
        segment_index, sample_index = np.unravel_index(
            np.argmin(distances),
            distances.shape,
        )
        controls = _segment_controls(segments[segment_index])
        parameter = _project_parameter(
            point,
            controls,
            float(parameters[sample_index]),
        )
        value = fitter._cubic_values(*controls, np.asarray((parameter,)))[0]
        errors.append(float(np.linalg.norm(value - point)))
    return np.asarray(errors)


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


def _centered_segment_samples(fit: _ContourFit) -> np.ndarray:
    points = _sample_segments(fit.fitted.segments)
    return points - (np.min(points, axis=0) + np.max(points, axis=0)) / 2.0


def _symmetric_maximum_distance(first: np.ndarray, second: np.ndarray) -> float:
    distances = np.linalg.norm(first[:, None, :] - second[None, :, :], axis=2)
    return max(
        float(np.max(np.min(distances, axis=1))),
        float(np.max(np.min(distances, axis=0))),
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
    assert len(fitter._corner_indices(bowl.threshold_raw, 0.065)) == 4
    assert not fitter._persistent_straight_runs(
        bowl.threshold_raw,
        0.08,
        source_pixel_spacing_mm=(_COLEMAN_PITCH_MM, _COLEMAN_PITCH_MM),
    )
    baseline = fitter._fit_contour(
        bowl.threshold_raw,
        _options(),
        _COLEMAN_P_SHAPE[1] * _COLEMAN_PITCH_MM,
        _COLEMAN_P_SHAPE[0] * _COLEMAN_PITCH_MM,
        fitter._ComplexityBudget(),
        source_pixel_spacing_mm=(_COLEMAN_PITCH_MM, _COLEMAN_PITCH_MM),
    )
    baseline_fit = _ContourFit(
        bowl.threshold_raw,
        bowl.threshold_raw,
        np.zeros(len(bowl.raw)),
        fitter._unchanged_source_edge(bowl.threshold_raw),
        baseline,
    )
    assert _segment_sequence(baseline_fit) == "LLCCCCLLCLLCLLC"
    assert _segment_sequence(bowl) == "LLCCCLLCLLCLLC"
    assert len(bowl.fitted.segments) == len(baseline.segments) - 1

    baseline_curve, threshold_target = _longest_cubic(baseline_fit)
    refined_curve, refined_target = _longest_cubic(bowl)
    assert len(threshold_target) == len(refined_target) == 147

    # Critical comparison 1: the restored pre-4039047 cubic against the
    # threshold-derived contour that it was asked to fit.
    threshold_fit = _error_metrics(threshold_target, baseline_curve)
    assert threshold_fit.maximum_mm <= 0.067
    assert threshold_fit.rms_mm <= 0.034

    # Critical comparison 2: the threshold contour's displacement to the
    # independently sampled source-raster crossing. It is much smaller than
    # the cubic fitting error, disproving local tolerance as the only issue,
    # but is comparable to the old signed centering bias.
    displacement = np.linalg.norm(
        refined_target - threshold_target,
        axis=1,
    )
    displacement_rms = math.sqrt(float(np.mean(displacement**2)))
    assert float(np.max(displacement)) <= 0.012
    assert displacement_rms <= 0.007
    assert displacement_rms <= 0.25 * threshold_fit.rms_mm
    assert float(np.mean(bowl.signed_source_displacements_mm[220:367])) >= 0.005

    # Critical comparison 3: fitting against the recovered source edge halves
    # the systematic inward bias and lowers maximum source-edge error without
    # adding segments or changing the protected span endpoints.
    baseline_source = _error_metrics(refined_target, baseline_curve)
    refined_source = _error_metrics(refined_target, refined_curve)
    assert refined_source.maximum_mm < baseline_source.maximum_mm
    assert abs(refined_source.signed_mean_mm) <= 0.50 * abs(
        baseline_source.signed_mean_mm
    )
    assert abs(refined_source.signed_mean_mm) <= 0.005
    assert refined_source.rms_mm <= 0.037
    assert refined_source.same_side_fraction <= 0.56


def test_actual_coleman_a_e_s_do_not_regress_against_source_edges(
    tmp_path: Path,
) -> None:
    for glyph in ("A", "E", "S"):
        pixels = _coleman_glyph_crop(glyph)
        fits = _fit_raster_outer_contours(
            tmp_path / f"coleman-{glyph}.png",
            pixels,
            pitch_mm=_COLEMAN_PITCH_MM,
        )
        before_errors: list[np.ndarray] = []
        after_errors: list[np.ndarray] = []
        before_segments = 0
        after_segments = 0
        for fit in fits:
            baseline = fitter._fit_contour(
                fit.threshold_raw,
                _options(),
                pixels.shape[1] * _COLEMAN_PITCH_MM,
                pixels.shape[0] * _COLEMAN_PITCH_MM,
                fitter._ComplexityBudget(),
                source_pixel_spacing_mm=(
                    _COLEMAN_PITCH_MM,
                    _COLEMAN_PITCH_MM,
                ),
            )
            before_errors.append(
                _fit_errors_against_points(fit.raw, baseline.segments)
            )
            after_errors.append(
                _fit_errors_against_points(fit.raw, fit.fitted.segments)
            )
            before_segments += len(baseline.segments)
            after_segments += len(fit.fitted.segments)
        before = np.concatenate(before_errors)
        after = np.concatenate(after_errors)
        assert after_segments <= before_segments
        assert float(np.max(after)) <= float(np.max(before)) + 1e-12
        assert math.sqrt(float(np.mean(after**2))) <= math.sqrt(
            float(np.mean(before**2))
        ) + 1e-12


def test_source_edge_refinement_preserves_constraints_and_rejects_ambiguous_profiles(
    tmp_path: Path,
) -> None:
    e_fit = _fit_raster_outer_contours(
        tmp_path / "coleman-E.png",
        _coleman_glyph_crop("E"),
        pitch_mm=_COLEMAN_PITCH_MM,
    )[0]
    assert np.count_nonzero(e_fit.source_edge.protected) >= 600
    assert np.all(
        e_fit.source_edge.signed_displacements_mm[e_fit.source_edge.protected]
        == 0.0
    )

    pixels = np.full((96, 96), 255, dtype=np.uint8)
    cv2.circle(pixels, (48, 48), 20, 0, thickness=-1, lineType=cv2.LINE_AA)
    cv2.circle(pixels, (48, 48), 18, 255, thickness=-1, lineType=cv2.LINE_AA)
    path = tmp_path / "ambiguous-thin-annulus.png"
    assert cv2.imwrite(str(path), pixels)
    source = fitter.prepare_raster_vectorization_source(
        read_raster_asset_payload(path)
    )
    angles = np.linspace(0.0, 2.0 * math.pi, 257, endpoint=False)
    radius_mm = 19.0 * _COLEMAN_PITCH_MM
    contour = np.column_stack(
        (radius_mm * np.cos(angles), radius_mm * np.sin(angles))
    )
    refinement = fitter._refine_contour_source_edges(
        contour,
        source,
        _options(),
        122,
        96 * _COLEMAN_PITCH_MM,
        96 * _COLEMAN_PITCH_MM,
    )
    assert not np.any(refinement.eligible)
    np.testing.assert_array_equal(refinement.points, contour)


def test_source_edge_profile_chunking_is_geometry_neutral(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pixels = _coleman_p_crop()
    normal = _fit_raster_outer_contours(
        tmp_path / "coleman-p-normal-chunks.png",
        pixels,
        pitch_mm=_COLEMAN_PITCH_MM,
    )
    monkeypatch.setattr(fitter, "_SOURCE_EDGE_PROFILE_CHUNK_SIZE", 17)
    chunked = _fit_raster_outer_contours(
        tmp_path / "coleman-p-small-chunks.png",
        pixels,
        pitch_mm=_COLEMAN_PITCH_MM,
    )

    assert len(chunked) == len(normal)
    for normal_fit, chunked_fit in zip(normal, chunked, strict=True):
        np.testing.assert_array_equal(chunked_fit.raw, normal_fit.raw)
        assert len(chunked_fit.fitted.segments) == len(normal_fit.fitted.segments)
        for normal_segment, chunked_segment in zip(
            normal_fit.fitted.segments,
            chunked_fit.fitted.segments,
            strict=True,
        ):
            np.testing.assert_array_equal(
                _segment_controls(chunked_segment),
                _segment_controls(normal_segment),
            )


def test_analytic_d_bowl_tracks_threshold_and_known_geometry(tmp_path: Path) -> None:
    pitch_mm = 0.08
    center = (70.25, 90.5)
    pixels = _d_bowl(center=center)
    fit = _fit_raster_outer_contours(
        tmp_path / "analytic-d-bowl.png",
        pixels,
        pitch_mm=pitch_mm,
    )[0]
    baseline = fitter._fit_contour(
        fit.threshold_raw,
        _options(),
        180 * pitch_mm,
        180 * pitch_mm,
        fitter._ComplexityBudget(),
        source_pixel_spacing_mm=(pitch_mm, pitch_mm),
    )

    assert any(isinstance(segment, fitter._CubicSegment) for segment in fit.fitted.segments)
    assert fit.fitted.max_fitting_error_mm <= 0.08
    for segment in fit.fitted.segments:
        target, _indices = _segment_target(fit.raw, segment)
        metrics = _error_metrics(target, segment)
        assert metrics.maximum_mm <= 0.08
        if len(target) >= 20:
            assert metrics.rms_mm <= 0.04
            assert abs(metrics.signed_mean_mm) <= 0.016
    ideal_errors = _ideal_d_distance_mm(
        _sample_segments(fit.fitted.segments),
        image_size=180,
        pitch_mm=pitch_mm,
        center=center,
        angle_radians=0.0,
    )
    baseline_ideal_errors = _ideal_d_distance_mm(
        _sample_segments(baseline.segments),
        image_size=180,
        pitch_mm=pitch_mm,
        center=center,
        angle_radians=0.0,
    )
    assert len(fit.fitted.segments) <= len(baseline.segments)
    assert float(np.max(ideal_errors)) < float(np.max(baseline_ideal_errors))
    assert math.sqrt(float(np.mean(ideal_errors**2))) < math.sqrt(
        float(np.mean(baseline_ideal_errors**2))
    )
    assert float(np.max(ideal_errors)) <= 0.062
    assert math.sqrt(float(np.mean(ideal_errors**2))) <= 0.019


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


def test_rotated_analytic_d_bowl_converges_across_source_scale(
    tmp_path: Path,
) -> None:
    angle = 0.47
    low_pitch = 0.08
    scale = 1.5
    low = _fit_raster_outer_contours(
        tmp_path / "d-bowl-scaled-low.png",
        _d_bowl(
            center=(90.25, 90.5),
            angle_radians=angle,
        ),
        pitch_mm=low_pitch,
    )[0]
    high = _fit_raster_outer_contours(
        tmp_path / "d-bowl-scaled-high.png",
        _d_bowl(
            center=(135.375, 135.75),
            angle_radians=angle,
            size=270,
            outer_radius_px=72.0,
            inner_radius_px=45.0,
        ),
        pitch_mm=low_pitch / scale,
    )[0]

    baselines: list[fitter._FittedContour] = []
    for fit, pitch in ((low, low_pitch), (high, low_pitch / scale)):
        baseline = fitter._fit_contour(
            fit.threshold_raw,
            _options(),
            180 * low_pitch,
            180 * low_pitch,
            fitter._ComplexityBudget(),
            source_pixel_spacing_mm=(pitch, pitch),
        )
        baselines.append(baseline)
        assert len(fit.fitted.segments) == len(baseline.segments)
    assert abs(len(high.fitted.segments) - len(low.fitted.segments)) <= 3
    refined_distance = _symmetric_maximum_distance(
        _centered_segment_samples(low),
        _centered_segment_samples(high),
    )
    baseline_samples = []
    for baseline in baselines:
        samples = _sample_segments(baseline.segments)
        baseline_samples.append(
            samples - (np.min(samples, axis=0) + np.max(samples, axis=0)) / 2.0
        )
    baseline_distance = _symmetric_maximum_distance(*baseline_samples)
    assert refined_distance <= baseline_distance
    assert refined_distance <= 0.10


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
