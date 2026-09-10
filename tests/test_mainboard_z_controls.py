from __future__ import annotations

import json
import threading
from contextlib import nullcontext
from dataclasses import replace

import pytest

from laser_aligner.config import ConfigError, LaserSettings, MachineSettings, load_settings
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.mainboard import CAPABILITY, control, validate_control
from laser_aligner.machine.service import MachineService
from laser_aligner.machine.z_limits import MainboardZLimits, mainboard_limits_path
from tests import test_machine_z_probe as helpers
from tests.test_marlin_mainboard import mainboard_serial
from tests.test_native_probe import native_probe  # noqa: F401
from tests.test_z_probe import ready_probe

machine_probe = helpers.machine_probe


@pytest.mark.parametrize(("action", "value", "confirmed"), [
    ("z_jog", 0, True), ("z_jog", 5.001, True), ("z_jog", -5.001, True),
    ("z_jog", True, True), ("z_jog", float("nan"), True), ("z_jog", 1, False),
    ("z_max", 19.99, True), ("z_max", 80.01, True), ("z_max", 60, False),
    ("z_max", float("inf"), True), ("z_max", "60", True), ("z_max", True, True),
])
def test_reject_invalid_controls_before_transport(action, value, confirmed):
    with pytest.raises(SafetyError):
        validate_control(action, value, confirmed)


def test_jog_uses_fresh_position_and_holds_owner_lock(machine_probe):
    machine, serial, owner, _ = machine_probe
    mainboard_serial(serial)
    assert machine.mainboard_control("status")["z_mm"] == 20
    serial.z = 35.0
    original = owner._execute_acknowledged

    def locked(command, **kwargs):
        assert owner._lock._is_owned()
        return original(command, **kwargs)

    owner._execute_acknowledged = locked
    result = machine.mainboard_control("z_jog", -2, confirmed=True)
    assert result["z_mm"] == 33
    assert "G1 Z33.000 F300" in serial.writes
    assert result["max_z_mm"] == result["hard_max_z_mm"] == 80
    assert result["min_z_mm"] == 20
    assert result["fresh"] and result["available"]
    assert machine._z_probe.reference is None


@pytest.mark.parametrize(("initial", "action", "value", "maximum"), [
    (59, "z_jog", 2, 60), (60, "z_jog", .01, 60), (20, "z_jog", -.1, 60),
    (61, "z_jog", -2, 60), (58, "z", 61, 60), (20, "z", 26, 80),
    (79.9, "z_jog", .2, 80),
])
def test_configured_bounds_reject_before_z_move(initial, action, value, maximum):
    serial, owner, _, _, _ = ready_probe()
    mainboard_serial(serial)
    serial.z = initial
    with pytest.raises(SafetyError):
        control(owner, action, value, confirmed=True, guard=nullcontext,
                on_failure=lambda: None, max_z_mm=maximum)
    assert not any(line.startswith(("G1", "G28")) for line in serial.writes)


@pytest.mark.parametrize(("initial", "delta", "maximum", "target"), [
    (55, 5, 60, 60), (25, -5, 60, 20), (79.9, .1, 80, 80),
])
def test_configured_boundary_accepted(initial, delta, maximum, target):
    serial, owner, _, _, _ = ready_probe()
    mainboard_serial(serial)
    serial.z = initial
    result = control(owner, "z_jog", delta, confirmed=True, guard=nullcontext,
                     on_failure=lambda: None, max_z_mm=maximum)
    assert result["z_mm"] == target


@pytest.mark.parametrize("caps,expected", [
    ([], False), (["Cap:E3_Z_LIMIT_80_V1:1"], True),
    (["Cap:E3_Z_LIMIT_80_V1:0"], False),
    (["Cap:E3_Z_LIMIT_80_V1:1"] * 2, False),
    (["Cap:E3_Z_LIMIT_100_V1:1"], False),
])
def test_status_reports_firmware_ceiling_capability_without_claiming_physical_feedback(caps, expected):
    serial, owner, _, _, _ = ready_probe()
    mainboard_serial(serial)
    serial.overrides["M115"] = ["FIRMWARE_NAME:Marlin", CAPABILITY, *caps, "ok"]
    result = control(owner, "status", None, confirmed=False, guard=nullcontext,
                     on_failure=lambda: None, max_z_mm=60)
    assert result["firmware_z_limit_verified"] is expected
    assert result["physical_feedback"] is False
    assert result["max_z_mm"] == 60
    assert not any(entry["command"].startswith(("G1", "M106")) for entry in result["transcript"])


