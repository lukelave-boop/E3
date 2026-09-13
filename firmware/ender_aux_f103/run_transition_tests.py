"""Execute the actual M997 guard body with fake motion, output and core state."""
from __future__ import annotations

import struct
import subprocess
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    source = (HERE / "marlin_usb_update.inc").read_text()
    body = source[source.index("void GcodeSuite::M997() {"):]
    preamble = r'''
#include <stdint.h>
#define ENABLED(x) x
#define SDSUPPORT 1
#define LOW 0
#define FAN_PIN 0
#define FAN1_PIN 1
static int effects, errors, entered, running;
static unsigned irq_off, barrier;
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
static struct { bool busy; bool has_blocks_queued() { return busy; } } planner;
static struct { bool z_probe_enabled; } endstops;
static struct { bool active; bool isPrinting() { return active; } } card;
static struct {
  int hot, bed; unsigned char fan_speed[2];
  int degTargetHotend(int) { return hot; }
  int degTargetBed() { return bed; }
  void disable_all_heaters() { effects |= 1; }
  void zero_fan_speeds() { effects |= 2; }
} thermalManager;
static bool IsRunning() { return running != 0; }
static void disable_all_steppers() { effects |= 16; }
static void write(int pin, int) { effects |= pin == 0 ? 4 : 8; }
#define WRITE(pin, value) write(pin, value)
#define SERIAL_ERROR_MSG(message) (++errors)
#define SERIAL_ECHOLNPGM(message) (effects |= 32)
#define SERIAL_FLUSHTX() (effects |= 64)
static void e3_enter_updater(uint32_t stack, uint32_t entry) {
  if (stack == 0x20010000 && entry == 0x08007131) ++entered;
}
class GcodeSuite { public: static void M997(); };
'''
    tests = r'''
#define CHECK(value) do { if (!(value)) return __LINE__; } while (0)
extern "C" int tests_main() {
  for (int scenario = 0; scenario < 15; ++scenario) {
    effects = errors = entered = 0; irq_off = barrier = 0;
    scb.VTOR = 0x08010200; systick.CTRL = 7;
    parser.command_ptr = scenario == 1 ? "M997 S1" : "M997";
    running = scenario != 2;
    planner.busy = scenario == 3;
    endstops.z_probe_enabled = scenario == 4;
    thermalManager.hot = scenario == 5;
    thermalManager.bed = scenario == 6;
    thermalManager.fan_speed[0] = scenario == 7;
    thermalManager.fan_speed[1] = scenario == 8;
    card.active = scenario == 9;
    DBGMCU->IDCODE = scenario == 10 ? 0x433 : 0x414;
    *(volatile uint16_t *)0x1FFFF7E0 = scenario == 11 ? 256 : 512;
    *(volatile uint32_t *)0x08007000 = scenario == 12 ? 0x20018000 : 0x20010000;
    *(volatile uint32_t *)0x08007004 = scenario == 13 ? 0x08007130 : scenario == 14 ? 0x08010001 : 0x08007131;
    GcodeSuite::M997();
    if (!scenario) {
      CHECK(entered == 1 && !errors && effects == 127);
      CHECK(scb.VTOR == 0x08007000 && !systick.CTRL && irq_off == 1 && barrier == 2);
    } else {
      CHECK(!entered && errors == 1 && !effects);
      CHECK(scb.VTOR == 0x08010200 && systick.CTRL == 7 && !irq_off && !barrier);
    }
  }
  return 0;
}
'''
    out = ROOT / "build/ender_aux_f103"
    harness = out / "transition_test.cpp"
    harness.write_text(preamble + body + tests)
    linker = out / "transition_test.ld"
    linker.write_text("ENTRY(tests_main)\nSECTIONS { . = 0x08080000; .text : { *(.text*) *(.rodata*) } . = 0x20010000; .data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } }")
    elf_path = out / "transition_test.elf"
    subprocess.run([str(ROOT / "build/firmware-tools/bin/arm-none-eabi-g++.exe"), "-mcpu=cortex-m3", "-mthumb", "-Os", "-std=c++14", "-ffreestanding", "-fno-builtin", "-nostdlib", "-fno-exceptions", "-fno-rtti", "-T", str(linker), str(harness), "-lgcc", "-o", str(elf_path)], check=True)
    uc = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
    for start, size in ((0x08000000, 0x100000), (0x20000000, 0x20000), (0xE0042000, 0x1000), (0x1FFFF000, 0x1000)):
        uc.mem_map(start, size)
    with elf_path.open("rb") as handle:
        elf = ELFFile(handle)
        entry = elf.header["e_entry"]
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                uc.mem_write(segment["p_vaddr"], segment.data())
    result = []

    def done(emulator, *_):
        result.append(emulator.reg_read(UC_ARM_REG_R0))
        emulator.emu_stop()

    uc.mem_write(0x080F0000, struct.pack("<H", 0xBE00))
    uc.hook_add(UC_HOOK_CODE, done, begin=0x080F0000, end=0x080F0000)
    uc.reg_write(UC_ARM_REG_SP, 0x20020000)
    uc.reg_write(UC_ARM_REG_LR, 0x080F0001)
    uc.emu_start(entry | 1, 0x080F0002, timeout=1_000_000, count=100000)
    if result != [0]:
        raise RuntimeError(f"M997 transition acceptance/rejection failed: {result}")
    print("Actual M997 guard body passed 15 Cortex-M3 fake-I/O acceptance/rejection cases")


if __name__ == "__main__":
    main()
