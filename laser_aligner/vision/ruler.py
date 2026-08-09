from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class RulerAxisDetection:
    start_image_px: tuple[float, float]
    end_image_px: tuple[float, float]
    tick_pitch_px: float
    tick_candidate_count: int
    periodicity_score: float
    start_snap_distance_px: float
    end_snap_distance_px: float


@dataclass(frozen=True, slots=True)
class HoneycombRulerDetection:
    ruler_origin_image_px: tuple[float, float]
    ruler_x_mark_image_px: tuple[float, float]
    ruler_xy_mark_image_px: tuple[float, float]
    axis_x: RulerAxisDetection
    axis_y: RulerAxisDetection
    corner_error_px: float
    axis_angle_deg: float

    @property
    def image_points(self) -> tuple[tuple[float, float], ...]:
        return (
            self.ruler_origin_image_px,
            self.ruler_x_mark_image_px,
            self.ruler_xy_mark_image_px,
        )


@dataclass(frozen=True, slots=True)
class _Line:
    point: np.ndarray
    direction: np.ndarray


def _point(value: tuple[float, float], label: str) -> np.ndarray:
    point = np.asarray(value, dtype=np.float64)
    if point.shape != (2,) or not np.isfinite(point).all():
        raise ValueError(f"{label} must contain two finite image coordinates")
    return point


def _line_intersection(first: _Line, second: _Line) -> np.ndarray:
    matrix = np.column_stack((first.direction, -second.direction))
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) < 1e-6:
        raise ValueError("The detected ruler axes are parallel")
    scales = np.linalg.solve(matrix, second.point - first.point)
    return first.point + first.direction * float(scales[0])


def _fit_baseline(
    lines: np.ndarray | None,
    start: np.ndarray,
    end: np.ndarray,
    gray: np.ndarray,
    outward: np.ndarray,
    ruler_span_mm: float,
) -> _Line:
    vector = end - start
    length = float(np.linalg.norm(vector))
    if length < 120.0:
        raise ValueError("Ruler endpoint hints are too close together")
    direction = vector / length
    normal = np.asarray((-direction[1], direction[0]), dtype=np.float64)
    if lines is None:
        raise ValueError("Could not find a continuous ruler edge near the hints")

    candidates: list[tuple[float, _Line]] = []
    midpoint = (start + end) * 0.5
    max_distance = max(35.0, length * 0.09)
    minimum_alignment = math.cos(math.radians(8.0))
    for raw in lines[:, 0]:
        first = raw[:2].astype(np.float64)
        second = raw[2:].astype(np.float64)
        candidate_vector = second - first
        candidate_length = float(np.linalg.norm(candidate_vector))
        if candidate_length <= 0.0:
            continue
        candidate_direction = candidate_vector / candidate_length
        alignment = float(np.dot(candidate_direction, direction))
        if abs(alignment) < minimum_alignment:
            continue
        if alignment < 0.0:
            candidate_direction = -candidate_direction
        candidate_midpoint = (first + second) * 0.5
        distance = abs(float(np.dot(candidate_midpoint - midpoint, normal)))
        if distance > max_distance:
            continue
        angle_error = math.degrees(math.acos(min(1.0, abs(alignment))))
        score = candidate_length - 5.0 * distance - 12.0 * angle_error
        candidate = _Line(candidate_midpoint, candidate_direction)
        candidates.append((score, candidate))
    if not candidates:
        raise ValueError("Could not fit a ruler edge close to the endpoint hints")
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    periodic: list[tuple[float, float, _Line]] = []
    for geometry_score, candidate in candidates[:24]:
        candidate_direction = candidate.direction
        if float(np.dot(candidate_direction, end - start)) < 0.0:
            candidate_direction = -candidate_direction
        candidate = _Line(candidate.point, candidate_direction)
        start_along = float(
            np.dot(start - candidate.point, candidate.direction)
        )
        end_along = float(np.dot(end - candidate.point, candidate.direction))
        try:
            _, periodicity = _periodicity(
                gray,
                candidate,
                outward,
                start_along,
                end_along,
                length / ruler_span_mm,
            )
        except ValueError:
            continue
        periodic.append((periodicity, geometry_score, candidate))
    if not periodic:
        raise ValueError(
            "Could not find a ruler edge with a verified repeated tick pattern"
        )
    best_periodicity = max(candidate[0] for candidate in periodic)
    minimum_periodicity = max(0.60, best_periodicity - 0.08)
    eligible = [
        candidate
        for candidate in periodic
        if candidate[0] >= minimum_periodicity
    ]
    eligible.sort(key=lambda candidate: (candidate[1], candidate[0]), reverse=True)
    return eligible[0][2]


