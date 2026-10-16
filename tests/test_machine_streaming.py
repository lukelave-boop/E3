"""GRBL streaming integration: bounded queued motion and exact-session aborts.

All controller traffic terminates at deterministic in-process fake transports.
These assertions establish protocol behavior, not physical speed or fail-off.
"""
from __future__ import annotations

import re
import threading
from collections import deque
from dataclasses import asdict
from decimal import Decimal
from unittest.mock import patch

import pytest
from test_controller_session import GRBL_IDENTITY, ResponsiveTransport, machine_settings, make_machine

from laser_aligner.config import LaserSettings
from laser_aligner.errors import SafetyError
from laser_aligner.machine import service as service_module
from laser_aligner.machine.service import MachineService
from laser_aligner.operation_focus import target_z
from tests import test_laser_focus as focus_helpers
from tests.fakes.simulator_controller import SimulatedController
from tests.test_thickness_focus import automatic

focus = focus_helpers.focus
focus_machine = focus_helpers.focus_machine
machine_probe = focus_helpers.machine_probe


def _motion(line: str) -> bool:
    return bool(re.match(r"(?:G1(?=[ X])|[XY])", line))


class StreamingTransport(ResponsiveTransport):
    """Hold motion ACKs independently of writes to exercise actual backpressure."""

    def __init__(self, *, options: str | None = "[OPT:V,16,128]", hold: bool = False):
        super().__init__("streaming")
        self.options = options
        self.hold = hold
        self.motion_lines: list[str] = []
        self.pending_motion: deque[str] = deque()
        self.motion_changed = threading.Condition()
        self.ack_waiting = threading.Event()
        self.observe_motion = False

    def _default_response(self, command: str) -> tuple[str, ...]:
        if command == "$I":
            return (GRBL_IDENTITY, *((self.options,) if self.options else ()), "ok")
        if self.observe_motion and _motion(command):
            with self.motion_changed:
                self.motion_lines.append(command)
                if self.hold:
                    self.pending_motion.append(command)
                self.motion_changed.notify_all()
                return () if self.hold else ("ok",)
        return super()._default_response(command)

    def wait_for_motion(self, count: int) -> None:
        with self.motion_changed:
            assert self.motion_changed.wait_for(lambda: len(self.motion_lines) >= count, timeout=3)

    def acknowledge_one(self) -> None:
        with self.motion_changed:
            assert self.pending_motion
            self.pending_motion.popleft()
            self.queue_response("ok")

    def release_all(self) -> None:
        with self.motion_changed:
            self.hold = False
            while self.pending_motion:
                self.pending_motion.popleft()
                self.queue_response("ok")


@pytest.fixture
def stream_machine(monkeypatch):
    created = []

    def create(*, options="[OPT:V,16,128]", hold=False):
        transport = StreamingTransport(options=options, hold=hold)
        machine, _factory = make_machine(monkeypatch, transport)
        created.append((machine, transport))
        # A realistic job ACK deadline is independent of short interactive reads.
        machine.settings.read_timeout = 0.2
        machine.settings.home_and_release_after_powered_job = False
        machine.connect()
        machine.prepare_job_start()
        # Keep fault assertions tied to the original generation. The ordinary
        # recovery tests cover creating another communication-only session.
        transport.synchronize_input = None
        transport.observe_motion = True
        receiver = machine._session.receiver
        read_ack = receiver.stream_read_ack

        def observe_ack_wait(*args, **kwargs):
            transport.ack_waiting.set()
            return read_ack(*args, **kwargs)

        monkeypatch.setattr(receiver, "stream_read_ack", observe_ack_wait)
        return machine, transport

    yield create
    for machine, transport in created:
        transport.write_release.set()
        transport.release_all()
        worker = machine._job_thread
        if worker is not None and worker.is_alive():
            machine.request_stop(_recover=False)
            worker.join(3)
        machine.disconnect()


def _program(count=20, *, powered=False):
    return "\n".join([
        "G21", "G90", "M5", "G0 X1 Y1 F100", "M4 S0",
        *[f"G1 X{index + 1}.000 Y1.000 F600.000 S{100 if powered else 0}"
          for index in range(count)],
        "M5",
    ])


def _start(machine, text):
    program = machine.preflight_program(text)
    machine.start_preflighted_program(
        program, "stream-test.gcode",
        authorization_phrase=machine.ARM_PHRASE if program.requires_laser_authorization else None,
    )
    assert machine._job_thread is not None
    return program, machine._job_thread


def _finish(machine, worker):
    worker.join(4)
    assert not worker.is_alive(), "stream worker did not finish"
    return machine.status()["job"]


