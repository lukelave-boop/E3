"""Acceptance and rejection coverage for scalar parsing and reused point proofs."""
import math
import random

import pytest

from laser_aligner.config import LaserSettings, MachineSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.geometry.polygon import convex_polygon_contains_normalized
from laser_aligner.machine.service import MachineService
from laser_aligner.raster_safety import (
    COORDINATE_REFERENCE,
    RasterTravelEnvelope,
    raster_boundary_directive,
)

SURFACE = ((10., 10.), (90., 10.), (90., 90.), (10., 90.))
GUARD = ((0., 0.), (100., 0.), (100., 100.), (0., 100.))
TRAVEL = RasterTravelEnvelope(-10, 110, -10, 110, True, "simulator", "test",
                             "test", COORDINATE_REFERENCE, "2026-10-15")


def machine(offset=(0., 0.), *, guard=GUARD):
    return MachineService(MachineSettings(
        protocol="grbl", port="test-only", allow_motion=True,
        work_area=WorkArea(0, 100, 0, 100), photo_x=20, photo_y=20,
        raster_travel_envelope=TRAVEL,
    ), LaserSettings(guarded_output_polygon_mm=guard,
                     spot_offset_x_mm=offset[0], spot_offset_y_mm=offset[1]),
        hardware_enabled=True)


def program(body):
    return "\n".join((raster_boundary_directive(SURFACE, TRAVEL), "G21", "G90", "M5",
                      *body, "M5"))


def analyze(service, text):
    assert service._session is None
    return service._analyze_program(text, service._configured_guarded_output_polygon())


@pytest.mark.parametrize("offset", [(0., 0.), (2., -3.), (-4., 1.)])
@pytest.mark.parametrize("guard", [None, GUARD])
def test_endpoint_acceptance_matches_independent_geometry_oracle(offset, guard):
    rng = random.Random(714)
    service = machine(offset, guard=guard)
    for case in range(100):
        current = (30., 30.)
        body = ["G0 X30 Y30 F500", "M4 S0"]
        accepted = True
        for _ in range(12):
            if case % 2:
                point = (rng.uniform(20, 80), rng.uniform(20, 80))
            else:
                point = (rng.choice([-10.000001, -10, 0, 10, 30, 90, 100, 110, 110.000001]),
                         rng.choice([0, 10, 30, 90, 100]))
            # Evaluate exactly the decimal values that will be serialized.
            point = tuple(round(value, 6) for value in point)
            power = rng.choice([0, 10])
            accepted &= all(-10 <= value <= 110 for value in point)
            if power:
                for endpoint in (current, point):
                    spot = (endpoint[0] + offset[0], endpoint[1] + offset[1])
                    accepted &= convex_polygon_contains_normalized(spot, SURFACE)
                    accepted &= convex_polygon_contains_normalized(endpoint, GUARD)
                    accepted &= convex_polygon_contains_normalized(spot, GUARD)
            body.append(f"G1 X{point[0]:.6f} Y{point[1]:.6f} F500 S{power}")
            current = point
        text = program(body)
        if accepted:
            assert analyze(service, text) == (text.splitlines(), True)
        else:
            with pytest.raises(SafetyError):
                analyze(service, text)


@pytest.mark.parametrize("transition", ["M3 S10", "M4 S10", "G1 X30 Y30 F500 S10"])
def test_power_transition_checks_start_after_laser_off_travel(transition):
    # A previously proven point does not authorize a different, laser-off
    # endpoint to start powered motion back into the surface.
    text = program(["G0 X30 Y30 F500", "M4 S10", "G1 X40 Y30 F500",
                    "M4 S0", "G1 X5 Y30 F500", transition])
    with pytest.raises(SafetyError, match="calibrated engraving surface"):
        analyze(machine(), text)


@pytest.mark.parametrize("transition", ["M3 S10", "M4 S10", "G1 X5 Y30 F500 S10"])
def test_zero_displacement_power_transition_does_not_inherit_travel_proof(transition):
    with pytest.raises(SafetyError, match="calibrated engraving surface"):
        analyze(machine(), program(["G0 X5 Y30 F500", "M4 S0", transition]))


@pytest.mark.parametrize("offset,point,error", [
    ((11, 0), (-1, 30), "powered controller"),
    ((-11, 0), (101, 30), "powered controller"),
    ((2, -3), (30, 11), "calibrated engraving surface"),
    ((2, 0), (89, 30), "calibrated engraving surface"),
])
def test_offset_keeps_distinct_controller_and_beam_authority(offset, point, error):
    with pytest.raises(SafetyError, match=error):
        analyze(machine(offset), program([f"G0 X{point[0]} Y{point[1]} F500", "M4 S10"]))


