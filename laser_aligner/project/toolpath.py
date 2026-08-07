from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import LaserSettings, WorkArea
from ..gcode.generator import ToolpathOptions, generate_frame_gcode, validate_paths
from ..gcode.job_plan import JobPlan, build_job_plan, e3_metadata_line
from ..geometry.svg import Polyline
from .model import LayerMode, ObjectKind, OperationLayer, ProjectDocument, SceneObject


@dataclass(slots=True)
class ProjectJob:
    text: str
    bounds_mm: tuple[float, float, float, float]
    cut_length_mm: float
    travel_length_mm: float
    estimated_seconds: float
    path_count: int
    point_count: int
    layer_summaries: list[dict[str, Any]] = field(default_factory=list)
    plan: JobPlan | None = None


def _fmt(value: float) -> str:
    if abs(value) < 0.0005:
        value = 0.0
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _transform_points(points: np.ndarray, item: SceneObject) -> np.ndarray:
    transform = item.transform
    output = np.asarray(points, dtype=np.float64).reshape(-1, 2).copy()
    output[:, 0] *= transform.width_mm
    output[:, 1] *= transform.height_mm
    if transform.mirror_x:
        output[:, 0] *= -1.0
    if transform.mirror_y:
        output[:, 1] *= -1.0
    radians = math.radians(transform.rotation_deg)
    rotation = np.array(
        [
            [math.cos(radians), -math.sin(radians)],
            [math.sin(radians), math.cos(radians)],
        ],
        dtype=np.float64,
    )
    output = output @ rotation.T
    output[:, 0] += transform.x_mm
    output[:, 1] += transform.y_mm
    return output


def _rounded_rectangle_points(
    radius_fraction_x: float,
    radius_fraction_y: float,
    segments_per_corner: int = 8,
) -> np.ndarray:
    radius_fraction_x = max(0.0, min(0.5, radius_fraction_x))
    radius_fraction_y = max(0.0, min(0.5, radius_fraction_y))
    if radius_fraction_x <= 1e-9 or radius_fraction_y <= 1e-9:
        return np.array(
            [
                [-0.5, -0.5],
                [0.5, -0.5],
                [0.5, 0.5],
                [-0.5, 0.5],
                [-0.5, -0.5],
            ],
            dtype=np.float64,
        )
    points: list[list[float]] = []
    centers = (
        (0.5 - radius_fraction_x, -0.5 + radius_fraction_y, -90.0, 0.0),
        (0.5 - radius_fraction_x, 0.5 - radius_fraction_y, 0.0, 90.0),
        (-0.5 + radius_fraction_x, 0.5 - radius_fraction_y, 90.0, 180.0),
        (-0.5 + radius_fraction_x, -0.5 + radius_fraction_y, 180.0, 270.0),
    )
    for center_x, center_y, start, end in centers:
        for index in range(segments_per_corner + 1):
            angle = math.radians(start + (end - start) * index / segments_per_corner)
            points.append(
                [
                    center_x + radius_fraction_x * math.cos(angle),
                    center_y + radius_fraction_y * math.sin(angle),
                ]
            )
    points.append(points[0])
    return np.asarray(points, dtype=np.float64)


def object_polylines(item: SceneObject) -> list[Polyline]:
    if not item.visible:
        return []
    if item.kind == ObjectKind.RECTANGLE:
        radius_mm = float(item.geometry.get("corner_radius_mm", 0.0))
        points = _rounded_rectangle_points(
            radius_mm / item.transform.width_mm,
            radius_mm / item.transform.height_mm,
        )
        return [Polyline(_transform_points(points, item), closed=True, source_tag=item.name)]
    if item.kind == ObjectKind.ELLIPSE:
        angles = np.linspace(0.0, 2.0 * math.pi, 73)
        points = np.column_stack([0.5 * np.cos(angles), 0.5 * np.sin(angles)])
        return [Polyline(_transform_points(points, item), closed=True, source_tag=item.name)]
    if item.kind == ObjectKind.LINE:
        points = np.asarray(item.geometry.get("points", [[-0.5, 0.0], [0.5, 0.0]]))
        return [Polyline(_transform_points(points, item), closed=False, source_tag=item.name)]
    if item.kind in {ObjectKind.PATH, ObjectKind.POLYGON}:
        output: list[Polyline] = []
        for line in item.geometry.get("polylines", []):
            points = np.asarray(line["points"], dtype=np.float64)
            transformed = _transform_points(points, item)
            closed = bool(line.get("closed", False))
            if closed and np.linalg.norm(transformed[0] - transformed[-1]) > 1e-9:
                transformed = np.vstack([transformed, transformed[0]])
            output.append(
                Polyline(transformed, closed=closed, source_tag=item.name)
            )
        return output
    return []


