from __future__ import annotations

import queue
import threading
from collections.abc import Callable

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.controller_dialects import GRBL_DIALECT
from laser_aligner.machine.controller_receiver import ControllerReceiver
from laser_aligner.machine.job_pause import JobPause


class _QueuedTransport:
    is_open = True

    def __init__(self) -> None:
        self.input: queue.Queue[str | Exception] = queue.Queue()
        self.item_read = threading.Event()

    def read_line(self, timeout: float) -> str | None:
        try:
            item = self.input.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        self.item_read.set()
        if isinstance(item, Exception):
            raise item
        return item


def _receiver(
    transport: _QueuedTransport,
    *,
    on_failure: Callable[[str], None],
) -> ControllerReceiver:
    return ControllerReceiver(
        transport,
        GRBL_DIALECT,
        on_failure=on_failure,
        on_idle=lambda _line: None,
        owner_alive=lambda: True,
    )


def _join_receiver(receiver: ControllerReceiver) -> None:
    receiver.stop()
    if receiver._thread is not None:
        receiver._thread.join(2.0)
        assert not receiver._thread.is_alive()


@pytest.mark.parametrize("outcome", ["ready", "unowned", "retired"])
def test_pending_admission_gets_next_ingress_turn_without_losing_fault_checks(outcome):
    polling = threading.Event()
    admission_waiting = threading.Event()
    release_admission = threading.Event()
    wrote = threading.Event()
    errors = []

    class Transport(_QueuedTransport):
        def read_line(self, timeout):
            if timeout > 0:
                polling.set()
            return super().read_line(timeout)

    transport = Transport()
    receiver = _receiver(transport, on_failure=lambda _: None)
    lock = threading.Lock()

    class DelayedAdmissionLock:
        def __enter__(self):
            if threading.current_thread().name == "test-admission":
                admission_waiting.set()
                assert release_admission.wait(2)
            lock.acquire()

        def __exit__(self, *args):
            lock.release()

    receiver._ingress = DelayedAdmissionLock()

    def admit():
        try:
            receiver.begin(1, wrote.set)
        except MachineError as exc:
            errors.append(str(exc))

    consumer = threading.Thread(target=admit, name="test-admission")
    consumer.start()
    try:
        assert admission_waiting.wait(1)
        receiver.start()
        # Admission has registered but has not acquired ingress. The worker
        # must not jump ahead and begin another blocking transport read.
        assert not polling.wait(0.05)
        if outcome == "unowned":
            transport.input.put("ok")
        elif outcome == "retired":
            receiver.stop()
        release_admission.set()
        consumer.join(2)
        assert not consumer.is_alive()
        assert wrote.is_set() is (outcome == "ready")
        assert bool(errors) is (outcome != "ready")
    finally:
        release_admission.set()
        consumer.join(2)
        _join_receiver(receiver)


@pytest.mark.parametrize(
    ("incoming", "detail"),
    [
        ("ALARM:1", "ALARM:1"),
        ("Grbl 1.1h ['$' for help]", "restarted"),
        ("<Alarm|MPos:0.000,0.000,0.000>", "state invalidated"),
        (OSError("USB disappeared"), "USB disappeared"),
    ],
    ids=["alarm", "restart", "alarm-status", "read-failure"],
)
def test_fatal_input_latches_before_a_later_write_can_be_admitted(
    monkeypatch: pytest.MonkeyPatch,
    incoming: str | Exception,
    detail: str,
) -> None:
    """Pause between consuming fatal input and publishing its fault latch.

    Releasing ingress before latching used to allow a later motion command to
    pass admission after the fatal frame had already been removed from RX.
    """
    transport = _QueuedTransport()
    latch_entered = threading.Event()
    release_latch = threading.Event()
    admission_attempted = threading.Event()
    wrote = threading.Event()
    failure_published = threading.Event()
    latch_owned_ingress: list[bool] = []
    callback_outside_ingress: list[bool] = []
    failures: list[str] = []
    admission_errors: list[str] = []

    def failed(reason: str) -> None:
        # A rejected admission may briefly own ingress on another thread. The
        # callback must be able to acquire it once that admission unwinds.
        acquired = receiver._ingress.acquire(timeout=1.0)
        callback_outside_ingress.append(acquired)
        if acquired:
            receiver._ingress.release()
        failures.append(reason)
        failure_published.set()

    receiver = _receiver(transport, on_failure=failed)
    original_latch = receiver._latch_failure

    def pause_first_latch(reason: str) -> None:
        if not latch_entered.is_set():
            acquired = receiver._ingress.acquire(blocking=False)
            latch_owned_ingress.append(not acquired)
            if acquired:
                receiver._ingress.release()
            latch_entered.set()
            assert release_latch.wait(2.0)
        original_latch(reason)

    monkeypatch.setattr(receiver, "_latch_failure", pause_first_latch)

    def admit_write() -> None:
        admission_attempted.set()
        try:
            receiver.begin(1, wrote.set)
        except MachineError as exc:
            admission_errors.append(str(exc))

    transport.input.put(incoming)
    worker = threading.Thread(target=admit_write)
    receiver.start()
    try:
        assert latch_entered.wait(1.0)
        worker.start()
        assert admission_attempted.wait(1.0)
        assert not wrote.wait(0.05)
        release_latch.set()
        worker.join(2.0)
        assert not worker.is_alive()
        assert failure_published.wait(1.0)
        assert latch_owned_ingress == [True]
        assert callback_outside_ingress == [True]
        assert len(failures) == 1 and detail in failures[0]
        assert len(admission_errors) == 1 and detail in admission_errors[0]
        assert not wrote.is_set()
    finally:
        release_latch.set()
        if worker.ident is not None:
            worker.join(2.0)
        _join_receiver(receiver)