@pytest.mark.parametrize("coordinate,accepted", [
    ("9.9999991", True), ("9.9999989", False), ("10", True),
    ("90.0000009", True), ("90.0000011", False),
])
def test_powered_proof_preserves_polygon_tolerance(coordinate, accepted):
    text = program([f"G0 X{coordinate} Y30 F500", "M4 S10",
                    f"G1 X{coordinate} Y30 F500 S10"])
    if accepted:
        assert analyze(machine(), text)[1]
    else:
        with pytest.raises(SafetyError, match="calibrated engraving surface"):
            analyze(machine(), text)


@pytest.mark.parametrize("line", [
    "G1 X40 Y30 F500 S10", "G1 X40 Y30 F500", "G1 X40 F500 S10",
    "G1 Y40 F500 S10", "G1X40Y30F500S10", "G1 X4e1 Y30 F500 S10",
    "G1 Y30 X40 F500 S10", "G1 X40 Y30 F500 S0", "G1 X40 Y30 F500 S1000",
    "G1 X40 Y30 F0 S10", "G1 X40 Y30 F999999 S10", "G0 X40 Y30 F500 S10",
    "G1 X40 Y30 F500 S1001", "G1 X40 Y30 F500 S-1", "G1 X40 Y30 Fnan S10",
    "G1 Xinf Y30 F500 S10", "G1 X1e999 Y30 F500 S10", "G1 X40 Y30 F500 S1e999",
    "G1 X40 Y30 F500 S10 X50", "G1 X40 Y30 F500 S10 M5", "G1 X40 Y30 F500 Z0",
    "G1 X40 Y30 F500 S10 bad", "G1 X40 Y30 F500 S10 ;comment", "G91",
    "M3 S0", "M4 S10", "M5", "G1 X" + "9" * 260 + " Y30 F500",
])
def test_scalar_parser_matches_full_allowlist_acceptance(line, monkeypatch):
    fast, reference = machine(), machine()
    monkeypatch.setattr(reference, "_raster_motion_values", lambda _: None)
    text = program(["G0 X30 Y30 F500", "M4 S0", line])
    try:
        expected = analyze(reference, text)
    except SafetyError:
        with pytest.raises(SafetyError):
            analyze(fast, text)
    else:
        assert analyze(fast, text) == expected


@pytest.mark.parametrize("axis", ["X", "Y"])
@pytest.mark.parametrize("sign", ["", "-"])
def test_maximum_length_exact_decimal_motion_stays_finite_and_bounds_checked(axis, sign):
    template = "G1 X{} Y30 F500" if axis == "X" else "G1 X30 Y{} F500"
    number = sign + "9" * (256 - len(template.format(sign)))
    line = template.format(number)
    assert len(line) == 256
    service = machine()
    parsed = service._raster_motion_values(line)
    assert parsed is not None and all(math.isfinite(value) for value in parsed[1:3])
    with pytest.raises(SafetyError, match="verified travel"):
        analyze(service, program(["G0 X30 Y30 F500", line]))
    with pytest.raises(SafetyError, match="exceeds 256"):
        service._raster_motion_values(line + "0")


@pytest.mark.parametrize("word,error", [("F", "feed.*ceiling"), ("S", "S power")])
def test_maximum_length_decimal_feed_and_power_keep_ceiling(word, error):
    template = "G1 X30 Y30 F{}" if word == "F" else "G1 X30 Y30 F500 S{}"
    line = template.format("9" * (256 - len(template.format(""))))
    assert len(line) == 256
    with pytest.raises(SafetyError, match=error):
        machine()._raster_motion_values(line)


@pytest.mark.parametrize("offset", [math.nan, math.inf, -math.inf])
def test_nonfinite_offset_cannot_reuse_point_authority(offset):
    service = machine()
    text = program(["G0 X30 Y30 F500", "M4 S10", "G1 X40 Y30 F500"])
    analyze(service, text)
    service.laser_settings.spot_offset_x_mm = offset
    with pytest.raises(SafetyError):
        analyze(service, text)


def test_completed_proof_does_not_survive_new_offset_or_guard():
    service = machine()
    text = program(["G0 X30 Y30 F500", "M4 S10", "G1 X89 Y30 F500"])
    analyze(service, text)
    service.laser_settings.spot_offset_x_mm = 2
    with pytest.raises(SafetyError, match="calibrated engraving surface"):
        analyze(service, text)
    service.laser_settings.spot_offset_x_mm = 0
    service.laser_settings.guarded_output_polygon_mm = ((0., 0.), (80., 0.), (80., 100.), (0., 100.))
    with pytest.raises(SafetyError, match="surface extends outside"):
        analyze(service, text)
