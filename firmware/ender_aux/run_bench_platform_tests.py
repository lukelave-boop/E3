"""Execute the production bench application with fake UART/time and GPIO MMIO.

This loads application.elf and executes its actual input wrapper, parser,
simulation state machine and shared inactive-output code. UART transmission,
millisecond time and watchdog servicing are intercepted. Startup, clocks,
electrical levels, input noise, wiring, controller timing and actual hardware
behavior are not emulated. No serial port or controller is accessed.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from run_platform_tests import FLASH_END, FLASH_START, GPIO_PORTS, RAM_END, RAM_START, require

APP_VECTORS = 0x08020200
GPIO_A, GPIO_B, GPIO_C = GPIO_PORTS
RCC_AHB1ENR = 0x40023830
IWDG_KR = 0x40003000
PC14_MASK = 3 << 28
LOW_PINS = {GPIO_A: (0, 1, 7), GPIO_B: (3, 4, 5, 6, 7, 8, 9), GPIO_C: (0, 2)}
GPIO_REGISTERS = (0x00, 0x04, 0x08, 0x0C, 0x18)


class BenchModel:
    """A bounded execution harness, not a model of an electrical controller."""

    def __init__(self, elf_path: Path):
        from elftools.elf.elffile import ELFFile
        from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc

        self.uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        for address, size in ((FLASH_START, FLASH_END - FLASH_START), (RAM_START, RAM_END - RAM_START),
                              (0x40000000, 0x30000), (0xE0000000, 0x50000), (0x1FFF7000, 0x1000)):
            self.uc.mem_map(address, size)
        self.uc.mem_write(FLASH_START, b"\xff" * (FLASH_END - FLASH_START))
        with elf_path.open("rb") as handle:
            elf = ELFFile(handle)
            require(elf.elfclass == 32 and elf.header["e_machine"] == "EM_ARM", "Expected production ARM ELF")
            symbols = elf.get_section_by_name(".symtab")
            require(symbols is not None, "ELF must retain its symbol table")
            self.symbols = {symbol.name: int(symbol["st_value"]) for symbol in symbols.iter_symbols()}
            require(self.symbols.get("__image_start") == APP_VECTORS, "Expected the production application ELF")
            for segment in elf.iter_segments():
                if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                    self.uc.mem_write(segment["p_vaddr"], segment.data())
                    if segment["p_paddr"] != segment["p_vaddr"]:
                        self.uc.mem_write(segment["p_paddr"], segment.data())

        required = ("bench_inputs_init", "bench_probe_raw", "application_init", "application_receive",
                    "application_tick", "platform_putchar", "platform_millis", "platform_service")
        for name in required:
            require(name in self.symbols, f"Required production symbol missing: {name}")
        self.sentinel = FLASH_START
        self.uc.mem_write(self.sentinel, b"\x00\xbe")
        self.flash_snapshot = bytes(self.uc.mem_read(FLASH_START, FLASH_END - FLASH_START))
        self.return_value: int | None = None
        self.now = 0
        self.output = bytearray()
        self.writes: list[tuple[int, int, int]] = []
        self.monitor_outputs = False
        self.levels = {GPIO_A: 0, GPIO_B: 0, GPIO_C: 1 << 3}
        self.uc.hook_add(UC_HOOK_CODE, self._returned, begin=self.sentinel, end=self.sentinel)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._mmio_write, begin=0x40000000, end=0x4002FFFF)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._flash_write, begin=FLASH_START, end=FLASH_END - 1)
        self.intercepts = {self.symbols[name] & ~1: name for name in
                           ("platform_putchar", "platform_millis", "platform_service")}
        for address in self.intercepts:
            self.uc.hook_add(UC_HOOK_CODE, self._intercept, begin=address, end=address)

    def word(self, address: int) -> int:
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def set_word(self, address: int, value: int) -> None:
        self.uc.mem_write(address, struct.pack("<I", value & 0xFFFFFFFF))

    def _intercept(self, uc, address, _size, _data):
        from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0

        name = self.intercepts[address]
        if name == "platform_putchar":
            self.output.append(uc.reg_read(UC_ARM_REG_R0) & 0xFF)
        elif name == "platform_millis":
            uc.reg_write(UC_ARM_REG_R0, self.now & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def _returned(self, uc, _address, _size, _data):
        from unicorn.arm_const import UC_ARM_REG_R0

        self.return_value = uc.reg_read(UC_ARM_REG_R0)
        uc.emu_stop()

    @staticmethod
    def _flash_write(_uc, _access, address, _size, _value, _data):
        raise RuntimeError(f"Bench application attempted a flash write at 0x{address:08X}")

    def _mmio_write(self, _uc, _access, address, size, value, _data):
        self.writes.append((address, size, value))
        allowed = {RCC_AHB1ENR, IWDG_KR} | {port + offset for port in GPIO_PORTS for offset in GPIO_REGISTERS}
        require(address in allowed, f"Unexpected peripheral/flash-control write at 0x{address:08X}")
        require(size == 4, "GPIO/register access width changed")
        if not self.monitor_outputs:
            return
        for port in GPIO_PORTS:
            actuator_pins = LOW_PINS[port] + ((3,) if port == GPIO_C else ())
            actuator_bits = sum(1 << pin for pin in actuator_pins)
            if address == port + 0x18:
                low_mask = sum(1 << pin for pin in LOW_PINS[port])
                permitted = (low_mask << 16) | ((1 << 3) if port == GPIO_C else 0)
                require(not value & ~permitted, "Unexpected GPIO latch command")
                require(not value & low_mask, "A step, direction, heater or fan was requested high")
                if port == GPIO_C:
                    require(not value & (1 << (3 + 16)), "Shared motor enable was requested low")
                    require(not value & ((1 << 13) | (1 << (13 + 16))), "Probe control latch was driven")
                self.levels[port] |= value & 0xFFFF
                self.levels[port] &= ~((value >> 16) & 0xFFFF)
            elif address == port:
                for pin in LOW_PINS[port]:
                    require((value >> (pin * 2)) & 3 in (0, 1), "Actuator pin selected alternate function")
                if port == GPIO_C:
                    require((value >> 26) & 3 == 0, "PC13 probe-control pin selected output/alternate function")
                    require((value >> 28) & 3 == 0, "PC14 input selected output/alternate function")
                    require((value >> 6) & 3 in (0, 1), "Motor-enable pin selected alternate function")
            elif address == port + 0x04:
                require(not value & actuator_bits, "Actuator pin selected open drain")
            elif address == port + 0x0C:
                for pin in actuator_pins:
                    require((value >> (pin * 2)) & 3 == 0, "Actuator pin acquired a pull resistor")
                if port == GPIO_C:
                    require((value >> 26) & 3 == 0, "PC13 probe control acquired a pull resistor")

    def call(self, name: str, *args: int) -> int:
        from unicorn.arm_const import (
            UC_ARM_REG_CONTROL,
            UC_ARM_REG_LR,
            UC_ARM_REG_PRIMASK,
            UC_ARM_REG_R0,
            UC_ARM_REG_R1,
            UC_ARM_REG_SP,
        )

        require(len(args) <= 2, "At most two function arguments supported")
        self.return_value = None
        self.uc.reg_write(UC_ARM_REG_CONTROL, 0)
        self.uc.reg_write(UC_ARM_REG_PRIMASK, 0)
        self.uc.reg_write(UC_ARM_REG_SP, RAM_END - 0x100)
        self.uc.reg_write(UC_ARM_REG_LR, self.sentinel | 1)
        for register, argument in zip((UC_ARM_REG_R0, UC_ARM_REG_R1), args, strict=False):
            self.uc.reg_write(register, argument & 0xFFFFFFFF)
        self.uc.emu_start(self.symbols[name] | 1, self.sentinel + 2, timeout=2_000_000, count=500_000)
        require(self.return_value is not None, f"{name} did not return within execution bound")
        return self.return_value

    def command(self, text: str, expected: str | None = None) -> str:
        require(len(text) < 256, "Test command exceeds frame bound")
        self.output.clear()
        for byte in (text + "\n").encode("ascii"):
            self.call("application_receive", byte)
        result = self.output.decode("ascii").strip()
        if expected is not None:
            require(result == expected, f"{text!r}: expected {expected!r}, got {result!r}")
        self.require_inactive()
        return result

    def require_inactive(self) -> None:
        for port, pins in LOW_PINS.items():
            for pin in pins:
                require(not self.levels[port] & (1 << pin), f"GPIO 0x{port:08X}:{pin} latch was high")
                require((self.word(port) >> (pin * 2)) & 3 == 1, "Inactive actuator did not remain GPIO output")
                require(not self.word(port + 0x04) & (1 << pin), "Inactive output unexpectedly became open drain")
        require(self.levels[GPIO_C] & (1 << 3), "Shared active-low motor enable was not high")
        require((self.word(GPIO_C) >> 6) & 3 == 1, "Motor-disable pin was not GPIO output")
        require(not self.word(GPIO_C + 0x04) & (1 << 3), "Motor-disable pin became open drain")
        require((self.word(GPIO_C) >> 26) & 15 == 0, "Probe-control/input pins did not remain inputs")
        require((self.word(GPIO_C + 0x0C) >> 26) & 3 == 0, "PC13 acquired a pull resistor")
        require((self.word(GPIO_C + 0x0C) >> 28) & 3 == 1, "PC14 weak pull-up was not retained")

    def advance(self, milliseconds: int) -> None:
        require(0 <= milliseconds <= 10000, "Test time advance exceeds bound")
        self.now += milliseconds
        self.call("application_tick")
        self.require_inactive()

    def status(self, **expected: str) -> str:
        result = self.command("STATUS")
        require(result.startswith("BENCH "), f"Unexpected status: {result!r}")
        fields = dict(item.split("=", 1) for item in result.split()[1:])
        for name, value in expected.items():
            require(fields.get(name) == value, f"Status {name}: expected {value!r}, got {fields.get(name)!r}")
        require(fields.get("OUTPUTS") == "DISABLED", "Bench status omitted disabled hardware output claim")
        return result


def run_tests(model: BenchModel) -> int:
    cases = 0
    # Direct production wrapper tests start with nonzero unrelated fields.
    for mode, pulls, clocks in ((0xAAAAAAAA, 0xFFFFFFFF, 0), (0x55555555, 0x55555555, 0xFFF0),
                                (0xFFFFFFFF, 0xAAAAAAAA, 4), (0, 0, 0xFFFFFFFF)):
        model.set_word(GPIO_C, mode)
        model.set_word(GPIO_C + 0x0C, pulls)
        model.set_word(RCC_AHB1ENR, clocks)
        before_other_ports = [bytes(model.uc.mem_read(port, 0x28)) for port in (GPIO_A, GPIO_B)]
        model.writes.clear()
        model.call("bench_inputs_init")
        require(model.word(GPIO_C) == mode & ~PC14_MASK, "Input init changed another pin's mode")
        require(model.word(GPIO_C + 0x0C) == (pulls & ~PC14_MASK) | (1 << 28), "Input init changed another pin's pull")
        require(model.word(RCC_AHB1ENR) == clocks | 4, "Input init changed another peripheral clock bit")
        require({entry[0] for entry in model.writes} == {RCC_AHB1ENR, GPIO_C, GPIO_C + 0x0C},
                "Input init touched unexpected registers")
        require(before_other_ports == [bytes(model.uc.mem_read(port, 0x28)) for port in (GPIO_A, GPIO_B)],
                "Input wrapper modified another GPIO port")
        cases += 1
    for bits, expected in ((0, 0), (0xFFFF, 1), (0xFFFF & ~(1 << 14), 0), (1 << 14, 1)):
        model.set_word(GPIO_C + 0x10, bits)
        model.writes.clear()
        require(model.call("bench_probe_raw") == expected, "Raw PC14 reading did not match injected level")
        require(not model.writes, "Raw input read performed a peripheral write")
        cases += 1

    for port in GPIO_PORTS:
        model.uc.mem_write(port, bytes(0x28))
    model.set_word(GPIO_C + 0x10, 1 << 14)
    model.monitor_outputs = True
    model.call("application_init")
    model.require_inactive()
    model.command("INFO", "E3AUX1 APP 0.2.0 BOARD=0401E013 MODE=BENCH OUTPUTS=DISABLED")
    model.status(FAN1="0", FAN2="0", PROBE="STOWED", SOURCE="SIM", MOTION="IDLE")
    cases += 1

    # Accepted virtual output commands must visibly change virtual state while
    # every actual GPIO request remains inactive throughout command execution.
    model.command("SIM FAN 1 100", "OK SIM FAN 1 100")
    model.command("SIM FAN 2 100", "OK SIM FAN 2 100")
    model.status(FAN1="100", FAN2="100")
    model.command("SIM PROBE DEPLOY", "OK SIM PROBE DEPLOY")
    model.status(PROBE="DEPLOYED")
    model.command("SIM TRIGGER 1", "OK SIM TRIGGER 1")
    model.status(TRIGGER="1")
    model.command("SIM TRIGGER 0", "OK SIM TRIGGER 0")
    model.command("SIM PROBE STOW", "OK SIM PROBE STOW")
    cases += 1

    model.command("SIM Z SET 10000", "OK SIM Z SET 10000")
    model.command("SIM Z MOVE -1000 1000", "OK SIM Z MOVE -1000 1000")
    model.advance(100)
    model.status(MOTION="MOVE")
    model.command("STOP", "OK STOP")
    model.status(FAN1="0", FAN2="0", PROBE="STOWED", MOTION="IDLE")
    cases += 1

    model.command("SIM PROBE DEPLOY", "OK SIM PROBE DEPLOY")
    model.command("SIM Z PROBE 1000 1000", "OK SIM Z PROBE 1000 1000")
    model.advance(100)
    model.command("SIM TRIGGER 1", "OK SIM TRIGGER 1")
    model.advance(100)
    model.status(MOTION="IDLE")
    cases += 1

    for raw in (0, 1):
        model.set_word(GPIO_C + 0x10, raw << 14)
        model.command("INPUTS", f"INPUTS PROBE_PC14={raw}")
        cases += 1

    model.command("STOP", "OK STOP")
    model.command("SIM RESET", "OK SIM RESET")
    model.set_word(GPIO_C + 0x10, 1 << 14)
    model.command("SIM SOURCE SWITCH", "OK SIM SOURCE SWITCH")
    model.command("SIM PROBE DEPLOY", "OK SIM PROBE DEPLOY")
    model.command("SIM Z PROBE 1000 1000", "OK SIM Z PROBE 1000 1000")
    model.advance(100)
    model.status(SOURCE="SWITCH", TRIGGER="0", MOTION="PROBE")
    model.set_word(GPIO_C + 0x10, 0)
    model.advance(100)
    model.status(TRIGGER="1", MOTION="IDLE", RESULT="CONTACT", Z_UM="9800")
    cases += 1

    model.command("STOP", "OK STOP")
    for command in ("SIM FAN 1 101", "SIM FAN 3 100", "SIM Z MOVE -999999 1000", "M3 S1000", "G1 Z1"):
        response = model.command(command)
        require(response.startswith("ERR "), f"Invalid command was accepted: {command!r} -> {response!r}")
        model.status(FAN1="0", FAN2="0", PROBE="STOWED", MOTION="IDLE")
        cases += 1

    model.command("SIM RESET", "OK SIM RESET")
    model.command("SIM PROBE DEPLOY", "OK SIM PROBE DEPLOY")
    model.command("SIM Z PROBE 1000 1000", "OK SIM Z PROBE 1000 1000")
    model.advance(1000)
    model.status(MOTION="IDLE", RESULT="NO_TRIGGER", Z_UM="9000")
    model.command("M5", "OK M5 OUTPUTS_OFF")
    cases += 1

    model.command("SIM FAN 1 100", "OK SIM FAN 1 100")
    model.advance(10000)
    model.status(FAN1="100", MOTION="IDLE")
    model.command("M5", "OK M5 OUTPUTS_OFF")
    model.status(FAN1="0", FAN2="0", PROBE="STOWED", MOTION="IDLE")
    cases += 1
    model.output.clear()
    model.call("application_receive", -2)
    model.call("application_receive", ord("\n"))
    require(model.output.decode("ascii").startswith("ERR "), "UART error was not reported")
    model.require_inactive()
    cases += 1
    require(bytes(model.uc.mem_read(FLASH_START, FLASH_END - FLASH_START)) == model.flash_snapshot,
            "Production bench execution changed flash bytes")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path,
                        default=Path(__file__).resolve().parents[2] / "build/ender_aux/application.elf")
    args = parser.parse_args()
    require(args.elf.is_file(), f"Build production bench application first: {args.elf}")
    cases = run_tests(BenchModel(args.elf))
    print(f"Production bench platform tests passed: {cases} cases "
          "(fake UART/time/MMIO; no physical input/output or startup validation).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
