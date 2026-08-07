from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..config import WorkArea
from ..errors import SafetyError, SvgError
from ..geometry.svg import Polyline, SvgGeometry

_PATH_BOUNDS_TOLERANCE_MM = 1e-6


@dataclass(slots=True)
class DesignPlacement:
    center_x_mm: float
    center_y_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: float = 0.0
    mirror_x: bool = False
    mirror_y: bool = False


@dataclass(slots=True)
class ToolpathOptions:
    power_mode: str = "M4"
    power: int = 100
    power_max: int = 1000
    travel_feed_mm_min: float = 3000.0
    engrave_feed_mm_min: float = 1200.0
    boundary_margin_mm: float = 0.0
    spot_offset_x_mm: float = 0.0
    spot_offset_y_mm: float = 0.0
    optimize_order: bool = True
    include_return_move: bool = False
    return_x_mm: float = 0.0
    return_y_mm: float = 0.0


@dataclass(slots=True)
class GcodeProgram:
    text: str
    bounds_mm: tuple[float, float, float, float]
    cut_length_mm: float
    travel_length_mm: float
    path_count: int
    point_count: int
    warnings: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "bounds_mm": list(self.bounds_mm),
            "cut_length_mm": self.cut_length_mm,
            "travel_length_mm": self.travel_length_mm,
            "path_count": self.path_count,
            "point_count": self.point_count,
            "warnings": self.warnings,
        }


def _fmt(value: float) -> str:
    if abs(value) < 0.0005:
        value = 0.0
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _safe_comment(value: str) -> str:
    return value.replace("\n", " ").replace("\r", " ").replace(";", ",")[:160]


def place_geometry(geometry: SvgGeometry, placement: DesignPlacement) -> list[Polyline]:
    if not all(
        math.isfinite(value)
        for value in (
            placement.center_x_mm,
            placement.center_y_mm,
            placement.width_mm,
            placement.height_mm,
            placement.rotation_deg,
        )
    ):
        raise SvgError("Placement values must be finite numbers")
    if placement.width_mm <= 0 or placement.height_mm <= 0:
        raise SvgError("Design width and height must be positive")
    min_x, min_y, max_x, max_y = geometry.bounds
    source_width = max_x - min_x
    source_height = max_y - min_y
    if source_width <= 0 or source_height <= 0:
        raise SvgError("SVG geometry has invalid bounds")

    radians = math.radians(placement.rotation_deg)
    rotation = np.array(
        [[math.cos(radians), -math.sin(radians)], [math.sin(radians), math.cos(radians)]],
        dtype=np.float64,
    )
    mirror = np.array(
        [-1.0 if placement.mirror_x else 1.0, -1.0 if placement.mirror_y else 1.0],
        dtype=np.float64,
    )
    output: list[Polyline] = []
    for line in geometry.polylines:
        if len(line.points) < 2:
            continue
        normalized = np.empty_like(line.points, dtype=np.float64)
        normalized[:, 0] = (line.points[:, 0] - min_x) / source_width
        # SVG Y grows downward; machine Y grows toward the top of the rectified view.
        normalized[:, 1] = 1.0 - (line.points[:, 1] - min_y) / source_height
        local = np.empty_like(normalized)
        local[:, 0] = (normalized[:, 0] - 0.5) * placement.width_mm
        local[:, 1] = (normalized[:, 1] - 0.5) * placement.height_mm
        local *= mirror
        machine = local @ rotation.T
        machine[:, 0] += placement.center_x_mm
        machine[:, 1] += placement.center_y_mm
        output.append(Polyline(machine, closed=line.closed, source_tag=line.source_tag))
    if not output:
        raise SvgError("No usable paths remained after placement")
    return output


def _path_length(points: np.ndarray) -> float:
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()) if len(points) >= 2 else 0.0


def _program_bounds(paths: list[Polyline]) -> tuple[float, float, float, float]:
    points = np.vstack([path.points for path in paths if len(path.points)])
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])


