from __future__ import annotations

from .controls import NumericSpinBox
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class _TransparencySpinBox(NumericSpinBox):
    """Reserve the actual themed editor width for a value or mixed selection."""

    def sizeHint(self) -> QtCore.QSize:
        size = super().sizeHint()
        option = QtWidgets.QStyleOptionSpinBox()
        self.initStyleOption(option)
        option.rect = QtCore.QRect(0, 0, size.width(), size.height())
        editor = self.style().subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_SpinBox,
            option, QtWidgets.QStyle.SubControl.SC_SpinBoxEditField, self,
        )
        metrics = self.lineEdit().fontMetrics()
        text_width = max(metrics.horizontalAdvance("100%"), metrics.horizontalAdvance("Mixed"))
        size.setWidth(size.width() - editor.width() + text_width + 6)
        return size

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.sizeHint()


class SelectionTransparencyBar(QtWidgets.QWidget):
    """View-only alignment controls, independent of document editing."""

    transparencyEdited = QtCore.Signal(int)
    resetRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("selectionTransparencyBar")
        self._updating = False
        self._has_selection = False
        self._selection_values: list[int] = []
        self._mixed = False
        self._spin_draft: str | None = None
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)

        label = QtWidgets.QLabel("Transparency")
        layout.addWidget(label)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setPageStep(10)
        self.slider.setMinimumWidth(70)
        self.slider.setMaximumWidth(180)
        self.slider.setAccessibleName("Selected object transparency")
        layout.addWidget(self.slider, 1)

        self.spin = _TransparencySpinBox()
        self.spin.setRange(0, 100)
        self.spin.setSuffix("%")
        self.spin.setKeyboardTracking(False)
        self.spin.setAccessibleName("Selected object transparency percent")
        self.spin.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed
        )
        label.setBuddy(self.spin)
        layout.addWidget(self.spin)

        self.summary = QtWidgets.QLabel("No selection")
        self.summary.setMinimumWidth(84)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.reset_button = QtWidgets.QPushButton("Reset all")
        self.reset_button.setAccessibleName("Reset all object transparency")
        self.reset_button.setToolTip("Show all project objects at 0% transparency.")
        layout.addWidget(self.reset_button)

        tooltip = (
            "Fade selected objects to align them with the bed and other objects. "
            "0% is fully visible; 100% hides the artwork. Selection outlines remain visible. "
            "View only: engraving output is unchanged. Resets when another project is opened."
        )
        self.setToolTip(tooltip)
        self.slider.setToolTip(tooltip)
        self.spin.setToolTip(tooltip)
        self.slider.valueChanged.connect(self._edited)
        self.slider.actionTriggered.connect(self._slider_action_triggered)
        self.slider.sliderReleased.connect(self._slider_released)
        self.spin.valueChanged.connect(self._edited)
        self.spin.lineEdit().textEdited.connect(self._spin_text_edited)
        self.spin.editingFinished.connect(self._spin_edit_finished)
        self.reset_button.clicked.connect(self.resetRequested)
        self.set_selection([])

    def set_selection(self, values: list[int]) -> None:
        """Refresh selection state without changing any object's appearance."""
        self._updating = True
        try:
            self._selection_values = list(values)
            self._spin_draft = None
            self._has_selection = bool(values)
            self.slider.setEnabled(self._has_selection)
            self.spin.setEnabled(self._has_selection)
            mixed = len(set(values)) > 1
            self._mixed = mixed
            value = values[0] if values and not mixed else 0
            self.slider.setValue(value)
            self.spin.setValue(value)
            self.spin.lineEdit().setPlaceholderText("Mixed" if mixed else "")
            if mixed:
                self.spin.lineEdit().clear()
            count = len(values)
            self.summary.setText(
                "Mixed" if mixed else f"{count} selected" if count else "No selection"
            )
        finally:
            self._updating = False

    def _edited(self, value: int) -> None:
        if self._updating or not self._has_selection:
            return
        self.set_selection([value] * len(self._selection_values))
        self.transparencyEdited.emit(value)

    def _slider_released(self) -> None:
        if self._mixed:
            self._edited(self.slider.value())

    def _slider_action_triggered(self, action: int) -> None:
        # Home can explicitly choose zero even if the mixed-state slider is
        # already there. Qt updates sliderPosition before emitting this signal.
        if self._mixed and action != QtWidgets.QAbstractSlider.SliderAction.SliderNoAction.value:
            self._edited(self.slider.sliderPosition())

    def _spin_text_edited(self, text: str) -> None:
        self._spin_draft = text

    def _spin_edit_finished(self) -> None:
        # In a mixed selection the placeholder's underlying value is zero.
        # Explicitly entering zero must still apply it, while merely focusing
        # or leaving the field must not homogenize the selected objects.
        if not self._mixed:
            return
        if self._spin_draft is not None:
            try:
                value = self.spin.valueFromText(self._spin_draft)
            except (ValueError, OverflowError):
                pass
            else:
                if 0 <= value <= 100:
                    self._edited(value)
                    return
        self.set_selection(self._selection_values)
