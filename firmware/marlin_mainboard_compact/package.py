"""Audit and assemble the compact F401 SD/USB kit, without hardware access."""

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

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from firmware.ender_aux_f401compact.host import MAX_PAYLOAD, validate_image  # noqa: E402
from firmware.marlin_mainboard_compact.prepare import verify  # noqa: E402

STOCK_SHA = "9a81f7564d62b2b131dcc96f0bf47725e3ee78c3b68b198bb54680f431e02710"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def audit_updater(elf_path, raw):
    from elftools.elf.elffile import ELFFile

    with elf_path.open("rb") as handle:
        elf = ELFFile(handle)
        vector = elf.get_section_by_name(".isr_vector")
        if (elf.header["e_machine"] != "EM_ARM" or vector is None
                or vector["sh_addr"] != 0x08010000 or vector["sh_size"] != 404):
            raise ValueError("Invalid compact updater vectors")
        symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
        if symbols["_estack"] != 0x20010000 or symbols["_ebss"] > 0x2000F000:
            raise ValueError("Updater exceeds its conservative RAM/stack allocation")
        if symbols["__flash_image_end"] != 0x08010000 + len(raw):
            raise ValueError("Updater binary size does not match ELF")
        segments = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        # GNU objcopy fills non-section alignment gaps with zero by default.
        reconstructed = bytearray(len(raw))
        for section in elf.iter_sections():
            if not section["sh_flags"] & 2 or not section["sh_size"] or section["sh_type"] == "SHT_NOBITS":
                continue
            owners = [s for s in segments if s.section_in_segment(section)]
            if len(owners) != 1:
                raise ValueError("Updater section lacks a unique load segment")
            segment = owners[0]
            start = segment["p_paddr"] + section["sh_addr"] - segment["p_vaddr"] - 0x08010000
            if not 0 <= start <= len(raw) - section["sh_size"]:
                raise ValueError("Updater section outside image bounds")
            reconstructed[start:start + section["sh_size"]] = section.data()
        if reconstructed != raw:
            raise ValueError("Updater ELF and binary differ")
    if not 408 <= len(raw) <= 65536 or b"E3AUX1 UPDATER 0.3.0 BOARD=0401C013\0" not in raw:
        raise ValueError("Wrong compact updater identity/size")
    stack, reset = struct.unpack_from("<II", raw)
    if stack != 0x20010000 or not reset & 1 or not 0x08010194 <= reset & ~1 < 0x08010000 + len(raw):
        raise ValueError("Invalid updater entry")


def assemble(updater, application):
    payload = validate_image(application)
    caps = (b"Cap:E3_MAINBOARD_V1:1", b"Cap:E3_MATERIAL_HEIGHT_V1:1",
            b"Cap:E3_USB_UPDATER_F401_V1:1", b"Cap:E3_Z_LIMIT_80_V1:1",
            b"Cap:E3_SURFACE_HEIGHT_V2:1", b"Cap:E3_RECOVERY_V1:1", b"Cap:E3_LIVE_Z_V1:1",
            b"E3SG:2 PROBE_Z:", b"E3HW:1 MCU:")
    if any(cap + b"\0" not in payload and cap + b"\n\0" not in payload for cap in caps):
        raise ValueError("Missing required runtime feature/hardware identity")
    if not 408 <= len(updater) <= 65536:
        raise ValueError("Updater does not fit its reserved sector")
    combined = updater.ljust(65536, b"\xff") + application
    if len(combined) > 0x30000 or combined[0x10200:] != payload:
        raise ValueError("Combined image crosses 256 KiB or has wrong application offset")
    return combined


