from __future__ import annotations

# ruff: noqa: E402, I001
import math
import os
from types import MethodType, SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import (
    Bounds, CommandStack, ObjectKind, ProjectDocument, SceneObject, Transform,
)

CTRL = QtCore.Qt.KeyboardModifier.ControlModifier
SHIFT = QtCore.Qt.KeyboardModifier.ShiftModifier
NONE = QtCore.Qt.KeyboardModifier.NoModifier


@pytest.fixture(scope="module")
def application():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def canvas(application):
    document = ProjectDocument.new(work_area=Bounds(0, 0, 100, 100))
    view = WorkspaceView(document.work_area)
    view.resize(640, 520)
    view.show()
    view.set_document(document)
    view.fit_work_area()
    application.processEvents()
    yield view, document
    view.close()
    view.deleteLater()
    application.processEvents()


def add_object(view, document, kind=ObjectKind.RECTANGLE):
    geometry = {}
    if kind in {ObjectKind.PATH, ObjectKind.POLYGON}:
        geometry = {"polylines": [{"points": [[-0.5, -0.5], [0.5, -0.5], [0, 0.5]], "closed": True}]}
    item = SceneObject(
        layer_id=document.active_layer_id, kind=kind, geometry=geometry,
        transform=Transform(50, 50, 20, 10, rotation_deg=17, mirror_x=True),
    )
    document.add_object(item)
    view.set_document(document)
    view.select_objects([item.id])
    return item, view._object_transform_overlay


def pointer(view, angle):
    angle = math.radians(angle)
    return view.workspace_scene.machine_to_scene(50 + 25 * math.cos(angle), 50 + 25 * math.sin(angle))


@pytest.mark.parametrize("kind", list(ObjectKind))
@pytest.mark.parametrize("raw, expected", [(23, 0), (67, 90), (157, 180), (-72, -90)])
def test_all_object_kinds_snap_absolute_rotation_to_axes(canvas, kind, raw, expected):
    view, document = canvas
    item, overlay = add_object(view, document, kind)
    assert overlay is not None
    before = item.to_dict()
    commits = []
    view.objectTransformCommitted.connect(lambda *args: commits.append(args))
    overlay.begin_rotation(pointer(view, 0))
    overlay.update_rotation(pointer(view, raw - 17), CTRL)
    assert overlay.display_transform.rotation_deg == pytest.approx(Transform.normalized_rotation(expected))
    assert item.to_dict() == before
    overlay.finish_rotation(pointer(view, raw - 17), CTRL)
    assert len(commits) == 1
    after = commits[0][2]
    assert after.to_dict() == item.transform.copy(rotation_deg=expected).to_dict()


@pytest.mark.parametrize("modifiers, expected", [(NONE, 67), (SHIFT, 60), (CTRL | SHIFT, 90)])
def test_free_rotation_and_shift_remain_available_ctrl_takes_priority(canvas, modifiers, expected):
    view, document = canvas
    _, overlay = add_object(view, document)
    overlay.begin_rotation(pointer(view, 0))
    overlay.update_rotation(pointer(view, 50), modifiers)
    assert overlay.display_transform.rotation_deg == pytest.approx(expected)


def test_press_and_release_ctrl_updates_stationary_preview_without_angle_drift(canvas):
    view, document = canvas
    item, overlay = add_object(view, document)
    overlay.begin_rotation(pointer(view, 0))
    overlay.update_rotation(pointer(view, 50))
    for _ in range(3):
        QtTest.QTest.keyPress(view, QtCore.Qt.Key.Key_Control)
        assert overlay.display_transform.rotation_deg == pytest.approx(90)
        QtTest.QTest.keyRelease(view, QtCore.Qt.Key.Key_Control)
        assert overlay.display_transform.rotation_deg == pytest.approx(67)
    assert item.transform.rotation_deg == 17


def test_actual_handle_events_commit_ctrl_snap_as_one_undoable_transform(canvas, application):
    view, document = canvas
    item, overlay = add_object(view, document, ObjectKind.ELLIPSE)
    history = CommandStack()
    host = SimpleNamespace(
        document=document, workspace=view, history=history, show_notice=lambda _: None,
    )
    host._transform_edited = MethodType(E3MainWindow._transform_edited, host)
    history.add_listener(lambda _: view.set_document(document))
    view.objectTransformCommitted.connect(
        lambda *args: E3MainWindow._object_transform_committed(host, *args)
    )
    start_scene = overlay.rotation_handle.sceneBoundingRect().center()
    start_angle = overlay._pointer_angle(start_scene)
    target_scene = pointer(view, start_angle + 50)
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton, CTRL, view.mapFromScene(start_scene))
    QtTest.QTest.mouseMove(view.viewport(), view.mapFromScene(target_scene))
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.MouseButton.LeftButton, CTRL, view.mapFromScene(target_scene))
    application.processEvents()
    assert item.transform.rotation_deg == pytest.approx(90)
    assert history.depth == 1
    assert history.undo()
    assert item.transform.rotation_deg == pytest.approx(17)
    assert history.redo()
    assert item.transform.rotation_deg == pytest.approx(90)


def test_template_snaps_actual_placement_and_releasing_ctrl_restores_free_angle(canvas):
    view, document = canvas
    item = SceneObject.rectangle(document.active_layer_id, center=(50, 50), width_mm=20, height_mm=10)
    view.set_template_preview([item], center_x_mm=50, center_y_mm=50, rotation_deg=17)
    preview = view._template_preview_item
    commits = []
    view.templatePlacementCommitted.connect(lambda *args: commits.append(args))
    preview.begin_rotation(pointer(view, 0))
    preview.update_rotation(pointer(view, 50))
    assert preview.placement()[2] == pytest.approx(67)
    QtTest.QTest.keyPress(view, QtCore.Qt.Key.Key_Control)
    assert preview.placement() == pytest.approx((50, 50, 90))
    QtTest.QTest.keyRelease(view, QtCore.Qt.Key.Key_Control)
    assert preview.placement()[2] == pytest.approx(67)
    preview.finish_rotation(pointer(view, 50), CTRL)
    assert commits == [pytest.approx((50, 50, 90))]


def test_polygon_initial_angle_snaps_without_changing_radius_and_stays_frozen(canvas):
    view, _ = canvas
    view.set_snap_enabled(False)
    view.set_creation_tool("polygon")
    QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                           pos=view.mapFromScene(view.workspace_scene.machine_to_scene(50, 50)))
    QtTest.QTest.mouseMove(view.viewport(), view.mapFromScene(pointer(view, 68)))
    radius, angle = view._polygon_radius_mm, view._polygon_angle_deg
    assert angle == pytest.approx(68, abs=1)
    QtTest.QTest.keyPress(view, QtCore.Qt.Key.Key_Control)
    assert view._polygon_angle_deg == 90
    assert view._polygon_radius_mm == radius
    QtTest.QTest.keyRelease(view, QtCore.Qt.Key.Key_Control)
    assert view._polygon_angle_deg == angle
    QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.MouseButton.LeftButton, CTRL,
                           view.mapFromScene(pointer(view, 68)))
    assert view._polygon_prompt is not None
    assert view._polygon_angle_deg == 90
    QtTest.QTest.keyRelease(view, QtCore.Qt.Key.Key_Control)
    assert view._polygon_angle_deg == 90
