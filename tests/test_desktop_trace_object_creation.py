from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.project import (
    CommandStack,
    NativePathGeometry,
    ObjectKind,
    PathAffineTransform,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    ProjectDocument,
    SceneObject,
    native_path_bounds,
    object_polylines,
    transform_native_path,
)
from laser_aligner.vision.trace_orientation import (
    estimate_trace_orientation,
    trace_rotation_transform,
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


def _native_detection_from_world(
    detection_id: str,
    index: int,
    geometry: NativePathGeometry,
) -> dict[str, object]:
    x_min, y_min, x_max, y_max = native_path_bounds(geometry)
    width = x_max - x_min
    height = y_max - y_min
    center = ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)
    normalized = transform_native_path(
        geometry,
        PathAffineTransform.from_components(
            scale_x=1.0 / width,
            scale_y=1.0 / height,
            translate_x=-center[0] / width,
            translate_y=-center[1] / height,
        ),
    )
    return {
        "id": detection_id,
        "index": index,
        "source": "direct",
        "confidence": 0.98,
        "selected_default": True,
        "shape": "contour",
        "center_mm": list(center),
        "width_mm": width,
        "height_mm": height,
        "native_verified": True,
        "native_path": normalized.to_dict(),
        "native_center_mm": list(center),
        "native_width_mm": width,
        "native_height_mm": height,
        "diagnostics": {
            "within_work_area": True,
            "native_fit_status": "verified",
        },
    }


def _world_bar(
    center: tuple[float, float],
    *,
    with_hole_and_cubic: bool = False,
) -> NativePathGeometry:
    cx, cy = center
    outer_segments = (
        PathLineSegment((cx + 12.0, cy - 2.0)),
        PathCubicSegment(
            (cx + 12.1, cy - 0.7),
            (cx + 11.9, cy + 0.7),
            (cx + 12.0, cy + 2.0),
        )
        if with_hole_and_cubic
        else PathLineSegment((cx + 12.0, cy + 2.0)),
        PathLineSegment((cx - 12.0, cy + 2.0)),
        PathLineSegment((cx - 12.0, cy - 2.0)),
    )
    subpaths = [
        PathSubpath(
            (cx - 12.0, cy - 2.0),
            outer_segments,
            closed=True,
        )
    ]
    if with_hole_and_cubic:
        subpaths.append(
            PathSubpath(
                (cx - 2.0, cy - 0.8),
                (
                    PathLineSegment((cx - 2.0, cy + 0.8)),
                    PathLineSegment((cx + 2.0, cy + 0.8)),
                    PathLineSegment((cx + 2.0, cy - 0.8)),
                    PathLineSegment((cx - 2.0, cy - 0.8)),
                ),
                closed=True,
            )
        )
    return NativePathGeometry(tuple(subpaths), fill_rule=PathFillRule.EVENODD)


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


