from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from laser_aligner.desktop.qt import require_qt
from laser_aligner.desktop.raster_vectorize_dialog import (
    _LIVE_PREVIEW_TASKS,
    RasterVectorizationDialog,
)
from laser_aligner.project.raster_asset import read_raster_asset_payload
from laser_aligner.project.raster_vectorize import RasterDetectionMode

QtCore, QtGui, QtWidgets = require_qt()


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


def _wait_until(
    application: QtWidgets.QApplication,
    predicate: Callable[[], bool],
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    application.processEvents()
    assert predicate()


def _payload(path: Path) -> Any:
    image = QtGui.QImage(10, 8, QtGui.QImage.Format.Format_RGBA8888)
    image.fill(QtGui.QColor(255, 255, 255, 0))
    painter = QtGui.QPainter(image)
    painter.fillRect(QtCore.QRect(2, 1, 6, 6), QtGui.QColor(20, 20, 20, 255))
    painter.end()
    assert image.save(str(path), "PNG")
    return read_raster_asset_payload(path)


def _result(*, has_alpha: bool = True) -> Any:
    rgba = np.full((8, 10, 4), 255, dtype=np.uint8)
    rgba[:, :, :3] = 238
    rgba[1:7, 2:8, :3] = 20
    if has_alpha:
        rgba[:, :, 3] = 0
        rgba[1:7, 2:8, 3] = 255
    mask = np.zeros((8, 10), dtype=np.uint8)
    mask[1:7, 2:8] = 255
    contour = SimpleNamespace(
        points=np.asarray(
            [
                (-0.3, -0.375),
                (0.3, -0.375),
                (0.3, 0.375),
                (-0.3, 0.375),
            ],
            dtype=np.float64,
        ),
        is_hole=False,
        max_fitting_error_mm=0.009,
        trace_cleanup_deviation_mm=0.007,
        smoothing_displacement_mm=0.002,
    )
    return SimpleNamespace(
        source_rgba=rgba,
        foreground_mask=mask,
        overlay_rgba=rgba.copy(),
        contours=(contour,),
        threshold_used=127,
        has_usable_alpha=has_alpha,
        raw_contour_point_count=96,
        fitted_segment_count=4,
        preview_flattened_point_count=5,
        max_estimated_deviation_mm=0.018,
    )


def _close_dialog(
    dialog: RasterVectorizationDialog,
    application: QtWidgets.QApplication,
) -> None:
    dialog.reject()
    dialog.deleteLater()
    application.processEvents()


def test_dialog_previews_controls_stats_and_acceptance_contract(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path / "transparent.png")
    results: list[Any] = []

    def vectorize(*_args: Any, **_kwargs: Any) -> Any:
        result = _result()
        results.append(result)
        return result

    dialog = RasterVectorizationDialog(
        payload,
        25.0,
        20.0,
        debounce_ms=0,
        vectorizer=vectorize,
    )
    dialog.show()
    _wait_until(qt_application, dialog.create_button.isEnabled)

    assert not dialog.original_preview._image.isNull()
    assert not dialog.mask_preview._image.isNull()
    assert not dialog.overlay_preview._image.isNull()
    assert "Raw contour points 96" in dialog.stats_label.text()
    assert "fitted segments 4" in dialog.stats_label.text()
    assert "preview-flattened points 5" in dialog.stats_label.text()
    assert "final E3 points" not in dialog.stats_label.text()
    assert "maximum validated fit 0.009 mm" in dialog.stats_label.text()
    assert "trace cleanup bound 0.007 mm" in dialog.stats_label.text()
    assert "user smoothing 0.002 mm" in dialog.stats_label.text()
    assert not dialog.threshold_row.isEnabled()
    assert dialog.alpha_row.isEnabled()
    assert dialog.detection_combo.model().item(dialog._alpha_mode_index).isEnabled()

    manual_index = dialog.detection_combo.findData(
        RasterDetectionMode.MANUAL_THRESHOLD
    )
    dialog.detection_combo.setCurrentIndex(manual_index)
    dialog.threshold_spin.setValue(91)
    _wait_until(qt_application, dialog.create_button.isEnabled)
    assert dialog.threshold_row.isEnabled()

    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("keep"))
    dialog.hide_source_check.setChecked(True)
    assert dialog.source_handling == "keep"
    assert dialog.hide_source_after

    dialog.create_button.click()
    qt_application.processEvents()

    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert dialog.vectorization_result is results[-1]
    assert dialog.accepted_options is not None
    assert dialog.accepted_options.threshold == 91
    assert dialog.accepted_options.detection_mode == RasterDetectionMode.MANUAL_THRESHOLD
    dialog.deleteLater()
    qt_application.processEvents()


