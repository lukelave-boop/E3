from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

_COLLIDING_DLL_NAMES = (
    "icuuc.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
)
_FORBIDDEN_BUNDLE_NAMES = (
    "icuuc.dll",
    "icudt78.dll",
    "libcrypto-3-x64.dll",
    "libssl-3-x64.dll",
)


def _normalized_path(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _is_within(path: Path, root: Path) -> bool:
    try:
        _normalized_path(path).relative_to(_normalized_path(root))
    except ValueError:
        return False
    return True


def sanitize_build_path(
    path_value: str,
    *,
    system_root: Path,
) -> tuple[str, tuple[str, ...]]:
    kept: list[str] = []
    removed: list[str] = []
    for raw_entry in path_value.split(os.pathsep):
        entry = raw_entry.strip()
        if not entry:
            continue
        candidate = Path(entry.strip('"'))
        has_collision = False
        if not _is_within(candidate, system_root):
            for name in _COLLIDING_DLL_NAMES:
                try:
                    if (candidate / name).is_file():
                        has_collision = True
                        break
                except OSError:
                    continue
        if has_collision:
            removed.append(entry)
        else:
            kept.append(entry)
    return os.pathsep.join(kept), tuple(removed)


def forbidden_bundle_dlls(internal_root: Path) -> tuple[Path, ...]:
    found = {
        path
        for name in _FORBIDDEN_BUNDLE_NAMES
        if (path := internal_root / name).is_file()
    }
    return tuple(sorted(found, key=lambda path: path.name.casefold()))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    sanitize = subparsers.add_parser("sanitize-path")
    sanitize.add_argument("--system-root", type=Path, required=True)
    validate = subparsers.add_parser("validate-bundle")
    validate.add_argument("--internal-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.command == "sanitize-path":
        sanitized, removed = sanitize_build_path(
            os.environ.get("PATH", ""),
            system_root=arguments.system_root,
        )
        for entry in removed:
            print(f"Excluding foreign DLL directory from build PATH: {entry}", file=sys.stderr)
        print(sanitized)
        return 0

    found = forbidden_bundle_dlls(arguments.internal_root)
    if found:
        print(
            "Unexpected foreign runtime DLLs were collected into the E3 bundle:",
            file=sys.stderr,
        )
        for path in found:
            print(f"  {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
