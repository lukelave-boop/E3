from __future__ import annotations

import subprocess
import sys
import threading
import time

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from laser_aligner.desktop.shutdown import arm_process_exit_watchdog


def _communicate_with_cleanup(
    process: subprocess.Popen[str],
    *,
    timeout: float,
) -> tuple[str, str]:
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(
            "watchdog subprocess did not terminate before the test timeout; "
            f"stdout={stdout!r}, stderr={stderr!r}"
        )


def test_process_exit_watchdog_uses_the_supplied_absolute_deadline() -> None:
    exited = threading.Event()
    exit_codes: list[int] = []
    started = time.monotonic()

    def record_exit(code: int) -> None:
        exit_codes.append(code)
        exited.set()

    thread = arm_process_exit_watchdog(
        started + 0.05,
        exit_process=record_exit,
    )

    assert exited.wait(0.5)
    thread.join(timeout=0.5)
    assert exit_codes == [0]
    assert 0.03 <= time.monotonic() - started < 0.5


def test_watchdog_terminates_process_with_stuck_qt_global_pool_worker() -> None:
    script = """
import time
from PySide6 import QtCore
from laser_aligner.desktop.shutdown import arm_process_exit_watchdog

class StuckTask(QtCore.QRunnable):
    def run(self):
        time.sleep(30.0)

application = QtCore.QCoreApplication([])
QtCore.QThreadPool.globalInstance().start(StuckTask())
print("READY", flush=True)
arm_process_exit_watchdog(time.monotonic() + 0.4)
QtCore.QTimer.singleShot(10, application.quit)
application.exec()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "READY"
    started = time.monotonic()
    _stdout, stderr = _communicate_with_cleanup(process, timeout=4.0)

    elapsed = time.monotonic() - started
    assert process.returncode == 0, stderr
    # Loaded xdist workers can delay Windows process-handle observation after
    # os._exit, but the stressed subprocess must still remain comfortably below
    # the product's five-second boundary.
    assert 0.2 <= elapsed < 3.0


def test_watchdog_exit_cannot_be_blocked_by_a_held_logging_handler() -> None:
    script = """
import logging
import time
from laser_aligner.desktop.shutdown import arm_process_exit_watchdog

handler = logging.StreamHandler()
logging.getLogger().addHandler(handler)
handler.acquire()
print("READY", flush=True)
arm_process_exit_watchdog(time.monotonic() + 0.25)
time.sleep(30.0)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "READY"
    started = time.monotonic()
    _stdout, stderr = _communicate_with_cleanup(process, timeout=4.0)

    elapsed = time.monotonic() - started
    assert process.returncode == 0, stderr
    assert 0.1 <= elapsed < 3.0


def test_production_main_window_close_signal_arms_process_boundary() -> None:
    script = """
import os
import time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtWidgets
from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.shutdown import arm_process_exit_watchdog

main_window_module.DESKTOP_SHUTDOWN_TIMEOUT_SECONDS = 0.4

class Controller:
    def __init__(self):
        self.deadline = None
    def begin_shutdown(self, deadline=None):
        if self.deadline is None:
            self.deadline = deadline
        return self.deadline
    def stop(self, deadline=None):
        self.begin_shutdown(deadline)

class StuckTask(QtCore.QRunnable):
    def run(self):
        time.sleep(30.0)

application = QtWidgets.QApplication([])
window = E3MainWindow.__new__(E3MainWindow)
QtWidgets.QMainWindow.__init__(window)
window.controller = Controller()
window._close_requested = False
window._closing = False
window._confirm_discard_changes = lambda: True
window._save_window_state = lambda: None
window._cancel_job_preparation = lambda *_args, **_kwargs: None
window._cancel_job_render = lambda: None
window._invalidate_generated_job = lambda **_kwargs: None
window.shutdownStarted.connect(arm_process_exit_watchdog)
QtCore.QThreadPool.globalInstance().start(StuckTask())
window.show()
print("READY", flush=True)
QtCore.QTimer.singleShot(10, window.close)
application.exec()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "READY"
    started = time.monotonic()
    _stdout, stderr = _communicate_with_cleanup(process, timeout=4.0)

    elapsed = time.monotonic() - started
    assert process.returncode == 0, stderr
    assert 0.2 <= elapsed < 3.0
