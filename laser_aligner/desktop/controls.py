from __future__ import annotations

from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class PanelScrollArea(QtWidgets.QScrollArea):
    """Opaque, vertically scrolling container for inspector panels."""

    def __init__(
        self,
        panel: QtWidgets.QWidget,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("inspectorScroll")
        self.setProperty("wheelScrollContainer", True)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        panel.setObjectName("inspectorPage")
        panel.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        if panel.layout() is not None:
            panel.layout().setSizeConstraint(
                QtWidgets.QLayout.SizeConstraint.SetMinAndMaxSize
            )
        self.setWidget(panel)

        palette = self.palette()
        background = QtGui.QColor("#1E1E1E")
        palette.setColor(QtGui.QPalette.ColorRole.Window, background)
        palette.setColor(QtGui.QPalette.ColorRole.Base, background)
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self.viewport().setAutoFillBackground(True)
        self.viewport().setPalette(palette)


class InspectorTabs(QtWidgets.QTabWidget):
    """Stable inspector tabs whose wheel never changes the active tab."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inspectorTabs")
        self.setDocumentMode(True)
        self.setTabPosition(QtWidgets.QTabWidget.TabPosition.North)
        self.setMovable(False)
        self.setElideMode(QtCore.Qt.TextElideMode.ElideRight)
        self.setUsesScrollButtons(True)
        self.tabBar().setObjectName("inspectorTabBar")
        self.tabBar().setExpanding(False)
        self.tabBar().installEventFilter(self)
        self._pages: dict[str, PanelScrollArea] = {}

    def add_panel(
        self,
        key: str,
        title: str,
        panel: QtWidgets.QWidget,
        *,
        tooltip: str | None = None,
    ) -> None:
        page = PanelScrollArea(panel, self)
        page.setProperty("panelKey", key)
        self._pages[key] = page
        index = self.addTab(page, title)
        self.setTabToolTip(index, tooltip or title)

    def select_panel(self, key: str) -> None:
        page = self._pages.get(key)
        if page is not None:
            self.setCurrentWidget(page)

    def current_scroll_area(self) -> PanelScrollArea | None:
        widget = self.currentWidget()
        return widget if isinstance(widget, PanelScrollArea) else None

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.tabBar() and event.type() == QtCore.QEvent.Type.Wheel:
            page = self.current_scroll_area()
            if page is not None:
                WheelGuard.scroll_area(page, event)
            event.accept()
            return True
        return super().eventFilter(watched, event)


class WheelGuard(QtCore.QObject):
    """Prevent accidental wheel edits and route the wheel to panel scrolling."""

    _SENSITIVE_TYPES = (
        QtWidgets.QAbstractSpinBox,
        QtWidgets.QComboBox,
        QtWidgets.QSlider,
        QtWidgets.QDial,
        QtWidgets.QTabBar,
    )

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() != QtCore.QEvent.Type.Wheel:
            return False
        if not isinstance(watched, self._SENSITIVE_TYPES):
            return False

        if isinstance(watched, QtWidgets.QTabBar):
            parent = watched.parent()
            while parent is not None and not isinstance(parent, InspectorTabs):
                parent = parent.parent()
            if isinstance(parent, InspectorTabs):
                page = parent.current_scroll_area()
                if page is not None:
                    self.scroll_area(page, event)
            event.accept()
            return True

        scroll = self._scroll_parent(watched)
        if scroll is not None:
            self.scroll_area(scroll, event)
        event.accept()
        return True

    @staticmethod
    def _scroll_parent(widget: QtCore.QObject) -> QtWidgets.QAbstractScrollArea | None:
        parent = widget.parent()
        while parent is not None:
            if (
                isinstance(parent, QtWidgets.QAbstractScrollArea)
                and bool(parent.property("wheelScrollContainer"))
            ):
                return parent
            parent = parent.parent()
        return None

    @staticmethod
    def scroll_area(
        area: QtWidgets.QAbstractScrollArea,
        event: QtCore.QEvent,
    ) -> None:
        if not isinstance(event, QtGui.QWheelEvent):
            return
        bar = area.verticalScrollBar()
        pixel_y = event.pixelDelta().y()
        if pixel_y:
            delta = pixel_y
        else:
            steps = event.angleDelta().y() / 120.0
            delta = int(round(steps * max(24, bar.singleStep() * 3)))
        bar.setValue(bar.value() - int(delta))
