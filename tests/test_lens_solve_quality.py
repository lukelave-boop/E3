from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from laser_aligner.calibration import lens as lens_module
from laser_aligner.calibration.lens import LensCalibrator, LensModel
from laser_aligner.config import LensCalibrationSettings
from laser_aligner.errors import CalibrationError

IMAGE_SIZE = (1920, 1080)
CAMERA_MATRIX = np.array(
    [[1380.0, 0.0, 960.0], [0.0, 1360.0, 540.0], [0.0, 0.0, 1.0]],
    dtype=np.float64,
)
DISTORTION = np.array([[-0.07, 0.018, 0.0008, -0.0012, 0.0]], dtype=np.float64)


def _axis_rotation(axis: str, degrees: float) -> np.ndarray:
    angle = np.radians(degrees)
    cosine, sine = float(np.cos(angle)), float(np.sin(angle))
    if axis == "x":
        return np.array([[1, 0, 0], [0, cosine, -sine], [0, sine, cosine]], dtype=np.float64)
    if axis == "y":
        return np.array([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]], dtype=np.float64)
    return np.array([[cosine, -sine, 0], [sine, cosine, 0], [0, 0, 1]], dtype=np.float64)


def _observation(
    calibrator: LensCalibrator,
    name: str,
    *,
    tilt_x: float,
    tilt_y: float,
    roll: float,
    center: tuple[float, float],
    distance: float,
    rng: np.random.Generator,
    noise_px: float = 0.08,
    fixed_plane_rotation: np.ndarray | None = None,
) -> lens_module._LensObservation:
    if fixed_plane_rotation is None:
        rotation = (
            _axis_rotation("z", roll)
            @ _axis_rotation("y", tilt_y)
            @ _axis_rotation("x", tilt_x)
        )
    else:
        rotation = fixed_plane_rotation @ _axis_rotation("z", roll)
    rotation_vector, _ = cv2.Rodrigues(rotation)
    translation = np.array([[0.0], [0.0], [distance]], dtype=np.float64)
    target = np.asarray([center[0] * IMAGE_SIZE[0], center[1] * IMAGE_SIZE[1]])
    object_points = calibrator._object_template().astype(np.float64)
    for _ in range(4):
        projected, _ = cv2.projectPoints(
            object_points,
            rotation_vector,
            translation,
            CAMERA_MATRIX,
            DISTORTION,
        )
        projected = projected.reshape(-1, 2)
        delta = target - np.mean(projected, axis=0)
        camera_points = object_points @ rotation.T + translation.reshape(1, 3)
        reciprocal_depth = float(np.mean(1.0 / camera_points[:, 2]))
        translation[0, 0] += delta[0] / (CAMERA_MATRIX[0, 0] * reciprocal_depth)
        translation[1, 0] += delta[1] / (CAMERA_MATRIX[1, 1] * reciprocal_depth)
    projected, _ = cv2.projectPoints(
        object_points,
        rotation_vector,
        translation,
        CAMERA_MATRIX,
        DISTORTION,
    )
    points = projected.reshape(-1, 2)
    if noise_px:
        points += rng.normal(0.0, noise_px, points.shape)
    assert np.min(points[:, 0]) >= 0 and np.max(points[:, 0]) < IMAGE_SIZE[0]
    assert np.min(points[:, 1]) >= 0 and np.max(points[:, 1]) < IMAGE_SIZE[1]
    return lens_module._LensObservation(
        name=name,
        image_size=IMAGE_SIZE,
        image_points=points.astype(np.float32),
    )


def _diverse_observations(
    calibrator: LensCalibrator,
    *,
    noise_px: float = 0.08,
) -> list[lens_module._LensObservation]:
    rng = np.random.default_rng(20260808)
    poses = (
        (-19, -13, -8, (0.25, 0.24), 410),
        (-17, 11, 9, (0.50, 0.23), 470),
        (-11, -19, 4, (0.75, 0.25), 530),
        (-8, 18, -12, (0.24, 0.50), 450),
        (2, -17, 15, (0.50, 0.50), 520),
        (1, 17, -4, (0.76, 0.49), 410),
        (11, -18, 7, (0.25, 0.75), 490),
        (10, 19, -10, (0.50, 0.76), 540),
        (17, -11, 12, (0.75, 0.75), 430),
        (20, 8, -6, (0.30, 0.35), 500),
        (-20, 2, 5, (0.69, 0.35), 440),
        (14, 15, 10, (0.50, 0.68), 510),
    )
    return [
        _observation(
            calibrator,
            f"view-{index:02d}.png",
            tilt_x=tilt_x,
            tilt_y=tilt_y,
            roll=roll,
            center=center,
            distance=distance,
            rng=rng,
            noise_px=noise_px,
        )
        for index, (tilt_x, tilt_y, roll, center, distance) in enumerate(poses)
    ]


