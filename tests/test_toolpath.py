import re

import pytest

from laser_aligner.config import LaserSettings
from laser_aligner.errors import SafetyError
from laser_aligner.project import (
    Bounds,
    LayerMode,
    ProjectDocument,
    SceneObject,
    generate_project_frame,
    generate_project_gcode,
)
from laser_aligner.templates import (
    RectangleGridSpec,
    instantiate_template,
    template_from_rectangle_grid,
)


def make_document():
    document = ProjectDocument.new("Toolpath", Bounds(15, 15, 205, 205))
    layer = document.layers[0]
    layer.speed_mm_min = 2000
    layer.power_percent = 10
    document.add_object(
        SceneObject.rectangle(
            layer.id,
            center=(110, 110),
            width_mm=76.2,
            height_mm=50.8,
            corner_radius_mm=6.35,
        )
    )
    return document


def test_project_gcode_is_bracketed_by_laser_off():
    job = generate_project_gcode(make_document(), LaserSettings(power_max=1000))

    assert "M4 S100" in job.text
    assert "M5 ; laser off before any motion" in job.text
    assert job.text.rstrip().endswith("; End of E3 project job")
    assert job.path_count == 1
    assert job.cut_length_mm > 200


def test_multiple_layers_keep_distinct_power_and_speed():
    document = make_document()
    second = document.add_layer(
        name="Second",
        color="#4FC3A1",
        mode=LayerMode.LINE,
    )
    second.power_percent = 25
    second.speed_mm_min = 900
    document.add_object(SceneObject.ellipse(second.id, center=(80, 80), width_mm=20, height_mm=20))

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert "M4 S100" in job.text
    assert "M4 S250" in job.text
    assert "F2000" in job.text
    assert "F900" in job.text
    assert len(job.layer_summaries) == 2


def test_disabled_layer_is_omitted():
    document = make_document()
    document.layers[0].output_enabled = False

    with pytest.raises(ValueError, match="no enabled"):
        generate_project_gcode(document, LaserSettings())


def test_bounds_violation_is_rejected():
    document = make_document()
    document.objects[0].transform.x_mm = 200

    with pytest.raises(SafetyError):
        generate_project_gcode(document, LaserSettings(boundary_margin_mm=0))


def test_exact_boundary_float_noise_is_safe_but_real_overflow_is_rejected():
    document = ProjectDocument.new("Exact fit", Bounds(0, 0, 220, 220))
    exact_fit = RectangleGridSpec(
        name="Exact fit",
        rows=1,
        columns=3,
        width_mm=66.668,
        height_mm=10.0,
        horizontal_gap_mm=9.998,
    )
    template = template_from_rectangle_grid(exact_fit)
    for item in instantiate_template(
        template,
        target_x_mm=110.0,
        target_y_mm=110.0,
        rotation_deg=0.0,
        target_layer_id=document.active_layer_id,
    ):
        document.add_object(item)

    job = generate_project_gcode(
        document,
        LaserSettings(boundary_margin_mm=0),
    )
    assert job.bounds_mm[0] == pytest.approx(0.0, abs=1e-9)
    assert job.bounds_mm[2] == pytest.approx(220.0, abs=1e-9)

    outside = document.objects[-1]
    outside.transform.x_mm += 0.001
    with pytest.raises(SafetyError):
        generate_project_gcode(
            document,
            LaserSettings(boundary_margin_mm=0),
        )


def test_frame_contains_no_positive_laser_command():
    job = generate_project_frame(make_document(), LaserSettings(frame_power=0))

    assert not re.search(r"\bM[34]\b", job.text)
    assert "M5" in job.text


def test_zero_power_layer_does_not_emit_laser_enable():
    document = make_document()
    document.layers[0].power_percent = 0

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert not re.search(r"\bM[34]\b", job.text)
    assert "Layer power is zero" in job.text


def test_unsupported_fill_layer_is_rejected_instead_of_silently_omitted():
    document = make_document()
    document.layers[0].mode = LayerMode.FILL

    with pytest.raises(ValueError, match="fill output"):
        generate_project_gcode(document, LaserSettings())


def test_text_output_is_rejected_until_outline_conversion_exists():
    document = ProjectDocument.new("Text", Bounds(15, 15, 205, 205))
    document.add_object(
        SceneObject(
            name="Label",
            kind="text",
            layer_id=document.active_layer_id,
            geometry={"text": "TEST", "font_family": "Sans Serif"},
        )
    )

    with pytest.raises(ValueError, match="Label"):
        generate_project_gcode(document, LaserSettings())


def test_pass_count_repeats_each_path():
    document = make_document()
    document.layers[0].passes = 3

    job = generate_project_gcode(document, LaserSettings(power_max=1000))

    assert job.path_count == 3
    assert job.text.count("M4 S100") == 3
    assert job.layer_summaries[0]["passes"] == 3


def test_layer_priority_controls_emission_order():
    document = ProjectDocument.new("Order", Bounds(15, 15, 205, 205))
    first = document.layers[0]
    first.name = "First"
    first.priority = 10
    second = document.add_layer(name="Second")
    second.priority = 0
    document.add_object(SceneObject.rectangle(first.id, center=(60, 60), width_mm=10, height_mm=10))
    document.add_object(SceneObject.rectangle(second.id, center=(140, 140), width_mm=10, height_mm=10))

    job = generate_project_gcode(document, LaserSettings())

    assert job.text.index("; Layer Second") < job.text.index("; Layer First")
