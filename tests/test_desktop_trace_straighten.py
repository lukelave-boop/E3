from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

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
    Transform,
    load_project,
    native_path_bounds,
    save_project,
    transform_native_path,
)
from laser_aligner.vision.trace_orientation import (
    MAX_TRACE_ORIENTATION_SEGMENTS,
    TraceOrientationEstimate,
    trace_rotation_transform,
)


def _rectangle(
    center: tuple[float, float],
    width: float,
    height: float,
) -> NativePathGeometry:
    center_x, center_y = center
    half_width = width / 2.0
    half_height = height / 2.0
    return NativePathGeometry(
        (
            PathSubpath(
                (center_x - half_width, center_y - half_height),
                (
                    PathLineSegment(
                        (center_x + half_width, center_y - half_height)
                    ),
                    PathLineSegment(
                        (center_x + half_width, center_y + half_height)
                    ),
                    PathLineSegment(
                        (center_x - half_width, center_y + half_height)
                    ),
                    PathLineSegment(
                        (center_x - half_width, center_y - half_height)
                    ),
                ),
                closed=True,
            ),
        )
    )


def _decorated_component(center: tuple[float, float]) -> NativePathGeometry:
    """Return one component with a cubic outer edge and an inner hole."""

    center_x, center_y = center
    return NativePathGeometry(
        (
            PathSubpath(
                (center_x - 3.0, center_y - 4.0),
                (
                    PathLineSegment((center_x + 3.0, center_y - 4.0)),
                    PathCubicSegment(
                        (center_x + 3.1, center_y - 1.5),
                        (center_x + 2.9, center_y + 1.5),
                        (center_x + 3.0, center_y + 4.0),
                    ),
                    PathLineSegment((center_x - 3.0, center_y + 4.0)),
                    PathLineSegment((center_x - 3.0, center_y - 4.0)),
                ),
                closed=True,
            ),
            PathSubpath(
                (center_x - 1.0, center_y - 1.0),
                (
                    PathLineSegment((center_x - 1.0, center_y + 1.0)),
                    PathLineSegment((center_x + 1.0, center_y + 1.0)),
                    PathLineSegment((center_x + 1.0, center_y - 1.0)),
                    PathLineSegment((center_x - 1.0, center_y - 1.0)),
                ),
                closed=True,
            ),
        ),
        fill_rule=PathFillRule.EVENODD,
    )


def _label_geometries(
    *,
    x_offset: float,
    angle_deg: float,
) -> list[NativePathGeometry]:
    geometries = [
        _rectangle((x_offset + 0.0, 25.0), 1.5, 10.0),
        _rectangle((x_offset + 8.0, 25.0), 1.5, 9.0),
        _rectangle((x_offset + 16.0, 25.0), 1.5, 11.0),
        _rectangle((x_offset + 8.0, 18.0), 27.0, 1.0),
        _decorated_component((x_offset + 23.0, 25.0)),
    ]
    bounds = [native_path_bounds(geometry) for geometry in geometries]
    pivot = (
        (min(item[0] for item in bounds) + max(item[2] for item in bounds)) / 2.0,
        (min(item[1] for item in bounds) + max(item[3] for item in bounds)) / 2.0,
    )
    rotation = trace_rotation_transform(angle_deg, pivot)
    return [transform_native_path(geometry, rotation) for geometry in geometries]


def _scene_object_from_world_geometry(
    document: ProjectDocument,
    geometry: NativePathGeometry,
    *,
    artwork_id: str,
    member_index: int,
    member_count: int,
    creation_mode: str,
) -> SceneObject:
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
    item = SceneObject.native_path(
        document.active_layer_id,
        normalized,
        name="Camera Trace",
        center=center,
        width_mm=width,
        height_mm=height,
        source_name="camera trace",
    )
    item.metadata.update(
        {
            "trace_source": "direct",
            "trace_orientation_eligible": True,
            "trace_output_mode": "native",
            "trace_artwork_id": artwork_id,
            "trace_artwork_member_index": member_index,
            "trace_artwork_member_count": member_count,
            "trace_creation_mode": creation_mode,
        }
    )
    return item


def _add_label(
    document: ProjectDocument,
    *,
    artwork_id: str,
    x_offset: float = 20.0,
    angle_deg: float = 2.0,
    combined: bool = False,
) -> list[SceneObject]:
    geometries = _label_geometries(x_offset=x_offset, angle_deg=angle_deg)
    if combined:
        geometries = [
            NativePathGeometry(
                tuple(
                    subpath
                    for geometry in geometries
                    for subpath in geometry.subpaths
                ),
                fill_rule=PathFillRule.EVENODD,
            )
        ]
    creation_mode = "combined" if combined else "separate"
    objects = [
        _scene_object_from_world_geometry(
            document,
            geometry,
            artwork_id=artwork_id,
            member_index=index,
            member_count=len(geometries),
            creation_mode=creation_mode,
        )
        for index, geometry in enumerate(geometries)
    ]
    for item in objects:
        document.add_object(item)
    return objects


