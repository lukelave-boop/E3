"""Relative speed presentation; controller and project values remain mm/min."""
from __future__ import annotations

import math
import re

from .controls import NumericDoubleSpinBox
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()

_PERCENT = re.compile(r"^\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*%?\s*$")
_EXPONENT_PREFIX = re.compile(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)[eE][-+]?$")


def _valid_limit(value: float | None) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and float(value) > 0
    )


def format_speed_percent(feed_mm_min: float, maximum_feed_mm_min: float | None) -> str:
    """Format against an explicit configured ceiling, never a guessed maximum."""
    if not _valid_limit(maximum_feed_mm_min) or not math.isfinite(feed_mm_min):
        return "—"
    percent = feed_mm_min * 100.0 / float(maximum_feed_mm_min)
    # A small positive feed must never look like a zero-speed request.
    number = (
        f"{percent:.3g}" if 0 < abs(percent) < 0.01
        else f"{percent:.2f}".rstrip("0").rstrip(".")
    )
    return f"{number}%"


def speed_tooltip(
    feed_mm_min: float,
    maximum_feed_mm_min: float | None,
    *,
    limit_kind: str = "work",
) -> str:
    if not _valid_limit(maximum_feed_mm_min):
        return (
            f"Configured {limit_kind} speed limit unavailable; percentage editing is disabled.\n"
            f"Stored speed: {feed_mm_min:g} mm/min."
        )
    text = (
        f"100% = {maximum_feed_mm_min:g} mm/min, the running machine's configured "
        f"{limit_kind} speed limit.\nStored speed: {feed_mm_min:g} mm/min."
    )
    if feed_mm_min > float(maximum_feed_mm_min):
        text += "\nThis saved speed exceeds the limit; it has not been reduced automatically."
    return text


class PercentageSpeedSpinBox(NumericDoubleSpinBox):
    """Show/edit percent while setValue()/value() retain canonical feed semantics.

    Loading a value and changing its reference retain the exact source float.
    Neither the rounded display nor Qt's numeric storage may rewrite a saved
    speed when the operator edits a different field or merely leaves this one.
    """

    def __init__(
        self,
        maximum_feed_mm_min: float | None = None,
        *,
        limit_kind: str = "work",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        self._maximum_feed_mm_min = maximum_feed_mm_min
        self._limit_kind = limit_kind
        self._canonical_value = 1.0
        self._loading = True
        self._user_edited = False
        super().__init__(parent)
        self.setDecimals(12)
        self.setKeyboardTracking(False)
        self.setCorrectionMode(QtWidgets.QAbstractSpinBox.CorrectionMode.CorrectToPreviousValue)
        self.setSuffix(" %")
        self.valueChanged.connect(self._accept_value)
        self.lineEdit().textEdited.connect(self._text_edited)
        self.editingFinished.connect(self._finish_edit)
        self.set_speed_limit(maximum_feed_mm_min)
        self.setValue(1.0)

    @property
    def speed_limit(self) -> float | None:
        return self._maximum_feed_mm_min

    def set_speed_limit(self, maximum_feed_mm_min: float | None) -> None:
        if (
            getattr(self, "_limit_initialized", False)
            and maximum_feed_mm_min == self._maximum_feed_mm_min
        ):
            return
        self._maximum_feed_mm_min = maximum_feed_mm_min
        self._loading = True
        try:
            limit = float(maximum_feed_mm_min) if _valid_limit(maximum_feed_mm_min) else 100000.0
            self.setRange(min(1.0, self._canonical_value), max(1.0, limit, self._canonical_value))
            self.setSingleStep(limit / 100.0)
            self.setReadOnly(not _valid_limit(maximum_feed_mm_min))
            self._refresh_text()
        finally:
            self._loading = False
            self._limit_initialized = True

    def setValue(self, value: float) -> None:
        feed = float(value)
        if not math.isfinite(feed) or feed <= 0:
            raise ValueError("Saved speed must be finite and positive")
        self._canonical_value = feed
        self._user_edited = False
        self._loading = True
        try:
            if feed > self.maximum():
                self.setMaximum(feed)
            if feed < self.minimum():
                self.setMinimum(feed)
            super().setValue(feed)
            self._refresh_text()
        finally:
            self._loading = False

    def value(self) -> float:
        return self._canonical_value

    def _accept_value(self, value: float) -> None:
        if not self._loading:
            self._canonical_value = value
            self._user_edited = False
            self._refresh_text()

    def _text_edited(self, _text: str) -> None:
        self._user_edited = True

    def _finish_edit(self) -> None:
        self._user_edited = False
        self._refresh_text()

    def _refresh_text(self) -> None:
        self.lineEdit().setText(self.textFromValue(self._canonical_value) + self.suffix())
        self.setToolTip(speed_tooltip(
            self._canonical_value, self._maximum_feed_mm_min, limit_kind=self._limit_kind,
        ))

    def textFromValue(self, value: float) -> str:
        return format_speed_percent(value, self._maximum_feed_mm_min).removesuffix("%")

    def valueFromText(self, text: str) -> float:
        if (
            not self._user_edited
            and _valid_limit(self._maximum_feed_mm_min)
            and text.strip().removesuffix("%").strip() == self.textFromValue(self._canonical_value)
        ):
            return self._canonical_value
        match = _PERCENT.fullmatch(text)
        if match is None or not _valid_limit(self._maximum_feed_mm_min):
            raise ValueError("Enter a percentage of the configured speed limit")
        # A focus/Return cycle on rounded text is not an instruction to alter
        # the exact saved feed, including a pre-existing over-limit feed.
        if (
            not self._user_edited
            and float(match.group(1)) == float(self.textFromValue(self._canonical_value))
        ):
            return self._canonical_value
        percent = float(match.group(1))
        feed = percent * float(self._maximum_feed_mm_min) / 100.0
        if not math.isfinite(percent) or not 0 < percent <= 100 or feed < 1.0:
            raise ValueError("Speed must be above 0% and at most 100% (at least 1 mm/min)")
        return feed

    def validate(self, text: str, position: int) -> tuple[QtGui.QValidator.State, str, int]:
        candidate = text.strip().removesuffix("%").strip()
        if candidate in {"", "+", "-", ".", "+.", "-."}:
            return QtGui.QValidator.State.Intermediate, text, position
        try:
            self.valueFromText(text)
        except ValueError:
            # Keep the entire numeric attempt visible while typing. Rejecting
            # its individual keystrokes would turn 0.05 into 0.5 or 105 into 10.
            # CorrectToPreviousValue rejects an invalid final value on commit.
            if _PERCENT.fullmatch(text) or _EXPONENT_PREFIX.fullmatch(candidate):
                return QtGui.QValidator.State.Intermediate, text, position
            return QtGui.QValidator.State.Invalid, text, position
        return QtGui.QValidator.State.Acceptable, text, position

    def stepBy(self, steps: int) -> None:
        if not _valid_limit(self._maximum_feed_mm_min):
            return
        if self.hasPendingEdit():
            self.interpretText()
        limit = float(self._maximum_feed_mm_min)
        feed = self._canonical_value + steps * limit / 100.0
        self.setValue(min(limit, max(1.0, feed)))

    def sizeHint(self) -> QtCore.QSize:
        size = super().sizeHint()
        size.setWidth(self.fontMetrics().horizontalAdvance("100.00 %") + 42)
        return size

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.sizeHint()
