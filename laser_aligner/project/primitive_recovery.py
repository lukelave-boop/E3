from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_MINIMUM_LINE_SAMPLES = 12
_MINIMUM_ARC_SAMPLES = 20
_MAXIMUM_ROBUST_ITERATIONS = 6
_MINIMUM_ARC_SWEEP_RADIANS = math.radians(30.0)
_MAXIMUM_ARC_BACKTRACK_FRACTION = 0.02
_MAXIMUM_ARC_SAMPLE_GAP_RADIANS = math.radians(30.0)
_ARC_ANGLE_COMPARISON_EPSILON_RADIANS = 1e-10
_MINIMUM_LINE_INTERSECTION_SINE = math.sin(math.radians(8.0))


@dataclass(frozen=True, slots=True)
class PrimitiveErrorBudget:
    """Physical residual limits shared by line and circular-arc hypotheses."""

    maximum_mm: float
    rms_mm: float
    endpoint_adjustment_mm: float
    source_normal_pitch_mm: float
    representation_mm: float


@dataclass(frozen=True, slots=True, eq=False)
class LineHypothesis:
    origin: np.ndarray
    direction: np.ndarray
    normal: np.ndarray
    start_projection: np.ndarray
    end_projection: np.ndarray
    support_length_mm: float
    maximum_residual_mm: float
    rms_residual_mm: float
    signed_mean_residual_mm: float
    maximum_endpoint_adjustment_mm: float
    inlier_count: int
    sample_count: int
    error_budget: PrimitiveErrorBudget


@dataclass(frozen=True, slots=True, eq=False)
class CircularArcHypothesis:
    center: np.ndarray
    radius_mm: float
    start_angle_radians: float
    sweep_radians: float
    support_length_mm: float
    maximum_residual_mm: float
    rms_residual_mm: float
    signed_mean_residual_mm: float
    maximum_endpoint_adjustment_mm: float
    angular_backtrack_radians: float
    stability_center_mm: float
    stability_radius_mm: float
    error_budget: PrimitiveErrorBudget

    @property
    def arc_length_mm(self) -> float:
        return abs(self.sweep_radians) * self.radius_mm


@dataclass(frozen=True, slots=True, eq=False)
class CanonicalCubicArc:
    start: np.ndarray
    control_1: np.ndarray
    control_2: np.ndarray
    end: np.ndarray
    start_angle_radians: float
    sweep_radians: float
    maximum_representation_error_mm: float


def _finite_positive_pair(
    source_pixel_spacing_mm: tuple[float, float],
) -> tuple[float, float]:
    spacing = tuple(float(value) for value in source_pixel_spacing_mm)
    if len(spacing) != 2 or not all(math.isfinite(value) and value > 0.0 for value in spacing):
        raise ValueError("source_pixel_spacing_mm must contain two positive values")
    return spacing[0], spacing[1]


def primitive_error_budget(
    tolerance_mm: float,
    source_pixel_spacing_mm: tuple[float, float],
    *,
    normal: np.ndarray | None = None,
) -> PrimitiveErrorBudget:
    """Return a tolerance cap that cannot grow beyond resolved pixel evidence.

    ``tolerance_mm`` is the authoritative internal native-fit tolerance.  A
    quantized edge can legitimately occupy roughly half of one complete source
    pixel along its normal, while a large user tolerance must not turn resolved
    curvature into a primitive.  The maximum, RMS, and endpoint gates therefore
    use the stricter of the native tolerance and fixed fractions of the full
    source-pixel normal extent.  The 4x reconstruction is deliberately not
    treated as four times more source information.
    """

    tolerance = float(tolerance_mm)
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError("tolerance_mm must be positive and finite")
    spacing_x, spacing_y = _finite_positive_pair(source_pixel_spacing_mm)
    if normal is None:
        normal_pitch = max(spacing_x, spacing_y)
    else:
        vector = np.asarray(normal, dtype=np.float64).reshape(2)
        length = float(np.linalg.norm(vector))
        if not math.isfinite(length) or length <= 1e-15:
            raise ValueError("normal must have non-zero finite length")
        vector = vector / length
        normal_pitch = abs(float(vector[0])) * spacing_x + abs(float(vector[1])) * spacing_y
    maximum = min(tolerance, 0.60 * normal_pitch)
    rms = min(0.35 * tolerance, 0.30 * normal_pitch)
    endpoint = min(0.50 * tolerance, 0.50 * normal_pitch)
    representation = min(0.10 * tolerance, 0.20 * rms)
    numeric = max(1e-12, tolerance * 1e-9)
    return PrimitiveErrorBudget(
        maximum_mm=max(maximum, numeric),
        rms_mm=max(rms, numeric),
        endpoint_adjustment_mm=max(endpoint, numeric),
        source_normal_pitch_mm=normal_pitch,
        representation_mm=max(representation, numeric),
    )


