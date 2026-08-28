from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .model import (
    OBJECT_ROLE_KEY,
    STOCK_BOUNDARY_ROLE,
    Bounds,
    ObjectKind,
    ProjectDocument,
    SceneObject,
    Transform,
)
from .path_geometry import (
    MAX_NATIVE_PATH_FLATTENED_POINTS,
    MAX_NATIVE_PATH_SUBDIVISION_DEPTH,
    PathAffineTransform,
    flatten_native_path,
)

_NATIVE_PATH_FLATTEN_TOLERANCE_MM = 0.025

EdgeMode = Literal["nearest", "top", "bottom", "left", "right"]


class StockLayoutError(ValueError):
    """Raised when a stock-relative layout command cannot be completed."""


@dataclass(frozen=True, slots=True)
class StockEdge:
    start: tuple[float, float]
    end: tuple[float, float]

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def midpoint(self) -> tuple[float, float]:
        return (
            (self.start[0] + self.end[0]) / 2.0,
            (self.start[1] + self.end[1]) / 2.0,
        )

    @property
    def angle_deg(self) -> float:
        angle = math.degrees(
            math.atan2(
                self.end[1] - self.start[1],
                self.end[0] - self.start[0],
            )
        )
        # Parallel edges are equivalent for layout. Keep text and imported art
        # upright instead of occasionally rotating it by 180 degrees.
        while angle >= 90.0:
            angle -= 180.0
        while angle < -90.0:
            angle += 180.0
        return angle


def is_stock_boundary(item: SceneObject) -> bool:
    return item.metadata.get(OBJECT_ROLE_KEY) == STOCK_BOUNDARY_ROLE


def is_output_geometry(item: SceneObject) -> bool:
    """Return whether an object is eligible to produce laser output."""

    return not is_stock_boundary(item)


def mark_stock_boundary(item: SceneObject) -> SceneObject:
    if item.kind in {ObjectKind.LINE, ObjectKind.TEXT, ObjectKind.IMAGE}:
        raise StockLayoutError(
            "A stock boundary must be a closed rectangle, ellipse, polygon, or path"
        )
    if item.kind in {ObjectKind.PATH, ObjectKind.POLYGON} and not any(
        subpath.closed and len(subpath.segments) >= 2
        for subpath in item.path_geometry().subpaths
    ):
        raise StockLayoutError(
            "A stock boundary path must contain at least one closed outline"
        )
    item.name = "Stock boundary"
    item.locked = True
    item.metadata[OBJECT_ROLE_KEY] = STOCK_BOUNDARY_ROLE
    item.metadata["construction_only"] = True
    item.metadata["excluded_from_output"] = True
    return item


def stock_boundaries(document: ProjectDocument) -> list[SceneObject]:
    return [item for item in document.objects if is_stock_boundary(item)]


def _transform_point(
    point: tuple[float, float],
    transform: Transform,
) -> tuple[float, float]:
    local_x = point[0] * transform.width_mm
    local_y = point[1] * transform.height_mm
    if transform.mirror_x:
        local_x = -local_x
    if transform.mirror_y:
        local_y = -local_y
    angle = math.radians(transform.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        transform.x_mm + local_x * cosine - local_y * sine,
        transform.y_mm + local_x * sine + local_y * cosine,
    )


def _signed_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    return 0.5 * sum(
        start[0] * end[1] - end[0] * start[1]
        for start, end in zip(points, points[1:] + points[:1], strict=False)
    )


