from __future__ import annotations

import json
import math
import threading
import time
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
        self.overrides["M106 P1 S0"] = ["ok"]
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


@pytest.mark.parametrize("initial_z", [0., 6., 20., 30.])
def test_reference_from_known_teaching_z_normalizes_at_confirmed_border(focus_machine, initial_z):
    machine, focus, primary = focus_machine
    machine.settings.mainboard_max_z_mm = 40.
    focus.serial.homed, focus.serial.z = True, initial_z
    before_xy = (primary.x, primary.y)
    result = machine.focus_control("reference", confirmed=True)
    moves = [line for line in focus.serial.writes if line.startswith(("G1 ", "G28 "))]
    if initial_z == 20:
        assert moves[0] == "G1 Z5.000 F300"
    else:
        assert moves[:2] == ["G1 Z20.000 F300", "G1 Z5.000 F300"]
    assert moves.count("G28 Z R0") == 1
    assert result["reference_ready"] and result["current_readback"]["z_mm"] == 30.
    assert not result["requires_clearance"]
    assert focus.owner.ready and focus.serial.close_calls == 0
    assert not any(line.startswith(("M112", "M999", "E3RECOVER")) for line in focus.serial.writes)
    assert (primary.x, primary.y) == before_xy


@pytest.mark.parametrize("change", ["unconfirmed", "wrong_border", "unknown_six", "unknown_twenty",
                                   "negative_z", "above_maximum", "low_ceiling", "high_clearance",
                                   "unstowed", "connection_lost"])
def test_reference_normalization_rejects_invalid_authority_without_motion(focus_machine, change):
    machine, focus, primary = focus_machine
    machine.settings.mainboard_max_z_mm = 40.
    focus.serial.homed, focus.serial.z = True, 6.
    kwargs = {"confirmed": True}
    if change == "unconfirmed":
        kwargs["confirmed"] = False
    elif change == "wrong_border":
        machine._jog_position_mm = (75., 143.)
    elif change.startswith("unknown"):
        focus.serial.homed = False
        focus.serial.z = 6. if change == "unknown_six" else 20.
    elif change == "negative_z":
        focus.serial.z = -.1
    elif change == "above_maximum":
        focus.serial.z = 40.001
    elif change == "low_ceiling":
        machine.settings.mainboard_max_z_mm = 24.
        kwargs["clearance_z_mm"] = 24.
    elif change == "high_clearance":
        kwargs["clearance_z_mm"] = 41.
    elif change == "unstowed":
        focus.serial.overrides["M119"] = ["z_min: open", "ok"]
    else:
        kwargs["_connection_alive"] = lambda: False
    before_xy = (primary.x, primary.y)
    with pytest.raises(MachineError):
        machine.focus_control("reference", **kwargs)
    assert not any(line.startswith(("G1 ", "G28 ")) for line in focus.serial.writes)
    assert focus.state.reference is None
    assert (primary.x, primary.y) == before_xy


def test_reference_normalization_verifies_initial_raise_before_native_homing(focus_machine):
    machine, focus, _ = focus_machine
    focus.serial.homed, focus.serial.z = True, 6.
    focus.serial.overrides["G1 Z20.000 F300"] = ["ok"]
    with pytest.raises(MachineError, match="movement was not confirmed"):
        machine.focus_control("reference", confirmed=True)
    assert focus.serial.writes.count("G1 Z20.000 F300") == 1
    assert "G28 Z R0" not in focus.serial.writes
    assert focus.state.reference is None and focus.state.requires_clearance


def test_stop_after_reference_precheck_prevents_normalization_move(focus_machine, monkeypatch):
    machine, focus, _ = focus_machine
    focus.serial.homed, focus.serial.z = True, 6.
    original = focus.owner._execute_acknowledged

    def execute(command, **kwargs):
        response = original(command, **kwargs)
        if command == "M119":
            machine.request_stop(_recover=False)
        return response

    monkeypatch.setattr(focus.owner, "_execute_acknowledged", execute)
    with pytest.raises(MachineError):
        machine.focus_control("reference", confirmed=True)
    assert not any(line.startswith(("G1 ", "G28 ")) for line in focus.serial.writes)
    assert focus.state.reference is None


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
        focus.serial.z = 0
    elif change == "over_ceiling":
        focus.serial.z = 80
        args["value"] = 1
    elif change == "large_jog":
        args["value"] = 5.01
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
    focus.serial.overrides["G1 Z29.000 F300"] = []
    entered = threading.Event()
    focus.serial.on_write = lambda line: entered.set() if line == "G1 Z29.000 F300" else None
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


