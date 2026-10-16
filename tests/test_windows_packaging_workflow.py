"""Verify packaging CI boundaries and dependency trust before executable launch."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PINNED_HASH = "0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f"

HARNESS = r"""
$ErrorActionPreference = 'Stop'
function Record([string]$Stage, $Value) {
    @{stage=$Stage; value=$Value} | ConvertTo-Json -Compress -Depth 5 |
        Add-Content -LiteralPath $env:E3_INSTALL_TEST_LOG
}
function Invoke-WebRequest {
    param($Uri, $OutFile)
    Record 'download' $Uri
    [IO.File]::WriteAllText($OutFile, 'inert fake installer')
}
function Get-FileHash {
    param($LiteralPath, $Algorithm)
    Record 'hash' $Algorithm
    @{Hash=$env:E3_INSTALL_TEST_HASH}
}
function Get-AuthenticodeSignature {
    param($LiteralPath)
    Record 'signature' $LiteralPath
    $certificate = [pscustomobject]@{}
    $certificate | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value {
        param($Type, $Issuer)
        $env:E3_INSTALL_TEST_SIGNER
    }
    @{Status=$env:E3_INSTALL_TEST_SIGNATURE; SignerCertificate=$certificate}
}
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru,
        $RedirectStandardOutput, $RedirectStandardError)
    if ($ArgumentList -eq '--version') {
        Record 'version' @{path=$FilePath; window=$WindowStyle}
        [IO.File]::WriteAllText($RedirectStandardOutput, $env:E3_INSTALL_TEST_VERSION)
        [IO.File]::WriteAllText($RedirectStandardError, '')
        $process = [pscustomobject]@{ExitCode=0}
        $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
            param($Milliseconds)
            Record 'version-wait' $Milliseconds
            $env:E3_INSTALL_TEST_TIMEOUT -ne '1'
        }
        $process | Add-Member -MemberType ScriptMethod -Name Kill -Value { Record 'kill' 'own compiler probe' }
        $process | Add-Member -MemberType ScriptMethod -Name Dispose -Value { }
        return $process
    }
    Record 'launch' @{path=$FilePath; arguments=$ArgumentList; window=$WindowStyle}
    $compilerRoot = Join-Path $env:E3_INSTALL_TEST_DESTINATION 'compiler'
    New-Item -ItemType Directory -Path $compilerRoot | Out-Null
    [IO.File]::WriteAllText((Join-Path $compilerRoot 'ISCC.exe'), 'inert fake compiler')
    @{ExitCode=0}
}
try {
    $arguments = @{Destination=$env:E3_INSTALL_TEST_DESTINATION}
    if ($env:E3_INSTALL_TEST_ALLOW_LOCAL -eq '1') { $arguments.AllowLocal=$true }
    $compiler = & $env:E3_INSTALL_TEST_SCRIPT @arguments
    Record 'result' @{compiler=$compiler; environment=$env:E3_INNO_COMPILER}
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
"""


def test_packaging_workflow_builds_without_publishing_or_running_the_installer() -> None:
    workflow = (ROOT / ".github/workflows/windows-packaging.yml").read_text()
    publisher = (ROOT / ".github/workflows/publish-development-update.yml").read_text()
    assert "pull_request:" in workflow and "workflow_dispatch:" in workflow
    assert "pull_request_target:" not in workflow
    assert "contents: read" in workflow and "contents: write" not in workflow
    assert "persist-credentials: false" in workflow
    for required in (
        "packaging/**",
        "pyproject.toml",
        "requirements*.txt",
        "tests/test_windows_installer_long_paths.py",
        "E3_TEST_INNO_COMPILER=$compiler",
        "-EphemeralCI",
        "-PassThru",
        "steps.package.outputs.installer",
        "steps.package.outputs.metadata",
        "retention-days: 7",
    ):
        assert required in workflow
    assert "publish_development_update.py" not in workflow
    assert "Start-Process" not in workflow
    assert "Set-Location" not in workflow  # Use the exact hosted checkout/workspace.
    for text in (workflow, publisher):
        assert r".\packaging\install_inno_setup_ci.ps1" in text
        assert "-InnoCompiler $env:E3_INNO_COMPILER" in text
        assert "choco install innosetup" not in text


@pytest.fixture
def helper_runner(tmp_path: Path):
    if os.name != "nt":
        pytest.skip("PowerShell dependency preparation is Windows-only")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    harness = tmp_path / "helper-harness.ps1"
    harness.write_text(HARNESS, encoding="utf-8")
    destination = tmp_path / "new dependency"
    log = tmp_path / "events.jsonl"

    def run(**overrides: str):
        environment = dict(os.environ)
        environment.update(
            GITHUB_ACTIONS="",
            RUNNER_ENVIRONMENT="",
            GITHUB_WORKSPACE="",
            E3_INNO_COMPILER="",
            E3_INSTALL_TEST_LOG=str(log),
            E3_INSTALL_TEST_DESTINATION=str(destination),
            E3_INSTALL_TEST_SCRIPT=str(ROOT / "packaging/install_inno_setup_ci.ps1"),
            E3_INSTALL_TEST_ALLOW_LOCAL="1",
            E3_INSTALL_TEST_HASH=PINNED_HASH,
            E3_INSTALL_TEST_SIGNER="Pyrsys B.V.",
            E3_INSTALL_TEST_SIGNATURE="Valid",
            E3_INSTALL_TEST_VERSION="7.1.0",
            E3_INSTALL_TEST_TIMEOUT="",
        )
        environment.update(overrides)
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            env=environment,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        events = [json.loads(line) for line in log.read_text(encoding="utf-8-sig").splitlines()] if log.exists() else []
        return result, events, destination

    return run


@pytest.mark.parametrize(
    "overrides, message, stages",
    [
        ({"E3_INSTALL_TEST_HASH": "0" * 64}, "SHA-256", ["download", "hash"]),
        (
            {"E3_INSTALL_TEST_SIGNATURE": "NotTrusted"},
            "signature",
            ["download", "hash", "signature"],
        ),
        (
            {"E3_INSTALL_TEST_SIGNER": "Unrelated Publisher"},
            "signature",
            ["download", "hash", "signature"],
        ),
    ],
)
def test_dependency_rejects_untrusted_binary_before_launch(helper_runner, overrides, message, stages) -> None:
    result, events, destination = helper_runner(**overrides)
    assert result.returncode != 0 and message in result.stdout
    assert [event["stage"] for event in events] == stages
    assert not (destination / "compiler").exists()


def test_dependency_requires_explicit_local_preparation(helper_runner) -> None:
    result, events, destination = helper_runner(E3_INSTALL_TEST_ALLOW_LOCAL="0")
    assert result.returncode != 0 and "-AllowLocal" in result.stdout
    assert events == [] and not destination.exists()


def test_dependency_refuses_existing_destination_without_overwriting(helper_runner, tmp_path: Path) -> None:
    destination = tmp_path / "new dependency"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep exactly", encoding="utf-8")
    result, events, _ = helper_runner()
    assert result.returncode != 0 and "already exists" in result.stdout
    assert events == [] and sentinel.read_text() == "keep exactly"
    assert list(destination.iterdir()) == [sentinel]


def test_dependency_refuses_drive_relative_destination(helper_runner) -> None:
    result, events, _ = helper_runner(E3_INSTALL_TEST_DESTINATION="C:relative-folder")
    assert result.returncode != 0 and "absolute path" in result.stdout
    assert events == []


def test_verified_dependency_uses_portable_hidden_install_and_exports_compiler(helper_runner, tmp_path: Path) -> None:
    github_env = tmp_path / "github-env.txt"
    result, events, destination = helper_runner(GITHUB_ENV=str(github_env))
    assert result.returncode == 0, result.stdout + result.stderr
    assert [event["stage"] for event in events] == [
        "download", "hash", "signature", "launch", "signature", "version", "version-wait", "result"
    ]
    launch = next(event["value"] for event in events if event["stage"] == "launch")
    assert launch["window"] == "Hidden"
    assert "/PORTABLE=1" in launch["arguments"]
    assert "/CURRENTUSER" in launch["arguments"]
    compiler = str(destination / "compiler" / "ISCC.exe")
    assert events[-1]["value"] == {"compiler": compiler, "environment": compiler}
    assert not github_env.exists()  # Local use cannot write hosted runner environment.


def test_dependency_rejects_extracted_compiler_version_before_export(helper_runner) -> None:
    result, events, _ = helper_runner(E3_INSTALL_TEST_VERSION="7.2.0")
    assert result.returncode != 0 and "Expected Inno Setup 7.1.0" in result.stdout
    assert [event["stage"] for event in events] == [
        "download", "hash", "signature", "launch", "signature", "version", "version-wait"
    ]


def test_dependency_version_probe_is_bounded_and_kills_only_its_process(helper_runner) -> None:
    result, events, _ = helper_runner(E3_INSTALL_TEST_TIMEOUT="1")
    assert result.returncode != 0 and "exceeded ten seconds" in result.stdout
    assert events[-2:] == [
        {"stage": "version-wait", "value": 10000},
        {"stage": "kill", "value": "own compiler probe"},
    ]
    assert not any(event["stage"] == "result" for event in events)
