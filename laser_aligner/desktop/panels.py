from __future__ import annotations

from typing import Any

from ..project import LayerMode, OperationLayer, ProjectDocument, SceneObject, Transform
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _form_row(layout: QtWidgets.QFormLayout, label: str, widget: QtWidgets.QWidget) -> None:
    layout.addRow(label, widget)


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

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.layer_list = QtWidgets.QListWidget()
        self.layer_list.setAlternatingRowColors(True)
        self.layer_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        layout.addWidget(self.layer_list, 1)

        button_row = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("Add")
        self.remove_button = QtWidgets.QPushButton("Remove")
        self.up_button = QtWidgets.QPushButton("Up")
        self.down_button = QtWidgets.QPushButton("Down")
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.remove_button)
        button_row.addWidget(self.up_button)
        button_row.addWidget(self.down_button)
        layout.addLayout(button_row)

        form = QtWidgets.QFormLayout()
        self.name_edit = QtWidgets.QLineEdit()
        self.mode_combo = QtWidgets.QComboBox()
        for mode in LayerMode:
            self.mode_combo.addItem(mode.value.title(), mode.value)
        self.speed_spin = QtWidgets.QDoubleSpinBox()
        self.speed_spin.setRange(1.0, 100000.0)
        self.speed_spin.setDecimals(1)
        self.speed_spin.setSuffix(" mm/min")
        self.power_spin = QtWidgets.QDoubleSpinBox()
        self.power_spin.setRange(0.0, 100.0)
        self.power_spin.setDecimals(1)
        self.power_spin.setSuffix(" %")
        self.passes_spin = QtWidgets.QSpinBox()
        self.passes_spin.setRange(1, 999)
        self.output_check = QtWidgets.QCheckBox("Output enabled")
        self.visible_check = QtWidgets.QCheckBox("Visible")
        _form_row(form, "Name", self.name_edit)
        _form_row(form, "Mode", self.mode_combo)
        _form_row(form, "Speed", self.speed_spin)
        _form_row(form, "Power", self.power_spin)
        _form_row(form, "Passes", self.passes_spin)
        form.addRow(self.output_check)
        form.addRow(self.visible_check)
        layout.addLayout(form)

        self.layer_list.currentItemChanged.connect(self._selection_changed)
        self.add_button.clicked.connect(self.addLayerRequested)
        self.remove_button.clicked.connect(self._remove_clicked)
        self.up_button.clicked.connect(lambda: self._move_clicked(-1))
        self.down_button.clicked.connect(lambda: self._move_clicked(1))
        self.name_edit.editingFinished.connect(self._emit_edit)
        self.mode_combo.currentIndexChanged.connect(self._emit_edit)
        self.speed_spin.valueChanged.connect(self._emit_edit)
        self.power_spin.valueChanged.connect(self._emit_edit)
        self.passes_spin.valueChanged.connect(self._emit_edit)
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
                item = QtWidgets.QListWidgetItem(layer.name)
                item.setData(QtCore.Qt.ItemDataRole.UserRole, layer.id)
                swatch = QtGui.QPixmap(14, 14)
                swatch.fill(QtGui.QColor(layer.color))
                item.setIcon(QtGui.QIcon(swatch))
                if not layer.visible:
                    item.setForeground(QtGui.QColor("#65727C"))
                self.layer_list.addItem(item)
                if layer.id == active_layer_id:
                    selected_row = row
            self.layer_list.setCurrentRow(selected_row)
            self._show_layer(document.layers[selected_row])
        finally:
            self._updating = False

    def current_layer_id(self) -> str | None:
        item = self.layer_list.currentItem()
        return None if item is None else str(item.data(QtCore.Qt.ItemDataRole.UserRole))

    def _selection_changed(
        self,
        current: QtWidgets.QListWidgetItem | None,
        previous: QtWidgets.QListWidgetItem | None,
    ) -> None:
        del previous
        if self._updating or current is None or self._document is None:
            return
        layer_id = str(current.data(QtCore.Qt.ItemDataRole.UserRole))
        self._show_layer(self._document.get_layer(layer_id))
        self.activeLayerChanged.emit(layer_id)

    def _show_layer(self, layer: OperationLayer) -> None:
        self._updating = True
        try:
            self.name_edit.setText(layer.name)
            self.mode_combo.setCurrentIndex(
                max(0, self.mode_combo.findData(layer.mode.value))
            )
            self.speed_spin.setValue(layer.speed_mm_min)
            self.power_spin.setValue(layer.power_percent)
            self.passes_spin.setValue(layer.passes)
            self.output_check.setChecked(layer.output_enabled)
            self.visible_check.setChecked(layer.visible)
        finally:
            self._updating = False

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
                "output_enabled": self.output_check.isChecked(),
                "visible": self.visible_check.isChecked(),
            },
        )

    def _remove_clicked(self) -> None:
        layer_id = self.current_layer_id()
        if layer_id is not None:
            self.removeLayerRequested.emit(layer_id)

    def _move_clicked(self, delta: int) -> None:
        layer_id = self.current_layer_id()
        if layer_id is not None:
            self.moveLayerRequested.emit(layer_id, int(delta))


