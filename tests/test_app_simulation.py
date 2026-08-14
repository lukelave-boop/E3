import json
import math
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.bed import BedPoint
from laser_aligner.calibration.lens import LensModel
from laser_aligner.calibration.support import (
    HoneycombCoordinateFrame,
    HoneycombSupportReference,
)
from laser_aligner.camera.service import CameraStatus
from laser_aligner.config import WorkArea, load_settings
from laser_aligner.core.runtime import CoreRuntime
from laser_aligner.errors import CalibrationError
from laser_aligner.gcode.preview import parse_gcode_segments
from laser_aligner.imaging import write_image_atomic
from laser_aligner.vision.ruler import HoneycombRulerDetection, RulerAxisDetection


def test_simulation_auto_maps_bed_and_generates_workspace(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "simulation": True, "open_browser": False},
                "camera": {"width": 800, "height": 600, "fps": 2},
                "calibration": {"bed": {"pixels_per_mm": 2}},
                "machine": {"backend": "simulator"},
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    context = AppContext(settings)
    context.start()
    try:
        assert context.bed.calibration is not None
        assert context.machine.connected
        image = context.rectified_frame(refresh=True)
        assert image.shape[:2] == (440, 440)
        assert not context.workspace_path.exists()
        persisted = context.rectified_frame(refresh=True, precision=True, persist=True)
        assert persisted.shape == image.shape
        assert context.workspace_path.exists()
        detection = context.detect_workpiece()
        assert detection["detected"]
    finally:
        context.stop()


def test_expanded_rectified_frame_does_not_replace_configured_workspace_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    configured = np.full((440, 440, 3), 17, dtype=np.uint8)
    expanded = WorkArea(-5.0, 230.0, -10.0, 240.0)
    context._cache_workspace(configured)
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    monkeypatch.setattr(
        context,
        "camera_frame",
        lambda **_kwargs: np.zeros((24, 32, 3), dtype=np.uint8),
    )

    def fake_rectify(
        _image: np.ndarray,
        *,
        work_area: WorkArea | None = None,
    ) -> np.ndarray:
        area = context.settings.machine.work_area if work_area is None else work_area
        ppm = context.settings.calibration.bed.pixels_per_mm
        return np.zeros(
            (
                int(round(area.height * ppm)),
                int(round(area.width * ppm)),
                3,
            ),
            dtype=np.uint8,
        )

    monkeypatch.setattr(context, "_rectify_camera_image", fake_rectify)

    display = context.rectified_frame(
        refresh=True,
        persist=True,
        work_area=expanded,
    )
    cached = context.rectified_frame(refresh=False)

    assert display.shape[:2] == (500, 470)
    assert np.array_equal(cached, configured)
    assert not context.workspace_path.exists()


def test_local_rectified_frame_does_not_replace_configured_workspace_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    configured = np.full((440, 440, 3), 17, dtype=np.uint8)
    context._cache_workspace(configured)
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    monkeypatch.setattr(
        context,
        "camera_frame",
        lambda **_kwargs: np.zeros((24, 32, 3), dtype=np.uint8),
    )
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(29.0, 37.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=220.0,
        height_mm=220.0,
        provenance_digest="3" * 64,
    )
    calls: list[tuple[WorkArea | None, HoneycombCoordinateFrame | None]] = []

    def fake_rectify(
        _image: np.ndarray,
        *,
        work_area: WorkArea | None = None,
        coordinate_frame: HoneycombCoordinateFrame | None = None,
    ) -> np.ndarray:
        calls.append((work_area, coordinate_frame))
        return np.full((440, 440, 3), 99, dtype=np.uint8)

    monkeypatch.setattr(context, "_rectify_camera_image", fake_rectify)
    local_area = WorkArea(0.0, 220.0, 0.0, 220.0)

    display = context.rectified_frame(
        refresh=False,
        persist=True,
        work_area=local_area,
        coordinate_frame=frame,
    )
    cached = context.rectified_frame(refresh=False)

    assert np.all(display == 99)
    assert calls == [(local_area, frame)]
    assert np.array_equal(cached, configured)
    assert not context.workspace_path.exists()


def test_browser_generation_uses_configured_photo_pose_for_exact_preview(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "browser-pose.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "simulation": True},
                "camera": {"autostart": False},
                "machine": {
                    "backend": "simulator",
                    "photo_position": {"x": 73, "y": 91, "z": None},
                },
                "laser": {"spot_offset_x_mm": -2, "spot_offset_y_mm": 3},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))

    result = context.generate_gcode(
        {
            "name": "pose.svg",
            "svg": '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>',
            "placement": {
                "center_x_mm": 110,
                "center_y_mm": 110,
                "width_mm": 10,
                "height_mm": 10,
            },
            "toolpath": {"power": 0, "optimize_order": False},
        }
    )

    segments = parse_gcode_segments(result["gcode"])
    assert segments[0].start_x == pytest.approx(71.0)
    assert segments[0].start_y == pytest.approx(94.0)


