from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.project import (
    CommandStack,
    NativePathGeometry,
    ObjectKind,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    ProjectDocument,
    SceneObject,
    object_polylines,
)


def _contour_detection(*, shape: str = "contour") -> dict[str, object]:
    return {
        "id": "trace-asymmetric",
        "index": 3,
        "source": "direct",
        "confidence": 0.91,
        "shape": shape,
        # The fitted rectangle center intentionally differs from the contour's
        # axis-aligned bounding-box center.
        "center_mm": [21.25, 32.75],
        "width_mm": 22.0,
        "height_mm": 15.0,
        "rotation_deg": 27.0,
        "corner_radius_mm": 2.5,
        "contour_mm": [
            [11.0, 28.0],
            [17.0, 23.0],
            [31.0, 29.0],
            [28.0, 43.0],
            [14.0, 39.0],
        ],
    }


def _create_trace_object(
    detection: dict[str, object], output_mode: str
):
    harness = SimpleNamespace(active_layer_id="trace-layer")
    return E3MainWindow._trace_detection_to_object(
        harness,
        detection,
        output_mode,
    )


@pytest.mark.parametrize(
    ("output_mode", "shape"),
    [
        ("exact", "rounded_rectangle"),
        ("smoothed", "rounded_rectangle"),
        ("rounded", "contour"),
    ],
)
def test_contour_trace_object_preserves_preview_world_polyline(
    output_mode: str,
    shape: str,
) -> None:
    detection = _contour_detection(shape=shape)
    preview_contour = [
        [10.5, 28.5],
        [16.5, 22.5],
        [31.5, 28.5],
        [28.5, 43.5],
        [13.5, 39.5],
    ]
    detection["vector_contour_mm"] = preview_contour

    item = _create_trace_object(detection, output_mode)

    assert item.kind == ObjectKind.PATH
    world_points = object_polylines(item)[0].points
    np.testing.assert_allclose(world_points[:-1], preview_contour, atol=1e-12)
    np.testing.assert_allclose(world_points[-1], preview_contour[0], atol=1e-12)
    assert (item.transform.x_mm, item.transform.y_mm) == pytest.approx(
        (21.0, 33.0)
    )
    assert item.metadata["trace_detector_center_mm"] == pytest.approx(
        detection["center_mm"]
    )


def test_contour_trace_object_uses_legacy_contour_when_vector_contour_is_absent(
) -> None:
    detection = _contour_detection()

    item = _create_trace_object(detection, "exact")

    expected = detection["contour_mm"]
    world_points = object_polylines(item)[0].points
    np.testing.assert_allclose(world_points[:-1], expected, atol=1e-12)


def test_recognized_rounded_trace_keeps_fitted_rectangle_transform() -> None:
    detection = _contour_detection(shape="rounded_rectangle")

    item = _create_trace_object(detection, "rounded")

    assert item.kind == ObjectKind.RECTANGLE
    assert (
        item.transform.x_mm,
        item.transform.y_mm,
        item.transform.width_mm,
        item.transform.height_mm,
        item.transform.rotation_deg,
    ) == pytest.approx((21.25, 32.75, 22.0, 15.0, 27.0))
    assert item.geometry["corner_radius_mm"] == pytest.approx(2.5)
    assert "trace_detector_center_mm" not in item.metadata


def test_recognized_washer_trace_creates_one_compound_semantic_object() -> None:
    detection = _contour_detection(shape="washer")
    outer = [[10, 20], [30, 20], [30, 40], [10, 40]]
    inner = [[17, 27], [17, 33], [23, 33], [23, 27]]
    detection["vector_contours_mm"] = [outer, inner]
    detection["diagnostics"] = {"hole_ratio": 0.3}

    item = _create_trace_object(detection, "rounded")

    assert item.kind == ObjectKind.PATH
    assert item.metadata["shape_kind"] == "washer"
    assert item.metadata["hole_ratio"] == pytest.approx(0.3)
    paths = object_polylines(item)
    assert len(paths) == 2
    np.testing.assert_allclose(paths[0].points[:-1], outer)
    np.testing.assert_allclose(paths[1].points[:-1], inner)


