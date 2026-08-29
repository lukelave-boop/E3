#!/usr/bin/env python3
from __future__ import annotations

import os
import runpy
import subprocess
import zipfile
from collections.abc import Iterable
from pathlib import Path

EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "htmlcov",
    "build",
    "dist",
}
EXCLUDED_FILES = {
    "config/local.json",
    ".coverage",
    "label-sheet-test.png",
    "trace-preview.png",
    "trace-result.json",
}


def is_excluded(relative: Path) -> bool:
    if relative.as_posix() in EXCLUDED_FILES:
        return True
    for part in relative.parts:
        if part in EXCLUDED_PARTS or part.endswith((".egg-info", ".dist-info")):
            return True
    return bool(relative.parts and relative.parts[0] == "data" and relative.name != ".gitkeep")


def release_version(root: Path) -> str:
    namespace = runpy.run_path(str(root / "laser_aligner" / "versioning.py"))
    resolver = namespace.get("build_version")
    if not callable(resolver):
        raise RuntimeError("laser_aligner.versioning.build_version must be callable")
    version = resolver(root)
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("E3 shared version resolver returned an empty version")
    return version.strip()


def git_tracked_paths(root: Path) -> tuple[Path, ...]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "Release archives must be built from a Git checkout with git available"
        ) from exc
    return tuple(
        Path(os.fsdecode(item))
        for item in result.stdout.split(b"\0")
        if item
    )


def release_files(root: Path, tracked_paths: Iterable[Path]) -> tuple[tuple[Path, Path], ...]:
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"Release checkout is unavailable: {root}") from exc
    files: list[tuple[Path, Path]] = []
    for relative in sorted(tracked_paths, key=lambda item: item.as_posix()):
        if relative.anchor or ".." in relative.parts:
            raise RuntimeError(f"Invalid tracked release path: {relative}")
        if is_excluded(relative):
            continue
        source = root / relative
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise RuntimeError(
                    f"Release archives do not permit symbolic links: {relative}"
                )
        try:
            resolved_source = source.resolve(strict=True)
            resolved_source.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Tracked release file resolves outside the checkout: {relative}"
            ) from exc
        if not resolved_source.is_file():
            raise RuntimeError(f"Tracked release file is missing or not regular: {relative}")
        files.append((resolved_source, relative))
    return tuple(files)


def build_release_archive(
    root: Path,
    output: Path,
    *,
    tracked_paths: Iterable[Path] | None = None,
) -> Path:
    manifest = release_files(
        root,
        git_tracked_paths(root) if tracked_paths is None else tracked_paths,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source, relative in manifest:
            archive.write(source, Path("laser-camera-aligner") / relative)
    return output


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    version = release_version(root)
    output = root / "dist" / f"laser-camera-aligner-{version}.zip"
    build_release_archive(root, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
