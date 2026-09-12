from __future__ import annotations

import json
import threading
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.laser_focus import CAPABILITY, LaserFocus, parse_geometry, validate_request
from laser_aligner.machine.z_probe import CrealityZProbe
from tests import test_machine_z_probe as helpers
from tests.test_native_probe import NativeSerial
from tests.test_secondary_controller import _controller

machine_probe = helpers.machine_probe
IDENTITY = ["FIRMWARE_NAME:Marlin 2.0.8.24F4 MACHINE_TYPE:Ender-3 S1 Pro",
            "Cap:E3_MAINBOARD_V1:1", "Cap:E3_COMPACT_F401_V1:1", CAPABILITY,
            "E3SG:2 PROBE_Z:0.000000 RETRACT:5.000000 MIN:-2 MAX:65 CEILING:80", "ok"]


class FocusSerial(NativeSerial):
    def __init__(self):
        super().__init__()
        self.contacts = [0., 5., 25.]
        self.home_z = 0
        self.overrides["M115"] = IDENTITY
        self.lose_z_after_probe = False

    def write_line(self, line):
        if line in self.overrides:
            return super().write_line(line)
        if line == "M123":
            self.writes.append(line)
            self.responses.extend([f"E3MB:1 FAN1:0 FAN2:0 Z_KNOWN:{int(self.homed)}", "ok"])
        elif line.startswith("G39 C"):
            self.writes.append(line)
            if self.on_write:
                self.on_write(line)
            contact = self.contacts.pop(0)
            self.z = max(10, contact)
            self.homed = not self.lose_z_after_probe
            self.responses.extend([f"E3MH:2 Z:{contact:.3f}", "ok"])
        elif line.startswith("G1 Z"):
            self.writes.append(line)
            if self.on_write:
                self.on_write(line)
            target = float(line.split()[1][1:])
            self.z = self.z + target if self.relative else target
            self.responses.append("ok")
        else:
            super().write_line(line)


@pytest.fixture
def focus(tmp_path):
    serial = FocusSerial()
    owner, fan = _controller([serial])
    fan.initialize_off()
    serial.writes.clear()
    state = LaserFocus(tmp_path / "focus.json", "ender")
    probe = CrealityZProbe(owner, lambda: None)

    def run(action, **kwargs):
        options = dict(confirmed=action != "status", value=None, clearance_z_mm=30., gap_mm=7.,
                       measurement_id=None, preview_id=None, maximum=80., primary_generation=1,
                       stop_epoch=0, xy=(15, 195), guard=nullcontext,
                       on_motion_start=lambda: None, on_failure=lambda: None)
        options.update(kwargs)
        with owner._lock:
            return state.execute(owner, probe, action, **options)
    return SimpleNamespace(serial=serial, owner=owner, fan=fan, state=state, probe=probe, run=run)


def measured(focus):
    focus.run("reference")
    return focus.run("measure")["surface"]["id"]


def taught(focus):
    measurement_id = measured(focus)
    for _ in range(10):
        focus.run("jog", value=-1, measurement_id=measurement_id)
    result = focus.run("teach", measurement_id=measurement_id)
    assert result["calibration"]["focus_offset_mm"] == 15
    return measurement_id


def test_teach_same_surface_and_elevated_thin_work_uses_contact_not_thickness(focus):
    measurement_id = taught(focus)
    before = list(focus.serial.writes)
    result = focus.run("preview", gap_mm=5, measurement_id=measurement_id)
    assert result["preview"]["target_z_mm"] == 18
    assert not any(s.startswith("G1") for s in focus.serial.writes[len(before):])
    moved = focus.run("move", gap_mm=5, preview_id=result["preview"]["id"])
    assert moved["current_readback"]["z_mm"] == 18
    assert moved["preview"] is None and moved["requires_clearance"]
    focus.run("clearance", clearance_z_mm=45)
    result = focus.run("measure", clearance_z_mm=45, xy=(25, 185))
    assert result["surface"]["elevation_mm"] == 25
    assert "thickness_above_honeycomb_mm" not in result["surface"]
    result = focus.run("preview", clearance_z_mm=45, gap_mm=3,
                       measurement_id=result["surface"]["id"], xy=(25, 185))
    assert result["preview"]["target_z_mm"] == 36
    focus.run("move", clearance_z_mm=45, gap_mm=3,
              preview_id=result["preview"]["id"], xy=(25, 185))
    assert focus.serial.z == 36


