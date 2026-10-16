from __future__ import annotations

import queue
import threading
from types import SimpleNamespace

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine import controller_receiver as receiver_module
from laser_aligner.machine.controller_dialects import GRBL_DIALECT
from laser_aligner.machine.controller_receiver import ControllerReceiver


class StreamTransport:
    is_open = True

    def __init__(self):
        self.input: queue.Queue[str | Exception] = queue.Queue()
        self.writes: list[int] = []
        self.positive_reads: list[float] = []
        self.worker_read = threading.Event()
        self.immediate_ack = False

    def read_line(self, timeout):
        if threading.current_thread().name == "primary-controller-receiver":
            self.worker_read.set()
        if timeout > 0:
            self.positive_reads.append(timeout)
        try:
            response = self.input.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        if isinstance(response, Exception):
            raise response
        return response

    def write(self, sequence):
        self.writes.append(sequence)
        if self.immediate_ack:
            self.input.put("ok")


@pytest.fixture
def stream():
    transport = StreamTransport()
    idle: list[str] = []
    failures: list[str] = []
    receiver = ControllerReceiver(
        transport, GRBL_DIALECT, on_failure=failures.append,
        on_idle=idle.append, owner_alive=lambda: True,
    )
    yield receiver, transport, idle, failures
    receiver.stop()
    if receiver._thread is not None:
        receiver._thread.join(1)
        assert not receiver._thread.is_alive()


def write(receiver, transport, sequence, byte_count=20, capacity=127):
    return receiver.stream_write(
        1, sequence, byte_count, lambda: transport.write(sequence), capacity=capacity,
    )


