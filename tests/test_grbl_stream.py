import random
import re
from decimal import Decimal

import pytest

from laser_aligner.machine.grbl_stream import (
    GrblStreamCompactor,
    GrblStreamWindow,
    canonical_linear_move,
    parse_stream_window,
)


@pytest.mark.parametrize(
    "command",
    [
        "G1 X0 Y0 F1",
        "G1 X-20.001 Y50.000 F6000.000 S100",
        "G1 X0001.0100 Y-0.000 F0006000.000 S000.000",
        "G1 X1.23456789012345678901234567890 Y2 F0.000000000000001 S0.000000000000001",
    ],
)
def test_only_bounded_canonical_linear_moves_are_eligible(command):
    assert canonical_linear_move(command)


@pytest.mark.parametrize(
    "command",
    [
        "G0 X1 Y2 F3",
        "G1 X1 Y2 Z3 F4",
        "G1 X1 F3",
        "G1 X1 Y2",
        "G1X1Y2F3",
        "g1 x1 y2 f3",
        "G01 X1 Y2 F3",
        " G1 X1 Y2 F3",
        "G1 X1 Y2 F3 ",
        "G1  X1 Y2 F3",
        "G1 X1 Y2 F3\n",
        "G1 X1 Y2 F3\rM4 S100",
        "G1 X1 Y2 F3;M4",
        "G1 X1 Y2 F3 (comment)",
        "G1 X1 Y2 F3 M4",
        "G1 X1 Y2 F3 S1 S0",
        "G1 X1 Y2 F3 X4",
        "G1 X1 Y2 F0",
        "G1 X1 Y2 F000.000",
        "G1 X1 Y2 F-3",
        "G1 X1 Y2 FNaN",
        "G1 X1 Y2 F3 S-1",
        "G1 X1 Y2 F3 S1e2",
        "G1 X+1 Y2 F3",
        "G1 X.1 Y2 F3",
        "G1 X1. Y2 F3",
        "G1 X1e2 Y2 F3",
        "G1 X１ Y2 F3",
        "G1 X1\tY2 F3",
        "G1 X1 Y2 F3\x00",
        "G1 X1 Y2 F" + "1" * 256,
        "M5",
        "G90",
        "G21",
        "E3AIRASSIST binding ON",
        "E3RASTER data",
        "",
        None,
    ],
)
def test_unsupported_or_malformed_input_cannot_enter_compactor(command):
    assert not canonical_linear_move(command)
    with pytest.raises(ValueError, match="canonical G1"):
        GrblStreamCompactor().encode(command)


def test_compactor_preserves_power_changes_and_omits_only_known_modal_values():
    compactor = GrblStreamCompactor()
    commands = (
        "G1 X10.000 Y20.000 F6000.000 S0",
        "G1 X10.100 Y20.000 F6000.000 S100",
        "G1 X10.200 Y20.000 F6000.000 S100",
        "G1 X10.300 Y20.000 F6000.000 S0",
        "G1 X10.300 Y21.000 F3000.000 S0",
        "G1 X10.200 Y21.100 F3000.000 S25.500",
    )
    assert [compactor.encode(command) for command in commands] == [
        "G1X10Y20F6000S0",
        "X10.1S100",
        "X10.2",
        "X10.3S0",
        "Y21F3000",
        "X10.2Y21.1S25.5",
    ]
    assert commands[0] == "G1 X10.000 Y20.000 F6000.000 S0"


def test_no_s_word_retains_previous_power_without_inventing_a_new_value():
    compactor = GrblStreamCompactor()
    assert compactor.encode("G1 X0 Y0 F1") == "G1X0Y0F1"
    assert compactor.encode("G1 X1 Y0 F1 S10") == "X1S10"
    assert compactor.encode("G1 X2 Y0 F1") == "X2"
    assert compactor.encode("G1 X3 Y0 F1 S10") == "X3"
    assert compactor.encode("G1 X4 Y0 F1 S0") == "X4S0"


def test_coincident_motion_and_power_transitions_never_disappear():
    compactor = GrblStreamCompactor()
    assert compactor.encode("G1 X1 Y2 F3 S10") == "G1X1Y2F3S10"
    assert compactor.encode("G1 X1 Y2 F3 S10") == "X1"
    assert compactor.encode("G1 X1 Y2 F3 S0") == "X1S0"
    assert compactor.encode("G1 X1 Y2 F4 S0") == "X1F4"


