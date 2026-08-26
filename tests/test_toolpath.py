import math
import re
import struct
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest

import laser_aligner.project.toolpath as toolpath_module
from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import LaserSettings, MachineSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.geometry import Polyline
from laser_aligner.machine.service import MachineService
from laser_aligner.project import (
    Bounds,
    CoordinateSpace,
    LayerMode,
    NativePathGeometry,
    ObjectKind,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    ProjectDocument,
    RasterAssetMetadata,
    SceneObject,
    Transform,
    decode_raster_grayscale,
    generate_project_frame,
    generate_project_gcode,
    probe_raster_asset,
    verify_project_job_assets,
)
from laser_aligner.templates import (
    RectangleGridSpec,
    instantiate_template,
    template_from_rectangle_grid,
)


def make_document():
    document = ProjectDocument.new("Toolpath", Bounds(15, 15, 205, 205))
    layer = document.layers[0]
    layer.speed_mm_min = 2000
    layer.power_percent = 10
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(110, 110),
            width_mm=76.2,
            height_mm=50.8,
            corner_radius_mm=6.35,
        )
    )
    return document


def make_image_document(
    image_path: Path,
    *,
    width_mm: float,
    height_mm: float,
    line_interval_mm: float = 1.0,
    overscan_percent: float = 0.0,
    work_area: Bounds | None = None,
) -> ProjectDocument:
    work_area = work_area or Bounds(0, 0, 100, 100)
    document = ProjectDocument.new("Raster image", work_area)
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = line_interval_mm
    layer.overscan_percent = overscan_percent
    layer.speed_mm_min = 600
    layer.power_percent = 10
    document.add_object(
        SceneObject(
            name="Pixels",
            kind="image",
            layer_id=layer.id,
            geometry={"asset": str(image_path)},
            transform={
                "x_mm": work_area.center[0],
                "y_mm": work_area.center[1],
                "width_mm": width_mm,
                "height_mm": height_mm,
            },
        )
    )
    return document


def honeycomb_frame(
    *,
    origin: tuple[float, float] = (100.0, 50.0),
    x_axis: tuple[float, float] = (0.0, 1.0),
    y_axis: tuple[float, float] = (-1.0, 0.0),
    width: float = 100.0,
    height: float = 100.0,
    digest: str = "a" * 64,
) -> HoneycombCoordinateFrame:
    return HoneycombCoordinateFrame(
        origin_machine_mm=origin,
        x_axis_machine=x_axis,
        y_axis_machine=y_axis,
        width_mm=width,
        height_mm=height,
        provenance_digest=digest,
    )


def _native_bulge_document(
    center_y: float,
    *,
    coordinate_space: CoordinateSpace = CoordinateSpace.MACHINE,
    control_y: float = 0.02,
) -> ProjectDocument:
    document = ProjectDocument.new(
        "Native bulge",
        Bounds(0, 0, 100, 100),
        coordinate_space=coordinate_space,
    )
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (-0.5, 0.0),
                (
                    PathCubicSegment(
                        (-0.25, control_y),
                        (0.25, control_y),
                        (0.5, 0.0),
                    ),
                ),
                closed=False,
            ),
        )
    )
    document.add_object(
        SceneObject.native_path(
            document.active_layer_id,
            geometry,
            transform=Transform(50.0, center_y, 80.0, 1.0),
        )
    )
    return document


def _closed_square_subpath(half_size: float) -> PathSubpath:
    return PathSubpath(
        (-half_size, -half_size),
        (
            PathLineSegment((half_size, -half_size)),
            PathLineSegment((half_size, half_size)),
            PathLineSegment((-half_size, half_size)),
        ),
        closed=True,
    )


def _closed_cubic_circle_subpath(radius: float) -> PathSubpath:
    handle = radius * 0.5522847498307936
    return PathSubpath(
        (radius, 0.0),
        (
            PathCubicSegment((radius, handle), (handle, radius), (0.0, radius)),
            PathCubicSegment((-handle, radius), (-radius, handle), (-radius, 0.0)),
            PathCubicSegment((-radius, -handle), (-handle, -radius), (0.0, -radius)),
            PathCubicSegment((handle, -radius), (radius, -handle), (radius, 0.0)),
        ),
        closed=True,
    )


def test_project_gcode_is_bracketed_by_laser_off():
    job = generate_project_gcode(make_document(), LaserSettings(power_max=1000))

    assert "M4 S100" in job.text
    assert "M5 ; laser off before any motion" in job.text
    assert job.text.rstrip().endswith("; End of E3 project job")
    assert job.path_count == 1
    assert job.cut_length_mm > 200


@pytest.mark.parametrize(
    ("correction", "comparison"),
    [(-100.0, lambda value: value < 100), (100.0, lambda value: value > 100)],
)
def test_vector_power_correction_emits_local_bounded_inline_s_without_m3(
    correction,
    comparison,
):
    document = ProjectDocument.new("Correction", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.speed_mm_min = 2000
    layer.power_percent = 10
    layer.vector_power_correction = correction
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(50, 50),
            width_mm=40,
            height_mm=20,
            corner_radius_mm=0,
        )
    )

    job = generate_project_gcode(
        document,
        LaserSettings(power_max=1000, preview_acceleration_mm_s2=500),
    )
    inline = [
        int(match.group(1))
        for line in job.text.splitlines()
        if line.startswith("G1 ")
        and (match := re.search(r"\bS(\d+)\b", line)) is not None
    ]

    assert "M4 S100" in job.text
    assert "M3" not in job.text
    assert inline and any(comparison(value) for value in inline)
    assert all(0 <= value <= 1000 for value in inline)
    assert job.text.splitlines()[-2] == "M5"
    MachineService(
        MachineSettings(
            backend="serial", allow_motion=True, work_area=Bounds(0, 0, 100, 100)
        ),
        LaserSettings(power_max=1000, boundary_margin_mm=0),
        hardware_enabled=True,
    ).validate_program(job.text)


def test_zero_vector_correction_retains_unsplit_gcode_and_straight_has_no_zone():
    document = ProjectDocument.new("Straight", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.vector_power_correction = -100
    document.add_object(
        SceneObject.line(layer.id, center=(50, 10), length_mm=80)
    )

    corrected = generate_project_gcode(document, LaserSettings(power_max=1000))
    layer.vector_power_correction = 0
    neutral = generate_project_gcode(document, LaserSettings(power_max=1000))

    corrected_motion = [line for line in corrected.text.splitlines() if line.startswith("G1 ")]
    neutral_motion = [line for line in neutral.text.splitlines() if line.startswith("G1 ")]
    assert corrected_motion == neutral_motion
    assert all(" S" not in line for line in corrected_motion)


def test_rounded_rectangle_does_not_expand_tessellation_into_corner_ramps():
    document = make_document()
    layer = document.layers[0]
    neutral = generate_project_gcode(document, LaserSettings(power_max=1000))
    layer.vector_power_correction = -100

    job = generate_project_gcode(document, LaserSettings(power_max=1000))
    powered_moves = [move for move in job.plan.moves if move.laser_on]

    assert len(powered_moves) == len([move for move in neutral.plan.moves if move.laser_on])
    assert all(" S" not in line for line in job.text.splitlines() if line.startswith("G1 "))


def test_frame_ignores_power_correction_and_remains_unpowered():
    document = make_document()
    document.layers[0].vector_power_correction = 100
    document.layers[0].raster_power_correction = 100

    frame = generate_project_frame(document, LaserSettings(power_max=1000))

    assert "M3 S" not in frame.text
    assert "M4 S" not in frame.text
    assert not re.search(r"\bS[1-9]\d*\b", frame.text)


def test_raster_correction_only_enters_image_when_overscan_is_insufficient():
    document = ProjectDocument.new("Raster correction", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 5
    layer.speed_mm_min = 1000
    layer.raster_power_correction = -100
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(50, 50),
            width_mm=20,
            height_mm=20,
            corner_radius_mm=0,
        )
    )

    layer.overscan_percent = 0
    insufficient = generate_project_gcode(document, LaserSettings(power_max=1000))
    layer.overscan_percent = 10
    sufficient = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert any(
        line.startswith("G1 ") and " S" in line
        for line in insufficient.text.splitlines()
    )
    assert all(
        " S" not in line
        for line in sufficient.text.splitlines()
        if line.startswith("G1 ")
    )
    assert "M4 S100" in insufficient.text
    assert "M3" not in insufficient.text


