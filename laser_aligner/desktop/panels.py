from __future__ import annotations

import math
from typing import Any

from ..project import (
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    SceneObject,
    Transform,
)
from ..units import parse_to_mm
from .controls import MeasurementSpinBox
from .qt import require_qt
from .theme import DEFAULT_CAMERA_OVERLAY_OPACITY

QtCore, QtGui, QtWidgets = require_qt()

_CORRECTED_OVERLAY_RATES_FPS = (0.5, 1.0, 2.0, 4.0, 5.0, 10.0, 15.0)
_DEFAULT_CORRECTED_OVERLAY_RATE_FPS = 2.0


def _frame_interval_ms(fps: float) -> int:
    return round(1000.0 / fps)


def _form_row(layout: QtWidgets.QFormLayout, label: str, widget: QtWidgets.QWidget) -> None:
    layout.addRow(label, widget)


def _panel_layout(widget: QtWidgets.QWidget) -> QtWidgets.QVBoxLayout:
    widget.setObjectName("controlPanel")
    widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(10, 10, 10, 12)
    layout.setSpacing(10)
    return layout


def _dense_panel_layout(widget: QtWidgets.QWidget) -> QtWidgets.QVBoxLayout:
    """Layout for frequently used dock controls where workspace space matters."""

    widget.setObjectName("controlPanel")
    widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(4, 4, 4, 5)
    layout.setSpacing(5)
    return layout


def _form_layout() -> QtWidgets.QFormLayout:
    form = QtWidgets.QFormLayout()
    form.setFieldGrowthPolicy(
        QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
    )
    form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setLabelAlignment(
        QtCore.Qt.AlignmentFlag.AlignLeft
        | QtCore.Qt.AlignmentFlag.AlignVCenter
    )
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(8)
    return form


