from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ..config import WorkArea
from .qt import require_qt


QtCore, QtGui, QtWidgets = require_qt()


TEST_IMAGE_SEED = 1729
_ACTION_TEXT_RESERVE_PX = 8


class _ActionButton(QtWidgets.QPushButton):
    """Footer button with enough reserve for platform font rendering."""

    def __init__(
        self,
        full_text: str,
        compact_text: str | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(full_text, parent)
        self.full_text = full_text
        self.compact_text = compact_text or full_text

    def use_compact_text(self, compact: bool) -> None:
        self.setText(self.compact_text if compact else self.full_text)

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        hint.setWidth(hint.width() + _ACTION_TEXT_RESERVE_PX)
        return hint

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.sizeHint()


def _placement_spin(
    minimum: float,
    maximum: float,
    value: float,
    *,
    suffix: str,
) -> QtWidgets.QDoubleSpinBox:
    spin = QtWidgets.QDoubleSpinBox()
    spin.setRange(float(minimum), float(maximum))
    spin.setDecimals(3)
    spin.setSingleStep(0.1)
    spin.setValue(float(value))
    spin.setSuffix(suffix)
    spin.setKeyboardTracking(False)
    return spin


class TemplateTestImageDialog(QtWidgets.QDialog):
    """Collect a known pose and deterministic image conditions for simulation."""

    def __init__(
        self,
        template_name: str,
        feature_count: int,
        work_area: WorkArea,
        initial_center: tuple[float, float] | None = None,
        initial_rotation_deg: float = 0.0,
        parent: QtWidgets.QWidget | None = None,
        submit_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        name = str(template_name).strip()
        if not name:
            raise ValueError("template_name must not be empty")
        count = int(feature_count)
        if count < 1:
            raise ValueError("feature_count must be at least 1")
        if not all(
            math.isfinite(float(value))
            for value in (
                work_area.x_min,
                work_area.x_max,
                work_area.y_min,
                work_area.y_max,
            )
        ):
            raise ValueError("work area limits must be finite")
        if work_area.x_max <= work_area.x_min or work_area.y_max <= work_area.y_min:
            raise ValueError("work area must have positive width and height")

        self._feature_count = count
        self._submit_handler = submit_handler
        self.setWindowTitle("Generate template test image")
        self.setModal(True)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(360, 390)
        self.resize(500, 450)

        if initial_center is None:
            center_x = (float(work_area.x_min) + float(work_area.x_max)) / 2.0
            center_y = (float(work_area.y_min) + float(work_area.y_max)) / 2.0
        else:
            center_x, center_y = (float(value) for value in initial_center)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        self.content_scroll = QtWidgets.QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content_scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.content_page = QtWidgets.QWidget()
        self.content_page.setMinimumWidth(0)
        self.content_page.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        content = QtWidgets.QVBoxLayout(self.content_page)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(12)
        self.content_scroll.setWidget(self.content_page)
        root.addWidget(self.content_scroll, 1)

        heading = QtWidgets.QLabel("Generate an alignment test image")
        heading.setObjectName("panelHeading")
        content.addWidget(heading)

        intro = QtWidgets.QLabel(
            f"Create a frozen, top-down view of {name}. The normal matcher will "
            "estimate the known placement below; no project objects are changed."
        )
        intro.setObjectName("mutedLabel")
        intro.setWordWrap(True)
        intro.setMinimumWidth(0)
        intro.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        content.addWidget(intro)

        placement_group = QtWidgets.QGroupBox("Known placement")
        placement_form = QtWidgets.QFormLayout(placement_group)
        placement_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        placement_form.setRowWrapPolicy(
            QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows
        )
        self.center_x_spin = _placement_spin(
            work_area.x_min,
            work_area.x_max,
            center_x,
            suffix=" mm",
        )
        self.center_y_spin = _placement_spin(
            work_area.y_min,
            work_area.y_max,
            center_y,
            suffix=" mm",
        )
        self.rotation_spin = _placement_spin(
            -180.0,
            180.0,
            float(initial_rotation_deg),
            suffix="°",
        )
        placement_form.addRow("Center X", self.center_x_spin)
        placement_form.addRow("Center Y", self.center_y_spin)
        placement_form.addRow("Rotation", self.rotation_spin)
        content.addWidget(placement_group)

        conditions_group = QtWidgets.QGroupBox("Image conditions")
        conditions_form = QtWidgets.QFormLayout(conditions_group)
        conditions_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        conditions_form.setRowWrapPolicy(
            QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows
        )
        self.noise_spin = QtWidgets.QDoubleSpinBox()
        self.noise_spin.setRange(0.0, 64.0)
        self.noise_spin.setDecimals(1)
        self.noise_spin.setSingleStep(1.0)
        self.noise_spin.setValue(0.0)
        self.noise_spin.setKeyboardTracking(False)
        self.noise_spin.setToolTip(
            "Pixel-intensity standard deviation. Zero creates a clean image."
        )
        self.missing_spin = QtWidgets.QSpinBox()
        self.missing_spin.setRange(0, self._feature_count)
        self.missing_spin.setValue(0)
        self.missing_spin.setKeyboardTracking(False)
        self.missing_spin.setToolTip(
            "Remove this many labels deterministically to test partial detection."
        )
        conditions_form.addRow("Noise strength (σ)", self.noise_spin)
        conditions_form.addRow("Missing labels", self.missing_spin)
        content.addWidget(conditions_group)

        self.condition_summary = QtWidgets.QLabel()
        self.condition_summary.setWordWrap(True)
        self.condition_summary.setMinimumWidth(0)
        self.condition_summary.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        content.addWidget(self.condition_summary)
        deterministic_note = QtWidgets.QLabel(
            "Generation is deterministic: the same settings produce the same image."
        )
        deterministic_note.setObjectName("mutedLabel")
        deterministic_note.setWordWrap(True)
        deterministic_note.setMinimumWidth(0)
        deterministic_note.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        content.addWidget(deterministic_note)
        content.addStretch(1)

        self.validation_label = QtWidgets.QLabel()
        self.validation_label.setObjectName("warningLabel")
        self.validation_label.setWordWrap(True)
        self.validation_label.setMinimumWidth(0)
        self.validation_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.validation_label.hide()
        root.addWidget(self.validation_label)

        actions = QtWidgets.QHBoxLayout()
        actions.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        actions.setSpacing(6)
        self.cancel_button = _ActionButton("Cancel")
        self.generate_button = _ActionButton("Generate test image", "Generate")
        self.generate_button.setObjectName("primaryActionButton")
        self.generate_button.setDefault(True)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.generate_button)
        root.addLayout(actions)

        self.missing_spin.valueChanged.connect(self._update_condition_summary)
        self.cancel_button.clicked.connect(self.reject)
        self.generate_button.clicked.connect(self._submit)
        self._update_condition_summary()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self._update_action_labels)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._update_action_labels)

    def _update_action_labels(self) -> None:
        available = max(0, self.contentsRect().width() - 28)
        required = (
            self.cancel_button.sizeHint().width()
            + self.generate_button.fontMetrics().horizontalAdvance(
                self.generate_button.full_text
            )
            + 38
            + 6
        )
        self.generate_button.use_compact_text(required > available)

    def parameters(self) -> dict[str, Any]:
        """Return framework-neutral generator inputs for the accepted dialog."""

        return {
            "center_x_mm": self.center_x_spin.value(),
            "center_y_mm": self.center_y_spin.value(),
            "rotation_deg": self.rotation_spin.value(),
            "noise_stddev": self.noise_spin.value(),
            "missing_count": self.missing_spin.value(),
            "seed": TEST_IMAGE_SEED,
        }

    def _submit(self) -> None:
        """Validate and generate before closing so entered values survive errors."""

        self.validation_label.hide()
        self.validation_label.clear()
        self.generate_button.setEnabled(False)
        try:
            if self._submit_handler is not None:
                self._submit_handler(dict(self.parameters()))
        except Exception as exc:
            self.validation_label.setText(
                f"Could not generate test image: {exc}"
            )
            self.validation_label.show()
            self.generate_button.setEnabled(True)
            return
        self.accept()

    def _update_condition_summary(self, *args: Any) -> None:
        del args
        visible = self._feature_count - self.missing_spin.value()
        if visible < 3:
            self.condition_summary.setObjectName("warningLabel")
            self.condition_summary.setText(
                f"{visible}/{self._feature_count} labels remain. Automatic matching "
                "requires at least three visible features."
            )
        else:
            self.condition_summary.setObjectName("mutedLabel")
            self.condition_summary.setText(
                f"{visible}/{self._feature_count} labels will be visible."
            )
        self.style().unpolish(self.condition_summary)
        self.style().polish(self.condition_summary)
