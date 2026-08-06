from __future__ import annotations

import os
from typing import Protocol

from ..errors import MachineError


class MachineTransport(Protocol):
    """Controller transport interface used by the guarded machine service."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write_raw(self, data: bytes) -> None: ...

    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout: float = 1.0) -> str | None: ...

    def drain(self) -> list[str]: ...


def posix_serial_supported() -> bool:
    return os.name == "posix"


def create_serial_transport(path: str, baudrate: int) -> MachineTransport:
    """Create the platform serial transport without importing it in simulation."""

    if not posix_serial_supported():
        raise MachineError(
            "POSIX serial hardware is unavailable on this platform. "
            "Use machine.backend='simulator'; Windows hardware control is not implemented."
        )
    from .serial_posix import PosixSerial

    return PosixSerial(path, baudrate)


def list_serial_ports() -> list[dict[str, str]]:
    """List serial ports when the supported POSIX backend is available."""

    if not posix_serial_supported():
        return []
    from .serial_posix import list_serial_ports as list_posix_serial_ports

    return list_posix_serial_ports()