class TransformPanel(QtWidgets.QWidget):
    transformEdited = QtCore.Signal(str, object)
    assignLayerRequested = QtCore.Signal(list, str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._object_id: str | None = None
        self._selected_ids: list[str] = []
        self._updating = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.summary = QtWidgets.QLabel("No object selected")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        form = QtWidgets.QFormLayout()
        self.x_spin = self._spin(-10000.0, 10000.0, " mm")
        self.y_spin = self._spin(-10000.0, 10000.0, " mm")
        self.width_spin = self._spin(0.001, 10000.0, " mm")
        self.height_spin = self._spin(0.001, 10000.0, " mm")
        self.rotation_spin = self._spin(-360.0, 360.0, "°")
        self.mirror_x = QtWidgets.QCheckBox("Mirror horizontally")
        self.mirror_y = QtWidgets.QCheckBox("Mirror vertically")
        _form_row(form, "Center X", self.x_spin)
        _form_row(form, "Center Y", self.y_spin)
        _form_row(form, "Width", self.width_spin)
        _form_row(form, "Height", self.height_spin)
        _form_row(form, "Rotation", self.rotation_spin)
        form.addRow(self.mirror_x)
        form.addRow(self.mirror_y)
        layout.addLayout(form)

        self.layer_combo = QtWidgets.QComboBox()
        layout.addWidget(QtWidgets.QLabel("Assign selected objects to layer"))
        layout.addWidget(self.layer_combo)
        layout.addStretch(1)

        for widget in (
            self.x_spin,
            self.y_spin,
            self.width_spin,
            self.height_spin,
            self.rotation_spin,
        ):
            widget.editingFinished.connect(self._emit_transform)
        self.mirror_x.toggled.connect(self._emit_transform)
        self.mirror_y.toggled.connect(self._emit_transform)
        self.layer_combo.activated.connect(self._assign_layer)

    @staticmethod
    def _spin(minimum: float, maximum: float, suffix: str) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
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

    def set_selection(
        self,
        objects: list[SceneObject],
        document: ProjectDocument,
    ) -> None:
        self._selected_ids = [item.id for item in objects]
        self._object_id = objects[0].id if len(objects) == 1 else None
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
            if not objects:
                self.summary.setText("No object selected")
                return
            if len(objects) > 1:
                self.summary.setText(f"{len(objects)} objects selected")
                layer_ids = {item.layer_id for item in objects}
                if len(layer_ids) == 1:
                    index = self.layer_combo.findData(next(iter(layer_ids)))
                    if index >= 0:
                        self.layer_combo.setCurrentIndex(index)
                return
            item = objects[0]
            self.summary.setText(f"{item.name} · {item.kind.value}")
            transform = item.transform
            self.x_spin.setValue(transform.x_mm)
            self.y_spin.setValue(transform.y_mm)
            self.width_spin.setValue(transform.width_mm)
            self.height_spin.setValue(transform.height_mm)
            self.rotation_spin.setValue(transform.rotation_deg)
            self.mirror_x.setChecked(transform.mirror_x)
            self.mirror_y.setChecked(transform.mirror_y)
            index = self.layer_combo.findData(item.layer_id)
            if index >= 0:
                self.layer_combo.setCurrentIndex(index)
        finally:
            self._updating = False

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
        self.transformEdited.emit(self._object_id, transform)

    def _assign_layer(self, index: int) -> None:
        if self._updating or not self._selected_ids:
            return
        layer_id = str(self.layer_combo.itemData(index))
        self.assignLayerRequested.emit(self._selected_ids, layer_id)


class CameraPanel(QtWidgets.QWidget):
    refreshRequested = QtCore.Signal()
    captureRequested = QtCore.Signal()
    lensCalibrationRequested = QtCore.Signal()
    bedCalibrationRequested = QtCore.Signal()
    opacityChanged = QtCore.Signal(float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.state_label = QtWidgets.QLabel("Camera not started")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        self.refresh_button = QtWidgets.QPushButton("Refresh corrected bed image")
        self.capture_button = QtWidgets.QPushButton("Capture still")
        self.lens_button = QtWidgets.QPushButton("Lens calibration…")
        self.bed_button = QtWidgets.QPushButton("Bed alignment…")
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.capture_button)
        layout.addWidget(self.lens_button)
        layout.addWidget(self.bed_button)

        layout.addWidget(QtWidgets.QLabel("Camera overlay opacity"))
        self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(60)
        layout.addWidget(self.opacity_slider)
        layout.addStretch(1)

        self.refresh_button.clicked.connect(self.refreshRequested)
        self.capture_button.clicked.connect(self.captureRequested)
        self.lens_button.clicked.connect(self.lensCalibrationRequested)
        self.bed_button.clicked.connect(self.bedCalibrationRequested)
        self.opacity_slider.valueChanged.connect(
            lambda value: self.opacityChanged.emit(value / 100.0)
        )

    def set_status(self, status: dict[str, Any] | None) -> None:
        if not status:
            self.state_label.setText("Camera unavailable")
            return
        connected = status.get("connected", False)
        if connected:
            self.state_label.setText(
                f"Online · {status.get('width', 0)} × {status.get('height', 0)} · "
                f"{status.get('fps', 0)} fps\n{status.get('device', '')}"
            )
        else:
            self.state_label.setText(
                f"Offline\n{status.get('last_error') or status.get('device', '')}"
            )


class MachinePanel(QtWidgets.QWidget):
    connectRequested = QtCore.Signal()
    disconnectRequested = QtCore.Signal()
    parkRequested = QtCore.Signal()
    stopRequested = QtCore.Signal()
    jogRequested = QtCore.Signal(float, float, float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.state_label = QtWidgets.QLabel("Disconnected")
        self.state_label.setWordWrap(True)
        layout.addWidget(self.state_label)

        row = QtWidgets.QHBoxLayout()
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.disconnect_button = QtWidgets.QPushButton("Disconnect")
        row.addWidget(self.connect_button)
        row.addWidget(self.disconnect_button)
        layout.addLayout(row)

        self.park_button = QtWidgets.QPushButton("Home and park at camera pose")
        layout.addWidget(self.park_button)

        jog_group = QtWidgets.QGroupBox("Jog")
        jog_layout = QtWidgets.QGridLayout(jog_group)
        self.jog_step = QtWidgets.QComboBox()
        for value in (0.1, 1.0, 5.0, 10.0, 50.0):
            self.jog_step.addItem(f"{value:g} mm", value)
        self.jog_speed = QtWidgets.QDoubleSpinBox()
        self.jog_speed.setRange(1.0, 10000.0)
        self.jog_speed.setValue(2000.0)
        self.jog_speed.setSuffix(" mm/min")
        up = QtWidgets.QPushButton("Y+")
        down = QtWidgets.QPushButton("Y−")
        left = QtWidgets.QPushButton("X−")
        right = QtWidgets.QPushButton("X+")
        jog_layout.addWidget(up, 0, 1)
        jog_layout.addWidget(left, 1, 0)
        jog_layout.addWidget(right, 1, 2)
        jog_layout.addWidget(down, 2, 1)
        jog_layout.addWidget(QtWidgets.QLabel("Step"), 3, 0)
        jog_layout.addWidget(self.jog_step, 3, 1, 1, 2)
        jog_layout.addWidget(QtWidgets.QLabel("Speed"), 4, 0)
        jog_layout.addWidget(self.jog_speed, 4, 1, 1, 2)
        layout.addWidget(jog_group)

        self.stop_button = QtWidgets.QPushButton("Software stop / laser off")
        self.stop_button.setObjectName("dangerButton")
        layout.addWidget(self.stop_button)
        layout.addStretch(1)

        self.connect_button.clicked.connect(self.connectRequested)
        self.disconnect_button.clicked.connect(self.disconnectRequested)
        self.park_button.clicked.connect(self.parkRequested)
        self.stop_button.clicked.connect(self.stopRequested)
        up.clicked.connect(lambda: self._jog(0.0, 1.0))
        down.clicked.connect(lambda: self._jog(0.0, -1.0))
        left.clicked.connect(lambda: self._jog(-1.0, 0.0))
        right.clicked.connect(lambda: self._jog(1.0, 0.0))

    def _jog(self, x_direction: float, y_direction: float) -> None:
        step = float(self.jog_step.currentData())
        self.jogRequested.emit(
            x_direction * step,
            y_direction * step,
            self.jog_speed.value(),
        )

    def set_status(self, status: dict[str, Any] | None) -> None:
        if not status:
            self.state_label.setText("Controller unavailable")
            return
        connected = bool(status.get("connected", False))
        armed = bool(status.get("armed", False))
        job = status.get("job", {})
        state = "RUNNING" if job.get("running") else ("ARMED" if armed else "SAFE")
        self.state_label.setText(
            f"{'Connected' if connected else 'Disconnected'} · "
            f"{status.get('protocol', 'unknown')} · {state}\n"
            f"Motion {'enabled' if status.get('allow_motion') else 'blocked'}"
        )
        self.connect_button.setEnabled(not connected)
        self.disconnect_button.setEnabled(connected)
        self.park_button.setEnabled(connected and bool(status.get("allow_motion")))


class ConsolePanel(QtWidgets.QWidget):
    commandSubmitted = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
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


class JobPanel(QtWidgets.QWidget):
    frameRequested = QtCore.Signal()
    generateRequested = QtCore.Signal()
    startRequested = QtCore.Signal()
    pauseRequested = QtCore.Signal()
    stopRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.summary = QtWidgets.QLabel("No job generated")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        layout.addWidget(self.progress)

        self.generate_button = QtWidgets.QPushButton("Generate toolpath")
        self.frame_button = QtWidgets.QPushButton("Frame bounds")
        self.start_button = QtWidgets.QPushButton("Start job")
        self.pause_button = QtWidgets.QPushButton("Pause / resume")
        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setObjectName("dangerButton")
        for widget in (
            self.generate_button,
            self.frame_button,
            self.start_button,
            self.pause_button,
            self.stop_button,
        ):
            layout.addWidget(widget)
        layout.addStretch(1)

        self.generate_button.clicked.connect(self.generateRequested)
        self.frame_button.clicked.connect(self.frameRequested)
        self.start_button.clicked.connect(self.startRequested)
        self.pause_button.clicked.connect(self.pauseRequested)
        self.stop_button.clicked.connect(self.stopRequested)

    def set_job_status(self, job: dict[str, Any] | None) -> None:
        job = job or {}
        running = bool(job.get("running", False))
        total = int(job.get("total_lines", 0) or 0)
        completed = int(job.get("completed_lines", 0) or 0)
        progress = 0.0 if total <= 0 else completed / total
        self.progress.setValue(int(round(progress * 1000)))
        if running:
            self.summary.setText(
                f"Running {job.get('name', 'job')} · {completed}/{total} lines"
            )
        elif job.get("error"):
            self.summary.setText(f"Stopped: {job['error']}")
        elif total:
            self.summary.setText(f"Idle · last job {completed}/{total} lines")
        else:
            self.summary.setText("No active controller job")


class ObjectPanel(QtWidgets.QWidget):
    selectionRequested = QtCore.Signal(list)
    objectEdited = QtCore.Signal(str, dict)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.tree = QtWidgets.QTreeWidget()
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

    def set_document(
        self,
        document: ProjectDocument,
        selected_ids: list[str] | None = None,
    ) -> None:
        selected = set(selected_ids or [])
        layers = {layer.id: layer for layer in document.layers}
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

    def _selection_changed(self) -> None:
        if self._updating:
            return
        self.selectionRequested.emit(
            [
                str(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
                for item in self.tree.selectedItems()
            ]
        )

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

    def __init__(self, database: object, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.database = database
        self._updating = False
        self._current_id: int | None = None

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search material presets")
        layout.addWidget(self.search)
        self.list = QtWidgets.QListWidget()
        self.list.setAlternatingRowColors(True)
        layout.addWidget(self.list, 1)

        form = QtWidgets.QFormLayout()
        self.material_edit = QtWidgets.QLineEdit()
        self.name_edit = QtWidgets.QLineEdit()
        self.thickness_spin = QtWidgets.QDoubleSpinBox()
        self.thickness_spin.setRange(-1.0, 1000.0)
        self.thickness_spin.setDecimals(3)
        self.thickness_spin.setSpecialValueText("Any")
        self.thickness_spin.setSuffix(" mm")
        self.mode_combo = QtWidgets.QComboBox()
        for mode in LayerMode:
            self.mode_combo.addItem(mode.value.title(), mode.value)
        self.speed_spin = QtWidgets.QDoubleSpinBox()
        self.speed_spin.setRange(1.0, 100000.0)
        self.speed_spin.setSuffix(" mm/min")
        self.power_spin = QtWidgets.QDoubleSpinBox()
        self.power_spin.setRange(0.0, 100.0)
        self.power_spin.setSuffix(" %")
        self.passes_spin = QtWidgets.QSpinBox()
        self.passes_spin.setRange(1, 999)
        self.interval_spin = QtWidgets.QDoubleSpinBox()
        self.interval_spin.setRange(0.001, 100.0)
        self.interval_spin.setDecimals(3)
        self.interval_spin.setSuffix(" mm")
        self.notes_edit = QtWidgets.QPlainTextEdit()
        self.notes_edit.setMaximumHeight(80)
        _form_row(form, "Material", self.material_edit)
        _form_row(form, "Preset", self.name_edit)
        _form_row(form, "Thickness", self.thickness_spin)
        _form_row(form, "Mode", self.mode_combo)
        _form_row(form, "Speed", self.speed_spin)
        _form_row(form, "Power", self.power_spin)
        _form_row(form, "Passes", self.passes_spin)
        _form_row(form, "Line interval", self.interval_spin)
        _form_row(form, "Notes", self.notes_edit)
        layout.addLayout(form)

        row = QtWidgets.QHBoxLayout()
        self.new_button = QtWidgets.QPushButton("New")
        self.save_button = QtWidgets.QPushButton("Save")
        self.apply_button = QtWidgets.QPushButton("Apply to active layer")
        self.delete_button = QtWidgets.QPushButton("Delete")
        row.addWidget(self.new_button)
        row.addWidget(self.save_button)
        row.addWidget(self.apply_button)
        row.addWidget(self.delete_button)
        layout.addLayout(row)

        self.search.textChanged.connect(self.refresh)
        self.list.currentItemChanged.connect(self._selection_changed)
        self.new_button.clicked.connect(self.clear_form)
        self.save_button.clicked.connect(self.save_current)
        self.apply_button.clicked.connect(self.apply_current)
        self.delete_button.clicked.connect(self.delete_current)
        self.refresh()
        self.clear_form()

    def refresh(self, *args: Any) -> None:
        del args
        try:
            presets = self.database.list(self.search.text())
        except Exception as exc:
            self.error.emit(f"Could not load material presets: {exc}")
            return
        current_id = self._current_id
        self._updating = True
        try:
            self.list.clear()
            current_row = -1
            for row, preset in enumerate(presets):
                thickness = "any thickness" if preset.thickness_mm is None else f"{preset.thickness_mm:g} mm"
                item = QtWidgets.QListWidgetItem(
                    f"{preset.material} · {preset.name}\n"
                    f"{thickness} · {preset.speed_mm_min:g} mm/min · {preset.power_percent:g}%"
                )
                item.setData(QtCore.Qt.ItemDataRole.UserRole, preset.id)
                self.list.addItem(item)
                if preset.id == current_id:
                    current_row = row
            if current_row >= 0:
                self.list.setCurrentRow(current_row)
        finally:
            self._updating = False

    def clear_form(self) -> None:
        self._current_id = None
        self._updating = True
        try:
            self.list.clearSelection()
            self.material_edit.setText("Material")
            self.name_edit.setText("Preset")
            self.thickness_spin.setValue(-1.0)
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(LayerMode.LINE.value))
            self.speed_spin.setValue(2000.0)
            self.power_spin.setValue(10.0)
            self.passes_spin.setValue(1)
            self.interval_spin.setValue(0.10)
            self.notes_edit.clear()
        finally:
            self._updating = False

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
            self.error.emit(f"Could not load material preset: {exc}")
            return
        self._current_id = preset.id
        self._show_preset(preset)

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
            notes=self.notes_edit.toPlainText(),
        )

    def save_current(self) -> None:
        try:
            preset = self.database.save(self._form_preset())
        except Exception as exc:
            self.error.emit(f"Could not save material preset: {exc}")
            return
        self._current_id = preset.id
        self.refresh()
        self.notice.emit(f"Saved material preset {preset.material} · {preset.name}")

    def apply_current(self) -> None:
        try:
            preset = self._form_preset()
        except Exception as exc:
            self.error.emit(f"Invalid material preset: {exc}")
            return
        self.applyPresetRequested.emit(preset)

    def delete_current(self) -> None:
        if self._current_id is None:
            return
        try:
            self.database.delete(self._current_id)
        except Exception as exc:
            self.error.emit(f"Could not delete material preset: {exc}")
            return
        self.clear_form()
        self.refresh()
        self.notice.emit("Material preset deleted")
