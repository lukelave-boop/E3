from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import socket
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .pi_job_protocol import (
    ACTION_JOB_ACTIVE,
    ACTION_JOB_BEGIN,
    ACTION_JOB_CHUNK,
    ACTION_JOB_DELETE,
    ACTION_JOB_FINALIZE,
    ACTION_JOB_LATEST,
    ACTION_JOB_RESULT,
    ACTION_JOB_START,
    ACTION_JOB_STATUS,
    ACTION_JOB_STOP,
    CAPABILITY_PI_OWNED_JOBS,
    CAPABILITY_PI_SECONDARY_MARLIN_FAN,
    PROTOCOL_VERSION,
    AuthenticatedChannel,
    PiJobProtocolError,
    authenticate_server,
    decode_upload_chunk,
    validate_guarded_output_polygon,
    validate_job_id,
    validate_job_name,
    validate_job_size,
    validate_request_id,
    validate_sha256,
    validate_upload_offset,
)
from .pi_job_service import EXECUTION_OWNER, PiJobService

LOGGER = logging.getLogger(__name__)

ACTION_SERVICE_CAPABILITIES = "service.capabilities"
ACTION_MACHINE_STATUS = "machine.status"
ACTION_MACHINE_CONNECT = "machine.connect"
ACTION_MACHINE_REPLACE_CONNECTION = "machine.replace_connection"
ACTION_MACHINE_DISCONNECT = "machine.disconnect"
ACTION_MACHINE_PREPARE_PHOTO_POSITION = "machine.prepare_photo_position"
ACTION_MACHINE_PREPARE_JOB_START = "machine.prepare_job_start"
ACTION_MACHINE_JOG = "machine.jog"
ACTION_MACHINE_COMMAND = "machine.command"
ACTION_MACHINE_REALTIME_POSITION = "machine.realtime_position"
ACTION_MACHINE_STEPPER_HOLD = "machine.stepper_hold"
ACTION_MACHINE_STEPPER_HOLD_RELEASE = "machine.stepper_hold.release"

MACHINE_ACTIONS = frozenset(
    {
        ACTION_MACHINE_STATUS,
        ACTION_MACHINE_CONNECT,
        ACTION_MACHINE_REPLACE_CONNECTION,
        ACTION_MACHINE_DISCONNECT,
        ACTION_MACHINE_PREPARE_PHOTO_POSITION,
        ACTION_MACHINE_PREPARE_JOB_START,
        ACTION_MACHINE_JOG,
        ACTION_MACHINE_COMMAND,
        ACTION_MACHINE_REALTIME_POSITION,
        ACTION_MACHINE_STEPPER_HOLD,
        ACTION_MACHINE_STEPPER_HOLD_RELEASE,
    }
)

SERVER_CAPABILITIES = (
    CAPABILITY_PI_OWNED_JOBS,
    CAPABILITY_PI_SECONDARY_MARLIN_FAN,
    "same-channel-stepper-hold-v1",
)

