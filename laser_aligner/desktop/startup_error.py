from __future__ import annotations

import os
import platform
import sys
import traceback
from pathlib import Path

from ..deployment import user_state_root

_REPORT_LIMIT = 32_768
_FIELD_LIMIT = 8_192
_DIALOG_ERROR_LIMIT = 2_048
_DIALOG_PATH_LIMIT = 1_024
_STARTUP_ERROR_FILENAME = "startup-error.log"


def _bounded_text(value: object, *, limit: int) -> str:
    text = str(value).replace("\x00", "�")
    if len(text) <= limit:
        return text
    marker = "\n...[truncated]"
    return text[: max(0, limit - len(marker))] + marker


def startup_error_log_path() -> Path:
    return user_state_root() / "logs" / _STARTUP_ERROR_FILENAME


def format_startup_error(error: BaseException) -> str:
    exception_type = f"{type(error).__module__}.{type(error).__qualname__}"
    formatted_traceback = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    fields = (
        ("Error type", exception_type),
        ("Error text", error),
        ("Import name", getattr(error, "name", None)),
        ("Import path", getattr(error, "path", None)),
        ("Executable", sys.executable),
        ("Frozen", getattr(sys, "frozen", False)),
        ("Bundle root", getattr(sys, "_MEIPASS", None)),
        ("Python", sys.version),
        ("Platform", platform.platform()),
        ("PATH", os.environ.get("PATH", "")),
        ("QT_PLUGIN_PATH", os.environ.get("QT_PLUGIN_PATH")),
        (
            "QT_QPA_PLATFORM_PLUGIN_PATH",
            os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH"),
        ),
        ("Build version", os.environ.get("E3_POSITIONING_SYSTEM_VERSION")),
        ("Build revision", os.environ.get("E3_POSITIONING_SYSTEM_REVISION")),
    )
    lines = ["E3 packaged desktop startup failure", ""]
    lines.extend(
        f"{label}: {_bounded_text(value, limit=_FIELD_LIMIT)}"
        for label, value in fields
    )
    lines.extend(
        (
            "",
            "Traceback:",
            _bounded_text(formatted_traceback, limit=_FIELD_LIMIT),
            "",
        )
    )
    return _bounded_text("\n".join(lines), limit=_REPORT_LIMIT)


def _write_startup_error_log(report: str) -> Path:
    destination = startup_error_log_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report, encoding="utf-8")
    return destination


def _show_native_error(message: str) -> None:
    if sys.platform != "win32":
        raise OSError("native startup error dialogs require Windows")
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        None,
        message,
        "E3 Positioning System — Startup failed",
        0x00000010 | 0x00010000,
    )


def _fallback_stderr(message: str) -> None:
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return
    try:
        print(message, file=stream)
    except Exception:
        pass


def report_frozen_desktop_import_error(error: BaseException) -> None:
    """Persist and display a bounded packaged desktop import failure."""

    report = format_startup_error(error)
    log_path: Path | None = None
    log_failure: Exception | None = None
    try:
        log_path = _write_startup_error_log(report)
    except Exception as exc:
        log_failure = exc

    error_summary = _bounded_text(
        f"{type(error).__module__}.{type(error).__qualname__}: {error}",
        limit=_DIALOG_ERROR_LIMIT,
    )
    if log_path is not None:
        detail = (
            "Diagnostic details were written to:\n"
            + _bounded_text(log_path, limit=_DIALOG_PATH_LIMIT)
        )
    else:
        detail = "The startup-error log could not be written."
        if log_failure is not None:
            detail += "\n" + _bounded_text(log_failure, limit=_DIALOG_ERROR_LIMIT)
    message = (
        "The packaged E3 desktop runtime failed to load.\n\n"
        f"Underlying error:\n{error_summary}\n\n"
        f"{detail}\n\n"
        "Reinstall or rebuild this E3 package."
    )
    try:
        _show_native_error(message)
    except Exception:
        _fallback_stderr(message)