def test_reference_and_high_measurement_ranges(focus):
    result = focus.run("reference", clearance_z_mm=80)
    assert result["reference_ready"] and focus.serial.z == 80
    assert "G39 C20.000 H5.000" in focus.serial.writes
    focus.serial.contacts = [65]
    result = focus.run("measure", clearance_z_mm=80)
    assert result["surface"]["contact_z_mm"] == 65
    assert "G39 C80.000 H65.000" in focus.serial.writes
    assert focus.serial.z == 80


def test_rereference_from_higher_clearance_returns_via_border20(focus):
    focus.run("reference")
    focus.serial.contacts = [0]
    focus.serial.writes.clear()
    focus.run("reference")
    moves = [s for s in focus.serial.writes if s.startswith("G1")]
    assert moves[0] == "G1 Z20.000 F300"
    assert focus.serial.z == 30


@pytest.mark.parametrize("gap,target", [(7, 20), (5, 18), (3, 16)])
def test_gauge_targets_and_no_motion_on_teach_preview(focus, gap, target):
    measurement_id = taught(focus)
    before = len(focus.serial.writes)
    result = focus.run("preview", gap_mm=gap, measurement_id=measurement_id)
    assert result["preview"]["target_z_mm"] == target
    assert not any(s.startswith("G1") for s in focus.serial.writes[before:])


@pytest.mark.parametrize("change", ["measurement", "position", "gap", "clearance", "calibration", "xy", "reset"])
def test_stale_preview_never_moves(focus, change):
    measurement_id = taught(focus)
    result = focus.run("preview", measurement_id=measurement_id)
    args = dict(preview_id=result["preview"]["id"])
    if change == "measurement":
        focus.state.clear_surface()
    elif change == "position":
        focus.serial.z += 1
    elif change == "gap":
        args["gap_mm"] = 5
    elif change == "clearance":
        args["clearance_z_mm"] = 35
    elif change == "calibration":
        focus.state.calibration["id"] = "changed"
    elif change == "xy":
        args["xy"] = (25, 185)
    else:
        focus.serial.homed = False
    before = len(focus.serial.writes)
    with pytest.raises(MachineError):
        focus.run("move", **args)
    assert not any(s.startswith("G1") for s in focus.serial.writes[before:])


@pytest.mark.parametrize("change", ["below_floor", "over_ceiling", "large_jog", "wrong_id", "wrong_xy", "gap_teach"])
def test_rejected_teaching_moves_have_no_motion(focus, change):
    measurement_id = measured(focus)
    args = dict(measurement_id=measurement_id, value=-1)
    action = "jog"
    if change == "below_floor":
        focus.serial.z = 5
    elif change == "over_ceiling":
        focus.serial.z = 80
        args["value"] = 1
    elif change == "large_jog":
        args["value"] = 1.01
    elif change == "wrong_id":
        args["measurement_id"] = "x"*36
    elif change == "wrong_xy":
        args["xy"] = (20, 195)
    else:
        action = "teach"
        args.update(value=None, gap_mm=5)
    before = len(focus.serial.writes)
    with pytest.raises(MachineError):
        focus.run(action, **args)
    assert not any(s.startswith("G1") for s in focus.serial.writes[before:])


