from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from typing import Any

import cv2
import numpy as np

from ..config import WorkArea

TRACE_MODES = {"auto", "color", "contrast"}
OUTPUT_MODES = {"rounded", "smoothed", "exact"}


@dataclass(slots=True)
class TraceOptions:
    detection_mode: str = "auto"
    target_hue: float | None = None
    target_bgr: tuple[int, int, int] | list[int] | None = None
    hue_tolerance: float = 14.0
    min_saturation: int = 45
    min_area_mm2: float = 30.0
    max_area_mm2: float = 20_000.0
    min_width_mm: float = 4.0
    min_height_mm: float = 3.0
    confidence_threshold: float = 0.55
    regular_grid: bool = True
    infer_missing: bool = True
    normalize_grid: bool = True
    output_mode: str = "rounded"
    border_offset_mm: float = 0.0
    smoothing_mm: float = 0.25

    def __post_init__(self) -> None:
        self.detection_mode = str(self.detection_mode).lower()
        if self.detection_mode not in TRACE_MODES:
            raise ValueError(f"Unknown detection mode: {self.detection_mode}")
        self.target_hue = (
            None if self.target_hue is None else float(self.target_hue) % 180.0
        )
        if self.target_bgr is not None:
            if len(self.target_bgr) != 3:
                raise ValueError("target_bgr must contain exactly three channels")
            self.target_bgr = tuple(
                max(0, min(255, int(value))) for value in self.target_bgr
            )
        self.hue_tolerance = max(1.0, min(90.0, float(self.hue_tolerance)))
        self.min_saturation = max(0, min(255, int(self.min_saturation)))
        self.min_area_mm2 = max(0.01, float(self.min_area_mm2))
        self.max_area_mm2 = max(self.min_area_mm2, float(self.max_area_mm2))
        self.min_width_mm = max(0.1, float(self.min_width_mm))
        self.min_height_mm = max(0.1, float(self.min_height_mm))
        self.confidence_threshold = max(
            0.0, min(1.0, float(self.confidence_threshold))
        )
        self.regular_grid = bool(self.regular_grid)
        self.infer_missing = bool(self.infer_missing)
        self.normalize_grid = bool(self.normalize_grid)
        self.output_mode = str(self.output_mode).lower()
        if self.output_mode not in OUTPUT_MODES:
            raise ValueError(f"Unknown output mode: {self.output_mode}")
        self.border_offset_mm = max(
            -25.0, min(25.0, float(self.border_offset_mm))
        )
        self.smoothing_mm = max(0.0, min(10.0, float(self.smoothing_mm)))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> TraceOptions:
        raw = dict(raw or {})
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TraceDetection:
    id: str
    index: int
    source: str
    confidence: float
    score: float
    shape: str
    center_mm: tuple[float, float]
    width_mm: float
    height_mm: float
    rotation_deg: float
    corner_radius_mm: float
    area_mm2: float
    contour_mm: list[list[float]]
    vector_contour_mm: list[list[float]]
    box_mm: list[list[float]]
    selected_default: bool
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["center_mm"] = list(self.center_mm)
        return payload


@dataclass(slots=True)
class TraceResult:
    detected: bool
    detections: list[TraceDetection]
    mode_used: str
    target_hue: float | None
    image_width: int
    image_height: int
    direct_count: int
    inferred_count: int
    grid: dict[str, Any] | None
    message: str
    options: TraceOptions

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "detections": [item.to_dict() for item in self.detections],
            "mode_used": self.mode_used,
            "target_hue": self.target_hue,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "direct_count": self.direct_count,
            "inferred_count": self.inferred_count,
            "grid": self.grid,
            "message": self.message,
            "options": self.options.to_dict(),
        }


def _new_id() -> str:
    return f"trace-{uuid.uuid4().hex}"


def _hue_distance(hue: np.ndarray, target: float) -> np.ndarray:
    delta = np.abs(hue.astype(np.float32) - float(target))
    return np.minimum(delta, 180.0 - delta)


