"""Call-count regressions for the Oct 15 duplicate validation fix, without I/O."""
from unittest.mock import patch

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.machine import service as service_module
from laser_aligner.machine.service import MachineService


@pytest.mark.parametrize("moves", [1, 128, 2048])
@pytest.mark.parametrize("canonical", [True, False])
def test_stream_validation_does_not_repeat_runtime_parser_or_resolve_per_move(
    moves, canonical, monkeypatch,
):
    def forbidden_transport(*args, **kwargs):
        pytest.fail("offline validation attempted to create a controller")

    monkeypatch.setattr(service_module, "create_machine_transport", forbidden_transport)
    machine = MachineService(
        MachineSettings(allow_motion=True), LaserSettings(),
        hardware_enabled=True,
    )
    # Noncanonical word order deliberately uses the complete allowlist parser.
    motion = "G1 X20 Y20 F500 S10" if canonical else "G1 Y20 X20 F500 S10"
    text = "\n".join([
        "G21", "G90", "M5", "G0 X10 Y10 F1000", "M4 S0",
        *[motion] * moves, "M5",
    ])
    with (
        patch.object(service_module, "parse_words", wraps=service_module.parse_words) as parse,
        patch.object(machine, "_resolved_air_assist_commands", wraps=machine._resolved_air_assist_commands) as resolve,
        patch.object(machine, "_check_line_safety", wraps=machine._check_line_safety) as runtime,
    ):
        program = machine.preflight_program(text)
        assert program.requires_laser_authorization
        assert runtime.call_count == 0
        # G21/G90/M5/M4/M5 parse once; canonical XY uses the strict fast path.
        assert parse.call_count == 5 + (0 if canonical else moves)
        assert resolve.call_count <= 10  # Constant overhead, independent of moves.
        before = parse.call_count
        machine._require_validated_program_integrity(program)
        assert parse.call_count == before
        assert runtime.call_count == 0

    # The cached proof must still notice changed safety authority.
    machine.settings.allow_motion = False
    with pytest.raises(SafetyError, match="changed after program preflight"):
        machine._require_validated_program_integrity(program)


def test_fast_validation_still_rejects_unsafe_tail_without_runtime_reparse():
    machine = MachineService(MachineSettings(allow_motion=True), LaserSettings(), hardware_enabled=True)
    text = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S0\n" + "G1 X20 Y20 F500 S10\n" * 128
    with patch.object(machine, "_check_line_safety", side_effect=AssertionError("duplicate parse")):
        with pytest.raises(SafetyError, match="rapid G0"):
            machine.preflight_program(text + "G0 X10 Y10 F1000\nM5")