def test_selected_probe_point_uses_offset_without_probing_and_returns_laser_to_same_point(focus_machine):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    focus.serial.writes.clear()
    result = machine.focus_control("position_probe", confirmed=True, value=[75., 150.])
    assert result["position_probe_available"]
    assert (primary.x, primary.y) == (71.698, 111.392)
    assert result["xy_sequence"] == {"laser_target_xy_mm": [75., 150.],
                                     "surface_target_xy_mm": [75., 150.], "laser_spot_offset_mm": [0., 0.],
                                     "probe_carriage_xy_mm": [71.698, 111.392], "phase": "probe"}
    assert focus.serial.z == 30
    assert result["surface"] is None
    assert not any(s.startswith(("G1", "G28", "G39", "M280")) for s in focus.serial.writes)
    measured = machine.focus_control("measure", confirmed=True)
    measurement_id = measured["surface"]["id"]
    result = machine.focus_control("align_laser", confirmed=True, measurement_id=measurement_id)
    assert (primary.x, primary.y) == (75., 150.)
    assert result["surface"]["id"] == measurement_id
    assert result["surface"]["carriage_xy_mm"] == [75., 150.]
    assert result["xy_sequence"]["phase"] == "laser"


def test_probe_can_reach_operator_camera_point_near_back_of_full_work_area(focus_machine):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    focus.serial.writes.clear()
    result = machine.focus_control("position_probe", confirmed=True, value=[80.177, 205.775])
    assert (primary.x, primary.y) == (76.875, 167.167)
    assert result["xy_sequence"]["surface_target_xy_mm"] == [80.177, 205.775]
    assert result["surface"] is None and focus.serial.z == 30
    assert not any(s.startswith(("G1", "G28", "G39", "M280")) for s in focus.serial.writes)
    measured = machine.focus_control("measure", confirmed=True)
    result = machine.focus_control("align_laser", confirmed=True, measurement_id=measured["surface"]["id"])
    assert (primary.x, primary.y) == (80.177, 205.775)
    assert result["surface"]["id"] == measured["surface"]["id"]


@pytest.mark.parametrize("endpoint", ["selected_point", "probe_carriage", "laser_return"])
def test_camera_point_near_back_still_requires_every_endpoint_inside_work_area(focus_machine, endpoint):
    machine, focus, primary = focus_machine
    if endpoint == "selected_point":
        # The probe carriage fits; the requested physical point does not.
        machine.settings.work_area.y_max = 200.
    elif endpoint == "laser_return":
        # The physical point and probe carriage fit; future laser carriage does not.
        machine.laser_settings.spot_offset_y_mm = -20.
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True,
                          value=[3.302, -20. if endpoint == "probe_carriage" else 38.608])
    before = (primary.x, primary.y)
    focus.serial.writes.clear()
    with pytest.raises(SafetyError, match="work area"):
        machine.focus_control("position_probe", confirmed=True, value=[80.177, 205.775])
    assert (primary.x, primary.y) == before
    assert focus.serial.z == 30
    assert not any(s.startswith(("G1", "G28", "G39", "M280")) for s in focus.serial.writes)


def test_new_selected_point_replaces_old_target_and_surface_only_at_clearance(focus_machine):
    machine, focus, primary = focus_machine
    align_for_measurement(machine)
    machine.focus_control("measure", confirmed=True)
    result = machine.focus_control("position_probe", confirmed=True, value=[100., 150.])
    assert result["surface"] is None and result["preview"] is None
    assert (primary.x, primary.y) == (97., 154.)
    assert result["xy_sequence"]["laser_target_xy_mm"] == [100., 150.]
    assert focus.serial.z == 30


