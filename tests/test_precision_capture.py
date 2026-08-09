from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.service import CameraService, FrameBurst
from laser_aligner.config import CameraSettings, PrecisionCaptureSettings
from laser_aligner.errors import CameraError
from laser_aligner.imaging import write_image_atomic
from laser_aligner.vision import fiducials


def _profile(**overrides: object) -> PrecisionCaptureSettings:
    values = {
        "settle_seconds": 0.0,
        "discard_frames": 2,
        "sample_frames": 3,
        "timeout_seconds": 1.0,
        "minimum_valid_frames": 2,
        "mad_multiplier": 3.5,
        "outlier_floor_px": 0.25,
        "max_jitter_rms_px": 0.75,
        "coordinate_strategy": "median",
    }
    values.update(overrides)
    return PrecisionCaptureSettings(**values)


def test_camera_burst_waits_for_distinct_fresh_frames() -> None:
    service = CameraService(CameraSettings(precision_capture=_profile()))
    with service._frame_condition:
        service._connected = True
        service._frame = np.zeros((8, 8, 3), dtype=np.uint8)

    def produce() -> None:
        time.sleep(0.02)
        for value in range(1, 7):
            with service._frame_condition:
                service._frame = np.full((8, 8, 3), value, dtype=np.uint8)
                service._frames_read += 1
                service._frame_condition.notify_all()
            time.sleep(0.02)

    producer = threading.Thread(target=produce)
    producer.start()
    burst = service.capture_burst(reapply_controls=False)
    producer.join(timeout=1.0)

    assert burst.discarded_frames == 2
    assert len(burst.frames) == 3
    assert list(burst.sequence_numbers) == sorted(set(burst.sequence_numbers))
    assert [int(frame[0, 0, 0]) for frame in burst.frames] == [3, 4, 5]


def test_camera_burst_times_out_instead_of_reusing_a_stale_frame() -> None:
    service = CameraService(
        CameraSettings(
            precision_capture=_profile(
                discard_frames=0,
                sample_frames=1,
                minimum_valid_frames=1,
                timeout_seconds=0.02,
            )
        )
    )
    with service._frame_condition:
        service._connected = True
        service._frame = np.zeros((4, 4, 3), dtype=np.uint8)

    with pytest.raises(CameraError, match=r"sample 1/1"):
        service.capture_burst(reapply_controls=False)


def test_interactive_stable_frame_uses_short_burst_not_parked_bed_profile() -> None:
    frame = np.full((4, 4, 3), 30, dtype=np.uint8)
    captured_profiles: list[tuple[PrecisionCaptureSettings, bool]] = []
    burst = FrameBurst(
        frames=(frame,),
        sequence_numbers=(4,),
        discarded_frames=2,
        settle_seconds=0.1,
        elapsed_seconds=0.3,
        sharpness_scores=(5.0,),
        controls=ControlResult({}, {}, {}),
    )
    harness = SimpleNamespace(
        settings=SimpleNamespace(camera=SimpleNamespace(precision_capture=PrecisionCaptureSettings())),
        camera=SimpleNamespace(
            capture_burst=lambda profile, *, score_frames: (
                captured_profiles.append((profile, score_frames)) or burst
            ),
        ),
        lens=SimpleNamespace(model=None),
    )
    harness._stable_camera_burst = lambda: AppContext._stable_camera_burst(harness)
    harness._prepare_camera_burst = lambda value, *, undistort: (
        AppContext._prepare_camera_burst(harness, value, undistort=undistort)
    )

    image, diagnostics = AppContext.stable_camera_frame(harness)

    assert int(image[0, 0, 0]) == 30
    assert len(captured_profiles) == 1
    profile, score_frames = captured_profiles[0]
    assert score_frames is False
    assert profile.sample_frames == 5
    assert profile.discard_frames == 2
    assert profile.settle_seconds == pytest.approx(0.1)
    assert profile.timeout_seconds == pytest.approx(2.0)
    assert diagnostics["capture_class"] == "interactive_stable"


