from __future__ import annotations

import errno
import glob
import os
import queue
import select
import termios
import threading
import time
from pathlib import Path

from ..errors import MachineError, TransientConnectionError

_BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}
if hasattr(termios, "B230400"):
    _BAUD_RATES[230400] = termios.B230400
_SERIAL_WRITE_TIMEOUT_SECONDS = 0.25
_MAX_SERIAL_LINE_BYTES = 8192
_MAX_QUEUED_SERIAL_LINES = 4096


def list_serial_ports() -> list[dict[str, str]]:
    by_id_entries = sorted(glob.glob("/dev/serial/by-id/*"))
    by_resolved = {str(Path(path).resolve()): path for path in by_id_entries}
    candidates = set(glob.glob("/dev/ttyUSB*")) | set(glob.glob("/dev/ttyACM*"))
    candidates |= set(by_id_entries)
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in sorted(candidates):
        resolved = str(Path(path).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        results.append({"path": by_resolved.get(resolved, path), "resolved": resolved})
    return results


class PosixSerial:
    def __init__(self, path: str, baudrate: int = 115200):
        self.path = path
        self.baudrate = baudrate
        self._fd: int | None = None
        self._queue: queue.Queue[str | MachineError] = queue.Queue(
            maxsize=_MAX_QUEUED_SERIAL_LINES
        )
        self._buffer = bytearray()
        self._stop = threading.Event()
        self._fault_lock = threading.Lock()
        self._fault_message: str | None = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._receive_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def open(self) -> None:
        with self._lifecycle_lock:
            self._open_locked()

    def _open_locked(self) -> None:
        if self.is_open:
            return
        baud = _BAUD_RATES.get(self.baudrate)
        if baud is None:
            raise MachineError(f"Unsupported POSIX baud rate: {self.baudrate}")
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.ENOENT, errno.ENOTDIR}:
                raise MachineError(
                    f"Could not open serial port {self.path}: {exc}"
                ) from exc
            raise TransientConnectionError(
                f"Could not open serial port {self.path}: {exc}"
            ) from exc
        try:
            attrs = termios.tcgetattr(fd)
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
            attrs[3] = 0
            attrs[4] = baud
            attrs[5] = baud
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 1
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIFLUSH)
        except OSError as exc:
            os.close(fd)
            raise MachineError(f"Could not configure serial port {self.path}: {exc}") from exc
        # A transport instance may be reopened after a controller reset or
        # disconnect. Replies and partial bytes belong to the old descriptor
        # and must never acknowledge commands in the new serial session.
        with self._receive_lock:
            self._queue = queue.Queue(maxsize=_MAX_QUEUED_SERIAL_LINES)
            self._buffer.clear()
        with self._fault_lock:
            self._fault_message = None
            self._stop.clear()
        self._fd = fd
        self._reader = threading.Thread(target=self._reader_loop, name="serial-reader", daemon=True)
        self._reader.start()

    def close(self) -> None:
        with self._lifecycle_lock:
            with self._fault_lock:
                self._stop.set()
            if self._reader and self._reader.is_alive():
                self._reader.join(timeout=1)
            fd = self._fd
            self._fd = None
            self._reader = None
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            with self._receive_lock:
                self._buffer.clear()
                self._queue = queue.Queue(maxsize=_MAX_QUEUED_SERIAL_LINES)

    def _reader_loop(self) -> None:
        fd = self._fd
        while not self._stop.is_set() and fd is not None:
            try:
                with self._receive_lock:
                    readable, _, _ = select.select([fd], [], [], 0.1)
                    if not readable:
                        continue
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        if not self._stop.is_set():
                            self._fail_reader("Serial connection closed unexpectedly")
                        return
                    self._buffer.extend(chunk)
                    while b"\n" in self._buffer or b"\r" in self._buffer:
                        newline_positions = [position for position in (self._buffer.find(b"\n"), self._buffer.find(b"\r")) if position >= 0]
                        index = min(newline_positions)
                        if index > _MAX_SERIAL_LINE_BYTES:
                            self._fail_reader(
                                f"Serial response line exceeded {_MAX_SERIAL_LINE_BYTES} bytes"
                            )
                            return
                        raw = bytes(self._buffer[:index])
                        del self._buffer[: index + 1]
                        while self._buffer and self._buffer[0] in (10, 13):
                            del self._buffer[:1]
                        text = raw.decode("utf-8", errors="replace").strip()
                        if text and not self._publish_line(text):
                            return
                    if len(self._buffer) > _MAX_SERIAL_LINE_BYTES:
                        self._fail_reader(
                            f"Serial response line exceeded {_MAX_SERIAL_LINE_BYTES} bytes"
                        )
                        return
            except (OSError, ValueError):
                if not self._stop.is_set():
                    self._fail_reader("Serial read failed")
                return

    def _publish_line(self, line: str) -> bool:
        try:
            self._queue.put_nowait(line)
            return True
        except queue.Full:
            self._fail_reader(
                f"Serial receive queue exceeded {_MAX_QUEUED_SERIAL_LINES} lines"
            )
            return False

    def _fail_reader(self, message: str) -> None:
        with self._fault_lock:
            if self._stop.is_set():
                return
            self._fault_message = message
            self._stop.set()
        with self._receive_lock:
            self._buffer.clear()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            self._queue.put_nowait(MachineError(message))

    @property
    def fault(self) -> str | None:
        """Return the latched reader failure without consuming response data."""

        with self._fault_lock:
            return self._fault_message

    def raise_if_faulted(self) -> None:
        """Raise a fresh error when the sole reader has latched a failure."""

        message = self.fault
        if message is not None:
            raise MachineError(message)

    def write_raw(self, data: bytes) -> None:
        if self._fd is None:
            raise MachineError("Serial port is not open")
        with self._write_lock:
            view = memoryview(data)
            deadline = time.monotonic() + _SERIAL_WRITE_TIMEOUT_SECONDS
            while view:
                try:
                    written = os.write(self._fd, view)
                    if written <= 0:
                        raise BlockingIOError
                    view = view[written:]
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise MachineError(
                            "Serial write timed out while the controller was not accepting data"
                        ) from None
                    select.select([], [self._fd], [], min(0.02, remaining))
                except OSError as exc:
                    raise MachineError(f"Serial write failed: {exc}") from exc

    def write_line(self, line: str) -> None:
        self.write_raw(line.rstrip("\r\n").encode("ascii", errors="replace") + b"\n")

    def read_line(self, timeout: float = 1.0) -> str | None:
        with self._receive_lock:
            receive_queue = self._queue
        try:
            response = receive_queue.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        if isinstance(response, MachineError):
            raise response
        return response

    def drain(self) -> list[str]:
        with self._receive_lock:
            lines: list[str] = []
            while True:
                try:
                    response = self._queue.get_nowait()
                except queue.Empty:
                    return lines
                if isinstance(response, MachineError):
                    raise response
                lines.append(response)

    def synchronize_input(self) -> None:
        """Discard every receive byte that predates the next command."""

        with self._receive_lock:
            fd = self._fd
            if fd is None:
                raise MachineError("Serial port is not open")
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            except OSError as exc:
                raise MachineError(f"Could not purge serial input: {exc}") from exc
            self._buffer.clear()
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return
