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
               "calibration_id": "calibration-1",
               "target_z_mm": 16.0, "current_z_mm": 30.0, "clearance_z_mm": 30.0}
    payload.update(changes)
    return payload


def unavailable_result(**changes):
    payload = result(
        available=False, reference_ready=False, recovery_available=True,
        current_readback={"z_mm": None, "z_known": False, "fresh": False},
        ender={"ready": False, "fault": "Marlin readiness timed out; last: no response",
               "generation": 4, "recovery_required": True},
        max_z_mm=40.0, probe_xy_offset_mm=[3.302, 38.608],
        surface=None, preview=None, xy_sequence=None,
    )
    payload.update(changes)
    return payload


def test_reconnect_preserves_required_clearance_without_restoring_motion(panel):
    panel.set_result(unavailable_result(requires_clearance=True))
    panel.path_clear.setChecked(True)
    assert panel.reconnect_ender.isEnabled()
    assert not panel.home.isEnabled() and not panel.reference.isEnabled()
    panel.set_result(result(
        action="recover", recovery_available=True, requires_clearance=True,
        reference_ready=False, surface=None, preview=None, xy_sequence=None,
        current_readback={"z_mm": None, "z_known": False, "fresh": True},
        ender={"ready": True, "fault": None, "generation": 5, "recovery_required": False},
    ))
    assert panel._result["requires_clearance"] is True
    assert panel.fresh()
    assert not panel.home.isEnabled()
    # Referencing Z remains a separate explicit path-confirmed operation.
    assert panel.reference.isEnabled()
    assert not panel.up.isEnabled() and not panel.move_probe.isEnabled()
    assert "then reference the border" in panel.next_step.text()
    assert "Home / park" not in panel.next_step.text()


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


def test_required_confirmations_are_above_their_focus_actions(panel, app):
    panel.resize(1000, 1400)
    app.processEvents()
    for checkbox, action in (
        (panel.xy_clear, panel.move_probe), (panel.xy_clear, panel.align_probe),
        (panel.xy_clear, panel.align_laser), (panel.flat_patch, panel.measure),
        (panel.gauge, panel.teach), (panel.gauge_removed, panel.move),
    ):
        assert checkbox.parentWidget() is action.parentWidget()
        assert checkbox.geometry().bottom() < action.geometry().top()
    assert panel.move_note.geometry().bottom() < panel.move.geometry().top()


def test_recovery_prompt_does_not_invite_another_camera_move(panel):
    panel.set_result(camera_result(surface=None))
    panel._status = status(controller_state="RECOVERING", connected=False)
    panel._sync()
    assert "Wait for controller recovery" in panel.next_step.text()
    assert not panel.position_probe.isEnabled() and not panel.move_probe.isEnabled()


def test_offset_transfer_is_explicit_and_requires_fresh_clearance(panel):
    panel.set_result(result(xy_offset_available=True, probe_xy_offset_mm=[-38.61, -3.3], surface=None))
    confirm(panel)
    calls = []
    panel.actionRequested.connect(lambda a, kw: calls.append((a, kw)))
    assert not panel.align_probe.isEnabled()
    panel.xy_clear.setChecked(True)
    assert panel.align_probe.isEnabled()
    panel.align_probe.click()
    assert calls == [("align_probe", {"confirmed": True, "clearance_z_mm": 30.0, "gap_mm": 7.0})]
    assert not panel.xy_clear.isChecked() and not panel.flat_patch.isChecked()
    panel.set_result(result(xy_offset_available=True, probe_xy_offset_mm=[-38.61, -3.3], requires_clearance=True))
    panel.xy_clear.setChecked(True)
    assert not panel.align_probe.isEnabled()


def test_probe_phase_requires_measured_return_before_teaching(panel):
    panel.set_result(result(xy_offset_available=True, probe_xy_offset_mm=[-38.61, -3.3],
                            xy_sequence={"phase": "probe"}))
    confirm(panel)
    panel.gauge.setChecked(True)
    panel.xy_clear.setChecked(True)
    assert panel.align_laser.isEnabled()
    assert not panel.align_probe.isEnabled()
    assert not panel.down.isEnabled() and not panel.teach.isEnabled() and not panel.preview.isEnabled()
    assert "Return the laser" in panel.next_step.text()
    calls = []
    panel.actionRequested.connect(lambda a, kw: calls.append((a, kw)))
    panel.align_laser.click()
    assert calls[0][0] == "align_laser" and calls[0][1]["measurement_id"] == "surface-1"
    panel.set_result(result(xy_offset_available=True, probe_xy_offset_mm=[-38.61, -3.3],
                            xy_sequence={"phase": "laser"}))
    assert panel.down.isEnabled() and "Same measured spot" in panel.flat_patch.text()
    assert not panel.measure.isEnabled()


