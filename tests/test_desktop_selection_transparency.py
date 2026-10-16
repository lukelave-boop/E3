from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.config import LaserSettings
from laser_aligner.desktop.selection_transparency import SelectionTransparencyBar
from laser_aligner.desktop.workspace import ObjectGraphicsItem, WorkspaceFrame, WorkspaceView
from laser_aligner.project import (
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    SceneObject,
    Transform,
    generate_project_gcode,
)
from laser_aligner.project.history import CommandStack, RemoveObjectsCommand


@pytest.fixture(scope="module")
def application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


def _destroy(widget: QtWidgets.QWidget) -> None:
    widget.close()
    widget.deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(widget, QtCore.QEvent.Type.DeferredDelete)


@pytest.fixture
def workspace(application: QtWidgets.QApplication):
    document = ProjectDocument.new("Alignment transparency")
    for index in range(3):
        document.add_object(
            SceneObject.rectangle(
                document.layers[0].id,
                center=(30.0 + index * 30.0, 30.0),
                width_mm=12.0,
                height_mm=10.0,
            )
        )
    view = WorkspaceView(document.work_area)
    frame = WorkspaceFrame(view)
    view.set_document(document)
    frame.resize(800, 600)
    frame.show()
    application.processEvents()
    try:
        yield document, view, frame
    finally:
        _destroy(frame)


def _render(scene: QtWidgets.QGraphicsScene) -> QtGui.QImage:
    image = QtGui.QImage(120, 120, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    try:
        scene.render(painter)
    finally:
        painter.end()
    return image


@pytest.mark.parametrize("kind", ["raster", "fill", "line"])
def test_transparency_fades_actual_raster_fill_and_line_pixels(
    application: QtWidgets.QApplication,
    tmp_path: Path,
    kind: str,
) -> None:
    del application
    layer = OperationLayer(mode=LayerMode.FILL if kind == "fill" else LayerMode.LINE)
    layer.color = "#204080"
    if kind == "raster":
        source = QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB32)
        source.fill(QtGui.QColor("#204080"))
        path = tmp_path / "artwork.png"
        assert source.save(str(path), "PNG")
        model = SceneObject(
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(width_mm=10.0, height_mm=10.0),
            geometry={"asset": str(path)},
        )
    else:
        model = SceneObject.rectangle(
            layer.id, center=(0.0, 0.0), width_mm=10.0, height_mm=10.0
        )
    item = ObjectGraphicsItem(model, layer)
    scene = QtWidgets.QGraphicsScene(QtCore.QRectF(-6.0, -6.0, 12.0, 12.0))
    scene.addItem(item)
    images = []
    for transparency in (0, 50, 100):
        item.set_display_transparency(transparency)
        assert item.opacity() == 1.0
        images.append(_render(scene))
    # Total color contrast also exercises thin cosmetic vector strokes.
    contrasts = [
        sum(
            765 - sum(image.pixelColor(x, y).getRgb()[:3])
            for x in range(image.width())
            for y in range(image.height())
        )
        for image in images
    ]
    assert contrasts[0] > 100
    assert contrasts[1] == pytest.approx(contrasts[0] * 0.5, rel=0.04)
    assert contrasts[2] == 0
    scene.clear()


def test_fully_transparent_selected_art_retains_visible_selection_outline(
    application: QtWidgets.QApplication,
) -> None:
    del application
    layer = OperationLayer(mode=LayerMode.FILL)
    model = SceneObject.rectangle(
        layer.id, center=(0.0, 0.0), width_mm=10.0, height_mm=10.0
    )
    item = ObjectGraphicsItem(model, layer)
    scene = QtWidgets.QGraphicsScene(QtCore.QRectF(-6.0, -6.0, 12.0, 12.0))
    scene.addItem(item)
    item.set_display_transparency(100)
    item.setSelected(True)
    image = _render(scene)
    assert image.pixelColor(60, 60) == QtGui.QColor("white")
    assert any(
        image.pixelColor(x, y) != QtGui.QColor("white")
        for x in range(image.width())
        for y in range(image.height())
    )
    scene.clear()


