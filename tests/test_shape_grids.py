from __future__ import annotations

import math
import re

import numpy as np
import pytest

from laser_aligner.config import LaserSettings
from laser_aligner.project import (
    Bounds,
    ObjectKind,
    ProjectDocument,
    SceneObject,
    generate_project_gcode,
)
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
    template_from_shape_grid,
)


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
    document.layers[0].passes = 3
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
        for line in rapid_lines
    ]
    radii = [math.dist(point, (50, 50)) for point in rapid_points]
    assert radii == pytest.approx([4, 4, 4, 10, 10, 10], abs=0.02)
    assert job.text.count("M4 S100") == 6
    first_power = job.text.index("M4 S100")
    for rapid in rapid_lines[1:]:
        next_rapid = job.text.index(rapid, first_power + 1)
        assert "M5" in job.text[first_power:next_rapid]
        first_power = job.text.index("M4 S100", next_rapid)


def _closed_square(
    center_x: float,
    center_y: float,
    size: float,
    *,
    reverse: bool = False,
) -> list[list[float]]:
    half = size / 2.0
    points = [
        [center_x - half, center_y - half],
        [center_x + half, center_y - half],
        [center_x + half, center_y + half],
        [center_x - half, center_y + half],
        [center_x - half, center_y - half],
    ]
    return list(reversed(points)) if reverse else points


def _nested_path_document(
    contours: list[list[list[float]]],
    *,
    passes: int = 3,
) -> ProjectDocument:
    document = ProjectDocument.new("Nested contours", Bounds(0, 0, 200, 120))
    layer = document.layers[0]
    layer.power_percent = 10
    layer.passes = passes
    document.add_object(
        SceneObject(
            name="Imported compound path",
            kind=ObjectKind.PATH,
            layer_id=layer.id,
            geometry={
                "polylines": [
                    {"points": contour, "closed": True}
                    for contour in contours
                ]
            },
            transform={
                "x_mm": 0,
                "y_mm": 0,
                "width_mm": 1,
                "height_mm": 1,
            },
        )
    )
    return document


def _rapid_starts(text: str) -> list[tuple[float, float]]:
    return [
        tuple(float(value) for value in re.findall(r"[XY](-?\d+(?:\.\d+)?)", line))
        for line in text.splitlines()
        if line.startswith("G0 X")
    ]


@pytest.mark.parametrize(
    ("reverse_inner", "reverse_outer"),
    ((False, False), (True, False), (False, True), (True, True)),
)
def test_nested_contours_complete_inner_passes_before_outer_independent_of_winding(
    reverse_inner: bool,
    reverse_outer: bool,
) -> None:
    document = _nested_path_document(
        [
            _closed_square(50, 50, 30, reverse=reverse_outer),
            _closed_square(50, 50, 10, reverse=reverse_inner),
        ]
    )

    job = generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        optimize_order=False,
    )

    starts = _rapid_starts(job.text)
    sizes = [10 if point[0] == pytest.approx(45) else 30 for point in starts]
    assert sizes == [10, 10, 10, 30, 30, 30]
    assert max(index for index, size in enumerate(sizes) if size == 10) < min(
        index for index, size in enumerate(sizes) if size == 30
    )


def test_multiple_washers_complete_each_depth_before_outer_contours() -> None:
    template = template_from_shape_grid(
        ShapeGridSpec(
            name="two washers",
            rows=1,
            columns=2,
            width_mm=20,
            height_mm=20,
            horizontal_gap_mm=30,
            shape_kind=ShapeKind.WASHER,
            inner_diameter_mm=8,
        )
    )
    document = ProjectDocument.new("Two washers", Bounds(0, 0, 200, 120))
    document.layers[0].power_percent = 10
    document.layers[0].passes = 3
    objects = instantiate_template(
        template,
        target_x_mm=100,
        target_y_mm=60,
        rotation_deg=0,
        target_layer_id=document.active_layer_id,
    )
    for item in objects:
        document.add_object(item)

    starts = _rapid_starts(
        generate_project_gcode(
            document,
            LaserSettings(power_max=1000),
            optimize_order=True,
        ).text
    )
    centers = [(item.transform.x_mm, item.transform.y_mm) for item in objects]
    labels: list[tuple[int, str]] = []
    for point in starts:
        washer = min(range(len(centers)), key=lambda index: math.dist(point, centers[index]))
        radius = math.dist(point, centers[washer])
        labels.append((washer, "inner" if radius < 7 else "outer"))
    for washer in range(2):
        inner = [i for i, value in enumerate(labels) if value == (washer, "inner")]
        outer = [i for i, value in enumerate(labels) if value == (washer, "outer")]
        assert len(inner) == 3
        assert len(outer) == 3
        assert max(inner) < min(outer)


def test_three_nesting_depths_finish_all_passes_deepest_first() -> None:
    document = _nested_path_document(
        [
            _closed_square(60, 60, 50),
            _closed_square(60, 60, 30),
            _closed_square(60, 60, 10),
        ]
    )

    starts = _rapid_starts(
        generate_project_gcode(
            document,
            LaserSettings(power_max=1000),
            optimize_order=False,
        ).text
    )
    assert [2 * (60 - x) for x, _y in starts] == [
        10,
        10,
        10,
        30,
        30,
        30,
        50,
        50,
        50,
    ]


def test_single_and_unrelated_closed_contours_retain_pass_major_order() -> None:
    single = _nested_path_document([_closed_square(30, 30, 10)])
    assert len(
        _rapid_starts(
            generate_project_gcode(
                single,
                LaserSettings(power_max=1000),
                optimize_order=False,
            ).text
        )
    ) == 3

    unrelated = _nested_path_document(
        [_closed_square(30, 30, 10), _closed_square(80, 30, 10)],
        passes=2,
    )
    starts = _rapid_starts(
        generate_project_gcode(
            unrelated,
            LaserSettings(power_max=1000),
            optimize_order=False,
        ).text
    )
    assert [x for x, _y in starts] == [25, 75, 25, 75]


def test_nested_contour_travel_is_always_preceded_by_laser_off() -> None:
    document = _nested_path_document(
        [_closed_square(50, 50, 30), _closed_square(50, 50, 10)],
        passes=3,
    )
    lines = generate_project_gcode(
        document,
        LaserSettings(power_max=1000),
        optimize_order=True,
    ).text.splitlines()
    rapid_indices = [index for index, line in enumerate(lines) if line.startswith("G0 X")]

    assert len(rapid_indices) == 6
    for previous, current in zip(rapid_indices, rapid_indices[1:], strict=False):
        assert "M5" in lines[previous:current]


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