def stock_polygons(item: SceneObject) -> list[list[tuple[float, float]]]:
    """Return closed stock contours in project coordinates."""

    transform = item.transform
    if item.kind == ObjectKind.RECTANGLE:
        return [list(transform.corners())]
    if item.kind == ObjectKind.ELLIPSE:
        return [
            [
                _transform_point(
                    (0.5 * math.cos(angle), 0.5 * math.sin(angle)),
                    transform,
                )
                for angle in (
                    2.0 * math.pi * index / 72.0 for index in range(72)
                )
            ]
        ]
    if item.kind not in {ObjectKind.PATH, ObjectKind.POLYGON}:
        return []
    geometry = item.path_geometry()
    affine = PathAffineTransform.from_components(
        scale_x=transform.width_mm * (-1.0 if transform.mirror_x else 1.0),
        scale_y=transform.height_mm * (-1.0 if transform.mirror_y else 1.0),
        rotation_deg=transform.rotation_deg,
        translate_x=transform.x_mm,
        translate_y=transform.y_mm,
    )
    flattened = flatten_native_path(
        geometry,
        _NATIVE_PATH_FLATTEN_TOLERANCE_MM,
        transform=affine,
        max_points=MAX_NATIVE_PATH_FLATTENED_POINTS,
        max_depth=MAX_NATIVE_PATH_SUBDIVISION_DEPTH,
    )
    polygons: list[list[tuple[float, float]]] = []
    for subpath, flattened_points in zip(
        geometry.subpaths,
        flattened,
        strict=True,
    ):
        if not subpath.closed:
            continue
        points = list(flattened_points)
        if len(points) >= 3:
            if math.dist(points[0], points[-1]) <= 1e-9:
                points.pop()
            polygons.append(points)
    return polygons


def primary_stock_polygon(item: SceneObject) -> list[tuple[float, float]]:
    polygons = stock_polygons(item)
    if not polygons:
        raise StockLayoutError("The stock boundary has no usable closed outline")
    return max(polygons, key=lambda points: abs(_signed_area(points)))


def _distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-18:
        return math.dist(point, start)
    ratio = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / length_squared
    ratio = max(0.0, min(1.0, ratio))
    projection = (start[0] + ratio * dx, start[1] + ratio * dy)
    return math.dist(point, projection)


