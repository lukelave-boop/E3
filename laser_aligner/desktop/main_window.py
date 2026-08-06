from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import CoreRuntime
from ..geometry.svg import parse_svg
from ..materials import MaterialDatabase, MaterialPreset
from ..project import (
    AddLayerCommand,
    AddObjectCommand,
    Alignment,
    AssignLayerCommand,
    Bounds,
    CommandStack,
    DuplicateObjectsCommand,
    GroupObjectsCommand,
    OperationLayer,
    ProjectDocument,
    RemoveLayerCommand,
    ReorderLayersCommand,
    ReorderObjectsCommand,
    RemoveObjectsCommand,
    SceneObject,
    Transform,
    UngroupObjectsCommand,
    UpdateLayerCommand,
    UpdateObjectPropertiesCommand,
    UpdateTransformCommand,
    UpdateTransformsCommand,
    aligned_transforms,
    autosave_is_newer,
    clear_autosave,
    distributed_transforms,
    generate_project_frame,
    generate_project_gcode,
    load_project,
    autosave_path,
    save_autosave,
    save_project,
)
from .controller import DesktopController
from .panels import (
    CameraPanel,
    ConsolePanel,
    JobPanel,
    LayerPanel,
    MachinePanel,
    MaterialPanel,
    ObjectPanel,
    TransformPanel,
)
from .qt import require_qt
from .workspace import WorkspaceFrame, WorkspaceView

QtCore, QtGui, QtWidgets = require_qt()


class LayerPaletteBar(QtWidgets.QWidget):
    layerSelected = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QtWidgets.QHBoxLayout(self)
        self._layout.setContentsMargins(5, 2, 5, 2)
        self._layout.setSpacing(3)
        self._buttons: dict[str, QtWidgets.QToolButton] = {}
        self._layout.addStretch(1)

    def set_layers(self, layers: list[OperationLayer], active_layer_id: str) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()
        for layer in layers:
            button = QtWidgets.QToolButton()
            button.setCheckable(True)
            button.setChecked(layer.id == active_layer_id)
            button.setToolTip(
                f"{layer.name}\n{layer.mode.value} · {layer.speed_mm_min:g} mm/min · "
                f"{layer.power_percent:g}%"
            )
            button.setFixedSize(28, 24)
            button.setStyleSheet(
                "QToolButton {"
                f"background: {layer.color}; border: 2px solid "
                f"{'#F5F8FA' if layer.id == active_layer_id else '#26343E'};"
                "border-radius: 4px;"
                "}"
            )
            button.clicked.connect(lambda checked=False, layer_id=layer.id: self.layerSelected.emit(layer_id))
            self._layout.addWidget(button)
            self._buttons[layer.id] = button
        self._layout.addStretch(1)


