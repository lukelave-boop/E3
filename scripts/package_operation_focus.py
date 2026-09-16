"""Freeze the raster surface-focus companion for the verified precision baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY = "pi-operation-focus-v1"
BASELINE_REVISION = "d8766fbc393dbd302718a9e431227fcfc2ed18a0"
PREDECESSOR = {'laser_aligner/config.py': 'f12b0135c68ee8e787cf316294c15a51451f9ec64338b85f2db9f0c2929f9f58',
 'laser_aligner/gcode/preview.py': '2374b695f140e98b95edd6d21cc4989da36061944ab68af67815dab7420444eb',
 'laser_aligner/machine/focus_bounds.py': '9d2f2403c38f50bf20b35bf364de54747128c131bb816ae46534ce6bceb93d5c',
 'laser_aligner/machine/job_focus.py': 'a8548e916aef62ea3e9715c11bd2c663a1e20898de6a690be1685aefe41412c5',
 'laser_aligner/machine/laser_focus.py': '16a68b1e82d807772929f6e68c907055eb8ff438e53e0c05cc1f68b42742dfe3',
 'laser_aligner/machine/mainboard.py': '4a6b5e63816b7b11cd37751f7ea01d3a4c6f422733da7b9c6f28fa99347e39f5',
 'laser_aligner/machine/material_surface.py': 'a0fb818ac12ea51492ef8b0bf2caf53031d8d2682772179be45a88e6ca82e276',
 'laser_aligner/machine/pi_job_protocol.py': 'a39634447897c97bfd32566e5200baee1849024d0f0645af577430733ffd7290',
 'laser_aligner/machine/pi_job_service.py': '9f847f4468a17fbd93796c173b1e6c32388399cb3f619ebcee28ee36c7e08469',
 'laser_aligner/machine/pi_machine_server.py': 'e4cc76c5236e41b3d3548d024f40ff858788cf495a9fe29903a4d109810ae366',
 'laser_aligner/machine/remote_service.py': '273691612369e39a94c91b84740e6ebef2ec70024a9c894d9400f1002d2df8e1',
 'laser_aligner/machine/secondary_controller.py': '788420240b542f1280343fc0677590510d2b13c395988871d2b75e67d88b08f2',
 'laser_aligner/machine/secondary_startup.py': 'd7c6bc19862d8f3dd5fd638581860057189956e4314157efb92f317298eb258b',
 'laser_aligner/machine/service.py': '7222dd5b1001d6c54f603fad2891582b1edfa6f574ac9ef0993ac5d29422cbc5',
 'laser_aligner/machine/setup_motion.py': 'f40132601ff1981da6b693a335ad673531434de32cd5ad0247c160f34d075775',
 'laser_aligner/machine/z_limits.py': 'c1ccb0ec6f82d7489d49dd50bdaf295a75a20b22117d3cad0ee56298c3b00310',
 'laser_aligner/machine/z_probe.py': 'fc45ce6f42c01b1cdcab0c6935bf2e876317b369c9c88f862ce28fdecf628ec4',
 'laser_aligner/machine/z_retention.py': 'f6f5ac717b12272365b8635b4c4874824e644d42594a99e0e7c94b1dd88ee812',
 'laser_aligner/operation_focus.py': None,
 'laser_aligner/remote_node.py': '474325070988bf6e0323a4c8874e843438166ad81e569d9d862de580e7cf7088'}


def source(revision: str, name: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=ROOT).replace(b"\r\n", b"\n")


def package(destination: Path, revision: str, version: str) -> Path:
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=ROOT, text=True,
    ).strip()
    sources = {name: source(revision, name) for name in PREDECESSOR}
    if any(CAPABILITY.encode() not in sources[name] for name in (
        "laser_aligner/operation_focus.py", "laser_aligner/machine/pi_machine_server.py",
    )):
        raise ValueError("Revision lacks operation-focus support")
    manifest = json.dumps({
        "revision": revision, "compatible_windows_version": version, "required_capability": CAPABILITY,
        "predecessors": [{"revision": BASELINE_REVISION, "files": PREDECESSOR}],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(data).hexdigest()}
                  for name, data in sources.items()],
    }, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-operation-focus-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (folder / "manifest.json").write_bytes(manifest)
    installer = source(revision, "scripts/install_workpiece_focus.py").decode()
    (folder / "install_operation_focus.py").write_text(
        installer.replace("BUILD_REPLACES_MANIFEST_SHA256", digest), encoding="utf-8", newline="\n",
    )
    (folder / "INSTALL.md").write_text(
        f"# Raster surface-focus companion\n\nDesktop {version}; exact source {revision}.\n"
        f"Requires capability {CAPABILITY}. Source-only update; no firmware flash.\n"
        "Raster uses 7 mm above the measured surface; cut/vector and Fill remain unchanged.\n"
        "Close E3 clients and confirm idle/disarmed before stopping e3-hardware-node.service.\n"
        "Run install_operation_focus.py --project <existing-project> for a read-only preview.\n"
        "After stopping the service, repeat with --apply, verify already-current, then restart.\n"
        "Unknown source edits reject; replacements receive adjacent backups. Configuration,\n"
        "gauge teaching, probe offsets, Z limits and honeycomb data are preserved.\n"
        "Reconnect, establish the required references and measure the workpiece again.\n"
        "Automated checks do not constitute physical focus qualification.\n",
        encoding="utf-8", newline="\n",
    )
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(package(args.destination, args.revision, args.version))


if __name__ == "__main__":
    main()