def test_unset_offset_never_becomes_zero_and_drafts_block_transfer(panel):
    panel.set_result(result(xy_offset_available=True, probe_xy_offset_mm=None))
    confirm(panel)
    panel.xy_clear.setChecked(True)
    assert not panel.align_probe.isEnabled() and not panel.apply_offset.isEnabled()
    panel.offset_x.setValue(-38.61)
    panel.offset_y.setValue(-3.30)
    assert panel.apply_offset.isEnabled()
    panel.offset_x.lineEdit().selectAll()
    QtTest.QTest.keyClick(panel.offset_x.lineEdit(), QtCore.Qt.Key.Key_Backspace)
    assert not panel.apply_offset.isEnabled()
    calls = []
    panel.actionRequested.connect(lambda a, kw: calls.append((a, kw)))
    QtTest.QTest.keyClicks(panel.offset_x.lineEdit(), "-38.61")
    panel.apply_offset.click()
    assert calls[0][0] == "set_xy_offset" and calls[0][1]["value"] == [-38.61, -3.3]
    assert not panel.align_probe.isEnabled()  # Saving must be acknowledged first.


def test_showing_offset_editor_does_not_authorize_unentered_axis(panel, app):
    panel.set_result(result(xy_offset_available=True, probe_xy_offset_mm=None))
    panel.offset_toggle.click()
    app.processEvents()
    panel.offset_x.setValue(3.302)
    panel.offset_y.setFocus()
    panel.offset_x.setFocus()
    app.processEvents()
    assert not panel.apply_offset.isEnabled()
    panel.offset_y.lineEdit().selectAll()
    QtTest.QTest.keyClicks(panel.offset_y.lineEdit(), "38.608")
    assert panel.apply_offset.isEnabled()


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


@pytest.mark.parametrize("changed", ["gap", "clearance", "surface", "age", "preview_id", "position", "limit",
                                     "calibration", "missing_calibration_id", "incompatible", "floor"])
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
    elif changed == "floor":
        panel.set_result(result(preview=preview(target_z_mm=-.1)))
    elif changed == "calibration":
        panel.set_result(result(calibration={**result()["calibration"], "id": "calibration-2"}, preview=preview()))
    elif changed == "missing_calibration_id":
        without_calibration = preview()
        without_calibration.pop("calibration_id")
        panel.set_result(result(preview=without_calibration))
    elif changed == "incompatible":
        panel.set_result(result(calibration_compatible=False, preview=preview()))
    else:
        panel._received_at = time.monotonic() - FRESH_SECONDS - 1
    calls = []
    panel.actionRequested.connect(lambda *args: calls.append(args))
    panel.request("move")
    assert calls == []
    assert not panel.move.isEnabled()
    assert panel.move_note.text().startswith("Move unavailable:")


def test_move_explains_each_confirmation_and_explicit_preview(panel):
    panel.set_result(result(preview=preview()))
    assert "Choose Preview target" in panel.move_note.text()
    panel.set_result(result(action="preview", preview=preview()))
    assert "Headroom and Z path clear" in panel.move_note.text()
    panel.path_clear.setChecked(True)
    assert "Gauge removed; path to target clear" in panel.move_note.text()
    assert "Gauge removed; path to target clear" in panel.next_step.text()
    panel.gauge_removed.setChecked(True)
    assert panel.move.isEnabled()
    assert "move Z to 16.000 mm" in panel.move_note.text()
    assert panel.next_step.text() == "Next: Choose Move to focus to position the laser."


