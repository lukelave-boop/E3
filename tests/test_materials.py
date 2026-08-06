import pytest

from laser_aligner.materials import MaterialDatabase, MaterialPreset
from laser_aligner.project import LayerMode, OperationLayer


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
