from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np


def _image_validation_error(image: object) -> str | None:
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        return "Empty image"
    if (
        image.dtype != np.uint8
        or image.ndim not in (2, 3)
        or (image.ndim == 3 and image.shape[2] != 3)
    ):
        return "Image must be a non-empty uint8 grayscale or BGR array"
    return None


def detect_aruco_markers(
    image: np.ndarray,
    dictionary_name: str = "DICT_4X4_50",
) -> list[dict[str, Any]]:
    image_error = _image_validation_error(image)
    if image_error == "Empty image":
        return []
    if image_error is not None:
        raise ValueError(image_error)
    if not isinstance(dictionary_name, str):
        raise ValueError("ArUco dictionary name must be a string")
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
    kernel[center - 1 : center + 2, 5 : size - 5] = 1.0
    kernel[5 : size - 5, center - 1 : center + 2] = 1.0
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
    image_error = _image_validation_error(image)
    if image_error is not None:
        return {"detected": False, "reason": image_error, "points": []}
    if type(grid_size) is not int or grid_size < 2:
        return {
            "detected": False,
            "reason": "Grid size must be an integer of at least two",
            "points": [],
        }
    try:
        plate_size = float(plate_size_mm)
        coordinates = tuple(float(value) for value in coordinates_mm)
    except (TypeError, ValueError):
        return {
            "detected": False,
            "reason": "Calibration grid coordinates are invalid",
            "points": [],
        }
    if (
        type(plate_size_mm) is bool
        or not math.isfinite(plate_size)
        or plate_size <= 0.0
        or len(coordinates) != grid_size
        or not all(math.isfinite(value) for value in coordinates)
        or any(
            left >= right
            for left, right in zip(coordinates, coordinates[1:], strict=False)
        )
        or coordinates[0] < 0.0
        or coordinates[-1] > plate_size
    ):
        return {
            "detected": False,
            "reason": "Calibration grid coordinates are invalid",
            "points": [],
        }
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    quad = _largest_plate_quad(gray)
    if quad is None:
        return {"detected": False, "reason": "Could not find the calibration plate boundary", "points": []}
    warp_size = 1000
    destination = np.array([[0, 0], [999, 0], [999, 999], [0, 999]], dtype=np.float32)
    image_to_plate = cv2.getPerspectiveTransform(quad, destination)
    plate_to_image = cv2.getPerspectiveTransform(destination, quad)
    warped = cv2.warpPerspective(gray, image_to_plate, (warp_size, warp_size))
    response = _cross_response(warped)
    expected = [value / plate_size * (warp_size - 1) for value in coordinates]
    search_radius = 50
    points = []
    scores = []
    for row_top, machine_y in enumerate(reversed(coordinates)):
        expected_y = expected[len(expected) - 1 - row_top]
        for col, machine_x in enumerate(coordinates):
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
            row_from_bottom = coordinates.index(machine_y)
            identifier = row_from_bottom * grid_size + col + 1
            scores.append(float(peak))
            points.append(
                {
                    "id": identifier,
                    "image_x": float(image_point[0]),
                    "image_y": float(image_point[1]),
                    "machine_x": float(machine_x),
                    "machine_y": float(machine_y),
                    "label": f"Auto fiducial {identifier}",
                    "score": float(peak),
                }
            )
    expected_count = grid_size * grid_size
    if len(points) != expected_count:
        return {
            "detected": False,
            "reason": (
                f"Detected only {len(points)} of {expected_count} expected locations"
            ),
            "points": points,
        }
    score_array = np.asarray(scores, dtype=np.float64)
    median = float(np.median(score_array))
    minimum = float(np.min(score_array))
    confidence = "high"
    if median <= 0 or minimum < median * 0.25:
        confidence = "low"
    elif minimum < median * 0.45:
        confidence = "medium"
    points.sort(key=lambda point: point["id"])
    return {
        "detected": True,
        "points": points,
        "plate_corners": quad.tolist(),
        "confidence": confidence,
        "orientation": "X+ camera-right; Y+ camera-up",
    }


