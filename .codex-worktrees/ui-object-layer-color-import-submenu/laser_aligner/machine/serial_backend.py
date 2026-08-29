from __future__ import annotations

import os

from ..errors import MachineError
from .transport import MachineTransport


def posix_serial_supported() -> bool:
    return os.name == "posix"


def create_serial_transport(path: str, baudrate: int) -> MachineTransport:
    """Create a local or network serial transport without eager platform imports."""

    from .network_transport import is_bridge_uri

    if is_bridge_uri(path):
        from .network_transport import NetworkSerialTransport

        return NetworkSerialTransport(path, baudrate)
    if not posix_serial_supported():
        raise MachineError(
            "POSIX serial hardware is unavailable on this platform. "
            "Use an e3bridge:// machine endpoint."
        )
    from .serial_posix import PosixSerial

    return PosixSerial(path, baudrate)


def list_serial_ports() -> list[dict[str, str]]:
    """List local serial ports when the supported POSIX backend is available."""

    if not posix_serial_supported():
        return []
    from .serial_posix import list_serial_ports as list_posix_serial_ports

    return list_posix_serial_ports()
