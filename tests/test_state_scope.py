from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from laser_aligner import state_scope, storage


def seed_state(root: Path, *, data_dir: Path | str = "../data") -> Path:
    config = root / "config" / "network-local.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "app": {"data_dir": str(data_dir)},
        "machine": {"unknown_future_safety_limit": 12},
    }), encoding="utf-8")
    data = Path(data_dir)
    if not data.is_absolute():
        data = (config.parent / data).resolve()
    (data / "calibration_profiles" / "optics").mkdir(parents=True)
    (data / "machines.json").write_bytes(b'{"unchanged": "registry"}\n')
    (data / "calibration_profiles" / "optics" / "bed.json").write_bytes(
        b'{"model_id":"exact-calibration-identity"}\n'
    )
    return config


def test_scope_copies_once_without_changing_calibration_or_safety_payload(tmp_path: Path) -> None:
    source = tmp_path / "old"
    config = seed_state(source)
    (source / "secrets").mkdir()
    (source / "secrets" / "bridge-token.txt").write_bytes(b"private-token")
    (source / "config" / "network-local.focus-calibration.json").write_bytes(b'{"offset":3}')
    original = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    target = tmp_path / "new"

    new_config = state_scope.prepare_state_scope(target, source)

    payload = json.loads(new_config.read_text())
    assert Path(payload["app"]["data_dir"]) == target / "data"
    assert payload["machine"] == {"unknown_future_safety_limit": 12}
    for relative, content in original.items():
        assert (source / relative).read_bytes() == content
        if relative != config.relative_to(source):
            assert (target / relative).read_bytes() == content
    new_config.write_text('{"edited":true}', encoding="utf-8")
    config.write_text('{"new-source":true}', encoding="utf-8")
    assert state_scope.prepare_state_scope(target, source) == new_config
    assert new_config.read_text() == '{"edited":true}'


def test_external_configured_data_is_copied_and_rebased(tmp_path: Path) -> None:
    source = tmp_path / "old"
    external = tmp_path / "camera-data"
    seed_state(source, data_dir=external)
    result = state_scope.prepare_state_scope(tmp_path / "new", source)
    data = Path(json.loads(result.read_text())["app"]["data_dir"])
    assert data.is_relative_to(tmp_path / "new")
    assert data != external
    assert (data / "machines.json").read_bytes() == (external / "machines.json").read_bytes()


def test_existing_scope_cannot_redirect_writes_back_into_legacy(tmp_path: Path) -> None:
    source = tmp_path / "old"
    seed_state(source)
    target = tmp_path / "new"
    result = state_scope.prepare_state_scope(target, source)
    raw = json.loads(result.read_text())
    raw["app"]["data_dir"] = str(source / "data")
    result.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(state_scope.StateScopeError, match="escapes"):
        state_scope.prepare_state_scope(target, source)


def test_manifest_canonicalizes_root_before_checking_resolved_children(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "old"
    seed_state(source)
    physical = tmp_path / "physical"
    state_scope.prepare_state_scope(physical, source)
    logical = tmp_path / "virtualized-spelling"
    checked = state_scope._checked_path
    monkeypatch.setattr(
        state_scope, "_checked_path", lambda path: physical if path == logical else checked(path),
    )
    assert state_scope._read_manifest(logical)["config"] == str(Path("config") / "network-local.json")


def test_sqlite_snapshot_includes_committed_wal_data(tmp_path: Path) -> None:
    source = tmp_path / "old"
    seed_state(source)
    with sqlite3.connect(source / "materials.sqlite") as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE recipes (name TEXT)")
        connection.execute("INSERT INTO recipes VALUES ('Keep this recipe')")
        connection.commit()
        state_scope.prepare_state_scope(tmp_path / "new", source)
    with sqlite3.connect(tmp_path / "new" / "materials.sqlite") as copied:
        assert copied.execute("SELECT name FROM recipes").fetchall() == [("Keep this recipe",)]


def test_incomplete_scope_never_falls_back_or_overwrites(tmp_path: Path) -> None:
    source = tmp_path / "old"
    seed_state(source)
    target = tmp_path / "new"
    target.mkdir()
    retained = target / "operator-file"
    retained.write_bytes(b"keep")
    with pytest.raises(state_scope.StateScopeError, match="incomplete"):
        state_scope.prepare_state_scope(target, source)
    assert retained.read_bytes() == b"keep"


def test_failed_or_changed_import_is_not_published(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "old"
    seed_state(source)
    original = state_scope._copy_tree

    def changing_copy(old, new, *, root=False):
        original(old, new, root=root)
        if root:
            (source / "data" / "machines.json").write_bytes(b"changed")

    monkeypatch.setattr(state_scope, "_copy_tree", changing_copy)
    with pytest.raises(state_scope.StateScopeError, match="changed during"):
        state_scope.prepare_state_scope(tmp_path / "new", source)
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".state-import-*"))


