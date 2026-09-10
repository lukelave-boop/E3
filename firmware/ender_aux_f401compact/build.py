"""Build and package the retained-loader E3 auxiliary firmware; never flash it."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VERSION = "0.3.0"
UPDATER_BASE = 0x08010000
UPDATER_SIZE = 0x10000
FLAGS = [
    "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-std=c11", "-Os", "-g3",
    "-ffreestanding", "-fno-builtin", "-fno-common", "-fno-stack-protector",
    "-fno-tree-loop-distribute-patterns", "-ffunction-sections", "-fdata-sections",
    "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wshadow", "-Wundef",
    "-nostdlib", "-Wl,--gc-sections", "-Wl,--build-id=none",
]


def toolchain_bin(requested: str | None = None) -> Path:
    if requested:
        folder = Path(requested).resolve()
        if (folder / "bin").is_dir():
            folder /= "bin"
        return folder
    explicit = os.environ.get("ARM_GCC_BIN")
    if explicit:
        return toolchain_bin(explicit)
    found = shutil.which("arm-none-eabi-gcc")
    if found:
        return Path(found).resolve().parent
    flat = ROOT / "build" / "firmware-tools" / "bin"
    if (flat / ("arm-none-eabi-gcc.exe" if os.name == "nt" else "arm-none-eabi-gcc")).is_file():
        return flat
    for folder in sorted((ROOT / "build" / "firmware-tools").glob("arm-gnu-toolchain-*/bin")):
        return folder
    raise RuntimeError("Set ARM_GCC_BIN or --toolchain to the Arm GNU Toolchain bin directory")


def tool(folder: Path, name: str) -> str:
    path = folder / ("arm-none-eabi-" + name + (".exe" if os.name == "nt" else ""))
    if not path.is_file():
        raise RuntimeError(f"Missing compiler tool: {path}")
    return str(path)


def compile_target(folder: Path, output: Path, name: str, sources: list[str], linker: str) -> bytes:
    elf = output / f"{name}.elf"
    command = [tool(folder, "gcc"), *FLAGS, "-I", str(HERE), "-T", str(HERE / linker)]
    command += [str(HERE / source) for source in sources]
    command += [f"-Wl,-Map={output / (name + '.map')}", "-lgcc", "-o", str(elf)]
    subprocess.run(command, check=True)
    subprocess.run([tool(folder, "objcopy"), "-O", "binary", str(elf), str(output / f"{name}.bin")], check=True)
    subprocess.run([tool(folder, "size"), str(elf)], check=True)
    return (output / f"{name}.bin").read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain")
    args = parser.parse_args()
    output = ROOT / "build" / "ender_aux_f401compact"
    output.mkdir(parents=True, exist_ok=True)
    compile_target(toolchain_bin(args.toolchain), output, "updater",
                   ["startup.c", "platform.c", "image.c", "protocol.c", "updater.c", "diagnostic.c"], "updater.ld")


if __name__ == "__main__":
    main()
