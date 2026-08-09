
import pytest

from laser_aligner.project import (
    Bounds,
    LayerMode,
    OperationLayer,
    ProjectDocument,
    ProjectFormatError,
    SceneObject,
    Transform,
)


def test_document_has_a_default_layer():
    document = ProjectDocument.new("Test", Bounds(15, 15, 205, 205))

    assert len(document.layers) == 1
    assert document.active_layer_id == document.layers[0].id
    assert document.work_area.width == 190


def test_transform_rotated_bounds_are_correct():
    transform = Transform(100, 100, 40, 20, rotation_deg=90)

    bounds = transform.bounds()

    assert bounds.width == pytest.approx(20)
    assert bounds.height == pytest.approx(40)
    assert bounds.center == pytest.approx((100, 100))


def test_document_round_trip_preserves_layers_objects_and_svg():
    document = ProjectDocument.new("Round trip")
    second = document.add_layer(name="Engrave", color="#4FC3A1", mode=LayerMode.LINE)
    rectangle = SceneObject.rectangle(
        second.id,
        center=(110, 110),
        width_mm=76.2,
        height_mm=50.8,
        corner_radius_mm=6.35,
    )
    document.add_object(rectangle)
    imported = SceneObject.path(
        second.id,
        [{"points": [[0, 0], [10, 0], [10, 5], [0, 0]], "closed": True}],
        center=(80, 70),
        source_name="sample.svg",
        source_svg="<svg/>",
    )
    document.add_object(imported)

    restored = ProjectDocument.from_dict(document.to_dict())

    assert restored.to_dict() == document.to_dict()
    assert restored.get_object(imported.id).metadata["source_svg"] == "<svg/>"


def test_path_is_normalized_around_its_center():
    document = ProjectDocument.new()
    item = SceneObject.path(
        document.active_layer_id,
        [{"points": [[10, 20], [30, 20], [30, 60]], "closed": False}],
        center=(110, 110),
    )

    assert item.transform.width_mm == pytest.approx(20)
    assert item.transform.height_mm == pytest.approx(40)
    points = item.geometry["polylines"][0]["points"]
    assert min(point[0] for point in points) == pytest.approx(-0.5)
    assert max(point[0] for point in points) == pytest.approx(0.5)
    assert min(point[1] for point in points) == pytest.approx(-0.5)
    assert max(point[1] for point in points) == pytest.approx(0.5)


def test_remove_layer_reassigns_objects():
    document = ProjectDocument.new()
    second = document.add_layer(name="Second")
    item = SceneObject.rectangle(second.id)
    document.add_object(item)

    document.remove_layer(second.id)

    assert item.layer_id == document.active_layer_id


def test_duplicate_object_gets_new_identity_and_offset():
    document = ProjectDocument.new()
    item = SceneObject.rectangle(document.active_layer_id, center=(10, 20))
    document.add_object(item)

    duplicate = document.duplicate_objects([item.id], offset_mm=(5, -2))[0]

    assert duplicate.id != item.id
    assert duplicate.transform.x_mm == 15
    assert duplicate.transform.y_mm == 18


def test_layer_power_scales_to_controller_range():
    layer = OperationLayer(power_percent=12.5)

    assert layer.controller_power(1000) == 125
    assert layer.controller_power(255) == 32


def test_invalid_layer_color_is_rejected():
    with pytest.raises(ProjectFormatError):
        OperationLayer(color="red")


@pytest.mark.parametrize("radius", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_rectangle_corner_radius_is_rejected(radius: float) -> None:
    item = SceneObject.rectangle("layer-test").to_dict()
    item["geometry"]["corner_radius_mm"] = radius

    with pytest.raises(
        ProjectFormatError,
        match="rectangle.corner_radius_mm must be a finite number",
    ):
        SceneObject.from_dict(item)


def test_project_string_booleans_are_rejected() -> None:
    with pytest.raises(ProjectFormatError, match="layer.output_enabled must be a JSON boolean"):
        OperationLayer.from_dict({"output_enabled": "false"})
    with pytest.raises(ProjectFormatError, match="transform.mirror_x must be a JSON boolean"):
        Transform.from_dict({"mirror_x": "false"})

    document = ProjectDocument.new()
    item = SceneObject.rectangle(document.active_layer_id)
    payload = item.to_dict()
    payload["visible"] = "false"
    with pytest.raises(ProjectFormatError, match="object.visible must be a JSON boolean"):
        SceneObject.from_dict(payload)

    path = SceneObject.path(
        document.active_layer_id,
        [{"points": [[0, 0], [1, 1]], "closed": False}],
    ).to_dict()
    path["geometry"]["polylines"][0]["closed"] = "false"
    with pytest.raises(ProjectFormatError, match="path.closed must be a JSON boolean"):
        SceneObject.from_dict(path)


@pytest.mark.parametrize("schema", [999, True, 1.0, 1.5, "1"])
def test_schema_mismatch_is_rejected(schema: object):
    payload = ProjectDocument.new().to_dict()
    payload["schema_version"] = schema

    with pytest.raises(ProjectFormatError):
        ProjectDocument.from_dict(payload)


def test_duplicate_group_gets_a_new_group_identity():
    document = ProjectDocument.new()
    first = SceneObject.rectangle(document.active_layer_id)
    second = SceneObject.ellipse(document.active_layer_id)
    first.group_id = second.group_id = "group-original"
    document.add_object(first)
    document.add_object(second)

    duplicates = document.duplicate_objects([first.id, second.id])

    assert duplicates[0].group_id == duplicates[1].group_id
    assert duplicates[0].group_id != "group-original"
    assert duplicates[0].group_id is not None


def test_group_identity_round_trips_through_project_json():
    document = ProjectDocument.new()
    first = SceneObject.rectangle(document.active_layer_id)
    second = SceneObject.ellipse(document.active_layer_id)
    first.group_id = second.group_id = "group-test"
    document.add_object(first)
    document.add_object(second)

    restored = ProjectDocument.from_dict(document.to_dict())

    assert [item.group_id for item in restored.objects] == ["group-test", "group-test"]
