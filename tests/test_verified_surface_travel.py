from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from laser_aligner.errors import SafetyError
from laser_aligner.geometry.polygon import convex_polygon_contains_normalized
from laser_aligner.raster_safety import (
    COORDINATE_REFERENCE,
    PREFIX,
    RasterBoundary,
    RasterTravelEnvelope,
    VerifiedSurfaceTravel,
    parse_raster_boundary,
    parse_raster_travel_envelope,
    raster_boundary_directive,
    require_verified_raster_travel,
)

FRAME_DIGEST = "a" * 64
OTHER_DIGEST = "b" * 64
RECTANGLE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
SURFACE = ((8.0, 0.0), (18.0, 0.0), (18.0, 10.0), (8.0, 10.0))
LEGACY_ENVELOPE = {
    "x_min": 0.0,
    "x_max": 10.0,
    "y_min": 0.0,
    "y_max": 10.0,
    "verified": True,
    "controller": "test controller",
    "firmware": "test firmware",
    "configuration": "test configuration digest",
    "reference": COORDINATE_REFERENCE,
    "tested_at": "2026-10-15",
}
LEGACY_DIGEST = "58b1c0de8c7313b8214d7d97cf5e3ae4206dadf549b1283ee69bc88a6c1bd677"


def surface_record(**changes):
    return replace(VerifiedSurfaceTravel(
        beam_polygon_mm=SURFACE,
        spot_offset_mm=(0.0, 0.0),
        coordinate_frame_digest=FRAME_DIGEST,
        verified=True,
        confirmed_at="2026-10-16",
        evidence="Operator confirmed the complete surface travel and cable clearance.",
    ), **changes)


def envelope(*, surface=None, **changes):
    return replace(RasterTravelEnvelope(**LEGACY_ENVELOPE, surface=surface), **changes)


def boundary_for(travel):
    surface = travel.surface
    if surface is None:
        return RasterBoundary(RECTANGLE, travel.bounds_mm, travel.digest)
    return RasterBoundary(
        surface.beam_polygon_mm,
        travel.bounds_mm,
        travel.digest,
        travel_surface_polygon_mm=surface.carriage_polygon_mm,
        surface_travel_digest=surface.digest,
    )


def encode_boundary(payload):
    return PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def test_legacy_envelope_bytes_digest_and_schema_one_directive_are_unchanged():
    travel = envelope()
    assert travel.to_dict() == LEGACY_ENVELOPE
    assert travel.digest == LEGACY_DIGEST
    assert parse_raster_travel_envelope(LEGACY_ENVELOPE) == travel
    assert parse_raster_travel_envelope(travel) == travel
    expected = encode_boundary({
        "schema": 1,
        "engraving_polygon_mm": RECTANGLE,
        "travel_bounds_mm": (0.0, 10.0, 0.0, 10.0),
        "travel_envelope_digest": LEGACY_DIGEST,
    })
    assert raster_boundary_directive(RECTANGLE, travel) == expected
    boundary = parse_raster_boundary(expected)
    assert boundary.directive == expected
    assert boundary.travel_surface_polygon_mm is None
    assert boundary.surface_travel_digest is None
    boundary.require_matches_envelope(travel, (0.0, 0.0))


def test_surface_record_and_envelope_roundtrip_do_not_duplicate_controller_evidence():
    surface = surface_record()
    payload = surface.to_dict()
    assert set(payload) == {
        "beam_polygon_mm", "spot_offset_mm", "coordinate_frame_digest",
        "verified", "confirmed_at", "evidence",
    }
    assert VerifiedSurfaceTravel.from_dict(json.loads(json.dumps(payload))) == surface
    travel = envelope(surface=surface)
    assert set(travel.to_dict()) == set(LEGACY_ENVELOPE) | {"surface"}
    assert travel.to_dict()["surface"] == payload
    assert parse_raster_travel_envelope(json.loads(json.dumps(travel.to_dict()))) == travel
    assert parse_raster_travel_envelope(travel) == travel
    assert travel.digest != LEGACY_DIGEST


