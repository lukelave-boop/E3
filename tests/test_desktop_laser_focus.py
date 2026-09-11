from __future__ import annotations

import copy
import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop.laser_focus import (
    FRESH_SECONDS,
    LaserFocusCoordinator,
    LaserFocusDialog,
    LaserFocusPanel,
)


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def status(**changes):
    payload = {
        "controller_state": "READY_MOTION", "controller_session_generation": 8,
        "controller_state_revision": 20, "connected": True, "armed": False,
        "allow_motion": True, "coordinate_reference_ready": True,
        "jog_ready": True, "protocol": "grbl", "z_probe": {"active": False}, "job": {},
    }
    payload.update(changes)
    return payload


def result(**changes):
    payload = {
        "action": "status", "available": True, "reference_ready": True,
        "current_readback": {"z_mm": 30.0, "z_known": True, "fresh": True},
        "surface": {"id": "surface-1", "contact_z_mm": 6.0, "elevation_mm": 6.1,
                    "carriage_xy_mm": [75.0, 143.0], "measured_at": 100.0},
        "calibration": {"id": "calibration-1", "focus_offset_mm": 10.0, "gauge_mm": 7,
                        "taught_z_mm": 16.0, "taught_contact_z_mm": 6.0, "taught_at": 100.0},
        "calibration_compatible": True, "calibration_persistent": True,
        "preview": None, "max_z_mm": 60.0, "clearance_z_mm": 30.0,
        "contact_min_mm": -2.0, "contact_max_mm": 15.0, "requires_clearance": False,
        "firmware_geometry": {"probe_z_mm": -2.0, "retract_mm": 2, "min_mm": -2,
                              "max_mm": 65, "ceiling_mm": 80},
    }
    payload.update(changes)
    return payload


def preview(**changes):
    payload = {"id": "preview-1", "measurement_id": "surface-1", "gap_mm": 7.0,
               "target_z_mm": 16.0, "current_z_mm": 30.0, "clearance_z_mm": 30.0}
    payload.update(changes)
    return payload


@pytest.fixture
def panel(app):
    widget = LaserFocusPanel()
    widget._status = status()
    widget.set_result(result())
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def confirm(panel):
    panel.path_clear.setChecked(True)
    panel.flat_patch.setChecked(True)


def test_focus_controls_present_signed_elevation_separate_from_thickness(panel):
    assert panel.height.text() == "Z 30.000 mm"
    assert "encoder" in panel.height.toolTip()
    assert "+6.100 mm above border" in panel.surface.text()
    assert "60 mm" in panel.maximum.text()
    assert panel.clearance.value() == 30
    assert [panel.gap.itemData(i) for i in range(panel.gap.count())] == [7, 5, 3]
    assert [panel.z_step.itemData(i) for i in range(panel.z_step.count())] == [.1, .5, 1]
    assert "−2 to 15" in panel.range_note.text()
    panel.set_result(result(surface={**result()["surface"], "elevation_mm": -.25}))
    assert "-0.250" in panel.surface.text()


def test_actions_are_explicit_and_teach_always_uses_7mm_not_selected_preview_gap(panel):
    calls = []
    panel.actionRequested.connect(lambda action, arguments: calls.append((action, arguments)))
    confirm(panel)
    panel.gap.setCurrentIndex(2)
    panel.clearance.setValue(40)
    assert calls == []
    assert not panel.path_clear.isChecked()
    panel.path_clear.setChecked(True)
    assert not panel.teach.isEnabled()
    panel.gauge.setChecked(True)
    panel.teach.click()
    assert calls == [("teach", {"confirmed": True, "clearance_z_mm": 40.0,
                                "gap_mm": 7.0, "measurement_id": "surface-1"})]
    panel.up.click()
    assert calls[-1][0] == "jog"
    assert calls[-1][1]["value"] == .1
    assert calls[-1][1]["measurement_id"] == "surface-1"


