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


def source(revision, name):
    return subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=ROOT).replace(b"\r\n", b"\n")


def package(destination, revision, version):
    revision = subprocess.check_output(["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
                                       cwd=ROOT, text=True).strip()
    sources = {name: source(revision, name) for name in PREVIOUS}
    integrated = {name: hashlib.sha256(source(INTEGRATED_REVISION, name)).hexdigest() for name in PREVIOUS}
    manifest = json.dumps({
        "revision": revision, "compatible_windows_version": version,
        "required_capability": "pi-thickness-focus-v1",
        "predecessors": [
            {"revision": "installed-workpiece-focus-568b1cc9", "files": PREVIOUS},
            {"revision": INTEGRATED_REVISION, "files": integrated},
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

Use E3 DEV TEST {version}, revision `{revision}`, with this exact companion.
It includes the integrated faster-Z and first-app-priority sources. Normal Z
travel still requires `Cap:E3_Z_SETUP_SPEED_V1:1`; use the previously supplied
matching speed firmware. This installer does not flash firmware or operate hardware.
If the speed firmware is already installed, this focus rule needs no new flash
or gauge teaching. A firmware identity change still requires reference and teaching.

Accepted predecessors: the recorded installed 568b1cc9 companion or the integrated
speed/priority sources at `{INTEGRATED_REVISION}`. Unknown edits reject. Operator
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
