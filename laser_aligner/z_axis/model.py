from __future__ import annotations

import math
import re
from enum import Enum

from ..errors import MachineError, SafetyError

_AXIS_VALUE_RE = re.compile(
    r"(?:^|\s)([XYZE])\s*:\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)",
    re.IGNORECASE,
)
_ENDSTOP_RE = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*:\s*(\S+)\s*$", re.IGNORECASE)


class ZState(str, Enum):
    UNKNOWN = "UNKNOWN"
    KNOWN = "KNOWN"
    HOMING = "HOMING"
    FAULT = "FAULT"


class ZReferenceMode(str, Enum):
    FIXED_EDGE = "fixed_edge"
    WORK_SURFACE = "work_surface"


def parse_m114(lines: list[str] | tuple[str, ...]) -> dict[str, float]:
    """Parse Marlin's logical position without trusting its fictional X/Y.

    The returned mapping includes every logical axis present before the
    optional ``Count`` suffix.  Callers of the Z subsystem consume only ``Z``.
    """

    parsed: dict[str, float] | None = None
    for raw_line in lines:
        logical = str(raw_line).split("Count", 1)[0]
        values: dict[str, float] = {}
        for match in _AXIS_VALUE_RE.finditer(logical):
            axis = match.group(1).upper()
            try:
                number = float(match.group(2))
            except ValueError as exc:  # pragma: no cover - regex already bounds syntax
                raise MachineError("M114 returned a malformed coordinate") from exc
            if not math.isfinite(number) or axis in values:
                raise MachineError("M114 returned malformed or duplicate coordinates")
            values[axis] = number
        if "Z" in values:
            if parsed is not None:
                raise MachineError("M114 returned more than one logical position")
            parsed = values
    if parsed is None:
        raise MachineError("M114 did not return a parseable logical Z position")
    return parsed


def parse_m119(lines: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Return normalized Marlin endstop states from an M119 exchange."""

    states: dict[str, str] = {}
    for raw_line in lines:
        match = _ENDSTOP_RE.match(str(raw_line))
        if match is None:
            continue
        name = match.group(1).lower()
        value = match.group(2).lower()
        if name in states:
            raise MachineError(f"M119 returned duplicate {name} state")
        states[name] = value
    if "z_min" not in states:
        raise MachineError("M119 did not report the CR Touch z_min state")
    return states


def probe_to_laser_position(
    probe_x_mm: float,
    probe_y_mm: float,
    probe_offset_x_mm: float,
    probe_offset_y_mm: float,
) -> tuple[float, float]:
    """Convert a desired physical probe point to real laser-axis coordinates."""

    values = (
        probe_x_mm,
        probe_y_mm,
        probe_offset_x_mm,
        probe_offset_y_mm,
    )
    if any(type(value) not in {int, float} or not math.isfinite(float(value)) for value in values):
        raise SafetyError("Probe coordinates and offsets must be finite numbers")
    return (
        float(probe_x_mm) - float(probe_offset_x_mm),
        float(probe_y_mm) - float(probe_offset_y_mm),
    )


def effective_safe_z_max(
    safe_max_mm: float,
    mode: ZReferenceMode | str,
    work_surface_height_mm: float | None = None,
    *,
    minimum_homed_z_mm: float = 0.0,
) -> float:
    """Return the reported-Z ceiling that preserves the fixed-reference limit."""

    try:
        reference_mode = ZReferenceMode(mode)
    except ValueError as exc:
        raise SafetyError("Unsupported Z reference mode") from exc
    if type(safe_max_mm) not in {int, float} or not math.isfinite(float(safe_max_mm)):
        raise SafetyError("Z safe maximum must be a finite number")
    maximum = float(safe_max_mm)
    if not 0 < maximum <= 80.0:
        raise SafetyError("Z safe maximum must be positive and cannot exceed 80 mm")
    if reference_mode is ZReferenceMode.FIXED_EDGE:
        return maximum
    if (
        type(work_surface_height_mm) not in {int, float}
        or not math.isfinite(float(work_surface_height_mm))
        or float(work_surface_height_mm) < 0.0
    ):
        raise SafetyError(
            "Work Area / Material Surface homing requires a valid non-negative "
            "surface height above the fixed reference"
        )
    effective = maximum - float(work_surface_height_mm)
    if effective < float(minimum_homed_z_mm):
        raise SafetyError(
            "The work-surface height leaves no verified Z travel below the fixed "
            "80 mm machine-reference ceiling"
        )
    return effective
