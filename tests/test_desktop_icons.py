from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from pathlib import Path

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
    assert not action_icon("preview_job").isNull()
    assert "stop" in ACTION_ICON_NAMES


def test_preview_action_receives_monitor_icon(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    action = QtGui.QAction("Preview generated job")

    updated = apply_action_icons({"preview_job": action}, size=20)

    assert updated == ("preview_job",)
    assert not action.icon().isNull()
    assert "preview" in available_icon_names()


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


def test_dev_test_application_icon_is_distinct_at_taskbar_sizes(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    assets = (
        Path(__file__).resolve().parents[1]
        / "laser_aligner"
        / "desktop"
        / "assets"
    )
    normal_path = assets / "e3-positioning-system.svg"
    dev_path = assets / "e3-dev-test.svg"

    assert normal_path.is_file()
    assert dev_path.is_file()
    assert normal_path.read_bytes() != dev_path.read_bytes()

    for size in (16, 32):
        normal_image = (
            QtGui.QIcon(str(normal_path))
            .pixmap(size, size)
            .toImage()
            .convertToFormat(QtGui.QImage.Format.Format_ARGB32)
        )
        dev_image = (
            QtGui.QIcon(str(dev_path))
            .pixmap(size, size)
            .toImage()
            .convertToFormat(QtGui.QImage.Format.Format_ARGB32)
        )
        assert not normal_image.isNull()
        assert not dev_image.isNull()

        normal_pixels = [
            normal_image.pixelColor(x, y)
            for y in range(size)
            for x in range(size)
        ]
        dev_pixels = [
            dev_image.pixelColor(x, y)
            for y in range(size)
            for x in range(size)
        ]
        differing_pixels = sum(
            normal.rgba() != development.rgba()
            for normal, development in zip(normal_pixels, dev_pixels, strict=True)
        )
        assert differing_pixels >= size * size // 4

        def is_badge_orange(color: QtGui.QColor) -> bool:
            return (
                color.alpha() >= 192
                and color.red() >= 210
                and color.green() >= 90
                and color.blue() <= 110
            )

        assert sum(map(is_badge_orange, dev_pixels)) >= size * size // 12
        assert sum(map(is_badge_orange, dev_pixels)) > 3 * sum(
            map(is_badge_orange, normal_pixels)
        )


def test_theme_keeps_compact_chrome_and_light_drafting_contract() -> None:
    assert 'font-size: 9pt' in DARK_STYLESHEET
    assert 'QToolBar#drawingToolbar' in DARK_STYLESHEET
    assert 'QDockWidget::title' in DARK_STYLESHEET
    assert 'padding: 3px 6px' in DARK_STYLESHEET
    assert 'min-height: 20px' in DARK_STYLESHEET
    assert 'QCheckBox::indicator:checked' in DARK_STYLESHEET
    assert 'border-radius: 8px' in DARK_STYLESHEET
    assert '#20C978' in DARK_STYLESHEET
    assert DRAFTING_COLORS["bed"] == "#FAFAFA"
    assert DRAFTING_COLORS["minor_grid"] != DRAFTING_COLORS["major_grid"]
    assert DRAFTING_COLORS["outside"] != DRAFTING_COLORS["bed"]
