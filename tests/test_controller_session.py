from __future__ import annotations

import ast
import logging
import queue
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine import service as service_module
from laser_aligner.machine.controller_session import ControllerState
from laser_aligner.machine.service import MachineService
from laser_aligner.machine.transport import InputSynchronizationEvidence

GRBL_IDENTITY = "[VER:1.1h.test:SESSION-HARDENING]"
GRBL_MODAL = "[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]"
GRBL_OFFSETS = (
    "[G54:0.000,0.000,0.000]",
    "[G55:0.000,0.000,0.000]",
    "[G56:0.000,0.000,0.000]",
    "[G57:0.000,0.000,0.000]",
    "[G58:0.000,0.000,0.000]",
    "[G59:0.000,0.000,0.000]",
    "[G92:0.000,0.000,0.000]",
    "ok",
)
GRBL_REALTIME = (
    "<Idle|MPos:0.000,0.000,0.000|WPos:0.000,0.000,0.000|"
    "WCO:0.000,0.000,0.000>"
)


class ResponsiveTransport:
    """Thread-safe GRBL fake with deterministic transaction fault injection."""

    def __init__(
        self,
        name: str,
        *,
        startup: tuple[str, ...] = (),
        replies: dict[str, list[tuple[str, ...] | None]] | None = None,
        realtime_replies: list[tuple[str, ...] | None] | None = None,
        open_error: BaseException | None = None,
    ) -> None:
        self.name = name
        self.is_open = False
        self.closed = threading.Event()
        self.opened = threading.Event()
        self.writes: list[tuple[str, str | bytes]] = []
        self.synchronizations: list[tuple[float, float]] = []
        self.block_command: str | None = None
        self.write_entered = threading.Event()
        self.write_release = threading.Event()
        self.write_release.set()
        self.concurrent_line_write_detected = False
        self._startup = startup
        self._open_error = open_error
        self._responses: queue.Queue[str] = queue.Queue()
        self._replies = {
            command: deque(command_replies)
            for command, command_replies in (replies or {}).items()
        }
        self._realtime_replies = deque(realtime_replies or [])
        self._lock = threading.Lock()
        self._line_writer = threading.Lock()

    @property
    def configured_endpoint(self) -> str:
        return "/dev/serial/by-id/e3-primary"

    @property
    def resolved_endpoint(self) -> str:
        return f"/dev/ttyACM-{self.name}"

    def open(self) -> None:
        if self._open_error is not None:
            raise self._open_error
        self.is_open = True
        self.opened.set()
        for line in self._startup:
            self._responses.put(line)

    def close(self) -> None:
        self.is_open = False
        self.closed.set()

    def synchronize_input(
        self,
        *,
        quiet_interval: float = 0.15,
        timeout: float = 0.75,
    ) -> InputSynchronizationEvidence:
        self.synchronizations.append((quiet_interval, timeout))
        discarded = self.drain()
        return InputSynchronizationEvidence(
            configured_endpoint=self.configured_endpoint,
            resolved_endpoint=self.resolved_endpoint,
            quiet_interval_seconds=quiet_interval,
            elapsed_seconds=0.0,
            discarded_bytes=0,
            discarded_lines=len(discarded),
            observed_activity=bool(discarded),
        )

    def queue_response(self, *lines: str) -> None:
        for line in lines:
            self._responses.put(line)

    def set_next_reply(self, command: str, *lines: str) -> None:
        self._replies.setdefault(command, deque()).append(tuple(lines))

    def set_next_timeout(self, command: str) -> None:
        self._replies.setdefault(command, deque()).append(None)

    def write_line(self, line: str) -> None:
        owns_writer = self._line_writer.acquire(blocking=False)
        if not owns_writer:
            self.concurrent_line_write_detected = True
            self._line_writer.acquire()
            owns_writer = True
        try:
            command = " ".join(line.strip().upper().split())
            with self._lock:
                self.writes.append(("line", command))
            if command == self.block_command:
                self.write_entered.set()
                if not self.write_release.wait(2.0):
                    raise TimeoutError("test write barrier was not released")
            configured = self._replies.get(command)
            if configured:
                responses = configured.popleft()
                if responses is not None:
                    self.queue_response(*responses)
                return
            self.queue_response(*self._default_response(command))
        finally:
            if owns_writer:
                self._line_writer.release()

    def write_raw(self, data: bytes) -> None:
        with self._lock:
            self.writes.append(("raw", data))
        if data == b"?":
            if self._realtime_replies:
                responses = self._realtime_replies.popleft()
                if responses is not None:
                    self.queue_response(*responses)
            else:
                self.queue_response(GRBL_REALTIME)

    def read_line(self, timeout: float = 1.0) -> str | None:
        try:
            return self._responses.get(timeout=min(max(timeout, 0.0), 0.002))
        except queue.Empty:
            return None

    def drain(self) -> list[str]:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._responses.get_nowait())
            except queue.Empty:
                return lines

    @staticmethod
    def _default_response(command: str) -> tuple[str, ...]:
        if command == "$I":
            return (GRBL_IDENTITY, "ok")
        if command == "$$":
            return ("$1=250", "$30=1000", "$32=1", "ok")
        if command == "$G":
            return (GRBL_MODAL, "ok")
        if command == "$#":
            return GRBL_OFFSETS
        return ("ok",)


class TransportFactory:
    def __init__(self, transports: list[ResponsiveTransport] | None = None) -> None:
        self.pending = deque(transports or [])
        self.created: list[ResponsiveTransport] = []

    def __call__(self, _backend: str, _port: str, _baudrate: int) -> ResponsiveTransport:
        transport = (
            self.pending.popleft()
            if self.pending
            else ResponsiveTransport(f"generated-{len(self.created) + 1}")
        )
        self.created.append(transport)
        return transport


def machine_settings() -> MachineSettings:
    return MachineSettings(
        backend="serial",
        protocol="grbl",
        port="/dev/serial/by-id/e3-primary",
        baudrate=115200,
        controller_startup_delay=0.0,
        read_timeout=0.02,
        allow_motion=True,
        home_before_photo=True,
        photo_x=0.0,
        photo_y=0.0,
    )


def make_machine(
    monkeypatch: pytest.MonkeyPatch,
    *transports: ResponsiveTransport,
) -> tuple[MachineService, TransportFactory]:
    factory = TransportFactory(list(transports))
    monkeypatch.setattr(service_module, "create_machine_transport", factory)
    machine = MachineService(
        machine_settings(),
        LaserSettings(),
        hardware_enabled=True,
    )
    return machine, factory


def wait_for_recovery(machine: MachineService, timeout: float = 2.0) -> None:
    with machine._lock:
        recovery = machine._recovery_thread
    assert recovery is not None
    recovery.join(timeout)
    assert not recovery.is_alive()


def test_controller_state_enum_and_transition_graph_are_authoritative() -> None:
    assert [state.value for state in ControllerState] == [
        "DISCONNECTED",
        "OPENING",
        "SYNCHRONIZING",
        "READY_HOME_REQUIRED",
        "READY_MOTION",
        "JOB_RUNNING",
        "STOPPING",
        "RECOVERING",
        "RECONNECT_REQUIRED",
        "FAULTED",
        "SHUTTING_DOWN",
    ]
    machine = MachineService(machine_settings(), LaserSettings(), hardware_enabled=True)
    with machine._lock, pytest.raises(MachineError, match="Invalid.*transition"):
        machine._set_controller_state_locked(ControllerState.READY_MOTION)
    assert machine.status()["controller_state"] == "DISCONNECTED"


def test_connect_publishes_only_after_full_grbl_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("handshake", startup=("Grbl startup chatter",))
    machine, _factory = make_machine(monkeypatch, transport)

    status = machine.connect()

    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["connected"] is True
    assert status["controller_session_generation"] == 1
    assert status["controller_session"]["resolved_endpoint"].endswith("handshake")
    assert [write for write in transport.writes[:6]] == [
        ("line", "$I"),
        ("line", "M5"),
        ("line", "$$"),
        ("line", "$G"),
        ("line", "$#"),
        ("raw", b"?"),
    ]
    diagnostics = status["controller_diagnostics"]
    assert diagnostics["last_successful_transaction"][
        "terminal_classification"
    ] == "realtime_status"
    assert diagnostics["synchronization"]["observed_activity"] is True
    assert diagnostics["last_successful_sync_at"] is not None
    assert diagnostics["firmware_identity"] == [GRBL_IDENTITY, "ok"]
    assert diagnostics["action_required"] == "HOME"
    assert status["controller_action_required"] == "HOME"
    machine.disconnect()