def test_straightened_separate_and_compound_creation_match_previewed_group_transform(
) -> None:
    base_geometries = [
        _world_bar((30.0, 35.0), with_hole_and_cubic=True),
        _world_bar((70.0, 35.0)),
    ]
    combined_bounds = [native_path_bounds(geometry) for geometry in base_geometries]
    pivot = (
        (
            min(bounds[0] for bounds in combined_bounds)
            + max(bounds[2] for bounds in combined_bounds)
        )
        / 2.0,
        (
            min(bounds[1] for bounds in combined_bounds)
            + max(bounds[3] for bounds in combined_bounds)
        )
        / 2.0,
    )
    detected_rotation = trace_rotation_transform(2.0, pivot)
    detections = [
        _native_detection_from_world(
            f"candidate-{index}",
            index,
            transform_native_path(geometry, detected_rotation),
        )
        for index, geometry in enumerate(base_geometries, start=1)
    ]
    estimate = estimate_trace_orientation(detections)
    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.08)
    assert estimate.correction_deg is not None
    assert estimate.pivot_mm is not None
    selected_ids = [str(item["id"]) for item in detections]

    class Harness:
        def __init__(self, *, straighten: bool) -> None:
            self.document = ProjectDocument.new()
            self.active_layer_id = self.document.active_layer_id
            self.history = CommandStack()
            self._trace_result = {
                "detections": detections,
                "options": {"regular_grid": False, "output_mode": "native"},
            }
            self._trace_orientation_estimate = estimate if straighten else None
            self._trace_straightening = estimate if straighten else None
            self.controller = SimpleNamespace(cancel_trace_detection=lambda: None)
            self.workspace = SimpleNamespace(
                clear_trace_preview=lambda: None,
                select_objects=lambda _ids: None,
            )
            self.trace_panel = SimpleNamespace(clear_result=lambda: None)

        def _trace_detection_world_geometry(self, detection):
            return E3MainWindow._trace_detection_world_geometry(detection)

        def _trace_detection_to_object(self, detection, output_mode):
            return E3MainWindow._trace_detection_to_object(
                self,
                detection,
                output_mode,
            )

        def _combined_trace_object(self, selected):
            return E3MainWindow._combined_trace_object(self, selected)

        def _apply_trace_group_rotation(self, objects, current_estimate) -> None:
            E3MainWindow._apply_trace_group_rotation(objects, current_estimate)

        def _clear_trace_preview(self) -> None:
            E3MainWindow._clear_trace_preview(self)

        def show_notice(self, _message: str) -> None:
            pass

        def show_error(self, message: str) -> None:
            raise AssertionError(message)

    def create(*, straighten: bool, combine: bool) -> Harness:
        harness = Harness(straighten=straighten)
        E3MainWindow._create_traced_objects(
            harness,
            {
                "selected_ids": selected_ids,
                "output_mode": "native",
                "purpose": "cut",
                "replace_previous": False,
                "combine": combine,
            },
        )
        return harness

    def world_paths(harness: Harness) -> list[np.ndarray]:
        return [
            np.asarray(polyline.points, dtype=float)
            for item in harness.document.objects
            for polyline in object_polylines(item)
        ]

    original = create(straighten=False, combine=False)
    original_paths = world_paths(original)
    assert len(original.document.objects) == 2
    assert all(
        "trace_straightened" not in item.metadata
        for item in original.document.objects
    )

    separate = create(straighten=True, combine=False)
    compound = create(straighten=True, combine=True)
    separate_paths = world_paths(separate)
    compound_paths = world_paths(compound)
    correction = trace_rotation_transform(
        estimate.correction_deg,
        estimate.pivot_mm,
    )
    expected_paths = [
        np.asarray([correction.apply(point) for point in path], dtype=float)
        for path in original_paths
    ]

    assert len(separate_paths) == len(compound_paths) == len(expected_paths) == 3
    for separate_path, compound_path, expected_path in zip(
        separate_paths,
        compound_paths,
        expected_paths,
        strict=True,
    ):
        np.testing.assert_allclose(separate_path, expected_path, atol=1e-9)
        np.testing.assert_allclose(compound_path, expected_path, atol=1e-9)

    for item, detection in zip(
        separate.document.objects,
        detections,
        strict=True,
    ):
        assert item.path_geometry() == NativePathGeometry.from_dict(
            detection["native_path"]
        )
        assert item.path_geometry().fill_rule is PathFillRule.EVENODD
        assert item.metadata["trace_straightened"] is True
        assert item.metadata["trace_correction_deg"] == pytest.approx(
            estimate.correction_deg
        )
    assert any(
        isinstance(segment, PathCubicSegment)
        for segment in separate.document.objects[0].path_geometry().subpaths[0].segments
    )
    combined = compound.document.objects[0]
    assert len(combined.path_geometry().subpaths) == 3
    assert combined.path_geometry().fill_rule is PathFillRule.EVENODD
    assert combined.metadata["trace_straightened"] is True

    stock_estimate = estimate_trace_orientation([detections[0]])
    assert stock_estimate.offered
    stock = Harness(straighten=True)
    stock._trace_orientation_estimate = stock_estimate
    stock._trace_straightening = stock_estimate
    E3MainWindow._create_traced_objects(
        stock,
        {
            "selected_ids": [selected_ids[0]],
            "output_mode": "native",
            "purpose": "stock",
            "replace_previous": False,
            "combine": False,
        },
    )
    assert len(stock.document.objects) == 1
    assert "trace_straightened" not in stock.document.objects[0].metadata
    stock_path = np.asarray(object_polylines(stock.document.objects[0])[0].points)
    np.testing.assert_allclose(stock_path, original_paths[0], atol=1e-9)

    for harness in (original, separate, compound, stock):
        harness.history.undo()
        assert harness.document.objects == []
