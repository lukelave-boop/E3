import json
import math

import pytest

from laser_aligner.project import ProjectDocument, SceneObject
from laser_aligner.templates import (
    TEMPLATE_EXTENSION,
    CutTemplate,
    TemplateCatalog,
    TemplateFormatError,
    TemplateLibrary,
    instantiate_template,
    template_from_project,
)


def _project_with_two_labels() -> ProjectDocument:
    document = ProjectDocument.new("Two labels")
    first = SceneObject.rectangle(
        document.active_layer_id,
        name="Small rounded label",
        center=(10.0, 20.0),
        width_mm=4.0,
        height_mm=2.0,
        corner_radius_mm=0.5,
    )
    first.transform = first.transform.copy(rotation_deg=10.0)
    second = SceneObject.ellipse(
        document.active_layer_id,
        name="Large oval label",
        center=(20.0, 24.0),
        width_mm=6.0,
        height_mm=4.0,
    )
    document.add_object(first)
    document.add_object(second)
    return document


def _project_with_compound_label_path(*, two_outer_components: bool = True) -> ProjectDocument:
    document = ProjectDocument.new("Compound SVG label grid")
    polylines = [
        {
            "points": [[0.0, 0.0], [20.0, 0.0], [20.0, 10.0], [0.0, 10.0]],
            "closed": True,
        },
        {
            "points": [[6.0, 3.0], [14.0, 3.0], [14.0, 7.0], [6.0, 7.0]],
            "closed": True,
        },
    ]
    if two_outer_components:
        polylines.append(
            {
                "points": [[30.0, 0.0], [50.0, 0.0], [50.0, 10.0], [30.0, 10.0]],
                "closed": True,
            }
        )
    item = SceneObject.path(
        document.active_layer_id,
        polylines,
        name="Imported compound path",
        center=(100.0, 50.0),
    )
    item.transform = item.transform.copy(rotation_deg=30.0)
    document.add_object(item)
    return document


def test_template_round_trip_preserves_versioned_geometry_and_matching_data():
    document = _project_with_two_labels()

    template = template_from_project(
        document,
        "Label grid A",
        description="Two differently shaped labels",
        trace_options={"mode": "contrast", "minimum_area_mm2": 4.0},
        marker_id=17,
        metadata={"stock": "letter"},
    )
    restored = CutTemplate.from_dict(template.to_dict())

    assert restored.to_dict() == template.to_dict()
    assert restored.size_mm == pytest.approx((template.bounds.width, template.bounds.height))
    assert restored.description == "Two differently shaped labels"
    assert restored.marker_id == 17
    assert restored.trace_options["mode"] == "contrast"
    assert restored.metadata["source_project_id"] == document.id
    assert [feature.object_id for feature in restored.features] == [
        item.id for item in restored.objects
    ]


def test_project_objects_are_cloned_and_normalized_around_union_bounds_center():
    document = _project_with_two_labels()
    source_objects = document.visible_output_objects()
    source_bounds = source_objects[0].bounds().union(source_objects[1].bounds())

    template = template_from_project(document, "Normalized")

    assert template.bounds.center == pytest.approx((0.0, 0.0))
    assert template.bounds.width == pytest.approx(source_bounds.width)
    assert template.bounds.height == pytest.approx(source_bounds.height)
    for source, normalized, feature in zip(
        source_objects, template.objects, template.features, strict=True
    ):
        assert normalized is not source
        assert normalized.transform.x_mm == pytest.approx(
            source.transform.x_mm - source_bounds.center[0]
        )
        assert normalized.transform.y_mm == pytest.approx(
            source.transform.y_mm - source_bounds.center[1]
        )
        assert normalized.transform.width_mm == source.transform.width_mm
        assert normalized.transform.height_mm == source.transform.height_mm
        assert normalized.geometry == source.geometry
        assert feature.center_mm == pytest.approx(
            (normalized.transform.x_mm, normalized.transform.y_mm)
        )
        assert feature.rotation_deg == source.transform.rotation_deg

    document.objects[0].geometry["corner_radius_mm"] = 0.0
    assert template.objects[0].geometry["corner_radius_mm"] == 0.5


