"""Build the hash-checked Pi companion for focus and clean-shutdown Z retention."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

if __package__:
    from .package_source_dependencies import material_dependencies
else:
    from package_source_dependencies import material_dependencies

ROOT = Path(__file__).resolve().parents[1]
# Authoritative main after the recorded installed focus/park correction. Accept
# exactly this predecessor or the bundled bytes; preserve all unknown edits.
PREVIOUS_REVISION = "bb37be7621cd7a35ea602b0f8899b87f2339679e"
PREVIOUS = {"laser_aligner/machine/setup_motion.py": None,
 'laser_aligner/config.py': 'f12b0135c68ee8e787cf316294c15a51451f9ec64338b85f2db9f0c2929f9f58',
 'laser_aligner/machine/mainboard.py': '0388228c3ded64c5b424f6da163d6260bbc90a0f1161b19a8a9c88062cea4eba',
 'laser_aligner/machine/service.py': 'bbf981708069a2b59ecb39a2facf037474b882755e1a91bebc1cb173676e9dac',
 'laser_aligner/machine/remote_service.py': 'a3df665796ad3a77110b00fbd523243c34bcdb964c9ec9cdd2bd4f69c9d4f47e',
 'laser_aligner/machine/pi_job_service.py': '47fa217348b1589f85bdffe6eb3b28586f2f62bf9f76d978aa9b9a2148fc07ed',
 'laser_aligner/machine/pi_machine_server.py': 'b37be2d367a1fd19e30abdc977c6db44d269939b1ea70b362ddd3aabd8d9be10',
 'laser_aligner/machine/pi_job_protocol.py': '62aa7a9a68348cedbb848f2ede93b62797af78e0d4190e67aa56ba9904229328',
 'laser_aligner/remote_node.py': '81d993afee3e6058aee1895e27f114dc519745928fdf191dcc6aefd6e179ebf9',
 'laser_aligner/machine/z_limits.py': 'c1ccb0ec6f82d7489d49dd50bdaf295a75a20b22117d3cad0ee56298c3b00310',
 'laser_aligner/machine/z_probe.py': '393a2d2c5e791754fed2a14fd706548df4b57bb96cc7fc46ba71d31101c96ece',
 'laser_aligner/machine/secondary_controller.py': 'e7e321e578711a94c7bb8e4a1aaf3b6670c315aa4f104bd0ba461724b19f2831',
 'laser_aligner/machine/secondary_startup.py': 'd7c6bc19862d8f3dd5fd638581860057189956e4314157efb92f317298eb258b',
 'laser_aligner/machine/laser_focus.py': '843dff645d0ab18df06de2c677a2d8ca43777d4d701c0a4113a5d2583af317c7',
 'laser_aligner/machine/focus_bounds.py': '9d2f2403c38f50bf20b35bf364de54747128c131bb816ae46534ce6bceb93d5c',
 'laser_aligner/machine/job_focus.py': 'd074b97956627441d37a127334597e018f87d1e10bbe22e9090d1e3d9bb8066d',
 'laser_aligner/machine/z_retention.py': None}


def packaged_focus_guide(guide: str, *, installer_name: str, predecessors: str) -> str:
    """Keep shared workflow guidance but bind installation to this actual kit."""
    before, heading, section = guide.partition("## Pi companion installation\n")
    if not heading:
        raise ValueError("The focus guide must contain its Pi companion installation section")
    _, next_heading, after = section.partition("\n## ")
    installation = (
        f"Use [INSTALL.md](INSTALL.md) in this package for its exact copy, dry-run and apply commands. "
        f"The bundled `{installer_name}` defaults to read-only and accepts {predecessors}, "
        "or already-current payload files. Unknown changes reject before replacement; changed "
        "existing sources receive byte-for-byte backups. Configuration, calibration files, "
        "travel limits and cooling settings are preserved.\n\n"
        "Apply only with the machine idle, E3 disconnected and the hardware-node service inactive. "
        "The installer neither stops nor starts the service and flashes no firmware. "
        "Install the separately supplied matching speed firmware by its README procedure, "
        "then establish a fresh border reference, teach the 7 mm gauge again and measure the workpiece.\n"
    )
    return before + heading + "\n" + installation + next_heading + after


def installation_guide(folder: Path) -> str:
    windows_source = str(folder.resolve()).replace("'", "''")
    return (
        "# Install this laser-focus companion\n\n"
        f"This installer accepts the exact `{PREVIOUS_REVISION}` predecessor or already-current "
        "payload files. Unknown edits reject before any replacement; replaced sources are backed up. "
        "Current sources require matching firmware advertising `Cap:E3_Z_SETUP_SPEED_V1:1` for "
        "normal Z movement. This source installer does not flash firmware. Follow the matching "
        "firmware package README, then reference the border, teach the gauge again and measure.\n\n"
        "Copy from Windows PowerShell:\n\n```powershell\n"
        f"scp -r '{windows_source}' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/\n"
        "```\n\nIn Pi Bash, preview the source changes without writing or sending controller commands:\n\n"
        "```sh\n"
        "e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner\n"
        f"e3_focus=/home/greenhouse-climate/{folder.name}\n"
        '"$e3_project/.venv/bin/python" "$e3_focus/install_laser_focus.py" --project "$e3_project"\n'
        "```\n\nWhen the machine is idle and E3 is disconnected, stop the service and apply in the "
        "same Pi Bash session. The installer checks for an inactive service and never stops or "
        "starts it itself. Restart only after installation succeeds:\n\n"
        "```sh\n"
        "sudo systemctl stop e3-hardware-node.service &&\n"
        "sudo systemctl reset-failed e3-hardware-node.service &&\n"
        '"$e3_project/.venv/bin/python" "$e3_focus/install_laser_focus.py" --project "$e3_project" --apply &&\n'
        "sudo systemctl start e3-hardware-node.service\n"
        "```\n"
    )


def package(destination: Path) -> Path:
    sources = {name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in PREVIOUS}
    dependencies = material_dependencies(sources, lambda name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    previous_files = {**PREVIOUS, **dependencies}
    manifest = json.dumps({"predecessor_revision": PREVIOUS_REVISION, "files": [
        {"path": name, "previous_sha256_lf": previous,
         "sha256_lf": hashlib.sha256(sources[name]).hexdigest()}
        for name, previous in previous_files.items()
    ]}, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-laser-focus-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest)
    template = (ROOT / "scripts/install_cpu_cooling.py").read_text(encoding="utf-8")
    installer = (template.replace("BUILD_REPLACES_MANIFEST_SHA256", digest)
                 .replace("CPU cooling companion", "laser-focus companion")
                 .replace("CPU cooling support not installed", "Laser-focus support not installed"))
    (folder / "install_laser_focus.py").write_text(installer, encoding="utf-8", newline="\n")
    guide = (ROOT / "docs/LASER_FOCUS.md").read_text(encoding="utf-8")
    guide = packaged_focus_guide(
        guide, installer_name="install_laser_focus.py",
        predecessors=f"the exact `{PREVIOUS_REVISION}` source predecessor",
    )
    (folder / "INSTALL.md").write_text(installation_guide(folder), encoding="utf-8", newline="\n")
    (folder / "README.md").write_text(
        "Current sources require matching firmware advertising `Cap:E3_Z_SETUP_SPEED_V1:1` "
        "for normal Z movement. Follow the matching firmware package README, then establish a "
        "fresh reference, teach the gauge again and measure the workpiece.\n\n"
        + guide.replace("__PI_PACKAGE__", folder.name),
        encoding="utf-8", newline="\n")
    (folder / "ENDER_RECOVERY.md").write_text((ROOT / "docs/ENDER_RECOVERY.md").read_text(encoding="utf-8"),
                 encoding="utf-8", newline="\n")
    return folder


if __name__ == "__main__":
    print(package(ROOT / "dist"))
