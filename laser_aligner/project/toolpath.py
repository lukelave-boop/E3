from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ..air_assist import AirAssistCommands
from ..config import LaserSettings, WorkArea
from ..errors import SafetyError
from ..gcode.generator import (
    ToolpathOptions,
    generate_frame_gcode,
    generate_frame_path_gcode,
    validate_paths,
)
from ..gcode.job_plan import (
    JobPlan,
    JobPlanCancelled,
    build_job_plan,
    e3_metadata_line,
)
from ..geometry.polygon import (
    convex_polygon_violation_normalized_mm,
    normalize_convex_polygon,
)
from ..geometry.svg import Polyline
from ..planning.cache import PlanningCache
from ..planning.digest import (
    polyline_sequence_digest,
    project_scene_revision,
    stage_dependency_digest,
)
from ..planning.model import (
    ArtifactMetadata,
    ControllerGeometryArtifact,
    CoordinateDomain,
    EncodedProgramArtifact,
    LayerOperation,
    NormalizedGeometryArtifact,
    OperationArtifact,
    PlacedGeometryArtifact,
    PlanningStage,
    RasterRow,
    RasterSource,
    SceneRevision,
)
from .model import (
    CoordinateSpace,
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    SceneObject,
)
from .path_geometry import (
    MAX_NATIVE_PATH_FLATTENED_POINTS,
    MAX_NATIVE_PATH_SUBDIVISION_DEPTH,
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathFillRule,
    PathSubpath,
    flatten_native_path,
    native_path_bounds,
    split_cubic,
    transform_native_path,
)
from .planner_limits import MAX_NEAREST_ORDER_PATHS as _MAX_NEAREST_ORDER_PATHS
from .planner_limits import MAX_RASTER_ROWS as _MAX_RASTER_ROWS
from .planner_limits import MAX_RASTER_SAMPLES as _MAX_RASTER_SAMPLES
from .planner_limits import MAX_SCANLINE_EDGE_TESTS as _MAX_SCANLINE_EDGE_TESTS
from .planner_limits import MAX_STREAM_COMMANDS as _MAX_STREAM_COMMANDS
from .planner_limits import MAX_UNIQUE_RASTER_ASSETS as _MAX_UNIQUE_RASTER_ASSETS
from .planner_limits import (
    MAX_UNIQUE_RASTER_DECODED_BYTES as _MAX_UNIQUE_RASTER_DECODED_BYTES,
)
from .planner_limits import (
    MAX_UNIQUE_RASTER_ENCODED_BYTES as _MAX_UNIQUE_RASTER_ENCODED_BYTES,
)
from .planner_limits import STREAM_COMMAND_RESERVE as _STREAM_COMMAND_RESERVE
from .power_correction import (
    DEFAULT_RAMP_STEPS,
    corrected_raster_span_motions,
    corrected_vector_motions,
)
from .raster_asset import (
    RasterAssetIdentity,
    decode_raster_grayscale,
    probe_raster_asset,
    read_raster_asset_payload,
    verify_raster_asset_identities,
)

if TYPE_CHECKING:
    from ..calibration.support import HoneycombCoordinateFrame


NATIVE_PATH_FLATTEN_TOLERANCE_MM = 0.025
NATIVE_PATH_FLATTEN_ALGORITHM_VERSION = 1
_NATIVE_PATH_FLATTEN_MAX_POINTS = MAX_NATIVE_PATH_FLATTENED_POINTS
_NATIVE_PATH_SUBDIVISION_MAX_DEPTH = MAX_NATIVE_PATH_SUBDIVISION_DEPTH
_NATIVE_PATH_TOPOLOGY_ALGORITHM_VERSION = 2
_NATIVE_PATH_TOPOLOGY_MAX_TESTS = _MAX_SCANLINE_EDGE_TESTS
_NATIVE_PATH_TOPOLOGY_NUMERIC_MARGIN_FLOOR_MM = 1e-9
_NATIVE_PATH_TOPOLOGY_NUMERIC_MARGIN_RELATIVE = 1e-12
_NORMALIZED_GEOMETRY_STAGE_VERSION = 2


class ToolpathGenerationCancelled(RuntimeError):
    """Cooperative cancellation of desktop background job planning."""


_TOOLPATH_CANCEL_CHECK: ContextVar[Callable[[], bool] | None] = ContextVar(
    "toolpath_cancel_check",
    default=None,
)


def _toolpath_cancel_requested() -> bool:
    cancel_check = _TOOLPATH_CANCEL_CHECK.get()
    return bool(cancel_check is not None and cancel_check())


def _raise_if_toolpath_cancelled() -> None:
    if _toolpath_cancel_requested():
        raise ToolpathGenerationCancelled("Toolpath generation was cancelled")


@dataclass(slots=True)
class _NormalizedPointBudget:
    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def native_max_points(self) -> int:
        if self.remaining < 1:
            raise self._exceeded()
        return self.remaining

    def consume(self, paths: Iterable[Polyline]) -> None:
        _raise_if_toolpath_cancelled()
        point_count = sum(len(path.points) for path in paths)
        if point_count > self.remaining:
            raise self._exceeded()
        self.used += point_count

    def _exceeded(self) -> ValueError:
        return ValueError(
            "Project vector normalization exceeds the aggregate "
            f"{self.limit:,}-point limit; simplify the project geometry"
        )


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
    spot_offset_mm: tuple[float, float] = (0.0, 0.0)
    air_assist_commands: AirAssistCommands | None = None
    raster_assets: tuple[RasterAssetIdentity, ...] = ()
    coordinate_space: CoordinateSpace = CoordinateSpace.MACHINE
    coordinate_frame_signature: tuple[str, int, str] | None = None
    execution_signature: tuple[Any, ...] | None = None
    guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None


# An 8x8 Bayer matrix preserves deterministic grayscale detail without requiring
# controller-specific inline S words on motion commands.
_BAYER_8X8 = np.asarray(
    [
        [0, 48, 12, 60, 3, 51, 15, 63],
        [32, 16, 44, 28, 35, 19, 47, 31],
        [8, 56, 4, 52, 11, 59, 7, 55],
        [40, 24, 36, 20, 43, 27, 39, 23],
        [2, 50, 14, 62, 1, 49, 13, 61],
        [34, 18, 46, 30, 33, 17, 45, 29],
        [10, 58, 6, 54, 9, 57, 5, 53],
        [42, 26, 38, 22, 41, 25, 37, 21],
    ],
    dtype=np.float32,
)


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


def _object_path_transform(item: SceneObject) -> PathAffineTransform:
    transform = item.transform
    return PathAffineTransform.from_components(
        scale_x=transform.width_mm * (-1.0 if transform.mirror_x else 1.0),
        scale_y=transform.height_mm * (-1.0 if transform.mirror_y else 1.0),
        rotation_deg=transform.rotation_deg,
        translate_x=transform.x_mm,
        translate_y=transform.y_mm,
    )


def _object_native_path(item: SceneObject) -> NativePathGeometry | None:
    if item.kind not in {ObjectKind.PATH, ObjectKind.POLYGON}:
        return None
    return item.path_geometry()


def _project_native_path(item: SceneObject) -> NativePathGeometry | None:
    geometry = _object_native_path(item)
    if geometry is None:
        return None
    return transform_native_path(geometry, _object_path_transform(item))


def _iter_native_cubic_bounds(
    geometry: NativePathGeometry,
) -> Iterable[tuple[float, float, float, float]]:
    """Yield exact bounds for each cubic without including adjacent line segments."""

    for subpath in geometry.subpaths:
        current = subpath.start
        for segment in subpath.segments:
            if isinstance(segment, PathCubicSegment):
                cubic = NativePathGeometry(
                    (PathSubpath(current, (segment,), closed=False),),
                )
                yield native_path_bounds(cubic)
            current = segment.to


def _point_segment_distance_squared(
    point: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
) -> float:
    vector = end - start
    length_squared = float(np.dot(vector, vector))
    if length_squared <= 1e-30:
        difference = point - start
        return float(np.dot(difference, difference))
    ratio = float(np.dot(point - start, vector)) / length_squared
    ratio = max(0.0, min(1.0, ratio))
    difference = point - (start + ratio * vector)
    return float(np.dot(difference, difference))


def _orientation(first: np.ndarray, second: np.ndarray, third: np.ndarray) -> float:
    first_delta = second - first
    second_delta = third - first
    return float(
        first_delta[0] * second_delta[1]
        - first_delta[1] * second_delta[0]
    )


def _segments_touch_or_intersect(
    first_start: np.ndarray,
    first_end: np.ndarray,
    second_start: np.ndarray,
    second_end: np.ndarray,
    *,
    clearance_mm: float,
) -> bool:
    first_side_start = _orientation(first_start, first_end, second_start)
    first_side_end = _orientation(first_start, first_end, second_end)
    second_side_start = _orientation(second_start, second_end, first_start)
    second_side_end = _orientation(second_start, second_end, first_end)
    if (
        (first_side_start > 0.0 > first_side_end)
        or (first_side_start < 0.0 < first_side_end)
    ) and (
        (second_side_start > 0.0 > second_side_end)
        or (second_side_start < 0.0 < second_side_end)
    ):
        return True
    tolerance_squared = clearance_mm**2
    return min(
        _point_segment_distance_squared(first_start, second_start, second_end),
        _point_segment_distance_squared(first_end, second_start, second_end),
        _point_segment_distance_squared(second_start, first_start, first_end),
        _point_segment_distance_squared(second_end, first_start, first_end),
    ) <= tolerance_squared


def _closed_paths_touch_or_intersect(
    first: np.ndarray,
    second: np.ndarray,
    *,
    clearance_mm: float,
) -> bool:
    first_starts = first[:-1]
    first_ends = first[1:]
    second_starts = second[:-1]
    second_ends = second[1:]
    if len(first_starts) > len(second_starts):
        first_starts, second_starts = second_starts, first_starts
        first_ends, second_ends = second_ends, first_ends
    second_minimum = np.minimum(second_starts, second_ends)
    second_maximum = np.maximum(second_starts, second_ends)
    tolerance = clearance_mm
    for first_start, first_end in zip(first_starts, first_ends, strict=True):
        first_minimum = np.minimum(first_start, first_end) - tolerance
        first_maximum = np.maximum(first_start, first_end) + tolerance
        candidates = np.flatnonzero(
            np.all(second_maximum >= first_minimum, axis=1)
            & np.all(second_minimum <= first_maximum, axis=1)
        )
        for index in candidates:
            if _segments_touch_or_intersect(
                first_start,
                first_end,
                second_starts[index],
                second_ends[index],
                clearance_mm=clearance_mm,
            ):
                return True
    return False


def _native_path_topology_numeric_margin_mm(
    first: np.ndarray,
    second: np.ndarray,
) -> float:
    scale = max(
        float(np.max(np.abs(first))),
        float(np.max(np.abs(second))),
    )
    return max(
        _NATIVE_PATH_TOPOLOGY_NUMERIC_MARGIN_FLOOR_MM,
        scale * _NATIVE_PATH_TOPOLOGY_NUMERIC_MARGIN_RELATIVE,
    )


def _validate_native_closed_subpath_topology(
    paths: list[np.ndarray],
    closed: list[bool],
    curve_envelopes_mm: list[float],
    *,
    source_tag: str,
) -> None:
    """Prove closed subpaths remain disjoint across both flattening envelopes."""

    candidates = [
        (path, path.min(axis=0), path.max(axis=0), envelope)
        for path, is_closed, envelope in zip(
            paths,
            closed,
            curve_envelopes_mm,
            strict=True,
        )
        if is_closed and len(path) >= 2
    ]
    candidates.sort(key=lambda value: float(value[1][0]))
    work = 0
    maximum_envelope = max(
        (candidate[3] for candidate in candidates),
        default=0.0,
    )
    global_scale = max(
        (float(np.max(np.abs(candidate[0]))) for candidate in candidates),
        default=0.0,
    )
    global_numeric_margin = max(
        _NATIVE_PATH_TOPOLOGY_NUMERIC_MARGIN_FLOOR_MM,
        global_scale * _NATIVE_PATH_TOPOLOGY_NUMERIC_MARGIN_RELATIVE,
    )
    for position, (
        first,
        first_minimum,
        first_maximum,
        first_envelope,
    ) in enumerate(candidates):
        maximum_pair_clearance = (
            first_envelope + maximum_envelope + global_numeric_margin
        )
        for second_position in range(position + 1, len(candidates)):
            second, second_minimum, second_maximum, second_envelope = candidates[
                second_position
            ]
            work += 1
            if work > _NATIVE_PATH_TOPOLOGY_MAX_TESTS:
                raise ValueError(
                    "Native compound path topology exceeds the bounded intersection "
                    f"test limit for {source_tag}; simplify the source artwork"
                )
            if second_minimum[0] > first_maximum[0] + maximum_pair_clearance:
                break
            clearance = (
                first_envelope
                + second_envelope
                + _native_path_topology_numeric_margin_mm(first, second)
            )
            if (
                second_minimum[1] > first_maximum[1] + clearance
                or second_maximum[1] < first_minimum[1] - clearance
            ):
                continue
            edge_tests = (len(first) - 1) * (len(second) - 1)
            if edge_tests > _NATIVE_PATH_TOPOLOGY_MAX_TESTS - work:
                raise ValueError(
                    "Native compound path topology exceeds the bounded intersection "
                    f"test limit for {source_tag}; simplify the source artwork"
                )
            work += edge_tests
            if _closed_paths_touch_or_intersect(
                first,
                second,
                clearance_mm=clearance,
            ):
                raise ValueError(
                    "Native compound path has touching or intersecting closed "
                    "subpaths, or insufficient flattened clearance to prove them "
                    f"disjoint after {NATIVE_PATH_FLATTEN_TOLERANCE_MM:g} mm planning "
                    f"flattening: {source_tag}; flattened boundaries must be separated "
                    f"by more than {first_envelope + second_envelope:g} mm plus the "
                    "scale-aware numeric margin"
                )


