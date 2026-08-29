from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..storage import (
    atomic_write_bytes,
    atomic_write_bytes_if_absent,
    default_user_data_dir,
    legacy_user_data_dir,
)
from .model import ProjectDocument, ProjectFormatError

PROJECT_EXTENSION = ".e3laser"
MAX_PROJECT_BYTES = 32 * 1024 * 1024


def _reject_json_constant(value: str) -> None:
    raise ProjectFormatError(f"Project JSON contains unsupported constant {value}")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ProjectFormatError(f"Project JSON contains duplicate key {key!r}")
        output[key] = value
    return output


def _opened_file_identity(value: os.stat_result) -> tuple[int | None, ...]:
    """Return fields comparable across separately opened handles."""

    fields = ["st_dev", "st_ino", "st_size", "st_mtime_ns"]
    if os.name == "nt":
        fields.append("st_birthtime_ns")
    else:
        fields.append("st_ctime_ns")
    return tuple(getattr(value, field, None) for field in fields)


def _read_project_bytes(source: Path) -> bytes:
    try:
        with source.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > MAX_PROJECT_BYTES:
                raise ProjectFormatError(
                    f"Project exceeds the {MAX_PROJECT_BYTES:,}-byte file limit"
                )
            data = handle.read(MAX_PROJECT_BYTES + 1)
            after = os.fstat(handle.fileno())

        # Open and read the path again. Matching metadata alone is not enough:
        # a replacement or rewrite can preserve timestamps and file size.
        with source.open("rb") as verification_handle:
            verification_before = os.fstat(verification_handle.fileno())
            verification = verification_handle.read(MAX_PROJECT_BYTES + 1)
            verification_after = os.fstat(verification_handle.fileno())
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ProjectFormatError(f"Could not read project {source}: {exc}") from exc

    if len(data) > MAX_PROJECT_BYTES or len(verification) > MAX_PROJECT_BYTES:
        raise ProjectFormatError(
            f"Project exceeds the {MAX_PROJECT_BYTES:,}-byte file limit"
        )

    identities = tuple(
        _opened_file_identity(value)
        for value in (
            before,
            after,
            verification_before,
            verification_after,
        )
    )
    if len(set(identities)) != 1 or verification != data:
        raise ProjectFormatError(
            f"Project changed while it was being read: {source}"
        )
    return data

def _autosave_filename(
    document: ProjectDocument,
    project_path: str | Path | None,
) -> str:
    if project_path:
        stem = Path(project_path).stem
    else:
        stem = document.name.strip().replace(" ", "-") or "untitled"
    safe = "".join(
        character for character in stem if character.isalnum() or character in "-_"
    )[:80]
    return f"{safe}-{document.id}.autosave{PROJECT_EXTENSION}"


def _default_autosave_path(
    document: ProjectDocument,
    project_path: str | Path | None,
) -> Path:
    filename = _autosave_filename(document, project_path)
    preferred = (default_user_data_dir() / "backups" / filename).expanduser().resolve()
    legacy = (legacy_user_data_dir() / "backups" / filename).expanduser().resolve()
    if preferred == legacy or preferred.exists() or not legacy.is_file():
        return preferred
    try:
        with legacy.open("rb") as handle:
            stat = os.fstat(handle.fileno())
            data = handle.read()
        atomic_write_bytes_if_absent(
            preferred,
            data,
            timestamps_ns=(stat.st_atime_ns, stat.st_mtime_ns),
        )
    except OSError:
        # Recovery must remain visible even when the new platform directory is
        # temporarily unwritable. A later successful lookup retries migration.
        return legacy
    return preferred


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
    # Reparse the complete serialized model so post-construction mutations
    # cannot publish a project that the loader itself would reject.
    ProjectDocument.from_dict(payload)
    try:
        serialized = json.dumps(
            payload,
            indent=indent,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProjectFormatError(f"Project contains non-JSON data: {exc}") from exc
    data = (serialized + "\n").encode("utf-8")

    if create_backup and destination.exists():
        backup = destination.with_suffix(destination.suffix + ".bak")
        atomic_write_bytes(backup, destination.read_bytes())

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
        text = _read_project_bytes(source).decode("utf-8")
        raw: Any = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
    except RecursionError as exc:
        raise ProjectFormatError(
            f"Project structure is nested too deeply in {source}"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectFormatError(f"Invalid JSON in {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectFormatError("Project root must be a JSON object")
    try:
        return ProjectDocument.from_dict(raw)
    except RecursionError as exc:
        raise ProjectFormatError(
            f"Project structure is nested too deeply in {source}"
        ) from exc


def autosave_path(
    document: ProjectDocument,
    *,
    project_path: str | Path | None = None,
    autosave_root: str | Path | None = None,
) -> Path:
    if autosave_root is None:
        return _default_autosave_path(document, project_path)
    root = Path(autosave_root).expanduser()
    return (root / _autosave_filename(document, project_path)).resolve()


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
    if autosave_root is not None:
        autosave_path(
            document,
            project_path=project_path,
            autosave_root=autosave_root,
        ).unlink(missing_ok=True)
        return
    filename = _autosave_filename(document, project_path)
    for root in (default_user_data_dir(), legacy_user_data_dir()):
        (root / "backups" / filename).expanduser().resolve().unlink(missing_ok=True)
