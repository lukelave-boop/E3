"""Execute the actual firmware kill prefix with fake STM32 GPIO registers."""

from __future__ import annotations

import argparse
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_SP


def audit_outputs(path: Path) -> None:
    uc = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
    uc.mem_map(0x08000000, 0x100000)
    uc.mem_map(0x20000000, 0x20000)
    uc.mem_map(0x40020000, 0x3000)
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        symbols = elf.get_section_by_name(".symtab")
        def address(name):
            return symbols.get_symbol_by_name(name)[0]["st_value"]
        entry = address("_Z4killPKcS0_b")
        fastio = address("_Z11FastIO_initv")
        boundary = address("_ZN11Temperature19disable_all_heatersEv") & ~1
        speeds = address("_ZN11Temperature9fan_speedE")
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                uc.mem_write(segment["p_vaddr"], segment.data())
    # FastIO's RAM port map is initialized during normal Marlin startup.
    initialized = []
    def initialized_callback(emulator, _address, _size, _data):
        initialized.append(True)
        emulator.emu_stop()
    hook = uc.hook_add(UC_HOOK_CODE, initialized_callback, begin=0x080F0000, end=0x080F0000)
    uc.reg_write(UC_ARM_REG_SP, 0x20010000)
    uc.reg_write(UC_ARM_REG_LR, 0x080F0001)
    uc.emu_start(fastio | 1, 0x080F0002, timeout=1_000_000, count=10000)
    uc.hook_del(hook)
    if not initialized:
        raise ValueError("Native FastIO initialization did not finish")
    uc.mem_write(speeds, b"\xff\xff")
    writes = []
    completed = []

    def record(_uc, _access, address, size, value, _data):
        if 0x40020000 <= address < 0x40023000:
            writes.append((address, size, value))

    def done(emulator, _address, _size, _data):
        completed.append(True)
        emulator.emu_stop()

    uc.hook_add(UC_HOOK_MEM_WRITE, record)
    uc.hook_add(UC_HOOK_CODE, done, begin=boundary, end=boundary)
    uc.reg_write(UC_ARM_REG_SP, 0x20010000)
    uc.reg_write(UC_ARM_REG_LR, 0x080F0001)
    for register in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2):
        uc.reg_write(register, 0)
    uc.emu_start(entry, 0x080F0002, timeout=1_000_000, count=10000)
    # BSRR high half resets bit 0 without touching other GPIO outputs.
    if (not completed or uc.mem_read(speeds, 2) != b"\x00\x00"
            or (0x40020018, 4, 0x10000) not in writes
            or (0x40020818, 4, 0x10000) not in writes):
        raise ValueError(f"Native kill did not clear both PWM settings and both fan GPIOs: {writes}")
    print("Actual compiled kill cleared FAN1/PC0 and FAN2/PA0 and their PWM state; fake MMIO.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    audit_outputs(parser.parse_args().elf)