def test_camera_target_subtracts_configured_laser_spot_offset_before_probe_offset(focus_machine):
    machine, focus, primary = focus_machine
    machine.laser_settings.spot_offset_x_mm = 10.
    machine.laser_settings.spot_offset_y_mm = -20.
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    result = machine.focus_control("position_probe", confirmed=True, value=[75., 150.])
    assert result["laser_spot_offset_mm"] == [10., -20.]
    assert (primary.x, primary.y) == (61.698, 131.392)
    assert result["xy_sequence"]["laser_target_xy_mm"] == [65., 170.]
    assert result["xy_sequence"]["surface_target_xy_mm"] == [75., 150.]
    measured = machine.focus_control("measure", confirmed=True)
    result = machine.focus_control("align_laser", confirmed=True, measurement_id=measured["surface"]["id"])
    assert (primary.x, primary.y) == (65., 170.)
    assert result["surface"]["id"] == measured["surface"]["id"]
    assert result["surface"]["carriage_xy_mm"] == [65., 170.]
    assert focus.serial.z == 30


def test_legacy_probe_alignment_remains_relative_with_nonzero_laser_spot_offset(focus_machine):
    machine, _focus, primary = focus_machine
    machine.laser_settings.spot_offset_x_mm = 10.
    machine.laser_settings.spot_offset_y_mm = -20.
    before = (primary.x, primary.y)
    result = align_for_measurement(machine)
    assert (primary.x, primary.y) == (before[0]-3, before[1]+4)
    assert result["xy_sequence"]["laser_target_xy_mm"] == list(before)
    assert result["xy_sequence"]["surface_target_xy_mm"] == [before[0]+10, before[1]-20]


def test_probe_target_rejects_future_laser_carriage_outside_bounds(focus_machine):
    machine, _focus, primary = focus_machine
    machine.laser_settings.spot_offset_x_mm = -2.
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    before = (primary.x, primary.y)
    # Physical point and probe carriage both fit, but returning the laser would not.
    target = [machine.settings.work_area.x_max-1., 150.]
    with pytest.raises(SafetyError, match="work area"):
        machine.focus_control("position_probe", confirmed=True, value=target)
    assert (primary.x, primary.y) == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "2"])
def test_probe_position_rejects_nonfinite_or_malformed_laser_spot_offset(focus_machine, value):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    machine.laser_settings.spot_offset_x_mm = value
    before = (primary.x, primary.y)
    focus.serial.writes.clear()
    with pytest.raises(MachineError):
        machine.focus_control("position_probe", confirmed=True, value=[75., 150.])
    assert (primary.x, primary.y) == before
    assert not any(s.startswith(("G1", "G28", "G39")) for s in focus.serial.writes)


def test_changed_laser_spot_offset_invalidates_measurement_and_return(focus_machine):
    machine, focus, primary = focus_machine
    align_for_measurement(machine)
    measured = machine.focus_control("measure", confirmed=True)
    machine.laser_settings.spot_offset_x_mm = 1.
    before = (primary.x, primary.y)
    with pytest.raises(SafetyError, match="measurement changed"):
        machine.focus_control("align_laser", confirmed=True, measurement_id=measured["surface"]["id"])
    assert (primary.x, primary.y) == before
    assert focus.state.xy_sequence is focus.state.surface is None


@pytest.mark.parametrize("value", [None, [0], [0, 1, 2], [True, 0], [float("nan"), 0],
                                   [0, float("inf")], [0, "2"], "1,2", [1e7, 0]])
def test_selected_probe_point_rejects_malformed_coordinates_before_serial(focus_machine, value):
    machine, focus, primary = focus_machine
    before = list(focus.serial.writes)
    position = (primary.x, primary.y)
    with pytest.raises(SafetyError):
        machine.focus_control("position_probe", confirmed=True, value=value)
    assert focus.serial.writes == before
    assert (primary.x, primary.y) == position


@pytest.mark.parametrize("change", ["target_bounds", "carriage_bounds", "rounding_bounds", "offset",
                                    "reference", "z", "clearance_latch", "confirmation", "primary_position",
                                    "primary_reference", "network", "secondary_session"])
