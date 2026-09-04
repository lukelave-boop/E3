import json
import time
from copy import deepcopy
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.bed import BedCalibration, BedMapper, BedPoint
from laser_aligner.calibration.registration import (
    accuracy_validation_targets,
    analyze_accuracy_measurements,
    analyze_dense_mesh_measurements,
    analyze_dense_validation_refinement,
    analyze_homography_refinement,
    analyze_registration_measurements,
    dense_confirmation_targets,
    dense_mesh_targets,
    dense_validation_targets,
    generate_registration_program,
    registration_targets,
    review_registration_measurements,
    suggested_registration_exclusions,
    targets_fit_support,
)
from laser_aligner.calibration.support import HoneycombSupportReference
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.service import FrameBurst
from laser_aligner.config import (
    BedCalibrationSettings,
    LaserSettings,
    WorkArea,
    load_settings,
)
from laser_aligner.errors import CalibrationError
from laser_aligner.machine.controller_dialects import GRBL_DIALECT
from tests.fakes.simulator_transport import SimulatedTransport


def _run_prepared_job(context: AppContext, job: object) -> None:
    if not context.machine.connected:
        transport = SimulatedTransport()
        transport.open()
        context.machine._transport = transport
        context.machine._connected = True
        context.machine._dialect = GRBL_DIALECT
        context.machine._protocol = "grbl"
        context.machine._coordinate_reference_ready = True
        context.machine._coordinate_state_reference = {
            "active_workspace": "G54",
            "active_offset_mm": [0.0, 0.0, 0.0],
            "g92_offset_mm": [0.0, 0.0, 0.0],
        }
        context.machine._jog_position_mm = (0.0, 0.0)
        with context.machine._lock:
            session = context.machine._adopt_legacy_test_session_locked()
            assert session is not None
            context.machine._coordinate_reference_session_generation = session.generation
    program = context.machine.preflight_program(job.program.text)
    context.machine.arm_program(context.machine.ARM_PHRASE, program)
    context.machine.start_validated_program(program, job.filename)
    deadline = time.monotonic() + 15.0
    while context.machine.status()["job"]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    status = context.machine.status()
    assert status["job"]["running"] is False
    assert status["job"]["error"] is None, status["controller_diagnostics"]


def _save_execution_support(context: AppContext) -> HoneycombSupportReference:
    """Store an automatic four-corner support inside the guarded machine area."""

    _install_test_bed(context)
    calibration = context.bed.calibration
    assert calibration is not None
    area = context.settings.machine.work_area
    margin = context.settings.laser.boundary_margin_mm
    inset = max(10.0, margin + 5.0)
    side = min(190.0, area.width - 2.0 * inset, area.height - 2.0 * inset)
    origin = (area.x_min + inset, area.y_min + inset)
    reference = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            origin,
            (origin[0] + side, origin[1]),
            (origin[0] + side, origin[1] + side),
            (origin[0], origin[1] + side),
        ),
        corner_topology=(0, 1, 2, 3),
        support_width_mm=side,
        support_height_mm=side,
        bed_calibration_created_at=calibration.created_at,
    )
    context.honeycomb_support.save(reference)
    return reference


def _install_test_bed(context: AppContext) -> None:
    """Install explicit physical-coordinate evidence for this test fixture."""

    context.machine.hardware_enabled = True
    context.machine.settings.allow_motion = True
    if context.bed.calibration is not None:
        return
    area = context.settings.machine.work_area
    width = context.settings.camera.width
    height = context.settings.camera.height
    context.bed.replace_points_and_solve(
        [
            BedPoint(0, height - 1, area.x_min, area.y_min),
            BedPoint(width - 1, height - 1, area.x_max, area.y_min),
            BedPoint(width - 1, 0, area.x_max, area.y_max),
            BedPoint(0, 0, area.x_min, area.y_max),
        ],
        width,
        height,
    )


def test_dense_mesh_targets_form_complete_five_by_five_grid() -> None:
    targets = dense_mesh_targets(WorkArea(x_min=10, x_max=210, y_min=10, y_max=210))
    assert len(targets) == 25
    assert len({target.machine_x for target in targets}) == 5
    assert len({target.machine_y for target in targets}) == 5