def test_project_gcode_applies_laser_spot_offset_but_keeps_design_bounds():
    baseline = generate_project_gcode(make_document(), LaserSettings(power_max=1000))
    corrected = generate_project_gcode(
        make_document(),
        LaserSettings(
            power_max=1000,
            spot_offset_x_mm=-28,
            spot_offset_y_mm=-8,
        ),
    )

    assert corrected.bounds_mm == pytest.approx(baseline.bounds_mm)
    assert "spot = controller + offset): X-28 Y-8" in corrected.text
    assert "; Controller bounds: X99.9..176.1 Y92.6..143.4" in corrected.text


def test_project_offset_rejects_shifted_controller_bounds():
    document = make_document()
    document.objects[0].transform.x_mm = 165

    with pytest.raises(SafetyError):
        generate_project_gcode(
            document,
            LaserSettings(
                boundary_margin_mm=0,
                spot_offset_x_mm=-28,
                spot_offset_y_mm=-8,
            ),
        )


def test_honeycomb_local_vector_is_rigidly_placed_before_spot_correction():
    document = ProjectDocument.new(
        "Local vector",
        Bounds(0, 0, 100, 100),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.add_object(
        SceneObject.line(
            document.active_layer_id,
            center=(20, 20),
            length_mm=20,
        )
    )
    frame = honeycomb_frame()

    job = generate_project_gcode(
        document,
        LaserSettings(
            power_max=1000,
            boundary_margin_mm=0,
            spot_offset_x_mm=2,
            spot_offset_y_mm=-3,
        ),
        optimize_order=False,
        start_position=(0, 0),
        coordinate_frame=frame,
        machine_work_area=WorkArea(0, 120, 0, 160),
    )

    assert job.coordinate_frame_signature == frame.provenance_signature
    assert job.bounds_mm == pytest.approx((80, 60, 80, 80))
    assert "G0 X78 Y63" in job.text
    assert "G1 X78 Y83" in job.text
    powered = [move for move in job.plan.moves if move.laser_on]
    assert [(move.end_x, move.end_y) for move in powered] == pytest.approx([(80, 80)])


def test_honeycomb_local_output_requires_frame_and_independent_machine_area():
    document = ProjectDocument.new(
        "Local",
        Bounds(0, 0, 100, 100),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.add_object(
        SceneObject.line(
            document.active_layer_id,
            center=(20, 20),
            length_mm=20,
        )
    )

    with pytest.raises(SafetyError, match="coordinate frame"):
        generate_project_gcode(document, LaserSettings())
    with pytest.raises(SafetyError, match="independent machine work area"):
        generate_project_gcode(
            document,
            LaserSettings(),
            coordinate_frame=honeycomb_frame(),
        )


def test_honeycomb_local_rejects_placed_vector_outside_machine_area():
    document = ProjectDocument.new(
        "Escaping local vector",
        Bounds(0, 0, 100, 100),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.add_object(
        SceneObject.line(
            document.active_layer_id,
            center=(20, 20),
            length_mm=20,
        )
    )

    with pytest.raises(SafetyError, match="placed design"):
        generate_project_gcode(
            document,
            LaserSettings(boundary_margin_mm=0),
            coordinate_frame=honeycomb_frame(),
            machine_work_area=WorkArea(0, 75, 0, 160),
        )


def test_honeycomb_local_rejects_controller_escape_after_spot_correction():
    document = ProjectDocument.new(
        "Controller escape",
        Bounds(0, 0, 100, 100),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.add_object(
        SceneObject.line(
            document.active_layer_id,
            center=(20, 20),
            length_mm=20,
        )
    )

    with pytest.raises(SafetyError, match="after laser spot correction"):
        generate_project_gcode(
            document,
            LaserSettings(
                boundary_margin_mm=0,
                spot_offset_x_mm=10,
            ),
            coordinate_frame=honeycomb_frame(),
            machine_work_area=WorkArea(75, 100, 0, 160),
        )


def test_honeycomb_local_vector_uses_same_explicit_polygon_as_machine_preflight():
    document = ProjectDocument.new(
        "Full support vector",
        Bounds(0, 0, 190, 190),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.add_object(
        SceneObject.rectangle(
            document.active_layer_id,
            center=(185, 185),
            width_mm=10,
            height_mm=10,
            corner_radius_mm=0,
        )
    )
    frame = honeycomb_frame(
        origin=(28, 40),
        x_axis=(1, 0),
        y_axis=(0, 1),
        width=190,
        height=190,
    )
    polygon = ((18.0, 30.0), (228.0, 30.0), (228.0, 240.0), (18.0, 240.0))
    machine_area = WorkArea(10, 210, 10, 210)
    laser = LaserSettings(
        power_max=1000,
        boundary_margin_mm=0,
        guarded_output_polygon_mm=polygon,
    )

    with pytest.raises(SafetyError, match="configured laser authority"):
        generate_project_gcode(
            document,
            laser,
            coordinate_frame=frame,
            machine_work_area=machine_area,
        )

    job = generate_project_gcode(
        document,
        laser,
        coordinate_frame=frame,
        machine_work_area=machine_area,
        guarded_output_polygon_mm=polygon,
        start_position=(28, 40),
    )
    preflight = MachineService(
        MachineSettings(backend="serial", allow_motion=True, work_area=machine_area),
        laser,
        hardware_enabled=True,
    ).preflight_program(
        job.text,
        guarded_output_polygon_mm=polygon,
    )

    assert job.bounds_mm == pytest.approx((208.0, 220.0, 218.0, 230.0))
    assert preflight.guarded_output_polygon_mm == polygon


def test_honeycomb_local_frame_uses_explicit_guarded_polygon() -> None:
    document = ProjectDocument.new(
        "Full support frame",
        Bounds(0, 0, 190, 190),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.add_object(
        SceneObject.rectangle(
            document.active_layer_id,
            center=(185, 185),
            width_mm=10,
            height_mm=10,
            corner_radius_mm=0,
        )
    )
    frame = honeycomb_frame(
        origin=(28, 40),
        x_axis=(1, 0),
        y_axis=(0, 1),
        width=190,
        height=190,
    )
    polygon = ((18.0, 30.0), (228.0, 30.0), (228.0, 240.0), (18.0, 240.0))
    machine_area = WorkArea(10, 210, 10, 210)

    frame_job = generate_project_frame(
        document,
        LaserSettings(
            boundary_margin_mm=0,
            guarded_output_polygon_mm=polygon,
        ),
        coordinate_frame=frame,
        machine_work_area=machine_area,
        guarded_output_polygon_mm=polygon,
        start_position=(28, 40),
    )

    assert frame_job.bounds_mm == pytest.approx((208.0, 220.0, 218.0, 230.0))

    restricted_polygon = (
        (18.0, 30.0),
        (215.0, 30.0),
        (215.0, 240.0),
        (18.0, 240.0),
    )
    with pytest.raises(SafetyError, match="placed frame path"):
        generate_project_frame(
            document,
            LaserSettings(
                boundary_margin_mm=0,
                guarded_output_polygon_mm=restricted_polygon,
            ),
            coordinate_frame=frame,
            machine_work_area=machine_area,
            guarded_output_polygon_mm=restricted_polygon,
            start_position=(28, 40),
        )


def test_multiple_layers_keep_distinct_power_and_speed():
    document = make_document()
    second = document.add_layer(
        name="Second",
        color="#4FC3A1",
        mode=LayerMode.LINE,
    )
    second.power_percent = 25
    second.speed_mm_min = 900
    document.add_object(SceneObject.ellipse(second.id, center=(80, 80), width_mm=20, height_mm=20))

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert "M4 S100" in job.text
    assert "M4 S250" in job.text
    assert "F2000" in job.text
    assert "F900" in job.text
    assert len(job.layer_summaries) == 2


def test_disabled_layer_is_omitted():
    document = make_document()
    document.layers[0].output_enabled = False

    with pytest.raises(ValueError, match="no enabled"):
        generate_project_gcode(document, LaserSettings())


def test_bounds_violation_is_rejected():
    document = make_document()
    document.objects[0].transform.x_mm = 200

    with pytest.raises(SafetyError):
        generate_project_gcode(document, LaserSettings(boundary_margin_mm=0))


def test_exact_boundary_float_noise_is_safe_but_real_overflow_is_rejected():
    document = ProjectDocument.new("Exact fit", Bounds(0, 0, 220, 220))
    exact_fit = RectangleGridSpec(
        name="Exact fit",
        rows=1,
        columns=3,
        width_mm=66.668,
        height_mm=10.0,
        horizontal_gap_mm=9.998,
    )
    template = template_from_rectangle_grid(exact_fit)
    for item in instantiate_template(
        template,
        target_x_mm=110.0,
        target_y_mm=110.0,
        rotation_deg=0.0,
        target_layer_id=document.active_layer_id,
    ):
        document.add_object(item)

    job = generate_project_gcode(
        document,
        LaserSettings(boundary_margin_mm=0),
    )
    assert job.bounds_mm[0] == pytest.approx(0.0, abs=1e-9)
    assert job.bounds_mm[2] == pytest.approx(220.0, abs=1e-9)

    outside = document.objects[-1]
    outside.transform.x_mm += 0.001
    with pytest.raises(SafetyError):
        generate_project_gcode(
            document,
            LaserSettings(boundary_margin_mm=0),
        )


def test_legacy_line_path_migration_preserves_planning_geometry() -> None:
    document = ProjectDocument.new("Legacy planning", Bounds(0, 0, 100, 100))
    current = SceneObject.path(
        document.active_layer_id,
        [
            {
                "points": [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5]],
                "closed": False,
            }
        ],
    )
    current.transform = Transform(
        40.0,
        60.0,
        30.0,
        20.0,
        rotation_deg=23.0,
        mirror_x=True,
        mirror_y=True,
    )
    document.add_object(current)
    payload = document.to_dict()
    payload["schema_version"] = 2
    payload["objects"][0]["geometry"] = {
        "polylines": [
            {
                "points": [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5]],
                "closed": False,
            }
        ]
    }

    migrated = ProjectDocument.from_dict(payload).objects[0]
    expected = toolpath_module.object_polylines(current)
    actual = toolpath_module.object_polylines(migrated)

    assert len(actual) == len(expected) == 1
    assert actual[0].closed is expected[0].closed is False
    assert actual[0].points == pytest.approx(expected[0].points)


def test_native_cubic_with_inside_endpoints_but_outside_curve_is_rejected() -> None:
    document = _native_bulge_document(99.99)
    flattened = toolpath_module.object_polylines(document.objects[0])[0]

    assert len(flattened.points) == 2
    assert max(flattened.points[:, 1]) < 100.0
    with pytest.raises(SafetyError, match="native curve"):
        generate_project_gcode(
            document,
            LaserSettings(boundary_margin_mm=0),
        )


def test_native_cubic_guarded_polygon_checks_full_curve_not_cached_vertices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _native_bulge_document(
        89.9,
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
        control_y=0.2,
    )
    item = document.objects[0]
    endpoints = np.asarray([[10.0, 89.9], [90.0, 89.9]], dtype=np.float64)
    monkeypatch.setattr(
        toolpath_module,
        "object_polylines",
        lambda _item: [Polyline(endpoints, closed=False, source_tag=item.name)],
    )
    frame = honeycomb_frame(
        origin=(0.0, 0.0),
        x_axis=(1.0, 0.0),
        y_axis=(0.0, 1.0),
    )
    polygon = ((0.0, 0.0), (100.0, 0.0), (100.0, 90.0), (0.0, 90.0))

    with pytest.raises(SafetyError, match="guarded output polygon.*native curve"):
        generate_project_gcode(
            document,
            LaserSettings(
                boundary_margin_mm=0,
                guarded_output_polygon_mm=polygon,
            ),
            coordinate_frame=frame,
            machine_work_area=WorkArea(0, 100, 0, 100),
            guarded_output_polygon_mm=polygon,
            start_position=(0.0, 0.0),
        )


def test_wholly_authorized_native_cubic_generates_and_frames_conservatively() -> None:
    document = _native_bulge_document(50.0, control_y=0.4)
    laser = LaserSettings(boundary_margin_mm=0)

    job = generate_project_gcode(document, laser)
    frame = generate_project_frame(document, laser)

    assert job.path_count == 1
    assert job.point_count > 2
    assert frame.bounds_mm[0] == pytest.approx(9.975)
    assert frame.bounds_mm[2] == pytest.approx(90.025)
    assert frame.bounds_mm[1] == pytest.approx(49.975)
    assert frame.bounds_mm[3] == pytest.approx(50.325)


def test_frame_contains_no_positive_laser_command():
    job = generate_project_frame(make_document(), LaserSettings(frame_power=0))

    assert not re.search(r"\bM[34]\b", job.text)
    assert "M5" in job.text


def test_honeycomb_local_frame_follows_rotated_corners_not_machine_aabb():
    document = ProjectDocument.new(
        "Local frame",
        Bounds(0, 0, 100, 100),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.add_object(
        SceneObject.rectangle(
            document.active_layer_id,
            center=(20, 30),
            width_mm=20,
            height_mm=10,
            corner_radius_mm=0,
        )
    )
    frame = honeycomb_frame()

    job = generate_project_frame(
        document,
        LaserSettings(boundary_margin_mm=0),
        start_position=(0, 0),
        coordinate_frame=frame,
        machine_work_area=WorkArea(0, 120, 0, 160),
    )

    assert job.coordinate_frame_signature == frame.provenance_signature
    assert job.bounds_mm == pytest.approx((65, 60, 75, 80))
    assert "G0 X75 Y60" in job.text
    assert "G1 X75 Y80" in job.text
    assert "G1 X65 Y80" in job.text
    assert "G1 X65 Y60" in job.text


def test_frame_includes_rotated_image_and_mixed_vector_bounds(tmp_path: Path) -> None:
    image_path = tmp_path / "frame.png"
    assert cv2.imwrite(str(image_path), np.zeros((2, 4), dtype=np.uint8))
    document = make_image_document(image_path, width_mm=4, height_mm=2)
    image_item = document.objects[0]
    image_item.transform.x_mm = 30
    image_item.transform.y_mm = 40
    image_item.transform.rotation_deg = 90

    image_only = generate_project_frame(document, LaserSettings(frame_power=0))

    assert image_only.bounds_mm == pytest.approx((29.0, 38.0, 31.0, 42.0))
    assert "G0 X29 Y38" in image_only.text
    assert "G1 X31 Y42" in image_only.text

    document.add_object(
        SceneObject.rectangle(
            document.active_layer_id,
            center=(80, 70),
            width_mm=10,
            height_mm=6,
        )
    )
    mixed = generate_project_frame(document, LaserSettings(frame_power=0))

    assert mixed.bounds_mm == pytest.approx((29.0, 38.0, 85.0, 73.0))


def test_zero_power_layer_does_not_emit_laser_enable():
    document = make_document()
    document.layers[0].power_percent = 0

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert not re.search(r"\bM[34]\b", job.text)
    assert "Layer power is zero" in job.text


def test_fill_layer_emits_bounded_scanlines_and_exact_preview():
    document = make_document()
    document.layers[0].mode = LayerMode.FILL
    document.layers[0].line_interval_mm = 2.0

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.path_count > 10
    assert "\"mode\":\"fill\"" in job.text
    assert job.plan is not None and job.plan.powered
    assert job.plan.maximum_power == pytest.approx(100.0)
    assert all(
        document.work_area.contains(move.end_x, move.end_y)
        for move in job.plan.moves
    )


def test_native_compound_scanlines_honor_evenodd_and_nonzero_winding() -> None:
    outer = np.asarray(
        [[-5.0, -5.0], [5.0, -5.0], [5.0, 5.0], [-5.0, 5.0], [-5.0, -5.0]],
        dtype=np.float64,
    )
    inner_same_winding = np.asarray(
        [[-2.0, -2.0], [2.0, -2.0], [2.0, 2.0], [-2.0, 2.0], [-2.0, -2.0]],
        dtype=np.float64,
    )

    evenodd = toolpath_module._scanline_x_intervals(
        [outer, inner_same_winding],
        0.0,
        PathFillRule.EVENODD,
    )
    nonzero_solid = toolpath_module._scanline_x_intervals(
        [outer, inner_same_winding],
        0.0,
        PathFillRule.NONZERO,
    )
    nonzero_hole = toolpath_module._scanline_x_intervals(
        [outer, inner_same_winding[::-1].copy()],
        0.0,
        PathFillRule.NONZERO,
    )

    assert evenodd == pytest.approx([(-5.0, -2.0), (2.0, 5.0)])
    assert nonzero_solid == pytest.approx([(-5.0, 5.0)])
    assert nonzero_hole == pytest.approx(evenodd)


def test_native_cubic_hole_remains_nested_after_planning_flattening() -> None:
    document = ProjectDocument.new("Native donut", Bounds(0, 0, 100, 100))
    item = SceneObject.native_path(
        document.active_layer_id,
        NativePathGeometry(
            (
                _closed_square_subpath(0.5),
                _closed_cubic_circle_subpath(0.2),
            ),
            fill_rule=PathFillRule.EVENODD,
        ),
        transform=Transform(50.0, 50.0, 80.0, 80.0),
    )

    paths = toolpath_module.object_polylines(item)

    assert len(paths) == 2
    assert all(path.closed for path in paths)
    assert toolpath_module._containment_depths(paths) == [0, 1]
    ordered = toolpath_module._containment_aware_source_order(paths)
    assert np.ptp(ordered[0].points[:, 0]) < np.ptp(ordered[1].points[:, 0])


def test_raster_layer_scans_vector_silhouette_and_image_assets(tmp_path: Path):
    document = make_document()
    document.layers[0].mode = LayerMode.RASTER
    document.layers[0].line_interval_mm = 2.0

    job = generate_project_gcode(document, LaserSettings())
    assert job.path_count > 10
    assert "\"mode\":\"raster\"" in job.text
    assert job.plan is not None
    assert any(
        not move.laser_on and not move.rapid and move.layer_name == "Line 01"
        for move in job.plan.moves
    )

    image_path = tmp_path / "pixels.png"
    image = np.full((10, 20), 255, dtype=np.uint8)
    image[:, 4:16] = 0
    assert cv2.imwrite(str(image_path), image)
    document.objects = [
        SceneObject(
            name="Pixels",
            kind="image",
            layer_id=document.active_layer_id,
            geometry={"asset": str(image_path)},
            transform={
                "x_mm": 110,
                "y_mm": 110,
                "width_mm": 40,
                "height_mm": 20,
            },
        )
    ]
    image_job = generate_project_gcode(document, LaserSettings())
    assert image_job.plan is not None and image_job.plan.powered
    assert image_job.bounds_mm[0] == pytest.approx(98.0)
    assert image_job.bounds_mm[2] == pytest.approx(122.0)


def test_image_raster_uses_contiguous_serpentine_rows_and_off_overscan(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "alternating.png"
    image = np.array([[0, 255, 0, 255], [0, 255, 0, 255]], dtype=np.uint8)
    assert cv2.imwrite(str(image_path), image)
    document = make_image_document(
        image_path,
        width_mm=4,
        height_mm=2,
        overscan_percent=25,
    )

    laser = LaserSettings(power_max=1000, travel_feed_mm_min=3000)
    job = generate_project_gcode(
        document,
        laser,
    )

    expected_rows = "\n".join(
        [
            "G0 X47 Y49.5 F3000",
            "G1 X48 Y49.5 F600",
            "M4 S100",
            "G1 X49 Y49.5 F600",
            "M5",
            "G1 X50 Y49.5 F600",
            "M4 S100",
            "G1 X51 Y49.5 F600",
            "M5",
            "G1 X52 Y49.5 F600",
            "G1 X53 Y49.5 F600",
        ]
    )
    assert expected_rows in job.text
    assert "G0 X53 Y50.5 F3000\nG1 X52 Y50.5 F600" in job.text
    assert job.plan is not None
    assert job.plan.planner_mode == "nearest path + fixed raster rows"
    assert job.plan.cut_distance_mm == pytest.approx(4.0)
    assert job.cut_length_mm == pytest.approx(4.0)
    assert all(not move.laser_on for move in job.plan.moves if move.rapid)
    assert all(
        not move.laser_on
        for move in job.plan.moves
        if move.end_x < 48.0 or move.end_x > 52.0
    )
    preflight = MachineService(
        MachineSettings(backend="serial", allow_motion=True),
        laser,
        hardware_enabled=True,
    ).preflight_program(job.text)
    assert preflight.requires_laser_authorization
    assert preflight.lines[-1] == "M5"


def test_honeycomb_local_raster_rows_are_sampled_locally_then_rigidly_placed():
    document = ProjectDocument.new(
        "Local raster",
        Bounds(0, 0, 100, 100),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 10
    layer.overscan_percent = 0
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(20, 30),
            width_mm=20,
            height_mm=20,
            corner_radius_mm=0,
        )
    )
    frame = honeycomb_frame()

    job = generate_project_gcode(
        document,
        LaserSettings(boundary_margin_mm=0),
        optimize_order=False,
        start_position=(0, 0),
        coordinate_frame=frame,
        machine_work_area=WorkArea(0, 120, 0, 160),
    )

    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    # Local horizontal rows become vertical machine rows under this +90-degree frame.
    assert all(move.start_x == pytest.approx(move.end_x) for move in powered)
    assert sorted({move.start_x for move in powered}) == pytest.approx([70.0, 80.0])
    assert all(abs(move.end_y - move.start_y) == pytest.approx(20.0) for move in powered)


def test_honeycomb_local_raster_overscan_is_checked_after_placement():
    document = ProjectDocument.new(
        "Local raster escape",
        Bounds(0, 0, 100, 100),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 10
    layer.overscan_percent = 50
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(20, 30),
            width_mm=20,
            height_mm=20,
            corner_radius_mm=0,
        )
    )

    with pytest.raises(SafetyError, match="placed raster overscan path"):
        generate_project_gcode(
            document,
            LaserSettings(boundary_margin_mm=0),
            coordinate_frame=honeycomb_frame(),
            machine_work_area=WorkArea(0, 120, 0, 75),
        )


def test_honeycomb_local_raster_uses_explicit_guarded_polygon() -> None:
    document = ProjectDocument.new(
        "Full support raster",
        Bounds(0, 0, 190, 190),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 2
    layer.overscan_percent = 0
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(185, 185),
            width_mm=10,
            height_mm=10,
            corner_radius_mm=0,
        )
    )
    frame = honeycomb_frame(
        origin=(28, 40),
        x_axis=(1, 0),
        y_axis=(0, 1),
        width=190,
        height=190,
    )
    polygon = ((18.0, 30.0), (228.0, 30.0), (228.0, 240.0), (18.0, 240.0))
    laser = LaserSettings(
        boundary_margin_mm=0,
        guarded_output_polygon_mm=polygon,
    )

    job = generate_project_gcode(
        document,
        laser,
        coordinate_frame=frame,
        machine_work_area=WorkArea(10, 210, 10, 210),
        guarded_output_polygon_mm=polygon,
        start_position=(28, 40),
    )

    assert job.plan is not None and job.plan.powered
    assert all(
        208.0 <= coordinate <= 218.0
        for move in job.plan.moves
        if move.laser_on
        for coordinate in (move.start_x, move.end_x)
    )
    assert all(
        220.0 <= coordinate <= 230.0
        for move in job.plan.moves
        if move.laser_on
        for coordinate in (move.start_y, move.end_y)
    )

    restricted_polygon = (
        (18.0, 30.0),
        (215.0, 30.0),
        (215.0, 240.0),
        (18.0, 240.0),
    )
    restricted_laser = LaserSettings(
        boundary_margin_mm=0,
        guarded_output_polygon_mm=restricted_polygon,
    )
    with pytest.raises(SafetyError, match="placed raster overscan path"):
        generate_project_gcode(
            document,
            restricted_laser,
            coordinate_frame=frame,
            machine_work_area=WorkArea(10, 210, 10, 210),
            guarded_output_polygon_mm=restricted_polygon,
            start_position=(28, 40),
        )


def test_honeycomb_local_image_raster_preserves_local_sampling(tmp_path: Path):
    image_path = tmp_path / "local-image.png"
    assert cv2.imwrite(str(image_path), np.zeros((2, 4), dtype=np.uint8))
    document = make_image_document(
        image_path,
        width_mm=4,
        height_mm=2,
        line_interval_mm=1,
        work_area=Bounds(0, 0, 100, 100),
    )
    document.coordinate_space = CoordinateSpace.HONEYCOMB_LOCAL
    document.objects[0].transform.x_mm = 20
    document.objects[0].transform.y_mm = 30

    job = generate_project_gcode(
        document,
        LaserSettings(boundary_margin_mm=0),
        optimize_order=False,
        start_position=(0, 0),
        coordinate_frame=honeycomb_frame(),
        machine_work_area=WorkArea(0, 120, 0, 160),
    )

    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    assert all(move.start_x == pytest.approx(move.end_x) for move in powered)
    assert sorted({move.start_x for move in powered}) == pytest.approx([69.5, 70.5])
    assert all(abs(move.end_y - move.start_y) == pytest.approx(4.0) for move in powered)


def test_image_source_top_matches_canvas_machine_y_with_mirrors_and_rotation(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "top-black.png"
    image = np.array([[0, 0], [255, 255]], dtype=np.uint8)
    assert cv2.imwrite(str(image_path), image)

    document = make_image_document(image_path, width_mm=2, height_mm=2)
    job = generate_project_gcode(document, LaserSettings(power_max=1000))
    assert job.plan is not None
    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    assert all(move.start_y == pytest.approx(50.5) for move in powered)
    assert all(move.end_y == pytest.approx(50.5) for move in powered)

    document.objects[0].transform.mirror_y = True
    mirrored = generate_project_gcode(document, LaserSettings(power_max=1000))
    assert mirrored.plan is not None
    mirrored_powered = [move for move in mirrored.plan.moves if move.laser_on]
    assert mirrored_powered
    assert all(move.start_y == pytest.approx(49.5) for move in mirrored_powered)
    assert all(move.end_y == pytest.approx(49.5) for move in mirrored_powered)

    document.objects[0].transform.mirror_y = False
    document.objects[0].transform.rotation_deg = 90
    rotated = generate_project_gcode(document, LaserSettings(power_max=1000))
    assert rotated.plan is not None
    rotated_powered = [move for move in rotated.plan.moves if move.laser_on]
    assert rotated_powered
    powered_midpoint_x = np.mean(
        [(move.start_x + move.end_x) / 2.0 for move in rotated_powered]
    )
    assert powered_midpoint_x < 50.0

    document.objects[0].transform.mirror_x = True
    document.objects[0].transform.mirror_y = True
    document.objects[0].transform.rotation_deg = 37
    mirrored_rotated = generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
    )
    assert mirrored_rotated.plan is not None
    transformed_powered = [
        move for move in mirrored_rotated.plan.moves if move.laser_on
    ]
    centroid = np.mean(
        [
            [(move.start_x + move.end_x) / 2.0, (move.start_y + move.end_y) / 2.0]
            for move in transformed_powered
        ],
        axis=0,
    )
    expected_top_direction = np.array(
        [math.sin(math.radians(37)), -math.cos(math.radians(37))]
    )
    assert float(np.dot(centroid - np.array([50.0, 50.0]), expected_top_direction)) > 0


@pytest.mark.parametrize("scan_angle_deg", [0.0, 37.0, 90.0])
def test_image_raster_honors_absolute_machine_scan_angle(
    tmp_path: Path,
    scan_angle_deg: float,
) -> None:
    image_path = tmp_path / f"black-{scan_angle_deg:g}.png"
    assert cv2.imwrite(str(image_path), np.zeros((3, 4), dtype=np.uint8))
    document = make_image_document(
        image_path,
        width_mm=8,
        height_mm=6,
        line_interval_mm=1,
    )
    document.objects[0].transform.rotation_deg = 23
    document.layers[0].scan_angle_deg = scan_angle_deg

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.plan is not None
    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    radians = math.radians(scan_angle_deg)
    direction = np.array([math.cos(radians), math.sin(radians)])
    for move in powered:
        vector = np.array(
            [move.end_x - move.start_x, move.end_y - move.start_y]
        )
        cross_product = direction[0] * vector[1] - direction[1] * vector[0]
        assert abs(float(cross_product)) <= 1e-3


def test_non_integral_image_dimensions_keep_exact_physical_pitch(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "non-integral.png"
    assert cv2.imwrite(str(image_path), np.zeros((3, 3), dtype=np.uint8))
    document = make_image_document(
        image_path,
        width_mm=5.0,
        height_mm=4.5,
        line_interval_mm=2.0,
    )

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.plan is not None
    powered = [move for move in job.plan.moves if move.laser_on]
    row_coordinates = sorted({round(move.start_y, 6) for move in powered})
    assert row_coordinates == pytest.approx([48.0, 50.0, 52.0])
    assert np.diff(row_coordinates) == pytest.approx([2.0, 2.0])
    assert all(move.distance_mm == pytest.approx(5.0) for move in powered)
    assert job.cut_length_mm == pytest.approx(15.0)

    assert cv2.imwrite(
        str(image_path),
        np.array([[255, 0, 255]], dtype=np.uint8),
    )
    horizontal = make_image_document(
        image_path,
        width_mm=5.0,
        height_mm=1.0,
        line_interval_mm=2.0,
    )
    horizontal_job = generate_project_gcode(
        horizontal,
        LaserSettings(power_max=1000),
    )
    assert horizontal_job.plan is not None
    horizontal_powered = [
        move for move in horizontal_job.plan.moves if move.laser_on
    ]
    assert len(horizontal_powered) == 1
    assert horizontal_powered[0].distance_mm == pytest.approx(2.0)


def test_vector_raster_islands_share_one_constant_feed_row() -> None:
    document = ProjectDocument.new("Raster islands", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 10
    layer.overscan_percent = 10
    layer.speed_mm_min = 600
    document.add_object(
        SceneObject(
            name="Islands",
            kind="path",
            layer_id=layer.id,
            transform={
                "x_mm": 50,
                "y_mm": 50,
                "width_mm": 20,
                "height_mm": 10,
            },
            geometry={
                "polylines": [
                    {
                        "points": [
                            [-0.5, -0.5],
                            [-0.1, -0.5],
                            [-0.1, 0.5],
                            [-0.5, 0.5],
                        ],
                        "closed": True,
                    },
                    {
                        "points": [
                            [0.1, -0.5],
                            [0.5, -0.5],
                            [0.5, 0.5],
                            [0.1, 0.5],
                        ],
                        "closed": True,
                    },
                ]
            },
        )
    )

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    expected_row = "\n".join(
        [
            "G0 X38 Y50 F3000",
            "G1 X40 Y50 F600",
            "M4 S100",
            "G1 X48 Y50 F600",
            "M5",
            "G1 X52 Y50 F600",
            "M4 S100",
            "G1 X60 Y50 F600",
            "M5",
            "G1 X62 Y50 F600",
        ]
    )
    assert expected_row in job.text
    assert job.plan is not None
    row_moves = [move for move in job.plan.moves if move.end_y == pytest.approx(50)]
    assert sum(move.rapid for move in row_moves) == 1
    assert any(not move.rapid and not move.laser_on for move in row_moves)


def test_raster_spot_correction_bounds_include_full_scan_row(tmp_path: Path) -> None:
    image_path = tmp_path / "edge.png"
    assert cv2.imwrite(str(image_path), np.zeros((1, 2), dtype=np.uint8))
    document = make_image_document(
        image_path,
        width_mm=10,
        height_mm=1,
        work_area=Bounds(0, 0, 100, 100),
    )
    document.objects[0].transform.x_mm = 10

    with pytest.raises(
        SafetyError,
        match="raster overscan path after laser spot correction",
    ):
        generate_project_gcode(
            document,
            LaserSettings(power_max=1000, spot_offset_x_mm=10),
        )


def test_image_raster_dithers_mid_gray_at_configured_physical_pitch(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "gray.png"
    assert cv2.imwrite(str(image_path), np.full((8, 8), 128, dtype=np.uint8))
    document = make_image_document(image_path, width_mm=8, height_mm=8)

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert "Raster tone: deterministic 8x8 ordered grayscale dither" in job.text
    assert '"raster_tone":"ordered-dither-8x8"' in job.text
    assert job.cut_length_mm == pytest.approx(32.0)
    assert job.bounds_mm == pytest.approx((46.0, 46.5, 54.0, 53.5))


def test_area_prefilter_makes_checker_minification_phase_and_resolution_invariant(
    tmp_path: Path,
) -> None:
    powered_signatures: list[list[tuple[float, float, float, float]]] = []
    for size, phase in ((1001, 0), (1001, 1), (2002, 0), (2002, 1)):
        coordinates = np.arange(size, dtype=np.uint16)
        checker = (
            ((coordinates[:, None] + coordinates[None, :] + phase) % 2) * 255
        ).astype(np.uint8)
        image_path = tmp_path / f"checker-{size}-{phase}.png"
        assert cv2.imwrite(str(image_path), checker)
        document = make_image_document(image_path, width_mm=8, height_mm=8)
        document.objects[0].transform.rotation_deg = 37
        document.layers[0].scan_angle_deg = 13

        job = generate_project_gcode(document, LaserSettings(power_max=1000))

        assert job.plan is not None
        powered_signatures.append(
            [
                (move.start_x, move.start_y, move.end_x, move.end_y)
                for move in job.plan.moves
                if move.laser_on
            ]
        )
        assert job.cut_length_mm == pytest.approx(31.891261, abs=1e-5)

    assert all(
        signature == powered_signatures[0]
        for signature in powered_signatures[1:]
    )


def test_image_raster_composites_transparency_onto_white(tmp_path: Path) -> None:
    image_path = tmp_path / "alpha.png"
    image = np.zeros((1, 2, 4), dtype=np.uint8)
    image[0, 0, 3] = 0
    image[0, 1, 3] = 255
    assert cv2.imwrite(str(image_path), image)
    document = make_image_document(image_path, width_mm=2, height_mm=1)

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.bounds_mm == pytest.approx((50.0, 50.0, 51.0, 50.0))
    assert job.cut_length_mm == pytest.approx(1.0)

    image[:, :, 3] = 0
    assert cv2.imwrite(str(image_path), image)
    with pytest.raises(ValueError, match="no engravable pixels after dithering"):
        generate_project_gcode(document, LaserSettings(power_max=1000))


def test_prepared_job_records_and_rechecks_external_raster_content(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "mutable.png"
    assert cv2.imwrite(str(image_path), np.zeros((4, 4), dtype=np.uint8))
    document = make_image_document(image_path, width_mm=4, height_mm=4)

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert len(job.raster_assets) == 1
    assert len(job.raster_assets[0].sha256) == 64
    verify_project_job_assets(job)

    assert cv2.imwrite(str(image_path), np.full((4, 4), 255, dtype=np.uint8))
    with pytest.raises(ValueError, match="changed on disk.*Regenerate"):
        verify_project_job_assets(job)


def test_prepared_frame_is_invalidated_when_external_raster_moves(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "frame-source.png"
    assert cv2.imwrite(str(image_path), np.zeros((2, 2), dtype=np.uint8))
    document = make_image_document(image_path, width_mm=2, height_mm=2)
    frame = generate_project_frame(document, LaserSettings(power_max=1000))
    moved = image_path.with_name("moved.png")
    image_path.rename(moved)

    with pytest.raises(ValueError, match="unavailable or no longer valid"):
        verify_project_job_assets(frame)


@pytest.mark.parametrize("suffix", [".jpg", ".bmp"])
def test_raster_dimension_probe_supports_import_dialog_formats(
    tmp_path: Path,
    suffix: str,
) -> None:
    image_path = tmp_path / f"probe{suffix}"
    assert cv2.imwrite(str(image_path), np.zeros((3, 4), dtype=np.uint8))
    document = make_image_document(image_path, width_mm=4, height_mm=3)

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.plan is not None and job.plan.powered


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


@pytest.mark.parametrize("color_type,bit_depth", [(3, 8), (0, 1)])
def test_bounded_raster_contract_supports_palette_and_low_bit_grayscale_png(
    tmp_path: Path,
    color_type: int,
    bit_depth: int,
) -> None:
    path = tmp_path / f"png-{color_type}-{bit_depth}.png"
    header = struct.pack(">IIBBBBB", 8, 8, bit_depth, color_type, 0, 0, 0)
    if color_type == 3:
        palette = _png_chunk(b"PLTE", b"\x00\x00\x00\xff\xff\xff")
        rows = b"".join(b"\x00" + bytes([0, 1] * 4) for _ in range(8))
    else:
        palette = b""
        rows = b"".join(b"\x00\x55" for _ in range(8))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + palette
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )

    metadata = probe_raster_asset(path)
    image, identity = decode_raster_grayscale(path, metadata=metadata)

    assert (metadata.width, metadata.height) == (8, 8)
    assert image.shape == (8, 8)
    assert set(np.unique(image)) == {0, 255}
    assert len(identity.sha256) == 64


def test_tiff_is_rejected_by_the_shared_raster_format_contract(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "unsupported.tiff"
    assert cv2.imwrite(str(image_path), np.zeros((3, 4), dtype=np.uint8))
    document = make_image_document(image_path, width_mm=4, height_mm=3)

    with pytest.raises(ValueError, match="use PNG, JPEG, or BMP"):
        generate_project_gcode(document, LaserSettings(power_max=1000))


def test_zero_power_raster_keeps_full_row_motion_laser_off(tmp_path: Path) -> None:
    image_path = tmp_path / "black.png"
    assert cv2.imwrite(str(image_path), np.zeros((1, 4), dtype=np.uint8))
    document = make_image_document(
        image_path,
        width_mm=4,
        height_mm=1,
        overscan_percent=25,
    )
    document.layers[0].power_percent = 0

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert not re.search(r"\bM[34]\b", job.text)
    assert "G1 X48 Y50 F600\nG1 X52 Y50 F600\nG1 X53 Y50 F600" in job.text
    assert job.plan is not None and not job.plan.powered
    assert all(not move.laser_on for move in job.plan.moves)
    assert job.cut_length_mm == pytest.approx(0.0)
    assert job.plan.cut_distance_mm == pytest.approx(0.0)
    assert job.layer_summaries[0]["cut_length_mm"] == pytest.approx(0.0)


@pytest.mark.parametrize("mode", [LayerMode.LINE, LayerMode.FILL])
@pytest.mark.parametrize("power_percent", [0.0, 0.04])
def test_zero_controller_power_vector_metrics_match_exact_laser_off_motion(
    mode: LayerMode,
    power_percent: float,
) -> None:
    document = ProjectDocument.new("Off vectors", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode = mode
    layer.power_percent = power_percent
    layer.line_interval_mm = 2
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(50, 50),
            width_mm=20,
            height_mm=10,
        )
    )

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert layer.controller_power(1000) == 0
    assert not re.search(r"\bM[34]\b", job.text)
    assert job.plan is not None and not job.plan.powered
    assert job.cut_length_mm == pytest.approx(0.0)
    assert job.layer_summaries[0]["cut_length_mm"] == pytest.approx(0.0)
    assert job.travel_length_mm == pytest.approx(job.plan.travel_distance_mm)
    assert job.layer_summaries[0]["travel_length_mm"] == pytest.approx(
        job.plan.travel_distance_mm
    )


def test_dithered_raster_metrics_match_the_exact_program(tmp_path: Path) -> None:
    image_path = tmp_path / "tones.png"
    tones = np.random.default_rng(42).integers(0, 256, size=(8, 10), dtype=np.uint8)
    assert cv2.imwrite(str(image_path), tones)
    document = make_image_document(
        image_path,
        width_mm=10,
        height_mm=8,
        overscan_percent=10,
    )

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.plan is not None
    assert job.plan.warnings == ()
    assert job.cut_length_mm == pytest.approx(job.plan.cut_distance_mm)
    assert job.travel_length_mm == pytest.approx(job.plan.travel_distance_mm)
    assert job.path_count == sum(move.laser_on for move in job.plan.moves)
    assert all(
        document.work_area.contains(move.end_x, move.end_y)
        for move in job.plan.moves
    )


def test_image_raster_rejects_unbounded_sample_and_command_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    black_path = tmp_path / "black.png"
    assert cv2.imwrite(str(black_path), np.zeros((1, 1), dtype=np.uint8))
    oversized = make_image_document(
        black_path,
        width_mm=5000,
        height_mm=5000,
        work_area=Bounds(0, 0, 10_000, 10_000),
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            cv2,
            "imread",
            lambda *_args, **_kwargs: pytest.fail(
                "physical sample limits must be checked before image decode"
            ),
        )
        with pytest.raises(ValueError, match="row planner limit|sample planner limit"):
            generate_project_gcode(oversized, LaserSettings(power_max=1000))

    too_many_rows = make_image_document(
        black_path,
        width_mm=0.001,
        height_mm=190,
        line_interval_mm=0.001,
        work_area=Bounds(-100, -100, 200, 200),
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            cv2,
            "imread",
            lambda *_args, **_kwargs: pytest.fail(
                "row limits must be checked before image decode"
            ),
        )
        with pytest.raises(ValueError, match="row planner limit"):
            generate_project_gcode(too_many_rows, LaserSettings(power_max=1000))

    checker_path = tmp_path / "checker.png"
    checker = (np.indices((512, 512)).sum(axis=0) % 2 * 255).astype(np.uint8)
    assert cv2.imwrite(str(checker_path), checker)
    too_many_commands = make_image_document(
        checker_path,
        width_mm=512,
        height_mm=512,
        work_area=Bounds(0, 0, 600, 600),
    )
    with pytest.raises(ValueError, match="command stream limit"):
        generate_project_gcode(too_many_commands, LaserSettings(power_max=1000))


def test_source_image_dimensions_are_bounded_before_full_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    huge_path = tmp_path / "huge-header.png"
    huge_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + (100_000).to_bytes(4, "big")
        + (100_000).to_bytes(4, "big")
        + b"\x08\x00\x00\x00\x00"
    )
    document = make_image_document(huge_path, width_mm=2, height_mm=2)
    monkeypatch.setattr(
        cv2,
        "imdecode",
        lambda *_args, **_kwargs: pytest.fail(
            "oversized source headers must be rejected before cv2.imdecode"
        ),
    )

    with pytest.raises(ValueError, match="dimension limit|decoded bytes.*decode limit"):
        generate_project_gcode(document, LaserSettings(power_max=1000))


def test_vector_scanline_iteration_work_is_bounded_before_looping() -> None:
    document = ProjectDocument.new("Dense vector", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 0.001
    angles = np.linspace(0.0, 2.0 * math.pi, 401)
    points = np.column_stack([0.5 * np.cos(angles), 0.5 * np.sin(angles)])
    document.add_object(
        SceneObject(
            name="Many edges",
            kind="path",
            layer_id=layer.id,
            transform={
                "x_mm": 50,
                "y_mm": 50,
                "width_mm": 40,
                "height_mm": 40,
            },
            geometry={
                "polylines": [
                    {"points": points.tolist(), "closed": True}
                ]
            },
        )
    )

    with pytest.raises(ValueError, match="scanline edge tests"):
        generate_project_gcode(document, LaserSettings(power_max=1000))


def test_single_dense_vector_row_is_rejected_before_span_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ProjectDocument.new("Dense row", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 10
    layer.overscan_percent = 0
    item = SceneObject.rectangle(layer.id, center=(50, 50), width_mm=20, height_mm=1)
    document.add_object(item)
    x = np.linspace(40.0, 60.0, 130_001)
    y = 50.0 + np.where(np.arange(130_001) % 2, 0.25, -0.25)
    points = np.column_stack([x, y])
    points[-1] = points[0]
    dense = toolpath_module.Polyline(points, closed=True, source_tag="dense")
    monkeypatch.setattr(toolpath_module, "object_polylines", lambda _item: [dense])
    monkeypatch.setattr(
        toolpath_module,
        "_scanline_rows",
        lambda *_args, **_kwargs: pytest.fail(
            "aggregate command budget must reject before span construction"
        ),
    )

    with pytest.raises(ValueError, match="streamed commands.*command limit"):
        generate_project_gcode(document, LaserSettings(power_max=1000))


def test_raster_command_budget_aggregates_objects_and_passes_before_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ProjectDocument.new("Aggregate commands", Bounds(0, 0, 200, 200))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 1
    layer.passes = 110
    for x_position in (60, 140):
        document.add_object(
            SceneObject.rectangle(
                layer.id,
                center=(x_position, 100),
                width_mm=20,
                height_mm=100,
            )
        )
    monkeypatch.setattr(
        toolpath_module,
        "_scanline_rows",
        lambda *_args, **_kwargs: pytest.fail(
            "aggregate command limits must be checked before raster rows"
        ),
    )

    with pytest.raises(ValueError, match="streamed commands.*command limit"):
        generate_project_gcode(document, LaserSettings(power_max=1000))


def test_raster_sample_budget_is_aggregate_across_objects_before_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "one.png"
    assert cv2.imwrite(str(image_path), np.zeros((1, 1), dtype=np.uint8))
    document = ProjectDocument.new("Aggregate samples", Bounds(-4000, -4000, 4000, 4000))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 1
    for name in ("First", "Second"):
        document.add_object(
            SceneObject(
                name=name,
                kind=ObjectKind.IMAGE,
                layer_id=layer.id,
                geometry={"asset": str(image_path)},
                transform={
                    "x_mm": 0,
                    "y_mm": 0,
                    "width_mm": 5000,
                    "height_mm": 2000,
                },
            )
        )
    monkeypatch.setattr(
        toolpath_module,
        "decode_raster_grayscale",
        lambda *_args, **_kwargs: pytest.fail(
            "aggregate sample limits must be checked before decode"
        ),
    )

    with pytest.raises(ValueError, match="aggregate samples.*sample planner limit"):
        generate_project_gcode(document, LaserSettings(power_max=1000))


def test_large_vector_path_sets_use_recorded_source_order_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ProjectDocument.new("Many vector paths", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    polylines = []
    count = toolpath_module._MAX_NEAREST_ORDER_PATHS + 1
    for index in range(count):
        y = -0.49 + 0.98 * index / max(1, count - 1)
        polylines.append(
            {
                "points": [[-0.49, y], [-0.48, y]],
                "closed": False,
            }
        )
    document.add_object(
        SceneObject.path(
            layer.id,
            polylines,
            center=(50, 50),
            name="Many paths",
        )
    )
    monkeypatch.setattr(
        toolpath_module,
        "_nearest_order",
        lambda *_args, **_kwargs: pytest.fail(
            "quadratic nearest ordering must not run above its path limit"
        ),
    )

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.plan is not None
    assert job.plan.planner_mode == "source order (nearest path limit)"
    assert '"planner":"source order (nearest path limit)"' in job.text


def test_native_vector_command_budget_rejects_before_text_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ProjectDocument.new("Huge vector", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    document.add_object(
        SceneObject.line(layer.id, center=(15, 15), length_mm=10, name="Source")
    )
    oversized = Polyline(np.zeros((249_999, 2), dtype=np.float64))
    monkeypatch.setattr(
        toolpath_module,
        "_operation_paths",
        lambda *_args, **_kwargs: ([oversized], None),
    )
    monkeypatch.setattr(
        toolpath_module,
        "_fmt",
        lambda *_args, **_kwargs: pytest.fail(
            "G-code text construction must not begin beyond the command budget"
        ),
    )

    with pytest.raises(ValueError, match="more than 250,000 streamed commands"):
        generate_project_gcode(document, LaserSettings(power_max=1000))


def test_repeated_raster_asset_is_probed_and_decoded_once_per_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "shared.png"
    assert cv2.imwrite(str(path), np.zeros((100, 100), dtype=np.uint8))
    document = ProjectDocument.new("Repeated source", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 1.0
    layer.power_percent = 10.0
    for index in range(100):
        document.add_object(
            SceneObject(
                name=f"Copy {index}",
                kind=ObjectKind.IMAGE,
                layer_id=layer.id,
                geometry={"asset": str(path)},
                transform={
                    "x_mm": 50,
                    "y_mm": 50,
                    "width_mm": 1,
                    "height_mm": 1,
                },
            )
        )

    original_probe = toolpath_module.probe_raster_asset
    original_decode = toolpath_module.decode_raster_grayscale
    calls = {"probe": 0, "decode": 0}

    def counted_probe(*args, **kwargs):
        calls["probe"] += 1
        return original_probe(*args, **kwargs)

    def counted_decode(*args, **kwargs):
        calls["decode"] += 1
        return original_decode(*args, **kwargs)

    monkeypatch.setattr(toolpath_module, "probe_raster_asset", counted_probe)
    monkeypatch.setattr(toolpath_module, "decode_raster_grayscale", counted_decode)

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert calls == {"probe": 1, "decode": 1}
    assert len(job.raster_assets) == 100
    assert len({identity.sha256 for identity in job.raster_assets}) == 1

    original_payload = toolpath_module.read_raster_asset_payload
    payload_calls = 0

    def counted_payload(*args, **kwargs):
        nonlocal payload_calls
        payload_calls += 1
        return original_payload(*args, **kwargs)

    monkeypatch.setattr(toolpath_module, "read_raster_asset_payload", counted_payload)
    frame = generate_project_frame(document, LaserSettings(power_max=1000))

    assert payload_calls == 1
    assert len(frame.raster_assets) == 1
    assert frame.raster_assets[0].sha256 == job.raster_assets[0].sha256


@pytest.mark.parametrize(
    ("count", "encoded_bytes", "decoded_bytes", "message"),
    [
        (3, 32 * 1024 * 1024, 1, "aggregate encoded bytes"),
        (5, 1, 16 * 1024 * 1024, "aggregate decoded bytes"),
        (65, 1, 1, "unique raster assets"),
    ],
)
def test_unique_raster_source_budgets_reject_before_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
    encoded_bytes: int,
    decoded_bytes: int,
    message: str,
) -> None:
    document = ProjectDocument.new("Many sources", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 1.0
    for index in range(count):
        document.add_object(
            SceneObject(
                name=f"Image {index}",
                kind=ObjectKind.IMAGE,
                layer_id=layer.id,
                geometry={"asset": str(tmp_path / f"source-{index}.png")},
                transform={
                    "x_mm": 50,
                    "y_mm": 50,
                    "width_mm": 1,
                    "height_mm": 1,
                },
            )
        )

    def metadata(path: str | Path) -> RasterAssetMetadata:
        source = Path(path).absolute()
        return RasterAssetMetadata(
            path=str(source),
            format="png",
            width=1,
            height=1,
            raw_width=1,
            raw_height=1,
            bit_depth=8,
            channels=1,
            orientation=1,
            encoded_bytes=encoded_bytes,
            decoded_bytes=decoded_bytes,
            mtime_ns=0,
        )

    monkeypatch.setattr(toolpath_module, "probe_raster_asset", metadata)
    monkeypatch.setattr(
        toolpath_module,
        "decode_raster_grayscale",
        lambda *_args, **_kwargs: pytest.fail("source budgets must reject before decode"),
    )
    monkeypatch.setattr(
        toolpath_module,
        "read_raster_asset_payload",
        lambda *_args, **_kwargs: pytest.fail(
            "source budgets must reject before identity reads"
        ),
    )

    with pytest.raises(ValueError, match=message):
        generate_project_gcode(document, LaserSettings(power_max=1000))
    with pytest.raises(ValueError, match=message):
        generate_project_frame(document, LaserSettings(power_max=1000))


def test_raster_image_requires_a_decodable_asset() -> None:
    document = ProjectDocument.new("Missing raster", Bounds(0, 0, 100, 100))
    document.layers[0].mode = LayerMode.RASTER
    document.add_object(
        SceneObject(
            name="Missing",
            kind="image",
            layer_id=document.active_layer_id,
            geometry={"asset": "/does/not/exist.png"},
        )
    )

    with pytest.raises(ValueError, match="does not exist"):
        generate_project_gcode(document, LaserSettings())


def test_raster_overscan_is_bounds_checked() -> None:
    document = ProjectDocument.new("Raster edge", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 2.0
    layer.overscan_percent = 10.0
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(50, 50),
            width_mm=100,
            height_mm=20,
        )
    )

    with pytest.raises(SafetyError, match="raster overscan"):
        generate_project_gcode(document, LaserSettings(boundary_margin_mm=0))


def test_text_output_is_rejected_until_outline_conversion_exists():
    document = ProjectDocument.new("Text", Bounds(15, 15, 205, 205))
    document.add_object(
        SceneObject(
            name="Label",
            kind="text",
            layer_id=document.active_layer_id,
            geometry={"text": "TEST", "font_family": "Sans Serif"},
        )
    )

    with pytest.raises(ValueError, match="Label"):
        generate_project_gcode(document, LaserSettings())


def test_pass_count_repeats_each_path():
    document = make_document()
    document.layers[0].passes = 3

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.path_count == 3
    assert job.text.count("M4 S100") == 3
    assert job.layer_summaries[0]["passes"] == 3


def test_layer_priority_controls_emission_order():
    document = ProjectDocument.new("Order", Bounds(15, 15, 205, 205))
    first = document.layers[0]
    first.name = "First"
    first.priority = 10
    second = document.add_layer(name="Second")
    second.priority = 0
    document.add_object(SceneObject.rectangle(first.id, center=(60, 60), width_mm=10, height_mm=10))
    document.add_object(SceneObject.rectangle(second.id, center=(140, 140), width_mm=10, height_mm=10))

    job = generate_project_gcode(document, LaserSettings())

    assert job.text.index("; Layer Second") < job.text.index("; Layer First")


def test_planner_choice_is_recorded_in_exact_preview_plan() -> None:
    optimized = generate_project_gcode(make_document(), LaserSettings(), optimize_order=True)
    source = generate_project_gcode(make_document(), LaserSettings(), optimize_order=False)

    assert optimized.plan is not None
    assert source.plan is not None
    assert optimized.plan.planner_mode == "nearest path"
    assert source.plan.planner_mode == "source order"
    assert optimized.plan.source_order_travel_mm is not None
    assert optimized.plan.planner_savings_mm >= 0
