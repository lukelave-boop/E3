"""Bounded authenticated primitives for the Pi-owned machine protocol.

``E3MACHINE/2`` is intentionally not wire-compatible with the retired raw-byte
``E3BRIDGE/1`` protocol. Authentication establishes direction-specific session
keys, and every subsequent JSON frame carries a monotonically counted HMAC.
The protocol does not provide encryption and belongs only on a trusted network.
"""

from __future__ import annotations

import base64
import binascii
import errno
import hashlib
import hmac
import json
import math
import re
import secrets
import select
import socket
import struct
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from ..errors import MachineError
from ..geometry.polygon import ConvexPolygon, normalize_convex_polygon
from ..storage import strict_json_loads

PROTOCOL_VERSION = "E3MACHINE/2"
LEGACY_PROTOCOL_VERSION = "E3BRIDGE/1"
CAPABILITY_PI_OWNED_JOBS = "pi-owned-jobs-v1"
CAPABILITY_PI_SECONDARY_MARLIN_FAN = "pi-secondary-marlin-fan-v1"
CAPABILITY_PI_EXECUTION_POLICY_DIAGNOSTICS = (
    "pi-execution-policy-diagnostics-v1"
)
CAPABILITY_PI_CONTROLLER_SESSION = "pi-controller-session-v1"
CAPABILITY_PI_STRUCTURED_ERRORS = "pi-structured-errors-v1"
CAPABILITY_PI_COHERENT_STATUS = "pi-coherent-status-v1"

ERROR_INVALID_REQUEST = "request.invalid"
ERROR_REQUEST_CONFLICT = "request.conflict"
ERROR_CONTROLLER_BUSY = "controller.busy"
ERROR_CONTROLLER_STALE_SESSION = "controller.stale_session"
ERROR_CONTROLLER_REJECTED = "controller.rejected"
ERROR_SAFETY_REJECTED = "safety.rejected"
ERROR_JOB_ACTIVE = "job.active"
ERROR_SERVICE_SHUTTING_DOWN = "service.shutting_down"
ERROR_INTERNAL = "service.internal"

ACTION_JOB_BEGIN = "job.begin"
ACTION_JOB_CHUNK = "job.chunk"
ACTION_JOB_FINALIZE = "job.finalize"
ACTION_JOB_START = "job.start"
ACTION_JOB_STATUS = "job.status"
ACTION_JOB_ACTIVE = "job.active"
ACTION_JOB_LATEST = "job.latest"
ACTION_JOB_RESULT = "job.result"
ACTION_JOB_STOP = "job.stop"
ACTION_JOB_DELETE = "job.delete"
JOB_ACTIONS = frozenset(
    {
        ACTION_JOB_BEGIN,
        ACTION_JOB_CHUNK,
        ACTION_JOB_FINALIZE,
        ACTION_JOB_START,
        ACTION_JOB_STATUS,
        ACTION_JOB_ACTIVE,
        ACTION_JOB_LATEST,
        ACTION_JOB_RESULT,
        ACTION_JOB_STOP,
        ACTION_JOB_DELETE,
    }
)

MAX_FRAME_PAYLOAD_BYTES = 128 * 1024
MAX_UPLOAD_CHUNK_BYTES = 64 * 1024
MAX_JOB_BYTES = 64 * 1024 * 1024
MAX_JOB_NAME_CHARACTERS = 160
MAX_GUARDED_POLYGON_POINTS = 64
MIN_TOKEN_LENGTH = 24

_MAX_AUTH_LINE_BYTES = 1024
_NONCE_BYTES = 32
_MAC_BYTES = hashlib.sha256().digest_size
_FRAME_PREFIX = struct.Struct("!IQ")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_CLIENT_AUTH_DOMAIN = b"E3MACHINE/2 client-auth\0"
_SERVER_AUTH_DOMAIN = b"E3MACHINE/2 server-auth\0"
_SESSION_DOMAIN = b"E3MACHINE/2 session\0"
_CLIENT_FRAME_DOMAIN = b"E3MACHINE/2 client-frame\0"
_SERVER_FRAME_DOMAIN = b"E3MACHINE/2 server-frame\0"
_CONNECT_IN_PROGRESS_ERRORS = frozenset(
    {
        errno.EINPROGRESS,
        errno.EWOULDBLOCK,
        errno.EALREADY,
        errno.EINTR,
        getattr(errno, "WSAEINPROGRESS", 10036),
        getattr(errno, "WSAEWOULDBLOCK", 10035),
        getattr(errno, "WSAEALREADY", 10037),
    }
)


