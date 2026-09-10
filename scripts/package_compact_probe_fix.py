"""Build an exact-source Pi-only patch kit using the tested companion installer."""
from __future__ import annotations

import hashlib
from pathlib import Path

OLD_SHA256 = "9eb59efd069e5ec35af14990da80b917268a02cfcc63d99b0ccb5c755ddd910e"
ROOT = Path(__file__).resolve().parents[1]


def package(destination: Path) -> Path:
    source = (ROOT / "laser_aligner/machine/z_probe.py").read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(source).hexdigest()
    template = (ROOT / "firmware/marlin_mainboard_compact/install_pi_support.py").read_text(encoding="utf-8")
    template = template.replace("secondary_startup.py", "z_probe.py")
    template = template.replace("compact-updater readiness companion", "compact Z-homing endpoint companion")
    start = template.index("DESIRED_SHA256 = ")
    end = template.index('SERVICE = ', start)
    template = template[:start] + (
        f'DESIRED_SHA256 = "{digest}"\n'
        f'ACCEPTED_SOURCE_SHA256 = {{"{OLD_SHA256}": "previous probe source", '
        'DESIRED_SHA256: "compact endpoint fix"}\n'
    ) + template[end:]
    output = destination / f"e3-compact-probe-fix-{digest[:8]}"
    output.mkdir(parents=True, exist_ok=True)
    (output / "z_probe.py").write_bytes(source)
    (output / "install_probe_fix.py").write_text(template, encoding="utf-8", newline="\n")
    (output / "README.txt").write_text(
        "Pi-only compact V1 homing endpoint correction. No firmware flash.\n"
        "Stop e3-hardware-node.service before applying.\n"
        "Run the project Python with install_probe_fix.py --project PROJECT --apply.\n"
        "Only the exact previous/current z_probe.py hashes are accepted; a backup is retained.\n"
        "Restart the service afterward. Physical final Z20 clearance is not yet verified.\n"
        f"Source SHA-256 (LF): {digest}\n", encoding="utf-8",
    )
    return output


if __name__ == "__main__":
    print(package(ROOT / "dist"))
