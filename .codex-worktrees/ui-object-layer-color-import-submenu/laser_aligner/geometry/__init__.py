from .polygon import (
    ConvexPolygon,
    convex_polygon_contains,
    convex_polygon_contains_normalized,
    convex_polygon_violation_mm,
    convex_polygon_violation_normalized_mm,
    normalize_convex_polygon,
)
from .svg import Polyline, SvgGeometry, parse_svg
from .transforms import apply_matrix, identity, parse_transform

__all__ = [
    "ConvexPolygon",
    "Polyline",
    "SvgGeometry",
    "apply_matrix",
    "convex_polygon_contains",
    "convex_polygon_contains_normalized",
    "convex_polygon_violation_mm",
    "convex_polygon_violation_normalized_mm",
    "identity",
    "normalize_convex_polygon",
    "parse_svg",
    "parse_transform",
]
