from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.desktop.theme import DRAFTING_COLORS
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import Bounds


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def test_interactive_view_paints_the_light_bed_without_a_camera_frame(
    qt_application: QtWidgets.QApplication,
) -> None:
    view = WorkspaceView(Bounds(0.0, 0.0, 300.0, 200.0))
    view.resize(900, 650)
    view.show()
    qt_application.processEvents()
    view.fit_work_area()
    qt_application.processEvents()

    image = view.grab().toImage()
    sample = view.mapFromScene(QtCore.QPointF(2.5, -2.5))
    color = image.pixelColor(sample)

    assert color.red() >= 240
    assert color.green() >= 240
    assert color.blue() >= 240
    assert (
        view.horizontalScrollBarPolicy()
        == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert (
        view.verticalScrollBarPolicy()
        == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )

    view.close()
    view.deleteLater()


def test_camera_overlay_has_no_machine_rectangle_background_tint(
    qt_application: QtWidgets.QApplication,
) -> None:
    work_area = Bounds(10.0, 10.0, 210.0, 210.0)
    camera_area = Bounds(0.0, 0.0, 220.0, 220.0)
    view = WorkspaceView(work_area)
    camera = QtGui.QImage(220, 220, QtGui.QImage.Format.Format_RGB888)
    camera.fill(QtGui.QColor("#808080"))
    view.set_camera_opacity(0.5)
    view.set_camera_image(camera, pixels_per_mm=1.0, image_area=camera_area)

    rendered = QtGui.QImage(220, 220, QtGui.QImage.Format.Format_RGB888)
    rendered.fill(QtGui.QColor("#FF00FF"))
    painter = QtGui.QPainter(rendered)
    try:
        view.workspace_scene.render(
            painter,
            QtCore.QRectF(0.0, 0.0, 220.0, 220.0),
            QtCore.QRectF(0.0, -220.0, 220.0, 220.0),
        )
    finally:
        painter.end()

    # These points contain the same camera pixel. The first lies outside the
    # X10..210 machine rectangle and the second lies inside it; the configured
    # machine area must not appear as a lighter rectangle through the overlay.
    outside_machine = rendered.pixelColor(5, 119)
    inside_machine = rendered.pixelColor(100, 119)
    assert outside_machine == inside_machine
    assert outside_machine != QtGui.QColor(DRAFTING_COLORS["bed"])

    view.close()
    view.deleteLater()
    qt_application.processEvents()


def test_detailed_toolpath_overlay_batches_segments_into_three_scene_items(
    qt_application: QtWidgets.QApplication,
) -> None:
    view = WorkspaceView(Bounds(0.0, 0.0, 300.0, 200.0))
    lines = ["G21", "G90", "M5", "G0 X1 Y1 F1000"]
    lines.extend(
        f"G0 X{1 + index % 100} Y{1 + index // 100} F1000"
        for index in range(1, 1000)
    )
    lines.extend(("M4 S100", "G1 X100 Y20 F500"))
    lines.extend(
        f"G1 X{100 - index % 100} Y{20 + index // 100} F500"
        for index in range(1, 1000)
    )
    lines.extend(("M5", "G1 X1 Y40 F500"))

    view.set_toolpath_preview("\n".join(lines))
    qt_application.processEvents()

    assert len(view._toolpath_items) == 3
    assert all(
        isinstance(item, QtWidgets.QGraphicsPathItem)
        for item in view._toolpath_items
    )
    assert sum(item.path().elementCount() for item in view._toolpath_items) > 3000
    view.clear_toolpath_preview()
    assert view._toolpath_items == []
    view.deleteLater()
