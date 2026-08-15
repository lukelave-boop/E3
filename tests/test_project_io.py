
import json

import pytest

import laser_aligner.project.io as project_io
from laser_aligner.project import (
    Bounds,
    CoordinateSpace,
    OperationLayer,
    ProjectDocument,
    ProjectFormatError,
    SceneObject,
    Transform,
    autosave_path,
    load_project,
    save_project,
)


def test_atomic_save_and_load(tmp_path):
    document = ProjectDocument.new("Fixture")
    document.layers[0].vector_power_correction = -22.5
    document.layers[0].raster_power_correction = 17.5
    document.add_object(SceneObject.rectangle(document.active_layer_id))

    path = save_project(document, tmp_path / "fixture")
    restored = load_project(path)

    assert path.suffix == ".e3laser"
    assert restored.to_dict() == document.to_dict()


def test_save_and_load_preserves_honeycomb_local_coordinate_space(tmp_path):
    document = ProjectDocument.new(
        "Honeycomb-local",
        Bounds(0.0, 0.0, 190.0, 190.0),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )

    path = save_project(document, tmp_path / "honeycomb-local")
    raw = json.loads(path.read_text(encoding="utf-8"))
    restored = load_project(path)

    assert raw["schema_version"] == 2
    assert raw["coordinate_space"] == "honeycomb_local"
    assert restored.coordinate_space is CoordinateSpace.HONEYCOMB_LOCAL
    assert restored.work_area == Bounds(0.0, 0.0, 190.0, 190.0)


def test_load_schema_one_migrates_to_explicit_machine_coordinates(tmp_path):
    payload = ProjectDocument.new("Legacy machine project").to_dict()
    payload["schema_version"] = 1
    payload.pop("coordinate_space")
    source = tmp_path / "legacy.e3laser"
    source.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_project(source)
    migrated = save_project(restored, tmp_path / "migrated.e3laser")
    migrated_payload = json.loads(migrated.read_text(encoding="utf-8"))

    assert restored.coordinate_space is CoordinateSpace.MACHINE
    assert migrated_payload["schema_version"] == 2
    assert migrated_payload["coordinate_space"] == "machine"


def test_load_schema_two_requires_coordinate_space(tmp_path):
    payload = ProjectDocument.new().to_dict()
    payload.pop("coordinate_space")
    source = tmp_path / "missing-coordinate-space.e3laser"
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ProjectFormatError, match="coordinate_space"):
        load_project(source)


def test_project_file_without_power_correction_loads_zero_defaults(tmp_path):
    document = ProjectDocument.new("Legacy correction defaults")
    payload = document.to_dict()
    payload["layers"][0].pop("vector_power_correction")
    payload["layers"][0].pop("raster_power_correction")
    path = tmp_path / "legacy.e3laser"
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_project(path)

    assert restored.layers[0].vector_power_correction == 0
    assert restored.layers[0].raster_power_correction == 0


@pytest.mark.parametrize("field", ["passes", "priority"])
@pytest.mark.parametrize("value", [True, 1.5, "1"])
def test_operation_layer_rejects_coerced_integer_fields(field, value):
    payload = OperationLayer().to_dict()
    payload[field] = value

    with pytest.raises(ProjectFormatError):
        OperationLayer.from_dict(payload)


@pytest.mark.parametrize("value", [True, 1.5, "1"])
def test_project_document_rejects_coerced_revision(value):
    payload = ProjectDocument.new().to_dict()
    payload["revision"] = value

    with pytest.raises(ProjectFormatError, match="revision must be an integer"):
        ProjectDocument.from_dict(payload)