def test_selected_probe_point_rejects_untrusted_or_unreachable_move(focus_machine, change):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    kwargs = dict(value=[75., 150.], confirmed=True)
    if change == "target_bounds":
        # Selected point is outside while its offset carriage lies inside.
        kwargs["value"] = [machine.settings.work_area.x_max + 1., 150.]
    elif change == "carriage_bounds":
        kwargs["value"] = [machine.settings.work_area.x_min + 1., 150.]
    elif change == "rounding_bounds":
        kwargs["value"] = [machine.settings.work_area.x_max + .0001, 150.]
    elif change == "offset":
        focus.state.probe_xy_offset_mm = None
    elif change == "reference":
        focus.state.reference = None
    elif change == "z":
        focus.serial.z = 29.9
    elif change == "clearance_latch":
        focus.state.requires_clearance = True
    elif change == "confirmation":
        kwargs["confirmed"] = False
    elif change == "primary_position":
        primary.x += .1
    elif change == "primary_reference":
        machine._coordinate_reference_ready = False
    elif change == "network":
        kwargs["_connection_alive"] = lambda: False
    else:
        focus.owner._generation += 1
    position = (primary.x, primary.y)
    focus.serial.writes.clear()
    with pytest.raises(MachineError):
        machine.focus_control("position_probe", **kwargs)
    assert (primary.x, primary.y) == position
    assert not any(s.startswith(("G1", "G28", "G39")) for s in focus.serial.writes)


def test_stop_interrupts_selected_probe_position_without_retaining_target(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    entered = threading.Event()
    original = primary.write_line
    def write(line):
        if line.startswith("G1 X"):
            entered.set()
            return
        original(line)
    monkeypatch.setattr(primary, "write_line", write)
    errors = []
    def position():
        try:
            machine.focus_control("position_probe", confirmed=True, value=[75., 150.])
        except MachineError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=position)
    worker.start()
    assert entered.wait(3)
    machine.request_stop(_recover=False)
    worker.join(3)
    assert errors and not worker.is_alive()
    assert focus.state.surface is focus.state.xy_sequence is None
    assert machine._jog_position_mm is None


@pytest.mark.parametrize("configured_feed", [300., 1200.])
def test_focus_xy_waits_for_delayed_planner_completion_beyond_ordinary_ack_timeout(
    focus_machine, monkeypatch, configured_feed,
):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    machine.settings.read_timeout = .1
    machine.laser_settings.travel_feed_mm_min = configured_feed
    barrier = machine._require_resolved_dialect().motion_barrier_command
    original_write = primary.write_line
    commands, timers, timeouts = [], [], {}
    original_send = machine.send_command

    def send(command, **kwargs):
        timeouts[command] = kwargs.get("timeout")
        return original_send(command, **kwargs)

    def write(command):
        commands.append(command)
        if command == barrier:
            # A planner barrier ACK arrives when the accepted move completes.
            # Keep the old F300 in one case so higher feed cannot mask this bug.
            timer = threading.Timer(.35, original_write, args=(command,))
            timer.daemon = True
            timers.append(timer)
            timer.start()
        else:
            original_write(command)

    monkeypatch.setattr(machine, "send_command", send)
    monkeypatch.setattr(primary, "write_line", write)
    try:
        result = machine.focus_control("position_probe", confirmed=True, value=[80.177, 205.775])
    finally:
        for timer in timers:
            timer.cancel()
            timer.join(1)
    assert (primary.x, primary.y) == (76.875, 167.167)
    assert result["xy_sequence"]["phase"] == "probe" and focus.serial.z == 30
    assert commands.count(barrier) == 1
    assert f"G1 X76.875 Y167.167 F{configured_feed:.3f}" in commands
    assert 5 < timeouts[barrier] < 120
    assert all(timeout is None for command, timeout in timeouts.items()
               if command in {"M5", "G21", "G90"} or command.startswith("G1 X"))


