"""Bounded, read-only readiness handshake on the secondary owner's transport."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from ..errors import MachineError


class StartupTransport(Protocol):
    def write_line(self, line: str) -> None: ...
    def read_line(self, timeout: float = 1.0) -> str | None: ...
    def synchronize_input(self) -> None: ...


def wait_for_marlin(
    transport: StartupTransport, *, clock: Callable[[], float] = time.monotonic,
) -> None:
    """Allow 45 seconds for updater/native initialization, never reset or BOOT.

    Only M115 is retried. A firmware identity followed by its OK is required;
    an ACK alone or USB enumeration is insufficient. No runtime command has
    been sent yet. Input synchronization separates attempts and the subsequent
    acknowledged fan-OFF exchange. This is not runtime motion recovery.
    """
    deadline = clock() + 45.0
    lines = total_bytes = 0
    last = "no response"
    for _attempt in range(15):
        if clock() >= deadline:
            break
        transport.synchronize_input()
        transport.write_line("M115")
        attempt_deadline = min(deadline, clock() + 3.0)
        identity = False
        while True:
            remaining = attempt_deadline - clock()
            if remaining <= 0:
                break
            response = transport.read_line(timeout=min(0.2, remaining))
            if response is None:
                continue
            lines += 1
            total_bytes += len(response)
            if lines > 512 or total_bytes > 32768:
                raise MachineError("Secondary startup response exceeded its bounded limit")
            text = response.strip()
            last = text[:160]
            if text.startswith("FIRMWARE_NAME:"):
                name = text.removeprefix("FIRMWARE_NAME:")
                if not (name == "Marlin" or name.startswith("Marlin ")):
                    raise MachineError(f"Unexpected secondary firmware during startup: {last}")
                identity = True
            elif text == "ok" or text.startswith("ok "):
                if identity:
                    # No stale readiness ACK may satisfy the following OFF.
                    transport.synchronize_input()
                    return
            elif text == "E3AUX1 UPDATER 0.1.0 BOARD=0401E013":
                raise MachineError("Secondary is in updater 0.1.0; install the startup-fixed SD firmware")
            elif text == "E3AUX1 UPDATER 0.2.0 BOARD=0401E013" or text == "ERR UNSUPPORTED":
                identity = False  # Updater is not application readiness.
            elif text == "start" or text.casefold().startswith("echo:marlin"):
                identity = False  # A reset invalidates a preceding identity.
            elif (
                text.casefold().startswith(("error", "alarm", "!!", "resend", "grbl", "e3aux1"))
                or "unknown command" in text.casefold()
            ):
                raise MachineError(f"Secondary startup rejected: {last}")
    raise MachineError(f"Secondary Marlin readiness timed out after at most 45 seconds; last: {last}")
