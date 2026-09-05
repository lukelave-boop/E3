from __future__ import annotations

import queue
import threading
from collections.abc import Callable

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.controller_dialects import GRBL_DIALECT
from laser_aligner.machine.controller_receiver import ControllerReceiver


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
