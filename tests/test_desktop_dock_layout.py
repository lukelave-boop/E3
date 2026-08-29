from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from itertools import combinations
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop dock tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.config import ZAxisSettings
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.theme import DARK_STYLESHEET


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _MaterialDatabaseStub:
    def list(self, query: str = "") -> list[object]:
        del query
        return []

    def list_for_profiles(self, **_kwargs: object) -> list[object]:
        return []


class _DockLayoutHarness(QtWidgets.QMainWindow):
    _dock = E3MainWindow._dock
    _create_docks = E3MainWindow._create_docks
    _create_status_bar = E3MainWindow._create_status_bar
    _update_status_bar_layout = E3MainWindow._update_status_bar_layout

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(DARK_STYLESHEET)
        self.resize(1600, 900)
        self.setDockOptions(
            QtWidgets.QMainWindow.DockOption.AllowNestedDocks
            | QtWidgets.QMainWindow.DockOption.AllowTabbedDocks
        )
        self.setCentralWidget(QtWidgets.QWidget())
        self.window_menu = QtWidgets.QMenu(self)
        self.material_database = _MaterialDatabaseStub()
        self.runtime = SimpleNamespace(
            context=SimpleNamespace(
                simulation_workspace_frame_supported=False,
                machine_identity=SimpleNamespace(
                    machine_profile_id="simulator",
                    tool_head_profile_id="simulated-laser-head",
                ),
            ),
            settings=SimpleNamespace(
                camera=SimpleNamespace(controls={}),
                machine=SimpleNamespace(z_axis=ZAxisSettings()),
            ),
        )
        self._create_docks()
        self._create_status_bar()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "job_progress"):
            self._update_status_bar_layout()


def test_default_layout_uses_unified_right_sidebar_and_global_progress(
    qt_application: QtWidgets.QApplication,
) -> None:
    window = _DockLayoutHarness()
    window.show()
    qt_application.processEvents()
    qt_application.processEvents()

    right = QtCore.Qt.DockWidgetArea.RightDockWidgetArea
    bottom = QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
    assert window.dockWidgetArea(window.layer_dock) == right
    assert window.dockWidgetArea(window.console_dock) == bottom
    assert window.corner(QtCore.Qt.Corner.BottomRightCorner) == right

    assert window.layer_dock.widget() is window.inspector_tabs
    assert window.console_dock.widget() is window.console_panel
    assert [
        window.inspector_tabs.tabText(index)
        for index in range(window.inspector_tabs.count())
    ] == [
        "Cuts",
        "Camera",
        "Objects",
        "Shape",
        "Templates",
        "Trace",
        "Machine",
        "Material Recipes",
    ]
    assert [
        window.inspector_tabs.tabToolTip(index)
        for index in range(window.inspector_tabs.count())
    ] == [
        "Cuts / Layers",
        "Camera controls",
        "Objects",
        "Shape Properties",
        "Templates",
        "Trace",
        "Machine",
        "Material Recipes",
    ]
    assert window.inspector_tabs.widget(6).widget() is window.machine_panel
    assert window.inspector_tabs.widget(7).widget() is window.material_panel

    assert window.layer_dock.isVisible()
    assert not window.console_dock.isVisible()
    assert window.console_dock.isHidden()
    for obsolete_name in (
        "preview_dock",
        "inspector_dock",
        "job_tabs",
        "job_panel",
        "gcode_preview",
    ):
        assert not hasattr(window, obsolete_name)

    central = window.centralWidget().geometry()
    layer = window.layer_dock.geometry()
    status = window.statusBar().geometry()
    progress_top_left = window.job_progress.mapTo(window, QtCore.QPoint(0, 0))
    progress = QtCore.QRect(progress_top_left, window.job_progress.size())
    tolerance = 5
    assert layer.top() <= central.top() + tolerance
    assert abs(layer.bottom() - central.bottom()) <= tolerance
    assert central.right() < layer.left()
    assert central.bottom() < status.top()
    assert layer.bottom() < status.top()
    assert window.contentsRect().contains(status)
    assert status.contains(progress)
    assert abs(status.bottom() - window.contentsRect().bottom()) <= tolerance
    assert window.job_progress.currentWidget() is window.job_progress.progress
    assert window.job_progress.progress.format().startswith("Execution")
    assert layer.width() >= window.contentsRect().width() * 0.25
    assert central.width() >= window.contentsRect().width() * 0.65
    assert central.height() >= window.contentsRect().height() * 0.85

    window.close()
    window.deleteLater()
    qt_application.processEvents()


