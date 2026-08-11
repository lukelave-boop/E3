from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any

from ..project import Bounds, SceneObject
from .model import CutTemplate, TemplateFeature, TemplateFormatError
from .shapes import ShapeKind, build_shape_object, semantic_shape_kind

GRID_AUTHORING_METADATA_KEY = "authoring"
GRID_AUTHORING_KIND = "shape_grid"
GRID_AUTHORING_VERSION = 1
LEGACY_GRID_AUTHORING_KIND = "rectangle_grid"
GRID_CELL_METADATA_KEY = "shape_grid_cell"
LEGACY_GRID_CELL_METADATA_KEY = "rectangle_grid_cell"
MAX_GRID_OBJECTS = 500
MIN_GRID_DIMENSION_MM = 0.001


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise TemplateFormatError(f"{name} must be a finite number")
    return float(value)


def _count(value: Any, name: str, minimum: int = 1, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TemplateFormatError(f"{name} must be an integer of at least {minimum}")
    if maximum is not None and value > maximum:
        raise TemplateFormatError(f"{name} must not exceed {maximum}")
    return value


@dataclass(slots=True, frozen=True)
class ShapeGridSpec:
    name: str
    rows: int
    columns: int
    width_mm: float
    height_mm: float
    shape_kind: ShapeKind | str = ShapeKind.ROUNDED_RECTANGLE
    corner_radius_mm: float = 0.0
    horizontal_gap_mm: float = 0.0
    vertical_gap_mm: float = 0.0
    description: str = ""
    polygon_sides: int = 6
    star_points: int = 5
    star_inner_ratio: float = 0.5
    flat_distance_mm: float | None = None
    inner_diameter_mm: float | None = None
    shape_rotation_deg: float = 0.0

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise TemplateFormatError("name must not be empty")
        rows, columns = _count(self.rows, "rows"), _count(self.columns, "columns")
        if rows * columns > MAX_GRID_OBJECTS:
            raise TemplateFormatError(f"Shape grid cannot contain more than {MAX_GRID_OBJECTS} objects")
        width, height = _finite(self.width_mm, "width_mm"), _finite(self.height_mm, "height_mm")
        if width < MIN_GRID_DIMENSION_MM or height < MIN_GRID_DIMENSION_MM:
            raise TemplateFormatError(f"cell dimensions must be at least {MIN_GRID_DIMENSION_MM:g} mm")
        try:
            kind = ShapeKind(self.shape_kind)
        except ValueError as exc:
            raise TemplateFormatError(f"Unsupported shape_kind {self.shape_kind!r}") from exc
        radius = _finite(self.corner_radius_mm, "corner_radius_mm")
        if radius < 0:
            raise TemplateFormatError("corner_radius_mm must not be negative")
        if radius > min(width, height) / 2:
            raise TemplateFormatError("corner_radius_mm must not exceed half the smaller cell dimension")
        gap_x, gap_y = (
            _finite(self.horizontal_gap_mm, "horizontal_gap_mm"),
            _finite(self.vertical_gap_mm, "vertical_gap_mm"),
        )
        if gap_x < 0 or gap_y < 0:
            raise TemplateFormatError("grid gaps must not be negative")
        if not math.isfinite(columns * width + (columns - 1) * gap_x) or not math.isfinite(
            rows * height + (rows - 1) * gap_y
        ):
            raise TemplateFormatError("Shape grid footprint must be finite")
        sides = _count(self.polygon_sides, "polygon_sides", 3, 12)
        points = _count(self.star_points, "star_points", 3, 12)
        ratio = _finite(self.star_inner_ratio, "star_inner_ratio")
        if not 0.05 <= ratio < 1.0:
            raise TemplateFormatError("star_inner_ratio must be at least 0.05 and less than 1")
        flat = None if self.flat_distance_mm is None else _finite(self.flat_distance_mm, "flat_distance_mm")
        if kind in {ShapeKind.CIRCLE_ONE_FLAT, ShapeKind.CIRCLE_TWO_FLATS}:
            if flat is None or not 0 < flat <= width:
                raise TemplateFormatError("flat_distance_mm must be positive and no greater than diameter")
        inner = None if self.inner_diameter_mm is None else _finite(self.inner_diameter_mm, "inner_diameter_mm")
        if kind == ShapeKind.WASHER and (inner is None or not 0 < inner < width):
            raise TemplateFormatError("inner_diameter_mm must be positive and smaller than outer diameter")
        rotation = _finite(self.shape_rotation_deg, "shape_rotation_deg")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "width_mm", width)
        object.__setattr__(self, "height_mm", height)
        object.__setattr__(self, "shape_kind", kind)
        object.__setattr__(self, "corner_radius_mm", radius)
        object.__setattr__(self, "horizontal_gap_mm", gap_x)
        object.__setattr__(self, "vertical_gap_mm", gap_y)
        object.__setattr__(self, "polygon_sides", sides)
        object.__setattr__(self, "star_points", points)
        object.__setattr__(self, "star_inner_ratio", ratio)
        object.__setattr__(self, "flat_distance_mm", flat)
        object.__setattr__(self, "inner_diameter_mm", inner)
        object.__setattr__(self, "shape_rotation_deg", rotation)
        object.__setattr__(self, "description", str(self.description))

    @property
    def count(self) -> int:
        return self.rows * self.columns

    @property
    def object_count(self) -> int:
        return self.count

    @property
    def horizontal_pitch_mm(self) -> float:
        return self.width_mm + self.horizontal_gap_mm

    @property
    def vertical_pitch_mm(self) -> float:
        return self.height_mm + self.vertical_gap_mm

    @property
    def pitch_x_mm(self) -> float:
        return self.horizontal_pitch_mm

    @property
    def pitch_y_mm(self) -> float:
        return self.vertical_pitch_mm

    @property
    def footprint_width_mm(self) -> float:
        return self.columns * self.width_mm + (self.columns - 1) * self.horizontal_gap_mm

    @property
    def footprint_height_mm(self) -> float:
        return self.rows * self.height_mm + (self.rows - 1) * self.vertical_gap_mm

    @property
    def footprint_size_mm(self) -> tuple[float, float]:
        return self.footprint_width_mm, self.footprint_height_mm

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "rows": self.rows,
            "columns": self.columns,
            "shape_kind": self.shape_kind.value,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "corner_radius_mm": self.corner_radius_mm,
            "horizontal_gap_mm": self.horizontal_gap_mm,
            "vertical_gap_mm": self.vertical_gap_mm,
            "polygon_sides": self.polygon_sides,
            "star_points": self.star_points,
            "star_inner_ratio": self.star_inner_ratio,
            "flat_distance_mm": self.flat_distance_mm,
            "inner_diameter_mm": self.inner_diameter_mm,
            "shape_rotation_deg": self.shape_rotation_deg,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ShapeGridSpec:
        try:
            return cls(
                **{key: raw[key] for key in ("name", "rows", "columns", "width_mm", "height_mm")},
                description=raw.get("description", ""),
                shape_kind=raw.get("shape_kind", ShapeKind.ROUNDED_RECTANGLE),
                corner_radius_mm=raw.get("corner_radius_mm", 0.0),
                horizontal_gap_mm=raw.get("horizontal_gap_mm", 0.0),
                vertical_gap_mm=raw.get("vertical_gap_mm", 0.0),
                polygon_sides=raw.get("polygon_sides", 6),
                star_points=raw.get("star_points", 5),
                star_inner_ratio=raw.get("star_inner_ratio", 0.5),
                flat_distance_mm=raw.get("flat_distance_mm"),
                inner_diameter_mm=raw.get("inner_diameter_mm"),
                shape_rotation_deg=raw.get("shape_rotation_deg", 0.0),
            )
        except KeyError as exc:
            raise TemplateFormatError(f"Shape-grid parameters are missing {exc.args[0]!r}") from exc

    def to_authoring_metadata(self) -> dict[str, Any]:
        return {
            "kind": GRID_AUTHORING_KIND,
            "version": GRID_AUTHORING_VERSION,
            **self.to_dict(),
            "ordering": "row-major-top-left",
        }

    @classmethod
    def from_authoring_metadata(cls, raw: Mapping[str, Any]) -> ShapeGridSpec:
        kind, version = str(raw.get("kind", "")), raw.get("version")
        if version != 1 or kind not in {GRID_AUTHORING_KIND, LEGACY_GRID_AUTHORING_KIND}:
            raise TemplateFormatError("Unsupported grid authoring metadata")
        values = dict(raw)
        if kind == LEGACY_GRID_AUTHORING_KIND:
            values["shape_kind"] = ShapeKind.ROUNDED_RECTANGLE.value
        return cls.from_dict(values)

    @classmethod
    def from_template(cls, template: CutTemplate) -> ShapeGridSpec:
        raw = template.metadata.get(GRID_AUTHORING_METADATA_KEY)
        if not isinstance(raw, Mapping):
            raise TemplateFormatError("Template has no grid authoring metadata")
        return cls.from_authoring_metadata(raw)


