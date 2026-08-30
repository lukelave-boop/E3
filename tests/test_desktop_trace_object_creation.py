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


def _skewed_native_detections(angle_deg: float = 2.0) -> list[dict[str, object]]:
    geometries = [
        _world_bar((30.0, 35.0), with_hole_and_cubic=True),
        _world_bar((70.0, 35.0)),
    ]
    bounds = [native_path_bounds(geometry) for geometry in geometries]
    pivot = (
        (min(item[0] for item in bounds) + max(item[2] for item in bounds)) / 2.0,
        (min(item[1] for item in bounds) + max(item[3] for item in bounds)) / 2.0,
    )
    rotation = trace_rotation_transform(angle_deg, pivot)
    return [
        _native_detection_from_world(
            f"candidate-{index}",
            index,
            transform_native_path(geometry, rotation),
        )
        for index, geometry in enumerate(geometries, start=1)
    ]


class _CreationWorkspace:
    def __init__(self) -> None:
        self._selected_ids: list[str] = []
        self.selection_calls: list[list[str]] = []
        self.clear_count = 0

    def selected_object_ids(self) -> list[str]:
        return list(self._selected_ids)

    def select_objects(self, object_ids: list[str]) -> None:
        self._selected_ids = list(object_ids)
        self.selection_calls.append(list(object_ids))

    def clear_trace_preview(self) -> None:
        self.clear_count += 1


class _CreationTransformPanel:
    def __init__(self) -> None:
        self.review = None
        self.clear_count = 0

    def set_straighten_review(self, estimate, *, eligible: bool = True) -> None:
        assert eligible
        self.review = estimate

    def clear_straighten_review(self) -> None:
        self.review = None
        self.clear_count += 1


class _CreationHarness:
    def __init__(
        self,
        detections: list[dict[str, object]],
        *,
        detected: bool = True,
        grid: bool = False,
    ) -> None:
        self.document = ProjectDocument.new()
        self.active_layer_id = self.document.active_layer_id
        self.history = CommandStack()
        self._trace_result = {
            "detected": detected,
            "detections": detections,
            "options": {
                "regular_grid": grid,
                "output_mode": "native",
            },
            "grid": {"rows": 1, "columns": len(detections)} if grid else None,
        }
        self.controller = SimpleNamespace(cancel_trace_detection=lambda: None)
        self.workspace = _CreationWorkspace()
        self.trace_panel = SimpleNamespace(clear_result=lambda: None)
        self.transform_panel = _CreationTransformPanel()
        self.selected_panels: list[str] = []
        self.inspector_tabs = SimpleNamespace(
            select_panel=self.selected_panels.append
        )
        self.notices: list[str] = []
        self.errors: list[str] = []

    def _trace_detection_world_geometry(
        self,
        detection: dict[str, object],
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
        self,
        detections: list[dict[str, object]],
    ) -> SceneObject:
        return E3MainWindow._combined_trace_object(self, detections)

    def _clear_trace_preview(self) -> None:
        E3MainWindow._clear_trace_preview(self)

    def _trace_object_world_geometry(
        self,
        item: SceneObject,
    ) -> NativePathGeometry:
        return E3MainWindow._trace_object_world_geometry(item)

    def _selected_trace_orientation_geometry(self, objects: list[SceneObject]):
        return E3MainWindow._selected_trace_orientation_geometry(self, objects)

    def _estimate_selected_trace_orientation(self, objects: list[SceneObject]):
        return E3MainWindow._estimate_selected_trace_orientation(self, objects)

    def _update_selected_trace_orientation(self, objects: list[SceneObject]):
        return E3MainWindow._update_selected_trace_orientation(self, objects)

    def show_notice(self, message: str) -> None:
        self.notices.append(message)

    def show_error(self, message: str) -> None:
        self.errors.append(message)


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