@pytest.mark.parametrize("barrier", ["M5", "M4 S0", "G0 X1 Y2 F3", "G90", "E3AIRASSIST binding OFF"])
def test_new_contiguous_stream_reestablishes_every_explicit_word(barrier):
    compactor = GrblStreamCompactor()
    command = "G1 X1.000 Y2.000 F3.000 S0"
    assert compactor.encode(command) == "G1X1Y2F3S0"
    assert not canonical_linear_move(barrier)
    compactor.reset()
    assert compactor.encode(command) == "G1X1Y2F3S0"


def test_decimal_compaction_is_exact_and_independent_of_decimal_context():
    compactor = GrblStreamCompactor()
    x = "-0001.1234567890123456789012345678901234567890000"
    y = "000.000000000000000000000000000000000000000000000000100"
    wire = compactor.encode(f"G1 X{x} Y{y} F6000.000 S000.000")
    assert wire == "G1X-1.123456789012345678901234567890123456789Y0.0000000000000000000000000000000000000000000000001F6000S0"
    wire_x, rest = wire.removeprefix("G1X").split("Y")
    wire_y, _ = rest.split("F")
    assert Decimal(wire_x) == Decimal(x)
    assert Decimal(wire_y) == Decimal(y)
    assert compactor.encode(f"G1 X{x} Y{y} F6000 S0") == "X-1.123456789012345678901234567890123456789"


def test_negative_zero_is_equivalent_and_invalid_lines_do_not_update_state():
    compactor = GrblStreamCompactor()
    assert compactor.encode("G1 X-0.000 Y-00.000 F1 S0") == "G1X0Y0F1S0"
    with pytest.raises(ValueError):
        compactor.encode("G1 X1 Y1 F1 S-1")
    assert compactor.encode("G1 X0.000 Y0 F1.000 S0.000") == "X0"


def test_modal_replay_matches_every_original_move_across_detailed_bidirectional_rows():
    compactor = GrblStreamCompactor()
    randomizer = random.Random(281)
    original_state = {}
    wire_state = {}
    originals = []
    wires = []
    for row in range(20):
        # Independent decimal replay includes blank pixels, changing feed and
        # power, absent S words, and both scan directions.
        compactor.reset()
        x_positions = range(100) if row % 2 == 0 else range(99, -1, -1)
        for x in x_positions:
            command = f"G1 X{x // 10}.{x % 10}00 Y{row}.000 F{randomizer.choice((3000, 6000))}.000"
            power = randomizer.choice((None, "0", "100", "25.125000"))
            if power is not None:
                command += " S" + power
            wire = compactor.encode(command)
            originals.append(command)
            wires.append(wire)
            for line, state in ((command, original_state), (wire, wire_state)):
                words = re.findall(r"([GXYFS])(-?[0-9]+(?:\.[0-9]+)?)", line)
                assert words
                state.update({letter: Decimal(value) for letter, value in words})
            assert wire_state == original_state
    assert len(wires) == len(originals) == 2000
    assert sum(len(wire) + 1 for wire in wires) < sum(len(line) + 1 for line in originals) * 0.6


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (["[VER:1.1h:example]", "[OPT:V,15,128]"], GrblStreamWindow(127, 15)),
        (["[OPT:VN0,16,1024]"], GrblStreamWindow(127, 16)),
        (["[OPT:,8,64]"], GrblStreamWindow(63, 8)),
        (["[OPT:V,1,2]"], GrblStreamWindow(1, 1)),
        (["[OPT:V,999999,999999]"], GrblStreamWindow(127, 16)),
    ],
)
def test_window_uses_only_advertised_capacity_with_fixed_host_ceilings(identity, expected):
    assert parse_stream_window(identity) == expected


@pytest.mark.parametrize(
    "identity",
    [
        [],
        ["[VER:1.1h:example]"],
        ["<Idle|Bf:15,128>"],
        ["[OPT:V,0,128]"],
        ["[OPT:V,15,0]"],
        ["[OPT:V,15,1]"],
        ["[OPT:V,-15,128]"],
        ["[OPT:V,15,+128]"],
        ["[OPT:V,15,128.0]"],
        ["[OPT:V,15,1000000]"],
        ["[OPT:V,15,128,extra]"],
        ["[OPT:V,15,128] "],
        ["[OPT:V,15,128]\n"],
        ["[OPT:V,15,１２８]"],
        ["[OPT:V,15,128]", "[OPT:V,15,128]"],
        ["[OPT:V,15,128]", "[OPT:V,15,64]"],
        ["[OPT:V,15,128]", "[OPT invalid]"],
        ["[OPT:" + "V" * 256 + ",15,128]"],
    ],
)
def test_missing_or_ambiguous_evidence_never_invents_a_receive_window(identity):
    assert parse_stream_window(identity) == GrblStreamWindow(None, 1)
