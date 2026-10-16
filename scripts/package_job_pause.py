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
PREVIOUS_REVISION = "21273d01cb9409b9aea0733c57f0387f387a720a"
PREVIOUS = {
    "laser_aligner/machine/grbl_stream.py": "cd740ccec84a2724f2a6db083aadbcb0ce167c0e0642ec675b689122eab2ae54",
    "laser_aligner/machine/controller_receiver.py": "7e11e6536579811e85462ede01c421e1e89d919aaec7989ec3f3d002521e0fde",
    "laser_aligner/machine/serial_posix.py": "00b54f818fe67d20ae52ceb8ee15cf9962ee1bd4bfd6ade29b7a6e2be163d9a9",
    "laser_aligner/machine/service.py": "ea959de1c3e2c3b691879fbfc2e5260c57f3b4e7acacc1ef3db40ff113c5f108",
    "laser_aligner/machine/pi_job_protocol.py": "9fca60feced609f51380119d0493310ef91a14915795ed3fd38319f2d313d161",
    "laser_aligner/machine/pi_job_service.py": "9653fff8a985a8bb6dd1eb6ccb13f4367dc4e70c61f58efac44c597ca8c867f2",
    "laser_aligner/machine/pi_machine_server.py": "4b648790875a8a0e79180140353e3ddae585629ee127eaa5f1e96c73b0f2a886",
    "laser_aligner/machine/job_pause.py": "9170262c5439a8136b8bbab8908f0ded0e7f71073f3e957dd2f98113f215b933",
    "laser_aligner/machine/pi_job_store.py": "7a2d691f8b7652e1c42e2a37a6c0b869a86c68d2414b32c50a8933bea93b769f",
    "laser_aligner/geometry/polygon.py": "c06bc6bf7ce8df59e50ec3f21dd1af038b0e41b9a68cf921f1e29eec1a1d9785",
    "laser_aligner/raster_safety.py": "1aacbd71edd58dba38c08b6c370357a00eb1719059ccd9a3142041d7d212300b",
    "laser_aligner/machine/job_focus.py": "c746116d140cb858eb0a86f199c4fe804d8f77204b99762a0b198a471f2a384c",
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
        "Twelve source files, including bounded compressed upload, geometry, storage and validated focus-metadata dependencies. This installer "
        "accepts only the exact pinned predecessor or already-current bytes, backs up replaced files, "
        "and rejects unknown changes. It does not touch configuration, calibration or firmware.\n\n"
        f"Pinned installed predecessor: `{PREVIOUS_REVISION}`. All twelve files must pass inspection before any replacement.\n\n"
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
