import socket
import threading
import uuid
from contextlib import nullcontext

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.pi_job_protocol import authenticate_client
from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_PROBE_Z
from laser_aligner.machine.z_probe import CrealityZProbe
from tests import test_pi_machine_server as helpers
from tests.test_secondary_controller import FakeSerial, _controller

server_harness = helpers.server_harness


class NativeSerial(FakeSerial):
    def __init__(self):
        super().__init__()
        self.z = .03
        self.relative = False
        self.homed = False
        self.overrides = {}

    def write_line(self, line):
        super().write_line(line)
        if line in self.overrides:
            self.responses.extend(self.overrides[line])
            return
        if line == "M115":
            self.responses.append("FIRMWARE_NAME:Marlin 2.0.8.26F4 MACHINE_TYPE:Ender-3 S1 Pro")
        elif line == "M119":
            self.responses.extend(["z_min: TRIGGERED", f"test_axis_known_z_flag = {str(self.homed).lower()}"])
        elif line == "M114":
            self.responses.append(f"X:150 Y:150 Z:{self.z:.3f} E:0 Count X:0 Y:0 Z:0")
        elif line == "G91":
            self.relative = True
        elif line == "G90":
            self.relative = False
        elif line == "G1 Z20.000 F300":
            self.z = self.z + 20 if self.relative else 20
        elif line == "G28 Z R0":
            self.z = 5
            self.homed = True
        elif line not in {"M106 S0", "G21", "M84 S0", "M400", "M420 S0", "M112"}:
            raise AssertionError(f"Unexpected native test command {line}")
        self.responses.append("ok")


@pytest.fixture
def native_probe():
    serial = NativeSerial()
    owner, fan = _controller([serial])
    fan.initialize_off()
    serial.writes.clear()
    return serial, CrealityZProbe(owner, lambda: None), fan


def run(probe):
    return probe.native_cycle_test(guard=nullcontext)


def test_one_native_cycle_lifts_before_homing_then_clears_without_height_authority(native_probe):
    serial, probe, _ = native_probe
    result = run(probe)
    assert serial.writes == [
        "M115", "M119", "G21", "G90", "M114", "M84 S0", "G91",
        "G1 Z20.000 F300", "G90", "M400", "M114", "G28 Z R0", "M420 S0",
        "M114", "M119", "G1 Z20.000 F300", "M400", "M114",
    ]
    assert serial.z == 20 and not serial.relative
    assert result["kind"] == "native_cycle_test"
    assert result["reference_ready"] is False
    assert "surface_height_mm" not in result
    assert probe.reference is None
    assert serial.open_calls == 1


@pytest.mark.parametrize(("command", "response"), [
    ("M115", ["other firmware", "ok"]),
    ("M119", ["z_min: open", "ok"]),
    ("M119", ["z_min: TRIGGERED", "z_min: TRIGGERED", "ok"]),
    ("M114", ["X:0 Y:0 Z:30", "ok"]),
    ("M114", ["ok"]),
])
def test_bad_initial_state_never_moves(native_probe, command, response):
    serial, probe, _ = native_probe
    serial.overrides[command] = response
    with pytest.raises(MachineError):
        run(probe)
    assert not any(line.startswith(("G1 ", "G28")) for line in serial.writes)


def test_failed_initial_lift_never_homes(native_probe):
    serial, probe, _ = native_probe
    serial.overrides["G1 Z20.000 F300"] = ["ok"]
    with pytest.raises(MachineError, match="Initial upward clearance"):
        run(probe)
    assert "G28 Z R0" not in serial.writes
    assert serial.writes.count("G1 Z20.000 F300") == 1


@pytest.mark.parametrize("response", [["ok"], ["Error:Homing failed"], ["start"]])
def test_failed_native_homing_never_retries_or_commands_final_lift(native_probe, response):
    serial, probe, _ = native_probe
    serial.overrides["G28 Z R0"] = response
    with pytest.raises(MachineError):
        run(probe)
    assert serial.writes.count("G28 Z R0") == 1
    assert serial.writes.count("G1 Z20.000 F300") == 1
    assert probe.reference is None


