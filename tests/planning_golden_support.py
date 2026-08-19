from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from planning_golden_extended_cases import (
    EXTENDED_CASE_BUILDERS,
    EXTENDED_CASE_NAMES,
    RejectionResult,
)

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
_FLOAT_DECIMAL_PLACES = 12
CORE_CASE_NAMES = (
    "simple_rectangle",
    "ellipse_curve",
    "nested_contours",
    "multiple_disjoint",
    "rotated_scaled",
    "multi_pass",
    "vector_power_correction",
)
CASE_NAMES = CORE_CASE_NAMES + EXTENDED_CASE_NAMES


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


def _line_layer(
    *,
    layer_id: str,
    name: str,
    speed_mm_min: float,
    power_percent: float,
    passes: int = 1,
    vector_power_correction: float = 0.0,
) -> OperationLayer:
    return OperationLayer(
        id=layer_id,
        name=name,
        color="#5CA9E7",
        mode=LayerMode.LINE,
        speed_mm_min=speed_mm_min,
        power_percent=power_percent,
        passes=passes,
        line_interval_mm=0.10,
        scan_angle_deg=0.0,
        overscan_percent=2.5,
        vector_power_correction=vector_power_correction,
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
) -> ProjectDocument:
    return ProjectDocument(
        id=project_id,
        name=name,
        work_area=Bounds(0.0, 0.0, 100.0, 100.0),
        coordinate_space=CoordinateSpace.MACHINE,
        layers=[layer],
        objects=objects,
        created_at="2026-08-19T00:00:00+00:00",
        modified_at="2026-08-19T00:00:00+00:00",
        revision=0,
    )


def _laser() -> LaserSettings:
    return LaserSettings(
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


def _generate(document: ProjectDocument) -> ProjectJob:
    return generate_project_gcode(
        document,
        _laser(),
        optimize_order=True,
        start_position=(0.0, 0.0),
    )


def _ellipse_curve_job() -> ProjectJob:
    layer = _line_layer(
        layer_id="layer-golden-ellipse",
        name="Golden Ellipse",
        speed_mm_min=1500.0,
        power_percent=20.0,
    )
    ellipse = SceneObject(
        id="object-golden-ellipse",
        name="Golden ellipse",
        kind=ObjectKind.ELLIPSE,
        layer_id=layer.id,
        transform=Transform(
            x_mm=50.0,
            y_mm=45.0,
            width_mm=36.0,
            height_mm=24.0,
        ),
        geometry={},
        visible=True,
        locked=False,
    )
    return _generate(
        _document(
            project_id="project-golden-ellipse",
            name="Golden ellipse curve",
            layer=layer,
            objects=[ellipse],
        )
    )


def _nested_contours_job() -> ProjectJob:
    layer = _line_layer(
        layer_id="layer-golden-nested",
        name="Golden Nested",
        speed_mm_min=1000.0,
        power_percent=30.0,
    )
    nested = SceneObject(
        id="object-golden-nested",
        name="Golden nested contours",
        kind=ObjectKind.PATH,
        layer_id=layer.id,
        transform=Transform(
            x_mm=50.0,
            y_mm=50.0,
            width_mm=60.0,
            height_mm=60.0,
        ),
        geometry={
            "polylines": [
                {
                    "points": [
                        [-0.5, -0.5],
                        [0.5, -0.5],
                        [0.5, 0.5],
                        [-0.5, 0.5],
                        [-0.5, -0.5],
                    ],
                    "closed": True,
                },
                {
                    "points": [
                        [-0.2, -0.2],
                        [0.2, -0.2],
                        [0.2, 0.2],
                        [-0.2, 0.2],
                        [-0.2, -0.2],
                    ],
                    "closed": True,
                },
            ]
        },
        visible=True,
        locked=False,
    )
    return _generate(
        _document(
            project_id="project-golden-nested",
            name="Golden nested contours",
            layer=layer,
            objects=[nested],
        )
    )


def _multiple_disjoint_job() -> ProjectJob:
    layer = _line_layer(
        layer_id="layer-golden-disjoint",
        name="Golden Disjoint",
        speed_mm_min=1600.0,
        power_percent=15.0,
    )
    objects = [
        SceneObject(
            id="object-golden-disjoint-a",
            name="Disjoint A",
            kind=ObjectKind.RECTANGLE,
            layer_id=layer.id,
            transform=Transform(15.0, 15.0, 10.0, 10.0),
            geometry={"corner_radius_mm": 0.0},
        ),
        SceneObject(
            id="object-golden-disjoint-b",
            name="Disjoint B",
            kind=ObjectKind.RECTANGLE,
            layer_id=layer.id,
            transform=Transform(80.0, 20.0, 12.0, 8.0),
            geometry={"corner_radius_mm": 0.0},
        ),
        SceneObject(
            id="object-golden-disjoint-c",
            name="Disjoint C",
            kind=ObjectKind.RECTANGLE,
            layer_id=layer.id,
            transform=Transform(45.0, 70.0, 14.0, 12.0),
            geometry={"corner_radius_mm": 0.0},
        ),
    ]
    return _generate(
        _document(
            project_id="project-golden-disjoint",
            name="Golden multiple disjoint",
            layer=layer,
            objects=objects,
        )
    )


def _rotated_scaled_job() -> ProjectJob:
    layer = _line_layer(
        layer_id="layer-golden-transform",
        name="Golden Transform",
        speed_mm_min=1400.0,
        power_percent=22.0,
    )
    rectangle = SceneObject(
        id="object-golden-transform",
        name="Golden transformed rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(
            x_mm=55.0,
            y_mm=45.0,
            width_mm=48.0,
            height_mm=18.0,
            rotation_deg=32.5,
        ),
        geometry={"corner_radius_mm": 0.0},
    )
    return _generate(
        _document(
            project_id="project-golden-transform",
            name="Golden rotated scaled",
            layer=layer,
            objects=[rectangle],
        )
    )


