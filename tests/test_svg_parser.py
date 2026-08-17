import numpy as np
import pytest

import laser_aligner.geometry.svg as svg_module
from laser_aligner.errors import SvgError
from laser_aligner.geometry.svg import parse_svg
from laser_aligner.geometry.transforms import apply_matrix, parse_transform


def _bounds(polylines) -> tuple[float, float, float, float]:
    points = np.vstack([line.points for line in polylines])
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return minimum[0], minimum[1], maximum[0], maximum[1]


def test_svg_shapes_paths_and_physical_size() -> None:
    svg = """
    <svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 200 100">
      <g transform="translate(10,5) scale(0.9)">
        <rect x="0" y="0" width="100" height="40" rx="4"/>
        <path d="M 0 60 C 20 20, 60 100, 100 60 S 180 60, 190 30"/>
        <path d="M 120 20 A 20 12 20 1 1 160 50"/>
        <circle cx="170" cy="75" r="10"/>
      </g>
    </svg>
    """
    geometry = parse_svg(svg)
    assert len(geometry.polylines) == 4
    assert geometry.point_count > 40
    assert geometry.width > 100
    assert geometry.intrinsic_width_mm > 50
    assert geometry.intrinsic_height_mm > 20


def test_relative_path_and_close() -> None:
    geometry = parse_svg('<svg xmlns="http://www.w3.org/2000/svg"><path d="m 10 10 l 20 0 v 20 h -20 z"/></svg>')
    line = geometry.polylines[0]
    assert line.closed
    assert np.allclose(line.points[0], line.points[-1])
    assert np.allclose(line.points.min(axis=0), [10, 10])
    assert np.allclose(line.points.max(axis=0), [30, 30])


def test_transform_matrix_behavior() -> None:
    matrix = parse_transform("translate(10,20) scale(2)")
    point = apply_matrix(np.array([[1.0, 1.0]]), matrix)[0]
    assert np.allclose(point, [12.0, 22.0])


@pytest.mark.parametrize(
    ("width", "height"),
    [
        ("50.8mm", "25.4mm"),
        ("5.08cm", "2.54cm"),
        ("2in", "1in"),
        ("192px", "96px"),
    ],
)
def test_physical_units_and_transformed_groups_map_to_exact_mm(
    width: str,
    height: str,
) -> None:
    geometry = parse_svg(
        f"""
        <svg xmlns="http://www.w3.org/2000/svg"
             width="{width}" height="{height}" viewBox="0 0 200 100">
          <g transform="translate(7,11) scale(1.5,2)">
            <rect x="10" y="20" width="40" height="20"/>
          </g>
        </svg>
        """
    )

    assert geometry.intrinsic_width_mm == pytest.approx(15.24, abs=0.01)
    assert geometry.intrinsic_height_mm == pytest.approx(10.16, abs=0.01)
    assert _bounds(geometry.physical_polylines()) == pytest.approx(
        (5.588, 12.954, 20.828, 23.114),
        abs=0.01,
    )


def test_view_box_only_uses_css_px_physical_scale() -> None:
    geometry = parse_svg(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="-12 7 96 48">
          <rect x="-12" y="7" width="96" height="48"/>
        </svg>
        """
    )

    assert geometry.intrinsic_width_mm == pytest.approx(25.4, abs=0.01)
    assert geometry.intrinsic_height_mm == pytest.approx(12.7, abs=0.01)
    assert _bounds(geometry.physical_polylines()) == pytest.approx(
        (0.0, 0.0, 25.4, 12.7),
        abs=0.01,
    )


def test_no_view_box_without_dimensions_uses_css_px_user_units() -> None:
    geometry = parse_svg(
        """
        <svg xmlns="http://www.w3.org/2000/svg">
          <rect width="96" height="48"/>
        </svg>
        """
    )

    assert geometry.intrinsic_width_mm == pytest.approx(25.4, abs=0.01)
    assert geometry.intrinsic_height_mm == pytest.approx(12.7, abs=0.01)


def test_no_view_box_explicit_dimensions_define_imported_artwork_size() -> None:
    geometry = parse_svg(
        """
        <svg xmlns="http://www.w3.org/2000/svg" width="25.4mm" height="12.7mm">
          <rect x="20" y="10" width="100" height="50"/>
        </svg>
        """
    )

    assert geometry.intrinsic_width_mm == pytest.approx(25.4, abs=0.01)
    assert geometry.intrinsic_height_mm == pytest.approx(12.7, abs=0.01)


def test_preserve_aspect_ratio_controls_view_box_scaling() -> None:
    default_geometry = parse_svg(
        """
        <svg xmlns="http://www.w3.org/2000/svg"
             width="40mm" height="40mm" viewBox="0 0 200 100">
          <rect width="200" height="100"/>
        </svg>
        """
    )
    stretched_geometry = parse_svg(
        """
        <svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm"
             viewBox="0 0 200 100" preserveAspectRatio="none">
          <rect width="200" height="100"/>
        </svg>
        """
    )

    assert (
        default_geometry.intrinsic_width_mm,
        default_geometry.intrinsic_height_mm,
    ) == pytest.approx((40.0, 20.0), abs=0.01)
    assert _bounds(default_geometry.physical_polylines()) == pytest.approx(
        (0.0, 10.0, 40.0, 30.0),
        abs=0.01,
    )
    assert (
        stretched_geometry.intrinsic_width_mm,
        stretched_geometry.intrinsic_height_mm,
    ) == pytest.approx((40.0, 40.0), abs=0.01)


@pytest.mark.parametrize(
    "svg, expected",
    [
        (
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
              <style>rect { display: none; }</style>
              <rect width="10" height="10"/>
            </svg>
            """,
            "CSS <style>",
        ),
        (
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
              <defs><clipPath id="crop"><circle cx="5" cy="5" r="4"/></clipPath></defs>
              <rect width="10" height="10" clip-path="url(#crop)"/>
            </svg>
            """,
            "<clipPath>",
        ),
        (
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
              <defs><mask id="fade"><rect width="10" height="10"/></mask></defs>
              <rect width="10" height="10" mask="url(#fade)"/>
            </svg>
            """,
            "<mask>",
        ),
        (
            """
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
              <rect width="10" height="10" style="transform: scale(0.5)"/>
            </svg>
            """,
            "CSS transform",
        ),
    ],
)
def test_geometry_changing_css_clip_and_mask_semantics_are_rejected(
    svg: str,
    expected: str,
) -> None:
    with pytest.raises(SvgError, match="unsupported rendering semantics") as error:
        parse_svg(svg)

    assert expected in str(error.value)


