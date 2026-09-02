from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .versioning import (
    RUNTIME_VERSION_ENVIRONMENT_VARIABLE,
)
from .versioning import (
    application_version as _resolved_application_version,
)

APPLICATION_NAME = "E3 Positioning System"
REVISION_ENVIRONMENT_VARIABLE = "E3_POSITIONING_SYSTEM_REVISION"
VERSION_ENVIRONMENT_VARIABLE = RUNTIME_VERSION_ENVIRONMENT_VARIABLE
DEV_TEST_ENVIRONMENT_VARIABLE = "E3_DEV_TEST"
DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE = "E3_DEV_TEST_FEATURE"
DEV_TEST_VERSION_ENVIRONMENT_VARIABLE = "E3_DEV_TEST_VERSION"
DEV_TEST_APPLICATION_NAME = "E3 DEV TEST"
DEV_TEST_APP_USER_MODEL_ID = "E3.DevTest"

_NORMAL_ICON_FILENAME = "e3-positioning-system.svg"
_DEV_TEST_ICON_FILENAME = "e3-dev-test.svg"
_DEV_TEST_FEATURE_MAX_LENGTH = 128
_DEV_TEST_VERSION_MAX_LENGTH = 64

_REVISION_LENGTH = 8
_SOURCE_SUFFIXES = frozenset({".css", ".html", ".js", ".json", ".py", ".svg"})


@dataclass(frozen=True, slots=True)
class DevTestIdentity:
    feature: str
    version: str


def _bounded_display_value(value: str, *, maximum_length: int) -> str:
    printable = "".join(" " if ord(character) < 32 else character for character in value)
    return " ".join(printable.split())[:maximum_length].strip()


def dev_test_identity(
    environment: Mapping[str, str] | None = None,
) -> DevTestIdentity | None:
    """Return the explicit feature-test identity, or ``None`` for production."""

    values = os.environ if environment is None else environment
    if values.get(DEV_TEST_ENVIRONMENT_VARIABLE) != "1":
        return None
    feature = _bounded_display_value(
        values.get(DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE, ""),
        maximum_length=_DEV_TEST_FEATURE_MAX_LENGTH,
    )
    version = _bounded_display_value(
        values.get(DEV_TEST_VERSION_ENVIRONMENT_VARIABLE, ""),
        maximum_length=_DEV_TEST_VERSION_MAX_LENGTH,
    ).removeprefix("v")
    return DevTestIdentity(
        feature=feature or "Feature build",
        version=version or application_version(),
    )


def application_icon_filename() -> str:
    return (
        _DEV_TEST_ICON_FILENAME
        if dev_test_identity() is not None
        else _NORMAL_ICON_FILENAME
    )


def configure_windows_app_user_model_id(
    *,
    platform: str | None = None,
    setter: Callable[[str], int] | None = None,
) -> bool:
    """Assign the DEV-only taskbar identity before Qt creates any UI.

    Production deliberately makes no Win32 call and retains its existing
    implicit AppUserModelID.
    """

    if dev_test_identity() is None:
        return False
    if (sys.platform if platform is None else platform) != "win32":
        return False
    if setter is None:
        import ctypes
        from ctypes import wintypes

        function = ctypes.WinDLL("shell32", use_last_error=True).SetCurrentProcessExplicitAppUserModelID
        function.argtypes = (wintypes.LPCWSTR,)
        function.restype = wintypes.HRESULT
        setter = function
    result = int(setter(DEV_TEST_APP_USER_MODEL_ID))
    if result < 0:
        raise OSError(
            f"Could not assign the E3 DEV TEST AppUserModelID (HRESULT 0x{result & 0xFFFFFFFF:08X})"
        )
    return True


def _source_revision(package_root: Path) -> str:
    """Return a short fingerprint of the files that make up the application."""
    digest = hashlib.blake2s(digest_size=_REVISION_LENGTH // 2)
    found_file = False
    try:
        paths = sorted(
            path
            for path in package_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in _SOURCE_SUFFIXES
            and "__pycache__" not in path.parts
        )
    except OSError:
        return "unknown"

    for path in paths:
        try:
            contents = path.read_bytes()
        except OSError:
            continue
        relative_path = path.relative_to(package_root).as_posix().encode("utf-8")
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(contents)
        digest.update(b"\0")
        found_file = True
    return digest.hexdigest() if found_file else "unknown"


def _normalized_revision(value: str) -> str | None:
    normalized = re.sub(r"[^A-Za-z0-9._+-]+", "-", value.strip())
    normalized = normalized.strip("-._+")
    return normalized[:32] or None


@lru_cache(maxsize=1)
def build_revision() -> str:
    """Return the packaged revision override or a fingerprint of this source tree."""
    override = _normalized_revision(
        os.environ.get(REVISION_ENVIRONMENT_VARIABLE, "")
    )
    if override is not None:
        return override
    return _source_revision(Path(__file__).resolve().parent)


def application_version() -> str:
    return _resolved_application_version()


def application_identity() -> str:
    development = dev_test_identity()
    if development is not None:
        return (
            f"{DEV_TEST_APPLICATION_NAME} — {development.feature} — "
            f"v{development.version}"
        )
    return f"{APPLICATION_NAME} {application_version()} · build {build_revision()[:8]}"


def application_window_title(project_name: str, *, dirty: bool = False) -> str:
    dirty_marker = " *" if dirty else ""
    return f"{application_identity()} — {project_name}{dirty_marker}"