def _flatten_object_native_path(
    item: SceneObject,
    *,
    max_points: int | None = None,
) -> list[Polyline]:
    geometry = _object_native_path(item)
    if geometry is None:
        return []
    flattened = flatten_native_path(
        geometry,
        NATIVE_PATH_FLATTEN_TOLERANCE_MM,
        transform=_object_path_transform(item),
        max_points=(
            _NATIVE_PATH_FLATTEN_MAX_POINTS if max_points is None else max_points
        ),
        max_depth=_NATIVE_PATH_SUBDIVISION_MAX_DEPTH,
    )
    arrays = [np.asarray(points, dtype=np.float64) for points in flattened]
    closed = [subpath.closed for subpath in geometry.subpaths]
    curve_envelopes = [
        (
            NATIVE_PATH_FLATTEN_TOLERANCE_MM
            if any(
                isinstance(segment, PathCubicSegment)
                for segment in subpath.segments
            )
            else 0.0
        )
        for subpath in geometry.subpaths
    ]
    _validate_native_closed_subpath_topology(
        arrays,
        closed,
        curve_envelopes,
        source_tag=item.name,
    )
    return [
        Polyline(
            points,
            closed=is_closed,
            source_tag=item.name,
        )
        for points, is_closed in zip(arrays, closed, strict=True)
    ]


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
        return _flatten_object_native_path(item)
    return []


def _budgeted_object_polylines(
    item: SceneObject,
    point_budget: _NormalizedPointBudget | None,
) -> list[Polyline]:
    if point_budget is None:
        return object_polylines(item)
    try:
        paths = (
            _flatten_object_native_path(
                item,
                max_points=point_budget.native_max_points(),
            )
            if item.visible and item.kind in {ObjectKind.PATH, ObjectKind.POLYGON}
            else object_polylines(item)
        )
    except ValueError as exc:
        if "bounded point limit" not in str(exc):
            raise
        raise point_budget._exceeded() from exc
    point_budget.consume(paths)
    return paths


def _prepared_object_polylines(
    item: SceneObject,
    prepared: dict[str, tuple[Polyline, ...]] | None,
    *,
    point_budget: _NormalizedPointBudget | None = None,
) -> list[Polyline]:
    if prepared is None:
        return _budgeted_object_polylines(item, point_budget)
    cached = prepared.get(item.id)
    if cached is None:
        cached = tuple(_budgeted_object_polylines(item, point_budget))
        prepared[item.id] = cached
    return list(cached)


def _object_fill_rule(item: SceneObject) -> PathFillRule:
    geometry = _object_native_path(item)
    return PathFillRule.EVENODD if geometry is None else geometry.fill_rule


def _nearest_order(paths: list[Polyline], start: np.ndarray) -> list[Polyline]:
    remaining: list[Polyline] = []
    for path in paths:
        _raise_if_toolpath_cancelled()
        remaining.append(
            Polyline(path.points.copy(), path.closed, path.source_tag)
        )
    ordered: list[Polyline] = []
    current = start.copy()
    while remaining:
        _raise_if_toolpath_cancelled()
        best_index = 0
        best_points: np.ndarray | None = None
        best_distance = float("inf")
        for index, path in enumerate(remaining):
            _raise_if_toolpath_cancelled()
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


def _point_in_closed_path(point: np.ndarray, polygon: np.ndarray) -> bool:
    """Even/odd containment independent of SVG winding direction."""
    vertices = polygon[:-1] if np.linalg.norm(polygon[0] - polygon[-1]) <= 1e-9 else polygon
    inside = False
    previous = vertices[-1]
    for current in vertices:
        _raise_if_toolpath_cancelled()
        cross = (current[1] > point[1]) != (previous[1] > point[1])
        if cross:
            x = (previous[0] - current[0]) * (point[1] - current[1]) / (previous[1] - current[1]) + current[0]
            if point[0] < x:
                inside = not inside
        previous = current
    return inside


def _containment_plan(paths: list[Polyline]) -> tuple[list[int], list[bool]]:
    """Return nesting depths and contours participating in containment."""
    depths = [0] * len(paths)
    nested = [False] * len(paths)
    bounds = []
    for path in paths:
        _raise_if_toolpath_cancelled()
        minimum = path.points.min(axis=0)
        maximum = path.points.max(axis=0)
        bounds.append((minimum, maximum))
    for index, inner in enumerate(paths):
        _raise_if_toolpath_cancelled()
        if not inner.closed or len(inner.points) < 4:
            continue
        probe = inner.points[0]
        for other_index, outer in enumerate(paths):
            _raise_if_toolpath_cancelled()
            if other_index == index or not outer.closed or len(outer.points) < 4:
                continue
            inner_min, inner_max = bounds[index]
            outer_min, outer_max = bounds[other_index]
            if (np.all(outer_min <= inner_min + 1e-9) and np.all(outer_max >= inner_max - 1e-9)
                    and np.any(outer_max - outer_min > inner_max - inner_min + 1e-9)
                    and _point_in_closed_path(probe, outer.points)):
                depths[index] += 1
                nested[index] = True
                nested[other_index] = True
    return depths, nested


def _containment_depths(paths: list[Polyline]) -> list[int]:
    """Return closed-contour nesting depths; open paths remain depth zero."""
    return _containment_plan(paths)[0]


def _containment_aware_nearest_order(paths: list[Polyline], start: np.ndarray) -> list[Polyline]:
    """Optimize travel without ever releasing a containing contour first."""
    depths = _containment_depths(paths)
    ordered: list[Polyline] = []
    current = start.copy()
    for depth in sorted(set(depths), reverse=True):
        group = [path for path, path_depth in zip(paths, depths, strict=True) if path_depth == depth]
        selected = _nearest_order(group, current)
        ordered.extend(selected)
        if selected:
            current = selected[-1].points[-1].copy()
    return ordered


def _containment_aware_source_order(paths: list[Polyline]) -> list[Polyline]:
    """Keep source order within each depth while always scheduling holes first."""
    depths = _containment_depths(paths)
    return [
        path
        for depth in sorted(set(depths), reverse=True)
        for path, path_depth in zip(paths, depths, strict=True)
        if path_depth == depth
    ]


def _bounds(paths: Iterable[Polyline]) -> tuple[float, float, float, float]:
    arrays = [path.points for path in paths if len(path.points)]
    if not arrays:
        raise ValueError("No usable paths")
    points = np.vstack(arrays)
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])


def _project_work_area(document: ProjectDocument) -> WorkArea:
    return WorkArea(
        x_min=document.work_area.x_min,
        x_max=document.work_area.x_max,
        y_min=document.work_area.y_min,
        y_max=document.work_area.y_max,
    )


def _coordinate_context(
    document: ProjectDocument,
    coordinate_frame: HoneycombCoordinateFrame | None,
    machine_work_area: WorkArea | None,
) -> tuple[WorkArea, WorkArea, tuple[str, int, str] | None]:
    """Resolve separate document and execution coordinate domains.

    Machine-coordinate projects preserve the historical behavior. A
    honeycomb-local project is intentionally unusable without both the exact
    reviewed rigid frame and an independent machine envelope.
    """

    local_work_area = _project_work_area(document)
    if document.coordinate_space is CoordinateSpace.MACHINE:
        if coordinate_frame is not None:
            raise ValueError(
                "A honeycomb coordinate frame cannot be applied to a machine-coordinate project"
            )
        return local_work_area, machine_work_area or local_work_area, None

    if document.coordinate_space is not CoordinateSpace.HONEYCOMB_LOCAL:
        raise ValueError(
            f"Unsupported project coordinate space: {document.coordinate_space!r}"
        )
    if coordinate_frame is None:
        raise SafetyError(
            "Honeycomb-local output requires the reviewed honeycomb coordinate frame"
        )
    if machine_work_area is None:
        raise SafetyError(
            "Honeycomb-local output requires an independent machine work area"
        )
    expected = (0.0, float(coordinate_frame.width_mm), 0.0, float(coordinate_frame.height_mm))
    actual = (
        local_work_area.x_min,
        local_work_area.x_max,
        local_work_area.y_min,
        local_work_area.y_max,
    )
    if any(abs(left - right) > 1e-6 for left, right in zip(actual, expected, strict=True)):
        raise SafetyError(
            "Honeycomb-local project bounds do not match the reviewed support frame: "
            f"project X{actual[0]:g}..{actual[1]:g} Y{actual[2]:g}..{actual[3]:g}; "
            f"support X0..{expected[1]:g} Y0..{expected[3]:g}"
        )
    return local_work_area, machine_work_area, coordinate_frame.provenance_signature


def _validate_paths_in_guarded_polygon(
    paths: list[Polyline],
    polygon: tuple[tuple[float, float], ...],
    *,
    coordinate_label: str,
) -> None:
    normalized = normalize_convex_polygon(
        polygon,
        label="guarded output polygon",
    )
    if not paths or any(
        len(path.points) == 0 or not np.isfinite(path.points).all()
        for path in paths
    ):
        raise ValueError("Path coordinates must be finite and nonempty")
    maximum = max(
        convex_polygon_violation_normalized_mm(point, normalized)
        for path in paths
        for point in path.points
    )
    if maximum > 1e-6:
        raise SafetyError(
            f"Path exceeds the configured guarded output polygon: "
            f"{coordinate_label} escapes by {maximum:.2f} mm"
        )


def _validate_native_curves_in_work_area(
    paths: Iterable[NativePathGeometry],
    work_area: WorkArea,
    margin_mm: float,
    *,
    coordinate_label: str,
) -> None:
    """Prove complete cubic curves fit, including the flattening error envelope."""

    envelope = NATIVE_PATH_FLATTEN_TOLERANCE_MM
    tolerance = 1e-6
    safe_minimum_x = work_area.x_min + margin_mm
    safe_maximum_x = work_area.x_max - margin_mm
    safe_minimum_y = work_area.y_min + margin_mm
    safe_maximum_y = work_area.y_max - margin_mm
    for geometry in paths:
        for minimum_x, minimum_y, maximum_x, maximum_y in _iter_native_cubic_bounds(
            geometry
        ):
            if (
                minimum_x - envelope < safe_minimum_x - tolerance
                or maximum_x + envelope > safe_maximum_x + tolerance
                or minimum_y - envelope < safe_minimum_y - tolerance
                or maximum_y + envelope > safe_maximum_y + tolerance
            ):
                raise SafetyError(
                    "Path exceeds the configured safe work area: "
                    f"{coordinate_label} native curve plus its {envelope:g} mm "
                    "flattening envelope is outside the authorized rectangle"
                )


