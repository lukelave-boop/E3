"""Normal cancellation keeps a proven session; fake hardware only."""
import time

import pytest
from test_job_pause import pause
from test_job_pause import paused_machine as paused_machine
from test_machine_streaming import _finish, _program, _start

from laser_aligner.errors import MachineError, SafetyError


def cancel(machine, **kwargs):
    state = machine.status()["job_pause"]
    return machine.request_cancel(**{
        "expected_run_id": state["run_id"], "expected_pause_revision": state["revision"], **kwargs,
    })


@pytest.mark.parametrize("powered", [False, True])
@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("options", [None, "[OPT:V,16,128]"])
def test_normal_stop_drains_only_buffered_tail_and_can_start_again(paused_machine, powered, paused, options):
    machine, transport = paused_machine(options=options)
    program, worker = _start(machine, _program(count=100, powered=powered))
    transport.wait_for_motion(1)
    if paused:
        pause(machine)
        with pytest.raises(MachineError, match="confirmation"):
            cancel(machine)
        assert ("raw", b"~") not in transport.writes
    session = machine._session
    stop_epoch = machine.operation_generation()
    cancel(machine, finish_buffered=paused)
    admitted = list(transport.motion_lines)
    assert machine.status()["job"]["phase"] == "cancelling"
    assert not machine.status()["job_pause"]["available"]
    time.sleep(.05)
    assert transport.motion_lines == admitted
    assert worker.is_alive()  # Pending ACKs cannot be discarded.
    transport.release_all()
    result = _finish(machine, worker)
    assert result["phase"] == "stopped", result
    assert result["error"] is None
    assert result["completed_lines"] < len(program.lines)
    assert transport.motion_lines == admitted
    assert machine._session is session
    assert machine.operation_generation() == stop_epoch
    assert machine.status()["controller_state"] == "READY_MOTION"
    assert not machine.armed and not machine._job_laser_authorized
    assert machine._last_successful_job is None
    assert ("raw", b"!\x18") not in transport.writes
    assert any(kind == "line" and value == "M5" for kind, value in transport.writes)
    # A fresh, edited program goes through normal validation and authorization.
    _, successor = _start(machine, "G21\nG90\nM5\nG1 X3 Y3 F300\nM5")
    assert _finish(machine, successor)["phase"] == "complete"


@pytest.mark.parametrize("field,value", [
    ("expected_run_id", 999), ("expected_pause_revision", 999),
    ("expected_run_id", True), ("finish_buffered", 1),
])
def test_cancel_rejects_stale_or_malformed_identity_without_writes(paused_machine, field, value):
    machine, transport = paused_machine()
    _, worker = _start(machine, _program(count=100))
    transport.wait_for_motion(1)
    with pytest.raises((MachineError, SafetyError)):
        cancel(machine, **{field: value})
    assert not machine._active_job_context.cancel_event.is_set()
    assert ("raw", b"~") not in transport.writes
    transport.release_all()
    assert _finish(machine, worker)["phase"] == "complete"


def test_emergency_stop_wins_during_normal_cancellation(paused_machine):
    machine, transport = paused_machine()
    _, worker = _start(machine, _program(count=100, powered=True))
    transport.wait_for_motion(1)
    cancel(machine)
    machine.request_stop(emergency=True, _recover=False)
    transport.release_all()
    result = _finish(machine, worker)
    assert result["phase"] == "failed"
    assert not machine._coordinate_reference_ready
    assert not machine._job_laser_authorized
    assert ("raw", b"!\x18") in transport.writes


def test_cancel_during_focus_rejected(paused_machine):
    machine, transport = paused_machine()
    _, worker = _start(machine, _program(count=100))
    transport.wait_for_motion(1)
    machine._active_job_context.status.phase = "focusing"
    with pytest.raises(MachineError, match="focus"):
        cancel(machine)
    assert not machine._active_job_context.cancel_event.is_set()
    machine._active_job_context.status.phase = "streaming"
    transport.release_all()
    assert _finish(machine, worker)["phase"] == "complete"


def test_stop_revokes_cancel_resume_waiting_at_physical_write(paused_machine):
    import threading
    machine, transport = paused_machine()
    _, worker = _start(machine, _program(count=100, powered=True))
    transport.wait_for_motion(1)
    pause(machine)
    transport.resume_release.clear()
    errors = []
    def request():
        try:
            cancel(machine, finish_buffered=True)
        except MachineError as exc:
            errors.append(exc)
    requester = threading.Thread(target=request)
    requester.start()
    assert transport.resume_entered.wait(2)
    machine.request_stop(emergency=True, _recover=False)
    transport.resume_release.set()
    requester.join(3)
    transport.release_all()
    assert _finish(machine, worker)["phase"] == "failed"
    assert errors
    assert ("raw", b"~") not in transport.writes
