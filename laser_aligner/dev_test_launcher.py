from __future__ import annotations

import json
import ntpath
import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .identity import (
    DEV_TEST_APP_USER_MODEL_ID,
    DEV_TEST_ENVIRONMENT_VARIABLE,
    DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE,
    DEV_TEST_VERSION_ENVIRONMENT_VARIABLE,
)

POINTER_FILENAME = "current-feature.json"
LAUNCHER_DISPLAY_NAME = "E3 DEV TEST"
LAUNCHER_PATH_ENVIRONMENT_VARIABLE = "E3_DEV_TEST_LAUNCHER"
BRANCH_ENVIRONMENT_VARIABLE = "E3_DEV_TEST_BRANCH"
REVISION_ENVIRONMENT_VARIABLE = "E3_DEV_TEST_REVISION"

_MAX_POINTER_BYTES = 65_536
_MAX_PATH_LENGTH = 4_096
_MAX_NAME_LENGTH = 128
_MAX_VERSION_LENGTH = 64
_MAX_BRANCH_LENGTH = 256
_MAX_REVISION_LENGTH = 64
_REQUIRED_FIELDS = frozenset({"name", "version", "branch", "revision", "exe"})
_VERSION_PATTERN = re.compile(r"[0-9][0-9A-Za-z._+-]{0,63}\Z")
_BRANCH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+\-]{0,255}\Z")
_REVISION_PATTERN = re.compile(r"[0-9a-fA-F]{7,64}\Z")

_WINDOWS_DETACHED_PROCESS = 0x00000008
_WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
_WINDOWS_CREATION_FLAGS = (
    _WINDOWS_DETACHED_PROCESS | _WINDOWS_CREATE_NEW_PROCESS_GROUP
)


class DevTestLauncherError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FeatureBuild:
    name: str
    version: str
    branch: str
    revision: str
    exe: Path

    def to_json_object(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "branch": self.branch,
            "revision": self.revision,
            "exe": str(self.exe),
        }


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DevTestLauncherError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _plain_bounded_string(
    value: Any,
    *,
    field: str,
    maximum_length: int,
) -> str:
    if not isinstance(value, str):
        raise DevTestLauncherError(f"{field} must be a string")
    if value != value.strip() or not value:
        raise DevTestLauncherError(f"{field} must be a non-empty trimmed string")
    if len(value) > maximum_length:
        raise DevTestLauncherError(
            f"{field} exceeds the {maximum_length}-character limit"
        )
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise DevTestLauncherError(f"{field} contains a control character")
    return value


