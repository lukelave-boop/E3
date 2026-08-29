from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.desktop.context_bar import ContextPropertyBar
from laser_aligner.project import ProjectDocument, SceneObject, Transform


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _document_objects() -> tuple[ProjectDocument, SceneObject, SceneObject]:
    document = ProjectDocument.new()
    rectangle = SceneObject.rectangle(
        document.active_layer_id,
        name="Rounded label",
        center=(12.5, 24.0),
        width_mm=20.0,
        height_mm=10.0,
        corner_radius_mm=4.0,
    )
    ellipse = SceneObject.ellipse(
        document.active_layer_id,
        name="Registration mark",
        center=(50.0, 60.0),
        width_mm=8.0,
        height_mm=9.0,
    )
    document.add_object(rectangle)
    document.add_object(ellipse)
    return document, rectangle, ellipse


def test_context_bar_keeps_disabled_properties_visible_until_one_object_selected(
    qt_application: QtWidgets.QApplication,
) -> None:
    document, rectangle, ellipse = _document_objects()
    bar = ContextPropertyBar()
    toolbar = QtWidgets.QToolBar()
    toolbar.addWidget(bar)
    toolbar.resize(1400, 72)
    toolbar.show()
    qt_application.processEvents()

    assert bar.selection_summary.text() == "No selection"
    assert bar.editor.isVisible()
    assert not bar.editor.isEnabled()
    for field in (
        bar.x_field,
        bar.y_field,
        bar.width_field,
        bar.height_field,
        bar.scale_x_field,
        bar.scale_y_field,
        bar.rotation_field,
        bar.units_field,
    ):
        assert field.isVisible()
    assert bar.x_spin.value() == pytest.approx(0.0)
    assert bar.width_spin.value() == pytest.approx(0.0)
    assert bar.scale_x_spin.value() == pytest.approx(100.0)
    assert bar.corner_radius_field.isHidden()

    bar.set_selection([ellipse], document)
    qt_application.processEvents()
    assert bar.selection_summary.text() == ellipse.name
    assert bar.editor.isVisible()
    assert bar.editor.isEnabled()
    assert bar.corner_radius_field.isHidden()
    assert bar.x_spin.value() == pytest.approx(50.0)
    assert bar.y_spin.value() == pytest.approx(60.0)

    bar.set_selection([rectangle, ellipse], document)
    qt_application.processEvents()
    assert bar.selection_summary.text() == "2 selected"
    assert bar.editor.isVisible()
    assert not bar.editor.isEnabled()
    assert bar.width_field.isVisible()
    assert bar.scale_x_field.isVisible()
    assert bar.rotation_field.isVisible()

    toolbar.close()
    toolbar.deleteLater()
    qt_application.processEvents()


def test_non_rectangle_edit_emits_complete_transform(
    qt_application: QtWidgets.QApplication,
) -> None:
    document, _, ellipse = _document_objects()
    bar = ContextPropertyBar()
    bar.set_selection([ellipse], document)
    transform_edits: list[tuple[str, Transform]] = []
    shape_edits: list[tuple[str, Transform, float]] = []
    bar.transformEdited.connect(
        lambda object_id, transform: transform_edits.append((object_id, transform))
    )
    bar.rectangleShapeEdited.connect(
        lambda object_id, transform, radius: shape_edits.append(
            (object_id, transform, radius)
        )
    )

    bar.x_spin.setValue(72.25)
    bar.width_spin.setValue(14.5)
    bar.rotation_spin.setValue(32.0)
    bar.x_spin.editingFinished.emit()

    assert shape_edits == []
    assert len(transform_edits) == 1
    object_id, transform = transform_edits[0]
    assert object_id == ellipse.id
    assert transform.x_mm == pytest.approx(72.25)
    assert transform.y_mm == pytest.approx(60.0)
    assert transform.width_mm == pytest.approx(14.5)
    assert transform.height_mm == pytest.approx(9.0)
    assert transform.rotation_deg == pytest.approx(32.0)

    bar.mirror_x.click()
    assert len(transform_edits) == 2
    assert transform_edits[-1][1].mirror_x

    bar.close()
    bar.deleteLater()
    qt_application.processEvents()


