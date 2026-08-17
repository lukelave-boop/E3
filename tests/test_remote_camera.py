from __future__ import annotations

import socket
import threading
import time
from collections.abc import Mapping
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from laser_aligner.camera import bridge as camera_bridge
from laser_aligner.camera import remote as camera_remote
from laser_aligner.camera.bridge import CameraBridgeServer
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.remote import (
    RemoteCameraService,
    _status_probe_delay,
)
from laser_aligner.camera.service import CameraStatus, CompressedCameraFrame, FrameBurst
from laser_aligner.config import CameraSettings, PrecisionCaptureSettings
from laser_aligner.errors import CameraError


class FakeCamera:
    def __init__(self) -> None:
        self.settings = CameraSettings(width=64, height=48, fps=15)
        self.started = False
        self.sequence = 10
        self.generation = 3
        self.frame = np.zeros((48, 64, 3), dtype=np.uint8)
        cv2.circle(self.frame, (32, 24), 10, (255, 255, 255), -1)
        self.controls = ControlResult({}, {}, {})
        self.snapshot_after_requests: list[int] = []

    def _current_generation(self) -> int:
        return self.generation

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False
        self.generation += 1

    def restart(self) -> None:
        self.started = True
        self.generation += 1

    def status(self) -> CameraStatus:
        return CameraStatus(
            connected=self.started,
            device="/dev/video-test",
            width=64 if self.started else 0,
            height=48 if self.started else 0,
            fps=15.0,
            frames_read=self.sequence,
            last_error=None,
            frame_age_seconds=0.01,
            negotiated_fps=30.0,
        )

    def frame_sequence(self) -> int:
        return self.sequence

    def snapshot(self) -> np.ndarray:
        return self.frame.copy()

    def snapshot_after(self, sequence: int, timeout: float = 6.0) -> np.ndarray:
        del timeout
        if not self.started:
            raise CameraError("Camera is not connected")
        self.snapshot_after_requests.append(sequence)
        self.sequence = max(self.sequence + 5, sequence + 1)
        return self.frame.copy()

    def jpeg(self, quality: int = 90) -> bytes:
        ok, encoded = cv2.imencode(
            ".jpg",
            self.frame,
            [cv2.IMWRITE_JPEG_QUALITY, quality],
        )
        assert ok
        return encoded.tobytes()

    def apply_controls(
        self,
        controls: Mapping[str, int | bool],
        timeout_seconds: float = 5.0,
    ) -> ControlResult:
        del timeout_seconds
        return ControlResult(
            dict(controls),
            dict(controls),
            {},
            verified={key: int(value) for key, value in controls.items()},
        )

    def apply_controls_and_snapshot(
        self,
        controls: Mapping[str, int | bool],
        settle_seconds: float = 0.35,
        timeout_seconds: float = 2.0,
    ) -> tuple[ControlResult, np.ndarray]:
        del settle_seconds
        return self.apply_controls(controls, timeout_seconds), self.frame.copy()

    def capture_burst(
        self,
        profile: PrecisionCaptureSettings,
        reapply_controls: bool = True,
        score_frames: bool = False,
    ) -> FrameBurst:
        del reapply_controls, score_frames
        frames: list[np.ndarray] = []
        sequences: list[int] = []
        for _ in range(profile.sample_frames):
            self.sequence += 1
            frames.append(self.frame.copy())
            sequences.append(self.sequence)
        return FrameBurst(
            frames=tuple(frames),
            sequence_numbers=tuple(sequences),
            discarded_frames=profile.discard_frames,
            settle_seconds=profile.settle_seconds,
            elapsed_seconds=0.1,
            sharpness_scores=(),
            controls=self.controls,
            timeout_seconds=profile.timeout_seconds,
            observed_fps=15.0,
            negotiated_fps=15.0,
            camera_generation=self.generation,
        )


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def test_remote_camera_round_trip(monkeypatch) -> None:
    token = "camera-bridge-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    settings = CameraSettings(
        device=f"e3camera://127.0.0.1:{port}",
        width=64,
        height=48,
        fps=15,
    )
    remote = RemoteCameraService(settings)
    try:
        remote.start()
        status = remote.status()
        assert status.connected and status.device == "/dev/video-test"
        frame = remote.snapshot()
        assert frame.shape == (48, 64, 3)
        profile = PrecisionCaptureSettings(
            sample_frames=3,
            discard_frames=1,
            minimum_valid_frames=1,
            consensus_frames=1,
        )
        burst = remote.capture_burst(profile)
        assert len(burst.frames) == 3
        assert len(burst.sharpness_scores) == 3
        remote.ensure_burst_current(burst)
        result = remote.apply_controls({"focus_absolute": 40})
        assert result.verified["focus_absolute"] == 40
    finally:
        remote.stop()
        server.stop()
        thread.join(timeout=1)


