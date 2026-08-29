from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..app import AppContext, RunningMachineIdentity
from ..config import (
    LEGACY_IMPLICIT_CONTROLLER_PORT,
    UNCONFIGURED_CONTROLLER_PORT,
    Settings,
    load_settings,
)
from ..machine.profiles import (
    MACHINE_REGISTRY_FILENAME,
    MachineRegistry,
    MachineSetupRequired,
)


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
        laser_lockout: bool = False,
    ) -> None:
        if type(hardware_enabled) is not bool:
            raise TypeError("hardware_enabled must be an exact boolean")
        if type(laser_lockout) is not bool:
            raise TypeError("laser_lockout must be an exact boolean")
        self.settings = settings
        self.hardware_enabled = hardware_enabled
        self.laser_lockout = laser_lockout
        registry_path = settings.app.data_dir / MACHINE_REGISTRY_FILENAME
        configured_port = settings.machine.port
        if not registry_path.exists() and (
            type(configured_port) is not str
            or not configured_port.strip()
            or configured_port.strip()
            in {
                UNCONFIGURED_CONTROLLER_PORT,
                LEGACY_IMPLICIT_CONTROLLER_PORT,
            }
        ):
            raise MachineSetupRequired(
                "No saved real machine is configured. Complete real-machine "
                "setup before starting E3."
            )
        self.machine_registry = MachineRegistry.load_or_migrate(settings)
        resolved_machine = self.machine_registry.resolve_machine()
        resolved_port = resolved_machine.machine.port
        if (
            type(resolved_port) is not str
            or not resolved_port.strip()
            or resolved_port.strip() == UNCONFIGURED_CONTROLLER_PORT
        ):
            raise MachineSetupRequired(
                "The active saved machine has no selected controller port. "
                "Complete real-machine setup before starting E3."
            )
        self.running_machine_id = resolved_machine.machine_id
        settings.machine = resolved_machine.machine
        settings.laser = resolved_machine.laser
        self.context = AppContext(
            settings,
            hardware_enabled=self.hardware_enabled,
            laser_lockout=self.laser_lockout,
            machine_identity=RunningMachineIdentity(
                machine_id=resolved_machine.machine_id,
                machine_name=resolved_machine.machine_name,
                created_from=resolved_machine.created_from,
                machine_profile_id=resolved_machine.machine_profile.id,
                tool_head_profile_id=resolved_machine.tool_head_profile.id,
                expected_camera_profile_id=(
                    resolved_machine.camera_profile_id
                ),
                expected_calibration_profile_id=(
                    resolved_machine.calibration_profile_id
                ),
            ),
        )
        self._state = RuntimeState.STOPPED
        self._error: str | None = None
        self._lock = threading.RLock()

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        *,
        hardware_enabled: bool = False,
        laser_lockout: bool = False,
    ) -> CoreRuntime:
        return cls(
            load_settings(config_path),
            hardware_enabled=hardware_enabled,
            laser_lockout=laser_lockout,
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

    def __enter__(self) -> CoreRuntime:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop()
