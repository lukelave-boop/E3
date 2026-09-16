"""Typed per-operation focus instructions; never controller G-code."""
from __future__ import annotations

import math

from .errors import SafetyError

PREFIX = "E3OPFOCUS"
CAPABILITY = "pi-operation-focus-v1"


def mode(line: str) -> str | None:
    if not line.startswith(PREFIX):
        return None
    if line not in (f"{PREFIX} CUT", f"{PREFIX} RASTER"):
        raise SafetyError("Invalid operation focus instruction")
    return line.split()[1]


def target_z(plan: dict, focus_mode: str) -> float:
    """Preserve the selected cut target; raster restores the taught 7 mm gap."""
    if focus_mode not in ("CUT", "RASTER"):
        raise SafetyError("Invalid operation focus mode")
    try:
        target, gap = plan["target_z_mm"], plan["gap_mm"]
        clearance, maximum = plan["clearance_z_mm"], plan.get("maximum", 80.)
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in (target, gap, clearance, maximum)) or not 3 <= gap <= 7:
            raise ValueError
        target = target if focus_mode == "CUT" else target + (7. - gap)
        if not 0 <= target < clearance <= maximum:
            raise ValueError
        return target
    except (KeyError, TypeError, ValueError):
        raise SafetyError(
            "Operation focus is outside the available Z clearance; correct the setup and measure again"
        ) from None


def validate_targets(lines, plan: dict | None) -> None:
    modes = {mode(line) for line in lines} - {None}
    if not modes:
        return
    if plan is None:
        raise SafetyError("Measure the workpiece before running a raster focus job")
    for focus_mode in modes:
        target_z(plan, focus_mode)
