from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.air_assist import AirAssistCommands, AirAssistMode
from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import LaserSettings, MachineSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.machine.service import MachineService
from laser_aligner.project import job_preflight as preflight_module
from laser_aligner.project import raster_asset as raster_asset_module
from laser_aligner.project import toolpath as toolpath_module
from laser_aligner.project.job_preflight import (
    JobPreflightCancelled,
    JobPreflightContext,
    PreflightSeverity,
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
    PathCubicSegment,
    PathLineSegment,
    PathSubpath,
)
from laser_aligner.project.planner_limits import MAX_RASTER_SAMPLES
from laser_aligner.project.raster_asset import RasterAssetMetadata

_DEFAULT_AREA = Bounds(0.0, 0.0, 100.0, 100.0)


def _rectangle_document(
    *,
    power_percent: float = 20.0,
    work_area: Bounds = _DEFAULT_AREA,
) -> ProjectDocument:
    document = ProjectDocument.new("Preflight", work_area=work_area)
    document.layers[0].power_percent = power_percent
    document.objects.append(
        SceneObject.rectangle(
            document.layers[0].id,
            center=work_area.center,
            width_mm=20.0,
            height_mm=10.0,
        )
    )
    return document


def _context(
    work_area: Bounds | WorkArea | None = _DEFAULT_AREA,
    **changes: object,
) -> JobPreflightContext:
    return JobPreflightContext(machine_work_area=work_area, **changes)


def _codes(report: object) -> tuple[str, ...]:
    return tuple(finding.code for finding in report.findings)  # type: ignore[attr-defined]


def test_clean_report_is_immutable_ready_and_counts_information() -> None:
    source_area = WorkArea(0.0, 100.0, 0.0, 100.0)
    context = _context(source_area)
    source_area.x_max = 50.0

    report = build_job_preflight_report(_rectangle_document(), context)

    assert report.ready
    assert not report.has_blockers
    assert report.counts.info == report.info_count == 2
    assert report.counts.warnings == report.warning_count == 0
    assert report.counts.blockers == report.blocker_count == 0
    assert report.counts.total == len(report.findings)
    assert _codes(report) == (
        "geometry.simple_bounds_checked",
        "planner.exact_checks_deferred",
    )
    assert context.machine_work_area == Bounds(0.0, 0.0, 100.0, 100.0)
    with pytest.raises(FrozenInstanceError):
        report.findings = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        report.findings[0].context["changed"] = True  # type: ignore[index]


def test_zero_power_and_known_execution_unready_are_warning_only() -> None:
    report = build_job_preflight_report(
        _rectangle_document(power_percent=0.0),
        _context(
            execution_ready=False,
            execution_unready_reason="Machine is disconnected",
        ),
    )

    assert report.ready
    assert report.warning_count == 2
    assert {finding.code for finding in report.findings} >= {
        "output.zero_power",
        "execution.not_ready",
    }
    assert all(
        finding.severity is not PreflightSeverity.BLOCKER
        for finding in report.findings
    )


def test_powered_air_assist_request_requires_configured_machine_output() -> None:
    document = _rectangle_document()
    layer = document.layers[0]
    layer.air_assist = True

    report = build_job_preflight_report(document, _context())

    finding = next(
        item
        for item in report.findings
        if item.code == "air_assist.output_unconfigured"
    )
    assert finding.severity is PreflightSeverity.BLOCKER
    assert finding.title == "Air Assist output not configured"
    assert finding.context["layer_ids"] == (layer.id,)
    assert finding.context["layer_names"] == (layer.name,)


def test_configured_air_assist_output_satisfies_powered_request() -> None:
    document = _rectangle_document()
    document.layers[0].air_assist = True
    commands = AirAssistCommands(
        mode=AirAssistMode.GRBL_COOLANT,
        protocol="grbl",
        fan_index=None,
        on_commands=("M8",),
        off_commands=("M9",),
    )

    report = build_job_preflight_report(
        document,
        _context(air_assist_commands=commands),
    )

    assert "air_assist.output_unconfigured" not in _codes(report)
    assert report.ready