@pytest.mark.parametrize("response", [["ok"], ["E3MH:1 Z:5", "ok"], ["E3MH:2 Z:16", "ok"], ["E3MH:2 Z:5", "E3MH:2 Z:5", "ok"]])
def test_bad_contact_never_retracts_using_invalid_result(focus, response):
    focus.run("reference")
    focus.serial.overrides["G39 C30.000 H15.000"] = response
    before = len(focus.serial.writes)
    with pytest.raises(MachineError):
        focus.run("measure")
    assert not any(s.startswith("G1") for s in focus.serial.writes[before:])
    assert focus.state.requires_clearance


def test_lost_z_after_contact_does_not_move(focus):
    focus.run("reference")
    focus.serial.lose_z_after_probe = True
    before = len(focus.serial.writes)
    with pytest.raises(MachineError, match="lost"):
        focus.run("measure")
    assert not any(s.startswith("G1") for s in focus.serial.writes[before:])


def test_calibration_persists_but_never_live_authority(focus):
    taught(focus)
    restored = LaserFocus(focus.state.path, "ender")
    assert restored.calibration == focus.state.calibration
    assert restored.surface is restored.reference is restored.preview is None
    with pytest.raises(MachineError):
        LaserFocus(focus.state.path, "another-controller")


@pytest.mark.parametrize("change", ["schema", "offset", "geometry_bool", "nonfinite", "extra", "id"])
def test_malformed_calibration_rejected(focus, change):
    taught(focus)
    data = json.loads(focus.state.path.read_text())
    if change == "schema":
        data["schema_version"] = True
    elif change == "offset":
        data["calibration"]["focus_offset_mm"] += 1
    elif change == "geometry_bool":
        data["calibration"]["geometry"]["probe_z_mm"] = False
    elif change == "nonfinite":
        data["calibration"]["taught_z_mm"] = float("nan")
    elif change == "extra":
        data["calibration"]["extra"] = 1
    else:
        data["calibration"]["id"] = "a"*10000
    focus.state.path.write_text(json.dumps(data))
    with pytest.raises(MachineError):
        LaserFocus(focus.state.path, "ender")


def test_clear_and_forget_cannot_bypass_clearance_latch(focus):
    taught(focus)
    assert focus.state.requires_clearance
    focus.run("clear_surface")
    focus.run("forget")
    focus.state.invalidate()
    assert focus.state.requires_clearance
    focus.run("clearance")
    assert not focus.state.requires_clearance


def test_low_configured_maximum_still_allows_status(focus):
    result = focus.run("status", maximum=20)
    assert result["max_z_mm"] == result["clearance_z_mm"] == 20


@pytest.mark.parametrize("lines", [[], IDENTITY+[CAPABILITY], IDENTITY[:-2]+["E3SG:2 PROBE_Z:1 RETRACT:5 MIN:-2 MAX:65 CEILING:80"], IDENTITY[:-2]+["E3SG:2 PROBE_Z:0 RETRACT:6 MIN:-2 MAX:65 CEILING:80"]])
def test_geometry_requires_exact_v2_contract(lines):
    with pytest.raises(MachineError):
        parse_geometry(lines)


@pytest.mark.parametrize("action,kwargs", [("jog", {"value": True}), ("jog", {"value": float("nan")}), ("teach", {"gap_mm": 5}), ("preview", {"gap_mm": 4}), ("reference", {"clearance_z_mm": 19}), ("measure", {"confirmed": False})])
def test_strict_requests_reject(action, kwargs):
    args = dict(confirmed=True)
    args.update(kwargs)
    with pytest.raises(MachineError):
        validate_request(action, **args)


@pytest.fixture
def focus_machine(machine_probe, focus):
    machine, _, _, primary = machine_probe
    machine._secondary_air_assist = focus.fan
    machine._z_probe = focus.probe
    machine._laser_focus = focus.state
    return machine, focus, primary