@pytest.mark.parametrize("change", ["hardware", "motion", "armed", "job", "unhomed", "deployed"])
def test_jog_preserves_admission_gates(machine_probe, change):
    machine, serial, _, _ = machine_probe
    state = mainboard_serial(serial)
    if change == "hardware":
        machine.hardware_enabled = False
    elif change == "motion":
        machine.settings.allow_motion = False
    elif change == "armed":
        import time
        machine._armed_until_monotonic = time.monotonic() + 60
    elif change == "job":
        machine._job.running = True
    elif change == "unhomed":
        state["known"] = False
    else:
        serial.overrides["M119"] = ["z_min: open", "ok"]
    try:
        with pytest.raises(MachineError):
            machine.mainboard_control("z_jog", 1, confirmed=True)
        assert not any(line.startswith("G1") for line in serial.writes)
    finally:
        machine._job.running = False


def test_save_ceiling_is_authoritative_atomic_and_survives_service_restart(machine_probe, tmp_path):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    path = mainboard_limits_path(tmp_path / "pi-hardware.json")
    machine._mainboard_z_limits = MainboardZLimits(path, machine.settings.air_assist.port)
    result = machine.mainboard_control("z_max", 60, confirmed=True)
    assert result["max_z_mm"] == 60 and result["max_z_persistent"]
    assert json.loads(path.read_text())["max_z_mm"] == 60
    restarted = MachineService(MachineSettings(), LaserSettings(), mainboard_limits_path=path)
    assert restarted.settings.mainboard_max_z_mm == 60
    assert machine.mainboard_control("status")["max_z_mm"] == 60
    assert not any(line.startswith(("G1", "G28", "M500")) for line in serial.writes)


def test_save_rejects_below_current_position_and_retains_old_limit(machine_probe, tmp_path):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    path = tmp_path / "z.json"
    store = machine._mainboard_z_limits = MainboardZLimits(path, "")
    store.save(80)
    serial.z = 61
    with pytest.raises(SafetyError, match="below"):
        machine.mainboard_control("z_max", 60, confirmed=True)
    assert machine.settings.mainboard_max_z_mm == store.load(80) == 80
    assert machine.status()["connected"]


def test_failed_save_does_not_change_runtime_or_previous_file(machine_probe, tmp_path, monkeypatch):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    path = tmp_path / "z.json"
    store = machine._mainboard_z_limits = MainboardZLimits(path, "")
    store.save(70)
    machine.settings.mainboard_max_z_mm = 70
    import laser_aligner.storage as storage

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(MachineError, match="Could not save"):
        machine.mainboard_control("z_max", 60, confirmed=True)
    assert machine.settings.mainboard_max_z_mm == store.load(80) == 70
    assert not list(tmp_path.glob(".z.json.*"))


def test_save_without_persistence_is_rejected_before_serial(machine_probe):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    before = list(serial.writes)
    with pytest.raises(MachineError, match="Persistent Z limits"):
        machine.mainboard_control("z_max", 60, confirmed=True)
    assert serial.writes == before


@pytest.mark.parametrize("text", [
    "not JSON", '{"schema_version":1,"controller_port":"","max_z_mm":60,"max_z_mm":80}',
    '{"schema_version":true,"controller_port":"","max_z_mm":60}',
    '{"schema_version":1,"controller_port":"other","max_z_mm":60}',
    '{"schema_version":1,"controller_port":"","max_z_mm":81}',
    '{"schema_version":1,"controller_port":"","max_z_mm":NaN}',
])
def test_invalid_saved_limit_never_falls_back_to_80(tmp_path, text):
    path = tmp_path / "z.json"
    path.write_text(text)
    with pytest.raises(MachineError, match="Invalid saved"):
        MachineService(MachineSettings(), LaserSettings(), mainboard_limits_path=path)


@pytest.mark.parametrize("maximum", [20, 60.5, 80])
def test_config_maximum_loads_and_is_public(tmp_path, maximum):
    path = tmp_path / "pi.json"
    path.write_text(json.dumps({"machine": {"mainboard_max_z_mm": maximum}}))
    settings = load_settings(path)
    assert settings.machine.mainboard_max_z_mm == maximum
    assert settings.public_dict()["machine"]["mainboard_max_z_mm"] == maximum


@pytest.mark.parametrize("maximum", [19.99, 80.01, True, "60", float("nan")])
def test_config_rejects_invalid_maximum(tmp_path, maximum):
    path = tmp_path / "pi.json"
    path.write_text(json.dumps({"machine": {"mainboard_max_z_mm": maximum}}))
    with pytest.raises(ConfigError):
        load_settings(path)


