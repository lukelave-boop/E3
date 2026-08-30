from __future__ import annotations

import numpy as np
import pytest

from laser_aligner.geometry.foreground import (
    clean_foreground_components,
    clean_foreground_components_with_diagnostics,
)


def test_component_and_hole_minimums_are_independent() -> None:
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[2:18, 2:18] = 255
    mask[7:9, 7:9] = 0
    mask[0, 19] = 255

    object_filtered, object_count = clean_foreground_components(
        mask,
        minimum_component_area_px=5.0,
        minimum_hole_area_px=0.0,
    )
    hole_filtered, hole_count = clean_foreground_components(
        mask,
        minimum_component_area_px=0.0,
        minimum_hole_area_px=5.0,
    )

    assert object_count == 1
    assert object_filtered[0, 19] == 0
    assert np.all(object_filtered[7:9, 7:9] == 0)
    assert hole_count == 2
    assert hole_filtered[0, 19] == 255
    assert np.all(hole_filtered[7:9, 7:9] == 255)


def test_absent_hole_minimum_retains_legacy_component_minimum_behavior() -> None:
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[2:18, 2:18] = 255
    mask[7:9, 7:9] = 0

    inherited, inherited_diagnostics = (
        clean_foreground_components_with_diagnostics(
            mask,
            minimum_component_area_px=5.0,
        )
    )
    independent, independent_diagnostics = (
        clean_foreground_components_with_diagnostics(
            mask,
            minimum_component_area_px=5.0,
            minimum_hole_area_px=0.0,
        )
    )

    assert np.all(inherited[7:9, 7:9] == 255)
    assert inherited_diagnostics.minimum_hole_area_px == 5.0
    assert inherited_diagnostics.filled_below_min_count == 1
    assert np.all(independent[7:9, 7:9] == 0)
    assert independent_diagnostics.minimum_hole_area_px == 0.0
    assert independent_diagnostics.preserved_hole_count == 1


@pytest.mark.parametrize(
    ("minimum_hole_area_px", "maximum_hole_area_px", "preserved"),
    [
        (7.0, None, False),
        (6.0, None, True),
        (5.0, 7.0, True),
        (0.0, 6.0, True),
        (0.0, 5.0, False),
        (0.0, None, True),
    ],
)
def test_hole_range_is_inclusive_and_unbounded_maximum_is_supported(
    minimum_hole_area_px: float,
    maximum_hole_area_px: float | None,
    preserved: bool,
) -> None:
    mask = np.zeros((16, 16), dtype=np.uint8)
    mask[2:14, 2:14] = 255
    mask[6:8, 6:9] = 0

    cleaned, diagnostics = clean_foreground_components_with_diagnostics(
        mask,
        minimum_component_area_px=0.0,
        minimum_hole_area_px=minimum_hole_area_px,
        maximum_hole_area_px=maximum_hole_area_px,
    )

    assert np.all(cleaned[6:8, 6:9] == (0 if preserved else 255))
    assert diagnostics.raw_hole_count == 1
    assert diagnostics.preserved_hole_count == int(preserved)
    assert (
        diagnostics.filled_below_min_count
        + diagnostics.filled_above_max_count
        == int(not preserved)
    )


def test_multiple_holes_are_filtered_and_counted_independently() -> None:
    mask = np.zeros((24, 24), dtype=np.uint8)
    mask[2:22, 2:22] = 255
    mask[5, 5] = 0
    mask[8:10, 8:10] = 0
    mask[13:15, 13:16] = 0

    cleaned, diagnostics = clean_foreground_components_with_diagnostics(
        mask,
        minimum_component_area_px=0.0,
        minimum_hole_area_px=2.0,
        maximum_hole_area_px=5.0,
    )

    assert cleaned[5, 5] == 255
    assert np.all(cleaned[8:10, 8:10] == 0)
    assert np.all(cleaned[13:15, 13:16] == 255)
    assert diagnostics.raw_hole_count == 3
    assert diagnostics.preserved_hole_count == 1
    assert diagnostics.filled_below_min_count == 1
    assert diagnostics.filled_above_max_count == 1


def test_no_maximum_preserves_an_arbitrarily_large_enclosed_hole() -> None:
    mask = np.zeros((48, 48), dtype=np.uint8)
    mask[2:46, 2:46] = 255
    mask[8:40, 8:40] = 0

    cleaned, diagnostics = clean_foreground_components_with_diagnostics(
        mask,
        minimum_component_area_px=0.0,
        minimum_hole_area_px=2.0,
        maximum_hole_area_px=None,
    )

    assert np.array_equal(cleaned, mask)
    assert diagnostics.raw_hole_count == 1
    assert diagnostics.preserved_hole_count == 1
    assert diagnostics.filled_above_max_count == 0


