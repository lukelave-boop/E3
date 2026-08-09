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
            notes="Crisp surface mark",
        )
    )

    assert saved.id is not None
    assert database.get(saved.id).material == "Birch plywood"
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
    )

    updated = preset.apply_to_layer(layer)

    assert updated.id == layer.id
    assert updated.name == layer.name
    assert updated.speed_mm_min == 800
    assert updated.power_percent == 35
    assert updated.passes == 2


def test_invalid_material_values_are_rejected():
    with pytest.raises(ValueError):
        MaterialPreset(material="Wood", name="Bad", power_percent=101)
