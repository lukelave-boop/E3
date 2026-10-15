from __future__ import annotations

import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from laser_aligner.desktop import background_cleanup as cleanup_module
from laser_aligner.desktop.background_cleanup import BackgroundCleanupDialog
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import ObjectPanel
from laser_aligner.desktop.qt import require_qt
from laser_aligner.desktop.workspace import ObjectGraphicsItem, WorkspaceView
from laser_aligner.project import (
    CommandStack,
    ProjectDocument,
    SceneObject,
    read_raster_asset_payload,
)

QtCore, QtGui, QtWidgets = require_qt()


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.png"
    pixels = np.full((32, 32, 3), 253, np.uint8)
    pixels[8:24, 8:24] = (0, 0, 150)
    pixels[12:20, 12:20] = 253
    assert cv2.imwrite(str(path), pixels)
    return path, read_raster_asset_payload(path)


def test_preview_toggle_tolerance_and_original_integrity(app, source):
    path, payload = source
    before = path.read_bytes()
    dialog = BackgroundCleanupDialog(payload, {})
    dialog.show()
    app.processEvents()
    assert dialog.original_image.pixelColor(0, 0).red() == 253
    assert dialog.after_image.pixelColor(0, 0).red() == 253
    dialog.enabled.setChecked(True)
    assert not dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).isEnabled()
    dialog.accept()  # A pending, unseen preview cannot be accepted.
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
    dialog._update_preview()
    assert dialog.after_image.pixelColor(0, 0) == QtGui.QColor("white")
    assert dialog.original_image.pixelColor(0, 0).red() == 253
    assert dialog.after_image.pixelColor(15, 15).red() == 253
    assert dialog.after_image.pixelColor(8, 8).red() == 150  # Retain color.
    dialog.tolerance.setValue(1)
    dialog._update_preview()
    assert dialog.slider.value() == 1
    assert dialog.after_image.pixelColor(0, 0).red() == 253
    dialog._actual_size()
    dialog._zoom(1.25)
    assert dialog.views[0].transform() == dialog.views[1].transform()
    dialog.reject()
    assert path.read_bytes() == before


def test_panel_selection_and_variant_cache_refresh_rebudget(app, source, monkeypatch):
    path, payload = source
    doc = ProjectDocument.new()
    original = SceneObject(kind="image", layer_id=doc.active_layer_id, geometry={"asset": str(path)})
    clean = SceneObject(kind="image", layer_id=doc.active_layer_id, geometry={
        "asset": str(path), "background_cleanup": {"enabled": True, "tolerance": 5}})
    doc.add_object(original)
    doc.add_object(clean)
    panel = ObjectPanel()
    requested = []
    panel.backgroundCleanupRequested.connect(requested.append)
    panel.set_document(doc, [clean.id])
    assert panel.background_cleanup_button.text() == "Clean background\u2026"
    panel.background_cleanup_button.click()
    assert requested == [clean.id]
    panel.set_selection([clean.id, original.id])
    assert not panel.background_cleanup_button.isEnabled()
    workspace = WorkspaceView(doc.work_area)
    workspace.set_document(doc)
    assert workspace._items_by_id[original.id]._raster_image.pixelColor(0, 0).red() == 253
    assert workspace._items_by_id[clean.id]._raster_image.pixelColor(0, 0).red() == 255
    assert workspace.refresh_raster_previews((payload.identity.path,))
    assert workspace._items_by_id[original.id]._raster_image.pixelColor(0, 0).red() == 253
    assert workspace._items_by_id[clean.id]._raster_image.pixelColor(0, 0).red() == 255
    clean.geometry["background_cleanup"]["enabled"] = False
    workspace.refresh_object(clean.id)
    assert workspace._items_by_id[clean.id]._raster_image.pixelColor(0, 0).red() == 253
    clean.geometry["background_cleanup"]["enabled"] = True
    workspace.set_document(doc)
    ObjectGraphicsItem.rebudget_raster_items(list(workspace._items_by_id.values()))
    assert workspace._items_by_id[original.id]._raster_image.pixelColor(0, 0).red() == 253
    assert workspace._items_by_id[clean.id]._raster_image.pixelColor(0, 0).red() == 255
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_PREVIEW_MAX_PIXELS", 256)
    workspace.set_document(doc)
    assert workspace._items_by_id[clean.id]._raster_image.width() == 16
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_PREVIEW_MAX_PIXELS", 4_000_000)
    workspace.set_document(doc)
    while workspace._raster_restore_queue:
        workspace._raster_restore_slice()
    assert workspace._items_by_id[clean.id]._raster_image.width() == 32
    assert workspace._items_by_id[original.id]._raster_image.pixelColor(0, 0).red() == 253
    assert workspace._items_by_id[clean.id]._raster_image.pixelColor(0, 0).red() == 255
    workspace.close()
    panel.close()


@pytest.mark.parametrize("outcome", ["apply", "cancel", "source_changed", "locked", "stale_preview"])
def test_main_window_commit_cancel_and_stale_source_rejection(app, source, monkeypatch, outcome):
    path, payload = source
    doc = ProjectDocument.new()
    item = SceneObject(kind="image", layer_id=doc.active_layer_id, geometry={"asset": str(path)})
    doc.add_object(item)
    before = item.to_dict()
    history = CommandStack()
    errors = []
    workspace = SimpleNamespace(
        selected_object_ids=lambda: [item.id], select_objects=lambda ids: None,
        raster_preview_identity_for_object=lambda _id: (payload.identity.path, payload.identity.sha256),
    )
    harness = SimpleNamespace(document=doc, history=history, workspace=workspace, show_error=errors.append)

    class Dialog:
        settings = {"enabled": True, "tolerance": 5}

        def __init__(self, *_args):
            pass

        def exec(self):
            if outcome == "source_changed":
                assert cv2.imwrite(str(path), np.zeros((32, 32), np.uint8))
            return (QtWidgets.QDialog.DialogCode.Rejected if outcome == "cancel"
                    else QtWidgets.QDialog.DialogCode.Accepted)

    monkeypatch.setattr(cleanup_module, "BackgroundCleanupDialog", Dialog)
    if outcome == "locked":
        item.locked = True
        before = item.to_dict()
    if outcome == "stale_preview":
        assert cv2.imwrite(str(path), np.zeros((32, 32), np.uint8))
    E3MainWindow.clean_image_background(harness, item.id)
    if outcome == "apply":
        assert not errors
        assert history.depth == 1
        assert item.geometry["background_cleanup"] == Dialog.settings
        history.undo()
        assert item.to_dict() == before
        history.redo()
        assert item.geometry["background_cleanup"] == Dialog.settings
    else:
        assert history.depth == 0
        assert item.to_dict() == before
        assert bool(errors) == (outcome != "cancel")
