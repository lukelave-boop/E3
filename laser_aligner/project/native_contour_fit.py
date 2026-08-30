"""Adapter from specialized physical contour trees to the native path fitter.

Imported rasters, non-grid camera Contrast, and Auto's raster strategies enter
the complete source-neutral pixel vectorizer. Manual/Auto Color and grid
detectors already own physical contour trees; this adapter lets those
specialized paths delegate to the same one authoritative line/cubic fitter
without duplicating its behavior.
"""

from __future__ import annotations

from .raster_vectorize import (
    PhysicalContourFitContour,
    PhysicalContourFitResult,
    PrimitiveRecoveryMetrics,
    fit_physical_contours_to_native_path,
)

__all__ = [
    "PhysicalContourFitContour",
    "PhysicalContourFitResult",
    "PrimitiveRecoveryMetrics",
    "fit_physical_contours_to_native_path",
]
