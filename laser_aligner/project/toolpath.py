from __future__ import annotations

import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ..config import LaserSettings, WorkArea
from ..errors import SafetyError
from ..gcode.generator import (
    ToolpathOptions,
    generate_frame_gcode,
    generate_frame_path_gcode,
    validate_paths,
)
from ..gcode.job_plan import JobPlan, build_job_plan, e3_metadata_line
from ..geometry.polygon import (
    convex_polygon_violation_normalized_mm,
    normalize_convex_polygon,
)
from ..geometry.svg import Polyline
from .model import (
    CoordinateSpace,
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    SceneObject,
)
from .power_correction import (
    DEFAULT_RAMP_STEPS,
    corrected_raster_span_motions,
    corrected_vector_motions,
)
from .raster_asset import (
    RasterAssetIdentity,
    RasterAssetMetadata,
    decode_raster_grayscale,
    probe_raster_asset,
    read_raster_asset_payload,
    verify_raster_asset_identities,
)

if TYPE_CHECKING:
    from ..calibration.support import HoneycombCoordinateFrame


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
    raster_assets: tuple[RasterAssetIdentity, ...] = ()
    coordinate_space: CoordinateSpace = CoordinateSpace.MACHINE
    coordinate_frame_signature: tuple[str, int, str] | None = None
    execution_signature: tuple[Any, ...] | None = None
    guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None


@dataclass(slots=True)
class _RasterRow:
    """One constant-velocity scan row with zero or more powered spans."""

    points: np.ndarray
    spans: list[Polyline]
    source_tag: str


@dataclass(slots=True)
class _RasterSource:
    """One bounded source shared by every image object in a generation."""

    metadata: RasterAssetMetadata
    image: np.ndarray | None = None
    identity: RasterAssetIdentity | None = None


@dataclass(slots=True)
class _LayerPlan:
    layer: OperationLayer
    paths: list[Polyline] = field(default_factory=list)
    raster_rows: list[_RasterRow] = field(default_factory=list)
    dithered_image: bool = False
    raster_assets: tuple[RasterAssetIdentity, ...] = ()


_MAX_STREAM_COMMANDS = 250_000
_MAX_RASTER_SAMPLES = 16_000_000
_MAX_RASTER_ROWS = 60_000
_MAX_SCANLINE_EDGE_TESTS = 16_000_000
_STREAM_COMMAND_RESERVE = 64
_MAX_NEAREST_ORDER_PATHS = 512
_MAX_UNIQUE_RASTER_ASSETS = 64
_MAX_UNIQUE_RASTER_ENCODED_BYTES = 64 * 1024 * 1024
_MAX_UNIQUE_RASTER_DECODED_BYTES = 64 * 1024 * 1024

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


def _point_in_closed_path(point: np.ndarray, polygon: np.ndarray) -> bool:
    """Even/odd containment independent of SVG winding direction."""
    vertices = polygon[:-1] if np.linalg.norm(polygon[0] - polygon[-1]) <= 1e-9 else polygon
    inside = False
    previous = vertices[-1]
    for current in vertices:
        cross = (current[1] > point[1]) != (previous[1] > point[1])
        if cross:
            x = (previous[0] - current[0]) * (point[1] - current[1]) / (previous[1] - current[1]) + current[0]
            if point[0] < x:
                inside = not inside
        previous = current
    return inside


