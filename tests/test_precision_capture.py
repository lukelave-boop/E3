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
from laser_aligner.errors import CameraError, MachineError
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
        "home",
        "hold:start",
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
        "home",
        "hold:start",
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

    timing: dict[str, float] = {}
    result = AppContext.capture_parked_trace_frame(harness, timing=timing)

    assert calls == [
        "validate",
        "home",
        "hold:start",
        "capture:raw",
        "hold:end",
        "score:False",
        "rectify",
        "cache",
        "persist",
    ]
    assert np.array_equal(result, frame)
    assert harness.workspace_path.exists()
    assert timing.keys() == {
        "prepare_photo_seconds",
        "hold_acquisition_seconds",
        "camera_burst_seconds",
        "precision_capture_total_seconds",
        "capture_seconds",
        "rectification_seconds",
        "capture_rectification_total_seconds",
    }
    assert all(value >= 0.0 for value in timing.values())
    assert timing["precision_capture_total_seconds"] >= (
        timing["prepare_photo_seconds"]
        + timing["hold_acquisition_seconds"]
        + timing["camera_burst_seconds"]
    )
    assert timing["capture_seconds"] >= timing["precision_capture_total_seconds"]
    assert timing["capture_rectification_total_seconds"] >= (
        timing["capture_seconds"] + timing["rectification_seconds"]
    )


def test_trace_capture_home_failure_never_acquires_stepper_hold() -> None:
    calls: list[str] = []

    @contextmanager
    def unexpected_hold():
        calls.append("hold:start")
        yield

    def fail_home() -> None:
        calls.append("home")
        raise MachineError("simulated Home / park failure")

    harness = SimpleNamespace(
        _require_valid_bed_calibration=lambda: calls.append("validate"),
        machine=SimpleNamespace(
            prepare_photo_position=fail_home,
            temporary_stepper_hold=unexpected_hold,
        ),
    )

    with pytest.raises(MachineError, match="Home / park failure"):
        AppContext.capture_parked_trace_frame(harness)

    assert calls == ["validate", "home"]


def test_trace_capture_exception_releases_stepper_hold() -> None:
    calls: list[str] = []
    holding = False

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

    def fail_capture() -> None:
        assert holding
        calls.append("capture:raw")
        raise CameraError("simulated capture cancellation")

    harness = SimpleNamespace(
        _require_valid_bed_calibration=lambda: calls.append("validate"),
        machine=SimpleNamespace(
            prepare_photo_position=lambda: calls.append("home"),
            temporary_stepper_hold=temporary_hold,
        ),
        _stable_camera_burst=fail_capture,
    )

    with pytest.raises(CameraError, match="capture cancellation"):
        AppContext.capture_parked_trace_frame(harness)

    assert holding is False
    assert calls == [
        "validate",
        "home",
        "hold:start",
        "capture:raw",
        "hold:end",
    ]


def _removed_simulation_workspace_trace_capture_case() -> None:
    frame = np.full((4, 4, 3), 60, dtype=np.uint8)
    harness = SimpleNamespace(
        _simulation_workspace_lock=threading.RLock(),
        _simulation_workspace_image=frame,
    )

    result = AppContext.capture_parked_trace_frame(harness)

    assert np.array_equal(result, frame)
    assert result is not frame


