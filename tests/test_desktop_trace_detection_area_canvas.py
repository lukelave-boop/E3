from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import Bounds, ProjectDocument, SceneObject


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def view(qt_application: QtWidgets.QApplication) -> Iterator[WorkspaceView]:
    widget = WorkspaceView(Bounds(0.0, 0.0, 220.0, 120.0))
    widget.resize(720, 480)
    widget.show()
    qt_application.processEvents()
    widget.fit_work_area()
    qt_application.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qt_application.processEvents()


def _position(view: WorkspaceView, point: tuple[float, float]) -> QtCore.QPoint:
    return view.mapFromScene(view.workspace_scene.machine_to_scene(*point))


def _drag(view: WorkspaceView, start: tuple[float, float], end: tuple[float, float]) -> None:
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=_position(view, start))
    QtTest.QTest.mouseMove(view.viewport(), _position(view, end), 10)
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=_position(view, end))
    QtWidgets.QApplication.processEvents()


@pytest.mark.parametrize("start,end", [((30, 25), (95, 80)), ((95, 80), (30, 25))])
@pytest.mark.parametrize("zoom", [1.0, 2.0])
def test_drag_emits_normalized_camera_coordinates_and_persistent_outline(
    view: WorkspaceView, start: tuple[float, float], end: tuple[float, float], zoom: float,
) -> None:
    view.zoom_by(zoom)
    selected: list[object] = []
    view.traceDetectionAreaSelected.connect(selected.append)
    view.begin_trace_detection_area_selection()
    _drag(view, start, end)
    assert len(selected) == 1
    # A viewport event rounds to a device pixel; world rounding follows current zoom.
    tolerance = 1.0 / abs(view.transform().m11())
    assert selected[0] == pytest.approx((30, 25, 95, 80), abs=tolerance)
    assert view.trace_detection_area == selected[0]
    assert not view.trace_detection_area_selection_active
    assert view._trace_detection_area_item.isVisible()
    x0, y0, x1, y1 = view.trace_detection_area
    assert view._trace_detection_area_item.rect() == QtCore.QRectF(x0, -y1, x1 - x0, y1 - y0)
    assert view._trace_detection_area_item.pen().isCosmetic()
    assert view._trace_detection_area_item.acceptedMouseButtons() == QtCore.Qt.MouseButton.NoButton
    view.clear_trace_preview()
    assert view._trace_detection_area_item.isVisible()
    view.set_trace_detection_area(None)
    assert not view._trace_detection_area_item.isVisible()


@pytest.mark.parametrize("cancel", ["escape", "right", "api", "authoring"])
def test_cancel_restores_previous_area_without_committing(view: WorkspaceView, cancel: str) -> None:
    previous = (10.0, 20.0, 40.0, 60.0)
    view.set_trace_detection_area(previous)
    selected: list[object] = []
    cancelled: list[bool] = []
    view.traceDetectionAreaSelected.connect(selected.append)
    view.traceDetectionAreaSelectionCancelled.connect(lambda: cancelled.append(True))
    view.begin_trace_detection_area_selection()
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=_position(view, (60, 30)))
    QtTest.QTest.mouseMove(view.viewport(), _position(view, (90, 70)), 10)
    if cancel == "escape":
        QtTest.QTest.keyClick(view, QtCore.Qt.Key.Key_Escape)
    elif cancel == "right":
        QtTest.QTest.mouseClick(view.viewport(), QtCore.Qt.MouseButton.RightButton)
    elif cancel == "authoring":
        view.cancel_authoring_interaction()
    else:
        view.cancel_trace_detection_area_selection()
    assert view.trace_detection_area == previous
    assert not view.trace_detection_area_selection_active
    assert view._trace_detection_area_item.rect() == QtCore.QRectF(10, -60, 30, 40)
    assert cancelled == [True]
    assert selected == []