def _containment_depths(paths: list[Polyline]) -> list[int]:
    """Return closed-contour nesting depths; open paths remain depth zero."""
    depths = [0] * len(paths)
    bounds = []
    for path in paths:
        minimum = path.points.min(axis=0)
        maximum = path.points.max(axis=0)
        bounds.append((minimum, maximum))
    for index, inner in enumerate(paths):
        if not inner.closed or len(inner.points) < 4:
            continue
        probe = inner.points[0]
        for other_index, outer in enumerate(paths):
            if other_index == index or not outer.closed or len(outer.points) < 4:
                continue
            inner_min, inner_max = bounds[index]
            outer_min, outer_max = bounds[other_index]
            if (np.all(outer_min <= inner_min + 1e-9) and np.all(outer_max >= inner_max - 1e-9)
                    and np.any(outer_max - outer_min > inner_max - inner_min + 1e-9)
                    and _point_in_closed_path(probe, outer.points)):
                depths[index] += 1
    return depths


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
    rows: Iterable[_RasterRow],
    coordinate_frame: HoneycombCoordinateFrame | None,
) -> list[_RasterRow]:
    return [
        _RasterRow(
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


def _reverse_raster_row(row: _RasterRow) -> _RasterRow:
    return _RasterRow(
        points=row.points[::-1].copy(),
        spans=[
            Polyline(span.points[::-1].copy(), closed=False, source_tag=span.source_tag)
            for span in reversed(row.spans)
        ],
        source_tag=row.source_tag,
    )


def _controller_raster_rows(
    rows: list[_RasterRow],
    laser: LaserSettings,
) -> list[_RasterRow]:
    offset = np.array(
        [laser.spot_offset_x_mm, laser.spot_offset_y_mm],
        dtype=np.float64,
    )
    if not np.isfinite(offset).all():
        raise ValueError("laser spot offsets must be finite")
    return [
        _RasterRow(
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
    rows: list[_RasterRow],
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
    row: _RasterRow,
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


def _layer_paths(document: ProjectDocument, layer: OperationLayer) -> list[Polyline]:
    paths: list[Polyline] = []
    for item in document.objects:
        if (
            item.layer_id == layer.id
            and item.visible
            and item.is_output_geometry
        ):
            paths.extend(object_polylines(item))
    return paths


def _scanline_rows(item: SceneObject, layer: OperationLayer) -> list[_RasterRow]:
    outlines = object_polylines(item)
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
    rows: list[_RasterRow] = []
    for row in range(row_count):
        y = first_y + row * interval
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
        scan_spans: list[np.ndarray] = []
        for index in range(0, len(intersections) - 1, 2):
            start_x, end_x = intersections[index : index + 2]
            if end_x - start_x <= 1e-9:
                continue
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
                _RasterRow(
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


def _scanline_paths(item: SceneObject, layer: OperationLayer) -> list[Polyline]:
    return [span for row in _scanline_rows(item, layer) for span in row.spans]


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
) -> tuple[int, int]:
    outlines = object_polylines(item)
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
) -> dict[str, _RasterSource]:
    """Probe each unique source once and bound aggregate decode work."""

    raster_sources: dict[str, _RasterSource] = {}
    aggregate_encoded_bytes = 0
    aggregate_decoded_bytes = 0
    for item in items:
        if item.kind != ObjectKind.IMAGE:
            continue
        source_path = _raster_source_path(item)
        if source_path in raster_sources:
            continue
        metadata = probe_raster_asset(source_path)
        raster_sources[metadata.path] = _RasterSource(metadata=metadata)
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
) -> dict[str, _RasterSource]:
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
            row_count, edge_count = _vector_scan_budget(item, layer)
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
    raster_sources: dict[str, _RasterSource],
) -> tuple[list[_RasterRow], RasterAssetIdentity]:
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

    rows: list[_RasterRow] = []
    estimated_commands = 0
    for row_index, row_position in enumerate(row_positions):
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
        row = _RasterRow(
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
    raster_sources: dict[str, _RasterSource],
) -> tuple[list[_RasterRow], tuple[RasterAssetIdentity, ...], int]:
    rows: list[_RasterRow] = []
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
            item_rows = _scanline_rows(item, layer)
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
) -> list[Polyline]:
    if layer.mode == LayerMode.LINE:
        return _layer_paths(document, layer)
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
    return [
        path
        for item in layer_objects
        for path in _scanline_paths(item, layer)
    ]


def _points_differ(first: np.ndarray, second: np.ndarray) -> bool:
    return float(np.linalg.norm(first - second)) > 1e-9


