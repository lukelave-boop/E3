from __future__ import annotations

from typing import Any

from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _layout_icon(kind: str, size: int = 24) -> QtGui.QIcon:
    pixel_ratio = 2
    pixmap = QtGui.QPixmap(size * pixel_ratio, size * pixel_ratio)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(pixel_ratio)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    stock_pen = QtGui.QPen(QtGui.QColor("#8FA2AE"), 1.4)
    stock_pen.setCosmetic(True)
    object_pen = QtGui.QPen(QtGui.QColor("#39D6C4"), 2.0)
    object_pen.setCosmetic(True)
    guide_pen = QtGui.QPen(QtGui.QColor("#E7B55C"), 1.25)
    guide_pen.setCosmetic(True)
    painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    painter.setPen(stock_pen)
    painter.drawRoundedRect(QtCore.QRectF(3.0, 3.0, 18.0, 18.0), 2.0, 2.0)

    painter.setPen(object_pen)
    if kind == "center_h":
        painter.drawRect(QtCore.QRectF(9.0, 7.0, 6.0, 10.0))
        painter.setPen(guide_pen)
        painter.drawLine(QtCore.QPointF(12.0, 4.0), QtCore.QPointF(12.0, 20.0))
        painter.drawLine(QtCore.QPointF(5.0, 12.0), QtCore.QPointF(9.0, 12.0))
        painter.drawLine(QtCore.QPointF(15.0, 12.0), QtCore.QPointF(19.0, 12.0))
    elif kind == "center_v":
        painter.drawRect(QtCore.QRectF(7.0, 9.0, 10.0, 6.0))
        painter.setPen(guide_pen)
        painter.drawLine(QtCore.QPointF(4.0, 12.0), QtCore.QPointF(20.0, 12.0))
        painter.drawLine(QtCore.QPointF(12.0, 5.0), QtCore.QPointF(12.0, 9.0))
        painter.drawLine(QtCore.QPointF(12.0, 15.0), QtCore.QPointF(12.0, 19.0))
    elif kind == "rotate":
        painter.drawLine(QtCore.QPointF(7.0, 15.0), QtCore.QPointF(17.0, 10.0))
        painter.setPen(guide_pen)
        painter.drawLine(QtCore.QPointF(5.0, 18.0), QtCore.QPointF(19.0, 11.0))
        painter.drawArc(QtCore.QRectF(8.0, 4.0, 10.0, 10.0), 35 * 16, 150 * 16)
        painter.drawLine(QtCore.QPointF(8.0, 7.0), QtCore.QPointF(9.5, 4.5))
        painter.drawLine(QtCore.QPointF(8.0, 7.0), QtCore.QPointF(11.0, 7.5))
    else:
        painter.drawRect(QtCore.QRectF(7.0, 8.0, 10.0, 8.0))
        painter.setPen(guide_pen)
        for start, end in (
            ((12.0, 12.0), (5.0, 5.0)),
            ((12.0, 12.0), (19.0, 5.0)),
            ((12.0, 12.0), (5.0, 19.0)),
            ((12.0, 12.0), (19.0, 19.0)),
        ):
            painter.drawLine(QtCore.QPointF(*start), QtCore.QPointF(*end))
    painter.end()
    return QtGui.QIcon(pixmap)


