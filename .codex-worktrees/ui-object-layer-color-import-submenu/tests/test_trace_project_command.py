from laser_aligner.project import AddObjectsCommand, ProjectDocument, SceneObject


def test_add_objects_command_is_one_undo_step():
    document = ProjectDocument.new()
    layer_id = document.active_layer_id
    items = [
        SceneObject.rectangle(layer_id, name=f"Trace {index}", center=(20 + index * 10, 30))
        for index in range(4)
    ]
    command = AddObjectsCommand(document, items, description="Create traced objects")
    command.redo()
    assert [item.id for item in document.objects] == [item.id for item in items]
    command.undo()
    assert document.objects == []
    command.redo()
    assert [item.id for item in document.objects] == [item.id for item in items]