@pytest.mark.parametrize("size", [(1080, 780), (900, 680)])
def test_compact_unified_sidebar_and_progress_keep_controls_reachable(
    qt_application: QtWidgets.QApplication,
    size: tuple[int, int],
) -> None:
    original_font = QtGui.QFont(qt_application.font())
    large_font = QtGui.QFont(original_font)
    large_font.setPointSize(13)
    qt_application.setFont(large_font)
    window: _DockLayoutHarness | None = None
    try:
        window = _DockLayoutHarness()
        window.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
        window.resize(*size)
        window.show()
        qt_application.processEvents()
        qt_application.processEvents()

        assert (window.width(), window.height()) == size
        assert window.minimumSizeHint().width() <= size[0]
        regions = {
            "canvas": window.centralWidget().geometry(),
            "sidebar": window.layer_dock.geometry(),
            "status": window.statusBar().geometry(),
        }
        for name, rectangle in regions.items():
            assert window.contentsRect().contains(rectangle), name
        for (left_name, left), (right_name, right) in combinations(
            regions.items(),
            2,
        ):
            assert not left.intersects(right), f"{left_name} intersects {right_name}"

        assert regions["canvas"].width() >= window.contentsRect().width() * 0.3
        available_height = window.contentsRect().height() - regions["status"].height()
        assert regions["canvas"].height() >= available_height * 0.9
        assert abs(regions["canvas"].height() - regions["sidebar"].height()) <= 5

        for index in range(window.inspector_tabs.count()):
            window.inspector_tabs.setCurrentIndex(index)
            qt_application.processEvents()
            scroll = window.inspector_tabs.current_scroll_area()
            assert scroll is not None
            assert scroll.horizontalScrollBar().maximum() == 0
            assert scroll.widget().width() <= scroll.viewport().width()

        window.inspector_tabs.setCurrentIndex(0)
        qt_application.processEvents()
        design_scroll = window.inspector_tabs.current_scroll_area()
        assert design_scroll is not None
        for control in (
            window.layer_panel.color_button,
            window.layer_panel.speed_spin,
            window.layer_panel.passes_spin,
            window.layer_panel.power_spin,
            window.layer_panel.output_check,
            window.layer_panel.visible_check,
        ):
            top_left = control.mapTo(
                design_scroll.viewport(),
                control.rect().topLeft(),
            )
            bottom_right = control.mapTo(
                design_scroll.viewport(),
                control.rect().bottomRight(),
            )
            assert top_left.x() >= 0
            assert bottom_right.x() < design_scroll.viewport().width()

        generate_controls = (
            (
                "templates",
                window.template_panel.apply_button,
                window.template_panel.generate_button,
            ),
            (
                "trace",
                window.trace_panel.create_button,
                window.trace_panel.generate_button,
            ),
        )
        for panel_key, create_button, generate_button in generate_controls:
            window.inspector_tabs.select_panel(panel_key)
            qt_application.processEvents()
            scroll = window.inspector_tabs.current_scroll_area()
            assert scroll is not None
            assert generate_button.geometry().top() > create_button.geometry().bottom()
            for button in (create_button, generate_button):
                scroll.ensureWidgetVisible(button)
                qt_application.processEvents()
                left = button.mapTo(scroll.viewport(), button.rect().topLeft()).x()
                right = button.mapTo(
                    scroll.viewport(),
                    button.rect().bottomRight(),
                ).x()
                assert left >= 0, button.text()
                assert right < scroll.viewport().width(), button.text()

        status = window.statusBar().geometry()
        progress_top_left = window.job_progress.mapTo(window, QtCore.QPoint(0, 0))
        progress = QtCore.QRect(progress_top_left, window.job_progress.size())
        assert status.contains(progress)
        assert progress.width() >= window.width() * 0.25
        assert progress.height() <= 24
        assert not window.cursor_label.isVisible()
        assert not window.selection_label.isVisible()
        assert not window.direct_edit_label.isVisible()

        window.runtime_label.setText("camera offline · controller offline")
        window.zoom_label.setText("Zoom 174%")
        window.job_progress.set_preparing(
            True,
            "Building exact previews · 40%",
            completed=400,
            total=1000,
        )
        window._update_status_bar_layout()
        qt_application.processEvents()
        preparation = window.job_progress.preparation_progress
        assert preparation.format() == "Preparing 40%"
        assert preparation.width() >= (
            preparation.fontMetrics().horizontalAdvance(preparation.format()) + 8
        )
        assert "Building exact previews · 40%" in window.job_progress.toolTip()
        if not window.runtime_label.isVisible():
            assert "camera offline · controller offline" in window.statusBar().toolTip()

        window.job_progress.set_preparing(False)
        window.job_progress.set_job_status(
            {
                "running": True,
                "total_lines": 100,
                "completed_lines": 100,
                "phase": "draining",
            }
        )
        window._update_status_bar_layout()
        qt_application.processEvents()
        execution = window.job_progress.progress
        assert execution.format() == "Finishing · motion"
        assert execution.width() >= (
            execution.fontMetrics().horizontalAdvance(execution.format()) + 8
        )
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
            qt_application.processEvents()
        qt_application.setFont(original_font)


