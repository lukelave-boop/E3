from __future__ import annotations

import math
import re
from typing import Literal

from ..units import DisplayUnit, MeasurementKind, from_mm, parse_to_mm
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


_NUMERIC_DRAFT = re.compile(
    r"^[-+]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d*)?|\.)?$"
)
_UNIT_DRAFT = re.compile(
    r'^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*[a-zA-Z²/^"]*$'
)


class _NumericEditMixin:
    """Keep drafts editable; only committed, valid values reach the model."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending_edit = False
        self.setKeyboardTracking(False)
        self.setCorrectionMode(QtWidgets.QAbstractSpinBox.CorrectionMode.CorrectToPreviousValue)
        self.lineEdit().textEdited.connect(self._mark_pending_edit)
        self.editingFinished.connect(self._finish_pending_edit)

    def hasPendingEdit(self) -> bool:
        return getattr(self, "_pending_edit", False)

    def _mark_pending_edit(self, _text: str) -> None:
        self._pending_edit = True

    def _finish_pending_edit(self) -> None:
        self._pending_edit = False

    def setValue(self, value) -> None:
        self._pending_edit = False
        super().setValue(value)

    def interpretText(self) -> None:
        super().interpretText()
        self._pending_edit = False

    def stepBy(self, steps: int) -> None:
        if not self.hasAcceptableInput():
            self.interpretText()
        super().stepBy(steps)
        self._pending_edit = False

    def _value_for_validation(self, text: str):
        return self.valueFromText(text)

    def _is_special_value(self, text: str) -> bool:
        return bool(self.specialValueText()) and text.strip() == self.specialValueText().strip()

    def _editable_text(self, text: str) -> str:
        candidate = text.strip()
        prefix, suffix = self.prefix().strip(), self.suffix().strip()
        if prefix and candidate.startswith(prefix):
            candidate = candidate[len(prefix):].strip()
        if suffix and candidate.lower().endswith(suffix.lower()):
            candidate = candidate[:-len(suffix)].strip()
        return candidate

    def _draft_is_editable(self, candidate: str) -> bool:
        group = self.locale().groupSeparator()
        if group:
            candidate = candidate.replace(group, "")
        candidate = candidate.replace(self.locale().decimalPoint(), ".")
        return _NUMERIC_DRAFT.fullmatch(candidate) is not None

    def validate(self, text: str, position: int) -> tuple[QtGui.QValidator.State, str, int]:
        if self._is_special_value(text):
            return QtGui.QValidator.State.Acceptable, text, position
        candidate = self._editable_text(text)
        if candidate in {"", "+", "-", ".", "+.", "-."}:
            return QtGui.QValidator.State.Intermediate, text, position
        try:
            value = self._value_for_validation(text)
        except (ValueError, OverflowError):
            state = (QtGui.QValidator.State.Intermediate if self._draft_is_editable(candidate)
                     else QtGui.QValidator.State.Invalid)
            return state, text, position
        if math.isfinite(value) and self.minimum() <= value <= self.maximum():
            return QtGui.QValidator.State.Acceptable, text, position
        # Rejecting a digit here would silently turn e.g. 40 into 0 when the
        # current range is 20..80. Keep the draft and reject it only on commit.
        return QtGui.QValidator.State.Intermediate, text, position


class NumericDoubleSpinBox(_NumericEditMixin, QtWidgets.QDoubleSpinBox):
    """Floating-point spin box with non-destructive, deferred text editing."""

    def _value_for_validation(self, text: str) -> float:
        return self._parse_numeric_value(text)

    def valueFromText(self, text: str) -> float:
        if self._is_special_value(text):
            return self.minimum()
        value = self._parse_numeric_value(text)
        # Match the field's displayed precision when committing; validation
        # uses the unrounded input so rounding cannot admit an over-limit value.
        return float(f"{value:.{self.decimals()}f}")

    def _parse_numeric_value(self, text: str) -> float:
        value, valid = self.locale().toDouble(self._editable_text(text))
        if not valid or not math.isfinite(value):
            raise ValueError("Enter a finite number")
        return value


class NumericSpinBox(_NumericEditMixin, QtWidgets.QSpinBox):
    """Integer spin box with non-destructive, deferred text editing."""

    def valueFromText(self, text: str) -> int:
        if self._is_special_value(text):
            return self.minimum()
        value, valid = self.locale().toInt(self._editable_text(text))
        if not valid:
            raise ValueError("Enter a whole number")
        return value


class MeasurementSpinBox(NumericDoubleSpinBox):
    """A numeric control that accepts explicit metric or imperial input.

    Most controls keep their underlying value in canonical millimetres.  The
    transform bar is the one exception because it already stores its current
    display value; ``storage="display"`` preserves that established behavior.
    """

    def __init__(
        self,
        kind: MeasurementKind = "length",
        *,
        storage: Literal["mm", "display"] = "mm",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        self._measurement_kind = kind
        self._display_unit: DisplayUnit = "mm"
        self._storage = storage
        super().__init__(parent)

    def setDisplayUnit(self, unit: DisplayUnit) -> None:
        self._display_unit = unit

    def _parse_numeric_value(self, text: str) -> float:
        candidate = self._editable_text(text)
        value_mm = parse_to_mm(candidate, self._display_unit, self._measurement_kind)
        if self._storage == "display":
            return from_mm(value_mm, self._display_unit, self._measurement_kind)
        return value_mm

    def _draft_is_editable(self, candidate: str) -> bool:
        # A unit is typed progressively ("1 i" before "1 in"). Full parsing and
        # range checks still decide whether Return/focus loss can commit it.
        return super()._draft_is_editable(candidate) or _UNIT_DRAFT.fullmatch(candidate) is not None


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
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        panel.setMinimumWidth(0)
        if panel.layout() is not None:
            panel.layout().setSizeConstraint(
                QtWidgets.QLayout.SizeConstraint.SetNoConstraint
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
