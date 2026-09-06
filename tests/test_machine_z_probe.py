from __future__ import annotations

import threading
import time
from unittest.mock import Mock

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.service import _PROBE_SUSPENSION_REASON as SUSPENSION_REASON
from laser_aligner.machine.service import MachineService
from tests.fakes.simulator_transport import SimulatedTransport
from tests.test_z_probe import ready_probe


@pytest.fixture
def machine_probe(monkeypatch):
    # Preserve synthetic sequence coverage; production has no bypass setting.
    monkeypatch.setattr("laser_aligner.machine.service._PROBE_SUSPENSION_REASON", "")
    serial, owner, fan, _, _ = ready_probe()
    primary = SimulatedTransport()
    monkeypatch.setattr("laser_aligner.machine.service.create_machine_transport", lambda *args: primary)
    machine = MachineService(
        MachineSettings(protocol="grbl", allow_motion=True, controller_startup_delay=0),
        LaserSettings(), hardware_enabled=True, secondary_air_assist=fan,
    )
    machine.connect()
    machine.prepare_photo_position()
    yield machine, serial, owner, primary
    machine.disconnect()


def reference(machine, **kwargs):
    return machine.probe_z("reference", confirmed=True, support_height_mm=-1.5, **kwargs)


@pytest.mark.parametrize("operation", ["reference", "measure"])
def test_production_suspension_rejects_before_any_controller_command(machine_probe, monkeypatch, operation):
    machine, serial, _, primary = machine_probe
    reference(machine)
    before_secondary = list(serial.writes)
    primary_line = Mock(wraps=primary.write_line)
    primary_raw = Mock(wraps=primary.write_raw)
    monkeypatch.setattr(primary, "write_line", primary_line)
    monkeypatch.setattr(primary, "write_raw", primary_raw)
    monkeypatch.setattr("laser_aligner.machine.service._PROBE_SUSPENSION_REASON", SUSPENSION_REASON)
    with pytest.raises(SafetyError, match="Material probing is suspended"):
        machine.probe_z(operation, confirmed=True, support_height_mm=-1.5)
    assert serial.writes == before_secondary
    primary_line.assert_not_called()
    primary_raw.assert_not_called()
    status = machine.status()["z_probe"]
    assert not status["available"] and not status["reference_ready"]
    assert status["unavailable_reason"] == SUSPENSION_REASON


def test_complete_service_reference_jog_measure_has_no_secondary_xy_authority(machine_probe):
    machine, serial, _, primary = machine_probe
    before = (primary.x, primary.y)
    reference(machine)
    assert (primary.x, primary.y) == before
    machine.jog(-10, -10, 300)
    result = machine.probe_z("measure", confirmed=True, support_height_mm=-1.5)
    assert result["surface_height_mm"] == pytest.approx(3)
    assert result["thickness_above_honeycomb_mm"] == pytest.approx(4.5)
    assert result["carriage_xy_mm"] == [100, 100]
    assert (primary.x, primary.y) == (100, 100)
    assert not machine.armed
    assert machine.status()["z_probe"]["reference_ready"]
    assert serial.z == 20


@pytest.mark.parametrize("change", ["hardware", "motion", "armed", "job", "unhomed", "position", "outside"])
def test_service_rejects_without_secondary_motion(machine_probe, change):
    machine, serial, _, _ = machine_probe
    if change == "hardware":
        machine.hardware_enabled = False
    elif change == "motion":
        machine.settings.allow_motion = False
    elif change == "armed":
        machine._armed_until_monotonic = time.monotonic() + 60
        machine._armed_until = time.time() + 60
    elif change == "job":
        machine._job.running = True
    elif change == "unhomed":
        machine._coordinate_reference_ready = False
    elif change == "position":
        machine._jog_position_mm = (20, 20)
    elif change == "outside":
        machine._jog_position_mm = (-1, 100)
    before = list(serial.writes)
    try:
        with pytest.raises(MachineError):
            reference(machine)
        assert serial.writes == before
    finally:
        machine._job.running = False


@pytest.mark.parametrize(("field", "value"), [
    ("confirmed", False), ("confirmed", 1), ("clearance_z_mm", float("nan")),
    ("clearance_z_mm", 19), ("clearance_z_mm", 81), ("support_height_mm", True),
    ("support_height_mm", -21),
])
def test_invalid_requests_do_not_write(machine_probe, field, value):
    machine, serial, _, _ = machine_probe
    args = {"confirmed": True, "clearance_z_mm": 20, "support_height_mm": -1.5, field: value}
    before = list(serial.writes)
    with pytest.raises(SafetyError):
        machine.probe_z("reference", **args)
    assert serial.writes == before


def test_home_invalidates_reference_and_changed_inputs_are_rejected(machine_probe):
    machine, serial, _, _ = machine_probe
    reference(machine)
    before = list(serial.writes)
    with pytest.raises(SafetyError, match="changed"):
        machine.probe_z("measure", confirmed=True, support_height_mm=0)
    assert serial.writes == before
    machine.prepare_photo_position()
    assert not machine.status()["z_probe"]["reference_ready"]
    with pytest.raises(SafetyError, match="Reference"):
        machine.probe_z("measure", confirmed=True, support_height_mm=-1.5)


def test_ack_only_contact_invalidates_and_stops(machine_probe):
    machine, serial, _, _ = machine_probe
    serial.overrides["G30 X110 Y110"] = ["ok"]
    with pytest.raises(MachineError, match="contact height"):
        reference(machine)
    assert machine._z_probe.reference is None
    assert machine._z_probe_result is None
    assert not machine.status()["coordinate_reference_ready"]


@pytest.mark.parametrize("cancel", ["stop", "disconnect", "disarm", "network"])
def test_interruption_during_secondary_ack_is_prompt_and_drops_height(machine_probe, cancel):
    machine, serial, _, _ = machine_probe
    serial.overrides["G28"] = []
    entered = threading.Event()
    alive = threading.Event()
    alive.set()
    serial.on_write = lambda command: entered.set() if command == "G28" else None
    errors = []

    def run():
        try:
            reference(machine, _connection_alive=alive.is_set)
        except MachineError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(3)
    start = time.monotonic()
    assert machine.status()["z_probe"]["active"]
    if cancel == "network":
        alive.clear()
    elif cancel == "stop":
        machine.request_stop(_recover=False)
    else:
        getattr(machine, cancel)()
    worker.join(2)
    assert time.monotonic() - start < 2
    assert not worker.is_alive() and errors
    assert machine._z_probe.reference is None and machine._z_probe_result is None
    assert "G30 X110 Y110" not in serial.writes
    assert "M112" in serial.writes