def test_selected_objects_only_change_and_multi_selection_reports_mixed(workspace) -> None:
    document, view, frame = workspace
    first, second, third = document.objects
    view.select_objects([first.id])
    view.set_selection_transparency(25)
    view.select_objects([second.id])
    view.set_selection_transparency(70)
    view.select_objects([first.id, second.id])

    assert sorted(view.selected_object_transparencies()) == [25, 70]
    assert frame.transparency_bar.summary.text() == "Mixed"
    assert frame.transparency_bar.spin.lineEdit().placeholderText() == "Mixed"
    assert view._items_by_id[third.id].display_transparency == 0

    frame.transparency_bar.slider.setValue(45)

    assert view.selected_object_transparencies() == [45, 45]
    assert frame.transparency_bar.spin.value() == 45
    assert "Mixed" not in frame.transparency_bar.summary.text()
    assert view._items_by_id[third.id].display_transparency == 0


def test_transparent_object_can_be_clicked_reselected_and_transformed(
    application: QtWidgets.QApplication, workspace
) -> None:
    document, view, _frame = workspace
    first = document.objects[0]
    view.select_objects([first.id])
    view.set_selection_transparency(100)
    view.select_objects([])
    item = view._items_by_id[first.id]
    edge = item.mapToScene(QtCore.QPointF(first.transform.width_mm / 2.0, 0.0))
    assert item in view.workspace_scene.items(edge)
    QtTest.QTest.mouseClick(
        view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=view.mapFromScene(edge)
    )
    application.processEvents()

    assert view.selected_object_ids() == [first.id]
    assert view.selected_object_transparencies() == [100]
    assert item.opacity() == 1.0
    overlay = view._object_transform_overlay
    assert overlay is not None and overlay.isVisible()
    assert overlay.opacity() == 1.0
    assert overlay.childItems()
    assert all(handle.opacity() == 1.0 for handle in overlay.childItems())
    view._preview_object_transform(first.id, first.transform.copy(x_mm=35.0))
    assert item.pos().x() == 35.0
    assert item.display_transparency == 100
    assert first.transform.x_mm == 30.0


def test_transparency_survives_refresh_transform_delete_and_undo(workspace) -> None:
    document, view, _frame = workspace
    first = document.objects[0]
    view.select_objects([first.id])
    view.set_selection_transparency(63)
    first.transform = first.transform.copy(rotation_deg=17.0)
    document.touch()
    view.refresh_object(first.id)
    assert view._items_by_id[first.id].display_transparency == 63
    view.set_document(document)
    assert view.selected_object_ids() == [first.id]
    assert view.selected_object_transparencies() == [63]

    history = CommandStack()
    history.execute(RemoveObjectsCommand(document, [first.id]))
    view.set_document(document)
    assert first.id not in view._items_by_id
    assert history.undo()
    view.set_document(document)
    view.select_objects([first.id])
    assert view.selected_object_transparencies() == [63]


def test_reopening_document_clears_transparency_even_when_ids_are_identical(workspace) -> None:
    document, view, frame = workspace
    first = document.objects[0]
    view.select_objects([first.id])
    view.set_selection_transparency(90)

    reopened = ProjectDocument.from_dict(document.to_dict())
    view.set_document(reopened)
    view.select_objects([first.id])

    assert view.selected_object_transparencies() == [0]
    assert frame.transparency_bar.spin.value() == 0


def test_reset_all_restores_unselected_and_temporarily_deleted_objects(workspace) -> None:
    document, view, frame = workspace
    first, second, _third = document.objects
    view.select_objects([first.id, second.id])
    view.set_selection_transparency(100)
    removed, index = document.remove_object(first.id)
    view.set_document(document)
    view.select_objects([])
    assert not frame.transparency_bar.slider.isEnabled()
    assert not frame.transparency_bar.spin.isEnabled()

    frame.transparency_bar.reset_button.click()
    document.add_object(removed, index)
    view.set_document(document)
    view.select_objects([first.id, second.id])

    assert view.selected_object_transparencies() == [0, 0]


def test_transparency_does_not_edit_document_or_generated_output(workspace) -> None:
    document, view, frame = workspace
    before = document.to_dict()
    original_output = generate_project_gcode(document, LaserSettings(power_max=1000)).text
    view.select_objects([item.id for item in document.objects])
    frame.transparency_bar.slider.setValue(100)
    assert document.to_dict() == before
    assert generate_project_gcode(document, LaserSettings(power_max=1000)).text == original_output
    frame.transparency_bar.reset_button.click()
    assert document.to_dict() == before


@pytest.mark.parametrize("value", [-1, 101, 0.5, 50.0, True, "50", None])
def test_invalid_transparency_values_reject_without_changing_selection(workspace, value) -> None:
    document, view, _frame = workspace
    view.select_objects([document.objects[0].id])
    view.set_selection_transparency(25)
    with pytest.raises((TypeError, ValueError)):
        view.set_selection_transparency(value)
    assert view.selected_object_transparencies() == [25]


