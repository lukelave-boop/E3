from __future__ import annotations

import os
import tempfile
from pathlib import Path


def pytest_configure(config: object) -> None:
    """Keep desktop preferences deterministic and isolated in every worker."""

    try:
        from PySide6 import QtCore
    except ImportError:
        return
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    settings_root = (
        Path(tempfile.gettempdir())
        / "e3-pytest-qsettings"
        / f"{worker}-{os.getpid()}"
    )
    settings_root.mkdir(parents=True, exist_ok=True)
    option = getattr(config, "option", None)
    if option is not None and getattr(option, "basetemp", None) is None:
        option.basetemp = str(settings_root / "pytest")
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    for scope in (
        QtCore.QSettings.Scope.UserScope,
        QtCore.QSettings.Scope.SystemScope,
    ):
        QtCore.QSettings.setPath(
            QtCore.QSettings.Format.IniFormat,
            scope,
            str(settings_root),
        )

    original_settings = QtCore.QSettings

    class _IsolatedQSettings(original_settings):
        def __init__(self, *args: object, **kwargs: object) -> None:
            if (
                len(args) >= 2
                and isinstance(args[0], str)
                and isinstance(args[1], str)
            ):
                filename = "".join(
                    character if character.isalnum() else "-"
                    for character in f"{args[0]}--{args[1]}"
                )
                super().__init__(
                    str(settings_root / f"{filename}.ini"),
                    original_settings.Format.IniFormat,
                )
                return
            super().__init__(*args, **kwargs)

    QtCore.QSettings = _IsolatedQSettings


def pytest_runtest_setup() -> None:
    """Start every test with empty application preference stores."""

    try:
        from PySide6 import QtCore
    except ImportError:
        return
    for application in ("PositioningSystem", "E3 Positioning System"):
        settings = QtCore.QSettings("E3", application)
        settings.clear()
        settings.sync()
