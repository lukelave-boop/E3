from __future__ import annotations

import math
from dataclasses import dataclass, replace

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
    frame_corners_image_px: tuple[tuple[float, float], ...] = ()

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


def _ordered_quad(points: np.ndarray) -> np.ndarray:
    center = np.mean(points, axis=0)
    angles = np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0])
    return points[np.argsort(angles)]


def _intersect_parametric_lines(
    first_point: np.ndarray,
    first_direction: np.ndarray,
    second_point: np.ndarray,
    second_direction: np.ndarray,
) -> np.ndarray:
    matrix = np.column_stack((first_direction, -second_direction))
    if abs(float(np.linalg.det(matrix))) < 1e-9:
        raise ValueError("Detected honeycomb boundary lines are parallel")
    scale = np.linalg.solve(matrix, second_point - first_point)[0]
    return first_point + first_direction * float(scale)


def _refine_to_inner_square(gray: np.ndarray, outer: np.ndarray) -> np.ndarray:
    """Move outer-frame edges inward onto the continuous ruler-square border."""
    center = np.mean(outer, axis=0)
    side_lengths = np.linalg.norm(np.roll(outer, -1, axis=0) - outer, axis=1)
    search_depth = float(np.min(side_lengths) * 0.10)
    fitted: list[tuple[np.ndarray, np.ndarray]] = []
    for index in range(4):
        start = outer[index]
        end = outer[(index + 1) % 4]
        vector = end - start
        length = float(np.linalg.norm(vector))
        direction = vector / length
        normal = np.asarray((-direction[1], direction[0]), dtype=np.float64)
        midpoint = (start + end) * 0.5
        if float(np.dot(center - midpoint, normal)) < 0.0:
            normal = -normal
        along = np.linspace(length * 0.06, length * 0.94, 240)
        best_score = -np.inf
        best_offset = 0.0
        for offset in np.linspace(search_depth * 0.012, search_depth, 80):
            base = start[None, :] + along[:, None] * direction + offset * normal
            minus = base - normal * 2.5
            plus = base + normal * 2.5
            line = base
            def sample(points: np.ndarray) -> np.ndarray:
                return cv2.remap(
                    gray,
                    points[:, 0].astype(np.float32).reshape(-1, 1),
                    points[:, 1].astype(np.float32).reshape(-1, 1),
                    cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE,
                ).ravel().astype(np.float64)

            gradient = np.abs(sample(plus) - sample(minus))
            darkness = 255.0 - sample(line)
            # A real frame/ruler border remains strong over most of its length;
            # honeycomb cell edges produce isolated peaks but weak lower quantiles.
            score = (
                float(np.percentile(gradient, 30)) * 2.0
                + float(np.mean(gradient))
                + float(np.percentile(darkness, 25)) * 0.35
            )
            if score > best_score:
                best_score = score
                best_offset = float(offset)
        fitted.append((start + best_offset * normal, direction))
    corners = []
    for index in range(4):
        previous = fitted[(index - 1) % 4]
        current = fitted[index]
        corners.append(
            _intersect_parametric_lines(
                previous[0], previous[1], current[0], current[1]
            )
        )
    return np.asarray(corners, dtype=np.float64)


