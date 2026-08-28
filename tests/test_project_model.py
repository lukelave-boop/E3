
import pytest

from laser_aligner.materials import builtin_material_presets
from laser_aligner.project import (
    Bounds,
    CoordinateSpace,
    LayerMode,
    NativePathGeometry,
    OperationLayer,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    ProjectDocument,
    ProjectFormatError,
    SceneObject,
    Transform,
    default_operation_layers,
)


def test_document_has_a_default_layer():
    document = ProjectDocument.new("Test", Bounds(15, 15, 205, 205))

    assert len(document.layers) == 1
    assert document.active_layer_id == document.layers[0].id
    assert document.work_area.width == 190


def test_e3_default_profiles_match_operator_workbook():
    layers = default_operation_layers()

    assert len(layers) == 13
    assert [layer.priority for layer in layers] == list(range(13))
    assert [layer.mode for layer in layers[:7]] == [LayerMode.LINE] * 7
    assert [layer.mode for layer in layers[7:]] == [LayerMode.RASTER] * 6
    assert (
        layers[0].name,
        layers[0].speed_mm_min,
        layers[0].power_percent,
        layers[0].passes,
        layers[0].color,
    ) == ("Copy / Printer Paper — CUT", 1500.0, 100.0, 1, "#ED23D2")
    assert (
        layers[10].name,
        layers[10].speed_mm_min,
        layers[10].power_percent,
        layers[10].line_interval_mm,
        layers[10].overscan_percent,
    ) == ("Opaque Black Acrylic — RASTER", 5000.0, 25.0, 0.08, 4.0)
    assert all(layer.vector_power_correction == 0 for layer in layers)
    assert all(layer.raster_power_correction == 0 for layer in layers)


def test_curated_recipe_source_preserves_every_default_layer_field() -> None:
    layers = default_operation_layers()
    expected = [
        ("Copy / Printer Paper — CUT", "#ED23D2", LayerMode.LINE, 1500.0, 100.0, 1, 0.10, 0.0, 2.5),
        ("3 mm Basswood / Poplar Ply — CUT", "#F02C3D", LayerMode.LINE, 300.0, 100.0, 5, 0.10, 0.0, 2.5),
        ("3 mm Birch Plywood — CUT", "#FF8A18", LayerMode.LINE, 220.0, 100.0, 7, 0.10, 0.0, 2.5),
        ("3 mm MDF — CUT", "#E5DA19", LayerMode.LINE, 180.0, 100.0, 8, 0.10, 0.0, 2.5),
        ("2 mm Opaque Black Acrylic — CUT", "#2DD12D", LayerMode.LINE, 180.0, 100.0, 8, 0.10, 0.0, 2.5),
        ("2 mm Vegetable-Tanned Leather — CUT", "#185CFF", LayerMode.LINE, 450.0, 100.0, 4, 0.10, 0.0, 2.5),
        ("1.5 mm Cardboard / Chipboard — CUT", "#A982E3", LayerMode.LINE, 900.0, 85.0, 2, 0.10, 0.0, 2.5),
        ("Basswood / Poplar Ply — RASTER", "#F02C3D", LayerMode.RASTER, 4000.0, 35.0, 1, 0.10, 0.0, 3.0),
        ("Birch Plywood — RASTER", "#FF8A18", LayerMode.RASTER, 3500.0, 32.0, 1, 0.10, 0.0, 3.0),
        ("MDF — RASTER", "#E5DA19", LayerMode.RASTER, 4500.0, 22.0, 1, 0.10, 0.0, 3.0),
        ("Opaque Black Acrylic — RASTER", "#2DD12D", LayerMode.RASTER, 5000.0, 25.0, 1, 0.08, 0.0, 4.0),
        ("Vegetable-Tanned Leather — RASTER", "#185CFF", LayerMode.RASTER, 4500.0, 18.0, 1, 0.10, 0.0, 3.0),
        ("Copy / Printer Paper — RASTER", "#ED23D2", LayerMode.RASTER, 6000.0, 12.0, 1, 0.10, 0.0, 3.0),
    ]

    assert [
        (
            layer.name,
            layer.color,
            layer.mode,
            layer.speed_mm_min,
            layer.power_percent,
            layer.passes,
            layer.line_interval_mm,
            layer.scan_angle_deg,
            layer.overscan_percent,
        )
        for layer in layers
    ] == expected
    assert [layer.priority for layer in layers] == list(range(13))
    assert all(layer.vector_power_correction == 0.0 for layer in layers)
    assert all(layer.raster_power_correction == 0.0 for layer in layers)
    assert all(layer.air_assist is False for layer in layers)
    assert all(layer.output_enabled is True for layer in layers)
    assert all(layer.visible is True for layer in layers)