class _TransformPanel:
    def __init__(self) -> None:
        self.review: TraceOrientationEstimate | None = None
        self.review_calls: list[TraceOrientationEstimate] = []
        self.clear_count = 0
        self.selection_ids: list[str] = []

    def set_selection(
        self,
        objects: list[SceneObject],
        _document: ProjectDocument,
    ) -> None:
        self.selection_ids = [item.id for item in objects]

    def set_straighten_review(
        self,
        estimate: TraceOrientationEstimate,
        *,
        eligible: bool = True,
    ) -> None:
        assert eligible
        self.review = estimate
        self.review_calls.append(estimate)

    def clear_straighten_review(self) -> None:
        self.review = None
        self.clear_count += 1


class _Workspace:
    def __init__(self) -> None:
        self._selected_ids: list[str] = []
        self.selection_calls: list[list[str]] = []

    def selected_object_ids(self) -> list[str]:
        return list(self._selected_ids)

    def select_objects(self, object_ids: list[str]) -> None:
        self._selected_ids = list(object_ids)
        self.selection_calls.append(list(object_ids))


class _Harness:
    def __init__(self, document: ProjectDocument) -> None:
        self.document = document
        self.history = CommandStack()
        self.workspace = _Workspace()
        self.transform_panel = _TransformPanel()
        self._expanding_group_selection = False
        self.selection_label = SimpleNamespace(setText=lambda _text: None)
        self.object_panel = SimpleNamespace(set_selection=lambda _ids: None)
        self.context_bar = SimpleNamespace(
            set_selection=lambda _objects, _document: None
        )
        self.stock_layout_toolbar = SimpleNamespace(
            set_context=lambda **_kwargs: None
        )
        self.last_job = None
        self.actions = {
            "undo": SimpleNamespace(
                setEnabled=lambda _enabled: None,
                setText=lambda _text: None,
            ),
            "redo": SimpleNamespace(
                setEnabled=lambda _enabled: None,
                setText=lambda _text: None,
            ),
        }
        self.refresh_calls: list[list[str]] = []

    def _update_status_bar_layout(self) -> None:
        pass

    def _refresh_document(self, selected_ids: list[str]) -> None:
        self.refresh_calls.append(list(selected_ids))
        E3MainWindow._selection_changed(self, list(selected_ids))

    def _history_changed(self, stack: CommandStack) -> None:
        E3MainWindow._history_changed(self, stack)

    def _trace_object_world_geometry(
        self,
        item: SceneObject,
    ) -> NativePathGeometry:
        return E3MainWindow._trace_object_world_geometry(item)

    def _selected_trace_orientation_geometry(
        self,
        objects: list[SceneObject],
    ):
        return E3MainWindow._selected_trace_orientation_geometry(self, objects)

    def _estimate_selected_trace_orientation(
        self,
        objects: list[SceneObject],
    ) -> TraceOrientationEstimate | None:
        return E3MainWindow._estimate_selected_trace_orientation(self, objects)

    def _update_selected_trace_orientation(
        self,
        objects: list[SceneObject],
    ) -> TraceOrientationEstimate | None:
        return E3MainWindow._update_selected_trace_orientation(self, objects)


def _select(harness: _Harness, objects: list[SceneObject]) -> None:
    harness.workspace.select_objects([item.id for item in objects])
    E3MainWindow._selection_changed(
        harness,
        harness.workspace.selected_object_ids(),
    )


def test_obsolete_pre_create_straighten_entry_points_are_removed() -> None:
    assert not hasattr(E3MainWindow, "_straighten_trace_selection")
    assert not hasattr(E3MainWindow, "_reset_trace_straightening")
    assert not hasattr(E3MainWindow, "_update_trace_orientation")
    assert not hasattr(E3MainWindow, "_apply_trace_group_rotation")


def test_world_geometry_adapter_applies_scale_mirrors_rotation_and_translation(
) -> None:
    document = ProjectDocument.new()
    geometry = _decorated_component((0.0, 0.0))
    transform = Transform(
        x_mm=42.0,
        y_mm=51.0,
        width_mm=28.0,
        height_mm=19.0,
        rotation_deg=12.5,
        mirror_x=True,
        mirror_y=True,
    )
    item = SceneObject.native_path(
        document.active_layer_id,
        geometry,
        transform=transform,
    )
    expected = transform_native_path(
        geometry,
        PathAffineTransform.from_components(
            scale_x=-28.0,
            scale_y=-19.0,
            rotation_deg=12.5,
            translate_x=42.0,
            translate_y=51.0,
        ),
    )

    assert E3MainWindow._trace_object_world_geometry(item) == expected


