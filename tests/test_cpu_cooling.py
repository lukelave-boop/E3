import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from laser_aligner.cpu_cooling import CpuCoolingWorker, enabled_from_environment, read_cpu_temperature
from laser_aligner.errors import MachineError
from laser_aligner.machine.cpu_cooling import CoolingPolicy
from tests import test_machine_z_probe as helpers
from tests.test_marlin_mainboard import mainboard_serial

machine_probe = helpers.machine_probe

def test_thresholds_hysteresis_and_missing_sensor():
    policy = CoolingPolicy()
    assert [policy.demand(t) for t in (44, 45, 44, 41, 40, 44, 46, None, 42, 39)] == [0, 255, 255, 255, 0, 0, 255, 255, 255, 0]


@pytest.mark.parametrize("temperature", [True, "45", float("nan"), float("inf"), -21, 151])
def test_invalid_sensor_values(temperature):
    with pytest.raises(ValueError):
        CoolingPolicy().demand(temperature)


def test_environment_defaults_off_and_rejects_unsupported_platform(monkeypatch):
    monkeypatch.delenv("E3_CPU_COOLING", raising=False)
    assert not enabled_from_environment()
    monkeypatch.setenv("E3_CPU_COOLING", "1")
    monkeypatch.setattr("laser_aligner.cpu_cooling.sys.platform", "win32")
    with pytest.raises(ValueError, match="Linux"):
        enabled_from_environment()
    monkeypatch.setattr("laser_aligner.cpu_cooling.sys.platform", "linux")
    assert enabled_from_environment()
    monkeypatch.setenv("E3_CPU_COOLING", "yes")
    with pytest.raises(ValueError):
        enabled_from_environment()


def test_linux_sensor_millidegrees_and_type_selection(tmp_path, monkeypatch):
    from pathlib import Path
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "type").write_text("cpu-thermal\n")
    (zone / "temp").write_text("45200\n")
    monkeypatch.setattr("laser_aligner.cpu_cooling.sys.platform", "linux")
    monkeypatch.setattr("laser_aligner.cpu_cooling.Path", lambda _: Path(tmp_path))
    assert read_cpu_temperature() == 45.2
    (zone / "temp").write_text("nan")
    with pytest.raises(ValueError):
        read_cpu_temperature()


@pytest.fixture
def cooling(machine_probe):
    machine, serial, owner, primary = machine_probe
    state = mainboard_serial(serial)
    machine.cpu_cooling_enabled = True
    serial.writes.clear()
    return machine, serial, owner, primary, state


def test_cooling_updates_only_fan1_while_job_and_laser_are_active(cooling):
    machine, serial, _, primary, state = cooling
    primary.write_line = Mock(wraps=primary.write_line)
    state["fan2"] = 255
    machine._job.running = True
    try:
        result = machine.update_cpu_cooling(255)
        assert result["state"] == "active"
        assert state["fan1"] == state["fan2"] == 255
        assert serial.writes == ["M115", "M123", "M106 P1 S255", "M123"]
        serial.writes.clear()
        machine.update_cpu_cooling(255)
        assert serial.writes == ["M115", "M123"]
        primary.write_line.assert_not_called()
    finally:
        machine._job.running = False


@pytest.mark.parametrize("change", ["disabled", "hardware", "probe", "closed"])
def test_gates_never_open_or_write(cooling, change):
    machine, serial, owner, _, _ = cooling
    opens = serial.open_calls
    if change == "disabled":
        machine.cpu_cooling_enabled = False
    elif change == "hardware":
        machine.hardware_enabled = False
    elif change == "probe":
        machine._z_probe_active = True
    else:
        owner.close()
    serial.writes.clear()
    try:
        machine.update_cpu_cooling(255)
    except MachineError:
        assert change in {"disabled", "hardware"}
    assert not serial.writes and serial.open_calls == opens
    machine._z_probe_active = False


