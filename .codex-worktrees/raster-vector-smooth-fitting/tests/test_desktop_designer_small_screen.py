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


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    previous_stylesheet = application.styleSheet()
    application.setStyle("Fusion")
    application.setStyleSheet(DARK_STYLESHEET)
    yield application
    application.setStyleSheet(previous_stylesheet)
    application.processEvents()


def _set_available_size(
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    height: int,
) -> None:
    monkeypatch.setattr(
        GridTemplateDesignerDialog,
        "_available_screen_size",
        lambda self: QtCore.QSize(width, height),
    )


def _assert_action_text_fits(button: QtWidgets.QPushButton) -> None:
    option = QtWidgets.QStyleOptionButton()
    button.initStyleOption(option)
    content_rect = button.style().subElementRect(
        QtWidgets.QStyle.SubElement.SE_PushButtonContents,
        option,
        button,
    )
    text_width = option.fontMetrics.horizontalAdvance(button.text())
    assert content_rect.width() >= text_width + 8
    assert content_rect.height() >= option.fontMetrics.height()


def test_designer_limits_initial_size_to_available_screen(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_available_size(monkeypatch, 720, 500)

    dialog = GridTemplateDesignerDialog(editing=True)
    dialog.show()
    qt_application.processEvents()

    assert dialog.size() == QtCore.QSize(688, 452)
    assert dialog.minimumSize() == QtCore.QSize(588, 382)
    assert dialog.width() < 720
    assert dialog.height() < 500

    splitter = dialog.findChild(QtWidgets.QSplitter)
    assert splitter is not None
    assert splitter.orientation() == QtCore.Qt.Orientation.Horizontal
    assert splitter.count() == 2
    assert all(size > 0 for size in splitter.sizes())
    assert dialog.preview.minimumSize() == QtCore.QSize(260, 40)

    dialog.close()
    dialog.deleteLater()


def test_compact_designer_keeps_forms_and_footer_inside_window(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_available_size(monkeypatch, 600, 430)

    dialog = GridTemplateDesignerDialog(editing=True)
    dialog.show()
    qt_application.processEvents()

    assert dialog.size() == QtCore.QSize(588, 382)
    assert dialog.minimumSize() == QtCore.QSize(588, 382)
    form_scroll = dialog.findChild(QtWidgets.QScrollArea, "inspectorScroll")
    assert form_scroll is not None
    assert form_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.description_edit.minimumHeight() == 64
    assert dialog.description_edit.maximumHeight() == 96

    preview_page = dialog.preview.parentWidget()
    assert not dialog.preview.geometry().intersects(
        dialog.footprint_status.geometry()
    )
    assert (
        dialog.footprint_status.geometry().bottom()
        <= preview_page.contentsRect().bottom()
    )

    for button in (
        dialog.cancel_button,
        dialog.add_project_button,
        dialog.save_button,
    ):
        _assert_action_text_fits(button)
        right_edge = button.mapTo(dialog, QtCore.QPoint(button.width(), 0)).x()
        assert right_edge <= dialog.contentsRect().right()

    dialog.close()
    dialog.deleteLater()
