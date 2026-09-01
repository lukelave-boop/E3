from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable


def arm_process_exit_watchdog(
    deadline: float,
    *,
    exit_process: Callable[[int], object] = os._exit,
    monotonic: Callable[[], float] = time.monotonic,
) -> threading.Thread:
    """Force process termination at the desktop's absolute shutdown deadline.

    Qt's global thread pool can keep the interpreter alive after the final
    window and event loop have both closed.  This daemon is the last-resort
    process boundary for that case.  It is armed only by the production desktop
    entry point after the user has accepted the Close request; unit-created
    windows do not install it.
    """

    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(float(deadline))
    ):
        raise TypeError("Desktop shutdown deadline must be finite")
    deadline = float(deadline)

    def force_exit_at_deadline() -> None:
        remaining = max(0.0, deadline - monotonic())
        if remaining > 0.0:
            threading.Event().wait(remaining)
        # Nothing may run before this final process boundary. In particular,
        # logging can block forever on a handler lock held by another stuck
        # thread, which would defeat the deadline this watchdog exists to keep.
        exit_process(0)

    thread = threading.Thread(
        target=force_exit_at_deadline,
        name="e3-shutdown-watchdog",
        daemon=True,
    )
    thread.start()
    return thread


__all__ = ["arm_process_exit_watchdog"]