def test_busy_owner_is_skipped_without_blocking(cooling):
    machine, serial, owner, _, _ = cooling
    locked, release = threading.Event(), threading.Event()
    def hold():
        with owner._lock:
            locked.set()
            release.wait(2)
    worker = threading.Thread(target=hold)
    worker.start()
    assert locked.wait(1)
    try:
        assert machine.update_cpu_cooling(255) == {"state": "busy"}
        assert not serial.writes
    finally:
        release.set()
        worker.join(2)


def test_stop_epoch_suspends_future_ticks(cooling):
    machine, serial, _, _, _ = cooling
    machine.update_cpu_cooling(255)
    with machine._stop_epoch_lock:
        machine._stop_epoch += 1
    serial.writes.clear()
    assert machine.update_cpu_cooling(255)["state"] == "paused_until_reconnect"
    assert not serial.writes


def test_epoch_change_during_exchange_never_sends_fan_on(cooling):
    machine, serial, _, _, _ = cooling
    owner = machine._secondary_air_assist.owner
    old_execute = owner._execute_acknowledged
    def execute(command, **kwargs):
        result = old_execute(command, **kwargs)
        if command == "M123":
            with machine._stop_epoch_lock:
                machine._stop_epoch += 1
        return result
    owner._execute_acknowledged = execute
    with pytest.raises(MachineError):
        machine.update_cpu_cooling(255)
    assert "M106 P1 S255" not in serial.writes


def test_wrong_identity_cannot_write_and_pauses(cooling):
    machine, serial, _, _, _ = cooling
    serial.overrides["M115"] = ["FIRMWARE_NAME:Marlin stock", "ok"]
    with pytest.raises(MachineError):
        machine.update_cpu_cooling(255)
    assert not any(line.startswith("M106") for line in serial.writes)
    assert machine.update_cpu_cooling(255)["state"] == "paused_until_reconnect"


def test_manual_on_rejected_manual_off_pauses_auto(cooling):
    machine, _, _, _, state = cooling
    machine.update_cpu_cooling(255)
    with pytest.raises(MachineError, match="owned by automatic"):
        machine.mainboard_control("fan1", 100, confirmed=True)
    machine.mainboard_control("fan1", 0)
    assert state["fan1"] == 0
    assert machine.update_cpu_cooling(255)["state"] == "paused_until_reconnect"


def test_worker_sensor_failure_demands_full_cooling():
    values = iter([46, 43, OSError("missing"), 39])
    demands = []
    def read():
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value
    worker = CpuCoolingWorker(SimpleNamespace(update_cpu_cooling=lambda pwm: demands.append(pwm) or {"state": "active"}), read_temperature=read)
    for _ in range(4):
        worker.tick()
    assert demands == [255, 255, 255, 0]


def test_stop_before_first_tick_does_not_enable_cooling(cooling):
    machine, serial, _, _, _ = cooling
    machine.request_stop(_recover=False)
    serial.writes.clear()
    result = machine.update_cpu_cooling(255)
    assert result["state"] in {"unavailable", "paused_until_reconnect", "busy"}
    assert "M106 P1 S255" not in serial.writes


def test_reinitialized_owner_resumes_after_stop_epoch(cooling):
    machine, serial, owner, _, _ = cooling
    machine.update_cpu_cooling(255)
    with machine._stop_epoch_lock:
        machine._stop_epoch += 1
    assert machine.update_cpu_cooling(255)["state"] == "paused_until_reconnect"
    # Model an explicit successful owner reinitialization; the worker itself
    # cannot create this generation or reopen the port.
    with owner._lock:
        owner._generation += 1
    assert machine.update_cpu_cooling(255)["state"] == "active"


def test_worker_stop_wakes_sleep_without_an_extra_tick():
    called = threading.Event()
    machine = SimpleNamespace(update_cpu_cooling=lambda pwm: called.set() or {"state": "active"})
    worker = CpuCoolingWorker(machine, read_temperature=lambda: 46)
    worker.start()
    assert called.wait(1)
    worker.stop()
    assert not worker.thread.is_alive()
