from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from laser_aligner.calibration.lens import LensModel
from laser_aligner.core import CoreRuntime
from laser_aligner.desktop.machine_setup import MachineSetupDialog
from laser_aligner.desktop.qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _wait_until(
    application: QtWidgets.QApplication,
    predicate,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for Qt background operation")
        time.sleep(0.005)


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _runtime(tmp_path: Path) -> CoreRuntime:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "default.json").read_text(encoding="utf-8"))
    payload["app"]["data_dir"] = str(tmp_path / "data")
    payload["app"]["open_browser"] = False
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    runtime = CoreRuntime.from_config(path, hardware_enabled=False)
    runtime.start()
    return runtime


def _quality(sharpness: float) -> dict[str, float | int]:
    return {
        "width": 1920,
        "height": 1080,
        "sharpness": sharpness,
        "luminance_mean": 126.0,
        "luminance_p01": 8.0,
        "luminance_p99": 244.0,
        "contrast_span": 236.0,
        "shadow_clip_percent": 0.2,
        "highlight_clip_percent": 0.3,
    }


def _lens_status(width: int, height: int) -> dict[str, Any]:
    images = [
        {
            "name": "current-top-left.png",
            "found": True,
            "corner_count": 54,
            "size": 100,
            "width": width,
            "height": height,
            "quality": _quality(321.4),
            "board_coverage_percent": 34.8,
            "board_center": [0.15, 0.17],
            "selected_for_active_resolution": True,
        },
        {
            "name": "current-bottom-right.png",
            "found": True,
            "corner_count": 54,
            "size": 100,
            "width": width,
            "height": height,
            "quality": _quality(287.9),
            "board_coverage_percent": 31.2,
            "board_center": [0.82, 0.79],
            "selected_for_active_resolution": True,
        },
        {
            "name": "legacy.jpg",
            "found": False,
            "corner_count": 0,
            "size": 100,
            "width": 1280,
            "height": 720,
            "quality": _quality(80.0),
            "board_coverage_percent": 0.0,
            "board_center": None,
            "selected_for_active_resolution": False,
        },
    ]
    views = [
        {
            "name": "current-top-left.png",
            "accepted": True,
            "reprojection_rms_px": 0.42,
            "reprojection_p95_px": 0.61,
            "reprojection_max_px": 0.74,
        },
        {
            "name": "current-bottom-right.png",
            "accepted": False,
            "exclusion_reason": "reprojection_outlier",
            "reprojection_rms_px": 1.87,
            "reprojection_p95_px": 2.12,
            "reprojection_max_px": 2.45,
        },
    ]
    quality = {
        "gate": "warning",
        "reject_reasons": [],
        "warning_reasons": [
            {
                "code": "limited_pose_span_minor",
                "message": "Checkerboard tilt diversity is limited in its second direction",
            }
        ],
        "input_count": 2,
        "accepted_count": 1,
        "metrics": {
            "corner_hull_ratio": 0.63,
            "pose_span_major_deg": 19.0,
            "pose_span_minor_deg": 5.0,
            "overall_rms_px": 0.42,
        },
        "views": views,
    }
    return {
        "calibrated": True,
        "model": {
            "image_width": width,
            "image_height": height,
            "rms_error": 0.42,
            "mean_reprojection_error": 0.31,
            "quality": quality,
            "views": views,
        },
        "image_count": len(images),
        "usable_image_count": 2,
        "total_usable_image_count": 2,
        "images": images,
        "active_resolution": {"width": width, "height": height},
        "resolution_selection": "requested",
        "resolution_groups": [
            {
                "width": 1280,
                "height": 720,
                "image_count": 1,
                "usable_image_count": 0,
                "selected": False,
            },
            {
                "width": width,
                "height": height,
                "image_count": 2,
                "usable_image_count": 2,
                "selected": True,
            },
        ],
        "last_solve_quality": quality,
        "view_coverage": {"occupied_cells": 2, "total_cells": 9, "percent": 22.2},
        "pattern": {"columns": 9, "rows": 6, "square_size_mm": 20.0, "minimum_images": 2},
    }


