from __future__ import annotations

import hashlib
import hmac
import os
import select
import socket
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..errors import MachineError, TransientConnectionError
from .transport import InputSynchronizationEvidence

_BRIDGE_SCHEME = "e3bridge"
_BRIDGE_VERSION = "E3BRIDGE/1"
_DEFAULT_BRIDGE_PORT = 8765
_TOKEN_ENV = "E3_BRIDGE_TOKEN"
_MIN_TOKEN_LENGTH = 24
_HANDSHAKE_TIMEOUT_SECONDS = 5.0
_NETWORK_WRITE_TIMEOUT_SECONDS = 0.35
_MAX_HANDSHAKE_LINE_BYTES = 1024
_MAX_SERIAL_LINE_BYTES = 8192
_MAX_RECEIVE_BUFFER_BYTES = 16384


@dataclass(frozen=True, slots=True)
class BridgeTarget:
    host: str
    port: int


def is_bridge_uri(value: str) -> bool:
    return isinstance(value, str) and value.lower().startswith(f"{_BRIDGE_SCHEME}://")


def parse_bridge_uri(value: str) -> BridgeTarget:
    if not isinstance(value, str) or not value.strip():
        raise MachineError("E3 bridge address must be a non-empty string")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise MachineError(f"Invalid E3 bridge address: {exc}") from exc
    if parsed.scheme.lower() != _BRIDGE_SCHEME:
        raise MachineError("E3 bridge address must use e3bridge://")
    if parsed.username is not None or parsed.password is not None:
        raise MachineError("E3 bridge credentials must not be embedded in the address")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise MachineError("E3 bridge address may contain only a host and optional port")
    host = parsed.hostname
    if not host:
        raise MachineError("E3 bridge address must include a host")
    if port is None:
        port = _DEFAULT_BRIDGE_PORT
    if not 1 <= port <= 65535:
        raise MachineError("E3 bridge port must be between 1 and 65535")
    return BridgeTarget(host=host, port=port)


def bridge_token_from_environment() -> str:
    token = os.environ.get(_TOKEN_ENV, "")
    if len(token) < _MIN_TOKEN_LENGTH:
        raise MachineError(
            f"{_TOKEN_ENV} must be set to a secret of at least {_MIN_TOKEN_LENGTH} characters"
        )
    return token


def _read_handshake_line(sock: socket.socket, timeout: float) -> str:
    deadline = time.monotonic() + max(0.0, timeout)
    data = bytearray()
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        readable, _, _ = select.select([sock], [], [], max(0.0, remaining))
        if not readable:
            break
        chunk = sock.recv(1)
        if not chunk:
            raise MachineError("E3 bridge closed the connection during authentication")
        if chunk in {b"\n", b"\r"}:
            if data:
                return data.decode("ascii", errors="strict")
            continue
        data.extend(chunk)
        if len(data) > _MAX_HANDSHAKE_LINE_BYTES:
            raise MachineError("E3 bridge authentication line is too long")
    raise MachineError("Timed out waiting for E3 bridge authentication")