def test_powered_dense_fit_spans_180mm_when_support_polygon_authorizes_it(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "open_browser": False},
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
                "laser": {
                    "guarded_output_polygon_mm": [
                        [0.0, 0.0],
                        [210.0, 0.0],
                        [210.0, 210.0],
                        [0.0, 210.0],
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _save_execution_support(context)
        job = context.prepare_dense_calibration_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
        )

        xs = sorted({target.machine_x for target in job.targets})
        ys = sorted({target.machine_y for target in job.targets})
        assert len(xs) == len(ys) == 5
        assert xs[-1] - xs[0] == pytest.approx(180.0)
        assert ys[-1] - ys[0] == pytest.approx(180.0)
        assert job.guarded_output_polygon_mm == (
            (0.0, 0.0),
            (210.0, 0.0),
            (210.0, 210.0),
            (0.0, 210.0),
        )
        session = json.loads(context.dense_calibration_path.read_text(encoding="utf-8"))
        assert session["guarded_output_polygon_mm"] == [
            [0.0, 0.0],
            [210.0, 0.0],
            [210.0, 210.0],
            [0.0, 210.0],
        ]
    finally:
        context.stop()


def test_dense_fit_and_validation_sessions_do_not_overwrite_each_other(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "open_browser": False},
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _save_execution_support(context)
        fit_job = context.prepare_dense_calibration_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        fit_session = json.loads(context.dense_calibration_path.read_text(encoding="utf-8"))
        nodes = np.asarray(sorted({target.machine_x for target in fit_job.targets}))
        context.bed.apply_residual_mesh(
            nodes,
            nodes,
            np.zeros((5, 5, 2), dtype=np.float64),
            fit_rms_mm=0.0,
            fit_max_mm=0.0,
        )
        _save_execution_support(context)
        validation_job = context.prepare_dense_calibration_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
            validation=True,
        )
        _run_prepared_job(context, validation_job)

        assert len(fit_job.targets) == 25
        assert len(validation_job.targets) == 16
        assert json.loads(context.dense_calibration_path.read_text(encoding="utf-8")) == fit_session
        validation_session = json.loads(context.dense_validation_path.read_text(encoding="utf-8"))
        assert len(validation_session["targets"]) == 16
        assert validation_session["validation"] is True
        assert validation_session["confirmation"] is False

        active_mesh = context.bed.calibration.residual_mesh
        context.bed.refine_residual_mesh(
            np.zeros((5, 5, 2), dtype=np.float64),
            analyzed_mesh_created_at=active_mesh.created_at,
            predicted_rms_mm=0.0,
            predicted_max_mm=0.0,
        )
        with pytest.raises(
            CalibrationError,
            match="These 16 interstitial marks belong.*before the validation refinement",
        ):
            context.analyze_dense_calibration_image(
                    np.zeros((600, 800, 3), dtype=np.uint8),
                validation=True,
            )
    finally:
        context.stop()


def test_legacy_shared_dense_validation_session_is_preserved(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"app": {"data_dir": "data", "open_browser": False}}),
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    legacy = {
        "schema_version": 1,
        "powered": True,
        "validation": True,
        "confirmation": False,
        "targets": [{"id": value} for value in range(16)],
    }
    (settings.app.data_dir / "dense_calibration.json").write_text(
        json.dumps(legacy),
        encoding="utf-8",
    )

    context = AppContext(settings)

    assert json.loads(context.dense_validation_path.read_text(encoding="utf-8")) == legacy
    assert json.loads(context.dense_calibration_path.read_text(encoding="utf-8")) == legacy


def test_malformed_confirmation_session_is_repaired_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"app": {"data_dir": "data", "open_browser": False}}),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    calibration = context.bed.calibration
    assert calibration is None
    context.start()
    try:
        _save_execution_support(context)
        fit_job = context.prepare_dense_calibration_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        nodes = np.asarray(sorted({target.machine_x for target in fit_job.targets}))
        context.bed.apply_residual_mesh(
            nodes,
            nodes,
            np.zeros((5, 5, 2), dtype=np.float64),
            fit_rms_mm=0.0,
            fit_max_mm=0.0,
        )
        context.bed.refine_residual_mesh(
            np.zeros((5, 5, 2), dtype=np.float64),
            analyzed_mesh_created_at=context.bed.calibration.residual_mesh.created_at,
            predicted_rms_mm=0.0,
            predicted_max_mm=0.0,
        )
        _save_execution_support(context)
        confirmation_job = context.prepare_dense_calibration_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
            confirmation=True,
        )
        _run_prepared_job(context, confirmation_job)
        malformed = json.loads(context.dense_confirmation_path.read_text(encoding="utf-8"))
        malformed["validation"] = True
        context.dense_confirmation_path.write_text(json.dumps(malformed), encoding="utf-8")

        observed: dict[str, int] = {}

        def detect(_image, _expected, *, search_radius_px):
            observed["search_radius_px"] = search_radius_px
            return {"detected": False, "reason": "fixture", "points": []}

        monkeypatch.setattr("laser_aligner.app.detect_crosshairs_near", detect)
        image = np.zeros((600, 800, 3), dtype=np.uint8)
        context.analyze_dense_calibration_image(image, confirmation=True)

        repaired = json.loads(context.dense_confirmation_path.read_text(encoding="utf-8"))
        assert repaired["validation"] is False
        assert repaired["confirmation"] is True
        # The explicit test mapping projects the Step 5 machine-space gate to 18 px.
        assert observed["search_radius_px"] == 18
    finally:
        context.stop()


def test_shifted_confirmation_targets_are_distinct_from_fit_and_refinement() -> None:
    area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    fit = {(item.machine_x, item.machine_y) for item in dense_mesh_targets(area)}
    refinement = {(item.machine_x, item.machine_y) for item in dense_validation_targets(area)}
    confirmation = dense_confirmation_targets(area)
    confirmation_points = {(item.machine_x, item.machine_y) for item in confirmation}
    assert len(confirmation) == 16
    assert confirmation_points.isdisjoint(fit)
    assert confirmation_points.isdisjoint(refinement)


