"""Offline integration of reversible image eligibility and verified raster travel.

All travel records and transforms here are synthetic. Nothing connects to or
commands a controller, changes configuration, or qualifies physical travel.
"""
from __future__ import annotations

import math
from dataclasses import replace

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import LaserSettings, MachineSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.machine.service import MachineService
from laser_aligner.project import (
    Bounds,
    CoordinateSpace,
    LayerMode,
    ProjectDocument,
    SceneObject,
    Transform,
    generate_project_gcode,
    read_raster_asset_payload,
)
from laser_aligner.project.image_edit import default_edit, process_image
from laser_aligner.project.image_outline import AddOutlineCommand
from laser_aligner.project.path_geometry import NativePathGeometry, PathLineSegment, PathSubpath
from laser_aligner.project.raster_asset import decode_raster_payload_planes
from laser_aligner.raster_safety import COORDINATE_REFERENCE, RasterTravelEnvelope

OUTPUT_POLYGON = ((0., 0.), (200., 0.), (200., 200.), (0., 200.))


def travel(**changes):
    return replace(RasterTravelEnvelope(
        -10, 210, -10, 210, True, "OFFLINE test controller", "test firmware",
        "synthetic values only", COORDINATE_REFERENCE, "2026-10-15",
    ), **changes)


def frame(angle=7):
    radians = math.radians(angle)
    return HoneycombCoordinateFrame(
        origin_machine_mm=(30, 40), x_axis_machine=(math.cos(radians), math.sin(radians)),
        y_axis_machine=(-math.sin(radians), math.cos(radians)), width_mm=100, height_mm=100,
        provenance_digest="f" * 64,
    )


def artwork(tmp_path, *, invert=False, rotation=0, scan=0, mirror=False):
    image = np.full((96, 128, 4), 255, np.uint8)
    image[:, :, :3] = (np.arange(128, dtype=np.uint8)[None, :, None] + 40)
    image[:, :16, 3] = 0
    image[42:46, 30:100, :3] = 220
    path = tmp_path / "synthetic-edited.png"
    assert cv2.imwrite(str(path), image)
    payload = read_raster_asset_payload(path)
    settings = default_edit(payload.identity.sha256, (128, 96))
    settings["invert"] = invert
    settings["regions"] = [
        {"operation": "restore", "kind": "rectangle", "points": [[-.5, -.5], [.5, .5]], "radius": 0},
        {"operation": "subtract", "kind": "rectangle", "points": [[-.08, -.5], [.08, .5]], "radius": 0},
        {"operation": "subtract", "kind": "ellipse", "points": [[.2, -.2], [.4, -.4]], "radius": 0},
    ]
    document = ProjectDocument.new("Masked travel", Bounds(0, 0, 100, 100),
                                   coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL)
    layer = document.layers[0]
    layer.mode, layer.power_percent, layer.speed_mm_min = LayerMode.RASTER, 20, 1200
    layer.line_interval_mm, layer.overscan_percent, layer.scan_angle_deg = .5, 2.5, scan
    layer.air_assist = False
    item = SceneObject(kind="image", layer_id=layer.id,
                       transform=Transform(50.0004, 50.0004, 32, 24, rotation_deg=rotation, mirror_x=mirror),
                       geometry={"asset": str(path), "image_edit": settings})
    document.add_object(item)
    gray, source_allowed = decode_raster_payload_planes(payload)
    _, allowed = process_image(gray, item.geometry, source_allowed=source_allowed)
    return document, item, allowed


def generate(document, *, envelope=None, placement=None, spot=(0., 0.), **kwargs):
    laser = LaserSettings(power_max=1000, spot_offset_x_mm=spot[0], spot_offset_y_mm=spot[1],
                          guarded_output_polygon_mm=OUTPUT_POLYGON)
    return generate_project_gcode(
        document, laser, coordinate_frame=placement or frame(), machine_work_area=WorkArea(0, 200, 0, 200),
        guarded_output_polygon_mm=OUTPUT_POLYGON, start_position=(10, 10),
        raster_travel_envelope=envelope, **kwargs,
    )