def test_dialog_default_pipeline_reuses_one_verified_prepared_source(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path / "real-pipeline.png")
    dialog = RasterVectorizationDialog(
        payload,
        25.0,
        20.0,
        debounce_ms=0,
    )
    try:
        dialog.show()
        _wait_until(
            qt_application,
            lambda: dialog.create_button.isEnabled()
            or dialog.status_label.text().startswith("Could not vectorize"),
        )
        prepared = dialog._prepared_source
        assert prepared is not None
        assert prepared.identity == payload.identity
        assert not dialog.original_preview._image.isNull()
        assert dialog.detection_combo.model().item(dialog._alpha_mode_index).isEnabled()

        alpha_index = dialog.detection_combo.findData(RasterDetectionMode.ALPHA)
        dialog.detection_combo.setCurrentIndex(alpha_index)
        _wait_until(qt_application, dialog.create_button.isEnabled)
        assert dialog._current_result is not None
        assert dialog._current_result.source_identity == payload.identity

        dialog.minimum_feature_spin.setValue(0.10)
        _wait_until(qt_application, dialog.create_button.isEnabled)
        assert dialog._prepared_source is prepared
    finally:
        _close_dialog(dialog, qt_application)


def test_preview_work_is_bounded_to_one_running_and_one_latest_pending(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path / "coalesced.png")
    started = threading.Event()
    release = threading.Event()
    calls: list[Any] = []

    def vectorize(_payload: Any, options: Any, **_kwargs: Any) -> Any:
        calls.append(options)
        if len(calls) == 1:
            started.set()
            assert release.wait(3.0)
        return _result()

    dialog = RasterVectorizationDialog(
        payload,
        25.0,
        20.0,
        debounce_ms=0,
        vectorizer=vectorize,
    )
    try:
        dialog.show()
        _wait_until(qt_application, started.is_set)
        manual_index = dialog.detection_combo.findData(
            RasterDetectionMode.MANUAL_THRESHOLD
        )
        dialog.detection_combo.setCurrentIndex(manual_index)
        dialog.threshold_spin.setValue(40)
        dialog.threshold_spin.setValue(80)
        dialog.threshold_spin.setValue(123)

        assert len(calls) == 1
        assert dialog._pending_request is not None
        release.set()
        _wait_until(qt_application, dialog.create_button.isEnabled)

        assert len(calls) == 2
        assert calls[-1].detection_mode == RasterDetectionMode.MANUAL_THRESHOLD
        assert calls[-1].threshold == 123
    finally:
        release.set()
        _close_dialog(dialog, qt_application)


def test_cancel_during_worker_retains_task_without_late_dialog_callbacks(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    payload = _payload(tmp_path / "cancelled.png")
    started = threading.Event()
    release = threading.Event()
    callback_finished = threading.Event()

    def vectorize(*_args: Any, **_kwargs: Any) -> Any:
        started.set()
        assert release.wait(3.0)
        callback_finished.set()
        return _result()

    dialog = RasterVectorizationDialog(
        payload,
        25.0,
        20.0,
        debounce_ms=0,
        vectorizer=vectorize,
    )
    dialog.show()
    _wait_until(qt_application, started.is_set)
    task = dialog._active_task
    assert task is not None
    assert task in _LIVE_PREVIEW_TASKS

    dialog.reject()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
    assert dialog.vectorization_result is None
    dialog.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(
        None,
        QtCore.QEvent.Type.DeferredDelete,
    )
    qt_application.processEvents()

    release.set()
    _wait_until(qt_application, callback_finished.is_set)
    _wait_until(qt_application, lambda: task not in _LIVE_PREVIEW_TASKS)
