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
import runpy
import sys

args = sys.argv[1:]
observe = runpy.run_path(os.environ["STORAGE_TEST_ENV_STUB"])["observe"]
observe("guard-" + args[0], args, managed=args[0] == "complete")
with pathlib.Path(os.environ["STORAGE_TEST_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(["storage", *args]) + "\n")
if args[0] == "begin" and os.environ.get("STORAGE_TEST_ADMIT") == "1":
    build_id = args[args.index("--build-id") + 1]
    source_root = pathlib.Path(args[args.index("--worktree") + 1])
    output_root = pathlib.Path(os.environ["STORAGE_TEST_OUTPUT"])
    if os.environ.get("STORAGE_TEST_RETURN_SOURCE") == "1":
        output_root = source_root
    else:
        output_root.mkdir(parents=True, exist_ok=False)
    print(json.dumps({"state": os.environ["STORAGE_TEST_STATE"], "token": "synthetic-token",
        "build_id": build_id, "measured_bytes": 0, "ceiling_bytes": 8 * 1024**3,
        "source_root": str(source_root), "output_root": str(output_root)}))
    raise SystemExit(0)
if args[0] == "verify" and os.environ.get("STORAGE_TEST_VERIFY_DENY") != "1":
    print("{}")
    raise SystemExit(0)
if args[0] == "complete" and os.environ.get("STORAGE_TEST_FULL_BUILD") == "1":
    print("{}")
    raise SystemExit(0)
print("synthetic storage admission denied", file=sys.stderr)
raise SystemExit(2)
'''

WINDOWS_GUARD_STUB = r'''
import json
import os
import pathlib
import runpy
import sys

runpy.run_path(os.environ["STORAGE_TEST_ENV_STUB"])["observe"](
    "windows-guard", sys.argv[1:], managed="sanitize-path" not in sys.argv,
)
with pathlib.Path(os.environ["STORAGE_TEST_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(["windows", *sys.argv[1:]]) + "\n")
if os.environ.get("STORAGE_TEST_FAIL_SANITIZER") == "1":
    print("synthetic sanitizer failure", file=sys.stderr)
    raise SystemExit(19)
if "sanitize-path" in sys.argv:
    print(os.environ["PATH"])
raise SystemExit(0)
'''

PYTHON_SHIM = r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
if ($Arguments.Count -ge 2 -and $Arguments[0] -eq '-c' -and $Arguments[1] -match 'find_spec') {
    & $env:STORAGE_TEST_PYTHON $env:STORAGE_TEST_ENV_STUB preflight
    if ($LASTEXITCODE -ne 0) { throw 'Unsafe prerequisite-check environment' }
    $line = ConvertTo-Json -InputObject @('preflight', $Arguments) -Compress -Depth 5
    Add-Content -LiteralPath $env:STORAGE_TEST_LOG -Value $line
    $global:LASTEXITCODE = if ($env:STORAGE_TEST_FAIL_PREFLIGHT -eq '1') { 23 } else { 0 }
    return
}
$allowed = @('build_storage_guard.py', 'windows_build_guard.py')
if ($Arguments.Count -gt 0 -and [IO.Path]::GetFileName($Arguments[0]) -in $allowed) {
    & $env:STORAGE_TEST_PYTHON @Arguments
    $code = $LASTEXITCODE
    if ($env:STORAGE_TEST_OUTPUT_FILE -and [IO.File]::Exists($env:STORAGE_TEST_OUTPUT_FILE)) {
        $env:STORAGE_TEST_OUTPUT = [IO.File]::ReadAllText($env:STORAGE_TEST_OUTPUT_FILE).Trim()
    }
    $global:LASTEXITCODE = $code
    return
}
if ($env:STORAGE_TEST_FULL_BUILD -eq '1' -and (
    ($Arguments[0] -eq '-m' -and $Arguments[1] -in @('pip', 'PyInstaller')) -or
    ($Arguments[0] -eq '-c') -or
    ([IO.Path]::GetFileName($Arguments[0]) -in @('version_for_build.py', 'write_build_info.py'))
)) {
    & $env:STORAGE_TEST_PYTHON $env:STORAGE_TEST_PAYLOAD_STUB @Arguments
    $global:LASTEXITCODE = $LASTEXITCODE
    return
}
if ($env:STORAGE_TEST_AFTER_DEPENDENCIES -eq '1' -and (
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

PAYLOAD_STUB = r'''
import json
import os
import pathlib
import runpy
import sys

args = sys.argv[1:]
runpy.run_path(os.environ["STORAGE_TEST_ENV_STUB"])["observe"]("payload", args)
with pathlib.Path(os.environ["STORAGE_TEST_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(["payload", *args]) + "\n")
root = pathlib.Path(os.environ["STORAGE_TEST_OUTPUT"]).resolve()

def option(name, default=None):
    return args[args.index(name) + 1] if name in args else default

def output(path, payload):
    path = pathlib.Path(path).resolve()
    if not path.is_relative_to(root):
        raise SystemExit("Synthetic builder refuses write outside leased output root: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)

if pathlib.Path(args[0]).name == "version_for_build.py":
    print("0.1.0")
elif pathlib.Path(args[0]).name == "write_build_info.py":
    output(option("--output"), json.dumps({"version": "0.1.0", "revision": option("--revision"),
        "channel": option("--channel"), "platform_key": "windows-x86_64"}).encode())
elif args[:2] == ["-m", "PyInstaller"]:
    if os.environ.get("STORAGE_TEST_FAIL_PYINSTALLER") == "1":
        raise SystemExit("synthetic PyInstaller failure")
    name = option("--name")
    dist = pathlib.Path(option("--distpath", "dist"))
    work = pathlib.Path(option("--workpath", "build"))
    spec = pathlib.Path(option("--specpath", "."))
    if "--onefile" in args:
        output(dist / (name + ".exe"), b"synthetic launcher")
    else:
        output(dist / name / (name + ".exe"), b"synthetic application")
        output(dist / name / "_internal" / "runtime.dll", b"synthetic bundled runtime")
    output(work / name / "Analysis-00.toc", b"synthetic PyInstaller analysis")
    output(spec / (name + ".spec"), b"synthetic PyInstaller specification")
elif args[:2] == ["-m", "pip"]:
    raise SystemExit("Local entrypoints must not install or upgrade dependencies")
elif args[0] == "-c":
    if os.environ.get("STORAGE_TEST_FAIL_DEPENDENCY") == "1":
        raise SystemExit("synthetic dependency unavailable")
else:
    raise SystemExit("Unexpected synthetic Python invocation")
'''

COMPILER_STUB = r'''
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
& $env:STORAGE_TEST_PYTHON $env:STORAGE_TEST_ENV_STUB compiler
if ($LASTEXITCODE -ne 0) { throw 'Synthetic compiler environment escaped managed storage' }
$line = ConvertTo-Json -InputObject @('compiler', $Arguments) -Compress -Depth 5
Add-Content -LiteralPath $env:STORAGE_TEST_LOG -Value $line
if ($env:STORAGE_TEST_FAIL_COMPILER -eq '1') {
    $global:LASTEXITCODE = 31
    return
}
$defines = @{}
foreach ($argument in $Arguments) {
    if ($argument.StartsWith('/D') -and $argument.Contains('=')) {
        $pieces = $argument.Substring(2).Split('=', 2)
        $defines[$pieces[0]] = $pieces[1]
    }
}
foreach ($key in @('E3BundleRoot', 'E3SeedRoot', 'E3InstallerOutputDir')) {
    if (-not $defines.ContainsKey($key)) { throw "Missing managed compiler definition: $key" }
    $path = [IO.Path]::GetFullPath($defines[$key])
    $prefix = [IO.Path]::GetFullPath($env:STORAGE_TEST_OUTPUT) + [IO.Path]::DirectorySeparatorChar
    if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Synthetic compiler refuses output outside leased directory: $path"
    }
}
New-Item -ItemType Directory -Path $defines['E3InstallerOutputDir'] -Force | Out-Null
$installer = Join-Path $defines['E3InstallerOutputDir'] ($defines['OutputBaseFilename'] + '.exe')
[IO.File]::WriteAllText($installer, 'synthetic installer')
$global:LASTEXITCODE = 0
'''

ENVIRONMENT_STUB = r'''
import json
import os
import pathlib
import sys

PATHS = {
    "TEMP": "temp", "TMP": "temp", "TMPDIR": "temp", "PIP_CACHE_DIR": "cache/pip",
    "PYINSTALLER_CONFIG_DIR": "cache/pyinstaller", "PYTHONPYCACHEPREFIX": "cache/pycache",
    "PYTHONUSERBASE": "cache/python-user",
}

def observe(stage, args, *, managed=True):
    snapshot = {name: os.environ.get(name) for name in (*PATHS, "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE")}
    with pathlib.Path(os.environ["STORAGE_TEST_ENV_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps({"stage": stage, "args": args, "values": snapshot}) + "\n")
    if os.environ.get("STORAGE_TEST_CHECK_ENV") != "1":
        return
    assert snapshot["PYTHONDONTWRITEBYTECODE"] == "1", (stage, snapshot)
    if stage == "preflight":
        assert snapshot["PYTHONNOUSERSITE"] == "1", (stage, snapshot)
    if not managed:
        return
    assert snapshot["PYTHONNOUSERSITE"] == "1", (stage, snapshot)
    root = pathlib.Path(os.environ["STORAGE_TEST_OUTPUT"])
    for name, relative in PATHS.items():
        assert snapshot[name] is not None and pathlib.Path(snapshot[name]) == root / relative, (stage, name, snapshot)
        if name in {"TEMP", "TMP", "TMPDIR"}:
            assert pathlib.Path(snapshot[name]).is_dir(), (stage, name, "managed temporary directory missing")

if __name__ == "__main__":
    observe(sys.argv[1], sys.argv[2:], managed=sys.argv[1] != "preflight")
'''

PRODUCTION_GUARD_WRAPPER = r'''
import importlib.util
import json
import os
import pathlib
import runpy
import shutil
import sys

args = sys.argv[1:]
runpy.run_path(os.environ["STORAGE_TEST_ENV_STUB"])["observe"](
    "guard-" + args[0], args, managed=args[0] == "complete",
)
with pathlib.Path(os.environ["STORAGE_TEST_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(["storage", *args]) + "\n")
path = pathlib.Path(__file__).with_name("production_storage_guard.py")
spec = importlib.util.spec_from_file_location("synthetic_production_guard", path)
guard = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = guard
spec.loader.exec_module(guard)
guard.discover_roots = lambda root: [root]
guard.inspect_build_processes = lambda: []
guard._validate_owner = lambda pid: {pid, os.getpid()}
guard.shutil.disk_usage = lambda path: shutil._ntuple_diskusage(100 * 1024**3, 0, 100 * 1024**3)
guard.retention._launcher_target = lambda path: pathlib.Path(os.environ["STORAGE_TEST_SELECTED"])
if args[0] == "begin":
    args += ["--state", os.environ["STORAGE_TEST_STATE"],
             "--launcher-pointer", os.environ["STORAGE_TEST_POINTER"]]
result = guard.main(args)
if result == 0 and args[0] == "begin":
    state = json.loads(pathlib.Path(os.environ["STORAGE_TEST_STATE"]).read_text())
    build_id = args[args.index("--build-id") + 1]
    lease = next(value for value in state["leases"] if value["id"] == build_id)
    pathlib.Path(os.environ["STORAGE_TEST_OUTPUT_FILE"]).write_text(lease["output_root"], encoding="utf-8")
raise SystemExit(result)
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
    if ($Name -in @('winget', 'choco')) { throw 'Synthetic fixture forbids machine-wide installation' }
    Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}
function winget { throw 'Synthetic fixture forbids winget installation' }
function choco { throw 'Synthetic fixture forbids choco installation' }
if ($env:STORAGE_TEST_VENV_COMMAND) {
    Set-Item -Path ('Function:' + $env:STORAGE_TEST_VENV_COMMAND) -Value {
        param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
        & $env:STORAGE_TEST_SHIM @Arguments
        $global:LASTEXITCODE = $LASTEXITCODE
    }
}
if ($true) {
    function Test-Path {
        [CmdletBinding(DefaultParameterSetName='Path')]
        param(
            [Parameter(Position=0, ParameterSetName='Path')][string[]]$Path,
            [Parameter(Mandatory=$true, ParameterSetName='LiteralPath')][string[]]$LiteralPath,
            [Microsoft.PowerShell.Commands.TestPathType]$PathType
        )
        if ($Path.Count -eq 1 -and $Path[0] -like '*Inno Setup 6*ISCC.exe') {
            return ($Path[0] -eq $env:STORAGE_TEST_COMPILER_COMMAND -and $env:STORAGE_TEST_NO_COMPILER -ne '1')
        }
        Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
    }
    Set-Item -Path ('Function:' + $env:STORAGE_TEST_COMPILER_COMMAND) -Value {
        param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments)
        & $env:STORAGE_TEST_COMPILER @Arguments
        $global:LASTEXITCODE = $LASTEXITCODE
    }
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
$environmentNames = @('PATH', 'TEMP', 'TMP', 'TMPDIR', 'PIP_CACHE_DIR', 'PYINSTALLER_CONFIG_DIR',
    'PYTHONPYCACHEPREFIX', 'PYTHONDONTWRITEBYTECODE', 'PYTHONUSERBASE', 'PYTHONNOUSERSITE')
$before = @{}
foreach ($name in $environmentNames) { $before[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $result = & $Target @builderArguments
    if ($env:STORAGE_TEST_RESULT) {
        ConvertTo-Json -InputObject $result -Depth 10 | Set-Content -LiteralPath $env:STORAGE_TEST_RESULT
    }
} finally {
    $after = @{}
    foreach ($name in $environmentNames) { $after[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    ConvertTo-Json -InputObject @{ before = $before; after = $after } -Depth 5 |
        Set-Content -LiteralPath $env:STORAGE_TEST_RESTORATION
}
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
    (packaging / "e3-dev-test.ico").write_bytes(b"synthetic icon")
    (root / "config").mkdir()
    (root / "config" / "default.json").write_text('{"simulation":true}', encoding="utf-8")
    harness = tmp_path / "invoke-synthetic.ps1"
    harness.write_text(HARNESS, encoding="utf-8")
    shim = tmp_path / "python-shim.ps1"
    shim.write_text(PYTHON_SHIM, encoding="utf-8")
    payload = tmp_path / "payload-stub.py"
    payload.write_text(PAYLOAD_STUB, encoding="utf-8")
    compiler = tmp_path / "compiler-stub.ps1"
    compiler.write_text(COMPILER_STUB, encoding="utf-8")
    environment_stub = tmp_path / "environment-stub.py"
    environment_stub.write_text(ENVIRONMENT_STUB, encoding="utf-8")
    log = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "STORAGE_TEST_LOG": str(log),
        "STORAGE_TEST_STATE": str(tmp_path / "private-storage-state.json"),
        "STORAGE_TEST_PYTHON": sys.executable,
        "STORAGE_TEST_SHIM": str(shim),
        "STORAGE_TEST_PAYLOAD_STUB": str(payload),
        "STORAGE_TEST_COMPILER": str(compiler),
        "STORAGE_TEST_COMPILER_COMMAND": str(tmp_path / "programs" / "Inno Setup 6" / "ISCC.exe"),
        "STORAGE_TEST_OUTPUT": str(root / ".e3-builds" / "synthetic-build"),
        "STORAGE_TEST_ENV_STUB": str(environment_stub),
        "STORAGE_TEST_ENV_LOG": str(tmp_path / "environment.jsonl"),
        "STORAGE_TEST_RESTORATION": str(tmp_path / "restoration.json"),
        "STORAGE_TEST_CHECK_ENV": "1",
        "ProgramFiles(x86)": str(tmp_path / "programs"),
    }
    # All inherited cache variables point to synthetic caller locations, never user caches.
    for name in ("TEMP", "TMP", "TMPDIR", "PIP_CACHE_DIR", "PYINSTALLER_CONFIG_DIR",
                 "PYTHONPYCACHEPREFIX", "PYTHONUSERBASE"):
        caller_path = tmp_path / "caller" / name.lower()
        caller_path.mkdir(parents=True)
        env[name] = str(caller_path)
    env["PYTHONDONTWRITEBYTECODE"] = "caller-value"
    env["PYTHONNOUSERSITE"] = "caller-user-site"

    def run(script: str = "build_windows.ps1", *arguments: str):
        env["STORAGE_TEST_ARGUMENTS"] = json.dumps(list(arguments))
        result = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(harness), "-Target", str(packaging / script)],
            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        restoration = json.loads(Path(env["STORAGE_TEST_RESTORATION"]).read_text(encoding="utf-8-sig"))
        assert restoration["after"] == restoration["before"], restoration
        return result

    return root, log, env, run


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8-sig").splitlines()]


def begin_arguments(log: Path) -> list[str]:
    recorded = [call for call in calls(log) if call[0] != "preflight" and call[:2] != ["windows", "sanitize-path"]]
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
    assert "--managed-outputs" in args


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
    enable_synthetic_managed_build(env)
    env["STORAGE_TEST_FAIL_DEPENDENCY"] = "1"
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0
    assert "synthetic dependency unavailable" in result.stderr
    recorded = calls(log)
    assert [call[1] for call in recorded if call[0] == "storage"] == ["begin", "verify"]
    assert not any("complete" in call or "release" in call for call in recorded)


def test_live_recheck_blocks_before_dependencies_or_replacement(entrypoint_fixture) -> None:
    root, log, env, run = entrypoint_fixture
    env.update(STORAGE_TEST_ADMIT="1", STORAGE_TEST_VERIFY_DENY="1")
    target = root / "dist" / "E3" / "old.exe"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"preserve until recheck succeeds")
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0
    assert "synthetic storage admission denied" in result.stderr
    recorded = calls(log)
    assert [call[1] for call in recorded if call[0] == "storage"] == ["begin", "verify"]
    assert not any(call[0] in {"synthetic-python", "payload"} for call in recorded)
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


def enable_synthetic_managed_build(env: dict[str, str]) -> Path:
    env.update(
        STORAGE_TEST_ADMIT="1", STORAGE_TEST_AFTER_DEPENDENCIES="1",
        STORAGE_TEST_FULL_BUILD="1", STORAGE_TEST_MANAGED="1", GITHUB_SHA="abcdef",
    )
    return Path(env["STORAGE_TEST_OUTPUT"])


def preserve_legacy_files(root: Path) -> dict[Path, bytes]:
    payloads = {
        "dist/E3/E3.exe": b"known-good old application",
        "dist/E3/_internal/config/network-local.json": b"old local private config",
        "dist/E3/_internal/data/calibration/profile.json": b"old calibration data",
        "dist/E3/config/default.json": b"old bundled config",
        "build/E3/evidence.txt": b"old build evidence",
        "installer-dist/E3-Setup.exe": b"old installer",
        "installer-dist/E3-Home-Setup.exe": b"old private installer",
        "launcher-dist/E3 DEV TEST.exe": b"old launcher",
        "packaging/machine-seed/secrets/bridge-token.txt": b"old private seed",
        "build-info.json": b"old build metadata",
        "E3.spec": b"old build spec",
        "data/operator-project.e3laser": b"operator project",
    }
    for relative, payload in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    selected = root.parent / "selected-launcher"
    selected.mkdir()
    (selected / "current-feature.json").write_text(
        json.dumps({"target_exe": str(root / "dist" / "E3" / "E3.exe")}), encoding="utf-8",
    )
    (selected / "E3 DEV TEST.exe").write_bytes(b"permanent selected launcher")
    # Every existing source/legacy/launcher file must survive byte for byte.
    return {
        path: path.read_bytes()
        for base in (root, selected)
        for path in base.rglob("*") if path.is_file()
    }


def assert_old_files_unchanged(snapshot: dict[Path, bytes], root: Path, output: Path) -> None:
    assert {path: path.read_bytes() for path in snapshot} == snapshot
    created = {path for path in root.rglob("*") if path.is_file()} - snapshot.keys()
    assert created
    assert all(path.is_relative_to(output) for path in created), created


def compiler_defines(log: Path) -> dict[str, str]:
    invocation = next(call for call in calls(log) if call[0] == "compiler")
    return dict(argument[2:].split("=", 1) for argument in invocation[1] if argument.startswith("/D"))


@pytest.mark.parametrize("legacy", [False, True])
def test_managed_windows_success_preserves_legacy_files_and_returns_exact_paths(entrypoint_fixture, legacy: bool) -> None:
    root, log, env, run = entrypoint_fixture
    output = enable_synthetic_managed_build(env)
    snapshot = preserve_legacy_files(root) if legacy else {
        path: path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    result_file = root.parent / "build-result.json"
    env["STORAGE_TEST_RESULT"] = str(result_file)
    result = run("build_windows.ps1", "-Revision", "abcdef", "-PassThru")
    assert result.returncode == 0, result.stdout + result.stderr
    assert_old_files_unchanged(snapshot, root, output)
    results = json.loads(result_file.read_text(encoding="utf-8-sig"))
    if not isinstance(results, list):
        results = [results]
    builds = [value for value in results if isinstance(value, dict) and value.get("Kind") == "E3WindowsBuild"]
    assert len(builds) == 1, results
    reported = builds[0]
    assert Path(reported["OutputRoot"]) == output
    assert Path(reported["Executable"]) == output / "dist" / "E3" / "E3.exe"
    assert Path(reported["Installer"]) == output / "installer-dist" / "E3-Setup.exe"
    assert reported["Version"] == "0.1.0"
    assert Path(reported["Executable"]).read_bytes() == b"synthetic application"
    assert Path(reported["Installer"]).read_text() == "synthetic installer"
    assert (output / "dist" / "E3" / "config" / "default.json").read_bytes() == (
        root / "config" / "default.json"
    ).read_bytes()
    recorded = calls(log)
    assert [call[1] for call in recorded if call[0] == "storage"] == ["begin", "verify", "complete"]
    assert recorded[-1][:2] == ["storage", "complete"]
    assert not any(call[0] == "unexpected-python" for call in recorded)
    assert Path(compiler_defines(log)["E3BundleRoot"]) == output / "dist" / "E3"
    assert not any(call[0] == "payload" and call[1:3] == ["-m", "pip"] for call in recorded)


@pytest.mark.parametrize("via_home_wrapper", [False, True])
def test_managed_private_success_preserves_state_and_keeps_credentials_out_of_guard(
    entrypoint_fixture, via_home_wrapper: bool,
) -> None:
    root, log, env, run = entrypoint_fixture
    output = enable_synthetic_managed_build(env)
    snapshot = preserve_legacy_files(root)
    state = root.parent / "synthetic-user-state"
    config = state / "config" / "network-local.json"
    profile = state / "data" / "calibration_profiles" / "1920x1080-manual-focus-010"
    registry = state / "data" / "machines.json"
    token = state / "secrets" / "bridge-token.txt"
    secret = "synthetic-private-credential-never-send-to-guard"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "app": {"data_dir": "../private-data"},
        "camera": {"width": 1920, "height": 1080, "controls": {"focus_absolute": 10}},
    }), encoding="utf-8")
    profile.mkdir(parents=True)
    (profile / "profile.json").write_text('{"calibration":"synthetic"}', encoding="utf-8")
    registry.write_text('{"machines":["synthetic"]}', encoding="utf-8")
    token.parent.mkdir(parents=True)
    token.write_text(secret, encoding="utf-8")
    state_before = {path: path.read_bytes() for path in state.rglob("*") if path.is_file()}
    if via_home_wrapper:
        result = run("build_home_installer.ps1", "-StateRoot", str(state))
    else:
        result = run(
            "build_windows.ps1", "-Revision", "abcdef", "-MachineSeed", "-ConfigPath", str(config),
            "-CalibrationProfile", str(profile), "-MachineRegistryPath", str(registry),
            "-BridgeToken", secret, "-OutputBaseFilename", "E3-Home-Setup",
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_old_files_unchanged(snapshot, root, output)
    assert {path: path.read_bytes() for path in state_before} == state_before
    definitions = compiler_defines(log)
    seed = Path(definitions["E3SeedRoot"])
    assert seed.is_relative_to(output)
    assert (seed / "secrets" / "bridge-token.txt").read_text(encoding="utf-8") == secret
    assert (seed / "data" / "machines.json").read_bytes() == registry.read_bytes()
    assert (seed / "data" / "calibration_profiles" / profile.name / "profile.json").read_bytes() == (
        profile / "profile.json"
    ).read_bytes()
    guard_calls = [call for call in calls(log) if call[0] == "storage"]
    assert [call[1] for call in guard_calls] == ["begin", "verify", "complete"]
    for value in (secret, str(config), str(profile), str(registry)):
        assert value not in json.dumps(guard_calls)
    assert secret not in result.stdout + result.stderr
    assert str(output / "installer-dist" / "E3-Home-Setup.exe") in result.stdout


def test_managed_launcher_success_preserves_selected_launcher_and_old_outputs(entrypoint_fixture) -> None:
    root, log, env, run = entrypoint_fixture
    output = enable_synthetic_managed_build(env)
    snapshot = preserve_legacy_files(root)
    result = run("build_dev_test_launcher.ps1", "-Python", env["STORAGE_TEST_SHIM"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert_old_files_unchanged(snapshot, root, output)
    assert (output / "launcher-dist" / "E3 DEV TEST.exe").read_bytes() == b"synthetic launcher"
    assert [call[1] for call in calls(log) if call[0] == "storage"] == ["begin", "verify", "complete"]


@pytest.mark.parametrize("failed_stage", ["PYINSTALLER", "COMPILER"])
def test_managed_build_failure_keeps_lease_and_legacy_outputs(entrypoint_fixture, failed_stage: str) -> None:
    root, log, env, run = entrypoint_fixture
    output = enable_synthetic_managed_build(env)
    snapshot = preserve_legacy_files(root)
    env[f"STORAGE_TEST_FAIL_{failed_stage}"] = "1"
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0
    assert_old_files_unchanged(snapshot, root, output)
    assert [call[1] for call in calls(log) if call[0] == "storage"] == ["begin", "verify"]


@pytest.mark.parametrize("script", ["build_windows.ps1", "build_dev_test_launcher.ps1"])
@pytest.mark.parametrize("failure", [None, "DEPENDENCY", "PYINSTALLER"])
@pytest.mark.parametrize("initial", [None, ""])
def test_whole_builder_restores_initially_unset_environment(
    entrypoint_fixture, script: str, failure: str | None, initial: str | None,
) -> None:
    _, log, env, run = entrypoint_fixture
    enable_synthetic_managed_build(env)
    for name in ("TEMP", "TMP", "TMPDIR", "PIP_CACHE_DIR", "PYINSTALLER_CONFIG_DIR",
                 "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE", "PYTHONUSERBASE", "PYTHONNOUSERSITE"):
        if initial is None:
            env.pop(name, None)
        else:
            env[name] = initial
    if failure:
        env[f"STORAGE_TEST_FAIL_{failure}"] = "1"
    arguments = ["-Revision", "abcdef"] if script == "build_windows.ps1" else ["-Python", env["STORAGE_TEST_SHIM"]]
    result = run(script, *arguments)
    assert (result.returncode == 0) is (failure is None), result.stdout + result.stderr
    actions = [call[1] for call in calls(log) if call[0] == "storage"]
    assert actions == (["begin", "verify", "complete"] if failure is None else ["begin", "verify"])
    observed = [json.loads(line) for line in Path(env["STORAGE_TEST_ENV_LOG"]).read_text().splitlines()]
    assert any(item["stage"] == "payload" for item in observed)
    if script == "build_windows.ps1" and failure is None:
        assert any(item["stage"] == "compiler" for item in observed)


@pytest.mark.parametrize("missing", ["PREFLIGHT", "COMPILER", "SANITIZER"])
def test_missing_installed_tools_refuse_without_lease_or_installation(entrypoint_fixture, missing: str) -> None:
    root, log, env, run = entrypoint_fixture
    enable_synthetic_managed_build(env)
    if missing == "COMPILER":
        env["STORAGE_TEST_NO_COMPILER"] = "1"
    else:
        env[f"STORAGE_TEST_FAIL_{missing}"] = "1"
    snapshot = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0
    assert all(call[0] == "preflight" or call[:2] == ["windows", "sanitize-path"] for call in calls(log))
    assert {path: path.read_bytes() for path in snapshot} == snapshot
    assert not (root / ".e3-builds").exists()
    assert "forbids machine-wide installation" not in result.stderr
    assert "forbids winget" not in result.stderr
    assert "forbids choco" not in result.stderr


@pytest.mark.parametrize("stage", ["PREFLIGHT", "SANITIZER", "BEGIN", "VERIFY"])
def test_pre_admission_failures_restore_initially_unset_python_environment(entrypoint_fixture, stage: str) -> None:
    _, _, env, run = entrypoint_fixture
    for name in ("PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE"):
        env.pop(name, None)
    if stage == "VERIFY":
        env.update(STORAGE_TEST_ADMIT="1", STORAGE_TEST_VERIFY_DENY="1")
    elif stage != "BEGIN":
        env[f"STORAGE_TEST_FAIL_{stage}"] = "1"
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0


def test_source_virtual_environment_is_only_an_input(entrypoint_fixture) -> None:
    root, log, env, run = entrypoint_fixture
    output = enable_synthetic_managed_build(env)
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"synthetic interpreter input; never execute this file")
    installed = root / ".venv" / "Lib" / "site-packages" / "installed.txt"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"pre-existing dependency input")
    env["STORAGE_TEST_VENV_COMMAND"] = str(python)
    snapshot = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode == 0, result.stdout + result.stderr
    assert_old_files_unchanged(snapshot, root, output)
    assert not any(call[0] == "payload" and call[1:3] == ["-m", "pip"] for call in calls(log))


def test_guard_cannot_return_source_root_for_normal_build(entrypoint_fixture) -> None:
    root, log, env, run = entrypoint_fixture
    enable_synthetic_managed_build(env)
    env["STORAGE_TEST_RETURN_SOURCE"] = "1"
    snapshot = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = run("build_windows.ps1", "-Revision", "abcdef")
    assert result.returncode != 0
    assert [call[1] for call in calls(log) if call[0] == "storage"] == ["begin"]
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == snapshot


def test_canonical_windows_flow_completes_with_production_schema_two_guard(entrypoint_fixture) -> None:
    root, log, env, run = entrypoint_fixture
    enable_synthetic_managed_build(env)
    packaging = root / "packaging"
    shutil.copyfile(ROOT / "packaging" / "build_storage_guard.py", packaging / "production_storage_guard.py")
    shutil.copyfile(ROOT / "packaging" / "artifact_retention.py", packaging / "artifact_retention.py")
    (packaging / "build_storage_guard.py").write_text(PRODUCTION_GUARD_WRAPPER, encoding="utf-8")
    selected = root.parent / "guard-selected.exe"
    selected.write_bytes(b"synthetic selected executable outside build root")
    pointer = root.parent / "guard-pointer.json"
    pointer.write_text(json.dumps({"exe": str(selected)}), encoding="utf-8")
    output_file = root.parent / "guard-output.txt"
    env.update(
        STORAGE_TEST_SELECTED=str(selected), STORAGE_TEST_POINTER=str(pointer),
        STORAGE_TEST_OUTPUT_FILE=str(output_file),
    )
    snapshot = preserve_legacy_files(root)
    result = run("build_windows.ps1", "-Revision", "abcdef", "-PassThru")
    assert result.returncode == 0, result.stdout + result.stderr
    output = Path(output_file.read_text(encoding="utf-8"))
    assert output.parent == root / ".e3-builds"
    assert output != root
    assert_old_files_unchanged(snapshot, root, output)
    state = json.loads(Path(env["STORAGE_TEST_STATE"]).read_text(encoding="utf-8"))
    assert state["schema_version"] == 2
    assert state["accounting_policy"] == "managed_outputs_v2"
    assert state["leases"] == []
    owned = {Path(item["snapshot"]["path"]): item for item in state["owned_outputs"]}
    assert output / "cache" in owned
    assert output / "temp" in owned
    assert output / "dist" / "E3" in owned
    assert output / "installer-dist" / "E3-Setup.exe" in owned
    assert all(path.is_relative_to(output) and item["snapshot"]["present"] for path, item in owned.items())
    assert [call[1] for call in calls(log) if call[0] == "storage"] == ["begin", "verify", "complete"]
    assert selected.read_bytes() == b"synthetic selected executable outside build root"


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