def _nearest_order(paths: list[Polyline], start: np.ndarray) -> list[Polyline]:
    remaining = [Polyline(path.points.copy(), path.closed, path.source_tag) for path in paths]
    ordered: list[Polyline] = []
    current = start.copy()
    while remaining:
        best_index = 0
        best_points: np.ndarray | None = None
        best_distance = float("inf")
        for index, path in enumerate(remaining):
            direct = float(np.linalg.norm(path.points[0] - current))
            reverse = float(np.linalg.norm(path.points[-1] - current))
            if reverse < direct and not path.closed:
                candidate = path.points[::-1].copy()
                distance = reverse
            else:
                candidate = path.points
                distance = direct
            if distance < best_distance:
                best_index = index
                best_points = candidate
                best_distance = distance
        selected = remaining.pop(best_index)
        assert best_points is not None
        selected.points = best_points
        ordered.append(selected)
        current = best_points[-1]
    return ordered


def _bounds(paths: Iterable[Polyline]) -> tuple[float, float, float, float]:
    arrays = [path.points for path in paths if len(path.points)]
    if not arrays:
        raise ValueError("No usable paths")
    points = np.vstack(arrays)
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])


def _length(points: np.ndarray) -> float:
    if len(points) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum())


def _controller_paths(
    paths: list[Polyline],
    laser: LaserSettings,
) -> list[Polyline]:
    offset = np.array(
        [laser.spot_offset_x_mm, laser.spot_offset_y_mm],
        dtype=np.float64,
    )
    if not np.isfinite(offset).all():
        raise ValueError("laser spot offsets must be finite")
    return [
        Polyline(
            path.points.astype(np.float64, copy=True) - offset,
            closed=path.closed,
            source_tag=path.source_tag,
        )
        for path in paths
    ]


def _raster_motion_points(path: Polyline, overscan_percent: float) -> np.ndarray:
    points = path.points
    if len(points) != 2 or overscan_percent <= 0:
        return points.copy()
    vector = points[1] - points[0]
    length = float(np.linalg.norm(vector))
    if length <= 1e-12:
        return points.copy()
    extension = vector / length * (length * overscan_percent / 100.0)
    return np.vstack([points[0] - extension, points[0], points[1], points[1] + extension])


def _layer_paths(document: ProjectDocument, layer: OperationLayer) -> list[Polyline]:
    paths: list[Polyline] = []
    for item in document.objects:
        if item.layer_id == layer.id and item.visible:
            paths.extend(object_polylines(item))
    return paths


