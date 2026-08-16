from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.controller import DesktopController


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _Machine:
    def __init__(self, *, fail_connect: bool = False) -> None:
        self.fail_connect = fail_connect
        self.calls: list[str] = []
        self.connected = True
        self.reconnect_required = True
        self.coordinate_reference_ready = False

    def operation_generation(self) -> int:
        return 0

    @contextmanager
    def operation_scope(self, _generation: int):
        yield

    def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.connected = False
        self.reconnect_required = False
        self.coordinate_reference_ready = False

    def connect(self) -> None:
        self.calls.append("connect")
        if self.fail_connect:
            raise RuntimeError("controller unavailable")
        self.connected = True
        self.coordinate_reference_ready = False

    def request_stop(self, *, emergency: bool = False) -> None:
        del emergency

    def status(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "controller_reconnect_required": self.reconnect_required,
            "coordinate_reference_ready": self.coordinate_reference_ready,
            "jog_ready": False,
            "armed": False,
            "job": {},
        }


def _wait_for_tasks(
    application: QtWidgets.QApplication, controller: DesktopController
) -> None:
    deadline = time.monotonic() + 3.0
    while controller.has_active_tasks:
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for reconnect task")
        time.sleep(0.005)
    application.processEvents()


def _controller(machine: _Machine) -> DesktopController:
    context = SimpleNamespace(machine=machine)
    runtime = SimpleNamespace(
        context=context,
        running=True,
        status=lambda: {"machine": machine.status()},
    )
    return DesktopController(runtime)


def test_explicit_reconnect_disconnects_then_connects_without_motion_or_home(
    qt_application: QtWidgets.QApplication,
) -> None:
    machine = _Machine()
    controller = _controller(machine)
    notices: list[str] = []
    controller.notice.connect(notices.append)

    controller.reconnect_machine()
    _wait_for_tasks(qt_application, controller)

    assert machine.calls == ["disconnect", "connect"]
    assert machine.connected
    assert not machine.coordinate_reference_ready
    assert machine.status()["jog_ready"] is False
    assert notices == ["Controller reconnected; Home / park required"]


def test_failed_explicit_reconnect_remains_disconnected_and_does_not_loop(
    qt_application: QtWidgets.QApplication,
) -> None:
    machine = _Machine(fail_connect=True)
    controller = _controller(machine)
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)

    controller.reconnect_machine()
    _wait_for_tasks(qt_application, controller)

    assert machine.calls == ["disconnect", "connect", "disconnect"]
    assert not machine.connected
    assert not machine.coordinate_reference_ready
    assert errors == ["Controller reconnection failed: controller unavailable"]
