from __future__ import annotations

from laser_aligner.machine import serial_backend
from laser_aligner.machine.network_transport import NetworkSerialTransport


def test_network_transport_is_available_when_local_posix_serial_is_not(monkeypatch) -> None:
    monkeypatch.setattr(serial_backend, "posix_serial_supported", lambda: False)
    transport = serial_backend.create_serial_transport(
        "e3bridge://e3-laser.local:8765", 115200
    )
    assert isinstance(transport, NetworkSerialTransport)
