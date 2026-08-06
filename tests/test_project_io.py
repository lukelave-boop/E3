import json

from laser_aligner.project import (
    ProjectDocument,
    SceneObject,
    autosave_path,
    load_project,
    save_project,
)


def test_atomic_save_and_load(tmp_path):
    document = ProjectDocument.new("Fixture")
    document.add_object(SceneObject.rectangle(document.active_layer_id))

    path = save_project(document, tmp_path / "fixture")
    restored = load_project(path)

    assert path.suffix == ".e3laser"
    assert restored.to_dict() == document.to_dict()


def test_save_creates_backup(tmp_path):
    document = ProjectDocument.new("Backup")
    path = save_project(document, tmp_path / "backup.e3laser")
    original = path.read_text()

    document.name = "Changed"
    save_project(document, path)

    backup = path.with_suffix(".e3laser.bak")
    assert backup.exists()
    assert backup.read_text() == original


def test_autosave_path_is_stable_and_outside_project(tmp_path):
    document = ProjectDocument.new("My project")

    first = autosave_path(document, autosave_root=tmp_path)
    second = autosave_path(document, autosave_root=tmp_path)

    assert first == second
    assert first.parent == tmp_path
    assert first.name.endswith(".autosave.e3laser")


def test_autosave_newer_detection_and_clear(tmp_path):
    import os
    import time

    from laser_aligner.project import (
        autosave_is_newer,
        clear_autosave,
        save_autosave,
    )

    document = ProjectDocument.new("Recover")
    project = save_project(document, tmp_path / "recover.e3laser")
    old = time.time() - 10
    os.utime(project, (old, old))
    autosave = save_autosave(
        document,
        project_path=project,
        autosave_root=tmp_path / "autosaves",
    )

    assert autosave_is_newer(
        document,
        project,
        autosave_root=tmp_path / "autosaves",
    )

    clear_autosave(
        document,
        project_path=project,
        autosave_root=tmp_path / "autosaves",
    )
    assert not autosave.exists()
