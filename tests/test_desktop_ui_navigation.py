from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop navigation tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.core import CoreRuntime
from laser_aligner.desktop.controls import PanelScrollArea
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import LayerPanel
from laser_aligner.desktop.theme import DARK_STYLESHEET
from laser_aligner.project import ProjectDocument


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    original_font = application.font()
    font_id = -1
    if os.name == "nt":
        font_id = QtGui.QFontDatabase.addApplicationFont(
            str(Path(os.environ["WINDIR"]) / "Fonts" / "segoeui.ttf")
        )
    application.setFont(QtGui.QFont("Segoe UI", 10))
    yield application
    application.setFont(original_font)
    if font_id >= 0:
        QtGui.QFontDatabase.removeApplicationFont(font_id)
    application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    application.processEvents()


def _flush(application: QtWidgets.QApplication) -> None:
    application.processEvents()
    application.processEvents()


def _dispose(application: QtWidgets.QApplication, widget: QtWidgets.QWidget) -> None:
    widget.close()
    widget.deleteLater()
    application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    application.processEvents()


def test_labeled_layer_actions_preserve_selection_and_request_signals(qt_application):
    document = ProjectDocument.new("Navigation")
    document.add_layer(name="Engrave")
    document.add_layer(name="Cut")
    panel = LayerPanel(max_work_feed_mm_min=6000)
    added, removed, moved, edits = [], [], [], []
    panel.addLayerRequested.connect(lambda: added.append(True))
    panel.removeLayerRequested.connect(removed.append)
    panel.moveLayerRequested.connect(lambda key, delta: moved.append((key, delta)))
    panel.layerEdited.connect(lambda key, values: edits.append((key, values)))
    try:
        selected_id = document.layers[1].id
        panel.set_document(document, selected_id)
        assert panel.output_check.text() == "Include in job"
        assert panel.visible_check.text() == "Show on canvas"
        for control, text in ((panel.add_button, "Add"), (panel.remove_button, "Remove"),
                              (panel.up_button, "Up"), (panel.down_button, "Down")):
            assert control.text() == text
            assert control.isEnabled()
            control.click()
        assert added == [True]
        assert removed == [selected_id]
        assert moved == [(selected_id, -1), (selected_id, 1)]
        assert panel.current_layer_id() == selected_id
        panel.output_check.click()
        assert edits[-1][0] == selected_id
        assert edits[-1][1]["output_enabled"] is False
        panel.visible_check.click()
        assert edits[-1][0] == selected_id
        assert edits[-1][1]["visible"] is False
        panel.set_document(ProjectDocument.new("Only layer"))
        assert not panel.remove_button.isEnabled()
        assert not panel.up_button.isEnabled()
        assert not panel.down_button.isEnabled()
        assert panel.add_button.isEnabled()
    finally:
        _dispose(qt_application, panel)


@pytest.mark.parametrize("width", [290, 360])
def test_layer_actions_and_selectors_fit_narrow_inspector(qt_application, width):
    panel = LayerPanel(max_work_feed_mm_min=6000)
    panel.set_document(ProjectDocument.new("Navigation"))
    scroll = PanelScrollArea(panel)
    scroll.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    scroll.resize(width, 640)
    scroll.show()
    _flush(qt_application)
    try:
        assert scroll.width() == width
        assert scroll.horizontalScrollBar().maximum() == 0
        labels = {label.buddy(): label.text() for label in panel.findChildren(QtWidgets.QLabel)
                  if label.buddy() is not None}
        assert labels[panel.layer_combo] == "Layer"
        assert labels[panel.mode_combo] == "Operation"
        controls = (panel.layer_combo, panel.mode_combo, panel.add_button,
                    panel.remove_button, panel.up_button, panel.down_button,
                    panel.output_check, panel.visible_check)
        for control in controls:
            scroll.ensureWidgetVisible(control)
            _flush(qt_application)
            left = control.mapTo(scroll.viewport(), control.rect().topLeft()).x()
            right = control.mapTo(scroll.viewport(), control.rect().bottomRight()).x()
            assert 0 <= left <= right < scroll.viewport().width(), control.objectName()
            if isinstance(control, QtWidgets.QPushButton):
                assert control.width() >= control.fontMetrics().horizontalAdvance(control.text()) + 12
    finally:
        _dispose(qt_application, scroll)