@pytest.mark.parametrize("travel,max_travel,max_work,expected", [
    (3000., 6000., 3000., 1200.),
    (700., 6000., 3000., 700.),
    (500., 500., 3000., 500.),
    (3000., 6000., 400., 400.),
])
def test_focus_xy_feed_respects_configured_and_focus_ceilings(
    focus_machine, travel, max_travel, max_work, expected,
):
    machine, _focus, _primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    machine.laser_settings.travel_feed_mm_min = travel
    machine.settings.max_travel_feed_mm_min = max_travel
    machine.settings.max_work_feed_mm_min = max_work
    result = machine.focus_control("position_probe", confirmed=True, value=[80.177, 205.775])
    moves = [entry["command"] for entry in result["transcript"] if entry["command"].startswith("G1 X")]
    assert moves == [f"G1 X76.875 Y167.167 F{expected:.3f}"]


def test_focus_xy_rejects_travel_that_cannot_fit_operation_budget_before_moving(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    machine.laser_settings.travel_feed_mm_min = 1.
    before = (primary.x, primary.y)
    original = primary.write_line
    commands = []
    def write(command):
        commands.append(command)
        original(command)
    monkeypatch.setattr(primary, "write_line", write)
    with pytest.raises(SafetyError, match="time limit"):
        machine.focus_control("position_probe", confirmed=True, value=[80.177, 205.775])
    assert (primary.x, primary.y) == before
    assert focus.serial.z == 30
    assert not any(command.startswith("G1 X") for command in commands)


@pytest.mark.parametrize("read_timeout", [2., 8.])
def test_focus_xy_reserves_final_readbacks_and_move_ack_inside_operation_deadline(
    focus_machine, monkeypatch, read_timeout,
):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    machine.settings.read_timeout = read_timeout
    before = (primary.x, primary.y)
    carriage = (76.875, 167.167)
    distance = math.dist(before, carriage)
    # A 110-second barrier budget fits with the former 6-second reserve, but
    # cannot also accommodate G1 ACK, two coordinate queries, and status readback.
    machine.laser_settings.travel_feed_mm_min = distance * 90.0 / 105.0
    original = primary.write_line
    commands = []
    def write(command):
        commands.append(command)
        original(command)
    monkeypatch.setattr(primary, "write_line", write)
    with pytest.raises(SafetyError, match="time limit"):
        machine.focus_control("position_probe", confirmed=True, value=[80.177, 205.775])
    assert (primary.x, primary.y) == before and focus.serial.z == 30
    assert not any(command.startswith("G1 X") for command in commands)


@pytest.mark.parametrize("failure", ["stop", "network", "ender", "controller_error"])
def test_focus_xy_completion_wait_is_interrupted_without_retaining_authority(
    focus_machine, monkeypatch, failure,
):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    focus.owner._mainboard_fan1_used = True
    barrier = machine._require_resolved_dialect().motion_barrier_command
    entered = threading.Event()
    alive = [True]
    original = primary.write_line
    commands, errors = [], []
    def write(command):
        commands.append(command)
        if command == barrier:
            entered.set()
            return  # No completion ACK: cancellation must interrupt this wait.
        original(command)
    monkeypatch.setattr(primary, "write_line", write)
    def position():
        try:
            machine.focus_control("position_probe", confirmed=True, value=[80.177, 205.775],
                                  _connection_alive=lambda: alive[0])
        except MachineError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=position)
    worker.start()
    assert entered.wait(3)
    if failure == "stop":
        machine.request_stop(_recover=False)
    elif failure == "network":
        alive[0] = False
    elif failure == "ender":
        focus.owner._generation += 1
    else:
        primary._queue.put("error:9")
    worker.join(3)
    assert errors and not worker.is_alive()
    assert commands.count(barrier) == 1
    assert len([s for s in commands if s.startswith("G1 X")]) == 1
    assert focus.state.surface is focus.state.xy_sequence is None
    assert machine._jog_position_mm is None
    assert "M112" not in focus.serial.writes


