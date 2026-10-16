"""Explicit raster surface and independently verified carriage-travel authority.

The directive is application metadata, never a controller instruction. Its digest
binds the exact physical verification record selected at preparation time.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from .errors import SafetyError
from .geometry.polygon import convex_polygon_contains_normalized, normalize_convex_polygon
from .storage import strict_json_loads

PREFIX = "E3RASTER "
CAPABILITY = "pi-raster-travel-envelope-v1"
SURFACE_CAPABILITY = "pi-raster-surface-travel-v2"
COORDINATE_REFERENCE = "homed_machine_xy_zero_work_offsets"


def _number(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{label} must be SHA-256")
    return value


def _point(value: object, label: str) -> tuple[float, float]:
    if type(value) not in (tuple, list) or len(value) != 2:
        raise ValueError(f"{label} must contain exactly X and Y")
    return _number(value[0], label), _number(value[1], label)


def _four_corners(value: object, label: str) -> tuple[tuple[float, float], ...]:
    if type(value) not in (tuple, list) or len(value) != 4:
        raise ValueError(f"{label} must have four corners")
    points = tuple(_point(point, label) for point in value)
    for a, b in zip(points, points[1:] + points[:1], strict=True):
        ex, ey = b[0] - a[0], b[1] - a[1]
        if not math.isfinite(math.hypot(ex, ey)) or any(
            not math.isfinite(ex * (p[1] - a[1]) - ey * (p[0] - a[0])) for p in points
        ):
            raise ValueError(f"{label} geometry exceeds finite numeric precision")
    return normalize_convex_polygon(points, label=label)


def _surface_contains(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    """Retain the existing 1e-6-mm tolerance and reject arithmetic overflow."""
    x, y = point
    for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        ex, ey = b[0] - a[0], b[1] - a[1]
        cross = ex * (y - a[1]) - ey * (x - a[0])
        if not math.isfinite(cross) or -cross / math.hypot(ex, ey) > 1e-6:
            return False
    return True


@dataclass(frozen=True, slots=True)
class VerifiedSurfaceTravel:
    """Operator-confirmed surface travel bound to one beam map and head offset."""

    beam_polygon_mm: tuple[tuple[float, float], ...]
    spot_offset_mm: tuple[float, float]
    coordinate_frame_digest: str
    verified: bool
    confirmed_at: str
    evidence: str
    _carriage_polygon_mm: tuple[tuple[float, float], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "beam_polygon_mm", _four_corners(self.beam_polygon_mm, "Verified travel surface"))
        object.__setattr__(self, "spot_offset_mm", _point(self.spot_offset_mm, "Verified surface spot offset"))
        _digest(self.coordinate_frame_digest, "Verified surface coordinate-frame digest")
        if type(self.verified) is not bool:
            raise ValueError("Verified surface verified must be an exact boolean")
        if type(self.confirmed_at) is not str:
            raise ValueError("Verified surface confirmed_at must be an ISO YYYY-MM-DD date")
        try:
            parsed = date.fromisoformat(self.confirmed_at)
        except ValueError as exc:
            raise ValueError("Verified surface confirmed_at must be an ISO YYYY-MM-DD date") from exc
        if parsed.isoformat() != self.confirmed_at:
            raise ValueError("Verified surface confirmed_at must be an ISO YYYY-MM-DD date")
        if type(self.evidence) is not str or not self.evidence.strip() or len(self.evidence) > 2048:
            raise ValueError("Verified surface evidence must be a nonempty string of at most 2048 characters")
        dx, dy = self.spot_offset_mm
        object.__setattr__(self, "_carriage_polygon_mm", _four_corners(
            tuple((x - dx, y - dy) for x, y in self.beam_polygon_mm), "Verified surface carriage polygon"))

    @classmethod
    def from_dict(cls, value: object) -> VerifiedSurfaceTravel:
        if type(value) is cls:
            value = value.to_dict()
        fields = {name for name, item in cls.__dataclass_fields__.items() if item.init}
        if type(value) is not dict or set(value) != fields:
            raise ValueError("Verified surface requires exact beam_polygon_mm, spot_offset_mm, coordinate_frame_digest, verified, confirmed_at and evidence fields")
        return cls(**value)

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name, item in self.__dataclass_fields__.items() if item.init}

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    @property
    def carriage_polygon_mm(self) -> tuple[tuple[float, float], ...]:
        return self._carriage_polygon_mm

    def require_match(self, engraving_polygon_mm: object, spot_offset_mm: object,
                      coordinate_frame_digest: str | None = None) -> None:
        try:
            current = VerifiedSurfaceTravel.from_dict(self)
            polygon = _four_corners(engraving_polygon_mm, "Calibrated engraving surface")
            offset = _point(spot_offset_mm, "Current laser spot offset")
        except (TypeError, ValueError) as exc:
            raise SafetyError(str(exc)) from exc
        if not current.verified:
            raise SafetyError("Surface travel requires an explicitly verified surface record")
        if polygon != current.beam_polygon_mm:
            raise SafetyError("Verified surface travel does not match the current calibrated engraving surface")
        if offset != current.spot_offset_mm:
            raise SafetyError("Verified surface travel does not match the current laser spot offset")
        if coordinate_frame_digest is not None and coordinate_frame_digest != current.coordinate_frame_digest:
            raise SafetyError("Verified surface travel coordinate-frame digest is stale")


def _finite_travel_point(point: object, label: str) -> tuple[float, float]:
    try:
        x, y = point
        if isinstance(x, bool) or isinstance(y, bool) or not math.isfinite(x) or not math.isfinite(y):
            raise ValueError
        return float(x), float(y)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SafetyError(f"{label}: machine carriage travel point must be finite") from exc


def _segment_interval(start: tuple[float, float], end: tuple[float, float],
                      polygon: tuple[tuple[float, float], ...], tolerance_mm: float) -> tuple[float, float] | None:
    """Clip a straight segment against all CCW half-planes, without sampling."""
    low, high = 0.0, 1.0
    dx, dy = end[0] - start[0], end[1] - start[1]
    for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
        ex, ey = b[0] - a[0], b[1] - a[1]
        value = ex * (start[1] - a[1]) - ey * (start[0] - a[0]) + tolerance_mm * math.hypot(ex, ey)
        delta = ex * dy - ey * dx
        if not math.isfinite(value) or not math.isfinite(delta):
            return None
        if delta == 0.0:
            if value < 0.0:
                return None
            continue
        crossing = -value / delta
        if delta > 0.0:
            low = max(low, crossing)
        else:
            high = min(high, crossing)
        if low > high:
            return None
    return low, high


def _require_travel_point(point: object, bounds: tuple[float, float, float, float],
                          surface: tuple[tuple[float, float], ...] | None, label: str) -> None:
    x, y = _finite_travel_point(point, label)
    xmin, xmax, ymin, ymax = bounds
    if xmin <= x <= xmax and ymin <= y <= ymax:
        return
    if surface is not None and _surface_contains((x, y), surface):
        return
    if surface is not None:
        raise SafetyError(f"{label}: machine carriage travel point X{x:.4f} Y{y:.4f} is outside the verified rectangle and surface travel polygon")
    extra = (max(0.0, xmin - x), max(0.0, x - xmax), max(0.0, ymin - y), max(0.0, y - ymax))
    clearance = ", ".join(f"{side} {distance:.4f} mm" for side, distance in zip(("X−", "X+", "Y−", "Y+"), extra, strict=True) if distance)
    raise SafetyError(f"{label}: machine carriage travel point X{x:.4f} Y{y:.4f} exceeds verified travel X{xmin:g}..{xmax:g}, Y{ymin:g}..{ymax:g}; extra clearance required: {clearance}")


def _require_travel_segment(start: object, end: object, bounds: tuple[float, float, float, float],
                            surface: tuple[tuple[float, float], ...] | None, label: str) -> None:
    first, last = _finite_travel_point(start, label), _finite_travel_point(end, label)
    xmin, xmax, ymin, ymax = bounds
    if (xmin <= first[0] <= xmax and ymin <= first[1] <= ymax
            and xmin <= last[0] <= xmax and ymin <= last[1] <= ymax):
        return
    if (surface is not None and _surface_contains(first, surface)
            and _surface_contains(last, surface)):
        return
    _require_travel_point(first, bounds, surface, label)
    _require_travel_point(last, bounds, surface, label)
    if surface is None:
        return  # The legacy rectangle is convex and both endpoints fit exactly.
    rectangle = ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))
    intervals = [interval for polygon, tolerance in ((rectangle, 0.0), (surface, 1e-6))
                 if (interval := _segment_interval(first, last, polygon, tolerance)) is not None]
    covered = 0.0
    for low, high in sorted(intervals):
        if low > covered:
            break
        covered = max(covered, high)
        if covered >= 1.0:
            return
    raise SafetyError(f"{label}: machine carriage travel segment crosses a gap outside the verified rectangle and surface travel polygon")


def _intersection_vertices(bounds: tuple[float, float, float, float],
                           surface: tuple[tuple[float, float], ...]) -> list[tuple[float, float]]:
    """Clip the surface to the exact rectangle; never form its convex hull."""
    xmin, xmax, ymin, ymax = bounds
    vertices = list(surface)
    for axis, limit, positive in ((0, xmin, True), (0, xmax, False), (1, ymin, True), (1, ymax, False)):
        if not vertices:
            break
        output: list[tuple[float, float]] = []
        previous = vertices[-1]
        previous_inside = previous[axis] >= limit if positive else previous[axis] <= limit
        for current in vertices:
            current_inside = current[axis] >= limit if positive else current[axis] <= limit
            if current_inside != previous_inside:
                fraction = (limit - previous[axis]) / (current[axis] - previous[axis])
                crossing = [previous[i] + fraction * (current[i] - previous[i]) for i in (0, 1)]
                crossing[axis] = limit
                output.append((crossing[0], crossing[1]))
            if current_inside:
                output.append(current)
            previous, previous_inside = current, current_inside
        vertices = output
    return vertices


def _travel_route(start: object, end: object, bounds: tuple[float, float, float, float],
                  surface: tuple[tuple[float, float], ...] | None, label: str) -> tuple[tuple[float, float], ...]:
    first, last = _finite_travel_point(start, label), _finite_travel_point(end, label)
    _require_travel_point(first, bounds, surface, label)
    _require_travel_point(last, bounds, surface, label)
    try:
        _require_travel_segment(first, last, bounds, surface, label)
        return (last,)
    except SafetyError:
        if surface is None:
            raise
    vertices = _intersection_vertices(bounds, surface)
    if not vertices:
        raise SafetyError(f"{label}: verified travel regions do not intersect; no continuous travel route is available")
    # A convex combination is inside this intersection. Select a waypoint on the
    # actual 0.001-mm output lattice, and prove its membership after rounding.
    center = tuple(math.fsum(point[i] for point in vertices) / len(vertices) for i in (0, 1))
    candidates = [center]
    candidates.extend(tuple((center[i] + point[i]) / 2.0 for i in (0, 1)) for point in vertices)
    candidates.extend(vertices)
    xmin, xmax, ymin, ymax = bounds
    seen: set[tuple[float, float]] = set()
    for candidate in candidates:
        rounded = (round(candidate[0], 3), round(candidate[1], 3))
        nearby = [rounded]
        nearby.extend((x / 1000.0, y / 1000.0)
                      for x in (math.floor(candidate[0] * 1000.0), math.ceil(candidate[0] * 1000.0))
                      for y in (math.floor(candidate[1] * 1000.0), math.ceil(candidate[1] * 1000.0)))
        for waypoint in nearby:
            if waypoint in seen:
                continue
            seen.add(waypoint)
            if not (xmin <= waypoint[0] <= xmax and ymin <= waypoint[1] <= ymax
                    and _surface_contains(waypoint, surface)):
                continue
            _require_travel_segment(first, waypoint, bounds, surface, label)
            _require_travel_segment(waypoint, last, bounds, surface, label)
            return waypoint, last
    raise SafetyError(f"{label}: verified travel intersection has no supported rounded waypoint; no travel route was generated")


@dataclass(frozen=True, slots=True)
class RasterTravelEnvelope:
    """Carriage rectangle in homed machine mm; evidence is operator recorded."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float
    verified: bool
    controller: str
    firmware: str
    configuration: str
    reference: str
    tested_at: str
    surface: VerifiedSurfaceTravel | None = None

    def __post_init__(self) -> None:
        for key in ("x_min", "x_max", "y_min", "y_max"):
            object.__setattr__(self, key, _number(getattr(self, key), key))
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("Raster travel envelope minima must be less than maxima")
        if type(self.verified) is not bool:
            raise ValueError("Raster travel verified must be an exact boolean")
        for key in ("controller", "firmware", "configuration", "reference", "tested_at"):
            value = getattr(self, key)
            if type(value) is not str or not value.strip() or len(value) > 512:
                raise ValueError(f"Raster travel {key} must be a nonempty string of at most 512 characters")
        if self.reference != COORDINATE_REFERENCE:
            raise ValueError(f"Raster travel reference must be {COORDINATE_REFERENCE}")
        try:
            parsed_date = date.fromisoformat(self.tested_at)
        except ValueError as exc:
            raise ValueError("Raster travel tested_at must be an ISO YYYY-MM-DD physical-test date") from exc
        if parsed_date.isoformat() != self.tested_at:
            raise ValueError("Raster travel tested_at must be an ISO YYYY-MM-DD physical-test date")
        if self.surface is not None:
            object.__setattr__(self, "surface", VerifiedSurfaceTravel.from_dict(self.surface))

    @property
    def bounds_mm(self) -> tuple[float, float, float, float]:
        return self.x_min, self.x_max, self.y_min, self.y_max

    def to_dict(self) -> dict[str, object]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "surface"}
        if self.surface is not None:
            result["surface"] = self.surface.to_dict()
        return result

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    def contains(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max

    def require_travel_point(self, point: tuple[float, float], *, label: str) -> None:
        self._require_verified()
        _require_travel_point(point, self.bounds_mm,
                              None if self.surface is None else self.surface.carriage_polygon_mm, label)

    def require_travel_segment(self, start: tuple[float, float], end: tuple[float, float], *, label: str) -> None:
        self._require_verified()
        _require_travel_segment(start, end, self.bounds_mm,
                                None if self.surface is None else self.surface.carriage_polygon_mm, label)

    def travel_route(self, start: tuple[float, float], end: tuple[float, float], *, label: str) -> tuple[tuple[float, float], ...]:
        self._require_verified()
        return _travel_route(start, end, self.bounds_mm,
                             None if self.surface is None else self.surface.carriage_polygon_mm, label)

    def _require_verified(self) -> None:
        if self.verified is not True:
            raise SafetyError("Raster travel requires a verified machine carriage envelope")
        if self.surface is not None and self.surface.verified is not True:
            raise SafetyError("Surface travel requires an explicitly verified surface record")


def parse_raster_travel_envelope(value: object) -> RasterTravelEnvelope | None:
    if value is None:
        return None
    if type(value) is RasterTravelEnvelope:
        # Reconstruct to defend against mutation via object.__setattr__.
        return RasterTravelEnvelope(**value.to_dict())
    fields = set(RasterTravelEnvelope.__dataclass_fields__) - {"surface"}
    if type(value) is not dict or set(value) not in (fields, fields | {"surface"}):
        raise ValueError("machine.raster_travel_envelope requires exact bounds, verified, controller, firmware, configuration, reference and tested_at fields")
    return RasterTravelEnvelope(**value)


def require_verified_raster_travel(value: object) -> RasterTravelEnvelope:
    try:
        envelope = parse_raster_travel_envelope(value)
    except (TypeError, ValueError) as exc:
        raise SafetyError(str(exc)) from exc
    if envelope is None or not envelope.verified:
        raise SafetyError("Raster travel requires a separately configured verified machine.raster_travel_envelope with recorded controller, firmware, configuration and physical-test evidence")
    if envelope.surface is not None and not envelope.surface.verified:
        raise SafetyError("Surface travel requires an explicitly verified surface record")
    return envelope


@dataclass(frozen=True, slots=True)
class RasterBoundary:
    engraving_polygon_mm: tuple[tuple[float, float], ...]
    travel_bounds_mm: tuple[float, float, float, float]
    travel_envelope_digest: str
    travel_surface_polygon_mm: tuple[tuple[float, float], ...] | None = None
    surface_travel_digest: str | None = None

    def __post_init__(self) -> None:
        if (self.travel_surface_polygon_mm is None) != (self.surface_travel_digest is None):
            raise ValueError("Surface boundary requires both travel polygon and verification digest")
        if self.travel_surface_polygon_mm is not None:
            object.__setattr__(self, "travel_surface_polygon_mm", _four_corners(
                self.travel_surface_polygon_mm, "Verified surface carriage polygon"))
            _digest(self.surface_travel_digest, "Surface travel digest")

    @property
    def directive(self) -> str:
        payload = {
            "schema": 1 if self.travel_surface_polygon_mm is None else 2,
            "engraving_polygon_mm": self.engraving_polygon_mm,
            "travel_bounds_mm": self.travel_bounds_mm,
            "travel_envelope_digest": self.travel_envelope_digest,
        }
        if self.travel_surface_polygon_mm is not None:
            payload.update(travel_surface_polygon_mm=self.travel_surface_polygon_mm,
                           surface_travel_digest=self.surface_travel_digest)
        return PREFIX + _canonical(payload)

    def require_travel_point(self, point: tuple[float, float], *, label: str) -> None:
        _require_travel_point(point, self.travel_bounds_mm, self.travel_surface_polygon_mm, label)

    def require_travel_segment(self, start: tuple[float, float], end: tuple[float, float], *, label: str) -> None:
        _require_travel_segment(start, end, self.travel_bounds_mm, self.travel_surface_polygon_mm, label)

    def travel_route(self, start: tuple[float, float], end: tuple[float, float], *, label: str) -> tuple[tuple[float, float], ...]:
        return _travel_route(start, end, self.travel_bounds_mm, self.travel_surface_polygon_mm, label)

    def require_matches_envelope(self, envelope: RasterTravelEnvelope, spot_offset_mm: object) -> None:
        verified = require_verified_raster_travel(envelope)
        if self.travel_bounds_mm != verified.bounds_mm or self.travel_envelope_digest != verified.digest:
            raise SafetyError("Raster travel configuration or physical verification changed after preparation; generate the job again")
        if verified.surface is None:
            if self.travel_surface_polygon_mm is not None or self.surface_travel_digest is not None:
                raise SafetyError("Raster boundary declares unconfigured verified surface travel")
            return
        surface = verified.surface
        surface.require_match(self.engraving_polygon_mm, spot_offset_mm)
        if (self.travel_surface_polygon_mm != surface.carriage_polygon_mm
                or self.surface_travel_digest != surface.digest):
            raise SafetyError("Raster boundary does not match the current verified surface travel record")

    def require_powered_point(self, point: tuple[float, float], *, label: str) -> None:
        if not convex_polygon_contains_normalized(point, self.engraving_polygon_mm):
            # Signed normal distances retain useful clearance for rotated surfaces.
            polygon = self.engraving_polygon_mm
            signed_area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True))
            orientation = 1.0 if signed_area > 0 else -1.0
            excess = max(0.0, *(
                -orientation * ((b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])) / math.dist(a, b)
                for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True)
            ))
            raise SafetyError(f"{label}: powered beam X{point[0]:.4f} Y{point[1]:.4f} lies outside the calibrated engraving surface by {excess:.4f} mm; move or resize the engraving inside the surface")