def test_scope_rejects_linked_source_before_resolving(tmp_path: Path) -> None:
    source = tmp_path / "old"
    seed_state(source)
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a Windows symlink requires developer mode or permission")
    with pytest.raises(state_scope.StateScopeError, match="linked path"):
        state_scope.prepare_state_scope(tmp_path / "new", linked)


def test_default_storage_respects_scoped_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path / "isolated"))
    assert storage.default_user_data_dir() == tmp_path / "isolated"


def test_scope_does_not_recursively_import_older_scopes(tmp_path: Path) -> None:
    source = tmp_path / "old"
    seed_state(source)
    older = source / "state-scopes" / "old-version"
    older.mkdir(parents=True)
    (older / "private").write_text("other scope", encoding="utf-8")
    target = source / "state-scopes" / "new-version"
    state_scope.prepare_state_scope(target, source)
    assert not (target / "state-scopes").exists()


def test_missing_user_config_imports_packaged_default_and_sidecars(tmp_path: Path) -> None:
    packaged = tmp_path / "bundle"
    config = seed_state(packaged)
    default = config.with_name("default.json")
    config.rename(default)
    result = state_scope.prepare_state_scope(
        tmp_path / "new", tmp_path / "absent-legacy", config_path=default,
    )
    assert result.name == "default.json"
    assert result.exists()
    data = Path(json.loads(result.read_text())["app"]["data_dir"])
    assert data.is_relative_to(tmp_path / "new")
    assert (data / "machines.json").exists()


def test_isolated_autosave_clear_and_material_lookup_never_touch_legacy(tmp_path: Path, monkeypatch) -> None:
    from laser_aligner.materials import database
    from laser_aligner.project import ProjectDocument, io

    legacy = tmp_path / "legacy"
    private = tmp_path / "private"
    monkeypatch.setenv("E3_STATE_ISOLATED", "1")
    monkeypatch.setenv("E3_USER_STATE_DIR", str(private))
    monkeypatch.setattr(io, "legacy_user_data_dir", lambda: legacy)
    monkeypatch.setattr(database, "legacy_user_data_dir", lambda: legacy)
    document = ProjectDocument()
    filename = io._autosave_filename(document, None)
    (legacy / "backups").mkdir(parents=True)
    retained = legacy / "backups" / filename
    retained.write_bytes(b"old recovery")
    (legacy / "materials.sqlite").write_bytes(b"old database")

    assert io._default_autosave_path(document, None) == private / "backups" / filename
    io.clear_autosave(document)
    assert retained.read_bytes() == b"old recovery"
    assert database._default_database_path() == private / "materials.sqlite"
    assert (legacy / "materials.sqlite").read_bytes() == b"old database"


def test_installer_restores_original_environment_and_removes_scope(tmp_path: Path, monkeypatch) -> None:
    from laser_aligner.updates import _windows_installer_environment

    original = {
        "LOCALAPPDATA": str(tmp_path / "original"), "APPDATA": None,
        "USERPROFILE": str(tmp_path / "home"), "HOME": None, "XDG_DATA_HOME": None,
    }
    for key in original:
        monkeypatch.setenv(key, "redirected")
    monkeypatch.setenv("E3_STATE_ORIGINAL_ENV", json.dumps(original))
    monkeypatch.setenv("E3_USER_STATE_DIR", "private")
    monkeypatch.setenv("E3_CONFIG_PATH", "private-config")
    monkeypatch.setenv("E3_STATE_ISOLATED", "1")
    result = _windows_installer_environment()
    for key, value in original.items():
        assert result.get(key) == value
    assert "E3_USER_STATE_DIR" not in result
    assert "E3_CONFIG_PATH" not in result
    assert not any(key.startswith("E3_STATE_") for key in result)