def test_explicit_grbl_without_optional_identity_payload_runs_full_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport(
        "identity-unavailable",
        replies={"$I": [("ok",)]},
    )
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)

    status = machine.connect()

    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["connected"] is True
    assert status["controller_diagnostics"]["firmware_identity"] == []
    assert [write for write in transport.writes[:6]] == [
        ("line", "$I"),
        ("line", "M5"),
        ("line", "$$"),
        ("line", "$G"),
        ("line", "$#"),
        ("raw", b"?"),
    ]
    assert any(
        "identity payload unavailable; continuing explicit-GRBL capability verification"
        in entry
        for entry in status["log"]
    )
    machine.disconnect()


@pytest.mark.parametrize(
    ("replies", "realtime_replies"),
    [
        ({"$I": [("ok",)], "$$": [("not-grbl", "ok")]}, None),
        ({"$I": [("ok",)], "$G": [("not-grbl", "ok")]}, None),
        ({"$I": [("ok",)], "$#": [("not-grbl", "ok")]}, None),
        ({"$I": [("ok",)]}, [("not-grbl",)]),
    ],
    ids=["settings", "modal", "offsets", "realtime"],
)
def test_explicit_grbl_without_identity_payload_still_fails_closed_on_bad_capability(
    monkeypatch: pytest.MonkeyPatch,
    replies: dict[str, list[tuple[str, ...] | None]],
    realtime_replies: list[tuple[str, ...] | None] | None,
) -> None:
    transport = ResponsiveTransport(
        "bad-capability",
        replies=replies,
        realtime_replies=realtime_replies,
    )
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)

    with pytest.raises(MachineError, match="GRBL handshake incompatibility"):
        machine.connect()

    status = machine.status()
    assert status["connected"] is False
    assert status["controller_state"] == "FAULTED"
    assert status["controller_diagnostics"]["last_failure_code"] == (
        "controller.handshake_incompatible"
    )
    assert transport.closed.is_set()


def test_auto_protocol_does_not_infer_grbl_from_identity_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport(
        "auto-ack-only",
        replies={"$I": [("ok",)], "M115": [("ok",)]},
    )
    settings = machine_settings()
    settings.protocol = "auto"
    monkeypatch.setattr(service_module, "create_machine_transport", TransportFactory([transport]))
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)
    machine = MachineService(settings, LaserSettings(), hardware_enabled=True)

    with pytest.raises(MachineError, match="protocol is not configured"):
        machine.connect()

    diagnostics = machine.status()["controller_diagnostics"]
    assert diagnostics["last_failure_code"] == "controller.identity_mismatch"
    assert "Set machine.protocol explicitly" not in diagnostics["last_failure"]


def test_explicit_grbl_requires_identity_query_to_terminate_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport(
        "identity-rejected",
        replies={"$I": [("error:20",)]},
    )
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)

    with pytest.raises(MachineError, match="identity query did not terminate cleanly"):
        machine.connect()

    status = machine.status()
    assert status["connected"] is False
    assert status["controller_state"] == "FAULTED"
    assert transport.closed.is_set()


def test_candidate_remains_private_until_handshake_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("private")
    transport.block_command = "$I"
    transport.write_release.clear()
    machine, _factory = make_machine(monkeypatch, transport)
    errors: list[BaseException] = []

    worker = threading.Thread(
        target=lambda: _capture_error(machine.connect, errors),
        daemon=True,
    )
    worker.start()
    assert transport.write_entered.wait(1.0)
    snapshot = machine.status()
    assert snapshot["connected"] is False
    assert snapshot["controller_state"] == "SYNCHRONIZING"
    assert machine._session is None
    assert machine._transport is None

    transport.write_release.set()
    worker.join(2.0)
    assert not worker.is_alive()
    assert errors == []
    assert machine.status()["controller_state"] == "READY_HOME_REQUIRED"
    machine.disconnect()


def _capture_error(callback: Any, errors: list[BaseException]) -> None:
    try:
        callback()
    except BaseException as exc:
        errors.append(exc)


@pytest.mark.parametrize("terminal", ["error:20", "ALARM:1"])
def test_consumed_error_and_alarm_do_not_poison_session(
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
) -> None:
    transport = ResponsiveTransport("consumed")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    generation = machine.status()["controller_session_generation"]
    transport.set_next_reply("$I", terminal)

    with pytest.raises(MachineError, match=terminal.split(":", 1)[0]):
        machine.send_command("$I")

    status = machine.status()
    assert status["connected"] is True
    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["controller_session_generation"] == generation
    assert not transport.closed.is_set()
    machine.disconnect()


def test_delayed_ack_after_consumed_rejection_quarantines_before_next_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectionTailTransport(ResponsiveTransport):
        arm_tail = False
        tail_due: float | None = None

        def read_line(self, timeout: float = 1.0) -> str | None:
            response = super().read_line(timeout)
            if (
                response is None
                and self.tail_due is not None
                and time.monotonic() >= self.tail_due
            ):
                self.tail_due = None
                return "ok"
            if self.arm_tail and response == "error:20":
                self.arm_tail = False
                self.tail_due = time.monotonic() + 0.001
            return response

    transport = RejectionTailTransport("rejection-tail")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    transport.set_next_reply("$I", "error:20")
    transport.arm_tail = True

    with pytest.raises(MachineError, match="unowned response"):
        machine.send_command("$I")

    commands_after_failure = list(transport.writes)
    with pytest.raises(MachineError, match="not ready|unavailable|reconnect"):
        machine.send_command("$I")
    assert transport.writes == commands_after_failure
    assert machine.status()["controller_state"] == "RECONNECT_REQUIRED"
    assert transport.closed.is_set()


def test_realtime_status_is_diverted_from_command_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("status-diversion")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.set_next_reply("$I", GRBL_REALTIME, GRBL_IDENTITY, "ok")

    assert machine.send_command("$I") == [GRBL_IDENTITY, "ok"]
    assert machine.status()["controller_state"] == "READY_HOME_REQUIRED"
    machine.disconnect()


def test_primary_synchronization_discards_chatter_arriving_during_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SettleChatterTransport(ResponsiveTransport):
        def synchronize_input(
            self,
            *,
            quiet_interval: float = 0.15,
            timeout: float = 0.75,
        ) -> InputSynchronizationEvidence:
            self.queue_response("Grbl late startup", "unterminated-fragment")
            return super().synchronize_input(
                quiet_interval=quiet_interval,
                timeout=timeout,
            )

    transport = SettleChatterTransport("settle-chatter")
    machine, _factory = make_machine(monkeypatch, transport)

    status = machine.connect()

    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["controller_diagnostics"]["synchronization"][
        "discarded_lines"
    ] == 2
    assert status["controller_diagnostics"]["firmware_identity"] == [
        GRBL_IDENTITY,
        "ok",
    ]
    machine.disconnect()


def test_duplicate_terminal_ack_is_unowned_and_quarantines_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("duplicate-ack")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    transport.set_next_reply("$I", GRBL_IDENTITY, "ok", "ok")

    with pytest.raises(MachineError, match="unowned response"):
        machine.send_command("$I")

    status = machine.status()
    assert status["controller_state"] == "RECONNECT_REQUIRED"
    assert status["controller_diagnostics"]["last_failure_code"] == (
        "controller.session_quarantined"
    )
    assert transport.closed.wait(1.0)


