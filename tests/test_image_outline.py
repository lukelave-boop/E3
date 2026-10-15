"""Image outline snapshots retain alignment without inheriting output authority."""
from __future__ import annotations

import copy

import pytest

from laser_aligner.project.history import (
    CommandStack,
    DuplicateObjectsCommand,
    RemoveLayerCommand,
    RemoveObjectsCommand,
    ReplaceObjectsCommand,
    UpdateObjectShapeCommand,
    UpdateTransformCommand,
    UpdateTransformsCommand,
)
from laser_aligner.project.image_outline import (
    AddOutlineCommand,
    DetachOutlineCommand,
    validate_outline_links,
)
from laser_aligner.project.io import load_project, save_project
from laser_aligner.project.model import (
    LayerMode,
    ObjectKind,
    ProjectDocument,
    SceneObject,
    Transform,
)
from laser_aligner.project.path_geometry import (
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathLineSegment,
    PathSubpath,
    flatten_native_path,
)


def outline() -> NativePathGeometry:
    return NativePathGeometry((PathSubpath(
        (-0.4, -0.4), (
            PathLineSegment((0.4, -0.4)),
            PathCubicSegment((0.45, -0.1), (0.45, 0.1), (0.4, 0.4)),
            PathLineSegment((-0.4, 0.4)),
            PathLineSegment((-0.4, -0.4)),
        ), closed=True,
    ),))


@pytest.fixture
def linked():
    document = ProjectDocument.new()
    source = SceneObject(
        kind=ObjectKind.IMAGE, name="Barrel", layer_id=document.active_layer_id,
        transform=Transform(70, 80, 48, 60), geometry={"asset": "barrel.png"},
    )
    document.add_object(source)
    history = CommandStack()
    command = AddOutlineCommand(document, source.id, outline())
    history.execute(command)
    return document, source, command.item, history


def test_outline_creation_is_one_inert_undoable_operation(linked):
    document, source, vector, history = linked
    layer = document.get_layer(vector.layer_id)
    assert layer.mode is LayerMode.LINE
    assert layer.power_percent == 0
    assert layer.output_enabled is False
    assert vector not in document.visible_output_objects()
    assert vector.geometry == outline().to_dict()
    assert vector.transform.to_dict() == source.transform.to_dict()
    assert history.depth == 1
    history.undo()
    assert document.objects == [source]
    assert len(document.layers) == 1
    history.redo()
    validate_outline_links(document.objects)
    assert document.get_object(vector.id) is vector


def test_removing_outline_layer_cannot_implicitly_enable_output(linked):
    document, _source, vector, _history = linked
    before = document.to_dict()
    with pytest.raises(ValueError, match="explicitly"):
        RemoveLayerCommand(document, vector.layer_id)
    with pytest.raises(ValueError, match="explicitly"):
        document.remove_layer(vector.layer_id)
    assert document.to_dict() == before


def test_direct_model_transform_obeys_linkage(linked):
    document, source, vector, _history = linked
    transform = source.transform.copy(rotation_deg=180, mirror_x=True, width_mm=90)
    previous = document.update_transform(source.id, transform)
    assert previous.rotation_deg == 0
    assert source.transform.to_dict() == vector.transform.to_dict() == transform.to_dict()
    with pytest.raises(ValueError, match="detach"):
        document.update_transform(vector.id, transform.copy(x_mm=99))
    document.validate()


def test_explicit_outline_layer_reassignment_and_undo_are_allowed(linked):
    document, _source, vector, history = linked
    original_layer = vector.layer_id
    history.execute(RemoveLayerCommand(
        document, original_layer, reassign_to=document.active_layer_id,
    ))
    assert vector.layer_id == document.active_layer_id
    history.undo()
    assert vector.layer_id == original_layer
    assert document.get_layer(vector.layer_id).output_enabled is False


def test_removing_outline_layer_into_disabled_fallback_is_allowed(linked):
    document, _source, vector, history = linked
    document.get_layer(document.active_layer_id).output_enabled = False
    history.execute(RemoveLayerCommand(document, vector.layer_id))
    assert vector not in document.visible_output_objects()


def test_failed_removal_preserves_links_and_revision(linked):
    document, source, _vector, _history = linked
    before = document.to_dict()
    command = RemoveObjectsCommand(document, [source.id, "missing-object"])
    with pytest.raises(KeyError):
        command.redo()
    assert document.to_dict() == before


@pytest.mark.parametrize("command_type", [UpdateTransformCommand, UpdateTransformsCommand])
def test_source_transforms_move_linked_snapshot_with_undo(linked, command_type):
    document, source, vector, history = linked
    before = source.transform.to_dict()
    geometry = copy.deepcopy(vector.geometry)
    transformed = Transform(102, 118, 96, 90, rotation_deg=37, mirror_x=True, mirror_y=True)
    command = (
        command_type(document, source.id, transformed)
        if command_type is UpdateTransformCommand
        else command_type(document, {source.id: transformed})
    )
    history.execute(command)
    assert vector.transform.to_dict() == source.transform.to_dict() == transformed.to_dict()
    assert vector.geometry == geometry
    affine = PathAffineTransform.from_components(
        translate_x=transformed.x_mm, translate_y=transformed.y_mm,
        scale_x=-transformed.width_mm, scale_y=-transformed.height_mm,
        rotation_deg=transformed.rotation_deg,
    )
    assert flatten_native_path(vector.path_geometry(), 0.01, transform=affine) == flatten_native_path(
        outline(), 0.01, transform=affine,
    )
    history.undo()
    assert vector.transform.to_dict() == source.transform.to_dict() == before
    history.redo()
    validate_outline_links(document.objects)