def test_remote_camera_rejects_mismatched_capture_profile(monkeypatch) -> None:
    token = "camera-bridge-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    assert server._listener is not None
    settings = CameraSettings(
        device=f"e3camera://127.0.0.1:{port}",
        width=1920,
        height=1080,
        fps=15,
    )
    remote = RemoteCameraService(settings)
    try:
        from laser_aligner.errors import CameraError

        with pytest.raises(CameraError, match="does not match"):
            remote.start()
    finally:
        remote.stop()
        server.stop()
        thread.join(timeout=1)


def test_persistent_raw_monitor_authenticates_frames_and_drops_stale_sequences(
    monkeypatch,
) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    stop = threading.Event()
    try:
        stream = remote.monitor_frames(fps=15, stop_event=stop)
        frames = [next(stream) for _ in range(3)]
        assert all(item["image"].shape == (1080, 1920, 3) for item in frames)
        sequences = [item["sequence"] for item in frames]
        assert sequences == sorted(sequences)
        assert all(
            b - a >= 5 for a, b in zip(sequences, sequences[1:], strict=False)
        )
        assert len(camera.snapshot_after_requests) == 3
        # The persistent stream authenticates once rather than creating one
        # request connection per delivered frame.
        assert server._slots._value == 7
        stop.set()
        stream.close()
        deadline = time.time() + 1
        while server._monitor_slots._value != 2 and time.time() < deadline:
            time.sleep(0.01)
        assert server._monitor_slots._value == 2
        assert server._slots._value == 8
    finally:
        stop.set()
        server.stop()
        thread.join(timeout=1)


def test_raw_monitor_enforces_its_stricter_per_frame_bound(monkeypatch) -> None:
    monkeypatch.setattr(
        camera_bridge,
        "_encode_frame",
        lambda _frame, _quality: b"x" * (4 * 1024 * 1024 + 1),
    )
    with pytest.raises(CameraError, match="bounded frame limit"):
        camera_bridge._encode_monitor_frame(np.zeros((1, 1, 3), dtype=np.uint8), 78)


def test_direct_monitor_forwards_source_bytes_without_resize_or_encode(monkeypatch) -> None:
    camera = FakeCamera()
    camera.settings.width = 1920
    camera.settings.height = 1080
    source_jpeg = b"\xff\xd8exact-camera-packet\xff\xd9"

    def direct(sequence: int, *, timeout: float) -> CompressedCameraFrame:
        del sequence, timeout
        return CompressedCameraFrame(
            jpeg=source_jpeg,
            frame=camera.frame,
            sequence=44,
            generation=camera.generation,
            captured_monotonic=time.monotonic(),
            width=1920,
            height=1080,
        )

    camera.direct_mjpeg_after = direct  # type: ignore[attr-defined]
    monkeypatch.setattr(camera_bridge.cv2, "resize", lambda *_a, **_k: pytest.fail("resize called"))
    monkeypatch.setattr(camera_bridge, "_encode_monitor_frame", lambda *_a, **_k: pytest.fail("encode called"))

    metadata, jpeg = camera_bridge._monitor_frame(
        camera, sequence=10, width=1920, height=1080, quality=78, timeout=1.0
    )

    assert jpeg is source_jpeg
    assert metadata["source_mode"] == "direct_mjpeg"
    assert metadata["sequence"] == 44
    assert metadata["jpeg_bytes"] == len(source_jpeg)
    assert metadata["capture_fps"] == 15.0
    assert metadata["negotiated_fps"] == 30.0


def test_non_native_monitor_resolution_uses_transcoded_fallback() -> None:
    camera = FakeCamera()
    camera.settings.width = 1920
    camera.settings.height = 1080
    camera.started = True
    direct_calls: list[int] = []

    def direct(sequence: int, *, timeout: float) -> CompressedCameraFrame:
        del timeout
        direct_calls.append(sequence)
        raise AssertionError("non-native request must not try direct passthrough")

    camera.direct_mjpeg_after = direct  # type: ignore[attr-defined]
    metadata, jpeg = camera_bridge._monitor_frame(
        camera, sequence=10, width=1280, height=720, quality=78, timeout=1.0
    )

    assert direct_calls == []
    assert metadata["source_mode"] == "transcoded"
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")


def test_native_monitor_request_uses_720p_transcoded_fallback() -> None:
    camera = FakeCamera()
    camera.settings.width = 1920
    camera.settings.height = 1080
    camera.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    camera.started = True

    metadata, jpeg = camera_bridge._monitor_frame(
        camera, sequence=10, width=1920, height=1080, quality=78, timeout=1.0
    )

    assert metadata["source_mode"] == "transcoded"
    assert (metadata["width"], metadata["height"]) == (1280, 720)
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")


