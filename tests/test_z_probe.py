from __future__ import annotations

import threading
import time
from contextlib import nullcontext

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.z_probe import CrealityZProbe, parse_contact, parse_position
from tests.test_secondary_controller import FakeSerial, _controller


class ProbeSerial(FakeSerial):
    def __init__(self):
        super().__init__()
        self.z = 20.0
        self.samples = [0.01, 0.02, 0.00, 3.01, 3.00, 3.02]
        self.overrides = {}

    def write_line(self, line):
        super().write_line(line)
        if line in self.overrides:
            self.responses.extend(self.overrides[line])
            return
        if line == "M115":
            self.responses.append("FIRMWARE_NAME:Marlin 2.0.8.26F4 MACHINE_TYPE:Ender-3 S1 Pro")
        elif line == "M119":
            self.responses.append("z_min: open")
        elif line == "G28":
            self.z = 5.0
        elif line.startswith("G1 Z"):
            self.z = float(line.split()[1][1:])
        elif line == "M114":
            self.responses.append(f"X:150.00 Y:150.00 Z:{self.z:.3f} E:0.00 Count X:0 Y:0 Z:8000")
        elif line == "G30 X110 Y110":
            height = self.samples.pop(0)
            self.responses.append(f"Bed X: 110.000 Y: 110.000 Z: {height:.3f}")
            self.z = max(5, height)
        self.responses.append("ok")


def ready_probe():
    serial = ProbeSerial()
    owner, fan = _controller([serial])
    fan.initialize_off()
    failures = []
    probe = CrealityZProbe(owner, lambda: failures.append("off"))
    return serial, owner, fan, probe, failures


def reference(probe, **kwargs):
    return probe.establish_reference(
        clearance_z_mm=20, support_height_mm=-1.5,
        primary_generation=1, stop_epoch=0, carriage_xy_mm=(110, 110),
        guard=kwargs.get("guard", nullcontext),
    )


def measure(probe, **kwargs):
    return probe.measure(
        primary_generation=kwargs.get("primary_generation", 1),
        stop_epoch=kwargs.get("stop_epoch", 0), carriage_xy_mm=(90, 90),
        guard=kwargs.get("guard", nullcontext),
    )


def test_border_and_material_use_actual_contacts_one_owner_and_retract():
    serial, owner, fan, probe, failures = ready_probe()
    result = reference(probe)
    assert result["reference"]["border_z_mm"] == pytest.approx(.01)
    result = measure(probe)
    assert result["surface_height_mm"] == pytest.approx(3)
    assert result["thickness_above_honeycomb_mm"] == pytest.approx(4.5)
    assert result["spread_mm"] == pytest.approx(.02)
    assert serial.z == 20
    assert serial.writes.count("G28") == 1
    assert serial.writes.count("G30 X110 Y110") == 6
    assert not any(line.startswith(("G92", "M851", "M500", "G38")) for line in serial.writes)
    fan.ensure_off()
    assert serial.open_calls == 1
    assert owner.ready and failures == []


@pytest.mark.parametrize("lines", [
    ("ok",), ("X:0 Y:0 Z:3", "ok"),
    ("Bed X:110 Y:110 Z:nan",), ("Bed X:110 Y:110 Z:inf",),
    ("Bed X:110 Y:110 Z:1", "Bed X:110 Y:110 Z:2"),
    ("Bed X:110 Y:110 Z:2oops",), ("Bed X:100 Y:110 Z:2",),
    ("Bed X:110 Y:110 Z:1e999",),
])
def test_contact_parser_rejects_non_measurements(lines):
    with pytest.raises(MachineError):
        parse_contact(lines)


@pytest.mark.parametrize("lines", [
    ("ok",), ("Count Z:8000",), ("X:1 Y:2 Z:3 Z:4",),
    ("X:1 Y:2 Z:3junk",), ("X:1 Y:2 Z:3", "X:1 Y:2 Z:3"),
])
def test_position_parser_rejects_ambiguous_readback(lines):
    with pytest.raises(MachineError):
        parse_position(lines)