@pytest.mark.parametrize(
    ("power_percent", "layer_output", "layer_visible", "object_visible"),
    (
        (0.0, True, True, True),
        (20.0, False, True, True),
        (20.0, True, False, True),
        (20.0, True, True, False),
    ),
)
def test_air_assist_mapping_is_not_required_without_powered_visible_output(
    power_percent: float,
    layer_output: bool,
    layer_visible: bool,
    object_visible: bool,
) -> None:
    document = _rectangle_document(power_percent=power_percent)
    layer = document.layers[0]
    layer.air_assist = True
    layer.output_enabled = layer_output
    layer.visible = layer_visible
    document.objects[0].visible = object_visible

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)


def test_coincident_line_does_not_require_air_assist_mapping() -> None:
    document = ProjectDocument.new("Coincident line", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    line = SceneObject.line(layer.id, center=_DEFAULT_AREA.center)
    line.geometry["points"] = [[0.0, 0.0], [0.0, 0.0]]
    document.objects.append(line)

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)


def test_coincident_imported_path_does_not_require_air_assist_mapping() -> None:
    document = ProjectDocument.new("Coincident path", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject.path(
            layer.id,
            ({"points": [[10.0, 10.0], [10.0, 10.0]], "closed": False},),
            center=_DEFAULT_AREA.center,
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)


@pytest.mark.parametrize("kind", [ObjectKind.LINE, ObjectKind.PATH])
def test_sub_micron_serialized_vector_does_not_require_air_mapping(
    kind: ObjectKind,
) -> None:
    document = ProjectDocument.new("Serialized coincident", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    transform = Transform(50.0, 50.0, 10.0, 1.0)
    if kind is ObjectKind.LINE:
        item = SceneObject(
            name="Tiny line",
            kind=kind,
            layer_id=layer.id,
            transform=transform,
            geometry={"points": [[0.0, 0.0], [0.00001, 0.0]]},
        )
    else:
        item = SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (PathLineSegment((0.00001, 0.0)),),
                    ),
                )
            ),
            transform=transform,
        )
    document.objects.append(item)

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)


@pytest.mark.parametrize("kind", [ObjectKind.LINE, ObjectKind.PATH])
def test_one_micron_serialized_vector_still_requires_air_mapping(
    kind: ObjectKind,
) -> None:
    document = ProjectDocument.new("Serialized motion", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    transform = Transform(50.0, 50.0, 10.0, 1.0)
    if kind is ObjectKind.LINE:
        item = SceneObject(
            name="One micron line",
            kind=kind,
            layer_id=layer.id,
            transform=transform,
            geometry={"points": [[0.0, 0.0], [0.0001, 0.0]]},
        )
    else:
        item = SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (PathLineSegment((0.0001, 0.0)),),
                    ),
                )
            ),
            transform=transform,
        )
    document.objects.append(item)

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" in _codes(report)


@pytest.mark.parametrize("kind", [ObjectKind.RECTANGLE, ObjectKind.ELLIPSE])
@pytest.mark.parametrize("mode", [LayerMode.LINE, LayerMode.FILL, LayerMode.RASTER])
def test_sub_micron_primitive_has_no_serialized_powered_output(
    kind: ObjectKind,
    mode: LayerMode,
) -> None:
    document = ProjectDocument.new("Tiny primitive", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = mode
    layer.power_percent = 20.0
    layer.air_assist = True
    constructor = (
        SceneObject.rectangle if kind is ObjectKind.RECTANGLE else SceneObject.ellipse
    )
    document.objects.append(
        constructor(
            layer.id,
            center=_DEFAULT_AREA.center,
            width_mm=0.0001,
            height_mm=0.0001,
        )
    )

    report = build_job_preflight_report(document, _context())
    job = toolpath_module.generate_project_gcode(document, LaserSettings())
    validated = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            work_area=_DEFAULT_AREA,
        ),
        LaserSettings(),
        hardware_enabled=True,
    ).preflight_program(job.text)

    assert "air_assist.output_unconfigured" not in _codes(report)
    assert "\nM8\n" not in job.text
    assert not any(
        line.partition(";")[0].strip().startswith(("M3", "M4"))
        for line in job.text.splitlines()
    )
    assert job.plan is not None and not job.plan.powered
    assert not validated.requires_laser_authorization


