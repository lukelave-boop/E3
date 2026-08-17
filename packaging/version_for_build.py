from __future__ import annotations

import os
import subprocess
from pathlib import Path

BASE_MAJOR = 0
BASE_MINOR = 6
BASE_PATCH = 0
BASELINE_REVISION = "76a7e4b193bee16008bc4bb1ee9893048ca1e586"


def version_from_commit_count(count: int) -> str:
    value = int(count)
    if value < 0:
        raise ValueError("Commit count cannot be negative")
    return f"{BASE_MAJOR}.{BASE_MINOR}.{BASE_PATCH + value}"


def build_version(repo_root: Path | None = None) -> str:
    override = os.environ.get("E3_BUILD_VERSION", "").strip()
    if override:
        return override
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[1]
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--count", f"{BASELINE_REVISION}..HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return version_from_commit_count(int(completed.stdout.strip()))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return version_from_commit_count(0)


if __name__ == "__main__":
    print(build_version())
