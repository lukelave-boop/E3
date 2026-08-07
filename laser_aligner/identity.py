from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

from . import __version__

APPLICATION_NAME = "E3 Positioning System"
REVISION_ENVIRONMENT_VARIABLE = "E3_POSITIONING_SYSTEM_REVISION"

_REVISION_LENGTH = 8
_SOURCE_SUFFIXES = frozenset({".css", ".html", ".js", ".json", ".py", ".svg"})


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


def application_identity() -> str:
    return f"{APPLICATION_NAME} {__version__} · rev {build_revision()}"


def application_window_title(project_name: str, *, dirty: bool = False) -> str:
    dirty_marker = " *" if dirty else ""
    return f"{application_identity()} — {project_name}{dirty_marker}"