@pytest.mark.parametrize("field,value", [
    ("verified", False),
    ("confirmed_at", "2026-10-15"),
    ("evidence", "New physical verification record."),
    ("coordinate_frame_digest", OTHER_DIGEST),
    ("spot_offset_mm", (1.0, -2.0)),
    ("beam_polygon_mm", ((8.1, 0), (18.1, 0), (18.1, 10), (8.1, 10))),
])
def test_every_surface_evidence_change_invalidates_surface_and_envelope_digests(field, value):
    original = surface_record()
    changed = replace(original, **{field: value})
    assert changed.digest != original.digest
    assert envelope(surface=changed).digest != envelope(surface=original).digest


@pytest.mark.parametrize("field,value", [
    ("controller", "different controller"),
    ("firmware", "different firmware"),
    ("configuration", "different configuration"),
    ("tested_at", "2026-10-16"),
])
def test_surface_inherits_envelope_controller_binding(field, value):
    travel = envelope(surface=surface_record())
    changed = replace(travel, **{field: value})
    assert changed.surface.digest == travel.surface.digest
    assert changed.digest != travel.digest
    with pytest.raises(SafetyError):
        boundary_for(travel).require_matches_envelope(changed, (0.0, 0.0))


def test_arbitrary_rotation_and_nonzero_beam_offset_preserve_exact_carriage_polygon():
    angle = math.radians(23)
    cosine, sine = math.cos(angle), math.sin(angle)
    polygon = tuple(
        (15 + cosine * x - sine * y, 6 + sine * x + cosine * y)
        for x, y in ((0, 0), (4, 0), (4, 6), (0, 6))
    )
    offset = (3.0, -2.0)
    surface = surface_record(beam_polygon_mm=polygon, spot_offset_mm=offset)
    expected = tuple((x - offset[0], y - offset[1]) for x, y in polygon)
    assert surface.carriage_polygon_mm == expected
    surface.require_match(polygon, offset, FRAME_DIGEST)
    travel = envelope(surface=surface)
    boundary = boundary_for(travel)
    boundary.require_matches_envelope(travel, offset)
    for point in expected:
        travel.require_travel_point(point, label="Rotated surface corner")
    travel.require_travel_segment(expected[0], expected[2], label="Rotated surface diagonal")
    with pytest.raises(SafetyError):
        travel.require_travel_point(polygon[2], label="Beam coordinate used as carriage")
    outside = (max(x for x, _ in expected), min(y for _, y in expected))
    with pytest.raises(SafetyError):
        travel.require_travel_point(outside, label="Unverified bounding-box corner")


def test_surface_matching_normalizes_winding_but_does_not_rotate_corner_identity():
    surface = surface_record()
    surface.require_match(SURFACE, (0, 0), FRAME_DIGEST)
    surface.require_match(tuple(reversed(SURFACE)), (0, 0), FRAME_DIGEST)
    assert surface_record(beam_polygon_mm=tuple(reversed(SURFACE))) == surface
    with pytest.raises(SafetyError):
        surface.require_match(SURFACE[1:] + SURFACE[:1], (0, 0), FRAME_DIGEST)


@pytest.mark.parametrize("polygon,offset,digest", [
    (((8.00001, 0), (18.00001, 0), (18.00001, 10), (8.00001, 10)), (0, 0), FRAME_DIGEST),
    (((8.000000001, 0), (18.000000001, 0), (18.000000001, 10), (8.000000001, 10)), (0, 0), FRAME_DIGEST),
    (SURFACE, (0.00001, 0), FRAME_DIGEST),
    (SURFACE, (0, -0.00001), FRAME_DIGEST),
    (SURFACE, (0.000000001, 0), FRAME_DIGEST),
    (SURFACE, (0, 0), OTHER_DIGEST),
])
def test_surface_matching_rejects_stale_geometry_offset_or_coordinate_frame(polygon, offset, digest):
    with pytest.raises(SafetyError):
        surface_record().require_match(polygon, offset, digest)


