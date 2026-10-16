"""Pause tests use only fake controllers; no physical output is qualified."""
from __future__ import annotations

import threading
import time

import pytest
from test_controller_session import make_machine
from test_machine_streaming import StreamingTransport, _finish, _program, _start

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine import job_pause, service
from laser_aligner.machine.job_pause import JobPause


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


class PauseTransport(StreamingTransport):
    def __init__(self, *, laser_mode="1", identity="[VER:1.1h.test:PAUSE]", options="[OPT:V,16,128]"):
        super().__init__(hold=True, options=options)
        self.laser_mode = laser_mode
        self.identity = identity
        self.suspended = False
        self.reply_status = True
        self.suspend_status = "Door:0"
        self.resume_entered = threading.Event()
        self.resume_release = threading.Event()
        self.resume_release.set()
        self.failure = None

    def _default_response(self, command):
        if command == "$$":
            return (f"$1={self.step_idle_delay_ms}", f"$32={self.laser_mode}", "ok")
        if command == "$I":
            return (self.identity, *((self.options,) if self.options else ()), "ok")
        return super()._default_response(command)

    def write_raw_guarded(self, data, guard):
        if data == b"~":
            self.resume_entered.set()
            assert self.resume_release.wait(3)
        guard()
        if self.failure:
            raise MachineError(self.failure)
        self.write_raw(data)

    def write_raw(self, data):
        if data == b"\x84":
            self.writes.append(("raw", data))
            self.suspended = True
        elif data == b"~":
            self.writes.append(("raw", data))
            self.suspended = False
        elif data == b"?" and self.observe_motion:
            self.writes.append(("raw", data))
            if self.reply_status:
                state = self.suspend_status if self.suspended else "Run"
                self.queue_response(f"<{state}|MPos:1,1,0|FS:0,0>")
        else:
            super().write_raw(data)


@pytest.fixture
def paused_machine(monkeypatch):
    created = []

    def create(**kwargs):
        transport = PauseTransport(**kwargs)
        machine, _ = make_machine(monkeypatch, transport)
        machine.settings.home_and_release_after_powered_job = False
        machine.connect()
        machine.prepare_job_start()
        transport.observe_motion = True
        transport.synchronize_input = None  # No asynchronous fake reconnection.
        created.append((machine, transport))
        return machine, transport

    yield create
    for machine, transport in created:
        transport.resume_release.set()
        machine.request_stop(_recover=False)
        transport.release_all()
        if machine._job_thread:
            machine._job_thread.join(3)


def request(machine, *, resume=False, **overrides):
    pause = machine.status()["job_pause"]
    return machine.request_pause(resume=resume, **{
        "expected_run_id": pause["run_id"], "expected_pause_revision": pause["revision"], **overrides,
    })


def pause(machine):
    request(machine)
    wait_for(lambda: machine.status()["job_pause"]["state"] == "paused")
    assert machine.status()["job"]["phase"] == "paused"


@pytest.mark.parametrize("powered", [False, True])
@pytest.mark.parametrize("options", [None, "[OPT:V,16,128]"])
def test_pause_keeps_pending_acks_and_resumes_exact_stream(paused_machine, monkeypatch, powered, options):
    machine, transport = paused_machine(options=options)
    monkeypatch.setattr(service, "_JOB_COMMAND_ACK_TIMEOUT_SECONDS", 0.5)
    program, worker = _start(machine, _program(powered=powered))
    transport.wait_for_motion(1)
    pause(machine)
    before = list(transport.motion_lines)
    time.sleep(0.65)  # A deliberate hold exceeds the ordinary ACK deadline.
    assert worker.is_alive() and machine.status()["job"]["error"] is None
    assert transport.motion_lines == before
    assert ("raw", b"!\x18") not in transport.writes
    assert program.digest == machine.status()["job"]["program_digest"]
    request(machine, resume=True)
    wait_for(lambda: machine.status()["job_pause"]["state"] == "running")
    transport.release_all()
    job = _finish(machine, worker)
    assert job["phase"] == "complete", job
    assert job["completed_lines"] == len(program.lines)
    assert len(transport.motion_lines) == 20
    assert transport.writes.count(("raw", b"\x84")) == 1
    assert transport.writes.count(("raw", b"~")) == 1


def test_pause_ordinary_ack_and_repeated_cycles(paused_machine):
    machine, transport = paused_machine()
    # Single noncanonical move exercises the ordinary transaction owner.
    _, worker = _start(machine, "G21\nG90\nM5\nG1 X2 Y2 F100\nM5")
    transport.wait_for_motion(1)
    for _ in range(2):
        pause(machine)
        request(machine, resume=True)
        wait_for(lambda: machine.status()["job_pause"]["state"] == "running")
    transport.release_all()
    assert _finish(machine, worker)["phase"] == "complete"


