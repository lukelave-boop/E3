from dataclasses import replace

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.machine.service import MachineService


def machine():
    return MachineService(MachineSettings(allow_motion=True), LaserSettings(), hardware_enabled=True)


def program_text(move="G1 X20 Y20 F500 S10"):
    return "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S0\n" + move + "\nM5"


@pytest.mark.parametrize("move", [
    "G1 X20 Y20 F500 S10", "G1 X20.123 Y20.456 F500.0 S0",
    "G1X20Y20F500S10", "G1 X2e1 Y20 F500 S10", "G1 Y20 X20 F500 S10",
])
def test_raster_fast_path_matches_general_validation(move, monkeypatch):
    fast = machine()
    slow = machine()
    monkeypatch.setattr(slow, "_raster_motion_values", lambda _: None)
    assert fast.preflight_program(program_text(move)) == slow.preflight_program(program_text(move))


@pytest.mark.parametrize("move", [
    "G0 X20 Y20 F500 S10", "G1 X20 Y20 F0 S10", "G1 X20 Y20 F999999 S10",
    "G1 X20 Y20 F500 S999999", "G1 X-999 Y20 F500 S10", "G1 X20 Y20 F500 S-1",
    "G1 X20 Y20 F500 S10 M3", "G1 X20 Y20 F500 S10 X0", "G1 X20 Y20 F500 S10 garbage",
    "G91", "G1 X20 Y20 Z0 F500 S10", "G1 X" + "9" * 300 + " Y20 F500",
])
def test_fast_validation_keeps_rejection_paths(move):
    with pytest.raises(SafetyError):
        machine().preflight_program(program_text(move))


def test_local_proof_reuses_validation_but_rejects_changed_settings(monkeypatch):
    service = machine()
    program = service.preflight_program(program_text())
    monkeypatch.setattr(service, "_analyze_program", lambda *a: pytest.fail("reparsed immutable proof"))
    service._require_validated_program_integrity(program)
    service.settings.max_work_feed_mm_min = 100
    with pytest.raises(SafetyError, match="changed after program preflight"):
        service._require_validated_program_integrity(program)


@pytest.mark.parametrize("field,value", [
    ("digest", "0" * 64), ("requires_motion", 1), ("requires_laser_authorization", False),
    ("lines", ("G21", "G90", "M5", "G91", "M5")),
])
def test_local_proof_rejects_replaced_fields(field, value):
    service = machine()
    program = service.preflight_program(program_text())
    object.__setattr__(program, field, value)
    with pytest.raises(SafetyError):
        service._require_validated_program_integrity(program)


def test_forged_program_still_requires_full_validation():
    service = machine()
    program = service.preflight_program(program_text())
    forged = replace(program, lines=("G21", "G90", "M5", "G91", "M5"))
    with pytest.raises(SafetyError):
        service._require_validated_program_integrity(forged)


@pytest.mark.parametrize("gate", ["motion", "hardware"])
def test_fast_motion_keeps_static_authority_gates(gate):
    service = machine()
    if gate == "motion":
        service.settings.allow_motion = False
    else:
        service.hardware_enabled = False
    with pytest.raises(SafetyError):
        service.preflight_program(program_text())