def _browser_generation_payload(**toolpath: object) -> dict[str, object]:
    return {
        "name": "labels.svg",
        "svg": (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<rect width="10" height="10"/></svg>'
        ),
        "placement": {
            "center_x_mm": 110,
            "center_y_mm": 110,
            "width_mm": 10,
            "height_mm": 10,
        },
        "toolpath": {"power": 0, "optimize_order": False, **toolpath},
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("power", True, "power must be a JSON integer"),
        ("power", 1.5, "power must be a JSON integer"),
        ("power", "10", "power must be a JSON integer"),
        ("travel_feed_mm_min", float("nan"), "finite JSON number"),
        ("travel_feed_mm_min", float("inf"), "finite JSON number"),
        ("travel_feed_mm_min", "1000", "finite JSON number"),
        ("travel_feed_mm_min", 0, "finite positive number"),
        ("travel_feed_mm_min", -1, "finite positive number"),
        ("engrave_feed_mm_min", float("nan"), "finite JSON number"),
        ("engrave_feed_mm_min", float("inf"), "finite JSON number"),
        ("engrave_feed_mm_min", 0, "finite positive number"),
        ("engrave_feed_mm_min", -1, "finite positive number"),
    ],
)
def test_browser_generation_rejects_coerced_or_invalid_toolpath_numbers(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    context = AppContext(_settings(tmp_path))

    with pytest.raises(ValueError, match=message):
        context.generate_gcode(_browser_generation_payload(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("center_x_mm", True),
        ("center_y_mm", "110"),
        ("width_mm", float("nan")),
        ("height_mm", float("inf")),
        ("rotation_deg", "0"),
    ],
)
def test_browser_generation_rejects_coerced_or_nonfinite_placement_numbers(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    context = AppContext(_settings(tmp_path))
    payload = _browser_generation_payload()
    payload["placement"][field] = value

    with pytest.raises(ValueError, match=f"{field} must be a finite JSON number"):
        context.generate_gcode(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("svg", 1, "svg must be a JSON string"),
        ("placement", [], "placement must be a JSON object"),
        ("toolpath", [], "toolpath must be a JSON object"),
        ("name", 1, "name must be a JSON string"),
    ],
)
def test_browser_generation_rejects_malformed_payload_sections(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    context = AppContext(_settings(tmp_path))
    payload = _browser_generation_payload()
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        context.generate_gcode(payload)


def test_browser_generation_rejects_nonstring_power_mode(tmp_path: Path) -> None:
    context = AppContext(_settings(tmp_path))

    with pytest.raises(ValueError, match="power_mode must be a JSON string"):
        context.generate_gcode(_browser_generation_payload(power_mode=4))


def test_rapid_browser_generations_publish_distinct_bounded_safe_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    payload = _browser_generation_payload()
    payload["name"] = "../" + ("very-long-name_" * 30) + " café!?!!.svg"
    tokens = iter(("repeat", "repeat", "different"))
    monkeypatch.setattr(
        "laser_aligner.app.secrets.token_hex",
        lambda _size: next(tokens),
    )

    first = context.generate_gcode(payload)
    second = context.generate_gcode(payload)

    assert first["filename"] != second["filename"]
    assert len(first["filename"]) < 140
    assert len(second["filename"]) < 140
    assert "/" not in first["filename"] and "\\" not in first["filename"]
    generated = context.settings.app.data_dir / "generated"
    assert (generated / first["filename"]).read_text(encoding="utf-8") == first["gcode"]
    assert (generated / second["filename"]).read_text(encoding="utf-8") == second["gcode"]


def test_rapid_captures_publish_distinct_bounded_safe_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    frames = iter(
        (
            np.zeros((16, 16, 3), dtype=np.uint8),
            np.full((16, 16, 3), 255, dtype=np.uint8),
        )
    )
    monkeypatch.setattr(
        context,
        "camera_frame",
        lambda undistort=True: next(frames),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "laser_aligner.app.time.strftime",
        lambda _format: "20260809-010203",
    )
    monkeypatch.setattr(
        "laser_aligner.app.secrets.token_hex",
        lambda _size: "repeat",
    )
    prefix = "../" + ("very-long-capture_" * 30) + " café!?!!"

    first = context.save_capture(prefix, precision=False)
    second = context.save_capture(prefix, precision=False)

    assert first != second
    assert first.parent == second.parent == context.settings.app.data_dir / "captures"
    assert len(first.name) < 120 and len(second.name) < 120
    assert "/" not in first.name and "\\" not in first.name
    first_image = cv2.imread(str(first), cv2.IMREAD_COLOR)
    second_image = cv2.imread(str(second), cv2.IMREAD_COLOR)
    assert first_image is not None and float(first_image.mean()) == pytest.approx(0.0)
    assert second_image is not None and float(second_image.mean()) == pytest.approx(255.0)


def test_raw_to_bed_composition_uses_one_remap_and_reduces_resampling_error(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "composed-map.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "composed-data",
                    "simulation": True,
                    "open_browser": False,
                },
                "camera": {"width": 128, "height": 128, "autostart": False},
                "calibration": {"bed": {"pixels_per_mm": 1}},
                "machine": {
                    "backend": "simulator",
                    "work_area": {
                        "x_min": 0,
                        "x_max": 64,
                        "y_min": 0,
                        "y_max": 64,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    lens = LensModel(
        camera_matrix=np.asarray(
            [[95.0, 0.0, 64.0], [0.0, 93.0, 64.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        ),
        distortion=np.asarray([[0.12, -0.04, 0.001, -0.002, 0.0]]),
        image_width=128,
        image_height=128,
        rms_error=0.2,
        mean_reprojection_error=0.15,
        images_used=12,
        created_at=1.0,
    )
    context.lens._model = lens
    image_points = np.asarray(
        [[10.25, 9.75], [110.5, 14.2], [104.1, 112.3], [6.8, 108.7]],
        dtype=np.float64,
    )
    machine_points = np.asarray(
        [[0.0, 64.0], [64.0, 64.0], [64.0, 0.0], [0.0, 0.0]],
        dtype=np.float64,
    )
    for image_point, machine_point in zip(
        image_points,
        machine_points,
        strict=True,
    ):
        context.bed.add_point(
            BedPoint(
                float(image_point[0]),
                float(image_point[1]),
                float(machine_point[0]),
                float(machine_point[1]),
            )
        )
    context.bed.solve(128, 128)

    grid_x, grid_y = np.meshgrid(
        np.arange(128, dtype=np.float64),
        np.arange(128, dtype=np.float64),
    )
    raw_grid = np.stack((grid_x, grid_y), axis=-1)
    corrected_grid = cv2.undistortPoints(
        raw_grid.reshape(-1, 1, 2),
        lens.camera_matrix,
        lens.distortion,
        P=lens.camera_matrix,
    ).reshape(128, 128, 2)

    def continuous_pattern(points: np.ndarray) -> np.ndarray:
        x = points[..., 0]
        y = points[..., 1]
        return (
            127.0
            + 35.0 * np.sin(0.31 * x)
            + 34.0 * np.cos(0.27 * y)
            + 25.0 * np.sin(0.21 * x + 0.19 * y)
        )

    raw = np.clip(continuous_pattern(corrected_grid), 0, 255).astype(np.uint8)
    corrected_x, corrected_y = context.bed.rectification_map()
    ideal = np.clip(
        continuous_pattern(np.stack((corrected_x, corrected_y), axis=-1)),
        0,
        255,
    )
    sequential = context.bed.rectify(lens.undistort(raw))

    with patch("laser_aligner.app.cv2.remap", wraps=cv2.remap) as remap:
        composed = context._rectify_camera_image(raw)
        cached_entry = next(iter(context._composed_map_cache.values()))
        repeated = context._rectify_camera_image(raw)

    assert remap.call_count == 2
    assert cached_entry[2] is next(iter(context._composed_map_cache.values()))[2]
    assert np.array_equal(composed, repeated)
    composed_error = float(np.mean(np.abs(composed.astype(np.float64) - ideal)))
    sequential_error = float(np.mean(np.abs(sequential.astype(np.float64) - ideal)))
    assert composed_error < sequential_error * 0.92

    local_frame = HoneycombCoordinateFrame(
        origin_machine_mm=(1.0, 2.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=64.0,
        height_mm=64.0,
        provenance_digest="4" * 64,
    )
    local = context._rectify_camera_image(raw, coordinate_frame=local_frame)
    local_maps = next(iter(context._composed_map_cache.values()))[2]
    local_repeated = context._rectify_camera_image(raw, coordinate_frame=local_frame)
    assert local_maps is next(iter(context._composed_map_cache.values()))[2]
    assert np.array_equal(local, local_repeated)
    assert not np.array_equal(local, composed)

    restored = context._rectify_camera_image(raw)
    restored_maps = next(iter(context._composed_map_cache.values()))[2]
    assert restored_maps is not local_maps
    assert np.array_equal(restored, composed)

    context.bed.apply_registration_translation(0.25, 0.0)
    context.bed.apply_registration_translation(0.5, 0.0)
    updated = context._rectify_camera_image(raw)
    updated_maps = next(iter(context._composed_map_cache.values()))[2]
    assert updated_maps is not cached_entry[2]
    assert not np.array_equal(updated, composed)

    context.bed.solve(127, 128)
    with pytest.raises(CalibrationError, match="bed calibration"):
        context._rectify_camera_image(raw)


def test_camera_calibration_readiness_accepts_connected_simulator(tmp_path: Path) -> None:
    context = AppContext(_settings(tmp_path))
    context.camera.start()
    try:
        assert context.camera_calibration_readiness() == {
            "state": "READY",
            "reasons": [],
            "synthetic": True,
        }
    finally:
        context.camera.stop()


def test_camera_calibration_readiness_rejects_geometry_and_control_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path, simulation=False))
    monkeypatch.setattr(
        context.camera,
        "status",
        lambda: CameraStatus(
            connected=True,
            device="/dev/video0",
            width=1280,
            height=720,
            fps=15.0,
            frames_read=10,
            last_error=None,
            frame_age_seconds=0.1,
            controls_critical_unverified={
                "exposure_mode": "readback was unavailable"
            },
        ),
    )

    readiness = context.camera_calibration_readiness()

    assert readiness["state"] == "BLOCKED"
    assert any(
        "expected 1920x1080, got 1280x720" in item
        for item in readiness["reasons"]
    )
    assert any(
        "exposure mode is unverified" in item for item in readiness["reasons"]
    )


def test_simulation_and_legacy_grid_follow_nonzero_configured_area(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "centered-area.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "centered-data",
                    "simulation": True,
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600, "fps": 2},
                "calibration": {"bed": {"pixels_per_mm": 2}},
                "machine": {
                    "backend": "simulator",
                    "work_area": {
                        "x_min": 10,
                        "x_max": 210,
                        "y_min": 20,
                        "y_max": 220,
                    },
                },
                "laser": {"boundary_margin_mm": 5},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    context.start()
    captured: list[dict[str, float]] = []

    def record_expected(
        _image: np.ndarray,
        expected: list[dict[str, float]],
        *,
        search_radius_px: int,
    ) -> dict[str, object]:
        assert search_radius_px == 65
        captured.extend(expected)
        return {"detected": True, "points": expected}

    monkeypatch.setattr("laser_aligner.app.detect_crosshairs_near", record_expected)
    try:
        correspondences = context.camera.calibration_correspondences()  # type: ignore[attr-defined]
        assert all(10.0 <= point[2] <= 210.0 for point in correspondences)
        assert all(20.0 <= point[3] <= 220.0 for point in correspondences)
        assert context.rectified_frame(refresh=True).shape[:2] == (400, 400)

        context.detect_bed_cross_grid()
        assert len(captured) == 25
        assert min(point["machine_x"] for point in captured) == pytest.approx(19.75)
        assert max(point["machine_x"] for point in captured) == pytest.approx(200.25)
        assert min(point["machine_y"] for point in captured) == pytest.approx(29.75)
        assert max(point["machine_y"] for point in captured) == pytest.approx(210.25)
    finally:
        context.stop()


def _settings(
    tmp_path: Path,
    *,
    simulation: bool = True,
    machine_backend: str = "simulator",
):
    config_path = tmp_path / f"config-{simulation}-{machine_backend}.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": f"data-{simulation}-{machine_backend}",
                    "simulation": simulation,
                    "open_browser": False,
                },
                "camera": {"autostart": False},
                "calibration": {"bed": {"pixels_per_mm": 2}},
                "machine": {"backend": machine_backend},
            }
        ),
        encoding="utf-8",
    )
    return load_settings(config_path)


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_app_context_rejects_non_boolean_hardware_gate(
    tmp_path: Path,
    value: object,
) -> None:
    with pytest.raises(TypeError, match="hardware_enabled must be an exact boolean"):
        AppContext(_settings(tmp_path), hardware_enabled=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_core_runtime_rejects_non_boolean_hardware_gate(
    tmp_path: Path,
    value: object,
) -> None:
    with pytest.raises(TypeError, match="hardware_enabled must be an exact boolean"):
        CoreRuntime(_settings(tmp_path), hardware_enabled=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_app_and_runtime_reject_non_boolean_laser_lockout(
    tmp_path: Path,
    value: object,
) -> None:
    with pytest.raises(TypeError, match="laser_lockout must be an exact boolean"):
        AppContext(_settings(tmp_path), laser_lockout=value)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="laser_lockout must be an exact boolean"):
        CoreRuntime(_settings(tmp_path), laser_lockout=value)  # type: ignore[arg-type]


def test_automatic_honeycomb_preserves_fresh_measured_rectangle_without_tick_recognition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    calibration = context.bed.calibration
    assert calibration is not None
    frame = np.asarray(
        [
            context.bed.mm_to_image(*point)
            for point in ((10.0, 200.0), (200.0, 200.0), (200.0, 10.0), (10.0, 10.0))
        ],
        dtype=np.float64,
    )
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    monkeypatch.setattr("laser_aligner.app.detect_honeycomb_frame", lambda _image: frame)

    reference, detection = context.detect_honeycomb_support_reference_automatically(
        np.zeros((440, 440, 3), dtype=np.uint8),
        ruler_mark_mm=190.0,
    )

    assert reference.ruler_origin_machine_mm == pytest.approx((10.0, 10.0))
    assert reference.ruler_x_mark_machine_mm == pytest.approx((200.0, 10.0))
    assert reference.ruler_xy_mark_machine_mm == pytest.approx((200.0, 200.0))
    assert reference.measured_ruler_span_mm == pytest.approx((190.0, 190.0))
    assert reference.schema_version == 2
    assert reference.has_four_corner_evidence is True
    assert np.asarray(reference.raw_corners_machine_mm) == pytest.approx(
        np.asarray(((10.0, 200.0), (200.0, 200.0), (200.0, 10.0), (10.0, 10.0)))
    )
    assert reference.corner_topology == (3, 2, 1, 0)
    assert reference.measurement_method == "automatic-four-edge-fit"
    assert detection.axis_x.tick_candidate_count == 0
    assert detection.axis_y.tick_candidate_count == 0


def _save_execution_support(context: AppContext) -> HoneycombSupportReference:
    calibration = context.bed.calibration
    assert calibration is not None
    reference = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            (10.0, 10.0),
            (200.0, 10.0),
            (200.0, 200.0),
            (10.0, 200.0),
        ),
        corner_topology=(0, 1, 2, 3),
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=calibration.created_at,
    )
    context.honeycomb_support.save(reference)
    return reference


