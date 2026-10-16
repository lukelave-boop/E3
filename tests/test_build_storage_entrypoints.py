"""Exercise build admission without installing dependencies or building E3."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

GUARD_STUB = r'''
import json
import os
import pathlib
import sys

args = sys.argv[1:]
with pathlib.Path(os.environ["STORAGE_TEST_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(["storage", *args]) + "\n")
if args[0] == "begin" and os.environ.get("STORAGE_TEST_ADMIT") == "1":
    build_id = args[args.index("--build-id") + 1]
    print(json.dumps({"state": os.environ["STORAGE_TEST_STATE"], "token": "synthetic-token",
        "build_id": build_id, "measured_bytes": 0, "ceiling_bytes": 8 * 1024**3}))
    raise SystemExit(0)
print("synthetic storage admission denied", file=sys.stderr)
raise SystemExit(2)
'''

WINDOWS_GUARD_STUB = r'''
import json
import os
import pathlib
import sys

with pathlib.Path(os.environ["STORAGE_TEST_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(["windows", *sys.argv[1:]]) + "\n")
if os.environ.get("STORAGE_TEST_AFTER_DEPENDENCIES") == "1":
    print(os.environ["PATH"] if "sanitize-path" in sys.argv else "{}")
    raise SystemExit(0)
print("synthetic stop after storage admission", file=sys.stderr)
raise SystemExit(19)
'''

PYTHON_SHIM = r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
$allowed = @('build_storage_guard.py', 'windows_build_guard.py')
if ($Arguments.Count -gt 0 -and [IO.Path]::GetFileName($Arguments[0]) -in $allowed) {
    & $env:STORAGE_TEST_PYTHON @Arguments
    $global:LASTEXITCODE = $LASTEXITCODE
    return
}
if ($env:STORAGE_TEST_AFTER_DEPENDENCIES -eq '1' -and (
    ($Arguments[0] -eq '-m' -and $Arguments[1] -eq 'pip') -or
    ($Arguments[0] -eq '-c') -or
    ([IO.Path]::GetFileName($Arguments[0]) -eq 'version_for_build.py')
)) {
    $line = ConvertTo-Json -InputObject @('synthetic-python', $Arguments) -Compress -Depth 5
    Add-Content -LiteralPath $env:STORAGE_TEST_LOG -Value $line
    if ([IO.Path]::GetFileName($Arguments[0]) -eq 'version_for_build.py') { '0.1.0' }
    $global:LASTEXITCODE = 0
    return
}
$line = ConvertTo-Json -InputObject @('unexpected-python', $Arguments) -Compress -Depth 5
Add-Content -LiteralPath $env:STORAGE_TEST_LOG -Value $line
$global:LASTEXITCODE = 79
Write-Error 'Synthetic fixture refuses dependency installation and packaging'
'''

HARNESS = r'''
param([string]$Target)
$ErrorActionPreference = 'Stop'
function Get-Command {
    [CmdletBinding()]
    param([string]$Name)
    if ($Name -eq 'python') {
        return [pscustomobject]@{ Source = $env:STORAGE_TEST_SHIM }
    }
    Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}
$rawArguments = @(ConvertFrom-Json -InputObject $env:STORAGE_TEST_ARGUMENTS)
$builderArguments = @{}
for ($index = 0; $index -lt $rawArguments.Count; $index++) {
    $name = $rawArguments[$index].TrimStart('-')
    if ($index + 1 -lt $rawArguments.Count -and -not $rawArguments[$index + 1].StartsWith('-')) {
        $builderArguments[$name] = $rawArguments[++$index]
    } else {
        $builderArguments[$name] = $true
    }
}
& $Target @builderArguments
'''


@pytest.fixture
def entrypoint_fixture(tmp_path: Path):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell is required for synthetic entrypoint tests")
    root = tmp_path / "synthetic-checkout"
    packaging = root / "packaging"
    packaging.mkdir(parents=True)
    # Copy only script sources, including shared helpers; never copy runtimes.
    for source in (ROOT / "packaging").glob("*.ps1"):
        shutil.copyfile(source, packaging / source.name)
    (packaging / "build_storage_guard.py").write_text(GUARD_STUB, encoding="utf-8")
    (packaging / "windows_build_guard.py").write_text(WINDOWS_GUARD_STUB, encoding="utf-8")
    harness = tmp_path / "invoke-synthetic.ps1"
    harness.write_text(HARNESS, encoding="utf-8")
    shim = tmp_path / "python-shim.ps1"
    shim.write_text(PYTHON_SHIM, encoding="utf-8")
    log = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "STORAGE_TEST_LOG": str(log),
        "STORAGE_TEST_STATE": str(tmp_path / "private-storage-state.json"),
        "STORAGE_TEST_PYTHON": sys.executable,
        "STORAGE_TEST_SHIM": str(shim),
    }

    def run(script: str = "build_windows.ps1", *arguments: str):
        env["STORAGE_TEST_ARGUMENTS"] = json.dumps(list(arguments))
        return subprocess.run(
            [pwsh, "-NoProfile", "-File", str(harness), "-Target", str(packaging / script)],
            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )

    return root, log, env, run


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8-sig").splitlines()]


def begin_arguments(log: Path) -> list[str]:
    recorded = calls(log)
    assert len(recorded) == 1, recorded
    assert recorded[0][:2] == ["storage", "begin"]
    return recorded[0][2:]


@pytest.mark.parametrize("fresh", [False, True])
def test_normal_and_legacy_fresh_flag_cannot_skip_storage_admission(entrypoint_fixture, fresh: bool) -> None:
    root, log, _, run = entrypoint_fixture
    arguments = ["-Revision", "abcdef"]
    if fresh:
        arguments.append("-RequireFreshOutputs")
    result = run("build_windows.ps1", *arguments)
    assert result.returncode != 0
    assert "synthetic storage admission denied" in result.stderr
    args = begin_arguments(log)
    assert Path(args[args.index("--worktree") + 1]) == root
    assert args[args.index("--mode") + 1] == "windows"
    assert args[args.index("--installer-name") + 1] == "E3-Setup"
    assert int(args[args.index("--pid") + 1]) > 0
    assert args[args.index("--build-id") + 1]
    assert "--ephemeral-ci" not in args


def test_denial_preserves_normal_existing_outputs_before_any_mutation(entrypoint_fixture) -> None:
    root, log, _, run = entrypoint_fixture
    payloads = {
        "dist/E3/old.exe": b"existing bundled application",
        "build/E3/evidence.txt": b"existing build evidence",
        "installer-dist/E3-Setup.exe": b"existing installer",
        "build-info.json": b"existing metadata",
        "E3.spec": b"existing build specification",
        "packaging/machine-seed/secrets/bridge-token.txt": b"synthetic preserved seed",
    }
    for relative, payload in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0
    begin_arguments(log)
    assert {relative: (root / relative).read_bytes() for relative in payloads} == payloads


def test_admitted_then_failed_build_does_not_complete_or_release(entrypoint_fixture) -> None:
    _, log, env, run = entrypoint_fixture
    env["STORAGE_TEST_ADMIT"] = "1"
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0
    assert "synthetic stop after storage admission" in result.stderr
    recorded = calls(log)
    assert [call[:2] for call in recorded] == [["storage", "begin"], ["windows", "sanitize-path"]]
    assert not any("complete" in call or "release" in call for call in recorded)


def test_live_recheck_after_dependencies_blocks_before_replacement(entrypoint_fixture) -> None:
    root, log, env, run = entrypoint_fixture
    env.update(STORAGE_TEST_ADMIT="1", STORAGE_TEST_AFTER_DEPENDENCIES="1")
    target = root / "dist" / "E3" / "old.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"preserve until recheck succeeds")
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0
    assert "synthetic storage admission denied" in result.stderr
    recorded = calls(log)
    assert [call[1] for call in recorded if call[0] == "storage"] == ["begin", "verify"]
    assert any(call[0] == "synthetic-python" and call[1][:2] == ["-m", "pip"] for call in recorded)
    assert recorded[-1][:2] == ["storage", "verify"]
    assert not any(call[0] == "unexpected-python" for call in recorded)
    assert target.read_bytes() == b"preserve until recheck succeeds"


def test_private_build_admission_receives_no_machine_values(entrypoint_fixture) -> None:
    _, log, _, run = entrypoint_fixture
    secret = "synthetic-credential-never-pass-to-storage-cli"
    config = "synthetic-private-config.json"
    profile = "synthetic-calibration-profile"
    registry = "synthetic-machine-registry.json"
    result = run(
        "build_windows.ps1", "-Revision", "abcdef", "-MachineSeed", "-BridgeToken", secret,
        "-ConfigPath", config, "-CalibrationProfile", profile, "-MachineRegistryPath", registry,
        "-OutputBaseFilename", "E3-Home-Setup",
    )
    assert result.returncode != 0
    args = begin_arguments(log)
    assert args[args.index("--mode") + 1] == "private"
    assert args[args.index("--installer-name") + 1] == "E3-Home-Setup"
    for private_value in (secret, config, profile, registry):
        assert private_value not in json.dumps(args)
    assert secret not in result.stdout + result.stderr


@pytest.mark.parametrize("explicit", [False, True])
def test_ci_environment_does_not_implicitly_enable_ephemeral_mode(entrypoint_fixture, explicit: bool) -> None:
    _, log, env, run = entrypoint_fixture
    env.update(CI="true", GITHUB_ACTIONS="true")
    arguments = ["-Revision", "abcdef"]
    if explicit:
        arguments.append("-EphemeralCI")
    result = run("build_windows.ps1", *arguments)
    assert result.returncode != 0
    args = begin_arguments(log)
    assert ("--ephemeral-ci" in args) is explicit
    assert "synthetic storage admission denied" in result.stderr


def test_launcher_build_requires_admission_before_python_or_output_mutation(entrypoint_fixture) -> None:
    root, log, env, run = entrypoint_fixture
    target = root / "launcher-dist" / "E3 DEV TEST.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"existing launcher")
    result = run("build_dev_test_launcher.ps1", "-Python", env["STORAGE_TEST_SHIM"])
    assert result.returncode != 0
    args = begin_arguments(log)
    assert args[args.index("--mode") + 1] == "launcher"
    assert target.read_bytes() == b"existing launcher"


@pytest.mark.parametrize("name", ["build_windows.ps1", "build_dev_test_launcher.ps1", "build_home_installer.ps1"])
def test_storage_guard_entrypoints_parse_without_execution(tmp_path: Path, name: str) -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell not installed")
    parser = tmp_path / "parse.ps1"
    parser.write_text(
        "param([string]$Target)\n"
        "$tokens = $null; $errors = $null\n"
        "$null = [System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(parser), "-Target", str(ROOT / "packaging" / name)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
