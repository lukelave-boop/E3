"""Embedded Z controls retain explicit authority across visibility changes."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop import focus_bed_view
from laser_aligner.desktop.laser_focus import LaserFocusWorkspace
from tests.test_desktop_focus_bed_view import Worker, publish, select
from tests.test_desktop_laser_focus import (
    FakeController,
    camera_result,
    camera_target,
    confirm,
    preview,
    result,
    retained_result,
    status,
)


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def workspace_factory(app, monkeypatch):
    monkeypatch.setattr(focus_bed_view, "_MonitorThread", Worker)
    created = []

    def create(*, calibration_mode=False):
        controller = FakeController()
        controller.runtime.context.camera = SimpleNamespace(monitor_frames=lambda **kwargs: iter(()))
        controller.runtime.context.focus_probe_target = lambda *args, **kwargs: camera_target()
        workspace = LaserFocusWorkspace(controller, calibration_mode=calibration_mode)
        created.append(workspace)
        workspace.resize(1100 if calibration_mode else 420, 950)
        workspace.show()
        app.processEvents()
        workspace.coordinator._timer.stop()
        workspace._camera_timer.stop()
        workspace.set_machine_status(status())
        workspace.panel.set_result(result())
        confirm(workspace.panel)
        return workspace, controller

    yield create
    for workspace in created:
        workspace.shutdown(force=True)
        workspace.close()
        workspace.deleteLater()
    app.processEvents()


def pause(workspace, method):
    if method == "hide":
        workspace.hide()
    else:
        workspace.set_suspended(True)
    assert workspace.coordinator._suspended
    assert not workspace.isEnabled()
    assert not workspace.bed_view._active


def resume(workspace, method):
    if method == "hide":
        workspace.show()
    else:
        workspace.set_suspended(False)
    workspace.coordinator._timer.stop()
    workspace._camera_timer.stop()
    assert not workspace.coordinator._suspended
    assert workspace.isEnabled()
    assert workspace.bed_view._active


@pytest.mark.parametrize("calibration_mode", [False, True])
@pytest.mark.parametrize("method", ["hide", "suspend"])
def test_late_read_after_pause_cannot_restore_local_authority(workspace_factory, calibration_mode, method):
    workspace, controller = workspace_factory(calibration_mode=calibration_mode)
    panel, coordinator = workspace.panel, workspace.coordinator
    coordinator.tick()
    operation, callbacks = controller.work.pop()
    late = operation()
    late.update(action="preview", preview=preview(), surface={**result()["surface"], "id": "late-surface"})
    pause(workspace, method)
    callbacks["on_success"](late)
    callbacks["on_finished"]()
    assert not panel.fresh()
    assert panel._preview_id is None
    assert panel._result["surface"]["id"] != "late-surface"
    assert not panel.measure.isEnabled() and not panel.move.isEnabled()
    resume(workspace, method)
    assert not panel.fresh() and not panel.measure.isEnabled()
    coordinator.tick()
    controller.complete()
    assert panel.fresh()
    assert panel._preview_id is None
    assert panel.measure.isEnabled()
    assert [action for action, _ in controller.machine.calls] == ["status", "status"]


@pytest.mark.parametrize("method", ["hide", "suspend"])
def test_pause_cancels_action_queued_behind_read_without_replay(workspace_factory, method):
    workspace, controller = workspace_factory()
    panel, coordinator = workspace.panel, workspace.coordinator
    coordinator.tick()
    panel.measure.click()
    assert coordinator._queued is not None and len(controller.work) == 1
    pause(workspace, method)
    assert coordinator._queued is None
    controller.complete()
    assert not controller.work and controller.machine.calls == []
    resume(workspace, method)
    coordinator.tick()
    controller.complete()
    assert [action for action, _ in controller.machine.calls] == ["status"]
    panel.measure.click()
    controller.complete()
    assert [action for action, _ in controller.machine.calls] == ["status", "measure"]


@pytest.mark.parametrize("action", ["home_reference", "home_retained", "reference"])
@pytest.mark.parametrize("method", ["hide", "suspend"])
@pytest.mark.parametrize("timing", ["before_worker", "during_operation"])
def test_pausing_dispatched_reference_does_not_cancel_authorized_operation(
    workspace_factory, action, method, timing,
):
    workspace, controller = workspace_factory()
    panel, coordinator = workspace.panel, workspace.coordinator
    payload = result(
        reference_ready=False, surface=None,
        ender={"ready": True, "generation": 4, "recovery_required": False},
    )
    if action == "home_retained":
        payload = retained_result(
            surface=None, ender={"ready": True, "generation": 4, "recovery_required": False},
        )
    elif action == "reference":
        payload.update(requires_clearance=True, xy_recovery_pending_reference=True)
    controller.machine.payload = payload
    panel.set_result(payload)
    confirm(panel)
    events = []
    focus_control = controller.machine.focus_control

    def home():
        events.append("home")
        if timing == "during_operation":
            pause(workspace, method)

    def focus(action_name, **arguments):
        if action_name == "reference":
            events.append("reference")
            if action == "reference" and timing == "during_operation":
                pause(workspace, method)
        return focus_control(action_name, **arguments)

    controller.machine.prepare_photo_position = home
    controller.machine.focus_control = focus
    assert panel._home_action() == action
    panel.reference.click()
    assert coordinator._mutation and len(controller.work) == 1
    if timing == "before_worker":
        pause(workspace, method)
    controller.complete()
    assert events == {
        "home_reference": ["home", "reference"],
        "home_retained": ["home"],
        "reference": ["reference"],
    }[action]
    assert not coordinator._error and not coordinator._mutation
    assert not coordinator._closed and coordinator._suspended
    assert not panel.reference.isEnabled()
    assert controller.stop_calls == 0 and not controller.work
    if action == "home_retained":
        assert panel._result["reference_ready"] is True
        assert "Saved Z reference retained" in panel.message.text()


@pytest.mark.parametrize("calibration_mode", [False, True])
@pytest.mark.parametrize("method", ["hide", "suspend"])
def test_pause_clears_camera_selection_and_resume_requires_new_point(
    workspace_factory, calibration_mode, method,
):
    workspace, controller = workspace_factory(calibration_mode=calibration_mode)
    panel = workspace.panel
    panel.set_result(camera_result(surface=None))
    publish(workspace.bed_view)
    workspace._camera_tick()
    panel.position_probe.click()
    select(workspace.bed_view)
    old_selection = workspace.bed_view.selection_snapshot()
    assert old_selection is not None and panel.can_move_probe()
    pause(workspace, method)
    assert panel._camera_target is None
    assert workspace.bed_view.selection_snapshot() is None
    resume(workspace, method)
    panel.set_result(camera_result(surface=None))
    publish(workspace.bed_view)
    workspace._camera_tick()
    workspace.bed_view.pointSelected.emit(old_selection)
    workspace._move_camera_probe()
    panel.position_probe.click()
    workspace._move_camera_probe()
    assert panel._camera_target is None
    assert not controller.work and controller.machine.calls == []
    select(workspace.bed_view)
    assert panel.can_move_probe()
    panel.position_probe.click()
    controller.complete()
    assert [action for action, _ in controller.machine.calls] == ["position_probe"]


@pytest.mark.parametrize("calibration_mode", [False, True])
@pytest.mark.parametrize("method", ["hide", "suspend"])
def test_pause_rejects_dispatched_camera_move_after_marker_was_cleared(
    workspace_factory, calibration_mode, method,
):
    workspace, controller = workspace_factory(calibration_mode=calibration_mode)
    workspace.panel.set_result(camera_result(surface=None))
    publish(workspace.bed_view)
    workspace._camera_tick()
    workspace.panel.position_probe.click()
    select(workspace.bed_view)
    workspace.panel.position_probe.click()
    assert len(controller.work) == 1 and workspace.coordinator._mutation
    assert workspace.bed_view.selection_snapshot() is None
    pause(workspace, method)
    controller.complete()
    assert controller.machine.calls == []
    assert not workspace.panel.fresh()


def test_unrelated_modal_pauses_daily_camera_and_controller_polling(workspace_factory, app):
    workspace, controller = workspace_factory()
    coordinator = workspace.coordinator
    modal = QtWidgets.QDialog()
    modal.setWindowTitle("Project settings")
    modal.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    try:
        modal.show()
        app.processEvents()
        assert QtWidgets.QApplication.activeModalWidget() is modal
        workspace._camera_tick()
        assert coordinator._suspended and not workspace.isEnabled()
        assert not workspace.bed_view._active
        coordinator.tick()
        coordinator.refresh()
        coordinator.request("status", {})
        coordinator.request("clear", {})
        assert not controller.work and controller.machine.calls == []
        modal.close()
        app.processEvents()
        workspace._camera_tick()
        coordinator._timer.stop()
        workspace._camera_timer.stop()
        assert not coordinator._suspended and workspace.isEnabled()
        coordinator.tick()
        controller.complete()
        assert [action for action, _ in controller.machine.calls] == ["status"]
    finally:
        modal.close()
        modal.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("calibration_mode", [False, True])
def test_closed_workspace_never_restarts_observers_when_shown(workspace_factory, app, calibration_mode):
    workspace, controller = workspace_factory(calibration_mode=calibration_mode)
    assert workspace.shutdown()
    workspace.hide()
    workspace.show()
    workspace.set_suspended(False)
    workspace._camera_tick()
    workspace.coordinator.tick()
    app.processEvents()
    assert workspace.coordinator._closed
    assert not workspace.coordinator._timer.isActive()
    assert not workspace._camera_timer.isActive()
    assert not workspace.panel._display_timer.isActive()
    assert not workspace.bed_view._active and workspace.bed_view._worker is None
    assert controller.machine.calls == [] and not controller.work
