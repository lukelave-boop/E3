"""Execute compiled compact HAL setup and M115 against fake GPIO/UART registers."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_SP

SENTINEL = 0x080F0000


def load(path: Path):
    uc = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
    for base, size in ((0x08000000, 0x100000), (0x20000000, 0x10000),
                       (0x40000000, 0x30000), (0xE000E000, 0x1000),
                       (0xE0042000, 0x1000), (0x1FFF7000, 0x1000)):
        uc.mem_map(base, size)
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        symbols = {s.name: s["st_value"] for s in elf.get_section_by_name(".symtab").iter_symbols()}
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                uc.mem_write(segment["p_vaddr"], segment.data())
    return uc, symbols


def call(uc, entry):
    returned = []

    def stop(emulator, _address, _size, _data):
        returned.append(True)
        emulator.emu_stop()

    hook = uc.hook_add(UC_HOOK_CODE, stop, begin=SENTINEL, end=SENTINEL)
    uc.reg_write(UC_ARM_REG_SP, 0x20010000)
    uc.reg_write(UC_ARM_REG_LR, SENTINEL | 1)
    uc.emu_start(entry | 1, SENTINEL + 2, timeout=2_000_000, count=200000)
    uc.hook_del(hook)
    if not returned:
        raise ValueError("Native profile routine did not return")


def audit_profile(path: Path) -> None:
    uc, symbols = load(path)
    writes = []

    def record(_emulator, _access, address, size, value, _data):
        if 0x40020000 <= address < 0x40021000:
            writes.append((address, size, value))

    uc.hook_add(UC_HOOK_MEM_WRITE, record)
    call(uc, symbols["_Z8HAL_initv"])
    for register, bit in ((0x40020018, 1), (0x40020018, 7), (0x40020418, 4)):
        if (register, 4, 1 << (16 + bit)) not in writes:
            raise ValueError(f"HAL setup failed to drive unused output low: {register:X}/{bit}")
        if any(address == register and value & (1 << bit) for address, _, value in writes):
            raise ValueError("HAL setup raised an unused heater or extrusion output")
        port = register - 0x18
        moder = int.from_bytes(uc.mem_read(port, 4), "little")
        if (moder >> (2 * bit)) & 3 != 1:
            raise ValueError("Unused heater/extrusion pin is not an output")

    required = ("_ZN10GcodeSuite3G39Ev", "_ZN10GcodeSuite4M115Ev", "_ZN10GcodeSuite4M997Ev",
                "_ZN10GcodeSuite4M106Ev", "_ZN10GcodeSuite4M107Ev", "_ZN10GcodeSuite4M123Ev",
                "_ZN10GcodeSuite4M280Ev")
    if any(name not in symbols for name in required):
        raise ValueError("A required native auxiliary command is absent")
    forbidden = ("_ZN10GcodeSuite4M104Ev", "_ZN10GcodeSuite4M109Ev", "_ZN10GcodeSuite4M140Ev",
                 "_ZN10GcodeSuite4M190Ev", "_ZN10GcodeSuite4M600Ev")
    if any(name in symbols for name in forbidden):
        raise ValueError("A removed heater/filament command is still linked")

    for device, capacity, offset in ((0x423, 256, 0.0), (0x433, 512, -2.375), (0x999, 123, -10.0)):
        emulator, names = load(path)
        emulator.mem_write(0xE0042000, (0x10000000 | device).to_bytes(4, "little"))
        emulator.mem_write(0x1FFF7A22, capacity.to_bytes(2, "little"))
        emulator.mem_write(names["_ZN5Probe6offsetE"] + 8, struct.pack("<f", offset))
        output = bytearray()

        def write_byte(emulator, _address, _size, _data, output=output):
            output.append(emulator.reg_read(UC_ARM_REG_R1) & 255)
            emulator.reg_write(UC_ARM_REG_R0, 1)
            emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

        write_address = names["_ZN14HardwareSerial5writeEh"] & ~1
        emulator.hook_add(UC_HOOK_CODE, write_byte, begin=write_address, end=write_address)
        call(emulator, names["_GLOBAL__sub_I_MSerial1"])
        call(emulator, names["_ZN10GcodeSuite4M115Ev"])
        text = output.decode("ascii")
        expected = ("FIRMWARE_NAME:Marlin", "EXTRUDER_COUNT:0", "Cap:EMERGENCY_PARSER:1",
                    "Cap:E3_MAINBOARD_V1:1", "Cap:E3_MATERIAL_HEIGHT_V1:1",
                    "Cap:E3_USB_UPDATER_F401_V1:1", "Cap:E3_COMPACT_F401_V1:1",
                    "Cap:E3_Z_LIMIT_80_V1:1",
                    "Cap:E3_SURFACE_HEIGHT_V2:1",
                    f"E3SG:2 PROBE_Z:{offset:.6f} RETRACT:5.000000 MIN:-2 MAX:65 CEILING:80",
                    "Cap:SDCARD:0", f"E3HW:1 MCU:{device:X} FLASH_KIB:{capacity}")
        if any(part not in text for part in expected):
            raise ValueError(f"Native M115 reported wrong capabilities/hardware: {text}")
    print("Compiled HAL drove PA1/PA7 heater gates and PB4 extrusion STEP low; compiled M115 passed three hardware-ID cases; fake MMIO/UART.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    audit_profile(parser.parse_args().elf)