# Public compatibility name retained for plugins/tests and old callers.
RectangleGridSpec = ShapeGridSpec


def _existing_cell_ids(template: CutTemplate | None) -> dict[tuple[int, int], str]:
    output: dict[tuple[int, int], str] = {}
    if template:
        for item in template.objects:
            raw = item.metadata.get(GRID_CELL_METADATA_KEY, item.metadata.get(LEGACY_GRID_CELL_METADATA_KEY))
            if isinstance(raw, Mapping) and isinstance(raw.get("row"), int) and isinstance(raw.get("column"), int):
                output.setdefault((raw["row"], raw["column"]), item.id)
    return output


def build_shape_grid_objects(
    spec: ShapeGridSpec, *, layer_id: str = "template-grid", existing: CutTemplate | None = None
) -> list[SceneObject]:
    if not isinstance(spec, ShapeGridSpec):
        raise TypeError("spec must be a ShapeGridSpec")
    if not str(layer_id).strip():
        raise TemplateFormatError("layer_id must not be empty")
    ids, objects = _existing_cell_ids(existing), []
    first_x = -spec.footprint_width_mm / 2 + spec.width_mm / 2
    first_y = spec.footprint_height_mm / 2 - spec.height_mm / 2
    for row in range(spec.rows):
        for column in range(spec.columns):
            item = build_shape_object(
                layer_id,
                name=f"Label {row + 1}, {column + 1}"
                if spec.shape_kind == ShapeKind.ROUNDED_RECTANGLE
                else f"Cut {row + 1}, {column + 1}",
                center=(first_x + column * spec.horizontal_pitch_mm, first_y - row * spec.vertical_pitch_mm),
                shape_kind=spec.shape_kind,
                width_mm=spec.width_mm,
                height_mm=spec.height_mm,
                corner_radius_mm=spec.corner_radius_mm,
                polygon_sides=spec.polygon_sides,
                star_points=spec.star_points,
                star_inner_ratio=spec.star_inner_ratio,
                flat_distance_mm=spec.flat_distance_mm,
                inner_diameter_mm=spec.inner_diameter_mm,
                rotation_deg=spec.shape_rotation_deg,
            )
            item.id = ids.get((row, column), item.id)
            item.metadata[GRID_CELL_METADATA_KEY] = {
                "version": 1,
                "row": row,
                "column": column,
                "shape_kind": spec.shape_kind.value,
            }
            objects.append(item)
    return objects