def test_empty_selection_disables_controls_and_noops_view_edit(workspace) -> None:
    document, view, frame = workspace
    assert not frame.transparency_bar.slider.isEnabled()
    assert not frame.transparency_bar.spin.isEnabled()
    assert "No selection" in frame.transparency_bar.summary.text()
    view.set_selection_transparency(80)
    assert view.selected_object_transparencies() == []
    assert all(view._items_by_id[item.id].display_transparency == 0 for item in document.objects)


def test_programmatic_bar_selection_changes_never_emit_edits(application) -> None:
    del application
    bar = SelectionTransparencyBar()
    changes: list[int] = []
    bar.transparencyEdited.connect(changes.append)
    try:
        for values in ([30], [30, 80], [], [100, 100]):
            bar.set_selection(values)
        assert changes == []
        assert bar.slider.value() == bar.spin.value() == 100
    finally:
        _destroy(bar)


@pytest.mark.parametrize("value", [0, 42, 100])
def test_mixed_numeric_entry_applies_only_on_commit(application, workspace, value) -> None:
    document, view, frame = workspace
    first, second, _third = document.objects
    view.select_objects([first.id])
    view.set_selection_transparency(20)
    view.select_objects([second.id])
    view.set_selection_transparency(80)
    view.select_objects([first.id, second.id])
    spin = frame.transparency_bar.spin
    spin.setFocus()
    spin.selectAll()
    QtTest.QTest.keyClicks(spin, str(value))
    assert sorted(view.selected_object_transparencies()) == [20, 80]
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Return)
    application.processEvents()
    assert view.selected_object_transparencies() == [value, value]
    assert spin.value() == value


@pytest.mark.parametrize("draft", ["", "101", "-1"])
def test_invalid_or_empty_mixed_draft_preserves_each_object(application, workspace, draft) -> None:
    document, view, frame = workspace
    first, second, _third = document.objects
    view.select_objects([first.id])
    view.set_selection_transparency(20)
    view.select_objects([second.id])
    view.set_selection_transparency(80)
    view.select_objects([first.id, second.id])
    spin = frame.transparency_bar.spin
    spin.setFocus()
    spin.selectAll()
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Backspace)
    if draft:
        QtTest.QTest.keyClicks(spin, draft)
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Return)
    application.processEvents()

    assert sorted(view.selected_object_transparencies()) == [20, 80]
    assert frame.transparency_bar.summary.text() == "Mixed"
    assert spin.lineEdit().placeholderText() == "Mixed"


def test_focusing_mixed_numeric_control_without_edit_does_not_apply_zero(application, workspace) -> None:
    document, view, frame = workspace
    first, second, _third = document.objects
    view.select_objects([first.id])
    view.set_selection_transparency(20)
    view.select_objects([second.id])
    view.set_selection_transparency(80)
    view.select_objects([first.id, second.id])
    spin = frame.transparency_bar.spin
    spin.setFocus()
    application.processEvents()
    view.setFocus()
    application.processEvents()

    assert sorted(view.selected_object_transparencies()) == [20, 80]
    assert frame.transparency_bar.summary.text() == "Mixed"
    assert spin.lineEdit().placeholderText() == "Mixed"


def test_clicking_mixed_slider_at_zero_makes_selected_objects_fully_visible(application, workspace) -> None:
    document, view, frame = workspace
    first, second, _third = document.objects
    view.select_objects([first.id])
    view.set_selection_transparency(20)
    view.select_objects([second.id])
    view.set_selection_transparency(80)
    view.select_objects([first.id, second.id])
    slider = frame.transparency_bar.slider
    option = QtWidgets.QStyleOptionSlider()
    slider.initStyleOption(option)
    handle = slider.style().subControlRect(
        QtWidgets.QStyle.ComplexControl.CC_Slider,
        option,
        QtWidgets.QStyle.SubControl.SC_SliderHandle,
        slider,
    )
    QtTest.QTest.mouseClick(slider, QtCore.Qt.MouseButton.LeftButton, pos=handle.center())
    application.processEvents()

    assert view.selected_object_transparencies() == [0, 0]
    assert frame.transparency_bar.spin.value() == 0
    assert "Mixed" not in frame.transparency_bar.summary.text()
