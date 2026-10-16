"""Explicit raster surface and independently verified carriage-travel authority.

The directive is application metadata, never a controller instruction. Its digest
binds the exact physical verification record selected at preparation time.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date

from .errors import SafetyError
from .geometry.polygon import convex_polygon_contains_normalized, normalize_convex_polygon
from .storage import strict_json_loads

PREFIX = "E3RASTER "
CAPABILITY = "pi-raster-travel-envelope-v1"
COORDINATE_REFERENCE = "homed_machine_xy_zero_work_offsets"


def _number(value: object, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


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

    @property
    def bounds_mm(self) -> tuple[float, float, float, float]:
        return self.x_min, self.x_max, self.y_min, self.y_max

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    def contains(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


def parse_raster_travel_envelope(value: object) -> RasterTravelEnvelope | None:
    if value is None:
        return None
    if type(value) is RasterTravelEnvelope:
        # Reconstruct to defend against mutation via object.__setattr__.
        return RasterTravelEnvelope(**value.to_dict())
    if type(value) is not dict or set(value) != set(RasterTravelEnvelope.__dataclass_fields__):
        raise ValueError("machine.raster_travel_envelope requires exact bounds, verified, controller, firmware, configuration, reference and tested_at fields")
    return RasterTravelEnvelope(**value)


def require_verified_raster_travel(value: object) -> RasterTravelEnvelope:
    try:
        envelope = parse_raster_travel_envelope(value)
    except (TypeError, ValueError) as exc:
        raise SafetyError(str(exc)) from exc
    if envelope is None or not envelope.verified:
        raise SafetyError("Raster travel requires a separately configured verified machine.raster_travel_envelope with recorded controller, firmware, configuration and physical-test evidence")
    return envelope


@dataclass(frozen=True, slots=True)
class RasterBoundary:
    engraving_polygon_mm: tuple[tuple[float, float], ...]
    travel_bounds_mm: tuple[float, float, float, float]
    travel_envelope_digest: str

    @property
    def directive(self) -> str:
        return PREFIX + _canonical({
            "schema": 1,
            "engraving_polygon_mm": self.engraving_polygon_mm,
            "travel_bounds_mm": self.travel_bounds_mm,
            "travel_envelope_digest": self.travel_envelope_digest,
        })

    def require_travel_point(self, point: tuple[float, float], *, label: str) -> None:
        x, y = point
        if not math.isfinite(x) or not math.isfinite(y):
            raise SafetyError(f"{label}: machine carriage travel point must be finite")
        xmin, xmax, ymin, ymax = self.travel_bounds_mm
        if xmin <= x <= xmax and ymin <= y <= ymax:
            return
        extra = (max(0.0, xmin - x), max(0.0, x - xmax), max(0.0, ymin - y), max(0.0, y - ymax))
        if any(extra):
            clearance = ", ".join(f"{side} {distance:.4f} mm" for side, distance in zip(("X−", "X+", "Y−", "Y+"), extra, strict=True) if distance)
            raise SafetyError(f"{label}: machine carriage travel point X{x:.4f} Y{y:.4f} exceeds verified travel X{xmin:g}..{xmax:g}, Y{ymin:g}..{ymax:g}; extra clearance required: {clearance}")

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
        if type(raw) is not dict or set(raw) != {"schema", "engraving_polygon_mm", "travel_bounds_mm", "travel_envelope_digest"} or type(raw["schema"]) is not int or raw["schema"] != 1:
            raise ValueError("Unsupported raster boundary schema or fields")
        polygon = normalize_convex_polygon(raw["engraving_polygon_mm"], label="Raster engraving surface")
        if len(polygon) != 4:
            raise ValueError("Raster engraving surface must have four corners")
        bounds_raw = raw["travel_bounds_mm"]
        if type(bounds_raw) is not list or len(bounds_raw) != 4:
            raise ValueError("Raster travel bounds must contain four numbers")
        bounds = tuple(_number(value, "Raster travel bound") for value in bounds_raw)
        if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            raise ValueError("Raster travel bounds are empty")
        digest = raw["travel_envelope_digest"]
        if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Raster travel envelope digest must be SHA-256")
        return RasterBoundary(polygon, bounds, digest)
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


def raster_boundary_directive(engraving_polygon_mm: object, envelope: RasterTravelEnvelope) -> str:
    verified = require_verified_raster_travel(envelope)
    polygon = normalize_convex_polygon(engraving_polygon_mm, label="Raster engraving surface")
    if len(polygon) != 4:
        raise ValueError("Raster engraving surface must have four corners")
    return RasterBoundary(polygon, verified.bounds_mm, verified.digest).directive
