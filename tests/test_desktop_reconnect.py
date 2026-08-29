from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.desktop.controller import DesktopController
from laser_aligner.machine import remote_service as remote_service_module
from laser_aligner.machine.pi_job_protocol import (
    CAPABILITY_PI_OWNED_JOBS,
    PROTOCOL_VERSION,
)
from laser_aligner.machine.pi_machine_server import (
    ACTION_MACHINE_DISCONNECT,
    ACTION_SERVICE_CAPABILITIES,
    SERVER_ACTION_SCHEMAS,
)
from laser_aligner.machine.remote_service import RemoteMachineService


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
        self.generation = 7
        self.scoped_generations: list[int] = []

    def operation_generation(self) -> int:
        return self.generation

    @contextmanager
    def operation_scope(self, generation: int):
        self.scoped_generations.append(generation)
        yield

    def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.generation += 1
        self.connected = False
        self.reconnect_required = False
        self.coordinate_reference_ready = False

    def connect(self) -> None:
        self.calls.append("connect")
        if self.fail_connect:
            raise RuntimeError("controller unavailable")
        self.connected = True
        self.coordinate_reference_ready = False

    def replace_connection(self) -> None:
        requested_generation = self.scoped_generations[-1]
        self.disconnect()
        replacement_generation = self.operation_generation()
        if replacement_generation != requested_generation + 1:
            raise RuntimeError("Controller reconnection was cancelled by software STOP")
        try:
            with self.operation_scope(replacement_generation):
                self.connect()
        except Exception:
            self.disconnect()
            raise

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


def _controller(machine: Any) -> DesktopController:
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
    assert machine.scoped_generations == [7, 8]
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


def test_desktop_disconnect_rebinds_remote_cleanup_after_revoking_worker_generation(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "desktop-disconnect-regression-token")
    machine = RemoteMachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            port="e3bridge://pi.test:9876",
            allow_motion=True,
            controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    requests: list[dict[str, Any]] = []

    def request_response(
        host: str,
        port: int,
        token: str,
        request: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        assert (host, port) == ("pi.test", 9876)
        assert token == "desktop-disconnect-regression-token"
        assert timeout > 0.0
        requests.append(dict(request))
        if request["action"] == ACTION_SERVICE_CAPABILITIES:
            return {
                "ok": True,
                "request_id": request["request_id"],
                "protocol_version": PROTOCOL_VERSION,
                "capabilities": [CAPABILITY_PI_OWNED_JOBS],
                "actions": SERVER_ACTION_SCHEMAS,
            }
        assert request["action"] == ACTION_MACHINE_DISCONNECT
        status = machine.status()
        status["connected"] = False
        return {
            "ok": True,
            "request_id": request["request_id"],
            "status": status,
        }

    monkeypatch.setattr(remote_service_module, "request_response", request_response)
    controller = _controller(machine)
    errors: list[str] = []
    notices: list[str] = []
    controller.errorOccurred.connect(errors.append)
    controller.notice.connect(notices.append)
    queued_generation = machine.operation_generation()

    controller.disconnect_machine()
    _wait_for_tasks(qt_application, controller)

    actions = [request["action"] for request in requests]
    assert machine.operation_generation() == queued_generation + 1
    assert actions.count(ACTION_MACHINE_DISCONNECT) == 1
    assert errors == []
    assert notices == ["Controller disconnected"]
