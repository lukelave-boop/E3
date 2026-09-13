"""Freeze a thickness-focus Pi companion from an exact Git revision."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from .package_z_setup_speed import PREVIOUS
else:
    from package_z_setup_speed import PREVIOUS

ROOT = Path(__file__).resolve().parents[1]
INTEGRATED_REVISION = "14c62d07b28722410bacb5f5e794279e21a62d4c"
# Pinned complete integrated source set; packaging also works in shallow CI checkouts.
INTEGRATED = {
    "laser_aligner/machine/setup_motion.py": "f40132601ff1981da6b693a335ad673531434de32cd5ad0247c160f34d075775",
    "laser_aligner/machine/pi_job_protocol.py": "a39634447897c97bfd32566e5200baee1849024d0f0645af577430733ffd7290",
    "laser_aligner/config.py": "f12b0135c68ee8e787cf316294c15a51451f9ec64338b85f2db9f0c2929f9f58",
    "laser_aligner/machine/mainboard.py": "4a6b5e63816b7b11cd37751f7ea01d3a4c6f422733da7b9c6f28fa99347e39f5",
    "laser_aligner/machine/service.py": "4bf818d2a4b1bddd80f9c6b6bb9cf6da80543a38c8dd2467d37b93e7dbf350e9",
    "laser_aligner/machine/remote_service.py": "663c33a9d62032705e2516ba92b35f60a6c713129151e34748ac26ed28c8e47d",
    "laser_aligner/machine/pi_job_service.py": "f36f0841b67c48074dacefad05a1de242325675d398ce7eed12301f651d07da3",
    "laser_aligner/machine/pi_machine_server.py": "1495e6e957901bd15c6d4e3d15f75d048183bd8582edf6260d4e6568f588933a",
    "laser_aligner/remote_node.py": "474325070988bf6e0323a4c8874e843438166ad81e569d9d862de580e7cf7088",
    "laser_aligner/machine/z_limits.py": "c1ccb0ec6f82d7489d49dd50bdaf295a75a20b22117d3cad0ee56298c3b00310",
    "laser_aligner/machine/z_probe.py": "fc45ce6f42c01b1cdcab0c6935bf2e876317b369c9c88f862ce28fdecf628ec4",
    "laser_aligner/machine/secondary_controller.py": "788420240b542f1280343fc0677590510d2b13c395988871d2b75e67d88b08f2",
    "laser_aligner/machine/secondary_startup.py": "d7c6bc19862d8f3dd5fd638581860057189956e4314157efb92f317298eb258b",
    "laser_aligner/machine/laser_focus.py": "aee6eb79f30bec7545b114c4324ba0c51c36ee18dd8154909502eb5323df49a8",
    "laser_aligner/machine/focus_bounds.py": "9d2f2403c38f50bf20b35bf364de54747128c131bb816ae46534ce6bceb93d5c",
    "laser_aligner/machine/job_focus.py": "416ca7900d4156617083d957577f45b966fc3734865755d8605c8359991633cc",
    "laser_aligner/machine/z_retention.py": "f6f5ac717b12272365b8635b4c4874824e644d42594a99e0e7c94b1dd88ee812"
}
THICKNESS_REVISION = '7ef632d4246bbe4aab9d64a65b1c55af60aaa2fd'
THICKNESS = {
    **INTEGRATED,
    'laser_aligner/machine/service.py': 'a4c7aaba8311efd9284ead5c2c485be2ab38086ae0ee532d553695792e295a2c',
    'laser_aligner/machine/remote_service.py': 'b118ead440f39b29dd8e93b58a30d3dd001090bd0483cc5da9b5c1a36b70a9d7',
    'laser_aligner/machine/pi_machine_server.py': 'b7299b102841d4a530a8a5653eb4edb08243f270fb6379d6cdf3612dde757d21',
    'laser_aligner/machine/laser_focus.py': '3f1610cbe953f8e34301b038d74c9577109622b8d53bdf42a210ad697680b5d7',
    'laser_aligner/machine/job_focus.py': '54c7dbfcd86a79de20dd14948f59dd4a73d3e7d2e2a7968d0a4c5204bd1ae653',
}



def source(revision, name):
    return subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=ROOT).replace(b"\r\n", b"\n")


def package(destination, revision, version):
    revision = subprocess.check_output(["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
                                       cwd=ROOT, text=True).strip()
    sources = {name: source(revision, name) for name in PREVIOUS}
    manifest = json.dumps({
        "revision": revision, "compatible_windows_version": version,
        "required_capability": "pi-thickness-focus-v1",
        "predecessors": [
            {"revision": "installed-workpiece-focus-568b1cc9", "files": PREVIOUS},
            {"revision": INTEGRATED_REVISION, "files": INTEGRATED},
            {"revision": THICKNESS_REVISION, "files": THICKNESS},
        ],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(content).hexdigest()}
                  for name, content in sources.items()],
    }, indent=2).encode("utf-8")
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-thickness-focus-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest)
    template = source(revision, "scripts/install_workpiece_focus.py").decode("utf-8")
    (folder / "install_thickness_focus.py").write_text(
        template.replace("BUILD_REPLACES_MANIFEST_SHA256", digest), encoding="utf-8", newline="\n")
    (folder / "LASER_FOCUS.md").write_bytes(source(revision, "docs/LASER_FOCUS.md"))
    windows_source = str(folder.resolve()).replace("'", "''")
    guide = f"""# Install automatic thickness-derived focus

