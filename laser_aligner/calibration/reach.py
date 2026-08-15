from __future__ import annotations

import copy
import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings, effective_laser_output_area
from ..geometry.polygon import normalize_convex_polygon
from ..storage import atomic_write_json, strict_json_loads
from .support import HoneycombSupportReference

LOGGER = logging.getLogger(__name__)
FIXTURE_REACH_SCHEMA_VERSION = 1
_FIXTURE_MODES = frozenset({"unclassified", "permanent", "movable"})
_LIMIT_KEYS = ("x_min", "x_max", "y_min", "y_max")
_EPSILON = 1e-7

Point = tuple[float, float]
Polygon = tuple[Point, ...]


def _optional_finite(value: object, label: str) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise ValueError(f"{label} must be null or a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be null or a finite number")
    return number


@dataclass(slots=True)
class FixtureReachEvidence:
    """Operator-recorded fixed-fixture classification and safe carriage limits.

    The limits are diagnostic evidence only. They never alter GRBL settings,
    machine.work_area, G-code, arming, or laser-output authority.
    """

    fixture_mode: str = "unclassified"
    x_min_mm: float | None = None
    x_max_mm: float | None = None
    y_min_mm: float | None = None
    y_max_mm: float | None = None
    observations: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)
    schema_version: int = FIXTURE_REACH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or (
            self.schema_version != FIXTURE_REACH_SCHEMA_VERSION
        ):
            raise ValueError(
                "Fixture reach evidence schema_version is unsupported"
            )
        if type(self.fixture_mode) is not str or self.fixture_mode not in _FIXTURE_MODES:
            raise ValueError(
                "fixture_mode must be unclassified, permanent, or movable"
            )
        self.x_min_mm = _optional_finite(self.x_min_mm, "x_min_mm")
        self.x_max_mm = _optional_finite(self.x_max_mm, "x_max_mm")
        self.y_min_mm = _optional_finite(self.y_min_mm, "y_min_mm")
        self.y_max_mm = _optional_finite(self.y_max_mm, "y_max_mm")
        if (
            self.x_min_mm is not None
            and self.x_max_mm is not None
            and self.x_max_mm <= self.x_min_mm
        ):
            raise ValueError("x_max_mm must be greater than x_min_mm")
        if (
            self.y_min_mm is not None
            and self.y_max_mm is not None
            and self.y_max_mm <= self.y_min_mm
        ):
            raise ValueError("y_max_mm must be greater than y_min_mm")
        if not isinstance(self.observations, dict):
            raise ValueError("observations must be a JSON object")
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in self.observations.items():
            if key not in _LIMIT_KEYS or not isinstance(value, Mapping):
                raise ValueError("observations contain an invalid limit entry")
            normalized[key] = copy.deepcopy(dict(value))
        self.observations = normalized
        if type(self.updated_at) not in {int, float} or not math.isfinite(
            float(self.updated_at)
        ):
            raise ValueError("updated_at must be a finite number")
        self.updated_at = float(self.updated_at)

    @property
    def complete(self) -> bool:
        return all(
            value is not None
            for value in (
                self.x_min_mm,
                self.x_max_mm,
                self.y_min_mm,
                self.y_max_mm,
            )
        )

    @property
    def safe_travel_area_mm(self) -> tuple[float, float, float, float] | None:
        if not self.complete:
            return None
        assert self.x_min_mm is not None
        assert self.x_max_mm is not None
        assert self.y_min_mm is not None
        assert self.y_max_mm is not None
        return (
            self.x_min_mm,
            self.x_max_mm,
            self.y_min_mm,
            self.y_max_mm,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fixture_mode": self.fixture_mode,
            "safe_travel_limits_mm": {
                "x_min": self.x_min_mm,
                "x_max": self.x_max_mm,
                "y_min": self.y_min_mm,
                "y_max": self.y_max_mm,
            },
            "observations": copy.deepcopy(self.observations),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> FixtureReachEvidence:
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise ValueError("Fixture reach evidence must be a JSON object")
        limits = raw.get("safe_travel_limits_mm") or {}
        if not isinstance(limits, Mapping):
            raise ValueError("safe_travel_limits_mm must be a JSON object")
        return cls(
            schema_version=raw.get(
                "schema_version", FIXTURE_REACH_SCHEMA_VERSION
            ),
            fixture_mode=raw.get("fixture_mode", "unclassified"),
            x_min_mm=limits.get("x_min"),
            x_max_mm=limits.get("x_max"),
            y_min_mm=limits.get("y_min"),
            y_max_mm=limits.get("y_max"),
            observations=copy.deepcopy(dict(raw.get("observations") or {})),
            updated_at=raw.get("updated_at", time.time()),
        )


class FixtureReachStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "fixture_reach.json"
        self._load_error: str | None = None
        self._evidence = self._load()

    @property
    def evidence(self) -> FixtureReachEvidence:
        return self._evidence

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _load(self) -> FixtureReachEvidence:
        if not self.path.exists():
            return FixtureReachEvidence()
        try:
            raw = strict_json_loads(self.path.read_text(encoding="utf-8"))
            return FixtureReachEvidence.from_dict(raw)
        except (
            KeyError,
            OSError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            self._load_error = f"Saved fixture reach evidence is invalid: {exc}"
            LOGGER.warning("Ignoring invalid fixture reach evidence: %s", exc)
            return FixtureReachEvidence()

    def save(self, evidence: FixtureReachEvidence) -> FixtureReachEvidence:
        canonical = FixtureReachEvidence.from_dict(evidence.to_dict())
        atomic_write_json(self.path, canonical.to_dict())
        self._evidence = canonical
        self._load_error = None
        return canonical

    def set_fixture_mode(self, mode: str) -> FixtureReachEvidence:
        payload = self._evidence.to_dict()
        payload["fixture_mode"] = mode
        payload["updated_at"] = time.time()
        return self.save(FixtureReachEvidence.from_dict(payload))

    def set_safe_travel_area(
        self,
        *,
        x_min_mm: float,
        x_max_mm: float,
        y_min_mm: float,
        y_max_mm: float,
        source: str,
        machine_port: str,
        protocol: str,
    ) -> FixtureReachEvidence:
        now = time.time()
        observations = copy.deepcopy(self._evidence.observations)
        for key, value in (
            ("x_min", x_min_mm),
            ("x_max", x_max_mm),
            ("y_min", y_min_mm),
            ("y_max", y_max_mm),
        ):
            observations[key] = {
                "value_mm": float(value),
                "source": str(source),
                "recorded_at": now,
                "machine_port": str(machine_port),
                "protocol": str(protocol),
            }
        return self.save(
            FixtureReachEvidence(
                fixture_mode=self._evidence.fixture_mode,
                x_min_mm=x_min_mm,
                x_max_mm=x_max_mm,
                y_min_mm=y_min_mm,
                y_max_mm=y_max_mm,
                observations=observations,
                updated_at=now,
            )
        )

    def record_limit(
        self,
        key: str,
        *,
        value_mm: float,
        position_mm: tuple[float, float],
        machine_port: str,
        protocol: str,
    ) -> FixtureReachEvidence:
        if key not in _LIMIT_KEYS:
            raise ValueError("Fixture reach limit key is invalid")
        value = _optional_finite(value_mm, key)
        assert value is not None
        x, y = position_mm
        if not all(math.isfinite(float(item)) for item in (x, y)):
            raise ValueError("Trusted machine position must be finite")
        payload = self._evidence.to_dict()
        limits = dict(payload["safe_travel_limits_mm"])
        limits[key] = value
        payload["safe_travel_limits_mm"] = limits
        observations = dict(payload.get("observations") or {})
        observations[key] = {
            "value_mm": value,
            "source": "trusted_jog_position",
            "recorded_at": time.time(),
            "observed_position_mm": [float(x), float(y)],
            "machine_port": str(machine_port),
            "protocol": str(protocol),
        }
        payload["observations"] = observations
        payload["updated_at"] = time.time()
        return self.save(FixtureReachEvidence.from_dict(payload))

    def clear_limits(self) -> FixtureReachEvidence:
        return self.save(
            FixtureReachEvidence(
                fixture_mode=self._evidence.fixture_mode,
                updated_at=time.time(),
            )
        )


def _rect_polygon(bounds: Sequence[float]) -> Polygon:
    if len(bounds) != 4:
        raise ValueError("Rectangle bounds must contain x_min, x_max, y_min, y_max")
    x_min, x_max, y_min, y_max = (float(value) for value in bounds)
    if not all(math.isfinite(value) for value in (x_min, x_max, y_min, y_max)):
        raise ValueError("Rectangle bounds must be finite")
    if x_max <= x_min or y_max <= y_min:
        raise ValueError("Rectangle bounds must have positive width and height")
    return ((x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max))


def _shift_polygon(polygon: Sequence[Sequence[float]], dx: float, dy: float) -> Polygon:
    return tuple((float(x) + dx, float(y) + dy) for x, y in polygon)


def _cross(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (
        c[0] - a[0]
    )


def _clean_polygon(points: Sequence[Point]) -> Polygon:
    cleaned: list[Point] = []
    for point in points:
        value = (float(point[0]), float(point[1]))
        if not cleaned or math.hypot(
            value[0] - cleaned[-1][0], value[1] - cleaned[-1][1]
        ) > _EPSILON:
            cleaned.append(value)
    if len(cleaned) > 1 and math.hypot(
        cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]
    ) <= _EPSILON:
        cleaned.pop()
    changed = True
    while changed and len(cleaned) >= 3:
        changed = False
        reduced: list[Point] = []
        for index, point in enumerate(cleaned):
            previous = cleaned[index - 1]
            following = cleaned[(index + 1) % len(cleaned)]
            if abs(_cross(previous, point, following)) <= _EPSILON:
                changed = True
                continue
            reduced.append(point)
        cleaned = reduced
    return tuple(cleaned) if len(cleaned) >= 3 else ()


def _segment_line_intersection(
    start: Point,
    end: Point,
    clip_start: Point,
    clip_end: Point,
) -> Point:
    edge = (clip_end[0] - clip_start[0], clip_end[1] - clip_start[1])
    segment = (end[0] - start[0], end[1] - start[1])
    numerator = edge[0] * (start[1] - clip_start[1]) - edge[1] * (
        start[0] - clip_start[0]
    )
    denominator = edge[0] * segment[1] - edge[1] * segment[0]
    if abs(denominator) <= _EPSILON:
        return end
    t = max(0.0, min(1.0, -numerator / denominator))
    return (start[0] + segment[0] * t, start[1] + segment[1] * t)


def intersect_convex_polygons(
    subject: Sequence[Sequence[float]],
    clip: Sequence[Sequence[float]],
) -> Polygon:
    """Return the convex intersection of two ordered polygons."""

    subject_polygon = normalize_convex_polygon(subject, label="subject polygon")
    clip_polygon = normalize_convex_polygon(clip, label="clip polygon")
    output: list[Point] = list(subject_polygon)
    for index, clip_start in enumerate(clip_polygon):
        clip_end = clip_polygon[(index + 1) % len(clip_polygon)]
        incoming = output
        output = []
        if not incoming:
            return ()
        start = incoming[-1]
        start_inside = _cross(clip_start, clip_end, start) >= -_EPSILON
        for end in incoming:
            end_inside = _cross(clip_start, clip_end, end) >= -_EPSILON
            if end_inside:
                if not start_inside:
                    output.append(
                        _segment_line_intersection(
                            start, end, clip_start, clip_end
                        )
                    )
                output.append(end)
            elif start_inside:
                output.append(
                    _segment_line_intersection(start, end, clip_start, clip_end)
                )
            start = end
            start_inside = end_inside
    return _clean_polygon(output)


def _intersect_many(polygons: Sequence[Sequence[Sequence[float]]]) -> Polygon:
    if not polygons:
        return ()
    result: Polygon = normalize_convex_polygon(polygons[0])
    for polygon in polygons[1:]:
        if not result:
            break
        result = intersect_convex_polygons(result, polygon)
    return result


def polygon_area(polygon: Sequence[Sequence[float]]) -> float:
    if len(polygon) < 3:
        return 0.0
    return abs(
        sum(
            float(polygon[index][0]) * float(polygon[(index + 1) % len(polygon)][1])
            - float(polygon[(index + 1) % len(polygon)][0])
            * float(polygon[index][1])
            for index in range(len(polygon))
        )
        / 2.0
    )


def _violation_mm(point: Point, polygon: Sequence[Sequence[float]]) -> float:
    normalized = normalize_convex_polygon(polygon)
    violation = 0.0
    for index, start in enumerate(normalized):
        end = normalized[(index + 1) % len(normalized)]
        edge_x = end[0] - start[0]
        edge_y = end[1] - start[1]
        cross = edge_x * (point[1] - start[1]) - edge_y * (
            point[0] - start[0]
        )
        violation = max(violation, -cross / math.hypot(edge_x, edge_y))
    return max(0.0, violation)


def _coverage_payload(
    support_polygon: Polygon,
    authority_polygon: Polygon,
    support_reference: HoneycombSupportReference,
) -> dict[str, Any]:
    intersection = intersect_convex_polygons(support_polygon, authority_polygon)
    support_area = polygon_area(support_polygon)
    usable_area = polygon_area(intersection)
    coverage = 0.0 if support_area <= 0.0 else usable_area / support_area
    maximum_escape = max(
        _violation_mm(point, authority_polygon) for point in support_polygon
    )
    return {
        "full_support": bool(coverage >= 1.0 - 1e-8 and maximum_escape <= 1e-6),
        "coverage_fraction": max(0.0, min(1.0, coverage)),
        "coverage_percent": max(0.0, min(100.0, coverage * 100.0)),
        "usable_area_mm2": usable_area,
        "maximum_corner_escape_mm": maximum_escape,
        "usable_polygon_machine_mm": [list(point) for point in intersection],
        "usable_polygon_local_mm": [
            list(support_reference.machine_to_local(*point))
            for point in intersection
        ],
    }


def _work_authority(settings: Settings) -> Polygon:
    area = settings.machine.work_area
    work = _rect_polygon((area.x_min, area.x_max, area.y_min, area.y_max))
    spot_x = float(settings.laser.spot_offset_x_mm)
    spot_y = float(settings.laser.spot_offset_y_mm)
    return _intersect_many((work, _shift_polygon(work, spot_x, spot_y)))


def _guarded_authority(settings: Settings) -> Polygon:
    configured = settings.laser.guarded_output_polygon_mm
    if configured is None:
        effective = effective_laser_output_area(
            settings.machine.work_area,
            settings.laser.boundary_margin_mm,
            settings.laser.spot_offset_x_mm,
            settings.laser.spot_offset_y_mm,
        )
        output = _rect_polygon(
            (effective.x_min, effective.x_max, effective.y_min, effective.y_max)
        )
        base = _rect_polygon(
            (
                settings.machine.work_area.x_min
                + settings.laser.boundary_margin_mm,
                settings.machine.work_area.x_max
                - settings.laser.boundary_margin_mm,
                settings.machine.work_area.y_min
                + settings.laser.boundary_margin_mm,
                settings.machine.work_area.y_max
                - settings.laser.boundary_margin_mm,
            )
        )
    else:
        output = normalize_convex_polygon(configured)
        base = output
    spot_x = float(settings.laser.spot_offset_x_mm)
    spot_y = float(settings.laser.spot_offset_y_mm)
    return _intersect_many((output, base, _shift_polygon(base, spot_x, spot_y)))


def _controller_travel_payload(
    support_polygon: Polygon,
    support_reference: HoneycombSupportReference,
    grbl_settings: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(grbl_settings, Mapping):
        return None
    x_max = grbl_settings.get("130")
    y_max = grbl_settings.get("131")
    if type(x_max) not in {int, float} or type(y_max) not in {int, float}:
        return None
    x_value = float(x_max)
    y_value = float(y_max)
    if not math.isfinite(x_value) or not math.isfinite(y_value):
        return None
    if x_value <= 0.0 or y_value <= 0.0:
        return None
    polygon = _rect_polygon((0.0, x_value, 0.0, y_value))
    payload = _coverage_payload(support_polygon, polygon, support_reference)
    payload.update(
        {
            "configured_area_mm": [0.0, x_value, 0.0, y_value],
            "soft_limits_enabled": bool(float(grbl_settings.get("20", 0.0))),
            "hard_limits_enabled": bool(float(grbl_settings.get("21", 0.0))),
            "enforcement_note": (
                "$130/$131 are controller settings, not physical reach evidence. "
                "Soft and hard limit enable states are reported separately."
            ),
        }
    )
    return payload


def build_fixture_reachability(
    settings: Settings,
    *,
    support_reference: HoneycombSupportReference | None,
    evidence: FixtureReachEvidence,
    grbl_settings: Mapping[str, Any] | None = None,
    load_error: str | None = None,
) -> dict[str, Any]:
    """Describe fixed-fixture coverage without changing any authority."""

    mode = evidence.fixture_mode
    limits = {
        "x_min": evidence.x_min_mm,
        "x_max": evidence.x_max_mm,
        "y_min": evidence.y_min_mm,
        "y_max": evidence.y_max_mm,
    }
    base: dict[str, Any] = {
        "fixture_mode": mode,
        "permanent_fixture": mode == "permanent",
        "evidence_state": (
            "COMPLETE"
            if evidence.complete
            else "PARTIAL"
            if any(value is not None for value in limits.values())
            else "MISSING"
        ),
        "safe_carriage_limits_mm": limits,
        "safe_carriage_area_mm": (
            None
            if evidence.safe_travel_area_mm is None
            else list(evidence.safe_travel_area_mm)
        ),
        "observations": copy.deepcopy(evidence.observations),
        "updated_at": evidence.updated_at,
        "load_error": load_error,
        "state": "NO_SUPPORT",
        "reasons": [],
        "configured_work": None,
        "guarded_output": None,
        "measured_travel": None,
        "combined": None,
        "controller_settings": None,
        "output_authority_within_measured_travel": None,
        "measurement_machine_ports": [],
        "measurement_protocols": [],
        "evidence_matches_current_machine": None,
    }
    recorded_ports = sorted(
        {
            str(item.get("machine_port"))
            for item in evidence.observations.values()
            if item.get("machine_port")
        }
    )
    recorded_protocols = sorted(
        {
            str(item.get("protocol"))
            for item in evidence.observations.values()
            if item.get("protocol")
        }
    )
    base["measurement_machine_ports"] = recorded_ports
    base["measurement_protocols"] = recorded_protocols
    if recorded_ports or recorded_protocols:
        port_matches = not recorded_ports or str(settings.machine.port) in recorded_ports
        protocol_matches = (
            not recorded_protocols
            or str(settings.machine.protocol) == "auto"
            or str(settings.machine.protocol) in recorded_protocols
        )
        base["evidence_matches_current_machine"] = bool(
            port_matches and protocol_matches
        )
    reasons: list[str] = []
    if load_error:
        reasons.append(load_error)
    if support_reference is None:
        reasons.append("No current honeycomb support is available for reach analysis")
        base["reasons"] = reasons
        return base

    support_polygon = normalize_convex_polygon(
        support_reference.rigid_support_corners_machine_mm,
        label="rigid honeycomb support",
    )
    work_authority = _work_authority(settings)
    guarded_authority = _guarded_authority(settings)
    configured_work = _coverage_payload(
        support_polygon, work_authority, support_reference
    )
    guarded_output = _coverage_payload(
        support_polygon, guarded_authority, support_reference
    )
    base["configured_work"] = configured_work
    base["guarded_output"] = guarded_output
    base["controller_settings"] = _controller_travel_payload(
        support_polygon, support_reference, grbl_settings
    )

    measured_authority: Polygon | None = None
    if evidence.safe_travel_area_mm is not None:
        measured_carriage = _rect_polygon(evidence.safe_travel_area_mm)
        measured_authority = _shift_polygon(
            measured_carriage,
            float(settings.laser.spot_offset_x_mm),
            float(settings.laser.spot_offset_y_mm),
        )
        base["measured_travel"] = _coverage_payload(
            support_polygon, measured_authority, support_reference
        )
        combined_authority = _intersect_many(
            (work_authority, guarded_authority, measured_authority)
        )
        if combined_authority:
            base["combined"] = _coverage_payload(
                support_polygon, combined_authority, support_reference
            )
        else:
            base["combined"] = {
                "full_support": False,
                "coverage_fraction": 0.0,
                "coverage_percent": 0.0,
                "usable_area_mm2": 0.0,
                "maximum_corner_escape_mm": math.inf,
                "usable_polygon_machine_mm": [],
                "usable_polygon_local_mm": [],
            }
        configured_output = normalize_convex_polygon(
            (
                settings.laser.guarded_output_polygon_mm
                if settings.laser.guarded_output_polygon_mm is not None
                else guarded_authority
            ),
            label="configured laser-output polygon",
        )
        output_escape = max(
            _violation_mm(point, measured_authority)
            for point in configured_output
        )
        base["output_authority_within_measured_travel"] = {
            "within": output_escape <= 1e-6,
            "maximum_escape_mm": output_escape,
        }

    evidence_mismatch = base["evidence_matches_current_machine"] is False
    if evidence_mismatch:
        reasons.append(
            "The saved reach evidence was recorded for a different controller "
            "port or protocol; re-measure before relying on it"
        )

    if evidence_mismatch:
        state = "STALE"
    elif mode == "unclassified":
        state = "UNCLASSIFIED"
        reasons.append(
            "Classify whether the honeycomb is permanent or movable before reach guidance"
        )
    elif mode == "permanent" and evidence.safe_travel_area_mm is None:
        state = "NOT_MEASURED"
        missing = [
            key.replace("_", " ").upper()
            for key, value in limits.items()
            if value is None
        ]
        reasons.append(
            "The fixture is permanent; record laser-off safe carriage limits "
            "without moving it"
            + (f" (missing: {', '.join(missing)})" if missing else "")
        )
    elif evidence.safe_travel_area_mm is None:
        state = "NOT_MEASURED"
        reasons.append("Safe carriage limits have not been recorded")
    else:
        combined = base["combined"] or {}
        if combined.get("full_support") is True:
            state = "FULL"
        else:
            state = "PARTIAL"
            reasons.append(
                "The fixed support is only "
                f"{float(combined.get('coverage_percent') or 0.0):.1f}% inside "
                "the intersection of measured travel and configured output authorities"
            )
        output_check = base["output_authority_within_measured_travel"] or {}
        if output_check.get("within") is False:
            reasons.append(
                "The configured laser-output polygon extends beyond the recorded "
                f"safe carriage reach by up to {float(output_check['maximum_escape_mm']):.1f} mm"
            )

    if mode == "permanent":
        reasons.append(
            "The honeycomb is a permanent fixture; do not reposition it to satisfy software bounds"
        )
    base["state"] = state
    base["reasons"] = reasons
    return base


__all__ = [
    "FIXTURE_REACH_SCHEMA_VERSION",
    "FixtureReachEvidence",
    "FixtureReachStore",
    "build_fixture_reachability",
    "intersect_convex_polygons",
    "polygon_area",
]
