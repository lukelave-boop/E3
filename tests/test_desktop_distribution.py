from __future__ import annotations

# Choose the headless Qt platform before importing desktop modules.
# ruff: noqa: E402, I001
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for distribution dialog tests")
from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.distribution_dialog import DistributionDialog
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.project import Bounds, CommandStack, ProjectDocument, SceneObject


@pytest.fixture(scope="module")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def project():
    document = ProjectDocument.new(work_area=Bounds(0, 0, 100, 100))
    objects = [
        SceneObject.rectangle(document.active_layer_id, center=(x, y), width_mm=10, height_mm=10)
        for x, y in [(10, 10), (20, 30), (60, 80)]
    ]
    for item in objects:
        document.add_object(item)
    return document, objects


def test_dialog_defaults_to_bed_and_cancel_does_not_edit(application, project):
    document, objects = project
    before = document.to_dict()
    dialog = DistributionDialog(document, [item.id for item in objects], horizontal=True)
    try:
        dialog.show()
        application.processEvents()
        assert dialog.target.currentData() == "bed"
        assert dialog.apply_button.isEnabled()
        assert "3 will move" in dialog.status.text()
        assert "left and right" in dialog.explanation.text()
        dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).click()
        assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
        assert document.to_dict() == before
    finally:
        dialog.close()


def test_rectangle_picker_excludes_boundary_from_distribution(application, project):
    document, objects = project
    boundary = SceneObject.rectangle(document.active_layer_id, center=(50, 50), width_mm=100, height_mm=80)
    document.add_object(boundary)
    dialog = DistributionDialog(document, [objects[0].id, objects[1].id, boundary.id], horizontal=False)
    try:
        dialog.target.setCurrentIndex(dialog.target.findData("rectangle"))
        assert not dialog.apply_button.isEnabled()
        assert "Choose a boundary" in dialog.status.text()
        dialog.boundary.setCurrentIndex(dialog.boundary.findData(boundary.id))
        assert dialog.apply_button.isEnabled()
        assert "bottom and top" in dialog.explanation.text()
        dialog.apply_button.click()
        assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
        assert set(dialog.transforms) == {objects[0].id, objects[1].id}
        assert objects[0].transform.y_mm == 10  # Dialog returns the edit; history applies it.
        assert [dialog.transforms[item.id].y_mm for item in objects[:2]] == pytest.approx([35, 65])
    finally:
        dialog.close()


def test_dialog_explains_too_few_objects_and_no_movement(application, project):
    document, objects = project
    dialog = DistributionDialog(document, [objects[0].id, objects[1].id], horizontal=True)
    try:
        dialog.target.setCurrentIndex(dialog.target.findData("selection"))
        assert not dialog.apply_button.isEnabled()
        assert "at least three" in dialog.status.text()
        objects[1].transform.x_mm = 35
        dialog.selected.append(objects[2].id)
        dialog._refresh()
        assert not dialog.apply_button.isEnabled()
        assert "already distributed" in dialog.status.text()
    finally:
        dialog.close()


def test_accept_revalidates_layout_if_document_changes(application, project):
    document, objects = project
    dialog = DistributionDialog(document, [item.id for item in objects], horizontal=True)
    try:
        assert dialog.apply_button.isEnabled()
        document.work_area = Bounds(0, 0, 20, 20)
        dialog.accept()
        assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
        assert not dialog.apply_button.isEnabled()
        assert "do not fit" in dialog.status.text()
        assert dialog.transforms == {}
    finally:
        dialog.close()


class _DistributionHarness(QtWidgets.QMainWindow):
    distribute_selection = E3MainWindow.distribute_selection

    def __init__(self, document, selected):
        super().__init__()
        self.document = document
        self.selected = selected
        self.history = CommandStack()
        self.notices = []
        self.workspace = SimpleNamespace(
            selected_object_ids=lambda: list(self.selected),
            select_objects=lambda ids: setattr(self, "selected", list(ids)),
        )

    def show_notice(self, message):
        self.notices.append(message)


@pytest.mark.parametrize("horizontal", [True, False])
@pytest.mark.parametrize("accept", [True, False])
def test_main_window_routes_dialog_through_one_undoable_command(application, project, horizontal, accept):
    document, objects = project
    selected = [item.id for item in objects]
    window = _DistributionHarness(document, selected)
    before = [item.transform.to_dict() for item in objects]
    handled = []

    def finish_dialog():
        dialog = window.findChild(DistributionDialog)
        if dialog is not None:
            handled.append(True)
            dialog.accept() if accept else dialog.reject()

    timer = QtCore.QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(finish_dialog)
    timer.start()
    try:
        window.distribute_selection(horizontal=horizontal)
        assert handled
        assert window.selected == selected
        if accept:
            assert window.history.depth == 1
            assert [item.transform.to_dict() for item in objects] != before
            assert window.history.undo()
        else:
            assert window.history.depth == 0
        assert [item.transform.to_dict() for item in objects] == before
    finally:
        timer.stop()
        window.close()


def test_empty_selection_provides_notice_without_dialog(application, project):
    window = _DistributionHarness(project[0], [])
    try:
        window.distribute_selection(horizontal=True)
        assert window.notices == ["Select the objects to distribute first"]
        assert window.findChild(DistributionDialog) is None
    finally:
        window.close()
