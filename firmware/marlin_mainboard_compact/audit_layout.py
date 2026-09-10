"""Audit an ELF and package it for the retained updater. No hardware access."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PRIMASK, UC_ARM_REG_SP

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from firmware.ender_aux_f401compact.host import FLASH_END, VECTOR_BASE, pack_image, validate_image  # noqa: E402


def audit_startup(elf_path: Path) -> None:
    """Run the real wrapper and CMSIS code with fake RCC/SCB registers."""
    emulator = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    emulator.mem_map(0x08000000, 0x100000)
    emulator.mem_map(0x20000000, 0x20000)
    emulator.mem_map(0x40023000, 0x1000)
    emulator.mem_map(0xE000E000, 0x1000)
    with elf_path.open("rb") as handle:
        elf = ELFFile(handle)
        wrapper = elf.get_section_by_name(".symtab").get_symbol_by_name("__wrap_SystemInit")[0]
        entry = wrapper["st_value"]
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                emulator.mem_write(segment["p_vaddr"], segment.data())
    sentinel = 0x080F0000
    finished = []

    def done(uc, _address, _size, _data):
        finished.append(True)
        uc.emu_stop()

    emulator.hook_add(UC_HOOK_CODE, done, begin=sentinel, end=sentinel)
    emulator.reg_write(UC_ARM_REG_SP, 0x20010000)
    emulator.reg_write(UC_ARM_REG_LR, sentinel | 1)
    emulator.reg_write(UC_ARM_REG_PRIMASK, 1)
    emulator.emu_start(entry, sentinel + 2, timeout=1_000_000, count=1000)
    vector_address = int.from_bytes(emulator.mem_read(0xE000ED08, 4), "little")
    if not finished or vector_address != VECTOR_BASE or emulator.reg_read(UC_ARM_REG_PRIMASK) != 0:
        raise ValueError("Startup failed to install application VTOR and restore interrupts")


def audit(elf_path: Path, objdump: Path) -> bytes:
    chunks = []
    with elf_path.open("rb") as handle:
        elf = ELFFile(handle)
        if elf.header["e_machine"] != "EM_ARM":
            raise ValueError("Expected ARM firmware")
        vectors = elf.get_section_by_name(".isr_vector")
        if vectors is None or vectors["sh_addr"] != VECTOR_BASE or vectors["sh_size"] not in {392, 404}:
            raise ValueError("Vector table does not match the retained updater layout")
        reset = elf.get_section_by_name(".symtab").get_symbol_by_name("Reset_Handler")[0]
        reset_start, reset_size = reset["st_value"] & ~1, reset["st_size"]
        segments = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        # ELF program headers can share a 64 KiB aligned segment below the
        # vector table. Only allocated sections belong in the flash binary.
        for section in elf.iter_sections():
            if not section["sh_flags"] & 2 or not section["sh_size"]:
                continue
            if section["sh_addr"] >= 0x20000000 and (
                section["sh_addr"] + section["sh_size"] > 0x20010000
            ):
                raise ValueError("ELF exceeds the conservative 64 KiB RAM profile")
            if section["sh_type"] == "SHT_NOBITS":
                continue
            owners = [s for s in segments if s.section_in_segment(section)]
            if len(owners) != 1:
                raise ValueError("Allocated section lacks a unique load segment")
            segment = owners[0]
            address = segment["p_paddr"] + section["sh_addr"] - segment["p_vaddr"]
            if address < VECTOR_BASE or address + section["sh_size"] > FLASH_END:
                raise ValueError("ELF load image overlaps protected flash or exceeds capacity")
            chunks.append((address, section.data()))
    disassembly = subprocess.check_output([str(objdump), "-d", str(elf_path)], text=True)

    def body(symbol: str) -> str:
        match = re.search(rf"^[0-9a-f]+ <{re.escape(symbol)}>:\n(.*?)(?=\n\n|\Z)",
                          disassembly, re.M | re.S)
        if match is None:
            raise ValueError(f"Missing firmware symbol: {symbol}")
        return match[1]

    reset_body = "\n".join(line for line in disassembly.splitlines()
                           if (match := re.match(r"\s*([0-9a-f]+):", line))
                           and reset_start <= int(match[1], 16) < reset_start + reset_size)
    if not re.search(r"<__wrap_SystemInit>.*<__libc_init_array>", reset_body, re.S):
        raise ValueError("Reset handler bypasses the retained-loader startup adaptation")
    wrapper = body("__wrap_SystemInit")
    if not re.search(r"<SystemInit>.*\bdsb\b.*\bisb\b.*\bcpsie\s+i", wrapper, re.S):
        raise ValueError("Startup does not restore interrupts after CMSIS initialization")
    audit_startup(elf_path)
    end = max(address + len(data) for address, data in chunks)
    payload = bytearray(b"\xff" * (end - VECTOR_BASE))
    for address, data in chunks:
        payload[address - VECTOR_BASE:address - VECTOR_BASE + len(data)] = data
    image = pack_image(bytes(payload))
    validate_image(image)
    return image
