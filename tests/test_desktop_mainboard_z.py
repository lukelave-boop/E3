from __future__ import annotations

# Qt must select the headless platform before desktop module imports.
# ruff: noqa: E402, I001
import os
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.mainboard_z import (
    MainboardZCoordinator,
    MainboardZPanel,
    Z_STATUS_FRESH_SECONDS,
)


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def status(**changes):
    result = {
        "controller_state": "READY_MOTION", "controller_session_generation": 8,
        "controller_state_revision": 20, "connected": True, "armed": False,
        "allow_motion": True, "coordinate_reference_ready": True,
        "jog_ready": True, "protocol": "grbl", "z_probe": {"active": False},
        "job": {},
    }
    result.update(changes)
    return result


def result(**changes):
    payload = {
        "action": "status", "z_mm": 20.0, "z_known": True,
        "max_z_mm": 80.0, "hard_max_z_mm": 80.0, "min_z_mm": 20.0,
        "max_z_persistent": True, "firmware_z_limit_verified": False,
        "available": True, "fresh": True,
    }
    payload.update(changes)
    return payload


@pytest.fixture
def panel(app):
    widget = MainboardZPanel()
    widget.set_machine_status(status())
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_height_is_separate_reported_z_with_explicit_cap_and_confirmation(panel):
    assert panel.height.text() == "Z — mm"
    assert not panel.up.isEnabled()
    assert "unknown" in panel.active_maximum.text()
    panel.set_result(result())
    assert panel.height.text() == "Z 20.000 mm"
    assert "encoder" in panel.height.toolTip()
    assert panel.active_maximum.text() == "Active maximum: 80 mm"
    assert "not confirmed" in panel.firmware_note.text()
    assert not panel.up.isEnabled()
    calls = []
    panel.jogRequested.connect(calls.append)
    panel.confirm.setChecked(True)
    panel.up.click()
    assert calls == [1.0]
    assert not panel.down.isEnabled()
    panel.set_result(result(z_mm=80, firmware_z_limit_verified=True))
    assert not panel.up.isEnabled()
    assert panel.down.isEnabled()
    assert panel.firmware_note.text() == "Firmware ceiling: 80 mm confirmed"


def test_step_choices_bounded_and_button_handlers_recheck_age(panel):
    assert [panel.step.itemData(i) for i in range(panel.step.count())] == [.1, 1, 5]
    panel.set_result(result(z_mm=78))
    panel.confirm.setChecked(True)
    panel.step.setCurrentIndex(2)
    assert not panel.up.isEnabled()
    calls = []
    panel.jogRequested.connect(calls.append)
    panel.down.click()
    assert calls == [-5.0]
    panel._received_at = time.monotonic() - Z_STATUS_FRESH_SECONDS - .1
    panel._jog(-1)
    assert calls == [-5.0]
    assert panel.height.text() == "Z — mm"
    assert "last reported" in panel.active_maximum.text()


@pytest.mark.parametrize("state", [
    {"connected": False, "controller_state": "DISCONNECTED"},
    {"controller_state": "STOPPING"}, {"controller_state": "FAULTED"},
    {"controller_state": "JOB_RUNNING", "job": {"running": True}},
    {"armed": True}, {"z_probe": {"active": True}},
    {"controller_state": "RECONNECT_REQUIRED"},
    {"status_stale": True},
])
def test_untrusted_or_active_machine_invalidates_z_and_disables_controls(panel, state):
    panel.set_result(result(z_mm=30))
    panel.confirm.setChecked(True)
    panel.set_machine_status(status(**state))
    assert panel.height.text() == "Z — mm"
    assert not panel.up.isEnabled()
    assert not panel.down.isEnabled()
    assert not panel.apply.isEnabled()


