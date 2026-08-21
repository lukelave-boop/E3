from __future__ import annotations

import glob
import logging
import math
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import CameraSettings, PrecisionCaptureSettings
from ..errors import CameraError
from .controls import ControlResult, validate_control_request
from .controls import apply_controls as apply_v4l2_controls

LOGGER = logging.getLogger(__name__)
_OPERATION_WAIT_SECONDS = 0.75
_RESTART_WAIT_SECONDS = 2.0
_READER_JOIN_SECONDS = 2.0
_MAX_DIRECT_MJPEG_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CompressedCameraFrame:
    jpeg: bytes
    frame: np.ndarray
    sequence: int
    generation: int
    captured_monotonic: float
    width: int
    height: int


def list_video_devices() -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    by_id = {str(Path(path).resolve()): path for path in glob.glob("/dev/v4l/by-id/*")}
    for path in sorted(glob.glob("/dev/video*")):
        resolved = str(Path(path).resolve())
        devices.append({"path": path, "by_id": by_id.get(resolved, "")})
    return devices


@dataclass(slots=True)
class CameraStatus:
    connected: bool
    device: str
    width: int
    height: int
    fps: float
    frames_read: int
    last_error: str | None
    frame_age_seconds: float | None = None
    controls_verified: dict[str, int] = field(default_factory=dict)
    controls_satisfied: dict[str, str] = field(default_factory=dict)
    controls_critical_unverified: dict[str, str] = field(default_factory=dict)
    negotiated_fps: float = 0.0
    operation: str | None = None
    monitor_source_mode: str = "transcoded"


@dataclass(slots=True)
class FrameBurst:
    frames: tuple[np.ndarray, ...]
    sequence_numbers: tuple[int, ...]
    discarded_frames: int
    settle_seconds: float
    elapsed_seconds: float
    sharpness_scores: tuple[float, ...]
    controls: ControlResult
    timeout_seconds: float | None = None
    observed_fps: float | None = None
    negotiated_fps: float | None = None
    sequence_gaps: int = 0
    camera_generation: int | None = None

    @property
    def sharpest_index(self) -> int:
        if not self.sharpness_scores:
            raise CameraError("Precision capture contains no frames")
        return max(range(len(self.sharpness_scores)), key=self.sharpness_scores.__getitem__)

    @property
    def sharpest_frame(self) -> np.ndarray:
        return self.frames[self.sharpest_index].copy()

    def diagnostics(self) -> dict[str, object]:
        return {
            "sample_frames": len(self.frames),
            "discarded_frames": int(self.discarded_frames),
            "settle_seconds": float(self.settle_seconds),
            "elapsed_seconds": float(self.elapsed_seconds),
            "timeout_seconds": (None if self.timeout_seconds is None else float(self.timeout_seconds)),
            "observed_fps": (None if self.observed_fps is None else float(self.observed_fps)),
            "negotiated_fps": (None if self.negotiated_fps is None else float(self.negotiated_fps)),
            "sequence_gaps": int(self.sequence_gaps),
            "camera_generation": self.camera_generation,
            "sequence_start": (int(self.sequence_numbers[0]) if self.sequence_numbers else None),
            "sequence_end": (int(self.sequence_numbers[-1]) if self.sequence_numbers else None),
            "sharpest_index": (int(self.sharpest_index) if self.sharpness_scores else None),
            "analysis_complete": bool(self.sharpness_scores),
            "sharpness_scores": [float(value) for value in self.sharpness_scores],
            "controls_applied": dict(self.controls.applied),
            "controls_skipped": dict(self.controls.skipped),
            "controls_verified": dict(self.controls.verified),
            "controls_satisfied": dict(self.controls.satisfied),
            "controls_critical_unverified": dict(self.controls.critical_unverified),
        }


