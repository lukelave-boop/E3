"""Audit and bundle a target-specific F103 SD installer and USB application."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

from audit_layout import audit
from audit_outputs import audit_outputs
from host import validate_image
from prepare_marlin import BASELINE, prepare

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def package(source: Path) -> Path:
    source = source.resolve()
    prepare(source)
    toolchain = ROOT / "build/firmware-tools/bin"
    elf = source / ".pio/build/HAL_STM32F103RET6_creality/firmware.elf"
    application = audit(elf, toolchain / "arm-none-eabi-objdump.exe")
    audit_outputs(elf)
    validate_image(application)
    updater_path = ROOT / "build/ender_aux_f103/updater.bin"
    updater = updater_path.read_bytes()
    stack, reset = struct.unpack_from("<II", updater)
    if not (304 <= len(updater) <= 0x9000 and 0x20000000 < stack <= 0x20010000
            and stack % 8 == 0 and reset & 1 and 0x08007130 <= reset & ~1 < 0x08010000):
        raise ValueError("F103 updater vector or extent invalid")
    for candidate in (elf, updater_path.with_suffix(".elf")):
        attrs = subprocess.check_output([str(toolchain / "arm-none-eabi-readelf.exe"), "-A", str(candidate)], text=True)
        if 'Tag_CPU_name: "7-M"' not in attrs or "Tag_ABI_VFP_args:" in attrs:
            raise ValueError("Expected Cortex-M3 software-float firmware")
    combined = updater.ljust(0x9000, b"\xff") + application
    digest = hashlib.sha256(combined).hexdigest()
    bundle = ROOT / "dist" / f"e3-mainboard-f103-usb-{digest[:8]}"
    bundle.mkdir(exist_ok=True)
    sd = bundle / "SD_CARD"
    sd.mkdir(exist_ok=True)
    (sd / "firmware.bin").write_bytes(combined)
    (bundle / "application.e3fw").write_bytes(application)
    for name in ("README.md", "host.py"):
        shutil.copyfile(HERE / name, bundle / name)
    (bundle / "requirements.txt").write_text("pyserial==3.5\n")
    shutil.copyfile(elf, bundle / "application.elf")
    shutil.copyfile(updater_path.with_suffix(".elf"), bundle / "updater.elf")
    patch = subprocess.check_output(["git", "diff", "--", "laser_aligner/machine/secondary_startup.py"], cwd=ROOT)
    if not patch:
        raise ValueError("Companion patch must be captured before this working change is committed")
    (bundle / "pi-readiness.patch").write_bytes(patch)
    tracked = subprocess.check_output(["git", "-C", str(source), "ls-files"], text=True).splitlines()
    overlays = json.loads((HERE / "source-files.json").read_text())
    with zipfile.ZipFile(bundle / "firmware-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(tracked) | overlays.keys()):
            path = source / name
            if path.is_file():
                archive.write(path, "marlin/" + name)
        for path in sorted(HERE.iterdir()):
            if path.is_file() and path.suffix in {".c", ".h", ".py", ".ld", ".inc", ".json", ".md"}:
                archive.write(path, "firmware/ender_aux_f103/" + path.name)
        for folder in (ROOT / "firmware/marlin_material", ROOT / "firmware/marlin_mainboard"):
            for path in sorted(folder.rglob("*")):
                if path.is_file() and path.suffix in {".patch", ".inc", ".h", ".json"}:
                    archive.write(path, path.relative_to(ROOT).as_posix())
    manifest = {
        "target": "STM32F103RET6 / Creality S1 28 KiB factory loader",
        "status": "experimental offline-verified candidate; installed board target unconfirmed",
        "working_machine_evidence": "Historical stock M115 is 2.0.8.26F4; do not select this F103 candidate for that same board",
        "marlin_baseline": BASELINE,
        "repository_base_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_status": "working-tree port; exact sources and companion patch included",
        "sd_load_address": "0x08007000", "application_vectors": "0x08010200",
        "updater_version": "0.2.0", "board_id": "0103E013", "usb_updates": True,
        "sd_bytes": len(combined), "sd_sha256": digest,
        "updater_padded_sha256": hashlib.sha256(combined[:0x9000]).hexdigest(),
        "physical_verified": False,
        "files": {p.relative_to(bundle).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(bundle.rglob("*")) if p.is_file() and p.name != "manifest.json"},
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(bundle.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(bundle))
    print(bundle.with_suffix(".zip"))
    print(f"SD image: {len(combined)} bytes, SHA256 {digest}")
    return bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    package(parser.parse_args().source)
