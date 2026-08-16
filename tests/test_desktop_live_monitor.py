from __future__ import annotations

import os
import time
from collections.abc import Iterator

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.live_monitor import LiveMonitorWindow, _MonitorThread
from laser_aligner.desktop.panels import CameraPanel


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _Camera:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []

    def monitor_frames(self, *, fps, width=1280, height=720, quality=78, stop_event):
        del quality
        self.calls.append((fps, width, height))
        sequence = 0
        while not stop_event.wait(0.01):
            sequence += 1
            yield {
                "image": np.zeros((height, width, 3), dtype=np.uint8),
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
