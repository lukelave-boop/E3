"""Compile the compact F401 application offline; never upload or open hardware."""

from __future__ import annotations

import argparse
import json
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
    for name, expected in BASELINE["packages"].items():
        if json.loads((args.core / "packages" / name / "package.json").read_text())["version"] != expected:
            raise ValueError(f"Dependency does not match the pin: {name}")
    binary = args.source / ".pio/build" / BASELINE["environment"] / "firmware.bin"
    if not 408 <= binary.stat().st_size <= 0x1FE00:
        raise ValueError("Application payload does not fit the retained updater's 256 KiB layout")


if __name__ == "__main__":
    main()
