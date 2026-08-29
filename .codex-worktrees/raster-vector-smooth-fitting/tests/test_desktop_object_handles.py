from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import math
import os
from collections.abc import Iterator
from types import MethodType, SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import (
    Bounds,
    CommandStack,
    ProjectDocument,
    SceneObject,
    Transform,
)


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _workspace_with_rect(
    application: QtWidgets.QApplication,
    *,
    locked: bool = False,
    rotation_deg: float = 0.0,
    center: tuple[float, float] = (50.0, 50.0),
    width_mm: float = 20.0,
    height_mm: float = 10.0,
) -> tuple[WorkspaceView, ProjectDocument, SceneObject]:
    area = Bounds(0.0, 0.0, 100.0, 100.0)
    document = ProjectDocument.new(work_area=area)
    rectangle = SceneObject.rectangle(
        document.active_layer_id,
        center=center,
        width_mm=width_mm,
        height_mm=height_mm,
    )
    rectangle.locked = locked
    rectangle.transform = rectangle.transform.copy(rotation_deg=rotation_deg)
    document.add_object(rectangle)
    view = WorkspaceView(area)
    view.resize(640, 520)
    view.show()
    view.set_document(document)
    application.processEvents()
    view.fit_work_area()
    application.processEvents()
    return view, document, rectangle


def _close(view: WorkspaceView, application: QtWidgets.QApplication) -> None:
    view.close()
    view.deleteLater()
    application.processEvents()


