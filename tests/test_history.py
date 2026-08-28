import pytest

from laser_aligner.project import (
    AddObjectCommand,
    AddObjectsCommand,
    CommandStack,
    DuplicateObjectsCommand,
    NativePathGeometry,
    PathLineSegment,
    PathSubpath,
    ProjectDocument,
    ProjectFormatError,
    RemoveObjectsCommand,
    ReplaceObjectsCommand,
    SceneObject,
    Transform,
    UpdateObjectShapeCommand,
    UpdateTransformCommand,
)


def _native_geometry(segment_count: int) -> NativePathGeometry:
    return NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                tuple(
                    PathLineSegment((float(index + 1), 0.0))
                    for index in range(segment_count)
                ),
            ),
        )
    )


def _one_segment_native_path(document: ProjectDocument, name: str) -> SceneObject:
    return SceneObject.native_path(
        document.active_layer_id,
        _native_geometry(1),
        name=name,
    )


def test_add_undo_redo():
    document = ProjectDocument.new()
    item = SceneObject.rectangle(document.active_layer_id)
    stack = CommandStack()

    stack.execute(AddObjectCommand(document, item))
    assert [obj.id for obj in document.objects] == [item.id]

    assert stack.undo()
    assert document.objects == []

    assert stack.redo()
    assert [obj.id for obj in document.objects] == [item.id]


def test_native_batch_add_limit_failure_is_atomic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import laser_aligner.project.model as project_model

    monkeypatch.setattr(project_model, "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT", 1)
    document = ProjectDocument.new("Atomic native add")
    first = _one_segment_native_path(document, "First")
    second = _one_segment_native_path(document, "Second")
    stack = CommandStack()

    with pytest.raises(ValueError, match="segment project limit"):
        stack.execute(AddObjectsCommand(document, (first, second)))

    assert document.objects == []
    assert stack.depth == 0


def test_native_replacement_limit_preflights_before_removal_and_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import laser_aligner.project.model as project_model

    monkeypatch.setattr(project_model, "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT", 1)
    document = ProjectDocument.new("Atomic native replace")
    original = _one_segment_native_path(document, "Original")
    document.add_object(original)
    first = _one_segment_native_path(document, "First replacement")
    second = _one_segment_native_path(document, "Second replacement")
    stack = CommandStack()

    with pytest.raises(ValueError, match="segment project limit"):
        stack.execute(
            ReplaceObjectsCommand(document, (original.id,), (first, second))
        )
    assert [item.id for item in document.objects] == [original.id]
    assert stack.depth == 0

    stack.execute(ReplaceObjectsCommand(document, (original.id,), (first,)))
    assert [item.id for item in document.objects] == [first.id]
    assert stack.undo()
    assert [item.id for item in document.objects] == [original.id]
    assert stack.redo()
    assert [item.id for item in document.objects] == [first.id]


def test_transform_undo_redo():
    document = ProjectDocument.new()
    item = SceneObject.rectangle(document.active_layer_id, center=(10, 20))
    document.add_object(item)
    stack = CommandStack()
    target = Transform(50, 60, 20, 30, rotation_deg=15)

    stack.execute(UpdateTransformCommand(document, item.id, target))
    assert item.transform.x_mm == 50

    stack.undo()
    assert item.transform.x_mm == 10

    stack.redo()
    assert item.transform.rotation_deg == 15


def test_object_shape_command_updates_transform_and_geometry_once_per_step():
    document = ProjectDocument.new()
    item = SceneObject.rectangle(
        document.active_layer_id,
        name="Rounded label",
        center=(10, 20),
        width_mm=20,
        height_mm=12,
        corner_radius_mm=2,
    )
    item.group_id = "label-grid"
    document.add_object(item)
    stack = CommandStack()
    original_revision = document.revision
    original_id = item.id
    original_layer = item.layer_id

    stack.execute(
        UpdateObjectShapeCommand(
            document,
            item.id,
            Transform(50, 60, 30, 18, rotation_deg=15),
            {"corner_radius_mm": 4},
        )
    )

    assert document.revision == original_revision + 1
    assert item.id == original_id
    assert item.layer_id == original_layer
    assert item.group_id == "label-grid"
    assert item.transform.to_dict() == Transform(
        50, 60, 30, 18, rotation_deg=15
    ).to_dict()
    assert item.geometry == {"corner_radius_mm": 4.0}

    assert stack.undo()
    assert document.revision == original_revision + 2
    assert item.transform.to_dict() == Transform(10, 20, 20, 12).to_dict()
    assert item.geometry == {"corner_radius_mm": 2.0}
    assert item.id == original_id
    assert item.layer_id == original_layer

    assert stack.redo()
    assert document.revision == original_revision + 3
    assert item.transform.to_dict() == Transform(
        50, 60, 30, 18, rotation_deg=15
    ).to_dict()
    assert item.geometry == {"corner_radius_mm": 4.0}


