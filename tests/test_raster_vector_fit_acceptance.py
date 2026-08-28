from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest

import laser_aligner.project.raster_vectorize as raster_vectorize
from laser_aligner.project import (
    PathCubicSegment,
    PathLineSegment,
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationOptions,
    prepare_raster_vectorization_source,
    read_raster_asset_payload,
    vectorize_raster_payload,
)

_DISPLAY_MM_PER_PIXEL = 0.10
_FIT_TOLERANCE_MM = 0.10


@dataclass(frozen=True, slots=True)
class _FitDiagnostics:
    raw_points: np.ndarray
    corner_indices: tuple[int, ...]
    segments: tuple[object, ...]


def _manual_options(*, threshold: int = 127) -> RasterVectorizationOptions:
    return RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=threshold,
        minimum_feature_area_mm2=0.0,
        smoothing_mm=0.0,
        simplification_tolerance_mm=_FIT_TOLERANCE_MM,
        contour_output=RasterContourOutput.ALL_CONTOURS,
    )


def _write_payload(path: Path, pixels: np.ndarray):
    assert cv2.imwrite(str(path), pixels)
    return read_raster_asset_payload(path)


def _display_size(pixels: np.ndarray) -> tuple[float, float]:
    height_px, width_px = pixels.shape[:2]
    return width_px * _DISPLAY_MM_PER_PIXEL, height_px * _DISPLAY_MM_PER_PIXEL


def _draw_capsule(
    canvas: np.ndarray,
    *,
    center: tuple[float, float],
    straight_length_px: float,
    diameter_px: float,
    horizontal: bool = False,
    supersample: int = 8,
) -> None:
    """Rasterize one capsule with controllable subpixel phase."""

    height_px, width_px = canvas.shape[:2]
    high = np.full(
        (height_px * supersample, width_px * supersample),
        255,
        dtype=np.uint8,
    )
    cx = int(round(center[0] * supersample))
    cy = int(round(center[1] * supersample))
    radius = int(round(diameter_px * supersample / 2.0))
    half_straight = int(round(straight_length_px * supersample / 2.0))
    if horizontal:
        first = (cx - half_straight, cy)
        second = (cx + half_straight, cy)
        cv2.rectangle(
            high,
            (first[0], cy - radius),
            (second[0], cy + radius),
            0,
            thickness=-1,
        )
    else:
        first = (cx, cy - half_straight)
        second = (cx, cy + half_straight)
        cv2.rectangle(
            high,
            (cx - radius, first[1]),
            (cx + radius, second[1]),
            0,
            thickness=-1,
        )
    cv2.circle(high, first, radius, 0, thickness=-1)
    cv2.circle(high, second, radius, 0, thickness=-1)
    reduced = cv2.resize(
        high,
        (width_px, height_px),
        interpolation=cv2.INTER_AREA,
    )
    np.minimum(canvas, reduced, out=canvas)


def _capsule_pair(
    *,
    fractional: bool = False,
    perturb_bottom_cap: bool = False,
) -> np.ndarray:
    pixels = np.full((180, 240), 255, dtype=np.uint8)
    _draw_capsule(
        pixels,
        center=(55.0, 88.0),
        straight_length_px=72.0,
        diameter_px=24.0,
    )
    second_center = (171.375, 89.625) if fractional else (171.0, 89.0)
    _draw_capsule(
        pixels,
        center=second_center,
        straight_length_px=72.0,
        diameter_px=24.0,
    )
    if perturb_bottom_cap:
        bottom = int(round(second_center[1] + 72.0 / 2.0 + 24.0 / 2.0))
        center_x = int(round(second_center[0]))
        # The antialiased cap reaches the preceding row at this threshold, so this
        # is one attached boundary pixel rather than a third isolated component.
        pixels[bottom, center_x] = 0
    return pixels


