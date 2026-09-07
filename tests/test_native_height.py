from contextlib import nullcontext

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_PROBE_Z
from laser_aligner.machine.z_probe import CrealityZProbe
from tests import test_native_probe as native
from tests import test_pi_machine_server as helpers
from tests.test_secondary_controller import _controller

server_harness = helpers.server_harness
native_rpc = native.native_rpc


class HeightSerial(native.NativeSerial):
    def __init__(self):
        super().__init__()
        self.contacts = [.02, 10.52]

    def write_line(self, line):
        if line == "G30 X110 Y110 E1" and line not in self.overrides:
            self.writes.append(line)
            if self.on_write:
                self.on_write(line)
            contact = self.contacts.pop(0)
            self.z = max(5, contact + 2)
            self.responses.extend([f"Bed X:110 Y:110 Z:{contact}", "ok"])
        else:
            super().write_line(line)


@pytest.fixture
def native_probe():
    serial = HeightSerial()
    owner, fan = _controller([serial])
    fan.initialize_off()
    serial.writes.clear()
    return serial, CrealityZProbe(owner, lambda: None), fan


def reference(probe):
    return probe.native_reference(support_height_mm=-1.5, primary_generation=1,
                                  stop_epoch=0, carriage_xy_mm=(110, 110),
                                  guard=nullcontext, on_motion_start=lambda: None)


def measure(probe, xy=(110, 110), generation=1):
    return probe.native_measure(primary_generation=generation, stop_epoch=0,
                                carriage_xy_mm=xy, guard=nullcontext,
                                on_motion_start=lambda: None)


def test_border_check_then_12mm_material_preserves_origin_and_clears(native_probe):
    serial, probe, _ = native_probe
    assert reference(probe)["border_check_required"]
    assert not any(line.startswith("G30") for line in serial.writes)
    assert measure(probe)["kind"] == "native_border_check"
    serial.writes.clear()
    result = measure(probe, (90, 90))
    assert result["surface_height_mm"] == pytest.approx(10.5)
    assert result["thickness_above_honeycomb_mm"] == pytest.approx(12)
    assert result["sample_count"] == 1 and result["spread_mm"] is None
    assert serial.z == 20
    assert serial.writes.count("G30 X110 Y110 E1") == 1
    assert not any(line.startswith(("G28", "G92", "M280", "M851", "M500")) for line in serial.writes)


def test_first_contact_must_be_on_border(native_probe):
    serial, probe, _ = native_probe
    reference(probe)
    serial.writes.clear()
    with pytest.raises(MachineError, match="before jogging"):
        measure(probe, (90, 90))
    assert not serial.writes


@pytest.mark.parametrize("change", ["missing", "session", "reset", "position", "pin"])
def test_bad_start_never_probes(native_probe, change):
    serial, probe, _ = native_probe
    reference(probe)
    if change == "missing":
        probe.invalidate()
    elif change == "reset":
        serial.homed = False
    elif change == "position":
        serial.z = 10
    elif change == "pin":
        serial.overrides["M119"] = ["z_min: open", "test_axis_known_z_flag = true", "ok"]
    serial.writes.clear()
    with pytest.raises(MachineError):
        measure(probe, generation=2 if change == "session" else 1)
    assert not any(line.startswith(("G1", "G28", "G30", "M112")) for line in serial.writes)


@pytest.mark.parametrize("response", [["ok"], ["Bed X:110 Y:110 Z:16", "ok"],
                                      ["Bed X:110 Y:110 Z:-3", "ok"],
                                      ["Bed X:110 Y:110 Z:nan", "ok"]])
def test_bad_contact_never_retries_or_lifts(native_probe, response):
    serial, probe, _ = native_probe
    reference(probe)
    serial.writes.clear()
    serial.overrides["G30 X110 Y110 E1"] = response
    with pytest.raises(MachineError):
        measure(probe)
    assert serial.writes.count("G30 X110 Y110 E1") == 1
    assert not any(line.startswith("G1") for line in serial.writes)
    assert not probe.native_border_checked


def test_border_zero_mismatch_clears_but_does_not_accept(native_probe):
    serial, probe, _ = native_probe
    reference(probe)
    serial.contacts = [1]
    with pytest.raises(MachineError, match="not near homed zero"):
        measure(probe)
    assert serial.z == 20 and not probe.native_border_checked


def test_failed_final_clearance_does_not_accept_border(native_probe):
    serial, probe, _ = native_probe
    reference(probe)
    serial.overrides["G1 Z20.000 F300"] = ["ok"]
    with pytest.raises(MachineError, match="retract"):
        measure(probe)
    assert not probe.native_border_checked


def rpc(harness, operation, **changes):
    args = native.fields(harness)
    args.update(operation=operation, **changes)
    return helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, **args)