def detect_keyed_crosshair_grid(
    image: np.ndarray,
    targets: list[dict[str, Any]],
    *,
    grid_size: int = 5,
    large_key_index: tuple[int, int] = (1, 1),
    medium_key_index: tuple[int, int] = (1, 2),
) -> dict[str, Any]:
    """Detect and orient a regular cross grid using two larger keyed crosses."""
    image_error = _image_validation_error(image)
    if image_error is not None:
        return {"detected": False, "reason": image_error, "points": []}
    if type(grid_size) is not int or grid_size < 2:
        return {
            "detected": False,
            "reason": "Grid size must be an integer of at least two",
            "points": [],
        }
    key_indices = (large_key_index, medium_key_index)
    if any(
        not isinstance(index, tuple)
        or len(index) != 2
        or any(type(value) is not int or value < 0 or value >= grid_size for value in index)
        for index in key_indices
    ) or large_key_index == medium_key_index:
        return {
            "detected": False,
            "reason": "Orientation-key indices are invalid",
            "points": [],
        }
    if len(targets) != grid_size * grid_size:
        return {
            "detected": False,
            "reason": f"Expected {grid_size * grid_size} base-grid targets; found {len(targets)}",
            "points": [],
        }

    try:
        normalized = []
        for item in targets:
            if type(item["id"]) is not int:
                raise ValueError
            normalized.append(
                {
                    "id": item["id"],
                    "machine_x": float(item["machine_x"]),
                    "machine_y": float(item["machine_y"]),
                }
            )
    except (KeyError, TypeError, ValueError):
        return {"detected": False, "reason": "Base-grid target metadata is invalid", "points": []}
    values = np.asarray(
        [[item["machine_x"], item["machine_y"]] for item in normalized],
        dtype=np.float64,
    )
    identifiers = [item["id"] for item in normalized]
    if not np.isfinite(values).all() or len(set(identifiers)) != len(identifiers):
        return {"detected": False, "reason": "Base-grid targets must be finite and uniquely numbered", "points": []}
    xs = sorted({item["machine_x"] for item in normalized})
    ys = sorted({item["machine_y"] for item in normalized})
    by_coordinate = {
        (item["machine_x"], item["machine_y"]): item for item in normalized
    }
    if (
        len(xs) != grid_size
        or len(ys) != grid_size
        or any((x, y) not in by_coordinate for y in ys for x in xs)
    ):
        return {
            "detected": False,
            "reason": "Base-grid targets do not form one complete regular 5x5 grid",
            "points": [],
        }

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    params = cv2.SimpleBlobDetector_Params()
    params.minThreshold = 0
    params.maxThreshold = 220
    params.thresholdStep = 10
    params.filterByArea = True
    params.minArea = 10
    params.maxArea = max(3000.0, float(gray.shape[0] * gray.shape[1]) * 0.004)
    params.filterByCircularity = False
    params.filterByConvexity = False
    params.filterByInertia = False
    params.filterByColor = True
    params.blobColor = 0
    blob_detector = cv2.SimpleBlobDetector_create(params)
    keypoints = blob_detector.detect(gray)
    # Isolate plausible cross intersections before asking OpenCV to assemble the
    # lattice. Running findCirclesGrid on the photograph directly lets bed
    # edges, rulers, screws, and the parked head dominate its clustering.
    centers = None
    minimum_dimension = float(min(gray.shape[:2]))
    for maximum_factor in (0.023, 0.03, 0.04, 0.05):
        for minimum_factor in (0.0074, 0.0065, 0.0085, 0.0055):
            candidate_image = np.full(gray.shape, 255, dtype=np.uint8)
            candidate_radius = max(3, int(round(minimum_dimension * 0.0055)))
            minimum_size = max(4.0, minimum_dimension * minimum_factor)
            maximum_size = max(
                minimum_size + 1.0, minimum_dimension * maximum_factor
            )
            for point in keypoints:
                if minimum_size <= point.size <= maximum_size:
                    cv2.circle(
                        candidate_image,
                        (int(round(point.pt[0])), int(round(point.pt[1]))),
                        candidate_radius,
                        0,
                        -1,
                    )
            candidate_params = cv2.SimpleBlobDetector_Params()
            candidate_params.minThreshold = 0
            candidate_params.maxThreshold = 220
            candidate_params.filterByArea = True
            candidate_params.minArea = max(8.0, candidate_radius**2)
            candidate_params.maxArea = float((candidate_radius * 4) ** 2)
            candidate_params.filterByCircularity = False
            candidate_params.filterByConvexity = False
            candidate_params.filterByInertia = False
            candidate_params.filterByColor = True
            candidate_params.blobColor = 0
            candidate_detector = cv2.SimpleBlobDetector_create(candidate_params)
            for flags in (
                cv2.CALIB_CB_SYMMETRIC_GRID | cv2.CALIB_CB_CLUSTERING,
                cv2.CALIB_CB_SYMMETRIC_GRID,
            ):
                found, candidate_centers = cv2.findCirclesGrid(
                    candidate_image,
                    (grid_size, grid_size),
                    flags=flags,
                    blobDetector=candidate_detector,
                )
                if found and candidate_centers is not None:
                    centers = candidate_centers
                    break
            if centers is not None:
                break
        if centers is not None:
            break
    found = centers is not None
    if not found or centers is None or len(centers) != grid_size * grid_size:
        return {
            "detected": False,
            "reason": "Could not find all 25 keyed base-grid crosses",
            "points": [],
        }

    center_matrix = np.asarray(centers, dtype=np.float64).reshape(grid_size, grid_size, 2)
    horizontal = np.linalg.norm(np.diff(center_matrix, axis=1), axis=2).reshape(-1)
    vertical = np.linalg.norm(np.diff(center_matrix, axis=0), axis=2).reshape(-1)
    spacing = float(np.median(np.concatenate((horizontal, vertical))))
    if not np.isfinite(spacing) or spacing <= 0:
        return {"detected": False, "reason": "Detected base grid has invalid spacing", "points": []}

    # Measure the length of both dark arms against nearby parallel background
    # strips. Blob diameter mostly describes the center intersection and proved
    # unstable for lightly marked real paper.
    gray_float = gray.astype(np.float32)
    radius = max(8, int(round(spacing * 0.22)))
    band = max(1, int(round(minimum_dimension * 0.0015)))
    gap = max(band + 2, int(round(minimum_dimension * 0.005)))
    cross_scores: list[float] = []
    for center_x, center_y in center_matrix.reshape(-1, 2):
        x = int(round(float(center_x)))
        y = int(round(float(center_y)))
        if (
            x - radius < 0
            or x + radius >= gray.shape[1]
            or y - radius < 0
            or y + radius >= gray.shape[0]
            or x - gap - band < 0
            or x + gap + band >= gray.shape[1]
            or y - gap - band < 0
            or y + gap + band >= gray.shape[0]
        ):
            return {"detected": False, "reason": "Detected base grid is too close to the image edge", "points": []}
        horizontal = gray_float[
            y - band : y + band + 1, x - radius : x + radius + 1
        ].mean(axis=0)
        horizontal_background = np.concatenate(
            (
                gray_float[
                    y - gap - band : y - gap + band + 1,
                    x - radius : x + radius + 1,
                ],
                gray_float[
                    y + gap - band : y + gap + band + 1,
                    x - radius : x + radius + 1,
                ],
            ),
            axis=0,
        ).mean(axis=0)
        vertical = gray_float[
            y - radius : y + radius + 1, x - band : x + band + 1
        ].mean(axis=1)
        vertical_background = np.concatenate(
            (
                gray_float[
                    y - radius : y + radius + 1,
                    x - gap - band : x - gap + band + 1,
                ],
                gray_float[
                    y - radius : y + radius + 1,
                    x + gap - band : x + gap + band + 1,
                ],
            ),
            axis=1,
        ).mean(axis=1)
        cross_scores.append(
            float(np.count_nonzero(horizontal_background - horizontal > 3.0))
            + float(np.count_nonzero(vertical_background - vertical > 3.0))
        )

    size_matrix = np.asarray(cross_scores, dtype=np.float64).reshape(grid_size, grid_size)
    ranked = np.argsort(size_matrix.reshape(-1))[::-1]
    large_flat, medium_flat = int(ranked[0]), int(ranked[1])
    remaining = np.delete(size_matrix.reshape(-1), [large_flat, medium_flat])
    regular_median = float(np.median(remaining))
    regular_maximum = float(np.max(remaining))
    large_size = float(size_matrix.reshape(-1)[large_flat])
    medium_size = float(size_matrix.reshape(-1)[medium_flat])
    if (
        regular_median <= 0
        or large_size / regular_median < 1.40
        or medium_size / regular_median < 1.18
        or large_size / medium_size < 1.12
        or medium_size / max(regular_maximum, 1e-9) < 1.08
    ):
        return {
            "detected": False,
            "reason": "The two orientation-key crosses are missing or ambiguous",
            "points": [],
            "key_sizes_px": {
                "large": large_size,
                "medium": medium_size,
                "regular_median": regular_median,
                "regular_maximum": regular_maximum,
            },
        }

    oriented_centers: np.ndarray | None = None
    orientation_name = ""
    candidates = 0
    for rotations in range(4):
        rotated_centers = np.rot90(center_matrix, rotations, axes=(0, 1))
        rotated_sizes = np.rot90(size_matrix, rotations, axes=(0, 1))
        for mirrored in (False, True):
            candidate_centers = np.fliplr(rotated_centers) if mirrored else rotated_centers
            candidate_sizes = np.fliplr(rotated_sizes) if mirrored else rotated_sizes
            if (
                np.unravel_index(int(np.argmax(candidate_sizes)), candidate_sizes.shape)
                == large_key_index
            ):
                without_large = candidate_sizes.copy()
                without_large[large_key_index] = -np.inf
                if (
                    np.unravel_index(int(np.argmax(without_large)), without_large.shape)
                    == medium_key_index
                ):
                    candidates += 1
                    oriented_centers = candidate_centers.copy()
                    orientation_name = f"rotation={rotations * 90}, mirrored={mirrored}"
    if candidates != 1 or oriented_centers is None:
        return {
            "detected": False,
            "reason": "The keyed base-grid orientation is ambiguous",
            "points": [],
        }

    points: list[dict[str, Any]] = []
    for row, machine_y in enumerate(ys):
        for column, machine_x in enumerate(xs):
            target = by_coordinate[(machine_x, machine_y)]
            image_x, image_y = oriented_centers[row, column]
            points.append(
                {
                    **target,
                    "image_x": float(image_x),
                    "image_y": float(image_y),
                    "label": f"Automatic base mark {target['id']}",
                }
            )
    points.sort(key=lambda point: point["id"])
    return {
        "detected": True,
        "points": points,
        "confidence": "high",
        "orientation": orientation_name,
        "key_sizes_px": {
            "large": large_size,
            "medium": medium_size,
            "regular_median": regular_median,
            "regular_maximum": regular_maximum,
        },
    }