def test_execution_pose_measurement_uses_broad_taught_registration_without_refitting_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    calibration = context.bed.calibration
    assert calibration is not None
    machine_corners = ((10.0, 10.0), (200.0, 10.0), (200.0, 200.0), (10.0, 200.0))
    teaching_corners = tuple(context.bed.mm_to_image(*point) for point in machine_corners)
    reference = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=machine_corners,
        corner_topology=(0, 1, 2, 3),
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=calibration.created_at,
    )
    image = np.zeros((440, 440, 3), dtype=np.uint8)
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    context.save_honeycomb_support_reference(
        reference,
        teaching_image=image,
        teaching_corners_px=teaching_corners,
    )
    registered = np.asarray(teaching_corners, dtype=np.float64) + (0.2, -0.1)
    monkeypatch.setattr(
        "laser_aligner.app.register_honeycomb_reference",
        lambda *_args: registered.copy(),
    )
    monkeypatch.setattr(
        "laser_aligner.app.detect_honeycomb_frame",
        lambda *_args, **_kwargs: pytest.fail(
            "execution verification must not require four uniform edge transitions"
        ),
    )

    observed, detection = context.detect_honeycomb_support_reference_automatically(
        image,
        ruler_mark_mm=190.0,
        require_taught_reference=True,
    )

    assert np.asarray(detection.frame_corners_image_px) == pytest.approx(registered)
    assert np.asarray(observed.raw_corners_machine_mm) == pytest.approx(
        np.asarray([context.bed.image_to_mm(*point) for point in registered])
    )


