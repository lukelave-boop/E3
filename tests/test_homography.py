import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.bed import BedMapper, BedPoint
from laser_aligner.config import BedCalibrationSettings, WorkArea
from laser_aligner.errors import CalibrationError


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


def _solved_square_mapper(tmp_path: Path) -> BedMapper:
    mapper = BedMapper(
        tmp_path,
        BedCalibrationSettings(),
        WorkArea(x_min=0, x_max=100, y_min=0, y_max=100),
    )
    for point in (
        BedPoint(10, 20, 0, 0, "top left"),
        BedPoint(210, 20, 100, 0, "top right"),
        BedPoint(210, 220, 100, 100, "bottom right"),
        BedPoint(10, 220, 0, 100, "bottom left"),
    ):
        mapper.add_point(point)
    mapper.solve(240, 240, provenance={"generation": "original"})
    return mapper


@pytest.mark.parametrize("operation", ("add", "delete", "replace"))
def test_point_mutations_immediately_invalidate_active_calibration(
    tmp_path: Path,
    operation: str,
) -> None:
    mapper = _solved_square_mapper(tmp_path)
    persisted_model = mapper.model_path.read_bytes()

    if operation == "add":
        mapper.add_point(BedPoint(110, 120, 50, 50, "center"))
    elif operation == "delete":
        mapper.delete_point(0)
    else:
        replacement = mapper.points
        replacement[0] = BedPoint(12, 22, 0, 0, "replacement")
        mapper.replace_points(replacement)

    assert mapper.calibration is None
    assert mapper.model_path.read_bytes() == persisted_model
    status = mapper.status()
    assert status["calibrated"] is False
    assert status["model_present"] is True
    assert "solve the bed mapping again" in status["calibration_unavailable_reason"]
    with pytest.raises(CalibrationError, match="has not been solved"):
        mapper.image_to_mm(110, 120)

    restarted = BedMapper(tmp_path, BedCalibrationSettings(), mapper.work_area)
    assert restarted.calibration is None
    assert "point generation" in restarted.calibration_unavailable_reason


@pytest.mark.parametrize("mismatch", ("separate_points", "embedded_points"))
def test_restart_rejects_mixed_persisted_point_generations(
    tmp_path: Path,
    mismatch: str,
) -> None:
    mapper = _solved_square_mapper(tmp_path)
    if mismatch == "separate_points":
        points_payload = json.loads(mapper.points_path.read_text(encoding="utf-8"))
        points_payload[0]["image_x"] += 0.25
        mapper.points_path.write_text(json.dumps(points_payload), encoding="utf-8")
    else:
        model_payload = json.loads(mapper.model_path.read_text(encoding="utf-8"))
        model_payload["points"][0]["label"] = "other generation"
        mapper.model_path.write_text(json.dumps(model_payload), encoding="utf-8")

    restarted = BedMapper(tmp_path, BedCalibrationSettings(), mapper.work_area)

    assert restarted.calibration is None
    assert restarted.status()["model_present"] is True
    assert "point generation" in restarted.calibration_unavailable_reason


def test_point_write_failure_restores_active_calibration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapper = _solved_square_mapper(tmp_path)
    previous_calibration = mapper.calibration
    previous_points = mapper.points
    from laser_aligner.calibration import bed as bed_module

    real_atomic_write = bed_module.atomic_write_json

    def fail_points(path, payload):
        if path == mapper.points_path:
            raise OSError("simulated point write failure")
        return real_atomic_write(path, payload)

    monkeypatch.setattr(bed_module, "atomic_write_json", fail_points)

    with pytest.raises(OSError, match="simulated point write failure"):
        mapper.add_point(BedPoint(110, 120, 50, 50, "center"))

    assert mapper.points == previous_points
    assert mapper.calibration is previous_calibration
    assert mapper.calibration_unavailable_reason is None


@pytest.mark.parametrize("operation", ("add", "replace", "replace_and_solve"))
@pytest.mark.parametrize("invalid_coordinate", (float("nan"), True, "12.5"))
def test_point_replacement_rejects_invalid_coordinates_before_persistence(
    tmp_path: Path,
    operation: str,
    invalid_coordinate: object,
) -> None:
    mapper = _solved_square_mapper(tmp_path)
    previous_calibration = mapper.calibration
    previous_points_bytes = mapper.points_path.read_bytes()
    previous_model_bytes = mapper.model_path.read_bytes()
    invalid = BedPoint(invalid_coordinate, 20, 0, 0, "invalid")  # type: ignore[arg-type]

    with pytest.raises(CalibrationError, match="Invalid bed calibration point"):
        if operation == "add":
            mapper.add_point(invalid)
        elif operation == "replace":
            mapper.replace_points([invalid, *mapper.points[1:]])
        else:
            mapper.replace_points_and_solve(
                [invalid, *mapper.points[1:]],
                240,
                240,
            )

    assert mapper.calibration is previous_calibration
    assert mapper.points_path.read_bytes() == previous_points_bytes
    assert mapper.model_path.read_bytes() == previous_model_bytes


