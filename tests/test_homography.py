import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.bed import BedMapper, BedPoint
from laser_aligner.config import BedCalibrationSettings, WorkArea


def test_bed_homography_round_trip(tmp_path: Path) -> None:
    mapper = BedMapper(tmp_path, BedCalibrationSettings(ransac_threshold_mm=0.2), WorkArea())
    image_points = np.array(
        [[100, 90], [900, 120], [950, 720], [70, 690], [500, 400], [300, 250], [730, 560]],
        dtype=np.float32,
    )
    known_h = np.array([[0.27, -0.01, -20.0], [0.005, -0.36, 250.0], [0.00002, -0.00003, 1.0]])
    machine_points = cv2.perspectiveTransform(image_points.reshape(-1, 1, 2), known_h).reshape(-1, 2)
    for index, (image, machine) in enumerate(zip(image_points, machine_points, strict=True)):
        mapper.add_point(BedPoint(float(image[0]), float(image[1]), float(machine[0]), float(machine[1]), str(index)))
    calibration = mapper.solve(1024, 768)
    assert calibration.rms_error_mm < 0.01
    x, y = mapper.image_to_mm(500, 400)
    expected = cv2.perspectiveTransform(np.array([[[500, 400]]], dtype=np.float32), known_h)[0, 0]
    assert np.allclose([x, y], expected, atol=0.01)
    u, v = mapper.mm_to_image(x, y)
    assert np.allclose([u, v], [500, 400], atol=0.01)

    test_image = np.zeros((768, 1024, 3), dtype=np.uint8)
    rectified = mapper.rectify(test_image, pixels_per_mm=2)
    assert rectified.shape[:2] == (440, 440)


def test_residual_mesh_round_trip_persists_and_resets(tmp_path: Path) -> None:
    area = WorkArea(x_min=0, x_max=200, y_min=0, y_max=200)
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), area)
    for point in (
        BedPoint(0, 0, 0, 0),
        BedPoint(200, 0, 200, 0),
        BedPoint(200, 200, 200, 200),
        BedPoint(0, 200, 0, 200),
    ):
        mapper.add_point(point)
    mapper.solve(201, 201)
    nodes = np.linspace(20, 180, 5)
    corrections = np.zeros((5, 5, 2))
    corrections[:, :, 0] = np.linspace(0.1, 0.5, 5)[None, :]
    corrections[:, :, 1] = np.linspace(-0.2, 0.2, 5)[:, None]
    mapper.apply_residual_mesh(nodes, nodes, corrections, fit_rms_mm=0.1, fit_max_mm=0.2)

    machine = mapper.image_to_mm(90, 120)
    assert machine != pytest.approx((90, 120))
    assert mapper.mm_to_image(*machine) == pytest.approx((90, 120), abs=1e-7)

    reloaded = BedMapper(tmp_path, BedCalibrationSettings(), area)
    assert reloaded.mm_to_image(*machine) == pytest.approx((90, 120), abs=1e-7)
    reloaded.reset_residual_mesh()
    assert reloaded.image_to_mm(90, 120) == pytest.approx((90, 120))


def test_residual_mesh_rejects_excessive_local_gradient(tmp_path: Path) -> None:
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), WorkArea(x_max=200, y_max=200))
    for point in (
        BedPoint(0, 0, 0, 0),
        BedPoint(200, 0, 200, 0),
        BedPoint(200, 200, 200, 200),
        BedPoint(0, 200, 0, 200),
    ):
        mapper.add_point(point)
    mapper.solve(201, 201)
    nodes = np.linspace(20, 180, 5)
    corrections = np.zeros((5, 5, 2))
    corrections[2, 2, 0] = 4.0
    with pytest.raises(Exception, match="3 mm safety bound"):
        mapper.apply_residual_mesh(nodes, nodes, corrections, fit_rms_mm=0.1, fit_max_mm=0.2)


