from __future__ import annotations

import threading
from unittest.mock import Mock

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.job_focus import binding_id, program_binding
from tests import test_laser_focus as helpers

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe

PROGRAM = "G21\nG90\nM5\nG0 X100 Y100 F1000\nM4 S100\nG1 X105 Y100 F500\nM5"


def stop_async(machine):
    threading.Thread(target=lambda: machine.request_stop(_recover=False), daemon=True).start()
    # Mark the worker cancelled synchronously without nesting STOP's write locks.
    machine._job_stop.set()


def select(machine):
    machine.focus_control("reference", confirmed=True)
    surface = machine.focus_control("measure", confirmed=True)["surface"]["id"]
    machine.focus_control("jog", confirmed=True, value=-5, measurement_id=surface)
    machine.focus_control("teach", confirmed=True, measurement_id=surface)
    machine.focus_control("clearance", confirmed=True)
    preview = machine.focus_control("preview", confirmed=True, measurement_id=surface)["preview"]
    return machine.focus_control("use_job", confirmed=True, preview_id=preview["id"])["job_focus"]


def wait(machine):
    machine._job_thread.join(8)
    assert not machine._job_thread.is_alive()


def test_job_clearance_travel_focus_cut_lift_home_order(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    plan = select(machine)
    events = []
    for device, label in ((primary, "xy"), (focus.serial, "z")):
        original = device.write_line
        def write(line, original=original, label=label):
            events.append((label, line))
            return original(line)
        monkeypatch.setattr(device, "write_line", write)
    program = machine.preflight_program(PROGRAM)
    assert program.lines[0] == "E3FOCUS " + plan["id"]
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    indices = [events.index(item) for item in [
        ("z", "G1 Z30.000 F300"), ("xy", "G0 X100 Y100 F1000"),
        ("z", "G1 Z25.000 F300"), ("xy", "M4 S100"), ("xy", "G1 X105 Y100 F500"),
    ]]
    assert indices == sorted(indices)
    lift = max(i for i, e in enumerate(events) if e == ("z", "G1 Z30.000 F300"))
    assert indices[-1] < lift < events.index(("xy", "$H"))
    assert any(e == ("xy", "M5") for e in events[indices[-1]+1:lift])
    assert not any(line.startswith("E3FOCUS") for _, line in events)
    assert not focus.state.requires_clearance
    with pytest.raises(MachineError, match="stale"):
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)


@pytest.mark.parametrize("failure", ["unknown", "firmware", "pin", "stop_descent", "failed_lift"])
def test_job_focus_failure_cannot_continue(focus_machine, monkeypatch, failure):
    machine, focus, primary = focus_machine
    select(machine)
    program = machine.preflight_program(PROGRAM)
    writes = []
    original = primary.write_line
    monkeypatch.setattr(primary, "write_line", lambda line: (writes.append(line), original(line))[1])
    if failure == "unknown":
        focus.serial.homed = False
    elif failure == "firmware":
        focus.serial.overrides["M115"] = ["wrong", "ok"]
    elif failure == "pin":
        focus.serial.overrides["M119"] = ["z_min: open", "ok"]
    elif failure == "stop_descent":
        focus.serial.on_write = lambda line: stop_async(machine) if line == "G1 Z25.000 F300" else None
    else:
        def corrupt(line):
            if line == "G1 Z25.000 F300":
                focus.serial.overrides["G1 Z30.000 F300"] = ["Error:lift rejected", "ok"]
        focus.serial.on_write = corrupt
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error
    assert "$H" not in writes
    if failure != "failed_lift":
        assert "M4 S100" not in writes
    if failure in {"stop_descent", "failed_lift"}:
        assert focus.state.requires_clearance


@pytest.mark.parametrize("change", ["surface", "calibration", "maximum", "xy", "session"])
def test_changed_binding_rejects_before_any_job_travel(focus_machine, change):
    machine, focus, primary = focus_machine
    select(machine)
    program = machine.preflight_program(PROGRAM)
    if change == "surface":
        focus.state.clear_surface()
    elif change == "calibration":
        focus.state.calibration["focus_offset_mm"] += 1
    elif change == "maximum":
        machine.settings.mainboard_max_z_mm = 35
    elif change == "xy":
        machine._jog_position_mm = (10, 10)
    else:
        focus.state.session = None
    primary.write_line = Mock(wraps=primary.write_line)
    with pytest.raises(MachineError, match="stale"):
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    assert not any(str(c.args[0]).startswith(("G0", "G1", "$H", "M4")) for c in primary.write_line.call_args_list)