def test_preview_must_be_explicit_current_and_gauge_removed_before_move(panel):
    confirm(panel)
    panel.set_result(result(preview=preview()))  # Status cannot issue local movement authority.
    panel.gauge_removed.setChecked(True)
    assert not panel.move.isEnabled()
    panel.set_result(result(action="preview", preview=preview()))
    assert panel.move.isEnabled()
    assert "16.000 mm" in panel.target.text()
    calls = []
    panel.actionRequested.connect(lambda a, args: calls.append((a, args)))
    panel.move.click()
    assert calls == [("move", {"confirmed": True, "clearance_z_mm": 30.0,
                               "gap_mm": 7.0, "preview_id": "preview-1"})]
    assert not panel.gauge_removed.isChecked()


@pytest.mark.parametrize("changed", ["gap", "clearance", "surface", "age", "preview_id", "position", "limit"])
def test_edited_or_stale_preview_cannot_authorize_move(panel, changed):
    confirm(panel)
    panel.set_result(result(action="preview", preview=preview()))
    panel.gauge_removed.setChecked(True)
    assert panel.move.isEnabled()
    if changed == "gap":
        panel.gap.setCurrentIndex(1)
    elif changed == "clearance":
        panel.clearance.setValue(40)
    elif changed == "surface":
        panel.set_result(result(surface={**result()["surface"], "id": "surface-2"}, preview=preview()))
    elif changed == "preview_id":
        panel.set_result(result(preview=preview(id="replacement")))
    elif changed == "position":
        panel.set_result(result(preview=preview(),
                                current_readback={"z_mm": 31.0, "z_known": True, "fresh": True}))
    elif changed == "limit":
        panel.set_result(result(preview=preview(target_z_mm=70)))
    else:
        panel._received_at = time.monotonic() - FRESH_SECONDS - 1
    calls = []
    panel.actionRequested.connect(lambda *args: calls.append(args))
    panel.request("move")
    assert calls == []
    assert not panel.move.isEnabled()


def test_clearance_numeric_draft_survives_poll_and_enter_cannot_move(panel, app):
    calls = []
    panel.actionRequested.connect(lambda *args: calls.append(args))
    panel.homeRequested.connect(lambda: calls.append("home"))
    confirm(panel)
    editor = panel.clearance.lineEdit()
    editor.setFocus()
    panel.clearance.selectAll()
    QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
    QtTest.QTest.keyClicks(editor, "4")
    panel.set_result(result())
    assert panel.clearance.cleanText() == "4"
    assert not panel.measure.isEnabled()
    QtTest.QTest.keyClicks(editor, "0")
    QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Return)
    app.processEvents()
    assert panel.clearance.value() == 40
    assert not panel.path_clear.isChecked()
    panel.path_clear.setChecked(True)
    assert panel.measure.isEnabled()
    assert calls == []
    panel.clearance.setValue(65)
    assert not panel.measure.isEnabled()  # Above actual machine maximum.


def test_contact_floor_unknown_z_calibration_and_clearance_gates(panel):
    confirm(panel)
    panel.set_result(result(current_readback={"z_mm": 8.0, "z_known": True, "fresh": True},
                            requires_clearance=True))
    assert not panel.down.isEnabled()  # Contact6 minus probe offset-2 gives8.
    assert panel.up.isEnabled()
    assert panel.return_clearance.isEnabled()
    assert not any(button.isEnabled() for button in panel.xy_buttons)
    assert not panel.home.isEnabled()
    panel.set_result(result(calibration_compatible=False))
    assert not panel.preview.isEnabled()
    assert "re-teach" in panel.calibration.text()
    panel.set_result(result(calibration_persistent=False))
    panel.gauge.setChecked(True)
    assert not panel.teach.isEnabled()
    panel.set_result(result(current_readback={"z_mm": 0.0, "z_known": False, "fresh": True}))
    assert not panel.down.isEnabled()
    assert not panel.return_clearance.isEnabled()
    assert not panel.measure.isEnabled()
    assert not panel.preview.isEnabled()
    assert not any(button.isEnabled() for button in panel.xy_buttons)
    assert "unknown" in panel.height.text()


