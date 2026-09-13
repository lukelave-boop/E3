"""Run the production non-motion Z restore command on an emulated Cortex-M4."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP

ROOT = Path(__file__).resolve().parents[2]


def run(source: Path, toolchain: Path) -> None:
    production = (source / "Marlin/src/gcode/temp/e3_mainboard.inc").read_text()
    production = "\n".join(line for line in production.splitlines()
                           if not line.lstrip().startswith("#include"))
    harness = (ROOT / "tests/marlin_z_restore_harness.cpp").read_text().replace("// MARKER", production)
    output = ROOT / "build/marlin-z-restore-tests"
    output.mkdir(parents=True, exist_ok=True)
    cpp = output / "harness.cpp"
    cpp.write_text(harness)
    linker = output / "test.ld"
    linker.write_text("ENTRY(test_main)\nSECTIONS { . = 0x08000000; .text : { *(.text*) *(.rodata*) } "
                      ". = 0x20000000; .data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } }")
    compiler = toolchain / ("arm-none-eabi-g++.exe" if (toolchain / "arm-none-eabi-g++.exe").exists()
                            else "arm-none-eabi-g++")
    elf_path = output / "test.elf"
    subprocess.run([str(compiler), "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft",
                    "-std=c++14", "-Os", "-fno-builtin", "-fno-exceptions", "-fno-rtti",
                    "-nostdlib", "-fno-tree-loop-distribute-patterns", "-Wall", "-Wextra", "-Werror",
                    "-I", str(source / "Marlin/src/module"), "-T", str(linker), str(cpp),
                    "-lm", "-lgcc", "-o", str(elf_path)], check=True)
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

    def done(uc, _address, _size, _data):
        finished.append(uc.reg_read(UC_ARM_REG_R0))
        uc.emu_stop()

    emulator.hook_add(UC_HOOK_CODE, done, begin=sentinel, end=sentinel)
    emulator.reg_write(UC_ARM_REG_SP, 0x201FF000)
    emulator.reg_write(UC_ARM_REG_LR, sentinel | 1)
    emulator.emu_start(entry | 1, sentinel + 2, timeout=5_000_000, count=2_000_000)
    if finished != [0]:
        raise RuntimeError(f"Production M124 restore test failed, harness line={finished}")
    print("Production M124 Cortex-M4 acceptance/rejection tests passed; fake I/O, no motion.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.toolchain.resolve())
