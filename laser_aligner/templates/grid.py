from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..project import Bounds, ObjectKind, SceneObject
from .model import CutTemplate, TemplateFeature, TemplateFormatError

GRID_AUTHORING_METADATA_KEY = "authoring"
GRID_AUTHORING_KIND = "rectangle_grid"
GRID_AUTHORING_VERSION = 1
GRID_CELL_METADATA_KEY = "rectangle_grid_cell"
MAX_GRID_OBJECTS = 500
MIN_GRID_DIMENSION_MM = 0.001


def _strict_count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TemplateFormatError(f"{name} must be an integer")
    if value < 1:
        raise TemplateFormatError(f"{name} must be at least one")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise TemplateFormatError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TemplateFormatError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise TemplateFormatError(f"{name} must be a finite number")
    return number


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TemplateFormatError(f"{name} must be a JSON object")
    return value


@dataclass(slots=True, frozen=True)
class RectangleGridSpec:
    """Portable authoring parameters for a centered grid of rounded rectangles.

    Gaps are edge-to-edge distances.  Center pitch and overall footprint are
    derived so the persisted recipe cannot contain conflicting measurements.
    """

    name: str
    rows: int
    columns: int
    width_mm: float
    height_mm: float
    corner_radius_mm: float = 0.0
    horizontal_gap_mm: float = 0.0
    vertical_gap_mm: float = 0.0
    description: str = ""

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise TemplateFormatError("name must not be empty")
        rows = _strict_count(self.rows, "rows")
        columns = _strict_count(self.columns, "columns")
        if rows * columns > MAX_GRID_OBJECTS:
            raise TemplateFormatError(
                f"Rectangle grid cannot contain more than {MAX_GRID_OBJECTS} objects"
            )

        width = _finite(self.width_mm, "width_mm")
        height = _finite(self.height_mm, "height_mm")
        if width < MIN_GRID_DIMENSION_MM:
            raise TemplateFormatError(
                f"width_mm must be at least {MIN_GRID_DIMENSION_MM:g}"
            )
        if height < MIN_GRID_DIMENSION_MM:
            raise TemplateFormatError(
                f"height_mm must be at least {MIN_GRID_DIMENSION_MM:g}"
            )

        radius = _finite(self.corner_radius_mm, "corner_radius_mm")
        if radius < 0.0:
            raise TemplateFormatError("corner_radius_mm must not be negative")
        maximum_radius = min(width, height) / 2.0
        if radius > maximum_radius:
            raise TemplateFormatError(
                "corner_radius_mm must not exceed half the smaller cell dimension"
            )

        horizontal_gap = _finite(self.horizontal_gap_mm, "horizontal_gap_mm")
        vertical_gap = _finite(self.vertical_gap_mm, "vertical_gap_mm")
        if horizontal_gap < 0.0:
            raise TemplateFormatError("horizontal_gap_mm must not be negative")
        if vertical_gap < 0.0:
            raise TemplateFormatError("vertical_gap_mm must not be negative")

        footprint_width = columns * width + (columns - 1) * horizontal_gap
        footprint_height = rows * height + (rows - 1) * vertical_gap
        if not math.isfinite(footprint_width) or not math.isfinite(footprint_height):
            raise TemplateFormatError("Rectangle grid footprint must be finite")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "columns", columns)
        object.__setattr__(self, "width_mm", width)
        object.__setattr__(self, "height_mm", height)
        object.__setattr__(self, "corner_radius_mm", radius)
        object.__setattr__(self, "horizontal_gap_mm", horizontal_gap)
        object.__setattr__(self, "vertical_gap_mm", vertical_gap)
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
        return (
            self.columns * self.width_mm
            + (self.columns - 1) * self.horizontal_gap_mm
        )

    @property
    def footprint_height_mm(self) -> float:
        return (
            self.rows * self.height_mm
            + (self.rows - 1) * self.vertical_gap_mm
        )

    @property
    def footprint_size_mm(self) -> tuple[float, float]:
        return self.footprint_width_mm, self.footprint_height_mm

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "rows": self.rows,
            "columns": self.columns,
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "corner_radius_mm": self.corner_radius_mm,
            "horizontal_gap_mm": self.horizontal_gap_mm,
            "vertical_gap_mm": self.vertical_gap_mm,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RectangleGridSpec:
        values = _mapping(raw, "rectangle-grid parameters")
        try:
            return cls(
                name=values["name"],
                description=values.get("description", ""),
                rows=values["rows"],
                columns=values["columns"],
                width_mm=values["width_mm"],
                height_mm=values["height_mm"],
                corner_radius_mm=values.get("corner_radius_mm", 0.0),
                horizontal_gap_mm=values.get("horizontal_gap_mm", 0.0),
                vertical_gap_mm=values.get("vertical_gap_mm", 0.0),
            )
        except KeyError as exc:
            raise TemplateFormatError(
                f"Rectangle-grid parameters are missing {exc.args[0]!r}"
            ) from exc

    def to_authoring_metadata(self) -> dict[str, Any]:
        return {
            "kind": GRID_AUTHORING_KIND,
            "version": GRID_AUTHORING_VERSION,
            **self.to_dict(),
            "ordering": "row-major-top-left",
        }

    @classmethod
    def from_authoring_metadata(cls, raw: Mapping[str, Any]) -> RectangleGridSpec:
        root = _mapping(raw, "authoring metadata")
        if str(root.get("kind", "")) != GRID_AUTHORING_KIND:
            raise TemplateFormatError("Template was not authored as a rectangle grid")
        version = root.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise TemplateFormatError("Rectangle-grid authoring version must be an integer")
        if version != GRID_AUTHORING_VERSION:
            raise TemplateFormatError(
                f"Unsupported rectangle-grid authoring version {version}; "
                f"expected {GRID_AUTHORING_VERSION}"
            )
        return cls.from_dict(root)

    @classmethod
    def from_template(cls, template: CutTemplate) -> RectangleGridSpec:
        metadata = template.metadata.get(GRID_AUTHORING_METADATA_KEY)
        if metadata is None:
            raise TemplateFormatError("Template has no rectangle-grid authoring metadata")
        return cls.from_authoring_metadata(_mapping(metadata, "template authoring metadata"))


