from __future__ import annotations

import weakref
from typing import Any

from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


class ModalDialogFirstPaintFilter(QtCore.QObject):
    """Schedule one bounded repaint after a modal message box is exposed."""

    repaintScheduled = QtCore.Signal(object)
    repaintCompleted = QtCore.Signal(object)

    def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802
        if (
            event.type() == QtCore.QEvent.Type.Show
            and isinstance(watched, QtWidgets.QMessageBox)
            and watched.isModal()
        ):
            dialog_ref = weakref.ref(watched)
            self.repaintScheduled.emit(watched)
            QtCore.QTimer.singleShot(
                0,
                lambda dialog_ref=dialog_ref: self._repaint_dialog(dialog_ref),
            )
        return False

    def _repaint_dialog(self, dialog_ref: Any) -> None:
        dialog = dialog_ref()
        if dialog is None or not dialog.isVisible():
            return
        dialog.ensurePolished()
        for child in dialog.findChildren(QtWidgets.QWidget):
            child.ensurePolished()
            child.update()
        dialog.updateGeometry()
        dialog.update()
        dialog.repaint()
        self.repaintCompleted.emit(dialog)


def install_modal_dialog_first_paint_fix(application: Any) -> ModalDialogFirstPaintFilter:
    """Install and retain the shared modal-message first-paint workaround."""

    existing = application.property("e3ModalDialogFirstPaintFilter")
    if isinstance(existing, ModalDialogFirstPaintFilter):
        return existing
    event_filter = ModalDialogFirstPaintFilter(application)
    application.installEventFilter(event_filter)
    application.setProperty("e3ModalDialogFirstPaintFilter", event_filter)
    return event_filter


__all__ = ["ModalDialogFirstPaintFilter", "install_modal_dialog_first_paint_fix"]
