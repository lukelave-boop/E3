"""Offscreen daily manual-height entry and stale/unsupported controller guards."""
import pytest

pytest.importorskip("PySide6")
from tests.test_desktop_daily_job_focus import app, daily, workpiece_result  # noqa: F401
from tests.test_desktop_laser_focus import status


def enable(panel):
    panel.set_result(workpiece_result(surface=None, job_focus=None, manual_height_available=True))
    panel.height_mode.setCurrentIndex(1)


def test_manual_dimensions_sent_and_labelled(daily):  # noqa: F811
    panel, _, controller = daily
    enable(panel)
    assert panel.manual_apply.isEnabled() and not panel.measure.isEnabled()
    panel.manual_thickness.setValue(3.2)
    panel.spacers.setValue(5.)
    payload = workpiece_result(manual_height_available=True)
    payload["job_focus"].update(height_source="manual", entered_height={
        "material_thickness_mm": 3.2, "spacer_thickness_mm": 5.,
    }, material_thickness_mm=3.2, spacer_thickness_mm=5.)
    controller.machine.payload = payload
    panel.manual_apply.click()
    controller.complete()
    assert controller.machine.calls == [("set_workpiece_height", {
        "confirmed": True, "clearance_z_mm": 30., "gap_mm": 7.,
        "value": {"material_thickness_mm": 3.2, "spacer_thickness_mm": 5.},
    })]
    assert "Manual entry" in panel.target.text()
    assert not panel.manual_thickness.isEnabled() and not panel.spacers.isEnabled()
    assert panel.clear_surface.isEnabled()


@pytest.mark.parametrize("change", ["unsupported", "reference", "unknown_z", "gauge", "below_clearance",
                                    "busy", "armed", "stale"])
def test_manual_entry_requires_current_ready_machine(daily, change):  # noqa: F811
    panel, coordinator, controller = daily
    enable(panel)
    if change == "unsupported":
        panel._result["manual_height_available"] = False
    elif change == "reference":
        panel._result["reference_ready"] = False
    elif change == "unknown_z":
        panel._result["current_readback"]["z_known"] = False
    elif change == "gauge":
        panel._result["calibration_compatible"] = False
    elif change == "below_clearance":
        panel._result["current_readback"]["z_mm"] = 29.
    elif change == "busy":
        coordinator.set_busy(True)
    elif change == "armed":
        coordinator.set_status(status(armed=True))
    else:
        panel._received_at -= 100
    panel.request("set_workpiece_height")
    assert controller.machine.calls == [] and controller.work == []


@pytest.mark.parametrize("event", ["stop", "session"])
def test_queued_manual_height_cancelled_by_authority_change(daily, event):  # noqa: F811
    panel, _, controller = daily
    enable(panel)
    panel.manual_apply.click()
    assert len(controller.work) == 1
    if event == "stop":
        controller.emergency_stop()
    else:
        controller.machine.snapshot = status(controller_session_generation=9)
    controller.complete()
    assert controller.machine.calls == []


def test_manual_entry_cannot_be_recorded_in_bed_survey(daily):  # noqa: F811
    from laser_aligner.desktop.precision_setup import BedSurveyDialog
    panel, _, _ = daily
    payload = workpiece_result(reference={"border_z_mm": 0.}, reference_id="reference-one")
    payload["surface"]["height_source"] = "manual"
    payload["surface"].pop("measured_at", None)
    panel.set_result(payload)
    survey = BedSurveyDialog(panel)
    survey.record.click()
    assert survey.survey is None
    assert "explicit surface measurement" in survey.summary.text()
    survey.close()
    survey.deleteLater()
