from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .controller_dialects import ControllerDialect
    from .transport import MachineTransport


class ControllerState(str, Enum):
    """Authoritative lifecycle of the primary-controller communication session."""

    DISCONNECTED = "DISCONNECTED"
    OPENING = "OPENING"
    SYNCHRONIZING = "SYNCHRONIZING"
    READY_HOME_REQUIRED = "READY_HOME_REQUIRED"
    READY_MOTION = "READY_MOTION"
    JOB_RUNNING = "JOB_RUNNING"
    STOPPING = "STOPPING"
    RECOVERING = "RECOVERING"
    RECONNECT_REQUIRED = "RECONNECT_REQUIRED"
    FAULTED = "FAULTED"
    SHUTTING_DOWN = "SHUTTING_DOWN"


CONNECTED_CONTROLLER_STATES = frozenset(
    {
        ControllerState.READY_HOME_REQUIRED,
        ControllerState.READY_MOTION,
        ControllerState.JOB_RUNNING,
    }
)

CONNECTING_CONTROLLER_STATES = frozenset(
    {
        ControllerState.OPENING,
        ControllerState.SYNCHRONIZING,
        ControllerState.RECOVERING,
    }
)


def _bounded_text(value: object, *, limit: int = 240) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _plain_evidence(value: object) -> object:
    """Return a small JSON-compatible synchronization evidence value."""

    if value is None or type(value) in {bool, int, float, str}:
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return _plain_evidence(asdict(value))
    if isinstance(value, dict):
        return {
            _bounded_text(key, limit=80): _plain_evidence(item)
            for key, item in list(value.items())[:24]
        }
    if isinstance(value, (list, tuple)):
        return [_plain_evidence(item) for item in value[:24]]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _plain_evidence(to_dict())
        except Exception:
            pass
    return _bounded_text(value)


class ControllerSessionDiagnostics:
    """Bounded, thread-safe diagnostics owned by one immutable session identity."""

    def __init__(self, state: ControllerState, *, transcript_limit: int = 120) -> None:
        self._lock = threading.RLock()
        self._state = state
        self._command_sequence = 0
        self._last_tx_at: float | None = None
        self._last_tx_command: str | None = None
        self._last_failure: str | None = None
        self._last_failure_code: str | None = None
        self._last_failed_command: str | None = None
        self._last_successful_transaction: dict[str, Any] | None = None
        self._synchronization: object = None
        self._last_successful_sync_at: float | None = None
        self._firmware_identity: list[str] = []
        self._transcript: deque[str] = deque(maxlen=transcript_limit)

    def set_state(self, state: ControllerState) -> None:
        with self._lock:
            self._state = state

    def set_synchronization(self, evidence: object) -> None:
        with self._lock:
            self._synchronization = _plain_evidence(evidence)
            self._last_successful_sync_at = time.time()

    def set_firmware_identity(self, responses: list[str]) -> None:
        with self._lock:
            self._firmware_identity = [
                _bounded_text(response, limit=200) for response in responses[:12]
            ]

    def next_command(self, command: str) -> int:
        with self._lock:
            self._command_sequence += 1
            sequence = self._command_sequence
            self._last_tx_at = time.time()
            self._last_tx_command = _bounded_text(command, limit=160)
            self._transcript.append(
                f"{time.strftime('%H:%M:%S')} TX #{sequence} {self._last_tx_command}"
            )
            return sequence

    def record_rx(self, sequence: int | None, response: str) -> None:
        with self._lock:
            label = "-" if sequence is None else str(sequence)
            self._transcript.append(
                f"{time.strftime('%H:%M:%S')} RX #{label} "
                f"{_bounded_text(response, limit=200)}"
            )

    def record_event(self, message: str) -> None:
        with self._lock:
            self._transcript.append(
                f"{time.strftime('%H:%M:%S')} INFO {_bounded_text(message)}"
            )

    def record_failure(
        self,
        message: object,
        *,
        code: str = "controller.failure",
        command: str | None = None,
    ) -> None:
        with self._lock:
            self._last_failure = _bounded_text(message)
            self._last_failure_code = _bounded_text(code, limit=80)
            self._last_failed_command = (
                self._last_tx_command
                if command is None
                else _bounded_text(command, limit=160)
            )
            self._transcript.append(
                f"{time.strftime('%H:%M:%S')} ERROR {self._last_failure}"
            )

    def record_success(
        self,
        sequence: int,
        *,
        started_monotonic: float,
        terminal_classification: str,
    ) -> None:
        completed_at = time.time()
        duration = max(0.0, time.monotonic() - started_monotonic)
        with self._lock:
            self._last_successful_transaction = {
                "sequence": sequence,
                "completed_at": completed_at,
                "duration_seconds": duration,
                "terminal_classification": _bounded_text(
                    terminal_classification,
                    limit=80,
                ),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.value,
                "command_sequence": self._command_sequence,
                "last_tx_at": self._last_tx_at,
                "last_tx_command": self._last_tx_command,
                "last_failure": self._last_failure,
                "last_failure_code": self._last_failure_code,
                "last_failed_command": self._last_failed_command,
                "last_successful_transaction": (
                    None
                    if self._last_successful_transaction is None
                    else dict(self._last_successful_transaction)
                ),
                "synchronization": self._synchronization,
                "last_successful_sync_at": self._last_successful_sync_at,
                "firmware_identity": list(self._firmware_identity),
                "transcript": list(self._transcript),
            }


@dataclass(frozen=True, slots=True, eq=False)
class ControllerSession:
    """Immutable identity and exact transport binding for one logical session."""

    generation: int
    transport: MachineTransport
    dialect: ControllerDialect
    configured_endpoint: str
    resolved_endpoint: str
    baudrate: int
    created_at: float
    created_monotonic: float
    diagnostics: ControllerSessionDiagnostics

    def matches(self, other: ControllerSession | None) -> bool:
        return bool(
            other is not None
            and other.generation == self.generation
            and other.transport is self.transport
        )

    def status_snapshot(self) -> dict[str, Any]:
        diagnostics = self.diagnostics.snapshot()
        return {
            "generation": self.generation,
            "protocol": self.dialect.id,
            "configured_endpoint": self.configured_endpoint,
            "resolved_endpoint": self.resolved_endpoint,
            "baudrate": self.baudrate,
            "created_at": self.created_at,
            "command_sequence": diagnostics["command_sequence"],
            "last_tx_at": diagnostics["last_tx_at"],
            "last_tx_command": diagnostics["last_tx_command"],
            "last_failure": diagnostics["last_failure"],
            "last_failure_code": diagnostics["last_failure_code"],
            "last_failed_command": diagnostics["last_failed_command"],
            "last_successful_transaction": diagnostics[
                "last_successful_transaction"
            ],
            "synchronization": diagnostics["synchronization"],
            "last_successful_sync_at": diagnostics["last_successful_sync_at"],
            "firmware_identity": diagnostics["firmware_identity"],
        }


__all__ = [
    "CONNECTED_CONTROLLER_STATES",
    "CONNECTING_CONTROLLER_STATES",
    "ControllerSession",
    "ControllerSessionDiagnostics",
    "ControllerState",
]
