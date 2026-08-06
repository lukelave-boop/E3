from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.panels import JobPanel, LayerPanel, MachinePanel
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


def test_job_panel_uses_compact_rows_and_preserves_action_states(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = JobPanel()
    _show_compact(panel, qt_application, 220)
    generated: list[bool] = []
    framed: list[bool] = []
    started: list[bool] = []
    stopped: list[bool] = []
    panel.generateRequested.connect(lambda: generated.append(True))
    panel.frameRequested.connect(lambda: framed.append(True))
    panel.startRequested.connect(lambda: started.append(True))
    panel.stopRequested.connect(lambda: stopped.append(True))

    assert panel.generate_button.geometry().top() == panel.frame_button.geometry().top()
    assert panel.start_button.geometry().top() == panel.stop_button.geometry().top()
    assert not panel.pause_button.isEnabled()
    for button in (
        panel.generate_button,
        panel.frame_button,
        panel.start_button,
        panel.pause_button,
        panel.stop_button,
    ):
        _assert_inside_panel(button, panel)

    panel.generate_button.click()
    panel.frame_button.click()
    panel.start_button.click()
    panel.stop_button.click()
    assert generated == [True]
    assert framed == [True]
    assert started == [True]
    assert stopped == [True]

    panel.close()
    panel.deleteLater()


def test_machine_panel_is_dense_without_relaxing_motion_or_stop_semantics(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = MachinePanel()
    _show_compact(panel, qt_application, 390)

    jog_group = next(
        group
        for group in panel.findChildren(QtWidgets.QGroupBox)
        if group.title().startswith("Jogging")
    )
    assert not jog_group.isEnabled()
    assert "physical emergency stop" in panel.safety_note.text()
    assert panel.stop_button.isEnabled()
    assert panel.connect_button.geometry().top() == panel.park_button.geometry().top()
    for button in (
        panel.connect_button,
        panel.disconnect_button,
        panel.park_button,
        panel.stop_button,
    ):
        _assert_inside_panel(button, panel)

    panel.set_status(
        {
            "connected": True,
            "armed": False,
            "protocol": "grbl",
            "allow_motion": False,
            "job": {},
        }
    )
    assert not panel.connect_button.isEnabled()
    assert panel.disconnect_button.isEnabled()
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

    stop_requests: list[bool] = []
    panel.stopRequested.connect(lambda: stop_requests.append(True))
    panel.stop_button.click()
    assert stop_requests == [True]

    panel.close()
    panel.deleteLater()
