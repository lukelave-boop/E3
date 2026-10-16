"""Build the source-only Pi companion for bounded GRBL command streaming."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from .install_workpiece_focus import canonical
    from .package_source_dependencies import COMMAND_STREAMING_FILES, require_command_streaming_sources
else:
    from install_workpiece_focus import canonical
    from package_source_dependencies import COMMAND_STREAMING_FILES, require_command_streaming_sources

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "aac45cdb58f1e746ecfd4c08624fd74230c172f4"
# Exact canonical-LF sources recorded installed on the Pi on 2026-10-15.
# Constants keep building possible in shallow CI; unknown local bytes reject.
PREVIOUS = {
    "laser_aligner/machine/grbl_stream.py": None,
    "laser_aligner/machine/controller_receiver.py": "58eb9f98e13ae850a0604455c3a7e4647ef87297a286c0147892182e9810ec5b",
    "laser_aligner/machine/service.py": "d515f4a4f226ac712074d4109da45cdb8ca6512f0cf518d48f76696be7216ec8",
}


def installation_guide(folder: Path) -> str:
    windows_source = str(folder.resolve()).replace("'", "''")
    return f"""# Install the GRBL command-streaming companion

This Pi-only source update improves delivery through detailed G1 paths while
retaining the existing motion, arming, bounds, Stop and fault checks. It changes
three application modules only. The current Windows E3 client remains compatible;
no Windows rebuild or launcher selection is required. It changes no firmware,
configuration, calibration, gauge teaching, saved limits or job program bytes.

The accepted predecessor is the exact recorded Pi runtime at
`{PREVIOUS_REVISION}`, or this bundle's already-current files. Unknown edits and
an existing unknown grbl_stream.py are rejected before any source is replaced.
Replaced files receive byte-for-byte backups. Default installation is read-only;
applying requires Linux and an inactive e3-hardware-node.service. The installer
does not stop/start services, connect to hardware, home, arm, move or fire.

Copy this exact folder from Windows PowerShell:

```powershell
scp -r '{windows_source}' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/
```

Inspect the source update in Pi Bash. This first command is a read-only dry run:

```sh
e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner
e3_streaming=/home/greenhouse-climate/{folder.name}
"$e3_project/.venv/bin/python" "$e3_streaming/install_command_streaming.py" --project "$e3_project"
```

The preview must accept all three files. Preserve a rejected hash for review;
do not force replacement. Once the machine is idle and all E3 clients are
disconnected, apply in the same Pi Bash session. Restart only after success:

```sh
sudo systemctl stop e3-hardware-node.service &&
sudo systemctl reset-failed e3-hardware-node.service &&
"$e3_project/.venv/bin/python" "$e3_streaming/install_command_streaming.py" --project "$e3_project" --apply &&
sudo systemctl start e3-hardware-node.service
```

Reconnect the current Windows E3 client and follow its normal Home, focus and
job-preflight requirements. Service restart does not retain live controller or
job authority. Software throughput tests do not verify physical motion, laser
output or Stop behavior. Qualify those with the identified controller, firmware
and configuration using supervised tests and the existing physical emergency
measures before relying on the update. The software is not safety-rated.
"""


def package(destination: Path, *, revision: str | None = None) -> Path:
    """Package selected source bytes, without contacting the Pi or controllers."""
    if revision is not None:
        revision = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            cwd=ROOT, text=True,
        ).strip()

    def source(name: str) -> bytes:
        data = (ROOT / name).read_bytes() if revision is None else subprocess.check_output(
            ["git", "show", f"{revision}:{name}"], cwd=ROOT,
        )
        return canonical(data)

    sources = {name: source(name) for name in COMMAND_STREAMING_FILES}
    require_command_streaming_sources(sources)
    manifest = json.dumps({
        "source_revision": revision,
        "predecessors": [{"revision": PREVIOUS_REVISION, "files": PREVIOUS}],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(data).hexdigest()}
                  for name, data in sources.items()],
    }, indent=2).encode("utf-8")
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-command-streaming-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest)
    # Keep the existing checksum, predecessor, path, service-state, race, backup,
    # newline and atomic file replacement protections in one installer engine.
    template = (ROOT / "scripts/install_workpiece_focus.py").read_text(encoding="utf-8")
    installer = template.replace("BUILD_REPLACES_MANIFEST_SHA256", digest).replace(
        "workpiece-focus companion", "command-streaming companion",
    ).replace("Workpiece-focus support not installed:", "Command-streaming support not installed:")
    (folder / "install_command_streaming.py").write_text(installer, encoding="utf-8", newline="\n")
    (folder / "INSTALL.md").write_text(installation_guide(folder), encoding="utf-8", newline="\n")
    (folder / "README.md").write_text(
        "# GRBL command-streaming Pi companion\n\n"
        "Three hashed application modules improve bounded command delivery through detailed paths. "
        "The current Windows E3 client remains compatible; no replacement Windows build is needed. "
        "Configuration, calibration, firmware and immutable job programs are unchanged.\n\n"
        f"The installer accepts the exact recorded {PREVIOUS_REVISION} predecessor or already-current "
        "files. It defaults to a read-only preview, rejects unknown source changes, backs up replaced "
        "bytes and requires the Pi service to be inactive before applying. It never starts/stops "
        "services or sends controller commands.\n\n"
        "Use [INSTALL.md](INSTALL.md) for the exact copy, dry-run and apply commands. Physical "
        "throughput and Stop qualification remain required; software controls are not safety-rated.\n",
        encoding="utf-8", newline="\n",
    )
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "dist")
    parser.add_argument("--revision", help="Git commit/ref providing the exact three source modules")
    args = parser.parse_args()
    print(package(args.destination, revision=args.revision))


if __name__ == "__main__":
    main()