def _coleman_stencil() -> np.ndarray:
    pixels = np.full((444, 1170), 255, dtype=np.uint8)
    cv2.putText(
        pixels,
        "COLEMAN",
        (45, 235),
        cv2.FONT_HERSHEY_SIMPLEX,
        4.4,
        0,
        18,
        cv2.LINE_AA,
    )
    for start in (250, 470, 690, 910):
        cv2.rectangle(pixels, (start, 275), (start + 175, 292), 0, -1)
    cv2.rectangle(pixels, (150, 345), (1020, 375), 0, -1)
    for center_x in range(210, 1010, 160):
        cv2.rectangle(
            pixels,
            (center_x - 5, 335),
            (center_x + 5, 385),
            255,
            -1,
        )
    # The bottom-row letters exercise the mixed-scale failure from the Coleman
    # artwork at its real 80 mm display width.  Place glyphs independently so
    # each outer contour remains measurable without font-spacing overlap.
    for x_position, glyph in zip((700, 750, 800, 850), "APSE", strict=True):
        cv2.putText(
            pixels,
            glyph,
            (x_position, 432),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            0,
            2,
            cv2.LINE_AA,
        )
    return pixels


def _corner_indices(points: np.ndarray, tolerance_mm: float) -> tuple[int, ...]:
    parameters = inspect.signature(raster_vectorize._corner_indices).parameters
    if len(parameters) == 1:
        return tuple(raster_vectorize._corner_indices(points))
    return tuple(raster_vectorize._corner_indices(points, tolerance_mm))


def _raw_outer_contours(
    payload,
    options: RasterVectorizationOptions,
    *,
    width_mm: float,
    height_mm: float,
) -> list[np.ndarray]:
    """Follow the production mask path and expose only oriented outer contours."""

    source = prepare_raster_vectorization_source(payload)
    grayscale = raster_vectorize._composited_grayscale(source)
    threshold = raster_vectorize._threshold_value(source, options, grayscale)
    source_mask = raster_vectorize._mask_at_resolution(
        source,
        options,
        grayscale,
        threshold,
    )
    cleaned_mask, _component_count = raster_vectorize._clean_components(
        source_mask,
        options.minimum_feature_area_mm2,
        width_mm,
        height_mm,
    )
    working_mask = raster_vectorize._oversampled_mask(
        source,
        options,
        grayscale,
        threshold,
        source_mask,
        cleaned_mask,
    )
    raw_contours, hierarchy = cv2.findContours(
        working_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_NONE,
    )
    assert hierarchy is not None
    parents = hierarchy[0, :, 3]
    contours: list[np.ndarray] = []
    for index, contour in enumerate(raw_contours):
        if int(parents[index]) >= 0:
            continue
        physical = raster_vectorize._physical_contour(
            contour,
            source.width_px,
            source.height_px,
            width_mm,
            height_mm,
        )
        if raster_vectorize._signed_area(physical) < 0.0:
            physical = physical[::-1].copy()
        contours.append(physical)
    return sorted(contours, key=lambda points: float(np.mean(points[:, 0])))


def _fit_raw_contour(
    points: np.ndarray,
    options: RasterVectorizationOptions,
    *,
    width_mm: float,
    height_mm: float,
) -> _FitDiagnostics:
    """Small adapter around the private fit seam used for acceptance diagnostics."""

    canonicalize = getattr(
        raster_vectorize,
        "_canonicalize_closed_contour",
        lambda values: np.asarray(values, dtype=np.float64).copy(),
    )
    canonical = canonicalize(points)
    fitting_tolerance = options.simplification_tolerance_mm * 0.65
    corners = _corner_indices(canonical, fitting_tolerance)

    fitted = raster_vectorize._fit_contour(
        canonical,
        options,
        width_mm,
        height_mm,
        raster_vectorize._ComplexityBudget(),
    )
    assert fitted.segments
    return _FitDiagnostics(canonical, corners, tuple(fitted.segments))


def _fit_payload_contours(
    path: Path,
    pixels: np.ndarray,
    options: RasterVectorizationOptions,
    *,
    width_mm: float | None = None,
    height_mm: float | None = None,
) -> tuple[object, list[_FitDiagnostics]]:
    payload = _write_payload(path, pixels)
    default_width, default_height = _display_size(pixels)
    width = default_width if width_mm is None else width_mm
    height = default_height if height_mm is None else height_mm
    result = vectorize_raster_payload(
        payload,
        options,
        displayed_width_mm=width,
        displayed_height_mm=height,
    )
    diagnostics = [
        _fit_raw_contour(
            points,
            options,
            width_mm=width,
            height_mm=height,
        )
        for points in _raw_outer_contours(
            payload,
            options,
            width_mm=width,
            height_mm=height,
        )
    ]
    return result, diagnostics


