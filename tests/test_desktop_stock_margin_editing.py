from __future__ import annotations

# Select the headless Qt backend before any desktop module imports PySide6.
# ruff: noqa: E402, I001
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop.main_window import _StockMarginDialog
from laser_aligner.desktop.controls import MeasurementSpinBox
from laser_aligner.desktop.stock_layout_bar import StockLayoutToolBar


@pytest.fixture(scope="module")
def application() -> Iterator[QtWidgets.QApplication]:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


@pytest.mark.parametrize("text,expected", [(".25", 0.25), ("2.75", 2.75), ("0.1 in", 2.54)])
@pytest.mark.parametrize("commit", ["button", "return"])
def test_custom_stock_margin_can_clear_replace_and_commit_decimal(
    application: QtWidgets.QApplication, text: str, expected: float, commit: str,
) -> None:
    parent = QtWidgets.QWidget()
    dialog = _StockMarginDialog(50.0, parent)
    try:
        dialog.show()
        dialog.margin.setFocus()
        application.processEvents()

        editor = dialog.margin.lineEdit()
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_A, QtCore.Qt.KeyboardModifier.ControlModifier)
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
        assert not dialog.margin.hasAcceptableInput()
        assert dialog.margin.value() == 50.0
        QtTest.QTest.keyClicks(editor, text)
        assert dialog.margin.hasAcceptableInput()
        assert dialog.margin.value() == 50.0
        if commit == "return":
            QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Return)
        else:
            ok = dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
            QtTest.QTest.mouseClick(ok, QtCore.Qt.MouseButton.LeftButton)
        assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
        assert dialog.margin.value() == pytest.approx(expected)
    finally:
        dialog.close()
        parent.close()
        dialog.deleteLater()
        parent.deleteLater()
        application.processEvents()


def test_custom_stock_margin_rejects_incomplete_draft_and_cancel(
    application: QtWidgets.QApplication,
) -> None:
    parent = QtWidgets.QWidget()
    dialog = _StockMarginDialog(50.0, parent)
    try:
        dialog.show()
        dialog.margin.setFocus()
        application.processEvents()
        editor = dialog.margin.lineEdit()
        editor.selectAll()
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
        QtTest.QTest.keyClicks(editor, ".")
        ok = dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        QtTest.QTest.mouseClick(ok, QtCore.Qt.MouseButton.LeftButton)
        assert dialog.isVisible()
        assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
        assert dialog.margin.value() == 50.0
        cancel = dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        QtTest.QTest.mouseClick(cancel, QtCore.Qt.MouseButton.LeftButton)
        assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
    finally:
        dialog.close()
        parent.close()
        dialog.deleteLater()
        parent.deleteLater()
        application.processEvents()



@pytest.mark.parametrize("button_name,signal_name", [
    ("center_horizontal_button", "centerHorizontalRequested"),
    ("center_vertical_button", "centerVerticalRequested"),
    ("rotate_button", "snapRotationRequested"),
    ("fit_button", "fitRequested"),
])
def test_stock_layout_click_commits_current_numeric_edit_before_action(
    application, button_name, signal_name,
):
    window = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(window)
    spin = MeasurementSpinBox()
    spin.setRange(0, 100)
    spin.setValue(10)
    toolbar = StockLayoutToolBar()
    layout.addWidget(spin)
    layout.addWidget(toolbar)
    toolbar.set_context(has_stock=True, selection_count=1)
    observed = []
    getattr(toolbar, signal_name).connect(lambda *_: observed.append(spin.value()))
    try:
        window.show()
        spin.setFocus()
        application.processEvents()
        spin.selectAll()
        QtTest.QTest.keyClicks(spin, "40")
        assert spin.value() == 10
        button = getattr(toolbar, button_name)
        QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton,
                               pos=QtCore.QPoint(4, button.height() // 2))
        assert observed == [40]
    finally:
        window.close()
        window.deleteLater()
        application.processEvents()
