from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np
import pytest

import laser_aligner.vision.object_trace as trace
from laser_aligner.config import WorkArea
from laser_aligner.project.raster_vectorize import (
    PixelVectorizationForestResult,
    PixelVectorizationRootFailure,
    PixelVectorizationRootReviewFilter,
    RasterVectorizationCancelledError,
    RasterVectorizationComplexityError,
    RasterVectorizationError,
    RasterVectorizationOptions,
    RasterVectorizationTiming,
    RasterVectorizationTopologyError,
    prepare_pixel_vectorization_source,
    vectorize_pixel_source_forest,
)


def _options(tolerance=0.1):
    return RasterVectorizationOptions(
        detection_mode="manual_threshold", threshold=127,
        minimum_feature_area_mm2=0.0, smoothing_mm=0.0,
        simplification_tolerance_mm=tolerance, contour_output="outer_only",
    )


def _source():
    image = np.full((100, 160, 3), 230, dtype=np.uint8)
    cv2.circle(image, (45, 50), 25, (25, 25, 25), -1)
    cv2.circle(image, (115, 50), 25, (25, 25, 25), -1)
    return prepare_pixel_vectorization_source(cv2.cvtColor(image, cv2.COLOR_BGR2RGBA))


def _fit(source, *, tolerance=0.1, previews=None):
    return trace._vectorize_camera_forest(
        source, _options(tolerance), displayed_width_mm=36,
        displayed_height_mm=24, timing=RasterVectorizationTiming(),
        mask_ready=(previews.append if previews is not None else lambda _mask: None),
        root_review_filter=PixelVectorizationRootReviewFilter(
            maximum_area_mm2=1000, minimum_width_mm=0, minimum_height_mm=0,
        ),
    )


def test_real_raster_hierarchy_failure_is_not_retried():
    # A raster hierarchy failure is distinct from native ambiguity. Even though
    # a tighter fit could change this result, that is outside this retry policy.
    pixels = np.full((120, 180), 255, dtype=np.uint8)
    ys = np.arange(15, 106, 5)
    xs = 90 + np.random.default_rng(0).integers(-6, 7, len(ys))
    left = np.array([(15, 15), *zip(xs, ys, strict=True), (15, 105)], dtype=np.int32)
    right = np.array([(165, 15), *zip(xs + 2, ys, strict=True), (165, 105)], dtype=np.int32)
    cv2.fillPoly(pixels, [left, right], 0)
    source = prepare_pixel_vectorization_source(cv2.cvtColor(pixels, cv2.COLOR_GRAY2RGBA))
    original = vectorize_pixel_source_forest(
        source, _options(), displayed_width_mm=36, displayed_height_mm=24,
    )
    assert original.result is None
    assert {failure.stage for failure in original.failures} == {"raster_hierarchy"}

    previews = []
    result, attempts, reason = _fit(source, previews=previews)
    assert result.result is None
    assert result.failures == original.failures
    assert attempts == (0.1,) and reason is None
    assert len(previews) == 1


@pytest.mark.parametrize("detail", ["full", "outer_silhouette"])
@pytest.mark.parametrize("threshold_mode", ["manual", "auto"])
def test_camera_retry_keeps_source_threshold_and_reports_effective_fit(
    monkeypatch, detail, threshold_mode,
):
    calls = []
    native = trace.vectorize_pixel_source_forest

    def fail_first(source, options, **kwargs):
        calls.append((source, options))
        result = native(source, options, **kwargs)
        if len(calls) == 1:
            raise RasterVectorizationTopologyError("Injected cross-root ambiguity")
        return result

    monkeypatch.setattr(trace, "vectorize_pixel_source_forest", fail_first)
    previews = []
    options = trace.TraceOptions(
        detection_mode="contrast", regular_grid=False, trace_detail=detail,
        contrast_threshold_mode=threshold_mode, contrast_threshold=128,
        min_area_mm2=1, min_width_mm=1, min_height_mm=1,
        confidence_threshold=0, native_fitting_tolerance_mm=0.1,
    )
    result = trace.detect_objects(
        cv2.cvtColor(_source().source_rgba, cv2.COLOR_RGBA2BGR), options,
        WorkArea(x_min=0, x_max=32, y_min=0, y_max=20), 5,
        raster_preview_callback=previews.append,
    )
    assert result.detected and result.direct_count == 2
    assert len(calls) == 2
    assert calls[0][0] is calls[1][0]
    assert replace(calls[0][1], simplification_tolerance_mm=0.025) == calls[1][1]
    assert options.native_fitting_tolerance_mm == 0.1
    assert result.diagnostics["native_fit"] == {
        "requested_tolerance_mm": 0.1, "effective_tolerance_mm": 0.025,
        "attempt_tolerances_mm": [0.1, 0.025],
        "topology_retry_reason": "Injected cross-root ambiguity",
    }
    assert "recovered with tighter native fit" in result.message
    assert all(item.native_verified for item in result.detections)
    assert all(item.diagnostics["native_fitting_tolerance_mm"] == 0.025 for item in result.detections)
    assert all(item.diagnostics["requested_fitting_tolerance_mm"] == 0.1 for item in result.detections)
    assert np.array_equal(previews[0].contour_mask, previews[1].contour_mask)


