from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Callable

import pytest
from test_controller_session import ResponsiveTransport, make_machine

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine import service as service_module
from laser_aligner.machine.controller_receiver import ControllerReceiver

POWERED_PROGRAM = (
    "G21\nG90\nM5\nG0 X1 Y1 F100\nM4 S10\nG1 X2 Y2 F100\nM5\n"
)


def wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "Primary session event did not settle before the deadline"


class EventTransport(ResponsiveTransport):
    """A controller peer whose asynchronous failures do not need an RPC."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.reader_fault: str | None = None
        self.fault_observed = threading.Event()
        self.events: list[tuple[str, str | bytes]] = []

    @property
    def fault(self) -> str | None:
        return self.reader_fault

    def raise_if_faulted(self) -> None:
        if self.reader_fault is not None:
            self.fault_observed.set()
            raise MachineError(self.reader_fault)

    def read_line(self, timeout: float = 1.0) -> str | None:
        if self.reader_fault is not None:
            self.fault_observed.set()
            raise MachineError(self.reader_fault)
        return super().read_line(timeout)

    def write_line(self, line: str) -> None:
        self.events.append(("line", line))
        super().write_line(line)

    def write_raw(self, data: bytes) -> None:
        self.events.append(("raw", data))
        super().write_raw(data)

    def close(self) -> None:
        self.events.append(("close", ""))
        super().close()


@pytest.mark.parametrize(
    "frame",
    ["ALARM:1", "<Alarm|MPos:0,0,0|WCO:0,0,0>", "Grbl 1.1h ['$' for help]"],
    ids=["alarm-line", "alarm-status", "controller-restart"],
)
def test_idle_controller_event_revokes_home_reference_without_a_command(
    monkeypatch: pytest.MonkeyPatch,
    frame: str,
) -> None:
    transport = EventTransport("idle-event")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        assert machine.status()["controller_state"] == "READY_MOTION"
        # Keep the failed generation available for inspection, without launching
        # communication recovery that would otherwise immediately replace it.
        transport.synchronize_input = None  # type: ignore[method-assign]
        transport.queue_response(frame)

        wait_until(lambda: not machine.status()["coordinate_reference_ready"])

        status = machine.status()
        assert status["controller_state"] != "READY_MOTION"
        assert status["armed"] is False
        program = machine.preflight_program(POWERED_PROGRAM)
        with pytest.raises((MachineError, SafetyError)):
            machine.arm_program(machine.ARM_PHRASE, program)
    finally:
        machine.disconnect()


def test_idle_transport_fault_revokes_reference_without_status_consuming_rx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("idle-reader-fault")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        transport.synchronize_input = None  # type: ignore[method-assign]
        transport.reader_fault = "Serial read failed"

        assert transport.fault_observed.wait(2.0)
        wait_until(lambda: not machine.status()["coordinate_reference_ready"])

        status = machine.status()
        assert status["controller_state"] != "READY_MOTION"
        assert status["armed"] is False
    finally:
        machine.disconnect()


def test_idle_telemetry_and_firmware_diagnostic_preserve_held_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("benign-idle-events")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        generation = machine.status()["controller_session_generation"]
        transport.queue_response(
            "<Idle|MPos:0,0,0|WCO:0,0,0>",
            "I (42) gpio: bounded controller diagnostic",
        )

        # A real subsequent exchange also proves that the benign events did not
        # steal its acknowledgement or poison the input boundary.
        assert "ok" in machine.send_command("$I")

        status = machine.status()
        assert status["controller_state"] == "READY_MOTION"
        assert status["coordinate_reference_ready"] is True
        assert status["controller_session_generation"] == generation
        assert transport.step_idle_delay_ms == 255
    finally:
        machine.disconnect()


def test_queued_idle_ack_cannot_acknowledge_a_silent_new_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("stale-idle-ack")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        transport.synchronize_input = None  # type: ignore[method-assign]
        transport.queue_response("ok")
        transport.set_next_timeout("M5")

        with pytest.raises(MachineError):
            machine.send_command("M5", timeout=0.15)

        wait_until(lambda: not machine.status()["coordinate_reference_ready"])
        assert machine.status()["controller_state"] != "READY_MOTION"
    finally:
        machine.disconnect()


def test_transaction_alarm_revokes_reference_and_blocks_new_arming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("owned-alarm")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        transport.synchronize_input = None  # type: ignore[method-assign]
        program = machine.preflight_program(POWERED_PROGRAM)
        transport.set_next_reply("$I", "ALARM:1")

        with pytest.raises(MachineError):
            machine.send_command("$I")

        wait_until(lambda: not machine.status()["coordinate_reference_ready"])
        with pytest.raises((MachineError, SafetyError)):
            machine.arm_program(machine.ARM_PHRASE, program)
        assert machine.status()["armed"] is False
    finally:
        machine.disconnect()


def test_powered_job_protocol_fault_attempts_realtime_abort_before_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("powered-fault")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        transport.synchronize_input = None  # type: ignore[method-assign]
        # The powered move is accepted by the fake before malformed framing is
        # observed. M5 alone cannot establish immediate planner cancellation.
        transport.set_next_reply("G1 X2 Y2 F100", "ok", " ok")
        program = machine.preflight_program(POWERED_PROGRAM)
        before_start = len(transport.events)

        machine.start_preflighted_program(
            program,
            "protocol-fault.gcode",
            authorization_phrase=machine.ARM_PHRASE,
        )
        wait_until(lambda: not machine.status()["job"]["running"])

        status = machine.status()
        assert status["job"]["error"] is not None
        assert "malformed" in status["job"]["error"].lower()
        assert status["coordinate_reference_ready"] is False
        # The failed job is published independently of bounded asynchronous
        # cleanup. Wait for that cleanup before inspecting its wire ordering.
        assert transport.closed.wait(1.0)
        events = transport.events[before_start:]
        reset_index = next(
            index
            for index, (kind, value) in enumerate(events)
            if kind == "raw" and isinstance(value, bytes) and b"\x18" in value
        )
        close_index = next(
            index for index, (kind, _value) in enumerate(events) if kind == "close"
        )
        assert reset_index < close_index
        assert any(
            kind == "line" and value == "M5"
            for kind, value in events[reset_index + 1 : close_index]
        )
        assert status["last_successful_job"] is None
    finally:
        machine.disconnect()


def test_old_session_event_cannot_revoke_a_recovered_and_homed_successor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = EventTransport("previous-session")
    replacement = EventTransport("replacement-session")
    machine, _factory = make_machine(monkeypatch, previous, replacement)
    try:
        machine.connect()
        machine.prepare_job_start()
        previous_generation = machine.status()["controller_session_generation"]
        machine.request_stop()
        wait_until(
            lambda: machine.status()["controller_session_generation"]
            > previous_generation
            and machine.status()["controller_state"] == "READY_HOME_REQUIRED"
        )
        machine.prepare_job_start()
        replacement_generation = machine.status()["controller_session_generation"]
        previous.queue_response("ALARM:1", "Grbl 1.1h ['$' for help]", "ok")
        previous.reader_fault = "Late failure on the retired transport"

        assert "ok" in machine.send_command("$I")

        status = machine.status()
        assert status["controller_session_generation"] == replacement_generation
        assert status["controller_state"] == "READY_MOTION"
        assert status["coordinate_reference_ready"] is True
        assert not replacement.closed.is_set()
    finally:
        machine.disconnect()


def test_successful_powered_jobs_retain_held_motion_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("successive-powered-jobs")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        generation = machine.status()["controller_session_generation"]
        program = machine.preflight_program(POWERED_PROGRAM)
        for index in range(2):
            machine.start_preflighted_program(
                program,
                f"held-job-{index}.gcode",
                authorization_phrase=machine.ARM_PHRASE,
            )
            wait_until(lambda: not machine.status()["job"]["running"])
            status = machine.status()
            assert status["job"]["error"] is None
            assert status["controller_state"] == "READY_MOTION"
            assert status["coordinate_reference_ready"] is True
            assert status["controller_session_generation"] == generation
            assert transport.step_idle_delay_ms == 255
            assert status["armed"] is False
        assert not any(
            kind == "raw" and isinstance(value, bytes) and b"\x18" in value
            for kind, value in transport.events
        )
    finally:
        machine.disconnect()


@pytest.mark.parametrize("action", ["stop_job", "disarm"])
def test_default_stop_or_disarm_aborts_an_inflight_powered_move(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    transport = EventTransport(f"inflight-{action}")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        transport.synchronize_input = None  # type: ignore[method-assign]
        # Keep an accepted powered move in flight with no acknowledgement. The
        # following powered move must never be written after cancellation.
        transport.set_next_timeout("G1 X2 Y2 F100")
        program = machine.preflight_program(
            POWERED_PROGRAM.replace(
                "G1 X2 Y2 F100\n", "G1 X2 Y2 F100\nG1 X3 Y3 F100\n"
            )
        )
        machine.start_preflighted_program(
            program,
            f"{action}-inflight.gcode",
            authorization_phrase=machine.ARM_PHRASE,
        )
        wait_until(lambda: ("line", "G1 X2 Y2 F100") in transport.events)
        before_stop = len(transport.events)

        started = time.monotonic()
        if action == "stop_job":
            machine.stop_job()  # Exercise the public emergency=False default.
        else:
            machine.disarm()
        elapsed = time.monotonic() - started

        assert elapsed < 2.5, "Cancellation waited for the blocked command ACK"
        wait_until(lambda: not machine.status()["job"]["running"])
        events = transport.events[before_stop:]
        reset_index = next(
            index
            for index, (kind, value) in enumerate(events)
            if kind == "raw"
            and isinstance(value, bytes)
            and b"!" in value
            and b"\x18" in value
        )
        close_index = next(
            index for index, (kind, _value) in enumerate(events) if kind == "close"
        )
        assert reset_index < close_index
        assert any(
            kind == "line" and value == "M5"
            for kind, value in events[reset_index + 1 : close_index]
        )
        assert ("line", "G1 X3 Y3 F100") not in transport.events
        status = machine.status()
        assert status["job"]["error"] is not None
        assert status["job"]["phase"] == "failed"
        assert status["last_successful_job"] is None
        assert status["coordinate_reference_ready"] is False
        assert status["armed"] is False
    finally:
        machine.disconnect()


def test_run_telemetry_between_job_transactions_is_not_an_idle_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("planner-between-transactions")
    machine, _factory = make_machine(monkeypatch, transport)
    between_transactions = threading.Event()
    continue_job = threading.Event()
    try:
        machine.connect()
        machine.prepare_job_start()
        session = machine._session
        assert session is not None and session.receiver is not None
        original_write = machine._write_running_job_line

        def pause_after_powered_move(
            command: str, *, track_transaction: bool = True
        ) -> bool:
            if (
                command == "M5"
                and ("line", "G1 X2 Y2 F100") in transport.events
                and not between_transactions.is_set()
            ):
                # Pause before admission of the next line, after the prior ACK
                # and its receive ownership have fully completed.
                between_transactions.set()
                if not continue_job.wait(2.0):
                    raise MachineError("Test did not release the inter-command barrier")
            return original_write(command, track_transaction=track_transaction)

        monkeypatch.setattr(machine, "_write_running_job_line", pause_after_powered_move)
        program = machine.preflight_program(POWERED_PROGRAM)
        machine.start_preflighted_program(
            program,
            "planner-telemetry.gcode",
            authorization_phrase=machine.ARM_PHRASE,
        )
        assert between_transactions.wait(2.0)
        assert session.receiver.has_transaction is False
        frame = "<Run|MPos:1.500,1.500,0.000|WCO:0,0,0>"
        transport.queue_response(frame)
        wait_until(
            lambda: any(
                frame in entry for entry in session.diagnostics.snapshot()["transcript"]
            )
        )

        assert session.receiver.failure is None
        assert machine.status()["coordinate_reference_ready"] is True
        continue_job.set()
        wait_until(lambda: not machine.status()["job"]["running"])
        assert machine.status()["job"]["error"] is None
        assert machine.status()["controller_state"] == "READY_MOTION"
    finally:
        continue_job.set()
        machine.disconnect()


def test_observed_receiver_fault_cannot_race_past_final_arm_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("alarm-arm-race")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        transport.synchronize_input = None  # type: ignore[method-assign]
        session = machine._session
        assert session is not None and session.receiver is not None
        program = machine.preflight_program(POWERED_PROGRAM)
        original_require_session = machine._require_session
        injected = False

        def observe_alarm_after_initial_authority_check():
            nonlocal injected
            result = original_require_session()
            if not injected:
                injected = True
                transport.queue_response("ALARM:1")
                wait_until(lambda: session.receiver.failure is not None)
            return result

        monkeypatch.setattr(
            machine, "_require_session", observe_alarm_after_initial_authority_check
        )
        # Hold the service lock to delay its asynchronous failure callback while
        # the receiver has already latched the alarm. This models scheduling
        # between the initial session check and publication of a laser grant.
        with machine._lock:
            with pytest.raises((MachineError, SafetyError)):
                machine.arm_program(machine.ARM_PHRASE, program)
            assert machine._armed_program_digest is None
        wait_until(lambda: not machine.status()["coordinate_reference_ready"])
        assert machine.status()["armed"] is False
    finally:
        machine.disconnect()


@pytest.mark.parametrize("frame", ["ALARM:23", " ok", "<Alarm|MPos:9,8,0>"])
def test_receiver_failure_diagnostics_preserve_exact_offending_frame(
    monkeypatch: pytest.MonkeyPatch,
    frame: str,
) -> None:
    transport = EventTransport("exact-fault-evidence")
    machine, _factory = make_machine(monkeypatch, transport)
    try:
        machine.connect()
        machine.prepare_job_start()
        transport.synchronize_input = None  # type: ignore[method-assign]
        transport.queue_response(frame)
        wait_until(lambda: not machine.status()["coordinate_reference_ready"])

        diagnostics = machine.status()["controller_diagnostics"]
        assert frame in diagnostics["last_failure"]
        assert any(
            " RX " in entry and entry.endswith(f" {frame}")
            for entry in diagnostics["transcript"]
        ), "Quarantine diagnostics lost the exact malformed or alarm RX frame"
    finally:
        machine.disconnect()


@pytest.mark.parametrize("all_attempts_fail", [False, True])
def test_receiver_start_failure_never_publishes_a_closed_ready_session(
    monkeypatch: pytest.MonkeyPatch,
    all_attempts_fail: bool,
) -> None:
    first = EventTransport("receiver-start-failure")
    second = EventTransport("receiver-start-retry")
    machine, factory = make_machine(monkeypatch, first, second)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 2)
    monkeypatch.setattr(service_module, "_INITIAL_CONNECT_RETRY_DELAY_SECONDS", 0.0)
    original_start = ControllerReceiver.start
    attempted_receivers: list[ControllerReceiver] = []

    def fail_receiver_start(receiver: ControllerReceiver) -> None:
        attempted_receivers.append(receiver)
        assert machine._session is None
        assert machine.status()["connected"] is False
        if all_attempts_fail or len(attempted_receivers) == 1:
            raise RuntimeError("primary RX thread could not start")
        original_start(receiver)

    monkeypatch.setattr(ControllerReceiver, "start", fail_receiver_start)
    try:
        if all_attempts_fail:
            with pytest.raises(RuntimeError, match="primary RX thread could not start"):
                machine.connect()
            status = machine.status()
            assert status["controller_state"] == "FAULTED"
            assert status["connected"] is False
            assert machine._session is None
            assert all(transport.closed.is_set() for transport in factory.created)
            assert all(receiver._stop.is_set() for receiver in attempted_receivers)
        else:
            status = machine.connect()
            assert status["controller_state"] == "READY_HOME_REQUIRED"
            assert status["coordinate_reference_ready"] is False
            assert status["controller_session_generation"] == 2
            assert first.closed.is_set()
            assert second.is_open
            assert attempted_receivers[0]._stop.is_set()
            assert machine._session is not None
            assert machine._session.transport is second
        assert len(attempted_receivers) == 2
    finally:
        machine.disconnect()


def test_receiver_fault_before_publication_rejects_the_private_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("fault-before-publication")
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)
    original_start = ControllerReceiver.start
    receivers: list[ControllerReceiver] = []

    def start_with_controller_alarm(receiver: ControllerReceiver) -> None:
        receivers.append(receiver)
        assert machine._session is None
        original_start(receiver)
        transport.queue_response("ALARM:23")
        wait_until(lambda: receiver.failure is not None)
        assert machine._session is None
        assert machine.status()["connected"] is False

    monkeypatch.setattr(ControllerReceiver, "start", start_with_controller_alarm)
    try:
        with pytest.raises(MachineError, match="ALARM:23"):
            machine.connect()
        status = machine.status()
        assert status["controller_state"] == "FAULTED"
        assert status["connected"] is False
        assert status["coordinate_reference_ready"] is False
        assert status["armed"] is False
        assert machine._session is None
        assert transport.closed.is_set()
        assert receivers[0]._stop.is_set()
    finally:
        machine.disconnect()


def test_observed_fault_after_final_job_check_cannot_publish_a_success_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = EventTransport("fault-before-terminal-publication")
    machine, _factory = make_machine(monkeypatch, transport)
    final_check_passed = threading.Event()
    publish_terminal = threading.Event()
    failure_callback_entered = threading.Event()
    allow_cleanup = threading.Event()
    try:
        machine.connect()
        machine.prepare_job_start()
        transport.synchronize_input = None  # type: ignore[method-assign]
        session = machine._session
        assert session is not None and session.receiver is not None
        original_check = machine._raise_session_failure
        original_failed = session.receiver._on_failure

        def pause_after_final_check(checked_session) -> None:
            original_check(checked_session)
            frame = inspect.currentframe()
            try:
                caller = None if frame is None else frame.f_back
                final_job_check = (
                    caller is not None and caller.f_code.co_name == "_run_job"
                )
            finally:
                del frame
            if final_job_check:
                final_check_passed.set()
                assert publish_terminal.wait(3.0)

        def delay_failure_cleanup(reason: str) -> None:
            # Keep the fault observable while delaying cancellation/cleanup, so
            # only the terminal publication guard can prevent a success receipt.
            failure_callback_entered.set()
            assert allow_cleanup.wait(3.0)
            original_failed(reason)

        monkeypatch.setattr(machine, "_raise_session_failure", pause_after_final_check)
        monkeypatch.setattr(session.receiver, "_on_failure", delay_failure_cleanup)
        program = machine.preflight_program("G21\nG90\nM5\nG0 X1 Y1 F100\nM5\n")
        machine.start_preflighted_program(program, "terminal-fault.gcode")
        assert final_check_passed.wait(2.0)
        transport.queue_response("ALARM:23")
        assert failure_callback_entered.wait(2.0)
        assert session.receiver.failure is not None
        assert not machine._job_stop.is_set()

        publish_terminal.set()
        worker = machine._job_thread
        assert worker is not None
        worker.join(2.0)
        assert not worker.is_alive()
        status = machine.status()
        assert status["last_successful_job"] is None
        assert status["job"]["phase"] == "failed"
        assert "ALARM:23" in status["job"]["error"]
        assert status["controller_state"] != "READY_MOTION"
        assert status["coordinate_reference_ready"] is False
        allow_cleanup.set()
        wait_until(lambda: not machine.status()["coordinate_reference_ready"])
    finally:
        publish_terminal.set()
        allow_cleanup.set()
        machine.disconnect()
