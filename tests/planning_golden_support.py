from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from laser_aligner.config import LaserSettings
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

GOLDEN_ROOT = Path(__file__).parent / "golden" / "planning"
EXPECTED_ROOT = GOLDEN_ROOT / "expected"
CASE_NAMES = ("simple_rectangle",)


def _simple_rectangle_job() -> ProjectJob:
    layer = OperationLayer(
        id="layer-golden-line",
        name="Golden Line",
        color="#E35D6A",
        mode=LayerMode.LINE,
        speed_mm_min=1200.0,
        power_percent=25.0,
        passes=1,
        line_interval_mm=0.10,
        scan_angle_deg=0.0,
        overscan_percent=2.5,
        vector_power_correction=0.0,
        raster_power_correction=0.0,
        air_assist=False,
        output_enabled=True,
        visible=True,
        priority=0,
    )
    rectangle = SceneObject(
        id="object-golden-rectangle",
        name="Golden rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(
            x_mm=40.0,
            y_mm=30.0,
            width_mm=40.0,
            height_mm=25.0,
            rotation_deg=0.0,
            mirror_x=False,
            mirror_y=False,
        ),
        geometry={"corner_radius_mm": 0.0},
        visible=True,
        locked=False,
    )
    document = ProjectDocument(
        id="project-golden-simple-rectangle",
        name="Golden simple rectangle",
        work_area=Bounds(0.0, 0.0, 100.0, 100.0),
        coordinate_space=CoordinateSpace.MACHINE,
        layers=[layer],
        objects=[rectangle],
        created_at="2026-08-19T00:00:00+00:00",
        modified_at="2026-08-19T00:00:00+00:00",
        revision=0,
    )
    laser = LaserSettings(
        power_mode="M4",
        power_max=1000,
        default_power=100,
        frame_power=0,
        travel_feed_mm_min=3000.0,
        engrave_feed_mm_min=1200.0,
        curve_tolerance_mm=0.15,
        boundary_margin_mm=0.0,
        guarded_output_polygon_mm=None,
        spot_offset_x_mm=0.0,
        spot_offset_y_mm=0.0,
        arm_timeout_seconds=60,
        allow_low_power_frame=False,
        return_to_photo_position=False,
        preview_acceleration_mm_s2=500.0,
        preview_command_delay_ms=0.0,
    )
    return generate_project_gcode(
        document,
        laser,
        optimize_order=True,
        start_position=(0.0, 0.0),
    )


_CASE_BUILDERS = {"simple_rectangle": _simple_rectangle_job}


def canonical_program(text: str) -> str:
    """Normalize only explicitly non-behavioral volatile program content."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    for index, line in enumerate(lines):
        if line.startswith("; Generated: "):
            lines[index] = "; Generated: <TIMESTAMP>"
    return "\n".join(lines)


def _canonical_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(child) for child in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported golden value: {type(value).__name__}")


def _json_text(value: Any) -> str:
    return (
        json.dumps(
            _canonical_value(value),
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def result_payload(job: ProjectJob) -> dict[str, Any]:
    return {
        "bounds_mm": job.bounds_mm,
        "coordinate_frame_signature": job.coordinate_frame_signature,
        "coordinate_space": job.coordinate_space,
        "cut_length_mm": job.cut_length_mm,
        "estimated_seconds": job.estimated_seconds,
        "execution_signature": job.execution_signature,
        "guarded_output_polygon_mm": job.guarded_output_polygon_mm,
        "layer_summaries": job.layer_summaries,
        "path_count": job.path_count,
        "point_count": job.point_count,
        "raster_assets": job.raster_assets,
        "travel_length_mm": job.travel_length_mm,
    }


def snapshot_case(case_name: str) -> dict[str, str]:
    try:
        job = _CASE_BUILDERS[case_name]()
    except KeyError as exc:
        raise ValueError(f"Unknown planning golden case: {case_name}") from exc
    if job.plan is None:
        raise AssertionError(f"Planning golden case {case_name} produced no JobPlan")
    return {
        "program.gcode": canonical_program(job.text),
        "result.json": _json_text(result_payload(job)),
        "preview.json": _json_text(job.plan),
    }


def expected_case_dir(case_name: str) -> Path:
    if case_name not in CASE_NAMES:
        raise ValueError(f"Unknown planning golden case: {case_name}")
    return EXPECTED_ROOT / case_name