def test_failed_final_lift_does_not_publish_success(native_probe):
    serial, probe, _ = native_probe
    serial.on_write = lambda line: serial.overrides.update({"G1 Z20.000 F300": ["ok"]}) if line == "G28 Z R0" else None
    with pytest.raises(MachineError, match="retract"):
        run(probe)
    assert probe.reference is None


@pytest.mark.parametrize("report", [
    ["z_min: open", "test_axis_known_z_flag = true", "ok"],
    ["z_min: TRIGGERED", "test_axis_known_z_flag = false", "ok"],
    ["z_min: TRIGGERED", "test_axis_known_z_flag = true", "test_axis_known_z_flag = false", "ok"],
])
def test_failed_post_home_state_prevents_final_motion(native_probe, report):
    serial, probe, _ = native_probe
    serial.on_write = lambda line: serial.overrides.update({"M119": report}) if line == "G28 Z R0" else None
    with pytest.raises(MachineError):
        run(probe)
    assert serial.writes.count("G1 Z20.000 F300") == 1


@pytest.fixture
def native_rpc(server_harness, native_probe):
    harness = server_harness
    serial, probe, fan = native_probe
    harness.machine._secondary_air_assist = fan
    harness.machine._z_probe = probe
    harness.service.connect()
    harness.service.prepare_photo_position()
    harness.transport.commands.clear()
    return harness, serial


def fields(harness, **changes):
    return dict(client_id=str(uuid.uuid4()), expected_boot_id=harness.service.boot_id,
                expected_session_generation=harness.machine.status()["controller_session_generation"],
                operation="native_test", confirmed=True, clearance_z_mm=20,
                support_height_mm=-1.5, **changes)


def test_native_rpc_is_separate_from_suspended_measurement_and_replay_is_cached(native_rpc):
    harness, serial = native_rpc
    args = fields(harness)
    request_id = str(uuid.uuid4())
    result = helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, request_id=request_id, **args)
    assert result["ok"], result
    before = list(serial.writes)
    assert helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, request_id=request_id, **args) == result
    assert serial.writes == before
    assert serial.writes.count("G28 Z R0") == 1
    assert harness.transport.commands.count("M5") == 2
    assert not any(line.startswith(("G0", "G1", "$H")) for line in harness.transport.commands)
    assert not harness.machine.status()["z_probe"]["reference_ready"]
    args["operation"] = "measure"
    assert not helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, **args)["ok"]
    assert serial.writes == before


@pytest.mark.parametrize("change", ["confirm", "motion", "hardware", "unhomed", "position", "clearance", "stale"])
def test_native_rpc_rejections_never_move(native_rpc, change):
    harness, serial = native_rpc
    args = fields(harness)
    if change == "confirm":
        args["confirmed"] = False
    elif change == "motion":
        harness.machine.settings.allow_motion = False
    elif change == "hardware":
        harness.machine.hardware_enabled = False
    elif change == "unhomed":
        harness.machine._coordinate_reference_ready = False
    elif change == "position":
        harness.machine._jog_position_mm = (20, 20)
    elif change == "clearance":
        args["clearance_z_mm"] = 30
    elif change == "stale":
        args["expected_session_generation"] = 999
    result = helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, **args)
    assert not result["ok"]
    assert not serial.writes


def test_native_disconnect_during_homing_prevents_retry_and_final_lift(native_rpc):
    harness, serial = native_rpc
    serial.overrides["G28 Z R0"] = []
    entered = threading.Event()
    serial.on_write = lambda line: entered.set() if line == "G28 Z R0" else None
    with socket.create_connection(("127.0.0.1", harness.server.bound_port), timeout=3) as sock:
        channel = authenticate_client(sock, helpers._TOKEN)
        channel.send_json({"action": ACTION_MACHINE_PROBE_Z, "request_id": str(uuid.uuid4()), **fields(harness)})
        assert entered.wait(3)
    helpers._wait_until(lambda: serial.close_calls > 0, timeout=3)
    assert serial.writes.count("G28 Z R0") == 1
    assert serial.writes.count("G1 Z20.000 F300") == 1
    assert harness.machine._z_probe.reference is None