def auto_target_hue(image: np.ndarray, min_saturation: int = 45) -> float | None:
    """Find the dominant chromatic hue while discounting neutral backgrounds."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1].astype(np.float32)
    a = lab[:, :, 1].astype(np.float32) - 128.0
    b = lab[:, :, 2].astype(np.float32) - 128.0
    chroma = np.hypot(a, b)
    weight = np.clip(
        (saturation - max(15.0, min_saturation * 0.55)) / 180.0,
        0.0,
        1.0,
    ) * np.clip((chroma - 5.0) / 70.0, 0.0, 1.0)
    if float(weight.sum()) < image.shape[0] * image.shape[1] * 0.005:
        return None
    histogram = np.bincount(
        hue.reshape(-1), weights=weight.reshape(-1), minlength=180
    ).astype(np.float64)
    radius = 5
    padded = np.concatenate((histogram[-radius:], histogram, histogram[:radius]))
    kernel = np.hanning(radius * 2 + 1)
    kernel /= max(float(kernel.sum()), 1e-12)
    smoothed = np.convolve(padded, kernel, mode="same")[radius:-radius]
    return float(np.argmax(smoothed))


def sample_color(
    image: np.ndarray,
    pixel_x: float,
    pixel_y: float,
    radius_px: int = 5,
) -> dict[str, Any]:
    if image is None or image.size == 0:
        raise ValueError("No image is available for color sampling")
    height, width = image.shape[:2]
    x = int(round(float(pixel_x)))
    y = int(round(float(pixel_y)))
    radius = max(1, int(radius_px))
    x0, x1 = max(0, x - radius), min(width, x + radius + 1)
    y0, y1 = max(0, y - radius), min(height, y + radius + 1)
    if x0 >= x1 or y0 >= y1:
        raise ValueError("Color sample lies outside the image")
    patch = image[y0:y1, x0:x1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    bgr = patch.reshape(-1, 3)
    median_bgr = np.median(bgr, axis=0)
    return {
        "hue": float(np.median(hsv[:, 0])),
        "saturation": float(np.median(hsv[:, 1])),
        "value": float(np.median(hsv[:, 2])),
        "bgr": [int(round(value)) for value in median_bgr],
        "rgb": [
            int(round(median_bgr[2])),
            int(round(median_bgr[1])),
            int(round(median_bgr[0])),
        ],
    }


def _color_mask(
    image: np.ndarray,
    target_hue: float,
    options: TraceOptions,
    pixels_per_mm: float,
) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    a = lab[:, :, 1].astype(np.float32) - 128.0
    b = lab[:, :, 2].astype(np.float32) - 128.0
    chroma = np.hypot(a, b)
    hue_mask = (
        (_hue_distance(hsv[:, :, 0], target_hue) <= options.hue_tolerance)
        & (hsv[:, :, 1] >= options.min_saturation)
        & (hsv[:, :, 2] >= 20)
        & (chroma >= 7.0)
    )
    if options.target_bgr is not None:
        target = np.asarray(options.target_bgr, dtype=np.uint8).reshape(1, 1, 3)
        target_lab = cv2.cvtColor(target, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)
        lab_float = lab.astype(np.float32)
        delta = lab_float - target_lab
        # Lightness varies across a real bed much more than chroma. Weight it
        # less heavily so one sampled neutral or colored surface remains
        # selectable under uneven illumination while still separating it from
        # the warmer wood backing.
        distance = np.sqrt(
            0.16 * delta[:, :, 0] ** 2
            + delta[:, :, 1] ** 2
            + delta[:, :, 2] ** 2
        )
        lab_tolerance = max(10.0, options.hue_tolerance)
        mask_bool = (distance <= lab_tolerance) & (hsv[:, :, 2] >= 12)
    else:
        mask_bool = hue_mask
    mask = mask_bool.astype(np.uint8) * 255
    radius = max(1, int(round(max(1.0, pixels_per_mm * 0.45))))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)


def _contrast_mask(image: np.ndarray, pixels_per_mm: float) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = max(15, int(round(pixels_per_mm * 8.0)))
    if scale % 2 == 0:
        scale += 1
    background = cv2.GaussianBlur(gray, (scale, scale), 0)
    contrast = cv2.absdiff(gray, background)
    threshold = max(8.0, float(np.percentile(contrast, 84)))
    _, mask = cv2.threshold(contrast, threshold, 255, cv2.THRESH_BINARY)
    radius = max(1, int(round(max(1.0, pixels_per_mm * 0.75))))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=3)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)


def _contrast_region_masks(
    image: np.ndarray,
    pixels_per_mm: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Segment darker and lighter regions without expanding their edges.

    The local absolute-contrast mask is useful for small artwork, but a filled
    object becomes a thick edge band whose external contour lies several
    millimetres outside the object. A much larger background estimate and a
    signed difference preserve the filled silhouette instead. Both masks are
    retained because real targets may be either darker or lighter than the bed.
    """

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    scale = max(31, int(round(pixels_per_mm * 36.0)))
    if scale % 2 == 0:
        scale += 1
    background = cv2.GaussianBlur(gray, (scale, scale), 0)
    radius = max(1, int(round(max(1.0, pixels_per_mm * 0.5))))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    masks: list[np.ndarray] = []
    for difference in (background - gray, gray - background):
        positive = difference[difference > 0]
        adaptive = 0.0 if positive.size == 0 else float(np.percentile(positive, 78))
        threshold = max(6.0, adaptive)
        mask = (difference >= threshold).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        masks.append(cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1))
    return masks[0], masks[1]


