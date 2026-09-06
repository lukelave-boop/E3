from __future__ import annotations

import json
import os
import threading
import time

import pytest
from test_controller_session import ResponsiveTransport, make_machine

from laser_aligner.machine import service as service_module
from laser_aligner.machine.controller_dialects import GRBL_DIALECT
from laser_aligner.machine.controller_receiver import ControllerReceiver
from laser_aligner.machine.io_diagnostics import IOProgress


def wait_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_snapshot_does_not_wait_for_metadata_or_receiver_owner():
    progress = IOProgress()
    receiver = ControllerReceiver(
        None, GRBL_DIALECT, on_failure=lambda _: None,
        on_idle=lambda _: None, owner_alive=lambda: True,
    )
    held = threading.Event()
    release = threading.Event()

    def owner():
        with progress._lock, receiver._condition:
            held.set()
            release.wait(3)

    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert held.wait(1)
        assert progress.snapshot() == {"snapshot_unavailable": "metadata_busy"}
        assert receiver.diagnostic_snapshot()["ownership"] == {
            "snapshot_unavailable": "condition_busy",
        }
        assert not release.is_set()
    finally:
        release.set()
        thread.join(2)


@pytest.mark.parametrize("snapshot_raises", [False, True])
def test_stream_timeout_evidence_precedes_abort_and_never_replaces_failure(
    monkeypatch, caplog, snapshot_raises,
):
    command = "G1 X2 Y2 F100"
    observations = []

    class Transport(ResponsiveTransport):
        def diagnostic_snapshot(self):
            observations.append((self.is_open, list(self.writes)))
            if snapshot_raises:
                raise ValueError("broken diagnostic provider")
            return {"open": self.is_open, "reader": {"phase": "select_wait"}}

    transport = Transport("timeout-evidence")
    machine, _ = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_JOB_COMMAND_ACK_TIMEOUT_SECONDS", 0.15)
    try:
        machine.connect()
        machine.prepare_job_start()
        session = machine._session
        program = machine.preflight_program(
            "G21\nG90\nM5\nG0 X1 Y1 F100\nM4 S10\n" + command + "\nM5\n"
        )
        machine.arm_program(machine.ARM_PHRASE, program)
        transport.set_next_timeout(command)
        machine.start_validated_program(program, "timeout.gcode")
        wait_until(lambda: session.diagnostics.snapshot()["ack_timeout_evidence"] is not None)
        wait_until(lambda: not machine.status()["job"]["running"])
        evidence = session.diagnostics.snapshot()["ack_timeout_evidence"]
        assert evidence["command"] == command
        assert evidence["generation"] == session.generation
        assert evidence["sequence"] == evidence["receiver"]["ownership"]["transaction"]
        assert evidence["receiver"]["stop_requested"] is False
        assert evidence["job"]["completed_lines"] < evidence["job"]["total_lines"]
        assert observations[0][0] is True
        assert observations[0][1][-1][1] == command
        assert sum(value == command for _, value in transport.writes) == 1
        if snapshot_raises:
            assert evidence["serial"] == {"snapshot_error": "ValueError"}
        else:
            assert evidence["serial"]["open"] is True
        assert "did not acknowledge" in machine.status()["job"]["error"]
        assert not machine.status()["coordinate_reference_ready"]
        assert not machine.status()["armed"]
        assert transport.closed.wait(2)
        assert not session.diagnostics.record_ack_timeout({"command": "cleanup M5"})
        assert session.diagnostics.snapshot()["ack_timeout_evidence"] == evidence
        messages = [r.message for r in caplog.records if "ACK timeout evidence=" in r.message]
        assert len(messages) == 1
        assert json.loads(messages[0].split("evidence=", 1)[1])["command"] == command
    finally:
        machine.disconnect()


def test_successful_ack_does_not_emit_timeout_evidence(monkeypatch, caplog):
    transport = ResponsiveTransport("successful-evidence")
    machine, _ = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        assert "ok" in machine.send_command("$G")
        assert machine._session.diagnostics.snapshot()["ack_timeout_evidence"] is None
        assert "ACK timeout evidence=" not in caplog.text
    finally:
        machine.disconnect()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX serial pseudoterminal")
def test_raw_diagnostics_distinguish_empty_polling_partial_and_framed_reply():
    import pty

    from laser_aligner.machine.serial_posix import PosixSerial

    master, slave = pty.openpty()
    serial = PosixSerial(os.ttyname(slave))
    try:
        serial.open()
        serial.write_line("G1 X2 Y2 F100")
        assert os.read(master, 1024) == b"G1 X2 Y2 F100\n"
        wait_until(lambda: serial.diagnostic_snapshot()["reader"]["counts"].get("select_empty", 0) >= 2)
        empty = serial.diagnostic_snapshot()
        assert empty["thread"]["alive"]
        assert empty["writer"]["accepted_bytes"] == 14
        assert "last_read_monotonic" not in empty["reader"]
        os.write(master, b"o")
        wait_until(lambda: serial.diagnostic_snapshot()["reader"].get("partial_bytes") == 1)
        partial = serial.diagnostic_snapshot()
        assert partial["reader"]["partial_hex"] == "6f"
        assert partial["reader"]["counts"].get("line_published", 0) == 0
        os.write(master, b"k\r\r\n")
        wait_until(lambda: serial.diagnostic_snapshot()["reader"]["counts"].get("line_published") == 1)
        # Observing diagnostics must not consume the acknowledgement.
        assert serial.read_line(1) == "ok"
        assert serial.diagnostic_snapshot()["reader"]["partial_bytes"] == 0
        serial.close()
        serial.open()
        assert "last_read_monotonic" not in serial.diagnostic_snapshot()["reader"]
    finally:
        serial.close()
        os.close(master)
        os.close(slave)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX serial pseudoterminal")
def test_blocked_select_is_visible_without_waiting_for_operational_lock(monkeypatch):
    import pty

    from laser_aligner.machine import serial_posix

    master, slave = pty.openpty()
    serial = serial_posix.PosixSerial(os.ttyname(slave))
    entered, release = threading.Event(), threading.Event()
    original_select = serial_posix.select.select

    def held_select(readers, writers, errors, timeout):
        if threading.current_thread().name == "serial-reader" and timeout == 0.1:
            entered.set()
            release.wait(3)
        return original_select(readers, writers, errors, timeout)

    monkeypatch.setattr(serial_posix.select, "select", held_select)
    try:
        serial.open()
        assert entered.wait(1)
        with serial._receive_lock, serial._write_lock:
            snapshot = serial.diagnostic_snapshot()
        assert snapshot["reader"]["phase"] == "select_wait"
        assert "select_empty" not in snapshot["reader"]["counts"]
        assert any("held_select" in location for location in snapshot["thread"]["stack"])
        assert snapshot["thread"]["native_id"] is not None
    finally:
        release.set()
        serial.close()
        os.close(master)
        os.close(slave)