def _authenticate(sock: socket.socket, token: str, baudrate: int) -> None:
    challenge_line = _read_handshake_line(sock, _HANDSHAKE_TIMEOUT_SECONDS)
    parts = challenge_line.split()
    if len(parts) == 2 and parts == [_BRIDGE_VERSION, "BUSY"]:
        raise MachineError("E3 bridge is already in use by another controller client")
    if len(parts) != 3 or parts[0] != _BRIDGE_VERSION or parts[1] != "CHALLENGE":
        raise MachineError("Remote endpoint is not an E3 machine bridge")
    try:
        challenge = bytes.fromhex(parts[2])
    except ValueError as exc:
        raise MachineError("E3 bridge sent an invalid authentication challenge") from exc
    if len(challenge) != 32:
        raise MachineError("E3 bridge sent an invalid authentication challenge")
    digest = hmac.new(token.encode("utf-8"), challenge, hashlib.sha256).hexdigest()
    sock.sendall(f"{_BRIDGE_VERSION} AUTH {digest}\n".encode("ascii"))
    result = _read_handshake_line(sock, _HANDSHAKE_TIMEOUT_SECONDS)
    fields = result.split()
    if len(fields) >= 2 and fields[:2] == [_BRIDGE_VERSION, "ERROR"]:
        reason = " ".join(fields[2:]) or "authentication failed"
        raise MachineError(f"E3 bridge rejected the connection: {reason}")
    if len(fields) == 2 and fields[0] == _BRIDGE_VERSION and fields[1] == "BUSY":
        raise MachineError("E3 bridge is already in use by another controller client")
    if len(fields) != 3 or fields[:2] != [_BRIDGE_VERSION, "READY"]:
        raise MachineError("E3 bridge did not complete authentication")
    try:
        remote_baudrate = int(fields[2])
    except ValueError as exc:
        raise MachineError("E3 bridge reported an invalid controller baud rate") from exc
    if remote_baudrate != baudrate:
        raise MachineError(
            "E3 bridge controller baud rate does not match this machine profile "
            f"({remote_baudrate} != {baudrate})"
        )


