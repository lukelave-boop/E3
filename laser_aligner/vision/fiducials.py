from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def detect_aruco_markers(
    image: np.ndarray,
    dictionary_name: str = "DICT_4X4_50",
) -> list[dict[str, Any]]:
    if not hasattr(cv2, "aruco"):
        return []
    dictionary_id = getattr(cv2.aruco, dictionary_name, None)
    if dictionary_id is None:
        raise ValueError(f"Unknown ArUco dictionary: {dictionary_name}")
    dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
    if hasattr(cv2.aruco, "ArucoDetector"):
        parameters = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(dictionary, parameters)
        corners, ids, _ = detector.detectMarkers(image)
    else:  # OpenCV < 4.7 compatibility
        parameters = cv2.aruco.DetectorParameters_create()
        corners, ids, _ = cv2.aruco.detectMarkers(image, dictionary, parameters=parameters)
    if ids is None:
        return []

    markers: list[dict[str, Any]] = []
    for marker_id, marker_corners in zip(ids.reshape(-1), corners, strict=True):
        points = np.asarray(marker_corners, dtype=float).reshape(4, 2)
        center = points.mean(axis=0)
        markers.append(
            {
                "id": int(marker_id),
                "center": [float(center[0]), float(center[1])],
                "corners": points.tolist(),
            }
        )
    return markers


def _order_quad(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    diffs = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(diffs)]
    ordered[3] = points[np.argmax(diffs)]
    return ordered


def _largest_plate_quad(gray: np.ndarray) -> np.ndarray | None:
    height, width = gray.shape[:2]
    image_area = float(height * width)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 35, 110)
    edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)),
        iterations=2,
    )
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        area = abs(float(cv2.contourArea(contour)))
        if area < image_area * 0.08:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        quad = _order_quad(approx.reshape(4, 2))
        top = np.linalg.norm(quad[1] - quad[0])
        bottom = np.linalg.norm(quad[2] - quad[3])
        left = np.linalg.norm(quad[3] - quad[0])
        right = np.linalg.norm(quad[2] - quad[1])
        if min(top, bottom, left, right) < 80:
            continue
        aspect = ((top + bottom) * 0.5) / max(1.0, (left + right) * 0.5)
        if 0.65 <= aspect <= 1.55:
            candidates.append((area, quad))
    return None if not candidates else max(candidates, key=lambda item: item[0])[1]


def _cross_response(gray: np.ndarray) -> np.ndarray:
    normalized = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    dark = 255 - normalized
    size = 41
    center = size // 2
    kernel = np.full((size, size), -0.05, dtype=np.float32)
    kernel[center - 1:center + 2, 5:size - 5] = 1.0
    kernel[5:size - 5, center - 1:center + 2] = 1.0
    cv2.circle(kernel, (center, center), 7, 0.35, 1)
    kernel -= kernel.mean()
    response = cv2.filter2D(dark.astype(np.float32), cv2.CV_32F, kernel)
    return cv2.GaussianBlur(response, (5, 5), 0)


def detect_crosshair_grid(
    image: np.ndarray,
    grid_size: int = 5,
    plate_size_mm: float = 220.0,
    coordinates_mm: tuple[float, ...] = (10.0, 60.0, 110.0, 160.0, 210.0),
) -> dict[str, Any]:
    if image is None or image.size == 0:
        return {'detected': False, 'reason': 'Empty image', 'points': []}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    quad = _largest_plate_quad(gray)
    if quad is None:
        return {'detected': False, 'reason': 'Could not find the calibration plate boundary', 'points': []}
    warp_size = 1000
    destination = np.array([[0, 0], [999, 0], [999, 999], [0, 999]], dtype=np.float32)
    image_to_plate = cv2.getPerspectiveTransform(quad, destination)
    plate_to_image = cv2.getPerspectiveTransform(destination, quad)
    warped = cv2.warpPerspective(gray, image_to_plate, (warp_size, warp_size))
    response = _cross_response(warped)
    expected = [value / plate_size_mm * (warp_size - 1) for value in coordinates_mm]
    search_radius = 50
    points = []
    scores = []
    for row_top, machine_y in enumerate(reversed(coordinates_mm)):
        expected_y = expected[len(expected) - 1 - row_top]
        for col, machine_x in enumerate(coordinates_mm):
            expected_x = expected[col]
            x0 = max(0, int(expected_x - search_radius))
            x1 = min(warp_size, int(expected_x + search_radius + 1))
            y0 = max(0, int(expected_y - search_radius))
            y1 = min(warp_size, int(expected_y + search_radius + 1))
            roi = response[y0:y1, x0:x1]
            _, peak, _, location = cv2.minMaxLoc(roi)
            px = float(x0 + location[0])
            py = float(y0 + location[1])
            image_point = cv2.perspectiveTransform(np.asarray([[[px, py]]], dtype=np.float32), plate_to_image)[0, 0]
            row_from_bottom = coordinates_mm.index(machine_y)
            identifier = row_from_bottom * grid_size + col + 1
            scores.append(float(peak))
            points.append({
                'id': identifier,
                'image_x': float(image_point[0]),
                'image_y': float(image_point[1]),
                'machine_x': float(machine_x),
                'machine_y': float(machine_y),
                'label': f'Auto fiducial {identifier}',
                'score': float(peak),
            })
    if len(points) != 25:
        return {'detected': False, 'reason': f'Detected only {len(points)} of 25 expected locations', 'points': points}
    score_array = np.asarray(scores, dtype=np.float64)
    median = float(np.median(score_array))
    minimum = float(np.min(score_array))
    confidence = 'high'
    if median <= 0 or minimum < median * 0.25:
        confidence = 'low'
    elif minimum < median * 0.45:
        confidence = 'medium'
    points.sort(key=lambda point: point['id'])
    return {
        'detected': True,
        'points': points,
        'plate_corners': quad.tolist(),
        'confidence': confidence,
        'orientation': 'X+ camera-right; Y+ camera-up',
    }