def _scanline_paths(item: SceneObject, layer: OperationLayer) -> list[Polyline]:
    outlines = object_polylines(item)
    if not outlines or any(not path.closed for path in outlines):
        raise ValueError(
            f"{layer.mode.value.title()} output requires closed vector geometry: "
            f"{item.name}"
        )
    angle = math.radians(layer.scan_angle_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    to_scan = np.array([[cosine, sine], [-sine, cosine]], dtype=np.float64)
    from_scan = to_scan.T
    polygons = [path.points @ to_scan.T for path in outlines]
    all_points = np.vstack(polygons)
    y_min = float(all_points[:, 1].min())
    y_max = float(all_points[:, 1].max())
    interval = float(layer.line_interval_mm)
    y = math.ceil((y_min - 1e-9) / interval) * interval
    segments: list[Polyline] = []
    row = 0
    while y <= y_max + 1e-9:
        intersections: list[float] = []
        for polygon in polygons:
            for start, end in zip(polygon[:-1], polygon[1:], strict=False):
                low_y = min(float(start[1]), float(end[1]))
                high_y = max(float(start[1]), float(end[1]))
                if high_y - low_y <= 1e-12 or not (low_y <= y < high_y):
                    continue
                ratio = (y - float(start[1])) / (float(end[1]) - float(start[1]))
                intersections.append(float(start[0]) + ratio * (float(end[0]) - float(start[0])))
        intersections.sort()
        for index in range(0, len(intersections) - 1, 2):
            start_x, end_x = intersections[index : index + 2]
            if end_x - start_x <= 1e-9:
                continue
            scan_points = np.array([[start_x, y], [end_x, y]], dtype=np.float64)
            if row % 2:
                scan_points = scan_points[::-1].copy()
            points = scan_points @ from_scan.T
            segments.append(
                Polyline(points, closed=False, source_tag=item.name)
            )
        row += 1
        y += interval
    if not segments:
        raise ValueError(f"{layer.mode.value.title()} produced no scanlines: {item.name}")
    return segments


def _image_raster_paths(item: SceneObject, layer: OperationLayer) -> list[Polyline]:
    from pathlib import Path

    import cv2

    asset = Path(str(item.geometry.get("asset", ""))).expanduser()
    if not asset.is_file():
        raise ValueError(f"Raster image asset does not exist: {asset}")
    image = cv2.imread(str(asset), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        raise ValueError(f"Raster image asset could not be decoded: {asset}")
    height, width = image.shape
    row_count = max(1, int(math.ceil(item.transform.height_mm / layer.line_interval_mm)))
    output: list[Polyline] = []
    for row_index in range(row_count):
        local_y = -0.5 + (row_index + 0.5) / row_count
        source_y = min(height - 1, int((row_index + 0.5) / row_count * height))
        dark = image[source_y] < 128
        runs: list[tuple[int, int]] = []
        start: int | None = None
        for column, enabled in enumerate(dark):
            if enabled and start is None:
                start = column
            if start is not None and (not enabled or column == width - 1):
                end = column + 1 if enabled and column == width - 1 else column
                runs.append((start, end))
                start = None
        if row_index % 2:
            runs.reverse()
        for start_column, end_column in runs:
            local = np.array(
                [
                    [start_column / width - 0.5, local_y],
                    [end_column / width - 0.5, local_y],
                ],
                dtype=np.float64,
            )
            if row_index % 2:
                local = local[::-1].copy()
            output.append(
                Polyline(
                    _transform_points(local, item),
                    closed=False,
                    source_tag=item.name,
                )
            )
    if not output:
        raise ValueError(f"Raster image contains no pixels below the 50% threshold: {item.name}")
    return output


def _operation_paths(
    document: ProjectDocument,
    layer: OperationLayer,
    layer_objects: list[SceneObject],
) -> list[Polyline]:
    if layer.mode == LayerMode.LINE:
        return _layer_paths(document, layer)
    unsupported = [
        item.name
        for item in layer_objects
        if item.kind in {ObjectKind.TEXT, ObjectKind.LINE}
        or (item.kind == ObjectKind.IMAGE and layer.mode != LayerMode.RASTER)
    ]
    if unsupported:
        raise ValueError(
            f"{layer.mode.value.title()} output is not implemented for: "
            + ", ".join(unsupported)
        )
    return [
        path
        for item in layer_objects
        for path in (
            _image_raster_paths(item, layer)
            if item.kind == ObjectKind.IMAGE
            else _scanline_paths(item, layer)
        )
    ]


def generate_project_gcode(
    document: ProjectDocument,
    laser: LaserSettings,
    *,
    power_max: int | None = None,
    optimize_order: bool = True,
    start_position: tuple[float, float] | None = None,
) -> ProjectJob:
    """Generate one guarded vector program containing all enabled line layers."""
    document.validate()
    controller_power_max = int(power_max or laser.power_max)
    work_area = WorkArea(
        x_min=document.work_area.x_min,
        x_max=document.work_area.x_max,
        y_min=document.work_area.y_min,
        y_max=document.work_area.y_max,
    )
    start = np.array(
        start_position or (work_area.x_min, work_area.y_min),
        dtype=np.float64,
    )
    current = start.copy()

    all_paths: list[Polyline] = []
    all_controller_paths: list[Polyline] = []
    layer_plans: list[tuple[OperationLayer, list[Polyline]]] = []
    for layer in sorted(document.layers, key=lambda item: item.priority):
        if not layer.visible or not layer.output_enabled:
            continue
        layer_objects = [
            item
            for item in document.objects
            if item.layer_id == layer.id and item.visible
        ]
        if not layer_objects:
            continue
        unsupported = [
            item.name
            for item in layer_objects
            if layer.mode == LayerMode.LINE
            and item.kind not in {
                ObjectKind.RECTANGLE,
                ObjectKind.ELLIPSE,
                ObjectKind.LINE,
                ObjectKind.PATH,
                ObjectKind.POLYGON,
            }
        ]
        if unsupported:
            raise ValueError(
                "Vector output is not implemented for: " + ", ".join(unsupported)
            )
        design_paths = _operation_paths(document, layer, layer_objects)
        if not design_paths:
            continue
        validate_paths(design_paths, work_area, laser.boundary_margin_mm)
        paths = _controller_paths(design_paths, laser)
        validate_paths(
            paths,
            work_area,
            laser.boundary_margin_mm,
            coordinate_label="controller path after laser spot correction",
        )
        controller_motion_paths = (
            [
                Polyline(
                    _raster_motion_points(path, layer.overscan_percent),
                    closed=False,
                    source_tag=path.source_tag,
                )
                for path in paths
            ]
            if layer.mode == LayerMode.RASTER
            else paths
        )
        validate_paths(
            controller_motion_paths,
            work_area,
            laser.boundary_margin_mm,
            coordinate_label="raster overscan path after laser spot correction",
        )
        layer_plans.append((layer, paths))
        all_paths.extend(design_paths)
        all_controller_paths.extend(controller_motion_paths)

    if not all_paths:
        raise ValueError("The project contains no enabled vector line paths")

    bounds = _bounds(all_paths)
    controller_bounds = _bounds(all_controller_paths)
    lines = [
        "; E3 Positioning System project job",
        f"; Project: {document.name.replace(';', ',')[:120]}",
        f"; Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"; Bounds: X{_fmt(bounds[0])}..{_fmt(bounds[2])} "
        f"Y{_fmt(bounds[1])}..{_fmt(bounds[3])}",
        "G21 ; millimetres",
        "G90 ; absolute positioning",
        "M5 ; laser off before any motion",
    ]
    lines.insert(
        4,
        e3_metadata_line(
            "job",
            {
                "planner": "nearest path" if optimize_order else "source order",
                "start_x": float(start[0]),
                "start_y": float(start[1]),
            },
        ),
    )
    if abs(laser.spot_offset_x_mm) >= 1e-12 or abs(laser.spot_offset_y_mm) >= 1e-12:
        lines[4:4] = [
            "; Laser spot offset (spot = controller + offset): "
            f"X{_fmt(laser.spot_offset_x_mm)} Y{_fmt(laser.spot_offset_y_mm)}",
            f"; Controller bounds: X{_fmt(controller_bounds[0])}..{_fmt(controller_bounds[2])} "
            f"Y{_fmt(controller_bounds[1])}..{_fmt(controller_bounds[3])}",
        ]
    current = start.copy()
    travel_length = 0.0
    cut_length = 0.0
    point_count = 0
    path_count = 0
    summaries: list[dict[str, Any]] = []
    source_order_travel = 0.0
    planned_order_travel = 0.0

    for layer, paths in layer_plans:
        power = layer.controller_power(controller_power_max)
        layer_cut = 0.0
        layer_travel = 0.0
        lines.append(
            f"; Layer {layer.name.replace(';', ',')[:80]} · "
            f"{layer.speed_mm_min:g} mm/min · {layer.power_percent:g}% · "
            f"{layer.passes} pass(es)"
        )
        lines.append(
            e3_metadata_line(
                "layer",
                {
                    "id": layer.id,
                    "name": layer.name,
                    "color": layer.color,
                    "power_percent": layer.power_percent,
                    "mode": layer.mode.value,
                },
            )
        )
        for pass_index in range(layer.passes):
            lines.append(f"; Pass {pass_index + 1}/{layer.passes}")
            lines.append(
                e3_metadata_line(
                    "pass",
                    {"index": pass_index + 1, "count": layer.passes},
                )
            )
            comparison_position = current.copy()
            for source_path in paths:
                source_motion = (
                    _raster_motion_points(source_path, layer.overscan_percent)
                    if layer.mode == LayerMode.RASTER
                    else source_path.points
                )
                source_order_travel += float(
                    np.linalg.norm(source_motion[0] - comparison_position)
                )
                comparison_position = source_motion[-1]
            ordered = _nearest_order(paths, current) if optimize_order else paths
            for path in ordered:
                points = path.points
                if len(points) < 2:
                    continue
                lines.append(e3_metadata_line("path", {"name": path.source_tag}))
                raster_motion = (
                    _raster_motion_points(path, layer.overscan_percent)
                    if layer.mode == LayerMode.RASTER
                    else points
                )
                travel_target = raster_motion[0]
                travel = float(np.linalg.norm(travel_target - current))
                planned_order_travel += travel
                layer_travel += travel
                travel_length += travel
                lines.append(
                    f"G0 X{_fmt(travel_target[0])} Y{_fmt(travel_target[1])} "
                    f"F{_fmt(laser.travel_feed_mm_min)}"
                )
                if layer.mode == LayerMode.RASTER and len(raster_motion) == 4:
                    lead_in = float(np.linalg.norm(raster_motion[1] - raster_motion[0]))
                    lines.append(
                        f"G1 X{_fmt(points[0, 0])} Y{_fmt(points[0, 1])} "
                        f"F{_fmt(layer.speed_mm_min)}"
                    )
                    layer_travel += lead_in
                    travel_length += lead_in
                if power > 0:
                    lines.append(f"{laser.power_mode.upper()} S{power}")
                else:
                    lines.append("; Layer power is zero; laser remains off")
                for point in points[1:]:
                    lines.append(
                        f"G1 X{_fmt(point[0])} Y{_fmt(point[1])} "
                        f"F{_fmt(layer.speed_mm_min)}"
                    )
                lines.append("M5")
                distance = _length(points)
                layer_cut += distance
                cut_length += distance
                point_count += len(points)
                path_count += 1
                current = points[-1]
                if layer.mode == LayerMode.RASTER and len(raster_motion) == 4:
                    lead_out = float(np.linalg.norm(raster_motion[3] - raster_motion[2]))
                    lines.append(
                        f"G1 X{_fmt(raster_motion[3, 0])} Y{_fmt(raster_motion[3, 1])} "
                        f"F{_fmt(layer.speed_mm_min)}"
                    )
                    layer_travel += lead_out
                    travel_length += lead_out
                    current = raster_motion[3]
        summaries.append(
            {
                "layer_id": layer.id,
                "name": layer.name,
                "path_count": len(paths) * layer.passes,
                "cut_length_mm": layer_cut,
                "travel_length_mm": layer_travel,
                "power": power,
                "speed_mm_min": layer.speed_mm_min,
                "passes": layer.passes,
            }
        )

    planner_savings = max(0.0, source_order_travel - planned_order_travel)
    lines.extend(
        [
            e3_metadata_line(
                "planner",
                {
                    "source_order_travel_mm": source_order_travel,
                    "planned_order_travel_mm": planned_order_travel,
                    "savings_mm": planner_savings,
                },
            ),
            "M5",
            "; End of E3 project job",
            "",
        ]
    )
    text = "\n".join(lines)
    plan = build_job_plan(
        text,
        power_max=controller_power_max,
        default_feed_mm_min=laser.travel_feed_mm_min,
        start_position=(float(start[0]), float(start[1])),
        acceleration_mm_s2=laser.preview_acceleration_mm_s2,
        command_delay_ms=laser.preview_command_delay_ms,
    )
    return ProjectJob(
        text=text,
        bounds_mm=bounds,
        cut_length_mm=cut_length,
        travel_length_mm=travel_length,
        estimated_seconds=plan.total_seconds,
        path_count=path_count,
        point_count=point_count,
        layer_summaries=summaries,
        plan=plan,
    )


def generate_project_frame(
    document: ProjectDocument,
    laser: LaserSettings,
    *,
    start_position: tuple[float, float] | None = None,
) -> ProjectJob:
    objects = document.visible_output_objects()
    paths = [path for item in objects for path in object_polylines(item)]
    if not paths:
        raise ValueError("The project contains no visible output geometry")
    bounds = _bounds(paths)
    work_area = WorkArea(
        x_min=document.work_area.x_min,
        x_max=document.work_area.x_max,
        y_min=document.work_area.y_min,
        y_max=document.work_area.y_max,
    )
    options = ToolpathOptions(
        power_mode=laser.power_mode,
        power=0,
        power_max=laser.power_max,
        travel_feed_mm_min=laser.travel_feed_mm_min,
        engrave_feed_mm_min=laser.travel_feed_mm_min,
        boundary_margin_mm=laser.boundary_margin_mm,
        spot_offset_x_mm=laser.spot_offset_x_mm,
        spot_offset_y_mm=laser.spot_offset_y_mm,
        optimize_order=False,
    )
    program = generate_frame_gcode(bounds, options, work_area, laser_enabled=False)
    plan = build_job_plan(
        program.text,
        power_max=laser.power_max,
        default_feed_mm_min=laser.travel_feed_mm_min,
        start_position=start_position or (work_area.x_min, work_area.y_min),
        acceleration_mm_s2=laser.preview_acceleration_mm_s2,
        command_delay_ms=laser.preview_command_delay_ms,
    )
    return ProjectJob(
        text=program.text,
        bounds_mm=program.bounds_mm,
        cut_length_mm=program.cut_length_mm,
        travel_length_mm=program.travel_length_mm,
        estimated_seconds=plan.total_seconds,
        path_count=program.path_count,
        point_count=program.point_count,
        layer_summaries=[],
        plan=plan,
    )