def test_clear_removes_stale_model_and_unavailable_reason(tmp_path: Path) -> None:
    mapper = _solved_square_mapper(tmp_path)
    mapper.add_point(BedPoint(110, 120, 50, 50, "center"))
    assert mapper.calibration_unavailable_reason is not None

    mapper.clear()

    assert mapper.points == []
    assert mapper.calibration is None
    assert mapper.calibration_unavailable_reason is None
    assert mapper.status()["model_present"] is False


def test_solve_reactivates_matching_point_generation_after_edit(tmp_path: Path) -> None:
    mapper = _solved_square_mapper(tmp_path)
    mapper.add_point(BedPoint(110, 120, 50, 50, "center"))

    solved = mapper.solve(240, 240, provenance={"generation": "replacement"})
    restarted = BedMapper(tmp_path, BedCalibrationSettings(), mapper.work_area)

    assert mapper.calibration is solved
    assert mapper.calibration_unavailable_reason is None
    assert restarted.calibration is not None
    assert restarted.calibration.provenance == {"generation": "replacement"}


def test_rectification_map_matches_inverse_mapping_with_residual_mesh(
    tmp_path: Path,
) -> None:
    area = WorkArea(x_min=10, x_max=30, y_min=20, y_max=40)
    mapper = BedMapper(
        tmp_path,
        BedCalibrationSettings(pixels_per_mm=2),
        area,
    )
    for point in (
        BedPoint(10, 20, 10, 20),
        BedPoint(30, 20, 30, 20),
        BedPoint(30, 40, 30, 40),
        BedPoint(10, 40, 10, 40),
    ):
        mapper.add_point(point)
    mapper.solve(64, 64)
    nodes = np.asarray([10.0, 20.0, 30.0])
    y_nodes = np.asarray([20.0, 30.0, 40.0])
    corrections = np.zeros((3, 3, 2), dtype=np.float64)
    corrections[:, :, 0] = np.asarray([0.1, 0.2, 0.3])[None, :]
    corrections[:, :, 1] = np.asarray([-0.2, 0.0, 0.2])[:, None]
    mapper.apply_residual_mesh(
        nodes,
        y_nodes,
        corrections,
        fit_rms_mm=0.1,
        fit_max_mm=0.2,
    )

    map_x, map_y = mapper.rectification_map()
    cached_x, cached_y = mapper.rectification_map()

    assert map_x.shape == (40, 40)
    assert map_y.shape == (40, 40)
    assert map_x is cached_x
    assert map_y is cached_y
    assert not map_x.flags.writeable
    assert not map_y.flags.writeable
    for row, column in ((0, 0), (7, 11), (20, 20), (39, 39)):
        machine_x = area.x_min + column / 2.0
        machine_y = area.y_max - row / 2.0
        expected_x, expected_y = mapper.mm_to_image(machine_x, machine_y)
        assert float(map_x[row, column]) == pytest.approx(expected_x, abs=2e-6)
        assert float(map_y[row, column]) == pytest.approx(expected_y, abs=2e-6)


def test_rectification_cache_survives_object_id_reuse_after_two_updates(
    tmp_path: Path,
) -> None:
    area = WorkArea(x_min=0, x_max=20, y_min=0, y_max=20)
    mapper = BedMapper(
        tmp_path,
        BedCalibrationSettings(pixels_per_mm=1),
        area,
    )
    for point in (
        BedPoint(0, 0, 0, 0),
        BedPoint(20, 0, 20, 0),
        BedPoint(20, 20, 20, 20),
        BedPoint(0, 20, 0, 20),
    ):
        mapper.add_point(point)
    mapper.solve(21, 21)
    original_x, _ = mapper.rectification_map()

    mapper.apply_registration_translation(0.25, 0.0)
    mapper.apply_registration_translation(0.5, 0.0)
    updated_x, updated_y = mapper.rectification_map()
    expected_x, expected_y = mapper.mm_to_image(10.0, 10.0)

    assert updated_x is not original_x
    assert float(updated_x[10, 10]) == pytest.approx(expected_x, abs=2e-6)
    assert float(updated_y[10, 10]) == pytest.approx(expected_y, abs=2e-6)
    assert expected_x == pytest.approx(9.25)


