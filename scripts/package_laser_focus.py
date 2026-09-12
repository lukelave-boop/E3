"""Build the hash-checked Pi companion for taught gauge focus and raised surfaces."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Operator-installed a43df3a1 focus companion; preserve all unknown edits.
PREVIOUS = {
    'laser_aligner/config.py': 'f12b0135c68ee8e787cf316294c15a51451f9ec64338b85f2db9f0c2929f9f58',
    'laser_aligner/machine/mainboard.py': '901f7fa7bd73af9727b90bbbf6963a322d04d53d81e92c0401ffdc05cd851195',
    'laser_aligner/machine/service.py': '35e9a7738485678a3c05c1b7cd2f6b9224913062649114ff5b22f9208e71d726',
    'laser_aligner/machine/remote_service.py': 'f9e64b8b7aaa25ac218f071b1aefdeba4bfbe7857b778a8ea09460e2571cef74',
    'laser_aligner/machine/pi_job_service.py': 'bf08f149d0754be5c430a243281406acdfab195716041c55593c8d145efac88f',
    'laser_aligner/machine/pi_machine_server.py': '9aba978b9234b50ab50a59dfb93f9ee7731dd2c9fdfd32143ef5fa5fb82b0498',
    'laser_aligner/remote_node.py': '81d993afee3e6058aee1895e27f114dc519745928fdf191dcc6aefd6e179ebf9',
    'laser_aligner/machine/z_limits.py': 'c1ccb0ec6f82d7489d49dd50bdaf295a75a20b22117d3cad0ee56298c3b00310',
    'laser_aligner/machine/z_probe.py': '393a2d2c5e791754fed2a14fd706548df4b57bb96cc7fc46ba71d31101c96ece',
    'laser_aligner/machine/secondary_startup.py': 'b052d29029c2d393014cd252ac70de56b01178ee8249a1f9002b5a0ddcfec0d2',
    'laser_aligner/machine/laser_focus.py': '81976161630bd5cd94f9e4be819784ba0aede582cd57726bf46856240c4a2ff2',
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
