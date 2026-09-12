"""Explicit XY authority for laser-off surface/focus positioning.

The legacy machine rectangle also covers Home/park. The separately configured
fixed output polygon may cover honeycomb beyond that rectangle. Their union,
not its bounding box or convex hull, bounds every focus transfer segment.
No camera detection or measured support dimensions can enlarge this authority.
"""
from __future__ import annotations

import math

from ..errors import SafetyError
from ..geometry.polygon import (
    convex_polygon_contains_normalized,
    normalize_convex_polygon,
)


class FocusXYBounds:
    def __init__(self, work_area, guarded_output_polygon):
        try:
            rectangle = normalize_convex_polygon((
                (work_area.x_min, work_area.y_min),
                (work_area.x_max, work_area.y_min),
                (work_area.x_max, work_area.y_max),
                (work_area.x_min, work_area.y_max),
            ))
            if work_area.x_min >= work_area.x_max or work_area.y_min >= work_area.y_max:
                raise ValueError("Invalid machine rectangle")
            self.polygons = (rectangle,) if guarded_output_polygon is None else (
                rectangle, normalize_convex_polygon(guarded_output_polygon),
            )
            for polygon in self.polygons:
                for index, a in enumerate(polygon):
                    b = polygon[(index + 1) % len(polygon)]
                    ex, ey = b[0] - a[0], b[1] - a[1]
                    if not math.isfinite(math.hypot(ex, ey)) or any(
                        not math.isfinite(ex * (p[1] - a[1]) - ey * (p[0] - a[0]))
                        for p in polygon
                    ):
                        raise ValueError("Nonfinite positioning geometry")
        except (TypeError, ValueError, AttributeError, OverflowError) as exc:
            raise SafetyError("Focus XY requires valid configured positioning bounds") from exc

    def contains(self, point):
        return self._finite(point) and any(
            convex_polygon_contains_normalized(point, polygon, tolerance_mm=0.)
            for polygon in self.polygons
        )

    @staticmethod
    def _finite(point):
        try:
            return (
                isinstance(point, (tuple, list)) and len(point) == 2
                and all(type(value) in (int, float) and math.isfinite(value) for value in point)
            )
        except OverflowError:
            return False

    def contains_segment(self, start, end):
        """Clip the segment to each convex area, then require gap-free coverage."""
        if not self.contains(start) or not self.contains(end):
            return False
        intervals = []
        dx, dy = end[0] - start[0], end[1] - start[1]
        for polygon in self.polygons:
            lower, upper = 0., 1.
            for index, a in enumerate(polygon):
                b = polygon[(index + 1) % len(polygon)]
                ex, ey = b[0] - a[0], b[1] - a[1]
                initial = ex * (start[1] - a[1]) - ey * (start[0] - a[0])
                slope = ex * dy - ey * dx
                if not math.isfinite(initial) or not math.isfinite(slope):
                    return False
                if slope == 0.:
                    if initial < 0.:
                        lower, upper = 1., 0.
                        break
                elif slope > 0.:
                    lower = max(lower, -initial / slope)
                else:
                    upper = min(upper, -initial / slope)
            if lower <= upper:
                intervals.append((lower, upper))
        covered = 0.
        for lower, upper in sorted(intervals):
            if lower > covered:
                return False
            covered = max(covered, upper)
        return covered >= 1.
