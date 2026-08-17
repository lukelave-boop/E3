from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one CI phase and report its duration.")
    parser.add_argument("label")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    started = time.perf_counter()
    try:
        result = subprocess.run(command, check=False)
    finally:
        elapsed = time.perf_counter() - started
        timing = f"{args.label}: {elapsed:.2f} seconds"
        print(f"::notice title=CI timing::{timing}", flush=True)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as summary:
                summary.write(f"- {timing}\n")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
