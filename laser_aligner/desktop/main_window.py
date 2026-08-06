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
    AddObjectsCommand,
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
    UpdateObjectShapeCommand,
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
from ..templates import (
    CutTemplate,
    RectangleGridSpec,
    TemplateLibrary,
    instantiate_template,
    template_from_rectangle_grid,
    template_from_project,
)
from .controller import DesktopController
from .controls import InspectorTabs, PanelScrollArea, WheelGuard
from .panels import (
    CameraPanel,
    ConsolePanel,
    JobPanel,
    LayerPanel,
    MachinePanel,
    MaterialPanel,
    ObjectPanel,
    TracePanel,
    TransformPanel,
)
from .qt import require_qt
from .template_designer import GridTemplateDesignerDialog, WORK_AREA_TOLERANCE_MM
from .template_panel import TemplatePanel
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
                f"{layer.power_percent:g}%\n"
                "Click to make active; selected objects are assigned to this layer."
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
        self._wheel_guard = WheelGuard(self)
        application = QtWidgets.QApplication.instance()
        if application is not None:
            application.installEventFilter(self._wheel_guard)
        self.material_database = MaterialDatabase()
        self.template_library = TemplateLibrary(
            self.runtime.settings.app.data_dir / "templates"
        )
        self._templates: dict[str, CutTemplate] = {}
        self.document = self._new_document()
        self.history = CommandStack(max_depth=300)
        self.project_path: Path | None = None
        self.active_layer_id = self.document.active_layer_id
        self.last_job: Any | None = None
        self.last_job_name = ""
        self.last_job_powered = False
        self.last_job_revision: int | None = None
        self._busy = False
        self._closing = False
        self._expanding_group_selection = False
        self._trace_result: dict[str, Any] | None = None
        self._template_match_result: dict[str, Any] | None = None

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
        self.controller.set_live_camera(
            self.camera_panel.live_enabled(),
            self.camera_panel.refresh_interval_ms(),
        )

        self.history.add_listener(self._history_changed)
        self._autosave_timer = QtCore.QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()

        self._restore_window_state()
        QtCore.QTimer.singleShot(0, self._ensure_window_visible)
        QtCore.QTimer.singleShot(0, self.workspace.fit_work_area)
        self._refresh_document()
        self._refresh_template_library()
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
        action("save_template", "Save project as cutting template…")
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
        action("fit", "Fit work area", "Home")
        action("fit_selection", "Fit selection", "Shift+F")
        action("zoom_in", "Zoom in", "Ctrl++")
        action("zoom_out", "Zoom out", "Ctrl+-")
        action("snap", "Snap to grid", "Ctrl+Alt+G", checkable=True)
        self.actions["snap"].setChecked(True)
        action("rectangle", "Add rectangle", "Alt+R")
        action("ellipse", "Add ellipse", "Alt+E")
        action("line", "Add line", "Alt+L")
        action("text", "Add text", "Alt+T")
        action("grid_template_designer", "Design grid cutting template…")
        action("trace_objects", "Detect / trace camera objects…", "Ctrl+Alt+T")
        action("template_alignment", "Cutting template alignment…", "Ctrl+Alt+A")
        action("refresh_camera", "Refresh camera", "F5")
        action("generate", "Generate toolpath", "Ctrl+Alt+Enter")
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
        self.actions["save_template"].triggered.connect(self.save_current_as_template)
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
        self.actions["fit_selection"].triggered.connect(self.workspace.fit_selection)
        self.actions["zoom_in"].triggered.connect(self.workspace.zoom_in)
        self.actions["zoom_out"].triggered.connect(self.workspace.zoom_out)
        self.actions["snap"].toggled.connect(self.workspace.set_snap_enabled)
        self.actions["rectangle"].triggered.connect(self.add_rectangle)
        self.actions["ellipse"].triggered.connect(self.add_ellipse)
        self.actions["line"].triggered.connect(self.add_line)
        self.actions["text"].triggered.connect(self.add_text)
        self.actions["grid_template_designer"].triggered.connect(
            lambda: self.open_grid_template_designer(None)
        )
        self.actions["trace_objects"].triggered.connect(self.open_trace_panel)
        self.actions["template_alignment"].triggered.connect(self.open_template_panel)
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
        for key in ("new", "open", "save", "save_as", "save_template", "import_svg"):
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
        view_menu.addAction(self.actions["fit_selection"])
        view_menu.addAction(self.actions["zoom_in"])
        view_menu.addAction(self.actions["zoom_out"])
        view_menu.addAction(self.actions["snap"])
        view_menu.addSeparator()
        view_menu.addAction(self.actions["minimize_window"])
        view_menu.addAction(self.actions["maximize_window"])
        view_menu.addAction(self.actions["reset_window_size"])

        create_menu = self.menuBar().addMenu("&Create")
        for key in ("rectangle", "ellipse", "line", "text"):
            create_menu.addAction(self.actions[key])
        create_menu.addSeparator()
        create_menu.addAction(self.actions["grid_template_designer"])
        create_menu.addAction(self.actions["trace_objects"])
        create_menu.addAction(self.actions["template_alignment"])

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
        drawing_labels = {
            "rectangle": "Rectangle",
            "ellipse": "Ellipse",
            "line": "Line",
            "text": "Text",
        }
        for key in ("rectangle", "ellipse", "line", "text"):
            button = QtWidgets.QToolButton()
            button.setDefaultAction(self.actions[key])
            button.setText(drawing_labels[key])
            button.setToolButtonStyle(
                QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
            )
            tools.addWidget(button)
        tools.addSeparator()
        tools.addAction(self.actions["trace_objects"])
        tools.addSeparator()
        tools.addAction(self.actions["fit"])
        tools.addAction(self.actions["fit_selection"])
        tools.addAction(self.actions["zoom_in"])
        tools.addAction(self.actions["zoom_out"])
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

        arrange_toolbar = self.addToolBar("Arrange")
        arrange_toolbar.setObjectName("arrangeToolbar")

        def arrange_menu_button(
            title: str,
            action_keys: tuple[str, ...],
        ) -> QtWidgets.QToolButton:
            button = QtWidgets.QToolButton()
            button.setText(title)
            button.setPopupMode(
                QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
            )
            menu = QtWidgets.QMenu(button)
            for action_key in action_keys:
                menu.addAction(self.actions[action_key])
            button.setMenu(menu)
            return button

        arrange_toolbar.addWidget(
            arrange_menu_button(
                "Align",
                (
                    "align_left",
                    "align_center_x",
                    "align_right",
                    "align_bottom",
                    "align_center_y",
                    "align_top",
                ),
            )
        )
        arrange_toolbar.addWidget(
            arrange_menu_button(
                "Distribute",
                ("distribute_h", "distribute_v"),
            )
        )
        arrange_toolbar.addWidget(
            arrange_menu_button(
                "Order",
                ("bring_front", "raise", "lower", "send_back"),
            )
        )

        job_toolbar = self.addToolBar("Job")
        job_toolbar.setObjectName("jobToolbar")
        job_labels = {
            "generate": "Toolpath",
            "frame": "Frame",
            "run": "Run",
            "stop": "Stop",
        }
        for key in ("generate", "frame", "run", "stop"):
            button = QtWidgets.QToolButton()
            button.setDefaultAction(self.actions[key])
            button.setText(job_labels[key])
            button.setToolButtonStyle(
                QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly
            )
            job_toolbar.addWidget(button)

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
        self.trace_panel = TracePanel()
        self.template_panel = TemplatePanel()
        self.camera_panel = CameraPanel()
        self.camera_panel.set_focus_controls(
            dict(self.runtime.settings.camera.controls)
        )
        self.machine_panel = MachinePanel()
        self.material_panel = MaterialPanel(self.material_database)
        self.console_panel = ConsolePanel()
        self.job_panel = JobPanel()
        self.gcode_preview = QtWidgets.QPlainTextEdit()
        self.gcode_preview.setReadOnly(True)
        self.gcode_preview.setLineWrapMode(
            QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap
        )

        self.inspector_tabs = InspectorTabs(self)
        self.inspector_tabs.add_panel("objects", "Objects", self.object_panel)
        self.inspector_tabs.add_panel("transform", "Transform", self.transform_panel)
        self.inspector_tabs.add_panel("trace", "Trace", self.trace_panel)
        self.inspector_tabs.add_panel("templates", "Templates", self.template_panel)
        self.inspector_tabs.add_panel("camera", "Camera", self.camera_panel)
        self.inspector_tabs.add_panel("machine", "Machine", self.machine_panel)
        self.inspector_tabs.add_panel("materials", "Materials", self.material_panel)
        self.inspector_tabs.add_panel("job", "Job", self.job_panel)

        right = QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        bottom = QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
        self.layer_scroll = PanelScrollArea(self.layer_panel, self)
        self.layer_scroll.setProperty("panelKey", "layers")
        self.layer_dock = self._dock(
            "Cuts / Layers",
            "layersDock",
            self.layer_scroll,
            right,
        )
        self.inspector_dock = self._dock(
            "Inspector",
            "inspectorDock",
            self.inspector_tabs,
            right,
        )
        self.inspector_dock.setMinimumWidth(360)
        self.console_dock = self._dock(
            "Console",
            "consoleDock",
            self.console_panel,
            bottom,
        )
        self.preview_dock = self._dock(
            "G-code preview",
            "gcodeDock",
            self.gcode_preview,
            bottom,
        )

        self.splitDockWidget(
            self.layer_dock,
            self.inspector_dock,
            QtCore.Qt.Orientation.Vertical,
        )
        self.tabifyDockWidget(self.console_dock, self.preview_dock)
        self.resizeDocks(
            [self.layer_dock, self.inspector_dock],
            [280, 430],
            QtCore.Qt.Orientation.Vertical,
        )
        self.layer_dock.raise_()
        self.inspector_dock.raise_()
        self.console_dock.raise_()

        dock_menu = self.menuBar().addMenu("&Panels")
        for dock in (
            self.layer_dock,
            self.inspector_dock,
            self.console_dock,
            self.preview_dock,
        ):
            dock_menu.addAction(dock.toggleViewAction())

    def _create_status_bar(self) -> None:
        self.cursor_label = QtWidgets.QLabel("X —  Y —")
        self.selection_label = QtWidgets.QLabel("0 objects selected")
        self.zoom_label = QtWidgets.QLabel("Zoom —")
        self.runtime_label = QtWidgets.QLabel("Starting core services…")
        self.statusBar().addWidget(self.cursor_label)
        self.statusBar().addWidget(self.selection_label)
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
        self.workspace.templatePlacementEdited.connect(
            self._template_canvas_placement_edited
        )
        self.workspace.templatePlacementCommitted.connect(
            self._template_canvas_placement_committed
        )
        self.workspace.deleteRequested.connect(self.delete_selection)
        self.workspace.pointPicked.connect(self._trace_point_picked)

        self.palette.layerSelected.connect(self._palette_layer_selected)
        self.layer_panel.activeLayerChanged.connect(self.set_active_layer)
        self.layer_panel.layerEdited.connect(self._layer_edited)
        self.layer_panel.addLayerRequested.connect(self.add_layer)
        self.layer_panel.removeLayerRequested.connect(self.remove_layer)
        self.layer_panel.moveLayerRequested.connect(self.move_layer)

        self.object_panel.selectionRequested.connect(self.workspace.select_objects)
        self.object_panel.objectEdited.connect(self._object_edited)
        self.transform_panel.transformEdited.connect(self._transform_edited)
        self.transform_panel.rectangleShapeEdited.connect(
            self._rectangle_shape_edited
        )
        self.transform_panel.assignLayerRequested.connect(self._assign_layer)

        self.trace_panel.detectRequested.connect(self._detect_trace_objects)
        self.trace_panel.pickColorRequested.connect(
            self._begin_trace_color_pick
        )
        self.trace_panel.clearRequested.connect(
            self._clear_trace_preview
        )
        self.trace_panel.createRequested.connect(
            self._create_traced_objects
        )
        self.trace_panel.selectionChanged.connect(
            self._trace_selection_changed
        )

        self.template_panel.saveRequested.connect(self.save_current_as_template)
        self.template_panel.newGridRequested.connect(
            lambda: self.open_grid_template_designer(None)
        )
        self.template_panel.editGridRequested.connect(
            self.open_grid_template_designer
        )
        self.template_panel.deleteRequested.connect(self.delete_cut_template)
        self.template_panel.refreshRequested.connect(self._refresh_template_library)
        self.template_panel.templateSelected.connect(self._template_selected)
        self.template_panel.autoMatchRequested.connect(
            lambda: self._request_template_match(None)
        )
        self.template_panel.matchSelectedRequested.connect(
            self._request_template_match
        )
        self.template_panel.placementChanged.connect(
            self._template_placement_changed
        )
        self.template_panel.applyRequested.connect(self._apply_template_objects)
        self.template_panel.clearRequested.connect(self._clear_template_preview)

        self.camera_panel.refreshRequested.connect(self.controller.refresh_camera_image)
        self.camera_panel.liveChanged.connect(self.controller.set_live_camera)
        self.camera_panel.refreshIntervalChanged.connect(
            self.controller.set_live_camera_interval
        )
        self.camera_panel.captureRequested.connect(self.controller.capture_camera_still)
        self.camera_panel.opacityChanged.connect(self.workspace.set_camera_opacity)
        self.camera_panel.focusApplyRequested.connect(
            self.controller.apply_camera_focus
        )
        self.camera_panel.focusSaveRequested.connect(
            self.controller.save_camera_focus
        )
        self.camera_panel.sharpnessRequested.connect(
            self.controller.measure_camera_sharpness
        )
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
        self.controller.cameraImageReady.connect(self._camera_image_ready)
        self.controller.cameraFocusChanged.connect(
            self._camera_focus_changed
        )
        self.controller.traceResultReady.connect(
            self._trace_result_ready
        )
        self.controller.traceColorReady.connect(
            self._trace_color_ready
        )
        self.controller.templateMatchReady.connect(
            self._template_match_ready
        )
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
        count = len(objects)
        self.selection_label.setText(
            "0 objects selected"
            if count == 0
            else f"{count} object{'s' if count != 1 else ''} selected"
        )
        self.object_panel.set_selection(object_ids)
        self.transform_panel.set_selection(objects, self.document)

    def _history_changed(self, stack: CommandStack) -> None:
        if (
            self.last_job is not None
            and self.last_job_revision != self.document.revision
        ):
            self._invalidate_generated_job()
        self.actions["undo"].setEnabled(stack.can_undo)
        self.actions["redo"].setEnabled(stack.can_redo)
        self.actions["undo"].setText(
            f"Undo {stack.undo_text}" if stack.undo_text else "Undo"
        )
        self.actions["redo"].setText(
            f"Redo {stack.redo_text}" if stack.redo_text else "Redo"
        )
        self._refresh_document(self.workspace.selected_object_ids())

    def _invalidate_generated_job(self) -> None:
        self.last_job = None
        self.last_job_name = ""
        self.last_job_powered = False
        self.last_job_revision = None
        if hasattr(self, "gcode_preview"):
            self.gcode_preview.clear()
        if hasattr(self, "workspace"):
            self.workspace.clear_toolpath_preview()
        if hasattr(self, "job_panel"):
            self.job_panel.summary.setText("No job generated")

    def _document_center(self) -> tuple[float, float]:
        return self.document.work_area.center

    def set_active_layer(self, layer_id: str) -> None:
        self.document.get_layer(layer_id)
        self.active_layer_id = layer_id
        self.layer_panel.set_document(self.document, layer_id)
        self.palette.set_layers(self.document.layers, layer_id)

    def _palette_layer_selected(self, layer_id: str) -> None:
        self.set_active_layer(layer_id)
        selected = self.workspace.selected_object_ids()
        changed = [
            object_id
            for object_id in selected
            if self.document.get_object(object_id).layer_id != layer_id
        ]
        if changed:
            layer = self.document.get_layer(layer_id)
            self._assign_layer(changed, layer_id)
            self.show_notice(
                f"Assigned {len(changed)} selected object"
                f"{'s' if len(changed) != 1 else ''} to {layer.name}"
            )

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

    def _rectangle_shape_edited(
        self,
        object_id: str,
        transform: Transform,
        corner_radius_mm: float,
    ) -> None:
        item = self.document.get_object(object_id)
        geometry = dict(item.geometry)
        geometry["corner_radius_mm"] = float(corner_radius_mm)
        if (
            item.transform.to_dict() == transform.to_dict()
            and item.geometry == geometry
        ):
            return
        self.history.execute(
            UpdateObjectShapeCommand(
                self.document,
                object_id,
                transform,
                geometry,
                description=f"Edit {item.name} shape",
            )
        )
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
        self._invalidate_generated_job()
        self._clear_trace_preview()
        self._clear_template_preview(show_message=False)
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
        self._invalidate_generated_job()
        self._clear_trace_preview()
        self._clear_template_preview(show_message=False)
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
        self.last_job_revision = self.document.revision
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
        self.last_job_revision = self.document.revision
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
        if self.last_job_revision != self.document.revision:
            self._invalidate_generated_job()
            self.show_error("The project changed; regenerate the toolpath before running")
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

    @staticmethod
    def _grid_spec_from_template(
        template: CutTemplate,
    ) -> RectangleGridSpec | None:
        try:
            return RectangleGridSpec.from_template(template)
        except (TypeError, ValueError):
            return None

    def open_grid_template_designer(
        self,
        template_id: str | None = None,
    ) -> None:
        existing: CutTemplate | None = None
        initial_spec: RectangleGridSpec | None = None
        if template_id is not None:
            existing = self._templates.get(str(template_id))
            if existing is None:
                self.show_notice("That cutting template is no longer available")
                self._refresh_template_library()
                return
            initial_spec = self._grid_spec_from_template(existing)
            if initial_spec is None:
                self.show_notice(
                    "This template was created from freeform project geometry. "
                    "Edit its objects in a project, then save a new template."
                )
                return

        def submit(action: str, payload: dict[str, Any]) -> None:
            nonlocal existing
            spec = RectangleGridSpec.from_dict(payload)
            if action == "project":
                self._add_rectangle_grid_to_project(spec)
            elif action == "save":
                try:
                    self._save_rectangle_grid_template(spec, existing=existing)
                except Exception as exc:
                    if existing is not None:
                        try:
                            latest = self.template_library.get(existing.id)
                        except Exception:
                            pass
                        else:
                            if latest.modified_at != existing.modified_at:
                                existing = latest
                                raise RuntimeError(
                                    f"{exc}. The latest library version is now "
                                    "loaded; review your values and press Update "
                                    "again to replace it."
                                ) from exc
                    raise
            else:
                raise ValueError(f"Unknown grid-template action: {action}")

        dialog = GridTemplateDesignerDialog(
            self,
            editing=existing is not None,
            max_width_mm=self.document.work_area.width,
            max_height_mm=self.document.work_area.height,
            submit_handler=submit,
        )
        if initial_spec is not None:
            dialog.set_spec(initial_spec.to_dict())
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _save_rectangle_grid_template(
        self,
        spec: RectangleGridSpec,
        *,
        existing: CutTemplate | None = None,
    ) -> None:
        template = template_from_rectangle_grid(
            spec,
            trace_options=(
                existing.trace_options
                if existing is not None
                else self.trace_panel.options()
            ),
            existing=existing,
        )
        path = (
            self.template_library.replace(
                template,
                expected_modified_at=existing.modified_at,
            )
            if existing is not None
            else self.template_library.save(template)
        )
        self._refresh_template_library(template.id)
        self.inspector_tabs.select_panel("templates")
        self._set_manual_template_placement(template.id)
        verb = "Updated" if existing is not None else "Saved"
        self.show_notice(
            f"{verb} grid template {template.name}: "
            f"{spec.rows} x {spec.columns} cuts in {path.name}"
        )

    def _add_rectangle_grid_to_project(self, spec: RectangleGridSpec) -> None:
        template = template_from_rectangle_grid(
            spec,
            trace_options=self.trace_panel.options(),
        )
        center_x, center_y = self._document_center()
        objects = instantiate_template(
            template,
            target_x_mm=center_x,
            target_y_mm=center_y,
            rotation_deg=0.0,
            target_layer_id=self.active_layer_id,
        )
        area = self.document.work_area.expanded(WORK_AREA_TOLERANCE_MM)
        if any(
            not area.contains(x, y)
            for item in objects
            for x, y in item.transform.corners()
        ):
            raise ValueError("The grid does not fit inside the project work area")
        self._clear_template_preview(show_message=False)
        self.history.execute(
            AddObjectsCommand(
                self.document,
                objects,
                description=f"Create {spec.name} grid",
            )
        )
        self.workspace.select_objects([item.id for item in objects])
        self.inspector_tabs.select_panel("objects")
        self.show_notice(
            f"Created {len(objects)} editable rounded rectangles as one undoable grid"
        )

    def _refresh_template_library(self, selected_id: str | None = None) -> None:
        self.controller.cancel_template_match()
        self._template_match_result = None
        self.workspace.clear_template_preview()
        if hasattr(self, "template_panel"):
            self.template_panel.set_busy(False)
        catalog = self.template_library.scan()
        templates = list(catalog.templates)
        self._templates = {item.id: item for item in templates}
        summaries = []
        for item in templates:
            summaries.append(
                {
                    "id": item.id,
                    "name": item.name,
                    "description": item.description,
                    "feature_count": len(item.features),
                    "width_mm": item.width_mm,
                    "height_mm": item.height_mm,
                    "grid_editable": self._grid_spec_from_template(item) is not None,
                }
            )
        self.template_panel.set_templates(summaries, selected_id=selected_id)
        if catalog.diagnostics:
            first = catalog.diagnostics[0]
            count = len(catalog.diagnostics)
            self.show_notice(
                f"Loaded {len(templates)} cutting template"
                f"{'s' if len(templates) != 1 else ''}; ignored {count} invalid "
                f"library file{'s' if count != 1 else ''}. "
                f"First: {first.path.name}: {first.message}"
            )

    def open_template_panel(self) -> None:
        selected_id = self.template_panel.current_template_id()
        self._refresh_template_library(selected_id)
        self.inspector_tabs.select_panel("templates")
        self.template_panel.set_calibration_ready(
            self.runtime.context.bed.calibration is not None
        )
        selected_id = self.template_panel.current_template_id()
        if selected_id:
            self._set_manual_template_placement(selected_id)
            self.show_notice(
                "Template preview ready. Align from the camera or adjust X, Y and rotation manually."
            )
        else:
            self.show_notice(
                "Create cut geometry, then save the project as a cutting template."
            )

    def save_current_as_template(self) -> None:
        if not self.document.visible_output_objects():
            self.show_notice("Add at least one visible cut object before saving a template")
            return
        default_name = self.document.name.strip() or "Label sheet"
        name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Save cutting template",
            "Template name:",
            text=default_name,
        )
        name = name.strip()
        if not accepted or not name:
            return
        try:
            template = template_from_project(
                self.document.clone(),
                name,
                trace_options=self.trace_panel.options(),
            )
            path = self.template_library.save(template)
        except Exception as exc:
            self.show_error(f"Could not save cutting template: {exc}")
            return
        self._refresh_template_library(template.id)
        self.inspector_tabs.select_panel("templates")
        self._set_manual_template_placement(template.id)
        self.show_notice(f"Saved cutting template {template.name} to {path.name}")

    def delete_cut_template(self, template_id: str) -> None:
        template = self._templates.get(str(template_id))
        if template is None:
            self.show_notice("That cutting template is no longer available")
            self._refresh_template_library()
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Delete cutting template",
            f"Delete the reusable template '{template.name}'?\n\n"
            "Existing project objects will not be changed.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self.template_library.delete(template.id)
        except Exception as exc:
            self.show_error(f"Could not delete cutting template: {exc}")
            return
        self._clear_template_preview(show_message=False)
        self._refresh_template_library()
        self.show_notice(
            f"Deleted cutting template {template.name}"
            if deleted
            else "That cutting template was already removed"
        )

    def _set_manual_template_placement(self, template_id: str) -> None:
        if template_id not in self._templates:
            return
        self.controller.cancel_template_match()
        self._template_match_result = None
        center_x, center_y = self._document_center()
        self.template_panel.set_placement(center_x, center_y, 0.0)
        if self.runtime.context.bed.calibration is not None:
            self.template_panel.set_match_message(
                "Manual placement is active. Choose Align selected template "
                "to replace it with a camera alignment."
            )
        else:
            self.template_panel.set_match_message(
                "Manual placement is active. Bed mapping is required for camera alignment."
            )

    def _template_selected(self, template_id: str) -> None:
        self.workspace.clear_template_preview()
        self._set_manual_template_placement(template_id)

    def _request_template_match(self, template_id: str | None = None) -> None:
        if not self._templates:
            self.show_notice("No cutting templates are available")
            return
        if template_id is not None and template_id not in self._templates:
            self.show_notice("The selected cutting template is no longer available")
            return
        if not self.runtime.running:
            self.show_notice("Camera services are still starting")
            return
        if self.runtime.context.bed.calibration is None:
            self.show_notice("Bed mapping is required before camera template alignment")
            return
        self._clear_trace_preview()
        snapshots = [
            CutTemplate.from_dict(item.to_dict())
            for item in self._templates.values()
        ]
        self.template_panel.set_match_message(
            "Detecting one corrected camera frame and comparing label geometry…"
        )
        self.controller.set_template_review_active(True)
        self.template_panel.set_busy(True)
        self.controller.match_cut_templates(snapshots, template_id=template_id)

    def _template_match_ready(self, payload: dict[str, Any]) -> None:
        self.template_panel.set_busy(False)
        camera_image = payload.get("camera_image")
        if isinstance(camera_image, QtGui.QImage) and not camera_image.isNull():
            self._camera_image_ready(camera_image)
        candidates = [dict(item) for item in payload.get("candidates", [])]
        self.template_panel.set_rankings(candidates)
        template_id = str(payload.get("template_id") or "")
        if template_id and template_id not in self._templates:
            self.controller.set_template_review_active(False)
            self._template_match_result = None
            self.workspace.clear_template_preview()
            self.template_panel.clear_placement()
            self.template_panel.set_match_message(
                "The matched template is no longer in the library. Run alignment again."
            )
            return
        if not payload.get("matched") or not payload.get("template_id"):
            self.controller.set_template_review_active(False)
            self._template_match_result = None
            self.workspace.clear_template_preview()
            self.template_panel.clear_placement()
            self.template_panel.set_match_message(
                str(payload.get("message", "No viable cutting-template match was found."))
            )
            self.show_notice(str(payload.get("message", "Template matching complete")))
            return
        self._template_match_result = dict(payload)
        self.template_panel.set_match_result(payload)
        self.inspector_tabs.select_panel("templates")
        self.show_notice(str(payload.get("message", "Template alignment ready for review")))

    def _template_placement_changed(self, payload: dict[str, Any]) -> None:
        template_id = str(payload.get("template_id") or "")
        template = self._templates.get(template_id)
        if template is None:
            self.workspace.clear_template_preview()
            return
        center_x = float(payload.get("center_x_mm", 0.0))
        center_y = float(payload.get("center_y_mm", 0.0))
        rotation = float(payload.get("rotation_deg", 0.0))
        self._update_template_match_adjustment(payload)
        try:
            objects = instantiate_template(
                template,
                target_x_mm=center_x,
                target_y_mm=center_y,
                rotation_deg=rotation,
                target_layer_id=self.active_layer_id,
            )
        except Exception as exc:
            self.workspace.clear_template_preview()
            self.template_panel.clear_placement()
            self.show_notice(f"Could not preview cutting template: {exc}")
            return
        detections: list[dict[str, Any]] = []
        if (
            self._template_match_result is not None
            and self._template_match_result.get("template_id") == template_id
        ):
            detections = list(self._template_match_result.get("detections", []))
        self.workspace.set_template_preview(
            objects,
            detections=detections,
            center_x_mm=center_x,
            center_y_mm=center_y,
            rotation_deg=rotation,
        )

    def _template_canvas_placement_edited(
        self,
        center_x_mm: float,
        center_y_mm: float,
        rotation_deg: float,
    ) -> None:
        """Mirror a live canvas gesture into the numeric placement controls."""

        self.template_panel.set_placement(
            center_x_mm,
            center_y_mm,
            rotation_deg,
            emit=False,
        )
        self._update_template_match_adjustment(self.template_panel.placement())

    def _template_canvas_placement_committed(
        self,
        center_x_mm: float,
        center_y_mm: float,
        rotation_deg: float,
    ) -> None:
        """Redraw a completed gesture from the panel's canonical precision."""

        self.template_panel.set_placement(
            center_x_mm,
            center_y_mm,
            rotation_deg,
            emit=False,
        )
        payload = dict(self.template_panel.placement())
        self._update_template_match_adjustment(payload)
        QtCore.QTimer.singleShot(
            0,
            lambda payload=payload: self._template_placement_changed(payload),
        )

    def _update_template_match_adjustment(self, payload: dict[str, Any]) -> None:
        result = self._template_match_result
        if result is None:
            return
        template_id = str(payload.get("template_id") or "")
        if template_id != str(result.get("template_id") or ""):
            return
        tolerance = 0.0005 + 1e-9
        center_changed = (
            abs(
                float(payload.get("center_x_mm", 0.0))
                - float(result.get("center_x_mm", 0.0))
            )
            > tolerance
            or abs(
                float(payload.get("center_y_mm", 0.0))
                - float(result.get("center_y_mm", 0.0))
            )
            > tolerance
        )
        rotation_delta = (
            float(payload.get("rotation_deg", 0.0))
            - float(result.get("rotation_deg", 0.0))
            + 180.0
        ) % 360.0 - 180.0
        self.template_panel.set_match_adjusted(
            center_changed or abs(rotation_delta) > tolerance
        )

    def _apply_template_objects(self, payload: dict[str, Any]) -> None:
        template_id = str(payload.get("template_id") or "")
        template = self._templates.get(template_id)
        if template is None:
            self.show_notice("Select an available cutting template first")
            return
        try:
            objects = instantiate_template(
                template,
                target_x_mm=float(payload.get("center_x_mm", 0.0)),
                target_y_mm=float(payload.get("center_y_mm", 0.0)),
                rotation_deg=float(payload.get("rotation_deg", 0.0)),
                target_layer_id=self.active_layer_id,
            )
            area = self.document.work_area.expanded(WORK_AREA_TOLERANCE_MM)
            outside = [
                item.name
                for item in objects
                if any(not area.contains(x, y) for x, y in item.transform.corners())
            ]
            if outside:
                raise ValueError(
                    f"{len(outside)} cut object{'s are' if len(outside) != 1 else ' is'} "
                    "outside the configured work area"
                )
            self.history.execute(
                AddObjectsCommand(
                    self.document,
                    objects,
                    description=f"Apply {template.name} template",
                )
            )
        except Exception as exc:
            self.show_error(f"Could not apply cutting template: {exc}")
            return
        self.workspace.select_objects([item.id for item in objects])
        self._clear_template_preview(show_message=False)
        self.show_notice(
            f"Created {len(objects)} aligned cut object"
            f"{'s' if len(objects) != 1 else ''} from {template.name}"
        )

    def _clear_template_preview(self, show_message: bool = True) -> None:
        self.controller.cancel_template_match()
        self._template_match_result = None
        self.workspace.clear_template_preview()
        if hasattr(self, "template_panel"):
            self.template_panel.set_busy(False)
            self.template_panel.clear_placement()
            if show_message:
                self.template_panel.set_match_message("Template preview cleared.")
        if show_message:
            self.show_notice("Template preview cleared")


    def open_trace_panel(self) -> None:
        self.inspector_tabs.select_panel("trace")
        self.trace_panel.set_calibration_ready(
            self.runtime.context.bed.calibration is not None
        )
        self.show_notice(
            "Trace mode: detect automatically or pick a target color from the camera image."
        )

    def _detect_trace_objects(self, raw_options: dict[str, Any]) -> None:
        self._clear_template_preview(show_message=False)
        self.controller.detect_trace_objects(raw_options)

    def _begin_trace_color_pick(self) -> None:
        if self.runtime.context.bed.calibration is None:
            self.show_error("Bed mapping is required before sampling camera color")
            return
        self._clear_template_preview(show_message=False)
        self.inspector_tabs.select_panel("trace")
        self.workspace.begin_point_pick()
        self.show_notice("Click the center of one target object in the camera image")

    def _trace_point_picked(self, x_mm: float, y_mm: float) -> None:
        self.controller.sample_trace_color(x_mm, y_mm)

    def _trace_color_ready(self, payload: dict[str, Any]) -> None:
        self.trace_panel.set_color_sample(payload)
        self.inspector_tabs.select_panel("trace")
        self.show_notice(
            f"Sampled target color at X{payload['machine_x']:.2f} "
            f"Y{payload['machine_y']:.2f}"
        )

    def _trace_result_ready(self, result: dict[str, Any]) -> None:
        self._trace_result = result
        self.trace_panel.set_result(result)
        self.inspector_tabs.select_panel("trace")
        self.workspace.set_trace_preview(
            list(result.get("detections", [])),
            self.trace_panel.selected_ids(),
        )
        self.show_notice(str(result.get("message", "Object detection complete")))

    def _trace_selection_changed(self, selected_ids: list[str]) -> None:
        if self._trace_result is None:
            return
        self.workspace.set_trace_preview(
            list(self._trace_result.get("detections", [])),
            selected_ids,
        )

    def _clear_trace_preview(self) -> None:
        self._trace_result = None
        self.workspace.clear_trace_preview()
        if hasattr(self, "trace_panel"):
            self.trace_panel.clear_result()

    def _trace_detection_to_object(
        self,
        detection: dict[str, Any],
        output_mode: str,
    ) -> SceneObject:
        index = int(detection.get("index", 0))
        name = f"Traced object {index:02d}"
        center = tuple(float(value) for value in detection["center_mm"])
        if (
            output_mode == "rounded"
            and detection.get("shape") == "rounded_rectangle"
        ):
            item = SceneObject.rectangle(
                self.active_layer_id,
                name=name,
                center=center,
                width_mm=float(detection["width_mm"]),
                height_mm=float(detection["height_mm"]),
                corner_radius_mm=float(detection.get("corner_radius_mm", 0.0)),
            )
            item.transform = item.transform.copy(
                rotation_deg=float(detection.get("rotation_deg", 0.0))
            )
        else:
            points = [
                [float(point[0]), float(point[1])]
                for point in detection.get("contour_mm", [])
            ]
            if len(points) < 3:
                raise ValueError(f"Trace {index} has no usable contour")
            item = SceneObject.path(
                self.active_layer_id,
                [{"points": points, "closed": True}],
                name=name,
                center=center,
                source_name="camera trace",
            )
        item.metadata.update(
            {
                "trace_source": detection.get("source", "direct"),
                "trace_confidence": float(detection.get("confidence", 0.0)),
                "trace_shape": detection.get("shape", output_mode),
            }
        )
        return item

    def _create_traced_objects(self, payload: dict[str, Any]) -> None:
        if self._trace_result is None:
            self.show_error("Run object detection before creating vector paths")
            return
        selected = set(str(value) for value in payload.get("selected_ids", []))
        detections = [
            item
            for item in self._trace_result.get("detections", [])
            if str(item.get("id")) in selected
        ]
        if not detections:
            self.show_notice("Select at least one detected outline")
            return
        output_mode = str(payload.get("output_mode", "rounded"))
        try:
            objects = [
                self._trace_detection_to_object(item, output_mode)
                for item in detections
            ]
            command = AddObjectsCommand(
                self.document,
                objects,
                description=f"Create {len(objects)} traced objects",
            )
            self.history.execute(command)
        except Exception as exc:
            self.show_error(f"Could not create traced objects: {exc}")
            return
        self.workspace.clear_trace_preview()
        self._trace_result = None
        self.trace_panel.clear_result()
        self.workspace.select_objects([item.id for item in objects])
        self.show_notice(
            f"Created {len(objects)} editable vector object"
            f"{'s' if len(objects) != 1 else ''}"
        )

    def _camera_image_ready(self, image: QtGui.QImage) -> None:
        self.workspace.set_camera_image(image)
        self.camera_panel.set_image_updated()

    def _camera_focus_changed(
        self,
        payload: dict[str, Any],
    ) -> None:
        self.camera_panel.set_focus_result(payload)
        if payload.get("changed"):
            self._clear_template_preview(show_message=False)
            self.show_notice(
                "Camera focus changed. Verify or redo lens and "
                "bed calibration before precision work."
            )

    def _runtime_status(self, status: dict[str, Any]) -> None:
        state = status.get("runtime_state", "unknown")
        camera = status.get("camera")
        machine = status.get("machine")
        self.camera_panel.set_status(camera)
        calibration_ready = bool((status.get("bed") or {}).get("calibrated", False))
        self.camera_panel.set_calibration_ready(calibration_ready)
        self.trace_panel.set_calibration_ready(calibration_ready)
        self.template_panel.set_calibration_ready(calibration_ready)
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
        settings.remove("mainWindow/geometry-v3")
        self.showNormal()
        self._ensure_window_visible(reset_size=True)

    def _restore_window_state(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        geometry = settings.value("mainWindow/geometry-v3")
        state = settings.value("mainWindow/state-v3")
        restored_geometry = bool(geometry and self.restoreGeometry(geometry))
        if state:
            self.restoreState(state)
        inspector_index = int(
            settings.value("mainWindow/inspector-tab-v3", 0)
        )
        if hasattr(self, "inspector_tabs"):
            self.inspector_tabs.setCurrentIndex(
                max(0, min(inspector_index, self.inspector_tabs.count() - 1))
            )
        self._ensure_window_visible(reset_size=not restored_geometry)

    def _save_window_state(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        settings.setValue("mainWindow/geometry-v3", self.saveGeometry())
        settings.setValue("mainWindow/state-v3", self.saveState())
        if hasattr(self, "inspector_tabs"):
            settings.setValue(
                "mainWindow/inspector-tab-v3",
                self.inspector_tabs.currentIndex(),
            )

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
