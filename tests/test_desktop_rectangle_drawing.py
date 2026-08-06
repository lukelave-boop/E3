from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from types import MethodType, SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import Bounds, CommandStack, ProjectDocument, SceneObject


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _workspace(
    application: QtWidgets.QApplication,
) -> tuple[WorkspaceView, ProjectDocument]:
    area = Bounds(0.0, 0.0, 100.0, 100.0)
    document = ProjectDocument.new(work_area=area)
    view = WorkspaceView(area)
    view.resize(640, 520)
    view.show()
    view.set_document(document)
    view.fit_work_area()
    application.processEvents()
    return view, document


def _close(view: WorkspaceView, application: QtWidgets.QApplication) -> None:
    view.close()
    view.deleteLater()
    application.processEvents()


def _viewport_point(view: WorkspaceView, x_mm: float, y_mm: float) -> QtCore.QPoint:
    return view.mapFromScene(view.workspace_scene.machine_to_scene(x_mm, y_mm))


def _actual_machine_point(
    view: WorkspaceView,
    viewport_point: QtCore.QPoint,
) -> tuple[float, float]:
    return view.workspace_scene.scene_to_machine(view.mapToScene(viewport_point))


def _drag(
    view: WorkspaceView,
    start: QtCore.QPoint,
    end: QtCore.QPoint,
) -> None:
    viewport = view.viewport()
    QtTest.QTest.mousePress(
        viewport,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        start,
    )
    QtTest.QTest.mouseMove(viewport, end)
    QtTest.QTest.mouseRelease(
        viewport,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        end,
    )


def test_select_and_rectangle_actions_are_exclusive_tools(
    qt_application: QtWidgets.QApplication,
) -> None:
    stops: list[str] = []

    class ActionHost(E3MainWindow):
        def __init__(self) -> None:
            QtWidgets.QMainWindow.__init__(self)
            self.history = SimpleNamespace(undo=lambda: None, redo=lambda: None)
            self.workspace = SimpleNamespace(
                fit_work_area=lambda: None,
                fit_selection=lambda: None,
                zoom_in=lambda: None,
                zoom_out=lambda: None,
                set_snap_enabled=lambda _enabled: None,
            )
            self.controller = SimpleNamespace(
                refresh_camera_image=lambda: None,
                emergency_stop=lambda: stops.append("stop"),
            )

    host = ActionHost()
    E3MainWindow._create_actions(host)

    assert host.actions["select_tool"].isCheckable()
    assert host.actions["rectangle"].isCheckable()
    assert host.actions["select_tool"].isChecked()
    host.actions["rectangle"].setChecked(True)
    assert host.actions["rectangle"].isChecked()
    assert not host.actions["select_tool"].isChecked()
    assert host.actions["stop"].shortcut() == QtGui.QKeySequence("Esc")

    host.actions["stop"].trigger()
    assert stops == ["stop"]
    assert host.actions["rectangle"].isChecked()

    host.deleteLater()
    qt_application.processEvents()


