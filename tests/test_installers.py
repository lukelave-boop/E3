from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_base_installer_checks_dependencies_and_bytecode_before_tests() -> None:
    source = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "pip install -r requirements-dev.txt" in source
    assert "python -m pip check" in source
    assert "python -m compileall -q laser_aligner" in source
    assert source.index("python -m pip check") < source.index("python -m pytest -q")


def test_desktop_installer_smoke_tests_qt_before_creating_launchers() -> None:
    source = (ROOT / "install-desktop.sh").read_text(encoding="utf-8")
    assert "libegl1 libgl1" in source
    assert "python -m pip check" in source
    assert "QT_QPA_PLATFORM=offscreen" in source
    assert source.index("from PySide6 import QtCore, QtGui, QtWidgets") < source.index("APPLICATIONS=")
