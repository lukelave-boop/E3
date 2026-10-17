from __future__ import annotations

import math
import re

import pytest

from laser_aligner.config import LaserSettings
from laser_aligner.errors import SafetyError
from laser_aligner.project import (
    Bounds,
    NativePathGeometry,
    PathCubicSegment,
    PathLineSegment,
    PathSubpath,
    ProjectDocument,
    SceneObject,
    Transform,
    generate_project_gcode,
    load_project,
    save_project,
)
from laser_aligner.project.arc_fit import fit_arc_span
from laser_aligner.project.toolpath import object_polylines


def _fitted_document(*, mirror: bool = False) -> ProjectDocument:
    # The native shape is anisotropic; its physical edge is an 8 mm circle.
    points = []
    for index in range(121):
        angle = math.pi * index / 120
        radius = 8.0 + (0.02 if index == 60 else 0.0)
        points.append((radius * math.cos(angle) / 20, radius * math.sin(angle) / 30))
    outer = PathSubpath(points[0], tuple(PathLineSegment(point) for point in points[1:]), True)
    hole = PathSubpath((-0.04, 0.08), (
        PathLineSegment((0.04, 0.08)), PathLineSegment((0.04, 0.12)),
        PathLineSegment((-0.04, 0.12)),
    ), True)
    original = NativePathGeometry((outer, hole))
    fitted = fit_arc_span(original, 0, 0, 120, width_mm=20, height_mm=30)
    assert fitted.radius_mm == pytest.approx(8, abs=0.005)
    assert fitted.geometry.subpaths[1] == hole
    assert any(isinstance(segment, PathCubicSegment) for segment in fitted.arc_subpath.segments)
    document = ProjectDocument.new("Fitted arc", Bounds(0, 0, 100, 100))
    document.layers[0].speed_mm_min = 600
    document.layers[0].power_percent = 10
    document.add_object(SceneObject.native_path(
        document.active_layer_id, fitted.geometry,
        transform=Transform(50, 50, 20, 30, 37, mirror_x=mirror),
    ))
    return document


@pytest.mark.parametrize("mirror", [False, True])
def test_fitted_arc_save_load_preserves_hole_and_guarded_job(tmp_path, mirror):
    document = _fitted_document(mirror=mirror)
    path = save_project(document, tmp_path / "arc.e3laser")
    restored = load_project(path)
    assert restored.to_dict() == document.to_dict()
    assert restored.to_dict()["schema_version"] == 6
    assert restored.objects[0].path_geometry().path_version == 1
    assert len(object_polylines(restored.objects[0])) == 2
    job = generate_project_gcode(restored, LaserSettings(power_max=1000, boundary_margin_mm=0))
    lines = [line.strip() for line in job.text.splitlines() if line.strip()]
    first_motion = next(index for index, line in enumerate(lines) if line.startswith(("G0 ", "G1 ")))
    assert "G21 ; millimetres" in lines[:first_motion]
    assert "G90 ; absolute positioning" in lines[:first_motion]
    assert "M5 ; laser off before any motion" in lines[:first_motion]
    assert lines[-2:] == ["M5", "; End of E3 project job"]
    assert re.search(r"(?m)^G[23](?:\s|$)", job.text) is None


def test_fitted_arc_still_rejected_outside_work_area():
    document = _fitted_document()
    document.objects[0].transform.x_mm = 1
    with pytest.raises(SafetyError, match="safe work area"):
        generate_project_gcode(document, LaserSettings(power_max=1000, boundary_margin_mm=0))
