"""Execute the actual F401 M997 guards and naked jump in Cortex-M emulation.

Peripheral/output guards use fake I/O. This does not qualify connected hardware.
"""
from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_BASEPRI,
    UC_ARM_REG_CONTROL,
    UC_ARM_REG_FAULTMASK,
    UC_ARM_REG_LR,
    UC_ARM_REG_MSP,
    UC_ARM_REG_PRIMASK,
    UC_ARM_REG_PSP,
    UC_ARM_REG_R0,
    UC_ARM_REG_SP,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from firmware.ender_aux_f401compact.build import tool, toolchain_bin  # noqa: E402

PREAMBLE = r'''
#include <stdint.h>
#define ENABLED(x) x
#define LOW 0
#define FAN_PIN 0
#define FAN1_PIN 1
static int effects, error_kind, entered, running;
static unsigned irq_off, barrier, expected_stack, expected_entry;
static struct { uint32_t VTOR; } scb;
static struct { uint32_t CTRL; } systick;
#define SCB (&scb)
#define SysTick (&systick)
struct Debug { uint32_t IDCODE; };
#define DBGMCU ((Debug *)0xE0042000)
static void __disable_irq() { irq_off++; }
static void __DSB() { barrier++; }
static void __ISB() { barrier++; }
static int strcmp(const char *a, const char *b) {
  while (*a && *a == *b) { ++a; ++b; }
  return *a - *b;
}
static struct { const char *command_ptr; } parser;
static struct {
  struct { unsigned length; } ring_buffer;
  const char *injected_commands_P;
  char injected_commands[2];
} queue;
static struct { bool busy; bool has_blocks_queued() { return busy; } } planner;
static struct { bool z_probe_enabled; } endstops;
#if SDSUPPORT
static struct {
  bool active, open;
  struct { bool saving; } flag;
  bool isPrinting() { return active; }
  bool isFileOpen() { return open; }
} card;
#endif
static struct {
  unsigned char fan_speed[2];
  #if HAS_HOTEND
  int hot;
  int degTargetHotend(int) { return hot; }
  #endif
  #if HAS_HEATED_BED
  int bed;
  int degTargetBed() { return bed; }
  #endif
  void disable_all_heaters() { effects |= 1; }
  void zero_fan_speeds() { effects |= 2; }
} thermalManager;
static bool IsRunning() { return running != 0; }
static void disable_all_steppers() { effects |= 16; }
static void write(int pin, int value) {
  if (value) effects |= 128;
  effects |= pin == 0 ? 4 : 8;
}
#define WRITE(pin, value) write(pin, value)
#define SERIAL_ERROR_MSG(message) \
  (error_kind = strcmp(message, "E3USB:1 PRECONDITION") ? 2 : 1)
#define SERIAL_ECHOLNPGM(message) \
  (effects |= strcmp(message, "E3USB:1 ENTERING_UPDATER") ? 128 : 32)
#define SERIAL_FLUSHTX() (effects |= 64)
static void e3_enter_updater(uint32_t stack, uint32_t entry) {
  if (stack == expected_stack && entry == expected_entry) ++entered;
}
class GcodeSuite { public: static void M997(); };
'''

