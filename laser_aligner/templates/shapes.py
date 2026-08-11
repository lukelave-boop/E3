from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from ..project import ObjectKind, SceneObject


class ShapeKind(StrEnum):
    RECTANGLE = "rectangle"
    ROUNDED_RECTANGLE = "rounded_rectangle"
    CIRCLE = "circle"
    ELLIPSE = "ellipse"
    CAPSULE = "capsule"
    TRIANGLE = "triangle"
    DIAMOND = "diamond"
    REGULAR_POLYGON = "regular_polygon"
    STAR = "star"
    CIRCLE_ONE_FLAT = "circle_one_flat"
    CIRCLE_TWO_FLATS = "circle_two_flats"
    WASHER = "washer"
    FREEFORM_CONTOUR = "freeform_contour"


SHAPE_METADATA_KEY = "shape_kind"


def _closed(points: list[tuple[float, float]]) -> dict[str, Any]:
    return {"points": [*points, points[0]], "closed": True}


def _ellipse_points(segments: int = 72) -> list[tuple[float, float]]:
    return [
        (0.5 * math.cos(2.0 * math.pi * index / segments), 0.5 * math.sin(2.0 * math.pi * index / segments))
        for index in range(segments)
    ]


def shape_polylines(
    shape_kind: ShapeKind | str,
    *,
    width_mm: float,
    height_mm: float,
    polygon_sides: int = 6,
    star_points: int = 5,
    star_inner_ratio: float = 0.5,
    flat_distance_mm: float | None = None,
    inner_diameter_mm: float | None = None,
) -> list[dict[str, Any]]:
    """Return normalized closed contours shared by preview and SceneObjects."""
    kind = ShapeKind(shape_kind)
    if kind in {ShapeKind.CIRCLE, ShapeKind.ELLIPSE}:
        return [_closed(_ellipse_points())]
    if kind == ShapeKind.WASHER:
        assert inner_diameter_mm is not None
        ratio_x = inner_diameter_mm / width_mm
        ratio_y = inner_diameter_mm / height_mm
        inner = [(x * ratio_x, y * ratio_y) for x, y in reversed(_ellipse_points())]
        return [_closed(_ellipse_points()), _closed(inner)]
    if kind == ShapeKind.TRIANGLE:
        return [_closed([(0.0, -0.5), (0.5, 0.5), (-0.5, 0.5)])]
    if kind == ShapeKind.DIAMOND:
        return [_closed([(0.0, -0.5), (0.5, 0.0), (0.0, 0.5), (-0.5, 0.0)])]
    if kind == ShapeKind.REGULAR_POLYGON:
        raw = [
            (
                math.cos(-math.pi / 2 + 2 * math.pi * i / polygon_sides),
                math.sin(-math.pi / 2 + 2 * math.pi * i / polygon_sides),
            )
            for i in range(polygon_sides)
        ]
        max_x = max(abs(x) for x, _ in raw)
        max_y = max(abs(y) for _, y in raw)
        return [_closed([(0.5 * x / max_x, 0.5 * y / max_y) for x, y in raw])]
    if kind == ShapeKind.STAR:
        raw = []
        for i in range(star_points * 2):
            radius = 1.0 if i % 2 == 0 else star_inner_ratio
            angle = -math.pi / 2 + math.pi * i / star_points
            raw.append((radius * math.cos(angle), radius * math.sin(angle)))
        max_x = max(abs(x) for x, _ in raw)
        max_y = max(abs(y) for _, y in raw)
        return [_closed([(0.5 * x / max_x, 0.5 * y / max_y) for x, y in raw])]
    if kind == ShapeKind.CAPSULE:
        # A normalized stadium with semicircles on the shorter dimension.
        points: list[tuple[float, float]] = []
        if width_mm >= height_mm:
            radius = height_mm / (2.0 * width_mm)
            center = 0.5 - radius
            for i in range(37):
                a = -math.pi / 2 + math.pi * i / 36
                points.append((center + radius * math.cos(a), 0.5 * math.sin(a)))
            for i in range(37):
                a = math.pi / 2 + math.pi * i / 36
                points.append((-center + radius * math.cos(a), 0.5 * math.sin(a)))
        else:
            radius = width_mm / (2.0 * height_mm)
            center = 0.5 - radius
            for i in range(37):
                a = math.pi + math.pi * i / 36
                points.append((0.5 * math.cos(a), center + radius * math.sin(a)))
            for i in range(37):
                a = math.pi * i / 36
                points.append((0.5 * math.cos(a), -center + radius * math.sin(a)))
        return [_closed(points)]
    if kind in {ShapeKind.CIRCLE_ONE_FLAT, ShapeKind.CIRCLE_TWO_FLATS}:
        assert flat_distance_mm is not None
        half = flat_distance_mm / (2.0 * width_mm)
        # Intersections are sampled exactly at x=+/- half; all intervening
        # points remain on the radius-0.5 circle.
        limit = math.acos(min(1.0, max(0.0, 2.0 * half)))
        if kind == ShapeKind.CIRCLE_ONE_FLAT:
            angles = [limit + (2 * math.pi - 2 * limit) * i / 72 for i in range(73)]
        else:
            angles = [limit + (math.pi - 2 * limit) * i / 36 for i in range(37)]
            angles += [math.pi + limit + (math.pi - 2 * limit) * i / 36 for i in range(37)]
        return [_closed([(0.5 * math.cos(a), 0.5 * math.sin(a)) for a in angles])]
    raise ValueError(f"{kind.value} uses native rectangle geometry")


