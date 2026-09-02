from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from laser_aligner import dev_test_launcher
from laser_aligner.dev_test_launcher import (
    BRANCH_ENVIRONMENT_VARIABLE,
    LAUNCHER_PATH_ENVIRONMENT_VARIABLE,
    REVISION_ENVIRONMENT_VARIABLE,
    DevTestLauncherError,
    FeatureBuild,
    feature_environment,
    load_feature_pointer,
    start_feature_process,
    validate_feature_object,
)
from laser_aligner.identity import (
    DEV_TEST_ENVIRONMENT_VARIABLE,
    DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE,
    DEV_TEST_VERSION_ENVIRONMENT_VARIABLE,
)

FEATURE_NAME = "Outer silhouette"
FEATURE_VERSION = "0.6.161"
FEATURE_BRANCH = "feature/trace-outer-silhouette"
FEATURE_REVISION = "0123456789abcdef0123456789abcdef01234567"


def _write_build(
    root: Path,
    directory_name: str,
    *,
    version: str = FEATURE_VERSION,
    revision: str = FEATURE_REVISION,
) -> Path:
    build_directory = root / directory_name
    build_directory.mkdir()
    executable = build_directory / "E3.exe"
    executable.write_bytes(b"MZ\x00fixture")
    (build_directory / "build-info.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "revision": revision,
                "channel": "development",
                "repository": "lukelave-boop/E3",
                "manifest_url": "https://example.invalid/update-manifest.json",
                "platform_key": "windows-x86_64",
                "packaged": True,
            }
        ),
        encoding="utf-8",
    )
    return executable


def _pointer_object(
    executable: Path,
    *,
    name: str = FEATURE_NAME,
    version: str = FEATURE_VERSION,
    branch: str = FEATURE_BRANCH,
    revision: str = FEATURE_REVISION,
) -> dict[str, str]:
    return {
        "name": name,
        "version": version,
        "branch": branch,
        "revision": revision,
        "exe": str(executable),
    }


def _write_pointer(path: Path, raw: dict[str, str]) -> None:
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_valid_pointer_binds_exact_packaged_build_metadata(tmp_path: Path) -> None:
    executable = _write_build(tmp_path, "feature-build")
    pointer = tmp_path / "current-feature.json"
    _write_pointer(pointer, _pointer_object(executable, revision=FEATURE_REVISION.upper()))

    feature = load_feature_pointer(pointer)

    assert feature == FeatureBuild(
        name=FEATURE_NAME,
        version=FEATURE_VERSION,
        branch=FEATURE_BRANCH,
        revision=FEATURE_REVISION,
        exe=executable.resolve(),
    )