def test_manual_home_lifts_before_xy(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    select(machine)
    surface = focus.state.surface["id"]
    machine.focus_control("jog", confirmed=True, value=-5, measurement_id=surface)
    home_z = []
    original = primary.write_line
    def write(line):
        if line == "$H":
            home_z.append(focus.serial.z)
        return original(line)
    monkeypatch.setattr(primary, "write_line", write)
    machine.prepare_photo_position()
    assert home_z == [30]
    assert not focus.state.requires_clearance


def test_start_from_focus_automatically_lifts_before_travel_and_arming(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    select(machine)
    surface = focus.state.surface["id"]
    machine.focus_control("jog", confirmed=True, value=-5, measurement_id=surface)
    preview = machine.focus_control("preview", confirmed=True, measurement_id=surface)["preview"]
    machine.focus_control("use_job", confirmed=True, preview_id=preview["id"])
    armed_z = []
    original = machine.arm_program
    def arm(*args, **kwargs):
        armed_z.append(focus.serial.z)
        return original(*args, **kwargs)
    monkeypatch.setattr(machine, "arm_program", arm)
    machine.start_preflighted_program(machine.preflight_program(PROGRAM), authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    assert armed_z == [30]


@pytest.mark.parametrize("failure", ["unknown", "rejected", "stop"])
def test_manual_home_failed_lift_never_homes(focus_machine, failure):
    machine, focus, primary = focus_machine
    select(machine)
    machine.focus_control("jog", confirmed=True, value=-5, measurement_id=focus.state.surface["id"])
    if failure == "unknown":
        focus.serial.homed = False
    elif failure == "rejected":
        focus.serial.overrides["G1 Z30.000 F300"] = ["Error:lift rejected", "ok"]
    else:
        focus.serial.on_write = lambda line: stop_async(machine) if line == "G1 Z30.000 F300" else None
    primary.write_line = Mock(wraps=primary.write_line)
    with pytest.raises(MachineError):
        machine.prepare_photo_position()
    assert "$H" not in [c.args[0] for c in primary.write_line.call_args_list]
    assert focus.state.requires_clearance


@pytest.mark.parametrize("line", ["E3FOCUS", "E3FOCUS nan", "E3FOCUS 00000000-0000-0000-0000-000000000000 M3 S100"])
def test_invalid_directive_rejected(line):
    with pytest.raises(MachineError):
        binding_id(line)


def test_directive_must_be_first_and_unique():
    line = "E3FOCUS 00000000-0000-0000-0000-000000000000"
    for lines in (["M5", line], [line, line]):
        with pytest.raises(MachineError):
            program_binding(lines)


def test_pi_owned_upload_retains_exact_focus_binding(focus_machine, tmp_path, monkeypatch):
    import copy
    import time

    from laser_aligner.machine.pi_job_service import PiJobService
    from laser_aligner.machine.pi_job_store import PiJobStore
    from laser_aligner.machine.pi_machine_server import PiMachineServer
    from laser_aligner.machine.remote_service import RemoteMachineService

    machine, focus, primary = focus_machine
    token = "job-focus-loopback-test-token-123456789"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    store = PiJobStore(tmp_path / "jobs")
    service = PiJobService(machine, store, watch_interval_seconds=.01)
    server = PiMachineServer(service, host="127.0.0.1", port=0, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 3
    while server._bound_port is None and time.monotonic() < deadline:
        time.sleep(.01)
    settings = copy.deepcopy(machine.settings)
    settings.port = f"e3bridge://127.0.0.1:{server.bound_port}"
    remote = RemoteMachineService(settings, copy.deepcopy(machine.laser_settings), hardware_enabled=True)
    try:
        remote._refresh_once()
        plan = select(remote)
        program = remote.preflight_program(PROGRAM)
        started = remote.start_preflighted_program(program, authorization_phrase=remote.ARM_PHRASE)
        remote.detach()
        wait(machine)
        assert machine._job.error is None
        assert focus.serial.z == 30
        assert store.read_program_bytes(started["job_id"]).startswith(("E3FOCUS " + plan["id"]).encode())
    finally:
        remote.detach()
        server.stop()
        thread.join(3)
