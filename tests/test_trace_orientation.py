from __future__ import annotations

import math
from collections.abc import Sequence

import pytest

import laser_aligner.vision.trace_orientation as trace_orientation_module
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
    estimate_trace_orientation,
    trace_native_world_geometry,
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


def _normalize_detection(
    detection_id: str,
    geometry: NativePathGeometry,
) -> dict[str, object]:
    x_min, y_min, x_max, y_max = native_path_bounds(geometry)
    width = x_max - x_min
    height = y_max - y_min
    center = ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)
    normalized = transform_native_path(
        geometry,
        PathAffineTransform.from_components(
            scale_x=1.0 / width,
            scale_y=1.0 / height,
            translate_x=-center[0] / width,
            translate_y=-center[1] / height,
        ),
    )
    return {
        "id": detection_id,
        "index": 1,
        "source": "direct",
        "native_verified": True,
        "native_path": normalized.to_dict(),
        "native_center_mm": list(center),
        "native_width_mm": width,
        "native_height_mm": height,
        "center_mm": list(center),
        "diagnostics": {"native_fit_status": "verified"},
    }


def _combined_pivot(
    geometries: Sequence[NativePathGeometry],
) -> tuple[float, float]:
    combined = NativePathGeometry(
        tuple(subpath for geometry in geometries for subpath in geometry.subpaths)
    )
    x_min, y_min, x_max, y_max = native_path_bounds(combined)
    return (x_min + x_max) / 2.0, (y_min + y_max) / 2.0


def _rotated_detections(
    items: Sequence[tuple[str, NativePathGeometry]],
    angle_deg: float,
) -> list[dict[str, object]]:
    pivot = _combined_pivot([geometry for _name, geometry in items])
    rotation = trace_rotation_transform(angle_deg, pivot)
    return [
        _normalize_detection(name, transform_native_path(geometry, rotation))
        for name, geometry in items
    ]


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


def _capsule(center: tuple[float, float]) -> NativePathGeometry:
    local = NativePathGeometry(
        (
            PathSubpath(
                (-2.5, -0.5),
                (
                    PathLineSegment((2.5, -0.5)),
                    PathCubicSegment((2.78, -0.5), (2.78, 0.5), (2.5, 0.5)),
                    PathLineSegment((-2.5, 0.5)),
                    PathCubicSegment((-2.78, 0.5), (-2.78, -0.5), (-2.5, -0.5)),
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
def test_label_skew_sign_and_correction(angle_deg: float) -> None:
    detections = _rotated_detections(_label_items(), angle_deg)

    estimate = estimate_trace_orientation(detections)

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(angle_deg, abs=0.08)
    assert estimate.correction_deg == pytest.approx(-angle_deg, abs=0.08)
    assert estimate.confidence >= 0.78
    assert estimate.supporting_candidate_count >= 3


def test_already_straight_label_is_not_offered() -> None:
    estimate = estimate_trace_orientation(_rotated_detections(_label_items(), 0.1))

    assert not estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(0.1, abs=0.08)
    assert estimate.suppression_reason == "trivial_skew"


def test_underline_stems_and_component_alignment_are_independent_evidence() -> None:
    estimate = estimate_trace_orientation(_rotated_detections(_label_items(), 1.75))
    diagnostics = estimate.to_diagnostics()

    assert estimate.offered
    assert diagnostics["line_evidence_weight"] > 0.0
    assert diagnostics["component_axis_evidence_weight"] > 0.0
    assert diagnostics["component_alignment_evidence_weight"] > 0.0
    assert diagnostics["elapsed_estimation_seconds"] < 0.25


def test_vertical_candidate_row_retains_alignment_extent_before_modulo_reduction(
) -> None:
    items = [
        ("row-1", _rectangle((0.0, 0.0), 10.0, 1.5)),
        ("row-2", _rectangle((0.0, 8.0), 10.0, 1.5)),
        ("row-3", _rectangle((0.0, 16.0), 10.0, 1.5)),
    ]

    estimate = estimate_trace_orientation(_rotated_detections(items, 2.0))

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.08)
    assert estimate.component_alignment_evidence_weight > 0.0


def test_large_curved_c_does_not_overpower_straight_label_evidence() -> None:
    items = [*_label_items(), ("large-curved-c", _curved_c((29.0, 0.0)))]

    estimate = estimate_trace_orientation(_rotated_detections(items, 2.0))

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.15)


def test_near_linear_cubic_contributes_but_remains_cubic_after_rotation() -> None:
    detections = _rotated_detections(
        [
            ("cubic-bar", _near_linear_cubic_bar()),
            ("stem", _rectangle((0.0, 6.0), 1.2, 9.0)),
        ],
        2.5,
    )

    estimate = estimate_trace_orientation(detections)

    assert estimate.offered
    assert estimate.near_linear_cubic_evidence_weight > 0.0
    geometry = trace_native_world_geometry(detections[0])
    corrected = transform_native_path(
        geometry,
        trace_rotation_transform(estimate.correction_deg, estimate.pivot_mm),
    )
    assert any(
        isinstance(segment, PathCubicSegment)
        for subpath in corrected.subpaths
        for segment in subpath.segments
    )


def test_tiny_fragments_do_not_destabilize_label_estimate() -> None:
    label = _rotated_detections(_label_items(), 2.0)
    fragments = [
        _normalize_detection(
            f"fragment-{index}",
            transform_native_path(
                _rectangle((30.0 + index, 8.0 - index), 0.2, 0.1),
                trace_rotation_transform(17.0 * index, (30.0 + index, 8.0 - index)),
            ),
        )
        for index in range(5)
    ]

    estimate = estimate_trace_orientation([*label, *fragments])

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.15)


def test_many_above_threshold_stencil_fragments_do_not_dominate_label() -> None:
    label = _rotated_detections(_label_items(), 2.0)
    fragments = [
        _normalize_detection(
            f"meaningful-fragment-{index}",
            _line_fragment(
                (
                    35.0 + 4.0 * math.cos(math.tau * index / 24.0),
                    4.0 * math.sin(math.tau * index / 24.0),
                ),
                0.9,
                17.0,
            ),
        )
        for index in range(24)
    ]

    estimate = estimate_trace_orientation([*label, *fragments])

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.15)
    assert estimate.evidence_count >= 24


