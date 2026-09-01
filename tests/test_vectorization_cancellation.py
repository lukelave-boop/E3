from __future__ import annotations

import threading
import time

import cv2
import numpy as np
import pytest

import laser_aligner.geometry.foreground as foreground_module
import laser_aligner.project.raster_vectorize as raster_vectorize_module
from laser_aligner.config import WorkArea
from laser_aligner.project import (
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationCancelledError,
    RasterVectorizationOptions,
    fit_physical_contours_to_native_path,
    prepare_pixel_vectorization_mask,
    prepare_pixel_vectorization_source,
    vectorize_pixel_source,
)
from laser_aligner.vision import (
    TraceDetectionCancelledError,
    TraceOptions,
    detect_objects,
)


def _circle_contour(point_count: int) -> np.ndarray:
    angles = np.linspace(0.0, 2.0 * np.pi, point_count, endpoint=False)
    return np.column_stack(
        (
            30.0 + 20.0 * np.cos(angles),
            30.0 + 20.0 * np.sin(angles),
        )
    )


def _complex_contour(point_count: int) -> np.ndarray:
    angles = np.linspace(0.0, 2.0 * np.pi, point_count, endpoint=False)
    radii = 20.0 + 1.5 * np.sin(37.0 * angles)
    return np.column_stack(
        (
            30.0 + radii * np.cos(angles),
            30.0 + radii * np.sin(angles),
        )
    )


def _hole_rich_trace_image() -> np.ndarray:
    image = np.full((800, 800, 3), 230, dtype=np.uint8)
    cv2.rectangle(image, (40, 40), (759, 759), (25, 25, 25), -1)
    for y in range(80, 760, 40):
        for column, x in enumerate(range(80, 760, 40)):
            radius = (4, 8, 16)[column % 3]
            cv2.circle(image, (x, y), radius, (230, 230, 230), -1)
    return image


def test_running_native_fit_observes_external_cancellation_promptly() -> None:
    cancel = threading.Event()
    fitting_started = threading.Event()
    errors: list[Exception] = []
    check_count = 0

    def cancel_check() -> bool:
        nonlocal check_count
        check_count += 1
        if check_count >= 32:
            fitting_started.set()
        return cancel.is_set()

    def run_fit() -> None:
        try:
            fit_physical_contours_to_native_path(
                [_complex_contour(8_192)],
                [None],
                source_pixel_spacing_mm=(0.01, 0.01),
                fitting_tolerance_mm=0.02,
                cancel_check=cancel_check,
            )
        except Exception as exc:  # worker outcome is asserted below
            errors.append(exc)

    worker = threading.Thread(target=run_fit, daemon=True)
    worker.start()
    reached_fitting = fitting_started.wait(2.0)
    cancel_started = time.perf_counter()
    cancel.set()
    worker.join(1.0)

    assert reached_fitting
    assert not worker.is_alive()
    assert time.perf_counter() - cancel_started < 1.0
    assert len(errors) == 1
    assert isinstance(errors[0], RasterVectorizationCancelledError)
    assert check_count >= 32


def test_non_cancelled_native_fit_is_byte_for_byte_unchanged() -> None:
    contour = _circle_contour(256)
    baseline = fit_physical_contours_to_native_path(
        [contour],
        [None],
        source_pixel_spacing_mm=(0.05, 0.05),
    )
    check_count = 0

    def continue_running() -> bool:
        nonlocal check_count
        check_count += 1
        return False

    cancellable = fit_physical_contours_to_native_path(
        [contour],
        [None],
        source_pixel_spacing_mm=(0.05, 0.05),
        cancel_check=continue_running,
    )

    assert check_count > 10
    assert cancellable == baseline
    assert cancellable.geometry.to_dict() == baseline.geometry.to_dict()


def test_pixel_vectorizer_polls_cancel_check_inside_bounded_work() -> None:
    grayscale = np.full((512, 512), 255, dtype=np.uint8)
    for y in range(30, 500, 45):
        for x in range(30, 500, 45):
            cv2.circle(grayscale, (x, y), 18, 0, -1)
    source = prepare_pixel_vectorization_source(
        cv2.cvtColor(grayscale, cv2.COLOR_GRAY2RGBA)
    )
    options = RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=127,
        minimum_feature_area_mm2=0.01,
        simplification_tolerance_mm=0.05,
    )
    check_count = 0

    def cancel_during_vectorization() -> bool:
        nonlocal check_count
        check_count += 1
        return check_count >= 64

    started = time.perf_counter()
    with pytest.raises(RasterVectorizationCancelledError):
        vectorize_pixel_source(
            source,
            options,
            displayed_width_mm=100.0,
            displayed_height_mm=100.0,
            cancel_check=cancel_during_vectorization,
        )

    assert check_count == 64
    assert time.perf_counter() - started < 1.0


