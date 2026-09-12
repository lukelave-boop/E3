from __future__ import annotations

import os
import time
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop import focus_bed_view
from laser_aligner.desktop.focus_bed_view import STALE_SECONDS, FocusBedView


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class Worker(QtCore.QObject):
    frameAvailable = QtCore.Signal()
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()

    def __init__(self, camera, fps, parent=None):
        super().__init__(parent)
        self.camera, self.fps = camera, fps
        self.started = self.stopped = False
        self.payload = None
        self.target = None
        self.waits = []
        self.stop_finishes = True
        self.running = True

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def wait(self, milliseconds):
        self.waits.append(milliseconds)
        return self.stop_finishes

    def isRunning(self):  # noqa: N802
        return self.running

    def set_target_size(self, width, height):
        self.target = (width, height)

    def take_latest(self):
        payload, self.payload = self.payload, None
        return payload


@pytest.fixture
def view(app, monkeypatch):
    del app
    monkeypatch.setattr(focus_bed_view, "_MonitorThread", Worker)
    camera = SimpleNamespace(monitor_frames=lambda **kwargs: iter(()))
    widget = FocusBedView(camera)
    widget.show()
    yield widget
    widget.end()
    widget.close()


def publish(view, *, received=None, source_age=0.1):
    worker = view._worker
    image = QtGui.QImage(640, 360, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("blue"))
    worker.payload = {
        "prepared_image": image, "width": 1920, "height": 1080,
        "frame_age_seconds": source_age,
        "received_monotonic": time.monotonic() if received is None else received,
    }
    worker.frameAvailable.emit()


def test_view_lifecycle_uses_camera_only_once_and_reopens(view):
    view.begin()
    worker = view._worker
    assert worker.started and worker.fps == 5
    view.begin()
    assert view._worker is worker
    publish(view)
    assert view.status_label.text().startswith("LIVE")
    assert "1920×1080" in view.status_label.text()
    assert "height-corrected" in view.note.text()
    assert not hasattr(view, "machine")
    assert not hasattr(view, "controller")
    view.end()
    assert worker.stopped and worker.waits == [2500]
    assert view._worker is None
    assert not view._age_timer.isActive()
    assert view.image_label.pixmap().isNull()
    assert view.status_label.text() == "STOPPED"
    view.begin()
    assert view._worker is not worker


def test_stalled_image_age_grows_and_reports_stale(view, monkeypatch):
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(focus_bed_view, "time", SimpleNamespace(monotonic=lambda: clock.value))
    view.begin()
    publish(view, received=100.0, source_age=0.2)
    assert "frame age 0.2 s" in view.status_label.text()
    clock.value += STALE_SECONDS
    view._update_status()
    assert view.status_label.text().startswith("STALE")
    assert "frame age 3.2 s" in view.status_label.text()
    assert "color" in view.status_label.styleSheet()
    publish(view, received=clock.value, source_age=0.1)
    assert view.status_label.text().startswith("LIVE")


@pytest.mark.parametrize("source_age", [None, float("nan"), -1, True])
def test_missing_or_invalid_capture_age_is_not_reported_as_fresh_capture(view, source_age):
    view.begin()
    publish(view, received=time.monotonic() - 4, source_age=source_age)
    assert view.status_label.text().startswith("STALE")
    assert "capture age unknown" in view.status_label.text()


def test_image_preserves_aspect_ratio_after_resize_and_click_has_no_action(view, app):
    view.begin()
    publish(view)
    view.resize(420, 650)
    app.processEvents()
    pixmap = view.image_label.pixmap()
    assert abs(pixmap.width() / pixmap.height() - 16 / 9) < 0.01
    assert pixmap.width() <= view.image_label.width()
    assert pixmap.height() <= view.image_label.height()
    assert view._worker.target == (view.image_label.width(), view.image_label.height())
    QtTest.QTest.mouseClick(view.image_label, QtCore.Qt.MouseButton.LeftButton)
    assert view._worker.started and not view._worker.stopped


def test_camera_failure_and_end_report_offline_without_retry_loop(view):
    view.begin()
    worker = view._worker
    worker.failed.emit("camera unavailable")
    worker.finished.emit()
    assert view.status_label.text() == "OFFLINE · camera unavailable"
    assert view._worker is None
    view.retry.click()
    assert view._worker is not None
    assert view.status_label.text().startswith("CONNECTING")


def test_missing_remote_camera_is_clear_and_does_not_start_worker(app):
    del app
    view = FocusBedView(object())
    view.begin()
    try:
        assert view._worker is None
        assert "OFFLINE" in view.status_label.text()
        assert "Pi camera" in view.status_label.text()
    finally:
        view.end()


def test_slow_worker_teardown_outlives_dialog_without_stopping_shared_camera(view):
    view.begin()
    worker = view._worker
    worker.stop_finishes = False
    view.end()
    assert worker.parent() is None
    assert worker in focus_bed_view._FINISHING_WORKERS
    worker.finished.emit()
    assert worker not in focus_bed_view._FINISHING_WORKERS


def test_real_monitor_worker_integration_starts_receives_and_stops(app):
    class Camera:
        stopped = False

        def monitor_frames(self, *, fps, stop_event):
            assert fps == 5
            try:
                while not stop_event.wait(0.01):
                    yield {
                        "image": np.zeros((90, 160, 3), dtype=np.uint8),
                        "width": 160, "height": 90,
                        "received_monotonic": time.monotonic(), "frame_age_seconds": 0.05,
                    }
            finally:
                self.stopped = True

    camera = Camera()
    view = FocusBedView(camera)
    view.show()
    view.begin()
    try:
        deadline = time.monotonic() + 2
        while not view.status_label.text().startswith("LIVE"):
            app.processEvents()
            assert time.monotonic() < deadline, view.status_label.text()
            time.sleep(0.005)
        assert not view.image_label.pixmap().isNull()
    finally:
        view.end()
        view.close()
    assert camera.stopped