@pytest.mark.parametrize("kind", [ObjectKind.RECTANGLE, ObjectKind.ELLIPSE])
@pytest.mark.parametrize("mode", [LayerMode.FILL, LayerMode.RASTER])
def test_sub_scanline_primitive_defers_no_rows_without_air_mapping(
    kind: ObjectKind,
    mode: LayerMode,
) -> None:
    document = ProjectDocument.new("Sub-scanline primitive", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = mode
    layer.line_interval_mm = 0.2
    layer.power_percent = 20.0
    layer.air_assist = True
    constructor = (
        SceneObject.rectangle if kind is ObjectKind.RECTANGLE else SceneObject.ellipse
    )
    document.objects.append(
        constructor(
            layer.id,
            center=(50.0, 50.0002),
            width_mm=10.0,
            height_mm=0.0001,
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)
    with pytest.raises(ValueError, match=rf"{mode.value.title()} produced no scanlines"):
        toolpath_module.generate_project_gcode(document, LaserSettings())


@pytest.mark.parametrize("kind", [ObjectKind.RECTANGLE, ObjectKind.ELLIPSE])
@pytest.mark.parametrize("mode", [LayerMode.FILL, LayerMode.RASTER])
def test_aligned_thin_primitive_proves_serialized_scan_span(
    kind: ObjectKind,
    mode: LayerMode,
) -> None:
    document = ProjectDocument.new("Aligned thin primitive", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = mode
    layer.line_interval_mm = 0.2
    layer.power_percent = 20.0
    layer.air_assist = True
    constructor = (
        SceneObject.rectangle if kind is ObjectKind.RECTANGLE else SceneObject.ellipse
    )
    document.objects.append(
        constructor(
            layer.id,
            center=(50.0, 50.0),
            width_mm=10.0,
            height_mm=0.0001,
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" in _codes(report)
    with pytest.raises(SafetyError, match="no resolved air-assist command mapping"):
        toolpath_module.generate_project_gcode(document, LaserSettings())


@pytest.mark.parametrize("mode", [LayerMode.FILL, LayerMode.RASTER])
def test_sub_scanline_linear_native_path_has_no_air_mapping_blocker(
    mode: LayerMode,
) -> None:
    document = ProjectDocument.new("Sub-scanline native path", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = mode
    layer.line_interval_mm = 0.2
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject.native_path(
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
            transform=Transform(50.0, 50.0002, 10.0, 0.0001),
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)
    with pytest.raises(ValueError, match=rf"{mode.value.title()} produced no scanlines"):
        toolpath_module.generate_project_gcode(document, LaserSettings())


@pytest.mark.parametrize(
    ("center_x", "spot_offset_x", "baseline_requires_mapping", "requires_mapping"),
    [
        pytest.param(50.0004, 0.0004, True, False, id="offset-collapses-step"),
        pytest.param(50.0, -0.0004, False, True, id="offset-creates-step"),
    ],
)
def test_spot_offset_controls_serialized_motion_classification(
    center_x: float,
    spot_offset_x: float,
    baseline_requires_mapping: bool,
    requires_mapping: bool,
) -> None:
    document = ProjectDocument.new("Offset threshold", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject(
            name="Threshold line",
            kind=ObjectKind.LINE,
            layer_id=layer.id,
            transform=Transform(center_x, 50.0, 10.0, 1.0),
            geometry={"points": [[0.0, 0.0], [0.00004, 0.0]]},
        )
    )

    baseline = build_job_preflight_report(document, _context())
    report = build_job_preflight_report(
        document,
        _context(spot_offset_x_mm=spot_offset_x),
    )

    assert (
        "air_assist.output_unconfigured" in _codes(baseline)
    ) is baseline_requires_mapping
    assert (
        "air_assist.output_unconfigured" in _codes(report)
    ) is requires_mapping
    laser = LaserSettings(spot_offset_x_mm=spot_offset_x)
    if requires_mapping:
        with pytest.raises(SafetyError, match="no resolved air-assist command mapping"):
            toolpath_module.generate_project_gcode(document, laser)
    else:
        job = toolpath_module.generate_project_gcode(document, laser)
        assert "\nM8\n" not in job.text
        assert job.plan is not None and not job.plan.powered


@pytest.mark.parametrize(
    ("center_x", "frame_origin_y", "requires_mapping"),
    [
        pytest.param(50.0004, -0.0004, False, id="frame-collapses-step"),
        pytest.param(50.0, 0.0004, True, id="frame-creates-step"),
    ],
)
def test_honeycomb_controller_transform_controls_serialized_motion(
    center_x: float,
    frame_origin_y: float,
    requires_mapping: bool,
) -> None:
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(100.0, frame_origin_y),
        x_axis_machine=(0.0, 1.0),
        y_axis_machine=(-1.0, 0.0),
        width_mm=100.0,
        height_mm=100.0,
        provenance_digest="ab" * 32,
    )
    document = ProjectDocument.new(
        "Honeycomb threshold",
        work_area=_DEFAULT_AREA,
        coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
    )
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject(
            name="Threshold line",
            kind=ObjectKind.LINE,
            layer_id=layer.id,
            transform=Transform(center_x, 50.0, 10.0, 1.0),
            geometry={"points": [[0.0, 0.0], [0.00004, 0.0]]},
        )
    )
    machine_area = Bounds(-1.0, -1.0, 101.0, 101.0)
    context = _context(
        machine_area,
        coordinate_frame=frame,
        honeycomb_execution_signature=(*frame.provenance_signature, "cd" * 32),
        expected_calibration_profile_id="camera-a",
        active_calibration_profile_id="camera-a",
        bed_calibration_state="VALID",
        honeycomb_support_state="CURRENT",
    )

    report = build_job_preflight_report(document, context)

    assert (
        "air_assist.output_unconfigured" in _codes(report)
    ) is requires_mapping
    if requires_mapping:
        with pytest.raises(SafetyError, match="no resolved air-assist command mapping"):
            toolpath_module.generate_project_gcode(
                document,
                LaserSettings(),
                coordinate_frame=frame,
                machine_work_area=machine_area,
            )
    else:
        job = toolpath_module.generate_project_gcode(
            document,
            LaserSettings(),
            coordinate_frame=frame,
            machine_work_area=machine_area,
        )
        assert "\nM8\n" not in job.text
        assert job.plan is not None and not job.plan.powered


def test_coincident_closed_polygon_does_not_require_air_assist_mapping() -> None:
    document = ProjectDocument.new("Coincident polygon", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = LayerMode.FILL
    layer.power_percent = 20.0
    layer.air_assist = True
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (0.0, 0.0),
                (PathLineSegment((0.0, 0.0)),),
                closed=True,
            ),
        )
    )
    document.objects.append(
        SceneObject(
            name="Coincident polygon",
            kind=ObjectKind.POLYGON,
            layer_id=layer.id,
            transform=Transform(50.0, 50.0, 20.0, 20.0),
            geometry=geometry.to_dict(),
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)
    assert "object.geometry_invalid" not in _codes(report)
    assert "object.closed_geometry_required" not in _codes(report)


def test_closed_out_and_back_fill_does_not_require_air_assist_mapping() -> None:
    document = ProjectDocument.new("Out and back fill", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = LayerMode.FILL
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (
                            PathLineSegment((1.0, 0.0)),
                            PathLineSegment((0.0, 0.0)),
                        ),
                        closed=True,
                    ),
                )
            ),
            transform=Transform(50.0, 50.0, 20.0, 20.0),
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)
    with pytest.raises(ValueError, match="Fill produced no scanlines"):
        toolpath_module.generate_project_gcode(document, LaserSettings())


