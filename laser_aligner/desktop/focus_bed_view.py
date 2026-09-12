"""Observational camera panel for the surface/focus setup window."""

from __future__ import annotations

import math
import time
from typing import Any

from .live_monitor import _MonitorThread
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()

STALE_SECONDS = 3.0
_FINISHING_WORKERS: set[_MonitorThread] = set()


def _release_worker(worker: _MonitorThread) -> None:
    if worker not in _FINISHING_WORKERS:
        return
    _FINISHING_WORKERS.discard(worker)
    worker.deleteLater()


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


class FocusBedView(QtWidgets.QWidget):
    """Reuse the shared camera's raw stream without acquiring machine authority.

    The containing dialog calls ``begin`` when shown and ``end`` when done.
    Neither operation starts/stops the shared camera or changes its settings.
    """

    def __init__(self, camera: Any, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.camera = camera
        self._worker: _MonitorThread | None = None
        self._active = False
        self._error: str | None = None
        self._received_at: float | None = None
        self._source_age: float | None = None
        self._source_size = ""
        self._pixmap: QtGui.QPixmap | None = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(QtWidgets.QLabel("Live bed view"))
        heading.addStretch()
        self.retry = QtWidgets.QPushButton("Retry view")
        self.retry.setAutoDefault(False)
        self.retry.clicked.connect(self._retry)
        heading.addWidget(self.retry)
        layout.addLayout(heading)
        self.status_label = QtWidgets.QLabel("STOPPED")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("focusCameraStatus")
        layout.addWidget(self.status_label)
        self.image_label = QtWidgets.QLabel("Live view starts when this window opens.")
        self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(320, 180)
        self.image_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Expanding
        )
        self.image_label.setStyleSheet("background: #101010; color: #dddddd;")
        self.image_label.installEventFilter(self)
        layout.addWidget(self.image_label, 1)
        self.note = QtWidgets.QLabel(
            "Raw camera view for visual positioning only. Raised surfaces are not "
            "height-corrected. Check the actual probe, surface and clearance."
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self._age_timer = QtCore.QTimer(self)
        self._age_timer.setInterval(250)
        self._age_timer.timeout.connect(self._update_status)

    def begin(self) -> None:
        if self._worker is not None:
            return
        self._active = True
        self._error = None
        self._received_at = None
        self._source_age = None
        self._pixmap = None
        self.image_label.setText("Connecting to the camera…")
        monitor = getattr(self.camera, "monitor_jpeg_frames", None)
        if not callable(monitor):
            monitor = getattr(self.camera, "monitor_frames", None)
        if not callable(monitor):
            self._error = "Live bed view needs the Pi camera connection."
            self._update_status()
            return
        # Keep the bounded latest-frame preparation already used by Live Monitor.
        worker = _MonitorThread(self.camera, 5, self)
        size = self.image_label.size()
        worker.set_target_size(size.width(), size.height())
        worker.frameAvailable.connect(self._frame_available)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self._worker = worker
        self._age_timer.start()
        self._update_status()
        worker.start()

    def end(self) -> None:
        self._active = False
        self._age_timer.stop()
        worker, self._worker = self._worker, None
        if worker is not None:
            # Queued events from this worker must not update a reopened view.
            worker.frameAvailable.disconnect(self._frame_available)
            worker.failed.disconnect(self._failed)
            worker.finished.disconnect(self._finished)
            worker.stop()
            if worker.wait(2500):
                worker.deleteLater()
            else:
                # Network teardown can outlast dialog closure. Do not destroy a
                # running QThread with the dialog or stop the shared camera.
                worker.setParent(None)
                _FINISHING_WORKERS.add(worker)
                worker.finished.connect(lambda w=worker: _release_worker(w))
                if not worker.isRunning():
                    _release_worker(worker)
        self._pixmap = None
        self._received_at = None
        self.image_label.setText("Live view stopped.")
        self._update_status()

    def _retry(self) -> None:
        self.end()
        self.begin()

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        if watched is self.image_label and event.type() == QtCore.QEvent.Type.Resize:
            worker = self._worker
            size = self.image_label.size()
            if worker is not None:
                worker.set_target_size(size.width(), size.height())
            self._render()
        return super().eventFilter(watched, event)

    def _render(self) -> None:
        if self._pixmap is None:
            return
        self.image_label.setPixmap(self._pixmap.scaled(
            self.image_label.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        ))

    @QtCore.Slot()
    def _frame_available(self) -> None:
        worker = self._worker
        if self.sender() is not None and self.sender() is not worker:
            return
        payload = None if worker is None else worker.take_latest()
        if payload is None or not self._active:
            return
        self._pixmap = QtGui.QPixmap.fromImage(payload["prepared_image"])
        now = time.monotonic()
        received = _finite_nonnegative(payload.get("received_monotonic"))
        self._received_at = now if received is None else min(now, received)
        self._source_age = _finite_nonnegative(payload.get("frame_age_seconds"))
        self._source_size = f"{payload['width']}×{payload['height']}"
        self._render()
        self._update_status()

    def _update_status(self) -> None:
        stale = False
        if not self._active:
            text = "STOPPED"
        elif self._error is not None:
            text = f"OFFLINE · {self._error}"
        elif self._received_at is None:
            text = "CONNECTING · waiting for a camera frame"
        else:
            elapsed = max(0.0, time.monotonic() - self._received_at)
            age = elapsed + (self._source_age or 0.0)
            stale = age >= STALE_SECONDS
            state = "STALE · last image only" if stale else "LIVE"
            age_text = (
                f"frame age {age:.1f} s" if self._source_age is not None
                else f"received {elapsed:.1f} s ago; capture age unknown"
            )
            text = f"{state} · {self._source_size} · {age_text}"
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            "color: #ef9a9a;" if stale or self._error is not None else ""
        )

    @QtCore.Slot(str)
    def _failed(self, message: str) -> None:
        if self.sender() is not None and self.sender() is not self._worker:
            return
        if self._active:
            self._error = message
            self._update_status()

    @QtCore.Slot()
    def _finished(self) -> None:
        if self.sender() is not None and self.sender() is not self._worker:
            return
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        if self._active and self._error is None:
            self._error = "Camera stream ended; use Retry view."
        self._update_status()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        self.end()
        super().closeEvent(event)


__all__ = ["FocusBedView"]
