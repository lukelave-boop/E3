#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import runpy
import subprocess
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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
_PROJECT_VERSION_RE = re.compile(r"version\s*=\s*(['\"])(?P<version>[^'\"]+)\1\s*(?:#.*)?")


def is_excluded(relative: Path) -> bool:
    if relative.as_posix() in EXCLUDED_FILES:
        return True
    for part in relative.parts:
        if part in EXCLUDED_PARTS or part.endswith((".egg-info", ".dist-info")):
            return True
    return bool(relative.parts and relative.parts[0] == "data" and relative.name != ".gitkeep")


def release_version(root: Path) -> str:
    in_project = False
    project_version: str | None = None
    for line in (root / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            if stripped == "[project]":
                in_project = True
                continue
            if in_project:
                break
        if in_project:
            match = _PROJECT_VERSION_RE.fullmatch(stripped)
            if match is not None:
                project_version = match.group("version").strip()
                break
    if not project_version:
        raise RuntimeError("pyproject.toml [project] must define a non-empty version")

    namespace: dict[str, Any] = runpy.run_path(str(root / "laser_aligner" / "__init__.py"))
    package_version = namespace.get("__version__")
    if not isinstance(package_version, str) or not package_version.strip():
        raise RuntimeError("laser_aligner.__version__ must be a non-empty string")
    if project_version != package_version.strip():
        raise RuntimeError(
            "Release version mismatch: pyproject.toml declares "
            f"{project_version!r} but laser_aligner declares {package_version.strip()!r}"
        )
    return project_version


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
