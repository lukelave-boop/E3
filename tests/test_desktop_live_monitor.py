from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.desktop import live_monitor
from laser_aligner.desktop.live_monitor import (
    LiveMonitorWindow,
    _MonitorThread,
    _prepare_monitor_payload,
)
from laser_aligner.desktop.panels import CameraPanel


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _Camera:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []

    def monitor_jpeg_frames(
        self, *, fps, width=1280, height=720, quality=78, stop_event
    ):
        del quality
        self.calls.append((fps, width, height))
        ok, encoded = cv2.imencode(
            ".jpg", np.zeros((height, width, 3), dtype=np.uint8)
        )
        assert ok
        jpeg = encoded.tobytes()
        sequence = 0
        while not stop_event.wait(0.01):
            sequence += 1
            yield {
                "jpeg": jpeg,
                "sequence": sequence,
                "width": width,
                "height": height,
                "source_mode": "direct_mjpeg",
                "capture_fps": 17.9,
                "received_monotonic": time.monotonic(),
                "frame_age_seconds": 0.106,
            }


class _BurstCamera:
    def monitor_frames(self, *, fps, stop_event):
        del fps, stop_event
        for sequence, received in enumerate((1.0, 1.1, 1.2), start=1):
            yield {
                "image": np.zeros((2, 2, 3), dtype=np.uint8),
                "sequence": sequence,
                "width": 2,
                "height": 2,
                "received_monotonic": received,
            }


def _jpeg(width: int, height: int, value: int = 0) -> bytes:
    ok, encoded = cv2.imencode(
        ".jpg",
        np.full((height, width, 3), value, dtype=np.uint8),
    )
    assert ok
    return encoded.tobytes()


def test_camera_panel_exposes_raw_monitor_independent_of_camera_status(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    panel = CameraPanel()
    requests: list[bool] = []
    panel.monitorRequested.connect(lambda: requests.append(True))
    try:
        assert panel.monitor_button.isEnabled()
        panel.monitor_button.click()
        assert requests == [True]
        panel.set_status({"connected": False, "last_error": "offline"})
        assert panel.monitor_button.isEnabled()
    finally:
        panel.close()


def test_live_monitor_defaults_starts_stops_and_has_no_machine_dependency(
    qt_application: QtWidgets.QApplication,
) -> None:
    camera = _Camera()
    window = LiveMonitorWindow(camera)
    window.show()
    try:
        assert window.start_button.text() == "Start Monitor"
        assert window.rate.currentData() == 10
        window.start_button.click()
        deadline = time.monotonic() + 2
        while "ONLINE" not in window.status_label.text():
            qt_application.processEvents()
            if time.monotonic() >= deadline:
                raise AssertionError("Monitor did not publish a frame")
            time.sleep(0.005)
        assert camera.calls == [(10, 1280, 720)]
        assert "1280×720" in window.status_label.text()
        assert "raw" in window.status_label.text().lower()
        assert "DIRECT MJPEG" in window.status_label.text()
        assert "Capture 17.9 fps" in window.status_label.text()
        assert "Network" in window.status_label.text()
        assert "Display" in window.status_label.text()
        assert "Age 106 ms" in window.status_label.text()
        assert not window.start_button.isEnabled()
        window.stop_button.click()
        deadline = time.monotonic() + 2
        while window._worker is not None:
            qt_application.processEvents()
            if time.monotonic() >= deadline:
                raise AssertionError("Monitor did not stop")
            time.sleep(0.005)
        assert window.start_button.isEnabled()
        assert not hasattr(window, "machine")
    finally:
        window.close()


def test_monitor_worker_receive_fps_counts_replaced_payloads() -> None:
    worker = _MonitorThread(_BurstCamera(), 10)

    worker.run()
    latest = worker.take_latest()

    assert latest is not None
    assert latest["sequence"] == 3
    assert latest["network_fps"] == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("width", "height", "source_mode"),
    [(1280, 720, "transcoded"), (1920, 1080, "direct_mjpeg")],
)
def test_monitor_preparation_decodes_toward_target_with_correct_aspect_ratio(
    width: int,
    height: int,
    source_mode: str,
) -> None:
    prepared = _prepare_monitor_payload(
        {
            "jpeg": _jpeg(width, height),
            "width": width,
            "height": height,
            "source_mode": source_mode,
        },
        (500, 500),
    )

    image = prepared["prepared_image"]
    assert (image.width(), image.height()) == (500, 281)
    assert "jpeg" not in prepared
    assert "image" not in prepared