def test_return_to_clearance_survives_lost_reference_and_retains_prior_minimum(panel):
    confirm(panel)
    panel.set_result(result(reference_ready=False, surface=None, requires_clearance=True,
                            return_clearance_mm=40,
                            current_readback={"z_mm": 16.0, "z_known": True, "fresh": True}))
    assert panel.clearance.value() == 40
    panel.path_clear.setChecked(True)
    assert panel.return_clearance.isEnabled()
    assert not panel.home.isEnabled()
    assert not any(button.isEnabled() for button in panel.xy_buttons)
    panel.clearance.setValue(30)
    assert not panel.return_clearance.isEnabled()
    assert "Return requires Z ≥ 40 mm" in panel.range_note.text()
    panel.clearance.setValue(40)
    panel.path_clear.setChecked(True)
    calls = []
    panel.actionRequested.connect(lambda action, args: calls.append((action, args)))
    panel.return_clearance.click()
    assert calls[-1][0] == "clearance"
    assert calls[-1][1]["clearance_z_mm"] == 40


class FakeMachine:
    def __init__(self):
        self.snapshot = status()
        self.payload = result()
        self.calls = []
        self.error = None

    def status(self):
        return dict(self.snapshot)

    def focus_control(self, action, **arguments):
        self.calls.append((action, arguments))
        if self.error:
            raise RuntimeError(self.error)
        response = copy.deepcopy(self.payload)
        response["action"] = action
        return response


class FakeController(QtCore.QObject):
    statusChanged = QtCore.Signal(dict)
    busyChanged = QtCore.Signal(bool)
    stopInitiated = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.machine = FakeMachine()
        self.runtime = SimpleNamespace(context=SimpleNamespace(machine=self.machine))
        self.work = []
        self.xy_calls = []
        self.home_calls = 0
        self.stop_calls = 0

    def _run(self, operation, **kwargs):
        self.work.append((operation, kwargs))
        if kwargs["show_busy"]:
            self.busyChanged.emit(True)

    def complete(self):
        operation, kwargs = self.work.pop(0)
        try:
            kwargs["on_success"](operation())
        except Exception as exc:
            kwargs["on_failure"](str(exc))
        finally:
            if kwargs["show_busy"]:
                self.busyChanged.emit(False)
            kwargs["on_finished"]()

    def jog(self, dx, dy, feed):
        self.xy_calls.append((dx, dy, feed))
        self.busyChanged.emit(True)

    def park_at_camera_pose(self):
        self.home_calls += 1
        self.busyChanged.emit(True)

    def emergency_stop(self):
        self.stop_calls += 1
        self.stopInitiated.emit()


@pytest.fixture
def coordinator(panel):
    controller = FakeController()
    item = LaserFocusCoordinator(panel, controller)
    item._timer.stop()
    item.set_status(status())
    panel.set_result(result())
    yield item, controller
    item.close()


def test_async_status_does_not_mark_app_busy_and_mutation_carries_measurement(coordinator):
    item, controller = coordinator
    item.tick()
    assert len(controller.work) == 1 and controller.machine.calls == []
    assert controller.work[0][1]["show_busy"] is False
    assert controller.work[0][1]["serialize_controller"] is True
    controller.complete()
    confirm(item.panel)
    item.panel.up.click()
    assert item._mutation and not item.panel.up.isEnabled()
    controller.complete()
    assert controller.machine.calls[-1] == ("jog", {"confirmed": True,
        "clearance_z_mm": 30.0, "gap_mm": 7.0, "measurement_id": "surface-1", "value": .1})
    assert item.panel.fresh()


def test_click_while_polling_runs_after_read_but_stop_cancels_queue(coordinator):
    item, controller = coordinator
    confirm(item.panel)
    item.tick()
    item.panel.up.click()
    assert item._queued is not None
    controller.complete()
    assert len(controller.work) == 1
    controller.complete()
    assert [action for action, _ in controller.machine.calls] == ["jog"]
    item._last_request = -100
    item.tick()
    item.panel.up.click()
    controller.emergency_stop()
    controller.complete()
    assert controller.work == []
    assert not item.panel.fresh()
    assert "STOP" in item.panel.message.text()