def test_admission_and_write_keep_one_receive_boundary_for_immediate_ack() -> None:
    transport = _QueuedTransport()
    failures: list[str] = []
    receiver = _receiver(transport, on_failure=failures.append)
    write_entered = threading.Event()
    finish_write = threading.Event()
    write_errors: list[Exception] = []

    def write() -> None:
        transport.input.put("ok")
        write_entered.set()
        assert finish_write.wait(2.0)

    def admit_write() -> None:
        try:
            receiver.begin(3, write)
        except Exception as exc:
            write_errors.append(exc)

    worker = threading.Thread(target=admit_write)
    worker.start()
    try:
        assert write_entered.wait(1.0)
        receiver.start()
        # The ACK exists on RX but dispatch cannot overtake the admitted write.
        assert not transport.item_read.wait(0.05)
        finish_write.set()
        worker.join(2.0)
        assert not worker.is_alive()
        assert receiver.read_line(timeout=1.0) == "ok"
        receiver.end(3)
        assert not receiver.has_transaction
        assert not write_errors
        assert not failures
    finally:
        finish_write.set()
        worker.join(2.0)
        _join_receiver(receiver)


def test_retired_receiver_does_not_report_its_delayed_close_as_a_new_failure() -> None:
    entered_read = threading.Event()
    close_read = threading.Event()

    class ClosingTransport(_QueuedTransport):
        def read_line(self, timeout: float) -> str | None:
            del timeout
            entered_read.set()
            assert close_read.wait(2.0)
            raise OSError("closed during session retirement")

    failures: list[str] = []
    receiver = _receiver(ClosingTransport(), on_failure=failures.append)
    receiver.start()
    try:
        assert entered_read.wait(1.0)
        receiver.stop()
        close_read.set()
        _join_receiver(receiver)
        assert not failures
        wrote = threading.Event()
        with pytest.raises(MachineError):
            receiver.begin(4, wrote.set)
        assert not wrote.is_set()
    finally:
        close_read.set()
        _join_receiver(receiver)


@pytest.mark.parametrize("terminal_reply", ["ok", "error:2"])
def test_suspended_telemetry_preserves_ordinary_replies_without_filling_queue(terminal_reply):
    transport = _QueuedTransport()
    pause = JobPause()
    recorded = []
    receiver = ControllerReceiver(
        transport, GRBL_DIALECT, on_failure=lambda _: None,
        on_idle=recorded.append, owner_alive=lambda: True, on_realtime=pause.observe,
    )
    receiver.begin(1, lambda: None)
    pause.request(resume=False)
    status = "<Door:0|MPos:1,1,0|FS:0,0>"
    replies = ("$1=255", "[GC:G0 G54 G17 G21 G90 G94 M5]", terminal_reply)

    def receive(line):
        transport.input.put(line)
        receiver.poll_suspended(timeout=0)

    for index in range(1024):
        if index in (100, 500, 900):
            receive(replies[(100, 500, 900).index(index)])
        receive(status)
    assert pause.state == "paused"
    assert receiver.failure is None
    assert receiver.diagnostic_snapshot()["ownership"]["pending_lines"] == len(replies)
    assert recorded == [status] * 1024

    pause.request(resume=True)
    resumed = "<Run|MPos:1,1,0|FS:0,0>"
    receive(resumed)
    assert pause.state == "running"
    assert [receiver.read_line(timeout=0) for _ in range(4)] == [*replies, resumed]
    receiver.end(1)
    assert not receiver.has_transaction
    assert receiver.failure is None


@pytest.mark.parametrize("frame,detail", [
    ("<Door:0|MPos:1,1,0", "malformed"),
    ("<Door:0|MPos:1,1,0|A:S>", "spindle"),
    ("<Door:1|MPos:1,1,0>", "door state changed"),
    ("<Run|MPos:1,1,0>", "without an explicit Resume"),
    ("ALARM:1", "alarm"),
    ("Grbl 1.1h ['$' for help]", "restarted"),
])
def test_suspended_telemetry_still_latches_faults_before_reply_delivery(frame, detail):
    transport = _QueuedTransport()
    pause = JobPause()
    recorded = []
    receiver = ControllerReceiver(
        transport, GRBL_DIALECT, on_failure=lambda _: None,
        on_idle=recorded.append, owner_alive=lambda: True, on_realtime=pause.observe,
    )
    receiver.begin(1, lambda: None)
    pause.request(resume=False)
    transport.input.put("<Door:0|MPos:1,1,0|FS:0,0>")
    receiver.poll_suspended(timeout=0)
    transport.input.put("ok")
    receiver.poll_suspended(timeout=0)
    transport.input.put(frame)
    with pytest.raises(MachineError, match=detail):
        receiver.poll_suspended(timeout=0)
    assert recorded[-1] == frame
    with pytest.raises(MachineError, match=detail):
        receiver.read_line(timeout=0)


def test_suspended_ordinary_reply_queue_keeps_its_existing_bound():
    transport = _QueuedTransport()
    pause = JobPause()
    receiver = ControllerReceiver(
        transport, GRBL_DIALECT, on_failure=lambda _: None,
        on_idle=lambda _: None, owner_alive=lambda: True, on_realtime=pause.observe,
    )
    receiver.begin(1, lambda: None)
    pause.request(resume=False)
    for _ in range(512):
        transport.input.put("$1=255")
        receiver.poll_suspended(timeout=0)
    transport.input.put("$1=255")
    with pytest.raises(MachineError, match="exceeded its bounded queue"):
        receiver.poll_suspended(timeout=0)