def test_validation_refinement_predicts_removal_of_consistent_y_bias() -> None:
    area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    targets = dense_validation_targets(area)
    measurements = [
        {
            "id": item.id,
            "machine_x": item.machine_x,
            "machine_y": item.machine_y,
            "observed_x": item.machine_x + 0.05,
            "observed_y": item.machine_y + 0.45,
            "score": 1.0,
            "seed_shift_px": 1.0,
        }
        for item in targets
    ]
    nodes = np.asarray([40.0, 75.0, 110.0, 145.0, 180.0])
    result = analyze_dense_validation_refinement(measurements, nodes, nodes)
    assert result["can_refine"] is True
    assert result["predicted_rms_mm"] < 0.20
    assert result["update_max_mm"] < 1.5


def test_validation_refinement_rejects_nonfinite_confidence_and_wrong_cell_identity() -> None:
    targets = dense_validation_targets(WorkArea(x_min=10, x_max=210, y_min=10, y_max=210))
    measurements = [
        {
            "id": item.id,
            "machine_x": item.machine_x,
            "machine_y": item.machine_y,
            "observed_x": item.machine_x,
            "observed_y": item.machine_y,
            "score": 1.0,
            "seed_shift_px": 1.0,
        }
        for item in targets
    ]
    nodes = np.asarray([40.0, 75.0, 110.0, 145.0, 180.0])
    measurements[0]["score"] = float("nan")
    with pytest.raises(CalibrationError, match="confidence metadata"):
        analyze_dense_validation_refinement(measurements, nodes, nodes)

    measurements[0]["score"] = 1.0
    measurements[0]["machine_x"] = measurements[1]["machine_x"]
    with pytest.raises(CalibrationError, match="one correctly identified point per mesh cell"):
        analyze_dense_validation_refinement(measurements, nodes, nodes)


def test_dense_mesh_analysis_fits_bounded_position_dependent_correction() -> None:
    targets = dense_mesh_targets(WorkArea(x_min=10, x_max=210, y_min=10, y_max=210))
    measurements = []
    for target in targets:
        error_x = 0.0015 * (target.machine_y - 110.0)
        error_y = 0.45 - 0.002 * (target.machine_y - 110.0)
        measurements.append(
            {
                "id": target.id,
                "machine_x": target.machine_x,
                "machine_y": target.machine_y,
                "observed_x": target.machine_x + error_x,
                "observed_y": target.machine_y + error_y,
                "score": 1.0,
                "seed_shift_px": 1.0,
            }
        )
    result = analyze_dense_mesh_measurements(measurements)
    assert result["can_apply"] is True
    assert np.asarray(result["corrections_mm"]).shape == (5, 5, 2)


def test_dense_mesh_analysis_rejects_nonfinite_confidence_and_wrong_grid_identity() -> None:
    targets = dense_mesh_targets(WorkArea(x_min=10, x_max=210, y_min=10, y_max=210))
    measurements = [
        {
            "id": target.id,
            "machine_x": target.machine_x,
            "machine_y": target.machine_y,
            "observed_x": target.machine_x,
            "observed_y": target.machine_y,
            "score": 1.0,
            "seed_shift_px": 1.0,
        }
        for target in targets
    ]
    measurements[3]["seed_shift_px"] = float("nan")
    with pytest.raises(CalibrationError, match="confidence metadata"):
        analyze_dense_mesh_measurements(measurements)

    measurements[3]["seed_shift_px"] = 1.0
    measurements[0]["machine_x"] = measurements[1]["machine_x"]
    with pytest.raises(CalibrationError, match="correctly identified regular"):
        analyze_dense_mesh_measurements(measurements)


def test_dense_mesh_analysis_infers_one_occluded_grid_cell() -> None:
    targets = dense_mesh_targets(WorkArea(x_min=10, x_max=210, y_min=10, y_max=210))
    measurements = [
        {
            "id": target.id,
            "machine_x": target.machine_x,
            "machine_y": target.machine_y,
            "observed_x": target.machine_x + (4.0 if target.id == 1 else 0.2),
            "observed_y": target.machine_y,
            "score": 1.0,
            "seed_shift_px": 1.0,
        }
        for target in targets
    ]

    result = analyze_dense_mesh_measurements(measurements)

    assert result["can_apply"] is True
    assert result["over_bound_ids"] == [1]
    assert result["inferred_ids"] == [1]
    assert result["measurements"][0]["over_correction_bound"] is True
    assert result["measurements"][0]["inferred"] is True
    assert "#1" in result["reason"]


def test_dense_mesh_analysis_rejects_two_unreliable_grid_cells() -> None:
    targets = dense_mesh_targets(WorkArea(x_min=10, x_max=210, y_min=10, y_max=210))
    measurements = [
        {
            "id": target.id,
            "machine_x": target.machine_x,
            "machine_y": target.machine_y,
            "observed_x": target.machine_x + (4.0 if target.id in {1, 25} else 0.2),
            "observed_y": target.machine_y,
            "score": 1.0,
            "seed_shift_px": 1.0,
        }
        for target in targets
    ]

    result = analyze_dense_mesh_measurements(measurements)

    assert result["can_apply"] is False
    assert result["unreliable_ids"] == [1, 25]
    assert result["inferred_ids"] == []
    assert result["rejected_ids"] == [1, 25]
    assert result["measurements"][0]["rejected"] is True
    assert result["measurements"][-1]["rejected"] is True
    assert "More than one" in result["reason"]


