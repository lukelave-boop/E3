import sqlite3

import pytest

from laser_aligner.materials import MaterialDatabase, MaterialPreset
from laser_aligner.project import LayerMode, OperationLayer


def test_material_database_uses_shared_platform_user_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_root = tmp_path / "platform-data"
    monkeypatch.setattr(
        "laser_aligner.materials.database.default_user_data_dir",
        lambda: user_root,
    )
    monkeypatch.setattr(
        "laser_aligner.materials.database.legacy_user_data_dir",
        lambda: tmp_path / "legacy-data",
    )

    database = MaterialDatabase()

    assert database.path == (user_root / "materials.sqlite").resolve()


def test_material_database_migrates_legacy_presets_without_overwriting_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import laser_aligner.materials.database as database_module

    preferred_root = tmp_path / "platform-data"
    legacy_root = tmp_path / "legacy-data"
    legacy_path = legacy_root / "materials.sqlite"
    legacy = MaterialDatabase(legacy_path)
    legacy.save(
        MaterialPreset(
            material="Birch plywood",
            name="Legacy preset",
            speed_mm_min=900.0,
            power_percent=22.0,
        )
    )
    original = legacy_path.read_bytes()
    monkeypatch.setattr(database_module, "default_user_data_dir", lambda: preferred_root)
    monkeypatch.setattr(database_module, "legacy_user_data_dir", lambda: legacy_root)

    migrated = MaterialDatabase()

    assert migrated.path == (preferred_root / "materials.sqlite").resolve()
    assert [preset.name for preset in migrated.list()] == ["Legacy preset"]
    assert legacy_path.read_bytes() == original

    migrated.save(MaterialPreset(material="Acrylic", name="New location"))
    reopened = MaterialDatabase()
    assert {preset.name for preset in reopened.list()} == {
        "Legacy preset",
        "New location",
    }
    assert legacy_path.read_bytes() == original