@pytest.mark.parametrize("cause, reason", [
    ("busy", "Wait for the current operation"),
    ("stale", "Refresh focus status"),
    ("unknown_z", "Reference the border"),
    ("reference", "Reference the border"),
    ("surface", "Measure the surface"),
    ("probe_phase", "Return the laser"),
    ("calibration", "Teach and save"),
    ("clearance", "Set Clearance Z"),
    ("position", "Z moved after the preview"),
    ("target", "outside the allowed"),
    ("armed", "laser disarmed"),
])
def test_move_blocking_reason_tracks_current_prerequisite(panel, cause, reason):
    confirm(panel)
    panel.set_result(result(action="preview", preview=preview()))
    panel.gauge_removed.setChecked(True)
    assert panel.move.isEnabled()
    if cause == "busy":
        panel._busy = True
    elif cause == "stale":
        panel._received_at -= FRESH_SECONDS + 1
    elif cause == "unknown_z":
        panel._result["current_readback"]["z_known"] = False
    elif cause == "reference":
        panel._result["reference_ready"] = False
    elif cause == "surface":
        panel._result["surface"] = None
    elif cause == "probe_phase":
        panel._result["xy_sequence"] = {"phase": "probe"}
    elif cause == "calibration":
        panel._result["calibration_compatible"] = False
    elif cause == "clearance":
        panel.clearance.setValue(65)
    elif cause == "position":
        panel._result["current_readback"]["z_mm"] = 31
    elif cause == "target":
        panel._result["preview"]["target_z_mm"] = -1
    else:
        panel._status = status(armed=True)
    panel._sync()
    assert not panel.move.isEnabled()
    assert reason in panel.move_note.text()


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
def taught_dialog(app, monkeypatch):
    from laser_aligner.desktop import focus_bed_view
    from tests.test_desktop_focus_bed_view import Worker, publish

    controller = FakeController()
    controller.runtime.context.camera = SimpleNamespace(monitor_frames=lambda **kwargs: iter(()))
    monkeypatch.setattr(focus_bed_view, "_MonitorThread", Worker)
    controller.machine.payload = result(
        max_z_mm=40, focus_travel_min_z_mm=0, max_teaching_step_mm=5,
        surface={**result()["surface"], "contact_z_mm": 5.234, "elevation_mm": 5.233},
        calibration={**result()["calibration"], "focus_offset_mm": -2.434,
                     "taught_z_mm": 2.8, "taught_contact_z_mm": 5.234},
        preview=preview(target_z_mm=2.8), xy_sequence={"phase": "laser"},
    )
    dialog = LaserFocusDialog(controller)
    dialog.coordinator._timer.stop()
    dialog._camera_timer.stop()
    dialog.show()
    app.processEvents()
    dialog.set_machine_status(status())
    dialog.panel.set_result(copy.deepcopy(controller.machine.payload))
    publish(dialog.bed_view)
    dialog._camera_tick()
    confirm(dialog.panel)
    yield dialog, controller
    assert not dialog.coordinator._mutation
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("camera_loss", ["stale", "offline"])
def test_measured_focus_preview_survives_camera_loss_poll_and_explicit_move(taught_dialog, camera_loss):
    dialog, controller = taught_dialog
    panel, coordinator = dialog.panel, dialog.coordinator
    panel.gauge_removed.setChecked(True)
    assert panel._preview_id is None and not panel.move.isEnabled()
    panel.preview.click()
    assert coordinator._busy and not panel.clearance.isEnabled()
    assert panel.clearance.hasAcceptableInput() and panel._clearance_value() == 30
    parameter_epoch, camera_epoch = coordinator._parameter_epoch, coordinator._camera_epoch
    if camera_loss == "stale":
        dialog.bed_view._received_at -= 4
        dialog.bed_view._update_status()
    else:
        dialog.bed_view._worker.failed.emit("Camera disconnected")
    dialog._camera_tick()
    assert not panel._camera_live
    assert coordinator._parameter_epoch == parameter_epoch
    assert coordinator._camera_epoch > camera_epoch
    controller.complete()
    assert controller.machine.calls == [("preview", {
        "confirmed": True, "clearance_z_mm": 30.0, "gap_mm": 7.0,
        "measurement_id": "surface-1",
    })]
    assert panel.target.text() == "Target Z: 2.800 mm · 7 mm gap"
    assert panel._preview_id == "preview-1" and panel.move.isEnabled()
    assert "no movement sent" in panel.message.text()
    coordinator._last_request = -float("inf")
    coordinator.tick()
    controller.complete()
    assert panel._preview_id == "preview-1" and panel.move.isEnabled()
    assert panel.target.text() == "Target Z: 2.800 mm · 7 mm gap"
    assert [action for action, _ in controller.machine.calls] == ["preview", "status"]
    # Only this separate click requests motion; the camera has stayed offline.
    panel.move.click()
    controller.machine.payload = result(
        current_readback={"z_mm": 2.8, "z_known": True, "fresh": True}, preview=None,
    )
    controller.complete()
    assert controller.machine.calls[-1] == ("move", {
        "confirmed": True, "clearance_z_mm": 30.0, "gap_mm": 7.0, "preview_id": "preview-1",
    })
    assert not panel.gauge_removed.isChecked() and panel._preview_id is None