@pytest.mark.parametrize("combine", [True, False])
def test_native_cut_creation_marks_and_selects_one_reviewable_artwork_batch(
    combine: bool,
) -> None:
    detections = _skewed_native_detections()
    harness = _CreationHarness(detections)
    detection_ids = [str(item["id"]) for item in detections]

    E3MainWindow._create_traced_objects(
        harness,
        {
            "selected_ids": detection_ids,
            "output_mode": "native",
            "purpose": "cut",
            "replace_previous": False,
            "combine": combine,
        },
    )

    objects = list(harness.document.objects)
    expected_count = 1 if combine else len(detections)
    assert len(objects) == expected_count
    assert harness.history.depth == 1
    assert harness.workspace.selection_calls == [[item.id for item in objects]]
    assert harness.selected_panels == ["transform"]
    assert harness.errors == []

    artwork_ids = {item.metadata["trace_artwork_id"] for item in objects}
    assert len(artwork_ids) == 1
    assert [
        item.metadata["trace_artwork_member_index"] for item in objects
    ] == list(range(expected_count))
    assert {
        item.metadata["trace_artwork_member_count"] for item in objects
    } == {expected_count}
    assert {
        item.metadata["trace_creation_mode"] for item in objects
    } == {"combined" if combine else "separate"}
    assert all(
        item.metadata["trace_orientation_eligible"] is True
        and item.metadata["trace_output_mode"] == "native"
        and "trace_correction_deg" not in item.metadata
        and "trace_straightened" not in item.metadata
        for item in objects
    )

    estimate = harness._update_selected_trace_orientation(objects)
    assert estimate is not None
    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.08)

    assert harness.history.undo()
    assert harness.document.objects == []
    assert harness.history.redo()
    assert [item.id for item in harness.document.objects] == [
        item.id for item in objects
    ]


def test_straighten_and_create_are_separate_history_entries() -> None:
    detections = _skewed_native_detections()
    harness = _CreationHarness(detections)
    E3MainWindow._create_traced_objects(
        harness,
        {
            "selected_ids": [str(item["id"]) for item in detections],
            "output_mode": "native",
            "purpose": "cut",
            "replace_previous": False,
            "combine": False,
        },
    )
    objects = list(harness.document.objects)
    before = {item.id: item.transform.to_dict() for item in objects}
    estimate = harness._update_selected_trace_orientation(objects)
    assert estimate is not None and estimate.offered

    E3MainWindow._straighten_selected_trace_objects(harness)

    after = {item.id: item.transform.to_dict() for item in objects}
    assert harness.history.depth == 2
    assert harness.history.undo_text == "Straighten Trace artwork"
    assert after != before

    assert harness.history.undo()
    assert {item.id: item.transform.to_dict() for item in objects} == before
    assert harness.history.undo_text == "Create 2 traced objects"

    assert harness.history.redo()
    assert {item.id: item.transform.to_dict() for item in objects} == after

    assert harness.history.undo()
    assert harness.history.undo()
    assert harness.document.objects == []


@pytest.mark.parametrize(
    ("purpose", "output_mode", "grid", "detected"),
    [
        ("cut", "exact", False, True),
        ("cut", "native", True, True),
        ("stock", "native", False, True),
        ("cut", "native", False, False),
    ],
)
def test_noneligible_creation_never_receives_orientation_provenance(
    purpose: str,
    output_mode: str,
    grid: bool,
    detected: bool,
) -> None:
    detections = _skewed_native_detections()
    if purpose == "stock":
        detections = detections[:1]
    harness = _CreationHarness(detections, detected=detected, grid=grid)

    E3MainWindow._create_traced_objects(
        harness,
        {
            "selected_ids": [str(item["id"]) for item in detections],
            "output_mode": output_mode,
            "purpose": purpose,
            "replace_previous": False,
            "combine": False,
        },
    )

    assert harness.document.objects
    assert all(
        "trace_orientation_eligible" not in item.metadata
        and "trace_output_mode" not in item.metadata
        and "trace_artwork_id" not in item.metadata
        and "trace_artwork_member_index" not in item.metadata
        and "trace_artwork_member_count" not in item.metadata
        and "trace_creation_mode" not in item.metadata
        for item in harness.document.objects
    )


def test_failed_native_fit_cannot_create_or_offer_straighten() -> None:
    detections = _skewed_native_detections()
    detections[0] = dict(detections[0])
    detections[0]["native_verified"] = False
    harness = _CreationHarness(detections)

    E3MainWindow._create_traced_objects(
        harness,
        {
            "selected_ids": [str(item["id"]) for item in detections],
            "output_mode": "native",
            "purpose": "cut",
            "replace_previous": False,
            "combine": False,
        },
    )

    assert harness.document.objects == []
    assert harness.history.depth == 0
    assert harness.workspace.selection_calls == []
    assert harness.transform_panel.review is None
    assert harness.errors == [
        "The selected native Trace geometry is not verified; run detection again"
    ]