def _assert_aborted(machine, transport, worker):
    status = _finish(machine, worker)
    assert status["error"]
    assert status["phase"] == "failed"
    assert not machine.status()["armed"]
    assert not machine.status()["coordinate_reference_ready"]
    assert machine.status()["last_successful_job"] is None
    assert transport.closed.wait(2)
    assert ("raw", b"!\x18") in transport.writes
    reset = transport.writes.index(("raw", b"!\x18"))
    assert ("line", "M5") in transport.writes[reset + 1:]


def _replay_motion(lines):
    """Independent decimal modal replay of endpoint/feed/power on each move."""
    state = {"X": Decimal(1), "Y": Decimal(1), "F": Decimal(100), "S": Decimal(0)}
    result = []
    for line in lines:
        for letter, number in re.findall(r"([A-Z])(-?\d+(?:\.\d+)?)", line):
            if letter in state:
                state[letter] = Decimal(number)
        result.append(tuple(state[key] for key in ("X", "Y", "F", "S")))
    return result


def test_stream_preserves_every_endpoint_feed_and_power_without_setting_changes(stream_machine):
    machine, transport = stream_machine()
    moves = [
        "G1 X1.1000 Y1.000 F600.000 S0",
        "G1 X1.2000 Y1.000 F600.000 S100",
        "G1 X1.2000 Y1.000 F600.000 S0",
        "G1 X1.2000 Y1.100 F500.000 S80.500",
        "G1 X1.3000 Y1.100 F500.000",
        "G1 X1.4000 Y1.100 F500.000 S0",
    ]
    text = "\n".join(["G21", "G90", "M5", "G0 X1 Y1 F100", "M4 S0", *moves, "M5"])
    original_settings = asdict(machine.settings)
    program, worker = _start(machine, text)
    original_lines, original_digest = program.lines, program.digest
    assert _finish(machine, worker)["error"] is None
    assert len(transport.motion_lines) == len(moves)
    assert _replay_motion(transport.motion_lines) == _replay_motion(moves)
    assert transport.motion_lines[0] == "G1X1.1Y1F600S0"
    assert transport.motion_lines[2] == "X1.2S0"  # Coincident S0 is never dropped.
    assert asdict(machine.settings) == original_settings
    assert (program.lines, program.digest) == (original_lines, original_digest)
    assert not any(kind == "line" and str(line).startswith("$") and "=" in str(line)
                   for kind, line in transport.writes[transport.writes.index(("line", "M4 S0")):])


@pytest.mark.parametrize("options,expected_limit", [("[OPT:V,16,32]", 31), ("[OPT:V,3,128]", 127)])
def test_known_capacity_pipelines_and_applies_byte_and_planner_backpressure(
    stream_machine, options, expected_limit,
):
    machine, transport = stream_machine(options=options, hold=True)
    _program_proof, worker = _start(machine, _program())
    assert transport.ack_waiting.wait(3)
    accepted = list(transport.motion_lines)
    assert len(accepted) > 1  # Deterministic throughput property: no ACK delivered yet.
    assert sum(len(line.encode("ascii")) + 1 for line in accepted) <= expected_limit
    if expected_limit == 31:
        assert sum(len(line) + 1 for line in accepted) + len(f"X{len(accepted) + 1}") + 1 > 31
    else:
        assert len(accepted) == 3
    assert machine.status()["job"]["running"]
    assert machine.status()["job"]["completed_lines"] == 5
    before_ack = len(accepted)
    transport.acknowledge_one()
    transport.wait_for_motion(before_ack + 1)
    transport.release_all()
    assert _finish(machine, worker)["error"] is None
    assert len(transport.motion_lines) == 20


@pytest.mark.parametrize("options", [None, "[OPT:V,0,128]", "[OPT:V,16,unknown]"])
def test_unknown_capacity_allows_one_outstanding_without_per_move_quiet_wait(stream_machine, options):
    machine, transport = stream_machine(options=options, hold=True)
    receiver = machine._session.receiver
    with (
        patch.object(machine, "_reject_queued_unowned_responses",
                     wraps=machine._reject_queued_unowned_responses) as quiet,
        patch.object(receiver, "finish_stream", wraps=receiver.finish_stream) as fence,
    ):
        _proof, worker = _start(machine, _program(40))
        assert transport.ack_waiting.wait(3)
        assert len(transport.motion_lines) == 1
        quiet_before = quiet.call_count
        transport.acknowledge_one()
        transport.wait_for_motion(2)
        assert quiet.call_count == quiet_before
        transport.release_all()
        assert _finish(machine, worker)["error"] is None
        assert fence.call_count == 1
        # Header, final laser-off and motion barrier retain ordinary quiet checks.
        assert quiet.call_count - quiet_before < 10
    assert len(transport.motion_lines) == 40


