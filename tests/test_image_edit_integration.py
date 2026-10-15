from __future__ import annotations

import copy
import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from laser_aligner.config import LaserSettings
from laser_aligner.project import (
    Bounds,
    CommandStack,
    LayerMode,
    ProjectDocument,
    SceneObject,
    Transform,
    UpdateObjectShapeCommand,
    generate_project_gcode,
    load_project,
    read_raster_asset_payload,
    save_project,
)
from laser_aligner.project.image_edit import default_edit, process_image
from laser_aligner.project.path_geometry import NativePathGeometry


@pytest.fixture
def artwork(tmp_path):
    path = tmp_path / "synthetic-barrel.png"
    gray = np.full((96, 128), 248, np.uint8)
    gray[12:68, 24:104] = np.arange(80, dtype=np.uint8)[None, :] + 40
    gray[29:34, 32:93] = 220  # Light lettering/detail remains source artwork.
    cv2.ellipse(gray, (64, 82), (48, 7), 0, 0, 360, 150, -1)
    assert cv2.imwrite(str(path), gray)
    payload = read_raster_asset_payload(path)
    document = ProjectDocument.new("Synthetic barrel", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode, layer.power_percent = LayerMode.RASTER, 10
    layer.line_interval_mm, layer.overscan_percent = 0.5, 0
    item = SceneObject(kind="image", layer_id=layer.id,
                       transform=Transform(50, 50, 32, 24), geometry={"asset": str(path)})
    document.add_object(item)
    settings = default_edit(payload.identity.sha256, (128, 96))
    settings["regions"] = [{"operation": "subtract", "kind": "rectangle",
                             "points": [[-.5, -.24], [.5, -.5]], "radius": 0}]
    return path, payload, gray, document, item, settings


def test_local_shadow_removal_persists_exact_source_and_all_retained_gray(artwork, tmp_path):
    path, _payload, gray, document, item, settings = artwork
    original = path.read_bytes()
    history = CommandStack()
    geometry = {**item.geometry, "image_edit": settings}
    history.execute(UpdateObjectShapeCommand(document, item.id, item.transform, geometry))
    processed, allowed = process_image(gray, item.geometry)
    assert np.array_equal(processed[allowed], gray[allowed])
    assert np.all(processed[74:] == 255)
    save_project(document, tmp_path / "edited.e3laser")
    reopened = load_project(tmp_path / "edited.e3laser")
    assert reopened.objects[0].geometry["image_edit"] == settings
    assert np.array_equal(process_image(gray, reopened.objects[0].geometry)[0], processed)
    history.undo()
    assert "image_edit" not in item.geometry
    history.redo()
    assert item.geometry["image_edit"] == settings
    assert path.read_bytes() == original


@pytest.mark.parametrize("inverted", [False, True])
@pytest.mark.parametrize("rotation,scan,mirror", [(0, 0, False), (31, 13, True), (90, 45, False)])
def test_excluded_pixels_cannot_power_after_minification_rotation_dithering(artwork, inverted, rotation, scan, mirror):
    _path, _payload, gray, document, item, settings = artwork
    settings["invert"] = inverted
    settings["regions"].append({"operation": "subtract", "kind": "rectangle",
                                "points": [[-.1, -.5], [.1, .5]], "radius": 0})
    item.geometry["image_edit"] = settings
    item.transform.rotation_deg, item.transform.mirror_x = rotation, mirror
    document.layers[0].scan_angle_deg = scan
    job = generate_project_gcode(document, LaserSettings())
    _processed, allowed = process_image(gray, item.geometry)
    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    angle = np.radians(rotation)
    for move in powered:
        # Dense inspection across emitted powered segments, including their ends.
        t = np.linspace(0.00001, .99999, max(2, int(np.hypot(move.end_x - move.start_x,
                                                           move.end_y - move.start_y) * 32)))
        dx = move.start_x + (move.end_x - move.start_x) * t - 50
        dy = move.start_y + (move.end_y - move.start_y) * t - 50
        x = (dx * np.cos(angle) + dy * np.sin(angle)) * (-1 if mirror else 1)
        y = -dx * np.sin(angle) + dy * np.cos(angle)
        columns = np.clip(((x / 32 + .5) * 128).astype(int), 0, 127)
        rows = np.clip(((.5 - y / 24) * 96).astype(int), 0, 95)
        assert np.all(allowed[rows, columns])
    assert all(not move.laser_on for move in job.plan.moves if move.rapid)
    commands = [line.strip() for line in job.text.splitlines() if line.strip() and not line.startswith(";")]
    assert commands[-1] == "M5"
    assert "G21" in job.text and "G90" in job.text


def test_changed_original_and_empty_masks_fail_output(artwork):
    path, _payload, gray, document, item, settings = artwork
    item.geometry["image_edit"] = settings
    assert cv2.imwrite(str(path), 255 - gray)
    with pytest.raises(ValueError, match="Original image changed"):
        generate_project_gcode(document, LaserSettings())
    assert cv2.imwrite(str(path), gray)
    settings["regions"] = [{"operation": "subtract", "kind": "rectangle",
                            "points": [[-.5, -.5], [.5, .5]], "radius": 0}]
    settings["invert"] = True
    with pytest.raises(ValueError, match="no engravable pixels"):
        generate_project_gcode(document, LaserSettings())


@pytest.mark.parametrize("schema", [1, 2, 3, 4, 5])
def test_legacy_schema_never_silently_ignores_edits(artwork, schema):
    _path, _payload, _gray, document, item, settings = artwork
    payload = document.to_dict()
    payload["schema_version"] = schema
    assert "image_edit" not in ProjectDocument.from_dict(payload).objects[0].geometry
    payload["objects"][0]["geometry"]["image_edit"] = settings
    with pytest.raises(ValueError, match="schema 6"):
        ProjectDocument.from_dict(payload)


def test_editor_commit_undo_invalidates_prepared_job_without_regenerating(artwork, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from laser_aligner.desktop import image_editor
    from laser_aligner.desktop.main_window import E3MainWindow
    from laser_aligner.desktop.qt import require_qt
    from laser_aligner.desktop.workspace import WorkspaceView

    _QtCore, _QtGui, QtWidgets = require_qt()
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _path, _payload, _gray, document, item, settings = artwork
    workspace = WorkspaceView(document.work_area)
    workspace.set_document(document)
    workspace.select_objects([item.id])
    history = CommandStack()
    invalidations, errors = [], []
    action = SimpleNamespace(setEnabled=lambda value: None, setText=lambda value: None)
    harness = SimpleNamespace(
        document=document, history=history, workspace=workspace, show_error=errors.append,
        last_job=object(), last_job_revision=document.revision,
        actions={"undo": action, "redo": action},
        _refresh_document=lambda ids: workspace.set_document(document),
        _invalidate_generated_job=lambda: invalidations.append(document.revision),
    )
    history.add_listener(lambda stack: E3MainWindow._history_changed(harness, stack))

    class Dialog:
        create_vector = False

        def __init__(self, *_args):
            self.settings = copy.deepcopy(settings)

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(image_editor, "ImageEditorDialog", Dialog)
    E3MainWindow.edit_image_for_engraving(harness, item.id)
    assert not errors
    assert history.depth == 1 and len(invalidations) == 1
    history.undo()
    history.redo()
    assert len(invalidations) == 3
    workspace.close()
    application.processEvents()


def test_new_vectors_are_inactive_and_invalid_geometry_is_flagged(artwork):
    from laser_aligner.project.image_outline import AddOutlineCommand

    _path, _payload, _gray, document, item, _settings = artwork
    native = NativePathGeometry.from_legacy_polylines([
        {"points": [[-.3, -.3], [.3, -.3], [.3, .3], [-.3, .3]], "closed": True},
    ])
    def program():
        return [line for line in generate_project_gcode(document, LaserSettings()).text.splitlines()
                if line.strip() and not line.startswith(";")]

    before = program()
    command = AddOutlineCommand(document, item.id, native)
    CommandStack().execute(command)
    assert command.layer.power_percent == 0 and not command.layer.output_enabled
    assert command.item.transform.to_dict() == item.transform.to_dict()
    assert program() == before
    invalid = NativePathGeometry.from_legacy_polylines([
        {"points": [[-.3, -.3], [.3, .3], [-.3, .3], [.3, -.3]], "closed": True},
    ])
    with pytest.raises(ValueError):
        AddOutlineCommand(document, item.id, invalid)
    command.item.geometry = invalid.to_dict()
    document.get_layer(command.item.layer_id).output_enabled = True
    with pytest.raises(ValueError, match="Invalid image cutting outline"):
        generate_project_gcode(document, LaserSettings())


@pytest.mark.parametrize("center,rotation,spot", [(50., 0., 0.), (50.0004, 0., 0.), (50.0004, 31., .1234)])
def test_transparent_source_stays_nonengraving_under_invert_and_restore(tmp_path, center, rotation, spot):
    path = tmp_path / "transparent.png"
    rgba = np.full((24, 24, 4), 255, np.uint8)
    rgba[:, :8, 3] = 0
    assert cv2.imwrite(str(path), rgba)
    payload = read_raster_asset_payload(path)
    document = ProjectDocument.new("Transparent inversion", Bounds(0, 0, 100, 100))
    layer = document.layers[0]
    layer.mode, layer.line_interval_mm, layer.power_percent, layer.overscan_percent = LayerMode.RASTER, 1, 10, 0
    settings = default_edit(payload.identity.sha256, [24, 24])
    settings["invert"] = True
    settings["regions"] = [{"operation": "restore", "kind": "rectangle", "points": [[-.5, -.5], [.5, .5]], "radius": 0}]
    item = SceneObject(kind="image", layer_id=layer.id, transform=Transform(center, 50, 24, 24, rotation_deg=rotation),
                       geometry={"asset": str(path), "image_edit": settings})
    document.add_object(item)
    job = generate_project_gcode(document, LaserSettings(spot_offset_x_mm=spot))
    powered = [move for move in job.plan.moves if move.laser_on]
    assert powered
    angle = np.radians(rotation)
    for move in powered:
        for x, y in ((move.start_x, move.start_y), (move.end_x, move.end_y)):
            # Generated job.plan is reconstructed from serialized controller
            # coordinates. Undo the configured beam shift before source mapping.
            local_x = (x + spot - center) * np.cos(angle) + (y - 50) * np.sin(angle)
            assert local_x >= -4 - 1e-9


def test_canvas_variants_and_preview_recovery_share_the_processed_source(artwork, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from laser_aligner.desktop.qt import require_qt
    from laser_aligner.desktop.workspace import ObjectGraphicsItem, WorkspaceView

    _QtCore, _QtGui, QtWidgets = require_qt()
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    _path, payload, gray, document, item, settings = artwork
    item.geometry["image_edit"] = settings
    duplicate = item.duplicate()
    duplicate.geometry["image_edit"]["invert"] = True
    document.add_object(duplicate)
    view = WorkspaceView(document.work_area)
    view.set_document(document)
    assert view._items_by_id[item.id].raster_cache_key != view._items_by_id[duplicate.id].raster_cache_key
    for obj in (item, duplicate):
        expected, _ = process_image(gray, obj.geometry)
        preview = view._items_by_id[obj.id]._raster_image
        assert preview.pixelColor(40, 30).red() == expected[30, 40]
        assert preview.pixelColor(64, 82).red() == 255
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_PREVIEW_MAX_PIXELS", 128)
    view.set_document(document)
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_PREVIEW_MAX_PIXELS", 4_000_000)
    view.set_document(document)
    while view._raster_restore_queue:
        view._raster_restore_slice()
    assert view.refresh_raster_previews((payload.identity.path,))
    for obj in (item, duplicate):
        expected, _ = process_image(gray, obj.geometry)
        preview = view._items_by_id[obj.id]._raster_image
        assert preview.width() == 128
        assert preview.pixelColor(40, 30).red() == expected[30, 40]
    view.close()
    application.processEvents()
