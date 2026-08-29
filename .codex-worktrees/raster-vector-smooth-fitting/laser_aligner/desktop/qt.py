from __future__ import annotations

from typing import Any

PYSIDE6_IMPORT_ERROR: Exception | None = None

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:  # pragma: no cover - exercised on non-desktop installs
    PYSIDE6_IMPORT_ERROR = exc
    QtCore = QtGui = QtWidgets = None  # type: ignore[assignment]


def require_qt() -> tuple[Any, Any, Any]:
    if PYSIDE6_IMPORT_ERROR is not None:
        raise RuntimeError(
            "The native desktop interface requires PySide6. "
            'Install it with: python -m pip install -e ".[desktop]"'
        ) from PYSIDE6_IMPORT_ERROR
    return QtCore, QtGui, QtWidgets
