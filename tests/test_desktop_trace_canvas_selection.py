from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.config import LaserSettings
from laser_aligner.desktop.panels import TracePanel
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import (
    Bounds,
    ProjectDocument,
    SceneObject,
    generate_project_gcode,
)


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


def test_select_objects_emits_only_the_complete_final_selection(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new(work_area=Bounds(0.0, 0.0, 220.0, 120.0))
    first = SceneObject.rectangle(
        document.active_layer_id,
        name="First",
        center=(35.0, 50.0),
        width_mm=20.0,
        height_mm=12.0,
    )
    second = SceneObject.rectangle(
        document.active_layer_id,
        name="Second",
        center=(75.0, 50.0),
        width_mm=20.0,
        height_mm=12.0,
    )
    document.add_object(first)
    document.add_object(second)
    view = WorkspaceView(document.work_area)
    view.set_document(document)
    emitted: list[list[str]] = []
    view.selectionIdsChanged.connect(lambda object_ids: emitted.append(object_ids))

    view.select_objects([first.id, second.id])

    assert len(emitted) == 1
    assert set(emitted[0]) == {first.id, second.id}
    assert set(view.selected_object_ids()) == {first.id, second.id}

    view.select_objects([first.id, second.id])
    assert len(emitted) == 1

    view.close()
    view.deleteLater()
    qt_application.processEvents()


def test_trace_review_announces_project_selection_clear_and_restore(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new(work_area=Bounds(0.0, 0.0, 220.0, 120.0))
    project_object = SceneObject.rectangle(
        document.active_layer_id,
        name="Selected Trace artwork",
        center=(35.0, 50.0),
        width_mm=20.0,
        height_mm=12.0,
    )
    document.add_object(project_object)
    view = WorkspaceView(document.work_area)
    view.set_document(document)
    emitted: list[list[str]] = []
    view.selectionIdsChanged.connect(lambda object_ids: emitted.append(object_ids))

    view.select_objects([project_object.id])
    assert emitted[-1] == [project_object.id]

    detection = _candidate("candidate", 1, 90.0)
    view.set_trace_preview([detection], {"candidate"})
    assert view.selected_object_ids() == []
    assert emitted[-1] == []

    view.clear_trace_preview()
    assert view.selected_object_ids() == [project_object.id]
    assert emitted[-1] == [project_object.id]

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