# This mapping is the discoverable server contract used by the desktop client.
# `action` and canonical UUID `request_id` are required on every request in
# addition to the per-action fields below.
SERVER_ACTION_SCHEMAS: dict[str, dict[str, tuple[str, ...] | str]] = {
    ACTION_SERVICE_CAPABILITIES: {
        "required": (),
        "optional": (),
        "response": ("protocol_version", "capabilities", "actions"),
    },
    ACTION_MACHINE_STATUS: {
        "required": (),
        "optional": (),
        "response": ("status",),
    },
    ACTION_MACHINE_CONNECT: {
        "required": (),
        "optional": (),
        "response": ("status",),
    },
    ACTION_MACHINE_REPLACE_CONNECTION: {
        "required": (),
        "optional": (),
        "response": ("status",),
    },
    ACTION_MACHINE_DISCONNECT: {
        "required": (),
        "optional": (),
        "response": ("status",),
    },
    ACTION_MACHINE_PREPARE_PHOTO_POSITION: {
        "required": (),
        "optional": ("capture_home_position",),
        "response": ("result",),
    },
    ACTION_MACHINE_PREPARE_JOB_START: {
        "required": (),
        "optional": (),
        "response": ("result",),
    },
    ACTION_MACHINE_JOG: {
        "required": ("dx_mm", "dy_mm", "feed_mm_min"),
        "optional": (),
        "response": ("result",),
    },
    ACTION_MACHINE_COMMAND: {
        "required": ("line",),
        "optional": ("timeout",),
        "response": ("responses",),
    },
    ACTION_MACHINE_REALTIME_POSITION: {
        "required": (),
        "optional": ("timeout",),
        "response": ("position",),
    },
    ACTION_MACHINE_STEPPER_HOLD: {
        "required": (),
        "optional": (),
        "response": ("state", "lease_id"),
        "replay": "forbidden",
    },
    ACTION_MACHINE_STEPPER_HOLD_RELEASE: {
        "required": ("lease_id",),
        "optional": (),
        "response": ("state", "lease_id"),
        "transport": "same authenticated channel as machine.stepper_hold",
    },
    ACTION_JOB_BEGIN: {
        "required": ("job_id", "name", "expected_size", "expected_sha256"),
        "optional": ("guarded_output_polygon_mm",),
        "response": ("job",),
    },
    ACTION_JOB_CHUNK: {
        "required": ("job_id", "offset", "data_b64"),
        "optional": (),
        "response": ("job",),
    },
    ACTION_JOB_FINALIZE: {
        "required": (
            "job_id",
            "program_digest",
            "requires_laser_authorization",
            "requires_motion",
            "guarded_output_polygon_mm",
            "execution_policy_digest",
        ),
        "optional": (),
        "response": ("job", "ready", "verification_seconds"),
    },
    ACTION_JOB_START: {
        "required": (
            "job_id",
            "program_digest",
            "requires_laser_authorization",
            "requires_motion",
            "guarded_output_polygon_mm",
            "execution_policy_digest",
        ),
        "optional": ("authorization_phrase",),
        "response": (
            "accepted",
            "duplicate",
            "execution_owner",
            "job",
            "start_latency_seconds",
        ),
    },
    ACTION_JOB_STATUS: {
        "required": ("job_id",),
        "optional": (),
        "response": ("job",),
    },
    ACTION_JOB_ACTIVE: {
        "required": (),
        "optional": (),
        "response": ("job",),
    },
    ACTION_JOB_RESULT: {
        "required": ("job_id",),
        "optional": (),
        "response": ("job",),
    },
    ACTION_JOB_LATEST: {
        "required": (),
        "optional": (),
        "response": ("job",),
    },
    ACTION_JOB_STOP: {
        "required": (),
        "optional": ("job_id", "emergency"),
        "response": ("job",),
        "priority": "bypasses ordinary operation serialization",
    },
    ACTION_JOB_DELETE: {
        "required": ("job_id",),
        "optional": (),
        "response": ("job",),
    },
}

_MONITOR_ACTIONS = frozenset(
    {
        ACTION_MACHINE_STATUS,
        ACTION_JOB_STATUS,
        ACTION_JOB_ACTIVE,
        ACTION_JOB_RESULT,
        ACTION_JOB_LATEST,
    }
)
_MAX_CLIENTS = 16
_MAX_REPLAY_ENTRIES = 256
_HANDSHAKE_TIMEOUT_SECONDS = 5.0
_REQUEST_TIMEOUT_SECONDS = 130.0
_STEPPER_HOLD_LEASE_SECONDS = 120.0
_MAX_ERROR_CHARACTERS = 512


@dataclass(slots=True)
class _ReplayEntry:
    fingerprint: str
    ready: threading.Event
    response: dict[str, Any] | None = None


def _bounded_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = type(exc).__name__
    return text[:_MAX_ERROR_CHARACTERS]