def _tick_candidates(
    lines: np.ndarray | None,
    baseline: _Line,
    outward: np.ndarray,
    expected_length: float,
) -> list[tuple[float, float]]:
    normal = np.asarray((-baseline.direction[1], baseline.direction[0]))
    if float(np.dot(normal, outward)) < 0.0:
        normal = -normal
    if lines is None:
        return []
    raw_candidates: list[tuple[float, float]] = []
    for raw in lines[:, 0]:
        first = raw[:2].astype(np.float64)
        second = raw[2:].astype(np.float64)
        segment = second - first
        segment_length = float(np.linalg.norm(segment))
        if segment_length < 5.0:
            continue
        if segment_length > min(80.0, expected_length * 0.12):
            continue
        if abs(float(np.dot(segment / segment_length, baseline.direction))) > 0.34:
            continue
        first_distance = float(np.dot(first - baseline.point, normal))
        second_distance = float(np.dot(second - baseline.point, normal))
        if min(abs(first_distance), abs(second_distance)) > 8.0:
            continue
        outward_extent = max(first_distance, second_distance)
        if outward_extent < 4.0:
            continue
        near = first if abs(first_distance) <= abs(second_distance) else second
        along = float(np.dot(near - baseline.point, baseline.direction))
        if abs(along) > expected_length * 1.25:
            continue
        raw_candidates.append((along, max(segment_length, outward_extent)))

    raw_candidates.sort()
    clusters: list[list[tuple[float, float]]] = []
    for candidate in raw_candidates:
        if not clusters or candidate[0] - clusters[-1][-1][0] > 1.8:
            clusters.append([candidate])
        else:
            clusters[-1].append(candidate)
    return [
        (
            float(np.median([candidate[0] for candidate in cluster])),
            max(candidate[1] for candidate in cluster),
        )
        for cluster in clusters
    ]


def _periodicity(
    gray: np.ndarray,
    baseline: _Line,
    outward: np.ndarray,
    start_along: float,
    end_along: float,
    expected_pitch: float,
) -> tuple[float, float]:
    normal = np.asarray((-baseline.direction[1], baseline.direction[0]))
    if float(np.dot(normal, outward)) < 0.0:
        normal = -normal
    low = min(start_along, end_along) - 20.0
    high = max(start_along, end_along) + 20.0
    along = np.arange(low, high + 1.0, dtype=np.float32)
    across = np.arange(
        2.0,
        max(14.0, min(40.0, expected_pitch * 5.0)),
        dtype=np.float32,
    )
    map_x = (
        baseline.point[0]
        + along[:, None] * baseline.direction[0]
        + across[None, :] * normal[0]
    )
    map_y = (
        baseline.point[1]
        + along[:, None] * baseline.direction[1]
        + across[None, :] * normal[1]
    )
    strip = cv2.remap(
        gray,
        map_x.astype(np.float32),
        map_y.astype(np.float32),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    ).astype(np.float64)
    response = np.mean(strip, axis=1)
    response -= cv2.GaussianBlur(response.reshape(-1, 1), (1, 21), 0).ravel()
    best_score = -1.0
    best_lag = 0
    minimum_lag = max(2, int(math.floor(expected_pitch * 0.75)))
    maximum_lag = max(minimum_lag, int(math.ceil(expected_pitch * 1.25)))
    for lag in range(minimum_lag, maximum_lag + 1):
        left = response[:-lag]
        right = response[lag:]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator <= 1e-9:
            continue
        score = float(np.dot(left, right) / denominator)
        if score > best_score:
            best_score = score
            best_lag = lag
    if best_lag <= 0 or best_score < 0.60:
        raise ValueError(
            "The repeated 1 mm ruler ticks were not detected reliably "
            f"(correlation {max(best_score, 0.0):.2f})"
        )
    return float(best_lag), best_score


