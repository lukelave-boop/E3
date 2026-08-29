import pytest

from laser_aligner.project import (
    Alignment,
    CommandStack,
    ProjectDocument,
    SceneObject,
    UpdateTransformsCommand,
    aligned_transforms,
    distributed_transforms,
)


def make_document():
    document = ProjectDocument.new()
    first = SceneObject.rectangle(
        document.active_layer_id,
        center=(20, 30),
        width_mm=10,
        height_mm=20,
    )
    second = SceneObject.rectangle(
        document.active_layer_id,
        center=(60, 50),
        width_mm=20,
        height_mm=10,
    )
    third = SceneObject.rectangle(
        document.active_layer_id,
        center=(130, 90),
        width_mm=30,
        height_mm=30,
    )
    for item in (first, second, third):
        document.add_object(item)
    return document, first, second, third


@pytest.mark.parametrize(
    ("alignment", "attribute"),
    [
        (Alignment.LEFT, "x_min"),
        (Alignment.CENTER_X, "center_x"),
        (Alignment.RIGHT, "x_max"),
        (Alignment.BOTTOM, "y_min"),
        (Alignment.CENTER_Y, "center_y"),
        (Alignment.TOP, "y_max"),
    ],
)
def test_alignment_produces_a_common_reference(alignment, attribute):
    document, first, second, third = make_document()
    transforms = aligned_transforms(document, [first.id, second.id, third.id], alignment)

    for object_id, transform in transforms.items():
        document.get_object(object_id).transform = transform

    boxes = [item.bounds() for item in (first, second, third)]
    if attribute == "center_x":
        values = [box.center[0] for box in boxes]
    elif attribute == "center_y":
        values = [box.center[1] for box in boxes]
    else:
        values = [getattr(box, attribute) for box in boxes]
    assert values == pytest.approx([values[0]] * len(values))


def test_distribution_spaces_centers_evenly_and_preserves_endpoints():
    document, first, second, third = make_document()
    transforms = distributed_transforms(
        document,
        [third.id, first.id, second.id],
        horizontal=True,
    )
    original_first = first.bounds().center[0]
    original_last = third.bounds().center[0]

    for object_id, transform in transforms.items():
        document.get_object(object_id).transform = transform

    centers = sorted(item.bounds().center[0] for item in (first, second, third))
    assert centers[0] == pytest.approx(original_first)
    assert centers[-1] == pytest.approx(original_last)
    assert centers[1] - centers[0] == pytest.approx(centers[2] - centers[1])


def test_alignment_can_be_undone_as_one_command():
    document, first, second, third = make_document()
    before = {item.id: item.transform.to_dict() for item in (first, second, third)}
    stack = CommandStack()
    transforms = aligned_transforms(
        document,
        [first.id, second.id, third.id],
        Alignment.TOP,
    )

    stack.execute(UpdateTransformsCommand(document, transforms, description="Align top"))
    assert len({round(item.bounds().y_max, 8) for item in (first, second, third)}) == 1

    assert stack.undo()
    assert {item.id: item.transform.to_dict() for item in (first, second, third)} == before


def test_alignment_and_distribution_require_multiple_objects():
    document, first, _, _ = make_document()
    assert aligned_transforms(document, [first.id], Alignment.LEFT) == {}
    assert distributed_transforms(document, [first.id], horizontal=True) == {}