def test_rectangle_edit_caps_radius_and_emits_one_atomic_shape_edit(
    qt_application: QtWidgets.QApplication,
) -> None:
    document, rectangle, _ = _document_objects()
    bar = ContextPropertyBar()
    bar.set_selection([rectangle], document)
    shape_edits: list[tuple[str, Transform, float]] = []
    transform_edits: list[tuple[str, Transform]] = []
    bar.rectangleShapeEdited.connect(
        lambda object_id, transform, radius: shape_edits.append(
            (object_id, transform, radius)
        )
    )
    bar.transformEdited.connect(
        lambda object_id, transform: transform_edits.append((object_id, transform))
    )

    assert not bar.corner_radius_field.isHidden()
    assert bar.corner_radius_spin.maximum() == pytest.approx(5.0)
    assert bar.corner_radius_spin.value() == pytest.approx(4.0)

    bar.width_spin.setValue(6.0)
    assert bar.corner_radius_spin.maximum() == pytest.approx(3.0)
    assert bar.corner_radius_spin.value() == pytest.approx(3.0)
    bar.width_spin.editingFinished.emit()

    assert transform_edits == []
    assert len(shape_edits) == 1
    object_id, transform, radius = shape_edits[0]
    assert object_id == rectangle.id
    assert transform.width_mm == pytest.approx(6.0)
    assert transform.height_mm == pytest.approx(10.0)
    assert radius == pytest.approx(3.0)

    bar.close()
    bar.deleteLater()
    qt_application.processEvents()


def test_multi_selection_cannot_emit_a_single_object_transform(
    qt_application: QtWidgets.QApplication,
) -> None:
    document, rectangle, ellipse = _document_objects()
    bar = ContextPropertyBar()
    edits: list[tuple[str, Transform]] = []
    shape_edits: list[tuple[str, Transform, float]] = []
    bar.transformEdited.connect(
        lambda object_id, transform: edits.append((object_id, transform))
    )
    bar.rectangleShapeEdited.connect(
        lambda object_id, transform, radius: shape_edits.append(
            (object_id, transform, radius)
        )
    )

    bar.set_selection([rectangle, ellipse], document)
    bar.width_spin.setValue(100.0)
    bar.width_spin.editingFinished.emit()
    bar.scale_x_spin.setValue(150.0)
    bar.scale_x_spin.editingFinished.emit()
    bar.mirror_y.setChecked(True)

    assert edits == []
    assert shape_edits == []

    bar.close()
    bar.deleteLater()
    qt_application.processEvents()


def test_aspect_lock_and_percentage_scaling_emit_one_complete_transform(
    qt_application: QtWidgets.QApplication,
) -> None:
    document, _, ellipse = _document_objects()
    bar = ContextPropertyBar()
    bar.set_selection([ellipse], document)
    edits: list[tuple[str, Transform]] = []
    bar.transformEdited.connect(
        lambda object_id, transform: edits.append((object_id, transform))
    )

    bar.aspect_lock.setChecked(True)
    bar.width_spin.setValue(16.0)
    bar.width_spin.editingFinished.emit()

    assert len(edits) == 1
    assert edits[-1][1].width_mm == pytest.approx(16.0)
    assert edits[-1][1].height_mm == pytest.approx(18.0)
    assert bar.scale_x_spin.value() == pytest.approx(200.0)
    assert bar.scale_y_spin.value() == pytest.approx(200.0)

    edits.clear()
    bar.scale_y_spin.setValue(50.0)
    bar.scale_y_spin.editingFinished.emit()

    assert len(edits) == 1
    assert edits[-1][1].width_mm == pytest.approx(4.0)
    assert edits[-1][1].height_mm == pytest.approx(4.5)
    assert bar.scale_x_spin.value() == pytest.approx(50.0)
    assert bar.scale_y_spin.value() == pytest.approx(50.0)

    bar.close()
    bar.deleteLater()
    qt_application.processEvents()


def test_inch_display_converts_back_to_millimetres_without_mutating_on_switch(
    qt_application: QtWidgets.QApplication,
) -> None:
    document, _, ellipse = _document_objects()
    bar = ContextPropertyBar()
    bar.set_selection([ellipse], document)
    edits: list[tuple[str, Transform]] = []
    bar.transformEdited.connect(
        lambda object_id, transform: edits.append((object_id, transform))
    )

    bar.units_combo.setCurrentIndex(bar.units_combo.findData("in"))
    assert edits == []
    assert bar.x_spin.value() == pytest.approx(50.0 / 25.4, abs=0.0001)
    assert bar.width_spin.value() == pytest.approx(8.0 / 25.4, abs=0.0001)
    assert bar.x_spin.suffix() == " in"

    bar.x_spin.setValue(2.0)
    bar.x_spin.editingFinished.emit()
    assert len(edits) == 1
    assert edits[-1][1].x_mm == pytest.approx(50.8)
    # Values which were rounded for inch display must not be written back with
    # that display rounding when a different field is edited.
    assert edits[-1][1].y_mm == 60.0
    assert edits[-1][1].width_mm == 8.0
    assert edits[-1][1].height_mm == 9.0

    bar.close()
    bar.deleteLater()
    qt_application.processEvents()