@pytest.mark.parametrize("width", [1199, 1200, 1400, 1600, 1900])
def test_live_status_details_never_displace_readable_global_progress(
    qt_application: QtWidgets.QApplication,
    width: int,
) -> None:
    window = _DockLayoutHarness()
    window.direct_edit_label.setText("Move  •  Size  •  Rotate")
    window.cursor_label.setText("Honeycomb X 287.233  Y -0.000 mm")
    window.selection_label.setText("0 objects selected")
    window.zoom_label.setText("Zoom 195%")
    window.runtime_label.setText("camera offline · grbl connected")
    window.job_progress.set_job_status(
        {
            "running": True,
            "total_lines": 100,
            "completed_lines": 100,
            "phase": "draining",
        }
    )
    window.resize(width, 780)
    window._update_status_bar_layout()
    window.show()
    qt_application.processEvents()
    qt_application.processEvents()

    progress = window.job_progress.progress
    assert progress.width() >= (
        progress.fontMetrics().horizontalAdvance(progress.format()) + 8
    )
    for label in (window.runtime_label, window.zoom_label):
        if label.isVisible():
            assert label.width() >= label.sizeHint().width()
    for label in (
        window.direct_edit_label,
        window.cursor_label,
        window.selection_label,
    ):
        if label.isVisible():
            assert label.width() >= label.sizeHint().width()

    if width < 1900:
        assert not window.direct_edit_label.isVisible()
        assert not window.cursor_label.isVisible()
        assert not window.selection_label.isVisible()
    else:
        assert window.direct_edit_label.isVisible()
        assert window.cursor_label.isVisible()
        assert window.selection_label.isVisible()

    window.close()
    window.deleteLater()
    qt_application.processEvents()


