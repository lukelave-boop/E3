from __future__ import annotations

import argparse
from pathlib import Path

from laser_aligner.dev_test_launcher import FeatureBuild, write_feature_pointer


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Atomically select the E3 build used by E3 DEV TEST"
    )
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--name", required=True)
    result.add_argument("--version", required=True)
    result.add_argument("--branch", required=True)
    result.add_argument("--revision", required=True)
    result.add_argument("--exe", type=Path, required=True)
    return result


def main() -> int:
    arguments = parser().parse_args()
    destination = write_feature_pointer(
        arguments.output,
        FeatureBuild(
            name=arguments.name,
            version=arguments.version,
            branch=arguments.branch,
            revision=arguments.revision,
            exe=arguments.exe,
        ),
    )
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
