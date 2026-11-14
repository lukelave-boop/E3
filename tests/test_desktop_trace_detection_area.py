from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtGui, QtWidgets

from laser_aligner.desktop import main_window
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import TracePanel
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import Bounds


@pytest.fixture(scope="module")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


def test_area_controls_are_explicit_and_not_persisted(application):
    panel = TracePanel()
    panel.set_calibration_ready(True)
    assert not panel.select_detection_area_button.isEnabled()
    assert "Turn off Use grid" in panel.detection_area_label.text()
    panel.regular_grid.setChecked(False)
    assert panel.select_detection_area_button.isEnabled()
    assert not panel.clear_detection_area_button.isEnabled()
    panel.set_detection_area((10.0, 20.0, 60.0, 80.0))
    assert "50.0 x 60.0 mm" in panel.detection_area_label.text()
    assert panel.clear_detection_area_button.isEnabled()
    assert not panel.regular_grid.isEnabled()
    assert "detection_area_mm" not in panel.options()
    panel._save_preferences()
    second = TracePanel()
    assert second._detection_area_mm is None
    assert not second.clear_detection_area_button.isEnabled()
    panel.set_detection_area(None, selecting=True)
    assert panel.select_detection_area_button.text() == "Cancel area selection"
    assert not panel.regular_grid.isEnabled()
    panel.set_detection_area(None)
    assert panel.regular_grid.isEnabled()
    panel.close()
    second.close()


def _harness():
    workspace = WorkspaceView(Bounds(0, 0, 100, 100))
    image = QtGui.QImage(200, 200, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("gray"))
    workspace.set_camera_image(image, pixels_per_mm=2.0)
    panel = TracePanel()
    panel.regular_grid.setChecked(False)
    panel.set_calibration_ready(True)
    cancelled = []
    h = SimpleNamespace(
        workspace=workspace, trace_panel=panel,
        _trace_detection_area_mm=None,
        _active_trace_request_id=21,
        _trace_result={"old": True},
        _trace_raster_preview_images={},
        _controller_busy=False, _job_preparation_busy=False,
        controller=SimpleNamespace(cancel_trace_detection=lambda: cancelled.append(True)),
        runtime=SimpleNamespace(context=SimpleNamespace(
            bed=SimpleNamespace(calibration=object()),
            machine=SimpleNamespace(status=lambda: {}),
        )),
        _reconcile_pristine_project_frame=lambda: False,
        _require_project_machine_work_area_match=lambda: None,
        _activate_selection_tool=lambda **kwargs: None,
        _clear_template_preview=lambda **kwargs: None,
        show_notice=lambda message: None,
        show_error=lambda message: pytest.fail(message),
    )
    h._clear_trace_preview = lambda: E3MainWindow._clear_trace_preview(h)
    workspace.traceDetectionAreaSelectionCancelled.connect(
        lambda: E3MainWindow._trace_detection_area_cancelled(h)
    )
    return h, cancelled


def test_select_clear_and_cancel_retire_stale_results(application):
    h, cancelled = _harness()
    E3MainWindow._begin_trace_detection_area(h)
    assert h._active_trace_request_id is None
    assert h._trace_result is None
    assert cancelled
    assert h.workspace.trace_detection_area_selection_active
    h.workspace.cancel_trace_detection_area_selection()
    assert not h.trace_panel._detection_area_selecting
    area = (10.0, 20.0, 70.0, 90.0)
    E3MainWindow._trace_detection_area_selected(h, area)
    assert h._trace_detection_area_mm == area
    assert h.workspace.trace_detection_area == area
    E3MainWindow._begin_trace_detection_area(h)
    h.workspace.cancel_trace_detection_area_selection()
    assert h._trace_detection_area_mm == area
    assert h.workspace.trace_detection_area == area
    E3MainWindow._clear_trace_detection_area(h)
    assert h._trace_detection_area_mm is None
    assert h.workspace.trace_detection_area is None
    assert not h.trace_panel.clear_detection_area_button.isEnabled()
    h.workspace.close()
    h.trace_panel.close()


def test_detect_forwards_selection_and_rejects_late_old_result(application, monkeypatch):
    h, _ = _harness()
    monkeypatch.setattr(main_window, "project_machine_state", lambda *a, **kw: SimpleNamespace(
        can_motion_calibration=True,
    ))
    area = (10.0, 20.0, 70.0, 90.0)
    E3MainWindow._trace_detection_area_selected(h, area)
    requests = []
    h.controller.detect_trace_objects = lambda options, **kwargs: (requests.append((options, kwargs)) or 22)
    E3MainWindow._detect_trace_objects(h, {"regular_grid": False})
    assert h._active_trace_request_id == 22
    assert requests == [({"regular_grid": False}, {"detection_area_mm": area})]
    E3MainWindow._clear_trace_detection_area(h)
    E3MainWindow._trace_result_ready(h, {"request_id": 22, "detections": [{"old": True}]})
    assert h._trace_result is None
    E3MainWindow._detect_trace_objects(h, {"regular_grid": False})
    assert requests[-1] == ({"regular_grid": False}, {})
    h.workspace.close()
    h.trace_panel.close()


def test_map_change_discards_area_and_candidate_evidence(application):
    h, _ = _harness()
    E3MainWindow._trace_detection_area_selected(h, (10, 20, 70, 90))
    E3MainWindow._calibration_review_evidence_invalidated(h)
    assert h._trace_detection_area_mm is None
    assert h.workspace.trace_detection_area is None
    assert h._trace_result is None
    assert not h.trace_panel.clear_detection_area_button.isEnabled()
    h.workspace.close()
    h.trace_panel.close()


def test_camera_invalidation_clears_area_and_blank_camera_cannot_select(application):
    h, cancelled = _harness()
    E3MainWindow._trace_detection_area_selected(h, (10, 20, 70, 90))
    E3MainWindow._begin_trace_detection_area(h)
    E3MainWindow._camera_image_invalidated(h)
    assert h._trace_detection_area_mm is None
    assert not h.workspace.trace_detection_area_selection_active
    assert cancelled
    notices = []
    h.show_notice = notices.append
    E3MainWindow._begin_trace_detection_area(h)
    assert not h.workspace.trace_detection_area_selection_active
    assert "Refresh the camera" in notices[-1]
    h.workspace.close()
    h.trace_panel.close()
