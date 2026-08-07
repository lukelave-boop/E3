from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from laser_aligner.camera.service import CameraService
from laser_aligner.config import CameraSettings
from laser_aligner.errors import CameraError


def test_snapshot_after_waits_for_a_new_frame() -> None:
    camera = CameraService(CameraSettings())
    with camera._lock:
        camera._connected = True
        camera._frame = np.zeros((2, 2, 3), dtype=np.uint8)
        camera._frames_read = 4

    def publish() -> None:
        time.sleep(0.03)
        with camera._lock:
            camera._frame = np.full((2, 2, 3), 73, dtype=np.uint8)
            camera._frames_read = 5

    thread = threading.Thread(target=publish)
    thread.start()
    try:
        frame = camera.snapshot_after(4, timeout=0.5)
    finally:
        thread.join()
    assert np.all(frame == 73)


def test_snapshot_after_rejects_a_stale_frame() -> None:
    camera = CameraService(CameraSettings())
    with camera._lock:
        camera._connected = True
        camera._frame = np.zeros((2, 2, 3), dtype=np.uint8)
        camera._frames_read = 4
    with pytest.raises(CameraError, match="fresh frame"):
        camera.snapshot_after(4, timeout=0.03)