def test_frame_digest_can_be_omitted_only_when_the_caller_does_not_supply_it():
    surface = surface_record()
    surface.require_match(SURFACE, (0, 0))
    with pytest.raises(SafetyError):
        surface.require_match(SURFACE, (0, 0), "")


@pytest.mark.parametrize("field,value", [
    ("verified", 1), ("verified", "true"), ("verified", None),
    ("coordinate_frame_digest", "A" * 64), ("coordinate_frame_digest", "a" * 63),
    ("coordinate_frame_digest", "z" * 64), ("coordinate_frame_digest", None),
    ("confirmed_at", "2026-02-30"), ("confirmed_at", "20261016"),
    ("confirmed_at", "2026-10-16T00:00:00Z"), ("confirmed_at", ""),
    ("evidence", " "), ("evidence", []),
    ("spot_offset_mm", (True, 0)), ("spot_offset_mm", ("0", 0)),
    ("spot_offset_mm", (float("nan"), 0)), ("spot_offset_mm", (0, float("inf"))),
    ("spot_offset_mm", (0,)), ("spot_offset_mm", (0, 0, 0)),
    ("beam_polygon_mm", ((0, 0), (10, 0), (0, 10))),
    ("beam_polygon_mm", ((0, 0), (10, 0), (10, 10), (0, 10), (-1, 5))),
    ("beam_polygon_mm", ((0, 0), (10, 0), (3, 3), (0, 10))),
    ("beam_polygon_mm", ((0, 0), (10, 10), (10, 0), (0, 10))),
    ("beam_polygon_mm", ((0, 0), (10, 0), (10, 0), (0, 10))),
    ("beam_polygon_mm", ((False, 0), (10, 0), (10, 10), (0, 10))),
    ("beam_polygon_mm", ((0, 0), (float("inf"), 0), (10, 10), (0, 10))),
])
def test_surface_record_rejects_malformed_values_in_constructor_and_json(field, value):
    with pytest.raises((TypeError, ValueError)):
        surface_record(**{field: value})
    payload = surface_record().to_dict()
    payload[field] = value
    with pytest.raises((TypeError, ValueError)):
        VerifiedSurfaceTravel.from_dict(payload)


@pytest.mark.parametrize("field", [
    "beam_polygon_mm", "spot_offset_mm", "coordinate_frame_digest",
    "verified", "confirmed_at", "evidence",
])
def test_surface_record_requires_every_field(field):
    payload = surface_record().to_dict()
    del payload[field]
    with pytest.raises((TypeError, ValueError)):
        VerifiedSurfaceTravel.from_dict(payload)


@pytest.mark.parametrize("extra", ["controller", "firmware", "schema", "unknown"])
def test_surface_record_rejects_unknown_or_duplicate_parent_metadata(extra):
    payload = surface_record().to_dict()
    payload[extra] = "unexpected"
    with pytest.raises((TypeError, ValueError)):
        VerifiedSurfaceTravel.from_dict(payload)


@pytest.mark.parametrize("payload", [None, [], True, "surface"])
def test_surface_record_requires_an_object(payload):
    with pytest.raises((TypeError, ValueError)):
        VerifiedSurfaceTravel.from_dict(payload)