def _validate_build_info(feature: FeatureBuild) -> None:
    path = feature.exe.parent / "build-info.json"
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise DevTestLauncherError(
            f"target build metadata is unavailable: {path}"
        ) from exc
    if len(raw_bytes) > _MAX_POINTER_BYTES:
        raise DevTestLauncherError("target build metadata exceeds its safe bound")
    try:
        raw = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DevTestLauncherError("target build metadata is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise DevTestLauncherError("target build metadata must be a JSON object")
    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise DevTestLauncherError("target is not a packaged E3 schema-1 build")
    if raw.get("packaged") is not True:
        raise DevTestLauncherError("target is not a packaged E3 schema-1 build")
    if raw.get("platform_key") != "windows-x86_64":
        raise DevTestLauncherError("target is not a Windows x86-64 E3 build")
    if raw.get("version") != feature.version:
        raise DevTestLauncherError(
            "configured version does not match the target E3 build metadata"
        )
    revision = raw.get("revision")
    if not isinstance(revision, str) or revision.casefold() != feature.revision.casefold():
        raise DevTestLauncherError(
            "configured revision does not match the target E3 build metadata"
        )


def validate_feature_object(
    raw: Any,
    *,
    launcher_path: Path | None = None,
) -> FeatureBuild:
    if not isinstance(raw, dict):
        raise DevTestLauncherError("the pointer root must be a JSON object")
    fields = frozenset(raw)
    missing = sorted(_REQUIRED_FIELDS - fields)
    extra = sorted(fields - _REQUIRED_FIELDS)
    if missing:
        raise DevTestLauncherError(f"missing required field(s): {', '.join(missing)}")
    if extra:
        raise DevTestLauncherError(f"unknown field(s): {', '.join(extra)}")

    name = _plain_bounded_string(
        raw["name"], field="name", maximum_length=_MAX_NAME_LENGTH
    )
    version = _plain_bounded_string(
        raw["version"], field="version", maximum_length=_MAX_VERSION_LENGTH
    )
    branch = _plain_bounded_string(
        raw["branch"], field="branch", maximum_length=_MAX_BRANCH_LENGTH
    )
    revision = _plain_bounded_string(
        raw["revision"], field="revision", maximum_length=_MAX_REVISION_LENGTH
    )
    executable_value = _plain_bounded_string(
        raw["exe"], field="exe", maximum_length=_MAX_PATH_LENGTH
    )

    if _VERSION_PATTERN.fullmatch(version) is None:
        raise DevTestLauncherError("version is not a sane E3 version")
    if (
        _BRANCH_PATTERN.fullmatch(branch) is None
        or ".." in branch
        or "//" in branch
        or branch.endswith(("/", "."))
    ):
        raise DevTestLauncherError("branch is not a sane Git branch name")
    if _REVISION_PATTERN.fullmatch(revision) is None:
        raise DevTestLauncherError("revision must be a hexadecimal Git revision")

    if os.name == "nt":
        drive, remainder = ntpath.splitdrive(executable_value)
        if (
            re.fullmatch(r"[A-Za-z]:", drive) is None
            or not remainder.startswith(("\\", "/"))
        ):
            raise DevTestLauncherError("exe must be an absolute local-drive path")
    elif not Path(executable_value).is_absolute():
        raise DevTestLauncherError("exe must be an absolute path")

    executable = Path(executable_value).resolve()
    if len(str(executable)) > _MAX_PATH_LENGTH:
        raise DevTestLauncherError("resolved exe path exceeds its safe bound")
    if executable.suffix.casefold() != ".exe":
        raise DevTestLauncherError("target must have an .exe extension")
    if not executable.is_file():
        raise DevTestLauncherError(f"target executable does not exist: {executable}")
    if launcher_path is not None and os.path.normcase(str(executable)) == os.path.normcase(
        str(Path(launcher_path).resolve())
    ):
        raise DevTestLauncherError("target executable cannot be the launcher itself")

    feature = FeatureBuild(
        name=name,
        version=version,
        branch=branch,
        revision=revision.lower(),
        exe=executable,
    )
    _validate_build_info(feature)
    return feature


def load_feature_pointer(
    path: Path,
    *,
    launcher_path: Path | None = None,
) -> FeatureBuild:
    pointer = Path(path).resolve()
    try:
        raw_bytes = pointer.read_bytes()
    except OSError as exc:
        raise DevTestLauncherError(f"cannot read {pointer}: {exc}") from exc
    if not raw_bytes:
        raise DevTestLauncherError("the pointer file is empty")
    if len(raw_bytes) > _MAX_POINTER_BYTES:
        raise DevTestLauncherError(
            f"the pointer file exceeds the {_MAX_POINTER_BYTES}-byte limit"
        )
    try:
        raw = json.loads(raw_bytes.decode("utf-8"), object_pairs_hook=_strict_object)
    except DevTestLauncherError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DevTestLauncherError("the pointer is not valid UTF-8 JSON") from exc
    return validate_feature_object(raw, launcher_path=launcher_path)


def write_feature_pointer(path: Path, feature: FeatureBuild) -> Path:
    destination = Path(path).resolve()
    validated = validate_feature_object(feature.to_json_object())
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    payload = json.dumps(
        validated.to_json_object(),
        indent=2,
        ensure_ascii=False,
    ) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    load_feature_pointer(destination)
    return destination


def _path_entry_is_in_bundle(entry: str, bundle_root: str) -> bool:
    candidate = ntpath.expandvars(entry.strip().strip('"'))
    if not candidate or not ntpath.isabs(candidate) or not ntpath.isabs(bundle_root):
        return False
    try:
        normalized_candidate = ntpath.normcase(ntpath.normpath(candidate))
        normalized_root = ntpath.normcase(ntpath.normpath(bundle_root))
        return ntpath.commonpath((normalized_candidate, normalized_root)) == normalized_root
    except (OSError, ValueError):
        return False


def feature_environment(
    feature: FeatureBuild,
    *,
    launcher_path: Path,
    source: Mapping[str, str] | None = None,
    bundle_root: str | None = None,
) -> dict[str, str]:
    environment = dict(os.environ if source is None else source)
    root = bundle_root
    if root is None:
        value = getattr(sys, "_MEIPASS", None)
        root = os.fspath(value) if value is not None else None
    if root is not None:
        for key, value in tuple(environment.items()):
            if key.casefold() == "path":
                environment[key] = ";".join(
                    entry
                    for entry in value.split(";")
                    if not _path_entry_is_in_bundle(entry, root)
                )
    environment.update(
        {
            DEV_TEST_ENVIRONMENT_VARIABLE: "1",
            DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE: feature.name,
            DEV_TEST_VERSION_ENVIRONMENT_VARIABLE: feature.version,
            BRANCH_ENVIRONMENT_VARIABLE: feature.branch,
            REVISION_ENVIRONMENT_VARIABLE: feature.revision,
            LAUNCHER_PATH_ENVIRONMENT_VARIABLE: str(Path(launcher_path).resolve()),
            "PYINSTALLER_RESET_ENVIRONMENT": "1",
        }
    )
    return environment


def _windows_dll_api() -> tuple[Any, Any, Any]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_directory = kernel32.GetDllDirectoryW
    get_directory.argtypes = (wintypes.DWORD, wintypes.LPWSTR)
    get_directory.restype = wintypes.DWORD
    set_directory = kernel32.SetDllDirectoryW
    set_directory.argtypes = (wintypes.LPCWSTR,)
    set_directory.restype = wintypes.BOOL
    return ctypes, get_directory, set_directory


def _get_windows_dll_directory() -> str | None:
    ctypes, get_directory, _set_directory = _windows_dll_api()
    capacity = 32_768
    buffer = ctypes.create_unicode_buffer(capacity)
    ctypes.set_last_error(0)
    length = int(get_directory(capacity, buffer))
    if length == 0:
        error_code = int(ctypes.get_last_error())
        if error_code:
            raise DevTestLauncherError(
                f"cannot inspect the Win32 DLL search directory (error {error_code})"
            )
        return None
    if length >= capacity:
        raise DevTestLauncherError("the Win32 DLL search directory exceeds its safe bound")
    return str(buffer.value)


def _set_windows_dll_directory(value: str | None) -> None:
    ctypes, _get_directory, set_directory = _windows_dll_api()
    ctypes.set_last_error(0)
    if set_directory(value):
        return
    raise DevTestLauncherError(
        f"cannot set the Win32 DLL search directory (error {int(ctypes.get_last_error())})"
    )


def start_feature_process(
    feature: FeatureBuild,
    *,
    launcher_path: Path,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    platform: str | None = None,
) -> subprocess.Popen[bytes]:
    active_platform = sys.platform if platform is None else platform
    if active_platform != "win32":
        raise DevTestLauncherError("E3 DEV TEST is a Windows-only launcher")
    environment = feature_environment(feature, launcher_path=launcher_path)
    previous = _get_windows_dll_directory()
    _set_windows_dll_directory(None)
    try:
        process = popen(
            [str(feature.exe)],
            cwd=str(feature.exe.parent),
            env=environment,
            close_fds=True,
            shell=False,
            creationflags=_WINDOWS_CREATION_FLAGS,
        )
    except OSError as exc:
        try:
            _set_windows_dll_directory(previous)
        except DevTestLauncherError as restore_error:
            raise DevTestLauncherError(
                f"cannot start the configured E3 build: {exc}; additionally, {restore_error}"
            ) from exc
        raise DevTestLauncherError(
            f"cannot start the configured E3 build: {exc}"
        ) from exc
    try:
        _set_windows_dll_directory(previous)
    except DevTestLauncherError:
        # CreateProcess success is authoritative. The independent E3 child has
        # already received a sanitized environment and DLL search state.
        pass
    return process


def set_process_app_user_model_id(
    app_id: str = DEV_TEST_APP_USER_MODEL_ID,
) -> None:
    if sys.platform != "win32":
        raise DevTestLauncherError("AppUserModelID assignment requires Windows")
    import ctypes
    from ctypes import wintypes

    function = ctypes.WinDLL("shell32", use_last_error=True).SetCurrentProcessExplicitAppUserModelID
    function.argtypes = (wintypes.LPCWSTR,)
    function.restype = wintypes.HRESULT
    result = int(function(app_id))
    if result < 0:
        raise DevTestLauncherError(
            f"cannot assign {app_id} (HRESULT 0x{result & 0xFFFFFFFF:08X})"
        )


def show_launch_error(pointer: Path, reason: str) -> None:
    message = (
        "No valid current feature build is configured.\n\n"
        f"Pointer: {Path(pointer).resolve()}\n\n"
        f"Reason: {reason}"
    )
    if sys.platform != "win32":
        raise DevTestLauncherError(message)
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        None,
        message,
        f"{LAUNCHER_DISPLAY_NAME} — Launch failed",
        0x00000010 | 0x00010000,
    )


def _launcher_executable() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        pointer = _launcher_executable().parent / POINTER_FILENAME
        show_launch_error(pointer, "the launcher does not accept command-line arguments")
        return 2
    launcher = _launcher_executable()
    pointer = launcher.parent / POINTER_FILENAME
    try:
        set_process_app_user_model_id()
        feature = load_feature_pointer(pointer, launcher_path=launcher)
        process = start_feature_process(feature, launcher_path=launcher)
    except (DevTestLauncherError, OSError) as exc:
        show_launch_error(pointer, str(exc))
        return 1
    return int(process.wait())


if __name__ == "__main__":
    raise SystemExit(main())