def test_millisecond_delayed_duplicate_ack_is_rejected_before_next_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedDuplicateTransport(ResponsiveTransport):
        arm_duplicate = False
        duplicate_due: float | None = None
        duplicate_emitted = threading.Event()

        def read_line(self, timeout: float = 1.0) -> str | None:
            response = super().read_line(timeout)
            if (
                response is None
                and self.duplicate_due is not None
                and time.monotonic() >= self.duplicate_due
            ):
                self.duplicate_due = None
                self.duplicate_emitted.set()
                return "ok"
            if self.arm_duplicate and response == "ok":
                self.arm_duplicate = False
                self.duplicate_due = time.monotonic() + 0.001
            return response

    transport = DelayedDuplicateTransport("delayed-duplicate")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    transport.arm_duplicate = True

    with pytest.raises(MachineError, match="unowned response"):
        machine.send_command("$I")

    assert transport.duplicate_emitted.is_set()
    assert machine.status()["controller_state"] == "RECONNECT_REQUIRED"
    assert transport.closed.is_set()


@pytest.mark.parametrize(
    "bad_response",
    ["Grbl 1.1h ['$' for help]", " ok"],
    ids=["startup", "malformed"],
)
def test_startup_or_malformed_frame_quarantines_and_clears_pending_transaction(
    monkeypatch: pytest.MonkeyPatch,
    bad_response: str,
) -> None:
    transport = ResponsiveTransport("bad-frame")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    transport.set_next_reply("$I", bad_response)

    with pytest.raises(MachineError):
        machine.send_command("$I")

    assert machine.status()["controller_state"] == "RECONNECT_REQUIRED"
    assert getattr(machine._job_context, "pending_transaction", None) is None
    assert transport.closed.wait(1.0)


def test_exact_pre_home_error_9_uses_only_bounded_unlock_and_still_requires_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport(
        "error-nine",
        replies={"M5": [("error:9",), ("ok",)]},
    )
    machine, _factory = make_machine(monkeypatch, transport)

    status = machine.connect()

    line_writes = [payload for channel, payload in transport.writes if channel == "line"]
    assert line_writes[:4] == ["$I", "M5", "$X", "M5"]
    assert "$H" not in line_writes
    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["coordinate_reference_ready"] is False
    machine.disconnect()


def test_non_error_9_pre_home_rejection_never_unlocks_or_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transports = tuple(
        ResponsiveTransport(
            f"not-error-nine-{attempt}",
            replies={"M5": [("error:8",)]},
        )
        for attempt in range(3)
    )
    machine, _factory = make_machine(monkeypatch, *transports)

    with pytest.raises(MachineError, match="error:8"):
        machine.connect()

    line_writes = [
        payload
        for transport in transports
        for channel, payload in transport.writes
        if channel == "line"
    ]
    assert "$X" not in line_writes
    assert machine.status()["controller_state"] == "FAULTED"
    assert machine._session is None
    assert all(transport.closed.is_set() for transport in transports)


def test_exact_error_9_with_delayed_ack_never_unlocks_on_ambiguous_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorNineTailTransport(ResponsiveTransport):
        tail_due: float | None = None

        def read_line(self, timeout: float = 1.0) -> str | None:
            response = super().read_line(timeout)
            if (
                response is None
                and self.tail_due is not None
                and time.monotonic() >= self.tail_due
            ):
                self.tail_due = None
                return "ok"
            if response == "error:9":
                self.tail_due = time.monotonic() + 0.001
            return response

    transport = ErrorNineTailTransport(
        "ambiguous-error-nine",
        replies={"M5": [("error:9",)]},
    )
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)

    with pytest.raises(MachineError, match="unowned response"):
        machine.connect()

    line_writes = [payload for channel, payload in transport.writes if channel == "line"]
    assert "$X" not in line_writes
    assert machine.status()["controller_state"] == "FAULTED"
    assert transport.closed.is_set()


def test_delayed_old_generation_ack_cannot_complete_new_dollar_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ResponsiveTransport(
        "old",
        replies={"$#": [GRBL_OFFSETS[:-1]]},
    )
    second = ResponsiveTransport("fresh")
    machine, factory = make_machine(monkeypatch, first, second)
    monkeypatch.setattr(service_module, "_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(service_module, "_INITIAL_CONNECT_RETRY_DELAY_SECONDS", 0.0)

    status = machine.connect()
    first.queue_response("ok")

    assert len(factory.created) == 2
    assert first.closed.is_set()
    assert first.read_line(0.0) == "ok"
    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["controller_session_generation"] == 2
    assert status["controller_session"]["resolved_endpoint"].endswith("fresh")
    assert ("line", "$#") in second.writes
    machine.disconnect()


def test_overall_connect_deadline_clamps_identity_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("deadline", replies={"$I": [None]})
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(service_module, "_CONTROLLER_SYNC_QUIET_SECONDS", 0.001)

    started = time.monotonic()
    with pytest.raises(MachineError, match="acknowledge|deadline"):
        machine.connect()
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert transport.synchronizations[0][1] <= 0.05 + 1e-9
    assert machine.status()["controller_state"] == "FAULTED"


@pytest.mark.parametrize("partial", [False, True], ids=["before-byte", "partial"])
def test_failed_or_partial_write_quarantines_exact_session(
    monkeypatch: pytest.MonkeyPatch,
    partial: bool,
) -> None:
    class WriteFailureTransport(ResponsiveTransport):
        fail_next_identity = False

        def write_line(self, line: str) -> None:
            command = " ".join(line.strip().upper().split())
            if self.fail_next_identity and command == "$I":
                self.fail_next_identity = False
                if partial:
                    with self._lock:
                        self.writes.append(("line-partial", command))
                    self.queue_response("ok")
                raise OSError("injected partial write" if partial else "injected write")
            super().write_line(line)

    transport = WriteFailureTransport("write-failure")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    generation = machine.status()["controller_session_generation"]
    transport.fail_next_identity = True

    with pytest.raises(MachineError, match="while writing"):
        machine.send_command("$I")

    status = machine.status()
    assert status["controller_state"] == "RECONNECT_REQUIRED"
    assert status["controller_session_generation"] == generation
    assert status["controller_diagnostics"]["last_failure_code"] == (
        "controller.session_quarantined"
    )
    assert transport.closed.is_set()
    if partial:
        assert "ok" in transport.drain()


def test_read_failure_quarantines_exact_session_and_records_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReadFailureTransport(ResponsiveTransport):
        fail_next_read = False

        def read_line(self, timeout: float = 1.0) -> str | None:
            if self.fail_next_read:
                self.fail_next_read = False
                raise OSError("USB disappeared")
            return super().read_line(timeout)

    transport = ReadFailureTransport("read-failure")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    transport.fail_next_read = True

    with pytest.raises(MachineError, match="read failed"):
        machine.send_command("$I")

    diagnostics = machine.status()["controller_diagnostics"]
    assert diagnostics["last_failure_code"] == "controller.session_quarantined"
    assert diagnostics["last_failed_command"] == "$I"
    assert transport.closed.is_set()


def test_stale_step_idle_hold_is_repaired_and_verified_before_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport(
        "stale-hold",
        replies={
            "$$": [
                ("$1=255", "$30=1000", "$32=1", "ok"),
                ("$1=250", "$30=1000", "$32=1", "ok"),
            ]
        },
    )
    machine, _factory = make_machine(monkeypatch, transport)

    status = machine.connect()

    line_writes = [payload for channel, payload in transport.writes if channel == "line"]
    assert line_writes[:6] == ["$I", "M5", "$$", "$1=250", "$$", "$G"]
    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["coordinate_reference_ready"] is False
    machine.disconnect()


@pytest.mark.parametrize(
    ("offsets", "missing"),
    [
        (GRBL_OFFSETS[1:], "active work offset"),
        (GRBL_OFFSETS[:-2] + ("ok",), "G92"),
    ],
    ids=["active-workspace", "g92"],
)
def test_incomplete_coordinate_payload_never_publishes_candidate(
    monkeypatch: pytest.MonkeyPatch,
    offsets: tuple[str, ...],
    missing: str,
) -> None:
    transport = ResponsiveTransport("bad-offsets", replies={"$#": [offsets]})
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)

    with pytest.raises(MachineError, match=missing):
        machine.connect()

    assert machine.status()["controller_state"] == "FAULTED"
    assert machine._session is None
    assert transport.closed.is_set()


