from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]
ConvexPolygon = tuple[Point, ...]


def normalize_convex_polygon(
    value: Sequence[Sequence[float]],
    *,
    label: str = "polygon",
) -> ConvexPolygon:
    """Validate and return one counter-clockwise finite convex polygon."""

    if isinstance(value, (str, bytes)) or len(value) < 3:
        raise ValueError(f"{label} must contain at least three points")
    points: list[Point] = []
    for index, point in enumerate(value):
        if isinstance(point, (str, bytes)) or len(point) != 2:
            raise ValueError(f"{label}[{index}] must contain exactly X and Y")
        x, y = point
        if type(x) not in {int, float} or type(y) not in {int, float}:
            raise ValueError(f"{label}[{index}] must contain finite numbers")
        x_value = float(x)
        y_value = float(y)
        if not math.isfinite(x_value) or not math.isfinite(y_value):
            raise ValueError(f"{label}[{index}] must contain finite numbers")
        points.append((x_value, y_value))

    crosses: list[float] = []
    for index, current in enumerate(points):
        following = points[(index + 1) % len(points)]
        after = points[(index + 2) % len(points)]
        edge_x = following[0] - current[0]
        edge_y = following[1] - current[1]
        next_x = after[0] - following[0]
        next_y = after[1] - following[1]
        if math.hypot(edge_x, edge_y) <= 1e-9:
            raise ValueError(f"{label} contains a repeated adjacent point")
        cross = edge_x * next_y - edge_y * next_x
        if abs(cross) <= 1e-9:
            raise ValueError(f"{label} must be strictly convex")
        crosses.append(cross)
    if not (all(value > 0.0 for value in crosses) or all(value < 0.0 for value in crosses)):
        raise ValueError(f"{label} must be one ordered convex polygon")
    if crosses[0] < 0.0:
        points.reverse()
    for edge_index, start in enumerate(points):
        end = points[(edge_index + 1) % len(points)]
        edge_x = end[0] - start[0]
        edge_y = end[1] - start[1]
        for point_index, point in enumerate(points):
            if point_index in {edge_index, (edge_index + 1) % len(points)}:
                continue
            cross = edge_x * (point[1] - start[1]) - edge_y * (
                point[0] - start[0]
            )
            if cross <= 1e-9:
                raise ValueError(f"{label} must be one ordered convex polygon")
    return tuple(points)


def convex_polygon_violation_mm(
    point: Sequence[float],
    polygon: Sequence[Sequence[float]],
) -> float:
    """Return zero inside a convex polygon or the greatest edge escape distance."""

    normalized = normalize_convex_polygon(polygon)
    return convex_polygon_violation_normalized_mm(point, normalized)


def convex_polygon_violation_normalized_mm(
    point: Sequence[float],
    polygon: ConvexPolygon,
) -> float:
    """Fast containment distance for an already normalized convex polygon."""

    x = float(point[0])
    y = float(point[1])
    if not math.isfinite(x) or not math.isfinite(y):
        return math.inf
    violation = 0.0
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        edge_x = end[0] - start[0]
        edge_y = end[1] - start[1]
        cross = edge_x * (y - start[1]) - edge_y * (x - start[0])
        violation = max(violation, -cross / math.hypot(edge_x, edge_y))
    return max(0.0, violation)


def convex_polygon_contains(
    point: Sequence[float],
    polygon: Sequence[Sequence[float]],
    *,
    tolerance_mm: float = 1e-6,
) -> bool:
    return convex_polygon_violation_mm(point, polygon) <= float(tolerance_mm)


def convex_polygon_contains_normalized(
    point: Sequence[float],
    polygon: ConvexPolygon,
    *,
    tolerance_mm: float = 1e-6,
) -> bool:
    return (
        convex_polygon_violation_normalized_mm(point, polygon)
        <= float(tolerance_mm)
    )
