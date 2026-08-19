from __future__ import annotations

from laser_aligner.config import LaserSettings
from laser_aligner.planning import (
    project_scene_revision,
    project_source_digest,
)
from laser_aligner.project import toolpath as toolpath_module
from laser_aligner.project.model import Bounds, ProjectDocument, SceneObject
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
