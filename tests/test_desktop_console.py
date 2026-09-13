from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.core import CoreRuntime
from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import ConsolePanel
from laser_aligner.machine.service import MachineService


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _DiagnosticMachine:
    """Canned read-only replies, with the real manual-command validator."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def operation_generation(self) -> int:
        return 0

    @contextmanager
    def operation_scope(self, _generation: int):
        yield

    def ensure_connected(self) -> None:
        pass

    def status(self) -> dict[str, object]:
        # Pi monitoring responses deliberately have no controller log.
        return {
            "connected": True,
            "controller_state": "READY_HOME_REQUIRED",
            "controller_session_generation": 1,
            "controller_state_revision": 1,
            "job": {},
        }

    def send_command(self, line: str) -> list[str]:
        cleaned = MachineService._validate_manual_command(self, line)
        self.commands.append(cleaned)
        return {
            "$I": ["[VER:TEST CONTROLLER]", "ok"],
            "$$": ["$30=1000", "$152=30", "ok"],
            "M5": [],
        }[cleaned]


def _wait_for_tasks(
    application: QtWidgets.QApplication, controller: DesktopController,
) -> None:
    deadline = time.monotonic() + 3.0
    while controller.has_active_tasks:
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for diagnostic task")
        time.sleep(0.005)
    application.processEvents()


def _console() -> tuple[ConsolePanel, DesktopController, _DiagnosticMachine]:
    machine = _DiagnosticMachine()
    runtime = SimpleNamespace(
        context=SimpleNamespace(machine=machine),
        running=True,
        status=lambda: {"machine": machine.status()},
    )
    controller = DesktopController(runtime)
    panel = ConsolePanel()
    panel.commandSubmitted.connect(controller.send_diagnostic)
    controller.diagnosticOutput.connect(panel.append_line)
    controller.busyChanged.connect(panel.set_busy)

    def refresh(status: dict) -> None:
        panel.set_status(status["machine"])
        panel.set_lines(status["machine"].get("log", []))

    controller.statusChanged.connect(refresh)
    controller.poll_status()
    return panel, controller, machine


def test_enter_and_send_replies_survive_empty_remote_status_and_remain_selectable(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel, controller, machine = _console()
    panel.command.setText("$I")
    QtTest.QTest.keyClick(panel.command, QtCore.Qt.Key.Key_Return)
    _wait_for_tasks(qt_application, controller)
    panel.command.setText("$$")
    panel.send.click()
    _wait_for_tasks(qt_application, controller)

    expected = "> $I\n[VER:TEST CONTROLLER]\nok\n> $$\n$30=1000\n$152=30\nok"
    assert machine.commands == ["$I", "$$"]
    assert panel.command.text() == ""
    assert panel.output.toPlainText() == expected
    panel.output.selectAll()
    for _ in range(3):
        controller.poll_status()
    assert panel.output.toPlainText() == expected
    assert panel.output.textCursor().selectedText().replace("\u2029", "\n") == expected
    assert panel.output.isReadOnly()
    panel.close()


def test_rejected_command_is_visible_and_never_sent(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel, controller, machine = _console()
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    panel.command.setText("M3 S100")
    panel.send.click()
    _wait_for_tasks(qt_application, controller)
    controller.poll_status()

    assert machine.commands == []
    assert panel.output.toPlainText().startswith("> M3 S100\nERROR: Manual commands")
    assert len(errors) == 1
    assert "read-only" in errors[0]
    panel.close()


def test_acknowledgement_without_response_lines_is_visible(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel, controller, machine = _console()
    panel.command.setText("M5")
    panel.send.click()
    _wait_for_tasks(qt_application, controller)
    assert machine.commands == ["M5"]
    assert panel.output.toPlainText() == "> M5\nacknowledged"
    panel.close()


def test_disabled_console_cannot_submit(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel, controller, machine = _console()
    panel.set_status({"controller_state": "JOB_RUNNING", "job": {"running": True}})
    panel.command.setText("$I")
    panel._submit()
    assert not panel.send.isEnabled()
    assert not controller.has_active_tasks
    assert machine.commands == []
    panel.close()


def test_controller_log_snapshots_do_not_accumulate_and_transcript_is_bounded(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = ConsolePanel()
    panel.set_lines(["12:00:00 RX ready"])
    panel.append_line("> $I\ncontroller identity\nok")
    for _ in range(3):
        panel.set_lines(["12:00:00 RX ready"])
    assert panel.output.toPlainText().count("12:00:00 RX ready") == 1
    panel.set_lines(["12:00:01 RX idle"])
    assert "12:00:00 RX ready" not in panel.output.toPlainText()
    assert "controller identity" in panel.output.toPlainText()
    panel.append_line("\n".join(f"reply {index}" for index in range(1200)))
    panel.set_lines([])
    assert panel.output.document().blockCount() == 1000
    assert panel.output.toPlainText().splitlines()[0] == "reply 200"
    assert panel.output.toPlainText().splitlines()[-1] == "reply 1199"
    panel.close()


def test_main_window_connects_diagnostic_output_to_persistent_console(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("XDG_DATA_HOME", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(name, str(tmp_path / "user-data"))
    monkeypatch.setattr(DesktopController, "start", lambda self: None)
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "default.json").read_text())
    payload["app"]["data_dir"] = str(tmp_path / "data")
    payload["camera"]["autostart"] = False
    payload["machine"]["port"] = "COM_TEST"
    config = tmp_path / "config.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    window = E3MainWindow(CoreRuntime.from_config(config))
    try:
        window.controller._diagnostic_complete("$I", ["[VER:TEST CONTROLLER]", "ok"])
        window._runtime_status({"machine": _DiagnosticMachine().status()})
        assert window.console_panel.output.toPlainText() == (
            "> $I\n[VER:TEST CONTROLLER]\nok"
        )
        cursor = window.console_panel.output.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
        assert cursor.block().text() == "> $I"
    finally:
        window.history.mark_clean()
        window.controller.stop()
        window._closing = True
        window.close()
        window.deleteLater()
        qt_application.processEvents()
