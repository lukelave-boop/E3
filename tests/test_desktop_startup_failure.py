from __future__ import annotations

import sys
from pathlib import Path

from laser_aligner.desktop import main as desktop_main
from laser_aligner.desktop import startup_error


def _qt_import_error() -> ImportError:
    try:
        raise ImportError(
            "DLL load failed while importing QtCore: "
            "The specified procedure could not be found.",
            name="QtCore",
            path=r"C:\bundle\_internal\PySide6\QtCore.pyd",
        )
    except ImportError as exc:
        return exc


def test_source_qt_failure_keeps_developer_install_guidance(
    monkeypatch,
    capsys,
) -> None:
    error = _qt_import_error()
    monkeypatch.setattr(desktop_main, "PYSIDE6_IMPORT_ERROR", error)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        desktop_main,
        "report_frozen_desktop_import_error",
        lambda _error: (_ for _ in ()).throw(AssertionError("unexpected reporter")),
    )

    assert desktop_main.main([]) == 2

    captured = capsys.readouterr()
    assert "PySide6 is not installed" in captured.err
    assert 'python -m pip install -e ".[desktop]"' in captured.err


def test_frozen_qt_failure_reports_the_original_exception(
    monkeypatch,
    capsys,
) -> None:
    error = _qt_import_error()
    reported: list[BaseException] = []
    monkeypatch.setattr(desktop_main, "PYSIDE6_IMPORT_ERROR", error)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        desktop_main,
        "report_frozen_desktop_import_error",
        reported.append,
    )

    assert desktop_main.main([]) == 2
    assert reported == [error]
    assert "pip install" not in capsys.readouterr().err


def test_startup_report_contains_import_and_runtime_context(monkeypatch) -> None:
    error = _qt_import_error()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\bundle\_internal", raising=False)
    monkeypatch.setenv("PATH", r"C:\bundle\_internal;C:\Windows\System32")
    monkeypatch.setenv("QT_PLUGIN_PATH", r"C:\bundle\_internal\PySide6\plugins")
    monkeypatch.setenv("E3_POSITIONING_SYSTEM_VERSION", "0.6.190")
    monkeypatch.setenv("E3_POSITIONING_SYSTEM_REVISION", "1" * 40)

    report = startup_error.format_startup_error(error)

    assert "Error type: builtins.ImportError" in report
    assert "The specified procedure could not be found" in report
    assert "Import name: QtCore" in report
    assert r"Import path: C:\bundle\_internal\PySide6\QtCore.pyd" in report
    assert r"Bundle root: C:\bundle\_internal" in report
    assert r"QT_PLUGIN_PATH: C:\bundle\_internal\PySide6\plugins" in report
    assert "Build version: 0.6.190" in report
    assert "Build revision: " + "1" * 40 in report
    assert "Traceback:" in report


def test_startup_report_is_bounded_and_nul_safe(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "x" * 30_000)
    error = ImportError("bad\x00dependency " + "y" * 100_000)

    report = startup_error.format_startup_error(error)

    assert len(report) <= startup_error._REPORT_LIMIT
    assert "\x00" not in report
    assert "...[truncated]" in report


def test_frozen_report_persists_log_and_shows_native_dialog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(startup_error, "user_state_root", lambda: tmp_path)
    monkeypatch.setattr(startup_error, "_show_native_error", messages.append)

    error = _qt_import_error()
    startup_error.report_frozen_desktop_import_error(error)

    log_path = tmp_path / "logs" / "startup-error.log"
    assert log_path.is_file()
    assert "builtins.ImportError" in log_path.read_text(encoding="utf-8")
    assert len(messages) == 1
    assert "packaged E3 desktop runtime failed to load" in messages[0]
    assert str(log_path) in messages[0]
    assert "pip install" not in messages[0]


def test_frozen_report_still_shows_dialog_when_log_write_fails(monkeypatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(
        startup_error,
        "_write_startup_error_log",
        lambda _report: (_ for _ in ()).throw(OSError("read-only state")),
    )
    monkeypatch.setattr(startup_error, "_show_native_error", messages.append)

    startup_error.report_frozen_desktop_import_error(_qt_import_error())

    assert len(messages) == 1
    assert "startup-error log could not be written" in messages[0]
    assert "read-only state" in messages[0]