def test_service_reference_check_jog_material_and_cached_replay(native_rpc):
    harness, serial = native_rpc
    assert rpc(harness, "native_reference")["ok"]
    assert rpc(harness, "native_measure")["ok"]
    harness.machine.jog(-10, -10, 300)
    args = native.fields(harness)
    args["operation"] = "native_measure"
    request_id = "be9fccd1-20e7-4a6c-aad3-6a18fbcc8199"
    result = helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, request_id=request_id, **args)
    assert result["ok"], result
    assert result["result"]["thickness_above_honeycomb_mm"] == pytest.approx(12)
    before = list(serial.writes)
    assert helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, request_id=request_id, **args) == result
    assert serial.writes == before


@pytest.mark.parametrize("operation", ["native_reference", "native_measure"])
@pytest.mark.parametrize("change", ["confirm", "hardware", "motion", "unhomed", "stale", "clearance", "armed"])
def test_service_rejects_unauthorized_height_operations(native_rpc, operation, change):
    harness, serial = native_rpc
    assert rpc(harness, "native_reference")["ok"]
    serial.writes.clear()
    args = {}
    if change == "confirm":
        args["confirmed"] = False
    elif change == "hardware":
        harness.machine.hardware_enabled = False
    elif change == "motion":
        harness.machine.settings.allow_motion = False
    elif change == "unhomed":
        harness.machine._coordinate_reference_ready = False
    elif change == "stale":
        args["expected_session_generation"] = 999
    elif change == "clearance":
        args["clearance_z_mm"] = 30
    elif change == "armed":
        import time
        harness.machine._armed_until_monotonic = time.monotonic() + 60
        harness.machine._armed_until = time.time() + 60
    assert not rpc(harness, operation, **args)["ok"]
    assert not serial.writes


def test_service_missing_contact_invalidates_reference(native_rpc, caplog):
    harness, serial = native_rpc
    assert rpc(harness, "native_reference")["ok"]
    serial.overrides["G30 X110 Y110 E1"] = ["ok"]
    assert not rpc(harness, "native_measure")["ok"]
    assert harness.machine._z_probe.reference is None
    assert '"command": "G30 X110 Y110 E1", "responses": ["ok"]' in caplog.text


def test_primary_home_invalidates_material_reference(native_rpc):
    harness, serial = native_rpc
    assert rpc(harness, "native_reference")["ok"]
    harness.machine.prepare_photo_position()
    serial.writes.clear()
    assert not rpc(harness, "native_measure")["ok"]
    assert not any(line.startswith("G30") for line in serial.writes)


@pytest.mark.parametrize("report", [
    ["z_min: open", "test_axis_known_z_flag = true", "ok"],
    ["z_min: TRIGGERED", "test_axis_known_z_flag = false", "ok"],
])
def test_post_contact_state_failure_prevents_lift(native_probe, report):
    serial, probe, _ = native_probe
    reference(probe)
    serial.writes.clear()
    serial.on_write = lambda line: serial.overrides.update({"M119": report}) if line.startswith("G30") else None
    with pytest.raises(MachineError):
        measure(probe)
    assert not any(line.startswith("G1") for line in serial.writes)
    assert not probe.native_border_checked


@pytest.mark.parametrize("material", [False, True])
def test_disconnect_during_single_contact_prevents_retry_and_final_lift(native_rpc, material):
    import socket
    import threading
    import uuid

    from laser_aligner.machine.pi_job_protocol import authenticate_client

    harness, serial = native_rpc
    command = "G39" if material else "G30 X110 Y110 E1"
    if material:
        serial.overrides["M115"] = [
            "FIRMWARE_NAME:Marlin E3-material MACHINE_TYPE:Ender-3 S1 Pro",
            "Cap:E3_MATERIAL_HEIGHT_V1:1", "ok",
        ]
    assert rpc(harness, "native_reference")["ok"]
    serial.writes.clear()
    serial.overrides[command] = []
    entered = threading.Event()
    serial.on_write = lambda line: entered.set() if line == command else None
    args = native.fields(harness)
    args["operation"] = "native_measure"
    with socket.create_connection(("127.0.0.1", harness.server.bound_port), timeout=3) as sock:
        channel = authenticate_client(sock, helpers._TOKEN)
        channel.send_json({"action": ACTION_MACHINE_PROBE_Z, "request_id": str(uuid.uuid4()), **args})
        assert entered.wait(3)
    # The owner closes first; MachineService then unwinds and invalidates the
    # reference. Wait for that completed cleanup instead of racing its handler.
    helpers._wait_until(lambda: serial.close_calls > 0
                       and harness.machine._z_probe.reference is None
                       and not harness.machine._z_probe_active, timeout=3)
    assert serial.writes.count(command) == 1
    assert not any(line.startswith("G1") for line in serial.writes)
    assert harness.machine._z_probe.reference is None


def test_changed_support_offset_rejects_without_contact(native_rpc):
    harness, serial = native_rpc
    assert rpc(harness, "native_reference")["ok"]
    serial.writes.clear()
    assert not rpc(harness, "native_measure", support_height_mm=0)["ok"]
    assert not serial.writes
