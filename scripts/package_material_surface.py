"""Freeze the material-plane Pi companion from an exact committed revision."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from . import package_thickness_focus as previous
else:
    import package_thickness_focus as previous

ROOT = Path(__file__).resolve().parents[1]
CAPABILITY = "pi-material-surface-v1"
SURFACE_MODULE = "laser_aligner/machine/material_surface.py"
PREVIEW_MODULE = "laser_aligner/gcode/preview.py"
STOP_RECOVERY_REVISION = "54f199efd33c2088c2fc14d12771b943e1032d65"
STOP_RECOVERY = {
    **previous.HOME_COOLING,
    "laser_aligner/machine/pi_job_service.py": "9f847f4468a17fbd93796c173b1e6c32388399cb3f619ebcee28ee36c7e08469",
}
# Every previously accepted complete companion has these same preview bytes.
# Pin them explicitly so a shallow checkout can package without reading history.
_PREVIEW_PREDECESSOR = "0620b59f8f64d25f19aab48a8081c490631f9bc65c730ab3c9e0de9c8eb0fff3"
PREDECESSORS = {
    revision: {SURFACE_MODULE: None, PREVIEW_MODULE: _PREVIEW_PREDECESSOR, **files}
    for revision, files in {
        "installed-workpiece-focus-568b1cc9": previous.PREVIOUS,
        previous.INTEGRATED_REVISION: previous.INTEGRATED,
        previous.THICKNESS_REVISION: previous.THICKNESS,
        previous.COOLING_REVISION: previous.COOLING,
        previous.REJECTION_REVISION: previous.REJECTION,
        previous.HONEYCOMB_REVISION: previous.HONEYCOMB,
        previous.HOME_COOLING_REVISION: previous.HOME_COOLING,
        STOP_RECOVERY_REVISION: STOP_RECOVERY,
    }.items()
}
SOURCE_FILES = tuple(next(iter(PREDECESSORS.values())))


def source(revision: str, name: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=ROOT).replace(b"\r\n", b"\n")


def package(destination: Path, revision: str, version: str) -> Path:
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=ROOT, text=True,
    ).strip()
    if any(set(files) != set(SOURCE_FILES) for files in PREDECESSORS.values()):
        raise ValueError("Every predecessor must describe the complete material-plane companion")
    sources = {name: source(revision, name) for name in SOURCE_FILES}
    if CAPABILITY.encode() not in sources[SURFACE_MODULE]:
        raise ValueError("Selected revision does not contain material-plane capability support")
    installer_source = source(revision, "scripts/install_workpiece_focus.py").decode("utf-8")
    guide_source = source(revision, "docs/MATERIAL_HEIGHT.md")
    precision_guide_source = source(revision, "docs/PRECISION_PLACEMENT.md")
    manifest = json.dumps({
        "revision": revision, "compatible_windows_version": version, "required_capability": CAPABILITY,
        "predecessors": [{"revision": baseline, "files": files} for baseline, files in PREDECESSORS.items()],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(content).hexdigest()} for name, content in sources.items()],
    }, indent=2).encode("utf-8")
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-material-surface-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest)
    (folder / "install_material_surface.py").write_text(
        installer_source.replace("BUILD_REPLACES_MANIFEST_SHA256", digest), encoding="utf-8", newline="\n",
    )
    (folder / "MATERIAL_HEIGHT.md").write_bytes(guide_source)
    (folder / "PRECISION_PLACEMENT.md").write_bytes(precision_guide_source)
    windows_source = str(folder.resolve()).replace("'", "''")
    guide = f"""# Install the material-plane companion

Use E3 DEV TEST {version}, revision `{revision}`, with this exact companion.
It advertises `{CAPABILITY}`. E3SURFACE binds the selected measured top, border
reference and controller session into immutable job bytes and is never sent to
the controllers. The companion also returns the current reference identity for
the bed survey. Neither feature establishes physical placement accuracy.

This source-only update needs no firmware flash. It preserves the existing
firmware checks, focus, clearance, STOP and Air Assist recovery guards. It accepts
all pinned complete thickness-focus predecessors, including the installed STOP
recovery source at `{STOP_RECOVERY_REVISION}`, or already-current files. Unknown
edits and mixed predecessors reject before writes. Configuration, saved honeycomb,
gauge teaching, probe offsets, Z limits, retained-Z and cooling data are preserved.
Replaced sources receive exact backups. The installer neither stops nor starts
the service; its default is a read-only preview.

Copy from Windows PowerShell:

```powershell
scp -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -i "$env:USERPROFILE\\.ssh\\greenhouse_pi_ed25519" -r '{windows_source}' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/
```

Inspect the exact source upgrade in Pi Bash:

```sh
e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner
e3_surface=/home/greenhouse-climate/{folder.name}
"$e3_project/.venv/bin/python" "$e3_surface/install_material_surface.py" --project "$e3_project"
```

After the preview accepts every source, with the machine idle and both clients
disconnected, stop the service, apply and restart in the same Pi Bash session:

```sh
sudo systemctl stop e3-hardware-node.service &&
sudo systemctl reset-failed e3-hardware-node.service &&
"$e3_project/.venv/bin/python" "$e3_surface/install_material_surface.py" --project "$e3_project" --apply &&
sudo systemctl start e3-hardware-node.service
```

Open E3 DEV TEST, establish the required reference and probe again. Workpiece
authority does not survive a service restart. Follow the current
[precision placement workflow](PRECISION_PLACEMENT.md), including its ordered
setup, height evidence and independent physical qualification before relying on
any placement tolerance. MATERIAL_HEIGHT.md is retained as historical context;
use PRECISION_PLACEMENT.md for this feature build.
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