def test_material_database_adds_zero_correction_to_legacy_schema(tmp_path) -> None:
    path = tmp_path / "legacy-schema.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE material_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material TEXT NOT NULL,
                name TEXT NOT NULL,
                thickness_mm REAL,
                mode TEXT NOT NULL,
                speed_mm_min REAL NOT NULL,
                power_percent REAL NOT NULL,
                passes INTEGER NOT NULL,
                line_interval_mm REAL NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                UNIQUE(material, name, thickness_mm)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO material_presets (
                material, name, mode, speed_mm_min, power_percent,
                passes, line_interval_mm, notes
            ) VALUES ('Cardstock', 'Legacy', 'line', 1000, 10, 1, 0.1, '')
            """
        )

    preset = MaterialDatabase(path).list()[0]

    assert preset.vector_power_correction == 0
    assert preset.raster_power_correction == 0

def test_material_database_falls_back_when_legacy_migration_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import laser_aligner.materials.database as database_module

    preferred_root = tmp_path / "platform-data"
    legacy_root = tmp_path / "legacy-data"
    legacy_path = legacy_root / "materials.sqlite"
    legacy = MaterialDatabase(legacy_path)
    legacy.save(MaterialPreset(material="Cardstock", name="Legacy fallback"))
    monkeypatch.setattr(database_module, "default_user_data_dir", lambda: preferred_root)
    monkeypatch.setattr(database_module, "legacy_user_data_dir", lambda: legacy_root)
    monkeypatch.setattr(database_module, "_migrate_database", lambda *_args: False)

    fallback = MaterialDatabase()

    assert fallback.path == legacy_path.resolve()
    assert [preset.name for preset in fallback.list()] == ["Legacy fallback"]
    assert not (preferred_root / "materials.sqlite").exists()


def test_material_database_migration_does_not_clobber_native_database(
    tmp_path,
) -> None:
    import laser_aligner.materials.database as database_module

    legacy_path = tmp_path / "legacy.sqlite"
    native_path = tmp_path / "native.sqlite"
    legacy = MaterialDatabase(legacy_path)
    legacy.save(MaterialPreset(material="Legacy", name="Do not copy"))
    native = MaterialDatabase(native_path)
    native.save(MaterialPreset(material="Native", name="Keep me"))

    assert database_module._migrate_database(legacy_path, native_path)

    assert [preset.name for preset in MaterialDatabase(native_path).list()] == [
        "Keep me"
    ]


def test_material_database_crud_and_search(tmp_path):
    database = MaterialDatabase(tmp_path / "materials.sqlite")
    saved = database.save(
        MaterialPreset(
            material="Birch plywood",
            name="Light mark",
            thickness_mm=3.0,
            speed_mm_min=2500,
            power_percent=12,
            vector_power_correction=-12.5,
            raster_power_correction=27.5,
            notes="Crisp surface mark",
        )
    )

    assert saved.id is not None
    assert database.get(saved.id).material == "Birch plywood"
    assert database.get(saved.id).vector_power_correction == -12.5
    assert database.get(saved.id).raster_power_correction == 27.5
    assert [item.id for item in database.list("crisp")] == [saved.id]

    saved.power_percent = 18
    updated = database.save(saved)
    assert updated.power_percent == 18

    database.delete(saved.id)
    assert database.list() == []
    with pytest.raises(KeyError):
        database.get(saved.id)


def test_material_preset_applies_to_layer_without_changing_identity():
    layer = OperationLayer(name="Original", color="#E35D6A")
    preset = MaterialPreset(
        material="Cardstock",
        name="Cut",
        mode=LayerMode.LINE,
        speed_mm_min=800,
        power_percent=35,
        passes=2,
        vector_power_correction=-25,
        raster_power_correction=40,
    )

    updated = preset.apply_to_layer(layer)

    assert updated.id == layer.id
    assert updated.name == layer.name
    assert updated.speed_mm_min == 800
    assert updated.power_percent == 35
    assert updated.passes == 2
    assert updated.vector_power_correction == -25
    assert updated.raster_power_correction == 40


def test_invalid_material_values_are_rejected():
    with pytest.raises(ValueError):
        MaterialPreset(material="Wood", name="Bad", power_percent=101)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("thickness_mm", float("nan")),
        ("thickness_mm", float("inf")),
        ("speed_mm_min", float("nan")),
        ("speed_mm_min", float("inf")),
        ("power_percent", float("nan")),
        ("vector_power_correction", float("nan")),
        ("raster_power_correction", float("inf")),
        ("line_interval_mm", float("nan")),
        ("line_interval_mm", float("inf")),
        ("passes", True),
        ("passes", 1.5),
        ("passes", "1"),
    ],
)
def test_material_preset_rejects_nonfinite_or_coerced_numbers(field, value):
    payload = {"material": "Wood", "name": "Invalid", field: value}

    with pytest.raises(ValueError):
        MaterialPreset(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("material", True),
        ("name", 1),
        ("mode", {}),
        ("notes", []),
    ],
)
def test_material_preset_rejects_coerced_strings(field, value):
    payload = {"material": "Wood", "name": "Invalid", field: value}

    with pytest.raises(ValueError, match="string"):
        MaterialPreset(**payload)


def test_material_database_revalidates_mutated_nonfinite_preset(tmp_path):
    database = MaterialDatabase(tmp_path / "materials.sqlite")
    preset = MaterialPreset(material="Wood", name="Mutated")
    preset.speed_mm_min = float("nan")

    with pytest.raises(ValueError):
        database.save(preset)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("speed_mm_min", float("inf")),
        ("line_interval_mm", float("inf")),
        ("passes", 1.5),
        ("material", sqlite3.Binary(b"Wood")),
        ("name", sqlite3.Binary(b"Invalid")),
        ("notes", sqlite3.Binary(b"notes")),
    ],
)
def test_material_database_rejects_malformed_persisted_rows(tmp_path, column, value):
    database = MaterialDatabase(tmp_path / "malformed.sqlite")
    columns = {
        "material": "Wood",
        "name": f"Invalid {column}",
        "thickness_mm": 1.0,
        "mode": "line",
        "speed_mm_min": 1000.0,
        "power_percent": 10.0,
        "passes": 1,
        "line_interval_mm": 0.1,
        "notes": "",
    }
    columns[column] = value
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            INSERT INTO material_presets (
                material, name, thickness_mm, mode, speed_mm_min,
                power_percent, passes, line_interval_mm, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(columns.values()),
        )

    with pytest.raises(ValueError):
        database.list()
