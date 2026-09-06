from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from laser_aligner.desktop.machine_setup import ImagePicker
from laser_aligner.desktop.qt import require_qt
from laser_aligner.desktop.surface_height import SurfaceHeightDialog
from tests import test_surface_height_calibration as fixtures

rig = fixtures.rig
surface_context = fixtures.surface_context
install_context_map = fixtures.install_context_map

QtCore, _, QtWidgets = require_qt()


@pytest.fixture
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_collect_fit_check_and_preview_without_changing_active_map(qt_app, surface_context, rig):
    context = surface_context
    preview = ImagePicker()
    dialog = SurfaceHeightDialog(
        context, preview, np.zeros((1080, 1920, 3), dtype=np.uint8),
        image_binding=context.surface_calibration_binding(),
    )
    assert not dialog.preview_button.isEnabled()
    for slot, height in (("lower", -2.), ("upper", 10.), ("check", 4.)):
        install_context_map(context, rig, height)
        dialog.height.setValue(height)
        dialog.save_buttons[slot].click()
        assert "Evidence saved" in dialog.result_status.text()
    before = context.bed_mapping_digest()
    dialog.solve_button.click()
    assert "PASSES" in dialog.result_status.text()
    assert dialog.preview_height.minimum() == -2
    assert dialog.preview_height.maximum() == 10
    dialog.preview_button.click()
    assert preview._image is not None
    assert context.bed_mapping_digest() == before
    dialog.show()
    qt_app.processEvents()
    assert dialog.findChild(QtWidgets.QLabel, "heightStudyScope").isVisible()
    dialog.close()


def test_incomplete_evidence_and_stale_photo_are_visible(qt_app, surface_context, rig):
    context = surface_context
    dialog = SurfaceHeightDialog(
        context, ImagePicker(), np.zeros((1080, 1920, 3), dtype=np.uint8),
        image_binding={"old": "camera"},
    )
    dialog.solve()
    assert "lower and upper" in dialog.result_status.text()
    for slot, height in (("lower", -2.), ("upper", 10.)):
        install_context_map(context, rig, height)
        dialog.height.setValue(height)
        dialog.save_map(slot)
    dialog.solve()
    assert "check required" in dialog.result_status.text()
    dialog.render_preview()
    assert "capture again" in dialog.result_status.text()
    assert not dialog.preview_button.isEnabled()
    dialog.close()


def test_replacing_plane_invalidates_ui_result(qt_app, surface_context, rig):
    context = surface_context
    dialog = SurfaceHeightDialog(context, ImagePicker(), None)
    for slot, height in (("lower", -2.), ("upper", 10.)):
        install_context_map(context, rig, height)
        dialog.height.setValue(height)
        dialog.save_map(slot)
    dialog.solve()
    assert dialog.model is not None
    dialog.save_map("upper")
    assert dialog.model is None
    assert not dialog.preview_button.isEnabled()
    dialog.close()
