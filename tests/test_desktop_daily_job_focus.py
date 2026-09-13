"""Daily measurements establish reusable workpiece focus without hidden extra steps."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from laser_aligner.desktop.laser_focus import FRESH_SECONDS, LaserFocusCoordinator, LaserFocusPanel
from tests.test_desktop_laser_focus import FakeController, preview, result, status


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def workpiece(gap=7.0):
    return {
        "id": f"workpiece-{gap:g}", "gap_mm": gap, "target_z_mm": 16.0 + gap - 7.0,
        "clearance_z_mm": 30.0, "reusable": True, "measurement_id": "surface-1",
        "calibration_id": "calibration-1", "focus_policy": "linear-0-6mm-v1",
        "material_thickness_mm": (7-gap)*1.5, "spacer_thickness_mm": 0.0,
    }


def workpiece_result(**changes):
    payload = result(
        thickness_focus_available=True, workpiece_focus_available=True, job_focus_required=True, job_focus_block_reason=None,
        job_focus=workpiece(), xy_sequence={"phase": "probe"},
    )
    payload.update(changes)
    return payload


@pytest.fixture
def daily(app):
    panel = LaserFocusPanel()
    controller = FakeController()
    coordinator = LaserFocusCoordinator(panel, controller)
    coordinator._timer.stop()
    coordinator.set_status(status())
    controller.machine.payload = workpiece_result(surface=None, job_focus=None)
    panel.set_result(controller.machine.payload)
    panel.path_clear.setChecked(True)
    panel.show()
    app.processEvents()
    yield panel, coordinator, controller
    coordinator.close()
    panel.close()
    panel.deleteLater()
    app.processEvents()


def test_daily_measurement_immediately_displays_focus_for_every_job(daily):
    panel, _, controller = daily
    assert panel.measure.isEnabled() and panel.gap.currentData() == 7.0
    assert panel.target.isVisible() and panel.job_note.isVisible()
    assert "every job" in panel.position_note.text()
    assert "supports" in panel.position_note.text()
    controller.machine.payload = workpiece_result()

    panel.measure.click()
    assert len(controller.work) == 1
    controller.complete()

    assert controller.machine.calls == [
        ("measure_workpiece", {"confirmed": True, "clearance_z_mm": 30.0, "gap_mm": 7.0, "value": 0.0}),
    ]
    assert panel._result["job_focus"]["id"] == "workpiece-7"
    assert "16.000" in panel.target.text() and "7.000 mm" in panel.target.text()
    assert "Every job" in panel.job_note.text() and "30" in panel.job_note.text()
    assert "ready for every job" in panel.next_step.text()
    assert "Return the laser" not in panel.next_step.text()
    assert controller.xy_calls == []


def test_spacers_are_sent_to_controller_and_locked_after_measurement(daily):
    panel, _, controller = daily
    assert panel.spacers.isEnabled()
    panel.spacers.setValue(5.0)
    payload = workpiece_result(job_focus=workpiece(13/3))
    payload["job_focus"]["spacer_thickness_mm"] = 5.0
    controller.machine.payload = payload
    panel.measure.click()
    controller.complete()
    assert controller.machine.calls == [
        ("measure_workpiece", {"confirmed": True, "clearance_z_mm": 30.0,
                               "gap_mm": 7.0, "value": 5.0}),
    ]
    assert not panel.spacers.isEnabled()
    assert "4.333 mm" in panel.target.text() and "4.000 mm" in panel.target.text()
    assert panel.gap.isHidden()
    assert controller.xy_calls == []


def test_observing_parked_workpiece_restores_spacers_without_writing(daily):
    panel, _, controller = daily
    payload = workpiece_result(job_focus=workpiece(13/3), surface=None, reference_ready=False)
    payload["job_focus"]["spacer_thickness_mm"] = 12.0
    panel.set_result(payload)
    assert panel.spacers.value() == 12.0 and not panel.spacers.isEnabled()
    assert panel.clear_surface.isEnabled()
    assert "ready for every job" in panel.next_step.text()
    assert controller.work == [] and controller.machine.calls == []


@pytest.mark.parametrize("change", ["stale", "busy", "armed", "stop", "unsupported"])
def test_measurement_rejects_missing_or_changed_authority(daily, change):
    panel, coordinator, controller = daily
    panel.set_result(workpiece_result(surface=None, job_focus=None))
    if change == "stale":
        panel._received_at -= FRESH_SECONDS + 1
    elif change == "busy":
        coordinator.set_busy(True)
    elif change == "armed":
        coordinator.set_status(status(armed=True))
    elif change == "stop":
        controller.emergency_stop()
    elif change == "unsupported":
        panel._result["thickness_focus_available"] = False
    panel.request("measure")
    assert controller.work == [] and controller.machine.calls == []


@pytest.mark.parametrize("change", ["stop", "session"])
def test_cancelled_measurement_worker_never_writes(daily, change):
    panel, _, controller = daily
    panel.measure.click()
    assert len(controller.work) == 1
    if change == "stop":
        controller.emergency_stop()
    else:
        controller.machine.snapshot = status(controller_session_generation=9)
    controller.complete()
    assert controller.machine.calls == []


def test_manual_gap_requests_cannot_change_daily_focus(daily):
    panel, coordinator, controller = daily
    panel.set_result(workpiece_result())
    panel.gap.setCurrentIndex(panel.gap.findData(5.0))
    panel.request("set_job_gap")
    coordinator.request("set_job_gap", {"confirmed": True, "gap_mm": 5.0})
    assert controller.work == [] and controller.machine.calls == []


@pytest.mark.parametrize("reason", [
    "Teach the 7 mm gauge before jobs.", "Calculated focus is outside the Z travel range.",
])
def test_measured_surface_without_valid_focus_shows_jobs_blocked(daily, reason):
    panel, _, controller = daily
    panel.set_result(workpiece_result(job_focus=None, job_focus_block_reason=reason))
    assert "Jobs blocked" in panel.job_note.text() and reason in panel.job_note.text()
    assert reason in panel.next_step.text()
    assert "ready for every job" not in panel.next_step.text()
    assert controller.work == []


def test_older_companion_does_not_claim_automatic_workpiece_focus(daily):
    panel, _, _ = daily
    panel.set_result(result())
    assert "Pi companion update" in panel.job_note.text()
    assert "Every job" not in panel.job_note.text()


def test_daily_workpiece_focus_keeps_manual_focus_and_teaching_in_setup(daily):
    panel, coordinator, controller = daily
    panel.set_result(workpiece_result(action="preview", preview=preview(), job_focus_available=True))
    assert panel.teach is None and panel.gauge is None and panel.offset_editor is None
    for control in (panel.preview, panel.move, panel.use_job):
        assert not control.isVisible() and not control.isEnabled()
    for action in ("preview", "move", "use_job"):
        panel.request(action)
        coordinator.request(action, {
            "confirmed": True, "clearance_z_mm": 30.0, "gap_mm": 7.0,
            "measurement_id": "surface-1", "preview_id": "preview-1",
        })
    assert controller.work == [] and controller.machine.calls == []


def test_compact_daily_workpiece_focus_fits_the_inspector(daily, app):
    panel, _, _ = daily
    panel.set_result(workpiece_result())
    panel.resize(380, 2200)
    app.processEvents()
    group = panel.preview_group
    assert group.title() == "Workpiece focus"
    assert group.geometry().right() < panel.width()
    assert panel.gap.isHidden()
    assert panel.spacers.geometry().bottom() < panel.thickness_note.geometry().top()
    assert panel.thickness_note.geometry().bottom() < panel.target.geometry().top()
    assert panel.target.geometry().bottom() < panel.job_note.geometry().top()
    assert panel.job_note.wordWrap()