@pytest.mark.parametrize("change", ["gap", "clearance", "stop", "session"])
def test_focus_preview_still_rejects_late_reply_after_authority_changes(taught_dialog, change):
    dialog, controller = taught_dialog
    panel, coordinator = dialog.panel, dialog.coordinator
    panel.gauge_removed.setChecked(True)
    panel.preview.click()
    operation, callbacks = controller.work.pop()
    response = operation()
    if change == "gap":
        panel.gap.setCurrentIndex(1)
    elif change == "clearance":
        panel.clearance.setValue(35)
    elif change == "stop":
        controller.emergency_stop()
    else:
        controller.machine.snapshot = status(controller_session_generation=9)
        controller.statusChanged.emit(controller.machine.snapshot)
    callbacks["on_success"](response)
    controller.busyChanged.emit(False)
    callbacks["on_finished"]()
    assert not panel.move.isEnabled() and panel._preview_id is None
    assert panel.move_note.text().startswith("Move unavailable:")
    assert "Target previewed" not in panel.message.text()
    # A later status observation never creates local Preview-click authority.
    panel.set_result(copy.deepcopy(controller.machine.payload))
    assert not panel.move.isEnabled() and panel._preview_id is None
    panel.move.click()
    assert [action for action, _ in controller.machine.calls] == ["preview"]
    assert coordinator._queued is None and not controller.work


@pytest.mark.parametrize("queued", [False, True])
def test_dialog_camera_loss_cancels_selected_xy_request_before_dispatch(taught_dialog, queued):
    from tests.test_desktop_focus_bed_view import select

    dialog, controller = taught_dialog
    panel, coordinator = dialog.panel, dialog.coordinator
    controller.runtime.context.focus_probe_target = lambda *args, **kwargs: camera_target()
    panel.set_result(camera_result(surface=None))
    panel.position_probe.click()
    select(dialog.bed_view)
    panel.xy_clear.setChecked(True)
    assert panel.move_probe.isEnabled()
    if queued:
        coordinator.tick()
        assert len(controller.work) == 1
    panel.move_probe.click()
    dialog.bed_view._received_at -= 4
    dialog.bed_view._update_status()
    dialog._camera_tick()
    while controller.work:
        controller.complete()
    assert not any(action == "position_probe" for action, _ in controller.machine.calls)
    assert "Camera target changed" in panel.message.text()
    assert not panel.move_probe.isEnabled()


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


def test_unavailable_ender_shows_configured_values_and_explicit_reconnect(panel, app):
    panel._status = status(controller_state="READY_HOME_REQUIRED", allow_motion=False,
                           coordinate_reference_ready=False, jog_ready=False)
    panel.set_result(unavailable_result())
    calls = []
    panel.actionRequested.connect(lambda action, args: calls.append((action, args)))
    app.processEvents()
    assert "unavailable" in panel.ender_status.text()
    assert "last: no response" in panel.failure_detail.text()
    assert panel.failure_detail.isVisible()
    assert "Configured maximum: 40" in panel.maximum.text()
    assert "Configured probe offset: X +3.302" in panel.offset_readout.text()
    assert "Configured laser offset:" in panel.calibration.text()
    assert not panel.fresh() and panel.height.text() == "Z — mm"
    assert not panel.reference.isEnabled() and not panel.position_probe.isEnabled()
    assert not panel.move.isEnabled() and not panel.up.isEnabled()
    assert panel.reconnect_ender.isEnabled()
    panel.reconnect_ender.click()
    assert calls == [("recover", {"confirmed": True, "clearance_z_mm": 30.0, "gap_mm": 7.0})]


@pytest.mark.parametrize("changes", [
    {"current_readback": {"z_mm": 30., "z_known": True, "fresh": True}},
    {"reference_ready": True}, {"surface": {"id": "unsafe", "elevation_mm": 4.}},
    {"ender": {"ready": True, "fault": None, "generation": 4, "recovery_required": True}},
    {"probe_xy_offset_mm": [float("nan"), 3.]},
])
def test_unavailable_ender_status_cannot_smuggle_motion_authority(panel, changes):
    with pytest.raises(ValueError):
        panel.set_result(unavailable_result(**changes))


