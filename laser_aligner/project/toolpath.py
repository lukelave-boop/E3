from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from ..config import LaserSettings, WorkArea
from ..geometry.svg import Polyline
from ..gcode.generator import ToolpathOptions, generate_frame_gcode, validate_paths
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


def _layer_paths(document: ProjectDocument, layer: OperationLayer) -> list[Polyline]:
    paths: list[Polyline] = []
    for item in document.objects:
        if item.layer_id == layer.id and item.visible:
            paths.extend(object_polylines(item))
    return paths


def generate_project_gcode(
    document: ProjectDocument,
    laser: LaserSettings,
    *,
    power_max: int | None = None,
    optimize_order: bool = True,
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
    start = np.array([work_area.x_min, work_area.y_min], dtype=np.float64)
    current = start.copy()

    all_paths: list[Polyline] = []
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
        if layer.mode != LayerMode.LINE:
            raise ValueError(
                f"Layer {layer.name!r} uses {layer.mode.value} output, which is not "
                "implemented in desktop v1 yet"
            )
        unsupported = [
            item.name
            for item in layer_objects
            if item.kind not in {
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
        paths = _layer_paths(document, layer)
        if not paths:
            continue
        validate_paths(paths, work_area, laser.boundary_margin_mm)
        if optimize_order:
            paths = _nearest_order(paths, current)
        current = paths[-1].points[-1]
        layer_plans.append((layer, paths))
        all_paths.extend(paths)

    if not all_paths:
        raise ValueError("The project contains no enabled vector line paths")

    bounds = _bounds(all_paths)
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
    current = start.copy()
    travel_length = 0.0
    cut_length = 0.0
    estimated_seconds = 0.0
    point_count = 0
    path_count = 0
    summaries: list[dict[str, Any]] = []

    for layer, paths in layer_plans:
        power = layer.controller_power(controller_power_max)
        layer_cut = 0.0
        layer_travel = 0.0
        lines.append(
            f"; Layer {layer.name.replace(';', ',')[:80]} · "
            f"{layer.speed_mm_min:g} mm/min · {layer.power_percent:g}% · "
            f"{layer.passes} pass(es)"
        )
        for pass_index in range(layer.passes):
            lines.append(f"; Pass {pass_index + 1}/{layer.passes}")
            ordered = _nearest_order(paths, current) if optimize_order else paths
            for path in ordered:
                points = path.points
                if len(points) < 2:
                    continue
                travel = float(np.linalg.norm(points[0] - current))
                layer_travel += travel
                travel_length += travel
                estimated_seconds += travel / max(laser.travel_feed_mm_min, 1.0) * 60.0
                lines.append(
                    f"G0 X{_fmt(points[0, 0])} Y{_fmt(points[0, 1])} "
                    f"F{_fmt(laser.travel_feed_mm_min)}"
                )
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
                estimated_seconds += distance / layer.speed_mm_min * 60.0
                point_count += len(points)
                path_count += 1
                current = points[-1]
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

    lines.extend(["M5", "; End of E3 project job", ""])
    return ProjectJob(
        text="\n".join(lines),
        bounds_mm=bounds,
        cut_length_mm=cut_length,
        travel_length_mm=travel_length,
        estimated_seconds=estimated_seconds,
        path_count=path_count,
        point_count=point_count,
        layer_summaries=summaries,
    )


def generate_project_frame(
    document: ProjectDocument,
    laser: LaserSettings,
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
        optimize_order=False,
    )
    program = generate_frame_gcode(bounds, options, work_area, laser_enabled=False)
    estimated = (
        program.cut_length_mm + program.travel_length_mm
    ) / max(laser.travel_feed_mm_min, 1.0) * 60.0
    return ProjectJob(
        text=program.text,
        bounds_mm=program.bounds_mm,
        cut_length_mm=program.cut_length_mm,
        travel_length_mm=program.travel_length_mm,
        estimated_seconds=estimated,
        path_count=program.path_count,
        point_count=program.point_count,
        layer_summaries=[],
    )
