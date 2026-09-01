from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from typing import Any, ClassVar

from .qt import require_qt

QtCore, _, _ = require_qt()

LOGGER = logging.getLogger(__name__)


def _exception_message(exc: Exception) -> str:
    parts = [str(exc) or exc.__class__.__name__]
    seen = {parts[0]}
    for raw_note in getattr(exc, "__notes__", ()):
        note = str(raw_note).strip()
        if note and note not in seen:
            parts.append(note)
            seen.add(note)
    return "\n".join(parts)


class TaskSignals(QtCore.QObject):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()
    # Controller-owned tasks use these signals so Qt can associate delivery
    # with the receiving QObject and automatically disconnect a destroyed
    # controller.  The legacy signals above remain available to dialogs that
    # own their complete task lifecycle.
    resultReady = QtCore.Signal(object, object)
    errorReady = QtCore.Signal(object, str)
    completed = QtCore.Signal(object)


class _TaskRetainer(QtCore.QObject):
    """Release completed wrappers only from this QObject's owning UI thread."""

    @QtCore.Slot(object)
    def release(self, task: object) -> None:
        if isinstance(task, FunctionTask):
            task._release_retention()


_TASK_RETAINER: _TaskRetainer | None = None


def _task_retainer() -> _TaskRetainer:
    global _TASK_RETAINER
    if _TASK_RETAINER is None:
        _TASK_RETAINER = _TaskRetainer()
    return _TASK_RETAINER


class FunctionTask(QtCore.QRunnable):
    """Run one blocking core operation without freezing the Qt event loop."""

    _live_lock: ClassVar[threading.Lock] = threading.Lock()
    _live_tasks: ClassVar[set[FunctionTask]] = set()

    def __init__(
        self,
        callback: Callable[[], Any],
        *,
        label: str = "Background task",
        cancel: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.callback = callback
        normalized_label = " ".join(str(label).split())
        self.label = normalized_label or "Background task"
        self.signals = TaskSignals()
        self._outcome_lock = threading.Lock()
        self._publish_outcome = True
        self._cancel_callback = cancel
        self._cancel_invoked = False
        self._finished_event = threading.Event()
        # Keep ownership in Python until the controller receives the queued
        # completion signal. QThreadPool auto-deletion plus an unreferenced
        # Python wrapper can otherwise trigger a startup-time double free.  A
        # shutdown may intentionally return before the callback does, so retain
        # every wrapper independently of its window/controller until run()
        # actually reaches its final statement.
        self.setAutoDelete(False)
        with self._live_lock:
            self._live_tasks.add(self)
        self.signals.completed.connect(
            _task_retainer().release,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )

    @property
    def finished(self) -> bool:
        return self._finished_event.is_set()

    def suppress_callbacks(self) -> None:
        """Prevent subsequent task outcome/UI signals while retaining ownership."""

        cancel_callback: Callable[[], None] | None = None
        with self._outcome_lock:
            self._publish_outcome = False
            if not self._cancel_invoked:
                self._cancel_invoked = True
                cancel_callback = self._cancel_callback
        if cancel_callback is not None:
            try:
                cancel_callback()
            except Exception:
                LOGGER.exception(
                    "Cooperative cancellation failed for %s",
                    self.label,
                )

    def _callbacks_allowed(self) -> bool:
        with self._outcome_lock:
            return self._publish_outcome

    def wait_until(self, deadline_monotonic: float) -> bool:
        """Wait only until one absolute monotonic deadline for task completion."""

        if (
            type(deadline_monotonic) not in {int, float}
            or not math.isfinite(float(deadline_monotonic))
        ):
            raise ValueError("Task deadline must be a finite monotonic timestamp")
        remaining = max(0.0, float(deadline_monotonic) - time.monotonic())
        return self._finished_event.wait(remaining)

    def start_on(self, pool: Any) -> None:
        """Submit this retained wrapper and release it if submission fails."""

        try:
            pool.start(self)
        except BaseException:
            self.suppress_callbacks()
            self._finished_event.set()
            self._release_retention()
            raise

    def _release_retention(self) -> None:
        with self._live_lock:
            self._live_tasks.discard(self)

    @classmethod
    def suppress_all_callbacks(cls) -> None:
        """Suppress outcomes for all live global-pool tasks during app shutdown."""

        with cls._live_lock:
            tasks = tuple(cls._live_tasks)
        for task in tasks:
            task.suppress_callbacks()

    @classmethod
    def unfinished_labels(cls) -> tuple[str, ...]:
        with cls._live_lock:
            return tuple(
                sorted(task.label for task in cls._live_tasks if not task.finished)
            )

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = self.callback()
        except Exception as exc:  # pragma: no cover - exercised with hardware
            message = _exception_message(exc)
            if self._callbacks_allowed():
                self.signals.failed.emit(message)
                self.signals.errorReady.emit(self, message)
            else:
                LOGGER.warning(
                    "Suppressed late failure from %s during shutdown: %s",
                    self.label,
                    message,
                )
        else:
            if self._callbacks_allowed():
                self.signals.succeeded.emit(result)
                self.signals.resultReady.emit(self, result)
        finally:
            # Internal completion is always delivered so a still-live
            # controller can release its registration. Legacy `finished` is a
            # UI callback and is intentionally suppressed with other outcomes.
            self._finished_event.set()
            self.signals.completed.emit(self)
            if self._callbacks_allowed():
                self.signals.finished.emit()