def test_fifo_credit_is_reserved_until_ack_is_consumed(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    assert receiver.has_transaction
    assert write(receiver, transport, 1, 50)
    assert write(receiver, transport, 2, 50)
    transport.input.put("ok")
    # Admission observes the ACK, but only its consumer releases byte credit.
    assert not write(receiver, transport, 3, 50)
    assert receiver.diagnostic_snapshot()["ownership"]["stream_bytes"] == 100
    assert receiver.stream_read_ack(1, 0) == (1, "ok")
    assert write(receiver, transport, 3, 50)
    transport.input.put("ok")
    transport.input.put("ok")
    assert receiver.stream_read_ack(1, 0) == (2, "ok")
    assert receiver.stream_read_ack(1, 0) == (3, "ok")
    receiver.finish_stream(1, quiet_interval=0, timeout=0)
    assert not receiver.has_transaction
    assert transport.writes == [1, 2, 3]


def test_exact_capacity_includes_newline_and_never_partially_admits(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    assert write(receiver, transport, 1, 63)
    assert write(receiver, transport, 2, 64)
    assert not write(receiver, transport, 3, 1)
    assert transport.writes == [1, 2]


def test_outstanding_command_count_is_bounded_even_for_tiny_lines(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    for sequence in range(1, 17):
        assert write(receiver, transport, sequence, 1)
    transport.input.put("ok")
    assert not write(receiver, transport, 17, 1)
    assert receiver.stream_read_ack(1, 0) == (1, "ok")
    assert write(receiver, transport, 17, 1)


@pytest.mark.parametrize("capacity,byte_count", [(0, 1), (128, 1), (True, 1), (127, 128), (127, 0), (127, True)])
def test_invalid_capacity_or_byte_length_never_writes(stream, capacity, byte_count):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    with pytest.raises(MachineError):
        write(receiver, transport, 1, byte_count, capacity)
    assert transport.writes == []
    assert receiver.failure


@pytest.mark.parametrize("response", [
    "error:2", "ALARM:1", "Grbl 1.1h ['$' for help]", "ok extra",
    "[GC:G1 G90]", "<Hold:0|MPos:0,0,0>", "<Door:0|MPos:0,0,0>",
    "<Alarm|MPos:0,0,0>", "<Sleep|MPos:0,0,0>",
])
def test_queued_fatal_frame_latches_before_another_physical_write(stream, response):
    receiver, transport, idle, _failures = stream
    receiver.begin_stream(1)
    assert write(receiver, transport, 1)
    transport.input.put(response)
    with pytest.raises(MachineError):
        write(receiver, transport, 2)
    assert transport.writes == [1]
    assert receiver.failure
    assert response in idle


def test_read_failure_latches_before_another_physical_write(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    assert write(receiver, transport, 1)
    transport.input.put(OSError("USB disappeared"))
    with pytest.raises(MachineError, match="USB disappeared"):
        write(receiver, transport, 2)
    assert transport.writes == [1]
    assert receiver.failure


def test_stale_ack_before_stream_cannot_be_owned_by_first_line(stream):
    receiver, transport, _idle, _failures = stream
    transport.input.put("ok")
    with pytest.raises(MachineError, match="unowned"):
        receiver.begin_stream(1)
    assert transport.writes == []


def test_excess_ack_is_rejected_before_credit_can_admit_another_line(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    assert write(receiver, transport, 1)
    transport.input.put("ok")
    transport.input.put("ok")
    with pytest.raises(MachineError, match="unowned streaming acknowledgement"):
        write(receiver, transport, 2)
    assert transport.writes == [1]


def test_benign_status_and_diagnostics_do_not_release_credit(stream):
    receiver, transport, idle, _failures = stream
    receiver.begin_stream(1)
    assert write(receiver, transport, 1, 127)
    frames = ["<Run|MPos:0,0,0>", "I (42) gpio: bounded controller diagnostic"]
    for frame in frames:
        transport.input.put(frame)
    assert receiver.stream_read_ack(1, 0) is None
    assert not write(receiver, transport, 2)
    assert idle == frames
    transport.input.put("ok")
    assert receiver.stream_read_ack(1, 0) == (1, "ok")


@pytest.mark.parametrize("consume_ack", [False, True])
def test_finish_rejects_pending_or_observed_but_unconsumed_ack(stream, consume_ack):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    assert write(receiver, transport, 1)
    if consume_ack:
        transport.input.put("ok")
    with pytest.raises(MachineError, match="all acknowledgements consumed"):
        receiver.finish_stream(1, quiet_interval=0, timeout=0)


def test_write_failure_is_not_retried_and_retires_stream(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)

    def partial_write():
        transport.writes.append(1)
        raise OSError("partial USB write")

    with pytest.raises(OSError, match="partial USB write"):
        receiver.stream_write(1, 1, 20, partial_write)
    with pytest.raises(MachineError, match="partial USB write"):
        write(receiver, transport, 2)
    assert transport.writes == [1]


def test_stop_revokes_pending_stream_and_wakes_paused_worker(stream):
    receiver, transport, _idle, failures = stream
    receiver.start()
    receiver.begin_stream(1)
    assert write(receiver, transport, 1)
    receiver.stop()
    transport.input.put("ok")
    with pytest.raises(MachineError, match="retired"):
        write(receiver, transport, 2)
    with pytest.raises(MachineError, match="retired"):
        receiver.stream_read_ack(1, 0)
    receiver._thread.join(1)
    assert not receiver._thread.is_alive()
    assert transport.writes == [1]
    assert failures == []


def test_stop_latched_during_admission_drain_prevents_physical_write(stream, monkeypatch):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    original_drain = receiver._drain_stream_input

    def retire_after_drain():
        status_received = original_drain()
        receiver.stop()
        return status_received

    monkeypatch.setattr(receiver, "_drain_stream_input", retire_after_drain)
    with pytest.raises(MachineError, match="retired"):
        write(receiver, transport, 1)
    assert transport.writes == []
    assert receiver.diagnostic_snapshot()["ownership"]["stream_bytes"] == 0


def test_stream_owner_pumps_without_worker_polling_or_per_line_quiet(stream):
    receiver, transport, _idle, _failures = stream
    receiver.start()
    receiver.begin_stream(1)
    transport.worker_read.clear()
    transport.positive_reads.clear()
    transport.immediate_ack = True
    for sequence in range(1, 101):
        assert write(receiver, transport, sequence)
        assert receiver.stream_read_ack(1, 0) == (sequence, "ok")
    assert not transport.worker_read.is_set()
    assert transport.positive_reads == []
    receiver.finish_stream(1, quiet_interval=0.01, timeout=0.1)
    assert transport.positive_reads
    assert transport.worker_read.wait(1)
    assert transport.writes == list(range(1, 101))


def test_finish_quiet_fence_rejects_late_unowned_ack(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    original_read = transport.read_line

    def late_ack(timeout):
        return "ok" if timeout > 0 else original_read(timeout)

    transport.read_line = late_ack
    with pytest.raises(MachineError, match="unowned streaming acknowledgement"):
        receiver.finish_stream(1, quiet_interval=0.01, timeout=0.1)


def test_status_at_final_queue_check_restarts_the_quiet_fence(stream, monkeypatch):
    receiver, transport, idle, _failures = stream
    elapsed = 0.0
    delivered = False
    positive_waits = []

    def read(timeout):
        nonlocal elapsed, delivered
        if timeout > 0:
            positive_waits.append(timeout)
            elapsed += timeout
        elif elapsed >= 0.01 and not delivered:
            delivered = True
            return "<Run|MPos:0,0,0>"
        return None

    monkeypatch.setattr(receiver_module, "time", SimpleNamespace(monotonic=lambda: elapsed))
    transport.read_line = read
    receiver.begin_stream(1)
    receiver.finish_stream(1, quiet_interval=0.01, timeout=0.1)
    assert idle == ["<Run|MPos:0,0,0>"]
    assert sum(positive_waits) == pytest.approx(0.02)


def test_stop_interrupts_a_long_stream_ack_wait_without_another_write(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    assert write(receiver, transport, 1)
    reading = threading.Event()
    errors = []
    original_read = transport.read_line

    def read(timeout):
        if timeout > 0:
            reading.set()
        return original_read(timeout)

    def wait_for_ack():
        try:
            receiver.stream_read_ack(1, 10)
        except MachineError as exc:
            errors.append(str(exc))

    transport.read_line = read
    waiter = threading.Thread(target=wait_for_ack)
    waiter.start()
    try:
        assert reading.wait(1)
        receiver.stop()
        waiter.join(1)
        assert not waiter.is_alive()
        assert errors == ["Controller receive session was retired"]
        assert transport.writes == [1]
    finally:
        receiver.stop()
        waiter.join(1)


def test_fault_callback_runs_outside_ingress_and_stream_cannot_resume(stream):
    receiver, transport, _idle, _failures = stream
    callback = threading.Event()
    outside_ingress = []

    def failed(_detail):
        acquired = receiver._ingress.acquire(blocking=False)
        outside_ingress.append(acquired)
        if acquired:
            receiver._ingress.release()
        callback.set()

    receiver._on_failure = failed
    receiver.start()
    receiver.begin_stream(1)
    transport.input.put("ALARM:1")
    with pytest.raises(MachineError):
        write(receiver, transport, 1)
    assert callback.wait(1)
    assert outside_ingress == [True]
    assert transport.writes == []


def test_normal_transaction_cannot_interleave_a_stream(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    with pytest.raises(MachineError, match="already has an owner"):
        receiver.begin(2, lambda: transport.write(2))
    assert transport.writes == []


def test_normal_transaction_resumes_after_clean_stream_fence(stream):
    receiver, transport, _idle, _failures = stream
    receiver.begin_stream(1)
    receiver.finish_stream(1, quiet_interval=0, timeout=0)
    receiver.begin(2, lambda: transport.write(2))
    transport.input.put("ok")
    assert receiver.read_line(0) == "ok"
    receiver.end(2)
    assert not receiver.has_transaction
