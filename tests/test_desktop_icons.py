from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop icon tests")

from PySide6 import QtGui, QtWidgets

from laser_aligner.desktop.icons import (
    ACTION_ICON_NAMES,
    action_icon,
    apply_action_icons,
    available_icon_names,
    make_icon,
)
from laser_aligner.desktop.theme import DARK_STYLESHEET, DRAFTING_COLORS


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def test_every_public_glyph_renders_nonempty_pixmap(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    names = available_icon_names()
    assert len(names) >= 25

    for name in names:
        icon = make_icon(name, size=22)
        assert not icon.isNull(), name
        pixmap = icon.pixmap(22, 22)
        assert not pixmap.isNull(), name
        image = pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_ARGB32)
        assert any(
            QtGui.QColor.fromRgba(image.pixel(x, y)).alpha() > 0
            for y in range(image.height())
            for x in range(image.width())
        ), name


def test_action_icon_mapping_applies_known_icons_only(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    actions = {
        "new": QtGui.QAction("New"),
        "rectangle": QtGui.QAction("Rectangle"),
        "about": QtGui.QAction("About"),
    }

    updated = apply_action_icons(actions, size=20)

    assert updated == ("new", "rectangle")
    assert not actions["new"].icon().isNull()
    assert not actions["rectangle"].icon().isNull()
    assert actions["about"].icon().isNull()
    assert not action_icon("run").isNull()
    assert "stop" in ACTION_ICON_NAMES


def test_invalid_icon_requests_fail_clearly(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    with pytest.raises(KeyError, match="Unknown desktop icon"):
        make_icon("not-a-real-tool")
    with pytest.raises(ValueError, match="at least 8 pixels"):
        make_icon("new", size=4)
    with pytest.raises(KeyError, match="No icon mapping"):
        action_icon("about")


def test_theme_keeps_compact_chrome_and_light_drafting_contract() -> None:
    assert 'font-size: 9pt' in DARK_STYLESHEET
    assert 'QToolBar#drawingToolbar' in DARK_STYLESHEET
    assert 'QDockWidget::title' in DARK_STYLESHEET
    assert 'padding: 3px 6px' in DARK_STYLESHEET
    assert 'min-height: 20px' in DARK_STYLESHEET
    assert DRAFTING_COLORS["bed"] == "#FAFAFA"
    assert DRAFTING_COLORS["minor_grid"] != DRAFTING_COLORS["major_grid"]
    assert DRAFTING_COLORS["outside"] != DRAFTING_COLORS["bed"]
