from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.panels import TransformPanel
from laser_aligner.project import ProjectDocument, SceneObject, Transform


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def test_rectangle_transform_panel_emits_size_and_radius_atomically(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new()
    rectangle = SceneObject.rectangle(
        document.active_layer_id,
        center=(10.0, 20.0),
        width_mm=20.0,
        height_mm=10.0,
        corner_radius_mm=4.0,
    )
    document.add_object(rectangle)
    panel = TransformPanel()
    panel.set_document_layers(document)
    panel.set_selection([rectangle], document)

    shape_edits: list[tuple[str, Transform, float]] = []
    transform_edits: list[tuple[str, Transform]] = []
    panel.rectangleShapeEdited.connect(
        lambda object_id, transform, radius: shape_edits.append(
            (object_id, transform, radius)
        )
    )
    panel.transformEdited.connect(
        lambda object_id, transform: transform_edits.append((object_id, transform))
    )

    assert not panel.corner_radius_label.isHidden()
    assert not panel.corner_radius_spin.isHidden()
    assert panel.corner_radius_spin.isEnabled()
    assert panel.corner_radius_spin.maximum() == pytest.approx(5.0)
    assert panel.corner_radius_spin.value() == pytest.approx(4.0)

    panel.width_spin.setValue(6.0)
    assert panel.corner_radius_spin.maximum() == pytest.approx(3.0)
    assert panel.corner_radius_spin.value() == pytest.approx(3.0)
    panel.width_spin.editingFinished.emit()

    assert transform_edits == []
    assert len(shape_edits) == 1
    object_id, transform, radius = shape_edits[0]
    assert object_id == rectangle.id
    assert transform.width_mm == pytest.approx(6.0)
    assert transform.height_mm == pytest.approx(10.0)
    assert radius == pytest.approx(3.0)

    panel.corner_radius_spin.setValue(1.25)
    panel.corner_radius_spin.editingFinished.emit()
    assert len(shape_edits) == 2
    assert shape_edits[-1][2] == pytest.approx(1.25)

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_corner_radius_is_only_available_for_one_rectangle(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new()
    rectangle = SceneObject.rectangle(document.active_layer_id)
    ellipse = SceneObject.ellipse(document.active_layer_id)
    document.add_object(rectangle)
    document.add_object(ellipse)
    panel = TransformPanel()
    panel.set_document_layers(document)

    transform_edits: list[tuple[str, Transform]] = []
    shape_edits: list[tuple[str, Transform, float]] = []
    panel.transformEdited.connect(
        lambda object_id, transform: transform_edits.append((object_id, transform))
    )
    panel.rectangleShapeEdited.connect(
        lambda object_id, transform, radius: shape_edits.append(
            (object_id, transform, radius)
        )
    )

    panel.set_selection([ellipse], document)
    assert panel.corner_radius_label.isHidden()
    assert panel.corner_radius_spin.isHidden()
    assert not panel.corner_radius_spin.isEnabled()
    panel.width_spin.setValue(42.0)
    panel.width_spin.editingFinished.emit()
    assert [entry[0] for entry in transform_edits] == [ellipse.id]
    assert shape_edits == []

    panel.set_selection([rectangle, ellipse], document)
    assert panel.corner_radius_label.isHidden()
    assert panel.corner_radius_spin.isHidden()
    assert not panel.corner_radius_spin.isEnabled()

    panel.set_selection([], document)
    assert panel.corner_radius_label.isHidden()
    assert panel.corner_radius_spin.isHidden()
    assert not panel.corner_radius_spin.isEnabled()

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()
