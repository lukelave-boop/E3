"""Build the guarded Pi companion for faster Z setup and first-app priority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

if __package__:
    from .package_source_dependencies import material_dependencies
else:
    from package_source_dependencies import material_dependencies

ROOT = Path(__file__).resolve().parents[1]
INSTALLED_NAME = "installed-workpiece-focus-568b1cc9"
INSTALLED_REVISION = "671b235f272b8a0e910ff5039006c0d431bb9cd8"
DESKTOP_VERSION = "0.7.132"
DESKTOP_REVISION = "92a7a6149713c7218fdc49de42336d660f24c48a"
# The operator installed all 15 paths from 568b1cc9. Its application payload
# matches this exact revision; other installed paths are not assumed upgraded.
# Install the new shared module first, after validation of the complete bundle.
PREVIOUS = {
    "laser_aligner/machine/setup_motion.py": None,
    # 568b1cc9 did not replace the protocol. These exact bytes also match the
    # reconstructed preceding bb37be7 tree and the priority kit's pinned input.
    "laser_aligner/machine/pi_job_protocol.py": "62aa7a9a68348cedbb848f2ede93b62797af78e0d4190e67aa56ba9904229328",
    "laser_aligner/config.py": "f12b0135c68ee8e787cf316294c15a51451f9ec64338b85f2db9f0c2929f9f58",
    "laser_aligner/machine/mainboard.py": "0388228c3ded64c5b424f6da163d6260bbc90a0f1161b19a8a9c88062cea4eba",
    "laser_aligner/machine/service.py": "75d59bc5659b692afcbaf72d3a75559c9f6657b9589b4545da4c811bcf8a840b",
    "laser_aligner/machine/remote_service.py": "327a19b944669549370152e845c874f82d039ab87ddc7823409459969f441994",
    "laser_aligner/machine/pi_job_service.py": "f36f0841b67c48074dacefad05a1de242325675d398ce7eed12301f651d07da3",
    "laser_aligner/machine/pi_machine_server.py": "de5eb513e79b9e4733fd8073d3070b0d3c6df9a0c2a349c892bf1889c6fc33b3",
    "laser_aligner/remote_node.py": "474325070988bf6e0323a4c8874e843438166ad81e569d9d862de580e7cf7088",
    "laser_aligner/machine/z_limits.py": "c1ccb0ec6f82d7489d49dd50bdaf295a75a20b22117d3cad0ee56298c3b00310",
    "laser_aligner/machine/z_probe.py": "393a2d2c5e791754fed2a14fd706548df4b57bb96cc7fc46ba71d31101c96ece",
    "laser_aligner/machine/secondary_controller.py": "788420240b542f1280343fc0677590510d2b13c395988871d2b75e67d88b08f2",
    "laser_aligner/machine/secondary_startup.py": "d7c6bc19862d8f3dd5fd638581860057189956e4314157efb92f317298eb258b",
    "laser_aligner/machine/laser_focus.py": "8c61e19d8198231d67f0f6dbacf95a176757e208f089dd8e2cdd73676be4d69a",
    "laser_aligner/machine/focus_bounds.py": "9d2f2403c38f50bf20b35bf364de54747128c131bb816ae46534ce6bceb93d5c",
    "laser_aligner/machine/job_focus.py": "34f2b85edc8b75406366a3baf20e282a6988aad157d092a782bb987d4ca1035d",
    "laser_aligner/machine/z_retention.py": "f6f5ac717b12272365b8635b4c4874824e644d42594a99e0e7c94b1dd88ee812",
}


def installation_guide(folder: Path) -> str:
    windows_source = str(folder.resolve()).replace("'", "''")
    return (
        "# Install faster Z setup and first-app connection priority\n\n"
        f"Use **E3 DEV TEST {DESKTOP_VERSION}**, frozen from Windows application revision "
        f"`{DESKTOP_REVISION}`, with this combined Pi companion and the matching speed firmware. "
        "The Windows priority build uses the existing focus/mainboard commands; the combined Pi "
        "supplies their faster movement and preserves the first connected app's control priority. "
        "Use this combined package for both features.\n\n"
        "This companion updates application sources only. Faster Z requires the matching "
        "e3-mainboard-f401-usb firmware package supplied with this build, advertising "
        "`Cap:E3_Z_SETUP_SPEED_V1:1`. Follow that firmware package's README before motion testing. "
        "The firmware raises the normal Z travel cap while retaining the previous probe/homing "
        "cycle speeds. This source installer does not flash firmware.\n\n"
        "Saved files, travel limits and cooling configuration are preserved. The changed firmware "
        "identity invalidates the prior reference and gauge calibration compatibility: reference "
        "the border, teach the 7 mm gauge again and measure the workpiece after updating. "
        "Do not reuse a retained-Z record across the firmware change.\n\n"
        "Copy the exact folder from Windows PowerShell:\n\n"
        "```powershell\n"
        f"scp -r '{windows_source}' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/\n"
        "```\n\n"
        "In Pi Bash, inspect the planned source changes first. This dry run is read-only "
        "and sends no controller commands:\n\n"
        "```sh\n"
        "e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner\n"
        f"e3_setup_speed=/home/greenhouse-climate/{folder.name}\n"
        '"$e3_project/.venv/bin/python" "$e3_setup_speed/install_z_setup_speed.py" '
        '--project "$e3_project"\n'
        "```\n\n"
        "The installer accepts the installed 568b1cc9 workpiece-focus companion or already-current "
        "sources. It checks all 17 payload paths, including the shared motion and priority protocol "
        "modules, before replacing any file. Unknown local edits "
        "or other baselines reject; do not force replacement. Changed existing sources receive "
        "byte-for-byte backups.\n\n"
        "When the machine is idle and all E3 instances are disconnected, use the same Pi Bash session to stop "
        "the service and apply. The installer requires an inactive service and never stops or "
        "starts it itself. Restart only after successful installation:\n\n"
        "```sh\n"
        "sudo systemctl stop e3-hardware-node.service &&\n"
        "sudo systemctl reset-failed e3-hardware-node.service &&\n"
        '"$e3_project/.venv/bin/python" "$e3_setup_speed/install_z_setup_speed.py" '
        '--project "$e3_project" --apply &&\n'
        "sudo systemctl start e3-hardware-node.service\n"
        "```\n\n"
        "The first connected app keeps control while another app monitors. Status and authenticated "
        "STOP remain available to the second app; its ordinary controls and cleanup cannot take "
        "ownership. Use explicit Disconnect while idle before switching apps. An older desktop "
        "without ownership-release support retains priority until its explicit Disconnect or a "
        "Pi service restart; closing it or losing Wi-Fi does not release priority.\n\n"
        f"Open E3 DEV TEST {DESKTOP_VERSION} after the service starts. While idle, connect normal E3 "
        "first and then open E3 DEV TEST; verify the second app is view-only and the first retains "
        "its connection. Disconnect the first app, connect the second, and repeat in reverse order. "
        "Establish the required "
        "reference and measure the workpiece again; service restart does not restore the "
        "previous workpiece focus. Verify the faster laser-off XY positioning and Z movement "
        "with clear physical travel paths. These speeds require operator physical verification. "
        "See [the focus guide](LASER_FOCUS.md) for the workflow.\n"
    )


def package(destination: Path) -> Path:
    sources = {name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in PREVIOUS}
    dependencies = material_dependencies(sources, lambda name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    manifest = json.dumps({
        "predecessors": [{"revision": INSTALLED_NAME, "files": {**PREVIOUS, **dependencies}}],
        "predecessor_application_revision": INSTALLED_REVISION,
        "compatible_windows_version": DESKTOP_VERSION,
        "compatible_windows_revision": DESKTOP_REVISION,
        "pi_control_capability": "pi-control-owner-v1",
        "required_firmware_capability": "Cap:E3_Z_SETUP_SPEED_V1:1",
        "files": [{"path": name, "sha256_lf": hashlib.sha256(content).hexdigest()}
                  for name, content in sources.items()],
    }, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-z-setup-speed-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest)
    template = (ROOT / "scripts/install_workpiece_focus.py").read_text(encoding="utf-8")
    installer = (template.replace("BUILD_REPLACES_MANIFEST_SHA256", digest)
                 .replace("workpiece-focus companion", "Z setup speed companion")
                 .replace("Workpiece-focus support not installed", "Z setup speed support not installed"))
    (folder / "install_z_setup_speed.py").write_text(installer, encoding="utf-8", newline="\n")
    (folder / "INSTALL.md").write_text(installation_guide(folder), encoding="utf-8", newline="\n")
    for name in ("LASER_FOCUS.md", "ENDER_RECOVERY.md", "Z_RETENTION.md"):
        guide = (ROOT / "docs" / name).read_text(encoding="utf-8")
        guide = (guide.replace("__PI_PACKAGE__", folder.name)
                 .replace("install_laser_focus.py", "install_z_setup_speed.py")
                 .replace("install_workpiece_focus.py", "install_z_setup_speed.py"))
        (folder / name).write_text(guide, encoding="utf-8", newline="\n")
    (folder / "README.md").write_text(
        "# Faster Z setup and first-app priority Pi companion\n\n"
        f"Use E3 DEV TEST {DESKTOP_VERSION}, this combined Pi companion and the matching speed "
        "firmware. The Pi provides faster laser-off setup XY/Z movement and preserves the first "
        "connected app's control priority. The payload includes both features' complete source "
        "dependencies, including the ownership protocol and shared motion module. "
        "It preserves existing motion, bounds, clearance, probe and laser-off checks. "
        "This package changes application sources only. Normal Z movement requires the matching "
        "firmware advertising `Cap:E3_Z_SETUP_SPEED_V1:1`; install the supplied firmware package "
        "using its README, then reference and teach the gauge again.\n\n"
        "The installed 568b1cc9 companion is the pinned predecessor. Unknown source edits reject; "
        "configuration and calibration are preserved, and replaced source bytes are backed up. "
        "Use [INSTALL.md](INSTALL.md) for the exact copy, read-only preview and guarded apply "
        "commands. The installer never starts or stops the service. "
        "See [the focus guide](LASER_FOCUS.md) for the operator workflow and verification limits.\n",
        encoding="utf-8", newline="\n",
    )
    return folder


if __name__ == "__main__":
    print(package(ROOT / "dist"))
