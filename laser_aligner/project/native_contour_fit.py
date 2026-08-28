"""Source-neutral physical contour to native path fitting contract.

Segmentation belongs to each source pipeline.  The implementation delegates to
the one authoritative fitter developed with raster vectorization so camera and
raster contours cannot drift into independent line/cubic fitting behavior.
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
