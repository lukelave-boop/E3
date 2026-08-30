from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

from laser_aligner.project import (
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    native_path_bounds,
    transform_native_path,
)
from laser_aligner.vision.trace_orientation import (
    MAX_TRACE_ORIENTATION_SEGMENTS,
    MAX_TRACE_ORIENTATION_SUBPATHS,
    TraceOrientationGeometry,
    estimate_trace_orientation,
    trace_rotation_transform,
)


def _rectangle(
    center: tuple[float, float],
    width: float,
    height: float,
) -> NativePathGeometry:
    half_width = width / 2.0
    half_height = height / 2.0
    local = NativePathGeometry(
        (
            PathSubpath(
                (-half_width, -half_height),
                (
                    PathLineSegment((half_width, -half_height)),
                    PathLineSegment((half_width, half_height)),
                    PathLineSegment((-half_width, half_height)),
                    PathLineSegment((-half_width, -half_height)),
                ),
                closed=True,
            ),
        )
    )
    return transform_native_path(
        local,
        PathAffineTransform.from_components(
            translate_x=center[0],
            translate_y=center[1],
        ),
    )


def _line_fragment(
    center: tuple[float, float],
    length: float,
    angle_deg: float,
) -> NativePathGeometry:
    radians = math.radians(angle_deg)
    dx = math.cos(radians) * length / 2.0
    dy = math.sin(radians) * length / 2.0
    return NativePathGeometry(
        (
            PathSubpath(
                (center[0] - dx, center[1] - dy),
                (PathLineSegment((center[0] + dx, center[1] + dy)),),
                closed=False,
            ),
        )
    )


def _combined_pivot(
    geometries: Sequence[NativePathGeometry],
) -> tuple[float, float]:
    bounds = [native_path_bounds(geometry) for geometry in geometries]
    return (
        (min(item[0] for item in bounds) + max(item[2] for item in bounds)) / 2.0,
        (min(item[1] for item in bounds) + max(item[3] for item in bounds)) / 2.0,
    )


def _compound_geometry(
    geometries: Sequence[NativePathGeometry],
) -> NativePathGeometry:
    return NativePathGeometry(
        tuple(
            subpath
            for geometry in geometries
            for subpath in geometry.subpaths
        ),
        fill_rule=PathFillRule.EVENODD,
    )


def _rotated_world_geometries(
    items: Sequence[tuple[str, NativePathGeometry]],
    angle_deg: float,
) -> list[tuple[str, NativePathGeometry]]:
    pivot = _combined_pivot([geometry for _name, geometry in items])
    rotation = trace_rotation_transform(angle_deg, pivot)
    return [
        (name, transform_native_path(geometry, rotation))
        for name, geometry in items
    ]


def _separate_artwork(
    items: Sequence[tuple[str, NativePathGeometry]],
    angle_deg: float,
    *,
    artwork_id: str = "label-artwork",
) -> list[TraceOrientationGeometry]:
    return [
        TraceOrientationGeometry(name, artwork_id, geometry)
        for name, geometry in _rotated_world_geometries(items, angle_deg)
    ]


def _combined_artwork(
    items: Sequence[tuple[str, NativePathGeometry]],
    angle_deg: float,
    *,
    object_id: str = "compound-label",
    artwork_id: str = "label-artwork",
) -> TraceOrientationGeometry:
    rotated = _rotated_world_geometries(items, angle_deg)
    return TraceOrientationGeometry(
        object_id,
        artwork_id,
        _compound_geometry([geometry for _name, geometry in rotated]),
    )


def _label_items(prefix: str = "label", x_offset: float = 0.0):
    return [
        (f"{prefix}-stem-1", _rectangle((x_offset + 0.0, 0.0), 1.5, 10.0)),
        (f"{prefix}-stem-2", _rectangle((x_offset + 8.0, 0.0), 1.5, 9.0)),
        (f"{prefix}-stem-3", _rectangle((x_offset + 16.0, 0.0), 1.5, 11.0)),
        (f"{prefix}-underline", _rectangle((x_offset + 8.0, -7.0), 27.0, 1.0)),
    ]