def _automatic_detection_for_corners(
    context: AppContext,
    machine_corners: tuple[tuple[float, float], ...],
) -> tuple[HoneycombSupportReference, HoneycombRulerDetection]:
    calibration = context.bed.calibration
    assert calibration is not None
    origin, x_corner, opposite, y_corner = machine_corners
    x_length = float(np.linalg.norm(np.asarray(x_corner) - np.asarray(origin)))
    y_length = float(np.linalg.norm(np.asarray(opposite) - np.asarray(x_corner)))
    x_vector = np.asarray(x_corner) - np.asarray(origin)
    y_vector = np.asarray(y_corner) - np.asarray(origin)
    angle = math.degrees(
        math.acos(
            min(1.0, abs(float(np.dot(x_vector, y_vector) / (x_length * y_length))))
        )
    )
    reference = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=machine_corners,
        corner_topology=(0, 1, 2, 3),
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=calibration.created_at,
    )
    detection = HoneycombRulerDetection(
        ruler_origin_image_px=origin,
        ruler_x_mark_image_px=x_corner,
        ruler_xy_mark_image_px=opposite,
        axis_x=RulerAxisDetection(origin, x_corner, 1.0, 0, 0.0, 0.0, 0.0),
        axis_y=RulerAxisDetection(x_corner, opposite, 1.0, 0, 0.0, 0.0, 0.0),
        corner_error_px=0.0,
        axis_angle_deg=angle,
        frame_corners_image_px=(origin, x_corner, opposite, y_corner),
    )
    return reference, detection


