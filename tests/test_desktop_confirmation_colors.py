from __future__ import annotations

import gc
import os
from collections import Counter

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.dialogs import install_confirmation_button_colors
from laser_aligner.desktop.theme import apply_dark_theme


@pytest.fixture(scope="session")
def application():
    # The application owns native event filters. Keep its Python wrapper alive
    # until pytest finishes instead of collecting it between parameter cases.
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def app(application):
    previous = application.styleSheet()
    previous_filter = application.property("e3ConfirmationButtonColorFilter")
    existing_widgets = set(application.topLevelWidgets())
    apply_dark_theme(application)
    yield application
    # close() only hides a widget. Destroy our native dialogs while both the
    # application and its event filter are still alive, including after failure.
    for widget in set(application.topLevelWidgets()) - existing_widgets:
        widget.close()
        widget.deleteLater()
    application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    if previous_filter is None:
        event_filter = application.property("e3ConfirmationButtonColorFilter")
        application.removeEventFilter(event_filter)
        application.setProperty("e3ConfirmationButtonColorFilter", None)
        event_filter.deleteLater()
        application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
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


def test_filter_and_dialogs_survive_collection_and_explicit_destruction(app):
    event_filter = install_confirmation_button_colors(app)
    destroyed = []
    for _ in range(3):
        dialog = QtWidgets.QMessageBox()
        dialog.destroyed.connect(lambda: destroyed.append(True))
        dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes)
        dialog.show()
        app.processEvents()
        gc.collect()
        assert install_confirmation_button_colors(app) is event_filter
        button = dialog.button(QtWidgets.QMessageBox.StandardButton.Yes)
        assert button.property("confirmationAnswer") == "yes"
        QtCore.QTimer.singleShot(0, button.click)
        assert dialog.exec() == int(QtWidgets.QMessageBox.StandardButton.Yes)
        dialog.deleteLater()
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    assert destroyed == [True, True, True]
