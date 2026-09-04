from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from ..air_assist import (
    AIR_ASSIST_DIRECTIVE_PREFIX,
    AirAssistCommands,
    AirAssistSettings,
    AirAssistTarget,
    validate_air_assist_settings,
)
from ..config import LaserSettings, MachineSettings
from ..errors import MachineError, SafetyError
from ..gcode.preview import contains_motion, parse_words, strip_comment
from ..geometry.polygon import (
    ConvexPolygon,
    convex_polygon_contains_normalized,
    normalize_convex_polygon,
)
from .controller_dialects import (
    CONTROLLER_DIALECT_REGISTRY,
    GRBL_DIALECT,
    MANUAL_QUERY_COMMANDS,
    MARLIN_DIALECT,
    CommandResponseKind,
    ControllerDialect,
    HomingResponseKind,
    is_exact_grbl_locked_error_response,
    parse_grbl_coordinate_state,
    parse_grbl_realtime_status,
    parse_grbl_step_idle_delay,
    resolve_air_assist_commands,
)
from .controller_session import (
    CONNECTED_CONTROLLER_STATES,
    CONNECTING_CONTROLLER_STATES,
    ControllerSession,
    ControllerSessionDiagnostics,
    ControllerState,
)
from .secondary_controller import SecondaryMarlinFanController
from .serial_backend import list_serial_ports as list_serial_ports
from .transport import MachineTransport
from .transport_factory import create_machine_transport

LOGGER = logging.getLogger(__name__)
_QUERY_COMMANDS = set(MANUAL_QUERY_COMMANDS)
_STREAM_G_CODES = {0, 1, 21, 90}
_STREAM_M_CODES = {3, 4, 5}
_STREAM_LETTERS = {"G", "M", "X", "Y", "F", "S"}
_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS = 6.0
_INITIAL_CONNECT_RETRY_DELAY_SECONDS = 0.2
_CONTROLLER_CONNECT_ATTEMPTS = 3
_CONTROLLER_CONNECT_DEADLINE_SECONDS = 15.0
_CONTROLLER_SYNC_QUIET_SECONDS = 0.2
_CONTROLLER_SYNC_TIMEOUT_SECONDS = 2.0
# Compatibility timing seams retained for deterministic homing race tests. The
# production defaults come from the immutable GRBL dialect policy.
_GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS = float(
    GRBL_DIALECT.homing.status_query_interval_seconds or 0.2
)
_GRBL_HOMING_TIMEOUT_SECONDS = GRBL_DIALECT.homing.timeout_floor_seconds
_JOB_COMMAND_ACK_TIMEOUT_SECONDS = 120.0
_GRBL_COORDINATE_EPSILON_MM = 0.001
_NONZERO_OUTPUT_MOTION_EPSILON_MM = 1e-9
_REALTIME_STOP_WRITE_DEADLINE_SECONDS = 0.35
# GRBL line responses carry no request identifier.  Hold a modest bounded quiet
# boundary after every completed transaction so reader/kernel scheduling jitter
# immediately behind a terminal response cannot silently donate a duplicate to
# the following command.  This is deliberately long enough to cover ordinary
# USB-reader scheduling while adding only 10 ms per acknowledged line.
_POST_TERMINAL_QUIET_SECONDS = 0.010
_MAX_ARM_TIMEOUT_SECONDS = 600
_PROGRAM_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_CONTROLLER_ACTION_REQUIRED: dict[ControllerState, str] = {
    ControllerState.DISCONNECTED: "CONNECT",
    ControllerState.OPENING: "WAIT",
    ControllerState.SYNCHRONIZING: "WAIT",
    ControllerState.READY_HOME_REQUIRED: "HOME",
    ControllerState.READY_MOTION: "NONE",
    ControllerState.JOB_RUNNING: "STOP_ONLY",
    ControllerState.STOPPING: "WAIT",
    ControllerState.RECOVERING: "WAIT",
    ControllerState.RECONNECT_REQUIRED: "RECONNECT",
    ControllerState.FAULTED: "CONNECT",
    ControllerState.SHUTTING_DOWN: "NONE",
}

