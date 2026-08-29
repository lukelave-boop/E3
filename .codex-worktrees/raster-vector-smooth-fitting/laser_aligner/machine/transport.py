from __future__ import annotations

from typing import Protocol


class MachineTransport(Protocol):
    """Communication mechanics used by the guarded machine service."""

    def open(self) -> None: ...

    def close(self) -> None: ...

    def write_raw(self, data: bytes) -> None: ...

    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout: float = 1.0) -> str | None: ...

    def drain(self) -> list[str]: ...


__all__ = ["MachineTransport"]
