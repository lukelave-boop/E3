from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from planning_golden_support import (  # noqa: E402
    CASE_NAMES,
    expected_case_dir,
    snapshot_case,
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _read_existing(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _update_case(case_name: str) -> None:
    snapshot = snapshot_case(case_name)
    case_dir = expected_case_dir(case_name)
    print(f"{case_name}:")

    for filename, content in snapshot.items():
        path = case_dir / filename
        previous = _read_existing(path)
        _write_lf(path, content)
        if previous is None:
            status = "created"
        elif previous == content:
            status = "unchanged"
        else:
            status = f"changed {_digest(previous)} -> {_digest(content)}"
        print(f"  {filename}: {status} ({_digest(content)})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicitly regenerate curated E3 planning golden fixtures."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--case",
        choices=CASE_NAMES,
        help="Regenerate one named planning golden case.",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="Regenerate every planning golden case.",
    )
    parser.add_argument(
        "--accept",
        action="store_true",
        help="Required acknowledgement that expected outputs will be rewritten.",
    )
    args = parser.parse_args()

    if not args.accept:
        parser.error("--accept is required to rewrite planning goldens")

    case_names = CASE_NAMES if args.all else (args.case,)
    for case_name in case_names:
        assert case_name is not None
        _update_case(case_name)

    print("\nReview the generated files with git diff before committing them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