def test_conflicting_labels_suppress_combined_selection_but_not_one_label() -> None:
    first = _rotated_detections(_label_items("first", 0.0), 2.0)
    second = _rotated_detections(_label_items("second", 55.0), -3.0)

    combined = estimate_trace_orientation([*first, *second])
    first_only = estimate_trace_orientation(first)

    assert not combined.offered
    assert combined.suppression_reason in {
        "conflicting_candidate_orientations",
        "conflicting_orientation_evidence",
        "diffuse_orientation_evidence",
        "low_orientation_confidence",
    }
    assert first_only.offered
    assert first_only.detected_skew_deg == pytest.approx(2.0, abs=0.1)


def test_smaller_differently_rotated_label_is_not_laundered_by_group_alignment(
) -> None:
    first = _rotated_detections(_label_items("first", 0.0), 2.0)
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
        for name, geometry in _label_items("smaller", 0.0)
    ]
    second = _rotated_detections(smaller_items, -3.0)

    estimate = estimate_trace_orientation([*first, *second])

    assert not estimate.offered
    assert estimate.suppression_reason == "conflicting_candidate_orientations"


def test_near_square_group_cannot_hide_a_second_label_orientation() -> None:
    first = _rotated_detections(_label_items("first", 0.0), 2.0)
    square_group = _rotated_detections(
        [
            (f"square-{index}", _rectangle((55.0 + index * 6.0, 0.0), 4.0, 4.0))
            for index in range(4)
        ],
        -3.0,
    )

    combined = estimate_trace_orientation([*first, *square_group])
    squares_only = estimate_trace_orientation(square_group)

    assert not combined.offered
    assert combined.suppression_reason == "conflicting_candidate_orientations"
    assert squares_only.offered
    assert squares_only.detected_skew_deg == pytest.approx(-3.0, abs=0.1)


def test_two_line_axis_group_cannot_hide_a_second_label_orientation() -> None:
    first = _rotated_detections(_label_items("first", 0.0), 2.0)
    dashes = _rotated_detections(
        [
            (f"dash-{index}", _capsule((55.0 + index * 7.0, 0.0)))
            for index in range(4)
        ],
        -3.0,
    )

    combined = estimate_trace_orientation([*first, *dashes])
    dashes_only = estimate_trace_orientation(dashes)

    assert dashes_only.offered
    assert dashes_only.detected_skew_deg == pytest.approx(-3.0, abs=0.1)
    assert not combined.offered
    assert combined.suppression_reason == "conflicting_candidate_orientations"