def _model(width: int, height: int, *, created_at: float = 1.0) -> LensModel:
    return LensModel(
        camera_matrix=np.array(
            [[300.0, 0.0, width / 2], [0.0, 302.0, height / 2], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        ),
        distortion=np.zeros((1, 5), dtype=np.float64),
        image_width=width,
        image_height=height,
        rms_error=0.2,
        mean_reprojection_error=0.15,
        images_used=3,
        created_at=created_at,
    )


def test_diverse_synthetic_views_solve_with_per_view_diagnostics(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(minimum_images=10))

    model = calibrator._solve_observations(_diverse_observations(calibrator), IMAGE_SIZE)

    assert model.quality["gate"] in {"pass", "warning"}
    assert model.quality["metrics"]["pose_span_major_deg"] >= 15.0
    assert model.quality["metrics"]["pose_span_minor_deg"] >= 7.0
    assert model.quality["metrics"]["corner_hull_ratio"] >= 0.5
    assert model.images_used == 12
    assert len(model.model_id) == 64
    assert len(model.views) == 12
    assert all(view["accepted"] for view in model.views)
    assert all(view["reprojection_p95_px"] >= view["reprojection_mean_px"] for view in model.views)


def test_flat_repositioned_views_are_rejected_even_with_low_rms(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(minimum_images=10))
    rng = np.random.default_rng(42)
    plane = _axis_rotation("y", 10.0) @ _axis_rotation("x", 10.0)
    centers = (
        (0.25, 0.25),
        (0.50, 0.24),
        (0.75, 0.25),
        (0.24, 0.50),
        (0.50, 0.50),
        (0.76, 0.50),
        (0.25, 0.75),
        (0.50, 0.76),
        (0.75, 0.75),
        (0.33, 0.38),
        (0.67, 0.38),
        (0.50, 0.66),
    )
    observations = [
        _observation(
            calibrator,
            f"flat-{index:02d}.png",
            tilt_x=0,
            tilt_y=0,
            roll=index * 13.0,
            center=center,
            distance=440 + index % 3 * 45,
            rng=rng,
            noise_px=0.03,
            fixed_plane_rotation=plane,
        )
        for index, center in enumerate(centers)
    ]

    with pytest.raises(CalibrationError, match="tilt"):
        calibrator._solve_observations(observations, IMAGE_SIZE)

    assert calibrator._last_solve_quality is not None
    assert calibrator._last_solve_quality["gate"] == "reject"
    codes = {item["code"] for item in calibrator._last_solve_quality["reject_reasons"]}
    assert "insufficient_pose_span_minor" in codes


def test_center_only_views_are_rejected_even_with_diverse_tilts(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(minimum_images=10))
    observations = _diverse_observations(calibrator)
    rng = np.random.default_rng(99)
    centered = [
        _observation(
            calibrator,
            item.name,
            tilt_x=(-18 + index * 3),
            tilt_y=(-16 + (index * 7) % 32),
            roll=index * 4,
            center=(0.5, 0.5),
            distance=720,
            rng=rng,
            noise_px=0.04,
        )
        for index, item in enumerate(observations)
    ]

    with pytest.raises(CalibrationError, match="cover|edge"):
        calibrator._solve_observations(centered, IMAGE_SIZE)

    assert calibrator._last_solve_quality is not None
    codes = {item["code"] for item in calibrator._last_solve_quality["reject_reasons"]}
    assert "insufficient_corner_coverage" in codes
    assert "missing_image_edge_coverage" in codes


def test_one_high_error_view_is_excluded_and_recorded(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(minimum_images=10))
    observations = _diverse_observations(calibrator)
    corrupt = observations[-1]
    rng = np.random.default_rng(7)
    observations[-1] = lens_module._LensObservation(
        name=corrupt.name,
        image_size=corrupt.image_size,
        image_points=(
            corrupt.image_points + rng.normal(0.0, 4.0, corrupt.image_points.shape)
        ).astype(np.float32),
    )

    model = calibrator._solve_observations(observations, IMAGE_SIZE)

    assert model.images_used == 11
    assert model.quality["excluded_count"] == 1
    excluded = [item for item in model.views if not item["accepted"]]
    assert [item["name"] for item in excluded] == [corrupt.name]
    assert excluded[0]["exclusion_reason"] == "reprojection_outlier"


def test_solve_rejects_nonfinite_or_wrong_resolution_observations(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(minimum_images=10))
    observations = _diverse_observations(calibrator)
    bad_points = observations[0].image_points.copy()
    bad_points[0, 0] = np.nan
    observations[0] = lens_module._LensObservation(
        name=observations[0].name,
        image_size=(1280, 720),
        image_points=bad_points,
    )

    with pytest.raises(CalibrationError, match="finite corners"):
        calibrator._solve_observations(observations, IMAGE_SIZE)

    assert calibrator._last_solve_quality is not None
    assert calibrator._last_solve_quality["reject_reasons"][0]["code"] == "invalid_observations"


def test_resolution_groups_are_explicit_and_solve_never_chooses_first_file(
    tmp_path: Path,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=3, rows=3, minimum_images=3),
    )
    for prefix, shape in (("small", (120, 160)), ("large", (240, 320))):
        for index in range(3):
            assert cv2.imwrite(
                str(calibrator.image_dir / f"{prefix}-{index}.png"),
                np.zeros((*shape, 3), dtype=np.uint8),
            )
    corners = np.asarray(
        [
            [[20.0 + column * 25.0, 20.0 + row * 25.0]]
            for row in range(3)
            for column in range(3)
        ],
        dtype=np.float32,
    )
    selected_model = _model(160, 120)

    with (
        patch.object(calibrator, "detect_corners", return_value=(True, corners)),
        patch.object(
            calibrator,
            "_solve_observations",
            return_value=selected_model,
        ) as solve_observations,
    ):
        status = calibrator.status()
        with pytest.raises(CalibrationError, match="multiple resolutions"):
            calibrator.solve()
        calibrator.index_pending_captures()
        result = calibrator.solve((160, 120))

    assert status["resolution_selection"] == "ambiguous"
    assert status["active_resolution"] is None
    assert status["usable_image_count"] == 0
    assert status["total_usable_image_count"] == 0
    assert [item["pending_image_count"] for item in status["resolution_groups"]] == [3, 3]
    observations, image_size = solve_observations.call_args.args
    assert image_size == (160, 120)
    assert len(observations) == 3
    assert all(item.image_size == (160, 120) for item in observations)
    assert result.quality["ignored_resolution_image_count"] == 3


