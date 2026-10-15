"""Offline geometry/serialization regression tests; envelope values are synthetic."""
import math
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import LaserSettings, MachineSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.geometry import Polyline
from laser_aligner.machine.service import MachineService
from laser_aligner.planning.model import RasterRow
from laser_aligner.project import (
    Bounds,
    CoordinateSpace,
    LayerMode,
    ProjectDocument,
    SceneObject,
    generate_project_gcode,
)
from laser_aligner.project.toolpath import _prepare_raster_clearance, _raster_motion_points
from laser_aligner.raster_safety import COORDINATE_REFERENCE, RasterTravelEnvelope


def envelope(**changes):
    return replace(RasterTravelEnvelope(5, 245, 5, 235, True, "OFFLINE fixture controller",
                   "fixture firmware", "fixture config", COORDINATE_REFERENCE, "2026-10-15"), **changes)


def frame(angle=0, origin=(30, 30)):
    a = math.radians(angle)
    return HoneycombCoordinateFrame(origin_machine_mm=origin,
        x_axis_machine=(math.cos(a), math.sin(a)), y_axis_machine=(-math.sin(a), math.cos(a)),
        width_mm=191, height_mm=191, provenance_digest="f" * 64)


def image_document(tmp_path: Path, image=None, *, width=180.752, height=182.367, center=(92.789, 94.155), rotation=0):
    path = tmp_path / "fixture.png"
    assert cv2.imwrite(str(path), np.zeros((20, 20), np.uint8) if image is None else image)
    doc = ProjectDocument.new("overscan", Bounds(0, 0, 191, 191), coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL)
    layer = doc.layers[0]
    layer.mode = LayerMode.RASTER
    layer.overscan_percent = 2.5
    layer.line_interval_mm = 1
    layer.speed_mm_min = 1200
    layer.air_assist = False
    doc.add_object(SceneObject(name="test image", kind="image", layer_id=layer.id,
        geometry={"asset": str(path)}, transform={"x_mm": center[0], "y_mm": center[1],
        "width_mm": width, "height_mm": height, "rotation_deg": rotation}))
    return doc


def generate(doc, *, travel=None, coordinate_frame=None, laser=None, **kwargs):
    polygon = ((10., 10.), (240., 10.), (240., 240.), (10., 240.))
    laser = laser or LaserSettings(power_max=1000, guarded_output_polygon_mm=polygon)
    return generate_project_gcode(doc, laser, coordinate_frame=coordinate_frame or frame(),
        machine_work_area=WorkArea(10, 210, 10, 210), guarded_output_polygon_mm=polygon,
        start_position=(15, 195), raster_travel_envelope=travel, **kwargs)


def powered(job):
    return [(m.start_x, m.start_y, m.end_x, m.end_y, m.power) for m in job.plan.moves if m.laser_on]


def test_exact_reproduction_preserves_engraving_and_accepts_verified_off_surface_travel(tmp_path):
    doc = image_document(tmp_path)
    row = RasterRow(np.array([[2.413, 3.0], [183.165, 3.0]]),
                    [Polyline(np.array([[2.413, 3.0], [183.165, 3.0]]))], "repro")
    _prepare_raster_clearance([row], doc.layers[0], LaserSettings())
    motion = _raster_motion_points(row.points, 2.5, row.lead_distance_mm)
    assert motion[:, 0].min() == pytest.approx(-2.1058)
    assert motion[:, 0].max() == pytest.approx(187.6838)
    with pytest.raises(SafetyError, match="verified machine.raster_travel_envelope"):
        generate(doc)
    job = generate(doc, travel=envelope())
    assert job.plan.bounds_mm[0] == pytest.approx(15)  # park is part of footprint
    assert min(m.end_x for m in job.plan.moves if not m.laser_on) == pytest.approx(27.894)
    assert min(m.start_x for m in job.plan.moves if m.laser_on) == pytest.approx(32.413)
    assert max(m.end_x for m in job.plan.moves if m.laser_on) == pytest.approx(213.165)
    assert all(m.power == 100 for m in job.plan.moves if m.laser_on)
    settings = MachineSettings(backend="serial", protocol="grbl", allow_motion=True,
        work_area=WorkArea(10, 210, 10, 210), photo_x=15, photo_y=195, raster_travel_envelope=envelope())
    service = MachineService(settings, LaserSettings(power_max=1000,
        guarded_output_polygon_mm=((10.,10.),(240.,10.),(240.,240.),(10.,240.))), hardware_enabled=True)
    assert service.preflight_program(job.text, guarded_output_polygon_mm=job.guarded_output_polygon_mm).requires_laser_authorization


def test_current_calibration_repro_cannot_use_historical_y215(tmp_path):
    doc = image_document(tmp_path)
    current = frame(math.degrees(math.atan2(.006622619658, .999978070214)),
                    (28.7714478853, 37.5035345157))
    with pytest.raises(SafetyError, match=r"Y maximum.*8\."):
        generate(doc, travel=envelope(y_max=215), coordinate_frame=current)


