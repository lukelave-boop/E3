from __future__ import annotations

import gc
import os
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop import controller as controller_module
from laser_aligner.desktop.controller import (
    DESKTOP_SHUTDOWN_TIMEOUT_SECONDS,
    DESKTOP_WORKER_DRAIN_SECONDS,
    DesktopController,
)
from laser_aligner.desktop.tasks import FunctionTask


class _Timer:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class _Signal:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def emit(self, message: str) -> None:
        self.messages.append(message)


class _Camera:
    def __init__(self) -> None:
        self.cancel_calls = 0

    def cancel_pending_requests(self, *, terminal: bool = False) -> None:
        assert terminal is True
        self.cancel_calls += 1


class _Machine:
    def __init__(self, *, pi_owned_execution: bool) -> None:
        self.pi_owned_execution = pi_owned_execution
        # Model a fresh remote client whose first status refresh has not run.
        self.pi_owned_job_active = False
        self.detach_calls = 0
        self.detach_deadlines: list[float] = []
        self.detach_remember_idle: list[bool] = []
        self.stop_calls: list[bool] = []

    def detach(
        self,
        *,
        deadline: float,
        remember_idle_for_shutdown: bool = False,
    ) -> None:
        self.detach_calls += 1
        self.detach_deadlines.append(deadline)
        self.detach_remember_idle.append(remember_idle_for_shutdown)

    def request_stop(self, *, emergency: bool = False) -> None:
        self.stop_calls.append(emergency)

    def operation_generation(self) -> int:
        return 0

    @contextmanager
    def operation_scope(self, _generation: int) -> Iterator[None]:
        yield


class _Runtime:
    def __init__(self, machine: _Machine | None = None) -> None:
        self.camera = _Camera()
        self.machine = machine or _Machine(pi_owned_execution=True)
        self.context = SimpleNamespace(camera=self.camera, machine=self.machine)
        self.stop_deadlines: list[float] = []

    def stop(self, *, deadline: float) -> None:
        self.stop_deadlines.append(deadline)


class _ThreadPool:
    def __init__(self) -> None:
        self.tasks: list[FunctionTask] = []
        self.waits: list[int] = []

    def start(self, task: FunctionTask) -> None:
        self.tasks.append(task)

    def waitForDone(self, timeout_ms: int) -> bool:
        self.waits.append(timeout_ms)
        return all(task.finished for task in self.tasks)