@pytest.mark.parametrize("base_verified,surface_verified", [(False, True), (True, False), (False, False)])
def test_all_guarded_uses_require_both_verification_records(base_verified, surface_verified):
    travel = envelope(surface=surface_record(verified=surface_verified), verified=base_verified)
    with pytest.raises(SafetyError):
        require_verified_raster_travel(travel)
    with pytest.raises(SafetyError):
        travel.require_travel_point((5, 5), label="Point inside base rectangle")
    with pytest.raises(SafetyError):
        travel.require_travel_segment((5, 5), (6, 5), label="Segment inside base rectangle")
    with pytest.raises(SafetyError):
        travel.travel_route((5, 5), (6, 5), label="Route inside base rectangle")
    with pytest.raises(SafetyError):
        boundary_for(travel).require_matches_envelope(travel, (0, 0))


def test_unverified_surface_cannot_match_even_when_geometry_is_identical():
    with pytest.raises(SafetyError):
        surface_record(verified=False).require_match(SURFACE, (0, 0), FRAME_DIGEST)


def test_schema_two_roundtrip_contains_only_the_two_additional_surface_fields():
    travel = envelope(surface=surface_record())
    original = boundary_for(travel)
    payload = json.loads(original.directive[len(PREFIX):])
    assert payload["schema"] == 2
    assert set(payload) == {
        "schema", "engraving_polygon_mm", "travel_bounds_mm", "travel_envelope_digest",
        "travel_surface_polygon_mm", "surface_travel_digest",
    }
    parsed = parse_raster_boundary(original.directive)
    assert parsed == original
    assert parsed.directive == original.directive
    parsed.require_matches_envelope(travel, (0, 0))


def test_directive_generation_checks_current_surface_offset_and_frame_when_supplied():
    travel = envelope(surface=surface_record())
    expected = boundary_for(travel).directive
    assert raster_boundary_directive(SURFACE, travel) == expected
    assert raster_boundary_directive(
        SURFACE, travel, spot_offset_mm=(0, 0), coordinate_frame_digest=FRAME_DIGEST,
    ) == expected
    with pytest.raises(SafetyError):
        raster_boundary_directive(SURFACE, travel, spot_offset_mm=(0.000000001, 0))
    with pytest.raises(SafetyError):
        raster_boundary_directive(SURFACE, travel, coordinate_frame_digest=OTHER_DIGEST)
    with pytest.raises(SafetyError):
        raster_boundary_directive(RECTANGLE, travel)


@pytest.mark.parametrize("schema", [True, False, 1.0, 2.0, "2", 0, 3, None])
def test_boundary_schema_is_a_supported_exact_integer(schema):
    payload = json.loads(boundary_for(envelope(surface=surface_record())).directive[len(PREFIX):])
    payload["schema"] = schema
    with pytest.raises(SafetyError):
        parse_raster_boundary(encode_boundary(payload))


@pytest.mark.parametrize("field", ["travel_surface_polygon_mm", "surface_travel_digest"])
def test_schema_two_requires_both_surface_fields_and_schema_one_rejects_either(field):
    payload = json.loads(boundary_for(envelope(surface=surface_record())).directive[len(PREFIX):])
    del payload[field]
    with pytest.raises(SafetyError):
        parse_raster_boundary(encode_boundary(payload))
    payload["schema"] = 1
    with pytest.raises(SafetyError):
        parse_raster_boundary(encode_boundary(payload))


@pytest.mark.parametrize("field,value", [
    ("travel_surface_polygon_mm", None),
    ("travel_surface_polygon_mm", [[0, 0], [1, 0], [0, 1]]),
    ("travel_surface_polygon_mm", [[0, 0], [1, 1], [1, 0], [0, 1]]),
    ("surface_travel_digest", None), ("surface_travel_digest", "A" * 64),
    ("surface_travel_digest", "bad"), ("unknown", True),
])
def test_schema_two_rejects_malformed_surface_fields(field, value):
    payload = json.loads(boundary_for(envelope(surface=surface_record())).directive[len(PREFIX):])
    payload[field] = value
    with pytest.raises(SafetyError):
        parse_raster_boundary(encode_boundary(payload))