def test_focus_xy_missing_completion_ack_remains_bounded_and_retires_position(focus_machine, monkeypatch):
    machine, focus, primary = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    machine.settings.read_timeout = .1
    barrier = machine._require_resolved_dialect().motion_barrier_command
    original = primary.write_line
    commands = []
    def write(command):
        commands.append(command)
        if command != barrier:
            original(command)
    monkeypatch.setattr(primary, "write_line", write)
    target = [primary.x + 3.402, primary.y + 38.608]  # Only 0.1 mm of XY travel.
    started = time.monotonic()
    with pytest.raises(MachineError, match="did not acknowledge"):
        machine.focus_control("position_probe", confirmed=True, value=target)
    assert 5 <= time.monotonic() - started < 10
    assert commands.count(barrier) == 1
    assert len([s for s in commands if s.startswith("G1 X")]) == 1
    assert focus.state.surface is focus.state.xy_sequence is None
    assert machine._jog_position_mm is None
    assert "M112" not in focus.serial.writes


def test_focus_readonly_failure_retains_config_but_no_live_authority_or_enders_kill(focus_machine):
    machine, focus, _ = focus_machine
    machine.focus_control("reference", confirmed=True)
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    machine.settings.mainboard_max_z_mm = 40
    focus.state.requires_clearance = True
    focus.serial.overrides["M115"] = ["Error: serial status failed"]
    focus.serial.writes.clear()
    result = machine.focus_control("status")
    assert result["available"] is False and result["recovery_available"]
    assert result["ender"]["ready"] is False and "status failed" in result["ender"]["fault"]
    assert result["current_readback"] == {"z_mm": None, "z_known": False, "fresh": False}
    assert result["max_z_mm"] == 40 and result["probe_xy_offset_mm"] == [3.302, 38.608]
    assert result["requires_clearance"] and not result["reference_ready"]
    assert focus.state.surface is focus.state.preview is None
    assert "M112" not in focus.serial.writes and machine.status()["connected"]


def test_explicit_ender_reconnect_needs_no_motion_or_home_authority_and_preserves_clearance(focus_machine):
    machine, focus, primary = focus_machine
    machine.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    machine.settings.mainboard_max_z_mm = 40
    machine.settings.allow_motion = False
    machine._coordinate_reference_ready = False
    machine._jog_position_mm = None
    focus.state.requires_clearance = True
    focus.owner.close()
    fresh = FocusSerial()
    focus.owner._serial_factory = lambda *_: fresh
    initial_primary = (primary.x, primary.y)
    old_generation = focus.owner.generation
    result = machine.focus_control("recover", confirmed=True)
    assert result["action"] == "recover" and result["available"] and result["recovery_available"], result["ender"]
    assert result["ender"] == {"ready": True, "fault": None, "generation": focus.owner.generation,
                               "recovery_required": False}
    assert focus.owner.generation > old_generation
    assert fresh.open_calls == 1
    assert fresh.writes[:3] == ["M115", "M106 S0", "M106 P1 S0"]
    assert not any(s.startswith(("G", "M112", "M999", "M997", "BOOT", "E3RECOVER")) for s in fresh.writes)
    assert (primary.x, primary.y) == initial_primary
    assert result["max_z_mm"] == 40 and result["probe_xy_offset_mm"] == [3.302, 38.608]
    assert result["requires_clearance"] and not result["reference_ready"]


@pytest.mark.parametrize("failure", ["startup", "fan_off"])
def test_explicit_ender_reconnect_failure_does_not_retry_mutation_or_reopen(focus_machine, monkeypatch, failure):
    machine, focus, _ = focus_machine
    fresh = FocusSerial()
    opens = []
    def factory(*_):
        opens.append(True)
        return fresh
    focus.owner._serial_factory = factory
    if failure == "startup":
        def wait(*_args, **_kwargs):
            raise MachineError("Secondary Marlin readiness timed out; last: no response")
        monkeypatch.setattr("laser_aligner.machine.secondary_controller.wait_for_marlin", wait)
    else:
        fresh.overrides["M106 S0"] = ["Error: fan OFF failed"]
    result = machine.focus_control("recover", confirmed=True)
    assert not result["available"] and result["ender"]["recovery_required"]
    assert not result["current_readback"]["fresh"]
    assert len(opens) == 1 and fresh.writes.count("M106 S0") == int(failure == "fan_off")
    assert not any(s.startswith(("G", "M112", "E3RECOVER")) for s in fresh.writes)
    machine.focus_control("status")
    assert len(opens) == 1


