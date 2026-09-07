"""Execute actual patched Marlin G39/probe functions on an emulated Cortex-M4."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    pos = opening + 1
    while depth:
        depth += (source[pos] == "{") - (source[pos] == "}")
        pos += 1
    return source[start:pos]


def run(source: Path, toolchain: Path) -> None:
    functions = function((source / "Marlin/src/module/probe.cpp").read_text(encoding="utf-8"),
                         "float Probe::run_z_probe(")
    for path in ("Marlin/src/module/material_height.inc", "Marlin/src/gcode/probe/G39.inc"):
        functions += "\n" + "\n".join(line for line in (source / path).read_text().splitlines()
                                       if not line.startswith("#include"))
    harness = (ROOT / "tests/marlin_material_harness.cpp").read_text().replace("// MARKER", functions)
    output = ROOT / "build/marlin-material-tests"
    output.mkdir(parents=True, exist_ok=True)
    (output / "harness.cpp").write_text(harness)
    linker = output / "test.ld"
    linker.write_text("ENTRY(test_main)\nSECTIONS { . = 0x08000000; .text : { *(.text*) *(.rodata*) } "
                      ". = 0x20000000; .data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } }")
    compiler = toolchain / ("arm-none-eabi-g++.exe" if (toolchain / "arm-none-eabi-g++.exe").exists()
                            else "arm-none-eabi-g++")
    for total, extra in ((2, 0), (3, 0), (3, 1)):
        elf_path = output / f"test-{total}-{extra}.elf"
        subprocess.run([str(compiler), "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft",
                        "-std=c++14", "-Os", "-fno-builtin", "-fno-exceptions", "-fno-rtti",
                        "-nostdlib", "-fno-tree-loop-distribute-patterns",
                        "-Wall", "-Wextra", "-Werror", f"-DTOTAL_PROBING={total}",
                        f"-DEXTRA_PROBING={extra}", f"-DMULTIPLE_PROBING={total - extra}",
                        "-I", str(source / "Marlin/src/module"),
                        "-T", str(linker), str(output / "harness.cpp"), "-lm", "-lgcc", "-o", str(elf_path)],
                       check=True)
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

        def done(uc, _address, _size, _data, finished=finished):
            finished.append(uc.reg_read(UC_ARM_REG_R0))
            uc.emu_stop()

        emulator.hook_add(UC_HOOK_CODE, done, begin=sentinel, end=sentinel)
        emulator.reg_write(UC_ARM_REG_SP, 0x201FF000)
        emulator.reg_write(UC_ARM_REG_LR, sentinel | 1)
        emulator.emu_start(entry | 1, sentinel + 2, timeout=20_000_000, count=20_000_000)
        if finished != [0]:
            raise RuntimeError(f"Production probe test failed: TOTAL_PROBING={total}, line={finished}")
        print(f"Production G39/probe Cortex-M4 tests passed, TOTAL_PROBING={total}, EXTRA={extra}; fake I/O.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--toolchain", type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.toolchain.resolve())
