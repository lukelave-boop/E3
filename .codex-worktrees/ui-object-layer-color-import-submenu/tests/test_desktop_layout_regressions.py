from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.template_designer import GridTemplateDesignerDialog
from laser_aligner.desktop.theme import DARK_STYLESHEET


_TEXT_PAINT_RESERVE_PX = 8


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _spec() -> dict[str, object]:
    return {
        "name": "Eight by two rounded rectangles",
        "description": "Red labels",
        "rows": 8,
        "columns": 2,
        "width_mm": 78.0,
        "height_mm": 21.0,
        "corner_radius_mm": 3.0,
        "spacing_mode": "gap",
        "horizontal_spacing_mm": 9.6,
        "vertical_spacing_mm": 3.0,
    }


def _use_large_windows_text(widget: QtWidgets.QWidget) -> None:
    # Windows display scaling normally scales controls and text together, but the
    # separate accessibility text-size setting can increase font metrics after a
    # layout was designed. Thirteen points is a realistic stress level for this
    # 10-point interface and catches exact-fit button sizing without being extreme.
    widget.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")


def _assert_button_text_has_paint_reserve(button: QtWidgets.QPushButton) -> None:
    option = QtWidgets.QStyleOptionButton()
    button.initStyleOption(option)
    content_rect = button.style().subElementRect(
        QtWidgets.QStyle.SubElement.SE_PushButtonContents,
        option,
        button,
    )
    text_width = option.fontMetrics.horizontalAdvance(button.text())
    assert content_rect.width() >= text_width + _TEXT_PAINT_RESERVE_PX, (
        f"{button.text()!r} has {content_rect.width()} px of content width for "
        f"{text_width} px of text; reserve at least {_TEXT_PAINT_RESERVE_PX} px "
        "for Windows font-weight and glyph-rendering differences"
    )
    assert content_rect.height() >= option.fontMetrics.height()


@pytest.mark.parametrize("editing", [False, True], ids=["save", "update"])
def test_grid_designer_footer_actions_do_not_clip_with_large_windows_text(
    qt_application: QtWidgets.QApplication,
    editing: bool,
) -> None:
    dialog = GridTemplateDesignerDialog(initial_spec=_spec(), editing=editing)
    _use_large_windows_text(dialog)
    dialog.resize(940, 640)
    dialog.show()
    qt_application.processEvents()

    expected = "Update template" if editing else "Save template"
    assert dialog.save_button.text() == expected
    for button in (
        dialog.cancel_button,
        dialog.add_project_button,
        dialog.save_button,
    ):
        _assert_button_text_has_paint_reserve(button)

    dialog.close()
    dialog.deleteLater()


def test_grid_designer_form_has_no_hidden_horizontal_overflow_with_large_text(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(initial_spec=_spec())
    _use_large_windows_text(dialog)
    dialog.resize(940, 640)
    dialog.show()
    qt_application.processEvents()

    form_scroll = dialog.findChild(QtWidgets.QScrollArea, "inspectorScroll")
    assert form_scroll is not None
    assert (
        form_scroll.horizontalScrollBarPolicy()
        == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert form_scroll.horizontalScrollBar().maximum() == 0, (
        "The designer disables horizontal scrolling, so its form page must fit "
        "the viewport even when Windows uses larger UI text"
    )

    dialog.close()
    dialog.deleteLater()


def test_compact_grid_designer_keeps_wrapped_status_visible_with_large_text(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        GridTemplateDesignerDialog,
        "_available_screen_size",
        lambda self: QtCore.QSize(600, 430),
    )
    dialog = GridTemplateDesignerDialog(initial_spec=_spec(), editing=True)
    _use_large_windows_text(dialog)
    dialog.show()
    qt_application.processEvents()

    assert dialog.size() == QtCore.QSize(588, 382)
    assert dialog.footprint_status.height() >= dialog.footprint_status.heightForWidth(
        dialog.footprint_status.width()
    )
    status_bottom = dialog.footprint_status.mapTo(
        dialog,
        dialog.footprint_status.rect().bottomRight(),
    ).y()
    assert status_bottom < dialog.cancel_button.mapTo(dialog, QtCore.QPoint()).y()
    for button in (
        dialog.cancel_button,
        dialog.add_project_button,
        dialog.save_button,
    ):
        _assert_button_text_has_paint_reserve(button)

    dialog.close()
    dialog.deleteLater()
