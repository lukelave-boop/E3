from __future__ import annotations

from laser_aligner.config import LaserSettings
from laser_aligner.materials import MaterialPreset
from laser_aligner.project import (
    Bounds,
    JobPreflightContext,
    ProjectDocument,
    SceneObject,
    build_job_preflight_report,
    generate_project_gcode,
)

_AREA = Bounds(0.0, 0.0, 100.0, 100.0)


def _rectangle_document() -> ProjectDocument:
    document = ProjectDocument.new("Material authority", work_area=_AREA)
    layer = document.layers[0]
    document.objects.append(
        SceneObject.rectangle(
            layer.id,
            center=_AREA.center,
            width_mm=20.0,
            height_mm=10.0,
        )
    )
    return document


def _codes(report: object) -> set[str]:
    return {finding.code for finding in report.findings}  # type: ignore[attr-defined]


def test_hand_authored_layer_preflights_and_plans_without_a_recipe() -> None:
    document = _rectangle_document()
    layer = document.layers[0]
    layer.speed_mm_min = 900.0
    layer.power_percent = 25.0
    layer.passes = 2

    report = build_job_preflight_report(
        document,
        JobPreflightContext(
            machine_work_area=_AREA,
            machine_max_work_feed_mm_min=2000.0,
            machine_max_travel_feed_mm_min=3000.0,
            planned_travel_feed_mm_min=1500.0,
        ),
    )
    job = generate_project_gcode(
        document,
        LaserSettings(
            boundary_margin_mm=0.0,
            travel_feed_mm_min=1500.0,
        ),
    )

    assert report.ready
    assert job.point_count > 0
    assert "M4" in job.text


def test_recipe_application_does_not_authorize_disabled_output() -> None:
    document = _rectangle_document()
    original = document.layers[0]
    original.output_enabled = False
    preset = MaterialPreset(
        material="Cardstock",
        name="Authoring values",
        speed_mm_min=800.0,
        power_percent=30.0,
    )

    replacement = preset.apply_to_layer(original)
    document.layers[0] = replacement
    report = build_job_preflight_report(
        document,
        JobPreflightContext(machine_work_area=_AREA),
    )

    assert replacement.id == original.id
    assert not replacement.output_enabled
    assert report.has_blockers
    assert {"output.layers_disabled", "output.none_enabled"} <= _codes(report)


def test_recipe_values_remain_subject_to_machine_feed_preflight() -> None:
    document = _rectangle_document()
    original = document.layers[0]
    preset = MaterialPreset(
        material="Plywood",
        name="Fast cut",
        speed_mm_min=6000.0,
        power_percent=40.0,
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="generic-diode-10w",
    )
    document.layers[0] = preset.apply_to_layer(
        original,
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="generic-diode-10w",
    )

    report = build_job_preflight_report(
        document,
        JobPreflightContext(
            machine_work_area=_AREA,
            machine_max_work_feed_mm_min=3000.0,
        ),
    )

    assert report.has_blockers
    assert "layer.work_feed_exceeds_machine_limit" in _codes(report)