def test_unknown_z_and_motion_disabled_do_not_grant_jog(panel):
    panel.set_result(result(z_known=False, z_mm=0))
    panel.confirm.setChecked(True)
    assert panel.height.text() == "Z unknown"
    assert not panel.up.isEnabled()
    panel.set_result(result(z_mm=30))
    panel.set_machine_status(status(allow_motion=False))
    assert not panel.up.isEnabled()
    panel.set_machine_status(status(controller_session_generation=9))
    assert panel.height.text() == "Z — mm"
    assert not panel.confirm.isChecked()


def test_editing_limit_is_not_active_until_saved_and_polls_preserve_edit(panel):
    panel.set_result(result(z_mm=30))
    assert panel.maximum.minimum() == 20
    assert panel.maximum.maximum() == 80
    calls = []
    panel.maximumRequested.connect(calls.append)
    panel.maximum.setValue(60)
    assert panel.active_maximum.text() == "Active maximum: 80 mm"
    panel.set_result(result(z_mm=30))
    assert panel.maximum.value() == 60
    panel.apply.click()
    assert calls == [60]
    panel.set_result(result(action="z_max", max_z_mm=60, z_mm=30))
    assert panel.active_maximum.text() == "Active maximum: 60 mm"
    assert not panel.apply.isEnabled()
    panel.maximum.setValue(65)
    panel.set_result(result(max_z_mm=60, max_z_persistent=False, z_mm=30))
    assert not panel.apply.isEnabled()
    assert "support update" in panel.apply.toolTip()


def test_clear_and_type_maximum_survives_live_poll_before_apply(panel, app):
    panel.set_result(result())
    editor = panel.maximum.lineEdit()
    editor.setFocus()
    panel.maximum.selectAll()
    QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
    assert panel.maximum.cleanText() == ""
    panel.set_result(result(z_mm=20.1))
    assert panel.maximum.cleanText() == ""
    QtTest.QTest.keyClicks(editor, "4")
    assert panel.maximum.cleanText() == "4"
    assert not panel.apply.isEnabled()
    panel.set_result(result(z_mm=20.2))
    QtTest.QTest.keyClicks(editor, "0")
    assert panel.maximum.cleanText() == "40"
    assert panel.maximum.value() == 80  # Draft has not been committed.
    assert panel.apply.isEnabled()
    assert panel.active_maximum.text() == "Active maximum: 80 mm"
    calls = []
    panel.maximumRequested.connect(calls.append)
    QtTest.QTest.mouseClick(panel.apply, QtCore.Qt.MouseButton.LeftButton)
    app.processEvents()
    assert calls == [40.0]


@pytest.mark.parametrize("draft", ["", "4", "81", "-1"])
def test_invalid_maximum_draft_cannot_be_applied(panel, draft):
    panel.set_result(result())
    editor = panel.maximum.lineEdit()
    editor.setFocus()
    panel.maximum.selectAll()
    QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
    QtTest.QTest.keyClicks(editor, draft)
    calls = []
    panel.maximumRequested.connect(calls.append)
    assert not panel.apply.isEnabled()
    panel._apply()
    assert calls == []
    assert panel.maximum.value() == 80


@pytest.mark.parametrize("draft", ["40", "4"])
def test_new_session_replaces_old_maximum_draft_without_focus_out_commit(panel, app, draft):
    panel.set_result(result())
    editor = panel.maximum.lineEdit()
    editor.setFocus()
    app.processEvents()
    assert editor.hasFocus()
    panel.maximum.selectAll()
    QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
    QtTest.QTest.keyClicks(editor, draft)
    assert panel.maximum.value() == 80
    assert panel.maximum.hasPendingEdit()
    requests = []
    panel.maximumRequested.connect(requests.append)
    panel.set_machine_status(status(controller_session_generation=9))
    app.processEvents()
    assert not panel.maximum.hasPendingEdit()
    assert not panel._maximum_edited
    assert panel.maximum.value() == 80
    panel.set_result(result(max_z_mm=60))
    assert panel.maximum.value() == 60
    assert not panel.apply.isEnabled()
    assert requests == []


