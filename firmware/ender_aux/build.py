"""Build and package the retained-loader E3 auxiliary firmware; never flash it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

from host import HEADER_SIZE, MAX_PAYLOAD, VECTOR_BASE, pack_image, validate_image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VERSION = "0.2.0"
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


def source_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted(HERE.iterdir()):
        if path.is_file() and path.suffix in {".c", ".h", ".ld", ".py", ".md", ".txt"}:
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain")
    args = parser.parse_args(argv)
    folder = toolchain_bin(args.toolchain)
    output = ROOT / "build" / "ender_aux"
    output.mkdir(parents=True, exist_ok=True)
    common = ["startup.c", "platform.c"]
    updater = compile_target(folder, output, "updater", [*common, "image.c", "protocol.c", "updater.c"], "updater.ld")
    application = compile_target(folder, output, "application", [*common, "application.c", "bench_inputs.c"], "application.ld")
    stack, reset = struct.unpack_from("<II", updater)
    if not (8 <= len(updater) <= UPDATER_SIZE and 0x20000000 < stack <= 0x20018000 and stack % 8 == 0
            and reset & 1 and UPDATER_BASE <= reset & ~1 < UPDATER_BASE + len(updater)):
        raise RuntimeError("Updater vector/size validation failed")
    package = pack_image(application)
    validate_image(package)
    combined = updater.ljust(UPDATER_SIZE, b"\xff") + package
    digest = source_digest()
    # Content-addressed names avoid stale Creality filename rejection without
    # embedding dates in compiler input or affecting reproducibility.
    bundle = ROOT / "dist" / f"ender-aux-{VERSION}-{digest[:8]}"
    sd = bundle / "SD_CARD" / "STM32F4_UPDATE"
    sd.mkdir(parents=True, exist_ok=True)
    sd_name = f"e3aux_{hashlib.sha256(combined).hexdigest()[:8]}.bin"
    (sd / sd_name).write_bytes(combined)
    (bundle / "application.e3fw").write_bytes(package)
    for name in ("host.py", "bench.py", "BENCH_GUIDE.md"):
        shutil.copyfile(HERE / name, bundle / name)
    shutil.copyfile(HERE / "UPLOAD_README.md", bundle / "UPLOAD_README.md")
    (bundle / "requirements.txt").write_text("pyserial==3.5\n", encoding="utf-8")
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    compiler = subprocess.run([tool(folder, "gcc"), "--version"], text=True, capture_output=True, check=True).stdout.splitlines()[0]
    manifest = {
        "project": "E3 auxiliary retained-loader bench firmware", "version": VERSION,
        "target": "STM32F401RET6 / CR4NS200141C13", "board_id": "0401E013",
        "verification": "experimental; this generated build requires its own operator acceptance",
        "updater_version": "0.2.0", "application_mode": "BENCH", "physical_outputs": "disabled",
        "source_sha256": digest, "repository_base_revision": revision,
        "compiler": compiler, "compiler_flags": FLAGS,
        "uart": {"interface": "USART1 PA9/PA10 via CH340", "baud": 115200, "format": "8N1"},
        "regions": {"stock_loader_preserved": ["0x08000000", "0x08010000"],
                    "updater": ["0x08010000", "0x08020000"],
                    "application_header": ["0x08020000", f"0x{VECTOR_BASE:08X}"],
                    "application_payload_max_bytes": MAX_PAYLOAD, "header_bytes": HEADER_SIZE},
        "sd_install_assumption": "Existing loader loads .bin at 0x08010000; must be physically verified on spare",
        "capabilities": ["identity", "output-off baseline", "application-only serial update",
                         "simulated FAN1/FAN2 percentages", "simulated Z/probe", "raw PC14 input"],
        "not_implemented": ["physical motion", "physical probe actuation", "laser",
                            "physical fan/air-assist ON", "automatic rollback", "production machine integration"],
        "files": {},
    }
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            data = path.read_bytes()
            manifest["files"][path.relative_to(bundle).as_posix()] = {
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = bundle.parent / (bundle.name + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                handle.write(path, arcname=str(Path(bundle.name) / path.relative_to(bundle)))
    print(f"SD image: {sd / sd_name}")
    print(f"Package: {archive}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
