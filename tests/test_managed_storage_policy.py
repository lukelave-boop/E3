"""Managed-only storage admission against isolated synthetic files and services."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
sys.path.insert(0, str(PACKAGING))
_SPEC = importlib.util.spec_from_file_location("e3_managed_policy_under_test", PACKAGING / "build_storage_guard.py")
assert _SPEC is not None and _SPEC.loader is not None
guard = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = guard
_SPEC.loader.exec_module(guard)
GIB = 1024**3
REFUSALS = (guard.StorageGuardError, guard.retention.RetentionError, OSError)


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "checkout"
    source.mkdir()

    def legacy_forbidden(*args, **kwargs):
        pytest.fail("managed admission attempted legacy discovery or census")

    for name in ("discover_roots", "_census_paths", "_measure"):
        monkeypatch.setattr(guard, name, legacy_forbidden)
    monkeypatch.setattr(guard, "inspect_build_processes", lambda: [])
    monkeypatch.setattr(
        guard.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(100 * GIB, 0, 100 * GIB),
    )
    selected = _write(tmp_path / "selected" / "E3.exe", b"synthetic selected executable")
    pointer = _write(tmp_path / "pointer.json", b"{}")
    monkeypatch.setattr(guard.retention, "_launcher_target", lambda path: selected)
    return {
        "worktree": source, "mode": "windows", "build_id": "first", "owner_pid": os.getppid(),
        "state_path": tmp_path / "state.json", "launcher_pointers": [pointer], "managed_outputs": True,
    }


def _write(path: Path, payload: bytes = b"synthetic artifact") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _state(environment):
    return json.loads(environment["state_path"].read_text(encoding="utf-8"))


def _identity(environment, lease):
    return {"state_path": environment["state_path"], "build_id": environment["build_id"],
            "token": lease["token"], "owner_pid": environment["owner_pid"]}


def _other(environment, name="other"):
    source = environment["worktree"].parent / name
    source.mkdir()
    return {**environment, "worktree": source, "build_id": name}


def _forbid_payload_reads(monkeypatch, paths):
    original = Path.open

    def checked(path, *args, **kwargs):
        if path in paths:
            pytest.fail(f"capacity accounting opened a payload: {path}")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked)


def _finish(environment):
    lease = guard.begin_build(**environment)
    guard.verify_build(**_identity(environment, lease))
    output = Path(lease["output_root"])
    for path in guard.output_paths(output, environment["mode"], managed=True):
        if path.suffix in {".json", ".spec", ".exe", ".AppImage"}:
            _write(path)
        else:
            _write(path / "generated.bin")
    guard.complete_build(**_identity(environment, lease))
    return lease


@pytest.mark.parametrize("mode", ["windows", "private", "launcher"])
def test_absent_state_starts_managed_policy_without_legacy_baseline(environment, mode):
    environment["mode"] = mode
    assert not environment["state_path"].exists()

    lease = guard.begin_build(**environment)

    source = environment["worktree"]
    assert Path(lease["output_root"]).parent == source / ".e3-builds"
    assert lease["managed_output"] is True
    assert lease["measured_bytes"] == 0
    assert lease["reserved_bytes"] == 2 * GIB
    assert lease["ceiling_bytes"] == 8 * GIB
    state = _state(environment)
    assert state["schema_version"] == 2
    assert state["accounting_policy"] == "managed_outputs_v2"
    assert state["managed_budget_bytes"] == 8 * GIB
    assert state["legacy_total_bytes"] is None
    assert state.get("baseline_bytes") is None
    assert [item["path"] for item in state["managed_containers"]] == [str(source / ".e3-builds")]
    guard.verify_build(**_identity(environment, lease))


def test_unreadable_legacy_trees_are_never_enumerated_or_opened(environment, monkeypatch):
    source = environment["worktree"]
    legacy_paths = [source / name for name in ("dist", ".venv", ".codex-worktrees", "build", "data")]
    payloads = [_write(path / "preserve.bin") for path in legacy_paths]
    _forbid_payload_reads(monkeypatch, payloads)
    original = guard.os.scandir

    def deny_legacy(path):
        if Path(path) in legacy_paths or Path(path) == source:
            pytest.fail(f"managed policy traversed unrelated source/legacy scope: {path}")
        return original(path)

    monkeypatch.setattr(guard.os, "scandir", deny_legacy)
    lease = guard.begin_build(**environment)
    assert lease["measured_bytes"] == 0
    assert _state(environment)["legacy_total_bytes"] is None
    guard.verify_build(**_identity(environment, lease))


def test_whole_pool_counts_unknown_private_failed_and_protected_payloads_without_reads(environment, monkeypatch):
    pool = environment["worktree"] / ".e3-builds"
    payloads = [
        _write(pool / "unregistered" / "odd.bin", b"a" * 19),
        _write(pool / "private-old" / "secrets" / "token.txt", b"b" * 23),
        _write(pool / "failed-old" / "temp" / "partial.bin", b"c" * 29),
        _write(pool / "rollback" / "dist" / "E3" / "E3.exe", b"d" * 31),
    ]
    monkeypatch.setattr(guard.retention, "_launcher_target", lambda path: payloads[-1])
    _forbid_payload_reads(monkeypatch, payloads)

    lease = guard.begin_build(**environment)

    assert lease["measured_bytes"] == 102
    assert sum(row["logical_bytes"] for row in lease["census"]) == 102
    assert _state(environment)["owned_outputs"] == []
    assert all(path.exists() for path in payloads)


@pytest.mark.parametrize("extra", [0, 1])
def test_fixed_budget_never_adopts_existing_managed_usage_as_baseline(environment, monkeypatch, extra):
    real_measure = guard._measure_managed

    def measured(state, *args, **kwargs):
        _, census = real_measure(state, *args, **kwargs)
        return 6 * GIB + extra, census

    monkeypatch.setattr(guard, "_measure_managed", measured)
    if extra:
        with pytest.raises(guard.StorageGuardError, match="budget"):
            guard.begin_build(**environment)
        assert not (environment["worktree"] / ".e3-builds").exists()
    else:
        lease = guard.begin_build(**environment)
        assert lease["ceiling_bytes"] == 8 * GIB
        assert lease["measured_bytes"] + lease["reserved_bytes"] == 8 * GIB
        assert _state(environment)["legacy_total_bytes"] is None


def test_reservations_share_eight_gib_across_sources_and_keep_failed_usage(environment):
    arguments = [environment, *[_other(environment, f"other-{index}") for index in range(4)]]
    leases = [guard.begin_build(**item) for item in arguments[:4]]
    state_before = environment["state_path"].read_bytes()
    assert sum(item["reserved_bytes"] for item in _state(environment)["leases"]) == 8 * GIB

    with pytest.raises(guard.StorageGuardError, match="budget"):
        guard.begin_build(**arguments[4])

    assert environment["state_path"].read_bytes() == state_before
    assert not (arguments[4]["worktree"] / ".e3-builds").exists()
    assert len(leases) == 4  # Interrupted builds have no completion/release call.


def test_failed_completion_retains_private_partial_bytes_and_reservation(environment, monkeypatch):
    environment["mode"] = "private"
    lease = guard.begin_build(**environment)
    guard.verify_build(**_identity(environment, lease))
    payload = _write(Path(lease["output_root"]) / "temp" / "secrets" / "token.txt", b"p" * 41)
    _forbid_payload_reads(monkeypatch, [payload])
    with pytest.raises(guard.StorageGuardError):
        guard.complete_build(**_identity(environment, lease))

    later = guard.begin_build(**_other(environment))

    assert later["measured_bytes"] == 41
    assert later["reserved_bytes"] == 4 * GIB
    assert len(_state(environment)["leases"]) == 2
    assert payload.exists()


def test_concurrent_admission_cannot_overreserve_or_lose_leases(environment):
    arguments = [environment, *[_other(environment, f"parallel-{index}") for index in range(4)]]
    barrier = Barrier(len(arguments))

    def attempt(item):
        barrier.wait(timeout=10)
        try:
            return guard.begin_build(**item)
        except guard.StorageGuardError:
            return None

    with ThreadPoolExecutor(max_workers=len(arguments)) as workers:
        results = list(workers.map(attempt, arguments))
    admitted = [result for result in results if result is not None]
    state = _state(environment)
    assert 1 <= len(admitted) <= 4
    assert len(state["leases"]) == len(admitted)
    assert sum(item["reserved_bytes"] for item in state["leases"]) <= 8 * GIB
    assert {item["token"] for item in state["leases"]} == {item["token"] for item in admitted}


@pytest.mark.parametrize("failure", ["missing", "identity", "inaccessible", "reparse"])
def test_bad_enrolled_pool_blocks_other_source_before_new_output(environment, monkeypatch, failure):
    guard.begin_build(**environment)
    pool = environment["worktree"] / ".e3-builds"
    other = _other(environment)
    state_before = environment["state_path"].read_bytes()
    if failure in {"missing", "identity"}:
        pool.rename(pool.with_name("preserved-pool"))
        if failure == "identity":
            pool.mkdir()
    elif failure == "inaccessible":
        original = guard.os.scandir

        def denied(path):
            if Path(path) == pool:
                raise PermissionError("synthetic inaccessible managed pool")
            return original(path)

        monkeypatch.setattr(guard.os, "scandir", denied)
    else:
        original = Path.lstat

        def reparse(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == pool:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info

        monkeypatch.setattr(Path, "lstat", reparse)

    with pytest.raises(REFUSALS):
        guard.begin_build(**other)

    assert environment["state_path"].read_bytes() == state_before
    assert not (other["worktree"] / ".e3-builds").exists()


def test_managed_payload_metadata_drift_during_census_fails_closed(environment, monkeypatch):
    guard.begin_build(**environment)
    pool = environment["worktree"] / ".e3-builds"
    payload = _write(pool / "unowned.bin", b"before")
    other = _other(environment)
    real_metadata = guard._metadata
    changed = False

    def drifting(path, *args, **kwargs):
        nonlocal changed
        result = real_metadata(path, *args, **kwargs)
        if path == pool and not changed:
            changed = True
            payload.write_bytes(b"changed-size-during-metadata-census")
        return result

    monkeypatch.setattr(guard, "_metadata", drifting)
    with pytest.raises(REFUSALS):
        guard.begin_build(**other)
    assert changed
    assert not (other["worktree"] / ".e3-builds").exists()


@pytest.mark.parametrize("initially_present", [False, True])
def test_container_appearance_or_replacement_after_census_is_not_enrolled(environment, monkeypatch, initially_present):
    pool = environment["worktree"] / ".e3-builds"
    if initially_present:
        _write(pool / "original.bin", b"preserve original container")
    real_admission = guard._managed_admission
    raced_payload = pool / "unknown-after-census.bin"

    def raced(state, lease, **kwargs):
        report = real_admission(state, lease, **kwargs)
        if initially_present:
            pool.rename(pool.with_name("preserved-original-pool"))
        _write(raced_payload, b"previously unmeasured bytes")
        return report

    monkeypatch.setattr(guard, "_managed_admission", raced)
    with pytest.raises(REFUSALS):
        guard.begin_build(**environment)

    assert raced_payload.read_bytes() == b"previously unmeasured bytes"
    assert not environment["state_path"].exists()
    assert not (pool / "windows-first").exists()


def test_schema_one_is_preserved_and_never_reinterpreted(environment):
    legacy = {
        "schema_version": 1, "baseline_bytes": 1000, "growth_headroom_bytes": 8 * GIB,
        "roots": [str(environment["worktree"])], "leases": [], "owned_outputs": [],
        "retention_registries": [], "observed_registries": [], "ephemeral": False,
    }
    environment["state_path"].write_text(json.dumps(legacy), encoding="utf-8")
    before = environment["state_path"].read_bytes()
    with pytest.raises(guard.StorageGuardError):
        guard.begin_build(**environment)
    assert environment["state_path"].read_bytes() == before
    assert not (environment["worktree"] / ".e3-builds").exists()


@pytest.mark.parametrize("reserve", [0, 1, 2 * GIB - 1])
def test_managed_reservation_cannot_be_lowered_below_two_gib(environment, reserve):
    with pytest.raises(guard.StorageGuardError):
        guard.begin_build(**environment, reserve_bytes=reserve)
    assert not (environment["worktree"] / ".e3-builds").exists()


@pytest.mark.parametrize("extra", [0, -1])
def test_free_space_floor_keeps_two_gib_after_default_reservation(environment, monkeypatch, extra):
    monkeypatch.setattr(
        guard.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10 * GIB, 0, 4 * GIB + extra),
    )
    if extra:
        with pytest.raises(guard.StorageGuardError, match="free-space"):
            guard.begin_build(**environment)
        assert not (environment["worktree"] / ".e3-builds").exists()
    else:
        lease = guard.begin_build(**environment)
        assert lease["free_bytes"] - lease["reserved_bytes"] == 2 * GIB


@pytest.mark.parametrize("mode", ["windows", "private", "launcher"])
def test_cache_and_temp_are_owned_inside_managed_destination(environment, mode):
    environment["mode"] = mode
    lease = _finish(environment)
    output = Path(lease["output_root"])
    paths = {Path(item["snapshot"]["path"]) for item in _state(environment)["owned_outputs"]}
    assert {output / "cache", output / "temp"} <= paths
    assert all(path.is_relative_to(output) for path in paths)
    assert output / "build" / "pyinstaller-cache" not in paths


def test_public_cache_and_temp_ownership_never_reads_private_payloads(environment, monkeypatch):
    lease = guard.begin_build(**environment)
    guard.verify_build(**_identity(environment, lease))
    output = Path(lease["output_root"])
    private_payloads = []
    for path in guard.output_paths(output, "windows", managed=True):
        if path.name in {"cache", "temp"}:
            private_payloads.append(_write(path / "secrets" / "token.txt", b"synthetic private cache"))
        elif path.suffix in {".json", ".spec", ".exe"}:
            _write(path)
        else:
            _write(path / "generated.bin")
    _forbid_payload_reads(monkeypatch, private_payloads)

    guard.complete_build(**_identity(environment, lease))
    successor = guard.begin_build(**{**environment, "build_id": "reuse-cache"})

    assert Path(successor["output_root"]) == output
    owners = _state(environment)["owned_outputs"]
    for path in (output / "cache", output / "temp"):
        owner = next(item for item in owners if item["snapshot"]["path"] == str(path))
        assert owner["snapshot"]["private"] is True


def test_readiness_is_advisory_without_state_directories_or_reservation(environment):
    arguments = {key: value for key, value in environment.items() if key != "managed_outputs"}
    before = set(environment["worktree"].parent.rglob("*"))

    report = guard.check_managed_build(**arguments)

    assert report["readiness_ok"] is True
    assert report["advisory"] is True
    assert report["reservation_created"] is False
    assert report["state_created"] is False
    assert set(environment["worktree"].parent.rglob("*")) == before
    assert not environment["state_path"].exists()
    assert not (environment["worktree"] / ".e3-builds").exists()


def test_readiness_of_owned_output_is_metadata_only_and_preserves_existing_state(environment, monkeypatch):
    lease = _finish(environment)
    output = Path(lease["output_root"])
    payloads = [path for path in output.rglob("*") if path.is_file()]
    _forbid_payload_reads(monkeypatch, payloads)
    before_state = environment["state_path"].read_bytes()
    before_paths = set(environment["worktree"].parent.rglob("*"))
    arguments = {key: value for key, value in environment.items() if key != "managed_outputs"}
    arguments["build_id"] = "readiness-later"

    report = guard.check_managed_build(**arguments)

    assert report["readiness_ok"] is True
    assert Path(report["output_root"]) == output
    assert report["reservation_created"] is False
    assert environment["state_path"].read_bytes() == before_state
    assert set(environment["worktree"].parent.rglob("*")) == before_paths


@pytest.mark.parametrize(("mode", "flags", "managed"), [
    ("windows", (), True),
    ("private", (), True),
    ("launcher", (), True),
    ("linux", (), False),
    ("windows", ("--legacy-accounting",), False),
    ("windows", ("--ephemeral-ci",), False),
    ("linux", ("--managed-outputs",), True),
])
def test_cli_dispatch_keeps_linux_strict_and_windows_managed(monkeypatch, capsys, mode, flags, managed):
    calls = []

    def capture_begin(**kwargs):
        calls.append(kwargs)
        if mode == "linux" and managed:
            raise guard.StorageGuardError("synthetic API refusal of managed Linux mode")
        return {"synthetic_dispatch": True}

    monkeypatch.setattr(guard, "begin_build", capture_begin)
    result = guard.main([
        "begin", "--worktree", "synthetic-checkout", "--mode", mode,
        "--build-id", "dispatch-only", "--pid", "123", *flags,
    ])

    assert len(calls) == 1
    assert calls[0]["mode"] == mode
    assert calls[0]["managed_outputs"] is managed
    assert calls[0]["ephemeral_ci"] is ("--ephemeral-ci" in flags)
    captured = capsys.readouterr()
    if mode == "linux" and managed:
        assert result == 2
        assert "synthetic API refusal" in captured.err
    else:
        assert result == 0
        assert json.loads(captured.out) == {"synthetic_dispatch": True}