@pytest.mark.parametrize("frame", [
    "error:20", "ALARM:1", "Grbl 1.1h ['$' for help]", " ok", "[GC:G1 G21 G90]",
    "<Hold:0|MPos:0,0,0|WCO:0,0,0>",
])
def test_fault_during_pending_stream_aborts_and_sends_no_later_move(stream_machine, frame):
    machine, transport = stream_machine(options="[OPT:V,2,128]", hold=True)
    _proof, worker = _start(machine, _program(powered=True))
    assert transport.ack_waiting.wait(3)
    before = list(transport.motion_lines)
    transport.queue_response(frame)
    _assert_aborted(machine, transport, worker)
    assert transport.motion_lines == before


def test_excess_stream_ack_is_not_reused_by_final_m5(stream_machine):
    machine, transport = stream_machine(hold=True)
    _proof, worker = _start(machine, _program(2))
    assert transport.ack_waiting.wait(3)
    transport.queue_response("ok", "ok", "ok")
    _assert_aborted(machine, transport, worker)
    assert "unowned" in machine.status()["job"]["error"].lower()


@pytest.mark.parametrize("cancel", ["stop", "disarm", "disconnect", "motion-policy"])
def test_pending_stream_rechecks_authority_before_next_motion(stream_machine, cancel):
    machine, transport = stream_machine(options="[OPT:V,2,128]", hold=True)
    _proof, worker = _start(machine, _program(powered=True))
    assert transport.ack_waiting.wait(3)
    before = list(transport.motion_lines)
    if cancel == "stop":
        machine.request_stop(_recover=False)
    elif cancel == "disarm":
        machine.disarm()
    elif cancel == "disconnect":
        machine.disconnect()
    else:
        machine.settings.allow_motion = False
        transport.acknowledge_one()
    _assert_aborted(machine, transport, worker)
    assert transport.motion_lines == before


def test_stop_during_blocked_stream_write_prevents_following_moves(stream_machine):
    machine, transport = stream_machine()
    transport.block_command = "G1X1Y1F600S100"
    transport.write_release.clear()
    _proof, worker = _start(machine, _program(powered=True))
    assert transport.write_entered.wait(3)
    # STOP can revoke ownership and issue realtime abort while the ordinary
    # transport write remains blocked. Its bounded M5 attempt may follow release.
    machine.request_stop(_recover=False)
    assert ("raw", b"!\x18") in transport.writes
    transport.write_release.set()
    _assert_aborted(machine, transport, worker)
    assert len(transport.motion_lines) == 1


def test_final_m5_and_completion_wait_for_every_motion_ack(stream_machine):
    machine, transport = stream_machine(hold=True)
    _proof, worker = _start(machine, _program(3))
    assert transport.ack_waiting.wait(3)
    motion_start = transport.writes.index(("line", transport.motion_lines[0]))
    assert ("line", "M5") not in transport.writes[motion_start:]
    transport.acknowledge_one()
    transport.acknowledge_one()
    assert ("line", "M5") not in transport.writes[motion_start:]
    assert machine.status()["job"]["running"]
    transport.release_all()
    assert _finish(machine, worker)["error"] is None
    tail = transport.writes[motion_start:]
    assert tail.index(("line", "M5")) > tail.index(("line", transport.motion_lines[-1]))
    assert ("line", "G4 P0.01") in tail


def test_new_stream_after_mode_boundary_and_next_job_restates_complete_mode(stream_machine):
    machine, transport = stream_machine()
    text = "\n".join([
        "G21", "G90", "M5", "G0 X1 Y1 F100", "M4 S0",
        "G1 X2 Y1 F600 S100", "G1 X3 Y1 F600 S0", "M5", "M4 S0",
        "G1 X3 Y1 F600 S0", "G1 X4 Y1 F600 S100", "M5",
    ])
    for _ in range(2):
        _proof, worker = _start(machine, text)
        assert _finish(machine, worker)["error"] is None
    assert transport.motion_lines == [
        "G1X2Y1F600S100", "X3S0", "G1X3Y1F600S0", "X4S100",
    ] * 2


def test_stream_ack_timeout_fails_off_without_retry(stream_machine, monkeypatch):
    machine, transport = stream_machine(hold=True)
    monkeypatch.setattr(service_module, "_JOB_COMMAND_ACK_TIMEOUT_SECONDS", 0.25)
    _proof, worker = _start(machine, _program(2))
    _assert_aborted(machine, transport, worker)
    assert len(transport.motion_lines) == 2
    assert "acknowledge" in machine.status()["job"]["error"]