def test_verified_contrast_creation_persists_native_lines_and_cubics() -> None:
    detection = _contour_detection()
    native = NativePathGeometry(
        (
            PathSubpath(
                start=(-0.5, -0.5),
                segments=(
                    PathLineSegment((0.5, -0.5)),
                    PathCubicSegment((0.5, -0.2), (0.2, 0.5), (-0.5, 0.5)),
                    PathLineSegment((-0.5, -0.5)),
                ),
                closed=True,
            ),
        ),
        fill_rule=PathFillRule.EVENODD,
    )
    detection.update(
        {
            "source": "direct",
            "native_verified": True,
            "native_path": native.to_dict(),
            "native_center_mm": [42.0, 51.0],
            "native_width_mm": 28.0,
            "native_height_mm": 19.0,
        }
    )

    item = _create_trace_object(detection, "exact")

    assert item.kind == ObjectKind.PATH
    assert item.path_geometry() == native
    assert (
        item.transform.x_mm,
        item.transform.y_mm,
        item.transform.width_mm,
        item.transform.height_mm,
    ) == pytest.approx((42.0, 51.0, 28.0, 19.0))
    assert item.metadata["source_name"] == "camera trace"


def test_normalized_grid_trace_creates_named_grid_cell_with_metadata() -> None:
    detection = _contour_detection(shape="rounded_rectangle")
    detection["diagnostics"] = {
        "grid_normalized": True,
        "grid_row": 2,
        "grid_column": 4,
    }

    item = _create_trace_object(detection, "rounded")

    assert item.name == "Grid R3 C5"
    assert item.metadata["trace_grid_normalized"] is True
    assert item.metadata["trace_grid_row"] == 2
    assert item.metadata["trace_grid_column"] == 4


def test_combined_trace_creation_is_one_evenodd_object_and_one_undo_step() -> None:
    document = ProjectDocument.new()
    first = _contour_detection()
    first["id"] = "first"
    first["vector_contour_mm"] = [
        [10.0, 10.0],
        [30.0, 10.0],
        [30.0, 30.0],
        [10.0, 30.0],
    ]
    first_native = NativePathGeometry(
        (
            PathSubpath(
                start=(-0.5, -0.5),
                segments=(
                    PathLineSegment((0.5, -0.5)),
                    PathCubicSegment((0.5, -0.2), (0.2, 0.5), (-0.5, 0.5)),
                    PathLineSegment((-0.5, -0.5)),
                ),
                closed=True,
            ),
        ),
        fill_rule=PathFillRule.EVENODD,
    )
    first.update(
        {
            "native_verified": True,
            "native_path": first_native.to_dict(),
            "native_center_mm": [20.0, 20.0],
            "native_width_mm": 20.0,
            "native_height_mm": 20.0,
        }
    )
    second = _contour_detection()
    second["id"] = "second"
    second["vector_contour_mm"] = [
        [20.0, 20.0],
        [40.0, 20.0],
        [40.0, 40.0],
        [20.0, 40.0],
    ]

    class Harness:
        def __init__(self) -> None:
            self.document = document
            self.active_layer_id = document.active_layer_id
            self.history = CommandStack()
            self._trace_result = {"detections": [first, second]}
            self.controller = SimpleNamespace(cancel_trace_detection=lambda: None)
            self.workspace = SimpleNamespace(
                clear_trace_preview=lambda: None,
                select_objects=lambda _ids: None,
            )
            self.trace_panel = SimpleNamespace(clear_result=lambda: None)

        def _trace_detection_world_geometry(
            self, detection: dict[str, object]
        ) -> NativePathGeometry:
            return E3MainWindow._trace_detection_world_geometry(detection)

        def _trace_detection_to_object(
            self,
            detection: dict[str, object],
            output_mode: str,
        ) -> SceneObject:
            return E3MainWindow._trace_detection_to_object(
                self,
                detection,
                output_mode,
            )

        def _combined_trace_object(
            self, detections: list[dict[str, object]]
        ) -> SceneObject:
            return E3MainWindow._combined_trace_object(self, detections)

        def _clear_trace_preview(self) -> None:
            E3MainWindow._clear_trace_preview(self)

        def show_notice(self, _message: str) -> None:
            pass

        def show_error(self, message: str) -> None:
            raise AssertionError(message)

    harness = Harness()
    E3MainWindow._create_traced_objects(
        harness,
        {
            "selected_ids": ["first", "second"],
            "output_mode": "exact",
            "purpose": "cut",
            "replace_previous": False,
            "combine": True,
        },
    )

    assert len(document.objects) == 1
    combined = document.objects[0]
    assert combined.kind == ObjectKind.PATH
    assert combined.path_geometry().fill_rule is PathFillRule.EVENODD
    assert len(combined.path_geometry().subpaths) == 2
    assert any(
        isinstance(segment, PathCubicSegment)
        for subpath in combined.path_geometry().subpaths
        for segment in subpath.segments
    )
    assert combined.metadata["trace_compound"] is True
    assert combined.metadata["trace_detection_ids"] == ["first", "second"]
    assert combined.bounds().x_min == pytest.approx(10.0)
    assert combined.bounds().x_max == pytest.approx(40.0)

    harness.history.undo()
    assert document.objects == []

    harness._trace_result = {"detections": [first, second]}
    E3MainWindow._create_traced_objects(
        harness,
        {
            "selected_ids": ["first", "second"],
            "output_mode": "exact",
            "purpose": "cut",
            "replace_previous": False,
            "combine": False,
        },
    )
    assert len(document.objects) == 2
    assert all(item.kind == ObjectKind.PATH for item in document.objects)
    harness.history.undo()
    assert document.objects == []