@pytest.mark.parametrize(
    ("realtime_replies", "match"),
    [([None], "did not provide"), ([("not-a-status-frame",)], "Unexpected")],
    ids=["timeout", "malformed"],
)
def test_invalid_realtime_handshake_never_publishes_candidate(
    monkeypatch: pytest.MonkeyPatch,
    realtime_replies: list[tuple[str, ...] | None],
    match: str,
) -> None:
    transport = ResponsiveTransport(
        "bad-realtime",
        realtime_replies=realtime_replies,
    )
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(service_module, "_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(MachineError, match=match):
        machine.connect()

    assert machine.status()["controller_state"] == "FAULTED"
    assert machine._session is None
    assert transport.closed.is_set()


def test_candidate_realtime_completion_rejects_delayed_unowned_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CandidateRealtimeTailTransport(ResponsiveTransport):
        tail_due: float | None = None

        def read_line(self, timeout: float = 1.0) -> str | None:
            response = super().read_line(timeout)
            if (
                response is None
                and self.tail_due is not None
                and time.monotonic() >= self.tail_due
            ):
                self.tail_due = None
                return "ok"
            if response == GRBL_REALTIME:
                self.tail_due = time.monotonic() + 0.001
            return response

    transport = CandidateRealtimeTailTransport("candidate-realtime-tail")
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)

    with pytest.raises(MachineError, match="unowned response"):
        machine.connect()

    assert machine.status()["controller_state"] == "FAULTED"
    assert machine._session is None
    assert transport.closed.is_set()


def test_wrong_controller_identity_at_configured_path_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport(
        "wrong-controller",
        replies={"$I": [("FIRMWARE_NAME:Marlin 2.1", "ok")]},
    )
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)

    with pytest.raises(MachineError, match="identity mismatch"):
        machine.connect()

    diagnostics = machine.status()["controller_diagnostics"]
    assert diagnostics["last_failure_code"] == "controller.identity_mismatch"
    assert machine._session is None
    assert transport.closed.is_set()


def test_unsupported_product_transport_synchronization_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("unsupported")
    transport.synchronize_input = None  # type: ignore[method-assign]
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)

    with pytest.raises(MachineError, match="bounded input synchronization"):
        machine.connect()

    assert machine.status()["controller_state"] == "FAULTED"
    assert transport.closed.is_set()


def test_stop_stays_bounded_while_write_gate_is_occupied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("blocked-write")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    transport.block_command = "$I"
    transport.write_release.clear()
    monkeypatch.setattr(service_module, "_REALTIME_STOP_WRITE_DEADLINE_SECONDS", 0.05)
    errors: list[BaseException] = []
    writer = threading.Thread(
        target=lambda: _capture_error(lambda: machine.send_command("$I"), errors),
        daemon=True,
    )
    writer.start()
    assert transport.write_entered.wait(1.0)

    started = time.monotonic()
    machine.request_stop()
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert machine.status()["controller_state"] == "RECONNECT_REQUIRED"
    transport.write_release.set()
    writer.join(2.0)
    assert not writer.is_alive()
    assert transport.closed.wait(1.0)
    assert ("line", "M5") in transport.writes


def test_stop_while_waiting_for_ack_quarantines_and_recovers_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AckWaitTransport(ResponsiveTransport):
        command_written = threading.Event()

        def write_line(self, line: str) -> None:
            super().write_line(line)
            if line.strip().upper() == "$I" and self.is_open:
                self.command_written.set()

    old = AckWaitTransport("ack-wait")
    fresh = ResponsiveTransport("after-ack-wait")
    machine, _factory = make_machine(monkeypatch, old, fresh)
    machine.connect()
    old.command_written.clear()
    old.set_next_timeout("$I")
    errors: list[BaseException] = []
    worker = threading.Thread(
        target=lambda: _capture_error(lambda: machine.send_command("$I", timeout=1.0), errors),
        daemon=True,
    )
    worker.start()
    assert old.command_written.wait(1.0)

    machine.request_stop()
    worker.join(2.0)
    wait_for_recovery(machine)

    assert not worker.is_alive()
    assert errors and "software stop" in str(errors[0]).lower()
    assert old.closed.is_set()
    status = machine.status()
    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["controller_session"]["resolved_endpoint"].endswith("after-ack-wait")
    machine.disconnect()


def test_concurrent_connect_calls_collapse_to_one_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("single-flight")
    transport.block_command = "$I"
    transport.write_release.clear()
    machine, factory = make_machine(monkeypatch, transport)
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []
    workers = [
        threading.Thread(
            target=lambda: _capture_result(machine.connect, results, errors),
            daemon=True,
        )
        for _index in range(2)
    ]
    for worker in workers:
        worker.start()
    assert transport.write_entered.wait(1.0)
    transport.write_release.set()
    for worker in workers:
        worker.join(2.0)
        assert not worker.is_alive()

    assert errors == []
    assert len(results) == 2
    assert len(factory.created) == 1
    assert {item["controller_session_generation"] for item in results} == {1}
    machine.disconnect()


def test_connect_and_reconnect_race_share_one_published_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("connect-reconnect-flight")
    transport.block_command = "$I"
    transport.write_release.clear()
    machine, factory = make_machine(monkeypatch, transport)
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []
    connector = threading.Thread(
        target=lambda: _capture_result(machine.connect, results, errors),
        daemon=True,
    )
    replacer = threading.Thread(
        target=lambda: _capture_result(machine.replace_connection, results, errors),
        daemon=True,
    )
    connector.start()
    assert transport.write_entered.wait(1.0)
    replacer.start()
    transport.write_release.set()
    connector.join(2.0)
    replacer.join(2.0)

    assert not connector.is_alive() and not replacer.is_alive()
    assert errors == []
    assert len(results) == 2
    assert len(factory.created) == 1
    assert {result["controller_session_generation"] for result in results} == {1}
    assert machine.status()["controller_state"] == "READY_HOME_REQUIRED"
    machine.disconnect()


def _capture_result(
    callback: Any,
    results: list[dict[str, Any]],
    errors: list[BaseException],
) -> None:
    try:
        results.append(callback())
    except BaseException as exc:
        errors.append(exc)


def test_home_is_generation_gated_and_rejected_when_already_motion_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("home-gate")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()

    machine.prepare_job_start()
    status = machine.status()
    assert status["controller_state"] == "READY_MOTION"
    assert status["coordinate_reference_session_generation"] == status[
        "controller_session_generation"
    ]
    with pytest.raises(MachineError, match="requires Home"):
        machine.prepare_job_start()
    machine.disconnect()


def test_explicit_home_then_start_reuses_exact_reference_without_second_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("explicit-home-start")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    program = machine.preflight_program("G21\nG90\nM5\nG0 X1 Y1 F100\nM5\n")

    with pytest.raises(SafetyError, match="completed explicitly"):
        machine.start_preflighted_program(program, "blocked-before-home.gcode")
    assert transport.writes.count(("line", "$H")) == 0

    machine.prepare_job_start()
    home_count = transport.writes.count(("line", "$H"))
    modal_count = transport.writes.count(("line", "$G"))
    offsets_count = transport.writes.count(("line", "$#"))
    assert home_count == 1

    machine.start_preflighted_program(program, "after-explicit-home.gcode")
    job_worker = machine._job_thread
    assert job_worker is not None
    job_worker.join(2.0)
    assert not job_worker.is_alive()

    status = machine.status()
    assert status["job"]["error"] is None
    assert transport.writes.count(("line", "$H")) == home_count
    assert transport.writes.count(("line", "$G")) > modal_count
    assert transport.writes.count(("line", "$#")) > offsets_count
    machine.disconnect()


@pytest.mark.parametrize(
    "bad_frame",
    ["Grbl 1.1h ['$' for help]", " ok"],
    ids=["restart-banner", "malformed"],
)
def test_mid_home_startup_or_malformed_frame_quarantines_exact_session(
    monkeypatch: pytest.MonkeyPatch,
    bad_frame: str,
) -> None:
    transport = ResponsiveTransport(
        "mid-home-frame",
        replies={"$H": [(bad_frame, "ok")]},
    )
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]

    with pytest.raises(MachineError, match="restarted|malformed"):
        machine.prepare_job_start()

    status = machine.status()
    assert status["controller_state"] == "RECONNECT_REQUIRED"
    assert status["coordinate_reference_ready"] is False
    assert status["controller_diagnostics"]["last_failed_command"] == "$H"
    assert transport.closed.is_set()
    assert "ok" in transport.drain()


