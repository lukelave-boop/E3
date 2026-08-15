from __future__ import annotations

import socket
import threading
import time
from collections.abc import Mapping

import cv2
import numpy as np
import pytest

from laser_aligner.camera.bridge import CameraBridgeServer
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.remote import RemoteCameraService
from laser_aligner.camera.service import CameraStatus, FrameBurst
from laser_aligner.config import CameraSettings, PrecisionCaptureSettings


class FakeCamera:
    def __init__(self) -> None:
        self.settings = CameraSettings(width=64, height=48, fps=15)
        self.started = False
        self.sequence = 10
        self.generation = 3
        self.frame = np.zeros((48, 64, 3), dtype=np.uint8)
        cv2.circle(self.frame, (32, 24), 10, (255, 255, 255), -1)
        self.controls = ControlResult({}, {}, {})

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
        )

    def frame_sequence(self) -> int:
        return self.sequence

    def snapshot(self) -> np.ndarray:
        return self.frame.copy()

    def snapshot_after(self, sequence: int, timeout: float = 6.0) -> np.ndarray:
        del timeout
        self.sequence = max(self.sequence + 1, sequence + 1)
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