def _model(width: int, height: int, distortion: float) -> LensModel:
    focal = float(max(width, height))
    return LensModel(
        camera_matrix=np.asarray(
            [
                [focal, 0.0, width / 2.0],
                [0.0, focal, height / 2.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        ),
        distortion=np.asarray([distortion, 0.0, 0.0, 0.0, 0.0]),
        image_width=width,
        image_height=height,
        rms_error=0.4,
        mean_reprojection_error=0.3,
        images_used=12,
        created_at=1.0,
    )


def test_lens_tab_shows_current_group_capture_evidence_and_worst_views(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    camera = runtime.context.camera.status()
    requested_sizes: list[tuple[int, int] | None] = []
    payload = _lens_status(camera.width, camera.height)

    def status(*, image_size=None):
        requested_sizes.append(image_size)
        return payload

    monkeypatch.setattr(runtime.context.lens, "status", status)
    try:
        dialog.refresh_all()

        assert requested_sizes[-1] == (camera.width, camera.height)
        assert f"Current camera: {camera.width} x {camera.height}" in dialog.lens_resolution_status.text()
        assert "CURRENT" in dialog.lens_resolution_status.text()
        assert dialog.lens_captures.rowCount() == 3
        assert dialog.lens_captures.horizontalHeaderItem(3).text() == "Preview sharpness"
        assert dialog.lens_captures.item(0, 2).text() == "Yes (54)"
        assert dialog.lens_captures.item(0, 3).text() == "321.4 @ 1920x1080"
        assert "same displayed dimensions" in dialog.lens_captures.item(0, 3).toolTip()
        assert dialog.lens_captures.item(0, 4).text() == "34.8%"
        assert dialog.lens_captures.item(0, 5).text() == "top-left"
        assert "Contrast 236" in dialog.lens_captures.item(0, 6).text()
        assert "Gate: WARNING" in dialog.lens_gate_status.text()
        assert "tilt diversity" in dialog.lens_gate_status.text()
        assert dialog.lens_view_errors.item(0, 0).text() == "current-bottom-right.png"
        assert dialog.lens_view_errors.item(0, 1).text() == "No"
        assert dialog.lens_solve_button.isEnabled()
        assert dialog.base_grid_capture_button.isEnabled()

        payload["last_solve_quality"] = {
            "gate": "reject",
            "reject_reasons": [
                {
                    "code": "ambiguous_resolution",
                    "message": "Select the current camera resolution explicitly",
                }
            ],
            "warning_reasons": [],
        }
        dialog.refresh_all()
        assert "Gate: REJECT" in dialog.lens_gate_status.text()
        assert "REJECT [ambiguous_resolution]" in dialog.lens_gate_status.text()
        assert dialog.lens_view_errors.rowCount() == 0
        # A rejected replacement attempt does not invalidate the still-active,
        # previously accepted model.
        assert dialog.base_grid_capture_button.isEnabled()

        payload["last_solve_quality"] = None
        payload["model"]["quality"] = {}
        dialog.refresh_all()
        assert not dialog.base_grid_capture_button.isEnabled()
        assert "pose-diversity" in dialog.base_grid_capture_button.toolTip()
        assert "genuinely tilted" in dialog.base_grid_capture_button.toolTip()
    finally:
        dialog.close()
        runtime.stop()


def test_ready_catalog_exposes_force_reindex_all_recovery(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    camera = runtime.context.camera.status()
    payload = _lens_status(camera.width, camera.height)
    payload["index"] = {
        "state": "ready",
        "indexing": False,
        "ready_count": 3,
        "pending_count": 0,
        "error_count": 0,
        "total_count": 3,
        "working_width": 640,
        "working_height": 360,
    }
    called = threading.Event()
    monkeypatch.setattr(
        runtime.context.lens,
        "status",
        lambda *, image_size=None: payload,
    )
    monkeypatch.setattr(
        runtime.context.lens,
        "reindex_all_captures",
        lambda: called.set()
        or {
            "indexed_count": 3,
            "usable_count": 2,
            "error_count": 0,
        },
    )
    dialog = MachineSetupDialog(runtime)
    try:
        assert dialog.lens_retry_index_button.text() == "Re-index all captures"
        assert dialog.lens_retry_index_button.isEnabled()

        dialog.lens_retry_index_button.click()
        _wait_until(qt_application, called.is_set)
        _wait_until(qt_application, lambda: not dialog.lens_index_busy)

        assert "3 updated" in dialog.operation_status.text()
        assert dialog.lens_retry_index_button.isEnabled()
    finally:
        dialog.close()
        runtime.stop()


def test_lens_readiness_blocks_capture_and_solve_with_visible_reasons(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    camera = runtime.context.camera.status()
    monkeypatch.setattr(
        runtime.context.lens,
        "status",
        lambda *, image_size=None: _lens_status(camera.width, camera.height),
    )
    monkeypatch.setattr(
        runtime.context,
        "camera_calibration_readiness",
        lambda: {
            "state": "BLOCKED",
            "reasons": ["focus absolute is unverified", "white balance temperature is unverified"],
        },
    )
    try:
        dialog.refresh_all()

        assert "Calibration readiness: BLOCKED" in dialog.lens_resolution_status.text()
        assert "focus absolute is unverified" in dialog.lens_resolution_status.text()
        assert not dialog.lens_capture_button.isEnabled()
        assert not dialog.lens_solve_button.isEnabled()
    finally:
        dialog.close()
        runtime.stop()


def test_preview_false_negative_does_not_disable_full_resolution_solve(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    camera = runtime.context.camera.status()
    payload = _lens_status(camera.width, camera.height)
    for image in payload["images"]:
        if image["selected_for_active_resolution"]:
            image["found"] = False
            image["corner_count"] = 0
    payload["usable_image_count"] = 0
    payload["total_usable_image_count"] = 0
    next(
        group for group in payload["resolution_groups"] if group.get("selected")
    )["usable_image_count"] = 0
    payload["index"] = {
        "state": "ready",
        "indexing": False,
        "ready_count": 3,
        "pending_count": 0,
        "error_count": 0,
        "total_count": 3,
        "working_width": 640,
        "working_height": 360,
    }
    monkeypatch.setattr(
        runtime.context.lens,
        "status",
        lambda *, image_size=None: payload,
    )
    dialog = MachineSetupDialog(runtime)
    try:
        assert "2/2 captures" in dialog.lens_status.text()
        assert "0 preview-detected" in dialog.lens_status.text()
        assert dialog.lens_solve_button.isEnabled()
        assert "originals" in dialog.lens_index_status.text()
    finally:
        dialog.close()
        runtime.stop()


def test_cold_lens_index_runs_offscreen_without_freezing_or_unsafe_close(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    lens = runtime.context.lens
    assert cv2.imwrite(
        str(lens.image_dir / "legacy-camera-frame.jpg"),
        np.zeros((1080, 1920, 3), dtype=np.uint8),
    )
    original_index = lens.index_pending_captures
    worker_entered = threading.Event()
    release_worker = threading.Event()
    detector_shapes: list[tuple[int, int]] = []

    def detect(image: np.ndarray) -> tuple[bool, None]:
        detector_shapes.append(tuple(image.shape[:2]))
        return False, None

    def blocked_index(*, retry_errors: bool = False) -> dict[str, Any]:
        worker_entered.set()
        assert release_worker.wait(3.0)
        return original_index(retry_errors=retry_errors)

    monkeypatch.setattr(lens, "detect_corners", detect)
    monkeypatch.setattr(lens, "index_pending_captures", blocked_index)
    dialog = MachineSetupDialog(runtime)
    try:
        # Construction/catalog polling must not run the detector on the GUI thread.
        assert detector_shapes == []
        assert dialog.lens_captures.item(0, 2).text() == "Pending"

        dialog.show()
        _wait_until(qt_application, worker_entered.is_set)
        assert dialog.lens_index_busy
        assert dialog.tabs.isEnabled()
        dialog.tabs.setCurrentIndex(1)
        qt_application.processEvents()
        assert dialog.lens_index_progress.isVisible()
        assert "final solve rechecks" in dialog.lens_index_status.text()
        assert not dialog.lens_capture_button.isEnabled()
        assert not dialog.lens_solve_button.isEnabled()
        assert not dialog.lens_clear_captures_button.isEnabled()
        dialog.lens_captures.selectRow(0)
        assert not dialog.lens_delete_capture_button.isEnabled()

        responsive: list[bool] = []
        QtCore.QTimer.singleShot(0, lambda: responsive.append(True))
        _wait_until(qt_application, lambda: bool(responsive))

        dialog.close()
        qt_application.processEvents()
        assert dialog.isVisible()
        assert "indexing is still running" in dialog.operation_status.text()

        release_worker.set()
        _wait_until(qt_application, lambda: not dialog.lens_index_busy)
        assert detector_shapes == [(360, 640)]
        assert not dialog.lens_index_progress.isVisible()
        assert "Evidence catalog ready" in dialog.lens_index_status.text()
        dialog.close()
        qt_application.processEvents()
        assert not dialog.isVisible()
    finally:
        release_worker.set()
        _wait_until(qt_application, lambda: not dialog.lens_index_busy)
        dialog.close()
        runtime.stop()


def test_lens_evidence_delete_and_clear_are_confirmation_gated(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    camera = runtime.context.camera.status()
    payload = _lens_status(camera.width, camera.height)
    deleted: list[str] = []
    cleared: list[bool] = []

    def status(*, image_size=None):
        return payload

    def delete_capture(name: str) -> bool:
        deleted.append(name)
        payload["images"] = [item for item in payload["images"] if item["name"] != name]
        payload["image_count"] = len(payload["images"])
        return True

    def clear_captures() -> int:
        cleared.append(True)
        count = len(payload["images"])
        payload["images"] = []
        payload["image_count"] = 0
        return count

    monkeypatch.setattr(runtime.context.lens, "status", status)
    monkeypatch.setattr(runtime.context.lens, "delete_capture", delete_capture)
    monkeypatch.setattr(runtime.context.lens, "clear_captures", clear_captures)
    answers = iter(
        (
            QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Yes,
            QtWidgets.QMessageBox.StandardButton.Yes,
        )
    )
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *args, **kwargs: next(answers))
    try:
        dialog.refresh_all()
        dialog.lens_captures.selectRow(0)
        dialog.delete_lens_capture()
        assert deleted == []

        dialog.delete_lens_capture()
        assert deleted == ["current-top-left.png"]
        assert dialog.lens_captures.rowCount() == 2

        dialog.clear_lens_captures()
        assert cleared == [True]
        assert dialog.lens_captures.rowCount() == 0
        assert payload["calibrated"] is True
    finally:
        dialog.close()
        runtime.stop()


def test_replacing_or_clearing_lens_marks_bed_stale_and_disables_dependents(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    camera = runtime.context.camera.status()
    original = _model(camera.width, camera.height, 0.0)
    replacement = _model(camera.width, camera.height, 0.02)
    runtime.context.lens.save_model(original)
    runtime.context.solve_bed()
    dialog = MachineSetupDialog(runtime)

    def solve(*, image_size):
        assert image_size == (camera.width, camera.height)
        runtime.context.lens.save_model(replacement)
        return replacement

    monkeypatch.setattr(runtime.context.lens, "solve", solve)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    try:
        assert dialog.registration_prepare_button.isEnabled()
        dialog.solve_lens()
        _wait_until(qt_application, lambda: not dialog.operation_busy)

        assert runtime.context.bed_status()["validity"]["state"] == "STALE"
        assert "Bed map dependency: STALE" in dialog.lens_bed_status.text()
        assert not dialog.registration_prepare_button.isEnabled()
        assert not dialog.validation_prepare_button.isEnabled()
        assert not dialog.rough_grid_detect_button.isEnabled()

        runtime.context.lens.save_model(original)
        runtime.context.solve_bed()
        dialog.refresh_all()
        assert dialog.registration_prepare_button.isEnabled()

        dialog.clear_lens()
        assert runtime.context.lens.model is None
        assert runtime.context.bed_status()["validity"]["state"] == "STALE"
        assert "Bed map dependency: STALE" in dialog.lens_bed_status.text()
        assert not dialog.registration_prepare_button.isEnabled()
        assert not dialog.base_grid_capture_button.isEnabled()
    finally:
        dialog.close()
        runtime.stop()


def test_lens_management_remains_reachable_at_minimum_dialog_size(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    camera = runtime.context.camera.status()
    monkeypatch.setattr(
        runtime.context.lens,
        "status",
        lambda *, image_size=None: _lens_status(camera.width, camera.height),
    )
    dialog = MachineSetupDialog(runtime)
    try:
        dialog.tabs.setCurrentIndex(1)
        dialog.resize(dialog.minimumSize())
        dialog.show()
        qt_application.processEvents()

        scroll = dialog.lens_scroll_area
        assert scroll.verticalScrollBar().maximum() > 0
        assert scroll.horizontalScrollBar().maximum() == 0
        scroll.ensureWidgetVisible(dialog.lens_clear_model_button)
        qt_application.processEvents()
        viewport_top_left = scroll.viewport().mapToGlobal(QtCore.QPoint(0, 0))
        viewport_rect = QtCore.QRect(viewport_top_left, scroll.viewport().size())
        button_top_left = dialog.lens_clear_model_button.mapToGlobal(QtCore.QPoint(0, 0))
        button_rect = QtCore.QRect(button_top_left, dialog.lens_clear_model_button.size())
        assert viewport_rect.intersects(button_rect)
    finally:
        dialog.close()
        runtime.stop()