@pytest.mark.parametrize("changes", [
    {"armed": True}, {"controller_state": "JOB_RUNNING", "job": {"running": True}},
    {"controller_state": "RECOVERING", "connected": False},
    {"status_stale": True}, {"z_probe": {"active": True}},
])
def test_reconnect_ender_requires_idle_disarmed_trusted_primary(panel, changes):
    panel.set_result(unavailable_result())
    panel._status = status(**changes)
    panel._sync()
    calls = []
    panel.actionRequested.connect(lambda *args: calls.append(args))
    panel.reconnect_ender.click()
    assert not panel.reconnect_ender.isEnabled() and not calls


def test_focus_failure_survives_temporary_state_change_and_read_only_retry(coordinator):
    item, controller = coordinator
    controller.machine.error = "Connect the shared Ender controller before laser focus"
    item.tick()
    controller.complete()
    detail = item.panel.failure_detail.text()
    unavailable = status(status_stale=True)
    item.set_status(unavailable)
    assert item.panel.failure_detail.text() == detail
    assert "Connect the shared Ender" in detail
    item.set_status(status())
    item.tick()
    assert len(controller.work) == 1
    controller.complete()  # Still unavailable: latch again instead of retrying forever.
    item._last_request = -100
    item.tick()
    assert controller.work == []
    assert [action for action, _ in controller.machine.calls] == ["status", "status"]


def test_home_completion_retries_read_only_status_after_latched_failure(coordinator):
    item, controller = coordinator
    controller.machine.error = "Ender readiness timed out"
    item.tick()
    controller.complete()
    item.panel.path_clear.setChecked(True)
    item.panel.home.click()
    assert controller.home_calls == 1 and item._busy
    assert "Ender readiness timed out" in item.panel.failure_detail.text()
    item.set_status(status())
    controller.machine.error = None
    controller.busyChanged.emit(False)
    item.tick()
    assert len(controller.work) == 1
    controller.complete()
    assert [action for action, _ in controller.machine.calls] == ["status", "status"]
    assert item.panel.fresh() and not item.panel.failure_detail.isVisible()


def test_reconnect_ender_is_explicit_and_unavailable_reply_never_becomes_fresh(coordinator):
    item, controller = coordinator
    controller.machine.payload = unavailable_result()
    item.tick()
    controller.complete()
    item._last_request = -100
    item.tick()
    assert controller.work == []
    assert controller.machine.calls[-1][0] == "status"
    item.panel.reconnect_ender.click()
    assert len(controller.work) == 1 and item._mutation
    controller.complete()
    assert controller.machine.calls[-1][0] == "recover"
    assert controller.machine.calls[-1][1]["confirmed"] is True
    assert not item.panel.fresh() and item._error
    assert "last: no response" in item.panel.failure_detail.text()
    controller.machine.payload = result(
        action="recover", recovery_available=True,
        ender={"ready": True, "fault": None, "generation": 6, "recovery_required": False},
        current_readback={"z_mm": 0.0, "z_known": False, "fresh": True},
        reference_ready=False, surface=None,
    )
    item.panel.reconnect_ender.click()
    controller.complete()
    assert item.panel.fresh() and not item._error
    assert "reference required" in item.panel.height.text()
    assert not item.panel.move.isEnabled() and item.panel._camera_target is None


def test_stop_during_reconnect_discards_late_reply_and_never_replays_recovery(coordinator):
    item, controller = coordinator
    item.panel.set_result(unavailable_result())
    item.panel.reconnect_ender.click()
    operation, callbacks = controller.work.pop()
    reply = operation()
    item.stopped()
    callbacks["on_success"](reply)
    controller.busyChanged.emit(False)
    callbacks["on_finished"]()
    item._last_request = -100
    item.tick()
    assert not item.panel.fresh() and controller.work == []
    assert [action for action, _ in controller.machine.calls] == ["recover"]


def test_reported_reconnect_activity_does_not_discard_its_own_reply(coordinator):
    item, controller = coordinator
    item.panel.set_result(unavailable_result())
    controller.machine.payload = result(
        recovery_available=True, reference_ready=False, surface=None,
        current_readback={"z_mm": 0., "z_known": False, "fresh": True},
        ender={"ready": True, "fault": None, "generation": 6, "recovery_required": False},
    )
    item.panel.reconnect_ender.click()
    operation, callbacks = controller.work.pop()
    reply = operation()
    item.set_status(status(z_probe={"active": True}))
    callbacks["on_success"](reply)
    controller.busyChanged.emit(False)
    callbacks["on_finished"]()
    assert item.panel.fresh() and not item._error
    assert not item.panel.reference.isEnabled()
    item.set_status(status())
    assert item.panel.fresh()
    assert [action for action, _ in controller.machine.calls] == ["recover"]


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