def _measurements(errors: list[tuple[float, float]]) -> list[dict[str, float]]:
    commanded = [
        (35.0, 35.0),
        (185.0, 35.0),
        (85.0, 85.0),
        (185.0, 85.0),
        (35.0, 135.0),
        (135.0, 135.0),
        (35.0, 185.0),
        (185.0, 185.0),
    ]
    return [
        {
            "id": index,
            "machine_x": x,
            "machine_y": y,
            "observed_x": x + error_x,
            "observed_y": y + error_y,
        }
        for index, ((x, y), (error_x, error_y)) in enumerate(zip(commanded, errors, strict=True), start=1)
    ]


def test_registration_program_has_sparse_safe_dry_and_powered_variants() -> None:
    work_area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    laser = LaserSettings(
        power_max=1000,
        boundary_margin_mm=5,
        travel_feed_mm_min=2000,
    )
    targets = registration_targets(work_area, mark_size_mm=5, boundary_margin_mm=laser.boundary_margin_mm)

    assert len(targets) == 8
    assert len({(target.machine_x, target.machine_y) for target in targets}) == 8
    assert (targets[6].machine_x, targets[6].machine_y) == pytest.approx((85, 185))
    assert all(work_area.contains(target.machine_x, target.machine_y, 7.5) for target in targets)

    dry = generate_registration_program(
        targets,
        laser,
        work_area,
        mark_size_mm=5,
        power_percent=10,
        powered=False,
        speed_mm_min=1200,
    )
    assert "M3 " not in dry.text
    assert "M4 " not in dry.text
    assert dry.text.rstrip().endswith("M5\n; End of generated job")
    assert dry.path_count == 16

    powered = generate_registration_program(
        targets,
        laser,
        work_area,
        mark_size_mm=5,
        power_percent=10,
        powered=True,
        speed_mm_min=1200,
    )
    assert powered.text.count("M4 S100") == 16
    assert not any(
        line.strip().split(maxsplit=1)[0] in {"M8", "M9", "M106", "M107"}
        for line in powered.text.splitlines()
        if line.strip()
    )
    assert powered.bounds_mm == pytest.approx((32.5, 32.5, 187.5, 187.5))


def test_registration_targets_follow_and_fit_detected_honeycomb() -> None:
    work_area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    support = HoneycombSupportReference.from_observations(
        ruler_origin_machine_mm=(29.2, 37.3),
        ruler_x_mark_machine_mm=(219.2, 40.8),
        ruler_xy_mark_machine_mm=(217.6, 230.8),
        ruler_mark_mm=190.0,
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=1.0,
    )

    targets = registration_targets(
        work_area,
        mark_size_mm=5.0,
        boundary_margin_mm=5.0,
        support_reference=support,
    )

    assert min(target.machine_y for target in targets) > 60.0
    assert targets_fit_support(targets, support, 7.5)
    assert all(work_area.contains(target.machine_x, target.machine_y, 7.5) for target in targets)


def test_powered_registration_requires_a_nonzero_verified_power() -> None:
    work_area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    targets = registration_targets(work_area)
    with pytest.raises(CalibrationError, match="verified visible-marking power"):
        generate_registration_program(
            targets,
            LaserSettings(),
            work_area,
            mark_size_mm=5,
            power_percent=0,
            powered=True,
            speed_mm_min=1200,
        )


def test_accuracy_validation_uses_five_distinct_holdout_targets() -> None:
    area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    holdouts = accuracy_validation_targets(area, mark_size_mm=5)
    registration = registration_targets(area, mark_size_mm=5)

    assert [(item.machine_x, item.machine_y) for item in holdouts] == pytest.approx(
        [(60, 60), (160, 60), (110, 110), (60, 160), (160, 160)]
    )
    assert not {(item.machine_x, item.machine_y) for item in holdouts}.intersection(
        (item.machine_x, item.machine_y) for item in registration
    )


def test_accuracy_validation_reports_pass_and_fail_against_fixed_limits() -> None:
    passing = analyze_accuracy_measurements(
        [
            {
                "id": index,
                "machine_x": float(index * 20),
                "machine_y": float(index * 25),
                "observed_x": float(index * 20 + 0.2),
                "observed_y": float(index * 25 - 0.1),
            }
            for index in range(1, 6)
        ]
    )
    failing_measurements = [dict(item) for item in passing["measurements"]]
    failing_measurements[-1]["observed_x"] += 1.5
    failing = analyze_accuracy_measurements(failing_measurements)

    assert passing["passed"] is True
    assert passing["rms_limit_mm"] == pytest.approx(0.5)
    assert passing["max_limit_mm"] == pytest.approx(1.0)
    assert failing["passed"] is False
    assert failing["classification"] == "fail"


def test_accuracy_validation_rejects_low_confidence_holdout() -> None:
    measurements = [
        {
            "id": index,
            "machine_x": float(index * 20),
            "machine_y": float(index * 25),
            "observed_x": float(index * 20 + 0.1),
            "observed_y": float(index * 25),
            "seed_shift_px": 55.0 if index == 3 else 2.0,
        }
        for index in range(1, 6)
    ]

    result = analyze_accuracy_measurements(measurements)

    assert result["classification"] == "invalid"
    assert result["passed"] is False
    assert result["low_confidence_ids"] == [3]


