from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.controller_session import ControllerState
from laser_aligner.machine.pi_job_service import PiJobService
from laser_aligner.machine.pi_job_store import PiJobStore
from laser_aligner.machine.service import MachineService
from laser_aligner.machine.z_retention import CAPABILITY, z_retention_path
from tests.fakes.simulator_transport import SimulatedTransport
from tests.test_job_focus import select
from tests.test_laser_focus import IDENTITY, FocusSerial
from tests.test_secondary_controller import _PORT, _controller


class RetentionSerial(FocusSerial):
    def __init__(self):
        super().__init__()
        self.overrides["M115"] = [*IDENTITY[:-1], CAPABILITY, "ok"]
        self.overrides["M17 Z"] = ["ok"]

    def write_line(self, line):
        if line.startswith("M124 Z"):
            self.writes.append(line)
            self.z, self.homed = float(line.split("Z")[1]), True
            self.responses.extend([f"E3ZR:1 Z:{self.z:.3f}", "ok"])
        else:
            super().write_line(line)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    serial = RetentionSerial()
    owner, fan = _controller([serial])
    fan.initialize_off()
    primaries = []

    def primary_factory(*args):
        primary = SimulatedTransport()
        primaries.append(primary)
        return primary

    monkeypatch.setattr("laser_aligner.machine.service.create_machine_transport", primary_factory)
    path = tmp_path / "config.json.laser-focus.json"
    machines = []

    def create():
        machine = MachineService(
            MachineSettings(
                protocol="grbl", allow_motion=True, controller_startup_delay=0,
                air_assist=AirAssistSettings(mode=AirAssistMode.SECONDARY_MARLIN_FAN,
                                           port=_PORT, baudrate=115200),
            ),
            LaserSettings(), hardware_enabled=True, secondary_air_assist=fan,
            focus_calibration_path=path,
        )
        machines.append(machine)
        return machine

    machine = create()
    machine.connect()
    machine.prepare_photo_position()
    machine.focus_control("reference", confirmed=True)
    assert serial.z == 30
    yield SimpleNamespace(machine=machine, create=create, serial=serial, owner=owner, fan=fan,
                          path=z_retention_path(path), tmp=tmp_path, primaries=primaries)
    for item in machines:
        item.request_stop(_recover=False)


def _clean(rig):
    return json.loads(rig.path.read_text())["clean"]


def test_clean_disconnect_restart_restores_z_without_probing_and_survives_xy_home(rig):
    rig.machine.disconnect()
    assert _clean(rig)
    rig.serial.z, rig.serial.homed = 0., False  # The controller powered down too.
    rig.serial.writes.clear()
    restarted = rig.create()
    assert not _clean(rig)
    restarted.connect()
    assert rig.serial.z == 30 and rig.serial.homed
    assert rig.serial.writes.count("M124 Z30.000") == 1
    assert not any(line.startswith(("G1 ", "G28", "G39")) for line in rig.serial.writes)
    assert restarted.status()["controller_state"] == "READY_HOME_REQUIRED"
    assert restarted.status()["z_retention"]["restored"]
    restarted.prepare_photo_position()
    assert restarted.focus_control("status")["reference_ready"]
    assert restarted.status()["z_retention"]["restored"]
    restarted.focus_control("measure", confirmed=True)
    assert rig.serial.writes.count("G28 Z R0") == 0


def test_live_ender_reconnect_verifies_matching_z_without_motion(rig):
    rig.machine.disconnect()
    rig.serial.writes.clear()
    rig.machine.connect()
    assert not _clean(rig)
    assert rig.machine.status()["z_retention"]["restored"]
    assert rig.serial.writes.count("M124 Z30.000") == 1
    assert not any(line.startswith(("G1 ", "G28", "G39")) for line in rig.serial.writes)


def test_later_pi_shutdown_preserves_clean_windows_disconnect_but_stop_revokes(rig):
    rig.machine.disconnect()
    saved = rig.path.read_bytes()
    service = PiJobService(rig.machine, PiJobStore(rig.tmp / "jobs"))
    service.shutdown()
    assert rig.path.read_bytes() == saved
    rig.machine.request_stop(_recover=False)
    assert not _clean(rig)


def test_idle_pi_shutdown_captures_before_stopping(rig):
    service = PiJobService(rig.machine, PiJobStore(rig.tmp / "jobs"))
    service.shutdown()
    assert _clean(rig)
    assert rig.machine.status()["controller_state"] == "SHUTTING_DOWN"


