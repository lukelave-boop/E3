"""Explicit-root, metadata-only E3 inventory and first-time state initialization.

Payload files are NEVER opened or read. Enumeration uses lstat/scandir only;
metadata digests describe names, identities, sizes and timestamps, never file
contents. Private installers and machine-seed copies contribute their metadata
sizes without exposing their contents. Links, inaccessible entries, hardlinks,
special files or drift between complete passes prevent initialization.

There is no Git/root discovery, app import, build admission, output adoption,
process inspection, deletion or build lease. Only a new report and an explicitly
requested new nonsecurity state file can be written. The initializer uses the
normal guard's state.json.lock convention and refuses any existing state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GROWTH_HEADROOM_BYTES = 8 * 1024**3
MAX_ROOTS = 512
GENERATED_RELATIVE_PATHS = (
    "dist", "installer-dist", "launcher-dist", "build", ".venv", ".e3-builds",
    "AppDir", "E3-x86_64.AppImage", "appimagetool-x86_64.AppImage",
    "build-info.json", "E3.spec", "packaging/machine-seed",
)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class InventoryError(ValueError):
    """A complete metadata baseline could not be established."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _absolute(value: Path | str) -> Path:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise InventoryError("all roots and destinations must be absolute without parent traversal")
    if os.name == "nt" and any(":" in part or part.endswith((" ", ".")) for part in path.parts[1:]):
        raise InventoryError("ambiguous Windows path or alternate data stream")
    return Path(os.path.abspath(path))


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _checked_stat(path: Path) -> os.stat_result:
    result = path.lstat()
    if stat.S_ISLNK(result.st_mode) or getattr(result, "st_file_attributes", 0) & _REPARSE:
        raise InventoryError(f"link/reparse point is excluded and makes the census incomplete: {path}")
    if not (stat.S_ISDIR(result.st_mode) or stat.S_ISREG(result.st_mode)):
        raise InventoryError(f"special filesystem entry is not measurable: {path}")
    if not result.st_ino or result.st_nlink < 1:
        raise InventoryError(f"filesystem identity unavailable: {path}")
    if stat.S_ISREG(result.st_mode) and result.st_nlink != 1:
        raise InventoryError(f"hardlink accounting is uncertain: {path}")
    return result


def _chain(path: Path, *, may_be_absent: bool = False) -> tuple[tuple[str, int, int], ...]:
    identities = []
    for item in [*reversed(path.parents), path]:
        try:
            info = _checked_stat(item)
        except FileNotFoundError:
            if may_be_absent:
                return tuple(identities)
            raise
        if item != path and not stat.S_ISDIR(info.st_mode):
            raise InventoryError(f"ancestor is not a directory: {item}")
        identities.append((str(item), info.st_dev, info.st_ino))
    return tuple(identities)


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
        getattr(info, "st_file_attributes", 0),
    )