def _segment_code(segment: object) -> str:
    if isinstance(segment, raster_vectorize._LineSegment):
        return "L"
    assert isinstance(segment, raster_vectorize._CubicSegment)
    return "C"


def _canonical_segment_sequence(segments: tuple[object, ...]) -> tuple[str, ...]:
    sequence = tuple(_segment_code(segment) for segment in segments)
    assert sequence
    return min(sequence[offset:] + sequence[:offset] for offset in range(len(sequence)))


def _segment_start_tangent(segment: object) -> np.ndarray:
    if isinstance(segment, raster_vectorize._LineSegment):
        return segment.end - segment.start
    assert isinstance(segment, raster_vectorize._CubicSegment)
    return segment.control_1 - segment.start


def _segment_end_tangent(segment: object) -> np.ndarray:
    if isinstance(segment, raster_vectorize._LineSegment):
        return segment.end - segment.start
    assert isinstance(segment, raster_vectorize._CubicSegment)
    return segment.end - segment.control_2


def _assert_g1_closed(segments: tuple[object, ...]) -> None:
    for first, second in zip(segments, (*segments[1:], segments[0]), strict=True):
        incoming = _segment_end_tangent(first)
        outgoing = _segment_start_tangent(second)
        incoming_norm = float(np.linalg.norm(incoming))
        outgoing_norm = float(np.linalg.norm(outgoing))
        assert incoming_norm > 1e-9
        assert outgoing_norm > 1e-9
        incoming /= incoming_norm
        outgoing /= outgoing_norm
        cross = abs(float(incoming[0] * outgoing[1] - incoming[1] * outgoing[0]))
        assert cross <= 0.05
        assert float(np.dot(incoming, outgoing)) > 0.0


def _sample_segments(segments: tuple[object, ...]) -> np.ndarray:
    sampled: list[np.ndarray] = []
    parameters = np.linspace(0.0, 1.0, 33, endpoint=False)
    for segment in segments:
        if isinstance(segment, raster_vectorize._LineSegment):
            values = (
                segment.start
                + parameters[:, None] * (segment.end - segment.start)
            )
        else:
            assert isinstance(segment, raster_vectorize._CubicSegment)
            values = raster_vectorize._cubic_values(
                segment.start,
                segment.control_1,
                segment.control_2,
                segment.end,
                parameters,
            )
        sampled.append(np.asarray(values, dtype=np.float64))
    return np.vstack(sampled)


def _centered_samples(segments: tuple[object, ...]) -> np.ndarray:
    points = _sample_segments(segments)
    center = (np.min(points, axis=0) + np.max(points, axis=0)) / 2.0
    return points - center


def _symmetric_max_nearest(first: np.ndarray, second: np.ndarray) -> float:
    distances = np.linalg.norm(first[:, None, :] - second[None, :, :], axis=2)
    return max(
        float(np.max(np.min(distances, axis=1))),
        float(np.max(np.min(distances, axis=0))),
    )


def _assert_equivalent_capsules(
    first: _FitDiagnostics,
    second: _FitDiagnostics,
    *,
    geometry_tolerance_mm: float,
) -> None:
    assert first.corner_indices == ()
    assert second.corner_indices == ()
    assert _canonical_segment_sequence(first.segments) == (
        _canonical_segment_sequence(second.segments)
    )
    _assert_g1_closed(first.segments)
    _assert_g1_closed(second.segments)
    assert _symmetric_max_nearest(
        _centered_samples(first.segments),
        _centered_samples(second.segments),
    ) <= geometry_tolerance_mm


def _assert_result_in_frame(result: object) -> None:
    for contour in result.contours:
        points = np.asarray(contour.points, dtype=np.float64)
        assert np.all(np.abs(points) <= 0.500000001)


