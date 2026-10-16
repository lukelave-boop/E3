from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.project import job_preflight as preflight_module
from laser_aligner.project.job_preflight import (
    JobPreflightContext,
    build_job_preflight_report,
)
from laser_aligner.project.model import (
    Bounds,
    CoordinateSpace,
    LayerMode,
    ObjectKind,
    ProjectDocument,
    SceneObject,
    Transform,
)
from laser_aligner.project.path_geometry import (
    NativePathGeometry,
    PathLineSegment,
    PathSubpath,
)


def _context(angle: float = 90.0) -> JobPreflightContext:
    radians = math.radians(angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(50.0, 50.0),
        x_axis_machine=(cosine, sine),
        y_axis_machine=(-sine, cosine),
        width_mm=100.0,
        height_mm=100.0,
        provenance_digest="ab" * 32,
    )
    return JobPreflightContext(
        machine_work_area=Bounds(-150.0, -150.0, 250.0, 250.0),
        coordinate_frame=frame,
        honeycomb_execution_signature=(*frame.provenance_signature, "cd" * 32),
        expected_calibration_profile_id="camera-a",
        active_calibration_profile_id="camera-a",
        bed_calibration_state="VALID",
        honeycomb_support_state="CURRENT",
    )


def _document() -> ProjectDocument:
    document = ProjectDocument.new(
        "Machine-axis advisory raster",
        work_area=Bounds(0.0, 0.0, 100.0, 100.0),
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.layers[0].mode = LayerMode.RASTER
    document.layers[0].line_interval_mm = 1.0
    return document


def _image_document(tmp_path: Path) -> ProjectDocument:
    image_path = tmp_path / "black.png"
    assert cv2.imwrite(str(image_path), np.zeros((2, 2), dtype=np.uint8))
    document = _document()
    document.objects.append(
        SceneObject(
            name="Rectangular raster",
            kind=ObjectKind.IMAGE,
            layer_id=document.layers[0].id,
            transform=Transform(50.0, 50.0, 20.0, 4.0),
            geometry={"asset": str(image_path)},
        )
    )
    return document


@pytest.mark.parametrize(
    ("frame_angle", "scan_angle", "rows", "columns"),
    [
        (90.0, 0.0, 20, 4),
        (-90.0, 0.0, 20, 4),
        (90.0, 90.0, 4, 20),
        (45.0, 0.0, 17, 17),
        (-45.0, 0.0, 17, 17),
        (45.0, 45.0, 4, 20),
    ],
)
@pytest.mark.parametrize("budget", ["rows", "samples"])
@pytest.mark.parametrize("limit_delta", [0, -1])
def test_rotated_raster_advisory_budget_accepts_and_rejects_machine_axis_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frame_angle: float,
    scan_angle: float,
    rows: int,
    columns: int,
    budget: str,
    limit_delta: int,
) -> None:
    document = _image_document(tmp_path)
    document.layers[0].scan_angle_deg = scan_angle
    before = document.to_dict()
    expected = rows if budget == "rows" else rows * columns
    monkeypatch.setattr(
        preflight_module,
        "MAX_RASTER_ROWS" if budget == "rows" else "MAX_RASTER_SAMPLES",
        expected + limit_delta,
    )

    report = build_job_preflight_report(document, _context(frame_angle))

    code = f"raster.aggregate_{budget}_exceeded"
    matches = [finding for finding in report.findings if finding.code == code]
    if limit_delta < 0:
        assert len(matches) == 1
        assert matches[0].context["actual"] == expected
        assert matches[0].context["limit"] == expected - 1
        assert report.has_blockers
    else:
        assert not matches
        assert report.ready
    assert document.to_dict() == before


