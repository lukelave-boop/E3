from __future__ import annotations

import struct
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import LaserSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.project import (
    Bounds,
    CoordinateSpace,
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    ProjectJob,
    SceneObject,
    Transform,
    generate_project_gcode,
)


@dataclass(frozen=True, slots=True)
class RejectionResult:
    exception_type: str
    message: str


EXTENDED_CASE_NAMES = (
    "vector_fill",
    "monochrome_raster",
    "grayscale_raster",
    "spot_offset",
    "honeycomb_guarded_placement",
    "honeycomb_missing_frame_rejected",
    "guarded_placement_escape_rejected",
    "guarded_controller_escape_rejected",
)


def _layer(
    *,
    layer_id: str,
    name: str,
    mode: LayerMode,
    speed_mm_min: float,
    power_percent: float,
    line_interval_mm: float = 5.0,
    scan_angle_deg: float = 0.0,
    overscan_percent: float = 0.0,
) -> OperationLayer:
    return OperationLayer(
        id=layer_id,
        name=name,
        color="#89B85C",
        mode=mode,
        speed_mm_min=speed_mm_min,
        power_percent=power_percent,
        passes=1,
        line_interval_mm=line_interval_mm,
        scan_angle_deg=scan_angle_deg,
        overscan_percent=overscan_percent,
        vector_power_correction=0.0,
        raster_power_correction=0.0,
        air_assist=False,
        output_enabled=True,
        visible=True,
        priority=0,
    )


def _document(
    *,
    project_id: str,
    name: str,
    layer: OperationLayer,
    objects: list[SceneObject],
    coordinate_space: CoordinateSpace = CoordinateSpace.MACHINE,
    work_area: Bounds | None = None,
) -> ProjectDocument:
    return ProjectDocument(
        id=project_id,
        name=name,
        work_area=work_area or Bounds(0.0, 0.0, 100.0, 100.0),
        coordinate_space=coordinate_space,
        layers=[layer],
        objects=objects,
        created_at="2026-08-19T00:00:00+00:00",
        modified_at="2026-08-19T00:00:00+00:00",
        revision=0,
    )


def _laser(**changes: object) -> LaserSettings:
    values: dict[str, object] = {
        "power_mode": "M4",
        "power_max": 1000,
        "default_power": 100,
        "frame_power": 0,
        "travel_feed_mm_min": 3000.0,
        "engrave_feed_mm_min": 1200.0,
        "curve_tolerance_mm": 0.15,
        "boundary_margin_mm": 0.0,
        "guarded_output_polygon_mm": None,
        "spot_offset_x_mm": 0.0,
        "spot_offset_y_mm": 0.0,
        "arm_timeout_seconds": 60,
        "allow_low_power_frame": False,
        "return_to_photo_position": False,
        "preview_acceleration_mm_s2": 500.0,
        "preview_command_delay_ms": 0.0,
    }
    values.update(changes)
    return LaserSettings(**values)


def _frame() -> HoneycombCoordinateFrame:
    return HoneycombCoordinateFrame(
        origin_machine_mm=(70.0, 30.0),
        x_axis_machine=(0.0, 1.0),
        y_axis_machine=(-1.0, 0.0),
        width_mm=80.0,
        height_mm=60.0,
        provenance_digest="b" * 64,
    )


def _guarded_polygon() -> tuple[tuple[float, float], ...]:
    return (
        (40.0, 20.0),
        (80.0, 20.0),
        (80.0, 80.0),
        (40.0, 80.0),
    )


def _vector_fill_job() -> ProjectJob:
    layer = _layer(
        layer_id="layer-golden-fill",
        name="Golden Fill",
        mode=LayerMode.FILL,
        speed_mm_min=1300.0,
        power_percent=18.0,
        line_interval_mm=5.0,
        scan_angle_deg=0.0,
    )
    rectangle = SceneObject(
        id="object-golden-fill",
        name="Golden filled rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(50.0, 50.0, 30.0, 20.0),
        geometry={"corner_radius_mm": 0.0},
    )
    return generate_project_gcode(
        _document(
            project_id="project-golden-fill",
            name="Golden vector fill",
            layer=layer,
            objects=[rectangle],
        ),
        _laser(),
        optimize_order=True,
        start_position=(0.0, 0.0),
    )