def _native_physical_segments(
    contour: object,
    width_mm: float,
    height_mm: float,
) -> list[tuple[object, np.ndarray, np.ndarray]]:
    scale = np.asarray((width_mm, height_mm), dtype=np.float64)
    start = np.asarray(contour.native_subpath.start, dtype=np.float64) * scale
    output: list[tuple[object, np.ndarray, np.ndarray]] = []
    for segment in contour.native_subpath.segments:
        end = np.asarray(segment.to, dtype=np.float64) * scale
        output.append((segment, start, end))
        start = end
    return output


def _assert_native_line_tracks_raw_samples(
    contour: object,
    raw_points: np.ndarray,
    width_mm: float,
    height_mm: float,
    predicate: Callable[[np.ndarray, np.ndarray], bool],
) -> None:
    for native_segment, start, end in _native_physical_segments(
        contour,
        width_mm,
        height_mm,
    ):
        if not isinstance(native_segment, PathLineSegment) or not predicate(start, end):
            continue
        start_index = int(np.argmin(np.linalg.norm(raw_points - start, axis=1)))
        end_index = int(np.argmin(np.linalg.norm(raw_points - end, axis=1)))
        assert float(np.linalg.norm(raw_points[start_index] - start)) < 1e-8
        assert float(np.linalg.norm(raw_points[end_index] - end)) < 1e-8
        target = raw_points[
            raster_vectorize._circular_indices(
                start_index,
                end_index,
                len(raw_points),
            )
        ]
        assert float(
            np.max(raster_vectorize._distance_to_segment(target, start, end))
        ) <= _FIT_TOLERANCE_MM * 0.80 + 1e-12
        return
    pytest.fail("The expected Coleman source edge was not a native line")


def test_coleman_e_outer_edges_are_native_lines_and_glyph_curves_remain(
    tmp_path: Path,
) -> None:
    pixels = _coleman_stencil()
    width_mm = 80.0
    height_mm = 30.358974
    result, fits = _fit_payload_contours(
        tmp_path / "coleman-stencil.png",
        pixels,
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
            threshold=122,
            minimum_feature_area_mm2=0.05,
            smoothing_mm=0.0,
            simplification_tolerance_mm=0.10,
            contour_output=RasterContourOutput.ALL_CONTOURS,
        ),
        width_mm=width_mm,
        height_mm=height_mm,
    )
    e_fit = next(
        fitted
        for fitted in fits
        if -19.0 < float(np.min(fitted.raw_points[:, 0])) < -17.0
    )
    e_contour = next(
        contour
        for contour in result.contours
        if -19.0
        < float(np.min(np.asarray(contour.points)[:, 0] * width_mm))
        < -17.0
    )
    lower = np.min(e_fit.raw_points, axis=0)
    upper = np.max(e_fit.raw_points, axis=0)

    _assert_native_line_tracks_raw_samples(
        e_contour,
        e_fit.raw_points,
        width_mm,
        height_mm,
        lambda start, end: (
            abs(float(end[1] - start[1])) < 0.05
            and float(np.linalg.norm(end - start)) > 3.0
            and float((start[1] + end[1]) / 2.0) < lower[1] + 0.10
        ),
    )
    _assert_native_line_tracks_raw_samples(
        e_contour,
        e_fit.raw_points,
        width_mm,
        height_mm,
        lambda start, end: (
            abs(float(end[1] - start[1])) < 0.05
            and float(np.linalg.norm(end - start)) > 3.0
            and float((start[1] + end[1]) / 2.0) > upper[1] - 0.10
        ),
    )
    _assert_native_line_tracks_raw_samples(
        e_contour,
        e_fit.raw_points,
        width_mm,
        height_mm,
        lambda start, end: (
            abs(float(end[0] - start[0])) < 0.05
            and float(np.linalg.norm(end - start)) > 5.0
            and float((start[0] + end[0]) / 2.0) < lower[0] + 0.10
        ),
    )
    assert any(
        isinstance(segment, PathCubicSegment)
        for segment in e_contour.native_subpath.segments
    )
    assert "".join(
        "L" if isinstance(segment, PathLineSegment) else "C"
        for segment in e_contour.native_subpath.segments
    ) == "LCLLCLCCCCCCLCLCLC"
    rounded_glyphs = [
        contour
        for contour in result.contours
        if float(np.max(np.asarray(contour.points)[:, 1] * height_mm)) > 0.0
        and (
            (
                -37.0
                < float(np.min(np.asarray(contour.points)[:, 0] * width_mm))
                < -30.5
            )
            or (
                -30.5
                < float(np.min(np.asarray(contour.points)[:, 0] * width_mm))
                < -24.0
            )
        )
    ]
    assert len(rounded_glyphs) >= 2
    assert all(
        contour.native_subpath.segments
        and any(
            isinstance(segment, PathCubicSegment)
            for segment in contour.native_subpath.segments
        )
        for contour in rounded_glyphs
    )
    assert e_contour.max_fitting_error_mm <= _FIT_TOLERANCE_MM * 0.80 + 1e-12


