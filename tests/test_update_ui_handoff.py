from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner import updates
from laser_aligner.desktop import update_ui


@pytest.fixture
def qt_application():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    application.setQuitOnLastWindowClosed(True)
    yield application
    application.setQuitOnLastWindowClosed(True)
    application.processEvents()


class _Window(QtWidgets.QMainWindow):
    def __init__(self, *, accept_close: bool = True) -> None:
        super().__init__()
        self.accept_close = accept_close
        self.events: list[str] = []

    def closeEvent(self, event) -> None:
        self.events.append("close")
        if self.accept_close:
            event.accept()
        else:
            event.ignore()

    def showEvent(self, event) -> None:
        self.events.append("show")
        super().showEvent(event)


class _Controller(QtCore.QObject):
    tasksDrained = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.active = True

    @property
    def has_active_tasks(self) -> bool:
        return self.active


class _PreparedWindow(_Window):
    def __init__(
        self,
        controller: _Controller,
        *,
        accept_prepare: bool = True,
        start_task_while_preparing: bool = False,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.accept_prepare = accept_prepare
        self.start_task_while_preparing = start_task_while_preparing

    def _prepare_close_request(self) -> bool:
        self.events.append("prepare")
        if not self.accept_prepare:
            return False
        if self.start_task_while_preparing:
            self.controller.active = True
        return True


class _AutoClosePreparedWindow(_PreparedWindow):
    def __init__(self, controller: _Controller) -> None:
        super().__init__(controller)
        self._close_requested = False
        controller.tasksDrained.connect(self._background_tasks_drained)

    def _prepare_close_request(self) -> bool:
        self.events.append("prepare")
        self._close_requested = True
        self.controller.active = True
        return True

    def _background_tasks_drained(self) -> None:
        if getattr(self, "_e3_update_idle_handoff", None) is not None:
            self.events.append("auto-close-suppressed")
            return
        if self._close_requested:
            self.events.append("auto-close-scheduled")
            QtCore.QTimer.singleShot(0, self.close)


def test_handoff_launches_after_close_without_last_window_exit(
    qt_application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    window = _Window()
    window.show()
    qt_application.processEvents()
    window.events.clear()

    package = tmp_path / "E3-Setup.exe"
    package.write_bytes(b"installer")
    launches: list[Path] = []

    def launch(path: Path) -> None:
        assert not window.isVisible()
        assert qt_application.quitOnLastWindowClosed() is False
        window.events.append("launch")
        launches.append(path)

    monkeypatch.setattr(update_ui, "launch_downloaded_update", launch)

    assert update_ui._handoff_downloaded_update(window, package) is True
    assert launches == [package]
    assert window.events[:2] == ["close", "launch"]


def test_handoff_rejected_close_never_launches(
    qt_application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    window = _Window(accept_close=False)
    window.show()
    qt_application.processEvents()

    monkeypatch.setattr(
        update_ui,
        "launch_downloaded_update",
        lambda _path: pytest.fail("installer launched after rejected close"),
    )

    assert (
        update_ui._handoff_downloaded_update(
            window,
            tmp_path / "E3-Setup.exe",
        )
        is False
    )
    assert window.isVisible()
    assert qt_application.quitOnLastWindowClosed() is True


def test_failed_installer_launch_restores_e3_window(
    qt_application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    window = _Window()
    window.show()
    qt_application.processEvents()

    def fail(_path: Path) -> None:
        raise OSError("simulated installer launch failure")

    monkeypatch.setattr(update_ui, "launch_downloaded_update", fail)

    with pytest.raises(OSError, match="simulated installer launch failure"):
        update_ui._handoff_downloaded_update(
            window,
            tmp_path / "E3-Setup.exe",
        )

    assert window.isVisible()
    assert qt_application.quitOnLastWindowClosed() is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher boundary")
def test_handoff_reaches_real_windows_launcher_boundary(
    qt_application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    window = _Window()
    window.show()
    qt_application.processEvents()
    window.events.clear()

    package = tmp_path / "E3-Setup.exe"
    package.write_bytes(b"installer")
    events: list[object] = []

    monkeypatch.setattr(
        updates,
        "_get_windows_dll_directory",
        lambda: events.append("capture") or r"C:\E3\_internal",
    )
    monkeypatch.setattr(
        updates,
        "_set_windows_dll_directory",
        lambda value, *, operation: events.append(("dll", value, operation)),
    )

    def popen(argv, **kwargs):
        events.append(("popen", argv, kwargs))
        return object()

    monkeypatch.setattr(updates.subprocess, "Popen", popen)

    assert update_ui._handoff_downloaded_update(window, package) is True
    assert window.events[0] == "close"
    assert events[0] == "capture"
    assert events[1][:2] == ("dll", None)
    assert events[2][0] == "popen"
    assert events[3][:2] == ("dll", r"C:\E3\_internal")


def test_requested_handoff_waits_for_controller_tasks_to_drain(
    qt_application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller = _Controller()
    controller.active = False
    window = _PreparedWindow(
        controller,
        start_task_while_preparing=True,
    )
    window.show()
    qt_application.processEvents()
    window.events.clear()
    package = tmp_path / "E3-Setup.exe"
    handoffs: list[Path] = []
    monkeypatch.setattr(
        update_ui,
        "_perform_downloaded_update_handoff",
        lambda _window, path: handoffs.append(path),
    )

    update_ui._request_downloaded_update_handoff(window, package)

    assert window.events == ["prepare"]
    assert handoffs == []
    assert window.isVisible()

    controller.active = False
    controller.tasksDrained.emit()
    qt_application.processEvents()

    assert handoffs == [package]

    controller.tasksDrained.emit()
    qt_application.processEvents()

    assert handoffs == [package]


def test_rejected_close_preparation_never_arms_or_launches_handoff(
    qt_application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller = _Controller()
    controller.active = True
    window = _PreparedWindow(controller)
    window.show()
    qt_application.processEvents()
    window.events.clear()
    handoffs: list[Path] = []
    monkeypatch.setattr(
        update_ui,
        "_perform_downloaded_update_handoff",
        lambda _window, path: handoffs.append(path),
    )

    update_ui._request_downloaded_update_handoff(
        window,
        tmp_path / "first-E3-Setup.exe",
    )
    assert getattr(window, "_e3_update_idle_handoff", None) is not None

    window.accept_prepare = False
    update_ui._request_downloaded_update_handoff(
        window,
        tmp_path / "rejected-E3-Setup.exe",
    )
    controller.active = False
    controller.tasksDrained.emit()
    qt_application.processEvents()

    assert window.events == ["prepare", "prepare"]
    assert handoffs == []
    assert getattr(window, "_e3_update_idle_handoff", None) is None


def test_updater_handoff_precedes_main_window_queued_auto_close(
    qt_application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller = _Controller()
    controller.active = False
    window = _AutoClosePreparedWindow(controller)
    window.show()
    qt_application.processEvents()
    window.events.clear()
    package = tmp_path / "E3-Setup.exe"
    launches: list[Path] = []

    def launch(path: Path) -> None:
        window.events.append("launch")
        launches.append(path)

    monkeypatch.setattr(update_ui, "launch_downloaded_update", launch)

    update_ui._request_downloaded_update_handoff(window, package)
    assert launches == []

    controller.active = False
    controller.tasksDrained.emit()

    assert launches == [package]
    assert window.events[:4] == [
        "prepare",
        "auto-close-suppressed",
        "close",
        "launch",
    ]

    qt_application.processEvents()
    assert launches == [package]


def test_failed_deferred_launch_is_not_closed_by_background_drain_timer(
    qt_application,
    monkeypatch,
    tmp_path: Path,
) -> None:
    controller = _Controller()
    controller.active = False
    window = _AutoClosePreparedWindow(controller)
    window.show()
    qt_application.processEvents()
    window.events.clear()
    errors: list[Exception] = []

    def fail(_path: Path) -> None:
        raise OSError("simulated deferred installer launch failure")

    monkeypatch.setattr(update_ui, "launch_downloaded_update", fail)
    monkeypatch.setattr(
        update_ui,
        "_show_handoff_failure",
        lambda _window, error: errors.append(error),
    )

    update_ui._request_downloaded_update_handoff(
        window,
        tmp_path / "E3-Setup.exe",
    )
    controller.active = False
    controller.tasksDrained.emit()
    qt_application.processEvents()

    assert window.events[:3] == [
        "prepare",
        "auto-close-suppressed",
        "close",
    ]
    assert window.isVisible()
    assert len(errors) == 1
    assert "simulated deferred installer launch failure" in str(errors[0])