def _multi_pass_job() -> ProjectJob:
    layer = _line_layer(
        layer_id="layer-golden-multipass",
        name="Golden Multi Pass",
        speed_mm_min=900.0,
        power_percent=40.0,
        passes=3,
    )
    rectangle = SceneObject(
        id="object-golden-multipass",
        name="Golden three pass rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(35.0, 40.0, 24.0, 16.0),
        geometry={"corner_radius_mm": 0.0},
    )
    return _generate(
        _document(
            project_id="project-golden-multipass",
            name="Golden multi pass",
            layer=layer,
            objects=[rectangle],
        )
    )


def _vector_power_correction_job() -> ProjectJob:
    layer = _line_layer(
        layer_id="layer-golden-correction",
        name="Golden Correction",
        speed_mm_min=1800.0,
        power_percent=35.0,
        vector_power_correction=60.0,
    )
    rectangle = SceneObject(
        id="object-golden-correction",
        name="Golden corrected rectangle",
        kind=ObjectKind.RECTANGLE,
        layer_id=layer.id,
        transform=Transform(40.0, 40.0, 30.0, 20.0),
        geometry={"corner_radius_mm": 0.0},
    )
    return _generate(
        _document(
            project_id="project-golden-correction",
            name="Golden vector power correction",
            layer=layer,
            objects=[rectangle],
        )
    )


_CASE_BUILDERS = {
    "simple_rectangle": _simple_rectangle_job,
    "ellipse_curve": _ellipse_curve_job,
    "nested_contours": _nested_contours_job,
    "multiple_disjoint": _multiple_disjoint_job,
    "rotated_scaled": _rotated_scaled_job,
    "multi_pass": _multi_pass_job,
    "vector_power_correction": _vector_power_correction_job,
}

_CASE_BUILDERS.update(EXTENDED_CASE_BUILDERS)



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
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Golden JSON floats must be finite")
        rounded = round(value, _FLOAT_DECIMAL_PLACES)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported golden value: {type(value).__name__}")


def canonical_json_text(value: Any) -> str:
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
        "raster_assets": [
            {
                "sha256": identity.sha256,
                "encoded_bytes": identity.encoded_bytes,
                "format": identity.format,
                "width": identity.width,
                "height": identity.height,
            }
            for identity in job.raster_assets
        ],
        "travel_length_mm": job.travel_length_mm,
    }


def snapshot_case(case_name: str) -> dict[str, str]:
    try:
        result = _CASE_BUILDERS[case_name]()
    except KeyError as exc:
        raise ValueError(f"Unknown planning golden case: {case_name}") from exc
    if isinstance(result, RejectionResult):
        return {
            "rejection.json": canonical_json_text(
                {
                    "exception_type": result.exception_type,
                    "message": result.message,
                }
            )
        }
    job = result
    if job.plan is None:
        raise AssertionError(f"Planning golden case {case_name} produced no JobPlan")
    return {
        "program.gcode": canonical_program(job.text),
        "result.json": canonical_json_text(result_payload(job)),
        "preview.json": canonical_json_text(job.plan),
    }


def expected_case_dir(case_name: str) -> Path:
    if case_name not in CASE_NAMES:
        raise ValueError(f"Unknown planning golden case: {case_name}")
    return EXPECTED_ROOT / case_name