def test_outer_vectorizer_observes_cancellation_after_external_opencv_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grayscale = np.full((192, 192), 255, dtype=np.uint8)
    cv2.rectangle(grayscale, (12, 12), (179, 179), 0, -1)
    for y in range(30, 170, 20):
        for x in range(30, 170, 20):
            cv2.circle(grayscale, (x, y), 5, 255, -1)
    source = prepare_pixel_vectorization_source(
        cv2.cvtColor(grayscale, cv2.COLOR_GRAY2RGBA)
    )
    options = RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=127,
        minimum_feature_area_mm2=0.0,
        simplification_tolerance_mm=0.10,
        contour_output=RasterContourOutput.OUTER_ONLY,
    )
    prepared = prepare_pixel_vectorization_mask(
        source,
        options,
        displayed_width_mm=48.0,
        displayed_height_mm=48.0,
    )
    original_find_contours = foreground_module.cv2.findContours
    cancelled = False
    retrieval_modes: list[int] = []

    def cancel_after_find_contours(mask, retrieval_mode, approximation):
        nonlocal cancelled
        result = original_find_contours(mask, retrieval_mode, approximation)
        retrieval_modes.append(retrieval_mode)
        cancelled = True
        return result

    monkeypatch.setattr(
        foreground_module.cv2,
        "findContours",
        cancel_after_find_contours,
    )

    with pytest.raises(RasterVectorizationCancelledError):
        vectorize_pixel_source(
            source,
            options,
            displayed_width_mm=48.0,
            displayed_height_mm=48.0,
            prepared_mask=prepared,
            cancel_check=lambda: cancelled,
        )

    assert retrieval_modes == [cv2.RETR_EXTERNAL]


def test_trace_cancellation_has_a_public_exception_and_no_preview() -> None:
    previews: list[object] = []

    with pytest.raises(TraceDetectionCancelledError):
        detect_objects(
            np.full((80, 120, 3), 255, dtype=np.uint8),
            TraceOptions(detection_mode="contrast", regular_grid=False),
            WorkArea(0.0, 30.0, 0.0, 20.0),
            4.0,
            raster_preview_callback=previews.append,
            cancel_check=lambda: True,
        )

    assert TraceDetectionCancelledError is RasterVectorizationCancelledError
    assert previews == []


def test_trace_cancel_check_reaches_the_shared_native_vectorizer() -> None:
    image = np.full((400, 400, 3), 230, dtype=np.uint8)
    cv2.circle(image, (200, 200), 80, (30, 30, 30), -1)
    check_count = 0

    def cancel_during_native_vectorization() -> bool:
        nonlocal check_count
        check_count += 1
        return check_count >= 64

    with pytest.raises(TraceDetectionCancelledError):
        detect_objects(
            image,
            TraceOptions(
                detection_mode="contrast",
                contrast_threshold_mode="manual",
                contrast_threshold=128,
                regular_grid=False,
                output_mode="native",
                min_area_mm2=10.0,
                min_width_mm=2.0,
                min_height_mm=2.0,
            ),
            WorkArea(0.0, 100.0, 0.0, 100.0),
            4.0,
            cancel_check=cancel_during_native_vectorization,
        )

    assert check_count == 64


