import sqlite3
from dataclasses import replace

import pytest

from laser_aligner.materials import (
    MaterialCompatibility,
    MaterialDatabase,
    MaterialPreset,
    builtin_material_presets,
)
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


def test_material_preset_preserves_legacy_positional_argument_order() -> None:
    preset = MaterialPreset(
        "Wood",
        "Legacy positional",
        3.0,
        LayerMode.FILL,
        1234.0,
        45.0,
        2,
        0.2,
        -10.0,
        20.0,
        "Legacy notes",
        42,
    )

    assert preset.vector_power_correction == -10.0
    assert preset.raster_power_correction == 20.0
    assert preset.notes == "Legacy notes"
    assert preset.id == 42
    assert preset.scan_angle_deg == 0.0


@pytest.mark.parametrize(
    ("preset_scope", "running_scope", "expected"),
    [
        (
            ("ender-3-s1-pro", "generic-diode-10w"),
            ("ender-3-s1-pro", "generic-diode-10w"),
            MaterialCompatibility.EXACT_MACHINE_TOOL,
        ),
        (
            ("ender-3-s1-pro", "generic-diode-10w"),
            ("generic-grbl", "generic-diode-10w"),
            MaterialCompatibility.INCOMPATIBLE,
        ),
        (
            (None, "generic-diode-10w"),
            ("generic-grbl", "generic-diode-10w"),
            MaterialCompatibility.TOOL_ONLY,
        ),
        (
            (None, "generic-diode-10w"),
            ("generic-grbl", "custom-laser-head"),
            MaterialCompatibility.INCOMPATIBLE,
        ),
        (
            (None, None),
            ("generic-grbl", "custom-laser-head"),
            MaterialCompatibility.UNIVERSAL,
        ),
    ],
)
def test_material_compatibility_is_strict_and_deterministic(
    preset_scope: tuple[str | None, str | None],
    running_scope: tuple[str | None, str | None],
    expected: MaterialCompatibility,
) -> None:
    preset = MaterialPreset(
        material="Wood",
        name="Recipe",
        machine_profile_id=preset_scope[0],
        tool_head_profile_id=preset_scope[1],
    )

    compatibility = preset.compatibility(*running_scope)

    assert compatibility is expected
    assert compatibility.can_apply is (expected is not MaterialCompatibility.INCOMPATIBLE)
    assert compatibility.label


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("machine_profile_id", "Ender-3-S1-Pro"),
        ("machine_profile_id", " ender-3-s1-pro"),
        ("tool_head_profile_id", "generic diode"),
        ("tool_head_profile_id", ""),
    ],
)
def test_material_preset_rejects_noncanonical_profile_ids(field, value) -> None:
    payload = {
        "material": "Wood",
        "name": "Invalid scope",
        "tool_head_profile_id": "generic-diode-10w",
        field: value,
    }

    with pytest.raises(ValueError, match="canonical profile ID"):
        MaterialPreset(**payload)


def test_material_preset_rejects_machine_only_scope() -> None:
    with pytest.raises(ValueError, match="must also specify a tool-head"):
        MaterialPreset(
            material="Wood",
            name="Machine only",
            machine_profile_id="ender-3-s1-pro",
        )


