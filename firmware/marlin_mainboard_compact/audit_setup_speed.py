"""Execute the linked settings/M203 speed profile and native scope with fake I/O."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_S0

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from firmware.marlin_mainboard_compact.audit_planner import member_offsets  # noqa: E402
from firmware.marlin_mainboard_compact.audit_profile import SENTINEL, call, load  # noqa: E402
from firmware.marlin_mainboard_compact.run_recovery_tests import check_parser, compile_test  # noqa: E402


def audit(elf: Path) -> None:
    with elf.open("rb") as handle:
        native = ELFFile(handle)
        layouts = []
        for unit in native.get_dwarf_info().iter_CUs():
            for die in unit.iter_DIEs():
                name = die.attributes.get("DW_AT_name")
                if not name or name.value != b"planner_settings_t":
                    continue
                if die.tag == "DW_TAG_typedef":
                    die = die.get_DIE_from_attribute("DW_AT_type")
                if "DW_AT_byte_size" in die.attributes:
                    layouts.append((die.attributes["DW_AT_byte_size"].value,
                                    member_offsets(die)["max_feedrate_mm_s"] + 8))
                    break
            if layouts:
                break
    if len(layouts) != 1 or layouts[0] not in ((60, 36), (72, 44)):
        raise ValueError("Unexpected native planner settings layout")
    settings_size, z_offset = layouts[0]
    for previous in (5, 10, 20, 40, 0, float("nan")):
        uc, symbols = load(elf)
        uc.mem_write(0xE000ED88, struct.pack("<I", 0xF00000))
        base = symbols["_ZN7Planner8settingsE"]
        before = bytearray(bytes(range(settings_size)))
        struct.pack_into("<f", before, z_offset, previous)
        uc.mem_write(base, bytes(before))
        expected = bytearray(before)
        struct.pack_into("<f", expected, z_offset, 20)
        reached = []

        def after_speed(emulator, *_, reached=reached, base=base):
            reached.append(bytes(emulator.mem_read(base, settings_size)))
            emulator.reg_write(UC_ARM_REG_PC, SENTINEL | 1)

        address = symbols["_ZN7Planner24reset_acceleration_ratesEv"] & ~1
        uc.hook_add(UC_HOOK_CODE, after_speed, begin=address, end=address)
        call(uc, symbols["_ZN14MarlinSettings11postprocessEv"])
        if reached != [bytes(expected)]:
            raise ValueError("Settings postprocess failed to replace only the persisted Z feed ceiling")

    uc, symbols = load(elf)
    uc.mem_write(0xE000ED88, struct.pack("<I", 0xF00000))

    def write_byte(emulator, *_):
        emulator.reg_write(UC_ARM_REG_R0, 1)
        emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

    address = symbols["_ZN14HardwareSerial5writeEh"] & ~1
    uc.hook_add(UC_HOOK_CODE, write_byte, begin=address, end=address)
    call(uc, symbols["_GLOBAL__sub_I_MSerial1"])
    for requested, expected in ((5, 5), (10, 10), (20, 20), (20.01, 20), (40, 20), (1000, 20)):
        uc.reg_write(UC_ARM_REG_R0, 2)
        uc.reg_write(UC_ARM_REG_R1, 0)
        uc.reg_write(UC_ARM_REG_S0, struct.unpack("<I", struct.pack("<f", requested))[0])
        call(uc, symbols["_ZN7Planner16set_max_feedrateEhf"])
        actual = struct.unpack("<f", uc.mem_read(symbols["_ZN7Planner8settingsE"] + z_offset, 4))[0]
        if actual != expected:
            raise ValueError(f"M203 planner setter did not cap Z at20: {requested} -> {actual}")

    header = (ROOT / "firmware/marlin_mainboard/e3_z_setup_speed.h").read_text().replace("#pragma once", "")
    harness = header + r'''
#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (0)
int early(float &maximum) {
  const e3_z_setup_speed::NativeCycle native(maximum);
  CHECK(maximum == 5);
  return 0;
}
extern "C" int test_main() {
  float maximum = 5;
  e3_z_setup_speed::configure(maximum);
  CHECK(maximum == 20 && e3_z_setup_speed::ready(maximum));
  CHECK(!early(maximum) && maximum == 20);
  {
    const e3_z_setup_speed::NativeCycle outer(maximum);
    CHECK(maximum == 5 && !e3_z_setup_speed::ready(maximum));
    { const e3_z_setup_speed::NativeCycle nested(maximum); CHECK(maximum == 5); }
    CHECK(maximum == 5);
  }
  CHECK(maximum == 20);
  maximum = 4;
  { const e3_z_setup_speed::NativeCycle native(maximum); CHECK(maximum == 4); }
  CHECK(maximum == 4 && !e3_z_setup_speed::ready(maximum));
  return 0;
}
'''
    output = ROOT / "build/z-setup-firmware/speed-tests"
    output.mkdir(parents=True, exist_ok=True)
    check_parser(compile_test(output, "native-speed", harness))
    print("Compiled settings preserved other fields across six prior Z ceilings; M203 capped six requests; native scopes restored on success, early return and nesting. Fake I/O only.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    audit(parser.parse_args().elf)
