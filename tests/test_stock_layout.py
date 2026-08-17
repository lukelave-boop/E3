from __future__ import annotations

import math

import pytest

from laser_aligner.config import LaserSettings
from laser_aligner.project import (
    Bounds,
    LayerMode,
    ProjectDocument,
    SceneObject,
    center_selection_on_stock,
    fit_selection_to_stock,
    generate_project_frame,
    generate_project_gcode,
    is_stock_boundary,
    mark_stock_boundary,
    meaningful_stock_edges,
    snap_selection_rotation_to_stock,
)


def _rectangular_stock(
    document: ProjectDocument,
    *,
    center: tuple[float, float] = (60.0, 45.0),
    width: float = 100.0,
    height: float = 60.0,
    rotation: float = 0.0,
) -> SceneObject:
    item = SceneObject.rectangle(
        document.active_layer_id,
        center=center,
        width_mm=width,
        height_mm=height,
    )
    item.transform = item.transform.copy(rotation_deg=rotation)
    return mark_stock_boundary(item)


def test_stock_boundary_round_trips_without_a_schema_change() -> None:
    document = ProjectDocument.new("Stock", Bounds(0, 0, 120, 90))
    stock = _rectangular_stock(document)
    document.add_object(stock)

    restored = ProjectDocument.from_dict(document.to_dict())
    restored_stock = restored.get_object(stock.id)

    assert document.to_dict()["schema_version"] == 2
    assert is_stock_boundary(restored_stock)
    assert restored_stock.locked is True
    assert restored_stock.metadata["construction_only"] is True
    assert restored_stock.metadata["excluded_from_output"] is True


def test_open_path_cannot_become_a_stock_boundary() -> None:
    document = ProjectDocument.new("Open stock", Bounds(0, 0, 120, 90))
    path = SceneObject.path(
        document.active_layer_id,
        [{"points": [(10.0, 10.0), (90.0, 10.0), (90.0, 60.0)], "closed": False}],
        center=(50.0, 35.0),
    )

    with pytest.raises(ValueError, match="closed outline"):
        mark_stock_boundary(path)


def test_stock_boundary_is_never_visible_output_geometry() -> None:
    document = ProjectDocument.new("Stock", Bounds(0, 0, 120, 90))
    stock = _rectangular_stock(document)
    art = SceneObject.rectangle(
        document.active_layer_id,
        center=(60.0, 45.0),
        width_mm=20.0,
        height_mm=10.0,
    )
    document.add_object(stock)
    document.add_object(art)

    assert document.visible_output_objects() == [art]


def test_stock_boundary_does_not_change_gcode_or_frame_bounds() -> None:
    baseline = ProjectDocument.new("Baseline", Bounds(0, 0, 120, 90))
    baseline.layers[0].power_percent = 10.0
    art = SceneObject.rectangle(
        baseline.active_layer_id,
        name="Stencil art",
        center=(60.0, 45.0),
        width_mm=20.0,
        height_mm=10.0,
    )
    baseline.add_object(art)

    with_stock = ProjectDocument.from_dict(baseline.to_dict())
    with_stock.add_object(_rectangular_stock(with_stock))

    laser = LaserSettings(power_max=1000, boundary_margin_mm=0.0)
    baseline_job = generate_project_gcode(baseline, laser)
    stock_job = generate_project_gcode(with_stock, laser)
    baseline_frame = generate_project_frame(baseline, laser)
    stock_frame = generate_project_frame(with_stock, laser)

    assert stock_job.text == baseline_job.text
    assert stock_job.path_count == baseline_job.path_count == 1
    assert stock_frame.bounds_mm == pytest.approx(baseline_frame.bounds_mm)


def test_project_with_only_stock_boundary_has_no_output_geometry() -> None:
    document = ProjectDocument.new("Stock only", Bounds(0, 0, 120, 90))
    document.add_object(_rectangular_stock(document))

    with pytest.raises(ValueError, match="no visible output geometry"):
        generate_project_frame(
            document,
            LaserSettings(boundary_margin_mm=0.0),
        )


@pytest.mark.parametrize("mode", list(LayerMode))
def test_stock_boundary_is_excluded_from_every_toolpath_mode(
    mode: LayerMode,
) -> None:
    document = ProjectDocument.new("Stock only", Bounds(0, 0, 120, 90))
    document.layers[0].mode = mode
    document.add_object(_rectangular_stock(document))

    with pytest.raises(ValueError, match="no enabled output paths"):
        generate_project_gcode(
            document,
            LaserSettings(boundary_margin_mm=0.0),
        )


def test_center_commands_move_selection_to_stock_center() -> None:
    document = ProjectDocument.new("Center", Bounds(0, 0, 150, 100))
    document.add_object(_rectangular_stock(document, center=(80.0, 55.0)))
    art = SceneObject.rectangle(
        document.active_layer_id,
        center=(15.0, 20.0),
        width_mm=20.0,
        height_mm=10.0,
    )
    document.add_object(art)

    horizontal = center_selection_on_stock(
        document,
        [art.id],
        horizontal=True,
    )[art.id]
    both = center_selection_on_stock(
        document,
        [art.id],
        horizontal=True,
        vertical=True,
    )[art.id]

    assert horizontal.x_mm == pytest.approx(80.0)
    assert horizontal.y_mm == pytest.approx(20.0)
    assert (both.x_mm, both.y_mm) == pytest.approx((80.0, 55.0))