def test_work_area_reference_holds_only_through_raw_frame_capture(
    tmp_path: Path,
) -> None:
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
    calibration = SimpleNamespace(created_at=7.0)

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

    home_position_snapshot = {
        "available": True,
        "state": "Idle",
        "mpos_mm": [0.0, 0.0, 0.0],
        "wpos_mm": [0.0, 0.0, 0.0],
        "wco_mm": [0.0, 0.0, 0.0],
    }
    coordinate_state = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }

    def prepare_photo_position(**kwargs: object) -> dict[str, object]:
        assert not holding
        assert kwargs == {"capture_home_position": True}
        calls.append("home")
        return {
            "homed": True,
            "parked": True,
            "coordinate_state": coordinate_state,
            "home_position_snapshot": home_position_snapshot,
        }

    positions = (
        {
            "state": "Idle",
            "mpos_mm": [109.998, 110.001, 0.0],
            "wpos_mm": [109.998, 110.001, 0.0],
            "wco_mm": [0.0, 0.0, 0.0],
            "wco_source": "reported",
            "derived_fields": [],
            "xy_complete": True,
            "raw_status": (
                "<Idle|MPos:109.998,110.001,0.000|WCO:0.000,0.000,0.000>"
            ),
            "sampled_at": 2.0,
        },
        {
            "state": "Idle",
            "mpos_mm": [110.003, 109.996, 0.0],
            "wpos_mm": [110.003, 109.996, 0.0],
            "wco_mm": [0.0, 0.0, 0.0],
            "wco_source": "reported",
            "derived_fields": [],
            "xy_complete": True,
            "raw_status": (
                "<Idle|MPos:110.003,109.996,0.000|WCO:0.000,0.000,0.000>"
            ),
            "sampled_at": 3.0,
        },
    )
    sample_count = 0

    def sample_position() -> dict[str, object]:
        nonlocal sample_count
        assert not holding
        calls.append(f"position:{sample_count + 1}")
        result = positions[sample_count]
        sample_count += 1
        return result

    harness = SimpleNamespace(
        _require_camera_calibration_ready=lambda: calls.append("camera:ready"),
        _require_valid_bed_calibration=lambda: calls.append("bed:ready"),
        bed=SimpleNamespace(calibration=calibration),
        machine=SimpleNamespace(
            temporary_stepper_hold=temporary_hold,
            prepare_photo_position=prepare_photo_position,
            status=lambda: {
                "protocol": "grbl",
                "coordinate_reference_ready": False,
            },
            sample_realtime_position=sample_position,
        ),
        settings=SimpleNamespace(
            machine=SimpleNamespace(
                backend="serial",
                photo_x=110.0,
                photo_y=110.0,
                photo_z=None,
            ),
            camera=SimpleNamespace(precision_capture=PrecisionCaptureSettings()),
        ),
        camera=SimpleNamespace(
            capture_burst=capture,
            _sharpness_score=score,
        ),
        lens=SimpleNamespace(model=None),
        honeycomb_detection_input_path=tmp_path / "honeycomb-detection-test.png",
        _coordinate_audit_lock=threading.RLock(),
        _coordinate_audit_capture_snapshot=None,
    )
    harness._position_snapshot_xy = AppContext._position_snapshot_xy
    harness._coordinate_capture_delta_mm = lambda before, after: (
        AppContext._coordinate_capture_delta_mm(before, after)
    )
    harness._sample_coordinate_audit_position = lambda: (
        AppContext._sample_coordinate_audit_position(harness)
    )
    harness._record_coordinate_audit_capture = lambda **kwargs: (
        AppContext._record_coordinate_audit_capture(harness, **kwargs)
    )
    harness.bed_mapping_digest = lambda: "mapping-digest"
    harness._stable_camera_burst = lambda: AppContext._stable_camera_burst(harness)
    harness._prepare_camera_burst = lambda value, *, undistort: (
        AppContext._prepare_camera_burst(harness, value, undistort=undistort)
    )

    result = AppContext.capture_parked_work_area_reference(harness)

    assert calls == [
        "camera:ready",
        "bed:ready",
        "home",
        "position:1",
        "hold:start",
        "capture:raw",
        "hold:end",
        "position:2",
        "score:released",
    ]
    assert np.array_equal(result, frame)
    assert result is not frame
    snapshot = harness._coordinate_audit_capture_snapshot
    assert snapshot["position_immediately_after_home"]["mpos_mm"] == pytest.approx(
        [0.0, 0.0, 0.0]
    )
    assert snapshot["position_before_capture"]["wpos_mm"] == pytest.approx(
        [109.998, 110.001, 0.0]
    )
    assert snapshot["position_after_capture"]["wpos_mm"] == pytest.approx(
        [110.003, 109.996, 0.0]
    )
    assert snapshot["position_stable_during_capture"] is True
    assert snapshot["maximum_position_delta_mm"] == pytest.approx(2**0.5 * 0.005)
    assert snapshot["commanded_position_error_xy_mm"] == pytest.approx(
        [0.003, -0.004]
    )
    assert snapshot["commanded_position_error_mm"] == pytest.approx(0.005)
    assert snapshot["coordinate_state"] == coordinate_state
    assert snapshot["bed_calibration_created_at"] == pytest.approx(7.0)
    assert snapshot["bed_mapping_digest"] == "mapping-digest"
    assert snapshot["trusted_at_capture"] is True
    assert snapshot["motors_released_after_capture"] is True
    assert snapshot["current_position_trusted_after_cleanup"] is False