def camera_target():
    return {"target_machine_xy_mm": [100.0, 120.0], "mapping_signature": "mapping-1"}


def camera_selection():
    return {"image_x": 900.0, "image_y": 550.0, "width": 1920, "height": 1080,
            "source_width": 1920, "source_height": 1080, "frame_age_seconds": 0.1,
            "snapshot_monotonic": time.monotonic(), "fresh": True, "mapping_signature": "mapping-1"}


def camera_result(**changes):
    return result(xy_offset_available=True, position_probe_available=True,
                  probe_xy_offset_mm=[3.302, 38.608], **changes)


def test_camera_selection_does_not_move_and_requires_path_confirmation(panel):
    panel.set_result(camera_result(surface=None))
    panel._camera_live = True
    confirm(panel)
    calls = []
    panel.actionRequested.connect(lambda *args: calls.append(args))
    panel.position_probe.click()
    panel.set_camera_target(camera_target())
    assert panel.position_probe.isChecked()
    assert "96.70" in panel.camera_target.text() and "81.39" in panel.camera_target.text()
    assert not panel.move_probe.isEnabled()
    assert calls == []
    panel.xy_clear.setChecked(True)
    assert panel.move_probe.isEnabled()
    panel.clearance.setValue(40)
    assert panel._camera_target is None and not panel.move_probe.isEnabled()


@pytest.mark.parametrize("within_grid", [True, False])
def test_camera_preview_distinguishes_original_sample_grid_from_work_limits(panel, within_grid):
    panel.set_result(camera_result(surface=None))
    panel._camera_live = True
    confirm(panel)
    panel.position_probe.click()
    panel.set_camera_target({**camera_target(), "within_original_calibration_grid": within_grid})
    assert ("Outside original calibration grid" in panel.camera_target.text()) is (not within_grid)
    assert not panel.move_probe.isEnabled()
    panel.xy_clear.setChecked(True)
    assert panel.move_probe.isEnabled()


@pytest.mark.parametrize("reason", ["old_pi", "offline", "unknown_z", "low_z", "offset_draft", "stale"])
def test_camera_position_rejects_unready_machine(panel, reason):
    panel.set_result(camera_result(surface=None))
    panel._camera_live = True
    confirm(panel)
    panel.xy_clear.setChecked(True)
    panel.set_camera_target(camera_target())
    assert panel.move_probe.isEnabled()
    if reason == "old_pi":
        panel._result.pop("position_probe_available")
    elif reason == "offline":
        panel._camera_live = False
    elif reason == "unknown_z":
        panel._result["current_readback"]["z_known"] = False
    elif reason == "low_z":
        panel._result["current_readback"]["z_mm"] = 29
    elif reason == "offset_draft":
        panel.offset_x.setValue(3.0)
    else:
        panel._received_at = time.monotonic() - FRESH_SECONDS - 1
    panel._sync()
    assert not panel.position_probe.isEnabled() and not panel.move_probe.isEnabled()


def test_camera_click_mapping_and_separate_move_are_integrated(app, monkeypatch):
    controller = FakeController()
    controller.runtime.context.focus_probe_target = lambda *args, **kwargs: camera_target()
    dialog = LaserFocusDialog(controller)
    dialog.coordinator._timer.stop()
    dialog._camera_timer.stop()
    dialog.set_machine_status(status())
    dialog.panel.set_result(camera_result(surface=None))
    monkeypatch.setattr(dialog.bed_view, "current_frame_fresh", lambda: True)
    monkeypatch.setattr(dialog.bed_view, "selection_snapshot", camera_selection)
    dialog._camera_tick()
    confirm(dialog.panel)
    dialog.panel.position_probe.click()
    dialog.bed_view.pointSelected.emit(camera_selection())
    assert controller.work == [] and controller.machine.calls == []
    dialog.panel.xy_clear.setChecked(True)
    dialog.panel.move_probe.click()
    assert len(controller.work) == 1
    controller.complete()
    assert controller.machine.calls == [("position_probe", {
        "confirmed": True, "clearance_z_mm": 30.0, "value": [100.0, 120.0]})]
    assert dialog.panel._camera_target is None
    assert not dialog.panel.flat_patch.isChecked()
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("cause", ["age", "mapping", "stop", "camera", "camera_during_mapping"])
def test_queued_camera_move_is_revalidated_before_controller(coordinator, cause):
    item, controller = coordinator
    item.panel.set_result(camera_result())
    mapped = camera_target()
    def mapper(*args, **kwargs):
        if cause == "mapping":
            raise ValueError("Camera calibration changed after selection")
        if kwargs["frame_age_seconds"] > 2:
            raise ValueError("Camera frame is stale")
        if cause == "camera_during_mapping":
            item.camera_selection_changed()
        return mapped
    controller.runtime.context.focus_probe_target = mapper
    selection = camera_selection()
    if cause == "age":
        selection["snapshot_monotonic"] -= 3
    item.request("position_probe", {"confirmed": True, "clearance_z_mm": 30.0,
                                   "value": mapped["target_machine_xy_mm"], "_camera_selection": selection})
    if cause == "stop":
        controller.emergency_stop()
    elif cause == "camera":
        item.camera_selection_changed()
    controller.complete()
    assert controller.machine.calls == []
    assert not item.panel.fresh()