def test_project_document_rejects_negative_revision():
    payload = ProjectDocument.new().to_dict()
    payload["revision"] = -1

    with pytest.raises(ProjectFormatError, match="cannot be negative"):
        ProjectDocument.from_dict(payload)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("project", "id", True),
        ("project", "name", {}),
        ("project", "created_at", 1),
        ("project", "modified_at", []),
        ("layer", "id", True),
        ("layer", "name", {}),
        ("layer", "color", 1),
        ("layer", "mode", []),
        ("object", "id", True),
        ("object", "name", {}),
        ("object", "kind", 1),
        ("object", "layer_id", []),
        ("object", "group_id", False),
    ],
)
def test_project_document_rejects_coerced_persisted_strings(
    section,
    field,
    value,
):
    document = ProjectDocument.new()
    document.add_object(SceneObject.rectangle(document.active_layer_id))
    payload = document.to_dict()
    target = payload
    if section == "layer":
        target = payload["layers"][0]
    elif section == "object":
        target = payload["objects"][0]
    target[field] = value

    with pytest.raises(ProjectFormatError, match="JSON string"):
        ProjectDocument.from_dict(payload)


@pytest.mark.parametrize(
    ("kind", "geometry"),
    [
        ("text", {"text": {}, "font_family": "Sans Serif"}),
        ("text", {"text": "Label", "font_family": []}),
        ("image", {"asset": {}}),
    ],
)
def test_project_document_rejects_coerced_geometry_strings(kind, geometry):
    document = ProjectDocument.new()
    document.add_object(SceneObject.rectangle(document.active_layer_id))
    payload = document.to_dict()
    payload["objects"][0]["kind"] = kind
    payload["objects"][0]["geometry"] = geometry

    with pytest.raises(ProjectFormatError, match="JSON string"):
        ProjectDocument.from_dict(payload)


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (OperationLayer, "air_assist"),
        (OperationLayer, "output_enabled"),
        (OperationLayer, "visible"),
        (Transform, "mirror_x"),
        (Transform, "mirror_y"),
    ],
)
@pytest.mark.parametrize("value", [0, 1, "false"])
def test_project_models_reject_coerced_boolean_fields(factory, field, value):
    with pytest.raises(ProjectFormatError, match="JSON boolean"):
        factory(**{field: value})


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("work_area", "x_min"),
        ("transform", "x_mm"),
        ("transform", "width_mm"),
        ("layer", "speed_mm_min"),
        ("layer", "power_percent"),
        ("layer", "line_interval_mm"),
    ],
)
@pytest.mark.parametrize("value", [True, "1.0"])
def test_project_document_rejects_coerced_numeric_fields(section, field, value):
    document = ProjectDocument.new()
    document.add_object(SceneObject.rectangle(document.active_layer_id))
    payload = document.to_dict()
    if section == "work_area":
        payload["work_area"][field] = value
    elif section == "transform":
        payload["objects"][0]["transform"][field] = value
    else:
        payload["layers"][0][field] = value

    with pytest.raises(ProjectFormatError, match="finite number"):
        ProjectDocument.from_dict(payload)


def test_save_revalidates_mutated_document_before_replacing_destination(tmp_path):
    document = ProjectDocument.new("Mutated")
    destination = save_project(document, tmp_path / "mutated.e3laser")
    original = destination.read_bytes()
    document.layers[0].speed_mm_min = float("nan")

    with pytest.raises(ProjectFormatError, match="finite number"):
        save_project(document, destination)

    assert destination.read_bytes() == original
    assert not destination.with_suffix(".e3laser.bak").exists()


def test_save_rejects_nonfinite_metadata_without_publishing_file(tmp_path):
    document = ProjectDocument.new("Invalid metadata")
    document.metadata["score"] = float("nan")

    with pytest.raises(ProjectFormatError, match="non-JSON data"):
        save_project(document, tmp_path / "invalid-metadata.e3laser")

    assert not (tmp_path / "invalid-metadata.e3laser").exists()