@pytest.mark.parametrize("attribute", ["width", "height"])
def test_relative_root_dimensions_are_rejected(attribute: str) -> None:
    with pytest.raises(SvgError, match=rf"SVG {attribute} must use an absolute"):
        parse_svg(
            f'<svg xmlns="http://www.w3.org/2000/svg" {attribute}="100%" '
            'viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'
        )


def test_extreme_curve_flattening_aborts_inside_parser_budget() -> None:
    curve = "M0 0 C1e9 1e9 -1e9 1e9 1 0 "
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        f'<path d="{curve * 20}"/></svg>'
    )

    with pytest.raises(SvgError, match="curve expansion exceeds.*flattening limit"):
        parse_svg(svg)


def test_svg_point_and_path_caps_fail_closed_during_shape_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svg_module, "_MAX_SVG_POINTS", 5)
    with pytest.raises(SvgError, match="5-point parser limit"):
        parse_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<polyline points="0,0 1,0 2,1 3,1 4,2 5,2"/></svg>'
        )

    monkeypatch.setattr(svg_module, "_MAX_SVG_POINTS", 100)
    monkeypatch.setattr(svg_module, "_MAX_SVG_PATHS", 2)
    with pytest.raises(SvgError, match="2-path parser limit"):
        parse_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<line x1="0" y1="0" x2="1" y2="1"/>'
            '<line x1="1" y1="0" x2="2" y2="1"/>'
            '<line x1="2" y1="0" x2="3" y2="1"/>'
            "</svg>"
        )


def test_svg_path_token_cap_applies_before_a_large_token_list_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svg_module, "_MAX_SVG_PATH_TOKENS", 8)

    with pytest.raises(SvgError, match="8-token parser limit"):
        parse_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<path d="M0 0 L1 0 L2 0 L3 0"/></svg>'
        )


def test_svg_dtd_and_entity_declarations_are_rejected_before_xml_expansion() -> None:
    with pytest.raises(SvgError, match="DTD and entity declarations"):
        parse_svg(
            '<!DOCTYPE svg [<!ENTITY repeated "0 0">]>'
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<polyline points="&repeated; 1 1"/></svg>'
        )


@pytest.mark.parametrize(
    "invalid_geometry",
    [
        '<path d="M0 0 L10 foo 10 L20 20"/>',
        '<rect x="oops" y="0" width="10" height="10"/>',
        '<polyline points="0,0 10,foo 10,10"/>',
        '<path d="M0 0 A5 5 0 2 0 10 10"/>',
    ],
)
def test_malformed_supported_geometry_rejects_the_complete_svg(
    invalid_geometry: str,
) -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<line x1="0" y1="0" x2="20" y2="20"/>'
        f"{invalid_geometry}</svg>"
    )

    with pytest.raises(SvgError, match="Could not parse SVG"):
        parse_svg(svg)


@pytest.mark.parametrize(
    "transform",
    [
        "translate(10 nope)",
        "rotate(10, 20)",
        "unknown(2)",
        "scale(2) trailing",
    ],
)
def test_malformed_transform_never_falls_back_to_different_geometry(
    transform: str,
) -> None:
    with pytest.raises(SvgError, match="Invalid SVG <rect> transform"):
        parse_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            f'<rect transform="{transform}" width="10" height="10"/></svg>'
        )


def test_duplicate_svg_ids_are_rejected_before_use_resolution() -> None:
    with pytest.raises(SvgError, match="duplicate element id 'target'"):
        parse_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<defs>'
            '<rect id="target" x="1" y="1" width="10" height="10"/>'
            '<circle id="target" cx="50" cy="50" r="5"/>'
            '</defs>'
            '<use href="#target"/>'
            '</svg>'
        )


@pytest.mark.parametrize(
    "ratio",
    [True, "0.01", 0.0, -0.1, float("nan"), float("inf")],
)
def test_curve_tolerance_ratio_must_be_a_finite_positive_number(
    ratio: object,
) -> None:
    with pytest.raises(SvgError, match="finite positive number"):
        parse_svg(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<rect width="10" height="10"/></svg>',
            curve_tolerance_ratio=ratio,
        )
