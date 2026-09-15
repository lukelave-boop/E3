"""Build the guarded Pi companion that preserves the first client's connection."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from .install_workpiece_focus import canonical
    from .package_source_dependencies import material_dependencies, requires_material_surface
    from .package_z_setup_speed import PREVIOUS as COMPLETE_PREVIOUS
else:
    from install_workpiece_focus import canonical
    from package_source_dependencies import material_dependencies, requires_material_surface
    from package_z_setup_speed import PREVIOUS as COMPLETE_PREVIOUS

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "671b235f272b8a0e910ff5039006c0d431bb9cd8"
PREVIOUS_PACKAGE = "e3-pi-workpiece-focus-568b1cc9"
# The server/client hashes match the operator-installed 568b1cc9 manifest.
# That companion left pi_job_protocol.py unchanged; its bytes match 671b235.
# Admit these exact complete paths or this bundle's already-updated bytes only.
PREVIOUS = {
    "laser_aligner/machine/pi_machine_server.py": "de5eb513e79b9e4733fd8073d3070b0d3c6df9a0c2a349c892bf1889c6fc33b3",
    "laser_aligner/machine/pi_job_protocol.py": "62aa7a9a68348cedbb848f2ede93b62797af78e0d4190e67aa56ba9904229328",
    "laser_aligner/machine/remote_service.py": "327a19b944669549370152e845c874f82d039ab87ddc7823409459969f441994",
}


def installation_guide(folder: Path) -> str:
    windows_source = str(folder.resolve()).replace("'", "''")
    return (
        "# Install first-client connection priority\n\n"
        "The Pi preserves the first connected E3 instance's control priority. A second "
        "instance cannot disconnect or reconnect its controller. Monitoring and STOP "
        "remain available. For an older desktop without ownership-release support, "
        "priority remains reserved until its explicit Disconnect or a Pi service restart; "
        "closing that desktop or losing Wi-Fi does not release priority. Use Disconnect "
        "while idle before changing between E3 and E3 DEV TEST.\n\n"
        f"This source-only companion accepts the exact installed {PREVIOUS_PACKAGE} "
        "module versions (application 671b235), or this companion's already-current files. "
        "It rejects unknown edits and preserves firmware, configuration, calibration, "
        "cooling and saved-Z files. Replaced sources receive byte-for-byte backups.\n\n"
        "The manifest lists the complete payload. Current material-aware sources include the "
        "guarded service and its dependencies; an explicitly selected historical revision "
        "retains its original three-module payload.\n\n"
        "Copy this exact folder from Windows PowerShell:\n\n"
        "```powershell\n"
        f"scp -r '{windows_source}' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/\n"
        "```\n\n"
        "In Pi Bash, inspect the planned changes. This first command is read-only "
        "and does not contact the controllers:\n\n"
        "```sh\n"
        "e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner\n"
        f"e3_priority=/home/greenhouse-climate/{folder.name}\n"
        '"$e3_project/.venv/bin/python" "$e3_priority/install_pi_control_priority.py" '
        '--project "$e3_project"\n'
        "```\n\n"
        "The installer must accept all three modules. If it rejects a source hash, "
        "preserve the output for review; do not force replacement. When the machine "
        "is idle and all E3 instances are disconnected, stop the service and apply "
        "using the same Pi Bash session. The installer requires an inactive service "
        "and never stops or starts it itself. Restart only after success:\n\n"
        "```sh\n"
        "sudo systemctl stop e3-hardware-node.service &&\n"
        "sudo systemctl reset-failed e3-hardware-node.service &&\n"
        '"$e3_project/.venv/bin/python" "$e3_priority/install_pi_control_priority.py" '
        '--project "$e3_project" --apply &&\n'
        "sudo systemctl start e3-hardware-node.service\n"
        "```\n\n"
        "While idle, connect normal E3 first and then open E3 DEV TEST. Confirm normal "
        "E3 retains its connection and the second instance reports the controller is "
        "in use. Explicitly Disconnect the first instance, then connect the second. "
        "Repeat in reverse order. This is an operator connection check; automated "
        "software tests do not verify physical controller or laser behavior. A service "
        "restart clears the prior workpiece measurement, so re-establish the required "
        "reference and workpiece focus before a later powered job.\n"
    )


def package(destination: Path, *, revision: str | None = None) -> Path:
    """Package working files by default, or exact payload blobs from a Git commit."""
    if revision is not None:
        revision = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            cwd=ROOT, text=True,
        ).strip()
    def source(name: str) -> bytes:
        return canonical(((ROOT / name).read_bytes() if revision is None else subprocess.check_output(
            ["git", "show", f"{revision}:{name}"], cwd=ROOT,
        )).replace(b"\r\n", b"\n"))
    sources = {name: source(name) for name in PREVIOUS}
    previous_files = dict(PREVIOUS)
    if requires_material_surface(sources):
        # Current server capability claims need the corresponding guarded
        # service implementation, not just enough files for imports to succeed.
        previous_files = dict(COMPLETE_PREVIOUS)
        sources.update({name: source(name) for name in previous_files})
    previous_files.update(material_dependencies(sources, source))
    manifest = json.dumps({
        "source_revision": revision,
        "predecessors": [{"revision": PREVIOUS_PACKAGE, "files": previous_files}],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(content).hexdigest()}
                  for name, content in sources.items()],
    }, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-control-priority-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest)
    # Reuse the existing checksum, predecessor, service-state, path, backup and
    # atomic-replacement guards without maintaining a second installer engine.
    template = (ROOT / "scripts/install_workpiece_focus.py").read_text(encoding="utf-8")
    installer = template.replace("BUILD_REPLACES_MANIFEST_SHA256", digest).replace(
        "workpiece-focus companion", "connection-priority companion",
    ).replace("Workpiece-focus support not installed:", "Connection-priority support not installed:")
    (folder / "install_pi_control_priority.py").write_text(installer, encoding="utf-8", newline="\n")
    (folder / "INSTALL.md").write_text(installation_guide(folder), encoding="utf-8", newline="\n")
    (folder / "README.md").write_text(
        "# First-client connection priority Pi companion\n\n"
        "The first connected E3 instance keeps controller priority when another instance opens. "
        f"This payload contains {len(sources)} hashed application modules, including required runtime dependencies. "
        "The installer defaults to a read-only preview, rejects unknown predecessor bytes, "
        "backs up replacements and requires the Pi service to be inactive before applying. "
        "Firmware and operator data are unchanged.\n\n"
        "Use [INSTALL.md](INSTALL.md) for exact copy, preview, installation and idle operator "
        "test commands, including the release limitation for older desktop clients.\n",
        encoding="utf-8", newline="\n",
    )
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "dist")
    parser.add_argument("--revision", help="Git commit/ref supplying exact application modules and required dependencies")
    args = parser.parse_args()
    print(package(args.destination, revision=args.revision))


if __name__ == "__main__":
    main()
