from __future__ import annotations

from laser_aligner.config import LaserSettings
from laser_aligner.planning import (
    project_scene_revision,
    project_source_digest,
)
from laser_aligner.project import toolpath as toolpath_module
from laser_aligner.project.model import Bounds, ProjectDocument, SceneObject, Transform
from laser_aligner.project.path_geometry import (
    NativePathGeometry,
    PathCubicSegment,
    PathSubpath,
)
from laser_aligner.project.toolpath import generate_project_gcode


def _document() -> ProjectDocument:
    document = ProjectDocument.new("Digest project", Bounds(0, 0, 100, 100))
    document.add_object(
        SceneObject.rectangle(
            document.active_layer_id,
            center=(25.0, 30.0),
            width_mm=12.0,
            height_mm=8.0,
            name="Digest rectangle",
        )
    )
    return document


def test_project_source_digest_ignores_revision_and_timestamps() -> None:
    document = _document()
    before = project_source_digest(document)

    document.touch()

    assert project_source_digest(document) == before


def test_project_source_digest_ignores_project_identity() -> None:
    first = _document()
    second = first.clone()
    second.id = "project-different-identity"
    second.created_at = "2000-01-01T00:00:00+00:00"
    second.modified_at = "2001-01-01T00:00:00+00:00"
    second.revision += 10

    assert project_source_digest(second) == project_source_digest(first)


def test_project_source_digest_is_mapping_order_independent() -> None:
    first = _document()
    second = first.clone()
    first.metadata = {"beta": 2, "alpha": 1}
    second.metadata = {"alpha": 1, "beta": 2}

    assert project_source_digest(second) == project_source_digest(first)


def test_project_source_digest_changes_with_planning_content() -> None:
    document = _document()
    before = project_source_digest(document)

    document.objects[0].transform.x_mm += 0.25

    assert project_source_digest(document) != before


def test_project_source_digest_changes_with_native_curve_controls() -> None:
    document = ProjectDocument.new("Native digest", Bounds(0, 0, 100, 100))
    document.add_object(
        SceneObject.native_path(
            document.active_layer_id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (PathCubicSegment((0.2, 0.4), (0.8, 0.4), (1.0, 0.0)),),
                    ),
                )
            ),
            transform=Transform(50.0, 50.0, 40.0, 20.0),
        )
    )
    before = project_source_digest(document)
    segment = document.objects[0].path_geometry().subpaths[0].segments[0]
    assert isinstance(segment, PathCubicSegment)
    document.objects[0].geometry = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                (PathCubicSegment((0.2, 0.45), segment.control_2, segment.to),),
            ),
        )
    ).to_dict()

    assert project_source_digest(document) != before


def test_project_scene_revision_carries_generation_and_source_digest() -> None:
    document = _document()
    scene = project_scene_revision(document)

    assert scene.project_id == document.id
    assert scene.revision == document.revision
    assert scene.coordinate_space is document.coordinate_space
    assert scene.source_digest == project_source_digest(document)
    assert scene.source_digest is not None
    assert len(scene.source_digest) == 64
    int(scene.source_digest, 16)


def test_project_generation_computes_one_scene_digest(
    monkeypatch,
) -> None:
    document = _document()
    calls = 0
    original = toolpath_module.project_scene_revision

    def counted_scene_revision(project: ProjectDocument):
        nonlocal calls
        calls += 1
        return original(project)

    monkeypatch.setattr(
        toolpath_module,
        "project_scene_revision",
        counted_scene_revision,
    )

    generate_project_gcode(document, LaserSettings(power_max=1000))

    assert calls == 1

def test_stage_dependency_digest_is_version_namespaced() -> None:
    from laser_aligner.planning import PlanningStage, stage_dependency_digest

    first = stage_dependency_digest(
        PlanningStage.NORMALIZED_GEOMETRY,
        1,
        {"value": 1},
    )
    second = stage_dependency_digest(
        PlanningStage.NORMALIZED_GEOMETRY,
        2,
        {"value": 1},
    )

    assert first != second


def test_artifact_dependency_digest_rejects_non_sha256_text() -> None:
    import pytest

    from laser_aligner.planning import ArtifactMetadata, CoordinateDomain, PlanningStage

    scene = project_scene_revision(_document())
    with pytest.raises(ValueError, match="dependency_digest"):
        ArtifactMetadata(
            artifact_id="artifact-invalid-dependency",
            scene_revision=scene,
            stage=PlanningStage.NORMALIZED_GEOMETRY,
            stage_version=1,
            coordinate_domain=CoordinateDomain.PROJECT,
            dependency_digest="not-a-digest",
        )