def main():
    # Offline ELF tooling is only required for a firmware build, not imports of
    # the pure image validators by desktop/host tests.
    from firmware.marlin_mainboard_compact.audit_layout import audit

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--objdump", type=Path, required=True)
    parser.add_argument("--updater", type=Path, default=ROOT / "build/ender_aux_f401compact/updater.bin")
    parser.add_argument("--stock", type=Path, default=ROOT / "build/stock-f401-2.0.8.26.bin")
    args = parser.parse_args()
    verify(args.source)
    elf = args.source / ".pio/build/STM32F401RC_creality/firmware.elf"
    image = audit(elf, args.objdump)
    updater = args.updater.read_bytes()
    audit_updater(args.updater.with_suffix(".elf"), updater)
    combined = assemble(updater, image)
    if sha(args.stock.read_bytes()) != STOCK_SHA:
        raise ValueError("Stock recovery hash mismatch")
    digest = sha(combined)
    folder = ROOT / "dist" / f"e3-mainboard-f401-usb-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=False)
    sd_name = f"SD_CARD/STM32F4_UPDATE/e3c_{digest[:8]}.bin"
    sd = folder / sd_name
    sd.parent.mkdir(parents=True)
    sd.write_bytes(combined)
    (folder / "application.e3fw").write_bytes(image)
    shutil.copyfile(elf, folder / "application.elf")
    shutil.copyfile(args.updater.with_suffix(".elf"), folder / "updater.elf")
    for name in ("README.md", "install_pi_support.py"):
        shutil.copyfile(HERE / name, folder / name)
    readme = (folder / "README.md").read_text()
    (folder / "README.md").write_text(readme.replace("__PACKAGE_NAME__", folder.name)
                                       .replace("__SD_NAME__", sd.name))
    shutil.copyfile(ROOT / "firmware/ender_aux_f401compact/host.py", folder / "host.py")
    shutil.copyfile(ROOT / "scripts/diagnose_ender_startup.py", folder / "diagnose_ender_startup.py")
    shutil.copyfile(ROOT / "laser_aligner/machine/secondary_startup.py", folder / "secondary_startup.py")
    (folder / "requirements.txt").write_text("pyserial==3.5\n")
    recovery = folder / "RECOVERY_STOCK/STM32F4_UPDATE/stock_2_0_8_26_9a81f756.bin"
    recovery.parent.mkdir(parents=True)
    shutil.copyfile(args.stock, recovery)
    with zipfile.ZipFile(folder / "firmware-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        names = subprocess.check_output(["git", "-C", str(args.source), "ls-files"], text=True).splitlines()
        names = set(names) | set(json.loads((HERE / "source-files.json").read_text()))
        for name in sorted(names):
            if (args.source / name).is_file():
                archive.write(args.source / name, "Marlin/" + name)
        for project in ("ender_aux_f401compact", "marlin_mainboard_compact", "marlin_material", "marlin_mainboard"):
            for path in sorted((ROOT / "firmware" / project).rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, "E3/" + path.relative_to(ROOT).as_posix())
        for name in ("tests/marlin_material_harness.cpp", "tests/test_ender_aux_f401compact.py",
                     "tests/test_compact_f401_package.py", "tests/test_compact_pi_support.py",
                     "tests/test_compact_prepare.py",
                     "scripts/diagnose_ender_startup.py", "laser_aligner/machine/secondary_startup.py"):
            archive.write(ROOT / name, "E3/" + name)
    manifest = {
        "target": "Creality S1 F401 RC/256KiB or RE/512KiB; fixed 256KiB flash / 64KiB RAM layout",
        "status": "experimental operator-test candidate; physical validation pending",
        "board_id": "0401C013", "updater_version": "0.3.0", "usb_updates_in_first_install": True,
        "sd_file": sd_name, "sd_load_address": "0x08010000", "sd_bytes": len(combined),
        "sd_sha256": digest, "image_end": hex(0x08010000 + len(combined)),
        "payload_bytes": len(image) - 512, "payload_capacity": MAX_PAYLOAD,
        "application_vectors": "0x08020200", "usb_erase_sectors": [5],
        "stock_sha256": STOCK_SHA,
        "repository_base_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_state": "working tree; exact patched firmware sources included",
        "physical_verified": False, "z_ceiling_mm": 80,
        "halt_recovery_capability": "E3_RECOVERY_V1",
        "halt_recovery": "explicit fresh token reset only; no automatic motion resume",
        "live_z_capability": "E3_LIVE_Z_V1",
        "live_z": "M154 S1/S0 opt-in 5 Hz executed-step telemetry; homing frame invalid",
        "surface_height_capability": "E3_SURFACE_HEIGHT_V2",
        "surface_contact_range_mm": [-2, 65], "surface_clearance_range_mm": [20, 80],
        "z_ceiling_capability": "E3_Z_LIMIT_80_V1", "files": {},
    }
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            manifest["files"][path.relative_to(folder).as_posix()] = sha(path.read_bytes())
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(folder.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder))
    print(json.dumps({"package": str(folder.with_suffix('.zip')), "sd_file": sd_name,
                      "bytes": len(combined), "sha256": digest}, indent=2))


if __name__ == "__main__":
    main()
