from __future__ import annotations

from collections.abc import Callable
from typing import Any

import cv2
import numpy as np

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
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(750)
        self._poll_timer.timeout.connect(self.poll_status)

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
        self.notice.emit("Core services started")

    def stop(self) -> None:
        self._poll_timer.stop()
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
    ) -> None:
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
            lambda task=task: self._task_finished(task),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.thread_pool.start(task)

    @QtCore.Slot(object)
    def _task_finished(self, task: FunctionTask) -> None:
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
        if not self.runtime.running:
            return
        self._run(
            lambda: image_to_qimage(self.runtime.context.rectified_frame(refresh=True)),
            on_success=self.cameraImageReady.emit,
            label="Corrected bed-image refresh",
        )

    def capture_camera_still(self) -> None:
        self._run(
            lambda: self.runtime.context.save_capture(
                prefix="desktop-capture",
                undistort=True,
            ),
            on_success=lambda path: self.notice.emit(f"Saved {path.name}"),
            label="Camera capture",
        )

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
