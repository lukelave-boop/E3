"""Independent writable state for each desktop build; imports never mutate state.

The pre-isolation state is an import source, never a writable fallback. A scope
is published only after its complete first-run snapshot has been prepared.
Machine and calibration payloads remain strict and are not schema-downgraded.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from .storage import (
    atomic_write_json,
    legacy_user_data_dir,
    platform_user_data_dir,
    strict_json_loads,
)

SCOPE_MANIFEST = "state-scope.json"
_EXCLUDED_ROOT_NAMES = frozenset({"state-scopes", "updates"})
_runtime_publication: tuple[Path, Path, str, str] | None = None


class StateScopeError(ValueError):
    pass


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _regular_tree(path: Path) -> None:
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise StateScopeError(f"State import cannot follow a linked path: {path}")


def _checked_path(path: Path) -> Path:
    absolute = Path(path).expanduser().absolute()
    for component in (absolute, *absolute.parents):
        _regular_tree(component)
    return absolute.resolve()


def _snapshot_tree(source: Path, *, root: bool = False) -> dict[str, str]:
    """Detect changed, added and removed files across the snapshot transaction."""

    _regular_tree(source)
    output: dict[str, str] = {}
    if not source.exists():
        return output
    if source.is_dir():
        for child in source.iterdir():
            if root and child.name in _EXCLUDED_ROOT_NAMES:
                continue
            if child.name.endswith((".sqlite-wal", ".sqlite-shm")):
                continue
            for relative, digest in _snapshot_tree(child).items():
                output[str(Path(child.name) / relative)] = digest
    elif source.is_file():
        if source.suffix == ".sqlite":
            # The SQLite backup transaction, rather than raw DB/WAL bytes,
            # supplies its coherent committed snapshot while another app runs.
            output[""] = "sqlite-backup"
        else:
            with source.open("rb") as stream:
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            output[""] = digest.hexdigest()
    else:
        raise StateScopeError(f"State import requires ordinary files: {source}")
    return output


def _copy_tree(source: Path, destination: Path, *, root: bool = False) -> None:
    _regular_tree(source)
    if not source.exists():
        return
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            if root and child.name in _EXCLUDED_ROOT_NAMES:
                continue
            # SQLite's consistent backup includes committed WAL content.
            if child.name.endswith((".sqlite-wal", ".sqlite-shm")):
                continue
            _copy_tree(child, destination / child.name)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".sqlite":
            with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as old:
                with closing(sqlite3.connect(destination)) as new:
                    old.backup(new)
        else:
            shutil.copy2(source, destination)
    else:
        raise StateScopeError(f"State import requires ordinary files: {source}")


def _read_manifest(root: Path) -> dict[str, Any]:
    # Windows packaged-process virtualization can resolve a newly created
    # directory differently than its previously nonexistent spelling.
    root = _checked_path(root)
    try:
        payload = strict_json_loads((root / SCOPE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise StateScopeError(f"State scope is incomplete: {root}") from exc
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise StateScopeError(f"State scope has unsupported metadata: {root}")
    config = payload.get("config")
    if not isinstance(config, str) or Path(config).is_absolute():
        raise StateScopeError(f"State scope has an invalid configuration path: {root}")
    resolved = (root / config).resolve()
    if not _within(resolved, root):
        raise StateScopeError(f"State configuration escapes its scope: {root}")
    config_paths = {resolved}
    config_directory = root / "config"
    if config_directory.is_dir():
        config_paths.update(config_directory.rglob("*.json"))
    for config_path in config_paths:
        _checked_path(config_path)
        if not config_path.is_file():
            continue
        raw = strict_json_loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("app"), dict):
            continue
        value = raw["app"].get("data_dir", "data")
        if not isinstance(value, str) or not value.strip():
            raise StateScopeError(f"Invalid scoped app.data_dir: {config_path}")
        data = Path(value).expanduser()
        if not data.is_absolute():
            data = config_path.parent / data
        data = _checked_path(data)
        if not _within(data, root):
            raise StateScopeError(f"Configured data escapes its E3 state scope: {config_path}")
    return payload


def prepare_state_scope(
    scope_root: Path,
    seed_root: Path,
    *,
    config_path: Path | None = None,
    legacy_auxiliary_root: Path | None = None,
) -> Path:
    """Clone a seed once, rebase config data paths, and return its scoped config.

    Existing complete scopes win and are never refreshed from another version.
    An unreadable seed, linked tree, or failed copy prevents publication; it
    never causes a writable fallback to the source. No controller is contacted.
    """

    destination = _checked_path(scope_root)
    source = _checked_path(seed_root)
    if destination == source or _within(source, destination):
        raise StateScopeError("State source and destination must be independent")
    if _within(destination, source) and not _within(destination, source / "state-scopes"):
        raise StateScopeError("Nested state scopes must use the excluded state-scopes directory")
    if destination.exists():
        return destination / _read_manifest(destination)["config"]

    original_config = (
        _checked_path(config_path)
        if config_path is not None
        else source / "config" / "network-local.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".state-import-", dir=destination.parent))
    try:
        snapshots = [(source, True, _snapshot_tree(source, root=True))]
        _copy_tree(source, temporary, root=True)
        if legacy_auxiliary_root is not None:
            legacy = _checked_path(legacy_auxiliary_root)
            for name in ("materials.sqlite", "backups", "layer-profiles.json"):
                old = legacy / name
                if not (temporary / name).exists() and old.exists():
                    snapshots.append((old, False, _snapshot_tree(old)))
                    _copy_tree(old, temporary / name)
        # Config sidecars include taught focus and mainboard limit calibration.
        config_relative = Path("config") / original_config.name
        if _within(original_config, source):
            config_relative = original_config.relative_to(source)
        elif original_config.exists():
            snapshots.append((original_config.parent, False, _snapshot_tree(original_config.parent)))
            _copy_tree(original_config.parent, temporary / "config")

        configs: dict[Path, Path] = {}
        source_configs = source / "config"
        if source_configs.is_dir():
            configs.update(
                (path, path.relative_to(source))
                for path in source_configs.rglob("*.json")
            )
        if original_config.is_file():
            configs[original_config] = config_relative
        data_sources: dict[Path, Path] = {}
        for old_config, relative_config in configs.items():
            raw = strict_json_loads(old_config.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("app"), dict):
                continue
            app = raw["app"]
            data_value = app.get("data_dir", "data")
            if not isinstance(data_value, str) or not data_value.strip():
                raise StateScopeError(f"Invalid app.data_dir in {old_config}")
            old_data = Path(data_value).expanduser()
            if not old_data.is_absolute():
                old_data = old_config.parent / old_data
            old_data = _checked_path(old_data)
            if _within(old_data, source):
                data_relative = old_data.relative_to(source)
            else:
                digest = hashlib.sha256(os.fsencode(str(old_data))).hexdigest()[:16]
                data_relative = Path("imported-data") / digest
            if old_data not in data_sources:
                if not _within(old_data, source):
                    if _within(destination, old_data) or _within(old_data, destination):
                        raise StateScopeError("Configured data overlaps the destination scope")
                    snapshots.append((old_data, False, _snapshot_tree(old_data)))
                    _copy_tree(old_data, temporary / data_relative)
                data_sources[old_data] = data_relative
            # This is the only semantic change to copied configuration. Never
            # rewrite calibration evidence, digests, bindings, or safety limits.
            app["data_dir"] = str(destination / data_relative)
            atomic_write_json(temporary / relative_config, raw)

        for snapshot_source, snapshot_root, before in snapshots:
            if _snapshot_tree(snapshot_source, root=snapshot_root) != before:
                raise StateScopeError(
                    "E3 settings or calibration changed during state import; "
                    "close the other E3 window and try again"
                )
        atomic_write_json(
            temporary / SCOPE_MANIFEST,
            {
                "schema_version": 1,
                "source": str(source),
                "config": str(config_relative),
                "data_sources": {
                    str(old): str(relative) for old, relative in data_sources.items()
                },
            },
        )
        try:
            temporary.rename(destination)
        except OSError:
            if not destination.exists():
                raise
            _read_manifest(destination)
        return destination / _read_manifest(destination)["config"]
    except (OSError, sqlite3.Error, UnicodeError, ValueError) as exc:
        if isinstance(exc, StateScopeError):
            raise
        raise StateScopeError(f"Could not prepare independent E3 state: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def prepare_runtime_state(explicit_config: Path | None = None) -> None:
    """Select build state at launch, after imports and before reading settings."""

    global _runtime_publication
    if os.environ.get("E3_USER_STATE_DIR", "").strip():
        return
    _runtime_publication = None
    explicit = explicit_config or os.environ.get("E3_CONFIG_PATH", "").strip()
    from .deployment import application_root, load_build_info

    seed = platform_user_data_dir().resolve()
    build = load_build_info()
    application = application_root()
    if getattr(sys, "frozen", False):
        if re.fullmatch(r"[0-9a-fA-F]{7,64}", build.revision):
            identity = f"{build.version}-{build.revision}"
        else:
            digest = hashlib.sha256(os.fsencode(str(application))).hexdigest()[:20]
            identity = f"{build.version}-unidentified-{digest}"
        category = "dev-test" if os.environ.get("E3_DEV_TEST") == "1" else "installed"
    else:
        identity = hashlib.sha256(os.fsencode(str(application))).hexdigest()[:20]
        category = "source"
    if explicit:
        config_identity = hashlib.sha256(os.fsencode(str(Path(explicit).resolve()))).hexdigest()[:20]
        identity += f"-config-{config_identity}"
        category = "explicit"
    identity = re.sub(r"[^A-Za-z0-9._-]", "_", identity)[:160]
    root = seed / "state-scopes" / category / identity
    publication = None
    if category == "installed":
        publication = (root.parent / "last-successful.json", root, build.version, build.revision)
    snapshot_source = seed
    if publication is not None and not root.exists():
        previous = _previous_installed_state(publication[0], build.version)
        if previous is not None:
            snapshot_source = previous
    seed_config = snapshot_source / "config" / "network-local.json"
    if snapshot_source != seed:
        seed_config = snapshot_source / _read_manifest(snapshot_source)["config"]
    if not seed_config.is_file():
        seed_config = application / "config" / "network-local.json"
    if not seed_config.is_file():
        seed_config = application / "config" / "default.json"
    prepared = prepare_state_scope(
        root, snapshot_source, config_path=seed_config,
        legacy_auxiliary_root=legacy_user_data_dir() if snapshot_source == seed else None,
    )
    if publication is not None:
        manifest = _read_manifest(root)
        manifest["build_version"] = build.version
        manifest["build_revision"] = build.revision
        atomic_write_json(root / SCOPE_MANIFEST, manifest)
    os.environ["E3_USER_STATE_DIR"] = str(root)
    if not explicit:
        os.environ["E3_CONFIG_PATH"] = str(prepared)
    os.environ["E3_STATE_ISOLATED"] = "1"
    _runtime_publication = publication


def _numeric_version(value: str) -> tuple[int, ...] | None:
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value) is None:
        return None
    return tuple(int(part) for part in value.split("."))


def _read_test_pointer(pointer: Path) -> tuple[Path, str, str] | None:
    if not pointer.exists():
        return None
    try:
        raw = strict_json_loads(_checked_path(pointer).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version", "scope", "version", "revision",
        }:
            raise ValueError("invalid fields")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or not all(
            isinstance(raw[key], str) and raw[key] for key in ("scope", "version", "revision")
        ):
            raise ValueError("invalid values")
        relative = Path(raw["scope"])
        if relative.is_absolute() or len(relative.parts) != 3 or any(
            part in {".", ".."} for part in relative.parts
        ):
            raise ValueError("invalid scope")
        scope = _checked_path(pointer.parent / relative)
        if scope.parents[2] != pointer.parent.resolve():
            raise ValueError("scope escapes test data")
        manifest = _read_manifest(scope)
        if manifest.get("test_version") != raw["version"] or manifest.get("test_revision") != raw["revision"]:
            raise ValueError("test scope identity differs")
        return scope, raw["version"], raw["revision"]
    except (OSError, ValueError, UnicodeError) as exc:
        raise StateScopeError("The previous prepared E3 test state pointer is invalid") from exc


def _write_test_identity(scope: Path, version: str, revision: str) -> None:
    manifest = _read_manifest(scope)
    for key, expected in (("test_version", version), ("test_revision", revision)):
        if key in manifest and manifest[key] != expected:
            raise StateScopeError("Existing E3 test state belongs to a different build")
    if manifest.get("test_version") == version and manifest.get("test_revision") == revision:
        return
    manifest["test_version"] = version
    manifest["test_revision"] = revision
    atomic_write_json(scope / SCOPE_MANIFEST, manifest)


def _publish_test_pointer(pointer: Path, scope: Path, version: str, revision: str) -> None:
    previous = _read_test_pointer(pointer)
    if previous is not None:
        old_version = _numeric_version(previous[1])
        new_version = _numeric_version(version)
        if old_version is not None and (
            new_version is None or old_version > new_version
            or (old_version == new_version and previous[0] != scope)
        ):
            return
    atomic_write_json(pointer, {
        "schema_version": 1, "scope": str(scope.relative_to(pointer.parent)),
        "version": version, "revision": revision,
    })


def prepare_test_state(
    scope_root: Path,
    seed_root: Path,
    *,
    version: str,
    revision: str,
    config_path: Path | None = None,
    legacy_auxiliary_root: Path | None = None,
) -> Path:
    """Carry test edits forward without mixing them into installed state.

    The channel pointer records completed preparation, not application or
    hardware verification. An established exact build opens independently of
    the pointer. Only a strictly newer numeric version imports its predecessor;
    unseen older or same-version revisions use the supplied conservative seed.
    """

    scope = _checked_path(scope_root)
    if len(scope.parents) < 3:
        raise StateScopeError("E3 test state requires its version/local/data directory layout")
    channel = scope.parents[2]
    expected_directory = f"{version}-{revision}"
    if (
        not version or not revision
        or scope.parent.name != "local"
        or scope.parent.parent.name.casefold() != expected_directory.casefold()
    ):
        raise StateScopeError("E3 test state path does not match its build identity")
    pointer = channel / "last-prepared-test.json"
    if scope.exists():
        manifest = _read_manifest(scope)
        _write_test_identity(scope, version, revision)
        try:
            with _publication_lock(pointer):
                _publish_test_pointer(pointer, scope, version, revision)
        except (OSError, StateScopeError):
            logging.getLogger(__name__).exception("Could not record E3 test state for the next test build")
        return scope / manifest["config"]

    channel.mkdir(parents=True, exist_ok=True)
    try:
        with _publication_lock(pointer):
            previous = _read_test_pointer(pointer)
            source = Path(seed_root)
            source_config = config_path
            legacy = legacy_auxiliary_root
            if previous is not None:
                old_version = _numeric_version(previous[1])
                new_version = _numeric_version(version)
                if old_version is not None and new_version is not None and old_version < new_version:
                    source = previous[0]
                    source_config = source / _read_manifest(source)["config"]
                    legacy = None
            prepared = prepare_state_scope(
                scope, source, config_path=source_config,
                legacy_auxiliary_root=legacy,
            )
            _write_test_identity(scope, version, revision)
            _publish_test_pointer(pointer, scope, version, revision)
            return prepared
    except OSError as exc:
        raise StateScopeError(
            "Another E3 test state preparation may be in progress; try again after it finishes"
        ) from exc


def _read_installed_pointer(pointer: Path) -> tuple[Path, str, str] | None:
    if not pointer.exists():
        return None
    try:
        raw = strict_json_loads(pointer.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "scope", "version", "revision"}:
            raise ValueError("invalid fields")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or not all(
            isinstance(raw[key], str) and raw[key] for key in ("scope", "version", "revision")
        ):
            raise ValueError("invalid values")
        relative = Path(raw["scope"])
        if relative.is_absolute() or len(relative.parts) != 1 or relative.name in {".", ".."}:
            raise ValueError("invalid scope")
        scope = _checked_path(pointer.parent / relative)
        if scope.parent != pointer.parent.resolve():
            raise ValueError("scope escapes installed data")
        manifest = _read_manifest(scope)
        if manifest.get("build_version") != raw["version"] or manifest.get("build_revision") != raw["revision"]:
            raise ValueError("scope build identity differs")
        return scope, raw["version"], raw["revision"]
    except (OSError, ValueError, UnicodeError) as exc:
        raise StateScopeError("The previous installed E3 state pointer is invalid") from exc


def _previous_installed_state(pointer: Path, version: str) -> Path | None:
    previous = _read_installed_pointer(pointer)
    if previous is None:
        return None
    previous_version = _numeric_version(previous[1])
    current_version = _numeric_version(version)
    if previous_version is not None and current_version is not None and previous_version < current_version:
        return previous[0]
    # A downgrade or unorderable version never imports a newer schema.
    return None


def record_runtime_startup() -> None:
    """Make this installed version the next update's source only after UI startup."""

    if _runtime_publication is None:
        return
    pointer, scope, version, revision = _runtime_publication
    try:
        with _publication_lock(pointer):
            previous = _read_installed_pointer(pointer)
            if previous is not None:
                old_version = _numeric_version(previous[1])
                new_version = _numeric_version(version)
                if old_version is not None and (
                    new_version is None or old_version > new_version
                    or (old_version == new_version and previous[0] != scope)
                ):
                    return
            atomic_write_json(pointer, {
                "schema_version": 1, "scope": scope.name,
                "version": version, "revision": revision,
            })
    except (OSError, StateScopeError):
        # A damaged shared import pointer must not prevent an established
        # version from opening its own complete, independent state.
        logging.getLogger(__name__).exception("Could not record E3 state for the next update")


@contextmanager
def _publication_lock(pointer: Path):
    """Do not let an older simultaneous startup replace a newer import source."""

    with pointer.with_suffix(".lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