def test_accuracy_validation_rejects_nonfinite_confidence_metadata() -> None:
    measurements = [
        {
            "id": index,
            "machine_x": float(index * 20),
            "machine_y": float(index * 25),
            "observed_x": float(index * 20),
            "observed_y": float(index * 25),
            "score": float("nan") if index == 2 else 1.0,
        }
        for index in range(1, 6)
    ]

    with pytest.raises(CalibrationError, match="confidence metadata"):
        analyze_accuracy_measurements(measurements)


def test_registration_analysis_accepts_only_consistent_translation() -> None:
    analysis = analyze_registration_measurements(_measurements([(-3.0, -0.75)] * 8))

    assert analysis["classification"] == "translation"
    assert analysis["can_apply_translation"] is True
    assert analysis["correction_x_mm"] == pytest.approx(3.0)
    assert analysis["correction_y_mm"] == pytest.approx(0.75)
    assert analysis["scatter_rms_mm"] == pytest.approx(0.0)


def test_registration_analysis_rejects_position_dependent_error() -> None:
    analysis = analyze_registration_measurements(
        _measurements(
            [
                (-3.0, -1.0),
                (2.0, -1.0),
                (-2.0, 1.5),
                (2.0, 1.5),
                (-3.0, -2.0),
                (2.5, 2.0),
                (-2.5, 2.5),
                (3.0, -2.5),
            ]
        )
    )

    assert analysis["classification"] == "position_dependent"
    assert analysis["can_apply_translation"] is False


def test_registration_review_can_exclude_one_clear_detection_outlier() -> None:
    errors = [(-3.0, -0.75)] * 8
    errors[6] = (-9.0, -12.0)
    all_measurements = analyze_registration_measurements(_measurements(errors))["measurements"]
    all_measurements[6]["seed_shift_px"] = 60.0

    suggested = suggested_registration_exclusions(all_measurements)
    reviewed = review_registration_measurements(all_measurements, suggested)

    assert suggested == [7]
    assert reviewed["classification"] == "translation"
    assert reviewed["can_apply_translation"] is True
    assert reviewed["excluded_ids"] == [7]
    assert reviewed["point_count"] == 7
    assert reviewed["correction_x_mm"] == pytest.approx(3.0)
    assert reviewed["correction_y_mm"] == pytest.approx(0.75)


def test_registration_review_rejects_excluding_more_than_two_marks() -> None:
    measurements = analyze_registration_measurements(_measurements([(-3.0, -0.75)] * 8))["measurements"]

    with pytest.raises(CalibrationError, match="At most two"):
        review_registration_measurements(measurements, [1, 2, 3])


def test_registration_review_rejects_nonfinite_confidence_metadata() -> None:
    measurements = analyze_registration_measurements(
        _measurements([(-3.0, -0.75)] * 8)
    )["measurements"]
    measurements[4]["seed_shift_px"] = float("nan")

    with pytest.raises(CalibrationError, match="confidence metadata"):
        review_registration_measurements(measurements, [])


def _homography_measurements(
    homography: np.ndarray,
) -> list[dict[str, float]]:
    measurements = _measurements([(0.0, 0.0)] * 8)
    targets = np.asarray(
        [[item["machine_x"], item["machine_y"]] for item in measurements],
        dtype=np.float64,
    )
    image_points = cv2.perspectiveTransform(targets.reshape(-1, 1, 2), np.linalg.inv(homography)).reshape(-1, 2)
    for item, image_point in zip(measurements, image_points, strict=True):
        item["image_x"] = float(image_point[0])
        item["image_y"] = float(image_point[1])
        item["observed_x"] = float(image_point[0])
        item["observed_y"] = float(image_point[1])
    return analyze_registration_measurements(measurements)["measurements"]


def test_full_map_refinement_accepts_broad_seven_inlier_fit() -> None:
    proposed = np.asarray(
        [[1.01, 0.004, 1.5], [-0.003, 0.99, 1.0], [0.00001, -0.00001, 1.0]],
        dtype=np.float64,
    )
    measurements = _homography_measurements(proposed)
    measurements[5]["image_x"] += 4.0
    measurements[5]["observed_x"] += 4.0

    result = analyze_homography_refinement(
        measurements,
        [],
        np.eye(3),
        WorkArea(x_min=10, x_max=210, y_min=10, y_max=210),
    )

    assert result["can_apply_full_map"] is True
    assert result["inlier_count"] == 7
    assert result["ransac_outlier_ids"] == [6]
    assert result["rms_error_mm"] < 0.01
    assert result["coverage_ratio"] >= 0.35


def test_full_map_refinement_accepts_support_constrained_sixty_nine_percent_span() -> None:
    measurements = _homography_measurements(np.eye(3))
    center = 110.0
    scale = 0.69 / 0.75
    for item in measurements:
        item["machine_x"] = center + (float(item["machine_x"]) - center) * scale
        item["machine_y"] = center + (float(item["machine_y"]) - center) * scale
        item["image_x"] = item["machine_x"]
        item["image_y"] = item["machine_y"]
        item["observed_x"] = item["machine_x"]
        item["observed_y"] = item["machine_y"]

    result = analyze_homography_refinement(
        measurements,
        [],
        np.eye(3),
        WorkArea(x_min=10, x_max=210, y_min=10, y_max=210),
    )

    assert result["can_apply_full_map"] is True
    assert result["span_x_ratio"] == pytest.approx(0.69)
    assert result["span_y_ratio"] == pytest.approx(0.69)
    assert result["coverage_ratio"] >= 0.35


