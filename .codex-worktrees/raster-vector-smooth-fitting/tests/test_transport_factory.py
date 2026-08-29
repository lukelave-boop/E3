from __future__ import annotations

import sys
from types import ModuleType

import pytest

import laser_aligner.machine as machine_package
from laser_aligner.errors import MachineError
from laser_aligner.machine import serial_backend
from laser_aligner.machine.network_transport import NetworkSerialTransport
from laser_aligner.machine.transport import MachineTransport
from laser_aligner.machine.transport_factory import create_machine_transport


def test_serial_backend_preserves_machine_transport_compatibility_import() -> None:
    assert serial_backend.MachineTransport is MachineTransport


def test_transport_boundary_exposes_only_communication_mechanics() -> None:
    public_members = {
        name for name in MachineTransport.__dict__ if not name.startswith("_")
    }

    assert public_members == {
        "open",
        "close",
        "write_raw",
        "write_line",
        "read_line",
        "drain",
    }
    assert "MachineTransport" not in machine_package.__all__
    assert "create_machine_transport" not in machine_package.__all__


def test_factory_delegates_serial_construction_without_opening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    class UnopenedTransport:
        opened = False

        def open(self) -> None:
            self.opened = True

    transport = UnopenedTransport()

    def construct(port: str, baudrate: int) -> UnopenedTransport:
        calls.append((port, baudrate))
        return transport

    monkeypatch.setattr(serial_backend, "create_serial_transport", construct)

    result = create_machine_transport("serial", "COM17", 230400)

    assert result is transport
    assert calls == [("COM17", 230400)]
    assert transport.opened is False


@pytest.mark.parametrize(
    "address",
    [
        "e3bridge://e3-laser.local:8765",
        "E3BRIDGE://e3-laser.local:8765",
    ],
)
def test_factory_selects_bridge_before_the_local_platform_gate(
    address: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serial_backend, "posix_serial_supported", lambda: False)

    transport = create_machine_transport("serial", address, 115200)

    assert isinstance(transport, NetworkSerialTransport)
    assert transport.address == address
    assert transport.baudrate == 115200
    assert transport.is_open is False


def test_factory_defers_bridge_uri_validation_until_transport_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serial_backend, "posix_serial_supported", lambda: False)

    transport = create_machine_transport(
        "serial",
        "e3bridge://name:secret@e3-laser.local",
        115200,
    )

    assert isinstance(transport, NetworkSerialTransport)
    with pytest.raises(MachineError, match="must not be embedded"):
        transport.open()


def test_factory_preserves_unsupported_local_serial_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serial_backend, "posix_serial_supported", lambda: False)

    with pytest.raises(MachineError, match="POSIX serial hardware is unavailable"):
        create_machine_transport("serial", "COM17", 115200)


def test_factory_preserves_lazy_local_posix_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[tuple[str, int]] = []
    fake_module = ModuleType("laser_aligner.machine.serial_posix")

    class FakePosixSerial:
        def __init__(self, path: str, baudrate: int) -> None:
            created.append((path, baudrate))

    fake_module.PosixSerial = FakePosixSerial  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, fake_module.__name__, fake_module)
    monkeypatch.setattr(serial_backend, "posix_serial_supported", lambda: True)

    result = create_machine_transport("serial", "/dev/controller", 57600)

    assert isinstance(result, FakePosixSerial)
    assert created == [("/dev/controller", 57600)]


@pytest.mark.parametrize(
    "backend", ["simulator", "SIMULATOR", "serial ", "network", ""]
)
def test_factory_rejects_unknown_backends_without_normalizing(backend: str) -> None:
    with pytest.raises(MachineError, match="must be exactly"):
        create_machine_transport(backend, "unused", 115200)