def _controller(machine: _Machine) -> SimpleNamespace:
    camera = _Camera()
    return SimpleNamespace(
        _shutdown_started=False,
        _shutdown_finalized=False,
        _shutdown_deadline_monotonic=None,
        _poll_timer=_Timer(),
        _camera_live_timer=_Timer(),
        _active_tasks=0,
        _tasks=set(),
        _trace_request_id=0,
        _trace_cancel_event=threading.Event(),
        _trace_review_active=True,
        _trace_sample_image=object(),
        _trace_sample_area=object(),
        _trace_sample_signature=object(),
        _template_match_request_id=0,
        _template_match_cancel_event=threading.Event(),
        _template_review_active=True,
        _template_review_signature=object(),
        runtime=SimpleNamespace(
            context=SimpleNamespace(camera=camera, machine=machine)
        ),
        errorOccurred=_Signal(),
    )


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.mark.parametrize(
    ("pi_owned_execution", "expected_detaches", "expected_stops"),
    [(True, 1, []), (False, 0, [False])],
)
def test_begin_shutdown_never_stops_remote_execution_from_an_empty_cache(
    pi_owned_execution: bool,
    expected_detaches: int,
    expected_stops: list[bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controller_module.time, "monotonic", lambda: 100.0)
    machine = _Machine(pi_owned_execution=pi_owned_execution)
    controller = _controller(machine)

    first_deadline = DesktopController.begin_shutdown(controller)
    repeated_deadline = DesktopController.begin_shutdown(controller)

    assert first_deadline == repeated_deadline == 104.0
    assert controller._shutdown_started is True
    assert controller._poll_timer.stop_calls == 1
    assert controller._camera_live_timer.stop_calls == 1
    assert controller._trace_cancel_event.is_set()
    assert controller._template_match_cancel_event.is_set()
    assert machine.detach_calls == expected_detaches
    assert machine.detach_deadlines == (
        [pytest.approx(100.05)] if pi_owned_execution else []
    )
    assert machine.detach_remember_idle == ([True] if pi_owned_execution else [])
    assert machine.stop_calls == expected_stops
    assert controller.runtime.context.camera.cancel_calls == 1
    assert controller.errorOccurred.messages == []


def test_remote_detach_budget_never_exceeds_the_global_shutdown_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(controller_module.time, "monotonic", lambda: 100.0)
    machine = _Machine(pi_owned_execution=True)
    controller = _controller(machine)

    DesktopController.begin_shutdown(controller, deadline=100.02)

    assert machine.detach_deadlines == [pytest.approx(100.02)]


def test_shutdown_deadline_never_extends_and_worker_drain_is_finite(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qt_application
    monkeypatch.setattr(controller_module.time, "monotonic", lambda: 100.0)
    runtime = _Runtime()
    controller = DesktopController(runtime)  # type: ignore[arg-type]
    pool = _ThreadPool()
    controller.thread_pool = pool  # type: ignore[assignment]

    assert DESKTOP_SHUTDOWN_TIMEOUT_SECONDS == 4.0
    assert DESKTOP_SHUTDOWN_TIMEOUT_SECONDS < 5.0
    assert DESKTOP_WORKER_DRAIN_SECONDS == 1.0
    assert controller.begin_shutdown(104.0) == 104.0
    assert controller.begin_shutdown(108.0) == 104.0
    assert controller.begin_shutdown(103.0) == 103.0

    controller.stop(deadline=109.0)

    assert pool.waits == [1000]
    assert -1 not in pool.waits
    assert runtime.stop_deadlines == [103.0]
    assert runtime.camera.cancel_calls == 1
    assert runtime.machine.detach_calls == 1
    assert runtime.machine.detach_deadlines == [pytest.approx(100.05)]


def test_late_task_outcomes_and_busy_signals_are_suppressed_after_shutdown(
    qt_application: QtWidgets.QApplication,
) -> None:
    runtime = _Runtime()
    controller = DesktopController(runtime)  # type: ignore[arg-type]
    pool = _ThreadPool()
    controller.thread_pool = pool  # type: ignore[assignment]
    successes: list[object] = []
    failures: list[str] = []
    finished: list[str] = []
    busy: list[bool] = []
    errors: list[str] = []
    drained: list[bool] = []
    controller.busyChanged.connect(busy.append)
    controller.errorOccurred.connect(errors.append)
    controller.tasksDrained.connect(lambda: drained.append(True))

    success_task = controller._run(
        lambda: "late result",
        on_success=successes.append,
        on_finished=lambda: finished.append("success"),
        label="Late success worker",
    )

    def fail_late() -> None:
        raise RuntimeError("late failure")

    failure_task = controller._run(
        fail_late,
        on_failure=failures.append,
        on_finished=lambda: finished.append("failure"),
        label="Late failure worker",
    )
    assert busy == [True, True]

    controller.begin_shutdown(time.monotonic() + 4.0)
    busy.clear()
    success_task.run()
    failure_task.run()
    qt_application.processEvents()

    assert successes == []
    assert failures == []
    assert finished == []
    assert busy == []
    assert errors == []
    assert drained == []
    assert controller._tasks == set()
    assert "Late success worker" not in FunctionTask.unfinished_labels()
    assert "Late failure worker" not in FunctionTask.unfinished_labels()


def test_trace_requests_replace_and_shutdown_sets_the_cooperative_cancel_token(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    runtime = _Runtime()
    controller = DesktopController(runtime)  # type: ignore[arg-type]
    queued_operations: list[object] = []
    controller._sync_camera_timer = lambda: None  # type: ignore[method-assign]
    controller._run = (  # type: ignore[method-assign]
        lambda operation, **_kwargs: queued_operations.append(operation)
    )

    initial_token = controller._trace_cancel_event
    controller.detect_trace_objects({})
    first_request_token = controller._trace_cancel_event
    controller.detect_trace_objects({})
    second_request_token = controller._trace_cancel_event

    assert len(queued_operations) == 2
    assert initial_token.is_set()
    assert first_request_token.is_set()
    assert not second_request_token.is_set()

    controller.begin_shutdown(time.monotonic() + 4.0)

    assert second_request_token.is_set()


def test_template_matches_replace_and_shutdown_sets_the_cooperative_cancel_token(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    runtime = _Runtime()
    controller = DesktopController(runtime)  # type: ignore[arg-type]
    queued_operations: list[object] = []
    controller._run = (  # type: ignore[method-assign]
        lambda operation, **_kwargs: queued_operations.append(operation)
    )

    initial_token = controller._template_match_cancel_event
    controller.match_cut_templates(())
    first_request_token = controller._template_match_cancel_event
    controller.match_cut_templates(())
    second_request_token = controller._template_match_cancel_event

    assert len(queued_operations) == 2
    assert initial_token.is_set()
    assert first_request_token.is_set()
    assert not second_request_token.is_set()

    controller.begin_shutdown(time.monotonic() + 4.0)

    assert second_request_token.is_set()


def test_function_task_uses_absolute_deadline_and_retains_wrapper_until_completion(
    qt_application: QtWidgets.QApplication,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocked() -> None:
        entered.set()
        assert release.wait(2.0)

    pool = QtCore.QThreadPool()
    task = FunctionTask(blocked, label="Retained shutdown worker")
    task.start_on(pool)
    assert entered.wait(1.0)
    assert not task.wait_until(time.monotonic() + 0.01)
    reference = weakref.ref(task)
    del task
    gc.collect()

    retained = reference()
    assert retained is not None
    assert "Retained shutdown worker" in FunctionTask.unfinished_labels()
    del retained

    release.set()
    assert pool.waitForDone(1000)
    completed = reference()
    assert completed is not None and completed.finished
    del completed
    qt_application.processEvents()
    gc.collect()
    assert "Retained shutdown worker" not in FunctionTask.unfinished_labels()


def test_function_task_cooperative_cancel_is_invoked_once_outside_outcome_lock(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    cancel_calls: list[str] = []
    task: FunctionTask

    def cancel() -> None:
        # Re-entering suppression proves the callback does not run under the
        # task outcome lock and remains idempotent.
        cancel_calls.append("cancel")
        task.suppress_callbacks()

    task = FunctionTask(lambda: None, label="Cancelable worker", cancel=cancel)
    task.suppress_callbacks()
    task.suppress_callbacks()

    assert cancel_calls == ["cancel"]
    task.run()


def test_controller_background_cancel_callback_is_invoked_on_shutdown(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    runtime = _Runtime()
    controller = DesktopController(runtime)  # type: ignore[arg-type]
    pool = _ThreadPool()
    controller.thread_pool = pool  # type: ignore[assignment]
    cancelled = threading.Event()

    task = controller.run_background(
        lambda: "unused",
        cancel=cancelled.set,
        label="Cancelable indexed preview",
    )

    controller.begin_shutdown(time.monotonic() + 4.0)

    assert cancelled.is_set()
    task.run()


@pytest.mark.parametrize("operator_stop", [False, True])
def test_pi_start_failure_survives_remote_cleanup_but_not_operator_stop(operator_stop):
    machine = SimpleNamespace(
        pi_owned_execution=True,
        operation_generation=lambda: 2 if operator_stop else 1,
        status=lambda: {"job": {"job_id": "rejected-job"}},
    )
    task = SimpleNamespace(label="Start job")
    # Use a hashable stand-in for a registered worker.
    class Task:
        label = "Start job"
    task = Task()
    registration = SimpleNamespace(
        machine_authority=SimpleNamespace(
            requested_operation_generation=1, invalidated_before_execution=False,
        ), on_failure=None,
    )
    controller = SimpleNamespace(
        _shutdown_started=False, _task_callbacks={task: registration},
        _task_authority_is_current=lambda _: False,
        runtime=SimpleNamespace(context=SimpleNamespace(machine=machine)),
        errorOccurred=_Signal(),
    )
    DesktopController._task_failed(controller, task, "secondary OFF failed")
    assert len(controller.errorOccurred.messages) == (0 if operator_stop else 1)
    if not operator_stop:
        assert controller._reported_start_failure_job == "rejected-job"


def test_pi_start_and_terminal_poll_report_error_only_once():
    job = {"job_id": "failed-start", "state": "failed", "running": False,
           "finished_at": 123, "error": "secondary OFF failed"}
    controller = SimpleNamespace(
        runtime=SimpleNamespace(running=True, status=lambda: {"machine": {"job": job}}),
        _accept_machine_status_revision=lambda _: True,
        statusChanged=SimpleNamespace(emit=lambda _: None),
        errorOccurred=_Signal(), _reported_terminal_job=None,
        _reported_start_failure_job="failed-start",
    )
    DesktopController.poll_status(controller)
    DesktopController.poll_status(controller)
    assert controller.errorOccurred.messages == []
    controller._reported_start_failure_job = None
    controller._reported_terminal_job = None
    controller._reported_terminal_jobs = {}
    DesktopController.poll_status(controller)
    DesktopController.poll_status(controller)
    assert len(controller.errorOccurred.messages) == 1


@pytest.mark.parametrize("state,error,stale,expected", [
    ("stopped", "Job stopped", False, False),
    ("stopped", None, False, False),
    ("stopped", "secondary OFF failed", False, True),
    ("failed", "Job stopped", False, True),
    ("interrupted", "Pi restarted", False, True),
    ("failed", "secondary OFF failed", True, False),
    ("starting", "Job stopped", False, False),
    ("receiving", "Job stopped", False, False),
])
def test_pi_terminal_notification_requires_fresh_terminal_failure(state, error, stale, expected):
    job = {"job_id": "old-job-12345", "name": "circle.gcode", "state": state,
           "running": False, "finished_at": 123, "error": error, "status_stale": stale}
    controller = SimpleNamespace(
        runtime=SimpleNamespace(running=True, status=lambda: {"machine": {
            "pi_owned_execution": True, "status_stale": True, "job": job,
        }}),
        _accept_machine_status_revision=lambda _: True,
        statusChanged=SimpleNamespace(emit=lambda _: None),
        errorOccurred=_Signal(), _reported_terminal_job=None,
    )
    DesktopController.poll_status(controller)
    assert len(controller.errorOccurred.messages) == int(expected)
    if expected:
        assert "circle.gcode [old-job-]" in controller.errorOccurred.messages[0]
        # Same UUID, amended finish timestamp, and an intervening job must not
        # redisplay an old failure as if it belonged to the new job.
        job["job_id"] = "another-job"
        DesktopController.poll_status(controller)
        job["job_id"] = "old-job-12345"
        job["finished_at"] = 124
        DesktopController.poll_status(controller)
        assert len(controller.errorOccurred.messages) == 2


def test_pi_stale_failure_is_reported_once_when_fresh():
    job = {"job_id": "failure", "state": "failed", "running": False,
           "finished_at": 123, "error": "controller alarm", "status_stale": True}
    controller = SimpleNamespace(
        runtime=SimpleNamespace(running=True, status=lambda: {"machine": {"job": job}}),
        _accept_machine_status_revision=lambda _: True,
        statusChanged=SimpleNamespace(emit=lambda _: None),
        errorOccurred=_Signal(), _reported_terminal_job=None,
    )
    DesktopController.poll_status(controller)
    assert controller.errorOccurred.messages == []
    job["status_stale"] = False
    DesktopController.poll_status(controller)
    DesktopController.poll_status(controller)
    assert len(controller.errorOccurred.messages) == 1
