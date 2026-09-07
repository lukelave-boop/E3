"""Run the compiled Cortex-M4 updater core against fake flash in Unicorn.

These are executable core tests, not a simulation of STM32 peripherals,
Creality's loader, brownouts, or the electrical board.
"""

from __future__ import annotations

import argparse
import subprocess

from build import FLAGS, HERE, ROOT, tool, toolchain_bin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain")
    args = parser.parse_args()
    from elftools.elf.elffile import ELFFile
    from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP

    out = ROOT / "build" / "ender_aux"
    out.mkdir(parents=True, exist_ok=True)
    linker = out / "core_test.ld"
    linker.write_text("""ENTRY(tests_main)
MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 2M
RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 2M }
SECTIONS {
 .text : { *(.text*) *(.rodata*) } > FLASH
 .data : { *(.data*) } > RAM
 .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
 /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }
}
""", encoding="utf-8")
    elf_path = out / "core_test.elf"
    subprocess.run([
        tool(toolchain_bin(args.toolchain), "gcc"), *FLAGS, "-DE3_CORE_TEST", "-I", str(HERE),
        "-T", str(linker), str(ROOT / "tests" / "firmware_core_harness.c"),
        str(HERE / "image.c"), str(HERE / "protocol.c"), str(HERE / "application.c"),
        "-lgcc", "-o", str(elf_path),
    ], check=True)
    emulator = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    emulator.mem_map(0x08000000, 0x200000)
    emulator.mem_map(0x20000000, 0x200000)
    with elf_path.open("rb") as handle:
        elf = ELFFile(handle)
        entry = elf.header["e_entry"]
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                emulator.mem_write(segment["p_vaddr"], segment.data())
    sentinel = 0x081F0000
    emulator.mem_write(sentinel, b"\x00\xbe")
    finished = []

    def on_instruction(uc, address, _size, _data):
        if address == sentinel:
            finished.append(uc.reg_read(UC_ARM_REG_R0))
            uc.emu_stop()

    emulator.hook_add(UC_HOOK_CODE, on_instruction, begin=sentinel, end=sentinel)
    emulator.reg_write(UC_ARM_REG_SP, 0x201FF000)
    emulator.reg_write(UC_ARM_REG_LR, sentinel | 1)
    emulator.emu_start(entry | 1, sentinel + 2, timeout=60_000_000, count=150_000_000)
    if not finished:
        raise RuntimeError("Core tests exceeded execution bound")
    if finished[0]:
        raise RuntimeError(f"Cortex-M4 core test failure code/line: {finished[0]}")
    print("Compiled Cortex-M4 core tests passed (fake flash/UART; no physical hardware).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