@pytest.mark.parametrize("kind", ["rectangle", "native"])
@pytest.mark.parametrize("mode", [LayerMode.RASTER, LayerMode.FILL])
@pytest.mark.parametrize("vertical", [False, True])
def test_rotated_thin_vector_advisory_proof_uses_raster_machine_angle_only(
    kind: str,
    mode: LayerMode,
    vertical: bool,
) -> None:
    document = _document()
    layer = document.layers[0]
    layer.mode = mode
    layer.line_interval_mm = 0.2
    layer.air_assist = True
    center = (50.05, 50.0) if vertical else (50.0, 50.05)
    width, height = (0.01, 10.0) if vertical else (10.0, 0.01)
    if kind == "rectangle":
        item = SceneObject.rectangle(
            layer.id,
            center=center,
            width_mm=width,
            height_mm=height,
            corner_radius_mm=0.0,
        )
    else:
        item = SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (-0.5, -0.5),
                        (
                            PathLineSegment((0.5, -0.5)),
                            PathLineSegment((0.5, 0.5)),
                            PathLineSegment((-0.5, 0.5)),
                        ),
                        closed=True,
                    ),
                )
            ),
            transform=Transform(*center, width, height),
        )
    document.objects.append(item)

    report = build_job_preflight_report(document, _context())

    requires_mapping = (not vertical) if mode is LayerMode.RASTER else vertical
    assert any(
        finding.code == "air_assist.output_unconfigured" for finding in report.findings
    ) is requires_mapping


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scan_angle_deg", "bad"),
        ("scan_angle_deg", float("nan")),
        ("scan_angle_deg", float("inf")),
        ("line_interval_mm", "bad"),
        ("speed_mm_min", "bad"),
    ],
)
def test_rotated_raster_malformed_settings_still_produce_structured_findings(
    field: str,
    value: object,
) -> None:
    document = _document()
    layer = document.layers[0]
    layer.air_assist = True
    setattr(layer, field, value)
    document.objects.append(
        SceneObject.rectangle(layer.id, center=(50.0, 50.0), width_mm=20.0, height_mm=4.0)
    )

    report = build_job_preflight_report(document, _context())

    assert any(
        finding.code == "layer.setting_invalid" and finding.context.get("field") == field
        for finding in report.findings
    )
    assert report.has_blockers


def test_raster_missing_frame_retains_existing_blockers(tmp_path: Path) -> None:
    document = _image_document(tmp_path)

    report = build_job_preflight_report(
        document,
        JobPreflightContext(machine_work_area=Bounds(0.0, 0.0, 100.0, 100.0)),
    )

    codes = {finding.code for finding in report.findings}
    assert {"honeycomb.frame_missing", "honeycomb.binding_missing"} <= codes
    assert report.has_blockers


def test_machine_raster_ignores_unexpected_frame_for_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _image_document(tmp_path)
    document.coordinate_space = CoordinateSpace.MACHINE
    context = JobPreflightContext(
        machine_work_area=document.work_area,
        coordinate_frame=_context().coordinate_frame,
    )
    monkeypatch.setattr(preflight_module, "MAX_RASTER_ROWS", 4)

    report = build_job_preflight_report(document, context)

    codes = {finding.code for finding in report.findings}
    assert "honeycomb.frame_unexpected" in codes
    assert "raster.aggregate_rows_exceeded" not in codes


@pytest.mark.parametrize("frame_angle", [90.0, -90.0, 180.0])
def test_cardinal_frame_retains_vector_scan_at_polygon_boundary(frame_angle: float) -> None:
    document = _document()
    layer = document.layers[0]
    layer.air_assist = True
    # Exactly one scanline lies on the included polygon edge. Residual cosine
    # at a quarter turn must not move that edge across the row lattice.
    if frame_angle == 90.0:
        center, width, height = (50.05, 50.0), 0.1, 10.0
    elif frame_angle == -90.0:
        center, width, height = (49.95, 50.0), 0.1, 10.0
    else:
        center, width, height = (50.0, 49.95), 10.0, 0.1
    document.objects.append(
        SceneObject.rectangle(
            layer.id,
            center=center,
            width_mm=width,
            height_mm=height,
            corner_radius_mm=0.0,
        )
    )

    report = build_job_preflight_report(document, _context(frame_angle))

    assert any(
        finding.code == "air_assist.output_unconfigured" for finding in report.findings
    )