def test_object_shape_command_validates_geometry_before_mutating_document():
    document = ProjectDocument.new()
    item = SceneObject.rectangle(document.active_layer_id, corner_radius_mm=2)
    document.add_object(item)
    revision = document.revision

    with pytest.raises(ProjectFormatError, match="cannot be negative"):
        UpdateObjectShapeCommand(
            document,
            item.id,
            item.transform.copy(width_mm=25),
            {"corner_radius_mm": -1},
        )

    assert document.revision == revision
    assert item.transform.width_mm == 40
    assert item.geometry == {"corner_radius_mm": 2.0}


def test_object_shape_command_native_segment_limit_rejects_execute_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import laser_aligner.project.model as project_model

    monkeypatch.setattr(project_model, "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT", 2)
    document = ProjectDocument.new("Atomic native shape execute")
    target = _one_segment_native_path(document, "Target")
    sibling = _one_segment_native_path(document, "Sibling")
    document.add_object(target)
    document.add_object(sibling)
    before = target.to_dict()
    revision = document.revision
    stack = CommandStack()

    with pytest.raises(ValueError, match="segment project limit"):
        stack.execute(
            UpdateObjectShapeCommand(
                document,
                target.id,
                target.transform.copy(width_mm=25.0),
                _native_geometry(2).to_dict(),
            )
        )

    assert target.to_dict() == before
    assert document.revision == revision
    assert stack.depth == 0


def test_failed_shape_execute_after_undo_preserves_redo_branch_and_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import laser_aligner.project.model as project_model

    monkeypatch.setattr(project_model, "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT", 2)
    document = ProjectDocument.new("Atomic native shape branch")
    target = _one_segment_native_path(document, "Target")
    document.add_object(target)
    document.add_object(_one_segment_native_path(document, "Sibling"))
    stack = CommandStack()
    moved = target.transform.copy(x_mm=15.0)
    stack.execute(UpdateTransformCommand(document, target.id, moved))
    assert stack.undo()
    before = document.to_dict()
    redo_text = stack.redo_text

    with pytest.raises(ValueError, match="segment project limit"):
        stack.execute(
            UpdateObjectShapeCommand(
                document,
                target.id,
                target.transform.copy(width_mm=25.0),
                _native_geometry(2).to_dict(),
            )
        )

    assert document.to_dict() == before
    assert stack.depth == 1
    assert not stack.can_undo
    assert stack.can_redo
    assert stack.redo_text == redo_text
    assert stack.redo()
    assert target.transform.to_dict() == moved.to_dict()


def test_object_shape_command_native_segment_limit_rejects_undo_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import laser_aligner.project.model as project_model

    monkeypatch.setattr(project_model, "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT", 3)
    document = ProjectDocument.new("Atomic native shape undo")
    target = SceneObject.native_path(
        document.active_layer_id,
        _native_geometry(2),
        name="Target",
    )
    document.add_object(target)
    document.add_object(_one_segment_native_path(document, "Sibling"))
    stack = CommandStack()
    stack.execute(
        UpdateObjectShapeCommand(
            document,
            target.id,
            target.transform.copy(width_mm=25.0),
            _native_geometry(1).to_dict(),
        )
    )
    document.add_object(_one_segment_native_path(document, "Later addition"))
    before = target.to_dict()
    revision = document.revision

    with pytest.raises(ValueError, match="segment project limit"):
        stack.undo()

    assert target.to_dict() == before
    assert document.revision == revision
    assert stack.can_undo
    assert not stack.can_redo


def test_object_shape_command_native_segment_limit_rejects_redo_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import laser_aligner.project.model as project_model

    monkeypatch.setattr(project_model, "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT", 3)
    document = ProjectDocument.new("Atomic native shape redo")
    target = _one_segment_native_path(document, "Target")
    document.add_object(target)
    document.add_object(_one_segment_native_path(document, "Sibling"))
    stack = CommandStack()
    stack.execute(
        UpdateObjectShapeCommand(
            document,
            target.id,
            target.transform.copy(width_mm=25.0),
            _native_geometry(2).to_dict(),
        )
    )
    assert stack.undo()
    document.add_object(_one_segment_native_path(document, "Later addition"))
    before = target.to_dict()
    revision = document.revision

    with pytest.raises(ValueError, match="segment project limit"):
        stack.redo()

    assert target.to_dict() == before
    assert document.revision == revision
    assert not stack.can_undo
    assert stack.can_redo


def test_new_command_after_undo_clears_redo_branch():
    document = ProjectDocument.new()
    stack = CommandStack()
    first = SceneObject.rectangle(document.active_layer_id)
    second = SceneObject.ellipse(document.active_layer_id)

    stack.execute(AddObjectCommand(document, first))
    stack.undo()
    stack.execute(AddObjectCommand(document, second))

    assert not stack.can_redo
    assert [item.id for item in document.objects] == [second.id]


