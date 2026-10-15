from __future__ import annotations

import copy
import os
import threading
import time

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtTest

from laser_aligner.desktop import image_editor as editor_module
from laser_aligner.desktop.image_editor import ImageEditorDialog
from laser_aligner.desktop.qt import require_qt
from laser_aligner.project.image_edit import trace_foreground, validate_outline
from laser_aligner.project.path_geometry import NativePathGeometry, PathLineSegment, PathSubpath
from laser_aligner.project.raster_asset import read_raster_asset_payload

QtCore, QtGui, QtWidgets = require_qt()


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def source(tmp_path):
    # Synthetic engraved subject with internal gray grain, lettering and separate shadow.
    gray = np.full((96, 128), 253, dtype=np.uint8)
    gray[10:65, 20:108] = np.tile(np.arange(88, dtype=np.uint8) + 50, (55, 1))
    gray[25:28, 32:96] = 230
    gray[35:48, 42:45] = 12
    cv2.ellipse(gray, (64, 80), (42, 6), 0, 0, 360, 145, -1)
    path = tmp_path / "synthetic-barrel.png"
    assert cv2.imwrite(str(path), gray)
    return path, read_raster_asset_payload(path), gray


def _rectangle_region():
    return {"operation": "subtract", "kind": "rectangle",
            "points": [[-.5, -.22], [.5, -.48]], "radius": 0.0}


def _subject_path():
    return NativePathGeometry((PathSubpath((-.35, .42), (
        PathLineSegment((.35, .42)), PathLineSegment((.35, -.2)), PathLineSegment((-.35, -.2)),
    ), closed=True),))


def test_shadow_edit_preserves_pixels_source_history_and_reopen(app, source):
    path, payload, gray = source
    encoded = path.read_bytes()
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    assert not dialog.create_vector
    dialog.add_region(_rectangle_region())
    edited = dialog.settings
    assert np.array_equal(dialog.after_gray[10:65, 20:108], gray[10:65, 20:108])
    assert np.all(dialog.after_gray[75:86, 24:104] == 255)
    assert not dialog.allowed[80, 64]
    dialog.undo()
    assert np.array_equal(dialog.after_gray, gray)
    dialog.redo()
    assert dialog.settings == edited
    dialog.before.setChecked(True)
    assert dialog.view.image_item.pixmap().toImage().pixelColor(64, 80).red() == 145
    dialog.before.setChecked(False)
    assert dialog.view.image_item.pixmap().toImage().pixelColor(64, 80).red() == 255
    reopened = ImageEditorDialog(payload, {"image_edit": edited}, 64, 48)
    assert reopened.settings == edited
    assert np.array_equal(reopened.after_gray, dialog.after_gray)
    dialog.invert.setChecked(True)
    assert dialog.after_gray[80, 64] == 255
    assert dialog.after_gray[40, 40] == 255 - gray[40, 40]
    dialog.reset_edits()
    assert not dialog.settings["regions"]
    dialog.undo()
    assert dialog.settings["regions"] == edited["regions"]
    dialog.reject()
    reopened.reject()
    assert path.read_bytes() == encoded


def test_trace_preview_preserves_refinements_until_explicit_replace(app, source):
    _, payload, gray = source
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    dialog.add_subpath(_subject_path().subpaths[0])
    refined = dialog.settings["outline"]
    candidate = trace_foreground(gray, {"minimum_component_area": 5})
    dialog._trace_finished(dialog._generation, candidate, None)
    assert dialog.settings["outline"] == refined
    assert dialog.candidate == candidate
    assert "Replace" in dialog.apply_candidate_button.text()
    assert len(candidate.subpaths) == 2
    dialog.apply_candidate()
    assert dialog.settings["outline"] == candidate.to_dict()
    dialog.undo()
    assert dialog.settings["outline"] == refined
    dialog.redo()
    assert dialog.settings["outline"] == candidate.to_dict()
    dialog.add_region(_rectangle_region())
    assert dialog.candidate is None
    dialog.reject()