def _rdp(
    points: list[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points[:]
    start = points[0]
    end = points[-1]
    farthest_index = -1
    farthest_distance = -1.0
    for index, point in enumerate(points[1:-1], start=1):
        distance = _distance_to_segment(point, start, end)
        if distance > farthest_distance:
            farthest_distance = distance
            farthest_index = index
    if farthest_distance <= tolerance or farthest_index < 0:
        return [start, end]
    left = _rdp(points[: farthest_index + 1], tolerance)
    right = _rdp(points[farthest_index:], tolerance)
    return left[:-1] + right


def _simplify_closed_polygon(
    points: list[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    if len(points) <= 4:
        return points[:]
    anchor = points[0]
    split_index = max(
        range(1, len(points)),
        key=lambda index: (
            (points[index][0] - anchor[0]) ** 2
            + (points[index][1] - anchor[1]) ** 2
        ),
    )
    first = _rdp(points[: split_index + 1], tolerance)
    second = _rdp(points[split_index:] + [points[0]], tolerance)
    simplified = first[:-1] + second[:-1]
    output: list[tuple[float, float]] = []
    for point in simplified:
        if not output or math.dist(point, output[-1]) > 1e-6:
            output.append(point)
    return output if len(output) >= 3 else points[:]


def meaningful_stock_edges(item: SceneObject) -> list[StockEdge]:
    polygon = primary_stock_polygon(item)
    bounds = _points_bounds(polygon)
    diagonal = math.hypot(bounds.width, bounds.height)
    simplified = _simplify_closed_polygon(
        polygon,
        tolerance=max(0.25, diagonal * 0.0035),
    )
    minimum_length = max(1.0, diagonal * 0.025)
    edges = [
        StockEdge(start, end)
        for start, end in zip(
            simplified,
            simplified[1:] + simplified[:1],
            strict=False,
        )
        if math.dist(start, end) >= minimum_length
    ]
    if not edges:
        raise StockLayoutError("No meaningful straight stock edge was found")
    return edges


def _outward_normal(
    edge: StockEdge,
    *,
    counterclockwise: bool,
) -> tuple[float, float]:
    dx = edge.end[0] - edge.start[0]
    dy = edge.end[1] - edge.start[1]
    length = max(edge.length, 1e-12)
    if counterclockwise:
        return (dy / length, -dx / length)
    return (-dy / length, dx / length)


def choose_stock_edge(
    item: SceneObject,
    reference_point: tuple[float, float],
    mode: EdgeMode = "nearest",
) -> StockEdge:
    edges = meaningful_stock_edges(item)
    if mode == "nearest":
        return min(
            edges,
            key=lambda edge: _distance_to_segment(
                reference_point,
                edge.start,
                edge.end,
            ),
        )
    counterclockwise = _signed_area(primary_stock_polygon(item)) > 0.0

    def direction_score(edge: StockEdge) -> tuple[float, float, float]:
        normal_x, normal_y = _outward_normal(
            edge,
            counterclockwise=counterclockwise,
        )
        if mode == "top":
            return (normal_y, edge.midpoint[1], edge.length)
        if mode == "bottom":
            return (-normal_y, -edge.midpoint[1], edge.length)
        if mode == "left":
            return (-normal_x, -edge.midpoint[0], edge.length)
        if mode == "right":
            return (normal_x, edge.midpoint[0], edge.length)
        raise StockLayoutError(f"Unknown stock edge mode: {mode}")

    return max(edges, key=direction_score)


def _points_bounds(points: Iterable[tuple[float, float]]) -> Bounds:
    materialized = list(points)
    if not materialized:
        raise StockLayoutError("No points are available for layout")
    xs = [point[0] for point in materialized]
    ys = [point[1] for point in materialized]
    return Bounds(min(xs), min(ys), max(xs), max(ys))


def _selection_objects(
    document: ProjectDocument,
    selected_ids: Iterable[str],
) -> list[SceneObject]:
    selected = set(selected_ids)
    objects = [
        item
        for item in document.objects
        if item.id in selected and not is_stock_boundary(item)
    ]
    if not objects:
        raise StockLayoutError("Select at least one text, SVG, or vector object")
    if any(item.locked for item in objects):
        raise StockLayoutError("Unlock the selected object before laying it out")
    return objects


def _selection_bounds(objects: list[SceneObject]) -> Bounds:
    bounds = objects[0].bounds()
    for item in objects[1:]:
        bounds = bounds.union(item.bounds())
    return bounds


def _nearest_stock(
    document: ProjectDocument,
    reference_point: tuple[float, float],
) -> SceneObject:
    boundaries = stock_boundaries(document)
    if not boundaries:
        raise StockLayoutError(
            "Trace an object with Purpose set to Stock boundary first"
        )
    return min(
        boundaries,
        key=lambda item: math.dist(reference_point, item.bounds().center),
    )


def center_selection_on_stock(
    document: ProjectDocument,
    selected_ids: Iterable[str],
    *,
    horizontal: bool = False,
    vertical: bool = False,
) -> dict[str, Transform]:
    if not horizontal and not vertical:
        return {}
    objects = _selection_objects(document, selected_ids)
    selection = _selection_bounds(objects)
    stock = _nearest_stock(document, selection.center)
    stock_bounds = _points_bounds(primary_stock_polygon(stock))
    dx = stock_bounds.center[0] - selection.center[0] if horizontal else 0.0
    dy = stock_bounds.center[1] - selection.center[1] if vertical else 0.0
    return {
        item.id: item.transform.copy(
            x_mm=item.transform.x_mm + dx,
            y_mm=item.transform.y_mm + dy,
        )
        for item in objects
    }


def snap_selection_rotation_to_stock(
    document: ProjectDocument,
    selected_ids: Iterable[str],
    *,
    edge_mode: EdgeMode = "nearest",
) -> tuple[dict[str, Transform], StockEdge]:
    objects = _selection_objects(document, selected_ids)
    selection = _selection_bounds(objects)
    stock = _nearest_stock(document, selection.center)
    edge = choose_stock_edge(stock, selection.center, edge_mode)

    # Rotate a multi-object selection as one rigid layout. The largest selected
    # object supplies the baseline angle, which is more predictable than object
    # creation order while preserving every relative angle and spacing.
    rotation_reference = max(
        objects,
        key=lambda item: item.transform.width_mm * item.transform.height_mm,
    )
    rotation_delta = Transform.normalized_rotation(
        edge.angle_deg - rotation_reference.transform.rotation_deg
    )
    angle = math.radians(rotation_delta)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    transforms: dict[str, Transform] = {}
    for item in objects:
        offset_x = item.transform.x_mm - selection.center[0]
        offset_y = item.transform.y_mm - selection.center[1]
        transforms[item.id] = item.transform.copy(
            x_mm=selection.center[0] + offset_x * cosine - offset_y * sine,
            y_mm=selection.center[1] + offset_x * sine + offset_y * cosine,
            rotation_deg=item.transform.rotation_deg + rotation_delta,
        )
    return transforms, edge


def _point_in_polygon(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _distance_to_segment(point, previous, current) <= 1e-8:
            return True
        crosses = (current[1] > point[1]) != (previous[1] > point[1])
        if crosses:
            x_intersection = (
                (previous[0] - current[0])
                * (point[1] - current[1])
                / (previous[1] - current[1])
                + current[0]
            )
            if point[0] < x_intersection:
                inside = not inside
        previous = current
    return inside


def _polygon_centroid(
    polygon: list[tuple[float, float]],
) -> tuple[float, float]:
    area_factor = sum(
        start[0] * end[1] - end[0] * start[1]
        for start, end in zip(
            polygon,
            polygon[1:] + polygon[:1],
            strict=False,
        )
    )
    if abs(area_factor) <= 1e-12:
        return _points_bounds(polygon).center
    x = 0.0
    y = 0.0
    for start, end in zip(
        polygon,
        polygon[1:] + polygon[:1],
        strict=False,
    ):
        cross = start[0] * end[1] - end[0] * start[1]
        x += (start[0] + end[0]) * cross
        y += (start[1] + end[1]) * cross
    return (x / (3.0 * area_factor), y / (3.0 * area_factor))


def _interior_candidates(
    polygon: list[tuple[float, float]],
    stock: SceneObject,
    *,
    divisions: int = 10,
) -> list[tuple[float, float]]:
    bounds = _points_bounds(polygon)
    candidates: list[tuple[float, float]] = []
    for candidate in (
        _polygon_centroid(polygon),
        bounds.center,
        (stock.transform.x_mm, stock.transform.y_mm),
    ):
        if _point_in_polygon(candidate, polygon):
            candidates.append(candidate)
    for x_index in range(1, divisions):
        for y_index in range(1, divisions):
            point = (
                bounds.x_min + bounds.width * x_index / divisions,
                bounds.y_min + bounds.height * y_index / divisions,
            )
            if _point_in_polygon(point, polygon):
                candidates.append(point)
    unique: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()
    for point in candidates:
        key = (round(point[0] * 1_000_000), round(point[1] * 1_000_000))
        if key not in seen:
            seen.add(key)
            unique.append(point)
    if not unique:
        raise StockLayoutError("Could not find a usable interior of the stock outline")
    return unique


def _candidate_transform(
    item: SceneObject,
    *,
    source_center: tuple[float, float],
    target_center: tuple[float, float],
    scale: float,
) -> Transform:
    return item.transform.copy(
        x_mm=target_center[0]
        + (item.transform.x_mm - source_center[0]) * scale,
        y_mm=target_center[1]
        + (item.transform.y_mm - source_center[1]) * scale,
        width_mm=max(0.001, item.transform.width_mm * scale),
        height_mm=max(0.001, item.transform.height_mm * scale),
    )


def _orientation(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (
        (second[0] - first[0]) * (third[1] - first[1])
        - (second[1] - first[1]) * (third[0] - first[0])
    )


def _properly_intersects(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    first_side = _orientation(first_start, first_end, second_start)
    second_side = _orientation(first_start, first_end, second_end)
    third_side = _orientation(second_start, second_end, first_start)
    fourth_side = _orientation(second_start, second_end, first_end)
    tolerance = 1e-9
    return (
        first_side * second_side < -tolerance
        and third_side * fourth_side < -tolerance
    )


def _segment_distance(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> float:
    if _properly_intersects(
        first_start,
        first_end,
        second_start,
        second_end,
    ):
        return 0.0
    return min(
        _distance_to_segment(first_start, second_start, second_end),
        _distance_to_segment(first_end, second_start, second_end),
        _distance_to_segment(second_start, first_start, first_end),
        _distance_to_segment(second_end, first_start, first_end),
    )


@dataclass(frozen=True, slots=True)
class _BoundaryEdge:
    start: tuple[float, float]
    end: tuple[float, float]
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(frozen=True, slots=True)
class _BoundaryGeometry:
    polygon: list[tuple[float, float]]
    edges: tuple[_BoundaryEdge, ...]


def _boundary_geometry(
    polygon: list[tuple[float, float]],
) -> _BoundaryGeometry:
    edges = tuple(
        _BoundaryEdge(
            start=start,
            end=end,
            x_min=min(start[0], end[0]),
            y_min=min(start[1], end[1]),
            x_max=max(start[0], end[0]),
            y_max=max(start[1], end[1]),
        )
        for start, end in zip(
            polygon,
            polygon[1:] + polygon[:1],
            strict=False,
        )
    )
    return _BoundaryGeometry(polygon=polygon, edges=edges)


def _fits_inside(
    transforms: Iterable[Transform],
    boundary: _BoundaryGeometry,
    margin_mm: float,
) -> bool:
    polygon = boundary.polygon
    for transform in transforms:
        footprint = list(transform.corners())
        if not all(_point_in_polygon(point, polygon) for point in footprint):
            return False
        footprint_edges = list(
            zip(footprint, footprint[1:] + footprint[:1], strict=False)
        )
        # A concave notch can cross an object's bounding footprint even when
        # every corner remains inside. Reject proper boundary crossings and
        # require exact segment-to-segment clearance for the requested margin.
        for object_start, object_end in footprint_edges:
            midpoint = (
                (object_start[0] + object_end[0]) / 2.0,
                (object_start[1] + object_end[1]) / 2.0,
            )
            if not _point_in_polygon(midpoint, polygon):
                return False
            object_x_min = min(object_start[0], object_end[0]) - margin_mm
            object_y_min = min(object_start[1], object_end[1]) - margin_mm
            object_x_max = max(object_start[0], object_end[0]) + margin_mm
            object_y_max = max(object_start[1], object_end[1]) + margin_mm
            for stock_edge in boundary.edges:
                if (
                    stock_edge.x_max < object_x_min
                    or stock_edge.x_min > object_x_max
                    or stock_edge.y_max < object_y_min
                    or stock_edge.y_min > object_y_max
                ):
                    continue
                if _properly_intersects(
                    object_start,
                    object_end,
                    stock_edge.start,
                    stock_edge.end,
                ):
                    return False
                if margin_mm > 0 and _segment_distance(
                    object_start,
                    object_end,
                    stock_edge.start,
                    stock_edge.end,
                ) + 1e-8 < margin_mm:
                    return False
    return True


def fit_selection_to_stock(
    document: ProjectDocument,
    selected_ids: Iterable[str],
    *,
    margin_mm: float = 3.0,
) -> dict[str, Transform]:
    objects = _selection_objects(document, selected_ids)
    selection = _selection_bounds(objects)
    stock = _nearest_stock(document, selection.center)
    polygon = primary_stock_polygon(stock)
    margin = max(0.0, float(margin_mm))
    stock_bounds = _points_bounds(polygon)
    usable_width = max(0.001, stock_bounds.width - 2.0 * margin)
    usable_height = max(0.001, stock_bounds.height - 2.0 * margin)
    initial_high = max(
        0.001,
        min(
            usable_width / max(selection.width, 0.001),
            usable_height / max(selection.height, 0.001),
        )
        * 1.25,
    )

    # Searching every candidate against a pixel-dense camera contour can make
    # one toolbar click take many seconds. Use a bounded RDP proxy only to rank
    # candidate placement centers, then re-evaluate the best centers and final
    # scale against the untouched original polygon.
    diagonal = math.hypot(stock_bounds.width, stock_bounds.height)
    tolerance = max(0.12, diagonal * 0.0015)
    search_polygon = polygon
    for _ in range(7):
        candidate_polygon = _simplify_closed_polygon(polygon, tolerance)
        search_polygon = candidate_polygon
        if len(candidate_polygon) <= 128:
            break
        tolerance *= 1.7
    exact_boundary = _boundary_geometry(polygon)
    search_boundary = _boundary_geometry(search_polygon)

    def candidate(
        target_center: tuple[float, float],
        scale: float,
    ) -> dict[str, Transform]:
        return {
            item.id: _candidate_transform(
                item,
                source_center=selection.center,
                target_center=target_center,
                scale=scale,
            )
            for item in objects
        }

    def maximum_scale(
        target_center: tuple[float, float],
        boundary: _BoundaryGeometry,
        *,
        iterations: int,
    ) -> float:
        low = 0.0
        high = initial_high
        if not _fits_inside(
            candidate(target_center, 1e-6).values(),
            boundary,
            margin,
        ):
            return 0.0
        for _ in range(8):
            if not _fits_inside(
                candidate(target_center, high).values(),
                boundary,
                margin,
            ):
                break
            low = high
            high *= 1.5
        for _ in range(iterations):
            middle = (low + high) / 2.0
            if _fits_inside(
                candidate(target_center, middle).values(),
                boundary,
                margin,
            ):
                low = middle
            else:
                high = middle
        return low

    centers = _interior_candidates(polygon, stock)
    ranked_centers: list[tuple[float, tuple[float, float]]] = []
    best_center = centers[0]
    best_search_scale = 0.0
    for target_center in centers:
        scale = maximum_scale(target_center, search_boundary, iterations=20)
        ranked_centers.append((scale, target_center))
        if scale > best_search_scale:
            best_center = target_center
            best_search_scale = scale

    # Refine around the best coarse-grid placement using the lightweight proxy.
    # This avoids the common failure where a concave stock centroid sits beside
    # a much larger usable region.
    step_x = stock_bounds.width / 10.0
    step_y = stock_bounds.height / 10.0
    for refinement in (0.5, 0.2):
        local_centers: list[tuple[float, float]] = []
        for x_offset in range(-2, 3):
            for y_offset in range(-2, 3):
                point = (
                    best_center[0] + x_offset * step_x * refinement,
                    best_center[1] + y_offset * step_y * refinement,
                )
                if _point_in_polygon(point, polygon):
                    local_centers.append(point)
        for target_center in local_centers:
            scale = maximum_scale(target_center, search_boundary, iterations=22)
            ranked_centers.append((scale, target_center))
            if scale > best_search_scale:
                best_center = target_center
                best_search_scale = scale

    ranked_centers.sort(key=lambda item: item[0], reverse=True)
    finalists: list[tuple[float, float]] = []
    seen: set[tuple[int, int]] = set()

    def add_finalist(point: tuple[float, float]) -> None:
        key = (round(point[0] * 1_000_000), round(point[1] * 1_000_000))
        if key not in seen:
            seen.add(key)
            finalists.append(point)

    for _, point in ranked_centers[:4]:
        add_finalist(point)

    # Keep spatially diverse finalists. A simplified search contour can smooth
    # over a narrow notch, so evaluating only the highest proxy scores could
    # miss a substantially better placement in another lobe of the real stock.
    bins: dict[tuple[int, int], tuple[float, tuple[float, float]]] = {}
    for scale, point in ranked_centers:
        x_ratio = (point[0] - stock_bounds.x_min) / max(stock_bounds.width, 1e-9)
        y_ratio = (point[1] - stock_bounds.y_min) / max(stock_bounds.height, 1e-9)
        key = (
            max(0, min(2, int(x_ratio * 3.0))),
            max(0, min(2, int(y_ratio * 3.0))),
        )
        if key not in bins or scale > bins[key][0]:
            bins[key] = (scale, point)
    for _, point in bins.values():
        add_finalist(point)

    best_scale = 0.0
    for target_center in finalists:
        scale = maximum_scale(target_center, exact_boundary, iterations=18)
        if scale > best_scale:
            best_center = target_center
            best_scale = scale
    best_scale = maximum_scale(best_center, exact_boundary, iterations=32)
    if best_scale <= 1e-6:
        raise StockLayoutError(
            "The selected object cannot fit inside the stock with that margin"
        )
    return candidate(best_center, best_scale)
