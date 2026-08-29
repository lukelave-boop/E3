from __future__ import annotations

import pytest

from laser_aligner.units import format_mm, from_mm, parse_to_mm, to_mm


@pytest.mark.parametrize(
    ("kind", "value_mm", "expected"),
    [("length", 25.4, 1.0), ("area", 645.16, 1.0), ("speed", 25.4, 1.0)],
)
def test_imperial_display_conversions_are_exact(kind: str, value_mm: float, expected: float) -> None:
    assert from_mm(value_mm, "in", kind) == pytest.approx(expected)
    assert to_mm(expected, "in", kind) == pytest.approx(value_mm)


@pytest.mark.parametrize(
    ("text", "display_unit", "kind", "expected_mm"),
    [
        ("1 in", "mm", "length", 25.4),
        ("25.4 mm", "in", "length", 25.4),
        ("1 in²", "mm", "area", 645.16),
        ("1 in/min", "mm", "speed", 25.4),
        ("2 inches/min", "mm", "speed", 50.8),
        ('1"', "mm", "length", 25.4),
        ("2.5", "in", "length", 63.5),
    ],
)
def test_parser_accepts_explicit_or_active_units(
    text: str, display_unit: str, kind: str, expected_mm: float
) -> None:
    assert parse_to_mm(text, display_unit, kind) == pytest.approx(expected_mm)


@pytest.mark.parametrize("text", ["1 cm", "one inch", "nan mm", "1 in2"])
def test_parser_rejects_invalid_or_incompatible_units(text: str) -> None:
    with pytest.raises(ValueError):
        parse_to_mm(text, "mm", "length")


def test_formatter_labels_the_selected_units() -> None:
    assert format_mm(25.4, "in") == "1.0000 in"
    assert format_mm(645.16, "in", "area") == "1.0000 in²"
