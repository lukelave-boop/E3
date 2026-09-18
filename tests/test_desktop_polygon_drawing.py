from __future__ import annotations

# ruff: noqa: E402, I001
import os
from types import MethodType, SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import Bounds, CommandStack, ProjectDocument


class PolygonToolbarHost(E3MainWindow):
    """Actual action/toolbars and canvas with no hardware runtime."""

    def __init__(self):
        QtWidgets.QMainWindow.__init__(self)
        self.document = ProjectDocument.new(work_area=Bounds(0, 0, 100, 100))
        self.active_layer_id = self.document.active_layer_id
        self.workspace = WorkspaceView(self.document.work_area)
        self.workspace.set_document(self.document)
        self.setCentralWidget(self.workspace)
        self.history = CommandStack()
        self.controller = SimpleNamespace(
            retry_camera_image=lambda: None, capture_for_placement=lambda: None,
            emergency_stop=lambda: None,
        )
        self.runtime = SimpleNamespace(settings=SimpleNamespace(
            machine=SimpleNamespace(max_work_feed_mm_min=6000),
        ))
        self._create_actions()
        self._create_toolbars()

    def _refresh_machine_selector(self):
        pass

    def show_notice(self, message, *args, **kwargs):
        pass

    def closeEvent(self, event):
        QtWidgets.QMainWindow.closeEvent(self, event)

    def resizeEvent(self, event):
        QtWidgets.QMainWindow.resizeEvent(self, event)

    def showEvent(self, event):
        QtWidgets.QMainWindow.showEvent(self, event)


@pytest.fixture(scope="module")
def application():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def canvas(application):
    document = ProjectDocument.new(work_area=Bounds(0, 0, 100, 100))
    view = WorkspaceView(document.work_area)
    view.resize(720, 540)
    view.show()
    view.set_document(document)
    view.fit_work_area()
    view.set_snap_enabled(False)
    view.set_creation_tool("polygon")
    application.processEvents()
    yield view, document
    view.close()
    view.deleteLater()
    application.processEvents()


def point(view, x, y):
    return view.mapFromScene(view.workspace_scene.machine_to_scene(x, y))


def click(view, x, y):
    QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=point(view, x, y))


def freeze(view):
    click(view, 50, 50)
    QtTest.QTest.mouseMove(view.viewport(), point(view, 70, 60))
    click(view, 70, 60)
    assert view._polygon_prompt is not None
    return view._polygon_prompt


def test_second_click_locks_size_and_live_typing_keeps_center_and_orientation(canvas):
    view, document = canvas
    commits = []
    view.polygonDrawCommitted.connect(lambda *args: commits.append(args))
    click(view, 50, 50)
    assert view._polygon_prompt is None
    QtTest.QTest.mouseMove(view.viewport(), point(view, 70, 60))
    assert view._polygon_preview_item is not None
    assert document.objects == []
    click(view, 70, 60)
    prompt = view._polygon_prompt
    fixed = (view._polygon_center_mm, view._polygon_radius_mm, view._polygon_angle_deg)
    QtTest.QTest.mouseMove(view.viewport(), point(view, 90, 10))
    before = view._polygon_preview_item.path()
    prompt.sides_edit.selectAll()
    QtTest.QTest.keyClicks(prompt.sides_edit, "3")
    assert view._polygon_preview_item.path() != before
    assert view._polygon_preview_item.path().elementCount() == 4
    assert (view._polygon_center_mm, view._polygon_radius_mm, view._polygon_angle_deg) == fixed
    assert commits == []
    assert document.objects == []
    QtTest.QTest.keyClick(prompt.sides_edit, QtCore.Qt.Key.Key_Return)
    assert len(commits) == 1
    assert commits[0][-1] == 3
    assert view._polygon_preview_item is None
    assert view._polygon_prompt is None
    assert view.creation_tool == "polygon"


@pytest.mark.parametrize("text", ["13", "99", "1000000", "2", "-3", "3.5", "abc", ""])
def test_invalid_count_is_rejected_without_clamping_or_committing(canvas, text):
    view, _ = canvas
    prompt = freeze(view)
    commits = []
    view.polygonDrawCommitted.connect(lambda *args: commits.append(args))
    before = view._polygon_preview_item.path()
    prompt.sides_edit.setText(text)
    assert not prompt.create_button.isEnabled()
    assert "3 to 12" in prompt.message.text()
    assert view._polygon_preview_item.path() == before
    QtTest.QTest.keyClick(prompt.sides_edit, QtCore.Qt.Key.Key_Return)
    assert not commits
    # Typing 12 passes through the temporary invalid value 1.
    prompt.sides_edit.selectAll()
    QtTest.QTest.keyClicks(prompt.sides_edit, "12")
    assert prompt.create_button.isEnabled()
    assert view._polygon_sides == 12
    assert view._polygon_preview_item.path().elementCount() == 13
    prompt.create_button.click()
    assert commits[0][-1] == 12