def test_closed_out_and_back_line_still_requires_air_assist_mapping() -> None:
    document = ProjectDocument.new("Out and back line", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = LayerMode.LINE
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (
                            PathLineSegment((1.0, 0.0)),
                            PathLineSegment((0.0, 0.0)),
                        ),
                        closed=True,
                    ),
                )
            ),
            transform=Transform(50.0, 50.0, 20.0, 20.0),
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" in _codes(report)


def test_noncollinear_closed_fill_still_requires_air_assist_mapping() -> None:
    document = ProjectDocument.new("Triangle fill", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = LayerMode.FILL
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (
                            PathLineSegment((1.0, 0.0)),
                            PathLineSegment((0.5, 1.0)),
                            PathLineSegment((0.0, 0.0)),
                        ),
                        closed=True,
                    ),
                )
            ),
            transform=Transform(50.0, 50.0, 20.0, 20.0),
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" in _codes(report)


def test_constant_native_cubic_does_not_require_air_assist_mapping() -> None:
    document = ProjectDocument.new("Constant cubic", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (
                            PathCubicSegment(
                                (0.0, 0.0),
                                (0.0, 0.0),
                                (0.0, 0.0),
                            ),
                        ),
                    ),
                )
            ),
            transform=Transform(50.0, 50.0, 20.0, 20.0),
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)