def test_cubic_axis_cluster_is_a_conflict_only_orientation_witness() -> None:
    first = _rotated_detections(_label_items("first", 0.0), 2.0)
    ovals = _rotated_detections(
        [
            (f"oval-{index}", _ellipse((55.0 + index * 10.0, 0.0), 8.0, 3.0))
            for index in range(4)
        ],
        -3.0,
    )

    combined = estimate_trace_orientation([*first, *ovals])
    ovals_only = estimate_trace_orientation(ovals)

    assert not ovals_only.offered
    assert not combined.offered
    assert combined.suppression_reason == "conflicting_candidate_orientations"


def test_circle_near_square_and_ambiguous_character_are_suppressed() -> None:
    circle = estimate_trace_orientation(
        [_normalize_detection("circle", _circle((0.0, 0.0), 8.0))]
    )
    square_geometry = transform_native_path(
        _rectangle((0.0, 0.0), 12.0, 11.2),
        trace_rotation_transform(2.0, (0.0, 0.0)),
    )
    square = estimate_trace_orientation(
        [_normalize_detection("near-square", square_geometry)]
    )
    triangle_geometry = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 8.0),
                (
                    PathLineSegment((-6.0, -5.0)),
                    PathLineSegment((6.0, -5.0)),
                    PathLineSegment((0.0, 8.0)),
                ),
                closed=True,
            ),
        )
    )
    triangle = estimate_trace_orientation(
        [_normalize_detection("ambiguous-character", triangle_geometry)]
    )

    assert not circle.offered
    assert circle.suppression_reason == "insufficient_orientation_evidence"
    assert not square.offered
    assert square.suppression_reason == "insufficient_independent_evidence_families"
    assert not triangle.offered


def test_conflicting_near_linear_cubic_cannot_override_straight_evidence() -> None:
    label = _rotated_detections(_label_items(), 2.0)
    bar = transform_native_path(
        _near_linear_cubic_bar(),
        PathAffineTransform.from_components(
            scale_x=1.5,
            scale_y=1.5,
            translate_x=55.0,
        ),
    )
    bar = transform_native_path(
        bar,
        trace_rotation_transform(-5.0, (55.0, 0.0)),
    )

    estimate = estimate_trace_orientation(
        [*label, _normalize_detection("conflicting-cubic", bar)]
    )

    assert estimate.near_linear_cubic_evidence_weight > 0.0
    assert not estimate.offered
    assert estimate.suppression_reason == "conflicting_candidate_orientations"


def test_failed_or_partial_native_fit_never_produces_an_estimate() -> None:
    detection = _normalize_detection("failed", _rectangle((0.0, 0.0), 20.0, 4.0))
    detection["native_verified"] = False

    estimate = estimate_trace_orientation([detection])

    assert not estimate.offered
    assert estimate.detected_skew_deg is None
    assert estimate.suppression_reason == "non_authoritative_native_geometry"


def test_group_pivot_uses_combined_exact_native_bounds() -> None:
    detections = _rotated_detections(_label_items(), 2.0)
    geometries = [trace_native_world_geometry(item) for item in detections]
    expected = _combined_pivot(geometries)

    estimate = estimate_trace_orientation(detections)

    assert estimate.pivot_mm == pytest.approx(expected, abs=1e-12)


def test_rigid_transform_preserves_lines_cubics_holes_islands_and_fill_rule() -> None:
    geometry = NativePathGeometry(
        (
            _rectangle((0.0, 0.0), 20.0, 12.0).subpaths[0],
            _circle((0.0, 0.0), 3.0).subpaths[0],
            _rectangle((0.0, 0.0), 1.0, 1.0).subpaths[0],
        ),
        fill_rule=PathFillRule.EVENODD,
    )

    transformed = transform_native_path(
        geometry,
        trace_rotation_transform(-2.0, (3.0, 4.0)),
    )

    assert transformed.fill_rule is PathFillRule.EVENODD
    assert transformed.path_version == geometry.path_version
    assert len(transformed.subpaths) == 3
    assert [subpath.closed for subpath in transformed.subpaths] == [True, True, True]
    assert [
        type(segment)
        for subpath in transformed.subpaths
        for segment in subpath.segments
    ] == [
        type(segment)
        for subpath in geometry.subpaths
        for segment in subpath.segments
    ]


