
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


def test_default_autosave_uses_shared_platform_data_root(tmp_path, monkeypatch):
    import laser_aligner.project.io as project_io

    monkeypatch.setattr(project_io, "default_user_data_dir", lambda: tmp_path / "user-data")
    monkeypatch.setattr(project_io, "legacy_user_data_dir", lambda: tmp_path / "legacy")
    document = ProjectDocument.new("Portable")

    path = autosave_path(document)

    assert path.parent == tmp_path / "user-data" / "backups"


def test_default_autosave_migrates_legacy_recovery_without_deleting_it(
    tmp_path,
    monkeypatch,
):
    import os

    import laser_aligner.project.io as project_io

    preferred_root = tmp_path / "platform-data"
    legacy_root = tmp_path / "legacy-data"
    monkeypatch.setattr(project_io, "default_user_data_dir", lambda: preferred_root)
    monkeypatch.setattr(project_io, "legacy_user_data_dir", lambda: legacy_root)
    document = ProjectDocument.new("Migrated recovery")
    filename = project_io._autosave_filename(document, None)
    legacy = legacy_root / "backups" / filename
    legacy.parent.mkdir(parents=True)
    payload = b'{"legacy":true}\n'
    legacy.write_bytes(payload)
    timestamp_ns = 1_700_000_000_123_456_789
    os.utime(legacy, ns=(timestamp_ns, timestamp_ns))

    migrated = autosave_path(document)

    assert migrated == (preferred_root / "backups" / filename).resolve()
    assert migrated.read_bytes() == payload
    assert migrated.stat().st_mtime_ns == timestamp_ns
    assert legacy.read_bytes() == payload

    project_io.clear_autosave(document)
    assert not migrated.exists()
    assert not legacy.exists()


def test_legacy_autosave_migration_never_overwrites_concurrent_native_save(
    tmp_path,
    monkeypatch,
):
    import laser_aligner.project.io as project_io

    preferred_root = tmp_path / "platform-data"
    legacy_root = tmp_path / "legacy-data"
    monkeypatch.setattr(project_io, "default_user_data_dir", lambda: preferred_root)
    monkeypatch.setattr(project_io, "legacy_user_data_dir", lambda: legacy_root)
    document = ProjectDocument.new("Migration race")
    filename = project_io._autosave_filename(document, None)
    legacy = legacy_root / "backups" / filename
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    preferred = preferred_root / "backups" / filename

    def concurrent_save(path, _data, **_kwargs):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"new native autosave")
        return False

    monkeypatch.setattr(
        project_io,
        "atomic_write_bytes_if_absent",
        concurrent_save,
    )

    selected = autosave_path(document)

    assert selected == preferred.resolve()
    assert selected.read_bytes() == b"new native autosave"
    assert legacy.read_bytes() == b"legacy"


def test_legacy_autosave_migration_cannot_backdate_concurrent_native_replace(
    tmp_path,
    monkeypatch,
):
    import os

    import laser_aligner.project.io as project_io

    preferred_root = tmp_path / "platform-data"
    legacy_root = tmp_path / "legacy-data"
    monkeypatch.setattr(project_io, "default_user_data_dir", lambda: preferred_root)
    monkeypatch.setattr(project_io, "legacy_user_data_dir", lambda: legacy_root)
    document = ProjectDocument.new("Timestamp race")
    filename = project_io._autosave_filename(document, None)
    legacy = legacy_root / "backups" / filename
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    legacy_time = 1_600_000_000_000_000_000
    native_time = 1_800_000_000_000_000_000
    os.utime(legacy, ns=(legacy_time, legacy_time))
    preferred = preferred_root / "backups" / filename
    publish = project_io.atomic_write_bytes_if_absent

    def publish_then_replace(path, data, **kwargs):
        installed = publish(path, data, **kwargs)
        replacement = path.with_suffix(".new")
        replacement.write_bytes(b"new native autosave")
        os.utime(replacement, ns=(native_time, native_time))
        os.replace(replacement, path)
        return installed

    monkeypatch.setattr(
        project_io,
        "atomic_write_bytes_if_absent",
        publish_then_replace,
    )

    selected = autosave_path(document)

    assert selected == preferred.resolve()
    assert selected.read_bytes() == b"new native autosave"
    assert selected.stat().st_mtime_ns == native_time


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
