"""Typed mainboard fan and bounded Z controls on the shared controller owner."""

from __future__ import annotations

import re
from collections.abc import Callable

from ..errors import MachineError, SafetyError
from .secondary_controller import CrealityControllerOwner, WriteGuardFactory
from .z_probe import finite_number, parse_position

CAPABILITY = "Cap:E3_MAINBOARD_V1:1"
_REPORT = re.compile(r"E3MB:1 FAN1:(\d+) FAN2:(\d+) Z_KNOWN:([01])")


def validate_control(action: object, value: object, confirmed: object) -> None:
    if type(action) is not str or action not in {"status", "fan1", "fan2", "z"}:
        raise SafetyError("Mainboard action must be status, fan1, fan2 or z")
    if type(confirmed) is not bool:
        raise SafetyError("Mainboard confirmation must be a boolean")
    if action == "status":
        if value is not None:
            raise SafetyError("Status does not accept a value")
    elif action == "z":
        finite_number(value, "Target Z", 20, 80)
        if not confirmed:
            raise SafetyError("Confirm a stowed probe and clearance for the Z move")
    elif type(value) is not int or not 0 <= value <= 100:
        raise SafetyError("Fan percentage must be an integer from 0 through 100")
    elif value > 0 and not confirmed:
        raise SafetyError("Confirm the selected fan output is ready to run")


def parse_status(lines: tuple[str, ...]) -> dict[str, object]:
    reports = [line.strip() for line in lines if line.strip().startswith("E3MB")]
    match = _REPORT.fullmatch(reports[0]) if len(reports) == 1 else None
    if match is None or any(int(match[i]) > 255 for i in (1, 2)):
        raise MachineError("Expected exactly one valid E3MB:1 mainboard status")
    return {"fan1_pwm": int(match[1]), "fan2_pwm": int(match[2]),
            "z_known": match[3] == "1"}


def control(
    owner: CrealityControllerOwner, action: str, value: int | float | None, *,
    confirmed: bool, guard: WriteGuardFactory, on_failure: Callable[[], None],
) -> dict[str, object]:
    validate_control(action, value, confirmed)
    generation = owner.generation
    transcript: list[dict[str, object]] = []

    def execute(command: str) -> tuple[str, ...]:
        with guard():
            if owner.generation != generation:
                raise MachineError("Mainboard session changed; operation cancelled")
        lines = owner._execute_acknowledged(
            command, allow_open=False, timeout=20 if action == "z" else 3,
            write_guard=guard, on_failure=on_failure, interrupt_on_failure=action == "z",
        )
        transcript.append({"command": command, "responses": list(lines)})
        with guard():
            if owner.generation != generation:
                raise MachineError("Mainboard session changed during the operation")
        return lines

    with owner._lock:
        identity = " ".join(execute("M115"))
        caps = [word for word in identity.split() if word.startswith("Cap:E3_MAINBOARD")]
        if caps != [CAPABILITY] or "FIRMWARE_NAME:Marlin " not in identity:
            raise MachineError("Install the E3 mainboard V1 firmware before using these controls")
        # Sticky for this owner: cleanup always includes both OFF commands after
        # the dual-fan profile has been identified, even after a serial failure.
        owner._mainboard_fan1_used = True
        before = parse_status(execute("M123"))
        initial_z = parse_position(execute("M114"))
        if action == "z":
            target = float(value)
            if not before["z_known"] or not 20 <= initial_z <= 80:
                raise SafetyError("Reference the border and raise to Z20 before manual Z control")
            if abs(target - initial_z) > 5.0001:
                raise SafetyError("Each Z move is limited to 5 mm")
            endstops = [s.strip().lower() for s in execute("M119")]
            if [s for s in endstops if s.startswith("z_min:")] != ["z_min: triggered"]:
                raise SafetyError("The expected stowed probe input was not reported")
            execute("G21")
            execute("G90")
            execute(f"G1 Z{target:.3f} F300")
            execute("M400")
            actual = parse_position(execute("M114"))
            if abs(actual - target) > 0.05:
                raise MachineError("The requested Z position was not confirmed")
        elif action.startswith("fan"):
            pwm = (int(value) * 255 + 50) // 100
            execute(f"M106 P{1 if action == 'fan1' else 0} S{pwm}")
            if action == "fan2":
                # The job client caches exact S255/S0, not arbitrary manual PWM.
                owner._secondary_fan_enabled = False if pwm == 0 else True if pwm == 255 else None
            after = parse_status(execute("M123"))
            other = "fan2_pwm" if action == "fan1" else "fan1_pwm"
            if after[action + "_pwm"] != pwm or after[other] != before[other]:
                raise MachineError("Independent fan command readback did not match")
        result = parse_status(execute("M123"))
        result.update(z_mm=parse_position(execute("M114")), firmware=identity,
                      action=action, transcript=transcript, physical_feedback=False)
        return result