@pytest.mark.parametrize("size", [(900, 680), (1080, 780), (1600, 900)])
def test_labeled_main_shell_keeps_canvas_and_stop_reachable(qt_application, tmp_path, monkeypatch, size):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    payload = json.loads((Path(__file__).parents[1] / "config/default.json").read_text())
    payload["app"]["data_dir"] = str(tmp_path / "data")
    payload["app"]["open_browser"] = False
    payload["camera"]["autostart"] = False
    payload["machine"]["allow_motion"] = False
    payload["machine"]["port"] = "COM_TEST"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    original_font = qt_application.font()
    qt_application.setFont(QtGui.QFont("Segoe UI", 13))
    # tests/conftest.py redirects even explicitly named native QSettings to temp INI files.
    assert QtCore.QSettings("E3", "E3 Positioning System").format() == QtCore.QSettings.Format.IniFormat
    window = E3MainWindow(CoreRuntime.from_config(path, hardware_enabled=False))
    window.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    window.resize(*size)
    window.show()
    deadline = time.monotonic() + 10
    try:
        while not window.runtime.running or window.controller.has_active_tasks:
            qt_application.processEvents()
            assert time.monotonic() < deadline, "Simulator startup did not finish"
            time.sleep(0.002)
        window.resize(*size)
        _flush(qt_application)
        assert (window.width(), window.height()) == size
        rail = window.findChild(QtWidgets.QToolBar, "drawingToolbar")
        assert rail.toolButtonStyle() == QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        for key in ("select_tool", "rectangle", "polygon", "trace_objects", "grid_template_designer", "fit_selection", "zoom_out"):
            action = window.actions[key]
            control = rail.widgetForAction(action)
            assert control is not None and control.isVisibleTo(window), key
            assert action.iconText()
            assert rail.rect().contains(control.geometry()), key
            assert control.width() >= control.fontMetrics().horizontalAdvance(action.iconText()) + 24, key
        assert window.actions["rectangle"].shortcut() == QtGui.QKeySequence("Alt+R")
        job_toolbar = window.findChild(QtWidgets.QToolBar, "jobToolbar")
        for key in ("refresh_camera", "generate", "preview_job"):
            assert window.actions[key] in job_toolbar.actions()
            assert window.actions[key].iconText()
        units = window.context_bar.units_combo
        option = QtWidgets.QStyleOptionComboBox()
        units.initStyleOption(option)
        text_rect = units.style().subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_ComboBox, option,
            QtWidgets.QStyle.SubControl.SC_ComboBoxEditField, units,
        )
        assert text_rect.width() >= units.fontMetrics().horizontalAdvance(units.currentText())
        stop = window.runtime_strip.stop_button
        assert stop.isVisibleTo(window)
        assert stop.isEnabled()
        assert window.safety_toolbar.rect().contains(stop.mapTo(window.safety_toolbar, stop.rect().topLeft()))
        assert window.safety_toolbar.rect().contains(stop.mapTo(window.safety_toolbar, stop.rect().bottomRight()))
        assert window.centralWidget().width() >= 260
        assert window.centralWidget().height() >= 250
        assert window.runtime.settings.machine.allow_motion is False
        assert window.runtime.context.machine.status()["armed"] is False
    finally:
        window.history.mark_clean()
        window._cancel_job_preparation("Navigation test cleanup")
        window._cancel_job_render()
        window.controller.stop()
        window._closing = True
        _dispose(qt_application, window)
        qt_application.setFont(original_font)