def detect_crosshairs_near(
    image: np.ndarray,
    expected_points: list[dict[str, Any]],
    search_radius_px: int = 55,
) -> dict[str, Any]:
    """Refine approximate crosshair locations without needing a plate boundary."""
    image_error = _image_validation_error(image)
    if image_error is not None:
        return {"detected": False, "reason": image_error, "points": []}
    if not expected_points:
        return {
            "detected": False,
            "reason": "Need at least one expected crosshair location",
            "points": [],
        }
    if type(search_radius_px) is not int or search_radius_px < 1:
        return {
            "detected": False,
            "reason": "Crosshair search radius must be a positive integer",
            "points": [],
        }
    try:
        identifiers = [item["id"] for item in expected_points]
        coordinates = np.asarray(
            [
                (
                    item["image_x"],
                    item["image_y"],
                    item["machine_x"],
                    item["machine_y"],
                )
                for item in expected_points
            ],
            dtype=np.float64,
        )
    except (KeyError, TypeError, ValueError):
        return {
            "detected": False,
            "reason": "Expected crosshair metadata is invalid",
            "points": [],
        }
    if (
        any(type(identifier) is not int for identifier in identifiers)
        or len(set(identifiers)) != len(identifiers)
        or coordinates.shape != (len(expected_points), 4)
        or not np.isfinite(coordinates).all()
    ):
        return {
            "detected": False,
            "reason": "Expected crosshairs must be finite and uniquely numbered",
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
    coordinate_strategy: str = "median",
    consensus_frames: int = 15,
    frame_quality_scores: tuple[float, ...] | list[float] | None = None,
) -> dict[str, Any]:
    """Detect every frame, then robustly aggregate each crosshair center."""

    if not images:
        return {
            "detected": False,
            "reason": "Precision capture contains no frames",
            "points": [],
        }
    if type(search_radius_px) is not int or search_radius_px < 1:
        raise ValueError("Crosshair search radius must be a positive integer")
    if type(minimum_valid_frames) is not int or minimum_valid_frames < 1:
        raise ValueError("Minimum valid frame count must be a positive integer")
    if minimum_valid_frames > len(images):
        raise ValueError("Minimum valid frame count cannot exceed the precision frame count")
    if coordinate_strategy not in {"median", "sharpest_inlier_frame", "stable_clarity_consensus"}:
        raise ValueError(f"Unsupported coordinate strategy: {coordinate_strategy}")
    if type(consensus_frames) is not int or consensus_frames < 1:
        raise ValueError("Consensus frame count must be a positive integer")
    if coordinate_strategy == "stable_clarity_consensus" and consensus_frames > len(images):
        raise ValueError("Consensus frame count cannot exceed the precision frame count")
    numeric_parameters = (
        ("MAD multiplier", mad_multiplier, False),
        ("outlier floor", outlier_floor_px, True),
        ("jitter limit", max_jitter_rms_px, False),
    )
    for label, raw_value, allow_zero in numeric_parameters:
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be finite") from exc
        if (
            type(raw_value) is bool
            or not math.isfinite(value)
            or value < 0
            or (not allow_zero and value == 0)
        ):
            qualifier = "non-negative" if allow_zero else "positive"
            raise ValueError(f"{label} must be finite and {qualifier}")
    if frame_quality_scores is not None and len(frame_quality_scores) != len(images):
        raise ValueError("Frame quality scores must match the precision frame count")
    if frame_quality_scores is not None:
        try:
            quality_values = np.asarray(frame_quality_scores, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError("Frame quality scores must be numeric") from exc
        if not np.isfinite(quality_values).all():
            raise ValueError("Frame quality scores must be finite")
    identifiers: list[int] = []
    for target in expected_points:
        if not isinstance(target, dict) or type(target.get("id")) is not int:
            raise ValueError("Expected crosshair identities must be JSON integers")
        try:
            coordinates = (
                float(target["image_x"]),
                float(target["image_y"]),
                float(target["machine_x"]),
                float(target["machine_y"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Expected crosshair coordinates are invalid") from exc
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("Expected crosshair coordinates must be finite")
        identifiers.append(target["id"])
    if not identifiers:
        return {
            "detected": False,
            "reason": "Need at least one expected crosshair location",
            "points": [],
        }
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Expected crosshair identities must be unique")
    for image in images:
        if (
            not isinstance(image, np.ndarray)
            or image.size == 0
            or image.dtype != np.uint8
            or image.ndim not in {2, 3}
            or (image.ndim == 3 and image.shape[2] != 3)
        ):
            raise ValueError("Precision frames must be uint8 grayscale or BGR images")
    required = minimum_valid_frames
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

    samples_by_id: dict[int, list[dict[str, Any]]] = {int(item["id"]): [] for item in expected_points}
    confidence_counts: dict[str, int] = {}
    for frame_index, detection in enumerate(detections):
        confidence = str(detection.get("confidence", "unknown"))
        confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
        if not detection.get("detected"):
            continue
        for point in detection.get("points", []):
            identifier = int(point["id"])
            if identifier in samples_by_id:
                samples_by_id[identifier].append({**point, "frame_index": int(frame_index)})

    aggregated: list[dict[str, Any]] = []
    rejected_frames: set[int] = set()
    inlier_samples_by_mark: dict[int, dict[int, dict[str, Any]]] = {}
    unstable: list[str] = []
    for target in expected_points:
        identifier = int(target["id"])
        samples = samples_by_id[identifier]
        if len(samples) < required:
            unstable.append(f"mark {identifier} has {len(samples)} valid frames; {required} required")
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
            unstable.append(f"mark {identifier} retained {inlier_count} stable frames; {required} required")
            continue

        inlier_coordinates = coordinates[inlier_mask]
        center = np.median(inlier_coordinates, axis=0)
        inlier_distances = np.linalg.norm(inlier_coordinates - center, axis=1)
        jitter_rms = float(np.sqrt(np.mean(np.square(inlier_distances))))
        jitter_max = float(np.max(inlier_distances))
        if jitter_rms > float(max_jitter_rms_px):
            unstable.append(
                f"mark {identifier} jitter is {jitter_rms:.3f} px (limit {float(max_jitter_rms_px):.3f} px)"
            )

        inlier_samples = [item for item, is_inlier in zip(samples, inlier_mask, strict=True) if bool(is_inlier)]
        inlier_samples_by_mark[identifier] = {int(item["frame_index"]): item for item in inlier_samples}
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
                "score": float(np.median([float(item["score"]) for item in inlier_samples])),
                "seed_x": expected_x,
                "seed_y": expected_y,
                "shift_px": float(np.hypot(center[0] - expected_x, center[1] - expected_y)),
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
        "worst_jitter_rms_px": (max((float(item["jitter_rms_px"]) for item in aggregated), default=0.0)),
        "max_jitter_rms_px": float(max_jitter_rms_px),
        "coordinate_strategy": coordinate_strategy,
    }
    if unstable or len(aggregated) != len(expected_points):
        return {
            "detected": False,
            "reason": "Precision capture was unstable: " + "; ".join(unstable),
            "points": aggregated,
            "capture_diagnostics": diagnostics,
        }

    if coordinate_strategy in {"sharpest_inlier_frame", "stable_clarity_consensus"}:
        common_inlier_frames = set.intersection(*(set(samples) for samples in inlier_samples_by_mark.values()))
        if not common_inlier_frames:
            return {
                "detected": False,
                "reason": ("Precision capture was unstable: no single frame survived outlier screening for every mark"),
                "points": aggregated,
                "capture_diagnostics": diagnostics,
            }
        qualities = frame_quality_scores or [0.0] * len(images)
        ranked_frames = sorted(
            common_inlier_frames,
            key=lambda index: (-float(qualities[index]), index),
        )
        if coordinate_strategy == "stable_clarity_consensus" and len(ranked_frames) < int(consensus_frames):
            diagnostics["eligible_frame_count"] = len(ranked_frames)
            diagnostics["required_consensus_frames"] = int(consensus_frames)
            return {
                "detected": False,
                "reason": (
                    "Precision capture was unstable: only "
                    f"{len(ranked_frames)} frames survived for every mark; "
                    f"{int(consensus_frames)} are required for the configured consensus"
                ),
                "points": aggregated,
                "capture_diagnostics": diagnostics,
            }
        selected_frame_indices = ranked_frames[: min(int(consensus_frames), len(ranked_frames))]
        selected_frame_index = selected_frame_indices[0]
        for point in aggregated:
            samples = [inlier_samples_by_mark[int(point["id"])][index] for index in selected_frame_indices]
            if coordinate_strategy == "sharpest_inlier_frame":
                samples = samples[:1]
            coordinates = np.asarray(
                [[sample["image_x"], sample["image_y"]] for sample in samples],
                dtype=np.float64,
            )
            center = np.median(coordinates, axis=0)
            point["image_x"] = float(center[0])
            point["image_y"] = float(center[1])
            point["score"] = float(np.median([float(sample["score"]) for sample in samples]))
            point["shift_px"] = float(np.hypot(center[0] - point["seed_x"], center[1] - point["seed_y"]))
            point["selected_frame_index"] = int(selected_frame_index)
            point["consensus_frame_count"] = len(samples)
        diagnostics.update(
            {
                "eligible_frame_count": len(common_inlier_frames),
                "selected_frame_index": int(selected_frame_index),
                "selected_frame_quality": float(qualities[selected_frame_index]),
                "selected_frame_indices": [int(index) for index in selected_frame_indices],
                "consensus_frame_count": (
                    1 if coordinate_strategy == "sharpest_inlier_frame" else len(selected_frame_indices)
                ),
            }
        )

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
        "orientation": (
            "Stable clarity-ranked consensus after median/MAD screening"
            if coordinate_strategy == "stable_clarity_consensus"
            else "Sharpest all-mark inlier frame after median/MAD screening"
            if coordinate_strategy == "sharpest_inlier_frame"
            else "Median/MAD aggregate of fresh precision frames"
        ),
        "maximum_seed_shift_px": maximum_shift,
        "capture_diagnostics": diagnostics,
    }
