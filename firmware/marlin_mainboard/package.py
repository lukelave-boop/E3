"""Assemble the complete SD installer and rollback kit; never access hardware."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from firmware.ender_aux.host import validate_image  # noqa: E402
from firmware.marlin_mainboard.prepare import verify  # noqa: E402

UPDATER_SHA256 = "6af48a8c8cbb59f55641fa1bc5efc5404bc6717b3e2a2b1b1a538ee9e2a8f8d0"
STOCK_SHA256 = "9a81f7564d62b2b131dcc96f0bf47725e3ee78c3b68b198bb54680f431e02710"
STOCK_URL = "https://cdn.creality.com/ow/official/c2721d8d-b8d8-423c-84bb-e87be0b17dd6.zip"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assemble(updater: bytes, image: bytes) -> bytes:
    if len(updater) != 65536 or sha(updater) != UPDATER_SHA256:
        raise ValueError("The installer requires the exact accepted 0.1.0 updater")
    stack, reset = struct.unpack_from("<II", updater)
    if stack != 0x20010000 or not reset & 1 or not 0x08010000 <= reset & ~1 < 0x08020000:
        raise ValueError("Invalid retained-updater entry vectors")
    payload = validate_image(image)
    if b"Cap:E3_MAINBOARD_V1:1\n\0" not in payload or b"Cap:E3_MATERIAL_HEIGHT_V1:1\n\0" not in payload:
        raise ValueError("The application is missing the required mainboard / material capability")
    combined = updater + image
    if len(combined) > 0x70000 or combined[0x10200:] != payload:
        raise ValueError("SD image exceeds the application region or has an incorrect layout")
    return combined


def main() -> None:
    from firmware.marlin_mainboard.audit_outputs import audit_outputs
    from firmware.marlin_material.package import audit

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--objdump", type=Path, required=True)
    parser.add_argument("--accepted-sd", type=Path, required=True)
    parser.add_argument("--stock-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    verify(args.source)
    elf = args.source / ".pio/build/STM32F401RC_creality/firmware.elf"
    image = audit(elf, args.objdump)
    audit_outputs(elf)
    updater = args.accepted_sd.read_bytes()[:65536]
    combined = assemble(updater, image)
    stock = args.stock_bin.read_bytes()
    if sha(stock) != STOCK_SHA256:
        raise ValueError("Stock rollback binary does not match the official F401 2.0.8.26 image")
    digest = sha(combined)
    folder = args.output / f"e3-mainboard-v1-{digest[:8]}"
    sd = folder / "SD_CARD/STM32F4_UPDATE"
    sd.mkdir(parents=True, exist_ok=True)
    name = f"e3main_{digest[:8]}.bin"
    (sd / name).write_bytes(combined)
    (folder / "application.e3fw").write_bytes(image)
    (folder / "firmware.elf").write_bytes(elf.read_bytes())
    recovery = folder / "RECOVERY_STOCK/STM32F4_UPDATE"
    recovery.mkdir(parents=True, exist_ok=True)
    (recovery / "stock_2_0_8_26_9a81f756.bin").write_bytes(stock)
    for filename in ("INSTALL.md", "VALIDATE.md", "README.md"):
        shutil.copyfile(HERE / filename, folder / filename)
    shutil.copyfile(HERE.parent / "ender_aux/host.py", folder / "host.py")
    (folder / "requirements.txt").write_text("pyserial==3.5\n")
    # Installable companion source, without local machine configuration/captures.
    # Includes the typed controls so the firmware handoff is self-contained.
    host_names = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    with zipfile.ZipFile(folder / "HOST_SOURCE.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in host_names:
            if filename.startswith("laser_aligner/") or filename in {
                "pyproject.toml", "README.md", "requirements.txt", "requirements-desktop.txt",
            }:
                archive.write(ROOT / filename, filename)
    # Ship corresponding sources, including native Marlin and the retained updater.
    with zipfile.ZipFile(folder / "firmware-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        names = subprocess.check_output(["git", "-C", str(args.source), "ls-files"], text=True).splitlines()
        overlays = json.loads((HERE / "source-files.json").read_text())
        for filename in sorted(set(names) | set(overlays)):
            path = args.source / filename
            if path.is_file():
                archive.write(path, "Marlin/" + filename)
        for project in ("ender_aux", "marlin_material", "marlin_mainboard"):
            for path in sorted((HERE.parent / project).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, "E3/firmware/" + path.relative_to(HERE.parent).as_posix())
    manifest = {
        "target": "Creality S1 Pro STM32F401RET6 / CR4NS200141C13",
        "status": "ready for operator physical validation", "physical_validation_complete": False,
        "features": ["independent FAN1 PC0 / M106 P1", "independent FAN2 PA0 / M106 P0",
                     "native CR Touch deploy/stow", "native Z homing and movement", "bounded G39 material height"],
        "sd_file": f"SD_CARD/STM32F4_UPDATE/{name}", "sd_load_address": "0x08010000",
        "sd_sha256": digest, "updater_sha256": sha(updater), "application_sha256": sha(image),
        "source_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "stock_source": STOCK_URL, "stock_sha256": sha(stock), "files": {},
    }
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest["files"][path.relative_to(folder).as_posix()] = sha(path.read_bytes())
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(folder.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder))
    print(json.dumps({"folder": str(folder.resolve()), "sd_file": str((sd / name).resolve()),
                      "sha256": digest, "bytes": len(combined)}, indent=2))


if __name__ == "__main__":
    main()
