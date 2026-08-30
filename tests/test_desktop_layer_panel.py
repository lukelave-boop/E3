from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop.controls import PanelScrollArea
from laser_aligner.desktop.main_window import (
    E3MainWindow,
    LAYER_PALETTE_COLORS,
    LayerPaletteBar,
)
from laser_aligner.desktop.panels import LayerPanel, MaterialPanel, ObjectPanel
from laser_aligner.desktop.theme import DARK_STYLESHEET
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.materials import MaterialDatabase, MaterialPreset
from laser_aligner.project import (
    CommandStack,
    LayerMode,
    NativePathGeometry,
    ObjectKind,
    OperationLayer,
    PathCubicSegment,
    PathSubpath,
    ProjectDocument,
    SceneObject,
    Transform,
)


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _document_with_operations() -> ProjectDocument:
    document = ProjectDocument.new("Operations")
    line = document.layers[0]
    line.name = "Cut outline"
    line.speed_mm_min = 1250.0
    line.power_percent = 42.5
    line.passes = 2
    line.vector_power_correction = -25.0
    line.raster_power_correction = 35.0

    fill = document.add_layer(name="Legacy fill", mode=LayerMode.FILL)
    fill.speed_mm_min = 800.0
    fill.power_percent = 18.0
    fill.output_enabled = False
    fill.visible = False
    return document


