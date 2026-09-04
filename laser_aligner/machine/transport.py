from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class InputSynchronizationEvidence:
    """Bounded evidence that a receive stream stayed quiet before a command.

    This is diagnostic evidence from software, not a safety-rated indication.
    ``discarded_bytes`` includes bytes observed directly while establishing the
    quiet window; ``discarded_lines`` covers already-framed queued replies.
    """

    configured_endpoint: str
    resolved_endpoint: str
    quiet_interval_seconds: float
    elapsed_seconds: float
    discarded_bytes: int
    discarded_lines: int
    observed_activity: bool


class MachineTransport(Protocol):
    """Communication mechanics used by the guarded machine service."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write_raw(self, data: bytes) -> None: ...

    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout: float = 1.0) -> str | None: ...

    def drain(self) -> list[str]: ...

    @property
    def configured_endpoint(self) -> str: ...

    @property
    def resolved_endpoint(self) -> str: ...

    def synchronize_input(
        self,
        *,
        quiet_interval: float = 0.15,
        timeout: float = 0.75,
    ) -> InputSynchronizationEvidence: ...


__all__ = ["InputSynchronizationEvidence", "MachineTransport"]