TESTS = r'''
#define CHECK(value) do { if (!(value)) return __LINE__; } while (0)
enum Scenario {
  rc, re, revision_bits, params, wrong_spelling, stopped, pending_motion,
  active_probe, hotend, bed, fan0, fan1, printing, file_open, saving,
  wrong_family, wrong_capacity_rc, wrong_capacity_re, stack_base, stack_high,
  stack_alignment, arm_entry, vector_entry, outside_updater, no_queued_command,
  queued_follower, injected_flash, injected_ram, last_valid_entry,
  first_valid_stack, unsupported_128, unsupported_384, scenario_count
};
extern "C" int tests_main() {
  for (int scenario = 0; scenario < scenario_count; ++scenario) {
    effects = error_kind = entered = 0; irq_off = barrier = 0;
    scb.VTOR = 0x08020200; systick.CTRL = 7;
    parser.command_ptr = scenario == params ? "M997 S1" :
                         scenario == wrong_spelling ? "M997 " : "M997";
    running = scenario != stopped;
    queue.ring_buffer.length = scenario == no_queued_command ? 0 :
                               scenario == queued_follower ? 2 : 1;
    queue.injected_commands_P = scenario == injected_flash ? "M5" : nullptr;
    queue.injected_commands[0] = scenario == injected_ram ? 'G' : 0;
    planner.busy = scenario == pending_motion;
    endstops.z_probe_enabled = scenario == active_probe;
    #if HAS_HOTEND
      thermalManager.hot = scenario == hotend;
    #endif
    #if HAS_HEATED_BED
      thermalManager.bed = scenario == bed;
    #endif
    thermalManager.fan_speed[0] = scenario == fan0;
    thermalManager.fan_speed[1] = scenario == fan1;
    #if SDSUPPORT
      card.active = scenario == printing;
      card.open = scenario == file_open;
      card.flag.saving = scenario == saving;
    #endif
    DBGMCU->IDCODE = scenario == wrong_family ? 0x414 :
                    scenario == re || scenario == wrong_capacity_re || scenario == unsupported_384 ? 0x433 :
                    scenario == revision_bits ? 0x10000423 : 0x423;
    *(volatile uint16_t *)0x1FFF7A22 = scenario == re || scenario == wrong_capacity_rc ? 512 :
                                     scenario == unsupported_128 ? 128 :
                                     scenario == unsupported_384 ? 384 : 256;
    expected_stack = scenario == stack_base ? 0x20000000 :
                     scenario == stack_high ? 0x20010008 :
                     scenario == stack_alignment ? 0x2000FFFC :
                     scenario == first_valid_stack ? 0x20000008 : 0x20010000;
    expected_entry = scenario == arm_entry ? 0x08010194 :
                     scenario == vector_entry ? 0x08010193 :
                     scenario == outside_updater ? 0x08020001 :
                     scenario == last_valid_entry ? 0x0801FFFF : 0x08010195;
    *(volatile uint32_t *)0x08010000 = expected_stack;
    *(volatile uint32_t *)0x08010004 = expected_entry;
    int expected_error = 0;
    switch (scenario) {
      case rc: case re: case revision_bits: case last_valid_entry: case first_valid_stack:
        break;
      case wrong_family: case wrong_capacity_rc: case wrong_capacity_re:
      case stack_base: case stack_high: case stack_alignment: case arm_entry:
      case vector_entry: case outside_updater: case unsupported_128: case unsupported_384:
        expected_error = 2; break;
      #if !HAS_HOTEND
        case hotend: break;
      #endif
      #if !HAS_HEATED_BED
        case bed: break;
      #endif
      #if !SDSUPPORT
        case printing: case file_open: case saving: break;
      #endif
      default: expected_error = 1;
    }
    GcodeSuite::M997();
    CHECK(error_kind == expected_error);
    if (!expected_error) {
      CHECK(entered == 1 && effects == 127);
      CHECK(scb.VTOR == 0x08010000 && !systick.CTRL && irq_off == 1 && barrier == 2);
    } else {
      CHECK(!entered && !effects);
      CHECK(scb.VTOR == 0x08020200 && systick.CTRL == 7 && !irq_off && !barrier);
    }
  }
  return 0;
}
'''


def compile_harness(out: Path, name: str, source: str, flags: list[str]) -> Path:
    harness = out / f"{name}.cpp"
    harness.write_text(source)
    linker = out / f"{name}.ld"
    linker.write_text(
        "ENTRY(tests_main)\nSECTIONS { . = 0x08080000; "
        ".text : { *(.text*) *(.rodata*) } . = 0x20010000; "
        ".data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } }\n"
    )
    elf_path = out / f"{name}.elf"
    subprocess.run(
        [
            tool(toolchain_bin(), "g++"),
            "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-Os", "-std=c++14",
            "-ffreestanding", "-fno-builtin", "-nostdlib", "-fno-exceptions",
            "-fno-rtti", *flags, "-T", str(linker), str(harness), "-lgcc", "-o", str(elf_path),
        ],
        check=True,
    )
    return elf_path


