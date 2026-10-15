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
        ("z", "G1 Z30.000 F1200"), ("xy", "G0 X100 Y100 F1000"),
        ("z", "G1 Z25.000 F600"), ("xy", "M4 S100"), ("xy", "G1 X105 Y100 F500"),
    ]]
    assert indices == sorted(indices)
    lift = max(i for i, e in enumerate(events) if e == ("z", "G1 Z30.000 F1200"))
    assert indices[-1] < lift < events.index(("xy", "$H"))
    assert any(e == ("xy", "M5") for e in events[indices[-1]+1:lift])
    assert not any(line.startswith("E3FOCUS") for _, line in events)
    assert not focus.state.requires_clearance
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    assert focus.state.job_plan["id"] == plan["id"]


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
        focus.serial.on_write = lambda line: stop_async(machine) if line == "G1 Z25.000 F600" else None
    else:
        def corrupt(line):
            if line == "G1 Z25.000 F600":
                focus.serial.overrides["G1 Z30.000 F1200"] = ["Error:lift rejected", "ok"]
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


@pytest.mark.parametrize("lowered", [False, True])
def test_selected_focus_survives_clearance_home_camera_park_then_start(focus_machine, lowered):
    machine, focus, primary = focus_machine
    plan = select(machine)
    if lowered:
        surface = focus.state.surface["id"]
        machine.focus_control("jog", confirmed=True, value=-5, measurement_id=surface)
        preview = machine.focus_control("preview", confirmed=True, measurement_id=surface)["preview"]
        plan = machine.focus_control("use_job", confirmed=True, preview_id=preview["id"])["job_focus"]
    program = machine.preflight_program(PROGRAM)
    machine.prepare_photo_position()
    status = machine.focus_control("status")
    assert status["job_focus"]["id"] == plan["id"]
    assert status["surface"] is None  # Parking must not invent a local probe measurement.
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None
    assert focus.serial.z == 30


@pytest.mark.parametrize("change", ["stop", "secondary", "maximum", "calibration", "z_unknown", "z_position", "firmware", "home_rejected", "park_rejected"])
def test_park_does_not_restore_focus_after_changed_authority(focus_machine, monkeypatch, change):
    machine, focus, primary = focus_machine
    select(machine)
    original = primary.write_line
    def write(line):
        if ((change == "home_rejected" and line == "$H")
            or (change == "park_rejected" and line.startswith("G0 "))):
            raise MachineError("Injected Home / park controller failure")
        result = original(line)
        if line == "$H":
            if change == "stop":
                machine._stop_epoch += 1
            elif change == "secondary":
                focus.owner._generation += 1
            elif change == "maximum":
                machine.settings.mainboard_max_z_mm = 35
            elif change == "calibration":
                focus.state.calibration["focus_offset_mm"] += 1
            elif change == "z_unknown":
                focus.serial.homed = False
            elif change == "z_position":
                focus.serial.z = 20
            else:
                focus.serial.overrides["M115"] = ["changed firmware", "ok"]
        return result
    monkeypatch.setattr(primary, "write_line", write)
    with pytest.raises(MachineError):
        machine.prepare_photo_position()
    assert focus.state.job_plan is None
    assert not machine._coordinate_reference_ready


def test_laser_off_jog_after_park_retains_flat_workpiece(focus_machine):
    machine, focus, primary = focus_machine
    select(machine)
    program = machine.preflight_program(PROGRAM)
    machine.prepare_photo_position()
    machine.jog(1, 0, 300)
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    wait(machine)
    assert machine._job.error is None


@pytest.mark.parametrize("failure", ["unknown", "rejected", "stop"])
def test_manual_home_failed_lift_never_homes(focus_machine, failure):
    machine, focus, primary = focus_machine
    select(machine)
    machine.focus_control("jog", confirmed=True, value=-5, measurement_id=focus.state.surface["id"])
    if failure == "unknown":
        focus.serial.homed = False
    elif failure == "rejected":
        focus.serial.overrides["G1 Z30.000 F1200"] = ["Error:lift rejected", "ok"]
    else:
        focus.serial.on_write = lambda line: stop_async(machine) if line == "G1 Z30.000 F1200" else None
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


@pytest.mark.parametrize("focus_modes", [None, ("RASTER",), ("CUT", "RASTER", "CUT")])
def test_pi_owned_upload_retains_exact_focus_binding(focus_machine, tmp_path, monkeypatch, focus_modes):
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
        remote.connect()
        remote._refresh_once()
        if focus_modes is None:
            plan = select(remote)
            text = PROGRAM
        else:
            from tests.test_thickness_focus import automatic
            plan = automatic(remote, focus)["job_focus"]
            text = "\n".join(
                PROGRAM.replace("M5\nG0", f"M5\nE3OPFOCUS {mode}\nG0")
                for mode in focus_modes
            )
        remote.prepare_photo_position()  # Normal camera/park operation before START.
        program = remote.preflight_program(text)
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
