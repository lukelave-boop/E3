from __future__ import annotations

import math

from .model import ObjectKind, SceneObject


def regular_polygon_points(
    center: tuple[float, float], radius_mm: float, angle_deg: float, sides: int,
) -> list[tuple[float, float]]:
    """Vertices on a fixed circumcircle; the first vertex defines orientation."""
    if isinstance(sides, bool) or not isinstance(sides, int) or not 3 <= sides <= 12:
        raise ValueError("Choose a whole number of sides from 3 to 12")
    if not all(math.isfinite(value) for value in (*center, radius_mm, angle_deg)):
        raise ValueError("Polygon position, radius and angle must be finite")
    if radius_mm <= 0:
        raise ValueError("Polygon radius must be positive")
    angle = math.radians(angle_deg)
    return [
        (
            center[0] + radius_mm * math.cos(angle + index * math.tau / sides),
            center[1] + radius_mm * math.sin(angle + index * math.tau / sides),
        )
        for index in range(sides)
    ]


def create_regular_polygon(
    layer_id: str, center: tuple[float, float], radius_mm: float, angle_deg: float, sides: int,
) -> SceneObject:
    points = regular_polygon_points(center, radius_mm, angle_deg, sides)
    # PATH normalization uses the bounding-box center, which differs from the
    # chosen circumcenter for odd-sided polygons. Preserve the actual vertices.
    bounds_center = (
        (min(x for x, _ in points) + max(x for x, _ in points)) / 2,
        (min(y for _, y in points) + max(y for _, y in points)) / 2,
    )
    item = SceneObject.path(
        layer_id, [{"points": points, "closed": True}],
        name=f"{sides}-sided polygon", center=bounds_center, source_name="E3 polygon",
    )
    item.kind = ObjectKind.POLYGON
    return item
