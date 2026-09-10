"""Run compiled no-extruder planner routines with fake GPIO/timer/stepper edges.

This executes the final application ELF, not a copied planner implementation.
It checks conversion, queueing, acceleration limits and cleaning rejection.
It does not execute the stepper ISR, validate physical motion, or certify limits.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.arm_const import (
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_S0,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from firmware.marlin_mainboard_compact.audit_profile import call, load  # noqa: E402

P = "_ZN7Planner"
BUFFER_SEGMENT = P + "14buffer_segmentEfffffhf"
SET_POSITION = P + "23set_machine_position_mmEffff"


def member_offsets(die, base=0):
    result = {}
    for child in die.iter_children():
        if child.tag != "DW_TAG_member" or "DW_AT_declaration" in child.attributes:
            continue
        location = child.attributes.get("DW_AT_data_member_location")
        offset = base + (location.value if location else 0)
        name = child.attributes.get("DW_AT_name")
        if name:
            result[name.value.decode()] = offset
        else:
            result.update(member_offsets(child.get_DIE_from_attribute("DW_AT_type"), offset))
    return result


def audit_layout(path):
    """Pin test field offsets to actual debug types before reading native state."""
    layouts = {}
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        functions = {
            name: (symbol["st_value"] & ~1, symbol["st_size"])
            for name in (BUFFER_SEGMENT, SET_POSITION)
            for symbol in elf.get_section_by_name(".symtab").get_symbol_by_name(name)
        }
        for unit in elf.get_dwarf_info().iter_CUs():
            for die in unit.iter_DIEs():
                name = die.attributes.get("DW_AT_name")
                if not name or name.value not in (b"planner_settings_t", b"block_t"):
                    continue
                if die.tag not in ("DW_TAG_typedef", "DW_TAG_structure_type"):
                    continue
                key = name.value.decode()
                if die.tag == "DW_TAG_typedef":
                    die = die.get_DIE_from_attribute("DW_AT_type")
                if "DW_AT_byte_size" not in die.attributes:
                    continue
                layouts[key] = (die.attributes["DW_AT_byte_size"].value, member_offsets(die))
                if len(layouts) == 2:
                    break
            if len(layouts) == 2:
                break
        settings_size, settings = layouts["planner_settings_t"]
        block_size, block = layouts["block_t"]
        expected_settings = {"axis_steps_per_mm": 16, "max_feedrate_mm_s": 28, "travel_acceleration": 48}
        expected_block = {"steps": 24, "millimeters": 16, "step_event_count": 40,
                          "direction_bits": 72, "acceleration_steps_per_s2": 88}
        if (settings_size != 60 or block_size != 96
                or any(settings.get(key) != value for key, value in expected_settings.items())
                or any(block.get(key) != value for key, value in expected_block.items())):
            raise ValueError("Native no-extruder planner layout differs from audited XYZ fixture")
        for name, size in ((P + "11steps_to_mmE", 12), (P + "8settingsE", 60),
                           (P + "29max_acceleration_steps_per_s2E", 12), (P + "12block_bufferE", 1536)):
            symbol = elf.get_section_by_name(".symtab").get_symbol_by_name(name)[0]
            if symbol["st_size"] != size:
                raise ValueError("Planner arrays are not the expected no-extruder allocation")
    return functions


def float_arguments(uc, values):
    for index, value in enumerate(values):
        uc.reg_write(UC_ARM_REG_S0 + index, struct.unpack("<I", struct.pack("<f", value))[0])


def scenario(path, functions, *, xyz=(0, 0, 5), e=0, extruder=0, cleaning=False,
             float_acceleration=False, set_position=False, ceiling_rejected=False):
    uc, symbols = load(path)
    uc.mem_write(0xE000ED88, struct.pack("<I", 0xF00000))  # CP10/11 for real hard-float instructions.
    wakeups = []
    counter_sets = []
    kills = []

    def kill_edge(emulator, *_):
        kills.append(True)
        emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

    address = symbols["_Z4killPKcS0_b"] & ~1
    uc.hook_add(UC_HOOK_CODE, kill_edge, begin=address, end=address)
    # Deliberately turn ordinary soft endstops off. The planner ceiling remains.
    uc.mem_write(symbols["soft_endstop"], b"\0")

    def timer_edge(emulator, *_):
        wakeups.append(emulator.reg_read(UC_ARM_REG_R0))
        emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

    def stepper_edge(emulator, *_):
        values = tuple(struct.unpack("<i", emulator.mem_read(emulator.reg_read(register), 4))[0]
                       for register in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
        counter_sets.append(values)
        emulator.reg_write(UC_ARM_REG_PC, emulator.reg_read(UC_ARM_REG_LR))

    for name, callback in (("_Z26HAL_timer_enable_interrupth", timer_edge),
                           ("_ZN7Stepper12set_positionERKlS1_S1_S1_", stepper_edge)):
        address = symbols[name] & ~1
        uc.hook_add(UC_HOOK_CODE, callback, begin=address, end=address)
    call(uc, symbols["_Z8HAL_initv"])
    call(uc, symbols[P + "4initEv"])
    settings_address = symbols[P + "8settingsE"]
    settings = struct.pack("<4I11f", 1000, 1000, 100, 20000, 80, 80, 400,
                           100, 100, 10, 300, 300, 300, 0, 0)
    uc.mem_write(settings_address, settings)
    steps_address = symbols[P + "11steps_to_mmE"]
    steps = struct.pack("<3f", 1 / 80, 1 / 80, 1 / 400)
    uc.mem_write(steps_address, steps)
    accel_address = symbols[P + "29max_acceleration_steps_per_s2E"]
    accelerations = struct.pack("<3I", 80000, 80000, 40000)
    uc.mem_write(accel_address, accelerations)
    uc.mem_write(symbols[P + "24acceleration_long_cutoffE"], struct.pack("<I", 0 if float_acceleration else 53687))
    uc.mem_write(symbols[P + "8max_jerkE"], struct.pack("<4f", 10, 10, 0.4, 0))
    uc.mem_write(symbols[P + "23cleaning_buffer_counterE"], struct.pack("<H", int(cleaning)))
    blocks = symbols[P + "12block_bufferE"]
    # Reused first-block E storage is deliberately nonzero. EXTRUDERS=0 must
    # ignore it rather than use it to index a nonexistent E acceleration slot.
    uc.mem_write(blocks + 36, struct.pack("<I", 0x12345678))
    untouched = b"\xA5" * (1536 - 96)
    uc.mem_write(blocks + 96, untouched)
    block_before = bytes(uc.mem_read(blocks, 96))
    violations = []

    def read_guard(emulator, _access, address, size, _value, _data):
        pc = emulator.reg_read(UC_ARM_REG_PC)
        if address < blocks + 40 and address + size > blocks + 36:
            violations.append("read unused E step storage")
        # These two real functions convert mm to steps. Their only valid reads
        # from this settings-array span are its three XYZ factors. Probe up to
        # extruder index 255 so the former unguarded E-axis calculation fails.
        if any(start <= pc < start + length for start, length in functions.values()):
            start = settings_address + 16
            if start <= address < start + 4 * 260 and not start <= address <= address + size <= start + 12:
                violations.append("read beyond XYZ step factors")

    uc.hook_add(UC_HOOK_MEM_READ, read_guard)
    float_arguments(uc, (*xyz, e) if set_position else (*xyz, e, 2, 0))
    uc.reg_write(UC_ARM_REG_R0, extruder)
    call(uc, symbols[SET_POSITION if set_position else BUFFER_SEGMENT])
    position = struct.unpack("<4i", uc.mem_read(symbols[P + "8positionE"], 16))
    head = uc.mem_read(symbols[P + "17block_buffer_headE"], 1)[0]
    expected_position = None if ceiling_rejected else (
        tuple(round(value * scale) for value, scale in zip(xyz, (80, 80, 400), strict=True)) + (0,)
    )
    if violations:
        raise ValueError(f"Native no-extruder planner accessed removed E allocation: {violations}")
    if (bytes(uc.mem_read(settings_address, 60)) != settings
            or bytes(uc.mem_read(steps_address, 12)) != steps
            or bytes(uc.mem_read(accel_address, 12)) != accelerations
            or bytes(uc.mem_read(blocks + 96, len(untouched))) != untouched):
        raise ValueError("Planner changed immutable settings or an unused block canary")
    if ceiling_rejected:
        if (kills != [True] or uc.reg_read(UC_ARM_REG_R0) or head
                or position != (0, 0, 0, 0) or wakeups or counter_sets
                or bytes(uc.mem_read(blocks, 96)) != block_before):
            raise ValueError("Z ceiling violation did not kill before queuing or changing position")
        return
    if kills:
        raise ValueError("Z ceiling rejected an in-range target")
    if set_position:
        if position != expected_position or head or counter_sets != [expected_position] or wakeups:
            raise ValueError("Native machine-position conversion did not ignore E")
        return
    accepted = uc.reg_read(UC_ARM_REG_R0)
    if cleaning:
        if accepted or head or position != (0, 0, 0, 0) or wakeups or bytes(uc.mem_read(blocks, 96)) != block_before:
            raise ValueError("Cleaning planner accepted work or changed its block")
        return
    if not any(xyz):
        if accepted != 1 or head or position != (0, 0, 0, 0):
            raise ValueError("E-only input created a motion block")
        return
    block = bytes(uc.mem_read(blocks, 96))
    native_steps = struct.unpack_from("<3I", block, 24)
    count = struct.unpack_from("<I", block, 40)[0]
    distance = struct.unpack_from("<f", block, 16)[0]
    acceleration = struct.unpack_from("<I", block, 88)[0]
    expected_steps = tuple(abs(value) for value in expected_position[:3])
    direction = sum(1 << index for index, value in enumerate(xyz) if value < 0)
    limit = min(rate * max(expected_steps) // step for rate, step in
                zip((80000, 80000, 40000), expected_steps, strict=True) if step)
    if (accepted != 1 or head != 1 or position != expected_position
            or native_steps != expected_steps or count != max(expected_steps)
            or not math.isclose(distance, math.sqrt(sum(
                (value / scale) ** 2 for value, scale in zip(expected_steps, (80, 80, 400), strict=True)
            )), rel_tol=1e-6)
            or not 0 < acceleration <= limit or block[72] != direction
            or wakeups != [0] or counter_sets):
        raise ValueError("Native planner produced an unexpected XYZ block/rate/direction")


def audit_planner(path: Path) -> None:
    functions = audit_layout(path)
    cases = (
        {"xyz": (0, 0, 80)}, {"xyz": (0, 0, 79.999)},
        {"xyz": (0, 0, -2)},
        *({"xyz": (0, 0, z), "ceiling_rejected": True}
          for z in (80.001, 95, 270, math.inf, -math.inf, math.nan)),
        {}, {"e": 12345, "extruder": 255}, {"e": -12345, "extruder": 7},
        {"e": math.nan, "extruder": 255},
        {"xyz": (1, -2, 3), "e": 12345, "extruder": 255},
        {"e": 12345, "extruder": 255, "float_acceleration": True},
        {"cleaning": True, "e": 12345, "extruder": 255},
        {"xyz": (0, 0, 0), "e": 12345, "extruder": 255},
        {"xyz": (1, -2, 3), "e": 12345, "set_position": True},
    )
    for index, options in enumerate(cases):
        try:
            scenario(path, functions, **options)
        except Exception as exc:
            raise ValueError(f"Native planner case {index + 1}: {options}: {exc}") from exc
    print(f"Compiled planner passed {len(cases)} Z-ceiling/XYZ/E-ignored/cleaning/acceleration cases with fake GPIO/timer/stepper/kill edges; settings and unused-block canaries intact. No stepper ISR or hardware motion exercised.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    audit_planner(parser.parse_args().elf)