def test_handles_require_one_unlocked_selected_object(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, document, rectangle = _workspace_with_rect(qt_application)
    assert view._object_transform_overlay is None

    view.select_objects([rectangle.id])
    qt_application.processEvents()

    overlay = view._object_transform_overlay
    assert overlay is not None
    assert overlay.object_id == rectangle.id
    assert set(overlay.resize_handles) == {
        "top_left",
        "top_right",
        "bottom_right",
        "bottom_left",
    }
    for handle in [*overlay.resize_handles.values(), overlay.rotation_handle]:
        assert handle.flags() & (
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        )

    second = SceneObject.ellipse(
        document.active_layer_id,
        center=(20.0, 20.0),
        width_mm=8.0,
        height_mm=8.0,
    )
    document.add_object(second)
    view.set_document(document)
    view.select_objects([rectangle.id, second.id])
    qt_application.processEvents()
    assert view._object_transform_overlay is None

    rectangle.locked = True
    view.set_document(document)
    view.select_objects([rectangle.id])
    qt_application.processEvents()
    assert view._object_transform_overlay is None

    rectangle.locked = False
    rectangle.visible = False
    view.set_document(document)
    view.select_objects([rectangle.id])
    qt_application.processEvents()
    assert view._object_transform_overlay is None

    rectangle.visible = True
    document.layers[0].visible = False
    view.set_document(document)
    view.select_objects([rectangle.id])
    qt_application.processEvents()
    assert view._object_transform_overlay is None

    _close(view, qt_application)


def test_corner_resize_previews_without_mutating_document_and_emits_transform(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, document, rectangle = _workspace_with_rect(qt_application)
    view.set_snap_enabled(False)
    view.select_objects([rectangle.id])
    overlay = view._object_transform_overlay
    assert overlay is not None
    original = rectangle.transform.to_dict()
    committed: list[tuple[str, object, object]] = []
    view.objectTransformCommitted.connect(
        lambda object_id, before, after: committed.append((object_id, before, after))
    )

    start = overlay.resize_handles["bottom_right"].sceneBoundingRect().center()
    target = view.workspace_scene.machine_to_scene(70.0, 35.0)
    overlay.begin_resize("bottom_right", start)
    overlay.finish_resize(target)

    assert len(committed) == 1
    object_id, before, after = committed[0]
    assert object_id == rectangle.id
    assert before.to_dict() == original
    assert after.x_mm == pytest.approx(55.0)
    assert after.y_mm == pytest.approx(45.0)
    assert after.width_mm == pytest.approx(30.0)
    assert after.height_mm == pytest.approx(20.0)
    assert rectangle.transform.to_dict() == original
    assert view._items_by_id[rectangle.id].pos() == QtCore.QPointF(55.0, -45.0)

    _close(view, qt_application)


def test_rotated_resize_keeps_opposite_corner_fixed(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _document, rectangle = _workspace_with_rect(
        qt_application,
        rotation_deg=90.0,
    )
    view.set_snap_enabled(False)
    view.select_objects([rectangle.id])
    overlay = view._object_transform_overlay
    assert overlay is not None
    original_fixed_corner = rectangle.transform.corners()[3]
    handle_machine = view.workspace_scene.scene_to_machine(
        overlay.resize_handles["bottom_right"].sceneBoundingRect().center()
    )
    assert handle_machine == pytest.approx(rectangle.transform.corners()[1])

    # In object-local machine coordinates this grows the bottom-right corner
    # from (+10, -5) to (+20, -10). The opposite top-left remains anchored.
    target_machine = (60.0, 70.0)
    target_scene = view.workspace_scene.machine_to_scene(*target_machine)
    start = overlay.resize_handles["bottom_right"].sceneBoundingRect().center()
    overlay.begin_resize("bottom_right", start)
    overlay.finish_resize(target_scene)
    after = overlay.display_transform

    assert after.width_mm == pytest.approx(30.0)
    assert after.height_mm == pytest.approx(15.0)
    assert after.corners()[3] == pytest.approx(original_fixed_corner)
    assert rectangle.transform.rotation_deg == pytest.approx(90.0)

    _close(view, qt_application)


def test_resize_cannot_cross_the_fixed_corner(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _document, rectangle = _workspace_with_rect(qt_application)
    view.set_snap_enabled(False)
    view.select_objects([rectangle.id])
    overlay = view._object_transform_overlay
    assert overlay is not None

    start = overlay.resize_handles["bottom_right"].sceneBoundingRect().center()
    # This pointer is beyond the opposite corner on both axes. The handle
    # stops at the minimum rather than producing a negative or mirrored size.
    target = view.workspace_scene.machine_to_scene(20.0, 80.0)
    overlay.begin_resize("bottom_right", start)
    overlay.finish_resize(target)
    after = overlay.display_transform

    assert after.width_mm == pytest.approx(overlay._MINIMUM_SIZE_MM)
    assert after.height_mm == pytest.approx(overlay._MINIMUM_SIZE_MM)
    assert after.width_mm > 0.0
    assert after.height_mm > 0.0

    _close(view, qt_application)


def test_rotation_handle_shift_snaps_and_preserves_model(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _document, rectangle = _workspace_with_rect(qt_application)
    view.select_objects([rectangle.id])
    overlay = view._object_transform_overlay
    assert overlay is not None
    committed: list[tuple[str, object, object]] = []
    view.objectTransformCommitted.connect(
        lambda object_id, before, after: committed.append((object_id, before, after))
    )

    center = (rectangle.transform.x_mm, rectangle.transform.y_mm)
    start_scene = view.workspace_scene.machine_to_scene(center[0], center[1] + 20.0)
    pointer_angle = math.radians(122.0)
    target_scene = view.workspace_scene.machine_to_scene(
        center[0] + math.cos(pointer_angle) * 20.0,
        center[1] + math.sin(pointer_angle) * 20.0,
    )
    overlay.begin_rotation(start_scene)
    overlay.finish_rotation(
        target_scene,
        QtCore.Qt.KeyboardModifier.ShiftModifier,
    )

    assert len(committed) == 1
    _object_id, before, after = committed[0]
    assert before.rotation_deg == pytest.approx(0.0)
    assert after.rotation_deg == pytest.approx(30.0)
    assert after.x_mm == pytest.approx(before.x_mm)
    assert after.y_mm == pytest.approx(before.y_mm)
    assert after.width_mm == pytest.approx(before.width_mm)
    assert after.height_mm == pytest.approx(before.height_mm)
    assert rectangle.transform.rotation_deg == pytest.approx(0.0)

    _close(view, qt_application)


def test_resize_handle_receives_an_offscreen_mouse_drag(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _document, rectangle = _workspace_with_rect(qt_application)
    view.set_snap_enabled(False)
    view.select_objects([rectangle.id])
    qt_application.processEvents()
    overlay = view._object_transform_overlay
    assert overlay is not None
    commits: list[object] = []
    view.objectTransformCommitted.connect(lambda *payload: commits.append(payload))

    handle_center = overlay.resize_handles["bottom_right"].sceneBoundingRect().center()
    start = view.mapFromScene(handle_center)
    end = view.mapFromScene(view.workspace_scene.machine_to_scene(68.0, 37.0))
    QtTest.QTest.mousePress(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        start,
    )
    QtTest.QTest.mouseMove(view.viewport(), end, 10)
    QtTest.QTest.mouseRelease(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        end,
    )
    qt_application.processEvents()

    assert commits
    after = commits[-1][2]
    assert after.width_mm > rectangle.transform.width_mm
    assert after.height_mm > rectangle.transform.height_mm

    _close(view, qt_application)


def test_clicking_resize_handle_does_not_snap_or_create_history(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _document, rectangle = _workspace_with_rect(
        qt_application,
        center=(50.2, 50.4),
        width_mm=20.3,
        height_mm=10.7,
    )
    view.select_objects([rectangle.id])
    qt_application.processEvents()
    overlay = view._object_transform_overlay
    assert overlay is not None
    original = rectangle.transform.to_dict()
    commits: list[object] = []
    view.objectTransformCommitted.connect(lambda *payload: commits.append(payload))

    handle_center = overlay.resize_handles["bottom_right"].sceneBoundingRect().center()
    position = view.mapFromScene(handle_center)
    QtTest.QTest.mousePress(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        position,
    )
    QtTest.QTest.mouseRelease(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        position,
    )
    qt_application.processEvents()

    assert commits == []
    assert rectangle.transform.to_dict() == original
    assert overlay.display_transform.to_dict() == original

    _close(view, qt_application)


def test_main_window_commits_direct_rectangle_resize_atomically() -> None:
    document = ProjectDocument.new()
    rectangle = SceneObject.rectangle(
        document.active_layer_id,
        width_mm=20.0,
        height_mm=10.0,
        corner_radius_mm=4.0,
    )
    document.add_object(rectangle)
    calls: list[tuple[str, Transform, float]] = []
    window = SimpleNamespace(
        document=document,
        workspace=SimpleNamespace(refresh_object=lambda _object_id: None),
        show_notice=lambda _message: None,
        _rectangle_shape_edited=lambda object_id, transform, radius: calls.append(
            (object_id, transform, radius)
        ),
        _transform_edited=lambda _object_id, _transform: pytest.fail(
            "Rectangle resize must use the atomic shape edit"
        ),
    )
    after = rectangle.transform.copy(width_mm=6.0, height_mm=5.0)

    E3MainWindow._object_transform_committed(
        window,
        rectangle.id,
        rectangle.transform.copy(),
        after,
    )

    assert len(calls) == 1
    object_id, committed, radius = calls[0]
    assert object_id == rectangle.id
    assert committed.to_dict() == after.to_dict()
    assert radius == pytest.approx(2.5)


def test_direct_rectangle_resize_round_trips_through_history_and_canvas(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, document, rectangle = _workspace_with_rect(qt_application)
    rectangle.geometry["corner_radius_mm"] = 4.0
    view.set_document(document)
    view.set_snap_enabled(False)
    view.select_objects([rectangle.id])
    history = CommandStack()
    notices: list[str] = []
    window = SimpleNamespace(
        document=document,
        workspace=view,
        history=history,
        show_notice=notices.append,
    )
    window._rectangle_shape_edited = MethodType(
        E3MainWindow._rectangle_shape_edited,
        window,
    )
    window._transform_edited = MethodType(E3MainWindow._transform_edited, window)
    history.add_listener(lambda _stack: view.set_document(document))
    view.objectTransformCommitted.connect(
        lambda object_id, before, after: E3MainWindow._object_transform_committed(
            window,
            object_id,
            before,
            after,
        )
    )

    overlay = view._object_transform_overlay
    assert overlay is not None
    start = overlay.resize_handles["bottom_right"].sceneBoundingRect().center()
    target = view.workspace_scene.machine_to_scene(53.0, 47.5)
    overlay.begin_resize("bottom_right", start)
    overlay.finish_resize(target)

    changed = rectangle.transform.copy()
    assert (changed.width_mm, changed.height_mm) == pytest.approx((13.0, 7.5))
    assert rectangle.geometry["corner_radius_mm"] == pytest.approx(3.75)
    assert history.depth == 1
    assert view.selected_object_ids() == [rectangle.id]
    assert view._object_transform_overlay is not None
    assert view._object_transform_overlay.display_transform.to_dict() == changed.to_dict()

    assert history.undo()
    assert rectangle.transform.width_mm == pytest.approx(20.0)
    assert rectangle.transform.height_mm == pytest.approx(10.0)
    assert rectangle.geometry["corner_radius_mm"] == pytest.approx(4.0)
    assert view._object_transform_overlay is not None
    assert view._object_transform_overlay.display_transform.to_dict() == (
        rectangle.transform.to_dict()
    )

    assert history.redo()
    assert rectangle.transform.to_dict() == changed.to_dict()
    assert rectangle.geometry["corner_radius_mm"] == pytest.approx(3.75)
    assert view._object_transform_overlay is not None
    assert view._object_transform_overlay.display_transform.to_dict() == changed.to_dict()
    assert notices == []

    _close(view, qt_application)


def test_stale_direct_transform_is_rejected_and_canvas_is_restored() -> None:
    document = ProjectDocument.new()
    rectangle = SceneObject.rectangle(document.active_layer_id)
    document.add_object(rectangle)
    before = rectangle.transform.copy()
    rectangle.transform = rectangle.transform.copy(x_mm=18.0)
    refreshed: list[str] = []
    notices: list[str] = []
    window = SimpleNamespace(
        document=document,
        workspace=SimpleNamespace(refresh_object=refreshed.append),
        show_notice=notices.append,
        _rectangle_shape_edited=lambda *_args: pytest.fail(
            "A stale transform must not be committed"
        ),
        _transform_edited=lambda *_args: pytest.fail(
            "A stale transform must not be committed"
        ),
    )

    E3MainWindow._object_transform_committed(
        window,
        rectangle.id,
        before,
        before.copy(width_mm=25.0),
    )

    assert refreshed == [rectangle.id]
    assert notices == ["The object changed; direct transform was cancelled"]
    assert rectangle.transform.x_mm == pytest.approx(18.0)
    assert rectangle.transform.width_mm == pytest.approx(before.width_mm)