def test_equal_endpoint_cubic_defers_mapping_to_exact_generation() -> None:
    document = ProjectDocument.new("Cubic loop", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (
                            PathCubicSegment(
                                (0.0, 0.5),
                                (1.0, 0.5),
                                (0.0, 0.0),
                            ),
                        ),
                    ),
                )
            ),
            transform=Transform(50.0, 50.0, 20.0, 20.0),
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)
    with pytest.raises(SafetyError, match="no resolved air-assist command mapping"):
        toolpath_module.generate_project_gcode(document, LaserSettings())


def test_control_only_cubic_rounding_emits_no_power_or_air_mapping_blocker() -> None:
    document = ProjectDocument.new("Rounded control-only cubic", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.power_percent = 20.0
    layer.air_assist = True
    document.objects.append(
        SceneObject.native_path(
            layer.id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (
                            PathCubicSegment(
                                (0.00002, 0.0),
                                (0.0, 0.0),
                                (0.0, 0.0),
                            ),
                        ),
                    ),
                )
            ),
            transform=Transform(50.00049, 50.0, 1.0, 1.0),
        )
    )

    report = build_job_preflight_report(document, _context())
    job = toolpath_module.generate_project_gcode(document, LaserSettings())

    assert "air_assist.output_unconfigured" not in _codes(report)
    assert "\nM8\n" not in job.text
    assert not any(
        line.partition(";")[0].strip().startswith(("M3", "M4"))
        for line in job.text.splitlines()
    )
    assert job.plan is not None and not job.plan.powered


