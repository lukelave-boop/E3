from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.config import LaserSettings
from laser_aligner.desktop.panels import TracePanel
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import (
    Bounds,
    NativePathGeometry,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    ProjectDocument,
    SceneObject,
    generate_project_gcode,
)
from laser_aligner.vision.trace_orientation import trace_rotation_transform


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _candidate(
    detection_id: str,
    index: int,
    center_x: float,
    *,
    source: str = "direct",
    size: float = 18.0,
    selected_default: bool = True,
) -> dict[str, object]:
    half = size / 2.0
    return {
        "id": detection_id,
        "index": index,
        "source": source,
        "confidence": 0.95,
        "selected_default": selected_default,
        "shape": "contour",
        "center_mm": [center_x, 50.0],
        "width_mm": size,
        "height_mm": size,
        "area_mm2": size * size,
        "vector_contour_mm": [
            [center_x - half, 50.0 - half],
            [center_x + half, 50.0 - half],
            [center_x + half, 50.0 + half],
            [center_x - half, 50.0 + half],
        ],
        "diagnostics": {"within_work_area": True},
    }


def _native_candidate(
    detection_id: str,
    index: int,
    center: tuple[float, float],
) -> dict[str, object]:
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (-0.5, -0.5),
                (
                    PathLineSegment((0.5, -0.5)),
                    PathCubicSegment((0.55, -0.1), (0.45, 0.5), (0.0, 0.5)),
                    PathLineSegment((-0.5, 0.5)),
                    PathLineSegment((-0.5, -0.5)),
                ),
                closed=True,
            ),
        ),
        fill_rule=PathFillRule.EVENODD,
    )
    return {
        "id": detection_id,
        "index": index,
        "source": "direct",
        "confidence": 0.98,
        "selected_default": True,
        "shape": "contour",
        "center_mm": list(center),
        "width_mm": 24.0,
        "height_mm": 12.0,
        "area_mm2": 250.0,
        "native_verified": True,
        "native_path": geometry.to_dict(),
        "native_center_mm": list(center),
        "native_width_mm": 24.0,
        "native_height_mm": 12.0,
        "diagnostics": {"within_work_area": True},
    }


def _path_snapshot(path: QtGui.QPainterPath) -> list[tuple[object, float, float]]:
    return [
        (path.elementAt(index).type, path.elementAt(index).x, path.elementAt(index).y)
        for index in range(path.elementCount())
    ]


def _show(view: WorkspaceView, application: QtWidgets.QApplication) -> None:
    view.resize(720, 480)
    view.show()
    application.processEvents()
    view.fit_work_area()
    application.processEvents()


def _click_candidate(
    view: WorkspaceView,
    center_x: float,
    modifiers: QtCore.Qt.KeyboardModifier = QtCore.Qt.KeyboardModifier.NoModifier,
) -> None:
    position = view.mapFromScene(
        view.workspace_scene.machine_to_scene(center_x, 50.0)
    )
    QtTest.QTest.mouseClick(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        modifiers,
        position,
    )
    QtWidgets.QApplication.processEvents()


def _drag_machine_rect(
    view: WorkspaceView,
    start: tuple[float, float],
    end: tuple[float, float],
) -> None:
    start_view = view.mapFromScene(
        view.workspace_scene.machine_to_scene(*start)
    )
    end_view = view.mapFromScene(view.workspace_scene.machine_to_scene(*end))
    QtTest.QTest.mousePress(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        start_view,
    )
    QtTest.QTest.mouseMove(view.viewport(), end_view, 10)
    QtTest.QTest.mouseRelease(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        end_view,
    )
    QtWidgets.QApplication.processEvents()