def _visible_status_widget_rectangles(
    window: _DockLayoutHarness,
) -> dict[str, QtCore.QRect]:
    status_bar = window.statusBar()
    widgets = {
        "direct edit": window.direct_edit_label,
        "cursor": window.cursor_label,
        "selection": window.selection_label,
        "progress": window.job_progress,
        "zoom": window.zoom_label,
        "runtime": window.runtime_label,
    }
    return {
        name: QtCore.QRect(
            widget.mapTo(status_bar, QtCore.QPoint(0, 0)),
            widget.size(),
        )
        for name, widget in widgets.items()
        if widget.isVisible()
    }


def _assert_status_widgets_do_not_overlap(window: _DockLayoutHarness) -> None:
    rectangles = _visible_status_widget_rectangles(window)
    for (left_name, left), (right_name, right) in combinations(
        rectangles.items(),
        2,
    ):
        assert not left.intersects(right), f"{left_name} overlaps {right_name}"


@pytest.mark.parametrize("width", [900, 1900])
def test_temporary_status_messages_reserve_space_and_restore_details(
    qt_application: QtWidgets.QApplication,
    width: int,
) -> None:
    window = _DockLayoutHarness()
    window.direct_edit_label.setText("Move  •  Size  •  Rotate")
    window.cursor_label.setText("Honeycomb X 287.233  Y -0.000 mm")
    window.selection_label.setText("12 objects selected")
    window.zoom_label.setText("Zoom 195%")
    window.runtime_label.setText(
        "camera offline · controller offline · motion disabled"
    )
    window.job_progress.set_job_status(
        {
            "running": True,
            "total_lines": 100,
            "completed_lines": 100,
            "phase": "draining",
        }
    )
    window.resize(width, 780)
    window.show()
    qt_application.processEvents()
    qt_application.processEvents()

    baseline_visibility = {
        widget: widget.isVisible()
        for widget in (
            window.direct_edit_label,
            window.cursor_label,
            window.selection_label,
            window.zoom_label,
            window.runtime_label,
        )
    }
    baseline_progress_maximum = window.job_progress.maximumWidth()
    _assert_status_widgets_do_not_overlap(window)

    message = (
        "Temporary camera recovery completed; the corrected overlay is ready"
    )
    window.statusBar().showMessage(message)
    qt_application.processEvents()
    qt_application.processEvents()

    assert window.statusBar().currentMessage() == message
    assert window.job_progress.isVisible()
    assert window.job_progress.currentWidget() is window.job_progress.progress
    assert window.job_progress.progress.format() == "Finishing · motion"
    assert window.job_progress.width() >= (
        window.job_progress.progress.fontMetrics().horizontalAdvance(
            window.job_progress.progress.format()
        )
        + 8
    )
    assert not window.direct_edit_label.isVisible()
    assert not window.cursor_label.isVisible()
    assert not window.selection_label.isVisible()
    if width == 900:
        assert not window.zoom_label.isVisible()
        assert not window.runtime_label.isVisible()
    else:
        assert window.zoom_label.isVisible()
        assert window.runtime_label.isVisible()

    status_bar = window.statusBar()
    progress_left = window.job_progress.mapTo(
        status_bar,
        QtCore.QPoint(0, 0),
    ).x()
    message_width = status_bar.fontMetrics().horizontalAdvance(message)
    available_message_width = progress_left - status_bar.contentsRect().left()
    if (
        message_width
        + window.job_progress.minimumWidth()
        + 80
        <= status_bar.contentsRect().width()
    ):
        assert available_message_width >= message_width
    else:
        assert available_message_width >= status_bar.contentsRect().width() * 0.7
        assert message in status_bar.toolTip()
    _assert_status_widgets_do_not_overlap(window)

    status_bar.clearMessage()
    qt_application.processEvents()
    qt_application.processEvents()

    assert status_bar.currentMessage() == ""
    assert window.job_progress.maximumWidth() == baseline_progress_maximum
    for widget, was_visible in baseline_visibility.items():
        assert widget.isVisible() is was_visible
    _assert_status_widgets_do_not_overlap(window)

    window.close()
    window.deleteLater()
    qt_application.processEvents()
