"""Managed build destinations tested with synthetic files and local-service stubs."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
sys.path.insert(0, str(PACKAGING))
_SPEC = importlib.util.spec_from_file_location("e3_managed_outputs_under_test", PACKAGING / "build_storage_guard.py")
assert _SPEC is not None and _SPEC.loader is not None
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "checkout"
    source.mkdir()
    monkeypatch.setattr(guard, "discover_roots", lambda worktree: [worktree])
    monkeypatch.setattr(guard, "inspect_build_processes", lambda: [])
    monkeypatch.setattr(
        guard.shutil, "disk_usage",
        lambda path: shutil._ntuple_diskusage(100 * 1024**3, 0, 100 * 1024**3),
    )
    pointer = tmp_path / "pointer.json"
    pointer.write_text("{}", encoding="utf-8")
    selected = tmp_path / "selected" / "E3.exe"
    selected.parent.mkdir()
    selected.write_bytes(b"synthetic selected executable")
    monkeypatch.setattr(guard.retention, "_launcher_target", lambda path: selected)
    return {
        "worktree": source, "mode": "windows", "build_id": "first", "owner_pid": os.getppid(),
        "state_path": tmp_path / "storage.json", "launcher_pointers": [pointer],
        "reserve_bytes": 1024, "managed_outputs": True,
    }


def _write(path: Path, payload: bytes = b"preserve original") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _stamp(path: Path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _forbid_reads(monkeypatch: pytest.MonkeyPatch, *payloads: Path) -> None:
    original = Path.open

    def checked_open(path, *args, **kwargs):
        if path in payloads:
            pytest.fail(f"preserved payload was opened: {path}")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)


def _identity(arguments: dict, lease: dict) -> dict:
    return {
        "state_path": Path(lease["state"]), "build_id": arguments["build_id"],
        "token": lease["token"], "owner_pid": arguments["owner_pid"],
    }


def _managed_leaves(source: Path) -> set[Path]:
    parent = source / ".e3-builds"
    return set(parent.iterdir()) if parent.exists() else set()


def _assert_fresh_destination(arguments: dict, lease: dict) -> Path:
    source = arguments["worktree"]
    output = Path(lease["output_root"])
    assert Path(lease["source_root"]) == source
    assert lease["managed_output"] is True
    assert output.parent == source / ".e3-builds"
    assert output.name.startswith(arguments["mode"] + "-")
    assert arguments["build_id"] in output.name
    assert output.is_dir()
    assert list(output.iterdir()) == []
    return output


def _finish_build(arguments: dict) -> dict:
    lease = guard.begin_build(**arguments)
    identity = _identity(arguments, lease)
    guard.verify_build(**identity)
    output = Path(lease["output_root"])
    for path in guard.output_paths(output, arguments["mode"], arguments.get("installer_name", "E3-Setup")):
        if path.suffix in {".json", ".spec", ".exe", ".AppImage"}:
            _write(path, b"synthetic generated output")
        else:
            _write(path / "generated.bin", b"synthetic generated output")
    if arguments["mode"] in {"windows", "private"}:
        _write(output / "dist" / "E3" / "E3.exe", b"synthetic executable")
    guard.complete_build(**identity)
    return lease


@pytest.mark.parametrize("mode", ["windows", "private", "launcher"])
def test_unknown_legacy_output_gets_empty_managed_root_without_reading_payload(environment, monkeypatch, mode):
    environment["mode"] = mode
    source = environment["worktree"]
    legacy_name = "launcher-dist/E3 DEV TEST.exe" if mode == "launcher" else "dist/E3/legacy.bin"
    payload = _write(source / legacy_name)
    source_file = _write(source / "laser_aligner" / "local_work.py", b"uncommitted source")
    project = _write(source / "data" / "unsaved.e3laser", b"unique project")
    before = {path: _stamp(path) for path in (payload, source_file, project)}
    _forbid_reads(monkeypatch, payload, source_file, project)

    lease = guard.begin_build(**environment)

    _assert_fresh_destination(environment, lease)
    assert {path: _stamp(path) for path in before} == before
    guard.verify_build(**_identity(environment, lease))


def test_changed_owned_output_is_preserved_and_new_build_uses_fresh_destination(environment, monkeypatch):
    _write(environment["worktree"] / "dist" / "E3" / "legacy.bin")
    first = _finish_build(environment)
    previous_output = Path(first["output_root"])
    payload = _write(previous_output / "dist" / "E3" / "user-project.e3laser", b"new unique artwork")
    before = _stamp(payload)
    _forbid_reads(monkeypatch, payload)
    environment["build_id"] = "second"

    lease = guard.begin_build(**environment)

    assert _assert_fresh_destination(environment, lease) != previous_output
    assert _stamp(payload) == before
    guard.verify_build(**_identity(environment, lease))


@pytest.mark.parametrize("protection", ["selected", "pinned", "running"])
def test_protected_owned_bundle_stays_in_place_while_new_build_gets_fresh_root(environment, monkeypatch, protection):
    _write(environment["worktree"] / "dist" / "E3" / "legacy.bin")
    registry = environment["state_path"].with_name("explicit-pins.json")
    registry.write_text('{"schema_version":1,"builds":[],"leases":[]}', encoding="utf-8")
    environment["retention_registry"] = registry
    first = _finish_build(environment)
    previous_output = Path(first["output_root"])
    executable = previous_output / "dist" / "E3" / "E3.exe"
    before = _stamp(executable)
    if protection == "selected":
        monkeypatch.setattr(guard.retention, "_launcher_target", lambda path: executable)
    elif protection == "pinned":
        monkeypatch.setattr(guard.retention, "_load_registry", lambda path: {
            "leases": [], "builds": [{"pin": "rollback", "artifacts": [{"path": str(executable.parent)}]}],
        })
    else:
        monkeypatch.setattr(guard, "inspect_build_processes", lambda: [
            guard.BuildProcess(999999, 1, executable, "synthetic running executable"),
        ])
    _forbid_reads(monkeypatch, executable)
    environment["build_id"] = "protected-successor"

    lease = guard.begin_build(**environment)

    assert _assert_fresh_destination(environment, lease) != previous_output
    assert _stamp(executable) == before
    guard.verify_build(**_identity(environment, lease))


def test_unchanged_successful_managed_output_is_reused_without_another_directory(environment, monkeypatch):
    source = environment["worktree"]
    legacy = _write(source / "dist" / "E3" / "legacy.bin")
    before = _stamp(legacy)
    _forbid_reads(monkeypatch, legacy)
    first = _finish_build(environment)
    previous_output = Path(first["output_root"])
    environment["build_id"] = "unchanged-successor"

    lease = guard.begin_build(**environment)

    assert lease["managed_output"] is True
    assert Path(lease["source_root"]) == source
    assert Path(lease["output_root"]) == previous_output
    assert _managed_leaves(source) == {previous_output}
    assert _stamp(legacy) == before
    guard.verify_build(**_identity(environment, lease))


@pytest.mark.parametrize("changed_setting", ["mode", "installer_name"])
def test_managed_output_reuse_does_not_cross_mode_or_installer_identity(environment, changed_setting):
    _write(environment["worktree"] / "dist" / "E3" / "legacy.bin")
    first = _finish_build(environment)
    previous_output = Path(first["output_root"])
    environment["build_id"] = "different-settings"
    environment[changed_setting] = "private" if changed_setting == "mode" else "Different-Installer"

    lease = guard.begin_build(**environment)

    assert _assert_fresh_destination(environment, lease) != previous_output
    assert _managed_leaves(environment["worktree"]) == {previous_output, Path(lease["output_root"])}
    guard.verify_build(**_identity(environment, lease))


def test_public_build_with_legacy_private_seed_uses_fresh_root_without_reading_seed(environment, monkeypatch):
    seed = _write(environment["worktree"] / "packaging" / "machine-seed" / "secrets" / "bridge-token.txt")
    before = _stamp(seed)
    _forbid_reads(monkeypatch, seed)

    lease = guard.begin_build(**environment)

    output = _assert_fresh_destination(environment, lease)
    assert not (output / "packaging" / "machine-seed").exists()
    assert _stamp(seed) == before
    guard.verify_build(**_identity(environment, lease))


def test_census_counts_whole_managed_container_without_opening_private_payloads(environment, monkeypatch):
    source = environment["worktree"]
    paths = [
        _write(source / ".e3-builds" / "windows-old" / "unexpected-cache.bin", b"a" * 19),
        _write(source / ".e3-builds" / "private-old" / "secrets" / "bridge-token.txt", b"b" * 23),
        _write(source / ".e3-builds" / "unregistered-note.bin", b"c" * 29),
        _write(source / ".venv" / "Lib" / "runtime.bin", b"d" * 31),
    ]
    _write(source / "source-not-build-output.py", b"e" * 101)
    _forbid_reads(monkeypatch, *paths)

    measured, census = guard._measure([source], ephemeral=False)

    assert measured == 19 + 23 + 29 + 31
    assert sum(item["logical_bytes"] for item in census) == measured


@pytest.mark.parametrize("replace", ["leaf", "container"])
def test_verify_rechecks_managed_root_identity_before_output_writes(environment, replace):
    _write(environment["worktree"] / "dist" / "E3" / "legacy.bin")
    lease = guard.begin_build(**environment)
    output = _assert_fresh_destination(environment, lease)
    original = output if replace == "leaf" else output.parent
    moved = original.with_name(original.name + "-preserved")
    original.rename(moved)
    output.mkdir(parents=True)

    with pytest.raises(guard.StorageGuardError):
        guard.verify_build(**_identity(environment, lease))

    assert moved.is_dir()
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("failure", ["budget", "free_space", "process_inventory", "accounting", "reservation"])
def test_admission_failures_never_allocate_managed_output_or_fall_back(environment, monkeypatch, failure):
    source = environment["worktree"]
    payload = _write(source / "dist" / "E3" / "legacy.bin")
    before = _stamp(payload)
    _forbid_reads(monkeypatch, payload)
    if failure == "budget":
        environment["reserve_bytes"] = guard.GROWTH_HEADROOM_BYTES + 1
    elif failure == "free_space":
        monkeypatch.setattr(
            guard.shutil, "disk_usage",
            lambda path: shutil._ntuple_diskusage(100 * 1024**3, 0, guard.MINIMUM_FREE_BYTES),
        )
    elif failure == "reservation":
        environment["reserve_bytes"] = 0
    else:
        def unavailable(*args, **kwargs):
            raise guard.StorageGuardError("inspection unavailable")

        monkeypatch.setattr(guard, "inspect_build_processes" if failure == "process_inventory" else "_measure", unavailable)

    with pytest.raises(guard.StorageGuardError):
        guard.begin_build(**environment)

    assert _managed_leaves(source) == set()
    assert _stamp(payload) == before
    if environment["state_path"].exists():
        assert json.loads(environment["state_path"].read_text(encoding="utf-8"))["leases"] == []


@pytest.mark.parametrize("process_kind", ["shared_venv", "source_builder"])
def test_process_using_shared_build_inputs_blocks_fresh_destination(environment, monkeypatch, process_kind):
    source = environment["worktree"]
    _write(source / "dist" / "E3" / "legacy.bin")
    executable = source / ".venv" / "Scripts" / "python.exe" if process_kind == "shared_venv" else None
    command = "synthetic interpreter" if process_kind == "shared_venv" else f'pwsh "{source / "packaging" / "build_windows.ps1"}"'
    monkeypatch.setattr(guard, "inspect_build_processes", lambda: [
        guard.BuildProcess(999999, 1, executable, command),
    ])

    with pytest.raises(guard.StorageGuardError):
        guard.begin_build(**environment)

    assert _managed_leaves(source) == set()
    if environment["state_path"].exists():
        assert json.loads(environment["state_path"].read_text(encoding="utf-8"))["leases"] == []


def test_leases_serialize_source_checkout_even_for_different_managed_modes(environment):
    source = environment["worktree"]
    _write(source / "dist" / "E3" / "legacy.bin")
    _write(source / "launcher-dist" / "E3 DEV TEST.exe")
    first = guard.begin_build(**environment)
    first_output = _assert_fresh_destination(environment, first)
    environment.update(build_id="different-mode", mode="launcher")

    with pytest.raises(guard.StorageGuardError):
        guard.begin_build(**environment)

    assert _managed_leaves(source) == {first_output}
    state = json.loads(environment["state_path"].read_text(encoding="utf-8"))
    assert len(state["leases"]) == 1
    assert state["leases"][0]["token"] == first["token"]
