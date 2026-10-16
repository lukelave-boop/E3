from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("e3_artifact_retention", ROOT / "packaging" / "artifact_retention.py")
assert SPEC is not None and SPEC.loader is not None
retention = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = retention
SPEC.loader.exec_module(retention)
NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def write_build(root: Path, build_id: str) -> tuple[Path, Path]:
    worktree = root / "worktrees" / build_id
    bundle = worktree / "dist" / "E3"
    bundle.mkdir(parents=True)
    (bundle / "E3.exe").write_bytes(b"MZ synthetic executable\x00")
    (bundle / "_internal").mkdir()
    (bundle / "_internal" / "runtime.dll").write_bytes(b"synthetic runtime" * 32)
    (bundle / "build-info.json").write_text(
        json.dumps({
            "schema_version": 1,
            "packaged": True,
            "platform_key": "windows-x86_64",
            "channel": "development",
            "version": "0.7.1",
            "revision": "abcdef0123456789",
        }),
        encoding="utf-8",
    )
    return worktree, bundle


def register(root: Path, registry: Path, build_id: str, days_old: float = 30) -> Path:
    worktree, bundle = write_build(root, build_id)
    retention.register_build(
        registry, worktree=worktree, build_id=build_id, bundle=bundle,
        completed_at=(NOW - timedelta(days=days_old)).isoformat(),
    )
    return bundle


def write_pointer(path: Path, bundle: Path) -> Path:
    path.write_text(json.dumps({
        "name": "Synthetic test", "version": "0.7.1", "branch": "test/retention",
        "revision": "abcdef0123456789", "exe": str(bundle / "E3.exe"),
    }), encoding="utf-8")
    return path


def plan(registry: Path, pointer: Path, **kwargs: object) -> dict:
    return retention.plan_retention(
        registry, launcher_pointers=[pointer], now=NOW,
        process_inspector=lambda: retention.ProcessSnapshot(), **kwargs,
    )


