from __future__ import annotations

# ruff: noqa: E402
import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets

from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.desktop.job_preview import JobPreviewDialog
from laser_aligner.gcode.job_plan import build_job_plan
from laser_aligner.raster_safety import RasterBoundary


@pytest.mark.parametrize("angle", [0.0, 37.0])
@pytest.mark.parametrize("deferred", [False, True])
def test_full_raster_footprint_is_exact_local_and_persistent(angle: float, deferred: bool) -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theta = math.radians(angle)
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(27.2, 18.0),
        x_axis_machine=(math.cos(theta), math.sin(theta)),
        y_axis_machine=(-math.sin(theta), math.cos(theta)),
        width_mm=191.0,
        height_mm=191.0,
        provenance_digest="a" * 64,
    )
    offset = (-2.0, 3.0)
    boundary = RasterBoundary(frame.corners_machine_mm, (-250.0, 300.0, -250.0, 300.0), "b" * 64)

    def move(code: str, x: float, y: float, power: int = 0) -> str:
        machine_x, machine_y = frame.local_to_machine(x, y)
        return f"{code} X{machine_x - offset[0]:.6f} Y{machine_y - offset[1]:.6f} F1200 S{power}"

    start_x, start_y = frame.local_to_machine(-2.1058, 3.0)
    plan = build_job_plan("\n".join([
        "G21", "G90", "M5", boundary.directive,
        "; Laser spot offset (spot = controller + offset): X-2 Y3",
        '; @E3_LAYER {"id":"raster","mode":"raster"}',
        "M4 S0",
        move("G1", 2.413, 3.0), move("G1", 183.165, 3.0, 200),
        move("G1", 187.6838, 3.0), "M5",
        move("G0", 187.6838, 184.6), "M4 S0",
        move("G1", 183.165, 184.6), move("G1", 2.413, 184.6, 200),
        move("G1", -2.1058, 184.6), "M5",
    ]), power_max=1000, start_position=(start_x - offset[0], start_y - offset[1]))
    dialog = JobPreviewDialog(
        plan, (0.0, 191.0, 0.0, 191.0), "overscan.gcode",
        coordinate_frame=frame, defer_render=deferred,
    )
    if deferred:
        dialog.canvas.start_deferred_render()
        while dialog.canvas._building:
            dialog.canvas._build_slice()

    canvas = dialog.canvas
    assert dialog.plan is plan
    assert canvas.movement_bounds_mm == pytest.approx((-2.1058, 3.0, 187.6838, 184.6), abs=1e-6)
    assert canvas.powered_bounds_mm == pytest.approx((2.413, 3.0, 183.165, 184.6), abs=1e-6)
    surface = canvas._surface_item.polygon().boundingRect()
    assert (surface.left(), -surface.bottom(), surface.right(), -surface.top()) == pytest.approx((0, 0, 191, 191))
    assert canvas.sceneRect().contains(QtCore.QPointF(-2.1058, -3.0))
    assert canvas.sceneRect().contains(QtCore.QPointF(191.0, -191.0))
    assert "Powered bounds: X2.41…183." in dialog.boundary_summary.text()
    assert "Full movement bounds: X-2.11…187.68" in dialog.boundary_summary.text()
    assert "row transfers" in dialog.boundary_summary.text()
    assert not dialog.travel_check.isChecked()
    full_path = canvas._movement_footprint_item.path()
    dialog.set_elapsed(0.0)
    canvas.set_layer_visible("raster", False)
    assert canvas._movement_footprint_item.isVisible()
    assert canvas._powered_footprint_item.isVisible()
    assert canvas._movement_footprint_item.path() == full_path
    assert "Engraving surface" in dialog.legend.text()
    dialog.close()
    dialog.deleteLater()
    application.processEvents()


def test_single_row_fit_includes_runup_outside_the_surface() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    plan = build_job_plan(
        "G21\nG90\nM5\nG0 X-30 Y10\nM4 S0\nG1 X0 Y10\n"
        "G1 X190 Y10 S200\nG1 X220 Y10 S0\nM5",
        power_max=1000, start_position=(-30.0, 10.0),
    )
    dialog = JobPreviewDialog(plan, (0, 191, 0, 191), "one-row.gcode")
    assert dialog.canvas.movement_bounds_mm == (-30.0, 10.0, 220.0, 10.0)
    assert dialog.canvas.sceneRect().contains(QtCore.QPointF(-30.0, -10.0))
    assert dialog.canvas.sceneRect().contains(QtCore.QPointF(220.0, -10.0))
    dialog.close()
    dialog.deleteLater()
    application.processEvents()