def _polygon_clearance_mm(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> float:
    clearance = math.inf
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        edge_x = end[0] - start[0]
        edge_y = end[1] - start[1]
        cross = edge_x * (point[1] - start[1]) - edge_y * (
            point[0] - start[0]
        )
        clearance = min(clearance, cross / math.hypot(edge_x, edge_y))
    return clearance


def _point_has_curve_clearance(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    return (
        _polygon_clearance_mm(point, polygon)
        >= NATIVE_PATH_FLATTEN_TOLERANCE_MM - 1e-6
    )


def _cubic_hull_proves_guarded_containment(
    start: tuple[float, float],
    segment: PathCubicSegment,
    polygon: tuple[tuple[float, float], ...],
    *,
    depth: int = 0,
) -> bool:
    if not (
        _point_has_curve_clearance(start, polygon)
        and _point_has_curve_clearance(segment.to, polygon)
    ):
        return False
    if all(
        _point_has_curve_clearance(point, polygon)
        for point in (start, segment.control_1, segment.control_2, segment.to)
    ):
        return True
    if depth >= _NATIVE_PATH_SUBDIVISION_MAX_DEPTH:
        return False
    first, second = split_cubic(start, segment)
    return _cubic_hull_proves_guarded_containment(
        start,
        first,
        polygon,
        depth=depth + 1,
    ) and _cubic_hull_proves_guarded_containment(
        first.to,
        second,
        polygon,
        depth=depth + 1,
    )


def _native_path_proves_guarded_containment(
    geometry: NativePathGeometry,
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    for subpath in geometry.subpaths:
        current = subpath.start
        for segment in subpath.segments:
            if isinstance(segment, PathCubicSegment):
                if not _cubic_hull_proves_guarded_containment(
                    current,
                    segment,
                    polygon,
                ):
                    return False
            current = segment.to
    return True


def _validate_native_curves_in_guarded_polygon(
    paths: Iterable[NativePathGeometry],
    polygon: tuple[tuple[float, float], ...],
    *,
    coordinate_label: str,
) -> None:
    normalized = normalize_convex_polygon(
        polygon,
        label="guarded output polygon",
    )
    for geometry in paths:
        if not _native_path_proves_guarded_containment(
            geometry,
            normalized,
        ):
            raise SafetyError(
                "Path exceeds the configured guarded output polygon: "
                f"{coordinate_label} native curve cannot be proven inside with its "
                f"{NATIVE_PATH_FLATTEN_TOLERANCE_MM:g} mm flattening envelope"
            )


def _coordinate_frame_matrix(
    coordinate_frame: HoneycombCoordinateFrame,
) -> tuple[np.ndarray, np.ndarray]:
    origin = np.asarray(coordinate_frame.origin_machine_mm, dtype=np.float64)
    axes = np.asarray(
        [coordinate_frame.x_axis_machine, coordinate_frame.y_axis_machine],
        dtype=np.float64,
    )
    if origin.shape != (2,) or axes.shape != (2, 2) or not (
        np.isfinite(origin).all() and np.isfinite(axes).all()
    ):
        raise ValueError("Honeycomb coordinate frame contains invalid geometry")
    if not np.allclose(axes @ axes.T, np.eye(2), atol=1e-9, rtol=0.0) or not math.isclose(
        float(np.linalg.det(axes)), 1.0, abs_tol=1e-9
    ):
        raise ValueError("Honeycomb coordinate frame must be a right-handed rigid transform")
    return origin, axes


def _coordinate_frame_path_transform(
    coordinate_frame: HoneycombCoordinateFrame | None,
) -> PathAffineTransform:
    if coordinate_frame is None:
        return PathAffineTransform()
    origin, axes = _coordinate_frame_matrix(coordinate_frame)
    return PathAffineTransform(
        m11=float(axes[0, 0]),
        m12=float(axes[1, 0]),
        m21=float(axes[0, 1]),
        m22=float(axes[1, 1]),
        dx=float(origin[0]),
        dy=float(origin[1]),
    )


def _place_native_paths(
    paths: Iterable[NativePathGeometry],
    coordinate_frame: HoneycombCoordinateFrame | None,
) -> list[NativePathGeometry]:
    transform = _coordinate_frame_path_transform(coordinate_frame)
    return [transform_native_path(path, transform) for path in paths]


def _controller_native_paths(
    paths: Iterable[NativePathGeometry],
    laser: LaserSettings,
) -> list[NativePathGeometry]:
    transform = PathAffineTransform(
        dx=-float(laser.spot_offset_x_mm),
        dy=-float(laser.spot_offset_y_mm),
    )
    return [transform_native_path(path, transform) for path in paths]


def _place_points(
    points: np.ndarray,
    coordinate_frame: HoneycombCoordinateFrame | None,
) -> np.ndarray:
    output = np.asarray(points, dtype=np.float64).reshape(-1, 2).copy()
    if coordinate_frame is None:
        return output
    origin, axes = _coordinate_frame_matrix(coordinate_frame)
    return output @ axes + origin


def _place_paths(
    paths: Iterable[Polyline],
    coordinate_frame: HoneycombCoordinateFrame | None,
) -> list[Polyline]:
    return [
        Polyline(
            _place_points(path.points, coordinate_frame),
            closed=path.closed,
            source_tag=path.source_tag,
        )
        for path in paths
    ]


def _place_raster_rows(
    rows: Iterable[RasterRow],
    coordinate_frame: HoneycombCoordinateFrame | None,
) -> list[RasterRow]:
    return [
        RasterRow(
            points=_place_points(row.points, coordinate_frame),
            spans=_place_paths(row.spans, coordinate_frame),
            source_tag=row.source_tag,
        )
        for row in rows
    ]


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


def _raster_motion_points(points: np.ndarray, overscan_percent: float) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(points) != 2 or overscan_percent <= 0:
        return points.copy()
    vector = points[1] - points[0]
    length = float(np.linalg.norm(vector))
    if length <= 1e-12:
        return points.copy()
    extension = vector / length * (length * overscan_percent / 100.0)
    return np.vstack([points[0] - extension, points[0], points[1], points[1] + extension])


def _reverse_raster_row(row: RasterRow) -> RasterRow:
    return RasterRow(
        points=row.points[::-1].copy(),
        spans=[
            Polyline(span.points[::-1].copy(), closed=False, source_tag=span.source_tag)
            for span in reversed(row.spans)
        ],
        source_tag=row.source_tag,
    )


def _controller_raster_rows(
    rows: list[RasterRow],
    laser: LaserSettings,
) -> list[RasterRow]:
    offset = np.array(
        [laser.spot_offset_x_mm, laser.spot_offset_y_mm],
        dtype=np.float64,
    )
    if not np.isfinite(offset).all():
        raise ValueError("laser spot offsets must be finite")
    return [
        RasterRow(
            points=row.points.astype(np.float64, copy=True) - offset,
            spans=[
                Polyline(
                    span.points.astype(np.float64, copy=True) - offset,
                    closed=False,
                    source_tag=span.source_tag,
                )
                for span in row.spans
            ],
            source_tag=row.source_tag,
        )
        for row in rows
    ]


def _raster_motion_paths(
    rows: list[RasterRow],
    overscan_percent: float,
) -> list[Polyline]:
    return [
        Polyline(
            _raster_motion_points(row.points, overscan_percent),
            closed=False,
            source_tag=row.source_tag,
        )
        for row in rows
    ]


def _raster_row_command_count(
    row: RasterRow,
    overscan_percent: float,
    *,
    powered: bool,
    power_correction: float = 0.0,
) -> int:
    motion = _raster_motion_points(row.points, overscan_percent)
    count = 1  # G0 to the row's laser-off lead-in.
    if len(motion) == 4:
        count += 2  # G1 to the image edge and G1 through lead-out.
    if not powered:
        return count + 1  # One laser-off G1 across the full row.
    position = row.points[0]
    for span in row.spans:
        if float(np.linalg.norm(span.points[0] - position)) > 1e-9:
            count += 1  # Laser-off gap.
        count += 3  # M3/M4, powered G1, standalone M5.
        if power_correction != 0.0:
            # Each image edge can add at most ``ramp_steps`` subdivisions.
            # This conservative bound keeps the final stream below its hard cap
            # without doing controller-specific motion planning here.
            count += 2 * DEFAULT_RAMP_STEPS
        position = span.points[-1]
    if float(np.linalg.norm(row.points[-1] - position)) > 1e-9:
        count += 1
    return count


def _normalized_layer_dependency_payload(
    document: ProjectDocument,
    layer: OperationLayer,
) -> dict[str, Any]:
    """Capture only source fields consumed by LINE geometry normalization."""

    return {
        "layer_id": layer.id,
        "native_path_flattening": {
            "algorithm_version": NATIVE_PATH_FLATTEN_ALGORITHM_VERSION,
            "tolerance_mm": NATIVE_PATH_FLATTEN_TOLERANCE_MM,
            "maximum_points": _NATIVE_PATH_FLATTEN_MAX_POINTS,
            "maximum_subdivision_depth": _NATIVE_PATH_SUBDIVISION_MAX_DEPTH,
        },
        "native_path_topology": {
            "algorithm_version": _NATIVE_PATH_TOPOLOGY_ALGORITHM_VERSION,
            "clearance_policy": "sum-per-subpath-curve-envelopes-plus-numeric-margin",
            "curve_envelope_mm": NATIVE_PATH_FLATTEN_TOLERANCE_MM,
            "numeric_margin_floor_mm": (
                _NATIVE_PATH_TOPOLOGY_NUMERIC_MARGIN_FLOOR_MM
            ),
            "numeric_margin_relative": (
                _NATIVE_PATH_TOPOLOGY_NUMERIC_MARGIN_RELATIVE
            ),
            "maximum_tests": _NATIVE_PATH_TOPOLOGY_MAX_TESTS,
        },
        "objects": [
            {
                "name": item.name,
                "kind": item.kind.value,
                "transform": item.transform.to_dict(),
                "geometry": item.geometry,
            }
            for item in document.objects
            if (
                item.layer_id == layer.id
                and item.visible
                and item.is_output_geometry
            )
        ],
    }


def _layer_paths(
    document: ProjectDocument,
    layer: OperationLayer,
    *,
    point_budget: _NormalizedPointBudget | None = None,
) -> list[Polyline]:
    paths: list[Polyline] = []
    for item in document.objects:
        if (
            item.layer_id == layer.id
            and item.visible
            and item.is_output_geometry
        ):
            paths.extend(_budgeted_object_polylines(item, point_budget))
    return paths


def _normalized_layer_geometry(
    document: ProjectDocument,
    layer: OperationLayer,
    scene_revision: SceneRevision | None = None,
    *,
    planning_cache: PlanningCache | None = None,
    point_budget: _NormalizedPointBudget | None = None,
    deferred_cache_writes: dict[
        str,
        tuple[
            tuple[Polyline, ...],
            tuple[float, float, float, float] | None,
        ],
    ]
    | None = None,
) -> NormalizedGeometryArtifact:
    """Capture or safely reuse LINE geometry at the normalized stage boundary."""

    dependency_digest = stage_dependency_digest(
        PlanningStage.NORMALIZED_GEOMETRY,
        _NORMALIZED_GEOMETRY_STAGE_VERSION,
        _normalized_layer_dependency_payload(document, layer),
    )
    cached = (
        None
        if planning_cache is None
        else planning_cache.get_normalized(dependency_digest)
    )
    if cached is None:
        paths = tuple(_layer_paths(document, layer, point_budget=point_budget))
        bounds_mm = _bounds(paths) if paths else None
        if planning_cache is not None:
            if deferred_cache_writes is None:
                planning_cache.put_normalized(
                    dependency_digest,
                    paths,
                    bounds_mm,
                )
            else:
                deferred_cache_writes[dependency_digest] = (paths, bounds_mm)
    else:
        paths, bounds_mm = cached
        if point_budget is not None:
            point_budget.consume(paths)
    native_geometries = [
        item.path_geometry()
        for item in document.objects
        if (
            item.layer_id == layer.id
            and item.visible
            and item.is_output_geometry
            and item.kind in {ObjectKind.PATH, ObjectKind.POLYGON}
        )
    ]
    flattened_point_count = sum(len(path.points) for path in paths)
    metadata = ArtifactMetadata(
        artifact_id=(
            f"{document.id}:{document.revision}:"
            f"{PlanningStage.NORMALIZED_GEOMETRY.value}:{layer.id}:"
            f"v{_NORMALIZED_GEOMETRY_STAGE_VERSION}"
        ),
        scene_revision=scene_revision or project_scene_revision(document),
        stage=PlanningStage.NORMALIZED_GEOMETRY,
        stage_version=_NORMALIZED_GEOMETRY_STAGE_VERSION,
        coordinate_domain=CoordinateDomain.PROJECT,
        dependency_digest=dependency_digest,
        bounds_mm=bounds_mm,
        statistics=(
            ("layer_count", 1),
            ("path_count", len(paths)),
            ("point_count", flattened_point_count),
            ("native_path_count", len(native_geometries)),
            (
                "native_subpath_count",
                sum(len(geometry.subpaths) for geometry in native_geometries),
            ),
            (
                "native_segment_count",
                sum(geometry.segment_count for geometry in native_geometries),
            ),
            ("flattened_path_count", len(paths)),
            ("flattened_point_count", flattened_point_count),
        ),
        provenance=(f"project-layer:{layer.id}",),
    )
    return NormalizedGeometryArtifact(
        metadata=metadata,
        layer_paths=((layer.id, paths),),
    )


def _preflight_normalized_point_budget(
    document: ProjectDocument,
    scene_revision: SceneRevision,
    *,
    planning_cache: PlanningCache | None,
    prepared_object_paths: dict[str, tuple[Polyline, ...]],
) -> tuple[
    dict[str, NormalizedGeometryArtifact],
    dict[
        str,
        tuple[
            tuple[Polyline, ...],
            tuple[float, float, float, float] | None,
        ],
    ],
]:
    """Prepare every vector source before publishing any new cache entries."""

    point_budget = _NormalizedPointBudget(_NATIVE_PATH_FLATTEN_MAX_POINTS)
    normalized_layers: dict[str, NormalizedGeometryArtifact] = {}
    deferred_cache_writes: dict[
        str,
        tuple[
            tuple[Polyline, ...],
            tuple[float, float, float, float] | None,
        ],
    ] = {}
    for layer in sorted(document.layers, key=lambda item: item.priority):
        _raise_if_toolpath_cancelled()
        if not layer.visible or not layer.output_enabled:
            continue
        layer_objects = [
            item
            for item in document.objects
            if (
                item.layer_id == layer.id
                and item.visible
                and item.is_output_geometry
            )
        ]
        if not layer_objects:
            continue
        if layer.mode == LayerMode.LINE:
            normalized_layers[layer.id] = _normalized_layer_geometry(
                document,
                layer,
                scene_revision,
                planning_cache=planning_cache,
                point_budget=point_budget,
                deferred_cache_writes=deferred_cache_writes,
            )
            continue
        for item in layer_objects:
            _prepared_object_polylines(
                item,
                prepared_object_paths,
                point_budget=point_budget,
            )
    return normalized_layers, deferred_cache_writes


def _line_operation_artifact(
    document: ProjectDocument,
    layer: OperationLayer,
    normalized: NormalizedGeometryArtifact,
) -> OperationArtifact:
    """Bind normalized LINE geometry to its existing operation-layer settings."""

    paths = list(normalized.paths_for_layer(layer.id))
    normalized_dependency = normalized.metadata.dependency_digest
    if normalized_dependency is None:
        raise RuntimeError("Normalized LINE artifact is missing its dependency digest")
    metadata = ArtifactMetadata(
        artifact_id=(
            f"{document.id}:{document.revision}:"
            f"{PlanningStage.OPERATIONS.value}:{layer.id}:v1"
        ),
        scene_revision=normalized.metadata.scene_revision,
        stage=PlanningStage.OPERATIONS,
        stage_version=1,
        coordinate_domain=CoordinateDomain.PROJECT,
        dependency_digest=stage_dependency_digest(
            PlanningStage.OPERATIONS,
            1,
            {
                "normalized_geometry": normalized_dependency,
                "layer": layer.to_dict(),
            },
        ),
        bounds_mm=normalized.metadata.bounds_mm,
        statistics=(
            ("layer_count", 1),
            ("path_count", len(paths)),
            ("point_count", sum(len(path.points) for path in paths)),
        ),
        provenance=(normalized.metadata.artifact_id,),
    )
    return OperationArtifact(
        metadata=metadata,
        layers=(LayerOperation(layer=layer, paths=paths),),
    )


def _placed_line_geometry_artifact(
    document: ProjectDocument,
    layer: OperationLayer,
    operation: OperationArtifact,
    coordinate_frame: HoneycombCoordinateFrame | None,
    coordinate_frame_signature: tuple[str, int, str] | None,
    *,
    planning_cache: PlanningCache | None = None,
) -> PlacedGeometryArtifact:
    """Apply the existing rigid placement at an explicit machine-beam boundary."""

    planned_layer = operation.layer_for_id(layer.id)
    if planned_layer is None:
        raise RuntimeError("LINE operation artifact lost its source layer")
    frame_dependency = (
        None
        if coordinate_frame is None
        else {
            "origin_machine_mm": [
                float(value) for value in coordinate_frame.origin_machine_mm
            ],
            "x_axis_machine": [
                float(value) for value in coordinate_frame.x_axis_machine
            ],
            "y_axis_machine": [
                float(value) for value in coordinate_frame.y_axis_machine
            ],
            "width_mm": float(coordinate_frame.width_mm),
            "height_mm": float(coordinate_frame.height_mm),
            "provenance_signature": coordinate_frame_signature,
        }
    )
    placement_dependency = stage_dependency_digest(
        PlanningStage.PLACED_GEOMETRY,
        1,
        {
            "geometry": polyline_sequence_digest(planned_layer.paths),
            "coordinate_frame": frame_dependency,
        },
    )
    cached = (
        None
        if planning_cache is None
        else planning_cache.get_placed(placement_dependency)
    )
    if cached is None:
        paths = tuple(_place_paths(planned_layer.paths, coordinate_frame))
        bounds_mm = _bounds(paths) if paths else None
        if planning_cache is not None:
            planning_cache.put_placed(
                placement_dependency,
                paths,
                bounds_mm,
            )
    else:
        paths, bounds_mm = cached
    metadata = ArtifactMetadata(
        artifact_id=(
            f"{document.id}:{document.revision}:"
            f"{PlanningStage.PLACED_GEOMETRY.value}:{layer.id}:v1"
        ),
        scene_revision=operation.metadata.scene_revision,
        stage=PlanningStage.PLACED_GEOMETRY,
        stage_version=1,
        coordinate_domain=CoordinateDomain.MACHINE_BEAM,
        dependency_digest=placement_dependency,
        bounds_mm=bounds_mm,
        statistics=(
            ("layer_count", 1),
            ("path_count", len(paths)),
            ("point_count", sum(len(path.points) for path in paths)),
        ),
        provenance=(operation.metadata.artifact_id,),
    )
    return PlacedGeometryArtifact(
        metadata=metadata,
        layer_paths=((layer.id, paths),),
        coordinate_frame_signature=coordinate_frame_signature,
    )


def _controller_line_geometry_artifact(
    document: ProjectDocument,
    layer: OperationLayer,
    placed: PlacedGeometryArtifact,
    laser: LaserSettings,
    *,
    planning_cache: PlanningCache | None = None,
) -> ControllerGeometryArtifact:
    """Apply the existing laser-spot correction at an explicit controller boundary."""

    beam_paths = list(placed.paths_for_layer(layer.id))
    controller_dependency = stage_dependency_digest(
        PlanningStage.CONTROLLER_GEOMETRY,
        1,
        {
            "geometry": polyline_sequence_digest(beam_paths),
            "spot_offset_mm": [
                float(laser.spot_offset_x_mm),
                float(laser.spot_offset_y_mm),
            ],
        },
    )
    cached = (
        None
        if planning_cache is None
        else planning_cache.get_controller(controller_dependency)
    )
    if cached is None:
        paths = tuple(_controller_paths(beam_paths, laser))
        bounds_mm = _bounds(paths) if paths else None
        if planning_cache is not None:
            planning_cache.put_controller(
                controller_dependency,
                paths,
                bounds_mm,
            )
    else:
        paths, bounds_mm = cached
    metadata = ArtifactMetadata(
        artifact_id=(
            f"{document.id}:{document.revision}:"
            f"{PlanningStage.CONTROLLER_GEOMETRY.value}:{layer.id}:v1"
        ),
        scene_revision=placed.metadata.scene_revision,
        stage=PlanningStage.CONTROLLER_GEOMETRY,
        stage_version=1,
        coordinate_domain=CoordinateDomain.CONTROLLER,
        dependency_digest=controller_dependency,
        bounds_mm=bounds_mm,
        statistics=(
            ("layer_count", 1),
            ("path_count", len(paths)),
            ("point_count", sum(len(path.points) for path in paths)),
        ),
        provenance=(placed.metadata.artifact_id,),
    )
    return ControllerGeometryArtifact(
        metadata=metadata,
        layer_paths=((layer.id, paths),),
        spot_offset_mm=(
            float(laser.spot_offset_x_mm),
            float(laser.spot_offset_y_mm),
        ),
    )


def _encoded_program_artifact(
    document: ProjectDocument,
    text: str,
    *,
    scene_revision: SceneRevision,
    bounds_mm: tuple[float, float, float, float],
    command_count: int,
    path_count: int,
    point_count: int,
    staged_line_artifact_ids: Iterable[str],
    unstaged_layer_count: int,
) -> EncodedProgramArtifact:
    """Wrap the exact finalized stream without changing its contents."""

    staged_ids = tuple(staged_line_artifact_ids)
    metadata = ArtifactMetadata(
        artifact_id=(
            f"{document.id}:{document.revision}:"
            f"{PlanningStage.ENCODED_PROGRAM.value}:v1"
        ),
        scene_revision=scene_revision,
        stage=PlanningStage.ENCODED_PROGRAM,
        stage_version=1,
        coordinate_domain=CoordinateDomain.PROGRAM,
        bounds_mm=bounds_mm,
        statistics=(
            ("command_count", command_count),
            ("path_count", path_count),
            ("point_count", point_count),
            ("staged_line_layer_count", len(staged_ids)),
            ("unstaged_layer_count", unstaged_layer_count),
        ),
        provenance=("project.toolpath:encoder", *staged_ids),
    )
    return EncodedProgramArtifact(
        metadata=metadata,
        text=text,
    )


def _scanline_x_intervals(
    polygons: list[np.ndarray],
    y: float,
    fill_rule: PathFillRule,
) -> list[tuple[float, float]]:
    intersections: list[float] = []
    winding_events: list[tuple[float, int]] = []
    for polygon in polygons:
        for start, end in zip(polygon[:-1], polygon[1:], strict=False):
            start_y = float(start[1])
            end_y = float(end[1])
            low_y = min(start_y, end_y)
            high_y = max(start_y, end_y)
            if high_y - low_y <= 1e-12 or not (low_y <= y < high_y):
                continue
            ratio = (y - start_y) / (end_y - start_y)
            x = float(start[0]) + ratio * (float(end[0]) - float(start[0]))
            if fill_rule is PathFillRule.EVENODD:
                intersections.append(x)
            else:
                winding_events.append((x, 1 if end_y > start_y else -1))

    if fill_rule is PathFillRule.EVENODD:
        intersections.sort()
        return [
            (intersections[index], intersections[index + 1])
            for index in range(0, len(intersections) - 1, 2)
            if intersections[index + 1] - intersections[index] > 1e-9
        ]

    winding_events.sort(key=lambda event: event[0])
    grouped: list[tuple[float, int]] = []
    for x, delta in winding_events:
        if grouped and abs(x - grouped[-1][0]) <= 1e-12:
            grouped[-1] = (grouped[-1][0], grouped[-1][1] + delta)
        else:
            grouped.append((x, delta))
    intervals: list[tuple[float, float]] = []
    winding = 0
    for index, (x, delta) in enumerate(grouped[:-1]):
        winding += delta
        next_x = grouped[index + 1][0]
        if winding != 0 and next_x - x > 1e-9:
            if intervals and x - intervals[-1][1] <= 1e-9:
                intervals[-1] = (intervals[-1][0], next_x)
            else:
                intervals.append((x, next_x))
    return intervals


def _scanline_rows(
    item: SceneObject,
    layer: OperationLayer,
    *,
    outlines: list[Polyline] | tuple[Polyline, ...] | None = None,
) -> list[RasterRow]:
    outlines = object_polylines(item) if outlines is None else list(outlines)
    if not outlines or any(not path.closed for path in outlines):
        raise ValueError(
            f"{layer.mode.value.title()} output requires closed vector geometry: "
            f"{item.name}"
        )
    to_scan, from_scan = _scan_matrices(layer.scan_angle_deg)
    polygons = [path.points @ to_scan.T for path in outlines]
    all_points = np.vstack(polygons)
    y_min = float(all_points[:, 1].min())
    y_max = float(all_points[:, 1].max())
    interval = float(layer.line_interval_mm)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Raster line interval must be positive and finite")
    first_y = math.ceil((y_min - 1e-9) / interval) * interval
    row_count = (
        0
        if first_y > y_max + 1e-9
        else int(math.floor((y_max + 1e-9 - first_y) / interval)) + 1
    )
    edge_count = sum(max(0, len(polygon) - 1) for polygon in polygons)
    row_iterations = row_count * layer.passes
    if row_iterations > _MAX_RASTER_ROWS:
        raise ValueError(
            f"Raster output for {item.name} requires {row_iterations:,} row iterations, "
            f"exceeding the {_MAX_RASTER_ROWS:,}-row planner limit; increase the line interval "
            "or simplify the vector geometry"
        )
    edge_tests = row_count * edge_count
    if edge_tests > _MAX_SCANLINE_EDGE_TESTS:
        raise ValueError(
            f"Raster output for {item.name} requires {edge_tests:,} scanline edge tests, "
            f"exceeding the {_MAX_SCANLINE_EDGE_TESTS:,}-test planner limit; increase the line "
            "interval or simplify the vector geometry"
        )
    rows: list[RasterRow] = []
    fill_rule = _object_fill_rule(item)
    for row in range(row_count):
        _raise_if_toolpath_cancelled()
        y = first_y + row * interval
        scan_spans: list[np.ndarray] = []
        for start_x, end_x in _scanline_x_intervals(polygons, y, fill_rule):
            scan_spans.append(
                np.array([[start_x, y], [end_x, y]], dtype=np.float64)
            )
        if scan_spans:
            row_points = np.array(
                [[scan_spans[0][0, 0], y], [scan_spans[-1][-1, 0], y]],
                dtype=np.float64,
            )
            if row % 2:
                row_points = row_points[::-1].copy()
                scan_spans = [span[::-1].copy() for span in reversed(scan_spans)]
            rows.append(
                RasterRow(
                    points=row_points @ from_scan.T,
                    spans=[
                        Polyline(
                            span @ from_scan.T,
                            closed=False,
                            source_tag=item.name,
                        )
                        for span in scan_spans
                    ],
                    source_tag=item.name,
                )
            )
    if not rows:
        raise ValueError(f"{layer.mode.value.title()} produced no scanlines: {item.name}")
    return rows


def _scanline_paths(
    item: SceneObject,
    layer: OperationLayer,
    *,
    outlines: list[Polyline] | tuple[Polyline, ...] | None = None,
) -> list[Polyline]:
    return [
        span
        for row in _scanline_rows(item, layer, outlines=outlines)
        for span in row.spans
    ]


def _scan_matrices(angle_degrees: float) -> tuple[np.ndarray, np.ndarray]:
    angle = math.radians(angle_degrees)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    machine_to_scan = np.array(
        [[cosine, sine], [-sine, cosine]],
        dtype=np.float64,
    )
    return machine_to_scan, machine_to_scan.T


def _vector_scan_budget(
    item: SceneObject,
    layer: OperationLayer,
    *,
    outlines: list[Polyline] | tuple[Polyline, ...] | None = None,
) -> tuple[int, int]:
    outlines = object_polylines(item) if outlines is None else list(outlines)
    if not outlines or any(not path.closed for path in outlines):
        raise ValueError(
            f"{layer.mode.value.title()} output requires closed vector geometry: "
            f"{item.name}"
        )
    to_scan, _from_scan = _scan_matrices(layer.scan_angle_deg)
    polygons = [path.points @ to_scan.T for path in outlines]
    all_points = np.vstack(polygons)
    y_min = float(all_points[:, 1].min())
    y_max = float(all_points[:, 1].max())
    interval = float(layer.line_interval_mm)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Raster line interval must be positive and finite")
    first_y = math.ceil((y_min - 1e-9) / interval) * interval
    row_count = (
        0
        if first_y > y_max + 1e-9
        else int(math.floor((y_max + 1e-9 - first_y) / interval)) + 1
    )
    edge_count = sum(max(0, len(polygon) - 1) for polygon in polygons)
    return row_count, edge_count


def _image_scan_budget(
    item: SceneObject,
    layer: OperationLayer,
) -> tuple[int, int]:
    machine_to_scan, _scan_to_machine = _scan_matrices(layer.scan_angle_deg)
    corners = np.asarray(item.transform.corners(), dtype=np.float64)
    scan_corners = corners @ machine_to_scan.T
    extent = scan_corners.max(axis=0) - scan_corners.min(axis=0)
    return (
        _axis_sample_count(float(extent[1]), layer.line_interval_mm, "Vertical"),
        _axis_sample_count(float(extent[0]), layer.line_interval_mm, "Horizontal"),
    )


def _raster_source_path(item: SceneObject) -> str:
    return str(
        Path(str(item.geometry.get("asset", ""))).expanduser().absolute()
    )


def _preflight_raster_sources(
    items: Iterable[SceneObject],
) -> dict[str, RasterSource]:
    """Probe each unique source once and bound aggregate decode work."""

    raster_sources: dict[str, RasterSource] = {}
    aggregate_encoded_bytes = 0
    aggregate_decoded_bytes = 0
    for item in items:
        if item.kind != ObjectKind.IMAGE:
            continue
        source_path = _raster_source_path(item)
        if source_path in raster_sources:
            continue
        metadata = probe_raster_asset(source_path)
        raster_sources[metadata.path] = RasterSource(metadata=metadata)
        aggregate_encoded_bytes += metadata.encoded_bytes
        aggregate_decoded_bytes += metadata.decoded_bytes

    if len(raster_sources) > _MAX_UNIQUE_RASTER_ASSETS:
        raise ValueError(
            f"Project references {len(raster_sources):,} unique raster assets, exceeding "
            f"the {_MAX_UNIQUE_RASTER_ASSETS:,}-asset planner limit"
        )
    if aggregate_encoded_bytes > _MAX_UNIQUE_RASTER_ENCODED_BYTES:
        raise ValueError(
            "Project raster sources require "
            f"{aggregate_encoded_bytes:,} aggregate encoded bytes, exceeding the "
            f"{_MAX_UNIQUE_RASTER_ENCODED_BYTES:,}-byte planner limit"
        )
    if aggregate_decoded_bytes > _MAX_UNIQUE_RASTER_DECODED_BYTES:
        raise ValueError(
            "Project raster sources require "
            f"{aggregate_decoded_bytes:,} aggregate decoded bytes, exceeding the "
            f"{_MAX_UNIQUE_RASTER_DECODED_BYTES:,}-byte planner limit"
        )
    return raster_sources


def _preflight_raster_budget(
    document: ProjectDocument,
    controller_power_max: int,
    *,
    prepared_object_paths: dict[str, tuple[Polyline, ...]] | None = None,
) -> dict[str, RasterSource]:
    """Reject aggregate raster work before constructing row/span geometry."""

    aggregate_rows = 0
    aggregate_samples = 0
    aggregate_edge_tests = 0
    aggregate_worst_spans = 0
    aggregate_commands = 0
    layer_by_id = {layer.id: layer for layer in document.layers}
    raster_items = [
        item
        for item in document.objects
        if (
            (layer := layer_by_id.get(item.layer_id)) is not None
            and layer.mode == LayerMode.RASTER
            and layer.visible
            and layer.output_enabled
            and item.visible
            and item.is_output_geometry
            and item.kind == ObjectKind.IMAGE
        )
    ]
    raster_sources = _preflight_raster_sources(raster_items)
    for item in document.objects:
        layer = layer_by_id.get(item.layer_id)
        if (
            layer is None
            or layer.mode != LayerMode.RASTER
            or not layer.visible
            or not layer.output_enabled
            or not item.visible
            or not item.is_output_geometry
        ):
            continue
        powered = layer.controller_power(controller_power_max) > 0
        overscan_commands = 2 if layer.overscan_percent > 0 else 0
        if item.kind == ObjectKind.IMAGE:
            row_count, column_count = _image_scan_budget(item, layer)
            aggregate_samples += row_count * column_count
            # Image tone is known only after bounded decode. Account for the
            # unavoidable row stream here; exact run commands are capped while
            # the rows are sampled.
            row_commands = 2 + overscan_commands + (3 if powered else 0)
        else:
            row_count, edge_count = _vector_scan_budget(
                item,
                layer,
                outlines=_prepared_object_polylines(item, prepared_object_paths),
            )
            edge_tests = row_count * edge_count
            aggregate_edge_tests += edge_tests
            worst_spans = row_count * max(1, edge_count // 2) * layer.passes
            aggregate_worst_spans += worst_spans
            row_commands = (
                2 + overscan_commands
                if not powered
                else 2 + overscan_commands + 4 * max(1, edge_count // 2)
            )
        aggregate_rows += row_count * layer.passes
        aggregate_commands += row_count * layer.passes * row_commands

    if aggregate_rows > _MAX_RASTER_ROWS:
        raise ValueError(
            f"Project raster output requires {aggregate_rows:,} aggregate row iterations, "
            f"exceeding the {_MAX_RASTER_ROWS:,}-row planner limit"
        )
    if aggregate_samples > _MAX_RASTER_SAMPLES:
        raise ValueError(
            f"Project raster output requires {aggregate_samples:,} aggregate samples, "
            f"exceeding the {_MAX_RASTER_SAMPLES:,}-sample planner limit"
        )
    if aggregate_edge_tests > _MAX_SCANLINE_EDGE_TESTS:
        raise ValueError(
            f"Project raster output requires {aggregate_edge_tests:,} aggregate scanline "
            f"edge tests, exceeding the {_MAX_SCANLINE_EDGE_TESTS:,}-test planner limit"
        )
    if aggregate_commands >= _MAX_STREAM_COMMANDS - _STREAM_COMMAND_RESERVE:
        detail = (
            f" and up to {aggregate_worst_spans:,} vector spans"
            if aggregate_worst_spans
            else ""
        )
        raise ValueError(
            f"Project raster geometry requires at least {aggregate_commands:,} streamed "
            f"commands{detail}, exceeding the {_MAX_STREAM_COMMANDS:,}-command limit; "
            "increase raster intervals or simplify the project"
        )
    return raster_sources


def _axis_sample_count(extent: float, pitch: float, label: str) -> int:
    if not math.isfinite(extent) or extent <= 0:
        raise ValueError(f"{label} raster extent must be positive and finite")
    if not math.isfinite(pitch) or pitch <= 0:
        raise ValueError("Raster line interval must be positive and finite")
    ratio = extent / pitch
    if not math.isfinite(ratio):
        raise ValueError(f"{label} raster sample count is not finite")
    return max(1, int(math.ceil(ratio - 1e-12)))


def _axis_sample_centers(
    minimum: float,
    maximum: float,
    pitch: float,
    count: int,
) -> np.ndarray:
    extent = maximum - minimum
    first = minimum + (extent - (count - 1) * pitch) / 2.0
    return first + np.arange(count, dtype=np.float64) * pitch


def _axis_sample_boundaries(
    minimum: float,
    maximum: float,
    centers: np.ndarray,
) -> np.ndarray:
    boundaries = np.empty(centers.size + 1, dtype=np.float64)
    boundaries[0] = minimum
    boundaries[-1] = maximum
    if centers.size > 1:
        boundaries[1:-1] = (centers[:-1] + centers[1:]) / 2.0
    return boundaries


def _scan_line_polygon_interval(
    polygon: np.ndarray,
    row_coordinate: float,
) -> tuple[float, float] | None:
    intersections: list[float] = []
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0), strict=True):
        start_row = float(start[1])
        end_row = float(end[1])
        if (start_row <= row_coordinate < end_row) or (
            end_row <= row_coordinate < start_row
        ):
            ratio = (row_coordinate - start_row) / (end_row - start_row)
            intersections.append(
                float(start[0]) + ratio * (float(end[0]) - float(start[0]))
            )
    if len(intersections) < 2:
        return None
    return min(intersections), max(intersections)


def _scan_points_to_machine(
    points: np.ndarray,
    scan_to_machine: np.ndarray,
) -> np.ndarray:
    return np.asarray(points, dtype=np.float64).reshape(-1, 2) @ scan_to_machine.T


def _scan_row_source_maps(
    scan_positions: np.ndarray,
    row_coordinate: float,
    item: SceneObject,
    scan_to_machine: np.ndarray,
    source_width: int,
    source_height: int,
) -> tuple[np.ndarray, np.ndarray]:
    scan_points = np.column_stack(
        [scan_positions, np.full(scan_positions.size, row_coordinate)]
    )
    source_points = _scan_points_source_coordinates(
        scan_points,
        item,
        scan_to_machine,
        source_width,
        source_height,
    )
    return (
        source_points[:, 0].astype(np.float32, copy=False).reshape(1, -1),
        source_points[:, 1].astype(np.float32, copy=False).reshape(1, -1),
    )


def _scan_points_source_coordinates(
    scan_points: np.ndarray,
    item: SceneObject,
    scan_to_machine: np.ndarray,
    source_width: int,
    source_height: int,
) -> np.ndarray:
    machine_points = _scan_points_to_machine(scan_points, scan_to_machine)
    delta = machine_points - np.array(
        [item.transform.x_mm, item.transform.y_mm],
        dtype=np.float64,
    )
    angle = math.radians(item.transform.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    local_x = delta[:, 0] * cosine + delta[:, 1] * sine
    local_y = -delta[:, 0] * sine + delta[:, 1] * cosine
    if item.transform.mirror_x:
        local_x *= -1.0
    if item.transform.mirror_y:
        local_y *= -1.0
    normalized_x = local_x / item.transform.width_mm
    normalized_y = local_y / item.transform.height_mm
    source_x = (normalized_x + 0.5) * source_width - 0.5
    # QImage draws its source top at negative scene Y, which is positive machine Y.
    source_y = (0.5 - normalized_y) * source_height - 0.5
    return np.column_stack([source_x, source_y])


def _area_prefilter_raster(
    image: np.ndarray,
    item: SceneObject,
    scan_to_machine: np.ndarray,
    pitch: float,
) -> np.ndarray:
    """Bound high-frequency source detail to the physical raster-cell footprint."""

    import cv2

    source_height, source_width = image.shape
    reference = _scan_points_source_coordinates(
        np.array([[0.0, 0.0], [pitch, 0.0], [0.0, pitch]], dtype=np.float64),
        item,
        scan_to_machine,
        source_width,
        source_height,
    )
    horizontal = reference[1] - reference[0]
    vertical = reference[2] - reference[0]
    footprint_width = abs(float(horizontal[0])) + abs(float(vertical[0]))
    footprint_height = abs(float(horizontal[1])) + abs(float(vertical[1]))
    filtered_width = min(
        source_width,
        max(1, int(math.ceil(source_width / max(1.0, footprint_width)))),
    )
    filtered_height = min(
        source_height,
        max(1, int(math.ceil(source_height / max(1.0, footprint_height)))),
    )
    if (filtered_width, filtered_height) == (source_width, source_height):
        return image
    return cv2.resize(
        image,
        (filtered_width, filtered_height),
        interpolation=cv2.INTER_AREA,
    )


def _ordered_dither_thresholds(width: int) -> np.ndarray:
    return (
        _BAYER_8X8[:, np.arange(width) % 8] + 0.5
    ) * (255.0 / 64.0)


def _ordered_dither_row(
    image: np.ndarray,
    row_index: int,
    thresholds: np.ndarray,
) -> np.ndarray:
    darkness = 255.0 - image.astype(np.float32)
    return darkness >= thresholds[row_index % 8]


def _enabled_runs(enabled: np.ndarray) -> list[tuple[int, int]]:
    padded = np.pad(enabled.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _image_raster_rows(
    item: SceneObject,
    layer: OperationLayer,
    *,
    powered: bool,
    command_budget: int,
    raster_sources: dict[str, RasterSource],
) -> tuple[list[RasterRow], RasterAssetIdentity]:
    import cv2

    asset = Path(str(item.geometry.get("asset", ""))).expanduser()
    machine_to_scan, scan_to_machine = _scan_matrices(layer.scan_angle_deg)
    image_corners = _transform_points(
        np.array(
            [
                [-0.5, 0.5],
                [0.5, 0.5],
                [0.5, -0.5],
                [-0.5, -0.5],
            ],
            dtype=np.float64,
        ),
        item,
    )
    scan_polygon = image_corners @ machine_to_scan.T
    scan_minimum = scan_polygon.min(axis=0)
    scan_maximum = scan_polygon.max(axis=0)
    scan_width = float(scan_maximum[0] - scan_minimum[0])
    scan_height = float(scan_maximum[1] - scan_minimum[1])
    column_count = _axis_sample_count(
        scan_width,
        layer.line_interval_mm,
        "Horizontal",
    )
    row_count = _axis_sample_count(
        scan_height,
        layer.line_interval_mm,
        "Vertical",
    )
    if row_count * layer.passes > _MAX_RASTER_ROWS:
        raise ValueError(
            f"Raster output for {item.name} requires {row_count * layer.passes:,} row iterations, "
            f"exceeding the {_MAX_RASTER_ROWS:,}-row planner limit; increase the line interval "
            "or reduce the image size"
        )
    sample_count = row_count * column_count
    if sample_count > _MAX_RASTER_SAMPLES:
        raise ValueError(
            f"Raster output for {item.name} requires {sample_count:,} samples, exceeding "
            f"the {_MAX_RASTER_SAMPLES:,}-sample planner limit; increase the line interval "
            "or reduce the image size"
        )
    source_path = _raster_source_path(item)
    try:
        source = raster_sources[source_path]
    except KeyError as exc:
        raise ValueError(f"Raster source was not included in project preflight: {asset}") from exc
    if source.image is None or source.identity is None:
        image, identity = decode_raster_grayscale(asset, metadata=source.metadata)
        image.setflags(write=False)
        source.image = image
        source.identity = identity
    image = source.image
    identity = source.identity
    image = _area_prefilter_raster(
        image,
        item,
        scan_to_machine,
        layer.line_interval_mm,
    )
    source_height, source_width = image.shape
    scan_positions = _axis_sample_centers(
        float(scan_minimum[0]),
        float(scan_maximum[0]),
        layer.line_interval_mm,
        column_count,
    )
    scan_boundaries = _axis_sample_boundaries(
        float(scan_minimum[0]),
        float(scan_maximum[0]),
        scan_positions,
    )
    row_positions = _axis_sample_centers(
        float(scan_minimum[1]),
        float(scan_maximum[1]),
        layer.line_interval_mm,
        row_count,
    )
    dither_thresholds = _ordered_dither_thresholds(column_count)

    rows: list[RasterRow] = []
    estimated_commands = 0
    for row_index, row_position in enumerate(row_positions):
        _raise_if_toolpath_cancelled()
        row_interval = _scan_line_polygon_interval(scan_polygon, float(row_position))
        if row_interval is None:
            continue
        map_x, map_y = _scan_row_source_maps(
            scan_positions,
            float(row_position),
            item,
            scan_to_machine,
            source_width,
            source_height,
        )
        sampled_row = cv2.remap(
            image,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )[0]
        runs = _enabled_runs(
            _ordered_dither_row(sampled_row, row_index, dither_thresholds)
        )
        if not runs:
            continue
        row_start, row_end = row_interval
        scan_spans = [
            np.array(
                [
                    [max(row_start, float(scan_boundaries[start_column])), row_position],
                    [min(row_end, float(scan_boundaries[end_column])), row_position],
                ],
                dtype=np.float64,
            )
            for start_column, end_column in runs
            if min(row_end, float(scan_boundaries[end_column]))
            - max(row_start, float(scan_boundaries[start_column]))
            > 1e-9
        ]
        if not scan_spans:
            continue
        row_scan = np.array(
            [[row_start, row_position], [row_end, row_position]],
            dtype=np.float64,
        )
        if row_index % 2:
            row_scan = row_scan[::-1].copy()
            scan_spans = [span[::-1].copy() for span in reversed(scan_spans)]
        row = RasterRow(
            points=_scan_points_to_machine(row_scan, scan_to_machine),
            spans=[
                Polyline(
                    _scan_points_to_machine(span, scan_to_machine),
                    closed=False,
                    source_tag=item.name,
                )
                for span in scan_spans
            ],
            source_tag=item.name,
        )
        rows.append(row)
        estimated_commands += _raster_row_command_count(
            row,
            layer.overscan_percent,
            powered=powered,
            power_correction=layer.raster_power_correction,
        )
        if estimated_commands * layer.passes >= command_budget:
            raise ValueError(
                f"Raster output for {item.name} exceeds the {_MAX_STREAM_COMMANDS:,}-command "
                "stream limit; increase the line interval, reduce the image size, or simplify the image"
            )
    if not rows:
        raise ValueError(f"Raster image contains no engravable pixels after dithering: {item.name}")
    return rows, identity


def _raster_rows(
    layer: OperationLayer,
    layer_objects: list[SceneObject],
    *,
    powered: bool,
    command_budget: int,
    raster_sources: dict[str, RasterSource],
    prepared_object_paths: dict[str, tuple[Polyline, ...]] | None = None,
) -> tuple[list[RasterRow], tuple[RasterAssetIdentity, ...], int]:
    rows: list[RasterRow] = []
    assets: list[RasterAssetIdentity] = []
    estimated_commands = 0
    for item in layer_objects:
        if item.kind == ObjectKind.IMAGE:
            item_rows, identity = _image_raster_rows(
                item,
                layer,
                powered=powered,
                command_budget=command_budget - estimated_commands,
                raster_sources=raster_sources,
            )
            assets.append(identity)
        else:
            item_rows = _scanline_rows(
                item,
                layer,
                outlines=_prepared_object_polylines(item, prepared_object_paths),
            )
        rows.extend(item_rows)
        estimated_commands += sum(
            _raster_row_command_count(
                row,
                layer.overscan_percent,
                powered=powered,
                power_correction=layer.raster_power_correction,
            )
            for row in item_rows
        ) * layer.passes
        if estimated_commands >= command_budget:
            raise ValueError(
                f"Aggregate raster output exceeds the {_MAX_STREAM_COMMANDS:,}-command "
                "stream limit; increase raster intervals or simplify the project"
            )
    return rows, tuple(assets), estimated_commands


def _operation_paths(
    document: ProjectDocument,
    layer: OperationLayer,
    layer_objects: list[SceneObject],
    *,
    scene_revision: SceneRevision | None = None,
    planning_cache: PlanningCache | None = None,
    prepared_object_paths: dict[str, tuple[Polyline, ...]] | None = None,
    normalized_geometry: NormalizedGeometryArtifact | None = None,
) -> tuple[list[Polyline], OperationArtifact | None]:
    if layer.mode == LayerMode.LINE:
        normalized = normalized_geometry or _normalized_layer_geometry(
            document,
            layer,
            scene_revision=scene_revision,
            planning_cache=planning_cache,
        )
        operation = _line_operation_artifact(document, layer, normalized)
        planned_layer = operation.layer_for_id(layer.id)
        if planned_layer is None:
            raise RuntimeError("LINE operation artifact lost its source layer")
        return planned_layer.paths, operation
    unsupported = [
        item.name
        for item in layer_objects
        if item.kind in {ObjectKind.TEXT, ObjectKind.LINE}
        or item.kind == ObjectKind.IMAGE
    ]
    if unsupported:
        raise ValueError(
            f"{layer.mode.value.title()} output is not implemented for: "
            + ", ".join(unsupported)
        )
    return (
        [
            path
            for item in layer_objects
            for path in _scanline_paths(
                item,
                layer,
                outlines=_prepared_object_polylines(item, prepared_object_paths),
            )
        ],
        None,
    )


def _points_differ(first: np.ndarray, second: np.ndarray) -> bool:
    return float(np.linalg.norm(first - second)) > 1e-9


def _points_have_motion(points: np.ndarray) -> bool:
    return len(points) >= 2 and any(
        (_fmt(float(start[0])), _fmt(float(start[1])))
        != (_fmt(float(end[0])), _fmt(float(end[1])))
        for start, end in zip(points[:-1], points[1:], strict=True)
    )


@dataclass(slots=True)
class _AirAssistEmitter:
    """Emit a resolved binary output without duplicating state transitions."""

    commands: AirAssistCommands | None
    active: bool = False

    def establish_off(self, lines: list[str]) -> None:
        if self.commands is not None:
            lines.extend(self.commands.program_lines(False))
        self.active = False

    def turn_on(self, lines: list[str]) -> None:
        if self.active:
            return
        if self.commands is None:
            raise SafetyError(
                "A powered layer requests air assist, but the machine has no "
                "resolved air-assist command mapping"
            )
        lines.extend(self.commands.program_lines(True))
        self.active = True

    def turn_off_before_layer(self, lines: list[str]) -> None:
        if not self.active:
            return
        if self.commands is None:  # pragma: no cover - guarded by turn_on
            raise SafetyError("Active air assist has no resolved command mapping")
        lines.append("M5")
        lines.extend(self.commands.program_lines(False))
        self.active = False


def _layer_has_powered_output(
    operation: LayerOperation,
    *,
    power_max: int,
) -> bool:
    if operation.layer.controller_power(power_max) <= 0:
        return False
    if operation.layer.mode == LayerMode.RASTER:
        return any(
            _points_have_motion(span.points)
            for row in operation.raster_rows
            for span in row.spans
        )
    return any(_points_have_motion(path.points) for path in operation.paths)


def _emit_raster_row(
    lines: list[str],
    row: RasterRow,
    layer: OperationLayer,
    laser: LaserSettings,
    power: int,
    power_max: int,
    current: np.ndarray,
    air_assist: _AirAssistEmitter,
) -> tuple[np.ndarray, float, float, int, int]:
    """Emit one scan row and return position, cut/travel, points, and paths."""

    motion = _raster_motion_points(row.points, layer.overscan_percent)
    lead_start = motion[0]
    row_start = row.points[0]
    row_end = row.points[-1]
    lead_end = motion[-1]
    route_travel = float(np.linalg.norm(lead_start - current))
    off_travel = route_travel
    cut = 0.0
    point_count = 0
    path_count = 0
    lead_in_mm = float(np.linalg.norm(row_start - lead_start))
    lead_out_mm = float(np.linalg.norm(lead_end - row_end))
    lines.append(e3_metadata_line("path", {"name": row.source_tag}))
    lines.append(
        f"G0 X{_fmt(lead_start[0])} Y{_fmt(lead_start[1])} "
        f"F{_fmt(laser.travel_feed_mm_min)}"
    )
    if _points_differ(lead_start, row_start):
        distance = float(np.linalg.norm(row_start - lead_start))
        off_travel += distance
        lines.append(
            f"G1 X{_fmt(row_start[0])} Y{_fmt(row_start[1])} "
            f"F{_fmt(layer.speed_mm_min)}"
        )

    position = row_start
    if power <= 0:
        lines.append(
            f"G1 X{_fmt(row_end[0])} Y{_fmt(row_end[1])} "
            f"F{_fmt(layer.speed_mm_min)}"
        )
        off_travel += float(np.linalg.norm(row_end - row_start))
        point_count = sum(len(span.points) for span in row.spans)
        path_count = len(row.spans)
        position = row_end
    else:
        for span in row.spans:
            if not _points_have_motion(span.points):
                continue
            span_start = span.points[0]
            span_end = span.points[-1]
            if _points_differ(position, span_start):
                gap = float(np.linalg.norm(span_start - position))
                off_travel += gap
                lines.append(
                    f"G1 X{_fmt(span_start[0])} Y{_fmt(span_start[1])} "
                    f"F{_fmt(layer.speed_mm_min)}"
                )
            if layer.air_assist:
                air_assist.turn_on(lines)
            lines.append(f"{laser.power_mode.upper()} S{power}")
            commanded_power = power
            motions = corrected_raster_span_motions(
                row_start,
                row_end,
                span_start,
                span_end,
                lead_in_mm=lead_in_mm,
                lead_out_mm=lead_out_mm,
                base_power=power,
                correction=layer.raster_power_correction,
                power_max=power_max,
                feed_mm_min=layer.speed_mm_min,
                acceleration_mm_s2=laser.preview_acceleration_mm_s2,
            )
            for motion in motions:
                power_word = ""
                if motion.power != commanded_power:
                    power_word = f" S{motion.power}"
                    commanded_power = motion.power
                lines.append(
                    f"G1 X{_fmt(motion.x)} Y{_fmt(motion.y)} "
                    f"F{_fmt(layer.speed_mm_min)}{power_word}"
                )
            lines.append("M5")
            distance = _length(span.points)
            cut += distance
            point_count += len(span.points)
            path_count += 1
            position = span_end
        if _points_differ(position, row_end):
            gap = float(np.linalg.norm(row_end - position))
            off_travel += gap
            lines.append(
                f"G1 X{_fmt(row_end[0])} Y{_fmt(row_end[1])} "
                f"F{_fmt(layer.speed_mm_min)}"
            )
            position = row_end

    if _points_differ(position, lead_end):
        distance = float(np.linalg.norm(lead_end - position))
        off_travel += distance
        lines.append(
            f"G1 X{_fmt(lead_end[0])} Y{_fmt(lead_end[1])} "
            f"F{_fmt(layer.speed_mm_min)}"
        )
    return lead_end.copy(), cut, off_travel, point_count, path_count


def _stream_command_count(lines: Iterable[str]) -> int:
    return sum(1 for line in lines if line.partition(";")[0].strip())


def _generate_project_gcode(
    document: ProjectDocument,
    laser: LaserSettings,
    *,
    power_max: int | None = None,
    optimize_order: bool = True,
    start_position: tuple[float, float] | None = None,
    coordinate_frame: HoneycombCoordinateFrame | None = None,
    machine_work_area: WorkArea | None = None,
    guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None,
    planning_cache: PlanningCache | None = None,
    air_assist_commands: AirAssistCommands | None = None,
) -> ProjectJob:
    """Generate one guarded vector program containing all enabled line layers."""
    _raise_if_toolpath_cancelled()
    if air_assist_commands is not None and not isinstance(
        air_assist_commands,
        AirAssistCommands,
    ):
        raise TypeError("air_assist_commands must be AirAssistCommands or None")
    document.validate()
    scene_revision = project_scene_revision(document)
    controller_power_max = int(power_max or laser.power_max)
    prepared_object_paths: dict[str, tuple[Polyline, ...]] = {}
    prepared_normalized_layers, deferred_normalized_cache_writes = (
        _preflight_normalized_point_budget(
            document,
            scene_revision,
            planning_cache=planning_cache,
            prepared_object_paths=prepared_object_paths,
        )
    )
    _raise_if_toolpath_cancelled()
    raster_sources = _preflight_raster_budget(
        document,
        controller_power_max,
        prepared_object_paths=prepared_object_paths,
    )
    _raise_if_toolpath_cancelled()
    local_work_area, execution_work_area, coordinate_frame_signature = (
        _coordinate_context(document, coordinate_frame, machine_work_area)
    )
    guarded_polygon = (
        None
        if guarded_output_polygon_mm is None
        else normalize_convex_polygon(
            guarded_output_polygon_mm,
            label="guarded output polygon",
        )
    )
    configured_polygon = (
        None
        if laser.guarded_output_polygon_mm is None
        else normalize_convex_polygon(
            laser.guarded_output_polygon_mm,
            label="laser.guarded_output_polygon_mm",
        )
    )
    if coordinate_frame is not None and guarded_polygon != configured_polygon:
        raise SafetyError(
            "Prepared output polygon does not match the configured laser authority"
        )
    if guarded_polygon is not None and coordinate_frame is None:
        raise SafetyError(
            "A guarded output polygon may be used only with a honeycomb-local project"
        )
    local_margin = 0.0 if coordinate_frame is not None else laser.boundary_margin_mm
    start = np.array(
        start_position or (execution_work_area.x_min, execution_work_area.y_min),
        dtype=np.float64,
    )
    current = start.copy()

    all_paths: list[Polyline] = []
    all_controller_paths: list[Polyline] = []
    layer_plans: list[LayerOperation] = []
    staged_line_artifact_ids: list[str] = []
    raster_command_estimate = 0
    vector_command_estimate = 0
    for layer in sorted(document.layers, key=lambda item: item.priority):
        _raise_if_toolpath_cancelled()
        if not layer.visible or not layer.output_enabled:
            continue
        layer_objects = [
            item
            for item in document.objects
            if (
                item.layer_id == layer.id
                and item.visible
                and item.is_output_geometry
            )
        ]
        if not layer_objects:
            continue
        local_native_paths = tuple(
            geometry
            for item in layer_objects
            if (geometry := _project_native_path(item)) is not None
        )
        _validate_native_curves_in_work_area(
            local_native_paths,
            local_work_area,
            local_margin,
            coordinate_label="local design",
        )
        placed_native_paths = _place_native_paths(local_native_paths, coordinate_frame)
        if guarded_polygon is None:
            _validate_native_curves_in_work_area(
                placed_native_paths,
                execution_work_area,
                laser.boundary_margin_mm,
                coordinate_label="placed design",
            )
        else:
            _validate_native_curves_in_guarded_polygon(
                placed_native_paths,
                guarded_polygon,
                coordinate_label="placed design",
            )
        controller_native_paths = _controller_native_paths(placed_native_paths, laser)
        if guarded_polygon is None:
            _validate_native_curves_in_work_area(
                controller_native_paths,
                execution_work_area,
                laser.boundary_margin_mm,
                coordinate_label="controller path after laser spot correction",
            )
        else:
            _validate_native_curves_in_guarded_polygon(
                controller_native_paths,
                guarded_polygon,
                coordinate_label="controller path after laser spot correction",
            )
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
        if layer.mode == LayerMode.RASTER:
            unsupported_raster = [
                item.name
                for item in layer_objects
                if item.kind in {ObjectKind.TEXT, ObjectKind.LINE}
            ]
            if unsupported_raster:
                raise ValueError(
                    "Raster output is not implemented for: "
                    + ", ".join(unsupported_raster)
                )
            design_rows, raster_assets, layer_raster_commands = _raster_rows(
                layer,
                layer_objects,
                powered=layer.controller_power(controller_power_max) > 0,
                command_budget=(
                    _MAX_STREAM_COMMANDS
                    - _STREAM_COMMAND_RESERVE
                    - raster_command_estimate
                    - vector_command_estimate
                ),
                raster_sources=raster_sources,
                prepared_object_paths=prepared_object_paths,
            )
            raster_command_estimate += layer_raster_commands
            local_design_motion_paths = _raster_motion_paths(
                design_rows,
                layer.overscan_percent,
            )
            validate_paths(
                local_design_motion_paths,
                local_work_area,
                local_margin,
                coordinate_label="local raster overscan path",
            )
            placed_rows = _place_raster_rows(design_rows, coordinate_frame)
            design_paths = [span for row in placed_rows for span in row.spans]
            design_motion_paths = _raster_motion_paths(
                placed_rows,
                layer.overscan_percent,
            )
            if guarded_polygon is None:
                validate_paths(
                    design_motion_paths,
                    execution_work_area,
                    laser.boundary_margin_mm,
                    coordinate_label="placed raster overscan path",
                )
            else:
                _validate_paths_in_guarded_polygon(
                    design_motion_paths,
                    guarded_polygon,
                    coordinate_label="placed raster overscan path",
                )
            controller_rows = _controller_raster_rows(placed_rows, laser)
            controller_motion_paths = _raster_motion_paths(
                controller_rows,
                layer.overscan_percent,
            )
            if guarded_polygon is None:
                validate_paths(
                    controller_motion_paths,
                    execution_work_area,
                    laser.boundary_margin_mm,
                    coordinate_label="raster overscan path after laser spot correction",
                )
            else:
                _validate_paths_in_guarded_polygon(
                    controller_motion_paths,
                    guarded_polygon,
                    coordinate_label="raster overscan path after laser spot correction",
                )
            layer_plans.append(
                LayerOperation(
                    layer=layer,
                    raster_rows=controller_rows,
                    dithered_image=any(
                        item.kind == ObjectKind.IMAGE for item in layer_objects
                    ),
                    raster_assets=raster_assets,
                )
            )
            all_paths.extend(design_paths)
            all_controller_paths.extend(controller_motion_paths)
            continue

        normalized_geometry = prepared_normalized_layers.get(layer.id)
        if normalized_geometry is not None and planning_cache is not None:
            dependency_digest = normalized_geometry.metadata.dependency_digest
            if dependency_digest is None:
                raise RuntimeError(
                    "Normalized LINE artifact is missing its dependency digest"
                )
            deferred = deferred_normalized_cache_writes.pop(
                dependency_digest,
                None,
            )
            if deferred is not None:
                planning_cache.put_normalized(
                    dependency_digest,
                    deferred[0],
                    deferred[1],
                )
        local_design_paths, operation_artifact = _operation_paths(
            document,
            layer,
            layer_objects,
            scene_revision=scene_revision,
            planning_cache=planning_cache,
            prepared_object_paths=prepared_object_paths,
            normalized_geometry=normalized_geometry,
        )
        if not local_design_paths:
            continue
        validate_paths(
            local_design_paths,
            local_work_area,
            local_margin,
            coordinate_label="local design",
        )
        placed_artifact: PlacedGeometryArtifact | None = None
        if operation_artifact is None:
            design_paths = _place_paths(local_design_paths, coordinate_frame)
        else:
            placed_artifact = _placed_line_geometry_artifact(
                document,
                layer,
                operation_artifact,
                coordinate_frame,
                coordinate_frame_signature,
                planning_cache=planning_cache,
            )
            design_paths = list(placed_artifact.paths_for_layer(layer.id))
        if guarded_polygon is None:
            validate_paths(
                design_paths,
                execution_work_area,
                laser.boundary_margin_mm,
                coordinate_label="placed design",
            )
        else:
            _validate_paths_in_guarded_polygon(
                design_paths,
                guarded_polygon,
                coordinate_label="placed design",
            )
        if placed_artifact is None:
            paths = _controller_paths(design_paths, laser)
        else:
            controller_artifact = _controller_line_geometry_artifact(
                document,
                layer,
                placed_artifact,
                laser,
                planning_cache=planning_cache,
            )
            staged_line_artifact_ids.append(controller_artifact.metadata.artifact_id)
            paths = list(controller_artifact.paths_for_layer(layer.id))
        if guarded_polygon is None:
            validate_paths(
                paths,
                execution_work_area,
                laser.boundary_margin_mm,
                coordinate_label="controller path after laser spot correction",
            )
        else:
            _validate_paths_in_guarded_polygon(
                paths,
                guarded_polygon,
                coordinate_label="controller path after laser spot correction",
            )
        layer_plans.append(LayerOperation(layer=layer, paths=paths))
        powered = layer.controller_power(controller_power_max) > 0
        per_path_overhead = 2 if powered else 1
        vector_command_estimate += layer.passes * sum(
            (
                len(
                    corrected_vector_motions(
                        path.points,
                        base_power=layer.controller_power(controller_power_max),
                        correction=layer.vector_power_correction,
                        power_max=controller_power_max,
                        feed_mm_min=layer.speed_mm_min,
                        acceleration_mm_s2=laser.preview_acceleration_mm_s2,
                    )
                )
                + 1
                if powered and layer.vector_power_correction != 0
                else len(path.points)
            )
            + per_path_overhead
            for path in paths
            if len(path.points) >= 2
        )
        if (
            4 + vector_command_estimate + raster_command_estimate
            > _MAX_STREAM_COMMANDS
        ):
            raise ValueError(
                "Aggregate vector and raster output requires more than "
                f"{_MAX_STREAM_COMMANDS:,} streamed commands; simplify project geometry "
                "or increase raster intervals"
            )
        all_paths.extend(design_paths)
        all_controller_paths.extend(paths)

    if not all_paths:
        raise ValueError("The project contains no enabled output paths")

    if air_assist_commands is None and any(
        layer_plan.layer.air_assist
        and _layer_has_powered_output(
            layer_plan,
            power_max=controller_power_max,
        )
        for layer_plan in layer_plans
    ):
        raise SafetyError(
            "A powered layer requests air assist, but the machine has no resolved "
            "air-assist command mapping"
        )

    bounds = _bounds(all_paths)
    controller_bounds = _bounds(all_controller_paths)
    has_raster = any(plan.raster_rows for plan in layer_plans)
    vector_path_count = sum(len(plan.paths) for plan in layer_plans)
    nearest_enabled = optimize_order and vector_path_count <= _MAX_NEAREST_ORDER_PATHS
    planner_mode = (
        "nearest path + fixed raster rows"
        if nearest_enabled and has_raster
        else "nearest path"
        if nearest_enabled
        else "source order + fixed raster rows (nearest path limit)"
        if optimize_order and has_raster
        else "source order (nearest path limit)"
        if optimize_order
        else "source order"
    )
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
    air_assist = _AirAssistEmitter(air_assist_commands)
    air_assist.establish_off(lines)
    lines.insert(
        4,
        e3_metadata_line(
            "job",
            {
                "planner": planner_mode,
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

    for layer_plan in layer_plans:
        _raise_if_toolpath_cancelled()
        layer = layer_plan.layer
        paths = layer_plan.paths
        power = layer.controller_power(controller_power_max)
        layer_has_powered_output = _layer_has_powered_output(
            layer_plan,
            power_max=controller_power_max,
        )
        if air_assist.active and not (
            layer_has_powered_output and layer.air_assist
        ):
            air_assist.turn_off_before_layer(lines)
        layer_cut = 0.0
        layer_travel = 0.0
        layer_path_count = 0
        layer_comment = (
            f"; Layer {layer.name.replace(';', ',')[:80]} · "
            f"{layer.speed_mm_min:g} mm/min · {layer.power_percent:g}% · "
            f"{layer.passes} pass(es) · vector correction "
            f"{layer.vector_power_correction:+g} · raster correction "
            f"{layer.raster_power_correction:+g}"
        )
        if layer.air_assist:
            layer_comment += " · Air assist: On"
        lines.append(layer_comment)
        if layer_plan.dithered_image:
            lines.append("; Raster tone: deterministic 8x8 ordered grayscale dither")
        if layer.mode == LayerMode.RASTER:
            lines.append("; Raster rows remain serpentine; overscan and white gaps are laser-off")
        if power <= 0:
            lines.append("; Layer power is zero; laser remains off")
        lines.append(
            e3_metadata_line(
                "layer",
                {
                    "id": layer.id,
                    "name": layer.name,
                    "color": layer.color,
                    "power_percent": layer.power_percent,
                    "vector_power_correction": layer.vector_power_correction,
                    "raster_power_correction": layer.raster_power_correction,
                    "mode": layer.mode.value,
                    **({"air_assist": True} if layer.air_assist else {}),
                    "raster_tone": (
                        "ordered-dither-8x8"
                        if layer_plan.dithered_image
                        else ""
                    ),
                },
            )
        )
        nested_order: list[Polyline] | None = None
        nested_flags: list[bool] | None = None
        if layer.mode == LayerMode.LINE and any(_containment_plan(paths)[1]):
            nested_order = (
                _containment_aware_nearest_order(paths, current)
                if nearest_enabled
                else _containment_aware_source_order(paths)
            )
            nested_flags = _containment_plan(nested_order)[1]
        for pass_index in range(layer.passes):
            _raise_if_toolpath_cancelled()
            if layer.mode == LayerMode.RASTER:
                lines.append(f"; Pass {pass_index + 1}/{layer.passes}")
                lines.append(
                    e3_metadata_line(
                        "pass",
                        {"index": pass_index + 1, "count": layer.passes},
                    )
                )
                rows = layer_plan.raster_rows
                if pass_index % 2:
                    rows = [
                        _reverse_raster_row(row)
                        for row in reversed(layer_plan.raster_rows)
                    ]
                comparison_position = current.copy()
                for row in rows:
                    _raise_if_toolpath_cancelled()
                    source_motion = _raster_motion_points(
                        row.points,
                        layer.overscan_percent,
                    )
                    source_order_travel += float(
                        np.linalg.norm(source_motion[0] - comparison_position)
                    )
                    comparison_position = source_motion[-1]
                for row in rows:
                    _raise_if_toolpath_cancelled()
                    row_motion = _raster_motion_points(
                        row.points,
                        layer.overscan_percent,
                    )
                    planned_order_travel += float(
                        np.linalg.norm(row_motion[0] - current)
                    )
                    (
                        current,
                        row_cut,
                        row_travel,
                        row_points,
                        row_paths,
                    ) = _emit_raster_row(
                        lines,
                        row,
                        layer,
                        laser,
                        power,
                        controller_power_max,
                        current,
                        air_assist,
                    )
                    layer_cut += row_cut
                    cut_length += row_cut
                    layer_travel += row_travel
                    travel_length += row_travel
                    point_count += row_points
                    path_count += row_paths
                    layer_path_count += row_paths
                continue

            comparison_position = current.copy()
            for source_path in paths:
                _raise_if_toolpath_cancelled()
                source_motion = source_path.points
                source_order_travel += float(
                    np.linalg.norm(source_motion[0] - comparison_position)
                )
                comparison_position = source_motion[-1]
            if nested_order is not None:
                ordered = nested_order
                assert nested_flags is not None
                nested = nested_flags
            else:
                ordered = _nearest_order(paths, current) if nearest_enabled else paths
                nested = [False] * len(ordered)
            for path, is_nested in zip(ordered, nested, strict=True):
                _raise_if_toolpath_cancelled()
                if is_nested and pass_index > 0:
                    continue
                path_passes = range(layer.passes) if is_nested else (pass_index,)
                for path_pass_index in path_passes:
                    _raise_if_toolpath_cancelled()
                    points = path.points
                    if not _points_have_motion(points):
                        continue
                    lines.append(
                        f"; Pass {path_pass_index + 1}/{layer.passes}"
                    )
                    lines.append(
                        e3_metadata_line(
                            "pass",
                            {
                                "index": path_pass_index + 1,
                                "count": layer.passes,
                            },
                        )
                    )
                    lines.append(
                        e3_metadata_line("path", {"name": path.source_tag})
                    )
                    travel_target = points[0]
                    travel = float(np.linalg.norm(travel_target - current))
                    planned_order_travel += travel
                    layer_travel += travel
                    travel_length += travel
                    lines.append(
                        f"G0 X{_fmt(travel_target[0])} Y{_fmt(travel_target[1])} "
                        f"F{_fmt(laser.travel_feed_mm_min)}"
                    )
                    if power > 0:
                        if layer.air_assist:
                            air_assist.turn_on(lines)
                        lines.append(f"{laser.power_mode.upper()} S{power}")
                    motions = (
                        corrected_vector_motions(
                            points,
                            base_power=power,
                            correction=layer.vector_power_correction,
                            power_max=controller_power_max,
                            feed_mm_min=layer.speed_mm_min,
                            acceleration_mm_s2=laser.preview_acceleration_mm_s2,
                        )
                        if power > 0
                        else []
                    )
                    _raise_if_toolpath_cancelled()
                    commanded_power = power
                    for point in points[1:]:
                        _raise_if_toolpath_cancelled()
                        if power > 0:
                            continue
                        lines.append(
                            f"G1 X{_fmt(point[0])} Y{_fmt(point[1])} "
                            f"F{_fmt(layer.speed_mm_min)}"
                        )
                    for motion in motions:
                        _raise_if_toolpath_cancelled()
                        power_word = ""
                        if motion.power != commanded_power:
                            power_word = f" S{motion.power}"
                            commanded_power = motion.power
                        lines.append(
                            f"G1 X{_fmt(motion.x)} Y{_fmt(motion.y)} "
                            f"F{_fmt(layer.speed_mm_min)}{power_word}"
                        )
                    lines.append("M5")
                    distance = _length(points)
                    if power > 0:
                        layer_cut += distance
                        cut_length += distance
                    else:
                        layer_travel += distance
                        travel_length += distance
                    point_count += len(points)
                    path_count += 1
                    layer_path_count += 1
                    current = points[-1]
        summaries.append(
            {
                "layer_id": layer.id,
                "name": layer.name,
                "path_count": layer_path_count,
                "cut_length_mm": layer_cut,
                "travel_length_mm": layer_travel,
                "power": power,
                "speed_mm_min": layer.speed_mm_min,
                "passes": layer.passes,
                "vector_power_correction": layer.vector_power_correction,
                "raster_power_correction": layer.raster_power_correction,
                **(
                    {
                        "air_assist": True,
                        "air_assist_label": "Air assist: On",
                    }
                    if layer.air_assist
                    else {}
                ),
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
        ]
    )
    if air_assist_commands is not None:
        air_assist.establish_off(lines)
        lines.append("M5")
    lines.extend(["; End of E3 project job", ""])
    command_count = _stream_command_count(lines)
    if command_count > _MAX_STREAM_COMMANDS:
        raise ValueError(
            f"Generated output contains {command_count:,} streamed commands, exceeding "
            f"the {_MAX_STREAM_COMMANDS:,}-command limit; increase raster line intervals "
            "or simplify project geometry"
        )
    text = "\n".join(lines)
    encoded = _encoded_program_artifact(
        document,
        text,
        scene_revision=scene_revision,
        bounds_mm=bounds,
        command_count=command_count,
        path_count=path_count,
        point_count=point_count,
        staged_line_artifact_ids=staged_line_artifact_ids,
        unstaged_layer_count=sum(
            1 for layer_plan in layer_plans if layer_plan.layer.mode != LayerMode.LINE
        ),
    )
    try:
        plan = build_job_plan(
            encoded.text,
            power_max=controller_power_max,
            default_feed_mm_min=laser.travel_feed_mm_min,
            start_position=(float(start[0]), float(start[1])),
            acceleration_mm_s2=laser.preview_acceleration_mm_s2,
            command_delay_ms=laser.preview_command_delay_ms,
            air_assist_commands=air_assist_commands,
            cancel_check=_toolpath_cancel_requested,
        )
    except JobPlanCancelled as exc:
        raise ToolpathGenerationCancelled("Toolpath generation was cancelled") from exc
    _raise_if_toolpath_cancelled()
    return ProjectJob(
        text=encoded.text,
        bounds_mm=bounds,
        cut_length_mm=cut_length,
        travel_length_mm=travel_length,
        estimated_seconds=plan.total_seconds,
        path_count=path_count,
        point_count=point_count,
        layer_summaries=summaries,
        plan=plan,
        spot_offset_mm=(
            float(laser.spot_offset_x_mm),
            float(laser.spot_offset_y_mm),
        ),
        air_assist_commands=air_assist_commands,
        raster_assets=tuple(
            identity
            for layer_plan in layer_plans
            for identity in layer_plan.raster_assets
        ),
        coordinate_space=document.coordinate_space,
        coordinate_frame_signature=coordinate_frame_signature,
        guarded_output_polygon_mm=guarded_polygon,
    )


def generate_project_gcode(
    document: ProjectDocument,
    laser: LaserSettings,
    *,
    power_max: int | None = None,
    optimize_order: bool = True,
    start_position: tuple[float, float] | None = None,
    coordinate_frame: HoneycombCoordinateFrame | None = None,
    machine_work_area: WorkArea | None = None,
    guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None,
    planning_cache: PlanningCache | None = None,
    air_assist_commands: AirAssistCommands | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> ProjectJob:
    """Generate one guarded project program with optional cooperative cancellation."""

    if cancel_check is not None and not callable(cancel_check):
        raise TypeError("cancel_check must be callable or None")
    token = _TOOLPATH_CANCEL_CHECK.set(cancel_check)
    try:
        return _generate_project_gcode(
            document,
            laser,
            power_max=power_max,
            optimize_order=optimize_order,
            start_position=start_position,
            coordinate_frame=coordinate_frame,
            machine_work_area=machine_work_area,
            guarded_output_polygon_mm=guarded_output_polygon_mm,
            planning_cache=planning_cache,
            air_assist_commands=air_assist_commands,
        )
    finally:
        _TOOLPATH_CANCEL_CHECK.reset(token)


def generate_project_frame(
    document: ProjectDocument,
    laser: LaserSettings,
    *,
    start_position: tuple[float, float] | None = None,
    coordinate_frame: HoneycombCoordinateFrame | None = None,
    machine_work_area: WorkArea | None = None,
    guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None,
) -> ProjectJob:
    document.validate()
    local_work_area, execution_work_area, coordinate_frame_signature = (
        _coordinate_context(document, coordinate_frame, machine_work_area)
    )
    guarded_polygon = (
        None
        if guarded_output_polygon_mm is None
        else normalize_convex_polygon(
            guarded_output_polygon_mm,
            label="guarded output polygon",
        )
    )
    configured_polygon = (
        None
        if laser.guarded_output_polygon_mm is None
        else normalize_convex_polygon(
            laser.guarded_output_polygon_mm,
            label="laser.guarded_output_polygon_mm",
        )
    )
    if coordinate_frame is not None and guarded_polygon != configured_polygon:
        raise SafetyError(
            "Prepared output polygon does not match the configured laser authority"
        )
    if guarded_polygon is not None and coordinate_frame is None:
        raise SafetyError(
            "A guarded output polygon may be used only with a honeycomb-local project"
        )
    objects = document.visible_output_objects()
    raster_sources = _preflight_raster_sources(objects)
    for source in raster_sources.values():
        source.identity = read_raster_asset_payload(
            source.metadata.path,
            metadata=source.metadata,
        ).identity
    paths: list[Polyline] = []
    native_paths: list[NativePathGeometry] = []
    point_budget = _NormalizedPointBudget(_NATIVE_PATH_FLATTEN_MAX_POINTS)
    for item in objects:
        if item.kind == ObjectKind.IMAGE:
            corners = _transform_points(
                np.array(
                    [
                        [-0.5, -0.5],
                        [0.5, -0.5],
                        [0.5, 0.5],
                        [-0.5, 0.5],
                        [-0.5, -0.5],
                    ],
                    dtype=np.float64,
                ),
                item,
            )
            paths.append(
                Polyline(corners, closed=True, source_tag=item.name)
            )
        else:
            native_path = _project_native_path(item)
            if native_path is not None:
                native_paths.append(native_path)
            paths.extend(_budgeted_object_polylines(item, point_budget))
    if not paths:
        raise ValueError("The project contains no visible output geometry")
    local_bounds = _bounds(paths)
    for geometry in native_paths:
        for curve_bounds in _iter_native_cubic_bounds(geometry):
            local_bounds = (
                min(
                    local_bounds[0],
                    curve_bounds[0] - NATIVE_PATH_FLATTEN_TOLERANCE_MM,
                ),
                min(
                    local_bounds[1],
                    curve_bounds[1] - NATIVE_PATH_FLATTEN_TOLERANCE_MM,
                ),
                max(
                    local_bounds[2],
                    curve_bounds[2] + NATIVE_PATH_FLATTEN_TOLERANCE_MM,
                ),
                max(
                    local_bounds[3],
                    curve_bounds[3] + NATIVE_PATH_FLATTEN_TOLERANCE_MM,
                ),
            )
    local_rectangle = Polyline(
        np.asarray(
            [
                [local_bounds[0], local_bounds[1]],
                [local_bounds[2], local_bounds[1]],
                [local_bounds[2], local_bounds[3]],
                [local_bounds[0], local_bounds[3]],
                [local_bounds[0], local_bounds[1]],
            ],
            dtype=np.float64,
        ),
        closed=True,
        source_tag="frame",
    )
    validate_paths(
        [local_rectangle],
        local_work_area,
        0.0 if coordinate_frame is not None else laser.boundary_margin_mm,
        coordinate_label="local frame path",
    )
    placed_rectangle = _place_paths([local_rectangle], coordinate_frame)[0]
    if guarded_polygon is not None:
        _validate_paths_in_guarded_polygon(
            [placed_rectangle],
            guarded_polygon,
            coordinate_label="placed frame path",
        )
        _validate_paths_in_guarded_polygon(
            _controller_paths([placed_rectangle], laser),
            guarded_polygon,
            coordinate_label="frame path after laser spot correction",
        )
    bounds = _bounds([placed_rectangle])
    options = ToolpathOptions(
        power_mode=laser.power_mode,
        power=0,
        power_max=laser.power_max,
        travel_feed_mm_min=laser.travel_feed_mm_min,
        engrave_feed_mm_min=laser.travel_feed_mm_min,
        boundary_margin_mm=(
            0.0 if guarded_polygon is not None else laser.boundary_margin_mm
        ),
        spot_offset_x_mm=laser.spot_offset_x_mm,
        spot_offset_y_mm=laser.spot_offset_y_mm,
        optimize_order=False,
        start_x_mm=(
            start_position
            or (execution_work_area.x_min, execution_work_area.y_min)
        )[0],
        start_y_mm=(
            start_position
            or (execution_work_area.x_min, execution_work_area.y_min)
        )[1],
    )
    frame_work_area = execution_work_area
    if guarded_polygon is not None:
        planned_start = start_position or (
            execution_work_area.x_min,
            execution_work_area.y_min,
        )
        frame_work_area = WorkArea(
            min(*(point[0] for point in guarded_polygon), planned_start[0]),
            max(*(point[0] for point in guarded_polygon), planned_start[0]),
            min(*(point[1] for point in guarded_polygon), planned_start[1]),
            max(*(point[1] for point in guarded_polygon), planned_start[1]),
        )
    program = (
        generate_frame_gcode(
            bounds,
            options,
            execution_work_area,
            laser_enabled=False,
        )
        if coordinate_frame is None
        else generate_frame_path_gcode(
            placed_rectangle,
            options,
            frame_work_area,
            laser_enabled=False,
        )
    )
    plan = build_job_plan(
        program.text,
        power_max=laser.power_max,
        default_feed_mm_min=laser.travel_feed_mm_min,
        start_position=start_position
        or (execution_work_area.x_min, execution_work_area.y_min),
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
        spot_offset_mm=(
            float(laser.spot_offset_x_mm),
            float(laser.spot_offset_y_mm),
        ),
        raster_assets=tuple(
            source.identity
            for source in raster_sources.values()
            if source.identity is not None
        ),
        coordinate_space=document.coordinate_space,
        coordinate_frame_signature=coordinate_frame_signature,
        guarded_output_polygon_mm=guarded_polygon,
    )


def verify_project_job_assets(job: ProjectJob) -> None:
    """Reject a prepared job if any external raster changed or disappeared."""

    verify_raster_asset_identities(job.raster_assets)