def _long_axis_rect(contour: np.ndarray) -> dict[str, Any] | None:
    rect = cv2.minAreaRect(contour.astype(np.float32))
    box = cv2.boxPoints(rect).astype(np.float64)
    vectors = np.roll(box, -1, axis=0) - box
    lengths = np.linalg.norm(vectors, axis=1)
    if float(lengths.min()) <= 1e-6:
        return None
    index = int(np.argmax(lengths))
    width = float(lengths[index])
    height = float(lengths[(index + 1) % 4])
    if height > width:
        width, height = height, width
        index = (index + 1) % 4
    vector = vectors[index] / max(float(lengths[index]), 1e-12)
    angle = math.degrees(math.atan2(float(vector[1]), float(vector[0])))
    while angle >= 90.0:
        angle -= 180.0
    while angle < -90.0:
        angle += 180.0
    return {
        "center": np.asarray(rect[0], dtype=np.float64),
        "width": width,
        "height": height,
        "angle_image_deg": angle,
        "box": box,
    }


def _rounded_mask(width: int, height: int, radius: int) -> np.ndarray:
    width, height = max(2, int(width)), max(2, int(height))
    radius = max(0, min(int(radius), width // 2, height // 2))
    mask = np.zeros((height, width), dtype=np.uint8)
    if radius == 0:
        mask[:] = 255
        return mask
    cv2.rectangle(mask, (radius, 0), (width - radius - 1, height - 1), 255, -1)
    cv2.rectangle(mask, (0, radius), (width - 1, height - radius - 1), 255, -1)
    for center in (
        (radius, radius),
        (width - radius - 1, radius),
        (width - radius - 1, height - radius - 1),
        (radius, height - radius - 1),
    ):
        cv2.circle(mask, center, radius, 255, -1)
    return mask


def _rounded_fit(contour: np.ndarray, rectangle: Mapping[str, Any]) -> tuple[float, float]:
    # minAreaRect measures the center-to-center span of the outermost raster
    # samples. A run from pixel 0 through pixel N-1 therefore reports N-1, not
    # N. Reconstruct the discrete extent before comparing candidate masks.
    width = max(4, int(round(float(rectangle["width"]))) + 1)
    height = max(4, int(round(float(rectangle["height"]))) + 1)
    center = np.asarray(rectangle["center"], dtype=np.float64)
    angle = math.radians(float(rectangle["angle_image_deg"]))
    u = np.array([math.cos(angle), math.sin(angle)], dtype=np.float64)
    v = np.array([-u[1], u[0]], dtype=np.float64)
    local = contour.reshape(-1, 2).astype(np.float64) - center
    local = np.column_stack((local @ u, local @ v))
    margin = 4
    local[:, 0] += (width - 1) / 2.0 + margin
    local[:, 1] += (height - 1) / 2.0 + margin
    observed = np.zeros((height + 2 * margin, width + 2 * margin), dtype=np.uint8)
    cv2.fillPoly(observed, [np.round(local).astype(np.int32)], 255)
    best_radius, best_iou = 0.0, 0.0
    maximum = int(round(min(width, height) * 0.50))
    coarse_step = max(1, int(math.ceil(maximum / 24.0)))
    coarse_radii = list(range(0, maximum + 1, coarse_step))
    if not coarse_radii or coarse_radii[-1] != maximum:
        coarse_radii.append(maximum)

    def evaluate(radius: int) -> float:
        candidate = np.zeros_like(observed)
        candidate[margin:margin + height, margin:margin + width] = _rounded_mask(
            width, height, radius
        )
        intersection = np.count_nonzero((candidate > 0) & (observed > 0))
        union = np.count_nonzero((candidate > 0) | (observed > 0))
        return 0.0 if union == 0 else float(intersection / union)

    for radius in coarse_radii:
        iou = evaluate(radius)
        if iou > best_iou:
            best_radius, best_iou = float(radius), iou

    fine_start = max(0, int(best_radius) - coarse_step)
    fine_end = min(maximum, int(best_radius) + coarse_step)
    for radius in range(fine_start, fine_end + 1):
        iou = evaluate(radius)
        if iou > best_iou:
            best_radius, best_iou = float(radius), iou
    return best_radius, best_iou


def _offset_contour(
    contour: np.ndarray,
    offset_px: float,
    smoothing_px: float,
) -> np.ndarray:
    contour = contour.reshape(-1, 1, 2).astype(np.int32)
    x, y, width, height = cv2.boundingRect(contour)
    margin = max(8, int(math.ceil(abs(offset_px))) + 5)
    local = contour.copy()
    local[:, 0, 0] -= x - margin
    local[:, 0, 1] -= y - margin
    mask = np.zeros((height + 2 * margin, width + 2 * margin), dtype=np.uint8)
    cv2.fillPoly(mask, [local], 255)
    radius = int(round(abs(offset_px)))
    if radius:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
        )
        mask = (cv2.dilate if offset_px > 0 else cv2.erode)(mask, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return contour.reshape(-1, 2).astype(np.float64)
    output = max(contours, key=cv2.contourArea)
    if smoothing_px > 0:
        output = cv2.approxPolyDP(output, smoothing_px, True)
    points = output.reshape(-1, 2).astype(np.float64)
    points[:, 0] += x - margin
    points[:, 1] += y - margin
    return points


def _pixel_to_machine(
    points: np.ndarray,
    work_area: WorkArea,
    pixels_per_mm: float,
) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    output = np.empty_like(points)
    output[:, 0] = work_area.x_min + points[:, 0] / pixels_per_mm
    output[:, 1] = work_area.y_max - points[:, 1] / pixels_per_mm
    return output


def _machine_geometry(
    rectangle: Mapping[str, Any],
    work_area: WorkArea,
    pixels_per_mm: float,
    offset_mm: float,
) -> dict[str, Any]:
    center = _pixel_to_machine(
        np.asarray(rectangle["center"]).reshape(1, 2), work_area, pixels_per_mm
    )[0]
    width = max(0.01, float(rectangle["width"]) / pixels_per_mm + 2 * offset_mm)
    height = max(0.01, float(rectangle["height"]) / pixels_per_mm + 2 * offset_mm)
    rotation = -float(rectangle["angle_image_deg"])
    radius = max(
        0.0,
        float(rectangle.get("radius_px", 0.0)) / pixels_per_mm + offset_mm,
    )
    radius = min(radius, width / 2.0, height / 2.0)
    angle = math.radians(rotation)
    u = np.array([math.cos(angle), math.sin(angle)])
    v = np.array([-u[1], u[0]])
    box = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        point = center + u * sx * width / 2 + v * sy * height / 2
        box.append([float(point[0]), float(point[1])])
    return {
        "center_mm": (float(center[0]), float(center[1])),
        "width_mm": width,
        "height_mm": height,
        "rotation_deg": rotation,
        "corner_radius_mm": radius,
        "box_mm": box,
    }


def _candidate(
    contour: np.ndarray,
    mask: np.ndarray,
    options: TraceOptions,
    work_area: WorkArea,
    pixels_per_mm: float,
) -> dict[str, Any] | None:
    area_px = float(cv2.contourArea(contour))
    area_mm2 = area_px / pixels_per_mm**2
    if not options.min_area_mm2 <= area_mm2 <= options.max_area_mm2:
        return None
    rectangle = _long_axis_rect(contour)
    if rectangle is None:
        return None
    if (
        rectangle["width"] / pixels_per_mm < options.min_width_mm
        or rectangle["height"] / pixels_per_mm < options.min_height_mm
    ):
        return None
    rect_area = max(1.0, rectangle["width"] * rectangle["height"])
    rectangularity = min(1.0, area_px / rect_area)
    hull_area = max(1.0, float(cv2.contourArea(cv2.convexHull(contour))))
    solidity = min(1.0, area_px / hull_area)
    radius_px, fit_iou = _rounded_fit(contour, rectangle)
    rectangle["radius_px"] = radius_px
    region = np.zeros_like(mask)
    cv2.drawContours(region, [contour], -1, 255, -1)
    pixels = region > 0
    coverage = 0.0 if not np.any(pixels) else float(np.mean(mask[pixels] > 0))
    perimeter = max(1.0, float(cv2.arcLength(contour, True)))
    compactness = max(0.0, min(1.0, 4.0 * math.pi * area_px / (perimeter * perimeter)))
    rounded = rectangularity >= 0.78 and solidity >= 0.82 and fit_iou >= 0.82
    if rounded:
        shape = "rounded_rectangle"
        score = (
            0.48 * fit_iou
            + 0.18 * rectangularity
            + 0.14 * solidity
            + 0.20 * coverage
        )
        confidence = max(0.0, min(1.0, (score - 0.35) / 0.55))
    else:
        # Non-grid tracing may preserve arbitrary colored silhouettes. A regular
        # label grid remains intentionally strict so scratches and merged parts
        # cannot seed false inferred cells.
        if options.regular_grid or solidity < 0.42 or coverage < 0.42:
            return None
        shape = "contour"
        score = 0.42 * solidity + 0.33 * coverage + 0.25 * compactness
        confidence = max(0.0, min(1.0, (score - 0.30) / 0.55))
    geometry = _machine_geometry(
        rectangle, work_area, pixels_per_mm, options.border_offset_mm
    )
    points = _offset_contour(
        contour,
        options.border_offset_mm * pixels_per_mm,
        0.0 if options.output_mode == "exact" else options.smoothing_mm * pixels_per_mm,
    )
    contour_mm = _pixel_to_machine(points, work_area, pixels_per_mm)
    observed_contour_mm = [[float(x), float(y)] for x, y in contour_mm]
    vector_contour_mm = (
        _rounded_polyline(
            geometry["center_mm"],
            geometry["width_mm"],
            geometry["height_mm"],
            geometry["rotation_deg"],
            geometry["corner_radius_mm"],
        )
        if options.output_mode == "rounded" and shape == "rounded_rectangle"
        else observed_contour_mm
    )
    return {
        "center_px": np.asarray(rectangle["center"], dtype=np.float64),
        "width_px": float(rectangle["width"]),
        "height_px": float(rectangle["height"]),
        "angle_image_deg": float(rectangle["angle_image_deg"]),
        "radius_px": radius_px,
        "area_mm2": area_mm2,
        "score": score,
        "confidence": confidence,
        "rectangularity": rectangularity,
        "solidity": solidity,
        "fit_iou": fit_iou,
        "coverage": coverage,
        "compactness": compactness,
        "shape": shape,
        "contour_mm": observed_contour_mm,
        "vector_contour_mm": vector_contour_mm,
        **geometry,
    }


def _mean_angle(angles: Sequence[float]) -> float:
    radians = np.radians(np.asarray(angles, dtype=float) * 2.0)
    return math.degrees(
        math.atan2(float(np.sin(radians).mean()), float(np.cos(radians).mean()))
        / 2.0
    )


def _clusters(values: Sequence[float], tolerance: float) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(float(item) for item in values):
        if not groups or abs(value - float(np.mean(groups[-1]))) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [float(np.mean(group)) for group in groups]


def _rounded_polyline(
    center: tuple[float, float],
    width: float,
    height: float,
    rotation: float,
    radius: float,
    segments_per_corner: int = 8,
) -> list[list[float]]:
    """Sample the fitted rounded rectangle used by rounded-vector output."""

    radius = max(0.0, min(radius, width / 2, height / 2))
    segments = max(1, int(segments_per_corner))
    local = []
    for cx, cy, start in (
        (width / 2 - radius, height / 2 - radius, 0),
        (-width / 2 + radius, height / 2 - radius, 90),
        (-width / 2 + radius, -height / 2 + radius, 180),
        (width / 2 - radius, -height / 2 + radius, 270),
    ):
        for step in range(segments + 1):
            angle = math.radians(start + 90 * step / segments)
            local.append([cx + radius * math.cos(angle), cy + radius * math.sin(angle)])
    points = np.asarray(local)
    angle = math.radians(rotation)
    matrix = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    world = points @ matrix.T + np.asarray(center)
    return [[float(x), float(y)] for x, y in world]


def _infer_grid(
    candidates: list[dict[str, Any]],
    options: TraceOptions,
    work_area: WorkArea,
    pixels_per_mm: float,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
]:
    if not options.regular_grid or len(candidates) < 4:
        return candidates, [], None

    def angle_distance(first: float, second: float) -> float:
        difference = abs(float(first) - float(second)) % 180.0
        return min(difference, 180.0 - difference)

    # Select the largest mutually similar shape family instead of taking a
    # global median that can be pulled toward sheet edges, shadows, or merged
    # components. A damaged member can still be recognized as a cell later,
    # but unrelated geometry must not define the grid's canonical shape.
    shape_families: list[list[dict[str, Any]]] = []
    for anchor in candidates:
        family = [
            item
            for item in candidates
            if 0.68 <= float(item["width_px"]) / float(anchor["width_px"]) <= 1.47
            and 0.68
            <= float(item["height_px"]) / float(anchor["height_px"])
            <= 1.47
            and angle_distance(
                float(item["angle_image_deg"]),
                float(anchor["angle_image_deg"]),
            )
            <= 12.0
        ]
        shape_families.append(family)
    filtered = max(
        shape_families,
        key=lambda family: (
            len(family),
            sum(float(item["score"]) for item in family),
        ),
    )
    if len(filtered) < 4:
        return candidates, [], None

    widths = np.asarray([item["width_px"] for item in filtered])
    heights = np.asarray([item["height_px"] for item in filtered])
    median_width = float(np.median(widths))
    median_height = float(np.median(heights))
    filtered = [
        item
        for item in filtered
        if 0.72 <= float(item["width_px"]) / median_width <= 1.38
        and 0.72 <= float(item["height_px"]) / median_height <= 1.38
    ]
    if len(filtered) < 4:
        return candidates, [], None
    median_width = float(np.median([item["width_px"] for item in filtered]))
    median_height = float(np.median([item["height_px"] for item in filtered]))
    median_radius = float(np.median([item["radius_px"] for item in filtered]))
    common_angle = _mean_angle([item["angle_image_deg"] for item in filtered])
    angle = math.radians(common_angle)
    u = np.array([math.cos(angle), math.sin(angle)])
    v = np.array([-u[1], u[0]])
    centers = np.asarray([item["center_px"] for item in filtered])
    x_values, y_values = centers @ u, centers @ v
    columns = _clusters(x_values, max(4.0, median_width * 0.45))
    rows = _clusters(y_values, max(4.0, median_height * 0.45))
    if len(columns) < 2 or len(rows) < 2 or len(columns) * len(rows) > 100:
        return candidates, [], None

    def regular_axis(
        values: Sequence[float], object_extent: float
    ) -> tuple[list[float], float, float]:
        observed = np.asarray(sorted(float(item) for item in values), dtype=np.float64)
        differences = np.diff(observed)
        if not len(differences) or float(np.min(differences)) <= 1e-6:
            return [], 0.0, 0.0
        seed = float(np.percentile(differences, 25))
        if seed <= object_extent * 0.82:
            return [], 0.0, 0.0
        steps = np.clip(np.rint(differences / seed), 1, 5).astype(int)
        indices = np.concatenate(([0], np.cumsum(steps))).astype(np.float64)
        centered_indices = indices - float(np.mean(indices))
        centered_values = observed - float(np.mean(observed))
        denominator = float(np.dot(centered_indices, centered_indices))
        if denominator <= 1e-9:
            return [], 0.0, 0.0
        spacing = float(np.dot(centered_indices, centered_values) / denominator)
        if spacing <= object_extent * 0.82:
            return [], 0.0, 0.0
        origin = float(np.mean(observed - indices * spacing))
        predicted = origin + indices * spacing
        residual_rms = float(
            np.sqrt(np.mean(np.square(observed - predicted)))
        )
        quality = max(0.0, 1.0 - residual_rms / max(spacing * 0.14, 1.0))
        expanded = [
            float(origin + index * spacing)
            for index in range(int(indices[-1]) + 1)
        ]
        return expanded, quality, spacing

    columns, x_quality, x_spacing = regular_axis(columns, median_width)
    rows, y_quality, y_spacing = regular_axis(rows, median_height)
    if len(columns) < 2 or len(rows) < 2 or len(columns) * len(rows) > 100:
        return candidates, [], None

    cell_members: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}
    for item in filtered:
        point = np.asarray(item["center_px"])
        x_value, y_value = float(point @ u), float(point @ v)
        column = int(np.argmin(np.abs(np.asarray(columns) - x_value)))
        row = int(np.argmin(np.abs(np.asarray(rows) - y_value)))
        distance = math.hypot(
            (x_value - columns[column]) / max(x_spacing, median_width),
            (y_value - rows[row]) / max(y_spacing, median_height),
        )
        if distance > 0.38:
            continue
        cell = (row, column)
        rank = float(item["score"]) - distance * 0.25
        previous = cell_members.get(cell)
        if previous is None or rank > previous[0]:
            cell_members[cell] = (rank, item)

    occupancy = len(cell_members) / (len(columns) * len(rows))
    grid_quality = min(x_quality, y_quality) * min(1.0, occupancy / 0.55)
    if occupancy < 0.45 or grid_quality < 0.45:
        return candidates, [], None

    normalize = bool(options.normalize_grid and options.output_mode == "rounded")

    def canonical_candidate(
        item: Mapping[str, Any] | None,
        row: int,
        column: int,
        *,
        inferred: bool,
    ) -> dict[str, Any] | None:
        center_px = u * columns[column] + v * rows[row]
        rectangle = {
            "center": center_px,
            "width": median_width,
            "height": median_height,
            "angle_image_deg": common_angle,
            "radius_px": median_radius,
        }
        geometry = _machine_geometry(
            rectangle, work_area, pixels_per_mm, options.border_offset_mm
        )
        if not all(
            work_area.contains(float(x), float(y)) for x, y in geometry["box_mm"]
        ):
            return None
        rounded_contour = _rounded_polyline(
            geometry["center_mm"],
            geometry["width_mm"],
            geometry["height_mm"],
            geometry["rotation_deg"],
            geometry["corner_radius_mm"],
        )
        if inferred:
            median_score = float(
                np.median([value[1]["score"] for value in cell_members.values()])
            )
            return {
                "center_px": center_px,
                "width_px": median_width,
                "height_px": median_height,
                "angle_image_deg": common_angle,
                "radius_px": median_radius,
                "area_mm2": median_width * median_height / pixels_per_mm**2,
                "score": median_score * grid_quality * 0.72,
                "confidence": min(0.68, grid_quality * 0.66),
                "rectangularity": 1.0,
                "solidity": 1.0,
                "fit_iou": 1.0,
                "coverage": 0.0,
                "compactness": 1.0,
                "shape": "rounded_rectangle",
                "contour_mm": rounded_contour,
                "vector_contour_mm": rounded_contour,
                "grid_row": row,
                "grid_column": column,
                "grid_normalized": True,
                **geometry,
            }

        assert item is not None
        output = dict(item)
        output.update(
            {
                "grid_row": row,
                "grid_column": column,
                "grid_normalized": normalize,
                "observed_center_mm": list(item["center_mm"]),
                "observed_width_mm": float(item["width_mm"]),
                "observed_height_mm": float(item["height_mm"]),
                "observed_rotation_deg": float(item["rotation_deg"]),
                "observed_corner_radius_mm": float(item["corner_radius_mm"]),
            }
        )
        if normalize:
            output.update(
                {
                    "center_px": center_px,
                    "width_px": median_width,
                    "height_px": median_height,
                    "angle_image_deg": common_angle,
                    "radius_px": median_radius,
                    "area_mm2": median_width * median_height / pixels_per_mm**2,
                    "shape": "rounded_rectangle",
                    "vector_contour_mm": rounded_contour,
                    **geometry,
                }
            )
        return output

    direct = []
    for (row, column), (_, item) in sorted(cell_members.items()):
        candidate = canonical_candidate(item, row, column, inferred=False)
        if candidate is not None:
            direct.append(candidate)

    inferred = []
    if options.infer_missing:
        for row in range(len(rows)):
            for column in range(len(columns)):
                if (row, column) in cell_members:
                    continue
                candidate = canonical_candidate(None, row, column, inferred=True)
                if candidate is not None:
                    inferred.append(candidate)

    canonical_geometry = _machine_geometry(
        {
            "center": u * columns[0] + v * rows[0],
            "width": median_width,
            "height": median_height,
            "angle_image_deg": common_angle,
            "radius_px": median_radius,
        },
        work_area,
        pixels_per_mm,
        options.border_offset_mm,
    )
    return direct, inferred, {
        "rows": len(rows),
        "columns": len(columns),
        "occupancy": occupancy,
        "quality": grid_quality,
        "rotation_deg": -common_angle,
        "direct_cells": len(direct),
        "missing_cells": len(inferred),
        "rejected_candidates": len(candidates) - len(direct),
        "normalized": normalize,
        "cell_width_mm": float(canonical_geometry["width_mm"]),
        "cell_height_mm": float(canonical_geometry["height_mm"]),
        "cell_corner_radius_mm": float(canonical_geometry["corner_radius_mm"]),
        "column_pitch_mm": x_spacing / pixels_per_mm,
        "row_pitch_mm": y_spacing / pixels_per_mm,
    }


def _to_detection(
    item: Mapping[str, Any], source: str, options: TraceOptions
) -> TraceDetection:
    confidence = float(item["confidence"])
    return TraceDetection(
        id=_new_id(), index=0, source=source,
        confidence=confidence, score=float(item["score"]),
        shape=str(item.get("shape", "contour")),
        center_mm=tuple(item["center_mm"]),
        width_mm=float(item["width_mm"]), height_mm=float(item["height_mm"]),
        rotation_deg=float(item["rotation_deg"]),
        corner_radius_mm=float(item["corner_radius_mm"]),
        area_mm2=float(item["area_mm2"]),
        contour_mm=[list(point) for point in item["contour_mm"]],
        vector_contour_mm=[
            list(point)
            for point in item.get("vector_contour_mm", item["contour_mm"])
        ],
        box_mm=[list(point) for point in item["box_mm"]],
        selected_default=(
            source == "direct" and confidence >= options.confidence_threshold
        ),
        diagnostics={
            "rectangularity": float(item["rectangularity"]),
            "solidity": float(item["solidity"]),
            "fit_iou": float(item["fit_iou"]),
            "color_coverage": float(item["coverage"]),
            "compactness": float(item.get("compactness", 0.0)),
            **(
                {
                    "grid_normalized": bool(item.get("grid_normalized", False)),
                    "grid_row": int(item["grid_row"]),
                    "grid_column": int(item["grid_column"]),
                }
                if "grid_row" in item and "grid_column" in item
                else {}
            ),
            **(
                {
                    "observed_center_mm": list(item["observed_center_mm"]),
                    "observed_width_mm": float(item["observed_width_mm"]),
                    "observed_height_mm": float(item["observed_height_mm"]),
                    "observed_rotation_deg": float(item["observed_rotation_deg"]),
                    "observed_corner_radius_mm": float(
                        item["observed_corner_radius_mm"]
                    ),
                }
                if "observed_center_mm" in item
                else {}
            ),
        },
    )


def detect_objects(
    image: np.ndarray,
    options: TraceOptions | Mapping[str, Any] | None,
    work_area: WorkArea,
    pixels_per_mm: float,
) -> TraceResult:
    options = (
        options
        if isinstance(options, TraceOptions)
        else TraceOptions.from_mapping(options)
    )
    if image is None or image.size == 0:
        raise ValueError("No rectified camera image is available")
    pixels_per_mm = float(pixels_per_mm)
    if pixels_per_mm <= 0:
        raise ValueError("pixels_per_mm must be positive")

    target_hue = options.target_hue
    masks: list[tuple[str, np.ndarray, float | None]] = []
    if options.detection_mode in {"auto", "color"}:
        if target_hue is None:
            target_hue = auto_target_hue(image, options.min_saturation)
        if target_hue is not None:
            masks.append((
                "color",
                _color_mask(image, target_hue, options, pixels_per_mm),
                target_hue,
            ))
    if options.detection_mode in {"auto", "contrast"}:
        masks.append(("contrast", _contrast_mask(image, pixels_per_mm), None))
        for region_mask in _contrast_region_masks(image, pixels_per_mm):
            masks.append(("contrast", region_mask, None))
    if not masks:
        return TraceResult(
            False, [], options.detection_mode, target_hue,
            image.shape[1], image.shape[0], 0, 0, None,
            "No suitable target color was found", options,
        )

    best = None
    for mode, mask, hue in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        candidates = [
            candidate for contour in contours
            if (candidate := _candidate(
                contour, mask, options, work_area, pixels_per_mm
            )) is not None
        ]
        quality = sum(item["score"] for item in candidates) + 0.35 * len(candidates)
        if best is None or quality > best[0]:
            best = (quality, mode, hue, candidates)
    assert best is not None
    _, mode_used, chosen_hue, candidates = best
    if chosen_hue is not None:
        target_hue = chosen_hue

    candidates, inferred, grid = _infer_grid(
        candidates, options, work_area, pixels_per_mm
    )
    detections = [
        *[_to_detection(item, "direct", options) for item in candidates],
        *[_to_detection(item, "inferred", options) for item in inferred],
    ]
    detections.sort(key=lambda item: (-item.center_mm[1], item.center_mm[0]))
    for index, detection in enumerate(detections, 1):
        detection.index = index

    direct_count = sum(item.source == "direct" for item in detections)
    inferred_count = sum(item.source == "inferred" for item in detections)
    selected_count = sum(item.selected_default for item in detections)
    message = (
        f"Found {direct_count} direct object{'s' if direct_count != 1 else ''}"
    )
    if inferred_count:
        message += (
            f" and inferred {inferred_count} missing grid "
            f"position{'s' if inferred_count != 1 else ''}"
        )
    if grid and grid.get("normalized"):
        message += (
            f"; fitted identical {int(grid['columns'])} × "
            f"{int(grid['rows'])} grid cells"
        )
    message += f"; {selected_count} selected by confidence"
    if not detections:
        message = "No objects passed the current filters"

    return TraceResult(
        bool(detections), detections, mode_used, target_hue,
        image.shape[1], image.shape[0], direct_count, inferred_count,
        grid, message, options,
    )