def _snap_to_tick(
    seed: np.ndarray,
    baseline: _Line,
    candidates: list[tuple[float, float]],
    pitch: float,
) -> tuple[np.ndarray, float]:
    seed_along = float(np.dot(seed - baseline.point, baseline.direction))
    maximum_distance = max(20.0, pitch * 8.0)
    nearby = [
        candidate
        for candidate in candidates
        if abs(candidate[0] - seed_along) <= maximum_distance
    ]
    if not nearby:
        raise ValueError("No detected ruler tick is close enough to an endpoint hint")
    selected = max(
        nearby,
        key=lambda candidate: (
            -abs(candidate[0] - seed_along) + 0.15 * min(candidate[1], 40.0),
            -abs(candidate[0] - seed_along),
        ),
    )
    snapped = baseline.point + selected[0] * baseline.direction
    return snapped, float(np.linalg.norm(snapped - seed))


def detect_honeycomb_rulers(
    image: np.ndarray,
    seed_points: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ],
    *,
    ruler_span_mm: float = 190.0,
) -> HoneycombRulerDetection:
    """Detect two perpendicular ticked rulers near three approximate hints."""

    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim not in (2, 3)
        or (image.ndim == 3 and image.shape[2] != 3)
    ):
        raise ValueError("Ruler detection requires a grayscale or color image")
    if image.size == 0:
        raise ValueError("Ruler detection image is empty")
    if not isinstance(seed_points, (list, tuple)) or len(seed_points) != 3:
        raise ValueError("Ruler detection requires exactly three endpoint hints")
    span = float(ruler_span_mm)
    if type(ruler_span_mm) is bool or not math.isfinite(span) or span <= 0.0:
        raise ValueError("Ruler span must be a finite positive number")
    origin_seed = _point(seed_points[0], "First ruler hint")
    corner_seed = _point(seed_points[1], "Second ruler hint")
    far_seed = _point(seed_points[2], "Third ruler hint")
    height, width = image.shape[:2]
    for point in (origin_seed, corner_seed, far_seed):
        if not (-1.0 <= point[0] <= width and -1.0 <= point[1] <= height):
            raise ValueError("Ruler endpoint hints must lie inside the captured image")

    gray = (
        image
        if image.ndim == 2
        else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    )
    gray = cv2.GaussianBlur(gray, (3, 3), 0.7)
    median = float(np.median(gray))
    lower = max(20, int(round(0.45 * median)))
    upper = max(lower + 20, min(220, int(round(1.15 * median))))
    edges = cv2.Canny(gray, lower, upper)

    first_seed_length = float(np.linalg.norm(corner_seed - origin_seed))
    second_seed_length = float(np.linalg.norm(far_seed - corner_seed))
    minimum_seed_length = min(first_seed_length, second_seed_length)
    corridor = np.zeros_like(edges)
    corridor_width = max(80, int(round(minimum_seed_length * 0.16)))
    cv2.line(
        corridor,
        tuple(np.rint(origin_seed).astype(int)),
        tuple(np.rint(corner_seed).astype(int)),
        255,
        corridor_width,
    )
    cv2.line(
        corridor,
        tuple(np.rint(corner_seed).astype(int)),
        tuple(np.rint(far_seed).astype(int)),
        255,
        corridor_width,
    )
    corridor_edges = cv2.bitwise_and(edges, corridor)
    long_lines = cv2.HoughLinesP(
        corridor_edges,
        1.0,
        np.pi / 720.0,
        threshold=max(45, int(round(minimum_seed_length * 0.10))),
        minLineLength=max(80, int(round(minimum_seed_length * 0.48))),
        maxLineGap=max(12, int(round(minimum_seed_length * 0.06))),
    )
    short_lines = cv2.HoughLinesP(
        corridor_edges,
        1.0,
        np.pi / 720.0,
        threshold=6,
        minLineLength=5,
        maxLineGap=4,
    )

    x_line = _fit_baseline(
        long_lines,
        origin_seed,
        corner_seed,
        gray,
        far_seed - corner_seed,
        span,
    )
    y_line = _fit_baseline(
        long_lines,
        corner_seed,
        far_seed,
        gray,
        origin_seed - corner_seed,
        span,
    )
    corner = _line_intersection(x_line, y_line)
    corner_error = float(np.linalg.norm(corner - corner_seed))
    seed_scale = max(
        float(np.linalg.norm(corner_seed - origin_seed)),
        float(np.linalg.norm(far_seed - corner_seed)),
    )
    if corner_error > max(30.0, seed_scale * 0.08):
        raise ValueError(
            f"Detected ruler corner is {corner_error:.1f} px from the middle hint"
        )

    x_direction = x_line.direction
    if float(np.dot(x_direction, corner_seed - origin_seed)) < 0.0:
        x_direction = -x_direction
    y_direction = y_line.direction
    if float(np.dot(y_direction, far_seed - corner_seed)) < 0.0:
        y_direction = -y_direction
    x_line = _Line(corner, x_direction)
    y_line = _Line(corner, y_direction)
    axis_angle = math.degrees(
        math.acos(
            min(1.0, abs(float(np.dot(x_direction, y_direction))))
        )
    )
    axis_angle = 180.0 - axis_angle if axis_angle > 90.0 else axis_angle
    if not 76.0 <= axis_angle <= 90.0:
        raise ValueError(
            f"Detected ruler axes are not perpendicular enough ({axis_angle:.1f} deg)"
        )

    x_expected_length = float(np.linalg.norm(corner_seed - origin_seed))
    y_expected_length = float(np.linalg.norm(far_seed - corner_seed))
    x_candidates = _tick_candidates(
        short_lines, x_line, far_seed - corner_seed, x_expected_length
    )
    y_candidates = _tick_candidates(
        short_lines, y_line, origin_seed - corner_seed, y_expected_length
    )
    if len(x_candidates) < max(30, int(span * 0.25)):
        raise ValueError("Too few tick strokes were detected on the first ruler")
    if len(y_candidates) < max(30, int(span * 0.25)):
        raise ValueError("Too few tick strokes were detected on the second ruler")

    origin, origin_snap = _snap_to_tick(
        origin_seed, x_line, x_candidates, x_expected_length / span
    )
    far, far_snap = _snap_to_tick(
        far_seed, y_line, y_candidates, y_expected_length / span
    )
    detected_corner = corner

    x_length = float(np.linalg.norm(detected_corner - origin))
    y_length = float(np.linalg.norm(far - detected_corner))
    x_pitch, x_periodicity = _periodicity(
        gray,
        x_line,
        far_seed - corner_seed,
        float(np.dot(origin - corner, x_direction)),
        float(np.dot(detected_corner - corner, x_direction)),
        x_length / span,
    )
    y_pitch, y_periodicity = _periodicity(
        gray,
        y_line,
        origin_seed - corner_seed,
        float(np.dot(detected_corner - corner, y_direction)),
        float(np.dot(far - corner, y_direction)),
        y_length / span,
    )
    for label, measured_length, pitch in (
        ("first", x_length, x_pitch),
        ("second", y_length, y_pitch),
    ):
        measured_span = measured_length / pitch
        if abs(measured_span - span) > max(4.0, span * 0.03):
            raise ValueError(
                f"The {label} ruler fit spans {measured_span:.1f} detected ticks; "
                f"expected {span:g}"
            )

    return HoneycombRulerDetection(
        ruler_origin_image_px=(float(origin[0]), float(origin[1])),
        ruler_x_mark_image_px=(
            float(detected_corner[0]),
            float(detected_corner[1]),
        ),
        ruler_xy_mark_image_px=(float(far[0]), float(far[1])),
        axis_x=RulerAxisDetection(
            start_image_px=(float(origin[0]), float(origin[1])),
            end_image_px=(float(detected_corner[0]), float(detected_corner[1])),
            tick_pitch_px=x_pitch,
            tick_candidate_count=len(x_candidates),
            periodicity_score=x_periodicity,
            start_snap_distance_px=origin_snap,
            end_snap_distance_px=corner_error,
        ),
        axis_y=RulerAxisDetection(
            start_image_px=(float(detected_corner[0]), float(detected_corner[1])),
            end_image_px=(float(far[0]), float(far[1])),
            tick_pitch_px=y_pitch,
            tick_candidate_count=len(y_candidates),
            periodicity_score=y_periodicity,
            start_snap_distance_px=corner_error,
            end_snap_distance_px=far_snap,
        ),
        corner_error_px=corner_error,
        axis_angle_deg=axis_angle,
    )
