from __future__ import annotations

import glob
import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import CameraSettings, PrecisionCaptureSettings, WorkArea
from ..errors import CameraError
from .controls import ControlResult, apply_controls

LOGGER = logging.getLogger(__name__)


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
    synthetic: bool = False


@dataclass(slots=True)
class FrameBurst:
    frames: tuple[np.ndarray, ...]
    sequence_numbers: tuple[int, ...]
    discarded_frames: int
    settle_seconds: float
    elapsed_seconds: float
    sharpness_scores: tuple[float, ...]
    controls: ControlResult

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
            "sequence_start": (
                int(self.sequence_numbers[0]) if self.sequence_numbers else None
            ),
            "sequence_end": (
                int(self.sequence_numbers[-1]) if self.sequence_numbers else None
            ),
            "sharpest_index": int(self.sharpest_index),
            "sharpness_scores": [float(value) for value in self.sharpness_scores],
            "controls_applied": dict(self.controls.applied),
            "controls_skipped": dict(self.controls.skipped),
            "controls_verified": dict(self.controls.verified),
        }


class CameraService:
    """Background OpenCV/V4L2 capture service."""

    def __init__(self, settings: CameraSettings):
        self.settings = settings
        self._capture: cv2.VideoCapture | None = None
        self._frame: np.ndarray | None = None
        self._frame_time = 0.0
        self._frames_read = 0
        self._last_error: str | None = None
        self._lock = threading.RLock()
        self._frame_condition = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._actual_fps = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._open()
        self._thread = threading.Thread(target=self._reader_loop, name="camera-reader", daemon=True)
        self._thread.start()

    def _open(self) -> None:
        source: str | int = self.settings.device
        if self.settings.device.isdigit():
            source = int(self.settings.device)
        capture = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not capture.isOpened() and isinstance(source, str):
            capture.release()
            capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            raise CameraError(f"Could not open camera {self.settings.device}")

        if len(self.settings.fourcc) == 4:
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.settings.fourcc))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        capture.set(cv2.CAP_PROP_FPS, self.settings.fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._capture = capture
        controls = apply_controls(self.settings.device, self.settings.controls)
        self._log_control_result(controls)

        for _ in range(max(0, self.settings.warmup_frames)):
            ok, frame = capture.read()
            if ok and frame is not None:
                with self._lock:
                    self._frame = frame
            time.sleep(0.01)
        with self._lock:
            self._connected = True
            self._last_error = None

    @staticmethod
    def _log_control_result(result: ControlResult) -> None:
        if result.applied:
            LOGGER.info("Applied camera controls: %s", result.applied)
        if result.skipped:
            LOGGER.info("Skipped camera controls: %s", result.skipped)

    def _reader_loop(self) -> None:
        assert self._capture is not None
        previous = time.monotonic()
        smoothed_fps = 0.0
        while not self._stop.is_set():
            ok, frame = self._capture.read()
            now = time.monotonic()
            if not ok or frame is None:
                with self._lock:
                    self._last_error = "Camera read failed"
                time.sleep(0.05)
                continue
            delta = max(now - previous, 1e-6)
            instant_fps = 1.0 / delta
            smoothed_fps = instant_fps if smoothed_fps == 0 else 0.9 * smoothed_fps + 0.1 * instant_fps
            previous = now
            with self._frame_condition:
                self._frame = frame
                self._frame_time = time.time()
                self._frames_read += 1
                self._actual_fps = smoothed_fps
                self._last_error = None
                self._frame_condition.notify_all()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._capture is not None:
            self._capture.release()
        with self._frame_condition:
            self._capture = None
            self._connected = False
            self._frame_condition.notify_all()

    def restart(self) -> None:
        self.stop()
        self.start()

    def snapshot(self) -> np.ndarray:
        with self._lock:
            if self._frame is None:
                raise CameraError("No camera frame is available")
            return self._frame.copy()

    def frame_sequence(self) -> int:
        """Return the monotonically increasing count of captured live frames."""
        with self._lock:
            return self._frames_read

    def snapshot_after(self, sequence: int, timeout: float = 6.0) -> np.ndarray:
        """Wait for and return a frame captured after ``sequence``.

        This prevents calibration workflows from analyzing the cached frame
        that was current before a machine-positioning operation completed.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            with self._lock:
                if self._frames_read > int(sequence) and self._frame is not None:
                    return self._frame.copy()
                connected = self._connected
                last_error = self._last_error
            if not connected:
                raise CameraError("Camera disconnected while waiting for a fresh frame")
            if last_error:
                raise CameraError(last_error)
            time.sleep(0.02)
        raise CameraError(
            f"Camera did not provide a fresh frame within {float(timeout):g} seconds"
        )

    @staticmethod
    def _sharpness_score(image: np.ndarray) -> float:
        gray = (
            cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            if image.ndim == 3
            else image
        )
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _wait_for_new_frame(
        self,
        after_sequence: int,
        deadline: float,
    ) -> tuple[np.ndarray, int]:
        with self._frame_condition:
            while self._frames_read <= after_sequence:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CameraError(
                        "Timed out waiting for fresh camera frames during precision capture"
                    )
                if self._stop.is_set() or not self._connected:
                    raise CameraError("Camera stopped during precision capture")
                self._frame_condition.wait(timeout=remaining)
            if self._frame is None:
                raise CameraError("No camera frame is available")
            return self._frame.copy(), int(self._frames_read)

    def capture_burst(
        self,
        settings: PrecisionCaptureSettings | None = None,
        *,
        reapply_controls: bool = True,
    ) -> FrameBurst:
        """Capture distinct post-settle frames for precision analysis.

        The background reader owns the camera. This method waits on its
        monotonic frame counter so buffered or repeated snapshots cannot be
        mistaken for independent samples.
        """

        profile = settings or self.settings.precision_capture
        started = time.monotonic()
        controls = (
            self.apply_configured_controls()
            if reapply_controls
            else ControlResult({}, {}, {})
        )
        if profile.settle_seconds > 0 and self._stop.wait(profile.settle_seconds):
            raise CameraError("Camera stopped during precision-capture settling")
        deadline = time.monotonic() + profile.timeout_seconds
        with self._lock:
            sequence = int(self._frames_read)

        for _ in range(profile.discard_frames):
            _, sequence = self._wait_for_new_frame(sequence, deadline)

        frames: list[np.ndarray] = []
        sequences: list[int] = []
        sharpness: list[float] = []
        for _ in range(profile.sample_frames):
            frame, sequence = self._wait_for_new_frame(sequence, deadline)
            frames.append(frame)
            sequences.append(sequence)
            sharpness.append(self._sharpness_score(frame))

        return FrameBurst(
            frames=tuple(frames),
            sequence_numbers=tuple(sequences),
            discarded_frames=profile.discard_frames,
            settle_seconds=profile.settle_seconds,
            elapsed_seconds=time.monotonic() - started,
            sharpness_scores=tuple(sharpness),
            controls=controls,
        )

    def jpeg(self, quality: int | None = None) -> bytes:
        frame = self.snapshot()
        encode_quality = int(quality or self.settings.jpeg_quality)
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, encode_quality])
        if not ok:
            raise CameraError("Could not encode camera frame")
        return encoded.tobytes()

    def mjpeg(self, target_fps: float = 10.0) -> Iterator[bytes]:
        delay = 1.0 / max(1.0, target_fps)
        while not self._stop.is_set():
            try:
                jpeg = self.jpeg(quality=min(self.settings.jpeg_quality, 85))
            except CameraError:
                time.sleep(0.2)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
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
            )

    def apply_configured_controls(self) -> ControlResult:
        return apply_controls(self.settings.device, self.settings.controls)


class SyntheticCameraService(CameraService):
    """Stable simulated overhead camera used for development without hardware."""

    def __init__(self, settings: CameraSettings, work_area: WorkArea):
        super().__init__(settings)
        self.work_area = work_area
        self._scene = "bed"
        self._synthetic_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._synthetic_thread and self._synthetic_thread.is_alive():
            return
        self._stop.clear()
        with self._frame_condition:
            self._connected = True
            self._last_error = None
            self._frame = self._render_scene()
            self._frame_condition.notify_all()
        self._synthetic_thread = threading.Thread(target=self._synthetic_loop, daemon=True, name="synthetic-camera")
        self._synthetic_thread.start()

    def _synthetic_loop(self) -> None:
        delay = 1.0 / max(1, min(self.settings.fps, 10))
        while not self._stop.is_set():
            frame = self._render_scene()
            with self._frame_condition:
                self._frame = frame
                self._frame_time = time.time()
                self._frames_read += 1
                self._actual_fps = 1.0 / delay
                self._frame_condition.notify_all()
            time.sleep(delay)

    def stop(self) -> None:
        self._stop.set()
        if self._synthetic_thread and self._synthetic_thread.is_alive():
            self._synthetic_thread.join(timeout=2)
        with self._frame_condition:
            self._connected = False
            self._frame_condition.notify_all()

    def capture_burst(
        self,
        settings: PrecisionCaptureSettings | None = None,
        *,
        reapply_controls: bool = True,
    ) -> FrameBurst:
        """Generate distinct deterministic samples without real-time waiting."""

        profile = settings or self.settings.precision_capture
        started = time.monotonic()
        controls = (
            self.apply_configured_controls()
            if reapply_controls
            else ControlResult({}, {}, {})
        )
        frames: list[np.ndarray] = []
        sequences: list[int] = []
        sharpness: list[float] = []
        with self._frame_condition:
            if not self._connected:
                raise CameraError("Camera stopped during precision capture")
            for _ in range(profile.discard_frames):
                self._frame = self._render_scene()
                self._frames_read += 1
            for _ in range(profile.sample_frames):
                frame = self._render_scene()
                self._frame = frame
                self._frame_time = time.time()
                self._frames_read += 1
                frames.append(frame.copy())
                sequences.append(self._frames_read)
                sharpness.append(self._sharpness_score(frame))
            self._frame_condition.notify_all()
        return FrameBurst(
            frames=tuple(frames),
            sequence_numbers=tuple(sequences),
            discarded_frames=profile.discard_frames,
            settle_seconds=0.0,
            elapsed_seconds=time.monotonic() - started,
            sharpness_scores=tuple(sharpness),
            controls=controls,
        )

    def set_scene(self, scene: str) -> None:
        if scene not in {"bed", "checkerboard"}:
            raise CameraError(f"Unknown synthetic scene: {scene}")
        self._scene = scene
        with self._frame_condition:
            self._frame = self._render_scene()
            self._frames_read += 1
            self._frame_time = time.time()
            self._frame_condition.notify_all()

    def _render_scene(self) -> np.ndarray:
        width = max(640, self.settings.width)
        height = max(480, self.settings.height)
        if self._scene == "checkerboard":
            return self._render_checkerboard(width, height)
        return self._render_bed(width, height)

    def calibration_correspondences(self) -> list[tuple[float, float, float, float, str]]:
        """Return exact synthetic image/machine points for automatic demo setup."""
        width = max(640, self.settings.width)
        height = max(480, self.settings.height)
        margin_x = width * 0.12
        margin_y = height * 0.08
        corners = np.array(
            [
                [margin_x + width * 0.08, margin_y],
                [width - margin_x, margin_y + height * 0.035],
                [width - margin_x + width * 0.055, height - margin_y],
                [margin_x - width * 0.06, height - margin_y - height * 0.025],
            ],
            dtype=np.float32,
        )
        machine = np.array(
            [
                [self.work_area.x_min, self.work_area.y_max],
                [self.work_area.x_max, self.work_area.y_max],
                [self.work_area.x_max, self.work_area.y_min],
                [self.work_area.x_min, self.work_area.y_min],
            ],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(machine, corners)
        points = [
            (10.0, 10.0), (210.0, 10.0), (210.0, 210.0), (10.0, 210.0),
            (110.0, 110.0), (50.0, 110.0), (170.0, 110.0), (110.0, 50.0), (110.0, 170.0),
        ]
        source = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
        image = cv2.perspectiveTransform(source, matrix).reshape(-1, 2)
        return [
            (float(pixel[0]), float(pixel[1]), float(machine_point[0]), float(machine_point[1]), f"Synthetic {index}")
            for index, (pixel, machine_point) in enumerate(zip(image, points, strict=True), start=1)
        ]

    def _render_checkerboard(self, width: int, height: int) -> np.ndarray:
        frame = np.full((height, width, 3), 215, dtype=np.uint8)
        squares_x, squares_y = 10, 7
        square = int(min(width * 0.075, height * 0.1))
        board_w, board_h = squares_x * square, squares_y * square
        x0 = (width - board_w) // 2
        y0 = (height - board_h) // 2
        for row in range(squares_y):
            for col in range(squares_x):
                value = 25 if (row + col) % 2 == 0 else 245
                cv2.rectangle(
                    frame,
                    (x0 + col * square, y0 + row * square),
                    (x0 + (col + 1) * square, y0 + (row + 1) * square),
                    (value, value, value),
                    -1,
                )
        cv2.putText(frame, "Synthetic 9 x 6 inner-corner calibration board", (35, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (40, 40, 40), 2, cv2.LINE_AA)
        return frame

    def _render_bed(self, width: int, height: int) -> np.ndarray:
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (58, 61, 64)
        margin_x = width * 0.12
        margin_y = height * 0.08
        corners = np.array(
            [
                [margin_x + width * 0.08, margin_y],
                [width - margin_x, margin_y + height * 0.035],
                [width - margin_x + width * 0.055, height - margin_y],
                [margin_x - width * 0.06, height - margin_y - height * 0.025],
            ],
            dtype=np.float32,
        )
        machine = np.array(
            [
                [self.work_area.x_min, self.work_area.y_max],
                [self.work_area.x_max, self.work_area.y_max],
                [self.work_area.x_max, self.work_area.y_min],
                [self.work_area.x_min, self.work_area.y_min],
            ],
            dtype=np.float32,
        )
        machine_to_image = cv2.getPerspectiveTransform(machine, corners)
        bed_mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillConvexPoly(bed_mask, corners.astype(np.int32), 255)
        bed_color = np.full_like(frame, (112, 117, 119))
        frame[bed_mask > 0] = bed_color[bed_mask > 0]

        def project(points: list[tuple[float, float]]) -> np.ndarray:
            source = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
            return cv2.perspectiveTransform(source, machine_to_image).reshape(-1, 2)

        for x in np.linspace(self.work_area.x_min, self.work_area.x_max, 12):
            line = project([(float(x), self.work_area.y_min), (float(x), self.work_area.y_max)]).astype(np.int32)
            cv2.line(frame, tuple(line[0]), tuple(line[1]), (89, 94, 97), 1, cv2.LINE_AA)
        for y in np.linspace(self.work_area.y_min, self.work_area.y_max, 12):
            line = project([(self.work_area.x_min, float(y)), (self.work_area.x_max, float(y))]).astype(np.int32)
            cv2.line(frame, tuple(line[0]), tuple(line[1]), (89, 94, 97), 1, cv2.LINE_AA)

        # A rotated rectangular test workpiece, drawn in machine coordinates.
        center = np.array([118.0, 104.0])
        size = np.array([105.0, 62.0])
        angle = np.deg2rad(11.0)
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        local = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]]) * size
        board_mm = local @ rotation.T + center
        board_px = project([tuple(point) for point in board_mm]).astype(np.int32)
        cv2.fillConvexPoly(frame, board_px, (154, 184, 206), cv2.LINE_AA)
        cv2.polylines(frame, [board_px], True, (50, 66, 78), 4, cv2.LINE_AA)

        calibration_points = [
            (10.0, 10.0),
            (210.0, 10.0),
            (210.0, 210.0),
            (10.0, 210.0),
            (110.0, 110.0),
            (50.0, 110.0),
            (170.0, 110.0),
            (110.0, 50.0),
            (110.0, 170.0),
        ]
        for index, point in enumerate(calibration_points, start=1):
            px = project([point])[0].astype(int)
            cv2.drawMarker(frame, tuple(px), (20, 20, 230), cv2.MARKER_CROSS, 24, 3, cv2.LINE_AA)
            cv2.putText(frame, str(index), (int(px[0] + 9), int(px[1] - 9)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 2, cv2.LINE_AA)

        cv2.polylines(frame, [corners.astype(np.int32)], True, (210, 215, 220), 3, cv2.LINE_AA)
        cv2.putText(frame, "SIMULATION - no hardware commands are sent", (32, 48), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (245, 245, 245), 2, cv2.LINE_AA)
        cv2.putText(frame, "Red crosses are known bed-calibration points", (32, height - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (240, 240, 240), 2, cv2.LINE_AA)
        return frame

    def status(self) -> CameraStatus:
        status = super().status()
        status.synthetic = True
        status.device = "synthetic"
        return status

    def apply_configured_controls(self) -> ControlResult:
        return ControlResult(dict(self.settings.controls), {}, {"all": "synthetic camera"})
