from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.dialogs import install_modal_dialog_first_paint_fix
from laser_aligner.desktop.theme import apply_dark_theme


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    apply_dark_theme(application)
    yield application
    application.processEvents()


def test_modal_message_box_content_is_visible_and_repainted_after_first_show(
    qt_application: QtWidgets.QApplication,
) -> None:
    event_filter = install_modal_dialog_first_paint_fix(qt_application)
    scheduled: list[QtWidgets.QMessageBox] = []
    completed: list[QtWidgets.QMessageBox] = []
    event_filter.repaintScheduled.connect(scheduled.append)
    event_filter.repaintCompleted.connect(completed.append)
    parent = QtWidgets.QWidget()
    parent.show()
    dialog = QtWidgets.QMessageBox(
        QtWidgets.QMessageBox.Icon.Information,
        "Bed mapping required",
        "Open Machine Setup and complete bed mapping.",
        QtWidgets.QMessageBox.StandardButton.Ok,
        parent,
    )
    dialog.setModal(True)

    dialog.show()
    qt_application.processEvents()

    label = dialog.findChild(QtWidgets.QLabel, "qt_msgbox_label")
    button = dialog.button(QtWidgets.QMessageBox.StandardButton.Ok)
    assert scheduled == [dialog]
    assert completed == [dialog]
    assert label is not None and label.isVisibleTo(dialog)
    assert button is not None and button.isVisibleTo(dialog)
    assert not dialog.grab().isNull()

    dialog.accept()
    qt_application.processEvents()
    assert parent.isEnabled()
    parent.close()


def test_modal_message_box_exec_result_is_unchanged(
    qt_application: QtWidgets.QApplication,
) -> None:
    install_modal_dialog_first_paint_fix(qt_application)
    parent = QtWidgets.QWidget()
    parent.show()
    dialog = QtWidgets.QMessageBox(
        QtWidgets.QMessageBox.Icon.Question,
        "Confirm",
        "Continue?",
        QtWidgets.QMessageBox.StandardButton.Yes
        | QtWidgets.QMessageBox.StandardButton.No,
        parent,
    )
    QtCore.QTimer.singleShot(
        0, lambda: dialog.done(int(QtWidgets.QMessageBox.StandardButton.Yes))
    )

    result = dialog.exec()

    assert result == int(QtWidgets.QMessageBox.StandardButton.Yes)
    assert parent.isEnabled()
    parent.close()
