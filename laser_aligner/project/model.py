from __future__ import annotations

import copy
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from numbers import Real
from typing import Any

PROJECT_SCHEMA_VERSION = 2
OBJECT_ROLE_KEY = "e3_role"
STOCK_BOUNDARY_ROLE = "stock_boundary"


class ProjectFormatError(ValueError):
    """Raised when an E3 project file is malformed or unsupported."""


class LayerMode(str, Enum):
    LINE = "line"
    FILL = "fill"
    RASTER = "raster"


class ObjectKind(str, Enum):
    RECTANGLE = "rectangle"
    ELLIPSE = "ellipse"
    LINE = "line"
    POLYGON = "polygon"
    PATH = "path"
    TEXT = "text"
    IMAGE = "image"


class CoordinateSpace(str, Enum):
    """Coordinate domain used by persisted project geometry."""

    MACHINE = "machine"
    HONEYCOMB_LOCAL = "honeycomb_local"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ProjectFormatError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ProjectFormatError(f"{name} must be a finite number")
    return number


def _positive(value: float, name: str) -> float:
    number = _finite(value, name)
    if number <= 0:
        raise ProjectFormatError(f"{name} must be positive")
    return number


def _boolean(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ProjectFormatError(f"{name} must be a JSON boolean")
    return value


def _string(value: Any, name: str) -> str:
    if type(value) is not str:
        raise ProjectFormatError(f"{name} must be a JSON string")
    return value


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectFormatError(f"{name} must be a JSON object")
    return value


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ProjectFormatError(f"{name} must be a JSON array")
    return value


def _color(value: str) -> str:
    text = str(value).strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
        except ValueError as exc:
            raise ProjectFormatError(f"Invalid layer color: {value}") from exc
        return text.upper()
    raise ProjectFormatError(f"Layer color must be #RRGGBB, received {value!r}")


@dataclass(slots=True, frozen=True)
class Bounds:
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def __post_init__(self) -> None:
        values = {
            "x_min": _finite(self.x_min, "x_min"),
            "y_min": _finite(self.y_min, "y_min"),
            "x_max": _finite(self.x_max, "x_max"),
            "y_max": _finite(self.y_max, "y_max"),
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)
        if self.x_max < self.x_min or self.y_max < self.y_min:
            raise ProjectFormatError("Bounds maximums must not be smaller than minimums")

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x_min + self.x_max) / 2.0, (self.y_min + self.y_max) / 2.0)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        margin = max(0.0, float(margin))
        return (
            self.x_min + margin <= x <= self.x_max - margin
            and self.y_min + margin <= y <= self.y_max - margin
        )

    def union(self, other: Bounds) -> Bounds:
        return Bounds(
            min(self.x_min, other.x_min),
            min(self.y_min, other.y_min),
            max(self.x_max, other.x_max),
            max(self.y_max, other.y_max),
        )

    def expanded(self, amount: float) -> Bounds:
        amount = _finite(amount, "amount")
        return Bounds(
            self.x_min - amount,
            self.y_min - amount,
            self.x_max + amount,
            self.y_max + amount,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "x_min": self.x_min,
            "y_min": self.y_min,
            "x_max": self.x_max,
            "y_max": self.y_max,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Bounds:
        raw = _object(raw, "work_area")
        return cls(
            x_min=raw["x_min"],
            y_min=raw["y_min"],
            x_max=raw["x_max"],
            y_max=raw["y_max"],
        )


@dataclass(slots=True)
class Transform:
    x_mm: float = 0.0
    y_mm: float = 0.0
    width_mm: float = 10.0
    height_mm: float = 10.0
    rotation_deg: float = 0.0
    mirror_x: bool = False
    mirror_y: bool = False

    def __post_init__(self) -> None:
        self.x_mm = _finite(self.x_mm, "transform.x_mm")
        self.y_mm = _finite(self.y_mm, "transform.y_mm")
        self.width_mm = _positive(self.width_mm, "transform.width_mm")
        self.height_mm = _positive(self.height_mm, "transform.height_mm")
        self.rotation_deg = self.normalized_rotation(self.rotation_deg)
        self.mirror_x = _boolean(self.mirror_x, "transform.mirror_x")
        self.mirror_y = _boolean(self.mirror_y, "transform.mirror_y")

    @staticmethod
    def normalized_rotation(value: float) -> float:
        rotation = _finite(value, "transform.rotation_deg")
        return (rotation + 180.0) % 360.0 - 180.0

    def copy(self, **changes: Any) -> Transform:
        payload = self.to_dict()
        payload.update(changes)
        return Transform.from_dict(payload)

    def corners(self) -> tuple[tuple[float, float], ...]:
        half_width = self.width_mm / 2.0
        half_height = self.height_mm / 2.0
        sx = -1.0 if self.mirror_x else 1.0
        sy = -1.0 if self.mirror_y else 1.0
        angle = math.radians(self.rotation_deg)
        cosine = math.cos(angle)
        sine = math.sin(angle)
        output: list[tuple[float, float]] = []
        for local_x, local_y in (
            (-half_width, -half_height),
            (half_width, -half_height),
            (half_width, half_height),
            (-half_width, half_height),
        ):
            local_x *= sx
            local_y *= sy
            world_x = self.x_mm + local_x * cosine - local_y * sine
            world_y = self.y_mm + local_x * sine + local_y * cosine
            output.append((world_x, world_y))
        return tuple(output)

    def bounds(self) -> Bounds:
        corners = self.corners()
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        return Bounds(min(xs), min(ys), max(xs), max(ys))

    def to_dict(self) -> dict[str, Any]:
        return {
            "x_mm": self.x_mm,
            "y_mm": self.y_mm,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "rotation_deg": self.rotation_deg,
            "mirror_x": self.mirror_x,
            "mirror_y": self.mirror_y,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Transform:
        raw = _object(raw, "object.transform")
        return cls(
            x_mm=raw.get("x_mm", 0.0),
            y_mm=raw.get("y_mm", 0.0),
            width_mm=raw.get("width_mm", 10.0),
            height_mm=raw.get("height_mm", 10.0),
            rotation_deg=raw.get("rotation_deg", 0.0),
            mirror_x=_boolean(raw.get("mirror_x", False), "transform.mirror_x"),
            mirror_y=_boolean(raw.get("mirror_y", False), "transform.mirror_y"),
        )


@dataclass(slots=True)
class OperationLayer:
    id: str = field(default_factory=lambda: _new_id("layer"))
    name: str = "Line"
    color: str = "#E35D6A"
    mode: LayerMode = LayerMode.LINE
    speed_mm_min: float = 2000.0
    power_percent: float = 10.0
    passes: int = 1
    line_interval_mm: float = 0.10
    scan_angle_deg: float = 0.0
    overscan_percent: float = 2.5
    vector_power_correction: float = 0.0
    raster_power_correction: float = 0.0
    air_assist: bool = False
    output_enabled: bool = True
    visible: bool = True
    priority: int = 0

    def __post_init__(self) -> None:
        self.id = str(self.id or _new_id("layer"))
        self.name = str(self.name or "Layer")[:80]
        self.color = _color(self.color)
        self.mode = self.mode if isinstance(self.mode, LayerMode) else LayerMode(str(self.mode))
        self.speed_mm_min = _positive(self.speed_mm_min, "layer.speed_mm_min")
        self.power_percent = _finite(self.power_percent, "layer.power_percent")
        if not 0.0 <= self.power_percent <= 100.0:
            raise ProjectFormatError("layer.power_percent must be between 0 and 100")
        if type(self.passes) is not int:
            raise ProjectFormatError("layer.passes must be an integer")
        if self.passes < 1:
            raise ProjectFormatError("layer.passes must be at least one")
        self.line_interval_mm = _positive(self.line_interval_mm, "layer.line_interval_mm")
        self.scan_angle_deg = Transform.normalized_rotation(self.scan_angle_deg)
        self.overscan_percent = _finite(self.overscan_percent, "layer.overscan_percent")
        if not 0.0 <= self.overscan_percent <= 100.0:
            raise ProjectFormatError("layer.overscan_percent must be between 0 and 100")
        self.vector_power_correction = _finite(
            self.vector_power_correction,
            "layer.vector_power_correction",
        )
        self.raster_power_correction = _finite(
            self.raster_power_correction,
            "layer.raster_power_correction",
        )
        for name, value in (
            ("vector_power_correction", self.vector_power_correction),
            ("raster_power_correction", self.raster_power_correction),
        ):
            if not -100.0 <= value <= 100.0:
                raise ProjectFormatError(f"layer.{name} must be between -100 and 100")
        self.air_assist = _boolean(self.air_assist, "layer.air_assist")
        self.output_enabled = _boolean(
            self.output_enabled,
            "layer.output_enabled",
        )
        self.visible = _boolean(self.visible, "layer.visible")
        if type(self.priority) is not int:
            raise ProjectFormatError("layer.priority must be an integer")

    def controller_power(self, power_max: int) -> int:
        if power_max <= 0:
            raise ValueError("power_max must be positive")
        return int(round(power_max * self.power_percent / 100.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "mode": self.mode.value,
            "speed_mm_min": self.speed_mm_min,
            "power_percent": self.power_percent,
            "passes": self.passes,
            "line_interval_mm": self.line_interval_mm,
            "scan_angle_deg": self.scan_angle_deg,
            "overscan_percent": self.overscan_percent,
            "vector_power_correction": self.vector_power_correction,
            "raster_power_correction": self.raster_power_correction,
            "air_assist": self.air_assist,
            "output_enabled": self.output_enabled,
            "visible": self.visible,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> OperationLayer:
        raw = _object(raw, "layer")
        return cls(
            id=_string(raw.get("id", _new_id("layer")), "layer.id"),
            name=_string(raw.get("name", "Layer"), "layer.name"),
            color=_string(raw.get("color", "#E35D6A"), "layer.color"),
            mode=LayerMode(
                _string(raw.get("mode", LayerMode.LINE.value), "layer.mode")
            ),
            speed_mm_min=raw.get("speed_mm_min", 2000.0),
            power_percent=raw.get("power_percent", 10.0),
            passes=raw.get("passes", 1),
            line_interval_mm=raw.get("line_interval_mm", 0.10),
            scan_angle_deg=raw.get("scan_angle_deg", 0.0),
            overscan_percent=raw.get("overscan_percent", 2.5),
            vector_power_correction=raw.get("vector_power_correction", 0.0),
            raster_power_correction=raw.get("raster_power_correction", 0.0),
            air_assist=_boolean(raw.get("air_assist", False), "layer.air_assist"),
            output_enabled=_boolean(
                raw.get("output_enabled", True),
                "layer.output_enabled",
            ),
            visible=_boolean(raw.get("visible", True), "layer.visible"),
            priority=raw.get("priority", 0),
        )


@dataclass(slots=True)
class SceneObject:
    id: str = field(default_factory=lambda: _new_id("object"))
    name: str = "Object"
    kind: ObjectKind = ObjectKind.RECTANGLE
    layer_id: str = ""
    transform: Transform = field(default_factory=Transform)
    geometry: dict[str, Any] = field(default_factory=dict)
    visible: bool = True
    locked: bool = False
    group_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = str(self.id or _new_id("object"))
        self.name = str(self.name or "Object")[:160]
        self.kind = self.kind if isinstance(self.kind, ObjectKind) else ObjectKind(str(self.kind))
        self.layer_id = str(self.layer_id)
        if not isinstance(self.transform, Transform):
            self.transform = Transform.from_dict(self.transform)
        if not isinstance(self.geometry, dict):
            raise ProjectFormatError("object.geometry must be an object")
        if not isinstance(self.metadata, dict):
            raise ProjectFormatError("object.metadata must be an object")
        self.geometry = copy.deepcopy(self.geometry)
        self.metadata = copy.deepcopy(self.metadata)
        self.visible = _boolean(self.visible, "object.visible")
        self.locked = _boolean(self.locked, "object.locked")
        self.group_id = None if self.group_id in {None, ""} else str(self.group_id)
        self.validate_geometry()

    @property
    def is_stock_boundary(self) -> bool:
        return self.metadata.get(OBJECT_ROLE_KEY) == STOCK_BOUNDARY_ROLE

    @property
    def is_output_geometry(self) -> bool:
        return not self.is_stock_boundary

    def validate_geometry(self) -> None:
        if self.kind == ObjectKind.RECTANGLE:
            radius = _finite(
                self.geometry.get("corner_radius_mm", 0.0),
                "rectangle.corner_radius_mm",
            )
            if radius < 0:
                raise ProjectFormatError("Rectangle corner radius cannot be negative")
            self.geometry["corner_radius_mm"] = radius
        elif self.kind == ObjectKind.LINE:
            points = self.geometry.get("points", [[-0.5, 0.0], [0.5, 0.0]])
            if len(points) != 2:
                raise ProjectFormatError("Line geometry requires exactly two points")
            self.geometry["points"] = [
                [_finite(point[0], "line.x"), _finite(point[1], "line.y")]
                for point in points
            ]
        elif self.kind in {ObjectKind.POLYGON, ObjectKind.PATH}:
            polylines = self.geometry.get("polylines")
            if not isinstance(polylines, list) or not polylines:
                raise ProjectFormatError("Path geometry requires at least one polyline")
            cleaned: list[dict[str, Any]] = []
            for line in polylines:
                points = line.get("points") if isinstance(line, Mapping) else None
                if not isinstance(points, Sequence) or len(points) < 2:
                    raise ProjectFormatError("Each polyline requires at least two points")
                cleaned.append(
                    {
                        "points": [
                            [_finite(point[0], "path.x"), _finite(point[1], "path.y")]
                            for point in points
                        ],
                        "closed": _boolean(
                            line.get("closed", False),
                            "path.closed",
                        ),
                    }
                )
            self.geometry["polylines"] = cleaned
        elif self.kind == ObjectKind.TEXT:
            self.geometry["text"] = _string(
                self.geometry.get("text", "Text"),
                "text.text",
            )
            self.geometry["font_family"] = _string(
                self.geometry.get("font_family", "Sans Serif"),
                "text.font_family",
            )
        elif self.kind == ObjectKind.IMAGE:
            self.geometry["asset"] = _string(
                self.geometry.get("asset", ""),
                "image.asset",
            )

    @classmethod
    def rectangle(
        cls,
        layer_id: str,
        *,
        name: str = "Rectangle",
        center: tuple[float, float] = (0.0, 0.0),
        width_mm: float = 40.0,
        height_mm: float = 25.0,
        corner_radius_mm: float = 0.0,
    ) -> SceneObject:
        return cls(
            name=name,
            kind=ObjectKind.RECTANGLE,
            layer_id=layer_id,
            transform=Transform(center[0], center[1], width_mm, height_mm),
            geometry={"corner_radius_mm": corner_radius_mm},
        )

    @classmethod
    def ellipse(
        cls,
        layer_id: str,
        *,
        name: str = "Ellipse",
        center: tuple[float, float] = (0.0, 0.0),
        width_mm: float = 30.0,
        height_mm: float = 30.0,
    ) -> SceneObject:
        return cls(
            name=name,
            kind=ObjectKind.ELLIPSE,
            layer_id=layer_id,
            transform=Transform(center[0], center[1], width_mm, height_mm),
        )

    @classmethod
    def line(
        cls,
        layer_id: str,
        *,
        name: str = "Line",
        center: tuple[float, float] = (0.0, 0.0),
        length_mm: float = 30.0,
        rotation_deg: float = 0.0,
    ) -> SceneObject:
        return cls(
            name=name,
            kind=ObjectKind.LINE,
            layer_id=layer_id,
            transform=Transform(center[0], center[1], length_mm, 1.0, rotation_deg),
            geometry={"points": [[-0.5, 0.0], [0.5, 0.0]]},
        )

    @classmethod
    def path(
        cls,
        layer_id: str,
        polylines: Iterable[Mapping[str, Any]],
        *,
        name: str = "Imported path",
        center: tuple[float, float] = (0.0, 0.0),
        source_name: str = "",
        source_svg: str | None = None,
    ) -> SceneObject:
        raw_lines: list[dict[str, Any]] = []
        all_points: list[tuple[float, float]] = []
        for line in polylines:
            points = [(float(point[0]), float(point[1])) for point in line["points"]]
            if len(points) < 2:
                continue
            raw_lines.append(
                {
                    "points": points,
                    "closed": _boolean(line.get("closed", False), "path.closed"),
                }
            )
            all_points.extend(points)
        if not all_points:
            raise ProjectFormatError("Imported path contains no usable points")
        min_x = min(point[0] for point in all_points)
        max_x = max(point[0] for point in all_points)
        min_y = min(point[1] for point in all_points)
        max_y = max(point[1] for point in all_points)
        width = max(max_x - min_x, 0.001)
        height = max(max_y - min_y, 0.001)
        local_center_x = (min_x + max_x) / 2.0
        local_center_y = (min_y + max_y) / 2.0
        normalized = [
            {
                "points": [
                    [(point[0] - local_center_x) / width, (point[1] - local_center_y) / height]
                    for point in line["points"]
                ],
                "closed": line["closed"],
            }
            for line in raw_lines
        ]
        metadata: dict[str, Any] = {"source_name": source_name}
        if source_svg is not None:
            metadata["source_svg"] = source_svg
        return cls(
            name=name,
            kind=ObjectKind.PATH,
            layer_id=layer_id,
            transform=Transform(center[0], center[1], width, height),
            geometry={"polylines": normalized},
            metadata=metadata,
        )

    def duplicate(
        self,
        offset_mm: tuple[float, float] = (5.0, -5.0),
        *,
        group_id: str | None = None,
    ) -> SceneObject:
        duplicate = SceneObject.from_dict(self.to_dict())
        duplicate.id = _new_id("object")
        duplicate.name = f"{self.name} copy"
        duplicate.group_id = group_id
        duplicate.transform = duplicate.transform.copy(
            x_mm=duplicate.transform.x_mm + float(offset_mm[0]),
            y_mm=duplicate.transform.y_mm + float(offset_mm[1]),
        )
        return duplicate

    def bounds(self) -> Bounds:
        return self.transform.bounds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "layer_id": self.layer_id,
            "transform": self.transform.to_dict(),
            "geometry": copy.deepcopy(self.geometry),
            "visible": self.visible,
            "locked": self.locked,
            "group_id": self.group_id,
            "metadata": copy.deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> SceneObject:
        raw = _object(raw, "object")
        transform = _object(raw.get("transform", {}), "object.transform")
        geometry = _object(raw.get("geometry", {}), "object.geometry")
        metadata = _object(raw.get("metadata", {}), "object.metadata")
        group_id = raw.get("group_id")
        if group_id is not None:
            group_id = _string(group_id, "object.group_id")
        return cls(
            id=_string(raw.get("id", _new_id("object")), "object.id"),
            name=_string(raw.get("name", "Object"), "object.name"),
            kind=ObjectKind(
                _string(raw.get("kind", ObjectKind.RECTANGLE.value), "object.kind")
            ),
            layer_id=_string(raw.get("layer_id", ""), "object.layer_id"),
            transform=Transform.from_dict(transform),
            geometry=copy.deepcopy(dict(geometry)),
            visible=_boolean(raw.get("visible", True), "object.visible"),
            locked=_boolean(raw.get("locked", False), "object.locked"),
            group_id=None if group_id in {None, ""} else group_id,
            metadata=copy.deepcopy(dict(metadata)),
        )


DEFAULT_LAYER_COLORS = (
    "#E35D6A",
    "#E7B55C",
    "#4FC3A1",
    "#5CA9E7",
    "#A982E3",
    "#E989C7",
    "#89B85C",
    "#D6804D",
)


DEFAULT_OPERATION_PROFILES = (
    {
        "builtin_key": "e3-10w-paper-cut",
        "material": "Copy / Printer Paper",
        "thickness_mm": None,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "Copy / Printer Paper — CUT",
            "color": "#ED23D2",
            "mode": LayerMode.LINE,
            "speed_mm_min": 1500.0,
            "power_percent": 100.0,
            "passes": 1,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 2.5,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-basswood-poplar-ply-3mm-cut",
        "material": "Basswood / Poplar Ply",
        "thickness_mm": 3.0,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "3 mm Basswood / Poplar Ply — CUT",
            "color": "#F02C3D",
            "mode": LayerMode.LINE,
            "speed_mm_min": 300.0,
            "power_percent": 100.0,
            "passes": 5,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 2.5,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-birch-plywood-3mm-cut",
        "material": "Birch Plywood",
        "thickness_mm": 3.0,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "3 mm Birch Plywood — CUT",
            "color": "#FF8A18",
            "mode": LayerMode.LINE,
            "speed_mm_min": 220.0,
            "power_percent": 100.0,
            "passes": 7,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 2.5,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-mdf-3mm-cut",
        "material": "MDF",
        "thickness_mm": 3.0,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "3 mm MDF — CUT",
            "color": "#E5DA19",
            "mode": LayerMode.LINE,
            "speed_mm_min": 180.0,
            "power_percent": 100.0,
            "passes": 8,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 2.5,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-black-acrylic-2mm-cut",
        "material": "Opaque Black Acrylic",
        "thickness_mm": 2.0,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "2 mm Opaque Black Acrylic — CUT",
            "color": "#2DD12D",
            "mode": LayerMode.LINE,
            "speed_mm_min": 180.0,
            "power_percent": 100.0,
            "passes": 8,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 2.5,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-leather-2mm-cut",
        "material": "Vegetable-Tanned Leather",
        "thickness_mm": 2.0,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "2 mm Vegetable-Tanned Leather — CUT",
            "color": "#185CFF",
            "mode": LayerMode.LINE,
            "speed_mm_min": 450.0,
            "power_percent": 100.0,
            "passes": 4,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 2.5,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-cardboard-chipboard-1.5mm-cut",
        "material": "Cardboard / Chipboard",
        "thickness_mm": 1.5,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "1.5 mm Cardboard / Chipboard — CUT",
            "color": "#A982E3",
            "mode": LayerMode.LINE,
            "speed_mm_min": 900.0,
            "power_percent": 85.0,
            "passes": 2,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 2.5,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-basswood-poplar-ply-raster",
        "material": "Basswood / Poplar Ply",
        "thickness_mm": None,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "Basswood / Poplar Ply — RASTER",
            "color": "#F02C3D",
            "mode": LayerMode.RASTER,
            "speed_mm_min": 4000.0,
            "power_percent": 35.0,
            "passes": 1,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 3.0,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-birch-plywood-raster",
        "material": "Birch Plywood",
        "thickness_mm": None,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "Birch Plywood — RASTER",
            "color": "#FF8A18",
            "mode": LayerMode.RASTER,
            "speed_mm_min": 3500.0,
            "power_percent": 32.0,
            "passes": 1,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 3.0,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-mdf-raster",
        "material": "MDF",
        "thickness_mm": None,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "MDF — RASTER",
            "color": "#E5DA19",
            "mode": LayerMode.RASTER,
            "speed_mm_min": 4500.0,
            "power_percent": 22.0,
            "passes": 1,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 3.0,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-black-acrylic-raster",
        "material": "Opaque Black Acrylic",
        "thickness_mm": None,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "Opaque Black Acrylic — RASTER",
            "color": "#2DD12D",
            "mode": LayerMode.RASTER,
            "speed_mm_min": 5000.0,
            "power_percent": 25.0,
            "passes": 1,
            "line_interval_mm": 0.08,
            "scan_angle_deg": 0.0,
            "overscan_percent": 4.0,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-leather-raster",
        "material": "Vegetable-Tanned Leather",
        "thickness_mm": None,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "Vegetable-Tanned Leather — RASTER",
            "color": "#185CFF",
            "mode": LayerMode.RASTER,
            "speed_mm_min": 4500.0,
            "power_percent": 18.0,
            "passes": 1,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 3.0,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
    {
        "builtin_key": "e3-10w-paper-raster",
        "material": "Copy / Printer Paper",
        "thickness_mm": None,
        "machine_profile_id": "ender-3-s1-pro",
        "tool_head_profile_id": "generic-diode-10w",
        "layer": {
            "name": "Copy / Printer Paper — RASTER",
            "color": "#ED23D2",
            "mode": LayerMode.RASTER,
            "speed_mm_min": 6000.0,
            "power_percent": 12.0,
            "passes": 1,
            "line_interval_mm": 0.10,
            "scan_angle_deg": 0.0,
            "overscan_percent": 3.0,
            "vector_power_correction": 0.0,
            "raster_power_correction": 0.0,
            "air_assist": False,
            "output_enabled": True,
            "visible": True,
        },
    },
)


def default_operation_layers() -> list[OperationLayer]:
    """Return fresh E3 10 W starting profiles for a new project."""
    return [
        OperationLayer(priority=index, **profile["layer"])
        for index, profile in enumerate(DEFAULT_OPERATION_PROFILES)
    ]


@dataclass(slots=True)
class ProjectDocument:
    id: str = field(default_factory=lambda: _new_id("project"))
    name: str = "Untitled"
    work_area: Bounds = field(default_factory=lambda: Bounds(0.0, 0.0, 220.0, 220.0))
    coordinate_space: CoordinateSpace = CoordinateSpace.MACHINE
    layers: list[OperationLayer] = field(default_factory=list)
    objects: list[SceneObject] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    modified_at: str = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)
    revision: int = 0

    def __post_init__(self) -> None:
        self.id = str(self.id or _new_id("project"))
        self.name = str(self.name or "Untitled")[:160]
        if not isinstance(self.work_area, Bounds):
            self.work_area = Bounds.from_dict(self.work_area)
        try:
            self.coordinate_space = (
                self.coordinate_space
                if isinstance(self.coordinate_space, CoordinateSpace)
                else CoordinateSpace(str(self.coordinate_space))
            )
        except ValueError as exc:
            raise ProjectFormatError(
                f"Unsupported project coordinate space: {self.coordinate_space!r}"
            ) from exc
        self.layers = [
            layer if isinstance(layer, OperationLayer) else OperationLayer.from_dict(layer)
            for layer in self.layers
        ]
        if not self.layers:
            self.layers = [
                OperationLayer(name="Line 01", color=DEFAULT_LAYER_COLORS[0], priority=0)
            ]
        self.objects = [
            item if isinstance(item, SceneObject) else SceneObject.from_dict(item)
            for item in self.objects
        ]
        if not isinstance(self.metadata, dict):
            raise ProjectFormatError("project.metadata must be a JSON object")
        self.metadata = copy.deepcopy(self.metadata)
        if type(self.revision) is not int:
            raise ProjectFormatError("project.revision must be an integer")
        if self.revision < 0:
            raise ProjectFormatError("project.revision cannot be negative")
        self.validate()

    @classmethod
    def new(
        cls,
        name: str = "Untitled",
        work_area: Bounds | None = None,
        *,
        coordinate_space: CoordinateSpace = CoordinateSpace.MACHINE,
    ) -> ProjectDocument:
        return cls(
            name=name,
            work_area=work_area or Bounds(0.0, 0.0, 220.0, 220.0),
            coordinate_space=coordinate_space,
        )

    @property
    def active_layer_id(self) -> str:
        return self.layers[0].id

    def touch(self) -> None:
        self.modified_at = _utc_now()
        self.revision += 1

    def validate(self) -> None:
        layer_ids = [layer.id for layer in self.layers]
        if len(set(layer_ids)) != len(layer_ids):
            raise ProjectFormatError("Layer IDs must be unique")
        object_ids = [item.id for item in self.objects]
        if len(set(object_ids)) != len(object_ids):
            raise ProjectFormatError("Object IDs must be unique")
        known_layers = set(layer_ids)
        for item in self.objects:
            if item.layer_id not in known_layers:
                raise ProjectFormatError(
                    f"Object {item.id} references unknown layer {item.layer_id}"
                )

    def get_layer(self, layer_id: str) -> OperationLayer:
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        raise KeyError(f"Unknown layer: {layer_id}")

    def get_object(self, object_id: str) -> SceneObject:
        for item in self.objects:
            if item.id == object_id:
                return item
        raise KeyError(f"Unknown object: {object_id}")

    def next_layer_color(self) -> str:
        return DEFAULT_LAYER_COLORS[len(self.layers) % len(DEFAULT_LAYER_COLORS)]

    def add_layer(
        self,
        *,
        name: str | None = None,
        color: str | None = None,
        mode: LayerMode = LayerMode.LINE,
    ) -> OperationLayer:
        index = len(self.layers)
        layer = OperationLayer(
            name=name or f"Layer {index + 1:02d}",
            color=color or self.next_layer_color(),
            mode=mode,
            priority=index,
        )
        self.layers.append(layer)
        self.touch()
        return layer

    def remove_layer(self, layer_id: str, reassign_to: str | None = None) -> OperationLayer:
        if len(self.layers) <= 1:
            raise ValueError("A project must contain at least one layer")
        layer = self.get_layer(layer_id)
        fallback = reassign_to or next(item.id for item in self.layers if item.id != layer_id)
        self.get_layer(fallback)
        for item in self.objects:
            if item.layer_id == layer_id:
                item.layer_id = fallback
        self.layers.remove(layer)
        self.touch()
        return layer

    def add_object(self, item: SceneObject, index: int | None = None) -> SceneObject:
        if item.layer_id not in {layer.id for layer in self.layers}:
            raise ValueError(f"Object references unknown layer {item.layer_id}")
        if any(existing.id == item.id for existing in self.objects):
            raise ValueError(f"Duplicate object ID: {item.id}")
        if index is None:
            self.objects.append(item)
        else:
            self.objects.insert(int(index), item)
        self.touch()
        return item

    def remove_object(self, object_id: str) -> tuple[SceneObject, int]:
        item = self.get_object(object_id)
        index = self.objects.index(item)
        self.objects.pop(index)
        self.touch()
        return item, index

    def duplicate_objects(
        self,
        object_ids: Iterable[str],
        offset_mm: tuple[float, float] = (5.0, -5.0),
    ) -> list[SceneObject]:
        ids = list(dict.fromkeys(object_ids))
        originals = [self.get_object(object_id) for object_id in ids]
        group_map = {
            item.group_id: _new_id("group")
            for item in originals
            if item.group_id is not None
        }
        duplicates: list[SceneObject] = []
        for item in originals:
            duplicate = item.duplicate(
                offset_mm,
                group_id=group_map.get(item.group_id),
            )
            self.objects.append(duplicate)
            duplicates.append(duplicate)
        if duplicates:
            self.touch()
        return duplicates

    def group_members(self, group_id: str | None) -> list[SceneObject]:
        if group_id is None:
            return []
        return [item for item in self.objects if item.group_id == group_id]

    def assign_layer(self, object_ids: Iterable[str], layer_id: str) -> None:
        self.get_layer(layer_id)
        changed = False
        for object_id in object_ids:
            item = self.get_object(object_id)
            if item.layer_id != layer_id:
                item.layer_id = layer_id
                changed = True
        if changed:
            self.touch()

    def update_transform(self, object_id: str, transform: Transform) -> Transform:
        item = self.get_object(object_id)
        previous = item.transform
        item.transform = transform
        self.touch()
        return previous

    def visible_output_objects(self) -> list[SceneObject]:
        layer_by_id = {layer.id: layer for layer in self.layers}
        return [
            item
            for item in self.objects
            if item.visible
            and item.is_output_geometry
            and layer_by_id[item.layer_id].visible
            and layer_by_id[item.layer_id].output_enabled
        ]

    def document_bounds(self) -> Bounds | None:
        visible = [item.bounds() for item in self.objects if item.visible]
        if not visible:
            return None
        output = visible[0]
        for bounds in visible[1:]:
            output = output.union(bounds)
        return output

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "id": self.id,
            "name": self.name,
            "work_area": self.work_area.to_dict(),
            "coordinate_space": self.coordinate_space.value,
            "layers": [layer.to_dict() for layer in self.layers],
            "objects": [item.to_dict() for item in self.objects],
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "metadata": copy.deepcopy(self.metadata),
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ProjectDocument:
        raw = _object(raw, "project")
        schema = raw.get("schema_version", 0)
        if type(schema) is not int:
            raise ProjectFormatError("Project schema_version must be an integer")
        if schema not in (1, PROJECT_SCHEMA_VERSION):
            raise ProjectFormatError(
                f"Unsupported project schema {schema}; expected 1 or {PROJECT_SCHEMA_VERSION}"
            )
        try:
            work_area = _object(raw["work_area"], "project.work_area")
            layers = _array(raw["layers"], "project.layers")
            objects = _array(raw.get("objects", []), "project.objects")
            metadata = _object(raw.get("metadata", {}), "project.metadata")
            return cls(
                id=_string(raw.get("id", _new_id("project")), "project.id"),
                name=_string(raw.get("name", "Untitled"), "project.name"),
                work_area=Bounds.from_dict(work_area),
                # Schema 1 projects predate movable-support coordinates. They
                # were authored directly in the machine frame and must never
                # be silently reinterpreted as honeycomb-local geometry.
                coordinate_space=(
                    CoordinateSpace.MACHINE
                    if schema == 1
                    else CoordinateSpace(
                        _string(
                            raw["coordinate_space"],
                            "project.coordinate_space",
                        )
                    )
                ),
                layers=[OperationLayer.from_dict(item) for item in layers],
                objects=[SceneObject.from_dict(item) for item in objects],
                created_at=_string(
                    raw.get("created_at", _utc_now()),
                    "project.created_at",
                ),
                modified_at=_string(
                    raw.get("modified_at", _utc_now()),
                    "project.modified_at",
                ),
                metadata=copy.deepcopy(dict(metadata)),
                revision=raw.get("revision", 0),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ProjectFormatError):
                raise
            raise ProjectFormatError(f"Invalid project structure: {exc}") from exc

    def clone(self) -> ProjectDocument:
        return ProjectDocument.from_dict(self.to_dict())
