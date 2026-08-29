from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from ..errors import MachineError, SafetyError
from .pi_job_protocol import validate_guarded_output_polygon
from .pi_job_store import JobValidation, PiJobStore
from .service import MachineService, ValidatedProgram

LOGGER = logging.getLogger(__name__)

EXECUTION_OWNER = "pi"
EXECUTION_POLICY_SCHEMA = "e3-pi-execution-policy-v1"

_ACTIVE_STATES = frozenset({"starting", "running", "stopping"})
_TERMINAL_STATES = frozenset({"complete", "failed", "stopped", "interrupted"})
_STARTED_STATES = _ACTIVE_STATES | _TERMINAL_STATES
_MAX_PUBLIC_ERROR_CHARS = 512
_WATCH_INTERVAL_SECONDS = 0.1

_PUBLIC_RECORD_FIELDS = frozenset(
    {
        "job_id",
        "revision",
        "name",
        "state",
        "expected_size",
        "expected_sha256",
        "received_size",
        "guarded_output_polygon_mm",
        "program_digest",
        "requires_laser_authorization",
        "requires_motion",
        "execution_policy_digest",
        "verification_seconds",
        "start_latency_seconds",
        "start_accepted_at",
        "ownership_accepted",
        "created_at",
        "prepared_at",
        "started_at",
        "finished_at",
        "updated_at",
        "completed_lines",
        "total_lines",
        "phase",
        "powered",
        "protocol",
        "error",
        "result",
        "program_retained",
    }
)


class PiJobServiceError(MachineError):
    """A high-level Pi-owned machine or job request was rejected."""


