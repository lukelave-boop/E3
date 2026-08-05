from __future__ import annotations

import glob
import os
import queue
import select
import termios
import threading
import time
from pathlib import Path

from ..errors import MachineError

_BAUD_RATES = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
    230400: getattr(termios, "B230400", termios.B115200),
}


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
        self._queue: queue.Queue[str] = queue.Queue()
        self._buffer = bytearray()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def open(self) -> None:
        if self.is_open:
            return
        baud = _BAUD_RATES.get(self.baudrate)
        if baud is None:
            raise MachineError(f"Unsupported POSIX baud rate: {self.baudrate}")
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            raise MachineError(f"Could not open serial port {self.path}: {exc}") from exc
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
            termios.tcflush(fd, termios.TCIOFLUSH)
        except OSError as exc:
            os.close(fd)
            raise MachineError(f"Could not configure serial port {self.path}: {exc}") from exc
        self._fd = fd
        self._stop.clear()
        self._reader = threading.Thread(target=self._reader_loop, name="serial-reader", daemon=True)
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=1)
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = None

    def _reader_loop(self) -> None:
        while not self._stop.is_set() and self._fd is not None:
            try:
                readable, _, _ = select.select([self._fd], [], [], 0.1)
                if not readable:
                    continue
                chunk = os.read(self._fd, 4096)
                if not chunk:
                    continue
                self._buffer.extend(chunk)
                while b"\n" in self._buffer or b"\r" in self._buffer:
                    newline_positions = [position for position in (self._buffer.find(b"\n"), self._buffer.find(b"\r")) if position >= 0]
                    index = min(newline_positions)
                    raw = bytes(self._buffer[:index])
                    del self._buffer[: index + 1]
                    while self._buffer and self._buffer[0] in (10, 13):
                        del self._buffer[:1]
                    text = raw.decode("utf-8", errors="replace").strip()
                    if text:
                        self._queue.put(text)
            except (OSError, ValueError):
                if not self._stop.is_set():
                    self._queue.put("[serial read error]")
                return

    def write_raw(self, data: bytes) -> None:
        if self._fd is None:
            raise MachineError("Serial port is not open")
        with self._write_lock:
            view = memoryview(data)
            while view:
                try:
                    written = os.write(self._fd, view)
                    view = view[written:]
                except BlockingIOError:
                    time.sleep(0.01)
                except OSError as exc:
                    raise MachineError(f"Serial write failed: {exc}") from exc

    def write_line(self, line: str) -> None:
        self.write_raw(line.rstrip("\r\n").encode("ascii", errors="replace") + b"\n")

    def read_line(self, timeout: float = 1.0) -> str | None:
        try:
            return self._queue.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def drain(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._queue.get_nowait())
            except queue.Empty:
                return lines