def test_pen_and_node_actions_preserve_native_curves(app, source):
    _, payload, _ = source
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    view = dialog.view
    view.set_tool("pen")
    view.points = [[-.3, -.1], [.3, -.1], [.3, .3], [-.3, .3]]
    view.pen_handles = [[.05, 0], [0, .05], [-.05, 0], [0, -.05]]
    view.finish_pen()
    assert all(segment["type"] == "cubic" for segment in dialog.settings["outline"]["subpaths"][0]["segments"])
    assert not validate_outline(dialog.settings["outline"])
    original = dialog.settings
    view.selected = (0, -1, "start")
    view.add_node()
    assert len(dialog.settings["outline"]["subpaths"][0]["segments"]) == 5
    view.selected = (0, 0, "to")
    view.delete_node()
    assert len(dialog.settings["outline"]["subpaths"][0]["segments"]) == 4
    dialog.undo()
    dialog.undo()
    assert dialog.settings == original
    view.selected = (0, -1, "start")
    view.remove_contour()
    assert dialog.settings["outline"] is None
    dialog.undo()
    assert dialog.settings == original
    dialog.reject()


def test_mouse_stroke_and_node_drag_only_process_on_commit(app, source, monkeypatch):
    _, payload, _ = source
    decoded = []
    decoder = editor_module.decode_raster_payload_planes
    monkeypatch.setattr(editor_module, "decode_raster_payload_planes",
                        lambda value: (decoded.append(1), decoder(value))[1])
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    dialog.show()
    app.processEvents()
    view = dialog.view
    view.set_tool("exclude")
    initial = dialog.preview_count
    start = view.mapFromScene(35, 80)
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
    for x in range(36, 90, 2):
        QtTest.QTest.mouseMove(view.viewport(), view.mapFromScene(x, 80))
    assert dialog.preview_count == initial
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                             pos=view.mapFromScene(90, 80))
    assert dialog.preview_count == initial + 1
    assert len(dialog.settings["regions"]) == 1
    dialog.add_subpath(_subject_path().subpaths[0])
    view.set_tool("nodes")
    initial = dialog.preview_count
    node = view.mapFromScene(view.mapped(dialog.settings["outline"]["subpaths"][0]["start"]))
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=node)
    for offset in range(1, 20):
        QtTest.QTest.mouseMove(view.viewport(), node + QtCore.QPoint(offset, 2))
    assert dialog.preview_count == initial
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=node + QtCore.QPoint(19, 2))
    assert dialog.preview_count == initial + 1
    assert decoded == [1]
    dialog.reject()


@pytest.mark.parametrize("kind", ["rectangle", "ellipse", "lasso", "restore"])
def test_selection_gestures_and_restore(app, source, kind):
    _, payload, _ = source
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    dialog.show()
    app.processEvents()
    view = dialog.view
    view.set_tool(kind)
    points = [(30, 75), (95, 75), (95, 85), (30, 85)] if kind == "lasso" else [(30, 75), (95, 85)]
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                           pos=view.mapFromScene(*points[0]))
    for point in points[1:]:
        QtTest.QTest.mouseMove(view.viewport(), view.mapFromScene(*point))
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.MouseButton.LeftButton,
                             pos=view.mapFromScene(*points[-1]))
    assert len(dialog.settings["regions"]) == 1
    assert dialog.settings["regions"][0]["kind"] == ({"lasso": "polygon", "restore": "brush"}.get(kind, kind))
    dialog.reject()


def test_stale_worker_result_and_close_do_not_overwrite_latest_edit(app, source, monkeypatch):
    _, payload, _ = source
    gate = threading.Event()
    started = threading.Event()

    def delayed(*_args, **_kwargs):
        started.set()
        gate.wait(3)
        return _subject_path()

    monkeypatch.setattr(editor_module, "trace_foreground", delayed)
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    dialog.preview_trace()
    assert started.wait(2)
    assert not dialog.trace_button.isEnabled()
    dialog.add_region(_rectangle_region())
    latest = dialog.settings
    gate.set()
    deadline = time.monotonic() + 3
    while dialog._worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert dialog._worker is None
    assert dialog.trace_button.isEnabled()
    assert dialog.settings == latest
    assert dialog.candidate is None
    dialog.reject()
    dialog._trace_finished(dialog._generation, _subject_path(), None)
    assert dialog.candidate is None


