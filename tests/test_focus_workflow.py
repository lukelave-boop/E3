"""Complete focus workflow on surface-V2 firmware without live-Z telemetry."""
from __future__ import annotations

import pytest

from laser_aligner.config import WorkArea
from laser_aligner.errors import SafetyError
from tests import test_laser_focus as helpers

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe

# Curated numeric regression: the recorded target is outside the old rectangle
# but within the explicitly configured honeycomb. No local files are consulted.
HONEYCOMB = (
    (18.218005, 29.679375), (228.217364, 30.198421),
    (227.698319, 240.197779), (17.698960, 239.678734),
)
SURFACE_POINT = (80.222, 213.031)
PROBE_CARRIAGE = (76.920, 174.423)


def position_and_measure(machine, primary, *, clearance=30.0):
    positioned = machine.focus_control(
        "position_probe", confirmed=True, value=list(SURFACE_POINT),
        clearance_z_mm=clearance,
    )
    assert (primary.x, primary.y) == PROBE_CARRIAGE
    assert positioned["surface"] is None
    measured = machine.focus_control("measure", confirmed=True, clearance_z_mm=clearance)
    measurement_id = measured["surface"]["id"]
    aligned = machine.focus_control(
        "align_laser", confirmed=True, measurement_id=measurement_id,
        clearance_z_mm=clearance,
    )
    assert (primary.x, primary.y) == SURFACE_POINT
    assert aligned["surface"]["id"] == measurement_id
    return measured["surface"]


@pytest.mark.parametrize("step", [2.0, 5.0])
def test_complete_teaching_workflow_without_new_firmware(focus_machine, step):
    machine, setup, primary = focus_machine
    machine.settings.work_area = WorkArea(10.0, 210.0, 10.0, 210.0)
    machine.settings.mainboard_max_z_mm = 40.0
    machine.laser_settings.guarded_output_polygon_mm = HONEYCOMB
    assert "Cap:E3_LIVE_Z_V1:1" not in setup.serial.overrides["M115"]
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    surface = position_and_measure(machine, primary)
    measurement_id = surface["id"]
    assert surface["contact_z_mm"] == 5.0

    # Coarse approach retains this measurement across every separately
    # completed jog. The physical position and readback must both reach Z10.
    for _ in range(int(20 / step)):
        result = machine.focus_control(
            "jog", confirmed=True, measurement_id=measurement_id, value=-step,
        )
        assert result["surface"]["id"] == measurement_id
    assert result["current_readback"]["z_mm"] == setup.serial.z == 10.0

    # Probe contact is not the laser-face collision plane: teach a valid
    # negative mounting offset below the old erroneous contact-coordinate floor.
    machine.focus_control("jog", confirmed=True, measurement_id=measurement_id, value=-5.0)
    machine.focus_control("jog", confirmed=True, measurement_id=measurement_id, value=-0.5)
    taught = machine.focus_control("teach", confirmed=True, measurement_id=measurement_id)
    assert taught["calibration"]["focus_offset_mm"] == -0.5

    for gap, expected_z in [(7, 4.5), (5, 2.5), (3, 0.5)]:
        preview = machine.focus_control(
            "preview", confirmed=True, measurement_id=measurement_id, gap_mm=gap,
        )["preview"]
        assert preview["target_z_mm"] == expected_z
        moved = machine.focus_control(
            "move", confirmed=True, gap_mm=gap, preview_id=preview["id"],
        )
        assert moved["current_readback"]["z_mm"] == setup.serial.z == expected_z
        assert moved["requires_clearance"]

    # Completing the full sequence never loosens the configured travel floor.
    # A rejected move clears measurement authority but must retain the ability
    # to return to the already established clearance.
    before = len(setup.serial.writes)
    with pytest.raises(SafetyError, match="minimum"):
        machine.focus_control("jog", confirmed=True, measurement_id=measurement_id, value=-2.0)
    assert not any(line.startswith("G1") for line in setup.serial.writes[before:])
    lifted = machine.focus_control("clearance", confirmed=True)
    assert lifted["current_readback"]["z_mm"] == setup.serial.z == 30.0
    assert not lifted["requires_clearance"]
    assert not any(line.startswith("M154") for line in setup.serial.writes)
    assert machine.settings.work_area.y_max == 210.0
    assert machine.settings.mainboard_max_z_mm == 40.0