def test_load_rejects_oversized_project_before_parsing(tmp_path):
    path = tmp_path / "oversized.e3laser"
    with path.open("wb") as handle:
        handle.seek(project_io.MAX_PROJECT_BYTES)
        handle.write(b"x")

    with pytest.raises(ProjectFormatError, match="file limit"):
        load_project(path)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_load_rejects_nonstandard_json_constants(tmp_path, constant):
    path = tmp_path / "constant.e3laser"
    path.write_text(
        '{"schema_version":1,"metadata":{"value":' + constant + "}}",
        encoding="utf-8",
    )

    with pytest.raises(ProjectFormatError, match="unsupported constant"):
        load_project(path)


def test_load_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "duplicate.e3laser"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(ProjectFormatError, match="duplicate key 'schema_version'"):
        load_project(path)


def test_load_wraps_invalid_utf8_as_project_format_error(tmp_path):
    path = tmp_path / "invalid-utf8.e3laser"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(ProjectFormatError, match="Invalid JSON"):
        load_project(path)


def test_load_wraps_excessive_json_nesting_as_project_format_error(tmp_path):
    path = tmp_path / "deep.e3laser"
    payload = json.dumps(ProjectDocument.new().to_dict())
    payload = payload.replace(
        '"metadata": {}',
        '"metadata": {"deep": ' + "[" * 2_000 + "0" + "]" * 2_000 + "}",
    )
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ProjectFormatError, match="nested too deeply"):
        load_project(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("work_area", [], "project.work_area must be a JSON object"),
        ("layers", {}, "project.layers must be a JSON array"),
        ("objects", {}, "project.objects must be a JSON array"),
        ("metadata", [], "project.metadata must be a JSON object"),
    ],
)
def test_project_document_rejects_malformed_top_level_sections(field, value, message):
    payload = ProjectDocument.new().to_dict()
    payload[field] = value

    with pytest.raises(ProjectFormatError, match=message):
        ProjectDocument.from_dict(payload)


@pytest.mark.parametrize("field", ["transform", "geometry", "metadata"])
def test_project_document_rejects_non_object_scene_sections(field):
    document = ProjectDocument.new()
    document.add_object(SceneObject.rectangle(document.active_layer_id))
    payload = document.to_dict()
    payload["objects"][0][field] = []

    with pytest.raises(ProjectFormatError, match="must be a JSON object"):
        ProjectDocument.from_dict(payload)


def test_save_creates_backup(tmp_path):
    document = ProjectDocument.new("Backup")
    path = save_project(document, tmp_path / "backup.e3laser")
    original = path.read_text()

    document.name = "Changed"
    save_project(document, path)

    backup = path.with_suffix(".e3laser.bak")
    assert backup.exists()
    assert backup.read_text() == original


def test_autosave_path_is_stable_and_outside_project(tmp_path):
    document = ProjectDocument.new("My project")

    first = autosave_path(document, autosave_root=tmp_path)
    second = autosave_path(document, autosave_root=tmp_path)

    assert first == second
    assert first.parent == tmp_path
    assert first.name.endswith(".autosave.e3laser")


def test_default_autosave_uses_shared_platform_data_root(tmp_path, monkeypatch):
    import laser_aligner.project.io as project_io

    monkeypatch.setattr(project_io, "default_user_data_dir", lambda: tmp_path / "user-data")
    monkeypatch.setattr(project_io, "legacy_user_data_dir", lambda: tmp_path / "legacy")
    document = ProjectDocument.new("Portable")

    path = autosave_path(document)

    assert path.parent == tmp_path / "user-data" / "backups"


def test_default_autosave_migrates_legacy_recovery_without_deleting_it(
    tmp_path,
    monkeypatch,
):
    import os

    import laser_aligner.project.io as project_io

    preferred_root = tmp_path / "platform-data"
    legacy_root = tmp_path / "legacy-data"
    monkeypatch.setattr(project_io, "default_user_data_dir", lambda: preferred_root)
    monkeypatch.setattr(project_io, "legacy_user_data_dir", lambda: legacy_root)
    document = ProjectDocument.new("Migrated recovery")
    filename = project_io._autosave_filename(document, None)
    legacy = legacy_root / "backups" / filename
    legacy.parent.mkdir(parents=True)
    payload = b'{"legacy":true}\n'
    legacy.write_bytes(payload)
    # Windows exposes file times at 100 ns precision.
    timestamp_ns = 1_700_000_000_123_456_700
    os.utime(legacy, ns=(timestamp_ns, timestamp_ns))

    migrated = autosave_path(document)

    assert migrated == (preferred_root / "backups" / filename).resolve()
    assert migrated.read_bytes() == payload
    assert migrated.stat().st_mtime_ns == timestamp_ns
    assert legacy.read_bytes() == payload

    project_io.clear_autosave(document)
    assert not migrated.exists()
    assert not legacy.exists()


