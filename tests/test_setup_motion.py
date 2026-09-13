from __future__ import annotations

import pytest

from laser_aligner.errors import SafetyError
from laser_aligner.machine.setup_motion import z_feed_mm_min


@pytest.mark.parametrize("current,target,fine,expected", [
    (0, 80, False, 1200), (79.9, 80, True, 1200), (20, 20, False, 1200),
    (80, 0, False, 600), (30, 29.9, True, 120), (30, 29.9, False, 600),
])
def test_z_travel_rates_are_bounded_and_directional(current, target, fine, expected):
    assert z_feed_mm_min(current, target, fine=fine) == expected


@pytest.mark.parametrize("bad", [True, None, "30", float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("position", ["current", "target"])
def test_unknown_or_nonfinite_z_cannot_select_a_travel_rate(bad, position):
    values = {"current": 20, "target": 30, position: bad}
    with pytest.raises(SafetyError, match="finite"):
        z_feed_mm_min(**values)
