"""Build the hash-checked Pi companion for taught gauge focus and raised surfaces."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Operator-confirmed a1c79819 Z kit, with the earlier startup and native probe fixes.
PREVIOUS = {
    "laser_aligner/config.py": "f12b0135c68ee8e787cf316294c15a51451f9ec64338b85f2db9f0c2929f9f58",
    "laser_aligner/machine/mainboard.py": "901f7fa7bd73af9727b90bbbf6963a322d04d53d81e92c0401ffdc05cd851195",
    "laser_aligner/machine/service.py": "575d0aefeea447b6f07f9a25520f56c096436877bcf6054087f0581c13d8b728",
    "laser_aligner/machine/remote_service.py": "e2e3011dfbb01f463b7eccbff215425d50e6e0e662e9b5552354ce6ca822daae",
    "laser_aligner/machine/pi_job_service.py": "ba5fe7a86ffc5b0889fbb41cca7612f4f9cf98dcc01844ffdb0bb4f1af67c0fb",
    "laser_aligner/machine/pi_machine_server.py": "51722904c43a2a09a3667be8ab5b5dfb8ed8788b59777f6b9b0579dee2f1c107",
    "laser_aligner/remote_node.py": "a08f8235759b15ea614b855f1bcdc8d26a8fa727bf3003da17c711fb2735a8e7",
    "laser_aligner/machine/z_limits.py": "c1ccb0ec6f82d7489d49dd50bdaf295a75a20b22117d3cad0ee56298c3b00310",
    "laser_aligner/machine/z_probe.py": "393a2d2c5e791754fed2a14fd706548df4b57bb96cc7fc46ba71d31101c96ece",
    "laser_aligner/machine/secondary_startup.py": "b052d29029c2d393014cd252ac70de56b01178ee8249a1f9002b5a0ddcfec0d2",
    "laser_aligner/machine/laser_focus.py": None,
}


def package(destination: Path) -> Path:
    sources = {name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in PREVIOUS}
    manifest = json.dumps({"files": [
        {"path": name, "previous_sha256_lf": previous,
         "sha256_lf": hashlib.sha256(sources[name]).hexdigest()}
        for name, previous in PREVIOUS.items()
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
    (folder / "README.md").write_text(guide.replace("__PI_PACKAGE__", folder.name),
                                      encoding="utf-8", newline="\n")
    return folder


if __name__ == "__main__":
    print(package(ROOT / "dist"))
