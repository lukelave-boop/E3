"""Package the two-million-command companion against the optimized Pi build."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from package_raster_capacity import ROOT, source

BASELINE = "fdfe9b22cbaa92e8c541b45cde3e558669bab4e2"
FILES = ("laser_aligner/gcode/limits.py", "laser_aligner/machine/service.py",
         "laser_aligner/machine/pi_job_service.py")


def package(destination: Path, revision: str) -> Path:
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=ROOT, text=True,
    ).strip()
    sources = {name: source(revision, name) for name in FILES}
    if b"MAX_STREAM_COMMANDS = 2_000_000" not in sources[FILES[0]]:
        raise ValueError("Revision lacks the two-million-command limit")
    manifest = json.dumps({
        "revision": revision,
        "predecessors": [{"revision": BASELINE, "files": {
            name: hashlib.sha256(source(BASELINE, name)).hexdigest() for name in FILES
        }}],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(data).hexdigest()}
                  for name, data in sources.items()],
    }, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-two-million-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (folder / "manifest.json").write_bytes(manifest)
    (folder / "install_two_million.py").write_text(
        source(revision, "scripts/install_workpiece_focus.py").decode().replace(
            "BUILD_REPLACES_MANIFEST_SHA256", digest,
        ), encoding="utf-8", newline="\n",
    )
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(package(args.destination, args.revision))