def test_service_serial_owner_and_controller_limits(focus_machine):
    machine, focus, primary = focus_machine
    original = focus.owner._execute_acknowledged
    def locked(command, **kwargs):
        assert focus.owner._lock._is_owned()
        return original(command, **kwargs)
    focus.owner._execute_acknowledged = locked
    before = (primary.x, primary.y)
    machine.focus_control("reference", confirmed=True)
    result = machine.focus_control("measure", confirmed=True)
    machine.focus_control("jog", confirmed=True, value=-1, measurement_id=result["surface"]["id"])
    assert (primary.x, primary.y) == before
    assert focus.serial.z == 29
    assert not machine.armed


@pytest.mark.parametrize("action", ["jog", "home", "arm", "job"])
def test_below_clearance_blocks_other_motion_and_output(focus_machine, action):
    machine, focus, primary = focus_machine
    focus.state.requires_clearance = True
    before = (primary.x, primary.y)
    with pytest.raises(SafetyError, match="clearance"):
        if action == "jog":
            machine.jog(-1, -1, 300)
        elif action == "home":
            machine.prepare_photo_position()
        elif action == "arm":
            machine.arm(machine.ARM_PHRASE)
        else:
            machine._start_validated_program(None, "test", start_stop_epoch=machine.operation_generation())
    assert (primary.x, primary.y) == before
    assert focus.state.requires_clearance


def test_stop_during_persistence_is_prompt_and_disk_runtime_agree(focus_machine, monkeypatch):
    machine, focus, _ = focus_machine
    machine.focus_control("reference", confirmed=True)
    result = machine.focus_control("measure", confirmed=True)
    import laser_aligner.machine.laser_focus as module
    original = module.atomic_write_json
    entered, release = threading.Event(), threading.Event()
    results, errors = [], []
    def slow(path, data):
        entered.set()
        assert release.wait(5)
        original(path, data)
    monkeypatch.setattr(module, "atomic_write_json", slow)
    def teach():
        try:
            results.append(machine.focus_control("teach", confirmed=True, measurement_id=result["surface"]["id"]))
        except MachineError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=teach)
    thread.start()
    assert entered.wait(3)
    machine.request_stop(_recover=False)
    assert thread.is_alive()
    release.set()
    thread.join(3)
    assert errors and not results and not thread.is_alive()
    assert LaserFocus(focus.state.path, "ender").calibration == focus.state.calibration
    assert focus.state.reference is focus.state.surface is None


@pytest.mark.parametrize("action", ["manual_z", "pin", "legacy_probe", "xy", "home", "stop"])
def test_service_external_actions_invalidate_surface_authority(focus_machine, action):
    machine, focus, _ = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("measure", confirmed=True)
    assert focus.state.surface is not None
    if action == "manual_z":
        machine.mainboard_control("z_jog", 1, confirmed=True)
    elif action == "pin":
        machine.probe_pin("inspect")
    elif action == "legacy_probe":
        # Rejecting the legacy native cycle from Z30 is still a changed context.
        with pytest.raises(MachineError):
            machine.probe_z("native_test", confirmed=True)
    elif action == "xy":
        machine.jog(-1, -1, 300)
    elif action == "home":
        machine.prepare_photo_position()
    else:
        machine.request_stop(_recover=False)
    assert focus.state.surface is focus.state.preview is None
    assert (focus.state.reference is not None) == (action == "xy")


def test_service_stop_interrupts_pending_focus_move(focus_machine):
    machine, focus, _ = focus_machine
    machine.focus_control("reference", confirmed=True)
    result = machine.focus_control("measure", confirmed=True)
    focus.serial.overrides["G1 Z29.000 F60"] = []
    entered = threading.Event()
    focus.serial.on_write = lambda line: entered.set() if line == "G1 Z29.000 F60" else None
    results, errors = [], []
    def move():
        try:
            results.append(machine.focus_control("jog", confirmed=True, value=-1,
                                                measurement_id=result["surface"]["id"]))
        except MachineError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=move)
    worker.start()
    assert entered.wait(3)
    machine.request_stop(_recover=False)
    worker.join(3)
    assert errors and not results and not worker.is_alive()
    assert "M112" in focus.serial.writes
    assert focus.state.requires_clearance and focus.state.surface is None