def test_strategy_metadata_does_not_affect_successful_geometry_estimate() -> None:
    detections = _rotated_detections(_label_items(), 2.0)
    estimates = []
    for strategy in ("auto", "manual", "color"):
        strategy_detections = [dict(item, strategy=strategy) for item in detections]
        estimates.append(estimate_trace_orientation(strategy_detections))

    assert all(estimate.offered for estimate in estimates)
    assert [estimate.detected_skew_deg for estimate in estimates] == pytest.approx(
        [2.0, 2.0, 2.0],
        abs=0.08,
    )


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

    estimate = estimate_trace_orientation(
        [_normalize_detection("many-segments", rotated)]
    )

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.08)
    assert estimate.evidence_count >= segment_count
    # This generous ceiling guards against accidentally restoring an O(n²)
    # seed scan while avoiding an ordinary-millisecond performance assertion.
    assert estimate.elapsed_seconds < 2.0


def test_many_candidate_conflict_checks_remain_bounded() -> None:
    rotated = transform_native_path(
        _rectangle((0.0, 0.0), 10.0, 1.0),
        trace_rotation_transform(2.0, (0.0, 0.0)),
    )
    prototype = _normalize_detection("prototype", rotated)
    detections = [
        dict(prototype, id=f"repeated-{index}")
        for index in range(2_000)
    ]

    estimate = estimate_trace_orientation(detections)

    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(2.0, abs=0.08)
    # A deliberately broad ceiling detects accidental all-pairs/window rescans
    # while allowing substantial variance on loaded Windows CI hosts.
    assert estimate.elapsed_seconds < 3.0


def test_combined_subpath_limit_returns_no_offer_without_combining_geometry() -> None:
    path = NativePathGeometry(
        (
            PathSubpath(
                (-0.5, -0.5),
                (PathLineSegment((0.5, 0.5)),),
                closed=False,
            ),
        )
    ).to_dict()
    detections = [
        {
            "id": f"single-segment-{index}",
            "source": "direct",
            "native_verified": True,
            "native_path": path,
            "native_center_mm": [0.0, 0.0],
            "native_width_mm": 2.0,
            "native_height_mm": 2.0,
        }
        for index in range(8_193)
    ]

    estimate = estimate_trace_orientation(detections)

    assert not estimate.offered
    assert estimate.suppression_reason == "analysis_complexity_limit"


def test_raw_complexity_limit_precedes_native_parse_and_transform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detection = {
        "id": "oversized-native-path",
        "source": "direct",
        "native_verified": True,
        "native_path": {
            "subpaths": [
                {
                    "segments": [{}] * 20_001,
                }
            ]
        },
        "native_center_mm": [0.0, 0.0],
        "native_width_mm": 2.0,
        "native_height_mm": 2.0,
    }

    def unexpected_parse(_detection):
        raise AssertionError("oversized paths must be rejected before native parsing")

    monkeypatch.setattr(
        trace_orientation_module,
        "trace_native_world_geometry",
        unexpected_parse,
    )

    estimate = estimate_trace_orientation([detection])

    assert not estimate.offered
    assert estimate.suppression_reason == "analysis_complexity_limit"


@pytest.mark.parametrize("angle_deg", [10.1, 12.0, 15.0])
def test_exceptionally_strong_label_can_offer_through_fifteen_degrees(
    angle_deg: float,
) -> None:
    estimate = estimate_trace_orientation(
        _rotated_detections(_label_items(), angle_deg)
    )

    assert estimate.offered
    assert estimate.confidence >= 0.92
    assert estimate.inlier_fraction >= 0.90
    assert len(estimate.evidence_families) >= 3
    assert estimate.detected_skew_deg == pytest.approx(angle_deg, abs=0.08)


def test_offer_range_stops_above_fifteen_degrees() -> None:
    estimate = estimate_trace_orientation(_rotated_detections(_label_items(), 15.1))

    assert not estimate.offered
    assert estimate.suppression_reason == "outside_skew_correction_range"


def test_large_skew_is_suppressed_outside_intended_range() -> None:
    estimate = estimate_trace_orientation(_rotated_detections(_label_items(), 18.0))

    assert not estimate.offered
    assert estimate.suppression_reason == "outside_skew_correction_range"
    assert math.isclose(estimate.correction_deg, -estimate.detected_skew_deg)
