from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from laser_aligner.desktop.laser_focus import FRESH_SECONDS
from tests import test_desktop_daily_job_focus as helpers
from tests.test_desktop_laser_focus import status

app = helpers.app
daily = helpers.daily
workpiece_result = helpers.workpiece_result


def saved_result():
    return workpiece_result(surface=None, job_focus=None, xy_sequence=None,
                            saved_workpiece_available=True,
                            saved_workpiece={"surface_elevation_mm": 2., "spacer_thickness_mm": 1.})


def test_saved_height_is_displayed_without_a_reuse_action(daily):
    panel, _, controller = daily
    panel.set_result(saved_result())
    assert not hasattr(panel, "reuse_workpiece")
    assert "+2.000" in panel.target.text() and "1.000" in panel.target.text()
    assert panel.spacers.value() == 1. and not panel.spacers.isEnabled()
    assert "automatically" in panel.next_step.text()
    assert "automatically" in panel.reuse_note.text()
    assert panel.clear_surface.isEnabled()
    assert controller.machine.calls == [] and controller.work == []
    assert controller.xy_calls == []


def test_automatically_reused_material_is_ready_without_clicking(daily):
    panel, _, controller = daily
    data = workpiece_result(surface=None, xy_sequence=None)
    data["job_focus"]["source"] = "saved_workpiece"
    panel.set_result(data)
    assert "Using saved material setup" in panel.job_note.text()
    assert "ready for every job" in panel.next_step.text()
    assert controller.machine.calls == [] and controller.work == []


@pytest.mark.parametrize("change", ["stale", "busy", "armed", "unsupported", "unknown_z", "clearance", "reference"])
def test_reuse_unavailable_without_current_authority(daily, change):
    panel, coordinator, controller = daily
    data = saved_result()
    if change == "unsupported":
        data["saved_workpiece_available"] = False
    elif change == "unknown_z":
        data["current_readback"]["z_known"] = False
    elif change == "clearance":
        data["current_readback"]["z_mm"] = 29.
    elif change == "reference":
        data["reference_ready"] = False
    panel.set_result(data)
    if change == "stale":
        panel._received_at -= FRESH_SECONDS + 1
    elif change == "busy":
        coordinator.set_busy(True)
    elif change == "armed":
        coordinator.set_status(status(armed=True))
    panel.request("reuse_workpiece")
    assert controller.work == [] and controller.machine.calls == []
