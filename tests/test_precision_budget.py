import math

import pytest

from laser_aligner.calibration.precision_budget import precision_error_budget
from laser_aligner.errors import CalibrationError


def test_optical_allowance_uses_larger_observation_then_adds_curve_and_rounding():
    result = precision_error_budget(optical_fit_max_mm=.04, optical_holdout_max_mm=.06)
    assert result["optical_mapping_contribution_mm"] == .06
    assert result["known_numerical_contribution_mm"] == pytest.approx(.06 + .025 + math.sqrt(2) * .0005)
    assert result["remaining_target_mm"] == pytest.approx(.1 - .06 - .025 - math.sqrt(2) * .0005)
    assert result["numerical_optical_evidence_complete"] is True
    assert result["physical_total_mm"] is None
    assert result["physically_qualified"] is False


def test_missing_physical_and_optical_measurements_remain_unknown():
    result = precision_error_budget()
    assert result["optical_mapping_contribution_mm"] is None
    assert result["optical_fit_max_mm"] is None
    assert result["optical_holdout_max_mm"] is None
    assert result["numerical_optical_evidence_complete"] is False
    assert all(part == {"status": "unmeasured", "value_mm": None}
               for part in result["physical_components"].values())
    assert result["physical_total_mm"] is None


def test_z_uncertainty_cannot_be_summed_into_xy_or_grant_physical_qualification():
    result = precision_error_budget(optical_fit_max_mm=0., optical_holdout_max_mm=0.,
                                    localization_mm=.02, position_repeatability_mm=.01, height_uncertainty_mm=.2)
    assert result["physical_components"]["height_uncertainty"] == {"status": "measured", "value_mm": .2}
    assert result["known_numerical_contribution_mm"] < .026
    assert result["physical_total_mm"] is None
    assert result["physically_qualified"] is False


def test_exhausted_budget_does_not_relax_target():
    result = precision_error_budget(optical_fit_max_mm=.1, optical_holdout_max_mm=.11)
    assert result["target_mm"] == .1
    assert result["known_numerical_contribution_mm"] > result["target_mm"]
    assert result["remaining_target_mm"] == 0.
    assert result["physically_qualified"] is False


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "0"])
@pytest.mark.parametrize("field", ["optical_fit_max_mm", "optical_holdout_max_mm", "localization_mm",
                                   "position_repeatability_mm", "height_uncertainty_mm"])
def test_invalid_measurements_cannot_disguise_unknown_or_negative_contributions(field, value):
    with pytest.raises(CalibrationError):
        precision_error_budget(**{field: value})