@pytest.mark.parametrize("condition", ["stop", "unknown", "lowered", "firmware", "busy", "armed", "failed_exit"])
def test_uncertain_or_nonidle_exit_never_saves_z(rig, condition):
    machine = rig.machine
    if condition == "stop":
        machine.request_stop(_recover=False)
    elif condition == "unknown":
        rig.serial.homed = False
    elif condition == "lowered":
        rig.serial.z = 25
        machine._laser_focus.requires_clearance = True
    elif condition == "firmware":
        rig.serial.overrides["M115"] = IDENTITY
    elif condition == "busy":
        machine._focus_operation_active = True
    elif condition == "armed":
        machine._armed_until_monotonic = time.monotonic() + 20
    elif condition == "failed_exit":
        service = PiJobService(machine, PiJobStore(rig.tmp / "jobs"))
        service.shutdown(clean_exit=False)
        assert not _clean(rig)
        return
    machine.disconnect()
    machine._focus_operation_active = False
    assert not _clean(rig)


def test_forget_z_is_state_only_and_requires_reference_for_focus_and_manual_z(rig):
    before = list(rig.serial.writes)
    result = rig.machine.focus_control("forget_z", confirmed=True)
    assert rig.serial.writes == before
    assert not result["reference_ready"] and result["available"] is False
    with pytest.raises(SafetyError, match="Reference"):
        rig.machine.focus_control("measure", confirmed=True)
    with pytest.raises(SafetyError, match="Reference"):
        rig.machine.mainboard_control("z_jog", 1, confirmed=True)
    assert rig.serial.z == 30
    rig.serial.contacts = [0.]
    rig.machine.focus_control("reference", confirmed=True)
    assert rig.machine.focus_control("status")["reference_ready"]


def test_stop_during_staged_checkpoint_prevents_clean_publication(rig, monkeypatch):
    store = rig.machine._z_retention
    original = store.save
    staged, release = threading.Event(), threading.Event()

    def blocked_save(snapshot, **kwargs):
        staged.set()
        assert release.wait(3)
        return original(snapshot, **kwargs)

    monkeypatch.setattr(store, "save", blocked_save)
    worker = threading.Thread(target=rig.machine.disconnect)
    worker.start()
    assert staged.wait(3)
    rig.machine.request_stop(_recover=False)
    release.set()
    worker.join(3)
    assert not worker.is_alive() and not _clean(rig)


def test_pending_checkpoint_cannot_reclaim_newer_process_session(rig):
    rig.machine.disconnect()
    pending = rig.create()
    rig.create()  # Another process took ownership after the pending snapshot.
    opened = len(rig.primaries)
    with pytest.raises(MachineError, match="another|changed|session|claim"):
        pending.connect()
    assert len(rig.primaries) == opened


def test_dirty_write_failure_prevents_controller_open(rig, monkeypatch):
    rig.machine.disconnect()
    opened = len(rig.primaries)

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("laser_aligner.machine.z_retention.atomic_write_json", fail)
    with pytest.raises(MachineError, match="invalidate"):
        rig.machine.connect()
    assert len(rig.primaries) == opened


def test_connection_override_does_not_restore_another_rig_checkpoint(rig):
    rig.machine.disconnect()
    rig.serial.z, rig.serial.homed = 0., False
    rig.serial.writes.clear()
    restarted = rig.create()
    restarted.connect(port="different-controller")
    assert not restarted.status()["z_retention"]["restored"]
    assert not any(line.startswith("M124") for line in rig.serial.writes)


def test_checkpoint_disk_failure_cannot_skip_primary_stop(rig, monkeypatch):
    epoch = rig.machine.operation_generation()
    rig.serial.homed = False

    def fail():
        raise MachineError("disk unavailable")

    monkeypatch.setattr(rig.machine._z_retention, "discard", fail)
    rig.machine.disconnect()
    assert rig.machine.operation_generation() == epoch + 1
    assert not rig.machine.connected
    assert not _clean(rig)