def _controller_paths(
    paths: list[Polyline],
    options: ToolpathOptions,
) -> list[Polyline]:
    offset = np.array(
        [options.spot_offset_x_mm, options.spot_offset_y_mm],
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


def _spot_offset_comment(options: ToolpathOptions) -> str | None:
    if (
        abs(options.spot_offset_x_mm) < 1e-12
        and abs(options.spot_offset_y_mm) < 1e-12
    ):
        return None
    return (
        "; Laser spot offset (spot = controller + offset): "
        f"X{_fmt(options.spot_offset_x_mm)} Y{_fmt(options.spot_offset_y_mm)}"
    )


def validate_paths(
    paths: list[Polyline],
    work_area: WorkArea,
    margin_mm: float = 0.0,
    *,
    coordinate_label: str = "design",
) -> None:
    minimum_x, minimum_y, maximum_x, maximum_y = _program_bounds(paths)
    safe_minimum_x = work_area.x_min + margin_mm
    safe_maximum_x = work_area.x_max - margin_mm
    safe_minimum_y = work_area.y_min + margin_mm
    safe_maximum_y = work_area.y_max - margin_mm
    if (
        minimum_x < safe_minimum_x - _PATH_BOUNDS_TOLERANCE_MM
        or maximum_x > safe_maximum_x + _PATH_BOUNDS_TOLERANCE_MM
        or minimum_y < safe_minimum_y - _PATH_BOUNDS_TOLERANCE_MM
        or maximum_y > safe_maximum_y + _PATH_BOUNDS_TOLERANCE_MM
    ):
        raise SafetyError(
            "Path exceeds the configured safe work area: "
            f"{coordinate_label} X={minimum_x:.2f}..{maximum_x:.2f}, "
            f"Y={minimum_y:.2f}..{maximum_y:.2f}; "
            f"safe X={safe_minimum_x:.2f}..{safe_maximum_x:.2f}, "
            f"Y={safe_minimum_y:.2f}..{safe_maximum_y:.2f}"
        )


def _nearest_order(paths: list[Polyline], start: np.ndarray) -> list[Polyline]:
    remaining = [Polyline(path.points.copy(), path.closed, path.source_tag) for path in paths]
    ordered: list[Polyline] = []
    current = start.copy()
    while remaining:
        best_index = 0
        best_points: np.ndarray | None = None
        best_distance = float("inf")
        for index, path in enumerate(remaining):
            points = path.points
            if path.closed and len(points) > 2:
                core = points[:-1] if np.linalg.norm(points[-1] - points[0]) < 1e-9 else points
                distances = np.linalg.norm(core - current, axis=1)
                start_index = int(np.argmin(distances))
                rotated = np.vstack([core[start_index:], core[: start_index + 1]])
                candidate_distance = float(distances[start_index])
                candidate = rotated
            else:
                direct = float(np.linalg.norm(points[0] - current))
                reverse = float(np.linalg.norm(points[-1] - current))
                if reverse < direct:
                    candidate_distance = reverse
                    candidate = points[::-1].copy()
                else:
                    candidate_distance = direct
                    candidate = points
            if candidate_distance < best_distance:
                best_distance = candidate_distance
                best_index = index
                best_points = candidate
        selected = remaining.pop(best_index)
        assert best_points is not None
        selected.points = best_points
        ordered.append(selected)
        current = selected.points[-1]
    return ordered


def generate_vector_gcode(
    geometry: SvgGeometry,
    placement: DesignPlacement,
    options: ToolpathOptions,
    work_area: WorkArea,
    design_name: str = "design.svg",
) -> GcodeProgram:
    if options.power_mode.upper() not in {"M3", "M4"}:
        raise ValueError("power_mode must be M3 or M4")
    if not 0 <= options.power <= options.power_max:
        raise ValueError("power must be between zero and power_max")
    design_paths = place_geometry(geometry, placement)
    validate_paths(design_paths, work_area, options.boundary_margin_mm)
    bounds = _program_bounds(design_paths)
    paths = _controller_paths(design_paths, options)
    validate_paths(
        paths,
        work_area,
        options.boundary_margin_mm,
        coordinate_label="controller path after laser spot correction",
    )
    start = np.array([work_area.x_min, work_area.y_min], dtype=np.float64)
    if options.optimize_order:
        paths = _nearest_order(paths, start)

    controller_bounds = _program_bounds(paths)
    lines = [
        "; Laser Camera Aligner vector job",
        f"; Source: {_safe_comment(design_name)}",
        f"; Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"; Bounds: X{_fmt(bounds[0])}..{_fmt(bounds[2])} Y{_fmt(bounds[1])}..{_fmt(bounds[3])}",
        f"; Power: {options.power}/{options.power_max}; feed: {_fmt(options.engrave_feed_mm_min)} mm/min",
        "G21 ; millimetres",
        "G90 ; absolute positioning",
        "M5 ; laser off before motion",
    ]
    offset_comment = _spot_offset_comment(options)
    if offset_comment is not None:
        lines[4:4] = [
            offset_comment,
            f"; Controller bounds: X{_fmt(controller_bounds[0])}..{_fmt(controller_bounds[2])} "
            f"Y{_fmt(controller_bounds[1])}..{_fmt(controller_bounds[3])}",
        ]
    current = start.copy()
    travel_length = 0.0
    cut_length = 0.0
    point_count = 0

    for index, path in enumerate(paths, start=1):
        points = path.points
        if len(points) < 2:
            continue
        travel_length += float(np.linalg.norm(points[0] - current))
        lines.append(f"; Path {index}: {path.source_tag or 'vector'}")
        lines.append(f"G0 X{_fmt(points[0, 0])} Y{_fmt(points[0, 1])} F{_fmt(options.travel_feed_mm_min)}")
        if options.power > 0:
            lines.append(f"{options.power_mode.upper()} S{int(options.power)}")
        else:
            lines.append("; Laser output disabled for this path")
        for point in points[1:]:
            lines.append(
                f"G1 X{_fmt(point[0])} Y{_fmt(point[1])} F{_fmt(options.engrave_feed_mm_min)}"
            )
        lines.append("M5")
        cut_length += _path_length(points)
        point_count += len(points)
        current = points[-1]

    if options.include_return_move:
        return_point = np.array([options.return_x_mm, options.return_y_mm], dtype=float)
        if not work_area.contains(float(return_point[0]), float(return_point[1])):
            raise SafetyError("Configured return position lies outside the work area")
        travel_length += float(np.linalg.norm(return_point - current))
        lines.append(
            f"G0 X{_fmt(return_point[0])} Y{_fmt(return_point[1])} F{_fmt(options.travel_feed_mm_min)}"
        )
    lines.extend(["M5", "; End of generated job", ""])
    return GcodeProgram(
        text="\n".join(lines),
        bounds_mm=bounds,
        cut_length_mm=cut_length,
        travel_length_mm=travel_length,
        path_count=len(paths),
        point_count=point_count,
        warnings=list(geometry.warnings),
    )


def generate_frame_gcode(
    bounds_mm: tuple[float, float, float, float],
    options: ToolpathOptions,
    work_area: WorkArea,
    laser_enabled: bool = False,
) -> GcodeProgram:
    min_x, min_y, max_x, max_y = bounds_mm
    rectangle = np.array(
        [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y], [min_x, min_y]],
        dtype=np.float64,
    )
    paths = [Polyline(rectangle, closed=True, source_tag="frame")]
    validate_paths(paths, work_area, options.boundary_margin_mm)
    controller_paths = _controller_paths(paths, options)
    validate_paths(
        controller_paths,
        work_area,
        options.boundary_margin_mm,
        coordinate_label="controller path after laser spot correction",
    )
    controller_rectangle = controller_paths[0].points
    effective_power = options.power if laser_enabled else 0
    lines = [
        "; Laser Camera Aligner framing pass",
        "; DRY MOTION ONLY" if effective_power == 0 else "; LOW-POWER LASER FRAME — verify configured power",
        "G21",
        "G90",
        "M5",
    ]
    offset_comment = _spot_offset_comment(options)
    if offset_comment is not None:
        controller_bounds = _program_bounds(controller_paths)
        lines.extend(
            [
                offset_comment,
                f"; Controller bounds: X{_fmt(controller_bounds[0])}..{_fmt(controller_bounds[2])} "
                f"Y{_fmt(controller_bounds[1])}..{_fmt(controller_bounds[3])}",
            ]
        )
    lines.append(
        f"G0 X{_fmt(controller_rectangle[0, 0])} "
        f"Y{_fmt(controller_rectangle[0, 1])} F{_fmt(options.travel_feed_mm_min)}"
    )
    if effective_power > 0:
        lines.append(f"{options.power_mode.upper()} S{effective_power}")
    for point in controller_rectangle[1:]:
        lines.append(f"G1 X{_fmt(point[0])} Y{_fmt(point[1])} F{_fmt(options.travel_feed_mm_min)}")
    lines.extend(["M5", ""])
    return GcodeProgram(
        text="\n".join(lines),
        bounds_mm=bounds_mm,
        cut_length_mm=_path_length(rectangle),
        travel_length_mm=float(
            np.linalg.norm(
                controller_rectangle[0]
                - np.array([work_area.x_min, work_area.y_min])
            )
        ),
        path_count=1,
        point_count=len(rectangle),
    )