def _coleman_small_glyphs(result: object) -> dict[str, object]:
    width_mm = 80.0
    height_mm = 30.358974
    expected_centers = {"A": 8.45, "P": 11.99, "S": 15.35, "E": 18.78}
    candidates: list[tuple[float, object]] = []
    for contour in result.contours:
        if contour.parent_index is not None:
            continue
        points = np.asarray(contour.points, dtype=np.float64) * np.asarray(
            (width_mm, height_mm)
        )
        lower = np.min(points, axis=0)
        upper = np.max(points, axis=0)
        if upper[1] < -12.0 and upper[1] - lower[1] > 1.0:
            candidates.append((float((lower[0] + upper[0]) / 2.0), contour))
    assert len(candidates) == 4
    return {
        glyph: min(candidates, key=lambda value: abs(value[0] - center))[1]
        for glyph, center in expected_centers.items()
    }


def test_coleman_small_lettering_uses_tighter_local_fit_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pixels = _coleman_stencil()
    payload = _write_payload(tmp_path / "coleman-mixed-scale-stencil.png", pixels)
    options = RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=122,
        minimum_feature_area_mm2=0.05,
        smoothing_mm=0.0,
        simplification_tolerance_mm=0.10,
        contour_output=RasterContourOutput.ALL_CONTOURS,
    )
    local_scale = raster_vectorize._local_fit_scale

    def ceiling_only(
        points: np.ndarray,
        maximum_tolerance_mm: float,
        source_pixel_spacing_mm: tuple[float, float] | None,
    ) -> object:
        diagnostics = local_scale(
            points,
            maximum_tolerance_mm,
            source_pixel_spacing_mm,
        )
        return raster_vectorize._LocalFitScale(
            effective_tolerance_mm=maximum_tolerance_mm,
            resolution_floor_mm=diagnostics.resolution_floor_mm,
            span_scale_mm=diagnostics.span_scale_mm,
            arc_length_mm=diagnostics.arc_length_mm,
            chord_length_mm=diagnostics.chord_length_mm,
            chord_sagitta_mm=diagnostics.chord_sagitta_mm,
        )

    monkeypatch.setattr(raster_vectorize, "_local_fit_scale", ceiling_only)
    old_result = vectorize_raster_payload(
        payload,
        options,
        displayed_width_mm=80.0,
        displayed_height_mm=30.358974,
    )
    monkeypatch.setattr(raster_vectorize, "_local_fit_scale", local_scale)
    new_result = vectorize_raster_payload(
        payload,
        options,
        displayed_width_mm=80.0,
        displayed_height_mm=30.358974,
    )
    old_glyphs = _coleman_small_glyphs(old_result)
    new_glyphs = _coleman_small_glyphs(new_result)

    physical_heights_mm = {"A": 1.44, "P": 1.49, "S": 1.52, "E": 1.50}
    assert sum(
        contour.rms_fitting_error_mm for contour in new_glyphs.values()
    ) <= 0.75 * sum(
        contour.rms_fitting_error_mm for contour in old_glyphs.values()
    )
    assert new_glyphs["A"].max_fitting_error_mm <= 0.021
    assert new_glyphs["E"].max_fitting_error_mm <= 0.043
    for glyph, contour in new_glyphs.items():
        assert (
            contour.max_fitting_error_mm / physical_heights_mm[glyph]
            <= 0.04
        )
        assert any(
            isinstance(segment, PathCubicSegment)
            for segment in contour.native_subpath.segments
        )
    old_segment_count = sum(
        len(contour.native_subpath.segments) for contour in old_glyphs.values()
    )
    new_segment_count = sum(
        len(contour.native_subpath.segments) for contour in new_glyphs.values()
    )
    assert old_segment_count == 63
    assert new_segment_count == 71