def test_malformed_native_geometry_keeps_geometry_and_mapping_blockers() -> None:
    document = ProjectDocument.new("Malformed path", work_area=_DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = LayerMode.FILL
    layer.power_percent = 20.0
    layer.air_assist = True
    item = SceneObject.native_path(
        layer.id,
        NativePathGeometry(
            (
                PathSubpath(
                    (0.0, 0.0),
                    (PathLineSegment((1.0, 0.0)), PathLineSegment((0.0, 0.0))),
                    closed=True,
                ),
            )
        ),
        transform=Transform(50.0, 50.0, 20.0, 20.0),
    )
    item.geometry = {"path_version": 1}
    document.objects.append(item)

    report = build_job_preflight_report(document, _context())

    assert "object.geometry_invalid" in _codes(report)
    assert "air_assist.output_unconfigured" in _codes(report)


def test_machine_work_area_mismatch_is_a_blocker() -> None:
    report = build_job_preflight_report(
        _rectangle_document(),
        _context(Bounds(0.0, 0.0, 120.0, 100.0)),
    )

    assert report.has_blockers
    assert "work_area.mismatch" in _codes(report)


def test_honeycomb_local_project_without_frame_or_binding_is_blocked() -> None:
    document = _rectangle_document()
    document.coordinate_space = CoordinateSpace.HONEYCOMB_LOCAL

    report = build_job_preflight_report(
        document,
        _context(
            expected_calibration_profile_id="camera-a",
            active_calibration_profile_id="camera-a",
        ),
    )

    assert report.has_blockers
    assert "honeycomb.frame_missing" in _codes(report)
    assert "honeycomb.binding_missing" in _codes(report)


def test_valid_honeycomb_frame_requires_matching_support_bed_and_profile() -> None:
    digest = "ab" * 32
    bed_digest = "cd" * 32
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(10.0, 20.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=100.0,
        height_mm=100.0,
        provenance_digest=digest,
    )
    document = _rectangle_document()
    document.coordinate_space = CoordinateSpace.HONEYCOMB_LOCAL
    report = build_job_preflight_report(
        document,
        _context(
            coordinate_frame=frame,
            honeycomb_execution_signature=(*frame.provenance_signature, bed_digest),
            expected_calibration_profile_id="camera-a",
            active_calibration_profile_id="camera-a",
            bed_calibration_state="VALID",
            honeycomb_support_state="CURRENT",
        ),
    )

    assert report.ready
    assert not any(code.startswith("honeycomb.") for code in _codes(report))


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        (
            {
                "bed_calibration_state": "STALE",
                "bed_calibration_reasons": ("Bed-map dependency changed",),
                "honeycomb_support_state": "CURRENT",
            },
            "honeycomb.bed_calibration_not_valid",
        ),
        (
            {
                "bed_calibration_state": "VALID",
                "honeycomb_support_state": "STALE",
                "honeycomb_support_reasons": ("Configured span changed",),
            },
            "honeycomb.support_not_current",
        ),
    ],
)
def test_honeycomb_coordinate_readiness_states_are_blockers(
    changes: dict[str, object],
    expected_code: str,
) -> None:
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(10.0, 20.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=100.0,
        height_mm=100.0,
        provenance_digest="ab" * 32,
    )
    document = _rectangle_document()
    document.coordinate_space = CoordinateSpace.HONEYCOMB_LOCAL
    report = build_job_preflight_report(
        document,
        _context(
            coordinate_frame=frame,
            honeycomb_execution_signature=(*frame.provenance_signature, "cd" * 32),
            expected_calibration_profile_id="camera-a",
            active_calibration_profile_id="camera-a",
            **changes,
        ),
    )

    assert report.has_blockers
    finding = next(item for item in report.findings if item.code == expected_code)
    assert finding.detail


def test_missing_raster_source_is_blocked_without_decode(tmp_path: Path) -> None:
    document = ProjectDocument.new(
        "Missing raster",
        work_area=Bounds(0.0, 0.0, 100.0, 100.0),
    )
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    document.objects.append(
        SceneObject(
            name="Missing image",
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(50.0, 50.0, 20.0, 10.0),
            geometry={"asset": str(tmp_path / "missing.png")},
        )
    )

    report = build_job_preflight_report(document, _context())

    assert report.has_blockers
    assert "raster.source_unavailable" in _codes(report)


