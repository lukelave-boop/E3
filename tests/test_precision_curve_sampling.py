from __future__ import annotations

import math

import numpy as np
import pytest

from laser_aligner.errors import SvgError
from laser_aligner.gcode.generator import DesignPlacement, place_geometry
from laser_aligner.geometry.svg import parse_svg, parse_svg_for_placement
from laser_aligner.project.model import SceneObject
from laser_aligner.project.toolpath import NATIVE_PATH_FLATTEN_TOLERANCE_MM, object_polylines


@pytest.mark.parametrize("diameter", [50., 220., 600.])
def test_desktop_ellipse_chords_stay_within_physical_envelope(diameter):
    item = SceneObject.ellipse("layer", width_mm=diameter, height_mm=diameter)
    source = item.to_dict()
    points = object_polylines(item)[0].points
    center = np.array([item.transform.x_mm, item.transform.y_mm])
    midpoints = (points[1:] + points[:-1]) / 2
    sagitta = diameter / 2 - np.linalg.norm(midpoints - center, axis=1)
    assert np.max(sagitta) <= NATIVE_PATH_FLATTEN_TOLERANCE_MM
    assert np.ptp(points[:, 0]) == pytest.approx(diameter)
    assert np.ptp(points[:, 1]) == pytest.approx(diameter)
    assert item.to_dict() == source


@pytest.mark.parametrize("radius", [5., 40., 100.])
def test_desktop_rounded_corner_chords_stay_within_physical_envelope(radius):
    item = SceneObject.rectangle("layer", width_mm=220., height_mm=220.)
    item.geometry["corner_radius_mm"] = radius
    points = object_polylines(item)[0].points
    per_corner = (len(points) - 1) // 4
    sagitta = radius * (1 - math.cos((math.pi / 2) / (per_corner - 1) / 2))
    assert sagitta <= .025
    assert np.ptp(points[:, 0]) == pytest.approx(220.)


@pytest.mark.parametrize("shape", [
    '<circle cx="0" cy="0" r="1"/>',
    '<g transform="scale(1000)"><circle cx="0" cy="0" r=".001"/></g>',
    '<path d="M -1 0 A 1 1 0 0 1 1 0 A 1 1 0 0 1 -1 0 Z"/>',
])
def test_browser_large_scaled_curves_keep_physical_chord_envelope(shape):
    svg = f'<svg viewBox="-1 -1 2 2">{shape}</svg>'
    geometry = parse_svg_for_placement(svg, 220., 220.)
    points = place_geometry(geometry, DesignPlacement(0., 0., 220., 220.))[0].points
    midpoints = (points[1:] + points[:-1]) / 2
    assert np.max(np.abs(110. - np.linalg.norm(midpoints, axis=1))) <= .025
    assert geometry.curve_approximation_mm <= .025
    assert np.ptp(points[:, 0]) == pytest.approx(220.)
    assert np.ptp(points[:, 1]) == pytest.approx(220.)


def test_browser_strict_cubic_preserves_collinear_overshoot():
    svg = '<svg viewBox="0 0 10 10"><path d="M0 0 C20 0 20 0 1 0 L1 10 L0 10 Z"/></svg>'
    geometry = parse_svg(svg, curve_tolerance_units=.001)
    assert geometry.width > 15.


def test_browser_rounded_rectangle_includes_straight_side_endpoints():
    svg = '<svg viewBox="0 0 220 220"><rect width="220" height="220" rx="100"/></svg>'
    geometry = parse_svg_for_placement(svg, 220., 220.)
    points = geometry.polylines[0].points
    for endpoint in ((220., 100.), (220., 120.), (120., 220.), (100., 220.)):
        assert np.any(np.all(np.isclose(points, endpoint), axis=1))
    assert geometry.curve_approximation_mm <= .025


@pytest.mark.parametrize("value", [True, 0., -1., float("nan"), float("inf")])
def test_browser_precision_tolerance_rejects_invalid_values(value):
    with pytest.raises(SvgError):
        parse_svg('<svg><circle r="1"/></svg>', curve_tolerance_units=value)


def test_browser_strict_parser_rejects_insufficient_subdivision():
    with pytest.raises(SvgError, match="subdivision limit"):
        parse_svg('<svg><path d="M0 0 Q1 1 2 0 L0 0"/></svg>', curve_tolerance_units=1e-20)


def test_browser_analysis_keeps_exact_imported_circle_dimensions():
    from laser_aligner.app import AppContext
    result = AppContext.analyze_svg(None, '<svg width="220mm" height="220mm" viewBox="-1 -1 2 2"><circle r="1"/></svg>')
    assert result["intrinsic_width_mm"] == pytest.approx(220.)
    assert result["intrinsic_height_mm"] == pytest.approx(220.)
