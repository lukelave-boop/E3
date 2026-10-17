"""Preview an explicit circular-arc edit without mutating a project.

The selected native vertices are anchors. Small, isolated edge defects may be
removed, but sustained disagreement, corners, and ambiguous topology reject the
edit. Circles are fitted in physical millimetres and stored as ordinary cubics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real

import numpy as np

from .path_geometry import (
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathLineSegment,
    PathSegment,
    PathSubpath,
    Point,
    flatten_native_path,
)
from .primitive_recovery import (
    CircularArcHypothesis,
    PrimitiveErrorBudget,
    _arc_length_weights,
    _circle_subset_parameters,
    _constrain_circle_to_endpoints,
    canonical_cubic_arc_spans,
)

MAX_ARC_FIT_SEGMENTS = 8_192
_MAX_SAMPLES = 65_536
_FIT_SAMPLES = 2_048


@dataclass(frozen=True, slots=True)
class ArcFitResult:
    geometry: NativePathGeometry
    arc_subpath: PathSubpath
    radius_mm: float
    sweep_degrees: float
    maximum_deviation_mm: float
    rms_deviation_mm: float
    replaced_segment_count: int
    arc_segment_count: int


def arc_fit_vertices(subpath: PathSubpath) -> tuple[Point, ...]:
    """Return selectable vertices; a closed path has no duplicate end vertex."""
    points = (subpath.start, *(segment.to for segment in subpath.segments))
    return points[:-1] if subpath.closed and points[-1] == points[0] else points


def _positive(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a positive finite number")
    number = float(value)
    if not math.isfinite(number) or not 1e-9 <= number <= 1e6:
        raise ValueError(f"{label} must be between 1e-9 and 1,000,000")
    return number


def _span(
    path: PathSubpath, start: int, end: int,
) -> tuple[tuple[Point, ...], tuple[PathSegment, ...], tuple[int, ...]]:
    vertices = arc_fit_vertices(path)
    for index in (start, end):
        if type(index) is not int or not 0 <= index < len(vertices):
            raise ValueError("Fit arc endpoint is outside the selected contour")
    if start == end:
        raise ValueError("Choose two different vertices to fit an arc")
    if not path.closed and start > end:
        raise ValueError("For an open path, choose the start before the end")
    segments = path.segments
    if path.closed and segments[-1].to != path.start:
        segments = (*segments, PathLineSegment(path.start))
    indices = tuple(range(start, end)) if end > start else (
        *range(start, len(segments)), *range(end),
    )
    return vertices, segments, indices


def _samples(span: PathSubpath, affine: PathAffineTransform, tolerance: float) -> np.ndarray:
    flat = np.asarray(flatten_native_path(
        NativePathGeometry((span,)), tolerance * 0.01,
        transform=affine, max_points=_MAX_SAMPLES,
    )[0])
    steps = np.linalg.norm(np.diff(flat, axis=0), axis=1)
    if np.any(steps <= 1e-12):
        raise ValueError("Selected span contains duplicate or collapsed vertices")
    requested = np.maximum(1, np.ceil(steps / (tolerance * 0.5)))
    if not np.isfinite(requested).all() or float(np.sum(requested)) + 1 > _MAX_SAMPLES:
        raise ValueError("Selected arc exceeds the sample limit; select a shorter span")
    counts = requested.astype(np.int64)
    # Include every original flattened vertex, even when a defect is extremely
    # narrow; uniform resampling alone could silently miss it.
    points = [flat[:1]]
    for first, last, count in zip(flat[:-1], flat[1:], counts, strict=True):
        points.append(first + np.arange(1, count + 1)[:, None] / count * (last - first))
    return np.concatenate(points)


def _uniform_samples(points: np.ndarray) -> np.ndarray:
    distances = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))))
    at = np.linspace(0.0, distances[-1], min(_FIT_SAMPLES, len(points)))
    return np.column_stack([np.interp(at, distances, points[:, axis]) for axis in (0, 1)])


def _fit(points: np.ndarray, tolerance: float) -> CircularArcHypothesis:
    samples = _uniform_samples(points)
    parameters = _circle_subset_parameters(samples)
    if parameters is None:
        raise ValueError("The selected span does not establish a stable circular arc")
    constrained = _constrain_circle_to_endpoints(samples, parameters[0])
    if constrained is None:
        raise ValueError("The selected endpoints do not establish a stable circular arc")
    center, radius = constrained
    offsets = points - center
    residuals = np.linalg.norm(offsets, axis=1) - radius
    angles = np.unwrap(np.arctan2(offsets[:, 1], offsets[:, 0]))
    sweep = float(angles[-1] - angles[0])
    if not math.radians(15) <= abs(sweep) < 2 * math.pi - 1e-6:
        raise ValueError("Choose a longer curved span with less than one complete turn")
    if radius * (1 - math.cos(min(abs(sweep), math.pi) / 2)) < 2 * tolerance:
        raise ValueError("The selected span is too straight to establish a reliable radius")
    progress = math.copysign(1.0, sweep) * (angles - angles[0])
    if float(np.min(progress)) < -1e-10 or float(np.max(progress)) > abs(sweep) + 1e-10:
        raise ValueError("The selected edge doubles back beyond one of its endpoints")
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(steps)))
    support = float(cumulative[-1])
    # A long chord can approach the centre between sampled vertices. Include
    # those exact radial minima when reporting/proving the change allowance.
    deltas = np.diff(points, axis=0)
    closest_parameters = np.clip(-np.sum(offsets[:-1] * deltas, axis=1) / steps**2, 0, 1)
    closest_radii = np.linalg.norm(offsets[:-1] + closest_parameters[:, None] * deltas, axis=1)
    maximum = max(float(np.max(np.abs(residuals))), float(np.max(np.abs(closest_radii - radius))))
    if maximum > 3 * tolerance:
        raise ValueError("A notch, corner or edge deviation is too deep for this arc")
    inliers = np.abs(residuals) <= tolerance
    if not inliers[0] or not inliers[-1]:
        raise ValueError("The selected endpoints do not agree with the fitted arc")
    unsupported = 0.0
    indices = np.flatnonzero(~inliers)
    for run in np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1):
        if not len(run):
            continue
        first, last = max(0, int(run[0]) - 1), min(len(points) - 1, int(run[-1]) + 1)
        width = float(cumulative[last] - cumulative[first])
        if width > 8 * tolerance:
            raise ValueError("A sustained notch or corner is too wide to remove with Fit arc")
        unsupported += width
    if unsupported > 0.08 * support:
        raise ValueError("Too much of the selected edge disagrees with a circular arc")
    weights = _arc_length_weights(points)
    valid_weights = weights * inliers
    total = float(np.sum(valid_weights))
    rms = math.sqrt(float(np.sum(valid_weights * residuals**2)) / total)
    bias = abs(float(np.sum(valid_weights * residuals)) / total)
    if rms > 0.35 * tolerance or bias > 0.15 * tolerance:
        raise ValueError("The selected edge has sustained non-circular curvature")
    # Nearby radius fits must agree across the two sides, preventing an ellipse
    # or a blended corner from passing solely because it has a small mean error.
    clean = points[inliers]
    stability = 0.0
    for subset in (clean[:max(3, len(clean) * 2 // 3)], clean[len(clean) // 3:]):
        alternative = _circle_subset_parameters(_uniform_samples(subset))
        if alternative is None:
            raise ValueError("The selected edge does not support a stable radius")
        stability = max(stability, float(np.linalg.norm(alternative[0] - center)), abs(alternative[1] - radius))
    if stability > max(3 * tolerance, 0.02 * radius):
        raise ValueError("Different parts of the selected edge imply different radii")
    directed = math.copysign(1.0, sweep) * np.diff(angles)
    backtrack = float(np.sum(np.maximum(-directed, 0)))
    if radius * backtrack > min(2 * tolerance, 0.02 * radius * abs(sweep)):
        raise ValueError("The selected edge doubles back or contains a corner")
    return CircularArcHypothesis(
        center=center, radius_mm=radius, start_angle_radians=float(angles[0]),
        sweep_radians=sweep, support_length_mm=support, maximum_residual_mm=maximum,
        rms_residual_mm=math.sqrt(float(np.sum(weights * residuals**2)) / float(np.sum(weights))),
        signed_mean_residual_mm=float(np.sum(weights * residuals)) / float(np.sum(weights)),
        maximum_endpoint_adjustment_mm=0.0, angular_backtrack_radians=backtrack,
        stability_center_mm=stability, stability_radius_mm=stability,
        error_budget=PrimitiveErrorBudget(3 * tolerance, tolerance, 0.0, tolerance, tolerance * 0.01),
    )


def _topology_signature(
    geometry: NativePathGeometry, affine: PathAffineTransform, tolerance: float,
) -> tuple[tuple[tuple[bool, ...], ...], tuple[int, ...]]:
    """Validate complete native curves, then record orientation and containment."""
    from .raster_vectorize import (
        _adjacent_control_arcs_share_only_endpoint,
        _boundaries_are_too_close,
        _control_arcs_are_disjoint,
        _cubic_self_topology_is_ambiguous,
        _native_control_arcs,
        _point_inside_closed_polyline,
        _signed_area,
        _TopologyWorkBudget,
    )

    budget = _TopologyWorkBudget()
    entries = []
    for subpath_index, path in enumerate(geometry.subpaths):
        arcs = _native_control_arcs(path, affine)
        current = path.start
        for segment in path.segments:
            if isinstance(segment, PathCubicSegment) and _cubic_self_topology_is_ambiguous(current, segment, affine):
                raise ValueError("Fit arc cannot preserve a self-intersecting curve")
            current = segment.to
        for index, arc in enumerate(arcs):
            if np.all(arc == arc[0]):
                raise ValueError("Fit arc cannot preserve a zero-length segment")
            entries.append((subpath_index, index, len(arcs), path.closed, arc))
    entries.sort(key=lambda entry: float(np.min(entry[4][:, 0])))
    for position, first in enumerate(entries):
        for second in entries[position + 1:]:
            budget.consume()
            if float(np.min(second[4][:, 0])) > float(np.max(first[4][:, 0])):
                break
            same = first[0] == second[0]
            forward = same and (first[1] + 1 == second[1] or (first[3] and first[1] + 1 == first[2] and second[1] == 0))
            reverse = same and (second[1] + 1 == first[1] or (second[3] and second[1] + 1 == second[2] and first[1] == 0))
            if forward:
                separate = _adjacent_control_arcs_share_only_endpoint(first[4], second[4], budget)
            elif reverse:
                separate = _adjacent_control_arcs_share_only_endpoint(second[4], first[4], budget)
            else:
                separate = _control_arcs_are_disjoint(first[4], second[4], budget, 0)
            if not separate:
                raise ValueError("Fit arc would intersect, touch or retrace another part of the path")
    flat = flatten_native_path(geometry, tolerance * 0.01, transform=affine, max_points=_MAX_SAMPLES)
    closed = [
        (index, np.asarray(points), tolerance * 0.01 if any(isinstance(segment, PathCubicSegment) for segment in path.segments) else 0.0)
        for index, (path, points) in enumerate(zip(geometry.subpaths, flat, strict=True)) if path.closed
    ]
    orientation = []
    containment = []
    for position, (index, polygon, envelope) in enumerate(closed):
        area = _signed_area(polygon[:-1])
        if abs(area) <= tolerance**2 * 1e-8:
            raise ValueError("Fit arc would collapse a closed contour")
        orientation.append(1 if area > 0 else -1)
        contained = []
        for other_position, (other_index, other, other_envelope) in enumerate(closed):
            budget.consume()
            if other_position > position and envelope + other_envelope > 0:
                budget.consume(len(polygon) + len(other))
                if _boundaries_are_too_close(polygon, other, envelope + other_envelope, budget):
                    # Native disjointness alone cannot certify which side of a
                    # flattened cubic a tiny hole occupies. Require clearance
                    # beyond both complete curve envelopes before testing nesting.
                    raise ValueError("Contours are too close to prove hole containment for Fit arc")
            if index != other_index:
                budget.consume(len(other) - 1)
                contained.append(_point_inside_closed_polyline(polygon[0], other))
            else:
                contained.append(False)
        containment.append(tuple(contained))
    return tuple(containment), tuple(orientation)


def fit_arc_span(
    geometry: NativePathGeometry,
    subpath_index: int,
    start_vertex: int,
    end_vertex: int,
    *,
    width_mm: float = 1.0,
    height_mm: float = 1.0,
    tolerance_mm: float = 0.1,
) -> ArcFitResult:
    """Prepare a circular replacement for the forward span between two vertices.

    Closed spans may cross the stored start vertex; in that case the contour is
    rotated to start at the selected anchor. Other segments/subpaths stay exact.
    Tolerance bounds the supported edge, with isolated defects limited to three
    times that depth, eight times that length, and 8% of total support.
    """
    if not isinstance(geometry, NativePathGeometry):
        raise ValueError("Fit arc requires native line/cubic path geometry")
    if geometry.segment_count > MAX_ARC_FIT_SEGMENTS:
        raise ValueError("Fit arc exceeds the bounded segment limit; simplify the path first")
    if type(subpath_index) is not int or not 0 <= subpath_index < len(geometry.subpaths):
        raise ValueError("Choose a valid contour for Fit arc")
    width = _positive(width_mm, "Object width")
    height = _positive(height_mm, "Object height")
    tolerance = _positive(tolerance_mm, "Edge variation")
    path = geometry.subpaths[subpath_index]
    vertices, segments, indices = _span(path, start_vertex, end_vertex)
    selected = PathSubpath(vertices[start_vertex], tuple(segments[index] for index in indices))
    affine = PathAffineTransform(m11=width, m22=height)
    hypothesis = _fit(_samples(selected, affine, tolerance), tolerance)
    cubics = canonical_cubic_arc_spans(hypothesis)
    if not cubics:
        raise ValueError("The fitted arc exceeds the bounded cubic representation limit")
    maximum_change = (
        hypothesis.maximum_residual_mm + tolerance * 0.01
        + max(span.maximum_representation_error_mm for span in cubics)
    )
    if maximum_change > 3 * tolerance:
        raise ValueError("The complete fitted curve exceeds the allowed edge change")

    def local(point: np.ndarray) -> Point:
        return float(point[0] / width), float(point[1] / height)

    replacement = tuple(PathCubicSegment(local(span.control_1), local(span.control_2),
                                        vertices[end_vertex] if index == len(cubics) - 1 else local(span.end))
                        for index, span in enumerate(cubics))
    arc = PathSubpath(vertices[start_vertex], replacement)
    if end_vertex > start_vertex:
        changed = PathSubpath(path.start, (*segments[:start_vertex], *replacement, *segments[end_vertex:]), path.closed)
    else:
        changed = PathSubpath(vertices[start_vertex], (*replacement, *segments[end_vertex:start_vertex]), path.closed)
    subpaths = list(geometry.subpaths)
    subpaths[subpath_index] = changed
    proposed = NativePathGeometry(tuple(subpaths), geometry.fill_rule, geometry.path_version)
    if _topology_signature(geometry, affine, tolerance) != _topology_signature(proposed, affine, tolerance):
        raise ValueError("Fit arc would change contour orientation or hole containment")
    return ArcFitResult(
        proposed, arc, hypothesis.radius_mm, math.degrees(hypothesis.sweep_radians),
        maximum_change, hypothesis.rms_residual_mm, len(indices), len(replacement),
    )