def test_legacy_model_loads_and_schema_two_round_trip_preserves_model_id() -> None:
    legacy = _model(320, 180).to_dict()
    legacy.pop("schema_version")
    legacy.pop("model_id")
    legacy.pop("quality")
    legacy.pop("views")
    legacy["image_files"] = ["one.jpg", "two.jpg", "three.jpg"]

    loaded = LensModel.from_dict(legacy)
    reloaded = LensModel.from_dict(loaded.to_dict())

    assert len(loaded.model_id) == 64
    assert loaded.model_id == reloaded.model_id
    assert [item["name"] for item in loaded.views] == legacy["image_files"]


@pytest.mark.parametrize("schema", [True, 1.0, 1.5, "1"])
def test_model_load_rejects_coerced_schema_versions(schema: object) -> None:
    payload = _model(320, 180).to_dict()
    payload["schema_version"] = schema

    with pytest.raises(ValueError, match="schema_version must be an integer"):
        LensModel.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("camera_matrix", [[1, 0], [0, 1]], "3x3"),
        ("distortion", [0, 0, float("nan"), 0, 0], "distortion"),
        ("image_width", 0, "dimensions"),
        ("rms_error", -0.1, "negative"),
        ("images_used", 0, "at least one"),
    ],
)
def test_model_load_rejects_malformed_or_nonfinite_values(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _model(320, 180).to_dict()
    payload[field] = value
    payload.pop("model_id")
    payload["schema_version"] = 1

    with pytest.raises(ValueError, match=message):
        LensModel.from_dict(payload)


def test_schema_two_rejects_a_model_id_that_does_not_match_parameters() -> None:
    payload = _model(320, 180).to_dict()
    payload["model_id"] = "0" * 64

    with pytest.raises(ValueError, match="model_id"):
        LensModel.from_dict(payload)


def test_model_id_is_independent_of_fit_metadata() -> None:
    first = _model(320, 180, created_at=1.0)
    second = _model(320, 180, created_at=99.0)
    second.rms_error = 0.8
    second.mean_reprojection_error = 0.7

    assert first.model_id == second.model_id