def test_failed_restore_report_cannot_grant_z_motion_even_if_firmware_is_known(rig, monkeypatch):
    rig.machine.disconnect()
    rig.serial.z, rig.serial.homed = 0., False
    original = rig.owner._execute_acknowledged

    def damaged_report(command, **kwargs):
        reply = original(command, **kwargs)
        return ("ok",) if command.startswith("M124") else reply

    monkeypatch.setattr(rig.owner, "_execute_acknowledged", damaged_report)
    restarted = rig.create()
    restarted.connect()
    assert rig.serial.homed and rig.serial.z == 30
    assert not restarted.status()["z_retention"]["restored"]
    restarted.prepare_photo_position()
    with pytest.raises(SafetyError, match="Reference"):
        restarted.mainboard_control("z_jog", 1, confirmed=True)
    with pytest.raises(SafetyError, match="Reference"):
        restarted.focus_control("measure", confirmed=True)


@pytest.mark.parametrize("action", ["photo", "job"])
def test_acknowledged_home_failure_cannot_rehydrate_old_z_datum(rig, monkeypatch, action):
    machine = rig.machine
    if action == "job":
        with machine._lock:
            machine._set_controller_state_locked(ControllerState.READY_HOME_REQUIRED, session=machine._session)
            machine._coordinate_reference_ready = False

    def reject_offsets(_state):
        raise SafetyError("Acknowledged Home has unexpected coordinate offsets")

    monkeypatch.setattr(machine, "_require_zero_xy_coordinate_offsets", reject_offsets)
    with pytest.raises(SafetyError, match="coordinate offsets"):
        (machine.prepare_photo_position if action == "photo" else machine.prepare_job_start)()

    assert rig.owner.ready and rig.serial.homed
    assert machine._laser_focus.retain_z_reference(rig.owner.generation) is None
    assert not machine.focus_control("status")["reference_ready"]


def test_selected_plan_clearance_verification_failure_discards_datum(rig, monkeypatch):
    rig.serial.contacts = [0., 5.]
    select(rig.machine)
    original = rig.owner._execute_acknowledged
    identities = 0

    def changed_identity(command, **kwargs):
        nonlocal identities
        reply = original(command, **kwargs)
        if command == "M115":
            identities += 1
            if identities == 2:  # Explicit clearance passed; the following verification failed.
                return ("Different firmware", "ok")
        return reply

    monkeypatch.setattr(rig.owner, "_execute_acknowledged", changed_identity)
    with pytest.raises(MachineError, match="firmware changed"):
        rig.machine.prepare_photo_position()
    assert rig.owner.ready and rig.serial.homed
    assert rig.machine._laser_focus.retain_z_reference(rig.owner.generation) is None
    assert not rig.machine.focus_control("status")["reference_ready"]


def test_reference_on_overridden_primary_cannot_save_original_rig_binding(rig):
    rig.machine.disconnect()
    rig.machine.connect(port="different-controller")
    rig.machine.prepare_photo_position()
    rig.serial.contacts = [0.]
    rig.machine.focus_control("reference", confirmed=True)
    assert rig.machine.focus_control("status")["reference_ready"]
    rig.machine.disconnect()
    assert not _clean(rig)


def test_new_primary_session_drops_leftover_datum_without_checkpoint(rig):
    machine = rig.machine
    # Model a retirement path that forgot the new datum-specific invalidation.
    with machine._lock:
        machine._session.receiver.stop()
        machine._session.transport.close()
        machine._session = None
        machine._transport = None
        machine._invalidate_coordinate_reference()
        machine._set_controller_state_locked(ControllerState.DISCONNECTED, force_terminal=True)
    assert machine._laser_focus.retain_z_reference(rig.owner.generation) is not None
    machine.connect()
    assert machine._laser_focus.retain_z_reference(rig.owner.generation) is None
    assert not machine.focus_control("status")["reference_ready"]


def test_connection_failure_cleans_candidate_even_if_later_discard_would_fail(rig, monkeypatch):
    machine = rig.machine
    machine.disconnect()
    cleanups = []
    original = machine._best_effort_fail_off

    def cleanup(transport, **kwargs):
        cleanups.append(transport)
        return original(transport, **kwargs)

    def identity_failure(**kwargs):
        raise MachineError("Identity unavailable")

    def failed_discard():
        raise MachineError("Disk unavailable")

    monkeypatch.setattr(machine, "_identify_protocol", identity_failure)
    monkeypatch.setattr(machine, "_best_effort_fail_off", cleanup)
    monkeypatch.setattr(machine._z_retention, "discard", failed_discard)
    with pytest.raises(MachineError, match="Identity unavailable"):
        machine.connect()
    assert cleanups and all(not transport.is_open for transport in cleanups)
    assert not _clean(rig)
