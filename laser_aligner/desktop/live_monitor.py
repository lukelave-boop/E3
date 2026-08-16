from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from .controller import image_to_qimage
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class _MonitorThread(QtCore.QThread):
    frameAvailable = QtCore.Signal()
    failed = QtCore.Signal(str)

    def __init__(self, camera: Any, fps: int, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.camera = camera
        self.fps = fps
        self.stop_event = threading.Event()
        self._latest_lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._notification_pending = False
        self._receive_times: deque[float] = deque(maxlen=60)

    def run(self) -> None:
        monitor = getattr(self.camera, "monitor_frames", None)
        if not callable(monitor):
            self.failed.emit("Raw Live Monitor requires an e3camera:// camera")
            return
        try:
            for payload in monitor(fps=self.fps, stop_event=self.stop_event):
                if self.stop_event.is_set():
                    return
                received = payload.get("received_monotonic")
                received_monotonic = (
                    float(received) if received is not None else time.monotonic()
                )
                self._receive_times.append(received_monotonic)
                while (
                    self._receive_times
                    and received_monotonic - self._receive_times[0] > 2.0
                ):
                    self._receive_times.popleft()
                network_fps = (
                    (len(self._receive_times) - 1)
                    / (self._receive_times[-1] - self._receive_times[0])
                    if len(self._receive_times) > 1
                    and self._receive_times[-1] > self._receive_times[0]
                    else 0.0
                )
                payload = dict(payload)
                payload["network_fps"] = network_fps
                notify = False
                with self._latest_lock:
                    self._latest = payload
                    if not self._notification_pending:
                        self._notification_pending = True
                        notify = True
                if notify:
                    self.frameAvailable.emit()
        except Exception as exc:
            if not self.stop_event.is_set():
                self.failed.emit(str(exc))

    def stop(self) -> None:
        self.stop_event.set()

    def take_latest(self) -> dict[str, Any] | None:
        with self._latest_lock:
            payload = self._latest
            self._latest = None
            self._notification_pending = False
            return payload


class LiveMonitorWindow(QtWidgets.QWidget):
    """Raw, authority-neutral view of the Pi-owned camera's latest frame."""

    def __init__(self, camera: Any, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.camera = camera
        self._worker: _MonitorThread | None = None
        self._frame_times: deque[float] = deque(maxlen=60)
        self.setWindowTitle("Raw Live Monitor — no machine authority")
        self.resize(960, 600)
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("Start Monitor")
        self.stop_button = QtWidgets.QPushButton("Stop Monitor")
        self.stop_button.setEnabled(False)
        self.rate = QtWidgets.QComboBox()
        for fps in (5, 10, 15):
            self.rate.addItem(f"{fps} fps", fps)
        self.rate.setCurrentIndex(self.rate.findData(10))
        self.status_label = QtWidgets.QLabel("STOPPED · raw / uncorrected")
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(QtWidgets.QLabel("Target"))
        controls.addWidget(self.rate)
        controls.addWidget(self.status_label, 1)
        layout.addLayout(controls)
        self.image_label = QtWidgets.QLabel("Start the raw monitor to watch the camera area.")
        self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(320, 180)
        self.image_label.setStyleSheet("background: #101010; color: #dddddd;")
        layout.addWidget(self.image_label, 1)
        note = QtWidgets.QLabel(
            "RAW / UNCORRECTED camera pixels. This view grants no controller, motion, "
            "laser, calibration, or safety authority."
        )
        note.setWordWrap(True)
        note.setObjectName("warningLabel")
        layout.addWidget(note)
        self.start_button.clicked.connect(self.start_monitor)
        self.stop_button.clicked.connect(self.stop_monitor)

    def start_monitor(self) -> None:
        if self._worker is not None:
            return
        self._frame_times.clear()
        worker = _MonitorThread(self.camera, int(self.rate.currentData()), self)
        worker.frameAvailable.connect(self._frame_available)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self._worker = worker
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.rate.setEnabled(False)
        self.status_label.setText("CONNECTING · raw / uncorrected")
        worker.start()

    def stop_monitor(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.stop()
        self.status_label.setText("STOPPING · raw / uncorrected")

    @QtCore.Slot()
    def _frame_available(self) -> None:
        worker = self._worker
        payload = None if worker is None else worker.take_latest()
        if payload is None:
            return
        image = image_to_qimage(payload["image"])
        pixmap = QtGui.QPixmap.fromImage(image)
        self.image_label.setPixmap(
            pixmap.scaled(
                self.image_label.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )
        now = time.monotonic()
        self._frame_times.append(now)
        while self._frame_times and now - self._frame_times[0] > 2.0:
            self._frame_times.popleft()
        display_fps = (
            (len(self._frame_times) - 1) / (self._frame_times[-1] - self._frame_times[0])
            if len(self._frame_times) > 1 and self._frame_times[-1] > self._frame_times[0]
            else 0.0
        )
        age = payload.get("frame_age_seconds")
        age_text = f"{float(age) * 1000:.0f} ms" if age is not None else "—"
        capture = payload.get("capture_fps")
        capture_text = f"{float(capture):.1f}" if capture is not None else "—"
        network_fps = float(payload.get("network_fps") or 0.0)
        source_mode = str(payload.get("source_mode", "transcoded")).upper().replace("_", " ")
        self.status_label.setText(
            f"ONLINE · {payload['width']}×{payload['height']} · {source_mode} · raw"
            f" · Capture {capture_text} fps · Network {network_fps:.1f} fps"
            f" · Display {display_fps:.1f} fps · Age {age_text}"
        )

    @QtCore.Slot(str)
    def _failed(self, message: str) -> None:
        self.status_label.setText(f"OFFLINE · {message}")

    @QtCore.Slot()
    def _finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.rate.setEnabled(True)
        if not self.status_label.text().startswith("OFFLINE"):
            self.status_label.setText("STOPPED · raw / uncorrected")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        worker = self._worker
        if worker is not None:
            worker.stop()
            worker.wait(2500)
        super().closeEvent(event)


__all__ = ["LiveMonitorWindow"]
