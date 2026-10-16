"""Stream ownership over real POSIX serial I/O and a fake PTY controller.

These tests open only a newly created pseudoterminal. They do not discover or
open hardware and do not establish physical feed, output or STOP performance.
"""

from __future__ import annotations

import os
import select
import threading
import time

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine.controller_dialects import GRBL_DIALECT
from laser_aligner.machine.controller_receiver import ControllerReceiver

pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="requires POSIX serial pseudoterminals and termios",
)


@pytest.fixture
def pty_stream():
    import pty

    from laser_aligner.machine.serial_posix import PosixSerial

    master, slave = pty.openpty()
    serial = PosixSerial(os.ttyname(slave), 115200)
    failures = []
    receiver = ControllerReceiver(
        serial, GRBL_DIALECT, on_failure=failures.append,
        on_idle=lambda _line: None, owner_alive=lambda: True,
    )
    try:
        serial.open()
        receiver.start()
        yield master, serial, receiver, failures
    finally:
        receiver.stop()
        if receiver._thread is not None:
            receiver._thread.join(2)
            assert not receiver._thread.is_alive()
        serial.close()
        os.close(master)
        os.close(slave)


def read_exact(master, count, timeout=2.0):
    received = bytearray()
    deadline = time.monotonic() + timeout
    while len(received) < count:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "fake PTY controller did not receive the complete write"
        readable, _, _ = select.select([master], [], [], remaining)
        assert readable, "fake PTY controller did not receive the complete write"
        received.extend(os.read(master, count - len(received)))
    return bytes(received)


def wait_for_queued(serial, count):
    deadline = time.monotonic() + 2
    while serial._queue.qsize() < count and time.monotonic() < deadline:
        time.sleep(0.002)
    assert serial._queue.qsize() >= count, "raw PTY reader did not queue the controller reply"


def test_real_serial_fifo_round_trip_and_credit_accounting(pty_stream):
    master, serial, receiver, failures = pty_stream
    receiver.begin_stream(1)
    commands = ["G1X10Y10F6000S0", "X10.1S100", "X10.2S0"]
    capacity = sum(len(command) + 1 for command in commands)
    for sequence, command in enumerate(commands, 1):
        assert receiver.stream_write(
            1, sequence, len(command) + 1,
            lambda command=command: serial.write_line(command), capacity=capacity,
        )
    assert not receiver.stream_write(1, 4, 1, lambda: pytest.fail("window overrun"), capacity=capacity)
    expected = ("\n".join(commands) + "\n").encode("ascii")
    assert read_exact(master, len(expected)) == expected
    os.write(master, b"<Run|MPos:10,10,0>\r\nok\r\nok\r\nok\r\n")
    assert [receiver.stream_read_ack(1, 1) for _ in commands] == [(1, "ok"), (2, "ok"), (3, "ok")]
    assert receiver.diagnostic_snapshot()["ownership"]["stream_bytes"] == 0
    receiver.finish_stream(1, quiet_interval=0.01, timeout=1)
    assert not receiver.has_transaction
    assert failures == []


def test_real_serial_stream_pump_does_not_wait_for_raw_reader_select(pty_stream, monkeypatch):
    from laser_aligner.machine import serial_posix

    master, serial, receiver, failures = pty_stream
    blocked, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_select = serial_posix.select.select
    errors = []
    command = "G1X10Y10F6000S0"

    def held_select(readers, writers, exceptional, timeout):
        if threading.current_thread().name == "serial-reader" and timeout == 0.1:
            blocked.set()
            release.wait(3)
            return [], [], []
        return original_select(readers, writers, exceptional, timeout)

    def pump():
        try:
            receiver.begin_stream(1)
            assert receiver.stream_write(1, 1, len(command) + 1, lambda: serial.write_line(command))
            assert receiver.stream_read_ack(1, 0) is None
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    monkeypatch.setattr(serial_posix.select, "select", held_select)
    producer = threading.Thread(target=pump)
    try:
        assert blocked.wait(1)
        producer.start()
        assert finished.wait(1), "stream pump blocked behind the raw reader's readiness wait"
        assert errors == []
        assert not release.is_set()
        assert read_exact(master, len(command) + 1) == (command + "\n").encode("ascii")
    finally:
        release.set()
        if producer.ident is not None:
            producer.join(2)
        monkeypatch.setattr(serial_posix.select, "select", original_select)
    os.write(master, b"ok\n")
    assert receiver.stream_read_ack(1, 1) == (1, "ok")
    receiver.finish_stream(1, quiet_interval=0.01, timeout=1)
    assert failures == []


@pytest.mark.parametrize("reply,frames", [
    (b"error:2\n", 1),
    (b"ALARM:1\n", 1),
    (b"Grbl 1.1h ['$' for help]\n", 1),
    (b"ok extra\n", 1),
    (b"ok\nok\n", 2),
])
def test_real_serial_queued_fault_or_excess_ack_prevents_next_write(pty_stream, reply, frames):
    master, serial, receiver, _failures = pty_stream
    receiver.begin_stream(1)
    command = "G1X10Y10F6000S0"
    assert receiver.stream_write(1, 1, len(command) + 1, lambda: serial.write_line(command))
    assert read_exact(master, len(command) + 1) == (command + "\n").encode("ascii")
    os.write(master, reply)
    wait_for_queued(serial, frames)
    with pytest.raises(MachineError):
        receiver.stream_write(1, 2, 7, lambda: serial.write_line("X10.1"))
    assert receiver.failure
    assert select.select([master], [], [], 0.05)[0] == []


def test_stream_ack_wait_does_not_block_independent_stop_bytes(pty_stream):
    master, serial, receiver, failures = pty_stream
    receiver.begin_stream(1)
    command = "G1X10Y10F6000S0"
    assert receiver.stream_write(1, 1, len(command) + 1, lambda: serial.write_line(command))
    read_exact(master, len(command) + 1)
    reading = threading.Event()
    original_read = serial.read_line
    errors = []

    def read_line(timeout=1.0):
        if threading.current_thread().name == "stream-ack-waiter" and timeout > 0:
            reading.set()
        return original_read(timeout)

    def wait_for_ack():
        try:
            receiver.stream_read_ack(1, 10)
        except MachineError as exc:
            errors.append(str(exc))

    serial.read_line = read_line
    waiter = threading.Thread(target=wait_for_ack, name="stream-ack-waiter")
    waiter.start()
    try:
        assert reading.wait(1)
        receiver.stop()
        # Model the existing independent realtime abort transport path. This
        # cannot prove firmware interruption or physical stopping behavior.
        serial.write_raw(b"!\x18")
        assert read_exact(master, 2) == b"!\x18"
        waiter.join(1)
        assert not waiter.is_alive()
        assert errors == ["Controller receive session was retired"]
        with pytest.raises(MachineError, match="retired"):
            receiver.stream_write(1, 2, 7, lambda: serial.write_line("X10.1"))
        assert failures == []
    finally:
        receiver.stop()
        waiter.join(2)


def test_real_serial_disconnect_invalidates_stream_before_next_write(pty_stream):
    _master, serial, receiver, _failures = pty_stream
    receiver.begin_stream(1)
    serial.close()
    with pytest.raises(MachineError, match="closed unexpectedly"):
        receiver.stream_write(1, 1, 7, lambda: pytest.fail("write on closed serial"))
    assert receiver.failure