def test_builtin_recipes_derive_all_controlled_values_from_default_layers() -> None:
    layers = default_operation_layers()
    recipes = builtin_material_presets()

    assert len(recipes) == len(layers) == 13
    assert len({recipe.builtin_key for recipe in recipes}) == 13
    for layer, recipe in zip(layers, recipes, strict=True):
        assert recipe.name == (
            "Cut" if layer.mode is LayerMode.LINE else "Raster"
        )
        assert recipe.mode is layer.mode
        assert recipe.speed_mm_min == layer.speed_mm_min
        assert recipe.power_percent == layer.power_percent
        assert recipe.passes == layer.passes
        assert recipe.line_interval_mm == layer.line_interval_mm
        assert recipe.scan_angle_deg == layer.scan_angle_deg
        assert recipe.overscan_percent == layer.overscan_percent
        assert recipe.vector_power_correction == layer.vector_power_correction
        assert recipe.raster_power_correction == layer.raster_power_correction
        assert recipe.air_assist is layer.air_assist
        assert recipe.recommended_color == layer.color
        assert recipe.machine_profile_id == "ender-3-s1-pro"
        assert recipe.tool_head_profile_id == "generic-diode-10w"


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


def test_honeycomb_coordinate_space_round_trips_explicitly():
    document = ProjectDocument.new(
        "Honeycomb-local",
        Bounds(0, 0, 190, 190),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )

    payload = document.to_dict()
    restored = ProjectDocument.from_dict(payload)

    assert payload["schema_version"] == 3
    assert payload["coordinate_space"] == "honeycomb_local"
    assert restored.coordinate_space is CoordinateSpace.HONEYCOMB_LOCAL


def test_schema_one_project_migrates_as_machine_coordinates():
    payload = ProjectDocument.new().to_dict()
    payload["schema_version"] = 1
    payload.pop("coordinate_space")

    restored = ProjectDocument.from_dict(payload)

    assert restored.coordinate_space is CoordinateSpace.MACHINE
    assert restored.to_dict()["schema_version"] == 3


def test_schema_one_project_cannot_claim_honeycomb_coordinates():
    payload = ProjectDocument.new().to_dict()
    payload["schema_version"] = 1
    payload["coordinate_space"] = "honeycomb_local"

    restored = ProjectDocument.from_dict(payload)

    assert restored.coordinate_space is CoordinateSpace.MACHINE


def test_schema_two_requires_explicit_coordinate_space():
    payload = ProjectDocument.new().to_dict()
    payload["schema_version"] = 2
    payload.pop("coordinate_space")

    with pytest.raises(ProjectFormatError, match="coordinate_space"):
        ProjectDocument.from_dict(payload)


def test_invalid_coordinate_space_is_rejected():
    payload = ProjectDocument.new().to_dict()
    payload["coordinate_space"] = "looks_like_a_square"

    with pytest.raises(ProjectFormatError, match="CoordinateSpace"):
        ProjectDocument.from_dict(payload)