def _feed_metadata(digest: Any, value: Any) -> None:
    digest.update(json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n")


def _validated_roots(values: Sequence[Path]) -> list[Path]:
    if not values or len(values) > MAX_ROOTS:
        raise InventoryError(f"provide 1-{MAX_ROOTS} explicitly approved checkout roots")
    roots = sorted({_absolute(root) for root in values})
    for root in roots:
        _chain(root)
        if not stat.S_ISDIR(_checked_stat(root).st_mode):
            raise InventoryError(f"checkout root is not a directory: {root}")
        # These are identity checks only: neither source marker is opened.
        for relative in ("pyproject.toml", "packaging/build_windows.ps1"):
            marker = root / relative
            _chain(marker)
            if not stat.S_ISREG(_checked_stat(marker).st_mode):
                raise InventoryError(f"approved root lacks an E3 source marker: {marker}")
    return roots


def _census_paths(roots: Sequence[Path]) -> list[Path]:
    # Set membership checks only each path's ancestors, instead of comparing
    # every generated path with all preceding paths across hundreds of roots.
    # Any candidate ancestor covers its descendants, irrespective of root order.
    candidates = {root / relative for root in roots for relative in GENERATED_RELATIVE_PATHS}
    return sorted(path for path in candidates if not any(parent in candidates for parent in path.parents))


def _scan_pass(roots: Sequence[Path]) -> dict[str, Any]:
    digest = hashlib.sha256()
    errors: list[str] = []
    totals = []
    entries = total = 0
    for root in roots:
        try:
            _feed_metadata(digest, ["root", _chain(root)])
        except (OSError, InventoryError) as exc:
            errors.append(str(exc))
    for target in _census_paths(roots):
        size = files = directories = 0
        target_errors_before = len(errors)
        try:
            _feed_metadata(digest, ["ancestors", str(target), _chain(target, may_be_absent=True)])
            try:
                initial = _checked_stat(target)
            except FileNotFoundError:
                _feed_metadata(digest, ["absent", str(target)])
                totals.append({"path": str(target), "present": False, "logical_bytes": 0, "files": 0, "directories": 0})
                continue
            pending = [(target, initial)]
            while pending:
                current, before = pending.pop()
                # Recheck every path directly; cached DirEntry information is not used.
                info = _checked_stat(current)
                if _signature(info) != _signature(before):
                    raise InventoryError(f"entry drift during enumeration: {current}")
                _feed_metadata(digest, ["entry", str(current), _signature(info)])
                entries += 1
                if stat.S_ISDIR(info.st_mode):
                    directories += 1
                    with os.scandir(current) as children:
                        names = sorted((Path(child.path) for child in children), reverse=True)
                    for child in names:
                        pending.append((child, _checked_stat(child)))
                else:
                    files += 1
                    size += info.st_size
                if _signature(_checked_stat(current)) != _signature(info):
                    raise InventoryError(f"entry drift during enumeration: {current}")
        except (OSError, InventoryError) as exc:
            errors.append(str(exc))
        total += size
        totals.append({
            "path": str(target), "present": True, "logical_bytes": size,
            "files": files, "directories": directories,
            "complete": len(errors) == target_errors_before,
        })
    return {"logical_bytes": total, "entry_count": entries, "metadata_sha256": digest.hexdigest(), "paths": totals, "errors": errors}


def inventory(roots: Sequence[Path]) -> dict[str, Any]:
    """Return an inventory of explicitly supplied roots without writing anything."""
    started = _now()
    supplied = [_absolute(root) for root in roots]
    try:
        approved = _validated_roots(supplied)
    except (OSError, InventoryError) as exc:
        return {
            "schema_version": 1, "started_at": started, "finished_at": _now(), "complete": False,
            "roots": [str(root) for root in supplied], "logical_bytes": None,
            "errors": [str(exc)], "paths": [], "payload_contents_read": False,
        }
    first = _scan_pass(approved)
    second = _scan_pass(approved)
    errors = list(dict.fromkeys(first["errors"] + second["errors"]))
    if first["metadata_sha256"] != second["metadata_sha256"] or first["logical_bytes"] != second["logical_bytes"]:
        errors.append("metadata drift between complete inventory passes; no baseline may be initialized")
    complete = not errors
    return {
        "schema_version": 1, "started_at": started, "finished_at": _now(),
        "complete": complete, "roots": [str(root) for root in approved],
        "generated_relative_paths": list(GENERATED_RELATIVE_PATHS),
        "logical_bytes": second["logical_bytes"] if complete else None,
        "observed_logical_bytes": second["logical_bytes"], "entry_count": second["entry_count"],
        "metadata_sha256": second["metadata_sha256"], "errors": errors, "paths": second["paths"],
        "payload_contents_read": False, "passes": 2,
        "accounting": "Logical sizes in the explicit generated-output scope; not whole-folder or physical allocated space.",
    }


def _new_destination(path: Path, roots: Sequence[Path]) -> Path:
    path = _absolute(path)
    if any(_within(path, root) or _within(root, path) for root in roots):
        raise InventoryError("reports and shared state must be outside approved checkout roots")
    _chain(path, may_be_absent=True)
    if os.path.lexists(path):
        raise InventoryError(f"refusing to overwrite existing destination: {path}")
    if not path.parent.is_dir():
        raise InventoryError(f"destination parent must already exist: {path.parent}")
    return path


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    _chain(path, may_be_absent=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    try:
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
        position = 0
        while position < len(data):
            written = os.write(descriptor, data[position:])
            if written <= 0:
                raise OSError("incomplete destination write")
            position += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _initialization_lock(state: Path) -> Iterator[None]:
    lock = state.with_name(state.name + ".lock")
    _chain(lock, may_be_absent=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise InventoryError("shared state is locked; existing locks are never cleared automatically") from exc
    identity = os.fstat(descriptor)
    os.close(descriptor)
    try:
        if os.path.lexists(state):
            raise InventoryError("state already exists; initialization never overwrites or resets its baseline")
        yield
    finally:
        current = _checked_stat(lock)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise InventoryError("initialization lock identity changed; it was not removed")
        lock.unlink()


def write_inventory(*, roots: Sequence[Path], report_path: Path) -> dict[str, Any]:
    approved = [_absolute(root) for root in roots]
    report_path = _new_destination(report_path, approved)
    result = inventory(approved)
    _write_new_json(report_path, result)
    return result


def initialize(
    *, roots: Sequence[Path], report_path: Path, state_path: Path,
    retention_registries: Sequence[Path] = (),
) -> dict[str, Any]:
    """Create only a first-time baseline; do not adopt outputs or admit a build."""
    approved = [_absolute(root) for root in roots]
    report_path = _new_destination(report_path, approved)
    state_path = _new_destination(state_path, approved)
    if report_path in {state_path, state_path.with_name(state_path.name + ".lock")}:
        raise InventoryError("report must be separate from shared state and its lock")
    registries = sorted({_absolute(path) for path in retention_registries} | {state_path.parent / "retention-registry.json"})
    for registry in registries:
        _chain(registry, may_be_absent=True)
        if registry in {state_path, report_path, state_path.with_name(state_path.name + ".lock")}:
            raise InventoryError("pin registries must be separate from state, report and lock")
    with _initialization_lock(state_path):
        result = inventory(approved)
        _write_new_json(report_path, result)
        if not result["complete"]:
            raise InventoryError(f"inventory is incomplete; no state was initialized; see {report_path}")
        state = {
            "schema_version": 1, "created_at": _now(), "baseline_bytes": result["logical_bytes"],
            "growth_headroom_bytes": GROWTH_HEADROOM_BYTES, "roots": result["roots"],
            "leases": [], "owned_outputs": [], "ephemeral": False,
            "retention_registries": [str(registry) for registry in registries],
            "observed_registries": [str(registry) for registry in registries if os.path.lexists(registry)],
        }
        _write_new_json(state_path, state)
    return {"initialized": True, "state": str(state_path), "report": str(report_path), "baseline_bytes": state["baseline_bytes"], "growth_headroom_bytes": GROWTH_HEADROOM_BYTES}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "init"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, action="append", required=True, help="explicit approved E3 checkout root; repeat as needed")
        command.add_argument("--report", type=Path, required=True, help="new report outside approved checkout roots")
        if name == "init":
            command.add_argument("--state", type=Path, required=True, help="new shared nonsecurity state; never overwritten")
            command.add_argument("--retention-registry", type=Path, action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inventory":
            result = write_inventory(roots=args.root, report_path=args.report)
            print(json.dumps({"report": str(args.report), "complete": result["complete"], "logical_bytes": result["logical_bytes"]}))
            return 0 if result["complete"] else 2
        result = initialize(roots=args.root, report_path=args.report, state_path=args.state, retention_registries=args.retention_registry)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"Metadata inventory refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