def test_benign_realtime_and_diagnostic_frames_do_not_acknowledge_motion(stream_machine):
    machine, transport = stream_machine(options="[OPT:V,2,128]", hold=True)
    _proof, worker = _start(machine, _program(4, powered=True))
    assert transport.ack_waiting.wait(3)
    transport.queue_response(
        "<Run|MPos:1,1,0|WCO:0,0,0>",
        "I (42) gpio: bounded controller diagnostic",
    )
    # Only the actual FIFO ACK releases credit for a third command.
    transport.acknowledge_one()
    transport.wait_for_motion(3)
    assert machine.status()["job"]["completed_lines"] == 6
    transport.release_all()
    assert _finish(machine, worker)["error"] is None
    assert len(transport.motion_lines) == 4


def test_simulated_peer_preserves_modal_motion_and_inline_laser_power():
    controller = SimulatedController()
    for command in ("G21", "G90", "M5", "M4 S0", "G1X1Y2F600S100", "X2", "Y3S0"):
        assert controller.receive_line(command) == ("ok",)
    assert (controller.x, controller.y, controller.power, controller.laser_on) == (2, 3, 0, False)
    controller.receive_line("X4S50")
    assert (controller.x, controller.y, controller.power, controller.laser_on) == (4, 3, 50, True)
    controller.receive_line("M5")
    controller.receive_line("X5")
    assert (controller.x, controller.y, controller.power, controller.laser_on) == (5, 3, 0, False)


def test_streaming_keeps_real_focus_descent_and_lift_boundaries(focus_machine, monkeypatch):
    machine, focus_state, primary = focus_machine
    plan = automatic(machine, focus_state)["job_focus"]
    events = []
    for device, label in ((primary, "xy"), (focus_state.serial, "z")):
        original = device.write_line

        def record(line, original=original, label=label):
            events.append((label, line))
            return original(line)

        monkeypatch.setattr(device, "write_line", record)

    stream = machine._stream_running_job_moves

    def record_stream(*args, **kwargs):
        events.append(("stream", focus_state.serial.z))
        return stream(*args, **kwargs)

    monkeypatch.setattr(machine, "_stream_running_job_moves", record_stream)
    lines = ["G21", "G90"]
    for focus_mode in ("RASTER", "CUT"):
        lines.extend([
            "M5", f"E3OPFOCUS {focus_mode}", "G0 X100 Y100 F1000", "M4 S0",
            "G1 X101 Y100 F500 S100", "G1 X102 Y100 F500 S0",
        ])
    _proof, worker = _start(machine, "\n".join([*lines, "M5"]))
    worker.join(8)
    assert not worker.is_alive()
    assert machine.status()["job"]["error"] is None
    stream_events = [(index, value) for index, (kind, value) in enumerate(events) if kind == "stream"]
    assert len(stream_events) == 2
    previous_stream = -1
    for (index, z), focus_mode in zip(stream_events, ("RASTER", "CUT"), strict=True):
        expected = target_z(plan, focus_mode)
        assert z == pytest.approx(expected, abs=0.0005)
        interval = events[previous_stream + 1:index]
        descent = interval.index(("z", f"G1 Z{expected:.3f} F600"))
        assert ("xy", "G4 P0.01") in interval[:descent]
        if previous_stream >= 0:
            lift = interval.index(("z", "G1 Z30.000 F1200"))
            assert ("xy", "M5") in interval[:lift]
            assert ("xy", "G4 P0.01") in interval[:lift]
        previous_stream = index
    assert not any(kind == "xy" and str(value).startswith("E3") for kind, value in events)
    last_motion = max(index for index, event in enumerate(events) if event == ("xy", "X102S0"))
    final_lift = max(index for index, event in enumerate(events) if event == ("z", "G1 Z30.000 F1200"))
    assert last_motion < final_lift
    assert ("xy", "M5") in events[last_motion:final_lift]
    assert ("xy", "G4 P0.01") in events[last_motion:final_lift]


