"""Windows facade for the Pi-owned ``E3MACHINE/2`` machine service.

The desktop remains responsible for deterministic program preflight and upload.
Once ``job.start`` is acknowledged, the Raspberry Pi owns execution.  Status
monitoring is deliberately observational: losing this client is never a run
enable signal and never stops an accepted job.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
import socket
import threading
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from ..config import LaserSettings, MachineSettings
from ..errors import MachineError, SafetyError
from .network_transport import bridge_token_from_environment, parse_bridge_uri
from .pi_job_protocol import (
    ACTION_JOB_ACTIVE,
    ACTION_JOB_BEGIN,
    ACTION_JOB_CHUNK,
    ACTION_JOB_FINALIZE,
    ACTION_JOB_RESULT,
    ACTION_JOB_START,
    ACTION_JOB_STATUS,
    ACTION_JOB_STOP,
    CAPABILITY_PI_OWNED_JOBS,
    MAX_JOB_BYTES,
    MAX_UPLOAD_CHUNK_BYTES,
    PROTOCOL_VERSION,
    PiJobProtocolError,
    authenticate_client,
    encode_upload_chunk,
    request_response,
    validate_job_name,
)
from .pi_job_service import canonical_program_bytes, execution_policy_digest
from .pi_machine_server import (
    ACTION_JOB_LATEST,
    ACTION_MACHINE_COMMAND,
    ACTION_MACHINE_CONNECT,
    ACTION_MACHINE_DISCONNECT,
    ACTION_MACHINE_JOG,
    ACTION_MACHINE_PREPARE_JOB_START,
    ACTION_MACHINE_PREPARE_PHOTO_POSITION,
    ACTION_MACHINE_REALTIME_POSITION,
    ACTION_MACHINE_REPLACE_CONNECTION,
    ACTION_MACHINE_STATUS,
    ACTION_MACHINE_STEPPER_HOLD,
    ACTION_MACHINE_STEPPER_HOLD_RELEASE,
    ACTION_SERVICE_CAPABILITIES,
)
from .service import MachineService, ValidatedProgram

_DEFAULT_RPC_TIMEOUT_SECONDS = 130.0
_MONITOR_RPC_TIMEOUT_SECONDS = 0.75
_STOP_RPC_TIMEOUT_SECONDS = 1.0
_HOLD_CONNECT_TIMEOUT_SECONDS = 5.0
_HOLD_SESSION_TIMEOUT_SECONDS = 180.0
_DEFAULT_MONITOR_INTERVAL_SECONDS = 0.75
_MAX_MONITOR_BACKOFF_SECONDS = 8.0
_MAX_CLIENT_DIAGNOSTIC_JOBS = 8
_MAX_LOCAL_JOB_IDENTITIES = 32
_START_RECOVERY_POLL_SECONDS = 2.0
_START_RECOVERY_POLL_INTERVAL_SECONDS = 0.1
_PROGRAM_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_JOB_STATES = frozenset({"starting", "running", "stopping"})
_TERMINAL_JOB_STATES = frozenset(
    {"complete", "failed", "stopped", "interrupted", "deleted"}
)


class _RemoteRequestRejected(MachineError):
    """The Pi returned a complete, authenticated rejection response."""


def _polygon_payload(
    polygon: tuple[tuple[float, float], ...] | None,
) -> list[list[float]] | None:
    if polygon is None:
        return None
    return [[float(x), float(y)] for x, y in polygon]


def _job_state(record: Mapping[str, Any] | None) -> str:
    if record is None:
        return "idle"
    raw = record.get("state", record.get("phase", "idle"))
    return raw.lower() if isinstance(raw, str) else "idle"


class RemoteMachineService:
    """MachineService-compatible client whose execution owner is the Pi."""

    ARM_PHRASE = MachineService.ARM_PHRASE
    pi_owned_execution = True

    def __init__(
        self,
        settings: MachineSettings,
        laser_settings: LaserSettings,
        hardware_enabled: bool = False,
        laser_lockout: bool = False,
        *,
        monitor_interval_seconds: float = _DEFAULT_MONITOR_INTERVAL_SECONDS,
    ) -> None:
        if type(hardware_enabled) is not bool:
            raise TypeError("hardware_enabled must be an exact boolean")
        if type(laser_lockout) is not bool:
            raise TypeError("laser_lockout must be an exact boolean")
        if type(monitor_interval_seconds) not in {int, float} or not math.isfinite(
            float(monitor_interval_seconds)
        ):
            raise TypeError("monitor_interval_seconds must be a finite number")
        if float(monitor_interval_seconds) <= 0.0:
            raise ValueError("monitor_interval_seconds must be positive")

        self.settings = settings
        self.laser_settings = laser_settings
        self.hardware_enabled = hardware_enabled
        self.laser_lockout = laser_lockout
        if settings.backend != "serial":
            raise MachineError(
                "Remote Pi machine execution requires machine.backend='serial'"
            )
        self._target = parse_bridge_uri(settings.port)

        # This instance is never connected.  It is the single local authority
        # for parsing and binding ValidatedProgram values before any upload.
        self._policy = MachineService(
            settings,
            laser_settings,
            hardware_enabled=hardware_enabled,
            laser_lockout=laser_lockout,
        )

        self._state_lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._capability_lock = threading.Lock()
        self._stop_epoch_lock = threading.Lock()
        self._operation_context = threading.local()
        self._stop_epoch = 0
        self._authorization_epoch = 0

        self._armed_until = 0.0
        self._armed_until_monotonic = 0.0
        self._armed_program_digest: str | None = None
        self._armed_authorization_phrase: str | None = None

        self._upload_job_id: str | None = None
        self._accepted_job_id: str | None = None
        self._tracked_job_id: str | None = None
        self._tracked_program_digest: str | None = None
        self._start_ownership_uncertain = False
        self._client_diagnostics: dict[str, dict[str, int | float]] = {}
        self._local_job_created_at: dict[str, float] = {}

        self._monitor_interval_seconds = float(monitor_interval_seconds)
        self._monitor_stop = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._detached = False
        self._capabilities_verified = False

        self._hold_lock = threading.RLock()
        self._hold_context = threading.local()

        self._status_cache = self._initial_status()

    def _initial_status(self) -> dict[str, Any]:
        status = copy.deepcopy(self._policy.status())
        status.update(
            {
                "port": self.settings.port,
                "node_protocol": PROTOCOL_VERSION,
                "execution_target": "pi",
                "execution_owner": "pi",
                "pi_owned_execution": True,
                "monitor_connected": False,
                "status_stale": True,
                "status_error": None,
            }
        )
        status["job"] = self._normalize_job(status.get("job"))
        return status

    @property
    def connected(self) -> bool:
        with self._state_lock:
            return self._status_cache.get("connected") is True

    @property
    def armed(self) -> bool:
        with self._state_lock:
            self._expire_arm_locked()
            return self._armed_program_digest is not None

    @property
    def pi_owned_job_active(self) -> bool:
        with self._state_lock:
            if self._start_ownership_uncertain:
                return True
            job = self._status_cache.get("job")
            return bool(
                isinstance(job, Mapping)
                and job.get("ownership_accepted") is True
                and _job_state(job) in _ACTIVE_JOB_STATES
            )

    def _clear_arm_locked(self) -> None:
        self._armed_until = 0.0
        self._armed_until_monotonic = 0.0
        self._armed_program_digest = None
        self._armed_authorization_phrase = None

    def _expire_arm_locked(self) -> None:
        if (
            self._armed_program_digest is not None
            and time.monotonic() >= self._armed_until_monotonic
        ):
            self._clear_arm_locked()

    def operation_generation(self) -> int:
        with self._stop_epoch_lock:
            return self._stop_epoch

    @contextmanager
    def operation_scope(self, generation: int):
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
        return self.operation_generation() if bound is None else int(bound)

    def _require_operation_current(self, generation: int) -> None:
        with self._stop_epoch_lock:
            if self._stop_epoch != generation:
                raise MachineError("Operation was cancelled by software STOP")

    def _require_hardware_authority(self) -> None:
        if self.hardware_enabled is not True:
            raise SafetyError(
                "The current process was not granted hardware authority and cannot "
                "control the remote Pi machine"
            )

    def _require_explicit_protocol_binding(self) -> None:
        if self.settings.protocol not in {"grbl", "marlin"}:
            raise MachineError(
                "Remote Pi machine execution requires an explicit matching "
                "machine.protocol of grbl or marlin; auto cannot bind the Pi safety "
                "policy"
            )

    @staticmethod
    def _request_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _validate_response(
        response: object,
        *,
        request_id: str,
        action: str,
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise PiJobProtocolError(
                f"Remote {action} response was not a JSON object"
            )
        if response.get("request_id") != request_id:
            raise PiJobProtocolError(
                f"Remote {action} response did not match its request ID"
            )
        if response.get("ok") is False:
            raw_error = response.get("error")
            detail = (
                raw_error
                if isinstance(raw_error, str) and raw_error
                else f"Remote {action} request was rejected"
            )
            raise _RemoteRequestRejected(detail)
        if response.get("ok") is not True:
            raise PiJobProtocolError(
                f"Remote {action} response omitted an exact success flag"
            )
        return response

    def _rpc(
        self,
        action: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float = _DEFAULT_RPC_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        request_id = self._request_id()
        request: dict[str, Any] = {"request_id": request_id, "action": action}
        if payload:
            request.update(payload)
        response = request_response(
            self._target.host,
            self._target.port,
            bridge_token_from_environment(),
            request,
            timeout=timeout,
        )
        return self._validate_response(
            response,
            request_id=request_id,
            action=action,
        )

    def _require_capabilities(
        self,
        *,
        timeout: float = _DEFAULT_RPC_TIMEOUT_SECONDS,
    ) -> None:
        # Keep an offline legacy profile inspectable in setup/UI, but fail before
        # the first controller, upload, or monitoring RPC.  The Pi resolves one
        # concrete dialect and Windows must bind the same policy explicitly.
        self._require_explicit_protocol_binding()
        with self._state_lock:
            if self._capabilities_verified:
                return
        with self._capability_lock:
            with self._state_lock:
                if self._capabilities_verified:
                    return
            response = self._rpc(ACTION_SERVICE_CAPABILITIES, timeout=timeout)
            capabilities = response.get("capabilities")
            actions = response.get("actions")
            if response.get("protocol_version") != PROTOCOL_VERSION:
                raise PiJobProtocolError(
                    "Remote machine capability response did not match E3MACHINE/2"
                )
            if (
                not isinstance(capabilities, list)
                or CAPABILITY_PI_OWNED_JOBS not in capabilities
                or not isinstance(actions, Mapping)
                or ACTION_JOB_START not in actions
                or ACTION_JOB_STATUS not in actions
                or ACTION_JOB_STOP not in actions
                or ACTION_JOB_LATEST not in actions
            ):
                raise PiJobProtocolError(
                    "The remote node does not advertise the required Pi-owned job "
                    "capability; update/start the combined Pi node"
                )
            with self._state_lock:
                self._capabilities_verified = True

    @staticmethod
    def _normalize_job(raw: object) -> dict[str, Any]:
        record = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}
        state = _job_state(record)
        raw_phase = record.get("phase")
        phase = raw_phase if isinstance(raw_phase, str) and raw_phase else state
        total_raw = record.get("total_lines", record.get("total_commands", 0))
        completed_raw = record.get(
            "completed_lines", record.get("completed_commands", 0)
        )
        total = total_raw if type(total_raw) is int and total_raw >= 0 else 0
        completed = (
            completed_raw
            if type(completed_raw) is int and 0 <= completed_raw <= total
            else 0
        )
        started_at = record.get("started_at")
        finished_at = record.get("finished_at")
        elapsed = record.get("elapsed_seconds")
        if type(elapsed) not in {int, float} or not math.isfinite(float(elapsed)):
            if type(started_at) in {int, float} and math.isfinite(float(started_at)):
                end = (
                    float(finished_at)
                    if type(finished_at) in {int, float}
                    and math.isfinite(float(finished_at))
                    else time.time()
                )
                elapsed = max(0.0, end - float(started_at))
            else:
                elapsed = None
        digest = record.get("program_digest", record.get("expected_sha256"))
        powered = record.get(
            "powered", record.get("requires_laser_authorization", False)
        )
        ownership_accepted = record.get("ownership_accepted") is True
        execution_owner = (
            "pi"
            if ownership_accepted
            else ("uncertain" if state == "starting" else "windows")
        )
        record.update(
            {
                "state": state,
                "running": ownership_accepted and state in _ACTIVE_JOB_STATES,
                "phase": phase,
                "name": record.get("name", ""),
                "total_lines": total,
                "completed_lines": completed,
                "progress": 0.0 if total == 0 else completed / total,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_seconds": elapsed,
                "error": record.get("error"),
                "program_digest": digest if isinstance(digest, str) else None,
                "powered": powered is True,
                "ownership_accepted": ownership_accepted,
                "execution_owner": execution_owner,
                "ownership_uncertain": state == "starting" and not ownership_accepted,
                "status_stale": record.get("status_stale") is True,
                "monitor_connected": record.get("monitor_connected") is True,
            }
        )
        return record

    def _observe_job_locked(
        self,
        record: Mapping[str, Any],
        *,
        accepted: bool | None = None,
    ) -> dict[str, Any]:
        job = self._normalize_job(record)
        job_id = job.get("job_id")
        digest = job.get("program_digest")
        if isinstance(job_id, str):
            self._tracked_job_id = job_id
            diagnostics = self._client_diagnostics.get(job_id)
            if diagnostics is not None:
                job.update(diagnostics)
        if isinstance(digest, str):
            self._tracked_program_digest = digest
        state = _job_state(job)
        ownership_accepted = job.get("ownership_accepted") is True
        if ownership_accepted:
            if isinstance(job_id, str):
                self._accepted_job_id = job_id
            self._start_ownership_uncertain = False
        elif state in _ACTIVE_JOB_STATES:
            # Durable `starting` is written before controller open/Home/arm and
            # is not the ownership boundary. Keep cleanup non-destructive while
            # monitoring for the explicit acceptance marker.
            self._start_ownership_uncertain = True
        elif accepted is False:
            self._start_ownership_uncertain = False
        elif state in _TERMINAL_JOB_STATES:
            self._start_ownership_uncertain = False
        self._status_cache["job"] = job
        return job

    def _record_client_diagnostics(
        self,
        job_id: str,
        **values: int | float,
    ) -> None:
        with self._state_lock:
            diagnostics = self._client_diagnostics.setdefault(job_id, {})
            diagnostics.update(values)
            while len(self._client_diagnostics) > _MAX_CLIENT_DIAGNOSTIC_JOBS:
                oldest = next(iter(self._client_diagnostics))
                del self._client_diagnostics[oldest]
            cached_job = self._status_cache.get("job")
            if isinstance(cached_job, dict) and cached_job.get("job_id") == job_id:
                cached_job.update(diagnostics)

    def _record_local_job_identity(self, job_id: str, created_at: float) -> None:
        with self._state_lock:
            self._local_job_created_at[job_id] = created_at
            while len(self._local_job_created_at) > _MAX_LOCAL_JOB_IDENTITIES:
                oldest = next(iter(self._local_job_created_at))
                del self._local_job_created_at[oldest]

    def _cache_remote_status(
        self,
        raw_status: Mapping[str, Any],
        *,
        job_record: Mapping[str, Any] | None,
    ) -> None:
        status = copy.deepcopy(dict(raw_status))
        with self._state_lock:
            previous_job = self._status_cache.get("job")
            monitor_connected = not self._detached
            status.update(
                {
                    "port": self.settings.port,
                    "node_protocol": PROTOCOL_VERSION,
                    "execution_target": "pi",
                    "execution_owner": "pi",
                    "pi_owned_execution": True,
                    "monitor_connected": monitor_connected,
                    "status_stale": not monitor_connected,
                    "status_error": None,
                }
            )
            self._status_cache = status
            observed = job_record
            if observed is None and isinstance(status.get("job"), Mapping):
                observed = status["job"]
            if observed is not None:
                self._observe_job_locked(observed)
            elif isinstance(previous_job, Mapping):
                self._status_cache["job"] = self._normalize_job(previous_job)
            else:
                self._status_cache["job"] = self._normalize_job(None)

    def _cache_job_record(
        self,
        raw_job: Mapping[str, Any],
        *,
        accepted: bool | None = None,
    ) -> dict[str, Any]:
        with self._state_lock:
            job = self._observe_job_locked(raw_job, accepted=accepted)
            monitor_connected = not self._detached
            job["monitor_connected"] = monitor_connected
            job["status_stale"] = not monitor_connected
            self._status_cache["monitor_connected"] = monitor_connected
            self._status_cache["status_stale"] = not monitor_connected
            self._status_cache["status_error"] = None
            return copy.deepcopy(job)

    def _mark_monitor_disconnected(self, error: str | None) -> None:
        with self._state_lock:
            self._status_cache["monitor_connected"] = False
            self._status_cache["status_stale"] = True
            self._status_cache["status_error"] = error
            job = self._status_cache.get("job")
            if isinstance(job, Mapping):
                cached = self._normalize_job(job)
            else:
                cached = self._normalize_job(None)
            accepted_active = bool(
                cached.get("ownership_accepted") is True
                and _job_state(cached) in _ACTIVE_JOB_STATES
            )
            if accepted_active:
                cached["running"] = True
                cached["execution_owner"] = "pi"
            elif self._start_ownership_uncertain:
                cached["running"] = False
                cached["ownership_uncertain"] = True
                cached["execution_owner"] = "uncertain"
            cached["monitor_connected"] = False
            cached["status_stale"] = True
            self._status_cache["job"] = cached

    @staticmethod
    def _response_mapping(response: Mapping[str, Any], key: str) -> dict[str, Any]:
        raw = response.get(key)
        if not isinstance(raw, Mapping):
            raise PiJobProtocolError(f"Remote response contained invalid {key} data")
        return copy.deepcopy(dict(raw))

    def _refresh_once(self) -> None:
        self._require_capabilities(timeout=_MONITOR_RPC_TIMEOUT_SECONDS)
        machine_response = self._rpc(
            ACTION_MACHINE_STATUS,
            timeout=_MONITOR_RPC_TIMEOUT_SECONDS,
        )
        raw_status = self._response_mapping(machine_response, "status")
        active_response = self._rpc(
            ACTION_JOB_ACTIVE,
            timeout=_MONITOR_RPC_TIMEOUT_SECONDS,
        )
        active_raw = active_response.get("job")
        if active_raw is not None and not isinstance(active_raw, Mapping):
            raise PiJobProtocolError("Remote active-job response was invalid")

        record = copy.deepcopy(dict(active_raw)) if isinstance(active_raw, Mapping) else None
        with self._state_lock:
            # A newly created upload supersedes an older terminal result for
            # monitoring purposes.  Once START dispatch is committed, the same
            # exact tracked UUID remains authoritative until acceptance or a
            # terminal rejection is observed.
            tracked_job_id = self._upload_job_id or (
                self._tracked_job_id
                if self._start_ownership_uncertain
                else self._accepted_job_id
            )
        if record is None and tracked_job_id is not None:
            status_response = self._rpc(
                ACTION_JOB_STATUS,
                {"job_id": tracked_job_id},
                timeout=_MONITOR_RPC_TIMEOUT_SECONDS,
            )
            record = self._response_mapping(status_response, "job")
        elif record is None:
            latest_response = self._rpc(
                ACTION_JOB_LATEST,
                timeout=_MONITOR_RPC_TIMEOUT_SECONDS,
            )
            latest_raw = latest_response.get("job")
            if latest_raw is not None and not isinstance(latest_raw, Mapping):
                raise PiJobProtocolError("Remote latest-job response was invalid")
            if isinstance(latest_raw, Mapping):
                record = copy.deepcopy(dict(latest_raw))
        self._cache_remote_status(raw_status, job_record=record)

    def _monitor_loop(self) -> None:
        delay = 0.0
        failures = 0
        while not self._monitor_stop.wait(delay):
            try:
                self._refresh_once()
            except MachineError as exc:
                failures += 1
                self._mark_monitor_disconnected(str(exc))
                delay = min(
                    _MAX_MONITOR_BACKOFF_SECONDS,
                    self._monitor_interval_seconds * (2 ** min(failures - 1, 4)),
                )
            else:
                failures = 0
                delay = self._monitor_interval_seconds

    def start_monitoring(self) -> None:
        with self._state_lock:
            thread = self._monitor_thread
            if thread is not None and thread.is_alive():
                return
            self._detached = False
            self._monitor_stop.clear()
            thread = threading.Thread(
                target=self._monitor_loop,
                name="remote-machine-status",
                daemon=True,
            )
            self._monitor_thread = thread
        thread.start()

    def detach(self) -> None:
        """Detach this desktop observer without issuing disconnect, M5, or STOP."""

        self._monitor_stop.set()
        # The stop epoch is the local linearization boundary for uploads and
        # START dispatch.  A pre-START operation that has not yet committed
        # ownership observes the new generation and leaves the Pi job inert.
        # No remote STOP is sent here: a committed or accepted Pi job remains
        # wholly Pi-owned while this desktop observer goes away.
        with self._stop_epoch_lock:
            self._stop_epoch += 1
            with self._state_lock:
                self._authorization_epoch += 1
                thread = self._monitor_thread
                self._clear_arm_locked()
                self._upload_job_id = None
                self._detached = True
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)
        with self._state_lock:
            if self._monitor_thread is thread:
                self._monitor_thread = None
        self._mark_monitor_disconnected(None)

    def abandon_start_attempt(self) -> None:
        """Drop desktop preparation state without touching possible Pi execution."""

        with self._state_lock:
            self._authorization_epoch += 1
            self._clear_arm_locked()
            self._upload_job_id = None

    def status(self) -> dict[str, Any]:
        """Return the current cache without performing network I/O."""

        with self._state_lock:
            self._expire_arm_locked()
            status = copy.deepcopy(self._status_cache)
            status["armed"] = self._armed_program_digest is not None
            status["armed_until"] = (
                self._armed_until if self._armed_program_digest is not None else None
            )
            status["arm_phrase"] = self.ARM_PHRASE
            status["pi_owned_execution"] = True
            return status

    def _validate_connection_identity(
        self,
        port: str | None,
        protocol: str | None,
        baudrate: int | None,
    ) -> None:
        if port is not None and port != self.settings.port:
            raise MachineError(
                "A running remote-machine profile cannot switch Pi endpoints; "
                "select or edit the saved machine instead"
            )
        if protocol is not None and protocol != self.settings.protocol:
            raise MachineError(
                "The requested controller protocol does not match the remote profile"
            )
        if baudrate is not None and baudrate != self.settings.baudrate:
            raise MachineError(
                "The requested controller baud rate does not match the remote profile"
            )

    def _machine_status_action(
        self,
        action: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        generation = self._operation_stop_epoch()
        self._require_operation_current(generation)
        self._require_capabilities()
        response = self._rpc(action, payload)
        remote_status = self._response_mapping(response, "status")
        self._cache_remote_status(remote_status, job_record=None)
        self._require_operation_current(generation)
        return self.status()

    def connect(
        self,
        port: str | None = None,
        protocol: str | None = None,
        baudrate: int | None = None,
    ) -> dict[str, Any]:
        self._require_hardware_authority()
        self._validate_connection_identity(port, protocol, baudrate)
        return self._machine_status_action(ACTION_MACHINE_CONNECT)

    def ensure_connected(self) -> dict[str, Any]:
        # The Pi owns the actual connection.  An authenticated idempotent connect
        # verifies it rather than trusting a possibly stale observer cache.
        return self.connect()

    def replace_connection(self) -> dict[str, Any]:
        self._require_hardware_authority()
        return self._machine_status_action(ACTION_MACHINE_REPLACE_CONNECTION)

    def disconnect(self) -> None:
        # Never wait for an upload/START operation while attempting controller
        # cleanup.  Revoking its generation causes every pre-START path to stop
        # at its next bounded RPC boundary; a committed START stays uncertain
        # and therefore remains non-destructive.
        if not self._operation_lock.acquire(blocking=False):
            self.detach()
            return
        try:
            if self.pi_owned_job_active:
                self.detach()
                return
            with self._stop_epoch_lock:
                self._stop_epoch += 1
            self._machine_status_action(ACTION_MACHINE_DISCONNECT)
            with self._state_lock:
                self._authorization_epoch += 1
                self._clear_arm_locked()
        finally:
            self._operation_lock.release()

    def preflight_program(
        self,
        text: str,
        *,
        guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None,
    ) -> ValidatedProgram:
        return self._policy.preflight_program(
            text,
            guarded_output_polygon_mm=guarded_output_polygon_mm,
        )

    def _require_current_program(self, program: ValidatedProgram) -> None:
        if type(program) is not ValidatedProgram:
            raise SafetyError("Program authorization requires an exact preflight result")
        fresh = self._policy.preflight_program(
            "\n".join(program.lines),
            guarded_output_polygon_mm=program.guarded_output_polygon_mm,
        )
        if fresh != program:
            raise SafetyError(
                "Program lines, digest, flags, machine bounds, offsets, feed ceilings, "
                "or hardware gates changed after program preflight; validate the exact "
                "program again"
            )

    def arm(
        self,
        phrase: str,
        *,
        program_digest: str | None = None,
        _expected_stop_epoch: int | None = None,
        _expected_authorization_epoch: int | None = None,
    ) -> float:
        """Reject digest-only arming; remote authority requires the exact program."""

        del phrase, program_digest, _expected_stop_epoch, _expected_authorization_epoch
        raise SafetyError(
            "Remote Pi execution can be armed only with an exact preflighted program"
        )

    def arm_program(self, phrase: str, program: ValidatedProgram) -> float:
        self._require_hardware_authority()
        generation = self._operation_stop_epoch()
        with self._state_lock:
            self._authorization_epoch += 1
            authorization_epoch = self._authorization_epoch
            self._clear_arm_locked()
        self._require_operation_current(generation)
        self._require_current_program(program)
        if type(phrase) is not str or phrase.strip() != self.ARM_PHRASE:
            raise SafetyError("Arming phrase did not match")
        if self.laser_lockout:
            raise SafetyError("Laser output is locked out for this process")
        if self.pi_owned_job_active:
            raise MachineError("Cannot change arming state while a Pi job is active")
        timeout = float(self.laser_settings.arm_timeout_seconds)
        with self._stop_epoch_lock:
            if self._stop_epoch != generation:
                raise MachineError("Arming was cancelled by software STOP")
            with self._state_lock:
                if self._authorization_epoch != authorization_epoch:
                    raise MachineError("Arming was cancelled by disarm")
                self._armed_until = time.time() + timeout
                self._armed_until_monotonic = time.monotonic() + timeout
                self._armed_program_digest = program.digest
                self._armed_authorization_phrase = phrase
                return self._armed_until

    def _consume_start_authorization(
        self,
        program: ValidatedProgram,
    ) -> tuple[str, float, int] | None:
        with self._state_lock:
            self._expire_arm_locked()
            if not program.requires_laser_authorization:
                self._clear_arm_locked()
                return None
            if (
                self._armed_program_digest != program.digest
                or self._armed_authorization_phrase is None
            ):
                self._clear_arm_locked()
                raise SafetyError(
                    "Laser control is not armed for this exact preflighted program"
                )
            phrase = self._armed_authorization_phrase
            expires_monotonic = self._armed_until_monotonic
            authorization_epoch = self._authorization_epoch
            # Authorization is one-use even when upload or START later fails.
            self._clear_arm_locked()
            return phrase, expires_monotonic, authorization_epoch

    def _authorization_phrase_for_start(
        self,
        authorization: tuple[str, float, int] | None,
    ) -> str | None:
        if authorization is None:
            return None
        phrase, expires_monotonic, authorization_epoch = authorization
        with self._state_lock:
            if (
                self._authorization_epoch != authorization_epoch
                or time.monotonic() >= expires_monotonic
            ):
                raise SafetyError(
                    "Laser START authorization expired or was revoked during upload; "
                    "the prepared Pi job remains inert"
                )
        return phrase

    def disarm(self) -> None:
        with self._state_lock:
            self._authorization_epoch += 1
            self._clear_arm_locked()
        if self.pi_owned_job_active:
            self.request_stop(emergency=False)
        # Before START, authorization exists only in this Windows facade. There
        # is no Pi-side grant to clear and therefore no idle disarm RPC.

    def prepare_photo_position(
        self, *, capture_home_position: bool = False
    ) -> dict[str, Any]:
        if type(capture_home_position) is not bool:
            raise TypeError("capture_home_position must be an exact boolean")
        self._require_hardware_authority()
        generation = self._operation_stop_epoch()
        self._require_operation_current(generation)
        self._require_capabilities()
        response = self._rpc(
            ACTION_MACHINE_PREPARE_PHOTO_POSITION,
            {"capture_home_position": capture_home_position},
        )
        result = self._response_mapping(response, "result")
        self._require_operation_current(generation)
        return result

    def prepare_job_start(self) -> dict[str, Any]:
        self._require_hardware_authority()
        generation = self._operation_stop_epoch()
        self._require_operation_current(generation)
        self._require_capabilities()
        response = self._rpc(ACTION_MACHINE_PREPARE_JOB_START)
        result = self._response_mapping(response, "result")
        self._require_operation_current(generation)
        return result

    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> dict[str, Any]:
        self._require_hardware_authority()
        generation = self._operation_stop_epoch()
        self._require_operation_current(generation)
        self._require_capabilities()
        response = self._rpc(
            ACTION_MACHINE_JOG,
            {
                "dx_mm": dx_mm,
                "dy_mm": dy_mm,
                "feed_mm_min": feed_mm_min,
            },
        )
        result = self._response_mapping(response, "result")
        self._require_operation_current(generation)
        return result

    def send_command(
        self,
        line: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
        _expected_stop_epoch: int | None = None,
    ) -> list[str]:
        self._require_hardware_authority()
        if _internal_motion:
            raise SafetyError(
                "Remote clients cannot request internal-motion command privileges"
            )
        generation = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        self._require_operation_current(generation)
        self._require_capabilities()
        payload: dict[str, Any] = {"line": line}
        if timeout is not None:
            payload["timeout"] = timeout
        response = self._rpc(ACTION_MACHINE_COMMAND, payload)
        raw = response.get("responses")
        if not isinstance(raw, list) or any(type(item) is not str for item in raw):
            raise PiJobProtocolError("Remote command response was invalid")
        self._require_operation_current(generation)
        return list(raw)

    def sample_realtime_position(
        self,
        timeout: float = 1.5,
        *,
        coordinate_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_hardware_authority()
        # Coordinate interpretation belongs to the Pi's trusted controller
        # session. A caller-provided local snapshot cannot replace it remotely.
        _ = coordinate_state
        self._require_capabilities()
        response = self._rpc(
            ACTION_MACHINE_REALTIME_POSITION,
            {"timeout": timeout},
        )
        return self._response_mapping(response, "position")

    def _open_stepper_hold(self) -> tuple[socket.socket, Any, str]:
        self._require_hardware_authority()
        self._require_capabilities()
        token = bridge_token_from_environment()
        sock: socket.socket | None = None
        request_id = self._request_id()
        try:
            sock = socket.create_connection(
                (self._target.host, self._target.port),
                timeout=_HOLD_CONNECT_TIMEOUT_SECONDS,
            )
            sock.settimeout(_HOLD_SESSION_TIMEOUT_SECONDS)
            channel = authenticate_client(sock, token)
            channel.send_json(
                {
                    "request_id": request_id,
                    "action": ACTION_MACHINE_STEPPER_HOLD,
                }
            )
            response = self._validate_response(
                channel.receive_json(),
                request_id=request_id,
                action=ACTION_MACHINE_STEPPER_HOLD,
            )
            lease_id = response.get("lease_id")
            if response.get("state") != "held" or not isinstance(lease_id, str):
                raise PiJobProtocolError(
                    "Remote stepper-hold response did not establish a lease"
                )
            return sock, channel, lease_id
        except Exception:
            if sock is not None:
                sock.close()
            raise

    def _release_stepper_hold(self, channel: Any, lease_id: str) -> None:
        request_id = self._request_id()
        channel.send_json(
            {
                "request_id": request_id,
                "action": ACTION_MACHINE_STEPPER_HOLD_RELEASE,
                "lease_id": lease_id,
            }
        )
        response = self._validate_response(
            channel.receive_json(),
            request_id=request_id,
            action=ACTION_MACHINE_STEPPER_HOLD_RELEASE,
        )
        if response.get("state") != "released" or response.get("lease_id") != lease_id:
            raise PiJobProtocolError(
                "Remote stepper-hold release did not match its active lease"
            )

    @contextmanager
    def temporary_stepper_hold(self):
        """Hold Pi steppers across local camera work on one authenticated session."""

        with self._hold_lock:
            depth = int(getattr(self._hold_context, "depth", 0))
            if depth:
                self._hold_context.depth = depth + 1
                try:
                    yield
                finally:
                    self._hold_context.depth -= 1
                return

            generation = self._operation_stop_epoch()
            self._require_operation_current(generation)
            sock, channel, lease_id = self._open_stepper_hold()
            self._hold_context.depth = 1
            body_error: BaseException | None = None
            try:
                yield
            except BaseException as exc:
                body_error = exc
                raise
            finally:
                self._hold_context.depth = 0
                try:
                    self._release_stepper_hold(channel, lease_id)
                except Exception as cleanup_error:
                    if body_error is None:
                        raise
                    add_note = getattr(body_error, "add_note", None)
                    if callable(add_note):
                        add_note(
                            f"Remote temporary stepper-hold cleanup failed: {cleanup_error}"
                        )
                finally:
                    sock.close()

    @staticmethod
    def _start_binding(program: ValidatedProgram) -> dict[str, Any]:
        return {
            "program_digest": program.digest,
            "requires_laser_authorization": program.requires_laser_authorization,
            "requires_motion": program.requires_motion,
            "guarded_output_polygon_mm": _polygon_payload(
                program.guarded_output_polygon_mm
            ),
            "execution_policy_digest": execution_policy_digest(program),
        }

    def _mark_start_uncertain(
        self,
        *,
        job_id: str,
        name: str,
        program: ValidatedProgram,
        generation: int,
    ) -> None:
        # This is the local dispatch commitment. Detach linearizes either before
        # it (generation changes and START is never sent) or after it (ownership
        # remains uncertain/accepted and detach stays non-destructive).
        with self._stop_epoch_lock:
            if self._stop_epoch != generation:
                raise MachineError("Job START was cancelled by desktop detach")
            with self._state_lock:
                self._accepted_job_id = None
                self._start_ownership_uncertain = True
                self._tracked_job_id = job_id
                self._tracked_program_digest = program.digest
                previous = self._status_cache.get("job")
                record = dict(previous) if isinstance(previous, Mapping) else {}
                record.update(
                    {
                        "job_id": job_id,
                        "name": name,
                        "state": "starting",
                        "program_digest": program.digest,
                        "requires_laser_authorization": (
                            program.requires_laser_authorization
                        ),
                        "requires_motion": program.requires_motion,
                    }
                )
                self._observe_job_locked(record)
        self._mark_monitor_disconnected(
            "START response was lost; querying the same Pi job before any further action"
        )

    def _recover_lost_start(
        self,
        *,
        job_id: str,
        original_error: MachineError,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + _START_RECOVERY_POLL_SECONDS
        while True:
            try:
                response = self._rpc(ACTION_JOB_STATUS, {"job_id": job_id})
                record = self._response_mapping(response, "job")
            except MachineError as recovery_error:
                self._mark_monitor_disconnected(str(recovery_error))
                raise MachineError(
                    "The Pi START response was lost and job ownership is uncertain. "
                    "The desktop did not retry START; reconnect and inspect this same "
                    f"job ({job_id}) before taking further action. Initial error: "
                    f"{original_error}"
                ) from recovery_error

            state = _job_state(record)
            ownership_accepted = record.get("ownership_accepted") is True
            if ownership_accepted and (
                state in _ACTIVE_JOB_STATES or state in _TERMINAL_JOB_STATES
            ):
                job = self._cache_job_record(record, accepted=True)
                return self._start_result(job, accepted=True, duplicate=True)

            self._cache_job_record(record)
            if state not in _ACTIVE_JOB_STATES:
                with self._state_lock:
                    self._start_ownership_uncertain = False
                    self._accepted_job_id = None
                self._cache_job_record(record, accepted=False)
                raise MachineError(
                    "The Pi did not accept START before the response was lost; the "
                    "prepared or failed job was not restarted automatically"
                ) from original_error
            if time.monotonic() >= deadline:
                raise MachineError(
                    "The Pi is still performing final START checks without a durable "
                    "ownership-acceptance marker. The desktop did not retry START; "
                    f"continue monitoring this same job ({job_id})"
                ) from original_error
            time.sleep(_START_RECOVERY_POLL_INTERVAL_SECONDS)

    @staticmethod
    def _start_result(
        job: Mapping[str, Any],
        *,
        accepted: bool,
        duplicate: bool,
    ) -> dict[str, Any]:
        result = copy.deepcopy(dict(job))
        result.update(
            {
                "accepted": accepted,
                "duplicate": duplicate,
                "execution_owner": "pi",
            }
        )
        return result

    def start_validated_program(
        self,
        program: ValidatedProgram,
        name: str = "generated.gcode",
        *,
        _expected_stop_epoch: int | None = None,
    ) -> dict[str, Any]:
        generation = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._operation_lock:
            self._require_hardware_authority()
            self._require_operation_current(generation)
            self._require_capabilities()
            self._require_current_program(program)
            validated_name = validate_job_name(name)
            canonical = canonical_program_bytes(program)
            if not 1 <= len(canonical) <= MAX_JOB_BYTES:
                raise MachineError(
                    f"Canonical job must contain 1 through {MAX_JOB_BYTES} bytes"
                )
            if hashlib.sha256(canonical).hexdigest() != program.digest:
                raise SafetyError("Canonical job bytes did not match the preflight digest")
            authorization = self._consume_start_authorization(program)
            job_id = str(uuid.uuid4())
            local_job_created_at = time.time()
            self._record_local_job_identity(job_id, local_job_created_at)
            self._record_client_diagnostics(
                job_id,
                job_created_at=local_job_created_at,
            )
            binding = self._start_binding(program)
            with self._state_lock:
                cached_job = self._status_cache.get("job")
                accepted_active = bool(
                    isinstance(cached_job, Mapping)
                    and cached_job.get("ownership_accepted") is True
                    and _job_state(cached_job) in _ACTIVE_JOB_STATES
                )
                if self._start_ownership_uncertain or accepted_active:
                    raise MachineError(
                        "Cannot prepare another job while Pi execution is active or "
                        "START ownership is uncertain"
                    )
                # A completed prior UUID must not outrank this new upload in the
                # background monitor. The local identity map remains available
                # for exact receipt validation.
                self._accepted_job_id = None
                self._upload_job_id = job_id
                self._tracked_job_id = job_id
                self._tracked_program_digest = program.digest

            upload_started = time.monotonic()
            try:
                begin = self._rpc(
                    ACTION_JOB_BEGIN,
                    {
                        "job_id": job_id,
                        "name": validated_name,
                        "expected_size": len(canonical),
                        "expected_sha256": program.digest,
                        "guarded_output_polygon_mm": binding[
                            "guarded_output_polygon_mm"
                        ],
                    },
                )
                self._cache_job_record(self._response_mapping(begin, "job"))
                offset = 0
                while offset < len(canonical):
                    self._require_operation_current(generation)
                    chunk = canonical[offset : offset + MAX_UPLOAD_CHUNK_BYTES]
                    chunk_response = self._rpc(
                        ACTION_JOB_CHUNK,
                        {
                            "job_id": job_id,
                            "offset": offset,
                            "data_b64": encode_upload_chunk(chunk),
                        },
                    )
                    self._cache_job_record(
                        self._response_mapping(chunk_response, "job")
                    )
                    offset += len(chunk)
                upload_seconds = max(0.0, time.monotonic() - upload_started)
                self._record_client_diagnostics(
                    job_id,
                    upload_bytes=len(canonical),
                    upload_seconds=round(upload_seconds, 6),
                    throughput_bps=round(
                        len(canonical) / max(upload_seconds, 1e-9),
                        3,
                    ),
                )
                self._require_operation_current(generation)
                finalize_started = time.monotonic()
                finalize = self._rpc(
                    ACTION_JOB_FINALIZE,
                    {"job_id": job_id, **binding},
                )
                if finalize.get("ready") is not True:
                    raise PiJobProtocolError(
                        "Remote job finalization did not report an exact READY state"
                    )
                finalize_seconds = max(0.0, time.monotonic() - finalize_started)
                self._record_client_diagnostics(
                    job_id,
                    finalize_seconds=round(finalize_seconds, 6),
                )
                self._cache_job_record(self._response_mapping(finalize, "job"))
                self._require_operation_current(generation)
            except Exception:
                with self._state_lock:
                    self._upload_job_id = None
                raise

            try:
                authorization_phrase = self._authorization_phrase_for_start(
                    authorization
                )
            except Exception:
                with self._state_lock:
                    self._upload_job_id = None
                raise
            start_payload: dict[str, Any] = {"job_id": job_id, **binding}
            if authorization_phrase is not None:
                start_payload["authorization_phrase"] = authorization_phrase
            self._mark_start_uncertain(
                job_id=job_id,
                name=validated_name,
                program=program,
                generation=generation,
            )
            start_requested = time.monotonic()
            try:
                start = self._rpc(ACTION_JOB_START, start_payload)
            except _RemoteRequestRejected:
                with self._state_lock:
                    self._start_ownership_uncertain = False
                    self._accepted_job_id = None
                    self._upload_job_id = None
                try:
                    rejected_status = self._rpc(
                        ACTION_JOB_STATUS,
                        {"job_id": job_id},
                    )
                    self._cache_job_record(
                        self._response_mapping(rejected_status, "job"),
                        accepted=False,
                    )
                except MachineError:
                    pass
                raise
            except MachineError as exc:
                with self._state_lock:
                    self._upload_job_id = None
                return self._recover_lost_start(job_id=job_id, original_error=exc)
            finally:
                self._record_client_diagnostics(
                    job_id,
                    start_latency_seconds=round(
                        max(0.0, time.monotonic() - start_requested),
                        6,
                    ),
                )

            start_job = self._response_mapping(start, "job")
            start_accepted_at = start_job.get("start_accepted_at")
            durable_acceptance = bool(
                start_job.get("ownership_accepted") is True
                and type(start_accepted_at) in {int, float}
                and math.isfinite(float(start_accepted_at))
            )
            if (
                start.get("accepted") is not True
                or start.get("execution_owner") != "pi"
                or not durable_acceptance
            ):
                invalid = PiJobProtocolError(
                    "Remote START response did not transfer execution ownership to the Pi"
                )
                with self._state_lock:
                    self._upload_job_id = None
                return self._recover_lost_start(job_id=job_id, original_error=invalid)
            job = self._cache_job_record(
                start_job,
                accepted=True,
            )
            with self._state_lock:
                self._upload_job_id = None
            duplicate = start.get("duplicate")
            if type(duplicate) is not bool:
                raise PiJobProtocolError(
                    "Remote START response contained an invalid duplicate flag"
                )
            return self._start_result(job, accepted=True, duplicate=duplicate)

    def start_preflighted_program(
        self,
        program: ValidatedProgram,
        name: str = "generated.gcode",
        *,
        authorization_phrase: str | None = None,
    ) -> dict[str, Any]:
        self._require_current_program(program)
        if program.requires_laser_authorization:
            if authorization_phrase is None:
                raise SafetyError(
                    "This powered program requires explicit START authorization"
                )
            self.arm_program(authorization_phrase, program)
        return self.start_validated_program(program, name)

    def start_job(self, text: str, name: str = "generated.gcode") -> dict[str, Any]:
        generation = self._operation_stop_epoch()
        try:
            program = self.preflight_program(text)
        except Exception:
            operation_current = self.operation_generation() == generation
            with self._state_lock:
                if operation_current:
                    self._clear_arm_locked()
            raise
        return self.start_validated_program(
            program,
            name,
            _expected_stop_epoch=generation,
        )

    def request_stop(self, emergency: bool = False) -> None:
        if type(emergency) is not bool:
            raise TypeError("emergency must be an exact boolean")
        with self._stop_epoch_lock:
            self._stop_epoch += 1
        with self._state_lock:
            self._authorization_epoch += 1
            self._clear_arm_locked()
            job_id = self._accepted_job_id or self._tracked_job_id
        payload: dict[str, Any] = {"emergency": emergency}
        if job_id is not None:
            payload["job_id"] = job_id
        response = self._rpc(
            ACTION_JOB_STOP,
            payload,
            timeout=_STOP_RPC_TIMEOUT_SECONDS,
        )
        raw_job = response.get("job")
        if raw_job is not None:
            if not isinstance(raw_job, Mapping):
                raise PiJobProtocolError("Remote STOP response contained invalid job data")
            self._cache_job_record(raw_job, accepted=True)

    def stop_job(self, emergency: bool = False) -> None:
        self.request_stop(emergency=emergency)

    @staticmethod
    def _receipt_from_job(
        job: Mapping[str, Any],
        *,
        program_digest: str,
    ) -> dict[str, Any] | None:
        candidate = job.get("receipt", job.get("last_successful_job"))
        if isinstance(candidate, Mapping):
            receipt = copy.deepcopy(dict(candidate))
            if "job_id" not in receipt and isinstance(job.get("job_id"), str):
                receipt["job_id"] = job["job_id"]
        elif _job_state(job) == "complete" and not job.get("error"):
            receipt = {
                "name": job.get("name", ""),
                "program_digest": job.get(
                    "program_digest", job.get("expected_sha256")
                ),
                "powered": job.get(
                    "powered", job.get("requires_laser_authorization", False)
                ),
                "started_at": job.get("started_at"),
                "finished_at": job.get("finished_at"),
                "completed_lines": job.get(
                    "completed_lines", job.get("completed_commands", 0)
                ),
                "total_lines": job.get("total_lines", job.get("total_commands", 0)),
                "backend": job.get("backend", "serial"),
                "protocol": job.get("protocol"),
                "hardware_enabled": job.get("hardware_enabled", True),
                "execution_owner": "pi",
                "job_id": job.get("job_id"),
            }
        else:
            return None
        finished_at = receipt.get("finished_at")
        if (
            receipt.get("program_digest") != program_digest
            or type(finished_at) not in {int, float}
            or not math.isfinite(float(finished_at))
        ):
            return None
        return receipt

    def _receipt_started_locally_after(
        self,
        receipt: Mapping[str, Any],
        threshold: float,
    ) -> bool:
        job_id = receipt.get("job_id")
        if not isinstance(job_id, str):
            return False
        with self._state_lock:
            created_at = self._local_job_created_at.get(job_id)
        return created_at is not None and created_at >= threshold

    def successful_job_receipt(
        self,
        program_digest: str,
        *,
        not_before: float,
    ) -> dict[str, Any] | None:
        if (
            type(program_digest) is not str
            or _PROGRAM_DIGEST_PATTERN.fullmatch(program_digest) is None
            or type(not_before) not in {int, float}
            or not math.isfinite(float(not_before))
        ):
            return None
        threshold = float(not_before)
        with self._state_lock:
            status = copy.deepcopy(self._status_cache)
            job_id = self._tracked_job_id
        last = status.get("last_successful_job")
        if isinstance(last, Mapping):
            receipt = self._receipt_from_job(
                {"state": "complete", "receipt": last},
                program_digest=program_digest,
            )
            if receipt is not None and self._receipt_started_locally_after(
                receipt,
                threshold,
            ):
                return receipt
        cached_job = status.get("job")
        if isinstance(cached_job, Mapping):
            receipt = self._receipt_from_job(
                cached_job,
                program_digest=program_digest,
            )
            if receipt is not None and self._receipt_started_locally_after(
                receipt,
                threshold,
            ):
                return receipt
        if job_id is None:
            return None
        try:
            response = self._rpc(ACTION_JOB_RESULT, {"job_id": job_id})
            job = self._response_mapping(response, "job")
        except MachineError:
            return None
        self._cache_job_record(job, accepted=True)
        receipt = self._receipt_from_job(
            job,
            program_digest=program_digest,
        )
        if receipt is None or not self._receipt_started_locally_after(
            receipt,
            threshold,
        ):
            return None
        return receipt


__all__ = ["RemoteMachineService"]