@pytest.mark.parametrize("travel", [None, envelope(verified=False)])
def test_missing_or_unverified_travel_is_explicitly_blocked(tmp_path, travel):
    with pytest.raises(SafetyError, match="separately configured verified"):
        generate(image_document(tmp_path), travel=travel, require_verified_raster_travel_limits=True)


def test_powered_output_outside_surface_still_rejected_with_large_travel(tmp_path):
    doc = image_document(tmp_path, center=(90, 94.155), width=185)
    with pytest.raises(SafetyError, match="powered raster engraving surface"):
        generate(doc, travel=envelope())


def test_motion_outside_travel_reports_extra_clearance(tmp_path):
    with pytest.raises(SafetyError, match=r"Machine carriage travel boundary.*X maximum.*2\.6838 mm"):
        generate(image_document(tmp_path), travel=envelope(x_max=215))


def test_initial_approach_and_row_transitions_are_in_footprint(tmp_path):
    job = generate(image_document(tmp_path, width=20, height=4, center=(90,90)), travel=envelope())
    rapids = [m for m in job.plan.moves if m.rapid]
    assert len(rapids) == 4
    assert (rapids[0].start_x, rapids[0].start_y) == (15,195)
    for previous, following in zip(rapids, rapids[1:], strict=False):
        assert previous.end_y != following.start_y or previous.end_x != following.start_x
        assert not following.laser_on
    with pytest.raises(SafetyError, match="X minimum.*5.0000 mm"):
        generate(image_document(tmp_path, width=20, height=4, center=(90,90)), travel=envelope(x_min=20))


@pytest.mark.parametrize("offset", [(2.5,-3.25), (-1.5,2.75)])
def test_nonzero_offsets_preserve_beam_placement_and_check_carriage(tmp_path, offset):
    doc = image_document(tmp_path, width=20, height=8, center=(90,90))
    normal = generate(doc, travel=envelope())
    laser = LaserSettings(power_max=1000, spot_offset_x_mm=offset[0], spot_offset_y_mm=offset[1],
        guarded_output_polygon_mm=((10.,10.),(240.,10.),(240.,240.),(10.,240.)))
    shifted = generate(doc, travel=envelope(), laser=laser)
    assert np.array(powered(shifted)) == pytest.approx(np.array(powered(normal)))
    assert shifted.plan.spot_offset_x == offset[0]
    assert shifted.plan.spot_offset_y == offset[1]


@pytest.mark.parametrize("rotation,scan,placement", [(0,37,0), (23,0,0), (-19,31,4)])
def test_rotated_scans_keep_exact_powered_samples(tmp_path, rotation, scan, placement):
    img = np.array([[0,255,254,128,0,255,1,255]] * 8, np.uint8)
    doc = image_document(tmp_path, img, width=20, height=12, center=(90,90), rotation=rotation)
    doc.layers[0].scan_angle_deg = scan
    legacy = generate(doc, coordinate_frame=frame(placement))
    revised = generate(doc, travel=envelope(), coordinate_frame=frame(placement))
    assert powered(revised) == powered(legacy)
    assert revised.cut_length_mm == pytest.approx(legacy.cut_length_mm)


@pytest.mark.parametrize("border", [255,254,250])
def test_white_nearwhite_and_dithered_borders_do_not_change_powered_pixels(tmp_path,border):
    img = np.full((16,16), border, np.uint8)
    img[4:12,4:12] = 0
    doc = image_document(tmp_path,img,width=16,height=16,center=(90,90))
    doc.layers[0].line_interval_mm = 1
    legacy = generate(doc)
    revised = generate(doc,travel=envelope())
    assert powered(revised) == powered(legacy)
    assert revised.travel_length_mm < legacy.travel_length_mm
    if border == 255:
        assert revised.plan.bounds_mm[2] < legacy.plan.bounds_mm[2]
    elif border == 250:
        assert len(powered(revised)) > 8  # near-white dither dots remain real output


def test_edge_pixels_and_serpentine_phase_are_preserved(tmp_path):
    img = np.full((8,8),255,np.uint8)
    img[0,0] = img[7,7] = 0
    img[3,2:4] = 0
    doc = image_document(tmp_path,img,width=8,height=8,center=(90,90))
    old,new = generate(doc),generate(doc,travel=envelope())
    assert powered(new) == powered(old)
    assert len(powered(new)) == 3
    assert new.path_count == old.path_count


def test_high_speed_preserves_modeled_runup_instead_of_assuming_percent(tmp_path):
    doc = image_document(tmp_path,np.zeros((2,20),np.uint8),width=20,height=2,center=(90,90))
    doc.layers[0].speed_mm_min = 6000
    job = generate(doc,travel=envelope())
    # v=100 mm/s, a=500 mm/s2 => 10 mm per end, versus 0.5mm from 2.5%.
    scans = [m for m in job.plan.moves if not m.rapid]
    assert scans[0].distance_mm == pytest.approx(10)
    assert scans[-1].distance_mm == pytest.approx(10)
    assert all(m.power == 100 for m in scans if m.laser_on)