def _muted(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setObjectName("mutedLabel")
    label.setWordWrap(True)
    return label


class LayerPanel(QtWidgets.QWidget):
    activeLayerChanged = QtCore.Signal(str)
    layerEdited = QtCore.Signal(str, dict)
    addLayerRequested = QtCore.Signal()
    removeLayerRequested = QtCore.Signal(str)
    moveLayerRequested = QtCore.Signal(str, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: ProjectDocument | None = None
        self._updating = False

        layout = _dense_panel_layout(self)

        self.layer_list = _LayerOperationsTree()
        self.layer_list.setObjectName("operationsLayerTree")
        self.layer_list.setAlternatingRowColors(True)
        self.layer_list.setMinimumHeight(104)
        self.layer_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.layer_list.setRootIsDecorated(False)
        self.layer_list.setUniformRowHeights(True)
        self.layer_list.setIndentation(0)
        self.layer_list.setIconSize(QtCore.QSize(12, 12))
        self.layer_list.setColumnCount(5)
        self.layer_list.setHeaderLabels(
            ["Layer", "Mode", "Spd / Pwr", "Out", "Show"]
        )
        self.layer_list.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        header = self.layer_list.header()
        header.setStretchLastSection(False)
        # The two stretch columns may need to collapse on a 360 px inspector
        # with large system text. The selected-operation editor still exposes
        # every value without relying on the abbreviated table cells.
        header.setMinimumSectionSize(28)
        header.setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        header.setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        header.setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        header.setSectionResizeMode(
            4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        layout.addWidget(self.layer_list, 1)

        editor = QtWidgets.QWidget()
        editor.setObjectName("layerQuickSettings")
        editor_layout = QtWidgets.QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(4)

        identity_row = QtWidgets.QHBoxLayout()
        identity_row.setSpacing(4)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("Operation name")
        self.color_button = QtWidgets.QPushButton("")
        self.color_button.setFixedWidth(30)
        self.color_button.setAccessibleName("Operation color")
        self.color_button.setToolTip(
            "Choose the operation color used in the workspace and bottom palette."
        )
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.mode_combo.setMinimumContentsLength(8)
        self.mode_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        for mode in LayerMode:
            self.mode_combo.addItem(mode.value.title(), mode.value)
        identity_row.addWidget(self.color_button)
        identity_row.addWidget(self.name_edit, 1)
        identity_row.addWidget(self.mode_combo)
        editor_layout.addLayout(identity_row)

        settings_grid = QtWidgets.QGridLayout()
        settings_grid.setContentsMargins(0, 0, 0, 0)
        settings_grid.setHorizontalSpacing(4)
        settings_grid.setVerticalSpacing(2)
        self.speed_spin = MeasurementSpinBox("speed")
        self.speed_spin.setRange(1.0, 100000.0)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setToolTip("Operation speed in millimetres per minute")
        self.speed_spin.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.power_spin = QtWidgets.QDoubleSpinBox()
        self.power_spin.setRange(0.0, 100.0)
        self.power_spin.setDecimals(1)
        self.power_spin.setSuffix(" %")
        self.power_spin.setToolTip("Maximum laser power for this operation")
        self.power_spin.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.passes_spin = QtWidgets.QSpinBox()
        self.passes_spin.setRange(1, 999)
        self.passes_spin.setToolTip("Number of passes")
        self.passes_spin.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        settings_grid.addWidget(QtWidgets.QLabel("Speed"), 0, 0)
        settings_grid.addWidget(QtWidgets.QLabel("Passes"), 0, 1)
        settings_grid.addWidget(QtWidgets.QLabel("Max power"), 0, 2)
        settings_grid.addWidget(self.speed_spin, 1, 0)
        settings_grid.addWidget(self.passes_spin, 1, 1)
        settings_grid.addWidget(self.power_spin, 1, 2)
        settings_grid.setColumnStretch(0, 3)
        settings_grid.setColumnStretch(1, 1)
        settings_grid.setColumnStretch(2, 2)
        editor_layout.addLayout(settings_grid)

        self.scan_row = QtWidgets.QWidget()
        scan_layout = QtWidgets.QGridLayout(self.scan_row)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.setHorizontalSpacing(4)
        scan_layout.setVerticalSpacing(2)
        self.interval_spin = MeasurementSpinBox()
        self.interval_spin.setRange(0.02, 10.0)
        self.interval_spin.setDecimals(3)
        self.interval_spin.setSuffix(" mm")
        self.interval_spin.setToolTip("Distance between fill/raster scan lines")
        self.angle_spin = QtWidgets.QDoubleSpinBox()
        self.angle_spin.setRange(-180.0, 180.0)
        self.angle_spin.setDecimals(1)
        self.angle_spin.setSuffix("°")
        self.angle_spin.setToolTip("Fill/raster scan angle")
        self.overscan_spin = QtWidgets.QDoubleSpinBox()
        self.overscan_spin.setRange(0.0, 100.0)
        self.overscan_spin.setDecimals(1)
        self.overscan_spin.setSuffix(" %")
        self.overscan_spin.setToolTip("Laser-off lead-in/out for raster rows")
        scan_layout.addWidget(QtWidgets.QLabel("Interval"), 0, 0)
        scan_layout.addWidget(QtWidgets.QLabel("Angle"), 0, 1)
        scan_layout.addWidget(QtWidgets.QLabel("Overscan"), 0, 2)
        scan_layout.addWidget(self.interval_spin, 1, 0)
        scan_layout.addWidget(self.angle_spin, 1, 1)
        scan_layout.addWidget(self.overscan_spin, 1, 2)
        scan_layout.setColumnStretch(0, 1)
        scan_layout.setColumnStretch(1, 1)
        scan_layout.setColumnStretch(2, 1)
        editor_layout.addWidget(self.scan_row)

        correction_group = QtWidgets.QGroupBox("Advanced · Power Correction")
        correction_layout = QtWidgets.QGridLayout(correction_group)
        correction_layout.setContentsMargins(6, 8, 6, 6)
        correction_layout.setHorizontalSpacing(4)
        correction_layout.setVerticalSpacing(2)
        self.vector_correction_spin = QtWidgets.QDoubleSpinBox()
        self.vector_correction_spin.setRange(-100.0, 100.0)
        self.vector_correction_spin.setDecimals(1)
        self.vector_correction_spin.setSuffix(" %")
        self.vector_correction_spin.setToolTip(
            "Adjusts commanded laser power near corners and other direction "
            "changes. 0 uses normal GRBL M4 dynamic power only. Negative values "
            "reduce power further; positive values increase it."
        )
        self.raster_correction_spin = QtWidgets.QDoubleSpinBox()
        self.raster_correction_spin.setRange(-100.0, 100.0)
        self.raster_correction_spin.setDecimals(1)
        self.raster_correction_spin.setSuffix(" %")
        self.raster_correction_spin.setToolTip(
            "Adjusts commanded laser power near raster direction reversals. 0 "
            "uses normal GRBL M4 dynamic power only. Negative values reduce power "
            "further; positive values increase it."
        )
        correction_layout.addWidget(QtWidgets.QLabel("Vector"), 0, 0)
        correction_layout.addWidget(self.vector_correction_spin, 0, 1)
        correction_layout.addWidget(QtWidgets.QLabel("Raster"), 1, 0)
        correction_layout.addWidget(self.raster_correction_spin, 1, 1)
        editor_layout.addWidget(correction_group)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(4)
        self.add_button = QtWidgets.QPushButton("+")
        self.add_button.setToolTip("Add operation")
        self.add_button.setAccessibleName("Add operation")
        self.remove_button = QtWidgets.QPushButton("−")
        self.remove_button.setToolTip("Remove selected operation")
        self.remove_button.setAccessibleName("Remove selected operation")
        self.up_button = QtWidgets.QPushButton("↑")
        self.up_button.setToolTip("Move selected operation up")
        self.up_button.setAccessibleName("Move selected operation up")
        self.down_button = QtWidgets.QPushButton("↓")
        self.down_button.setToolTip("Move selected operation down")
        self.down_button.setAccessibleName("Move selected operation down")
        for button in (
            self.add_button,
            self.remove_button,
            self.up_button,
            self.down_button,
        ):
            button.setFixedWidth(30)
            action_row.addWidget(button)
        self.output_check = QtWidgets.QCheckBox("Output")
        self.output_check.setToolTip("Include this operation when generating a job.")
        self.visible_check = QtWidgets.QCheckBox("Show")
        self.visible_check.setToolTip("Show this operation in the workspace.")
        action_row.addStretch(1)
        action_row.addWidget(self.output_check)
        action_row.addWidget(self.visible_check)
        editor_layout.addLayout(action_row)
        self.mode_notice = _muted("")
        self.mode_notice.setObjectName("warningLabel")
        self.mode_notice.hide()
        editor_layout.addWidget(self.mode_notice)
        layout.addWidget(editor)

        self.layer_list.currentItemChanged.connect(self._selection_changed)
        self.layer_list.itemChanged.connect(self._table_item_changed)
        self.add_button.clicked.connect(self.addLayerRequested)
        self.color_button.clicked.connect(self._choose_color)
        self.remove_button.clicked.connect(self._remove_clicked)
        self.up_button.clicked.connect(lambda: self._move_clicked(-1))
        self.down_button.clicked.connect(lambda: self._move_clicked(1))
        self.name_edit.editingFinished.connect(self._emit_edit)
        self.mode_combo.currentIndexChanged.connect(self._emit_edit)
        self.mode_combo.currentIndexChanged.connect(self._sync_scan_controls)
        self.speed_spin.editingFinished.connect(self._emit_edit)
        self.power_spin.editingFinished.connect(self._emit_edit)
        self.passes_spin.editingFinished.connect(self._emit_edit)
        self.interval_spin.editingFinished.connect(self._emit_edit)
        self.angle_spin.editingFinished.connect(self._emit_edit)
        self.overscan_spin.editingFinished.connect(self._emit_edit)
        self.vector_correction_spin.editingFinished.connect(self._emit_edit)
        self.raster_correction_spin.editingFinished.connect(self._emit_edit)
        self.output_check.toggled.connect(self._emit_edit)
        self.visible_check.toggled.connect(self._emit_edit)

    def set_document(self, document: ProjectDocument, active_layer_id: str | None = None) -> None:
        self._document = document
        active_layer_id = active_layer_id or document.active_layer_id
        self._updating = True
        try:
            self.layer_list.clear()
            selected_row = 0
            for row, layer in enumerate(document.layers):
                item = _LayerOperationsItem(
                    [
                        layer.name,
                        layer.mode.value.title(),
                        self._operation_summary(layer),
                        "",
                        "",
                    ]
                )
                item.setData(QtCore.Qt.ItemDataRole.UserRole, layer.id)
                swatch = QtGui.QPixmap(14, 14)
                swatch.fill(QtGui.QColor(layer.color))
                item.setIcon(0, QtGui.QIcon(swatch))
                item.setTextAlignment(
                    1, QtCore.Qt.AlignmentFlag.AlignCenter
                )
                item.setTextAlignment(
                    2,
                    QtCore.Qt.AlignmentFlag.AlignRight
                    | QtCore.Qt.AlignmentFlag.AlignVCenter,
                )
                item.setTextAlignment(
                    3, QtCore.Qt.AlignmentFlag.AlignCenter
                )
                item.setTextAlignment(
                    4, QtCore.Qt.AlignmentFlag.AlignCenter
                )
                item.setFlags(
                    item.flags()
                    | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                    | QtCore.Qt.ItemFlag.ItemIsSelectable
                )
                item.setCheckState(
                    3,
                    QtCore.Qt.CheckState.Checked
                    if layer.output_enabled
                    else QtCore.Qt.CheckState.Unchecked,
                )
                item.setCheckState(
                    4,
                    QtCore.Qt.CheckState.Checked
                    if layer.visible
                    else QtCore.Qt.CheckState.Unchecked,
                )
                details = (
                    f"{layer.speed_mm_min:g} mm/min · {layer.power_percent:g}% power · "
                    f"{layer.passes} pass{'es' if layer.passes != 1 else ''}"
                )
                item.setToolTip(2, details)
                if not layer.visible:
                    muted = QtGui.QBrush(QtGui.QColor("#65727C"))
                    for column in range(self.layer_list.columnCount()):
                        item.setForeground(column, muted)
                self.layer_list.addTopLevelItem(item)
                if layer.id == active_layer_id:
                    selected_row = row
            self.layer_list.setCurrentRow(selected_row)
            if document.layers:
                self._show_layer(document.layers[selected_row])
            self._update_action_states()
        finally:
            self._updating = False

    @staticmethod
    def _operation_summary(layer: OperationLayer) -> str:
        return f"{layer.speed_mm_min:g} / {layer.power_percent:g}%"

    def current_layer_id(self) -> str | None:
        item = self.layer_list.currentItem()
        return None if item is None else str(item.data(QtCore.Qt.ItemDataRole.UserRole))

    def _selection_changed(
        self,
        current: QtWidgets.QTreeWidgetItem | None,
        previous: QtWidgets.QTreeWidgetItem | None,
    ) -> None:
        del previous
        if self._updating or current is None or self._document is None:
            return
        layer_id = str(current.data(QtCore.Qt.ItemDataRole.UserRole))
        self._show_layer(self._document.get_layer(layer_id))
        self._update_action_states()
        self.activeLayerChanged.emit(layer_id)

    def _table_item_changed(
        self,
        item: QtWidgets.QTreeWidgetItem,
        column: int,
    ) -> None:
        if self._updating or column not in {3, 4}:
            return
        layer_id = str(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
        if not layer_id:
            return
        self.layerEdited.emit(
            layer_id,
            {
                "output_enabled": item.checkState(3)
                == QtCore.Qt.CheckState.Checked,
                "visible": item.checkState(4) == QtCore.Qt.CheckState.Checked,
            },
        )

    def _show_layer(self, layer: OperationLayer) -> None:
        self._updating = True
        try:
            self.name_edit.setText(layer.name)
            swatch = QtGui.QColor(layer.color)
            foreground = "#07130F" if swatch.lightnessF() >= 0.58 else "#FFFFFF"
            self.color_button.setStyleSheet(
                "QPushButton {"
                f"background: {layer.color}; color: {foreground};"
                "border: 1px solid #70818B;"
                "}"
            )
            self.color_button.setProperty("layerColor", layer.color)
            self.mode_combo.setCurrentIndex(
                max(0, self.mode_combo.findData(layer.mode.value))
            )
            self.speed_spin.setValue(layer.speed_mm_min)
            self.power_spin.setValue(layer.power_percent)
            self.passes_spin.setValue(layer.passes)
            self.interval_spin.setValue(layer.line_interval_mm)
            self.angle_spin.setValue(layer.scan_angle_deg)
            self.overscan_spin.setValue(layer.overscan_percent)
            self.vector_correction_spin.setValue(layer.vector_power_correction)
            self.raster_correction_spin.setValue(layer.raster_power_correction)
            self.output_check.setChecked(layer.output_enabled)
            self.visible_check.setChecked(layer.visible)
            self.output_check.setToolTip("Include this operation when generating a job.")
            self.mode_notice.hide()
            self._sync_scan_controls()
        finally:
            self._updating = False

    def _choose_color(self) -> None:
        if self._updating:
            return
        layer_id = self.current_layer_id()
        if layer_id is None or self._document is None:
            return
        layer = self._document.get_layer(layer_id)
        selected = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(layer.color),
            self,
            "Choose operation color",
        )
        if not selected.isValid():
            return
        self.layerEdited.emit(layer_id, {"color": selected.name()})

    def _emit_edit(self, *args: Any) -> None:
        del args
        if self._updating:
            return
        layer_id = self.current_layer_id()
        if layer_id is None:
            return
        self.layerEdited.emit(
            layer_id,
            {
                "name": self.name_edit.text(),
                "mode": self.mode_combo.currentData(),
                "speed_mm_min": self.speed_spin.value(),
                "power_percent": self.power_spin.value(),
                "passes": self.passes_spin.value(),
                "line_interval_mm": self.interval_spin.value(),
                "scan_angle_deg": self.angle_spin.value(),
                "overscan_percent": self.overscan_spin.value(),
                "vector_power_correction": self.vector_correction_spin.value(),
                "raster_power_correction": self.raster_correction_spin.value(),
                "output_enabled": self.output_check.isChecked(),
                "visible": self.visible_check.isChecked(),
            },
        )

    def _sync_scan_controls(self, *args: Any) -> None:
        del args
        self.scan_row.setVisible(self.mode_combo.currentData() != LayerMode.LINE.value)
        self.overscan_spin.setEnabled(
            self.mode_combo.currentData() == LayerMode.RASTER.value
        )

    def _remove_clicked(self) -> None:
        layer_id = self.current_layer_id()
        if layer_id is not None:
            self.removeLayerRequested.emit(layer_id)

    def _move_clicked(self, delta: int) -> None:
        layer_id = self.current_layer_id()
        if layer_id is not None:
            self.moveLayerRequested.emit(layer_id, int(delta))

    def _update_action_states(self) -> None:
        count = self.layer_list.count()
        row = self.layer_list.currentRow()
        has_current = row >= 0
        self.remove_button.setEnabled(has_current and count > 1)
        self.up_button.setEnabled(has_current and row > 0)
        self.down_button.setEnabled(has_current and row < count - 1)


class _LayerOperationsItem(QtWidgets.QTreeWidgetItem):
    """Tree item with the common QListWidgetItem text() convenience."""

    def text(self, column: int = 0) -> str:
        return super().text(column)

    def setText(self, *args: Any) -> None:
        if len(args) == 1:
            super().setText(0, args[0])
            return
        super().setText(*args)

    def data(self, *args: Any) -> Any:
        if len(args) == 1:
            return super().data(0, args[0])
        return super().data(*args)

    def setData(self, *args: Any) -> None:
        if len(args) == 2:
            super().setData(0, args[0], args[1])
            return
        super().setData(*args)

    def icon(self, column: int = 0) -> QtGui.QIcon:
        return super().icon(column)

    def setIcon(self, *args: Any) -> None:
        if len(args) == 1:
            super().setIcon(0, args[0])
            return
        super().setIcon(*args)

    def foreground(self, column: int = 0) -> QtGui.QBrush:
        return super().foreground(column)

    def setForeground(self, *args: Any) -> None:
        if len(args) == 1:
            super().setForeground(0, args[0])
            return
        super().setForeground(*args)

    def checkState(self, column: int = 0) -> QtCore.Qt.CheckState:
        return super().checkState(column)

    def setCheckState(self, *args: Any) -> None:
        if len(args) == 1:
            super().setCheckState(0, args[0])
            return
        super().setCheckState(*args)


class _LayerOperationsTree(QtWidgets.QTreeWidget):
    """QTreeWidget with the small QListWidget API used by older integrations."""

    def count(self) -> int:
        return self.topLevelItemCount()

    def item(self, row: int) -> QtWidgets.QTreeWidgetItem | None:
        return self.topLevelItem(row)

    def addItem(self, item: QtWidgets.QTreeWidgetItem) -> None:
        self.addTopLevelItem(item)

    def currentRow(self) -> int:
        current = self.currentItem()
        return -1 if current is None else self.indexOfTopLevelItem(current)

    def setCurrentRow(self, row: int) -> None:
        item = self.topLevelItem(row)
        if item is None:
            self.clearSelection()
            self.setCurrentItem(None)
            return
        self.setCurrentItem(item)


class TransformPanel(QtWidgets.QWidget):
    transformEdited = QtCore.Signal(str, object)
    rectangleShapeEdited = QtCore.Signal(str, object, float)
    assignLayerRequested = QtCore.Signal(list, str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._object_id: str | None = None
        self._selected_ids: list[str] = []
        self._rectangle_selected = False
        self._updating = False

        layout = _panel_layout(self)
        self.summary = QtWidgets.QLabel("No object selected")
        self.summary.setWordWrap(True)
        self.summary.setMinimumWidth(0)
        self.summary.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.summary.setToolTip(self.summary.text())
        layout.addWidget(self.summary)

        form = _form_layout()
        self.x_spin = self._spin(-10000.0, 10000.0, " mm")
        self.y_spin = self._spin(-10000.0, 10000.0, " mm")
        self.width_spin = self._spin(0.001, 10000.0, " mm")
        self.height_spin = self._spin(0.001, 10000.0, " mm")
        self.corner_radius_spin = self._spin(0.0, 5000.0, " mm")
        self.corner_radius_spin.setToolTip(
            "Limited to half of the rectangle's smaller dimension"
        )
        self.rotation_spin = self._spin(-360.0, 360.0, "°")
        self.mirror_x = QtWidgets.QCheckBox("Flip horizontally")
        self.mirror_x.setToolTip("Mirror the selected object horizontally.")
        self.mirror_y = QtWidgets.QCheckBox("Flip vertically")
        self.mirror_y.setToolTip("Mirror the selected object vertically.")
        _form_row(form, "Center X", self.x_spin)
        _form_row(form, "Center Y", self.y_spin)
        _form_row(form, "Width", self.width_spin)
        _form_row(form, "Height", self.height_spin)
        self.corner_radius_label = QtWidgets.QLabel("Corner radius")
        form.addRow(self.corner_radius_label, self.corner_radius_spin)
        self.corner_radius_label.setVisible(False)
        self.corner_radius_spin.setVisible(False)
        self.corner_radius_spin.setEnabled(False)
        _form_row(form, "Rotation", self.rotation_spin)
        form.addRow(self.mirror_x)
        form.addRow(self.mirror_y)
        layout.addLayout(form)

        self.layer_combo = QtWidgets.QComboBox()
        self.layer_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.layer_combo.setMinimumContentsLength(12)
        layout.addWidget(_muted("Assign selected objects to layer"))
        layout.addWidget(self.layer_combo)
        layout.addStretch(1)

        for widget in (
            self.x_spin,
            self.y_spin,
            self.width_spin,
            self.height_spin,
            self.rotation_spin,
            self.corner_radius_spin,
        ):
            widget.editingFinished.connect(self._emit_transform)
        self.width_spin.valueChanged.connect(self._update_corner_radius_limit)
        self.height_spin.valueChanged.connect(self._update_corner_radius_limit)
        self.mirror_x.toggled.connect(self._emit_transform)
        self.mirror_y.toggled.connect(self._emit_transform)
        self.layer_combo.activated.connect(self._assign_layer)

    @staticmethod
    def _spin(minimum: float, maximum: float, suffix: str) -> QtWidgets.QDoubleSpinBox:
        spin = MeasurementSpinBox() if suffix == " mm" else QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(1.0)
        spin.setSuffix(suffix)
        return spin

    def set_document_layers(self, document: ProjectDocument) -> None:
        current = self.layer_combo.currentData()
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for layer in document.layers:
            self.layer_combo.addItem(layer.name, layer.id)
        if current is not None:
            index = self.layer_combo.findData(current)
            if index >= 0:
                self.layer_combo.setCurrentIndex(index)
        self.layer_combo.blockSignals(False)

    def _set_summary(self, text: str) -> None:
        self.summary.setText(text)
        self.summary.setToolTip(text)

    def set_selection(
        self,
        objects: list[SceneObject],
        document: ProjectDocument,
    ) -> None:
        self._selected_ids = [item.id for item in objects]
        self._object_id = objects[0].id if len(objects) == 1 else None
        self._rectangle_selected = bool(
            len(objects) == 1 and objects[0].kind == ObjectKind.RECTANGLE
        )
        self._updating = True
        try:
            enabled = len(objects) == 1
            for widget in (
                self.x_spin,
                self.y_spin,
                self.width_spin,
                self.height_spin,
                self.rotation_spin,
                self.mirror_x,
                self.mirror_y,
            ):
                widget.setEnabled(enabled)
            self.corner_radius_label.setVisible(self._rectangle_selected)
            self.corner_radius_spin.setVisible(self._rectangle_selected)
            self.corner_radius_spin.setEnabled(self._rectangle_selected)
            if not objects:
                self._set_summary("No object selected")
                return
            if len(objects) > 1:
                self._set_summary(f"{len(objects)} objects selected")
                layer_ids = {item.layer_id for item in objects}
                if len(layer_ids) == 1:
                    index = self.layer_combo.findData(next(iter(layer_ids)))
                    if index >= 0:
                        self.layer_combo.setCurrentIndex(index)
                return
            item = objects[0]
            self._set_summary(f"{item.name} · {item.kind.value}")
            transform = item.transform
            self.x_spin.setValue(transform.x_mm)
            self.y_spin.setValue(transform.y_mm)
            self.width_spin.setValue(transform.width_mm)
            self.height_spin.setValue(transform.height_mm)
            self._update_corner_radius_limit()
            if self._rectangle_selected:
                self.corner_radius_spin.setValue(
                    float(item.geometry.get("corner_radius_mm", 0.0))
                )
            self.rotation_spin.setValue(transform.rotation_deg)
            self.mirror_x.setChecked(transform.mirror_x)
            self.mirror_y.setChecked(transform.mirror_y)
            index = self.layer_combo.findData(item.layer_id)
            if index >= 0:
                self.layer_combo.setCurrentIndex(index)
        finally:
            self._updating = False

    def _update_corner_radius_limit(self, *args: Any) -> None:
        del args
        maximum = min(self.width_spin.value(), self.height_spin.value()) / 2.0
        self.corner_radius_spin.setMaximum(maximum)

    def _emit_transform(self, *args: Any) -> None:
        del args
        if self._updating or self._object_id is None:
            return
        transform = Transform(
            x_mm=self.x_spin.value(),
            y_mm=self.y_spin.value(),
            width_mm=self.width_spin.value(),
            height_mm=self.height_spin.value(),
            rotation_deg=self.rotation_spin.value(),
            mirror_x=self.mirror_x.isChecked(),
            mirror_y=self.mirror_y.isChecked(),
        )
        if self._rectangle_selected:
            radius = min(
                self.corner_radius_spin.value(),
                min(transform.width_mm, transform.height_mm) / 2.0,
            )
            self.rectangleShapeEdited.emit(self._object_id, transform, radius)
        else:
            self.transformEdited.emit(self._object_id, transform)

    def _assign_layer(self, index: int) -> None:
        if self._updating or not self._selected_ids:
            return
        layer_id = str(self.layer_combo.itemData(index))
        self.assignLayerRequested.emit(self._selected_ids, layer_id)


class CameraPanel(QtWidgets.QWidget):
    monitorRequested = QtCore.Signal()
    refreshRequested = QtCore.Signal()
    captureRequested = QtCore.Signal()
    lensCalibrationRequested = QtCore.Signal()
    bedCalibrationRequested = QtCore.Signal()
    opacityChanged = QtCore.Signal(float)
    liveChanged = QtCore.Signal(bool)
    refreshIntervalChanged = QtCore.Signal(int)
    focusApplyRequested = QtCore.Signal(bool, int)
    focusSaveRequested = QtCore.Signal(bool, int)
    sharpnessRequested = QtCore.Signal()
    focusSweepRequested = QtCore.Signal(int, int, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._syncing_focus = False
        self._calibration_ready = False
        self._camera_connected = False
        layout = _panel_layout(self)

        heading = QtWidgets.QLabel("Camera and overlay")
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)

        self.state_label = QtWidgets.QLabel("Camera not started")
        self.state_label.setObjectName("statusCard")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        self.monitor_button = QtWidgets.QPushButton("Open Raw Live Monitor…")
        self.monitor_button.setToolTip(
            "Watch the Pi camera without calibration or machine-control authority"
        )
        layout.addWidget(self.monitor_button)

        overlay_group = QtWidgets.QGroupBox("Camera overlay")
        overlay_group.setToolTip("Corrected bed overlay controls")
        overlay_layout = QtWidgets.QVBoxLayout(overlay_group)
        overlay_layout.setSpacing(8)

        self.live_check = QtWidgets.QCheckBox("Live overlay")
        self.live_check.setToolTip(
            "Live corrected overlay: refresh the camera image continuously."
        )
        self.live_check.setChecked(True)
        self.live_rate = QtWidgets.QComboBox()
        for fps in _CORRECTED_OVERLAY_RATES_FPS:
            self.live_rate.addItem(
                f"{fps:g} fps",
                _frame_interval_ms(fps),
            )
        self.live_rate.setCurrentIndex(
            self.live_rate.findData(
                _frame_interval_ms(_DEFAULT_CORRECTED_OVERLAY_RATE_FPS)
            )
        )
        overlay_layout.addWidget(self.live_check)
        live_rate_row = QtWidgets.QHBoxLayout()
        live_rate_label = QtWidgets.QLabel("Rate")
        live_rate_label.setToolTip("Corrected overlay refresh rate")
        live_rate_row.addWidget(live_rate_label)
        live_rate_row.addWidget(self.live_rate, 1)
        overlay_layout.addLayout(live_rate_row)

        self.image_state = QtWidgets.QLabel("Waiting for corrected image")
        self.image_state.setObjectName("mutedLabel")
        self.image_state.setWordWrap(True)
        self.image_state.setMinimumWidth(0)
        self.image_state.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        overlay_layout.addWidget(self.image_state)
        overlay_layout.addWidget(
            _muted(
                "The overlay is geometrically valid only while the machine "
                "is at the saved camera pose."
            )
        )

        opacity_row = QtWidgets.QHBoxLayout()
        opacity_row.addWidget(QtWidgets.QLabel("Opacity"))
        self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(round(DEFAULT_CAMERA_OVERLAY_OPACITY * 100))
        opacity_row.addWidget(self.opacity_slider, 1)
        overlay_layout.addLayout(opacity_row)

        self.refresh_button = QtWidgets.QPushButton("Refresh now")
        self.capture_button = QtWidgets.QPushButton("Save still image")
        overlay_layout.addWidget(self.refresh_button)
        overlay_layout.addWidget(self.capture_button)
        layout.addWidget(overlay_group)

        calibration_group = QtWidgets.QGroupBox("Calibration")
        calibration_layout = QtWidgets.QVBoxLayout(calibration_group)
        calibration_layout.addWidget(
            _muted(
                "Use these guided tools after moving the camera, changing "
                "resolution, or changing focus."
            )
        )
        self.lens_button = QtWidgets.QPushButton("Calibrate lens…")
        self.lens_button.setToolTip("Open the lens calibration workflow.")
        self.bed_button = QtWidgets.QPushButton("Bed alignment…")
        calibration_layout.addWidget(self.lens_button)
        calibration_layout.addWidget(self.bed_button)
        layout.addWidget(calibration_group)

        focus_group = QtWidgets.QGroupBox("Focus")
        focus_layout = QtWidgets.QVBoxLayout(focus_group)
        focus_layout.setSpacing(8)
        self.autofocus_check = QtWidgets.QCheckBox("Autofocus")
        self.autofocus_check.setToolTip(
            "Continuous autofocus lets the camera adjust focus automatically."
        )
        focus_layout.addWidget(self.autofocus_check)

        focus_row = QtWidgets.QHBoxLayout()
        self.focus_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.focus_slider.setRange(0, 250)
        self.focus_slider.setSingleStep(5)
        self.focus_slider.setPageStep(10)
        self.focus_spin = QtWidgets.QSpinBox()
        self.focus_spin.setRange(0, 250)
        self.focus_spin.setSingleStep(5)
        self.focus_spin.setSuffix(" focus")
        focus_row.addWidget(self.focus_slider, 1)
        focus_row.addWidget(self.focus_spin)
        focus_layout.addLayout(focus_row)

        self.apply_focus_button = QtWidgets.QPushButton("Apply")
        self.measure_button = QtWidgets.QPushButton("Measure focus")
        self.measure_button.setToolTip(
            "Measure sharpness at the current focus setting."
        )
        focus_layout.addWidget(self.apply_focus_button)
        focus_layout.addWidget(self.measure_button)

        self.focus_sweep_button = QtWidgets.QPushButton("Test focus range…")
        self.focus_sweep_button.setToolTip(
            "Compare several manual focus values, then restore the current "
            "camera focus without changing saved calibration."
        )
        focus_layout.addWidget(self.focus_sweep_button)

        self.save_focus_button = QtWidgets.QPushButton("Save focus")
        self.save_focus_button.setToolTip(
            "Save as locked startup focus. A matching calibration profile is "
            "selected after restart."
        )
        focus_layout.addWidget(self.save_focus_button)
        self.calibration_profile_label = QtWidgets.QLabel(
            "Calibration profile: waiting for runtime status"
        )
        self.calibration_profile_label.setObjectName("statusCard")
        self.calibration_profile_label.setWordWrap(True)
        focus_layout.addWidget(self.calibration_profile_label)
        self.sharpness_label = QtWidgets.QLabel("Sharpness score: —")
        self.sharpness_label.setObjectName("statusCard")
        self.sharpness_label.setWordWrap(True)
        focus_layout.addWidget(self.sharpness_label)
        self.focus_warning = QtWidgets.QLabel(
            "Focus changed. Verify or redo lens and bed calibration before "
            "precision placement."
        )
        self.focus_warning.setObjectName("warningLabel")
        self.focus_warning.setWordWrap(True)
        self.focus_warning.setVisible(False)
        focus_layout.addWidget(self.focus_warning)
        layout.addWidget(focus_group)
        layout.addStretch(1)

        self.refresh_button.clicked.connect(self.refreshRequested)
        self.monitor_button.clicked.connect(self.monitorRequested)
        self.capture_button.clicked.connect(self.captureRequested)
        self.lens_button.clicked.connect(self.lensCalibrationRequested)
        self.bed_button.clicked.connect(self.bedCalibrationRequested)
        self.opacity_slider.valueChanged.connect(
            lambda value: self.opacityChanged.emit(value / 100.0)
        )
        self.live_check.toggled.connect(self.liveChanged)
        self.live_rate.currentIndexChanged.connect(
            lambda index: self.refreshIntervalChanged.emit(
                int(self.live_rate.itemData(index))
            )
        )
        self.autofocus_check.toggled.connect(self._autofocus_changed)
        self.focus_slider.valueChanged.connect(self._slider_changed)
        self.focus_spin.valueChanged.connect(self._spin_changed)
        self.apply_focus_button.clicked.connect(self._apply_focus)
        self.save_focus_button.clicked.connect(self._save_focus)
        self.measure_button.clicked.connect(self.sharpnessRequested)
        self.focus_sweep_button.clicked.connect(self._request_focus_sweep)

    def live_enabled(self) -> bool:
        return self.live_check.isChecked()

    def refresh_interval_ms(self) -> int:
        return int(self.live_rate.currentData())

    def focus_settings(self) -> tuple[bool, int]:
        return self.autofocus_check.isChecked(), self.focus_spin.value()

    def set_focus_controls(self, controls: dict[str, Any]) -> None:
        automatic = bool(
            controls.get(
                "focus_automatic_continuous",
                controls.get("focus_auto", 0),
            )
        )
        value = max(0, min(250, int(controls.get("focus_absolute", 10))))
        for widget in (self.autofocus_check, self.focus_slider, self.focus_spin):
            widget.blockSignals(True)
        try:
            self.autofocus_check.setChecked(automatic)
            self.focus_slider.setValue(value)
            self.focus_spin.setValue(value)
        finally:
            for widget in (self.autofocus_check, self.focus_slider, self.focus_spin):
                widget.blockSignals(False)
        self._set_manual_focus_enabled(not automatic)

    def set_focus_result(self, payload: dict[str, Any]) -> None:
        if "autofocus" in payload:
            self.set_focus_controls(
                {
                    "focus_automatic_continuous": 1 if payload["autofocus"] else 0,
                    "focus_absolute": payload.get(
                        "focus_value", self.focus_spin.value()
                    ),
                }
            )
        if payload.get("sharpness") is not None:
            self.sharpness_label.setText(
                f"Sharpness score: {float(payload['sharpness']):.1f}\n"
                "Higher is sharper; compare values on the same scene."
            )
        sweep = payload.get("focus_sweep")
        if isinstance(sweep, list) and sweep:
            ranked = sorted(
                sweep,
                key=lambda item: float(item["median_sharpness"]),
                reverse=True,
            )
            lines = [
                f"Best tested focus: {int(ranked[0]['focus'])} "
                f"({float(ranked[0]['median_sharpness']):.1f})",
                "  ".join(
                    f"{int(item['focus'])}: {float(item['median_sharpness']):.1f}"
                    for item in ranked
                ),
                f"Restored focus {int(payload['restored_focus'])}; calibration unchanged.",
            ]
            self.sharpness_label.setText("\n".join(lines))
        if payload.get("changed"):
            self.focus_warning.setVisible(True)
        profile_label = payload.get("calibration_profile_label")
        if profile_label:
            prefix = "Saved profile" if payload.get("profile_restart_required") else "Active profile"
            suffix = "\nRestart the app to activate it." if payload.get("profile_restart_required") else ""
            self.calibration_profile_label.setText(f"{prefix}: {profile_label}{suffix}")
        skipped = payload.get("skipped") or {}
        self.sharpness_label.setToolTip(
            "; ".join(f"{name}: {reason}" for name, reason in skipped.items())
        )

    def set_calibration_profile(self, payload: dict[str, Any] | None) -> None:
        if not payload:
            return
        label = payload.get("active_label")
        profiles = payload.get("profiles") or []
        if label:
            self.calibration_profile_label.setText(
                f"Active profile: {label}\nSaved profiles: {len(profiles)}"
            )

    def set_calibration_ready(self, ready: bool) -> None:
        self._calibration_ready = bool(ready)
        overlay_enabled = self._calibration_ready
        self.live_check.setEnabled(overlay_enabled)
        self.live_rate.setEnabled(overlay_enabled)
        self.refresh_button.setEnabled(overlay_enabled)
        if not ready:
            self.image_state.setText(
                "Bed mapping is required for a corrected overlay"
            )

    def set_image_updated(self) -> None:
        current = QtCore.QTime.currentTime().toString("HH:mm:ss")
        mode = "LIVE" if self.live_check.isChecked() else "STILL"
        self.image_state.setText(f"{mode} overlay updated at {current}")

    def _update_focus_enabled(self) -> None:
        enabled = self._camera_connected
        for widget in (
            self.autofocus_check,
            self.apply_focus_button,
            self.save_focus_button,
            self.measure_button,
            self.focus_sweep_button,
        ):
            widget.setEnabled(enabled)
        self._set_manual_focus_enabled(
            enabled and not self.autofocus_check.isChecked()
        )

    def set_status(self, status: dict[str, Any] | None) -> None:
        if not status:
            self.state_label.setText("Camera unavailable")
            return
        connected = bool(status.get("connected", False))
        self._camera_connected = connected
        if connected:
            self.state_label.setText(
                f"Online · {status.get('width', 0)} × {status.get('height', 0)} · "
                f"{status.get('fps', 0)} fps\n{status.get('device', '')}"
            )
        else:
            self.state_label.setText(
                f"Offline\n{status.get('last_error') or status.get('device', '')}"
            )
        self._update_focus_enabled()

    def _set_manual_focus_enabled(self, enabled: bool) -> None:
        resolved = bool(enabled)
        self.focus_slider.setEnabled(resolved)
        self.focus_spin.setEnabled(resolved)

    def _autofocus_changed(self, enabled: bool) -> None:
        self._set_manual_focus_enabled(self._camera_connected and not enabled)

    def _slider_changed(self, value: int) -> None:
        if self._syncing_focus:
            return
        self._syncing_focus = True
        try:
            self.focus_spin.setValue(value)
        finally:
            self._syncing_focus = False

    def _spin_changed(self, value: int) -> None:
        if self._syncing_focus:
            return
        self._syncing_focus = True
        try:
            self.focus_slider.setValue(value)
        finally:
            self._syncing_focus = False

    def _apply_focus(self) -> None:
        autofocus, value = self.focus_settings()
        self.focusApplyRequested.emit(autofocus, value)

    def _save_focus(self) -> None:
        autofocus, value = self.focus_settings()
        self.focusSaveRequested.emit(autofocus, value)

    def _request_focus_sweep(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Test manual focus range")
        layout = QtWidgets.QVBoxLayout(dialog)
        form = _form_layout()
        current = self.focus_spin.value()
        start = QtWidgets.QSpinBox()
        end = QtWidgets.QSpinBox()
        step = QtWidgets.QSpinBox()
        for widget in (start, end):
            widget.setRange(0, 250)
            widget.setSingleStep(5)
        step.setRange(1, 50)
        step.setSingleStep(1)
        start.setValue(max(0, current - 10))
        end.setValue(min(250, current + 20))
        step.setValue(5)
        form.addRow("From", start)
        form.addRow("Through", end)
        form.addRow("Step", step)
        layout.addLayout(form)
        layout.addWidget(
            _muted(
                "Three fresh sharpness readings are compared at each value. "
                "The current focus is restored afterward; nothing is saved."
            )
        )
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
            | QtWidgets.QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText(
            "Run sweep"
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        first, last = sorted((start.value(), end.value()))
        self.focusSweepRequested.emit(first, last, step.value())




class TracePanel(QtWidgets.QWidget):
    detectRequested = QtCore.Signal(dict)
    pickColorRequested = QtCore.Signal()
    clearRequested = QtCore.Signal()
    createRequested = QtCore.Signal(dict)
    generateRequested = QtCore.Signal()
    selectionChanged = QtCore.Signal(list)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._detections: list[dict[str, Any]] = []
        self._updating = False
        self._result_is_current = False
        self._sampled_bgr: list[int] | None = None
        self._sampled_hue: float | None = None
        self._color_pick_active = False
        self._calibration_ready = False
        self._generate_enabled = True
        self._trace_settings = QtCore.QSettings("E3", "PositioningSystem")
        layout = _panel_layout(self)

        heading = QtWidgets.QLabel("Trace objects")
        heading.setObjectName("panelHeading")
        heading.setToolTip("Detect and trace objects in the corrected camera image.")
        layout.addWidget(heading)
        layout.addWidget(
            _muted(
                "Detect repeated labels or other contrasting objects in the "
                "corrected camera image, review every outline, then create "
                "normal editable vector objects."
            )
        )

        source_group = QtWidgets.QGroupBox("Detection source")
        source_form = _form_layout()
        source_group.setLayout(source_form)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("Auto detect", "auto")
        self.mode_combo.addItem("By color", "color")
        self.mode_combo.addItem("By contrast", "contrast")
        self.mode_combo.setToolTip(
            "Choose automatic color / contrast detection, color detection, "
            "or high-contrast detection."
        )
        self.target_hue = QtWidgets.QDoubleSpinBox()
        self.target_hue.setRange(-1.0, 179.0)
        self.target_hue.setDecimals(0)
        self.target_hue.setSpecialValueText("Automatic")
        self.target_hue.setValue(-1.0)
        self.hue_tolerance = QtWidgets.QDoubleSpinBox()
        self.hue_tolerance.setRange(1.0, 90.0)
        self.hue_tolerance.setValue(14.0)
        self.hue_tolerance.setSuffix(" hue")
        self.min_saturation = QtWidgets.QSpinBox()
        self.min_saturation.setRange(0, 255)
        self.min_saturation.setValue(45)
        sample_row = QtWidgets.QWidget()
        sample_layout = QtWidgets.QHBoxLayout(sample_row)
        sample_layout.setContentsMargins(0, 0, 0, 0)
        self.pick_color_button = QtWidgets.QPushButton("Pick color")
        self.pick_color_button.setToolTip(
            "Click, then click the target in the corrected camera image."
        )
        self.color_swatch = QtWidgets.QLabel()
        self.color_swatch.setFixedSize(30, 22)
        self.color_swatch.setStyleSheet(
            "background: #7B3333; border: 1px solid #66737C; border-radius: 3px;"
        )
        sample_layout.addWidget(self.pick_color_button, 1)
        sample_layout.addWidget(self.color_swatch)
        _form_row(source_form, "Mode", self.mode_combo)
        _form_row(source_form, "Target hue", self.target_hue)
        _form_row(source_form, "Hue tolerance", self.hue_tolerance)
        _form_row(source_form, "Minimum saturation", self.min_saturation)
        _form_row(source_form, "Sample", sample_row)
        layout.addWidget(source_group)

        filter_group = QtWidgets.QGroupBox("Object filters")
        filter_form = _form_layout()
        filter_group.setLayout(filter_form)
        self.min_area = MeasurementSpinBox("area")
        self.min_area.setRange(0.01, 100_000.0)
        self.min_area.setValue(30.0)
        self.min_area.setSuffix(" mm²")
        self.max_area = MeasurementSpinBox("area")
        self.max_area.setRange(0.1, 1_000_000.0)
        self.max_area.setValue(20_000.0)
        self.max_area.setSuffix(" mm²")
        self.min_width = MeasurementSpinBox()
        self.min_width.setRange(0.1, 1000.0)
        self.min_width.setValue(4.0)
        self.min_width.setSuffix(" mm")
        self.min_height = MeasurementSpinBox()
        self.min_height.setRange(0.1, 1000.0)
        self.min_height.setValue(3.0)
        self.min_height.setSuffix(" mm")
        self.confidence = QtWidgets.QDoubleSpinBox()
        self.confidence.setRange(0.0, 100.0)
        self.confidence.setValue(55.0)
        self.confidence.setSuffix(" %")
        for spin in (
            self.min_area,
            self.max_area,
            self.min_width,
            self.min_height,
            self.confidence,
        ):
            size_policy = spin.sizePolicy()
            size_policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Policy.Expanding)
            spin.setSizePolicy(size_policy)
        self.regular_grid = QtWidgets.QCheckBox("Use grid")
        self.regular_grid.setToolTip(
            "Treat repeated objects as a regular row-and-column layout."
        )
        self.regular_grid.setChecked(True)
        self.infer_missing = QtWidgets.QCheckBox("Infer gaps")
        self.infer_missing.setToolTip(
            "Show grid positions inferred behind missing or obscured objects."
        )
        self.infer_missing.setChecked(True)
        self.repair_grid_edges = QtWidgets.QCheckBox("Repair weak repeated edges")
        self.repair_grid_edges.setToolTip(
            "When one side of a repeated rounded rectangle is obscured or has "
            "weak contrast, borrow only that side from the fitted grid and the "
            "other detected cells. Legitimately shifted or differently sized "
            "cells are not forced to match."
        )
        self.repair_grid_edges.setChecked(True)
        self.normalize_grid = QtWidgets.QCheckBox("Make grid cells identical")
        self.normalize_grid.setToolTip(
            "Fit one repeated-object model to the grid. Every accepted and "
            "inferred cell receives the same width, height, and corner radius. "
            "Use Snap cells to fitted grid when centers and rotations should "
            "also follow the fitted lattice."
        )
        self.normalize_grid.setChecked(True)
        self.snap_grid_cells = QtWidgets.QCheckBox("Snap cells to fitted grid")
        self.snap_grid_cells.setToolTip(
            "When enabled, identical cells share the fitted grid's centers and "
            "rotation. Disable this for a looser grid: direct cells keep their "
            "observed center and rotation while still sharing width, height, "
            "and corner radius. Inferred cells remain on the fitted grid."
        )
        self.snap_grid_cells.setChecked(True)
        self.normalize_anchor = QtWidgets.QComboBox()
        self.normalize_anchor.addItem("Center", "center")
        self.normalize_anchor.addItem("Detected top edge", "top")
        self.normalize_anchor.setToolTip(
            "Choose what stays fixed when direct cells receive an identical "
            "height. Detected top edge prevents a damaged bottom edge from "
            "shifting an otherwise clean top border."
        )
        _form_row(filter_form, "Minimum area", self.min_area)
        _form_row(filter_form, "Maximum area", self.max_area)
        _form_row(filter_form, "Minimum width", self.min_width)
        _form_row(filter_form, "Minimum height", self.min_height)
        confidence_label = QtWidgets.QLabel("Auto-select")
        confidence_label.setToolTip(
            "Automatically select detections at or above this confidence."
        )
        filter_form.addRow(confidence_label, self.confidence)
        filter_form.addRow(self.regular_grid)
        filter_form.addRow(self.infer_missing)
        filter_form.addRow(self.repair_grid_edges)
        filter_form.addRow(self.normalize_grid)
        filter_form.addRow(self.snap_grid_cells)
        _form_row(filter_form, "Identical-cell anchor", self.normalize_anchor)
        layout.addWidget(filter_group)

        output_group = QtWidgets.QGroupBox("Vector output")
        output_form = _form_layout()
        output_group.setLayout(output_form)
        self.trace_purpose = QtWidgets.QComboBox()
        self.trace_purpose.addItem("Cut geometry", "cut")
        self.trace_purpose.addItem("Stock boundary (layout only)", "stock")
        self.trace_purpose.setToolTip(
            "Cut geometry creates normal laser-output objects. Stock boundary "
            "creates one locked, camera-aligned construction outline that is "
            "never included in a toolpath and enables Stock layout controls."
        )
        self.output_mode = QtWidgets.QComboBox()
        self.output_mode.addItem("Best-fit analytic shapes", "rounded")
        self.output_mode.addItem("Simplified contours", "smoothed")
        self.output_mode.addItem("Exact contours", "exact")
        self.output_mode.setMinimumWidth(0)
        self.output_mode.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.output_mode.setToolTip(
            "Choose recognized analytic geometry (including washers, circles, "
            "ellipses, polygons, and rounded rectangles), simplified pixel "
            "contours, or exact pixel-derived contours."
        )
        self.border_offset_mode = QtWidgets.QComboBox()
        self.border_offset_mode.addItem("Uniform", "uniform")
        self.border_offset_mode.addItem("Per edge", "custom")
        self.border_offset_mode.setToolTip(
            "Use one offset on every edge, or adjust the top, right, bottom, "
            "and left edges independently for fitted rounded rectangles."
        )
        self.border_offset = MeasurementSpinBox()
        self.border_offset.setRange(-25.0, 25.0)
        self.border_offset.setDecimals(2)
        self.border_offset.setSingleStep(0.1)
        self.border_offset.setValue(0.0)
        self.border_offset.setSuffix(" mm")
        self.border_offset.setToolTip(
            "Positive values expand every edge; negative values trim every edge."
        )
        self.border_offset_label = QtWidgets.QLabel("Border offset")
        self.border_offset_label.setToolTip(self.border_offset.toolTip())
        self.edge_offsets = QtWidgets.QWidget()
        edge_layout = QtWidgets.QGridLayout(self.edge_offsets)
        edge_layout.setContentsMargins(0, 0, 0, 0)
        edge_layout.setHorizontalSpacing(6)
        edge_layout.setVerticalSpacing(4)
        self.edge_offset_fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        edge_tip = (
            "Edges are relative to the rotated detected object, not the screen. "
            "Positive expands that edge; negative trims that edge and its two "
            "adjoining rounded corners."
        )
        for index, edge in enumerate(("Top", "Right", "Bottom", "Left")):
            label = QtWidgets.QLabel(edge)
            field = MeasurementSpinBox()
            field.setRange(-25.0, 25.0)
            field.setDecimals(2)
            field.setSingleStep(0.1)
            field.setSuffix(" mm")
            field.setToolTip(edge_tip)
            label.setToolTip(edge_tip)
            edge_layout.addWidget(label, index // 2, (index % 2) * 2)
            edge_layout.addWidget(field, index // 2, (index % 2) * 2 + 1)
            self.edge_offset_fields[edge.lower()] = field
        self.edge_offsets.setToolTip(edge_tip)
        self.smoothing = MeasurementSpinBox()
        self.smoothing.setRange(0.0, 10.0)
        self.smoothing.setDecimals(2)
        self.smoothing.setValue(0.25)
        self.smoothing.setSuffix(" mm")
        smoothing_tip = (
            "Maximum contour simplification tolerance for Simplified contours. "
            "Lower values preserve more edge detail. This setting does not "
            "apply to best-fit analytic shapes or Exact contours."
        )
        self.smoothing.setToolTip(smoothing_tip)
        self.smoothing_label = QtWidgets.QLabel("Simplify tolerance")
        self.smoothing_label.setToolTip(smoothing_tip)
        _form_row(output_form, "Purpose", self.trace_purpose)
        _form_row(output_form, "Geometry output", self.output_mode)
        _form_row(output_form, "Offset mode", self.border_offset_mode)
        output_form.addRow(self.border_offset_label, self.border_offset)
        self.edge_offsets_label = QtWidgets.QLabel("Edge offsets")
        self.edge_offsets_label.setToolTip(edge_tip)
        output_form.addRow(self.edge_offsets_label, self.edge_offsets)
        output_form.addRow(self.smoothing_label, self.smoothing)
        layout.addWidget(output_group)

        self.detect_button = QtWidgets.QPushButton("Detect objects")
        self.detect_button.setObjectName("primaryButton")
        self.detect_button.setToolTip(
            "On hardware this homes and parks the machine, holds both axes "
            "through a fresh multi-frame capture, releases them, then traces "
            "the captured image. Ensure the travel path is clear."
        )
        self.clear_button = QtWidgets.QPushButton("Clear detection preview")
        self.clear_button.setToolTip(
            "Clear the temporary camera detection overlay. This does not delete "
            "editable Trace objects that were already created in the project."
        )
        layout.addWidget(self.detect_button)
        layout.addWidget(self.clear_button)

        self.status_label = QtWidgets.QLabel(
            "Capture a clear corrected bed image, then detect objects."
        )
        self.status_label.setObjectName("statusCard")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        result_group = QtWidgets.QGroupBox("Detected outlines")
        result_group.setToolTip("Review detected outlines before creating objects.")
        result_layout = QtWidgets.QVBoxLayout(result_group)
        result_layout.addWidget(
            _muted(
                "Green outlines show the proposed vector output. Gray outlines "
                "are unselected direct detections. Yellow or orange dashed "
                "outlines are inferred and remain unchecked until you approve them."
            )
        )
        self.result_tree = QtWidgets.QTreeWidget()
        self.result_tree.setMinimumHeight(220)
        self.result_tree.setColumnCount(5)
        self.result_tree.setHeaderLabels(
            ["Use", "#", "Source", "Confidence", "Geometry"]
        )
        self.result_tree.setRootIsDecorated(False)
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.header().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.result_tree.header().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.result_tree.header().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.result_tree.header().setSectionResizeMode(
            3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.result_tree.header().setSectionResizeMode(
            4, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.select_all_checkbox = QtWidgets.QCheckBox("Select / deselect all")
        self.select_all_checkbox.setTristate(True)
        self.select_all_checkbox.setEnabled(False)
        self.select_all_checkbox.setToolTip(
            "Check to select every detected outline; clear to deselect every outline. "
            "A partial check means only some outlines are selected."
        )
        result_layout.addWidget(self.select_all_checkbox)
        result_layout.addWidget(self.result_tree)
        select_row = QtWidgets.QVBoxLayout()
        self.select_grid_button = QtWidgets.QPushButton("Select complete grid")
        self.select_grid_button.setToolTip(
            "Select every fitted grid cell, including reviewed inferred gaps."
        )
        self.select_grid_button.setEnabled(False)
        self.select_direct_button = QtWidgets.QPushButton("Select direct")
        self.select_all_button = QtWidgets.QPushButton("Select all")
        self.select_none_button = QtWidgets.QPushButton("Select none")
        select_row.addWidget(self.select_grid_button)
        select_row.addWidget(self.select_direct_button)
        select_row.addWidget(self.select_all_button)
        select_row.addWidget(self.select_none_button)
        result_layout.addLayout(select_row)
        self.create_button = QtWidgets.QPushButton("Create objects")
        self.create_button.setToolTip(
            "Create vector objects from the selected detected outlines."
        )
        self.create_button.setObjectName("primaryButton")
        self.create_button.setEnabled(False)
        self.replace_previous = QtWidgets.QCheckBox("Replace earlier Trace objects")
        self.replace_previous.setChecked(True)
        self.replace_previous.setToolTip(
            "When checked, creating this reviewed batch removes objects made by "
            "earlier Trace captures while preserving drawings, imports, and other "
            "project objects. The replacement is one undoable edit."
        )
        result_layout.addWidget(self.replace_previous)
        result_layout.addWidget(self.create_button)
        self.generate_button = QtWidgets.QPushButton("Generate")
        self.generate_button.setObjectName("traceGenerateButton")
        self.generate_button.setToolTip("Generate the current project toolpath")
        result_layout.addWidget(self.generate_button)
        layout.addWidget(result_group)
        layout.addStretch(1)

        self._restore_preferences()

        self.pick_color_button.clicked.connect(self.pickColorRequested)
        self.detect_button.clicked.connect(
            lambda: self.detectRequested.emit(self.options())
        )
        self.clear_button.clicked.connect(self._clear_clicked)
        self.create_button.clicked.connect(self._create_clicked)
        self.generate_button.clicked.connect(self.generateRequested)
        self.result_tree.itemChanged.connect(self._result_changed)
        self.select_all_checkbox.stateChanged.connect(
            self._select_all_checkbox_changed
        )
        self.select_grid_button.clicked.connect(
            lambda: self._set_all_checked(True, include_inferred=True)
        )
        self.select_direct_button.clicked.connect(self._select_direct)
        self.select_all_button.clicked.connect(
            lambda: self._set_all_checked(True, include_inferred=True)
        )
        self.select_none_button.clicked.connect(
            lambda: self._set_all_checked(False, include_inferred=True)
        )
        self.trace_purpose.currentIndexChanged.connect(self._sync_output_controls)
        self.output_mode.currentIndexChanged.connect(self._sync_output_controls)
        self.border_offset_mode.currentIndexChanged.connect(
            self._sync_output_controls
        )
        self.regular_grid.toggled.connect(self._sync_output_controls)
        self.normalize_grid.toggled.connect(self._sync_output_controls)
        self.snap_grid_cells.toggled.connect(self._sync_output_controls)

        for widget in (
            self.mode_combo,
            self.target_hue,
            self.hue_tolerance,
            self.min_saturation,
            self.min_area,
            self.max_area,
            self.min_width,
            self.min_height,
            self.confidence,
            self.regular_grid,
            self.infer_missing,
            self.repair_grid_edges,
            self.normalize_grid,
            self.snap_grid_cells,
            self.normalize_anchor,
            self.output_mode,
            self.border_offset_mode,
            self.border_offset,
            *self.edge_offset_fields.values(),
            self.smoothing,
        ):
            if isinstance(widget, QtWidgets.QComboBox):
                widget.currentIndexChanged.connect(self._mark_stale)
                widget.currentIndexChanged.connect(self._save_preferences)
            elif isinstance(widget, QtWidgets.QAbstractButton):
                widget.toggled.connect(self._mark_stale)
                widget.toggled.connect(self._save_preferences)
            else:
                widget.valueChanged.connect(self._mark_stale)
                widget.valueChanged.connect(self._save_preferences)
        self._sync_output_controls()

    @staticmethod
    def _settings_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _set_combo_data(combo: QtWidgets.QComboBox, value: Any) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _restore_preferences(self) -> None:
        settings = self._trace_settings
        settings.beginGroup("trace")
        try:
            self._set_combo_data(
                self.mode_combo,
                settings.value("detection_mode", self.mode_combo.currentData()),
            )
            self.target_hue.setValue(
                float(settings.value("target_hue", self.target_hue.value()))
            )
            self.hue_tolerance.setValue(
                float(settings.value("hue_tolerance", self.hue_tolerance.value()))
            )
            self.min_saturation.setValue(
                int(float(settings.value("min_saturation", self.min_saturation.value())))
            )
            self.min_area.setValue(
                float(settings.value("min_area_mm2", self.min_area.value()))
            )
            self.max_area.setValue(
                float(settings.value("max_area_mm2", self.max_area.value()))
            )
            self.min_width.setValue(
                float(settings.value("min_width_mm", self.min_width.value()))
            )
            self.min_height.setValue(
                float(settings.value("min_height_mm", self.min_height.value()))
            )
            self.confidence.setValue(
                float(settings.value("confidence_percent", self.confidence.value()))
            )

            for key, widget in (
                ("regular_grid", self.regular_grid),
                ("infer_missing", self.infer_missing),
                ("repair_grid_edges", self.repair_grid_edges),
                ("normalize_grid", self.normalize_grid),
                ("snap_grid_cells", self.snap_grid_cells),
            ):
                widget.setChecked(
                    self._settings_bool(settings.value(key, widget.isChecked()), widget.isChecked())
                )

            self._set_combo_data(
                self.normalize_anchor,
                settings.value("normalize_anchor", self.normalize_anchor.currentData()),
            )
            self._set_combo_data(
                self.output_mode,
                settings.value("output_mode", self.output_mode.currentData()),
            )
            self._set_combo_data(
                self.border_offset_mode,
                settings.value(
                    "border_offset_mode", self.border_offset_mode.currentData()
                ),
            )
            self.border_offset.setValue(
                float(settings.value("border_offset_mm", self.border_offset.value()))
            )
            for edge, field in self.edge_offset_fields.items():
                field.setValue(
                    float(settings.value(f"border_offset_{edge}_mm", field.value()))
                )
            self.smoothing.setValue(
                float(settings.value("smoothing_mm", self.smoothing.value()))
            )
            sampled_hue = settings.value("sampled_hue")
            sampled_bgr = str(settings.value("sampled_bgr", "")).strip()
            if sampled_hue is not None and sampled_bgr:
                try:
                    channels = [int(part) for part in sampled_bgr.split(",")]
                    if len(channels) == 3 and all(0 <= value <= 255 for value in channels):
                        self._sampled_hue = float(sampled_hue)
                        self._sampled_bgr = channels
                        blue, green, red = channels
                        self.color_swatch.setStyleSheet(
                            f"background: rgb({red},{green},{blue}); "
                            "border: 1px solid #AAB4BB; border-radius: 3px;"
                        )
                except (TypeError, ValueError):
                    self._sampled_hue = None
                    self._sampled_bgr = None
        finally:
            settings.endGroup()

    def _save_preferences(self, *_args: object) -> None:
        settings = self._trace_settings
        settings.beginGroup("trace")
        try:
            settings.setValue("detection_mode", self.mode_combo.currentData())
            settings.setValue("target_hue", self.target_hue.value())
            settings.setValue("hue_tolerance", self.hue_tolerance.value())
            settings.setValue("min_saturation", self.min_saturation.value())
            settings.setValue("min_area_mm2", self.min_area.value())
            settings.setValue("max_area_mm2", self.max_area.value())
            settings.setValue("min_width_mm", self.min_width.value())
            settings.setValue("min_height_mm", self.min_height.value())
            settings.setValue("confidence_percent", self.confidence.value())
            settings.setValue("regular_grid", self.regular_grid.isChecked())
            settings.setValue("infer_missing", self.infer_missing.isChecked())
            settings.setValue("repair_grid_edges", self.repair_grid_edges.isChecked())
            settings.setValue("normalize_grid", self.normalize_grid.isChecked())
            settings.setValue("snap_grid_cells", self.snap_grid_cells.isChecked())
            settings.setValue("normalize_anchor", self.normalize_anchor.currentData())
            settings.setValue("output_mode", self.output_mode.currentData())
            settings.setValue("border_offset_mode", self.border_offset_mode.currentData())
            settings.setValue("border_offset_mm", self.border_offset.value())
            for edge, field in self.edge_offset_fields.items():
                settings.setValue(f"border_offset_{edge}_mm", field.value())
            settings.setValue("smoothing_mm", self.smoothing.value())
            if self._sampled_hue is not None and self._sampled_bgr is not None:
                settings.setValue("sampled_hue", self._sampled_hue)
                settings.setValue(
                    "sampled_bgr", ",".join(str(value) for value in self._sampled_bgr)
                )
            else:
                settings.remove("sampled_hue")
                settings.remove("sampled_bgr")
        finally:
            settings.endGroup()
        settings.sync()

    def options(self) -> dict[str, Any]:
        hue = self.target_hue.value()
        sampled_color_is_current = (
            self._sampled_bgr is not None
            and self._sampled_hue is not None
            and hue >= 0
            and min(
                abs(hue - self._sampled_hue),
                180.0 - abs(hue - self._sampled_hue),
            )
            < 0.5
        )
        return {
            "detection_mode": str(self.mode_combo.currentData()),
            "target_hue": None if hue < 0 else hue,
            "target_bgr": (
                list(self._sampled_bgr) if sampled_color_is_current else None
            ),
            "hue_tolerance": self.hue_tolerance.value(),
            "min_saturation": self.min_saturation.value(),
            "min_area_mm2": self.min_area.value(),
            "max_area_mm2": self.max_area.value(),
            "min_width_mm": self.min_width.value(),
            "min_height_mm": self.min_height.value(),
            "confidence_threshold": self.confidence.value() / 100.0,
            "regular_grid": self.regular_grid.isChecked(),
            "infer_missing": self.infer_missing.isChecked(),
            "repair_grid_edges": self.repair_grid_edges.isChecked(),
            "normalize_grid": self.normalize_grid.isChecked(),
            "snap_grid_cells": self.snap_grid_cells.isChecked(),
            "normalize_anchor": str(self.normalize_anchor.currentData()),
            "output_mode": str(self.output_mode.currentData()),
            "border_offset_mode": str(self.border_offset_mode.currentData()),
            "border_offset_mm": self.border_offset.value(),
            "border_offset_top_mm": self.edge_offset_fields["top"].value(),
            "border_offset_right_mm": self.edge_offset_fields["right"].value(),
            "border_offset_bottom_mm": self.edge_offset_fields["bottom"].value(),
            "border_offset_left_mm": self.edge_offset_fields["left"].value(),
            "smoothing_mm": self.smoothing.value(),
        }

    def set_color_sample(self, payload: dict[str, Any]) -> None:
        self.set_color_pick_active(False)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData("color"))
        self._sampled_hue = float(payload["hue"])
        self.target_hue.setValue(self._sampled_hue)
        self._sampled_bgr = [int(value) for value in payload.get("bgr", [])]
        if len(self._sampled_bgr) != 3:
            self._sampled_bgr = None
        rgb = payload.get("rgb", [128, 64, 64])
        red, green, blue = (int(value) for value in rgb)
        self.color_swatch.setStyleSheet(
            f"background: rgb({red},{green},{blue}); "
            "border: 1px solid #AAB4BB; border-radius: 3px;"
        )
        self.status_label.setText(
            f"Sampled hue {float(payload['hue']):.0f} at "
            f"X{float(payload['machine_x']):.2f} "
            f"Y{float(payload['machine_y']):.2f}. Press Detect objects."
        )
        self._save_preferences()

    def set_color_pick_active(self, active: bool, *, sampling: bool = False) -> None:
        self._color_pick_active = bool(active)
        if sampling:
            self.pick_color_button.setText("Sampling…")
            self.pick_color_button.setEnabled(False)
            self.status_label.setText("Sampling the selected camera point…")
        elif active:
            self.pick_color_button.setText("Cancel color pick")
            self.pick_color_button.setEnabled(True)
            self.status_label.setText(
                "COLOR PICK ACTIVE — click inside the target on the camera image."
            )
        else:
            self.pick_color_button.setText("Pick color")
            self.pick_color_button.setEnabled(self._calibration_ready)

    def set_color_pick_failed(self, message: str) -> None:
        self.set_color_pick_active(False)
        self.status_label.setText(f"Color sampling failed: {message}")

    def set_result(self, result: dict[str, Any]) -> None:
        self._detections = list(result.get("detections", []))
        grid = result.get("grid") or {}
        grid_normalized = bool(grid.get("normalized"))
        cells_snapped = bool(grid.get("cells_snapped"))
        output_area = (
            result.get("output_work_area")
            or grid.get("output_work_area")
            or {}
        )
        camera_area = (
            result.get("camera_work_area")
            or grid.get("camera_work_area")
            or {}
        )
        guarded_output_area = bool(output_area and output_area != camera_area)
        boundary_name = (
            "guarded output area" if guarded_output_area else "configured work area"
        )
        self._result_is_current = True
        self._updating = True
        try:
            self.result_tree.clear()
            for detection in self._detections:
                item = QtWidgets.QTreeWidgetItem()
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, detection["id"])
                item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0,
                    QtCore.Qt.CheckState.Checked
                    if detection.get("selected_default")
                    else QtCore.Qt.CheckState.Unchecked,
                )
                item.setText(1, str(detection.get("index", "")))
                source = str(detection.get("source", "direct"))
                diagnostics = detection.get("diagnostics") or {}
                within_work_area = bool(
                    diagnostics.get("within_work_area", True)
                )
                touches_image_edge = bool(
                    diagnostics.get("touches_image_edge", False)
                )
                source_text = source.title()
                if touches_image_edge:
                    source_text += " · cropped"
                if not within_work_area:
                    source_text += " · outside"
                if diagnostics.get("damage_suspected"):
                    source_text += " · damaged?"
                if diagnostics.get("likely_open_cell"):
                    source_text += " · likely cut/open"
                item.setText(2, source_text)
                item.setText(3, f"{float(detection.get('confidence', 0)) * 100:.0f}%")
                item.setText(
                    4,
                    f"{float(detection.get('width_mm', 0)):.1f} × "
                    f"{float(detection.get('height_mm', 0)):.1f} mm",
                )
                shape = str(detection.get("shape", "contour"))
                if shape == "rounded_rectangle":
                    radius = float(detection.get("corner_radius_mm", 0.0))
                    item.setText(4, f"{item.text(4)} · R {radius:.2f} mm")
                    geometry_tip = (
                        "Fitted rounded rectangle: "
                        f"{float(detection.get('width_mm', 0)):.2f} × "
                        f"{float(detection.get('height_mm', 0)):.2f} mm, "
                        f"corner radius {radius:.2f} mm, rotation "
                        f"{float(detection.get('rotation_deg', 0)):.2f}°."
                    )
                    if diagnostics.get("grid_normalized"):
                        row = int(diagnostics.get("grid_row", 0)) + 1
                        column = int(diagnostics.get("grid_column", 0)) + 1
                        geometry_tip += (
                            f" Fitted grid cell row {row}, column {column}. "
                        )
                        if source == "inferred":
                            geometry_tip += (
                                "This missing position is inferred from the "
                                "fitted lattice and has no observed pose."
                            )
                        elif cells_snapped:
                            geometry_tip += (
                                "Its dimensions, center, and rotation follow "
                                "the repeated-object lattice."
                            )
                        else:
                            geometry_tip += (
                                "Its dimensions are shared across the grid; "
                                "its observed center and rotation are retained."
                            )
                        if "observed_width_mm" in diagnostics:
                            geometry_tip += (
                                " Raw observation was "
                                f"{float(diagnostics['observed_width_mm']):.2f} × "
                                f"{float(diagnostics['observed_height_mm']):.2f} mm."
                            )
                        repaired_axes = diagnostics.get("repaired_center_axes") or []
                        if repaired_axes:
                            geometry_tip += (
                                " Its observed "
                                + " and ".join(str(axis) for axis in repaired_axes)
                                + " was truncated or enlarged, so that center axis "
                                "uses the repeated-grid fit."
                            )
                elif shape == "washer":
                    hole_ratio = float(diagnostics.get("hole_ratio", 0.0))
                    outer_diameter = float(detection.get("width_mm", 0.0))
                    inner_diameter = outer_diameter * hole_ratio
                    item.setText(
                        4,
                        f"Washer · OD {outer_diameter:.2f} mm · "
                        f"ID {inner_diameter:.2f} mm",
                    )
                    geometry_tip = (
                        "Best-fit washer: concentric circular contours with "
                        f"outer diameter {outer_diameter:.2f} mm, inner diameter "
                        f"{inner_diameter:.2f} mm, and center offset "
                        f"{float(diagnostics.get('center_offset_mm', 0.0)):.3f} mm."
                    )
                elif shape in {"circle", "ellipse", "triangle", "regular_polygon"}:
                    display_shape = shape.replace("_", " ").title()
                    item.setText(4, f"{display_shape} · {item.text(4)}")
                    geometry_tip = (
                        f"Best-fit {shape.replace('_', ' ')}: "
                        f"{float(detection.get('width_mm', 0)):.2f} × "
                        f"{float(detection.get('height_mm', 0)):.2f} mm."
                    )
                else:
                    geometry_tip = (
                        "No analytic fit passed the confidence gates, so the "
                        "detected contour geometry will be used."
                    )
                if diagnostics.get("damage_suspected"):
                    reasons = "; ".join(
                        str(value) for value in diagnostics.get("damage_reasons", [])
                    )
                    geometry_tip += (
                        " DAMAGE SUSPECTED: this observation disagrees with the "
                        f"repeated-cell family ({reasons}). A trace is still "
                        "shown, but it was left unchecked for review."
                    )
                if diagnostics.get("likely_open_cell"):
                    geometry_tip += (
                        " LIKELY ALREADY CUT / OPEN: the cell interior has much "
                        "stronger exposed-bed texture and edge evidence than the "
                        "label-family baseline. It was left unchecked."
                    )
                if not within_work_area:
                    overrun = float(
                        diagnostics.get("work_area_overrun_mm", 0.0)
                    )
                    if (
                        diagnostics.get("grid_normalized")
                        and diagnostics.get("observed_within_work_area") is True
                    ):
                        geometry_tip += (
                            " The raw observed fit was inside the output limit, "
                            "but shared grid sizing makes this fitted output extend "
                            f"{overrun:.2f} mm outside the {boundary_name}."
                        )
                    else:
                        geometry_tip += (
                            f" This output extends {overrun:.2f} mm outside the "
                            f"{boundary_name}."
                        )
                    geometry_tip += (
                        " It was not preselected. Reposition the workpiece or "
                        "verify the configured machine limits before engraving."
                    )
                if touches_image_edge:
                    edge_names = ", ".join(
                        str(value)
                        for value in diagnostics.get("image_edge_sides", [])
                    ) or "camera"
                    geometry_tip += (
                        f" The observed mask touches the {edge_names} edge of the "
                        "corrected camera/work-area raster, so the complete object "
                        "outline cannot be verified and it was not preselected."
                    )
                for column in range(self.result_tree.columnCount()):
                    item.setToolTip(column, geometry_tip)
                if not within_work_area or touches_image_edge:
                    item.setForeground(2, QtGui.QColor("#E06666"))
                elif source == "inferred":
                    item.setForeground(2, QtGui.QColor("#E7B55C"))
                self.result_tree.addTopLevelItem(item)
        finally:
            self._updating = False
        self.select_grid_button.setEnabled(grid_normalized and bool(self._detections))
        if grid_normalized:
            self.select_grid_button.setText(
                f"Select complete {int(grid.get('columns', 0))} × "
                f"{int(grid.get('rows', 0))} grid"
            )
        else:
            self.select_grid_button.setText("Select complete grid")
        outside_cells = int(grid.get("outside_cells", 0))
        if outside_cells:
            self.select_grid_button.setToolTip(
                "Select every fitted grid cell, including inferred and "
                "out-of-limit cells. Generation remains blocked until every "
                f"selected output lies inside the {boundary_name}."
            )
        else:
            self.select_grid_button.setToolTip(
                "Select every fitted grid cell, including reviewed inferred gaps."
            )
        self.status_label.setText(str(result.get("message", "Detection complete")))
        self._sync_select_all_checkbox()
        self._update_create_button()
        self.selectionChanged.emit(self.selected_ids())

    def set_calibration_ready(self, ready: bool) -> None:
        self._calibration_ready = bool(ready)
        self.detect_button.setEnabled(bool(ready))
        if not self._color_pick_active:
            self.pick_color_button.setEnabled(bool(ready))
        if not ready:
            self.status_label.setText(
                "Bed mapping is required before camera objects can be traced."
            )

    def detections(self) -> list[dict[str, Any]]:
        return list(self._detections)

    def selected_ids(self) -> list[str]:
        selected = []
        for row in range(self.result_tree.topLevelItemCount()):
            item = self.result_tree.topLevelItem(row)
            if item.checkState(0) == QtCore.Qt.CheckState.Checked:
                selected.append(str(item.data(0, QtCore.Qt.ItemDataRole.UserRole)))
        return selected

    def clear_result(self) -> None:
        self._detections = []
        self._result_is_current = False
        self.result_tree.clear()
        self.select_grid_button.setText("Select complete grid")
        self.select_grid_button.setEnabled(False)
        self._sync_select_all_checkbox()
        self.create_button.setEnabled(False)
        self.status_label.setText("Trace preview cleared.")

    def set_generate_enabled(self, enabled: bool) -> None:
        self._generate_enabled = bool(enabled)
        self.generate_button.setEnabled(self._generate_enabled)

    def _mark_stale(self, *args: Any) -> None:
        del args
        if self._updating or not self._detections:
            return
        self._result_is_current = False
        self.create_button.setEnabled(False)
        self.status_label.setText(
            "Trace settings changed. Run Detect objects again before creating paths."
        )

    def _sync_output_controls(self, *args: Any) -> None:
        del args
        stock_mode = self.trace_purpose.currentData() == "stock"
        self.replace_previous.setText(
            "Replace earlier Stock boundary"
            if stock_mode
            else "Replace earlier Trace objects"
        )
        self.create_button.setToolTip(
            "Create one locked, non-cutting Stock boundary from the selected outline."
            if stock_mode
            else "Create vector objects from the selected detected outlines."
        )
        rounded_output = self.output_mode.currentData() == "rounded"
        if not rounded_output and self.border_offset_mode.currentData() == "custom":
            self.border_offset_mode.setCurrentIndex(
                self.border_offset_mode.findData("uniform")
            )
        custom_offset = (
            rounded_output and self.border_offset_mode.currentData() == "custom"
        )
        self.border_offset_mode.setEnabled(rounded_output)
        self.border_offset_label.setVisible(not custom_offset)
        self.border_offset.setVisible(not custom_offset)
        self.edge_offsets_label.setVisible(custom_offset)
        self.edge_offsets.setVisible(custom_offset)
        smoothing_enabled = self.output_mode.currentData() == "smoothed"
        self.smoothing_label.setEnabled(smoothing_enabled)
        self.smoothing.setEnabled(smoothing_enabled)
        grid_enabled = self.regular_grid.isChecked()
        self.infer_missing.setEnabled(grid_enabled)
        self.repair_grid_edges.setEnabled(
            grid_enabled and self.output_mode.currentData() == "rounded"
        )
        self.normalize_grid.setEnabled(
            grid_enabled and self.output_mode.currentData() == "rounded"
        )
        self.snap_grid_cells.setEnabled(
            self.normalize_grid.isEnabled() and self.normalize_grid.isChecked()
        )
        self.normalize_anchor.setEnabled(
            self.snap_grid_cells.isEnabled()
            and not self.snap_grid_cells.isChecked()
        )
        self._update_create_button()

    def _result_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        del item, column
        if self._updating:
            return
        self._sync_select_all_checkbox()
        self._update_create_button()
        self.selectionChanged.emit(self.selected_ids())

    def _sync_select_all_checkbox(self) -> None:
        total = self.result_tree.topLevelItemCount()
        selected = len(self.selected_ids())
        if total == 0 or selected == 0:
            state = QtCore.Qt.CheckState.Unchecked
        elif selected == total:
            state = QtCore.Qt.CheckState.Checked
        else:
            state = QtCore.Qt.CheckState.PartiallyChecked
        was_updating = self._updating
        self._updating = True
        try:
            self.select_all_checkbox.setEnabled(total > 0)
            self.select_all_checkbox.setCheckState(state)
        finally:
            self._updating = was_updating

    def _select_all_checkbox_changed(self, state: int) -> None:
        if self._updating:
            return
        check_state = QtCore.Qt.CheckState(state)
        if check_state == QtCore.Qt.CheckState.PartiallyChecked:
            return
        self._set_all_checked(
            check_state == QtCore.Qt.CheckState.Checked,
            include_inferred=True,
        )

    def _update_create_button(self) -> None:
        count = len(self.selected_ids())
        stock_mode = self.trace_purpose.currentData() == "stock"
        if stock_mode:
            self.create_button.setText(
                "Create stock boundary"
                if count == 1
                else "Select one stock outline"
            )
            enabled = self._result_is_current and count == 1
        else:
            self.create_button.setText(
                f"Create {count} object{'s' if count != 1 else ''}"
            )
            enabled = self._result_is_current and count > 0
        self.create_button.setEnabled(enabled)

    def _set_all_checked(self, checked: bool, include_inferred: bool) -> None:
        self._updating = True
        try:
            for row in range(self.result_tree.topLevelItemCount()):
                item = self.result_tree.topLevelItem(row)
                detection = self._detections[row]
                if include_inferred or detection.get("source") != "inferred":
                    item.setCheckState(
                        0,
                        QtCore.Qt.CheckState.Checked
                        if checked
                        else QtCore.Qt.CheckState.Unchecked,
                    )
        finally:
            self._updating = False
        self._sync_select_all_checkbox()
        self._update_create_button()
        self.selectionChanged.emit(self.selected_ids())

    def _select_direct(self) -> None:
        self._updating = True
        try:
            for row in range(self.result_tree.topLevelItemCount()):
                item = self.result_tree.topLevelItem(row)
                detection = self._detections[row]
                diagnostics = detection.get("diagnostics") or {}
                direct = (
                    detection.get("source") == "direct"
                    and bool(diagnostics.get("within_work_area", True))
                    and not bool(diagnostics.get("touches_image_edge", False))
                )
                item.setCheckState(
                    0,
                    QtCore.Qt.CheckState.Checked
                    if direct
                    else QtCore.Qt.CheckState.Unchecked,
                )
        finally:
            self._updating = False
        self._sync_select_all_checkbox()
        self._update_create_button()
        self.selectionChanged.emit(self.selected_ids())

    def _clear_clicked(self) -> None:
        self.clear_result()
        self.clearRequested.emit()

    def _create_clicked(self) -> None:
        if not self._result_is_current:
            return
        self.createRequested.emit(
            {
                "selected_ids": self.selected_ids(),
                "output_mode": str(self.output_mode.currentData()),
                "purpose": str(self.trace_purpose.currentData()),
                "replace_previous": self.replace_previous.isChecked(),
            }
        )

class MachinePanel(QtWidgets.QWidget):
    parkRequested = QtCore.Signal()
    jogRequested = QtCore.Signal(float, float, float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._connected = False
        self._allow_motion = False
        self._reconnect_required = False
        self._jog_ready = False
        self._armed = False
        self._job_running = False
        layout = _dense_panel_layout(self)

        self.state_label = QtWidgets.QLabel("Disconnected")
        self.state_label.setObjectName("statusCard")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        self.park_button = QtWidgets.QPushButton("Home / park")
        self.park_button.setToolTip("Home and park at the configured camera pose")
        layout.addWidget(self.park_button)

        self.jog_group = QtWidgets.QGroupBox("Jog")
        jog_layout = QtWidgets.QGridLayout(self.jog_group)
        jog_layout.setContentsMargins(6, 10, 6, 6)
        jog_layout.setHorizontalSpacing(4)
        jog_layout.setVerticalSpacing(3)
        self.jog_step = QtWidgets.QComboBox()
        for value in (0.1, 1.0, 5.0, 10.0, 50.0):
            self.jog_step.addItem(f"{value:g} mm", value)
        self.jog_step.setEditable(True)
        self.jog_step.setToolTip("Jog distance; enter a value in mm or in")
        self.jog_speed = MeasurementSpinBox("speed")
        self.jog_speed.setRange(1.0, 10000.0)
        self.jog_speed.setValue(2000.0)
        self.jog_speed.setSuffix(" mm/min")
        self.jog_up = QtWidgets.QPushButton("Y+")
        self.jog_down = QtWidgets.QPushButton("Y−")
        self.jog_left = QtWidgets.QPushButton("X−")
        self.jog_right = QtWidgets.QPushButton("X+")
        jog_layout.addWidget(self.jog_up, 0, 1)
        jog_layout.addWidget(self.jog_left, 1, 0)
        jog_layout.addWidget(self.jog_right, 1, 2)
        jog_layout.addWidget(self.jog_down, 2, 1)
        jog_layout.addWidget(QtWidgets.QLabel("Step"), 3, 0)
        jog_layout.addWidget(self.jog_step, 3, 1, 1, 2)
        jog_layout.addWidget(QtWidgets.QLabel("Speed"), 4, 0)
        jog_layout.addWidget(self.jog_speed, 4, 1, 1, 2)
        jog_layout.setColumnStretch(1, 1)
        self.jog_note = _muted(
            "Laser-off incremental moves from the last Home / park pose. "
            "Jogging may move beyond the configured work area for limit measurement."
        )
        jog_layout.addWidget(self.jog_note, 5, 0, 1, 3)
        self.jog_group.setEnabled(False)
        layout.addWidget(self.jog_group)

        self.safety_note = _muted(
            "Software stop requests feed hold, controller reset, and laser off. "
            "It does not replace the physical emergency stop."
        )
        layout.addWidget(self.safety_note)
        layout.addStretch(1)

        self.park_button.clicked.connect(self.parkRequested)
        self.jog_up.clicked.connect(lambda: self._jog(0.0, 1.0))
        self.jog_down.clicked.connect(lambda: self._jog(0.0, -1.0))
        self.jog_left.clicked.connect(lambda: self._jog(-1.0, 0.0))
        self.jog_right.clicked.connect(lambda: self._jog(1.0, 0.0))

    def _jog(self, x_direction: float, y_direction: float) -> None:
        try:
            step = parse_to_mm(self.jog_step.currentText(), "mm")
        except ValueError:
            self.state_label.setText(
                'Jog step must be a measurement such as "5 mm" or "0.25 in"'
            )
            return
        self.jogRequested.emit(
            x_direction * step,
            y_direction * step,
            self.jog_speed.value(),
        )

    def set_status(self, status: dict[str, Any] | None) -> None:
        if not status:
            self.state_label.setText("Controller unavailable")
            self._connected = False
            self._allow_motion = False
            self._reconnect_required = False
            self._jog_ready = False
            self._armed = False
            self._job_running = False
            self._sync_action_buttons()
            return
        connected = bool(status.get("connected", False))
        connecting = bool(status.get("connecting", False))
        self._connected = connected
        self._allow_motion = bool(status.get("allow_motion"))
        self._reconnect_required = bool(
            status.get("controller_reconnect_required", False)
        )
        self._jog_ready = bool(status.get("jog_ready", False))
        maximum_jog_feed = status.get("max_travel_feed_mm_min")
        if type(maximum_jog_feed) in {int, float} and math.isfinite(
            float(maximum_jog_feed)
        ) and float(maximum_jog_feed) > 0:
            self.jog_speed.setMaximum(float(maximum_jog_feed))
        armed = bool(status.get("armed", False))
        job = status.get("job", {})
        self._armed = armed
        self._job_running = bool(job.get("running", False))
        state = "RUNNING" if job.get("running") else (
            "ARMED" if armed else "SAFE"
        )
        if self._reconnect_required:
            motion_state = "RECONNECT REQUIRED"
        elif (
            connected
            and status.get("backend") == "serial"
            and status.get("allow_motion")
            and not status.get("coordinate_reference_ready", False)
        ):
            motion_state = "HOME REQUIRED"
        else:
            motion_state = f"Motion {'ready' if self._allow_motion else 'blocked'}"
        self.state_label.setText(
            f"{'Connecting' if connecting else ('Connected' if connected else 'Disconnected')} | "
            f"{status.get('protocol', 'unknown')} | {state} | "
            + motion_state
        )
        self._sync_action_buttons()

    def set_busy(self, busy: bool) -> None:
        """Prevent overlapping machine actions."""

        self._busy = bool(busy)
        self._sync_action_buttons()

    def _sync_action_buttons(self) -> None:
        self.park_button.setEnabled(
            not self._busy
            and self._allow_motion
            and not self._reconnect_required
        )
        jog_enabled = (
            not self._busy
            and self._connected
            and self._allow_motion
            and not self._reconnect_required
            and self._jog_ready
            and not self._armed
            and not self._job_running
        )
        self.jog_group.setEnabled(jog_enabled)
        if self._reconnect_required:
            jog_tip = "Disconnect and reconnect, then Home / park before jogging"
        elif not self._connected:
            jog_tip = "Connect and complete Home / park before jogging"
        elif not self._allow_motion:
            jog_tip = "Jogging is blocked by machine.allow_motion"
        elif self._armed:
            jog_tip = "Disarm laser control before jogging"
        elif self._job_running:
            jog_tip = "Wait for the controller job to finish"
        elif not self._jog_ready:
            jog_tip = "Complete Home / park before jogging"
        elif self._busy:
            jog_tip = "Wait for the current machine operation to finish"
        else:
            jog_tip = "Laser-off move; configured work-area bounds are not applied"
        self.jog_group.setToolTip(jog_tip)
        self.park_button.setToolTip(
            "Disconnect and reconnect before Home / park"
            if self._reconnect_required
            else (
                "Connect automatically, then home and park at the configured camera pose"
                if not self._connected
                else "Home and park at the configured camera pose"
            )
        )


class ConsolePanel(QtWidgets.QWidget):
    commandSubmitted = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = _panel_layout(self)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(1000)
        self.command = QtWidgets.QLineEdit()
        self.command.setPlaceholderText("Read-only diagnostic command")
        self.send = QtWidgets.QPushButton("Send")
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.command, 1)
        row.addWidget(self.send)
        layout.addWidget(self.output, 1)
        layout.addLayout(row)
        self.send.clicked.connect(self._submit)
        self.command.returnPressed.connect(self._submit)

    def _submit(self) -> None:
        text = self.command.text().strip()
        if text:
            self.commandSubmitted.emit(text)
            self.command.clear()

    def set_lines(self, lines: list[str]) -> None:
        text = "\n".join(lines)
        if text != self.output.toPlainText():
            self.output.setPlainText(text)
            scrollbar = self.output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def append_line(self, line: str) -> None:
        self.output.appendPlainText(line)


class JobProgressWidget(QtWidgets.QStackedWidget):
    """Global preparation/execution progress for the main-window status bar."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("jobProgressWidget")
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.setMinimumWidth(180)
        self.setFixedHeight(18)

        self._preparation_summary = ""
        self._prepared_summary = "No job generated"
        self._execution_summary = "Controller idle · no job started"
        self._execution_format = "Execution 0%"
        self.preparation_progress = QtWidgets.QProgressBar()
        self.preparation_progress.setObjectName("jobPreparationProgress")
        self.preparation_progress.setFixedHeight(18)
        self.preparation_progress.setTextVisible(True)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setObjectName("jobExecutionProgress")
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(18)
        self.progress.setFormat(self._execution_format)
        self.addWidget(self.progress)
        self.addWidget(self.preparation_progress)
        self._preparing = False
        self.setCurrentWidget(self.progress)
        self._sync_tooltip()

    def _sync_tooltip(self) -> None:
        summaries = []
        if self._preparing and self._preparation_summary:
            summaries.append(self._preparation_summary)
        summaries.extend((self._prepared_summary, self._execution_summary))
        self.setToolTip("\n".join(summaries))
        self.progress.setToolTip(self.toolTip())
        self.preparation_progress.setToolTip(self.toolTip())

    def _sync_execution_format(self) -> None:
        self.progress.setFormat(self._execution_format)

    def set_preparing(
        self,
        active: bool,
        label: str = "Preparing exact job preview",
        *,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        self._preparing = bool(active)
        if self._preparing:
            self._preparation_summary = str(label)
            if completed is None or total is None or total <= 0:
                self.preparation_progress.setRange(0, 0)
                visible_label = "Preparing…"
            else:
                self.preparation_progress.setRange(0, int(total))
                self.preparation_progress.setValue(
                    max(0, min(int(completed), int(total)))
                )
                progress = max(0.0, min(float(completed) / float(total), 1.0))
                visible_label = f"Preparing {progress * 100:.0f}%"
            self.preparation_progress.setFormat(visible_label)
            self.setCurrentWidget(self.preparation_progress)
        else:
            self._preparation_summary = ""
            self.preparation_progress.setRange(0, 100)
            self.preparation_progress.setValue(0)
            self.setCurrentWidget(self.progress)
        self._sync_tooltip()

    def set_job_status(self, job: dict[str, Any] | None) -> None:
        job = job or {}
        running = bool(job.get("running", False))
        total = int(job.get("total_lines", 0) or 0)
        completed = int(job.get("completed_lines", 0) or 0)
        progress = 0.0 if total <= 0 else completed / total
        self.progress.setValue(int(round(progress * 1000)))
        phase = str(job.get("phase", "streaming" if running else "idle"))
        finishing_labels = {
            "draining": "Finishing · motion",
            "homing": "Finishing · homing",
            "parking": "Finishing · parking",
            "releasing": "Finishing · release",
        }
        self._execution_format = finishing_labels.get(
            phase,
            f"Execution {progress * 100:.0f}%",
        )
        self._sync_execution_format()
        if running:
            execution_labels = {
                "draining": "Toolpath sent · waiting for queued motion to finish",
                "homing": "Toolpath complete · homing machine",
                "parking": "Homing complete · moving to park position",
                "releasing": "Park complete · releasing motors",
            }
            self._execution_summary = execution_labels.get(
                phase,
                f"Running {job.get('name', 'job')} · {completed}/{total} lines",
            )
        elif job.get("error"):
            self._execution_summary = f"Controller stopped: {job['error']}"
        elif total:
            self._execution_summary = (
                f"Controller idle · last job {completed}/{total} lines"
            )
        else:
            self._execution_summary = "Controller idle · no job started"
        self._sync_tooltip()

    def set_prepared_job(
        self,
        summary: str,
        *,
        power_percent: float,
        controller_power: float,
    ) -> None:
        self._prepared_summary = (
            f"Prepared · {summary} · max power {power_percent:.1f}% / "
            f"S{controller_power:g}"
        )
        self._sync_execution_format()
        self._sync_tooltip()

    def clear_prepared_job(self) -> None:
        self._prepared_summary = "No job generated"
        self._sync_execution_format()
        self._sync_tooltip()

    def set_machine_status(self, machine: dict[str, Any] | None) -> None:
        del machine
        self._sync_tooltip()


class ObjectPanel(QtWidgets.QWidget):
    selectionRequested = QtCore.Signal(list)
    objectEdited = QtCore.Signal(str, dict)
    rasterVectorizeRequested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False
        self._object_kinds: dict[str, ObjectKind] = {}
        layout = _panel_layout(self)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setMinimumHeight(220)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Object", "Layer", "Visible", "Locked"])
        self.tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
            | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tree)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemChanged.connect(self._item_changed)

        self.image_group = QtWidgets.QGroupBox("Image")
        image_layout = QtWidgets.QVBoxLayout(self.image_group)
        image_layout.setContentsMargins(8, 8, 8, 8)
        self.raster_vectorize_button = QtWidgets.QPushButton(
            "Trace image to vectors\u2026"
        )
        self.raster_vectorize_button.setToolTip(
            "Convert the selected raster image into native E3 line and cubic paths"
        )
        image_layout.addWidget(self.raster_vectorize_button)
        layout.addWidget(self.image_group)
        self.raster_vectorize_button.clicked.connect(
            self._request_raster_vectorization
        )
        self.image_group.setVisible(False)
        self.raster_vectorize_button.setEnabled(False)

    def set_document(
        self,
        document: ProjectDocument,
        selected_ids: list[str] | None = None,
    ) -> None:
        selected = set(selected_ids or [])
        layers = {layer.id: layer for layer in document.layers}
        self._object_kinds = {
            scene_object.id: scene_object.kind for scene_object in document.objects
        }
        self._updating = True
        try:
            self.tree.clear()
            for scene_object in reversed(document.objects):
                layer = layers[scene_object.layer_id]
                item = QtWidgets.QTreeWidgetItem(
                    [scene_object.name, layer.name, "", ""]
                )
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, scene_object.id)
                item.setFlags(
                    item.flags()
                    | QtCore.Qt.ItemFlag.ItemIsEditable
                    | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(
                    2,
                    QtCore.Qt.CheckState.Checked
                    if scene_object.visible
                    else QtCore.Qt.CheckState.Unchecked,
                )
                item.setCheckState(
                    3,
                    QtCore.Qt.CheckState.Checked
                    if scene_object.locked
                    else QtCore.Qt.CheckState.Unchecked,
                )
                swatch = QtGui.QPixmap(12, 12)
                swatch.fill(QtGui.QColor(layer.color))
                item.setIcon(1, QtGui.QIcon(swatch))
                self.tree.addTopLevelItem(item)
                item.setSelected(scene_object.id in selected)
        finally:
            self._updating = False
        self._sync_image_action()

    def set_selection(self, object_ids: list[str]) -> None:
        wanted = set(object_ids)
        self._updating = True
        try:
            for row in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(row)
                object_id = str(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
                item.setSelected(object_id in wanted)
        finally:
            self._updating = False
        self._sync_image_action()

    def _selection_changed(self) -> None:
        if self._updating:
            return
        selected = self._selected_ids()
        self._sync_image_action(selected)
        self.selectionRequested.emit(selected)

    def _selected_ids(self) -> list[str]:
        return [
            str(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
            for item in self.tree.selectedItems()
        ]

    def _selected_image_id(self, selected_ids: list[str] | None = None) -> str | None:
        selected = self._selected_ids() if selected_ids is None else selected_ids
        if len(selected) != 1:
            return None
        object_id = selected[0]
        return (
            object_id
            if self._object_kinds.get(object_id) is ObjectKind.IMAGE
            else None
        )

    def _sync_image_action(self, selected_ids: list[str] | None = None) -> None:
        object_id = self._selected_image_id(selected_ids)
        available = object_id is not None
        self.image_group.setVisible(available)
        self.raster_vectorize_button.setEnabled(available)

    def _request_raster_vectorization(self) -> None:
        object_id = self._selected_image_id()
        if object_id is not None:
            self.rasterVectorizeRequested.emit(object_id)

    def _item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if self._updating:
            return
        object_id = str(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
        changes: dict[str, Any] = {}
        if column == 0:
            changes["name"] = item.text(0)
        elif column == 2:
            changes["visible"] = item.checkState(2) == QtCore.Qt.CheckState.Checked
        elif column == 3:
            changes["locked"] = item.checkState(3) == QtCore.Qt.CheckState.Checked
        if changes:
            self.objectEdited.emit(object_id, changes)


class MaterialPanel(QtWidgets.QWidget):
    applyPresetRequested = QtCore.Signal(object)
    notice = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(
        self,
        database: object,
        parent: QtWidgets.QWidget | None = None,
        *,
        machine_profile_id: str | None = None,
        tool_head_profile_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.database = database
        self._updating = False
        self._current_id: int | None = None
        self._machine_profile_id = machine_profile_id
        self._tool_head_profile_id = tool_head_profile_id

        layout = _panel_layout(self)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search material recipes")
        layout.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setMinimumHeight(170)
        layout.addWidget(self.list, 1)

        form = _form_layout()
        self.material_edit = QtWidgets.QLineEdit()
        self.name_edit = QtWidgets.QLineEdit()
        self.thickness_spin = MeasurementSpinBox()
        self.thickness_spin.setRange(-1.0, 1000.0)
        self.thickness_spin.setDecimals(3)
        self.thickness_spin.setSpecialValueText("Any")
        self.thickness_spin.setSuffix(" mm")
        self.mode_combo = QtWidgets.QComboBox()
        for mode in LayerMode:
            self.mode_combo.addItem(mode.value.title(), mode.value)
        self.speed_spin = MeasurementSpinBox("speed")
        self.speed_spin.setRange(1.0, 100000.0)
        self.speed_spin.setSuffix(" mm/min")
        self.power_spin = QtWidgets.QDoubleSpinBox()
        self.power_spin.setRange(0.0, 100.0)
        self.power_spin.setSuffix(" %")
        self.passes_spin = QtWidgets.QSpinBox()
        self.passes_spin.setRange(1, 999)
        self.interval_spin = MeasurementSpinBox()
        self.interval_spin.setRange(0.001, 100.0)
        self.interval_spin.setDecimals(3)
        self.interval_spin.setSuffix(" mm")
        self.scan_angle_spin = QtWidgets.QDoubleSpinBox()
        self.scan_angle_spin.setRange(-360.0, 360.0)
        self.scan_angle_spin.setDecimals(1)
        self.scan_angle_spin.setSuffix("\N{DEGREE SIGN}")
        self.overscan_spin = QtWidgets.QDoubleSpinBox()
        self.overscan_spin.setRange(0.0, 100.0)
        self.overscan_spin.setDecimals(2)
        self.overscan_spin.setSuffix(" %")
        self.air_assist_check = QtWidgets.QCheckBox("Use air assist")
        self.vector_correction_spin = QtWidgets.QDoubleSpinBox()
        self.vector_correction_spin.setRange(-100.0, 100.0)
        self.vector_correction_spin.setSuffix(" %")
        self.vector_correction_spin.setToolTip(
            "Material-specific commanded-power bias near vector direction changes"
        )
        self.raster_correction_spin = QtWidgets.QDoubleSpinBox()
        self.raster_correction_spin.setRange(-100.0, 100.0)
        self.raster_correction_spin.setSuffix(" %")
        self.raster_correction_spin.setToolTip(
            "Material-specific commanded-power bias near raster reversals"
        )
        self.recommended_color_edit = QtWidgets.QLineEdit()
        self.recommended_color_edit.setPlaceholderText("Keep current layer color")
        self.recommended_color_edit.setToolTip(
            "Optional #RRGGBB authoring color to use when this recipe is applied"
        )
        self.machine_scope_edit = QtWidgets.QLineEdit()
        self.machine_scope_edit.setPlaceholderText("Universal / no machine scope")
        self.machine_scope_edit.setToolTip(
            "Optional stable machine profile ID; exact matches only"
        )
        self.tool_scope_edit = QtWidgets.QLineEdit()
        self.tool_scope_edit.setPlaceholderText("Universal / no tool-head scope")
        self.tool_scope_edit.setToolTip(
            "Optional stable tool-head profile ID; exact matches only"
        )
        self.notes_edit = QtWidgets.QPlainTextEdit()
        self.notes_edit.setMaximumHeight(80)
        _form_row(form, "Material", self.material_edit)
        _form_row(form, "Recipe", self.name_edit)
        _form_row(form, "Thickness", self.thickness_spin)
        _form_row(form, "Mode", self.mode_combo)
        _form_row(form, "Speed", self.speed_spin)
        _form_row(form, "Power", self.power_spin)
        _form_row(form, "Passes", self.passes_spin)
        _form_row(form, "Line interval", self.interval_spin)
        _form_row(form, "Scan angle", self.scan_angle_spin)
        _form_row(form, "Overscan", self.overscan_spin)
        _form_row(form, "Air assist", self.air_assist_check)
        _form_row(form, "Vector correction", self.vector_correction_spin)
        _form_row(form, "Raster correction", self.raster_correction_spin)
        _form_row(form, "Recommended color", self.recommended_color_edit)
        _form_row(form, "Machine profile", self.machine_scope_edit)
        _form_row(form, "Tool-head profile", self.tool_scope_edit)
        _form_row(form, "Notes", self.notes_edit)
        layout.addLayout(form)

        actions = QtWidgets.QGridLayout()
        self.new_button = QtWidgets.QPushButton("New")
        self.save_button = QtWidgets.QPushButton("Save")
        self.apply_button = QtWidgets.QPushButton("Apply recipe to active layer")
        self.delete_button = QtWidgets.QPushButton("Delete")
        actions.addWidget(self.new_button, 0, 0)
        actions.addWidget(self.save_button, 0, 1)
        actions.addWidget(self.apply_button, 1, 0)
        actions.addWidget(self.delete_button, 1, 1)
        layout.addLayout(actions)

        self.search.textChanged.connect(self.refresh)
        self.list.currentItemChanged.connect(self._selection_changed)
        self.new_button.clicked.connect(self.clear_form)
        self.save_button.clicked.connect(self.save_current)
        self.apply_button.clicked.connect(self.apply_current)
        self.delete_button.clicked.connect(self.delete_current)
        self.machine_scope_edit.textChanged.connect(self._update_apply_state)
        self.tool_scope_edit.textChanged.connect(self._update_apply_state)
        self.refresh()
        self.clear_form()

    def set_profile_context(
        self,
        machine_profile_id: str | None,
        tool_head_profile_id: str | None,
    ) -> None:
        """Refresh recipe compatibility for the immutable running profiles."""

        machine_profile_id = machine_profile_id or None
        tool_head_profile_id = tool_head_profile_id or None
        if (
            machine_profile_id == self._machine_profile_id
            and tool_head_profile_id == self._tool_head_profile_id
        ):
            return
        self._machine_profile_id = machine_profile_id
        self._tool_head_profile_id = tool_head_profile_id
        self.refresh()
        self._update_apply_state()

    @staticmethod
    def _scope_text(preset: object) -> str:
        machine_profile_id = preset.machine_profile_id
        tool_head_profile_id = preset.tool_head_profile_id
        if machine_profile_id is not None:
            return f"machine {machine_profile_id} + tool {tool_head_profile_id}"
        if tool_head_profile_id is not None:
            return f"tool {tool_head_profile_id}"
        return "universal"

    def _compatibility(self, preset: object) -> object:
        return preset.compatibility(
            machine_profile_id=self._machine_profile_id,
            tool_head_profile_id=self._tool_head_profile_id,
        )

    def refresh(self, *args: Any) -> None:
        del args
        try:
            presets = self.database.list_for_profiles(
                machine_profile_id=self._machine_profile_id,
                tool_head_profile_id=self._tool_head_profile_id,
                search=self.search.text(),
            )
        except Exception as exc:
            self.error.emit(f"Could not load material recipes: {exc}")
            return
        current_id = self._current_id
        self._updating = True
        try:
            self.list.clear()
            current_row = -1
            for row, preset in enumerate(presets):
                thickness = (
                    "any thickness"
                    if preset.thickness_mm is None
                    else f"{preset.thickness_mm:g} mm"
                )
                compatibility = self._compatibility(preset)
                item = QtWidgets.QListWidgetItem(
                    f"{preset.material} · {preset.name}\n"
                    f"{thickness} · {preset.mode.value.title()} · "
                    f"{preset.speed_mm_min:g} mm/min · {preset.power_percent:g}% · "
                    f"{preset.passes} pass{'es' if preset.passes != 1 else ''}\n"
                    f"{compatibility.label} · {self._scope_text(preset)}"
                )
                item.setData(QtCore.Qt.ItemDataRole.UserRole, preset.id)
                item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, compatibility.value)
                if not compatibility.can_apply:
                    item.setForeground(QtGui.QColor("#9A9A9A"))
                item.setToolTip(
                    f"Compatibility: {compatibility.label}\n"
                    f"Scope: {self._scope_text(preset)}"
                )
                self.list.addItem(item)
                if preset.id == current_id:
                    current_row = row
            if current_row >= 0:
                self.list.setCurrentRow(current_row)
        finally:
            self._updating = False
        self._update_apply_state()

    def clear_form(self) -> None:
        self._current_id = None
        self._updating = True
        try:
            self.list.clearSelection()
            self.material_edit.setText("Material")
            self.name_edit.setText("Recipe")
            self.thickness_spin.setValue(-1.0)
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(LayerMode.LINE.value))
            self.speed_spin.setValue(2000.0)
            self.power_spin.setValue(10.0)
            self.passes_spin.setValue(1)
            self.interval_spin.setValue(0.10)
            self.scan_angle_spin.setValue(0.0)
            self.overscan_spin.setValue(2.5)
            self.air_assist_check.setChecked(False)
            self.vector_correction_spin.setValue(0.0)
            self.raster_correction_spin.setValue(0.0)
            self.recommended_color_edit.clear()
            self.machine_scope_edit.clear()
            self.tool_scope_edit.clear()
            self.notes_edit.clear()
        finally:
            self._updating = False
        self._update_apply_state()

    def _selection_changed(
        self,
        current: QtWidgets.QListWidgetItem | None,
        previous: QtWidgets.QListWidgetItem | None,
    ) -> None:
        del previous
        if self._updating or current is None:
            return
        preset_id = int(current.data(QtCore.Qt.ItemDataRole.UserRole))
        try:
            preset = self.database.get(preset_id)
        except Exception as exc:
            self.error.emit(f"Could not load material recipe: {exc}")
            return
        self._current_id = preset.id
        self._show_preset(preset)
        self._update_apply_state()

    def _show_preset(self, preset: object) -> None:
        self._updating = True
        try:
            self.material_edit.setText(preset.material)
            self.name_edit.setText(preset.name)
            self.thickness_spin.setValue(-1.0 if preset.thickness_mm is None else preset.thickness_mm)
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(preset.mode.value))
            self.speed_spin.setValue(preset.speed_mm_min)
            self.power_spin.setValue(preset.power_percent)
            self.passes_spin.setValue(preset.passes)
            self.interval_spin.setValue(preset.line_interval_mm)
            self.scan_angle_spin.setValue(preset.scan_angle_deg)
            self.overscan_spin.setValue(preset.overscan_percent)
            self.air_assist_check.setChecked(preset.air_assist)
            self.vector_correction_spin.setValue(preset.vector_power_correction)
            self.raster_correction_spin.setValue(preset.raster_power_correction)
            self.recommended_color_edit.setText(preset.recommended_color or "")
            self.machine_scope_edit.setText(preset.machine_profile_id or "")
            self.tool_scope_edit.setText(preset.tool_head_profile_id or "")
            self.notes_edit.setPlainText(preset.notes)
        finally:
            self._updating = False

    def _form_preset(self) -> object:
        from ..materials import MaterialPreset

        thickness = self.thickness_spin.value()
        return MaterialPreset(
            id=self._current_id,
            material=self.material_edit.text(),
            name=self.name_edit.text(),
            thickness_mm=None if thickness < 0 else thickness,
            mode=LayerMode(str(self.mode_combo.currentData())),
            speed_mm_min=self.speed_spin.value(),
            power_percent=self.power_spin.value(),
            passes=self.passes_spin.value(),
            line_interval_mm=self.interval_spin.value(),
            scan_angle_deg=self.scan_angle_spin.value(),
            overscan_percent=self.overscan_spin.value(),
            air_assist=self.air_assist_check.isChecked(),
            vector_power_correction=self.vector_correction_spin.value(),
            raster_power_correction=self.raster_correction_spin.value(),
            recommended_color=self.recommended_color_edit.text().strip() or None,
            machine_profile_id=self.machine_scope_edit.text().strip() or None,
            tool_head_profile_id=self.tool_scope_edit.text().strip() or None,
            notes=self.notes_edit.toPlainText(),
        )

    def _update_apply_state(self, *args: Any) -> None:
        del args
        if self._updating:
            return
        try:
            can_apply = self._compatibility(self._form_preset()).can_apply
        except (TypeError, ValueError):
            can_apply = False
        self.apply_button.setEnabled(can_apply)

    def save_current(self) -> None:
        try:
            preset = self.database.save(self._form_preset())
        except Exception as exc:
            self.error.emit(f"Could not save material recipe: {exc}")
            return
        self._current_id = preset.id
        self.refresh()
        self.notice.emit(f"Saved material recipe {preset.material} · {preset.name}")

    def apply_current(self) -> None:
        try:
            preset = self._form_preset()
        except Exception as exc:
            self.error.emit(f"Invalid material recipe: {exc}")
            return
        compatibility = self._compatibility(preset)
        if not compatibility.can_apply:
            self.error.emit(
                f"Cannot apply this material recipe: {compatibility.label} "
                "for the running machine and tool-head profiles"
            )
            self._update_apply_state()
            return
        self.applyPresetRequested.emit(preset)

    def delete_current(self) -> None:
        if self._current_id is None:
            return
        try:
            self.database.delete(self._current_id)
        except Exception as exc:
            self.error.emit(f"Could not delete material recipe: {exc}")
            return
        self.clear_form()
        self.refresh()
        self.notice.emit("Material recipe deleted")
