from __future__ import annotations

# Qt must select the headless platform before desktop module imports.
# ruff: noqa: E402, I001
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.machine_setup import MachineSetupDialog
from laser_aligner.desktop.main_view_probe import MainViewProbe
from tests.test_desktop_job_async import _dispose, _window
from tests.test_desktop_laser_focus import result, status


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    item, errors, _notices = _window(tmp_path, monkeypatch)
    item.inspector_tabs.select_panel("machine")
    item.mainboard_z._timer.stop()
    item.focus_workspace.coordinator._timer.stop()
    app.processEvents()
    yield item
    item.focus_workspace.shutdown(force=True)
    _dispose(app, item)
    assert not errors


def test_machine_embeds_reference_and_retention_without_focus_dialog(window, app):
    workspace = window.focus_workspace
    assert workspace.parentWidget() is window.machine_panel.z_control
    assert workspace.coordinator.controller is window.controller
    assert not workspace.panel.calibration_mode
    assert not hasattr(window.machine_panel.z_control, "focus")
    assert not hasattr(window, "open_laser_focus")
    assert not any("Surface / laser focus" in button.text()
                   for button in window.machine_panel.findChildren(QtWidgets.QPushButton))
    group_names = [group.title() for group in workspace.findChildren(QtWidgets.QGroupBox)
                   if group.isVisible()]
    assert any("Reference and measure" in title for title in group_names)
    assert not any("Preview and position" in title for title in group_names)
    assert workspace.panel.teach is None
    workspace.set_machine_status(status())
    workspace.panel.set_result(result(z_retention={
        "available": True, "restored": True, "reason": "Border datum restored.",
    }))
    app.processEvents()
    assert workspace.panel.z_retention_group.isVisible()
    assert workspace.panel.reference.text() == "Home / park XY"
    assert workspace.panel.forget_z.isEnabled()


def test_daily_probe_uses_main_canvas_without_duplicate_live_view(window):
    daily = window.focus_workspace
    assert isinstance(daily.bed_view, MainViewProbe)
    assert not any(label.text() == "Live bed view"
                   for label in daily.findChildren(QtWidgets.QLabel))
    assert "main view on the left" in daily.panel.camera_target.text()
    assert "main view on the left" in daily.panel.position_probe.toolTip()
    assert not hasattr(daily.bed_view, "_worker")


def test_main_image_delivery_preserves_probe_evidence_and_plain_images_clear_it(window):
    received = []
    window.workspace.cameraImageChanged.connect(received.append)
    area = window.workspace.workspace_scene.work_area
    ppm = window.runtime.settings.calibration.bed.pixels_per_mm
    image = QtGui.QImage(round(area.width * ppm), round(area.height * ppm), QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("#666666"))
    metadata = {"source_generation": 17}
    window._camera_image_ready({"image": image, "focus_frame_metadata": metadata})
    assert received[-1] == metadata
    window._camera_image_ready(image)
    assert received[-1] is None
    window._camera_image_invalidated()
    assert received[-1] is None


def test_machine_setup_owns_preview_and_resumes_daily_view(window, app, monkeypatch):
    daily = window.focus_workspace
    transitions = []
    suspend = daily.set_suspended

    def suspended(value):
        transitions.append(value)
        suspend(value)

    def setup_exec(dialog):
        assert transitions == [True]
        assert not daily.bed_view._active
        assert dialog.tabs.currentIndex() == 6
        setup = dialog.focus_workspace
        assert setup.coordinator.controller is window.controller
        assert setup.panel.calibration_mode
        assert setup.panel.teach is not None
        assert setup.panel.preview is not None
        assert setup.panel.move is not None
        assert setup.panel.use_job is not None
        labels = [check.text() for check in setup.findChildren(QtWidgets.QCheckBox)]
        assert "Gauge removed; path to target clear" not in labels
        assert "Same flat surface across job; gauge removed; Z and travel path clear" not in labels
        return 0

    monkeypatch.setattr(daily, "set_suspended", suspended)
    monkeypatch.setattr(MachineSetupDialog, "exec", setup_exec)
    window.open_machine_setup(6)
    app.processEvents()
    assert transitions == [True, False]
    assert daily.bed_view._active
    assert window._machine_setup_dialog is None
    assert not window.controller._calibration_review_active
    assert not daily.coordinator._closed


