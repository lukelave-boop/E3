"""Execute the production mismatch parser with fake UART/hardware in Cortex-M4.

These tests cover late/repeated diagnostic queries and rejected commands. They
do not model the serial adapter, MCU registers, clocks, or physical hardware.
"""

from __future__ import annotations

import argparse
import subprocess

from build import FLAGS, HERE, ROOT, tool, toolchain_bin

HARNESS = r"""
#include <stdbool.h>
#include <stdint.h>
#include "diagnostic.h"
#include "platform.h"

#define CHECK(value) do { if (!(value)) return __LINE__; } while (0)
#define REPORT "ERR MCU_MISMATCH\n" \
    "E3HW:1 MCU:0419 FLASH_KIB:1024 LAYOUT:F401_COMPACT_V1\n"

static char output[256];
static unsigned output_length;
static unsigned output_overflow;
static unsigned unsafe_calls;
static uint32_t now;
volatile unsigned diagnostic_cases;

void platform_putchar(char byte) {
    if (output_length + 1U < sizeof(output)) {
        output[output_length++] = byte;
        output[output_length] = 0;
    } else ++output_overflow;
}
void platform_report_hardware(void) {
    const char *text = "E3HW:1 MCU:0419 FLASH_KIB:1024 LAYOUT:F401_COMPACT_V1\n";
    while (*text) platform_putchar(*text++);
}
void platform_init(void) { ++unsafe_calls; }
void platform_safe_outputs(void) {}
bool platform_board_supported(void) { return false; }
uint32_t platform_millis(void) { return now; }
int platform_getchar(void) { return -1; }
void platform_service(void) {}
bool platform_erase_application(void) { ++unsafe_calls; return false; }
bool platform_program_word(uint32_t address, uint32_t value) {
    (void)address; (void)value; ++unsafe_calls; return false;
}
uint32_t platform_read_word(uint32_t address) {
    (void)address; ++unsafe_calls; return 0;
}
void platform_boot_application(void) { ++unsafe_calls; }
void platform_reset(void) { ++unsafe_calls; }

static void send(const char *text) {
    while (*text) diagnostic_receive((unsigned char)*text++);
}
static int take(const char *expected) {
    unsigned index = 0;
    while (expected[index] && expected[index] == output[index]) ++index;
    const int matched = !expected[index] && !output[index];
    output_length = 0;
    output[0] = 0;
    ++diagnostic_cases;
    return matched && !output_overflow && !unsafe_calls;
}

int tests_main(void) {
    diagnostic_init();
    CHECK(take(REPORT));

    /* A connection arriving long after boot can query again indefinitely. */
    static const uint32_t times[] = {0, 60000, UINT32_MAX, 10};
    static const char *const queries[] = {"M115\n", "INFO\r\n", "DIAG\n"};
    for (unsigned t = 0; t < sizeof(times) / sizeof(times[0]); ++t) {
        now = times[t];
        for (unsigned repeat = 0; repeat < 5; ++repeat) {
            for (unsigned idle = 0; idle < 100; ++idle) diagnostic_receive(-1);
            CHECK(take(""));
            for (unsigned q = 0; q < sizeof(queries) / sizeof(queries[0]); ++q) {
                send(queries[q]);
                CHECK(take(REPORT));
            }
        }
    }

    /* Rejected silicon cannot enter maintenance, inspect flash, or boot. */
    static const char *const rejected[] = {
        "HOLD\n", "BOOT\n", "UPDATE\n", "RESET\n", "M997\n",
        "BEGIN 0401C013 00000200 12345678\n", "DATA 00000000 FFFFFFFF\n",
        "END\n", "M115 S1\n", "INFO X\n", "DIAG X\n", "M106 P0 S255\n",
        "G28 Z\n", "G39\n", "M112\n", "info\n", " M115\n", "INFO \n",
        "\n", "\r\n", "12345678\n", "123456789\n"
    };
    for (unsigned n = 0; n < sizeof(rejected) / sizeof(rejected[0]); ++n) {
        send(rejected[n]);
        CHECK(take(""));
        send("INFO\n");
        CHECK(take(REPORT));
    }

    /* Incomplete input has no action; ordinary no-byte polls preserve it. */
    send("M1");
    now = 4000000;
    for (unsigned idle = 0; idle < 1000; ++idle) diagnostic_receive(-1);
    CHECK(take(""));
    send("15\r\n");
    CHECK(take(REPORT));

    /* An oversized line is discarded as a whole, including a valid suffix. */
    for (unsigned n = 0; n < 1024; ++n) diagnostic_receive('A');
    send("M115\n");
    CHECK(take(""));
    send("DIAG\n");
    CHECK(take(REPORT));

    /* Binary/UART-error input never authorizes a query or action. Newline
     * resynchronizes the parser so later independent queries still work. */
    for (int byte = -3; byte <= 256; ++byte) {
        if (byte == -1 || byte == '\r' || byte == '\n' || (byte >= 32 && byte < 127)) continue;
        send("M");
        diagnostic_receive(byte);
        send("115\n");
        CHECK(take(""));
        send("M115\n");
        CHECK(take(REPORT));
    }

    /* Reinitialization discards both partial and overlong pending input. */
    send("IN");
    diagnostic_init();
    CHECK(take(REPORT));
    send("FO\n");
    CHECK(take(""));
    send("123456789");
    diagnostic_init();
    CHECK(take(REPORT));
    send("INFO\n");
    CHECK(take(REPORT));
    CHECK(unsafe_calls == 0);
    return 0;
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain")
    args = parser.parse_args()
    from elftools.elf.elffile import ELFFile
    from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_SP

    out = ROOT / "build" / "ender_aux_f401compact" / "diagnostic_tests"
    out.mkdir(parents=True, exist_ok=True)
    harness = out / "diagnostic_harness.c"
    harness.write_text(HARNESS, encoding="utf-8")
    linker = out / "diagnostic_test.ld"
    linker.write_text("""ENTRY(tests_main)
MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 256K
RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 64K }
SECTIONS {
 .text : { *(.text*) *(.rodata*) } > FLASH
 .data : { *(.data*) } > RAM
 .bss (NOLOAD) : { *(.bss*) *(COMMON) } > RAM
 /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }
}
""", encoding="utf-8")
    elf_path = out / "diagnostic_test.elf"
    subprocess.run([
        tool(toolchain_bin(args.toolchain), "gcc"), *FLAGS, "-DE3_CORE_TEST", "-I", str(HERE),
        "-T", str(linker), str(harness), str(HERE / "diagnostic.c"),
        "-lgcc", "-o", str(elf_path),
    ], check=True)
    emulator = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    emulator.mem_map(0x08000000, 0x40000)
    emulator.mem_map(0x20000000, 0x10000)
    with elf_path.open("rb") as handle:
        elf = ELFFile(handle)
        entry = elf.header["e_entry"]
        cases = elf.get_section_by_name(".symtab").get_symbol_by_name("diagnostic_cases")[0]["st_value"]
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                emulator.mem_write(segment["p_vaddr"], segment.data())
    sentinel = 0x0803F000
    emulator.mem_write(sentinel, b"\x00\xbe")
    finished = []

    def returned(uc, _address, _size, _data):
        finished.append(uc.reg_read(UC_ARM_REG_R0))
        uc.emu_stop()

    emulator.hook_add(UC_HOOK_CODE, returned, begin=sentinel, end=sentinel)
    emulator.reg_write(UC_ARM_REG_SP, 0x2000F000)
    emulator.reg_write(UC_ARM_REG_LR, sentinel | 1)
    emulator.emu_start(entry | 1, sentinel + 2, timeout=10_000_000, count=5_000_000)
    if not finished:
        raise RuntimeError("Diagnostic tests exceeded execution bound")
    if finished[0]:
        raise RuntimeError(f"Diagnostic test failure at generated harness line {finished[0]}: {harness}")
    count = int.from_bytes(emulator.mem_read(cases, 4), "little")
    print(f"Compiled Cortex-M4 diagnostic tests passed ({count} checks; fake UART/hardware; no physical hardware).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