def test_border_touching_background_is_never_filled_or_counted_as_a_hole() -> None:
    mask = np.full((18, 18), 255, dtype=np.uint8)
    mask[0:5, 0:5] = 0
    mask[8:11, 8:11] = 0

    cleaned, diagnostics = clean_foreground_components_with_diagnostics(
        mask,
        minimum_component_area_px=0.0,
        minimum_hole_area_px=0.0,
        maximum_hole_area_px=0.0,
    )

    assert np.all(cleaned[0:5, 0:5] == 0)
    assert np.all(cleaned[8:11, 8:11] == 255)
    assert diagnostics.raw_hole_count == 1
    assert diagnostics.filled_above_max_count == 1


def test_protected_background_is_never_filled_or_counted_as_a_hole() -> None:
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[2:18, 2:18] = 255
    mask[7:13, 7:13] = 0
    protected = np.zeros_like(mask)
    protected[8:12, 8:12] = 255

    cleaned, diagnostics = clean_foreground_components_with_diagnostics(
        mask,
        minimum_component_area_px=0.0,
        minimum_hole_area_px=0.0,
        maximum_hole_area_px=0.0,
        protected_background_mask=protected,
    )

    assert np.array_equal(cleaned, mask)
    assert diagnostics.raw_hole_count == 0
    assert diagnostics.preserved_hole_count == 0
    assert diagnostics.filled_below_min_count == 0
    assert diagnostics.filled_above_max_count == 0


def test_nested_island_is_preserved_with_its_hole() -> None:
    mask = np.zeros((25, 25), dtype=np.uint8)
    mask[2:23, 2:23] = 255
    mask[6:19, 6:19] = 0
    mask[10:15, 10:15] = 255

    cleaned, diagnostics = clean_foreground_components_with_diagnostics(
        mask,
        minimum_component_area_px=0.0,
        minimum_hole_area_px=144.0,
        maximum_hole_area_px=144.0,
    )

    assert np.array_equal(cleaned, mask)
    assert diagnostics.retained_component_count == 2
    assert diagnostics.raw_hole_count == 1
    assert diagnostics.preserved_hole_count == 1


def test_nested_island_is_absorbed_when_its_enclosing_hole_is_filled() -> None:
    mask = np.zeros((25, 25), dtype=np.uint8)
    mask[2:23, 2:23] = 255
    mask[6:19, 6:19] = 0
    mask[10:15, 10:15] = 255

    cleaned, diagnostics = clean_foreground_components_with_diagnostics(
        mask,
        minimum_component_area_px=0.0,
        minimum_hole_area_px=0.0,
        maximum_hole_area_px=143.0,
    )

    assert np.all(cleaned[2:23, 2:23] == 255)
    assert np.all(cleaned[:2] == 0)
    assert diagnostics.retained_component_count == 2
    assert diagnostics.raw_hole_count == 1
    assert diagnostics.filled_above_max_count == 1


def test_wrench_like_root_survives_independent_reflection_hole_filtering() -> None:
    mask = np.zeros((36, 64), dtype=np.uint8)
    mask[13:23, 5:50] = 255
    mask[7:29, 42:59] = 255
    mask[16, 12] = 0
    mask[15:18, 20:23] = 0
    mask[14:18, 29:34] = 0
    mask[12:19, 48:55] = 0

    cleaned, diagnostics = clean_foreground_components_with_diagnostics(
        mask,
        minimum_component_area_px=50.0,
        minimum_hole_area_px=2.0,
        maximum_hole_area_px=30.0,
    )

    assert diagnostics.retained_component_count == 1
    assert diagnostics.raw_hole_count == 4
    assert diagnostics.preserved_hole_count == 2
    assert diagnostics.filled_below_min_count == 1
    assert diagnostics.filled_above_max_count == 1
    assert cleaned[16, 12] == 255
    assert np.all(cleaned[15:18, 20:23] == 0)
    assert np.all(cleaned[14:18, 29:34] == 0)
    assert np.all(cleaned[12:19, 48:55] == 255)
    assert np.count_nonzero(cleaned) > 50


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"minimum_component_area_px": -1.0}, "minimum_component_area_px"),
        ({"minimum_component_area_px": float("inf")}, "minimum_component_area_px"),
        ({"minimum_hole_area_px": float("nan")}, "minimum_hole_area_px"),
        ({"minimum_hole_area_px": True}, "minimum_hole_area_px"),
        ({"maximum_hole_area_px": -1.0}, "maximum_hole_area_px"),
        (
            {"minimum_hole_area_px": 5.0, "maximum_hole_area_px": 4.0},
            "greater than or equal",
        ),
    ],
)
def test_cleanup_rejects_invalid_area_thresholds(
    changes: dict[str, object],
    message: str,
) -> None:
    mask = np.zeros((4, 4), dtype=np.uint8)
    arguments: dict[str, object] = {"minimum_component_area_px": 0.0}
    arguments.update(changes)

    with pytest.raises(ValueError, match=message):
        clean_foreground_components(mask, **arguments)