@pytest.mark.parametrize("error", [
    RasterVectorizationComplexityError("bounded work exceeded"),
    RasterVectorizationCancelledError("cancelled"),
    RasterVectorizationError("unrelated fit failure"),
])
def test_non_topology_failure_is_never_retried(monkeypatch, error):
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(trace, "vectorize_pixel_source_forest", fail)
    with pytest.raises(type(error), match=str(error)):
        _fit(_source())
    assert len(calls) == 1


@pytest.mark.parametrize("tolerance,expected", [(0.1, [0.1, 0.025]), (0.02, [0.02, 0.01]), (0.01, [0.01])])
def test_persistent_ambiguity_stays_rejected_with_bounded_retry(monkeypatch, tolerance, expected):
    calls = []

    def fail(source, options, **kwargs):
        calls.append(options.simplification_tolerance_mm)
        raise RasterVectorizationTopologyError("Injected persistent ambiguity")

    monkeypatch.setattr(trace, "vectorize_pixel_source_forest", fail)
    with pytest.raises(RasterVectorizationTopologyError, match="threshold or Minimum area"):
        _fit(_source(), tolerance=tolerance)
    assert calls == expected


def test_success_and_usable_partial_forests_are_not_retried(monkeypatch):
    source = _source()
    original, attempts, reason = _fit(source)
    assert original.result is not None
    assert attempts == (0.1,) and reason is None
    partial = replace(original, root_tree_count=original.root_tree_count + 1, failures=(
        PixelVectorizationRootFailure(2, 1, "native_topology", "Unusable separate root"),
    ))
    calls = []

    def usable(*args, **kwargs):
        calls.append(args)
        return partial

    monkeypatch.setattr(trace, "vectorize_pixel_source_forest", usable)
    actual, attempts, reason = _fit(source)
    assert actual is partial
    assert len(calls) == 1 and attempts == (0.1,) and reason is None


def test_wholly_invalid_native_forest_gets_one_verified_retry(monkeypatch):
    native = trace.vectorize_pixel_source_forest
    calls = []

    def fail_first(source, options, **kwargs):
        calls.append(options.simplification_tolerance_mm)
        if len(calls) == 1:
            return PixelVectorizationForestResult(
                result=None, root_tree_count=1, failures=(
                    PixelVectorizationRootFailure(0, 1, "native_topology", "Unresolved boundary"),
                ),
            )
        return native(source, options, **kwargs)

    monkeypatch.setattr(trace, "vectorize_pixel_source_forest", fail_first)
    result, attempts, reason = _fit(_source())
    assert result.result is not None and result.valid_root_tree_count == 2
    assert calls == [0.1, 0.025] and attempts == (0.1, 0.025)
    assert reason == "Unresolved boundary"


def test_real_unresolved_native_boundary_remains_rejected():
    rng = np.random.default_rng(22)
    pixels = np.full((100, 160), 255, dtype=np.uint8)
    points = np.array([
        (round(80 + (30 + rng.uniform(-8, 8)) * np.cos(angle)),
         round(50 + (30 + rng.uniform(-8, 8)) * np.sin(angle)))
        for angle in np.linspace(0, 2 * np.pi, 40, endpoint=False)
    ], dtype=np.int32)
    cv2.fillPoly(pixels, [points], 0)
    source = prepare_pixel_vectorization_source(cv2.cvtColor(pixels, cv2.COLOR_GRAY2RGBA))
    previews = []
    result, attempts, reason = _fit(source, previews=previews)
    assert result.result is None and result.valid_root_tree_count == 0
    assert {failure.stage for failure in result.failures} == {"native_topology"}
    assert attempts == (0.1, 0.025) and "topology" in reason
    assert np.array_equal(previews[0].contour_mask, previews[1].contour_mask)


def test_cancel_between_attempts_prevents_retry(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise RasterVectorizationTopologyError("Injected topology failure")

    def check():
        if calls:
            raise trace.TraceDetectionCancelledError("Cancelled between fits")

    monkeypatch.setattr(trace, "vectorize_pixel_source_forest", fail)
    monkeypatch.setattr(trace, "_check_trace_cancelled", check)
    with pytest.raises(trace.TraceDetectionCancelledError, match="between fits"):
        _fit(_source())
    assert len(calls) == 1
