from __future__ import annotations

import numpy as np

from laser_aligner.config import LaserSettings
from laser_aligner.geometry.svg import Polyline
from laser_aligner.planning import PlanningCache, project_scene_revision
from laser_aligner.project import toolpath as toolpath_module
from laser_aligner.project.model import Bounds, ProjectDocument, SceneObject
from laser_aligner.project.toolpath import generate_project_gcode


def _document() -> ProjectDocument:
    document = ProjectDocument.new("Cache project", Bounds(0, 0, 100, 100))
    document.add_object(
        SceneObject.rectangle(
            document.active_layer_id,
            center=(25.0, 30.0),
            width_mm=12.0,
            height_mm=8.0,
            name="Cache rectangle",
        )
    )
    return document


def test_repeated_generation_reuses_normalized_line_geometry(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._layer_paths

    def counted_layer_paths(project, layer):
        nonlocal calls
        calls += 1
        return original(project, layer)

    monkeypatch.setattr(toolpath_module, "_layer_paths", counted_layer_paths)

    first = generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )
    second = generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )

    assert calls == 1
    assert first.bounds_mm == second.bounds_mm
    assert first.path_count == second.path_count
    assert first.point_count == second.point_count
    assert cache.stats.normalized_hits == 1
    assert cache.stats.normalized_misses == 1


def test_layer_setting_change_reuses_normalized_geometry(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._layer_paths

    def counted_layer_paths(project, layer):
        nonlocal calls
        calls += 1
        return original(project, layer)

    monkeypatch.setattr(toolpath_module, "_layer_paths", counted_layer_paths)

    generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )
    document.layers[0].speed_mm_min += 100.0
    document.touch()
    generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )

    assert calls == 1
    assert cache.stats.normalized_hits == 1


def test_geometry_change_invalidates_normalized_cache(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._layer_paths

    def counted_layer_paths(project, layer):
        nonlocal calls
        calls += 1
        return original(project, layer)

    monkeypatch.setattr(toolpath_module, "_layer_paths", counted_layer_paths)

    generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )
    document.objects[0].transform.x_mm += 0.25
    document.touch()
    generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )

    assert calls == 2
    assert cache.stats.normalized_misses == 2


def test_cache_hit_rehydrates_current_artifact_identity(monkeypatch) -> None:
    document = _document()
    layer = document.layers[0]
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._layer_paths

    def counted_layer_paths(project, operation_layer):
        nonlocal calls
        calls += 1
        return original(project, operation_layer)

    monkeypatch.setattr(toolpath_module, "_layer_paths", counted_layer_paths)

    first = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
        planning_cache=cache,
    )
    document.touch()
    second = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
        planning_cache=cache,
    )

    assert calls == 1
    assert first.metadata.artifact_id != second.metadata.artifact_id
    assert first.metadata.dependency_digest == second.metadata.dependency_digest
    assert second.metadata.scene_revision.revision == document.revision


def test_cached_geometry_is_isolated_from_callers() -> None:
    cache = PlanningCache()
    digest = "a" * 64
    original = Polyline(
        np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
        closed=False,
        source_tag="isolated",
    )
    cache.put_normalized(digest, (original,), (1.0, 2.0, 3.0, 4.0))

    first = cache.get_normalized(digest)
    assert first is not None
    first[0][0].points[0, 0] = 999.0

    second = cache.get_normalized(digest)
    assert second is not None
    assert second[0][0].points[0, 0] == 1.0


def test_normalized_cache_is_bounded_lru() -> None:
    cache = PlanningCache(max_normalized_entries=2)
    path = Polyline(
        np.asarray([[0.0, 0.0], [1.0, 0.0]], dtype=np.float64),
        source_tag="bounded",
    )

    cache.put_normalized("1" * 64, (path,), (0.0, 0.0, 1.0, 0.0))
    cache.put_normalized("2" * 64, (path,), (0.0, 0.0, 1.0, 0.0))
    assert cache.get_normalized("1" * 64) is not None
    cache.put_normalized("3" * 64, (path,), (0.0, 0.0, 1.0, 0.0))

    assert cache.stats.normalized_entries == 2
    assert cache.stats.normalized_evictions == 1
    assert cache.get_normalized("2" * 64) is None
    assert cache.get_normalized("1" * 64) is not None
    assert cache.get_normalized("3" * 64) is not None
