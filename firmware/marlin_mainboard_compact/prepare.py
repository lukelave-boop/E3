"""Prepare an untouched pinned Creality tree or verify the exact compact profile."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASELINE = json.loads((HERE.parent / "marlin_material/baseline.json").read_text())
OVERLAYS = {
    "Marlin/src/module/e3_material_height.h": HERE.parent / "marlin_material/overlay/e3_material_height.h",
    "Marlin/src/module/material_height.inc": HERE.parent / "marlin_material/overlay/material_height.inc",
    "Marlin/src/gcode/probe/G39.inc": HERE.parent / "marlin_material/overlay/G39.inc",
    "Marlin/src/HAL/STM32/e3_startup.inc": HERE.parent / "marlin_material/overlay/e3_startup.inc",
    "Marlin/src/gcode/temp/e3_mainboard.inc": HERE.parent / "marlin_mainboard/e3_mainboard.inc",
    "Marlin/src/gcode/control/e3_usb_update.inc": HERE / "marlin_usb_update.inc",
}


def verify(source: Path) -> None:
    expected = json.loads((HERE / "source-files.json").read_text())
    for name, digest in expected.items():
        if hashlib.sha256((source / name).read_text(encoding="utf-8").encode()).hexdigest() != digest:
            raise ValueError(f"Preserving changed source; compact profile hash mismatch: {name}")


def prepare(source: Path) -> None:
    source = source.resolve()
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != BASELINE["revision"]:
        raise ValueError("Use the pinned Creality source revision")
    expected = json.loads((HERE / "source-files.json").read_text())
    changed = subprocess.check_output(["git", "-C", str(source), "diff", "HEAD", "--name-only"], text=True).splitlines()
    untracked = subprocess.check_output(["git", "-C", str(source), "ls-files", "--others", "--exclude-standard"], text=True).splitlines()
    if changed:
        if (set(changed) | set(untracked)) - expected.keys():
            raise ValueError("Preserving source changes outside the compact profile")
        verify(source)
        return
    if untracked:
        raise ValueError("Preserving untracked source files; use a clean isolated checkout")
    # Windows checkout may turn the patch itself into CRLF while the pinned
    # source's .gitattributes requires LF for C/C++ and native endings for INI.
    # Pass a canonical patch over stdin; let Git honor each source file's
    # attributes rather than changing checkout settings or rewriting the tree.
    patch = (HERE / "compact.patch").read_bytes().replace(b"\r\n", b"\n")
    # Some upstream blobs may store literal CRLF without a text clean filter.
    # Ignore context-line whitespace for matching only; the pinned-clean-tree
    # checks above and exact resulting hashes below still reject source edits.
    apply = ["git", "-C", str(source), "apply", "--ignore-space-change"]
    subprocess.run([*apply, "--check", "-"], input=patch, check=True)
    subprocess.run([*apply, "--whitespace=nowarn", "-"], input=patch, check=True)
    for relative, origin in OVERLAYS.items():
        (source / relative).write_bytes(origin.read_bytes())
    verify(source)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    prepare(parser.parse_args().source)