def test_successful_update_imports_latest_state_and_old_version_reopens_its_own(tmp_path: Path, monkeypatch) -> None:
    from laser_aligner import deployment

    seed = tmp_path / "legacy"
    seed_state(seed)
    monkeypatch.setattr(state_scope, "platform_user_data_dir", lambda: seed)
    monkeypatch.setattr(state_scope, "legacy_user_data_dir", lambda: tmp_path / "absent-legacy")
    monkeypatch.setattr(deployment, "application_root", lambda: tmp_path / "bundle")
    monkeypatch.setattr(state_scope.sys, "frozen", True, raising=False)
    monkeypatch.setattr(state_scope, "_runtime_publication", None)
    for key in ("E3_USER_STATE_DIR", "E3_CONFIG_PATH", "E3_STATE_ISOLATED", "E3_DEV_TEST"):
        monkeypatch.setenv(key, "")

    def launch(version, revision, *, successful=True):
        for key in ("E3_USER_STATE_DIR", "E3_CONFIG_PATH", "E3_STATE_ISOLATED"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(deployment, "load_build_info", lambda: SimpleNamespace(version=version, revision=revision))
        state_scope.prepare_runtime_state()
        result = storage.default_user_data_dir()
        if successful:
            state_scope.record_runtime_startup()
        return result

    first = launch("0.8.1", "a" * 40)
    edited = first / "data" / "calibration_profiles" / "optics" / "bed.json"
    edited.write_bytes(b"operator's newer calibration")
    pointer = first.parent / "last-successful.json"
    original_pointer = pointer.read_bytes()
    failed = launch("0.8.2", "b" * 40, successful=False)
    assert (failed / "data" / "calibration_profiles" / "optics" / "bed.json").read_bytes() == edited.read_bytes()
    assert pointer.read_bytes() == original_pointer
    next_release = launch("0.8.3", "c" * 40)
    assert (next_release / "data" / "calibration_profiles" / "optics" / "bed.json").read_bytes() == edited.read_bytes()
    current_pointer = pointer.read_bytes()
    assert launch("0.8.1", "a" * 40) == first
    assert edited.read_bytes() == b"operator's newer calibration"
    assert pointer.read_bytes() == current_pointer
    pointer.write_bytes(b"damaged global pointer")
    assert launch("0.8.1", "a" * 40) == first


def test_explicit_desktop_config_retains_machine_config_but_isolates_auxiliary_state(tmp_path: Path, monkeypatch) -> None:
    from laser_aligner import deployment

    seed = tmp_path / "legacy"
    explicit = seed_state(seed)
    original = explicit.read_bytes()
    monkeypatch.setattr(state_scope, "platform_user_data_dir", lambda: seed)
    monkeypatch.setattr(state_scope, "legacy_user_data_dir", lambda: tmp_path / "absent-legacy")
    monkeypatch.setattr(deployment, "application_root", lambda: tmp_path / "source")
    for key in ("E3_USER_STATE_DIR", "E3_CONFIG_PATH", "E3_STATE_ISOLATED"):
        monkeypatch.setenv(key, "")
    state_scope.prepare_runtime_state(explicit)
    assert storage.state_is_isolated()
    assert storage.default_user_data_dir().is_relative_to(seed / "state-scopes" / "explicit")
    assert explicit.read_bytes() == original
    assert not state_scope.os.environ.get("E3_CONFIG_PATH")


def test_success_pointer_lock_contention_does_not_overwrite_another_startup(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "old"
    seed_state(source)
    target = tmp_path / "installed" / "new"
    state_scope.prepare_state_scope(target, source)
    pointer = target.parent / "last-successful.json"
    monkeypatch.setattr(state_scope, "_runtime_publication", (pointer, target, "0.8.2", "b" * 40))
    with state_scope._publication_lock(pointer):
        state_scope.record_runtime_startup()
    assert not pointer.exists()


def test_new_test_build_inherits_latest_prepared_settings_but_old_build_stays_independent(tmp_path: Path) -> None:
    baseline = tmp_path / "production"
    seed_state(baseline)
    channel = tmp_path / "launcher" / "state"

    def prepare(version, revision):
        root = channel / f"{version}-{revision}" / "local" / "E3 Positioning System"
        config = state_scope.prepare_test_state(root, baseline, version=version, revision=revision)
        return root, config

    first, _ = prepare("0.7.292", "a" * 40)
    registry = first / "data" / "machines.json"
    registry.write_bytes(b'{"raster_travel_envelope":{"x_min":-20},"operator_edit":42}\n')
    bed = first / "data" / "calibration_profiles" / "optics" / "bed.json"
    bed.write_bytes(b'{"model_id":"newer-test-calibration"}\n')
    second, second_config = prepare("0.7.300", "b" * 40)
    assert (second / "data" / "machines.json").read_bytes() == registry.read_bytes()
    assert (second / "data" / "calibration_profiles" / "optics" / "bed.json").read_bytes() == bed.read_bytes()
    assert Path(json.loads(second_config.read_text())["app"]["data_dir"]) == second / "data"
    assert (baseline / "data" / "machines.json").read_bytes() == b'{"unchanged": "registry"}\n'

    pointer = channel / "last-prepared-test.json"
    latest_pointer = pointer.read_bytes()
    (second / "data" / "machines.json").write_bytes(b'{"newer_format":true}\n')
    reopened, _ = prepare("0.7.292", "a" * 40)
    assert reopened == first
    assert registry.read_bytes() == b'{"raster_travel_envelope":{"x_min":-20},"operator_edit":42}\n'
    assert pointer.read_bytes() == latest_pointer
    older, _ = prepare("0.7.150", "c" * 40)
    equal_other_revision, _ = prepare("0.7.300", "d" * 40)
    for conservative in (older, equal_other_revision):
        assert (conservative / "data" / "machines.json").read_bytes() == (baseline / "data" / "machines.json").read_bytes()
    assert pointer.read_bytes() == latest_pointer


def test_existing_prepared_scope_can_become_initial_test_source(tmp_path: Path) -> None:
    baseline = tmp_path / "production"
    seed_state(baseline)
    version, revision = "0.7.292", "a" * 40
    root = tmp_path / "state" / f"{version}-{revision}" / "local" / "E3 Positioning System"
    state_scope.prepare_state_scope(root, baseline)
    (root / "data" / "machines.json").write_bytes(b'{"retained_modern_setting":true}\n')
    state_scope.prepare_test_state(root, baseline, version=version, revision=revision)
    pointer = root.parents[2] / "last-prepared-test.json"
    assert state_scope._read_test_pointer(pointer) == (root, version, revision)
    assert (root / "data" / "machines.json").read_bytes() == b'{"retained_modern_setting":true}\n'


def test_test_pointer_failure_or_contention_never_blocks_an_existing_scope(tmp_path: Path) -> None:
    baseline = tmp_path / "production"
    seed_state(baseline)
    channel = tmp_path / "state"
    root = channel / f"0.7.292-{'a' * 40}" / "local" / "E3 Positioning System"
    config = state_scope.prepare_test_state(root, baseline, version="0.7.292", revision="a" * 40)
    pointer = channel / "last-prepared-test.json"
    before = pointer.read_bytes()
    new_root = channel / f"0.7.300-{'b' * 40}" / "local" / "E3 Positioning System"
    with state_scope._publication_lock(pointer):
        assert state_scope.prepare_test_state(root, baseline, version="0.7.292", revision="a" * 40) == config
        with pytest.raises(state_scope.StateScopeError, match="preparation may be in progress"):
            state_scope.prepare_test_state(new_root, baseline, version="0.7.300", revision="b" * 40)
    assert pointer.read_bytes() == before
    assert not new_root.exists()
    pointer.write_bytes(b"damaged channel pointer")
    assert state_scope.prepare_test_state(root, baseline, version="0.7.292", revision="a" * 40) == config
    with pytest.raises(state_scope.StateScopeError, match="pointer is invalid"):
        state_scope.prepare_test_state(new_root, baseline, version="0.7.300", revision="b" * 40)
    assert not new_root.exists()
