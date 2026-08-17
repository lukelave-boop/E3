from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from ..config import WorkArea


def corrected_frame_size(
    work_area: WorkArea,
    pixels_per_mm: float,
) -> tuple[int, int]:
    """Return the full-bed corrected-frame width and height in pixels."""

    if not isinstance(work_area, WorkArea):
        raise TypeError("work_area must be a WorkArea")
    ppm = float(pixels_per_mm)
    if not math.isfinite(ppm) or ppm <= 0.0:
        raise ValueError("pixels_per_mm must be a positive finite number")
    if work_area.width <= 0.0 or work_area.height <= 0.0:
        raise ValueError("work_area must have positive width and height")
    return (
        max(1, int(round(work_area.width * ppm))),
        max(1, int(round(work_area.height * ppm))),
    )


def prepare_corrected_test_image(
    image: np.ndarray,
    work_area: WorkArea,
    pixels_per_mm: float,
) -> np.ndarray:
    """Validate and resize a top-down full-work-area BGR image.

    Source dimensions may differ from the configured corrected-frame size, but
    they must be consistent with one uniform pixel scale.  The only permitted
    aspect discrepancy is the half-pixel uncertainty introduced when an ideal
    floating-point width and height are rounded to integer image dimensions.
    """

    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("The selected test image is empty")
    if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ValueError("The selected test image must decode as an 8-bit color image")
    target_width, target_height = corrected_frame_size(work_area, pixels_per_mm)
    source_height, source_width = image.shape[:2]
    source_ratio = source_width / source_height
    expected_ratio = work_area.width / work_area.height

    width_scale = (
        (source_width - 0.5) / work_area.width,
        (source_width + 0.5) / work_area.width,
    )
    height_scale = (
        (source_height - 0.5) / work_area.height,
        (source_height + 0.5) / work_area.height,
    )
    compatible_scale_min = max(width_scale[0], height_scale[0])
    compatible_scale_max = min(width_scale[1], height_scale[1])
    rounding_epsilon = max(
        1.0,
        abs(compatible_scale_min),
        abs(compatible_scale_max),
    ) * 1e-12
    if compatible_scale_min > compatible_scale_max + rounding_epsilon:
        raise ValueError(
            "The test image must represent the complete corrected work area: "
            f"expected aspect ratio {expected_ratio:.4f}, got {source_ratio:.4f}"
        )
    if (source_width, source_height) == (target_width, target_height):
        return np.ascontiguousarray(image).copy()
    interpolation = (
        cv2.INTER_AREA
        if source_width >= target_width and source_height >= target_height
        else cv2.INTER_LINEAR
    )
    resized = cv2.resize(
        image,
        (target_width, target_height),
        interpolation=interpolation,
    )
    return np.ascontiguousarray(resized)


def load_corrected_test_image(
    path: str | Path,
    work_area: WorkArea,
    pixels_per_mm: float,
) -> np.ndarray:
    """Decode a PNG/JPEG without platform-specific filename limitations."""

    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"Could not read test image {source}: {exc}") from exc
    encoded = np.frombuffer(payload, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not decode test image {source}")
    return prepare_corrected_test_image(image, work_area, pixels_per_mm)


__all__ = [
    "corrected_frame_size",
    "load_corrected_test_image",
    "prepare_corrected_test_image",
]
