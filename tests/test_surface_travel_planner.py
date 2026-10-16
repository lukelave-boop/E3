"""Offline planner acceptance/rejection tests; no private image or hardware is used."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import LaserSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.geometry import Polyline
from laser_aligner.planning.model import RasterRow
from laser_aligner.project import (
    Bounds,
    CoordinateSpace,
    LayerMode,
    ProjectDocument,
    SceneObject,
    generate_project_gcode,
    load_project,
    toolpath,
)
from laser_aligner.raster_safety import (
    COORDINATE_REFERENCE,
    RasterTravelEnvelope,
    VerifiedSurfaceTravel,
    program_raster_boundary,
)

CURRENT_FRAME = HoneycombCoordinateFrame(
    origin_machine_mm=(28.771447885318622, 37.503534515747134),
    x_axis_machine=(0.9999780702139779, 0.0066226196575648495),
    y_axis_machine=(-0.00662261965756489, 0.9999780702139778),
    width_mm=191,
    height_mm=191,
    provenance_digest="f" * 64,
)
OUTPUT_POLYGON = ((-100.0, -100.0), (400.0, -100.0), (400.0, 400.0), (-100.0, 400.0))


def rectangle(**changes):
    return replace(
        RasterTravelEnvelope(
            10, 225.7672592961884, 10, 210, True,
            "OFFLINE fixture controller", "fixture firmware", "fixture configuration",
            COORDINATE_REFERENCE, "2026-10-16",
        ),
        **changes,
    )


def verified_travel(coordinate_frame=CURRENT_FRAME, *, offset=(0.0, 0.0)):
    return rectangle(surface=VerifiedSurfaceTravel(
        beam_polygon_mm=coordinate_frame.corners_machine_mm,
        spot_offset_mm=offset,
        coordinate_frame_digest=coordinate_frame.provenance_digest,
        verified=True,
        confirmed_at="2026-10-16",
        evidence="OFFLINE synthetic operator confirmation; not physical verification",
    ))


def rotated_frame(angle, *, origin=(50.0, 20.0)):
    radians = math.radians(angle)
    return replace(
        CURRENT_FRAME,
        origin_machine_mm=origin,
        x_axis_machine=(math.cos(radians), math.sin(radians)),
        y_axis_machine=(-math.sin(radians), math.cos(radians)),
    )


def image_document(
    tmp_path: Path, image=None, *, width=20.0, height=10.0,
    center=(95.5, 95.5), rotation=0.0, scan_angle=0.0, speed=1200.0,
):
    path = tmp_path / "synthetic-surface-travel.png"
    assert cv2.imwrite(str(path), np.zeros((10, 20), np.uint8) if image is None else image)
    document = ProjectDocument.new(
        "Synthetic surface travel", Bounds(0, 0, 191, 191),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.overscan_percent = 2.5
    layer.line_interval_mm = 1.0
    layer.speed_mm_min = speed
    layer.scan_angle_deg = scan_angle
    layer.air_assist = False
    document.add_object(SceneObject(
        name="Synthetic image", kind="image", layer_id=layer.id,
        geometry={"asset": str(path)},
        transform={"x_mm": center[0], "y_mm": center[1], "width_mm": width,
                   "height_mm": height, "rotation_deg": rotation},
    ))
    return document


def generate(document, *, travel=None, coordinate_frame=CURRENT_FRAME, offset=(0.0, 0.0), start=(15, 195)):
    output_polygon = OUTPUT_POLYGON if coordinate_frame is not None else None
    return generate_project_gcode(
        document,
        LaserSettings(
            power_max=1000, spot_offset_x_mm=offset[0], spot_offset_y_mm=offset[1],
            guarded_output_polygon_mm=output_polygon,
        ),
        coordinate_frame=coordinate_frame,
        machine_work_area=WorkArea(10, 210, 10, 210),
        guarded_output_polygon_mm=output_polygon,
        raster_travel_envelope=travel,
        start_position=start,
    )


def powered_moves(job):
    return tuple(
        (move.start_x, move.start_y, move.end_x, move.end_y, move.power)
        for move in job.plan.moves if move.laser_on
    )


def patch_rows(monkeypatch, row_endpoints):
    """Supply deterministic synthetic samples; retain the complete downstream planner."""
    def rows(*args, **kwargs):
        result = []
        for endpoints in row_endpoints:
            points = np.asarray(endpoints, dtype=float)
            result.append(RasterRow(
                points=points.copy(), spans=[Polyline(points.copy())], source_tag="synthetic row",
            ))
        return result, (), 20 * len(result)

    monkeypatch.setattr(toolpath, "_raster_rows", rows)


def test_reported_limiting_row_keeps_exact_powered_extent_above_y210(tmp_path, monkeypatch):
    # This is only the numerical limiting row from the failure, not the private image.
    local_row = ((102.95055108359132, 186.18852476780188),
                 (102.25055108359133, 186.18852476780188))
    patch_rows(monkeypatch, [local_row])
    document = image_document(tmp_path, speed=6000)
    row = RasterRow(np.asarray(local_row), [Polyline(np.asarray(local_row))], "limiting row")
    toolpath._prepare_raster_clearance([row], document.layers[0], LaserSettings())
    motion = toolpath._place_points(toolpath._raster_motion_points(row.points, 2.5, row.lead_distance_mm), CURRENT_FRAME)
    assert row.lead_distance_mm == pytest.approx(10.0)
    assert motion[:, 1].max() == pytest.approx(224.43600474898037)
    assert toolpath._place_points(row.points, CURRENT_FRAME)[:, 1].max() == pytest.approx(224.3697785524044)
    with pytest.raises(SafetyError, match=r"Y maximum: 14\.4360 mm"):
        generate(document, travel=rectangle())

    job = generate(document, travel=verified_travel())
    reference = generate(document, travel=rectangle(y_max=245))
    assert max(max(move.start_y, move.end_y) for move in job.plan.moves) == pytest.approx(224.436)
    assert max(max(move.start_y, move.end_y) for move in job.plan.moves if move.laser_on) == pytest.approx(224.370)
    assert powered_moves(job) == powered_moves(reference)
    assert job.cut_length_mm == reference.cut_length_mm
    assert job.travel_length_mm == reference.travel_length_mm
    assert rectangle().y_max == 210  # The independent rectangle was not enlarged.


def test_full_191_mm_image_with_blank_margins_preserves_every_powered_sample(tmp_path):
    image = np.full((191, 191), 255, np.uint8)
    image[12:-12, 12:-12] = 0
    image[45:65, 80:84] = 255  # Include real internal laser-off gaps.
    document = image_document(tmp_path, image, width=191, height=191, speed=6000)
    with pytest.raises(SafetyError, match="Y maximum"):
        generate(document, travel=rectangle())
    job = generate(document, travel=verified_travel())
    reference = generate(document, travel=rectangle(y_max=245))
    assert document.objects[0].transform.width_mm == 191
    assert document.objects[0].transform.height_mm == 191
    assert powered_moves(job) == powered_moves(reference)
    assert job.cut_length_mm == reference.cut_length_mm
    assert max(move.end_y for move in job.plan.moves if move.laser_on) > 210
    assert program_raster_boundary(job.text.splitlines()) is not None


def test_full_bleed_runout_outside_both_verified_regions_is_not_clamped(tmp_path):
    coordinate_frame = rotated_frame(0, origin=(30, 30))
    document = image_document(tmp_path, np.zeros((191, 191), np.uint8), width=191, height=191, speed=6000)
    # A wider synthetic rectangle proves the image is otherwise a valid raster.
    reference = generate(document, travel=rectangle(x_min=-100, x_max=400, y_min=-100, y_max=400),
                         coordinate_frame=coordinate_frame)
    assert powered_moves(reference)
    with pytest.raises(SafetyError, match="travel"):
        generate(document, travel=verified_travel(coordinate_frame), coordinate_frame=coordinate_frame)


@pytest.mark.parametrize("surface_angle,scan_angle,image_angle,offset", [
    (23, 37, 17, (2.5, -3.25)),
    (-19, 31, -12, (-1.5, 2.75)),
    (0, 63, 0, (0.0, 0.0)),
])
def test_rotated_surfaces_scans_and_offsets_do_not_change_powered_pixels(
    tmp_path, surface_angle, scan_angle, image_angle, offset,
):
    coordinate_frame = rotated_frame(surface_angle)
    image = np.array([[0, 255, 254, 128, 0, 255, 1, 255]] * 8, np.uint8)
    document = image_document(
        tmp_path, image, width=30, height=20, center=(100, 170), rotation=image_angle,
        scan_angle=scan_angle, speed=6000,
    )
    beam_start = coordinate_frame.local_to_machine(95, 95)
    start = (beam_start[0] - offset[0], beam_start[1] - offset[1])
    job = generate(document, travel=verified_travel(coordinate_frame, offset=offset),
                   coordinate_frame=coordinate_frame, offset=offset, start=start)
    reference = generate(document, travel=rectangle(x_min=-100, x_max=400, y_min=-100, y_max=400),
                         coordinate_frame=coordinate_frame, offset=offset, start=start)
    assert powered_moves(job) == powered_moves(reference)
    assert job.cut_length_mm == reference.cut_length_mm
    assert (job.plan.spot_offset_x, job.plan.spot_offset_y) == offset


def test_initial_transfer_routes_around_gap_between_valid_endpoints(tmp_path, monkeypatch):
    coordinate_frame = rotated_frame(0, origin=(30, 30))
    travel = verified_travel(coordinate_frame)
    patch_rows(monkeypatch, [((1, 190), (5, 190))])
    document = image_document(tmp_path)
    travel.require_travel_point((15, 209), label="valid start in rectangle")
    travel.require_travel_point((30.6, 220), label="valid lead in surface")
    # The direct shortcut goes through x<30, y>210, outside both regions.
    with pytest.raises(SafetyError, match="travel"):
        travel.require_travel_segment((15, 209), (30.6, 220), label="Unsafe direct approach")
    job = generate(document, travel=travel, coordinate_frame=coordinate_frame, start=(15, 209))
    reference = generate(document, travel=rectangle(y_max=245), coordinate_frame=coordinate_frame, start=(15, 209))
    assert powered_moves(job) == powered_moves(reference)
    assert sum(move.rapid for move in job.plan.moves) > sum(move.rapid for move in reference.plan.moves)
    for move in job.plan.moves:
        travel.require_travel_segment((move.start_x, move.start_y), (move.end_x, move.end_y),
                                      label="Serialized routed approach")


def test_row_transfer_routes_around_gap_between_valid_endpoints(tmp_path, monkeypatch):
    coordinate_frame = rotated_frame(0, origin=(30, 30))
    travel = verified_travel(coordinate_frame)
    patch_rows(monkeypatch, [((5, 180), (0, 180)), ((2, 190), (5, 190))])
    document = image_document(tmp_path)
    travel.require_travel_point((29.6, 210), label="valid first runout in rectangle")
    travel.require_travel_point((31.6, 220), label="valid next lead in surface")
    with pytest.raises(SafetyError, match="travel"):
        travel.require_travel_segment((29.6, 210), (31.6, 220), label="Unsafe direct row transfer")
    job = generate(document, travel=travel, coordinate_frame=coordinate_frame, start=(35, 205))
    reference = generate(document, travel=rectangle(y_max=245), coordinate_frame=coordinate_frame, start=(35, 205))
    assert powered_moves(job) == powered_moves(reference)
    assert sum(move.rapid for move in job.plan.moves) > sum(move.rapid for move in reference.plan.moves)
    for move in job.plan.moves:
        travel.require_travel_segment((move.start_x, move.start_y), (move.end_x, move.end_y),
                                      label="Serialized routed row transfer")


def test_surface_travel_never_authorizes_powered_pixels_outside_surface(tmp_path):
    document = image_document(tmp_path, width=20, height=10, center=(100, 190))
    with pytest.raises(SafetyError, match="powered raster engraving surface"):
        generate(document, travel=verified_travel())


@pytest.mark.parametrize("change", ["digest", "geometry", "offset", "unverified"])
def test_stale_surface_verification_is_rejected_before_generation(tmp_path, change):
    document = image_document(tmp_path)
    coordinate_frame = CURRENT_FRAME
    travel = verified_travel()
    offset = (0.0, 0.0)
    if change == "digest":
        coordinate_frame = replace(coordinate_frame, provenance_digest="e" * 64)
    elif change == "geometry":
        coordinate_frame = replace(coordinate_frame, origin_machine_mm=(29, 38))
    elif change == "offset":
        offset = (1.0, -2.0)
    else:
        travel = replace(travel, surface=replace(travel.surface, verified=False))
    with pytest.raises(SafetyError, match="(?i)surface"):
        generate(document, travel=travel, coordinate_frame=coordinate_frame, offset=offset)


def test_surface_record_cannot_be_applied_without_a_calibrated_frame(tmp_path):
    document = image_document(tmp_path)
    document.coordinate_space = CoordinateSpace.MACHINE
    with pytest.raises(SafetyError, match="matching calibrated coordinate frame"):
        generate(document, travel=verified_travel(), coordinate_frame=None)


def test_legacy_without_surface_record_keeps_old_authority_and_powered_samples(tmp_path):
    document = image_document(tmp_path, center=(95, 80))
    legacy = generate(document)
    rectangle_only = generate(document, travel=rectangle())
    surface = generate(document, travel=verified_travel())
    assert program_raster_boundary(legacy.text.splitlines()) is None
    assert powered_moves(surface) == powered_moves(rectangle_only) == powered_moves(legacy)
    boundary = program_raster_boundary(rectangle_only.text.splitlines())
    assert boundary is not None
    assert boundary.travel_bounds_mm == rectangle().bounds_mm


@pytest.mark.parametrize("schema", [1, 2, 3, 4, 5, 6])
def test_existing_machine_project_schemas_keep_identical_generated_programs(tmp_path, schema):
    document = image_document(tmp_path, center=(95, 80))
    document.coordinate_space = CoordinateSpace.MACHINE
    document.work_area = Bounds(10, 10, 210, 210)
    expected = generate(document, coordinate_frame=None)
    payload = document.to_dict()
    payload["schema_version"] = schema
    if schema == 1:
        payload.pop("coordinate_space")
    path = tmp_path / "legacy-machine.e3laser"
    source = json.dumps(payload)
    path.write_text(source, encoding="utf-8")
    restored = load_project(path)
    actual = generate(restored, coordinate_frame=None)
    # Generation timestamps are presentation comments; controller commands stay identical.
    assert [line for line in actual.text.splitlines() if not line.startswith(";")] == [
        line for line in expected.text.splitlines() if not line.startswith(";")
    ]
    assert powered_moves(actual) == powered_moves(expected)
    assert path.read_text(encoding="utf-8") == source
    assert program_raster_boundary(actual.text.splitlines()) is None


def test_surface_travel_does_not_expand_independent_powered_output_authority(tmp_path):
    document = image_document(tmp_path, center=(100, 180))
    restricted_output = ((10.0, 10.0), (210.0, 10.0), (210.0, 210.0), (10.0, 210.0))
    with pytest.raises(SafetyError, match="guarded output"):
        generate_project_gcode(
            document, LaserSettings(guarded_output_polygon_mm=restricted_output),
            coordinate_frame=CURRENT_FRAME, machine_work_area=WorkArea(10, 210, 10, 210),
            guarded_output_polygon_mm=restricted_output, raster_travel_envelope=verified_travel(),
            start_position=(15, 195),
        )


@pytest.fixture
def rounding_travel():
    coordinate_frame = replace(rotated_frame(0, origin=(8, 8)), width_mm=10, height_mm=10)
    return replace(verified_travel(coordinate_frame), x_min=0, x_max=10, y_min=0, y_max=10)


def test_transfer_routing_uses_serialized_target_when_rounding_crosses_gap(rounding_travel):
    current = np.array([1.0, 9.0])
    target = np.array([9.0, 10.14285])
    rounding_travel.require_travel_segment(tuple(current), tuple(target), label="Raw direct transfer")
    with pytest.raises(SafetyError, match="travel"):
        rounding_travel.require_travel_segment(tuple(current), (9.0, 10.143), label="Rounded direct transfer")

    lines = []
    distance = toolpath._emit_laser_off_travel(lines, current, target, 1200, rounding_travel)

    assert lines == ["G0 X9 Y9 F1200", "G0 X9 Y10.143 F1200"]
    rounding_travel.require_travel_segment(tuple(current), (9.0, 9.0), label="Emitted approach")
    rounding_travel.require_travel_segment((9.0, 9.0), (9.0, 10.143), label="Emitted destination")
    assert distance == pytest.approx(8.0 + 1.14285)
    assert tuple(target) == (9.0, 10.14285)


@pytest.mark.parametrize("current_is_emitted", [False, True])
def test_transfer_keeps_actual_initial_pose_exact_but_rounds_prior_emitted_end(rounding_travel, current_is_emitted):
    current = np.array([9.0, 10.14285])
    target = np.array([1.0, 9.0])
    rounding_travel.require_travel_segment(tuple(current), tuple(target), label="Exact initial approach")
    with pytest.raises(SafetyError, match="travel"):
        rounding_travel.require_travel_segment((9.0, 10.143), tuple(target), label="Rounded prior endpoint")

    lines = []
    distance = toolpath._emit_laser_off_travel(
        lines, current, target, 1200, rounding_travel, current_is_emitted=current_is_emitted,
    )

    if current_is_emitted:
        assert lines == ["G0 X9 Y9 F1200", "G0 X1 Y9 F1200"]
        rounding_travel.require_travel_segment((9.0, 10.143), (9.0, 9.0), label="Emitted previous endpoint")
        rounding_travel.require_travel_segment((9.0, 9.0), tuple(target), label="Emitted destination")
        assert distance == pytest.approx(1.14285 + 8.0)
    else:
        assert lines == ["G0 X1 Y9 F1200"]
        rounding_travel.require_travel_segment(tuple(current), tuple(target), label="Exact actual initial pose")
        assert distance == pytest.approx(math.dist(current, target))
    assert tuple(current) == (9.0, 10.14285)
