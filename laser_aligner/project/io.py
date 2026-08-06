from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .model import ProjectDocument, ProjectFormatError


PROJECT_EXTENSION = ".e3laser"


def normalize_project_path(path: str | Path) -> Path:
    output = Path(path).expanduser()
    if output.suffix.lower() != PROJECT_EXTENSION:
        output = output.with_suffix(PROJECT_EXTENSION)
    return output.resolve()


def save_project(
    document: ProjectDocument,
    path: str | Path,
    *,
    create_backup: bool = True,
    indent: int = 2,
) -> Path:
    destination = normalize_project_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document.validate()
    payload = document.to_dict()
    data = (json.dumps(payload, indent=indent, sort_keys=True) + "\n").encode("utf-8")

    if create_backup and destination.exists():
        backup = destination.with_suffix(destination.suffix + ".bak")
        backup.write_bytes(destination.read_bytes())

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_project(path: str | Path) -> ProjectDocument:
    source = Path(path).expanduser().resolve()
    try:
        raw: Any = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ProjectFormatError(f"Invalid JSON in {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectFormatError("Project root must be a JSON object")
    return ProjectDocument.from_dict(raw)


def autosave_path(
    document: ProjectDocument,
    *,
    project_path: str | Path | None = None,
    autosave_root: str | Path | None = None,
) -> Path:
    if autosave_root is None:
        autosave_root = (
            Path.home()
            / ".local"
            / "share"
            / "e3-positioning-system"
            / "backups"
        )
    root = Path(autosave_root).expanduser()
    if project_path:
        stem = Path(project_path).stem
    else:
        stem = document.name.strip().replace(" ", "-") or "untitled"
    safe = "".join(character for character in stem if character.isalnum() or character in "-_")[:80]
    return (root / f"{safe}-{document.id}.autosave{PROJECT_EXTENSION}").resolve()


def save_autosave(
    document: ProjectDocument,
    *,
    project_path: str | Path | None = None,
    autosave_root: str | Path | None = None,
) -> Path:
    path = autosave_path(
        document,
        project_path=project_path,
        autosave_root=autosave_root,
    )
    return save_project(document, path, create_backup=False, indent=0)


def autosave_is_newer(
    document: ProjectDocument,
    project_path: str | Path,
    *,
    autosave_root: str | Path | None = None,
) -> bool:
    project = normalize_project_path(project_path)
    autosave = autosave_path(
        document,
        project_path=project,
        autosave_root=autosave_root,
    )
    return (
        autosave.is_file()
        and (not project.exists() or autosave.stat().st_mtime > project.stat().st_mtime)
    )


def clear_autosave(
    document: ProjectDocument,
    *,
    project_path: str | Path | None = None,
    autosave_root: str | Path | None = None,
) -> None:
    autosave_path(
        document,
        project_path=project_path,
        autosave_root=autosave_root,
    ).unlink(missing_ok=True)