def test_coordinate_audit_snapshot_is_not_published_when_image_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    frame = np.full((8, 8, 3), 95, dtype=np.uint8)

    @contextmanager
    def temporary_hold():
        calls.append("hold:start")
        try:
            yield
        finally:
            calls.append("hold:end")

    def prepare_photo_position(**kwargs: object) -> dict[str, object]:
        assert kwargs == {"capture_home_position": True}
        calls.append("home")
        return {"homed": True, "parked": True}

    def sample_position() -> dict[str, object]:
        calls.append("position")
        return {
            "available": True,
            "state": "Idle",
            "mpos_mm": [110.0, 110.0, 0.0],
            "wpos_mm": [110.0, 110.0, 0.0],
            "wco_mm": [0.0, 0.0, 0.0],
        }

    def fail_image_write(*_args: object, **_kwargs: object) -> None:
        calls.append("write")
        raise OSError("simulated audit image write failure")

    monkeypatch.setattr("laser_aligner.app.write_image_atomic", fail_image_write)
    calibration = SimpleNamespace(created_at=7.0)
    harness = SimpleNamespace(
        _require_camera_calibration_ready=lambda: calls.append("camera:ready"),
        _require_valid_bed_calibration=lambda: calls.append("bed:ready"),
        bed=SimpleNamespace(calibration=calibration),
        machine=SimpleNamespace(
            temporary_stepper_hold=temporary_hold,
            prepare_photo_position=prepare_photo_position,
        ),
        _sample_coordinate_audit_position=sample_position,
        _stable_camera_burst=lambda: calls.append("capture:raw") or object(),
        _prepare_camera_burst=lambda value, *, undistort: (
            calls.append(f"process:{undistort}")
            or SimpleNamespace(sharpest_frame=frame)
        ),
        honeycomb_detection_input_path=tmp_path / "honeycomb-detection-test.png",
        _coordinate_audit_lock=threading.RLock(),
        _coordinate_audit_capture_snapshot={"trusted_at_capture": True},
    )

    with pytest.raises(OSError, match="simulated audit image write failure"):
        AppContext.capture_parked_work_area_reference(harness)

    assert calls == [
        "camera:ready",
        "bed:ready",
        "home",
        "position",
        "hold:start",
        "capture:raw",
        "hold:end",
        "position",
        "process:True",
        "write",
    ]
    assert harness._coordinate_audit_capture_snapshot is None


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


def test_crosshair_burst_rejects_malformed_frames_and_numeric_gates() -> None:
    expected = [
        {
            "id": 1,
            "image_x": 10.0,
            "image_y": 12.0,
            "machine_x": 5.0,
            "machine_y": 6.0,
        }
    ]
    frame = np.zeros((4, 4), dtype=np.uint8)

    with pytest.raises(ValueError, match="uint8"):
        fiducials.detect_crosshairs_burst(
            [np.empty((0, 0, 3), dtype=np.uint8)],
            expected,
        )
    with pytest.raises(ValueError, match="finite"):
        fiducials.detect_crosshairs_burst(
            [frame],
            expected,
            frame_quality_scores=[float("nan")],
        )
    with pytest.raises(ValueError, match="cannot exceed"):
        fiducials.detect_crosshairs_burst(
            [frame],
            expected,
            minimum_valid_frames=2,
        )


def test_crosshair_burst_rejects_duplicate_or_nonfinite_expected_marks() -> None:
    frame = np.zeros((4, 4), dtype=np.uint8)
    expected = {
        "id": 1,
        "image_x": 10.0,
        "image_y": 12.0,
        "machine_x": 5.0,
        "machine_y": 6.0,
    }

    with pytest.raises(ValueError, match="unique"):
        fiducials.detect_crosshairs_burst([frame], [expected, dict(expected)])
    with pytest.raises(ValueError, match="finite"):
        fiducials.detect_crosshairs_burst(
            [frame],
            [{**expected, "image_x": float("nan")}],
        )
