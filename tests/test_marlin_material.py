from contextlib import nullcontext

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.z_probe import (
    CrealityZProbe,
    material_height_supported,
    parse_material_contact,
)
from tests import test_native_probe as native
from tests import test_pi_machine_server as helpers
from tests.test_native_height import HeightSerial, measure, reference, rpc
from tests.test_secondary_controller import _controller

CAPABILITY = "Cap:E3_MATERIAL_HEIGHT_V1:1"
IDENTITY = "FIRMWARE_NAME:Marlin E3-material MACHINE_TYPE:Ender-3 S1 Pro"
native_rpc = native.native_rpc
native_probe = native.native_probe
server_harness = helpers.server_harness


class MaterialSerial(HeightSerial):
    def __init__(self):
        super().__init__()
        self.overrides["M115"] = [IDENTITY, CAPABILITY, "ok"]

    def write_line(self, line):
        if line == "G39" and line not in self.overrides:
            self.writes.append(line)
            if self.on_write:
                self.on_write(line)
            contact = self.contacts.pop(0)
            self.z = max(10, contact + 5)
            self.responses.extend([f"E3MH:1 Z:{contact}", "ok"])
        else:
            super().write_line(line)


@pytest.fixture
def material_probe():
    serial = MaterialSerial()
    serial.contacts = [0, 10.5]
    owner, fan = _controller([serial])
    fan.initialize_off()
    serial.writes.clear()
    return serial, CrealityZProbe(owner, lambda: None)


@pytest.mark.parametrize("firmware", [
    "Cap:E3_MATERIAL_HEIGHT_V1:0", "Cap:E3_MATERIAL_HEIGHT_V2:1",
    CAPABILITY + " " + CAPABILITY, CAPABILITY + "junk",
])
def test_unsupported_capabilities_rejected(firmware):
    with pytest.raises(MachineError):
        material_height_supported(firmware)


def test_stock_and_exact_capability():
    assert not material_height_supported(IDENTITY)
    assert material_height_supported(IDENTITY + " " + CAPABILITY)


@pytest.mark.parametrize("lines", [
    ["ok"], ["Bed X:110 Y:110 Z:5.5", "ok"], ["E3MH:2 Z:5.5"],
    ["E3MH:1 Z:nan"], ["E3MH:1 Z:inf"], ["E3MH:1 Z:1e999"],
    ["E3MH:1 Z:10.501"], ["E3MH:1 Z:-2.001"],
    ["E3MH:1 Z:1", "E3MH:1 Z:2"], ["E3MH:1 Z:1", "E3MH:1 BROKEN"],
])
def test_bad_result_rejected(lines):
    with pytest.raises(MachineError):
        parse_material_contact(tuple(lines))


@pytest.mark.parametrize("height", [-2, 0, 5.5, 10.5])
def test_result_boundaries(height):
    assert parse_material_contact((f"E3MH:1 Z:{height}", "ok")) == height


def test_material_cycle_preserves_reference_and_returns_to_clearance(material_probe):
    serial, probe = material_probe
    reference(probe)
    measure(probe)
    serial.writes.clear()
    result = measure(probe, (90, 90))
    assert result["thickness_above_honeycomb_mm"] == pytest.approx(12)
    assert serial.writes.count("G39") == 1
    assert "M115" in serial.writes
    assert not any(s.startswith(("G30", "G28", "G92", "M280")) for s in serial.writes)
    assert serial.z == 20


@pytest.mark.parametrize("identity", [
    [IDENTITY, "ok"], [IDENTITY, "Cap:E3_MATERIAL_HEIGHT_V2:1", "ok"],
    [IDENTITY + " changed", CAPABILITY, "ok"],
])
def test_changed_firmware_rejects_before_motion(material_probe, identity):
    serial, probe = material_probe
    reference(probe)
    serial.writes.clear()
    serial.overrides["M115"] = identity
    with pytest.raises(MachineError):
        measure(probe)
    assert serial.writes == ["M115"]
    assert not probe.native_motion_started


@pytest.mark.parametrize("response", [
    ["ok"], ["E3MH:1 Z:13", "ok"], ["Error:E3MH:1 PROBE_FAILED", "ok"],
])
def test_failed_material_never_falls_back_or_lifts(material_probe, response):
    serial, probe = material_probe
    reference(probe)
    serial.writes.clear()
    serial.overrides["G39"] = response
    with pytest.raises(MachineError):
        measure(probe)
    assert serial.writes.count("G39") == 1
    assert not any(s.startswith(("G1 ", "G30")) for s in serial.writes)


def test_service_g39_failure_invalidates_reference(native_rpc):
    harness, serial = native_rpc
    serial.overrides["M115"] = [IDENTITY, CAPABILITY, "ok"]
    serial.overrides["G39"] = ["ok"]
    assert rpc(harness, "native_reference")["ok"]
    assert not rpc(harness, "native_measure")["ok"]
    assert harness.machine._z_probe.reference is None
    assert serial.writes.count("G39") == 1
    assert not any(s.startswith("G30") for s in serial.writes)


def test_service_material_success_and_cached_replay(native_rpc):
    harness, serial = native_rpc
    serial.overrides["M115"] = [IDENTITY, CAPABILITY, "ok"]
    serial.overrides["G39"] = ["E3MH:1 Z:0", "ok"]
    assert rpc(harness, "native_reference")["ok"]
    assert rpc(harness, "native_measure")["ok"]
    serial.overrides["G39"] = ["E3MH:1 Z:5.5", "ok"]
    args = native.fields(harness)
    args["operation"] = "native_measure"
    request_id = "9deabf57-bba8-4e15-a09a-aa16c430388d"
    from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_PROBE_Z

    result = helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, request_id=request_id, **args)
    assert result["ok"], result
    assert result["result"]["thickness_above_honeycomb_mm"] == pytest.approx(7)
    before = list(serial.writes)
    assert helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, request_id=request_id, **args) == result
    assert serial.writes == before


def test_stale_session_rejects_without_commands(material_probe):
    serial, probe = material_probe
    reference(probe)
    serial.writes.clear()
    with pytest.raises(MachineError):
        probe.native_measure(primary_generation=999, stop_epoch=0,
                             carriage_xy_mm=(110, 110), guard=nullcontext,
                             on_motion_start=lambda: None)
    assert not serial.writes
