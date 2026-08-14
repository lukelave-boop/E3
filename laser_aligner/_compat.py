"""Small compatibility helpers for the project's supported Python versions."""

from __future__ import annotations

try:
    from enum import StrEnum as StrEnum
except ImportError:  # pragma: no cover - exercised by the Python 3.10 CI job
    from enum import Enum

    class StrEnum(str, Enum):
        """Python 3.10-compatible subset used by explicit string-valued enums."""

        def __str__(self) -> str:
            return str(self.value)


__all__ = ["StrEnum"]
