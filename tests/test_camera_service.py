from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from laser_aligner.camera import service as camera_service_module
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.service import CameraService
from laser_aligner.config import CameraSettings, PrecisionCaptureSettings
from laser_aligner.errors import CameraError


def _profile(*, sample_frames: int = 2, timeout_seconds: float = 1.0) -> PrecisionCaptureSettings:
    return PrecisionCaptureSettings(
        settle_seconds=0.0,
        discard_frames=0,
        sample_frames=sample_frames,
        timeout_seconds=timeout_seconds,
        minimum_valid_frames=1,
        coordinate_strategy="median",
        consensus_frames=1,
    )


def _publish(camera: CameraService, value: int) -> None:
    with camera._frame_condition:
        camera._frame = np.full((4, 4, 3), value, dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()
        camera._frames_read += 1
        camera._frame_condition.notify_all()


def _wait_for_operation(camera: CameraService, label: str, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while camera.status().operation != label:
        if time.monotonic() >= deadline:
            pytest.fail(f"Camera operation {label!r} did not start")
        time.sleep(0.005)


def test_non_consensus_profile_allows_unused_consensus_count_above_sample() -> None:
    profile = _profile(sample_frames=2)
    profile.consensus_frames = 15

    CameraService._validate_capture_profile(profile)


def test_consensus_profile_rejects_consensus_count_above_sample() -> None:
    profile = _profile(sample_frames=2)
    profile.coordinate_strategy = "stable_clarity_consensus"
    profile.consensus_frames = 15

    with pytest.raises(CameraError, match="consensus count"):
        CameraService._validate_capture_profile(profile)


def test_snapshot_after_waits_for_a_new_frame() -> None:
    camera = CameraService(CameraSettings())
    with camera._lock:
        camera._connected = True
        camera._frame = np.zeros((2, 2, 3), dtype=np.uint8)
        camera._frames_read = 4

    def publish() -> None:
        time.sleep(0.03)
        with camera._frame_condition:
            camera._frame = np.full((2, 2, 3), 73, dtype=np.uint8)
            camera._frame_monotonic = time.monotonic()
            camera._frames_read = 5
            camera._frame_condition.notify_all()

    thread = threading.Thread(target=publish)
    thread.start()
    try:
        frame = camera.snapshot_after(4, timeout=0.5)
    finally:
        thread.join()
    assert np.all(frame == 73)


def test_snapshot_after_rejects_a_stale_frame() -> None:
    camera = CameraService(CameraSettings())
    with camera._lock:
        camera._connected = True
        camera._frame = np.zeros((2, 2, 3), dtype=np.uint8)
        camera._frames_read = 4
    with pytest.raises(CameraError, match="fresh frame"):
        camera.snapshot_after(4, timeout=0.03)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0.0, True])
def test_snapshot_after_rejects_invalid_timeouts(timeout: object) -> None:
    camera = CameraService(CameraSettings())

    with pytest.raises(CameraError, match="positive finite"):
        camera.snapshot_after(0, timeout=timeout)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("settle_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("sample_frames", True),
        ("minimum_valid_frames", 3),
        ("mad_multiplier", float("nan")),
        ("max_jitter_rms_px", 0.0),
    ],
)
def test_capture_burst_rejects_malformed_profile_values(
    field_name: str,
    value: object,
) -> None:
    profile = _profile()
    setattr(profile, field_name, value)
    camera = CameraService(CameraSettings(precision_capture=profile))

    with pytest.raises(CameraError, match="Precision capture"):
        camera.capture_burst(reapply_controls=False)