def test_secondary_air_off_follows_acknowledged_motion_and_primary_m5(monkeypatch):
    from tests import test_secondary_air_assist_integration as air_helpers

    events = []
    binding = air_helpers._binding()
    secondary = air_helpers._FakeSecondarySerial(on_write=lambda line: events.append(("air", line)))
    fan = air_helpers._fan_controller(binding, [secondary])
    primary = StreamingTransport(hold=True)
    write_primary = primary.write_line

    def record_primary(line):
        events.append(("xy", line))
        return write_primary(line)

    monkeypatch.setattr(primary, "write_line", record_primary)
    monkeypatch.setattr(service_module, "create_machine_transport", lambda *_args: primary)
    settings = machine_settings()
    settings.air_assist = air_helpers._air_settings()
    settings.read_timeout = 0.2
    settings.home_and_release_after_powered_job = False
    machine = MachineService(settings, LaserSettings(), hardware_enabled=True, secondary_air_assist=fan)
    worker = None
    try:
        machine.connect()
        machine.prepare_job_start()
        primary.observe_motion = True
        events.clear()
        text = "\n".join([
            "G21", "G90", "M5", *binding.program_lines(False),
            "G0 X1 Y1 F100", *binding.program_lines(True), "M4 S0",
            "G1 X2 Y1 F600 S100", "G1 X3 Y1 F600 S0", "M5", *binding.program_lines(False),
            "M5",
        ])
        _proof, worker = _start(machine, text)
        primary.wait_for_motion(2)
        air_on = events.index(("air", "M106 S255"))
        assert ("air", "M106 S0") not in events[air_on + 1:]
        assert machine.status()["job"]["running"]
        primary.release_all()
        assert _finish(machine, worker)["error"] is None
        air_off = events.index(("air", "M106 S0"), air_on + 1)
        last_move = events.index(("xy", "X3S0"))
        assert air_on < last_move < air_off
        assert ("xy", "M5") in events[last_move:air_off]
        assert not any(kind == "xy" and str(line).startswith(("E3", "M106", "M107"))
                       for kind, line in events)
    finally:
        primary.release_all()
        if worker is not None and worker.is_alive():
            machine.request_stop(_recover=False)
            worker.join(3)
        machine.disconnect()


@pytest.mark.parametrize("authority", ["stop", "motion", "hardware", "authorization"])
def test_revocation_after_stream_admission_drain_blocks_actual_write(stream_machine, monkeypatch, authority):
    machine, transport = stream_machine()
    receiver = machine._session.receiver
    drain = receiver._drain_stream_input
    stopped = threading.Event()

    def stop_between_guard_and_write():
        result = drain()
        if receiver._stream is not None and not stopped.is_set():
            # Revoke after the outer service guard but before the callback.
            # STOP sets cancellation before receiver retirement; the other
            # gates can also change while admission drains queued input.
            if authority == "stop":
                machine._active_job_context.stop_event.set()
            elif authority == "motion":
                machine.settings.allow_motion = False
            elif authority == "hardware":
                machine.hardware_enabled = False
            else:
                machine._job_laser_authorized = False
            stopped.set()
        return result

    monkeypatch.setattr(receiver, "_drain_stream_input", stop_between_guard_and_write)
    _proof, worker = _start(machine, _program(3, powered=True))
    _assert_aborted(machine, transport, worker)
    assert stopped.is_set()
    assert transport.motion_lines == []


@pytest.mark.parametrize("capacity", ["unknown", "small"])
def test_oversized_stream_line_rejected_before_job_motion_or_arm(stream_machine, capacity):
    machine, transport = stream_machine(options=None if capacity == "unknown" else "[OPT:V,16,16]")
    coordinate = "1." + "123456789" * 8 if capacity == "unknown" else "1.234"
    move = f"G1 X{coordinate} Y{coordinate} F600 S100"
    assert len(move) < 256  # Valid canonical source; wire capacity is the issue.
    program = machine.preflight_program("\n".join([
        "G21", "G90", "M5", "G0 X1 Y1 F100", "M4 S0", move, move, "M5",
    ]))
    original = (program.lines, program.digest)
    before = len(transport.writes)
    with pytest.raises(SafetyError, match="stream|window|byte|capacity"):
        machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    assert not machine.status()["armed"]
    assert not machine.status()["job"]["running"]
    assert not any(kind == "line" and str(line).startswith(("G0", "G1", "M3", "M4"))
                   for kind, line in transport.writes[before:])
    assert (program.lines, program.digest) == original


def test_isolated_canonical_move_retains_ordinary_transaction_path(stream_machine):
    machine, transport = stream_machine(options="[OPT:V,16,16]")
    move = "G1 X1.234 Y1.234 F600 S100"
    with patch.object(machine, "_stream_running_job_moves", wraps=machine._stream_running_job_moves) as stream:
        _proof, worker = _start(machine, "\n".join([
            "G21", "G90", "M5", "G0 X1 Y1 F100", "M4 S0", move, "M5",
        ]))
        assert _finish(machine, worker)["error"] is None
        assert stream.call_count == 0
    assert transport.motion_lines == [move]