def test_residual_mesh_allows_one_stale_guarded_refinement(tmp_path: Path) -> None:
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), WorkArea(x_max=200, y_max=200))
    for point in (
        BedPoint(0, 0, 0, 0),
        BedPoint(200, 0, 200, 0),
        BedPoint(200, 200, 200, 200),
        BedPoint(0, 200, 0, 200),
    ):
        mapper.add_point(point)
    mapper.solve(201, 201)
    nodes = np.linspace(20, 180, 5)
    mapper.apply_residual_mesh(nodes, nodes, np.zeros((5, 5, 2)), fit_rms_mm=0.1, fit_max_mm=0.2)
    assert mapper.calibration is not None
    created_at = mapper.calibration.residual_mesh.created_at
    delta = np.zeros((5, 5, 2))
    delta[:, :, 1] = -0.4
    mapper.refine_residual_mesh(
        delta,
        analyzed_mesh_created_at=created_at,
        predicted_rms_mm=0.1,
        predicted_max_mm=0.2,
    )
    assert mapper.calibration.residual_mesh.refinement_count == 1
    assert mapper.image_to_mm(100, 100)[1] == pytest.approx(99.6)
    with pytest.raises(Exception, match="already been refined"):
        mapper.refine_residual_mesh(
            delta,
            analyzed_mesh_created_at=created_at,
            predicted_rms_mm=0.1,
            predicted_max_mm=0.2,
        )


def test_bed_mapping_can_reverse_saved_x_axis_and_resolve(tmp_path: Path) -> None:
    area = WorkArea(x_min=10, x_max=210, y_min=20, y_max=180)
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), area)
    points = [
        BedPoint(10, 20, 10, 20, "a"),
        BedPoint(210, 20, 210, 20, "b"),
        BedPoint(210, 180, 210, 180, "c"),
        BedPoint(10, 180, 10, 180, "d"),
    ]
    for point in points:
        mapper.add_point(point)
    mapper.solve(220, 200)

    mapper.reverse_machine_axis("x")

    assert [(point.machine_x, point.machine_y) for point in mapper.points] == [
        (210, 20),
        (10, 20),
        (10, 180),
        (210, 180),
    ]
    assert mapper.image_to_mm(10, 20) == pytest.approx((210, 20))
    assert mapper.axis_mapping_state() == {
        "x": {"reversed": True, "recorded": True},
        "y": {"reversed": False, "recorded": True},
    }

    reloaded = BedMapper(tmp_path, BedCalibrationSettings(), area)
    assert reloaded.image_to_mm(210, 180) == pytest.approx((10, 180))
    assert reloaded.axis_mapping_state()["x"] == {
        "reversed": True,
        "recorded": True,
    }

    reloaded.set_machine_axis_reversed("x", False)
    reopened = BedMapper(tmp_path, BedCalibrationSettings(), area)
    assert reopened.axis_mapping_state()["x"] == {
        "reversed": False,
        "recorded": True,
    }
    assert reopened.image_to_mm(10, 20) == pytest.approx((10, 20))


def test_legacy_axis_orientation_can_be_confirmed_without_mirroring_points(
    tmp_path: Path,
) -> None:
    area = WorkArea(x_min=0, x_max=100, y_min=0, y_max=100)
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), area)
    for point in (
        BedPoint(0, 0, 100, 0),
        BedPoint(100, 0, 0, 0),
        BedPoint(100, 100, 0, 100),
        BedPoint(0, 100, 100, 100),
    ):
        mapper.add_point(point)
    mapper.solve(100, 100)
    payload = json.loads(mapper.model_path.read_text(encoding="utf-8"))
    payload.pop("axis_mapping")
    mapper.model_path.write_text(json.dumps(payload), encoding="utf-8")

    legacy = BedMapper(tmp_path, BedCalibrationSettings(), area)
    before = [(point.machine_x, point.machine_y) for point in legacy.points]
    assert legacy.axis_mapping_state()["x"] == {
        "reversed": True,
        "recorded": False,
    }

    legacy.set_machine_axis_reversed("x", True)
    confirmed = BedMapper(tmp_path, BedCalibrationSettings(), area)

    assert [(point.machine_x, point.machine_y) for point in confirmed.points] == before
    assert confirmed.axis_mapping_state()["x"] == {
        "reversed": True,
        "recorded": True,
    }