@pytest.mark.parametrize("change", ["stop", "session", "disconnect", "job", "close"])
def test_late_success_cannot_restore_authority_after_machine_change(coordinator, change):
    item, controller = coordinator
    item.tick()
    operation, kwargs = controller.work.pop()
    late = operation()
    late.update(action="preview", preview=preview())
    if change == "stop":
        item.stopped()
    elif change == "close":
        item.close()
    else:
        controller.machine.snapshot = status(**{
            "session": {"controller_session_generation": 9},
            "disconnect": {"controller_state": "DISCONNECTED", "connected": False},
            "job": {"controller_state": "JOB_RUNNING", "job": {"running": True}},
        }[change])
        item.set_status(controller.machine.snapshot)
    kwargs["on_success"](late)
    kwargs["on_finished"]()
    assert item.panel._preview_id is None
    if change != "close":
        assert not item.panel.fresh()


def test_edited_gap_invalidates_inflight_preview(coordinator):
    item, controller = coordinator
    controller.machine.payload = result(preview=preview())
    item.panel.preview.click()
    item.panel.gap.setCurrentIndex(1)
    controller.complete()
    assert item.panel._preview_id is None
    assert not item.panel.move.isEnabled()


def test_old_firmware_failure_is_inline_latched_until_refresh(coordinator):
    item, controller = coordinator
    controller.machine.error = "Install the E3 surface-height V2 firmware before using laser focus"
    item.tick()
    controller.complete()
    assert "V2 firmware" in item.panel.message.text()
    assert not item.panel.measure.isEnabled()
    item._last_request = -100
    item.tick()
    assert controller.work == []
    item.refresh()
    assert len(controller.work) == 1
    controller.complete()


def test_modal_dialog_preserves_stop_during_work_and_refuses_silent_close(app):
    controller = FakeController()
    dialog = LaserFocusDialog(controller)
    dialog.set_machine_status(status())
    dialog.coordinator._timer.stop()
    dialog.panel.set_result(result())
    confirm(dialog.panel)
    dialog.show()
    app.processEvents()
    dialog.panel.up.click()
    assert dialog.stop.isEnabled()
    dialog.reject()
    assert dialog.isVisible()
    dialog.stop.click()
    assert controller.stop_calls == 1
    controller.complete()
    dialog.reject()
    assert not dialog.isVisible()
    assert dialog.coordinator._closed
    dialog.deleteLater()
    app.processEvents()


def test_machine_and_setup_open_same_focus_dialog_without_hardware(app, tmp_path, monkeypatch):
    from laser_aligner.desktop.machine_setup import MachineSetupDialog
    from tests.test_desktop_job_async import _dispose, _window

    opened = []

    def focus_exec(dialog):
        dialog.coordinator._timer.stop()
        opened.append(dialog.parentWidget())
        assert dialog.windowTitle() == "Surface / laser focus"
        assert dialog.panel.clearance.value() == 30
        return 0

    def setup_exec(dialog):
        assert dialog.tabs.tabText(6) == "7 · Z / laser focus"
        assert not hasattr(dialog, "z_probe_panel")
        dialog.laser_focus_button.click()
        assert opened[-1] is dialog
        return 0

    monkeypatch.setattr(LaserFocusDialog, "exec", focus_exec)
    monkeypatch.setattr(MachineSetupDialog, "exec", setup_exec)
    window, errors, _ = _window(tmp_path, monkeypatch)
    try:
        window.machine_panel.z_control.focus.click()
        assert opened == [window]
        assert window._laser_focus_dialog is None
        window.open_machine_setup(6)
        assert len(opened) == 2
        assert window._machine_setup_dialog is None
        assert not errors
    finally:
        _dispose(app, window)