def test_failed_setup_construction_resumes_daily_view(window, monkeypatch):
    daily = window.focus_workspace
    transitions = []
    suspend = daily.set_suspended

    def suspended(value):
        transitions.append(value)
        suspend(value)

    def failed_setup(*_args, **_kwargs):
        assert transitions == [True]
        assert not daily.bed_view._active
        raise RuntimeError("Setup construction failed")

    monkeypatch.setattr(daily, "set_suspended", suspended)
    monkeypatch.setattr(main_window_module, "MachineSetupDialog", failed_setup)
    with pytest.raises(RuntimeError, match="Setup construction failed"):
        window.open_machine_setup(6)
    assert transitions == [True, False]
    assert daily.bed_view._active
    assert window._machine_setup_dialog is None
    assert not window.controller._calibration_review_active


def test_closing_from_setup_does_not_resume_daily_observers(window, monkeypatch):
    daily = window.focus_workspace
    transitions = []
    suspend = daily.set_suspended

    def suspended(value):
        transitions.append(value)
        suspend(value)

    def setup_exec(_dialog):
        assert window._prepare_close_request()
        return 0

    monkeypatch.setattr(daily, "set_suspended", suspended)
    monkeypatch.setattr(MachineSetupDialog, "exec", setup_exec)
    window.open_machine_setup(6)
    assert transitions == [True]
    assert daily.coordinator._closed
    assert not daily.bed_view._active
    assert not daily._camera_timer.isActive()
    assert window._machine_setup_dialog is None


def test_setup_cannot_interrupt_daily_mutation(window, monkeypatch):
    opened = []
    monkeypatch.setattr(main_window_module, "MachineSetupDialog", lambda *a, **k: opened.append(True))
    window.focus_workspace.coordinator._mutation = True
    try:
        window.open_machine_setup(6)
        assert opened == []
        assert "Wait for the Z operation" in window.statusBar().currentMessage()
    finally:
        window.focus_workspace.coordinator._mutation = False


@pytest.mark.parametrize("action", ["status", "measure"])
def test_window_shutdown_closes_pending_focus_and_rejects_late_reply(window, monkeypatch, action):
    workspace = window.focus_workspace
    coordinator = workspace.coordinator
    captured = []
    monkeypatch.setattr(window.controller, "_run", lambda operation, **kwargs: captured.append((operation, kwargs)))
    workspace.set_machine_status(status())
    workspace.panel.set_result(result())
    coordinator.request(action, {"confirmed": True, "clearance_z_mm": 30.0, "gap_mm": 7.0})
    assert coordinator._pending
    assert len(captured) == 1
    assert window._prepare_close_request()
    assert coordinator._closed
    assert not coordinator._timer.isActive()
    assert not workspace._camera_timer.isActive()
    assert not workspace.bed_view._active
    assert window.controller._shutdown_started
    before = dict(workspace.panel._result)
    _operation, callbacks = captured.pop()
    callbacks["on_success"](result(action=action, max_z_mm=40))
    callbacks["on_finished"]()
    assert workspace.panel._result == before
    assert not coordinator._pending


@pytest.mark.parametrize("mode", ["disconnected", "restored", "recovery"])
def test_machine_tab_switch_releases_camera_and_compact_controls_fit(window, app, mode):
    workspace = window.focus_workspace
    if mode == "restored":
        workspace.set_machine_status(status())
        workspace.panel.set_result(result(z_retention={
            "available": True, "restored": True, "reason": "Border datum restored.",
        }))
    elif mode == "recovery":
        workspace.set_machine_status(status(controller_state="READY_HOME_REQUIRED", jog_ready=False,
                                            coordinate_reference_ready=False))
        workspace.panel.set_result(result(requires_clearance=True, xy_recovery_available=True,
                                          ender={"ready": True, "generation": 4, "recovery_required": False}))
    window.resize(1280, 900)
    window.resizeDocks([window.layer_dock], [420], QtCore.Qt.Orientation.Horizontal)
    app.processEvents()
    assert workspace.isVisible()
    assert workspace.bed_view._active
    assert workspace.minimumSizeHint().width() <= 420
    assert workspace.width() <= window.machine_panel.width()
    window.inspector_tabs.select_panel("camera")
    app.processEvents()
    assert not workspace.bed_view._active
    window.inspector_tabs.select_panel("machine")
    app.processEvents()
    assert workspace.bed_view._active
