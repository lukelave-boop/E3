from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..project import ObjectKind, ProjectDocument, SceneObject, Transform
from .controls import MeasurementSpinBox
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class ContextPropertyBar(QtWidgets.QWidget):
    """Always-visible, selection-aware properties for the upper tool strip."""

    _COMPACT_BREAKPOINT_PX = 1120
    _RESPONSIVE_MINIMUM_WIDTH_PX = 620
    _NORMAL_SPIN_MINIMUM_PX = 76
    _NORMAL_SPIN_MAXIMUM_PX = 98
    _COMPACT_SPIN_WIDTH_PX = 68
    _MINIMUM_SIZE_MM = 0.001

    transformEdited = QtCore.Signal(str, object)
    rectangleShapeEdited = QtCore.Signal(str, object, float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("contextPropertyBar")
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        self._object_id: str | None = None
        self._rectangle_selected = False
        self._base_width_mm = 0.0
        self._base_height_mm = 0.0
        self._source_transform: Transform | None = None
        self._source_radius_mm = 0.0
        self._display_references: dict[QtWidgets.QDoubleSpinBox, float] = {}
        self._locked_aspect_ratio = 1.0
        self._unit = "mm"
        self._updating = False
        self._responsive_updating = False
        self._compact = False

        layout = QtWidgets.QHBoxLayout(self)
        self._bar_layout = layout
        layout.setContentsMargins(5, 2, 5, 2)
        layout.setSpacing(5)

        self.selection_summary = QtWidgets.QLabel("No selection")
        self.selection_summary.setObjectName("contextSelectionSummary")
        self.selection_summary.setMinimumWidth(88)
        self.selection_summary.setMaximumWidth(170)
        self.selection_summary.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft
            | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.selection_summary.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.selection_summary)

        separator = QtWidgets.QFrame()
        separator.setObjectName("contextPropertySeparator")
        separator.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        separator.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        self.editor = QtWidgets.QWidget()
        self.editor.setObjectName("contextPropertyEditor")
        editor_layout = QtWidgets.QGridLayout(self.editor)
        self._editor_layout = editor_layout
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setHorizontalSpacing(5)
        editor_layout.setVerticalSpacing(1)

        self.x_spin = self._spin(-10000.0, 10000.0, " mm", "Center X")
        self.y_spin = self._spin(-10000.0, 10000.0, " mm", "Center Y")
        self.width_spin = self._spin(0.0, 10000.0, " mm", "Width")
        self.height_spin = self._spin(0.0, 10000.0, " mm", "Height")
        self.scale_x_spin = self._spin(
            0.001,
            100000.0,
            "%",
            "Width scale",
        )
        self.scale_y_spin = self._spin(
            0.001,
            100000.0,
            "%",
            "Height scale",
        )
        self.rotation_spin = self._spin(-360.0, 360.0, "°", "Rotation")
        self.corner_radius_spin = self._spin(
            0.0,
            5000.0,
            " mm",
            "Corner radius",
        )
        self._metric_spins = (
            self.x_spin,
            self.y_spin,
            self.width_spin,
            self.height_spin,
            self.corner_radius_spin,
        )
        self._spins = (
            self.x_spin,
            self.y_spin,
            self.width_spin,
            self.height_spin,
            self.scale_x_spin,
            self.scale_y_spin,
            self.rotation_spin,
            self.corner_radius_spin,
        )
        self.scale_x_spin.setValue(100.0)
        self.scale_y_spin.setValue(100.0)
        self.corner_radius_spin.setToolTip(
            "Corner radius; limited to half of the rectangle's smaller dimension."
        )

        self.x_field, self.x_label = self._field("XPos", self.x_spin, "Center X")
        self.y_field, self.y_label = self._field("YPos", self.y_spin, "Center Y")
        self.width_field, self.width_label = self._field(
            "Width", self.width_spin, "Width"
        )
        self.height_field, self.height_label = self._field(
            "Height", self.height_spin, "Height"
        )
        self.scale_x_field, self.scale_x_label = self._field(
            "Scale X", self.scale_x_spin, "Width scale"
        )
        self.scale_y_field, self.scale_y_label = self._field(
            "Scale Y", self.scale_y_spin, "Height scale"
        )
        self.rotation_field, self.rotation_label = self._field(
            "Rotate", self.rotation_spin, "Rotation"
        )
        self.corner_radius_field, self.corner_radius_label = self._field(
            "Radius",
            self.corner_radius_spin,
            "Corner radius",
        )
        self._field_labels = (
            (self.x_label, "XPos", "X"),
            (self.y_label, "YPos", "Y"),
            (self.width_label, "Width", "W"),
            (self.height_label, "Height", "H"),
            (self.scale_x_label, "Scale X", "%W"),
            (self.scale_y_label, "Scale Y", "%H"),
            (self.rotation_label, "Rotate", "A°"),
            (self.corner_radius_label, "Radius", "R"),
        )

        editor_layout.addWidget(self.x_field, 0, 0)
        editor_layout.addWidget(self.y_field, 1, 0)
        editor_layout.addWidget(self.width_field, 0, 1)
        editor_layout.addWidget(self.height_field, 1, 1)
        editor_layout.addWidget(self.scale_x_field, 0, 2)
        editor_layout.addWidget(self.scale_y_field, 1, 2)

        self.aspect_lock = QtWidgets.QToolButton()
        self.aspect_lock.setObjectName("contextAspectLock")
        self.aspect_lock.setText("Lock")
        self.aspect_lock.setCheckable(True)
        self.aspect_lock.setAutoRaise(True)
        self.aspect_lock.setAccessibleName("Lock aspect ratio")
        self.aspect_lock.setAccessibleDescription(
            "Keep width and height proportional while resizing."
        )
        self.aspect_lock.setToolTip(
            "Lock width and height proportions. The current ratio is captured "
            "when this is enabled."
        )
        editor_layout.addWidget(
            self.aspect_lock,
            0,
            3,
            2,
            1,
            QtCore.Qt.AlignmentFlag.AlignVCenter,
        )

        editor_layout.addWidget(self.rotation_field, 0, 4)

        self.units_combo = QtWidgets.QComboBox()
        self.units_combo.setObjectName("contextUnitsCombo")
        self.units_combo.addItem("mm", "mm")
        self.units_combo.addItem("in", "in")
        self.units_combo.setAccessibleName("Transform units")
        self.units_combo.setAccessibleDescription(
            "Display and enter position and size values in millimetres or inches."
        )
        self.units_combo.setToolTip(
            "Display units. Project geometry remains stored in millimetres."
        )
        self.units_field, self.units_label = self._field(
            "Units", self.units_combo, "Transform units"
        )
        editor_layout.addWidget(self.units_field, 1, 4)

        editor_layout.addWidget(self.corner_radius_field, 0, 5)

        self.mirror_x = self._mirror_button(
            "Flip H",
            "Flip horizontally",
            "Mirror the selected object horizontally.",
        )
        self.mirror_y = self._mirror_button(
            "Flip V",
            "Flip vertically",
            "Mirror the selected object vertically.",
        )
        editor_layout.addWidget(self.mirror_x, 1, 5)
        editor_layout.addWidget(self.mirror_y, 1, 6)

        layout.addWidget(self.editor)
        layout.addStretch(1)

        for widget in (
            self.x_spin,
            self.y_spin,
            self.rotation_spin,
            self.corner_radius_spin,
        ):
            widget.editingFinished.connect(self._emit_transform)
        self.width_spin.editingFinished.connect(self._width_edit_finished)
        self.height_spin.editingFinished.connect(self._height_edit_finished)
        self.scale_x_spin.editingFinished.connect(self._scale_x_edit_finished)
        self.scale_y_spin.editingFinished.connect(self._scale_y_edit_finished)
        self.width_spin.valueChanged.connect(self._update_corner_radius_limit)
        self.height_spin.valueChanged.connect(self._update_corner_radius_limit)
        self.aspect_lock.toggled.connect(self._aspect_lock_toggled)
        self.units_combo.currentIndexChanged.connect(self._units_changed)
        self.mirror_x.toggled.connect(self._emit_transform)
        self.mirror_y.toggled.connect(self._emit_transform)

        self.clear_selection()

    @property
    def compact(self) -> bool:
        return self._compact

    def minimumSizeHint(self) -> QtCore.QSize:
        """Advertise a width which keeps the full two-row editor reachable."""

        hint = super().minimumSizeHint()
        hint.setWidth(min(hint.width(), self._RESPONSIVE_MINIMUM_WIDTH_PX))
        return hint

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        if hasattr(self, "_field_labels"):
            self._set_compact(event.size().width() < self._COMPACT_BREAKPOINT_PX)
        super().resizeEvent(event)

    def changeEvent(self, event: QtCore.QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.FontChange and hasattr(
            self,
            "_field_labels",
        ):
            self._set_compact(self._compact, force=True)

    def _set_compact(self, compact: bool, *, force: bool = False) -> None:
        if self._responsive_updating or (self._compact == compact and not force):
            return
        self._responsive_updating = True
        try:
            self._compact = bool(compact)
            if self._compact:
                self._bar_layout.setContentsMargins(3, 1, 3, 1)
                self._bar_layout.setSpacing(3)
                self._editor_layout.setHorizontalSpacing(2)
                self.selection_summary.setMinimumWidth(76)
                self.selection_summary.setMaximumWidth(92)
            else:
                self._bar_layout.setContentsMargins(5, 2, 5, 2)
                self._bar_layout.setSpacing(5)
                self._editor_layout.setHorizontalSpacing(5)
                self.selection_summary.setMinimumWidth(88)
                self.selection_summary.setMaximumWidth(170)

            for label, normal_text, compact_text in self._field_labels:
                label.setText(compact_text if self._compact else normal_text)
            self.units_label.setText("U" if self._compact else "Units")
            for spin in self._spins:
                normal_suffix = str(spin.property("contextNormalSuffix") or "")
                spin.setSuffix("" if self._compact else normal_suffix)
                if self._compact:
                    spin.setFixedWidth(self._COMPACT_SPIN_WIDTH_PX)
                else:
                    spin.setMinimumWidth(self._NORMAL_SPIN_MINIMUM_PX)
                    spin.setMaximumWidth(self._NORMAL_SPIN_MAXIMUM_PX)

            self.aspect_lock.setText("L" if self._compact else "Lock")
            self.mirror_x.setText("H" if self._compact else "Flip H")
            self.mirror_y.setText("V" if self._compact else "Flip V")
            compact_button_width = max(
                28,
                self.fontMetrics().horizontalAdvance("W") + 14,
            )
            for button in (self.aspect_lock, self.mirror_x, self.mirror_y):
                if self._compact:
                    button.setFixedWidth(compact_button_width)
                else:
                    button.setMinimumWidth(0)
                    button.setMaximumWidth(16777215)
            if self._compact:
                self.units_combo.setFixedWidth(50)
            else:
                self.units_combo.setMinimumWidth(52)
                self.units_combo.setMaximumWidth(68)
            self.updateGeometry()
        finally:
            self._responsive_updating = False

    @staticmethod
    def _spin(
        minimum: float,
        maximum: float,
        suffix: str,
        accessible_name: str,
    ) -> QtWidgets.QDoubleSpinBox:
        spin = (
            MeasurementSpinBox(storage="display")
            if suffix == " mm"
            else QtWidgets.QDoubleSpinBox()
        )
        spin.setObjectName("context" + accessible_name.replace(" ", "") + "Spin")
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        spin.setSuffix(suffix)
        spin.setProperty("contextNormalSuffix", suffix)
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(ContextPropertyBar._NORMAL_SPIN_MINIMUM_PX)
        spin.setMaximumWidth(ContextPropertyBar._NORMAL_SPIN_MAXIMUM_PX)
        spin.setAccessibleName(accessible_name)
        if suffix == "%":
            unit_name = "percent"
        elif suffix == "°":
            unit_name = "degrees"
        else:
            unit_name = "millimetres"
        spin.setAccessibleDescription(f"{accessible_name}, in {unit_name}")
        spin.setToolTip(f"{accessible_name} of the selected object, in {unit_name}")
        return spin

    @staticmethod
    def _field(
        label_text: str,
        control: QtWidgets.QWidget,
        description: str,
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QLabel]:
        field = QtWidgets.QWidget()
        field.setObjectName("context" + description.replace(" ", "") + "Field")
        layout = QtWidgets.QHBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label = QtWidgets.QLabel(label_text)
        label.setObjectName("contextPropertyLabel")
        label.setToolTip(description)
        label.setBuddy(control)
        layout.addWidget(label)
        layout.addWidget(control)
        return field, label

    @staticmethod
    def _mirror_button(
        text: str,
        accessible_name: str,
        tooltip: str,
    ) -> QtWidgets.QToolButton:
        button = QtWidgets.QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setAccessibleName(accessible_name)
        button.setToolTip(tooltip)
        return button

    def clear_selection(self) -> None:
        self.set_selection([])

    def set_selection(
        self,
        objects: Sequence[SceneObject],
        document: ProjectDocument | None = None,
    ) -> None:
        """Reflect selection state without mutating the supplied document."""

        del document  # Kept for drop-in parity with TransformPanel.set_selection().
        selected = list(objects)
        editable = len(selected) == 1
        self._object_id = selected[0].id if editable else None
        self._rectangle_selected = bool(
            editable and selected[0].kind == ObjectKind.RECTANGLE
        )
        self._updating = True
        try:
            # The strip never disappears. Disabled zero-value controls preserve
            # the spatial model and mirror established laser-editor behavior.
            self.editor.setVisible(True)
            self.editor.setEnabled(editable)
            self.corner_radius_field.setVisible(self._rectangle_selected)
            self.corner_radius_spin.setEnabled(self._rectangle_selected)
            self._set_dimension_minimum(editable)

            if not selected:
                self._set_summary("No selection")
                self._clear_transform_values()
                return
            if len(selected) > 1:
                self._set_summary(f"{len(selected)} selected")
                self._clear_transform_values()
                return

            item = selected[0]
            self._set_summary(item.name)
            transform = item.transform
            self._source_transform = transform.copy()
            self._base_width_mm = transform.width_mm
            self._base_height_mm = transform.height_mm
            self.x_spin.setValue(self._from_mm(transform.x_mm))
            self.y_spin.setValue(self._from_mm(transform.y_mm))
            self.width_spin.setValue(self._from_mm(transform.width_mm))
            self.height_spin.setValue(self._from_mm(transform.height_mm))
            self.scale_x_spin.setValue(100.0)
            self.scale_y_spin.setValue(100.0)
            self._update_corner_radius_limit()
            if self._rectangle_selected:
                radius_mm = float(item.geometry.get("corner_radius_mm", 0.0))
                self._source_radius_mm = radius_mm
                self.corner_radius_spin.setValue(self._from_mm(radius_mm))
            self.rotation_spin.setValue(transform.rotation_deg)
            self.mirror_x.setChecked(transform.mirror_x)
            self.mirror_y.setChecked(transform.mirror_y)
            if self.aspect_lock.isChecked():
                self._capture_aspect_ratio()
            self._remember_display_values()
        finally:
            self._updating = False

    def _clear_transform_values(self) -> None:
        self._source_transform = None
        self._source_radius_mm = 0.0
        self._display_references.clear()
        self._base_width_mm = 0.0
        self._base_height_mm = 0.0
        for spin in (
            self.x_spin,
            self.y_spin,
            self.width_spin,
            self.height_spin,
            self.rotation_spin,
            self.corner_radius_spin,
        ):
            spin.setValue(0.0)
        self.scale_x_spin.setValue(100.0)
        self.scale_y_spin.setValue(100.0)
        self.mirror_x.setChecked(False)
        self.mirror_y.setChecked(False)

    def _set_summary(self, text: str) -> None:
        self.selection_summary.setText(text)
        self.selection_summary.setToolTip(text)

    def _set_dimension_minimum(self, editable: bool) -> None:
        minimum = self._from_mm(self._MINIMUM_SIZE_MM) if editable else 0.0
        self.width_spin.setMinimum(minimum)
        self.height_spin.setMinimum(minimum)

    def _unit_factor(self, unit: str | None = None) -> float:
        return 25.4 if (unit or self._unit) == "in" else 1.0

    def _from_mm(self, value_mm: float, unit: str | None = None) -> float:
        return value_mm / self._unit_factor(unit)

    def _to_mm(self, displayed_value: float, unit: str | None = None) -> float:
        return displayed_value * self._unit_factor(unit)

    def _units_changed(self, *args: Any) -> None:
        del args
        new_unit = str(self.units_combo.currentData() or "mm")
        if new_unit == self._unit:
            return
        old_unit = self._unit
        values_mm = {
            spin: self._to_mm(spin.value(), old_unit) for spin in self._metric_spins
        }
        self._updating = True
        try:
            self._unit = new_unit
            position_limit = self._from_mm(10000.0)
            size_minimum = (
                self._from_mm(self._MINIMUM_SIZE_MM)
                if self._object_id is not None
                else 0.0
            )
            self.x_spin.setRange(-position_limit, position_limit)
            self.y_spin.setRange(-position_limit, position_limit)
            self.width_spin.setRange(size_minimum, position_limit)
            self.height_spin.setRange(size_minimum, position_limit)
            suffix = " in" if new_unit == "in" else " mm"
            decimals = 4 if new_unit == "in" else 3
            step = 0.1 if new_unit == "in" else 1.0
            for spin in self._metric_spins:
                if isinstance(spin, MeasurementSpinBox):
                    spin.setDisplayUnit(new_unit)
                spin.setDecimals(decimals)
                spin.setSingleStep(step)
                spin.setProperty("contextNormalSuffix", suffix)
                spin.setSuffix("" if self._compact else suffix)
            for spin, value_mm in values_mm.items():
                spin.setValue(self._from_mm(value_mm))
            self._update_corner_radius_limit()
            self._remember_display_values()
        finally:
            self._updating = False

    def _remember_display_values(self) -> None:
        self._display_references = {spin: spin.value() for spin in self._spins}

    def _metric_value_or_source(
        self,
        spin: QtWidgets.QDoubleSpinBox,
        source_value_mm: float,
    ) -> float:
        """Preserve exact model values when a rounded display was not edited."""

        if spin.value() == self._display_references.get(spin):
            return source_value_mm
        return self._to_mm(spin.value())

    def _plain_value_or_source(
        self,
        spin: QtWidgets.QDoubleSpinBox,
        source_value: float,
    ) -> float:
        if spin.value() == self._display_references.get(spin):
            return source_value
        return spin.value()

    def _update_corner_radius_limit(self, *args: Any) -> None:
        del args
        maximum = min(self.width_spin.value(), self.height_spin.value()) / 2.0
        self.corner_radius_spin.setMaximum(maximum)

    def _aspect_lock_toggled(self, checked: bool) -> None:
        if checked and self._object_id is not None:
            self._capture_aspect_ratio()

    def _capture_aspect_ratio(self) -> None:
        height = self.height_spin.value()
        if height > 0.0:
            self._locked_aspect_ratio = self.width_spin.value() / height

    def _width_edit_finished(self) -> None:
        if self._updating or self._object_id is None:
            return
        self._updating = True
        try:
            if self.aspect_lock.isChecked() and self._locked_aspect_ratio > 0.0:
                self.height_spin.setValue(
                    self.width_spin.value() / self._locked_aspect_ratio
                )
            self._update_corner_radius_limit()
            self._sync_scales_from_dimensions()
        finally:
            self._updating = False
        self._emit_transform()

    def _height_edit_finished(self) -> None:
        if self._updating or self._object_id is None:
            return
        self._updating = True
        try:
            if self.aspect_lock.isChecked() and self._locked_aspect_ratio > 0.0:
                self.width_spin.setValue(
                    self.height_spin.value() * self._locked_aspect_ratio
                )
            self._update_corner_radius_limit()
            self._sync_scales_from_dimensions()
        finally:
            self._updating = False
        self._emit_transform()

    def _scale_x_edit_finished(self) -> None:
        self._apply_scale(self.scale_x_spin.value(), horizontal=True)

    def _scale_y_edit_finished(self) -> None:
        self._apply_scale(self.scale_y_spin.value(), horizontal=False)

    def _apply_scale(self, percentage: float, *, horizontal: bool) -> None:
        if self._updating or self._object_id is None:
            return
        factor = percentage / 100.0
        self._updating = True
        try:
            if horizontal:
                width_mm = self._base_width_mm * factor
                self.width_spin.setValue(self._from_mm(width_mm))
                if self.aspect_lock.isChecked() and self._locked_aspect_ratio > 0.0:
                    height_mm = width_mm / self._locked_aspect_ratio
                    self.height_spin.setValue(self._from_mm(height_mm))
            else:
                height_mm = self._base_height_mm * factor
                self.height_spin.setValue(self._from_mm(height_mm))
                if self.aspect_lock.isChecked() and self._locked_aspect_ratio > 0.0:
                    width_mm = height_mm * self._locked_aspect_ratio
                    self.width_spin.setValue(self._from_mm(width_mm))
            self._update_corner_radius_limit()
            self._sync_scales_from_dimensions()
        finally:
            self._updating = False
        self._emit_transform()

    def _sync_scales_from_dimensions(self) -> None:
        width_mm = self._to_mm(self.width_spin.value())
        height_mm = self._to_mm(self.height_spin.value())
        if self._base_width_mm > 0.0:
            self.scale_x_spin.setValue(width_mm / self._base_width_mm * 100.0)
        if self._base_height_mm > 0.0:
            self.scale_y_spin.setValue(height_mm / self._base_height_mm * 100.0)

    def _emit_transform(self, *args: Any) -> None:
        del args
        if self._updating or self._object_id is None:
            return
        source = self._source_transform
        if source is None:
            return
        transform = Transform(
            x_mm=self._metric_value_or_source(self.x_spin, source.x_mm),
            y_mm=self._metric_value_or_source(self.y_spin, source.y_mm),
            width_mm=self._metric_value_or_source(
                self.width_spin,
                source.width_mm,
            ),
            height_mm=self._metric_value_or_source(
                self.height_spin,
                source.height_mm,
            ),
            rotation_deg=self._plain_value_or_source(
                self.rotation_spin,
                source.rotation_deg,
            ),
            mirror_x=self.mirror_x.isChecked(),
            mirror_y=self.mirror_y.isChecked(),
        )
        if self._rectangle_selected:
            radius_mm = self._metric_value_or_source(
                self.corner_radius_spin,
                self._source_radius_mm,
            )
            radius = min(
                radius_mm,
                min(transform.width_mm, transform.height_mm) / 2.0,
            )
            self.rectangleShapeEdited.emit(self._object_id, transform, radius)
        else:
            self.transformEdited.emit(self._object_id, transform)


__all__ = ["ContextPropertyBar"]
