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


def add_exception_note(error: BaseException, note: str) -> None:
    """Attach one PEP 678-style note on every supported Python version."""

    if not isinstance(note, str):
        raise TypeError("exception note must be a string")
    native_add_note = getattr(error, "add_note", None)
    if callable(native_add_note):
        native_add_note(note)
        return
    notes = getattr(error, "__notes__", None)
    if notes is None:
        notes = []
        error.__notes__ = notes
    elif not isinstance(notes, list):
        notes = list(notes)
        error.__notes__ = notes
    notes.append(note)


__all__ = ["StrEnum", "add_exception_note"]
