from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets

from laser_aligner.config import LaserSettings
from laser_aligner.project import OperationLayer, SceneObject, load_project, save_project
from laser_aligner.project.toolpath import generate_project_gcode
from tests.test_desktop_job_async import _dispose, _window


@pytest.fixture
def editor(tmp_path, monkeypatch):
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window, errors, _notices = _window(tmp_path, monkeypatch)
    first_layer = window.document.layers[0]
    first_layer.speed_mm_min = 1500
    first_layer.power_percent = 0
    first_layer.output_enabled = True
    second_layer = OperationLayer(name="Second cut", speed_mm_min=3000, power_percent=0)
    window.document.layers.append(second_layer)
    first = SceneObject.rectangle(first_layer.id, name="First shape")
    second = SceneObject.rectangle(first_layer.id, name="Second shape")
    first.transform.x_mm = first.transform.y_mm = 50
    second.transform.x_mm = second.transform.y_mm = 120
    window.document.add_object(first)
    window.document.add_object(second)
    window._refresh_document([first.id])
    try:
        yield application, window, first, second, first_layer, second_layer
        assert not errors
    finally:
        _dispose(application, window)


@pytest.mark.parametrize("control", ["cuts_row", "color_tile", "cuts_dropdown", "object_dropdown"])
def test_layer_assignment_changes_only_target_and_survives_undo_save_and_generation(editor, tmp_path, control):
    app, window, first, second, old, new = editor
    original_revision = window.document.revision
    if control == "cuts_row":
        window.layer_panel.layer_list.setCurrentRow(len(window.document.layers) - 1)
    elif control == "color_tile":
        window.palette._buttons[new.id].click()
    elif control == "cuts_dropdown":
        combo = window.layer_panel.layer_combo
        combo.setCurrentIndex(combo.findData(new.id))
        combo.activated.emit(combo.currentIndex())
    else:
        # Row assignment remains local even when another object is selected.
        window.workspace.select_objects([second.id])
        tree = window.object_panel.tree
        item = next(
            tree.topLevelItem(row) for row in range(tree.topLevelItemCount())
            if tree.topLevelItem(row).data(0, QtCore.Qt.ItemDataRole.UserRole) == first.id
        )
        combo = tree.itemWidget(item, 1).findChild(QtWidgets.QComboBox, "objectLayerSelector")
        combo.setCurrentIndex(combo.findData(new.id))
        combo.activated.emit(combo.currentIndex())
    app.processEvents()
    assert first.layer_id == new.id
    assert second.layer_id == old.id
    assert window.document.revision > original_revision
    assert old.speed_mm_min == 1500
    window.workspace.select_objects([second.id])
    assert window.layer_panel.current_layer_id() == old.id
    assert window.layer_panel.layer_combo.currentData() == old.id
    assert window.layer_panel.speed_spin.value() == 1500
    assert first.layer_id == new.id
    window.history.undo()
    assert first.layer_id == old.id
    assert second.layer_id == old.id
    window.history.redo()
    assert first.layer_id == new.id
    path = tmp_path / "assignments.e3laser"
    save_project(window.document, path)
    restored = load_project(path)
    assert restored.get_object(first.id).layer_id == new.id
    assert restored.get_object(second.id).layer_id == old.id
    program = generate_project_gcode(restored, LaserSettings())
    assert "F1500" in program.text
    assert "F3000" in program.text


def test_explicit_multi_selection_assigns_all_selected_but_not_other_shapes(editor):
    app, window, first, second, old, new = editor
    third = SceneObject.rectangle(old.id, name="Unselected")
    window.document.add_object(third)
    window._refresh_document([first.id, second.id])
    window.palette._buttons[new.id].click()
    app.processEvents()
    assert first.layer_id == second.layer_id == new.id
    assert third.layer_id == old.id
    revision = window.document.revision
    window.palette._buttons[new.id].click()
    app.processEvents()
    assert window.document.revision == revision


def test_queued_assignment_uses_selection_at_click_time(editor):
    app, window, first, second, old, new = editor
    window.palette._buttons[new.id].click()
    window.workspace.select_objects([second.id])
    app.processEvents()
    assert first.layer_id == new.id
    assert second.layer_id == old.id


@pytest.mark.parametrize("column", [3, 4])
def test_layer_output_and_visibility_checkboxes_do_not_reassign_shapes(editor, column):
    app, window, first, second, old, new = editor
    tree = window.layer_panel.layer_list
    row = tree.topLevelItem(len(window.document.layers) - 1)
    tree.setCurrentItem(row, column)
    row.setCheckState(column, QtCore.Qt.CheckState.Unchecked)
    app.processEvents()
    assert first.layer_id == second.layer_id == old.id
    updated_layer = window.document.get_layer(new.id)
    assert not (updated_layer.output_enabled if column == 3 else updated_layer.visible)
    combo = window.layer_panel.layer_combo
    combo.setCurrentIndex(combo.findData(new.id))
    combo.activated.emit(combo.currentIndex())
    app.processEvents()
    assert first.layer_id == new.id
    assert second.layer_id == old.id