@pytest.mark.parametrize("changes", [
    {"travel_bounds_mm": (0, 10.000000001, 0, 10)},
    {"travel_envelope_digest": OTHER_DIGEST},
    {"surface_travel_digest": OTHER_DIGEST},
    {"travel_surface_polygon_mm": ((8.1, 0), (18.1, 0), (18.1, 10), (8.1, 10))},
    {"engraving_polygon_mm": ((8.1, 0), (18.1, 0), (18.1, 10), (8.1, 10))},
])
def test_boundary_cannot_change_geometry_or_evidence_binding(changes):
    travel = envelope(surface=surface_record())
    with pytest.raises(SafetyError):
        replace(boundary_for(travel), **changes).require_matches_envelope(travel, (0, 0))


def test_boundary_cannot_drop_or_add_surface_authority():
    base = envelope()
    extended = envelope(surface=surface_record())
    with pytest.raises(SafetyError):
        boundary_for(base).require_matches_envelope(extended, (0, 0))
    with pytest.raises(SafetyError):
        boundary_for(extended).require_matches_envelope(base, (0, 0))
    with pytest.raises(SafetyError):
        boundary_for(extended).require_matches_envelope(extended, (1, 0))


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("surface_polygon,start,end", [
    (((20, 0), (30, 0), (30, 10), (20, 10)), (5, 5), (25, 5)),
    (((8, 8), (18, 8), (18, 18), (8, 18)), (1, 9), (9, 17)),
    (((10.000003, 0), (20, 0), (20, 10), (10.000003, 10)), (5, 5), (15, 5)),
])
def test_allowed_endpoints_do_not_authorize_a_segment_through_an_unverified_gap(
    surface_polygon, start, end, reverse,
):
    travel = envelope(surface=surface_record(beam_polygon_mm=surface_polygon))
    if reverse:
        start, end = end, start
    travel.require_travel_point(start, label="Allowed start")
    travel.require_travel_point(end, label="Allowed end")
    with pytest.raises(SafetyError):
        travel.require_travel_segment(start, end, label="Transfer through unverified gap")


@pytest.mark.parametrize("surface_polygon,start,end", [
    (SURFACE, (5, 5), (15, 5)),
    (((10, 0), (20, 0), (20, 10), (10, 10)), (5, 5), (15, 5)),
    (((10, 10), (20, 10), (20, 20), (10, 20)), (5, 5), (15, 15)),
    (((8, 8), (18, 8), (18, 18), (8, 18)), (5, 5), (15, 15)),
    (SURFACE, (0, 0), (18, 0)),
    (SURFACE, (0, 10), (18, 10)),
    (SURFACE, (18, 10), (18, 10)),
])
def test_intersecting_or_touching_authorized_regions_cover_the_complete_segment(surface_polygon, start, end):
    travel = envelope(surface=surface_record(beam_polygon_mm=surface_polygon))
    travel.require_travel_segment(start, end, label="Continuous verified transfer")
    travel.require_travel_segment(end, start, label="Reverse continuous verified transfer")


@pytest.mark.parametrize("with_surface", [False, True])
def test_rectangle_limits_are_exact_without_borrowing_polygon_tolerance(with_surface):
    travel = envelope(surface=surface_record() if with_surface else None)
    travel.require_travel_point((0, 5), label="Exact rectangle boundary")
    travel.require_travel_segment((0, 0), (0, 10), label="Rectangle edge")
    with pytest.raises(SafetyError):
        travel.require_travel_point((-5e-10, 5), label="Outside rectangle only")
    with pytest.raises(SafetyError):
        travel.require_travel_segment((-5e-10, 5), (1, 5), label="Outside rectangle start")


def test_surface_containment_uses_existing_one_e_minus_six_mm_tolerance():
    travel = envelope(surface=surface_record())
    travel.require_travel_point((18 + 0.5e-6, 5), label="Within surface numeric tolerance")
    travel.require_travel_segment((18 + 0.5e-6, 1), (18 + 0.5e-6, 9), label="Surface edge tolerance")
    with pytest.raises(SafetyError):
        travel.require_travel_point((18 + 2e-6, 5), label="Outside surface tolerance")
    with pytest.raises(SafetyError):
        travel.require_travel_segment((18 + 2e-6, 1), (18 + 2e-6, 9), label="Outside surface edge")


