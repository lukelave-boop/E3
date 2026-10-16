"""Synthetic packaging ownership/admission tests; no E3 process is launched."""

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
_SPEC = importlib.util.spec_from_file_location("e3_storage_guard_under_test", PACKAGING / "build_storage_guard.py")
assert _SPEC is not None and _SPEC.loader is not None
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setattr(guard, "discover_roots", lambda worktree: [worktree])
    monkeypatch.setattr(guard, "inspect_build_processes", lambda: [])
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(100 * 1024**3, 0, 100 * 1024**3))
    pointer = tmp_path / "pointer.json"
    selected = tmp_path / "selected" / "E3.exe"
    selected.parent.mkdir()
    selected.write_bytes(b"selected")
    pointer.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(guard.retention, "_launcher_target", lambda path: selected)
    return {
        "worktree": root, "mode": "windows", "build_id": "first", "owner_pid": os.getppid(),
        "state_path": tmp_path / "storage.json", "launcher_pointers": [pointer], "reserve_bytes": 1024,
    }


def finish_build(arguments: dict, *, seed: bool = False):
    lease = guard.begin_build(**arguments)
    identity = {"state_path": Path(lease["state"]), "build_id": arguments["build_id"], "token": lease["token"], "owner_pid": arguments["owner_pid"]}
    guard.verify_build(**identity)
    for path in guard.output_paths(arguments["worktree"], arguments["mode"], arguments.get("installer_name", "E3-Setup")):
        if path.name == "machine-seed" and not seed:
            continue
        if path.suffix in {".json", ".spec", ".exe", ".AppImage"}:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"generated")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / "generated.bin").write_bytes(b"generated")
    guard.complete_build(**identity)
    return lease


def test_successful_build_owns_exact_outputs_and_allows_unchanged_rebuild(environment):
    finish_build(environment)
    environment["build_id"] = "second"
    lease = guard.begin_build(**environment)
    assert lease["baseline_bytes"] == 0
    assert lease["ceiling_bytes"] == guard.GROWTH_HEADROOM_BYTES
    state = json.loads(environment["state_path"].read_text())
    assert len(state["owned_outputs"]) == 6  # Includes the destination-local PyInstaller cache.
    assert len(state["leases"]) == 1