def test_rectangle_activation_does_not_insert_a_preset_object(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, document = _workspace(qt_application)
    rectangle_action = QtGui.QAction("Rectangle")
    rectangle_action.setCheckable(True)
    notices: list[str] = []
    harness = SimpleNamespace(
        document=document,
        active_layer_id=document.active_layer_id,
        workspace=view,
        actions={"rectangle": rectangle_action},
        show_notice=notices.append,
    )
    revision = document.revision

    E3MainWindow.add_rectangle(harness)

    assert document.objects == []
    assert document.revision == revision
    assert view.creation_tool == "rectangle"
    assert view.dragMode() == QtWidgets.QGraphicsView.DragMode.NoDrag
    assert view.cursor().shape() == QtCore.Qt.CursorShape.ArrowCursor
    assert rectangle_action.isChecked()
    assert "drag between opposite corners" in notices[-1]
    _close(view, qt_application)


@pytest.mark.parametrize(
    ("start_mm", "end_mm"),
    [
        ((20.0, 30.0), (70.0, 80.0)),
        ((70.0, 30.0), (20.0, 80.0)),
        ((20.0, 80.0), (70.0, 30.0)),
        ((70.0, 80.0), (20.0, 30.0)),
    ],
)
def test_rectangle_drag_previews_and_normalizes_every_direction(
    qt_application: QtWidgets.QApplication,
    start_mm: tuple[float, float],
    end_mm: tuple[float, float],
) -> None:
    view, _ = _workspace(qt_application)
    view.set_snap_enabled(False)
    view.set_creation_tool("rectangle", color="#E7B55C")
    commits: list[tuple[float, float, float, float]] = []
    view.rectangleDrawCommitted.connect(lambda *values: commits.append(values))
    start = _viewport_point(view, *start_mm)
    end = _viewport_point(view, *end_mm)
    actual_start = _actual_machine_point(view, start)
    actual_end = _actual_machine_point(view, end)

    QtTest.QTest.mousePress(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        start,
    )
    assert commits == []
    assert view._rectangle_preview_item is not None
    assert (
        view._rectangle_preview_item.brush().style()
        == QtCore.Qt.BrushStyle.NoBrush
    )
    QtTest.QTest.mouseMove(view.viewport(), end)
    assert view._rectangle_preview_item is not None
    QtTest.QTest.mouseRelease(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        end,
    )

    assert len(commits) == 1
    center_x, center_y, width, height = commits[0]
    assert center_x == pytest.approx((actual_start[0] + actual_end[0]) / 2.0)
    assert center_y == pytest.approx((actual_start[1] + actual_end[1]) / 2.0)
    assert width == pytest.approx(abs(actual_end[0] - actual_start[0]))
    assert height == pytest.approx(abs(actual_end[1] - actual_start[1]))
    assert view._rectangle_preview_item is None
    assert view.creation_tool == "rectangle"
    _close(view, qt_application)


def test_rectangle_creation_snaps_both_corners_and_ignores_degenerate_drags(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _ = _workspace(qt_application)
    view.set_snap_step(10.0)
    view.set_creation_tool("rectangle")
    commits: list[tuple[float, float, float, float]] = []
    view.rectangleDrawCommitted.connect(lambda *values: commits.append(values))

    _drag(
        view,
        _viewport_point(view, 21.2, 31.2),
        _viewport_point(view, 58.7, 52.2),
    )
    assert commits == [pytest.approx((40.0, 40.0, 40.0, 20.0))]

    point = _viewport_point(view, 35.1, 35.1)
    _drag(view, point, point)
    _drag(
        view,
        _viewport_point(view, 41.1, 41.1),
        _viewport_point(view, 44.1, 44.1),
    )
    assert len(commits) == 1
    assert view.creation_tool == "rectangle"
    assert view._rectangle_preview_item is None
    _close(view, qt_application)


def test_minimum_rectangle_accepts_one_tenth_mm_despite_float_rounding(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _ = _workspace(qt_application)

    assert view._rectangle_bounds_are_drawable(Bounds(10.1, 10.1, 10.2, 10.2))
    assert not view._rectangle_bounds_are_drawable(Bounds(10.1, 10.1, 10.1, 10.2))
    _close(view, qt_application)


def test_rectangle_tool_persists_and_select_cancels_an_inflight_draft(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _ = _workspace(qt_application)
    view.set_creation_tool("rectangle")
    commits: list[tuple[float, float, float, float]] = []
    view.rectangleDrawCommitted.connect(lambda *values: commits.append(values))

    _drag(
        view,
        _viewport_point(view, 10.0, 10.0),
        _viewport_point(view, 30.0, 30.0),
    )
    _drag(
        view,
        _viewport_point(view, 50.0, 50.0),
        _viewport_point(view, 80.0, 70.0),
    )
    assert len(commits) == 2
    assert view.creation_tool == "rectangle"

    QtTest.QTest.mouseClick(
        view.viewport(),
        QtCore.Qt.MouseButton.RightButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        _viewport_point(view, 90.0, 90.0),
    )
    assert view.creation_tool == ""
    assert view.dragMode() == QtWidgets.QGraphicsView.DragMode.RubberBandDrag
    view.set_creation_tool("rectangle")

    start = _viewport_point(view, 15.0, 75.0)
    end = _viewport_point(view, 40.0, 90.0)
    QtTest.QTest.mousePress(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        start,
    )
    QtTest.QTest.mouseMove(view.viewport(), end)
    assert view._rectangle_preview_item is not None
    view.set_creation_tool(None)
    QtTest.QTest.mouseRelease(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        end,
    )
    assert len(commits) == 2
    assert view._rectangle_preview_item is None
    assert view.dragMode() == QtWidgets.QGraphicsView.DragMode.RubberBandDrag
    _close(view, qt_application)


def test_space_pan_preempts_rectangle_creation(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, _ = _workspace(qt_application)
    view.zoom_in()
    view.set_creation_tool("rectangle")
    commits: list[tuple[float, float, float, float]] = []
    view.rectangleDrawCommitted.connect(lambda *values: commits.append(values))
    start = _viewport_point(view, 25.0, 25.0)
    end = _viewport_point(view, 45.0, 45.0)
    scroll_before = (
        view.horizontalScrollBar().value(),
        view.verticalScrollBar().value(),
    )

    QtTest.QTest.keyPress(view, QtCore.Qt.Key.Key_Space)
    _drag(view, start, end)
    QtTest.QTest.keyRelease(view, QtCore.Qt.Key.Key_Space)

    assert commits == []
    assert view._rectangle_preview_item is None
    assert view.creation_tool == "rectangle"
    assert (
        view.horizontalScrollBar().value(),
        view.verticalScrollBar().value(),
    ) != scroll_before
    _close(view, qt_application)


def test_rectangle_mode_preserves_direct_resize_handles(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, document = _workspace(qt_application)
    rectangle = SceneObject.rectangle(
        document.active_layer_id,
        center=(50.0, 50.0),
        width_mm=20.0,
        height_mm=10.0,
    )
    document.add_object(rectangle)
    view.set_document(document)
    view.set_snap_enabled(False)
    outline_position = _viewport_point(view, 60.0, 50.0)
    assert not view._creation_hit_is_direct_edit(outline_position)
    view.select_objects([rectangle.id])
    view.set_creation_tool("rectangle")
    qt_application.processEvents()
    assert view._creation_hit_is_direct_edit(outline_position)
    overlay = view._object_transform_overlay
    assert overlay is not None
    transforms: list[tuple[object, ...]] = []
    rectangles: list[tuple[float, float, float, float]] = []
    view.objectTransformCommitted.connect(lambda *payload: transforms.append(payload))
    view.rectangleDrawCommitted.connect(lambda *payload: rectangles.append(payload))

    start = view.mapFromScene(
        overlay.resize_handles["bottom_right"].sceneBoundingRect().center()
    )
    end = _viewport_point(view, 68.0, 37.0)
    _drag(view, start, end)
    qt_application.processEvents()

    assert transforms
    assert rectangles == []
    assert view.creation_tool == "rectangle"
    _close(view, qt_application)


def test_committed_rectangle_uses_one_history_entry_and_selects_then_round_trips(
    qt_application: QtWidgets.QApplication,
) -> None:
    view, document = _workspace(qt_application)
    history = CommandStack()
    notices: list[str] = []
    harness = SimpleNamespace(
        active_layer_id=document.active_layer_id,
        document=document,
        history=history,
        workspace=view,
        show_notice=notices.append,
    )
    harness._add_object = MethodType(E3MainWindow._add_object, harness)
    history.add_listener(lambda _stack: view.set_document(document))

    E3MainWindow._rectangle_draw_committed(harness, 45.0, 55.0, 30.0, 18.0)
    qt_application.processEvents()

    assert len(document.objects) == 1
    rectangle = document.objects[0]
    assert rectangle.transform.x_mm == pytest.approx(45.0)
    assert rectangle.transform.y_mm == pytest.approx(55.0)
    assert rectangle.transform.width_mm == pytest.approx(30.0)
    assert rectangle.transform.height_mm == pytest.approx(18.0)
    assert rectangle.geometry["corner_radius_mm"] == pytest.approx(0.0)
    assert history.depth == 1
    assert history.undo_text == "Add rectangle"
    assert view.selected_object_ids() == [rectangle.id]
    assert view._object_transform_overlay is not None
    assert view._object_transform_overlay.object_id == rectangle.id

    assert history.undo()
    assert document.objects == []
    assert history.redo()
    assert [item.id for item in document.objects] == [rectangle.id]
    assert notices[-1] == "Rectangle created: 30.000 x 18.000 mm"
    _close(view, qt_application)