def test_click_and_flat_drag_keep_previous_area_and_allow_retry(view: WorkspaceView) -> None:
    previous = (10.0, 20.0, 40.0, 60.0)
    view.set_trace_detection_area(previous)
    selected: list[object] = []
    view.traceDetectionAreaSelected.connect(selected.append)
    view.begin_trace_detection_area_selection()
    _drag(view, (60, 30), (60, 30))
    _drag(view, (60, 30), (90, 30))
    assert view.trace_detection_area_selection_active
    assert view.trace_detection_area == previous
    assert selected == []
    _drag(view, (60, 30), (90, 70))
    assert len(selected) == 1


def test_selection_blocks_project_editing_and_does_not_snap(view: WorkspaceView) -> None:
    document = ProjectDocument.new(work_area=view.workspace_scene.work_area)
    item = SceneObject.rectangle(document.active_layer_id, name="Existing", center=(50, 50), width_mm=30, height_mm=30)
    document.add_object(item)
    view.set_document(document)
    view.select_objects([item.id])
    original = item.to_dict()
    deleted: list[bool] = []
    moved: list[object] = []
    view.deleteRequested.connect(lambda: deleted.append(True))
    view.objectMoveCommitted.connect(lambda *args: moved.append(args))
    view.snap_enabled = True
    view.snap_step_mm = 50
    view.begin_trace_detection_area_selection()
    QtTest.QTest.keyClick(view, QtCore.Qt.Key.Key_Delete)
    _drag(view, (50, 50), (87, 77))
    assert item.to_dict() == original
    assert deleted == [] and moved == []
    assert view.selected_object_ids() == [item.id]
    assert view.trace_detection_area == pytest.approx((50, 50, 87, 77), abs=0.5)


def test_drag_uses_displayed_camera_bounds_including_offset(view: WorkspaceView) -> None:
    # The scene uses the current camera frame, including honeycomb-local displays.
    area = Bounds(-20, -10, 80, 60)
    image = QtGui.QImage(100, 70, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("white"))
    view.set_camera_image(image, pixels_per_mm=1.0, image_area=area)
    view.fit_camera_image()
    view.begin_trace_detection_area_selection()
    _drag(view, (-25, -15), (85, 65))
    assert view.trace_detection_area == pytest.approx((-20, -10, 80, 60))


def test_area_selection_does_not_change_trace_candidate_selection(view: WorkspaceView) -> None:
    candidate = {
        "id": "part", "index": 1, "source": "direct", "confidence": 0.95,
        "selected_default": True, "shape": "contour", "center_mm": [50.0, 50.0],
        "width_mm": 20.0, "height_mm": 20.0, "area_mm2": 400.0,
        "vector_contour_mm": [[40.0, 40.0], [60.0, 40.0], [60.0, 60.0], [40.0, 60.0]],
        "diagnostics": {"within_work_area": True},
    }
    view.set_trace_preview([candidate], {"part"})
    changed: list[object] = []
    view.traceSelectionIdsChanged.connect(changed.append)
    view.begin_trace_detection_area_selection()
    _drag(view, (50, 50), (90, 90))
    assert view.selected_trace_ids() == ["part"]
    assert changed == []


@pytest.mark.parametrize("next_tool", ["rectangle", "point_pick"])
def test_other_drawing_tools_end_area_mode(view: WorkspaceView, next_tool: str) -> None:
    previous = (10.0, 20.0, 40.0, 60.0)
    view.set_trace_detection_area(previous)
    view.begin_trace_detection_area_selection()
    if next_tool == "point_pick":
        view.begin_point_pick()
    else:
        view.set_creation_tool(next_tool)
    assert not view.trace_detection_area_selection_active
    assert view.trace_detection_area == previous


@pytest.mark.parametrize("area", [(0, 0, 0, 1), (2, 0, 1, 1), (0, 0, float("nan"), 1), (0, 0, 1)])
def test_invalid_area_does_not_replace_prior_rectangle(view: WorkspaceView, area: tuple[float, ...]) -> None:
    previous = (10.0, 20.0, 40.0, 60.0)
    view.set_trace_detection_area(previous)
    with pytest.raises(ValueError):
        view.set_trace_detection_area(area)
    assert view.trace_detection_area == previous