def test_delayed_duplicate_home_ack_cannot_ack_following_coordinate_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedHomeAckTransport(ResponsiveTransport):
        duplicate_due: float | None = None
        home_ack_returned = False

        def read_line(self, timeout: float = 1.0) -> str | None:
            response = super().read_line(timeout)
            if (
                response is None
                and self.duplicate_due is not None
                and time.monotonic() >= self.duplicate_due
            ):
                self.duplicate_due = None
                return "ok"
            if (
                not self.home_ack_returned
                and response == "ok"
                and self.writes
                and self.writes[-1] == ("line", "$H")
            ):
                self.home_ack_returned = True
                self.duplicate_due = time.monotonic() + 0.001
            return response

    transport = DelayedHomeAckTransport("delayed-home-ack")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    handshake_modal_count = transport.writes.count(("line", "$G"))

    with pytest.raises(MachineError, match="unowned response"):
        machine.prepare_job_start()

    assert transport.home_ack_returned is True
    assert transport.writes.count(("line", "$G")) == handshake_modal_count
    status = machine.status()
    assert status["controller_state"] == "RECONNECT_REQUIRED"
    assert status["controller_diagnostics"]["last_failed_command"] == "$H"
    assert transport.closed.is_set()


def test_active_idle_homing_fallback_consumes_one_delayed_home_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ActiveIdleLateAckTransport(ResponsiveTransport):
        late_ack_due: float | None = None

        def read_line(self, timeout: float = 1.0) -> str | None:
            response = super().read_line(timeout)
            if (
                response is None
                and self.late_ack_due is not None
                and time.monotonic() >= self.late_ack_due
            ):
                self.late_ack_due = None
                return "ok"
            if response == "<Idle|MPos:0,0,0>":
                self.late_ack_due = time.monotonic() + 0.001
            return response

    transport = ActiveIdleLateAckTransport(
        "active-idle-late-ack",
        replies={
            "$H": [
                (
                    "<Home|MPos:1,1,0>",
                    "<Idle|MPos:0,0,0>",
                )
            ]
        },
    )
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    handshake_modal_count = transport.writes.count(("line", "$G"))

    machine.prepare_job_start()

    status = machine.status()
    assert status["controller_state"] == "READY_MOTION"
    assert transport.late_ack_due is None
    assert transport.writes.count(("line", "$G")) == handshake_modal_count + 1
    assert status["controller_diagnostics"]["last_failed_command"] is None
    machine.disconnect()


def test_home_and_status_poll_are_coherent_and_two_home_requests_exclude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("home-race")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.block_command = "$H"
    transport.write_release.clear()
    results: list[dict[str, Any]] = []
    errors: list[BaseException] = []
    workers = [
        threading.Thread(
            target=lambda: _capture_result(machine.prepare_job_start, results, errors),
            daemon=True,
        )
        for _index in range(2)
    ]
    workers[0].start()
    assert transport.write_entered.wait(1.0)
    workers[1].start()

    for _poll in range(20):
        snapshot = machine.status()
        assert snapshot["controller_state"] == "READY_HOME_REQUIRED"
        assert snapshot["coordinate_reference_ready"] is False
    transport.write_release.set()
    for worker in workers:
        worker.join(2.0)
        assert not worker.is_alive()

    assert len(results) == 1
    assert len(errors) == 1
    assert "requires Home" in str(errors[0])
    assert transport.writes.count(("line", "$H")) == 1
    assert machine.status()["controller_state"] == "READY_MOTION"
    machine.disconnect()


def test_realtime_status_completion_rejects_delayed_unowned_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RealtimeTailTransport(ResponsiveTransport):
        arm_tail = False
        tail_due: float | None = None

        def read_line(self, timeout: float = 1.0) -> str | None:
            response = super().read_line(timeout)
            if (
                response is None
                and self.tail_due is not None
                and time.monotonic() >= self.tail_due
            ):
                self.tail_due = None
                return "ok"
            if self.arm_tail and response == GRBL_REALTIME:
                self.arm_tail = False
                self.tail_due = time.monotonic() + 0.001
            return response

    transport = RealtimeTailTransport("realtime-tail")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    transport.synchronize_input = None  # type: ignore[method-assign]
    transport.arm_tail = True

    with pytest.raises(MachineError, match="alignment became uncertain"):
        machine.sample_realtime_position(timeout=0.1)

    status = machine.status()
    assert status["controller_state"] == "RECONNECT_REQUIRED"
    assert status["controller_diagnostics"]["last_failed_command"] == "?"
    assert transport.closed.is_set()


def test_stop_during_homing_quarantines_old_generation_without_ready_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = ResponsiveTransport("stopped-home")
    fresh = ResponsiveTransport("after-stopped-home")
    machine, _factory = make_machine(monkeypatch, old, fresh)
    machine.connect()
    old.block_command = "$H"
    old.write_release.clear()
    errors: list[BaseException] = []
    worker = threading.Thread(
        target=lambda: _capture_error(machine.prepare_job_start, errors),
        daemon=True,
    )
    worker.start()
    assert old.write_entered.wait(1.0)

    machine.request_stop()
    old.write_release.set()
    worker.join(2.0)
    assert not worker.is_alive()
    wait_for_recovery(machine)

    assert errors and "cancelled" in str(errors[0]).lower()
    assert old.closed.is_set()
    status = machine.status()
    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["coordinate_reference_ready"] is False
    assert status["controller_session"]["resolved_endpoint"].endswith(
        "after-stopped-home"
    )
    machine.disconnect()


def _reconnect_while_old_job_worker_unwinds(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    MachineService,
    ResponsiveTransport,
    ResponsiveTransport,
    threading.Thread,
    threading.Event,
]:
    old = ResponsiveTransport("unwinding-job")
    fresh = ResponsiveTransport("replacement-session")
    machine, _factory = make_machine(monkeypatch, old, fresh)
    monkeypatch.setattr(service_module, "_REALTIME_STOP_WRITE_DEADLINE_SECONDS", 0.02)
    cleanup_entered = threading.Event()
    cleanup_release = threading.Event()
    original_fail_off = machine._best_effort_fail_off

    def pause_stale_job_cleanup(transport: Any, *, context: str) -> None:
        if context == "job cleanup":
            cleanup_entered.set()
            assert cleanup_release.wait(2.0)
        original_fail_off(transport, context=context)

    monkeypatch.setattr(machine, "_best_effort_fail_off", pause_stale_job_cleanup)
    machine.connect()
    machine.prepare_job_start()
    program = machine.preflight_program("G21\nG90\nM5\nG0 X1 Y1 F100\nM5\n")
    old.block_command = "M5"
    old.write_release.clear()
    machine.start_validated_program(program, "unwinding.gcode")
    assert old.write_entered.wait(1.0)

    machine.request_stop()
    old.write_release.set()
    assert cleanup_entered.wait(1.0)
    wait_for_recovery(machine)
    worker = machine._job_thread
    assert worker is not None and worker.is_alive()
    status = machine.status()
    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["controller_session"]["resolved_endpoint"].endswith(
        "replacement-session"
    )
    return machine, old, fresh, worker, cleanup_release


def test_reconnect_can_publish_while_old_job_worker_unwinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine, old, fresh, worker, cleanup_release = (
        _reconnect_while_old_job_worker_unwinds(monkeypatch)
    )
    try:
        assert worker.is_alive()
        assert old.closed.is_set()
        assert fresh.is_open
    finally:
        cleanup_release.set()
        worker.join(2.0)
        machine.disconnect()


def test_stale_job_cleanup_cannot_close_replacement_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine, old, fresh, worker, cleanup_release = (
        _reconnect_while_old_job_worker_unwinds(monkeypatch)
    )
    cleanup_release.set()
    worker.join(2.0)
    try:
        assert not worker.is_alive()
        assert old.closed.is_set()
        assert fresh.is_open
        assert not fresh.closed.is_set()
        assert machine.status()["connected"] is True
    finally:
        machine.disconnect()


def test_stale_job_worker_cannot_write_replacement_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine, old, fresh, worker, cleanup_release = (
        _reconnect_while_old_job_worker_unwinds(monkeypatch)
    )
    replacement_writes = list(fresh.writes)
    cleanup_release.set()
    worker.join(2.0)
    try:
        assert not worker.is_alive()
        assert fresh.writes == replacement_writes
        assert ("line", "G0 X1 Y1 F100") not in fresh.writes
    finally:
        machine.disconnect()