def test_equal_vertical_capsules_at_integer_pixel_phases_are_equivalent(
    tmp_path: Path,
) -> None:
    pixels = _capsule_pair()
    result, fits = _fit_payload_contours(
        tmp_path / "integer-phase-capsules.png",
        pixels,
        _manual_options(),
    )

    assert result.connected_component_count == 2
    assert len(result.contours) == 2
    assert len(fits) == 2
    _assert_equivalent_capsules(fits[0], fits[1], geometry_tolerance_mm=0.03)
    _assert_result_in_frame(result)


@pytest.mark.parametrize("threshold", [104, 127, 150])
def test_subpixel_capsules_remain_equivalent_across_threshold_phases(
    tmp_path: Path,
    threshold: int,
) -> None:
    pixels = _capsule_pair(fractional=True)
    result, fits = _fit_payload_contours(
        tmp_path / f"subpixel-capsules-{threshold}.png",
        pixels,
        _manual_options(threshold=threshold),
    )

    assert result.connected_component_count == 2
    assert len(result.contours) == 2
    _assert_equivalent_capsules(fits[0], fits[1], geometry_tolerance_mm=0.14)
    _assert_result_in_frame(result)


def test_one_pixel_rounded_cap_perturbation_does_not_create_a_corner(
    tmp_path: Path,
) -> None:
    pixels = _capsule_pair(perturb_bottom_cap=True)
    result, fits = _fit_payload_contours(
        tmp_path / "one-pixel-cap-perturbation.png",
        pixels,
        _manual_options(),
    )

    assert result.connected_component_count == 2
    assert len(result.contours) == 2
    _assert_equivalent_capsules(fits[0], fits[1], geometry_tolerance_mm=0.14)
    _assert_result_in_frame(result)


@pytest.mark.parametrize("horizontal", [False, True], ids=["vertical", "horizontal"])
def test_horizontal_and_vertical_capsules_keep_smooth_cap_joins(
    tmp_path: Path,
    horizontal: bool,
) -> None:
    pixels = np.full((180, 220), 255, dtype=np.uint8)
    _draw_capsule(
        pixels,
        center=(110.25, 90.375),
        straight_length_px=88.0,
        diameter_px=28.0,
        horizontal=horizontal,
    )
    result, fits = _fit_payload_contours(
        tmp_path / f"{'horizontal' if horizontal else 'vertical'}-capsule.png",
        pixels,
        _manual_options(),
    )

    assert result.connected_component_count == 1
    assert len(fits) == 1
    fit = fits[0]
    assert fit.corner_indices == ()
    assert "L" in {_segment_code(segment) for segment in fit.segments}
    assert "C" in {_segment_code(segment) for segment in fit.segments}
    _assert_g1_closed(fit.segments)
    _assert_result_in_frame(result)


def test_anisotropic_display_keeps_capsule_smooth_and_in_frame(
    tmp_path: Path,
) -> None:
    pixels = np.full((180, 180), 255, dtype=np.uint8)
    _draw_capsule(
        pixels,
        center=(90.375, 90.625),
        straight_length_px=84.0,
        diameter_px=30.0,
    )
    result, fits = _fit_payload_contours(
        tmp_path / "anisotropic-capsule.png",
        pixels,
        _manual_options(),
        width_mm=36.0,
        height_mm=12.0,
    )

    assert result.connected_component_count == 1
    assert len(fits) == 1
    assert fits[0].corner_indices == ()
    _assert_g1_closed(fits[0].segments)
    _assert_result_in_frame(result)


