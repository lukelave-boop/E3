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

def test_planning_cache_supports_concurrent_worker_access() -> None:
    from concurrent.futures import ThreadPoolExecutor

    cache = PlanningCache(max_normalized_entries=16)
    path = Polyline(
        np.asarray([[0.0, 0.0], [1.0, 0.0]], dtype=np.float64),
        source_tag="threaded",
    )

    def exercise(worker: int) -> None:
        for index in range(100):
            digest = f"{(worker * 100 + index) % 32:064x}"
            cache.put_normalized(digest, (path,), (0.0, 0.0, 1.0, 0.0))
            cache.get_normalized(digest)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(exercise, range(4)))

    assert cache.stats.normalized_entries <= 16


def test_desktop_job_generation_owns_and_passes_session_planning_cache() -> None:
    import ast
    from pathlib import Path

    source_path = (
        Path(__file__).resolve().parents[1]
        / "laser_aligner"
        / "desktop"
        / "main_window.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    main_window = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "E3MainWindow"
    )
    methods = {
        node.name: node
        for node in main_window.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    init = methods["__init__"]
    owns_cache = any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "_planning_cache"
            for target in node.targets
        )
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "PlanningCache"
        for node in ast.walk(init)
    )
    assert owns_cache

    begin = methods["_begin_job_generation"]
    context_captures_cache = any(
        isinstance(node, ast.Dict)
        and any(
            isinstance(key, ast.Constant)
            and key.value == "planning_cache"
            and isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "self"
            and value.attr == "_planning_cache"
            for key, value in zip(node.keys, node.values, strict=True)
        )
        for node in ast.walk(begin)
    )
    assert context_captures_cache

    ready = methods["_job_snapshot_ready"]
    passes_cache = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "generate_project_gcode"
        and any(
            keyword.arg == "planning_cache"
            and isinstance(keyword.value, ast.Subscript)
            and isinstance(keyword.value.value, ast.Name)
            and keyword.value.value.id == "context"
            for keyword in node.keywords
        )
        for node in ast.walk(ready)
    )
    assert passes_cache

def test_repeated_generation_reuses_placed_line_geometry(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._place_paths

    def counted_place_paths(paths, coordinate_frame):
        nonlocal calls
        calls += 1
        return original(paths, coordinate_frame)

    monkeypatch.setattr(toolpath_module, "_place_paths", counted_place_paths)

    generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )
    generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )

    assert calls == 1
    assert cache.stats.placed_hits == 1
    assert cache.stats.placed_misses == 1


def test_layer_setting_change_reuses_placed_geometry(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._place_paths

    def counted_place_paths(paths, coordinate_frame):
        nonlocal calls
        calls += 1
        return original(paths, coordinate_frame)

    monkeypatch.setattr(toolpath_module, "_place_paths", counted_place_paths)

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
    assert cache.stats.placed_hits == 1


def test_geometry_change_invalidates_placed_cache(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._place_paths

    def counted_place_paths(paths, coordinate_frame):
        nonlocal calls
        calls += 1
        return original(paths, coordinate_frame)

    monkeypatch.setattr(toolpath_module, "_place_paths", counted_place_paths)

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
    assert cache.stats.placed_misses == 2


def test_placed_cache_hit_rehydrates_current_artifact_identity(monkeypatch) -> None:
    document = _document()
    layer = document.layers[0]
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._place_paths

    def counted_place_paths(paths, coordinate_frame):
        nonlocal calls
        calls += 1
        return original(paths, coordinate_frame)

    monkeypatch.setattr(toolpath_module, "_place_paths", counted_place_paths)

    normalized_first = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
        planning_cache=cache,
    )
    operation_first = toolpath_module._line_operation_artifact(
        document,
        layer,
        normalized_first,
    )
    first = toolpath_module._placed_line_geometry_artifact(
        document,
        layer,
        operation_first,
        None,
        None,
        planning_cache=cache,
    )

    document.touch()
    normalized_second = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
        planning_cache=cache,
    )
    operation_second = toolpath_module._line_operation_artifact(
        document,
        layer,
        normalized_second,
    )
    second = toolpath_module._placed_line_geometry_artifact(
        document,
        layer,
        operation_second,
        None,
        None,
        planning_cache=cache,
    )

    assert calls == 1
    assert first.metadata.artifact_id != second.metadata.artifact_id
    assert first.metadata.dependency_digest == second.metadata.dependency_digest
    assert second.metadata.scene_revision.revision == document.revision


def test_placed_cache_is_bounded_lru() -> None:
    cache = PlanningCache(max_placed_entries=2)
    path = Polyline(
        np.asarray([[0.0, 0.0], [1.0, 0.0]], dtype=np.float64),
        source_tag="placed-bounded",
    )

    cache.put_placed("4" * 64, (path,), (0.0, 0.0, 1.0, 0.0))
    cache.put_placed("5" * 64, (path,), (0.0, 0.0, 1.0, 0.0))
    assert cache.get_placed("4" * 64) is not None
    cache.put_placed("6" * 64, (path,), (0.0, 0.0, 1.0, 0.0))

    assert cache.stats.placed_entries == 2
    assert cache.stats.placed_evictions == 1
    assert cache.get_placed("5" * 64) is None
    assert cache.get_placed("4" * 64) is not None
    assert cache.get_placed("6" * 64) is not None

