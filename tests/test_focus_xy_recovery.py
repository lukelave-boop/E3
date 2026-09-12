"""Recovery Home is a distinct, laser-off exception with no Z authority."""
from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.controller_dialects import MARLIN_DIALECT
from laser_aligner.machine.controller_session import ControllerState
from tests import test_laser_focus as helpers

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe


@pytest.fixture
def xy_recovery_machine(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    machine.settings.photo_x, machine.settings.photo_y = 15., 195.
    machine.settings.mainboard_max_z_mm = 40.
    machine._invalidate_coordinate_reference()
    with machine._lock:
        machine._set_controller_state_locked(
            ControllerState.READY_HOME_REQUIRED, session=machine._session,
        )
    focus.state.requires_clearance = True
    focus.serial.homed, focus.serial.z = True, 30.
    writes = []
    original = primary.write_line

    def write(line):
        writes.append(line)
        return original(line)

    monkeypatch.setattr(primary, "write_line", write)
    return machine, focus, primary, writes


def assert_no_primary_motion(writes):
    assert not any(line.startswith(("$H", "G0 ", "G1 ")) for line in writes)


def assert_no_focus_authority(machine, focus):
    assert focus.state.requires_clearance
    assert focus.state.reference is focus.state.surface is focus.state.preview is None
    assert focus.state.xy_sequence is None
    assert not machine.armed


@pytest.mark.parametrize("known,z", [(True, 0.), (True, 6.), (True, 30.), (True, 40.),
                                   (False, -.25), (False, .03), (False, .25)])
def test_recovery_home_verifies_ender_without_z_motion_and_retains_clearance(xy_recovery_machine, known, z):
    machine, focus, primary, writes = xy_recovery_machine
    focus.serial.homed, focus.serial.z = known, z
    old_generation = focus.owner.generation
    result = machine.focus_control("recover_xy", confirmed=True)
    assert result["action"] == "recover_xy"
    assert result["requires_clearance"] and not result["reference_ready"]
    assert result["surface"] is result["preview"] is None
    assert result["current_readback"] == {"z_mm": z, "z_known": known, "fresh": True}
    assert machine.controller_state is ControllerState.READY_MOTION
    assert machine._coordinate_reference_ready and machine._jog_position_mm == (15., 195.)
    assert (primary.x, primary.y) == (15., 195.)
    assert writes.count("$H") == 1
    assert any(line.startswith("G0 X15.000 Y195.000") for line in writes)
    assert writes.index("M5") < writes.index("$H")
    assert set(focus.serial.writes) <= {"M115", "M123", "M114", "M119"}
    for command in ("M115", "M123", "M114", "M119"):
        assert focus.serial.writes.count(command) >= 2
    assert focus.serial.z == z and focus.owner.generation == old_generation
    assert focus.serial.close_calls == 0
    assert_no_focus_authority(machine, focus)


@pytest.mark.parametrize("known,z", [(True, 25.), (False, 0.)])
def test_recovery_accepts_minimum_native_reference_ceiling(xy_recovery_machine, known, z):
    machine, focus, _, writes = xy_recovery_machine
    machine.settings.mainboard_max_z_mm = 25.
    focus.serial.homed, focus.serial.z = known, z
    result = machine.focus_control("recover_xy", confirmed=True, clearance_z_mm=25.)
    assert result["current_readback"] == {"z_mm": z, "z_known": known, "fresh": True}
    assert writes.count("$H") == 1
    assert set(focus.serial.writes) <= {"M115", "M123", "M114", "M119"}
    assert_no_focus_authority(machine, focus)


@pytest.mark.parametrize("failure", ["confirmation", "nonbool_confirmation", "hardware", "motion", "armed", "job",
                                   "already_homed", "no_clearance_restriction", "not_serial", "not_grbl",
                                   "home_disabled", "ender_closed", "missing_ender", "same_controller", "network"])
def test_recovery_home_admission_rejects_before_motion(xy_recovery_machine, failure):
    machine, focus, _, writes = xy_recovery_machine
    kwargs = {"confirmed": True}
    if failure == "confirmation":
        kwargs["confirmed"] = False
    elif failure == "nonbool_confirmation":
        kwargs["confirmed"] = 1
    elif failure == "hardware":
        machine.hardware_enabled = False
    elif failure == "motion":
        machine.settings.allow_motion = False
    elif failure == "armed":
        machine._armed_until_monotonic = time.monotonic() + 60
    elif failure == "job":
        machine._job.running = True
    elif failure == "already_homed":
        machine._controller_state = ControllerState.READY_MOTION
    elif failure == "no_clearance_restriction":
        focus.state.requires_clearance = False
    elif failure == "not_serial":
        machine.settings.backend = "simulator"
    elif failure == "not_grbl":
        machine._session = replace(machine._session, dialect=MARLIN_DIALECT)
        machine._dialect = MARLIN_DIALECT
    elif failure == "home_disabled":
        machine.settings.home_before_photo = False
    elif failure == "ender_closed":
        focus.owner.close()
    elif failure == "missing_ender":
        machine._z_probe = None
    elif failure == "same_controller":
        machine._session = replace(machine._session, resolved_endpoint=focus.owner.session.port)
    else:
        kwargs["_connection_alive"] = lambda: False
    try:
        with pytest.raises(MachineError):
            machine.focus_control("recover_xy", **kwargs)
        assert_no_primary_motion(writes)
        assert not any(line.startswith("G") for line in focus.serial.writes)
    finally:
        machine._job.running = False


@pytest.mark.parametrize("failure", ["unknown_nonzero", "unknown_negative", "known_negative", "above_maximum",
                                   "missing_position", "duplicate_position", "missing_board", "unknown_flag_missing",
                                   "unknown_flag_true", "known_flag_false", "duplicate_flag", "unstowed", "duplicate_stowed",
                                   "firmware", "low_maximum", "clearance_above_maximum"])
def test_recovery_home_preflight_rejects_untrusted_ender_evidence(xy_recovery_machine, failure):
    machine, focus, _, writes = xy_recovery_machine
    kwargs = {"confirmed": True}
    if failure in {"unknown_nonzero", "unknown_negative"}:
        focus.serial.homed = False
        focus.serial.z = 6. if failure == "unknown_nonzero" else -.251
    elif failure == "known_negative":
        focus.serial.z = -.001
    elif failure == "above_maximum":
        focus.serial.z = 40.001
    elif failure == "missing_position":
        focus.serial.overrides["M114"] = ["ok"]
    elif failure == "duplicate_position":
        focus.serial.overrides["M114"] = ["X:0 Y:0 Z:30", "X:0 Y:0 Z:30", "ok"]
    elif failure == "missing_board":
        focus.serial.overrides["M123"] = ["ok"]
    elif failure in {"unknown_flag_missing", "unknown_flag_true"}:
        focus.serial.homed, focus.serial.z = False, .03
        focus.serial.overrides["M119"] = ["z_min: TRIGGERED", "ok"]
        if failure == "unknown_flag_true":
            focus.serial.overrides["M119"].insert(1, "test_axis_known_z_flag = true")
    elif failure == "known_flag_false":
        focus.serial.overrides["M119"] = ["z_min: TRIGGERED", "test_axis_known_z_flag = false", "ok"]
    elif failure == "duplicate_flag":
        focus.serial.overrides["M119"] = ["z_min: TRIGGERED", "test_axis_known_z_flag = true",
                                          "test_axis_known_z_flag = true", "ok"]
    elif failure == "unstowed":
        focus.serial.overrides["M119"] = ["z_min: open", "test_axis_known_z_flag = true", "ok"]
    elif failure == "duplicate_stowed":
        focus.serial.overrides["M119"] = ["z_min: TRIGGERED", "z_min: TRIGGERED",
                                          "test_axis_known_z_flag = true", "ok"]
    elif failure == "firmware":
        focus.serial.overrides["M115"] = [line for line in helpers.IDENTITY if line != helpers.CAPABILITY]
    elif failure == "low_maximum":
        machine.settings.mainboard_max_z_mm = 24.
        focus.serial.z = 20.
        kwargs["clearance_z_mm"] = 24.
    else:
        kwargs["clearance_z_mm"] = 41.
    with pytest.raises(MachineError):
        machine.focus_control("recover_xy", **kwargs)
    assert_no_primary_motion(writes)
    assert not any(line.startswith("G") for line in focus.serial.writes)
    assert_no_focus_authority(machine, focus)


@pytest.mark.parametrize("operation", ["home", "job_home", "jog", "arm", "job"])
def test_successful_recovery_keeps_ordinary_motion_and_output_blocked(xy_recovery_machine, operation):
    machine, focus, _, writes = xy_recovery_machine
    machine.focus_control("recover_xy", confirmed=True)
    writes.clear()
    with pytest.raises(SafetyError, match="clearance"):
        if operation == "home":
            machine.prepare_photo_position()
        elif operation == "job_home":
            machine.prepare_job_start()
        elif operation == "jog":
            machine.jog(1, 1, 300)
        elif operation == "arm":
            machine.arm(machine.ARM_PHRASE)
        else:
            machine._start_validated_program(None, "test", start_stop_epoch=machine.operation_generation())
    assert_no_primary_motion(writes)
    assert_no_focus_authority(machine, focus)


@pytest.mark.parametrize("failure", ["position", "known_flag", "firmware", "unstowed"])
def test_recovery_home_requires_fresh_unchanged_ender_after_parking(xy_recovery_machine, monkeypatch, failure):
    machine, focus, primary, writes = xy_recovery_machine
    original = primary.write_line

    def changed(line):
        result = original(line)
        if line.startswith("G0 "):
            if failure == "position":
                focus.serial.z += 1
            elif failure == "known_flag":
                focus.serial.homed = False
            elif failure == "firmware":
                focus.serial.overrides["M115"] = [line.replace("2.0.8.24F4", "2.0.8.99F4") for line in helpers.IDENTITY]
            else:
                focus.serial.overrides["M119"] = ["z_min: open", "test_axis_known_z_flag = true", "ok"]
        return result

    monkeypatch.setattr(primary, "write_line", changed)
    with pytest.raises(MachineError):
        machine.focus_control("recover_xy", confirmed=True)
    assert writes.count("$H") == 1
    assert not machine._coordinate_reference_ready
    assert_no_focus_authority(machine, focus)
    assert not any(line.startswith(("G1 ", "G28 ", "G39 ")) for line in focus.serial.writes)


@pytest.mark.parametrize("phase", ["preflight", "home", "final_readback"])
@pytest.mark.parametrize("cancel", ["stop", "network", "primary_generation", "secondary_generation"])
def test_recovery_home_cancellation_never_publishes_authority(xy_recovery_machine, monkeypatch, phase, cancel):
    machine, focus, _, writes = xy_recovery_machine
    alive = [True]
    triggered = []
    original_ender = focus.owner._execute_acknowledged
    original_read = machine._read_session_line

    def invalidate():
        if triggered:
            return
        triggered.append(True)
        if cancel == "stop":
            machine.request_stop(_recover=False)
        elif cancel == "network":
            alive[0] = False
        elif cancel == "primary_generation":
            machine._session = replace(machine._session, generation=machine._session.generation + 1)
        else:
            focus.owner._generation += 1

    def ender(command, **kwargs):
        response = original_ender(command, **kwargs)
        if command == "M119":
            count = focus.serial.writes.count("M119")
            if (phase == "preflight" and count == 1) or (phase == "final_readback" and count == 2):
                invalidate()
        return response

    def primary_read(session, **kwargs):
        response = original_read(session, **kwargs)
        if phase == "home" and writes and writes[-1] == "$H" and response == "ok":
            invalidate()
        return response

    monkeypatch.setattr(focus.owner, "_execute_acknowledged", ender)
    monkeypatch.setattr(machine, "_read_session_line", primary_read)
    with pytest.raises(MachineError):
        machine.focus_control("recover_xy", confirmed=True, _connection_alive=lambda: alive[0])
    assert triggered
    if phase == "preflight":
        assert_no_primary_motion(writes)
    else:
        assert writes.count("$H") == 1
    assert not machine._coordinate_reference_ready
    assert_no_focus_authority(machine, focus)
    assert not any(line.startswith(("G1 ", "G28 ", "G39 ")) for line in focus.serial.writes)


def test_failed_g39_stop_reconnect_recovery_home_then_separate_reference(focus_machine):
    machine, focus, primary = focus_machine
    machine.settings.mainboard_max_z_mm = 40.
    machine.focus_control("reference", confirmed=True)
    focus.serial.overrides["G39 C30.000 H15.000"] = ["Error:G39 contact failed", "ok"]
    stop_epoch = machine.operation_generation()
    with pytest.raises(MachineError):
        machine.focus_control("measure", confirmed=True)
    assert machine.operation_generation() > stop_epoch
    assert not machine.connected and focus.state.requires_clearance
    machine.disconnect()
    fresh = helpers.FocusSerial()
    focus.owner._serial_factory = lambda *_: fresh
    machine.connect()
    assert machine.controller_state is ControllerState.READY_HOME_REQUIRED
    machine.focus_control("recover", confirmed=True)
    assert focus.owner.ready and not machine._coordinate_reference_ready
    before = (primary.x, primary.y)
    with pytest.raises(SafetyError, match="clearance"):
        machine.prepare_photo_position()
    assert (primary.x, primary.y) == before
    fresh.writes.clear()
    result = machine.focus_control("recover_xy", confirmed=True)
    assert result["requires_clearance"] and not result["reference_ready"]
    assert set(fresh.writes) <= {"M115", "M123", "M114", "M119"}
    assert fresh.z == .03 and not fresh.homed
    assert_no_focus_authority(machine, focus)
    fresh.writes.clear()
    result = machine.focus_control("reference", confirmed=True)
    assert result["reference_ready"] and not result["requires_clearance"]
    assert result["surface"] is result["preview"] is None
    assert fresh.writes.count("G28 Z R0") == 1
    assert fresh.writes.count("G39 C20.000 H5.000") == 1
    assert fresh.z == 30. and not machine.armed


@pytest.mark.parametrize("phase", ["home_ack", "park_barrier"])
@pytest.mark.parametrize("cancel", ["stop", "network", "primary_generation", "secondary_generation"])
def test_recovery_cancels_pending_primary_response_promptly(xy_recovery_machine, monkeypatch, phase, cancel):
    machine, focus, primary, writes = xy_recovery_machine
    entered = threading.Event()
    alive, errors, results = [True], [], []
    original = primary.write_line
    barrier = machine._session.dialect.motion_barrier_command

    def write(line):
        pending_home = phase == "home_ack" and line == "$H"
        pending_barrier = (phase == "park_barrier" and line == barrier
                           and any(command.startswith("G0 ") for command in writes))
        if pending_home or pending_barrier:
            writes.append(line)
            entered.set()
            return None
        return original(line)

    def recover():
        try:
            results.append(machine.focus_control("recover_xy", confirmed=True, _connection_alive=lambda: alive[0]))
        except MachineError as exc:
            errors.append(str(exc))

    monkeypatch.setattr(primary, "write_line", write)
    worker = threading.Thread(target=recover, daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        started = time.monotonic()
        if cancel == "stop":
            machine.request_stop(_recover=False)
        elif cancel == "network":
            alive[0] = False
        elif cancel == "primary_generation":
            machine._session = replace(machine._session, generation=machine._session.generation + 1)
        else:
            focus.owner._generation += 1
        worker.join(3)
        assert time.monotonic() - started < 3
        assert not worker.is_alive() and errors and not results
        assert not machine._coordinate_reference_ready
        assert_no_focus_authority(machine, focus)
        assert not any(line.startswith(("G1 ", "G28 ", "G39 ")) for line in focus.serial.writes)
    finally:
        if worker.is_alive():
            machine.request_stop(_recover=False)
            worker.join(3)


@pytest.mark.parametrize("operation", ["clearance", "mainboard_z", "mainboard_jog"])
def test_known_z_recovery_requires_reference_before_any_z_positioning(xy_recovery_machine, operation):
    machine, focus, _, writes = xy_recovery_machine
    machine.focus_control("recover_xy", confirmed=True)
    writes.clear()
    focus.serial.writes.clear()
    with pytest.raises(MachineError):
        if operation == "clearance":
            machine.focus_control("clearance", confirmed=True)
        elif operation == "mainboard_z":
            machine.mainboard_control("z", 30., confirmed=True)
        else:
            machine.mainboard_control("z_jog", 1., confirmed=True)
    assert_no_primary_motion(writes)
    assert not any(line.startswith(("G1 ", "G28 ", "G39 ")) for line in focus.serial.writes)
    assert_no_focus_authority(machine, focus)
    result = machine.focus_control("reference", confirmed=True)
    assert result["reference_ready"] and not result["requires_clearance"]
    assert not focus.state.xy_recovery_pending_reference


@pytest.mark.parametrize("invalidate", ["status", "clear_surface", "forget", "stop", "failed_reference"])
def test_recovery_reference_requirement_survives_invalidation(xy_recovery_machine, invalidate):
    machine, focus, _, _ = xy_recovery_machine
    machine.focus_control("recover_xy", confirmed=True)
    assert focus.state.xy_recovery_pending_reference
    if invalidate == "stop":
        machine.request_stop(_recover=False)
    elif invalidate == "failed_reference":
        focus.serial.overrides["G39 C20.000 H5.000"] = ["Error:G39 contact failed", "ok"]
        with pytest.raises(MachineError):
            machine.focus_control("reference", confirmed=True)
    else:
        machine.focus_control(invalidate, confirmed=invalidate != "status")
    assert focus.state.xy_recovery_pending_reference
    assert_no_focus_authority(machine, focus)