def test_hole_filtered_trace_cancels_inside_source_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_entered = threading.Event()
    cleanup_unique_entered = threading.Event()
    release_cleanup_unique = threading.Event()
    cleanup_completed = threading.Event()
    in_cleanup = threading.Event()
    cancel = threading.Event()
    errors: list[Exception] = []
    results: list[object] = []
    previews: list[object] = []
    original_cleanup = (
        raster_vectorize_module.clean_foreground_components_with_diagnostics
    )
    original_unique = foreground_module.np.unique

    def instrumented_cleanup(*args: object, **kwargs: object) -> object:
        assert kwargs["minimum_hole_area_px"] is not None
        assert kwargs["maximum_hole_area_px"] is not None
        assert callable(kwargs["cancellation_checkpoint"])
        in_cleanup.set()
        cleanup_entered.set()
        try:
            result = original_cleanup(*args, **kwargs)
        finally:
            in_cleanup.clear()
        cleanup_completed.set()
        return result

    def blocked_unique(*args: object, **kwargs: object) -> object:
        if in_cleanup.is_set() and not cleanup_unique_entered.is_set():
            cleanup_unique_entered.set()
            if not release_cleanup_unique.wait(2.0):
                raise AssertionError("Timed out waiting to cancel hole cleanup")
        return original_unique(*args, **kwargs)

    monkeypatch.setattr(
        raster_vectorize_module,
        "clean_foreground_components_with_diagnostics",
        instrumented_cleanup,
    )
    monkeypatch.setattr(foreground_module.np, "unique", blocked_unique)

    def run_trace() -> None:
        try:
            results.append(
                detect_objects(
                    _hole_rich_trace_image(),
                    TraceOptions(
                        detection_mode="contrast",
                        contrast_threshold_mode="manual",
                        contrast_threshold=128,
                        regular_grid=False,
                        output_mode="native",
                        min_area_mm2=1.0,
                        max_area_mm2=100_000.0,
                        min_hole_area_mm2=5.0,
                        max_hole_area_mm2=30.0,
                        min_width_mm=0.1,
                        min_height_mm=0.1,
                    ),
                    WorkArea(0.0, 200.0, 0.0, 200.0),
                    4.0,
                    raster_preview_callback=previews.append,
                    cancel_check=cancel.is_set,
                )
            )
        except Exception as exc:  # worker outcome is asserted below
            errors.append(exc)

    worker = threading.Thread(target=run_trace, daemon=True)
    worker.start()
    try:
        assert cleanup_entered.wait(3.0)
        assert cleanup_unique_entered.wait(3.0)
        cancel_started = time.perf_counter()
        cancel.set()
    finally:
        release_cleanup_unique.set()
    worker.join(1.0)

    assert not worker.is_alive()
    assert time.perf_counter() - cancel_started < 1.0
    assert len(errors) == 1
    assert isinstance(errors[0], TraceDetectionCancelledError)
    assert not cleanup_completed.is_set()
    assert results == []
    assert previews == []


@pytest.mark.parametrize("trace_detail", ["full", "outer_silhouette"])
def test_hole_filtered_trace_cancels_during_subsequent_native_fitting(
    monkeypatch: pytest.MonkeyPatch,
    trace_detail: str,
) -> None:
    fitting_entered = threading.Event()
    release_fitting = threading.Event()
    fitting_completed = threading.Event()
    cancel = threading.Event()
    errors: list[Exception] = []
    results: list[object] = []
    previews: list[object] = []
    original_fit = raster_vectorize_module._fit_contour

    def blocked_fit(*args: object, **kwargs: object) -> object:
        fitting_entered.set()
        if not release_fitting.wait(2.0):
            raise AssertionError("Timed out waiting to cancel native fitting")
        result = original_fit(*args, **kwargs)
        fitting_completed.set()
        return result

    monkeypatch.setattr(raster_vectorize_module, "_fit_contour", blocked_fit)

    def run_trace() -> None:
        try:
            results.append(
                detect_objects(
                    _hole_rich_trace_image(),
                    TraceOptions(
                        detection_mode="contrast",
                        contrast_threshold_mode="manual",
                        contrast_threshold=128,
                        regular_grid=False,
                        output_mode="native",
                        trace_detail=trace_detail,
                        min_area_mm2=1.0,
                        max_area_mm2=100_000.0,
                        min_hole_area_mm2=5.0,
                        max_hole_area_mm2=30.0,
                        min_width_mm=0.1,
                        min_height_mm=0.1,
                    ),
                    WorkArea(0.0, 200.0, 0.0, 200.0),
                    4.0,
                    raster_preview_callback=previews.append,
                    cancel_check=cancel.is_set,
                )
            )
        except Exception as exc:  # worker outcome is asserted below
            errors.append(exc)

    worker = threading.Thread(target=run_trace, daemon=True)
    worker.start()
    try:
        assert fitting_entered.wait(3.0)
        cancel_started = time.perf_counter()
        cancel.set()
    finally:
        release_fitting.set()
    worker.join(1.0)

    assert not worker.is_alive()
    assert time.perf_counter() - cancel_started < 1.0
    assert len(errors) == 1
    assert isinstance(errors[0], TraceDetectionCancelledError)
    assert not fitting_completed.is_set()
    assert results == []
    assert len(previews) == 1
    assert previews[0].connected_component_count == 1
    contour_mask = previews[0].contour_mask
    assert int(contour_mask[80 * 4 + 2, 80 * 4 + 2]) == 255
    assert int(contour_mask[80 * 4 + 2, 120 * 4 + 2]) == 0
    assert int(contour_mask[80 * 4 + 2, 160 * 4 + 2]) == 255