def detect_crosshairs_near(
    image: np.ndarray,
    expected_points: list[dict[str, Any]],
    search_radius_px: int = 55,
) -> dict[str, Any]:
    """Refine approximate crosshair locations without needing a plate boundary."""
    if image is None or image.size == 0:
        return {"detected": False, "reason": "Empty image", "points": []}
    if not expected_points:
        return {
            "detected": False,
            "reason": "Need at least one expected crosshair location",
            "points": [],
        }

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    response = _cross_response(gray)
    height, width = gray.shape[:2]
    refined = []
    scores = []

    for target in expected_points:
        expected_x = float(target["image_x"])
        expected_y = float(target["image_y"])
        x0 = max(0, int(round(expected_x)) - search_radius_px)
        x1 = min(width, int(round(expected_x)) + search_radius_px + 1)
        y0 = max(0, int(round(expected_y)) - search_radius_px)
        y1 = min(height, int(round(expected_y)) + search_radius_px + 1)
        roi = response[y0:y1, x0:x1]
        if roi.size == 0:
            return {
                "detected": False,
                "reason": f"Search area for fiducial {target['id']} is outside the image",
                "points": refined,
            }

        _, peak, _, location = cv2.minMaxLoc(roi)
        px = float(x0 + location[0])
        py = float(y0 + location[1])

        radius = 7
        lx0 = max(0, int(px) - radius)
        lx1 = min(width, int(px) + radius + 1)
        ly0 = max(0, int(py) - radius)
        ly1 = min(height, int(py) + radius + 1)
        local = response[ly0:ly1, lx0:lx1]
        local = np.maximum(local - float(np.percentile(local, 35)), 0)
        total = float(local.sum())
        if total > 0:
            yy, xx = np.mgrid[ly0:ly1, lx0:lx1]
            px = float((xx * local).sum() / total)
            py = float((yy * local).sum() / total)

        scores.append(float(peak))
        refined.append(
            {
                "id": int(target["id"]),
                "image_x": px,
                "image_y": py,
                "machine_x": float(target["machine_x"]),
                "machine_y": float(target["machine_y"]),
                "label": f"Auto fiducial {int(target['id'])}",
                "score": float(peak),
                "seed_x": expected_x,
                "seed_y": expected_y,
                "shift_px": float(np.hypot(px - expected_x, py - expected_y)),
            }
        )

    score_array = np.asarray(scores, dtype=np.float64)
    median = float(np.median(score_array))
    minimum = float(np.min(score_array))
    max_shift = max(point["shift_px"] for point in refined)

    confidence = "high"
    if median <= 0 or minimum < median * 0.22 or max_shift > search_radius_px * 0.9:
        confidence = "low"
    elif minimum < median * 0.42 or max_shift > search_radius_px * 0.65:
        confidence = "medium"

    refined.sort(key=lambda point: point["id"])
    return {
        "detected": True,
        "points": refined,
        "confidence": confidence,
        "orientation": "Seeded from current mapping; refined to nearby burned crosses",
        "maximum_seed_shift_px": float(max_shift),
    }


