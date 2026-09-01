from __future__ import annotations

import threading
import time

from laser_aligner.app import AppContext
from laser_aligner.core.runtime import CoreRuntime, RuntimeState


class _BoundedMachine:
    def __init__(self) -> None:
        self.deadlines: list[float] = []
        self.disconnect_calls = 0

    def shutdown(self, *, deadline: float) -> None:
        self.deadlines.append(deadline)
        time.sleep(min(0.04, max(0.0, deadline - time.monotonic())))

    def disconnect(self) -> None:
        self.disconnect_calls += 1


class _BoundedCamera:
    def __init__(self) -> None:
        self.deadlines: list[float] = []

    def stop(self, *, deadline: float) -> None:
        self.deadlines.append(deadline)
        time.sleep(min(0.04, max(0.0, deadline - time.monotonic())))


def test_runtime_and_context_share_one_absolute_shutdown_deadline() -> None:
    deadline = time.monotonic() + 0.5
    machine = _BoundedMachine()
    camera = _BoundedCamera()
    context = AppContext.__new__(AppContext)
    context.machine = machine
    context.camera = camera
    runtime = CoreRuntime.__new__(CoreRuntime)
    runtime.context = context
    runtime._lock = threading.RLock()
    runtime._state = RuntimeState.RUNNING
    runtime._error = None
    started = time.monotonic()

    runtime.stop(deadline=deadline)

    assert time.monotonic() - started < 0.3
    assert machine.deadlines == [deadline]
    assert camera.deadlines == [deadline]
    assert machine.disconnect_calls == 0
    assert runtime.state is RuntimeState.STOPPED


def test_ordinary_context_stop_retains_non_desktop_disconnect_contract() -> None:
    calls: list[str] = []

    class Machine:
        def disconnect(self) -> None:
            calls.append("machine.disconnect")

    class Camera:
        def stop(self) -> None:
            calls.append("camera.stop")

    context = AppContext.__new__(AppContext)
    context.machine = Machine()
    context.camera = Camera()

    context.stop()

    assert calls == ["machine.disconnect", "camera.stop"]
