"""One operator-requested CR Touch pin diagnostic, without any axis command.

MachineService owns admission and the shared Creality session. These exchanges
report controller replies only: neither an acknowledgement nor an open endstop
proves where the physical pin is. Each pin action needs operator observation.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import contextmanager

from ..errors import MachineError, SafetyError
from .secondary_controller import CrealityControllerOwner, WriteGuardFactory

_ACTIONS = {"inspect": None, "deploy": "M280 P0 S10", "stow": "M280 P0 S90"}
_MAX_TRANSCRIPT_LINES = 32
_MAX_LINE_CHARACTERS = 512
_EXCHANGE_TIMEOUT_SECONDS = 3.0
_PIN_SETTLE_SECONDS = 0.8
_FIRMWARE = re.compile(r"(?:^|\s)FIRMWARE_NAME:Marlin(?:\s|$)", re.I)
_MACHINE = re.compile(r"(?:^|\s)MACHINE_TYPE:Ender-3 S1 Pro(?:\s|$)", re.I)


class ProbePinDiagnosticError(MachineError):
    """A failed diagnostic with bounded, JSON-safe command context."""

    def __init__(self, message: str, diagnostic: dict[str, object]) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic
        self.transcript = diagnostic["transcript"]


def run_pin_diagnostic(
    owner: CrealityControllerOwner,
    action: str,
    guard: WriteGuardFactory,
    on_failure: Callable[[], None],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Inspect, deploy once, or stow once using an already initialized owner.

    There is no retry, homing, axis motion, automatic stow, or coordinate reset.
    A bounded pause after servo acknowledgement allows observation to settle;
    M119/M114 still cannot prove physical deployment or authorize a descent.
    The caller supplies all hardware,
    laser-off, exclusive-operation, STOP, and connection admission checks.
    """
    if type(action) is not str or action not in _ACTIONS:
        raise SafetyError("Probe pin action must be inspect, deploy, or stow")
    if not callable(guard) or not callable(on_failure) or not callable(sleep):
        raise TypeError("Probe pin guard, failure callback, and sleep must be callable")
    if not isinstance(owner, CrealityControllerOwner):
        raise TypeError("Probe pin diagnostics require the shared Creality owner")

    transcript: list[dict[str, object]] = []
    generation = owner.generation
    result: dict[str, object] = {
        "kind": "probe_pin_diagnostic",
        "action": action,
        "controller_generation": generation,
        "firmware": None,
        "transcript": transcript,
        "completed": False,
        "physical_pin_state": "unknown",
        "operator_observation_required": True,
    }

    @contextmanager
    def session_guard():
        with guard():
            if owner.generation != generation:
                raise MachineError("Creality session changed during the pin diagnostic")
            yield

    def execute(command: str) -> tuple[str, ...]:
        entry: dict[str, object] = {"command": command, "responses": []}
        transcript.append(entry)
        try:
            lines = owner._execute_acknowledged(
                command,
                write_guard=session_guard,
                allow_open=False,
                timeout=_EXCHANGE_TIMEOUT_SECONDS,
                on_failure=on_failure,
                interrupt_on_failure=False,
            )
            entry["responses"] = [
                line[:_MAX_LINE_CHARACTERS] for line in lines[:_MAX_TRANSCRIPT_LINES]
            ]
            entry["response_lines_count"] = len(lines)
            entry["responses_truncated"] = (
                len(lines) > _MAX_TRANSCRIPT_LINES
                or any(len(line) > _MAX_LINE_CHARACTERS for line in lines)
            )
            with session_guard():
                pass
            return lines
        except Exception as exc:
            entry["error"] = str(exc)[:_MAX_LINE_CHARACTERS]
            raise

    try:
        with session_guard():
            owner.raise_if_faulted()
            if not owner.ready:
                raise MachineError("The shared Creality controller must be initialized before a pin diagnostic")
        identity = " ".join(execute("M115"))
        if not _FIRMWARE.search(identity) or not _MACHINE.search(identity):
            raise MachineError("Expected the existing Ender-3 S1 Pro / Marlin controller")
        result["firmware"] = identity[:_MAX_LINE_CHARACTERS]
        if (command := _ACTIONS[action]) is not None:
            execute(command)
            # Raw M280 does not include the BLTouch helper's 750 ms alarm-aware
            # settling interval. Wait without holding the cross-controller gate,
            # then recheck cancellation before sending the observation queries.
            sleep(_PIN_SETTLE_SECONDS)
            with session_guard():
                pass
        execute("M119")
        execute("M114")
        with session_guard():
            result["completed"] = True
        return result
    except Exception as exc:
        detail = str(exc)[:_MAX_LINE_CHARACTERS]
        result["error"] = detail
        raise ProbePinDiagnosticError(detail, result) from exc