@pytest.mark.parametrize("change", ["clearance", "ceiling", "network"])
def test_service_rejects_unavailable_envelope_or_connection_without_focus_motion(focus_machine, change):
    machine, focus, _ = focus_machine
    kwargs = dict(confirmed=True)
    if change == "clearance":
        kwargs["clearance_z_mm"] = 81
    elif change == "ceiling":
        machine.settings.mainboard_max_z_mm = 25
    else:
        kwargs["_connection_alive"] = lambda: False
    before = len(focus.serial.writes)
    with pytest.raises(MachineError):
        machine.focus_control("reference", **kwargs)
    assert not any(s.startswith(("G1", "G28", "G39")) for s in focus.serial.writes[before:])


def test_lowering_clearance_after_forget_does_not_release_xy_latch(focus):
    taught(focus)
    focus.run("forget")
    focus.run("clear_surface")
    with pytest.raises(SafetyError, match="at least"):
        focus.run("clearance", clearance_z_mm=20)
    assert focus.state.requires_clearance


@pytest.mark.parametrize("value", [None, [1], [1, 2, 3], [True, 0], [float("nan"), 0], [101, 0], "1,2"])
def test_xy_offset_requires_two_measured_finite_values(value):
    with pytest.raises(SafetyError):
        validate_request("set_xy_offset", confirmed=True, value=value)


def test_xy_offset_persists_without_measurement_authority(focus):
    result = focus.run("set_xy_offset", value=[-3.302, -38.608])
    assert result["probe_xy_offset_mm"] == [-3.302, -38.608]
    assert not any(s.startswith(("G1", "G28", "G39")) for s in focus.serial.writes)
    restored = LaserFocus(focus.state.path, "ender")
    assert restored.probe_xy_offset_mm == [-3.302, -38.608]
    assert restored.xy_sequence is restored.surface is restored.reference is None
    data = json.loads(focus.state.xy_path.read_text())
    data["controller_port"] = "another"
    focus.state.xy_path.write_text(json.dumps(data))
    with pytest.raises(MachineError, match="XY offset"):
        LaserFocus(focus.state.path, "ender")


def align_for_measurement(machine):
    machine.focus_control("reference", confirmed=True)
    # The measured probe lies +3,-4 from laser; fixture starts at its park.
    machine.focus_control("set_xy_offset", confirmed=True, value=[3, -4])
    return machine.focus_control("align_probe", confirmed=True)


def test_offset_transfers_measure_same_physical_point_and_preserve_only_return(focus_machine):
    machine, focus, primary = focus_machine
    before = (primary.x, primary.y)
    result = align_for_measurement(machine)
    probe_xy = (before[0]-3, before[1]+4)
    assert (primary.x, primary.y) == probe_xy
    assert result["xy_sequence"]["phase"] == "probe"
    result = machine.focus_control("measure", confirmed=True)
    measurement_id = result["surface"]["id"]
    with pytest.raises(SafetyError, match="Align the laser"):
        focus.run("jog", measurement_id=measurement_id, value=-.1, xy=probe_xy,
                  primary_generation=machine._session.generation)
    result = machine.focus_control("align_laser", confirmed=True, measurement_id=measurement_id)
    assert (primary.x, primary.y) == before
    assert result["surface"]["id"] == measurement_id
    assert result["surface"]["carriage_xy_mm"] == list(before)
    assert result["xy_sequence"]["phase"] == "laser"
    machine.focus_control("jog", confirmed=True, measurement_id=measurement_id, value=-.1)
    assert focus.serial.z == 29.9
    machine.focus_control("clearance", confirmed=True)
    machine.jog(-1, -1, 300)
    assert focus.state.surface is focus.state.xy_sequence is None


