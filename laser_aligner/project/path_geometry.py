"""Validated, Qt-free native line and cubic path geometry.

The persisted representation is deliberately small and explicit.  Segment
starts are implicit: the first segment starts at its subpath's ``start`` and
each later segment starts at the preceding segment's ``to`` point.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from typing import Any, TypeAlias

NATIVE_PATH_FORMAT_VERSION = 1
MAX_NATIVE_PATH_SUBPATHS = 8_192
MAX_NATIVE_PATH_SEGMENTS = 100_000
MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT = 250_000
MAX_NATIVE_PATH_FLATTENED_POINTS = 250_000
MAX_NATIVE_PATH_SUBDIVISION_DEPTH = 18
MAX_NATIVE_PATH_COORDINATE_MAGNITUDE = 1_000_000.0
MAX_NATIVE_PATH_JSON_NESTING = 8

Point: TypeAlias = tuple[float, float]


class PathFillRule(str, Enum):
    EVENODD = "evenodd"
    NONZERO = "nonzero"


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _coordinate(value: object, label: str) -> float:
    number = _finite_number(value, label)
    if abs(number) > MAX_NATIVE_PATH_COORDINATE_MAGNITUDE:
        raise ValueError(
            f"{label} exceeds the native-path coordinate magnitude limit of "
            f"{MAX_NATIVE_PATH_COORDINATE_MAGNITUDE:g}"
        )
    return number


def _point(value: object, label: str) -> Point:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 2
    ):
        raise ValueError(f"{label} must be a two-number JSON array")
    return (
        _coordinate(value[0], f"{label}[0]"),
        _coordinate(value[1], f"{label}[1]"),
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _exact_keys(
    raw: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    missing = expected - set(raw)
    extra = set(raw) - expected
    if missing:
        raise ValueError(f"{label} is missing required field(s): {sorted(missing)}")
    if extra:
        raise ValueError(f"{label} contains unsupported field(s): {sorted(extra)}")


def _validate_json_nesting(value: object) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_NATIVE_PATH_JSON_NESTING:
            raise ValueError(
                "Native path JSON exceeds the maximum nesting depth of "
                f"{MAX_NATIVE_PATH_JSON_NESTING}"
            )
        if isinstance(current, Mapping):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


@dataclass(frozen=True, slots=True)
class PathLineSegment:
    to: Point

    def __post_init__(self) -> None:
        object.__setattr__(self, "to", _point(self.to, "line.to"))

    def to_dict(self) -> dict[str, object]:
        return {"type": "line", "to": [self.to[0], self.to[1]]}


@dataclass(frozen=True, slots=True)
class PathCubicSegment:
    control_1: Point
    control_2: Point
    to: Point

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "control_1",
            _point(self.control_1, "cubic.control_1"),
        )
        object.__setattr__(
            self,
            "control_2",
            _point(self.control_2, "cubic.control_2"),
        )
        object.__setattr__(self, "to", _point(self.to, "cubic.to"))

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "cubic",
            "control_1": [self.control_1[0], self.control_1[1]],
            "control_2": [self.control_2[0], self.control_2[1]],
            "to": [self.to[0], self.to[1]],
        }


PathSegment: TypeAlias = PathLineSegment | PathCubicSegment


def _segment_from_dict(raw: object, label: str) -> PathSegment:
    segment = _mapping(raw, label)
    segment_type = segment.get("type")
    if type(segment_type) is not str:
        raise ValueError(f"{label}.type must be a JSON string")
    if segment_type == "line":
        _exact_keys(segment, {"type", "to"}, label)
        return PathLineSegment(_point(segment["to"], f"{label}.to"))
    if segment_type == "cubic":
        _exact_keys(
            segment,
            {"type", "control_1", "control_2", "to"},
            label,
        )
        return PathCubicSegment(
            _point(segment["control_1"], f"{label}.control_1"),
            _point(segment["control_2"], f"{label}.control_2"),
            _point(segment["to"], f"{label}.to"),
        )
    raise ValueError(f"{label}.type must be 'line' or 'cubic'")


@dataclass(frozen=True, slots=True)
class PathSubpath:
    start: Point
    segments: tuple[PathSegment, ...]
    closed: bool = False

    def __post_init__(self) -> None:
        start = _point(self.start, "subpath.start")
        segments = tuple(self.segments)
        if not segments:
            raise ValueError("A native subpath requires at least one segment")
        if len(segments) > MAX_NATIVE_PATH_SEGMENTS:
            raise ValueError(
                "A native path contains more than "
                f"{MAX_NATIVE_PATH_SEGMENTS:,} segments; simplify the source artwork"
            )
        if any(
            not isinstance(segment, (PathLineSegment, PathCubicSegment))
            for segment in segments
        ):
            raise TypeError("Native subpath segments must be line or cubic segments")
        if type(self.closed) is not bool:
            raise ValueError("subpath.closed must be a JSON boolean")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "segments", segments)

    @classmethod
    def from_dict(cls, raw: object) -> PathSubpath:
        value = _mapping(raw, "subpath")
        _exact_keys(value, {"start", "closed", "segments"}, "subpath")
        if type(value["closed"]) is not bool:
            raise ValueError("subpath.closed must be a JSON boolean")
        segments = _array(value["segments"], "subpath.segments")
        if len(segments) > MAX_NATIVE_PATH_SEGMENTS:
            raise ValueError(
                "A native path contains more than "
                f"{MAX_NATIVE_PATH_SEGMENTS:,} segments; simplify the source artwork"
            )
        return cls(
            start=_point(value["start"], "subpath.start"),
            segments=tuple(
                _segment_from_dict(segment, f"subpath.segments[{index}]")
                for index, segment in enumerate(segments)
            ),
            closed=value["closed"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "start": [self.start[0], self.start[1]],
            "closed": self.closed,
            "segments": [segment.to_dict() for segment in self.segments],
        }


@dataclass(frozen=True, slots=True)
class NativePathGeometry:
    subpaths: tuple[PathSubpath, ...]
    fill_rule: PathFillRule = PathFillRule.EVENODD
    path_version: int = NATIVE_PATH_FORMAT_VERSION

    def __post_init__(self) -> None:
        subpaths = tuple(self.subpaths)
        if not subpaths:
            raise ValueError("Native path geometry requires at least one subpath")
        if len(subpaths) > MAX_NATIVE_PATH_SUBPATHS:
            raise ValueError(
                "Native path geometry contains more than "
                f"{MAX_NATIVE_PATH_SUBPATHS:,} subpaths; simplify the source artwork"
            )
        if any(not isinstance(subpath, PathSubpath) for subpath in subpaths):
            raise TypeError("Native path geometry requires validated PathSubpath values")
        if type(self.path_version) is not int or (
            self.path_version != NATIVE_PATH_FORMAT_VERSION
        ):
            raise ValueError(
                "Unsupported native path format version "
                f"{self.path_version!r}; expected {NATIVE_PATH_FORMAT_VERSION}"
            )
        try:
            fill_rule = (
                self.fill_rule
                if isinstance(self.fill_rule, PathFillRule)
                else PathFillRule(self.fill_rule)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported native path fill rule: {self.fill_rule!r}") from exc
        if sum(len(subpath.segments) for subpath in subpaths) > MAX_NATIVE_PATH_SEGMENTS:
            raise ValueError(
                "Native path geometry contains more than "
                f"{MAX_NATIVE_PATH_SEGMENTS:,} segments; simplify the source artwork"
            )
        object.__setattr__(self, "subpaths", subpaths)
        object.__setattr__(self, "fill_rule", fill_rule)

    @property
    def segment_count(self) -> int:
        return sum(len(subpath.segments) for subpath in self.subpaths)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> NativePathGeometry:
        _validate_json_nesting(raw)
        value = _mapping(raw, "native path geometry")
        _exact_keys(
            value,
            {"path_version", "fill_rule", "subpaths"},
            "native path geometry",
        )
        version = value["path_version"]
        if type(version) is not int:
            raise ValueError("native path path_version must be a JSON integer")
        fill_rule = value["fill_rule"]
        if type(fill_rule) is not str:
            raise ValueError("native path fill_rule must be a JSON string")
        subpaths = _array(value["subpaths"], "native path subpaths")
        if len(subpaths) > MAX_NATIVE_PATH_SUBPATHS:
            raise ValueError(
                "Native path geometry contains more than "
                f"{MAX_NATIVE_PATH_SUBPATHS:,} subpaths; simplify the source artwork"
            )
        parsed_subpaths: list[PathSubpath] = []
        total_segments = 0
        for index, raw_subpath in enumerate(subpaths):
            subpath_mapping = _mapping(raw_subpath, f"native path subpaths[{index}]")
            raw_segments = subpath_mapping.get("segments")
            if isinstance(raw_segments, list):
                total_segments += len(raw_segments)
                if total_segments > MAX_NATIVE_PATH_SEGMENTS:
                    raise ValueError(
                        "Native path geometry contains more than "
                        f"{MAX_NATIVE_PATH_SEGMENTS:,} segments; simplify the source artwork"
                    )
            parsed_subpaths.append(PathSubpath.from_dict(subpath_mapping))
        return cls(
            subpaths=tuple(parsed_subpaths),
            fill_rule=PathFillRule(fill_rule),
            path_version=version,
        )

    @classmethod
    def from_legacy_polylines(
        cls,
        polylines: Iterable[Mapping[str, Any]],
        *,
        fill_rule: PathFillRule = PathFillRule.EVENODD,
    ) -> NativePathGeometry:
        if isinstance(polylines, (str, bytes, bytearray, Mapping)):
            raise ValueError("Legacy path polylines must be an iterable of JSON objects")
        subpaths: list[PathSubpath] = []
        total_segments = 0
        for index, raw_line in enumerate(polylines):
            if index >= MAX_NATIVE_PATH_SUBPATHS:
                raise ValueError(
                    "Native path geometry contains more than "
                    f"{MAX_NATIVE_PATH_SUBPATHS:,} subpaths; simplify the source artwork"
                )
            line = _mapping(raw_line, f"legacy polyline[{index}]")
            points_raw = line.get("points")
            if (
                not isinstance(points_raw, Sequence)
                or isinstance(points_raw, (str, bytes, bytearray))
            ):
                raise ValueError(f"legacy polyline[{index}].points must be an array")
            closed = line.get("closed", False)
            if type(closed) is not bool:
                raise ValueError(f"legacy polyline[{index}].closed must be a JSON boolean")
            if len(points_raw) > MAX_NATIVE_PATH_SEGMENTS + 2:
                raise ValueError(
                    "Native path geometry contains more than "
                    f"{MAX_NATIVE_PATH_SEGMENTS:,} segments; simplify the source artwork"
                )
            points = [
                _point(point, f"legacy polyline[{index}].points[{point_index}]")
                for point_index, point in enumerate(points_raw)
            ]
            if len(points) < 2:
                raise ValueError("Each legacy polyline requires at least two points")
            if closed and len(points) > 2 and points[-1] == points[0]:
                points.pop()
            if len(points) < 2:
                raise ValueError("A closed legacy polyline has no usable segment")
            total_segments += len(points) - 1
            if total_segments > MAX_NATIVE_PATH_SEGMENTS:
                raise ValueError(
                    "Native path geometry contains more than "
                    f"{MAX_NATIVE_PATH_SEGMENTS:,} segments; simplify the source artwork"
                )
            subpaths.append(
                PathSubpath(
                    start=points[0],
                    segments=tuple(PathLineSegment(point) for point in points[1:]),
                    closed=closed,
                )
            )
        return cls(tuple(subpaths), fill_rule=fill_rule)

    def to_dict(self) -> dict[str, object]:
        return {
            "path_version": self.path_version,
            "fill_rule": self.fill_rule.value,
            "subpaths": [subpath.to_dict() for subpath in self.subpaths],
        }


@dataclass(frozen=True, slots=True)
class PathAffineTransform:
    """A two-dimensional affine transform.

    ``self.compose(other)`` returns a transform that applies ``other`` first
    and then applies ``self``.
    """

    m11: float = 1.0
    m12: float = 0.0
    m21: float = 0.0
    m22: float = 1.0
    dx: float = 0.0
    dy: float = 0.0

    def __post_init__(self) -> None:
        for name in ("m11", "m12", "m21", "m22", "dx", "dy"):
            object.__setattr__(self, name, _finite_number(getattr(self, name), name))

    @classmethod
    def from_components(
        cls,
        *,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        rotation_deg: float = 0.0,
        translate_x: float = 0.0,
        translate_y: float = 0.0,
    ) -> PathAffineTransform:
        sx = _finite_number(scale_x, "scale_x")
        sy = _finite_number(scale_y, "scale_y")
        angle = math.radians(_finite_number(rotation_deg, "rotation_deg"))
        cosine = math.cos(angle)
        sine = math.sin(angle)
        return cls(
            m11=cosine * sx,
            m12=-sine * sy,
            m21=sine * sx,
            m22=cosine * sy,
            dx=translate_x,
            dy=translate_y,
        )

    def apply(self, point: Sequence[object]) -> Point:
        x = _finite_number(point[0], "point.x")
        y = _finite_number(point[1], "point.y")
        transformed = (
            self.m11 * x + self.m12 * y + self.dx,
            self.m21 * x + self.m22 * y + self.dy,
        )
        if not all(math.isfinite(value) for value in transformed):
            raise ValueError("Affine transformation produced a non-finite point")
        return transformed

    def compose(self, other: PathAffineTransform) -> PathAffineTransform:
        if not isinstance(other, PathAffineTransform):
            raise TypeError("other must be a PathAffineTransform")
        return PathAffineTransform(
            m11=self.m11 * other.m11 + self.m12 * other.m21,
            m12=self.m11 * other.m12 + self.m12 * other.m22,
            m21=self.m21 * other.m11 + self.m22 * other.m21,
            m22=self.m21 * other.m12 + self.m22 * other.m22,
            dx=self.m11 * other.dx + self.m12 * other.dy + self.dx,
            dy=self.m21 * other.dx + self.m22 * other.dy + self.dy,
        )


def evaluate_cubic(start: Point, segment: PathCubicSegment, t: float) -> Point:
    if not isinstance(segment, PathCubicSegment):
        raise TypeError("segment must be a PathCubicSegment")
    p0 = _point(start, "cubic.start")
    parameter = _finite_number(t, "cubic parameter")
    if not 0.0 <= parameter <= 1.0:
        raise ValueError("cubic parameter must be between 0 and 1")
    inverse = 1.0 - parameter
    return (
        inverse**3 * p0[0]
        + 3.0 * inverse**2 * parameter * segment.control_1[0]
        + 3.0 * inverse * parameter**2 * segment.control_2[0]
        + parameter**3 * segment.to[0],
        inverse**3 * p0[1]
        + 3.0 * inverse**2 * parameter * segment.control_1[1]
        + 3.0 * inverse * parameter**2 * segment.control_2[1]
        + parameter**3 * segment.to[1],
    )


def split_cubic(
    start: Point,
    segment: PathCubicSegment,
    t: float = 0.5,
) -> tuple[PathCubicSegment, PathCubicSegment]:
    if not isinstance(segment, PathCubicSegment):
        raise TypeError("segment must be a PathCubicSegment")
    p0 = _point(start, "cubic.start")
    parameter = _finite_number(t, "cubic split parameter")
    if not 0.0 < parameter < 1.0:
        raise ValueError("cubic split parameter must be strictly between 0 and 1")

    def interpolate(first: Point, second: Point) -> Point:
        return (
            first[0] + (second[0] - first[0]) * parameter,
            first[1] + (second[1] - first[1]) * parameter,
        )

    p01 = interpolate(p0, segment.control_1)
    p12 = interpolate(segment.control_1, segment.control_2)
    p23 = interpolate(segment.control_2, segment.to)
    p012 = interpolate(p01, p12)
    p123 = interpolate(p12, p23)
    middle = interpolate(p012, p123)
    return (
        PathCubicSegment(p01, p012, middle),
        PathCubicSegment(p123, p23, segment.to),
    )


def quadratic_to_cubic(start: Point, control: Point, end: Point) -> PathCubicSegment:
    p0 = _point(start, "quadratic.start")
    p1 = _point(control, "quadratic.control")
    p2 = _point(end, "quadratic.end")
    return PathCubicSegment(
        (
            p0[0] + (2.0 / 3.0) * (p1[0] - p0[0]),
            p0[1] + (2.0 / 3.0) * (p1[1] - p0[1]),
        ),
        (
            p2[0] + (2.0 / 3.0) * (p1[0] - p2[0]),
            p2[1] + (2.0 / 3.0) * (p1[1] - p2[1]),
        ),
        p2,
    )


def transform_native_path(
    geometry: NativePathGeometry,
    transform: PathAffineTransform,
) -> NativePathGeometry:
    if not isinstance(geometry, NativePathGeometry):
        raise TypeError("geometry must be NativePathGeometry")
    if not isinstance(transform, PathAffineTransform):
        raise TypeError("transform must be PathAffineTransform")
    subpaths: list[PathSubpath] = []
    for subpath in geometry.subpaths:
        segments: list[PathSegment] = []
        for segment in subpath.segments:
            if isinstance(segment, PathLineSegment):
                segments.append(PathLineSegment(transform.apply(segment.to)))
            else:
                segments.append(
                    PathCubicSegment(
                        transform.apply(segment.control_1),
                        transform.apply(segment.control_2),
                        transform.apply(segment.to),
                    )
                )
        subpaths.append(
            PathSubpath(
                transform.apply(subpath.start),
                tuple(segments),
                subpath.closed,
            )
        )
    return NativePathGeometry(
        tuple(subpaths),
        fill_rule=geometry.fill_rule,
        path_version=geometry.path_version,
    )


def _derivative_roots(p0: float, p1: float, p2: float, p3: float) -> tuple[float, ...]:
    a = -p0 + 3.0 * p1 - 3.0 * p2 + p3
    b = 2.0 * (p0 - 2.0 * p1 + p2)
    c = p1 - p0
    scale = max(1.0, abs(a), abs(b), abs(c))
    epsilon = 1e-14 * scale
    if abs(a) <= epsilon:
        if abs(b) <= epsilon:
            return ()
        root = -c / b
        return (root,) if 0.0 < root < 1.0 else ()
    discriminant = b * b - 4.0 * a * c
    if discriminant < -epsilon * scale:
        return ()
    square_root = math.sqrt(max(0.0, discriminant))
    roots = ((-b - square_root) / (2.0 * a), (-b + square_root) / (2.0 * a))
    return tuple(sorted({root for root in roots if 0.0 < root < 1.0}))


def native_path_bounds(
    geometry: NativePathGeometry,
    transform: PathAffineTransform | None = None,
) -> tuple[float, float, float, float]:
    if not isinstance(geometry, NativePathGeometry):
        raise TypeError("geometry must be NativePathGeometry")
    affine = transform or PathAffineTransform()
    xs: list[float] = []
    ys: list[float] = []
    for subpath in geometry.subpaths:
        current = affine.apply(subpath.start)
        xs.append(current[0])
        ys.append(current[1])
        for segment in subpath.segments:
            if isinstance(segment, PathLineSegment):
                current = affine.apply(segment.to)
                xs.append(current[0])
                ys.append(current[1])
                continue
            transformed = PathCubicSegment(
                affine.apply(segment.control_1),
                affine.apply(segment.control_2),
                affine.apply(segment.to),
            )
            candidates = {0.0, 1.0}
            candidates.update(
                _derivative_roots(
                    current[0],
                    transformed.control_1[0],
                    transformed.control_2[0],
                    transformed.to[0],
                )
            )
            candidates.update(
                _derivative_roots(
                    current[1],
                    transformed.control_1[1],
                    transformed.control_2[1],
                    transformed.to[1],
                )
            )
            for parameter in sorted(candidates):
                point = evaluate_cubic(current, transformed, parameter)
                xs.append(point[0])
                ys.append(point[1])
            current = transformed.to
    return min(xs), min(ys), max(xs), max(ys)


def _distance_to_chord(point: Point, start: Point, end: Point) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-30:
        return math.dist(point, start)
    ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator
    ratio = max(0.0, min(1.0, ratio))
    projection = (start[0] + ratio * dx, start[1] + ratio * dy)
    return math.dist(point, projection)


def _split_cubic_points(
    start: Point,
    control_1: Point,
    control_2: Point,
    end: Point,
) -> tuple[tuple[Point, Point, Point, Point], tuple[Point, Point, Point, Point]]:
    def midpoint(first: Point, second: Point) -> Point:
        return ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)

    p01 = midpoint(start, control_1)
    p12 = midpoint(control_1, control_2)
    p23 = midpoint(control_2, end)
    p012 = midpoint(p01, p12)
    p123 = midpoint(p12, p23)
    middle = midpoint(p012, p123)
    return (
        (start, p01, p012, middle),
        (middle, p123, p23, end),
    )


def flatten_native_path(
    geometry: NativePathGeometry,
    tolerance_mm: float,
    *,
    transform: PathAffineTransform | None = None,
    max_points: int = MAX_NATIVE_PATH_FLATTENED_POINTS,
    max_depth: int = MAX_NATIVE_PATH_SUBDIVISION_DEPTH,
) -> tuple[tuple[Point, ...], ...]:
    """Flatten after applying ``transform`` so tolerance is measured physically.

    Closed subpaths include the repeated start point in their returned tuple.
    """

    if not isinstance(geometry, NativePathGeometry):
        raise TypeError("geometry must be NativePathGeometry")
    tolerance = _finite_number(tolerance_mm, "native path flatten tolerance")
    if tolerance <= 0.0:
        raise ValueError("native path flatten tolerance must be positive")
    if type(max_points) is not int or not 1 <= max_points <= MAX_NATIVE_PATH_FLATTENED_POINTS:
        raise ValueError(
            "max_points must be an integer from 1 through "
            f"{MAX_NATIVE_PATH_FLATTENED_POINTS:,}"
        )
    if type(max_depth) is not int or not 0 <= max_depth <= MAX_NATIVE_PATH_SUBDIVISION_DEPTH:
        raise ValueError(
            "max_depth must be an integer from 0 through "
            f"{MAX_NATIVE_PATH_SUBDIVISION_DEPTH}"
        )
    affine = transform or PathAffineTransform()
    emitted = 0

    def append(output: list[Point], point: Point) -> None:
        nonlocal emitted
        if emitted >= max_points:
            raise ValueError(
                "Native path flattening exceeds the bounded point limit; simplify "
                "the source artwork"
            )
        output.append(point)
        emitted += 1

    def flatten_cubic_points(
        start: Point,
        control_1: Point,
        control_2: Point,
        end: Point,
        output: list[Point],
        depth: int,
    ) -> None:
        flatness = max(
            _distance_to_chord(control_1, start, end),
            _distance_to_chord(control_2, start, end),
        )
        if flatness <= tolerance:
            append(output, end)
            return
        if depth >= max_depth:
            raise ValueError(
                "Native cubic could not meet the flattening tolerance within the "
                "bounded subdivision depth; simplify the source artwork"
            )
        first, second = _split_cubic_points(start, control_1, control_2, end)
        flatten_cubic_points(*first, output, depth + 1)
        flatten_cubic_points(*second, output, depth + 1)

    flattened: list[tuple[Point, ...]] = []
    for subpath in geometry.subpaths:
        start = affine.apply(subpath.start)
        current = start
        output: list[Point] = []
        append(output, start)
        for segment in subpath.segments:
            if isinstance(segment, PathLineSegment):
                current = affine.apply(segment.to)
                append(output, current)
            else:
                control_1 = affine.apply(segment.control_1)
                control_2 = affine.apply(segment.control_2)
                end = affine.apply(segment.to)
                flatten_cubic_points(
                    current,
                    control_1,
                    control_2,
                    end,
                    output,
                    0,
                )
                current = end
        if subpath.closed and output[-1] != start:
            append(output, start)
        flattened.append(tuple(output))
    return tuple(flattened)


def reverse_subpath(subpath: PathSubpath) -> PathSubpath:
    if not isinstance(subpath, PathSubpath):
        raise TypeError("subpath must be a PathSubpath")
    starts: list[Point] = []
    current = subpath.start
    for segment in subpath.segments:
        starts.append(current)
        current = segment.to
    reversed_segments: list[PathSegment] = []
    for start, segment in reversed(
        list(zip(starts, subpath.segments, strict=True))
    ):
        if isinstance(segment, PathLineSegment):
            reversed_segments.append(PathLineSegment(start))
        else:
            reversed_segments.append(
                PathCubicSegment(segment.control_2, segment.control_1, start)
            )
    return PathSubpath(current, tuple(reversed_segments), subpath.closed)


__all__ = [
    "MAX_NATIVE_PATH_COORDINATE_MAGNITUDE",
    "MAX_NATIVE_PATH_FLATTENED_POINTS",
    "MAX_NATIVE_PATH_JSON_NESTING",
    "MAX_NATIVE_PATH_SEGMENTS",
    "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT",
    "MAX_NATIVE_PATH_SUBDIVISION_DEPTH",
    "MAX_NATIVE_PATH_SUBPATHS",
    "NATIVE_PATH_FORMAT_VERSION",
    "NativePathGeometry",
    "PathAffineTransform",
    "PathCubicSegment",
    "PathFillRule",
    "PathLineSegment",
    "PathSegment",
    "PathSubpath",
    "Point",
    "evaluate_cubic",
    "flatten_native_path",
    "native_path_bounds",
    "quadratic_to_cubic",
    "reverse_subpath",
    "split_cubic",
    "transform_native_path",
]
