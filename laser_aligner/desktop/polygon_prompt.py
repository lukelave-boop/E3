from __future__ import annotations

from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


class PolygonSidesPrompt(QtWidgets.QFrame):
    """Canvas child, so the main window's Escape/Stop shortcut remains active."""

    sidesChanged = QtCore.Signal(int)
    accepted = QtCore.Signal()
    cancelled = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("polygonSidesPrompt")
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setFixedWidth(285)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Polygon sides (3–12)", self))
        self.sides_edit = QtWidgets.QLineEdit("6", self)
        self.sides_edit.setAccessibleName("Polygon sides")
        self.sides_edit.setMaxLength(20)
        layout.addWidget(self.sides_edit)
        self.message = QtWidgets.QLabel("Size and orientation are locked.", self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QtWidgets.QHBoxLayout()
        self.create_button = QtWidgets.QPushButton("Create", self)
        self.cancel_button = QtWidgets.QPushButton("Cancel", self)
        buttons.addWidget(self.create_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.sides_edit.textChanged.connect(self._changed)
        self.sides_edit.returnPressed.connect(self._accept)
        self.create_button.clicked.connect(self._accept)
        self.cancel_button.clicked.connect(self.cancelled)

    def _changed(self, text: str) -> None:
        text = text.strip()
        if not text.isascii() or not text.isdecimal() or not 3 <= int(text) <= 12:
            self.set_error("Enter a whole number from 3 to 12. More than 12 is not supported.")
            return
        self.set_error("")
        self.sidesChanged.emit(int(text))

    def set_error(self, message: str) -> None:
        self.message.setText(message or "Size and orientation are locked.")
        self.create_button.setEnabled(not message)
        self.adjustSize()

    def _accept(self) -> None:
        if self.create_button.isEnabled():
            self.accepted.emit()

    def focus_sides(self) -> None:
        self.sides_edit.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        self.sides_edit.selectAll()
