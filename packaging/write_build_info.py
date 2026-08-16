from __future__ import annotations

import argparse
import json
from pathlib import Path

from laser_aligner import __version__


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Write packaged E3 build metadata")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--revision", required=True)
    result.add_argument("--channel", default="development")
    result.add_argument("--repository", default="lukelave-boop/E3")
    result.add_argument("--release-tag", default="e3-development")
    result.add_argument("--platform-key", required=True)
    return result


def main() -> int:
    arguments = parser().parse_args()
    manifest_url = (
        f"https://github.com/{arguments.repository}/releases/download/"
        f"{arguments.release_tag}/update-manifest.json"
    )
    payload = {
        "schema_version": 1,
        "version": __version__,
        "revision": arguments.revision.strip().lower(),
        "channel": arguments.channel,
        "repository": arguments.repository,
        "manifest_url": manifest_url,
        "platform_key": arguments.platform_key,
        "packaged": True,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
