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
PREVIOUS_REVISION = "e649a22"
PREVIOUS = {
    "laser_aligner/machine/grbl_stream.py": "cd740ccec84a2724f2a6db083aadbcb0ce167c0e0642ec675b689122eab2ae54",
    "laser_aligner/machine/controller_receiver.py": "7e11e6536579811e85462ede01c421e1e89d919aaec7989ec3f3d002521e0fde",
    "laser_aligner/machine/serial_posix.py": "00b54f818fe67d20ae52ceb8ee15cf9962ee1bd4bfd6ade29b7a6e2be163d9a9",
    "laser_aligner/machine/service.py": "dc2ad76d3ae50ad7259ac4aa7697fca60bbc477db4a405bdbc6a5fae98635113",
    "laser_aligner/machine/pi_job_protocol.py": "a14989248b0445912676bf41c1bb52893ae7ad06e5a1a5b1a01e4944b1303a73",
    "laser_aligner/machine/pi_job_service.py": "9653fff8a985a8bb6dd1eb6ccb13f4367dc4e70c61f58efac44c597ca8c867f2",
    "laser_aligner/machine/pi_machine_server.py": "2dae83558117983049890eedab2e35969736bf05a5c442349d9db5ab65341af8",
    "laser_aligner/machine/job_pause.py": "9170262c5439a8136b8bbab8908f0ded0e7f71073f3e957dd2f98113f215b933",
    "laser_aligner/machine/pi_job_store.py": "1cc52dd1aec446fcff69471ad5ccb72421405412855caa151d7ec1ca20aa51ef",
    "laser_aligner/geometry/polygon.py": "fafab1a368bbfb7506160c85235b9cd1faa6981a076e3ad57146d60dda8a275f",
    "laser_aligner/raster_safety.py": "6c644c8b4aadcaf827c4a5810145be045a7ec7ff438fa2ec6e7d96a7bbaa3863",
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
        "Eleven source files, including bounded compressed upload, geometry and storage dependencies. This installer "
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
