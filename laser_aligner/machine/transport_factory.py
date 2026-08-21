from __future__ import annotations

from ..errors import MachineError
from .transport import MachineTransport


def create_machine_transport(
    backend: str,
    port: str,
    baudrate: int,
) -> MachineTransport:
    """Construct one unopened transport from the existing saved settings.

    Opening, retries, command exchange, and cleanup remain the responsibility
    of ``MachineService``.  The controller protocol is intentionally not an
    input because transport selection has no controller-command semantics.
    """

    if backend == "serial":
        from .serial_backend import create_serial_transport

        return create_serial_transport(port, baudrate)
    raise MachineError(
        "Machine transport backend must be exactly 'serial'"
    )


__all__ = ["create_machine_transport"]