def test_shape_edit_can_transform_source_but_never_regenerates_path(linked):
    document, source, vector, history = linked
    frozen = copy.deepcopy(vector.geometry)
    new_geometry = dict(source.geometry)
    new_geometry["background_cleanup"] = {"enabled": True, "tolerance": 32}
    history.execute(UpdateObjectShapeCommand(
        document, source.id, source.transform.copy(width_mm=90, rotation_deg=63), new_geometry,
    ))
    assert vector.geometry == frozen
    assert vector.transform.to_dict() == source.transform.to_dict()
    history.undo()
    assert "background_cleanup" not in source.geometry
    assert vector.geometry == frozen
    validate_outline_links(document.objects)


def test_independent_linked_transform_rejected_until_explicit_detach(linked):
    document, source, vector, history = linked
    after = vector.transform.copy(x_mm=123)
    for construct in (
        lambda: UpdateTransformCommand(document, vector.id, after),
        lambda: UpdateTransformsCommand(document, {vector.id: after}),
        lambda: UpdateObjectShapeCommand(document, vector.id, after, vector.geometry),
        lambda: UpdateTransformsCommand(document, {source.id: source.transform, vector.id: after}),
    ):
        with pytest.raises(ValueError, match="detach"):
            construct()
    history.execute(DetachOutlineCommand(document, vector.id))
    history.execute(UpdateTransformCommand(document, vector.id, after))
    assert vector.transform.x_mm == 123
    assert source.transform.x_mm == 70
    history.undo()
    history.undo()
    assert vector.metadata["image_outline"]["linked"] is True
    validate_outline_links(document.objects)


@pytest.mark.parametrize("replace", [False, True])
def test_removing_source_detaches_snapshot_and_undo_restores_link(linked, replace):
    document, source, vector, history = linked
    before = vector.to_dict()
    replacement = SceneObject.rectangle(document.active_layer_id)
    command = (
        ReplaceObjectsCommand(document, [source.id], [replacement])
        if replace else RemoveObjectsCommand(document, [source.id])
    )
    history.execute(command)
    assert vector.metadata["image_outline"]["linked"] is False
    assert vector.geometry == before["geometry"]
    document.validate()
    history.undo()
    assert vector.to_dict() == before
    document.validate()
    history.redo()
    document.validate()


def test_copying_pair_relinks_copies_and_path_only_copy_detaches(linked):
    document, source, vector, history = linked
    pair = DuplicateObjectsCommand(document, [vector.id, source.id])
    history.execute(pair)
    vector_copy, source_copy = pair.duplicates
    assert vector_copy.metadata["image_outline"]["source_id"] == source_copy.id
    assert vector_copy.metadata["image_outline"]["linked"] is True
    document.validate()
    history.undo()
    history.redo()
    document.validate()
    single = DuplicateObjectsCommand(document, [vector.id])
    history.execute(single)
    assert single.duplicates[0].metadata["image_outline"]["linked"] is False
    document.validate()


def test_linkage_persistence_and_transforms_after_reopen(linked, tmp_path):
    document, source, vector, _history = linked
    destination = save_project(document, tmp_path / "outline.e3laser")
    restored = load_project(destination)
    assert restored.get_object(vector.id).geometry == vector.geometry
    assert restored.get_object(vector.id).metadata == vector.metadata
    transformed = source.transform.copy(rotation_deg=129, width_mm=24, mirror_y=True)
    UpdateTransformCommand(restored, source.id, transformed).redo()
    assert restored.get_object(vector.id).transform.to_dict() == transformed.to_dict()
    restored.validate()


@pytest.mark.parametrize("damage", ["missing", "misaligned", "wrong_kind", "bad_boolean"])
def test_invalid_persisted_links_rejected(linked, damage):
    document, source, vector, _history = linked
    payload = document.to_dict()
    raw_vector = next(item for item in payload["objects"] if item["id"] == vector.id)
    if damage == "missing":
        raw_vector["metadata"]["image_outline"]["source_id"] = "missing"
    elif damage == "misaligned":
        raw_vector["transform"]["x_mm"] += 1
    elif damage == "wrong_kind":
        raw_vector["metadata"]["image_outline"]["source_id"] = vector.id
    else:
        raw_vector["metadata"]["image_outline"]["linked"] = 1
    with pytest.raises(ValueError, match="[Ll]ink|outline"):
        ProjectDocument.from_dict(payload)


def test_open_or_self_crossing_outline_rejected_without_mutation(linked):
    document, source, _vector, _history = linked
    before = document.to_dict()
    shapes = [
        NativePathGeometry((PathSubpath((0, 0), (PathLineSegment((1, 1)),)),)),
        NativePathGeometry.from_legacy_polylines([{
            "points": [[-0.4, -0.4], [0.4, 0.4], [-0.4, 0.4], [0.4, -0.4]],
            "closed": True,
        }]),
    ]
    for geometry in shapes:
        with pytest.raises(ValueError):
            AddOutlineCommand(document, source.id, geometry)
        assert document.to_dict() == before
