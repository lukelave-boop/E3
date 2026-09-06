from __future__ import annotations

import hashlib

import pytest

from laser_aligner.air_assist import AirAssistCommands, AirAssistMode, AirAssistSettings
from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.machine.controller_dialects import resolve_air_assist_commands
from laser_aligner.machine.service import MachineService


def _secondary_settings() -> AirAssistSettings:
    return AirAssistSettings(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN,
        port="secondary-test-port",
    )


@pytest.mark.parametrize(
    "line",
    [
        "G21", "G90", "M5", "M4 S10", "G0 X100 Y100 F4000",
        " G1 X100.123 Y99.456 F4000 ", "G1 X99 Y98 F4000 S10",
        "", "; E3AIRASSIST is only a comment here",
    ],
)
def test_secondary_classifier_does_not_hash_mapping_for_ordinary_lines(
    monkeypatch: pytest.MonkeyPatch,
    line: str,
) -> None:
    commands = resolve_air_assist_commands(_secondary_settings(), protocol="grbl")
    assert commands is not None

    def unexpected_digest(_commands: AirAssistCommands) -> str:
        raise AssertionError("ordinary lines must not serialize or hash the mapping")

    monkeypatch.setattr(AirAssistCommands, "mapping_digest", property(unexpected_digest))

    assert commands.kind_for_program_line(line) is None


@pytest.mark.parametrize("enabled", [False, True])
def test_secondary_classifier_still_accepts_only_exact_bound_directives(
    enabled: bool,
) -> None:
    commands = resolve_air_assist_commands(_secondary_settings(), protocol="grbl")
    assert commands is not None
    directive = commands.program_lines(enabled)[0]
    assert commands.kind_for_program_line(f" \t{directive}\r\n") == (
        "on" if enabled else "off"
    )

    for malformed in (
        directive.replace(commands.mapping_digest, "0" * 64),
        directive + " extra",
        directive.replace("E3AIRASSIST ", "E3AIRASSISTED "),
        directive.replace("E3AIRASSIST ", "E3AIRASSIST  "),
        "E3AIRASSIST",
    ):
        with pytest.raises(ValueError, match="exactly match"):
            commands.kind_for_program_line(malformed)


def test_secondary_preflight_preserves_program_bytes_digest_and_power_authority() -> None:
    settings = _secondary_settings()
    commands = resolve_air_assist_commands(settings, protocol="grbl")
    assert commands is not None
    off = commands.program_lines(False)[0]
    on = commands.program_lines(True)[0]
    lines = (
        "G21", "G90", "M5", off, "G0 X100 Y100 F4000", on, "M4 S10",
        "G1 X101 Y100 F4000", "G1 X101 Y101 F4000 S10", "M5", off, "M5",
    )
    text = "\n".join(lines)
    machine = MachineService(
        MachineSettings(
            backend="serial", protocol="grbl", allow_motion=True, air_assist=settings,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )

    program = machine.preflight_program(text)

    assert program.lines == lines
    assert program.digest == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert program.requires_laser_authorization is True
    assert program.requires_motion is True
    assert program.air_assist_commands == commands
    machine._require_validated_program_integrity(program)