def _emit_raster_row(
    lines: list[str],
    row: _RasterRow,
    layer: OperationLayer,
    laser: LaserSettings,
    power: int,
    power_max: int,
    current: np.ndarray,
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
            span_start = span.points[0]
            span_end = span.points[-1]
            if _points_differ(position, span_start):
                gap = float(np.linalg.norm(span_start - position))
                off_travel += gap
                lines.append(
                    f"G1 X{_fmt(span_start[0])} Y{_fmt(span_start[1])} "
                    f"F{_fmt(layer.speed_mm_min)}"
                )
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
) -> ProjectJob:
    """Generate one guarded vector program containing all enabled line layers."""
    document.validate()
    controller_power_max = int(power_max or laser.power_max)
    raster_sources = _preflight_raster_budget(document, controller_power_max)
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
    layer_plans: list[_LayerPlan] = []
    raster_command_estimate = 0
    vector_command_estimate = 0
    for layer in sorted(document.layers, key=lambda item: item.priority):
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
                _LayerPlan(
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

        local_design_paths = _operation_paths(document, layer, layer_objects)
        if not local_design_paths:
            continue
        validate_paths(
            local_design_paths,
            local_work_area,
            local_margin,
            coordinate_label="local design",
        )
        design_paths = _place_paths(local_design_paths, coordinate_frame)
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
        paths = _controller_paths(design_paths, laser)
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
        layer_plans.append(_LayerPlan(layer=layer, paths=paths))
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
        layer = layer_plan.layer
        paths = layer_plan.paths
        power = layer.controller_power(controller_power_max)
        layer_cut = 0.0
        layer_travel = 0.0
        layer_path_count = 0
        lines.append(
            f"; Layer {layer.name.replace(';', ',')[:80]} · "
            f"{layer.speed_mm_min:g} mm/min · {layer.power_percent:g}% · "
            f"{layer.passes} pass(es) · vector correction "
            f"{layer.vector_power_correction:+g} · raster correction "
            f"{layer.raster_power_correction:+g}"
        )
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
                    "raster_tone": (
                        "ordered-dither-8x8"
                        if layer_plan.dithered_image
                        else ""
                    ),
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
            if layer.mode == LayerMode.RASTER:
                rows = layer_plan.raster_rows
                if pass_index % 2:
                    rows = [
                        _reverse_raster_row(row)
                        for row in reversed(layer_plan.raster_rows)
                    ]
                comparison_position = current.copy()
                for row in rows:
                    source_motion = _raster_motion_points(
                        row.points,
                        layer.overscan_percent,
                    )
                    source_order_travel += float(
                        np.linalg.norm(source_motion[0] - comparison_position)
                    )
                    comparison_position = source_motion[-1]
                for row in rows:
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
                source_motion = source_path.points
                source_order_travel += float(
                    np.linalg.norm(source_motion[0] - comparison_position)
                )
                comparison_position = source_motion[-1]
            ordered = _containment_aware_nearest_order(paths, current) if nearest_enabled else paths
            for path in ordered:
                points = path.points
                if len(points) < 2:
                    continue
                lines.append(e3_metadata_line("path", {"name": path.source_tag}))
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
                commanded_power = power
                for point in points[1:]:
                    if power > 0:
                        continue
                    lines.append(
                        f"G1 X{_fmt(point[0])} Y{_fmt(point[1])} "
                        f"F{_fmt(layer.speed_mm_min)}"
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
    command_count = _stream_command_count(lines)
    if command_count > _MAX_STREAM_COMMANDS:
        raise ValueError(
            f"Generated output contains {command_count:,} streamed commands, exceeding "
            f"the {_MAX_STREAM_COMMANDS:,}-command limit; increase raster line intervals "
            "or simplify project geometry"
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
        raster_assets=tuple(
            identity
            for layer_plan in layer_plans
            for identity in layer_plan.raster_assets
        ),
        coordinate_space=document.coordinate_space,
        coordinate_frame_signature=coordinate_frame_signature,
        guarded_output_polygon_mm=guarded_polygon,
    )


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
            paths.extend(object_polylines(item))
    if not paths:
        raise ValueError("The project contains no visible output geometry")
    local_bounds = _bounds(paths)
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
