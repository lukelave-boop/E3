from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

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
