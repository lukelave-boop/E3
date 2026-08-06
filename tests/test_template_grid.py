import json

import pytest

from laser_aligner.project import ProjectDocument, SceneObject
from laser_aligner.templates import (
    MAX_GRID_OBJECTS,
    RectangleGridSpec,
    TemplateFormatError,
    TemplateLibrary,
    template_from_project,
    template_from_rectangle_grid,
)


def _spec(**changes):
    values = {
        "name": "Three by two labels",
        "description": "Reusable rounded label sheet",
        "rows": 2,
        "columns": 3,
        "width_mm": 20.0,
        "height_mm": 10.0,
        "corner_radius_mm": 2.0,
        "horizontal_gap_mm": 3.0,
        "vertical_gap_mm": 4.0,
    }
    values.update(changes)
    return RectangleGridSpec(**values)


def test_rectangle_grid_spec_round_trip_derives_pitch_footprint_and_count():
    spec = _spec()

    assert spec.count == 6
    assert spec.object_count == 6
    assert spec.horizontal_pitch_mm == pytest.approx(23.0)
    assert spec.vertical_pitch_mm == pytest.approx(14.0)
    assert spec.pitch_x_mm == pytest.approx(23.0)
    assert spec.pitch_y_mm == pytest.approx(14.0)
    assert spec.footprint_size_mm == pytest.approx((66.0, 24.0))

    payload = spec.to_dict()
    payload.update(
        {
            "spacing_mode": "gap",
            "horizontal_pitch_mm": 999.0,
            "vertical_pitch_mm": 999.0,
            "footprint_width_mm": 999.0,
            "object_count": 999,
        }
    )
    assert RectangleGridSpec.from_dict(payload) == spec


def test_rectangle_grid_builds_centered_row_major_geometry_and_metadata():
    spec = _spec()
    template = template_from_rectangle_grid(
        spec,
        trace_options={"detection_mode": "contrast"},
    )

    assert template.name == spec.name
    assert template.description == spec.description
    assert template.bounds.center == pytest.approx((0.0, 0.0))
    assert template.size_mm == pytest.approx(spec.footprint_size_mm)
    assert len(template.objects) == spec.count
    assert len(template.features) == spec.count
    assert [item.name for item in template.objects] == [
        "Label 1, 1",
        "Label 1, 2",
        "Label 1, 3",
        "Label 2, 1",
        "Label 2, 2",
        "Label 2, 3",
    ]
    assert [
        (item.transform.x_mm, item.transform.y_mm) for item in template.objects
    ] == pytest.approx(
        [(-23.0, 7.0), (0.0, 7.0), (23.0, 7.0), (-23.0, -7.0), (0.0, -7.0), (23.0, -7.0)]
    )
    assert all(item.transform.width_mm == 20.0 for item in template.objects)
    assert all(item.transform.height_mm == 10.0 for item in template.objects)
    assert all(item.geometry["corner_radius_mm"] == 2.0 for item in template.objects)
    assert [feature.object_id for feature in template.features] == [
        item.id for item in template.objects
    ]
    assert RectangleGridSpec.from_template(template) == spec
    assert template.trace_options == {"detection_mode": "contrast"}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": " "}, "name"),
        ({"rows": 0}, "rows"),
        ({"rows": True}, "integer"),
        ({"columns": 1.5}, "integer"),
        ({"rows": MAX_GRID_OBJECTS + 1, "columns": 1}, "more than"),
        ({"width_mm": float("nan")}, "finite"),
        ({"height_mm": 0.0001}, "at least"),
        ({"corner_radius_mm": -0.1}, "negative"),
        ({"corner_radius_mm": 5.1}, "half the smaller"),
        ({"horizontal_gap_mm": -0.1}, "negative"),
        ({"vertical_gap_mm": float("inf")}, "finite"),
        ({"width_mm": 1e308, "columns": 2}, "footprint"),
    ],
)
def test_rectangle_grid_rejects_invalid_or_excessive_parameters(changes, message):
    with pytest.raises(TemplateFormatError, match=message):
        _spec(**changes)


def test_grid_edit_preserves_template_identity_creation_and_surviving_cell_ids():
    original = template_from_rectangle_grid(
        _spec(),
        trace_options={"detection_mode": "color"},
    )
    original.marker_id = 41
    original.metadata["stock"] = "letter"
    original_ids = [item.id for item in original.objects]

    edited_spec = _spec(
        name="Edited grid",
        description="New dimensions",
        rows=3,
        width_mm=21.0,
        corner_radius_mm=3.0,
    )
    edited = template_from_rectangle_grid(edited_spec, existing=original)

    assert edited.id == original.id
    assert edited.created_at == original.created_at
    assert edited.name == "Edited grid"
    assert edited.description == "New dimensions"
    assert edited.trace_options == original.trace_options
    assert edited.marker_id == 41
    assert edited.metadata["stock"] == "letter"
    assert [item.id for item in edited.objects[:6]] == original_ids
    assert len({item.id for item in edited.objects}) == edited_spec.count
    assert RectangleGridSpec.from_template(edited) == edited_spec


def test_grid_edit_refuses_to_reinterpret_a_freeform_template():
    document = ProjectDocument.new("Freeform")
    document.add_object(
        SceneObject.rectangle(document.active_layer_id, width_mm=10.0, height_mm=5.0)
    )
    freeform = template_from_project(document, "Freeform")

    with pytest.raises(TemplateFormatError, match="authoring metadata"):
        template_from_rectangle_grid(_spec(), existing=freeform)


def test_library_replace_updates_exact_file_without_rename_or_duplicate(tmp_path):
    library = TemplateLibrary(tmp_path / "templates")
    original = template_from_rectangle_grid(_spec())
    path = library.save(original)
    malformed = library.root / "unrelated.e3template"
    malformed.write_text("{bad-json", encoding="utf-8")
    edited = template_from_rectangle_grid(
        _spec(name="A renamed grid", width_mm=22.0),
        existing=original,
    )

    replaced_path = library.replace(
        edited,
        expected_modified_at=original.modified_at,
    )

    assert replaced_path == path
    assert path.exists()
    assert malformed.exists()
    assert len(list(library.root.glob("*.e3template"))) == 2
    restored = library.load(path)
    assert restored.id == original.id
    assert restored.created_at == original.created_at
    assert restored.name == "A renamed grid"
    assert RectangleGridSpec.from_template(restored).width_mm == 22.0


def test_library_replace_rejects_creation_change_stale_edit_and_duplicate_target(tmp_path):
    library = TemplateLibrary(tmp_path / "templates")
    original = template_from_rectangle_grid(_spec())
    first_path = library.save(original, "first")
    edited = template_from_rectangle_grid(_spec(name="Edited"), existing=original)

    changed_creation = edited.to_dict()
    changed_creation["created_at"] = "2020-01-01T00:00:00+00:00"
    with pytest.raises(TemplateFormatError, match="creation timestamp"):
        library.replace(type(edited).from_dict(changed_creation))

    with pytest.raises(TemplateFormatError, match="changed after it was opened"):
        library.replace(edited, expected_modified_at="stale-timestamp")

    duplicate_payload = json.loads(first_path.read_text(encoding="utf-8"))
    duplicate_path = library.root / "duplicate.e3template"
    duplicate_path.write_text(json.dumps(duplicate_payload), encoding="utf-8")
    with pytest.raises(TemplateFormatError, match="duplicate template ID"):
        library.replace(edited)

    missing = template_from_rectangle_grid(_spec(name="Missing"))
    with pytest.raises(FileNotFoundError, match="No template with ID"):
        library.replace(missing)
