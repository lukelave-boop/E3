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

from laser_aligner.desktop.main_window import E3MainWindow


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _MaterialDatabaseStub:
    def list(self, query: str = "") -> list[object]:
        del query
        return []


class _DockLayoutHarness(QtWidgets.QMainWindow):
    _dock = E3MainWindow._dock
    _create_docks = E3MainWindow._create_docks

    def __init__(self) -> None:
        super().__init__()
        self.resize(1600, 900)
        self.setDockOptions(
            QtWidgets.QMainWindow.DockOption.AllowNestedDocks
            | QtWidgets.QMainWindow.DockOption.AllowTabbedDocks
        )
        self.setCentralWidget(QtWidgets.QWidget())
        self.window_menu = QtWidgets.QMenu(self)
        self.material_database = _MaterialDatabaseStub()
        self.runtime = SimpleNamespace(
            context=SimpleNamespace(simulation_workspace_frame_supported=False),
            settings=SimpleNamespace(camera=SimpleNamespace(controls={})),
        )
        self._create_docks()


def test_default_docks_match_drawn_three_region_layout(
    qt_application: QtWidgets.QApplication,
) -> None:
    window = _DockLayoutHarness()
    window.show()
    qt_application.processEvents()
    qt_application.processEvents()

    right = QtCore.Qt.DockWidgetArea.RightDockWidgetArea
    bottom = QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
    assert window.dockWidgetArea(window.layer_dock) == right
    assert window.dockWidgetArea(window.preview_dock) == bottom
    assert window.dockWidgetArea(window.inspector_dock) == bottom
    assert window.corner(QtCore.Qt.Corner.BottomRightCorner) == right

    assert window.layer_dock.widget() is window.inspector_tabs
    assert window.preview_dock.widget() is window.gcode_preview
    assert window.inspector_dock.widget() is window.job_tabs
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
    ]
    assert [
        window.job_tabs.tabText(index)
        for index in range(window.job_tabs.count())
    ] == ["Laser", "Machine", "Material Library"]

    assert window.layer_dock.isVisible()
    assert window.preview_dock.isVisible()
    assert window.inspector_dock.isVisible()
    assert not window.console_dock.isVisible()
    assert window.console_dock.isHidden()
    assert window.inspector_dock not in window.tabifiedDockWidgets(window.preview_dock)

    central = window.centralWidget().geometry()
    layer = window.layer_dock.geometry()
    preview = window.preview_dock.geometry()
    runtime = window.inspector_dock.geometry()
    tolerance = 5
    assert layer.top() <= central.top() + tolerance
    assert abs(layer.bottom() - preview.bottom()) <= tolerance
    assert abs(preview.top() - runtime.top()) <= tolerance
    assert abs(preview.bottom() - runtime.bottom()) <= tolerance
    assert preview.right() < runtime.left()
    assert runtime.right() < layer.left()
    assert central.bottom() < preview.top()
    assert layer.width() >= window.contentsRect().width() * 0.32
    assert preview.width() < runtime.width()
    assert preview.height() < window.contentsRect().height() * 0.4

    window.close()
    window.deleteLater()
    qt_application.processEvents()


@pytest.mark.parametrize("size", [(1080, 780), (900, 680)])
def test_compact_default_docks_stay_disjoint_and_controls_remain_reachable(
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
        window.resize(*size)
        window.show()
        qt_application.processEvents()
        qt_application.processEvents()

        assert (window.width(), window.height()) == size
        assert window.minimumSizeHint().width() <= size[0]
        regions = {
            "canvas": window.centralWidget().geometry(),
            "design": window.layer_dock.geometry(),
            "gcode": window.preview_dock.geometry(),
            "runtime": window.inspector_dock.geometry(),
        }
        for name, rectangle in regions.items():
            assert window.contentsRect().contains(rectangle), name
        for (left_name, left), (right_name, right) in combinations(
            regions.items(),
            2,
        ):
            assert not left.intersects(right), f"{left_name} intersects {right_name}"

        assert regions["canvas"].width() >= 500
        assert regions["canvas"].height() >= 400

        for tabs in (window.inspector_tabs, window.job_tabs):
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                qt_application.processEvents()
                scroll = tabs.current_scroll_area()
                assert scroll is not None
                assert scroll.horizontalScrollBar().maximum() == 0
                assert scroll.widget().width() <= scroll.viewport().width()

        window.inspector_tabs.setCurrentIndex(0)
        window.job_tabs.setCurrentIndex(0)
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

        runtime_scroll = window.job_tabs.current_scroll_area()
        assert runtime_scroll is not None
        assert runtime_scroll.horizontalScrollBar().maximum() == 0
        for button in (
            window.job_panel.generate_button,
            window.job_panel.start_button,
            window.job_panel.stop_button,
        ):
            runtime_scroll.ensureWidgetVisible(button)
            qt_application.processEvents()
            left = button.mapTo(runtime_scroll.viewport(), button.rect().topLeft()).x()
            right = button.mapTo(
                runtime_scroll.viewport(),
                button.rect().bottomRight(),
            ).x()
            assert left >= 0, button.text()
            assert right < runtime_scroll.viewport().width(), button.text()
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
            qt_application.processEvents()
        qt_application.setFont(original_font)
