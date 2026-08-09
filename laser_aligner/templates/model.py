from __future__ import annotations

import copy
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from ..project import (
    Bounds,
    ObjectKind,
    ProjectDocument,
    ProjectFormatError,
    SceneObject,
)

TEMPLATE_SCHEMA_VERSION = 1
TEMPLATE_EXTENSION = ".e3template"


class TemplateFormatError(ValueError):
    """Raised when a reusable cutting template is malformed or unsupported."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TemplateFormatError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise TemplateFormatError(f"{name} must be a finite number")
    return number


def _positive(value: Any, name: str) -> float:
    number = _finite(value, name)
    if number <= 0.0:
        raise TemplateFormatError(f"{name} must be positive")
    return number


def _json_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TemplateFormatError(f"{name} must be a JSON object")
    result = copy.deepcopy(dict(value))
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TemplateFormatError(f"{name} must contain only JSON-compatible values") from exc
    return result


def _timestamp(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise TemplateFormatError(f"{name} must not be empty")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TemplateFormatError(f"{name} must be an ISO-8601 timestamp") from exc
    return text


def _normalized_rotation(value: Any) -> float:
    rotation = _finite(value, "feature.rotation_deg")
    return (rotation + 180.0) % 360.0 - 180.0


def _polygon_area(points: Sequence[tuple[float, float]]) -> float:
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, (*points[1:], points[0]), strict=True)
    )


def _point_on_segment(
    point: tuple[float, float],
    first: tuple[float, float],
    second: tuple[float, float],
    tolerance: float = 1e-9,
) -> bool:
    delta_x = second[0] - first[0]
    delta_y = second[1] - first[1]
    cross = (point[0] - first[0]) * delta_y - (point[1] - first[1]) * delta_x
    if abs(cross) > tolerance * max(1.0, abs(delta_x), abs(delta_y)):
        return False
    dot = (point[0] - first[0]) * delta_x + (point[1] - first[1]) * delta_y
    if dot < -tolerance:
        return False
    return dot <= delta_x * delta_x + delta_y * delta_y + tolerance


def _point_in_polygon(
    point: tuple[float, float],
    polygon: Sequence[tuple[float, float]],
) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _point_on_segment(point, previous, current):
            return True
        crosses = (current[1] > point[1]) != (previous[1] > point[1])
        if crosses:
            intersection_x = (
                (previous[0] - current[0])
                * (point[1] - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if point[0] < intersection_x:
                inside = not inside
        previous = current
    return inside


def _polygon_bounds(
    points: Sequence[tuple[float, float]],
) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _polygon_contains(
    outer: Sequence[tuple[float, float]],
    inner: Sequence[tuple[float, float]],
) -> bool:
    outer_bounds = _polygon_bounds(outer)
    inner_bounds = _polygon_bounds(inner)
    tolerance = 1e-9
    if not (
        outer_bounds[0] <= inner_bounds[0] + tolerance
        and outer_bounds[1] <= inner_bounds[1] + tolerance
        and outer_bounds[2] >= inner_bounds[2] - tolerance
        and outer_bounds[3] >= inner_bounds[3] - tolerance
    ):
        return False
    return all(_point_in_polygon(point, outer) for point in inner)


@dataclass(slots=True, frozen=True)
class TemplateFeature:
    """A camera-matchable geometric feature in template-local millimetres."""

    center_mm: tuple[float, float]
    width_mm: float
    height_mm: float
    rotation_deg: float = 0.0
    object_id: str = ""
    kind: str = ""

    def __post_init__(self) -> None:
        center = self.center_mm
        if not isinstance(center, Sequence) or isinstance(center, (str, bytes)) or len(center) != 2:
            raise TemplateFormatError("feature.center_mm must contain x and y")
        object.__setattr__(
            self,
            "center_mm",
            (
                _finite(center[0], "feature.center_mm[0]"),
                _finite(center[1], "feature.center_mm[1]"),
            ),
        )
        object.__setattr__(self, "width_mm", _positive(self.width_mm, "feature.width_mm"))
        object.__setattr__(self, "height_mm", _positive(self.height_mm, "feature.height_mm"))
        object.__setattr__(self, "rotation_deg", _normalized_rotation(self.rotation_deg))
        object.__setattr__(self, "object_id", str(self.object_id))
        object.__setattr__(self, "kind", str(self.kind))

    @property
    def center_x_mm(self) -> float:
        return self.center_mm[0]

    @property
    def center_y_mm(self) -> float:
        return self.center_mm[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "center_mm": [self.center_mm[0], self.center_mm[1]],
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "rotation_deg": self.rotation_deg,
            "object_id": self.object_id,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TemplateFeature:
        if not isinstance(raw, Mapping):
            raise TemplateFormatError("Template feature must be a JSON object")
        try:
            return cls(
                center_mm=tuple(raw["center_mm"]),
                width_mm=raw["width_mm"],
                height_mm=raw["height_mm"],
                rotation_deg=raw.get("rotation_deg", 0.0),
                object_id=str(raw.get("object_id", "")),
                kind=str(raw.get("kind", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, TemplateFormatError):
                raise
            raise TemplateFormatError(f"Invalid template feature: {exc}") from exc


def _object_feature(item: SceneObject) -> TemplateFeature:
    return TemplateFeature(
        center_mm=(item.transform.x_mm, item.transform.y_mm),
        width_mm=item.transform.width_mm,
        height_mm=item.transform.height_mm,
        rotation_deg=item.transform.rotation_deg,
        object_id=item.id,
        kind=item.kind.value,
    )


def _closed_component_polygons(
    item: SceneObject,
) -> list[list[tuple[float, float]]]:
    if item.kind not in {ObjectKind.PATH, ObjectKind.POLYGON}:
        return []
    transform = item.transform
    mirror_x = -1.0 if transform.mirror_x else 1.0
    mirror_y = -1.0 if transform.mirror_y else 1.0
    polygons: list[list[tuple[float, float]]] = []
    for line in item.geometry.get("polylines", []):
        if not bool(line.get("closed", False)):
            continue
        points = [
            (
                float(point[0]) * transform.width_mm * mirror_x,
                float(point[1]) * transform.height_mm * mirror_y,
            )
            for point in line.get("points", [])
        ]
        if len(points) > 1 and math.isclose(points[0][0], points[-1][0], abs_tol=1e-9) and math.isclose(
            points[0][1], points[-1][1], abs_tol=1e-9
        ):
            points.pop()
        if len(points) < 3 or abs(_polygon_area(points)) <= 1e-9:
            continue
        polygons.append(points)
    return polygons


def _component_features(item: SceneObject) -> list[TemplateFeature]:
    polygons = _closed_component_polygons(item)
    outer_components: list[list[tuple[float, float]]] = []
    areas = [abs(_polygon_area(points)) for points in polygons]
    for index, points in enumerate(polygons):
        contained = any(
            other_index != index
            and areas[other_index] > areas[index] + 1e-9
            and _polygon_contains(other, points)
            for other_index, other in enumerate(polygons)
        )
        if not contained:
            outer_components.append(points)

    # A single component has no grid information beyond the SceneObject's
    # normal feature, which also remains the most stable representation for
    # arbitrary paths. Split only when independent repeated components exist.
    if len(outer_components) <= 1:
        return []

    angle = math.radians(item.transform.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    features: list[TemplateFeature] = []
    for points in outer_components:
        x_min, y_min, x_max, y_max = _polygon_bounds(points)
        width = x_max - x_min
        height = y_max - y_min
        if width <= 1e-9 or height <= 1e-9:
            continue
        local_x = (x_min + x_max) / 2.0
        local_y = (y_min + y_max) / 2.0
        features.append(
            TemplateFeature(
                center_mm=(
                    item.transform.x_mm + local_x * cosine - local_y * sine,
                    item.transform.y_mm + local_x * sine + local_y * cosine,
                ),
                width_mm=width,
                height_mm=height,
                rotation_deg=item.transform.rotation_deg,
                object_id=item.id,
                kind=item.kind.value,
            )
        )
    return features if len(features) > 1 else []


@dataclass(slots=True)
class CutTemplate:
    """Versioned, reusable cut geometry expressed around a local origin."""

    name: str
    bounds: Bounds
    objects: list[SceneObject]
    features: list[TemplateFeature]
    id: str = field(default_factory=lambda: _new_id("template"))
    description: str = ""
    trace_options: dict[str, Any] = field(default_factory=dict)
    marker_id: str | int | None = None
    created_at: str = field(default_factory=_utc_now)
    modified_at: str = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    schema_version: ClassVar[int] = TEMPLATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.id = str(self.id).strip()
        if not self.id:
            raise TemplateFormatError("Template ID must not be empty")
        self.name = str(self.name).strip()
        if not self.name:
            raise TemplateFormatError("Template name must not be empty")
        self.description = str(self.description)

        if not isinstance(self.bounds, Bounds):
            try:
                self.bounds = Bounds.from_dict(self.bounds)
            except (KeyError, TypeError, ValueError, ProjectFormatError) as exc:
                raise TemplateFormatError(f"Invalid template bounds: {exc}") from exc
        if self.bounds.width <= 0.0 or self.bounds.height <= 0.0:
            raise TemplateFormatError("Template bounds must have positive width and height")
        if not math.isclose(self.bounds.center[0], 0.0, abs_tol=1e-9) or not math.isclose(
            self.bounds.center[1], 0.0, abs_tol=1e-9
        ):
            raise TemplateFormatError("Template bounds must be normalized around (0, 0)")

        if not isinstance(self.objects, list) or not self.objects:
            raise TemplateFormatError("Template must contain at least one cut object")
        cloned_objects: list[SceneObject] = []
        try:
            for item in self.objects:
                source = item.to_dict() if isinstance(item, SceneObject) else item
                cloned_objects.append(SceneObject.from_dict(source))
        except (TypeError, ValueError, ProjectFormatError) as exc:
            raise TemplateFormatError(f"Invalid template object: {exc}") from exc
        self.objects = cloned_objects

        if not isinstance(self.features, list) or not self.features:
            raise TemplateFormatError("Template must contain at least one matching feature")
        self.features = [
            item if isinstance(item, TemplateFeature) else TemplateFeature.from_dict(item)
            for item in self.features
        ]

        object_ids = [item.id for item in self.objects]
        if len(object_ids) != len(set(object_ids)):
            raise TemplateFormatError("Template object IDs must be unique")
        known_ids = set(object_ids)
        referenced_ids = [feature.object_id for feature in self.features if feature.object_id]
        if any(object_id not in known_ids for object_id in referenced_ids):
            raise TemplateFormatError("Template feature references an unknown object")

        self.trace_options = _json_object(self.trace_options, "trace_options")
        self.metadata = _json_object(self.metadata, "metadata")
        if self.marker_id is not None:
            if isinstance(self.marker_id, bool):
                raise TemplateFormatError("marker_id must be a string, integer, or null")
            if isinstance(self.marker_id, int):
                if self.marker_id < 0:
                    raise TemplateFormatError("marker_id integer must not be negative")
            else:
                self.marker_id = str(self.marker_id).strip()
                if not self.marker_id:
                    self.marker_id = None
        self.created_at = _timestamp(self.created_at, "created_at")
        self.modified_at = _timestamp(self.modified_at, "modified_at")

    @property
    def width_mm(self) -> float:
        return self.bounds.width

    @property
    def height_mm(self) -> float:
        return self.bounds.height

    @property
    def size_mm(self) -> tuple[float, float]:
        return (self.width_mm, self.height_mm)

    def touch(self) -> None:
        self.modified_at = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TEMPLATE_SCHEMA_VERSION,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "bounds": self.bounds.to_dict(),
            "size_mm": {"width": self.width_mm, "height": self.height_mm},
            "objects": [item.to_dict() for item in self.objects],
            "features": [feature.to_dict() for feature in self.features],
            "trace_options": copy.deepcopy(self.trace_options),
            "marker_id": self.marker_id,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "metadata": copy.deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CutTemplate:
        if not isinstance(raw, Mapping):
            raise TemplateFormatError("Template root must be a JSON object")
        schema = raw.get("schema_version", 0)
        if type(schema) is not int:
            raise TemplateFormatError("Template schema_version must be an integer")
        if schema != TEMPLATE_SCHEMA_VERSION:
            raise TemplateFormatError(
                f"Unsupported template schema {schema}; expected {TEMPLATE_SCHEMA_VERSION}"
            )
        try:
            size = raw["size_mm"]
            if not isinstance(size, Mapping):
                raise TemplateFormatError("size_mm must be a JSON object")
            bounds = Bounds.from_dict(raw["bounds"])
            width = _positive(size["width"], "size_mm.width")
            height = _positive(size["height"], "size_mm.height")
            if not math.isclose(bounds.width, width, abs_tol=1e-9) or not math.isclose(
                bounds.height, height, abs_tol=1e-9
            ):
                raise TemplateFormatError("Template size_mm does not match bounds")
            return cls(
                id=str(raw["id"]),
                name=str(raw["name"]),
                description=str(raw.get("description", "")),
                bounds=bounds,
                objects=[SceneObject.from_dict(item) for item in raw["objects"]],
                features=[TemplateFeature.from_dict(item) for item in raw["features"]],
                trace_options=raw.get("trace_options", {}),
                marker_id=raw.get("marker_id"),
                created_at=str(raw["created_at"]),
                modified_at=str(raw["modified_at"]),
                metadata=raw.get("metadata", {}),
            )
        except (KeyError, TypeError, ValueError, OverflowError, ProjectFormatError) as exc:
            if isinstance(exc, TemplateFormatError):
                raise
            raise TemplateFormatError(f"Invalid template structure: {exc}") from exc


def template_from_project(
    document: ProjectDocument,
    name: str,
    *,
    description: str = "",
    trace_options: Mapping[str, Any] | None = None,
    marker_id: str | int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> CutTemplate:
    """Clone visible output objects into a template-local coordinate system."""

    visible = document.visible_output_objects()
    if not visible:
        raise TemplateFormatError("Cannot create a cutting template from an empty project")

    object_bounds = visible[0].bounds()
    for item in visible[1:]:
        object_bounds = object_bounds.union(item.bounds())
    origin_x, origin_y = object_bounds.center

    objects: list[SceneObject] = []
    features: list[TemplateFeature] = []
    for source in visible:
        item = SceneObject.from_dict(source.to_dict())
        item.transform = item.transform.copy(
            x_mm=item.transform.x_mm - origin_x,
            y_mm=item.transform.y_mm - origin_y,
        )
        objects.append(item)
        features.extend(_component_features(item) or [_object_feature(item)])

    template_metadata = {
        "source_project_id": document.id,
        "source_project_name": document.name,
    }
    if metadata:
        template_metadata.update(dict(metadata))
    return CutTemplate(
        name=name,
        description=description,
        bounds=Bounds(
            -object_bounds.width / 2.0,
            -object_bounds.height / 2.0,
            object_bounds.width / 2.0,
            object_bounds.height / 2.0,
        ),
        objects=objects,
        features=features,
        trace_options={} if trace_options is None else dict(trace_options),
        marker_id=marker_id,
        metadata=template_metadata,
    )


def instantiate_template(
    template: CutTemplate,
    target_x_mm: float,
    target_y_mm: float,
    rotation_deg: float = 0.0,
    target_layer_id: str = "",
) -> list[SceneObject]:
    """Place template objects rigidly at a target center without applying scale."""

    target_x = _finite(target_x_mm, "target_x_mm")
    target_y = _finite(target_y_mm, "target_y_mm")
    rotation = _finite(rotation_deg, "rotation_deg")
    layer_id = str(target_layer_id)
    if not layer_id:
        raise TemplateFormatError("target_layer_id must not be empty")

    angle = math.radians(rotation)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    group_ids = {
        item.group_id: _new_id("group")
        for item in template.objects
        if item.group_id is not None
    }
    output: list[SceneObject] = []
    for source in template.objects:
        item = SceneObject.from_dict(source.to_dict())
        local_x = item.transform.x_mm
        local_y = item.transform.y_mm
        item.id = _new_id("object")
        item.layer_id = layer_id
        item.group_id = group_ids.get(item.group_id)
        item.transform = item.transform.copy(
            x_mm=target_x + local_x * cosine - local_y * sine,
            y_mm=target_y + local_x * sine + local_y * cosine,
            rotation_deg=item.transform.rotation_deg + rotation,
        )
        output.append(item)
    return output
