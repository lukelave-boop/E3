from __future__ import annotations

import pytest

from laser_aligner.desktop.qt import PYSIDE6_IMPORT_ERROR

pytestmark = pytest.mark.skipif(PYSIDE6_IMPORT_ERROR is not None, reason="PySide6 unavailable")


def test_scoped_qt_settings_copy_legacy_once_without_writing_registry(tmp_path, monkeypatch):
    from laser_aligner.desktop import preferences
    from laser_aligner.desktop.qt import require_qt

    QtCore, _QtGui, _QtWidgets = require_qt()
    original_settings = QtCore.QSettings
    legacy_values = {"mainWindow/geometry-v7": QtCore.QByteArray(b"window bytes"), "trace/threshold": 127}

    class ReadOnlyLegacy:
        def allKeys(self):
            return list(legacy_values)

        def value(self, key):
            return legacy_values[key]

    def constructor(*arguments):
        if arguments[0] == "E3":
            return ReadOnlyLegacy()
        return original_settings(*arguments)

    constructor.Format = original_settings.Format
    monkeypatch.setattr(QtCore, "QSettings", constructor)
    monkeypatch.setenv("E3_STATE_ISOLATED", "1")
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path / "first"))
    first = preferences.desktop_preferences()
    assert first.value("mainWindow/geometry-v7") == QtCore.QByteArray(b"window bytes")
    first.setValue("trace/threshold", 52)
    first.sync()
    legacy_values["trace/threshold"] = 200
    reopened = preferences.desktop_preferences()
    assert reopened.value("trace/threshold", type=int) == 52

    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path / "second"))
    second = preferences.desktop_preferences()
    assert second.value("trace/threshold", type=int) == 200
    second.setValue("trace/threshold", 30)
    second.sync()
    assert reopened.value("trace/threshold", type=int) == 52
    assert legacy_values["trace/threshold"] == 200
    assert preferences.desktop_preferences("PositioningSystem").fileName().endswith("trace.ini")
