"""Stable scrolling while action controls become unavailable."""

from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class _KeyboardNavigation(QtCore.QObject):
    active = False

    def eventFilter(self, watched, event):
        if event.type() in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease):
            if event.key() in (QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Backtab):
                self.active = event.type() == QtCore.QEvent.Type.KeyPress
        return False


class StableScrollArea(QtWidgets.QScrollArea):
    """Keep Qt's disabled-control focus fallback from scrolling the form.

    Qt calls focusNextPrevChild when disabling a focused control, then reveals
    the replacement even if it is far away. Only an actual Tab/Backtab event
    should cause that reveal. Explicit ensureWidgetVisible calls stay intact.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        application = QtWidgets.QApplication.instance()
        guard = getattr(application, "_e3_scroll_keyboard_navigation", None)
        if guard is None:
            guard = _KeyboardNavigation(application)
            application._e3_scroll_keyboard_navigation = guard
            application.installEventFilter(guard)
        self._keyboard_navigation = guard

    def focusNextPrevChild(self, next):
        bars = (self.horizontalScrollBar(), self.verticalScrollBar())
        positions = [bar.value() for bar in bars]
        keyboard = self._keyboard_navigation.active
        result = super().focusNextPrevChild(next)
        if not keyboard:
            for bar, position in zip(bars, positions, strict=True):
                bar.setValue(position)
        return result
