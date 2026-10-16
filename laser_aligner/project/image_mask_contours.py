"""Bounded subpixel contours for the image editor's binary eligibility plane."""
from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np

# Marching squares, directed with foreground on the right in image coordinates.
# Saddles connect foreground diagonally, matching the cleanup's 8-connectivity.
# Unlike walking pixel centres, these edges cannot retrace a one-pixel whisker.
_EDGES = (
    (), ((3, 0),), ((0, 1),), ((3, 1),),
    ((1, 2),), ((3, 2), (1, 0)), ((0, 2),), ((3, 2),),
    ((2, 3),), ((2, 0),), ((0, 3), (2, 1)), ((2, 1),),
    ((1, 3),), ((1, 0),), ((0, 3),), (),
)
_OFFSETS = ((1, 0), (2, 1), (1, 2), (0, 1))
MAX_BOUNDARY_POINTS = 250_000
MAX_CONTOURS = 4096


def mask_contours(
    mask: np.ndarray, *, include_holes: bool,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[np.ndarray], list[int | None]]:
    """Return parent-first contours in source pixel-centre coordinates.

    Half-level crossings lie at most half a pixel outside the outermost pixel
    centres, including at the image frame. Padding closes edge-touching regions.
    No smoothing, component removal or geometric repair occurs here.
    """
    from .raster_vectorize import RasterVectorizationCancelledError

    def check():
        if cancel_check is not None and cancel_check():
            raise RasterVectorizationCancelledError("Image outline cancelled")

    check()
    pixels = np.pad((mask != 0).astype(np.uint8), 1)
    cases = (pixels[:-1, :-1] + 2 * pixels[:-1, 1:]
             + 4 * pixels[1:, 1:] + 8 * pixels[1:, :-1])
    counts = np.bincount(cases.ravel(), minlength=16)
    if sum(int(counts[i]) * len(edges) for i, edges in enumerate(_EDGES)) > MAX_BOUNDARY_POINTS:
        raise ValueError("Image outline exceeds the bounded contour-point limit; resize the source image")
    links: dict[tuple[int, int], tuple[int, int]] = {}
    for case, edges in enumerate(_EDGES):
        if not edges:
            continue
        check()
        rows, columns = np.nonzero(cases == case)
        for index, (row, column) in enumerate(zip(rows.tolist(), columns.tolist(), strict=True)):
            if index % 1024 == 0:
                check()
            for start, end in edges:
                a, b = _OFFSETS[start], _OFFSETS[end]
                links[(2 * column + a[0], 2 * row + a[1])] = (2 * column + b[0], 2 * row + b[1])
    if not links:
        raise ValueError("No closed foreground contours were produced")
    loops = []
    while links:
        check()
        if len(loops) >= MAX_CONTOURS:
            raise ValueError("Image outline exceeds the bounded contour-count limit")
        start = next(iter(links))
        points = [start]
        current = links.pop(start)
        while current != start:
            if len(points) % 1024 == 0:
                check()
            points.append(current)
            current = links.pop(current)
        loop = np.asarray(points, dtype=np.float32) / 2 - 1
        # Remove only exactly collinear intermediate vertices.
        before = loop - np.roll(loop, 1, axis=0)
        after = np.roll(loop, -1, axis=0) - loop
        loop = loop[before[:, 0] * after[:, 1] != before[:, 1] * after[:, 0]]
        loops.append(loop)

    # Larger enclosing loops precede their descendants. Disjoint bounding boxes
    # avoid polygon queries; cap the remaining point work before each query.
    loops.sort(key=lambda loop: abs(cv2.contourArea(loop)), reverse=True)
    bounds = [(loop.min(axis=0), loop.max(axis=0)) for loop in loops]
    parents: list[int | None] = []
    point_work = 0
    for index, loop in enumerate(loops):
        check()
        parent = None
        lower, upper = bounds[index]
        for previous in range(index - 1, -1, -1):
            if previous % 256 == 0:
                check()
            lo, hi = bounds[previous]
            if np.any(lower < lo) or np.any(upper > hi):
                continue
            point_work += len(loops[previous])
            if point_work > 5_000_000:
                raise ValueError("Image outline exceeds the bounded hierarchy-work limit")
            if cv2.pointPolygonTest(loops[previous], tuple(map(float, loop[0])), False) > 0:
                parent = previous
                break
        parents.append(parent)
    if not include_holes:
        loops = [loop for loop, parent in zip(loops, parents, strict=True) if parent is None]
        parents = [None] * len(loops)
    return loops, parents
