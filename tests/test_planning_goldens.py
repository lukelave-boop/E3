from __future__ import annotations

import pytest
from planning_golden_support import (
    CASE_NAMES,
    canonical_json_text,
    expected_case_dir,
    snapshot_case,
)


def test_planning_golden_json_normalizes_cross_runtime_float_noise() -> None:
    equivalent_pairs = (
        (131.99999999999997, 132.0),
        (29.92290882536933, 29.922908825369326),
        (7.401446237163121, 7.401446237163124),
        (-0.0, 0.0),
    )

    for first, second in equivalent_pairs:
        assert canonical_json_text({"value": first}) == canonical_json_text(
            {"value": second}
        )


def test_planning_golden_json_preserves_meaningful_float_changes() -> None:
    assert canonical_json_text({"value": 1.0}) != canonical_json_text(
        {"value": 1.000000001}
    )


@pytest.mark.parametrize("case_name", CASE_NAMES)
def test_planning_golden_matches_expected(case_name: str) -> None:
    snapshot = snapshot_case(case_name)
    case_dir = expected_case_dir(case_name)

    for filename, actual in snapshot.items():
        expected_path = case_dir / filename
        assert expected_path.is_file(), (
            f"Missing planning golden {expected_path}. "
            "Regenerate it explicitly with scripts/update_planning_goldens.py."
        )
        expected = expected_path.read_text(encoding="utf-8")
        assert actual == expected, (
            f"Planning golden changed for {case_name}/{filename}. "
            "Review the change; do not update goldens just to make the test pass."
        )


@pytest.mark.parametrize("case_name", CASE_NAMES)
def test_planning_golden_generation_is_deterministic(case_name: str) -> None:
    first = snapshot_case(case_name)
    second = snapshot_case(case_name)
    third = snapshot_case(case_name)

    assert first == second == third