def test_material_preset_applies_complete_settings_without_scaling_or_enabling() -> None:
    layer = OperationLayer(
        id="layer-existing",
        name="Hand-edited name",
        color="#E35D6A",
        mode=LayerMode.LINE,
        speed_mm_min=777.0,
        power_percent=7.0,
        passes=7,
        line_interval_mm=0.7,
        scan_angle_deg=-45.0,
        overscan_percent=8.0,
        vector_power_correction=-8.0,
        raster_power_correction=9.0,
        air_assist=False,
        output_enabled=False,
        visible=False,
        priority=9,
    )
    preset = MaterialPreset(
        material="Wood",
        name="Raster",
        mode=LayerMode.RASTER,
        speed_mm_min=4321.0,
        power_percent=67.0,
        passes=3,
        line_interval_mm=0.08,
        scan_angle_deg=37.0,
        overscan_percent=6.5,
        vector_power_correction=-12.0,
        raster_power_correction=23.0,
        air_assist=True,
        recommended_color="#a1b2c3",
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="generic-diode-10w",
    )

    updated = preset.apply_to_layer(
        layer,
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="generic-diode-10w",
    )

    assert updated.id == "layer-existing"
    assert updated.name == "Hand-edited name"
    assert updated.color == "#A1B2C3"
    assert updated.mode is LayerMode.RASTER
    assert updated.speed_mm_min == 4321.0
    assert updated.power_percent == 67.0
    assert updated.passes == 3
    assert updated.line_interval_mm == 0.08
    assert updated.scan_angle_deg == 37.0
    assert updated.overscan_percent == 6.5
    assert updated.vector_power_correction == -12.0
    assert updated.raster_power_correction == 23.0
    assert updated.air_assist is True
    assert updated.output_enabled is False
    assert updated.visible is False
    assert updated.priority == 9

    with pytest.raises(ValueError, match="incompatible"):
        preset.apply_to_layer(
            layer,
            machine_profile_id="generic-grbl",
            tool_head_profile_id="generic-diode-10w",
        )


