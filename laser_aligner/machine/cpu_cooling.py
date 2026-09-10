"""Narrow FAN1-only cooling policy and exchanges; no independent serial owner."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import MachineError
from .mainboard import CAPABILITY, parse_status


@dataclass
class CoolingPolicy:
    on: bool = False

    def demand(self, temperature: float | None) -> int:
        if temperature is None:
            self.on = True  # Missing sensor requests cooling, never silently disables it.
        elif type(temperature) not in (int, float) or not math.isfinite(temperature) or not -20 <= temperature <= 150:
            raise ValueError("Invalid CPU temperature")
        elif temperature >= 45:
            self.on = True
        elif temperature <= 40:
            self.on = False
        return 255 if self.on else 0


def update_fan(owner, pwm: int, *, guard) -> dict:
    if type(pwm) is not int or pwm not in (0, 255):
        raise ValueError("CPU fan requires OFF or full speed")

    def execute(command):
        return owner._execute_acknowledged(
            command, allow_open=False, timeout=2, write_guard=guard,
            interrupt_on_failure=False,
        )

    identity = " ".join(execute("M115"))
    caps = [word for word in identity.split() if word.startswith("Cap:E3_MAINBOARD")]
    if caps != [CAPABILITY] or "FIRMWARE_NAME:Marlin " not in identity:
        raise MachineError("CPU cooling requires E3 mainboard V1 firmware")
    owner._mainboard_fan1_used = True
    before = parse_status(execute("M123"))
    if before["fan1_pwm"] == pwm:
        return dict(before, changed=False)
    execute(f"M106 P1 S{pwm}")
    after = parse_status(execute("M123"))
    if after["fan1_pwm"] != pwm or after["fan2_pwm"] != before["fan2_pwm"]:
        raise MachineError("CPU fan readback or independent air-assist state did not match")
    return dict(after, changed=True)