@pytest.mark.parametrize("phase", ["click", "refresh", "move"])
def test_camera_calibration_errors_are_shown_inline_without_motion(app, monkeypatch, phase):
    from laser_aligner.errors import CalibrationError
    controller = FakeController()
    dialog = LaserFocusDialog(controller)
    dialog.coordinator._timer.stop()
    dialog._camera_timer.stop()
    dialog.set_machine_status(status())
    dialog.panel.set_result(camera_result(surface=None))
    monkeypatch.setattr(dialog.bed_view, "current_frame_fresh", lambda: True)
    monkeypatch.setattr(dialog.bed_view, "selection_snapshot", camera_selection)
    dialog._camera_tick()
    confirm(dialog.panel)
    dialog.panel.position_probe.click()
    def fail(*args, **kwargs):
        raise CalibrationError("Saved camera calibration is stale")
    controller.runtime.context.focus_probe_target = fail
    if phase == "click":
        dialog.bed_view.pointSelected.emit(camera_selection())
    else:
        dialog.panel.set_camera_target(camera_target())
        dialog.panel.xy_clear.setChecked(True)
        if phase == "refresh":
            dialog._camera_tick()
        else:
            dialog._move_camera_probe()
    assert "calibration is stale" in dialog.panel.camera_target.text()
    assert dialog.panel._camera_target is None
    assert controller.machine.calls == [] and controller.work == []
    dialog.reject()
    dialog.deleteLater()
    app.processEvents()

def test_camera_selection_edit_while_polling_cancels_queued_move(coordinator):
    item, controller = coordinator
    item.panel.set_result(camera_result())
    controller.runtime.context.focus_probe_target = lambda *args, **kwargs: camera_target()
    item.tick()
    item.request("position_probe", {"confirmed": True, "clearance_z_mm": 30.0,
        "value": [100.0, 120.0], "_camera_selection": camera_selection()})
    assert item._queued is not None
    item.parameters_edited()
    controller.complete()
    controller.complete()
    assert controller.machine.calls == []
    assert "target changed" in item.panel.message.text()