class CameraService:
    """Background capture with one owner for disruptive camera operations.

    The reader is the only code that calls ``VideoCapture.read``. Precision
    bursts, control changes, and lifecycle transitions are serialized by the
    operation lock. Ordinary snapshots only retain the immutable published
    frame reference while holding the state lock, then copy pixels after the
    lock is released so preview traffic cannot stall the reader at 1080p.
    """

    def __init__(self, settings: CameraSettings):
        self.settings = settings
        self._capture: Any | None = None
        self._frame: np.ndarray | None = None
        self._frame_time = 0.0
        self._frame_monotonic = 0.0
        self._frames_read = 0
        self._last_error: str | None = None
        self._lock = threading.RLock()
        self._frame_condition = threading.Condition(self._lock)
        self._operation_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._teardown_complete = threading.Event()
        self._teardown_complete.set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._actual_fps = 0.0
        self._negotiated_fps = 0.0
        self._control_result = ControlResult({}, {}, {})
        self._generation = 0
        self._operation: str | None = None
        self._direct_mjpeg = False
        self._compressed_frame: CompressedCameraFrame | None = None

    def _current_generation(self) -> int:
        with self._lock:
            return self._generation

    def _is_cancelled(
        self,
        generation: int,
        stop_event: threading.Event | None = None,
    ) -> bool:
        with self._lock:
            changed = generation != self._generation
        return changed or (stop_event is not None and stop_event.is_set())

    @contextmanager
    def _exclusive_operation(
        self,
        label: str,
        *,
        request_generation: int,
        wait_seconds: float = _OPERATION_WAIT_SECONDS,
    ) -> Iterator[None]:
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        acquired = False
        while not acquired:
            if self._is_cancelled(request_generation):
                raise CameraError(f"{label.capitalize()} was cancelled before it began")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                with self._lock:
                    owner = self._operation or "another camera operation"
                raise CameraError(f"Camera is busy with {owner}; {label} did not start")
            acquired = self._operation_lock.acquire(timeout=min(0.05, remaining))
        try:
            if self._is_cancelled(request_generation):
                raise CameraError(f"{label.capitalize()} was cancelled before it began")
            with self._frame_condition:
                self._operation = label
                self._frame_condition.notify_all()
            yield
        finally:
            with self._frame_condition:
                if self._operation == label:
                    self._operation = None
                self._frame_condition.notify_all()
            self._operation_lock.release()

    def start(self) -> None:
        with self._lock:
            if self._connected and self._thread is not None and self._thread.is_alive():
                return
            generation = self._generation
        with self._exclusive_operation(
            "camera start",
            request_generation=generation,
            wait_seconds=_RESTART_WAIT_SECONDS,
        ):
            if not self._teardown_complete.wait(timeout=_RESTART_WAIT_SECONDS):
                raise CameraError("Previous camera shutdown did not finish in time")
            with self._lock:
                if self._connected and self._thread is not None and self._thread.is_alive():
                    return
            self._start_owned(generation)

    def _start_owned(self, generation: int) -> None:
        session_stop = threading.Event()
        capture: Any | None = None
        try:
            (
                capture,
                frame,
                compressed,
                warmup_count,
                controls,
                negotiated_fps,
                direct_mjpeg,
            ) = self._open_session(
                generation,
                session_stop,
            )
            if self._is_cancelled(generation, session_stop):
                raise CameraError("Camera start was cancelled")
            now_wall = time.time()
            now_monotonic = time.monotonic()
            thread = threading.Thread(
                target=self._reader_loop,
                args=(capture, session_stop, generation),
                name="camera-reader",
                daemon=True,
            )
            with self._frame_condition:
                if generation != self._generation:
                    raise CameraError("Camera start was cancelled")
                self._stop = session_stop
                self._capture = capture
                self._thread = thread
                self._frame = frame
                self._frame_time = now_wall if frame is not None else 0.0
                self._frame_monotonic = now_monotonic if frame is not None else 0.0
                self._frames_read += warmup_count
                if compressed is not None and frame is not None:
                    self._compressed_frame = CompressedCameraFrame(
                        jpeg=compressed,
                        frame=frame,
                        sequence=self._frames_read,
                        generation=generation,
                        captured_monotonic=now_monotonic,
                        width=int(frame.shape[1]),
                        height=int(frame.shape[0]),
                    )
                else:
                    self._compressed_frame = None
                self._direct_mjpeg = direct_mjpeg
                self._connected = True
                self._actual_fps = 0.0
                self._negotiated_fps = negotiated_fps
                self._control_result = controls
                self._last_error = None
                self._frame_condition.notify_all()
            thread.start()
            if self._is_cancelled(generation, session_stop):
                raise CameraError("Camera start was cancelled")
        except Exception as exc:
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    LOGGER.exception("Could not release camera after failed start")
            with self._frame_condition:
                if generation == self._generation:
                    self._capture = None
                    self._thread = None
                    self._connected = False
                    self._frame = None
                    self._compressed_frame = None
                    self._direct_mjpeg = False
                    self._frame_time = 0.0
                    self._frame_monotonic = 0.0
                    self._last_error = str(exc)
                    self._frame_condition.notify_all()
            raise

    def _open_session(
        self,
        generation: int,
        stop_event: threading.Event,
    ) -> tuple[
        Any,
        np.ndarray | None,
        bytes | None,
        int,
        ControlResult,
        float,
        bool,
    ]:
        native = self._open_native_session(generation, stop_event)
        if native is not None:
            return native

        source: str | int = self.settings.device
        if self.settings.device.isdigit():
            source = int(self.settings.device)
        capture: cv2.VideoCapture | None = None
        if sys.platform.startswith("linux"):
            capture = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if capture is None or not capture.isOpened():
            if capture is not None:
                capture.release()
            capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            capture.release()
            raise CameraError(f"Could not open camera {self.settings.device}")
        try:
            initialized = self._initialize_open_capture(capture, generation, stop_event)
            return (capture, *initialized)
        except Exception:
            capture.release()
            raise

    def _open_native_session(
        self,
        generation: int,
        stop_event: threading.Event,
    ) -> tuple[Any, np.ndarray | None, bytes | None, int, ControlResult, float, bool] | None:
        if (
            not sys.platform.startswith("linux")
            or self.settings.fourcc.upper() not in {"MJPG", "MJPEG"}
            or self.settings.device.isdigit()
            or not self.settings.device.startswith("/dev/v4l/by-id/")
        ):
            return None
        from .v4l2_mjpeg import NativeV4L2MjpegCapture

        capture: NativeV4L2MjpegCapture | None = None
        try:
            capture = NativeV4L2MjpegCapture(
                self.settings.device,
                self.settings.width,
                self.settings.height,
                self.settings.fps,
            )
            controls = self._apply_controls_to_device(
                self.settings.controls,
                timeout_seconds=5.0,
                cancelled=lambda: self._is_cancelled(generation, stop_event),
            )
            self._log_control_result(controls)
            if self._is_cancelled(generation, stop_event):
                raise CameraError("Camera start was cancelled")
            frame: np.ndarray | None = None
            compressed: bytes | None = None
            warmup_count = 0
            for _ in range(max(1, self.settings.warmup_frames)):
                if self._is_cancelled(generation, stop_event):
                    raise CameraError("Camera start was cancelled during warmup")
                ok, candidate = capture.read()
                if not ok:
                    continue
                decoded = self._decode_direct_mjpeg(candidate)
                if decoded is None:
                    raise CameraError("Native V4L2 returned invalid MJPEG data")
                compressed, frame = decoded
                warmup_count += 1
            if frame is None or compressed is None:
                raise CameraError("Native V4L2 produced no valid MJPEG warmup frame")
            return (
                capture,
                frame,
                compressed,
                warmup_count,
                controls,
                capture.negotiated_fps,
                True,
            )
        except Exception as exc:
            if capture is not None:
                capture.release()
            if self._is_cancelled(generation, stop_event):
                raise CameraError("Camera start was cancelled") from exc
            LOGGER.warning(
                "Native V4L2 MJPEG unavailable for %s; using decoded OpenCV fallback: %s",
                self.settings.device,
                exc,
            )
            return None

    def _initialize_open_capture(
        self,
        capture: cv2.VideoCapture,
        generation: int,
        stop_event: threading.Event,
    ) -> tuple[
        np.ndarray | None,
        bytes | None,
        int,
        ControlResult,
        float,
        bool,
    ]:
        if len(self.settings.fourcc) == 4:
            capture.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*self.settings.fourcc),
            )
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        capture.set(cv2.CAP_PROP_FPS, self.settings.fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        read_timeout_property = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
        if read_timeout_property is not None:
            capture.set(read_timeout_property, 2000)

        controls = self._apply_controls_to_device(
            self.settings.controls,
            timeout_seconds=5.0,
            cancelled=lambda: self._is_cancelled(generation, stop_event),
        )
        self._log_control_result(controls)
        if self._is_cancelled(generation, stop_event):
            raise CameraError("Camera start was cancelled")

        frame: np.ndarray | None = None
        warmup_count = 0
        for _ in range(max(0, self.settings.warmup_frames)):
            if self._is_cancelled(generation, stop_event):
                raise CameraError("Camera start was cancelled during warmup")
            try:
                ok, candidate = capture.read()
            except Exception as exc:
                raise CameraError(f"Camera warmup read failed: {exc}") from exc
            if ok:
                if self._valid_frame(candidate):
                    frame = candidate
                else:
                    continue
                warmup_count += 1

        try:
            negotiated_fps = float(capture.get(cv2.CAP_PROP_FPS))
        except Exception:
            negotiated_fps = 0.0
        if not np.isfinite(negotiated_fps) or negotiated_fps < 0:
            negotiated_fps = 0.0
        return frame, None, warmup_count, controls, negotiated_fps, False

    def _decode_direct_mjpeg(self, candidate: object) -> tuple[bytes, np.ndarray] | None:
        if isinstance(candidate, bytes):
            jpeg = candidate
        elif isinstance(candidate, np.ndarray) and candidate.dtype == np.uint8:
            jpeg = candidate.reshape(-1).tobytes()
        else:
            return None
        if (
            not 4 <= len(jpeg) <= _MAX_DIRECT_MJPEG_BYTES
            or not jpeg.startswith(b"\xff\xd8")
            or not jpeg.endswith(b"\xff\xd9")
        ):
            return None
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if not self._valid_frame(frame):
            return None
        assert isinstance(frame, np.ndarray)
        if frame.shape[:2] != (self.settings.height, self.settings.width):
            return None
        return jpeg, frame

    @staticmethod
    def _log_control_result(result: ControlResult) -> None:
        if result.applied:
            LOGGER.info("Applied camera controls: %s", result.applied)
        if result.skipped:
            LOGGER.info("Skipped camera controls: %s", result.skipped)

    @staticmethod
    def _valid_frame(frame: object) -> bool:
        return bool(
            isinstance(frame, np.ndarray)
            and frame.size > 0
            and frame.dtype == np.uint8
            and frame.ndim in (2, 3)
            and (frame.ndim == 2 or frame.shape[2] == 3)
        )

    def _reader_loop(
        self,
        capture: Any,
        stop_event: threading.Event,
        generation: int,
    ) -> None:
        previous = time.monotonic()
        smoothed_fps = 0.0
        while not stop_event.is_set():
            try:
                ok, frame = capture.read()
            except Exception as exc:
                ok, frame = False, None
                error = f"Camera read failed: {exc}"
            else:
                error = "Camera read failed"
            if stop_event.is_set():
                break
            compressed: bytes | None = None
            if ok and self._direct_mjpeg:
                decoded = self._decode_direct_mjpeg(frame)
                if decoded is None:
                    ok = False
                    error = "Camera returned invalid direct MJPEG data"
                else:
                    compressed, frame = decoded
            if not ok or not self._valid_frame(frame):
                if ok and frame is not None:
                    error = "Camera returned a malformed frame"
                with self._frame_condition:
                    if generation != self._generation or capture is not self._capture:
                        break
                    self._last_error = error
                    self._frame_condition.notify_all()
                stop_event.wait(0.05)
                continue
            now = time.monotonic()
            delta = max(now - previous, 1e-6)
            instant_fps = 1.0 / delta
            smoothed_fps = instant_fps if smoothed_fps == 0 else 0.9 * smoothed_fps + 0.1 * instant_fps
            previous = now
            with self._frame_condition:
                if generation != self._generation or capture is not self._capture:
                    break
                self._frame = frame
                self._frame_time = time.time()
                self._frame_monotonic = now
                self._frames_read += 1
                if compressed is not None:
                    self._compressed_frame = CompressedCameraFrame(
                        jpeg=compressed,
                        frame=frame,
                        sequence=self._frames_read,
                        generation=generation,
                        captured_monotonic=now,
                        width=int(frame.shape[1]),
                        height=int(frame.shape[0]),
                    )
                self._actual_fps = smoothed_fps
                self._last_error = None
                self._frame_condition.notify_all()

    def stop(self) -> None:
        with self._stop_lock:
            self._teardown_complete.clear()
            with self._frame_condition:
                self._generation += 1
                stop_event = self._stop
                stop_event.set()
                capture = self._capture
                thread = self._thread
                self._capture = None
                self._thread = None
                self._connected = False
                self._frame = None
                self._compressed_frame = None
                self._direct_mjpeg = False
                self._frame_time = 0.0
                self._frame_monotonic = 0.0
                self._actual_fps = 0.0
                self._negotiated_fps = 0.0
                self._frame_condition.notify_all()
            try:
                if capture is not None:
                    try:
                        capture.release()
                    except Exception:
                        LOGGER.exception("Could not release camera during shutdown")
                if thread is not None and thread is not threading.current_thread() and thread.is_alive():
                    thread.join(timeout=_READER_JOIN_SECONDS)
                    if thread.is_alive():
                        with self._frame_condition:
                            self._last_error = "Camera reader did not stop within 2 seconds"
                            self._frame_condition.notify_all()
            finally:
                self._teardown_complete.set()

    def restart(self) -> None:
        self.stop()
        generation = self._current_generation()
        with self._exclusive_operation(
            "camera restart",
            request_generation=generation,
            wait_seconds=_RESTART_WAIT_SECONDS,
        ):
            if not self._teardown_complete.wait(timeout=_RESTART_WAIT_SECONDS):
                raise CameraError("Previous camera shutdown did not finish in time")
            with self._lock:
                if self._connected and self._thread is not None and self._thread.is_alive():
                    return
            self._start_owned(generation)

    def _published_frame(self) -> tuple[np.ndarray, float, str | None, int]:
        with self._lock:
            if not self._connected:
                raise CameraError("Camera is not connected")
            frame = self._frame
            if frame is None:
                raise CameraError("No camera frame is available")
            monotonic_timestamp = self._frame_monotonic
            last_error = self._last_error
            generation = self._generation
        return frame, monotonic_timestamp, last_error, generation

    def snapshot(self) -> np.ndarray:
        frame, monotonic_timestamp, last_error, generation = self._published_frame()
        age = time.monotonic() - monotonic_timestamp
        maximum_age = max(2.0, 5.0 / max(1.0, float(self.settings.fps)))
        if monotonic_timestamp <= 0 or age > maximum_age:
            detail = f" ({last_error})" if last_error else ""
            raise CameraError(f"Latest camera frame is stale ({age:.2f} s old){detail}")
        result = frame.copy()
        if self._is_cancelled(generation):
            raise CameraError("Camera stopped while copying the latest frame")
        return result

    def frame_sequence(self) -> int:
        """Return the monotonically increasing count of captured live frames."""
        with self._lock:
            return self._frames_read

    def direct_mjpeg_after(self, sequence: int, *, timeout: float = 6.0) -> CompressedCameraFrame:
        """Return the newest validated source JPEG without copying or encoding it."""
        deadline = time.monotonic() + float(timeout)
        with self._frame_condition:
            generation = self._generation
            while True:
                compressed = self._compressed_frame
                if not self._direct_mjpeg:
                    raise CameraError("Native direct MJPEG capture is unavailable")
                if compressed is not None and compressed.sequence > sequence and compressed.generation == generation:
                    return compressed
                if generation != self._generation or not self._connected:
                    raise CameraError("Camera stopped while waiting for direct MJPEG")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CameraError("Timed out waiting for a fresh direct MJPEG frame")
                self._frame_condition.wait(timeout=remaining)

    def ensure_burst_current(self, burst: FrameBurst) -> None:
        """Reject deferred work after the source was stopped or reopened."""
        generation = burst.camera_generation
        if generation is not None and self._is_cancelled(generation):
            raise CameraError("Camera stopped or restarted after this frame burst")

    def snapshot_after(self, sequence: int, timeout: float = 6.0) -> np.ndarray:
        """Wait for a new frame without accepting a frame from another session."""
        if type(sequence) is not int or sequence < 0:
            raise CameraError("Fresh-frame sequence must be a non-negative integer")
        try:
            timeout_seconds = float(timeout)
        except (TypeError, ValueError) as exc:
            raise CameraError("Fresh-frame timeout must be a positive finite number") from exc
        if type(timeout) is bool or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise CameraError("Fresh-frame timeout must be a positive finite number")
        deadline = time.monotonic() + timeout_seconds
        with self._frame_condition:
            generation = self._generation
            while self._frames_read <= int(sequence):
                if generation != self._generation:
                    raise CameraError("Camera restarted while waiting for a fresh frame")
                if not self._connected:
                    raise CameraError("Camera disconnected while waiting for a fresh frame")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = f" ({self._last_error})" if self._last_error else ""
                    raise CameraError(
                        f"Camera did not provide a fresh frame within {timeout_seconds:g} seconds{detail}"
                    )
                self._frame_condition.wait(timeout=remaining)
            frame = self._frame
            if frame is None:
                raise CameraError("No camera frame is available")
        result = frame.copy()
        if self._is_cancelled(generation):
            raise CameraError("Camera stopped while copying a fresh frame")
        return result

    @staticmethod
    def _sharpness_score(image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _wait_for_new_frame(
        self,
        after_sequence: int,
        deadline: float,
        generation: int,
        stop_event: threading.Event,
    ) -> tuple[np.ndarray, int, float]:
        with self._frame_condition:
            while self._frames_read <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    detail = f" ({self._last_error})" if self._last_error else ""
                    raise CameraError(f"Timed out waiting for fresh camera frames during precision capture{detail}")
                if stop_event.is_set() or generation != self._generation or not self._connected:
                    raise CameraError("Camera stopped during precision capture")
                self._frame_condition.wait(timeout=remaining)
            if stop_event.is_set() or generation != self._generation:
                raise CameraError("Camera stopped during precision capture")
            frame = self._frame
            sequence = int(self._frames_read)
            frame_monotonic = float(self._frame_monotonic)
            if frame is None:
                raise CameraError("No camera frame is available")
        result = frame.copy()
        if self._is_cancelled(generation, stop_event):
            raise CameraError("Camera stopped during precision capture")
        return result, sequence, frame_monotonic

    def _wait_settle(
        self,
        seconds: float,
        deadline: float,
        generation: int,
        stop_event: threading.Event,
    ) -> None:
        remaining_budget = deadline - time.monotonic()
        if remaining_budget <= 0:
            raise CameraError("Precision-capture deadline expired before settling")
        wait_seconds = min(max(0.0, float(seconds)), remaining_budget)
        if wait_seconds > 0 and stop_event.wait(wait_seconds):
            raise CameraError("Camera stopped during precision-capture settling")
        if self._is_cancelled(generation, stop_event):
            raise CameraError("Camera stopped during precision-capture settling")
        if wait_seconds < float(seconds) or time.monotonic() >= deadline:
            raise CameraError("Precision-capture deadline expired during settling")

    @staticmethod
    def _validate_capture_profile(profile: PrecisionCaptureSettings) -> None:
        if type(profile.sample_frames) is not int or profile.sample_frames <= 0:
            raise CameraError("Precision capture requires at least one sample frame")
        if type(profile.discard_frames) is not int or profile.discard_frames < 0:
            raise CameraError("Precision capture discard count must be a non-negative integer")
        if (
            type(profile.minimum_valid_frames) is not int
            or not 1 <= profile.minimum_valid_frames <= profile.sample_frames
        ):
            raise CameraError("Precision capture minimum-valid count is outside the sample count")
        if (
            type(profile.consensus_frames) is not int
            or profile.consensus_frames < 1
            or (
                profile.coordinate_strategy == "stable_clarity_consensus"
                and profile.consensus_frames > profile.sample_frames
            )
        ):
            raise CameraError("Precision capture consensus count is outside the sample count")
        numeric_values = (
            ("settle time", profile.settle_seconds, True),
            ("timeout", profile.timeout_seconds, False),
            ("MAD multiplier", profile.mad_multiplier, False),
            ("outlier floor", profile.outlier_floor_px, True),
            ("jitter limit", profile.max_jitter_rms_px, False),
        )
        for label, raw_value, allow_zero in numeric_values:
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise CameraError(f"Precision capture {label} must be finite") from exc
            if type(raw_value) is bool or not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
                qualifier = "non-negative" if allow_zero else "positive"
                raise CameraError(f"Precision capture {label} must be finite and {qualifier}")
        if profile.coordinate_strategy not in {
            "median",
            "sharpest_inlier_frame",
            "stable_clarity_consensus",
        }:
            raise CameraError("Precision capture coordinate strategy is unsupported")

    def capture_burst(
        self,
        settings: PrecisionCaptureSettings | None = None,
        *,
        reapply_controls: bool = True,
        score_frames: bool = True,
    ) -> FrameBurst:
        """Capture one serialized sequence of distinct post-settle frames.

        ``timeout_seconds`` bounds control reapplication, settling, discards,
        and acquisition after this caller gains ownership. Waiting for another
        owner has its own short bound and fails with a camera-busy error.
        """

        profile = settings or self.settings.precision_capture
        self._validate_capture_profile(profile)
        request_generation = self._current_generation()
        frames: list[np.ndarray] = []
        sequences: list[int] = []
        frame_times: list[float] = []
        with self._exclusive_operation(
            "precision capture",
            request_generation=request_generation,
        ):
            with self._lock:
                if not self._connected:
                    raise CameraError("Camera is not connected")
                generation = self._generation
                stop_event = self._stop
                negotiated_fps = self._negotiated_fps
            started = time.monotonic()
            deadline = started + float(profile.timeout_seconds)
            controls = (
                self._apply_controls_owned(
                    self.settings.controls,
                    generation,
                    stop_event,
                    timeout_seconds=max(0.0, deadline - time.monotonic()),
                )
                if reapply_controls
                else ControlResult({}, {}, {})
            )
            self._wait_settle(
                profile.settle_seconds,
                deadline,
                generation,
                stop_event,
            )
            with self._lock:
                sequence = int(self._frames_read)

            for discard_index in range(profile.discard_frames):
                try:
                    _, sequence, _ = self._wait_for_new_frame(
                        sequence,
                        deadline,
                        generation,
                        stop_event,
                    )
                except CameraError as exc:
                    if "Timed out" not in str(exc):
                        raise
                    raise CameraError(
                        "Timed out waiting for fresh camera frames during precision "
                        f"capture discard {discard_index + 1}/{profile.discard_frames}: {exc}"
                    ) from exc

            for sample_index in range(profile.sample_frames):
                try:
                    frame, sequence, frame_time = self._wait_for_new_frame(
                        sequence,
                        deadline,
                        generation,
                        stop_event,
                    )
                except CameraError as exc:
                    if "Timed out" not in str(exc):
                        raise
                    raise CameraError(
                        "Timed out waiting for fresh camera frames during precision "
                        f"capture sample {sample_index + 1}/{profile.sample_frames}: {exc}"
                    ) from exc
                frames.append(frame)
                sequences.append(sequence)
                frame_times.append(frame_time)
            if self._is_cancelled(generation, stop_event):
                raise CameraError("Camera stopped during precision capture")
            elapsed_seconds = time.monotonic() - started

        sharpness_values: list[float] = []
        if score_frames:
            for frame in frames:
                sharpness_values.append(self._sharpness_score(frame))
                if self._is_cancelled(generation, stop_event):
                    raise CameraError("Camera stopped during precision-capture analysis")
        sharpness = tuple(sharpness_values)
        observed_fps: float | None = None
        if len(frame_times) >= 2 and frame_times[-1] > frame_times[0]:
            observed_fps = (sequences[-1] - sequences[0]) / (frame_times[-1] - frame_times[0])
        elif frames:
            with self._lock:
                observed_fps = self._actual_fps or negotiated_fps or None
        sequence_gaps = sum(
            max(0, current - previous - 1) for previous, current in zip(sequences, sequences[1:], strict=False)
        )
        return FrameBurst(
            frames=tuple(frames),
            sequence_numbers=tuple(sequences),
            discarded_frames=profile.discard_frames,
            settle_seconds=profile.settle_seconds,
            elapsed_seconds=elapsed_seconds,
            sharpness_scores=sharpness,
            controls=controls,
            timeout_seconds=profile.timeout_seconds,
            observed_fps=observed_fps,
            negotiated_fps=negotiated_fps or None,
            sequence_gaps=sequence_gaps,
            camera_generation=generation,
        )

    def jpeg(self, quality: int | None = None) -> bytes:
        frame = self.snapshot()
        raw_quality = self.settings.jpeg_quality if quality is None else quality
        if type(raw_quality) is not int:
            raise CameraError("JPEG quality must be an integer")
        encode_quality = raw_quality
        encode_quality = max(1, min(100, encode_quality))
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, encode_quality],
        )
        if not ok:
            raise CameraError("Could not encode camera frame")
        return encoded.tobytes()

    def mjpeg(self, target_fps: float = 10.0) -> Iterator[bytes]:
        try:
            fps = float(target_fps)
        except (TypeError, ValueError) as exc:
            raise CameraError("MJPEG target FPS must be a positive finite number") from exc
        if type(target_fps) is bool or not math.isfinite(fps) or fps <= 0.0:
            raise CameraError("MJPEG target FPS must be a positive finite number")
        delay = 1.0 / max(1.0, fps)
        generation = self._current_generation()
        while not self._is_cancelled(generation):
            try:
                jpeg = self.jpeg(quality=min(self.settings.jpeg_quality, 85))
            except CameraError:
                time.sleep(0.2)
                continue
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(jpeg)).encode()
                + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
            time.sleep(delay)

    def status(self) -> CameraStatus:
        with self._lock:
            frame = self._frame
            height, width = frame.shape[:2] if frame is not None else (0, 0)
            return CameraStatus(
                connected=self._connected,
                device=self.settings.device,
                width=width,
                height=height,
                fps=round(self._actual_fps, 1),
                frames_read=self._frames_read,
                last_error=self._last_error,
                frame_age_seconds=(
                    None
                    if self._frame is None or self._frame_monotonic <= 0
                    else max(0.0, time.monotonic() - self._frame_monotonic)
                ),
                controls_verified=dict(self._control_result.verified),
                controls_satisfied=dict(self._control_result.satisfied),
                controls_critical_unverified=dict(self._control_result.critical_unverified),
                negotiated_fps=round(self._negotiated_fps, 1),
                operation=self._operation,
                monitor_source_mode=("direct_mjpeg" if self._direct_mjpeg else "transcoded"),
            )

    def _apply_controls_to_device(
        self,
        requested: Mapping[str, int | bool],
        *,
        timeout_seconds: float,
        cancelled: Callable[[], bool],
    ) -> ControlResult:
        return apply_v4l2_controls(
            self.settings.device,
            requested,
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
        )

    def _apply_controls_owned(
        self,
        requested: Mapping[str, int | bool],
        generation: int,
        stop_event: threading.Event,
        *,
        timeout_seconds: float,
    ) -> ControlResult:
        result = self._apply_controls_to_device(
            requested,
            timeout_seconds=timeout_seconds,
            cancelled=lambda: self._is_cancelled(generation, stop_event),
        )
        if self._is_cancelled(generation, stop_event):
            raise CameraError("Camera stopped while applying controls")
        with self._lock:
            if generation != self._generation:
                raise CameraError("Camera stopped while applying controls")
            self._control_result = result
        self._log_control_result(result)
        return result

    def apply_controls(
        self,
        requested: Mapping[str, int | bool],
        *,
        timeout_seconds: float = 5.0,
    ) -> ControlResult:
        try:
            control_timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise CameraError("Camera-control timeout must be a positive finite number") from exc
        if type(timeout_seconds) is bool or not math.isfinite(control_timeout) or control_timeout <= 0:
            raise CameraError("Camera-control timeout must be a positive finite number")
        try:
            normalized = validate_control_request(requested)
        except ValueError as exc:
            raise CameraError(str(exc)) from exc
        request_generation = self._current_generation()
        with self._exclusive_operation(
            "camera control update",
            request_generation=request_generation,
        ):
            with self._lock:
                if not self._connected:
                    raise CameraError("Camera is not connected")
                generation = self._generation
                stop_event = self._stop
            result = self._apply_controls_owned(
                normalized,
                generation,
                stop_event,
                timeout_seconds=control_timeout,
            )
        if self._is_cancelled(generation, stop_event):
            raise CameraError("Camera stopped while applying controls")
        return result

    def apply_controls_and_snapshot(
        self,
        requested: Mapping[str, int | bool],
        *,
        settle_seconds: float = 0.35,
        timeout_seconds: float = 2.0,
    ) -> tuple[ControlResult, np.ndarray]:
        try:
            settle = float(settle_seconds)
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise CameraError("Camera-control settle and timeout values must be finite") from exc
        if (
            type(settle_seconds) is bool
            or type(timeout_seconds) is bool
            or not math.isfinite(settle)
            or not math.isfinite(timeout)
            or settle < 0
            or timeout <= 0
        ):
            raise CameraError("Camera-control settle and timeout values must be finite")
        try:
            normalized = validate_control_request(requested)
        except ValueError as exc:
            raise CameraError(str(exc)) from exc
        request_generation = self._current_generation()
        with self._exclusive_operation(
            "camera control update",
            request_generation=request_generation,
        ):
            with self._lock:
                if not self._connected:
                    raise CameraError("Camera is not connected")
                generation = self._generation
                stop_event = self._stop
            deadline = time.monotonic() + timeout
            result = self._apply_controls_owned(
                normalized,
                generation,
                stop_event,
                timeout_seconds=max(0.0, deadline - time.monotonic()),
            )
            with self._lock:
                sequence = self._frames_read
            self._wait_settle(
                settle,
                deadline,
                generation,
                stop_event,
            )
            frame, _, _ = self._wait_for_new_frame(
                sequence,
                deadline,
                generation,
                stop_event,
            )
        if self._is_cancelled(generation, stop_event):
            raise CameraError("Camera stopped while applying controls")
        return result, frame

    def apply_configured_controls(self) -> ControlResult:
        return self.apply_controls(self.settings.controls)