def test_legacy_autosave_migration_never_overwrites_concurrent_native_save(
    tmp_path,
    monkeypatch,
):
    import laser_aligner.project.io as project_io

    preferred_root = tmp_path / "platform-data"
    legacy_root = tmp_path / "legacy-data"
    monkeypatch.setattr(project_io, "default_user_data_dir", lambda: preferred_root)
    monkeypatch.setattr(project_io, "legacy_user_data_dir", lambda: legacy_root)
    document = ProjectDocument.new("Migration race")
    filename = project_io._autosave_filename(document, None)
    legacy = legacy_root / "backups" / filename
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    preferred = preferred_root / "backups" / filename

    def concurrent_save(path, _data, **_kwargs):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"new native autosave")
        return False

    monkeypatch.setattr(
        project_io,
        "atomic_write_bytes_if_absent",
        concurrent_save,
    )

    selected = autosave_path(document)

    assert selected == preferred.resolve()
    assert selected.read_bytes() == b"new native autosave"
    assert legacy.read_bytes() == b"legacy"


def test_legacy_autosave_migration_cannot_backdate_concurrent_native_replace(
    tmp_path,
    monkeypatch,
):
    import os

    import laser_aligner.project.io as project_io

    preferred_root = tmp_path / "platform-data"
    legacy_root = tmp_path / "legacy-data"
    monkeypatch.setattr(project_io, "default_user_data_dir", lambda: preferred_root)
    monkeypatch.setattr(project_io, "legacy_user_data_dir", lambda: legacy_root)
    document = ProjectDocument.new("Timestamp race")
    filename = project_io._autosave_filename(document, None)
    legacy = legacy_root / "backups" / filename
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    legacy_time = 1_600_000_000_000_000_000
    native_time = 1_800_000_000_000_000_000
    os.utime(legacy, ns=(legacy_time, legacy_time))
    preferred = preferred_root / "backups" / filename
    publish = project_io.atomic_write_bytes_if_absent

    def publish_then_replace(path, data, **kwargs):
        installed = publish(path, data, **kwargs)
        replacement = path.with_suffix(".new")
        replacement.write_bytes(b"new native autosave")
        os.utime(replacement, ns=(native_time, native_time))
        os.replace(replacement, path)
        return installed

    monkeypatch.setattr(
        project_io,
        "atomic_write_bytes_if_absent",
        publish_then_replace,
    )

    selected = autosave_path(document)

    assert selected == preferred.resolve()
    assert selected.read_bytes() == b"new native autosave"
    assert selected.stat().st_mtime_ns == native_time


def test_autosave_newer_detection_and_clear(tmp_path):
    import os
    import time

    from laser_aligner.project import (
        autosave_is_newer,
        clear_autosave,
        save_autosave,
    )

    document = ProjectDocument.new("Recover")
    project = save_project(document, tmp_path / "recover.e3laser")
    old = time.time() - 10
    os.utime(project, (old, old))
    autosave = save_autosave(
        document,
        project_path=project,
        autosave_root=tmp_path / "autosaves",
    )

    assert autosave_is_newer(
        document,
        project,
        autosave_root=tmp_path / "autosaves",
    )

    clear_autosave(
        document,
        project_path=project,
        autosave_root=tmp_path / "autosaves",
    )
    assert not autosave.exists()
