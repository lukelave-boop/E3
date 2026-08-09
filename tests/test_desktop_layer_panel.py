from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop.controls import PanelScrollArea
from laser_aligner.desktop.main_window import LAYER_PALETTE_COLORS, LayerPaletteBar
from laser_aligner.desktop.panels import LayerPanel
from laser_aligner.desktop.theme import DARK_STYLESHEET
from laser_aligner.project import LayerMode, OperationLayer, ProjectDocument


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