def detect_crosshairs_burst(
    images: list[np.ndarray] | tuple[np.ndarray, ...],
    expected_points: list[dict[str, Any]],
    *,
    search_radius_px: int = 55,
    minimum_valid_frames: int = 1,
    mad_multiplier: float = 3.5,
    outlier_floor_px: float = 0.25,
    max_jitter_rms_px: float = 0.75,
) -> dict[str, Any]:
    """Detect every frame, then robustly aggregate each crosshair center."""

    if not images:
        return {
            "detected": False,
            "reason": "Precision capture contains no frames",
            "points": [],
        }
    required = max(1, int(minimum_valid_frames))
    detections = [
        detect_crosshairs_near(
            image,
            expected_points,
            search_radius_px=search_radius_px,
        )
        for image in images
    ]
    successful = [item for item in detections if item.get("detected")]
    if len(successful) < required:
        return {
            "detected": False,
            "reason": (
                f"Only {len(successful)} of {len(images)} precision frames "
                f"contained all expected marks; {required} are required"
            ),
            "points": [],
            "capture_diagnostics": {
                "requested_frames": len(images),
                "successful_frames": len(successful),
                "minimum_valid_frames": required,
            },
        }

    samples_by_id: dict[int, list[dict[str, Any]]] = {
        int(item["id"]): [] for item in expected_points
    }
    confidence_counts: dict[str, int] = {}
    for frame_index, detection in enumerate(detections):
        confidence = str(detection.get("confidence", "unknown"))
        confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
        if not detection.get("detected"):
            continue
        for point in detection.get("points", []):
            identifier = int(point["id"])
            if identifier in samples_by_id:
                samples_by_id[identifier].append(
                    {**point, "frame_index": int(frame_index)}
                )

    aggregated: list[dict[str, Any]] = []
    rejected_frames: set[int] = set()
    unstable: list[str] = []
    for target in expected_points:
        identifier = int(target["id"])
        samples = samples_by_id[identifier]
        if len(samples) < required:
            unstable.append(
                f"mark {identifier} has {len(samples)} valid frames; {required} required"
            )
            continue
        coordinates = np.asarray(
            [[item["image_x"], item["image_y"]] for item in samples],
            dtype=np.float64,
        )
        initial_center = np.median(coordinates, axis=0)
        distances = np.linalg.norm(coordinates - initial_center, axis=1)
        median_distance = float(np.median(distances))
        mad = float(np.median(np.abs(distances - median_distance)))
        robust_sigma = 1.4826 * mad
        rejection_radius = max(
            float(outlier_floor_px),
            median_distance + float(mad_multiplier) * robust_sigma,
        )
        inlier_mask = distances <= rejection_radius + 1e-12
        inlier_count = int(np.count_nonzero(inlier_mask))
        for sample, is_inlier in zip(samples, inlier_mask, strict=True):
            if not bool(is_inlier):
                rejected_frames.add(int(sample["frame_index"]))
        if inlier_count < required:
            unstable.append(
                f"mark {identifier} retained {inlier_count} stable frames; {required} required"
            )
            continue

        inlier_coordinates = coordinates[inlier_mask]
        center = np.median(inlier_coordinates, axis=0)
        inlier_distances = np.linalg.norm(inlier_coordinates - center, axis=1)
        jitter_rms = float(np.sqrt(np.mean(np.square(inlier_distances))))
        jitter_max = float(np.max(inlier_distances))
        if jitter_rms > float(max_jitter_rms_px):
            unstable.append(
                f"mark {identifier} jitter is {jitter_rms:.3f} px "
                f"(limit {float(max_jitter_rms_px):.3f} px)"
            )

        inlier_samples = [
            item
            for item, is_inlier in zip(samples, inlier_mask, strict=True)
            if bool(is_inlier)
        ]
        expected_x = float(target["image_x"])
        expected_y = float(target["image_y"])
        aggregated.append(
            {
                "id": identifier,
                "image_x": float(center[0]),
                "image_y": float(center[1]),
                "machine_x": float(target["machine_x"]),
                "machine_y": float(target["machine_y"]),
                "label": f"Auto fiducial {identifier}",
                "score": float(
                    np.median([float(item["score"]) for item in inlier_samples])
                ),
                "seed_x": expected_x,
                "seed_y": expected_y,
                "shift_px": float(
                    np.hypot(center[0] - expected_x, center[1] - expected_y)
                ),
                "sample_count": len(samples),
                "inlier_count": inlier_count,
                "outlier_count": len(samples) - inlier_count,
                "jitter_rms_px": jitter_rms,
                "jitter_max_px": jitter_max,
                "mad_px": mad,
                "rejection_radius_px": rejection_radius,
            }
        )

    diagnostics = {
        "requested_frames": len(images),
        "successful_frames": len(successful),
        "minimum_valid_frames": required,
        "rejected_frame_count": len(rejected_frames),
        "rejected_frame_indices": sorted(rejected_frames),
        "confidence_counts": confidence_counts,
        "worst_jitter_rms_px": (
            max((float(item["jitter_rms_px"]) for item in aggregated), default=0.0)
        ),
        "max_jitter_rms_px": float(max_jitter_rms_px),
    }
    if unstable or len(aggregated) != len(expected_points):
        return {
            "detected": False,
            "reason": "Precision capture was unstable: " + "; ".join(unstable),
            "points": aggregated,
            "capture_diagnostics": diagnostics,
        }

    aggregated.sort(key=lambda point: point["id"])
    maximum_shift = max(float(point["shift_px"]) for point in aggregated)
    confidence = "high"
    if confidence_counts.get("low", 0):
        confidence = "low"
    elif confidence_counts.get("medium", 0):
        confidence = "medium"
    return {
        "detected": True,
        "points": aggregated,
        "confidence": confidence,
        "orientation": "Median/MAD aggregate of fresh precision frames",
        "maximum_seed_shift_px": maximum_shift,
        "capture_diagnostics": diagnostics,
    }
