"""Build a Pi-only, hash-checked automatic FAN1 cooling companion kit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = {
    "laser_aligner/remote_node.py": "5a0e549c406251e7c0c8fc6e0c6fcc14850f52db800646c1f9d8edc8f88b4a5e",
    "laser_aligner/machine/service.py": "c93b2868a554eca3badc53d0cd1354571707a5b0d0cb52df8a3914a66bf8e3e2",
    "laser_aligner/cpu_cooling.py": None,
    "laser_aligner/machine/cpu_cooling.py": None,
}


def package(destination):
    sources = {name: (ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in PREVIOUS}
    manifest = json.dumps({"files": [
        {"path": name, "previous_sha256_lf": old,
         "sha256_lf": hashlib.sha256(sources[name]).hexdigest()}
        for name, old in PREVIOUS.items()
    ]}, indent=2).encode()
    digest = hashlib.sha256(manifest).hexdigest()
    output = destination / f"e3-pi-cooling-{digest[:8]}"
    output.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
    (output / "manifest.json").write_bytes(manifest)
    template = (ROOT / "scripts/install_cpu_cooling.py").read_text(encoding="utf-8")
    (output / "install_cpu_cooling.py").write_text(
        template.replace("BUILD_REPLACES_MANIFEST_SHA256", digest), encoding="utf-8", newline="\n",
    )
    (output / "45-e3-cpu-cooling.conf").write_text("[Service]\nEnvironment=E3_CPU_COOLING=1\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    print(package(ROOT / "dist"))
