"""Shared admission guard for ordinary E3 packaging, without a prune executor.

The first complete census establishes a legacy baseline. Subsequent builds share
that fixed baseline plus 8 GiB of growth headroom, atomic reservations and a 2 GiB
free-space floor. The baseline can fall, never rise. All known checkout roots
remain in the census, including unregistered, private and protected outputs.

Only exact outputs recorded after a successful guarded build may be replaced by
another invocation of the same build mode. Public output ownership hashes every
byte; private ownership records identity, size, times and complete tree metadata
without reading credentials. Neither ownership nor admission authorizes pruning.
The existing builder remains responsible for its narrowly defined replacements.
Failed builds retain their leases and reservations for explicit recovery review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import artifact_retention as retention

GROWTH_HEADROOM_BYTES = 8 * 1024**3
DEFAULT_RESERVATION_BYTES = 2 * 1024**3
MINIMUM_FREE_BYTES = 2 * 1024**3
_MAX_ROOTS = 512


class StorageGuardError(ValueError):
    pass


@dataclass(frozen=True)
class BuildProcess:
    pid: int
    parent_pid: int
    executable: Path | None
    command_line: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _existing_chain(path: Path) -> None:
    for entry in [*reversed(path.parents), path]:
        try:
            info = retention._stat(entry)
        except FileNotFoundError:
            return
        if entry != path and not stat.S_ISDIR(info.st_mode):
            raise StorageGuardError(f"ancestor is not a directory: {entry}")


def _metadata(path: Path, *, allow_posix_links: bool = False) -> tuple[list[dict[str, Any]], int]:
    """Enumerate only the explicit generated path; never follow a link."""
    _existing_chain(path)
    if not os.path.lexists(path):
        return [], 0
    rows, pending, total = [], [path], 0
    while pending:
        current = pending.pop()
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) and allow_posix_links and current != path:
            rows.append({"relative": str(current.relative_to(path)), "stamp": retention._stamp(info), "link": os.readlink(current)})
            total += info.st_size
            continue
        info = retention._stat(current)
        rows.append({"relative": str(current.relative_to(path)), "stamp": retention._stamp(info)})
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(current) as entries:
                pending.extend(sorted((Path(entry.path) for entry in entries), reverse=True))
        else:
            total += info.st_size
    return rows, total


def _snapshot(
    path: Path, *, private: bool, allow_posix_links: bool = False,
    expected_metadata_fingerprint: str | None = None,
) -> dict[str, Any]:
    rows, size = _metadata(path, allow_posix_links=allow_posix_links)
    metadata_fingerprint = retention._digest(rows)
    if expected_metadata_fingerprint is not None and metadata_fingerprint != expected_metadata_fingerprint:
        raise StorageGuardError(f"output metadata changed before content inspection: {path}")
    digest = hashlib.sha256(retention._json_bytes(rows))
    if not private:
        for row in rows:
            parts = tuple(part.casefold() for part in Path(row["relative"]).parts)
            for index, part in enumerate(parts):
                if part in retention._PROTECTED_NAMES and not (
                    part == "data" and index >= 2 and parts[index - 2:index + 1] == ("_internal", "cv2", "data")
                ):
                    raise StorageGuardError(f"private/user payload appeared in public output: {path}")
            if any(part.endswith(".e3laser") or part == "network-local.json" for part in parts):
                raise StorageGuardError(f"user project/configuration appeared in public output: {path}")
        for row in rows:
            current = path / row["relative"]
            if "link" in row or not stat.S_ISREG(row["stamp"][2]):
                continue
            with current.open("rb") as handle:
                if retention._stamp(os.fstat(handle.fileno())) != tuple(row["stamp"]):
                    raise StorageGuardError(f"output changed while opening: {current}")
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                if retention._stamp(os.fstat(handle.fileno())) != tuple(row["stamp"]):
                    raise StorageGuardError(f"output changed while reading: {current}")
    after, after_size = _metadata(path, allow_posix_links=allow_posix_links)
    if rows != after or size != after_size:
        raise StorageGuardError(f"output changed during inventory: {path}")
    return {
        "path": str(path), "present": bool(rows), "logical_bytes": size,
        "metadata_fingerprint": metadata_fingerprint, "fingerprint": digest.hexdigest(), "private": private,
    }


def output_paths(worktree: Path, mode: str, installer_name: str = "E3-Setup") -> list[Path]:
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", installer_name) or installer_name.split(".")[0].upper() in reserved:
        raise StorageGuardError("installer name must be a plain non-device filename stem")
    if mode in {"windows", "private"}:
        names = ["build/E3", "build/pyinstaller-cache", "dist/E3", f"installer-dist/{installer_name}.exe", "build-info.json", "E3.spec"]
        if mode == "private":
            names.append("packaging/machine-seed")
    elif mode == "launcher":
        names = ["build/dev-test-launcher", "build/dev-test-launcher-spec", "build/pyinstaller-cache", "launcher-dist/E3 DEV TEST.exe"]
    elif mode == "linux":
        names = ["build/E3", "dist/E3", "AppDir", "E3-x86_64.AppImage", "build-info.json", "E3.spec"]
    else:
        raise StorageGuardError("unsupported packaging mode")
    return [worktree / name for name in names]


def _census_paths(root: Path) -> list[Path]:
    return [root / name for name in (
        "dist", "installer-dist", "launcher-dist", "build", ".venv", ".e3-builds",
        "AppDir", "E3-x86_64.AppImage", "appimagetool-x86_64.AppImage",
        "build-info.json", "E3.spec", "packaging/machine-seed",
    )]


def discover_roots(worktree: Path) -> list[Path]:
    """Git's registered roots plus known E3 snapshot containers; metadata only."""
    result = subprocess.run(
        ["git", "-C", str(worktree), "worktree", "list", "--porcelain"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    roots = {retention._path(line[9:]) for line in result.stdout.splitlines() if line.startswith("worktree ")}
    if worktree not in roots:
        raise StorageGuardError("requested checkout is absent from Git's worktree list")
    pending = list(roots)
    while pending:
        root = pending.pop()
        retention._check_ancestors(root)
        for name in (".codex-worktrees", "worktrees"):
            container = root / name
            _existing_chain(container)
            if not container.exists():
                continue
            with os.scandir(container) as entries:
                children = [Path(entry.path) for entry in entries]
            for child in children:
                info = retention._stat(child)
                if not stat.S_ISDIR(info.st_mode):
                    continue
                for candidate in (child, child / "E3"):
                    _existing_chain(candidate)
                    _existing_chain(candidate / "pyproject.toml")
                    _existing_chain(candidate / "packaging" / "build_windows.ps1")
                    if (candidate / "pyproject.toml").is_file() and (candidate / "packaging" / "build_windows.ps1").is_file():
                        if candidate not in roots:
                            roots.add(candidate)
                            pending.append(candidate)
                            if len(roots) > _MAX_ROOTS:
                                raise StorageGuardError("checkout discovery exceeded its bounded root count")
                        break
    return sorted(roots)


def _validate_ephemeral(worktree: Path) -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or retention._path(os.environ.get("GITHUB_WORKSPACE", "")) != worktree
        or not os.environ.get("RUNNER_TEMP")
    ):
        raise StorageGuardError("ephemeral mode requires this exact GitHub-hosted Actions workspace")


def _state_location(worktree: Path, supplied: Path | None, ephemeral: bool) -> Path:
    if ephemeral:
        _validate_ephemeral(worktree)
    if supplied is not None:
        path = retention._path(supplied)
    elif ephemeral:
        path = retention._path(Path(os.environ["RUNNER_TEMP"]) / "e3-build-storage.json")
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        path = retention._path(Path(os.environ["LOCALAPPDATA"]) / "E3 Build Storage" / "state.json")
    else:
        raise StorageGuardError("this local platform requires an explicit shared --state path")
    _existing_chain(path)
    if not path.parent.exists():
        if not path.parent.parent.is_dir():
            raise StorageGuardError("state parent must have an existing parent directory")
        path.parent.mkdir()
    return path


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    state = retention._read_json(path)
    if state.get("schema_version") != 1:
        raise StorageGuardError("unsupported storage state schema")
    if type(state.get("baseline_bytes")) is not int or state["baseline_bytes"] < 0:
        raise StorageGuardError("invalid legacy baseline")
    if state.get("growth_headroom_bytes") != GROWTH_HEADROOM_BYTES:
        raise StorageGuardError("unexpected growth policy; no silent policy reset")
    if not all(isinstance(state.get(key), list) for key in ("roots", "leases", "owned_outputs", "retention_registries", "observed_registries")):
        raise StorageGuardError("invalid storage state collections")
    if not state["roots"] or len(state["roots"]) > _MAX_ROOTS:
        raise StorageGuardError("invalid persisted census roots")
    for root in state["roots"]:
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise StorageGuardError("census root must be absolute")
        retention._path(root)
    for lease in state["leases"]:
        if not isinstance(lease, dict) or type(lease.get("reserved_bytes")) is not int or lease["reserved_bytes"] < 0:
            raise StorageGuardError("invalid persisted reservation")
        retention._check_id(lease.get("id"))
        if not re.fullmatch(r"[0-9a-f]{32}", str(lease.get("token", ""))):
            raise StorageGuardError("invalid lease token")
    return state


@contextmanager
def _locked_state(path: Path) -> Iterator[dict[str, Any]]:
    retention._check_ancestors(path, allow_missing_leaf=True)
    lock = path.with_name(path.name + ".lock")
    retention._check_ancestors(lock, allow_missing_leaf=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise StorageGuardError("storage state is locked; stale locks need explicit review") from exc
    identity = retention._identity(os.fstat(descriptor))
    try:
        os.close(descriptor)
        before = retention._stamp(retention._stat(path)) if path.exists() else None
        state = _load_state(path)
        holder = {"state": state}
        yield holder
        after = retention._stamp(retention._stat(path)) if path.exists() else None
        if before != after:
            raise StorageGuardError("storage state changed outside its lock")
        retention._write_json(path, holder["state"], overwrite=True)
    finally:
        if retention._identity(retention._stat(lock)) == identity:
            lock.unlink()


def _measure(roots: Sequence[Path], *, ephemeral: bool) -> tuple[int, list[dict[str, Any]]]:
    for root in roots:
        _existing_chain(root)
    candidates = {path for root in roots for path in _census_paths(root)}
    # Membership by ancestor keeps the same non-overlapping scope without
    # comparing every generated path against every other checkout's paths.
    paths = [path for path in candidates if not any(parent in candidates for parent in path.parents)]
    measured, total = [], 0
    for path in sorted(paths):
        size = _snapshot(path, private=True, allow_posix_links=ephemeral and os.name != "nt")["logical_bytes"]
        total += size
        if size:
            measured.append({"path": str(path), "logical_bytes": size})
    return total, measured


def inspect_build_processes() -> list[BuildProcess]:
    if os.name != "nt":
        raise StorageGuardError("local process inspection currently requires Windows; hosted CI must be explicit")
    script = (
        "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine) | ConvertTo-Json -Compress"
    )
    response = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    rows = json.loads(response.stdout)
    if not isinstance(rows, list) or not rows:
        raise StorageGuardError("process inventory is incomplete")
    processes = []
    for row in rows:
        relevant = str(row.get("Name", "")).casefold().startswith(("e3", "python", "pyinstaller", "powershell", "pwsh", "iscc", "py.exe"))
        if relevant and (not row.get("ExecutablePath") or not row.get("CommandLine")):
            raise StorageGuardError("a relevant process could not be inspected")
        if row.get("ExecutablePath"):
            processes.append(BuildProcess(
                int(row["ProcessId"]), int(row["ParentProcessId"]), retention._path(row["ExecutablePath"]),
                row.get("CommandLine") or "",
            ))
    return processes


def _validate_owner(owner_pid: int) -> set[int]:
    if type(owner_pid) is not int or owner_pid <= 0:
        raise StorageGuardError("builder owner PID must be positive")
    ignored = {os.getpid()}
    parent = os.getppid()
    if parent == owner_pid:
        return ignored | {owner_pid}
    # Windows venv python.exe is a redirector: the real interpreter can be its
    # child rather than the shell's direct child. Admit only a bounded observed
    # chain of Python processes actually invoking this same guard script.
    if os.name == "nt":
        processes = {process.pid: process for process in inspect_build_processes()}
        for _ in range(4):
            if parent == owner_pid:
                return ignored | {owner_pid}
            process = processes.get(parent)
            if (
                process is None or process.pid in ignored or process.executable is None
                or not re.fullmatch(r"python(?:w|[0-9.]*)?\.exe", process.executable.name, re.IGNORECASE)
                or "build_storage_guard.py" not in process.command_line.casefold()
            ):
                break
            ignored.add(parent)
            parent = process.parent_pid
    raise StorageGuardError("builder owner PID is not this guard's verified invoking parent or Python redirector ancestor")


def _protection_paths(state: dict[str, Any], lease: dict[str, Any]) -> list[Path]:
    ignored_process_ids = _validate_owner(lease["owner_pid"])
    protected = []
    if not lease["ephemeral"]:
        if not lease["launcher_pointers"]:
            raise StorageGuardError("a verified launcher pointer is required for local packaging")
        for pointer in lease["launcher_pointers"]:
            protected.append(retention._launcher_target(retention._path(pointer)))
        processes = inspect_build_processes()
        for process in processes:
            if process.pid in ignored_process_ids:
                continue
            normalized_root = lease["worktree"].replace("/", "\\").casefold()
            command = process.command_line.replace("/", "\\").casefold()
            # Frozen runtime helpers protect their own output trees. A separate
            # destination can coexist with those processes, while shared venv
            # use and another builder still serialize access to source inputs.
            uses_venv = process.executable and retention._within(process.executable, Path(lease["worktree"]) / ".venv")
            is_builder = any(marker in command for marker in (
                "-m pyinstaller", "build_windows.ps1", "build_home_installer.ps1",
                "build_dev_test_launcher.ps1", "build_with_retention.ps1",
            ))
            if uses_venv or (is_builder and normalized_root in command):
                raise StorageGuardError(f"another process is using shared build inputs (PID {process.pid})")
            if process.executable:
                protected.append(process.executable)
    for registry_name in state["retention_registries"]:
        registry = retention._path(registry_name)
        _existing_chain(registry)
        if not registry.exists():
            if registry_name in state["observed_registries"]:
                raise StorageGuardError("a previously observed pin registry is unavailable")
            continue
        if registry_name not in state["observed_registries"]:
            state["observed_registries"].append(registry_name)
        if os.path.lexists(registry.with_name(registry.name + ".lock")):
            raise StorageGuardError("a pin registry is locked by an update")
        registered = retention._load_registry(registry)
        for build in registered["builds"]:
            if build["pin"]:
                protected.extend(retention._path(artifact["path"]) for artifact in build["artifacts"])
        protected.extend(retention._path(item["worktree"]) for item in registered["leases"])
    return protected


def _output_root(lease: dict[str, Any]) -> Path:
    return retention._path(lease.get("output_root", lease["worktree"]))


def _root_identities(path: Path) -> list[dict[str, Any]]:
    retention._check_ancestors(path)
    return [{"path": str(parent), "identity": list(retention._identity(retention._stat(parent)))}
            for parent in [*reversed(path.parents), path]]


def _verify_output_root(lease: dict[str, Any]) -> None:
    path = _output_root(lease)
    expected = lease.get("output_root_identities")
    if expected is not None and _root_identities(path) != expected:
        raise StorageGuardError("build output directory or an ancestor was replaced")


def _destination_conflict(state: dict[str, Any], lease: dict[str, Any], protected: Sequence[Path]) -> bool:
    """Metadata-only choice; unknown/changed/protected outputs are preserved."""
    root = _output_root(lease)
    if lease["mode"] == "windows" and os.path.lexists(root / "packaging" / "machine-seed"):
        return True
    for path in output_paths(root, lease["mode"], lease["installer_name"]):
        _existing_chain(path)
        if any(retention._overlaps(path, target) for target in protected):
            return True
        owners = [item for item in state["owned_outputs"] if item["snapshot"]["path"] == str(path)]
        if not os.path.lexists(path):
            if any(item["snapshot"]["present"] for item in owners):
                return True
            continue
        if len(owners) != 1 or owners[0]["mode"] != lease["mode"]:
            return True
        rows, size = _metadata(path)
        prior = owners[0]["snapshot"]
        if retention._digest(rows) != prior.get("metadata_fingerprint") or size != prior.get("logical_bytes"):
            return True
    return False


def _managed_destination(source: Path, mode: str, build_id: str) -> Path:
    retention._check_id(build_id)
    path = source / ".e3-builds" / f"{mode}-{build_id}"
    _existing_chain(path)
    if os.path.lexists(path):
        raise StorageGuardError("fresh managed output destination already exists")
    return path


def _reusable_destination(state: dict[str, Any], lease: dict[str, Any], protected: Sequence[Path]) -> Path | None:
    """Reuse only a completed destination whose identity and ownership survive."""
    source = Path(lease["worktree"])
    candidates = {}
    for item in state["owned_outputs"]:
        if (
            item.get("source_root") != str(source) or item.get("mode") != lease["mode"]
            or item.get("installer_name") != lease["installer_name"] or not item.get("managed_output")
        ):
            continue
        destination = retention._path(item["output_root"])
        if destination.parent != source / ".e3-builds" or not destination.name.startswith(lease["mode"] + "-"):
            raise StorageGuardError("stored managed destination is outside its source container")
        retention._check_id(destination.name[len(lease["mode"]) + 1:])
        candidates[str(destination)] = item
    for item in sorted(candidates.values(), key=lambda entry: entry["completed_at"], reverse=True):
        destination = retention._path(item["output_root"])
        _existing_chain(destination)
        if not destination.exists() or not item.get("output_root_identities"):
            continue
        if _root_identities(destination) != item["output_root_identities"]:
            continue
        proposed = {**lease, "output_root": str(destination)}
        if not _destination_conflict(state, proposed, protected):
            return destination
    return None


def _verify_replacements(state: dict[str, Any], lease: dict[str, Any]) -> None:
    _verify_output_root(lease)
    protected = _protection_paths(state, lease)
    root = _output_root(lease)
    if lease["mode"] == "windows" and os.path.lexists(root / "packaging" / "machine-seed"):
        raise StorageGuardError("public packaging cannot remove private machine-seed residue")
    for before in lease["before"]:
        path = retention._path(before["path"])
        if any(retention._overlaps(path, target) for target in protected):
            raise StorageGuardError(f"selected, running or pinned output would be replaced: {path}")
        _existing_chain(path)
        owners = [item for item in state["owned_outputs"] if item["snapshot"]["path"] == str(path)]
        if os.path.lexists(path) and (len(owners) != 1 or owners[0]["mode"] != lease["mode"]):
            raise StorageGuardError(f"existing output is unknown or belongs to another mode: {path}")
        expected_metadata = before.get("metadata_fingerprint")
        if not isinstance(expected_metadata, str):
            raise StorageGuardError("previous output ownership lacks an independent metadata fingerprint")
        current = _snapshot(
            path, private=lease["mode"] == "private", allow_posix_links=lease["ephemeral"] and os.name != "nt",
            expected_metadata_fingerprint=expected_metadata,
        )
        if current != before:
            raise StorageGuardError(f"output changed since build admission: {path}")
        if not current["present"]:
            continue
        if len(owners) != 1 or owners[0]["mode"] != lease["mode"] or owners[0]["snapshot"] != current:
            raise StorageGuardError(f"existing output is unknown, changed, or belongs to another mode: {path}")


def _admission(state: dict[str, Any], lease: dict[str, Any], *, already_reserved: bool) -> dict[str, Any]:
    roots = [retention._path(root) for root in state["roots"]]
    measured, census = _measure(roots, ephemeral=lease["ephemeral"])
    if not state["leases"]:
        state["baseline_bytes"] = min(state["baseline_bytes"], measured)
    reserved = sum(item["reserved_bytes"] for item in state["leases"])
    requested = 0 if already_reserved else lease["reserved_bytes"]
    ceiling = state["baseline_bytes"] + state["growth_headroom_bytes"]
    if measured + reserved + requested > ceiling:
        raise StorageGuardError(f"growth budget exceeded: measured={measured}, reserved={reserved}, requested={requested}, ceiling={ceiling}")
    free = shutil.disk_usage(lease["worktree"]).free
    if free - reserved - requested < MINIMUM_FREE_BYTES:
        raise StorageGuardError(f"free-space floor would be crossed: free={free}, required={reserved + requested + MINIMUM_FREE_BYTES}")
    return {"measured_bytes": measured, "reserved_bytes": reserved + requested, "ceiling_bytes": ceiling, "baseline_bytes": state["baseline_bytes"], "free_bytes": free, "census": census}


def begin_build(
    *, worktree: Path, mode: str, build_id: str, owner_pid: int, state_path: Path | None = None,
    roots: Sequence[Path] = (), launcher_pointers: Sequence[Path] | None = None,
    retention_registry: Path | None = None, installer_name: str = "E3-Setup",
    reserve_bytes: int = DEFAULT_RESERVATION_BYTES, ephemeral_ci: bool = False,
    managed_outputs: bool = False,
) -> dict[str, Any]:
    worktree = retention._path(worktree)
    retention._check_ancestors(worktree)
    retention._check_id(build_id)
    _validate_owner(owner_pid)
    if type(reserve_bytes) is not int or reserve_bytes <= 0:
        raise StorageGuardError("build reservation must be a positive integer")
    state_path = _state_location(worktree, state_path, ephemeral_ci)
    discovered = {worktree, *discover_roots(worktree), *(retention._path(root) for root in roots)}
    if any(retention._overlaps(state_path, path) for path in output_paths(worktree, mode, installer_name)):
        raise StorageGuardError("state cannot overlap a packaging output")
    if launcher_pointers is None:
        launcher_pointers = [] if ephemeral_ci else [Path.home() / "Documents" / "E3 Dev Test" / "current-feature.json"]
    registry = retention._path(retention_registry) if retention_registry else state_path.parent / "retention-registry.json"
    lease = {
        "id": build_id, "token": uuid.uuid4().hex, "owner_pid": owner_pid, "worktree": str(worktree),
        "mode": mode, "installer_name": installer_name, "reserved_bytes": reserve_bytes,
        "started_at": _now(), "ephemeral": ephemeral_ci, "retention_registry": str(registry),
        "launcher_pointers": [str(retention._path(pointer)) for pointer in launcher_pointers],
        "output_root": str(worktree), "managed_output": False,
        "before": [],
    }
    create_destination = False
    with _locked_state(state_path) as holder:
        state = holder["state"]
        if state is None:
            initial, _ = _measure(sorted(discovered), ephemeral=ephemeral_ci)
            state = {
                "schema_version": 1, "created_at": _now(), "baseline_bytes": 0 if ephemeral_ci else initial,
                "growth_headroom_bytes": GROWTH_HEADROOM_BYTES, "roots": sorted(str(root) for root in discovered),
                "leases": [], "owned_outputs": [], "ephemeral": ephemeral_ci,
                "retention_registries": [], "observed_registries": [],
            }
            holder["state"] = state
        if state["ephemeral"] != ephemeral_ci:
            raise StorageGuardError("local and hosted-CI storage state cannot be mixed")
        state["retention_registries"] = sorted(set(state["retention_registries"]) | {str(registry)})
        state["roots"] = sorted(set(state["roots"]) | {str(root) for root in discovered})
        if len(state["roots"]) > _MAX_ROOTS:
            raise StorageGuardError("too many census roots")
        if any(item["id"] == build_id or retention._overlaps(Path(item["worktree"]), worktree) for item in state["leases"]):
            raise StorageGuardError("a failed or active build already holds this checkout or build ID")
        if managed_outputs and not ephemeral_ci:
            if mode not in {"windows", "private", "launcher"}:
                raise StorageGuardError("managed output destinations currently support Windows builders only")
            protected = _protection_paths(state, lease)
            if _destination_conflict(state, lease, protected):
                destination = _reusable_destination(state, lease, protected)
                if destination is None:
                    destination = _managed_destination(worktree, mode, build_id)
                    create_destination = True
                lease["output_root"] = str(destination)
                lease["managed_output"] = True
                if not create_destination:
                    lease["output_root_identities"] = _root_identities(destination)
        paths = output_paths(_output_root(lease), mode, installer_name)
        if any(retention._overlaps(state_path, path) for path in paths):
            raise StorageGuardError("state cannot overlap a packaging output")
        if lease["managed_output"] and retention._within(state_path, _output_root(lease)):
            raise StorageGuardError("state cannot be stored inside a managed build destination")
        for path in paths:
            _existing_chain(path)
            if os.path.lexists(path):
                owners = [item for item in state["owned_outputs"] if item["snapshot"]["path"] == str(path)]
                if len(owners) != 1 or owners[0]["mode"] != mode:
                    raise StorageGuardError(f"existing output is unknown or belongs to another mode: {path}")
                lease["before"].append(dict(owners[0]["snapshot"]))
            else:
                lease["before"].append(_snapshot(path, private=mode == "private"))
        _verify_replacements(state, lease)
        report = _admission(state, lease, already_reserved=False)
        state["leases"].append(lease)
        if create_destination:
            destination = _output_root(lease)
            _existing_chain(destination)
            if not destination.parent.exists():
                destination.parent.mkdir()
            retention._check_ancestors(destination.parent)
            destination.mkdir()  # Exclusive leaf creation under the admission lock.
        lease["output_root_identities"] = _root_identities(_output_root(lease))
    return {"state": str(state_path), "build_id": build_id, "token": lease["token"],
            "source_root": str(worktree), "output_root": lease["output_root"],
            "managed_output": lease["managed_output"], **report}


def _owned_lease(state: dict[str, Any], build_id: str, token: str, owner_pid: int) -> dict[str, Any]:
    _validate_owner(owner_pid)
    found = [item for item in state["leases"] if item["id"] == build_id and item["token"] == token and item["owner_pid"] == owner_pid]
    if len(found) != 1:
        raise StorageGuardError("exact build lease and invoking builder ownership are required")
    if found[0]["ephemeral"]:
        _validate_ephemeral(Path(found[0]["worktree"]))
    return found[0]


def verify_build(*, state_path: Path, build_id: str, token: str, owner_pid: int) -> dict[str, Any]:
    state_path = retention._path(state_path)
    with _locked_state(state_path) as holder:
        state = holder["state"]
        if state is None:
            raise StorageGuardError("build admission state disappeared")
        lease = _owned_lease(state, build_id, token, owner_pid)
        _verify_replacements(state, lease)
        report = _admission(state, lease, already_reserved=True)
        lease["verified_at"] = _now()
    return {"verified": build_id, **report}


def complete_build(*, state_path: Path, build_id: str, token: str, owner_pid: int) -> dict[str, Any]:
    state_path = retention._path(state_path)
    with _locked_state(state_path) as holder:
        state = holder["state"]
        if state is None:
            raise StorageGuardError("build admission state disappeared")
        lease = _owned_lease(state, build_id, token, owner_pid)
        _verify_output_root(lease)
        if "verified_at" not in lease:
            raise StorageGuardError("build never passed the immediately-before-replacement verification")
        if lease["mode"] == "windows" and os.path.lexists(_output_root(lease) / "packaging" / "machine-seed"):
            raise StorageGuardError("public build unexpectedly produced private machine-seed material")
        snapshots = [
            _snapshot(Path(before["path"]), private=lease["mode"] == "private", allow_posix_links=lease["ephemeral"] and os.name != "nt")
            for before in lease["before"]
        ]
        if not all(snapshot["present"] or snapshot["path"] == str(_output_root(lease) / "packaging" / "machine-seed") for snapshot in snapshots):
            raise StorageGuardError("successful build is missing an expected generated output")
        paths = {snapshot["path"] for snapshot in snapshots}
        state["owned_outputs"] = [item for item in state["owned_outputs"] if item["snapshot"]["path"] not in paths]
        state["owned_outputs"].extend({
            "build_id": build_id, "mode": lease["mode"], "completed_at": _now(), "snapshot": snapshot,
            "source_root": lease["worktree"], "output_root": str(_output_root(lease)),
            "managed_output": lease.get("managed_output", False), "installer_name": lease["installer_name"],
            "output_root_identities": lease.get("output_root_identities"),
        } for snapshot in snapshots)
        state["leases"].remove(lease)
        measured, census = _measure([Path(root) for root in state["roots"]], ephemeral=lease["ephemeral"])
        ceiling = state["baseline_bytes"] + GROWTH_HEADROOM_BYTES
    return {"completed": build_id, "measured_bytes": measured, "ceiling_bytes": ceiling, "budget_exceeded": measured > ceiling, "census": census}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("begin")
    begin.add_argument("--worktree", type=Path, required=True)
    begin.add_argument("--mode", choices=("windows", "private", "launcher", "linux"), required=True)
    begin.add_argument("--build-id", required=True)
    begin.add_argument("--pid", type=int, required=True)
    begin.add_argument("--state", type=Path)
    begin.add_argument("--root", type=Path, action="append", default=[])
    begin.add_argument("--launcher-pointer", type=Path, action="append")
    begin.add_argument("--retention-registry", type=Path)
    begin.add_argument("--installer-name", default="E3-Setup")
    begin.add_argument("--reserve-bytes", type=int, default=DEFAULT_RESERVATION_BYTES)
    begin.add_argument("--ephemeral-ci", action="store_true")
    begin.add_argument("--managed-outputs", action="store_true")
    for name in ("verify", "complete"):
        command = commands.add_parser(name)
        command.add_argument("--state", type=Path, required=True)
        command.add_argument("--build-id", required=True)
        command.add_argument("--token", required=True)
        command.add_argument("--pid", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "begin":
            result = begin_build(
                worktree=args.worktree, mode=args.mode, build_id=args.build_id, owner_pid=args.pid,
                state_path=args.state, roots=args.root, launcher_pointers=args.launcher_pointer,
                retention_registry=args.retention_registry, installer_name=args.installer_name,
                reserve_bytes=args.reserve_bytes, ephemeral_ci=args.ephemeral_ci, managed_outputs=args.managed_outputs,
            )
        else:
            operation = verify_build if args.command == "verify" else complete_build
            result = operation(state_path=args.state, build_id=args.build_id, token=args.token, owner_pid=args.pid)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f"E3 build storage guard refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
