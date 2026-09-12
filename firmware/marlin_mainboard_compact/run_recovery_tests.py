"""Execute the production halted parser and polled UART/GPIO code in Cortex-M4.

These tests use fake registers/transport. They do not operate a controller or
qualify physical stopping, watchdog timing, reset wiring, or motor holding.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PRIMASK,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_SP,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from firmware.ender_aux_f401compact.build import tool, toolchain_bin  # noqa: E402

CORE = r'''
#include "recovery_protocol.h"
#define CHECK(x) do { if (!(x)) return __LINE__; } while (0)
static char output[4096];
static unsigned used;
static bool tx_ok = true;
static bool send(const char *s) {
  if (!tx_ok) return false;
  while (*s && used < sizeof(output)-1) output[used++] = *s++;
  output[used] = 0;
  return !*s;
}
static void clear() { used=0; output[0]=0; }
static E3RecoveryParser::Action feed(E3RecoveryParser &p, const char *s) {
  E3RecoveryParser::Action action=E3RecoveryParser::NONE;
  while (*s) if (p.receive((unsigned char)*s++) == E3RecoveryParser::RESET) action=E3RecoveryParser::RESET;
  return action;
}
static bool same(const char *a, const char *b) {
  while (*a && *a==*b) {++a;++b;}
  return *a==*b;
}
static void reset_command(char *out) {
  const char *prefix="E3RECOVER ";
  unsigned n=0;
  while (*prefix) out[n++]=*prefix++;
  const unsigned token=sizeof("E3RECOVERY:1 STATE:HALTED BOARD:0401C013 TOKEN:")-1;
  for (unsigned i=0;i<8;++i) out[n++]=output[token+i];
  out[n++]='\n'; out[n]=0;
}
extern "C" int test_main() {
  E3RecoveryParser p(0,send);
  CHECK(feed(p,"E3RECOVER 9E3779B9\n")==E3RecoveryParser::NONE);
  CHECK(same(output,"Error:E3RECOVERY:1 REJECTED\nok\n")); clear();
  CHECK(feed(p,"M115\r\n")==E3RecoveryParser::NONE);
  CHECK(same(output,"E3RECOVERY:1 STATE:HALTED BOARD:0401C013 TOKEN:9E3779B9\nok\n"));
  char old[20], current[20]; reset_command(old); clear();
  feed(p,"M115\n"); reset_command(current); clear();
  CHECK(feed(p,old)==E3RecoveryParser::NONE); clear();
  // Rejection consumes even the current challenge.
  CHECK(feed(p,current)==E3RecoveryParser::NONE); clear();
  feed(p,"M115\n"); reset_command(current); clear();
  CHECK(feed(p,current)==E3RecoveryParser::RESET);
  CHECK(same(output,"E3RECOVERY:1 RESETTING\n")); clear();
  CHECK(feed(p,current)==E3RecoveryParser::NONE); clear();
  static const char *const bad[]={"M999\n","M997\n","BOOT\n","HOLD\n","G28\n","M106 S255\n",
    "BEGIN 0401C013 00000200 12345678\n","E3RECOVER\n","M115 X\n","M115\rX\n"," m115\n"};
  for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);++i) {
    feed(p,"M115\n"); reset_command(current); clear();
    CHECK(feed(p,bad[i])==E3RecoveryParser::NONE); clear();
    CHECK(feed(p,current)==E3RecoveryParser::NONE); clear();
  }
  for(int c=-2;c<=256;++c) {
    if(c==-1 || c=='\r' || c=='\n' || (c>=32&&c<=126)) continue;
    feed(p,"M115\n"); reset_command(current); clear();
    p.receive(c); feed(p,current); CHECK(!same(output,"E3RECOVERY:1 RESETTING\n")); clear();
    feed(p,"M115\n"); CHECK(used>50); clear();
  }
  feed(p,"M115\n"); reset_command(current); clear();
  for(unsigned i=0;i<1000;++i) p.receive('A');
  CHECK(feed(p,current)==E3RecoveryParser::NONE); clear();
  feed(p,"M115\n"); reset_command(current); clear();
  for(unsigned i=0;i<1000;++i) CHECK(p.receive(-1)==E3RecoveryParser::NONE);
  CHECK(feed(p,current)==E3RecoveryParser::RESET); clear();
  // Neither a failed query nor a failed RESETTING transmission authorizes reset.
  tx_ok=false; feed(p,"M115\n"); tx_ok=true;
  CHECK(feed(p,current)==E3RecoveryParser::NONE); clear();
  feed(p,"M115\n"); reset_command(current); clear(); tx_ok=false;
  CHECK(feed(p,current)==E3RecoveryParser::NONE); tx_ok=true;
  CHECK(feed(p,current)==E3RecoveryParser::NONE); clear();
  return 0;
}
'''

MMIO = r'''
#include <stdint.h>
struct GPIO_TypeDef { volatile uint32_t MODER,OTYPER,OSPEEDR,PUPDR,IDR,ODR,BSRR,LCKR,AFR[2]; };
struct USART_TypeDef { volatile uint32_t SR,DR,BRR,CR1,CR2,CR3; };
struct RCC_TypeDef { volatile uint32_t CR,PLLCFGR,CFGR,CIR,AHB1RSTR,AHB2RSTR,r0[2],APB1RSTR,APB2RSTR,r1[2],AHB1ENR,AHB2ENR,r2[2],APB1ENR,APB2ENR; };
struct IWDG_TypeDef { volatile uint32_t KR; };
struct DWT_TypeDef { volatile uint32_t CTRL,CYCCNT; };
struct CoreDebug_TypeDef { volatile uint32_t DEMCR; };
#define GPIOA ((GPIO_TypeDef *)0x40020000)
#define GPIOB ((GPIO_TypeDef *)0x40020400)
#define GPIOC ((GPIO_TypeDef *)0x40020800)
#define USART1 ((USART_TypeDef *)0x40011000)
#define RCC ((RCC_TypeDef *)0x40023800)
#define IWDG ((IWDG_TypeDef *)0x40003000)
#define DWT ((DWT_TypeDef *)0xE0001000)
#define CoreDebug ((CoreDebug_TypeDef *)0xE000EDFC)
#define RCC_AHB1ENR_GPIOAEN 1
#define RCC_AHB1ENR_GPIOBEN 2
#define RCC_AHB1ENR_GPIOCEN 4
#define RCC_APB2ENR_USART1EN 16
#define USART_SR_PE 1
#define USART_SR_FE 2
#define USART_SR_NE 4
#define USART_SR_ORE 8
#define USART_SR_RXNE 32
#define USART_SR_TC 64
#define USART_SR_TXE 128
#define USART_CR1_UE 8192
#define USART_CR1_TE 8
#define USART_CR1_RE 4
#define CoreDebug_DEMCR_TRCENA_Msk 0x1000000
#define DWT_CTRL_CYCCNTENA_Msk 1
static uint32_t SystemCoreClock=84000000;
static void __disable_irq() { __asm volatile("cpsid i" ::: "memory"); }
static void __DSB() { __asm volatile("dsb" ::: "memory"); }
__attribute__((noreturn)) static void NVIC_SystemReset() {
  *(volatile uint32_t *)0xE000ED0C=0x05FA0004;
  for(;;) __asm volatile("nop");
}
'''


def compile_test(out: Path, name: str, contents: str) -> Path:
    source = out / f"{name}.cpp"
    source.write_text(contents, encoding="utf-8")
    linker = out / "test.ld"
    linker.write_text("ENTRY(test_main)\nSECTIONS {. = 0x08000000; .text : {*(.text*) *(.rodata*)} "
                      ". = 0x20000000; .data : {*(.data*)} .bss : {*(.bss*) *(COMMON)} }\n")
    elf = out / f"{name}.elf"
    subprocess.run([tool(toolchain_bin(), "g++"), "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft",
                    "-Os", "-std=c++14", "-ffreestanding", "-fno-builtin", "-fno-exceptions", "-fno-rtti",
                    "-nostdlib", "-I", str(HERE), "-T", str(linker), str(source), "-lgcc", "-o", str(elf)], check=True)
    return elf


def load(path: Path):
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    for base, size in ((0x08000000, 0x40000), (0x20000000, 0x10000), (0x40000000, 0x30000), (0xE0000000, 0x10000)):
        uc.mem_map(base, size)
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        entry = elf.header["e_entry"]
        for segment in elf.iter_segments():
            if segment["p_type"] == "PT_LOAD" and segment["p_filesz"]:
                uc.mem_write(segment["p_vaddr"], segment.data())
    uc.reg_write(UC_ARM_REG_SP, 0x20010000)
    return uc, entry


def check_parser(path: Path):
    uc, entry = load(path)
    results = []
    sentinel = 0x0803F000

    def returned(emulator, *_args):
        results.append(emulator.reg_read(UC_ARM_REG_R0))
        emulator.emu_stop()

    uc.hook_add(UC_HOOK_CODE, returned, begin=sentinel, end=sentinel)
    uc.reg_write(UC_ARM_REG_LR, sentinel | 1)
    uc.emu_start(entry | 1, sentinel + 2, timeout=5_000_000, count=5_000_000)
    if results != [0]:
        raise RuntimeError(f"Recovery parser failed at generated harness line {results}")


def check_mmio(path: Path, scenario: str, native_entry: str | None = None, masked: bool = False):
    uc, entry = load(path)
    native_names = {}
    if native_entry is not None:
        with path.open("rb") as handle:
            native_names = {symbol.name: symbol["st_value"] for symbol in
                            ELFFile(handle).get_section_by_name(".symtab").iter_symbols()}
        returned = []

        def fastio_done(emulator, *_args):
            returned.append(True)
            emulator.emu_stop()

        hook = uc.hook_add(UC_HOOK_CODE, fastio_done, begin=0x0803F000, end=0x0803F000)
        uc.reg_write(UC_ARM_REG_LR, 0x0803F001)
        uc.emu_start(native_names["_Z11FastIO_initv"] | 1, 0x0803F002, count=10000)
        uc.hook_del(hook)
        if not returned:
            raise RuntimeError("Native FastIO did not initialize")
        uc.mem_write(native_names["SystemCoreClock"], (84000000).to_bytes(4, "little"))
        uc.mem_write(native_names["_ZN11Temperature9fan_speedE"], b"\xff\xff")
        entry = native_names[native_entry]
        uc.reg_write(UC_ARM_REG_SP, 0x20010000)
        for register in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2):
            uc.reg_write(register, 0)
        uc.reg_write(UC_ARM_REG_PRIMASK, int(masked))

        def forbid_native_uart(_emulator, *_args):
            raise RuntimeError("Native kill attempted interrupt-driven UART output")

        address = native_names["_ZN14HardwareSerial5writeEh"] & ~1
        uc.hook_add(UC_HOOK_CODE, forbid_native_uart, begin=address, end=address)
    incoming = bytearray()
    output = bytearray()
    resets = []
    writes = []
    idle = 0
    initialized = False
    sent_reset = False
    stalled = False

    def read(emulator, _access, address, _size, _value, _data):
        nonlocal idle
        if address == 0x40011000:
            status = 0 if stalled else 192
            if incoming:
                status |= 32
            elif initialized:
                idle += 1
                if idle == (101000 if stalled else 500):
                    emulator.emu_stop()
            emulator.mem_write(address, status.to_bytes(4, "little"))
        elif address == 0x40011004:
            value = incoming.pop(0) if incoming else 0
            emulator.mem_write(address, value.to_bytes(4, "little"))

    def write(emulator, _access, address, size, value, _data):
        nonlocal initialized, sent_reset, stalled
        writes.append((address, size, value))
        if address == 0x40011004:
            output.append(value & 255)
            if output.endswith(b"\nError:E3RECOVERY:1 HALTED\n"):
                if native_names and uc.mem_read(native_names["_ZN11Temperature9fan_speedE"], 2) != b"\x00\x00":
                    raise RuntimeError("Halted UART started before native fan PWM shutdown")
                initialized = True
                incoming.extend(b"M115\n")
            if output.endswith(b"\nok\n") and not sent_reset:
                match = re.search(rb" TOKEN:([0-9A-F]{8})\nok\n", output)
                if match:
                    sent_reset = True
                    if scenario in {"reset", "stalled_ack"}:
                        incoming.extend(b"E3RECOVER " + match[1] + b"\n")
                        stalled = scenario == "stalled_ack"
                    elif scenario == "reject":
                        incoming.extend(b"M999\nBOOT\nM997\nG28\nM106 S255\nE3RECOVER 00000000\n")
        if address == 0xE000ED0C:
            resets.append(value)
            emulator.emu_stop()
        if address == 0x40003000 and value != 0xAAAA:
            raise RuntimeError("Recovery attempted to start or reconfigure watchdog")
        if 0x40023C00 <= address <= 0x40023C18:
            raise RuntimeError("Recovery touched flash programming registers")

    uc.hook_add(UC_HOOK_MEM_READ, read, begin=0x40011000, end=0x40011007)
    uc.hook_add(UC_HOOK_MEM_WRITE, write)
    uc.emu_start(entry | 1, 0x0803F002, timeout=5_000_000, count=5_000_000)
    if not initialized or b"E3RECOVERY:1 STATE:HALTED BOARD:0401C013 TOKEN:" not in output:
        raise RuntimeError(f"No polled halted response: {scenario}")
    if resets != ([0x05FA0004] if scenario == "reset" else []):
        raise RuntimeError(f"Unexpected system reset: {scenario}/{resets}")
    if scenario == "reset" and not output.endswith(b"E3RECOVERY:1 RESETTING\n"):
        raise RuntimeError("Reset preceded its transmitted acknowledgment")
    if uc.reg_read(UC_ARM_REG_PRIMASK) != 1:
        raise RuntimeError("Recovery reenabled interrupts")
    expected = ((0x40020000, 0, False), (0x40020800, 0, False), (0x40020000, 1, False),
                (0x40020000, 7, False), (0x40020800, 3, True), (0x40020800, 2, False),
                (0x40020400, 8, False), (0x40020400, 6, False), (0x40020400, 4, False))
    for port, pin, high in expected:
        value = 1 << (pin if high else pin + 16)
        if (port + 24, 4, value) not in writes:
            raise RuntimeError("An output was not driven to its inactive state")
        if any(a == port + 24 and v & (1 << pin) for a, _, v in writes) and not high:
            raise RuntimeError("Recovery raised an actuator output")
    cr1 = int.from_bytes(uc.mem_read(0x4001100C, 4), "little")
    if cr1 != 8204 or int.from_bytes(uc.mem_read(0x40011008, 4), "little") != 729:
        raise RuntimeError("USART is not 115200-baud polling with interrupts disabled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path, help="Also audit the final linked native kill/minkill paths")
    args = parser.parse_args()
    out = ROOT / "build/marlin-recovery-tests"
    out.mkdir(parents=True, exist_ok=True)
    check_parser(compile_test(out, "core", CORE))
    production = (HERE / "e3_recovery.inc").read_text().replace(
        '#include "HAL/STM32/e3_recovery_protocol.h"', '#include "recovery_protocol.h"',
    )
    hardware = compile_test(out, "mmio", MMIO + production + '\nextern "C" void test_main() { e3_recovery_loop(); }\n')
    for scenario in ("hold", "reject", "reset", "stalled_ack"):
        check_mmio(hardware, scenario)
    print("Compiled recovery parser and 4 polled UART/GPIO scenarios passed; no physical hardware.")
    if args.elf:
        for entry in ("_Z4killPKcS0_b", "_Z7minkillb"):
            for masked in (False, True):
                for scenario in ("hold", "reject", "reset", "stalled_ack"):
                    check_mmio(args.elf, scenario, entry, masked)
        print("Final native kill/minkill passed 16 scenarios with IRQs initially on/off; outputs/PWM off, no native UART, tokenized reset only. Fake MMIO, no hardware.")


if __name__ == "__main__":
    main()
