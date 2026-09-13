"""Compile the F103RET6 retained updater offline; never flash hardware."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FLAGS = [
    "-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft", "-std=c11", "-Os", "-g3",
    "-ffreestanding", "-fno-builtin", "-fno-common", "-fno-stack-protector",
    "-fno-tree-loop-distribute-patterns", "-ffunction-sections", "-fdata-sections",
    "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wshadow", "-Wundef",
    "-nostdlib", "-Wl,--gc-sections", "-Wl,--build-id=none",
]


def build(toolchain: Path) -> Path:
    output = ROOT / "build/ender_aux_f103"
    output.mkdir(exist_ok=True)
    suffix = ".exe" if (toolchain / "arm-none-eabi-gcc.exe").exists() else ""
    elf = output / "updater.elf"
    subprocess.run([
        str(toolchain / ("arm-none-eabi-gcc" + suffix)), *FLAGS,
        "-I", str(HERE), "-T", str(HERE / "updater.ld"),
        *(str(HERE / name) for name in ("startup.c", "platform.c", "image.c", "protocol.c", "updater.c")),
        "-lgcc", "-o", str(elf), f"-Wl,-Map={output / 'updater.map'}",
    ], check=True)
    subprocess.run([str(toolchain / ("arm-none-eabi-objcopy" + suffix)), "-O", "binary", str(elf), str(output / "updater.bin")], check=True)
    subprocess.run([str(toolchain / ("arm-none-eabi-size" + suffix)), str(elf)], check=True)
    return elf


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", type=Path, default=ROOT / "build/firmware-tools/bin")
    parser.add_argument("--marlin-source", type=Path)
    parser.add_argument("--core", type=Path, default=ROOT / "build/platformio")
    args = parser.parse_args()
    build(args.toolchain.resolve())
    if args.marlin_source:
        import os
        import sys

        from prepare_marlin import prepare
        prepare(args.marlin_source)
        subprocess.run([sys.executable, "-m", "platformio", "run", "-d", str(args.marlin_source),
                        "-e", "HAL_STM32F103RET6_creality", "-j", "4"],
                       env={**os.environ, "PLATFORMIO_CORE_DIR": str(args.core.resolve())}, check=True)