def test_remote_monitor_timestamps_receipt_before_jpeg_decode(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    decode_started = False
    original_decode = camera_remote._decode_frame

    def decode_after_receipt(jpeg: bytes) -> np.ndarray:
        nonlocal decode_started
        decode_started = True
        return original_decode(jpeg)

    def monotonic() -> float:
        return 20.0 if decode_started else 10.0

    monkeypatch.setattr(camera_remote, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(camera_remote, "_decode_frame", decode_after_receipt)
    stop = threading.Event()
    stream = remote.monitor_frames(fps=10, stop_event=stop)
    try:
        payload = next(stream)
        assert payload["received_monotonic"] == 10.0
        assert payload["capture_fps"] == 15.0
        assert payload["negotiated_fps"] == 30.0
    finally:
        stop.set()
        stream.close()
        server.stop()
        thread.join(timeout=1)


def test_encoded_monitor_path_does_not_eagerly_decode_with_opencv(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    monkeypatch.setattr(
        camera_remote,
        "_decode_frame",
        lambda _jpeg: pytest.fail("encoded monitor eagerly decoded with OpenCV"),
    )
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    stop = threading.Event()
    stream = remote.monitor_jpeg_frames(fps=15, stop_event=stop)
    try:
        payload = next(stream)
        assert payload["jpeg"].startswith(b"\xff\xd8")
        assert (payload["width"], payload["height"]) == (1920, 1080)
        assert payload["jpeg_bytes"] == len(payload["jpeg"])
        assert payload["received_monotonic"] is not None
    finally:
        stop.set()
        stream.close()
        server.stop()
        thread.join(timeout=1)


def test_encoded_monitor_payload_rejects_bounds_and_dimension_mismatch() -> None:
    with pytest.raises(CameraError, match="bounded"):
        camera_remote._validated_monitor_payload(
            {"width": 1920, "height": 1080},
            [b"x" * (4 * 1024 * 1024 + 1)],
            requested_width=1920,
            requested_height=1080,
            requested_fps=15,
            received_monotonic=1.0,
        )

    ok, encoded = cv2.imencode(
        ".jpg", np.zeros((720, 1280, 3), dtype=np.uint8)
    )
    assert ok
    with pytest.raises(CameraError, match="JPEG dimensions"):
        camera_remote._validated_monitor_payload(
            {"width": 1920, "height": 1080, "source_mode": "direct_mjpeg"},
            [encoded.tobytes()],
            requested_width=1920,
            requested_height=1080,
            requested_fps=15,
            received_monotonic=1.0,
        )


def test_encoded_monitor_accepts_reported_720p_transcoded_fallback() -> None:
    ok, encoded = cv2.imencode(
        ".jpg", np.zeros((720, 1280, 3), dtype=np.uint8)
    )
    assert ok
    payload = camera_remote._validated_monitor_payload(
        {"width": 1280, "height": 720, "source_mode": "transcoded"},
        [encoded.tobytes()],
        requested_width=1920,
        requested_height=1080,
        requested_fps=15,
        received_monotonic=1.0,
    )

    assert (payload["width"], payload["height"]) == (1280, 720)
    assert payload["source_mode"] == "transcoded"


def test_raw_monitor_rejects_bad_authentication_and_invalid_profiles(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    try:
        monkeypatch.setenv("E3_BRIDGE_TOKEN", "wrong-monitor-token-still-long-enough")
        with pytest.raises(Exception, match="rejected|closed"):
            next(remote.monitor_frames())
        monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
        with pytest.raises(Exception, match="FPS"):
            next(remote.monitor_frames(fps=12))
        with pytest.raises(Exception, match="resolution"):
            next(remote.monitor_frames(width=640, height=480))
    finally:
        server.stop()
        thread.join(timeout=1)


def test_precision_capture_remains_authoritative_during_raw_monitor(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    stop = threading.Event()
    stream = remote.monitor_frames(fps=10, stop_event=stop)
    try:
        assert next(stream)["image"].shape == (1080, 1920, 3)
        burst = remote.capture_burst(
            PrecisionCaptureSettings(
                sample_frames=2,
                discard_frames=0,
                minimum_valid_frames=1,
                consensus_frames=1,
            )
        )
        assert len(burst.frames) == 2
        assert next(stream)["image"].shape == (1080, 1920, 3)
        assert camera.controls.requested == {}
    finally:
        stop.set()
        stream.close()
        server.stop()
        thread.join(timeout=1)


def test_raw_monitor_recovers_after_camera_stop_and_restart(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    stop = threading.Event()
    stream = remote.monitor_frames(fps=15, stop_event=stop)
    try:
        first = next(stream)
        camera.stop()
        restart = threading.Timer(0.4, camera.restart)
        restart.start()
        recovered = next(stream)
        restart.join(timeout=1)
        assert recovered["sequence"] > first["sequence"]
        assert camera.started
    finally:
        stop.set()
        stream.close()
        server.stop()
        thread.join(timeout=1)

def test_remote_status_is_cached_and_does_not_touch_network(monkeypatch) -> None:
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://127.0.0.1:65534")
    )

    monkeypatch.setattr(
        remote,
        "_request",
        lambda *_args, **_kwargs: pytest.fail(
            "status() attempted synchronous network I/O"
        ),
    )

    status = remote.status()

    assert not status.connected
    assert status.device == "e3camera://127.0.0.1:65534"
    assert status.frames_read == 0


def test_remote_status_probe_uses_bounded_exponential_backoff() -> None:
    assert [_status_probe_delay(value) for value in range(1, 8)] == [
        2.0,
        4.0,
        8.0,
        16.0,
        30.0,
        30.0,
        30.0,
    ]