def test_snapshot_rejects_disconnected_or_old_cached_frame() -> None:
    camera = CameraService(CameraSettings(fps=30))
    with camera._lock:
        camera._connected = True
        camera._frame = np.zeros((2, 2, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic() - 3.0
        camera._last_error = "Camera read failed"

    with pytest.raises(CameraError, match=r"stale.*Camera read failed"):
        camera.snapshot()

    with camera._lock:
        camera._connected = False
        camera._frame_monotonic = time.monotonic()
    with pytest.raises(CameraError, match="not connected"):
        camera.snapshot()


def test_camera_status_reports_monotonic_frame_age() -> None:
    camera = CameraService(CameraSettings())
    with camera._lock:
        camera._connected = True
        camera._frame = np.zeros((2, 2, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic() - 0.25

    status = camera.status()

    assert status.frame_age_seconds == pytest.approx(0.25, abs=0.05)


def test_concurrent_precision_bursts_have_exclusive_frame_sequences() -> None:
    camera = CameraService(CameraSettings(precision_capture=_profile()))
    with camera._frame_condition:
        camera._connected = True
        camera._frame = np.zeros((4, 4, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()

    bursts: list[object] = []
    failures: list[Exception] = []

    def capture() -> None:
        try:
            bursts.append(camera.capture_burst(reapply_controls=False))
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    first = threading.Thread(target=capture)
    second = threading.Thread(target=capture)
    first.start()
    _wait_for_operation(camera, "precision capture")
    second.start()
    for value in range(1, 5):
        _publish(camera, value)
        time.sleep(0.03)
    first.join(timeout=1.0)
    second.join(timeout=1.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    assert len(bursts) == 2
    ordered = sorted(bursts, key=lambda burst: burst.sequence_numbers[0])
    assert ordered[0].sequence_numbers == (1, 2)
    assert ordered[1].sequence_numbers == (3, 4)
    assert set(ordered[0].sequence_numbers).isdisjoint(ordered[1].sequence_numbers)


def test_preview_snapshot_remains_available_during_precision_capture() -> None:
    camera = CameraService(CameraSettings(precision_capture=_profile(timeout_seconds=5.0)))
    original = np.full((8, 8, 3), 41, dtype=np.uint8)
    with camera._frame_condition:
        camera._connected = True
        camera._frame = original
        camera._frame_monotonic = time.monotonic()

    failure: list[Exception] = []

    def capture() -> None:
        try:
            camera.capture_burst(reapply_controls=False)
        except Exception as exc:
            failure.append(exc)

    thread = threading.Thread(target=capture)
    thread.start()
    _wait_for_operation(camera, "precision capture")

    preview = camera.snapshot()
    preview[:] = 0
    assert np.all(original == 41)

    camera.stop()
    thread.join(timeout=0.5)
    assert not thread.is_alive()
    assert len(failure) == 1
    assert "stopped" in str(failure[0]).lower()


def test_restart_cancels_an_inflight_burst_before_starting_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = CameraService(CameraSettings(precision_capture=_profile(timeout_seconds=5.0)))
    with camera._frame_condition:
        camera._connected = True
        camera._frame = np.zeros((4, 4, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()

    failure: list[Exception] = []
    restarted: list[int] = []

    def fake_start(generation: int) -> None:
        restarted.append(generation)
        with camera._frame_condition:
            camera._connected = True
            camera._frame = np.full((4, 4, 3), 88, dtype=np.uint8)
            camera._frame_monotonic = time.monotonic()

    monkeypatch.setattr(camera, "_start_owned", fake_start)

    def capture() -> None:
        try:
            camera.capture_burst(reapply_controls=False)
        except Exception as exc:
            failure.append(exc)

    thread = threading.Thread(target=capture)
    thread.start()
    _wait_for_operation(camera, "precision capture")
    camera.restart()
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert len(failure) == 1
    assert "stopped" in str(failure[0]).lower()
    assert restarted == [camera._generation]
    assert camera.snapshot()[0, 0, 0] == 88


def test_control_update_and_precision_capture_never_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = CameraService(CameraSettings(precision_capture=_profile(sample_frames=1)))
    with camera._frame_condition:
        camera._connected = True
        camera._frame = np.zeros((4, 4, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()

    first_control_entered = threading.Event()
    release_first_control = threading.Event()
    active = 0
    maximum_active = 0
    calls = 0
    guard = threading.Lock()

    def apply_controls(
        requested: object,
        *,
        timeout_seconds: float,
        cancelled: object,
    ) -> ControlResult:
        del requested, timeout_seconds, cancelled
        nonlocal active, maximum_active, calls
        with guard:
            calls += 1
            active += 1
            maximum_active = max(maximum_active, active)
            current = calls
        if current == 1:
            first_control_entered.set()
            assert release_first_control.wait(timeout=1.0)
        with guard:
            active -= 1
        return ControlResult({}, {}, {})

    monkeypatch.setattr(camera, "_apply_controls_to_device", apply_controls)
    burst_failure: list[Exception] = []
    control_failure: list[Exception] = []

    def capture() -> None:
        try:
            camera.capture_burst()
        except Exception as exc:
            burst_failure.append(exc)

    def update_controls() -> None:
        try:
            camera.apply_controls({"focus_absolute": 40})
        except Exception as exc:
            control_failure.append(exc)

    burst_thread = threading.Thread(target=capture)
    control_thread = threading.Thread(target=update_controls)
    burst_thread.start()
    assert first_control_entered.wait(timeout=0.5)
    control_thread.start()
    time.sleep(0.03)
    assert calls == 1
    release_first_control.set()
    time.sleep(0.03)
    _publish(camera, 52)
    burst_thread.join(timeout=1.0)
    control_thread.join(timeout=1.0)

    assert not burst_thread.is_alive()
    assert not control_thread.is_alive()
    assert burst_failure == []
    assert control_failure == []
    assert calls == 2
    assert maximum_active == 1


def test_stop_cancels_control_application_without_waiting_for_its_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = CameraService(CameraSettings())
    with camera._frame_condition:
        camera._connected = True
        camera._frame = np.zeros((4, 4, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()

    entered = threading.Event()
    failure: list[Exception] = []

    def apply_controls(
        requested: object,
        *,
        timeout_seconds: float,
        cancelled: object,
    ) -> ControlResult:
        del requested, timeout_seconds
        entered.set()
        deadline = time.monotonic() + 1.0
        while not cancelled() and time.monotonic() < deadline:  # type: ignore[operator]
            time.sleep(0.005)
        return ControlResult({}, {}, {})

    monkeypatch.setattr(camera, "_apply_controls_to_device", apply_controls)

    def update_controls() -> None:
        try:
            camera.apply_controls({"focus_absolute": 40})
        except Exception as exc:
            failure.append(exc)

    thread = threading.Thread(target=update_controls)
    thread.start()
    assert entered.wait(timeout=0.5)
    started = time.monotonic()
    camera.stop()
    elapsed = time.monotonic() - started
    thread.join(timeout=0.5)

    assert elapsed < 0.25
    assert not thread.is_alive()
    assert len(failure) == 1
    assert "stopped" in str(failure[0]).lower()


def test_stop_releases_a_blocked_driver_read_before_joining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingCapture:
        def __init__(self) -> None:
            self.released = threading.Event()
            self.reader_entered = threading.Event()
            self.read_count = 0

        def isOpened(self) -> bool:
            return True

        def set(self, _property: int, _value: float) -> bool:
            return True

        def get(self, property_id: int) -> float:
            if property_id == camera_service_module.cv2.CAP_PROP_FPS:
                return 12.5
            return 0.0

        def read(self) -> tuple[bool, np.ndarray | None]:
            self.read_count += 1
            if self.read_count == 1:
                return True, np.full((6, 8, 3), 17, dtype=np.uint8)
            self.reader_entered.set()
            self.released.wait(timeout=5.0)
            return False, None

        def release(self) -> None:
            self.released.set()

    capture = BlockingCapture()
    monkeypatch.setattr(
        camera_service_module.cv2,
        "VideoCapture",
        lambda *_args: capture,
    )
    camera = CameraService(CameraSettings(warmup_frames=1, controls={}))
    camera.start()
    assert capture.reader_entered.wait(timeout=0.5)
    assert camera.status().negotiated_fps == 12.5

    started = time.monotonic()
    camera.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert not camera.status().connected
    assert camera._thread is None


def test_precision_deadline_includes_settling_time() -> None:
    profile = _profile(sample_frames=1, timeout_seconds=0.03)
    profile.settle_seconds = 0.10
    camera = CameraService(CameraSettings(precision_capture=profile))
    with camera._frame_condition:
        camera._connected = True
        camera._frame = np.zeros((4, 4, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()

    started = time.monotonic()
    with pytest.raises(CameraError, match="deadline expired during settling"):
        camera.capture_burst(reapply_controls=False)

    assert time.monotonic() - started < 0.15


def test_sharpness_analysis_runs_after_releasing_camera_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = CameraService(
        CameraSettings(precision_capture=_profile(sample_frames=1))
    )
    with camera._frame_condition:
        camera._connected = True
        camera._frame = np.zeros((4, 4, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()

    analysis_entered = threading.Event()
    release_analysis = threading.Event()
    result: list[object] = []

    def score(_image: np.ndarray) -> float:
        analysis_entered.set()
        assert release_analysis.wait(timeout=1.0)
        return 123.0

    monkeypatch.setattr(camera, "_sharpness_score", score)
    thread = threading.Thread(
        target=lambda: result.append(camera.capture_burst(reapply_controls=False))
    )
    thread.start()
    _wait_for_operation(camera, "precision capture")
    _publish(camera, 64)
    assert analysis_entered.wait(timeout=0.5)

    assert camera.status().operation is None
    assert camera.snapshot()[0, 0, 0] == 64

    release_analysis.set()
    thread.join(timeout=0.5)
    assert not thread.is_alive()
    assert result[0].sharpness_scores == (123.0,)


@pytest.mark.parametrize(
    "requested",
    (
        {"focus-absolute": 40},
        {"focus_absolute": 40.5},
        {"focus_absolute": "40"},
    ),
)
def test_camera_service_rejects_malformed_control_requests(
    requested: object,
) -> None:
    camera = CameraService(CameraSettings())

    with pytest.raises(CameraError, match="Camera control"):
        camera.apply_controls(requested)  # type: ignore[arg-type]


@pytest.mark.parametrize("quality", (True, 90.5, "90"))
def test_jpeg_rejects_noninteger_quality(quality: object) -> None:
    camera = CameraService(CameraSettings())
    with camera._lock:
        camera._connected = True
        camera._frame = np.zeros((4, 4, 3), dtype=np.uint8)
        camera._frame_monotonic = time.monotonic()

    with pytest.raises(CameraError, match="JPEG quality must be an integer"):
        camera.jpeg(quality)  # type: ignore[arg-type]


@pytest.mark.parametrize("fps", (float("nan"), float("inf"), 0.0, True, "bad"))
def test_mjpeg_rejects_invalid_target_fps(fps: object) -> None:
    stream = CameraService(CameraSettings()).mjpeg(fps)  # type: ignore[arg-type]

    with pytest.raises(CameraError, match="positive finite"):
        next(stream)


@pytest.mark.parametrize(
    ("frame", "valid"),
    (
        (np.zeros((4, 4, 3), dtype=np.uint8), True),
        (np.zeros((4, 4), dtype=np.uint8), True),
        (np.empty((0, 0, 3), dtype=np.uint8), False),
        (np.zeros((4, 4, 3), dtype=np.float32), False),
        (np.zeros((4, 4, 2), dtype=np.uint8), False),
    ),
)
def test_camera_frame_validation_rejects_malformed_driver_frames(
    frame: np.ndarray,
    valid: bool,
) -> None:
    assert CameraService._valid_frame(frame) is valid