def test_layer_panel_summarizes_operations_and_preserves_list_api(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    document = _document_with_operations()
    panel = LayerPanel()

    panel.set_document(document, document.layers[1].id)

    assert panel.layer_list.count() == 2
    assert panel.layer_list.item(0).text() == "Cut outline"
    assert panel.layer_list.item(0).text(1) == "Line"
    assert panel.layer_list.item(0).text(2) == "1250 / 42.5%"
    assert panel.layer_list.item(0).checkState(3) == QtCore.Qt.CheckState.Checked
    assert panel.layer_list.item(1).text(1) == "Fill"
    assert panel.layer_list.item(1).checkState(3) == QtCore.Qt.CheckState.Unchecked
    assert panel.layer_list.item(1).checkState(4) == QtCore.Qt.CheckState.Unchecked
    assert panel.current_layer_id() == document.layers[1].id
    assert not panel.mode_notice.isVisibleTo(panel)
    assert panel.scan_row.isVisibleTo(panel)
    assert "Include this operation" in panel.output_check.toolTip()
    assert panel.vector_correction_spin.value() == 0.0
    assert panel.raster_correction_spin.value() == 0.0

    fill_index = panel.mode_combo.findData(LayerMode.FILL.value)
    raster_index = panel.mode_combo.findData(LayerMode.RASTER.value)
    assert panel.mode_combo.itemText(fill_index) == "Fill"
    assert panel.mode_combo.itemText(raster_index) == "Raster"

    panel.close()
    panel.deleteLater()


def test_bottom_palette_scrolls_many_layers_without_widening_compact_window(
    qt_application: QtWidgets.QApplication,
) -> None:
    host = QtWidgets.QMainWindow()
    host.resize(900, 120)
    palette = LayerPaletteBar()
    toolbar = QtWidgets.QToolBar()
    toolbar.addWidget(palette)
    host.addToolBar(QtCore.Qt.ToolBarArea.BottomToolBarArea, toolbar)
    layers = [
        OperationLayer(name=f"Operation {index + 1:02d}", priority=index)
        for index in range(24)
    ]

    palette.set_layers(layers, layers[0].id)
    host.show()
    qt_application.processEvents()

    assert host.width() == 900
    assert palette.minimumSizeHint().width() < 500
    assert palette._scroll.horizontalScrollBar().maximum() > 0
    assert palette.add_button.isVisibleTo(palette)
    assert palette.add_button.geometry().right() < palette.width()

    scroll_bar = palette._scroll.horizontalScrollBar()
    scroll_bar.setValue(scroll_bar.maximum())
    qt_application.processEvents()
    final_button = palette._buttons[layers[-1].id]
    final_left = final_button.mapTo(
        palette._scroll.viewport(), final_button.rect().topLeft()
    ).x()
    final_right = final_button.mapTo(
        palette._scroll.viewport(), final_button.rect().bottomRight()
    ).x()
    assert final_left >= 0
    assert final_right < palette._scroll.viewport().width()

    host.close()
    host.deleteLater()


def test_unused_palette_swatch_requests_a_matching_new_operation(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    layer = OperationLayer(name="Existing operation")
    palette = LayerPaletteBar()
    requested: list[str] = []
    palette.presetLayerRequested.connect(requested.append)

    palette.set_layers([layer], layer.id)
    palette._preset_buttons[1].click()

    assert len(palette._preset_buttons) == len(LAYER_PALETTE_COLORS)
    assert requested == [LAYER_PALETTE_COLORS[1]]


def test_bottom_palette_describes_fill_and_disabled_operations(
    qt_application: QtWidgets.QApplication,
) -> None:
    layer = OperationLayer(
        name="Legacy fill",
        mode=LayerMode.FILL,
        output_enabled=False,
        visible=False,
    )
    palette = LayerPaletteBar()
    palette.resize(700, 44)
    palette.set_layers([layer], layer.id)
    palette.show()
    qt_application.processEvents()

    tooltip = palette._buttons[layer.id].toolTip()
    assert "Fill toolpath" in tooltip
    assert "Output off · hidden" in tooltip
    assert palette._buttons[layer.id].isEnabled()
    assert palette._buttons[layer.id].isChecked()

    palette.close()
    palette.deleteLater()


def test_layer_panel_inline_state_and_quick_editor_keep_existing_signals(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = _document_with_operations()
    panel = LayerPanel()
    panel.set_document(document)
    edits: list[tuple[str, dict[str, object]]] = []
    selections: list[str] = []
    moves: list[tuple[str, int]] = []
    removals: list[str] = []
    panel.layerEdited.connect(lambda layer_id, values: edits.append((layer_id, values)))
    panel.activeLayerChanged.connect(selections.append)
    panel.moveLayerRequested.connect(lambda layer_id, delta: moves.append((layer_id, delta)))
    panel.removeLayerRequested.connect(removals.append)

    second = panel.layer_list.item(1)
    panel.layer_list.setCurrentRow(1)
    qt_application.processEvents()
    assert selections == [document.layers[1].id]

    second.setCheckState(4, QtCore.Qt.CheckState.Checked)
    qt_application.processEvents()
    assert edits[-1] == (
        document.layers[1].id,
        {"output_enabled": False, "visible": True},
    )

    panel.name_edit.setText("Updated operation")
    panel.name_edit.editingFinished.emit()
    qt_application.processEvents()
    assert edits[-1][0] == document.layers[1].id
    assert edits[-1][1]["name"] == "Updated operation"
    assert edits[-1][1]["mode"] == LayerMode.FILL.value

    panel.up_button.click()
    panel.remove_button.click()
    assert moves == [(document.layers[1].id, -1)]
    assert removals == [document.layers[1].id]

    panel.close()
    panel.deleteLater()


def test_layer_air_assist_edit_is_selected_layer_scoped_and_undoable(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = _document_with_operations()
    first, second = document.layers
    first.air_assist = True
    second.air_assist = False
    second.speed_mm_min = 1234.56
    second.power_percent = 12.34
    second.line_interval_mm = 0.12345
    second.scan_angle_deg = Transform.normalized_rotation(17.89)
    second.overscan_percent = 4.567
    second.vector_power_correction = 0.1234
    second.raster_power_correction = -0.2345
    original_second = second.to_dict()
    panel = LayerPanel()
    history = CommandStack()
    workspace = SimpleNamespace(selected_object_ids=lambda: [])

    def refresh(_selected_ids: list[str] | None = None) -> None:
        panel.set_document(document, second.id)

    harness = SimpleNamespace(
        document=document,
        history=history,
        workspace=workspace,
        _refresh_document=refresh,
    )
    panel.layerEdited.connect(
        lambda layer_id, changes: E3MainWindow._layer_edited(
            harness, layer_id, changes
        ),
        QtCore.Qt.ConnectionType.QueuedConnection,
    )
    refresh()

    assert panel.current_layer_id() == second.id
    assert panel.air_assist_check.text() == "Air assist"
    assert panel.air_assist_check.toolTip() == (
        "Run configured air assist at 100% while this operation is "
        "cutting/engraving."
    )
    assert not panel.air_assist_check.isChecked()

    panel.layer_list.setCurrentRow(0)
    qt_application.processEvents()
    assert panel.current_layer_id() == first.id
    assert panel.air_assist_check.isChecked()
    panel.layer_list.setCurrentRow(1)
    qt_application.processEvents()
    assert panel.current_layer_id() == second.id
    assert not panel.air_assist_check.isChecked()

    panel.air_assist_check.click()
    qt_application.processEvents()

    assert document.get_layer(first.id).air_assist is True
    assert document.get_layer(second.id).air_assist is True
    edited_second = document.get_layer(second.id).to_dict()
    assert {
        key: value for key, value in edited_second.items() if key != "air_assist"
    } == {
        key: value for key, value in original_second.items() if key != "air_assist"
    }
    assert history.depth == 1
    assert history.undo_text == f"Edit {second.name}"

    assert history.undo()
    refresh()
    assert document.get_layer(first.id).air_assist is True
    assert document.get_layer(second.id).air_assist is False
    assert document.get_layer(second.id).to_dict() == original_second
    assert not panel.air_assist_check.isChecked()

    assert history.redo()
    refresh()
    assert document.get_layer(second.id).air_assist is True
    redone_second = document.get_layer(second.id).to_dict()
    assert {
        key: value for key, value in redone_second.items() if key != "air_assist"
    } == {
        key: value for key, value in original_second.items() if key != "air_assist"
    }
    assert panel.air_assist_check.isChecked()

    panel.close()
    panel.deleteLater()


def test_layer_numeric_editor_emits_once_when_the_value_is_committed(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = _document_with_operations()
    panel = LayerPanel()
    panel.set_document(document)
    panel.show()
    qt_application.processEvents()
    edits: list[tuple[str, dict[str, object]]] = []
    panel.layerEdited.connect(
        lambda layer_id, values: edits.append((layer_id, values))
    )

    panel.speed_spin.setFocus()
    panel.speed_spin.lineEdit().selectAll()
    QtTest.QTest.keyClicks(panel.speed_spin.lineEdit(), "2345.6")
    qt_application.processEvents()

    assert edits == []

    QtTest.QTest.keyClick(
        panel.speed_spin,
        QtCore.Qt.Key.Key_Return,
    )
    qt_application.processEvents()

    assert len(edits) == 1
    assert edits[0][0] == document.active_layer_id
    assert edits[0][1]["speed_mm_min"] == pytest.approx(2345.6)

    panel.close()
    panel.deleteLater()


def test_layer_power_correction_editor_emits_exact_values_on_commit(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = _document_with_operations()
    panel = LayerPanel()
    panel.set_document(document, document.layers[0].id)
    edits: list[tuple[str, dict[str, object]]] = []
    panel.layerEdited.connect(
        lambda layer_id, values: edits.append((layer_id, values))
    )

    assert panel.vector_correction_spin.value() == -25.0
    assert panel.raster_correction_spin.value() == 35.0
    panel.vector_correction_spin.setValue(-40.5)
    panel.vector_correction_spin.editingFinished.emit()
    qt_application.processEvents()

    assert edits[-1][1]["vector_power_correction"] == -40.5
    assert edits[-1][1]["raster_power_correction"] == 35.0
    assert "normal GRBL M4" in panel.vector_correction_spin.toolTip()

    panel.close()
    panel.deleteLater()


def test_material_panel_round_trips_and_applies_power_correction(
    qt_application: QtWidgets.QApplication,
    tmp_path,
) -> None:
    database = MaterialDatabase(tmp_path / "materials.sqlite")
    preset = database.save(
        MaterialPreset(
            material="Cardstock",
            name="Corrected",
            vector_power_correction=-15.5,
            raster_power_correction=22.5,
        )
    )
    panel = MaterialPanel(database)
    applied: list[MaterialPreset] = []
    panel.applyPresetRequested.connect(applied.append)
    item = next(
        panel.list.item(index)
        for index in range(panel.list.count())
        if panel.list.item(index).data(QtCore.Qt.ItemDataRole.UserRole) == preset.id
    )
    panel.list.setCurrentItem(item)
    qt_application.processEvents()

    assert panel.vector_correction_spin.value() == -15.5
    assert panel.raster_correction_spin.value() == 22.5
    panel.apply_current()

    assert applied[0].vector_power_correction == -15.5
    assert applied[0].raster_power_correction == 22.5

    panel.close()
    panel.deleteLater()


def test_material_panel_lists_complete_recipes_compatible_first_and_guards_apply(
    qt_application: QtWidgets.QApplication,
    tmp_path,
) -> None:
    database = MaterialDatabase(tmp_path / "scoped-materials.sqlite")
    universal = database.save(
        MaterialPreset(material="Universal", name="Mark")
    )
    incompatible = database.save(
        MaterialPreset(
            material="Other machine",
            name="Engrave",
            machine_profile_id="machine-b",
            tool_head_profile_id="tool-b",
        )
    )
    tool_only = database.save(
        MaterialPreset(
            material="Tool match",
            name="Cut",
            tool_head_profile_id="tool-a",
        )
    )
    exact = database.save(
        MaterialPreset(
            material="Birch plywood",
            name="Detailed raster",
            thickness_mm=3.2,
            mode=LayerMode.RASTER,
            speed_mm_min=1375.0,
            power_percent=28.5,
            passes=3,
            line_interval_mm=0.075,
            scan_angle_deg=37.5,
            overscan_percent=8.25,
            air_assist=True,
            vector_power_correction=-12.5,
            raster_power_correction=21.5,
            recommended_color="#123456",
            machine_profile_id="machine-a",
            tool_head_profile_id="tool-a",
            notes="Complete recipe",
        )
    )
    panel = MaterialPanel(
        database,
        machine_profile_id="machine-a",
        tool_head_profile_id="tool-a",
    )
    applied: list[MaterialPreset] = []
    errors: list[str] = []
    panel.applyPresetRequested.connect(applied.append)
    panel.error.connect(errors.append)

    listed_ids = [
        panel.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        for row in range(panel.list.count())
    ]
    assert listed_ids == [exact.id, tool_only.id, universal.id, incompatible.id]
    exact_item = panel.list.item(0)
    assert "3.2 mm · Raster · 1375 mm/min · 28.5% · 3 passes" in exact_item.text()
    assert "Exact machine + tool" in exact_item.text()
    assert "machine machine-a + tool tool-a" in exact_item.text()

    panel.list.setCurrentItem(exact_item)
    qt_application.processEvents()
    assert panel.scan_angle_spin.value() == 37.5
    assert panel.overscan_spin.value() == 8.25
    assert panel.air_assist_check.isChecked()
    assert panel.vector_correction_spin.value() == -12.5
    assert panel.raster_correction_spin.value() == 21.5
    assert panel.recommended_color_edit.text() == "#123456"
    assert panel.machine_scope_edit.text() == "machine-a"
    assert panel.tool_scope_edit.text() == "tool-a"
    assert panel.apply_button.isEnabled()
    panel.apply_current()
    assert applied == [database.get(exact.id)]

    panel.list.setCurrentItem(panel.list.item(3))
    qt_application.processEvents()
    assert not panel.apply_button.isEnabled()
    panel.apply_current()
    assert applied == [database.get(exact.id)]
    assert errors and "incompatible" in errors[-1].lower()

    panel.set_profile_context("machine-b", "tool-b")
    refreshed_ids = [
        panel.list.item(row).data(QtCore.Qt.ItemDataRole.UserRole)
        for row in range(panel.list.count())
    ]
    assert refreshed_ids == [incompatible.id, universal.id, exact.id, tool_only.id]
    assert panel.apply_button.isEnabled()

    panel.close()
    panel.deleteLater()


def test_main_window_recipe_apply_is_one_authoring_command_without_output_enablement(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    document = ProjectDocument.new("Recipe application")
    layer = document.layers[0]
    layer.name = "Keep authoring name"
    layer.color = "#ABCDEF"
    layer.output_enabled = False
    layer.visible = False
    layer.priority = 7
    first_object = SceneObject.line(
        layer.id,
        center=(10.0, 10.0),
        length_mm=5.0,
    )
    second_object = SceneObject.line(
        layer.id,
        center=(20.0, 20.0),
        length_mm=7.0,
    )
    document.add_object(first_object)
    document.add_object(second_object)
    before = layer.to_dict()
    objects_before = [item.to_dict() for item in document.objects]
    selected_ids = [first_object.id, second_object.id]
    refreshes: list[list[str]] = []
    notices: list[str] = []
    errors: list[str] = []
    identity = SimpleNamespace(
        machine_profile_id="machine-a",
        tool_head_profile_id="tool-a",
    )
    harness = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(machine_identity=identity)),
        document=document,
        active_layer_id=layer.id,
        history=CommandStack(),
        workspace=SimpleNamespace(
            selected_object_ids=lambda: list(selected_ids),
        ),
        _refresh_document=lambda selection=None: refreshes.append(list(selection or [])),
        show_notice=notices.append,
        show_error=errors.append,
    )
    recipe = MaterialPreset(
        material="Birch plywood",
        name="Complete cut",
        thickness_mm=3.0,
        mode=LayerMode.FILL,
        speed_mm_min=875.0,
        power_percent=34.0,
        passes=4,
        line_interval_mm=0.085,
        scan_angle_deg=22.5,
        overscan_percent=6.5,
        air_assist=True,
        vector_power_correction=-18.0,
        raster_power_correction=24.0,
        recommended_color="#102030",
        machine_profile_id="machine-a",
        tool_head_profile_id="tool-a",
    )

    E3MainWindow.apply_material_preset(harness, recipe)

    applied = document.get_layer(layer.id)
    assert harness.history.depth == 1
    assert errors == []
    assert refreshes == [selected_ids]
    assert notices == [
        "Applied Birch plywood \N{MIDDLE DOT} Complete cut to Keep authoring name"
    ]
    assert applied.id == before["id"]
    assert applied.name == before["name"]
    assert applied.color == "#102030"
    assert applied.mode is LayerMode.FILL
    assert applied.speed_mm_min == 875.0
    assert applied.power_percent == 34.0
    assert applied.passes == 4
    assert applied.line_interval_mm == 0.085
    assert applied.scan_angle_deg == 22.5
    assert applied.overscan_percent == 6.5
    assert applied.air_assist is True
    assert applied.vector_power_correction == -18.0
    assert applied.raster_power_correction == 24.0
    assert applied.output_enabled is False
    assert applied.visible is False
    assert applied.priority == 7
    assert [item.to_dict() for item in document.objects] == objects_before

    assert harness.history.undo()
    assert document.get_layer(layer.id).to_dict() == before
    assert harness.history.redo()
    assert document.get_layer(layer.id).to_dict() == applied.to_dict()


def test_main_window_rejects_incompatible_recipe_before_history_mutation(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    document = ProjectDocument.new("Incompatible recipe")
    layer = document.layers[0]
    before = document.to_dict()
    errors: list[str] = []
    harness = SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                machine_identity=SimpleNamespace(
                    machine_profile_id="machine-a",
                    tool_head_profile_id="tool-a",
                )
            )
        ),
        document=document,
        active_layer_id=layer.id,
        history=CommandStack(),
        show_error=errors.append,
    )
    recipe = MaterialPreset(
        material="Acrylic",
        name="Wrong machine",
        machine_profile_id="machine-b",
        tool_head_profile_id="tool-b",
    )

    E3MainWindow.apply_material_preset(harness, recipe)

    assert document.to_dict() == before
    assert harness.history.depth == 0
    assert errors and "incompatible" in errors[-1].lower()


def test_runtime_status_refreshes_recipes_from_running_identity(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    calls: list[tuple[str, str]] = []

    class Sink:
        def __getattr__(self, name: str):
            del name
            return lambda *args, **kwargs: None

    harness = SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                machine_identity=SimpleNamespace(
                    machine_profile_id="running-machine",
                    tool_head_profile_id="running-tool",
                )
            )
        ),
        material_panel=SimpleNamespace(
            set_profile_context=lambda machine, tool: calls.append((machine, tool))
        ),
        camera_panel=Sink(),
        trace_panel=Sink(),
        template_panel=Sink(),
        machine_panel=Sink(),
        runtime_strip=Sink(),
        console_panel=Sink(),
        job_progress=Sink(),
        runtime_label=Sink(),
        _update_status_bar_layout=lambda: None,
        _maybe_start_calibration_capture=lambda machine: None,
    )

    E3MainWindow._runtime_status(
        harness,
        {
            "runtime_state": "running",
            "machine_profile_id": "untrusted-status-machine",
            "tool_head_profile_id": "untrusted-status-tool",
        },
    )

    assert calls == [("running-machine", "running-tool")]


def test_layer_panel_fits_360_pixel_inspector_with_large_text(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = LayerPanel()
    panel.set_document(_document_with_operations())
    scroll = PanelScrollArea(panel)
    scroll.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    scroll.resize(360, 760)
    scroll.show()
    qt_application.processEvents()

    assert (
        scroll.horizontalScrollBarPolicy()
        == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert scroll.horizontalScrollBar().maximum() == 0
    assert panel.width() <= scroll.viewport().width()
    assert (
        sum(
            panel.layer_list.columnWidth(column)
            for column in range(panel.layer_list.columnCount())
        )
        <= panel.layer_list.viewport().width()
    )
    for button in panel.findChildren(QtWidgets.QAbstractButton):
        if not button.isVisible() or not button.text():
            continue
        left = button.mapTo(scroll.viewport(), button.rect().topLeft()).x()
        right = button.mapTo(scroll.viewport(), button.rect().bottomRight()).x()
        assert left >= 0, button.text()
        assert right < scroll.viewport().width(), button.text()

    scroll.close()
    scroll.deleteLater()


def test_layer_panel_exposes_operation_color_editing(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document_with_operations()
    panel = LayerPanel()
    panel.set_document(document)
    edits: list[tuple[str, dict[str, object]]] = []
    panel.layerEdited.connect(
        lambda layer_id, changes: edits.append((layer_id, changes))
    )
    monkeypatch.setattr(
        QtWidgets.QColorDialog,
        "getColor",
        lambda *args, **kwargs: QtGui.QColor("#12ABCD"),
    )

    panel.color_button.click()
    qt_application.processEvents()

    assert panel.color_button.property("layerColor") == document.layers[0].color
    assert edits == [(document.layers[0].id, {"color": "#12abcd"})]

    panel.close()
    panel.deleteLater()


def test_safe_trace_layer_uses_ordinary_color_picker_without_changing_authority(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_layer = OperationLayer(
        name="Artwork trace",
        color="#FF4F9F",
        mode=LayerMode.LINE,
        power_percent=0.0,
        output_enabled=False,
        visible=True,
    )
    document = ProjectDocument(layers=[trace_layer])
    panel = LayerPanel()
    panel.set_document(document, trace_layer.id)
    edits: list[tuple[str, dict[str, object]]] = []
    panel.layerEdited.connect(
        lambda layer_id, changes: edits.append((layer_id, changes))
    )
    monkeypatch.setattr(
        QtWidgets.QColorDialog,
        "getColor",
        lambda *args, **kwargs: QtGui.QColor("#00D9FF"),
    )

    panel.color_button.click()
    qt_application.processEvents()

    assert panel.current_layer_id() == trace_layer.id
    assert edits == [(trace_layer.id, {"color": "#00d9ff"})]
    payload = trace_layer.to_dict()
    payload.update(edits[0][1])
    recolored = OperationLayer.from_dict(payload)
    assert recolored.color == "#00D9FF"
    assert recolored.mode is LayerMode.LINE
    assert recolored.power_percent == 0.0
    assert recolored.output_enabled is False
    assert recolored.visible is True

    panel.close()
    panel.deleteLater()


def _object_layer_row(
    panel: ObjectPanel,
    object_id: str,
) -> tuple[int, QtWidgets.QTreeWidgetItem]:
    for row in range(panel.tree.topLevelItemCount()):
        item = panel.tree.topLevelItem(row)
        if item.data(0, QtCore.Qt.ItemDataRole.UserRole) == object_id:
            return row, item
    raise AssertionError(f"Object row not found: {object_id}")


def _object_layer_color_button(
    panel: ObjectPanel,
    object_id: str,
) -> QtWidgets.QPushButton:
    _row, item = _object_layer_row(panel, object_id)
    cell = panel.tree.itemWidget(item, 1)
    assert cell is not None
    button = cell.findChild(QtWidgets.QPushButton, "objectLayerColorButton")
    assert button is not None
    return button


def test_object_layer_color_button_targets_assigned_layer_and_keeps_name_selection(
    qt_application: QtWidgets.QApplication,
) -> None:
    first_layer = OperationLayer(name="First", color="#D64D4D")
    second_layer = OperationLayer(name="Second", color="#4D6ED6")
    document = ProjectDocument(layers=[first_layer, second_layer])
    first = SceneObject.rectangle(first_layer.id, name="First object")
    second = SceneObject.rectangle(second_layer.id, name="Second object")
    document.add_object(first)
    document.add_object(second)
    original_assignments = {item.id: item.layer_id for item in document.objects}

    panel = ObjectPanel()
    panel.resize(640, 400)
    panel.set_document(document)
    panel.show()
    qt_application.processEvents()
    color_requests: list[str] = []
    selections: list[list[str]] = []
    panel.layerColorEditRequested.connect(color_requests.append)
    panel.selectionRequested.connect(selections.append)

    button = _object_layer_color_button(panel, first.id)
    assert button.property("layerId") == first_layer.id
    assert button.property("layerColor") == first_layer.color
    assert button.isEnabled()
    assert button.minimumWidth() >= 22
    assert button.minimumHeight() >= 22
    assert "Change operation layer color" in button.toolTip()
    assert "Change operation layer color" in button.accessibleName()

    button.click()
    qt_application.processEvents()
    assert color_requests == [first_layer.id]
    assert {item.id: item.layer_id for item in document.objects} == original_assignments

    panel.tree.clearSelection()
    _row, item = _object_layer_row(panel, first.id)
    item_rect = panel.tree.visualItemRect(item)
    name_x = panel.tree.header().sectionViewportPosition(0) + 12
    QtTest.QTest.mouseClick(
        panel.tree.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        pos=QtCore.QPoint(name_x, item_rect.center().y()),
    )
    qt_application.processEvents()
    assert item.isSelected()
    assert selections[-1] == [first.id]

    panel.close()
    panel.deleteLater()


def test_objects_layer_color_uses_shared_undoable_path_for_raster_and_native_path(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layer = OperationLayer(
        name="Safe raster trace",
        color="#6FAF58",
        mode=LayerMode.LINE,
        speed_mm_min=1375.0,
        power_percent=0.0,
        passes=3,
        output_enabled=False,
        visible=True,
        line_interval_mm=0.23,
        scan_angle_deg=17.0,
        overscan_percent=8.0,
        vector_power_correction=-12.0,
        raster_power_correction=9.0,
    )
    document = ProjectDocument(layers=[layer])
    raster = SceneObject(
        name="Imported raster",
        kind=ObjectKind.IMAGE,
        layer_id=layer.id,
        transform=Transform(width_mm=20.0, height_mm=10.0),
        geometry={"asset": "missing-object-layer-color-test.png"},
    )
    native_path = SceneObject.native_path(
        layer.id,
        NativePathGeometry(
            subpaths=(
                PathSubpath(
                    start=(-0.5, 0.0),
                    segments=(
                        PathCubicSegment(
                            control_1=(-0.25, 0.5),
                            control_2=(0.25, 0.5),
                            to=(0.5, 0.0),
                        ),
                    ),
                    closed=False,
                ),
            )
        ),
        name="Native cubic path",
        transform=Transform(width_mm=20.0, height_mm=10.0),
    )
    document.add_object(raster)
    document.add_object(native_path)
    initial_layer = layer.to_dict()
    initial_revision = document.revision
    initial_assignments = {item.id: item.layer_id for item in document.objects}

    layer_panel = LayerPanel()
    object_panel = ObjectPanel()
    workspace = WorkspaceView(document.work_area)
    palette = LayerPaletteBar()
    history = CommandStack()
    refresh_count = 0

    def refresh(selected_ids: list[str] | None = None) -> None:
        nonlocal refresh_count
        refresh_count += 1
        workspace.set_document(document)
        layer_panel.set_document(document, layer.id)
        object_panel.set_document(document, selected_ids)
        palette.set_layers(document.layers, layer.id)

    harness = SimpleNamespace(
        document=document,
        history=history,
        workspace=workspace,
        _refresh_document=refresh,
    )
    history.add_listener(lambda _stack: refresh(workspace.selected_object_ids()))
    layer_panel.layerEdited.connect(
        lambda layer_id, changes: E3MainWindow._layer_edited(
            harness, layer_id, changes
        ),
        QtCore.Qt.ConnectionType.QueuedConnection,
    )
    object_panel.layerColorEditRequested.connect(layer_panel.choose_color)
    refresh()

    raster_button = _object_layer_color_button(object_panel, raster.id)
    path_button = _object_layer_color_button(object_panel, native_path.id)
    assert raster_button.property("layerId") == layer.id
    assert path_button.property("layerId") == layer.id
    assert raster_button.property("layerColor") == layer.color
    assert path_button.property("layerColor") == layer.color

    dialog_colors = iter((QtGui.QColor(), QtGui.QColor("#2A7BCB")))
    dialog_initial_colors: list[str] = []

    def choose_color(initial: QtGui.QColor, *_args, **_kwargs) -> QtGui.QColor:
        dialog_initial_colors.append(initial.name())
        return next(dialog_colors)

    monkeypatch.setattr(QtWidgets.QColorDialog, "getColor", choose_color)

    raster_button.click()
    qt_application.processEvents()
    assert document.revision == initial_revision
    assert history.depth == 0
    assert document.get_layer(layer.id).to_dict() == initial_layer

    path_button.click()
    qt_application.processEvents()
    changed = document.get_layer(layer.id)
    assert dialog_initial_colors == ["#6faf58", "#6faf58"]
    assert changed.color == "#2A7BCB"
    assert history.depth == 1
    assert history.undo_text == f"Edit {layer.name}"
    assert {
        key: value for key, value in changed.to_dict().items() if key != "color"
    } == {
        key: value for key, value in initial_layer.items() if key != "color"
    }
    assert {item.id: item.layer_id for item in document.objects} == initial_assignments

    assert _object_layer_color_button(
        object_panel, raster.id
    ).property("layerColor") == "#2A7BCB"
    assert _object_layer_color_button(
        object_panel, native_path.id
    ).property("layerColor") == "#2A7BCB"
    layer_item = layer_panel.layer_list.topLevelItem(0)
    layer_icon = layer_item.icon(0).pixmap(14, 14).toImage()
    assert layer_icon.pixelColor(layer_icon.rect().center()).name() == "#2a7bcb"
    assert layer_panel.color_button.property("layerColor") == "#2A7BCB"
    assert "#2a7bcb" in palette._buttons[layer.id].styleSheet().lower()
    assert workspace._items_by_id[native_path.id].pen().color().name() == "#2a7bcb"

    assert history.undo()
    assert document.get_layer(layer.id).color == "#6FAF58"
    assert layer_panel.color_button.property("layerColor") == "#6FAF58"
    assert workspace._items_by_id[native_path.id].pen().color().name() == "#6faf58"
    assert all(
        _object_layer_color_button(object_panel, item.id).property("layerColor")
        == "#6FAF58"
        for item in (raster, native_path)
    )

    assert history.redo()
    assert document.get_layer(layer.id).color == "#2A7BCB"
    assert workspace._items_by_id[native_path.id].pen().color().name() == "#2a7bcb"
    assert refresh_count >= 5

    workspace.close()
    workspace.deleteLater()
    object_panel.close()
    object_panel.deleteLater()
    layer_panel.close()
    layer_panel.deleteLater()
    palette.close()
    palette.deleteLater()
