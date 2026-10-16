from __future__ import annotations

import copy

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
    save_project,
)
from laser_aligner.project.raster_cleanup import background_mask, clean_background


def settings(tolerance=5, enabled=True):
    return {"background_cleanup": {"enabled": enabled, "tolerance": tolerance}}


def test_only_edge_connected_near_white_is_cleaned_without_mutating_input():
    gray = np.full((12, 12), 253, np.uint8)
    gray[3:9, 3:9] = 0
    gray[4:8, 4:8] = 253  # Enclosed highlight must not be touched.
    gray[0, 5] = 249
    original = gray.copy()
    cleaned = clean_background(gray, settings())
    assert cleaned[0, 0] == 255
    assert cleaned[0, 5] == 249
    assert cleaned[3, 3] == 0
    assert cleaned[5, 5] == 253
    assert np.array_equal(gray, original)
    assert np.array_equal(clean_background(gray, settings(enabled=False)), gray)
    assert np.array_equal(clean_background(gray, {}), gray)
    assert np.array_equal(clean_background(gray, settings(1)), gray)
    assert clean_background(gray, settings(6))[0, 5] == 255


def test_all_edges_are_seeded_and_diagonal_connections_do_not_leak():
    gray = np.zeros((7, 7), np.uint8)
    gray[0, 0] = gray[1, 1] = gray[6, 3] = gray[3, 6] = gray[3, 0] = 253
    mask = background_mask(gray, 2)
    assert mask.sum() == 4
    assert not mask[1, 1]


@pytest.mark.parametrize("value", [None, {}, {"enabled": True},
    {"enabled": 1, "tolerance": 5}, {"enabled": True, "tolerance": True},
    {"enabled": True, "tolerance": -1}, {"enabled": True, "tolerance": 65},
    {"enabled": True, "tolerance": 5.5}, {"enabled": True, "tolerance": "5"},
    {"enabled": True, "tolerance": 5, "other": 1}])
def test_invalid_settings_are_rejected_by_model_and_processing(value):
    geometry = {"asset": "unused.png", "background_cleanup": value}
    with pytest.raises(ValueError, match="background cleanup"):
        SceneObject(kind="image", geometry=geometry)
    with pytest.raises(ValueError, match="background cleanup"):
        clean_background(np.full((2, 2), 255, np.uint8), geometry)


def document_for(path):
    doc = ProjectDocument.new("Cleanup", Bounds(0, 0, 100, 100))
    layer = doc.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 1
    layer.overscan_percent = 0
    layer.power_percent = 10
    item = SceneObject(kind="image", layer_id=layer.id,
                       transform=Transform(50, 50, 16, 16), geometry={"asset": str(path)})
    doc.add_object(item)
    return doc, item


def test_project_roundtrip_undo_redo_and_original_file_integrity(tmp_path):
    asset = tmp_path / "original.png"
    assert cv2.imwrite(str(asset), np.full((16, 16), 253, np.uint8))
    original = asset.read_bytes()
    doc, item = document_for(asset)
    history = CommandStack()
    geometry = {**item.geometry, **settings()}
    revision = doc.revision
    history.execute(UpdateObjectShapeCommand(doc, item.id, item.transform, geometry))
    assert doc.revision > revision
    destination = tmp_path / "cleanup.e3laser"
    save_project(doc, destination)
    restored = load_project(destination)
    assert restored.objects[0].geometry == geometry
    assert restored.to_dict()["schema_version"] == 6
    history.undo()
    assert "background_cleanup" not in item.geometry
    history.redo()
    assert item.geometry == geometry
    assert asset.read_bytes() == original


@pytest.mark.parametrize("schema", [1, 2, 3, 4])
def test_old_projects_migrate_with_cleanup_off_and_reject_unversioned_settings(schema):
    doc, item = document_for("original.png")
    payload = doc.to_dict()
    payload["schema_version"] = schema
    restored = ProjectDocument.from_dict(payload)
    assert "background_cleanup" not in restored.objects[0].geometry
    assert restored.to_dict()["schema_version"] == 6
    payload["objects"][0]["geometry"].update(settings())
    with pytest.raises(ValueError, match="requires project schema 5"):
        ProjectDocument.from_dict(payload)


