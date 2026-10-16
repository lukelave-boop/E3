"""Forward reading of frozen files emitted by each historical schema writer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from laser_aligner.project import ProjectFormatError, load_project, save_project
from laser_aligner.project.io import save_autosave
from laser_aligner.project.model import PROJECT_SCHEMA_VERSION
from laser_aligner.project.path_geometry import PathFillRule, flatten_native_path

FIXTURES = Path(__file__).parent / "fixtures" / "project_history"
SCHEMAS = (1, 2, 3, 4, 5)


@pytest.fixture(params=SCHEMAS, ids=lambda schema: f"schema-{schema}")
def historical_project(request, tmp_path):
    source = tmp_path / f"schema-{request.param}.e3laser"
    source.write_bytes((FIXTURES / source.name).read_bytes())
    raw = json.loads(source.read_text(encoding="utf-8"))
    assert raw["schema_version"] == request.param
    return source, raw


def test_historical_load_preserves_identity_layers_and_object_state(historical_project):
    source, raw = historical_project
    before = source.read_bytes()
    document = load_project(source)
    loaded = document.to_dict()

    assert loaded["schema_version"] == PROJECT_SCHEMA_VERSION
    for key in ("id", "name", "work_area", "created_at", "modified_at", "metadata", "revision"):
        assert loaded[key] == raw[key], key
    assert loaded["coordinate_space"] == raw.get("coordinate_space", "machine")
    assert len(loaded["layers"]) == len(raw["layers"]) == 3
    for actual, original in zip(loaded["layers"], raw["layers"], strict=True):
        for key, value in original.items():
            assert actual[key] == value, (original["id"], key)
        assert actual["vector_power_correction"] == original.get("vector_power_correction", 0.0)
        assert actual["raster_power_correction"] == original.get("raster_power_correction", 0.0)

    assert len(loaded["objects"]) == len(raw["objects"]) == 7
    assert {item["kind"] for item in loaded["objects"]} == {
        "rectangle", "ellipse", "line", "polygon", "path", "text", "image",
    }
    for actual, original in zip(loaded["objects"], raw["objects"], strict=True):
        for key, value in original.items():
            if key != "geometry":
                assert actual[key] == value, (original["id"], key)
    assert source.read_bytes() == before
    assert list(source.parent.iterdir()) == [source]


def test_historical_geometry_retains_paths_curves_and_raster_settings(historical_project):
    source, raw = historical_project
    document = load_project(source)
    for original in raw["objects"]:
        item = document.get_object(original["id"])
        geometry = original["geometry"]
        if "polylines" in geometry:
            # Compare actual drawn line sequences, including explicit closure,
            # rather than constructing expected data with today's migration code.
            path = item.path_geometry()
            assert path.fill_rule is PathFillRule.EVENODD
            assert "polylines" not in item.geometry
            assert [subpath.closed for subpath in path.subpaths] == [
                line["closed"] for line in geometry["polylines"]
            ]
            expected = tuple(
                tuple(tuple(point) for point in line["points"])
                for line in geometry["polylines"]
            )
            assert flatten_native_path(path, tolerance_mm=0.001) == expected
        else:
            # Native cubic controls, fill rule, Unicode text, asset path,
            # cleanup settings and all other shape data remain exact.
            assert item.geometry == geometry
        assert "image_edit" not in item.geometry
        assert "image_outline" not in item.metadata

    image = document.get_object("object-image")
    assert image.geometry["asset"] == "assets/caf\u00e9 logo.png"
    if raw["schema_version"] < 5:
        assert "background_cleanup" not in image.geometry
    else:
        assert image.geometry["background_cleanup"] == {"enabled": True, "tolerance": 9}


def test_historical_explicit_save_preserves_original_backup_and_reopens(historical_project):
    source, raw = historical_project
    original_bytes = source.read_bytes()
    document = load_project(source)
    expected = document.to_dict()

    assert save_project(document, source) == source
    saved = json.loads(source.read_text(encoding="utf-8"))
    assert saved["schema_version"] == PROJECT_SCHEMA_VERSION
    assert load_project(source).to_dict() == expected
    backup = source.with_suffix(".e3laser.bak")
    assert backup.read_bytes() == original_bytes
    assert json.loads(backup.read_text(encoding="utf-8"))["schema_version"] == raw["schema_version"]
    assert load_project(backup).to_dict() == expected


def test_historical_autosave_roundtrips_without_touching_source(historical_project, tmp_path):
    source, _raw = historical_project
    original_bytes = source.read_bytes()
    document = load_project(source)
    document.name += " recovered edit"
    document.get_object("object-text").geometry["text"] += " \u2713"
    document.touch()
    expected = document.to_dict()

    autosave = save_autosave(document, project_path=source, autosave_root=tmp_path / "recovery")

    assert load_project(autosave).to_dict() == expected
    assert source.read_bytes() == original_bytes
    assert not source.with_suffix(".e3laser.bak").exists()
    assert load_project(source).name != document.name


def test_historical_schema_rejects_unversioned_editor_features(historical_project):
    source, raw = historical_project
    image = next(item for item in raw["objects"] if item["kind"] == "image")
    image["geometry"]["image_edit"] = {}
    source.write_text(json.dumps(raw), encoding="utf-8")
    original_bytes = source.read_bytes()

    with pytest.raises(ProjectFormatError, match="Image edits require project schema 6"):
        load_project(source)

    assert source.read_bytes() == original_bytes
