from __future__ import annotations

import json
import os
import site
import tempfile
from pathlib import Path
from typing import Any


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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
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
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_bytes_if_absent(
    path: Path,
    data: bytes,
    *,
    timestamps_ns: tuple[int, int] | None = None,
) -> bool:
    """Install complete bytes only when no destination exists.

    The hard-link publication is atomic and cannot overwrite a file created by
    another process while the temporary payload is being prepared.
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
            with temp_path.open("rb") as handle:
                os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            return False
        return True
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))