def test_pointer_schema_requires_exact_fields(tmp_path: Path) -> None:
    executable = _write_build(tmp_path, "feature-build")
    raw = _pointer_object(executable)

    missing = dict(raw)
    missing.pop("branch")
    with pytest.raises(DevTestLauncherError, match=r"missing required field\(s\): branch"):
        validate_feature_object(missing)

    extra = {**raw, "fallback_exe": str(executable)}
    with pytest.raises(DevTestLauncherError, match=r"unknown field\(s\): fallback_exe"):
        validate_feature_object(extra)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", " Outer silhouette", "trimmed string"),
        ("name", "Outer\x00silhouette", "control character"),
        ("version", "release candidate", "sane E3 version"),
        ("branch", "../trace-outer-silhouette", "sane Git branch"),
        ("branch", "feature//trace-outer-silhouette", "sane Git branch"),
        ("revision", "not-a-git-sha", "hexadecimal Git revision"),
        ("revision", 161, "revision must be a string"),
    ],
)
def test_pointer_rejects_unsound_scalar_values(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    executable = _write_build(tmp_path, "feature-build")
    raw: dict[str, Any] = _pointer_object(executable)
    raw[field] = value

    with pytest.raises(DevTestLauncherError, match=message):
        validate_feature_object(raw)


def test_pointer_rejects_unbounded_relative_non_exe_missing_and_self_targets(
    tmp_path: Path,
) -> None:
    executable = _write_build(tmp_path, "feature-build")
    raw = _pointer_object(executable)

    with pytest.raises(DevTestLauncherError, match="absolute"):
        validate_feature_object({**raw, "exe": "relative.exe"})
    with pytest.raises(DevTestLauncherError, match="4096-character limit"):
        validate_feature_object({**raw, "exe": "C:\\" + "x" * 4096 + ".exe"})
    with pytest.raises(DevTestLauncherError, match=r"\.exe extension"):
        validate_feature_object({**raw, "exe": str(executable.with_suffix(".bat"))})
    with pytest.raises(DevTestLauncherError, match="does not exist"):
        validate_feature_object({**raw, "exe": str(executable.with_name("missing.exe"))})
    with pytest.raises(DevTestLauncherError, match="cannot be the launcher itself"):
        validate_feature_object(raw, launcher_path=executable)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "pointer file is empty"),
        (b"{", "not valid UTF-8 JSON"),
        (b"\xff", "not valid UTF-8 JSON"),
        (b"[]", "pointer root must be a JSON object"),
    ],
)
def test_pointer_rejects_empty_invalid_or_non_object_json(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    pointer = tmp_path / "current-feature.json"
    pointer.write_bytes(payload)

    with pytest.raises(DevTestLauncherError, match=message):
        load_feature_pointer(pointer)


def test_pointer_rejects_duplicate_json_fields_and_oversized_payload(
    tmp_path: Path,
) -> None:
    executable = _write_build(tmp_path, "feature-build")
    raw = _pointer_object(executable)
    encoded = json.dumps(raw)
    duplicated = encoded.replace(
        f'"name": "{FEATURE_NAME}"',
        f'"name": "{FEATURE_NAME}", "name": "Other feature"',
        1,
    )
    pointer = tmp_path / "current-feature.json"
    pointer.write_text(duplicated, encoding="utf-8")

    with pytest.raises(DevTestLauncherError, match="duplicate JSON field: name"):
        load_feature_pointer(pointer)

    pointer.write_bytes(b" " * 65_537)
    with pytest.raises(DevTestLauncherError, match="65536-byte limit"):
        load_feature_pointer(pointer)


def test_missing_pointer_is_a_clear_validation_failure(tmp_path: Path) -> None:
    pointer = tmp_path / "current-feature.json"

    with pytest.raises(DevTestLauncherError, match="cannot read"):
        load_feature_pointer(pointer)


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"schema_version": 2, "packaged": True}, "packaged E3 schema-1 build"),
        (
            {
                "schema_version": 1,
                "packaged": True,
                "platform_key": "linux-x86_64",
                "version": FEATURE_VERSION,
                "revision": FEATURE_REVISION,
            },
            "Windows x86-64 E3 build",
        ),
        (
            {
                "schema_version": 1,
                "packaged": True,
                "platform_key": "windows-x86_64",
                "version": "0.6.160",
                "revision": FEATURE_REVISION,
            },
            "configured version does not match",
        ),
        (
            {
                "schema_version": 1,
                "packaged": True,
                "platform_key": "windows-x86_64",
                "version": FEATURE_VERSION,
                "revision": "f" * 40,
            },
            "configured revision does not match",
        ),
    ],
)
def test_pointer_rejects_build_info_that_does_not_bind_the_target(
    tmp_path: Path,
    metadata: dict[str, object],
    message: str,
) -> None:
    executable = _write_build(tmp_path, "feature-build")
    (executable.parent / "build-info.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )

    with pytest.raises(DevTestLauncherError, match=message):
        validate_feature_object(_pointer_object(executable))


@pytest.mark.parametrize("schema_version", [True, 1.0, "1"])
def test_pointer_rejects_non_integer_build_info_schema_version(
    tmp_path: Path,
    schema_version: object,
) -> None:
    executable = _write_build(tmp_path, "feature-build")
    metadata_path = executable.parent / "build-info.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["schema_version"] = schema_version
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(DevTestLauncherError, match="packaged E3 schema-1 build"):
        validate_feature_object(_pointer_object(executable))


def test_pointer_rejects_non_string_build_info_revision(tmp_path: Path) -> None:
    revision = "1234567"
    executable = _write_build(
        tmp_path,
        "feature-build",
        revision=revision,
    )
    metadata_path = executable.parent / "build-info.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["revision"] = int(revision)
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(DevTestLauncherError, match="revision does not match"):
        validate_feature_object(_pointer_object(executable, revision=revision))


def test_pointer_rejects_missing_malformed_duplicate_and_oversized_build_info(
    tmp_path: Path,
) -> None:
    executable = _write_build(tmp_path, "feature-build")
    metadata_path = executable.parent / "build-info.json"
    raw = _pointer_object(executable)

    metadata_path.unlink()
    with pytest.raises(DevTestLauncherError, match="build metadata is unavailable"):
        validate_feature_object(raw)

    metadata_path.write_bytes(b"{")
    with pytest.raises(DevTestLauncherError, match="not valid UTF-8 JSON"):
        validate_feature_object(raw)

    metadata_path.write_text(
        '{"schema_version": 1, "schema_version": 1}', encoding="utf-8"
    )
    with pytest.raises(DevTestLauncherError, match="duplicate JSON field"):
        validate_feature_object(raw)

    metadata_path.write_bytes(b" " * 65_537)
    with pytest.raises(DevTestLauncherError, match="metadata exceeds its safe bound"):
        validate_feature_object(raw)


def test_feature_environment_is_explicit_and_does_not_mutate_the_parent(
    tmp_path: Path,
) -> None:
    executable = _write_build(tmp_path, "feature-build")
    launcher = tmp_path / "permanent" / "E3 DEV TEST.exe"
    feature = FeatureBuild(
        name=FEATURE_NAME,
        version=FEATURE_VERSION,
        branch=FEATURE_BRANCH,
        revision=FEATURE_REVISION,
        exe=executable,
    )
    source = {
        "Path": (
            '"C:\\launcher-bundle";C:\\launcher-bundle\\_internal;'
            "C:\\Windows\\System32;relative-entry"
        ),
        "KEEP": "parent-value",
        DEV_TEST_ENVIRONMENT_VARIABLE: "spoofed",
    }
    original = dict(source)

    environment = feature_environment(
        feature,
        launcher_path=launcher,
        source=source,
        bundle_root=r"C:\launcher-bundle",
    )

    assert source == original
    assert environment == {
        "Path": r"C:\Windows\System32;relative-entry",
        "KEEP": "parent-value",
        DEV_TEST_ENVIRONMENT_VARIABLE: "1",
        DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE: FEATURE_NAME,
        DEV_TEST_VERSION_ENVIRONMENT_VARIABLE: FEATURE_VERSION,
        BRANCH_ENVIRONMENT_VARIABLE: FEATURE_BRANCH,
        REVISION_ENVIRONMENT_VARIABLE: FEATURE_REVISION,
        LAUNCHER_PATH_ENVIRONMENT_VARIABLE: str(launcher.resolve()),
        "PYINSTALLER_RESET_ENVIRONMENT": "1",
    }


def test_start_feature_process_uses_exact_detached_popen_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _write_build(tmp_path, "feature-build")
    launcher = tmp_path / "E3 DEV TEST.exe"
    feature = FeatureBuild(
        name=FEATURE_NAME,
        version=FEATURE_VERSION,
        branch=FEATURE_BRANCH,
        revision=FEATURE_REVISION,
        exe=executable,
    )
    environment = {"sentinel": "sanitized child environment"}
    popen_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    dll_directory_calls: list[str | None] = []

    class Process:
        pass

    process = Process()

    def fake_popen(*args: object, **kwargs: object) -> Process:
        popen_calls.append((args, kwargs))
        return process

    monkeypatch.setattr(
        dev_test_launcher,
        "feature_environment",
        lambda received, *, launcher_path: environment,
    )
    monkeypatch.setattr(
        dev_test_launcher,
        "_get_windows_dll_directory",
        lambda: r"C:\launcher-bundle",
    )
    monkeypatch.setattr(
        dev_test_launcher,
        "_set_windows_dll_directory",
        dll_directory_calls.append,
    )

    result = start_feature_process(
        feature,
        launcher_path=launcher,
        popen=fake_popen,
        platform="win32",
    )

    assert result is process
    assert popen_calls == [
        (
            ([str(executable)],),
            {
                "cwd": str(executable.parent),
                "env": environment,
                "close_fds": True,
                "shell": False,
                "creationflags": 0x00000008 | 0x00000200,
            },
        )
    ]
    assert dll_directory_calls == [None, r"C:\launcher-bundle"]


@pytest.mark.parametrize(
    ("payload", "expected_reason"),
    [
        (None, "cannot read"),
        (b"{", "not valid UTF-8 JSON"),
    ],
)
def test_launcher_main_reports_missing_or_invalid_pointer_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes | None,
    expected_reason: str,
) -> None:
    launcher = tmp_path / "E3 DEV TEST.exe"
    launcher.write_bytes(b"stable launcher")
    pointer = tmp_path / "current-feature.json"
    if payload is not None:
        pointer.write_bytes(payload)
    errors: list[tuple[Path, str]] = []

    monkeypatch.setattr(dev_test_launcher, "_launcher_executable", lambda: launcher)
    monkeypatch.setattr(dev_test_launcher, "set_process_app_user_model_id", lambda: None)
    monkeypatch.setattr(
        dev_test_launcher,
        "start_feature_process",
        lambda *args, **kwargs: pytest.fail("invalid pointers must not launch a fallback"),
    )
    monkeypatch.setattr(
        dev_test_launcher,
        "show_launch_error",
        lambda received_pointer, reason: errors.append((received_pointer, reason)),
    )

    assert dev_test_launcher.main([]) == 1
    assert len(errors) == 1
    assert errors[0][0] == pointer
    assert expected_reason in errors[0][1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer script")
def test_install_script_rejects_invalid_pointer_before_writing(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    launcher = tmp_path / "E3 DEV TEST.exe"
    launcher.write_bytes(b"MZ\x00fixture")
    pointer = tmp_path / "invalid-current-feature.json"
    pointer.write_text("{", encoding="utf-8")
    install_directory = tmp_path / "install"
    desktop_directory = tmp_path / "desktop"

    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repository / "packaging" / "install_dev_test_launcher.ps1"),
            "-LauncherSource",
            str(launcher),
            "-PointerSource",
            str(pointer),
            "-InstallDirectory",
            str(install_directory),
            "-DesktopDirectory",
            str(desktop_directory),
            "-Python",
            sys.executable,
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        # A cold Windows PowerShell plus Python process can exceed 30 seconds
        # under a loaded compatibility runner before the script reaches its
        # fail-closed pointer validation. Keep the functional assertions exact
        # while allowing for runner startup and endpoint-scanning latency.
        timeout=120,
        check=False,
    )

    assert result.returncode != 0
    assert "failed validation" in result.stderr
    assert not install_directory.exists()
    assert not desktop_directory.exists()