class StockLayoutToolBar(QtWidgets.QToolBar):
    """Contextual stock-relative layout controls for selected project objects."""

    centerHorizontalRequested = QtCore.Signal()
    centerVerticalRequested = QtCore.Signal()
    centerBothRequested = QtCore.Signal()
    snapRotationRequested = QtCore.Signal(str)
    fitRequested = QtCore.Signal(float)
    customMarginRequested = QtCore.Signal()

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__("Stock layout", parent)
        self.setObjectName("stockLayoutToolbar")
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QtCore.QSize(22, 22))
        self._fit_margin_mm = 3.0

        title = QtWidgets.QLabel("Stock layout")
        title.setObjectName("toolbarSectionLabel")
        title.setToolTip(
            "Position the selected artwork relative to the nearest traced stock boundary."
        )
        self.addWidget(title)
        self.addSeparator()

        self.center_horizontal_button = self._button(
            "Center horizontally in stock",
            "center_h",
            self.centerHorizontalRequested.emit,
        )
        self.addWidget(self.center_horizontal_button)
        self.center_vertical_button = self._button(
            "Center vertically in stock",
            "center_v",
            self.centerVerticalRequested.emit,
        )
        self.addWidget(self.center_vertical_button)

        self.rotate_button = QtWidgets.QToolButton()
        self.rotate_button.setIcon(_layout_icon("rotate"))
        self.rotate_button.setIconSize(QtCore.QSize(22, 22))
        self.rotate_button.setToolTip(
            "Snap the selected object's rotation parallel to the nearest meaningful stock edge."
        )
        self.rotate_button.setAccessibleName("Snap rotation to stock edge")
        self.rotate_button.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup
        )
        self.rotate_button.clicked.connect(
            lambda checked=False: self.snapRotationRequested.emit("nearest")
        )
        rotate_menu = QtWidgets.QMenu(self.rotate_button)
        for label, mode in (
            ("Nearest edge", "nearest"),
            ("Top edge", "top"),
            ("Bottom edge", "bottom"),
            ("Left edge", "left"),
            ("Right edge", "right"),
        ):
            item = rotate_menu.addAction(label)
            item.triggered.connect(
                lambda checked=False, value=mode: self.snapRotationRequested.emit(
                    value
                )
            )
        self.rotate_button.setMenu(rotate_menu)
        self.addWidget(self.rotate_button)

        self.fit_button = QtWidgets.QToolButton()
        self.fit_button.setIcon(_layout_icon("fit"))
        self.fit_button.setIconSize(QtCore.QSize(22, 22))
        self.fit_button.setToolTip(
            "Scale and center the selected object to the largest size that fits inside stock."
        )
        self.fit_button.setAccessibleName("Fit selected object to stock")
        self.fit_button.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup
        )
        self.fit_button.clicked.connect(
            lambda checked=False: self._emit_fit()
        )
        fit_menu = QtWidgets.QMenu(self.fit_button)
        for margin in (0.0, 2.0, 3.0, 5.0, 10.0):
            item = fit_menu.addAction(f"Fit with {margin:g} mm margin")
            item.triggered.connect(
                lambda checked=False, value=margin: self._set_and_emit_fit(value)
            )
        fit_menu.addSeparator()
        custom_margin = fit_menu.addAction("Custom margin…")
        custom_margin.triggered.connect(
            lambda checked=False: self.customMarginRequested.emit()
        )
        self.fit_button.setMenu(fit_menu)
        self.addWidget(self.fit_button)

        self.addSeparator()
        self.more_button = QtWidgets.QToolButton()
        self.more_button.setText("Align")
        self.more_button.setIcon(_layout_icon("fit"))
        self.more_button.setIconSize(QtCore.QSize(20, 20))
        self.more_button.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.more_button.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        )
        self.more_button.setToolTip(
            "More stock-relative layout commands for the current selection."
        )
        more_menu = QtWidgets.QMenu(self.more_button)
        actions: tuple[tuple[str, Any], ...] = (
            ("Center horizontally", self.centerHorizontalRequested.emit),
            ("Center vertically", self.centerVerticalRequested.emit),
            ("Center both", self.centerBothRequested.emit),
            (
                "Rotate parallel to nearest edge",
                lambda: self.snapRotationRequested.emit("nearest"),
            ),
            ("Fit to stock", self._emit_fit),
        )
        for label, callback in actions:
            item = more_menu.addAction(label)
            item.triggered.connect(
                lambda checked=False, function=callback: function()
            )
        self.more_button.setMenu(more_menu)
        self.addWidget(self.more_button)
        self.setVisible(False)

    def _button(
        self,
        tooltip: str,
        icon_kind: str,
        callback: Any,
    ) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        button.setIcon(_layout_icon(icon_kind))
        button.setIconSize(QtCore.QSize(22, 22))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.clicked.connect(
            lambda checked=False: callback()
        )
        return button

    @property
    def fit_margin_mm(self) -> float:
        return self._fit_margin_mm

    def set_fit_margin(self, margin_mm: float) -> None:
        self._fit_margin_mm = max(0.0, float(margin_mm))
        self.fit_button.setToolTip(
            "Scale and center the selected object to the largest size that fits "
            f"inside stock with a {self._fit_margin_mm:g} mm margin."
        )

    def _set_and_emit_fit(self, margin_mm: float) -> None:
        self.set_fit_margin(margin_mm)
        self._emit_fit()

    def _emit_fit(self) -> None:
        self.fitRequested.emit(self._fit_margin_mm)

    def set_context(self, *, has_stock: bool, selection_count: int) -> None:
        active = bool(has_stock and selection_count > 0)
        self.setVisible(active)
        self.setEnabled(active)