def execution_policy_digest(program: ValidatedProgram) -> str:
    """Return a stable digest of the exact local preflight safety authority."""

    if type(program) is not ValidatedProgram or type(program.safety_profile) is not tuple:
        raise SafetyError("Execution policy requires an exact local preflight result")
    try:
        encoded = json.dumps(
            {
                "schema": EXECUTION_POLICY_SCHEMA,
                "safety_profile": program.safety_profile,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise SafetyError("Execution policy contains non-canonical values") from exc
    return hashlib.sha256(encoded).hexdigest()


def canonical_program_bytes(program: ValidatedProgram) -> bytes:
    """Return the one byte representation accepted by the Pi job store."""

    if type(program) is not ValidatedProgram or type(program.lines) is not tuple:
        raise SafetyError("Canonical bytes require an exact local preflight result")
    if any(type(line) is not str for line in program.lines):
        raise SafetyError("Canonical program lines must be strings")
    return "\n".join(program.lines).encode("utf-8")


def _bounded_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = type(exc).__name__
    return text[:_MAX_PUBLIC_ERROR_CHARS]


def _record_value(record: Mapping[str, Any], key: str, default: Any = None) -> Any:
    return record[key] if key in record else default


def _public_record(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        key: value
        for key, value in record.items()
        if key in _PUBLIC_RECORD_FIELDS
    }


def _normalize_polygon(
    raw: object,
) -> tuple[tuple[float, float], ...] | None:
    return validate_guarded_output_polygon(raw)


class PiJobService:
    """Own one Pi-local MachineService and its durable high-level job lifecycle."""

    def __init__(
        self,
        machine: MachineService,
        store: PiJobStore,
        *,
        boot_id: str | None = None,
        watch_interval_seconds: float = _WATCH_INTERVAL_SECONDS,
    ):
        if not isinstance(machine, MachineService):
            raise TypeError("machine must be a MachineService")
        if not isinstance(store, PiJobStore):
            raise TypeError("store must be a PiJobStore")
        if (
            type(watch_interval_seconds) not in {int, float}
            or not math.isfinite(float(watch_interval_seconds))
            or float(watch_interval_seconds) <= 0.0
        ):
            raise ValueError("watch_interval_seconds must be positive and finite")
        self.machine = machine
        self.store = store
        self.boot_id = str(uuid.uuid4()) if boot_id is None else str(boot_id)
        self._watch_interval = float(watch_interval_seconds)
        self._ordinary_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._cache_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._watcher: threading.Thread | None = None
        self._active_job_id: str | None = None
        self._stop_requested_for: str | None = None
        reconciled = self.store.reconcile_boot()
        interrupted = [
            record
            for record in reconciled
            if _record_value(record, "state") == "interrupted"
        ]
        if not interrupted:
            interrupted = [
                record
                for record in self.store.list_records()
                if _record_value(record, "state") == "interrupted"
            ]
        for record in interrupted:
            LOGGER.warning(
                "Pi job %s is interrupted after node restart; it will not resume",
                str(_record_value(record, "job_id", "unknown"))[:8],
            )
        active = self.store.active()
        if active is not None:
            # A store implementation must never return an active record after
            # reconciliation. Refuse to adopt or resume it if it does.
            job_id = str(_record_value(active, "job_id", ""))
            if job_id:
                self.store.update_state(
                    job_id,
                    "interrupted",
                    phase="interrupted",
                    error="Pi process restarted before job completion",
                    finished_at=time.time(),
                )
        self._latest_terminal_record: dict[str, Any] | None = None
        self._refresh_latest_result_cache()
        self._cached_machine_status: dict[str, Any] = {}
        self._refresh_machine_status()

    @staticmethod
    def _require_sha256(value: object, label: str) -> str:
        if type(value) is not str or len(value) != 64:
            raise PiJobServiceError(f"{label} must be a lowercase SHA-256 digest")
        try:
            decoded = bytes.fromhex(value)
        except ValueError as exc:
            raise PiJobServiceError(
                f"{label} must be a lowercase SHA-256 digest"
            ) from exc
        if len(decoded) != 32 or value != value.lower():
            raise PiJobServiceError(f"{label} must be a lowercase SHA-256 digest")
        return value

    @staticmethod
    def _require_flag(value: object, label: str) -> bool:
        if type(value) is not bool:
            raise PiJobServiceError(f"{label} must be a JSON boolean")
        return value

    @staticmethod
    def _same_digest(left: str, right: str) -> bool:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))

    def _assert_program_binding(
        self,
        program: ValidatedProgram,
        *,
        program_digest: object,
        requires_laser_authorization: object,
        requires_motion: object,
        guarded_output_polygon_mm: object,
        policy_digest: object,
    ) -> None:
        expected_program_digest = self._require_sha256(
            program_digest,
            "Program digest",
        )
        expected_policy_digest = self._require_sha256(
            policy_digest,
            "Execution-policy digest",
        )
        expected_powered = self._require_flag(
            requires_laser_authorization,
            "requires_laser_authorization",
        )
        expected_motion = self._require_flag(requires_motion, "requires_motion")
        expected_polygon = _normalize_polygon(guarded_output_polygon_mm)
        actual_policy_digest = execution_policy_digest(program)
        if not self._same_digest(program.digest, expected_program_digest):
            raise SafetyError("Uploaded program digest does not match local preflight")
        if program.requires_laser_authorization is not expected_powered:
            raise SafetyError("Uploaded powered-program flag does not match local preflight")
        if program.requires_motion is not expected_motion:
            raise SafetyError("Uploaded motion flag does not match local preflight")
        if program.guarded_output_polygon_mm != expected_polygon:
            raise SafetyError("Uploaded guarded output authority does not match local preflight")
        if not self._same_digest(actual_policy_digest, expected_policy_digest):
            raise SafetyError("Execution policy does not match Pi-local safety settings")

    def _validate_canonical_text(
        self,
        text: str,
        polygon: tuple[tuple[float, float], ...] | None,
    ) -> ValidatedProgram:
        if type(text) is not str:
            raise SafetyError("Stored program is not UTF-8 text")
        program = self.machine.preflight_program(
            text,
            guarded_output_polygon_mm=polygon,
        )
        if text.encode("utf-8") != canonical_program_bytes(program):
            raise SafetyError(
                "Uploaded program bytes are not the canonical preflight representation"
            )
        return program

    def begin_upload(
        self,
        job_id: str,
        name: str,
        expected_size: int,
        expected_sha256: str,
        guarded_output_polygon_mm: object = None,
    ) -> dict[str, Any]:
        with self._ordinary_lock:
            record = self.store.begin(
                job_id,
                name=name,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                guarded_output_polygon_mm=_normalize_polygon(
                    guarded_output_polygon_mm
                ),
            )
        LOGGER.info(
            "Pi job upload begun id=%s name=%r size=%d",
            str(_record_value(record, "job_id", "unknown"))[:8],
            str(_record_value(record, "name", ""))[:80],
            int(_record_value(record, "expected_size", 0)),
        )
        return _public_record(record) or {}

    def append_upload_chunk(
        self,
        job_id: str,
        offset: int,
        data: bytes,
    ) -> dict[str, Any]:
        with self._ordinary_lock:
            record = self.store.append_chunk(job_id, offset=offset, data=data)
        return _public_record(record) or {}

    def finalize_upload(
        self,
        job_id: str,
        *,
        program_digest: str,
        requires_laser_authorization: bool,
        requires_motion: bool,
        guarded_output_polygon_mm: object,
        policy_digest: str,
    ) -> dict[str, Any]:
        verification_started = time.monotonic()
        expected_polygon = _normalize_polygon(guarded_output_polygon_mm)

        def validator(
            text: str,
            stored_polygon: tuple[tuple[float, float], ...] | None,
        ) -> JobValidation:
            if stored_polygon != expected_polygon:
                raise SafetyError(
                    "Finalized guarded output authority differs from upload metadata"
                )
            program = self._validate_canonical_text(text, stored_polygon)
            self._assert_program_binding(
                program,
                program_digest=program_digest,
                requires_laser_authorization=requires_laser_authorization,
                requires_motion=requires_motion,
                guarded_output_polygon_mm=expected_polygon,
                policy_digest=policy_digest,
            )
            return JobValidation(
                program_digest=program.digest,
                requires_laser_authorization=program.requires_laser_authorization,
                requires_motion=program.requires_motion,
                execution_policy_digest=execution_policy_digest(program),
                value=program,
            )

        with self._ordinary_lock:
            self.store.finalize(job_id, validator=validator)
            verification_seconds = round(
                max(0.0, time.monotonic() - verification_started),
                6,
            )
            record = self.store.update_state(
                job_id,
                "prepared",
                verification_seconds=verification_seconds,
            )
        LOGGER.info(
            "Pi job verified and prepared id=%s bytes=%d verify_seconds=%.6f",
            str(_record_value(record, "job_id", "unknown"))[:8],
            int(_record_value(record, "expected_size", 0)),
            verification_seconds,
        )
        return _public_record(record) or {}

    def _assert_record_binding(
        self,
        record: Mapping[str, Any],
        *,
        program_digest: str,
        requires_laser_authorization: bool,
        requires_motion: bool,
        guarded_output_polygon_mm: object,
        policy_digest: str,
    ) -> None:
        stored_digest = str(_record_value(record, "program_digest", ""))
        stored_policy = str(_record_value(record, "execution_policy_digest", ""))
        expected_digest = self._require_sha256(program_digest, "Program digest")
        expected_policy = self._require_sha256(
            policy_digest,
            "Execution-policy digest",
        )
        if not self._same_digest(stored_digest, expected_digest):
            raise SafetyError("START program digest does not match the prepared job")
        if not self._same_digest(stored_policy, expected_policy):
            raise SafetyError("START execution policy does not match the prepared job")
        if (
            _record_value(record, "requires_laser_authorization")
            is not self._require_flag(
                requires_laser_authorization,
                "requires_laser_authorization",
            )
        ):
            raise SafetyError("START powered-program flag does not match the prepared job")
        if _record_value(record, "requires_motion") is not self._require_flag(
            requires_motion,
            "requires_motion",
        ):
            raise SafetyError("START motion flag does not match the prepared job")
        if _normalize_polygon(
            _record_value(record, "guarded_output_polygon_mm")
        ) != _normalize_polygon(guarded_output_polygon_mm):
            raise SafetyError("START guarded output authority does not match the prepared job")

    def _preflight_committed_program(
        self,
        record: Mapping[str, Any],
    ) -> ValidatedProgram:
        job_id = str(record["job_id"])
        raw = self.store.read_program_bytes(job_id)
        expected_size = _record_value(record, "expected_size")
        expected_sha256 = str(_record_value(record, "expected_sha256", ""))
        if type(expected_size) is not int or len(raw) != expected_size:
            raise SafetyError("Committed program byte count changed after finalization")
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if not self._same_digest(actual_sha256, expected_sha256):
            raise SafetyError("Committed program digest changed after finalization")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise SafetyError("Committed program is not valid UTF-8") from exc
        polygon = _normalize_polygon(
            _record_value(record, "guarded_output_polygon_mm")
        )
        program = self._validate_canonical_text(text, polygon)
        self._assert_program_binding(
            program,
            program_digest=_record_value(record, "program_digest"),
            requires_laser_authorization=_record_value(
                record,
                "requires_laser_authorization",
            ),
            requires_motion=_record_value(record, "requires_motion"),
            guarded_output_polygon_mm=polygon,
            policy_digest=_record_value(record, "execution_policy_digest"),
        )
        if not self._same_digest(program.digest, actual_sha256):
            raise SafetyError("Committed bytes are not bound to the canonical program digest")
        return program

    def _current_active_record(self) -> Mapping[str, Any] | None:
        active = self.store.active()
        if active is not None:
            return active
        with self._state_lock:
            active_job_id = self._active_job_id
        if active_job_id is None:
            return None
        try:
            record = self.store.get(active_job_id)
        except Exception:
            return None
        if _record_value(record, "state") in _ACTIVE_STATES:
            return record
        return None

    def _require_idle(self, operation: str) -> None:
        active = self._current_active_record()
        if active is not None:
            raise PiJobServiceError(
                f"Cannot {operation} while Pi-owned job {active['job_id']} is active"
            )

    def _update_terminal(
        self,
        job_id: str,
        state: str,
        *,
        job: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "phase": state,
            "finished_at": time.time(),
            "error": None if error is None else error[:_MAX_PUBLIC_ERROR_CHARS],
        }
        if job is not None:
            fields.update(
                {
                    "completed_lines": int(job.get("completed_lines") or 0),
                    "total_lines": int(job.get("total_lines") or 0),
                    "powered": bool(job.get("powered")),
                }
            )
        record = self.store.update_state(job_id, state, **fields)
        log = LOGGER.info if state == "complete" else LOGGER.warning
        log("Pi job %s reached terminal state %s", job_id[:8], state)
        with self._state_lock:
            if record.get("ownership_accepted") is True:
                self._latest_terminal_record = dict(record)
            if self._active_job_id == job_id:
                self._active_job_id = None
            if self._stop_requested_for == job_id:
                self._stop_requested_for = None
        return record

    def _start_watcher(self, job_id: str, program_digest: str) -> None:
        watcher = threading.Thread(
            target=self._watch_job,
            args=(job_id, program_digest),
            name=f"pi-job-watch-{job_id[:8]}",
            daemon=True,
        )
        watcher.start()
        with self._state_lock:
            self._watcher = watcher

    def _watch_job(self, job_id: str, program_digest: str) -> None:
        last_progress: tuple[object, ...] | None = None
        try:
            while not self._stop_event.is_set():
                machine_status = self.machine.status()
                self._set_machine_status(machine_status)
                job = machine_status.get("job")
                if not isinstance(job, dict):
                    raise PiJobServiceError("Machine status omitted its local job record")
                if job.get("program_digest") != program_digest:
                    raise PiJobServiceError("Local runner changed its active program identity")
                progress = (
                    job.get("phase"),
                    job.get("completed_lines"),
                    job.get("total_lines"),
                    job.get("running"),
                )
                if progress != last_progress and bool(job.get("running")):
                    with self._state_lock:
                        stop_requested = self._stop_requested_for == job_id
                    if not stop_requested:
                        try:
                            self.store.update_state(
                                job_id,
                                "running",
                                phase=str(job.get("phase") or "running")[:64],
                                completed_lines=int(
                                    job.get("completed_lines") or 0
                                ),
                                total_lines=int(job.get("total_lines") or 0),
                                powered=bool(job.get("powered")),
                            )
                        except Exception:
                            # STOP may have durably advanced running -> stopping
                            # between the fast flag check and this atomic write.
                            with self._state_lock:
                                stop_requested = (
                                    self._stop_requested_for == job_id
                                )
                            if not stop_requested:
                                raise
                        else:
                            last_progress = progress
                if not bool(job.get("running")):
                    with self._state_lock:
                        was_stopped = self._stop_requested_for == job_id
                    error = job.get("error")
                    if was_stopped:
                        self._update_terminal(job_id, "stopped", job=job)
                    elif job.get("phase") == "complete" and error is None:
                        self._update_terminal(job_id, "complete", job=job)
                    else:
                        self._update_terminal(
                            job_id,
                            "failed",
                            job=job,
                            error=str(error or "Pi-local machine execution failed"),
                        )
                    self._refresh_machine_status()
                    return
                self._stop_event.wait(self._watch_interval)
        except Exception as exc:
            self.machine.request_stop(emergency=False)
            try:
                self._update_terminal(
                    job_id,
                    "failed",
                    error=_bounded_error(exc),
                )
            except Exception:
                pass
            self._refresh_machine_status()

    def start(
        self,
        job_id: str,
        *,
        program_digest: str,
        requires_laser_authorization: bool,
        requires_motion: bool,
        guarded_output_polygon_mm: object,
        policy_digest: str,
        authorization_phrase: str | None = None,
    ) -> dict[str, Any]:
        start_requested = time.monotonic()
        requested_generation = self.machine.operation_generation()
        with self._ordinary_lock:
            record = self.store.get(job_id)
            self._assert_record_binding(
                record,
                program_digest=program_digest,
                requires_laser_authorization=requires_laser_authorization,
                requires_motion=requires_motion,
                guarded_output_polygon_mm=guarded_output_polygon_mm,
                policy_digest=policy_digest,
            )
            state = str(_record_value(record, "state", ""))
            if state in _STARTED_STATES:
                LOGGER.debug(
                    "Duplicate START observed for Pi job %s in state %s",
                    job_id[:8],
                    state,
                )
                return {
                    "accepted": (
                        record.get("ownership_accepted") is True
                        and state != "interrupted"
                    ),
                    "duplicate": True,
                    "execution_owner": EXECUTION_OWNER,
                    "job": _public_record(record),
                }
            if state != "prepared":
                raise PiJobServiceError("Only a fully prepared job can be started")
            active = self._current_active_record()
            if active is not None and active.get("job_id") != job_id:
                raise PiJobServiceError(
                    f"Pi-owned job {active['job_id']} is already active"
                )
            try:
                program = self._preflight_committed_program(record)
                self._assert_program_binding(
                    program,
                    program_digest=program_digest,
                    requires_laser_authorization=requires_laser_authorization,
                    requires_motion=requires_motion,
                    guarded_output_polygon_mm=guarded_output_polygon_mm,
                    policy_digest=policy_digest,
                )
            except Exception as exc:
                self._update_terminal(
                    job_id,
                    "failed",
                    error=_bounded_error(exc),
                )
                raise
            record = self.store.update_state(
                job_id,
                "starting",
                phase="starting",
                error=None,
            )
            with self._state_lock:
                self._active_job_id = job_id
                self._stop_requested_for = None
            try:
                with self.machine.operation_scope(requested_generation):
                    self.machine.ensure_connected()
                    local_job = self.machine.start_preflighted_program(
                        program,
                        str(_record_value(record, "name", "generated.gcode")),
                        authorization_phrase=authorization_phrase,
                    )
                if not isinstance(local_job, dict) or not bool(local_job.get("running")):
                    raise PiJobServiceError(
                        "Local MachineService did not accept the prepared job"
                    )
                machine_status = self.machine.status()
                record = self.store.update_state(
                    job_id,
                    "running",
                    phase=str(local_job.get("phase") or "streaming")[:64],
                    started_at=float(local_job.get("started_at") or time.time()),
                    completed_lines=int(local_job.get("completed_lines") or 0),
                    total_lines=int(local_job.get("total_lines") or len(program.lines)),
                    powered=bool(local_job.get("powered")),
                    protocol=machine_status.get("protocol"),
                    ownership_accepted=True,
                    start_accepted_at=time.time(),
                    start_latency_seconds=round(
                        max(0.0, time.monotonic() - start_requested),
                        6,
                    ),
                )
                self._set_machine_status(machine_status)
                self._start_watcher(job_id, program.digest)
                LOGGER.info(
                    "Pi job START accepted; local execution began id=%s lines=%d",
                    job_id[:8],
                    int(local_job.get("total_lines") or len(program.lines)),
                )
            except Exception as exc:
                self.machine.request_stop(emergency=False)
                with self._state_lock:
                    was_stopped = self._stop_requested_for == job_id
                was_stopped = was_stopped or (
                    self.machine.operation_generation() != requested_generation
                )
                self._update_terminal(
                    job_id,
                    "stopped" if was_stopped else "failed",
                    error=None if was_stopped else _bounded_error(exc),
                )
                self._refresh_machine_status()
                raise
            return {
                "accepted": True,
                "duplicate": False,
                "execution_owner": EXECUTION_OWNER,
                "job": _public_record(record),
            }

    def stop(
        self,
        *,
        emergency: bool = False,
        requested_job_id: str | None = None,
    ) -> dict[str, Any] | None:
        if type(emergency) is not bool:
            raise PiJobServiceError("emergency must be a JSON boolean")
        # Deliberately bypass _ordinary_lock: request_stop is the local immediate
        # stop primitive and must interrupt an ACK wait or a queued operation.
        # Do not put even a durable-store read in front of the controller stop.
        with self._state_lock:
            active_job_id = self._active_job_id
            if active_job_id is not None:
                self._stop_requested_for = active_job_id
        self.machine.request_stop(emergency=emergency)
        LOGGER.warning(
            "Pi-local STOP issued active_job=%s emergency=%s",
            "none" if active_job_id is None else active_job_id[:8],
            emergency,
        )
        active: Mapping[str, Any] | None = None
        if active_job_id is None:
            try:
                active = self.store.active()
            except Exception:
                active = None
            if active is not None:
                active_job_id = str(_record_value(active, "job_id", "")) or None
                with self._state_lock:
                    if active_job_id is not None:
                        self._stop_requested_for = active_job_id
        if active_job_id:
            try:
                active = self.store.update_state(
                    active_job_id,
                    "stopping",
                    phase="stopping",
                )
            except Exception:
                # Controller interruption has already happened. The watcher will
                # make another bounded persistence attempt at terminal state.
                try:
                    active = self.store.get(active_job_id)
                except Exception:
                    active = None
        self._refresh_machine_status()
        return _public_record(active)

    def get(self, job_id: str) -> dict[str, Any]:
        return _public_record(self.store.get(job_id)) or {}

    def active(self) -> dict[str, Any] | None:
        return _public_record(self._current_active_record())

    def result(self, job_id: str) -> dict[str, Any]:
        record = self.store.get(job_id)
        if _record_value(record, "state") not in _TERMINAL_STATES:
            raise PiJobServiceError("Job has no terminal result yet")
        return _public_record(record) or {}

    @staticmethod
    def _terminal_sort_key(record: Mapping[str, Any]) -> tuple[float, str]:
        raw_time = record.get(
            "finished_at",
            record.get("updated_at", record.get("created_at", 0.0)),
        )
        timestamp = (
            float(raw_time)
            if type(raw_time) in {int, float} and math.isfinite(float(raw_time))
            else 0.0
        )
        return timestamp, str(record.get("job_id", ""))

    def _refresh_latest_result_cache(self) -> None:
        eligible = [
            record
            for record in self.store.list_records()
            if record.get("state") in _TERMINAL_STATES
            and record.get("ownership_accepted") is True
        ]
        latest = max(eligible, key=self._terminal_sort_key, default=None)
        with self._state_lock:
            self._latest_terminal_record = None if latest is None else dict(latest)

    def latest_result(self) -> dict[str, Any] | None:
        """Return the newest bounded accepted terminal result from this Pi."""

        with self._state_lock:
            latest = self._latest_terminal_record
            return _public_record(latest)

    def delete(self, job_id: str) -> dict[str, Any]:
        with self._ordinary_lock:
            active = self._current_active_record()
            if active is not None and active.get("job_id") == job_id:
                raise PiJobServiceError("Cannot delete an active Pi-owned job")
            deleted = _public_record(self.store.delete(job_id)) or {}
            self._refresh_latest_result_cache()
            return deleted

    def _run_idle_machine_operation(self, operation: str, function: Any) -> Any:
        requested_generation = self.machine.operation_generation()
        with self._ordinary_lock:
            self._require_idle(operation)
            try:
                with self.machine.operation_scope(requested_generation):
                    return function()
            finally:
                self._refresh_machine_status()

    def connect(self) -> dict[str, Any]:
        return self._run_idle_machine_operation(
            "connect to the controller",
            self.machine.connect,
        )

    def replace_connection(self) -> dict[str, Any]:
        return self._run_idle_machine_operation(
            "replace the controller connection",
            self.machine.replace_connection,
        )

    def disconnect(self) -> dict[str, Any]:
        def disconnect() -> dict[str, Any]:
            self.machine.disconnect()
            return self.machine.status()

        return self._run_idle_machine_operation(
            "disconnect the controller",
            disconnect,
        )

    def prepare_photo_position(
        self,
        *,
        capture_home_position: bool = False,
    ) -> dict[str, Any]:
        return self._run_idle_machine_operation(
            "prepare the photography position",
            lambda: self.machine.prepare_photo_position(
                capture_home_position=capture_home_position
            ),
        )

    def prepare_job_start(self) -> dict[str, Any]:
        return self._run_idle_machine_operation(
            "prepare a job start",
            self.machine.prepare_job_start,
        )

    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> dict[str, Any]:
        return self._run_idle_machine_operation(
            "jog the controller",
            lambda: self.machine.jog(dx_mm, dy_mm, feed_mm_min),
        )

    def manual_command(
        self,
        line: str,
        timeout: float | None = None,
    ) -> list[str]:
        return self._run_idle_machine_operation(
            "send a manual controller command",
            lambda: self.machine.send_command(line, timeout=timeout),
        )

    def realtime_position(self, timeout: float = 1.5) -> dict[str, Any]:
        return self._run_idle_machine_operation(
            "sample controller position",
            lambda: self.machine.sample_realtime_position(timeout=timeout),
        )

    @contextmanager
    def temporary_stepper_hold(self) -> Iterator[None]:
        requested_generation = self.machine.operation_generation()
        with self._ordinary_lock:
            self._require_idle("hold the controller steppers")
            try:
                with self.machine.operation_scope(requested_generation):
                    with self.machine.temporary_stepper_hold():
                        yield
            finally:
                self._refresh_machine_status()

    @staticmethod
    def _safe_machine_status(raw: Mapping[str, Any]) -> dict[str, Any]:
        status = dict(raw)
        # MachineService's local diagnostics contain controller G-code and the
        # literal arm phrase. Neither belongs in remote monitoring responses.
        status.pop("log", None)
        status.pop("arm_phrase", None)
        return status

    def _set_machine_status(self, raw: Mapping[str, Any]) -> None:
        safe = self._safe_machine_status(raw)
        safe.update(
            {
                "boot_id": self.boot_id,
                "generation": self.machine.operation_generation(),
                "execution_owner": EXECUTION_OWNER,
            }
        )
        with self._cache_lock:
            self._cached_machine_status = safe

    def _refresh_machine_status(self) -> None:
        self._set_machine_status(self.machine.status())

    def status(self) -> dict[str, Any]:
        with self._cache_lock:
            return dict(self._cached_machine_status)

    def shutdown(self, *, stop_machine: bool = True) -> None:
        self._stop_event.set()
        if stop_machine:
            self.machine.request_stop(emergency=False)
        watcher = self._watcher
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=2.0)
        if stop_machine:
            try:
                self.machine.disconnect()
            except Exception:
                pass
        self._refresh_machine_status()