def test_unknown_output_is_refused_without_reading_its_contents(environment, monkeypatch):
    payload = environment["worktree"] / "dist" / "E3" / "credential.bin"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"do-not-open")
    original = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == payload:
            pytest.fail("unknown legacy payload was opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(guard.StorageGuardError, match="unknown"):
        guard.begin_build(**environment)
    assert not environment["state_path"].exists()


def test_modified_owned_output_and_raced_output_are_refused(environment):
    finish_build(environment)
    environment["build_id"] = "second"
    lease = guard.begin_build(**environment)
    (environment["worktree"] / "dist" / "E3" / "new-project.e3laser").write_text("unique")
    with pytest.raises(guard.StorageGuardError, match="changed"):
        guard.verify_build(state_path=Path(lease["state"]), build_id="second", token=lease["token"], owner_pid=environment["owner_pid"])


def test_private_material_added_to_owned_public_output_is_never_read(environment, monkeypatch):
    finish_build(environment)
    payload = environment["worktree"] / "dist" / "E3" / "secrets" / "bridge-token.txt"
    payload.parent.mkdir()
    payload.write_bytes(b"must-not-read")
    original = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == payload:
            pytest.fail("private material added to an owned public output was read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    environment["build_id"] = "later"
    with pytest.raises(guard.StorageGuardError, match="metadata changed"):
        guard.begin_build(**environment)


def test_private_owned_outputs_use_metadata_only_and_cannot_change_mode(environment, monkeypatch):
    environment["mode"] = "private"
    finish_build(environment, seed=True)
    payload = environment["worktree"] / "packaging" / "machine-seed" / "generated.bin"
    original = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == payload:
            pytest.fail("private payload was read")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    environment["build_id"] = "private-second"
    guard.begin_build(**environment)
    environment["mode"], environment["build_id"] = "windows", "wrong-mode"
    with pytest.raises(guard.StorageGuardError):
        guard.begin_build(**environment)


def test_baseline_never_rises_and_counts_build_caches_and_virtualenv(environment):
    root = environment["worktree"]
    for name in ("build/firmware-tools/tool.bin", ".venv/Lib/runtime.bin"):
        path = root / name
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x" * 100)
    finish_build(environment)
    state = json.loads(environment["state_path"].read_text())
    assert state["baseline_bytes"] == 200
    extra = root / "build" / "later-cache.bin"
    extra.write_bytes(b"x" * 300)
    environment["build_id"] = "later"
    lease = guard.begin_build(**environment)
    assert lease["baseline_bytes"] == 200
    assert lease["measured_bytes"] >= 500


def test_reservations_are_global_and_atomic_across_checkouts(environment, tmp_path):
    environment["reserve_bytes"] = guard.GROWTH_HEADROOM_BYTES - 1
    guard.begin_build(**environment)
    other = tmp_path / "other"
    other.mkdir()
    environment.update(worktree=other, build_id="other", reserve_bytes=2)
    with pytest.raises(guard.StorageGuardError, match="budget exceeded"):
        guard.begin_build(**environment)
    state = json.loads(environment["state_path"].read_text())
    assert len(state["leases"]) == 1


def test_free_space_floor_blocks_before_build(environment, monkeypatch):
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(1, 0, guard.MINIMUM_FREE_BYTES))
    with pytest.raises(guard.StorageGuardError, match="free-space floor"):
        guard.begin_build(**environment)


def test_selected_and_unknown_named_runtime_helpers_are_protected(environment, monkeypatch):
    finish_build(environment)
    environment["build_id"] = "second"
    helper = environment["worktree"] / "dist" / "E3" / "_internal" / "QtWebEngineProcess.exe"
    monkeypatch.setattr(guard, "inspect_build_processes", lambda: [guard.BuildProcess(999999, 1, helper, "")])
    with pytest.raises(guard.StorageGuardError, match="selected, running or pinned"):
        guard.begin_build(**environment)


def test_pin_registry_remains_known_when_later_argument_is_omitted(environment, monkeypatch):
    registry = environment["state_path"].with_name("explicit-pins.json")
    registry.write_text('{"schema_version":1,"builds":[],"leases":[]}', encoding="utf-8")
    environment["retention_registry"] = registry
    finish_build(environment)
    state = json.loads(environment["state_path"].read_text())
    assert str(registry) in state["retention_registries"]
    monkeypatch.setattr(guard.retention, "_load_registry", lambda path: {"leases": [], "builds": [{"pin": "rollback", "artifacts": [{"path": str(environment["worktree"] / "dist" / "E3")}]}]})
    environment.pop("retention_registry")
    environment["build_id"] = "later"
    with pytest.raises(guard.StorageGuardError, match="pinned"):
        guard.begin_build(**environment)


def test_spoofed_owner_is_rejected(environment):
    environment["owner_pid"] = 99999999
    with pytest.raises(guard.StorageGuardError, match="verified invoking"):
        guard.begin_build(**environment)


@pytest.mark.skipif(os.name != "nt", reason="Windows venv redirectors")
def test_windows_redirector_chain_is_verified_and_unrelated_python_is_not(monkeypatch):
    monkeypatch.setattr(guard.os, "getpid", lambda: 300)
    monkeypatch.setattr(guard.os, "getppid", lambda: 200)
    process = guard.BuildProcess(200, 100, Path("C:/venv/Scripts/python.exe"), "python packaging/build_storage_guard.py begin --pid 100")
    monkeypatch.setattr(guard, "inspect_build_processes", lambda: [process])
    assert guard._validate_owner(100) == {100, 200, 300}
    monkeypatch.setattr(guard, "inspect_build_processes", lambda: [guard.BuildProcess(200, 100, process.executable, "unrelated.py")])
    with pytest.raises(guard.StorageGuardError):
        guard._validate_owner(100)


@pytest.mark.parametrize("name", ["Workshop-Installer", "E3-Home-Setup", "portable_2026.10"])
def test_custom_installer_names_preserve_ordinary_build_compatibility(tmp_path, name):
    assert tmp_path / "installer-dist" / f"{name}.exe" in guard.output_paths(tmp_path, "windows", name)


@pytest.mark.parametrize("name", ["../escape", "CON", "nul.backup", "a/b", "C:other", ""])
def test_ambiguous_installer_names_are_rejected(tmp_path, name):
    with pytest.raises(guard.StorageGuardError):
        guard.output_paths(tmp_path, "windows", name)


def test_census_counts_nested_outputs_only_once(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    nested = root / "dist" / "nested-checkout"
    sibling = tmp_path / "checkout-other"
    observed = []
    monkeypatch.setattr(guard, "_existing_chain", lambda path: None)

    def metadata_only(path, *, private, allow_posix_links):
        assert private is True
        observed.append(path)
        return {"logical_bytes": 1}

    monkeypatch.setattr(guard, "_snapshot", metadata_only)
    total, _ = guard._measure([nested, sibling, root, nested], ephemeral=False)
    expected = sorted([*guard._census_paths(root), *guard._census_paths(sibling)])
    assert observed == expected
    assert total == len(expected)


def test_census_supports_hundreds_of_explicit_checkout_roots(tmp_path, monkeypatch):
    roots = [tmp_path / f"checkout-{number:03}" for number in range(296)]
    monkeypatch.setattr(guard, "_existing_chain", lambda path: None)
    monkeypatch.setattr(guard, "_snapshot", lambda path, **kwargs: {"logical_bytes": 1})
    total, census = guard._measure(roots, ephemeral=False)
    assert total == 3552
    assert len(census) == 3552
