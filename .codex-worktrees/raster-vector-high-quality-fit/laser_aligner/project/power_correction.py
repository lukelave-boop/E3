from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

MAX_POWER_BIAS_FRACTION = 0.50
DEFAULT_RAMP_STEPS = 3
_EPSILON = 1e-9
_MIN_CORRECTION_BLOCK_MM = 0.001
_MIN_CORNER_SEVERITY = 0.01


@dataclass(frozen=True, slots=True)
class CorrectedMotion:
    """One collinear motion endpoint and its commanded controller power."""

    x: float
    y: float
    power: int
    severity: float


def _spaced_boundaries(values: Sequence[float], lower: float, upper: float) -> list[float]:
    """Keep generated blocks distinct at E3's 0.001 mm output precision."""

    interior = sorted(
        value
        for value in values
        if lower + _MIN_CORRECTION_BLOCK_MM
        <= value
        <= upper - _MIN_CORRECTION_BLOCK_MM
    )
    output = [lower]
    for value in interior:
        if value - output[-1] >= _MIN_CORRECTION_BLOCK_MM:
            output.append(value)
    if upper - output[-1] < _MIN_CORRECTION_BLOCK_MM and len(output) > 1:
        output.pop()
    output.append(upper)
    return output


def _merge_same_power(motions: Sequence[CorrectedMotion]) -> list[CorrectedMotion]:
    merged: list[CorrectedMotion] = []
    for motion in motions:
        if merged and merged[-1].power == motion.power:
            merged[-1] = motion
        else:
            merged.append(motion)
    return merged


def corrected_power(
    base_power: int,
    correction: float,
    severity: float,
    power_max: int,
) -> int:
    """Apply a bounded material bias without replacing GRBL M4 scaling.

    At correction magnitude 100 and local severity 1, the commanded value is
    biased by 50 percent of base power. GRBL M4 remains responsible for its
    own instantaneous velocity-dependent PWM scaling.
    """

    if power_max <= 0:
        raise ValueError("power_max must be positive")
    base = max(0, min(int(power_max), int(base_power)))
    value = float(correction)
    local = float(severity)
    if not math.isfinite(value) or not -100.0 <= value <= 100.0:
        raise ValueError("correction must be between -100 and 100")
    if not math.isfinite(local) or not 0.0 <= local <= 1.0:
        raise ValueError("severity must be between 0 and 1")
    if base == 0 or value == 0.0 or local == 0.0:
        return base
    bias = value / 100.0 * MAX_POWER_BIAS_FRACTION * local
    return max(0, min(int(power_max), int(round(base * (1.0 + bias)))))


def corner_severity(
    incoming: Sequence[float],
    outgoing: Sequence[float],
) -> float:
    """Return 0 for straight continuation and 1 for a full reversal."""

    in_x, in_y = float(incoming[0]), float(incoming[1])
    out_x, out_y = float(outgoing[0]), float(outgoing[1])
    in_length = math.hypot(in_x, in_y)
    out_length = math.hypot(out_x, out_y)
    if in_length <= _EPSILON or out_length <= _EPSILON:
        return 0.0
    cosine = (in_x * out_x + in_y * out_y) / (in_length * out_length)
    cosine = max(-1.0, min(1.0, cosine))
    return (1.0 - cosine) / 2.0


def braking_distance_mm(feed_mm_min: float, acceleration_mm_s2: float) -> float:
    feed = float(feed_mm_min)
    acceleration = float(acceleration_mm_s2)
    if not math.isfinite(feed) or feed <= 0:
        raise ValueError("feed_mm_min must be positive and finite")
    if not math.isfinite(acceleration) or acceleration <= 0:
        raise ValueError("acceleration_mm_s2 must be positive and finite")
    velocity_mm_s = feed / 60.0
    return velocity_mm_s * velocity_mm_s / (2.0 * acceleration)


def split_segment_power_profile(
    start: Sequence[float],
    end: Sequence[float],
    *,
    base_power: int,
    correction: float,
    power_max: int,
    start_severity: float = 0.0,
    end_severity: float = 0.0,
    start_zone_mm: float = 0.0,
    end_zone_mm: float = 0.0,
    ramp_steps: int = DEFAULT_RAMP_STEPS,
) -> list[CorrectedMotion]:
    """Split one straight segment into a small piecewise power profile."""

    start_x, start_y = float(start[0]), float(start[1])
    end_x, end_y = float(end[0]), float(end[1])
    delta_x, delta_y = end_x - start_x, end_y - start_y
    length = math.hypot(delta_x, delta_y)
    if length <= _EPSILON:
        return []
    if ramp_steps < 1:
        raise ValueError("ramp_steps must be at least one")
    if correction == 0.0 or (start_severity <= 0.0 and end_severity <= 0.0):
        return [CorrectedMotion(end_x, end_y, int(base_power), 0.0)]

    start_zone = max(0.0, min(length, float(start_zone_mm)))
    end_zone = max(0.0, min(length, float(end_zone_mm)))
    boundaries = {0.0, length}
    if start_severity > 0.0 and start_zone > _EPSILON:
        boundaries.update(start_zone * step / ramp_steps for step in range(1, ramp_steps + 1))
    if end_severity > 0.0 and end_zone > _EPSILON:
        boundaries.update(length - end_zone * step / ramp_steps for step in range(1, ramp_steps + 1))
    unique = _spaced_boundaries(tuple(boundaries), 0.0, length)

    motions: list[CorrectedMotion] = []
    for lower, upper in pairwise(unique):
        midpoint = (lower + upper) / 2.0
        start_local = (
            start_severity * max(0.0, 1.0 - midpoint / start_zone)
            if start_zone > _EPSILON
            else 0.0
        )
        distance_to_end = length - midpoint
        end_local = (
            end_severity * max(0.0, 1.0 - distance_to_end / end_zone)
            if end_zone > _EPSILON
            else 0.0
        )
        local = max(0.0, min(1.0, max(start_local, end_local)))
        ratio = upper / length
        motions.append(
            CorrectedMotion(
                start_x + delta_x * ratio,
                start_y + delta_y * ratio,
                corrected_power(base_power, correction, local, power_max),
                local,
            )
        )
    return _merge_same_power(motions)


