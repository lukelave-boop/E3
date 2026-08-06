from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..app import AppContext
from ..config import Settings, load_settings


class RuntimeState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(slots=True)
class RuntimeSnapshot:
    state: RuntimeState
    status: dict[str, Any] | None
    error: str | None = None


class CoreRuntime:
    """UI-neutral lifecycle boundary around the existing camera/machine core.

    The browser server and the native desktop application both need the same
    calibrated camera, project geometry, G-code and guarded machine services.
    This class deliberately exposes the existing AppContext without exposing
    HTTP concerns to the desktop layer.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        hardware_enabled: bool = False,
    ) -> None:
        self.settings = settings
        self.hardware_enabled = bool(hardware_enabled)
        self.context = AppContext(settings, hardware_enabled=self.hardware_enabled)
        self._state = RuntimeState.STOPPED
        self._error: str | None = None
        self._lock = threading.RLock()

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        *,
        hardware_enabled: bool = False,
    ) -> "CoreRuntime":
        return cls(
            load_settings(config_path),
            hardware_enabled=hardware_enabled,
        )

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    @property
    def running(self) -> bool:
        return self.state == RuntimeState.RUNNING

    def start(self) -> None:
        with self._lock:
            if self._state in {RuntimeState.STARTING, RuntimeState.RUNNING}:
                return
            self._state = RuntimeState.STARTING
            self._error = None
        try:
            self.context.start()
        except Exception as exc:
            with self._lock:
                self._state = RuntimeState.FAILED
                self._error = str(exc)
            raise
        with self._lock:
            self._state = RuntimeState.RUNNING

    def stop(self) -> None:
        with self._lock:
            if self._state in {RuntimeState.STOPPED, RuntimeState.STOPPING}:
                return
            self._state = RuntimeState.STOPPING
        try:
            self.context.stop()
        finally:
            with self._lock:
                self._state = RuntimeState.STOPPED

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            state = self._state
            error = self._error
        status: dict[str, Any] | None = None
        if state == RuntimeState.RUNNING:
            try:
                status = self.context.status()
            except Exception as exc:
                error = str(exc)
        return RuntimeSnapshot(state=state, status=status, error=error)

    def status(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "runtime_state": snapshot.state.value,
            "runtime_error": snapshot.error,
            **(snapshot.status or {}),
        }

    def __enter__(self) -> "CoreRuntime":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()
