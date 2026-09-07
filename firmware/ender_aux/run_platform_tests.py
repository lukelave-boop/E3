"""Execute production flash/GPIO wrappers against fake MMIO and flash in Unicorn.

This loads updater.elf, including its SRAM flash routines. It tests production
range/identity guards and inactive GPIO register requests. It does not emulate
STM32 timing, clocks, reset/boot sequences, watchdogs, power faults, stock-loader
behavior, or electrical outputs. No controller or serial port is accessed.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

FLASH_START = 0x08000000
APP_START = 0x08020000
FLASH_END = 0x08080000
RAM_START = 0x20000000
RAM_END = 0x20018000
FLASH_KEYR = 0x40023C04
FLASH_SR = 0x40023C0C
FLASH_CR = 0x40023C10
FLASH_LOCK = 1 << 31
DEVICE_ID = 0xE0042000
FLASH_SIZE = 0x1FFF7A22
GPIO_PORTS = (0x40020000, 0x40020400, 0x40020800)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class PlatformModel:
    """Only model the register behavior needed to call the compiled wrappers."""

    def __init__(self, elf_path: Path):
        from elftools.elf.elffile import ELFFile
        from unicorn import (
            UC_ARCH_ARM,
            UC_HOOK_CODE,
            UC_HOOK_MEM_READ,
            UC_HOOK_MEM_WRITE,
            UC_MODE_MCLASS,
            UC_MODE_THUMB,
            Uc,
        )

        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.uc.mem_map(FLASH_START, FLASH_END - FLASH_START)
        self.uc.mem_map(RAM_START, RAM_END - RAM_START)
        self.uc.mem_map(0x40000000, 0x30000)
        self.uc.mem_map(0xE0000000, 0x50000)
        self.uc.mem_map(0x1FFF7000, 0x1000)
        self.uc.mem_write(FLASH_START, b"\xff" * (FLASH_END - FLASH_START))
        with elf_path.open("rb") as handle:
            elf = ELFFile(handle)
            require(elf.elfclass == 32 and elf.header["e_machine"] == "EM_ARM", "Expected a 32-bit ARM ELF")
            symbols = elf.get_section_by_name(".symtab")
            require(symbols is not None, "Production ELF must retain its symbol table")
            self.symbols = {symbol.name: int(symbol["st_value"]) for symbol in symbols.iter_symbols()}
            require(self.symbols.get("__image_start") == 0x08010000, "ELF must be the production updater")
            for segment in elf.iter_segments():
                if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                    # RAM functions are copied to their run addresses, as startup
                    # would do. Their flash load image is retained for snapshots.
                    self.uc.mem_write(segment["p_vaddr"], segment.data())
                    if segment["p_paddr"] != segment["p_vaddr"]:
                        self.uc.mem_write(segment["p_paddr"], segment.data())

        for name in ("runtime_ready", "platform_program_word", "platform_erase_application", "platform_safe_outputs"):
            require(name in self.symbols, f"Required production symbol is missing: {name}")
        self.sentinel = FLASH_START
        self.uc.mem_write(self.sentinel, b"\x00\xbe")
        self.protected = bytes(self.uc.mem_read(FLASH_START, APP_START - FLASH_START))
        self.flash_cr = FLASH_LOCK
        self.unlock_stage = 0
        self.control_writes: list[int] = []
        self.key_writes: list[int] = []
        self.program_writes: list[tuple[int, int, int]] = []
        self.erased: list[int] = []
        self.output_levels = {port: 0xFFFF for port in GPIO_PORTS}
        self.return_value: int | None = None
        self.uc.hook_add(UC_HOOK_CODE, self._returned, begin=self.sentinel, end=self.sentinel)
        self.uc.hook_add(UC_HOOK_MEM_READ, self._read_mmio, begin=FLASH_SR, end=FLASH_CR + 3)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._write_mmio, begin=0x40000000, end=0x4002FFFF)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._program_flash, begin=FLASH_START, end=FLASH_END - 1)
        self.set_target()

    def word(self, address: int) -> int:
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def set_target(self, *, device: int = 0x433, size: int = 512, ready: bool = True) -> None:
        self.uc.mem_write(DEVICE_ID, struct.pack("<I", device))
        self.uc.mem_write(FLASH_SIZE, struct.pack("<H", size))
        self.uc.mem_write(self.symbols["runtime_ready"], bytes([ready]))

    def clear_events(self) -> None:
        self.control_writes.clear()
        self.key_writes.clear()
        self.program_writes.clear()
        self.erased.clear()

    def _read_mmio(self, uc, _access, address, _size, _value, _data):
        if address == FLASH_SR:
            # Ready, no error: emulate write-one-to-clear status instead of RAM.
            uc.mem_write(FLASH_SR, struct.pack("<I", 0))
        elif address == FLASH_CR:
            uc.mem_write(FLASH_CR, struct.pack("<I", self.flash_cr))

    def _write_mmio(self, uc, _access, address, size, value, _data):
        if address == FLASH_KEYR:
            require(size == 4, "Flash unlock key has wrong width")
            self.key_writes.append(value)
            if value == 0x45670123:
                self.unlock_stage = 1
            elif value == 0xCDEF89AB and self.unlock_stage == 1:
                self.flash_cr &= ~FLASH_LOCK
                self.unlock_stage = 0
            else:
                raise RuntimeError("Unexpected flash unlock sequence")
        elif address == FLASH_CR:
            self.control_writes.append(value)
            require(not self.flash_cr & FLASH_LOCK, "Production tried changing locked flash control")
            require(not value & (1 << 2), "Mass erase requested")
            self.flash_cr = value
            if value & (1 << 16):
                require(value & (1 << 1), "Flash START without sector erase")
                sector = (value >> 3) & 15
                require(5 <= sector <= 7, f"Production attempted protected sector erase: {sector}")
                first = APP_START + (sector - 5) * 0x20000
                self.erased.append(sector)
                uc.mem_write(first, b"\xff" * 0x20000)
                self.flash_cr &= ~(1 << 16)  # Hardware clears START after acceptance.
        elif address in (0x40023C08, 0x40023C14):
            raise RuntimeError("Option-byte mutation requested")
        for port in GPIO_PORTS:
            if address == port + 0x18:
                self.output_levels[port] |= value & 0xFFFF
                self.output_levels[port] &= ~((value >> 16) & 0xFFFF)

    def _program_flash(self, uc, _access, address, size, value, _data):
        require(not self.flash_cr & FLASH_LOCK and self.flash_cr & 1, "Flash write without unlocked programming mode")
        require(APP_START <= address and address + size <= FLASH_END, "Flash write crossed a protected boundary")
        require(size == 1, "Production byte-programming width changed")
        previous = uc.mem_read(address, size)[0]
        require(previous & value == value, "Flash programming attempted a 0-to-1 bit transition")
        self.program_writes.append((address, size, value))

    def _returned(self, uc, _address, _size, _data):
        from unicorn.arm_const import UC_ARM_REG_R0

        self.return_value = uc.reg_read(UC_ARM_REG_R0)
        uc.emu_stop()

    def call(self, name: str, *args: int) -> int:
        from unicorn.arm_const import (
            UC_ARM_REG_CONTROL,
            UC_ARM_REG_LR,
            UC_ARM_REG_PRIMASK,
            UC_ARM_REG_R0,
            UC_ARM_REG_R1,
            UC_ARM_REG_SP,
        )

        require(len(args) <= 2, "Harness supports at most two arguments")
        self.return_value = None
        self.uc.reg_write(UC_ARM_REG_CONTROL, 0)
        self.uc.reg_write(UC_ARM_REG_PRIMASK, 0)
        self.uc.reg_write(UC_ARM_REG_SP, RAM_END)
        self.uc.reg_write(UC_ARM_REG_LR, self.sentinel | 1)
        for register, argument in zip((UC_ARM_REG_R0, UC_ARM_REG_R1), args, strict=False):
            self.uc.reg_write(register, argument)
        self.uc.emu_start(self.symbols[name] | 1, self.sentinel + 2, timeout=20_000_000, count=20_000_000)
        require(self.return_value is not None, f"{name} exceeded execution bound")
        require(bytes(self.uc.mem_read(FLASH_START, APP_START - FLASH_START)) == self.protected,
                f"{name} changed a retained bootloader/updater byte")
        return self.return_value

    def require_no_flash_activity(self, context: str) -> None:
        require(not (self.control_writes or self.key_writes or self.program_writes or self.erased),
                f"Rejected {context} nevertheless accessed flash programming controls")


def run_tests(model: PlatformModel) -> int:
    cases = 0
    invalid_addresses = (
        0, FLASH_START, 0x0800FFFC, 0x08010000, APP_START - 4, APP_START - 1,
        APP_START + 1, FLASH_END - 3, FLASH_END, 0xFFFFFFFF, RAM_START,
    )
    for address in invalid_addresses:
        model.clear_events()
        require(model.call("platform_program_word", address, 0x12345678) == 0,
                f"Invalid program address accepted: 0x{address:08X}")
        model.require_no_flash_activity(f"address 0x{address:08X}")
        cases += 1

    for target in ({"device": 0x423}, {"device": 0}, {"size": 384}, {"size": 256}, {"ready": False}):
        model.set_target(**target)
        for function, args in (("platform_program_word", (APP_START, 0)), ("platform_erase_application", ())):
            model.clear_events()
            require(model.call(function, *args) == 0, f"{function} accepted unsupported target {target}")
            model.require_no_flash_activity(f"target {target}")
            cases += 1
    model.set_target()

    for address, value in ((APP_START, 0x12345678), (FLASH_END - 4, 0x76543210)):
        model.clear_events()
        require(model.call("platform_program_word", address, value) == 1, "Valid boundary programming rejected")
        require(model.word(address) == value, "Accepted program word differs from requested value")
        require([item[0] for item in model.program_writes] == list(range(address, address + 4)),
                "Valid word did not program exactly its four bytes")
        require(model.flash_cr & FLASH_LOCK, "Flash was left unlocked after successful programming")
        require(not model.erased, "Programming unexpectedly erased a sector")
        cases += 1

    model.clear_events()
    require(model.call("platform_program_word", APP_START, 0x12345678) == 1, "Idempotent word rejected")
    model.require_no_flash_activity("unchanged word")
    cases += 1
    require(model.call("platform_program_word", APP_START, 0xFFFFFFFF) == 0, "0-to-1 programming accepted")
    model.require_no_flash_activity("0-to-1 word")
    require(model.word(APP_START) == 0x12345678, "Rejected word changed flash")
    cases += 1

    model.uc.mem_write(APP_START, b"\x00" * (FLASH_END - APP_START))
    model.clear_events()
    require(model.call("platform_erase_application") == 1, "Valid application erase rejected")
    require(model.erased == [5, 6, 7], "Application erase did not touch exactly sectors 5, 6, 7")
    require(bytes(model.uc.mem_read(APP_START, FLASH_END - APP_START)) == b"\xff" * (FLASH_END - APP_START),
            "Application erase left non-erased bytes")
    require(model.flash_cr & FLASH_LOCK, "Flash was left unlocked after erase")
    require(not model.program_writes, "Erase unexpectedly programmed flash")
    cases += 1

    # Start with high output latches and alternate functions to verify requests
    # actually clear levels/select GPIO, rather than relying on zeroed fake RAM.
    for port in GPIO_PORTS:
        model.output_levels[port] = 0xFFFF
        model.uc.mem_write(port, struct.pack("<I", 0xAAAAAAAA))
        model.uc.mem_write(port + 0x0C, struct.pack("<I", 0xFFFFFFFF))
    model.clear_events()
    model.call("platform_safe_outputs")
    port_a, port_b, port_c = GPIO_PORTS
    inactive_pins = {port_a: (0, 1, 7), port_b: (3, 4, 5, 6, 7, 8, 9), port_c: (0, 2)}
    for port, pins in inactive_pins.items():
        for pin in pins:
            require(not model.output_levels[port] & (1 << pin), f"GPIO 0x{port:08X}:{pin} was not set low")
            require((model.word(port) >> (pin * 2)) & 3 == 1, "Inactive GPIO was not configured as an output")
    require(model.output_levels[port_c] & (1 << 3), "Shared active-low motor enable was not set high")
    require((model.word(port_c) >> 6) & 3 == 1, "Motor disable pin was not configured as output")
    for pin in (13, 14):
        require((model.word(port_c) >> (pin * 2)) & 3 == 0, "Probe pin was driven as an output")
        require((model.word(port_c + 0x0C) >> (pin * 2)) & 3 == 0, "Probe input acquired a pull resistor")
    model.require_no_flash_activity("inactive GPIO request")
    return cases + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path, default=Path(__file__).resolve().parents[2] / "build/ender_aux/updater.elf")
    args = parser.parse_args()
    require(args.elf.is_file(), f"Build the production updater first: {args.elf}")
    cases = run_tests(PlatformModel(args.elf))
    print(f"Production platform tests passed: {cases} cases (fake MMIO/flash; no hardware timing or electrical validation).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