def _arc_length_weights(points: np.ndarray) -> np.ndarray:
    if len(points) == 1:
        return np.ones(1, dtype=np.float64)
    steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    weights = np.zeros(len(points), dtype=np.float64)
    weights[0] = steps[0] / 2.0
    weights[-1] = steps[-1] / 2.0
    if len(points) > 2:
        weights[1:-1] = (steps[:-1] + steps[1:]) / 2.0
    if float(np.sum(weights)) <= 1e-15:
        return np.ones(len(points), dtype=np.float64)
    return weights


def _weighted_tls(
    points: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    total = float(np.sum(weights))
    if total <= 1e-15:
        return None
    origin = np.sum(points * weights[:, None], axis=0) / total
    centered = points - origin
    covariance = (centered * weights[:, None]).T @ centered / total
    try:
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    except np.linalg.LinAlgError:
        return None
    if not np.isfinite(eigenvalues).all() or float(eigenvalues[-1]) <= 1e-18:
        return None
    direction = eigenvectors[:, -1]
    chord = points[-1] - points[0]
    if float(np.dot(direction, chord)) < 0.0:
        direction = -direction
    normal = np.asarray((-direction[1], direction[0]), dtype=np.float64)
    return origin, direction, normal


def _weighted_metrics(
    residuals: np.ndarray,
    weights: np.ndarray,
) -> tuple[float, float, float]:
    total = float(np.sum(weights))
    if total <= 1e-15:
        return math.inf, math.inf, math.inf
    maximum = float(np.max(np.abs(residuals)))
    rms = math.sqrt(float(np.sum(weights * residuals**2)) / total)
    signed_mean = float(np.sum(weights * residuals) / total)
    return maximum, rms, signed_mean


def _radial_normal_error_limits(
    radial_normals: np.ndarray,
    tolerance_mm: float,
    source_pixel_spacing_mm: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return projected pitch plus per-sample radial-error limits."""

    spacing_x, spacing_y = source_pixel_spacing_mm
    normal_pitch = np.abs(radial_normals[:, 0]) * spacing_x + np.abs(radial_normals[:, 1]) * spacing_y
    tolerance = float(tolerance_mm)
    numeric = max(1e-12, tolerance * 1e-9)
    maximum = np.maximum(np.minimum(tolerance, 0.60 * normal_pitch), numeric)
    rms = np.maximum(np.minimum(0.35 * tolerance, 0.30 * normal_pitch), numeric)
    endpoint = np.maximum(
        np.minimum(0.50 * tolerance, 0.50 * normal_pitch),
        numeric,
    )
    return normal_pitch, maximum, rms, endpoint


def fit_line_hypothesis(
    points: np.ndarray,
    tolerance_mm: float,
    source_pixel_spacing_mm: tuple[float, float],
) -> LineHypothesis | None:
    """Fit a deterministic robust total-least-squares line to ordered points."""

    values = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(values) < _MINIMUM_LINE_SAMPLES or not np.isfinite(values).all():
        return None
    base_weights = _arc_length_weights(values)
    support_length = float(np.sum(np.linalg.norm(np.diff(values, axis=0), axis=1)))
    source_spacing = _finite_positive_pair(source_pixel_spacing_mm)
    minimum_support = max(
        6.0 * float(tolerance_mm),
        8.0 * max(source_spacing),
    )
    if support_length < minimum_support:
        return None

    robust_weights = base_weights.copy()
    active = np.ones(len(values), dtype=bool)
    model: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
    for _iteration in range(_MAXIMUM_ROBUST_ITERATIONS):
        model = _weighted_tls(values, robust_weights)
        if model is None:
            return None
        origin, _direction, normal = model
        residuals = (values - origin) @ normal
        median = float(np.median(residuals[active]))
        mad = float(np.median(np.abs(residuals[active] - median)))
        scale = max(1.4826 * mad, 1e-12)
        cutoff = 2.75 * scale
        normalized = np.abs(residuals - median) / max(cutoff, 1e-12)
        next_active = normalized < 1.0
        # Tukey weights locate the model robustly, but acceptance below still
        # validates every point against the physical maximum envelope.
        tukey = np.square(np.maximum(0.0, 1.0 - normalized**2))
        next_weights = base_weights * tukey
        if np.count_nonzero(next_active) < max(
            _MINIMUM_LINE_SAMPLES,
            math.ceil(0.90 * len(values)),
        ):
            next_active = np.ones(len(values), dtype=bool)
            next_weights = base_weights.copy()
        if np.array_equal(next_active, active):
            robust_weights = next_weights
            break
        active = next_active
        robust_weights = next_weights
    model = _weighted_tls(values, robust_weights)
    if model is None:
        return None
    origin, direction, normal = model
    error_budget = primitive_error_budget(
        tolerance_mm,
        source_spacing,
        normal=normal,
    )
    residuals = (values - origin) @ normal
    maximum, rms, signed_mean = _weighted_metrics(residuals, base_weights)
    if maximum > error_budget.maximum_mm or rms > error_budget.rms_mm:
        return None

    projections = (values - origin) @ direction
    projection_steps = np.diff(projections)
    backwards = np.maximum(-projection_steps, 0.0)
    projected_support = float(projections[-1] - projections[0])
    if projected_support <= 1e-15:
        return None
    if float(np.sum(backwards)) > max(
        error_budget.source_normal_pitch_mm,
        0.02 * projected_support,
    ):
        return None
    start_projection = origin + projections[0] * direction
    end_projection = origin + projections[-1] * direction
    endpoint_adjustment = max(
        float(np.linalg.norm(start_projection - values[0])),
        float(np.linalg.norm(end_projection - values[-1])),
    )
    if endpoint_adjustment > error_budget.endpoint_adjustment_mm:
        return None

    # Independent halves must agree on orientation.  This rejects a smooth
    # low-frequency bend whose aggregate residual happens to fit a wide budget.
    midpoint = len(values) // 2
    half_models = (
        _weighted_tls(values[: midpoint + 1], base_weights[: midpoint + 1]),
        _weighted_tls(values[midpoint:], base_weights[midpoint:]),
    )
    if any(item is None for item in half_models):
        return None
    first_direction = half_models[0][1]  # type: ignore[index]
    second_direction = half_models[1][1]  # type: ignore[index]
    alignment = abs(float(np.dot(first_direction, second_direction)))
    maximum_angle = min(
        math.radians(4.0),
        math.atan2(
            4.0 * error_budget.maximum_mm,
            max(projected_support, 1e-15),
        ),
    )
    if alignment < math.cos(max(maximum_angle, math.radians(0.25))):
        return None
    return LineHypothesis(
        origin=origin.copy(),
        direction=direction.copy(),
        normal=normal.copy(),
        start_projection=start_projection,
        end_projection=end_projection,
        support_length_mm=projected_support,
        maximum_residual_mm=maximum,
        rms_residual_mm=rms,
        signed_mean_residual_mm=signed_mean,
        maximum_endpoint_adjustment_mm=endpoint_adjustment,
        inlier_count=int(np.count_nonzero(active)),
        sample_count=len(values),
        error_budget=error_budget,
    )


def line_intersection(
    first: LineHypothesis,
    second: LineHypothesis,
    observed_corner: np.ndarray,
) -> tuple[np.ndarray, float] | None:
    """Return a nearby clean line-line corner, rejecting distant spikes."""

    denominator = float(first.direction[0] * second.direction[1] - first.direction[1] * second.direction[0])
    if abs(denominator) < _MINIMUM_LINE_INTERSECTION_SINE:
        return None
    delta = second.origin - first.origin
    parameter = float((delta[0] * second.direction[1] - delta[1] * second.direction[0]) / denominator)
    intersection = first.origin + parameter * first.direction
    observed = np.asarray(observed_corner, dtype=np.float64).reshape(2)
    adjustment = float(np.linalg.norm(intersection - observed))
    allowance = min(
        first.error_budget.endpoint_adjustment_mm,
        second.error_budget.endpoint_adjustment_mm,
    )
    if adjustment > allowance:
        return None
    return intersection, adjustment


def _algebraic_circle_parameters(points: np.ndarray) -> tuple[np.ndarray, float] | None:
    reference = np.mean(points, axis=0)
    centered = points - reference
    scale = float(np.max(np.linalg.norm(centered, axis=1)))
    if scale <= 1e-15:
        return None
    normalized = centered / scale
    matrix = np.column_stack((2.0 * normalized[:, 0], 2.0 * normalized[:, 1], np.ones(len(points))))
    target = np.sum(normalized**2, axis=1)
    try:
        solution, _residuals, rank, singular_values = np.linalg.lstsq(
            matrix,
            target,
            rcond=None,
        )
    except np.linalg.LinAlgError:
        return None
    if rank < 3 or not np.isfinite(solution).all():
        return None
    if singular_values[-1] <= singular_values[0] * 1e-10:
        return None
    center_normalized = solution[:2]
    radius_squared = float(solution[2] + np.dot(center_normalized, center_normalized))
    if radius_squared <= 1e-15:
        return None
    return reference + scale * center_normalized, scale * math.sqrt(radius_squared)


def _refine_circle_parameters(
    points: np.ndarray,
    center: np.ndarray,
    radius: float,
) -> tuple[np.ndarray, float] | None:
    values = np.asarray(points, dtype=np.float64)
    center = np.asarray(center, dtype=np.float64).copy()
    radius = float(radius)
    base_weights = _arc_length_weights(values)
    for _iteration in range(_MAXIMUM_ROBUST_ITERATIONS):
        offsets = values - center
        distances = np.linalg.norm(offsets, axis=1)
        if np.any(distances <= 1e-15):
            return None
        residuals = distances - radius
        median = float(np.median(residuals))
        scale = max(1.4826 * float(np.median(np.abs(residuals - median))), 1e-12)
        huber = np.minimum(1.0, (2.5 * scale) / np.maximum(np.abs(residuals), 1e-15))
        weights = base_weights * huber
        jacobian = np.column_stack((-offsets / distances[:, None], -np.ones(len(values))))
        weighted_jacobian = jacobian * np.sqrt(weights)[:, None]
        weighted_residuals = residuals * np.sqrt(weights)
        try:
            delta, _residuals, rank, _singular = np.linalg.lstsq(
                weighted_jacobian,
                -weighted_residuals,
                rcond=None,
            )
        except np.linalg.LinAlgError:
            return None
        if rank < 3 or not np.isfinite(delta).all():
            return None
        center += delta[:2]
        radius += float(delta[2])
        if radius <= 1e-15:
            return None
        if float(np.linalg.norm(delta)) <= 1e-12 * max(1.0, radius):
            break
    return center, radius


def _constrain_circle_to_endpoints(
    points: np.ndarray,
    center: np.ndarray,
) -> tuple[np.ndarray, float] | None:
    start = points[0]
    end = points[-1]
    chord = end - start
    chord_length = float(np.linalg.norm(chord))
    if chord_length <= 1e-12:
        return None
    midpoint = (start + end) / 2.0
    normal = np.asarray((-chord[1], chord[0]), dtype=np.float64) / chord_length
    half_chord = chord_length / 2.0
    height = float(np.dot(center - midpoint, normal))
    weights = _arc_length_weights(points)
    for _iteration in range(_MAXIMUM_ROBUST_ITERATIONS):
        candidate_center = midpoint + height * normal
        radius = math.hypot(half_chord, height)
        offsets = candidate_center - points
        distances = np.linalg.norm(offsets, axis=1)
        if np.any(distances <= 1e-15) or radius <= 1e-15:
            return None
        residuals = distances - radius
        derivative = (offsets @ normal) / distances - height / radius
        median = float(np.median(residuals))
        scale = max(1.4826 * float(np.median(np.abs(residuals - median))), 1e-12)
        huber = np.minimum(1.0, (2.5 * scale) / np.maximum(np.abs(residuals), 1e-15))
        robust_weights = weights * huber
        denominator = float(np.sum(robust_weights * derivative**2))
        if denominator <= 1e-18:
            break
        delta = -float(np.sum(robust_weights * derivative * residuals)) / denominator
        height += delta
        if abs(delta) <= 1e-12 * max(1.0, radius):
            break
    candidate_center = midpoint + height * normal
    radius = math.hypot(half_chord, height)
    if not np.isfinite(candidate_center).all() or not math.isfinite(radius):
        return None
    return candidate_center, radius


def _unwrapped_arc_angles(
    points: np.ndarray,
    center: np.ndarray,
) -> tuple[np.ndarray, float, float] | None:
    angles = np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0])
    unwrapped = np.unwrap(angles)
    sweep = float(unwrapped[-1] - unwrapped[0])
    if abs(sweep) <= 1e-12:
        return None
    direction = math.copysign(1.0, sweep)
    differences = direction * np.diff(unwrapped)
    if (
        float(np.max(np.abs(np.diff(unwrapped))))
        > _MAXIMUM_ARC_SAMPLE_GAP_RADIANS + _ARC_ANGLE_COMPARISON_EPSILON_RADIANS
    ):
        return None
    backtrack = float(np.sum(np.maximum(-differences, 0.0)))
    return unwrapped, sweep, backtrack


def _closed_arc_order(
    points: np.ndarray,
    center: np.ndarray,
) -> tuple[float, float] | None:
    """Return one ordered full turn and its accumulated angular backtrack."""

    angles = np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0])
    following = np.roll(angles, -1)
    differences = np.arctan2(
        np.sin(following - angles),
        np.cos(following - angles),
    )
    if float(np.max(np.abs(differences))) > _MAXIMUM_ARC_SAMPLE_GAP_RADIANS + _ARC_ANGLE_COMPARISON_EPSILON_RADIANS:
        return None
    total = float(np.sum(differences))
    if not (1.5 * math.pi <= abs(total) <= 2.5 * math.pi):
        return None
    direction = math.copysign(1.0, total)
    directed = direction * differences
    backtrack = float(np.sum(np.maximum(-directed, 0.0)))
    sweep = direction * 2.0 * math.pi
    return sweep, backtrack


def _circle_subset_parameters(points: np.ndarray) -> tuple[np.ndarray, float] | None:
    initial = _algebraic_circle_parameters(points)
    if initial is None:
        return None
    return _refine_circle_parameters(points, initial[0], initial[1])


def fit_circular_arc_hypothesis(
    points: np.ndarray,
    tolerance_mm: float,
    source_pixel_spacing_mm: tuple[float, float],
    *,
    closed: bool = False,
) -> CircularArcHypothesis | None:
    """Fit and conservatively validate one ordered conceptual circular arc."""

    values = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(values) < _MINIMUM_ARC_SAMPLES or not np.isfinite(values).all():
        return None
    if closed and float(np.linalg.norm(values[0] - values[-1])) <= 1e-12:
        values = values[:-1]
    if len(values) < _MINIMUM_ARC_SAMPLES:
        return None
    source_spacing = _finite_positive_pair(source_pixel_spacing_mm)
    support_length = float(np.sum(np.linalg.norm(np.diff(values, axis=0), axis=1)))
    minimum_support = max(
        8.0 * float(tolerance_mm),
        12.0 * max(source_spacing),
    )
    if support_length < minimum_support:
        return None
    parameters = _circle_subset_parameters(values)
    if parameters is None:
        return None
    center, radius = parameters
    if not closed:
        constrained = _constrain_circle_to_endpoints(values, center)
        if constrained is None:
            return None
        center, radius = constrained
    if radius < 2.0 * max(source_spacing):
        return None

    if closed:
        order = _closed_arc_order(values, center)
        if order is None:
            return None
        sweep, angular_backtrack = order
        start_angle = math.atan2(values[0, 1] - center[1], values[0, 0] - center[0])
    else:
        angle_result = _unwrapped_arc_angles(values, center)
        if angle_result is None:
            return None
        unwrapped, sweep, angular_backtrack = angle_result
        start_angle = float(unwrapped[0])
        if abs(sweep) < _MINIMUM_ARC_SWEEP_RADIANS - _ARC_ANGLE_COMPARISON_EPSILON_RADIANS:
            return None
        if abs(sweep) > 2.0 * math.pi + _ARC_ANGLE_COMPARISON_EPSILON_RADIANS:
            return None
    if angular_backtrack > max(
        _MAXIMUM_ARC_BACKTRACK_FRACTION * abs(sweep),
        2.0 * max(source_spacing) / radius,
    ):
        return None

    offsets = values - center
    distances = np.linalg.norm(offsets, axis=1)
    if np.any(distances <= 1e-15):
        return None
    residuals = distances - radius
    weights = _arc_length_weights(values)
    radial_normals = offsets / distances[:, None]
    normal_pitch, local_maximum, local_rms, local_endpoint = _radial_normal_error_limits(
        radial_normals,
        tolerance_mm,
        source_spacing,
    )
    # The scalar budget records the largest radial-normal envelope present on
    # the arc.  Pointwise acceptance still uses the local limits below, so a
    # coarse source axis cannot hide a deviation resolved along the fine axis.
    envelope_index = int(np.argmax(normal_pitch))
    error_budget = primitive_error_budget(
        tolerance_mm,
        source_spacing,
        normal=radial_normals[envelope_index],
    )
    tolerance = float(tolerance_mm)
    numeric = max(1e-12, tolerance * 1e-9)
    strict_radial_rms = max(
        min(0.35 * tolerance, 0.30 * min(source_spacing)),
        numeric,
    )
    # Canonical controls occupy angles between source samples, so their radial
    # representation error remains below the finest source-axis resolution.
    strict_representation = max(
        min(0.10 * tolerance, 0.20 * strict_radial_rms),
        numeric,
    )
    if strict_representation < error_budget.representation_mm:
        error_budget = PrimitiveErrorBudget(
            maximum_mm=error_budget.maximum_mm,
            rms_mm=error_budget.rms_mm,
            endpoint_adjustment_mm=error_budget.endpoint_adjustment_mm,
            source_normal_pitch_mm=error_budget.source_normal_pitch_mm,
            representation_mm=strict_representation,
        )
    maximum, rms, signed_mean = _weighted_metrics(residuals, weights)
    if maximum > error_budget.maximum_mm or rms > error_budget.rms_mm:
        return None
    if abs(signed_mean) > 0.45 * error_budget.rms_mm:
        return None
    if np.any(np.abs(residuals) > local_maximum):
        return None
    weight_sum = float(np.sum(weights))
    if weight_sum <= 1e-15:
        return None
    normalized_residuals = residuals / local_rms
    normalized_rms = math.sqrt(float(np.sum(weights * normalized_residuals**2)) / weight_sum)
    normalized_bias = float(np.sum(weights * normalized_residuals)) / weight_sum
    if normalized_rms > 1.0 or abs(normalized_bias) > 0.45:
        return None

    if not closed:
        sagitta = radius * (1.0 - math.cos(min(abs(sweep), math.pi) / 2.0))
        if sagitta < max(
            max(source_spacing),
            0.75 * error_budget.maximum_mm,
        ):
            return None

    subset = values[::2]
    if not np.array_equal(subset[-1], values[-1]):
        subset = np.vstack((subset, values[-1]))
    subset_parameters = _circle_subset_parameters(subset)
    if subset_parameters is None:
        return None
    subset_center, subset_radius = subset_parameters
    stability_center = float(np.linalg.norm(subset_center - center))
    stability_radius = abs(float(subset_radius - radius))
    stability_allowance = max(2.0 * error_budget.maximum_mm, 0.02 * radius)
    if max(stability_center, stability_radius) > stability_allowance:
        return None

    endpoint_adjustment = max(
        abs(float(np.linalg.norm(values[0] - center) - radius)),
        abs(float(np.linalg.norm(values[-1] - center) - radius)),
    )
    if endpoint_adjustment > error_budget.endpoint_adjustment_mm:
        return None
    if abs(float(residuals[0])) > float(local_endpoint[0]) or abs(float(residuals[-1])) > float(local_endpoint[-1]):
        return None
    return CircularArcHypothesis(
        center=center.copy(),
        radius_mm=float(radius),
        start_angle_radians=start_angle,
        sweep_radians=sweep,
        support_length_mm=support_length,
        maximum_residual_mm=maximum,
        rms_residual_mm=rms,
        signed_mean_residual_mm=signed_mean,
        maximum_endpoint_adjustment_mm=endpoint_adjustment,
        angular_backtrack_radians=angular_backtrack,
        stability_center_mm=stability_center,
        stability_radius_mm=stability_radius,
        error_budget=error_budget,
    )


def _cubic_power_coefficients(controls: np.ndarray) -> np.ndarray:
    return np.vstack(
        (
            controls[0],
            3.0 * (controls[1] - controls[0]),
            3.0 * (controls[0] - 2.0 * controls[1] + controls[2]),
            -controls[0] + 3.0 * controls[1] - 3.0 * controls[2] + controls[3],
        )
    )


def canonical_cubic_radial_error(
    controls: np.ndarray,
    center: np.ndarray,
    radius_mm: float,
) -> float:
    """Return the exact radial extrema error of one circle-constrained cubic."""

    relative = np.asarray(controls, dtype=np.float64).reshape(4, 2) - np.asarray(
        center,
        dtype=np.float64,
    ).reshape(2)
    coefficients = _cubic_power_coefficients(relative)
    squared = np.polynomial.polynomial.polyadd(
        np.polynomial.polynomial.polymul(coefficients[:, 0], coefficients[:, 0]),
        np.polynomial.polynomial.polymul(coefficients[:, 1], coefficients[:, 1]),
    )
    derivative = np.polynomial.polynomial.polyder(squared)
    roots = np.polynomial.polynomial.polyroots(derivative)
    parameters = [0.0, 1.0]
    parameters.extend(
        float(root.real) for root in roots if abs(float(root.imag)) <= 1e-10 and 0.0 < float(root.real) < 1.0
    )
    radii_squared = np.polynomial.polynomial.polyval(parameters, squared)
    radii = np.sqrt(np.maximum(radii_squared, 0.0))
    return float(np.max(np.abs(radii - float(radius_mm))))


def canonical_cubic_arc_spans(
    hypothesis: CircularArcHypothesis,
    *,
    maximum_segments: int = 64,
) -> tuple[CanonicalCubicArc, ...]:
    """Encode an exact conceptual arc as bounded canonical cubic spans."""

    sweep = float(hypothesis.sweep_radians)
    segment_count = max(1, int(math.ceil(abs(sweep) / (math.pi / 2.0))))
    while segment_count <= maximum_segments:
        delta = sweep / segment_count
        spans: list[CanonicalCubicArc] = []
        maximum_error = 0.0
        first_start: np.ndarray | None = None
        previous_end: np.ndarray | None = None
        closes_full_turn = math.isclose(
            abs(sweep),
            2.0 * math.pi,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        for index in range(segment_count):
            start_angle = hypothesis.start_angle_radians + index * delta
            end_angle = start_angle + delta
            start = (
                hypothesis.center
                + hypothesis.radius_mm
                * np.asarray(
                    (math.cos(start_angle), math.sin(start_angle)),
                    dtype=np.float64,
                )
                if previous_end is None
                else previous_end.copy()
            )
            if first_start is None:
                first_start = start.copy()
            end = hypothesis.center + hypothesis.radius_mm * np.asarray(
                (math.cos(end_angle), math.sin(end_angle)),
                dtype=np.float64,
            )
            if index == segment_count - 1 and closes_full_turn:
                end = first_start.copy()
            start_tangent = np.asarray(
                (-math.sin(start_angle), math.cos(start_angle)),
                dtype=np.float64,
            )
            end_tangent = np.asarray(
                (-math.sin(end_angle), math.cos(end_angle)),
                dtype=np.float64,
            )
            handle = (4.0 / 3.0) * math.tan(delta / 4.0) * hypothesis.radius_mm
            control_1 = start + handle * start_tangent
            control_2 = end - handle * end_tangent
            controls = np.vstack((start, control_1, control_2, end))
            error = canonical_cubic_radial_error(
                controls,
                hypothesis.center,
                hypothesis.radius_mm,
            )
            maximum_error = max(maximum_error, error)
            spans.append(
                CanonicalCubicArc(
                    start=start,
                    control_1=control_1,
                    control_2=control_2,
                    end=end,
                    start_angle_radians=start_angle,
                    sweep_radians=delta,
                    maximum_representation_error_mm=error,
                )
            )
            previous_end = end
        if maximum_error <= hypothesis.error_budget.representation_mm:
            return tuple(spans)
        segment_count *= 2
    raise ValueError("A circular arc exceeds the bounded canonical-cubic span limit")
