"""Offscreen verification of linked outline transform previews and rejection."""
from __future__ import annotations

import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.workspace import ObjectGraphicsItem, WorkspaceView
from laser_aligner.project.history import CommandStack
from laser_aligner.project.image_outline import AddOutlineCommand, DetachOutlineCommand
from laser_aligner.project.model import ObjectKind, ProjectDocument, SceneObject, Transform
from laser_aligner.project.path_geometry import NativePathGeometry


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


@pytest.fixture
def linked_view(app, tmp_path):
    path = tmp_path / "source.png"
    assert cv2.imwrite(str(path), np.full((32, 32), 128, np.uint8))
    document = ProjectDocument.new()
    source = SceneObject(
        kind=ObjectKind.IMAGE, layer_id=document.active_layer_id,
        geometry={"asset": str(path)}, transform=Transform(70, 80, 40, 60),
    )
    document.add_object(source)
    geometry = NativePathGeometry.from_legacy_polylines([{
        "points": [[-.4, -.4], [.4, -.4], [.4, .4], [-.4, .4]], "closed": True,
    }])
    history = CommandStack()
    command = AddOutlineCommand(document, source.id, geometry)
    history.execute(command)
    workspace = WorkspaceView(document.work_area)
    workspace.set_document(document)
    yield document, source, command.item, history, workspace
    workspace.workspace_scene.selectionChanged.disconnect(workspace._emit_selection)
    workspace.close()
    workspace.deleteLater()
    # Destroy only this fixture's view; other tests may own pending deletions.
    QtCore.QCoreApplication.sendPostedEvents(workspace, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def test_linked_path_suppresses_direct_move_and_resize_until_detached(linked_view):
    document, _source, vector, history, workspace = linked_view
    workspace.select_objects([vector.id])
    item = workspace._items_by_id[vector.id]
    assert not item.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert workspace._object_transform_overlay is None
    history.execute(DetachOutlineCommand(document, vector.id))
    workspace.set_document(document)
    workspace.select_objects([vector.id])
    item = workspace._items_by_id[vector.id]
    assert item.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert workspace._object_transform_overlay.object_id == vector.id


def test_source_live_preview_and_cancel_keep_outline_aligned_without_decode(linked_view, monkeypatch):
    document, source, vector, _history, workspace = linked_view
    before = document.to_dict()
    workspace.select_objects([source.id])
    overlay = workspace._object_transform_overlay
    transformed = source.transform.copy(
        x_mm=95, y_mm=107, width_mm=80, height_mm=30, rotation_deg=42, mirror_x=True,
    )

    def no_decode(*_args, **_kwargs):
        raise AssertionError("Transform preview must not decode its source image")

    monkeypatch.setattr(ObjectGraphicsItem, "_load_raster_snapshot", no_decode)
    overlay._interaction_before = source.transform.copy()
    overlay._preview(transformed)
    for target in (source, vector):
        graphics = workspace._items_by_id[target.id]
        assert graphics.pos() == QtCore.QPointF(95, -107)
        assert graphics.rotation() == pytest.approx(-42)
        assert graphics.transform().m11() == -1
    vector_graphics = workspace._items_by_id[vector.id]
    assert vector_graphics.path().boundingRect().width() == pytest.approx(64)
    assert vector_graphics.path().boundingRect().height() == pytest.approx(24)
    assert document.to_dict() == before
    overlay.cancel_interaction()
    for target in (source, vector):
        graphics = workspace._items_by_id[target.id]
        assert graphics.pos() == QtCore.QPointF(70, -80)
        assert graphics.rotation() == 0
        assert graphics.transform().m11() == 1
    assert document.to_dict() == before


def test_live_image_drag_moves_its_outline_without_mutating_document(linked_view):
    document, source, vector, _history, workspace = linked_view
    before = document.to_dict()
    workspace._items_by_id[source.id].setPos(92, -101)
    assert workspace._items_by_id[vector.id].pos() == QtCore.QPointF(92, -101)
    assert document.to_dict() == before
    workspace._items_by_id[source.id].setPos(70, -80)
    assert workspace._items_by_id[vector.id].pos() == QtCore.QPointF(70, -80)
    assert document.to_dict() == before


@pytest.mark.parametrize("action", ["move", "numeric"])
def test_main_window_rejected_direct_path_transform_refreshes_canvas(linked_view, action):
    document, _source, vector, history, workspace = linked_view
    notices = []

    def refresh(ids):
        workspace.set_document(document)
        workspace.select_objects(ids)

    window = SimpleNamespace(
        document=document, history=history, workspace=workspace,
        _refresh_document=refresh, show_notice=notices.append,
    )
    before = document.to_dict()
    depth = history.depth
    proposed = vector.transform.copy(x_mm=102, rotation_deg=45)
    # Reproduce already-painted drag geometry, including a programmatic signal.
    workspace._items_by_id[vector.id].preview_transform(
        vector, document.get_layer(vector.layer_id), proposed,
    )
    if action == "move":
        E3MainWindow._object_moved(window, vector.id, (70, 80), (102, 80))
    else:
        E3MainWindow._transform_edited(window, vector.id, proposed)
    assert notices and "detach" in notices[-1]
    assert history.depth == depth
    assert document.to_dict() == before
    graphics = workspace._items_by_id[vector.id]
    assert graphics.pos() == QtCore.QPointF(70, -80)
    assert graphics.rotation() == 0
