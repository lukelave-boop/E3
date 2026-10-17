"""Semantic regressions for batched planning and exact-preview preparation."""
import weakref

import numpy as np
import pytest

import laser_aligner.project.toolpath as toolpath
from laser_aligner.config import LaserSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.gcode.generator import validate_paths
from laser_aligner.gcode.job_plan import build_job_plan
from laser_aligner.geometry import Polyline
from laser_aligner.geometry.polygon import convex_polygon_violation_normalized_mm
from laser_aligner.machine.material_surface import attach, program_binding
from tests.test_toolpath import make_document, make_image_document


def surface_metadata():
    return {
        "schema_version": 1, "model_id": "a" * 64, "binding_id": "b" * 64,
        "datum_id": "synthetic", "capture_pose": [110., 110., None],
        "plan_id": "synthetic-plan", "measurement_id": "synthetic-measurement",
        "surface_elevation_mm": 2., "honeycomb_height_mm": 0.,
        "reference": {"synthetic": True}, "session": [1, 2, 3],
        "evidence_source_ids": [str(index) * 64 for index in range(3)],
    }


def test_surface_binding_precedes_single_exact_index(monkeypatch):
    original = toolpath.build_job_plan
    texts = []

    def indexed(text, **kwargs):
        texts.append(text)
        return original(text, **kwargs)

    monkeypatch.setattr(toolpath, "build_job_plan", indexed)
    metadata = surface_metadata()
    laser = LaserSettings(power_max=1000)
    job = toolpath.generate_project_gcode(make_document(), laser,
                                         material_surface_metadata=metadata)
    assert texts == [job.text]
    assert program_binding(line for line in job.text.splitlines()
                           if line and not line.startswith(";")) == metadata
    assert attach(job.text, metadata) == job.text
    assert job.plan == build_job_plan(
        job.text, power_max=1000, default_feed_mm_min=laser.travel_feed_mm_min,
        acceleration_mm_s2=laser.preview_acceleration_mm_s2,
        command_delay_ms=laser.preview_command_delay_ms,
    )
    for move in job.plan.moves:
        assert job.text.splitlines()[move.line_number - 1].startswith(("G0 ", "G1 "))


def test_surface_binding_rejects_invalid_metadata_and_command_overflow(monkeypatch):
    with pytest.raises(SafetyError, match="schema"):
        toolpath.generate_project_gcode(make_document(), LaserSettings(),
                                       material_surface_metadata={"bad": True})
    monkeypatch.setattr(toolpath, "_stream_command_count", lambda lines: toolpath._MAX_STREAM_COMMANDS)
    with pytest.raises(ValueError, match="stream command limit after surface"):
        toolpath.generate_project_gcode(make_document(), LaserSettings(),
                                       material_surface_metadata=surface_metadata())


def test_raster_temporaries_are_released_before_exact_index(tmp_path, monkeypatch):
    import cv2

    image = tmp_path / "dense.png"
    assert cv2.imwrite(str(image), np.full((80, 80), 128, dtype=np.uint8))
    document = make_image_document(image, width_mm=8, height_mm=8, line_interval_mm=.1)
    arrays = []
    original_rows = toolpath._image_raster_rows
    original_plan = toolpath.build_job_plan

    def rows(*args, **kwargs):
        result = original_rows(*args, **kwargs)
        arrays.extend(weakref.ref(span.points) for row in result[0] for span in row.spans)
        return result

    def plan(*args, **kwargs):
        assert arrays and all(reference() is None for reference in arrays)
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(toolpath, "_image_raster_rows", rows)
    monkeypatch.setattr(toolpath, "build_job_plan", plan)
    assert toolpath.generate_project_gcode(document, LaserSettings()).plan.moves


@pytest.mark.parametrize("size", [1, 4096, 4097])
@pytest.mark.parametrize("bad", [None, "outside", "nan", "empty"])
def test_batched_rectangle_bounds_checks_every_chunk(size, bad):
    paths = [Polyline(np.array([[1., 1.], [2., 2.]])) for _ in range(size)]
    if bad == "outside":
        paths[-1].points[-1, 0] = 10.001
    elif bad == "nan":
        paths[-1].points[-1, 1] = np.nan
    elif bad == "empty":
        paths[-1].points = np.empty((0, 2))
    if bad is None:
        validate_paths(paths, WorkArea(0, 10, 0, 10))
    else:
        with pytest.raises(SafetyError if bad == "outside" else ValueError):
            validate_paths(paths, WorkArea(0, 10, 0, 10))


def test_batched_polygon_matches_scalar_boundary_decisions():
    polygon = ((0., 3.), (3., 0.), (6., 3.), (3., 6.))
    rng = np.random.default_rng(718)
    for points in [rng.uniform(1, 5, (64, 2)) for _ in range(20)] + [
        np.array([[0., 3.], [3., 6.]]),
        np.array([[-1e-7, 3.], [3., 6.]]),
        np.array([[-2e-6, 3.], [3., 6.]]),
    ]:
        accepted = max(convex_polygon_violation_normalized_mm(p, polygon) for p in points) <= 1e-6
        if accepted:
            toolpath._validate_paths_in_guarded_polygon([Polyline(points)], polygon, coordinate_label="test")
        else:
            with pytest.raises(SafetyError):
                toolpath._validate_paths_in_guarded_polygon([Polyline(points)], polygon, coordinate_label="test")
    with pytest.raises(ValueError, match="finite"):
        toolpath._validate_paths_in_guarded_polygon(
            [Polyline(np.array([[np.nan, 3.]]))], polygon, coordinate_label="test")


@pytest.mark.parametrize("offset", [0.0, -0.0, 0.125, float("nan")])
@pytest.mark.parametrize("closed", [False, True])
def test_raster_coordinate_reuse_preserves_offsets_and_source_geometry(offset, closed):
    points = np.array([[-0., 3.], [4., 3.]])
    row = toolpath.RasterRow(points=points.copy(), spans=[Polyline(points.copy(), closed=closed)],
                            source_tag="test")
    before = row.spans[0].points.copy()
    placed = toolpath._place_raster_rows([row], None, scan_angle_deg=0)
    laser = LaserSettings(spot_offset_x_mm=offset)
    if np.isnan(offset):
        with pytest.raises(ValueError, match="finite"):
            toolpath._controller_raster_rows(placed, laser)
    else:
        result = toolpath._controller_raster_rows(placed, laser)
        expected = before - np.array([offset, 0.])
        assert np.array_equal(result[0].spans[0].points.view(np.uint64), expected.view(np.uint64))
        assert result[0].spans[0].closed is False
    assert np.array_equal(row.spans[0].points.view(np.uint64), before.view(np.uint64))


def test_cached_coordinate_format_retains_rounding_and_zero_normalization():
    values = [-0., 0., -.0004999, .0004999, -.0005, .0005, -1.2345, 1.2345, 999999.9999]
    for value in values * 2:
        normalized = 0.0 if abs(value) < .0005 else value
        assert toolpath._fmt(value) == f"{normalized:.3f}".rstrip("0").rstrip(".")
