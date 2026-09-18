from __future__ import annotations

import math

import numpy as np
import pytest

from laser_aligner.project import ObjectKind, SceneObject
from laser_aligner.project.polygon import create_regular_polygon, regular_polygon_points
from laser_aligner.project.toolpath import object_polylines


@pytest.mark.parametrize("sides", range(3, 13))
@pytest.mark.parametrize("angle", [0.0, 37.0, -125.0])
def test_polygon_preserves_clicked_center_radius_and_angle(sides: int, angle: float) -> None:
    center = (43.0, 58.0)
    expected = regular_polygon_points(center, 12, angle, sides)
    assert len(expected) == sides
    assert all(math.dist(point, center) == pytest.approx(12) for point in expected)
    assert np.mean(expected, axis=0) == pytest.approx(center)
    assert expected[0] == pytest.approx((
        center[0] + 12 * math.cos(math.radians(angle)),
        center[1] + 12 * math.sin(math.radians(angle)),
    ))
    item = create_regular_polygon("layer-1", center, 12, angle, sides)
    assert item.kind is ObjectKind.POLYGON
    restored = SceneObject.from_dict(item.to_dict())
    paths = object_polylines(restored)
    assert len(paths) == 1
    assert paths[0].closed
    assert paths[0].points[:sides] == pytest.approx(np.array(expected))


@pytest.mark.parametrize("sides", [2, 0, -4, 13, 99, 1000000, 4.5, "12", True])
def test_polygon_rejects_invalid_side_count(sides: object) -> None:
    with pytest.raises(ValueError, match="3 to 12"):
        create_regular_polygon("layer-1", (30, 30), 10, 0, sides)


@pytest.mark.parametrize("radius", [0, -1, float("nan"), float("inf")])
def test_polygon_rejects_invalid_radius(radius: float) -> None:
    with pytest.raises(ValueError):
        create_regular_polygon("layer-1", (30, 30), radius, 0, 6)


@pytest.mark.parametrize("center, angle", [((float("inf"), 0), 0), ((0, 0), float("nan"))])
def test_polygon_rejects_invalid_position_or_angle(center: tuple[float, float], angle: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        regular_polygon_points(center, 10, angle, 6)


def test_polygon_generates_guarded_program_and_rejects_out_of_bounds() -> None:
    from laser_aligner.config import LaserSettings
    from laser_aligner.errors import SafetyError
    from laser_aligner.project import Bounds, ProjectDocument, generate_project_gcode

    document = ProjectDocument.new(work_area=Bounds(0, 0, 100, 100))
    item = create_regular_polygon(document.active_layer_id, (50, 50), 15, 22, 12)
    document.add_object(item)
    job = generate_project_gcode(document, LaserSettings(power_max=1000))
    assert "G21" in job.text and "G90" in job.text
    assert "M5" in job.text
    item.transform.x_mm += 100
    with pytest.raises(SafetyError):
        generate_project_gcode(document, LaserSettings(power_max=1000))