def test_duplicate_and_delete_commands_are_reversible():
    document = ProjectDocument.new()
    source = SceneObject.rectangle(document.active_layer_id)
    document.add_object(source)
    stack = CommandStack()

    duplicate = DuplicateObjectsCommand(document, [source.id])
    stack.execute(duplicate)
    duplicate_id = duplicate.duplicates[0].id
    stack.execute(RemoveObjectsCommand(document, [source.id, duplicate_id]))
    assert document.objects == []

    stack.undo()
    assert {item.id for item in document.objects} == {source.id, duplicate_id}
    stack.undo()
    assert [item.id for item in document.objects] == [source.id]


def test_clean_tracking_and_external_dirty_flag():
    document = ProjectDocument.new()
    stack = CommandStack()
    stack.mark_clean()
    assert stack.is_clean

    stack.execute(AddObjectCommand(document, SceneObject.rectangle(document.active_layer_id)))
    assert not stack.is_clean

    stack.mark_clean()
    assert stack.is_clean

    stack.mark_dirty()
    assert not stack.is_clean


def test_layer_commands_are_reversible():
    from laser_aligner.project import (
        AddLayerCommand,
        OperationLayer,
        RemoveLayerCommand,
        UpdateLayerCommand,
    )

    document = ProjectDocument.new()
    stack = CommandStack()
    added = OperationLayer(name="Engrave", color="#4FC3A1", priority=1)

    stack.execute(AddLayerCommand(document, added))
    assert document.get_layer(added.id).name == "Engrave"

    item = SceneObject.rectangle(added.id)
    document.add_object(item)
    edited = OperationLayer.from_dict({**added.to_dict(), "power_percent": 25.0})
    stack.execute(UpdateLayerCommand(document, added.id, edited))
    assert document.get_layer(added.id).power_percent == 25.0

    remove = RemoveLayerCommand(document, added.id)
    stack.execute(remove)
    assert item.layer_id == remove.fallback_id

    assert stack.undo()
    assert item.layer_id == added.id
    assert document.get_layer(added.id).power_percent == 25.0
    assert stack.undo()
    assert document.get_layer(added.id).power_percent == 10.0

    # The object addition was external to this command stack, so remove it before
    # undoing the layer creation itself.
    document.remove_object(item.id)
    assert stack.undo()
    assert all(layer.id != added.id for layer in document.layers)


def test_reorder_objects_command_changes_and_restores_z_order():
    from laser_aligner.project import ReorderObjectsCommand

    document = ProjectDocument.new()
    items = [
        SceneObject.rectangle(document.active_layer_id, name=f"Item {index}")
        for index in range(3)
    ]
    for item in items:
        document.add_object(item)
    stack = CommandStack()
    order = [items[2].id, items[0].id, items[1].id]

    stack.execute(ReorderObjectsCommand(document, order))
    assert [item.id for item in document.objects] == order

    assert stack.undo()
    assert [item.id for item in document.objects] == [item.id for item in items]


def test_group_and_ungroup_commands_are_reversible():
    from laser_aligner.project import GroupObjectsCommand, UngroupObjectsCommand

    document = ProjectDocument.new()
    first = SceneObject.rectangle(document.active_layer_id)
    second = SceneObject.ellipse(document.active_layer_id)
    third = SceneObject.line(document.active_layer_id)
    for item in (first, second, third):
        document.add_object(item)
    stack = CommandStack()

    group = GroupObjectsCommand(document, [first.id, second.id])
    stack.execute(group)
    assert first.group_id == second.group_id == group.group_id
    assert third.group_id is None

    ungroup = UngroupObjectsCommand(document, [first.id])
    stack.execute(ungroup)
    assert first.group_id is None
    assert second.group_id is None

    assert stack.undo()
    assert first.group_id == second.group_id == group.group_id
    assert stack.undo()
    assert first.group_id is None
    assert second.group_id is None


def test_object_properties_command_is_reversible():
    from laser_aligner.project import UpdateObjectPropertiesCommand

    document = ProjectDocument.new()
    item = SceneObject.rectangle(document.active_layer_id, name="Before")
    document.add_object(item)
    stack = CommandStack()

    stack.execute(
        UpdateObjectPropertiesCommand(
            document,
            item.id,
            {"name": "After", "visible": False, "locked": True},
        )
    )
    assert (item.name, item.visible, item.locked) == ("After", False, True)

    assert stack.undo()
    assert (item.name, item.visible, item.locked) == ("Before", True, False)


def test_reorder_layers_updates_priority_and_is_reversible():
    from laser_aligner.project import ReorderLayersCommand

    document = ProjectDocument.new()
    second = document.add_layer(name="Second")
    third = document.add_layer(name="Third")
    original = [layer.id for layer in document.layers]
    stack = CommandStack()
    reordered = [third.id, original[0], second.id]

    stack.execute(ReorderLayersCommand(document, reordered))
    assert [layer.id for layer in document.layers] == reordered
    assert [layer.priority for layer in document.layers] == [0, 1, 2]

    assert stack.undo()
    assert [layer.id for layer in document.layers] == original