build_rectangle_grid_objects = build_shape_grid_objects


def template_from_shape_grid(
    spec: ShapeGridSpec, *, trace_options: Mapping[str, Any] | None = None, existing: CutTemplate | None = None
) -> CutTemplate:
    if existing is not None:
        ShapeGridSpec.from_template(existing)
    objects = build_shape_grid_objects(spec, existing=existing)
    features = [
        TemplateFeature(
            center_mm=(item.transform.x_mm, item.transform.y_mm),
            width_mm=item.transform.width_mm,
            height_mm=item.transform.height_mm,
            rotation_deg=item.transform.rotation_deg,
            object_id=item.id,
            kind=semantic_shape_kind(item),
            descriptor={
                "polygon_sides": spec.polygon_sides,
                "star_points": spec.star_points,
                "star_inner_ratio": spec.star_inner_ratio,
                "flat_distance_mm": spec.flat_distance_mm,
                "hole_ratio": (
                    spec.inner_diameter_mm / spec.width_mm
                    if spec.shape_kind == ShapeKind.WASHER
                    else None
                ),
            },
        )
        for item in objects
    ]
    metadata = dict(existing.metadata) if existing else {}
    metadata[GRID_AUTHORING_METADATA_KEY] = spec.to_authoring_metadata()
    identity = {"id": existing.id, "created_at": existing.created_at} if existing else {}
    return CutTemplate(
        name=spec.name,
        description=spec.description,
        bounds=Bounds(
            -spec.footprint_width_mm / 2,
            -spec.footprint_height_mm / 2,
            spec.footprint_width_mm / 2,
            spec.footprint_height_mm / 2,
        ),
        objects=objects,
        features=features,
        trace_options=dict(existing.trace_options if existing and trace_options is None else trace_options or {}),
        marker_id=existing.marker_id if existing else None,
        metadata=metadata,
        **identity,
    )


template_from_rectangle_grid = template_from_shape_grid
