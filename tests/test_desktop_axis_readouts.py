from __future__ import annotations

# ruff: noqa: E402, I001
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from laser_aligner.desktop.panels import MachinePanel
from laser_aligner.desktop.theme import DARK_STYLESHEET


@pytest.fixture
def panel():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    widget = MachinePanel()
    widget.setStyleSheet(DARK_STYLESHEET)
    widget.resize(340, 800)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def status(**changes):
    result = dict(connected=True, controller_state="READY_MOTION", allow_motion=True,
                  coordinate_reference_ready=True, jog_ready=True,
                  jog_position_mm={"x": -12.345, "y": 210}, job={})
    result.update(changes)
    return result


def test_axis_boxes_keep_size_and_controls_stationary_as_text_changes(panel):
    app = QtWidgets.QApplication.instance()
    boxes = [panel.x_position, panel.y_position, panel.z_control.height]
    sizes = [box.size() for box in boxes]
    panel.set_status(status())
    app.processEvents()
    buttons = [panel.jog_up, panel.jog_left, panel.z_control.up, panel.z_control.down]
    positions = [button.pos() for button in buttons]
    for text in ("X — mm", "Z unknown", "Y -9999.999 mm", "Z live homing " * 20):
        for box in boxes:
            box.setText(text)
        app.processEvents()
        assert [box.size() for box in boxes] == sizes
        assert [button.pos() for button in buttons] == positions
        assert all(box.minimumSize() == box.maximumSize() == box.size() for box in boxes)
    assert panel.x_position.y() < panel.jog_group.y()
    assert panel.z_control.height.y() < panel.z_control.up.y()


def test_xy_coordinates_use_completed_position_and_clear_when_unavailable(panel):
    panel.set_status(status())
    assert panel.x_position.text() == "X -12.345 mm"
    assert panel.y_position.text() == "Y 210.000 mm"
    for changes in (
        {"connected": False}, {"status_stale": True}, {"status_refresh_error": "offline"},
        {"coordinate_reference_ready": False}, {"job": {"running": True}},
        {"jog_position_mm": None}, {"jog_position_mm": {"x": float("nan"), "y": True}},
    ):
        panel.set_status(status(**changes))
        assert panel.x_position.text() == "X — mm"
        assert panel.y_position.text() == "Y — mm"
    panel.set_status(status())
    panel.set_busy(True)
    assert panel.x_position.text() == "X — mm"
    panel.set_busy(False)
    assert panel.x_position.text() == "X -12.345 mm"