def _detect_honeycomb_frame_unseeded(image: np.ndarray) -> np.ndarray:
    """Measure four fresh honeycomb edges in one already-validated image."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    minimum_dimension = min(height, width)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0.7), 35, 120)
    window = max(15, int(round(minimum_dimension * 0.045))) | 1
    density = cv2.boxFilter(
        (edges > 0).astype(np.float32),
        -1,
        (window, window),
        normalize=True,
    )
    mask = np.where(density >= 0.075, 255, 0).astype(np.uint8)
    close_size = max(9, int(round(minimum_dimension * 0.035))) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, np.ndarray]] = []
    image_area = float(width * height)
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < image_area * 0.20:
            continue
        rectangle = cv2.minAreaRect(contour)
        box = cv2.boxPoints(rectangle).astype(np.float64)
        box_area = float(rectangle[1][0] * rectangle[1][1])
        if box_area <= 0.0 or area / box_area < 0.55:
            continue
        side_lengths = np.linalg.norm(np.roll(box, -1, axis=0) - box, axis=1)
        aspect = float(np.max(side_lengths) / max(np.min(side_lengths), 1e-9))
        if aspect > 1.8:
            continue
        candidates.append((area / box_area + area / image_area, _ordered_quad(box)))
    if not candidates:
        raise ValueError("Could not segment one dominant rectangular honeycomb region")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return _refine_to_inner_square(gray, candidates[0][1])


def _sample_gray(gray: np.ndarray, points: np.ndarray) -> np.ndarray:
    return cv2.remap(
        gray,
        points[:, 0].astype(np.float32).reshape(-1, 1),
        points[:, 1].astype(np.float32).reshape(-1, 1),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    ).ravel().astype(np.float64)


def _fit_seeded_honeycomb_edges(gray: np.ndarray, seed: np.ndarray) -> np.ndarray:
    """Fit one fresh straight edge near each taught edge without changing topology."""

    seed_lengths = np.linalg.norm(np.roll(seed, -1, axis=0) - seed, axis=1)
    if float(np.min(seed_lengths)) < 80.0:
        raise ValueError("Registered honeycomb edge seed is too short")
    # The registration should already be close. Keep the search strip narrow
    # enough that the outer frame/image border cannot replace the taught cutting
    # border, while allowing a few pixels of registration and edge-fit error.
    maximum_displacement = float(np.clip(np.min(seed_lengths) * 0.012, 5.0, 12.0))
    fitted: list[tuple[np.ndarray, np.ndarray]] = []
    for index in range(4):
        start = seed[index]
        end = seed[(index + 1) % 4]
        vector = end - start
        length = float(np.linalg.norm(vector))
        seed_direction = vector / length
        seed_normal = np.asarray((-seed_direction[1], seed_direction[0]))
        along = np.linspace(length * 0.07, length * 0.93, 320)
        angle_candidates = np.linspace(-0.45, 0.45, 7)
        offset_candidates = np.linspace(
            -maximum_displacement,
            maximum_displacement,
            max(25, int(round(maximum_displacement * 2.0)) + 1),
        )
        best_score = -np.inf
        best_line: tuple[np.ndarray, np.ndarray] | None = None
        for angle_degrees in angle_candidates:
            angle = math.radians(float(angle_degrees))
            cosine = math.cos(angle)
            sine = math.sin(angle)
            direction = np.asarray(
                (
                    seed_direction[0] * cosine - seed_direction[1] * sine,
                    seed_direction[0] * sine + seed_direction[1] * cosine,
                ),
                dtype=np.float64,
            )
            normal = np.asarray((-direction[1], direction[0]), dtype=np.float64)
            midpoint = (start + end) * 0.5
            for offset in offset_candidates:
                base = (
                    midpoint[None, :]
                    + (along - length * 0.5)[:, None] * direction
                    + float(offset) * seed_normal
                )
                # Score a continuous fresh intensity transition along the full
                # taught edge, not texture density or a projected template line.
                minus = _sample_gray(gray, base - normal * 1.5)
                plus = _sample_gray(gray, base + normal * 1.5)
                signed_gradient = plus - minus
                sign = 1.0 if float(np.median(signed_gradient)) >= 0.0 else -1.0
                coherent_gradient = signed_gradient * sign
                coherent = float(np.percentile(coherent_gradient, 35))
                strength = float(np.mean(np.clip(coherent_gradient, 0.0, 100.0)))
                coverage = float(np.mean(coherent_gradient >= 10.0))
                displacement_penalty = 0.18 * abs(float(offset))
                angle_penalty = 1.2 * abs(float(angle_degrees))
                score = (
                    coherent * 2.5
                    + strength
                    + coverage * 30.0
                    - displacement_penalty
                    - angle_penalty
                )
                if score > best_score:
                    best_score = score
                    best_line = (midpoint + float(offset) * seed_normal, direction)
        if best_line is None or best_score < 18.0:
            raise ValueError(
                f"Could not freshly fit taught honeycomb edge {index + 1}"
            )
        fitted.append(best_line)

    corners: list[np.ndarray] = []
    for index in range(4):
        previous = fitted[(index - 1) % 4]
        current = fitted[index]
        corner = _intersect_parametric_lines(
            previous[0],
            previous[1],
            current[0],
            current[1],
        )
        displacement = float(np.linalg.norm(corner - seed[index]))
        if displacement > maximum_displacement * 1.8:
            raise ValueError(
                "Fresh honeycomb edge intersection moved too far from its taught corner"
            )
        corners.append(corner)
    return np.asarray(corners, dtype=np.float64)


def detect_honeycomb_frame(
    image: np.ndarray,
    *,
    seed_corners: np.ndarray | None = None,
) -> np.ndarray:
    """Measure the four physical honeycomb edges from the current image.

    A registered reference supplies only narrow search strips and corner
    topology. Its projected corners are never returned as measurements.
    """
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim not in (2, 3)
        or (image.ndim == 3 and image.shape[2] != 3)
        or image.size == 0
    ):
        raise ValueError("Honeycomb-frame detection requires a non-empty uint8 image")
    if seed_corners is None:
        return _detect_honeycomb_frame_unseeded(image)

    seed = np.asarray(seed_corners, dtype=np.float64)
    if seed.shape != (4, 2) or not np.isfinite(seed).all():
        raise ValueError("Honeycomb search seed must contain four finite corners")
    height, width = image.shape[:2]
    if (
        np.any(seed[:, 0] < 0.0)
        or np.any(seed[:, 0] >= width)
        or np.any(seed[:, 1] < 0.0)
        or np.any(seed[:, 1] >= height)
    ):
        raise ValueError("Registered honeycomb search seed leaves the image")
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return _fit_seeded_honeycomb_edges(gray, seed)


def register_honeycomb_reference(
    image: np.ndarray,
    reference_image: np.ndarray,
    reference_corners: np.ndarray,
) -> np.ndarray:
    """Project taught cutting-surface corners into a fresh homed-bed image."""
    for value, label in ((image, "Live"), (reference_image, "Reference")):
        if (
            not isinstance(value, np.ndarray)
            or value.dtype != np.uint8
            or value.ndim not in (2, 3)
            or value.size == 0
        ):
            raise ValueError(f"{label} honeycomb image must be a non-empty uint8 image")
    corners = np.asarray(reference_corners, dtype=np.float32)
    if corners.shape != (4, 2) or not np.isfinite(corners).all():
        raise ValueError("Taught honeycomb corners must contain four finite points")
    live_gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    reference_gray = (
        reference_image
        if reference_image.ndim == 2
        else cv2.cvtColor(reference_image, cv2.COLOR_BGR2GRAY)
    )
    detector = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.02)
    reference_keys, reference_descriptors = detector.detectAndCompute(
        reference_gray, None
    )
    live_keys, live_descriptors = detector.detectAndCompute(live_gray, None)
    if reference_descriptors is None or live_descriptors is None:
        raise ValueError("Could not find enough visual features in the homed-bed images")
    matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(
        reference_descriptors,
        live_descriptors,
        k=2,
    )
    good = [first for first, second in matches if first.distance < 0.72 * second.distance]
    if len(good) < 30:
        raise ValueError(
            f"Homed-bed reference registration found only {len(good)} reliable matches"
        )
    source = np.float32([reference_keys[item.queryIdx].pt for item in good])
    target = np.float32([live_keys[item.trainIdx].pt for item in good])
    # Registration used as execution evidence must follow the movable support,
    # not stationary material outside it.  Fit from features whose taught-image
    # locations lie inside the accepted cutting-surface quadrilateral.
    support_polygon = corners.reshape(-1, 1, 2)
    support_mask = np.asarray(
        [
            cv2.pointPolygonTest(
                support_polygon,
                (float(point[0]), float(point[1])),
                False,
            )
            >= 0.0
            for point in source
        ],
        dtype=bool,
    )
    source = source[support_mask]
    target = target[support_mask]
    if len(source) < 30:
        raise ValueError(
            "Homed-bed reference registration found only "
            f"{len(source)} reliable support-local matches"
        )
    transform, inliers = cv2.findHomography(source, target, cv2.RANSAC, 3.0)
    inlier_count = int(np.count_nonzero(inliers)) if inliers is not None else 0
    if transform is None or inlier_count < 20:
        raise ValueError(
            f"Homed-bed reference registration retained only {inlier_count} inliers"
        )
    inlier_source = source[np.asarray(inliers).ravel().astype(bool)]
    taught_span = np.ptp(corners, axis=0)
    inlier_span = np.ptp(inlier_source, axis=0)
    center = np.mean(corners, axis=0)
    if (
        np.any(taught_span <= 0.0)
        or np.any(inlier_span < taught_span * 0.25)
        or np.count_nonzero(inlier_source[:, 0] < center[0]) < 10
        or np.count_nonzero(inlier_source[:, 0] >= center[0]) < 10
        or np.count_nonzero(inlier_source[:, 1] < center[1]) < 10
        or np.count_nonzero(inlier_source[:, 1] >= center[1]) < 10
    ):
        raise ValueError(
            "Homed-bed reference registration did not cover enough of the "
            "accepted honeycomb surface"
        )
    projected = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), transform)[
        :, 0, :
    ].astype(np.float64)
    height, width = live_gray.shape
    if (
        not np.isfinite(projected).all()
        or np.any(projected[:, 0] < 0.0)
        or np.any(projected[:, 0] >= width)
        or np.any(projected[:, 1] < 0.0)
        or np.any(projected[:, 1] >= height)
    ):
        raise ValueError("Registered honeycomb cutting-surface corners leave the image")
    return projected


def detect_honeycomb_rulers_automatically(
    image: np.ndarray,
    *,
    ruler_span_mm: float = 190.0,
) -> tuple[HoneycombRulerDetection, ...]:
    """Detect a frame, then try every corner and X/Y ruler assignment."""
    frame = detect_honeycomb_frame(image)
    results: list[HoneycombRulerDetection] = []
    failures: list[str] = []
    for corner_index in range(4):
        frame_corner = frame[corner_index]
        neighbors = (
            frame[(corner_index - 1) % 4],
            frame[(corner_index + 1) % 4],
        )
        for x_neighbor, y_neighbor in (neighbors, neighbors[::-1]):
            x_vector = x_neighbor - frame_corner
            y_vector = y_neighbor - frame_corner
            # Segmentation follows the dense honeycomb/frame boundary, while
            # printed ruler baselines may sit just inside or outside it. Search
            # a bounded family of parallel corridor offsets instead of assuming
            # the two visual structures share an identical pixel corner.
            for x_offset in (-0.045, 0.0, 0.045):
                for y_offset in (-0.045, 0.0, 0.045):
                    corner = frame_corner + x_vector * x_offset + y_vector * y_offset
                    x_hint = corner + x_vector * 0.72
                    y_hint = corner + y_vector * 0.72
                    try:
                        result = detect_honeycomb_rulers(
                            image,
                            (tuple(x_hint), tuple(corner), tuple(y_hint)),
                            ruler_span_mm=ruler_span_mm,
                        )
                    except ValueError as exc:
                        failures.append(str(exc))
                        continue
                    candidate = replace(
                        result,
                        frame_corners_image_px=tuple(
                            (float(point[0]), float(point[1])) for point in frame
                        ),
                    )
                    if not any(
                        np.linalg.norm(
                            np.asarray(candidate.ruler_x_mark_image_px)
                            - np.asarray(existing.ruler_x_mark_image_px)
                        )
                        < 5.0
                        and np.linalg.norm(
                            np.asarray(candidate.ruler_origin_image_px)
                            - np.asarray(existing.ruler_origin_image_px)
                        )
                        < 5.0
                        for existing in results
                    ):
                        results.append(candidate)
    if not results:
        detail = ""
        if failures:
            counts = {message: failures.count(message) for message in set(failures)}
            strongest = max(counts, key=counts.get)
            detail = f" Most candidate corridors failed because: {strongest}."
        raise ValueError(
            "The honeycomb rectangle was found, but its X/Y ruler ticks and shared zero were not."
            + detail
        )
    return tuple(results)


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
    scores: dict[float, float] = {}
    # Hints identify a ruler corridor, not its endpoints, so their length is
    # only a weak pitch prior. Search the practical camera range broadly and
    # use that prior only to break similarly periodic alternatives.
    minimum_lag = 2.5
    maximum_lag = min(24.0, max(12.0, expected_pitch * 2.5))
    sample_positions = np.arange(response.size, dtype=np.float64)
    for lag in np.arange(minimum_lag, maximum_lag + 0.025, 0.05):
        usable = sample_positions + lag <= sample_positions[-1]
        left = response[usable]
        right = np.interp(sample_positions[usable] + lag, sample_positions, response)
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator <= 1e-9:
            continue
        score = float(np.dot(left, right) / denominator)
        scores[float(lag)] = score
    if not scores:
        best_score = -1.0
        best_lag = 0
    else:
        best_lag, best_score = max(
            scores.items(),
            key=lambda item: item[1]
            - 0.05 * abs(math.log(item[0] / max(expected_pitch, 1e-9))),
        )
    if best_lag <= 0 or best_score < 0.60:
        raise ValueError(
            "The repeated 1 mm ruler ticks were not detected reliably "
            f"(correlation {max(best_score, 0.0):.2f})"
        )
    return float(best_lag), best_score


def _ruler_extent_from_corner(
    corner: np.ndarray,
    direction: np.ndarray,
    pitch_px: float,
    span_mm: float,
    *,
    reverse: bool,
) -> np.ndarray:
    """Project a physical ruler span from its detected zero intersection."""
    sign = -1.0 if reverse else 1.0
    return corner + direction * pitch_px * span_mm * sign


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
        raise ValueError("Ruler detection requires exactly three direction/corner hints")
    span = float(ruler_span_mm)
    if type(ruler_span_mm) is bool or not math.isfinite(span) or span <= 0.0:
        raise ValueError("Ruler span must be a finite positive number")
    origin_seed = _point(seed_points[0], "X ruler hint")
    corner_seed = _point(seed_points[1], "Shared ruler zero hint")
    far_seed = _point(seed_points[2], "Y ruler hint")
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
        raise ValueError("Too few tick strokes were detected on the X ruler")
    if len(y_candidates) < max(30, int(span * 0.25)):
        raise ValueError("Too few tick strokes were detected on the Y ruler")

    detected_corner = corner
    x_pitch, x_periodicity = _periodicity(
        gray,
        x_line,
        far_seed - corner_seed,
        -x_expected_length,
        0.0,
        x_expected_length / span,
    )
    y_pitch, y_periodicity = _periodicity(
        gray,
        y_line,
        origin_seed - corner_seed,
        0.0,
        y_expected_length,
        y_expected_length / span,
    )
    origin = _ruler_extent_from_corner(
        detected_corner,
        x_direction,
        x_pitch,
        span,
        reverse=True,
    )
    far = _ruler_extent_from_corner(
        detected_corner,
        y_direction,
        y_pitch,
        span,
        reverse=False,
    )
    origin_snap = float(np.linalg.norm(origin - origin_seed))
    far_snap = float(np.linalg.norm(far - far_seed))
    for label, point in (("X", origin), ("Y", far)):
        if not (0.0 <= point[0] < width and 0.0 <= point[1] < height):
            raise ValueError(
                f"The detected {span:g} mm {label} ruler extent leaves the image"
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
