"""Machine-axis raster scans preserve calibrated artwork and planner guards."""

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

import laser_aligner.project.toolpath as toolpath_module
from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import LaserSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.project import (
    Bounds,
    CoordinateSpace,
    LayerMode,
    ProjectDocument,
    SceneObject,
    generate_project_gcode,
)


def _rotation(angle: float) -> np.ndarray:
    radians = math.radians(angle)
    return np.array([
        [math.cos(radians), -math.sin(radians)],
        [math.sin(radians), math.cos(radians)],
    ])


def _frame(angle: float, center: tuple[float, float] = (60, 60)):
    rotation = _rotation(angle)
    origin = np.asarray(center) - rotation @ np.array([50.0, 50.0])
    return HoneycombCoordinateFrame(
        origin_machine_mm=tuple(origin),
        x_axis_machine=tuple(rotation[:, 0]),
        y_axis_machine=tuple(rotation[:, 1]),
        width_mm=100,
        height_mm=100,
        provenance_digest="a" * 64,
    )


def _document(tmp_path: Path, kind: str, *, width: float = 12, height: float = 8):
    document = ProjectDocument.new(
        "Machine-axis raster",
        Bounds(0, 0, 100, 100),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 1
    layer.overscan_percent = 10
    if kind == "image":
        image_path = tmp_path / "pattern.png"
        assert cv2.imwrite(str(image_path), np.zeros((32, 48), dtype=np.uint8))
        item = SceneObject(
            name="Image",
            kind="image",
            layer_id=layer.id,
            geometry={"asset": str(image_path)},
            transform={"x_mm": 50, "y_mm": 50,
                       "width_mm": width, "height_mm": height},
        )
    else:
        item = SceneObject.rectangle(
            layer.id, center=(50, 50), width_mm=width, height_mm=height,
            corner_radius_mm=0,
        )
    document.add_object(item)
    return document


def _generate(document, frame, *, laser=None, work_area=None):
    return generate_project_gcode(
        document,
        laser or LaserSettings(boundary_margin_mm=0),
        coordinate_frame=frame,
        machine_work_area=work_area or WorkArea(-100, 200, -100, 200),
        start_position=(60, 60),
        optimize_order=False,
    )


@pytest.mark.parametrize("kind", ["image", "vector"])
@pytest.mark.parametrize("frame_angle", [0.379, -0.379, 37, 90])
@pytest.mark.parametrize("scan_angle", [0, -23, 37, 90])
def test_raster_direction_is_machine_relative_and_rows_advance_monotonically(
    tmp_path: Path, kind: str, frame_angle: float, scan_angle: float,
) -> None:
    document = _document(tmp_path, kind)
    document.layers[0].scan_angle_deg = scan_angle
    document.objects[0].transform.rotation_deg = 17
    frame = _frame(frame_angle)
    job = _generate(document, frame)
    assert job.plan is not None
    sweeps = [move for move in job.plan.moves if not move.rapid]
    assert sweeps and any(move.laser_on for move in sweeps)
    direction = _rotation(scan_angle)[:, 0]
    normal = _rotation(scan_angle)[:, 1]
    row_positions = []
    for move in sweeps:
        start = np.array([move.start_x, move.start_y])
        end = np.array([move.end_x, move.end_y])
        assert abs(float(np.dot(end - start, normal))) <= 0.0015
        if scan_angle == 0:
            assert move.start_y == move.end_y
        if scan_angle == 90:
            assert move.start_x == move.end_x
        row_position = float(np.dot(start, normal))
        if not row_positions or abs(row_position - row_positions[-1]) > 0.01:
            row_positions.append(row_position)
        if move.laser_on:
            # Inverting only the calibrated placement and object rotation must
            # put every burned endpoint back inside the original artwork.
            for point in (start, end):
                local = _rotation(frame_angle).T @ (
                    point - np.asarray(frame.origin_machine_mm)
                )
                object_point = _rotation(17).T @ (local - np.array([50, 50]))
                assert abs(object_point[0]) <= 6.002
                assert abs(object_point[1]) <= 4.002
    assert len(row_positions) >= 2
    assert np.diff(row_positions) == pytest.approx(
        [document.layers[0].line_interval_mm] * (len(row_positions) - 1), abs=0.002,
    )
    powered_directions = [
        np.sign(np.dot([move.end_x - move.start_x, move.end_y - move.start_y], direction))
        for move in sweeps if move.laser_on
    ]
    assert set(powered_directions) == {-1, 1}
    assert document.layers[0].scan_angle_deg == scan_angle
    assert document.objects[0].transform.rotation_deg == 17
    commands = [line for line in job.text.splitlines() if line and not line.startswith(";")]
    assert commands[-1] == "M5"


@pytest.mark.parametrize("frame_angle", [0.379, 90])
@pytest.mark.parametrize("mirror_x, mirror_y", [(False, False), (True, False),
                                                (False, True), (True, True)])
def test_pattern_and_mirrors_keep_calibrated_artwork_placement(
    tmp_path: Path, frame_angle: float, mirror_x: bool, mirror_y: bool,
) -> None:
    document = _document(tmp_path, "image")
    layer = document.layers[0]
    layer.line_interval_mm = 0.25
    item = document.objects[0]
    image = np.full((32, 48), 255, dtype=np.uint8)
    image[:16, :24] = 0
    assert cv2.imwrite(item.geometry["asset"], image)
    item.transform.rotation_deg = 23
    item.transform.mirror_x = mirror_x
    item.transform.mirror_y = mirror_y
    frame = _frame(frame_angle)
    job = _generate(document, frame)
    assert job.plan is not None
    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    lengths = np.array([move.distance_mm for move in powered])
    midpoints = np.array([
        [(move.start_x + move.end_x) / 2, (move.start_y + move.end_y) / 2]
        for move in powered
    ])
    centroid = np.average(midpoints, axis=0, weights=lengths)
    source_center = np.array([-3 * (-1 if mirror_x else 1),
                              2 * (-1 if mirror_y else 1)])
    expected = np.asarray(frame.origin_machine_mm) + _rotation(frame_angle) @ (
        np.array([50, 50]) + _rotation(23) @ source_center
    )
    assert centroid == pytest.approx(expected, abs=0.2)
    assert all(move.start_y == move.end_y for move in powered)
    for midpoint in midpoints:
        local = _rotation(frame_angle).T @ (midpoint - frame.origin_machine_mm)
        source = _rotation(23).T @ (local - np.array([50, 50]))
        source *= [-1 if mirror_x else 1, -1 if mirror_y else 1]
        assert source[0] <= 0.3
        assert source[1] >= -0.3


@pytest.mark.parametrize("frame_angle", [0.379, -0.379])
def test_dithered_half_micron_row_rounds_to_one_machine_y(
    tmp_path: Path, frame_angle: float,
) -> None:
    document = _document(tmp_path, "image", width=8, height=2)
    item = document.objects[0]
    item.transform.rotation_deg = -frame_angle
    image = np.tile(np.array([0, 255, 0, 255, 0, 255, 0, 255], dtype=np.uint8), (2, 1))
    assert cv2.imwrite(item.geometry["asset"], image)
    frame = _frame(frame_angle, center=(60, 50.5005))
    job = _generate(document, frame)
    assert job.plan is not None
    sweeps = [move for move in job.plan.moves if not move.rapid]
    assert len([move for move in sweeps if move.laser_on]) >= 6
    assert all(move.start_y == move.end_y for move in sweeps)
    assert len({move.start_y for move in sweeps}) == 2


@pytest.mark.parametrize("kind", ["image", "vector"])
@pytest.mark.parametrize("overscan, offset, label", [
    (50, 0, "placed raster overscan path"),
    (0, 1, "raster overscan path after laser spot correction"),
])
def test_machine_horizontal_runout_and_spot_offset_remain_bounded(
    tmp_path: Path, kind: str, overscan: float, offset: float, label: str,
) -> None:
    document = _document(tmp_path, kind, width=20, height=2)
    document.layers[0].overscan_percent = overscan
    frame = _frame(90)
    laser = LaserSettings(boundary_margin_mm=0, spot_offset_x_mm=offset)
    assert _generate(document, frame, laser=laser).plan is not None
    # Artwork occupies X59..61. Horizontal runout or the spot correction
    # crosses X58.5; the old locally horizontal scan would miss that runout.
    with pytest.raises(SafetyError, match=label):
        _generate(document, frame, laser=laser, work_area=WorkArea(58.5, 100, 0, 100))


@pytest.mark.parametrize("kind", ["image", "vector"])
def test_corrected_angle_row_budget_accepts_short_machine_height(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    document = _document(tmp_path, kind, width=2, height=20)
    monkeypatch.setattr(toolpath_module, "_MAX_RASTER_ROWS", 4)
    assert _generate(document, _frame(90)).plan is not None


@pytest.mark.parametrize("kind, frame_angle, limit_name, limit, message", [
    ("image", 90, "_MAX_RASTER_ROWS", 10, "row planner limit"),
    ("vector", 90, "_MAX_RASTER_ROWS", 10, "row planner limit"),
    ("image", 45, "_MAX_RASTER_SAMPLES", 100, "sample planner limit"),
    ("vector", 90, "_MAX_SCANLINE_EDGE_TESTS", 40, "scanline edge tests"),
    ("vector", 90, "_MAX_STREAM_COMMANDS", 60, "streamed commands.*command limit"),
])
def test_corrected_angle_budget_rejects_before_sampling_or_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
    frame_angle: float, limit_name: str, limit: int, message: str,
) -> None:
    document = _document(tmp_path, kind, width=20, height=2)
    document.layers[0].overscan_percent = 0
    monkeypatch.setattr(toolpath_module, limit_name, limit)
    monkeypatch.setattr(toolpath_module, "_STREAM_COMMAND_RESERVE", 0)
    monkeypatch.setattr(
        toolpath_module, "decode_raster_grayscale" if kind == "image" else "_scanline_rows",
        lambda *_args, **_kwargs: pytest.fail("Budget must reject before sampling or decode"),
    )
    with pytest.raises(ValueError, match=message):
        _generate(document, _frame(frame_angle))


@pytest.mark.parametrize("kind", ["image", "vector"])
def test_machine_axis_overscan_does_not_gain_local_surface_authority(
    tmp_path: Path, kind: str,
) -> None:
    document = _document(tmp_path, kind, width=10, height=2)
    document.objects[0].transform.y_mm = 1
    document.layers[0].overscan_percent = 0
    assert _generate(document, _frame(90)).plan is not None
    document.layers[0].overscan_percent = 50
    with pytest.raises(SafetyError, match="local raster overscan path"):
        _generate(document, _frame(90))


def test_fill_keeps_project_relative_direction_under_rotated_honeycomb(tmp_path: Path) -> None:
    document = _document(tmp_path, "vector")
    document.layers[0].mode = LayerMode.FILL
    document.layers[0].scan_angle_deg = 0
    job = _generate(document, _frame(90))
    assert job.plan is not None
    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    assert all(move.start_x == move.end_x for move in powered)
    assert all(move.start_y != move.end_y for move in powered)


@pytest.mark.parametrize("drift_in_span", [False, True])
def test_cardinal_row_canonicalization_rejects_actual_perpendicular_motion(
    drift_in_span: bool,
) -> None:
    row_points = np.array([[10.0, 20.0], [30.0, 20.0]])
    span_points = np.array([[12.0, 20.0], [28.0, 20.0]])
    (span_points if drift_in_span else row_points)[-1, 1] += 0.01
    row = toolpath_module.RasterRow(
        points=row_points,
        spans=[toolpath_module.Polyline(span_points, closed=False)],
        source_tag="Inconsistent scan",
    )
    with pytest.raises(SafetyError, match="does not match its machine scan angle"):
        toolpath_module._place_raster_rows([row], None, scan_angle_deg=0)
