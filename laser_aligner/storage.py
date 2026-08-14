from __future__ import annotations

import json
import os
import site
import tempfile
from pathlib import Path
from typing import Any


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON numbers must be finite; unsupported constant {value}")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"JSON contains duplicate key {key!r}")
        output[key] = value
    return output


def strict_json_loads(text: str) -> Any:
    """Decode standards-compliant JSON without ambiguous duplicate keys."""

    return json.loads(
        text,
        parse_constant=_reject_json_constant,
        object_pairs_hook=_strict_json_object,
    )


def default_user_data_dir() -> Path:
    """Return the writable per-user data root for E3 on Windows and Linux."""

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "E3 Positioning System"
        return Path(site.getuserbase()) / "E3 Positioning System"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / "e3-positioning-system"
    return Path(site.getuserbase()) / "share" / "e3-positioning-system"


def legacy_user_data_dir() -> Path:
    """Return the pre-portability data root used on every platform."""

    return Path.home() / ".local" / "share" / "e3-positioning-system"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return default


def _fsync_parent_directory(path: Path) -> None:
    """Best-effort persistence for a newly published directory entry."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path.parent, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Some network and virtual filesystems do not support directory fsync.
        pass
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_parent_directory(path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace a binary artifact only after its complete payload reaches disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_parent_directory(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _publish_temp_if_absent(temp_path: Path, path: Path) -> bool:
    """Publish one complete temporary file without replacing an existing path."""

    if os.name == "nt":
        try:
            os.rename(temp_path, path)
        except OSError as exc:
            if isinstance(exc, FileExistsError) or getattr(
                exc, "winerror", None
            ) in {80, 183}:
                return False
            raise
        return True
    try:
        os.link(temp_path, path)
    except FileExistsError:
        return False
    return True


def atomic_write_bytes_if_absent(
    path: Path,
    data: bytes,
    *,
    timestamps_ns: tuple[int, int] | None = None,
) -> bool:
    """Install complete bytes only when no destination exists.

    Final publication is an atomic no-overwrite rename on Windows and an
    atomic hard link on POSIX, so a concurrently created destination always wins.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if timestamps_ns is not None:
            os.utime(temp_path, ns=timestamps_ns)
            # Windows rejects fsync on a read-only descriptor. Reopen the
            # completed file read/write so the timestamp update can be flushed
            # before its no-overwrite publication.
            with temp_path.open("r+b") as handle:
                os.fsync(handle.fileno())
        if not _publish_temp_if_absent(temp_path, path):
            return False
        _fsync_parent_directory(path)
        return True
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))
