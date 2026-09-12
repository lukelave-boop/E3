"""Live camera panel with explicit pixel selection and no machine access."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
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

    pointSelected = QtCore.Signal(dict)
    selectionInvalidated = QtCore.Signal(str)

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
        self._frame_metadata: dict[str, Any] | None = None
        self._selection: dict[str, Any] | None = None
        self._selection_enabled = False
        self._display_rect = QtCore.QRectF()
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
            "Camera selections use the calibrated bed plane. Raised surfaces are not "
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
        self._frame_metadata = None
        self._invalidate_selection("Camera view restarted")
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
        self._frame_metadata = None
        self._display_rect = QtCore.QRectF()
        self._invalidate_selection("Camera view stopped")
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
        elif (watched is self.image_label and event.type() == QtCore.QEvent.Type.MouseButtonPress
              and event.button() == QtCore.Qt.MouseButton.LeftButton and self._selection_enabled):
            self._select_point(event.position())
            return True
        return super().eventFilter(watched, event)

    def set_selection_enabled(self, enabled: bool) -> None:
        self._selection_enabled = bool(enabled)
        self.image_label.setCursor(QtCore.Qt.CursorShape.CrossCursor if enabled else QtCore.Qt.CursorShape.ArrowCursor)

    def clear_selection(self) -> None:
        self._invalidate_selection("Selected point cleared", notify=False)

    def _invalidate_selection(self, reason: str, *, notify: bool = True) -> None:
        if self._selection is None:
            return
        self._selection = None
        self._render()
        if notify:
            self.selectionInvalidated.emit(reason)

    def _frame_age(self) -> float | None:
        if self._received_at is None or self._source_age is None:
            return None
        return max(0.0, time.monotonic() - self._received_at) + self._source_age

    def _frame_fresh(self) -> bool:
        age = self._frame_age()
        metadata = self._frame_metadata
        dimensions_valid = bool(metadata and all(type(metadata.get(key)) is int and 0 < metadata[key] <= 30000
                                                for key in ("width", "height")))
        return bool(self._active and self._worker is not None and self._error is None
                    and dimensions_valid and self._pixmap is not None and not self._pixmap.isNull()
                    and metadata["camera_settings"] == self._camera_settings()
                    and age is not None and age < STALE_SECONDS)

    def current_frame_fresh(self) -> bool:
        return self._frame_fresh()

    def selection_fresh(self) -> bool:
        if not self._frame_fresh() or self._selection is None:
            return False
        current = self._frame_metadata
        return bool(current and all(self._selection.get(key) == current.get(key)
                                    for key in ("width", "height", "camera_fingerprint")))

    def selection_snapshot(self) -> dict[str, Any] | None:
        if self._selection is None:
            return None
        snapshot = copy.deepcopy(self._selection)
        # Continued matching live frames validate the marker; the original raw
        # metadata remains available separately for auditing the selected image.
        snapshot["frame_age_seconds"] = self._frame_age()
        snapshot["received_monotonic"] = self._received_at
        snapshot["snapshot_monotonic"] = time.monotonic()
        snapshot["fresh"] = self.selection_fresh()
        return snapshot

    def _select_point(self, point: QtCore.QPointF) -> None:
        if not self._frame_fresh() or not self._display_rect.contains(point):
            return
        metadata = self._frame_metadata
        if metadata is None:
            return
        x = (point.x() - self._display_rect.left()) / self._display_rect.width() * metadata["width"]
        y = (point.y() - self._display_rect.top()) / self._display_rect.height() * metadata["height"]
        if not 0 <= x < metadata["width"] or not 0 <= y < metadata["height"]:
            return
        self._selection = dict(copy.deepcopy(metadata), image_x=x, image_y=y,
                               selected_monotonic=time.monotonic())
        self._render()
        self.pointSelected.emit(self.selection_snapshot())

    def _metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = copy.deepcopy({k: v for k, v in payload.items() if k not in {"prepared_image", "image", "jpeg"}})
        metadata["raw_frame_metadata"] = copy.deepcopy(metadata)
        metadata["camera_settings"] = self._camera_settings()
        fingerprint_fields = {k: metadata.get(k) for k in (
            "width", "height", "source_mode", "source_width", "source_height",
            "camera_fingerprint", "source_fingerprint", "camera_id", "camera_device", "camera_settings")}
        metadata["camera_fingerprint"] = hashlib.sha256(
            json.dumps(fingerprint_fields, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        return metadata

    def _camera_settings(self) -> dict[str, Any] | None:
        settings = getattr(self.camera, "settings", None)
        return dataclasses.asdict(settings) if dataclasses.is_dataclass(settings) else None

    def _render(self) -> None:
        if self._pixmap is None:
            self._display_rect = QtCore.QRectF()
            return
        available = self.image_label.contentsRect()
        scaled = self._pixmap.scaled(
            available.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        # Treat preview pixels as logical label pixels even when a prepared
        # QImage carries a DPR; otherwise click geometry shifts on high DPI.
        scaled.setDevicePixelRatio(1.0)
        self._display_rect = QtCore.QRectF(QtWidgets.QStyle.alignedRect(
            self.image_label.layoutDirection(), QtCore.Qt.AlignmentFlag.AlignCenter,
            scaled.size(), available))
        if self._selection is not None:
            x = self._selection["image_x"] / self._selection["width"] * scaled.width()
            y = self._selection["image_y"] / self._selection["height"] * scaled.height()
            painter = QtGui.QPainter(scaled)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ffcf40"), 2))
            painter.drawEllipse(QtCore.QPointF(x, y), 6, 6)
            painter.drawLine(QtCore.QPointF(x-12, y), QtCore.QPointF(x+12, y))
            painter.drawLine(QtCore.QPointF(x, y-12), QtCore.QPointF(x, y+12))
            painter.end()
        self.image_label.setPixmap(scaled)

    @QtCore.Slot()
    def _frame_available(self) -> None:
        worker = self._worker
        if self.sender() is not None and self.sender() is not worker:
            return
        payload = None if worker is None else worker.take_latest()
        if payload is None or not self._active:
            return
        self._pixmap = QtGui.QPixmap.fromImage(payload["prepared_image"])
        self._pixmap.setDevicePixelRatio(1.0)
        metadata = self._metadata(payload)
        if self._selection and any(self._selection.get(key) != metadata.get(key)
                                   for key in ("width", "height", "camera_fingerprint")):
            self._invalidate_selection("Camera source or image dimensions changed")
        self._frame_metadata = metadata
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
        if self._selection is not None and not self._frame_fresh():
            self._invalidate_selection("Camera frame is stale or unavailable; select a point on a fresh view")

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
