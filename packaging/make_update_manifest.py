from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from laser_aligner import __version__


def _asset(
    path: Path,
    repository: str,
    release_tag: str,
) -> dict[str, object]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "name": path.name,
        "url": (
            f"https://github.com/{repository}/releases/download/"
            f"{release_tag}/{path.name}"
        ),
        "sha256": digest,
        "size": path.stat().st_size,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Create E3 update manifest")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--revision", required=True)
    result.add_argument("--channel", default="development")
    result.add_argument("--repository", default="lukelave-boop/E3")
    result.add_argument("--release-tag", default="e3-development")
    result.add_argument("--windows", type=Path, required=True)
    result.add_argument("--linux", type=Path, required=True)
    return result


def main() -> int:
    arguments = parser().parse_args()
    payload = {
        "schema_version": 1,
        "version": __version__,
        "revision": arguments.revision.strip().lower(),
        "channel": arguments.channel,
        "published_at": datetime.now(UTC).isoformat(),
        "assets": {
            "windows-x86_64": _asset(
                arguments.windows,
                arguments.repository,
                arguments.release_tag,
            ),
            "linux-x86_64": _asset(
                arguments.linux,
                arguments.repository,
                arguments.release_tag,
            ),
        },
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