def _request_fingerprint(request: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            dict(request),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise PiJobProtocolError("Request is not canonical strict JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _number(value: object, label: str, *, positive: bool = False) -> float:
    if type(value) not in {int, float}:
        raise PiJobProtocolError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0.0):
        raise PiJobProtocolError(f"{label} must be a finite number")
    return result


def _exact_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise PiJobProtocolError(f"{label} must be a JSON boolean")
    return value


class PiMachineServer:
    """Authenticated concurrent E3MACHINE/2 server for one PiJobService."""

    def __init__(
        self,
        service: PiJobService,
        *,
        host: str,
        port: int,
        token: str,
    ) -> None:
        if not isinstance(service, PiJobService):
            raise TypeError("service must be a PiJobService")
        if type(host) is not str or not host:
            raise ValueError("host must be a non-empty string")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be an integer from 0 through 65535")
        self.service = service
        self.host = host
        self.port = port
        self.token = token
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._bound_port: int | None = None
        self._slots = threading.BoundedSemaphore(_MAX_CLIENTS)
        self._threads_lock = threading.Lock()
        self._threads: set[threading.Thread] = set()
        self._connections: set[socket.socket] = set()
        self._monitor_lock = threading.Lock()
        self._monitoring_requests_in_flight = 0
        self._replay_lock = threading.Lock()
        self._replay: OrderedDict[str, _ReplayEntry] = OrderedDict()
        self._lease_ids: OrderedDict[str, None] = OrderedDict()

    @property
    def bound_port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("Pi machine server is not listening")
        return self._bound_port

    def stop(self) -> None:
        """Stop accepting network clients without stopping a Pi-owned job."""

        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._threads_lock:
            connections = tuple(self._connections)
        for conn in connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def _monitor_count(self) -> int:
        with self._monitor_lock:
            return self._monitoring_requests_in_flight

    def _status(self) -> dict[str, Any]:
        status = self.service.status()
        status["monitoring_requests_in_flight"] = self._monitor_count()
        return status

    @staticmethod
    def _request_id_for_error(request: Mapping[str, Any]) -> str | None:
        raw = request.get("request_id")
        if type(raw) is str and len(raw) <= 64:
            return raw
        return None

    @staticmethod
    def _error_response(
        request_id: str | None,
        exc: BaseException,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "request_id": request_id,
            "error": _bounded_error(exc),
        }

    @staticmethod
    def _validate_schema(request: Mapping[str, Any], action: str) -> None:
        schema = SERVER_ACTION_SCHEMAS.get(action)
        if schema is None:
            raise PiJobProtocolError(f"Unsupported E3 machine action: {action}")
        required = set(schema["required"])
        optional = set(schema["optional"])
        allowed = {"action", "request_id"} | required | optional
        missing = required - set(request)
        if missing:
            raise PiJobProtocolError(
                f"{action} is missing required fields: {', '.join(sorted(missing))}"
            )
        unexpected = set(request) - allowed
        if unexpected:
            raise PiJobProtocolError(
                f"{action} contains unsupported fields: {', '.join(sorted(unexpected))}"
            )

    @staticmethod
    def _binding(request: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "program_digest": validate_sha256(
                request.get("program_digest"),
                label="program_digest",
            ),
            "requires_laser_authorization": _exact_bool(
                request.get("requires_laser_authorization"),
                "requires_laser_authorization",
            ),
            "requires_motion": _exact_bool(
                request.get("requires_motion"),
                "requires_motion",
            ),
            "guarded_output_polygon_mm": validate_guarded_output_polygon(
                request.get("guarded_output_polygon_mm")
            ),
            "policy_digest": validate_sha256(
                request.get("execution_policy_digest"),
                label="execution_policy_digest",
            ),
        }

    def _dispatch(self, request: Mapping[str, Any], action: str) -> dict[str, Any]:
        self._validate_schema(request, action)
        if action == ACTION_SERVICE_CAPABILITIES:
            return {
                "protocol_version": PROTOCOL_VERSION,
                "capabilities": list(SERVER_CAPABILITIES),
                "actions": copy.deepcopy(SERVER_ACTION_SCHEMAS),
                "boot_id": self.service.boot_id,
                "execution_owner": EXECUTION_OWNER,
            }
        if action == ACTION_MACHINE_STATUS:
            return {"status": self._status()}
        if action == ACTION_MACHINE_CONNECT:
            self.service.connect()
            return {"status": self._status()}
        if action == ACTION_MACHINE_REPLACE_CONNECTION:
            self.service.replace_connection()
            return {"status": self._status()}
        if action == ACTION_MACHINE_DISCONNECT:
            self.service.disconnect()
            return {"status": self._status()}
        if action == ACTION_MACHINE_PREPARE_PHOTO_POSITION:
            capture_home = _exact_bool(
                request.get("capture_home_position", False),
                "capture_home_position",
            )
            return {
                "result": self.service.prepare_photo_position(
                    capture_home_position=capture_home
                )
            }
        if action == ACTION_MACHINE_PREPARE_JOB_START:
            return {"result": self.service.prepare_job_start()}
        if action == ACTION_MACHINE_JOG:
            return {
                "result": self.service.jog(
                    _number(request.get("dx_mm"), "dx_mm"),
                    _number(request.get("dy_mm"), "dy_mm"),
                    _number(
                        request.get("feed_mm_min"),
                        "feed_mm_min",
                        positive=True,
                    ),
                )
            }
        if action == ACTION_MACHINE_COMMAND:
            line = request.get("line")
            if type(line) is not str:
                raise PiJobProtocolError("line must be a string")
            timeout_raw = request.get("timeout")
            timeout = (
                None
                if timeout_raw is None
                else _number(timeout_raw, "timeout", positive=True)
            )
            return {"responses": self.service.manual_command(line, timeout)}
        if action == ACTION_MACHINE_REALTIME_POSITION:
            return {
                "position": self.service.realtime_position(
                    _number(
                        request.get("timeout", 1.5),
                        "timeout",
                        positive=True,
                    )
                )
            }
        if action == ACTION_JOB_BEGIN:
            job = self.service.begin_upload(
                validate_job_id(request.get("job_id")),
                validate_job_name(request.get("name")),
                validate_job_size(request.get("expected_size")),
                validate_sha256(
                    request.get("expected_sha256"),
                    label="expected_sha256",
                ),
                validate_guarded_output_polygon(
                    request.get("guarded_output_polygon_mm")
                ),
            )
            return {"job": job}
        if action == ACTION_JOB_CHUNK:
            job = self.service.append_upload_chunk(
                validate_job_id(request.get("job_id")),
                validate_upload_offset(request.get("offset")),
                decode_upload_chunk(request.get("data_b64")),
            )
            return {"job": job}
        if action == ACTION_JOB_FINALIZE:
            job = self.service.finalize_upload(
                validate_job_id(request.get("job_id")),
                **self._binding(request),
            )
            return {
                "job": job,
                "ready": True,
                "verification_seconds": job.get("verification_seconds"),
            }
        if action == ACTION_JOB_START:
            phrase = request.get("authorization_phrase")
            if phrase is not None and (type(phrase) is not str or len(phrase) > 128):
                raise PiJobProtocolError(
                    "authorization_phrase must be bounded text when provided"
                )
            response = self.service.start(
                validate_job_id(request.get("job_id")),
                authorization_phrase=phrase,
                **self._binding(request),
            )
            job = response.get("job")
            response["start_latency_seconds"] = (
                job.get("start_latency_seconds") if isinstance(job, dict) else None
            )
            return response
        if action == ACTION_JOB_STATUS:
            return {"job": self.service.get(validate_job_id(request.get("job_id")))}
        if action == ACTION_JOB_ACTIVE:
            active = self.service.active()
            if active is not None:
                LOGGER.debug(
                    "Authenticated monitor observed active Pi job %s",
                    str(active.get("job_id", "unknown"))[:8],
                )
            return {"job": active}
        if action == ACTION_JOB_RESULT:
            return {
                "job": self.service.result(validate_job_id(request.get("job_id")))
            }
        if action == ACTION_JOB_LATEST:
            return {"job": self.service.latest_result()}
        if action == ACTION_JOB_STOP:
            raw_job_id = request.get("job_id")
            requested_job_id = (
                None if raw_job_id is None else validate_job_id(raw_job_id)
            )
            emergency = _exact_bool(request.get("emergency", False), "emergency")
            return {
                "job": self.service.stop(
                    emergency=emergency,
                    requested_job_id=requested_job_id,
                )
            }
        if action == ACTION_JOB_DELETE:
            return {"job": self.service.delete(validate_job_id(request.get("job_id")))}
        if action in {ACTION_MACHINE_STEPPER_HOLD, ACTION_MACHINE_STEPPER_HOLD_RELEASE}:
            raise PiJobProtocolError(
                "Stepper-hold messages require their dedicated same-channel session"
            )
        raise PiJobProtocolError(f"Unsupported E3 machine action: {action}")

    def _dispatch_monitored(
        self,
        request: Mapping[str, Any],
        action: str,
    ) -> dict[str, Any]:
        monitoring = action in _MONITOR_ACTIONS
        if monitoring:
            with self._monitor_lock:
                self._monitoring_requests_in_flight += 1
        try:
            return self._dispatch(request, action)
        finally:
            if monitoring:
                with self._monitor_lock:
                    self._monitoring_requests_in_flight -= 1

    def _trim_replay_locked(self) -> None:
        while len(self._replay) > _MAX_REPLAY_ENTRIES:
            removable = next(
                (
                    request_id
                    for request_id, entry in self._replay.items()
                    if entry.ready.is_set()
                ),
                None,
            )
            if removable is None:
                break
            del self._replay[removable]

    def _replayable_response(self, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = validate_request_id(request.get("request_id"))
        action = request.get("action")
        if type(action) is not str:
            raise PiJobProtocolError("E3 machine request is missing an action")
        fingerprint = _request_fingerprint(request)
        with self._replay_lock:
            entry = self._replay.get(request_id)
            if entry is None:
                entry = _ReplayEntry(fingerprint, threading.Event())
                self._replay[request_id] = entry
                owner = True
            else:
                self._replay.move_to_end(request_id)
                owner = False
                if entry.fingerprint != fingerprint:
                    raise PiJobProtocolError(
                        "request_id was already used for a different request"
                    )
        if not owner:
            if not entry.ready.wait(_REQUEST_TIMEOUT_SECONDS):
                raise PiJobProtocolError("Original request is still in progress")
            assert entry.response is not None
            return copy.deepcopy(entry.response)
        try:
            try:
                body = self._dispatch_monitored(request, action)
                response = {"ok": True, "request_id": request_id, **body}
            except Exception as exc:
                response = self._error_response(request_id, exc)
            entry.response = copy.deepcopy(response)
            return response
        finally:
            entry.ready.set()
            with self._replay_lock:
                self._trim_replay_locked()

    def _claim_lease_request(self, request_id: str) -> None:
        with self._replay_lock:
            if request_id in self._lease_ids or request_id in self._replay:
                raise PiJobProtocolError(
                    "machine.stepper_hold requests cannot be replayed"
                )
            self._lease_ids[request_id] = None
            self._lease_ids.move_to_end(request_id)
            while len(self._lease_ids) > _MAX_REPLAY_ENTRIES:
                self._lease_ids.popitem(last=False)

    def _stepper_hold_session(
        self,
        conn: socket.socket,
        channel: AuthenticatedChannel,
        request: Mapping[str, Any],
    ) -> None:
        error_request_id = self._request_id_for_error(request)
        try:
            request_id = validate_request_id(request.get("request_id"))
            error_request_id = request_id
            self._validate_schema(request, ACTION_MACHINE_STEPPER_HOLD)
            self._claim_lease_request(request_id)
            lease_id = str(uuid.uuid4())
            release_request_id: str | None = None
            with self.service.temporary_stepper_hold():
                channel.send_json(
                    {
                        "ok": True,
                        "request_id": request_id,
                        "state": "held",
                        "lease_id": lease_id,
                    }
                )
                conn.settimeout(_STEPPER_HOLD_LEASE_SECONDS)
                release = channel.receive_json()
                error_request_id = self._request_id_for_error(release)
                release_request_id = validate_request_id(release.get("request_id"))
                error_request_id = release_request_id
                self._claim_lease_request(release_request_id)
                if release.get("action") != ACTION_MACHINE_STEPPER_HOLD_RELEASE:
                    raise PiJobProtocolError(
                        "Held channel requires machine.stepper_hold.release"
                    )
                self._validate_schema(release, ACTION_MACHINE_STEPPER_HOLD_RELEASE)
                if release.get("lease_id") != lease_id:
                    raise PiJobProtocolError("Stepper-hold lease_id does not match")
            channel.send_json(
                {
                    "ok": True,
                    "request_id": release_request_id,
                    "state": "released",
                    "lease_id": lease_id,
                }
            )
        except Exception as exc:
            try:
                channel.send_json(self._error_response(error_request_id, exc))
            except Exception:
                pass

    def _handle(self, conn: socket.socket, address: tuple[object, ...]) -> None:
        acquired = self._slots.acquire(blocking=False)
        authenticated = False
        current = threading.current_thread()
        with self._threads_lock:
            self._connections.add(conn)
        with conn:
            try:
                if not acquired:
                    return
                conn.settimeout(_HANDSHAKE_TIMEOUT_SECONDS)
                channel = authenticate_server(conn, self.token)
                authenticated = True
                conn.settimeout(_REQUEST_TIMEOUT_SECONDS)
                request = channel.receive_json()
                if request.get("action") == ACTION_MACHINE_STEPPER_HOLD:
                    self._stepper_hold_session(conn, channel, request)
                    return
                try:
                    response = self._replayable_response(request)
                except Exception as exc:
                    response = self._error_response(
                        self._request_id_for_error(request),
                        exc,
                    )
                channel.send_json(response)
            except Exception as exc:
                LOGGER.warning(
                    "E3 machine client session ended (%s)",
                    type(exc).__name__,
                )
            finally:
                if authenticated:
                    LOGGER.debug(
                        "Authenticated machine client detached; Pi execution continues independently"
                    )
                if acquired:
                    self._slots.release()
                with self._threads_lock:
                    self._threads.discard(current)
                    self._connections.discard(conn)

    def serve_forever(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            listener.listen(_MAX_CLIENTS)
            listener.settimeout(0.25)
            self._listener = listener
            self._bound_port = int(listener.getsockname()[1])
            while not self._stop.is_set():
                try:
                    conn, address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                worker = threading.Thread(
                    target=self._handle,
                    args=(conn, address),
                    name="e3-pi-machine-client",
                    daemon=True,
                )
                with self._threads_lock:
                    self._threads.add(worker)
                try:
                    worker.start()
                except Exception:
                    with self._threads_lock:
                        self._threads.discard(worker)
                    conn.close()
                    raise
            self._listener = None
        with self._threads_lock:
            workers = tuple(self._threads)
        for worker in workers:
            worker.join(timeout=1.0)


__all__ = [
    "ACTION_JOB_ACTIVE",
    "ACTION_JOB_BEGIN",
    "ACTION_JOB_CHUNK",
    "ACTION_JOB_DELETE",
    "ACTION_JOB_FINALIZE",
    "ACTION_JOB_LATEST",
    "ACTION_JOB_RESULT",
    "ACTION_JOB_START",
    "ACTION_JOB_STATUS",
    "ACTION_JOB_STOP",
    "ACTION_MACHINE_COMMAND",
    "ACTION_MACHINE_CONNECT",
    "ACTION_MACHINE_DISCONNECT",
    "ACTION_MACHINE_JOG",
    "ACTION_MACHINE_PREPARE_JOB_START",
    "ACTION_MACHINE_PREPARE_PHOTO_POSITION",
    "ACTION_MACHINE_REALTIME_POSITION",
    "ACTION_MACHINE_REPLACE_CONNECTION",
    "ACTION_MACHINE_STATUS",
    "ACTION_MACHINE_STEPPER_HOLD",
    "ACTION_MACHINE_STEPPER_HOLD_RELEASE",
    "ACTION_SERVICE_CAPABILITIES",
    "MACHINE_ACTIONS",
    "PiMachineServer",
    "SERVER_ACTION_SCHEMAS",
    "SERVER_CAPABILITIES",
]