@pytest.mark.parametrize("change", ["unknown", "low_z", "out_of_bounds", "position_mismatch", "network", "unreferenced"])
def test_alignment_rejects_before_xy_motion(focus_machine, change):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3, -4])
    kwargs = {}
    if change == "unknown":
        focus.state.probe_xy_offset_mm = None
    elif change == "low_z":
        focus.serial.z = 29
    elif change == "out_of_bounds":
        machine.settings.work_area.x_min = primary.x - 1
    elif change == "position_mismatch":
        primary.x += 1
    elif change == "network":
        kwargs["_connection_alive"] = lambda: False
    else:
        focus.state.reference = None
    before = (primary.x, primary.y)
    with pytest.raises(MachineError):
        machine.focus_control("align_probe", confirmed=True, **kwargs)
    assert (primary.x, primary.y) == before
    assert focus.state.surface is focus.state.xy_sequence is None


def test_laser_alignment_requires_current_measurement_and_session(focus_machine):
    machine, focus, primary = focus_machine
    align_for_measurement(machine)
    result = machine.focus_control("measure", confirmed=True)
    before = (primary.x, primary.y)
    with pytest.raises(SafetyError, match="measurement"):
        machine.focus_control("align_laser", confirmed=True, measurement_id="x"*36)
    assert (primary.x, primary.y) == before
    assert result["surface"]["id"] != "x"*36


@pytest.mark.parametrize("failure", ["network", "position", "ender"])
def test_alignment_failure_never_preserves_measurement_or_cached_xy(focus_machine, failure, monkeypatch):
    machine, focus, primary = focus_machine
    align_for_measurement(machine)
    result = machine.focus_control("measure", confirmed=True)
    alive = [True]
    original = primary.write_line
    commands = []
    def write(line):
        commands.append(line)
        original(line)
        if line.startswith("G1 X"):
            if failure == "network":
                alive[0] = False
            elif failure == "position":
                primary.x += .2
            elif failure == "ender":
                focus.owner._generation += 1
    monkeypatch.setattr(primary, "write_line", write)
    with pytest.raises(MachineError):
        machine.focus_control("align_laser", confirmed=True,
                              measurement_id=result["surface"]["id"],
                              _connection_alive=lambda: alive[0])
    assert commands.index("M5") < next(i for i, s in enumerate(commands) if s.startswith("G1 X"))
    assert len([s for s in commands if s.startswith("G1 X")]) == 1
    assert focus.state.surface is focus.state.xy_sequence is None
    assert machine._jog_position_mm is None


def test_stop_interrupts_unacknowledged_xy_alignment(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    align_for_measurement(machine)
    result = machine.focus_control("measure", confirmed=True)
    entered = threading.Event()
    original = primary.write_line
    def write(line):
        if line.startswith("G1 X"):
            entered.set()
            return  # Controller accepts no ACK; STOP must not wait for it.
        original(line)
    monkeypatch.setattr(primary, "write_line", write)
    errors = []
    def align():
        try:
            machine.focus_control("align_laser", confirmed=True, measurement_id=result["surface"]["id"])
        except MachineError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=align)
    worker.start()
    assert entered.wait(3)
    machine.request_stop(_recover=False)
    worker.join(3)
    assert errors and not worker.is_alive()
    assert focus.state.surface is focus.state.xy_sequence is None
    assert machine._jog_position_mm is None


def test_network_cancellation_at_write_boundary_prevents_alignment_move(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3, -4])
    before = (primary.x, primary.y)
    alive = [True]
    original = machine._begin_session_transaction
    def begin(session, sequence, write):
        # Drop the monitoring connection after all coordinate checks, at the
        # physical write boundary for the move (the cached pose was cleared).
        if machine._jog_position_mm is None:
            alive[0] = False
        return original(session, sequence, write)
    monkeypatch.setattr(machine, "_begin_session_transaction", begin)
    with pytest.raises(MachineError):
        machine.focus_control("align_probe", confirmed=True, _connection_alive=lambda: alive[0])
    assert (primary.x, primary.y) == before
    assert focus.state.xy_sequence is None