def test_execution_pose_verifier_accepts_fresh_matching_four_edge_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    recorded = _save_execution_support(context)
    corners = ((10.0, 10.0), (200.0, 10.0), (200.0, 200.0), (10.0, 200.0))
    observed = _automatic_detection_for_corners(
        context,
        corners,
    )
    monkeypatch.setattr(
        context,
        "capture_parked_work_area_reference",
        lambda: np.zeros((32, 32, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        context,
        "detect_honeycomb_support_reference_automatically",
        lambda _image, *, ruler_mark_mm, require_taught_reference=False: observed,
    )
    monkeypatch.setattr(context.bed, "image_to_mm", lambda x, y: (x, y))

    result = context.verify_honeycomb_pose_for_execution(
        context.honeycomb_execution_signature()
    )

    assert recorded.measured_ruler_span_mm == pytest.approx((190.0, 190.0))
    assert result["verified"] is True
    assert result["maximum_corner_error_mm"] == pytest.approx(0.0)
    assert result["maximum_edge_length_error_mm"] == pytest.approx(0.0)


def test_execution_pose_verifier_rejects_legacy_three_point_reference(
    tmp_path: Path,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
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

    assert context.honeycomb_execution_signature() is None
    with pytest.raises(CalibrationError, match="lacks four independent"):
        context.verify_honeycomb_pose_for_execution(
            ("honeycomb-rigid-frame", 2, "legacy", "bed-map")
        )


def test_execution_binding_check_is_camera_free_and_rejects_changed_support(
    tmp_path: Path,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    _save_execution_support(context)
    signature = context.honeycomb_execution_signature()
    assert signature is not None

    context.validate_honeycomb_execution_binding(signature)
    context.honeycomb_support.clear()

    with pytest.raises(CalibrationError, match="changed after job preparation"):
        context.validate_honeycomb_execution_binding(signature)


def test_legacy_annotated_reference_seeds_automatic_schema_two_upgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 220.0, 0.0, 0.0),
            BedPoint(220.0, 220.0, 220.0, 0.0),
            BedPoint(220.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        221,
        221,
    )
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    reference_image = np.zeros((221, 221, 3), dtype=np.uint8)
    write_image_atomic(context.honeycomb_visual_reference_path, reference_image)
    context.honeycomb_visual_reference_metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cutting_surface_corners_px": [
                    [10.0, 200.0],
                    [200.0, 200.0],
                    [200.0, 10.0],
                    [10.0, 10.0],
                ],
            }
        ),
        encoding="utf-8",
    )
    seeded = np.asarray(
        ((10.0, 200.0), (200.0, 200.0), (200.0, 10.0), (10.0, 10.0)),
        dtype=np.float64,
    )
    calls: list[tuple[np.ndarray | None, bool]] = []
    monkeypatch.setattr(
        "laser_aligner.app.register_honeycomb_reference",
        lambda *_args: seeded.copy(),
    )

    def detect(_image: np.ndarray, *, seed_corners=None) -> np.ndarray:
        calls.append((seed_corners, seed_corners is not None))
        return seeded.copy()

    monkeypatch.setattr("laser_aligner.app.detect_honeycomb_frame", detect)

    support, detection = context.detect_honeycomb_support_reference_automatically(
        reference_image,
        ruler_mark_mm=190.0,
    )

    assert support.is_execution_verifiable
    assert support.schema_version == 2
    assert len(calls) == 1
    assert calls[0][1] is True
    assert calls[0][0] == pytest.approx(seeded)
    assert np.asarray(detection.frame_corners_image_px) == pytest.approx(seeded)