def test_material_database_migrates_old_schema_losslessly_and_idempotently(
    tmp_path,
) -> None:
    path = tmp_path / "old-materials.sqlite"
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
                vector_power_correction REAL NOT NULL DEFAULT 0,
                raster_power_correction REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                UNIQUE(material, name, thickness_mm)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO material_presets (
                id, material, name, thickness_mm, mode, speed_mm_min,
                power_percent, passes, line_interval_mm,
                vector_power_correction, raster_power_correction, notes
            ) VALUES (17, 'Birch', 'Legacy', 3.0, 'fill', 987.5,
                      23.5, 4, 0.125, -7.5, 8.5, 'keep exactly')
            """
        )

    first = MaterialDatabase(path)
    migrated = first.get(17)
    first_payload = migrated
    second = MaterialDatabase(path)

    assert second.get(17) == first_payload
    assert migrated.material == "Birch"
    assert migrated.name == "Legacy"
    assert migrated.thickness_mm == 3.0
    assert migrated.mode is LayerMode.FILL
    assert migrated.speed_mm_min == 987.5
    assert migrated.power_percent == 23.5
    assert migrated.passes == 4
    assert migrated.line_interval_mm == 0.125
    assert migrated.vector_power_correction == -7.5
    assert migrated.raster_power_correction == 8.5
    assert migrated.notes == "keep exactly"
    assert migrated.scan_angle_deg == 0.0
    assert migrated.overscan_percent == 2.5
    assert migrated.air_assist is False
    assert migrated.recommended_color is None
    assert migrated.machine_profile_id is None
    assert migrated.tool_head_profile_id is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM material_presets"
        ).fetchone()[0] == 1


def test_material_database_migration_preserves_legacy_null_thickness_duplicates(
    tmp_path,
) -> None:
    path = tmp_path / "legacy-null-duplicates.sqlite"
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
        connection.executemany(
            """
            INSERT INTO material_presets (
                id, material, name, thickness_mm, mode, speed_mm_min,
                power_percent, passes, line_interval_mm, notes
            ) VALUES (?, 'Paper', 'Mark', NULL, 'line', ?, 10, 1, 0.1, ?)
            """,
            ((4, 1000, "first"), (9, 1100, "second")),
        )

    migrated = MaterialDatabase(path).list()

    assert [preset.id for preset in migrated] == [4, 9]
    assert [preset.speed_mm_min for preset in migrated] == [1000.0, 1100.0]
    assert [preset.notes for preset in migrated] == ["first", "second"]

    migrated[0].speed_mm_min = 1200.0
    updated = MaterialDatabase(path).save(migrated[0])
    assert updated.id == 4
    assert updated.speed_mm_min == 1200.0
    assert [preset.id for preset in MaterialDatabase(path).list()] == [4, 9]


@pytest.mark.parametrize(
    "schema_change",
    [
        "ALTER TABLE material_presets DROP COLUMN notes",
        "ALTER TABLE material_presets ADD COLUMN mystery TEXT",
    ],
)
def test_material_database_migration_fails_closed_for_unknown_schema(
    tmp_path,
    schema_change: str,
) -> None:
    path = tmp_path / "unsafe-schema.sqlite"
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
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        connection.execute(schema_change)

    with pytest.raises(ValueError, match="Cannot safely migrate"):
        MaterialDatabase(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == [("material_presets",), ("sqlite_sequence",)]


def test_material_database_rejects_newer_schema(tmp_path) -> None:
    path = tmp_path / "newer.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(ValueError, match="newer"):
        MaterialDatabase(path)


def test_scoped_presets_can_coexist_and_are_sorted_by_compatibility(tmp_path) -> None:
    database = MaterialDatabase(tmp_path / "scopes.sqlite")
    common = {
        "material": "Birch",
        "name": "Cut",
        "thickness_mm": 3.0,
    }
    incompatible = database.save(
        MaterialPreset(
            **common,
            machine_profile_id="generic-grbl",
            tool_head_profile_id="custom-laser-head",
        )
    )
    universal = database.save(MaterialPreset(**common))
    tool = database.save(
        MaterialPreset(**common, tool_head_profile_id="generic-diode-10w")
    )
    exact = database.save(
        MaterialPreset(
            **common,
            machine_profile_id="ender-3-s1-pro",
            tool_head_profile_id="generic-diode-10w",
        )
    )

    ordered = database.list_for_profiles(
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="generic-diode-10w",
    )

    assert [item.id for item in ordered] == [
        exact.id,
        tool.id,
        universal.id,
        incompatible.id,
    ]
    assert [
        item.compatibility("ender-3-s1-pro", "generic-diode-10w")
        for item in ordered
    ] == [
        MaterialCompatibility.EXACT_MACHINE_TOOL,
        MaterialCompatibility.TOOL_ONLY,
        MaterialCompatibility.UNIVERSAL,
        MaterialCompatibility.INCOMPATIBLE,
    ]
    with pytest.raises(sqlite3.IntegrityError):
        database.save(
            MaterialPreset(
                **common,
                machine_profile_id="ender-3-s1-pro",
                tool_head_profile_id="generic-diode-10w",
            )
        )

    exact.speed_mm_min = 765.0
    exact.notes = "Scoped update"
    updated = database.save(exact)
    assert updated.speed_mm_min == 765.0
    assert updated.notes == "Scoped update"
    assert updated.machine_profile_id == "ender-3-s1-pro"
    database.delete(incompatible.id)
    reopened = MaterialDatabase(database.path)
    assert {preset.id for preset in reopened.list()} == {
        universal.id,
        tool.id,
        exact.id,
    }


def test_builtin_seed_is_insert_only_and_remembers_edits_and_deletion(tmp_path) -> None:
    path = tmp_path / "seed.sqlite"
    database = MaterialDatabase(path)
    builtins = builtin_material_presets()

    assert database.seed(builtins) == 13
    assert database.seed(builtins) == 0
    seeded = database.list()[0]
    edited = replace(
        seeded,
        speed_mm_min=seeded.speed_mm_min + 123.0,
        builtin_key=None,
    )
    saved = database.save(edited)
    original_key = seeded.builtin_key

    assert saved.builtin_key == original_key
    assert MaterialDatabase(path).seed(builtin_material_presets()) == 0
    assert MaterialDatabase(path).get(seeded.id).speed_mm_min == edited.speed_mm_min

    MaterialDatabase(path).delete(seeded.id)
    reopened = MaterialDatabase(path)
    assert reopened.seed(builtin_material_presets()) == 0
    assert len(reopened.list()) == 12


def test_builtin_seed_never_replaces_user_row_with_same_scoped_identity(
    tmp_path,
) -> None:
    database = MaterialDatabase(tmp_path / "occupied-seed.sqlite")
    builtin = builtin_material_presets()[0]
    custom = database.save(replace(builtin, builtin_key=None, speed_mm_min=42.0))

    assert database.seed([builtin]) == 0
    assert database.get(custom.id).speed_mm_min == 42.0

    database.delete(custom.id)
    assert MaterialDatabase(database.path).seed([builtin]) == 0
    assert MaterialDatabase(database.path).list() == []
