"""Opt-in E3 build registration and conservative, read-only retention planning.

Only explicitly registered completed Windows bundles, individual installers and
PyInstaller scratch are considered. No source checkout, user data, legacy output
or unregistered directory is adopted by scanning. One registry can cover many
worktrees. Pins, launcher targets, active processes and non-expiring build leases
are protected before the newest-two, seven-day, eight-GiB policy is evaluated.

This first version deliberately has NO deletion executor: even --apply refuses.
Plans are review material, not deletion authorization or a recoverable-space
promise. Fingerprints include every byte and filesystem identity; changed files,
hard links, reparse points, incomplete inspection and absent rollback protection
fail closed. Registration, lease and pin commands are the only registry writes;
plan writes only when an explicit --output is supplied. Never import the app or
connect to its hardware while doing storage maintenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KEEP_NEWEST = 2
MIN_AGE_DAYS = 7
BUDGET_BYTES = 8 * 1024**3
SCHEMA_VERSION = 1
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_GENERATED_NAME = re.compile(r"E3(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?\Z", re.IGNORECASE)
_PROTECTED_NAMES = frozenset({
    ".git", ".venv", ".codex", ".agents", "data", "projects", "secrets",
    "captures", "calibration_profiles", "evidence", "machine-seed",
})


class RetentionError(ValueError):
    """The requested inventory operation cannot be proved safe."""


@dataclass(frozen=True)
class ProcessSnapshot:
    executable_paths: tuple[Path, ...] = ()
    command_lines: tuple[str, ...] = ()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _utc(value: datetime | None = None) -> datetime:
    result = value if value is not None else datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise RetentionError("timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def _timestamp(value: str) -> datetime:
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RetentionError("invalid timezone-qualified timestamp") from exc


def _path(value: Path | str) -> Path:
    candidate = Path(value)
    if ".." in candidate.parts:
        raise RetentionError("parent traversal is not allowed")
    candidate = Path(os.path.abspath(candidate))
    if os.name == "nt" and any(
        ":" in part or part.endswith((" ", ".")) for part in candidate.parts[1:]
    ):
        raise RetentionError("ambiguous Windows path or alternate data stream")
    return candidate


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _overlaps(first: Path, second: Path) -> bool:
    return _within(first, second) or _within(second, first)


def _stat(path: Path) -> os.stat_result:
    result = path.lstat()
    if stat.S_ISLNK(result.st_mode) or getattr(result, "st_file_attributes", 0) & _REPARSE:
        raise RetentionError(f"symlink/reparse point is not permitted: {path}")
    if not result.st_ino or result.st_nlink < 1:
        raise RetentionError(f"filesystem identity is unavailable: {path}")
    if stat.S_ISREG(result.st_mode) and result.st_nlink != 1:
        raise RetentionError(f"hard-linked file is not eligible: {path}")
    if not (stat.S_ISREG(result.st_mode) or stat.S_ISDIR(result.st_mode)):
        raise RetentionError(f"special filesystem entry is not permitted: {path}")
    return result


def _identity(result: os.stat_result) -> tuple[int, int]:
    return result.st_dev, result.st_ino


def _stamp(result: os.stat_result) -> tuple[int, ...]:
    # Windows lstat guesses executable permission from a filename extension;
    # fstat has no filename and omits those synthetic bits for the same handle.
    # File type, read-only state, identity, links, size and time still must match.
    mode = result.st_mode & ~0o111 if os.name == "nt" else result.st_mode
    return (*_identity(result), mode, result.st_nlink, result.st_size, result.st_mtime_ns)


def _check_ancestors(path: Path, *, allow_missing_leaf: bool = False) -> list[list[Any]]:
    chain = list(reversed(path.parents)) + [path]
    identities = []
    for item in chain:
        try:
            info = _stat(item)
        except FileNotFoundError:
            if allow_missing_leaf and item == path:
                break
            raise
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise RetentionError(f"ancestor is not a directory: {item}")
        identities.append([str(item), *_identity(info)])
    return identities


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RetentionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, maximum: int = 8 * 1024**2) -> dict[str, Any]:
    _check_ancestors(path)
    before = _stat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise RetentionError(f"invalid or oversized JSON file: {path}")
    with path.open("rb") as handle:
        if _stamp(os.fstat(handle.fileno())) != _stamp(before):
            raise RetentionError(f"file changed while opening: {path}")
        raw = handle.read(maximum + 1)
        if _stamp(os.fstat(handle.fileno())) != _stamp(before):
            raise RetentionError(f"file changed while reading: {path}")
    if _stamp(_stat(path)) != _stamp(before):
        raise RetentionError(f"file changed while reading: {path}")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RetentionError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RetentionError(f"JSON object required: {path}")
    return value


def _check_id(build_id: str) -> None:
    if not isinstance(build_id, str) or not _ID.fullmatch(build_id):
        raise RetentionError("build ID must be 1-96 plain letters, digits, dots, underscores or hyphens")


def _load_registry(registry: Path) -> dict[str, Any]:
    _check_ancestors(registry, allow_missing_leaf=True)
    if not registry.exists():
        return {"schema_version": SCHEMA_VERSION, "builds": [], "leases": []}
    result = _read_json(registry)
    if result.get("schema_version") != SCHEMA_VERSION or set(result) != {"schema_version", "builds", "leases"}:
        raise RetentionError("unsupported registry schema")
    if not isinstance(result["builds"], list) or not isinstance(result["leases"], list):
        raise RetentionError("invalid registry collections")
    seen: set[str] = set()
    artifact_paths: list[Path] = []
    for build in result["builds"]:
        if not isinstance(build, dict):
            raise RetentionError("invalid registered build")
        _check_id(build.get("id"))
        if build["id"] in seen:
            raise RetentionError("duplicate registered build ID")
        seen.add(build["id"])
        _timestamp(build.get("completed_at"))
        if build.get("pin") not in (None, "keep", "rollback"):
            raise RetentionError("invalid pin role")
        if not isinstance(build.get("worktree"), str) or not Path(build["worktree"]).is_absolute():
            raise RetentionError("registered worktree must be an absolute path")
        worktree = _path(build["worktree"])
        artifacts = build.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise RetentionError("registered build must have artifacts")
        kinds: set[str] = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise RetentionError("invalid registered artifact")
            kind = artifact.get("kind")
            if kind in kinds:
                raise RetentionError("duplicate artifact kind")
            kinds.add(kind)
            if not isinstance(artifact.get("path"), str) or not Path(artifact["path"]).is_absolute():
                raise RetentionError("registered artifact must be an absolute path")
            artifact_path = _path(artifact["path"])
            _allowed_artifact(worktree, artifact_path, kind)
            if any(_overlaps(artifact_path, other) for other in artifact_paths):
                raise RetentionError("registered artifact paths overlap")
            artifact_paths.append(artifact_path)
            if not re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("fingerprint", ""))):
                raise RetentionError("invalid artifact fingerprint")
            if type(artifact.get("logical_bytes")) is not int or artifact["logical_bytes"] < 0:
                raise RetentionError("invalid artifact size")
        if "bundle" not in kinds:
            raise RetentionError("registered build must include a bundle")
    lease_ids: set[str] = set()
    for lease in result["leases"]:
        if not isinstance(lease, dict):
            raise RetentionError("invalid build lease")
        _check_id(lease.get("id"))
        if lease["id"] in lease_ids or not re.fullmatch(r"[0-9a-f]{32}", str(lease.get("token", ""))):
            raise RetentionError("invalid or duplicate build lease")
        lease_ids.add(lease["id"])
        if not isinstance(lease.get("worktree"), str) or not Path(lease["worktree"]).is_absolute():
            raise RetentionError("leased worktree must be an absolute path")
        _path(lease["worktree"])
        _timestamp(lease.get("started_at"))
        if type(lease.get("reserved_bytes")) is not int or lease["reserved_bytes"] < 0:
            raise RetentionError("invalid build lease reservation")
    return result


def _write_json(path: Path, value: Any, *, overwrite: bool) -> None:
    _check_ancestors(path, allow_missing_leaf=True)
    if path.exists() and not overwrite:
        raise RetentionError(f"refusing to overwrite an existing file: {path}")
    raw = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if not overwrite:
        # Exclusive creation works where hard-link creation is unavailable.
        # An interrupted export is incomplete review material, never authority.
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _check_ancestors(path, allow_missing_leaf=True)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def _locked_registry(registry: Path) -> Iterator[dict[str, Any]]:
    _check_ancestors(registry, allow_missing_leaf=True)
    lock = registry.with_name(registry.name + ".lock")
    _check_ancestors(lock, allow_missing_leaf=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RetentionError("registry locked; never automatically remove a stale lock") from exc
    lock_identity = _identity(os.fstat(descriptor))
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        descriptor = -1
        registry_before = _stat(registry) if registry.exists() else None
        payload = _load_registry(registry)
        yield payload
        if registry_before is None:
            if registry.exists():
                raise RetentionError("registry appeared during update")
        elif _stamp(_stat(registry)) != _stamp(registry_before):
            raise RetentionError("registry changed during update")
        _write_json(registry, payload, overwrite=True)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if _identity(_stat(lock)) == lock_identity:
            lock.unlink()


def _allowed_artifact(worktree: Path, path: Path, kind: str) -> None:
    if not _within(path, worktree) or path == worktree:
        raise RetentionError("artifact must be strictly inside its explicit worktree")
    parts = path.relative_to(worktree).parts
    if kind == "bundle":
        valid = len(parts) == 2 and parts[0] == "dist" and bool(_GENERATED_NAME.fullmatch(parts[1]))
    elif kind == "scratch":
        valid = parts == ("build", "E3")
    elif kind == "installer":
        valid = (
            len(parts) == 2 and parts[0] == "installer-dist" and path.suffix.lower() == ".exe"
            and bool(_GENERATED_NAME.fullmatch(path.stem)) and "home" not in path.stem.casefold()
        )
    else:
        valid = False
    if not valid:
        raise RetentionError(f"not an allowed generated {kind} artifact: {path}")


def _bundle_metadata(bundle: Path) -> dict[str, Any]:
    metadata = _read_json(bundle / "build-info.json", maximum=65_536)
    if (
        metadata.get("schema_version") != 1 or metadata.get("packaged") is not True
        or metadata.get("platform_key") != "windows-x86_64"
        or not isinstance(metadata.get("version"), str) or not metadata["version"]
        or not re.fullmatch(r"[0-9a-fA-F]{7,64}", str(metadata.get("revision", "")))
        or metadata.get("channel") == "home"
    ):
        raise RetentionError("bundle is not a supported non-private completed Windows build")
    executable = _stat(bundle / "E3.exe")
    if not stat.S_ISREG(executable.st_mode):
        raise RetentionError("bundle has no regular E3.exe")
    return {key: metadata[key] for key in ("version", "revision")}


def _fingerprint(path: Path, *, kind: str) -> dict[str, Any]:
    ancestors = _check_ancestors(path)
    nodes: list[tuple[Path, os.stat_result]] = []
    pending = [path]
    while pending:
        current = pending.pop()
        info = _stat(current)  # never trust cached DirEntry stat information
        relative = current.relative_to(path) if current != path else Path(".")
        # OpenCV ships read-only Haar cascade vendor assets at this exact path.
        # Do not make a general exception for directories named data.
        parts = tuple(part.casefold() for part in relative.parts)
        if any(
            part in _PROTECTED_NAMES and not (
                kind == "bundle" and part == "data" and index == 2
                and parts[:3] == ("_internal", "cv2", "data")
            )
            for index, part in enumerate(parts)
        ):
            raise RetentionError(f"protected payload present: {current}")
        if current.suffix.casefold() == ".e3laser" or current.name.casefold() == "network-local.json":
            raise RetentionError(f"user data present: {current}")
        nodes.append((current, info))
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(current) as entries:
                pending.extend(sorted((Path(entry.path) for entry in entries), reverse=True))
    if kind in ("bundle", "scratch") and not stat.S_ISDIR(nodes[0][1].st_mode):
        raise RetentionError("bundle and scratch artifacts must be directories")
    if kind == "installer" and not stat.S_ISREG(nodes[0][1].st_mode):
        raise RetentionError("installer must be a single regular file")
    checksum = hashlib.sha256(_json_bytes(ancestors))
    total = files = 0
    identities: set[tuple[int, int]] = set()
    for current, before in nodes:
        if _identity(before) in identities:
            raise RetentionError(f"duplicate filesystem identity: {current}")
        identities.add(_identity(before))
        checksum.update(_json_bytes([str(current.relative_to(path)), _stamp(before)]))
        if stat.S_ISREG(before.st_mode):
            content = hashlib.sha256()
            with current.open("rb") as handle:
                if _stamp(os.fstat(handle.fileno())) != _stamp(before):
                    raise RetentionError(f"file changed while opening: {current}")
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    content.update(block)
                if _stamp(os.fstat(handle.fileno())) != _stamp(before):
                    raise RetentionError(f"file changed while hashing: {current}")
            checksum.update(content.digest())
            total += before.st_size
            files += 1
    # Recheck directories too, detecting additions/removals during the walk.
    for current, before in nodes:
        if _stamp(_stat(current)) != _stamp(before):
            raise RetentionError(f"artifact changed during inventory: {current}")
    if _check_ancestors(path) != ancestors:
        raise RetentionError("artifact ancestors changed during inventory")
    return {
        "kind": kind, "path": str(path), "fingerprint": checksum.hexdigest(),
        "logical_bytes": total, "files": files,
    }


def _validate_artifacts(build: dict[str, Any]) -> None:
    for artifact in build["artifacts"]:
        actual = _fingerprint(_path(artifact["path"]), kind=artifact["kind"])
        if actual != artifact:
            raise RetentionError(f"registered artifact changed: {artifact['path']}")


def _registry_separate(registry: Path, artifacts: Sequence[dict[str, Any]]) -> None:
    for artifact in artifacts:
        if _overlaps(registry, _path(artifact["path"])):
            raise RetentionError("registry must be outside registered artifacts")


def register_build(
    registry: Path, *, worktree: Path, build_id: str, bundle: Path,
    installer: Path | None = None, scratch: Path | None = None,
    lease_token: str | None = None, completed_at: str | None = None,
) -> dict[str, Any]:
    """Explicitly register only fresh generated outputs after successful packaging."""
    registry, worktree, bundle = _path(registry), _path(worktree), _path(bundle)
    _check_id(build_id)
    _check_ancestors(worktree)
    # Machine-seeded installers can embed private calibration and credentials.
    # Inspect only presence, never contents, and do not register such builds.
    packaging = worktree / "packaging"
    if os.path.lexists(packaging):
        _check_ancestors(packaging)
    if os.path.lexists(packaging / "machine-seed"):
        raise RetentionError("machine-seed material is present; private build registration is forbidden")
    artifacts = []
    for kind, value in (("bundle", bundle), ("installer", installer), ("scratch", scratch)):
        if value is None:
            continue
        candidate = _path(value)
        _allowed_artifact(worktree, candidate, kind)
        if kind == "scratch":
            for marker in ("PYZ-00.toc", "EXE-00.toc"):
                if not stat.S_ISREG(_stat(candidate / marker).st_mode):
                    raise RetentionError("scratch directory lacks PyInstaller markers")
        artifacts.append(_fingerprint(candidate, kind=kind))
    metadata = _bundle_metadata(bundle)
    _registry_separate(registry, artifacts)
    completed = _timestamp(completed_at) if completed_at is not None else _utc()
    if completed > _utc() + timedelta(seconds=1):
        raise RetentionError("completion timestamp cannot be in the future")
    record = {
        "id": build_id, "worktree": str(worktree), "completed_at": completed.isoformat(),
        "pin": None, "metadata": metadata, "artifacts": artifacts,
    }
    with _locked_registry(registry) as payload:
        for build in payload["builds"]:
            if build["id"] == build_id:
                raise RetentionError("build ID is already registered; registrations are immutable")
            if any(_overlaps(_path(old["path"]), _path(new["path"])) for old in build["artifacts"] for new in artifacts):
                raise RetentionError("artifact overlaps an existing registration")
        matching = [lease for lease in payload["leases"] if _overlaps(worktree, _path(lease["worktree"]))]
        if matching and not (
            len(matching) == 1 and matching[0]["id"] == build_id
            and matching[0]["worktree"] == str(worktree) and matching[0]["token"] == lease_token
        ):
            raise RetentionError("worktree has a different active build lease")
        if lease_token is not None and not matching:
            raise RetentionError("build lease token no longer owns this worktree")
        _validate_artifacts(record)
        payload["builds"].append(record)
    return record


def acquire_lease(
    registry: Path, *, worktree: Path, build_id: str, pid: int | None = None,
    reserve_bytes: int = 0, budget_bytes: int = BUDGET_BYTES,
) -> dict[str, Any]:
    registry, worktree = _path(registry), _path(worktree)
    _check_id(build_id)
    _check_ancestors(worktree)
    if not worktree.is_dir():
        raise RetentionError("worktree must be a directory")
    if pid is not None and (type(pid) is not int or pid <= 0):
        raise RetentionError("lease PID must be positive")
    if type(reserve_bytes) is not int or reserve_bytes < 0 or type(budget_bytes) is not int or budget_bytes <= 0:
        raise RetentionError("reservation must be nonnegative and admission budget must be positive")
    lease = {
        "id": build_id, "worktree": str(worktree), "token": uuid.uuid4().hex,
        "pid": pid if pid is not None else os.getpid(), "started_at": _utc().isoformat(),
        "reserved_bytes": reserve_bytes,
    }
    with _locked_registry(registry) as payload:
        if any(build["id"] == build_id for build in payload["builds"]):
            raise RetentionError("build ID is already completed")
        if any(item["id"] == build_id or _overlaps(worktree, _path(item["worktree"])) for item in payload["leases"]):
            raise RetentionError("an active build lease already owns this worktree or build ID")
        if any("reserved_bytes" not in item for item in payload["leases"]):
            raise RetentionError("existing lease has an unknown storage reservation")
        registered = sum(artifact["logical_bytes"] for build in payload["builds"] for artifact in build["artifacts"])
        reserved = sum(item["reserved_bytes"] for item in payload["leases"])
        if registered + reserved + reserve_bytes > budget_bytes:
            raise RetentionError("global registered outputs plus active build reservations exceed the admission budget")
        payload["leases"].append(lease)
    return lease


def release_lease(registry: Path, *, build_id: str, token: str) -> None:
    _check_id(build_id)
    with _locked_registry(_path(registry)) as payload:
        matching = [item for item in payload["leases"] if item["id"] == build_id and item["token"] == token]
        if len(matching) != 1:
            raise RetentionError("exact lease ownership token required; no automatic stale-lease removal")
        payload["leases"].remove(matching[0])


def set_pin(registry: Path, *, build_id: str, role: str = "rollback") -> dict[str, Any]:
    if role not in ("rollback", "keep"):
        raise RetentionError("pin role must be rollback or keep")
    with _locked_registry(_path(registry)) as payload:
        matching = [build for build in payload["builds"] if build["id"] == build_id]
        if len(matching) != 1:
            raise RetentionError("pin requires an existing registered build")
        _validate_artifacts(matching[0])
        matching[0]["pin"] = role
        return dict(matching[0])


def inspect_processes() -> ProcessSnapshot:
    """Inspect Windows process metadata; never execute or contact an E3 process."""
    if os.name != "nt":
        raise RetentionError("live process inspection is supported only on Windows")
    script = (
        "$ErrorActionPreference='Stop'; "
        "@(Get-CimInstance Win32_Process | Select-Object Name,ExecutablePath,CommandLine) "
        "| ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, check=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        rows = json.loads(completed.stdout)
        if not isinstance(rows, list) or not rows:
            raise RetentionError("process enumeration did not return a list")
        paths, command_lines = [], []
        for row in rows:
            name = row.get("Name", "").casefold()
            relevant = name.startswith(("e3", "python", "pyinstaller")) or name in {
                "powershell.exe", "pwsh.exe", "iscc.exe", "py.exe",
            }
            if relevant and (not row.get("ExecutablePath") or not row.get("CommandLine")):
                raise RetentionError("a relevant process cannot be fully inspected")
            if row.get("ExecutablePath"):
                paths.append(_path(row["ExecutablePath"]))
            if relevant:
                command_lines.append(row["CommandLine"])
        return ProcessSnapshot(tuple(paths), tuple(command_lines))
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError, TypeError) as exc:
        raise RetentionError("process inspection failed; all cleanup eligibility disabled") from exc


def _launcher_target(pointer: Path) -> Path:
    value = _read_json(pointer, maximum=65_536)
    if not isinstance(value.get("exe"), str) or not Path(value["exe"]).is_absolute():
        raise RetentionError("launcher target must be an absolute executable path")
    executable = _path(value["exe"])
    _check_ancestors(executable)
    if executable.name.casefold() != "e3.exe" or not executable.is_file():
        raise RetentionError("launcher does not point to an existing E3 executable")
    metadata = _bundle_metadata(executable.parent)
    if value.get("version") != metadata["version"] or str(value.get("revision", "")).casefold() != metadata["revision"].casefold():
        raise RetentionError("launcher identity does not match its adjacent build-info.json")
    return executable


def plan_retention(
    registry: Path, *, launcher_pointers: Sequence[Path] = (), active_paths: Sequence[Path] = (),
    now: datetime | None = None, process_inspector: Callable[[], ProcessSnapshot] | None = None,
) -> dict[str, Any]:
    """Return a reviewable plan without creating files, registry entries or locks."""
    registry, moment = _path(registry), _utc(now)
    blockers: list[str] = []
    inspection_errors: list[str] = []
    try:
        payload = _load_registry(registry)
        if registry.with_name(registry.name + ".lock").exists():
            raise RetentionError("registry is locked by an update")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        inspection_errors.append(f"registry inspection failed: {exc}")
        payload = {"builds": [], "leases": []}
    protected = []
    if not launcher_pointers:
        inspection_errors.append("no launcher pointer supplied; selected build cannot be verified")
    for pointer in launcher_pointers:
        try:
            protected.append((_launcher_target(_path(pointer)), "launcher"))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            inspection_errors.append(f"launcher inspection failed: {pointer}: {exc}")
    for active in active_paths:
        try:
            path = _path(active)
            _check_ancestors(path)
            protected.append((path, "explicit-active-path"))
        except (OSError, ValueError) as exc:
            inspection_errors.append(f"active path inspection failed: {exc}")
    processes = ProcessSnapshot()
    try:
        processes = (process_inspector or inspect_processes)()
        if not isinstance(processes, ProcessSnapshot) or not all(isinstance(line, str) for line in processes.command_lines):
            raise RetentionError("invalid process snapshot")
        protected.extend((_path(path), "active-process") for path in processes.executable_paths)
    except Exception as exc:
        processes = ProcessSnapshot()
        inspection_errors.append(f"process inspection failed: {exc}")
    builds = []
    for build in payload["builds"]:
        result = dict(build, status="verified", protection_reasons=[], eligible=False)
        reasons = result["protection_reasons"]
        try:
            _registry_separate(registry, build["artifacts"])
            _validate_artifacts(build)
        except (OSError, ValueError, KeyError) as exc:
            result["status"] = "unverified"
            reasons.append("changed-or-unavailable-artifact")
            inspection_errors.append(f"artifact inspection failed for {build['id']}: {exc}")
        if build["pin"]:
            reasons.append("pinned-" + build["pin"])
        worktree = _path(build["worktree"])
        for path, reason in protected:
            if _overlaps(path, worktree) or any(_overlaps(path, _path(item["path"])) for item in build["artifacts"]):
                reasons.append(reason)
        if any(_overlaps(worktree, _path(lease["worktree"])) for lease in payload["leases"]):
            reasons.append("active-build-lease")
        normalized_worktree = str(worktree).replace("/", "\\").casefold()
        if any(normalized_worktree in line.replace("/", "\\").casefold() for line in processes.command_lines):
            reasons.append("active-process-worktree")
        age = moment - _timestamp(build["completed_at"])
        if age < timedelta(days=MIN_AGE_DAYS):
            reasons.append("minimum-age")
        result["protection_reasons"] = sorted(set(reasons))
        builds.append(result)
    active_reasons = {"launcher", "active-process", "explicit-active-path", "active-build-lease", "active-process-worktree"}
    newest = sorted(
        (build for build in builds if not build["pin"] and not active_reasons.intersection(build["protection_reasons"])),
        key=lambda build: (_timestamp(build["completed_at"]), build["id"]), reverse=True,
    )
    for build in newest[:KEEP_NEWEST]:
        build["protection_reasons"].append("newest-unpinned-build")
    if not any(build["pin"] == "rollback" and build["status"] == "verified" for build in builds):
        blockers.append("no verified explicitly pinned rollback build")
    blockers.extend(inspection_errors)
    candidates = []
    for build in sorted(builds, key=lambda item: (_timestamp(item["completed_at"]), item["id"])):
        if not blockers and not build["protection_reasons"]:
            build["eligible"] = True
            candidates.extend(dict(artifact, build_id=build["id"]) for artifact in build["artifacts"])
    total = sum(artifact["logical_bytes"] for build in builds for artifact in build["artifacts"])
    reserved = sum(lease.get("reserved_bytes", 0) for lease in payload["leases"])
    candidate_bytes = sum(artifact["logical_bytes"] for artifact in candidates)
    plan = {
        "schema_version": SCHEMA_VERSION, "generated_at": moment.isoformat(), "registry": str(registry),
        "execution_enabled": False, "inspection_complete": not inspection_errors,
        "blocking_reasons": blockers, "builds": builds,
        "active_leases": [{key: value for key, value in lease.items() if key != "token"} for lease in payload["leases"]],
        "policy": {"keep_newest_unpinned": KEEP_NEWEST, "minimum_age_days": MIN_AGE_DAYS, "budget_bytes": BUDGET_BYTES},
        "registered_logical_bytes": total, "reserved_logical_bytes": reserved,
        "committed_logical_bytes": total + reserved,
        "budget_bytes": BUDGET_BYTES, "budget_exceeded": total + reserved > BUDGET_BYTES,
        "candidates": candidates, "candidate_logical_bytes": candidate_bytes,
        "projected_retained_logical_bytes": total - candidate_bytes,
        "protected_over_budget": total - candidate_bytes > BUDGET_BYTES,
        "space_accounting": "Logical bytes only; not verified physical reclaim. Unregistered outputs are untouched.",
    }
    plan["manifest_sha256"] = _digest(plan)
    return plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="read-only inventory; never deletes")
    plan.add_argument("--registry", type=Path, required=True)
    plan.add_argument("--launcher-pointer", type=Path, action="append", default=[])
    plan.add_argument("--active-path", type=Path, action="append", default=[])
    plan.add_argument("--output", type=Path)
    plan.add_argument("--apply", action="store_true", help="refused: destructive policy has not been approved")
    plan.add_argument("--reviewed-manifest-sha256", help="reserved for a future approved executor; never authorizes this version")
    register = commands.add_parser("register")
    register.add_argument("--registry", type=Path, required=True)
    register.add_argument("--worktree", type=Path, required=True)
    register.add_argument("--build-id", required=True)
    register.add_argument("--bundle", type=Path, required=True)
    register.add_argument("--installer", type=Path)
    register.add_argument("--scratch", type=Path)
    register.add_argument("--lease-token")
    lease = commands.add_parser("lease")
    lease.add_argument("--registry", type=Path, required=True)
    lease.add_argument("--worktree", type=Path, required=True)
    lease.add_argument("--build-id", required=True)
    lease.add_argument("--pid", type=int)
    lease.add_argument("--reserve-bytes", type=int, default=0)
    lease.add_argument("--budget-bytes", type=int, default=BUDGET_BYTES)
    release = commands.add_parser("release")
    release.add_argument("--registry", type=Path, required=True)
    release.add_argument("--build-id", required=True)
    release.add_argument("--token", required=True)
    pin = commands.add_parser("pin")
    pin.add_argument("--registry", type=Path, required=True)
    pin.add_argument("--build-id", required=True)
    pin.add_argument("--role", choices=("rollback", "keep"), default="rollback")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "plan":
            if arguments.apply:
                raise RetentionError("Deletion is disabled. --apply requires a separately approved policy and executor.")
            result = plan_retention(arguments.registry, launcher_pointers=arguments.launcher_pointer, active_paths=arguments.active_path)
            if arguments.output:
                output = _path(arguments.output)
                if _overlaps(output, _path(arguments.registry)) or any(
                    _overlaps(output, _path(build["worktree"])) for build in result["builds"]
                ):
                    raise RetentionError("manifest output must be outside registry and registered worktrees")
                _write_json(output, result, overwrite=False)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["inspection_complete"] else 2
        if arguments.command == "register":
            result = register_build(
                arguments.registry, worktree=arguments.worktree, build_id=arguments.build_id,
                bundle=arguments.bundle, installer=arguments.installer, scratch=arguments.scratch,
                lease_token=arguments.lease_token,
            )
        elif arguments.command == "lease":
            result = acquire_lease(
                arguments.registry, worktree=arguments.worktree, build_id=arguments.build_id, pid=arguments.pid,
                reserve_bytes=arguments.reserve_bytes, budget_bytes=arguments.budget_bytes,
            )
        elif arguments.command == "release":
            release_lease(arguments.registry, build_id=arguments.build_id, token=arguments.token)
            result = {"released": arguments.build_id}
        else:
            result = set_pin(arguments.registry, build_id=arguments.build_id, role=arguments.role)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Artifact retention refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