def test_compound_path_creates_one_feature_per_independent_outer_contour():
    template = template_from_project(
        _project_with_compound_label_path(),
        "Two labels in one imported path",
    )

    assert len(template.objects) == 1
    assert len(template.features) == 2
    assert {feature.object_id for feature in template.features} == {
        template.objects[0].id
    }
    assert [feature.width_mm for feature in template.features] == pytest.approx(
        [20.0, 20.0]
    )
    assert [feature.height_mm for feature in template.features] == pytest.approx(
        [10.0, 10.0]
    )
    assert [feature.rotation_deg for feature in template.features] == pytest.approx(
        [30.0, 30.0]
    )
    cosine = math.cos(math.radians(30.0))
    sine = math.sin(math.radians(30.0))
    assert [feature.center_mm for feature in template.features] == pytest.approx(
        [(-15.0 * cosine, -15.0 * sine), (15.0 * cosine, 15.0 * sine)]
    )


def test_single_outer_path_with_a_hole_falls_back_to_object_feature():
    template = template_from_project(
        _project_with_compound_label_path(two_outer_components=False),
        "Single compound label",
    )

    assert len(template.features) == 1
    feature = template.features[0]
    item = template.objects[0]
    assert feature.center_mm == pytest.approx((0.0, 0.0))
    assert feature.width_mm == pytest.approx(item.transform.width_mm)
    assert feature.height_mm == pytest.approx(item.transform.height_mm)
    assert feature.rotation_deg == pytest.approx(item.transform.rotation_deg)


def test_empty_or_non_output_project_cannot_become_a_cut_template():
    empty = ProjectDocument.new()
    with pytest.raises(TemplateFormatError, match="empty project"):
        template_from_project(empty, "Empty")

    hidden = _project_with_two_labels()
    hidden.layers[0].visible = False
    with pytest.raises(TemplateFormatError, match="empty project"):
        template_from_project(hidden, "Hidden")


def test_instantiation_applies_only_rigid_rotation_and_translation():
    template = template_from_project(_project_with_two_labels(), "Rigid")
    local_centers = [
        (item.transform.x_mm, item.transform.y_mm) for item in template.objects
    ]

    placed = instantiate_template(
        template,
        100.0,
        200.0,
        rotation_deg=90.0,
        target_layer_id="layer-target",
    )

    assert len(placed) == len(template.objects)
    assert len({item.id for item in placed}) == len(placed)
    for source, item, (local_x, local_y) in zip(
        template.objects, placed, local_centers, strict=True
    ):
        assert item.id != source.id
        assert item.layer_id == "layer-target"
        assert item.transform.x_mm == pytest.approx(100.0 - local_y)
        assert item.transform.y_mm == pytest.approx(200.0 + local_x)
        assert item.transform.width_mm == source.transform.width_mm
        assert item.transform.height_mm == source.transform.height_mm
        assert item.transform.rotation_deg == pytest.approx(
            (source.transform.rotation_deg + 90.0 + 180.0) % 360.0 - 180.0
        )
        assert item.geometry == source.geometry


def test_unsupported_schema_and_inconsistent_size_are_rejected():
    payload = template_from_project(_project_with_two_labels(), "Schema").to_dict()
    payload["schema_version"] = 999

    with pytest.raises(TemplateFormatError, match="Unsupported template schema"):
        CutTemplate.from_dict(payload)

    for malformed_schema in (True, 1.0, 1.5, "1"):
        payload["schema_version"] = malformed_schema
        with pytest.raises(TemplateFormatError, match="must be an integer"):
            CutTemplate.from_dict(payload)

    payload["schema_version"] = 1
    payload["size_mm"]["width"] += 1.0
    with pytest.raises(TemplateFormatError, match="does not match bounds"):
        CutTemplate.from_dict(payload)


def test_library_round_trip_listing_overwrite_lookup_and_delete(tmp_path):
    template = template_from_project(_project_with_two_labels(), "Grid Set 01")
    library = TemplateLibrary(tmp_path / "templates")

    path = library.save(template)

    assert path.suffix == TEMPLATE_EXTENSION
    assert path.parent == library.root
    assert library.load(path).to_dict() == template.to_dict()
    assert library.get(template.id).id == template.id
    assert [item.id for item in library.list_templates()] == [template.id]
    with pytest.raises(FileExistsError):
        library.save(template)

    template.description = "Updated description"
    assert library.save(template, overwrite=True) == path
    assert library.load_by_id(template.id).description == "Updated description"
    assert library.delete(template.id) is True
    assert library.delete(template.id) is False
    assert library.list_templates() == []


