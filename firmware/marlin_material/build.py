"""Build the pinned auxiliary Marlin profile; never select an upload target."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from prepare import BASELINE, prepare


def verify_packages(core: Path) -> None:
    for name, expected in BASELINE["packages"].items():
        metadata = json.loads((core / "packages" / name / "package.json").read_text())
        if metadata["version"] != expected:
            raise ValueError(f"Unpinned dependency {name}: {metadata['version']} != {expected}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if version("platformio") != BASELINE["platformio"]:
        raise ValueError("Install the pinned PlatformIO version from baseline.json")
    if not args.check_only:
        prepare(args.source)
        subprocess.run([sys.executable, "-m", "platformio", "run", "-d", str(args.source),
                        "-e", BASELINE["environment"], "-j", "4"],
                       env={**os.environ, "PLATFORMIO_CORE_DIR": str(args.core.resolve())}, check=True)
    verify_packages(args.core)
    print("Pinned PlatformIO dependencies verified.")
