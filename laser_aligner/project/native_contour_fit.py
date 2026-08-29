"""Adapter from specialized physical contour trees to the native path fitter.

Imported rasters and non-grid camera Contrast enter the complete source-neutral
pixel vectorizer. Auto, Color, and grid detectors already own physical contour
trees; this adapter lets those specialized paths delegate to the same one
authoritative line/cubic fitter without duplicating its behavior.
"""

from __future__ import annotations

from .raster_vectorize import (
    PhysicalContourFitContour,
    PhysicalContourFitResult,
    fit_physical_contours_to_native_path,
)

__all__ = [
    "PhysicalContourFitContour",
    "PhysicalContourFitResult",
    "fit_physical_contours_to_native_path",
]
