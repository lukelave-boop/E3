from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .storage import default_user_data_dir, strict_json_loads

_BUILD_INFO_FILENAME = "build-info.json"
_DEFAULT_REPOSITORY = "lukelave-boop/E3"
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class BuildInfo:
    schema_version: int
    version: str
    revision: str
    channel: str
    repository: str
    manifest_url: str
    platform_key: str
    packaged: bool

    @property
    def short_revision(self) -> str:
        return self.revision[:8] if self.revision else "unknown"


@dataclass(frozen=True, slots=True)
class LaunchProfile:
    config_path: Path
    hardware_enabled: bool
    laser_lockout: bool


def application_root() -> Path:
    """Return the replaceable application-file root for source or frozen builds."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def user_state_root() -> Path:
    """Return E3's writable, upgrade-preserved machine/user state root."""

    override = os.environ.get("E3_USER_STATE_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else default_user_data_dir()


def user_config_path() -> Path:
    override = os.environ.get("E3_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return user_state_root() / "config" / "network-local.json"


def bridge_token_path() -> Path:
    override = os.environ.get("E3_BRIDGE_TOKEN_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return user_state_root() / "secrets" / "bridge-token.txt"


def update_cache_dir() -> Path:
    path = user_state_root() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_platform_key() -> str:
    system = platform.system().strip().lower()
    machine = platform.machine().strip().lower()
    architecture = (
        "x86_64"
        if machine in {"amd64", "x86_64", "x64"}
        else "arm64" if machine in {"aarch64", "arm64"} else machine or "unknown"
    )
    prefix = "windows" if system == "windows" else "linux" if system == "linux" else system
    return f"{prefix}-{architecture}"


def _build_info_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    override = os.environ.get("E3_BUILD_INFO", "").strip()
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(application_root() / _BUILD_INFO_FILENAME)
    candidates.append(Path(__file__).resolve().parents[1] / _BUILD_INFO_FILENAME)
    output: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in output:
            output.append(resolved)
    return tuple(output)


def _string_value(raw: Mapping[str, Any], key: str, default: str = "") -> str:
    value = raw.get(key, default)
    return str(value).strip() if value is not None else default


def _load_build_info_file(path: Path) -> BuildInfo | None:
    try:
        raw = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(raw, Mapping):
        return None
    try:
        schema_version = int(raw.get("schema_version", 1))
    except (TypeError, ValueError):
        return None
    if schema_version != 1:
        return None
    return BuildInfo(
        schema_version=1,
        version=_string_value(raw, "version", __version__) or __version__,
        revision=_string_value(raw, "revision", "unknown") or "unknown",
        channel=_string_value(raw, "channel", "development") or "development",
        repository=_string_value(raw, "repository", _DEFAULT_REPOSITORY)
        or _DEFAULT_REPOSITORY,
        manifest_url=_string_value(raw, "manifest_url"),
        platform_key=_string_value(raw, "platform_key", runtime_platform_key())
        or runtime_platform_key(),
        packaged=bool(raw.get("packaged", True)),
    )


def load_build_info() -> BuildInfo:
    for path in _build_info_candidates():
        if path.is_file():
            loaded = _load_build_info_file(path)
            if loaded is not None:
                return loaded
    revision = os.environ.get("E3_POSITIONING_SYSTEM_REVISION", "").strip()
    return BuildInfo(
        schema_version=1,
        version=__version__,
        revision=revision or "source",
        channel=os.environ.get("E3_UPDATE_CHANNEL", "source").strip() or "source",
        repository=_DEFAULT_REPOSITORY,
        manifest_url=os.environ.get("E3_UPDATE_MANIFEST_URL", "").strip(),
        platform_key=runtime_platform_key(),
        packaged=False,
    )


def read_bridge_token() -> str | None:
    direct = os.environ.get("E3_BRIDGE_TOKEN", "").strip()
    if direct:
        return direct
    candidates = (
        bridge_token_path(),
        application_root() / "secrets" / "bridge-token.txt",
    )
    for path in candidates:
        try:
            token = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if token:
            return token
    return None


def resolve_launch_profile() -> LaunchProfile:
    """Choose preserved machine state first, then a safe packaged fallback."""

    explicit = os.environ.get("E3_CONFIG_PATH", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"E3 configuration does not exist: {path}")
        hardware = (
            os.environ.get("E3_HARDWARE_MODE", "1").strip().lower()
            not in _FALSE_VALUES
        )
        return LaunchProfile(path, hardware_enabled=hardware, laser_lockout=hardware)

    preserved = user_config_path()
    if preserved.is_file():
        return LaunchProfile(preserved, hardware_enabled=True, laser_lockout=True)

    root = application_root()
    legacy_machine = root / "config" / "network-local.json"
    if legacy_machine.is_file():
        return LaunchProfile(legacy_machine, hardware_enabled=True, laser_lockout=True)

    packaged_default = root / "config" / "default.json"
    if packaged_default.is_file():
        return LaunchProfile(
            packaged_default,
            hardware_enabled=False,
            laser_lockout=True,
        )

    source_default = Path(__file__).resolve().parents[1] / "config" / "default.json"
    if source_default.is_file():
        return LaunchProfile(source_default, hardware_enabled=False, laser_lockout=True)

    raise FileNotFoundError(
        "E3 has no preserved machine configuration or packaged default configuration"
    )