@pytest.mark.parametrize("invalidate", ["offset", "position", "session", "stale"])
def test_rejected_click_keeps_pixel_but_revokes_old_move_target(app, monkeypatch, invalidate):
    from laser_aligner.desktop import focus_bed_view
    from laser_aligner.errors import CalibrationError
    from tests.test_desktop_focus_bed_view import Worker, publish, select

    controller = FakeController()
    controller.runtime.context.camera = SimpleNamespace(monitor_frames=lambda **kwargs: iter(()))
    def mapper(x, y, **kwargs):
        if x < 600:
            raise CalibrationError("Selected point is outside the configured machine work area")
        return camera_target()
    controller.runtime.context.focus_probe_target = mapper
    monkeypatch.setattr(focus_bed_view, "_MonitorThread", Worker)
    dialog = LaserFocusDialog(controller)
    dialog.coordinator._timer.stop()
    dialog._camera_timer.stop()
    try:
        dialog.show()
        app.processEvents()
        dialog.set_machine_status(status())
        dialog.panel.set_result(camera_result(surface=None))
        publish(dialog.bed_view)
        dialog._camera_tick()
        confirm(dialog.panel)
        dialog.panel.xy_clear.setChecked(True)
        dialog.panel.position_probe.click()
        select(dialog.bed_view)
        assert dialog.panel.move_probe.isEnabled()
        select(dialog.bed_view, x=.25)
        snapshot = dialog.bed_view.selection_snapshot()
        assert snapshot["image_x"] == pytest.approx(480)
        assert dialog.bed_view._selection_rejected
        assert dialog.panel._camera_target is None
        assert not dialog.panel.move_probe.isEnabled()
        assert "machine work area" in dialog.panel.camera_target.text()
        dialog._move_camera_probe()
        dialog.panel.move_probe.click()
        assert controller.machine.calls == [] and controller.work == []
        # A new accepted pixel replaces red feedback with a new move target.
        select(dialog.bed_view)
        assert dialog.panel.move_probe.isEnabled()
        assert not dialog.bed_view._selection_rejected
        assert not dialog.panel._camera_point_rejected
        select(dialog.bed_view, x=.25)
        # Matching idle readback retains the diagnosis and marker.
        dialog.panel.set_result(camera_result(surface=None))
        assert dialog.bed_view.selection_snapshot() is not None
        if invalidate == "offset":
            dialog.panel.offset_x.setValue(4)
        elif invalidate == "position":
            dialog.panel.set_result(camera_result(surface=None, current_carriage_xy_mm=[90, 100]))
        elif invalidate == "session":
            dialog.set_machine_status(status(controller_session_generation=9))
        else:
            dialog.bed_view._received_at -= 10
            dialog.bed_view._update_status()
        assert dialog.bed_view.selection_snapshot() is None
        assert not dialog.panel._camera_point_rejected
        assert not dialog.panel.move_probe.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()

def test_camera_head_preview_uses_laser_center_correction_and_invalidates_changes(panel):
    panel.set_result(camera_result(laser_spot_offset_mm=[2.0, -1.0]))
    panel._camera_live = True
    confirm(panel)
    panel.set_camera_target(camera_target())
    assert "94.70" in panel.camera_target.text() and "82.39" in panel.camera_target.text()
    panel.set_result(camera_result(laser_spot_offset_mm=[3.0, -1.0]))
    assert panel._camera_target is None
    assert "offset changed" in panel.camera_target.text()



def test_coarse_teaching_steps_require_pi_support_and_revert_on_old_node(panel):
    payload = result()
    payload["max_teaching_step_mm"] = 5
    panel.set_result(payload)
    assert [panel.z_step.itemData(i) for i in range(panel.z_step.count())] == [.1, .5, 1, 2, 5]
    panel.z_step.setCurrentIndex(panel.z_step.findData(5))
    panel.set_result(result())
    assert [panel.z_step.itemData(i) for i in range(panel.z_step.count())] == [.1, .5, 1]
    assert panel.z_step.currentData() == .1


def test_long_jog_completion_defers_poll_but_allows_next_click(coordinator, monkeypatch):
    item, controller = coordinator
    now = [100.0]
    monkeypatch.setattr("laser_aligner.desktop.laser_focus.time.monotonic", lambda: now[0])
    item.panel.set_result(result())
    confirm(item.panel)
    item.panel.up.click()
    assert len(controller.work) == 1
    now[0] += 6.0
    controller.complete()
    item.tick()
    assert controller.work == []
    # No artificial inter-click timer: completion enables the next single move.
    item.panel.up.click()
    assert len(controller.work) == 1
    controller.complete()
    now[0] += 2.01
    item.tick()
    assert len(controller.work) == 1
    assert controller.work[0][1]["show_busy"] is False



def test_teaching_below_probe_contact_requires_updated_pi_and_respects_zero(panel):
    confirm(panel)
    payload = result(current_readback={"z_mm": 6.0, "z_known": True, "fresh": True},
                     requires_clearance=True, focus_travel_min_z_mm=0.0, max_teaching_step_mm=5)
    payload["surface"]["contact_z_mm"] = 5.124
    panel.set_result(payload)
    panel.z_step.setCurrentIndex(panel.z_step.findData(1))
    assert panel.down.isEnabled()
    assert "0 to" in panel.teaching_limits.text()
    payload["current_readback"]["z_mm"] = .5
    panel.set_result(payload)
    assert not panel.down.isEnabled()
    assert "smaller step" in panel.teaching_limits.text()
    panel.z_step.setCurrentIndex(panel.z_step.findData(.1))
    assert panel.down.isEnabled()
    payload["current_readback"]["z_mm"] = 0
    panel.set_result(payload)
    assert not panel.down.isEnabled()
    assert "minimum reached" in panel.teaching_limits.text()
