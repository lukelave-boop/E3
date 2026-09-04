from __future__ import annotations

import os
import threading
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
    CAPABILITY_PI_COHERENT_STATUS,
    CAPABILITY_PI_CONTROLLER_SESSION,
    CAPABILITY_PI_OWNED_JOBS,
    CAPABILITY_PI_STRUCTURED_ERRORS,
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
    boot_id = "00000000-0000-4000-8000-000000000002"

    def metadata() -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "boot_id": boot_id,
            "build": {"version": "test", "revision": "test"},
            "state_revision": 1,
            "controller_session_generation": 0,
            "controller_state": "DISCONNECTED",
        }

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
                **metadata(),
                "capabilities": [
                    CAPABILITY_PI_OWNED_JOBS,
                    CAPABILITY_PI_CONTROLLER_SESSION,
                    CAPABILITY_PI_STRUCTURED_ERRORS,
                    CAPABILITY_PI_COHERENT_STATUS,
                ],
                "actions": SERVER_ACTION_SCHEMAS,
            }
        assert request["action"] == ACTION_MACHINE_DISCONNECT
        status = machine.status()
        status["connected"] = False
        return {
            "ok": True,
            "request_id": request["request_id"],
            "status": status,
            **metadata(),
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


def test_motion_result_from_superseded_operation_generation_is_not_presented(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = _Machine()
    machine.reconnect_required = False
    machine.coordinate_reference_ready = True
    session = {"value": 4}
    revision = {"value": 12}
    started = threading.Event()
    release = threading.Event()

    def status() -> dict[str, object]:
        return {
            "controller_state": "READY_MOTION",
            "controller_session_generation": session["value"],
            "controller_state_revision": revision["value"],
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": True,
            "jog_ready": True,
            "armed": False,
            "job": {},
        }

    def jog(_dx: float, _dy: float, _feed: float) -> dict[str, object]:
        started.set()
        assert release.wait(3.0)
        return {"position": {"x": 1.0, "y": 0.0}}

    monkeypatch.setattr(machine, "status", status)
    monkeypatch.setattr(machine, "ensure_connected", lambda: None, raising=False)
    monkeypatch.setattr(machine, "jog", jog, raising=False)
    controller = _controller(machine)
    notices: list[str] = []
    controller.notice.connect(notices.append)

    controller.jog(1.0, 0.0, 100.0)
    deadline = time.monotonic() + 3.0
    while not started.is_set():
        qt_application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for jog worker")
        time.sleep(0.005)

    # Model an external STOP/recovery replacing the authority while the worker
    # is still returning. The deliberately permissive fake operation_scope lets
    # this test exercise the desktop's queued-callback suppression directly.
    machine.generation += 1
    session["value"] += 1
    revision["value"] += 1
    release.set()
    _wait_for_tasks(qt_application, controller)

    assert notices == []


def test_motion_result_from_superseded_controller_session_is_not_presented(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = _Machine()
    machine.reconnect_required = False
    machine.coordinate_reference_ready = True
    session = {"value": 4}
    revision = {"value": 12}
    started = threading.Event()
    release = threading.Event()

    def status() -> dict[str, object]:
        return {
            "controller_state": "READY_MOTION",
            "controller_session_generation": session["value"],
            "controller_state_revision": revision["value"],
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": True,
            "jog_ready": True,
            "armed": False,
            "job": {},
        }

    def jog(_dx: float, _dy: float, _feed: float) -> dict[str, object]:
        started.set()
        assert release.wait(3.0)
        return {"position": {"x": 1.0, "y": 0.0}}

    monkeypatch.setattr(machine, "status", status)
    monkeypatch.setattr(machine, "ensure_connected", lambda: None, raising=False)
    monkeypatch.setattr(machine, "jog", jog, raising=False)
    controller = _controller(machine)
    notices: list[str] = []
    controller.notice.connect(notices.append)

    controller.jog(1.0, 0.0, 100.0)
    deadline = time.monotonic() + 3.0
    while not started.is_set():
        qt_application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for jog worker")
        time.sleep(0.005)

    # Keep the operation generation unchanged so this specifically proves that
    # replacing the controller session invalidates the queued result.
    session["value"] += 1
    revision["value"] += 1
    release.set()
    _wait_for_tasks(qt_application, controller)

    assert machine.generation == 7
    assert notices == []


def test_queued_home_never_executes_on_a_newly_recovered_session(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = _Machine()
    authority = {"session": 4, "revision": 12}
    scope_entered = threading.Event()
    release_scope = threading.Event()
    ensure_calls: list[bool] = []
    home_calls: list[bool] = []
    publications: list[object] = []

    def status() -> dict[str, object]:
        return {
            "controller_state": "READY_HOME_REQUIRED",
            "controller_session_generation": authority["session"],
            "controller_state_revision": authority["revision"],
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": False,
            "jog_ready": False,
            "armed": False,
            "job": {},
        }

    @contextmanager
    def delayed_operation_scope(generation: int):
        assert generation == machine.generation
        scope_entered.set()
        assert release_scope.wait(3.0)
        yield

    def ensure_connected() -> None:
        ensure_calls.append(True)

    def home_operation() -> dict[str, object]:
        home_calls.append(True)
        return {"position": {"x": 100.0, "y": 100.0}}

    monkeypatch.setattr(machine, "status", status)
    monkeypatch.setattr(machine, "operation_scope", delayed_operation_scope)
    monkeypatch.setattr(machine, "ensure_connected", ensure_connected, raising=False)
    controller = _controller(machine)
    task = controller._run(
        home_operation,
        on_success=publications.append,
        label="Home and park",
        requires_controller=True,
    )

    deadline = time.monotonic() + 3.0
    while not scope_entered.is_set():
        qt_application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for queued Home worker")
        time.sleep(0.005)

    # Client B performs STOP and completes communication-only recovery before
    # client A's already-queued Home may verify its original authority.
    authority.update(session=5, revision=20)
    release_scope.set()
    assert task.wait_until(time.monotonic() + 3.0)
    _wait_for_tasks(qt_application, controller)

    assert machine.generation == 7
    assert ensure_calls == []
    assert home_calls == []
    assert publications == []


def test_home_revalidates_session_after_idempotent_ensure_connected(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = _Machine()
    authority = {"session": 4, "revision": 12}
    ensure_calls: list[bool] = []
    home_calls: list[bool] = []

    def status() -> dict[str, object]:
        return {
            "controller_state": "READY_HOME_REQUIRED",
            "controller_session_generation": authority["session"],
            "controller_state_revision": authority["revision"],
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": False,
            "jog_ready": False,
            "armed": False,
            "job": {},
        }

    def ensure_connected() -> None:
        ensure_calls.append(True)
        # Model a remote idempotent Connect returning the session recovered
        # after this Home request was created.
        authority.update(session=5, revision=20)

    def home_operation() -> dict[str, object]:
        home_calls.append(True)
        return {"position": {"x": 100.0, "y": 100.0}}

    monkeypatch.setattr(machine, "status", status)
    monkeypatch.setattr(machine, "ensure_connected", ensure_connected, raising=False)
    controller = _controller(machine)
    task = controller._run(
        home_operation,
        label="Home and park",
        requires_controller=True,
    )
    assert task.wait_until(time.monotonic() + 3.0)
    _wait_for_tasks(qt_application, controller)

    assert ensure_calls == [True]
    assert home_calls == []


@pytest.mark.parametrize("worker_fails", [False, True])
def test_queued_home_callbacks_are_discarded_when_stop_advances_state_revision(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    worker_fails: bool,
) -> None:
    machine = _Machine()
    state = {"name": "READY_HOME_REQUIRED", "revision": 12}
    started = threading.Event()
    release = threading.Event()
    successes: list[object] = []
    failures: list[str] = []
    finishes: list[bool] = []

    def status() -> dict[str, object]:
        return {
            "controller_state": state["name"],
            "controller_session_generation": 4,
            "controller_state_revision": state["revision"],
            "connected": state["name"] == "READY_HOME_REQUIRED",
            "allow_motion": True,
            "coordinate_reference_ready": False,
            "jog_ready": False,
            "armed": False,
            "job": {},
        }

    def home_operation() -> dict[str, object]:
        started.set()
        assert release.wait(3.0)
        if worker_fails:
            raise RuntimeError("late Home failure")
        return {"position": {"x": 100.0, "y": 100.0}}

    monkeypatch.setattr(machine, "status", status)
    monkeypatch.setattr(machine, "ensure_connected", lambda: None, raising=False)
    controller = _controller(machine)
    task = controller._run(
        home_operation,
        on_success=successes.append,
        on_failure=failures.append,
        on_finished=lambda: finishes.append(True),
        label="Home and park",
        requires_controller=True,
    )

    deadline = time.monotonic() + 3.0
    while not started.is_set():
        qt_application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for Home worker")
        time.sleep(0.005)
    release.set()
    assert task.wait_until(time.monotonic() + 3.0)

    # The worker has captured revision 12 and queued its result. A global STOP
    # then advances only authoritative state; the local operation/session IDs
    # deliberately remain unchanged until Qt receives the queued callbacks.
    state.update(name="STOPPING", revision=13)
    _wait_for_tasks(qt_application, controller)

    assert machine.generation == 7
    assert successes == []
    assert failures == []
    assert finishes == []


def test_home_success_captured_after_stop_started_is_not_presented(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = _Machine()
    operation_returned = threading.Event()
    successes: list[object] = []

    def status() -> dict[str, object]:
        stopped = operation_returned.is_set()
        return {
            "controller_state": "STOPPING" if stopped else "READY_HOME_REQUIRED",
            "controller_session_generation": 4,
            "controller_state_revision": 13 if stopped else 12,
            "connected": not stopped,
            "allow_motion": True,
            "coordinate_reference_ready": False,
            "jog_ready": False,
            "armed": False,
            "job": {},
        }

    def home_operation() -> dict[str, object]:
        operation_returned.set()
        return {"position": {"x": 100.0, "y": 100.0}}

    monkeypatch.setattr(machine, "status", status)
    monkeypatch.setattr(machine, "ensure_connected", lambda: None, raising=False)
    controller = _controller(machine)
    task = controller._run(
        home_operation,
        on_success=successes.append,
        label="Home and park",
        requires_controller=True,
    )
    assert task.wait_until(time.monotonic() + 3.0)
    _wait_for_tasks(qt_application, controller)

    # Revision 13 equals the worker's captured revision. The STOPPING snapshot
    # itself must therefore be enough to reject the apparent Home success.
    assert successes == []


def test_status_projection_rejects_lower_same_boot_revision_and_accepts_new_boot(
    qt_application: QtWidgets.QApplication,
) -> None:
    machine = _Machine()
    snapshots = iter(
        (
            {
                "machine": {
                    "controller_state": "READY_MOTION",
                    "controller_session_generation": 5,
                    "state_revision": 20,
                    "node_boot_id": "boot-a",
                }
            },
            {
                "machine": {
                    "controller_state": "READY_HOME_REQUIRED",
                    "controller_session_generation": 4,
                    "state_revision": 19,
                    "node_boot_id": "boot-a",
                }
            },
            {
                "machine": {
                    "controller_state": "READY_HOME_REQUIRED",
                    "controller_session_generation": 1,
                    "state_revision": 1,
                    "node_boot_id": "boot-b",
                }
            },
        )
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(machine=machine),
        running=True,
        status=lambda: next(snapshots),
    )
    controller = DesktopController(runtime)
    delivered: list[dict[str, Any]] = []
    controller.statusChanged.connect(delivered.append)

    controller.poll_status()
    controller.poll_status()
    controller.poll_status()
    qt_application.processEvents()

    assert [item["machine"]["node_boot_id"] for item in delivered] == [
        "boot-a",
        "boot-b",
    ]