def test_stop_cancels_reconnect_candidate_before_it_can_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = ResponsiveTransport("before-reconnect-stop")
    blocked = ResponsiveTransport("blocked-reconnect")
    blocked.block_command = "$I"
    blocked.write_release.clear()
    machine, factory = make_machine(monkeypatch, initial, blocked)
    machine.connect()
    machine.request_stop(_recover=False)
    errors: list[BaseException] = []
    worker = threading.Thread(
        target=lambda: _capture_error(machine.replace_connection, errors),
        daemon=True,
    )
    worker.start()
    assert blocked.write_entered.wait(1.0)

    machine.request_stop(_recover=False)
    blocked.write_release.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert errors and "cancelled" in str(errors[0]).lower()
    assert blocked.closed.wait(1.0)
    assert machine.status()["controller_state"] == "DISCONNECTED"
    assert machine._session is None
    assert len(factory.created) == 2


def test_secondary_fault_status_cannot_frame_primary_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("secondary-isolation")
    machine, _factory = make_machine(monkeypatch, transport)
    machine.connect()
    machine._secondary_air_assist = SimpleNamespace(
        status=SimpleNamespace(
            ready=False,
            enabled=None,
            fault="secondary USB failed",
            port="/dev/ttySECONDARY",
            baudrate=115200,
            mapping_digest="a" * 64,
        )
    )
    transport.set_next_reply("$I", GRBL_REALTIME, GRBL_IDENTITY, "ok")

    assert machine.send_command("$I") == [GRBL_IDENTITY, "ok"]
    status = machine.status()
    assert status["controller_state"] == "READY_HOME_REQUIRED"
    assert status["secondary_air_assist"]["fault"] == "secondary USB failed"
    machine._secondary_air_assist = None
    machine.disconnect()


def test_shutdown_prevents_blocked_candidate_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ResponsiveTransport("shutdown")
    transport.block_command = "$I"
    transport.write_release.clear()
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_REALTIME_STOP_WRITE_DEADLINE_SECONDS", 0.05)
    errors: list[BaseException] = []
    connector = threading.Thread(
        target=lambda: _capture_error(machine.connect, errors),
        daemon=True,
    )
    connector.start()
    assert transport.write_entered.wait(1.0)

    machine.shutdown(deadline=time.monotonic() + 0.2)
    transport.write_release.set()
    connector.join(2.0)

    assert not connector.is_alive()
    assert errors
    status = machine.status()
    assert status["controller_state"] == "SHUTTING_DOWN"
    assert status["connected"] is False
    assert machine._session is None


def test_shutdown_during_recovery_prevents_candidate_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = ResponsiveTransport("before-shutdown-recovery")
    recovery = ResponsiveTransport("shutdown-recovery-candidate")
    recovery.block_command = "$I"
    recovery.write_release.clear()
    machine, _factory = make_machine(monkeypatch, initial, recovery)
    monkeypatch.setattr(service_module, "_REALTIME_STOP_WRITE_DEADLINE_SECONDS", 0.02)
    machine.connect()

    machine.request_stop()
    assert recovery.write_entered.wait(1.0)
    machine.shutdown(deadline=time.monotonic() + 0.1)
    recovery.write_release.set()
    wait_for_recovery(machine)

    status = machine.status()
    assert status["controller_state"] == "SHUTTING_DOWN"
    assert status["connected"] is False
    assert machine._session is None
    assert recovery.closed.wait(1.0)


def test_twenty_stop_recover_home_cycles_use_fresh_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine, factory = make_machine(monkeypatch)
    machine.connect()
    generations: list[int] = []

    for _cycle in range(20):
        previous_generation = machine.status()["controller_session_generation"]
        machine.request_stop()
        wait_for_recovery(machine)
        recovered = machine.status()
        assert recovered["controller_state"] == "READY_HOME_REQUIRED"
        assert recovered["controller_session_generation"] > previous_generation
        assert recovered["coordinate_reference_ready"] is False
        machine.prepare_job_start()
        homed = machine.status()
        assert homed["controller_state"] == "READY_MOTION"
        assert homed["coordinate_reference_session_generation"] == homed[
            "controller_session_generation"
        ]
        generations.append(homed["controller_session_generation"])

    assert generations == sorted(set(generations))
    assert len(factory.created) == 21
    machine.disconnect()


@pytest.mark.parametrize(
    ("command", "responses", "error_match"),
    [
        ("$$", (), "acknowledge"),
        ("$$", ("$30=1000", "$32=1", "ok"), "did not report.*\\$1"),
        ("$G", (), "acknowledge"),
        ("$#", GRBL_OFFSETS[:-1], "acknowledge"),
    ],
    ids=["settings-timeout", "settings-missing-dollar-one", "modal-timeout", "offset-timeout"],
)
def test_connect_stage_faults_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    command: str,
    responses: tuple[str, ...],
    error_match: str,
) -> None:
    caplog.set_level(logging.INFO, logger=service_module.__name__)
    transport = ResponsiveTransport("stage-fault", replies={command: [responses]})
    machine, _factory = make_machine(monkeypatch, transport)
    monkeypatch.setattr(service_module, "_CONTROLLER_CONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(service_module, "_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(MachineError, match=error_match):
        machine.connect()

    assert machine.status()["controller_state"] == "FAULTED"
    assert transport.closed.is_set()
    journal = caplog.text
    assert "primary controller connect attempt failed" in journal
    assert "generation=1" in journal
    assert "endpoint=/dev/ttyACM-stage-fault" in journal
    assert "transcript=" in journal
    assert command in journal
    assert command in journal


def _drive_recovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MachineService, TransportFactory]:
    initial = ResponsiveTransport("before-recovery-failure")
    machine, factory = make_machine(monkeypatch, initial)
    machine.connect()
    for attempt in range(3):
        failed = ResponsiveTransport(f"failed-recovery-{attempt}")
        failed.synchronize_input = None  # type: ignore[method-assign]
        factory.pending.append(failed)
    machine.request_stop()
    wait_for_recovery(machine)
    assert machine.status()["controller_state"] == "RECONNECT_REQUIRED"
    return machine, factory


def test_recovery_failure_is_actionable_reconnect_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine, factory = _drive_recovery_failure(monkeypatch)

    status = machine.status()
    assert status["connected"] is False
    assert status["controller_reconnect_required"] is True
    assert status["controller_diagnostics"]["last_failure"]
    assert len(factory.created) == 4
    machine.disconnect()


def test_explicit_reconnect_after_recovery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine, factory = _drive_recovery_failure(monkeypatch)
    fresh = ResponsiveTransport("explicit-reconnect")
    factory.pending.append(fresh)

    result = machine.replace_connection()

    assert result["controller_state"] == "READY_HOME_REQUIRED"
    assert result["connected"] is True
    assert result["controller_session"]["resolved_endpoint"].endswith(
        "explicit-reconnect"
    )
    machine.disconnect()