def _circle(center: tuple[float, float], radius: float) -> NativePathGeometry:
    handle = radius * 0.5522847498307936
    cx, cy = center
    return NativePathGeometry(
        (
            PathSubpath(
                (cx + radius, cy),
                (
                    PathCubicSegment(
                        (cx + radius, cy + handle),
                        (cx + handle, cy + radius),
                        (cx, cy + radius),
                    ),
                    PathCubicSegment(
                        (cx - handle, cy + radius),
                        (cx - radius, cy + handle),
                        (cx - radius, cy),
                    ),
                    PathCubicSegment(
                        (cx - radius, cy - handle),
                        (cx - handle, cy - radius),
                        (cx, cy - radius),
                    ),
                    PathCubicSegment(
                        (cx + handle, cy - radius),
                        (cx + radius, cy - handle),
                        (cx + radius, cy),
                    ),
                ),
                closed=True,
            ),
        )
    )


def _ellipse(
    center: tuple[float, float],
    width: float,
    height: float,
) -> NativePathGeometry:
    return transform_native_path(
        _circle((0.0, 0.0), 1.0),
        PathAffineTransform.from_components(
            scale_x=width / 2.0,
            scale_y=height / 2.0,
            translate_x=center[0],
            translate_y=center[1],
        ),
    )


def _curved_c(center: tuple[float, float]) -> NativePathGeometry:
    cx, cy = center
    return NativePathGeometry(
        (
            PathSubpath(
                (cx + 7.0, cy + 6.0),
                (
                    PathCubicSegment(
                        (cx + 1.0, cy + 10.0),
                        (cx - 8.0, cy + 7.0),
                        (cx - 8.0, cy),
                    ),
                    PathCubicSegment(
                        (cx - 8.0, cy - 7.0),
                        (cx + 1.0, cy - 10.0),
                        (cx + 7.0, cy - 6.0),
                    ),
                    PathLineSegment((cx + 5.0, cy - 3.5)),
                    PathCubicSegment(
                        (cx + 0.5, cy - 6.0),
                        (cx - 4.5, cy - 4.5),
                        (cx - 4.5, cy),
                    ),
                    PathCubicSegment(
                        (cx - 4.5, cy + 4.5),
                        (cx + 0.5, cy + 6.0),
                        (cx + 5.0, cy + 3.5),
                    ),
                    PathLineSegment((cx + 7.0, cy + 6.0)),
                ),
                closed=True,
            ),
        )
    )


def _near_linear_cubic_bar() -> NativePathGeometry:
    return NativePathGeometry(
        (
            PathSubpath(
                (-12.0, -0.8),
                (
                    PathCubicSegment((-4.0, -0.74), (4.0, -0.86), (12.0, -0.8)),
                    PathLineSegment((12.0, 0.8)),
                    PathCubicSegment((4.0, 0.86), (-4.0, 0.74), (-12.0, 0.8)),
                    PathLineSegment((-12.0, -0.8)),
                ),
                closed=True,
            ),
        )
    )


@pytest.mark.parametrize("angle_deg", [2.0, -3.0])
def test_compound_label_uses_disconnected_components(angle_deg: float) -> None:
    geometry = _combined_artwork(_label_items(), angle_deg)

    estimate = estimate_trace_orientation([geometry])

    assert estimate.offered
    assert estimate.selected_ids == ("compound-label",)
    assert estimate.detected_skew_deg == pytest.approx(angle_deg, abs=0.08)
    assert estimate.correction_deg == pytest.approx(-angle_deg, abs=0.08)
    assert estimate.confidence >= 0.78
    assert estimate.supporting_candidate_count >= 3
    assert estimate.component_axis_evidence_weight > 0.0
    assert estimate.component_alignment_evidence_weight > 0.0