@pytest.mark.parametrize(("command", "response", "error"), [
    ("M115", ["FIRMWARE_NAME:Other", "ok"], "Expected"),
    ("M119", ["z_min: TRIGGERED", "ok"], "open"),
    ("M119", ["z_min: open", "z_min: open", "ok"], "open"),
    ("G30 X110 Y110", ["ok"], "contact height"),
    ("G30 X110 Y110", ["Error:Probing Failed", "ok"], "rejected"),
    ("G30 X110 Y110", ["start", "ok"], "rejected"),
])
def test_reference_rejects_missing_identity_triggered_probe_and_no_contact(command, response, error):
    serial, _, _, probe, _ = ready_probe()
    serial.overrides[command] = response
    with pytest.raises(MachineError, match=error):
        reference(probe)
    assert probe.reference is None
    if command == "M119":
        assert "G28" not in serial.writes


def test_repeatability_failure_never_publishes_reference():
    serial, _, _, probe, _ = ready_probe()
    serial.samples = [0, .2, 0]
    with pytest.raises(MachineError, match="disagree"):
        reference(probe)
    assert probe.reference is None


@pytest.mark.parametrize("responses", [[], ["FIRMWARE_NAME:Marlin partial reply"]])
def test_identity_timeout_names_command_and_retains_failure_without_motion(
    responses, monkeypatch, caplog,
):
    serial, owner, _, probe, failures = ready_probe()
    serial.overrides["M115"] = responses
    exchange = owner._execute_acknowledged

    def fast_exchange(command, **kwargs):
        kwargs["timeout"] = 0.005
        return exchange(command, **kwargs)

    monkeypatch.setattr(owner, "_execute_acknowledged", fast_exchange)
    before = len(serial.writes)
    with pytest.raises(MachineError, match="M115: timed out") as error:
        reference(probe)

    assert f"received {len(responses)} lines" in str(error.value)
    assert (responses[-1] if responses else "last: none") in str(error.value)
    assert probe.transcript == [
        {"command": "M115", "responses": [], "error": str(error.value)}
    ]
    assert "command=M115" in caplog.text
    assert (responses[-1] if responses else "responses=()") in caplog.text
    assert serial.writes[before:] == ["M115", "M112"]
    assert probe.reference is None
    assert not owner.ready
    assert failures == ["off"]


@pytest.mark.parametrize("changed", ["owner", "primary", "stop"])
def test_reference_cannot_survive_session_or_stop_change(changed):
    serial, owner, _, probe, _ = ready_probe()
    reference(probe)
    if changed == "owner":
        owner.close()
    before = list(serial.writes)
    with pytest.raises(SafetyError, match="Reference"):
        measure(probe, primary_generation=2 if changed == "primary" else 1,
                stop_epoch=1 if changed == "stop" else 0)
    assert serial.writes == before
    assert probe.reference is None


def test_changed_z_does_not_start_a_probe():
    serial, _, _, probe, _ = ready_probe()
    reference(probe)
    serial.z = 10
    with pytest.raises(MachineError, match="Z moved"):
        measure(probe)
    assert serial.writes.count("G30 X110 Y110") == 3


def test_unacknowledged_retract_position_blocks_contacts():
    serial, _, _, probe, _ = ready_probe()
    serial.overrides["G1 Z20.000 F300"] = ["ok"]
    with pytest.raises(MachineError, match="retract"):
        reference(probe)
    assert "G30 X110 Y110" not in serial.writes


@pytest.mark.parametrize("height", [-2.1, 10.1, 79])
def test_contact_outside_clearance_interval_does_not_command_retract(height):
    serial, _, _, probe, _ = ready_probe()
    reference(probe)
    serial.samples = [height]
    before = len(serial.writes)
    with pytest.raises(MachineError, match="bounded height"):
        measure(probe)
    assert serial.writes[before:] == ["M114", "G30 X110 Y110"]


def test_interrupt_does_not_wait_for_ack_and_cannot_interrupt_replacement_session():
    serial, owner, fan, probe, _ = ready_probe()
    entered = threading.Event()
    serial.overrides["G28"] = []
    serial.on_write = lambda command: entered.set() if command == "G28" else None
    errors = []

    def run():
        try:
            reference(probe)
        except MachineError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(2)
    generation = owner.generation
    started = time.monotonic()
    assert fan.status.enabled is False  # Status must not wait behind G28.
    owner.interrupt_probe(generation)
    assert time.monotonic() - started < .5
    thread.join(2)
    assert not thread.is_alive() and errors
    assert serial.writes.count("M112") == 1
    owner.interrupt_probe(generation)
    assert serial.writes.count("M112") == 1
    assert probe.reference is None
