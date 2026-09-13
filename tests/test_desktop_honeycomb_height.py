"""Step 7 datum edits and daily saved-height display, with offscreen widgets."""
import pytest

pytest.importorskip("PySide6")
from laser_aligner.desktop.laser_focus import LaserFocusPanel
from tests import test_desktop_laser_focus as helpers

app = helpers.app
panel = helpers.panel


def payload(**changes):
    return helpers.result(thickness_focus_available=True, honeycomb_height_persistent=True,
                          honeycomb_height_mm=-1.5, **changes)


def test_saved_height_edit_survives_poll_until_explicit_save(panel):
    panel.set_result(payload())
    assert panel.honeycomb_group.isVisible()
    assert panel.honeycomb_height.isEnabled() and not panel.save_honeycomb.isEnabled()
    panel.honeycomb_height.setValue(-1.6)
    panel.set_result(payload())
    assert panel.honeycomb_height.value() == -1.6
    assert "Saved: -1.500" in panel.honeycomb_note.text()
    calls = []
    panel.actionRequested.connect(lambda *args: calls.append(args))
    panel.save_honeycomb.click()
    assert len(calls) == 1 and calls[0][0] == "set_honeycomb_height"
    assert calls[0][1]["value"] == -1.6 and calls[0][1]["confirmed"] is True
    result = payload(action="set_honeycomb_height", surface=None, preview=None, job_focus=None)
    result["honeycomb_height_mm"] = -1.6
    panel.set_result(result)
    assert not panel.save_honeycomb.isEnabled()
    assert "Saved: -1.600" in panel.honeycomb_note.text()


@pytest.mark.parametrize("block", ["old_pi", "busy", "armed", "stale", "nonpersistent"])
def test_save_datum_does_not_bypass_authority(panel, block):
    result = payload()
    if block == "old_pi":
        result.pop("honeycomb_height_mm")
        result["thickness_focus_available"] = False
    if block == "nonpersistent":
        result["honeycomb_height_persistent"] = False
    panel.set_result(result)
    panel.honeycomb_height.setValue(-1.6)
    if block == "busy":
        panel._busy = True
    if block == "armed":
        panel._status = helpers.status(armed=True)
    if block == "stale":
        panel._received_at = 0
    panel._sync()
    assert not panel.save_honeycomb.isEnabled()


def test_daily_panel_shows_controller_datum_and_cannot_edit(app):
    panel = LaserFocusPanel()
    panel._status = helpers.status()
    result = payload()
    result["honeycomb_height_mm"] = -1.6
    panel.set_result(result)
    assert "-1.600 mm relative to border" in panel.thickness_note.text()
    assert panel.honeycomb_group.isHidden() and not panel.save_honeycomb.isEnabled()
    panel.close()
    panel.deleteLater()
