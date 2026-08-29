from __future__ import annotations

import math

import pytest

import laser_aligner.project.path_geometry as path_geometry_module
from laser_aligner.project import (
    MAX_NATIVE_PATH_COORDINATE_MAGNITUDE,
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    evaluate_cubic,
    flatten_native_path,
    native_path_bounds,
    quadratic_to_cubic,
    reverse_subpath,
    split_cubic,
    transform_native_path,
)


def _mixed_geometry() -> NativePathGeometry:
    return NativePathGeometry(
        (
            PathSubpath(
                (-0.5, 0.0),
                (
                    PathLineSegment((0.0, -0.5)),
                    PathCubicSegment(
                        (0.2, -0.5),
                        (0.5, -0.2),
                        (0.5, 0.0),
                    ),
                ),
                closed=True,
            ),
        ),
        fill_rule=PathFillRule.NONZERO,
    )


def test_native_line_and_cubic_round_trip_through_canonical_json() -> None:
    geometry = _mixed_geometry()

    payload = geometry.to_dict()
    restored = NativePathGeometry.from_dict(payload)

    assert restored == geometry
    assert payload == {
        "path_version": 1,
        "fill_rule": "nonzero",
        "subpaths": [
            {
                "start": [-0.5, 0.0],
                "closed": True,
                "segments": [
                    {"type": "line", "to": [0.0, -0.5]},
                    {
                        "type": "cubic",
                        "control_1": [0.2, -0.5],
                        "control_2": [0.5, -0.2],
                        "to": [0.5, 0.0],
                    },
                ],
            }
        ],
    }


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf")])
def test_native_path_rejects_non_json_coordinates(value: object) -> None:
    payload = _mixed_geometry().to_dict()
    payload["subpaths"][0]["segments"][0]["to"][0] = value

    with pytest.raises(ValueError, match="finite number"):
        NativePathGeometry.from_dict(payload)


def test_native_path_rejects_excessive_coordinate_magnitude() -> None:
    with pytest.raises(ValueError, match="coordinate magnitude"):
        PathLineSegment((MAX_NATIVE_PATH_COORDINATE_MAGNITUDE + 1.0, 0.0))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"path_version": True}),
        lambda payload: payload.update({"fill_rule": "paint-bucket"}),
        lambda payload: payload["subpaths"][0]["segments"][0].update(
            {"type": "quadratic"}
        ),
        lambda payload: payload["subpaths"][0].update({"closed": 1}),
        lambda payload: payload["subpaths"][0]["segments"][0].update(
            {"surprise": []}
        ),
    ],
)
def test_native_path_strict_parser_rejects_malformed_fields(mutation) -> None:
    payload = _mixed_geometry().to_dict()
    mutation(payload)

    with pytest.raises(ValueError):
        NativePathGeometry.from_dict(payload)


def test_native_path_rejects_newer_format_version_without_downconversion() -> None:
    payload = _mixed_geometry().to_dict()
    payload["path_version"] = 2

    with pytest.raises(ValueError, match="Unsupported native path format version 2"):
        NativePathGeometry.from_dict(payload)


def test_native_path_rejects_json_nested_beyond_explicit_limit() -> None:
    payload = _mixed_geometry().to_dict()
    nested: object = 0
    for _index in range(path_geometry_module.MAX_NATIVE_PATH_JSON_NESTING + 1):
        nested = [nested]
    payload["unexpected"] = nested

    with pytest.raises(ValueError, match="nesting depth"):
        NativePathGeometry.from_dict(payload)


def test_native_path_enforces_subpath_and_segment_limits(monkeypatch) -> None:
    line = PathLineSegment((1.0, 0.0))
    monkeypatch.setattr(path_geometry_module, "MAX_NATIVE_PATH_SEGMENTS", 1)
    with pytest.raises(ValueError, match="segments"):
        PathSubpath((0.0, 0.0), (line, line))

    subpath = PathSubpath((0.0, 0.0), (line,))
    monkeypatch.setattr(path_geometry_module, "MAX_NATIVE_PATH_SUBPATHS", 1)
    with pytest.raises(ValueError, match="subpaths"):
        NativePathGeometry((subpath, subpath))


def test_native_path_parser_rejects_aggregate_segments_before_construction(
    monkeypatch,
) -> None:
    monkeypatch.setattr(path_geometry_module, "MAX_NATIVE_PATH_SEGMENTS", 2)
    payload = {
        "path_version": 1,
        "fill_rule": "evenodd",
        "subpaths": [
            {
                "start": [0.0, 0.0],
                "closed": False,
                "segments": [
                    {"type": "line", "to": [1.0, 0.0]},
                    {"type": "line", "to": [2.0, 0.0]},
                ],
            },
            {
                "start": [0.0, 1.0],
                "closed": False,
                "segments": [{"type": "line", "to": [1.0, 1.0]}],
            },
        ],
    }

    with pytest.raises(ValueError, match="segments"):
        NativePathGeometry.from_dict(payload)