def test_combined_and_separate_objects_in_one_artwork_are_equivalent() -> None:
    combined = estimate_trace_orientation([
        _combined_artwork(_label_items(), 2.0),
    ])
    separate = estimate_trace_orientation(
        _separate_artwork(_label_items(), 2.0)
    )

    assert combined.offered and separate.offered
    assert separate.detected_skew_deg == pytest.approx(
        combined.detected_skew_deg,
        abs=1e-12,
    )
    assert separate.correction_deg == pytest.approx(combined.correction_deg, abs=1e-12)
    assert separate.pivot_mm == pytest.approx(combined.pivot_mm, abs=1e-12)
    assert separate.confidence == pytest.approx(combined.confidence, abs=1e-12)
    assert separate.evidence_count == combined.evidence_count
    assert separate.evidence_families == combined.evidence_families


def test_curved_glyph_and_long_underline_form_one_reliable_artwork() -> None:
    items = [
        ("curved-c", _curved_c((0.0, 0.0))),
        ("underline", _rectangle((5.0, -9.0), 30.0, 1.0)),
    ]

    estimate = estimate_trace_orientation([
        _combined_artwork(items, 2.0, object_id="c-with-underline"),
    ])

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.15)
    assert {"line", "component_axis"}.issubset(estimate.evidence_families)


def test_curved_component_does_not_veto_strong_common_label_evidence() -> None:
    items = [*_label_items(), ("large-curved-c", _curved_c((29.0, 0.0)))]

    estimate = estimate_trace_orientation([
        _combined_artwork(items, 2.0, object_id="compound-with-c"),
    ])

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.15)


def test_many_small_stencil_fragments_do_not_destroy_strong_skew() -> None:
    fragments = [
        (
            f"fragment-{index}",
            _line_fragment(
                (
                    35.0 + 8.0 * math.cos(math.tau * index / 72.0),
                    2.0 + 8.0 * math.sin(math.tau * index / 72.0),
                ),
                0.9,
                -34.0 + (index * 19.0) % 68.0,
            ),
        )
        for index in range(72)
    ]
    geometry = _combined_artwork(
        [*_label_items(), *fragments],
        2.0,
        object_id="stencil-with-72-fragments",
    )

    estimate = estimate_trace_orientation([geometry])

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.2)
    assert estimate.evidence_count >= 72


def test_same_artwork_components_cannot_trigger_cross_artwork_veto() -> None:
    label = _separate_artwork(_label_items(), 2.0, artwork_id="one-batch")
    conflicting_fragment = _separate_artwork(
        [("diagonal-fragment", _rectangle((42.0, 0.0), 12.0, 1.0))],
        -5.0,
        artwork_id="one-batch",
    )

    estimate = estimate_trace_orientation([*label, *conflicting_fragment])

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.2)


def test_two_distinct_reliable_artworks_veto_the_offer() -> None:
    first = _combined_artwork(
        _label_items("first", 0.0),
        3.0,
        object_id="first-label",
        artwork_id="first-artwork",
    )
    second = _combined_artwork(
        _label_items("second", 55.0),
        -5.0,
        object_id="second-label",
        artwork_id="second-artwork",
    )

    combined = estimate_trace_orientation([first, second])
    first_only = estimate_trace_orientation([first])

    assert not combined.offered
    assert combined.suppression_reason == "conflicting_candidate_orientations"
    assert first_only.offered
    assert first_only.detected_skew_deg == pytest.approx(3.0, abs=0.1)


def test_smaller_distinct_label_is_not_laundered_by_larger_artwork() -> None:
    first = _combined_artwork(
        _label_items("large", 0.0),
        2.0,
        object_id="large-label",
        artwork_id="large-artwork",
    )
    smaller_items = [
        (
            name,
            transform_native_path(
                geometry,
                PathAffineTransform.from_components(
                    scale_x=0.5,
                    scale_y=0.5,
                    translate_x=55.0,
                ),
            ),
        )
        for name, geometry in _label_items("small", 0.0)
    ]
    second = _combined_artwork(
        smaller_items,
        -3.0,
        object_id="small-label",
        artwork_id="small-artwork",
    )

    estimate = estimate_trace_orientation([first, second])

    assert not estimate.offered
    assert estimate.suppression_reason == "conflicting_candidate_orientations"