class PiJobProtocolError(MachineError):
    """A bounded E3MACHINE/2 handshake, frame, or field was invalid."""


def _token_bytes(token: str) -> bytes:
    if type(token) is not str or len(token) < MIN_TOKEN_LENGTH:
        raise PiJobProtocolError(
            f"E3 machine token must be a string of at least {MIN_TOKEN_LENGTH} characters"
        )
    return token.encode("utf-8")


def validate_job_id(value: object) -> str:
    """Return one canonical lowercase UUID safe for deriving store filenames."""

    if type(value) is not str:
        raise PiJobProtocolError("job_id must be a canonical UUID string")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise PiJobProtocolError("job_id must be a canonical UUID string") from exc
    canonical = str(parsed)
    if value != canonical:
        raise PiJobProtocolError("job_id must use canonical lowercase UUID syntax")
    return canonical


def validate_request_id(value: object) -> str:
    return validate_job_id(value)


def validate_client_id(value: object) -> str:
    """Return one canonical client UUID used only for bounded correlation."""

    return validate_job_id(value)


def validate_boot_id(value: object, *, label: str = "expected_boot_id") -> str:
    """Return one canonical Pi process boot UUID used for request CAS."""

    if type(value) is not str:
        raise PiJobProtocolError(f"{label} must be a canonical UUID string")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise PiJobProtocolError(
            f"{label} must be a canonical UUID string"
        ) from exc
    canonical = str(parsed)
    if value != canonical:
        raise PiJobProtocolError(
            f"{label} must use canonical lowercase UUID syntax"
        )
    return canonical


def validate_session_generation(
    value: object,
    *,
    label: str = "expected_session_generation",
) -> int:
    """Validate a controller-session compare-and-swap generation."""

    if type(value) is not int or value < 0:
        raise PiJobProtocolError(f"{label} must be a non-negative integer")
    return value


