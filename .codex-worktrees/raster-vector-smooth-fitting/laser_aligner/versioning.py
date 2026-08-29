from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_MAJOR = 0
BASE_MINOR = 6
BASE_PATCH = 0
BASELINE_REVISION = "76a7e4b193bee16008bc4bb1ee9893048ca1e586"

BUILD_VERSION_ENVIRONMENT_VARIABLE = "E3_BUILD_VERSION"
RUNTIME_VERSION_ENVIRONMENT_VARIABLE = "E3_POSITIONING_SYSTEM_VERSION"
BUILD_INFO_ENVIRONMENT_VARIABLE = "E3_BUILD_INFO"


def version_from_commit_count(count: int) -> str:
    value = int(count)
    if value < 0:
        raise ValueError("Commit count cannot be negative")
    return f"{BASE_MAJOR}.{BASE_MINOR}.{BASE_PATCH + value}"


def _base_version() -> str:
    return version_from_commit_count(0)


def _repository_root(repo_root: Path | None = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[1]


def _git_version(repo_root: Path) -> str | None:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "rev-list",
                "--count",
                f"{BASELINE_REVISION}..HEAD",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return version_from_commit_count(int(completed.stdout.strip()))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def _version_from_build_info(path: Path) -> str | None:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get("version")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def build_version(repo_root: Path | None = None) -> str:
    """Return the version used when producing a new E3 build."""

    override = os.environ.get(BUILD_VERSION_ENVIRONMENT_VARIABLE, "").strip()
    if override:
        return override

    root = _repository_root(repo_root)
    git_version = _git_version(root)
    return git_version or _base_version()


def application_version(repo_root: Path | None = None) -> str:
    """Return the version presented by this running E3 application."""

    runtime_override = os.environ.get(
        RUNTIME_VERSION_ENVIRONMENT_VARIABLE, ""
    ).strip()
    if runtime_override:
        return runtime_override

    build_override = os.environ.get(BUILD_VERSION_ENVIRONMENT_VARIABLE, "").strip()
    if build_override:
        return build_override

    explicit_build_info = os.environ.get(
        BUILD_INFO_ENVIRONMENT_VARIABLE, ""
    ).strip()
    if explicit_build_info:
        resolved = _version_from_build_info(
            Path(explicit_build_info).expanduser().resolve()
        )
        if resolved:
            return resolved

    if getattr(sys, "frozen", False):
        packaged = _version_from_build_info(
            Path(sys.executable).resolve().parent / "build-info.json"
        )
        if packaged:
            return packaged

    root = _repository_root(repo_root)
    git_version = _git_version(root)
    if git_version:
        return git_version

    unpacked_build = _version_from_build_info(root / "build-info.json")
    return unpacked_build or _base_version()