def test_cubic_axis_cluster_is_a_distinct_artwork_conflict_witness() -> None:
    label = _combined_artwork(
        _label_items("label", 0.0),
        2.0,
        object_id="label",
        artwork_id="label-artwork",
    )
    ovals = _separate_artwork(
        [
            (f"oval-{index}", _ellipse((55.0 + index * 10.0, 0.0), 8.0, 3.0))
            for index in range(4)
        ],
        -3.0,
        artwork_id="oval-artwork",
    )

    ovals_only = estimate_trace_orientation(ovals)
    combined = estimate_trace_orientation([label, *ovals])

    assert not ovals_only.offered
    assert not combined.offered
    assert combined.suppression_reason == "conflicting_candidate_orientations"


def test_distinct_near_linear_cubic_artwork_vetoes_straight_evidence() -> None:
    label = _combined_artwork(
        _label_items("label", 0.0),
        2.0,
        object_id="label",
        artwork_id="label-artwork",
    )
    translated_bar = transform_native_path(
        _near_linear_cubic_bar(),
        PathAffineTransform.from_components(
            scale_x=1.5,
            scale_y=1.5,
            translate_x=55.0,
        ),
    )
    cubic_artwork = _combined_artwork(
        [("conflicting-cubic", translated_bar)],
        -5.0,
        object_id="conflicting-cubic",
        artwork_id="cubic-artwork",
    )

    estimate = estimate_trace_orientation([label, cubic_artwork])

    assert estimate.near_linear_cubic_evidence_weight > 0.0
    assert not estimate.offered
    assert estimate.suppression_reason == "conflicting_candidate_orientations"


def test_already_straight_compound_label_is_not_offered() -> None:
    estimate = estimate_trace_orientation([
        _combined_artwork(_label_items(), 0.1),
    ])

    assert not estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(0.1, abs=0.08)
    assert estimate.suppression_reason == "trivial_skew"


def test_circle_and_near_square_are_suppressed() -> None:
    circle = estimate_trace_orientation([
        TraceOrientationGeometry("circle", "circle-artwork", _circle((0.0, 0.0), 8.0)),
    ])
    square_geometry = transform_native_path(
        _rectangle((0.0, 0.0), 12.0, 11.2),
        trace_rotation_transform(2.0, (0.0, 0.0)),
    )
    square = estimate_trace_orientation([
        TraceOrientationGeometry("near-square", "square-artwork", square_geometry),
    ])

    assert not circle.offered
    assert circle.suppression_reason == "insufficient_orientation_evidence"
    assert not square.offered
    assert square.suppression_reason == "insufficient_independent_evidence_families"


def test_near_linear_cubic_contributes_as_evidence() -> None:
    items = [
        ("cubic-bar", _near_linear_cubic_bar()),
        ("stem", _rectangle((0.0, 6.0), 1.2, 9.0)),
    ]

    estimate = estimate_trace_orientation(
        _separate_artwork(items, 2.5)
    )

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.5, abs=0.1)
    assert estimate.near_linear_cubic_evidence_weight > 0.0


def test_group_pivot_uses_exact_combined_native_bounds() -> None:
    items = _separate_artwork(_label_items(), 2.0)
    expected = _combined_pivot([item.geometry for item in items])

    estimate = estimate_trace_orientation(items)

    assert estimate.pivot_mm == pytest.approx(expected, abs=1e-12)


def test_rigid_transform_preserves_lines_cubics_holes_islands_and_fill_rule() -> None:
    geometry = NativePathGeometry(
        (
            _rectangle((0.0, 0.0), 20.0, 12.0).subpaths[0],
            _circle((0.0, 0.0), 3.0).subpaths[0],
            _rectangle((0.0, 0.0), 1.0, 1.0).subpaths[0],
            _near_linear_cubic_bar().subpaths[0],
        ),
        fill_rule=PathFillRule.EVENODD,
    )

    transformed = transform_native_path(
        geometry,
        trace_rotation_transform(-2.0, (3.0, 4.0)),
    )

    assert transformed.fill_rule is PathFillRule.EVENODD
    assert transformed.path_version == geometry.path_version
    assert len(transformed.subpaths) == 4
    assert [subpath.closed for subpath in transformed.subpaths] == [
        True,
        True,
        True,
        True,
    ]
    assert [
        type(segment)
        for subpath in transformed.subpaths
        for segment in subpath.segments
    ] == [
        type(segment)
        for subpath in geometry.subpaths
        for segment in subpath.segments
    ]


