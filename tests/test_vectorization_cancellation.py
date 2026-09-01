from __future__ import annotations

import threading
import time

import cv2
import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.project import (
    RasterDetectionMode,
    RasterVectorizationCancelledError,
    RasterVectorizationOptions,
    fit_physical_contours_to_native_path,
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
