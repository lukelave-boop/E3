from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.controls import PanelScrollArea
from laser_aligner.desktop.panels import CameraPanel, TracePanel, TransformPanel
from laser_aligner.desktop.theme import DARK_STYLESHEET


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _show_in_narrow_inspector(
    panel: QtWidgets.QWidget,
    application: QtWidgets.QApplication,
) -> PanelScrollArea:
    scroll = PanelScrollArea(panel)
    scroll.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    scroll.resize(360, 700)
    scroll.show()
    application.processEvents()
    return scroll


def _assert_no_hidden_horizontal_content(
    scroll: PanelScrollArea,
    panel: QtWidgets.QWidget,
) -> None:
    assert (
        scroll.horizontalScrollBarPolicy()
        == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert scroll.horizontalScrollBar().maximum() == 0
    assert panel.width() <= scroll.viewport().width()
    for button in panel.findChildren(QtWidgets.QAbstractButton):
        if not button.isVisible() or not button.text():
            continue
        top_left = button.mapTo(scroll.viewport(), button.rect().topLeft())
        bottom_right = button.mapTo(scroll.viewport(), button.rect().bottomRight())
        assert top_left.x() >= 0, button.text()
        assert bottom_right.x() < scroll.viewport().width(), button.text()


@pytest.mark.parametrize(
    "panel_type",
    [CameraPanel, TracePanel],
    ids=["camera", "trace"],
)
def test_action_heavy_panels_fit_narrow_inspector_with_large_text(
    qt_application: QtWidgets.QApplication,
    panel_type: type[QtWidgets.QWidget],
) -> None:
    panel = panel_type()
    scroll = _show_in_narrow_inspector(panel, qt_application)

    _assert_no_hidden_horizontal_content(scroll, panel)

    scroll.close()
    scroll.deleteLater()


def test_transform_summary_wraps_long_names_without_widening_inspector(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TransformPanel()
    summary = (
        "Extremely long customer label object name that must wrap without "
        "widening the inspector"
    )
    panel._set_summary(summary)
    scroll = _show_in_narrow_inspector(panel, qt_application)

    _assert_no_hidden_horizontal_content(scroll, panel)
    assert panel.summary.wordWrap()
    assert panel.summary.height() > panel.summary.fontMetrics().height()
    assert panel.summary.toolTip() == summary

    scroll.close()
    scroll.deleteLater()
