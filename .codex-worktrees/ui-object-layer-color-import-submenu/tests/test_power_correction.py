from __future__ import annotations

import math

import pytest

from laser_aligner.project.power_correction import (
    braking_distance_mm,
    corner_severity,
    corrected_power,
    corrected_raster_span_motions,
    corrected_vector_motions,
)


def test_power_mapping_is_bounded_and_has_exact_neutral_cases() -> None:
    assert corrected_power(400, 0, 1, 1000) == 400
    assert corrected_power(400, 100, 0, 1000) == 400
    assert corrected_power(400, 100, 1, 1000) == 600
    assert corrected_power(400, -100, 1, 1000) == 200
    assert corrected_power(900, 100, 1, 1000) == 1000
    assert corrected_power(0, -100, 1, 1000) == 0


@pytest.mark.parametrize("correction", [-100, -50, 50, 100])
def test_power_mapping_changes_monotonically_with_severity(correction: float) -> None:
    values = [corrected_power(500, correction, value / 10, 1000) for value in range(11)]
    expected = sorted(values, reverse=correction < 0)
    assert values == expected


def test_power_mapping_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="correction"):
        corrected_power(100, 101, 1, 1000)
    with pytest.raises(ValueError, match="severity"):
        corrected_power(100, 1, -0.1, 1000)
    with pytest.raises(ValueError, match="power_max"):
        corrected_power(100, 1, 1, 0)


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [(0, 0.0), (45, 0.1464), (90, 0.5), (135, 0.8536), (180, 1.0)],
)
def test_corner_severity_uses_straight_continuation_as_zero(
    degrees: float,
    expected: float,
) -> None:
    radians = math.radians(degrees)
    assert corner_severity((1, 0), (math.cos(radians), math.sin(radians))) == pytest.approx(
        expected,
        abs=0.0001,
    )


def test_braking_distance_uses_feed_and_acceleration_units() -> None:
    assert braking_distance_mm(1000, 500) == pytest.approx(0.2777777778)


def test_straight_and_collinear_paths_are_not_subdivided() -> None:
    for points in (
        [(0, 0), (100, 0)],
        [(0, 0), (30, 0), (60, 0), (100, 0)],
    ):
        motions = corrected_vector_motions(
            points,
            base_power=400,
            correction=-100,
            power_max=1000,
            feed_mm_min=2000,
            acceleration_mm_s2=500,
        )
        assert [(motion.x, motion.y) for motion in motions] == points[1:]
        assert {motion.power for motion in motions} == {400}


def test_rectangle_correction_is_localized_and_preserves_geometry() -> None:
    points = [(0, 0), (40, 0), (40, 20), (0, 20), (0, 0)]
    motions = corrected_vector_motions(
        points,
        base_power=400,
        correction=-100,
        power_max=1000,
        feed_mm_min=2000,
        acceleration_mm_s2=500,
    )

    assert motions[-1].x == pytest.approx(0)
    assert motions[-1].y == pytest.approx(0)
    assert all(200 <= motion.power <= 400 for motion in motions)
    assert any(motion.power < 400 for motion in motions)
    assert any(motion.power == 400 for motion in motions)
    assert len(motions) <= 28


def test_short_zigzag_safely_merges_overlapping_zones() -> None:
    points = [(0, 0), (0.2, 0), (0.2, 0.2), (0.4, 0.2), (0.4, 0.4)]
    motions = corrected_vector_motions(
        points,
        base_power=800,
        correction=100,
        power_max=1000,
        feed_mm_min=6000,
        acceleration_mm_s2=500,
    )

    assert motions[-1].x == pytest.approx(0.4)
    assert motions[-1].y == pytest.approx(0.4)
    assert all(math.isfinite(motion.x) and math.isfinite(motion.y) for motion in motions)
    assert all(800 <= motion.power <= 1000 for motion in motions)


def test_sub_precision_correction_zone_does_not_emit_duplicate_points() -> None:
    motions = corrected_vector_motions(
        [(0, 0), (10, 0), (10, 10)],
        base_power=500,
        correction=-100,
        power_max=1000,
        feed_mm_min=1,
        acceleration_mm_s2=500,
    )

    rounded = [(round(motion.x, 3), round(motion.y, 3)) for motion in motions]
    assert rounded == [(10.0, 0.0), (10.0, 10.0)]


def test_raster_correction_stays_out_of_image_when_overscan_is_sufficient() -> None:
    covered = corrected_raster_span_motions(
        (0, 0),
        (20, 0),
        (0, 0),
        (20, 0),
        lead_in_mm=1.0,
        lead_out_mm=1.0,
        base_power=400,
        correction=-100,
        power_max=1000,
        feed_mm_min=1000,
        acceleration_mm_s2=500,
    )
    uncovered = corrected_raster_span_motions(
        (0, 0),
        (20, 0),
        (0, 0),
        (20, 0),
        lead_in_mm=0.0,
        lead_out_mm=0.0,
        base_power=400,
        correction=-100,
        power_max=1000,
        feed_mm_min=1000,
        acceleration_mm_s2=500,
    )

    assert [(motion.x, motion.power) for motion in covered] == [(20.0, 400)]
    assert any(motion.power < 400 for motion in uncovered)
    assert uncovered[-1].x == pytest.approx(20.0)