def test_cleanup_removes_dither_speckles_and_keeps_program_guards(tmp_path):
    asset = tmp_path / "light.png"
    gray = np.full((16, 16), 253, np.uint8)
    gray[4:12, 4:12] = 0
    assert cv2.imwrite(str(asset), gray)
    doc, item = document_for(asset)
    before = generate_project_gcode(doc, LaserSettings())
    item.geometry.update(settings())
    after = generate_project_gcode(doc, LaserSettings())
    assert after.cut_length_mm < before.cut_length_mm
    assert after.cut_length_mm == pytest.approx(64)
    assert after.raster_assets == before.raster_assets
    commands = [line.split(";", 1)[0].strip() for line in after.text.splitlines() if line and not line.startswith(";")]
    assert "G21" in commands and "G90" in commands and commands[-1] == "M5"
    assert commands.index("M5") < next(i for i, line in enumerate(commands) if line.startswith("G0 "))
    assert all(not move.laser_on for move in after.plan.moves if move.rapid)
    # A duplicate referencing the same file must not inherit another object's cleanup.
    duplicate = copy.deepcopy(item)
    duplicate.id += "-duplicate"
    duplicate.geometry.pop("background_cleanup")
    doc.add_object(duplicate)
    both = generate_project_gcode(doc, LaserSettings())
    assert both.cut_length_mm == pytest.approx(before.cut_length_mm + after.cut_length_mm)


def test_all_white_cleanup_is_rejected_as_empty_and_invalid_runtime_settings_fail(tmp_path):
    asset = tmp_path / "background.png"
    assert cv2.imwrite(str(asset), np.full((16, 16), 253, np.uint8))
    doc, item = document_for(asset)
    assert generate_project_gcode(doc, LaserSettings()).cut_length_mm > 0
    item.geometry.update(settings())
    with pytest.raises(ValueError, match="no engravable pixels"):
        generate_project_gcode(doc, LaserSettings())
    item.geometry.update(settings(100))
    with pytest.raises(ValueError, match="tolerance"):
        generate_project_gcode(doc, LaserSettings())


def test_cleanup_precedes_minification_and_matches_explicit_white_source(tmp_path):
    source = tmp_path / "source.png"
    expected = tmp_path / "expected.png"
    gray = np.full((96, 96), 253, np.uint8)
    gray[24:72, 24:72] = 30
    gray[32:64, 32:64] = 253
    assert cv2.imwrite(str(source), gray)
    assert cv2.imwrite(str(expected), clean_background(gray, settings()))
    signatures = []
    for path, cleanup in ((source, True), (expected, False)):
        doc, item = document_for(path)
        item.geometry.update(settings(enabled=cleanup))
        item.transform.rotation_deg = 31
        item.transform.mirror_x = True
        doc.layers[0].scan_angle_deg = 13
        job = generate_project_gcode(doc, LaserSettings())
        signatures.append([(m.start_x, m.start_y, m.end_x, m.end_y, m.laser_on)
                           for m in job.plan.moves])
    assert signatures[0] == signatures[1]


def test_alpha_is_composited_before_cleanup_and_decode_bounds_are_enforced(tmp_path):
    from laser_aligner.project.raster_asset import decode_raster_grayscale

    pixels = np.zeros((8, 8, 4), np.uint8)
    pixels[:, :, 3] = 2  # Near-transparent black composites to 253.
    path = tmp_path / "alpha.png"
    assert cv2.imwrite(str(path), pixels)
    gray, _identity = decode_raster_grayscale(path)
    assert np.all(gray == 253)
    assert np.all(clean_background(gray, settings()) == 255)
    with pytest.raises(ValueError, match="bounded raster"):
        background_mask(np.zeros((1, 8193), np.uint8), 5)
