from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import LaserSettings, WorkArea
from laser_aligner.machine.service import MachineService
from laser_aligner.project import job_preflight as preflight_module
from laser_aligner.project import raster_asset as raster_asset_module
from laser_aligner.project import toolpath as toolpath_module
from laser_aligner.project.job_preflight import (
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