def parse_raster_boundary(line: str) -> RasterBoundary | None:
    if not line.startswith("E3RASTER"):
        return None
    if not line.startswith(PREFIX) or len(line) > 4096:
        raise SafetyError("Malformed E3RASTER boundary directive")
    try:
        raw = strict_json_loads(line[len(PREFIX):])
        base_fields = {"schema", "engraving_polygon_mm", "travel_bounds_mm", "travel_envelope_digest"}
        if type(raw) is not dict or type(raw.get("schema")) is not int or raw["schema"] not in (1, 2):
            raise ValueError("Unsupported raster boundary schema or fields")
        fields = base_fields if raw["schema"] == 1 else base_fields | {"travel_surface_polygon_mm", "surface_travel_digest"}
        if set(raw) != fields:
            raise ValueError("Unsupported raster boundary schema or fields")
        polygon = _four_corners(raw["engraving_polygon_mm"], "Raster engraving surface")
        bounds_raw = raw["travel_bounds_mm"]
        if type(bounds_raw) is not list or len(bounds_raw) != 4:
            raise ValueError("Raster travel bounds must contain four numbers")
        bounds = tuple(_number(value, "Raster travel bound") for value in bounds_raw)
        if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            raise ValueError("Raster travel bounds are empty")
        digest = _digest(raw["travel_envelope_digest"], "Raster travel envelope digest")
        if raw["schema"] == 1:
            return RasterBoundary(polygon, bounds, digest)
        surface_polygon = _four_corners(raw["travel_surface_polygon_mm"], "Verified surface carriage polygon")
        surface_digest = _digest(raw["surface_travel_digest"], "Surface travel digest")
        return RasterBoundary(polygon, bounds, digest, surface_polygon, surface_digest)
    except (TypeError, ValueError, KeyError) as exc:
        raise SafetyError(f"Invalid raster boundary directive: {exc}") from exc


def program_raster_boundary(lines: Iterable[str]) -> RasterBoundary | None:
    result = None
    for line in lines:
        boundary = parse_raster_boundary(line.strip())
        if boundary is not None:
            if result is not None:
                raise SafetyError("A program may contain only one E3RASTER boundary directive")
            result = boundary
    return result


def raster_boundary_directive(engraving_polygon_mm: object, envelope: RasterTravelEnvelope,
                              *, spot_offset_mm: object = None,
                              coordinate_frame_digest: str | None = None) -> str:
    verified = require_verified_raster_travel(envelope)
    polygon = _four_corners(engraving_polygon_mm, "Raster engraving surface")
    if verified.surface is not None:
        surface = verified.surface
        surface.require_match(polygon, surface.spot_offset_mm if spot_offset_mm is None else spot_offset_mm,
                              coordinate_frame_digest)
        return RasterBoundary(polygon, verified.bounds_mm, verified.digest,
                              surface.carriage_polygon_mm, surface.digest).directive
    return RasterBoundary(polygon, verified.bounds_mm, verified.digest).directive