def test_rotation_snap_uses_nearest_meaningful_stock_edge() -> None:
    document = ProjectDocument.new("Rotate", Bounds(0, 0, 180, 140))
    stock = _rectangular_stock(
        document,
        center=(90.0, 70.0),
        width=120.0,
        height=60.0,
        rotation=27.0,
    )
    document.add_object(stock)
    art = SceneObject.rectangle(
        document.active_layer_id,
        center=(90.0, 35.0),
        width_mm=30.0,
        height_mm=8.0,
    )
    document.add_object(art)

    transforms, edge = snap_selection_rotation_to_stock(
        document,
        [art.id],
        edge_mode="nearest",
    )

    assert abs(abs(edge.angle_deg) - 27.0) < 1e-8
    assert transforms[art.id].rotation_deg == pytest.approx(edge.angle_deg)
    assert transforms[art.id].x_mm == art.transform.x_mm
    assert transforms[art.id].y_mm == art.transform.y_mm


def test_rotation_snap_preserves_multi_object_layout() -> None:
    document = ProjectDocument.new("Rotate group", Bounds(0, 0, 180, 140))
    document.add_object(
        _rectangular_stock(
            document,
            center=(90.0, 70.0),
            width=120.0,
            height=60.0,
            rotation=30.0,
        )
    )
    first = SceneObject.rectangle(
        document.active_layer_id,
        center=(75.0, 70.0),
        width_mm=12.0,
        height_mm=8.0,
    )
    first.transform = first.transform.copy(rotation_deg=10.0)
    second = SceneObject.rectangle(
        document.active_layer_id,
        center=(105.0, 70.0),
        width_mm=12.0,
        height_mm=8.0,
    )
    second.transform = second.transform.copy(rotation_deg=-5.0)
    document.add_object(first)
    document.add_object(second)

    before_distance = math.dist(
        (first.transform.x_mm, first.transform.y_mm),
        (second.transform.x_mm, second.transform.y_mm),
    )
    transforms, edge = snap_selection_rotation_to_stock(
        document,
        [first.id, second.id],
        edge_mode="top",
    )

    assert edge.angle_deg == pytest.approx(30.0)
    assert transforms[first.id].rotation_deg == pytest.approx(30.0)
    assert transforms[second.id].rotation_deg == pytest.approx(15.0)
    assert math.dist(
        (transforms[first.id].x_mm, transforms[first.id].y_mm),
        (transforms[second.id].x_mm, transforms[second.id].y_mm),
    ) == pytest.approx(before_distance)


def test_meaningful_edges_simplify_jagged_trace_runs() -> None:
    document = ProjectDocument.new("Jagged", Bounds(0, 0, 120, 90))
    contour = [
        (10.0, 10.0),
        (30.0, 10.04),
        (50.0, 9.98),
        (70.0, 10.03),
        (90.0, 10.0),
        (90.0, 60.0),
        (10.0, 60.0),
    ]
    stock = mark_stock_boundary(
        SceneObject.path(
            document.active_layer_id,
            [{"points": contour, "closed": True}],
            center=(50.0, 35.0),
        )
    )

    edges = meaningful_stock_edges(stock)

    assert len(edges) <= 6
    assert max(edge.length for edge in edges) > 75.0


def test_fit_to_stock_scales_and_centers_with_margin() -> None:
    document = ProjectDocument.new("Fit", Bounds(0, 0, 120, 90))
    document.add_object(
        _rectangular_stock(
            document,
            center=(60.0, 45.0),
            width=100.0,
            height=60.0,
        )
    )
    art = SceneObject.rectangle(
        document.active_layer_id,
        center=(20.0, 20.0),
        width_mm=20.0,
        height_mm=10.0,
    )
    document.add_object(art)

    fitted = fit_selection_to_stock(
        document,
        [art.id],
        margin_mm=5.0,
    )[art.id]

    assert (fitted.x_mm, fitted.y_mm) == pytest.approx((60.0, 45.0))
    assert fitted.width_mm == pytest.approx(90.0, abs=1e-5)
    assert fitted.height_mm == pytest.approx(45.0, abs=1e-5)


def test_fit_to_irregular_stock_stays_inside_concave_outline() -> None:
    document = ProjectDocument.new("Concave", Bounds(0, 0, 120, 100))
    contour = [
        (10.0, 10.0),
        (110.0, 10.0),
        (110.0, 90.0),
        (70.0, 90.0),
        (70.0, 55.0),
        (50.0, 55.0),
        (50.0, 90.0),
        (10.0, 90.0),
    ]
    stock = mark_stock_boundary(
        SceneObject.path(
            document.active_layer_id,
            [{"points": contour, "closed": True}],
            center=(60.0, 50.0),
        )
    )
    document.add_object(stock)
    art = SceneObject.rectangle(
        document.active_layer_id,
        center=(20.0, 20.0),
        width_mm=20.0,
        height_mm=20.0,
        corner_radius_mm=2.0,
    )
    document.add_object(art)

    fitted = fit_selection_to_stock(document, [art.id], margin_mm=3.0)[art.id]

    assert fitted.width_mm > 35.0
    assert fitted.height_mm > 35.0
    assert math.isfinite(fitted.x_mm)
    assert math.isfinite(fitted.y_mm)