def _monochrome_raster_job() -> ProjectJob:
    layer = _layer(
        layer_id="layer-golden-raster",
        name="Golden Raster",
        mode=LayerMode.RASTER,
        speed_mm_min=1000.0,
        power_percent=20.0,
        line_interval_mm=5.0,
        scan_angle_deg=0.0,
        overscan_percent=10.0,
    )
    rectangle = SceneObject(
        id="object-golden-raster",
        name="Golden raster rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(40.0, 40.0, 20.0, 20.0),
        geometry={"corner_radius_mm": 0.0},
    )
    return generate_project_gcode(
        _document(
            project_id="project-golden-raster",
            name="Golden monochrome raster",
            layer=layer,
            objects=[rectangle],
        ),
        _laser(),
        optimize_order=True,
        start_position=(0.0, 0.0),
    )


def _bmp24_bytes(rows: list[list[int]]) -> bytes:
    if not rows or not rows[0]:
        raise ValueError("BMP rows must be non-empty")
    height = len(rows)
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("BMP rows must form a rectangle")
    stride = ((width * 3 + 3) // 4) * 4
    pixel_bytes = stride * height
    header = struct.pack("<2sIHHI", b"BM", 54 + pixel_bytes, 0, 0, 54)
    dib = struct.pack(
        "<IIIHHIIIIII",
        40,
        width,
        height,
        1,
        24,
        0,
        pixel_bytes,
        2835,
        2835,
        0,
        0,
    )
    payload = bytearray()
    for row in reversed(rows):
        scan = bytearray()
        for value in row:
            gray = max(0, min(255, int(value)))
            scan.extend((gray, gray, gray))
        scan.extend(b"\x00" * (stride - len(scan)))
        payload.extend(scan)
    return header + dib + bytes(payload)


def _grayscale_raster_job() -> ProjectJob:
    rows = [
        [0, 64, 128, 255],
        [255, 192, 96, 0],
        [32, 224, 160, 80],
        [255, 255, 0, 0],
    ]
    with tempfile.TemporaryDirectory(prefix="e3-planning-golden-") as temp_dir:
        asset = Path(temp_dir) / "golden-grayscale.bmp"
        asset.write_bytes(_bmp24_bytes(rows))

        layer = _layer(
            layer_id="layer-golden-grayscale",
            name="Golden Grayscale",
            mode=LayerMode.RASTER,
            speed_mm_min=900.0,
            power_percent=25.0,
            line_interval_mm=5.0,
            scan_angle_deg=0.0,
            overscan_percent=0.0,
        )
        image = SceneObject(
            id="object-golden-grayscale",
            name="Golden grayscale image",
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(50.0, 50.0, 20.0, 20.0),
            geometry={"asset": str(asset)},
        )
        return generate_project_gcode(
            _document(
                project_id="project-golden-grayscale",
                name="Golden grayscale raster",
                layer=layer,
                objects=[image],
            ),
            _laser(),
            optimize_order=True,
            start_position=(0.0, 0.0),
        )


def _spot_offset_job() -> ProjectJob:
    layer = _layer(
        layer_id="layer-golden-spot",
        name="Golden Spot Offset",
        mode=LayerMode.LINE,
        speed_mm_min=1200.0,
        power_percent=30.0,
    )
    line = SceneObject(
        id="object-golden-spot",
        name="Golden spot line",
        kind=ObjectKind.LINE,
        layer_id=layer.id,
        transform=Transform(50.0, 40.0, 30.0, 1.0, rotation_deg=25.0),
        geometry={"points": [[-0.5, 0.0], [0.5, 0.0]]},
    )
    return generate_project_gcode(
        _document(
            project_id="project-golden-spot",
            name="Golden spot offset",
            layer=layer,
            objects=[line],
        ),
        _laser(spot_offset_x_mm=2.0, spot_offset_y_mm=-3.0),
        optimize_order=False,
        start_position=(0.0, 0.0),
    )


def _honeycomb_guarded_placement_job() -> ProjectJob:
    layer = _layer(
        layer_id="layer-golden-honeycomb",
        name="Golden Honeycomb",
        mode=LayerMode.LINE,
        speed_mm_min=1100.0,
        power_percent=28.0,
    )
    rectangle = SceneObject(
        id="object-golden-honeycomb",
        name="Golden local rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(20.0, 15.0, 20.0, 10.0),
        geometry={"corner_radius_mm": 0.0},
    )
    polygon = _guarded_polygon()
    laser = _laser(guarded_output_polygon_mm=polygon)
    return generate_project_gcode(
        _document(
            project_id="project-golden-honeycomb",
            name="Golden honeycomb guarded placement",
            layer=layer,
            objects=[rectangle],
            coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
            work_area=Bounds(0.0, 0.0, 80.0, 60.0),
        ),
        laser,
        optimize_order=False,
        start_position=(70.0, 30.0),
        coordinate_frame=_frame(),
        machine_work_area=WorkArea(0.0, 120.0, 0.0, 120.0),
        guarded_output_polygon_mm=polygon,
    )


def _expect_safety_error(action: Callable[[], object]) -> RejectionResult:
    try:
        action()
    except SafetyError as exc:
        return RejectionResult(type(exc).__name__, str(exc))
    raise AssertionError("Expected planning to reject with SafetyError")


def _honeycomb_missing_frame_rejected() -> RejectionResult:
    layer = _layer(
        layer_id="layer-golden-missing-frame",
        name="Golden Missing Frame",
        mode=LayerMode.LINE,
        speed_mm_min=1000.0,
        power_percent=20.0,
    )
    line = SceneObject(
        id="object-golden-missing-frame",
        name="Golden missing-frame line",
        kind=ObjectKind.LINE,
        layer_id=layer.id,
        transform=Transform(20.0, 20.0, 20.0, 1.0),
        geometry={"points": [[-0.5, 0.0], [0.5, 0.0]]},
    )
    document = _document(
        project_id="project-golden-missing-frame",
        name="Golden missing frame rejection",
        layer=layer,
        objects=[line],
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
        work_area=Bounds(0.0, 0.0, 80.0, 60.0),
    )
    return _expect_safety_error(lambda: generate_project_gcode(document, _laser()))


def _guarded_placement_escape_rejected() -> RejectionResult:
    layer = _layer(
        layer_id="layer-golden-placement-escape",
        name="Golden Placement Escape",
        mode=LayerMode.LINE,
        speed_mm_min=1000.0,
        power_percent=20.0,
    )
    rectangle = SceneObject(
        id="object-golden-placement-escape",
        name="Golden escaping local rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(75.0, 55.0, 10.0, 10.0),
        geometry={"corner_radius_mm": 0.0},
    )
    polygon = _guarded_polygon()
    document = _document(
        project_id="project-golden-placement-escape",
        name="Golden guarded placement escape rejection",
        layer=layer,
        objects=[rectangle],
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
        work_area=Bounds(0.0, 0.0, 80.0, 60.0),
    )
    laser = _laser(guarded_output_polygon_mm=polygon)
    return _expect_safety_error(
        lambda: generate_project_gcode(
            document,
            laser,
            coordinate_frame=_frame(),
            machine_work_area=WorkArea(0.0, 120.0, 0.0, 120.0),
            guarded_output_polygon_mm=polygon,
            start_position=(70.0, 30.0),
        )
    )


def _guarded_controller_escape_rejected() -> RejectionResult:
    layer = _layer(
        layer_id="layer-golden-controller-escape",
        name="Golden Controller Escape",
        mode=LayerMode.LINE,
        speed_mm_min=1000.0,
        power_percent=20.0,
    )
    rectangle = SceneObject(
        id="object-golden-controller-escape",
        name="Golden controller-escape rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(25.0, 24.5, 10.0, 9.0),
        geometry={"corner_radius_mm": 0.0},
    )
    polygon = _guarded_polygon()
    document = _document(
        project_id="project-golden-controller-escape",
        name="Golden controller escape rejection",
        layer=layer,
        objects=[rectangle],
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
        work_area=Bounds(0.0, 0.0, 80.0, 60.0),
    )
    laser = _laser(
        guarded_output_polygon_mm=polygon,
        spot_offset_x_mm=10.0,
    )
    return _expect_safety_error(
        lambda: generate_project_gcode(
            document,
            laser,
            coordinate_frame=_frame(),
            machine_work_area=WorkArea(0.0, 120.0, 0.0, 120.0),
            guarded_output_polygon_mm=polygon,
            start_position=(70.0, 30.0),
        )
    )


EXTENDED_CASE_BUILDERS = {
    "vector_fill": _vector_fill_job,
    "monochrome_raster": _monochrome_raster_job,
    "grayscale_raster": _grayscale_raster_job,
    "spot_offset": _spot_offset_job,
    "honeycomb_guarded_placement": _honeycomb_guarded_placement_job,
    "honeycomb_missing_frame_rejected": _honeycomb_missing_frame_rejected,
    "guarded_placement_escape_rejected": _guarded_placement_escape_rejected,
    "guarded_controller_escape_rejected": _guarded_controller_escape_rejected,
}