@pytest.mark.parametrize("point", [(float("nan"), 5), (5, float("inf")), (-float("inf"), 5)])
def test_nonfinite_points_cannot_pass_point_or_segment_validation(point):
    travel = envelope(surface=surface_record())
    with pytest.raises(SafetyError):
        travel.require_travel_point(point, label="Nonfinite point")
    with pytest.raises(SafetyError):
        travel.require_travel_segment(point, (5, 5), label="Nonfinite segment start")
    with pytest.raises(SafetyError):
        travel.require_travel_segment((5, 5), point, label="Nonfinite segment end")


def route_authority(travel, use_boundary):
    return boundary_for(travel) if use_boundary else travel


def assert_two_segment_route(travel, authority, start, end):
    route = authority.travel_route(start, end, label="Verified routed transfer")
    assert type(route) is tuple
    assert len(route) == 2
    waypoint, destination = route
    assert destination == end
    assert waypoint != start
    assert waypoint == tuple(round(coordinate, 3) for coordinate in waypoint)
    assert travel.contains(*waypoint)
    assert convex_polygon_contains_normalized(waypoint, travel.surface.carriage_polygon_mm)
    authority.require_travel_segment(start, waypoint, label="Route to common region")
    authority.require_travel_segment(waypoint, destination, label="Route from common region")
    assert authority.travel_route(start, end, label="Repeated routed transfer") == route
    return route


@pytest.mark.parametrize("use_boundary", [False, True])
@pytest.mark.parametrize("with_surface", [False, True])
def test_direct_route_preserves_destination_without_inserting_or_rounding_waypoints(use_boundary, with_surface):
    travel = envelope(surface=surface_record() if with_surface else None)
    authority = route_authority(travel, use_boundary)
    start, end = (1.0002, 2.3333), (9.1234, 8.5678)
    assert authority.travel_route(start, end, label="Direct rectangle transfer") == (end,)


@pytest.mark.parametrize("use_boundary", [False, True])
def test_direct_route_across_overlapping_regions_needs_no_intermediate_point(use_boundary):
    travel = envelope(surface=surface_record())
    authority = route_authority(travel, use_boundary)
    end = (15, 5)
    assert authority.travel_route((5, 5), end, label="Direct continuous union transfer") == (end,)


