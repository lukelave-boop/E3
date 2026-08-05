#!/usr/bin/env python3
from __future__ import annotations

import zipfile
from pathlib import Path

VERSION = "0.1.0"
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
EXCLUDED_FILES = {"config/local.json", ".coverage"}


def is_excluded(relative: Path) -> bool:
    if relative.as_posix() in EXCLUDED_FILES:
        return True
    for part in relative.parts:
        if part in EXCLUDED_PARTS or part.endswith((".egg-info", ".dist-info")):
            return True
    return bool(relative.parts and relative.parts[0] == "data" and relative.name != ".gitkeep")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / f"laser-camera-aligner-{VERSION}.zip"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or is_excluded(relative):
                continue
            archive.write(path, Path("laser-camera-aligner") / relative)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