def load_emulator(elf_path: Path) -> tuple[Uc, int]:
    uc = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
    for start, size in (
        (0x08000000, 0x100000), (0x20000000, 0x20000),
        (0xE0042000, 0x1000), (0x1FFF7000, 0x1000),
    ):
        uc.mem_map(start, size)
    with elf_path.open("rb") as handle:
        elf = ELFFile(handle)
        entry = elf.header["e_entry"]
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                uc.mem_write(segment["p_vaddr"], segment.data())
    return uc, entry


def check_guard_body(out: Path, body: str, enabled: bool) -> None:
    elf_path = compile_harness(
        out, f"transition_guards_{int(enabled)}", PREAMBLE + body + TESTS,
        [f"-D{name}={int(enabled)}" for name in ("HAS_HOTEND", "HAS_HEATED_BED", "SDSUPPORT")],
    )
    uc, entry = load_emulator(elf_path)
    result = []

    def done(emulator, *_):
        result.append(emulator.reg_read(UC_ARM_REG_R0))
        emulator.emu_stop()

    uc.mem_write(0x080F0000, struct.pack("<H", 0xBE00))
    uc.hook_add(UC_HOOK_CODE, done, begin=0x080F0000, end=0x080F0000)
    uc.reg_write(UC_ARM_REG_SP, 0x20020000)
    uc.reg_write(UC_ARM_REG_LR, 0x080F0001)
    uc.emu_start(entry | 1, 0x080F0002, timeout=1_000_000, count=200000)
    if result != [0]:
        raise RuntimeError(f"M997 guard tests, optional peripherals {enabled}: {result}")


def check_naked_jump(out: Path, naked: str) -> None:
    source = "#include <stdint.h>\n" + naked + r'''
extern "C" void tests_main() { e3_enter_updater(0x20008000, 0x08010195); }
'''
    elf_path = compile_harness(out, "transition_naked_jump", source, [])
    for use_psp in (False, True):
        uc, entry = load_emulator(elf_path)
        reached = []

        def target(emulator, address, *_, destinations=reached):
            destinations.append(address)
            emulator.emu_stop()

        uc.mem_write(0x08010194, struct.pack("<H", 0xBE00))
        uc.hook_add(UC_HOOK_CODE, target, begin=0x08010194, end=0x08010194)
        uc.reg_write(UC_ARM_REG_MSP, 0x2001F000)
        uc.reg_write(UC_ARM_REG_PSP, 0x2001E000)
        uc.reg_write(UC_ARM_REG_CONTROL, 2 if use_psp else 0)
        uc.reg_write(UC_ARM_REG_PRIMASK, 0)
        uc.reg_write(UC_ARM_REG_BASEPRI, 0x20)
        uc.reg_write(UC_ARM_REG_FAULTMASK, 1)
        uc.emu_start(entry | 1, 0x080F0000, timeout=1_000_000, count=1000)
        expected = {
            UC_ARM_REG_MSP: 0x20008000,
            UC_ARM_REG_SP: 0x20008000,
            UC_ARM_REG_CONTROL: 0,
            UC_ARM_REG_PRIMASK: 1,
            UC_ARM_REG_BASEPRI: 0,
            UC_ARM_REG_FAULTMASK: 0,
        }
        actual = {register: uc.reg_read(register) for register in expected}
        if reached != [0x08010194] or actual != expected:
            raise RuntimeError(f"M997 naked jump, PSP={use_psp}: {reached}, {actual}")


def main() -> None:
    source = (HERE / "marlin_usb_update.inc").read_text()
    body_index = source.index("void GcodeSuite::M997() {")
    naked = source[source.index("__attribute__((naked"):body_index]
    body = source[body_index:]
    out = ROOT / "build/marlin_mainboard_compact"
    out.mkdir(parents=True, exist_ok=True)
    for enabled in (False, True):
        check_guard_body(out, body, enabled)
    check_naked_jump(out, naked)
    print("Actual F401 M997 guards passed 64 fake-I/O cases; naked M4 jump passed MSP/PSP cases")


if __name__ == "__main__":
    main()
