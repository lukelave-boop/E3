"""Non-destructive, edge-connected white-background cleanup."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

DEFAULT_TOLERANCE = 5
MAX_TOLERANCE = 64


def cleanup_settings(geometry: Mapping[str, Any]) -> tuple[bool, int]:
    value = geometry.get("background_cleanup")
    if "background_cleanup" not in geometry:
        return False, DEFAULT_TOLERANCE
    if not isinstance(value, dict) or set(value) != {"enabled", "tolerance"}:
        raise ValueError("Image background cleanup requires enabled and tolerance")
    enabled, tolerance = value["enabled"], value["tolerance"]
    if type(enabled) is not bool:
        raise ValueError("Image background cleanup enabled must be a boolean")
    if type(tolerance) is not int or not 0 <= tolerance <= MAX_TOLERANCE:
        raise ValueError(f"Image background cleanup tolerance must be an integer from 0 to {MAX_TOLERANCE}")
    return enabled, tolerance


def background_mask(gray: np.ndarray, tolerance: int) -> np.ndarray:
    """Select near-white pixels connected to any edge by four-neighbour paths.

    Work on full-resolution, white-composited grayscale, before resampling.
    A padded white border seeds all four edges in a single bounded flood fill.
    """
    import cv2

    cleanup_settings({"background_cleanup": {"enabled": True, "tolerance": tolerance}})
    if gray.dtype != np.uint8 or gray.ndim != 2 or not gray.size:
        raise ValueError("Background cleanup requires a nonempty 8-bit grayscale image")
    from .raster_asset import MAX_RASTER_DECODED_BYTES, MAX_RASTER_DIMENSION

    if max(gray.shape) > MAX_RASTER_DIMENSION or gray.size * 4 > MAX_RASTER_DECODED_BYTES:
        raise ValueError("Background cleanup image exceeds the bounded raster decode limit")
    candidate = np.pad((gray >= 255 - tolerance).astype(np.uint8), 1, constant_values=1)
    cv2.floodFill(candidate, None, (0, 0), 2, flags=4)
    return candidate[1:-1, 1:-1] == 2


def clean_background(gray: np.ndarray, geometry: Mapping[str, Any]) -> np.ndarray:
    enabled, tolerance = cleanup_settings(geometry)
    if not enabled:
        return gray
    output = gray.copy()
    output[background_mask(gray, tolerance)] = 255
    return output
