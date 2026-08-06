from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .qt import require_qt

QtCore, _, _ = require_qt()


class TaskSignals(QtCore.QObject):
    succeeded = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    finished = QtCore.Signal()


class FunctionTask(QtCore.QRunnable):
    """Run one blocking core operation without freezing the Qt event loop."""

    def __init__(self, callback: Callable[[], Any]) -> None:
        super().__init__()
        self.callback = callback
        self.signals = TaskSignals()
        # Keep ownership in Python until the controller receives the queued
        # completion signal. QThreadPool auto-deletion plus an unreferenced
        # Python wrapper can otherwise trigger a startup-time double free.
        self.setAutoDelete(False)

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = self.callback()
        except Exception as exc:  # pragma: no cover - exercised with hardware
            self.signals.failed.emit(str(exc))
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()