@pytest.mark.parametrize("maximum,accepted", [(24.9, False), (25, True)])
def test_native_probe_initial_lift_obeys_ceiling(machine_probe, native_probe, maximum, accepted):  # noqa: F811
    machine, _, _, _ = machine_probe
    serial, probe, fan = native_probe
    machine._z_probe = probe
    machine._secondary_air_assist = fan
    serial.z = 20
    serial.homed = True
    machine.settings.mainboard_max_z_mm = maximum
    if accepted:
        result = machine.probe_z("native_test", confirmed=True)
        assert result["clearance_z_mm"] == 20
    else:
        with pytest.raises(SafetyError, match="initial lift"):
            machine.probe_z("native_test", confirmed=True)
        assert not any(line.startswith(("G1", "G28")) for line in serial.writes)
        assert serial.writes == ["M114"]


def test_legacy_probe_clearance_above_maximum_rejects_before_motion(machine_probe):
    machine, serial, _, _ = machine_probe
    machine.settings.mainboard_max_z_mm = 25
    with pytest.raises(SafetyError, match="clearance exceeds"):
        machine.probe_z("reference", confirmed=True, clearance_z_mm=30)
    assert not any(line.startswith(("G1", "G28")) for line in serial.writes)


def test_normal_machine_status_does_not_poll_mainboard_serial(machine_probe):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    before = list(serial.writes)
    machine.status()
    assert serial.writes == before


def test_stop_interrupts_pending_z_jog_without_a_successful_height(machine_probe):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    serial.overrides["G1 Z21.000 F300"] = []
    entered = threading.Event()
    serial.on_write = lambda line: entered.set() if line == "G1 Z21.000 F300" else None
    results, errors = [], []

    def move():
        try:
            results.append(machine.mainboard_control("z_jog", 1, confirmed=True))
        except MachineError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=move)
    worker.start()
    assert entered.wait(3)
    machine.request_stop(_recover=False)
    worker.join(2)
    assert not worker.is_alive()
    assert errors and not results
    assert "M112" in serial.writes
    assert not machine._z_probe_active


@pytest.mark.parametrize("write_fails", [False, True])
def test_stop_remains_prompt_during_slow_limit_save_and_does_not_publish_stale_z(
    machine_probe, tmp_path, monkeypatch, write_fails,
):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    store = machine._mainboard_z_limits = MainboardZLimits(tmp_path / "z.json", "")
    store.save(70)
    machine.settings.mainboard_max_z_mm = 70
    import laser_aligner.machine.z_limits as limits
    original = limits.atomic_write_json
    writing, release, stopped = threading.Event(), threading.Event(), threading.Event()
    results, errors = [], []

    def slow_write(path, data):
        writing.set()
        assert release.wait(5)
        if write_fails:
            raise OSError("disk failed")
        original(path, data)

    def save():
        try:
            results.append(machine.mainboard_control("z_max", 60, confirmed=True))
        except MachineError as exc:
            errors.append(str(exc))

    def stop():
        machine.request_stop(_recover=False)
        stopped.set()

    monkeypatch.setattr(limits, "atomic_write_json", slow_write)
    writer = threading.Thread(target=save)
    stopper = threading.Thread(target=stop)
    writer.start()
    try:
        assert writing.wait(3)
        stopper.start()
        stop_was_prompt = stopped.wait(1)
    finally:
        release.set()
        writer.join(3)
        if stopper.ident is not None:
            stopper.join(3)
    assert stop_was_prompt, "STOP waited for the blocked disk write"
    assert not writer.is_alive() and not stopper.is_alive()
    assert errors and not results
    expected = 70 if write_fails else 60
    assert machine.settings.mainboard_max_z_mm == store.load(80) == expected
    assert not any(line.startswith(("G1", "G28")) for line in serial.writes)


def test_session_change_during_limit_save_cannot_return_fresh_height(machine_probe, tmp_path, monkeypatch):
    machine, serial, _, _ = machine_probe
    mainboard_serial(serial)
    store = machine._mainboard_z_limits = MainboardZLimits(tmp_path / "z.json", "")
    original_save, original_session = store.save, machine._session

    def save_then_change_session(value):
        original_save(value)
        machine._session = replace(original_session, generation=original_session.generation + 1)

    monkeypatch.setattr(store, "save", save_then_change_session)
    try:
        with pytest.raises(MachineError, match="session changed"):
            machine.mainboard_control("z_max", 60, confirmed=True)
        assert machine.settings.mainboard_max_z_mm == store.load(80) == 60
    finally:
        machine._session = original_session
