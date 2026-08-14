from __future__ import annotations

import math
import re

import numpy as np
import pytest

from laser_aligner.config import LaserSettings
from laser_aligner.project import Bounds, ProjectDocument, generate_project_gcode
from laser_aligner.project.toolpath import (
    _containment_aware_nearest_order,
    _containment_depths,
    object_polylines,
)
from laser_aligner.templates import (
    GRID_AUTHORING_METADATA_KEY,
    ShapeGridSpec,
    ShapeKind,
    TemplateFormatError,
    instantiate_template,
    shape_polylines,
    template_from_shape_grid,
)


def test_shape_kind_preserves_string_enum_behavior() -> None:
    assert ShapeKind.RECTANGLE == "rectangle"
    assert str(ShapeKind.RECTANGLE) == "rectangle"


@pytest.mark.parametrize("kind", list(ShapeKind)[:-1])
def test_every_authored_shape_builds_closed_geometry(kind: ShapeKind) -> None:
    values = {"name": kind.value, "rows": 1, "columns": 1, "width_mm": 12.0, "height_mm": 10.0, "shape_kind": kind}
    if kind in {ShapeKind.CIRCLE, ShapeKind.CIRCLE_ONE_FLAT, ShapeKind.CIRCLE_TWO_FLATS, ShapeKind.WASHER}:
        values["height_mm"] = 12.0
    if kind in {ShapeKind.CIRCLE_ONE_FLAT, ShapeKind.CIRCLE_TWO_FLATS}:
        values["flat_distance_mm"] = 10.0
    if kind == ShapeKind.WASHER:
        values["inner_diameter_mm"] = 5.0
    template = template_from_shape_grid(ShapeGridSpec(**values))
    paths = object_polylines(template.objects[0])
    assert paths
    assert all(path.closed and np.linalg.norm(path.points[0] - path.points[-1]) < 1e-9 for path in paths)
    assert template.features[0].kind == kind.value


def test_two_flat_circle_12_by_10_preserves_circle_and_exact_flat_length() -> None:
    spec = ShapeGridSpec(
        name="flat",
        rows=1,
        columns=1,
        width_mm=12,
        height_mm=12,
        shape_kind=ShapeKind.CIRCLE_TWO_FLATS,
        flat_distance_mm=10,
    )
    points = object_polylines(template_from_shape_grid(spec).objects[0])[0].points
    assert np.ptp(points[:, 0]) == pytest.approx(10.0)
    assert np.ptp(points[:, 1]) == pytest.approx(12.0)
    flat_points = points[np.isclose(points[:, 0], 5.0)]
    assert np.ptp(flat_points[:, 1]) == pytest.approx(2 * math.sqrt(11), abs=1e-8)


def test_washer_is_one_object_two_concentric_contours_ordered_hole_first() -> None:
    spec = ShapeGridSpec(
        name="washer", rows=1, columns=1, width_mm=20, height_mm=20, shape_kind=ShapeKind.WASHER, inner_diameter_mm=8
    )
    template = template_from_shape_grid(spec)
    paths = object_polylines(template.objects[0])
    assert len(template.objects) == 1
    assert len(paths) == 2
    assert [np.ptp(path.points[:, 0]) for path in paths] == pytest.approx([20, 8])
    assert _containment_depths(paths) == [0, 1]
    ordered = _containment_aware_nearest_order(paths, np.asarray((10.0, 0.0)))
    assert np.ptp(ordered[0].points[:, 0]) == pytest.approx(8)
    assert np.ptp(ordered[1].points[:, 0]) == pytest.approx(20)
    assert template.features[0].descriptor["hole_ratio"] == pytest.approx(0.4)


def test_generated_washer_job_cuts_hole_first_with_laser_off_travel() -> None:
    spec = ShapeGridSpec(
        name="washer", rows=1, columns=1, width_mm=20, height_mm=20,
        shape_kind=ShapeKind.WASHER, inner_diameter_mm=8,
    )
    template = template_from_shape_grid(spec)
    document = ProjectDocument.new("Washer", Bounds(0, 0, 100, 100))
    document.layers[0].power_percent = 10
    for item in instantiate_template(
        template, target_x_mm=50, target_y_mm=50, rotation_deg=0,
        target_layer_id=document.active_layer_id,
    ):
        document.add_object(item)

    job = generate_project_gcode(
        document, LaserSettings(power_max=1000), optimize_order=True,
        start_position=(60, 50),
    )
    rapid_lines = [line for line in job.text.splitlines() if line.startswith("G0 X")]
    rapid_points = [
        tuple(float(value) for value in re.findall(r"[XY](-?\d+(?:\.\d+)?)", line))
        for line in rapid_lines[:2]
    ]
    assert math.dist(rapid_points[0], (50, 50)) == pytest.approx(4, abs=0.02)
    assert math.dist(rapid_points[1], (50, 50)) == pytest.approx(10, abs=0.02)
    assert job.text.count("M4 S100") == 2
    first_power = job.text.index("M4 S100")
    second_rapid = job.text.index(rapid_lines[1])
    assert "M5" in job.text[first_power:second_rapid]


def test_legacy_rectangle_recipe_migrates_to_rounded_rectangle() -> None:
    spec = ShapeGridSpec.from_authoring_metadata(
        {
            "kind": "rectangle_grid",
            "version": 1,
            "name": "legacy",
            "rows": 1,
            "columns": 1,
            "width_mm": 20,
            "height_mm": 10,
            "corner_radius_mm": 2,
            "horizontal_gap_mm": 0,
            "vertical_gap_mm": 0,
        }
    )
    assert spec.shape_kind == ShapeKind.ROUNDED_RECTANGLE
    template = template_from_shape_grid(spec)
    assert template.metadata[GRID_AUTHORING_METADATA_KEY]["kind"] == "shape_grid"


@pytest.mark.parametrize("inner", (0, 20, 21))
def test_washer_rejects_invalid_hole(inner: float) -> None:
    with pytest.raises(TemplateFormatError, match="inner_diameter"):
        ShapeGridSpec(
            name="bad",
            rows=1,
            columns=1,
            width_mm=20,
            height_mm=20,
            shape_kind=ShapeKind.WASHER,
            inner_diameter_mm=inner,
        )


def test_direct_shape_geometry_rejects_missing_required_dimensions() -> None:
    with pytest.raises(ValueError, match="inner_diameter_mm"):
        shape_polylines(
            ShapeKind.WASHER,
            width_mm=20.0,
            height_mm=20.0,
        )
    with pytest.raises(ValueError, match="flat_distance_mm"):
        shape_polylines(
            ShapeKind.CIRCLE_TWO_FLATS,
            width_mm=20.0,
            height_mm=20.0,
        )
