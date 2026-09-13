"""Feed policy for stowed-probe travel; contact cycles keep firmware feeds."""

from __future__ import annotations

import math

from ..errors import MachineError, SafetyError

Z_SETUP_SPEED_CAPABILITY = "Cap:E3_Z_SETUP_SPEED_V1:1"
Z_RAISE_FEED_MM_MIN = 1200
Z_LOWER_FEED_MM_MIN = 600
Z_FINE_LOWER_FEED_MM_MIN = 120


def z_setup_speed_supported(firmware: str) -> bool:
    return [word for word in firmware.split() if word.startswith("Cap:E3_Z_SETUP_SPEED")] == [
        Z_SETUP_SPEED_CAPABILITY
    ]


def require_z_setup_speed(firmware: str) -> None:
    if not z_setup_speed_supported(firmware):
        raise MachineError("Install the matching E3 Z setup speed firmware before moving Z")


def z_feed_mm_min(current: float, target: float, *, fine: bool = False) -> int:
    """Use faster lifting and a separate fine downward gauge approach."""
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in (current, target)):
        raise SafetyError("Z travel positions must be finite numbers")
    if target >= current:
        return Z_RAISE_FEED_MM_MIN
    return Z_FINE_LOWER_FEED_MM_MIN if fine else Z_LOWER_FEED_MM_MIN
