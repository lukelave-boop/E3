"""Prepare or verify a pinned F103RET6 application with the retained USB updater."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASELINE = "7fffa9270ff8fb5ee208a5b04f832e20bab19c60"


def prepare(source: Path) -> None:
    source = source.resolve()
    expected = json.loads((HERE / "source-files.json").read_text())
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != BASELINE:
        raise ValueError("F103 source must be the pinned Creality revision")
    changed = subprocess.check_output(["git", "-C", str(source), "diff", "--name-only"], text=True).splitlines()
    if changed:
        if set(changed) - expected.keys():
            raise ValueError("Preserving unexpected source modifications")
        for relative, digest in expected.items():
            if hashlib.sha256((source / relative).read_text(encoding="utf-8").encode()).hexdigest() != digest:
                raise ValueError(f"Preserving modified source: {relative}")
        return
    if subprocess.check_output(["git", "-C", str(source), "ls-files", "--others", "--exclude-standard"], text=True).strip():
        raise ValueError("Use a clean isolated source tree; untracked files are preserved")
    material = ROOT / "firmware/marlin_material"
    mainboard = ROOT / "firmware/marlin_mainboard"
    for patch in (material / "baseline.patch", material / "material.patch", mainboard / "mainboard.patch"):
        subprocess.run(["git", "-C", str(source), "apply", "--whitespace=nowarn", str(patch)], check=True)

    def replace(relative: str, old: str, new: str):
        path = source / relative
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            raise ValueError(f"Source substitution is not unique: {relative}")
        path.write_text(text.replace(old, new), encoding="utf-8", newline="\n")

    replace("Marlin/Configuration.h", "// #define USER_STM32F103  1\n#define USER_STM32F401  1", "#define USER_STM32F103  1\n#define PLATFORM_M997_SUPPORT\n// #define USER_STM32F401  1")
    replace("Marlin/Configuration.h", "#define EEPROM_AUTO_INIT", "//#define EEPROM_AUTO_INIT")
    replace("Marlin/Configuration_adv.h", "#define LASER_FEATURE //", "//#define LASER_FEATURE //")
    replace("Marlin/src/pins/pins.h", '"stm32f1/pins_CREALITY_S1.h"         // STM32F1                                env:STM32F103RET6_creality', '"stm32f1/pins_CREALITY_S1.h"         // STM32F1 env:STM32F103RET6_creality env:HAL_STM32F103RET6_creality')
    replace("ini/stm32f1.ini", "platform      = ststm32@~12.1", "platform      = ststm32@12.1.1")
    replace("ini/stm32f1.ini", "board_build.offset   = 0x7000\nboard_build.ldscript = ldscript.ld\nboard_upload.offset_address = 0x08007000", "board_build.offset   = 0x10200\nboard_build.ldscript = ldscript.ld\nboard_upload.offset_address = 0x08010200")
    replace("ini/stm32f1.ini", "-DTRANSFER_CLOCK_DIV=8", "-DTRANSFER_CLOCK_DIV=8 -Wl,--wrap=SystemInit")
    replace("Marlin/src/HAL/STM32/HAL.cpp", "void HAL_reboot() { NVIC_SystemReset(); }", 'void HAL_reboot() { NVIC_SystemReset(); }\n#include "e3_startup.inc"')
    replace("Marlin/src/gcode/control/M997.cpp", "void GcodeSuite::M997() {\n\n  flashFirmware(parser.intval('S'));\n\n}", '#include "e3_usb_update.inc"')
    replace("Marlin/src/gcode/host/M115.cpp", 'SERIAL_ECHOLNPGM("Cap:E3_MAINBOARD_V1:1");', 'SERIAL_ECHOLNPGM("Cap:E3_MAINBOARD_V1:1");\n  SERIAL_ECHOLNPGM("Cap:E3_USB_UPDATER_F103_V1:1");')
    for name, folder in {"e3_material_height.h": "module", "material_height.inc": "module", "G39.inc": "gcode/probe", "e3_startup.inc": "HAL/STM32"}.items():
        (source / "Marlin/src" / folder / name).write_bytes((material / "overlay" / name).read_bytes())
    (source / "Marlin/src/gcode/temp/e3_mainboard.inc").write_bytes((mainboard / "e3_mainboard.inc").read_bytes())
    (source / "Marlin/src/gcode/control/e3_usb_update.inc").write_bytes((HERE / "marlin_usb_update.inc").read_bytes())
    prepare(source)  # Verify the entire resulting source against reviewed hashes.


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    prepare(parser.parse_args().source)
