import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.bed import BedCalibration, BedMapper, BedPoint
from laser_aligner.calibration.registration import (
    accuracy_validation_targets,
    analyze_accuracy_measurements,
    analyze_homography_refinement,
    analyze_registration_measurements,
    generate_registration_program,
    registration_targets,
    review_registration_measurements,
    suggested_registration_exclusions,
)
from laser_aligner.config import BedCalibrationSettings, LaserSettings, WorkArea, load_settings
from laser_aligner.errors import CalibrationError


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
        for index, ((x, y), (error_x, error_y)) in enumerate(
            zip(commanded, errors, strict=True), start=1
        )
    ]


def test_registration_program_has_sparse_safe_dry_and_powered_variants() -> None:
    work_area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)
    laser = LaserSettings(
        power_max=1000,
        boundary_margin_mm=5,
        travel_feed_mm_min=2000,
    )
    targets = registration_targets(
        work_area, mark_size_mm=5, boundary_margin_mm=laser.boundary_margin_mm
    )

    assert len(targets) == 8
    assert len({(target.machine_x, target.machine_y) for target in targets}) == 8
    assert (targets[6].machine_x, targets[6].machine_y) == pytest.approx((85, 185))
    assert all(
        work_area.contains(target.machine_x, target.machine_y, 7.5)
        for target in targets
    )

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
    assert powered.bounds_mm == pytest.approx((32.5, 32.5, 187.5, 187.5))


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
    assert not {
        (item.machine_x, item.machine_y) for item in holdouts
    }.intersection((item.machine_x, item.machine_y) for item in registration)


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


def test_registration_analysis_accepts_only_consistent_translation() -> None:
    analysis = analyze_registration_measurements(
        _measurements([(-3.0, -0.75)] * 8)
    )

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
    all_measurements = analyze_registration_measurements(_measurements(errors))[
        "measurements"
    ]
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
    measurements = analyze_registration_measurements(
        _measurements([(-3.0, -0.75)] * 8)
    )["measurements"]

    with pytest.raises(CalibrationError, match="At most two"):
        review_registration_measurements(measurements, [1, 2, 3])


def _homography_measurements(
    homography: np.ndarray,
) -> list[dict[str, float]]:
    measurements = _measurements([(0.0, 0.0)] * 8)
    targets = np.asarray(
        [[item["machine_x"], item["machine_y"]] for item in measurements],
        dtype=np.float64,
    )
    image_points = cv2.perspectiveTransform(
        targets.reshape(-1, 1, 2), np.linalg.inv(homography)
    ).reshape(-1, 2)
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
                    "simulation": True,
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "simulator"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        context.prepare_fine_registration_job(
            powered=False,
            power_percent=0,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        with pytest.raises(CalibrationError, match="dry motion only"):
            context.analyze_fine_registration_image(
                context.camera_frame(undistort=True)
            )
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
                    "simulation": True,
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "simulator"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        job = context.prepare_accuracy_validation_job(
            powered=False,
            power_percent=0,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
        assert len(job.targets) == 5
        assert "M3 " not in job.program.text
        assert "M4 " not in job.program.text
        with pytest.raises(CalibrationError, match="dry motion only"):
            context.analyze_accuracy_validation_image(
                context.camera_frame(undistort=True)
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
                    "simulation": True,
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600},
                "machine": {"backend": "simulator"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    try:
        job = context.prepare_accuracy_validation_job(
            powered=True,
            power_percent=10,
            mark_size_mm=5,
            speed_mm_min=1200,
        )
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

        context.bed.apply_registration_translation(0.2, 0.0)
        with pytest.raises(CalibrationError, match="map changed"):
            context.analyze_accuracy_validation_image(image)
    finally:
        context.stop()