@pytest.mark.parametrize("kwargs", [{"laser_mode": "0"}, {"laser_mode": "bad"}, {"identity": "[VER:0.9j:OLD]"}])
def test_unsupported_controller_rejects_without_pause_bytes(paused_machine, kwargs):
    machine, transport = paused_machine(**kwargs)
    _start(machine, _program())
    transport.wait_for_motion(1)
    with pytest.raises(MachineError, match="GRBL 1.1"):
        request(machine)
    assert ("raw", b"\x84") not in transport.writes


def test_stale_requests_and_early_resume_are_rejected(paused_machine):
    machine, transport = paused_machine()
    _start(machine, _program())
    transport.wait_for_motion(1)
    with pytest.raises(MachineError, match="confirmed paused"):
        request(machine, resume=True)
    with pytest.raises(MachineError, match="stale"):
        request(machine, expected_run_id=999)
    pause(machine)
    with pytest.raises(MachineError, match="stale"):
        request(machine, resume=True, expected_pause_revision=0)
    assert ("raw", b"~") not in transport.writes


@pytest.mark.parametrize("fault", ["STOP", "allow_motion", "hardware_enabled", "authorization", "alarm", "restart", "unexpected_resume", "lost_status", "open_door"])
def test_pause_failure_cannot_resume(paused_machine, monkeypatch, fault):
    machine, transport = paused_machine()
    _, worker = _start(machine, _program(powered=True))
    transport.wait_for_motion(1)
    pause(machine)
    if fault == "STOP":
        machine.request_stop(_recover=False)
    elif fault == "allow_motion":
        machine.settings.allow_motion = False
    elif fault == "hardware_enabled":
        machine.hardware_enabled = False
    elif fault == "authorization":
        machine._job_laser_authorized = False
    elif fault == "alarm":
        transport.queue_response("ALARM:1")
    elif fault == "restart":
        transport.queue_response("Grbl 1.1h ['$' for help]")
    elif fault == "unexpected_resume":
        transport.queue_response("<Run|MPos:1,1,0>")
    elif fault == "lost_status":
        monkeypatch.setattr(job_pause, "STATUS_TIMEOUT", 0.1)
        transport.reply_status = False
    else:
        transport.queue_response("<Door:1|MPos:1,1,0>")
    job = _finish(machine, worker)
    assert job["phase"] == "failed", job
    with pytest.raises((MachineError, SafetyError)):
        request(machine, resume=True)
    assert ("raw", b"~") not in transport.writes
    assert ("raw", b"!\x18") in transport.writes


def test_stop_wins_when_resume_waits_for_physical_write(paused_machine):
    machine, transport = paused_machine()
    _, worker = _start(machine, _program(powered=True))
    transport.wait_for_motion(1)
    pause(machine)
    transport.resume_release.clear()
    failures = []

    def resume():
        try:
            request(machine, resume=True)
        except MachineError as exc:
            failures.append(exc)

    thread = threading.Thread(target=resume)
    thread.start()
    assert transport.resume_entered.wait(2)
    machine.request_stop(_recover=False)
    transport.resume_release.set()
    thread.join(3)
    assert not thread.is_alive()
    assert failures
    assert ("raw", b"~") not in transport.writes
    assert _finish(machine, worker)["phase"] == "failed"


def test_pause_transition_timeout_fails_off(paused_machine, monkeypatch):
    machine, transport = paused_machine()
    _, worker = _start(machine, _program())
    transport.wait_for_motion(1)
    transport.suspend_status = "Door:2"
    monkeypatch.setattr(job_pause, "TRANSITION_TIMEOUT", 0.1)
    request(machine)
    assert _finish(machine, worker)["phase"] == "failed"
    assert ("raw", b"!\x18") in transport.writes


def test_spindle_report_blocks_paused_claim():
    pause = JobPause()
    pause.request(resume=False)
    with pytest.raises(MachineError, match="spindle"):
        pause.observe("<Door:0|MPos:0,0,0|A:S>")


def test_pause_is_unavailable_during_secondary_focus(paused_machine):
    machine, transport = paused_machine()
    _start(machine, _program())
    transport.wait_for_motion(1)
    machine._active_job_context.status.phase = "focusing"
    with pytest.raises(MachineError, match="focus"):
        request(machine)
    assert ("raw", b"\x84") not in transport.writes

