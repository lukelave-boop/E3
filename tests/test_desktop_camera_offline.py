from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.controller import DesktopController


@pytest.fixture
def qt_application():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _Camera:
    def __init__(self) -> None:
        self.connected = False
        self.frames_read = 0
        self.last_error = "offline"

    def status(self):
        return SimpleNamespace(
            connected=self.connected,
            frames_read=self.frames_read,
            last_error=self.last_error,
        )


class _Context:
    def __init__(self) -> None:
        self.camera = _Camera()
        self.bed = SimpleNamespace(calibration=object())

    def bed_calibration_validity(self):
        return {"state": "VALID", "reasons": []}


def _controller(device: str) -> DesktopController:
    context = _Context()
    runtime = SimpleNamespace(
        context=context,
        settings=SimpleNamespace(
            camera=SimpleNamespace(device=device),
        ),
        running=True,
        status=lambda: {"machine": {"job": {}}},
    )
    return DesktopController(runtime)


def test_automatic_unreachable_remote_camera_is_silent(qt_application) -> None:
    controller = _controller("e3camera://192.168.5.18:8766")
    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)

    message = (
        "Could not communicate with remote camera at "
        "192.168.5.18:8766: timed out"
    )
    controller._camera_refresh_failed(message, 0)

    assert errors == []
    assert controller._camera_error_latched == message


def test_manual_unreachable_remote_camera_still_alerts(qt_application) -> None:
    controller = _controller("e3camera://192.168.5.18:8766")
    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)

    message = (
        "Could not communicate with remote camera at "
        "192.168.5.18:8766: timed out"
    )
    controller._camera_refresh_failed(message, 0, manual=True)

    assert len(errors) == 1
    assert "Remote camera is unavailable" in errors[0]
    assert "usable offline" in errors[0]


def test_remote_configuration_error_is_not_silenced(qt_application) -> None:
    controller = _controller("e3camera://192.168.5.18:8766")
    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)

    controller._camera_refresh_failed(
        "Remote Pi camera profile does not match the desktop profile: width",
        0,
    )

    assert len(errors) == 1
    assert "Remote camera is unavailable" in errors[0]


def test_local_camera_failure_keeps_existing_warning(qt_application) -> None:
    controller = _controller("0")
    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)

    controller._camera_refresh_failed("Could not open camera 0", 0)

    assert len(errors) == 1
    assert "Another application may have exclusive control" in errors[0]
