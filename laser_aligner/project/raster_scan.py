"""Resolve raster scan direction independently of calibrated artwork placement."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .model import LayerMode, OperationLayer

if TYPE_CHECKING:
    from ..calibration.support import HoneycombCoordinateFrame


def project_scan_angle(
    layer: OperationLayer,
    coordinate_frame: HoneycombCoordinateFrame | None,
) -> float:
    """Return the project-frame angle used to sample an operation.

    Raster angles are relative to machine X. Subtracting the support rotation
    before sampling leaves that direction unchanged when artwork is placed in
    the machine. Fill retains its project-relative angle. Persisted layers and
    artwork transforms are never modified.
    """
    if layer.mode != LayerMode.RASTER or coordinate_frame is None:
        return layer.scan_angle_deg
    x_axis = coordinate_frame.x_axis_machine
    frame_angle = math.degrees(math.atan2(x_axis[1], x_axis[0]))
    return layer.scan_angle_deg - frame_angle


def scan_cos_sin(angle_degrees: float) -> tuple[float, float]:
    """Keep cardinal scan axes exact, including boundary-row intersections."""
    cardinal = {0.0: (1.0, 0.0), 90.0: (0.0, 1.0),
                180.0: (-1.0, 0.0), 270.0: (0.0, -1.0)}
    if (axis := cardinal.get(angle_degrees % 360.0)) is not None:
        return axis
    angle = math.radians(angle_degrees)
    return math.cos(angle), math.sin(angle)