def test_path_is_normalized_around_its_center():
    document = ProjectDocument.new()
    item = SceneObject.path(
        document.active_layer_id,
        [{"points": [[10, 20], [30, 20], [30, 60]], "closed": False}],
        center=(110, 110),
    )

    assert item.transform.width_mm == pytest.approx(20)
    assert item.transform.height_mm == pytest.approx(40)
    subpath = item.path_geometry().subpaths[0]
    points = [subpath.start, *(segment.to for segment in subpath.segments)]
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


def test_layer_power_correction_defaults_and_round_trip() -> None:
    legacy = OperationLayer().to_dict()
    legacy.pop("vector_power_correction")
    legacy.pop("raster_power_correction")
    restored = OperationLayer.from_dict(legacy)
    assert restored.vector_power_correction == 0
    assert restored.raster_power_correction == 0

    layer = OperationLayer(
        vector_power_correction=-37.5,
        raster_power_correction=62.5,
    )
    assert OperationLayer.from_dict(layer.to_dict()).to_dict() == layer.to_dict()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vector_power_correction", -100.1),
        ("vector_power_correction", 100.1),
        ("raster_power_correction", float("nan")),
        ("raster_power_correction", "10"),
    ],
)
def test_layer_power_correction_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ProjectFormatError):
        OperationLayer(**{field: value})


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
    path["geometry"]["subpaths"][0]["closed"] = "false"
    with pytest.raises(ProjectFormatError, match="subpath.closed must be a JSON boolean"):
        SceneObject.from_dict(path)


@pytest.mark.parametrize("schema", [999, True, 1.0, 1.5, "1"])
def test_schema_mismatch_is_rejected(schema: object):
    payload = ProjectDocument.new().to_dict()
    payload["schema_version"] = schema

    with pytest.raises(ProjectFormatError):
        ProjectDocument.from_dict(payload)


def test_newer_project_schema_is_rejected_without_downconversion():
    payload = ProjectDocument.new().to_dict()
    payload["schema_version"] = 4

    with pytest.raises(ProjectFormatError, match="Unsupported project schema 4"):
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


@pytest.mark.parametrize("schema", [1, 2])
def test_legacy_project_path_geometry_migrates_to_canonical_native_lines(schema):
    document = ProjectDocument.new("Legacy path")
    path = SceneObject.path(
        document.active_layer_id,
        [
            {
                "points": [[10, 20], [30, 20], [30, 60], [10, 20]],
                "closed": True,
            }
        ],
        center=(80, 70),
    )
    document.add_object(path)
    payload = document.to_dict()
    payload["schema_version"] = schema
    payload["objects"][0]["geometry"] = {
        "polylines": [
            {
                "points": [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, -0.5]],
                "closed": True,
            }
        ]
    }
    if schema == 1:
        payload.pop("coordinate_space")

    restored = ProjectDocument.from_dict(payload)
    geometry = restored.objects[0].path_geometry()

    assert restored.to_dict()["schema_version"] == 3
    assert restored.coordinate_space is CoordinateSpace.MACHINE
    assert "polylines" not in restored.objects[0].geometry
    assert geometry.fill_rule is PathFillRule.EVENODD
    assert geometry.subpaths[0].closed is True
    assert [segment.to for segment in geometry.subpaths[0].segments] == [
        (0.5, -0.5),
        (0.5, 0.5),
    ]
    assert all(
        isinstance(segment, PathLineSegment)
        for segment in geometry.subpaths[0].segments
    )


@pytest.mark.parametrize("schema", [1, 2])
def test_legacy_project_path_rejects_unexpected_polyline_child_fields(schema):
    document = ProjectDocument.new("Malformed legacy path")
    path = SceneObject.path(
        document.active_layer_id,
        [{"points": [[0, 0], [1, 1]], "closed": False}],
    )
    document.add_object(path)
    payload = document.to_dict()
    payload["schema_version"] = schema
    payload["objects"][0]["geometry"] = {
        "polylines": [
            {
                "points": [[-0.5, -0.5], [0.5, 0.5]],
                "closed": False,
                "segments": [],
            }
        ]
    }
    if schema == 1:
        payload.pop("coordinate_space")

    with pytest.raises(
        ProjectFormatError,
        match=r"legacy polyline\[0\].*unsupported field.*segments",
    ):
        ProjectDocument.from_dict(payload)