def test_legacy_polylines_convert_to_line_only_native_subpaths() -> None:
    geometry = NativePathGeometry.from_legacy_polylines(
        [
            {
                "points": [[0, 0], [1, 0], [1, 1], [0, 0]],
                "closed": True,
            },
            {"points": [[2, 2], [3, 3]], "closed": False},
        ]
    )

    assert geometry.fill_rule is PathFillRule.EVENODD
    assert len(geometry.subpaths) == 2
    assert geometry.subpaths[0].start == (0.0, 0.0)
    assert [segment.to for segment in geometry.subpaths[0].segments] == [
        (1.0, 0.0),
        (1.0, 1.0),
    ]
    assert geometry.subpaths[0].closed is True
    assert geometry.subpaths[1].closed is False
    assert all(
        isinstance(segment, PathLineSegment)
        for subpath in geometry.subpaths
        for segment in subpath.segments
    )


def test_cubic_evaluation_and_subdivision_are_deterministic() -> None:
    segment = PathCubicSegment((0.0, 1.0), (1.0, 1.0), (1.0, 0.0))

    assert evaluate_cubic((0.0, 0.0), segment, 0.5) == pytest.approx((0.5, 0.75))
    first, second = split_cubic((0.0, 0.0), segment)
    assert first.to == pytest.approx((0.5, 0.75))
    assert evaluate_cubic((0.0, 0.0), first, 1.0) == pytest.approx(first.to)
    assert evaluate_cubic(first.to, second, 1.0) == pytest.approx(segment.to)
    assert split_cubic((0.0, 0.0), segment) == (first, second)


def test_quadratic_to_cubic_is_mathematically_exact() -> None:
    start = (-2.0, 1.0)
    control = (3.0, 5.0)
    end = (7.0, -1.0)
    cubic = quadratic_to_cubic(start, control, end)

    for index in range(101):
        parameter = index / 100.0
        inverse = 1.0 - parameter
        expected = (
            inverse**2 * start[0]
            + 2.0 * inverse * parameter * control[0]
            + parameter**2 * end[0],
            inverse**2 * start[1]
            + 2.0 * inverse * parameter * control[1]
            + parameter**2 * end[1],
        )
        assert evaluate_cubic(start, cubic, parameter) == pytest.approx(expected)


def test_native_cubic_bounds_include_interior_extrema_after_affine_transform() -> None:
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                (PathCubicSegment((0.0, 1.0), (1.0, 1.0), (1.0, 0.0)),),
            ),
        )
    )
    transform = PathAffineTransform.from_components(
        scale_x=2.0,
        scale_y=4.0,
        translate_x=10.0,
        translate_y=-3.0,
    )

    assert native_path_bounds(geometry) == pytest.approx((0.0, 0.0, 1.0, 0.75))
    assert native_path_bounds(geometry, transform) == pytest.approx(
        (10.0, -3.0, 12.0, 0.0)
    )


def test_affine_transform_compose_and_native_transform_cover_mirrors_rotation_translation() -> None:
    scale_and_mirror = PathAffineTransform.from_components(scale_x=-2.0, scale_y=3.0)
    rotate_and_move = PathAffineTransform.from_components(
        rotation_deg=90.0,
        translate_x=5.0,
        translate_y=7.0,
    )
    combined = rotate_and_move.compose(scale_and_mirror)
    point = (0.25, -0.5)

    assert combined.apply(point) == pytest.approx(
        rotate_and_move.apply(scale_and_mirror.apply(point))
    )
    transformed = transform_native_path(_mixed_geometry(), combined)
    assert transformed.subpaths[0].start == pytest.approx(combined.apply((-0.5, 0.0)))
    cubic = transformed.subpaths[0].segments[1]
    assert isinstance(cubic, PathCubicSegment)
    assert cubic.control_1 == pytest.approx(combined.apply((0.2, -0.5)))
    assert cubic.control_2 == pytest.approx(combined.apply((0.5, -0.2)))


def _distance_to_segment(point, start, end) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0.0:
        return math.dist(point, start)
    ratio = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / length_squared
    ratio = max(0.0, min(1.0, ratio))
    projected = (start[0] + ratio * dx, start[1] + ratio * dy)
    return math.dist(point, projected)


