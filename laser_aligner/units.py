"""Canonical millimetre storage with metric/imperial presentation helpers.

Geometry, calibration, machine configuration, and generated G-code remain in
millimetres.  This module is intentionally Qt- and HTTP-independent so every
frontend can convert only at its input/output boundary.
"""
from __future__ import annotations

import math
import re
from typing import Literal

DisplayUnit = Literal["mm", "in"]
MeasurementKind = Literal["length", "area", "speed"]

MM_PER_INCH = 25.4
_UNIT_PATTERN = re.compile(
    r'^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z²/^\"]*)\s*$'
)


def require_display_unit(value: str) -> DisplayUnit:
    unit = str(value).strip().lower()
    if unit in {"mm", "millimeter", "millimeters", "millimetre", "millimetres"}:
        return "mm"
    if unit in {"in", "inch", "inches", "\""}:
        return "in"
    raise ValueError("Display unit must be mm or in")


def _factor(unit: DisplayUnit, kind: MeasurementKind) -> float:
    if unit == "mm":
        return 1.0
    if kind == "length":
        return MM_PER_INCH
    if kind == "area":
        return MM_PER_INCH**2
    return MM_PER_INCH


def from_mm(value_mm: float, unit: DisplayUnit, kind: MeasurementKind = "length") -> float:
    return float(value_mm) / _factor(unit, kind)


def to_mm(value: float, unit: DisplayUnit, kind: MeasurementKind = "length") -> float:
    return float(value) * _factor(unit, kind)


def suffix(unit: DisplayUnit, kind: MeasurementKind = "length") -> str:
    if kind == "area":
        return " mm²" if unit == "mm" else " in²"
    if kind == "speed":
        return " mm/min" if unit == "mm" else " in/min"
    return " mm" if unit == "mm" else " in"


def format_mm(
    value_mm: float,
    unit: DisplayUnit,
    kind: MeasurementKind = "length",
    *,
    precision: int | None = None,
) -> str:
    value = from_mm(value_mm, unit, kind)
    decimals = precision if precision is not None else (4 if unit == "in" else 3)
    return f"{value:.{decimals}f}{suffix(unit, kind)}"


def parse_to_mm(
    text: str,
    display_unit: DisplayUnit,
    kind: MeasurementKind = "length",
) -> float:
    """Parse a displayed value, accepting an explicit mm/in suffix.

    A missing suffix uses the active display unit.  Area and speed require a
    compatible suffix when one is supplied so a bare ``in`` cannot silently be
    accepted for square inches or inches per minute.
    """
    match = _UNIT_PATTERN.match(str(text))
    if match is None:
        raise ValueError("Enter a finite number optionally followed by mm or in")
    value = float(match.group(1))
    if not math.isfinite(value):
        raise ValueError("Measurement must be finite")
    raw_unit = match.group(2).lower().replace("²", "2")
    if not raw_unit:
        unit = display_unit
    elif kind == "length" and raw_unit in {"mm", "millimeter", "millimeters", "millimetre", "millimetres"}:
        unit = "mm"
    elif kind == "length" and raw_unit in {"in", "inch", "inches", '"'}:
        unit = "in"
    elif kind == "area" and raw_unit in {
        "mm2", "sqmm", "squaremm", "millimeter2", "millimeters2",
        "millimetre2", "millimetres2",
    }:
        unit = "mm"
    elif kind == "area" and raw_unit in {
        "in2", "inch2", "inches2", "sqin", "squarein",
    }:
        unit = "in"
    elif kind == "speed" and raw_unit in {
        "mm/min", "mmpermin", "mmmin", "millimeters/min", "millimetres/min",
    }:
        unit = "mm"
    elif kind == "speed" and raw_unit in {
        "in/min", "inch/min", "inches/min", "inpermin", "inmin",
    }:
        unit = "in"
    else:
        raise ValueError(f"Use a {kind} unit compatible with mm or in")
    return to_mm(value, unit, kind)