def validate_sha256(value: object, *, label: str = "sha256") -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise PiJobProtocolError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def validate_job_size(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_JOB_BYTES:
        raise PiJobProtocolError(
            f"job size must be an integer from 1 through {MAX_JOB_BYTES} bytes"
        )
    return value


def validate_job_name(value: object) -> str:
    if type(value) is not str:
        raise PiJobProtocolError("job name must be a string")
    if not value or value != value.strip():
        raise PiJobProtocolError("job name must be non-empty without outer whitespace")
    if len(value) > MAX_JOB_NAME_CHARACTERS:
        raise PiJobProtocolError(
            f"job name must not exceed {MAX_JOB_NAME_CHARACTERS} characters"
        )
    if _CONTROL_CHARACTER_PATTERN.search(value):
        raise PiJobProtocolError("job name must not contain control characters")
    if "/" in value or "\\" in value:
        raise PiJobProtocolError("job name must not contain path separators")
    return value


def validate_guarded_output_polygon(value: object) -> ConvexPolygon | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise PiJobProtocolError(
            "guarded_output_polygon_mm must be null or an ordered convex polygon"
        )
    if len(value) > MAX_GUARDED_POLYGON_POINTS:
        raise PiJobProtocolError(
            "guarded_output_polygon_mm exceeds the bounded point limit"
        )
    try:
        return normalize_convex_polygon(
            value,
            label="guarded_output_polygon_mm",
        )
    except (TypeError, ValueError) as exc:
        raise PiJobProtocolError(str(exc)) from exc


def validate_upload_offset(value: object, *, maximum: int = MAX_JOB_BYTES) -> int:
    if type(maximum) is not int or maximum < 0:
        raise ValueError("maximum upload offset must be a non-negative integer")
    if type(value) is not int or not 0 <= value <= maximum:
        raise PiJobProtocolError(
            f"upload offset must be an integer from 0 through {maximum}"
        )
    return value


def encode_upload_chunk(data: bytes) -> str:
    if type(data) is not bytes or not 1 <= len(data) <= MAX_UPLOAD_CHUNK_BYTES:
        raise PiJobProtocolError(
            f"upload chunk must contain 1 through {MAX_UPLOAD_CHUNK_BYTES} bytes"
        )
    return base64.b64encode(data).decode("ascii")


def decode_upload_chunk(value: object) -> bytes:
    if type(value) is not str:
        raise PiJobProtocolError("upload chunk must be base64 text")
    maximum_encoded = 4 * math.ceil(MAX_UPLOAD_CHUNK_BYTES / 3)
    if not value or len(value) > maximum_encoded:
        raise PiJobProtocolError(
            f"decoded upload chunk must contain 1 through {MAX_UPLOAD_CHUNK_BYTES} bytes"
        )
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PiJobProtocolError("upload chunk is not canonical base64") from exc
    if not 1 <= len(decoded) <= MAX_UPLOAD_CHUNK_BYTES:
        raise PiJobProtocolError(
            f"decoded upload chunk must contain 1 through {MAX_UPLOAD_CHUNK_BYTES} bytes"
        )
    if base64.b64encode(decoded).decode("ascii") != value:
        raise PiJobProtocolError("upload chunk is not canonical base64")
    return decoded


def _read_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        try:
            chunk = sock.recv(length - len(data))
        except OSError as exc:
            raise PiJobProtocolError(f"E3 machine connection read failed: {exc}") from exc
        if not chunk:
            raise PiJobProtocolError("E3 machine connection closed unexpectedly")
        data.extend(chunk)
    return bytes(data)


def _read_ascii_line(sock: socket.socket) -> str:
    data = bytearray()
    while True:
        try:
            chunk = sock.recv(1)
        except OSError as exc:
            raise PiJobProtocolError(
                f"E3 machine authentication read failed: {exc}"
            ) from exc
        if not chunk:
            raise PiJobProtocolError(
                "E3 machine connection closed during authentication"
            )
        if chunk == b"\r":
            try:
                following = sock.recv(1)
            except OSError as exc:
                raise PiJobProtocolError(
                    f"E3 machine authentication read failed: {exc}"
                ) from exc
            if following != b"\n":
                raise PiJobProtocolError(
                    "E3 machine authentication line has an invalid line ending"
                )
            chunk = following
        if chunk == b"\n":
            if not data:
                continue
            try:
                return data.decode("ascii", errors="strict")
            except UnicodeError as exc:
                raise PiJobProtocolError(
                    "E3 machine authentication line was not ASCII"
                ) from exc
        data.extend(chunk)
        if len(data) > _MAX_AUTH_LINE_BYTES:
            raise PiJobProtocolError("E3 machine authentication line is too long")


def _send_ascii_line(sock: socket.socket, line: str) -> None:
    try:
        sock.sendall(line.rstrip("\r\n").encode("ascii", errors="strict") + b"\n")
    except (OSError, UnicodeError) as exc:
        raise PiJobProtocolError(
            f"E3 machine authentication write failed: {exc}"
        ) from exc


def _legacy_incompatibility() -> PiJobProtocolError:
    return PiJobProtocolError(
        "The endpoint speaks legacy E3BRIDGE/1 raw serial, which is incompatible "
        "with the Pi-owned E3MACHINE/2 job service; update/start the combined Pi node"
    )


def _parse_nonce(value: str, *, label: str) -> bytes:
    try:
        nonce = bytes.fromhex(value)
    except ValueError as exc:
        raise PiJobProtocolError(f"E3 machine {label} is invalid") from exc
    if len(nonce) != _NONCE_BYTES:
        raise PiJobProtocolError(f"E3 machine {label} is invalid")
    return nonce


def _auth_digest(key: bytes, domain: bytes, *parts: bytes) -> str:
    return hmac.new(key, domain + b"".join(parts), hashlib.sha256).hexdigest()


def _session_key(token: bytes, server_nonce: bytes, client_nonce: bytes) -> bytes:
    return hmac.new(
        token,
        _SESSION_DOMAIN + server_nonce + client_nonce,
        hashlib.sha256,
    ).digest()


class AuthenticatedChannel:
    """One authenticated, counted E3MACHINE/2 JSON channel."""

    def __init__(
        self,
        sock: socket.socket,
        *,
        send_key: bytes,
        receive_key: bytes,
        send_domain: bytes,
        receive_domain: bytes,
    ) -> None:
        self.sock = sock
        self._send_key = send_key
        self._receive_key = receive_key
        self._send_domain = send_domain
        self._receive_domain = receive_domain
        self._send_counter = 0
        self._receive_counter = 0
        self._send_lock = threading.Lock()
        self._receive_lock = threading.Lock()

    def send_json(self, payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            raise PiJobProtocolError("E3 machine frame payload must be a JSON object")
        try:
            encoded = json.dumps(
                dict(payload),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise PiJobProtocolError(
                f"E3 machine frame could not be serialized: {exc}"
            ) from exc
        if not 1 <= len(encoded) <= MAX_FRAME_PAYLOAD_BYTES:
            raise PiJobProtocolError(
                f"E3 machine frame must contain 1 through {MAX_FRAME_PAYLOAD_BYTES} bytes"
            )
        with self._send_lock:
            counter = self._send_counter
            prefix = _FRAME_PREFIX.pack(len(encoded), counter)
            signature = hmac.new(
                self._send_key,
                self._send_domain + prefix + encoded,
                hashlib.sha256,
            ).digest()
            try:
                self.sock.sendall(prefix + encoded + signature)
            except OSError as exc:
                raise PiJobProtocolError(
                    f"E3 machine frame write failed: {exc}"
                ) from exc
            self._send_counter += 1

    def receive_json(self) -> dict[str, Any]:
        with self._receive_lock:
            prefix = _read_exact(self.sock, _FRAME_PREFIX.size)
            payload_length, counter = _FRAME_PREFIX.unpack(prefix)
            if not 1 <= payload_length <= MAX_FRAME_PAYLOAD_BYTES:
                raise PiJobProtocolError("E3 machine frame payload length is invalid")
            encoded = _read_exact(self.sock, payload_length)
            received_signature = _read_exact(self.sock, _MAC_BYTES)
            expected_signature = hmac.new(
                self._receive_key,
                self._receive_domain + prefix + encoded,
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(received_signature, expected_signature):
                raise PiJobProtocolError("E3 machine frame authentication failed")
            if counter != self._receive_counter:
                raise PiJobProtocolError(
                    "E3 machine frame counter was replayed or out of sequence"
                )
            self._receive_counter += 1
        try:
            decoded = strict_json_loads(encoded.decode("utf-8", errors="strict"))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise PiJobProtocolError("E3 machine frame is not strict UTF-8 JSON") from exc
        if not isinstance(decoded, dict):
            raise PiJobProtocolError("E3 machine frame payload must be a JSON object")
        return decoded


def authenticate_client(sock: socket.socket, token: str) -> AuthenticatedChannel:
    """Authenticate a client and return its counted request/response channel."""

    key = _token_bytes(token)
    first = _read_ascii_line(sock).split()
    if first and first[0] == LEGACY_PROTOCOL_VERSION:
        raise _legacy_incompatibility()
    if len(first) != 3 or first[:2] != [PROTOCOL_VERSION, "CHALLENGE"]:
        advertised = first[0] if first else "empty response"
        raise PiJobProtocolError(
            f"Remote endpoint protocol {advertised!r} is incompatible with {PROTOCOL_VERSION}"
        )
    server_nonce = _parse_nonce(first[2], label="server challenge")
    client_nonce = secrets.token_bytes(_NONCE_BYTES)
    client_digest = _auth_digest(
        key,
        _CLIENT_AUTH_DOMAIN,
        server_nonce,
        client_nonce,
    )
    _send_ascii_line(
        sock,
        f"{PROTOCOL_VERSION} AUTH {client_nonce.hex()} {client_digest}",
    )
    result = _read_ascii_line(sock).split()
    if result and result[0] == LEGACY_PROTOCOL_VERSION:
        raise _legacy_incompatibility()
    if len(result) >= 2 and result[:2] == [PROTOCOL_VERSION, "ERROR"]:
        reason = " ".join(result[2:]) or "authentication_failed"
        raise PiJobProtocolError(f"E3 machine authentication failed: {reason}")
    if len(result) != 3 or result[:2] != [PROTOCOL_VERSION, "READY"]:
        raise PiJobProtocolError("E3 machine server did not complete authentication")
    expected_server_digest = _auth_digest(
        key,
        _SERVER_AUTH_DOMAIN,
        server_nonce,
        client_nonce,
    )
    if not hmac.compare_digest(result[2], expected_server_digest):
        raise PiJobProtocolError("E3 machine server authentication failed")
    session = _session_key(key, server_nonce, client_nonce)
    return AuthenticatedChannel(
        sock,
        send_key=hmac.new(session, _CLIENT_FRAME_DOMAIN, hashlib.sha256).digest(),
        receive_key=hmac.new(session, _SERVER_FRAME_DOMAIN, hashlib.sha256).digest(),
        send_domain=_CLIENT_FRAME_DOMAIN,
        receive_domain=_SERVER_FRAME_DOMAIN,
    )


def authenticate_server(sock: socket.socket, token: str) -> AuthenticatedChannel:
    """Authenticate a client and return the server side of its JSON channel."""

    key = _token_bytes(token)
    server_nonce = secrets.token_bytes(_NONCE_BYTES)
    _send_ascii_line(sock, f"{PROTOCOL_VERSION} CHALLENGE {server_nonce.hex()}")
    response = _read_ascii_line(sock).split()
    if response and response[0] == LEGACY_PROTOCOL_VERSION:
        raise _legacy_incompatibility()
    if len(response) != 4 or response[:2] != [PROTOCOL_VERSION, "AUTH"]:
        _send_ascii_line(sock, f"{PROTOCOL_VERSION} ERROR authentication_failed")
        raise PiJobProtocolError(
            f"Client protocol is incompatible with {PROTOCOL_VERSION}"
        )
    client_nonce = _parse_nonce(response[2], label="client nonce")
    expected_client_digest = _auth_digest(
        key,
        _CLIENT_AUTH_DOMAIN,
        server_nonce,
        client_nonce,
    )
    if not hmac.compare_digest(response[3], expected_client_digest):
        _send_ascii_line(sock, f"{PROTOCOL_VERSION} ERROR authentication_failed")
        raise PiJobProtocolError("E3 machine client authentication failed")
    server_digest = _auth_digest(
        key,
        _SERVER_AUTH_DOMAIN,
        server_nonce,
        client_nonce,
    )
    _send_ascii_line(sock, f"{PROTOCOL_VERSION} READY {server_digest}")
    session = _session_key(key, server_nonce, client_nonce)
    return AuthenticatedChannel(
        sock,
        send_key=hmac.new(session, _SERVER_FRAME_DOMAIN, hashlib.sha256).digest(),
        receive_key=hmac.new(session, _CLIENT_FRAME_DOMAIN, hashlib.sha256).digest(),
        send_domain=_SERVER_FRAME_DOMAIN,
        receive_domain=_CLIENT_FRAME_DOMAIN,
    )


def _remaining_deadline_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise PiJobProtocolError("E3 machine request deadline expired")
    return remaining


class _DeadlineSocket:
    """Apply one absolute deadline before every protocol socket operation."""

    def __init__(self, sock: socket.socket, deadline: float) -> None:
        self._sock = sock
        self._deadline = deadline

    def recv(self, length: int) -> bytes:
        self._sock.settimeout(_remaining_deadline_seconds(self._deadline))
        return self._sock.recv(length)

    def sendall(self, payload: bytes) -> None:
        self._sock.settimeout(_remaining_deadline_seconds(self._deadline))
        self._sock.sendall(payload)


def _resolve_until_deadline(
    host: str,
    port: int,
    deadline: float,
) -> tuple[tuple[Any, ...], ...]:
    """Resolve without allowing a platform DNS call to hold shutdown."""

    resolved: list[tuple[Any, ...]] = []
    failures: list[BaseException] = []
    finished = threading.Event()

    def resolve() -> None:
        try:
            resolved.extend(
                socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            )
        except BaseException as exc:  # pragma: no cover - platform resolver failures
            failures.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(
        target=resolve,
        name="e3-machine-shutdown-resolver",
        daemon=True,
    )
    thread.start()
    if not finished.wait(_remaining_deadline_seconds(deadline)):
        raise PiJobProtocolError("E3 machine address resolution deadline expired")
    if failures:
        failure = failures[0]
        raise PiJobProtocolError(
            f"Could not resolve E3 machine at {host}:{port}: {failure}"
        ) from failure
    if not resolved:
        raise PiJobProtocolError(f"Could not resolve E3 machine at {host}:{port}")
    return tuple(resolved)


def _connect_until_deadline(
    addresses: Sequence[tuple[Any, ...]],
    deadline: float,
) -> socket.socket:
    last_error: OSError | None = None
    for family, sock_type, protocol, _canonical_name, address in addresses:
        _remaining_deadline_seconds(deadline)
        sock = socket.socket(family, sock_type, protocol)
        try:
            sock.setblocking(False)
            result = sock.connect_ex(address)
            if result not in {0, errno.EISCONN}:
                if result not in _CONNECT_IN_PROGRESS_ERRORS:
                    raise OSError(result, f"socket connect failed ({result})")
                _readable, writable, exceptional = select.select(
                    [],
                    [sock],
                    [sock],
                    _remaining_deadline_seconds(deadline),
                )
                if not writable and not exceptional:
                    raise TimeoutError("E3 machine connection deadline expired")
                error_code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error_code:
                    raise OSError(
                        error_code,
                        f"socket connect failed ({error_code})",
                    )
            sock.setblocking(True)
            return sock
        except OSError as exc:
            last_error = exc
            try:
                sock.close()
            except OSError:
                pass
    if last_error is not None:
        raise last_error
    raise OSError("Could not connect to the E3 machine")


def request_response(
    host: str,
    port: int,
    token: str,
    request: Mapping[str, Any],
    *,
    timeout: float = 5.0,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Perform one authenticated request/response exchange."""

    if type(host) is not str or not host:
        raise PiJobProtocolError("E3 machine host must be a non-empty string")
    if type(port) is not int or not 1 <= port <= 65535:
        raise PiJobProtocolError(
            "E3 machine port must be an integer from 1 through 65535"
        )
    if (
        type(timeout) not in {int, float}
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise PiJobProtocolError("E3 machine request timeout must be positive and finite")
    if deadline is not None and (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(float(deadline))
    ):
        raise PiJobProtocolError("E3 machine request deadline must be finite")
    try:
        if deadline is None:
            sock = socket.create_connection((host, port), timeout=float(timeout))
            bounded_sock: socket.socket | _DeadlineSocket = sock
        else:
            effective_deadline = min(
                float(deadline),
                time.monotonic() + float(timeout),
            )
            addresses = _resolve_until_deadline(host, port, effective_deadline)
            sock = _connect_until_deadline(addresses, effective_deadline)
            bounded_sock = _DeadlineSocket(sock, effective_deadline)
        with sock:
            if deadline is None:
                sock.settimeout(float(timeout))
            channel = authenticate_client(bounded_sock, token)  # type: ignore[arg-type]
            channel.send_json(request)
            return channel.receive_json()
    except PiJobProtocolError:
        raise
    except OSError as exc:
        raise PiJobProtocolError(
            f"Could not communicate with E3 machine at {host}:{port}: {exc}"
        ) from exc


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
    "AuthenticatedChannel",
    "CAPABILITY_PI_COHERENT_STATUS",
    "CAPABILITY_PI_CONTROLLER_SESSION",
    "CAPABILITY_PI_EXECUTION_POLICY_DIAGNOSTICS",
    "CAPABILITY_PI_OWNED_JOBS",
    "CAPABILITY_PI_SECONDARY_MARLIN_FAN",
    "CAPABILITY_PI_STRUCTURED_ERRORS",
    "ERROR_CONTROLLER_BUSY",
    "ERROR_CONTROLLER_REJECTED",
    "ERROR_CONTROLLER_STALE_SESSION",
    "ERROR_INTERNAL",
    "ERROR_INVALID_REQUEST",
    "ERROR_JOB_ACTIVE",
    "ERROR_REQUEST_CONFLICT",
    "ERROR_SAFETY_REJECTED",
    "ERROR_SERVICE_SHUTTING_DOWN",
    "JOB_ACTIONS",
    "LEGACY_PROTOCOL_VERSION",
    "MAX_FRAME_PAYLOAD_BYTES",
    "MAX_GUARDED_POLYGON_POINTS",
    "MAX_JOB_BYTES",
    "MAX_JOB_NAME_CHARACTERS",
    "MAX_UPLOAD_CHUNK_BYTES",
    "MIN_TOKEN_LENGTH",
    "PROTOCOL_VERSION",
    "PiJobProtocolError",
    "authenticate_client",
    "authenticate_server",
    "decode_upload_chunk",
    "encode_upload_chunk",
    "request_response",
    "validate_boot_id",
    "validate_client_id",
    "validate_guarded_output_polygon",
    "validate_job_id",
    "validate_job_name",
    "validate_job_size",
    "validate_request_id",
    "validate_session_generation",
    "validate_sha256",
    "validate_upload_offset",
]
