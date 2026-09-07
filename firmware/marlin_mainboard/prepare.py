"""Prepare only an untouched pinned checkout, or verify an exact prepared tree."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from firmware.marlin_material.prepare import BASELINE, OVERLAYS  # noqa: E402

MATERIAL = HERE.parent / "marlin_material"


def verify(source: Path) -> None:
    expected = json.loads((HERE / "source-files.json").read_text())
    for name, digest in expected.items():
        data = (source / name).read_text(encoding="utf-8").encode()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Preserving changed source; hash mismatch: {name}")


def prepare(source: Path) -> None:
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != BASELINE["revision"]:
        raise ValueError("The checkout is not the pinned Creality revision")
    changed = subprocess.check_output(["git", "-C", str(source), "diff", "HEAD", "--name-only"], text=True).splitlines()
    if changed:
        expected = json.loads((HERE / "source-files.json").read_text())
        if set(changed) - expected.keys():
            raise ValueError("Preserving changes outside the mainboard patch")
        verify(source)
        return
    overlays = {source / "Marlin/src" / folder / name: MATERIAL / "overlay" / name
                for name, folder in OVERLAYS.items()}
    overlays[source / "Marlin/src/gcode/temp/e3_mainboard.inc"] = HERE / "e3_mainboard.inc"
    for target, origin in overlays.items():
        if target.exists() and target.read_bytes() != origin.read_bytes():
            raise ValueError(f"Preserving existing overlay: {target}")
    for patch in [*(MATERIAL / name for name in ("baseline.patch", "material.patch", "layout.patch")),
                  HERE / "mainboard.patch"]:
        subprocess.run(["git", "-C", str(source), "apply", "--whitespace=nowarn", str(patch)], check=True)
    for target, origin in overlays.items():
        target.write_bytes(origin.read_bytes())
    verify(source)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    prepare(parser.parse_args().source.resolve())
