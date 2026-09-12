"""Build the hash-checked Pi companion for taught gauge focus and raised surfaces."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Operator-installed 7602e2df focus companion; preserve all unknown edits.
PREVIOUS = {
    'laser_aligner/config.py': 'f12b0135c68ee8e787cf316294c15a51451f9ec64338b85f2db9f0c2929f9f58',
    'laser_aligner/machine/mainboard.py': '901f7fa7bd73af9727b90bbbf6963a322d04d53d81e92c0401ffdc05cd851195',
    'laser_aligner/machine/service.py': '79d5d546de867f01d74f4d60575c7ddb82c9c464d41d6cf10031d58f903663e6',
    'laser_aligner/machine/remote_service.py': '7f8e78998e51214eca9ec3529d24caec48e96a9d48f2952a997dfdf78060afa6',
    'laser_aligner/machine/pi_job_service.py': 'bf08f149d0754be5c430a243281406acdfab195716041c55593c8d145efac88f',
    'laser_aligner/machine/pi_machine_server.py': '11ded1c82b38f9b6a9f15ddf157117035f6183f21a73a8936d63bac3532ab2b9',
    'laser_aligner/remote_node.py': '81d993afee3e6058aee1895e27f114dc519745928fdf191dcc6aefd6e179ebf9',
    'laser_aligner/machine/z_limits.py': 'c1ccb0ec6f82d7489d49dd50bdaf295a75a20b22117d3cad0ee56298c3b00310',
    'laser_aligner/machine/z_probe.py': '393a2d2c5e791754fed2a14fd706548df4b57bb96cc7fc46ba71d31101c96ece',
    'laser_aligner/machine/secondary_startup.py': 'b052d29029c2d393014cd252ac70de56b01178ee8249a1f9002b5a0ddcfec0d2',
    'laser_aligner/machine/laser_focus.py': '9588b4a35874e24e41bffefaa817aca1ce6c2ca6f16dafeae8759739de770d05',
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