def test_trace_creation_replaces_only_previous_trace_objects_and_is_undoable() -> None:
    document = ProjectDocument.new()
    old_trace = SceneObject.rectangle(
        document.active_layer_id,
        name="Earlier trace",
        center=(30.0, 30.0),
        width_mm=20.0,
        height_mm=10.0,
    )
    old_trace.metadata["trace_source"] = "direct"
    manual_object = SceneObject.rectangle(
        document.active_layer_id,
        name="Keep me",
        center=(80.0, 80.0),
        width_mm=12.0,
        height_mm=12.0,
    )
    document.add_object(old_trace)
    document.add_object(manual_object)
    cancellations: list[bool] = []
    preview_clears: list[bool] = []
    panel_clears: list[bool] = []
    selected_objects: list[list[str]] = []
    notices: list[str] = []
    errors: list[str] = []

    class Harness:
        def __init__(self) -> None:
            self.document = document
            self.active_layer_id = document.active_layer_id
            self.history = CommandStack()
            self._trace_result = {"detections": [_contour_detection()]}
            self.controller = SimpleNamespace(
                cancel_trace_detection=lambda: cancellations.append(True)
            )
            self.workspace = SimpleNamespace(
                clear_trace_preview=lambda: preview_clears.append(True),
                select_objects=selected_objects.append,
            )
            self.trace_panel = SimpleNamespace(
                clear_result=lambda: panel_clears.append(True)
            )

        def _trace_detection_to_object(
            self,
            detection: dict[str, object],
            output_mode: str,
        ):
            return E3MainWindow._trace_detection_to_object(
                self,
                detection,
                output_mode,
            )

        def _clear_trace_preview(self) -> None:
            E3MainWindow._clear_trace_preview(self)

        def show_notice(self, message: str) -> None:
            notices.append(message)

        def show_error(self, message: str) -> None:
            errors.append(message)

    harness = Harness()
    E3MainWindow._create_traced_objects(
        harness,
        {
            "selected_ids": ["trace-asymmetric"],
            "output_mode": "exact",
        },
    )

    assert len(document.objects) == 2
    assert old_trace.id not in {item.id for item in document.objects}
    assert manual_object.id in {item.id for item in document.objects}
    assert cancellations == [True]
    assert preview_clears == [True]
    assert panel_clears == [True]
    new_trace = next(
        item for item in document.objects if item.id != manual_object.id
    )
    assert selected_objects == [[new_trace.id]]
    assert harness._trace_result is None
    assert notices == ["Replaced 1 earlier Trace object with 1 new object"]
    assert errors == []

    harness.history.undo()
    assert [item.id for item in document.objects] == [
        old_trace.id,
        manual_object.id,
    ]
