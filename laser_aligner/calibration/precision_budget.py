"""Advisory numerical error accounting, separate from physical qualification."""
from __future__ import annotations

import math

from ..errors import CalibrationError

TARGET_XY_MM = 0.1
CURVE_APPROXIMATION_MM = 0.025
GCODE_XY_ROUNDING_RADIAL_MM = math.sqrt(2) * 0.0005


def precision_error_budget(
    *, optical_fit_max_mm: float | None = None,
    optical_holdout_max_mm: float | None = None,
    localization_mm: float | None = None,
    position_repeatability_mm: float | None = None,
    height_uncertainty_mm: float | None = None,
) -> dict:
    """Keep unknown components unknown; this budget grants no accuracy claim.

    Fit and independent-check maxima describe the same optical mapping, so use
    their maximum rather than counting that mapping twice. Curve approximation
    and coordinate rounding can then add in the same direction. These are a
    conservative numerical allowance, not a measured worst-case physical bound.
    Height uncertainty is a Z measurement and must not be added directly to XY.
    """
    values = locals().copy()
    for name, value in values.items():
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise CalibrationError(f"{name} must be a nonnegative finite measurement or unmeasured")
    optical_values = [v for v in (optical_fit_max_mm, optical_holdout_max_mm) if v is not None]
    optical = max(optical_values) if optical_values else None
    known = sum((CURVE_APPROXIMATION_MM, GCODE_XY_ROUNDING_RADIAL_MM, optical or 0.))
    physical = {
        name: {"value_mm": value, "status": "unmeasured" if value is None else "measured"}
        for name, value in (("localization", localization_mm),
                            ("position_repeatability", position_repeatability_mm),
                            ("height_uncertainty", height_uncertainty_mm))
    }
    return {
        "target_mm": TARGET_XY_MM,
        "optical_fit_max_mm": optical_fit_max_mm,
        "optical_holdout_max_mm": optical_holdout_max_mm,
        "optical_mapping_contribution_mm": optical,
        "curve_approximation_mm": CURVE_APPROXIMATION_MM,
        "gcode_xy_rounding_radial_mm": GCODE_XY_ROUNDING_RADIAL_MM,
        "known_numerical_contribution_mm": known,
        "remaining_target_mm": max(0., TARGET_XY_MM - known),
        "numerical_optical_evidence_complete": all(v is not None for v in (optical_fit_max_mm, optical_holdout_max_mm)),
        "physical_components": physical,
        "physical_total_mm": None,
        "physically_qualified": False,
        "advisory_only": True,
        "note": "Known numerical allowances only. Unmeasured physical errors are unknown; remaining target is not demonstrated accuracy.",
    }
