from __future__ import annotations

from typing import Any

from ..storage import default_user_data_dir, state_is_isolated
from .qt import require_qt


def desktop_preferences(application: str = "E3 Positioning System") -> Any:
    """Keep each launched build's preferences away from older Qt registry keys."""

    QtCore, _QtGui, _QtWidgets = require_qt()
    if not state_is_isolated():
        # Explicit embedding and isolated widget tests retain their caller's
        # settings contract; ordinary desktop launches establish a state scope.
        return QtCore.QSettings("E3", application)
    filename = "trace.ini" if application == "PositioningSystem" else "window.ini"
    path = default_user_data_dir() / "preferences" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = QtCore.QSettings(str(path), QtCore.QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    if not settings.contains("stateScope/imported"):
        # Read the legacy registry once; never remove or write its values.
        legacy = QtCore.QSettings("E3", application)
        for key in legacy.allKeys():
            settings.setValue(key, legacy.value(key))
        settings.setValue("stateScope/imported", True)
        settings.sync()
    return settings