@pytest.mark.parametrize(
    ("pixel", "error_type", "message"),
    (
        (255, ValueError, "no engravable pixels after dithering"),
        (0, SafetyError, "no resolved air-assist command mapping"),
    ),
)
def test_image_air_assist_mapping_defers_to_exact_pixel_generation(
    tmp_path: Path,
    pixel: int,
    error_type: type[Exception],
    message: str,
) -> None:
    image_path = tmp_path / f"raster-{pixel}.png"
    assert cv2.imwrite(
        str(image_path),
        np.full((2, 2), pixel, dtype=np.uint8),
    )
    document = ProjectDocument.new("Raster assist", _DEFAULT_AREA)
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.air_assist = True
    layer.power_percent = 20.0
    layer.line_interval_mm = 1.0
    document.objects.append(
        SceneObject(
            name="Raster pixels",
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(50.0, 50.0, 2.0, 2.0),
            geometry={"asset": str(image_path)},
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "air_assist.output_unconfigured" not in _codes(report)
    with pytest.raises(error_type, match=message):
        toolpath_module.generate_project_gcode(document, LaserSettings())


def test_project_with_no_enabled_output_is_blocked() -> None:
    document = _rectangle_document()
    document.layers[0].output_enabled = False

    report = build_job_preflight_report(document, _context())

    assert report.has_blockers
    assert "output.layers_disabled" in _codes(report)
    assert "output.none_enabled" in _codes(report)


def test_generated_feed_rates_above_machine_ceilings_are_blockers() -> None:
    document = _rectangle_document()
    document.layers[0].speed_mm_min = 7000.0

    report = build_job_preflight_report(
        document,
        _context(
            machine_max_work_feed_mm_min=6000.0,
            machine_max_travel_feed_mm_min=6000.0,
            planned_travel_feed_mm_min=7000.0,
        ),
    )

    assert report.has_blockers
    assert "layer.work_feed_exceeds_machine_limit" in _codes(report)
    assert "travel.feed_exceeds_machine_limit" in _codes(report)


def test_finding_codes_and_order_are_stable_for_known_unsupported_content() -> None:
    document = ProjectDocument.new(
        "Unsupported",
        work_area=Bounds(0.0, 0.0, 100.0, 100.0),
    )
    document.objects.append(
        SceneObject(
            name="Image on line layer",
            kind=ObjectKind.IMAGE,
            layer_id=document.layers[0].id,
            transform=Transform(50.0, 50.0, 20.0, 10.0),
            geometry={"asset": "unused.png"},
        )
    )

    first = build_job_preflight_report(document, _context())
    second = build_job_preflight_report(document, _context())

    assert _codes(first) == _codes(second) == (
        "object.unsupported_layer_mode",
        "geometry.complex_bounds_deferred",
        "planner.exact_checks_deferred",
    )


def test_fill_preflight_rejects_an_open_native_cubic_path() -> None:
    document = ProjectDocument.new("Open fill", _DEFAULT_AREA)
    document.layers[0].mode = LayerMode.FILL
    document.objects.append(
        SceneObject.native_path(
            document.active_layer_id,
            NativePathGeometry(
                (
                    PathSubpath(
                        (0.0, 0.0),
                        (PathCubicSegment((0.2, 0.5), (0.8, 0.5), (1.0, 0.0)),),
                        closed=False,
                    ),
                )
            ),
            transform=Transform(50.0, 50.0, 20.0, 10.0),
        )
    )

    report = build_job_preflight_report(document, _context())

    assert report.has_blockers
    assert "object.closed_geometry_required" in _codes(report)


def test_aggregate_image_raster_sample_limit_uses_shared_planner_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = ProjectDocument.new(
        "Large raster",
        work_area=Bounds(0.0, 0.0, 1000.0, 1000.0),
    )
    layer = document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.line_interval_mm = 0.01
    document.objects.append(
        SceneObject(
            name="Dense image",
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(500.0, 500.0, 500.0, 500.0),
            geometry={"asset": "dense.png"},
        )
    )

    def metadata(path: str | Path) -> RasterAssetMetadata:
        return RasterAssetMetadata(
            path=str(Path(path).absolute()),
            format="png",
            width=10,
            height=10,
            raw_width=10,
            raw_height=10,
            bit_depth=8,
            channels=3,
            orientation=1,
            encoded_bytes=100,
            decoded_bytes=400,
            mtime_ns=1,
        )

    monkeypatch.setattr(preflight_module, "probe_raster_asset", metadata)

    report = build_job_preflight_report(
        document,
        _context(Bounds(0.0, 0.0, 1000.0, 1000.0)),
    )

    finding = next(
        item
        for item in report.findings
        if item.code == "raster.aggregate_samples_exceeded"
    )
    assert finding.severity is PreflightSeverity.BLOCKER
    assert finding.context["actual"] > MAX_RASTER_SAMPLES
    assert finding.context["limit"] == MAX_RASTER_SAMPLES


def test_preflight_never_constructs_objects_flattens_plans_decodes_or_calls_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _rectangle_document()

    def unexpected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("advisory preflight crossed an authoritative boundary")

    monkeypatch.setattr(SceneObject, "__init__", unexpected)
    monkeypatch.setattr(toolpath_module, "object_polylines", unexpected)
    monkeypatch.setattr(toolpath_module, "generate_project_gcode", unexpected)
    monkeypatch.setattr(raster_asset_module, "decode_raster_grayscale", unexpected)
    monkeypatch.setattr(MachineService, "status", unexpected)
    monkeypatch.setattr(MachineService, "prepare_job_start", unexpected)
    monkeypatch.setattr(MachineService, "preflight_program", unexpected)

    report = build_job_preflight_report(document, _context())

    assert report.ready


def test_mutated_invalid_enums_and_numeric_settings_stay_structured() -> None:
    document = _rectangle_document()
    document.layers[0].power_percent = "bad"  # type: ignore[assignment]
    document.layers[0].overscan_percent = "bad"  # type: ignore[assignment]
    document.layers[0].priority = "bad"  # type: ignore[assignment]
    document.layers[0].air_assist = "bad"  # type: ignore[assignment]
    document.objects[0].kind = "unknown"  # type: ignore[assignment]

    report = build_job_preflight_report(document, _context())

    assert report.has_blockers
    assert "layer.setting_invalid" in _codes(report)
    assert "object.kind_invalid" in _codes(report)


def test_exact_simple_shape_bounds_block_out_of_area_without_flattening() -> None:
    document = _rectangle_document()
    document.objects[0].transform.x_mm = 99.0

    report = build_job_preflight_report(document, _context())

    assert "geometry.local_bounds_outside_work_area" in _codes(report)


def test_approximated_shape_envelopes_are_deferred_to_exact_planning() -> None:
    document = ProjectDocument.new("Rounded", work_area=_DEFAULT_AREA)
    rounded = SceneObject.rectangle(
        document.layers[0].id,
        center=(87.0, 50.0),
        width_mm=20.0,
        height_mm=20.0,
        corner_radius_mm=9.0,
    )
    rounded.transform.rotation_deg = 45.0
    document.objects.append(rounded)

    report = build_job_preflight_report(document, _context())

    assert report.ready
    assert "geometry.local_bounds_outside_work_area" not in _codes(report)
    assert "geometry.complex_bounds_deferred" in _codes(report)
    job = toolpath_module.generate_project_gcode(
        document,
        LaserSettings(boundary_margin_mm=0.0),
    )
    assert job.point_count > 0


def test_image_transform_envelope_is_not_treated_as_commanded_bounds() -> None:
    document = ProjectDocument.new("Raster envelope", work_area=_DEFAULT_AREA)
    document.layers[0].mode = LayerMode.RASTER
    document.objects.append(
        SceneObject(
            name="Sparse image",
            kind=ObjectKind.IMAGE,
            layer_id=document.layers[0].id,
            transform=Transform(99.0, 50.0, 20.0, 20.0),
            geometry={"asset": "missing.png"},
        )
    )

    report = build_job_preflight_report(document, _context())

    assert "geometry.local_bounds_outside_work_area" not in _codes(report)
    assert "geometry.complex_bounds_deferred" in _codes(report)
    assert "raster.source_unavailable" in _codes(report)


def test_structured_preflight_polls_cooperative_cancellation() -> None:
    document = _rectangle_document()
    checks = 0

    def cancel_after_work_starts() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 4

    with pytest.raises(JobPreflightCancelled, match="cancelled"):
        build_job_preflight_report(
            document,
            _context(),
            cancel_check=cancel_after_work_starts,
        )

    assert checks >= 4
    assert build_job_preflight_report(
        document,
        _context(),
        cancel_check=lambda: False,
    ) == build_job_preflight_report(document, _context())