class E3MainWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        runtime: CoreRuntime,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        window_flags = self.windowFlags()
        window_flags &= ~QtCore.Qt.WindowType.FramelessWindowHint
        window_flags |= (
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.WindowTitleHint
            | QtCore.Qt.WindowType.WindowSystemMenuHint
            | QtCore.Qt.WindowType.WindowMinimizeButtonHint
            | QtCore.Qt.WindowType.WindowMaximizeButtonHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )
        self.setWindowFlags(window_flags)
        self.runtime = runtime
        self.controller = DesktopController(runtime, self)
        self.material_database = MaterialDatabase()
        self.document = self._new_document()
        self.history = CommandStack(max_depth=300)
        self.project_path: Path | None = None
        self.active_layer_id = self.document.active_layer_id
        self.last_job: Any | None = None
        self.last_job_name = ""
        self.last_job_powered = False
        self._busy = False
        self._closing = False
        self._expanding_group_selection = False

        self.setWindowTitle("E3 Positioning System")
        icon_path = Path(__file__).resolve().parent / "assets" / "e3-positioning-system.svg"
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.setMinimumSize(900, 600)
        self.resize(1180, 720)
        self.setDockOptions(
            QtWidgets.QMainWindow.DockOption.AllowNestedDocks
            | QtWidgets.QMainWindow.DockOption.AllowTabbedDocks
            | QtWidgets.QMainWindow.DockOption.AnimatedDocks
        )

        self.workspace = WorkspaceView(self.document.work_area)
        self.workspace_frame = WorkspaceFrame(self.workspace)
        self.setCentralWidget(self.workspace_frame)

        self._create_actions()
        self._create_menus()
        self._create_toolbars()
        self._create_docks()
        self._create_status_bar()
        self._connect_signals()

        self.history.add_listener(self._history_changed)
        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        self._restore_window_state()
        QtCore.QTimer.singleShot(0, self._ensure_window_visible)
        self._refresh_document()
        self.controller.start()

    def _new_document(self) -> ProjectDocument:
        area = self.runtime.settings.machine.work_area
        return ProjectDocument.new(
            work_area=Bounds(area.x_min, area.y_min, area.x_max, area.y_max)
        )

    def _create_actions(self) -> None:
        style = self.style()
        self.actions: dict[str, QtGui.QAction] = {}

        def action(
            key: str,
            text: str,
            shortcut: str | None = None,
            icon: QtWidgets.QStyle.StandardPixmap | None = None,
            checkable: bool = False,
        ) -> QtGui.QAction:
            item = QtGui.QAction(
                style.standardIcon(icon) if icon is not None else QtGui.QIcon(),
                text,
                self,
            )
            if shortcut:
                item.setShortcut(QtGui.QKeySequence(shortcut))
            item.setCheckable(checkable)
            self.actions[key] = item
            return item

        action("new", "New project", "Ctrl+N", QtWidgets.QStyle.StandardPixmap.SP_FileIcon)
        action("open", "Open project…", "Ctrl+O", QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton)
        action("save", "Save project", "Ctrl+S", QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton)
        action("save_as", "Save project as…", "Ctrl+Shift+S")
        action("import_svg", "Import SVG…", "Ctrl+I")
        action("quit", "Quit", "Ctrl+Q")
        action("undo", "Undo", "Ctrl+Z", QtWidgets.QStyle.StandardPixmap.SP_ArrowBack)
        action("redo", "Redo", "Ctrl+Shift+Z", QtWidgets.QStyle.StandardPixmap.SP_ArrowForward)
        action("delete", "Delete", "Delete", QtWidgets.QStyle.StandardPixmap.SP_TrashIcon)
        action("duplicate", "Duplicate", "Ctrl+D")
        action("select_all", "Select all", "Ctrl+A")
        action("group", "Group", "Ctrl+G")
        action("ungroup", "Ungroup", "Ctrl+Shift+G")
        action("align_left", "Align left")
        action("align_center_x", "Align horizontal centers")
        action("align_right", "Align right")
        action("align_bottom", "Align bottom")
        action("align_center_y", "Align vertical centers")
        action("align_top", "Align top")
        action("distribute_h", "Distribute horizontally")
        action("distribute_v", "Distribute vertically")
        action("bring_front", "Bring to front", "Ctrl+Shift+]")
        action("raise", "Raise one step", "Ctrl+]")
        action("lower", "Lower one step", "Ctrl+[")
        action("send_back", "Send to back", "Ctrl+Shift+[")
        action("fit", "Fit work area", "F")
        action("snap", "Snap to grid", "Ctrl+Shift+G", checkable=True)
        self.actions["snap"].setChecked(True)
        action("rectangle", "Rectangle", "R", checkable=True)
        action("ellipse", "Ellipse", "E", checkable=True)
        action("line", "Line", "L", checkable=True)
        action("text", "Text", "T", checkable=True)
        action("refresh_camera", "Refresh camera", "F5")
        action("generate", "Generate toolpath", "Ctrl+G")
        action("frame", "Generate dry frame", "Ctrl+Shift+F")
        action("run", "Run current job", "Ctrl+Enter")
        action("stop", "Software stop / laser off", "Esc")
        action("minimize_window", "Minimize window", "Ctrl+M")
        action("maximize_window", "Maximize / restore window", "Ctrl+Shift+M")
        action("reset_window_size", "Reset window size")
        action("about", "About E3 Positioning System")

        self.actions["undo"].setEnabled(False)
        self.actions["redo"].setEnabled(False)

        self.actions["new"].triggered.connect(self.new_project)
        self.actions["open"].triggered.connect(self.open_project)
        self.actions["save"].triggered.connect(self.save_project)
        self.actions["save_as"].triggered.connect(lambda: self.save_project(save_as=True))
        self.actions["import_svg"].triggered.connect(self.import_svg)
        self.actions["quit"].triggered.connect(self.close)
        self.actions["undo"].triggered.connect(self.history.undo)
        self.actions["redo"].triggered.connect(self.history.redo)
        self.actions["delete"].triggered.connect(self.delete_selection)
        self.actions["duplicate"].triggered.connect(self.duplicate_selection)
        self.actions["select_all"].triggered.connect(
            lambda: self.workspace.select_objects([item.id for item in self.document.objects])
        )
        self.actions["group"].triggered.connect(self.group_selection)
        self.actions["ungroup"].triggered.connect(self.ungroup_selection)
        self.actions["align_left"].triggered.connect(lambda: self.align_selection(Alignment.LEFT))
        self.actions["align_center_x"].triggered.connect(lambda: self.align_selection(Alignment.CENTER_X))
        self.actions["align_right"].triggered.connect(lambda: self.align_selection(Alignment.RIGHT))
        self.actions["align_bottom"].triggered.connect(lambda: self.align_selection(Alignment.BOTTOM))
        self.actions["align_center_y"].triggered.connect(lambda: self.align_selection(Alignment.CENTER_Y))
        self.actions["align_top"].triggered.connect(lambda: self.align_selection(Alignment.TOP))
        self.actions["distribute_h"].triggered.connect(lambda: self.distribute_selection(horizontal=True))
        self.actions["distribute_v"].triggered.connect(lambda: self.distribute_selection(horizontal=False))
        self.actions["bring_front"].triggered.connect(lambda: self.reorder_selection("front"))
        self.actions["raise"].triggered.connect(lambda: self.reorder_selection("raise"))
        self.actions["lower"].triggered.connect(lambda: self.reorder_selection("lower"))
        self.actions["send_back"].triggered.connect(lambda: self.reorder_selection("back"))
        self.actions["fit"].triggered.connect(self.workspace.fit_work_area)
        self.actions["snap"].toggled.connect(self.workspace.set_snap_enabled)
        self.actions["rectangle"].triggered.connect(self.add_rectangle)
        self.actions["ellipse"].triggered.connect(self.add_ellipse)
        self.actions["line"].triggered.connect(self.add_line)
        self.actions["text"].triggered.connect(self.add_text)
        self.actions["refresh_camera"].triggered.connect(self.controller.refresh_camera_image)
        self.actions["generate"].triggered.connect(self.generate_toolpath)
        self.actions["frame"].triggered.connect(self.generate_frame)
        self.actions["run"].triggered.connect(self.run_current_job)
        self.actions["stop"].triggered.connect(self.controller.emergency_stop)
        self.actions["minimize_window"].triggered.connect(self.showMinimized)
        self.actions["maximize_window"].triggered.connect(self._toggle_maximized)
        self.actions["reset_window_size"].triggered.connect(self._reset_window_size)
        self.actions["about"].triggered.connect(self.show_about)

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        for key in ("new", "open", "save", "save_as", "import_svg"):
            file_menu.addAction(self.actions[key])
        file_menu.addSeparator()
        file_menu.addAction(self.actions["quit"])

        edit_menu = self.menuBar().addMenu("&Edit")
        for key in ("undo", "redo", "duplicate", "delete", "select_all"):
            edit_menu.addAction(self.actions[key])
        edit_menu.addSeparator()
        edit_menu.addAction(self.actions["group"])
        edit_menu.addAction(self.actions["ungroup"])

        align_menu = self.menuBar().addMenu("&Arrange")
        for key in (
            "align_left",
            "align_center_x",
            "align_right",
            "align_bottom",
            "align_center_y",
            "align_top",
        ):
            align_menu.addAction(self.actions[key])
        align_menu.addSeparator()
        align_menu.addAction(self.actions["distribute_h"])
        align_menu.addAction(self.actions["distribute_v"])
        align_menu.addSeparator()
        for key in ("bring_front", "raise", "lower", "send_back"):
            align_menu.addAction(self.actions[key])

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.actions["fit"])
        view_menu.addAction(self.actions["snap"])
        view_menu.addSeparator()
        view_menu.addAction(self.actions["minimize_window"])
        view_menu.addAction(self.actions["maximize_window"])
        view_menu.addAction(self.actions["reset_window_size"])

        create_menu = self.menuBar().addMenu("&Create")
        for key in ("rectangle", "ellipse", "line", "text"):
            create_menu.addAction(self.actions[key])

        laser_menu = self.menuBar().addMenu("&Laser")
        for key in ("generate", "frame", "run", "stop"):
            laser_menu.addAction(self.actions[key])

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self.actions["about"])

    def _create_toolbars(self) -> None:
        file_toolbar = self.addToolBar("File")
        file_toolbar.setObjectName("fileToolbar")
        for key in ("new", "open", "save", "import_svg"):
            file_toolbar.addAction(self.actions[key])

        edit_toolbar = self.addToolBar("Edit")
        edit_toolbar.setObjectName("editToolbar")
        for key in ("undo", "redo", "duplicate", "delete"):
            edit_toolbar.addAction(self.actions[key])

        tools = QtWidgets.QToolBar("Drawing tools", self)
        tools.setObjectName("drawingToolbar")
        tools.setOrientation(QtCore.Qt.Orientation.Vertical)
        self.addToolBar(QtCore.Qt.ToolBarArea.LeftToolBarArea, tools)
        tool_group = QtGui.QActionGroup(self)
        tool_group.setExclusive(False)
        for key in ("rectangle", "ellipse", "line", "text"):
            tools.addAction(self.actions[key])
            tool_group.addAction(self.actions[key])
        tools.addSeparator()
        tools.addAction(self.actions["fit"])
        tools.addAction(self.actions["snap"])
        self.snap_combo = QtWidgets.QComboBox()
        self.snap_combo.setToolTip("Grid snap spacing")
        for step in (0.1, 0.5, 1.0, 2.0, 5.0, 10.0):
            self.snap_combo.addItem(f"{step:g} mm", step)
        self.snap_combo.setCurrentIndex(self.snap_combo.findData(1.0))
        self.snap_combo.currentIndexChanged.connect(
            lambda index: self.workspace.set_snap_step(
                float(self.snap_combo.itemData(index))
            )
        )
        tools.addWidget(self.snap_combo)

        align_toolbar = self.addToolBar("Align")
        align_toolbar.setObjectName("alignToolbar")
        for key in (
            "align_left",
            "align_center_x",
            "align_right",
            "align_bottom",
            "align_center_y",
            "align_top",
            "distribute_h",
            "distribute_v",
            "bring_front",
            "raise",
            "lower",
            "send_back",
        ):
            align_toolbar.addAction(self.actions[key])

        job_toolbar = self.addToolBar("Job")
        job_toolbar.setObjectName("jobToolbar")
        for key in ("generate", "frame", "run", "stop"):
            job_toolbar.addAction(self.actions[key])

        self.palette = LayerPaletteBar()
        palette_toolbar = QtWidgets.QToolBar("Layer palette", self)
        palette_toolbar.setObjectName("layerPaletteToolbar")
        palette_toolbar.addWidget(self.palette)
        self.addToolBar(QtCore.Qt.ToolBarArea.BottomToolBarArea, palette_toolbar)

    def _dock(
        self,
        title: str,
        object_name: str,
        widget: QtWidgets.QWidget,
        area: QtCore.Qt.DockWidgetArea,
    ) -> QtWidgets.QDockWidget:
        dock = QtWidgets.QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setWidget(widget)
        dock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
            | QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(area, dock)
        return dock

    def _create_docks(self) -> None:
        self.layer_panel = LayerPanel()
        self.object_panel = ObjectPanel()
        self.transform_panel = TransformPanel()
        self.camera_panel = CameraPanel()
        self.machine_panel = MachinePanel()
        self.material_panel = MaterialPanel(self.material_database)
        self.console_panel = ConsolePanel()
        self.job_panel = JobPanel()
        self.gcode_preview = QtWidgets.QPlainTextEdit()
        self.gcode_preview.setReadOnly(True)
        self.gcode_preview.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)

        right = QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        bottom = QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
        self.layer_dock = self._dock("Cuts / Layers", "layersDock", self.layer_panel, right)
        self.object_dock = self._dock("Objects", "objectsDock", self.object_panel, right)
        self.transform_dock = self._dock("Transform", "transformDock", self.transform_panel, right)
        self.camera_dock = self._dock("Camera", "cameraDock", self.camera_panel, right)
        self.machine_dock = self._dock("Move / Machine", "machineDock", self.machine_panel, right)
        self.material_dock = self._dock("Material library", "materialDock", self.material_panel, right)
        self.job_dock = self._dock("Job control", "jobDock", self.job_panel, right)
        self.console_dock = self._dock("Console", "consoleDock", self.console_panel, bottom)
        self.preview_dock = self._dock("G-code preview", "gcodeDock", self.gcode_preview, bottom)
        self.splitDockWidget(
            self.layer_dock,
            self.object_dock,
            QtCore.Qt.Orientation.Vertical,
        )
        for dock in (
            self.transform_dock,
            self.camera_dock,
            self.machine_dock,
            self.material_dock,
            self.job_dock,
        ):
            self.tabifyDockWidget(self.object_dock, dock)
        self.tabifyDockWidget(self.console_dock, self.preview_dock)
        self.resizeDocks(
            [self.layer_dock, self.object_dock],
            [310, 360],
            QtCore.Qt.Orientation.Vertical,
        )
        self.layer_dock.raise_()
        self.object_dock.raise_()
        self.console_dock.raise_()

        dock_menu = self.menuBar().addMenu("&Panels")
        for dock in (
            self.layer_dock,
            self.object_dock,
            self.transform_dock,
            self.camera_dock,
            self.machine_dock,
            self.material_dock,
            self.job_dock,
            self.console_dock,
            self.preview_dock,
        ):
            dock_menu.addAction(dock.toggleViewAction())

    def _create_status_bar(self) -> None:
        self.cursor_label = QtWidgets.QLabel("X —  Y —")
        self.zoom_label = QtWidgets.QLabel("Zoom —")
        self.runtime_label = QtWidgets.QLabel("Starting core services…")
        self.statusBar().addWidget(self.cursor_label)
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.statusBar().addPermanentWidget(self.runtime_label)

    def _connect_signals(self) -> None:
        self.workspace.cursorPositionChanged.connect(
            lambda x, y: self.cursor_label.setText(f"X {x:8.3f}  Y {y:8.3f} mm")
        )
        self.workspace.zoomChanged.connect(
            lambda zoom: self.zoom_label.setText(f"Zoom {zoom * 100:.0f}%")
        )
        self.workspace.selectionIdsChanged.connect(self._selection_changed)
        self.workspace.objectMoveCommitted.connect(self._object_moved)
        self.workspace.deleteRequested.connect(self.delete_selection)

        self.palette.layerSelected.connect(self.set_active_layer)
        self.layer_panel.activeLayerChanged.connect(self.set_active_layer)
        self.layer_panel.layerEdited.connect(self._layer_edited)
        self.layer_panel.addLayerRequested.connect(self.add_layer)
        self.layer_panel.removeLayerRequested.connect(self.remove_layer)
        self.layer_panel.moveLayerRequested.connect(self.move_layer)

        self.object_panel.selectionRequested.connect(self.workspace.select_objects)
        self.object_panel.objectEdited.connect(self._object_edited)
        self.transform_panel.transformEdited.connect(self._transform_edited)
        self.transform_panel.assignLayerRequested.connect(self._assign_layer)

        self.camera_panel.refreshRequested.connect(self.controller.refresh_camera_image)
        self.camera_panel.captureRequested.connect(self.controller.capture_camera_still)
        self.camera_panel.opacityChanged.connect(self.workspace.set_camera_opacity)
        self.camera_panel.lensCalibrationRequested.connect(
            lambda: self._calibration_message("Lens calibration")
        )
        self.camera_panel.bedCalibrationRequested.connect(
            lambda: self._calibration_message("Bed alignment")
        )

        self.machine_panel.connectRequested.connect(self.controller.connect_machine)
        self.machine_panel.disconnectRequested.connect(self.controller.disconnect_machine)
        self.machine_panel.parkRequested.connect(self.controller.park_at_camera_pose)
        self.machine_panel.stopRequested.connect(self.controller.emergency_stop)
        self.machine_panel.jogRequested.connect(self.controller.jog)
        self.console_panel.commandSubmitted.connect(self.controller.send_diagnostic)
        self.material_panel.applyPresetRequested.connect(self.apply_material_preset)
        self.material_panel.notice.connect(self.show_notice)
        self.material_panel.error.connect(self.show_error)

        self.job_panel.generateRequested.connect(self.generate_toolpath)
        self.job_panel.frameRequested.connect(self.generate_frame)
        self.job_panel.startRequested.connect(self.run_current_job)
        self.job_panel.pauseRequested.connect(self.controller.pause_resume)
        self.job_panel.stopRequested.connect(self.controller.emergency_stop)

        self.controller.statusChanged.connect(self._runtime_status)
        self.controller.cameraImageReady.connect(self.workspace.set_camera_image)
        self.controller.errorOccurred.connect(self.show_error)
        self.controller.notice.connect(self.show_notice)
        self.controller.busyChanged.connect(self._busy_changed)

    def _refresh_document(self, selected_ids: list[str] | None = None) -> None:
        if not any(layer.id == self.active_layer_id for layer in self.document.layers):
            self.active_layer_id = self.document.active_layer_id
        self.workspace.set_document(self.document)
        self.layer_panel.set_document(self.document, self.active_layer_id)
        self.object_panel.set_document(
            self.document,
            selected_ids or self.workspace.selected_object_ids(),
        )
        self.transform_panel.set_document_layers(self.document)
        self.palette.set_layers(self.document.layers, self.active_layer_id)
        if selected_ids:
            self.workspace.select_objects(selected_ids)
        self._selection_changed(self.workspace.selected_object_ids())
        self._update_title()

    def _selection_changed(self, object_ids: list[str]) -> None:
        known = {item.id: item for item in self.document.objects}
        object_ids = [object_id for object_id in object_ids if object_id in known]
        if not self._expanding_group_selection:
            group_ids = {
                known[object_id].group_id
                for object_id in object_ids
                if known[object_id].group_id is not None
            }
            expanded = set(object_ids)
            for group_id in group_ids:
                expanded.update(item.id for item in self.document.group_members(group_id))
            if expanded != set(object_ids):
                self._expanding_group_selection = True
                try:
                    self.workspace.select_objects(list(expanded))
                finally:
                    self._expanding_group_selection = False
                object_ids = list(expanded)
        objects = [known[object_id] for object_id in object_ids]
        self.object_panel.set_selection(object_ids)
        self.transform_panel.set_selection(objects, self.document)

    def _history_changed(self, stack: CommandStack) -> None:
        self.actions["undo"].setEnabled(stack.can_undo)
        self.actions["redo"].setEnabled(stack.can_redo)
        self.actions["undo"].setText(
            f"Undo {stack.undo_text}" if stack.undo_text else "Undo"
        )
        self.actions["redo"].setText(
            f"Redo {stack.redo_text}" if stack.redo_text else "Redo"
        )
        self._refresh_document(self.workspace.selected_object_ids())

    def _document_center(self) -> tuple[float, float]:
        return self.document.work_area.center

    def set_active_layer(self, layer_id: str) -> None:
        self.document.get_layer(layer_id)
        self.active_layer_id = layer_id
        self.layer_panel.set_document(self.document, layer_id)
        self.palette.set_layers(self.document.layers, layer_id)

    def apply_material_preset(self, preset: MaterialPreset) -> None:
        layer = self.document.get_layer(self.active_layer_id)
        replacement = preset.apply_to_layer(layer)
        self.history.execute(
            UpdateLayerCommand(
                self.document,
                layer.id,
                replacement,
                description=f"Apply {preset.material} preset",
            )
        )
        self._refresh_document(self.workspace.selected_object_ids())
        self.show_notice(
            f"Applied {preset.material} · {preset.name} to {layer.name}"
        )

    def add_layer(self) -> None:
        index = len(self.document.layers)
        layer = OperationLayer(
            name=f"Layer {index + 1:02d}",
            color=self.document.next_layer_color(),
            priority=index,
        )
        self.history.execute(AddLayerCommand(self.document, layer))
        self.active_layer_id = layer.id
        self._refresh_document()

    def move_layer(self, layer_id: str, delta: int) -> None:
        order = [layer.id for layer in self.document.layers]
        index = order.index(layer_id)
        target = max(0, min(index + int(delta), len(order) - 1))
        if target == index:
            return
        order.pop(index)
        order.insert(target, layer_id)
        self.history.execute(
            ReorderLayersCommand(
                self.document,
                order,
                description="Move layer up" if delta < 0 else "Move layer down",
            )
        )
        self.active_layer_id = layer_id
        self._refresh_document(self.workspace.selected_object_ids())

    def remove_layer(self, layer_id: str) -> None:
        try:
            command = RemoveLayerCommand(self.document, layer_id)
        except ValueError as exc:
            self.show_error(str(exc))
            return
        self.history.execute(command)
        self.active_layer_id = command.fallback_id
        self._refresh_document()

    def _layer_edited(self, layer_id: str, changes: dict[str, Any]) -> None:
        layer = self.document.get_layer(layer_id)
        payload = layer.to_dict()
        payload.update(changes)
        payload["mode"] = str(changes.get("mode", payload["mode"]))
        replacement = OperationLayer.from_dict(payload)
        if replacement.to_dict() == layer.to_dict():
            return
        self.history.execute(
            UpdateLayerCommand(
                self.document,
                layer_id,
                replacement,
                description=f"Edit {layer.name}",
            )
        )
        self._refresh_document(self.workspace.selected_object_ids())

    def _add_object(self, item: SceneObject, description: str) -> None:
        command = AddObjectCommand(self.document, item, description=description)
        self.history.execute(command)
        self.workspace.select_objects([item.id])

    def add_rectangle(self) -> None:
        self._add_object(
            SceneObject.rectangle(
                self.active_layer_id,
                center=self._document_center(),
                width_mm=40.0,
                height_mm=25.0,
                corner_radius_mm=3.0,
            ),
            "Add rectangle",
        )

    def add_ellipse(self) -> None:
        self._add_object(
            SceneObject.ellipse(
                self.active_layer_id,
                center=self._document_center(),
                width_mm=30.0,
                height_mm=30.0,
            ),
            "Add ellipse",
        )

    def add_line(self) -> None:
        self._add_object(
            SceneObject.line(
                self.active_layer_id,
                center=self._document_center(),
                length_mm=40.0,
            ),
            "Add line",
        )

    def add_text(self) -> None:
        text, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Create text",
            "Text:",
            text="E3",
        )
        if not accepted or not text:
            return
        item = SceneObject(
            name="Text",
            kind="text",
            layer_id=self.active_layer_id,
            transform=Transform(
                self._document_center()[0],
                self._document_center()[1],
                max(15.0, len(text) * 6.0),
                10.0,
            ),
            geometry={"text": text, "font_family": "Sans Serif"},
        )
        self._add_object(item, "Add text")

    def delete_selection(self) -> None:
        selected = self.workspace.selected_object_ids()
        if selected:
            self.history.execute(RemoveObjectsCommand(self.document, selected))

    def duplicate_selection(self) -> None:
        selected = self.workspace.selected_object_ids()
        if not selected:
            return
        command = DuplicateObjectsCommand(self.document, selected)
        self.history.execute(command)
        self.workspace.select_objects([item.id for item in command.duplicates])

    def _object_moved(
        self,
        object_id: str,
        before: tuple[float, float],
        after: tuple[float, float],
    ) -> None:
        item = self.document.get_object(object_id)
        delta_x = after[0] - before[0]
        delta_y = after[1] - before[1]
        members = self.document.group_members(item.group_id) if item.group_id else [item]
        transforms = {
            member.id: member.transform.copy(
                x_mm=member.transform.x_mm + delta_x,
                y_mm=member.transform.y_mm + delta_y,
            )
            for member in members
        }
        self.history.execute(
            UpdateTransformsCommand(
                self.document,
                transforms,
                description="Move group" if item.group_id else "Move object",
            )
        )
        self.workspace.select_objects([member.id for member in members])

    def _object_edited(self, object_id: str, changes: dict[str, Any]) -> None:
        item = self.document.get_object(object_id)
        if all(getattr(item, name) == value for name, value in changes.items()):
            return
        self.history.execute(
            UpdateObjectPropertiesCommand(
                self.document,
                object_id,
                changes,
                description=f"Edit {item.name}",
            )
        )
        self.workspace.select_objects([object_id])

    def _transform_edited(self, object_id: str, transform: Transform) -> None:
        current = self.document.get_object(object_id).transform
        if current.to_dict() == transform.to_dict():
            return
        self.history.execute(UpdateTransformCommand(self.document, object_id, transform))
        self.workspace.select_objects([object_id])

    def _assign_layer(self, object_ids: list[str], layer_id: str) -> None:
        self.history.execute(AssignLayerCommand(self.document, object_ids, layer_id))
        self.workspace.select_objects(object_ids)


    def group_selection(self) -> None:
        selected = self.workspace.selected_object_ids()
        if len(selected) < 2:
            self.show_notice("Select at least two objects to group")
            return
        command = GroupObjectsCommand(self.document, selected)
        self.history.execute(command)
        self.workspace.select_objects(selected)

    def ungroup_selection(self) -> None:
        selected = self.workspace.selected_object_ids()
        try:
            command = UngroupObjectsCommand(self.document, selected)
        except ValueError as exc:
            self.show_notice(str(exc))
            return
        affected = list(command.before)
        self.history.execute(command)
        self.workspace.select_objects(affected)

    def align_selection(self, alignment: Alignment) -> None:
        selected = self.workspace.selected_object_ids()
        transforms = aligned_transforms(self.document, selected, alignment)
        if not transforms:
            self.show_notice("Select at least two objects to align")
            return
        self.history.execute(
            UpdateTransformsCommand(
                self.document,
                transforms,
                description=f"Align {alignment.value}",
            )
        )
        self.workspace.select_objects(selected)

    def distribute_selection(self, *, horizontal: bool) -> None:
        selected = self.workspace.selected_object_ids()
        transforms = distributed_transforms(
            self.document,
            selected,
            horizontal=horizontal,
        )
        if not transforms:
            self.show_notice("Select at least three objects to distribute")
            return
        self.history.execute(
            UpdateTransformsCommand(
                self.document,
                transforms,
                description=(
                    "Distribute horizontally" if horizontal else "Distribute vertically"
                ),
            )
        )
        self.workspace.select_objects(selected)

    def reorder_selection(self, mode: str) -> None:
        selected = self.workspace.selected_object_ids()
        if not selected:
            self.show_notice("Select at least one object to reorder")
            return
        selected_set = set(selected)
        order = [item.id for item in self.document.objects]
        if mode == "front":
            order = [object_id for object_id in order if object_id not in selected_set] + [
                object_id for object_id in order if object_id in selected_set
            ]
            description = "Bring to front"
        elif mode == "back":
            order = [object_id for object_id in order if object_id in selected_set] + [
                object_id for object_id in order if object_id not in selected_set
            ]
            description = "Send to back"
        elif mode == "raise":
            for index in range(len(order) - 2, -1, -1):
                if order[index] in selected_set and order[index + 1] not in selected_set:
                    order[index], order[index + 1] = order[index + 1], order[index]
            description = "Raise objects"
        elif mode == "lower":
            for index in range(1, len(order)):
                if order[index] in selected_set and order[index - 1] not in selected_set:
                    order[index], order[index - 1] = order[index - 1], order[index]
            description = "Lower objects"
        else:
            raise ValueError(f"Unknown object-order mode: {mode}")
        current = [item.id for item in self.document.objects]
        if order == current:
            return
        self.history.execute(
            ReorderObjectsCommand(self.document, order, description=description)
        )
        self.workspace.select_objects(selected)

    def import_svg(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import SVG",
            str(Path.home()),
            "Scalable Vector Graphics (*.svg)",
        )
        if not filename:
            return
        try:
            svg_text = Path(filename).read_text(encoding="utf-8")
            geometry = parse_svg(svg_text)
            polylines = [
                {
                    "points": [[float(x), float(-y)] for x, y in line.points],
                    "closed": line.closed,
                }
                for line in geometry.polylines
            ]
            item = SceneObject.path(
                self.active_layer_id,
                polylines,
                name=Path(filename).stem,
                center=self._document_center(),
                source_name=Path(filename).name,
                source_svg=svg_text,
            )
            self._add_object(item, "Import SVG")
            if geometry.warnings:
                self.show_notice("Imported with warnings: " + "; ".join(geometry.warnings))
        except Exception as exc:
            self.show_error(f"Could not import SVG: {exc}")

    def new_project(self) -> None:
        if not self._confirm_discard_changes():
            return
        self.document = self._new_document()
        self.project_path = None
        self.active_layer_id = self.document.active_layer_id
        self.history.clear()
        self.history.mark_clean()
        self.last_job = None
        self.gcode_preview.clear()
        self.workspace.clear_toolpath_preview()
        self._refresh_document()

    def open_project(self) -> None:
        if not self._confirm_discard_changes():
            return
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open E3 project",
            str(Path.home()),
            "E3 Laser Projects (*.e3laser)",
        )
        if not filename:
            return
        try:
            document = load_project(filename)
            if autosave_is_newer(document, filename):
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Recover autosave",
                    "A newer autosave exists for this project. Recover it?",
                    QtWidgets.QMessageBox.StandardButton.Yes
                    | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.Yes,
                )
                if answer == QtWidgets.QMessageBox.StandardButton.Yes:
                    document = load_project(
                        autosave_path(document, project_path=filename)
                    )
            self.document = document
        except Exception as exc:
            self.show_error(f"Could not open project: {exc}")
            return
        self.project_path = Path(filename)
        self.active_layer_id = self.document.active_layer_id
        self.history.clear()
        self.history.mark_clean()
        self.last_job = None
        self.gcode_preview.clear()
        self.workspace.clear_toolpath_preview()
        self._refresh_document()

    def save_project(self, save_as: bool = False) -> bool:
        destination = self.project_path
        if save_as or destination is None:
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Save E3 project",
                str(destination or (Path.home() / f"{self.document.name}.e3laser")),
                "E3 Laser Projects (*.e3laser)",
            )
            if not filename:
                return False
            destination = Path(filename)
        try:
            self.project_path = save_project(self.document, destination)
        except Exception as exc:
            self.show_error(f"Could not save project: {exc}")
            return False
        clear_autosave(self.document, project_path=self.project_path)
        self.history.mark_clean()
        self.show_notice(f"Saved {self.project_path.name}")
        return True

    def _autosave(self) -> None:
        if self.history.is_clean or not self.document.objects:
            return
        try:
            save_autosave(self.document, project_path=self.project_path)
        except Exception as exc:
            self.statusBar().showMessage(f"Autosave failed: {exc}", 5000)

    def generate_toolpath(self) -> None:
        try:
            job = generate_project_gcode(
                self.document,
                self.runtime.settings.laser,
            )
        except Exception as exc:
            self.show_error(f"Toolpath generation failed: {exc}")
            return
        self.last_job = job
        self.last_job_name = f"{self.document.name}-{time.strftime('%Y%m%d-%H%M%S')}.gcode"
        layer_by_id = {layer.id: layer for layer in self.document.layers}
        self.last_job_powered = any(
            layer_by_id[item.layer_id].power_percent > 0
            for item in self.document.visible_output_objects()
        )
        self.gcode_preview.setPlainText(job.text)
        self.workspace.set_toolpath_preview(job.text)
        self.job_panel.summary.setText(
            f"{job.path_count} paths · {job.cut_length_mm:.1f} mm cut · "
            f"{job.travel_length_mm:.1f} mm travel · "
            f"estimated {job.estimated_seconds:.1f} s"
        )
        generated_dir = self.runtime.settings.app.data_dir / "generated"
        generated_dir.mkdir(parents=True, exist_ok=True)
        path = generated_dir / self.last_job_name
        path.write_text(job.text, encoding="utf-8")
        self.show_notice(f"Generated and validated {path.name}")

    def generate_frame(self) -> None:
        try:
            job = generate_project_frame(self.document, self.runtime.settings.laser)
        except Exception as exc:
            self.show_error(f"Frame generation failed: {exc}")
            return
        self.last_job = job
        self.last_job_name = f"frame-{time.strftime('%Y%m%d-%H%M%S')}.gcode"
        self.last_job_powered = False
        self.gcode_preview.setPlainText(job.text)
        self.workspace.set_toolpath_preview(job.text)
        self.job_panel.summary.setText(
            f"Dry frame · bounds X{job.bounds_mm[0]:.2f}..{job.bounds_mm[2]:.2f} "
            f"Y{job.bounds_mm[1]:.2f}..{job.bounds_mm[3]:.2f}"
        )
        self.show_notice("Dry frame generated; no laser-enable command is present")

    def run_current_job(self) -> None:
        if self.last_job is None:
            self.show_error("Generate a project toolpath or dry frame first")
            return
        machine = self.runtime.context.machine.status()
        if not machine.get("connected"):
            self.show_error("Connect the controller before running a job")
            return
        if not self.runtime.settings.machine.allow_motion:
            self.show_error("Motion is blocked in the local configuration")
            return

        phrase: str | None = None
        if self.last_job_powered:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Powered laser job",
                "This program contains powered laser output.\n\n"
                "Confirm the enclosure, extraction, material, focus and hardware "
                "emergency stop before continuing.",
                QtWidgets.QMessageBox.StandardButton.Ok
                | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Ok:
                return
            required = machine.get("arm_phrase", "ENABLE LASER CONTROL")
            phrase, accepted = QtWidgets.QInputDialog.getText(
                self,
                "Arm laser temporarily",
                f"Type exactly:\n{required}",
            )
            if not accepted or phrase != required:
                self.show_error("Arming phrase did not match")
                return

        self.controller.run_job(
            self.last_job.text,
            self.last_job_name,
            arm_phrase=phrase,
        )

    def _runtime_status(self, status: dict[str, Any]) -> None:
        state = status.get("runtime_state", "unknown")
        camera = status.get("camera")
        machine = status.get("machine")
        self.camera_panel.set_status(camera)
        self.machine_panel.set_status(machine)
        if machine:
            self.console_panel.set_lines(list(machine.get("log", [])))
            self.job_panel.set_job_status(machine.get("job"))
        if state == "running":
            camera_state = "camera online" if camera and camera.get("connected") else "camera offline"
            machine_state = (
                f"{machine.get('protocol')} connected"
                if machine and machine.get("connected")
                else "controller offline"
            )
            self.runtime_label.setText(f"{camera_state} · {machine_state}")
        else:
            self.runtime_label.setText(f"Runtime {state}")
        if status.get("runtime_error"):
            self.runtime_label.setText(status["runtime_error"])

    def _busy_changed(self, busy: bool) -> None:
        self._busy = busy
        self.statusBar().showMessage("Working…" if busy else "", 0 if busy else 1000)

    def _calibration_message(self, title: str) -> None:
        QtWidgets.QMessageBox.information(
            self,
            title,
            "The desktop shell is now using the same camera/calibration core as the "
            "browser application. The native step-by-step calibration dialogs are "
            "the next desktop milestone; the existing browser wizard remains the "
            "validated calibration interface for this branch.",
        )

    def show_notice(self, message: str) -> None:
        self.statusBar().showMessage(message, 7000)

    def show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 10000)
        QtWidgets.QMessageBox.critical(self, "E3 Positioning System", message)

    def show_about(self) -> None:
        QtWidgets.QMessageBox.about(
            self,
            "About E3 Positioning System",
            "<h2>E3 Positioning System</h2>"
            "<p>Native camera-assisted laser design and control workspace for the "
            "Ender-3 S1 Pro, Creality Falcon controller and Logitech C920.</p>"
            "<p>This is an original interface built on the existing E3 calibrated "
            "camera, G-code and guarded machine-control core.</p>",
        )

    def _confirm_discard_changes(self) -> bool:
        if self.history.is_clean:
            return True
        answer = QtWidgets.QMessageBox.question(
            self,
            "Unsaved changes",
            "Save changes to the current project?",
            QtWidgets.QMessageBox.StandardButton.Save
            | QtWidgets.QMessageBox.StandardButton.Discard
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Save,
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Cancel:
            return False
        if answer == QtWidgets.QMessageBox.StandardButton.Save:
            return self.save_project()
        return True

    def _update_title(self) -> None:
        dirty = "" if self.history.is_clean else " *"
        filename = self.project_path.name if self.project_path else self.document.name
        self.setWindowTitle(f"{filename}{dirty} — E3 Positioning System")

    def _available_geometry(self) -> QtCore.QRect:
        screen = self.screen() or QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return QtCore.QRect(0, 0, 1280, 720)
        return screen.availableGeometry()

    def _ensure_window_visible(self, reset_size: bool = False) -> None:
        if self.isMaximized() or self.isFullScreen():
            return

        available = self._available_geometry()
        margin_x = 40
        margin_y = 50
        maximum_width = max(900, available.width() - margin_x)
        maximum_height = max(600, available.height() - margin_y)

        if reset_size:
            width = min(1180, maximum_width)
            height = min(720, maximum_height)
        else:
            width = min(max(self.width(), self.minimumWidth()), maximum_width)
            height = min(max(self.height(), self.minimumHeight()), maximum_height)

        self.resize(width, height)

        frame = self.frameGeometry()
        if not available.contains(frame):
            frame.moveCenter(available.center())
            if frame.left() < available.left():
                frame.moveLeft(available.left())
            if frame.top() < available.top():
                frame.moveTop(available.top())
            if frame.right() > available.right():
                frame.moveRight(available.right())
            if frame.bottom() > available.bottom():
                frame.moveBottom(available.bottom())
            self.move(frame.topLeft())

    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
            QtCore.QTimer.singleShot(0, self._ensure_window_visible)
        else:
            self.showMaximized()

    def _reset_window_size(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        settings.remove("mainWindow/geometry-v2")
        self.showNormal()
        self._ensure_window_visible(reset_size=True)

    def _restore_window_state(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        geometry = settings.value("mainWindow/geometry-v2")
        state = settings.value("mainWindow/state-v2")
        restored_geometry = bool(geometry and self.restoreGeometry(geometry))
        if state:
            self.restoreState(state)
        self._ensure_window_visible(reset_size=not restored_geometry)

    def _save_window_state(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        settings.setValue("mainWindow/geometry-v2", self.saveGeometry())
        settings.setValue("mainWindow/state-v2", self.saveState())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        if not self._confirm_discard_changes():
            event.ignore()
            return
        self._closing = True
        self._save_window_state()
        self.controller.stop()
        event.accept()
