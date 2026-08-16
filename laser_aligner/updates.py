from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .deployment import BuildInfo, runtime_platform_key, update_cache_dir
from .storage import strict_json_loads

_MAX_MANIFEST_BYTES = 1_048_576
_DOWNLOAD_CHUNK_BYTES = 1_048_576
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{7,64}$")


class UpdateError(RuntimeError):
    """Raised when update metadata, download, verification, or launch fails."""


@dataclass(frozen=True, slots=True)
class UpdateAsset:
    platform_key: str
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class UpdateManifest:
    schema_version: int
    version: str
    revision: str
    channel: str
    published_at: str
    assets: dict[str, UpdateAsset]


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    current: BuildInfo
    manifest: UpdateManifest
    asset: UpdateAsset
    available: bool


def _required_string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(f"Update manifest field {key!r} must be non-empty text")
    return value.strip()


def _https_url(value: str, label: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise UpdateError(f"{label} must use an absolute HTTPS URL")
    return value


def _asset_from_mapping(platform_key: str, raw: Mapping[str, Any]) -> UpdateAsset:
    name = _required_string(raw, "name")
    if Path(name).name != name:
        raise UpdateError("Update asset name must not contain a directory path")
    sha256 = _required_string(raw, "sha256").lower()
    if _SHA256_RE.fullmatch(sha256) is None:
        raise UpdateError("Update asset sha256 must be 64 hexadecimal characters")
    size = raw.get("size")
    if type(size) is not int or size <= 0:
        raise UpdateError("Update asset size must be a positive integer")
    return UpdateAsset(
        platform_key=platform_key,
        name=name,
        url=_https_url(_required_string(raw, "url"), "Update asset URL"),
        sha256=sha256,
        size=size,
    )


def parse_manifest(payload: bytes | str) -> UpdateManifest:
    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        raw = strict_json_loads(text)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise UpdateError(f"Update manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise UpdateError("Update manifest root must be a JSON object")
    if raw.get("schema_version") != 1:
        raise UpdateError(
            f"Unsupported update manifest schema: {raw.get('schema_version')!r}"
        )
    revision = _required_string(raw, "revision").lower()
    if _REVISION_RE.fullmatch(revision) is None:
        raise UpdateError("Update manifest revision must be a Git hexadecimal revision")
    assets_raw = raw.get("assets")
    if not isinstance(assets_raw, Mapping) or not assets_raw:
        raise UpdateError("Update manifest assets must be a non-empty object")
    assets: dict[str, UpdateAsset] = {}
    for platform_key, value in assets_raw.items():
        if not isinstance(platform_key, str) or not platform_key.strip():
            raise UpdateError("Update manifest platform keys must be non-empty text")
        if not isinstance(value, Mapping):
            raise UpdateError(f"Update asset {platform_key!r} must be an object")
        normalized_key = platform_key.strip().lower()
        assets[normalized_key] = _asset_from_mapping(normalized_key, value)
    return UpdateManifest(
        schema_version=1,
        version=_required_string(raw, "version"),
        revision=revision,
        channel=_required_string(raw, "channel"),
        published_at=_required_string(raw, "published_at"),
        assets=assets,
    )


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        _https_url(url, "Update URL"),
        headers={
            "Accept": "application/json, application/octet-stream;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "E3-Positioning-System-Updater/1",
        },
        method="GET",
    )


def fetch_manifest(url: str, *, timeout: float = 15.0) -> UpdateManifest:
    try:
        with urllib.request.urlopen(_request(url), timeout=timeout) as response:
            payload = response.read(_MAX_MANIFEST_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"Could not download update manifest: {exc}") from exc
    if len(payload) > _MAX_MANIFEST_BYTES:
        raise UpdateError("Update manifest exceeds the maximum allowed size")
    return parse_manifest(payload)


def _same_revision(left: str, right: str) -> bool:
    left_normalized = left.strip().lower()
    right_normalized = right.strip().lower()
    if not left_normalized or not right_normalized:
        return False
    if left_normalized in {"unknown", "source"}:
        return False
    return (
        left_normalized == right_normalized
        or left_normalized.startswith(right_normalized)
        or right_normalized.startswith(left_normalized)
    )


def check_for_update(build: BuildInfo) -> UpdateCheckResult:
    if not build.manifest_url:
        raise UpdateError("This E3 build does not define an update manifest URL")
    manifest = fetch_manifest(build.manifest_url)
    if manifest.channel != build.channel:
        raise UpdateError(
            "Update channel mismatch: "
            f"installed {build.channel!r}, manifest {manifest.channel!r}"
        )
    platform_key = runtime_platform_key().lower()
    asset = manifest.assets.get(platform_key)
    if asset is None:
        available = ", ".join(sorted(manifest.assets))
        raise UpdateError(
            f"No update package is published for {platform_key}; available: {available}"
        )
    return UpdateCheckResult(
        current=build,
        manifest=manifest,
        asset=asset,
        available=not _same_revision(build.revision, manifest.revision),
    )


def download_update(
    asset: UpdateAsset,
    *,
    destination_dir: Path | None = None,
    timeout: float = 60.0,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    directory = destination_dir or update_cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / asset.name
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(_request(asset.url), timeout=timeout) as response:
            with temporary.open("wb") as handle:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > asset.size:
                        raise UpdateError(
                            "Downloaded update is larger than the manifest size"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress is not None:
                        progress(received, asset.size)
                handle.flush()
                os.fsync(handle.fileno())
    except UpdateError:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"Could not download update package: {exc}") from exc
    if received != asset.size:
        temporary.unlink(missing_ok=True)
        raise UpdateError(
            f"Downloaded update size mismatch: expected {asset.size}, got {received}"
        )
    if digest.hexdigest() != asset.sha256:
        temporary.unlink(missing_ok=True)
        raise UpdateError(
            "Downloaded update failed SHA-256 verification and was discarded"
        )
    os.replace(temporary, destination)
    return destination


def _launch_windows_installer(path: Path) -> None:
    if path.suffix.lower() != ".exe":
        raise UpdateError("Windows update package must be an .exe installer")
    try:
        subprocess.Popen(
            [
                str(path),
                "/SP-",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
                "/NORESTART",
            ],
            close_fds=True,
        )
    except OSError as exc:
        raise UpdateError(f"Could not start the Windows update installer: {exc}") from exc


def _copy_executable(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    mode = destination.stat().st_mode
    destination.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _replace_running_appimage(downloaded: Path, current: Path) -> Path:
    if not current.is_file():
        raise UpdateError(f"Current AppImage does not exist: {current}")
    if not os.access(current.parent, os.W_OK):
        raise UpdateError(f"AppImage directory is not writable: {current.parent}")
    replacement = current.with_name(f".{current.name}.update")
    backup = current.with_name(f"{current.name}.previous")
    replacement.unlink(missing_ok=True)
    _copy_executable(downloaded, replacement)
    backup.unlink(missing_ok=True)
    moved_current = False
    try:
        os.replace(current, backup)
        moved_current = True
        os.replace(replacement, current)
    except OSError as exc:
        replacement.unlink(missing_ok=True)
        if moved_current and backup.exists() and not current.exists():
            os.replace(backup, current)
        raise UpdateError(f"Could not replace the running AppImage: {exc}") from exc
    return current


def _launch_linux_appimage(path: Path) -> None:
    if not path.name.lower().endswith(".appimage"):
        raise UpdateError("Linux update package must be an AppImage")
    current_value = os.environ.get("APPIMAGE", "").strip()
    launch_path = path
    if current_value:
        launch_path = _replace_running_appimage(path, Path(current_value).resolve())
    else:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    try:
        subprocess.Popen([str(launch_path)], close_fds=True, start_new_session=True)
    except OSError as exc:
        raise UpdateError(f"Could not start the updated AppImage: {exc}") from exc


def launch_downloaded_update(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise UpdateError(f"Downloaded update package does not exist: {resolved}")
    if sys.platform == "win32":
        _launch_windows_installer(resolved)
        return
    if sys.platform.startswith("linux"):
        _launch_linux_appimage(resolved)
        return
    raise UpdateError(f"Self-update is not supported on {sys.platform!r}")