def test_reset_fine_registration_recovers_saved_review_after_dialog_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "open_browser": False},
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    measurements = [{"id": index} for index in range(1, 9)]
    context.fine_registration_path.parent.mkdir(parents=True, exist_ok=True)
    context.fine_registration_path.write_text(
        json.dumps({"measurements": measurements, "analysis": {"excluded_ids": [8]}}),
        encoding="utf-8",
    )
    reviewed = {"full_map_refinement": {"can_apply_full_map": True}}
    review_calls: list[tuple[list[dict[str, int]], list[int]]] = []

    class CalibrationResult:
        @staticmethod
        def to_dict() -> dict[str, object]:
            return {"fine_registration": {"translation_x_mm": 0.0, "translation_y_mm": 0.0}}

    monkeypatch.setattr(context.bed, "reset_registration_translation", CalibrationResult)
    monkeypatch.setattr(context.honeycomb_support, "clear", lambda: None)

    def review(items: list[dict[str, int]], excluded: list[int]) -> dict[str, object]:
        review_calls.append((items, excluded))
        return reviewed

    monkeypatch.setattr(context, "review_fine_registration_measurements", review)

    result = context.reset_fine_registration()

    assert review_calls == [(measurements, [8])]
    assert result["review_measurements"] == measurements
    assert result["review_analysis"] is reviewed


def test_full_map_refinement_rejects_six_inlier_fit() -> None:
    proposed = np.asarray(
        [[1.01, 0.004, 1.5], [-0.003, 0.99, 1.0], [0.00001, -0.00001, 1.0]],
        dtype=np.float64,
    )
    measurements = _homography_measurements(proposed)
    measurements[5]["image_x"] += 4.0
    measurements[5]["observed_x"] += 4.0
    measurements[6]["image_y"] += 5.0
    measurements[6]["observed_y"] += 5.0

    result = analyze_homography_refinement(
        measurements,
        [],
        np.eye(3),
        WorkArea(x_min=10, x_max=210, y_min=10, y_max=210),
    )

    assert result["can_apply_full_map"] is False
    assert result["inlier_count"] < 7
    assert "at least 7 geometric inliers" in result["reason"]


def test_full_map_refinement_rejects_low_confidence_selected_mark() -> None:
    proposed = np.asarray(
        [[1.01, 0.004, 1.5], [-0.003, 0.99, 1.0], [0.00001, -0.00001, 1.0]],
        dtype=np.float64,
    )
    measurements = _homography_measurements(proposed)
    measurements[0]["seed_shift_px"] = 55.0

    result = analyze_homography_refinement(
        measurements,
        [],
        np.eye(3),
        WorkArea(x_min=10, x_max=210, y_min=10, y_max=210),
    )

    assert result["can_apply_full_map"] is False
    assert "low confidence" in result["reason"]


def test_bed_registration_translation_persists_and_can_be_reset(
    tmp_path: Path,
) -> None:
    area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), area)
    for point in (
        BedPoint(0, 0, 10, 10),
        BedPoint(200, 0, 210, 10),
        BedPoint(200, 200, 210, 210),
        BedPoint(0, 200, 10, 210),
    ):
        mapper.add_point(point)
    mapper.solve(201, 201)

    mapper.apply_registration_translation(3.0, 0.75, analysis={"test": True})
    assert mapper.image_to_mm(100, 100) == pytest.approx((113.0, 110.75))
    assert mapper.calibration is not None
    assert mapper.calibration.registration_x_mm == pytest.approx(3.0)

    reloaded = BedMapper(tmp_path, BedCalibrationSettings(), area)
    assert reloaded.image_to_mm(100, 100) == pytest.approx((113.0, 110.75))
    reloaded.reset_registration_translation()
    assert reloaded.image_to_mm(100, 100) == pytest.approx((110.0, 110.0))


def test_bed_homography_refinement_persists_and_restores_previous_map(
    tmp_path: Path,
) -> None:
    area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), area)
    for point in (
        BedPoint(0, 0, 10, 10),
        BedPoint(200, 0, 210, 10),
        BedPoint(200, 200, 210, 210),
        BedPoint(0, 200, 10, 210),
    ):
        mapper.add_point(point)
    original = mapper.solve(201, 201)
    proposed = np.asarray(original.image_to_machine, dtype=np.float64).copy()
    proposed[0, 2] += 2.0
    analysis = {
        "can_apply_full_map": True,
        "rms_error_mm": 0.2,
        "max_error_mm": 0.4,
        "inlier_count": 7,
        "selected_count": 8,
        "base_image_to_machine": original.image_to_machine.tolist(),
    }

    mapper.apply_registration_homography(proposed, analysis=analysis)
    assert mapper.image_to_mm(100, 100) == pytest.approx((112.0, 110.0))
    assert mapper.calibration is not None
    assert mapper.calibration.refinement_base is not None

    reloaded = BedMapper(tmp_path, BedCalibrationSettings(), area)
    assert reloaded.image_to_mm(100, 100) == pytest.approx((112.0, 110.0))
    reloaded.reset_registration_homography()
    assert reloaded.image_to_mm(100, 100) == pytest.approx((110.0, 110.0))
    assert reloaded.calibration is not None
    assert reloaded.calibration.refinement_base is None


