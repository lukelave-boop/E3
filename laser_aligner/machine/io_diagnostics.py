"""Bounded observational evidence; never controller authority or a watchdog."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any


class IOProgress:
    """Small checkpoints with a nonblocking diagnostic snapshot.

    The metadata gate is never held across I/O or a controller callback. A
    snapshot must still tolerate a stuck owner rather than delaying recovery.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"phase": "not_started"}
        self._counts: dict[str, int] = {}

    def mark(self, phase: str, **fields: Any) -> None:
        with self._lock:
            self._state.update(fields)
            self._state.update(phase=phase, checkpoint_monotonic=time.monotonic())
            self._counts[phase] = self._counts.get(phase, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"snapshot_unavailable": "metadata_busy"}
        try:
            now = time.monotonic()
            ages = {
                key.removesuffix("_monotonic"): round(max(0.0, now - value), 6)
                for key, value in self._state.items() if key.endswith("_monotonic")
            }
            return {**self._state, "ages_seconds": ages, "counts": dict(self._counts)}
        finally:
            self._lock.release()


def thread_snapshot(thread: threading.Thread | None) -> dict[str, Any]:
    """Only code locations, never frame locals, source text, or other threads."""
    if thread is None:
        return {"alive": False}
    result: dict[str, Any] = {
        "name": thread.name, "ident": thread.ident,
        "native_id": thread.native_id, "alive": thread.is_alive(),
    }
    frame = sys._current_frames().get(thread.ident)
    stack = []
    try:
        while frame is not None and len(stack) < 12:
            stack.append(f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno}:{frame.f_code.co_name}")
            frame = frame.f_back
    finally:
        del frame
    result["stack"] = stack
    return result