def test_real_raster_rectangle_corners_remain_locked(tmp_path: Path) -> None:
    pixels = np.full((160, 200), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (45, 35), (154, 124), 0, thickness=-1)
    width_mm, height_mm = _display_size(pixels)
    result, fits = _fit_payload_contours(
        tmp_path / "true-rectangle.png",
        pixels,
        _manual_options(),
    )

    assert result.connected_component_count == 1
    assert len(fits) == 1
    fit = fits[0]
    assert len(fit.corner_indices) == 4
    corner_points = fit.raw_points[np.asarray(fit.corner_indices, dtype=np.int64)]
    expected = np.asarray(
        [
            ((45.0 / 200.0 - 0.5) * width_mm, (0.5 - 35.0 / 160.0) * height_mm),
            ((154.0 / 200.0 - 0.5) * width_mm, (0.5 - 35.0 / 160.0) * height_mm),
            ((154.0 / 200.0 - 0.5) * width_mm, (0.5 - 124.0 / 160.0) * height_mm),
            ((45.0 / 200.0 - 0.5) * width_mm, (0.5 - 124.0 / 160.0) * height_mm),
        ],
        dtype=np.float64,
    )
    for point in expected:
        assert float(np.min(np.linalg.norm(corner_points - point, axis=1))) <= 0.15
    assert all(
        isinstance(segment, raster_vectorize._LineSegment)
        for segment in fit.segments
    )
    _assert_result_in_frame(result)


def test_stencil_notch_and_narrow_gap_preserve_topology(tmp_path: Path) -> None:
    pixels = np.full((180, 220), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (35, 35), (95, 145), 0, thickness=-1)
    cv2.fillPoly(
        pixels,
        [np.asarray(((95, 74), (81, 90), (95, 106)), dtype=np.int32)],
        255,
    )
    cv2.rectangle(pixels, (100, 35), (160, 145), 0, thickness=-1)
    width_mm, height_mm = _display_size(pixels)
    result, fits = _fit_payload_contours(
        tmp_path / "stencil-notch-gap.png",
        pixels,
        _manual_options(),
    )

    assert result.connected_component_count == 2
    assert len(result.contours) == 2
    assert len(fits) == 2
    paths = sorted(
        (
            np.asarray(contour.points, dtype=np.float64)
            * np.asarray((width_mm, height_mm))
            for contour in result.contours
        ),
        key=lambda points: float(np.mean(points[:, 0])),
    )
    assert float(np.min(paths[1][:, 0]) - np.max(paths[0][:, 0])) >= 0.15
    notch_tip = np.asarray(
        (
            (81.0 / 220.0 - 0.5) * width_mm,
            (0.5 - 90.0 / 180.0) * height_mm,
        )
    )
    left_corner_points = fits[0].raw_points[
        np.asarray(fits[0].corner_indices, dtype=np.int64)
    ]
    assert float(np.min(np.linalg.norm(left_corner_points - notch_tip, axis=1))) <= 0.20
    _assert_result_in_frame(result)


def test_closed_capsule_fit_is_invariant_to_cyclic_start_index(
    tmp_path: Path,
) -> None:
    pixels = np.full((180, 180), 255, dtype=np.uint8)
    _draw_capsule(
        pixels,
        center=(90.375, 90.625),
        straight_length_px=84.0,
        diameter_px=30.0,
    )
    payload = _write_payload(tmp_path / "cyclic-capsule.png", pixels)
    options = _manual_options()
    width_mm, height_mm = _display_size(pixels)
    raw = _raw_outer_contours(
        payload,
        options,
        width_mm=width_mm,
        height_mm=height_mm,
    )[0]
    offsets = sorted({0, 1, len(raw) // 17, len(raw) // 4, len(raw) // 2})
    fits = [
        _fit_raw_contour(
            np.roll(raw, -offset, axis=0),
            options,
            width_mm=width_mm,
            height_mm=height_mm,
        )
        for offset in offsets
    ]
    baseline = fits[0]
    baseline_sequence = _canonical_segment_sequence(baseline.segments)
    baseline_points = _centered_samples(baseline.segments)
    for fitted in fits:
        assert fitted.corner_indices == ()
        assert _canonical_segment_sequence(fitted.segments) == baseline_sequence
        assert len(fitted.segments) == len(baseline.segments)
        _assert_g1_closed(fitted.segments)
        assert _symmetric_max_nearest(
            baseline_points,
            _centered_samples(fitted.segments),
        ) <= 0.03