def test_complexity_preflight_runs_before_any_geometry_parse_or_transform() -> None:
    document = ProjectDocument.new()
    harness = _Harness(document)
    raw_segment = {"type": "line", "to": [1.0, 1.0]}
    segment_count = MAX_TRACE_ORIENTATION_SEGMENTS // 2 + 1

    def fake_item(object_id: str, member_index: int):
        return SimpleNamespace(
            id=object_id,
            kind=ObjectKind.PATH,
            geometry={
                "path_version": 1,
                "fill_rule": "nonzero",
                "subpaths": [
                    {
                        "start": [0.0, 0.0],
                        "closed": False,
                        "segments": [raw_segment] * segment_count,
                    }
                ],
            },
            metadata={
                "trace_source": "direct",
                "trace_orientation_eligible": True,
                "trace_output_mode": "native",
                "trace_artwork_id": "trace-artwork-large",
                "trace_artwork_member_index": member_index,
                "trace_artwork_member_count": 2,
                "trace_creation_mode": "separate",
            },
        )

    transform_calls: list[str] = []

    def unexpected_transform(item) -> NativePathGeometry:
        transform_calls.append(item.id)
        raise AssertionError("over-limit geometry must not be parsed or transformed")

    harness._trace_object_world_geometry = unexpected_transform  # type: ignore[method-assign]
    adapted = E3MainWindow._selected_trace_orientation_geometry(
        harness,
        [fake_item("first", 0), fake_item("second", 1)],  # type: ignore[list-item]
    )

    assert adapted == ()
    assert transform_calls == []


@pytest.mark.parametrize("combined", [True, False])
def test_normal_project_selection_reviews_finished_trace_artwork(
    combined: bool,
) -> None:
    document = ProjectDocument.new()
    objects = _add_label(
        document,
        artwork_id="trace-artwork-one",
        angle_deg=2.0,
        combined=combined,
    )
    harness = _Harness(document)

    _select(harness, objects)

    estimate = harness.transform_panel.review
    assert harness.transform_panel.selection_ids == [item.id for item in objects]
    assert estimate is not None
    assert estimate.selected_ids == tuple(item.id for item in objects)
    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.08)


def test_selection_change_clears_review_for_non_trace_or_mixed_selection() -> None:
    document = ProjectDocument.new()
    traced = _add_label(
        document,
        artwork_id="trace-artwork-one",
        combined=True,
    )
    manual = SceneObject.rectangle(
        document.active_layer_id,
        name="Manual rectangle",
        center=(100.0, 80.0),
        width_mm=20.0,
        height_mm=10.0,
    )
    document.add_object(manual)
    harness = _Harness(document)

    _select(harness, traced)
    assert harness.transform_panel.review is not None

    _select(harness, [manual])
    assert harness.transform_panel.review is None

    _select(harness, [*traced, manual])
    assert harness.transform_panel.review is None
    assert harness.transform_panel.clear_count >= 2


def test_partial_separate_artwork_selection_is_not_treated_as_complete() -> None:
    document = ProjectDocument.new()
    traced = _add_label(
        document,
        artwork_id="trace-artwork-one",
        combined=False,
    )
    harness = _Harness(document)

    _select(harness, traced)
    assert harness.transform_panel.review is not None

    _select(harness, traced[:-1])
    assert harness.transform_panel.review is None


def test_manual_project_rotation_recomputes_current_world_orientation() -> None:
    document = ProjectDocument.new()
    objects = _add_label(
        document,
        artwork_id="trace-artwork-one",
        angle_deg=2.0,
        combined=True,
    )
    harness = _Harness(document)
    _select(harness, objects)
    first = harness.transform_panel.review
    assert first is not None and first.offered

    item = objects[0]
    document.update_transform(
        item.id,
        item.transform.copy(
            x_mm=item.transform.x_mm + 11.0,
            y_mm=item.transform.y_mm - 7.0,
            rotation_deg=item.transform.rotation_deg + 3.0,
        ),
    )
    _select(harness, objects)
    current = harness.transform_panel.review

    assert current is not None
    assert current.offered
    assert current.detected_skew_deg == pytest.approx(5.0, abs=0.08)
    assert len(harness.transform_panel.review_calls) == 2


def test_two_complete_trace_artworks_keep_distinct_conflict_identity() -> None:
    document = ProjectDocument.new()
    first = _add_label(
        document,
        artwork_id="trace-artwork-first",
        x_offset=20.0,
        angle_deg=3.0,
    )
    second = _add_label(
        document,
        artwork_id="trace-artwork-second",
        x_offset=100.0,
        angle_deg=-5.0,
    )
    harness = _Harness(document)

    _select(harness, [*first, *second])

    estimate = harness.transform_panel.review
    assert estimate is not None
    assert not estimate.offered
    assert estimate.suppression_reason == "conflicting_candidate_orientations"


