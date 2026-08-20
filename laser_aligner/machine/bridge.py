from __future__ import annotations

import argparse
import hashlib
import hmac
import logging
import os
import secrets
import select
import socket
import threading
import time
from collections.abc import Callable
from typing import Protocol

from ..errors import MachineError
from .controller_dialects import CONTROLLER_DIALECT_REGISTRY

LOGGER = logging.getLogger(__name__)
_BRIDGE_VERSION = "E3BRIDGE/1"
_TOKEN_ENV = "E3_BRIDGE_TOKEN"
_MIN_TOKEN_LENGTH = 24
_DEFAULT_PORT = 8765
_HANDSHAKE_TIMEOUT_SECONDS = 5.0
_NETWORK_WRITE_TIMEOUT_SECONDS = 0.35
_MAX_HANDSHAKE_LINE_BYTES = 1024


class BridgeSerial(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def write_raw(self, data: bytes) -> None: ...
    def write_line(self, line: str) -> None: ...
    def read_line(self, timeout: float = 1.0) -> str | None: ...


def _load_token() -> str:
    token = os.environ.get(_TOKEN_ENV, "")
    if len(token) < _MIN_TOKEN_LENGTH:
        raise MachineError(
            f"{_TOKEN_ENV} must be set to a secret of at least {_MIN_TOKEN_LENGTH} characters"
        )
    return token


def _read_line(conn: socket.socket, timeout: float) -> str:
    deadline = time.monotonic() + max(0.0, timeout)
    data = bytearray()
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        readable, _, _ = select.select([conn], [], [], max(0.0, remaining))
        if not readable:
            break
        chunk = conn.recv(1)
        if not chunk:
            raise MachineError("Client disconnected during E3 bridge authentication")
        if chunk in {b"\n", b"\r"}:
            if data:
                return data.decode("ascii", errors="strict")
            continue
        data.extend(chunk)
        if len(data) > _MAX_HANDSHAKE_LINE_BYTES:
            raise MachineError("E3 bridge authentication line is too long")
    raise MachineError("Timed out waiting for E3 bridge authentication")


def _send_with_deadline(conn: socket.socket, payload: bytes) -> None:
    view = memoryview(payload)
    deadline = time.monotonic() + _NETWORK_WRITE_TIMEOUT_SECONDS
    while view:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MachineError("E3 bridge client stopped accepting data")
        _, writable, _ = select.select([], [conn], [], remaining)
        if not writable:
            raise MachineError("E3 bridge client stopped accepting data")
        written = conn.send(view)
        if written <= 0:
            raise MachineError("E3 bridge client connection closed during write")
        view = view[written:]


def _send_line(conn: socket.socket, line: str) -> None:
    _send_with_deadline(conn, line.rstrip("\r\n").encode("ascii", errors="replace") + b"\n")


def _authenticate_client(conn: socket.socket, token: str) -> bool:
    challenge = secrets.token_bytes(32)
    _send_line(conn, f"{_BRIDGE_VERSION} CHALLENGE {challenge.hex()}")
    response = _read_line(conn, _HANDSHAKE_TIMEOUT_SECONDS).split()
    if len(response) != 3 or response[:2] != [_BRIDGE_VERSION, "AUTH"]:
        _send_line(conn, f"{_BRIDGE_VERSION} ERROR authentication_failed")
        return False
    expected = hmac.new(token.encode("utf-8"), challenge, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(response[2], expected):
        _send_line(conn, f"{_BRIDGE_VERSION} ERROR authentication_failed")
        return False
    return True


def _default_serial_factory(path: str, baudrate: int) -> BridgeSerial:
    if os.name != "posix":
        raise MachineError("The E3 machine bridge requires a POSIX host such as Raspberry Pi OS")
    from .serial_posix import PosixSerial

    return PosixSerial(path, baudrate)


def _fail_safe_disconnect(serial: BridgeSerial, protocol: str) -> None:
    """Best-effort controller shutdown after loss of the authenticated client.

    This is a software cleanup path, not a safety-rated emergency stop. For
    GRBL, realtime feed hold plus soft reset prevents queued work from silently
    resuming when the network client disappears. M5 is still attempted after
    the reset. Physical emergency-stop hardware remains authoritative.
    """

    try:
        stop_policy = CONTROLLER_DIALECT_REGISTRY.get(protocol).emergency_stop
    except KeyError:
        stop_policy = None
    if stop_policy is not None and stop_policy.raw_command is not None:
        try:
            serial.write_raw(stop_policy.raw_command)
        except Exception:
            LOGGER.exception(
                "Could not issue %s after client loss",
                stop_policy.failure_label,
            )
    elif stop_policy is not None and stop_policy.line_command is not None:
        try:
            serial.write_line(stop_policy.line_command)
        except Exception:
            LOGGER.exception(
                "Could not issue %s after client loss",
                stop_policy.failure_label,
            )
    try:
        serial.write_line("M5")
    except Exception:
        LOGGER.exception("Could not issue M5 after E3 bridge client loss")


def _serve_authenticated_client(
    conn: socket.socket,
    *,
    serial_path: str,
    baudrate: int,
    protocol: str,
    serial_factory: Callable[[str, int], BridgeSerial],
) -> None:
    serial = serial_factory(serial_path, baudrate)
    serial_open = False
    try:
        serial.open()
        serial_open = True
        try:
            serial.write_line("M5")
        except Exception as exc:
            LOGGER.exception("Initial E3 bridge M5 cleanup failed")
            raise MachineError(
                "Controller did not accept the bridge startup laser-off request"
            ) from exc
        _send_line(conn, f"{_BRIDGE_VERSION} READY {baudrate}")
        conn.setblocking(False)
        while True:
            readable, _, _ = select.select([conn], [], [], 0.02)
            if readable:
                try:
                    payload = conn.recv(4096)
                except BlockingIOError:
                    payload = None
                if payload == b"":
                    return
                if payload:
                    serial.write_raw(payload)
            while True:
                response = serial.read_line(timeout=0.0)
                if response is None:
                    break
                _send_line(conn, response)
    finally:
        if serial_open:
            _fail_safe_disconnect(serial, protocol)
            serial.close()


class BridgeServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        serial_path: str,
        baudrate: int,
        protocol: str,
        token: str,
        serial_factory: Callable[[str, int], BridgeSerial] = _default_serial_factory,
    ):
        self.host = host
        self.port = port
        self.serial_path = serial_path
        self.baudrate = baudrate
        self.protocol = protocol
        self.token = token
        self.serial_factory = serial_factory
        self._client_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_client: socket.socket | None = None
        self._stop = threading.Event()
        self._listener: socket.socket | None = None

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._state_lock:
            active_client = self._active_client
        if active_client is not None:
            try:
                active_client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _handle_connection(self, conn: socket.socket, address: tuple[object, ...]) -> None:
        with conn:
            acquired = False
            try:
                conn.setblocking(True)
                if not _authenticate_client(conn, self.token):
                    LOGGER.warning(
                        "Rejected unauthenticated E3 bridge client from %s",
                        address[0],
                    )
                    return
                acquired = self._client_lock.acquire(blocking=False)
                if not acquired:
                    _send_line(conn, f"{_BRIDGE_VERSION} BUSY")
                    return
                LOGGER.info("Authenticated E3 bridge client from %s", address[0])
                with self._state_lock:
                    if self._stop.is_set():
                        return
                    self._active_client = conn
                _serve_authenticated_client(
                    conn,
                    serial_path=self.serial_path,
                    baudrate=self.baudrate,
                    protocol=self.protocol,
                    serial_factory=self.serial_factory,
                )
            except Exception as exc:
                LOGGER.warning("E3 bridge client session ended: %s", exc)
                try:
                    _send_line(conn, f"{_BRIDGE_VERSION} ERROR session_failed")
                except Exception:
                    pass
            finally:
                if acquired:
                    with self._state_lock:
                        if self._active_client is conn:
                            self._active_client = None
                    self._client_lock.release()
                LOGGER.info("E3 bridge client disconnected")

    def serve_forever(self) -> None:
        if not 1 <= self.port <= 65535:
            raise MachineError("E3 bridge port must be between 1 and 65535")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(4)
        listener.settimeout(0.5)
        self._listener = listener
        LOGGER.info(
            "E3 bridge listening on %s:%d for %s at %d baud",
            self.host,
            self.port,
            self.serial_path,
            self.baudrate,
        )
        try:
            while not self._stop.is_set():
                try:
                    conn, address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                threading.Thread(
                    target=self._handle_connection,
                    args=(conn, address),
                    name="e3-bridge-client",
                    daemon=True,
                ).start()
        finally:
            self._listener = None
            try:
                listener.close()
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authenticated E3 controller bridge")
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="Explicitly enable access to the physical controller",
    )
    parser.add_argument("--serial", required=True, help="Pi-local controller serial device")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address; use 0.0.0.0 only on a trusted/firewalled machine network",
    )
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--protocol", choices=("grbl", "marlin"), default="grbl")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.hardware is not True:
        parser.error("--hardware is required before the E3 bridge may open a controller")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = _load_token()
    server = BridgeServer(
        host=args.host,
        port=args.port,
        serial_path=args.serial,
        baudrate=args.baudrate,
        protocol=args.protocol,
        token=token,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping E3 bridge")
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