def test_physical_dollar_hash_failure_sequence_has_all_seven_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=service_module.__name__)
    old_job_session = ResponsiveTransport("trusted-job-session")
    failed_home_session = ResponsiveTransport(
        "failed-home-session",
        replies={"$#": [GRBL_OFFSETS, GRBL_OFFSETS[:-1]]},
    )
    final_session = ResponsiveTransport("final-home-session")
    machine, factory = make_machine(
        monkeypatch,
        old_job_session,
        failed_home_session,
        final_session,
    )
    monkeypatch.setattr(service_module, "_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(service_module, "_REALTIME_STOP_WRITE_DEADLINE_SECONDS", 0.02)
    machine.connect()
    machine.prepare_job_start()
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X1 Y1 F100\nM5\n"
    )
    old_job_session.block_command = "M5"
    old_job_session.write_release.clear()
    machine.start_validated_program(program, "interrupted.gcode")
    assert old_job_session.write_entered.wait(1.0)

    machine.request_stop()
    old_job_session.write_release.set()
    job_worker = machine._job_thread
    if job_worker is not None:
        job_worker.join(2.0)
        assert not job_worker.is_alive()
    wait_for_recovery(machine)
    assert old_job_session.closed.wait(1.0)
    assert machine.status()["controller_state"] == "READY_HOME_REQUIRED"
    assert machine.status()["job"]["running"] is False
    assert not any(
        channel == "line"
        and isinstance(payload, str)
        and payload.startswith(("$H", "G0", "G1", "M3", "M4"))
        for channel, payload in failed_home_session.writes
    )
    failed_generation = machine.status()["controller_session_generation"]

    with pytest.raises(MachineError, match="acknowledge"):
        machine.prepare_job_start()
    failed_home_session.drain()
    failed_home_session.queue_response("ok")
    assert failed_home_session.closed.is_set()  # Outcome 1.
    failed_status = machine.status()
    assert failed_status["controller_state"] != "READY_MOTION"  # Outcome 3.
    assert failed_status["coordinate_reference_ready"] is False
    failed_diagnostics = failed_status["controller_diagnostics"]
    assert failed_diagnostics["last_failure_code"] == "controller.command_timeout"
    assert failed_diagnostics["last_failed_command"] == "$#"
    assert failed_diagnostics["failure_detail"]
    assert failed_diagnostics["last_stop_at"] is not None
    assert "primary controller Home failed" in caplog.text
    assert "command='$#'" in caplog.text or "TX" in caplog.text
    wait_for_recovery(machine)

    recovered = machine.status()
    assert recovered["controller_session_generation"] > failed_generation  # Outcome 4.
    assert recovered["controller_state"] == "READY_HOME_REQUIRED"  # Outcome 5.
    assert recovered["controller_diagnostics"]["last_recovery_at"] is not None
    assert failed_home_session.read_line(0.0) == "ok"  # Outcome 2.
    same_service = id(machine)
    machine.prepare_job_start()
    completed = machine.status()
    assert completed["controller_state"] == "READY_MOTION"  # Outcome 6.
    assert id(machine) == same_service  # Outcome 7.
    assert len(factory.created) == 3
    assert not final_session.closed.is_set()
    assert ("line", "G0 X1 Y1 F100") not in final_session.writes
    machine.disconnect()


