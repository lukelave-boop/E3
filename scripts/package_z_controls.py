"""Package the Pi companion for desktop Z jogging and persisted limits."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Operator Pi: 5f52e40 plus the verified startup, compact probe, and CPU cooling kits.
PREVIOUS = {
    'laser_aligner/config.py': '030ac6d207174556993c1676514da49f0678c5d820891bc51efd3a3277f89416',
    'laser_aligner/machine/mainboard.py': 'bec87d2d3e9e8c9c8ffc4c78bca02e7b9e842c6e0abdefb1585da95c5c8f88bd',
    'laser_aligner/machine/service.py': '8b13d43abea5324daac2c972f8ce7d1edfd25f105f0b54df31f3a6c32a6c4aea',
    'laser_aligner/machine/remote_service.py': 'd282e57128f9fb460397226181575846b6ff6a51231ef73074ea964cbc4d6bf1',
    'laser_aligner/machine/pi_job_service.py': 'ba5fe7a86ffc5b0889fbb41cca7612f4f9cf98dcc01844ffdb0bb4f1af67c0fb',
    'laser_aligner/machine/pi_machine_server.py': '82dc73d50d91750a5619a75132f6b88ef24aeffea31b2d7881844adca70607e2',
    'laser_aligner/remote_node.py': 'ea7d1969bcb9c1768cd3221d5b22064c2bf3291c37eba1f0f6223cc3255eff0f',
    'laser_aligner/machine/z_limits.py': None,
}


def package(destination: Path) -> Path:
    sources = {name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in PREVIOUS}
    manifest = json.dumps({"files": [
        {"path": name, "previous_sha256_lf": previous,
         "sha256_lf": hashlib.sha256(sources[name]).hexdigest()}
        for name, previous in PREVIOUS.items()
    ]}, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    folder = destination / f"e3-pi-z-controls-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (folder / "manifest.json").write_bytes(manifest)
    template = (ROOT / "scripts/install_cpu_cooling.py").read_text(encoding="utf-8")
    installer = (template.replace("BUILD_REPLACES_MANIFEST_SHA256", digest)
                 .replace("CPU cooling companion", "desktop Z-control companion")
                 .replace("CPU cooling support not installed", "Z-control support not installed"))
    (folder / "install_z_controls.py").write_text(installer, encoding="utf-8", newline="\n")
    guide = (ROOT / "docs/MAINBOARD_Z_CONTROLS.md").read_text(encoding="utf-8")
    (folder / "README.md").write_text(guide.replace("__PI_PACKAGE__", folder.name), encoding="utf-8")
    return folder


if __name__ == "__main__":
    print(package(ROOT / "dist"))