def powered(job):
    return [(move.start_x, move.start_y, move.end_x, move.end_y, move.power)
            for move in job.plan.moves if move.laser_on and move.power > 0]


@pytest.mark.parametrize("invert", [False, True])
@pytest.mark.parametrize("rotation,scan,mirror,spot", [
    (0, 0, False, (0., 0.)), (31, 13, True, (.1234, -.2345)), (90, 45, False, (-.3578, .4012)),
])
def test_mask_alpha_and_dither_survive_expanded_travel_and_transforms(
    tmp_path, invert, rotation, scan, mirror, spot,
):
    document, item, allowed = artwork(tmp_path, invert=invert, rotation=rotation, scan=scan, mirror=mirror)
    placement = frame()
    legacy = generate(document, placement=placement, spot=spot)
    revised = generate(document, envelope=travel(), placement=placement, spot=spot)
    assert powered(revised) == powered(legacy)
    assert revised.cut_length_mm == pytest.approx(legacy.cut_length_mm)
    assert revised.raster_travel_envelope == travel()
    assert revised.plan.raster_boundary is not None
    assert all(not move.laser_on for move in revised.plan.moves if move.rapid)
    # Inspect the serialized program's actual physical beam, including fractional
    # spot offsets that are more precise than the display metadata.
    angle = math.radians(rotation)
    for move in revised.plan.moves:
        if not move.laser_on or move.power <= 0:
            continue
        ratios = np.linspace(0., 1., max(3, math.ceil(move.distance_mm * 40)))
        beam = np.column_stack((
            move.start_x + ratios * (move.end_x - move.start_x) - revised.plan.spot_offset_x + spot[0],
            move.start_y + ratios * (move.end_y - move.start_y) - revised.plan.spot_offset_y + spot[1],
        ))
        local = np.asarray([placement.machine_to_local(*point) for point in beam])
        delta = local - (item.transform.x_mm, item.transform.y_mm)
        local_x = (delta[:, 0] * math.cos(angle) + delta[:, 1] * math.sin(angle)) * (-1 if mirror else 1)
        local_y = -delta[:, 0] * math.sin(angle) + delta[:, 1] * math.cos(angle)
        columns = np.floor((local_x / 32 + .5) * 128).astype(int)
        rows = np.floor((.5 - local_y / 24) * 96).astype(int)
        assert np.all((columns >= 0) & (columns < 128) & (rows >= 0) & (rows < 96))
        assert np.all(allowed[rows, columns])
    commands = [line.strip() for line in revised.text.splitlines() if line.strip() and not line.startswith(";")]
    assert commands[-1] == "M5"
    assert sum(line.startswith("E3RASTER ") for line in commands) == 1


def test_only_zero_power_travel_can_leave_surface_after_masked_sampling(tmp_path):
    document, item, _allowed = artwork(tmp_path, invert=True)
    item.transform.x_mm = 10
    document.layers[0].overscan_percent = 60
    # Remaining eligible pixels stay inside the local surface, while run-up
    # extends left of it. The original frame and sampling grid stay fixed.
    item.geometry["image_edit"]["regions"].append({
        "operation": "subtract", "kind": "rectangle", "points": [[-.5, -.5], [-.1, .5]], "radius": 0,
    })
    placement = frame(0)
    with pytest.raises(SafetyError, match="verified machine.raster_travel_envelope"):
        generate(document, placement=placement)
    job = generate(document, envelope=travel(), placement=placement)
    outside_surface = [move for move in job.plan.moves
                       if min(move.start_x, move.end_x) < 30 and not move.rapid]
    assert outside_surface
    assert all(not move.laser_on and move.power == 0 for move in outside_surface)
    assert all(min(move.start_x, move.end_x) >= 30 for move in job.plan.moves if move.laser_on)