def test_straighten_is_one_rigid_group_history_edit_with_exact_undo_redo() -> None:
    document = ProjectDocument.new()
    objects = _add_label(
        document,
        artwork_id="trace-artwork-one",
        angle_deg=2.0,
    )
    harness = _Harness(document)
    _select(harness, objects)
    estimate = harness.transform_panel.review
    assert estimate is not None and estimate.offered
    assert estimate.correction_deg is not None
    assert estimate.pivot_mm is not None

    world_bounds = [
        native_path_bounds(E3MainWindow._trace_object_world_geometry(item))
        for item in objects
    ]
    expected_pivot = (
        (
            min(bounds[0] for bounds in world_bounds)
            + max(bounds[2] for bounds in world_bounds)
        )
        / 2.0,
        (
            min(bounds[1] for bounds in world_bounds)
            + max(bounds[3] for bounds in world_bounds)
        )
        / 2.0,
    )
    assert estimate.pivot_mm == pytest.approx(expected_pivot, abs=1e-12)

    before_transforms = {
        item.id: item.transform.to_dict() for item in objects
    }
    before_geometry = {item.id: copy.deepcopy(item.geometry) for item in objects}
    before_metadata = {item.id: copy.deepcopy(item.metadata) for item in objects}
    correction = trace_rotation_transform(
        estimate.correction_deg,
        estimate.pivot_mm,
    )
    harness.history.add_listener(harness._history_changed)

    E3MainWindow._straighten_selected_trace_objects(harness)

    assert harness.history.depth == 1
    assert harness.history.undo_text == "Straighten Trace artwork"
    assert harness.workspace.selection_calls[-1] == [item.id for item in objects]
    assert harness.refresh_calls == [[item.id for item in objects]]
    straightened_review = harness.transform_panel.review
    assert straightened_review is not None
    assert not straightened_review.offered
    assert abs(straightened_review.detected_skew_deg) < 0.08
    for item in objects:
        before = Transform.from_dict(before_transforms[item.id])
        expected_center = correction.apply((before.x_mm, before.y_mm))
        assert (item.transform.x_mm, item.transform.y_mm) == pytest.approx(
            expected_center,
            abs=1e-12,
        )
        assert item.transform.rotation_deg == pytest.approx(
            before.rotation_deg + estimate.correction_deg,
            abs=1e-12,
        )
        assert item.transform.width_mm == before.width_mm
        assert item.transform.height_mm == before.height_mm
        assert item.geometry == before_geometry[item.id]
        assert item.metadata == before_metadata[item.id]
        assert "trace_correction_deg" not in item.metadata

    after_transforms = {
        item.id: item.transform.to_dict() for item in objects
    }
    after_estimate = harness._estimate_selected_trace_orientation(objects)
    assert after_estimate is not None
    assert not after_estimate.offered
    assert abs(after_estimate.detected_skew_deg) < 0.08

    decorated = objects[-1].path_geometry()
    assert decorated.fill_rule is PathFillRule.EVENODD
    assert len(decorated.subpaths) == 2
    assert any(
        isinstance(segment, PathCubicSegment)
        for subpath in decorated.subpaths
        for segment in subpath.segments
    )
    assert all(item.kind is ObjectKind.PATH for item in objects)

    assert harness.history.undo()
    assert {
        item.id: item.transform.to_dict() for item in objects
    } == before_transforms
    assert harness.history.redo_text == "Straighten Trace artwork"
    undo_review = harness.transform_panel.review
    assert undo_review is not None
    assert undo_review.offered
    assert undo_review.detected_skew_deg == pytest.approx(2.0, abs=0.08)

    assert harness.history.redo()
    assert {
        item.id: item.transform.to_dict() for item in objects
    } == after_transforms
    redo_review = harness.transform_panel.review
    assert redo_review is not None
    assert not redo_review.offered
    assert abs(redo_review.detected_skew_deg) < 0.08


def test_saved_trace_provenance_remains_reviewable_after_load(
    tmp_path: Path,
) -> None:
    document = ProjectDocument.new()
    original = _add_label(
        document,
        artwork_id="trace-artwork-persistent",
        angle_deg=2.0,
    )
    path = save_project(document, tmp_path / "trace-artwork.e3laser")

    loaded = load_project(path)
    loaded_objects = list(loaded.objects)
    harness = _Harness(loaded)
    _select(harness, loaded_objects)

    assert [item.metadata for item in loaded_objects] == [
        item.metadata for item in original
    ]
    estimate = harness.transform_panel.review
    assert estimate is not None
    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.08)