def test_context_bar_uses_compact_two_row_controls(
    qt_application: QtWidgets.QApplication,
) -> None:
    document, rectangle, _ = _document_objects()
    bar = ContextPropertyBar()
    bar.set_selection([rectangle], document)
    bar.show()
    qt_application.processEvents()

    assert isinstance(bar.layout(), QtWidgets.QHBoxLayout)
    assert isinstance(bar.editor.layout(), QtWidgets.QGridLayout)
    assert bar.sizeHint().height() <= 64
    for spin in bar.findChildren(QtWidgets.QDoubleSpinBox):
        assert spin.maximumWidth() <= 98
        assert spin.accessibleName()
        assert spin.toolTip()
    assert bar.mirror_x.toolTip()
    assert bar.mirror_y.toolTip()
    assert bar.aspect_lock.toolTip()
    assert bar.aspect_lock.accessibleName()
    assert bar.units_combo.toolTip()
    assert bar.units_combo.accessibleName()
    assert bar.selection_summary.minimumWidth() >= 76
    assert (
        bar.focusPolicy() == QtCore.Qt.FocusPolicy.NoFocus
        or bar.focusPolicy() == QtCore.Qt.FocusPolicy.StrongFocus
    )

    bar.close()
    bar.deleteLater()
    qt_application.processEvents()


def test_context_bar_reflows_without_clipping_at_900px_and_13pt(
    qt_application: QtWidgets.QApplication,
) -> None:
    document, rectangle, _ = _document_objects()
    font = QtGui.QFont(qt_application.font())
    font.setPointSize(13)
    toolbar = QtWidgets.QToolBar()
    toolbar.setFont(font)
    bar = ContextPropertyBar()
    bar.setFont(font)
    bar.set_selection([rectangle], document)
    toolbar.addWidget(bar)
    toolbar.resize(900, 80)
    toolbar.show()
    qt_application.processEvents()

    assert bar.compact
    assert bar.minimumSizeHint().width() <= 620
    assert bar.sizeHint().width() <= bar.width()
    assert bar.units_combo.isVisible()
    assert bar.units_combo.currentText() == "mm"
    assert "millimetres" in bar.units_combo.accessibleDescription()
    assert bar.rotation_label.text() == "A°"
    assert bar.corner_radius_label.text() == "R"
    assert bar.scale_x_label.text() == "%W"
    assert bar.scale_y_label.text() == "%H"
    assert bar.aspect_lock.text() == "L"
    assert bar.mirror_x.text() == "H"
    assert bar.mirror_y.text() == "V"

    controls = [
        bar.selection_summary,
        bar.x_field,
        bar.y_field,
        bar.width_field,
        bar.height_field,
        bar.scale_x_field,
        bar.scale_y_field,
        bar.rotation_field,
        bar.units_field,
        bar.corner_radius_field,
        bar.aspect_lock,
        bar.mirror_x,
        bar.mirror_y,
    ]
    for control in controls:
        assert control.isVisible()
        top_left = control.mapTo(bar, QtCore.QPoint(0, 0))
        bounds = QtCore.QRect(top_left, control.size())
        assert bar.rect().contains(bounds), (
            f"{control.objectName() or control.__class__.__name__} overflowed: "
            f"{bounds.getRect()} outside {bar.rect().getRect()}"
        )
    for spin in bar.findChildren(QtWidgets.QDoubleSpinBox):
        assert spin.isVisible()
        assert spin.suffix() == ""
        assert spin.accessibleName()
        assert spin.accessibleDescription()
        assert spin.toolTip()
        assert spin.width() == bar._COMPACT_SPIN_WIDTH_PX

    toolbar.resize(1400, 80)
    qt_application.processEvents()
    assert not bar.compact
    assert bar.units_combo.isVisible()
    assert bar.rotation_label.text() == "Rotate"
    assert bar.corner_radius_label.text() == "Radius"
    assert bar.scale_x_label.text() == "Scale X"
    assert bar.scale_y_label.text() == "Scale Y"
    assert bar.x_spin.suffix() == " mm"
    assert bar.rotation_spin.suffix() == "°"
    assert bar.aspect_lock.text() == "Lock"
    assert bar.mirror_x.text() == "Flip H"
    assert bar.mirror_y.text() == "Flip V"

    toolbar.close()
    toolbar.deleteLater()
    qt_application.processEvents()