@pytest.mark.parametrize("changes", [
    {"fresh": False}, {"available": False}, {"z_mm": float("nan")},
    {"max_z_mm": 81}, {"hard_max_z_mm": 90}, {"max_z_mm": None},
    {"z_known": 1},
])
def test_malformed_backend_status_rejected(panel, changes):
    with pytest.raises(ValueError, match="incomplete Z status"):
        panel.set_result(result(**changes))


class Machine:
    def __init__(self):
        self.snapshot = status()
        self.calls = []
        self.reply = result()
        self.generation = 1

    def status(self):
        return dict(self.snapshot)

    def operation_generation(self):
        return self.generation

    def ensure_connected(self):
        pass

    def request_stop(self, *, emergency):
        self.generation += 1

    @contextmanager
    def operation_scope(self, generation):
        yield

    def mainboard_control(self, action, *, value=None, confirmed=False):
        self.calls.append((action, value, confirmed))
        return dict(self.reply)


class Controller(QtCore.QObject):
    statusChanged = QtCore.Signal(dict)
    busyChanged = QtCore.Signal(bool)
    stopInitiated = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.machine = Machine()
        self.runtime = SimpleNamespace(context=SimpleNamespace(machine=self.machine))
        self.tasks = []
        self._shutdown_started = False

    def _run(self, callback, **options):
        self.tasks.append((callback, options))
        if options["show_busy"]:
            self.busyChanged.emit(True)

    def finish(self):
        callback, options = self.tasks.pop(0)
        try:
            value = callback()
        except Exception as exc:
            options["on_failure"](str(exc))
        else:
            options["on_success"](value)
        if options["show_busy"]:
            self.busyChanged.emit(False)
        options["on_finished"]()


@pytest.fixture
def monitor(panel):
    controller = Controller()
    coordinator = MainboardZCoordinator(panel, controller)
    coordinator._timer.stop()
    coordinator.set_status({"machine": controller.machine.status()})
    yield coordinator, controller
    coordinator.deleteLater()
    controller.deleteLater()


def test_poll_does_not_toggle_global_busy_and_queues_typed_click_once(monitor, panel):
    coordinator, controller = monitor
    coordinator.tick()
    assert len(controller.tasks) == 1
    assert controller.tasks[0][1]["show_busy"] is False
    coordinator.tick()
    assert len(controller.tasks) == 1
    controller.finish()
    panel.confirm.setChecked(True)
    coordinator._last_request = -100
    coordinator.tick()
    assert panel.up.isEnabled()  # A background read must not flicker this control.
    panel.up.click()
    assert coordinator._queued == ("z_jog", 1.0)
    assert panel.height.text() == "Z — mm"
    controller.finish()  # Old pre-move poll cannot restore a position.
    assert panel.height.text() == "Z — mm"
    assert len(controller.tasks) == 1
    controller.machine.reply = result(action="z_jog", z_mm=21)
    controller.finish()
    assert controller.machine.calls[-1] == ("z_jog", 1.0, True)
    assert panel.height.text() == "Z 21.000 mm"
    assert not coordinator._pending


def test_stop_cancels_queued_z_and_late_poll_does_not_restore_position(monitor, panel):
    coordinator, controller = monitor
    panel.set_result(result())
    panel.confirm.setChecked(True)
    coordinator.tick()
    panel.up.click()
    controller.stopInitiated.emit()
    controller.finish()
    assert controller.machine.calls == []
    assert coordinator._queued is None
    assert panel.height.text() == "Z — mm"
    assert not panel.confirm.isChecked()
    assert not coordinator._pending


def test_primary_session_change_rejects_request_before_backend(monitor, panel):
    coordinator, controller = monitor
    coordinator.tick()
    controller.machine.snapshot = status(controller_session_generation=9)
    controller.finish()
    assert not controller.machine.calls
    assert panel.height.text() == "Z — mm"
    assert "session changed" in panel.message.text()


