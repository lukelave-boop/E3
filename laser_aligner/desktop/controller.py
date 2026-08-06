from __future__ import annotations

import json
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

from ..camera.controls import apply_controls
from ..core import CoreRuntime
from .qt import require_qt
from .tasks import FunctionTask

QtCore, QtGui, QtWidgets = require_qt()


def image_to_qimage(image: np.ndarray) -> QtGui.QImage:
    if image is None or image.size == 0:
        return QtGui.QImage()
    if image.ndim == 2:
        contiguous = np.ascontiguousarray(image)
        output = QtGui.QImage(
            contiguous.data,
            contiguous.shape[1],
            contiguous.shape[0],
            contiguous.strides[0],
            QtGui.QImage.Format.Format_Grayscale8,
        )
        return output.copy()
    if image.shape[2] == 4:
        converted = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
        fmt = QtGui.QImage.Format.Format_RGBA8888
    else:
        converted = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        fmt = QtGui.QImage.Format.Format_RGB888
    contiguous = np.ascontiguousarray(converted)
    output = QtGui.QImage(
        contiguous.data,
        contiguous.shape[1],
        contiguous.shape[0],
        contiguous.strides[0],
        fmt,
    )
    return output.copy()


class DesktopController(QtCore.QObject):
    statusChanged = QtCore.Signal(dict)
    cameraImageReady = QtCore.Signal(object)
    errorOccurred = QtCore.Signal(str)
    notice = QtCore.Signal(str)
    busyChanged = QtCore.Signal(bool)
    cameraFocusChanged = QtCore.Signal(dict)

    def __init__(
        self,
        runtime: CoreRuntime,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self._active_tasks = 0
        self._tasks: set[FunctionTask] = set()
        self._camera_refresh_in_flight = False
        self._live_camera_enabled = False
        self._live_camera_interval_ms = 1000
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(750)
        self._poll_timer.timeout.connect(self.poll_status)
        self._camera_live_timer = QtCore.QTimer(self)
        self._camera_live_timer.setInterval(self._live_camera_interval_ms)
        self._camera_live_timer.timeout.connect(self.refresh_camera_image)

    def start(self) -> None:
        self._run(
            self.runtime.start,
            on_success=lambda _: self._started(),
            label="Start core services",
        )

    def _started(self) -> None:
        self._poll_timer.start()
        self.poll_status()
        if self.runtime.context.bed.calibration is not None:
            self.refresh_camera_image()
            if self._live_camera_enabled:
                self._camera_live_timer.start()
        self.notice.emit("Core services started")

    def stop(self) -> None:
        self._poll_timer.stop()
        self._camera_live_timer.stop()
        self.thread_pool.waitForDone(5000)
        self._tasks.clear()
        self.runtime.stop()

    def _set_busy(self, delta: int) -> None:
        self._active_tasks = max(0, self._active_tasks + delta)
        self.busyChanged.emit(self._active_tasks > 0)

    def _run(
        self,
        callback: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        label: str = "Operation",
        show_busy: bool = True,
    ) -> FunctionTask:
        if show_busy:
            self._set_busy(1)
        task = FunctionTask(callback)
        self._tasks.add(task)

        if on_success is not None:
            task.signals.succeeded.connect(on_success)

        task.signals.failed.connect(
            lambda message: self.errorOccurred.emit(f"{label} failed: {message}")
        )

        # Route cleanup through a QObject slot in the GUI thread. Do not drop
        # the final Python reference from the worker thread.
        task.signals.finished.connect(
            lambda task=task, show_busy=show_busy: self._task_finished(
                task, show_busy
            ),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.thread_pool.start(task)
        return task

    @QtCore.Slot(object, bool)
    def _task_finished(
        self,
        task: FunctionTask,
        show_busy: bool = True,
    ) -> None:
        if show_busy:
            self._set_busy(-1)
        self._tasks.discard(task)

    def poll_status(self) -> None:
        if not self.runtime.running:
            return
        try:
            status = self.runtime.status()
        except Exception as exc:
            self.errorOccurred.emit(f"Status refresh failed: {exc}")
            return
        self.statusChanged.emit(status)

    def refresh_camera_image(self) -> None:
        if (
            not self.runtime.running
            or self._camera_refresh_in_flight
            or self.runtime.context.bed.calibration is None
        ):
            return
        self._camera_refresh_in_flight = True
        task = self._run(
            lambda: image_to_qimage(self.runtime.context.rectified_frame(refresh=True)),
            on_success=self.cameraImageReady.emit,
            label="Corrected bed-image refresh",
            show_busy=False,
        )
        task.signals.finished.connect(
            self._camera_refresh_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

    @QtCore.Slot()
    def _camera_refresh_finished(self) -> None:
        self._camera_refresh_in_flight = False

    def set_live_camera(self, enabled: bool, interval_ms: int | None = None) -> None:
        self._live_camera_enabled = bool(enabled)
        if interval_ms is not None:
            self.set_live_camera_interval(interval_ms)
        if self._live_camera_enabled and self.runtime.running:
            self._camera_live_timer.start()
            self.refresh_camera_image()
        else:
            self._camera_live_timer.stop()

    def set_live_camera_interval(self, interval_ms: int) -> None:
        self._live_camera_interval_ms = max(250, min(10_000, int(interval_ms)))
        self._camera_live_timer.setInterval(self._live_camera_interval_ms)
        if self._live_camera_enabled and self.runtime.running:
            self._camera_live_timer.start()

    def capture_camera_still(self) -> None:
        self._run(
            lambda: self.runtime.context.save_capture(
                prefix="desktop-capture",
                undistort=True,
            ),
            on_success=lambda path: self.notice.emit(f"Saved {path.name}"),
            label="Camera capture",
        )

    @staticmethod
    def _sharpness_score(image: np.ndarray) -> float:
        """Variance-of-Laplacian focus metric; higher is sharper."""
        if image is None or image.size == 0:
            return 0.0
        gray = (
            cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            if image.ndim == 3
            else image
        )
        height, width = gray.shape[:2]
        if width >= 40 and height >= 40:
            margin_x = max(1, int(width * 0.10))
            margin_y = max(1, int(height * 0.10))
            region = gray[
                margin_y : height - margin_y,
                margin_x : width - margin_x,
            ]
        else:
            region = gray
        if region.size == 0:
            return 0.0
        return float(cv2.Laplacian(region, cv2.CV_64F).var())

    def _apply_camera_focus(
        self,
        autofocus: bool,
        focus_value: int,
    ) -> dict[str, Any]:
        camera = self.runtime.context.camera
        status = camera.status()
        value = max(0, min(250, int(focus_value)))
        if status.synthetic:
            return {
                "autofocus": bool(autofocus),
                "focus_value": value,
                "sharpness": self._sharpness_score(camera.snapshot()),
                "applied": {},
                "skipped": {"camera": "synthetic camera"},
                "changed": True,
            }

        automatic = 1 if autofocus else 0
        requested: dict[str, int] = {
            "focus_automatic_continuous": automatic,
            "focus_auto": automatic,
        }
        if not autofocus:
            requested["focus_absolute"] = value

        result = apply_controls(camera.settings.device, requested)
        camera.settings.controls.update(requested)
        time.sleep(0.35)
        return {
            "autofocus": bool(autofocus),
            "focus_value": value,
            "sharpness": self._sharpness_score(camera.snapshot()),
            "applied": dict(result.applied),
            "skipped": dict(result.skipped),
            "changed": True,
        }

    def apply_camera_focus(
        self,
        autofocus: bool,
        focus_value: int,
    ) -> None:
        self._run(
            lambda: self._apply_camera_focus(autofocus, focus_value),
            on_success=self._camera_focus_complete,
            label="Apply camera focus",
        )

    def measure_camera_sharpness(self) -> None:
        self._run(
            lambda: {
                "sharpness": self._sharpness_score(
                    self.runtime.context.camera.snapshot()
                ),
                "changed": False,
            },
            on_success=self.cameraFocusChanged.emit,
            label="Measure camera sharpness",
            show_busy=False,
        )

    def save_camera_focus(
        self,
        autofocus: bool,
        focus_value: int,
    ) -> None:
        def operation() -> dict[str, Any]:
            payload = self._apply_camera_focus(autofocus, focus_value)
            payload["saved_path"] = str(
                self._persist_camera_focus(autofocus, focus_value)
            )
            return payload

        self._run(
            operation,
            on_success=self._camera_focus_complete,
            label="Save locked camera focus",
        )

    def _persist_camera_focus(
        self,
        autofocus: bool,
        focus_value: int,
    ) -> Path:
        settings = self.runtime.settings
        source = settings.source_path
        target = source
        if source.name != "local.json":
            target = settings.project_root / "config" / "local.json"

        payload: dict[str, Any] = {}
        if target.exists():
            payload = json.loads(target.read_text(encoding="utf-8"))
        elif source.exists():
            payload = json.loads(source.read_text(encoding="utf-8"))

        camera_payload = payload.setdefault("camera", {})
        controls = camera_payload.setdefault("controls", {})
        automatic = 1 if autofocus else 0
        controls["focus_automatic_continuous"] = automatic
        controls["focus_auto"] = automatic
        controls["focus_absolute"] = max(0, min(250, int(focus_value)))

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return target

    def _camera_focus_complete(self, payload: dict[str, Any]) -> None:
        self.cameraFocusChanged.emit(payload)
        saved_path = payload.get("saved_path")
        if saved_path:
            self.notice.emit(f"Saved locked camera focus to {saved_path}")
        else:
            self.notice.emit("Applied camera focus")
        if self.runtime.context.bed.calibration is not None:
            self.refresh_camera_image()

    def connect_machine(self) -> None:
        self._run(
            self.runtime.context.machine.connect,
            on_success=lambda _: self._machine_changed("Controller connected"),
            label="Controller connection",
        )

    def disconnect_machine(self) -> None:
        self._run(
            self.runtime.context.machine.disconnect,
            on_success=lambda _: self._machine_changed("Controller disconnected"),
            label="Controller disconnect",
        )

    def park_at_camera_pose(self) -> None:
        self._run(
            self.runtime.context.machine.prepare_photo_position,
            on_success=lambda result: self._machine_changed(
                f"Parked at X{result['position']['x']:.2f} Y{result['position']['y']:.2f}"
            ),
            label="Home and park",
        )


    def run_job(self, gcode: str, name: str, *, arm_phrase: str | None = None) -> None:
        def operation() -> dict[str, Any]:
            if arm_phrase is not None:
                self.runtime.context.machine.arm(arm_phrase)
            return self.runtime.context.machine.start_job(gcode, name)

        self._run(
            operation,
            on_success=lambda _: self._machine_changed(f"Started {name}"),
            label="Start job",
        )

    def pause_resume(self) -> None:
        self.errorOccurred.emit(
            "Pause/resume is reserved in the desktop UI but will remain disabled "
            "until the Falcon controller's realtime hold/resume behavior is tested."
        )

    def emergency_stop(self) -> None:
        self._run(
            lambda: self.runtime.context.machine.stop_job(emergency=True),
            on_success=lambda _: self._machine_changed("Software stop sent; laser-off requested"),
            label="Software stop",
        )

    def send_diagnostic(self, command: str) -> None:
        self._run(
            lambda: self.runtime.context.machine.send_command(command),
            on_success=lambda responses: self._diagnostic_complete(command, responses),
            label="Diagnostic command",
        )

    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> None:
        del dx_mm, dy_mm, feed_mm_min
        self.errorOccurred.emit(
            "Jogging is visible in the desktop shell but remains disabled until "
            "the core exposes a separately tested guarded jog operation."
        )

    def _diagnostic_complete(self, command: str, responses: list[str]) -> None:
        self.notice.emit(f"{command}: {' · '.join(responses) if responses else 'acknowledged'}")
        self.poll_status()

    def _machine_changed(self, message: str) -> None:
        self.notice.emit(message)
        self.poll_status()
