"""Package the two-file Pi command-capacity companion from a committed revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from .package_source_dependencies import reject_partial_raster_sources
else:
    from package_source_dependencies import reject_partial_raster_sources

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "1e904823e134c49f319f9379b4bca665bac7bcae"
FILES = ("laser_aligner/gcode/limits.py", "laser_aligner/machine/service.py")


def source(revision: str, name: str) -> bytes:
    return subprocess.check_output(
        ["git", "show", f"{revision}:{name}"], cwd=ROOT,
    ).replace(b"\r\n", b"\n")


def package(destination: Path, revision: str) -> Path:
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=ROOT, text=True,
    ).strip()
    sources = {name: source(revision, name) for name in FILES}
    reject_partial_raster_sources(sources)
    if b"MAX_STREAM_COMMANDS = 1_000_000" not in sources[FILES[0]]:
        raise ValueError("Revision lacks the one-million-command limit")
    manifest = json.dumps({
        "revision": revision,
        "predecessors": [{"revision": BASELINE, "files": {
            FILES[0]: None,
            FILES[1]: hashlib.sha256(source(BASELINE, FILES[1])).hexdigest(),
        }}],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(data).hexdigest()}
                  for name, data in sources.items()],
    }, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-raster-capacity-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (folder / "manifest.json").write_bytes(manifest)
    installer = source(revision, "scripts/install_workpiece_focus.py").decode()
    (folder / "install_raster_capacity.py").write_text(
        installer.replace("BUILD_REPLACES_MANIFEST_SHA256", digest),
        encoding="utf-8", newline="\n",
    )
    (folder / "INSTALL.md").write_text(
        "# Raster command capacity\n\n"
        f"Exact source revision: {revision}. Ceiling: 1,000,000 commands.\n"
        "Updates only the shared limit and MachineService validator; no firmware flash.\n"
        "The recorded operation-focus predecessor is required; unknown edits reject.\n"
        "Close E3 clients and confirm idle/disarmed before stopping e3-hardware-node.service.\n"
        "Run install_raster_capacity.py --project <existing-project> for read-only validation.\n"
        "After stopping the service, repeat with --apply, then verify already_current.\n"
        "Restart the service and reconnect. The installer preserves source backups and\n"
        "does not change configuration, gauge teaching, calibration, or saved Z data.\n"
        "Physical engraving quality and controller throughput require operator testing.\n",
        encoding="utf-8", newline="\n",
    )
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(package(args.destination, args.revision))