def test_adaptive_flattening_is_deterministic_and_within_physical_tolerance() -> None:
    segment = PathCubicSegment((0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
    geometry = NativePathGeometry((PathSubpath((0.0, 0.0), (segment,)),))
    transform = PathAffineTransform.from_components(scale_x=40.0, scale_y=10.0)
    tolerance = 0.025

    first = flatten_native_path(geometry, tolerance, transform=transform)
    second = flatten_native_path(geometry, tolerance, transform=transform)

    assert first == second
    flattened = first[0]
    for index in range(10_001):
        local = evaluate_cubic((0.0, 0.0), segment, index / 10_000.0)
        physical = transform.apply(local)
        distance = min(
            _distance_to_segment(physical, start, end)
            for start, end in zip(flattened, flattened[1:], strict=False)
        )
        assert distance <= tolerance + 1e-12


@pytest.mark.parametrize(
    ("segment", "transform", "tolerance"),
    [
        (
            PathCubicSegment((10.0, 5.0), (-9.0, -4.0), (1.0, 0.0)),
            PathAffineTransform(),
            0.025,
        ),
        (
            PathCubicSegment((0.0, 1.0), (1.0, -1.0), (1.0, 0.0)),
            PathAffineTransform(),
            0.025,
        ),
        (
            PathCubicSegment((2.0, 2.0), (-2.0, 2.0), (0.001, 0.0)),
            PathAffineTransform(),
            0.025,
        ),
        (
            PathCubicSegment((1.0, 2.0), (-1.0, 2.0), (1e-12, -1e-12)),
            PathAffineTransform(),
            0.025,
        ),
        (
            PathCubicSegment((0.0, 1.0), (1.0, 1.0), (1.0, 0.0)),
            PathAffineTransform.from_components(scale_x=1e-6, scale_y=1e-6),
            1e-8,
        ),
        (
            PathCubicSegment((0.0, 1.0), (1.0, 1.0), (1.0, 0.0)),
            PathAffineTransform.from_components(scale_x=1e6, scale_y=1e6),
            25.0,
        ),
        (
            PathCubicSegment((0.0, 1.0), (1.0, -1.0), (1.0, 0.0)),
            PathAffineTransform.from_components(
                scale_x=-37.0,
                scale_y=4.5,
                rotation_deg=137.0,
                translate_x=12.0,
                translate_y=-8.0,
            ),
            0.025,
        ),
    ],
    ids=(
        "controls-far-beyond-endpoints",
        "s-curve",
        "near-loop",
        "nearly-coincident-endpoints",
        "very-small-scale",
        "very-large-scale",
        "nonuniform-mirror-rotation",
    ),
)
def test_adaptive_flattening_adversarial_curves_obey_physical_error_bound(
    segment: PathCubicSegment,
    transform: PathAffineTransform,
    tolerance: float,
) -> None:
    start = (0.0, 0.0)
    geometry = NativePathGeometry((PathSubpath(start, (segment,)),))

    first = flatten_native_path(geometry, tolerance, transform=transform)[0]
    second = flatten_native_path(geometry, tolerance, transform=transform)[0]

    assert first == second
    numerical_slack = max(1e-12, tolerance * 1e-9)
    for index in range(4_001):
        physical = transform.apply(
            evaluate_cubic(start, segment, index / 4_000.0)
        )
        distance = min(
            _distance_to_segment(physical, chord_start, chord_end)
            for chord_start, chord_end in zip(first, first[1:], strict=False)
        )
        assert distance <= tolerance + numerical_slack


def test_flattening_applies_nonuniform_scaling_before_tolerance() -> None:
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                (PathCubicSegment((0.0, 0.1), (1.0, 0.1), (1.0, 0.0)),),
            ),
        )
    )

    shallow = flatten_native_path(
        geometry,
        0.05,
        transform=PathAffineTransform.from_components(scale_x=1.0, scale_y=0.1),
    )[0]
    tall = flatten_native_path(
        geometry,
        0.05,
        transform=PathAffineTransform.from_components(scale_x=1.0, scale_y=100.0),
    )[0]

    assert len(tall) > len(shallow)


def test_flattening_does_not_collapse_collinear_cubic_overshoot() -> None:
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                (PathCubicSegment((4.0, 0.0), (-3.0, 0.0), (1.0, 0.0)),),
            ),
        )
    )

    points = flatten_native_path(geometry, 0.01)[0]

    assert len(points) > 2
    assert max(point[0] for point in points) > 1.0


def test_flattening_rejects_point_and_recursion_limit_overruns() -> None:
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                (PathCubicSegment((0.0, 1.0), (1.0, 1.0), (1.0, 0.0)),),
            ),
        )
    )

    with pytest.raises(ValueError, match="point limit"):
        flatten_native_path(geometry, 0.001, max_points=2)
    with pytest.raises(ValueError, match="subdivision depth"):
        flatten_native_path(geometry, 0.001, max_depth=0)


def test_reverse_subpath_reverses_cubic_controls_and_preserves_shape() -> None:
    original = PathSubpath(
        (0.0, 0.0),
        (
            PathLineSegment((1.0, 0.0)),
            PathCubicSegment((1.0, 1.0), (2.0, 1.0), (2.0, 0.0)),
        ),
        closed=False,
    )

    reversed_path = reverse_subpath(original)

    assert reversed_path.start == (2.0, 0.0)
    first = reversed_path.segments[0]
    assert isinstance(first, PathCubicSegment)
    assert first.control_1 == (2.0, 1.0)
    assert first.control_2 == (1.0, 1.0)
    assert first.to == (1.0, 0.0)
    assert reverse_subpath(reversed_path) == original
