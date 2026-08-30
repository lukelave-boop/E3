"""Qt-neutral advisory project checks before exact toolpath planning.

This module deliberately inspects only immutable snapshots, persisted project
facts, simple analytic bounds, and bounded raster headers. It does not flatten
vector geometry, decode raster pixels, plan motion, or contact a controller.
The exact project toolpath generator remains authoritative.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from ..air_assist import AirAssistCommands
from ..config import WorkArea
from .model import (
    OBJECT_ROLE_KEY,
    STOCK_BOUNDARY_ROLE,
    Bounds,
    CoordinateSpace,
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    SceneObject,
)
from .path_geometry import PathCubicSegment, PathFillRule, PathLineSegment
from .planner_limits import (
    MAX_RASTER_ROWS,
    MAX_RASTER_SAMPLES,
    MAX_STREAM_COMMANDS,
    MAX_UNIQUE_RASTER_ASSETS,
    MAX_UNIQUE_RASTER_DECODED_BYTES,
    MAX_UNIQUE_RASTER_ENCODED_BYTES,
    STREAM_COMMAND_RESERVE,
)
from .raster_asset import RasterAssetMetadata, probe_raster_asset

if TYPE_CHECKING:
    from ..calibration.support import HoneycombCoordinateFrame


_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BOUNDS_TOLERANCE_MM = 1e-6
_NONZERO_OUTPUT_TOLERANCE_MM = 1e-9
_STRUCTURED_SCAN_EDGE_TEST_LIMIT = 50_000


class PreflightSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


def _freeze_context_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_context_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_context_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_context_value(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    code: str
    severity: PreflightSeverity
    title: str
    message: str
    detail: str = ""
    context: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        code = str(self.code).strip().casefold()
        if not _CODE_RE.fullmatch(code):
            raise ValueError("Preflight finding codes must be stable dotted identifiers")
        severity = (
            self.severity
            if isinstance(self.severity, PreflightSeverity)
            else PreflightSeverity(str(self.severity))
        )
        title = str(self.title).strip()
        message = str(self.message).strip()
        if not title or not message:
            raise ValueError("Preflight finding title and message must not be empty")
        if not isinstance(self.context, Mapping):
            raise TypeError("Preflight finding context must be a mapping")

        object.__setattr__(self, "code", code)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "detail", str(self.detail).strip())
        object.__setattr__(
            self,
            "context",
            _freeze_context_value(dict(self.context)),
        )


@dataclass(frozen=True, slots=True)
class PreflightCounts:
    info: int = 0
    warnings: int = 0
    blockers: int = 0

    def __post_init__(self) -> None:
        for name in ("info", "warnings", "blockers"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"Preflight count {name} must be a non-negative integer")

    @property
    def total(self) -> int:
        return self.info + self.warnings + self.blockers


@dataclass(frozen=True, slots=True)
class JobPreflightReport:
    findings: tuple[PreflightFinding, ...] = ()
    counts: PreflightCounts = field(init=False)

    def __post_init__(self) -> None:
        findings = tuple(self.findings)
        if not all(isinstance(item, PreflightFinding) for item in findings):
            raise TypeError("Job preflight findings must be PreflightFinding values")
        counts = PreflightCounts(
            info=sum(item.severity is PreflightSeverity.INFO for item in findings),
            warnings=sum(
                item.severity is PreflightSeverity.WARNING for item in findings
            ),
            blockers=sum(
                item.severity is PreflightSeverity.BLOCKER for item in findings
            ),
        )
        object.__setattr__(self, "findings", findings)
        object.__setattr__(self, "counts", counts)

    @property
    def ready(self) -> bool:
        return not self.has_blockers

    @property
    def has_blockers(self) -> bool:
        return self.counts.blockers > 0

    @property
    def info_count(self) -> int:
        return self.counts.info

    @property
    def warning_count(self) -> int:
        return self.counts.warnings

    @property
    def blocker_count(self) -> int:
        return self.counts.blockers


@dataclass(frozen=True, slots=True)
class JobPreflightContext:
    """Detached execution/configuration facts safe for worker-side inspection."""

    machine_work_area: Bounds | WorkArea | None
    controller_power_max: int = 1000
    machine_max_work_feed_mm_min: float | None = None
    machine_max_travel_feed_mm_min: float | None = None
    planned_travel_feed_mm_min: float | None = None
    spot_offset_x_mm: float = 0.0
    spot_offset_y_mm: float = 0.0
    air_assist_commands: AirAssistCommands | None = None
    coordinate_frame: HoneycombCoordinateFrame | None = None
    honeycomb_execution_signature: tuple[str, int, str, str] | None = None
    guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None
    machine_id: str = ""
    machine_profile_id: str = ""
    expected_calibration_profile_id: str | None = None
    active_calibration_profile_id: str | None = None
    bed_calibration_state: str | None = None
    bed_calibration_reasons: tuple[str, ...] = ()
    honeycomb_support_state: str | None = None
    honeycomb_support_reasons: tuple[str, ...] = ()
    execution_ready: bool = True
    execution_unready_reason: str = ""

    def __post_init__(self) -> None:
        area = self.machine_work_area
        if isinstance(area, WorkArea):
            area = Bounds(area.x_min, area.y_min, area.x_max, area.y_max)
        elif area is not None and not isinstance(area, Bounds):
            raise TypeError("machine_work_area must be Bounds, WorkArea, or None")

        signature = self.honeycomb_execution_signature
        if signature is not None:
            signature = tuple(signature)

        polygon = self.guarded_output_polygon_mm
        if polygon is not None:
            polygon = tuple((float(point[0]), float(point[1])) for point in polygon)

        if type(self.execution_ready) is not bool:
            raise TypeError("execution_ready must be an exact boolean")
        for name in ("spot_offset_x_mm", "spot_offset_y_mm"):
            value = getattr(self, name)
            if type(value) is bool or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a finite number")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if (
            self.air_assist_commands is not None
            and not isinstance(self.air_assist_commands, AirAssistCommands)
        ):
            raise TypeError(
                "air_assist_commands must be AirAssistCommands or None"
            )

        object.__setattr__(self, "machine_work_area", area)
        object.__setattr__(self, "honeycomb_execution_signature", signature)
        object.__setattr__(self, "guarded_output_polygon_mm", polygon)
        object.__setattr__(self, "machine_id", str(self.machine_id).strip())
        object.__setattr__(
            self,
            "machine_profile_id",
            str(self.machine_profile_id).strip(),
        )
        for name in (
            "expected_calibration_profile_id",
            "active_calibration_profile_id",
        ):
            value = getattr(self, name)
            object.__setattr__(
                self,
                name,
                None if value is None else str(value).strip() or None,
            )
        for name in ("bed_calibration_state", "honeycomb_support_state"):
            value = getattr(self, name)
            object.__setattr__(
                self,
                name,
                None if value is None else str(value).strip().upper() or None,
            )
        for name in ("bed_calibration_reasons", "honeycomb_support_reasons"):
            object.__setattr__(
                self,
                name,
                tuple(str(value).strip() for value in getattr(self, name) if str(value).strip()),
            )
        object.__setattr__(
            self,
            "execution_unready_reason",
            str(self.execution_unready_reason).strip(),
        )


def _bounds_tuple(bounds: Bounds) -> tuple[float, float, float, float]:
    return bounds.x_min, bounds.y_min, bounds.x_max, bounds.y_max


def _bounds_match(first: Bounds, second: Bounds) -> bool:
    return all(
        abs(left - right) <= _BOUNDS_TOLERANCE_MM
        for left, right in zip(
            _bounds_tuple(first),
            _bounds_tuple(second),
            strict=True,
        )
    )


def _inside(outer: Bounds, inner: Bounds) -> bool:
    return (
        inner.x_min >= outer.x_min - _BOUNDS_TOLERANCE_MM
        and inner.y_min >= outer.y_min - _BOUNDS_TOLERANCE_MM
        and inner.x_max <= outer.x_max + _BOUNDS_TOLERANCE_MM
        and inner.y_max <= outer.y_max + _BOUNDS_TOLERANCE_MM
    )


def _finite_number(value: object) -> float | None:
    if type(value) is bool or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _serialized_coordinate_mm(value: float) -> float:
    """Quantize a coordinate exactly as project G-code serialization does."""
    if abs(value) < 0.0005:
        value = 0.0
    return float(f"{value:.3f}")


def _transform_point(item: SceneObject, x: float, y: float) -> tuple[float, float]:
    transform = item.transform
    local_x = x * transform.width_mm * (-1.0 if transform.mirror_x else 1.0)
    local_y = y * transform.height_mm * (-1.0 if transform.mirror_y else 1.0)
    angle = math.radians(transform.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        transform.x_mm + local_x * cosine - local_y * sine,
        transform.y_mm + local_x * sine + local_y * cosine,
    )


def _rectangle_corners(item: SceneObject) -> tuple[tuple[float, float], ...]:
    return tuple(
        _transform_point(item, x, y)
        for x, y in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))
    )


def _primitive_local_outline(
    item: SceneObject,
) -> tuple[tuple[float, float], ...] | None:
    """Return the exact bounded primitive samples used by project planning."""
    if item.kind is ObjectKind.RECTANGLE:
        radius_mm = _finite_number(item.geometry.get("corner_radius_mm", 0.0))
        if radius_mm is None or radius_mm < 0.0:
            return None
        radius_x = max(0.0, min(0.5, radius_mm / item.transform.width_mm))
        radius_y = max(0.0, min(0.5, radius_mm / item.transform.height_mm))
        if radius_x <= 1e-9 or radius_y <= 1e-9:
            return (
                (-0.5, -0.5),
                (0.5, -0.5),
                (0.5, 0.5),
                (-0.5, 0.5),
                (-0.5, -0.5),
            )
        points: list[tuple[float, float]] = []
        centers = (
            (0.5 - radius_x, -0.5 + radius_y, -90.0, 0.0),
            (0.5 - radius_x, 0.5 - radius_y, 0.0, 90.0),
            (-0.5 + radius_x, 0.5 - radius_y, 90.0, 180.0),
            (-0.5 + radius_x, -0.5 + radius_y, 180.0, 270.0),
        )
        for center_x, center_y, start, end in centers:
            for index in range(9):
                angle = math.radians(start + (end - start) * index / 8)
                points.append(
                    (
                        center_x + radius_x * math.cos(angle),
                        center_y + radius_y * math.sin(angle),
                    )
                )
        points.append(points[0])
        return tuple(points)
    if item.kind is ObjectKind.ELLIPSE:
        return tuple(
            (
                0.5 * math.cos(2.0 * math.pi * index / 72),
                0.5 * math.sin(2.0 * math.pi * index / 72),
            )
            for index in range(73)
        )
    return None


def _scanline_x_intervals(
    polygons: tuple[tuple[tuple[float, float], ...], ...],
    y: float,
    fill_rule: PathFillRule,
) -> tuple[tuple[float, float], ...]:
    """Mirror exact planner intersections for bounded, already-linear geometry."""
    intersections: list[float] = []
    winding_events: list[tuple[float, int]] = []
    for polygon in polygons:
        for start, end in zip(polygon[:-1], polygon[1:], strict=False):
            start_y = start[1]
            end_y = end[1]
            low_y = min(start_y, end_y)
            high_y = max(start_y, end_y)
            if high_y - low_y <= 1e-12 or not (low_y <= y < high_y):
                continue
            ratio = (y - start_y) / (end_y - start_y)
            x = start[0] + ratio * (end[0] - start[0])
            if fill_rule is PathFillRule.EVENODD:
                intersections.append(x)
            else:
                winding_events.append((x, 1 if end_y > start_y else -1))

    if fill_rule is PathFillRule.EVENODD:
        intersections.sort()
        return tuple(
            (intersections[index], intersections[index + 1])
            for index in range(0, len(intersections) - 1, 2)
            if intersections[index + 1] - intersections[index] > 1e-9
        )

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
    return tuple(intervals)


def _bounded_scan_has_serialized_motion(
    polygons: tuple[tuple[tuple[float, float], ...], ...],
    layer: OperationLayer,
    serialized_design_point: Callable[
        [tuple[float, float]], tuple[float, float] | None
    ],
    *,
    fill_rule: PathFillRule,
) -> bool | None:
    """Classify exact linear scan spans, or defer when bounded work is exceeded."""
    interval = _finite_number(layer.line_interval_mm)
    angle_degrees = _finite_number(layer.scan_angle_deg)
    if interval is None or interval <= 0.0 or angle_degrees is None:
        return None
    angle = math.radians(angle_degrees)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    scan_polygons = tuple(
        tuple(
            (
                point[0] * cosine + point[1] * sine,
                -point[0] * sine + point[1] * cosine,
            )
            for point in polygon
        )
        for polygon in polygons
    )
    if not scan_polygons or any(len(polygon) < 2 for polygon in scan_polygons):
        return False
    scan_points = tuple(point for polygon in scan_polygons for point in polygon)
    y_min = min(point[1] for point in scan_points)
    y_max = max(point[1] for point in scan_points)
    scaled_first_y = (y_min - 1e-9) / interval
    if not math.isfinite(scaled_first_y):
        return None
    first_y = math.ceil(scaled_first_y) * interval
    if first_y > y_max + 1e-9:
        return False
    scaled_row_count = (y_max + 1e-9 - first_y) / interval
    if not math.isfinite(scaled_row_count):
        return None
    row_count = int(math.floor(scaled_row_count)) + 1
    edge_count = sum(max(0, len(polygon) - 1) for polygon in scan_polygons)
    if row_count * edge_count > _STRUCTURED_SCAN_EDGE_TEST_LIMIT:
        return None

    for row in range(row_count):
        y = first_y + row * interval
        for start_x, end_x in _scanline_x_intervals(
            scan_polygons,
            y,
            fill_rule,
        ):
            start = (start_x * cosine - y * sine, start_x * sine + y * cosine)
            end = (end_x * cosine - y * sine, end_x * sine + y * cosine)
            serialized_start = serialized_design_point(start)
            serialized_end = serialized_design_point(end)
            if serialized_start is None or serialized_end is None:
                return None
            if serialized_start != serialized_end:
                return True
    return False


def _bounds_from_points(points: tuple[tuple[float, float], ...]) -> Bounds:
    return Bounds(
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _simple_object_bounds(item: SceneObject) -> Bounds | None:
    if item.kind is ObjectKind.RECTANGLE:
        radius = _finite_number(item.geometry.get("corner_radius_mm", 0.0))
        if radius != 0.0:
            return None
        return _bounds_from_points(_rectangle_corners(item))
    if item.kind is ObjectKind.LINE:
        points = item.geometry.get("points")
        if not isinstance(points, list) or len(points) != 2:
            return None
        try:
            transformed = tuple(
                _transform_point(item, float(point[0]), float(point[1]))
                for point in points
            )
        except (TypeError, ValueError, IndexError):
            return None
        if not all(math.isfinite(value) for point in transformed for value in point):
            return None
        return _bounds_from_points(transformed)
    return None


def _object_has_potential_nonzero_output(
    item: SceneObject,
    layer: OperationLayer,
    document: ProjectDocument,
    context: JobPreflightContext,
) -> bool | None:
    """Prove serialized motion, prove none, or defer to exact generation."""
    if not _valid_transform(item)[0]:
        # Invalid transforms are reported separately and must not suppress a
        # layer-level configuration finding.
        return True

    def controller_design(
        design_point: tuple[float, float],
    ) -> tuple[float, float] | None:
        design_x, design_y = design_point
        if document.coordinate_space is CoordinateSpace.MACHINE:
            machine_x, machine_y = design_x, design_y
        elif document.coordinate_space is CoordinateSpace.HONEYCOMB_LOCAL:
            frame = context.coordinate_frame
            if frame is None:
                return None
            try:
                machine_x, machine_y = frame.local_to_machine(design_x, design_y)
            except (AttributeError, TypeError, ValueError):
                return None
        else:
            return None
        controller_point = (
            machine_x - context.spot_offset_x_mm,
            machine_y - context.spot_offset_y_mm,
        )
        if not all(math.isfinite(value) for value in controller_point):
            return None
        return controller_point

    def controller(point: tuple[float, float]) -> tuple[float, float] | None:
        return controller_design(_transform_point(item, *point))

    def serialized_design(
        design_point: tuple[float, float],
    ) -> tuple[float, float] | None:
        result = controller_design(design_point)
        if result is None:
            return None
        return (
            _serialized_coordinate_mm(result[0]),
            _serialized_coordinate_mm(result[1]),
        )

    def serialized(point: tuple[float, float]) -> tuple[float, float] | None:
        return serialized_design(_transform_point(item, *point))

    if item.kind in {ObjectKind.RECTANGLE, ObjectKind.ELLIPSE}:
        local_outline = _primitive_local_outline(item)
        if local_outline is None:
            # Malformed geometry is reported elsewhere; remain conservative.
            return True
        outline = tuple(serialized(point) for point in local_outline)
        if any(point is None for point in outline):
            return True
        serialized_outline = tuple(point for point in outline if point is not None)
        if layer.mode is LayerMode.LINE:
            return any(
                first != second
                for first, second in zip(
                    serialized_outline[:-1],
                    serialized_outline[1:],
                    strict=True,
                )
            )
        if layer.mode in {LayerMode.FILL, LayerMode.RASTER}:
            design_outline = tuple(
                _transform_point(item, *point) for point in local_outline
            )
            return _bounded_scan_has_serialized_motion(
                (design_outline,),
                layer,
                serialized_design,
                fill_rule=PathFillRule.EVENODD,
            )
        return True

    if item.kind is ObjectKind.LINE:
        points = item.geometry.get("points")
        if not isinstance(points, list) or len(points) != 2:
            return True
        try:
            endpoints = tuple(
                serialized((float(point[0]), float(point[1]))) for point in points
            )
        except (TypeError, ValueError, IndexError):
            return True
        return None in endpoints or endpoints[0] != endpoints[1]

    if item.kind is ObjectKind.IMAGE:
        # Structured preflight intentionally does not decode raster pixels, so
        # it cannot distinguish a powered image from an all-white asset. Exact
        # generation remains authoritative after bounded asset checks.
        return None

    if item.kind not in {ObjectKind.PATH, ObjectKind.POLYGON}:
        return True

    try:
        geometry = item.path_geometry()
    except (TypeError, ValueError):
        # Preserve the independent malformed-geometry blocker and remain
        # conservative about the affected layer.
        return True

    def differs(first: tuple[float, float], second: tuple[float, float]) -> bool:
        first_point = serialized(first)
        second_point = serialized(second)
        if first_point is None or second_point is None:
            return True
        first_x, first_y = first_point
        second_x, second_y = second_point
        return (
            math.hypot(second_x - first_x, second_y - first_y)
            > _NONZERO_OUTPUT_TOLERANCE_MM
        )

    if layer.mode in {LayerMode.FILL, LayerMode.RASTER}:
        if any(
            isinstance(segment, PathCubicSegment)
            for subpath in geometry.subpaths
            for segment in subpath.segments
        ):
            # Curves require the exact bounded flattener. Defer their mapping
            # decision instead of treating un-emitted controls as output.
            return None
        polygons: list[tuple[tuple[float, float], ...]] = []
        for subpath in geometry.subpaths:
            if not subpath.closed:
                continue
            points = [_transform_point(item, *subpath.start)]
            for segment in subpath.segments:
                if not isinstance(segment, PathLineSegment):  # pragma: no cover
                    return None
                points.append(_transform_point(item, *segment.to))
            if points[-1] != points[0]:
                points.append(points[0])
            polygons.append(tuple(points))
        return _bounded_scan_has_serialized_motion(
            tuple(polygons),
            layer,
            serialized_design,
            fill_rule=geometry.fill_rule,
        )

    if layer.mode is not LayerMode.LINE:
        # Invalid/unknown modes are reported independently; keep their output
        # configuration diagnosis conservative.
        return True

    deferred = False
    for subpath in geometry.subpaths:
        current = subpath.start
        for segment in subpath.segments:
            if isinstance(segment, PathLineSegment):
                if differs(current, segment.to):
                    return True
            elif isinstance(segment, PathCubicSegment):
                if differs(current, segment.to):
                    return True
                if not (
                    segment.control_1 == current
                    and segment.control_2 == current
                    and segment.to == current
                ):
                    # Control-only excursions may disappear at the exact
                    # flattening tolerance or may produce intermediate points.
                    # Only exact generation can distinguish those cases.
                    deferred = True
            else:  # pragma: no cover - validated native geometry is exhaustive
                return True
            current = segment.to
        if subpath.closed and differs(current, subpath.start):
            return True
    return None if deferred else False


def _image_scan_counts(item: SceneObject, layer: OperationLayer) -> tuple[int, int]:
    angle = math.radians(layer.scan_angle_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    scan_points = tuple(
        (x * cosine + y * sine, -x * sine + y * cosine)
        for x, y in _rectangle_corners(item)
    )
    width = max(point[0] for point in scan_points) - min(
        point[0] for point in scan_points
    )
    height = max(point[1] for point in scan_points) - min(
        point[1] for point in scan_points
    )
    pitch = layer.line_interval_mm
    return (
        max(1, int(math.ceil(height / pitch - 1e-12))),
        max(1, int(math.ceil(width / pitch - 1e-12))),
    )


def _output_object(item: SceneObject) -> bool:
    metadata = item.metadata
    return not (
        isinstance(metadata, Mapping)
        and metadata.get(OBJECT_ROLE_KEY) == STOCK_BOUNDARY_ROLE
    )


def _valid_transform(item: SceneObject) -> tuple[bool, str]:
    transform = item.transform
    for name in ("x_mm", "y_mm", "rotation_deg"):
        if _finite_number(getattr(transform, name, None)) is None:
            return False, name
    for name in ("width_mm", "height_mm"):
        value = _finite_number(getattr(transform, name, None))
        if value is None or value <= 0.0:
            return False, name
    if type(transform.mirror_x) is not bool:
        return False, "mirror_x"
    if type(transform.mirror_y) is not bool:
        return False, "mirror_y"
    return True, ""


def build_job_preflight_report(
    document: ProjectDocument,
    context: JobPreflightContext,
) -> JobPreflightReport:
    """Build a deterministic advisory report without invoking exact planning."""

    if not isinstance(document, ProjectDocument):
        raise TypeError("document must be a ProjectDocument")
    if not isinstance(context, JobPreflightContext):
        raise TypeError("context must be a JobPreflightContext")

    findings: list[PreflightFinding] = []

    def add(
        code: str,
        severity: PreflightSeverity,
        title: str,
        message: str,
        *,
        detail: str = "",
        finding_context: Mapping[str, object] | None = None,
    ) -> None:
        findings.append(
            PreflightFinding(
                code=code,
                severity=severity,
                title=title,
                message=message,
                detail=detail,
                context={} if finding_context is None else finding_context,
            )
        )

    layers = [item for item in document.layers if isinstance(item, OperationLayer)]
    objects = [item for item in document.objects if isinstance(item, SceneObject)]
    if len(layers) != len(document.layers):
        add(
            "project.layer_invalid",
            PreflightSeverity.BLOCKER,
            "Invalid layer record",
            "The project contains a value that is not an operation layer.",
        )
    if len(objects) != len(document.objects):
        add(
            "project.object_invalid",
            PreflightSeverity.BLOCKER,
            "Invalid object record",
            "The project contains a value that is not a scene object.",
        )
    if not layers:
        add(
            "project.layers_missing",
            PreflightSeverity.BLOCKER,
            "No operation layers",
            "The project has no operation layer to plan.",
        )
    if not objects:
        add(
            "project.objects_missing",
            PreflightSeverity.BLOCKER,
            "No project objects",
            "The project has no objects to include in output.",
        )

    layer_ids = [layer.id for layer in layers]
    if len(layer_ids) != len(set(layer_ids)):
        add(
            "project.layer_ids_duplicate",
            PreflightSeverity.BLOCKER,
            "Duplicate layer identifiers",
            "Operation layer identifiers must be unique before planning.",
        )
    object_ids = [item.id for item in objects]
    if len(object_ids) != len(set(object_ids)):
        add(
            "project.object_ids_duplicate",
            PreflightSeverity.BLOCKER,
            "Duplicate object identifiers",
            "Scene object identifiers must be unique before planning.",
        )

    power_max_valid = (
        type(context.controller_power_max) is int
        and context.controller_power_max > 0
    )
    if not power_max_valid:
        add(
            "context.power_max_invalid",
            PreflightSeverity.BLOCKER,
            "Invalid controller power range",
            "The controller power maximum must be a positive integer.",
            finding_context={"value": context.controller_power_max},
        )

    machine_area = context.machine_work_area
    if document.coordinate_space is CoordinateSpace.MACHINE:
        if machine_area is None:
            add(
                "work_area.machine_missing",
                PreflightSeverity.BLOCKER,
                "Machine work area unavailable",
                "A configured machine work area is required for project preflight.",
            )
        elif not _bounds_match(document.work_area, machine_area):
            add(
                "work_area.mismatch",
                PreflightSeverity.BLOCKER,
                "Project and machine work areas differ",
                "The machine-coordinate project work area does not match the configured machine.",
                finding_context={
                    "project_bounds_mm": _bounds_tuple(document.work_area),
                    "machine_bounds_mm": _bounds_tuple(machine_area),
                },
            )
        if context.coordinate_frame is not None:
            add(
                "honeycomb.frame_unexpected",
                PreflightSeverity.BLOCKER,
                "Unexpected honeycomb frame",
                "A honeycomb coordinate frame cannot be applied to a machine-coordinate project.",
            )
        if context.guarded_output_polygon_mm is not None:
            add(
                "honeycomb.output_polygon_without_frame",
                PreflightSeverity.BLOCKER,
                "Unexpected honeycomb output polygon",
                "A guarded honeycomb output polygon requires a honeycomb-local project.",
            )
    elif document.coordinate_space is CoordinateSpace.HONEYCOMB_LOCAL:
        frame = context.coordinate_frame
        if (
            context.bed_calibration_state is not None
            and context.bed_calibration_state != "VALID"
        ):
            add(
                "honeycomb.bed_calibration_not_valid",
                PreflightSeverity.BLOCKER,
                "Camera-to-machine map is not valid",
                "Honeycomb-local output requires a current valid camera-to-machine map.",
                detail="; ".join(context.bed_calibration_reasons),
                finding_context={"state": context.bed_calibration_state},
            )
        if (
            context.honeycomb_support_state is not None
            and context.honeycomb_support_state != "CURRENT"
        ):
            add(
                "honeycomb.support_not_current",
                PreflightSeverity.BLOCKER,
                "Honeycomb support is not current",
                "Honeycomb-local output requires the current configured support evidence.",
                detail="; ".join(context.honeycomb_support_reasons),
                finding_context={"state": context.honeycomb_support_state},
            )
        if frame is None:
            add(
                "honeycomb.frame_missing",
                PreflightSeverity.BLOCKER,
                "Honeycomb coordinate frame missing",
                "Honeycomb-local output requires the current reviewed coordinate frame.",
            )
        if machine_area is None:
            add(
                "honeycomb.machine_work_area_missing",
                PreflightSeverity.BLOCKER,
                "Machine work area unavailable",
                "Honeycomb-local output requires an independent machine work area.",
            )
        if frame is not None:
            expected = Bounds(0.0, 0.0, frame.width_mm, frame.height_mm)
            if not _bounds_match(document.work_area, expected):
                add(
                    "honeycomb.work_area_mismatch",
                    PreflightSeverity.BLOCKER,
                    "Project and support bounds differ",
                    "The honeycomb-local project bounds do not match the reviewed support frame.",
                    finding_context={
                        "project_bounds_mm": _bounds_tuple(document.work_area),
                        "support_bounds_mm": _bounds_tuple(expected),
                    },
                )

        signature = context.honeycomb_execution_signature
        if signature is None:
            add(
                "honeycomb.binding_missing",
                PreflightSeverity.BLOCKER,
                "Honeycomb support binding unavailable",
                "Current execution-grade support and complete bed-map evidence are required.",
            )
        elif len(signature) != 4:
            add(
                "honeycomb.binding_invalid",
                PreflightSeverity.BLOCKER,
                "Honeycomb support binding invalid",
                "The support/bed execution signature is malformed.",
            )
        else:
            if frame is not None and tuple(signature[:3]) != tuple(
                frame.provenance_signature
            ):
                add(
                    "honeycomb.frame_binding_mismatch",
                    PreflightSeverity.BLOCKER,
                    "Honeycomb frame binding changed",
                    "The reviewed support frame does not match the current execution binding.",
                )
            bed_digest = str(signature[3]).casefold()
            if not _SHA256_RE.fullmatch(bed_digest):
                add(
                    "honeycomb.bed_binding_invalid",
                    PreflightSeverity.BLOCKER,
                    "Bed-map binding invalid",
                    "The honeycomb execution binding lacks a valid complete bed-map digest.",
                )

        expected_profile = context.expected_calibration_profile_id
        active_profile = context.active_calibration_profile_id
        if expected_profile is None or active_profile is None:
            add(
                "honeycomb.profile_binding_missing",
                PreflightSeverity.BLOCKER,
                "Calibration profile binding missing",
                "The running machine and active calibration profile must be explicitly bound.",
                finding_context={
                    "machine_id": context.machine_id,
                    "machine_profile_id": context.machine_profile_id,
                    "expected_profile_id": expected_profile,
                    "active_profile_id": active_profile,
                },
            )
        elif expected_profile != active_profile:
            add(
                "honeycomb.profile_binding_mismatch",
                PreflightSeverity.BLOCKER,
                "Calibration profile binding mismatch",
                "The active calibration profile does not match the running machine binding.",
                finding_context={
                    "machine_id": context.machine_id,
                    "machine_profile_id": context.machine_profile_id,
                    "expected_profile_id": expected_profile,
                    "active_profile_id": active_profile,
                },
            )
        polygon = context.guarded_output_polygon_mm
        if polygon is not None and len(polygon) < 3:
            add(
                "honeycomb.output_polygon_invalid",
                PreflightSeverity.BLOCKER,
                "Guarded output polygon invalid",
                "The configured honeycomb output authority must contain at least three points.",
            )
    else:
        add(
            "coordinate_space.unsupported",
            PreflightSeverity.BLOCKER,
            "Unsupported coordinate space",
            "The project coordinate space is not supported for job planning.",
            finding_context={"coordinate_space": str(document.coordinate_space)},
        )

    layer_by_id: dict[str, OperationLayer] = {}
    for layer in layers:
        layer_by_id.setdefault(layer.id, layer)
        setting_rules = (
            ("speed_mm_min", 0.0, None, False),
            ("power_percent", 0.0, 100.0, True),
            ("line_interval_mm", 0.0, None, False),
            ("scan_angle_deg", None, None, True),
            ("overscan_percent", 0.0, 100.0, True),
            ("vector_power_correction", -100.0, 100.0, True),
            ("raster_power_correction", -100.0, 100.0, True),
        )
        if not isinstance(layer.mode, LayerMode):
            add(
                "layer.mode_invalid",
                PreflightSeverity.BLOCKER,
                "Invalid layer mode",
                f"Layer {layer.name!r} has an unsupported operation mode.",
                finding_context={"layer_id": layer.id, "mode": str(layer.mode)},
            )
        if type(layer.passes) is not int or layer.passes < 1:
            add(
                "layer.setting_invalid",
                PreflightSeverity.BLOCKER,
                "Invalid layer setting",
                f"Layer {layer.name!r} must use at least one integer pass.",
                finding_context={"layer_id": layer.id, "field": "passes"},
            )
        for field_name, minimum, maximum, minimum_inclusive in setting_rules:
            value = _finite_number(getattr(layer, field_name, None))
            invalid = value is None
            if value is not None and minimum is not None:
                invalid = invalid or (
                    value < minimum if minimum_inclusive else value <= minimum
                )
            if value is not None and maximum is not None:
                invalid = invalid or value > maximum
            if invalid:
                add(
                    "layer.setting_invalid",
                    PreflightSeverity.BLOCKER,
                    "Invalid layer setting",
                    f"Layer {layer.name!r} has an invalid {field_name} value.",
                    finding_context={"layer_id": layer.id, "field": field_name},
                )
        for field_name in ("air_assist", "visible", "output_enabled"):
            if type(getattr(layer, field_name, None)) is not bool:
                add(
                    "layer.setting_invalid",
                    PreflightSeverity.BLOCKER,
                    "Invalid layer setting",
                    f"Layer {layer.name!r} has a non-boolean {field_name} value.",
                    finding_context={"layer_id": layer.id, "field": field_name},
                )
        if type(layer.priority) is not int:
            add(
                "layer.setting_invalid",
                PreflightSeverity.BLOCKER,
                "Invalid layer setting",
                f"Layer {layer.name!r} has a non-integer priority value.",
                finding_context={"layer_id": layer.id, "field": "priority"},
            )

    enabled_layers = [
        layer for layer in layers if layer.visible is True and layer.output_enabled is True
    ]
    if not enabled_layers:
        add(
            "output.layers_disabled",
            PreflightSeverity.BLOCKER,
            "No enabled output layers",
            "No visible operation layer currently has output enabled.",
        )

    enabled_pairs: list[tuple[SceneObject, OperationLayer]] = []
    simple_bounds: list[tuple[SceneObject, Bounds]] = []
    deferred_bounds = 0
    for item in objects:
        layer = layer_by_id.get(item.layer_id)
        if layer is None:
            add(
                "object.layer_missing",
                PreflightSeverity.BLOCKER,
                "Object layer unavailable",
                f"Object {item.name!r} references an unknown operation layer.",
                finding_context={"object_id": item.id, "layer_id": item.layer_id},
            )
            continue
        if not isinstance(item.kind, ObjectKind):
            add(
                "object.kind_invalid",
                PreflightSeverity.BLOCKER,
                "Invalid object kind",
                f"Object {item.name!r} has an unsupported kind.",
                finding_context={"object_id": item.id, "kind": str(item.kind)},
            )
        transform_valid, invalid_field = _valid_transform(item)
        if not transform_valid:
            add(
                "object.transform_invalid",
                PreflightSeverity.BLOCKER,
                "Invalid object transform",
                f"Object {item.name!r} has an invalid transform.",
                finding_context={"object_id": item.id, "field": invalid_field},
            )
        for field_name in ("visible", "locked"):
            if type(getattr(item, field_name, None)) is not bool:
                add(
                    "object.setting_invalid",
                    PreflightSeverity.BLOCKER,
                    "Invalid object setting",
                    f"Object {item.name!r} has a non-boolean {field_name} value.",
                    finding_context={"object_id": item.id, "field": field_name},
                )

        enabled = (
            item.visible is True
            and _output_object(item)
            and layer.visible is True
            and layer.output_enabled is True
        )
        if not enabled:
            continue
        enabled_pairs.append((item, layer))

        valid_mode_and_kind = isinstance(layer.mode, LayerMode) and isinstance(
            item.kind,
            ObjectKind,
        )
        unsupported = valid_mode_and_kind and (
            (
                layer.mode is LayerMode.LINE
                and item.kind not in {
                    ObjectKind.RECTANGLE,
                    ObjectKind.ELLIPSE,
                    ObjectKind.LINE,
                    ObjectKind.PATH,
                    ObjectKind.POLYGON,
                }
            )
            or (
                layer.mode in {LayerMode.FILL, LayerMode.RASTER}
                and item.kind in {ObjectKind.TEXT, ObjectKind.LINE, ObjectKind.IMAGE}
                and not (
                    layer.mode is LayerMode.RASTER
                    and item.kind is ObjectKind.IMAGE
                )
            )
        )
        if unsupported:
            add(
                "object.unsupported_layer_mode",
                PreflightSeverity.BLOCKER,
                "Unsupported object and layer combination",
                f"{layer.mode.value.title()} output is not implemented for {item.name!r}.",
                finding_context={
                    "object_id": item.id,
                    "kind": item.kind.value,
                    "layer_id": layer.id,
                    "layer_mode": layer.mode.value,
                },
            )
        if (
            layer.mode in {LayerMode.FILL, LayerMode.RASTER}
            and item.kind in {ObjectKind.PATH, ObjectKind.POLYGON}
        ):
            try:
                geometry = item.path_geometry()
            except (TypeError, ValueError):
                add(
                    "object.geometry_invalid",
                    PreflightSeverity.BLOCKER,
                    "Invalid vector geometry",
                    f"Object {item.name!r} has no inspectable vector paths.",
                    finding_context={"object_id": item.id},
                )
            else:
                if any(not subpath.closed for subpath in geometry.subpaths):
                    add(
                        "object.closed_geometry_required",
                        PreflightSeverity.BLOCKER,
                        "Closed vector geometry required",
                        f"{layer.mode.value.title()} output requires closed paths for {item.name!r}.",
                        finding_context={"object_id": item.id, "layer_id": layer.id},
                    )

        if transform_valid and isinstance(item.kind, ObjectKind):
            bounds = _simple_object_bounds(item)
            if bounds is None:
                deferred_bounds += 1
            else:
                simple_bounds.append((item, bounds))
                if not _inside(document.work_area, bounds):
                    add(
                        "geometry.local_bounds_outside_work_area",
                        PreflightSeverity.BLOCKER,
                        "Object exceeds local work area",
                        f"Exact simple-shape bounds for {item.name!r} leave the project work area.",
                        finding_context={
                            "object_id": item.id,
                            "object_bounds_mm": _bounds_tuple(bounds),
                            "work_area_mm": _bounds_tuple(document.work_area),
                        },
                    )

    if not enabled_pairs:
        add(
            "output.none_enabled",
            PreflightSeverity.BLOCKER,
            "No enabled output geometry",
            "The project contains no visible output object on an enabled visible layer.",
        )

    enabled_geometry_layer_ids = {
        layer.id
        for item, layer in enabled_pairs
        if _object_has_potential_nonzero_output(item, layer, document, context) is True
    }
    powered_air_assist_layers: list[OperationLayer] = []
    if power_max_valid:
        for layer in layers:
            power_percent = _finite_number(layer.power_percent)
            if (
                layer.id in enabled_geometry_layer_ids
                and layer.air_assist is True
                and power_percent is not None
                and 0.0 <= power_percent <= 100.0
                and int(
                    round(
                        context.controller_power_max
                        * power_percent
                        / 100.0
                    )
                )
                > 0
            ):
                powered_air_assist_layers.append(layer)
    if powered_air_assist_layers and context.air_assist_commands is None:
        add(
            "air_assist.output_unconfigured",
            PreflightSeverity.BLOCKER,
            "Air Assist output not configured",
            "Powered operations request Air Assist, but this machine has no "
            "configured Air Assist output. Configure it in Machine Manager "
            "before generating the job.",
            finding_context={
                "layer_ids": tuple(layer.id for layer in powered_air_assist_layers),
                "layer_names": tuple(
                    layer.name for layer in powered_air_assist_layers
                ),
            },
        )

    work_feed_limit = _finite_number(context.machine_max_work_feed_mm_min)
    if context.machine_max_work_feed_mm_min is not None and (
        work_feed_limit is None or work_feed_limit <= 0.0
    ):
        add(
            "context.work_feed_limit_invalid",
            PreflightSeverity.BLOCKER,
            "Invalid machine work-feed ceiling",
            "The configured machine work-feed ceiling must be positive and finite.",
        )
    elif work_feed_limit is not None:
        used_layer_ids: set[str] = set()
        for _item, layer in enabled_pairs:
            if layer.id in used_layer_ids:
                continue
            used_layer_ids.add(layer.id)
            speed = _finite_number(layer.speed_mm_min)
            if speed is not None and speed > work_feed_limit:
                add(
                    "layer.work_feed_exceeds_machine_limit",
                    PreflightSeverity.BLOCKER,
                    "Layer work feed exceeds machine ceiling",
                    f"Layer {layer.name!r} requests a work feed above the configured machine limit.",
                    finding_context={
                        "layer_id": layer.id,
                        "requested_mm_min": speed,
                        "limit_mm_min": work_feed_limit,
                    },
                )

    travel_feed_limit = _finite_number(context.machine_max_travel_feed_mm_min)
    planned_travel_feed = _finite_number(context.planned_travel_feed_mm_min)
    if context.machine_max_travel_feed_mm_min is not None and (
        travel_feed_limit is None or travel_feed_limit <= 0.0
    ):
        add(
            "context.travel_feed_limit_invalid",
            PreflightSeverity.BLOCKER,
            "Invalid machine travel-feed ceiling",
            "The configured machine travel-feed ceiling must be positive and finite.",
        )
    if context.planned_travel_feed_mm_min is not None and (
        planned_travel_feed is None or planned_travel_feed <= 0.0
    ):
        add(
            "context.travel_feed_invalid",
            PreflightSeverity.BLOCKER,
            "Invalid planned travel feed",
            "The configured generated-job travel feed must be positive and finite.",
        )
    if (
        enabled_pairs
        and travel_feed_limit is not None
        and travel_feed_limit > 0.0
        and planned_travel_feed is not None
        and planned_travel_feed > travel_feed_limit
    ):
        add(
            "travel.feed_exceeds_machine_limit",
            PreflightSeverity.BLOCKER,
            "Travel feed exceeds machine ceiling",
            "Generated rapid travel would exceed the configured machine limit.",
            finding_context={
                "requested_mm_min": planned_travel_feed,
                "limit_mm_min": travel_feed_limit,
            },
        )

    if simple_bounds:
        union = simple_bounds[0][1]
        for _item, bounds in simple_bounds[1:]:
            union = Bounds(
                min(union.x_min, bounds.x_min),
                min(union.y_min, bounds.y_min),
                max(union.x_max, bounds.x_max),
                max(union.y_max, bounds.y_max),
            )
        add(
            "geometry.simple_bounds_checked",
            PreflightSeverity.INFO,
            "Simple-shape bounds checked",
            "Exact local bounds were checked for supported simple shapes.",
            finding_context={
                "object_count": len(simple_bounds),
                "combined_bounds_mm": _bounds_tuple(union),
            },
        )
    if deferred_bounds:
        add(
            "geometry.complex_bounds_deferred",
            PreflightSeverity.INFO,
            "Complex geometry deferred",
            "Complex path bounds and placed/controller geometry remain authoritative in exact planning.",
            finding_context={"object_count": deferred_bounds},
        )

    raster_pairs = [
        (item, layer)
        for item, layer in enabled_pairs
        if layer.mode is LayerMode.RASTER and item.kind is ObjectKind.IMAGE
    ]
    raster_metadata: dict[str, RasterAssetMetadata] = {}
    aggregate_rows = 0
    aggregate_samples = 0
    aggregate_commands = 0
    for item, layer in raster_pairs:
        asset = item.geometry.get("asset")
        if not isinstance(asset, str) or not asset.strip():
            add(
                "raster.source_missing",
                PreflightSeverity.BLOCKER,
                "Raster source missing",
                f"Raster object {item.name!r} does not reference an image asset.",
                finding_context={"object_id": item.id},
            )
        else:
            source = str(Path(asset).expanduser().absolute())
            if source not in raster_metadata:
                try:
                    metadata = probe_raster_asset(source)
                except ValueError as exc:
                    add(
                        "raster.source_unavailable",
                        PreflightSeverity.BLOCKER,
                        "Raster source unavailable",
                        f"Raster source for {item.name!r} is not ready for planning.",
                        detail=str(exc),
                        finding_context={"object_id": item.id, "path": source},
                    )
                else:
                    raster_metadata[metadata.path] = metadata

        if (
            _valid_transform(item)[0]
            and _finite_number(layer.line_interval_mm) is not None
            and layer.line_interval_mm > 0.0
            and _finite_number(layer.scan_angle_deg) is not None
            and type(layer.passes) is int
            and layer.passes >= 1
        ):
            row_count, column_count = _image_scan_counts(item, layer)
            aggregate_rows += row_count * layer.passes
            aggregate_samples += row_count * column_count
            power_percent = _finite_number(layer.power_percent)
            overscan_percent = _finite_number(layer.overscan_percent)
            if (
                power_max_valid
                and power_percent is not None
                and 0.0 <= power_percent <= 100.0
                and overscan_percent is not None
                and 0.0 <= overscan_percent <= 100.0
            ):
                powered = (
                    int(
                        round(
                            context.controller_power_max
                            * power_percent
                            / 100.0
                        )
                    )
                    > 0
                )
                row_commands = 2 + (2 if overscan_percent > 0 else 0)
                if powered:
                    row_commands += 3
                aggregate_commands += row_count * layer.passes * row_commands

    aggregate_encoded = sum(
        metadata.encoded_bytes for metadata in raster_metadata.values()
    )
    aggregate_decoded = sum(
        metadata.decoded_bytes for metadata in raster_metadata.values()
    )
    if len(raster_metadata) > MAX_UNIQUE_RASTER_ASSETS:
        add(
            "raster.aggregate_assets_exceeded",
            PreflightSeverity.BLOCKER,
            "Too many raster sources",
            "The project exceeds the exact planner's unique raster-asset limit.",
            finding_context={
                "actual": len(raster_metadata),
                "limit": MAX_UNIQUE_RASTER_ASSETS,
            },
        )
    if aggregate_encoded > MAX_UNIQUE_RASTER_ENCODED_BYTES:
        add(
            "raster.aggregate_encoded_bytes_exceeded",
            PreflightSeverity.BLOCKER,
            "Raster encoded-byte budget exceeded",
            "The project exceeds the exact planner's aggregate encoded raster limit.",
            finding_context={
                "actual": aggregate_encoded,
                "limit": MAX_UNIQUE_RASTER_ENCODED_BYTES,
            },
        )
    if aggregate_decoded > MAX_UNIQUE_RASTER_DECODED_BYTES:
        add(
            "raster.aggregate_decoded_bytes_exceeded",
            PreflightSeverity.BLOCKER,
            "Raster decoded-byte budget exceeded",
            "The project exceeds the exact planner's aggregate decoded raster limit.",
            finding_context={
                "actual": aggregate_decoded,
                "limit": MAX_UNIQUE_RASTER_DECODED_BYTES,
            },
        )
    if aggregate_rows > MAX_RASTER_ROWS:
        add(
            "raster.aggregate_rows_exceeded",
            PreflightSeverity.BLOCKER,
            "Raster row budget exceeded",
            "Image raster output exceeds the exact planner's aggregate row limit.",
            finding_context={"actual": aggregate_rows, "limit": MAX_RASTER_ROWS},
        )
    if aggregate_samples > MAX_RASTER_SAMPLES:
        add(
            "raster.aggregate_samples_exceeded",
            PreflightSeverity.BLOCKER,
            "Raster sample budget exceeded",
            "Image raster output exceeds the exact planner's aggregate sample limit.",
            finding_context={
                "actual": aggregate_samples,
                "limit": MAX_RASTER_SAMPLES,
            },
        )
    if aggregate_commands >= MAX_STREAM_COMMANDS - STREAM_COMMAND_RESERVE:
        add(
            "raster.aggregate_commands_exceeded",
            PreflightSeverity.BLOCKER,
            "Raster command budget exceeded",
            "Image raster output reaches the exact planner's reserved stream-command limit.",
            finding_context={
                "actual": aggregate_commands,
                "limit": MAX_STREAM_COMMANDS - STREAM_COMMAND_RESERVE,
            },
        )
    if raster_metadata:
        add(
            "raster.sources_probed",
            PreflightSeverity.INFO,
            "Raster sources inspected",
            "Bounded raster headers are readable; exact payload identity and pixel decode remain deferred.",
            finding_context={
                "unique_sources": len(raster_metadata),
                "encoded_bytes": aggregate_encoded,
                "decoded_bytes": aggregate_decoded,
            },
        )

    if enabled_pairs and power_max_valid:
        power_percents = [
            _finite_number(layer.power_percent) for _item, layer in enabled_pairs
        ]
        output_powers = (
            [
                int(round(context.controller_power_max * value / 100.0))
                for value in power_percents
                if value is not None and 0.0 <= value <= 100.0
            ]
            if all(
                value is not None and 0.0 <= value <= 100.0
                for value in power_percents
            )
            else []
        )
        if output_powers and max(output_powers) <= 0:
            add(
                "output.zero_power",
                PreflightSeverity.WARNING,
                "All enabled output is zero power",
                "The project can be planned, but every enabled operation will keep the laser off.",
            )

    if not context.execution_ready:
        add(
            "execution.not_ready",
            PreflightSeverity.WARNING,
            "Execution is not currently ready",
            "Known runtime state is not ready for execution; exact start-time gates still apply.",
            detail=context.execution_unready_reason,
        )

    add(
        "planner.exact_checks_deferred",
        PreflightSeverity.INFO,
        "Exact planning checks remain authoritative",
        "Vector flattening, fill/raster construction, placement, correction, final bounds, and stream validation run only in the exact planner.",
    )
    return JobPreflightReport(tuple(findings))


__all__ = [
    "JobPreflightContext",
    "JobPreflightReport",
    "PreflightCounts",
    "PreflightFinding",
    "PreflightSeverity",
    "build_job_preflight_report",
]