@pytest.mark.parametrize("envelope", [None, travel(verified=False)])
def test_masked_output_rejects_missing_or_unverified_required_travel(tmp_path, envelope):
    document, _item, _allowed = artwork(tmp_path)
    with pytest.raises(SafetyError, match="separately configured verified"):
        generate(document, envelope=envelope, require_verified_raster_travel_limits=True)


def test_masked_output_rejects_too_small_travel_and_powered_surface_violation(tmp_path):
    document, item, _allowed = artwork(tmp_path, invert=True)
    with pytest.raises(SafetyError, match="Machine carriage travel boundary"):
        generate(document, envelope=travel(x_max=70))
    with pytest.raises(SafetyError, match="Machine carriage travel boundary"):
        generate(document, envelope=travel(x_min=20))  # Initial approach starts at X10.
    item.transform.x_mm = -10
    with pytest.raises(SafetyError, match="powered raster engraving surface"):
        generate(document, envelope=travel())


def test_masked_program_retains_envelope_digest_preflight_gate(tmp_path):
    document, _item, _allowed = artwork(tmp_path)
    job = generate(document, envelope=travel())
    # Pure parser/preflight only: neither instance connects to any controller.
    settings = MachineSettings(backend="serial", protocol="grbl", allow_motion=True,
                               work_area=WorkArea(0, 200, 0, 200), photo_x=10, photo_y=10,
                               raster_travel_envelope=travel())
    laser = LaserSettings(power_max=1000, guarded_output_polygon_mm=OUTPUT_POLYGON)
    service = MachineService(settings, laser, hardware_enabled=True)
    assert service.preflight_program(job.text, guarded_output_polygon_mm=OUTPUT_POLYGON).requires_laser_authorization
    settings.raster_travel_envelope = travel(configuration="different synthetic record")
    with pytest.raises(SafetyError, match="verification changed"):
        service.preflight_program(job.text, guarded_output_polygon_mm=OUTPUT_POLYGON)


def test_expanded_travel_preserves_invalid_derived_cut_path_rejection(tmp_path):
    document, item, _allowed = artwork(tmp_path)
    path = NativePathGeometry((PathSubpath((-.4, -.3), (
        PathLineSegment((.4, -.3)), PathLineSegment((.4, .3)), PathLineSegment((-.4, .3)),
    ), True),))
    command = AddOutlineCommand(document, item.id, path)
    command.redo()
    assert not command.layer.output_enabled and command.layer.power_percent == 0
    generate(document, envelope=travel())
    document.get_layer(command.item.layer_id).output_enabled = True
    document.get_layer(command.item.layer_id).power_percent = 10
    malformed = NativePathGeometry((PathSubpath((-.4, -.3), (
        PathLineSegment((.4, .3)), PathLineSegment((-.4, .3)), PathLineSegment((.4, -.3)),
    ), True),))
    command.item.geometry = malformed.to_dict()
    with pytest.raises(ValueError, match="Invalid image cutting outline"):
        generate(document, envelope=travel())


def test_modeled_leads_cannot_bypass_final_stream_command_limit(tmp_path, monkeypatch):
    from laser_aligner.project import toolpath

    image = np.zeros((50, 8), np.uint8)
    path = tmp_path / "budget.png"
    assert cv2.imwrite(str(path), image)
    payload = read_raster_asset_payload(path)
    document = ProjectDocument.new("Lead budget", Bounds(0, 0, 100, 100),
                                   coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL)
    layer = document.layers[0]
    layer.mode, layer.power_percent, layer.line_interval_mm, layer.overscan_percent = LayerMode.RASTER, 10, 1, 0
    layer.air_assist = False
    document.add_object(SceneObject(kind="image", layer_id=layer.id, transform=Transform(50, 50, 8, 50),
                                   geometry={"asset": str(path), "image_edit": default_edit(payload.identity.sha256, [8, 50])}))
    monkeypatch.setattr(toolpath, "_MAX_STREAM_COMMANDS", 270)
    monkeypatch.setattr(toolpath, "_STREAM_COMMAND_RESERVE", 0)
    with pytest.raises(ValueError, match="Generated output contains.*streamed commands"):
        generate(document, envelope=travel())
