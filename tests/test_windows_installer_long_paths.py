from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = ROOT / "packaging" / "E3-installer.iss"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Requires the Windows Inno Setup compiler")


def _compiler_candidates() -> list[Path]:
    roots = [
        Path(value)
        for name in ("ProgramFiles", "ProgramFiles(x86)")
        if (value := os.environ.get(name))
    ]
    if local := os.environ.get("LOCALAPPDATA"):
        roots.append(Path(local) / "Programs")
    return [
        root / folder / "ISCC.exe"
        for root in roots
        for folder in ("Inno Setup 7", "Inno Setup 7 (32-bit)")
    ]


@pytest.fixture(scope="module")
def inno_compiler() -> Path:
    explicit = os.environ.get("E3_TEST_INNO_COMPILER")
    candidates = [Path(explicit)] if explicit else _compiler_candidates()
    diagnostics = []
    for candidate in candidates:
        if not candidate.is_file():
            diagnostics.append(f"Compiler does not exist: {candidate}")
            continue
        result = subprocess.run(
            [str(candidate), "/?"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        banner = result.stdout + result.stderr
        match = re.search(r"Inno Setup\s+(\d+)(?:\D|$)", banner)
        if result.returncode == 0 and match and int(match.group(1)) >= 7:
            return candidate.resolve()
        diagnostics.append(f"Not a supported Inno Setup 7 compiler: {candidate}\n{banner}")
    if explicit:
        pytest.fail("E3_TEST_INNO_COMPILER must select Inno Setup 7 or newer.\n" + "\n".join(diagnostics))
    pytest.skip("Inno Setup 7 is not installed; set E3_TEST_INNO_COMPILER to run real compiler checks")


def _deep_directory(root: Path, minimum_length: int) -> Path:
    while len(str(root)) < minimum_length:
        component = "nested ü with spaces " + "x" * 24
        component = component[:max(1, min(len(component), minimum_length - len(str(root)) - 1))]
        root /= component.rstrip(" .") or "x"
    return root


def _payload_files(bundle: Path) -> dict[Path, bytes]:
    # One path is enumerable by MAX_PATH-limited code but cannot be opened;
    # another has a long parent, which old compilers can silently omit entirely.
    boundary_parent = _deep_directory(bundle / "leaf boundary", 215)
    assert len(str(boundary_parent)) < 260, "Use a shorter pytest base directory for the boundary fixture"
    long_leaf = boundary_parent / ("long leaf ü " + "x" * 80 + ".obj")
    long_parent = _deep_directory(bundle / "parent boundary", 300)
    files = {
        bundle / "E3.exe": b"Synthetic payload only: this file is never executed.\n",
        bundle / "short.txt": b"Short payload control.\n",
        long_leaf: b"The leaf exceeds MAX_PATH while its parent does not.\n",
        long_parent / "deep Qt object ü with spaces.cpp.obj": b"The parent also exceeds MAX_PATH.\n",
    }
    assert len(str(long_leaf)) > 260
    assert len(str(long_parent)) > 260
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return files


def _compile_repository_installer(
    compiler: Path, work: Path, bundle: Path, output: Path
) -> subprocess.CompletedProcess[str]:
    wrapper = work / "compile-repository-installer.iss"
    # Include the actual repository script, retaining its source recursion,
    # compression and install rules. Only add a compiler evidence manifest.
    wrapper.write_text(
        f'#include "{INSTALLER_SCRIPT}"\n'
        "[Setup]\n"
        "OutputManifestFile=payload-manifest.tsv\n",
        encoding="utf-8-sig",
    )
    temporary = work / "compiler-temp"
    temporary.mkdir()
    environment = dict(os.environ, TEMP=str(temporary), TMP=str(temporary))
    result = subprocess.run(
        [
            str(compiler),
            "/DMyAppVersion=0.0.1",
            "/DOutputBaseFilename=E3-Synthetic-Path-Test",
            f"/DE3BundleRoot={bundle}",
            f"/DE3InstallerOutputDir={output}",
            str(wrapper),
        ],
        cwd=work,
        env=environment,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    (work / "compiler.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    return result


def _compiler_output_path(work: Path, output: Path | str) -> Path:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell is not None, "Windows packaging requires PowerShell"
    script = work / "compiler-output-path.ps1"
    script.write_text(
        "param([string]$HelperFile, [string]$OutputPath)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        ". $HelperFile\n"
        "ConvertTo-E3InnoPath -Path $OutputPath | ConvertTo-Json -Compress\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            shell, "-NoProfile", "-NonInteractive", "-File", str(script),
            "-HelperFile", str(ROOT / "packaging" / "inno_setup.ps1"),
            "-OutputPath", str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    return Path(json.loads(result.stdout))


def _source_key(source: str) -> str:
    if source.startswith("\\\\?\\UNC\\"):
        source = "\\\\" + source[8:]
    elif source.startswith("\\\\?\\"):
        source = source[4:]
    return os.path.normcase(os.path.abspath(source))


@pytest.mark.parametrize("long_output", [False, True], ids=["normal-output", "long-output"])
def test_repository_installer_preserves_every_long_path_payload(
    inno_compiler: Path, tmp_path: Path, long_output: bool
) -> None:
    bundle = tmp_path / "payload ü with spaces"
    files = _payload_files(bundle)
    output = tmp_path / "installer output ü"
    if long_output:
        output = _deep_directory(output, 300)
        assert len(str(output)) > 260

    # Inno 7 normalizes source paths itself, but OutputDir still needs the
    # explicit extended form when it exceeds MAX_PATH. This names the same
    # physical directory; the synthetic payload is never copied or relocated.
    compiler_output = _compiler_output_path(tmp_path, output)
    result = _compile_repository_installer(inno_compiler, tmp_path, bundle, compiler_output)

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    executable = output / "E3-Synthetic-Path-Test.exe"
    assert executable.is_file() and executable.stat().st_size > 0
    manifest = output / "payload-manifest.tsv"
    rows = list(csv.DictReader(io.StringIO(manifest.read_text(encoding="utf-8-sig")), delimiter="\t"))
    actual = {_source_key(row["SourceFilename"]): row for row in rows}
    expected = {_source_key(str(path)): content for path, content in files.items()}
    assert len(rows) == len(actual) == len(expected), "Compiler omitted or duplicated payload files"
    assert actual.keys() == expected.keys()
    for source, content in expected.items():
        assert actual[source]["SHA256Sum"].casefold() == hashlib.sha256(content).hexdigest()
        assert int(actual[source]["OriginalSize"]) == len(content)
    # Compilation and manifest reads are the endpoint: never execute this setup.


def test_repository_installer_rejects_missing_payload(inno_compiler: Path, tmp_path: Path) -> None:
    output = tmp_path / "installer output"

    result = _compile_repository_installer(inno_compiler, tmp_path, tmp_path / "absent payload", output)

    assert result.returncode != 0, "A missing payload must not produce a successful empty installer"
    assert "No files found matching" in result.stdout + result.stderr
    assert not (output / "E3-Synthetic-Path-Test.exe").exists()


@pytest.mark.parametrize(
    ("normal", "extended"),
    [
        (r"C:\build folder\installer", r"\\?\C:\build folder\installer"),
        (r"\\server\share\build folder\installer", r"\\?\UNC\server\share\build folder\installer"),
        (r"\\?\C:\build folder\installer", r"\\?\C:\build folder\installer"),
    ],
    ids=["drive", "unc", "already-extended"],
)
def test_compiler_output_helper_preserves_directory_identity(
    tmp_path: Path, normal: str, extended: str
) -> None:
    assert str(_compiler_output_path(tmp_path, normal)) == extended


def test_windows_powershell51_discovers_compiler_and_preserves_error_policy(
    inno_compiler: Path, tmp_path: Path
) -> None:
    shell = shutil.which("powershell.exe")
    if shell is None:
        pytest.skip("Windows PowerShell 5.1 is not installed")
    script = tmp_path / "powershell51-compiler-discovery.ps1"
    script.write_text(
        "param([string]$HelperFile, [string]$CompilerPath)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
        ". $HelperFile\n"
        "$selected = Get-E3InnoCompiler -ExplicitPath $CompilerPath\n"
        "@{Compiler=$selected; ErrorPolicy=[string]$ErrorActionPreference; "
        "Major=$PSVersionTable.PSVersion.Major; Minor=$PSVersionTable.PSVersion.Minor} "
        "| ConvertTo-Json -Compress\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(script),
            "-HelperFile", str(ROOT / "packaging" / "inno_setup.ps1"),
            "-CompilerPath", str(inno_compiler),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    evidence = json.loads(result.stdout)
    assert (evidence["Major"], evidence["Minor"]) == (5, 1)
    assert _source_key(evidence["Compiler"]) == _source_key(str(inno_compiler))
    assert evidence["ErrorPolicy"] == "Stop"
