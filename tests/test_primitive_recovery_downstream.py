from __future__ import annotations

import math
import re

import numpy as np
import pytest

from laser_aligner.config import LaserSettings
from laser_aligner.project import (
    NATIVE_PATH_FORMAT_VERSION,
    Bounds,
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathLineSegment,
    ProjectDocument,
    SceneObject,
    Transform,
    fit_physical_contours_to_native_path,
    generate_project_gcode,
    transform_native_path,
)
from laser_aligner.project.toolpath import object_polylines
from laser_aligner.vision.trace_orientation import (
    TraceOrientationGeometry,
    estimate_trace_orientation,
    trace_rotation_transform,
)

_SOURCE_PITCH_MM = 0.25
_BAR_ANGLE_DEG = 1.7


def _shallow_staircase_bar() -> tuple[np.ndarray, np.ndarray]:
    angle = math.radians(_BAR_ANGLE_DEG)
    rotation = np.asarray(
        (
            (math.cos(angle), -math.sin(angle)),
            (math.sin(angle), math.cos(angle)),
        ),
        dtype=np.float64,
    )
    local_corners = np.asarray(
        ((-20.0, -3.0), (20.0, -3.0), (20.0, 3.0), (-20.0, 3.0)),
        dtype=np.float64,
    )
    corners = local_corners @ rotation.T + np.asarray((50.0, 40.0))
    exact_parts: list[np.ndarray] = []
    staircase_parts: list[np.ndarray] = []
    for index, start in enumerate(corners):
        end = corners[(index + 1) % len(corners)]
        sample_count = 401 if index % 2 == 0 else 121
        exact = np.linspace(start, end, sample_count, endpoint=False)
        exact_parts.append(exact)
        staircase_parts.append(
            np.round(exact / _SOURCE_PITCH_MM) * _SOURCE_PITCH_MM
        )
    return np.vstack(staircase_parts), np.vstack(exact_parts)


def _circle() -> np.ndarray:
    angles = np.linspace(0.0, math.tau, 257, endpoint=False)
    return np.column_stack(
        (
            50.0 + 7.0 * np.cos(angles),
            70.0 + 7.0 * np.sin(angles),
        )
    )


def _fit_bar(*, include_circle: bool):
    staircase, _exact = _shallow_staircase_bar()
    contours = [staircase]
    if include_circle:
        circle = _circle()
        contours.append(circle)
    return fit_physical_contours_to_native_path(
        contours,
        [None] * len(contours),
        source_pixel_spacing_mm=(_SOURCE_PITCH_MM, _SOURCE_PITCH_MM),
        fitting_tolerance_mm=0.30,
        frame_bounds_mm=(0.0, 0.0, 100.0, 100.0),
    )


def _segment_classes(geometry: NativePathGeometry) -> tuple[type[object], ...]:
    return tuple(
        type(segment)
        for subpath in geometry.subpaths
        for segment in subpath.segments
    )


def test_recovered_geometry_round_trips_and_retains_guarded_gcode_contract() -> None:
    recovered = _fit_bar(include_circle=True)
    metrics = recovered.primitive_recovery

    assert metrics.recovered_line_count >= 2
    assert metrics.recovered_arc_count == 1
    native_payload = recovered.geometry.to_dict()
    segment_types = {
        segment["type"]
        for subpath in native_payload["subpaths"]
        for segment in subpath["segments"]
    }
    assert native_payload["path_version"] == NATIVE_PATH_FORMAT_VERSION == 1
    assert segment_types == {"line", "cubic"}

    native_round_trip = NativePathGeometry.from_dict(native_payload)
    assert native_round_trip.to_dict() == native_payload
    assert all(
        segment_type in {PathLineSegment, PathCubicSegment}
        for segment_type in _segment_classes(native_round_trip)
    )

    document = ProjectDocument.new(
        "Recovered primitive compatibility",
        Bounds(0.0, 0.0, 100.0, 100.0),
    )
    layer = document.layers[0]
    layer.speed_mm_min = 600.0
    layer.power_percent = 10.0
    item = SceneObject.native_path(
        layer.id,
        native_round_trip,
        name="Recovered bar and circle",
        transform=Transform(
            x_mm=recovered.center_mm[0],
            y_mm=recovered.center_mm[1],
            width_mm=recovered.width_mm,
            height_mm=recovered.height_mm,
        ),
    )
    document.add_object(item)

    project_payload = document.to_dict()
    assert project_payload["schema_version"] == 3
    assert project_payload["objects"][0]["geometry"] == native_payload
    restored = ProjectDocument.from_dict(project_payload)
    restored_geometry = restored.objects[0].path_geometry()
    assert restored_geometry.to_dict() == native_payload
    assert len(object_polylines(restored.objects[0])) == 2

    job = generate_project_gcode(
        restored,
        LaserSettings(power_max=1000, boundary_margin_mm=0.0),
    )
    lines = [line.strip() for line in job.text.splitlines() if line.strip()]
    initial_m5_index = lines.index("M5 ; laser off before any motion")
    first_motion_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith(("G0 ", "G1 "))
    )

    assert job.plan is not None
    assert "G21 ; millimetres" in lines
    assert "G90 ; absolute positioning" in lines
    assert initial_m5_index < first_motion_index
    assert lines[-2:] == ["M5", "; End of E3 project job"]
    assert re.search(r"(?m)^G[23](?:\s|$)", job.text) is None


def test_recovered_shallow_bar_drives_orientation_without_mutation() -> None:
    recovered = _fit_bar(include_circle=False)
    assert recovered.primitive_recovery.recovered_line_count >= 2
    world_geometry = transform_native_path(
        recovered.geometry,
        PathAffineTransform.from_components(
            scale_x=recovered.width_mm,
            scale_y=recovered.height_mm,
            translate_x=recovered.center_mm[0],
            translate_y=recovered.center_mm[1],
        ),
    )
    original_payload = world_geometry.to_dict()
    original_segment_classes = _segment_classes(world_geometry)

    estimate = estimate_trace_orientation(
        [TraceOrientationGeometry("recovered-bar", "trace-artwork", world_geometry)]
    )

    assert world_geometry.to_dict() == original_payload
    assert estimate.offered
    assert estimate.detected_skew_deg == pytest.approx(_BAR_ANGLE_DEG, abs=0.20)
    assert estimate.correction_deg == pytest.approx(-_BAR_ANGLE_DEG, abs=0.20)

    corrected = transform_native_path(
        world_geometry,
        trace_rotation_transform(estimate.correction_deg, estimate.pivot_mm),
    )
    corrected_estimate = estimate_trace_orientation(
        [TraceOrientationGeometry("corrected-bar", "trace-artwork", corrected)]
    )

    assert world_geometry.to_dict() == original_payload
    assert _segment_classes(corrected) == original_segment_classes
    assert not corrected_estimate.offered
    assert corrected_estimate.suppression_reason == "trivial_skew"
    assert corrected_estimate.detected_skew_deg == pytest.approx(0.0, abs=0.20)
