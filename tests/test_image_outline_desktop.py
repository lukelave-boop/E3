"""Offscreen verification of linked outline transform previews and rejection."""
from __future__ import annotations

import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtCore, QtTest, QtWidgets

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


def test_linked_path_has_move_and_resize_controls_before_and_after_detach(linked_view):
    document, _source, vector, history, workspace = linked_view
    workspace.select_objects([vector.id])
    item = workspace._items_by_id[vector.id]
    assert item.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert workspace._object_transform_overlay.object_id == vector.id
    history.execute(DetachOutlineCommand(document, vector.id))
    workspace.set_document(document)
    workspace.select_objects([vector.id])
    item = workspace._items_by_id[vector.id]
    assert item.flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert workspace._object_transform_overlay.object_id == vector.id


@pytest.mark.parametrize("driver", ["image", "outline", "pair"])
def test_either_live_preview_and_cancel_keep_pair_aligned_without_decode(linked_view, monkeypatch, driver):
    document, source, vector, _history, workspace = linked_view
    before = document.to_dict()
    workspace.select_objects(
        [source.id, vector.id] if driver == "pair" else [source.id if driver == "image" else vector.id]
    )
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


@pytest.mark.parametrize("driver", ["image", "outline"])
def test_live_drag_moves_pair_without_mutating_document(linked_view, driver):
    document, source, vector, _history, workspace = linked_view
    before = document.to_dict()
    driver_id = source.id if driver == "image" else vector.id
    workspace._items_by_id[driver_id].setPos(92, -101)
    assert all(workspace._items_by_id[item.id].pos() == QtCore.QPointF(92, -101) for item in (source, vector))
    assert document.to_dict() == before
    workspace._items_by_id[driver_id].setPos(70, -80)
    assert workspace._items_by_id[vector.id].pos() == QtCore.QPointF(70, -80)
    assert document.to_dict() == before


@pytest.mark.parametrize("action", ["move", "numeric"])
def test_main_window_path_transform_moves_pair_in_one_undo_step(linked_view, action):
    document, source, vector, history, workspace = linked_view
    notices = []

    def refresh(ids):
        workspace.set_document(document)
        workspace.select_objects(ids)

    window = SimpleNamespace(
        document=document, history=history, workspace=workspace,
        _refresh_document=refresh, show_notice=notices.append,
    )
    history.add_listener(lambda _stack: workspace.set_document(document))
    before = source.transform.to_dict()
    depth = history.depth
    proposed = vector.transform.copy(x_mm=102, rotation_deg=45 if action == "numeric" else 0)
    # Reproduce already-painted drag geometry, including a programmatic signal.
    workspace._items_by_id[vector.id].preview_transform(
        vector, document.get_layer(vector.layer_id), proposed,
    )
    if action == "move":
        E3MainWindow._object_moved(window, vector.id, (70, 80), (102, 80))
    else:
        E3MainWindow._transform_edited(window, vector.id, proposed)
    assert not notices
    assert history.depth == depth + 1
    assert source.transform.to_dict() == vector.transform.to_dict() == proposed.to_dict()
    for item in (source, vector):
        graphics = workspace._items_by_id[item.id]
        assert graphics.pos() == QtCore.QPointF(102, -80)
        assert graphics.rotation() == -proposed.rotation_deg
    history.undo()
    assert source.transform.to_dict() == vector.transform.to_dict() == before
    history.redo()
    assert source.transform.to_dict() == vector.transform.to_dict() == proposed.to_dict()


@pytest.mark.parametrize("locked_member", ["image", "outline"])
def test_locking_either_member_disables_pair_handles_and_drag(linked_view, locked_member):
    document, source, vector, _history, workspace = linked_view
    locked = source if locked_member == "image" else vector
    locked.locked = True
    workspace.set_document(document)
    for item in (source, vector):
        workspace.select_objects([item.id])
        assert workspace._object_transform_overlay is None
        workspace.refresh_object(item.id)
        assert not workspace._items_by_id[item.id].flags() & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable


@pytest.mark.parametrize("selection", ["none", "pair"])
def test_mouse_drag_topmost_outline_moves_pair_once_with_live_preview(linked_view, app, selection):
    document, source, vector, history, workspace = linked_view
    workspace.resize(800, 650)
    workspace.show()
    workspace.fit_work_area()
    workspace.set_snap_enabled(False)
    app.processEvents()
    notices = []
    window = SimpleNamespace(
        document=document, history=history, workspace=workspace,
        _refresh_document=lambda ids: workspace.set_document(document), show_notice=notices.append,
    )
    history.add_listener(lambda _stack: workspace.set_document(document))
    workspace.objectMoveCommitted.connect(lambda *args: E3MainWindow._object_moved(window, *args))
    workspace.select_objects([source.id, vector.id] if selection == "pair" else [])
    start = workspace.mapFromScene(QtCore.QPointF(70, -80))
    end = start + QtCore.QPoint(35, -23)
    delta = workspace.mapToScene(end) - workspace.mapToScene(start)
    before = source.transform.to_dict()
    depth = history.depth
    viewport = workspace.viewport()
    QtTest.QTest.mousePress(viewport, QtCore.Qt.MouseButton.LeftButton, pos=start)
    QtTest.QTest.mouseMove(viewport, end)
    for item in (source, vector):
        graphics = workspace._items_by_id[item.id]
        assert graphics.pos().x() == pytest.approx(70 + delta.x())
        assert graphics.pos().y() == pytest.approx(-80 + delta.y())
        assert item.transform.to_dict() == before
    QtTest.QTest.mouseRelease(viewport, QtCore.Qt.MouseButton.LeftButton, pos=end)
    assert not notices
    assert history.depth == depth + 1
    assert source.transform.to_dict() == vector.transform.to_dict()
    assert source.transform.x_mm == pytest.approx(70 + delta.x())
    assert source.transform.y_mm == pytest.approx(80 - delta.y())
    history.undo()
    assert source.transform.to_dict() == vector.transform.to_dict() == before
    history.redo()
    document.validate()
