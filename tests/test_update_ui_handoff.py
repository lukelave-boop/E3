from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

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