class NetworkSerialTransport:
    """Cross-platform MachineTransport backed by an authenticated E3 Pi bridge."""

    def __init__(self, address: str, baudrate: int = 115200):
        self.address = address
        self.baudrate = baudrate
        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._socket is not None

    @property
    def configured_endpoint(self) -> str:
        return self.address

    @property
    def resolved_endpoint(self) -> str:
        target = parse_bridge_uri(self.address)
        return f"{target.host}:{target.port}"

    def open(self) -> None:
        if self.is_open:
            return
        target = parse_bridge_uri(self.address)
        token = bridge_token_from_environment()
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection(
                (target.host, target.port), timeout=_HANDSHAKE_TIMEOUT_SECONDS
            )
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            _authenticate(sock, token, self.baudrate)
            sock.setblocking(False)
        except socket.gaierror as exc:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            raise MachineError(
                f"Could not resolve E3 bridge host {target.host}: {exc}"
            ) from exc
        except OSError as exc:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            raise TransientConnectionError(
                f"Could not connect to E3 bridge at {target.host}:{target.port}: {exc}"
            ) from exc
        except Exception:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            raise
        self._buffer.clear()
        self._socket = sock

    def close(self) -> None:
        sock = self._socket
        self._socket = None
        self._buffer.clear()
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise MachineError("E3 bridge connection is not open")
        return self._socket

    def write_raw(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("Controller transport writes must be bytes")
        if not data:
            return
        with self._write_lock:
            sock = self._require_socket()
            view = memoryview(data)
            deadline = time.monotonic() + _NETWORK_WRITE_TIMEOUT_SECONDS
            while view:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MachineError("E3 bridge write timed out")
                try:
                    _, writable, _ = select.select([], [sock], [], remaining)
                    if not writable:
                        raise MachineError("E3 bridge write timed out")
                    written = sock.send(view)
                except OSError as exc:
                    raise MachineError(f"E3 bridge write failed: {exc}") from exc
                if written <= 0:
                    raise MachineError("E3 bridge connection closed during write")
                view = view[written:]

    def write_line(self, line: str) -> None:
        self.write_raw(line.rstrip("\r\n").encode("ascii", errors="replace") + b"\n")

    def _pop_line(self) -> str | None:
        newline_positions = [
            position
            for position in (self._buffer.find(b"\n"), self._buffer.find(b"\r"))
            if position >= 0
        ]
        if not newline_positions:
            if len(self._buffer) > _MAX_SERIAL_LINE_BYTES:
                raise MachineError(
                    f"E3 bridge response line exceeded {_MAX_SERIAL_LINE_BYTES} bytes"
                )
            return None
        index = min(newline_positions)
        if index > _MAX_SERIAL_LINE_BYTES:
            raise MachineError(
                f"E3 bridge response line exceeded {_MAX_SERIAL_LINE_BYTES} bytes"
            )
        raw = bytes(self._buffer[:index])
        del self._buffer[: index + 1]
        while self._buffer and self._buffer[0] in (10, 13):
            del self._buffer[:1]
        text = raw.decode("utf-8", errors="replace").strip()
        return text or self._pop_line()

    def _receive_available(self, timeout: float) -> bool:
        sock = self._require_socket()
        try:
            readable, _, _ = select.select([sock], [], [], max(0.0, timeout))
            if not readable:
                return False
            chunk = sock.recv(4096)
        except OSError as exc:
            raise MachineError(f"E3 bridge read failed: {exc}") from exc
        if not chunk:
            raise MachineError("E3 bridge connection closed")
        self._buffer.extend(chunk)
        if len(self._buffer) > _MAX_RECEIVE_BUFFER_BYTES and b"\n" not in self._buffer and b"\r" not in self._buffer:
            raise MachineError("E3 bridge receive buffer exceeded its safety limit")
        return True

    def read_line(self, timeout: float = 1.0) -> str | None:
        with self._read_lock:
            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                line = self._pop_line()
                if line is not None:
                    return line
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                if not self._receive_available(remaining):
                    return None

    def drain(self) -> list[str]:
        with self._read_lock:
            lines: list[str] = []
            while True:
                line = self._pop_line()
                if line is not None:
                    lines.append(line)
                    continue
                if not self._receive_available(0.0):
                    return lines

    def synchronize_input(
        self,
        *,
        quiet_interval: float = 0.15,
        timeout: float = 0.75,
    ) -> InputSynchronizationEvidence:
        """Discard framed/network-buffered RX until the bridge stays quiet.

        A local POSIX endpoint additionally purges the kernel TTY queue.  At
        this network boundary we can only prove quiet on the authenticated
        bridge stream, so callers retain the endpoint type in their evidence.
        """

        if quiet_interval <= 0 or timeout <= 0 or quiet_interval > timeout:
            raise ValueError(
                "Bridge synchronization needs a positive quiet interval no longer than its timeout"
            )
        started = time.monotonic()
        deadline = started + timeout
        discarded_bytes = 0
        discarded_lines = 0
        observed_activity = False
        with self._read_lock:
            discarded_bytes += len(self._buffer)
            discarded_lines += sum(
                1 for byte in self._buffer if byte in {10, 13}
            )
            self._buffer.clear()
            quiet_started = time.monotonic()
            while True:
                now = time.monotonic()
                if now - quiet_started >= quiet_interval:
                    return InputSynchronizationEvidence(
                        configured_endpoint=self.configured_endpoint,
                        resolved_endpoint=self.resolved_endpoint,
                        quiet_interval_seconds=quiet_interval,
                        elapsed_seconds=now - started,
                        discarded_bytes=discarded_bytes,
                        discarded_lines=discarded_lines,
                        observed_activity=observed_activity,
                    )
                remaining = deadline - now
                if remaining <= 0:
                    raise MachineError(
                        "E3 bridge input did not become quiet before the synchronization deadline"
                    )
                sock = self._require_socket()
                wait = min(quiet_interval - (now - quiet_started), remaining)
                try:
                    readable, _, _ = select.select([sock], [], [], wait)
                    if not readable:
                        continue
                    chunk = sock.recv(4096)
                except OSError as exc:
                    raise MachineError(
                        f"E3 bridge synchronization read failed: {exc}"
                    ) from exc
                if not chunk:
                    raise MachineError(
                        "E3 bridge connection closed while synchronizing input"
                    )
                observed_activity = True
                discarded_bytes += len(chunk)
                discarded_lines += sum(1 for byte in chunk if byte in {10, 13})
                quiet_started = time.monotonic()