def test_analysis_complexity_limit_precedes_component_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                tuple(
                    PathLineSegment((float(index), 0.0))
                    for index in range(1, MAX_TRACE_ORIENTATION_SEGMENTS + 2)
                ),
                closed=False,
            ),
        )
    )

    def unexpected_analysis(*_args, **_kwargs):
        raise AssertionError("oversized geometry must be rejected before analysis")

    monkeypatch.setattr(
        "laser_aligner.vision.trace_orientation._analyze_artwork",
        unexpected_analysis,
    )
    estimate = estimate_trace_orientation([
        TraceOrientationGeometry("oversized", "oversized-artwork", geometry),
    ])

    assert not estimate.offered
    assert estimate.suppression_reason == "analysis_complexity_limit"


def test_public_complexity_limits_are_stable_adapter_contract() -> None:
    assert MAX_TRACE_ORIENTATION_SEGMENTS == 20_000
    assert MAX_TRACE_ORIENTATION_SUBPATHS == 8_192


def test_large_stencil_analysis_remains_interactive() -> None:
    components = [
        (
            f"glyph-{index}",
            _rectangle(
                (float(index % 23) * 2.0, float(index // 23) * 5.0),
                1.2,
                3.0,
            ),
        )
        for index in range(92)
    ]

    estimate = estimate_trace_orientation([
        _combined_artwork(
            components,
            2.0,
            object_id="ninety-two-component-stencil",
        ),
    ])

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.1)
    assert estimate.elapsed_seconds < 0.5


def test_many_segment_consensus_uses_bounded_seed_work() -> None:
    segment_count = 4_000
    straight = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                tuple(
                    PathLineSegment((float(index * 3), 0.0))
                    for index in range(1, segment_count + 1)
                ),
                closed=False,
            ),
        )
    )
    rotated = transform_native_path(
        straight,
        trace_rotation_transform(2.0, (segment_count * 1.5, 0.0)),
    )

    estimate = estimate_trace_orientation([
        TraceOrientationGeometry("many-segments", "one-artwork", rotated),
    ])

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.08)
    assert estimate.evidence_count >= segment_count
    assert estimate.elapsed_seconds < 2.0


@pytest.mark.parametrize("angle_deg", [10.1, 12.0, 15.0])
def test_exceptionally_strong_label_can_offer_through_fifteen_degrees(
    angle_deg: float,
) -> None:
    estimate = estimate_trace_orientation([
        _combined_artwork(_label_items(), angle_deg),
    ])

    assert estimate.offered
    assert estimate.confidence >= 0.92
    assert estimate.inlier_fraction >= 0.90
    assert len(estimate.evidence_families) >= 3
    assert estimate.detected_skew_deg == pytest.approx(angle_deg, abs=0.08)


@pytest.mark.parametrize("angle_deg", [15.1, 18.0])
def test_offer_range_stops_above_fifteen_degrees(angle_deg: float) -> None:
    estimate = estimate_trace_orientation([
        _combined_artwork(_label_items(), angle_deg),
    ])

    assert not estimate.offered
    assert estimate.suppression_reason == "outside_skew_correction_range"
    assert math.isclose(estimate.correction_deg, -estimate.detected_skew_deg)


def test_public_geometry_contract_rejects_invalid_values() -> None:
    geometry = _rectangle((0.0, 0.0), 10.0, 2.0)

    with pytest.raises(ValueError, match="object_id"):
        TraceOrientationGeometry("", "artwork", geometry)
    with pytest.raises(ValueError, match="artwork_id"):
        TraceOrientationGeometry("object", "", geometry)
    with pytest.raises(TypeError, match="NativePathGeometry"):
        TraceOrientationGeometry("object", "artwork", object())  # type: ignore[arg-type]


def test_non_geometry_input_returns_conservative_no_offer() -> None:
    estimate = estimate_trace_orientation([object()])  # type: ignore[list-item]

    assert not estimate.offered
    assert estimate.suppression_reason == "invalid_world_geometry"
