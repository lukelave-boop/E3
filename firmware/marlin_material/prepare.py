"""Prepare a pinned Marlin tree for compilation. Never open or upload to hardware."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = json.loads((HERE / "baseline.json").read_text())
EXPECTED_FILES = json.loads((HERE / "source-files.json").read_text())
OVERLAYS = {
    "e3_material_height.h": "module",
    "material_height.inc": "module",
    "G39.inc": "gcode/probe",
    "e3_startup.inc": "HAL/STM32",
}


def prepare(source: Path) -> None:
    source = source.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True,
    ).strip()
    if revision != BASELINE["revision"]:
        raise ValueError("Source revision does not match baseline.json")
    changed = subprocess.check_output(
        ["git", "-C", str(source), "diff", "HEAD", "--name-only"], text=True,
    ).splitlines()
    if set(changed) - EXPECTED_FILES.keys():
        raise ValueError("Preserving unexpected changes outside the pinned patch")
    # Each patch is independently idempotent. Unexpected edits fail git's check;
    # never reset, clean, overwrite a changed overlay, or check out another ref.
    for name in ("baseline.patch", "material.patch", "layout.patch"):
        command = ["git", "-C", str(source), "apply", "--whitespace=nowarn"]
        patch = str(HERE / name)
        if subprocess.run([*command, "--reverse", "--check", patch], capture_output=True).returncode == 0:
            continue
        subprocess.run([*command, "--check", patch], check=True)
        subprocess.run([*command, patch], check=True)
    for name, folder in OVERLAYS.items():
        data = (HERE / "overlay" / name).read_bytes()
        target = source / "Marlin" / "src" / folder / name
        if target.exists() and target.read_bytes() != data:
            raise ValueError(f"Preserving changed overlay: {target}")
        if not target.exists():
            target.write_bytes(data)
    for name, expected in EXPECTED_FILES.items():
        actual = hashlib.sha256((source / name).read_text(encoding="utf-8").encode()).hexdigest()
        if actual != expected:
            raise ValueError(f"Patched source differs from the reviewed content: {name}")
    hashes = {str(p.relative_to(HERE)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(HERE.rglob("*")) if p.suffix in {".patch", ".inc", ".h", ".json"}}
    print(json.dumps({"baseline": revision, "patch_files": hashes, "source": str(source)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    prepare(parser.parse_args().source)