def test_bed_registration_rejects_large_cumulative_translation(tmp_path: Path) -> None:
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), WorkArea())
    for point in (
        BedPoint(0, 0, 0, 0),
        BedPoint(220, 0, 220, 0),
        BedPoint(220, 220, 220, 220),
        BedPoint(0, 220, 0, 220),
    ):
        mapper.add_point(point)
    mapper.solve(221, 221)

    with pytest.raises(CalibrationError, match="5 mm limit"):
        mapper.apply_registration_translation(5.1, 0.0)


def test_legacy_bed_calibration_without_fine_registration_loads_at_zero() -> None:
    calibration = BedCalibration.from_dict(
        {
            "image_to_machine": np.eye(3).tolist(),
            "machine_to_image": np.eye(3).tolist(),
            "image_width": 800,
            "image_height": 600,
            "rms_error_mm": 0.2,
            "max_error_mm": 0.4,
            "inlier_count": 8,
            "point_count": 8,
            "created_at": 1.0,
        }
    )

    assert calibration.registration_x_mm == 0.0
    assert calibration.registration_y_mm == 0.0
    assert calibration.registration_created_at is None


def test_dry_registration_session_cannot_be_analyzed_as_burned_marks(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _install_test_bed(context)
        context.prepare_fine_registration_job(
            powered=False,
            power_percent=0,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        with pytest.raises(CalibrationError, match="laser power 0%"):
            context.analyze_fine_registration_image(np.zeros((600, 800, 3), dtype=np.uint8))
    finally:
        context.stop()


def test_powered_registration_is_rechecked_against_detected_support_at_start(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "open_browser": False},
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.machine.hardware_enabled = True
    context.machine.settings.allow_motion = True
    area = context.settings.machine.work_area
    context.bed.replace_points_and_solve(
        [
            BedPoint(0, 599, area.x_min, area.y_min),
            BedPoint(799, 599, area.x_max, area.y_min),
            BedPoint(799, 0, area.x_max, area.y_max),
            BedPoint(0, 0, area.x_min, area.y_max),
        ],
        800,
        600,
    )
    calibration = context.bed.calibration
    assert calibration is not None
    _save_execution_support(context)
    job = context.prepare_fine_registration_job(
        powered=True,
        power_percent=10,
        mark_size_mm=5,
        speed_mm_min=1200,
    )

    context.validate_powered_calibration_support(job.program.text, job.filename)
    lines = job.program.text.splitlines()
    rapid_index = next(index for index, line in enumerate(lines) if line.startswith("G0 "))
    powered_index = next(index for index, line in enumerate(lines) if line.startswith("G1 "))
    escape_x = area.x_min + context.settings.laser.boundary_margin_mm
    escape_y = area.y_min + context.settings.laser.boundary_margin_mm
    lines[rapid_index] = f"G0 X{escape_x:g} Y{escape_y:g} F3000"
    lines[powered_index] = f"G1 X{escape_x + 1:g} Y{escape_y:g} F1200"
    escaped = "\n".join(lines) + "\n"
    assert escaped != job.program.text
    # Preserve the independent containment rejection after the newer exact-
    # program digest guard by binding this deliberately escaped fixture to its
    # altered program, as though it had been prepared that way.
    session = json.loads(context.fine_registration_path.read_text(encoding="utf-8"))
    session["program_digest"] = context.machine.preflight_program(escaped).digest
    context.fine_registration_path.write_text(json.dumps(session), encoding="utf-8")
    with pytest.raises(CalibrationError, match="leaves the detected honeycomb"):
        context.validate_powered_calibration_support(escaped, job.filename)


def test_fine_registration_session_is_bound_to_the_active_bed_map(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _save_execution_support(context)
        job = context.prepare_fine_registration_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        session = json.loads(
            context.fine_registration_path.read_text(encoding="utf-8")
        )
        assert session["image_to_machine"] == context.bed.calibration.image_to_machine.tolist()
        assert session["residual_mesh_created_at"] is None
        _run_prepared_job(context, job)

        prepared_map = context.bed.calibration.image_to_machine.copy()
        nodes = np.asarray(
            sorted(
                {
                    target.machine_x
                    for target in dense_mesh_targets(
                        context.settings.machine.work_area
                    )
                }
            )
        )
        context.bed.apply_residual_mesh(
            nodes,
            nodes,
            np.zeros((5, 5, 2), dtype=np.float64),
            fit_rms_mm=0.0,
            fit_max_mm=0.0,
        )
        assert np.array_equal(
            context.bed.calibration.image_to_machine,
            prepared_map,
        )
        with pytest.raises(CalibrationError, match="bed map changed"):
            context.analyze_fine_registration_image(
                    np.zeros((600, 800, 3), dtype=np.uint8)
            )
    finally:
        context.stop()


def test_stale_fine_registration_capture_fails_before_motor_hold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _save_execution_support(context)
        job = context.prepare_fine_registration_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        _run_prepared_job(context, job)
        context.bed.apply_registration_translation(0.2, 0.0)
        monkeypatch.setattr(context, "_require_camera_calibration_ready", lambda: None)

        def unexpected_hold():
            raise AssertionError("stale registration reached the motor-hold scope")

        monkeypatch.setattr(context.machine, "temporary_stepper_hold", unexpected_hold)
        with pytest.raises(CalibrationError, match="bed map changed"):
            context.capture_fine_registration()
    finally:
        context.stop()


def test_legacy_fine_registration_session_without_map_identity_is_rejected(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _save_execution_support(context)
        job = context.prepare_fine_registration_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        _run_prepared_job(context, job)
        session = json.loads(
            context.fine_registration_path.read_text(encoding="utf-8")
        )
        session.pop("image_to_machine")
        session.pop("residual_mesh_created_at")
        context.fine_registration_path.write_text(
            json.dumps(session),
            encoding="utf-8",
        )

        with pytest.raises(CalibrationError, match="bed map changed"):
            context.analyze_fine_registration_image(
                    np.zeros((600, 800, 3), dtype=np.uint8)
            )
    finally:
        context.stop()


def test_fine_registration_apply_rejects_modified_and_stale_analysis(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _install_test_bed(context)
        current = context._seal_analysis(
            {
                "can_apply_translation": True,
                "correction_x_mm": 0.1,
                "correction_y_mm": -0.1,
            }
        )
        context.fine_registration_path.write_text(
            json.dumps({"analysis": current}),
            encoding="utf-8",
        )
        applied = context.apply_fine_registration(current)
        assert applied["fine_registration"]["translation_x_mm"] == pytest.approx(0.1)

        modified = deepcopy(current)
        modified["correction_x_mm"] = 9.0
        with pytest.raises(CalibrationError, match="modified after review"):
            context.apply_fine_registration(modified)

        replacement = context._seal_analysis(
            {
                "can_apply_translation": True,
                "correction_x_mm": 0.2,
                "correction_y_mm": 0.0,
            }
        )
        context.fine_registration_path.write_text(
            json.dumps({"analysis": replacement}),
            encoding="utf-8",
        )
        with pytest.raises(CalibrationError, match="result is stale"):
            context.apply_fine_registration(current)
    finally:
        context.stop()


def test_bed_map_corrections_clear_honeycomb_pose(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _install_test_bed(context)
        calibration = context.bed.calibration
        assert calibration is not None
        context.honeycomb_support.save(
            HoneycombSupportReference.from_observations(
                ruler_origin_machine_mm=(10.0, 10.0),
                ruler_x_mark_machine_mm=(200.0, 10.0),
                ruler_xy_mark_machine_mm=(200.0, 200.0),
                ruler_mark_mm=190.0,
                support_width_mm=190.0,
                support_height_mm=190.0,
                bed_calibration_created_at=calibration.created_at,
            )
        )
        reviewed = context._seal_analysis(
            {
                "can_apply_translation": True,
                "correction_x_mm": 0.1,
                "correction_y_mm": -0.1,
            }
        )
        context.fine_registration_path.write_text(
            json.dumps({"analysis": reviewed}),
            encoding="utf-8",
        )

        context.apply_fine_registration(reviewed)

        assert context.honeycomb_support.reference is None
        assert context.honeycomb_execution_signature() is None
    finally:
        context.stop()


def test_dry_accuracy_validation_cannot_be_analyzed_as_burned_holdouts(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _install_test_bed(context)
        job = context.prepare_accuracy_validation_job(
            powered=False,
            power_percent=0,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        assert len(job.targets) == 5
        assert "M3 " not in job.program.text
        assert "M4 " not in job.program.text
        with pytest.raises(CalibrationError, match="laser power 0%"):
            context.analyze_accuracy_validation_image(
                np.zeros((600, 800, 3), dtype=np.uint8)
            )
    finally:
        context.stop()


def test_powered_accuracy_validation_scores_synthetic_holdouts_and_rejects_stale_map(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "serial"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        _save_execution_support(context)
        job = context.prepare_accuracy_validation_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        _run_prepared_job(context, job)
        image = np.full((600, 800, 3), 220, dtype=np.uint8)
        for target in job.targets:
            x, y = context.bed.mm_to_image(target.machine_x, target.machine_y)
            center = (int(round(x)), int(round(y)))
            cv2.line(
                image,
                (center[0] - 15, center[1]),
                (center[0] + 15, center[1]),
                (25, 25, 25),
                3,
            )
            cv2.line(
                image,
                (center[0], center[1] - 15),
                (center[0], center[1] + 15),
                (25, 25, 25),
                3,
            )

        result = context.analyze_accuracy_validation_image(image)
        assert result["analysis"]["passed"] is True
        assert result["analysis"]["point_count"] == 5
        assert Path(result["capture_path"]).exists()

        burst = FrameBurst(
            frames=tuple(image.copy() for _ in range(15)),
            sequence_numbers=tuple(range(101, 116)),
            discarded_frames=8,
            settle_seconds=1.5,
            elapsed_seconds=2.6,
            sharpness_scores=tuple(100.0 for _ in range(15)),
            controls=ControlResult({}, {}, {}),
        )
        precision = context.analyze_accuracy_validation_burst(burst)
        diagnostics = precision["analysis"]["precision_capture"]
        assert diagnostics["camera"]["sample_frames"] == 15
        assert diagnostics["aggregation"]["worst_jitter_rms_px"] == pytest.approx(
            0.0
        )
        assert all(
            item["inlier_count"] == 15
            for item in precision["analysis"]["measurements"]
        )

        context.bed.apply_registration_translation(0.2, 0.0)
        with pytest.raises(CalibrationError, match="map changed"):
            context.analyze_accuracy_validation_image(image)
    finally:
        context.stop()