@pytest.mark.parametrize("valid_application", [False, True])
def test_focus_recovery_resets_halted_peer_once_and_requires_fresh_application_before_off(focus_machine, valid_application):
    machine, focus, _ = focus_machine
    class HaltedFocusSerial(FocusSerial):
        halted = True

        def write_line(self, line):
            if line == "M115" and self.halted:
                self.writes.append(line)
                self.responses.extend(["E3RECOVERY:1 STATE:HALTED BOARD:0401C013 TOKEN:ABCD1234", "ok"])
            elif line == "E3RECOVER ABCD1234":
                self.writes.append(line)
                self.halted = False
                self.responses.append("E3RECOVERY:1 RESETTING")
            else:
                super().write_line(line)

    fresh = HaltedFocusSerial()
    fresh.overrides["M115"] = IDENTITY[:-1] + (["Cap:E3_RECOVERY_V1:1"] if valid_application else []) + ["ok"]
    focus.owner._serial_factory = lambda *_: fresh
    result = machine.focus_control("recover", confirmed=True)
    assert result["available"] is valid_application
    assert fresh.writes[:3] == ["M115", "E3RECOVER ABCD1234", "M115"]
    assert fresh.writes.count("E3RECOVER ABCD1234") == fresh.open_calls == 1
    assert not any(command.startswith(("G", "M112", "M997", "M999", "BOOT")) for command in fresh.writes)
    if valid_application:
        assert fresh.writes[3:5] == ["M106 S0", "M106 P1 S0"]
        assert result["current_readback"]["fresh"] and not result["reference_ready"]
    else:
        assert fresh.writes == fresh.writes[:3]
        assert "expected recovery firmware" in result["ender"]["fault"]


@pytest.mark.parametrize("failure", ["confirmation", "hardware", "armed", "job", "primary", "network"])
def test_ender_recovery_admission_never_opens_on_rejection(focus_machine, failure):
    machine, focus, _ = focus_machine
    opens = []
    focus.owner._serial_factory = lambda *_: opens.append(True)
    kwargs = {"confirmed": True}
    if failure == "confirmation":
        kwargs["confirmed"] = False
    elif failure == "hardware":
        machine.hardware_enabled = False
    elif failure == "armed":
        machine._armed_until_monotonic = time.monotonic() + 100
    elif failure == "job":
        machine._job.running = True
    elif failure == "primary":
        machine._session = None
        machine._transport = None
    else:
        kwargs["_connection_alive"] = lambda: False
    with pytest.raises(MachineError):
        machine.focus_control("recover", **kwargs)
    assert opens == []


@pytest.mark.parametrize("failure", ["stop", "network", "primary_generation"])
def test_ender_reconnect_is_cancelled_during_startup_without_off_or_motion(focus_machine, failure):
    machine, focus, _ = focus_machine
    fresh = FocusSerial()
    started = threading.Event()
    fresh.on_write = lambda line: started.set() if line == "M115" else None
    def read(timeout=1):
        time.sleep(min(timeout, .02))
        return None
    fresh.read_line = read
    focus.owner._serial_factory = lambda *_: fresh
    alive, errors, results = [True], [], []
    def recover():
        try:
            results.append(machine.focus_control("recover", confirmed=True, _connection_alive=lambda: alive[0]))
        except MachineError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=recover)
    worker.start()
    assert started.wait(3)
    assert machine.status()["z_probe"]["active"]
    if failure == "stop":
        machine.request_stop(_recover=False)
    elif failure == "network":
        alive[0] = False
    else:
        machine._session = None
    worker.join(3)
    assert errors and not results and not worker.is_alive()
    assert fresh.open_calls == 1 and not focus.owner.ready
    assert fresh.writes == ["M115"]


def test_explicit_emergency_stop_still_interrupts_idle_ender(focus_machine):
    machine, focus, _ = focus_machine
    killed = threading.Event()
    focus.serial.on_write = lambda line: killed.set() if line == "M112" else None
    machine.request_stop(emergency=True, _recover=False)
    assert killed.wait(2) and "M112" in focus.serial.writes