def test_repeated_generation_reuses_controller_line_geometry(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._controller_paths

    def counted_controller_paths(paths, laser):
        nonlocal calls
        calls += 1
        return original(paths, laser)

    monkeypatch.setattr(
        toolpath_module,
        "_controller_paths",
        counted_controller_paths,
    )

    generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )
    generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        planning_cache=cache,
    )

    assert calls == 1
    assert cache.stats.controller_hits == 1
    assert cache.stats.controller_misses == 1


def test_layer_setting_change_reuses_controller_geometry(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._controller_paths

    def counted_controller_paths(paths, laser):
        nonlocal calls
        calls += 1
        return original(paths, laser)

    monkeypatch.setattr(
        toolpath_module,
        "_controller_paths",
        counted_controller_paths,
    )

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
    assert cache.stats.placed_hits == 1
    assert cache.stats.controller_hits == 1


def test_spot_offset_change_invalidates_only_controller_cache(monkeypatch) -> None:
    document = _document()
    cache = PlanningCache()
    calls = 0
    original = toolpath_module._controller_paths

    def counted_controller_paths(paths, laser):
        nonlocal calls
        calls += 1
        return original(paths, laser)

    monkeypatch.setattr(
        toolpath_module,
        "_controller_paths",
        counted_controller_paths,
    )

    generate_project_gcode(
        document,
        LaserSettings(
            power_max=1000,
            spot_offset_x_mm=0.0,
            spot_offset_y_mm=0.0,
        ),
        planning_cache=cache,
    )
    generate_project_gcode(
        document,
        LaserSettings(
            power_max=1000,
            spot_offset_x_mm=0.2,
            spot_offset_y_mm=0.0,
        ),
        planning_cache=cache,
    )

    assert calls == 2
    assert cache.stats.normalized_hits == 1
    assert cache.stats.placed_hits == 1
    assert cache.stats.controller_misses == 2


def test_controller_cache_hit_rehydrates_current_artifact_identity(monkeypatch) -> None:
    document = _document()
    layer = document.layers[0]
    cache = PlanningCache()
    laser = LaserSettings(power_max=1000)
    calls = 0
    original = toolpath_module._controller_paths

    def counted_controller_paths(paths, settings):
        nonlocal calls
        calls += 1
        return original(paths, settings)

    monkeypatch.setattr(
        toolpath_module,
        "_controller_paths",
        counted_controller_paths,
    )

    normalized_first = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
        planning_cache=cache,
    )
    operation_first = toolpath_module._line_operation_artifact(
        document,
        layer,
        normalized_first,
    )
    placed_first = toolpath_module._placed_line_geometry_artifact(
        document,
        layer,
        operation_first,
        None,
        None,
        planning_cache=cache,
    )
    first = toolpath_module._controller_line_geometry_artifact(
        document,
        layer,
        placed_first,
        laser,
        planning_cache=cache,
    )

    document.touch()
    normalized_second = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
        planning_cache=cache,
    )
    operation_second = toolpath_module._line_operation_artifact(
        document,
        layer,
        normalized_second,
    )
    placed_second = toolpath_module._placed_line_geometry_artifact(
        document,
        layer,
        operation_second,
        None,
        None,
        planning_cache=cache,
    )
    second = toolpath_module._controller_line_geometry_artifact(
        document,
        layer,
        placed_second,
        laser,
        planning_cache=cache,
    )

    assert calls == 1
    assert first.metadata.artifact_id != second.metadata.artifact_id
    assert first.metadata.dependency_digest == second.metadata.dependency_digest
    assert second.metadata.scene_revision.revision == document.revision


def test_controller_cache_is_bounded_lru() -> None:
    cache = PlanningCache(max_controller_entries=2)
    path = Polyline(
        np.asarray([[0.0, 0.0], [1.0, 0.0]], dtype=np.float64),
        source_tag="controller-bounded",
    )

    cache.put_controller("7" * 64, (path,), (0.0, 0.0, 1.0, 0.0))
    cache.put_controller("8" * 64, (path,), (0.0, 0.0, 1.0, 0.0))
    assert cache.get_controller("7" * 64) is not None
    cache.put_controller("9" * 64, (path,), (0.0, 0.0, 1.0, 0.0))

    assert cache.stats.controller_entries == 2
    assert cache.stats.controller_evictions == 1
    assert cache.get_controller("8" * 64) is None
    assert cache.get_controller("7" * 64) is not None
    assert cache.get_controller("9" * 64) is not None