def test_seeded_1000_controller_session_lifecycle_soak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run production connect/transaction/STOP paths for 1,000 seeded lifecycles."""

    rng = random.Random(0xE3)
    factory = TransportFactory()
    monkeypatch.setattr(service_module, "create_machine_transport", factory)
    monkeypatch.setattr(service_module, "_INITIAL_CONNECT_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(service_module, "_REALTIME_STOP_WRITE_DEADLINE_SECONDS", 0.01)
    # Dedicated framing tests above exercise the real millisecond boundary;
    # the soak targets lifecycle/session ownership while remaining CI-bounded.
    monkeypatch.setattr(service_module, "_POST_TERMINAL_QUIET_SECONDS", 0.0)
    machine = MachineService(machine_settings(), LaserSettings(), hardware_enabled=True)
    initial_threads = {thread.ident for thread in threading.enumerate()}
    previous_generation = 0

    for lifecycle in range(1_000):
        injected = rng.choice(
            (
                "startup_chatter",
                "partial_line",
                "status_frame",
                "delayed_ack",
                "transient_open_failure",
                "stop_race",
                "client_reconnect",
                "polling",
            )
        )
        if injected == "transient_open_failure":
            factory.pending.append(
                ResponsiveTransport(
                    f"soak-open-failure-{lifecycle}",
                    open_error=OSError("seeded transient open failure"),
                )
            )
        startup = ()
        if injected == "startup_chatter":
            startup = ("Grbl 1.1h ['$' for help]", "[MSG:hello]")
        elif injected == "partial_line":
            startup = ("<Idle|MPos:partial",)
        transport = ResponsiveTransport(
            f"soak-{lifecycle}",
            startup=startup,
        )
        factory.pending.append(transport)

        connected = machine.connect()
        generation = connected["controller_session_generation"]
        assert generation > previous_generation
        assert connected["controller_state"] == "READY_HOME_REQUIRED"
        assert connected["coordinate_reference_ready"] is False

        if injected == "status_frame":
            transport.set_next_reply("$I", GRBL_REALTIME, GRBL_IDENTITY, "ok")
            assert machine.send_command("$I") == [GRBL_IDENTITY, "ok"]
        elif injected == "delayed_ack":
            transport.set_next_timeout("$I")
            with pytest.raises(MachineError, match="acknowledge"):
                machine.send_command("$I", timeout=0.001)
            transport.drain()
            transport.queue_response("ok")
            wait_for_recovery(machine)
            assert transport.read_line(0.0) == "ok"
            assert machine.status()["controller_session_generation"] > generation
        elif injected == "stop_race":
            machine.request_stop()
            wait_for_recovery(machine)
        elif injected == "client_reconnect":
            machine.request_stop()
            wait_for_recovery(machine)
            generation_after_recovery = machine.status()[
                "controller_session_generation"
            ]
            assert machine.replace_connection()[
                "controller_session_generation"
            ] == generation_after_recovery
        elif injected == "polling":
            for _poll in range(3):
                polled = machine.status()
                assert polled["controller_state"] == "READY_HOME_REQUIRED"

        current = machine.status()
        assert current["connected"] is True
        assert current["controller_reconnect_required"] is False
        assert current["controller_state"] == "READY_HOME_REQUIRED"
        assert current["coordinate_reference_ready"] is False
        with machine._lock:
            assert machine._session is not None
            assert machine._session.generation == current[
                "controller_session_generation"
            ]
        for created in factory.created:
            assert created.concurrent_line_write_detected is False
            assert not any(
                isinstance(payload, str)
                and payload.startswith(("G0", "G1", "M3", "M4", "$H"))
                for _channel, payload in created.writes
            )
        machine.disconnect()
        disconnected = machine.status()
        assert disconnected["controller_state"] == "DISCONNECTED"
        assert disconnected["connected"] is False
        assert disconnected["controller_session"] is None
        previous_generation = current["controller_session_generation"]

    assert machine.status()["controller_session_generation"] >= 1_000
    assert {thread.ident for thread in threading.enumerate()} == initial_threads


@dataclass(frozen=True, slots=True)
class FaultMatrixCase:
    number: int
    scenario: str
    verification: str


_CORE = "tests/test_controller_session.py::"
_MACHINE = "tests/test_machine.py::"
_TRANSCRIPTS = "tests/test_machine_transcripts.py::"
_POSIX = "tests/test_serial_posix.py::"
_SECONDARY = "tests/test_secondary_controller.py::"
_PI = "tests/test_pi_machine_server.py::"
_DESKTOP = "tests/test_desktop_reconnect.py::"
_DESKTOP_STATE = "tests/test_desktop_machine_state.py::"

FAULT_MATRIX = (
    FaultMatrixCase(1, "clean GRBL startup and handshake", _CORE + "test_connect_publishes_only_after_full_grbl_handshake"),
    FaultMatrixCase(2, "complete startup chatter", _CORE + "test_primary_synchronization_discards_chatter_arriving_during_settle"),
    FaultMatrixCase(3, "partial unterminated startup bytes", _POSIX + "test_synchronize_input_discards_queued_partial_and_kernel_rx_bytes"),
    FaultMatrixCase(4, "invalid UTF-8 startup bytes", _POSIX + "test_synchronize_input_discards_queued_partial_and_kernel_rx_bytes"),
    FaultMatrixCase(5, "bytes arriving during settle interval", _CORE + "test_primary_synchronization_discards_chatter_arriving_during_settle"),
    FaultMatrixCase(6, "bytes pending in kernel RX", _POSIX + "test_synchronize_input_discards_queued_partial_and_kernel_rx_bytes"),
    FaultMatrixCase(7, "delayed ok from previous command", _CORE + "test_physical_dollar_hash_failure_sequence_has_all_seven_outcomes"),
    FaultMatrixCase(8, "delayed ok from previous generation", _CORE + "test_delayed_old_generation_ack_cannot_complete_new_dollar_hash"),
    FaultMatrixCase(9, "duplicate ok", _CORE + "test_millisecond_delayed_duplicate_ack_is_rejected_before_next_command"),
    FaultMatrixCase(10, "status interleaved with payload", _CORE + "test_realtime_status_is_diverted_from_command_payload"),
    FaultMatrixCase(11, "startup banner mid-handshake", _CORE + "test_startup_or_malformed_frame_quarantines_and_clears_pending_transaction"),
    FaultMatrixCase(12, "complete consumed error", _CORE + "test_consumed_error_and_alarm_do_not_poison_session"),
    FaultMatrixCase(13, "complete consumed alarm", _CORE + "test_consumed_error_and_alarm_do_not_poison_session"),
    FaultMatrixCase(14, "exact pre-home error 9", _CORE + "test_exact_pre_home_error_9_uses_only_bounded_unlock_and_still_requires_home"),
    FaultMatrixCase(15, "non-error-9 rejection", _CORE + "test_non_error_9_pre_home_rejection_never_unlocks_or_publishes"),
    FaultMatrixCase(16, "write failure before any byte", _CORE + "test_failed_or_partial_write_quarantines_exact_session"),
    FaultMatrixCase(17, "partial write failure", _CORE + "test_failed_or_partial_write_quarantines_exact_session"),
    FaultMatrixCase(18, "read failure", _CORE + "test_read_failure_quarantines_exact_session_and_records_command"),
    FaultMatrixCase(19, "$I timeout", _CORE + "test_overall_connect_deadline_clamps_identity_transaction"),
    FaultMatrixCase(20, "$$ timeout", _CORE + "test_connect_stage_faults_fail_closed"),
    FaultMatrixCase(21, "valid $$ without $1", _CORE + "test_connect_stage_faults_fail_closed"),
    FaultMatrixCase(22, "stale $1=255 recovery", _CORE + "test_stale_step_idle_hold_is_repaired_and_verified_before_publication"),
    FaultMatrixCase(23, "$G timeout", _CORE + "test_connect_stage_faults_fail_closed"),
    FaultMatrixCase(24, "$# timeout", _CORE + "test_physical_dollar_hash_failure_sequence_has_all_seven_outcomes"),
    FaultMatrixCase(25, "$# without active workspace", _CORE + "test_incomplete_coordinate_payload_never_publishes_candidate"),
    FaultMatrixCase(26, "$# without G92", _CORE + "test_incomplete_coordinate_payload_never_publishes_candidate"),
    FaultMatrixCase(27, "realtime query timeout", _CORE + "test_invalid_realtime_handshake_never_publishes_candidate"),
    FaultMatrixCase(28, "malformed realtime frame", _CORE + "test_invalid_realtime_handshake_never_publishes_candidate"),
    FaultMatrixCase(29, "STOP while waiting for ACK", _CORE + "test_stop_while_waiting_for_ack_quarantines_and_recovers_fresh_session"),
    FaultMatrixCase(30, "STOP while write gate occupied", _CORE + "test_stop_stays_bounded_while_write_gate_is_occupied"),
    FaultMatrixCase(31, "STOP while homing", _CORE + "test_stop_during_homing_quarantines_old_generation_without_ready_publish"),
    FaultMatrixCase(32, "STOP while job streaming", _TRANSCRIPTS + "test_stop_during_active_job_cancels_stream_without_receipt"),
    FaultMatrixCase(33, "STOP before job completion", _MACHINE + "test_stop_during_final_ack_cannot_publish_success_receipt"),
    FaultMatrixCase(34, "reconnect while worker unwinds", _CORE + "test_reconnect_can_publish_while_old_job_worker_unwinds"),
    FaultMatrixCase(35, "stale cleanup closes new transport", _CORE + "test_stale_job_cleanup_cannot_close_replacement_transport"),
    FaultMatrixCase(36, "stale worker writes new transport", _CORE + "test_stale_job_worker_cannot_write_replacement_transport"),
    FaultMatrixCase(37, "repeated reconnect clicks", _PI + "test_two_clients_share_one_replacement_and_stale_disconnect_is_rejected"),
    FaultMatrixCase(38, "Connect and Reconnect race", _CORE + "test_connect_and_reconnect_race_share_one_published_session"),
    FaultMatrixCase(39, "Reconnect and STOP race", _CORE + "test_stop_cancels_reconnect_candidate_before_it_can_publish"),
    FaultMatrixCase(40, "Home and polling race", _CORE + "test_home_and_status_poll_are_coherent_and_two_home_requests_exclude"),
    FaultMatrixCase(41, "two simultaneous Home requests", _CORE + "test_home_and_status_poll_are_coherent_and_two_home_requests_exclude"),
    FaultMatrixCase(42, "two desktop clients", _PI + "test_two_clients_share_one_replacement_and_stale_disconnect_is_rejected"),
    FaultMatrixCase(43, "client closes during another job", _PI + "test_authenticated_client_disconnect_does_not_stop_accepted_powered_job"),
    FaultMatrixCase(44, "primary USB disappears idle", _CORE + "test_read_failure_quarantines_exact_session_and_records_command"),
    FaultMatrixCase(45, "primary USB disappears during job", _PI + "test_pi_local_controller_failure_persists_failed_without_auto_retry"),
    FaultMatrixCase(46, "device reappears same by-id path", _CORE + "test_twenty_stop_recover_home_cycles_use_fresh_generations"),
    FaultMatrixCase(47, "wrong controller at path", _CORE + "test_wrong_controller_identity_at_configured_path_fails_closed"),
    FaultMatrixCase(48, "Pi service restart", _PI + "test_pi_restart_marks_persisted_running_job_interrupted_without_resume"),
    FaultMatrixCase(49, "Windows disconnect reconnect", _DESKTOP + "test_explicit_reconnect_disconnects_then_connects_without_motion_or_home"),
    FaultMatrixCase(50, "shutdown during recovery", _CORE + "test_shutdown_during_recovery_prevents_candidate_publication"),
    FaultMatrixCase(51, "secondary fault isolation", _CORE + "test_secondary_fault_status_cannot_frame_primary_transaction"),
    FaultMatrixCase(52, "secondary synchronize regression", _SECONDARY + "test_initial_open_applies_startup_delay_and_synchronizes_before_exact_off"),
    FaultMatrixCase(53, "no motion or laser in recovery", _CORE + "test_physical_dollar_hash_failure_sequence_has_all_seven_outcomes"),
    FaultMatrixCase(54, "no automatic job resume", _PI + "test_pi_restart_marks_persisted_running_job_interrupted_without_resume"),
    FaultMatrixCase(55, "Home bound to exact generation", _CORE + "test_home_is_generation_gated_and_rejected_when_already_motion_ready"),
    FaultMatrixCase(56, "no ONLINE reconnect contradiction", _DESKTOP_STATE + "test_reconnect_and_disconnected_states_cannot_claim_motion_ready"),
    FaultMatrixCase(57, "automatic recovery reaches HOME REQUIRED", _CORE + "test_twenty_stop_recover_home_cycles_use_fresh_generations"),
    FaultMatrixCase(58, "automatic recovery failure", _CORE + "test_recovery_failure_is_actionable_reconnect_required"),
    FaultMatrixCase(59, "explicit reconnect after failure", _CORE + "test_explicit_reconnect_after_recovery_failure"),
    FaultMatrixCase(60, "twenty STOP recover Home cycles", _CORE + "test_twenty_stop_recover_home_cycles_use_fresh_generations"),
)


@pytest.mark.parametrize(
    "case",
    FAULT_MATRIX,
    ids=lambda case: f"{case.number:02d}-{case.scenario}",
)
def test_fault_matrix_has_explicit_verification_owner(case: FaultMatrixCase) -> None:
    assert len(FAULT_MATRIX) == 60
    assert [item.number for item in FAULT_MATRIX] == list(range(1, 61))
    assert case.scenario
    relative_path, separator, owner_name = case.verification.partition("::")
    assert separator == "::"
    assert relative_path.startswith("tests/")
    assert owner_name.startswith("test_")
    source_path = Path(__file__).resolve().parents[1] / relative_path
    assert source_path.is_file(), f"Missing verification file: {relative_path}"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=relative_path)
    owners = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert owner_name in owners, f"Unresolved executable owner: {case.verification}"
    assert any(isinstance(node, ast.Assert) for node in ast.walk(owners[owner_name]))
