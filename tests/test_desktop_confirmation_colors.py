from __future__ import annotations

import os
from collections import Counter

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.dialogs import install_confirmation_button_colors
from laser_aligner.desktop.theme import apply_dark_theme


@pytest.fixture
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    previous = application.styleSheet()
    apply_dark_theme(application)
    yield application
    application.setStyleSheet(previous)
    application.processEvents()


@pytest.mark.parametrize("default", ["Yes", "No", "Cancel"])
@pytest.mark.parametrize("clicked", ["Yes", "No", "Cancel"])
def test_answers_keep_colors_and_results_independent_of_default(app, default, clicked):
    standard = QtWidgets.QMessageBox.StandardButton
    dialog = QtWidgets.QMessageBox()
    dialog.setText("Continue?")
    dialog.setStandardButtons(standard.Yes | standard.No | standard.Cancel)
    dialog.setDefaultButton(getattr(standard, default))
    dialog.show()
    app.processEvents()
    expected = {"Yes": "#237a43", "No": "#a92f3e", "Cancel": "#444444"}
    for name, color in expected.items():
        button = dialog.button(getattr(standard, name))
        assert button.property("confirmationAnswer") == name.lower()
        # The fill dominates regardless of DPI, label glyphs, or focus outline.
        image = button.grab().toImage()
        pixels = Counter(
            image.pixelColor(x, y).name()
            for x in range(image.width()) for y in range(image.height())
        )
        assert pixels.most_common(1)[0][0] == color
    assert dialog.defaultButton() is dialog.button(getattr(standard, default))
    QtCore.QTimer.singleShot(0, dialog.button(getattr(standard, clicked)).click)
    assert dialog.exec() == int(getattr(standard, clicked))
    dialog.close()


def test_static_question_api_and_escape_keep_no_default_and_cancel(app):
    standard = QtWidgets.QMessageBox.StandardButton
    observed = []

    def dismiss():
        dialog = app.activeModalWidget()
        observed.append(dialog.button(standard.No).property("confirmationAnswer"))
        observed.append(dialog.defaultButton() is dialog.button(standard.No))
        dialog.close()

    QtCore.QTimer.singleShot(0, dismiss)
    result = QtWidgets.QMessageBox.question(
        None, "Confirm", "Continue?", standard.Yes | standard.No | standard.Cancel, standard.No
    )
    assert observed == ["no", True]
    assert result == standard.Cancel


@pytest.mark.parametrize("widget_type", [QtWidgets.QMessageBox, QtWidgets.QDialogButtonBox])
def test_custom_yes_no_roles_and_unrelated_buttons(app, widget_type):
    widget = widget_type()
    yes = widget.addButton("Proceed", widget_type.ButtonRole.YesRole)
    no = widget.addButton("Keep existing", widget_type.ButtonRole.NoRole)
    okay = widget.addButton(widget_type.StandardButton.Ok)
    widget.show()
    app.processEvents()
    assert yes.property("confirmationAnswer") == "yes"
    assert no.property("confirmationAnswer") == "no"
    assert okay.property("confirmationAnswer") == ""
    widget.close()


def test_filter_installation_is_idempotent(app):
    assert install_confirmation_button_colors(app) is install_confirmation_button_colors(app)