def test_raw_parked_burst_defers_scoring_until_after_caller_releases_hold() -> None:
    frame = np.full((4, 4, 3), 35, dtype=np.uint8)
    burst = FrameBurst(
        frames=(frame,),
        sequence_numbers=(9,),
        discarded_frames=0,
        settle_seconds=0.0,
        elapsed_seconds=0.1,
        sharpness_scores=(),
        controls=ControlResult({}, {}, {}),
    )
    calls: list[bool] = []
    harness = SimpleNamespace(
        settings=SimpleNamespace(
            camera=SimpleNamespace(precision_capture=PrecisionCaptureSettings())
        ),
        camera=SimpleNamespace(
            capture_burst=lambda _profile, *, score_frames: (
                calls.append(score_frames) or burst
            )
        ),
    )

    result = AppContext.precision_camera_burst(harness, undistort=False)

    assert result is burst
    assert calls == [False]
    assert result.sharpness_scores == ()


def test_deferred_scoring_rejects_a_camera_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = CameraService(CameraSettings())
    with camera._frame_condition:
        camera._connected = True
        camera._frame = np.zeros((4, 4, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()
    burst = FrameBurst(
        frames=(np.full((4, 4, 3), 44, dtype=np.uint8),),
        sequence_numbers=(1,),
        discarded_frames=0,
        settle_seconds=0.0,
        elapsed_seconds=0.1,
        sharpness_scores=(),
        controls=ControlResult({}, {}, {}),
        camera_generation=camera._generation,
    )
    harness = SimpleNamespace(camera=camera, lens=SimpleNamespace(model=None))

    def stop_during_score(_image: np.ndarray) -> float:
        camera.stop()
        return 5.0

    monkeypatch.setattr(camera, "_sharpness_score", stop_during_score)

    with pytest.raises(CameraError, match="stopped or restarted"):
        AppContext._prepare_camera_burst(harness, burst, undistort=False)


@pytest.mark.parametrize(
    ("capture_method", "analysis_method"),
    [
        ("capture_fine_registration", "analyze_fine_registration_burst"),
        ("capture_accuracy_validation", "analyze_accuracy_validation_burst"),
    ],
)
def test_registration_capture_homes_before_burst_and_can_skip_rehoming(
    capture_method: str,
    analysis_method: str,
) -> None:
    frame = np.full((4, 4, 3), 50, dtype=np.uint8)
    burst = FrameBurst(
        frames=(frame,),
        sequence_numbers=(7,),
        discarded_frames=0,
        settle_seconds=0.0,
        elapsed_seconds=0.1,
        sharpness_scores=(1.0,),
        controls=ControlResult({}, {}, {}),
    )
    calls: list[str] = []

    @contextmanager
    def temporary_hold():
        calls.append("hold:start")
        try:
            yield
        finally:
            calls.append("hold:end")

    harness = SimpleNamespace(
        _require_camera_calibration_ready=lambda: calls.append("camera:ready"),
        _require_session_execution=lambda _session, _label: calls.append("receipt") or {},
        _require_session_bed_mapping=lambda _session, _calibration, _label: calls.append(
            "map"
        ),
        bed=SimpleNamespace(calibration=object()),
        fine_registration_path=Path("missing-fine-session.json"),
        accuracy_validation_path=Path("missing-accuracy-session.json"),
        machine=SimpleNamespace(
            prepare_photo_position=lambda: calls.append("home"),
            temporary_stepper_hold=temporary_hold,
        ),
        precision_camera_burst=lambda undistort=True: calls.append(f"burst:{undistort}") or burst,
        _prepare_camera_burst=lambda value, undistort: (
            calls.append(f"process:{undistort}") or value
        ),
    )
    setattr(
        harness,
        analysis_method,
        lambda value: calls.append("analyze") or {"burst": value},
    )

    image, payload = getattr(AppContext, capture_method)(harness, home_first=True)
    assert calls == [
        "camera:ready",
        "receipt",
        "map",
        "hold:start",
        "home",
        "burst:False",
        "hold:end",
        "process:True",
        "analyze",
    ]
    assert image is not frame
    assert payload["burst"] is burst

    calls.clear()
    getattr(AppContext, capture_method)(harness, home_first=False)
    assert calls == [
        "camera:ready",
        "receipt",
        "map",
        "hold:start",
        "burst:False",
        "hold:end",
        "process:True",
        "analyze",
    ]


def test_dense_capture_uses_precision_burst_and_explicit_grid_mode() -> None:
    frames = (
        np.full((4, 4, 3), 10, dtype=np.uint8),
        np.full((4, 4, 3), 20, dtype=np.uint8),
    )
    burst = FrameBurst(
        frames=frames,
        sequence_numbers=(10, 11),
        discarded_frames=8,
        settle_seconds=1.5,
        elapsed_seconds=2.0,
        sharpness_scores=(1.0, 2.0),
        controls=ControlResult({}, {}, {}),
    )
    calls: list[object] = []

    @contextmanager
    def temporary_hold():
        calls.append("hold:start")
        try:
            yield
        finally:
            calls.append("hold:end")

    def analyze(
        image: np.ndarray,
        images: tuple[np.ndarray, ...],
        diagnostics: dict[str, object],
        *,
        validation: bool,
        confirmation: bool,
    ) -> dict[str, object]:
        calls.append((int(image[0, 0, 0]), len(images), validation, confirmation))
        assert diagnostics["sample_frames"] == 2
        return {
            "precision_capture": {
                "aggregation": {"selected_frame_index": 1},
            }
        }

    harness = SimpleNamespace(
        _require_camera_calibration_ready=lambda: calls.append("camera:ready"),
        _require_session_execution=lambda _session, _label: calls.append("receipt") or {},
        dense_calibration_path=Path("missing-dense-session.json"),
        dense_validation_path=Path("missing-dense-validation-session.json"),
        dense_confirmation_path=Path("missing-dense-confirmation-session.json"),
        machine=SimpleNamespace(
            prepare_photo_position=lambda: calls.append("home"),
            temporary_stepper_hold=temporary_hold,
        ),
        precision_camera_burst=lambda undistort=True: calls.append(f"burst:{undistort}") or burst,
        _prepare_camera_burst=lambda value, undistort: (
            calls.append(f"process:{undistort}") or value
        ),
        _analyze_dense_calibration_capture=analyze,
    )

    image, _ = AppContext.capture_dense_calibration(
        harness,
        validation=True,
        confirmation=False,
    )

    assert calls == [
        "camera:ready",
        "receipt",
        "hold:start",
        "home",
        "burst:False",
        "hold:end",
        "process:True",
        (20, 2, True, False),
    ]
    assert int(image[0, 0, 0]) == 20


def test_trace_capture_homes_and_holds_only_through_camera_frames(tmp_path) -> None:
    calls: list[str] = []
    frame = np.full((8, 8, 3), 80, dtype=np.uint8)
    holding = False
    burst = FrameBurst(
        frames=(frame,),
        sequence_numbers=(12,),
        discarded_frames=2,
        settle_seconds=0.1,
        elapsed_seconds=0.2,
        sharpness_scores=(),
        controls=ControlResult({}, {}, {}),
    )

    @contextmanager
    def temporary_hold():
        nonlocal holding
        calls.append("hold:start")
        holding = True
        try:
            yield
        finally:
            holding = False
            calls.append("hold:end")

    def capture(_profile: object, *, score_frames: bool) -> FrameBurst:
        assert holding
        assert score_frames is False
        calls.append("capture:raw")
        return burst

    def score(_image: np.ndarray) -> float:
        assert not holding, "Sharpness scoring must run after the motor hold is released"
        calls.append("score:False")
        return 5.0

    harness = SimpleNamespace(
        _simulation_workspace_lock=threading.RLock(),
        _simulation_workspace_image=None,
        _require_valid_bed_calibration=lambda: calls.append("validate"),
        machine=SimpleNamespace(
            temporary_stepper_hold=temporary_hold,
            prepare_photo_position=lambda: calls.append("home"),
        ),
        settings=SimpleNamespace(
            camera=SimpleNamespace(precision_capture=PrecisionCaptureSettings())
        ),
        camera=SimpleNamespace(
            capture_burst=capture,
            _sharpness_score=score,
        ),
        lens=SimpleNamespace(model=None),
        _rectify_camera_image=lambda image: calls.append("rectify") or image.copy(),
        workspace_path=tmp_path / "trace-workspace.png",
        _cache_workspace=lambda image: calls.append("cache"),
        _persist_workspace=lambda image: (
            calls.append("persist"),
            write_image_atomic(tmp_path / "trace-workspace.png", image),
        )[-1],
    )
    harness._stable_camera_burst = lambda: AppContext._stable_camera_burst(harness)
    harness._prepare_camera_burst = lambda value, *, undistort: (
        AppContext._prepare_camera_burst(harness, value, undistort=undistort)
    )

    result = AppContext.capture_parked_trace_frame(harness)

    assert calls == [
        "validate",
        "hold:start",
        "home",
        "capture:raw",
        "hold:end",
        "score:False",
        "rectify",
        "cache",
        "persist",
    ]
    assert np.array_equal(result, frame)
    assert harness.workspace_path.exists()


def test_trace_capture_uses_simulation_workspace_without_machine_activity() -> None:
    frame = np.full((4, 4, 3), 60, dtype=np.uint8)
    harness = SimpleNamespace(
        _simulation_workspace_lock=threading.RLock(),
        _simulation_workspace_image=frame,
    )

    result = AppContext.capture_parked_trace_frame(harness)

    assert np.array_equal(result, frame)
    assert result is not frame


def test_work_area_reference_holds_only_through_raw_frame_capture() -> None:
    calls: list[str] = []
    holding = False
    frame = np.full((8, 8, 3), 90, dtype=np.uint8)
    burst = FrameBurst(
        frames=(frame,),
        sequence_numbers=(3,),
        discarded_frames=2,
        settle_seconds=0.1,
        elapsed_seconds=0.2,
        sharpness_scores=(),
        controls=ControlResult({}, {}, {}),
    )
    calibration = object()

    @contextmanager
    def temporary_hold():
        nonlocal holding
        calls.append("hold:start")
        holding = True
        try:
            yield
        finally:
            holding = False
            calls.append("hold:end")

    def capture(_profile: object, *, score_frames: bool) -> FrameBurst:
        assert holding
        assert score_frames is False
        calls.append("capture:raw")
        return burst

    def score(_image: np.ndarray) -> float:
        assert not holding
        calls.append("score:released")
        return 4.0

    harness = SimpleNamespace(
        _require_camera_calibration_ready=lambda: calls.append("camera:ready"),
        _require_valid_bed_calibration=lambda: calls.append("bed:ready"),
        bed=SimpleNamespace(calibration=calibration),
        machine=SimpleNamespace(
            temporary_stepper_hold=temporary_hold,
            prepare_photo_position=lambda: calls.append("home"),
        ),
        settings=SimpleNamespace(
            camera=SimpleNamespace(precision_capture=PrecisionCaptureSettings())
        ),
        camera=SimpleNamespace(
            capture_burst=capture,
            _sharpness_score=score,
        ),
        lens=SimpleNamespace(model=None),
    )
    harness._stable_camera_burst = lambda: AppContext._stable_camera_burst(harness)
    harness._prepare_camera_burst = lambda value, *, undistort: (
        AppContext._prepare_camera_burst(harness, value, undistort=undistort)
    )

    result = AppContext.capture_parked_work_area_reference(harness)

    assert calls == [
        "camera:ready",
        "bed:ready",
        "hold:start",
        "home",
        "capture:raw",
        "hold:end",
        "score:released",
    ]
    assert np.array_equal(result, frame)
    assert result is not frame


def test_crosshair_burst_rejects_one_spatial_outlier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offsets = [
        -0.08,
        0.02,
        0.04,
        -0.03,
        0.01,
        -0.05,
        0.06,
        -0.02,
        0.03,
        -0.01,
        0.05,
        -0.04,
        0.00,
        0.02,
        4.0,
    ]
    images = [np.full((2, 2), index, dtype=np.uint8) for index in range(15)]
    expected = [
        {
            "id": 1,
            "image_x": 100.0,
            "image_y": 80.0,
            "machine_x": 25.0,
            "machine_y": 20.0,
        }
    ]

    def fake_detect(
        image: np.ndarray,
        expected_points: list[dict[str, object]],
        search_radius_px: int = 55,
    ) -> dict[str, object]:
        del expected_points, search_radius_px
        offset = offsets[int(image[0, 0])]
        return {
            "detected": True,
            "confidence": "high",
            "points": [
                {
                    "id": 1,
                    "image_x": 100.0 + offset,
                    "image_y": 80.0 - offset * 0.25,
                    "machine_x": 25.0,
                    "machine_y": 20.0,
                    "score": 100.0,
                    "shift_px": abs(offset),
                }
            ],
        }

    monkeypatch.setattr(fiducials, "detect_crosshairs_near", fake_detect)
    result = fiducials.detect_crosshairs_burst(
        images,
        expected,
        minimum_valid_frames=9,
        max_jitter_rms_px=0.75,
    )

    assert result["detected"] is True
    point = result["points"][0]
    assert point["inlier_count"] == 14
    assert point["outlier_count"] == 1
    assert point["image_x"] == pytest.approx(100.0, abs=0.03)
    assert result["capture_diagnostics"]["rejected_frame_count"] == 1


def test_crosshair_burst_rejects_consistently_jittery_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = [np.full((2, 2), index, dtype=np.uint8) for index in range(12)]
    expected = [
        {
            "id": 1,
            "image_x": 100.0,
            "image_y": 80.0,
            "machine_x": 25.0,
            "machine_y": 20.0,
        }
    ]

    def fake_detect(
        image: np.ndarray,
        expected_points: list[dict[str, object]],
        search_radius_px: int = 55,
    ) -> dict[str, object]:
        del expected_points, search_radius_px
        offset = -1.0 if int(image[0, 0]) % 2 else 1.0
        return {
            "detected": True,
            "confidence": "high",
            "points": [
                {
                    "id": 1,
                    "image_x": 100.0 + offset,
                    "image_y": 80.0,
                    "machine_x": 25.0,
                    "machine_y": 20.0,
                    "score": 100.0,
                    "shift_px": 1.0,
                }
            ],
        }

    monkeypatch.setattr(fiducials, "detect_crosshairs_near", fake_detect)
    result = fiducials.detect_crosshairs_burst(
        images,
        expected,
        minimum_valid_frames=9,
        max_jitter_rms_px=0.5,
    )

    assert result["detected"] is False
    assert "jitter" in result["reason"]


def test_crosshair_burst_uses_sharpest_frame_that_survives_all_mark_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = [np.full((2, 2), index, dtype=np.uint8) for index in range(3)]
    expected = [
        {"id": 1, "image_x": 100.0, "image_y": 80.0, "machine_x": 25.0, "machine_y": 20.0},
        {"id": 2, "image_x": 200.0, "image_y": 160.0, "machine_x": 50.0, "machine_y": 40.0},
    ]

    def fake_detect(
        image: np.ndarray,
        expected_points: list[dict[str, object]],
        search_radius_px: int = 55,
    ) -> dict[str, object]:
        del expected_points, search_radius_px
        index = int(image[0, 0])
        first_offset = (0.0, 0.1, 4.0)[index]
        second_offset = (0.0, 0.1, 0.2)[index]
        return {
            "detected": True,
            "confidence": "high",
            "points": [
                {
                    "id": 1,
                    "image_x": 100.0 + first_offset,
                    "image_y": 80.0,
                    "machine_x": 25.0,
                    "machine_y": 20.0,
                    "score": 100.0,
                    "shift_px": first_offset,
                },
                {
                    "id": 2,
                    "image_x": 200.0 + second_offset,
                    "image_y": 160.0,
                    "machine_x": 50.0,
                    "machine_y": 40.0,
                    "score": 100.0,
                    "shift_px": second_offset,
                },
            ],
        }

    monkeypatch.setattr(fiducials, "detect_crosshairs_near", fake_detect)
    result = fiducials.detect_crosshairs_burst(
        images,
        expected,
        minimum_valid_frames=2,
        coordinate_strategy="sharpest_inlier_frame",
        frame_quality_scores=(1.0, 5.0, 10.0),
    )

    assert result["detected"] is True
    assert result["capture_diagnostics"]["selected_frame_index"] == 1
    assert result["capture_diagnostics"]["eligible_frame_count"] == 2
    assert [point["image_x"] for point in result["points"]] == pytest.approx([100.1, 200.1])
    assert all(point["selected_frame_index"] == 1 for point in result["points"])


def test_crosshair_burst_uses_median_of_clarity_ranked_stable_frames(monkeypatch) -> None:
    images = [np.full((8, 8), index, dtype=np.uint8) for index in range(5)]
    expected = [
        {"id": 1, "image_x": 100.0, "image_y": 80.0, "machine_x": 25.0, "machine_y": 20.0},
        {"id": 2, "image_x": 200.0, "image_y": 160.0, "machine_x": 50.0, "machine_y": 40.0},
    ]
    offsets = [0.0, 0.1, 0.2, 0.3, 8.0]

    def fake_detect(image, _expected, *, search_radius_px):
        del search_radius_px
        offset = offsets[int(image[0, 0])]
        return {
            "detected": True,
            "confidence": "high",
            "points": [
                {
                    "id": target["id"],
                    "image_x": target["image_x"] + offset,
                    "image_y": target["image_y"],
                    "machine_x": target["machine_x"],
                    "machine_y": target["machine_y"],
                    "score": 100.0,
                    "shift_px": offset,
                }
                for target in expected
            ],
        }

    monkeypatch.setattr(fiducials, "detect_crosshairs_near", fake_detect)
    result = fiducials.detect_crosshairs_burst(
        images,
        expected,
        minimum_valid_frames=3,
        coordinate_strategy="stable_clarity_consensus",
        consensus_frames=3,
        frame_quality_scores=(1.0, 5.0, 4.0, 3.0, 100.0),
    )

    assert result["detected"] is True
    assert result["capture_diagnostics"]["selected_frame_indices"] == [1, 2, 3]
    assert result["capture_diagnostics"]["consensus_frame_count"] == 3
    assert [point["image_x"] for point in result["points"]] == pytest.approx([100.2, 200.2])
    assert all(point["consensus_frame_count"] == 3 for point in result["points"])


def test_crosshair_burst_rejects_an_incomplete_stable_consensus(monkeypatch) -> None:
    images = [np.full((4, 4), index, dtype=np.uint8) for index in range(3)]
    expected = [{"id": 1, "image_x": 10.0, "image_y": 12.0, "machine_x": 5.0, "machine_y": 6.0}]

    def fake_detect(image, _expected, *, search_radius_px):
        del search_radius_px
        offset = (0.0, 0.1, 5.0)[int(image[0, 0])]
        return {
            "detected": True,
            "confidence": "high",
            "points": [
                {
                    **expected[0],
                    "image_x": 10.0 + offset,
                    "score": 100.0,
                    "shift_px": offset,
                }
            ],
        }

    monkeypatch.setattr(fiducials, "detect_crosshairs_near", fake_detect)
    result = fiducials.detect_crosshairs_burst(
        images,
        expected,
        minimum_valid_frames=2,
        coordinate_strategy="stable_clarity_consensus",
        consensus_frames=3,
        frame_quality_scores=(1.0, 2.0, 3.0),
    )

    assert result["detected"] is False
    assert "only 2 frames survived" in result["reason"]
    assert result["capture_diagnostics"]["required_consensus_frames"] == 3