def test_schema_three_rejects_legacy_polyline_geometry():
    document = ProjectDocument.new("Spoofed current project")
    item = SceneObject.path(
        document.active_layer_id,
        [{"points": [[0, 0], [1, 1]], "closed": False}],
    )
    document.add_object(item)
    payload = document.to_dict()
    payload["objects"][0]["geometry"] = {
        "polylines": [{"points": [[-0.5, -0.5], [0.5, 0.5]], "closed": False}]
    }

    with pytest.raises(ProjectFormatError, match="schema 3.*canonical native"):
        ProjectDocument.from_dict(payload)


def test_native_path_constructor_clone_duplicate_and_group_preserve_exact_geometry():
    document = ProjectDocument.new("Native curve")
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (-0.5, 0.0),
                (
                    PathCubicSegment(
                        (-0.25, -0.75),
                        (0.25, 0.75),
                        (0.5, 0.0),
                    ),
                ),
                closed=False,
            ),
        ),
        fill_rule=PathFillRule.NONZERO,
    )
    first = SceneObject.native_path(
        document.active_layer_id,
        geometry,
        name="Curve one",
        transform=Transform(20, 30, 40, 50, rotation_deg=15, mirror_x=True),
    )
    second = SceneObject.native_path(
        document.active_layer_id,
        geometry,
        name="Curve two",
        transform=Transform(60, 70, 20, 30, mirror_y=True),
    )
    first.group_id = second.group_id = "group-native"
    document.add_object(first)
    document.add_object(second)

    before_clone = document.to_dict()
    clone = document.clone()
    duplicates = document.duplicate_objects([first.id, second.id])

    assert clone.to_dict() == before_clone
    assert clone.objects[0].path_geometry() == geometry
    assert clone.objects[1].path_geometry() == geometry
    assert duplicates[0].path_geometry() == geometry
    assert duplicates[1].path_geometry() == geometry
    assert duplicates[0].group_id == duplicates[1].group_id
    assert duplicates[0].group_id != "group-native"


def test_project_native_segment_limit_rejects_before_partial_add(monkeypatch):
    import laser_aligner.project.model as project_model

    monkeypatch.setattr(project_model, "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT", 1)
    document = ProjectDocument.new("Bounded paths")
    geometry = NativePathGeometry(
        (PathSubpath((0.0, 0.0), (PathLineSegment((1.0, 0.0)),)),)
    )
    first = SceneObject.native_path(document.active_layer_id, geometry, name="First")
    second = SceneObject.native_path(document.active_layer_id, geometry, name="Second")
    document.add_object(first)

    with pytest.raises(ValueError, match="segment project limit"):
        document.add_object(second)

    assert [item.id for item in document.objects] == [first.id]


def test_project_native_segment_batch_preflight_is_atomic_and_replacement_aware(
    monkeypatch,
):
    import laser_aligner.project.model as project_model

    monkeypatch.setattr(project_model, "MAX_NATIVE_PATH_SEGMENTS_PER_PROJECT", 1)
    document = ProjectDocument.new("Bounded path batch")
    geometry = NativePathGeometry(
        (PathSubpath((0.0, 0.0), (PathLineSegment((1.0, 0.0)),)),)
    )
    first = SceneObject.native_path(document.active_layer_id, geometry, name="First")
    second = SceneObject.native_path(document.active_layer_id, geometry, name="Second")

    with pytest.raises(ValueError, match="segment project limit"):
        document.validate_object_additions((first, second))
    assert document.objects == []

    document.add_object(first)
    with pytest.raises(ValueError, match="segment project limit"):
        document.validate_object_additions((second,))
    document.validate_object_additions((second,), replacing_ids=(first.id,))
