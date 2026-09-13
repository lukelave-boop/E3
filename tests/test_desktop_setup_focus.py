from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.machine_setup import MachineSetupDialog
from tests.test_desktop_laser_focus import result
from tests.test_desktop_machine_setup import _runtime, _set_controller_state


@pytest.fixture
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _wait(app, condition):
    deadline = time.monotonic() + 4.0
    while not condition():
        app.processEvents()
        assert time.monotonic() < deadline, "Timed out waiting for focus/setup cleanup"
        time.sleep(0.005)


@pytest.fixture
def setup(app, tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _set_controller_state(monkeypatch, runtime, "READY_MOTION")
    controller = DesktopController(runtime)
    dialog = MachineSetupDialog(runtime, controller=controller, navigation_only=True)
    focus = dialog.focus_workspace
    focus.coordinator._timer.stop()
    focus.set_machine_status(runtime.context.machine.status())
    focus.panel.set_result(result())
    yield dialog, controller
    controller.thread_pool.waitForDone(4000)
    app.processEvents()
    dialog.begin_shutdown()
    dialog.deleteLater()
    controller.deleteLater()
    app.processEvents()
    runtime.stop()


def test_setup_embeds_calibration_on_tab_seven_without_another_owner(setup):
    dialog, controller = setup
    focus = dialog.focus_workspace
    assert dialog.tabs.widget(6) is focus
    assert dialog.tabs.tabText(6) == "7 · Z / laser focus"
    assert focus.coordinator.controller is controller
    assert focus.panel.offset_editor is not None
    assert focus.panel.teach is not None
    assert not hasattr(dialog, "laser_focus_button")
    assert dialog._controller_operation_scope == controller.controller_worker_scope


def test_focus_camera_view_follows_setup_tab_visibility(setup, app, monkeypatch):
    dialog, _controller = setup
    events = []
    focus = dialog.focus_workspace
    monkeypatch.setattr(focus.bed_view, "begin", lambda: events.append("begin"))
    monkeypatch.setattr(focus.bed_view, "end", lambda: events.append("end"))
    dialog.tabs.setCurrentIndex(0)
    dialog.show()
    app.processEvents()
    assert "begin" not in events
    dialog.tabs.setCurrentIndex(6)
    app.processEvents()
    assert events[-1] == "begin"
    dialog.tabs.setCurrentIndex(0)
    app.processEvents()
    assert events[-1] == "end"


def test_setup_shutdown_can_cancel_a_focus_mutation(setup):
    dialog, _controller = setup
    focus = dialog.focus_workspace
    focus.coordinator._mutation = True
    assert not dialog.shutdown_focus_workspace()
    assert not focus.coordinator._closed
    dialog.begin_shutdown()
    assert focus.coordinator._closed
    assert not focus._camera_timer.isActive()


def test_controller_free_setup_explains_why_focus_is_unavailable(app, tmp_path):
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime, navigation_only=True)
    try:
        assert dialog.focus_workspace is None
        labels = dialog.tabs.widget(6).findChildren(QtWidgets.QLabel)
        assert any("running desktop controller" in label.text() for label in labels)
        assert not dialog.tabs.widget(6).findChildren(QtWidgets.QPushButton)
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        runtime.stop()


def test_setup_worker_blocks_focus_until_its_cleanup_finishes(setup, app, monkeypatch):
    dialog, controller = setup
    focus = dialog.focus_workspace
    started, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(controller.runtime.context.machine, "focus_control", lambda *a, **k: calls.append(a))

    def operation():
        started.set()
        assert release.wait(4.0)

    try:
        assert dialog._start_operation("Setup test", operation, lambda _: None)
        assert started.wait(1.0)
        assert dialog.operation_busy
        assert focus.panel._busy
        focus.coordinator.set_busy(False)
        focus.coordinator.request("clear", {})
        assert focus.panel._busy
        assert calls == []
        assert not dialog._start_operation("Second test", lambda: None, lambda _: None)
        dialog.reject()
        assert not focus.coordinator._closed
    finally:
        release.set()
        _wait(app, lambda: not dialog.operation_busy)
    assert not focus.panel._busy
    assert dialog.close_button.isEnabled()
    assert dialog._start_operation("Next setup test", lambda: None, lambda _: None)
    _wait(app, lambda: not dialog.operation_busy)


def test_focus_worker_blocks_setup_and_stop_invalidates_its_authority(setup, app, monkeypatch):
    dialog, controller = setup
    focus = dialog.focus_workspace
    started, release = threading.Event(), threading.Event()
    stopped = []

    def operation(action, **kwargs):
        started.set()
        assert release.wait(4.0)
        return result()

    monkeypatch.setattr(controller.runtime.context.machine, "focus_control", operation)
    monkeypatch.setattr(controller.runtime.context.machine, "request_stop", lambda **kwargs: stopped.append(kwargs))
    try:
        focus.coordinator.request("clear", {})
        assert started.wait(1.0)
        assert dialog.operation_busy
        assert not dialog.close_button.isEnabled()
        assert not dialog.tabs.isTabEnabled(0)
        assert dialog.tabs.isTabEnabled(6)
        assert not dialog._start_operation("Rejected setup test", lambda: None, lambda _: None)
        dialog.done(QtWidgets.QDialog.DialogCode.Rejected)
        assert not focus.coordinator._closed
        dialog.request_software_stop()
        assert stopped == [{"emergency": True}]
        assert "STOP requested" in focus.panel.message.text()
    finally:
        release.set()
        _wait(app, lambda: not dialog.operation_busy)
        app.processEvents()
    assert dialog.close_button.isEnabled()
    assert dialog.tabs.isTabEnabled(0)
    assert focus.panel._result.get("surface") is None


@pytest.mark.parametrize("action", ["reject", "accept", "close", "done", "begin_shutdown"])
def test_every_setup_exit_cleans_up_focus_observers(setup, app, action):
    dialog, _controller = setup
    focus = dialog.focus_workspace
    if action == "done":
        dialog.done(QtWidgets.QDialog.DialogCode.Rejected)
    else:
        getattr(dialog, action)()
    app.processEvents()
    assert focus.coordinator._closed
    assert not focus.coordinator._timer.isActive()
    assert not focus._camera_timer.isActive()
    assert not focus.panel._display_timer.isActive()
    assert dialog.shutdown_focus_workspace()