def test_pi_missing_support_shows_inline_error_and_no_retry_storm(monitor, panel):
    coordinator, controller = monitor
    controller.machine.mainboard_control = None
    coordinator.tick()
    controller.finish()
    assert "matching Pi support" in panel.message.text()
    coordinator._last_request = -100
    coordinator.tick()
    assert not controller.tasks
    coordinator.refresh()
    assert len(controller.tasks) == 1
    controller.finish()


@pytest.mark.parametrize("changes", [
    {"z_probe": {"active": True}}, {"armed": True},
    {"controller_state": "JOB_RUNNING", "job": {"running": True}},
    {"controller_state": "DISCONNECTED", "connected": False},
])
def test_poll_skips_probe_job_armed_disconnected(monitor, changes):
    coordinator, controller = monitor
    coordinator.set_status({"machine": status(**changes)})
    coordinator.tick()
    assert not controller.tasks


def test_mutation_completion_survives_its_own_probe_active_status(monitor, panel):
    coordinator, controller = monitor
    panel.set_result(result())
    panel.confirm.setChecked(True)
    panel.up.click()
    coordinator.set_status({"machine": status(z_probe={"active": True})})
    controller.machine.reply = result(action="z_jog", z_mm=21)
    controller.finish()
    coordinator.set_status({"machine": status()})
    assert panel.height.text() == "Z 21.000 mm"
    assert panel.up.isEnabled()


def test_automatic_poll_pauses_for_hidden_controls_and_modal_dialog(monitor, panel, app):
    coordinator, controller = monitor
    panel.set_result(result())
    panel.hide()
    coordinator.tick()
    assert not controller.tasks
    assert not panel.fresh()
    panel.show()
    app.processEvents()
    dialog = QtWidgets.QDialog(panel)
    dialog.setModal(True)
    dialog.show()
    app.processEvents()
    try:
        assert QtWidgets.QApplication.activeModalWidget() is dialog
        coordinator.tick()
        assert not controller.tasks
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
    coordinator.tick()
    assert len(controller.tasks) == 1
    controller.finish()
    assert panel.fresh()


def test_real_worker_keeps_qt_responsive_during_read(app, panel):
    entered = threading.Event()
    release = threading.Event()
    machine = Machine()
    worker_threads = []

    def read(action, **kwargs):
        worker_threads.append(threading.get_ident())
        entered.set()
        release.wait(3)
        return result()

    machine.mainboard_control = read
    runtime = SimpleNamespace(context=SimpleNamespace(machine=machine), running=True)
    controller = DesktopController(runtime)
    coordinator = MainboardZCoordinator(panel, controller)
    coordinator._timer.stop()
    coordinator.set_status({"machine": status()})
    try:
        coordinator.tick()
        assert entered.wait(1)
        markers = []
        QtCore.QTimer.singleShot(0, lambda: markers.append("responsive"))
        app.processEvents()
        assert markers == ["responsive"]
        assert worker_threads != [threading.get_ident()]
        assert controller._active_tasks == 0
    finally:
        release.set()
        deadline = time.monotonic() + 3
        while controller.has_active_tasks and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        coordinator.deleteLater()
        controller.deleteLater()
    assert panel.height.text() == "Z 20.000 mm"