def test_click_ctrl_click_empty_click_and_rubber_band_selection(
    qt_application: QtWidgets.QApplication,
) -> None:
    view = WorkspaceView(Bounds(0.0, 0.0, 220.0, 120.0))
    detections = [
        _candidate("direct-1", 1, 40.0),
        _candidate("direct-2", 2, 80.0),
        _candidate("direct-3", 3, 120.0),
        _candidate(
            "inferred-4",
            4,
            160.0,
            source="inferred",
            selected_default=False,
        ),
    ]
    view.set_trace_preview(detections, {"direct-1", "direct-2", "direct-3"})
    _show(view, qt_application)

    assert view.selected_trace_ids() == ["direct-1", "direct-2", "direct-3"]
    _click_candidate(view, 80.0)
    assert view.selected_trace_ids() == ["direct-2"]
    _click_candidate(view, 120.0, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert view.selected_trace_ids() == ["direct-2", "direct-3"]
    _click_candidate(view, 80.0, QtCore.Qt.KeyboardModifier.ControlModifier)
    assert view.selected_trace_ids() == ["direct-3"]

    empty = view.mapFromScene(view.workspace_scene.machine_to_scene(205.0, 105.0))
    QtTest.QTest.mouseClick(
        view.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        empty,
    )
    qt_application.processEvents()
    assert view.selected_trace_ids() == []

    _drag_machine_rect(view, (20.0, 25.0), (135.0, 75.0))
    assert view.selected_trace_ids() == ["direct-1", "direct-2", "direct-3"]
    _drag_machine_rect(view, (20.0, 25.0), (180.0, 75.0))
    assert "inferred-4" not in view.selected_trace_ids()
    _click_candidate(view, 160.0)
    assert view.selected_trace_ids() == ["inferred-4"]

    view.close()
    view.deleteLater()
    qt_application.processEvents()


def test_panel_and_canvas_share_one_selected_id_set(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    view = WorkspaceView(Bounds(0.0, 0.0, 220.0, 120.0))
    detections = [
        _candidate("first", 1, 55.0),
        _candidate("second", 2, 115.0),
    ]
    panel.set_result(
        {
            "mode_used": "contrast",
            "message": "Two candidates",
            "detections": detections,
            "grid": None,
        }
    )
    view.set_trace_preview(detections, panel.selected_ids())
    panel.selectionChanged.connect(view.set_trace_selected_ids)
    view.traceSelectionIdsChanged.connect(panel.set_selected_ids)
    _show(view, qt_application)

    _click_candidate(view, 115.0)
    assert view.selected_trace_ids() == ["second"]
    assert panel.selected_ids() == ["second"]

    panel.set_selected_ids(["first"], emit=True)
    qt_application.processEvents()
    assert view.selected_trace_ids() == ["first"]
    assert panel.selected_ids() == ["first"]

    panel.close()
    view.close()
    panel.deleteLater()
    view.deleteLater()
    qt_application.processEvents()


def test_overlap_zoom_redetect_clear_and_project_selection_restoration(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new(work_area=Bounds(0.0, 0.0, 220.0, 120.0))
    project_object = SceneObject.rectangle(
        document.active_layer_id,
        name="Existing artwork",
        center=(25.0, 95.0),
        width_mm=20.0,
        height_mm=12.0,
    )
    document.add_object(project_object)
    before_preview_job = generate_project_gcode(document, LaserSettings())
    view = WorkspaceView(document.work_area)
    view.set_document(document)
    view.select_objects([project_object.id])
    large = _candidate("large", 1, 90.0, size=36.0, selected_default=False)
    small = _candidate("small", 2, 90.0, size=12.0, selected_default=False)
    view.set_trace_preview([large, small], set())
    _show(view, qt_application)
    during_preview_job = generate_project_gcode(document, LaserSettings())

    assert view.selected_object_ids() == []
    assert during_preview_job.path_count == before_preview_job.path_count == 1
    assert during_preview_job.cut_length_mm == before_preview_job.cut_length_mm
    assert "large" not in during_preview_job.text
    assert "small" not in during_preview_job.text
    assert not (
        view._items_by_id[project_object.id].flags()
        & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    )
    _click_candidate(view, 90.0)
    assert view.selected_trace_ids() == ["small"]
    candidate_item = view._trace_candidates_by_id["small"]
    view.zoom_in()
    view.centerOn(view.workspace_scene.machine_to_scene(90.0, 50.0))
    qt_application.processEvents()
    assert view.selected_trace_ids() == ["small"]
    assert view._trace_candidates_by_id["small"] is candidate_item

    replacement = _candidate("replacement", 1, 145.0)
    view.set_trace_preview([replacement], {"replacement"})
    assert "small" not in view._trace_candidates_by_id
    assert view.selected_trace_ids() == ["replacement"]
    assert document.objects == [project_object]

    view.clear_trace_preview()
    assert view.selected_trace_ids() == []
    assert view.selected_object_ids() == [project_object.id]
    assert (
        view._items_by_id[project_object.id].flags()
        & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    )
    assert document.objects == [project_object]

    view.close()
    view.deleteLater()
    qt_application.processEvents()


def test_straightening_moves_only_selected_vectors_and_reset_is_exact(
    qt_application: QtWidgets.QApplication,
) -> None:
    view = WorkspaceView(Bounds(0.0, 0.0, 220.0, 120.0))
    selected = _native_candidate("selected", 1, (55.0, 50.0))
    unselected = _native_candidate("unselected", 2, (125.0, 50.0))
    camera = QtGui.QImage(16, 12, QtGui.QImage.Format.Format_RGB32)
    camera.fill(QtGui.QColor("#7A5134"))
    view.set_camera_image(camera, image_area=Bounds(0.0, 0.0, 220.0, 120.0))
    view.set_trace_preview([selected, unselected], {"selected"})
    original_selected = _path_snapshot(
        view._trace_candidates_by_id["selected"].path()
    )
    original_unselected = _path_snapshot(
        view._trace_candidates_by_id["unselected"].path()
    )
    original_types = [element[0] for element in original_selected]
    original_camera_key = view._camera_item.pixmap().cacheKey()
    original_camera_area = view._camera_image_area

    view.set_trace_straightening(
        ["selected"],
        trace_rotation_transform(-2.0, (90.0, 50.0)),
    )

    transformed_selected = _path_snapshot(
        view._trace_candidates_by_id["selected"].path()
    )
    assert transformed_selected != original_selected
    assert [element[0] for element in transformed_selected] == original_types
    assert (
        _path_snapshot(view._trace_candidates_by_id["unselected"].path())
        == original_unselected
    )
    assert view.selected_trace_ids() == ["selected"]
    assert view._camera_item.pixmap().cacheKey() == original_camera_key
    assert view._camera_image_area == original_camera_area

    view.set_trace_straightening((), None)

    assert (
        _path_snapshot(view._trace_candidates_by_id["selected"].path())
        == original_selected
    )
    assert (
        _path_snapshot(view._trace_candidates_by_id["unselected"].path())
        == original_unselected
    )
    assert view._camera_item.pixmap().cacheKey() == original_camera_key
    assert view._camera_image_area == original_camera_area

    view.close()
    view.deleteLater()
    qt_application.processEvents()