def test_readonly_focus_status_does_not_publish_probe_activity(focus_machine):
    machine, focus, _ = focus_machine
    entered = threading.Event()
    focus.serial.on_write = lambda line: entered.set() if line == "M115" else None
    focus.serial.overrides["M115"] = []
    results = []
    worker = threading.Thread(target=lambda: results.append(machine.focus_control("status")))
    worker.start()
    assert entered.wait(2)
    assert not machine.status()["z_probe"]["active"]
    focus.serial.responses.extend(IDENTITY)
    worker.join(2)
    assert not worker.is_alive() and results[0]["available"]


@pytest.mark.parametrize("step,feed", [(-5, 300), (-2, 300), (-1, 300), (-.5, 60), (-.1, 60), (5, 300)])
def test_teaching_approach_steps_keep_completion_and_readback(focus, step, feed):
    token = measured(focus)
    focus.serial.writes.clear()
    result = focus.run("jog", value=step, measurement_id=token)
    command = f"G1 Z{30 + step:.3f} F{feed}"
    writes = focus.serial.writes
    assert writes.count(command) == 1
    after = writes[writes.index(command) + 1:]
    assert after == ["M400", "M114", "M123", "M119"]
    assert result["current_readback"]["z_mm"] == 30 + step
    assert result["max_teaching_step_mm"] == 5
    assert all(entry["duration_seconds"] >= 0 for entry in result["transcript"])


@pytest.mark.parametrize("z,step,maximum", [(3, -5, 40), (38, 5, 40), (30, -5.001, 40), (30, 5.001, 40)])
def test_coarse_step_rejects_floor_ceiling_and_oversize_before_movement(focus, z, step, maximum):
    token = measured(focus)
    focus.serial.z = z
    focus.serial.writes.clear()
    with pytest.raises(MachineError):
        focus.run("jog", value=step, measurement_id=token, maximum=maximum)
    assert not any(line.startswith("G1 ") for line in focus.serial.writes)


@pytest.mark.parametrize("failure", ["lost_reference", "wrong_position", "deployed_pin"])
def test_coarse_step_does_not_report_success_on_bad_completion(focus, failure):
    token = measured(focus)
    def corrupt(line):
        if line.startswith("G1 Z"):
            if failure == "lost_reference":
                focus.serial.homed = False
            elif failure == "wrong_position":
                focus.serial.overrides["M114"] = ["X:0 Y:0 Z:30 E:0", "ok"]
            else:
                focus.serial.overrides["M119"] = ["z_min: open", "ok"]
    focus.serial.on_write = corrupt
    focus.serial.writes.clear()
    with pytest.raises(MachineError):
        focus.run("jog", value=-5, measurement_id=token)
    assert sum(line.startswith("G1 Z") for line in focus.serial.writes) == 1
    assert focus.state.requires_clearance



def test_teach_below_probe_contact_persists_negative_offset_and_focuses_raised_surface(focus):
    token = measured(focus)  # Raw contact Z5; independent of laser face.
    focus.serial.z = 6
    result = focus.run("jog", value=-3, measurement_id=token)
    assert result["current_readback"]["z_mm"] == 3
    assert result["focus_travel_min_z_mm"] == 0
    result = focus.run("teach", measurement_id=token)
    assert result["calibration"]["focus_offset_mm"] == -2
    assert LaserFocus(focus.state.path, "ender").calibration == result["calibration"]
    # Derived 3 mm gap would require Z below zero and remains rejected.
    with pytest.raises(MachineError):
        focus.run("preview", gap_mm=3, measurement_id=token)
    focus.run("clearance", clearance_z_mm=45)
    surface = focus.run("measure", clearance_z_mm=45)["surface"]  # Contact25.
    preview = focus.run("preview", clearance_z_mm=45, measurement_id=surface["id"])["preview"]
    assert preview["target_z_mm"] == 23
    moved = focus.run("move", clearance_z_mm=45, preview_id=preview["id"])
    assert moved["current_readback"]["z_mm"] == 23
