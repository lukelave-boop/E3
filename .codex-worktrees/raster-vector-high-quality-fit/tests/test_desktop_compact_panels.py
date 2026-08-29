from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.panels import JobProgressWidget, LayerPanel, MachinePanel
from laser_aligner.desktop.theme import DARK_STYLESHEET
from laser_aligner.project import ProjectDocument


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _show_compact(
    panel: QtWidgets.QWidget,
    application: QtWidgets.QApplication,
    height: int,
) -> None:
    panel.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 10pt; }")
    panel.resize(420, height)
    panel.show()
    application.processEvents()


def _assert_inside_panel(widget: QtWidgets.QWidget, panel: QtWidgets.QWidget) -> None:
    top_left = widget.mapTo(panel, widget.rect().topLeft())
    bottom_right = widget.mapTo(panel, widget.rect().bottomRight())
    assert top_left.x() >= 0
    assert top_left.y() >= 0
    assert bottom_right.x() < panel.width()
    assert bottom_right.y() < panel.height()


def test_layer_panel_keeps_table_and_quick_settings_visible_at_dock_width(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new("Compact operations")
    panel = LayerPanel()
    panel.set_document(document)
    _show_compact(panel, qt_application, 320)

    quick_settings = panel.findChild(QtWidgets.QWidget, "layerQuickSettings")
    assert quick_settings is not None
    assert panel.layer_list.geometry().top() <= 5
    assert panel.layer_list.geometry().bottom() < quick_settings.geometry().top()
    for control in (
        panel.color_button,
        panel.speed_spin,
        panel.passes_spin,
        panel.power_spin,
        panel.output_check,
        panel.visible_check,
    ):
        assert control.isVisibleTo(panel)
        _assert_inside_panel(control, panel)

    explanatory_copy = [
        label.text()
        for label in panel.findChildren(QtWidgets.QLabel)
        if label.text().startswith("Layer colors identify")
    ]
    assert explanatory_copy == []

    panel.close()
    panel.deleteLater()


def test_job_progress_widget_is_compact_and_switches_preparation_state(
    qt_application: QtWidgets.QApplication,
) -> None:
    widget = JobProgressWidget()
    _show_compact(widget, qt_application, 18)

    assert widget.height() == 18
    assert widget.currentWidget() is widget.progress
    assert widget.findChildren(QtWidgets.QPushButton) == []
    for removed_control in (
        "generate_button",
        "preview_button",
        "pause_button",
        "stop_button",
    ):
        assert not hasattr(widget, removed_control)

    widget.set_preparing(
        True,
        "Building exact previews · 40%",
        completed=400,
        total=1000,
    )
    assert widget.currentWidget() is widget.preparation_progress
    assert widget.preparation_progress.minimum() == 0
    assert widget.preparation_progress.maximum() == 1000
    assert widget.preparation_progress.value() == 400
    assert widget.preparation_progress.format() == "Preparing 40%"
    assert "Building exact previews · 40%" in widget.toolTip()

    widget.set_preparing(False)
    assert widget.currentWidget() is widget.progress
    assert widget.preparation_progress.value() == 0

    widget.close()
    widget.deleteLater()


def test_machine_panel_is_dense_without_duplicate_primary_controls(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = MachinePanel()
    _show_compact(panel, qt_application, 390)

    jog_group = panel.jog_group
    assert not jog_group.isEnabled()
    assert "physical emergency stop" in panel.safety_note.text()
    assert not hasattr(panel, "connect_button")
    assert not hasattr(panel, "disconnect_button")
    assert not hasattr(panel, "stop_button")
    _assert_inside_panel(panel.park_button, panel)

    panel.set_status(
        {
            "connected": False,
            "armed": False,
            "protocol": "grbl",
            "allow_motion": True,
            "job": {},
        }
    )
    assert panel.park_button.isEnabled()
    assert "connect automatically" in panel.park_button.toolTip().lower()

    panel.set_status(
        {
            "connected": True,
            "armed": False,
            "protocol": "grbl",
            "allow_motion": False,
            "job": {},
        }
    )
    assert not panel.park_button.isEnabled()
    assert "Motion blocked" in panel.state_label.text()

    panel.set_status(
        {
            "connected": True,
            "armed": False,
            "protocol": "grbl",
            "allow_motion": True,
            "job": {},
        }
    )
    assert panel.park_button.isEnabled()
    assert not jog_group.isEnabled()
    assert "Home / park" in jog_group.toolTip()

    jog_requests: list[tuple[float, float, float]] = []
    panel.jogRequested.connect(
        lambda dx, dy, feed: jog_requests.append((dx, dy, feed))
    )
    panel.set_status(
        {
            "connected": True,
            "armed": False,
            "protocol": "grbl",
            "allow_motion": True,
            "coordinate_reference_ready": True,
            "jog_ready": True,
            "max_travel_feed_mm_min": 1200.0,
            "job": {},
        }
    )
    assert jog_group.isEnabled()
    assert panel.jog_speed.maximum() == 1200.0
    panel.jog_step.setCurrentIndex(1)
    panel.jog_right.click()
    assert jog_requests == [(1.0, 0.0, 1200.0)]

    panel.set_status(
        {
            "connected": True,
            "armed": True,
            "protocol": "grbl",
            "allow_motion": True,
            "coordinate_reference_ready": True,
            "jog_ready": True,
            "job": {},
        }
    )
    assert not jog_group.isEnabled()
    assert "Disarm" in jog_group.toolTip()

    panel.set_status(
        {
            "connected": True,
            "armed": False,
            "protocol": "grbl",
            "allow_motion": True,
            "controller_reconnect_required": True,
            "job": {},
        }
    )
    assert "RECONNECT REQUIRED" in panel.state_label.text()
    assert not panel.park_button.isEnabled()
    assert not jog_group.isEnabled()
    assert "disconnect and reconnect" in panel.park_button.toolTip().lower()

    panel.set_status(
        {
            "connected": True,
            "armed": False,
            "protocol": "grbl",
            "allow_motion": True,
            "job": {},
        }
    )
    assert panel.park_button.isEnabled()

    panel.set_busy(True)
    assert not panel.park_button.isEnabled()
    assert not jog_group.isEnabled()
    panel.set_status(
        {
            "connected": True,
            "armed": False,
            "protocol": "grbl",
            "allow_motion": True,
            "job": {},
        }
    )
    assert not panel.park_button.isEnabled()
    panel.set_busy(False)
    assert panel.park_button.isEnabled()

    park_requests: list[bool] = []
    panel.parkRequested.connect(lambda: park_requests.append(True))
    panel.park_button.click()
    assert park_requests == [True]

    panel.close()
    panel.deleteLater()
