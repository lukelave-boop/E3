#!/usr/bin/env python3
from pathlib import Path

from laser_aligner.calibration.targets import write_default_targets


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for generated in write_default_targets(root / "targets"):
        print(generated)
