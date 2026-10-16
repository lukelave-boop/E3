"""Session-bound GRBL laser pause state; no transport or UI ownership here."""
from __future__ import annotations

import threading
import time

from ..errors import MachineError

CAPABILITY = "pi-job-pause-v1"
TRANSITION_TIMEOUT = 10.0
STATUS_TIMEOUT = 5.0


class JobPause:
    """Retain planner state using GRBL 1.1's laser-mode safety-door suspend.

    Door suspend disables the spindle regardless of the firmware's optional
    DISABLE_LASER_DURING_HOLD setting. Verified $32=1 forbids parking movement.
    Only an explicit resume issues cycle-start; faults use the existing STOP.
    The job worker owns polling and continues to dispatch (never steal) ACKs.
    """

    def __init__(self) -> None:
        self.state = "running"
        self.revision = 0
        self.started: float | None = None
        self.excluded = 0.0
        self.changed = 0.0
        self.last_status = 0.0
        self.last_query = 0.0
        self._lock = threading.RLock()

    def clock(self) -> float:
        with self._lock:
            now = time.monotonic()
            return (self.started if self.started is not None else now) - self.excluded

    def request(self, *, resume: bool) -> bool:
        with self._lock:
            now = time.monotonic()
            if resume:
                if self.state == "resuming":
                    return False
                if self.state != "paused":
                    raise MachineError("Resume requires a confirmed paused job")
                if now - self.last_status > STATUS_TIMEOUT:
                    raise MachineError("Paused controller status is stale; use STOP")
                self.state = "resuming"
            else:
                if self.state in {"pausing", "paused"}:
                    return False
                if self.state != "running":
                    raise MachineError("Wait for the current pause/resume transition")
                self.started = now
                self.last_status = now
                self.state = "pausing"
            self.changed = now
            self.revision += 1
            self.last_query = 0.0
            return True

    def observe(self, response: str) -> bool:
        """Validate telemetry; return permission for an expected Door state."""
        state = response[1:-1].split("|", 1)[0]
        with self._lock:
            if self.state == "running":
                return False
            now = time.monotonic()
            self.last_status = now
            if state == "Door:0":
                accessories = [part[2:] for part in response[1:-1].split("|") if part.startswith("A:")]
                if any("S" in part or "C" in part for part in accessories):
                    raise MachineError("Controller reports spindle output during pause")
                if self.state == "pausing":
                    self.state = "paused"
                    self.revision += 1
                return True
            if state in {"Door:1", "Door:2", "Door:3"}:
                # Door:1 means physically open: never authorize Resume.
                if self.state == "paused":
                    raise MachineError("Controller door state changed while paused")
                return True
            if state in {"Run", "Idle"}:
                if self.state == "resuming":
                    assert self.started is not None
                    self.excluded += now - self.started
                    self.started = None
                    self.state = "running"
                    self.revision += 1
                elif self.state == "paused":
                    raise MachineError("Controller resumed without an explicit Resume request")
                return False
            raise MachineError(f"Unexpected controller state during pause: {state}")

    def check(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self.state in {"pausing", "resuming"} and now - self.changed > TRANSITION_TIMEOUT:
                raise MachineError("Controller did not confirm pause/resume before its deadline")
            if self.state != "running" and now - self.last_status > STATUS_TIMEOUT:
                raise MachineError("Controller status was lost during pause")