def _existing_cell_ids(template: CutTemplate | None) -> dict[tuple[int, int], str]:
    if template is None:
        return {}
    output: dict[tuple[int, int], str] = {}
    for item in template.objects:
        raw = item.metadata.get(GRID_CELL_METADATA_KEY)
        if not isinstance(raw, Mapping):
            continue
        row = raw.get("row")
        column = raw.get("column")
        if (
            isinstance(row, int)
            and not isinstance(row, bool)
            and isinstance(column, int)
            and not isinstance(column, bool)
            and row >= 0
            and column >= 0
        ):
            output.setdefault((row, column), item.id)
    return output


def build_rectangle_grid_objects(
    spec: RectangleGridSpec,
    *,
    layer_id: str = "template-grid",
    existing: CutTemplate | None = None,
) -> list[SceneObject]:
    """Build normalized row-major rectangle objects for a grid recipe."""

    if not isinstance(spec, RectangleGridSpec):
        raise TypeError("spec must be a RectangleGridSpec")
    layer_id = str(layer_id).strip()
    if not layer_id:
        raise TemplateFormatError("layer_id must not be empty")
    existing_ids = _existing_cell_ids(existing)
    objects: list[SceneObject] = []
    first_x = -spec.footprint_width_mm / 2.0 + spec.width_mm / 2.0
    first_y = spec.footprint_height_mm / 2.0 - spec.height_mm / 2.0
    for row in range(spec.rows):
        for column in range(spec.columns):
            item = SceneObject.rectangle(
                layer_id,
                name=f"Label {row + 1}, {column + 1}",
                center=(
                    first_x + column * spec.horizontal_pitch_mm,
                    first_y - row * spec.vertical_pitch_mm,
                ),
                width_mm=spec.width_mm,
                height_mm=spec.height_mm,
                corner_radius_mm=spec.corner_radius_mm,
            )
            item.id = existing_ids.get((row, column), item.id)
            item.metadata[GRID_CELL_METADATA_KEY] = {
                "version": GRID_AUTHORING_VERSION,
                "row": row,
                "column": column,
            }
            objects.append(item)
    return objects


def template_from_rectangle_grid(
    spec: RectangleGridSpec,
    *,
    trace_options: Mapping[str, Any] | None = None,
    existing: CutTemplate | None = None,
) -> CutTemplate:
    """Create or rebuild a version-1 cut template from a rectangle-grid recipe.

    Passing ``existing`` is the parametric edit path.  It is accepted
    only for a template previously authored by this generator, preserves the
    persistent template identity and creation timestamp, and reuses surviving
    cell object IDs by row and column.
    """

    if not isinstance(spec, RectangleGridSpec):
        raise TypeError("spec must be a RectangleGridSpec")
    if existing is not None:
        RectangleGridSpec.from_template(existing)

    objects = build_rectangle_grid_objects(
        spec,
        existing=existing,
    )
    features = [
        TemplateFeature(
            center_mm=(item.transform.x_mm, item.transform.y_mm),
            width_mm=item.transform.width_mm,
            height_mm=item.transform.height_mm,
            rotation_deg=item.transform.rotation_deg,
            object_id=item.id,
            kind=ObjectKind.RECTANGLE.value,
        )
        for item in objects
    ]

    template_metadata = dict(existing.metadata) if existing is not None else {}
    template_metadata[GRID_AUTHORING_METADATA_KEY] = spec.to_authoring_metadata()

    if existing is None:
        resolved_trace_options = {} if trace_options is None else dict(trace_options)
        resolved_marker_id = None
        identity: dict[str, Any] = {}
    else:
        resolved_trace_options = (
            existing.trace_options
            if trace_options is None
            else dict(trace_options)
        )
        resolved_marker_id = existing.marker_id
        identity = {
            "id": existing.id,
            "created_at": existing.created_at,
        }

    return CutTemplate(
        name=spec.name,
        description=spec.description,
        bounds=Bounds(
            -spec.footprint_width_mm / 2.0,
            -spec.footprint_height_mm / 2.0,
            spec.footprint_width_mm / 2.0,
            spec.footprint_height_mm / 2.0,
        ),
        objects=objects,
        features=features,
        trace_options=resolved_trace_options,
        marker_id=resolved_marker_id,
        metadata=template_metadata,
        **identity,
    )
