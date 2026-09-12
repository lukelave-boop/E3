"""Bounded secondary readiness with optional explicit, token-bound recovery."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from typing import Protocol

from ..errors import MachineError


class StartupTransport(Protocol):
    def write_line(self, line: str) -> None: ...
    def read_line(self, timeout: float = 1.0) -> str | None: ...
    def synchronize_input(self) -> None: ...


def wait_for_marlin(
    transport: StartupTransport, *, clock: Callable[[], float] = time.monotonic,
    guard=None, deadline: float | None = None, recover_halted: bool = False,
) -> tuple[str, ...]:
    """Allow 45 seconds for updater/native initialization.

    Only M115 is retried. A firmware identity followed by its OK is required;
    an ACK alone or USB enumeration is insufficient. No runtime command has
    been sent yet. Input synchronization separates attempts and the subsequent
    acknowledged fan-OFF exchange. Only explicit recover_halted permits one
    token-bound reset of supported halted firmware; normal startup never resets,
    sends BOOT, or resumes an interrupted operation.
    """
    deadline = min(clock() + 45.0, deadline) if deadline is not None else clock() + 45.0
    lines = total_bytes = 0
    last = "no response"
    recovery_requested = False

    def checked():
        return guard() if guard is not None else nullcontext()

    for _attempt in range(15):
        with checked():
            pass
        if clock() >= deadline:
            break
        with checked():
            transport.synchronize_input()
            transport.write_line("M115")
        attempt_deadline = min(deadline, clock() + 3.0)
        identity = False
        identity_lines = []
        halted_token = None
        while True:
            with checked():
                pass
            remaining = attempt_deadline - clock()
            if remaining <= 0:
                break
            response = transport.read_line(timeout=min(0.2, remaining))
            with checked():
                pass
            if response is None:
                continue
            lines += 1
            total_bytes += len(response)
            if lines > 512 or total_bytes > 32768:
                raise MachineError("Secondary startup response exceeded its bounded limit")
            text = response.strip()
            last = text[:160]
            halted = re.fullmatch(r"E3RECOVERY:1 STATE:HALTED BOARD:0401C013 TOKEN:([0-9A-F]{8})", text)
            if halted:
                if halted_token is not None or identity:
                    raise MachineError("Ambiguous Ender halt identity; recovery was not sent")
                halted_token = halted[1]
                identity = False
                continue
            if halted_token is not None and text != "ok":
                if text == "start" or text.casefold().startswith("echo:marlin"):
                    halted_token = None
                    identity = False
                    identity_lines.clear()
                    continue
                raise MachineError("Incomplete or mixed Ender halt identity; recovery was not sent")
            identity_lines.append(text)
            if text.startswith("FIRMWARE_NAME:"):
                name = text.removeprefix("FIRMWARE_NAME:")
                if not (name == "Marlin" or name.startswith("Marlin ")):
                    raise MachineError(f"Unexpected secondary firmware during startup: {last}")
                identity = True
            elif text == "ok" or text.startswith("ok "):
                if halted_token is not None:
                    if not recover_halted:
                        raise MachineError("Ender reports HALTED; explicit Ender recovery is required")
                    if recovery_requested:
                        raise MachineError("Ender remained HALTED after the single recovery request")
                    # Only a complete response to this fresh M115 may authorize
                    # the one-shot token command. No reset command is retried.
                    with checked():
                        transport.write_line(f"E3RECOVER {halted_token}")
                    recovery_requested = True
                    reset_deadline = min(deadline, clock() + 3.0)
                    resetting = False
                    while clock() < reset_deadline:
                        with checked():
                            pass
                        reply = transport.read_line(timeout=min(0.2, reset_deadline - clock()))
                        with checked():
                            pass
                        if reply is None:
                            continue
                        if reply.strip() != "E3RECOVERY:1 RESETTING":
                            raise MachineError(f"Ender recovery was not accepted: {reply[:160]}")
                        resetting = True
                        break
                    if not resetting:
                        raise MachineError("Ender recovery acknowledgement timed out; reset was not retried")
                    break  # Require another complete, fresh application identity.
                if identity:
                    if recovery_requested and "Cap:E3_RECOVERY_V1:1" not in identity_lines:
                        raise MachineError("Recovered Ender did not report the expected recovery firmware capability")
                    # No stale readiness ACK may satisfy the following OFF.
                    with checked():
                        transport.synchronize_input()
                    return tuple(identity_lines)
            elif text == "E3AUX1 UPDATER 0.1.0 BOARD=0401E013":
                raise MachineError("Secondary is in updater 0.1.0; install the startup-fixed SD firmware")
            elif text in {
                "E3AUX1 UPDATER 0.2.0 BOARD=0401E013",
                "E3AUX1 UPDATER 0.2.0 BOARD=0103E013",
                "E3AUX1 UPDATER 0.3.0 BOARD=0401C013",
                "ERR UNSUPPORTED",
            }:
                identity = False  # Updater is not application readiness.
            elif text == "start" or text.casefold().startswith("echo:marlin"):
                identity = False  # A reset invalidates a preceding identity.
                halted_token = None
                identity_lines.clear()
            elif (
                text.casefold().startswith(("error", "alarm", "!!", "resend", "grbl", "e3aux1", "e3recovery"))
                or "unknown command" in text.casefold()
            ):
                raise MachineError(f"Secondary startup rejected: {last}")
    raise MachineError(f"Secondary Marlin readiness timed out after at most 45 seconds; last: {last}")