@pytest.fixture
def inventory(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    registry = tmp_path / "registry"
    bundles = {f"build-{i}": register(tmp_path, registry, f"build-{i}", 40 - i) for i in range(5)}
    retention.set_pin(registry, build_id="build-0", role="rollback")
    pointer = write_pointer(tmp_path / "pointer.json", bundles["build-4"])
    return registry, pointer, bundles


def by_id(report: dict) -> dict[str, dict]:
    return {build["id"]: build for build in report["builds"]}


def test_default_plan_never_changes_payload_and_preserves_newest_selected_rollback(inventory) -> None:
    registry, pointer, bundles = inventory
    before = {name: (path / "E3.exe").read_bytes() for name, path in bundles.items()}
    report = plan(registry, pointer)
    assert report["execution_enabled"] is False
    assert report["inspection_complete"] is True
    builds = by_id(report)
    assert not builds["build-0"]["eligible"]
    assert not builds["build-3"]["eligible"]
    assert not builds["build-4"]["eligible"]
    assert builds["build-1"]["eligible"]
    assert not builds["build-2"]["eligible"]
    assert report["candidate_logical_bytes"] > 0
    assert {name: (path / "E3.exe").read_bytes() for name, path in bundles.items()} == before


def test_missing_rollback_blocks_candidates_but_not_inventory(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    bundles = [register(tmp_path, registry, f"b{i}", 40 - i) for i in range(4)]
    pointer = write_pointer(tmp_path / "pointer.json", bundles[-1])
    report = plan(registry, pointer)
    assert report["inspection_complete"] is True
    assert report["blocking_reasons"]
    assert not report["candidates"]


def test_recent_build_survives_count_and_budget(inventory, monkeypatch) -> None:
    registry, pointer, bundles = inventory
    register(pointer.parent, registry, "recent", 1)
    monkeypatch.setattr(retention, "BUDGET_BYTES", 1)
    report = plan(registry, pointer)
    assert not by_id(report)["recent"]["eligible"]
    assert report["budget_exceeded"] is True
    assert not by_id(report)["build-0"]["eligible"]
    assert not by_id(report)["build-4"]["eligible"]


def test_deterministic_ties_and_seven_day_boundary(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    bundles = {name: register(tmp_path, registry, name, 7) for name in ["d", "b", "c", "a", "e", "f"]}
    retention.set_pin(registry, build_id="a", role="rollback")
    pointer = write_pointer(tmp_path / "pointer.json", bundles["f"])
    first = plan(registry, pointer)
    second = plan(registry, pointer)
    assert first["candidates"] == second["candidates"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert any(build["eligible"] for build in first["builds"])


@pytest.mark.parametrize("change", ["modify", "add", "remove"])
def test_changed_payload_blocks_inventory(inventory, change: str) -> None:
    registry, pointer, bundles = inventory
    target = bundles["build-1"] / "_internal" / "runtime.dll"
    if change == "modify":
        target.write_bytes(b"changed after registration")
    elif change == "add":
        (target.parent / "unexpected.txt").write_text("new user file", encoding="utf-8")
    else:
        target.unlink()
    report = plan(registry, pointer)
    assert not report["inspection_complete"]
    assert not report["candidates"]


def test_running_artifact_and_explicit_keep_pin_are_protected(inventory) -> None:
    registry, pointer, bundles = inventory
    retention.set_pin(registry, build_id="build-2", role="keep")
    report = plan(registry, pointer, active_paths=[bundles["build-1"] / "E3.exe"])
    assert not by_id(report)["build-1"]["eligible"]
    assert not by_id(report)["build-2"]["eligible"]


def test_process_snapshot_protects_runtime_and_source_command_line(inventory) -> None:
    registry, pointer, bundles = inventory
    report = retention.plan_retention(
        registry, launcher_pointers=[pointer], now=NOW,
        process_inspector=lambda: retention.ProcessSnapshot(
            executable_paths=(bundles["build-1"] / "E3.exe",),
            command_lines=(f'python "{bundles["build-2"].parents[1] / "packaging" / "build.py"}"',),
        ),
    )
    assert not by_id(report)["build-1"]["eligible"]
    assert not by_id(report)["build-2"]["eligible"]


def test_failed_process_inspection_fails_closed(inventory) -> None:
    registry, pointer, _ = inventory

    def failed_inspection():
        raise OSError("synthetic process access denied")

    report = retention.plan_retention(
        registry, launcher_pointers=[pointer], now=NOW, process_inspector=failed_inspection,
    )
    assert not report["inspection_complete"]
    assert not report["candidates"]


@pytest.mark.parametrize("pointer_state", ["missing", "invalid", "wrong_revision"])
def test_unverified_launcher_blocks_candidates(inventory, pointer_state: str) -> None:
    registry, pointer, _ = inventory
    if pointer_state == "missing":
        pointer.unlink()
    elif pointer_state == "invalid":
        pointer.write_text("{", encoding="utf-8")
    else:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        payload["revision"] = "123456789abcdef0"
        pointer.write_text(json.dumps(payload), encoding="utf-8")
    report = plan(registry, pointer)
    assert not report["inspection_complete"]
    assert not report["candidates"]


@pytest.mark.parametrize("forbidden", ["data", "projects", "secrets", "calibration_profiles", "evidence", ".git", ".venv"])
def test_registration_rejects_user_data_inside_bundle(tmp_path: Path, forbidden: str) -> None:
    worktree, bundle = write_build(tmp_path, "bad")
    (bundle / forbidden).mkdir()
    (bundle / forbidden / "preserve.txt").write_text("not a generated runtime", encoding="utf-8")
    with pytest.raises(retention.RetentionError):
        retention.register_build(tmp_path / "registry", worktree=worktree, build_id="bad", bundle=bundle)


def test_registration_rejects_escape_and_nonstandard_output(tmp_path: Path) -> None:
    worktree, bundle = write_build(tmp_path, "bad")
    for claimed_worktree in [tmp_path / "elsewhere", bundle]:
        claimed_worktree.mkdir(exist_ok=True)
        with pytest.raises(retention.RetentionError):
            retention.register_build(
                tmp_path / "registry", worktree=claimed_worktree, build_id="bad", bundle=bundle,
            )


def test_hardlinks_are_ineligible_even_without_content_changes(tmp_path: Path) -> None:
    worktree, bundle = write_build(tmp_path, "linked")
    try:
        os.link(bundle / "E3.exe", tmp_path / "outside-hardlink.exe")
    except OSError as exc:
        pytest.skip(f"Hardlinks unavailable on synthetic filesystem: {exc}")
    with pytest.raises(retention.RetentionError):
        retention.register_build(tmp_path / "registry", worktree=worktree, build_id="linked", bundle=bundle)


def test_registry_cannot_alias_payload(tmp_path: Path) -> None:
    worktree, bundle = write_build(tmp_path, "recursive")
    with pytest.raises(retention.RetentionError):
        retention.register_build(bundle / "registry", worktree=worktree, build_id="recursive", bundle=bundle)


@pytest.mark.parametrize("hazard", ["hardlink", "reparse_ancestor"])
def test_metadata_link_hazards_fail_closed_without_link_creation(tmp_path: Path, monkeypatch, hazard: str) -> None:
    worktree, bundle = write_build(tmp_path, "hazard")
    target = bundle / "E3.exe" if hazard == "hardlink" else worktree.parent
    original = Path.lstat

    def substituted(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == target:
            fields = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
            fields["st_nlink" if hazard == "hardlink" else "st_file_attributes"] = 2 if hazard == "hardlink" else 0x400
            return SimpleNamespace(**fields)
        return result

    monkeypatch.setattr(Path, "lstat", substituted)
    with pytest.raises(retention.RetentionError):
        retention.register_build(tmp_path / "registry", worktree=worktree, build_id="hazard", bundle=bundle)


def test_vendor_opencv_data_is_allowed_but_bundle_user_data_is_not(tmp_path: Path) -> None:
    worktree, bundle = write_build(tmp_path, "vendor")
    vendor = bundle / "_internal" / "cv2" / "data"
    vendor.mkdir(parents=True)
    (vendor / "haarcascade_frontalface_default.xml").write_text("synthetic vendor resource", encoding="utf-8")
    retention.register_build(tmp_path / "registry", worktree=worktree, build_id="vendor", bundle=bundle)


def test_global_build_reservations_block_a_second_checkout(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    first, _ = write_build(tmp_path, "first")
    second, _ = write_build(tmp_path, "second")
    lease = retention.acquire_lease(
        registry, worktree=first, build_id="first", reserve_bytes=6 * 1024**3,
        budget_bytes=8 * 1024**3,
    )
    with pytest.raises(retention.RetentionError, match="admission budget"):
        retention.acquire_lease(
            registry, worktree=second, build_id="second", reserve_bytes=3 * 1024**3,
            budget_bytes=8 * 1024**3,
        )
    retention.release_lease(registry, build_id="first", token=lease["token"])
    retention.acquire_lease(
        registry, worktree=second, build_id="second", reserve_bytes=3 * 1024**3,
        budget_bytes=8 * 1024**3,
    )


def test_lease_ownership_collision_and_registration_token(tmp_path: Path) -> None:
    registry = tmp_path / "registry"
    worktree, bundle = write_build(tmp_path, "leased")
    lease = retention.acquire_lease(registry, worktree=worktree, build_id="leased", pid=os.getpid())
    with pytest.raises(retention.RetentionError):
        retention.acquire_lease(registry, worktree=worktree, build_id="other", pid=os.getpid())
    with pytest.raises(retention.RetentionError):
        retention.release_lease(registry, build_id="leased", token="incorrect-token")
    with pytest.raises(retention.RetentionError):
        retention.register_build(registry, worktree=worktree, build_id="leased", bundle=bundle)
    retention.register_build(
        registry, worktree=worktree, build_id="leased", bundle=bundle, lease_token=lease["token"],
    )
    retention.release_lease(registry, build_id="leased", token=lease["token"])


def test_unreleased_lease_is_protected_regardless_of_pid_liveness(inventory) -> None:
    registry, pointer, bundles = inventory
    retention.acquire_lease(
        registry, worktree=bundles["build-1"].parents[1], build_id="unfinished", pid=2147483647,
    )
    report = plan(registry, pointer)
    assert not by_id(report)["build-1"]["eligible"]


def test_stale_or_corrupt_manifest_never_produces_candidates(inventory) -> None:
    registry, pointer, _ = inventory
    registry.write_text("{broken", encoding="utf-8")
    try:
        report = plan(registry, pointer)
    except retention.RetentionError:
        return
    assert not report["inspection_complete"]
    assert not report["candidates"]


@pytest.mark.skipif(os.name != "nt", reason="Windows junction containment")
def test_junction_or_ancestor_escape_is_rejected(tmp_path: Path) -> None:
    worktree, bundle = write_build(tmp_path, "junction")
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = bundle / "linked"
    # No deletion is performed on a junction; pytest owns all synthetic paths.
    creation = subprocess.run([
        "pwsh", "-NoProfile", "-Command",
        "New-Item -ItemType Junction -Path $env:RETENTION_JUNCTION -Target $env:RETENTION_TARGET | Out-Null",
    ], env={**os.environ, "RETENTION_JUNCTION": str(junction), "RETENTION_TARGET": str(outside)},
        capture_output=True, text=True)
    if creation.returncode:
        pytest.skip(f"Synthetic junction unavailable: {creation.stderr}")
    assert junction.exists()
    with pytest.raises(retention.RetentionError):
        retention.register_build(tmp_path / "registry", worktree=worktree, build_id="junction", bundle=bundle)


def test_apply_flag_never_deletes(inventory) -> None:
    registry, pointer, bundles = inventory
    result = subprocess.run([
        sys.executable, str(ROOT / "packaging" / "artifact_retention.py"),
        "plan", "--registry", str(registry), "--launcher-pointer", str(pointer), "--apply",
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert all((bundle / "E3.exe").exists() for bundle in bundles.values())


WRAPPER_CORE_STUB = r'''
import json, os, pathlib, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ["RETENTION_TEST_LOG"])
with log.open("a", encoding="utf-8") as out:
    out.write(json.dumps(args) + "\n")
if args[0] == "plan":
    print(json.dumps({"inspection_complete": os.environ.get("RETENTION_TEST_INCOMPLETE") != "1",
        "execution_enabled": False, "candidates": [], "reserved_logical_bytes": 0,
        "registered_logical_bytes": int(os.environ.get("RETENTION_TEST_USED_BYTES", "0"))}))
    if os.environ.get("RETENTION_TEST_INCOMPLETE") == "1":
        raise SystemExit(2)
elif args[0] == "lease":
    print(json.dumps({"token": "synthetic-lease-token"}))
else:
    print("{}")
'''


@pytest.fixture
def wrapper_fixture(tmp_path: Path):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell not installed")
    root = tmp_path / "synthetic-checkout"
    packaging = root / "packaging"
    packaging.mkdir(parents=True)
    shutil.copyfile(ROOT / "packaging" / "build_with_retention.ps1", packaging / "build_with_retention.ps1")
    (packaging / "artifact_retention.py").write_text(WRAPPER_CORE_STUB, encoding="utf-8")
    (packaging / "build_windows.ps1").write_text(
        "param($Channel, $OutputBaseFilename, $Revision, [switch]$RequireFreshOutputs, [switch]$PassThru, $RetentionRegistry)\n"
        "Set-Location (Join-Path $PSScriptRoot '..')\n"
        "if (-not $RequireFreshOutputs) { throw 'fresh mode missing' }\n"
        "Add-Content -LiteralPath $env:RETENTION_TEST_LOG -Value '\"BUILD\"'\n"
        "if ($env:RETENTION_TEST_FAIL_BUILD -eq '1') { throw 'synthetic build failure' }\n"
        "[pscustomobject]@{ Kind='E3WindowsBuild'; OutputRoot=(Get-Location).Path; "
        "Executable=(Join-Path (Get-Location) 'dist/E3/E3.exe'); "
        "Installer=(Join-Path (Get-Location) ('installer-dist/' + $OutputBaseFilename + '.exe')) }\n",
        encoding="utf-8",
    )
    log = tmp_path / "calls.jsonl"
    env = {**os.environ, "RETENTION_TEST_LOG": str(log)}
    command = [pwsh, "-NoProfile", "-File", str(packaging / "build_with_retention.ps1"),
               "-RegistryPath", str(tmp_path / "registry"), "-LauncherPointer", str(tmp_path / "pointer.json"),
               "-Python", sys.executable]
    return root, log, env, command


def read_calls(log: Path) -> list:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_wrapper_defaults_to_inventory_without_lease_or_build(wrapper_fixture) -> None:
    _, log, env, command = wrapper_fixture
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert [call[0] for call in read_calls(log)] == ["plan"]


@pytest.mark.parametrize("output", ["build/E3", "dist/E3", "installer-dist", "packaging/machine-seed"])
def test_wrapper_refuses_every_existing_destructive_builder_output(wrapper_fixture, output: str) -> None:
    root, log, env, command = wrapper_fixture
    (root / output).mkdir(parents=True)
    result = subprocess.run([*command, "-Build"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Refusing to replace existing output" in result.stderr
    assert [call[0] for call in read_calls(log)] == ["plan"]


def test_wrapper_refuses_incomplete_inspection_before_build(wrapper_fixture) -> None:
    _, log, env, command = wrapper_fixture
    env["RETENTION_TEST_INCOMPLETE"] = "1"
    result = subprocess.run([*command, "-Build"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert [call[0] for call in read_calls(log)] == ["plan"]


def test_wrapper_prints_incomplete_inventory_report(wrapper_fixture) -> None:
    _, log, env, command = wrapper_fixture
    env["RETENTION_TEST_INCOMPLETE"] = "1"
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["inspection_complete"] is False
    assert [call[0] for call in read_calls(log)] == ["plan"]


@pytest.mark.parametrize("fails", [False, True])
def test_wrapper_uses_canonical_lease_and_registers_only_success(wrapper_fixture, fails: bool) -> None:
    _, log, env, command = wrapper_fixture
    if fails:
        env["RETENTION_TEST_FAIL_BUILD"] = "1"
    result = subprocess.run([*command, "-Build", "-BuildId", "fixture-build"], env=env,
                            capture_output=True, text=True)
    calls = read_calls(log)
    actions = [call if isinstance(call, str) else call[0] for call in calls]
    if fails:
        assert result.returncode != 0
        assert actions == ["plan", "plan", "BUILD"]
    else:
        assert result.returncode == 0, result.stderr
        assert actions == ["plan", "plan", "BUILD", "register", "plan"]
    assert "lease" not in actions and "release" not in actions
    if not fails:
        registration = next(call for call in calls if isinstance(call, list) and call[0] == "register")
        assert registration[registration.index("--installer") + 1].endswith("E3-Setup.exe")
        assert "--lease-token" not in registration


def test_wrapper_budget_blocks_before_acquiring_lease(wrapper_fixture) -> None:
    _, log, env, command = wrapper_fixture
    env["RETENTION_TEST_USED_BYTES"] = str(7 * 1024**3)
    result = subprocess.run([*command, "-Build"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "admission budget" in result.stderr
    assert [call[0] for call in read_calls(log)] == ["plan"]


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity"])
def test_wrapper_rejects_invalid_budget(wrapper_fixture, value: str) -> None:
    _, log, env, command = wrapper_fixture
    result = subprocess.run([*command, "-Build", "-BudgetGiB", value], env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert not log.exists()


def test_wrapper_relative_paths_keep_identity_after_builder_changes_cwd(wrapper_fixture, tmp_path: Path) -> None:
    _, log, env, command = wrapper_fixture
    command[command.index("-RegistryPath") + 1] = "relative-registry.json"
    command[command.index("-LauncherPointer") + 1] = "relative-pointer.json"
    result = subprocess.run([*command, "-Build"], env=env, cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [call for call in read_calls(log) if isinstance(call, list)]
    assert {call[call.index("--registry") + 1] for call in calls} == {str(tmp_path / "relative-registry.json")}


@pytest.mark.parametrize("relative", ["build/E3", "dist/E3", "installer-dist", "build-info.json", "E3.spec"])
def test_canonical_fresh_mode_stops_before_dependency_install(tmp_path: Path, relative: str) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell not installed")
    root = tmp_path / "fresh-build"
    (root / "packaging").mkdir(parents=True)
    builder = root / "packaging" / "build_windows.ps1"
    shutil.copyfile(ROOT / "packaging" / "build_windows.ps1", builder)
    shutil.copyfile(ROOT / "packaging" / "build_storage_gate.ps1", root / "packaging" / "build_storage_gate.ps1")
    (root / "packaging" / "build_storage_guard.py").write_text(
        "import json, sys\n"
        "root = sys.argv[sys.argv.index('--worktree') + 1]\n"
        "print(json.dumps({'source_root': root, 'output_root': root}))\n",
        encoding="utf-8",
    )
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("preserve this synthetic output", encoding="utf-8")
    result = subprocess.run([pwsh, "-NoProfile", "-File", str(builder), "-RequireFreshOutputs"],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "refuses to replace" in result.stderr
    assert target.read_text(encoding="utf-8") == "preserve this synthetic output"


def test_powershell_files_parse_without_execution(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell not installed")
    script = tmp_path / "parse.ps1"
    script.write_text(
        "param([string]$Target)\n"
        "$tokens = $null; $errors = $null\n"
        "$null = [System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }\n",
        encoding="utf-8",
    )
    for filename in ["build_with_retention.ps1", "build_windows.ps1"]:
        result = subprocess.run([pwsh, "-NoProfile", "-File", str(script),
                                 "-Target", str(ROOT / "packaging" / filename)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