def test_execution_pose_verifier_rejects_fresh_moved_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    _save_execution_support(context)
    corners = ((11.0, 10.0), (201.0, 10.0), (201.0, 200.0), (11.0, 200.0))
    observed = _automatic_detection_for_corners(
        context,
        corners,
    )
    monkeypatch.setattr(
        context,
        "capture_parked_work_area_reference",
        lambda: np.zeros((32, 32, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        context,
        "detect_honeycomb_support_reference_automatically",
        lambda _image, *, ruler_mark_mm, require_taught_reference=False: observed,
    )
    monkeypatch.setattr(context.bed, "image_to_mm", lambda x, y: (x, y))

    with pytest.raises(CalibrationError, match="honeycomb moved"):
        context.verify_honeycomb_pose_for_execution(
            context.honeycomb_execution_signature()
        )


def test_execution_pose_verifier_rejects_fresh_edge_length_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    _save_execution_support(context)
    # This remains inside the automatic detector's strict 2 mm nominal-side
    # acceptance, but exceeds the tighter 0.75 mm start-to-taught comparison.
    corners = ((10.0, 10.0), (198.5, 10.0), (198.5, 200.0), (10.0, 200.0))
    observed = _automatic_detection_for_corners(
        context,
        corners,
    )
    monkeypatch.setattr(
        context,
        "capture_parked_work_area_reference",
        lambda: np.zeros((32, 32, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        context,
        "detect_honeycomb_support_reference_automatically",
        lambda _image, *, ruler_mark_mm, require_taught_reference=False: observed,
    )
    monkeypatch.setattr(context.bed, "image_to_mm", lambda x, y: (x, y))

    with pytest.raises(CalibrationError, match="changed scale"):
        context.verify_honeycomb_pose_for_execution(
            context.honeycomb_execution_signature(),
            corner_tolerance_mm=20.0,
        )


def test_execution_pose_verifier_rejects_fresh_nonorthogonal_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    _save_execution_support(context)
    corners = ((10.0, 10.0), (200.0, 10.0), (203.3, 199.97), (13.3, 199.94))
    observed = _automatic_detection_for_corners(context, corners)
    monkeypatch.setattr(
        context,
        "capture_parked_work_area_reference",
        lambda: np.zeros((32, 32, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        context,
        "detect_honeycomb_support_reference_automatically",
        lambda _image, *, ruler_mark_mm, require_taught_reference=False: observed,
    )
    monkeypatch.setattr(context.bed, "image_to_mm", lambda x, y: (x, y))

    with pytest.raises(CalibrationError, match="not square enough"):
        context.verify_honeycomb_pose_for_execution(
            context.honeycomb_execution_signature(),
            corner_tolerance_mm=10.0,
            orthogonality_tolerance_deg=0.5,
        )


def test_execution_pose_verifier_preserves_taught_zero_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    _save_execution_support(context)
    canonical = ((10.0, 10.0), (200.0, 10.0), (200.0, 200.0), (10.0, 200.0))
    observed_reference, detection = _automatic_detection_for_corners(
        context,
        canonical,
    )
    # Registration preserves a cyclic topology but may begin at any corner and
    # may reverse winding. Verification must match it to the taught topology,
    # not infer a new zero from machine-axis extrema.
    detection = replace(
        detection,
        frame_corners_image_px=(canonical[2], canonical[1], canonical[0], canonical[3]),
    )
    monkeypatch.setattr(
        context,
        "capture_parked_work_area_reference",
        lambda: np.zeros((32, 32, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        context,
        "detect_honeycomb_support_reference_automatically",
        lambda _image, *, ruler_mark_mm, require_taught_reference=False: (
            observed_reference,
            detection,
        ),
    )
    monkeypatch.setattr(context.bed, "image_to_mm", lambda x, y: (x, y))

    result = context.verify_honeycomb_pose_for_execution(
        context.honeycomb_execution_signature()
    )

    assert result["maximum_corner_error_mm"] == pytest.approx(0.0)


def test_trace_camera_area_expands_to_show_current_honeycomb_without_changing_machine_area(
    tmp_path: Path,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    calibration = context.bed.calibration
    assert calibration is not None
    context.honeycomb_support.save(
        HoneycombSupportReference.from_observations(
            ruler_origin_machine_mm=(29.2, 37.3),
            ruler_x_mark_machine_mm=(219.2, 40.8),
            ruler_xy_mark_machine_mm=(217.6, 230.8),
            ruler_mark_mm=190.0,
            support_width_mm=190.0,
            support_height_mm=190.0,
            bed_calibration_created_at=calibration.created_at,
        )
    )

    configured = context.settings.machine.work_area
    expanded = context.trace_camera_work_area()

    assert (configured.x_min, configured.x_max, configured.y_min, configured.y_max) == (
        0.0,
        220.0,
        0.0,
        220.0,
    )
    assert expanded.x_min == 0.0
    assert expanded.x_max >= 221.0
    assert expanded.y_min == 0.0
    assert expanded.y_max >= 233.0
    frame = context.current_honeycomb_coordinate_frame()
    assert frame is not None
    assert frame.origin_machine_mm == pytest.approx((29.2, 37.3))

    context.clear_honeycomb_support_reference()
    assert context.current_honeycomb_coordinate_frame() is None


def test_execution_pose_verification_accepts_automatic_corner_agreement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    calibration = context.bed.calibration
    assert calibration is not None
    recorded = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            (20.0, 30.0),
            (210.0, 30.0),
            (210.0, 220.0),
            (20.0, 220.0),
        ),
        corner_topology=(0, 1, 2, 3),
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=calibration.created_at,
    )
    observed = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            (20.3, 29.8),
            (210.3, 29.8),
            (210.3, 219.8),
            (20.3, 219.8),
        ),
        corner_topology=(0, 1, 2, 3),
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=calibration.created_at,
    )
    signature = ("honeycomb-rigid-frame", 2, "pose", "bed-map")
    monkeypatch.setattr(context, "_current_honeycomb_support", lambda: recorded)
    monkeypatch.setattr(context, "honeycomb_execution_signature", lambda: signature)
    monkeypatch.setattr(
        context,
        "capture_parked_work_area_reference",
        lambda: np.zeros((12, 12, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        context,
        "detect_honeycomb_support_reference_automatically",
        lambda _image, **_kwargs: (
            observed,
            type(
                "Detection",
                (),
                {
                    "frame_corners_image_px": (
                        (20.3, 29.8),
                        (210.3, 29.8),
                        (210.3, 219.8),
                        (20.3, 219.8),
                    )
                },
            )(),
        ),
    )
    monkeypatch.setattr(context.bed, "image_to_mm", lambda x, y: (x, y))

    result = context.verify_honeycomb_pose_for_execution(signature)

    assert result["verified"] is True
    assert result["maximum_corner_error_mm"] == pytest.approx(
        np.hypot(0.3, 0.2)
    )
    assert result["corner_tolerance_mm"] == pytest.approx(0.5)


def test_execution_pose_verification_rejects_automatic_corner_displacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    calibration = context.bed.calibration
    assert calibration is not None
    recorded = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            (20.0, 30.0),
            (210.0, 30.0),
            (210.0, 220.0),
            (20.0, 220.0),
        ),
        corner_topology=(0, 1, 2, 3),
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=calibration.created_at,
    )
    displaced = HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            (20.0, 30.75),
            (210.0, 30.75),
            (210.0, 220.75),
            (20.0, 220.75),
        ),
        corner_topology=(0, 1, 2, 3),
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=calibration.created_at,
    )
    signature = ("honeycomb-rigid-frame", 2, "pose", "bed-map")
    monkeypatch.setattr(context, "_current_honeycomb_support", lambda: recorded)
    monkeypatch.setattr(context, "honeycomb_execution_signature", lambda: signature)
    monkeypatch.setattr(
        context,
        "capture_parked_work_area_reference",
        lambda: np.zeros((12, 12, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        context,
        "detect_honeycomb_support_reference_automatically",
        lambda _image, **_kwargs: (
            displaced,
            type(
                "Detection",
                (),
                {
                    "frame_corners_image_px": (
                        (20.0, 30.75),
                        (210.0, 30.75),
                        (210.0, 220.75),
                        (20.0, 220.75),
                    )
                },
            )(),
        ),
    )
    monkeypatch.setattr(context.bed, "image_to_mm", lambda x, y: (x, y))

    with pytest.raises(
        CalibrationError,
        match=r"differs by 0\.750 mm \(limit 0\.500 mm\)",
    ):
        context.verify_honeycomb_pose_for_execution(signature)


def test_honeycomb_clicks_seed_detection_but_never_define_saved_geometry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    calibration = context.bed.calibration
    assert calibration is not None
    calibration_before = calibration.to_dict()
    detected_points = tuple(
        context.bed.mm_to_image(*point)
        for point in ((15.0, 20.0), (205.0, 20.0), (205.0, 210.0))
    )
    detection = HoneycombRulerDetection(
        detected_points[0],
        detected_points[1],
        detected_points[2],
        RulerAxisDetection(
            detected_points[0], detected_points[1], 2.0, 190, 0.95, 8.0, 5.0
        ),
        RulerAxisDetection(
            detected_points[1], detected_points[2], 2.0, 191, 0.93, 5.0, 7.0
        ),
        5.0,
        89.5,
    )
    rough_hints = ((5.0, 5.0), (40.0, 80.0), (100.0, 120.0))
    received: dict[str, object] = {}

    def detect(image, hints, *, ruler_span_mm):
        received["image"] = image
        received["hints"] = hints
        received["span"] = ruler_span_mm
        return detection

    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    monkeypatch.setattr("laser_aligner.app.detect_honeycomb_rulers", detect)
    reference, returned = context.detect_honeycomb_support_reference(
        np.zeros((600, 800, 3), dtype=np.uint8),
        rough_hints,
        ruler_mark_mm=190.0,
    )
    context.save_honeycomb_support_reference(reference)

    assert returned is detection
    assert received["hints"] == rough_hints
    assert received["span"] == 190.0
    assert reference.ruler_origin_machine_mm == pytest.approx((15.0, 20.0))
    assert reference.ruler_x_mark_machine_mm == pytest.approx((205.0, 20.0))
    assert reference.ruler_xy_mark_machine_mm == pytest.approx((205.0, 210.0))
    assert rough_hints != detection.image_points
    assert context.bed.calibration is calibration
    assert context.bed.calibration.to_dict() == calibration_before


def test_honeycomb_detection_rejects_mapped_span_error_without_replacing_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 439.0, 0.0, 0.0),
            BedPoint(439.0, 439.0, 220.0, 0.0),
            BedPoint(439.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        440,
        440,
    )
    calibration = context.bed.calibration
    assert calibration is not None
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    existing = HoneycombSupportReference.from_observations(
        ruler_origin_machine_mm=(15.0, 15.0),
        ruler_x_mark_machine_mm=(205.0, 15.0),
        ruler_xy_mark_machine_mm=(205.0, 205.0),
        ruler_mark_mm=190.0,
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=calibration.created_at,
    )
    context.save_honeycomb_support_reference(existing)
    detected_machine = ((16.7, 11.6), (208.0, 11.6), (208.0, 196.1))
    detected_image = tuple(
        context.bed.mm_to_image(*point) for point in detected_machine
    )
    detection = HoneycombRulerDetection(
        detected_image[0],
        detected_image[1],
        detected_image[2],
        RulerAxisDetection(
            detected_image[0], detected_image[1], 2.0, 190, 0.95, 2.0, 2.0
        ),
        RulerAxisDetection(
            detected_image[1], detected_image[2], 2.0, 190, 0.95, 2.0, 2.0
        ),
        2.0,
        90.0,
    )
    monkeypatch.setattr(
        "laser_aligner.app.detect_honeycomb_rulers",
        lambda *_args, **_kwargs: detection,
    )

    with pytest.raises(
        CalibrationError,
        match=r"190 mm reference: Y measured 184\.5 mm",
    ):
        context.detect_honeycomb_support_reference(
            np.zeros((440, 440, 3), dtype=np.uint8),
            ((10.0, 10.0), (200.0, 10.0), (200.0, 200.0)),
            ruler_mark_mm=190.0,
        )

    assert context.honeycomb_support.reference == existing


def test_simulation_workspace_frame_is_memory_only_and_copy_isolated(
    tmp_path: Path,
) -> None:
    context = AppContext(_settings(tmp_path))
    image = np.full((440, 440, 3), (25, 80, 210), dtype=np.uint8)

    info = context.set_simulation_workspace_frame(
        image,
        source_name="generated Alpha labels",
        metadata={"center_x_mm": 117.0, "rotation_deg": 8.0},
    )
    image[:] = 0
    first = context.rectified_frame(refresh=True)
    first[:] = 1
    second = context.rectified_frame(refresh=False)

    assert info["source_name"] == "generated Alpha labels"
    assert info["width"] == 440
    assert context.has_simulation_workspace_frame
    assert tuple(second[0, 0]) == (25, 80, 210)
    assert context.simulation_workspace_frame_info()["metadata"] == {
        "center_x_mm": 117.0,
        "rotation_deg": 8.0,
    }
    assert context.simulation_workspace_frame_status() == {
        "active": True,
        "source_name": "generated Alpha labels",
        "width": 440,
        "height": 440,
        "pixels_per_mm": 2.0,
    }
    assert "metadata" not in context.status()["simulation_workspace_frame"]
    assert not context.workspace_path.exists()

    context.clear_simulation_workspace_frame()
    assert not context.has_simulation_workspace_frame
    assert context.simulation_workspace_frame_info() is None
    assert context.simulation_workspace_frame_status() is None


def test_bed_reference_prefers_lossless_png_but_reads_legacy_jpeg(tmp_path: Path) -> None:
    context = AppContext(_settings(tmp_path))
    legacy = np.full((20, 30, 3), 45, dtype=np.uint8)
    lossless = np.full((20, 30, 3), 190, dtype=np.uint8)
    write_image_atomic(context.legacy_bed_reference_path, legacy)

    first = context.bed_reference()
    write_image_atomic(context.bed_reference_path, lossless)
    second = context.bed_reference()

    assert float(np.mean(first)) == pytest.approx(45.0, abs=2.0)
    assert np.array_equal(second, lossless)


@pytest.mark.parametrize(
    ("simulation", "machine_backend", "hardware_enabled"),
    (
        (False, "simulator", False),
        (True, "serial", False),
        (True, "simulator", True),
    ),
)
def test_simulation_workspace_frame_enforces_the_full_safety_gate(
    tmp_path: Path,
    simulation: bool,
    machine_backend: str,
    hardware_enabled: bool,
) -> None:
    context = AppContext(
        _settings(
            tmp_path,
            simulation=simulation,
            machine_backend=machine_backend,
        ),
        hardware_enabled=hardware_enabled,
    )

    with pytest.raises(RuntimeError, match="Test images require simulation mode"):
        context.set_simulation_workspace_frame(
            np.zeros((440, 440, 3), dtype=np.uint8),
            source_name="unsafe",
        )


def test_simulation_workspace_frame_rejects_wrong_corrected_dimensions(
    tmp_path: Path,
) -> None:
    context = AppContext(_settings(tmp_path))

    with pytest.raises(ValueError, match="expected 440x440, got 640x480"):
        context.set_simulation_workspace_frame(
            np.zeros((480, 640, 3), dtype=np.uint8),
            source_name="wrong size",
        )
