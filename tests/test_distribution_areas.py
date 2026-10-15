import pytest

from laser_aligner.project import (
    Bounds,
    CommandStack,
    ProjectDocument,
    SceneObject,
    UpdateTransformsCommand,
    distributed_transforms,
    mark_stock_boundary,
)


def make_document():
    document = ProjectDocument.new(work_area=Bounds(10, -20, 130, 100))
    items = [
        SceneObject.rectangle(document.active_layer_id, center=center, width_mm=size, height_mm=size)
        for center, size in [((20, 0), 10), ((50, 25), 20), ((95, 70), 30)]
    ]
    for item in items:
        document.add_object(item)
    return document, items


@pytest.mark.parametrize("horizontal", [True, False])
def test_bed_equal_edge_gaps_including_both_margins_and_undo(horizontal):
    document, items = make_document()
    before = [item.transform.to_dict() for item in items]
    revision = document.revision
    transforms = distributed_transforms(document, [item.id for item in items], horizontal=horizontal, target="bed")
    assert document.revision == revision  # Calculation is read-only.
    assert [item.transform.to_dict() for item in items] == before
    history = CommandStack()
    history.execute(UpdateTransformsCommand(document, transforms))
    boxes = [item.bounds() for item in items]
    starts = [box.x_min if horizontal else box.y_min for box in boxes]
    ends = [box.x_max if horizontal else box.y_max for box in boxes]
    low, high = (10, 130) if horizontal else (-20, 100)
    gaps = [starts[0] - low, starts[1] - ends[0], starts[2] - ends[1], high - ends[2]]
    assert gaps == pytest.approx([15] * 4)
    assert [item.transform.y_mm if horizontal else item.transform.x_mm for item in items] == [
        payload["y_mm" if horizontal else "x_mm"] for payload in before
    ]
    assert document.revision > revision
    assert history.depth == 1
    assert history.undo()
    assert [item.transform.to_dict() for item in items] == before
    assert history.redo()
    assert [item.bounds() for item in items] == boxes


@pytest.mark.parametrize("horizontal", [True, False])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_chosen_rectangle_is_fixed_and_excluded_even_when_selected(horizontal, rotation):
    document, items = make_document()
    boundary = SceneObject.rectangle(document.active_layer_id, center=(70, 40), width_mm=120, height_mm=120)
    boundary.transform.rotation_deg = rotation
    boundary.locked = True
    document.add_object(boundary)
    original = boundary.to_dict()
    transforms = distributed_transforms(
        document, [boundary.id, items[0].id, items[0].id],
        horizontal=horizontal, target="rectangle", boundary_id=boundary.id,
    )
    assert set(transforms) == {items[0].id}
    assert (transforms[items[0].id].x_mm if horizontal else transforms[items[0].id].y_mm) == pytest.approx(
        70 if horizontal else 40
    )
    assert boundary.to_dict() == original


@pytest.mark.parametrize("horizontal", [True, False])
def test_area_contains_rotated_objects_without_resizing_or_mirroring(horizontal):
    document, items = make_document()
    item = items[0]
    item.transform = item.transform.copy(x_mm=-200, y_mm=300, rotation_deg=30, mirror_x=True)
    before = item.transform.to_dict()
    result = distributed_transforms(document, [item.id], horizontal=horizontal, target="bed")[item.id]
    box = result.bounds()
    assert document.work_area.expanded(1e-9).contains(box.x_min, box.y_min)
    assert document.work_area.expanded(1e-9).contains(box.x_max, box.y_max)
    for key in ("width_mm", "height_mm", "rotation_deg", "mirror_x", "mirror_y"):
        assert result.to_dict()[key] == before[key]


@pytest.mark.parametrize("horizontal", [True, False])
def test_tied_centers_use_document_order_not_selection_order(horizontal):
    document, items = make_document()
    for item in items:
        item.transform = item.transform.copy(x_mm=30, y_mm=30)
    ids = [item.id for item in items]
    assert distributed_transforms(document, ids, horizontal=horizontal, target="bed") == distributed_transforms(
        document, reversed(ids), horizontal=horizontal, target="bed"
    )


@pytest.mark.parametrize("horizontal", [True, False])
@pytest.mark.parametrize("case", ["total", "cross_axis", "empty", "locked", "stock", "zero_area"])
def test_invalid_area_layout_rejects_without_mutating_document(horizontal, case):
    document, items = make_document()
    ids = [item.id for item in items]
    if case == "total":
        document.work_area = Bounds(0, 0, 50, 50)
    elif case == "cross_axis":
        items[0].transform = items[0].transform.copy(**{"height_mm" if horizontal else "width_mm": 150})
    elif case == "empty":
        ids = []
    elif case == "locked":
        items[0].locked = True
    elif case == "stock":
        mark_stock_boundary(items[0])
    else:
        document.work_area = Bounds(0, 0, 0, 100)
    before = document.to_dict()
    with pytest.raises(ValueError):
        distributed_transforms(document, ids, horizontal=horizontal, target="bed")
    assert document.to_dict() == before


@pytest.mark.parametrize("case", ["missing", "rotated", "rounded", "ellipse", "only_boundary"])
def test_rectangle_reference_rejections(case):
    document, items = make_document()
    boundary = items.pop()
    if case == "rotated":
        boundary.transform.rotation_deg = 45
    elif case == "rounded":
        boundary.geometry["corner_radius_mm"] = 3
    elif case == "ellipse":
        boundary.kind = SceneObject.ellipse(document.active_layer_id).kind
    ids = [item.id for item in items] if case != "only_boundary" else [boundary.id]
    with pytest.raises(ValueError):
        distributed_transforms(
            document, ids, horizontal=True, target="rectangle",
            boundary_id=boundary.id if case != "missing" else None,
        )


@pytest.mark.parametrize("horizontal", [True, False])
def test_exact_fit_allows_zero_gaps(horizontal):
    document, items = make_document()
    document.work_area = Bounds(0, 0, 60, 60)
    transforms = distributed_transforms(document, [item.id for item in items], horizontal=horizontal, target="bed")
    assert [transforms[item.id].x_mm if horizontal else transforms[item.id].y_mm for item in items] == [5, 20, 45]


def test_selection_span_keeps_legacy_centers_and_requires_three_objects():
    document, items = make_document()
    assert distributed_transforms(document, [items[0].id, items[1].id], horizontal=True) == {}
    result = distributed_transforms(document, [item.id for item in items], horizontal=False)
    assert [result[item.id].y_mm for item in items] == [0, 35, 70]
    with pytest.raises(ValueError, match="Choose"):
        distributed_transforms(document, [item.id for item in items], horizontal=True, target="unknown")