_ALLOWED_CONTROLLER_STATE_TRANSITIONS: dict[
    ControllerState,
    frozenset[ControllerState],
] = {
    ControllerState.DISCONNECTED: frozenset(
        {ControllerState.OPENING, ControllerState.SHUTTING_DOWN}
    ),
    ControllerState.OPENING: frozenset(
        {
            ControllerState.SYNCHRONIZING,
            ControllerState.FAULTED,
            ControllerState.RECONNECT_REQUIRED,
            ControllerState.STOPPING,
            ControllerState.DISCONNECTED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.SYNCHRONIZING: frozenset(
        {
            ControllerState.OPENING,
            ControllerState.READY_HOME_REQUIRED,
            ControllerState.FAULTED,
            ControllerState.RECONNECT_REQUIRED,
            ControllerState.STOPPING,
            ControllerState.DISCONNECTED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.READY_HOME_REQUIRED: frozenset(
        {
            ControllerState.READY_MOTION,
            ControllerState.JOB_RUNNING,
            ControllerState.STOPPING,
            ControllerState.RECONNECT_REQUIRED,
            ControllerState.DISCONNECTED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.READY_MOTION: frozenset(
        {
            ControllerState.READY_HOME_REQUIRED,
            ControllerState.JOB_RUNNING,
            ControllerState.STOPPING,
            ControllerState.RECONNECT_REQUIRED,
            ControllerState.DISCONNECTED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.JOB_RUNNING: frozenset(
        {
            ControllerState.READY_HOME_REQUIRED,
            ControllerState.READY_MOTION,
            ControllerState.STOPPING,
            ControllerState.RECONNECT_REQUIRED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.STOPPING: frozenset(
        {
            ControllerState.RECOVERING,
            ControllerState.RECONNECT_REQUIRED,
            ControllerState.DISCONNECTED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.RECOVERING: frozenset(
        {
            ControllerState.READY_HOME_REQUIRED,
            ControllerState.RECONNECT_REQUIRED,
            ControllerState.STOPPING,
            ControllerState.DISCONNECTED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.RECONNECT_REQUIRED: frozenset(
        {
            ControllerState.OPENING,
            ControllerState.RECOVERING,
            ControllerState.STOPPING,
            ControllerState.DISCONNECTED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.FAULTED: frozenset(
        {
            ControllerState.OPENING,
            ControllerState.DISCONNECTED,
            ControllerState.SHUTTING_DOWN,
        }
    ),
    ControllerState.SHUTTING_DOWN: frozenset(),
}

_FAILURE_CONTROLLER = "controller.failure"
_FAILURE_OPEN = "controller.open_failed"
_FAILURE_SYNC = "controller.sync_failed"
_FAILURE_IDENTITY = "controller.identity_mismatch"
_FAILURE_COMMAND_TIMEOUT = "controller.command_timeout"
_FAILURE_SESSION_QUARANTINED = "controller.session_quarantined"
_FAILURE_RECONNECT_REQUIRED = "controller.reconnect_required"
_FAILURE_HOME = "controller.home_failed"
_FAILURE_COORDINATE_QUERY = "controller.coordinate_query_failed"


def _connect_failure_code(error: BaseException) -> str:
    """Return one stable bounded diagnostic code for a candidate failure."""

    detail = str(error).lower()
    if "synchroniz" in detail or "quiet interval" in detail:
        return _FAILURE_SYNC
    if "protocol could not be identified" in detail:
        return _FAILURE_IDENTITY
    if "did not acknowledge" in detail or "overall 15 second deadline" in detail:
        return _FAILURE_COMMAND_TIMEOUT
    if any(token in detail for token in ("coordinate", "workspace", "g92")):
        return _FAILURE_COORDINATE_QUERY
    return _FAILURE_OPEN


def _add_exception_note(error: BaseException, note: str) -> None:
    """Attach cleanup context on Python 3.10 as well as newer runtimes."""

    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", ()))
    notes.append(note)
    error.__notes__ = notes


def _program_line_contains_motion(line: str) -> bool:
    """Exclude strict E3-owned auxiliary instructions from G-code parsing."""

    return not line.startswith(AIR_ASSIST_DIRECTIVE_PREFIX) and contains_motion(line)


class _ControllerCommandRejected(MachineError):
    """A complete controller rejection whose response has been consumed."""


@dataclass(slots=True)
class JobStatus:
    running: bool = False
    phase: str = "idle"
    name: str = ""
    total_lines: int = 0
    completed_lines: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    program_digest: str = ""
    powered: bool = False

    def to_dict(self) -> dict[str, Any]:
        elapsed = None
        if self.started_at is not None:
            end = self.finished_at or time.time()
            elapsed = max(0.0, end - self.started_at)
        return {
            "running": self.running,
            "phase": self.phase,
            "name": self.name,
            "total_lines": self.total_lines,
            "completed_lines": self.completed_lines,
            "progress": 0.0 if self.total_lines == 0 else self.completed_lines / self.total_lines,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": elapsed,
            "error": self.error,
            "program_digest": self.program_digest or None,
            "powered": self.powered,
        }


@dataclass(frozen=True, slots=True, eq=False)
class _JobRunContext:
    identity: int
    session: ControllerSession
    stop_event: threading.Event
    status: JobStatus
    air_assist_commands: AirAssistCommands | None
    air_assist_off_commands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidatedProgram:
    lines: tuple[str, ...]
    digest: str
    requires_laser_authorization: bool
    requires_motion: bool
    safety_profile: tuple[Any, ...]
    air_assist_commands: AirAssistCommands | None = None
    guarded_output_polygon_mm: ConvexPolygon | None = None


class MachineService:
    ARM_PHRASE = "ENABLE LASER CONTROL"

    @property
    def _protocol(self) -> str:
        """Compatibility view of the selected or resolved dialect ID."""

        return self._protocol_id

    @_protocol.setter
    def _protocol(self, value: str) -> None:
        self._protocol_id = value
        try:
            self._dialect = CONTROLLER_DIALECT_REGISTRY.get(value)
        except KeyError:
            self._dialect = None

    def _require_resolved_dialect(self) -> ControllerDialect:
        dialect = self._dialect
        if dialect is None:
            raise MachineError("Controller protocol has not been resolved")
        return dialect

    def _resolved_air_assist_commands(self) -> AirAssistCommands | None:
        settings = self.settings.air_assist
        if type(settings) is not AirAssistSettings:
            raise SafetyError("machine.air_assist must be typed AirAssistSettings")
        try:
            validate_air_assist_settings(
                settings,
                protocol=self.settings.protocol,
                primary_port=self.settings.port,
            )
            commands = resolve_air_assist_commands(
                settings,
                protocol=self.settings.protocol,
            )
        except (TypeError, ValueError) as exc:
            raise SafetyError(str(exc)) from exc
        if commands is not None and commands.target is AirAssistTarget.PRIMARY:
            self._air_assist_off_commands = commands.off_commands
        else:
            self._air_assist_off_commands = ()
        return commands

    def _require_air_assist_dialect_compatibility(self) -> None:
        commands = self._resolved_air_assist_commands()
        dialect = self._require_resolved_dialect()
        if (
            commands is not None
            and commands.target is AirAssistTarget.PRIMARY
            and commands.protocol != dialect.id
        ):
            raise SafetyError(
                "Configured air-assist output does not match the connected controller dialect"
            )

    def _air_assist_command_kind(
        self,
        line: str,
        *,
        commands: AirAssistCommands | None = None,
    ) -> str | None:
        if commands is None:
            commands = self._resolved_air_assist_commands()
        if commands is None:
            if line.strip().startswith(AIR_ASSIST_DIRECTIVE_PREFIX):
                raise SafetyError(
                    "E3 air-assist instruction has no resolved machine mapping"
                )
            return None
        try:
            return commands.kind_for_program_line(line)
        except ValueError as exc:
            raise SafetyError(str(exc)) from exc

    def _best_effort_fail_off(
        self,
        transport: MachineTransport,
        *,
        context: str,
    ) -> None:
        """Write laser-off first, then the cached configured air-off commands."""

        with self._transport_write_lock:
            self._best_effort_fail_off_locked(transport, context=context)

    def _best_effort_fail_off_locked(
        self,
        transport: MachineTransport,
        *,
        context: str,
    ) -> None:
        """Write fail-off commands while the caller owns the transport write gate."""

        off_commands = tuple(
            dict.fromkeys(
                (
                    *self._active_job_air_assist_off_commands,
                    *self._air_assist_off_commands,
                )
            )
        )
        for command in ("M5", *off_commands):
            try:
                transport.write_line(command)
                self._append_log("TX", f"{command} ({context})")
            except Exception as exc:
                self._append_log(
                    "ERROR",
                    f"{context} command {command!r} failed: {exc}",
                )

    def _start_bounded_stop_cleanup(
        self,
        session: ControllerSession,
        *,
        context: str,
        deadline: float,
        emergency_line: str | None = None,
        emergency_success_log: str | None = None,
        emergency_failure_label: str | None = None,
    ) -> None:
        """Order fail-off behind an in-flight write without blocking STOP forever."""

        finished = threading.Event()

        def cleanup() -> None:
            try:
                with self._transport_write_lock:
                    if emergency_line is not None:
                        try:
                            session.transport.write_line(emergency_line)
                        except Exception as exc:
                            self._append_log(
                                "ERROR",
                                f"{emergency_failure_label or 'Emergency stop'} failed: {exc}",
                            )
                        else:
                            self._append_log(
                                "TX",
                                emergency_success_log or emergency_line,
                            )
                    self._best_effort_fail_off_locked(
                        session.transport,
                        context=context,
                    )
            finally:
                try:
                    session.transport.close()
                except Exception as exc:
                    session.diagnostics.record_failure(f"STOP close failed: {exc}")
                finished.set()

        try:
            threading.Thread(
                target=cleanup,
                name=f"controller-stop-cleanup-{session.generation}",
                daemon=True,
            ).start()
        except Exception as exc:
            self._append_log(
                "ERROR",
                f"{context} worker could not start: {exc}",
            )
            remaining = max(0.0, deadline - time.monotonic())
            acquired = self._transport_write_lock.acquire(timeout=remaining)
            try:
                if acquired:
                    self._best_effort_fail_off_locked(
                        session.transport,
                        context=f"{context} synchronous fallback",
                    )
            finally:
                if acquired:
                    self._transport_write_lock.release()
                try:
                    session.transport.close()
                except Exception as close_error:
                    session.diagnostics.record_failure(
                        f"STOP fallback close failed: {close_error}"
                    )
            return
        remaining = max(0.0, deadline - time.monotonic())
        if not finished.wait(remaining):
            self._append_log(
                "ERROR",
                f"{context} M5/close remains queued behind an in-flight write",
            )
            LOGGER.error(
                "primary controller STOP cleanup pending generation=%d endpoint=%s "
                "protocol=%s code=STOP_CLEANUP_TIMEOUT detail=%s",
                session.generation,
                session.resolved_endpoint,
                session.dialect.id,
                f"{context} M5/close queued behind in-flight write",
            )

    def _secondary_controller_for(
        self,
        commands: AirAssistCommands | None,
    ) -> SecondaryMarlinFanController | None:
        if commands is None or commands.target is not AirAssistTarget.PI_SECONDARY:
            return None
        controller = self._secondary_air_assist
        if controller is None:
            raise SafetyError(
                "The Pi-owned secondary Marlin Air Assist controller is unavailable"
            )
        if controller.binding != commands:
            raise SafetyError(
                "The Pi-owned secondary Air Assist controller does not match the "
                "immutable job mapping"
            )
        return controller

    def _prepare_secondary_air_assist_for_start(
        self,
        program: ValidatedProgram,
    ) -> None:
        controller = self._secondary_controller_for(program.air_assist_commands)
        if controller is None:
            return
        try:
            controller.ensure_off()
        except Exception as exc:
            raise SafetyError(
                "Pi-owned secondary Air Assist could not establish acknowledged OFF"
            ) from exc
        self._append_log("AUX", "M106 S0 (acknowledged pre-start OFF)")

    @contextmanager
    def _secondary_job_write_guard(self):
        """Linearize only an auxiliary write, never its acknowledgement wait."""

        with self._secondary_write_gate:
            if self._current_job_stop_event().is_set():
                raise MachineError("Job stopped")
            yield

    def _raise_if_secondary_faulted(self) -> None:
        context = self._current_job_run_context()
        commands = (
            context.air_assist_commands
            if context is not None
            else self._active_job_air_assist_commands
        )
        controller = self._secondary_controller_for(commands)
        if controller is None:
            return
        controller.raise_if_faulted()

    def _execute_secondary_air_assist_instruction(self, line: str) -> bool:
        context = self._current_job_run_context()
        commands = (
            context.air_assist_commands
            if context is not None
            else self._active_job_air_assist_commands
        )
        if commands is None or commands.target is not AirAssistTarget.PI_SECONDARY:
            return False
        kind = self._air_assist_command_kind(line, commands=commands)
        if kind is None:
            return False
        controller = self._secondary_controller_for(commands)
        assert controller is not None
        self._check_line_safety(line, job_execution=True)
        enabled = kind == "on"
        controller.set_enabled(
            enabled,
            mapping_digest=commands.mapping_digest,
            write_guard=self._secondary_job_write_guard,
        )
        physical = commands.on_commands if enabled else commands.off_commands
        self._append_log("AUX", f"{' / '.join(physical)} (acknowledged)")
        return True

    def _best_effort_secondary_off(
        self,
        *,
        context: str,
        commands: AirAssistCommands | None = None,
    ) -> None:
        if commands is None:
            commands = self._active_job_air_assist_commands
        if commands is None:
            try:
                commands = self._resolved_air_assist_commands()
            except Exception:
                commands = None
        try:
            controller = self._secondary_controller_for(commands)
        except Exception as exc:
            self._append_log(
                "ERROR",
                f"{context} secondary Air Assist OFF unavailable: {exc}",
            )
            return
        if controller is None:
            return
        try:
            if controller.best_effort_off():
                self._append_log("AUX", f"M106 S0 ({context})")
            else:
                self._append_log(
                    "ERROR",
                    f"{context} secondary command 'M106 S0' was not acknowledged",
                )
        except Exception as exc:
            self._append_log(
                "ERROR",
                f"{context} secondary command 'M106 S0' failed: {exc}",
            )

    def _queue_secondary_off(self, *, context: str) -> None:
        commands = self._active_job_air_assist_commands
        if commands is None or commands.target is not AirAssistTarget.PI_SECONDARY:
            return

        def cleanup() -> None:
            try:
                self._best_effort_secondary_off(context=context)
            finally:
                with self._secondary_cleanup_lock:
                    if self._secondary_cleanup_thread is threading.current_thread():
                        self._secondary_cleanup_thread = None

        with self._secondary_cleanup_lock:
            current = self._secondary_cleanup_thread
            if current is not None and current.is_alive():
                return
            thread = threading.Thread(
                target=cleanup,
                name="secondary-air-assist-off",
                daemon=True,
            )
            self._secondary_cleanup_thread = thread
            try:
                thread.start()
            except Exception:
                self._secondary_cleanup_thread = None
                raise

    def _uses_grbl_coordinate_state(self) -> bool:
        dialect = self._dialect
        return bool(
            self.settings.backend == "serial"
            and dialect is not None
            and dialect.coordinate_state_query_commands
        )

    def __init__(
        self,
        settings: MachineSettings,
        laser_settings: LaserSettings,
        hardware_enabled: bool = False,
        laser_lockout: bool = False,
        *,
        secondary_air_assist: SecondaryMarlinFanController | None = None,
    ):
        if type(laser_lockout) is not bool:
            raise TypeError("laser_lockout must be an exact boolean")
        self.settings = settings
        self.laser_settings = laser_settings
        self.hardware_enabled = hardware_enabled
        self.laser_lockout = laser_lockout
        if (
            secondary_air_assist is not None
            and not isinstance(
                secondary_air_assist,
                SecondaryMarlinFanController,
            )
        ):
            raise TypeError(
                "secondary_air_assist must be SecondaryMarlinFanController or None"
            )
        self._secondary_air_assist = secondary_air_assist
        self._controller_state = ControllerState.DISCONNECTED
        self._controller_state_revision = 0
        self._controller_session_generation = 0
        self._session: ControllerSession | None = None
        self._candidate_session: ControllerSession | None = None
        self._candidate_connect_deadline: float | None = None
        self._last_session_diagnostics: ControllerSessionDiagnostics | None = None
        self._last_controller_failure: str | None = None
        self._last_controller_failure_code: str | None = None
        self._last_controller_failed_command: str | None = None
        self._last_controller_stop_at: float | None = None
        self._last_controller_recovery_at: float | None = None
        self._recovery_thread: threading.Thread | None = None
        self._recovery_stop_epoch: int | None = None
        self._recovery_endpoint: str | None = None
        self._recovery_protocol: str | None = None
        self._recovery_baudrate: int | None = None
        self._shutdown_requested = False
        self._last_replacement_request_epoch: int | None = None
        self._last_replacement_session_generation: int | None = None
        self._transport: MachineTransport | None = None
        # Keep the last known valid OFF mapping available even if mutable
        # settings later become invalid during an emergency cleanup path.
        self._air_assist_off_commands: tuple[str, ...] = ()
        self._active_job_air_assist_off_commands: tuple[str, ...] = ()
        self._active_job_air_assist_commands: AirAssistCommands | None = None
        self._protocol = settings.protocol
        self._active_port = settings.port
        self._active_baudrate = settings.baudrate
        self._connected = False
        self._connecting = False
        self._trusted_controller_session_established = False
        self._controller_reconnect_required = False
        self._coordinate_reference_ready = False
        self._coordinate_reference_session_generation: int | None = None
        self._coordinate_state_reference: dict[str, Any] | None = None
        # Incremental UI jogging is translated to absolute moves.  This
        # position is published only by Home / park or a completed jog and is
        # invalidated whenever another operation can change machine position.
        self._jog_position_mm: tuple[float, float] | None = None
        self._armed_until = 0.0
        self._armed_until_monotonic = 0.0
        self._armed_program_digest: str | None = None
        self._lock = threading.RLock()
        # Own each command/ack exchange, and allow a multi-command operation
        # to retain that ownership through its complete controller sequence.
        # Emergency stop intentionally bypasses this lock.
        self._command_lock = threading.RLock()
        # This tiny gate orders software STOP after any in-flight job write.
        # It is never held while waiting for a controller acknowledgement.
        self._transport_write_lock = threading.RLock()
        # STOP never waits for a secondary acknowledgement.  This gate spans
        # only the actual auxiliary serial write so STOP can order an already
        # authorized ON before its independently queued OFF cleanup.
        self._secondary_write_gate = threading.Lock()
        self._secondary_cleanup_lock = threading.Lock()
        self._secondary_cleanup_thread: threading.Thread | None = None
        self._job = JobStatus()
        self._job_thread: threading.Thread | None = None
        self._job_stop = threading.Event()
        self._active_job_context: _JobRunContext | None = None
        self._job_context = threading.local()
        self._job_identity = 0
        self._stop_epoch_lock = threading.Lock()
        self._stop_epoch = 0
        self._authorization_epoch = 0
        self._operation_context = threading.local()
        self._job_laser_authorized = False
        self._last_successful_job: dict[str, Any] | None = None
        self._log: deque[str] = deque(maxlen=200)
        try:
            configured_air = resolve_air_assist_commands(
                self.settings.air_assist,
                protocol=self.settings.protocol,
            )
        except (AttributeError, TypeError, ValueError):
            configured_air = None
        if (
            configured_air is not None
            and configured_air.target is AirAssistTarget.PRIMARY
        ):
            self._air_assist_off_commands = configured_air.off_commands

    def _set_controller_state_locked(
        self,
        state: ControllerState,
        *,
        session: ControllerSession | None = None,
        failure: object | None = None,
        failure_code: str | None = None,
        failed_command: str | None = None,
        force_terminal: bool = False,
    ) -> None:
        """Publish one coherent controller state and its compatibility views."""

        if not isinstance(state, ControllerState):
            raise TypeError("controller state must be ControllerState")
        previous = self._controller_state
        if (
            previous is not state
            and state not in _ALLOWED_CONTROLLER_STATE_TRANSITIONS[previous]
            and not (
                force_terminal
                and state
                in {ControllerState.DISCONNECTED, ControllerState.SHUTTING_DOWN}
            )
        ):
            raise MachineError(
                "Invalid primary-controller state transition: "
                f"{previous.value} -> {state.value}"
            )
        if previous is not state:
            self._controller_state = state
            self._controller_state_revision += 1
            target_for_log = session or self._session or self._candidate_session
            LOGGER.info(
                "primary controller transition previous=%s state=%s revision=%d "
                "generation=%s endpoint=%s protocol=%s reason=%s",
                previous.value,
                state.value,
                self._controller_state_revision,
                (
                    self._controller_session_generation
                    if target_for_log is None
                    else target_for_log.generation
                ),
                "-" if target_for_log is None else target_for_log.resolved_endpoint,
                "-" if target_for_log is None else target_for_log.dialect.id,
                "-" if failure is None else str(failure)[:240],
            )
        target = session or self._session or self._candidate_session
        if target is not None:
            target.diagnostics.set_state(state)
            self._last_session_diagnostics = target.diagnostics
            if failure is not None:
                target.diagnostics.record_failure(
                    failure,
                    code=failure_code or _FAILURE_CONTROLLER,
                    command=failed_command,
                )
        if failure is not None:
            self._last_controller_failure = str(failure)[:500]
            self._last_controller_failure_code = (
                failure_code
                or (
                    _FAILURE_OPEN
                    if state is ControllerState.FAULTED
                    else _FAILURE_SESSION_QUARANTINED
                )
            )
            self._last_controller_failed_command = failed_command
        self._connected = state in CONNECTED_CONTROLLER_STATES
        self._connecting = state in CONNECTING_CONTROLLER_STATES
        self._controller_reconnect_required = (
            state is ControllerState.RECONNECT_REQUIRED
        )

    def _next_controller_session_generation_locked(self) -> int:
        self._controller_session_generation += 1
        return self._controller_session_generation

    @staticmethod
    def _transport_endpoint(
        transport: MachineTransport,
        attribute: str,
        fallback: str,
    ) -> str:
        try:
            value = getattr(transport, attribute)
            if callable(value):
                value = value()
        except Exception:
            value = None
        if type(value) is str and value.strip():
            return value.strip()
        return fallback

    def _make_controller_session(
        self,
        *,
        transport: MachineTransport,
        dialect: ControllerDialect,
        endpoint: str,
        baudrate: int,
        state: ControllerState,
    ) -> ControllerSession:
        with self._lock:
            generation = self._next_controller_session_generation_locked()
        diagnostics = ControllerSessionDiagnostics(state)
        return ControllerSession(
            generation=generation,
            transport=transport,
            dialect=dialect,
            configured_endpoint=self._transport_endpoint(
                transport,
                "configured_endpoint",
                endpoint,
            ),
            resolved_endpoint=self._transport_endpoint(
                transport,
                "resolved_endpoint",
                endpoint,
            ),
            baudrate=baudrate,
            created_at=time.time(),
            created_monotonic=time.monotonic(),
            diagnostics=diagnostics,
        )

    @staticmethod
    def _same_controller_session(
        left: ControllerSession | None,
        right: ControllerSession | None,
    ) -> bool:
        return bool(left is not None and left.matches(right))

    def _adopt_legacy_test_session_locked(self) -> ControllerSession | None:
        """Bridge old tests that directly install the former private fields.

        Product code never mutates those fields directly. Keeping this narrow
        adapter lets focused legacy safety tests continue to construct a
        transport without weakening publication of real connection candidates.
        """

        if self._session is not None:
            return self._session
        transport = self._transport
        if (
            transport is None
            or not self._connected
            or self._controller_reconnect_required
            or getattr(
                transport,
                "test_only_allow_legacy_input_synchronization",
                False,
            )
            is not True
        ):
            return None
        dialect = self._dialect
        if dialect is None:
            return None
        session = self._make_controller_session(
            transport=transport,
            dialect=dialect,
            endpoint=self._active_port,
            baudrate=self._active_baudrate,
            state=(
                ControllerState.READY_MOTION
                if self._coordinate_reference_ready
                else ControllerState.READY_HOME_REQUIRED
            ),
        )
        drain = getattr(transport, "drain", None)
        discarded = drain() if callable(drain) else []
        session.diagnostics.set_synchronization(
            {
                "method": "legacy-test-adoption",
                "discarded_lines": len(discarded),
            }
        )
        self._session = session
        if self._controller_state in {
            ControllerState.DISCONNECTED,
            ControllerState.FAULTED,
        }:
            self._set_controller_state_locked(ControllerState.OPENING, session=session)
            self._set_controller_state_locked(
                ControllerState.SYNCHRONIZING,
                session=session,
            )
        self._set_controller_state_locked(
            ControllerState.READY_HOME_REQUIRED,
            session=session,
        )
        if self._coordinate_reference_ready:
            self._coordinate_reference_session_generation = session.generation
            self._set_controller_state_locked(
                ControllerState.READY_MOTION,
                session=session,
            )
        return session

    def _current_job_run_context(self) -> _JobRunContext | None:
        context = getattr(self._job_context, "current", None)
        if isinstance(context, _JobRunContext):
            return context
        return self._active_job_context

    def _current_job_stop_event(self) -> threading.Event:
        context = self._current_job_run_context()
        return self._job_stop if context is None else context.stop_event

    @property
    def controller_state(self) -> ControllerState:
        with self._lock:
            return self._controller_state

    @property
    def connected(self) -> bool:
        with self._lock:
            if self._session is None and self._transport is not None and self._connected:
                # Compatibility only for older tests that directly install
                # private fields instead of calling connect().
                return not self._connecting and not self._controller_reconnect_required
            return self._controller_state in CONNECTED_CONTROLLER_STATES

    @property
    def armed(self) -> bool:
        return self.connected and time.monotonic() < self._armed_until_monotonic

    def _clear_arm_authorization(self) -> None:
        self._armed_until = 0.0
        self._armed_until_monotonic = 0.0
        self._armed_program_digest = None

    def _invalidate_coordinate_reference(self) -> None:
        self._coordinate_reference_ready = False
        self._coordinate_reference_session_generation = None
        self._coordinate_state_reference = None
        self._jog_position_mm = None

    def _mark_controller_command_state_untrusted(
        self,
        session: ControllerSession | None = None,
        *,
        reason: object = "controller exchange became ambiguous",
        recover: bool = True,
        failure_code: str = _FAILURE_SESSION_QUARANTINED,
        failed_command: str | None = None,
    ) -> None:
        """Quarantine only the exact session whose response position is unknown."""

        with self._lock:
            if session is None:
                session = self._adopt_legacy_test_session_locked()
            if session is None:
                self._invalidate_coordinate_reference()
                return
            session.diagnostics.record_failure(
                reason,
                code=failure_code,
                command=failed_command,
            )
            if not self._same_controller_session(self._session, session):
                return
            self._session = None
            self._transport = None
            self._invalidate_coordinate_reference()
            self._set_controller_state_locked(
                ControllerState.RECONNECT_REQUIRED,
                session=session,
                failure=reason,
                failure_code=failure_code,
                failed_command=failed_command,
            )
            transcript = session.diagnostics.snapshot()["transcript"][-8:]
            LOGGER.warning(
                "primary controller session quarantined generation=%d endpoint=%s "
                "protocol=%s code=%s command=%s detail=%s transcript=%s",
                session.generation,
                session.resolved_endpoint,
                session.dialect.id,
                failure_code,
                failed_command or "-",
                str(reason)[:500],
                " | ".join(str(item) for item in transcript)[:1200],
            )
        with self._stop_epoch_lock:
            self._authorization_epoch += 1
            self._clear_arm_authorization()
            self._job_laser_authorized = False
            context = self._active_job_context
            if context is not None and context.session.matches(session):
                context.stop_event.set()
            recovery_epoch = self._stop_epoch
        self._best_effort_fail_off(
            session.transport,
            context="uncertain-session quarantine",
        )
        try:
            session.transport.close()
        except Exception as exc:
            session.diagnostics.record_failure(f"quarantine close failed: {exc}")
        if recover and callable(getattr(session.transport, "synchronize_input", None)):
            self._schedule_controller_recovery(recovery_epoch, session=session)

    def operation_generation(self) -> int:
        """Capture the STOP generation when an operation is requested.

        Queue owners should retain this value and bind it in
        :meth:`operation_scope` inside the eventual worker. That makes a STOP
        cancel work which was already queued but had not begun executing.
        """

        with self._stop_epoch_lock:
            return self._stop_epoch

    @contextmanager
    def operation_scope(self, generation: int):
        """Bind a request-time STOP generation to machine calls on this thread."""

        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError("Machine operation generation must be an integer")
        previous = getattr(self._operation_context, "stop_epoch", None)
        self._operation_context.stop_epoch = generation
        try:
            yield
        finally:
            if previous is None:
                try:
                    del self._operation_context.stop_epoch
                except AttributeError:
                    pass
            else:
                self._operation_context.stop_epoch = previous

    def _operation_stop_epoch(self) -> int:
        bound = getattr(self._operation_context, "stop_epoch", None)
        if bound is not None:
            return int(bound)
        return self.operation_generation()

    def _require_operation_generation_current(
        self,
        generation: int,
        *,
        operation: str,
    ) -> None:
        with self._stop_epoch_lock:
            if self._stop_epoch != generation:
                raise MachineError(f"{operation} was cancelled by software STOP")

    def _require_expected_session_generation_locked(
        self,
        expected_session_generation: int | None,
        *,
        operation: str,
    ) -> None:
        """Compare one caller-observed generation at the mutation point.

        The caller must own ``_command_lock`` and ``_lock``.  Comparing the
        monotonic service generation (rather than only ``_session``) also works
        while an exact failed session is quarantined and no active session is
        published.
        """

        if expected_session_generation is None:
            return
        if (
            isinstance(expected_session_generation, bool)
            or not isinstance(expected_session_generation, int)
            or expected_session_generation < 0
        ):
            raise TypeError("Expected controller session generation must be a non-negative integer")
        if self._controller_session_generation != expected_session_generation:
            raise MachineError(
                f"{operation} expected controller session generation "
                f"{expected_session_generation}, but current generation is "
                f"{self._controller_session_generation}"
            )

    def _append_log(self, direction: str, line: str) -> None:
        entry = f"{time.strftime('%H:%M:%S')} {direction} {line}"
        self._log.append(entry)
        LOGGER.debug("machine %s %s", direction, line)

    def connect(
        self,
        port: str | None = None,
        protocol: str | None = None,
        baudrate: int | None = None,
    ) -> dict[str, Any]:
        """Establish one fully synchronized controller session.

        A newly opened transport remains a private candidate until every
        non-motion bring-up transaction succeeds. No ordinary command can
        resolve that transport through ``_require_connection`` before the
        final publication compare-and-set.
        """

        self._require_safety_configuration()
        connect_stop_epoch = self._operation_stop_epoch()
        with self._command_lock:
            with self._lock:
                self._adopt_legacy_test_session_locked()
                if self._shutdown_requested:
                    raise MachineError("Controller service is shutting down")
                if (
                    self._session is not None
                    and self._controller_state in CONNECTED_CONTROLLER_STATES
                ):
                    return self.status()
                recovery = self._controller_state in {
                    ControllerState.RECONNECT_REQUIRED,
                    ControllerState.RECOVERING,
                    ControllerState.STOPPING,
                }
            selected_protocol = self.settings.protocol if protocol is None else protocol
            if type(selected_protocol) is not str:
                raise MachineError("Protocol must be auto, grbl, or marlin")
            selected = selected_protocol.lower()
            if selected not in {"auto", *CONTROLLER_DIALECT_REGISTRY.ids}:
                raise MachineError("Protocol must be auto, grbl, or marlin")
            active_port = self.settings.port if port is None else port
            if type(active_port) is not str or not active_port.strip():
                raise MachineError("Controller port must be a non-empty string")
            active_baudrate = self.settings.baudrate if baudrate is None else baudrate
            if type(active_baudrate) is not int or active_baudrate <= 0:
                raise MachineError("Controller baud rate must be a positive integer")
            return self._connect_locked(
                active_port=active_port,
                selected_protocol=selected,
                active_baudrate=active_baudrate,
                expected_stop_epoch=connect_stop_epoch,
                recovery=recovery,
            )

    def _connect_locked(
        self,
        *,
        active_port: str,
        selected_protocol: str,
        active_baudrate: int,
        expected_stop_epoch: int,
        recovery: bool,
    ) -> dict[str, Any]:
        if self.hardware_enabled is not True:
            raise SafetyError(
                "The current process was not granted hardware authority "
                "and cannot open the configured controller."
            )
        started = time.monotonic()
        deadline = started + _CONTROLLER_CONNECT_DEADLINE_SECONDS
        with self._lock:
            self._candidate_connect_deadline = deadline
        last_error: BaseException | None = None
        attempted_transport_ids: set[int] = set()
        for attempt in range(1, _CONTROLLER_CONNECT_ATTEMPTS + 1):
            self._raise_if_connection_cancelled(expected_stop_epoch)
            if time.monotonic() >= deadline:
                last_error = MachineError(
                    "Controller connection exceeded the overall 15 second deadline"
                )
                break
            LOGGER.info(
                "primary controller connect attempt=%d/%d endpoint=%s protocol=%s "
                "baudrate=%d recovery=%s",
                attempt,
                _CONTROLLER_CONNECT_ATTEMPTS,
                active_port,
                selected_protocol,
                active_baudrate,
                recovery,
            )
            with self._lock:
                self._set_controller_state_locked(
                    ControllerState.RECOVERING if recovery else ControllerState.OPENING
                )
            transport = create_machine_transport(
                self.settings.backend,
                active_port,
                active_baudrate,
            )
            if id(transport) in attempted_transport_ids:
                # A retry is useful only with a genuinely new input stream.
                # Reusing an object that may still contain a late terminal line
                # would turn a fresh-session retry into same-session ambiguity.
                break
            attempted_transport_ids.add(id(transport))
            provisional_dialect = (
                GRBL_DIALECT
                if selected_protocol == "auto"
                else CONTROLLER_DIALECT_REGISTRY.get(selected_protocol)
            )
            candidate = self._make_controller_session(
                transport=transport,
                dialect=provisional_dialect,
                endpoint=active_port,
                baudrate=active_baudrate,
                state=(
                    ControllerState.RECOVERING
                    if recovery
                    else ControllerState.OPENING
                ),
            )
            with self._lock:
                self._candidate_session = candidate
                self._last_session_diagnostics = candidate.diagnostics
            try:
                transport.open()
                self._raise_if_connection_cancelled(expected_stop_epoch)
                with self._lock:
                    if not recovery:
                        self._set_controller_state_locked(
                            ControllerState.SYNCHRONIZING,
                            session=candidate,
                        )
                self._wait_for_controller_startup(
                    expected_stop_epoch=expected_stop_epoch
                )
                dialect_id = self._identify_protocol(
                    expected_stop_epoch=expected_stop_epoch,
                    selected_protocol=selected_protocol,
                )
                dialect = CONTROLLER_DIALECT_REGISTRY.get(dialect_id)
                candidate = replace(candidate, dialect=dialect)
                with self._lock:
                    if (
                        self._candidate_session is None
                        or self._candidate_session.generation != candidate.generation
                    ):
                        raise MachineError(
                            "Controller connection was cancelled before synchronization"
                        )
                    self._candidate_session = candidate
                self._validate_candidate_air_assist(dialect)
                if self.settings.backend == "serial" and dialect is GRBL_DIALECT:
                    self._normalize_and_release_grbl_after_connect(
                        session=candidate,
                        expected_stop_epoch=expected_stop_epoch,
                    )
                    self._verify_grbl_candidate_alignment(
                        candidate,
                        expected_stop_epoch=expected_stop_epoch,
                    )
                elif self.settings.backend == "serial" and dialect is MARLIN_DIALECT:
                    self._send_candidate_command(
                        candidate,
                        MARLIN_DIALECT.laser_off_command,
                        timeout=max(
                            _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS,
                            self.settings.read_timeout,
                        ),
                        expected_stop_epoch=expected_stop_epoch,
                    )
                air_assist = resolve_air_assist_commands(
                    self.settings.air_assist,
                    protocol=dialect.id,
                )
                if (
                    air_assist is not None
                    and air_assist.target is AirAssistTarget.PRIMARY
                ):
                    for command in air_assist.off_commands:
                        self._send_candidate_command(
                            candidate,
                            command,
                            timeout=max(
                                _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS,
                                self.settings.read_timeout,
                            ),
                            expected_stop_epoch=expected_stop_epoch,
                        )
                self._raise_if_connection_cancelled(expected_stop_epoch)
                with self._lock:
                    if (
                        self._candidate_session is None
                        or self._candidate_session.generation != candidate.generation
                        or self._shutdown_requested
                    ):
                        raise MachineError(
                            "Controller connection was cancelled before publication"
                        )
                    self._session = candidate
                    self._candidate_session = None
                    self._candidate_connect_deadline = None
                    self._transport = candidate.transport
                    self._protocol = dialect.id
                    self._active_port = active_port
                    self._active_baudrate = active_baudrate
                    self._invalidate_coordinate_reference()
                    self._clear_arm_authorization()
                    self._trusted_controller_session_established = True
                    if recovery:
                        self._last_controller_recovery_at = time.time()
                    self._set_controller_state_locked(
                        ControllerState.READY_HOME_REQUIRED,
                        session=candidate,
                    )
                self._resolved_air_assist_commands()
                self._append_log(
                    "INFO",
                    f"connected session {candidate.generation} using {dialect.id}",
                )
                LOGGER.info(
                    "primary controller session ready generation=%d endpoint=%s "
                    "protocol=%s recovery=%s action_required=HOME",
                    candidate.generation,
                    candidate.resolved_endpoint,
                    dialect.id,
                    recovery,
                )
                return self.status()
            except BaseException as exc:
                last_error = exc
                candidate.diagnostics.record_failure(
                    exc,
                    code=_connect_failure_code(exc),
                )
                attempt_transcript = candidate.diagnostics.snapshot()["transcript"][-8:]
                LOGGER.warning(
                    "primary controller connect attempt failed attempt=%d/%d "
                    "generation=%d endpoint=%s protocol=%s detail=%s transcript=%s",
                    attempt,
                    _CONTROLLER_CONNECT_ATTEMPTS,
                    candidate.generation,
                    candidate.resolved_endpoint,
                    candidate.dialect.id,
                    str(exc)[:500],
                    " | ".join(str(item) for item in attempt_transcript)[:1200],
                )
                with self._lock:
                    if (
                        self._candidate_session is not None
                        and self._candidate_session.generation == candidate.generation
                    ):
                        self._candidate_session = None
                self._best_effort_fail_off(
                    candidate.transport,
                    context=f"connection attempt {attempt} cleanup",
                )
                try:
                    candidate.transport.close()
                except Exception as close_error:
                    candidate.diagnostics.record_failure(
                        f"connection cleanup close failed: {close_error}"
                    )
                self._active_job_air_assist_off_commands = ()
                self._best_effort_secondary_off(context="connection cleanup")
                self._raise_if_connection_cancelled(expected_stop_epoch)
                if isinstance(exc, (SafetyError, _ControllerCommandRejected)) or (
                    "authentication_failed" in str(exc).lower()
                ):
                    break
                if attempt >= _CONTROLLER_CONNECT_ATTEMPTS:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= _INITIAL_CONNECT_RETRY_DELAY_SECONDS:
                    break
                self._append_log(
                    "INFO",
                    "Controller session attempt failed; opening a fresh transport",
                )
                time.sleep(_INITIAL_CONNECT_RETRY_DELAY_SECONDS)
        assert last_error is not None
        with self._lock:
            self._session = None
            self._transport = None
            self._candidate_connect_deadline = None
            self._invalidate_coordinate_reference()
            self._clear_arm_authorization()
            self._set_controller_state_locked(
                (
                    ControllerState.RECONNECT_REQUIRED
                    if recovery or self._trusted_controller_session_established
                    else ControllerState.FAULTED
                ),
                failure=last_error,
                failure_code=_connect_failure_code(last_error),
            )
            LOGGER.error(
                "primary controller connect failed endpoint=%s protocol=%s "
                "recovery=%s code=%s detail=%s",
                active_port,
                selected_protocol,
                recovery,
                _connect_failure_code(last_error),
                str(last_error)[:500],
            )
        raise last_error

    def _raise_if_connection_cancelled(self, expected_stop_epoch: int) -> None:
        with self._stop_epoch_lock:
            cancelled = self._stop_epoch != expected_stop_epoch
        with self._lock:
            shutting_down = self._shutdown_requested
        if cancelled:
            raise MachineError("Controller connection was cancelled by software STOP")
        if shutting_down:
            raise MachineError("Controller connection was cancelled by shutdown")

    def _remaining_candidate_timeout(self, requested: float) -> float:
        with self._lock:
            deadline = self._candidate_connect_deadline
        if deadline is None:
            return requested
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise MachineError(
                "Controller connection exceeded the overall 15 second deadline"
            )
        return min(requested, remaining)

    def ensure_connected(self) -> dict[str, Any]:
        """Return a trusted connection, reconnecting when necessary."""

        requested_stop_epoch = self._operation_stop_epoch()
        with self._stop_epoch_lock:
            if self._stop_epoch != requested_stop_epoch:
                raise MachineError("Controller connection was cancelled by software STOP")
        # Keep the observe-disconnect-connect sequence under the same ownership
        # used by controller operations. Otherwise two queued operations can
        # both observe one untrusted connection and the later caller can tear
        # down the fresh connection established by the first caller.
        with self._command_lock:
            with self._lock:
                self._adopt_legacy_test_session_locked()
                connected = (
                    self._session is not None
                    and self._controller_state in CONNECTED_CONTROLLER_STATES
                )
            if connected:
                return self.status()
            return self.connect()

    def _wait_for_controller_startup(
        self,
        *,
        expected_stop_epoch: int,
    ) -> list[str]:
        with self._lock:
            session = self._candidate_session
        if session is None:
            raise MachineError("Controller connection candidate is unavailable")
        startup_delay = max(0.0, self.settings.controller_startup_delay)
        if startup_delay:
            bounded_delay = self._remaining_candidate_timeout(startup_delay)
            time.sleep(bounded_delay)
            if bounded_delay < startup_delay:
                raise MachineError(
                    "Controller startup settling exceeded the overall connection deadline"
                )
        self._raise_if_connection_cancelled(expected_stop_epoch)
        synchronize = getattr(session.transport, "synchronize_input", None)
        if callable(synchronize):
            synchronization_timeout = self._remaining_candidate_timeout(
                _CONTROLLER_SYNC_TIMEOUT_SECONDS
            )
            if synchronization_timeout < _CONTROLLER_SYNC_QUIET_SECONDS:
                raise MachineError(
                    "Controller connection deadline cannot accommodate the required quiet interval"
                )
            try:
                evidence = synchronize(
                    quiet_interval=_CONTROLLER_SYNC_QUIET_SECONDS,
                    timeout=synchronization_timeout,
                )
            except TypeError:
                # Compatibility for narrow scripted fakes predating the keyword
                # API. Product transports implement the keyword-only contract.
                evidence = synchronize()
            session.diagnostics.set_synchronization(evidence)
            startup: list[str] = []
        else:
            if (
                getattr(
                    session.transport,
                    "test_only_allow_legacy_input_synchronization",
                    False,
                )
                is not True
            ):
                raise MachineError(
                    "Primary-controller transport does not implement bounded input "
                    "synchronization"
                )
            # Explicitly tagged test-only compatibility seam. Both supported
            # product transports implement bounded quiet synchronization.
            startup = session.transport.drain()
            session.diagnostics.set_synchronization(
                {
                    "method": "legacy-test-drain",
                    "discarded_lines": len(startup),
                }
            )
        for line in startup:
            self._append_log("RX", line)
            session.diagnostics.record_rx(None, line)
        return startup

    def _identify_protocol(
        self,
        *,
        expected_stop_epoch: int,
        selected_protocol: str = "auto",
    ) -> str:
        with self._lock:
            session = self._candidate_session
        if session is None:
            raise MachineError("Controller connection candidate is unavailable")
        probes = (
            CONTROLLER_DIALECT_REGISTRY.probe_attempts
            if selected_protocol == "auto"
            else tuple(
                probe
                for probe in CONTROLLER_DIALECT_REGISTRY.probe_attempts
                if probe.dialect_id == selected_protocol
            )
        )
        for probe in probes:
            dialect = CONTROLLER_DIALECT_REGISTRY.get(probe.dialect_id)
            probe_session = replace(session, dialect=dialect)
            with self._lock:
                if not self._same_controller_session(self._candidate_session, session):
                    raise MachineError("Controller connection candidate was superseded")
                self._candidate_session = probe_session
            session = probe_session
            try:
                responses = self._send_candidate_command(
                    session,
                    probe.command,
                    timeout=probe.timeout_seconds,
                    expected_stop_epoch=expected_stop_epoch,
                    terminal_error_consumed=probe.terminal_error_consumed,
                )
            except _ControllerCommandRejected:
                responses = []
            if dialect.recognizes_identity(responses):
                session.diagnostics.set_firmware_identity(responses)
                return dialect.id
            if selected_protocol != "auto":
                break
        raise MachineError(
            "Controller protocol could not be identified. Set machine.protocol explicitly after running tools/controller_probe.py."
        )

    def _validate_candidate_air_assist(self, dialect: ControllerDialect) -> None:
        try:
            commands = resolve_air_assist_commands(
                self.settings.air_assist,
                protocol=dialect.id,
            )
        except (TypeError, ValueError) as exc:
            raise SafetyError(str(exc)) from exc
        if (
            commands is not None
            and commands.target is AirAssistTarget.PRIMARY
            and commands.protocol != dialect.id
        ):
            raise SafetyError(
                "Configured air-assist output does not match the connected controller dialect"
            )

    def _send_candidate_command(
        self,
        session: ControllerSession,
        command: str,
        *,
        timeout: float,
        expected_stop_epoch: int,
        terminal_error_consumed: bool = False,
    ) -> list[str]:
        """Execute one transaction against an unpublished exact candidate."""

        self._raise_if_connection_cancelled(expected_stop_epoch)
        with self._lock:
            current = self._candidate_session
            if not self._same_controller_session(current, session):
                raise MachineError("Controller connection candidate was superseded")
        transaction_started = time.monotonic()
        sequence = session.diagnostics.next_command(command)
        with self._transport_write_lock:
            self._raise_if_connection_cancelled(expected_stop_epoch)
            try:
                session.transport.write_line(command)
                self._append_log("TX", command)
            except Exception as exc:
                raise MachineError(
                    f"Command {command!r} failed while writing candidate session: {exc}"
                ) from exc
        try:
            responses = self._wait_for_ack(
                self._remaining_candidate_timeout(timeout),
                expected_stop_epoch=expected_stop_epoch,
                terminal_error_consumed=terminal_error_consumed,
                session=session,
                command_sequence=sequence,
                transaction_started_at=transaction_started,
                require_active_session=False,
            )
            LOGGER.info(
                "primary controller handshake transaction generation=%d endpoint=%s "
                "protocol=%s sequence=%d command=%r duration_seconds=%.6f terminal=%s",
                session.generation,
                session.resolved_endpoint,
                session.dialect.id,
                sequence,
                command,
                max(0.0, time.monotonic() - transaction_started),
                CommandResponseKind.ACKNOWLEDGEMENT.value,
            )
            return responses
        except _ControllerCommandRejected:
            raise
        except Exception as exc:
            raise MachineError(f"Command {command!r} failed: {exc}") from exc

    def _verify_grbl_candidate_alignment(
        self,
        session: ControllerSession,
        *,
        expected_stop_epoch: int,
    ) -> None:
        timeout = max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout)
        modal_command, offsets_command = GRBL_DIALECT.coordinate_state_query_commands
        modal = self._send_candidate_command(
            session,
            modal_command,
            timeout=timeout,
            expected_stop_epoch=expected_stop_epoch,
        )
        offsets = self._send_candidate_command(
            session,
            offsets_command,
            timeout=timeout,
            expected_stop_epoch=expected_stop_epoch,
        )
        coordinate_state = parse_grbl_coordinate_state(modal, offsets)
        transaction_started = time.monotonic()
        sequence = session.diagnostics.next_command("?")
        with self._transport_write_lock:
            self._raise_if_connection_cancelled(expected_stop_epoch)
            try:
                session.transport.write_raw(b"?")
                self._append_log("TX", "? (connection synchronization)")
            except Exception as exc:
                raise MachineError(
                    f"GRBL realtime synchronization query failed while writing: {exc}"
                ) from exc
        realtime_timeout = self._remaining_candidate_timeout(timeout)
        deadline = time.monotonic() + realtime_timeout
        while time.monotonic() < deadline:
            self._raise_if_connection_cancelled(expected_stop_epoch)
            try:
                response = session.transport.read_line(
                    timeout=min(0.2, max(0.0, deadline - time.monotonic()))
                )
            except Exception as exc:
                raise MachineError(
                    f"GRBL realtime synchronization query failed while reading: {exc}"
                ) from exc
            if not response:
                continue
            session.diagnostics.record_rx(sequence, response)
            self._append_log("RX", response)
            stripped = response.strip()
            if stripped.startswith("<") and stripped.endswith(">"):
                parse_grbl_realtime_status(
                    stripped,
                    coordinate_state=coordinate_state,
                )
                self._reject_queued_unowned_responses(
                    session,
                    command_sequence=sequence,
                    transaction_deadline=deadline,
                )
                session.diagnostics.record_success(
                    sequence,
                    started_monotonic=transaction_started,
                    terminal_classification=CommandResponseKind.REALTIME_STATUS.value,
                )
                return
            kind = session.dialect.classify_command_response(stripped)
            if kind is CommandResponseKind.ACKNOWLEDGEMENT:
                raise MachineError(
                    "Unexpected unowned acknowledgement while synchronizing GRBL status"
                )
            if kind in {CommandResponseKind.ERROR, CommandResponseKind.ALARM}:
                raise MachineError(stripped)
            raise MachineError(
                "Unexpected startup or malformed response while synchronizing GRBL status: "
                f"{stripped!r}"
            )
        raise MachineError(
            "Controller did not provide a valid GRBL realtime status within "
            f"{realtime_timeout:g} seconds"
        )

    def _schedule_controller_recovery(
        self,
        expected_stop_epoch: int,
        *,
        session: ControllerSession,
    ) -> None:
        """Start or retarget one communication-only recovery worker."""

        with self._lock:
            if self._shutdown_requested:
                return
            self._recovery_stop_epoch = expected_stop_epoch
            self._recovery_endpoint = session.configured_endpoint
            self._recovery_protocol = session.dialect.id
            self._recovery_baudrate = session.baudrate
            running = self._recovery_thread
            if running is not None and running.is_alive():
                if self._controller_state is ControllerState.STOPPING:
                    self._set_controller_state_locked(ControllerState.RECOVERING)
                return
            if self._controller_state in {
                ControllerState.STOPPING,
                ControllerState.RECONNECT_REQUIRED,
            }:
                self._set_controller_state_locked(ControllerState.RECOVERING)
            thread = threading.Thread(
                target=self._controller_recovery_worker,
                name="primary-controller-recovery",
                daemon=True,
            )
            self._recovery_thread = thread
            try:
                thread.start()
            except Exception as exc:
                self._recovery_thread = None
                self._set_controller_state_locked(
                    ControllerState.RECONNECT_REQUIRED,
                    failure=f"Controller recovery worker could not start: {exc}",
                )

    def _controller_recovery_worker(self) -> None:
        """Recover communication only; never Home, move, arm, or resume a job."""

        try:
            while True:
                with self._lock:
                    if self._shutdown_requested:
                        return
                    expected_stop_epoch = self._recovery_stop_epoch
                    endpoint = self._recovery_endpoint
                    protocol = self._recovery_protocol
                    baudrate = self._recovery_baudrate
                if (
                    expected_stop_epoch is None
                    or endpoint is None
                    or protocol is None
                    or baudrate is None
                ):
                    return
                try:
                    with self.operation_scope(expected_stop_epoch):
                        self.connect(
                            port=endpoint,
                            protocol=protocol,
                            baudrate=baudrate,
                        )
                except Exception as exc:
                    with self._lock:
                        if self._shutdown_requested:
                            return
                        if self._recovery_stop_epoch != expected_stop_epoch:
                            if self._controller_state is ControllerState.STOPPING:
                                self._set_controller_state_locked(
                                    ControllerState.RECOVERING
                                )
                            continue
                        if self._controller_state is ControllerState.RECOVERING:
                            self._set_controller_state_locked(
                                ControllerState.RECONNECT_REQUIRED,
                                failure=exc,
                                failure_code=_FAILURE_RECONNECT_REQUIRED,
                            )
                    LOGGER.warning(
                        "primary controller recovery failed endpoint=%s protocol=%s "
                        "stop_epoch=%d code=%s detail=%s",
                        endpoint,
                        protocol,
                        expected_stop_epoch,
                        _FAILURE_RECONNECT_REQUIRED,
                        str(exc)[:500],
                    )
                    return
                with self._lock:
                    if self._recovery_stop_epoch != expected_stop_epoch:
                        continue
                    LOGGER.info(
                        "primary controller recovery complete stop_epoch=%d "
                        "generation=%s endpoint=%s protocol=%s action_required=HOME",
                        expected_stop_epoch,
                        "-" if self._session is None else self._session.generation,
                        endpoint,
                        protocol,
                    )
                return
        finally:
            with self._lock:
                if self._recovery_thread is threading.current_thread():
                    self._recovery_thread = None

    def disconnect(self) -> None:
        # request_stop owns the exact-session M5, close, cancellation, and
        # candidate quarantine. Explicit disconnect suppresses recovery.
        self.stop_job(emergency=False, _recover=False)
        with self._command_lock, self._lock:
            self._best_effort_secondary_off(context="disconnect cleanup")
            # Once the transport is closed there is no same-session controller
            # path left on which retaining a failed job's immutable OFF mapping
            # could provide another retry.
            self._active_job_air_assist_off_commands = ()
            self._active_job_air_assist_commands = None
            self._session = None
            self._candidate_session = None
            self._candidate_connect_deadline = None
            self._transport = None
            self._invalidate_coordinate_reference()
            self._clear_arm_authorization()
            self._job_laser_authorized = False
            self._recovery_stop_epoch = None
            if not self._shutdown_requested:
                self._set_controller_state_locked(
                    ControllerState.DISCONNECTED,
                    force_terminal=True,
                )

    def replace_connection(self) -> dict[str, Any]:
        """Explicitly replace an untrusted session under a fresh STOP generation.

        ``disconnect()`` intentionally advances the STOP generation as part of
        its laser-off cleanup.  A reconnect worker is normally still bound to
        the generation captured when the UI action was queued, so the new
        ``connect()`` must receive a scope captured after that cleanup.  Exactly
        one generation advance belongs to disconnect itself; any additional
        advance proves that a concurrent STOP raced with replacement and must
        cancel the attempt.
        """

        requested_generation = self._operation_stop_epoch()
        with self._command_lock:
            with self._stop_epoch_lock:
                current_generation = self._stop_epoch
            with self._lock:
                if (
                    current_generation != requested_generation
                    and self._last_replacement_request_epoch == requested_generation
                    and self._session is not None
                    and self._last_replacement_session_generation
                    == self._session.generation
                    and self._controller_state in CONNECTED_CONTROLLER_STATES
                ):
                    return self.status()
                if current_generation != requested_generation:
                    raise MachineError(
                        "Controller reconnection was cancelled by software STOP"
                    )
                # Reconnect is idempotent after another caller or automatic
                # recovery has already published a trusted successor.
                if (
                    self._session is not None
                    and self._controller_state in CONNECTED_CONTROLLER_STATES
                    and not self._controller_reconnect_required
                ):
                    return self.status()
                previous = self._session
                endpoint = (
                    previous.configured_endpoint
                    if previous is not None
                    else self._active_port
                )
                protocol = (
                    previous.dialect.id if previous is not None else self._protocol
                )
                baudrate = (
                    previous.baudrate
                    if previous is not None
                    else self._active_baudrate
                )
            self.stop_job(emergency=False, _recover=False)
            replacement_generation = self.operation_generation()
            try:
                with self.operation_scope(replacement_generation):
                    result = self.connect(
                        port=endpoint,
                        protocol=protocol,
                        baudrate=baudrate,
                    )
            except Exception as exc:
                with self._stop_epoch_lock:
                    cancelled = self._stop_epoch != replacement_generation
                with self._lock:
                    if not cancelled and self._controller_state not in {
                        ControllerState.RECONNECT_REQUIRED,
                        ControllerState.FAULTED,
                        ControllerState.DISCONNECTED,
                        ControllerState.SHUTTING_DOWN,
                    }:
                        self._set_controller_state_locked(
                            ControllerState.RECONNECT_REQUIRED,
                            failure=exc,
                            failure_code=_FAILURE_RECONNECT_REQUIRED,
                        )
                raise
            with self._lock:
                self._invalidate_coordinate_reference()
                self._clear_arm_authorization()
                self._job_laser_authorized = False
                self._last_replacement_request_epoch = requested_generation
                self._last_replacement_session_generation = (
                    None if self._session is None else self._session.generation
                )
            return result

    def arm(
        self,
        phrase: str,
        *,
        program_digest: str | None = None,
        _expected_stop_epoch: int | None = None,
        _expected_authorization_epoch: int | None = None,
    ) -> float:
        # Arming participates in controller-operation ownership even though it
        # does not write to the transport. This keeps a jog or Home / park from
        # passing its unarmed gate and then becoming armed mid-motion.
        requested_stop_epoch = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._stop_epoch_lock:
            if self._stop_epoch != requested_stop_epoch:
                raise MachineError("Arming was cancelled by software STOP")
            requested_authorization_epoch = (
                self._authorization_epoch
                if _expected_authorization_epoch is None
                else _expected_authorization_epoch
            )
            if self._authorization_epoch != requested_authorization_epoch:
                raise MachineError("Arming was cancelled by disarm")
            # Preserve the request-time one-use semantics while waiting for a
            # controller operation to finish.
            self._clear_arm_authorization()
        with self._command_lock:
            return self._arm_locked(
                phrase,
                program_digest=program_digest,
                _expected_stop_epoch=requested_stop_epoch,
                _expected_authorization_epoch=requested_authorization_epoch,
            )

    def _arm_locked(
        self,
        phrase: str,
        *,
        program_digest: str | None,
        _expected_stop_epoch: int | None,
        _expected_authorization_epoch: int | None,
    ) -> float:
        # Every attempt consumes an earlier grant, even when this attempt is
        # malformed or rejected. A temporary grant must not survive for replay.
        requested_stop_epoch = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._stop_epoch_lock:
            arm_stop_epoch = requested_stop_epoch
            if self._stop_epoch != arm_stop_epoch:
                raise MachineError("Arming was cancelled by software STOP")
            arm_authorization_epoch = (
                self._authorization_epoch
                if _expected_authorization_epoch is None
                else _expected_authorization_epoch
            )
            if self._authorization_epoch != arm_authorization_epoch:
                raise MachineError("Arming was cancelled by disarm")
            self._clear_arm_authorization()
        self._require_safety_configuration()
        if self.laser_lockout:
            raise SafetyError("Laser output is locked out for this process")
        if phrase.strip() != self.ARM_PHRASE:
            raise SafetyError("Arming phrase did not match")
        controller_session = self._require_session()
        if self.settings.backend == "serial" and (
            not self._coordinate_reference_ready
            or self._coordinate_reference_session_generation
            != controller_session.generation
            or self._controller_state is not ControllerState.READY_MOTION
        ):
            raise SafetyError(
                "Home / park must complete after this controller connection or reset "
                "before laser control can be armed"
            )
        if self._job.running:
            raise MachineError("Cannot change arming state while a job is running")
        timeout = float(self.laser_settings.arm_timeout_seconds)
        with self._stop_epoch_lock:
            if self._stop_epoch != arm_stop_epoch:
                raise MachineError("Arming was cancelled by software STOP")
            if self._authorization_epoch != arm_authorization_epoch:
                raise MachineError("Arming was cancelled by disarm")
            if self._controller_reconnect_required:
                raise MachineError(
                    "Controller command state is untrusted after software STOP; "
                    "disconnect and reconnect before arming"
                )
            self._armed_until = time.time() + timeout
            self._armed_until_monotonic = time.monotonic() + timeout
            self._armed_program_digest = program_digest
        self._append_log("INFO", "laser control armed temporarily")
        return self._armed_until

    def arm_program(self, phrase: str, program: ValidatedProgram) -> float:
        arm_stop_epoch = self._operation_stop_epoch()
        with self._stop_epoch_lock:
            if self._stop_epoch != arm_stop_epoch:
                raise MachineError("Arming was cancelled by software STOP")
            arm_authorization_epoch = self._authorization_epoch
            self._clear_arm_authorization()
        self._require_current_safety_profile(program)
        return self.arm(
            phrase,
            program_digest=program.digest,
            _expected_stop_epoch=arm_stop_epoch,
            _expected_authorization_epoch=arm_authorization_epoch,
        )

    def disarm(self) -> None:
        # Authorization has its own generation so disarm can defeat an arm
        # currently being validated without cancelling unrelated laser-off motion.
        with self._stop_epoch_lock:
            self._authorization_epoch += 1
            self._clear_arm_authorization()
            self._job_laser_authorized = False
        if self._job.running:
            self.stop_job(emergency=False)
            return
        if self._transport is None:
            self._best_effort_secondary_off(context="disarm without primary connection")
            return
        # No powered job is active, so wait for command ownership. A trusted
        # stream consumes the M5 acknowledgement; an already-untrusted stream
        # receives a fresh best-effort M5 and remains reconnect-only.
        with self._command_lock:
            if self._job.running:
                self.stop_job(emergency=False)
                return
            transport = self._transport
            if transport is None:
                return
            if self._controller_reconnect_required:
                self._best_effort_fail_off(
                    transport,
                    context="disarm on untrusted connection",
                )
                self._best_effort_secondary_off(
                    context="disarm on untrusted connection"
                )
                return
            controller_session = self._require_session()
            transport = controller_session.transport
            fail_off_error: Exception | None = None
            for command in ("M5", *self._air_assist_off_commands):
                try:
                    self._send_command_locked(
                        command,
                        timeout=_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS,
                        _internal_air_assist_off=(command != "M5"),
                    )
                except Exception as exc:
                    fail_off_error = fail_off_error or exc
                    self._append_log(
                        "ERROR",
                        f"Disarm command {command!r} failed: {exc}",
                    )
            if fail_off_error is not None:
                self._mark_controller_command_state_untrusted(
                    controller_session,
                    reason=f"Disarm fail-off became ambiguous: {fail_off_error}",
                )
            self._best_effort_secondary_off(context="disarm cleanup")

    def _require_session(self) -> ControllerSession:
        with self._lock:
            session = self._adopt_legacy_test_session_locked()
            if self._controller_reconnect_required:
                raise MachineError(
                    "Controller command state is untrusted after STOP or an uncertain "
                    "controller exchange; reconnect before issuing more commands"
                )
            if (
                session is None
                or self._controller_state not in CONNECTED_CONTROLLER_STATES
            ):
                raise MachineError("Controller is not connected")
            return session

    def _require_connection(self) -> MachineTransport:
        return self._require_session().transport

    @staticmethod
    def _codes(words: list[Any], letter: str) -> set[int]:
        output: set[int] = set()
        for word in words:
            if word.letter != letter:
                continue
            rounded = round(word.value)
            if abs(word.value - rounded) < 1e-9:
                output.add(int(rounded))
        return output

    def _check_line_safety(
        self,
        line: str,
        *,
        job_execution: bool = False,
        preflight: bool = False,
    ) -> None:
        line_commands = (
            self._active_job_air_assist_commands
            if (
                job_execution
                and not preflight
                and self._active_job_air_assist_commands is not None
            )
            else self._resolved_air_assist_commands()
        )
        air_assist_kind = self._air_assist_command_kind(
            line,
            commands=line_commands,
        )
        if (
            air_assist_kind is not None
            and line_commands is not None
            and line_commands.target is AirAssistTarget.PI_SECONDARY
        ):
            self._require_safety_configuration()
            laser_authorized = preflight or self.armed or (
                job_execution and self._job_laser_authorized
            )
            if air_assist_kind == "on" and not laser_authorized:
                raise SafetyError(
                    "Air-assist enable is allowed only inside an authorized powered job"
                )
            if not (preflight or job_execution):
                raise SafetyError(
                    "Air-assist commands are allowed only in streamed jobs"
                )
            if (
                self.settings.backend == "serial"
                and self.hardware_enabled is not True
            ):
                raise SafetyError("Hardware control is not enabled for this process")
            return
        cleaned = strip_comment(line)
        if not cleaned:
            return
        words = parse_words(cleaned)
        m_codes = self._codes(words, "M")
        g_codes = self._codes(words, "G")
        air_assist_kind = self._air_assist_command_kind(
            cleaned,
            commands=line_commands,
        )
        powers = (
            []
            if air_assist_kind is not None
            else [word.value for word in words if word.letter == "S"]
        )
        requests_laser = bool(m_codes.intersection({3, 4}) or any(value > 0 for value in powers))
        requests_air_assist = air_assist_kind is not None
        is_homing = cleaned.upper() == "$H" or 28 in g_codes
        requests_motion = contains_motion(cleaned) or is_homing
        if requests_laser or requests_motion or requests_air_assist:
            self._require_safety_configuration()
        if g_codes in ({0}, {1}):
            feeds = [word.value for word in words if word.letter == "F"]
            if len(feeds) != 1 or not math.isfinite(feeds[0]) or feeds[0] <= 0:
                raise SafetyError("Every G0/G1 line must include one positive finite F feed rate")
            maximum_feed = (
                self.settings.max_travel_feed_mm_min
                if g_codes == {0}
                else self.settings.max_work_feed_mm_min
            )
            if feeds[0] > maximum_feed:
                motion = "travel" if g_codes == {0} else "work"
                raise SafetyError(
                    f"{motion.capitalize()} feed F{feeds[0]:g} exceeds the "
                    f"configured {motion} ceiling of {maximum_feed:g} mm/min"
                )
        # Static program preflight must be possible before homing and temporary
        # arming. Runtime command paths still require live authorization.
        laser_authorized = preflight or self.armed or (
            job_execution and self._job_laser_authorized
        )
        if requests_laser and not laser_authorized:
            raise SafetyError("Laser-enable or positive-power command blocked because control is not armed")
        if air_assist_kind == "on" and not laser_authorized:
            raise SafetyError(
                "Air-assist enable is allowed only inside an authorized powered job"
            )
        if requests_air_assist and not (preflight or job_execution):
            raise SafetyError("Air-assist commands are allowed only in streamed jobs")
        if requests_motion and self.settings.allow_motion is not True:
            raise SafetyError("Motion commands are disabled in machine.allow_motion")
        if (
            (requests_laser or requests_motion or requests_air_assist)
            and self.settings.backend == "serial"
            and self.hardware_enabled is not True
        ):
            raise SafetyError("Hardware control is not enabled for this process")

    def _validate_manual_command(self, line: str) -> str:
        cleaned = strip_comment(line).upper()
        if not cleaned:
            raise MachineError("Controller command is empty")
        if cleaned in _QUERY_COMMANDS or cleaned == "M5":
            return cleaned
        raise SafetyError(
            "Manual commands are limited to read-only identity/status queries and M5. "
            "Use the guarded photography-position action or a validated generated job for motion."
        )

    def send_command(
        self,
        line: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
        _expected_stop_epoch: int | None = None,
    ) -> list[str]:
        expected_stop_epoch = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._command_lock:
            return self._send_command_locked(
                line,
                timeout=timeout,
                _internal_motion=_internal_motion,
                _expected_stop_epoch=expected_stop_epoch,
            )

    def _send_command_locked(
        self,
        line: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
        _internal_air_assist_off: bool = False,
        _expected_stop_epoch: int | None = None,
        _terminal_error_consumed: bool = False,
    ) -> list[str]:
        if len(line) > 256:
            raise MachineError("Single controller command exceeds 256 characters")
        if self._job.running:
            raise MachineError("Manual commands are disabled while a job is running")
        if _internal_air_assist_off:
            cleaned = " ".join(strip_comment(line).upper().split())
            trusted_off_commands = {
                *self._active_job_air_assist_off_commands,
                *self._air_assist_off_commands,
            }
            if cleaned not in trusted_off_commands:
                raise SafetyError(
                    "Internal air-off command does not match a trusted configured mapping"
                )
        else:
            cleaned = (
                strip_comment(line).upper()
                if _internal_motion
                else self._validate_manual_command(line)
            )
        if not cleaned:
            raise MachineError("Controller command is empty")
        session: ControllerSession
        sequence: int
        with self._transport_write_lock:
            if _expected_stop_epoch is not None:
                with self._stop_epoch_lock:
                    if self._stop_epoch != _expected_stop_epoch:
                        raise MachineError("Operation was cancelled by software STOP")
            if not _internal_air_assist_off:
                self._check_line_safety(cleaned)
            session = self._require_session()
            transaction_started = time.monotonic()
            sequence = session.diagnostics.next_command(cleaned)
            try:
                session.transport.write_line(cleaned)
                self._append_log("TX", cleaned)
            except Exception as exc:
                # A partial write can still produce a delayed response. Do not
                # let that response acknowledge a later command after retry.
                self._mark_controller_command_state_untrusted(
                    session,
                    reason=f"Command {cleaned!r} write failed: {exc}",
                    failure_code=_FAILURE_SESSION_QUARANTINED,
                    failed_command=cleaned,
                )
                raise MachineError(
                    f"Command {cleaned!r} failed while writing: {exc}"
                ) from exc
        acknowledgement_timeout = timeout or self.settings.read_timeout
        try:
            responses = self._wait_for_ack(
                acknowledgement_timeout,
                expected_stop_epoch=_expected_stop_epoch,
                terminal_error_consumed=_terminal_error_consumed,
                session=session,
                command_sequence=sequence,
                transaction_started_at=transaction_started,
            )
            LOGGER.info(
                "primary controller transaction generation=%d endpoint=%s protocol=%s "
                "sequence=%d command=%r duration_seconds=%.6f terminal=%s",
                session.generation,
                session.resolved_endpoint,
                session.dialect.id,
                sequence,
                cleaned,
                max(0.0, time.monotonic() - transaction_started),
                CommandResponseKind.ACKNOWLEDGEMENT.value,
            )
            return responses
        except Exception as exc:
            if not isinstance(exc, _ControllerCommandRejected):
                # A timeout, read failure, cancellation, or disconnect leaves
                # the command/response position unknowable. An explicit
                # controller error/alarm is different: that terminal response
                # has been consumed and callers may apply a documented fallback.
                detail = str(exc)
                if "restarted" in detail:
                    failure_code = _FAILURE_SESSION_QUARANTINED
                elif "malformed" in detail:
                    failure_code = _FAILURE_SESSION_QUARANTINED
                elif "did not acknowledge" in detail:
                    failure_code = _FAILURE_COMMAND_TIMEOUT
                elif "read failed" in detail:
                    failure_code = _FAILURE_SESSION_QUARANTINED
                else:
                    failure_code = _FAILURE_SESSION_QUARANTINED
                self._mark_controller_command_state_untrusted(
                    session,
                    reason=f"Command {cleaned!r} response became ambiguous: {exc}",
                    failure_code=failure_code,
                    failed_command=cleaned,
                )
            if isinstance(exc, _ControllerCommandRejected) and _terminal_error_consumed:
                raise
            raise MachineError(f"Command {cleaned!r} failed: {exc}") from exc

    def _wait_for_ack(
        self,
        timeout: float,
        *,
        expected_stop_epoch: int | None = None,
        terminal_error_consumed: bool = False,
        session: ControllerSession | None = None,
        command_sequence: int | None = None,
        transaction_started_at: float | None = None,
        require_active_session: bool = True,
    ) -> list[str]:
        # Candidate synchronization is owned by its STOP epoch, never by an old
        # job context that may still be unwinding on another thread.
        job_stop = (
            self._current_job_stop_event() if require_active_session else None
        )
        if session is None:
            context = self._current_job_run_context()
            session = context.session if context is not None else self._require_session()
            pending = getattr(self._job_context, "pending_transaction", None)
            if (
                command_sequence is None
                and isinstance(pending, tuple)
                and len(pending) == 3
                and self._same_controller_session(pending[0], session)
            ):
                command_sequence = pending[1]
                transaction_started_at = pending[2]

        def clear_pending_transaction() -> None:
            pending = getattr(self._job_context, "pending_transaction", None)
            if (
                isinstance(pending, tuple)
                and len(pending) == 3
                and self._same_controller_session(pending[0], session)
                and (command_sequence is None or pending[1] == command_sequence)
            ):
                self._job_context.pending_transaction = None

        try:
            if self._job.running and job_stop is not None and job_stop.is_set():
                raise MachineError("Job stopped")
            if require_active_session:
                with self._lock:
                    if not self._same_controller_session(self._session, session):
                        if job_stop is not None and job_stop.is_set():
                            raise MachineError("Job stopped") from None
                        raise MachineError("Controller session was superseded")
            transport = session.transport
            deadline = time.monotonic() + timeout
            responses: list[str] = []
            while time.monotonic() < deadline:
                if expected_stop_epoch is not None:
                    with self._stop_epoch_lock:
                        if self._stop_epoch != expected_stop_epoch:
                            raise MachineError("Operation was cancelled by software STOP")
                if self._job.running and job_stop is not None and job_stop.is_set():
                    raise MachineError("Job stopped")
                try:
                    response = transport.read_line(
                        timeout=min(0.2, max(0.0, deadline - time.monotonic()))
                    )
                except Exception as exc:
                    raise MachineError(f"Controller read failed: {exc}") from exc
                if not response:
                    continue
                if self._job.running and job_stop is not None and job_stop.is_set():
                    raise MachineError("Job stopped")
                if expected_stop_epoch is not None:
                    with self._stop_epoch_lock:
                        if self._stop_epoch != expected_stop_epoch:
                            raise MachineError("Operation was cancelled by software STOP")
                self._append_log("RX", response)
                session.diagnostics.record_rx(command_sequence, response)
                response_kind = session.dialect.classify_command_response(response)
                if response_kind is CommandResponseKind.REALTIME_STATUS:
                    # Asynchronous telemetry belongs to neither command payload
                    # nor its terminal acknowledgement.
                    continue
                if response_kind is CommandResponseKind.ACKNOWLEDGEMENT:
                    responses.append(response)
                    self._reject_queued_unowned_responses(
                        session,
                        command_sequence=command_sequence,
                        transaction_deadline=deadline,
                    )
                    if command_sequence is not None:
                        session.diagnostics.record_success(
                            command_sequence,
                            started_monotonic=(
                                time.monotonic()
                                if transaction_started_at is None
                                else transaction_started_at
                            ),
                            terminal_classification=response_kind.value,
                        )
                    return responses
                if response_kind in {
                    CommandResponseKind.ERROR,
                    CommandResponseKind.ALARM,
                }:
                    self._reject_queued_unowned_responses(
                        session,
                        command_sequence=command_sequence,
                        transaction_deadline=deadline,
                    )
                    raise _ControllerCommandRejected(response)
                if response_kind is CommandResponseKind.STARTUP:
                    raise MachineError(
                        "Controller restarted during an in-flight command transaction"
                    )
                if response_kind is CommandResponseKind.MALFORMED:
                    if (
                        terminal_error_consumed
                        and re.fullmatch(r"error:[\x20-\x7e]{1,128}", response, re.IGNORECASE)
                    ):
                        self._reject_queued_unowned_responses(
                            session,
                            command_sequence=command_sequence,
                            transaction_deadline=deadline,
                        )
                        raise _ControllerCommandRejected(response)
                    raise MachineError(
                        f"Controller returned a malformed response frame: {response!r}"
                    )
                responses.append(response)
            raise MachineError(
                f"Controller did not acknowledge command within {timeout:g} seconds"
            )
        finally:
            clear_pending_transaction()

    def _reject_queued_unowned_responses(
        self,
        session: ControllerSession,
        *,
        command_sequence: int | None,
        transaction_deadline: float,
        allow_one_acknowledgement: bool = False,
    ) -> list[str]:
        """Require a short quiet boundary after a terminal line response.

        GRBL has no request identifiers. Once one terminal response has completed
        a transaction, a later terminal or payload line has no safe owner and must
        never be retained to acknowledge the next command. The bounded quiet
        interval also covers reader-thread scheduling immediately after ``ok``.
        Realtime status remains asynchronous telemetry and may be diverted safely,
        but it restarts the quiet interval.
        """

        consumed_owned: list[str] = []
        quiet_started = time.monotonic()
        while True:
            now = time.monotonic()
            quiet_remaining = _POST_TERMINAL_QUIET_SECONDS - (now - quiet_started)
            transaction_remaining = transaction_deadline - now
            if quiet_remaining <= 0.0:
                return consumed_owned
            if transaction_remaining <= 0.0:
                raise MachineError(
                    "Controller transaction ended without a quiet post-terminal boundary"
                )
            try:
                response = session.transport.read_line(
                    timeout=min(quiet_remaining, transaction_remaining)
                )
            except Exception as exc:
                raise MachineError(
                    f"Controller read failed while checking transaction boundary: {exc}"
                ) from exc
            if not response:
                continue
            self._append_log("RX", response)
            session.diagnostics.record_rx(command_sequence, response)
            response_kind = session.dialect.classify_command_response(response)
            if response_kind is CommandResponseKind.REALTIME_STATUS:
                quiet_started = time.monotonic()
                continue
            if (
                allow_one_acknowledgement
                and response_kind is CommandResponseKind.ACKNOWLEDGEMENT
            ):
                allow_one_acknowledgement = False
                consumed_owned.append(response)
                quiet_started = time.monotonic()
                continue
            raise MachineError(
                "Controller returned an unowned response after the terminal "
                f"acknowledgement: {response!r} ({response_kind.value})"
            )

    def _reject_prewrite_unowned_responses(
        self,
        session: ControllerSession,
    ) -> None:
        """Reject queued non-telemetry before assigning a new line transaction."""

        while True:
            try:
                response = session.transport.read_line(timeout=0.0)
            except Exception as exc:
                raise MachineError(
                    f"Controller read failed before command write: {exc}"
                ) from exc
            if not response:
                return
            self._append_log("RX", response)
            session.diagnostics.record_rx(None, response)
            response_kind = session.dialect.classify_command_response(response)
            if response_kind is CommandResponseKind.REALTIME_STATUS:
                continue
            raise MachineError(
                "Controller returned an unowned response before a new command "
                f"could be assigned: {response!r} ({response_kind.value})"
            )

    def query_identity(self) -> list[str]:
        # Preserve the pre-connection failure path: unresolved ``auto`` uses
        # M115 and then fails through the ordinary connection gate.
        dialect = self._dialect or MARLIN_DIALECT
        return self.send_command(dialect.identity_query_command, timeout=3.0)

    @staticmethod
    def _reported_grbl_step_idle_delay(responses: list[str]) -> int | None:
        return parse_grbl_step_idle_delay(responses)

    @staticmethod
    def _is_exact_grbl_locked_error(error: BaseException) -> bool:
        """Return whether a consumed terminal response was exactly GRBL error:9."""

        current: BaseException | None = error
        while current is not None:
            if isinstance(current, _ControllerCommandRejected):
                return is_exact_grbl_locked_error_response(str(current))
            current = current.__cause__
        return False

    def _laser_off_with_pre_home_grbl_unlock(
        self,
        execute: Callable[[str], list[str]],
        *,
        context: str,
        dialect: ControllerDialect | None = None,
    ) -> None:
        """Require M5, narrowly unlocking an exact GRBL pre-home alarm lock."""

        try:
            execute(GRBL_DIALECT.laser_off_command)
        except MachineError as exc:
            if not (
                (self._dialect if dialect is None else dialect) is GRBL_DIALECT
                and self.settings.home_before_photo
                and self._is_exact_grbl_locked_error(exc)
            ):
                raise
            self._append_log(
                "INFO",
                f"M5 was blocked by the GRBL alarm lock during {context}; "
                "unlocking before mandatory Home / park",
            )
            session = GRBL_DIALECT.grbl_session
            assert session is not None
            execute(session.unlock_command)
            execute(GRBL_DIALECT.laser_off_command)

    def _normalize_and_release_grbl_after_connect(
        self,
        *,
        session: ControllerSession | None = None,
        expected_stop_epoch: int | None = None,
    ) -> None:
        """Normalize a newly connected controller without resetting it."""

        policy = GRBL_DIALECT.grbl_session
        assert policy is not None
        timeout = _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS
        normal = int(self.settings.grbl_step_idle_delay_ms)

        def execute(command: str) -> list[str]:
            if session is not None:
                if expected_stop_epoch is None:
                    raise MachineError("Candidate normalization requires a STOP epoch")
                try:
                    return self._send_candidate_command(
                        session,
                        command,
                        timeout=timeout,
                        expected_stop_epoch=expected_stop_epoch,
                    )
                except _ControllerCommandRejected as exc:
                    raise MachineError(
                        f"Command {command!r} was rejected by the controller: {exc}"
                    ) from exc
            return self.send_command(command, timeout=timeout, _internal_motion=True)

        # Establish an acknowledged laser-off state before inspecting or
        # changing settings. The only fallback remains the exact GRBL error:9
        # path guarded by mandatory Home / park configuration.
        self._laser_off_with_pre_home_grbl_unlock(
            execute,
            context="connection normalization",
            dialect=GRBL_DIALECT if session is not None else None,
        )
        responses = execute(policy.settings_query_command)
        current = self._reported_grbl_step_idle_delay(responses)
        if current is None:
            raise MachineError("GRBL $$ did not report the $1 step-idle delay")
        # An ordinary connection has not moved the machine, so it does not need
        # the $SLP/soft-reset fallback used after a held capture or powered job.
        if current == policy.held_step_idle_delay_ms:
            execute(policy.format_step_idle_delay(normal))
            verification = execute(policy.settings_query_command)
            if self._reported_grbl_step_idle_delay(verification) != normal:
                raise MachineError(
                    "GRBL did not verify the configured finite $1 step-idle delay"
                )
        self._invalidate_coordinate_reference()
        if current == policy.held_step_idle_delay_ms:
            self._append_log(
                "INFO",
                f"Recovered stale camera motor hold at connection; restored $1={normal}",
            )

    def _release_grbl_motors(
        self,
        *,
        restore_idle_delay: int | None,
        job_execution: bool,
        context: str,
    ) -> None:
        """Restore scoped hold state and explicitly release GRBL motors."""

        session = GRBL_DIALECT.grbl_session
        assert session is not None
        controller_session = self._require_session()
        timeout = _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS
        failures: list[str] = []

        def execute(command: str) -> list[str]:
            if job_execution:
                return self._execute_running_job_command(command, timeout=timeout)
            return self.send_command(
                command,
                timeout=timeout,
                _internal_motion=True,
            )

        try:
            execute(GRBL_DIALECT.laser_off_command)
        except Exception as exc:
            failures.append(f"M5 failed: {exc}")

        if restore_idle_delay is not None:
            try:
                execute(session.format_step_idle_delay(restore_idle_delay))
            except Exception as exc:
                failures.append(f"idle-delay restore failed: {exc}")

        try:
            execute(session.motor_disable_command)
            self._append_log("INFO", f"{context}: motors released with $MD")
        except MachineError as disable_error:
            self._append_log(
                "INFO",
                f"{context}: $MD unavailable ({disable_error}); falling back to GRBL sleep",
            )
            try:
                execute(session.motor_sleep_command)
                time.sleep(session.sleep_before_reset_seconds)
                with self._transport_write_lock:
                    if job_execution and self._current_job_stop_event().is_set():
                        raise MachineError("Job stopped")
                    controller_session.transport.write_raw(session.soft_reset_command)
                self._append_log("TX", f"GRBL soft reset after {context} $SLP")
                self._mark_controller_command_state_untrusted(
                    controller_session,
                    reason=(
                        f"GRBL soft reset after {context} requires a fresh synchronized session"
                    ),
                )
                raise MachineError(
                    "GRBL sleep fallback reset the controller; a fresh synchronized "
                    "session and explicit Home are required"
                )
            except Exception as exc:
                failures.append(f"explicit motor release failed: {exc}")
        finally:
            self._invalidate_coordinate_reference()
            if not job_execution:
                with self._lock:
                    if (
                        self._session is not None
                        and self._controller_state is ControllerState.READY_MOTION
                    ):
                        self._set_controller_state_locked(
                            ControllerState.READY_HOME_REQUIRED,
                            session=self._session,
                        )

        if failures:
            raise MachineError(f"{context} cleanup incomplete: {'; '.join(failures)}")

    @contextmanager
    def temporary_stepper_hold(self):
        """Keep GRBL steppers energized only for a scoped camera operation."""

        with self._command_lock:
            with self._temporary_stepper_hold_locked():
                yield

    @contextmanager
    def _temporary_stepper_hold_locked(self):
        """Implement a camera hold while the caller owns controller replies."""

        self._require_safety_configuration()
        if self.settings.backend != "serial" or self._dialect is not GRBL_DIALECT:
            yield
            return
        controller_session = self._require_session()
        session = GRBL_DIALECT.grbl_session
        assert session is not None
        responses = self.send_command(
            session.settings_query_command,
            timeout=max(6.0, self.settings.read_timeout),
        )
        original = self._reported_grbl_step_idle_delay(responses)
        if original is None:
            raise MachineError("GRBL did not report $1, so temporary stepper holding was not started")
        restore_delay = (
            int(self.settings.grbl_step_idle_delay_ms)
            if original == session.held_step_idle_delay_ms
            else original
        )
        operation_error: BaseException | None = None
        try:
            if original != session.held_step_idle_delay_ms:
                self.send_command(
                    session.format_step_idle_delay(session.held_step_idle_delay_ms),
                    timeout=max(6.0, self.settings.read_timeout),
                    _internal_motion=True,
                )
                self._append_log("INFO", f"Temporary camera hold enabled; saved $1={original}")
            yield
        except BaseException as exc:
            operation_error = exc
            raise
        finally:
            with self._lock:
                same_session = self._same_controller_session(
                    self._session,
                    controller_session,
                )
            if same_session:
                try:
                    self._release_grbl_motors(
                        restore_idle_delay=restore_delay,
                        job_execution=False,
                        context="temporary camera hold",
                    )
                except Exception as cleanup_error:
                    self._append_log(
                        "ERROR",
                        f"Temporary camera motor-release cleanup failed: {cleanup_error}",
                    )
                    if operation_error is None:
                        raise
                    _add_exception_note(
                        operation_error,
                        f"Temporary camera motor-release cleanup also failed: {cleanup_error}"
                    )
                else:
                    self._append_log(
                        "INFO",
                        f"Temporary camera hold released; restored $1={restore_delay}",
                    )

    def _wait_until_idle(self, timeout: float = 120.0) -> list[str]:
        dialect = self._require_resolved_dialect()
        transport = self._require_connection()
        if dialect.realtime_status_query is None:
            return self.send_command(
                dialect.motion_barrier_command,
                timeout=timeout,
                _internal_motion=True,
            )

        deadline = time.monotonic() + timeout
        responses: list[str] = []
        while time.monotonic() < deadline:
            transport.write_raw(dialect.realtime_status_query)
            self._append_log("TX", "?")
            query_deadline = min(deadline, time.monotonic() + 0.8)
            while time.monotonic() < query_deadline:
                line = transport.read_line(timeout=min(0.15, query_deadline - time.monotonic()))
                if not line:
                    continue
                responses.append(line)
                self._append_log("RX", line)
                lower = line.lower()
                if lower.startswith("<idle"):
                    return responses
                if lower.startswith("<alarm") or lower.startswith("alarm") or lower.startswith("error"):
                    raise MachineError(line)
            time.sleep(0.1)
        raise MachineError(f"Controller did not become idle within {timeout:g} seconds")

    def _wait_for_motion_complete(
        self,
        timeout: float = 120.0,
        *,
        expected_stop_epoch: int | None = None,
    ) -> list[str]:
        """Wait behind all previously accepted planner motion.

        GRBL's short positive ``G4`` dwell enters the planner synchronization
        path after preceding motion. This is more portable across GRBL-derived controllers than
        requiring a particular realtime ``?`` status-report shape. Marlin has
        a dedicated synchronization command.
        """

        command = self._require_resolved_dialect().motion_barrier_command
        return self.send_command(
            command,
            timeout=timeout,
            _internal_motion=True,
            _expected_stop_epoch=expected_stop_epoch,
        )

    def _execute_grbl_homing_exchange(
        self,
        *,
        timeout: float,
        write_homing_command: Callable[[], None],
        is_cancelled: Callable[[], bool],
        cancellation_message: str,
        session: ControllerSession | None = None,
    ) -> list[str]:
        """Complete a written ``$H`` with one shared GRBL acceptance policy.

        A normal terminal acknowledgement remains authoritative.  Otherwise,
        fallback success requires a controller-reported active homing state
        followed by Idle.  Idle alone is deliberately ambiguous.
        """

        if is_cancelled():
            raise MachineError(cancellation_message)
        homing = GRBL_DIALECT.homing
        assert homing.realtime_status_query is not None
        if session is None:
            session = self._require_session()
        transport = session.transport
        responses: list[str] = []
        saw_active_homing = False
        deadline = time.monotonic() + timeout
        transaction_started = time.monotonic()
        sequence = session.diagnostics.next_command(GRBL_DIALECT.homing.command)
        next_status_query = (
            time.monotonic() + _GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS
        )

        try:
            write_homing_command()
            while time.monotonic() < deadline:
                if is_cancelled():
                    raise MachineError(cancellation_message)
                now = time.monotonic()
                if now >= next_status_query:
                    transport.write_raw(homing.realtime_status_query)
                    self._append_log("TX", "?")
                    next_status_query = (
                        now + _GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS
                    )
                line = transport.read_line(
                    timeout=min(0.1, max(0.0, deadline - time.monotonic()))
                )
                if not line:
                    continue
                if is_cancelled():
                    raise MachineError(cancellation_message)
                responses.append(line)
                self._append_log("RX", line)
                session.diagnostics.record_rx(sequence, line)
                response_kind = GRBL_DIALECT.classify_homing_response(line)
                if response_kind is HomingResponseKind.ACKNOWLEDGEMENT:
                    self._reject_queued_unowned_responses(
                        session,
                        command_sequence=sequence,
                        transaction_deadline=deadline,
                    )
                    session.diagnostics.record_success(
                        sequence,
                        started_monotonic=transaction_started,
                        terminal_classification=response_kind.value,
                    )
                    LOGGER.info(
                        "primary controller homing transaction generation=%d "
                        "endpoint=%s protocol=%s sequence=%d command='$H' "
                        "duration_seconds=%.6f terminal=%s",
                        session.generation,
                        session.resolved_endpoint,
                        session.dialect.id,
                        sequence,
                        max(0.0, time.monotonic() - transaction_started),
                        response_kind.value,
                    )
                    return responses
                if response_kind is HomingResponseKind.REJECTION:
                    raise _ControllerCommandRejected(line)
                if response_kind is HomingResponseKind.STARTUP:
                    raise MachineError(
                        "Controller restarted during the in-flight GRBL homing exchange"
                    )
                if response_kind is HomingResponseKind.MALFORMED:
                    raise MachineError(
                        f"Controller returned a malformed GRBL homing frame: {line!r}"
                    )
                if response_kind is HomingResponseKind.ACTIVE:
                    saw_active_homing = True
                elif (
                    response_kind is HomingResponseKind.IDLE
                    and saw_active_homing
                    and homing.accepts_active_to_idle_without_ack
                ):
                    responses.extend(
                        self._reject_queued_unowned_responses(
                            session,
                            command_sequence=sequence,
                            transaction_deadline=deadline,
                            allow_one_acknowledgement=True,
                        )
                    )
                    session.diagnostics.record_success(
                        sequence,
                        started_monotonic=transaction_started,
                        terminal_classification="active_to_idle",
                    )
                    LOGGER.info(
                        "primary controller homing transaction generation=%d "
                        "endpoint=%s protocol=%s sequence=%d command='$H' "
                        "duration_seconds=%.6f terminal=active_to_idle",
                        session.generation,
                        session.resolved_endpoint,
                        session.dialect.id,
                        sequence,
                        max(0.0, time.monotonic() - transaction_started),
                    )
                    self._append_log(
                        "INFO",
                        "GRBL homing completed from active-to-idle realtime status evidence without a terminal ok",
                    )
                    return responses
            raise MachineError(
                "Controller did not provide an acknowledgement or a verified active-to-idle homing transition "
                f"within {timeout:g} seconds"
            )
        except Exception as exc:
            stopped = is_cancelled()
            if not isinstance(exc, _ControllerCommandRejected):
                failure_code = (
                    _FAILURE_COMMAND_TIMEOUT
                    if "did not provide" in str(exc)
                    else _FAILURE_SESSION_QUARANTINED
                )
                self._mark_controller_command_state_untrusted(
                    session,
                    reason=f"GRBL homing exchange became ambiguous: {exc}",
                    failure_code=failure_code,
                    failed_command=GRBL_DIALECT.homing.command,
                )
            if stopped:
                raise MachineError(cancellation_message) from exc
            if isinstance(exc, _ControllerCommandRejected):
                raise MachineError(f"Command '$H' failed: {exc}") from exc
            if isinstance(exc, MachineError) and str(exc).startswith("Command '$H'"):
                raise
            raise MachineError(f"Command '$H' failed: {exc}") from exc

    def _execute_grbl_homing_locked(
        self,
        *,
        timeout: float,
        expected_stop_epoch: int,
    ) -> list[str]:
        """Run ``$H`` while an ordinary operation owns the command lock."""

        session = self._require_session()

        def is_cancelled() -> bool:
            with self._stop_epoch_lock:
                return self._stop_epoch != expected_stop_epoch

        def write_homing_command() -> None:
            with self._transport_write_lock:
                if is_cancelled():
                    raise MachineError("Home / park was cancelled by software STOP")
                self._check_line_safety(GRBL_DIALECT.homing.command)
                with self._lock:
                    if not self._same_controller_session(self._session, session):
                        raise MachineError(
                            "Home / park controller session was superseded"
                        )
                try:
                    session.transport.write_line(GRBL_DIALECT.homing.command)
                    self._append_log("TX", GRBL_DIALECT.homing.command)
                except Exception as exc:
                    raise MachineError(f"Command '$H' failed while writing: {exc}") from exc

        return self._execute_grbl_homing_exchange(
            timeout=timeout,
            write_homing_command=write_homing_command,
            is_cancelled=is_cancelled,
            cancellation_message="Home / park was cancelled by software STOP",
            session=session,
        )

    def _execute_running_job_grbl_homing(self, *, timeout: float) -> list[str]:
        """Run ``$H`` while the active job owns transport access."""

        context = self._current_job_run_context()
        if context is None:
            raise MachineError("Controller job context is unavailable")
        return self._execute_grbl_homing_exchange(
            timeout=timeout,
            write_homing_command=lambda: self._write_running_job_line(
                GRBL_DIALECT.homing.command,
                track_transaction=False,
            ),
            is_cancelled=context.stop_event.is_set,
            cancellation_message="Job stopped",
            session=context.session,
        )

    @staticmethod
    def _parse_grbl_coordinate_state(
        modal_responses: list[str],
        offset_responses: list[str],
    ) -> dict[str, Any]:
        return parse_grbl_coordinate_state(modal_responses, offset_responses)

    def _read_grbl_coordinate_state(self) -> dict[str, Any]:
        modal_command, offsets_command = GRBL_DIALECT.coordinate_state_query_commands
        modal = self.send_command(
            modal_command,
            timeout=max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout),
        )
        offsets = self.send_command(
            offsets_command,
            timeout=max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout),
        )
        return self._parse_grbl_coordinate_state(modal, offsets)

    @classmethod
    def _parse_grbl_realtime_status(
        cls,
        response: str,
        *,
        coordinate_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return parse_grbl_realtime_status(
            response,
            coordinate_state=coordinate_state,
        )

    def sample_realtime_position(
        self,
        timeout: float = 1.5,
        *,
        coordinate_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read one diagnostic GRBL position snapshot using only ``?``."""

        if type(timeout) not in {int, float} or not math.isfinite(float(timeout)):
            raise MachineError("Realtime position timeout must be a finite number")
        timeout_seconds = float(timeout)
        if timeout_seconds <= 0.0 or timeout_seconds > 10.0:
            raise MachineError(
                "Realtime position timeout must be greater than 0 and at most 10 seconds"
            )
        with self._command_lock:
            if self._job.running:
                raise MachineError("Cannot sample realtime position while a job is running")
            controller_session = self._require_session()
            transport = controller_session.transport
            dialect = self._require_resolved_dialect()
            if dialect.realtime_status_query is None:
                raise MachineError(
                    "Realtime MPos/WPos/WCO sampling is currently available only for GRBL"
                )
            with self._transport_write_lock:
                transaction_started = time.monotonic()
                sequence = controller_session.diagnostics.next_command("?")
                try:
                    transport.write_raw(dialect.realtime_status_query)
                except Exception as exc:
                    self._mark_controller_command_state_untrusted(
                        controller_session,
                        reason=f"Realtime position query write failed: {exc}",
                    )
                    raise MachineError(
                        f"Realtime position query failed while writing: {exc}"
                    ) from exc
                self._append_log("TX", "? (realtime position snapshot)")
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                response = transport.read_line(
                    timeout=min(0.2, max(0.0, deadline - time.monotonic()))
                )
                if not response:
                    continue
                self._append_log("RX", response)
                controller_session.diagnostics.record_rx(sequence, response)
                stripped = response.strip()
                if stripped.startswith("<") and stripped.endswith(">"):
                    snapshot = self._parse_grbl_realtime_status(
                        stripped,
                        coordinate_state=(
                            self._coordinate_state_reference
                            if coordinate_state is None
                            else coordinate_state
                        ),
                    )
                    if snapshot["xy_complete"] is not True:
                        raise MachineError(
                            "GRBL realtime status did not provide a complete XY position"
                        )
                    snapshot["sampled_at"] = time.time()
                    try:
                        self._reject_queued_unowned_responses(
                            controller_session,
                            command_sequence=sequence,
                            transaction_deadline=deadline,
                        )
                    except Exception as exc:
                        self._mark_controller_command_state_untrusted(
                            controller_session,
                            reason=(
                                "Realtime position response boundary became "
                                f"ambiguous: {exc}"
                            ),
                            failure_code=_FAILURE_SESSION_QUARANTINED,
                            failed_command="?",
                        )
                        raise MachineError(
                            "Controller response alignment became uncertain after "
                            "realtime sampling"
                        ) from exc
                    controller_session.diagnostics.record_success(
                        sequence,
                        started_monotonic=transaction_started,
                        terminal_classification=CommandResponseKind.REALTIME_STATUS.value,
                    )
                    return snapshot
                response_kind = controller_session.dialect.classify_command_response(
                    stripped
                )
                if response_kind in {
                    CommandResponseKind.ALARM,
                    CommandResponseKind.ERROR,
                }:
                    raise MachineError(
                        f"GRBL rejected realtime position sampling: {stripped}"
                    )
                if response_kind is CommandResponseKind.ACKNOWLEDGEMENT:
                    self._mark_controller_command_state_untrusted(
                        controller_session,
                        reason="Unowned acknowledgement arrived during realtime position sampling",
                    )
                    raise MachineError(
                        "Controller response alignment is uncertain after an unowned acknowledgement"
                    )
                if response_kind in {
                    CommandResponseKind.STARTUP,
                    CommandResponseKind.MALFORMED,
                }:
                    self._mark_controller_command_state_untrusted(
                        controller_session,
                        reason=(
                            "Controller restart or malformed frame arrived during "
                            "realtime position sampling"
                        ),
                    )
                    raise MachineError(
                        "Controller response alignment became uncertain during realtime sampling"
                    )
            raise MachineError(
                "GRBL did not return realtime position status within "
                f"{timeout_seconds:g} seconds"
            )

    @staticmethod
    def _coordinate_state_difference(
        reference: dict[str, Any], current: dict[str, Any]
    ) -> str | None:
        if current["active_workspace"] != reference["active_workspace"]:
            return (
                f"active workspace changed from {reference['active_workspace']} "
                f"to {current['active_workspace']}"
            )
        for label, key in (
            (current["active_workspace"], "active_offset_mm"),
            ("G92", "g92_offset_mm"),
        ):
            before = reference[key]
            after = current[key]
            if any(
                abs(float(after[index]) - float(before[index]))
                > _GRBL_COORDINATE_EPSILON_MM
                for index in range(3)
            ):
                return f"{label} changed from {before} mm to {after} mm"
        return None

    def _verify_grbl_coordinate_state(self) -> dict[str, Any]:
        controller_session = self._require_session()
        current = self._read_grbl_coordinate_state()
        reference = self._coordinate_state_reference
        if reference is None:
            raise SafetyError(
                "Home / park did not record a GRBL work-coordinate reference for this connection"
            )
        difference = self._coordinate_state_difference(reference, current)
        if difference is not None:
            self._invalidate_coordinate_reference()
            with self._lock:
                if (
                    self._same_controller_session(
                        self._session,
                        controller_session,
                    )
                    and self._controller_state is ControllerState.READY_MOTION
                ):
                    self._set_controller_state_locked(
                        ControllerState.READY_HOME_REQUIRED,
                        session=controller_session,
                    )
            raise SafetyError(
                "GRBL coordinate state changed after Home / park: "
                f"{difference}. The job was blocked; Home / park and realign the camera job."
            )
        return current

    @staticmethod
    def _coordinate_state_summary(state: dict[str, Any]) -> str:
        workspace = state["active_workspace"]
        active = ",".join(f"{float(value):g}" for value in state["active_offset_mm"])
        g92 = ",".join(f"{float(value):g}" for value in state["g92_offset_mm"])
        return f"{workspace}=[{active}] mm; G92=[{g92}] mm"

    @staticmethod
    def _require_zero_xy_coordinate_offsets(state: dict[str, Any]) -> None:
        nonzero: list[str] = []
        for label, key in (
            (str(state["active_workspace"]), "active_offset_mm"),
            ("G92", "g92_offset_mm"),
        ):
            values = [float(value) for value in state[key]][:2]
            if any(abs(value) > _GRBL_COORDINATE_EPSILON_MM for value in values):
                nonzero.append(
                    f"{label} XY=[{values[0]:g},{values[1]:g}] mm"
                )
        if nonzero:
            raise SafetyError(
                "Home / park requires zero GRBL XY work offsets so configured "
                "absolute positions match the physical machine: " + "; ".join(nonzero)
            )

    def prepare_photo_position(
        self, *, capture_home_position: bool = False
    ) -> dict[str, Any]:
        """Home if configured, then park XY at the repeatable camera pose.

        This routine never emits a laser-enable command. It remains subject to
        the normal serial-hardware and ``machine.allow_motion`` gates.
        """
        if type(capture_home_position) is not bool:
            raise TypeError("capture_home_position must be an exact boolean")
        operation_stop_epoch = self._operation_stop_epoch()
        started = time.monotonic()
        with self._lock:
            attempt_session = self._session
        with self._command_lock:
            try:
                result = self._prepare_photo_position_locked(
                    operation_stop_epoch=operation_stop_epoch,
                    park_at_photo_position=True,
                    capture_home_position=capture_home_position,
                )
            except BaseException as exc:
                self._log_home_outcome(
                    session=attempt_session,
                    started=started,
                    error=exc,
                )
                raise
            self._log_home_outcome(
                session=attempt_session,
                started=started,
                error=None,
            )
            return result

    def prepare_job_start(self) -> dict[str, Any]:
        """Home once and establish coordinates without moving to the camera pose.

        This routine never emits a laser-enable command.  The validated program
        remains responsible for its initial laser-off travel from Home to the
        first toolpath position.
        """

        operation_stop_epoch = self._operation_stop_epoch()
        started = time.monotonic()
        with self._lock:
            attempt_session = self._session
        with self._command_lock:
            try:
                result = self._prepare_photo_position_locked(
                    operation_stop_epoch=operation_stop_epoch,
                    park_at_photo_position=False,
                    capture_home_position=False,
                )
            except BaseException as exc:
                self._log_home_outcome(
                    session=attempt_session,
                    started=started,
                    error=exc,
                )
                raise
            self._log_home_outcome(
                session=attempt_session,
                started=started,
                error=None,
            )
            return result

    def _log_home_outcome(
        self,
        *,
        session: ControllerSession | None,
        started: float,
        error: BaseException | None,
    ) -> None:
        duration = max(0.0, time.monotonic() - started)
        if error is None:
            LOGGER.info(
                "primary controller Home complete generation=%s endpoint=%s "
                "protocol=%s duration_seconds=%.6f state=READY_MOTION",
                "-" if session is None else session.generation,
                "-" if session is None else session.resolved_endpoint,
                "-" if session is None else session.dialect.id,
                duration,
            )
            return
        with self._lock:
            session_was_quarantined = bool(
                session is not None
                and not self._same_controller_session(self._session, session)
                and self._last_controller_failure_code is not None
            )
            if not session_was_quarantined:
                self._last_controller_failure_code = _FAILURE_HOME
                self._last_controller_failure = str(error)[:500]
                self._last_controller_failed_command = "$H"
            code = self._last_controller_failure_code or _FAILURE_HOME
        transcript: list[str] = []
        if session is not None:
            if session_was_quarantined:
                session.diagnostics.record_event(f"Home failed: {error}")
            else:
                session.diagnostics.record_failure(
                    error,
                    code=code,
                    command="$H",
                )
            transcript = session.diagnostics.snapshot()["transcript"][-8:]
        LOGGER.error(
            "primary controller Home failed generation=%s endpoint=%s protocol=%s "
            "duration_seconds=%.6f code=%s detail=%s transcript=%s",
            "-" if session is None else session.generation,
            "-" if session is None else session.resolved_endpoint,
            "-" if session is None else session.dialect.id,
            duration,
            code,
            str(error)[:500],
            " | ".join(transcript)[:1200],
        )

    def _prepare_photo_position_locked(
        self,
        *,
        operation_stop_epoch: int,
        park_at_photo_position: bool,
        capture_home_position: bool,
    ) -> dict[str, Any]:
        with self._stop_epoch_lock:
            if self._stop_epoch != operation_stop_epoch:
                raise MachineError("Home / park was cancelled by software STOP")
        self._require_safety_configuration()
        if self._job.running:
            raise MachineError("Cannot move to the photography position while a job is running")
        controller_session = self._require_session()
        with self._lock:
            if self._controller_state is not ControllerState.READY_HOME_REQUIRED:
                raise MachineError(
                    "Home / park is allowed only when the synchronized controller "
                    "session requires Home"
                )
        if self.settings.backend == "serial" and not self.settings.home_before_photo:
            raise SafetyError(
                "Serial hardware requires machine.home_before_photo=true so Home / park "
                "can establish a repeatable absolute coordinate reference"
            )
        x = float(self.settings.photo_x)
        y = float(self.settings.photo_y)
        if park_at_photo_position and not self.settings.work_area.contains(
            x,
            y,
            self.laser_settings.boundary_margin_mm,
        ):
            raise SafetyError("Configured photography position lies outside the safe work area")

        transcript: list[dict[str, Any]] = []
        idle_responses: list[str] = []
        home_position_snapshot: dict[str, Any] | None = None

        def require_not_stopped() -> None:
            with self._stop_epoch_lock:
                if self._stop_epoch != operation_stop_epoch:
                    raise MachineError("Home / park was cancelled by software STOP")

        def execute(command: str, timeout: float | None = None) -> list[str]:
            acknowledgement_timeout = (
                max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout)
                if timeout is None
                else timeout
            )
            responses = self.send_command(
                command,
                timeout=acknowledgement_timeout,
                _internal_motion=True,
                _expected_stop_epoch=operation_stop_epoch,
            )
            transcript.append({"command": command, "responses": responses})
            return responses

        self._invalidate_coordinate_reference()
        dialect = self._require_resolved_dialect()
        try:
            self._laser_off_with_pre_home_grbl_unlock(
                execute,
                context="Home / park",
            )
            if self.settings.home_before_photo:
                if dialect is GRBL_DIALECT:
                    homing_responses = self._execute_grbl_homing_locked(
                        timeout=max(
                            _GRBL_HOMING_TIMEOUT_SECONDS,
                            self.settings.read_timeout,
                        ),
                        expected_stop_epoch=operation_stop_epoch,
                    )
                    transcript.append(
                        {"command": dialect.homing.command, "responses": homing_responses}
                    )
                else:
                    execute(
                        dialect.homing.command,
                        timeout=max(
                            dialect.homing.timeout_floor_seconds,
                            self.settings.read_timeout,
                        ),
                    )
            coordinate_state = (
                self._read_grbl_coordinate_state()
                if self._uses_grbl_coordinate_state()
                else None
            )
            require_not_stopped()
            if coordinate_state is not None:
                # Work/G92 offsets affect G0 coordinates even immediately after
                # homing. Reject them before issuing the photography-position
                # move, then verify again after the move below.
                self._require_zero_xy_coordinate_offsets(coordinate_state)
            if capture_home_position and self.settings.home_before_photo:
                try:
                    home_position_snapshot = {
                        "available": True,
                        **self.sample_realtime_position(
                            coordinate_state=coordinate_state,
                        ),
                    }
                except Exception as exc:
                    home_position_snapshot = {
                        "available": False,
                        "error": str(exc),
                    }
                    self._append_log(
                        "INFO",
                        f"Post-home realtime position was unavailable: {exc}",
                    )
            execute("G21")
            execute("G90")
            if park_at_photo_position:
                execute(
                    f"G0 X{x:.3f} Y{y:.3f} F{float(self.laser_settings.travel_feed_mm_min):.3f}",
                    timeout=max(
                        _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS,
                        self.settings.read_timeout,
                    ),
                )
                idle_responses = self._wait_for_motion_complete(
                    timeout=120.0,
                    expected_stop_epoch=operation_stop_epoch,
                )
                parked_coordinate_state = (
                    self._read_grbl_coordinate_state()
                    if self._uses_grbl_coordinate_state()
                    else None
                )
                require_not_stopped()
                if parked_coordinate_state is not None:
                    self._require_zero_xy_coordinate_offsets(parked_coordinate_state)
                    coordinate_state = parked_coordinate_state
            if coordinate_state is not None:
                self._append_log(
                    "INFO",
                    ("Home / park" if park_at_photo_position else "Job-start Home")
                    + " GRBL coordinate reference: "
                    + self._coordinate_state_summary(coordinate_state),
                )
        except Exception:
            self._invalidate_coordinate_reference()
            raise
        with self._stop_epoch_lock:
            stopped = self._stop_epoch != operation_stop_epoch
        with self._lock:
            if (
                stopped
                or not self._same_controller_session(
                    self._session,
                    controller_session,
                )
                or self._controller_state
                is not ControllerState.READY_HOME_REQUIRED
            ):
                self._invalidate_coordinate_reference()
                raise MachineError(
                    "Home / park was cancelled because its controller session was stopped or superseded"
                )
            self._coordinate_reference_ready = True
            self._coordinate_reference_session_generation = (
                controller_session.generation
            )
            self._coordinate_state_reference = coordinate_state
            self._jog_position_mm = (x, y) if park_at_photo_position else None
            self._set_controller_state_locked(
                ControllerState.READY_MOTION,
                session=controller_session,
            )
        return {
            "position": (
                {"x": x, "y": y, "z": self.settings.photo_z}
                if park_at_photo_position
                else None
            ),
            "homed": self.settings.home_before_photo,
            "parked": park_at_photo_position,
            "transcript": transcript,
            "idle_responses": idle_responses,
            "coordinate_state": coordinate_state,
            "home_position_snapshot": home_position_snapshot,
            "warning": (
                "photo_position.z is recorded but is not moved automatically; set laser focus/material height manually."
                if park_at_photo_position and self.settings.photo_z is not None
                else None
            ),
        }

    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> dict[str, Any]:
        """Make one laser-off XY move from the last trusted jog pose.

        Home / park establishes the starting pose.  Incremental requests are
        converted to absolute moves so controller modal state cannot change
        their meaning. Jogging intentionally does not apply the configured work
        area because it is also the operator's tool for measuring that area.
        Any attempted jog move invalidates the cached pose until the planner
        confirms completion.
        """

        values = {
            "X jog distance": dx_mm,
            "Y jog distance": dy_mm,
            "Jog feed": feed_mm_min,
        }
        invalid_types = [
            label for label, value in values.items() if type(value) not in {int, float}
        ]
        if invalid_types:
            raise SafetyError(
                "Jog distances and feed must be finite numbers: "
                + ", ".join(invalid_types)
            )
        finite = {label: float(value) for label, value in values.items()}
        invalid = [label for label, value in finite.items() if not math.isfinite(value)]
        if invalid:
            raise SafetyError(
                "Jog distances and feed must be finite numbers: " + ", ".join(invalid)
            )
        dx = finite["X jog distance"]
        dy = finite["Y jog distance"]
        feed = finite["Jog feed"]
        if dx == 0.0 and dy == 0.0:
            raise SafetyError("Jog distance must move X or Y")
        if feed <= 0.0:
            raise SafetyError("Jog feed must be positive")

        operation_stop_epoch = self._operation_stop_epoch()
        with self._command_lock:
            with self._stop_epoch_lock:
                if self._stop_epoch != operation_stop_epoch:
                    raise MachineError("Jog was cancelled by software STOP")
            self._require_safety_configuration()
            if self.settings.allow_motion is not True:
                raise SafetyError("Jogging is disabled in machine.allow_motion")
            if feed > float(self.settings.max_travel_feed_mm_min):
                raise SafetyError(
                    f"Jog feed F{feed:g} exceeds the configured travel ceiling of "
                    f"{float(self.settings.max_travel_feed_mm_min):g} mm/min"
                )
            controller_session = self._require_session()
            if self._job.running:
                raise MachineError("Cannot jog while a controller job is running")
            if self.armed:
                raise SafetyError("Disarm laser control before jogging")
            with self._lock:
                motion_ready = bool(
                    self._controller_state is ControllerState.READY_MOTION
                    and self._coordinate_reference_ready
                    and self._coordinate_reference_session_generation
                    == controller_session.generation
                )
            if not motion_ready or self._jog_position_mm is None:
                raise SafetyError(
                    "Home / park must complete before jogging so the current XY position is known"
                )
            if self._uses_grbl_coordinate_state():
                self._verify_grbl_coordinate_state()

            current_x, current_y = self._jog_position_mm
            target_x = round(current_x + dx, 3)
            target_y = round(current_y + dy, 3)
            if target_x == current_x and target_y == current_y:
                raise SafetyError("Jog distance is below the 0.001 mm controller resolution")

            transcript: list[dict[str, Any]] = []

            def execute(command: str, timeout: float | None = None) -> None:
                responses = self.send_command(
                    command,
                    timeout=(
                        max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout)
                        if timeout is None
                        else timeout
                    ),
                    _internal_motion=True,
                    _expected_stop_epoch=operation_stop_epoch,
                )
                transcript.append({"command": command, "responses": responses})

            execute("M5")
            execute("G21")
            execute("G90")
            with self._stop_epoch_lock:
                if self._stop_epoch != operation_stop_epoch:
                    raise MachineError("Jog was cancelled by software STOP")
                # From this point the controller may accept motion.  Do not
                # expose the old position during the command/ACK window.
                self._jog_position_mm = None
            execute(f"G1 X{target_x:.3f} Y{target_y:.3f} F{feed:.3f}")
            idle_responses = self._wait_for_motion_complete(
                timeout=120.0,
                expected_stop_epoch=operation_stop_epoch,
            )
            with self._stop_epoch_lock, self._lock:
                if self._stop_epoch != operation_stop_epoch:
                    raise MachineError("Jog was cancelled by software STOP")
                if (
                    not self._same_controller_session(
                        self._session,
                        controller_session,
                    )
                    or self._controller_state is not ControllerState.READY_MOTION
                    or self._coordinate_reference_session_generation
                    != controller_session.generation
                ):
                    raise MachineError(
                        "Controller command state became untrusted during jogging; "
                        "a fresh synchronized session and explicit Home are required"
                    )
                self._jog_position_mm = (target_x, target_y)
            return {
                "position": {"x": target_x, "y": target_y},
                "delta": {"x": dx, "y": dy},
                "feed_mm_min": feed,
                "transcript": transcript,
                "idle_responses": idle_responses,
            }

    def _validate_stream_line(self, line: str) -> tuple[list[Any], set[int], set[int]]:
        if len(line) > 256:
            raise SafetyError("Single streamed G-code line exceeds 256 characters")
        commands = self._resolved_air_assist_commands()
        air_assist_kind = self._air_assist_command_kind(line, commands=commands)
        if (
            air_assist_kind is not None
            and commands is not None
            and commands.target is AirAssistTarget.PI_SECONDARY
        ):
            return [], set(), set()
        try:
            words = parse_words(line, require_full_match=True)
        except ValueError as exc:
            raise SafetyError(str(exc)) from exc
        if not words:
            raise SafetyError("Executable G-code line contains no supported words")
        g_codes = self._codes(words, "G")
        m_codes = self._codes(words, "M")
        auxiliary_m_codes = m_codes.intersection({8, 9, 106, 107})
        if auxiliary_m_codes and air_assist_kind is None:
            raise SafetyError(
                "Streamed air-assist command does not match the exact configured output"
            )
        letters = {word.letter for word in words}
        allowed_letters = _STREAM_LETTERS | ({"P"} if air_assist_kind is not None else set())
        unsupported = letters - allowed_letters
        if unsupported:
            raise SafetyError(f"Unsupported G-code word(s): {', '.join(sorted(unsupported))}")
        if any(word.letter in {"G", "M"} and abs(word.value - round(word.value)) >= 1e-9 for word in words):
            raise SafetyError("G and M codes must be whole numbers")
        if len(g_codes) > 1 or len(m_codes) > 1 or (g_codes and m_codes):
            raise SafetyError("Each streamed line may contain only one G code or one M code")
        if auxiliary_m_codes:
            return words, g_codes, m_codes
        if not g_codes.issubset(_STREAM_G_CODES):
            raise SafetyError(f"Unsupported streamed G code: {sorted(g_codes - _STREAM_G_CODES)}")
        if not m_codes.issubset(_STREAM_M_CODES):
            raise SafetyError(f"Unsupported streamed M code: {sorted(m_codes - _STREAM_M_CODES)}")

        counts: dict[str, int] = {}
        values: dict[str, float] = {}
        for word in words:
            counts[word.letter] = counts.get(word.letter, 0) + 1
            values[word.letter] = word.value
        for letter in ("G", "M", "X", "Y", "F", "S"):
            if counts.get(letter, 0) > 1:
                raise SafetyError(f"Duplicate {letter} word on streamed line")
        if "F" in values and values["F"] <= 0:
            raise SafetyError("Feed rate must be positive")
        if "F" in values and not math.isfinite(values["F"]):
            raise SafetyError("Feed rate must be finite")
        if "S" in values and not 0 <= values["S"] <= self.laser_settings.power_max:
            raise SafetyError(f"S power must be between 0 and {self.laser_settings.power_max}")

        if g_codes in ({0}, {1}):
            if "X" not in values and "Y" not in values:
                raise SafetyError("G0/G1 line must contain X and/or Y")
            if "F" not in values:
                raise SafetyError("Every G0/G1 line must include an explicit F feed rate")
            maximum_feed = (
                self.settings.max_travel_feed_mm_min
                if g_codes == {0}
                else self.settings.max_work_feed_mm_min
            )
            if values["F"] > maximum_feed:
                motion = "travel" if g_codes == {0} else "work"
                raise SafetyError(
                    f"{motion.capitalize()} feed F{values['F']:g} exceeds the "
                    f"configured {motion} ceiling of {maximum_feed:g} mm/min"
                )
            allowed_motion_letters = {"G", "X", "Y", "F"}
            if g_codes == {1}:
                allowed_motion_letters.add("S")
            if any(word.letter not in allowed_motion_letters for word in words):
                raise SafetyError(
                    "Only X, Y, F, and bounded inline S on G1 are permitted "
                    "on streamed motion lines"
                )
        elif g_codes in ({21}, {90}):
            if len(words) != 1:
                raise SafetyError("G21 and G90 must be standalone lines")
        elif m_codes in ({3}, {4}):
            if counts.get("S", 0) != 1 or len(words) != 2:
                raise SafetyError("M3/M4 must include exactly one S value on the same line")
        elif m_codes == {5}:
            if len(words) != 1:
                raise SafetyError("M5 must be a standalone line")
        else:
            raise SafetyError("Unsupported streamed G-code line")
        return words, g_codes, m_codes

    def _configured_guarded_output_polygon(self) -> ConvexPolygon | None:
        configured = self.laser_settings.guarded_output_polygon_mm
        if configured is None:
            return None
        try:
            return normalize_convex_polygon(
                configured,
                label="laser.guarded_output_polygon_mm",
            )
        except ValueError as exc:
            raise SafetyError(str(exc)) from exc

    def _resolve_guarded_output_polygon(
        self,
        requested: tuple[tuple[float, float], ...] | None,
    ) -> ConvexPolygon | None:
        if requested is None:
            return None
        configured = self._configured_guarded_output_polygon()
        if configured is None:
            raise SafetyError(
                "A support-bound output polygon was requested, but none is configured"
            )
        try:
            normalized = normalize_convex_polygon(
                requested,
                label="guarded output polygon",
            )
        except ValueError as exc:
            raise SafetyError(str(exc)) from exc
        if normalized != configured:
            raise SafetyError(
                "The requested guarded output polygon does not match the configured authority"
            )
        return normalized

    def _analyze_program(
        self,
        text: str,
        guarded_output_polygon_mm: ConvexPolygon | None = None,
    ) -> tuple[list[str], bool]:
        self._require_safety_configuration()
        guarded_polygon = self._resolve_guarded_output_polygon(
            guarded_output_polygon_mm
        )
        air_assist_commands = self._resolved_air_assist_commands()
        lines: list[str] = []
        for raw_line_number, raw_line in enumerate(text.splitlines(), start=1):
            instruction = raw_line.strip()
            try:
                air_assist_kind = self._air_assist_command_kind(
                    instruction,
                    commands=air_assist_commands,
                )
            except SafetyError as exc:
                raise SafetyError(
                    f"Line {raw_line_number}: invalid E3 air-assist instruction: {exc}"
                ) from exc
            if (
                air_assist_kind is not None
                and air_assist_commands is not None
                and air_assist_commands.target is AirAssistTarget.PI_SECONDARY
            ):
                lines.append(instruction)
                continue
            cleaned = strip_comment(raw_line)
            if cleaned:
                lines.append(cleaned)
        if not lines:
            raise SafetyError("G-code program is empty")
        if len(lines) > 250_000:
            raise SafetyError("G-code program exceeds the 250,000-line safety limit")

        seen_mm = False
        seen_absolute = False
        seen_initial_m5 = False
        laser_on = False
        air_assist_on = False
        seen_air_assist_command = False
        air_interval_had_powered_motion = False
        last_air_assist_kind: str | None = None
        requires_laser_authorization = False
        position_established = False
        x: float | None = None
        y: float | None = None
        last_m_code: int | None = None
        last_line_is_m5 = False

        for index, line in enumerate(lines, start=1):
            words, g_codes, m_codes = self._validate_stream_line(line)
            last_line_is_m5 = m_codes == {5}
            values = {word.letter: word.value for word in words}
            air_assist_kind = self._air_assist_command_kind(
                line,
                commands=air_assist_commands,
            )
            try:
                self._check_line_safety(
                    line,
                    job_execution=True,
                    preflight=True,
                )
            except SafetyError as exc:
                raise SafetyError(f"Line {index}: {exc}") from exc

            if g_codes == {21}:
                seen_mm = True
            elif g_codes == {90}:
                seen_absolute = True
            elif g_codes in ({0}, {1}):
                if not seen_mm or not seen_absolute:
                    raise SafetyError(f"Line {index}: G21 and G90 must appear before motion")
                if not seen_initial_m5:
                    raise SafetyError(f"Line {index}: M5 must appear before the first motion")
                if "S" in values:
                    if g_codes != {1} or last_m_code not in {3, 4}:
                        raise SafetyError(
                            f"Line {index}: inline S is allowed only on G1 after M3/M4"
                        )
                    requires_laser_authorization = True
                    laser_on = values["S"] > 0
                if x is None or y is None:
                    if "X" not in values or "Y" not in values:
                        raise SafetyError(f"Line {index}: the first move must establish both X and Y")
                new_x = values.get("X", x)
                new_y = values.get("Y", y)
                assert new_x is not None and new_y is not None
                has_xy_displacement = (
                    x is not None
                    and y is not None
                    and math.hypot(new_x - x, new_y - y)
                    > _NONZERO_OUTPUT_MOTION_EPSILON_MM
                )
                controller_allowed = (
                    convex_polygon_contains_normalized(
                        (new_x, new_y), guarded_polygon
                    )
                    if guarded_polygon is not None
                    else self.settings.work_area.contains(
                        new_x,
                        new_y,
                        self.laser_settings.boundary_margin_mm,
                    )
                )
                if not controller_allowed:
                    raise SafetyError(
                        f"Line {index}: G-code point X{new_x:.3f} Y{new_y:.3f} "
                        "lies outside the configured guarded output authority"
                    )
                spot_x = new_x + float(self.laser_settings.spot_offset_x_mm)
                spot_y = new_y + float(self.laser_settings.spot_offset_y_mm)
                spot_allowed = (
                    convex_polygon_contains_normalized(
                        (spot_x, spot_y), guarded_polygon
                    )
                    if guarded_polygon is not None
                    else self.settings.work_area.contains(
                        spot_x,
                        spot_y,
                        self.laser_settings.boundary_margin_mm,
                    )
                )
                if not spot_allowed:
                    raise SafetyError(
                        f"Line {index}: physical laser spot X{spot_x:.3f} "
                        f"Y{spot_y:.3f} lies outside the configured guarded output authority"
                    )
                if g_codes == {0} and laser_on:
                    raise SafetyError(f"Line {index}: rapid G0 motion is blocked while the laser is enabled")
                x, y = new_x, new_y
                position_established = True
                if (
                    g_codes == {1}
                    and has_xy_displacement
                    and laser_on
                    and air_assist_on
                ):
                    air_interval_had_powered_motion = True

            if m_codes in ({3}, {4}):
                if not seen_mm or not seen_absolute or not seen_initial_m5:
                    raise SafetyError(f"Line {index}: G21, G90, and an initial M5 are required before laser enable")
                if not position_established:
                    raise SafetyError(
                        f"Line {index}: an absolute XY move with the laser off "
                        "is required before laser enable"
                    )
                requires_laser_authorization = True
                laser_on = values["S"] > 0
                last_m_code = next(iter(m_codes))
            elif m_codes == {5}:
                laser_on = False
                seen_initial_m5 = True
                last_m_code = 5

            if air_assist_kind is not None:
                if not seen_mm or not seen_absolute or not seen_initial_m5:
                    raise SafetyError(
                        f"Line {index}: G21, G90, and an initial M5 are required "
                        "before air-assist commands"
                    )
                if not seen_air_assist_command and air_assist_kind != "off":
                    raise SafetyError(
                        f"Line {index}: the first air-assist command must establish OFF"
                    )
                if air_assist_kind == "on" and last_air_assist_kind == "on":
                    raise SafetyError(
                        f"Line {index}: duplicate air-assist {air_assist_kind.upper()} command"
                    )
                if air_assist_kind == "on":
                    if laser_on:
                        raise SafetyError(
                            f"Line {index}: air assist must be enabled while the laser is off"
                        )
                    if not position_established:
                        raise SafetyError(
                            f"Line {index}: establish an absolute XY position before enabling air assist"
                    )
                    air_assist_on = True
                    air_interval_had_powered_motion = False
                else:
                    if laser_on:
                        raise SafetyError(
                            f"Line {index}: M5 must disable the laser before air assist is disabled"
                        )
                    if air_assist_on and not air_interval_had_powered_motion:
                        raise SafetyError(
                            f"Line {index}: air-assist ON interval contained no powered work motion"
                        )
                    air_assist_on = False
                seen_air_assist_command = True
                last_air_assist_kind = air_assist_kind

        if not seen_mm or not seen_absolute:
            raise SafetyError("Program must explicitly set millimetres (G21) and absolute positioning (G90)")
        if not seen_initial_m5:
            raise SafetyError("Program must contain M5 before motion or laser commands")
        if laser_on or last_m_code != 5 or not last_line_is_m5:
            raise SafetyError("Program must end with a standalone M5 laser-off command")
        if air_assist_on:
            raise SafetyError("Program must disable air assist before its final M5")
        return lines, requires_laser_authorization

    def _require_safety_configuration(self) -> None:
        if type(self.settings.backend) is not str or self.settings.backend != "serial":
            raise SafetyError("Machine backend must be exactly 'serial'")
        if type(self.settings.protocol) is not str or self.settings.protocol not in {
            "auto",
            "grbl",
            "marlin",
        }:
            raise SafetyError("Machine protocol must be exactly auto, grbl, or marlin")
        self._resolved_air_assist_commands()

        boolean_values = {
            "machine.allow_motion": self.settings.allow_motion,
            "machine.home_before_photo": self.settings.home_before_photo,
            "machine.home_and_release_after_powered_job": (
                self.settings.home_and_release_after_powered_job
            ),
            "process.hardware_enabled": self.hardware_enabled,
            "process.laser_lockout": self.laser_lockout,
        }
        invalid_booleans = [
            label for label, value in boolean_values.items() if type(value) is not bool
        ]
        if invalid_booleans:
            raise SafetyError(
                "Machine safety gates must be exact booleans: "
                + ", ".join(invalid_booleans)
            )

        area = self.settings.work_area
        values = {
            "machine.work_area.x_min": area.x_min,
            "machine.work_area.x_max": area.x_max,
            "machine.work_area.y_min": area.y_min,
            "machine.work_area.y_max": area.y_max,
            "machine.max_travel_feed_mm_min": self.settings.max_travel_feed_mm_min,
            "machine.max_work_feed_mm_min": self.settings.max_work_feed_mm_min,
            "machine.read_timeout": self.settings.read_timeout,
            "machine.controller_startup_delay": self.settings.controller_startup_delay,
            "machine.photo_x": self.settings.photo_x,
            "machine.photo_y": self.settings.photo_y,
            "laser.boundary_margin_mm": self.laser_settings.boundary_margin_mm,
            "laser.spot_offset_x_mm": self.laser_settings.spot_offset_x_mm,
            "laser.spot_offset_y_mm": self.laser_settings.spot_offset_y_mm,
            "laser.travel_feed_mm_min": self.laser_settings.travel_feed_mm_min,
        }
        if self.settings.photo_z is not None:
            values["machine.photo_z"] = self.settings.photo_z
        invalid_numeric_types = [
            label for label, value in values.items() if type(value) not in {int, float}
        ]
        if invalid_numeric_types:
            raise SafetyError(
                "Machine safety settings must be finite numbers: "
                + ", ".join(invalid_numeric_types)
            )
        finite = {label: float(value) for label, value in values.items()}
        invalid = [label for label, value in finite.items() if not math.isfinite(value)]
        if invalid:
            raise SafetyError(
                "Machine safety settings must be finite numbers: " + ", ".join(invalid)
            )
        if (
            finite["machine.work_area.x_max"] <= finite["machine.work_area.x_min"]
            or finite["machine.work_area.y_max"] <= finite["machine.work_area.y_min"]
        ):
            raise SafetyError("Machine work-area bounds are invalid")
        if (
            finite["machine.max_travel_feed_mm_min"] <= 0
            or finite["machine.max_work_feed_mm_min"] <= 0
        ):
            raise SafetyError("Machine feed ceilings must be positive")
        if finite["machine.read_timeout"] <= 0:
            raise SafetyError("Machine read timeout must be positive")
        if finite["machine.controller_startup_delay"] < 0:
            raise SafetyError("Machine controller startup delay cannot be negative")
        arm_timeout = self.laser_settings.arm_timeout_seconds
        if type(arm_timeout) is not int or not 1 <= arm_timeout <= _MAX_ARM_TIMEOUT_SECONDS:
            raise SafetyError(
                "Laser arm timeout must be an integer from 1 through "
                f"{_MAX_ARM_TIMEOUT_SECONDS} seconds"
            )
        travel_feed = finite["laser.travel_feed_mm_min"]
        if travel_feed <= 0:
            raise SafetyError("Laser travel feed must be positive")
        if travel_feed > finite["machine.max_travel_feed_mm_min"]:
            raise SafetyError(
                "Laser travel feed exceeds the configured machine travel ceiling"
            )
        margin = finite["laser.boundary_margin_mm"]
        width = finite["machine.work_area.x_max"] - finite["machine.work_area.x_min"]
        height = finite["machine.work_area.y_max"] - finite["machine.work_area.y_min"]
        if margin < 0 or margin * 2 >= min(width, height):
            raise SafetyError("Laser boundary margin leaves no valid work area")
        if type(self.laser_settings.power_max) is not int or self.laser_settings.power_max <= 0:
            raise SafetyError("Laser power ceiling must be a positive integer")
        if type(self.settings.baudrate) is not int or self.settings.baudrate <= 0:
            raise SafetyError("Machine baud rate must be a positive integer")
        if (
            type(self.settings.grbl_step_idle_delay_ms) is not int
            or not 0 <= self.settings.grbl_step_idle_delay_ms < 255
        ):
            raise SafetyError("GRBL step idle delay must be an integer from 0 through 254")
        if type(self.settings.port) is not str or not self.settings.port.strip():
            raise SafetyError("Machine controller port must be a non-empty string")

    def validate_program(self, text: str) -> list[str]:
        return list(self.preflight_program(text).lines)

    def _program_safety_profile(
        self,
        guarded_output_polygon_mm: ConvexPolygon | None = None,
    ) -> tuple[Any, ...]:
        self._require_safety_configuration()
        area = self.settings.work_area
        air_assist = self._resolved_air_assist_commands()
        air_assist_profile = (
            None
            if air_assist is None
            else (
                air_assist.mode.value,
                air_assist.target.value,
                air_assist.protocol,
                air_assist.fan_index,
                air_assist.port,
                air_assist.baudrate,
                air_assist.on_commands,
                air_assist.off_commands,
                air_assist.mapping_digest,
            )
        )
        return (
            self.settings.backend,
            self.settings.protocol,
            self.settings.allow_motion,
            self.hardware_enabled,
            self.laser_lockout,
            self.settings.home_before_photo,
            self.settings.home_and_release_after_powered_job,
            float(area.x_min),
            float(area.x_max),
            float(area.y_min),
            float(area.y_max),
            float(self.laser_settings.boundary_margin_mm),
            float(self.laser_settings.spot_offset_x_mm),
            float(self.laser_settings.spot_offset_y_mm),
            int(self.laser_settings.power_max),
            float(self.settings.max_travel_feed_mm_min),
            float(self.settings.max_work_feed_mm_min),
            float(self.laser_settings.travel_feed_mm_min),
            self.laser_settings.arm_timeout_seconds,
            float(self.settings.photo_x),
            float(self.settings.photo_y),
            None if self.settings.photo_z is None else float(self.settings.photo_z),
            self._configured_guarded_output_polygon(),
            guarded_output_polygon_mm,
            air_assist_profile,
        )

    def preflight_program(
        self,
        text: str,
        *,
        guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None,
    ) -> ValidatedProgram:
        guarded_polygon = self._resolve_guarded_output_polygon(
            guarded_output_polygon_mm
        )
        lines, requires_laser_authorization = self._analyze_program(
            text,
            guarded_polygon,
        )
        if self.laser_lockout and requires_laser_authorization:
            raise SafetyError(
                "Program contains laser-enable commands while process laser lockout is active"
            )
        canonical = "\n".join(lines).encode("utf-8")
        air_assist_commands = self._resolved_air_assist_commands()
        return ValidatedProgram(
            lines=tuple(lines),
            digest=hashlib.sha256(canonical).hexdigest(),
            requires_laser_authorization=requires_laser_authorization,
            requires_motion=any(_program_line_contains_motion(line) for line in lines),
            safety_profile=self._program_safety_profile(guarded_polygon),
            air_assist_commands=air_assist_commands,
            guarded_output_polygon_mm=guarded_polygon,
        )

    def _require_current_safety_profile(self, program: ValidatedProgram) -> None:
        self._require_validated_program_integrity(program)

    def _require_validated_program_integrity(self, program: ValidatedProgram) -> None:
        if type(program) is not ValidatedProgram:
            raise SafetyError("Program authorization requires an exact preflight result")
        if type(program.lines) is not tuple or any(
            type(line) is not str for line in program.lines
        ):
            raise SafetyError("Program authorization contains invalid line data")
        if type(program.digest) is not str:
            raise SafetyError("Program authorization contains an invalid digest")
        if (
            type(program.requires_laser_authorization) is not bool
            or type(program.requires_motion) is not bool
            or type(program.safety_profile) is not tuple
            or (
                program.air_assist_commands is not None
                and type(program.air_assist_commands) is not AirAssistCommands
            )
            or (
                program.guarded_output_polygon_mm is not None
                and type(program.guarded_output_polygon_mm) is not tuple
            )
        ):
            raise SafetyError("Program authorization contains invalid safety metadata")

        lines, requires_laser_authorization = self._analyze_program(
            "\n".join(program.lines),
            program.guarded_output_polygon_mm,
        )
        canonical_lines = tuple(lines)
        canonical_digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
        requires_motion = any(_program_line_contains_motion(line) for line in lines)
        current_profile = self._program_safety_profile(
            program.guarded_output_polygon_mm
        )
        current_air_assist_commands = self._resolved_air_assist_commands()
        if (
            program.lines != canonical_lines
            or program.digest != canonical_digest
            or program.requires_laser_authorization is not requires_laser_authorization
            or program.requires_motion is not requires_motion
            or program.safety_profile != current_profile
            or program.air_assist_commands != current_air_assist_commands
        ):
            raise SafetyError(
                "Program lines, digest, flags, machine bounds, offsets, feed ceilings, "
                "air-assist mapping, or hardware gates changed after program preflight; validate the exact "
                "program again"
            )

    def start_validated_program(
        self,
        program: ValidatedProgram,
        name: str = "generated.gcode",
        *,
        _expected_stop_epoch: int | None = None,
    ) -> dict[str, Any]:
        # A STOP that arrives after this request begins must cancel this exact
        # start, even if it lands during controller-state verification.
        start_stop_epoch = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._command_lock:
            try:
                self._require_current_safety_profile(program)
                return self._start_validated_program(
                    program,
                    name,
                    start_stop_epoch=start_stop_epoch,
                )
            except Exception:
                with self._stop_epoch_lock:
                    if self._stop_epoch == start_stop_epoch:
                        self._clear_arm_authorization()
                raise

    def start_preflighted_program(
        self,
        program: ValidatedProgram,
        name: str = "generated.gcode",
        *,
        authorization_phrase: str | None = None,
    ) -> dict[str, Any]:
        """Run the complete guarded local start sequence for one exact program.

        Remote Pi-owned execution implements the same public seam, but transfers
        the immutable program to the node before its local MachineService performs
        these controller-side steps.  Keeping the desktop handoff at this level
        prevents UI code from pretending that a network transport owns execution.
        """

        # Revalidate at the public start boundary. Physical Home is an explicit
        # operator action: Start may use only the coordinate authority already
        # established for this exact READY_MOTION session and never homes again.
        self._require_current_safety_profile(program)
        if self.settings.backend == "serial":
            with self._lock:
                session = self._session
                motion_ready = bool(
                    session is not None
                    and self._controller_state is ControllerState.READY_MOTION
                    and self._coordinate_reference_ready
                    and self._coordinate_reference_session_generation
                    == session.generation
                )
            if not motion_ready:
                raise SafetyError(
                    "Home / park must be completed explicitly for the current "
                    "controller session before Start"
                )
        if program.requires_laser_authorization:
            if authorization_phrase is None:
                raise SafetyError(
                    "This powered program requires explicit START authorization"
                )
            self.arm_program(authorization_phrase, program)
        return self.start_validated_program(program, name)

    def start_job(self, text: str, name: str = "generated.gcode") -> dict[str, Any]:
        start_stop_epoch = self._operation_stop_epoch()
        try:
            program = self.preflight_program(text)
        except Exception:
            with self._stop_epoch_lock:
                if self._stop_epoch == start_stop_epoch:
                    self._clear_arm_authorization()
            raise
        return self.start_validated_program(
            program,
            name,
            _expected_stop_epoch=start_stop_epoch,
        )

    def _start_validated_program(
        self,
        program: ValidatedProgram,
        name: str,
        *,
        start_stop_epoch: int,
    ) -> dict[str, Any]:
        with self._lock:
            requires_laser_authorization = program.requires_laser_authorization
            requires_motion = program.requires_motion
            start_authorization_epoch: int | None = None
            with self._stop_epoch_lock:
                if self._stop_epoch != start_stop_epoch:
                    raise MachineError("Job start was cancelled by software STOP")
                if requires_laser_authorization:
                    start_authorization_epoch = self._authorization_epoch
                    if (
                        not self._connected
                        or time.monotonic() >= self._armed_until_monotonic
                        or self._armed_program_digest != program.digest
                    ):
                        raise SafetyError(
                            "This powered program must be armed immediately before "
                            "starting using its exact preflight result"
                        )
            if self._job.running:
                raise MachineError("A controller job is already running")
            # The Pi-side secondary owner must prove an acknowledged OFF state
            # before this exact immutable program can become active.
            self._prepare_secondary_air_assist_for_start(program)
            controller_session = self._require_session()
            lines = list(program.lines)
            if (
                requires_motion
                and self.settings.backend == "serial"
                and (
                    not self._coordinate_reference_ready
                    or self._coordinate_reference_session_generation
                    != controller_session.generation
                    or self._controller_state is not ControllerState.READY_MOTION
                )
            ):
                raise SafetyError(
                    "Home / park must complete after this controller connection or reset "
                    "before an absolute-motion job can start"
                )
            if (
                requires_motion
                and self._uses_grbl_coordinate_state()
            ):
                self._verify_grbl_coordinate_state()
            if (
                requires_laser_authorization
                and self.settings.backend == "serial"
                and self.settings.home_and_release_after_powered_job
            ):
                if not self.settings.home_before_photo:
                    raise SafetyError(
                        "Automatic post-job Home / park requires machine.home_before_photo=true"
                    )
            with self._stop_epoch_lock:
                if self._stop_epoch != start_stop_epoch:
                    raise MachineError("Job start was cancelled by software STOP")
                if requires_laser_authorization:
                    if self._authorization_epoch != start_authorization_epoch:
                        raise MachineError("Job start was cancelled by disarm")
                    if (
                        not self._connected
                        or time.monotonic() >= self._armed_until_monotonic
                        or self._armed_program_digest != program.digest
                    ):
                        raise SafetyError(
                            "This powered program must be armed immediately before "
                            "starting using its exact preflight result"
                        )
                self._job_laser_authorized = requires_laser_authorization
                # Bind cleanup to the exact immutable mapping that passed the
                # pre-start integrity check. Mutable settings may change after
                # that check, but they must never redirect cleanup away from the
                # output named in the already-validated program.
                active_air_assist = program.air_assist_commands
                self._active_job_air_assist_commands = active_air_assist
                self._active_job_air_assist_off_commands = (
                    ()
                    if (
                        active_air_assist is None
                        or active_air_assist.target is AirAssistTarget.PI_SECONDARY
                    )
                    else active_air_assist.off_commands
                )
                # Every start consumes any temporary grant. A powered job keeps
                # its exact authorization only in this running-job state.
                self._clear_arm_authorization()
                if requires_motion:
                    self._jog_position_mm = None
                job_status = JobStatus(
                    running=True,
                    phase="streaming",
                    name=name[:160],
                    total_lines=len(lines),
                    completed_lines=0,
                    started_at=time.time(),
                    program_digest=program.digest,
                    powered=requires_laser_authorization,
                )
                self._job_identity += 1
                job_stop = threading.Event()
                context = _JobRunContext(
                    identity=self._job_identity,
                    session=controller_session,
                    stop_event=job_stop,
                    status=job_status,
                    air_assist_commands=active_air_assist,
                    air_assist_off_commands=self._active_job_air_assist_off_commands,
                )
                self._job = job_status
                self._job_stop = job_stop
                self._active_job_context = context
                self._set_controller_state_locked(
                    ControllerState.JOB_RUNNING,
                    session=controller_session,
                )
                self._job_thread = threading.Thread(
                    target=self._run_job,
                    args=(
                        context,
                        lines,
                        requires_laser_authorization,
                        requires_motion,
                        program.digest,
                    ),
                    name="gcode-streamer",
                    daemon=True,
                )
                try:
                    self._job_thread.start()
                except Exception as exc:
                    # No command worker exists to run the normal failure
                    # cleanup.  Revoke the exact job authority, publish a real
                    # terminal failure instead of a phantom running job, and
                    # still attempt laser-off before returning the error.
                    self._job.finished_at = time.time()
                    self._job.error = f"Job runner could not start: {exc}"
                    self._job.phase = "failed"
                    self._job.running = False
                    # The cleanup M5 below is intentionally unacknowledged.  Its
                    # eventual `ok` must never be allowed to satisfy a later
                    # command exchange, so revoke this controller session before
                    # attempting the write (we already hold _stop_epoch_lock).
                    transport = controller_session.transport
                    self._session = None
                    self._transport = None
                    self._invalidate_coordinate_reference()
                    self._set_controller_state_locked(
                        ControllerState.RECONNECT_REQUIRED,
                        session=controller_session,
                        failure=f"Job runner could not start: {exc}",
                    )
                    self._authorization_epoch += 1
                    self._clear_arm_authorization()
                    self._job_laser_authorized = False
                    context.stop_event.set()
                    self._active_job_context = None
                    if transport is None:
                        self._append_log(
                            "ERROR",
                            "Job-start failure cleanup could not reach the controller",
                        )
                    else:
                        self._best_effort_fail_off(
                            transport,
                            context="job-start failure cleanup",
                        )
                        transport.close()
                        if callable(
                            getattr(transport, "synchronize_input", None)
                        ):
                            self._schedule_controller_recovery(
                                self._stop_epoch,
                                session=controller_session,
                            )
                    self._best_effort_secondary_off(
                        context="job-start failure cleanup"
                    )
                    raise
            return self._job.to_dict()

    def _execute_running_job_command(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> list[str]:
        """Execute an internal completion command while the job owns the transport."""

        if not self._write_running_job_line(command):
            raise MachineError(
                "Internal primary-controller completion command was routed as auxiliary"
            )
        try:
            return self._wait_for_ack(timeout or self.settings.read_timeout)
        except MachineError as exc:
            raise MachineError(f"Command {command!r} failed: {exc}") from exc

    def _set_running_job_phase(self, phase: str) -> None:
        context = self._current_job_run_context()
        if context is None:
            raise MachineError("Controller job context is unavailable")
        with self._lock:
            if self._active_job_context is context:
                context.status.phase = phase

    def _write_running_job_line(
        self,
        command: str,
        *,
        track_transaction: bool = True,
    ) -> bool:
        """Execute one job line; return whether the primary controller was written."""

        context = self._current_job_run_context()
        if context is None:
            raise MachineError("Controller job context is unavailable")
        job_stop = context.stop_event
        if job_stop.is_set():
            raise MachineError("Job stopped")
        if self._execute_secondary_air_assist_instruction(command):
            return False
        self._raise_if_secondary_faulted()

        with self._transport_write_lock:
            if job_stop.is_set():
                raise MachineError("Job stopped")
            self._check_line_safety(command, job_execution=True)
            with self._lock:
                if (
                    not self._same_controller_session(
                        self._session,
                        context.session,
                    )
                    or self._controller_state is not ControllerState.JOB_RUNNING
                ):
                    if job_stop.is_set():
                        raise MachineError("Job stopped") from None
                    raise MachineError("Controller job session was superseded")
            transaction_started = time.monotonic()
            sequence = (
                context.session.diagnostics.next_command(command)
                if track_transaction
                else None
            )
            try:
                context.session.transport.write_line(command)
                self._append_log("TX", command)
            except Exception as exc:
                self._mark_controller_command_state_untrusted(
                    context.session,
                    reason=f"Job command {command!r} write failed: {exc}",
                )
                raise MachineError(
                    f"Job command {command!r} failed while writing: {exc}"
                ) from exc
            if sequence is not None:
                self._job_context.pending_transaction = (
                    context.session,
                    sequence,
                    transaction_started,
                )
        return True

    def _finish_powered_job_home_park_and_release(self) -> None:
        """Home, park, and release a serial machine after a successful laser job."""

        self._require_safety_configuration()
        dialect = self._require_resolved_dialect()
        if not self.settings.home_before_photo:
            raise SafetyError(
                "Automatic post-job Home / park requires machine.home_before_photo=true"
            )
        x = float(self.settings.photo_x)
        y = float(self.settings.photo_y)
        travel_feed = float(self.laser_settings.travel_feed_mm_min)
        configured_idle_delay = self.settings.grbl_step_idle_delay_ms

        setup_timeout = max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout)
        positioning_error: Exception | None = None
        try:
            if not self.settings.work_area.contains(
                x,
                y,
                self.laser_settings.boundary_margin_mm,
            ):
                raise SafetyError(
                    "Configured photography position lies outside the safe work area"
                )
            # GRBL acknowledges planner acceptance before physical motion is
            # necessarily complete. Synchronize behind the final job move before
            # issuing the system-level homing command, which GRBL rejects in Run.
            self._execute_running_job_command(
                dialect.motion_barrier_command,
                timeout=max(
                    _JOB_COMMAND_ACK_TIMEOUT_SECONDS,
                    self.settings.read_timeout,
                ),
            )
            self._set_running_job_phase("homing")
            if dialect is GRBL_DIALECT:
                self._execute_running_job_grbl_homing(
                    timeout=max(
                        _GRBL_HOMING_TIMEOUT_SECONDS,
                        self.settings.read_timeout,
                    ),
                )
            else:
                self._execute_running_job_command(
                    dialect.homing.command,
                    timeout=max(
                        dialect.homing.timeout_floor_seconds,
                        self.settings.read_timeout,
                    ),
                )
            self._set_running_job_phase("parking")
            self._execute_running_job_command("G21", timeout=setup_timeout)
            self._execute_running_job_command("G90", timeout=setup_timeout)
            self._execute_running_job_command(
                f"G0 X{x:.3f} Y{y:.3f} "
                f"F{travel_feed:.3f}",
                timeout=setup_timeout,
            )
            self._execute_running_job_command(
                dialect.motion_barrier_command,
                timeout=max(120.0, self.settings.read_timeout),
            )
        except BaseException as exc:
            positioning_error = exc

        release_error: Exception | None = None
        try:
            self._set_running_job_phase("releasing")
            if dialect is GRBL_DIALECT:
                session = dialect.grbl_session
                assert session is not None
                try:
                    responses = self._execute_running_job_command(
                        session.settings_query_command,
                        timeout=setup_timeout,
                    )
                    restore_idle_delay = (
                        configured_idle_delay
                        if self._reported_grbl_step_idle_delay(responses)
                        == session.held_step_idle_delay_ms
                        else None
                    )
                except Exception as settings_error:
                    # The release must still be attempted. Restoring the configured
                    # finite delay is safer than risking a persisted $1=255 hold.
                    restore_idle_delay = configured_idle_delay
                    self._append_log(
                        "INFO",
                        "Could not inspect GRBL $1 after the job; forcing the configured "
                        f"finite delay before release ({settings_error})",
                    )
                self._release_grbl_motors(
                    restore_idle_delay=restore_idle_delay,
                    job_execution=True,
                    context="post-job Home / park",
                )
            else:
                self._execute_running_job_command(
                    dialect.motor_release_command,
                    timeout=setup_timeout,
                )
        except Exception as exc:
            release_error = exc

        # Released axes are convenient for access but no longer a trusted
        # absolute coordinate reference.
        self._invalidate_coordinate_reference()
        if positioning_error is not None:
            if release_error is not None:
                _add_exception_note(
                    positioning_error,
                    f"Post-job motor release also failed: {release_error}"
                )
            raise positioning_error
        if release_error is not None:
            raise release_error
        self._append_log(
            "INFO",
            "Powered job completed; machine homed, parked, and motors released",
        )

    def _run_job(
        self,
        context: _JobRunContext,
        lines: list[str],
        requires_laser_authorization: bool,
        requires_motion: bool,
        program_digest: str,
    ) -> None:
        self._job_context.current = context
        error: str | None = None
        air_assist_off_acknowledged = context.air_assist_commands is None
        try:
            job_ack_timeout = max(
                _JOB_COMMAND_ACK_TIMEOUT_SECONDS,
                self.settings.read_timeout,
            )
            self._write_running_job_line("M5")
            self._wait_for_ack(job_ack_timeout)
            # The service-level prelude covers powered setup/calibration jobs
            # that intentionally contain no layer Air Assist commands. Bind it
            # only to the immutable mapping that passed program integrity checks.
            for command in context.air_assist_off_commands:
                self._write_running_job_line(command)
                self._wait_for_ack(job_ack_timeout)
            for index, line in enumerate(lines, start=1):
                primary_written = self._write_running_job_line(line)
                # GRBL may delay an acknowledgement while its planner is full or
                # while a standalone laser-state command synchronizes queued
                # motion. A short interactive-command timeout can therefore turn
                # a healthy finishing move into a false job failure.
                if primary_written:
                    self._wait_for_ack(job_ack_timeout)
                with self._lock:
                    if self._active_job_context is context:
                        context.status.completed_lines = index
            run_completion = (
                requires_laser_authorization
                and self.settings.backend == "serial"
                and self.settings.home_and_release_after_powered_job
            )
            if run_completion:
                self._set_running_job_phase("draining")
                self._append_log(
                    "INFO",
                    "Powered toolpath streamed; waiting for queued motion to finish before Home / park",
                )
            self._write_running_job_line("M5")
            self._wait_for_ack(job_ack_timeout)
            for command in context.air_assist_off_commands:
                self._write_running_job_line(command)
                self._wait_for_ack(job_ack_timeout)
            active_air_assist = context.air_assist_commands
            secondary = self._secondary_controller_for(active_air_assist)
            if secondary is not None:
                secondary.set_enabled(
                    False,
                    mapping_digest=active_air_assist.mapping_digest,
                    write_guard=self._secondary_job_write_guard,
                )
            air_assist_off_acknowledged = True
            if run_completion:
                self._finish_powered_job_home_park_and_release()
            elif self.settings.backend == "serial" and requires_motion:
                # Controller acknowledgement proves planner acceptance, not
                # physical completion. Do not publish a successful terminal
                # job while accepted motion may still be running.
                self._set_running_job_phase("draining")
                self._execute_running_job_command(
                    self._require_resolved_dialect().motion_barrier_command,
                    timeout=max(
                        _JOB_COMMAND_ACK_TIMEOUT_SECONDS,
                        self.settings.read_timeout,
                    ),
                )
            if context.stop_event.is_set():
                raise MachineError("Job stopped")
        except BaseException as exc:
            with self._lock:
                stopped_by_request = bool(
                    context.stop_event.is_set()
                    and self._last_controller_stop_at is not None
                    and self._last_controller_stop_at >= context.status.started_at
                )
            error = "Job stopped" if stopped_by_request else str(exc)
            # After any failed streamed command, the controller's receive queue
            # and planner acknowledgement position are not provable. Keep the
            # transport subject to a reconnect boundary so stale acknowledgements
            # cannot be mistaken for later command responses.
            self._mark_controller_command_state_untrusted(
                context.session,
                reason=f"Controller job failed: {exc}",
            )
            LOGGER.error("Controller job failed: %s", exc)
            self._append_log("ERROR", f"Controller job failed: {exc}")
            # Retain an explicit exact-session laser-off attempt even though
            # quarantine also performs fail-off before close.
            self._best_effort_fail_off(
                context.session.transport,
                context="job cleanup",
            )
            self._best_effort_secondary_off(
                context="job cleanup",
                commands=context.air_assist_commands,
            )
        finally:
            with self._lock:
                is_current = self._active_job_context is context
                with self._stop_epoch_lock:
                    if error is None and context.stop_event.is_set():
                        error = "Job stopped"
                    context.status.finished_at = time.time()
                    context.status.error = error
                    context.status.phase = (
                        "failed" if error is not None else "complete"
                    )
                    if error is None and is_current:
                        self._last_successful_job = {
                            "name": context.status.name,
                            "program_digest": program_digest,
                            "powered": requires_laser_authorization,
                            "started_at": context.status.started_at,
                            "finished_at": context.status.finished_at,
                            "completed_lines": context.status.completed_lines,
                            "total_lines": context.status.total_lines,
                            "backend": self.settings.backend,
                            "protocol": context.session.dialect.id,
                            "hardware_enabled": self.hardware_enabled,
                        }
                    # Publish running=False last on this exact job object. A
                    # superseded worker cannot overwrite a successor job.
                    context.status.running = False
                    if is_current:
                        self._job = context.status
                        self._active_job_context = None
                        self._clear_arm_authorization()
                        self._job_laser_authorized = False
                        if air_assist_off_acknowledged:
                            self._active_job_air_assist_off_commands = ()
                            self._active_job_air_assist_commands = None
                        if (
                            self._same_controller_session(
                                self._session,
                                context.session,
                            )
                            and self._controller_state
                            is ControllerState.JOB_RUNNING
                        ):
                            coordinate_ready = bool(
                                self._coordinate_reference_ready
                                and self._coordinate_reference_session_generation
                                == context.session.generation
                            )
                            self._set_controller_state_locked(
                                (
                                    ControllerState.READY_MOTION
                                    if coordinate_ready
                                    else ControllerState.READY_HOME_REQUIRED
                                ),
                                session=context.session,
                            )
            self._job_context.current = None
            self._job_context.pending_transaction = None

    def request_stop(self, emergency: bool = False, *, _recover: bool = True) -> None:
        """Quarantine the exact session and issue bounded priority laser-off.

        Communication recovery is asynchronous and can publish only
        ``READY_HOME_REQUIRED``. It never homes, moves, arms, or resumes work.
        """

        stop_at = time.time()
        stop_call_deadline = time.monotonic() + _REALTIME_STOP_WRITE_DEADLINE_SECONDS
        with self._stop_epoch_lock:
            self._stop_epoch += 1
            stop_epoch = self._stop_epoch
            self._authorization_epoch += 1
            self._job_stop.set()
            context = self._active_job_context
            if context is not None:
                context.stop_event.set()
            self._job_laser_authorized = False
            self._clear_arm_authorization()
        with self._lock:
            self._last_controller_stop_at = stop_at
            session = self._adopt_legacy_test_session_locked()
            candidate = self._candidate_session
            if session is not None or candidate is not None:
                if self._controller_state is not ControllerState.SHUTTING_DOWN:
                    self._set_controller_state_locked(ControllerState.STOPPING)
            self._session = None
            self._candidate_session = None
            self._candidate_connect_deadline = None
            self._transport = None
            self._invalidate_coordinate_reference()
            if not _recover:
                self._recovery_stop_epoch = None
            LOGGER.info(
                "primary controller STOP latched stop_epoch=%d emergency=%s "
                "generation=%s endpoint=%s protocol=%s recovery_requested=%s",
                stop_epoch,
                emergency,
                "-" if session is None else session.generation,
                "-" if session is None else session.resolved_endpoint,
                "-" if session is None else session.dialect.id,
                _recover,
            )

        quarantined: list[ControllerSession] = []
        for item in (session, candidate):
            if item is not None and all(
                item.transport is not existing.transport for existing in quarantined
            ):
                quarantined.append(item)

        if session is not None:
            transport = session.transport
            stop_dialect = session.dialect
            stop_policy = (
                stop_dialect.emergency_stop
                if emergency
                else None
            )
            if stop_policy is not None and stop_policy.raw_command is not None:
                finished = threading.Event()
                failures: list[Exception] = []

                def realtime_stop() -> None:
                    try:
                        transport.write_raw(stop_policy.raw_command)
                    except Exception as exc:
                        failures.append(exc)
                    finally:
                        finished.set()

                try:
                    threading.Thread(
                        target=realtime_stop,
                        name="controller-realtime-stop",
                        daemon=True,
                    ).start()
                except Exception as exc:
                    self._append_log(
                        "ERROR",
                        f"{stop_policy.failure_label} could not start: {exc}; continuing with M5",
                    )
                else:
                    remaining = max(0.0, stop_call_deadline - time.monotonic())
                    if not finished.wait(remaining):
                        self._append_log(
                            "ERROR",
                            f"{stop_policy.failure_label} write timed out; continuing with M5",
                        )
                    elif failures:
                        self._append_log(
                            "ERROR",
                            f"{stop_policy.failure_label} failed: {failures[0]}",
                        )
                    else:
                        self._append_log("TX", stop_policy.success_log)
        for item in quarantined:
            is_primary = session is not None and item.transport is session.transport
            item_stop_policy = (
                item.dialect.emergency_stop
                if emergency and is_primary
                else None
            )
            self._start_bounded_stop_cleanup(
                item,
                context=(
                    "software STOP"
                    if is_primary
                    else "software STOP candidate cleanup"
                ),
                deadline=stop_call_deadline,
                emergency_line=(
                    None if item_stop_policy is None else item_stop_policy.line_command
                ),
                emergency_success_log=(
                    None if item_stop_policy is None else item_stop_policy.success_log
                ),
                emergency_failure_label=(
                    None if item_stop_policy is None else item_stop_policy.failure_label
                ),
            )
        # The primary path above is authoritative and complete before this
        # independent bounded cleanup is even dispatched.  Never wait here for
        # the secondary exchange lock or its acknowledgement.
        try:
            self._queue_secondary_off(context="software STOP")
        except Exception as exc:
            self._append_log(
                "ERROR",
                f"software STOP secondary OFF cleanup could not start: {exc}",
            )

        recovery_source = session or candidate
        if (
            _recover
            and recovery_source is not None
            and callable(
                getattr(recovery_source.transport, "synchronize_input", None)
            )
        ):
            self._schedule_controller_recovery(
                stop_epoch,
                session=recovery_source,
            )
        else:
            with self._lock:
                if self._controller_state is ControllerState.STOPPING:
                    self._set_controller_state_locked(
                        ControllerState.RECONNECT_REQUIRED
                        if _recover and recovery_source is not None
                        else ControllerState.DISCONNECTED
                    )

    def stop_job(self, emergency: bool = False, *, _recover: bool = True) -> None:
        self.request_stop(emergency=emergency, _recover=_recover)
        if (
            self._job_thread
            and self._job_thread.is_alive()
            and threading.current_thread() is not self._job_thread
        ):
            self._job_thread.join(timeout=1.5)

    def shutdown(self, *, deadline: float | None = None) -> None:
        """Permanently prevent publication and perform bounded fail-off cleanup."""

        with self._lock:
            self._shutdown_requested = True
            self._recovery_stop_epoch = None
            self._set_controller_state_locked(
                ControllerState.SHUTTING_DOWN,
                force_terminal=True,
            )
        self.stop_job(emergency=False, _recover=False)
        with self._lock:
            recovery = self._recovery_thread
        if (
            recovery is not None
            and recovery.is_alive()
            and recovery is not threading.current_thread()
        ):
            timeout = 1.5
            if deadline is not None:
                timeout = max(0.0, min(timeout, deadline - time.monotonic()))
            recovery.join(timeout=timeout)

    def successful_job_receipt(
        self,
        program_digest: str,
        *,
        not_before: float,
    ) -> dict[str, Any] | None:
        """Return a completed exact-program receipt, never a prepared/running job."""

        if (
            type(program_digest) is not str
            or _PROGRAM_DIGEST_PATTERN.fullmatch(program_digest) is None
            or type(not_before) not in {int, float}
            or not math.isfinite(float(not_before))
        ):
            return None
        threshold = float(not_before)
        with self._lock:
            receipt = self._last_successful_job
            if (
                receipt is None
                or receipt.get("program_digest") != program_digest
                or float(receipt.get("finished_at") or 0.0) < threshold
            ):
                return None
            return dict(receipt)

    def status(self) -> dict[str, Any]:
        secondary_status = None
        if self._secondary_air_assist is not None:
            snapshot = self._secondary_air_assist.status
            secondary_status = {
                "ready": snapshot.ready,
                "enabled": snapshot.enabled,
                "fault": snapshot.fault,
                "port": snapshot.port,
                "baudrate": snapshot.baudrate,
                "mapping_digest": snapshot.mapping_digest,
            }
        with self._lock:
            state = self._controller_state
            session = self._session or self._candidate_session
            session_snapshot = None if session is None else session.status_snapshot()
            diagnostics = (
                session.diagnostics
                if session is not None
                else self._last_session_diagnostics
            )
            session_diagnostics = (
                None if diagnostics is None else diagnostics.snapshot()
            )
            diagnostic_snapshot = (
                {
                    "state_revision": self._controller_state_revision,
                    "last_failure": self._last_controller_failure,
                    "last_failure_code": self._last_controller_failure_code,
                    "last_failed_command": self._last_controller_failed_command,
                    "last_successful_transaction": None,
                    "synchronization": None,
                    "last_successful_sync_at": None,
                    "firmware_identity": [],
                    "transcript": [],
                }
                if session_diagnostics is None
                else {
                    **session_diagnostics,
                    "state_revision": self._controller_state_revision,
                    "last_failure": (
                        session_diagnostics["last_failure"]
                        or self._last_controller_failure
                    ),
                    "last_failure_code": (
                        session_diagnostics["last_failure_code"]
                        or self._last_controller_failure_code
                    ),
                    "last_failed_command": (
                        session_diagnostics["last_failed_command"]
                        or self._last_controller_failed_command
                    ),
                }
            )
            diagnostic_snapshot.update(
                {
                    "failure_detail": diagnostic_snapshot["last_failure"],
                    "last_stop_at": self._last_controller_stop_at,
                    "last_recovery_at": self._last_controller_recovery_at,
                    "action_required": _CONTROLLER_ACTION_REQUIRED[state],
                }
            )
            connected = state in CONNECTED_CONTROLLER_STATES
            connecting = state in CONNECTING_CONTROLLER_STATES
            reconnect_required = state is ControllerState.RECONNECT_REQUIRED
            coordinate_reference_ready = bool(
                connected
                and self._session is not None
                and self._coordinate_reference_ready
                and self._coordinate_reference_session_generation
                == self._session.generation
                and state in {ControllerState.READY_MOTION, ControllerState.JOB_RUNNING}
            )
            jog_position = self._jog_position_mm
            job = self._job.to_dict()
            last_successful_job = (
                None
                if self._last_successful_job is None
                else dict(self._last_successful_job)
            )
        armed = self.armed
        return {
            "connected": connected,
            "connecting": connecting,
            "controller_state": state.value,
            "controller_session_generation": self._controller_session_generation,
            "controller_state_revision": self._controller_state_revision,
            "controller_recovery_in_progress": state is ControllerState.RECOVERING,
            "controller_action_required": _CONTROLLER_ACTION_REQUIRED[state],
            "controller_session": session_snapshot,
            "controller_diagnostics": diagnostic_snapshot,
            "backend": self.settings.backend,
            "hardware_enabled": self.hardware_enabled,
            "laser_lockout": self.laser_lockout,
            "protocol": self._protocol,
            "port": self._active_port,
            "baudrate": self._active_baudrate,
            "allow_motion": self.settings.allow_motion,
            "coordinate_reference_ready": coordinate_reference_ready,
            "coordinate_reference_session_generation": (
                self._coordinate_reference_session_generation
                if coordinate_reference_ready
                else None
            ),
            "coordinate_state_reference": self._coordinate_state_reference,
            "jog_position_mm": (
                None
                if jog_position is None
                else {"x": jog_position[0], "y": jog_position[1]}
            ),
            "jog_ready": bool(
                state is ControllerState.READY_MOTION
                and coordinate_reference_ready
                and jog_position is not None
                and not armed
                and not job["running"]
            ),
            "max_travel_feed_mm_min": self.settings.max_travel_feed_mm_min,
            "controller_reconnect_required": reconnect_required,
            "armed": armed,
            "armed_until": self._armed_until if armed else None,
            "arm_phrase": self.ARM_PHRASE,
            "secondary_air_assist": secondary_status,
            "job": job,
            "last_successful_job": last_successful_job,
            "log": list(self._log)[-80:],
        }
