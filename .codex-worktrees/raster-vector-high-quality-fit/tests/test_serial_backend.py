import subprocess
import sys

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError
from laser_aligner.machine import serial_backend
from laser_aligner.machine.service import MachineService


def test_machine_service_import_does_not_load_posix_transport() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import laser_aligner.machine.service; "
            "print('laser_aligner.machine.serial_posix' in sys.modules)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "False"


def test_unsupported_platform_lists_no_serial_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serial_backend, "posix_serial_supported", lambda: False)

    assert serial_backend.list_serial_ports() == []


def test_unsupported_platform_rejects_serial_backend_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serial_backend, "posix_serial_supported", lambda: False)
    machine = MachineService(
        MachineSettings(backend="serial", port="unsupported"),
        LaserSettings(),
        hardware_enabled=True,
    )

    with pytest.raises(MachineError, match="POSIX serial hardware is unavailable"):
        machine.connect()

    assert not machine.connected