Use E3 DEV TEST {version} with this exact Pi companion, revision `{revision}`.
The companion fixes a focus/cooling lock inversion that could block a teaching
jog before motion. The existing Windows 0.7.133 build remains compatible.
It includes the integrated faster-Z and first-app-priority sources. Normal Z
travel still requires `Cap:E3_Z_SETUP_SPEED_V1:1`; use the previously supplied
matching speed firmware. This installer does not flash firmware or operate hardware.
If the speed firmware is already installed, this focus rule needs no new flash
or gauge teaching. A firmware identity change still requires reference and teaching.

Accepted predecessors: the recorded installed 568b1cc9 companion or the integrated
speed/priority sources at `{INTEGRATED_REVISION}`, or the thickness-focus sources
at `{THICKNESS_REVISION}`. Unknown edits reject. Operator
configuration, calibration and saved-Z data are preserved; replaced sources are
backed up. Default installation is a read-only preview.

Copy from Windows PowerShell:

```powershell
scp -r '{windows_source}' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/
```

In Pi Bash:

```sh
e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner
e3_focus=/home/greenhouse-climate/{folder.name}
"$e3_project/.venv/bin/python" "$e3_focus/install_thickness_focus.py" --project "$e3_project"
```

Only after the preview accepts every source, with the machine idle and both apps
disconnected, apply in the same Pi Bash session:

```sh
sudo systemctl stop e3-hardware-node.service &&
sudo systemctl reset-failed e3-hardware-node.service &&
"$e3_project/.venv/bin/python" "$e3_focus/install_thickness_focus.py" --project "$e3_project" --apply &&
sudo systemctl start e3-hardware-node.service
```

Open **E3 DEV TEST**. Establish the required reference, enter total spacers (zero
for material directly on the honeycomb), and measure a flat workpiece. Check the
reported thickness, calculated gap and focus Z. The selected rig datum is -1.5 mm
relative to the black border. The gap is `max(3, 7 - 2*thickness/3)` mm: 1.5 mm
material gives 6 mm; 3 mm gives 5 mm; 4 mm gives 4.333 mm; 6 mm and thicker gives
3 mm. Clear measurement before changing spacers. Remove the gauge before travel.
Verify physical clearance and focus before a supervised material trial. Automated
tests do not establish dimensional accuracy or cutting performance.
"""
    (folder / "INSTALL.md").write_text(guide, encoding="utf-8", newline="\n")
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--destination", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(package(args.destination, args.revision, args.version))