def test_rectification_preserves_output_dimensions_and_uses_neutral_border(
    tmp_path: Path,
) -> None:
    area = WorkArea(x_min=0, x_max=4, y_min=0, y_max=4)
    mapper = BedMapper(
        tmp_path,
        BedCalibrationSettings(pixels_per_mm=1),
        area,
    )
    for point in (
        BedPoint(-2, -2, 0, 4),
        BedPoint(2, -2, 4, 4),
        BedPoint(2, 2, 4, 0),
        BedPoint(-2, 2, 0, 0),
    ):
        mapper.add_point(point)
    mapper.solve(4, 4)
    image = np.full((4, 4, 3), 200, dtype=np.uint8)

    rectified = mapper.rectify(image)

    assert rectified.shape == (4, 4, 3)
    assert tuple(rectified[0, 0]) == (35, 35, 35)
    assert tuple(rectified[3, 3]) == (200, 200, 200)


@pytest.mark.parametrize("operation", ("map", "matrix", "rectify"))
def test_rectification_rejects_explicit_zero_pixels_per_mm(
    tmp_path: Path,
    operation: str,
) -> None:
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), WorkArea(x_max=20, y_max=20))
    for point in (
        BedPoint(0, 0, 0, 0),
        BedPoint(20, 0, 20, 0),
        BedPoint(20, 20, 20, 20),
        BedPoint(0, 20, 0, 20),
    ):
        mapper.add_point(point)
    mapper.solve(21, 21)

    with pytest.raises(CalibrationError, match="finite and positive"):
        if operation == "map":
            mapper.rectification_map(0)
        elif operation == "matrix":
            mapper.image_to_canvas_matrix(0)
        else:
            mapper.rectify(np.zeros((21, 21, 3), dtype=np.uint8), 0)


@pytest.mark.parametrize("pixels_per_mm", (float("nan"), True, "invalid", 10_000.0))
def test_rectification_rejects_invalid_or_excessive_output_density(
    tmp_path: Path,
    pixels_per_mm: object,
) -> None:
    mapper = _solved_square_mapper(tmp_path)

    with pytest.raises(CalibrationError, match="pixels_per_mm|pixel limit"):
        mapper.rectification_map(pixels_per_mm)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "image",
    (
        np.empty((0, 0, 3), dtype=np.uint8),
        np.zeros((240, 240, 3), dtype=np.float32),
        np.zeros((240, 240, 2), dtype=np.uint8),
    ),
)
def test_rectification_rejects_malformed_images(
    tmp_path: Path,
    image: np.ndarray,
) -> None:
    mapper = _solved_square_mapper(tmp_path)

    with pytest.raises(CalibrationError, match="uint8 grayscale or BGR"):
        mapper.rectify(image)


@pytest.mark.parametrize(
    ("operation", "first", "second"),
    (
        ("image", float("nan"), 1.0),
        ("image", True, 1.0),
        ("machine", 1.0, float("inf")),
    ),
)
def test_bed_mapping_rejects_nonfinite_coordinates(
    tmp_path: Path,
    operation: str,
    first: object,
    second: object,
) -> None:
    mapper = _solved_square_mapper(tmp_path)

    with pytest.raises(CalibrationError, match="must be finite"):
        if operation == "image":
            mapper.image_to_mm(first, second)  # type: ignore[arg-type]
        else:
            mapper.mm_to_image(first, second)  # type: ignore[arg-type]


@pytest.mark.parametrize("dimension", (0, True, 240.5))
def test_bed_solve_rejects_invalid_image_dimensions(
    tmp_path: Path,
    dimension: object,
) -> None:
    mapper = BedMapper(tmp_path, BedCalibrationSettings(), WorkArea())
    for point in (
        BedPoint(0, 0, 0, 0),
        BedPoint(220, 0, 220, 0),
        BedPoint(220, 220, 220, 220),
        BedPoint(0, 220, 0, 220),
    ):
        mapper.add_point(point)

    with pytest.raises(CalibrationError, match="positive integers"):
        mapper.solve(dimension, 240)  # type: ignore[arg-type]


def test_registration_write_failure_does_not_publish_unpersisted_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapper = _solved_square_mapper(tmp_path)
    original = mapper.calibration
    original_bytes = mapper.model_path.read_bytes()
    from laser_aligner.calibration import bed as bed_module

    def fail_write(path, payload):
        del path, payload
        raise OSError("simulated model write failure")

    monkeypatch.setattr(bed_module, "atomic_write_json", fail_write)

    with pytest.raises(OSError, match="simulated model write failure"):
        mapper.apply_registration_translation(0.5, 0.25)

    assert mapper.calibration is original
    assert mapper.model_path.read_bytes() == original_bytes


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