def test_vector_is_explicit_snapshot_and_bad_geometry_rejected(app, source):
    _, payload, _ = source
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    dialog.add_subpath(_subject_path().subpaths[0])
    assert not dialog.create_vector
    dialog.vector_checkbox.setChecked(True)
    dialog.offset.setValue(.25)
    dialog.preview_vector()
    dialog.accept()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert dialog.vector_outline is not None
    assert dialog.vector_outline.to_dict() == dialog.settings["outline"]
    assert dialog.offset_mm == .25
    assert dialog.link_vector
    bad = ImageEditorDialog(payload, {}, 64, 48)
    state = bad.settings
    native = _subject_path().to_dict()
    native["subpaths"][0]["segments"][0]["to"] = [.35, -.2]
    native["subpaths"][0]["segments"][1]["to"] = [.35, .42]
    state["outline"] = native
    state["mask_enabled"] = False
    bad.commit(state)
    before = copy.deepcopy(bad.settings)
    bad.vector_checkbox.setChecked(True)
    bad.accept()
    assert bad.result() != QtWidgets.QDialog.DialogCode.Accepted
    assert "Cannot create vector" in bad.status.text()
    assert bad.settings == before
    bad.reject()


def test_cancelled_gesture_reset_and_stale_candidate_preserve_history(app, source):
    _, payload, _ = source
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    dialog.show()
    app.processEvents()
    dialog.add_subpath(_subject_path().subpaths[0])
    original = dialog.settings
    generation = dialog._generation
    dialog._trace_finished(generation, _subject_path(), None)
    view = dialog.view
    view.set_tool("exclude")
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=view.mapFromScene(35, 80))
    QtTest.QTest.mouseMove(view.viewport(), view.mapFromScene(90, 80))
    assert dialog.candidate is None
    dialog._trace_finished(generation, _subject_path(), None)
    assert dialog.candidate is None
    QtTest.QTest.keyClick(view.viewport(), QtCore.Qt.Key.Key_Escape)
    assert dialog.settings == original
    assert not view.points
    dialog.reset_edits()
    assert dialog.settings["outline"] is None
    dialog.undo()
    assert dialog.settings == original
    dialog.reject()


def test_trace_preferences_save_without_replacing_outline_or_reprocessing(app, source):
    _, payload, _ = source
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    dialog.add_subpath(_subject_path().subpaths[0])
    original = dialog.settings["outline"]
    count = dialog.preview_count
    dialog.tolerance.setValue(42)
    dialog.smoothing.setValue(1.5)
    assert dialog.preview_count == count
    dialog.accept()
    assert dialog.settings["trace"]["tolerance"] == 42
    assert dialog.settings["trace"]["smoothing"] == 1.5
    reopened = ImageEditorDialog(payload, {"image_edit": dialog.settings}, 64, 48)
    assert reopened.tolerance.value() == 42
    assert reopened.smoothing.value() == 1.5
    assert reopened.settings["outline"] == original
    reopened.reject()


def test_local_edits_require_explicit_mask_outline_review_before_vector(app, source):
    _, payload, _ = source
    dialog = ImageEditorDialog(payload, {}, 64, 48)
    dialog.add_subpath(_subject_path().subpaths[0])
    dialog.add_region(_rectangle_region())
    dialog.vector_checkbox.setChecked(True)
    dialog.preview_vector()
    assert dialog.vector_outline is None
    assert "accept it before creating a vector" in dialog.status.text()
    candidate = editor_module.outline_from_mask(dialog.allowed, dialog._trace_options())
    dialog._candidate_from_mask = True
    dialog._trace_finished(dialog._generation, candidate, None)
    assert dialog.settings["regions"]
    dialog.apply_candidate()
    assert not dialog.settings["regions"]
    assert dialog.settings["outline"] == candidate.to_dict()
    dialog.preview_vector()
    assert dialog.vector_outline == candidate
    dialog.accept()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted


def test_transparent_source_remains_excluded_after_invert_and_restore(app, tmp_path):
    pixels = np.zeros((32, 32, 4), dtype=np.uint8)
    pixels[8:24, 8:24, :3] = 128
    pixels[8:24, 8:24, 3] = 255
    path = tmp_path / "alpha.png"
    assert cv2.imwrite(str(path), pixels)
    dialog = ImageEditorDialog(read_raster_asset_payload(path), {}, 32, 32)
    dialog.invert.setChecked(True)
    assert dialog.after_gray[0, 0] == 255
    assert not dialog.allowed[0, 0]
    dialog.add_region({"operation": "restore", "kind": "rectangle",
                       "points": [[-.5, .5], [.5, -.5]], "radius": 0})
    assert not dialog.allowed[0, 0]
    assert dialog.after_gray[0, 0] == 255
    assert dialog.after_gray[12, 12] == 127
    dialog.reject()