@pytest.mark.parametrize("cancel", ["button", "right", "select", "document"])
def test_cancellation_discards_draft_without_project_changes(canvas, cancel):
    view, document = canvas
    prompt = freeze(view)
    revision = document.revision
    if cancel == "button":
        prompt.cancel_button.click()
    elif cancel == "right":
        QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.MouseButton.RightButton, pos=point(view, 60, 50))
    elif cancel == "select":
        view.set_creation_tool(None)
    else:
        view.set_document(ProjectDocument.new())
    assert view._polygon_preview_item is None
    assert view._polygon_prompt is None
    assert document.revision == revision
    assert not document.objects


def test_zero_radius_does_not_freeze_and_out_of_area_cannot_commit(canvas):
    view, document = canvas
    click(view, 2, 2)
    click(view, 2, 2)
    assert view._polygon_prompt is None
    click(view, 30, 30)
    prompt = view._polygon_prompt
    assert prompt is not None
    assert not prompt.create_button.isEnabled()
    assert "work area" in prompt.message.text()
    assert not document.objects


def test_polygon_commit_is_one_undo_step_and_round_trips(canvas):
    view, document = canvas
    history = CommandStack()
    host = SimpleNamespace(
        active_layer_id=document.active_layer_id, document=document, history=history,
        workspace=view, show_notice=lambda _: None,
    )
    host._add_object = MethodType(E3MainWindow._add_object, host)
    history.add_listener(lambda _: view.set_document(document))
    view.polygonDrawCommitted.connect(lambda *args: E3MainWindow._polygon_draw_committed(host, *args))
    prompt = freeze(view)
    prompt.sides_edit.setText("5")
    prompt.create_button.click()
    assert len(document.objects) == 1
    assert history.depth == 1
    assert history.undo_text == "Add polygon"
    item = document.objects[0]
    assert view.selected_object_ids() == [item.id]
    restored = ProjectDocument.from_dict(document.to_dict())
    assert restored.objects[0].to_dict() == item.to_dict()
    assert history.undo()
    assert not document.objects
    assert history.redo()
    assert len(document.objects) == 1


def test_space_pan_does_not_place_center(canvas):
    view, _ = canvas
    QtTest.QTest.keyPress(view, QtCore.Qt.Key.Key_Space)
    click(view, 50, 50)
    QtTest.QTest.keyRelease(view, QtCore.Qt.Key.Key_Space)
    assert view._polygon_center_mm is None


def test_polygon_tool_activation_preserves_document_and_uses_active_layer(canvas):
    view, document = canvas
    view.set_creation_tool(None)
    action = QtGui.QAction("Polygon")
    action.setCheckable(True)
    host = SimpleNamespace(
        document=document, active_layer_id=document.active_layer_id, workspace=view,
        actions={"polygon": action}, show_notice=lambda _: None,
    )
    revision = document.revision
    E3MainWindow.add_polygon(host)
    assert action.isChecked()
    assert view.creation_tool == "polygon"
    assert document.revision == revision
    assert not document.objects
    assert view._creation_color.name().lower() == document.layers[0].color.lower()


def test_sidebar_polygon_action_is_visible_exclusive_and_activates_tool(application):
    host = PolygonToolbarHost()
    host.show()
    application.processEvents()
    toolbar = host.findChild(QtWidgets.QToolBar, "drawingToolbar")
    polygon = host.actions["polygon"]
    assert polygon in toolbar.actions()
    assert not polygon.icon().isNull()
    assert toolbar.widgetForAction(polygon).isVisible()
    host.actions["rectangle"].setChecked(True)
    polygon.trigger()
    assert host.workspace.creation_tool == "polygon"
    assert polygon.isChecked()
    assert not host.actions["rectangle"].isChecked()
    assert not host.actions["select_tool"].isChecked()
    host.workspace.set_creation_tool(None)
    host.close()
    host.deleteLater()


def test_snapping_applies_to_center_and_sizing_point(canvas):
    view, _ = canvas
    view.set_snap_enabled(True)
    view.set_snap_step(5)
    click(view, 51, 49)
    click(view, 69, 51)
    assert view._polygon_center_mm == (50, 50)
    assert view._polygon_radius_mm == pytest.approx(20)
    assert view._polygon_angle_deg == pytest.approx(0)


def test_escape_still_triggers_main_window_stop_while_typing(application):
    host = QtWidgets.QMainWindow()
    view = WorkspaceView(Bounds(0, 0, 100, 100))
    host.setCentralWidget(view)
    host.resize(720, 540)
    stops = []
    stop = QtGui.QAction(host)
    stop.setShortcut(QtGui.QKeySequence("Esc"))
    stop.triggered.connect(lambda: stops.append(True))
    host.addAction(stop)
    host.show()
    host.activateWindow()
    view.fit_work_area()
    view.set_creation_tool("polygon")
    application.processEvents()
    prompt = freeze(view)
    application.processEvents()
    QtTest.QTest.keyClick(prompt.sides_edit, QtCore.Qt.Key.Key_Escape)
    assert stops == [True]
    host.close()
    host.deleteLater()
