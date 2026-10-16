"""Build the exact, source-only Pi pause/resume companion; never contact hardware."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

if __package__:
    from .install_workpiece_focus import canonical
else:
    from install_workpiece_focus import canonical

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_REVISION = "34902b8"
PREVIOUS = {
    "laser_aligner/machine/grbl_stream.py": "cd740ccec84a2724f2a6db083aadbcb0ce167c0e0642ec675b689122eab2ae54",
    "laser_aligner/machine/controller_receiver.py": "3af3b69a2f2d7eb26d90c50c8b0048b1a78c66ade685ae3e4b867fc225f5da73",
    "laser_aligner/machine/serial_posix.py": "1b8c025927976eeb84eeb23c0702ab634c9d381663adc3e4d49589d6e133963d",
    "laser_aligner/machine/service.py": "b26b5210ef533053bd1da2f45122b4b945c1c282099e3fad74be649d0b06abe2",
    "laser_aligner/machine/pi_job_protocol.py": "a39634447897c97bfd32566e5200baee1849024d0f0645af577430733ffd7290",
    "laser_aligner/machine/pi_job_service.py": "13f237107d51e994352ab6c1856bc8a18a5d1b655fbe2ebe8bb1ff996c8b9bbf",
    "laser_aligner/machine/pi_machine_server.py": "9e0dbf9801a73799fd8f129ed691e9eff8445c667269a29e3d555930c9e2dd0c",
    "laser_aligner/machine/job_pause.py": None,
}


def package(destination: Path, *, revision: str | None = None) -> Path:
    if revision is not None:
        revision = subprocess.check_output(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
            cwd=ROOT, text=True,
        ).strip()

    def source(name):
        return canonical((ROOT / name).read_bytes() if revision is None else subprocess.check_output(
            ["git", "show", f"{revision}:{name}"], cwd=ROOT,
        ))

    sources = {name: source(name) for name in PREVIOUS}
    for name, data in sources.items():
        compile(data, name, "exec")
    if b"def request_pause(" not in sources["laser_aligner/machine/service.py"]:
        raise ValueError("Selected revision does not implement job pause")
    manifest = json.dumps({
        "source_revision": revision,
        "predecessors": [{"revision": PREVIOUS_REVISION, "files": PREVIOUS}],
        "files": [{"path": name, "sha256_lf": hashlib.sha256(data).hexdigest()}
                  for name, data in sources.items()],
    }, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-job-pause-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in sources.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (folder / "manifest.json").write_bytes(manifest)
    installer = (ROOT / "scripts/install_workpiece_focus.py").read_text(encoding="utf-8")
    installer = installer.replace("BUILD_REPLACES_MANIFEST_SHA256", digest).replace("workpiece-focus", "job-pause")
    (folder / "install_job_pause.py").write_text(installer, encoding="utf-8", newline="\n")
    (folder / "TESTING.md").write_bytes(source("docs/JOB_PAUSE.md"))
    (folder / "INSTALL.md").write_text(
        "# Pi pause/resume companion\n\n"
        "Eight source files, including the unchanged streaming dependency. This installer "
        "accepts only the exact pinned predecessor or already-current bytes, backs up replaced files, "
        "and rejects unknown changes. It does not touch configuration, calibration or firmware.\n\n"
        "First finish or explicitly stop any job, disconnect all clients, and verify the Pi is idle, "
        "disarmed and unowned. Never stop its service during an accepted job.\n\n"
        "Copy this folder to the Pi, then use its project Python to run:\n\n"
        "```sh\npython install_job_pause.py --project /absolute/project/path\n```\n\n"
        "This defaults to read-only inspection. After the checks above, stop "
        "e3-hardware-node.service and run the same command with `--apply`. Restart the service "
        "only after successful installation and source verification. The installer requires an "
        "inactive Linux service and never starts/stops services or sends controller commands.\n\n"
        "Use the matching E3 DEV TEST build. Older nodes keep Pause unavailable. Follow "
        "[TESTING.md](TESTING.md); physical behavior remains unverified.\n",
        encoding="utf-8", newline="\n",
    )
    return folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "dist")
    parser.add_argument("--revision")
    args = parser.parse_args()
    print(package(args.destination, revision=args.revision))