def test_frames_arriving_while_preparation_is_pending_select_the_newest(
    monkeypatch,
) -> None:
    preparation_started = threading.Event()
    release_preparation = threading.Event()
    prepared_sequences: list[int] = []

    class PendingCamera:
        def monitor_jpeg_frames(self, *, fps, stop_event):
            del fps, stop_event
            yield {"jpeg": b"one", "sequence": 1, "width": 2, "height": 2}
            assert preparation_started.wait(1)
            yield {"jpeg": b"two", "sequence": 2, "width": 2, "height": 2}
            yield {"jpeg": b"three", "sequence": 3, "width": 2, "height": 2}

    def prepare(payload, target):
        del target
        sequence = payload["sequence"]
        prepared_sequences.append(sequence)
        if sequence == 1:
            preparation_started.set()
            assert release_preparation.wait(1)
        result = dict(payload)
        result["prepared_image"] = QtGui.QImage(2, 2, QtGui.QImage.Format.Format_RGB32)
        return result

    monkeypatch.setattr(live_monitor, "_prepare_monitor_payload", prepare)
    worker = _MonitorThread(PendingCamera(), 15)
    worker.start()
    assert preparation_started.wait(1)
    deadline = time.monotonic() + 1
    while not worker._receiver_done.is_set() and time.monotonic() < deadline:
        time.sleep(0.001)
    release_preparation.set()
    assert worker.wait(1000)

    latest = worker.take_latest()
    assert prepared_sequences == [1, 3]
    assert latest is not None
    assert latest["sequence"] == 3


def test_high_rate_source_keeps_one_pending_gui_notification(monkeypatch) -> None:
    class HighRateCamera:
        def monitor_frames(self, *, fps, stop_event):
            del fps, stop_event
            image = np.zeros((2, 2, 3), dtype=np.uint8)
            for sequence in range(200):
                yield {
                    "image": image,
                    "sequence": sequence,
                    "width": 2,
                    "height": 2,
                    "received_monotonic": sequence / 100.0,
                }

    def prepare(payload, target):
        del target
        result = dict(payload)
        result["prepared_image"] = QtGui.QImage(2, 2, QtGui.QImage.Format.Format_RGB32)
        return result

    monkeypatch.setattr(live_monitor, "_prepare_monitor_payload", prepare)
    worker = _MonitorThread(HighRateCamera(), 15)
    notifications: list[bool] = []
    worker.frameAvailable.connect(lambda: notifications.append(True))

    worker.run()

    latest = worker.take_latest()
    assert len(notifications) == 1
    assert latest is not None
    assert latest["sequence"] == 199


def test_gui_slot_only_creates_pixmap_and_does_so_on_gui_thread(
    qt_application: QtWidgets.QApplication,
    monkeypatch,
) -> None:
    image = QtGui.QImage(320, 180, QtGui.QImage.Format.Format_RGB32)

    class PreparedWorker:
        def take_latest(self):
            return {
                "prepared_image": image,
                "sequence": 1,
                "width": 1920,
                "height": 1080,
                "source_mode": "direct_mjpeg",
                "capture_fps": 17.2,
                "network_fps": 14.9,
                "frame_age_seconds": 0.029,
            }

    real_pixmap = QtGui.QPixmap
    called_threads: list[QtCore.QThread] = []

    class PixmapProxy:
        @staticmethod
        def fromImage(source):  # noqa: N802
            called_threads.append(QtCore.QThread.currentThread())
            return real_pixmap.fromImage(source)

    monkeypatch.setattr(live_monitor.QtGui, "QPixmap", PixmapProxy)
    monkeypatch.setattr(
        live_monitor,
        "_prepare_monitor_payload",
        lambda *_args: pytest.fail("GUI slot performed frame preparation"),
    )
    window = LiveMonitorWindow(_Camera())
    window._worker = PreparedWorker()  # type: ignore[assignment]
    try:
        window._frame_available()
        assert called_threads == [qt_application.thread()]
        assert "Capture 17.2 fps" in window.status_label.text()
        assert "Network 14.9 fps" in window.status_label.text()
        assert "Display 0.0 fps" in window.status_label.text()
        assert "Age 29 ms" in window.status_label.text()
    finally:
        window._worker = None
        window.close()


def test_resize_updates_worker_target_without_queuing_render_work(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application

    class TargetWorker:
        def __init__(self) -> None:
            self.targets: list[tuple[int, int]] = []

        def set_target_size(self, width, height):
            self.targets.append((width, height))

    window = LiveMonitorWindow(_Camera())
    worker = TargetWorker()
    window._worker = worker  # type: ignore[assignment]
    try:
        window.show()
        QtWidgets.QApplication.processEvents()
        worker.targets.clear()
        window.resize(800, 500)
        QtWidgets.QApplication.processEvents()
        size = window.image_label.size()
        assert worker.targets[-1] == (size.width(), size.height())
        assert len(worker.targets) <= 2
    finally:
        window._worker = None
        window.close()