def test_library_rejects_unsafe_names_and_malformed_files(tmp_path):
    library = TemplateLibrary(tmp_path / "templates")
    template = template_from_project(_project_with_two_labels(), "Safe")

    with pytest.raises(TemplateFormatError, match="directory"):
        library.save(template, "../outside")

    library.root.mkdir(parents=True)
    malformed = library.root / f"malformed{TEMPLATE_EXTENSION}"
    malformed.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")

    with pytest.raises(TemplateFormatError, match="Unsupported template schema"):
        library.list_templates()


def test_strict_library_listing_rejects_duplicate_persistent_ids(tmp_path):
    library = TemplateLibrary(tmp_path / "templates")
    first = template_from_project(_project_with_two_labels(), "First")
    duplicate = CutTemplate.from_dict(first.to_dict())
    duplicate.name = "Same identity in another file"
    library.save(first, "first")
    library.save(duplicate, "duplicate")

    with pytest.raises(TemplateFormatError, match="Duplicate template IDs") as exc_info:
        library.list_templates()

    assert first.id in str(exc_info.value)
    assert "first.e3template" in str(exc_info.value)
    assert "duplicate.e3template" in str(exc_info.value)


def test_resilient_scan_keeps_unique_templates_and_diagnoses_every_bad_file(tmp_path):
    library = TemplateLibrary(tmp_path / "templates")
    unique = template_from_project(_project_with_two_labels(), "Usable")
    duplicate = template_from_project(_project_with_two_labels(), "Duplicate A")
    duplicate_copy = CutTemplate.from_dict(duplicate.to_dict())
    duplicate_copy.name = "Duplicate B"
    library.save(unique, "usable")
    library.save(duplicate, "duplicate-a")
    library.save(duplicate_copy, "duplicate-b")
    malformed = library.root / f"broken{TEMPLATE_EXTENSION}"
    malformed.write_text("{not-json", encoding="utf-8")
    bad_encoding = library.root / f"bad-encoding{TEMPLATE_EXTENSION}"
    bad_encoding.write_bytes(b"\xff\xfe\x00")

    catalog = library.scan()

    assert isinstance(catalog, TemplateCatalog)
    assert [template.id for template in catalog.templates] == [unique.id]
    assert catalog.get(unique.id) is not None
    assert catalog.get(duplicate.id) is None
    assert len(catalog.diagnostics) == 4
    duplicate_diagnostics = [
        item for item in catalog.diagnostics if item.code == "duplicate-id"
    ]
    assert {item.path.name for item in duplicate_diagnostics} == {
        "duplicate-a.e3template",
        "duplicate-b.e3template",
    }
    assert {item.template_id for item in duplicate_diagnostics} == {duplicate.id}
    invalid = [item for item in catalog.diagnostics if item.code == "invalid-template"]
    assert {item.path.name for item in invalid} == {
        "bad-encoding.e3template",
        "broken.e3template",
    }
    assert library.catalog() == catalog


def test_delete_unique_template_ignores_unrelated_malformed_file(tmp_path):
    library = TemplateLibrary(tmp_path / "templates")
    unique = template_from_project(_project_with_two_labels(), "Usable")
    saved = library.save(unique, "usable")
    malformed = library.root / f"broken{TEMPLATE_EXTENSION}"
    malformed.write_text("{not-json", encoding="utf-8")

    assert library.delete(unique.id)
    assert not saved.exists()
    assert malformed.exists()


def test_delete_refuses_ambiguous_duplicate_template_id(tmp_path):
    library = TemplateLibrary(tmp_path / "templates")
    first = template_from_project(_project_with_two_labels(), "First")
    duplicate = CutTemplate.from_dict(first.to_dict())
    duplicate.name = "Duplicate"
    library.save(first, "first")
    library.save(duplicate, "duplicate")

    with pytest.raises(TemplateFormatError, match="Cannot delete duplicate template ID"):
        library.delete(first.id)

    assert len(list(library.root.glob(f"*{TEMPLATE_EXTENSION}"))) == 2
