from __future__ import annotations

from typing import Any

from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _muted(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("mutedLabel")
    label.setWordWrap(True)
    return label


class TemplatePanel(QtWidgets.QWidget):
    """Template library, matching, and reviewed rigid-placement controls."""

    saveRequested = QtCore.Signal()
    deleteRequested = QtCore.Signal(str)
    refreshRequested = QtCore.Signal()
    templateSelected = QtCore.Signal(str)
    autoMatchRequested = QtCore.Signal()
    matchSelectedRequested = QtCore.Signal(str)
    placementChanged = QtCore.Signal(dict)
    applyRequested = QtCore.Signal(dict)
    clearRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("controlPanel")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self._updating = False
        self._templates: dict[str, dict[str, Any]] = {}
        self._placement_valid = False
        self._calibration_ready = False
        self._busy = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 12)
        layout.setSpacing(10)

        heading = QtWidgets.QLabel("Cutting templates")
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)
        layout.addWidget(
            _muted(
                "Save reusable label-sheet geometry, identify or select a template, "
                "then review its camera alignment before creating project objects."
            )
        )

        self.template_combo = QtWidgets.QComboBox()
        self.template_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.template_combo.setMinimumContentsLength(22)
        layout.addWidget(self.template_combo)

        library_buttons = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("Save project as template…")
        self.refresh_button = QtWidgets.QToolButton()
        self.refresh_button.setText("Refresh")
        self.delete_button = QtWidgets.QToolButton()
        self.delete_button.setText("Delete")
        library_buttons.addWidget(self.save_button, 1)
        library_buttons.addWidget(self.refresh_button)
        library_buttons.addWidget(self.delete_button)
        layout.addLayout(library_buttons)

        self.template_summary = _muted("No cutting templates saved yet.")
        layout.addWidget(self.template_summary)

        match_group = QtWidgets.QGroupBox("Camera match")
        match_layout = QtWidgets.QVBoxLayout(match_group)
        self.auto_button = QtWidgets.QPushButton("Auto identify and align")
        self.match_selected_button = QtWidgets.QPushButton("Align selected template")
        match_layout.addWidget(self.auto_button)
        match_layout.addWidget(self.match_selected_button)
        self.match_status = _muted(
            "Automatic matching compares detected label geometry and grid spacing. "
            "Use manual selection when candidates are ambiguous."
        )
        match_layout.addWidget(self.match_status)
        layout.addWidget(match_group)

        placement_group = QtWidgets.QGroupBox("Reviewed placement")
        placement_layout = QtWidgets.QFormLayout(placement_group)
        placement_layout.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self.x_spin = self._placement_spin(" mm", -1000.0, 1000.0, 0.10)
        self.y_spin = self._placement_spin(" mm", -1000.0, 1000.0, 0.10)
        self.rotation_spin = self._placement_spin("°", -180.0, 180.0, 0.10)
        placement_layout.addRow("Center X", self.x_spin)
        placement_layout.addRow("Center Y", self.y_spin)
        placement_layout.addRow("Rotation", self.rotation_spin)

        nudge_row = QtWidgets.QGridLayout()
        self.nudge_step = self._placement_spin(" mm", 0.01, 10.0, 0.10)
        self.nudge_step.setValue(0.10)
        nudge_row.addWidget(QtWidgets.QLabel("Step (mm / °)"), 0, 0)
        nudge_row.addWidget(self.nudge_step, 0, 1, 1, 2)
        for index, (label, axis, direction) in enumerate(
            (
                ("X−", "x", -1.0),
                ("X+", "x", 1.0),
                ("Y−", "y", -1.0),
                ("Y+", "y", 1.0),
                ("R−", "rotation", -1.0),
                ("R+", "rotation", 1.0),
            )
        ):
            button = QtWidgets.QToolButton()
            button.setText(label)
            button.clicked.connect(
                lambda checked=False, axis=axis, direction=direction: self._nudge(
                    axis, direction
                )
            )
            nudge_row.addWidget(button, 1 + index // 2, index % 2 + 1)
        placement_layout.addRow(nudge_row)
        layout.addWidget(placement_group)

        self.apply_button = QtWidgets.QPushButton("Create aligned cut objects")
        self.apply_button.setEnabled(False)
        self.clear_button = QtWidgets.QPushButton("Clear template preview")
        layout.addWidget(self.apply_button)
        layout.addWidget(self.clear_button)
        layout.addStretch(1)

        self.template_combo.currentIndexChanged.connect(self._template_changed)
        self.save_button.clicked.connect(self.saveRequested.emit)
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        self.delete_button.clicked.connect(self._delete_clicked)
        self.auto_button.clicked.connect(self.autoMatchRequested.emit)
        self.match_selected_button.clicked.connect(self._match_selected)
        self.apply_button.clicked.connect(self._apply_clicked)
        self.clear_button.clicked.connect(self.clearRequested.emit)
        self.x_spin.valueChanged.connect(self._emit_placement)
        self.y_spin.valueChanged.connect(self._emit_placement)
        self.rotation_spin.valueChanged.connect(self._emit_placement)

        self._update_enabled()

    @staticmethod
    def _placement_spin(
        suffix: str,
        minimum: float,
        maximum: float,
        step: float,
    ) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setKeyboardTracking(False)
        return spin

    def current_template_id(self) -> str | None:
        value = self.template_combo.currentData()
        return str(value) if value else None

    def placement(self) -> dict[str, Any]:
        return {
            "template_id": self.current_template_id(),
            "center_x_mm": self.x_spin.value(),
            "center_y_mm": self.y_spin.value(),
            "rotation_deg": self.rotation_spin.value(),
        }

    def set_templates(
        self,
        templates: list[dict[str, Any]],
        selected_id: str | None = None,
    ) -> None:
        previous = selected_id or self.current_template_id()
        self._templates = {str(item["id"]): dict(item) for item in templates}
        self._placement_valid = False
        self._updating = True
        try:
            self.template_combo.clear()
            for item in sorted(templates, key=lambda entry: str(entry["name"]).lower()):
                self.template_combo.addItem(str(item["name"]), str(item["id"]))
            if previous:
                index = self.template_combo.findData(previous)
                if index >= 0:
                    self.template_combo.setCurrentIndex(index)
        finally:
            self._updating = False
        self._show_template_summary()
        self._placement_valid = False
        self._update_enabled()

    def set_rankings(self, rankings: list[dict[str, Any]]) -> None:
        """Order the library by the latest camera ranking and show confidence."""

        if not self._templates:
            return
        current = self.current_template_id()
        scores = {
            str(item.get("template_id", "")): float(item.get("confidence", 0.0) or 0.0)
            for item in rankings
        }
        ordered_ids = [
            str(item.get("template_id", ""))
            for item in rankings
            if str(item.get("template_id", "")) in self._templates
        ]
        ordered_ids.extend(
            template_id
            for template_id in sorted(
                self._templates,
                key=lambda value: str(self._templates[value].get("name", "")).casefold(),
            )
            if template_id not in ordered_ids
        )
        self._updating = True
        try:
            self.template_combo.clear()
            for template_id in ordered_ids:
                summary = self._templates[template_id]
                label = str(summary.get("name", "Unnamed template"))
                if template_id in scores:
                    label += f" — {scores[template_id] * 100:.0f}%"
                self.template_combo.addItem(label, template_id)
            if current:
                index = self.template_combo.findData(current)
                if index >= 0:
                    self.template_combo.setCurrentIndex(index)
        finally:
            self._updating = False
        self._show_template_summary()

    def set_placement(
        self,
        center_x_mm: float,
        center_y_mm: float,
        rotation_deg: float,
        *,
        emit: bool = True,
    ) -> None:
        self._updating = True
        try:
            self.x_spin.setValue(float(center_x_mm))
            self.y_spin.setValue(float(center_y_mm))
            self.rotation_spin.setValue(float(rotation_deg))
        finally:
            self._updating = False
        self._placement_valid = bool(self.current_template_id())
        self._update_enabled()
        if emit:
            self.placementChanged.emit(self.placement())

    def set_match_result(self, payload: dict[str, Any]) -> None:
        template_id = str(payload.get("template_id", ""))
        index = self.template_combo.findData(template_id)
        if index < 0:
            self.clear_placement()
            self.set_match_message(
                "The matched template is no longer in the library. Run alignment again."
            )
            return
        self._updating = True
        try:
            self.template_combo.setCurrentIndex(index)
        finally:
            self._updating = False
        self._show_template_summary()
        self.set_placement(
            float(payload.get("center_x_mm", 0.0)),
            float(payload.get("center_y_mm", 0.0)),
            float(payload.get("rotation_deg", 0.0)),
        )
        confidence = float(payload.get("confidence", payload.get("score", 0.0)))
        residual = float(payload.get("rms_error_mm") or 0.0)
        matched = int(payload.get("matched_count", 0))
        feature_count = int(payload.get("feature_count", matched))
        status = (
            f"{confidence * 100:.0f}% match · {matched}/{feature_count} features · "
            f"{residual:.2f} mm RMS"
        )
        warnings = [str(item) for item in payload.get("warnings", []) if item]
        if warnings:
            status += "\nReview: " + "; ".join(warnings)
        self.match_status.setText(status)

    def set_match_message(self, message: str) -> None:
        self.match_status.setText(str(message))

    def clear_placement(self) -> None:
        self._placement_valid = False
        self._update_enabled()

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._update_enabled()
        if busy:
            self.match_status.setText("Detecting sheet geometry and ranking templates…")

    def set_calibration_ready(self, ready: bool) -> None:
        self._calibration_ready = bool(ready)
        self._update_enabled()
        if not ready:
            self.match_status.setText(
                "Bed mapping is required before camera template matching. "
                "Templates can still be saved and positioned manually."
            )

    def _show_template_summary(self) -> None:
        template_id = self.current_template_id()
        item = self._templates.get(template_id or "")
        if item is None:
            self.template_summary.setText("No cutting templates saved yet.")
            return
        feature_count = int(item.get("feature_count", 0))
        width = float(item.get("width_mm", 0.0))
        height = float(item.get("height_mm", 0.0))
        description = str(item.get("description", "")).strip()
        text = f"{feature_count} cuts · {width:.1f} × {height:.1f} mm"
        if description:
            text += f"\n{description}"
        self.template_summary.setText(text)

    def _update_enabled(self) -> None:
        available = bool(self._templates)
        self.template_combo.setEnabled(available)
        self.delete_button.setEnabled(available and not self._busy)
        can_match = available and self._calibration_ready and not self._busy
        self.auto_button.setEnabled(can_match)
        self.match_selected_button.setEnabled(can_match)
        self.apply_button.setEnabled(
            available and self._placement_valid and not self._busy
        )
        for widget in (self.x_spin, self.y_spin, self.rotation_spin, self.nudge_step):
            widget.setEnabled(available and not self._busy)

    def _template_changed(self, index: int) -> None:
        del index
        if not self._updating:
            self._placement_valid = False
        self._show_template_summary()
        self._update_enabled()
        if self._updating:
            return
        template_id = self.current_template_id()
        if template_id:
            self.templateSelected.emit(template_id)

    def _emit_placement(self, *args: Any) -> None:
        del args
        if not self._updating and self.current_template_id():
            self._placement_valid = True
            self._update_enabled()
            self.placementChanged.emit(self.placement())

    def _nudge(self, axis: str, direction: float) -> None:
        step = self.nudge_step.value() * float(direction)
        if axis == "x":
            self.x_spin.setValue(self.x_spin.value() + step)
        elif axis == "y":
            self.y_spin.setValue(self.y_spin.value() + step)
        else:
            self.rotation_spin.setValue(self.rotation_spin.value() + step)

    def _match_selected(self) -> None:
        template_id = self.current_template_id()
        if template_id:
            self.matchSelectedRequested.emit(template_id)

    def _delete_clicked(self) -> None:
        template_id = self.current_template_id()
        if template_id:
            self.deleteRequested.emit(template_id)

    def _apply_clicked(self) -> None:
        payload = self.placement()
        if payload["template_id"]:
            self.applyRequested.emit(payload)