@pytest.mark.parametrize("interruption", [None, "stop", "session_change", "disconnect"])
def test_foreground_controller_worker_waits_for_read_and_rechecks_authority(
    app, panel, interruption,
):
    read_entered = threading.Event()
    release_read = threading.Event()
    foreground_entered = threading.Event()
    disconnect_entered = threading.Event()
    machine = Machine()
    order = []

    def read(action, **kwargs):
        order.append("read started")
        read_entered.set()
        release_read.wait(3)
        order.append("read finished")
        return result()

    def foreground():
        assert release_read.is_set(), "foreground raced an active Pi read"
        foreground_entered.set()
        order.append("foreground")

    def disconnect():
        machine.generation += 1
        disconnect_entered.set()

    machine.mainboard_control = read
    machine.disconnect = disconnect
    runtime = SimpleNamespace(
        context=SimpleNamespace(machine=machine), running=True,
        status=lambda: {"machine": machine.status()},
    )
    controller = DesktopController(runtime)
    coordinator = MainboardZCoordinator(panel, controller)
    coordinator._timer.stop()
    coordinator.set_status({"machine": status()})
    errors = []
    controller.errorOccurred.connect(errors.append)
    try:
        coordinator.tick()
        assert read_entered.wait(1)
        controller._run(foreground, requires_controller=True, label="Jog")
        assert not foreground_entered.wait(.1)
        markers = []
        QtCore.QTimer.singleShot(0, lambda: markers.append("responsive"))
        app.processEvents()
        assert markers == ["responsive"]
        if interruption == "stop":
            started = time.monotonic()
            controller.emergency_stop()
            assert time.monotonic() - started < .2
            assert machine.generation == 2
        elif interruption == "session_change":
            machine.snapshot = status(controller_session_generation=9)
        elif interruption == "disconnect":
            controller.disconnect_machine()
            # Cleanup can execute while the ordinary read still owns the gate.
            assert disconnect_entered.wait(1)
        release_read.set()
        deadline = time.monotonic() + 3
        while controller.has_active_tasks and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert not controller.has_active_tasks
        if interruption is None:
            assert foreground_entered.is_set()
            assert order == ["read started", "read finished", "foreground"]
            assert errors == []
        else:
            assert not foreground_entered.is_set()
    finally:
        release_read.set()
        controller.thread_pool.waitForDone(3000)
        app.processEvents()
        coordinator.deleteLater()
        controller.deleteLater()


@pytest.mark.parametrize("stop", [False, True])
def test_machine_setup_worker_shares_z_read_gate_and_stop_cancels_waiter(
    app, panel, tmp_path, monkeypatch, stop,
):
    from laser_aligner.desktop.machine_setup import MachineSetupDialog
    from tests.test_desktop_machine_setup import _runtime, _set_controller_state

    runtime = _runtime(tmp_path)
    _set_controller_state(monkeypatch, runtime, "READY_MOTION")
    read_entered, release_read = threading.Event(), threading.Event()
    setup_entered = threading.Event()
    machine = runtime.context.machine
    epoch = [1]
    monkeypatch.setattr(machine, "operation_generation", lambda: epoch[0])
    monkeypatch.setattr(machine, "ensure_connected", lambda: None)
    monkeypatch.setattr(machine, "request_stop", lambda **kwargs: epoch.__setitem__(0, epoch[0] + 1))

    def read(action, **kwargs):
        read_entered.set()
        release_read.wait(3)
        return result()

    monkeypatch.setattr(machine, "mainboard_control", read)
    controller = DesktopController(runtime)
    coordinator = MainboardZCoordinator(panel, controller)
    coordinator._timer.stop()
    coordinator.set_status({"machine": machine.status()})
    dialog = MachineSetupDialog(runtime, controller_operation_scope=controller.controller_worker_scope)
    try:
        coordinator.tick()
        assert read_entered.wait(1)
        assert dialog._start_operation(
            "Test setup Home", setup_entered.set, lambda _: None,
            requires_controller=True,
        )
        assert not setup_entered.wait(.1)
        markers = []
        QtCore.QTimer.singleShot(0, lambda: markers.append(True))
        app.processEvents()
        assert markers == [True]
        if stop:
            started = time.monotonic()
            dialog.request_software_stop()
            assert time.monotonic() - started < .2
        release_read.set()
        deadline = time.monotonic() + 3
        while (controller.has_active_tasks or dialog.operation_busy) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert not dialog.operation_busy
        assert setup_entered.is_set() is not stop
    finally:
        release_read.set()
        controller.thread_pool.waitForDone(3000)
        app.processEvents()
        dialog.close()
        dialog.deleteLater()
        coordinator.deleteLater()
        controller.deleteLater()
        runtime.stop()