def build_shape_object(
    layer_id: str,
    *,
    name: str,
    center: tuple[float, float],
    shape_kind: ShapeKind | str,
    width_mm: float,
    height_mm: float,
    corner_radius_mm: float = 0.0,
    polygon_sides: int = 6,
    star_points: int = 5,
    star_inner_ratio: float = 0.5,
    flat_distance_mm: float | None = None,
    inner_diameter_mm: float | None = None,
    rotation_deg: float = 0.0,
) -> SceneObject:
    kind = ShapeKind(shape_kind)
    if kind in {ShapeKind.RECTANGLE, ShapeKind.ROUNDED_RECTANGLE}:
        item = SceneObject.rectangle(
            layer_id,
            name=name,
            center=center,
            width_mm=width_mm,
            height_mm=height_mm,
            corner_radius_mm=corner_radius_mm if kind == ShapeKind.ROUNDED_RECTANGLE else 0.0,
        )
    elif kind in {ShapeKind.CIRCLE, ShapeKind.ELLIPSE}:
        item = SceneObject.ellipse(layer_id, name=name, center=center, width_mm=width_mm, height_mm=height_mm)
    else:
        normalized = shape_polylines(
            kind,
            width_mm=width_mm,
            height_mm=height_mm,
            polygon_sides=polygon_sides,
            star_points=star_points,
            star_inner_ratio=star_inner_ratio,
            flat_distance_mm=flat_distance_mm,
            inner_diameter_mm=inner_diameter_mm,
        )
        item = SceneObject.path(
            layer_id,
            [
                {"points": [(x * width_mm, y * height_mm) for x, y in line["points"]], "closed": line["closed"]}
                for line in normalized
            ],
            name=name,
            center=center,
        )
    item.transform.rotation_deg = item.transform.normalized_rotation(rotation_deg)
    item.metadata[SHAPE_METADATA_KEY] = kind.value
    if kind == ShapeKind.WASHER:
        item.metadata["hole_ratio"] = float(inner_diameter_mm) / width_mm
    return item


def semantic_shape_kind(item: SceneObject) -> str:
    value = item.metadata.get(SHAPE_METADATA_KEY)
    if isinstance(value, str) and value in ShapeKind._value2member_map_:
        return value
    if item.kind == ObjectKind.RECTANGLE:
        return (
            ShapeKind.ROUNDED_RECTANGLE.value
            if float(item.geometry.get("corner_radius_mm", 0.0)) > 0
            else ShapeKind.RECTANGLE.value
        )
    if item.kind == ObjectKind.ELLIPSE:
        return (
            ShapeKind.CIRCLE.value
            if math.isclose(item.transform.width_mm, item.transform.height_mm, rel_tol=1e-6)
            else ShapeKind.ELLIPSE.value
        )
    return ShapeKind.FREEFORM_CONTOUR.value
