import copy

import pytest

from laser_aligner.materials.layer_profiles import LayerProfileStore, load_profile_command
from laser_aligner.project import OperationLayer, ProjectDocument, SceneObject

SCOPE = ("machine", "tool")


def profile(*layers):
    return {"scope": list(SCOPE), "layers": [layer.to_dict() for layer in layers]}


def test_profile_library_round_trip_and_delete(tmp_path):
    store = LayerProfileStore(tmp_path / "profiles.json")
    layers = [OperationLayer(power_percent=17, passes=3, air_assist=True)]
    store.save("Paper", SCOPE, layers)
    assert LayerProfileStore(store.path).read()["Paper"] == profile(*layers)
    with pytest.raises(ValueError, match="already exists"):
        store.save("Paper", SCOPE, layers)
    store.delete("Paper")
    assert store.read() == {}


def test_invalid_library_is_not_overwritten(tmp_path):
    store = LayerProfileStore(tmp_path / "profiles.json")
    store.path.write_text('{"schema_version": 999}', encoding="utf-8")
    original = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.save("Paper", SCOPE, [OperationLayer()])
    assert store.path.read_bytes() == original


def test_load_remaps_by_color_and_undo_restores_objects_and_layers():
    old = OperationLayer(color="#123456", power_percent=10)
    obj = SceneObject.rectangle(old.id)
    document = ProjectDocument(layers=[old], objects=[obj])
    saved = OperationLayer(color=old.color, power_percent=45, output_enabled=False)
    extra = OperationLayer(color="#ABCDEF", passes=4)
    command = load_profile_command(document, profile(saved, extra), SCOPE)
    command.redo()
    assert [layer.to_dict() for layer in document.layers] == [
        saved.to_dict(), extra.to_dict(),
    ]
    assert obj.layer_id == saved.id
    assert document.revision == 1
    document.validate()
    command.undo()
    assert document.layers[0].to_dict() == old.to_dict()
    assert obj.layer_id == old.id
    command.redo()
    assert obj.layer_id == saved.id
    document.validate()


@pytest.mark.parametrize("failure", ["missing", "ambiguous", "scope", "power", "empty"])
def test_load_rejects_invalid_profile_without_changing_project(failure):
    current = OperationLayer(color="#123456")
    document = ProjectDocument(
        layers=[current], objects=[SceneObject.rectangle(current.id)],
    )
    raw = profile(OperationLayer(color=current.color))
    if failure == "missing":
        raw["layers"][0]["color"] = "#ABCDEF"
    elif failure == "ambiguous":
        raw["layers"].append(OperationLayer(color=current.color).to_dict())
    elif failure == "scope":
        raw["scope"] = ["another-machine", "tool"]
    elif failure == "power":
        raw["layers"][0]["power_percent"] = 101
    else:
        raw["layers"] = []
    before = copy.deepcopy(document.to_dict())
    with pytest.raises(ValueError):
        load_profile_command(document, raw, SCOPE)
    assert document.to_dict() == before


def test_same_layer_identity_survives_color_change():
    layer = OperationLayer()
    document = ProjectDocument(layers=[layer], objects=[SceneObject.rectangle(layer.id)])
    raw = profile(layer)
    raw["layers"][0]["color"] = "#123456"
    load_profile_command(document, raw, SCOPE).redo()
    assert document.objects[0].layer_id == layer.id
    assert document.layers[0].color == "#123456"


def test_load_rejects_merging_two_used_layers():
    layers = [OperationLayer(color="#123456"), OperationLayer(color="#123456")]
    document = ProjectDocument(
        layers=layers, objects=[SceneObject.rectangle(layer.id) for layer in layers],
    )
    with pytest.raises(ValueError, match="combine"):
        load_profile_command(document, profile(OperationLayer(color="#123456")), SCOPE)


def test_failed_publication_preserves_saved_library(tmp_path, monkeypatch):
    store = LayerProfileStore(tmp_path / "profiles.json")
    store.save("Original", SCOPE, [OperationLayer()])
    before = store.path.read_bytes()

    def fail_replace(*args):
        raise OSError("Simulated publication failure")

    monkeypatch.setattr("laser_aligner.storage.os.replace", fail_replace)
    with pytest.raises(OSError):
        store.save("New", SCOPE, [OperationLayer()])
    assert store.path.read_bytes() == before


def test_loaded_settings_are_independent_of_library_payload():
    document = ProjectDocument.new()
    raw = profile(OperationLayer(power_percent=20))
    command = load_profile_command(document, raw, SCOPE)
    raw["layers"][0]["power_percent"] = 99
    command.redo()
    assert document.layers[0].power_percent == 20
    command.undo()
    command.redo()
    assert document.layers[0].power_percent == 20


def test_overwrite_replaces_whole_set_preserving_other_profiles(tmp_path):
    store = LayerProfileStore(tmp_path / "profiles.json")
    store.save("Paper", SCOPE, [OperationLayer()])
    store.save("Other", SCOPE, [OperationLayer()])
    other = store.read()["Other"]
    replacement = [OperationLayer(power_percent=23, passes=3), OperationLayer()]
    store.save("Paper", SCOPE, replacement, overwrite=True)
    assert store.read()["Paper"] == profile(*replacement)
    assert store.read()["Other"] == other


@pytest.mark.parametrize("failure", ["missing", "scope", "invalid", "publication"])
def test_overwrite_rejection_preserves_library(tmp_path, monkeypatch, failure):
    store = LayerProfileStore(tmp_path / "profiles.json")
    store.save("Paper", SCOPE, [OperationLayer()])
    before = store.path.read_bytes()
    name = "Missing" if failure == "missing" else "Paper"
    scope = ("other", "tool") if failure == "scope" else SCOPE
    layers = [] if failure == "invalid" else [OperationLayer(power_percent=22)]
    if failure == "publication":
        def fail_replace(*args):
            raise OSError("Publication failed")
        monkeypatch.setattr("laser_aligner.storage.os.replace", fail_replace)
    with pytest.raises((ValueError, OSError)):
        store.save(name, scope, layers, overwrite=True)
    assert store.path.read_bytes() == before
