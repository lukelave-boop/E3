from __future__ import annotations

# Select the offscreen Qt platform before desktop imports.
# ruff: noqa: E402, I001
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop.template_panel import TemplatePanel


@pytest.fixture(scope="module")
def application() -> Iterator[QtWidgets.QApplication]:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


@pytest.mark.parametrize("field,text,button_text,key,expected", [
    ("x_spin", "25", "X+", "center_x_mm", 25.1),
    ("y_spin", "25", "Y+", "center_y_mm", 25.1),
    ("rotation_spin", "2.5", "R+", "rotation_deg", 2.6),
    ("nudge_step", ".5", "X+", "center_x_mm", 10.5),
    ("x_spin", "9999", "X+", "center_x_mm", 10.1),
    ("x_spin", "", "X+", "center_x_mm", 10.1),
    ("nudge_step", "0", "X+", "center_x_mm", 10.1),
])
def test_mouse_nudge_uses_completed_draft_and_rejects_invalid_draft(
    application: QtWidgets.QApplication,
    field: str, text: str, button_text: str, key: str, expected: float,
) -> None:
    panel = TemplatePanel()
    panel.set_templates([{"id": "numeric-test", "name": "Numeric test"}])
    panel.set_placement(10.0, 10.0, 0.0)
    # Keep the actual nudge buttons inside the exposed offscreen window.
    panel.resize(600, 1400)
    try:
        panel.show()
        spin = getattr(panel, field)
        previous = spin.value()
        spin.setFocus()
        application.processEvents()
        assert spin.hasFocus()
        editor = spin.lineEdit()
        editor.selectAll()
        QtTest.QTest.keyClick(editor, QtCore.Qt.Key.Key_Backspace)
        QtTest.QTest.keyClicks(editor, text)
        assert spin.value() == previous
        assert spin.hasPendingEdit()
        updates: list[dict[str, object]] = []
        panel.placementChanged.connect(updates.append)
        button = next(
            child for child in panel.findChildren(QtWidgets.QToolButton)
            if child.text() == button_text
        )
        clicks: list[bool] = []
        button.clicked.connect(lambda: clicks.append(True))
        QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
        application.processEvents()
        assert clicks == [True]
        assert panel.placement()[key] == pytest.approx(expected)
        assert updates[-1][key] == pytest.approx(expected)
        assert not spin.hasPendingEdit()
    finally:
        panel.close()
        panel.deleteLater()
        application.processEvents()