def test_changing_only_pointer_switches_the_launched_feature_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = tmp_path / "E3 DEV TEST.exe"
    launcher_contents = b"one permanent launcher"
    launcher.write_bytes(launcher_contents)
    pointer = tmp_path / "current-feature.json"
    first_executable = _write_build(
        tmp_path,
        "outer-silhouette",
        version="0.6.161",
        revision="1" * 40,
    )
    second_executable = _write_build(
        tmp_path,
        "next-feature",
        version="0.6.162",
        revision="2" * 40,
    )
    launched: list[tuple[FeatureBuild, Path]] = []

    class Process:
        def wait(self) -> int:
            return 0

    def start(feature: FeatureBuild, *, launcher_path: Path) -> Process:
        launched.append((feature, launcher_path))
        return Process()

    monkeypatch.setattr(dev_test_launcher, "_launcher_executable", lambda: launcher)
    monkeypatch.setattr(dev_test_launcher, "set_process_app_user_model_id", lambda: None)
    monkeypatch.setattr(dev_test_launcher, "start_feature_process", start)
    monkeypatch.setattr(
        dev_test_launcher,
        "show_launch_error",
        lambda *_args: pytest.fail("both pointers are valid"),
    )

    _write_pointer(
        pointer,
        _pointer_object(first_executable, revision="1" * 40),
    )
    assert dev_test_launcher.main([]) == 0

    _write_pointer(
        pointer,
        _pointer_object(
            second_executable,
            name="Next feature",
            version="0.6.162",
            branch="feature/next-feature",
            revision="2" * 40,
        ),
    )
    assert dev_test_launcher.main([]) == 0

    assert [feature.exe for feature, _launcher_path in launched] == [
        first_executable.resolve(),
        second_executable.resolve(),
    ]
    assert all(path == launcher for _feature, path in launched)
    assert launcher.read_bytes() == launcher_contents
