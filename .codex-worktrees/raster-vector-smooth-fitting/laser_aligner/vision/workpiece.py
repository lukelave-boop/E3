from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(slots=True)
class WorkpieceDetection:
    polygon_px: list[list[float]]
    center_px: list[float]
    width_px: float
    height_px: float
    angle_deg: float
    area_ratio: float
    rectangularity: float
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "polygon_px": self.polygon_px,
            "center_px": self.center_px,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "angle_deg": self.angle_deg,
            "area_ratio": self.area_ratio,
            "rectangularity": self.rectangularity,
            "score": self.score,
        }


def detect_workpiece(
    image: np.ndarray,
    min_area_ratio: float = 0.03,
    canny_low: int = 40,
    canny_high: int = 130,
) -> WorkpieceDetection | None:
    """Find the most workpiece-like quadrilateral in a rectified bed image.

    This is deliberately traditional computer vision, not a neural network. It
    performs best when the workpiece edge contrasts with the spoil board.
    """
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        return None
    if (
        image.dtype != np.uint8
        or image.ndim not in (2, 3)
        or (image.ndim == 3 and image.shape[2] != 3)
    ):
        raise ValueError("Workpiece image must be a non-empty uint8 grayscale or BGR array")
    if (
        type(min_area_ratio) is bool
        or not math.isfinite(float(min_area_ratio))
        or not 0.0 < float(min_area_ratio) < 0.92
    ):
        raise ValueError("Minimum workpiece area ratio must be finite and between 0 and 0.92")
    if (
        type(canny_low) is not int
        or type(canny_high) is not int
        or not 0 <= canny_low < canny_high <= 255
    ):
        raise ValueError("Canny thresholds must be integers with 0 <= low < high <= 255")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    gray = cv2.GaussianBlur(gray, (7, 7), 0)
    edges = cv2.Canny(gray, canny_low, canny_high)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = float(image.shape[0] * image.shape[1])
    best: WorkpieceDetection | None = None

    for contour in contours:
        area = float(cv2.contourArea(contour))
        area_ratio = area / image_area
        if area_ratio < min_area_ratio or area_ratio > 0.92:
            continue
        rect = cv2.minAreaRect(contour)
        (center_x, center_y), (width, height), angle = rect
        rectangle_area = float(width * height)
        if rectangle_area <= 1.0:
            continue
        rectangularity = min(1.0, area / rectangle_area)
        perimeter = cv2.arcLength(contour, True)
        compactness = 4.0 * np.pi * area / max(perimeter * perimeter, 1.0)
        score = area_ratio * (0.65 + 0.35 * rectangularity) * (0.8 + 0.2 * compactness)
        box = cv2.boxPoints(rect)
        detection = WorkpieceDetection(
            polygon_px=[[float(x), float(y)] for x, y in box],
            center_px=[float(center_x), float(center_y)],
            width_px=float(width),
            height_px=float(height),
            angle_deg=float(angle),
            area_ratio=area_ratio,
            rectangularity=rectangularity,
            score=float(score),
        )
        if best is None or detection.score > best.score:
            best = detection
    return best