def corrected_vector_motions(
    points: Sequence[Sequence[float]],
    *,
    base_power: int,
    correction: float,
    power_max: int,
    feed_mm_min: float,
    acceleration_mm_s2: float,
    ramp_steps: int = DEFAULT_RAMP_STEPS,
) -> list[CorrectedMotion]:
    """Return geometry-preserving powered blocks with localized corner bias."""

    coordinates = [(float(point[0]), float(point[1])) for point in points]
    if len(coordinates) < 2:
        return []
    closed = (
        len(coordinates) >= 4
        and math.hypot(
            coordinates[0][0] - coordinates[-1][0],
            coordinates[0][1] - coordinates[-1][1],
        )
        <= _EPSILON
    )
    vertex_count = len(coordinates) - 1 if closed else len(coordinates)
    severities = [0.0] * vertex_count
    vertex_range = range(vertex_count) if closed else range(1, vertex_count - 1)
    for index in vertex_range:
        previous = coordinates[(index - 1) % vertex_count]
        current = coordinates[index]
        following = coordinates[(index + 1) % vertex_count]
        severity = corner_severity(
            (current[0] - previous[0], current[1] - previous[1]),
            (following[0] - current[0], following[1] - current[1]),
        )
        severities[index] = severity if severity >= _MIN_CORNER_SEVERITY else 0.0

    full_stop_distance = braking_distance_mm(feed_mm_min, acceleration_mm_s2)
    motions: list[CorrectedMotion] = []
    for segment_index in range(len(coordinates) - 1):
        start_index = segment_index
        end_index = (segment_index + 1) % vertex_count
        start_severity = severities[start_index] if start_index < vertex_count else 0.0
        end_severity = severities[end_index] if end_index < vertex_count else 0.0
        motions.extend(
            split_segment_power_profile(
                coordinates[segment_index],
                coordinates[segment_index + 1],
                base_power=base_power,
                correction=correction,
                power_max=power_max,
                start_severity=start_severity,
                end_severity=end_severity,
                start_zone_mm=full_stop_distance * start_severity,
                end_zone_mm=full_stop_distance * end_severity,
                ramp_steps=ramp_steps,
            )
        )
    return motions


def corrected_raster_span_motions(
    row_start: Sequence[float],
    row_end: Sequence[float],
    span_start: Sequence[float],
    span_end: Sequence[float],
    *,
    lead_in_mm: float,
    lead_out_mm: float,
    base_power: int,
    correction: float,
    power_max: int,
    feed_mm_min: float,
    acceleration_mm_s2: float,
    ramp_steps: int = DEFAULT_RAMP_STEPS,
) -> list[CorrectedMotion]:
    """Bias only the image-edge distance not covered by laser-off overscan."""

    row_x, row_y = float(row_start[0]), float(row_start[1])
    end_x, end_y = float(row_end[0]), float(row_end[1])
    delta_x, delta_y = end_x - row_x, end_y - row_y
    row_length = math.hypot(delta_x, delta_y)
    if row_length <= _EPSILON:
        return []
    unit_x, unit_y = delta_x / row_length, delta_y / row_length
    span_start_x, span_start_y = float(span_start[0]), float(span_start[1])
    span_end_x, span_end_y = float(span_end[0]), float(span_end[1])
    span_start_distance = (
        (span_start_x - row_x) * unit_x + (span_start_y - row_y) * unit_y
    )
    span_end_distance = (
        (span_end_x - row_x) * unit_x + (span_end_y - row_y) * unit_y
    )
    if span_end_distance - span_start_distance <= _EPSILON:
        return []
    if correction == 0.0:
        return [CorrectedMotion(span_end_x, span_end_y, int(base_power), 0.0)]

    braking = braking_distance_mm(feed_mm_min, acceleration_mm_s2)
    start_zone = max(0.0, braking - max(0.0, float(lead_in_mm)))
    end_zone = max(0.0, braking - max(0.0, float(lead_out_mm)))
    if start_zone <= _EPSILON and end_zone <= _EPSILON:
        return [CorrectedMotion(span_end_x, span_end_y, int(base_power), 0.0)]
    start_severity = min(1.0, start_zone / braking)
    end_severity = min(1.0, end_zone / braking)

    boundaries = {span_start_distance, span_end_distance}
    for step in range(1, ramp_steps + 1):
        boundaries.add(start_zone * step / ramp_steps)
        boundaries.add(row_length - end_zone * step / ramp_steps)
    unique = _spaced_boundaries(
        tuple(boundaries),
        span_start_distance,
        span_end_distance,
    )

    motions: list[CorrectedMotion] = []
    for lower, upper in pairwise(unique):
        midpoint = (lower + upper) / 2.0
        start_local = (
            start_severity * max(0.0, 1.0 - midpoint / start_zone)
            if start_zone > _EPSILON
            else 0.0
        )
        end_local = (
            end_severity
            * max(0.0, 1.0 - (row_length - midpoint) / end_zone)
            if end_zone > _EPSILON
            else 0.0
        )
        local = max(0.0, min(1.0, max(start_local, end_local)))
        motions.append(
            CorrectedMotion(
                row_x + unit_x * upper,
                row_y + unit_y * upper,
                corrected_power(base_power, correction, local, power_max),
                local,
            )
        )
    return _merge_same_power(motions)