def test_normalized_dependency_digest_survives_revision_only_change() -> None:
    document = _document()
    layer = document.layers[0]

    first = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
    )
    document.touch()
    second = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
    )

    assert first.metadata.artifact_id != second.metadata.artifact_id
    assert first.metadata.dependency_digest == second.metadata.dependency_digest

    document.objects[0].transform.x_mm += 0.25
    third = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
    )
    assert third.metadata.dependency_digest != second.metadata.dependency_digest


def test_normalized_stage_two_identity_and_statistics_are_explicit() -> None:
    document = ProjectDocument.new("Native statistics", Bounds(0, 0, 100, 100))
    document.add_object(
        SceneObject.native_path(
            document.active_layer_id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (PathCubicSegment((0.2, 0.4), (0.8, 0.4), (1.0, 0.0)),),
                    ),
                )
            ),
            transform=Transform(50.0, 50.0, 40.0, 20.0),
        )
    )
    artifact = toolpath_module._normalized_layer_geometry(
        document,
        document.layers[0],
        project_scene_revision(document),
    )
    statistics = dict(artifact.metadata.statistics)

    assert artifact.metadata.stage_version == 2
    assert artifact.metadata.artifact_id.endswith(":v2")
    assert statistics["native_path_count"] == 1
    assert statistics["native_segment_count"] == 1
    assert statistics["flattened_path_count"] == 1
    assert statistics["flattened_point_count"] > 2
    assert statistics["flattened_point_count"] == statistics["point_count"]


def test_normalized_dependency_tracks_flatten_contract(monkeypatch) -> None:
    document = _document()
    layer = document.layers[0]
    first = toolpath_module._normalized_layer_geometry(document, layer)

    monkeypatch.setattr(
        toolpath_module,
        "NATIVE_PATH_FLATTEN_TOLERANCE_MM",
        0.0125,
    )
    tolerance_changed = toolpath_module._normalized_layer_geometry(document, layer)
    monkeypatch.setattr(
        toolpath_module,
        "NATIVE_PATH_FLATTEN_ALGORITHM_VERSION",
        toolpath_module.NATIVE_PATH_FLATTEN_ALGORITHM_VERSION + 1,
    )
    algorithm_changed = toolpath_module._normalized_layer_geometry(document, layer)

    assert (
        tolerance_changed.metadata.dependency_digest
        != first.metadata.dependency_digest
    )
    assert (
        algorithm_changed.metadata.dependency_digest
        != tolerance_changed.metadata.dependency_digest
    )


def test_operation_dependency_changes_without_forcing_placement_change() -> None:
    document = _document()
    layer = document.layers[0]
    scene = project_scene_revision(document)
    normalized_before = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        scene,
    )
    operation_before = toolpath_module._line_operation_artifact(
        document,
        layer,
        normalized_before,
    )
    placed_before = toolpath_module._placed_line_geometry_artifact(
        document,
        layer,
        operation_before,
        None,
        None,
    )

    layer.speed_mm_min += 100.0
    normalized_after = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
    )
    operation_after = toolpath_module._line_operation_artifact(
        document,
        layer,
        normalized_after,
    )
    placed_after = toolpath_module._placed_line_geometry_artifact(
        document,
        layer,
        operation_after,
        None,
        None,
    )

    assert (
        normalized_after.metadata.dependency_digest
        == normalized_before.metadata.dependency_digest
    )
    assert (
        operation_after.metadata.dependency_digest
        != operation_before.metadata.dependency_digest
    )
    assert (
        placed_after.metadata.dependency_digest
        == placed_before.metadata.dependency_digest
    )


def test_controller_dependency_changes_with_spot_offset_only() -> None:
    document = _document()
    layer = document.layers[0]
    normalized = toolpath_module._normalized_layer_geometry(
        document,
        layer,
        project_scene_revision(document),
    )
    operation = toolpath_module._line_operation_artifact(
        document,
        layer,
        normalized,
    )
    placed = toolpath_module._placed_line_geometry_artifact(
        document,
        layer,
        operation,
        None,
        None,
    )

    first = toolpath_module._controller_line_geometry_artifact(
        document,
        layer,
        placed,
        LaserSettings(
            power_max=1000,
            spot_offset_x_mm=0.0,
            spot_offset_y_mm=0.0,
        ),
    )
    second = toolpath_module._controller_line_geometry_artifact(
        document,
        layer,
        placed,
        LaserSettings(
            power_max=1000,
            spot_offset_x_mm=0.2,
            spot_offset_y_mm=0.0,
        ),
    )

    assert first.metadata.dependency_digest != second.metadata.dependency_digest
