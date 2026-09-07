"""Compile the mainboard application without selecting any hardware operation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from prepare import BASELINE, prepare


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--core", type=Path, required=True)
    args = parser.parse_args()
    if version("platformio") != BASELINE["platformio"]:
        raise ValueError("The pinned PlatformIO version is required")
    prepare(args.source.resolve())
    subprocess.run([sys.executable, "-m", "platformio", "run", "-d", str(args.source),
                    "-e", BASELINE["environment"], "-j", "4"],
                   env={**os.environ, "PLATFORMIO_CORE_DIR": str(args.core.resolve())}, check=True)
    import json
    for name, expected in BASELINE["packages"].items():
        if json.loads((args.core / "packages" / name / "package.json").read_text())["version"] != expected:
            raise ValueError(f"Dependency does not match the pin: {name}")


if __name__ == "__main__":
    main()
