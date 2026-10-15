"""Stable scrolling while action controls become unavailable."""

from shiboken6 import isValid

from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class _KeyboardNavigation(QtCore.QObject):
    active = False

    def eventFilter(self, watched, event):
        if event.type() in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease):
            if event.key() in (QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Backtab):
                self.active = event.type() == QtCore.QEvent.Type.KeyPress
        if isinstance(watched, QtWidgets.QWidget):
            click = event.type() == QtCore.QEvent.Type.MouseButtonPress
            activate = (event.type() == QtCore.QEvent.Type.KeyPress
                        and event.key() in (QtCore.Qt.Key.Key_Space, QtCore.Qt.Key.Key_Return))
            navigate = (event.type() == QtCore.QEvent.Type.Wheel
                        or (event.type() == QtCore.QEvent.Type.KeyPress and self.active)
                        or (click and isinstance(watched, QtWidgets.QScrollBar)))
            if click or activate or navigate:
                parent = watched.parentWidget()
                while parent is not None:
                    if isinstance(parent, StableScrollArea):
                        if navigate:
                            parent._clear_anchor()
                        elif isinstance(watched, QtWidgets.QAbstractButton):
                            parent._anchor_button(watched)
                        elif click:
                            parent._clear_anchor()
                    parent = parent.parentWidget()
        return False


class StableScrollArea(QtWidgets.QScrollArea):
    """Keep Qt's disabled-control focus fallback from scrolling the form.

    Qt calls focusNextPrevChild when disabling a focused control, then reveals
    the replacement even if it is far away. Only an actual Tab/Backtab event
    should cause that reveal. A clicked button also anchors asynchronous layout
    changes: wrapping status labels must not move it away from the pointer.
    Explicit navigation and user scrolling release the anchor.
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
        self._anchor = None
        self._anchor_parents = []
        self._restoring = False
        self._anchor_timer = QtCore.QTimer(self)
        self._anchor_timer.setSingleShot(True)
        self._anchor_timer.timeout.connect(self._restore_anchor)
        for bar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            bar.actionTriggered.connect(self._clear_anchor)
            bar.sliderPressed.connect(self._clear_anchor)

    def _clear_anchor(self):
        self._anchor_timer.stop()
        for widget in self._anchor_parents:
            if isValid(widget):
                widget.removeEventFilter(self)
        self._anchor_parents = []
        self._anchor = None

    def _anchor_button(self, button):
        self._clear_anchor()
        self._anchor = button
        self._anchor_position = button.mapTo(self.viewport(), QtCore.QPoint())
        parent = button
        while parent is not None and parent is not self.viewport():
            parent.installEventFilter(self)
            self._anchor_parents.append(parent)
            parent = parent.parentWidget()

    def eventFilter(self, watched, event):
        if (not self._restoring and self._anchor is not None
                and event.type() in (QtCore.QEvent.Type.LayoutRequest,
                                     QtCore.QEvent.Type.Move, QtCore.QEvent.Type.Resize)):
            self._anchor_timer.start(0)
        return super().eventFilter(watched, event)

    def scrollContentsBy(self, dx, dy):
        if not self._restoring and not self._anchor_timer.isActive():
            self._clear_anchor()
        super().scrollContentsBy(dx, dy)

    def _restore_anchor(self):
        button = self._anchor
        if button is None or not isValid(button) or not button.isVisibleTo(self):
            self._clear_anchor()
            return
        delta = button.mapTo(self.viewport(), QtCore.QPoint()) - self._anchor_position
        self._restoring = True
        try:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        finally:
            self._restoring = False

    def ensureWidgetVisible(self, child, xMargin=50, yMargin=50):
        self._clear_anchor()
        super().ensureWidgetVisible(child, xMargin, yMargin)

    def ensureVisible(self, x, y, xMargin=50, yMargin=50):
        self._clear_anchor()
        super().ensureVisible(x, y, xMargin, yMargin)

    def focusNextPrevChild(self, next):
        bars = (self.horizontalScrollBar(), self.verticalScrollBar())
        positions = [bar.value() for bar in bars]
        keyboard = self._keyboard_navigation.active
        self._restoring = True
        try:
            result = super().focusNextPrevChild(next)
            if not keyboard:
                for bar, position in zip(bars, positions, strict=True):
                    bar.setValue(position)
        finally:
            self._restoring = False
        return result
