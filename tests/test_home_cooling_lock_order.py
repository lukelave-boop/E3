"""Force the observed Home/cooling interleaving without physical controllers."""
import threading

import pytest

from laser_aligner.errors import MachineError
from tests import test_laser_focus as helpers
from tests.test_job_focus import PROGRAM, wait
from tests.test_thickness_focus import automatic

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe


@pytest.mark.parametrize("stop_during_home", [False, True])
@pytest.mark.parametrize("operation", ["home", "completion"])
def test_home_guard_does_not_hold_stop_lock_while_waiting_for_cooling(focus_machine, monkeypatch, stop_during_home, operation):
    machine, focus, primary = focus_machine
    plan = automatic(machine, focus)["job_focus"]
    machine.cpu_cooling_enabled = True
    cooling_entered, home_waiting = threading.Event(), threading.Event()
    errors, observations = [], []
    active_thread = [threading.get_ident()]
    original_lock = focus.owner._lock

    class OwnerLock:
        def acquire(self, blocking=True):
            if threading.get_ident() == active_thread[0] and cooling_entered.is_set():
                home_waiting.set()
            return original_lock.acquire(blocking)

        def release(self):
            original_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_):
            self.release()

    class BoundedGate:
        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            if not self.lock.acquire(timeout=2):
                raise RuntimeError("Home/cooling lock inversion")
            return self

        def __exit__(self, *_):
            self.lock.release()

    def cooling_exchange(owner, pwm, *, guard):
        cooling_entered.set()  # CPU cooling already owns Ender.
        assert home_waiting.wait(2)
        with guard():
            pass
        observations.append(machine.operation_generation())
        if stop_during_home:
            machine.request_stop(_recover=False)
            observations.append(machine.operation_generation())
        return {"changed": False}

    def cool():
        try:
            machine.update_cpu_cooling(255)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=cool)
    original_exchange = machine._execute_grbl_homing_exchange

    def exchange(**kwargs):
        original_write = kwargs["write_homing_command"]
        def write():
            original_write()  # Release the command's guard before overlapping.
            worker.start()
            assert cooling_entered.wait(2)
        return original_exchange(**dict(kwargs, write_homing_command=write))

    monkeypatch.setattr(focus.owner, "_lock", OwnerLock())
    monkeypatch.setattr(machine, "_secondary_write_gate", BoundedGate())
    monkeypatch.setattr("laser_aligner.machine.cpu_cooling.update_fan", cooling_exchange)
    if operation == "home":
        monkeypatch.setattr(machine, "_execute_grbl_homing_exchange", exchange)
    else:
        from laser_aligner.machine import job_focus
        original_move = job_focus.move
        def move(*args, **kwargs):
            result = original_move(*args, **kwargs)
            if kwargs.get("verify_only") and not worker.ident:
                active_thread[0] = threading.get_ident()
                worker.start()
                assert cooling_entered.wait(2)
            return result
        monkeypatch.setattr(job_focus, "move", move)
    try:
        if operation == "completion":
            machine.start_preflighted_program(machine.preflight_program(PROGRAM),
                                             authorization_phrase=machine.ARM_PHRASE)
            wait(machine)
            assert bool(machine._job.error) == stop_during_home
        elif stop_during_home:
            with pytest.raises(MachineError):
                machine.prepare_photo_position()
        else:
            machine.prepare_photo_position()
    finally:
        home_waiting.set()
        if worker.ident:
            worker.join(4)
    assert not worker.is_alive()
    assert not errors
    assert observations[0] == plan["session"][2]
    if stop_during_home:
        assert observations[-1] > observations[0]
        assert focus.state.job_plan is None
        assert not machine.armed
    else:
        assert focus.state.job_plan["id"] == plan["id"]
        assert focus.state.job_plan["parked_at_clearance"] is True
    assert not primary.laser_on