@pytest.mark.parametrize("use_boundary", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_route_avoids_nonconvex_union_gap_using_rounded_common_waypoint(use_boundary, reverse):
    travel = envelope(surface=surface_record(beam_polygon_mm=(
        (8, 8), (18, 8), (18, 18), (8, 18),
    )))
    authority = route_authority(travel, use_boundary)
    start, end = (1, 9), (9, 17)
    if reverse:
        start, end = end, start
    with pytest.raises(SafetyError):
        authority.require_travel_segment(start, end, label="Direct transfer crosses gap")
    assert_two_segment_route(travel, authority, start, end)


@pytest.mark.parametrize("use_boundary", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_route_between_disconnected_regions_is_rejected(use_boundary, reverse):
    travel = envelope(surface=surface_record(beam_polygon_mm=(
        (20, 0), (30, 0), (30, 10), (20, 10),
    )))
    authority = route_authority(travel, use_boundary)
    start, end = (5, 5), (25, 5)
    if reverse:
        start, end = end, start
    authority.require_travel_point(start, label="Allowed disconnected start")
    authority.require_travel_point(end, label="Allowed disconnected end")
    with pytest.raises(SafetyError):
        authority.travel_route(start, end, label="Disconnected travel regions")


@pytest.mark.parametrize("use_boundary", [False, True])
def test_route_uses_carriage_intersection_for_rotated_surface_and_nonzero_spot_offset(use_boundary):
    angle = math.radians(23)
    cosine, sine = math.cos(angle), math.sin(angle)
    carriage_polygon = tuple(
        (8 + cosine * x - sine * y, 8 + sine * x + cosine * y)
        for x, y in ((0, 0), (10, 0), (10, 10), (0, 10))
    )
    offset = (2.0, -3.0)
    beam_polygon = tuple((x + offset[0], y + offset[1]) for x, y in carriage_polygon)
    travel = envelope(surface=surface_record(beam_polygon_mm=beam_polygon, spot_offset_mm=offset))
    authority = route_authority(travel, use_boundary)
    start = (1, 9)
    end = tuple(sum(point[axis] for point in carriage_polygon) / 4 for axis in (0, 1))
    with pytest.raises(SafetyError):
        authority.require_travel_segment(start, end, label="Rotated direct transfer crosses gap")
    assert_two_segment_route(travel, authority, start, end)


@pytest.mark.parametrize("use_boundary", [False, True])
def test_route_can_use_exactly_touching_common_corner(use_boundary):
    travel = envelope(surface=surface_record(beam_polygon_mm=(
        (10, 10), (20, 10), (20, 20), (10, 20),
    )))
    authority = route_authority(travel, use_boundary)
    start, end = (1, 9), (11, 19)
    with pytest.raises(SafetyError):
        authority.require_travel_segment(start, end, label="Direct transfer misses shared corner")
    route = assert_two_segment_route(travel, authority, start, end)
    assert route[0] == (10, 10)


@pytest.mark.parametrize("use_boundary", [False, True])
def test_route_rejects_narrow_intersection_without_any_representable_common_waypoint(use_boundary):
    # The common cap is x=5.0003..5.0005 and y=9.9999..10. No 0.001 mm
    # coordinate pair fits, even with the existing 1e-6 mm surface tolerance.
    travel = envelope(surface=surface_record(beam_polygon_mm=(
        (5.0004, 9.9999), (6.0004, 10.9999),
        (5.0004, 11.9999), (4.0004, 10.9999),
    )))
    authority = route_authority(travel, use_boundary)
    start, end = (1, 9.9), (5.0004, 10.9999)
    authority.require_travel_point(start, label="Allowed narrow-cap start")
    authority.require_travel_point(end, label="Allowed narrow-cap end")
    for rounded_point in ((5.000, 10.000), (5.001, 10.000)):
        assert not convex_polygon_contains_normalized(rounded_point, travel.surface.carriage_polygon_mm)
    with pytest.raises(SafetyError):
        authority.require_travel_segment(start, end, label="Direct transfer misses narrow cap")
    with pytest.raises(SafetyError):
        authority.travel_route(start, end, label="No representable common waypoint")


@pytest.mark.parametrize("use_boundary", [False, True])
@pytest.mark.parametrize("point", [(5, 5), (15, 5), (18, 10)])
def test_zero_length_route_is_one_unchanged_destination(use_boundary, point):
    travel = envelope(surface=surface_record())
    authority = route_authority(travel, use_boundary)
    assert authority.travel_route(point, point, label="Zero-length transfer") == (point,)


@pytest.mark.parametrize("use_boundary", [False, True])
@pytest.mark.parametrize("point", [(float("nan"), 5), (5, float("inf")), (-float("inf"), 5), (-0.001, 5)])
def test_route_rejects_nonfinite_or_unauthorized_start_and_destination(use_boundary, point):
    travel = envelope(surface=surface_record())
    authority = route_authority(travel, use_boundary)
    with pytest.raises(SafetyError):
        authority.travel_route(point, (5, 5), label="Invalid route start")
    with pytest.raises(SafetyError):
        authority.travel_route((5, 5), point, label="Invalid route destination")
