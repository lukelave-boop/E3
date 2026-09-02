from __future__ import annotations

import copy
import hashlib
import logging
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core import CoreRuntime
from ..gcode.job_plan import (
    JobPlanCancelled,
    build_job_plan,
    restart_program_from_move,
)
from ..geometry.polygon import normalize_convex_polygon
from ..identity import (
    application_icon_filename,
    application_identity,
    application_window_title,
)
from ..machine.controller_dialects import resolve_air_assist_commands
from ..materials import (
    MaterialDatabase,
    MaterialPreset,
    builtin_material_presets,
    resolve_new_project_operation_defaults,
)
from ..planning import PlanningCache
from ..project import (
    GCODE_FILE_DIALOG_FILTER,
    LIGHTBURN_FILE_DIALOG_FILTER,
    RASTER_FILE_DIALOG_FILTER,
    SVG_FILE_DIALOG_FILTER,
    AddLayerCommand,
    AddObjectCommand,
    AddObjectsCommand,
    Alignment,
    AssignLayerCommand,
    Bounds,
    CommandStack,
    CoordinateSpace,
    DuplicateObjectsCommand,
    FunctionalCommand,
    GroupObjectsCommand,
    JobPreflightCancelled,
    JobPreflightContext,
    JobPreflightReport,
    LayerMode,
    NativePathGeometry,
    ObjectKind,
    OperationLayer,
    PathAffineTransform,
    PathFillRule,
    ProjectDocument,
    ProjectJob,
    RasterVectorizationOptions,
    RasterVectorizationResult,
    RemoveLayerCommand,
    RemoveObjectsCommand,
    ReorderLayersCommand,
    ReorderObjectsCommand,
    ReplaceObjectsCommand,
    SceneObject,
    ToolpathGenerationCancelled,
    Transform,
    UngroupObjectsCommand,
    UpdateLayerCommand,
    UpdateObjectPropertiesCommand,
    UpdateObjectShapeCommand,
    UpdateTransformCommand,
    UpdateTransformsCommand,
    aligned_transforms,
    autosave_is_newer,
    autosave_path,
    build_job_preflight_report,
    center_selection_on_stock,
    clear_autosave,
    distributed_transforms,
    fit_selection_to_stock,
    generate_project_gcode,
    is_stock_boundary,
    load_gcode_project,
    load_lightburn_project,
    load_project,
    load_svg_project,
    mark_stock_boundary,
    native_path_bounds,
    read_raster_asset_payload,
    save_autosave,
    save_project,
    scan_gcode_file,
    scan_lightburn_file,
    scan_raster_file,
    scan_svg_file,
    snap_selection_rotation_to_stock,
    stock_boundaries,
    transform_native_path,
    verify_project_job_assets,
)
from ..storage import atomic_write_text
from ..templates import (
    CutTemplate,
    RectangleGridSpec,
    TemplateLibrary,
    instantiate_template,
    template_from_project,
    template_from_rectangle_grid,
)
from ..vision.trace_orientation import (
    MAX_TRACE_ORIENTATION_SEGMENTS,
    MAX_TRACE_ORIENTATION_SUBPATHS,
    TraceOrientationEstimate,
    TraceOrientationGeometry,
    estimate_trace_orientation,
    trace_rotation_transform,
)
from .context_bar import ContextPropertyBar
from .controller import (
    DESKTOP_SHUTDOWN_TIMEOUT_SECONDS,
    DesktopController,
    image_to_qimage,
)
from .controls import InspectorTabs, WheelGuard
from .icons import action_icon, apply_action_icons
from .import_review import review_import_manifest
from .job_preflight import JobPreflightDialog
from .job_preview import (
    JobPreviewDialog,
    JobPreviewPreparationCancelled,
    PreparedJobPreview,
    prepare_job_preview,
)
from .machine_manager import MachineManagerDialog
from .machine_setup import MachineSetupDialog
from .panels import (
    CameraPanel,
    ConsolePanel,
    JobProgressWidget,
    LayerPanel,
    MachinePanel,
    MaterialPanel,
    ObjectPanel,
    TracePanel,
    TransformPanel,
)
from .qt import require_qt
from .raster_vectorize_dialog import RasterVectorizationDialog
from .runtime_strip import RuntimeSafetyStrip
from .setup_guide import show_setup_guide
from .stock_layout_bar import StockLayoutToolBar
from .template_designer import WORK_AREA_TOLERANCE_MM, GridTemplateDesignerDialog
from .template_panel import TemplatePanel
from .text_dialog import VectorTextDialog
from .text_geometry import create_vector_text_object
from .workspace import WorkspaceFrame, WorkspaceView

QtCore, QtGui, QtWidgets = require_qt()

LOGGER = logging.getLogger(__name__)


def _qimage_content_sha256(image: QtGui.QImage) -> str:
    """Hash active QImage bytes without format-dependent row padding."""

    digest = hashlib.sha256()
    pixels = image.constBits()
    active_row_bytes = (image.width() * image.depth() + 7) // 8
    stride = image.bytesPerLine()
    for row in range(image.height()):
        start = row * stride
        digest.update(pixels[start : start + active_row_bytes])
    return digest.hexdigest()

_DESIGN_DOCK_MIN_WIDTH = 360
_DESIGN_DOCK_DEFAULT_WIDTH = 420
_PRIMARY_CONTROLS_INLINE_WIDTH = 1800
_STATUS_LAYOUT_RESERVE = 56
_STATUS_MESSAGE_PADDING = 12

_AUTHORING_ACTION_KEYS = (
    "new",
    "open",
    "save",
    "save_as",
    "save_template",
    "import_svg",
    "import_gcode",
    "import_lightburn",
    "import_image",
    "undo",
    "redo",
    "delete",
    "duplicate",
    "select_all",
    "group",
    "ungroup",
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
    "rectangle",
    "ellipse",
    "line",
    "text",
    "grid_template_designer",
    "trace_objects",
    "template_alignment",
)


LAYER_PALETTE_COLORS = (
    "#101010", "#185CFF", "#F02C3D", "#2DD12D", "#E5DA19",
    "#FF8A18", "#18C9D4", "#ED23D2", "#8A8A8A", "#25358E",
    "#A71927", "#178A35", "#9A941A", "#A75E1D", "#15828A",
    "#9A248B", "#B2B2B2", "#5367B5", "#B35F70", "#61B471",
    "#B9B35D", "#C88A56", "#5EAFB5", "#B065A8", "#D0D0D0",
    "#7184DC", "#DA7D8A", "#83CB91", "#D6CD77", "#DD9B68",
)


class LayerPaletteBar(QtWidgets.QWidget):
    layerSelected = QtCore.Signal(str)
    addLayerRequested = QtCore.Signal()
    presetLayerRequested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(2, 1, 2, 1)
        outer.setSpacing(3)
        self._buttons: dict[str, QtWidgets.QToolButton] = {}
        self._preset_buttons: dict[int, QtWidgets.QToolButton] = {}
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setObjectName("layerPaletteScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll.setMinimumWidth(70)
        self._scroll.setFixedHeight(31)
        self._scroll.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._button_host = QtWidgets.QWidget()
        self._button_host.setObjectName("layerPaletteButtonHost")
        self._layout = QtWidgets.QHBoxLayout(self._button_host)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._layout.setSizeConstraint(
            QtWidgets.QLayout.SizeConstraint.SetMinimumSize
        )
        self._scroll.setWidget(self._button_host)
        outer.addWidget(self._scroll, 1)

        self.add_button = QtWidgets.QToolButton()
        self.add_button.setText("+")
        self.add_button.setToolTip("Create a new operation layer")
        self.add_button.setAccessibleName("Add operation layer")
        self.add_button.setFixedSize(26, 24)
        self.add_button.clicked.connect(self.addLayerRequested)
        outer.addWidget(self.add_button)

    def set_layers(self, layers: list[OperationLayer], active_layer_id: str) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()
        self._preset_buttons.clear()

        swatch_count = max(len(LAYER_PALETTE_COLORS), len(layers))
        for index in range(swatch_count):
            layer = layers[index] if index < len(layers) else None
            color_value = (
                layer.color
                if layer is not None
                else LAYER_PALETTE_COLORS[index % len(LAYER_PALETTE_COLORS)]
            )
            button = QtWidgets.QToolButton()
            button.setCheckable(True)
            button.setChecked(layer is not None and layer.id == active_layer_id)
            button.setText(f"{index:02d}")
            if layer is not None:
                button.setAccessibleName(f"Operation {index:02d}: {layer.name}")
                mode_status = f"{layer.mode.value.title()} toolpath"
                button.setToolTip(
                    f"{index:02d} · {layer.name}\n"
                    f"{mode_status} · {layer.speed_mm_min:g} mm/min · "
                    f"{layer.power_percent:g}%\n"
                    f"Output {'on' if layer.output_enabled else 'off'} · "
                    f"{'shown' if layer.visible else 'hidden'}\n"
                    "Click to make active; selected objects are assigned to this layer."
                )
                button.clicked.connect(
                    lambda checked=False, layer_id=layer.id: self.layerSelected.emit(
                        layer_id
                    )
                )
                self._buttons[layer.id] = button
            else:
                button.setAccessibleName(f"Unused operation color {index:02d}")
                button.setToolTip(
                    f"{index:02d} · unused operation color\n"
                    "Click to create a new line operation with this color."
                )
                button.clicked.connect(
                    lambda checked=False, color=color_value: self.presetLayerRequested.emit(
                        color
                    )
                )
            button.setFixedSize(27, 23)
            swatch = QtGui.QColor(color_value)
            foreground = "#07130F" if swatch.lightnessF() >= 0.58 else "#FFFFFF"
            button.setStyleSheet(
                "QToolButton {"
                f"background: {color_value}; color: {foreground}; border: 1px solid "
                f"{'#FFFFFF' if layer is not None and layer.id == active_layer_id else '#303030'};"
                "border-radius: 2px; padding: 0; font-size: 8pt;"
                "}"
            )
            self._layout.addWidget(button)
            self._preset_buttons[index] = button

        self._layout.addStretch(1)
        self._button_host.adjustSize()
        self._scroll.horizontalScrollBar().setValue(0)


class E3MainWindow(QtWidgets.QMainWindow):
    shutdownStarted = QtCore.Signal(float)

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
        self.material_database.seed(builtin_material_presets())
        self.template_library = TemplateLibrary(
            self.runtime.settings.app.data_dir / "templates"
        )
        self._templates: dict[str, CutTemplate] = {}
        self._planning_cache = PlanningCache()
        self._new_project_defaults_notice: str | None = None
        self._new_project_defaults_source: str | None = None
        self.document = self._new_document()
        self.history = CommandStack(max_depth=300)
        self.project_path: Path | None = None
        self.active_layer_id = self.document.active_layer_id
        self.last_job: Any | None = None
        self.last_job_name = ""
        self.last_job_powered = False
        self.last_job_revision: int | None = None
        self.last_job_work_area: tuple[float, float, float, float] | None = None
        self.last_job_coordinate_frame: tuple[Any, ...] | None = None
        self.last_job_preview_data: PreparedJobPreview | None = None
        self.last_job_preflight_report: JobPreflightReport | None = None
        self._job_preflight_dialog: JobPreflightDialog | None = None
        self._job_preview_dialog: JobPreviewDialog | None = None
        self._machine_manager_dialog: MachineManagerDialog | None = None
        self._machine_setup_dialog: MachineSetupDialog | None = None
        self._pending_calibration_capture: dict[str, Any] | None = None
        self._busy = False
        self._controller_busy = False
        self._job_preparation_busy = False
        self._job_preparation_label = ""
        self._job_preparation_owner: tuple[str, int] | None = None
        self._job_request_id = 0
        self._job_worker_requests: dict[int, threading.Event] = {}
        self._job_worker_phases: dict[int, str] = {}
        self._job_cancel_reason = ""
        self._job_render_request_id: int | None = None
        self._job_render_pending: set[str] = set()
        self._job_render_progress: dict[str, float] = {}
        self._authoring_freeze_owner: int | None = None
        self._authoring_action_states: dict[str, bool] = {}
        self._closing = False
        self._close_requested = False
        self._expanding_group_selection = False
        self._trace_result: dict[str, Any] | None = None
        self._active_trace_request_id: int | None = None
        self._trace_raster_preview_images: dict[str, QtGui.QImage] = {}
        self._trace_raster_preview_area: Bounds | None = None
        self._trace_raster_preview_signature: object | None = None
        self._template_match_result: dict[str, Any] | None = None

        self._application_identity = application_identity()
        self.setWindowTitle(self._application_identity)
        icon_path = (
            Path(__file__).resolve().parent
            / "assets"
            / application_icon_filename()
        )
        if icon_path.exists():
            self.setWindowIcon(QtGui.QIcon(str(icon_path)))
        self.setMinimumSize(900, 600)
        self.resize(1320, 820)
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
        if self._new_project_defaults_notice is not None:
            self.show_notice(self._new_project_defaults_notice)
        self._default_window_state = self.saveState(7)
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
        support = self.runtime.context._current_honeycomb_support()
        # The authoring/display frame and execution authority are deliberately
        # separate.  A current legacy three-point reference is still enough to
        # place the camera raster and the empty X0/Y0 drafting surface over the
        # physical honeycomb.  It remains unable to generate or run a powered
        # honeycomb-local job: _project_coordinate_frame() requires the newer
        # automatic four-corner evidence before toolpath preparation.
        if support is not None:
            document = ProjectDocument.new(
                work_area=Bounds(
                    0.0,
                    0.0,
                    support.support_width_mm,
                    support.support_height_mm,
                ),
                coordinate_space=CoordinateSpace.HONEYCOMB_LOCAL,
            )
        else:
            area = self.runtime.settings.machine.work_area
            document = ProjectDocument.new(
                work_area=Bounds(area.x_min, area.y_min, area.x_max, area.y_max)
            )
        identity = self.runtime.context.machine_identity
        defaults = resolve_new_project_operation_defaults(
            machine_profile_id=identity.machine_profile_id,
            tool_head_profile_id=identity.tool_head_profile_id,
            max_work_feed_mm_min=(
                self.runtime.settings.machine.max_work_feed_mm_min
            ),
        )
        document.layers = list(defaults.layers)
        self._new_project_defaults_source = defaults.source.value
        self._new_project_defaults_notice = defaults.notice
        return document

    def _running_material_profile_ids(self) -> tuple[str, str]:
        """Return stable profiles from the running context, never next launch."""

        identity = self.runtime.context.machine_identity
        return (
            identity.machine_profile_id,
            identity.tool_head_profile_id,
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
        import_actions = (
            ("import_svg", "SVG…", "Ctrl+I", "Import SVG…"),
            ("import_gcode", "G-code…", None, "Import G-code…"),
            (
                "import_lightburn",
                "LightBurn project…",
                None,
                "Import LightBurn project…",
            ),
            (
                "import_image",
                "Raster image…",
                "Ctrl+Shift+I",
                "Import raster image…",
            ),
        )
        for key, text, shortcut, tool_tip in import_actions:
            item = action(key, text, shortcut)
            item.setIconText(tool_tip)
            item.setToolTip(tool_tip)
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
        action("select_tool", "Select tool", checkable=True)
        action("rectangle", "Draw rectangle", "Alt+R", checkable=True)
        action("ellipse", "Add ellipse", "Alt+E")
        action("line", "Add line", "Alt+L")
        action("text", "Add text", "Alt+T")
        action("grid_template_designer", "Design grid cutting template…")
        action("trace_objects", "Detect / trace camera objects…", "Ctrl+Alt+T")
        action("template_alignment", "Cutting template alignment…", "Ctrl+Alt+A")
        action("refresh_camera", "Refresh camera", "F5")
        action("machine_manager", "Manage machines…", "Ctrl+Alt+Shift+M")
        action("machine_setup", "Machine Setup…", "Ctrl+Alt+M")
        action("generate", "Generate toolpath", "Ctrl+Alt+Enter")
        action("optimize_paths", "Optimize path ordering")
        action("preview_job", "Preview generated job", "Alt+P")
        action("export_gcode", "Export generated G-code…", "Ctrl+Shift+E")
        action("stop", "Software stop / laser off", "Esc")
        action("minimize_window", "Minimize window", "Ctrl+M")
        action("maximize_window", "Maximize / restore window", "Ctrl+Shift+M")
        action("reset_window_size", "Reset window size")
        action("reset_workspace_layout", "Reset workspace layout")
        action("setup_guide", "Permanent camera setup guide…")
        action("about", "About E3 Positioning System")

        self._drawing_action_group = QtGui.QActionGroup(self)
        self._drawing_action_group.setExclusive(True)
        self._drawing_action_group.addAction(self.actions["select_tool"])
        self._drawing_action_group.addAction(self.actions["rectangle"])
        self.actions["select_tool"].setChecked(True)

        apply_action_icons(self.actions, size=20)

        self.actions["undo"].setEnabled(False)
        self.actions["redo"].setEnabled(False)
        self.actions["export_gcode"].setEnabled(False)
        self.actions["optimize_paths"].setCheckable(True)
        self.actions["optimize_paths"].setChecked(True)

        self.actions["new"].triggered.connect(self.new_project)
        self.actions["open"].triggered.connect(self.open_project)
        self.actions["save"].triggered.connect(self.save_project)
        self.actions["save_as"].triggered.connect(lambda: self.save_project(save_as=True))
        self.actions["save_template"].triggered.connect(self.save_current_as_template)
        self.actions["import_svg"].triggered.connect(self.import_svg)
        self.actions["import_gcode"].triggered.connect(self.import_gcode)
        self.actions["import_lightburn"].triggered.connect(self.import_lightburn)
        self.actions["import_image"].triggered.connect(self.import_image)
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
        self.actions["select_tool"].triggered.connect(
            lambda checked=False: self._activate_selection_tool()
        )
        self.actions["rectangle"].triggered.connect(
            lambda checked=False: self.add_rectangle()
        )
        self.actions["ellipse"].triggered.connect(self.add_ellipse)
        self.actions["line"].triggered.connect(self.add_line)
        self.actions["text"].triggered.connect(self.add_text)
        self.actions["grid_template_designer"].triggered.connect(
            lambda: self.open_grid_template_designer(None)
        )
        self.actions["trace_objects"].triggered.connect(self.open_trace_panel)
        self.actions["template_alignment"].triggered.connect(self.open_template_panel)
        self.actions["refresh_camera"].triggered.connect(self.controller.retry_camera_image)
        self.actions["machine_manager"].triggered.connect(self.open_machine_manager)
        self.actions["machine_setup"].triggered.connect(self.open_machine_setup)
        self.actions["setup_guide"].triggered.connect(
            lambda: show_setup_guide(self)
        )
        self.actions["generate"].triggered.connect(self.generate_toolpath)
        self.actions["preview_job"].triggered.connect(self.show_job_preview)
        self.actions["export_gcode"].triggered.connect(self.export_gcode)
        self.actions["stop"].triggered.connect(self.controller.emergency_stop)
        self.actions["minimize_window"].triggered.connect(self.showMinimized)
        self.actions["maximize_window"].triggered.connect(self._toggle_maximized)
        self.actions["reset_window_size"].triggered.connect(self._reset_window_size)
        self.actions["reset_workspace_layout"].triggered.connect(
            self._reset_workspace_layout
        )
        self.actions["about"].triggered.connect(self.show_about)

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        for key in (
            "new",
            "open",
            "save",
            "save_as",
            "save_template",
        ):
            file_menu.addAction(self.actions[key])
        self.import_menu = file_menu.addMenu("Import")
        for key in (
            "import_svg",
            "import_gcode",
            "import_lightburn",
            "import_image",
        ):
            self.import_menu.addAction(self.actions[key])
        file_menu.addSeparator()
        file_menu.addAction(self.actions["quit"])

        edit_menu = self.menuBar().addMenu("&Edit")
        for key in ("undo", "redo", "duplicate", "delete", "select_all"):
            edit_menu.addAction(self.actions[key])
        edit_menu.addSeparator()
        edit_menu.addAction(self.actions["group"])
        edit_menu.addAction(self.actions["ungroup"])

        tools_menu = self.menuBar().addMenu("&Tools")
        for key in ("rectangle", "ellipse", "line", "text"):
            tools_menu.addAction(self.actions[key])
        tools_menu.addSeparator()
        tools_menu.addAction(self.actions["grid_template_designer"])
        tools_menu.addAction(self.actions["trace_objects"])
        tools_menu.addAction(self.actions["template_alignment"])
        tools_menu.addSeparator()
        tools_menu.addAction(self.actions["refresh_camera"])
        tools_menu.addAction(self.actions["machine_manager"])
        tools_menu.addAction(self.actions["machine_setup"])

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

        laser_menu = self.menuBar().addMenu("&Laser Tools")
        for key in (
            "generate",
            "optimize_paths",
            "preview_job",
            "export_gcode",
            "stop",
        ):
            laser_menu.addAction(self.actions[key])

        self.window_menu = self.menuBar().addMenu("&Window")
        self.window_menu.addAction(self.actions["minimize_window"])
        self.window_menu.addAction(self.actions["maximize_window"])
        self.window_menu.addAction(self.actions["reset_window_size"])
        self.window_menu.addAction(self.actions["reset_workspace_layout"])
        self.window_menu.addSeparator()
        for key in ("fit", "fit_selection", "zoom_in", "zoom_out", "snap"):
            self.window_menu.addAction(self.actions[key])
        self.window_menu.addSeparator()

        self.help_menu = self.menuBar().addMenu("&Help")
        self.help_menu.addAction(self.actions["setup_guide"])
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.actions["about"])

    def _create_toolbars(self) -> None:
        machine_toolbar = self.addToolBar("Machine")
        machine_toolbar.setObjectName("machineToolbar")
        machine_toolbar.setIconSize(QtCore.QSize(20, 20))
        machine_toolbar.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        machine_label = QtWidgets.QLabel("Machine:")
        machine_label.setToolTip(
            "Running machine and the machine selected for the next E3 launch"
        )
        machine_toolbar.addWidget(machine_label)
        self._updating_machine_selector = False
        self.machine_selector = QtWidgets.QComboBox()
        self.machine_selector.setObjectName("machineSelector")
        self.machine_selector.setMinimumWidth(250)
        self.machine_selector.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.machine_selector.activated.connect(self._machine_selector_activated)
        machine_toolbar.addWidget(self.machine_selector)
        machine_toolbar.addAction(self.actions["machine_manager"])
        self._refresh_machine_selector()

        file_toolbar = self.addToolBar("File")
        file_toolbar.setObjectName("fileToolbar")
        file_toolbar.setIconSize(QtCore.QSize(20, 20))
        file_toolbar.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        for key in ("new", "open", "save", "import_svg"):
            file_toolbar.addAction(self.actions[key])

        edit_toolbar = self.addToolBar("Edit")
        edit_toolbar.setObjectName("editToolbar")
        edit_toolbar.setIconSize(QtCore.QSize(20, 20))
        edit_toolbar.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        for key in ("undo", "redo", "duplicate", "delete", "group", "ungroup"):
            edit_toolbar.addAction(self.actions[key])

        tools = QtWidgets.QToolBar("Drawing tools", self)
        tools.setObjectName("drawingToolbar")
        tools.setOrientation(QtCore.Qt.Orientation.Vertical)
        tools.setIconSize(QtCore.QSize(22, 22))
        tools.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        tools.setMinimumWidth(40)
        tools.setMaximumWidth(44)
        self.addToolBar(QtCore.Qt.ToolBarArea.LeftToolBarArea, tools)

        for key in ("select_tool", "line", "rectangle", "ellipse", "text"):
            tools.addAction(self.actions[key])
        tools.addSeparator()
        for key in (
            "grid_template_designer",
            "trace_objects",
            "template_alignment",
        ):
            tools.addAction(self.actions[key])
        tools.addSeparator()
        for key in ("fit", "fit_selection", "zoom_in", "zoom_out"):
            tools.addAction(self.actions[key])

        snap_button = QtWidgets.QToolButton()
        snap_button.setDefaultAction(self.actions["snap"])
        snap_button.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.MenuButtonPopup
        )
        snap_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        snap_menu = QtWidgets.QMenu(snap_button)
        snap_group = QtGui.QActionGroup(snap_menu)
        snap_group.setExclusive(True)
        for step in (0.1, 0.5, 1.0, 2.0, 5.0, 10.0):
            step_action = snap_menu.addAction(f"{step:g} mm grid")
            step_action.setCheckable(True)
            step_action.setChecked(step == 1.0)
            step_action.triggered.connect(
                lambda checked=False, value=step: self.workspace.set_snap_step(value)
            )
            snap_group.addAction(step_action)
        snap_button.setMenu(snap_menu)
        tools.addWidget(snap_button)

        arrange_toolbar = self.addToolBar("Arrange")
        arrange_toolbar.setObjectName("arrangeToolbar")
        arrange_toolbar.setIconSize(QtCore.QSize(20, 20))

        def arrange_menu_button(
            title: str,
            action_keys: tuple[str, ...],
        ) -> QtWidgets.QToolButton:
            button = QtWidgets.QToolButton()
            button.setText(title)
            button.setIcon(action_icon(title.lower(), size=20))
            button.setIconSize(QtCore.QSize(20, 20))
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setToolTip(f"{title} selected objects")
            button.setAccessibleName(f"{title} selected objects")
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
        job_toolbar.setIconSize(QtCore.QSize(20, 20))
        job_toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly)
        job_toolbar.addAction(self.actions["refresh_camera"])
        job_toolbar.addSeparator()
        for key in ("generate", "preview_job"):
            job_toolbar.addAction(self.actions[key])

        self.safety_toolbar = QtWidgets.QToolBar("Runtime and safety", self)
        self.safety_toolbar.setObjectName("safetyToolbar")
        self.safety_toolbar.setMovable(False)
        self.safety_toolbar.setFloatable(False)
        self.safety_toolbar.setAllowedAreas(
            QtCore.Qt.ToolBarArea.TopToolBarArea
        )
        self.safety_toolbar.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.PreventContextMenu
        )
        self.safety_toolbar.toggleViewAction().setEnabled(False)
        self.runtime_strip = RuntimeSafetyStrip(self)
        self.runtime_strip.set_chrome_mode(True)
        self.safety_toolbar.addWidget(self.runtime_strip)
        self.addToolBar(
            QtCore.Qt.ToolBarArea.TopToolBarArea,
            self.safety_toolbar,
        )
        self._safety_on_own_row = False

        self.addToolBarBreak(QtCore.Qt.ToolBarArea.TopToolBarArea)
        context_toolbar = QtWidgets.QToolBar("Selection properties", self)
        context_toolbar.setObjectName("contextToolbar")
        context_toolbar.setMovable(False)
        context_toolbar.setFloatable(False)
        self.context_bar = ContextPropertyBar(self)
        context_toolbar.addWidget(self.context_bar)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, context_toolbar)

        self.stock_layout_toolbar = StockLayoutToolBar(self)
        self.addToolBar(
            QtCore.Qt.ToolBarArea.TopToolBarArea,
            self.stock_layout_toolbar,
        )

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
        machine_profile_id, tool_head_profile_id = (
            E3MainWindow._running_material_profile_ids(self)
        )
        self.material_panel = MaterialPanel(
            self.material_database,
            machine_profile_id=machine_profile_id,
            tool_head_profile_id=tool_head_profile_id,
        )
        self.console_panel = ConsolePanel()
        # Design, camera, machine, and material controls share one full-height
        # sidebar so the central bed/camera workspace can use the full window
        # height. Exact job review remains in its dedicated modal Preview.
        self.inspector_tabs = InspectorTabs(self)
        self.inspector_tabs.setTabPosition(QtWidgets.QTabWidget.TabPosition.South)
        self.inspector_tabs.add_panel(
            "layers", "Cuts", self.layer_panel, tooltip="Cuts / Layers"
        )
        self.inspector_tabs.add_panel(
            "camera", "Camera", self.camera_panel, tooltip="Camera controls"
        )
        self.inspector_tabs.add_panel("objects", "Objects", self.object_panel)
        self.inspector_tabs.add_panel(
            "transform", "Shape", self.transform_panel, tooltip="Shape Properties"
        )
        self.inspector_tabs.add_panel("templates", "Templates", self.template_panel)
        self.inspector_tabs.add_panel("trace", "Trace", self.trace_panel)
        self.inspector_tabs.add_panel("machine", "Machine", self.machine_panel)
        self.inspector_tabs.add_panel(
            "materials",
            "Material Recipes",
            self.material_panel,
        )

        right = QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        bottom = QtCore.Qt.DockWidgetArea.BottomDockWidgetArea
        self.setCorner(QtCore.Qt.Corner.TopRightCorner, right)
        self.setCorner(QtCore.Qt.Corner.BottomRightCorner, right)
        self.setCorner(QtCore.Qt.Corner.BottomLeftCorner, bottom)
        self.layer_dock = self._dock(
            "Cuts / Layers",
            "layersDock",
            self.inspector_tabs,
            right,
        )
        self.layer_dock.setMinimumWidth(_DESIGN_DOCK_MIN_WIDTH)
        self.console_dock = self._dock(
            "Console",
            "consoleDock",
            self.console_panel,
            bottom,
        )
        self.resizeDocks(
            [self.layer_dock],
            [_DESIGN_DOCK_DEFAULT_WIDTH],
            QtCore.Qt.Orientation.Horizontal,
        )
        self.layer_dock.raise_()
        self.console_dock.hide()

        for dock in (
            self.layer_dock,
            self.console_dock,
        ):
            self.window_menu.addAction(dock.toggleViewAction())

    def _create_status_bar(self) -> None:
        status_bar = self.statusBar()
        self.direct_edit_label = QtWidgets.QLabel("Move  •  Size  •  Rotate")
        self.direct_edit_label.setObjectName("directEditStatus")
        self.direct_edit_label.setToolTip(
            "Drag an object to move it; use its corner and rotation handles for "
            "direct editing. Hold Shift while rotating for 15° snapping."
        )
        self.cursor_label = QtWidgets.QLabel("X —  Y —")
        self.selection_label = QtWidgets.QLabel("0 objects selected")
        self.job_progress = JobProgressWidget(self)
        self._job_progress_maximum_width = self.job_progress.maximumWidth()
        self.zoom_label = QtWidgets.QLabel("Zoom —")
        self.runtime_label = QtWidgets.QLabel("Starting core services…")
        status_bar.setMinimumHeight(self.job_progress.height() + 6)
        status_bar.addWidget(self.direct_edit_label)
        status_bar.addWidget(self.cursor_label)
        status_bar.addWidget(self.selection_label)
        status_bar.addPermanentWidget(self.job_progress, 1)
        status_bar.addPermanentWidget(self.zoom_label)
        status_bar.addPermanentWidget(self.runtime_label)
        status_bar.messageChanged.connect(
            lambda _message: self._update_status_bar_layout()
        )
        self._update_status_bar_layout()

    def _connect_signals(self) -> None:
        self.workspace.cursorPositionChanged.connect(self._set_cursor_status)
        self.workspace.zoomChanged.connect(self._set_zoom_status)
        self.workspace.selectionIdsChanged.connect(self._selection_changed)
        self.workspace.objectMoveCommitted.connect(self._object_moved)
        self.workspace.objectTransformCommitted.connect(
            self._object_transform_committed
        )
        self.workspace.templatePlacementEdited.connect(
            self._template_canvas_placement_edited
        )
        self.workspace.templatePlacementCommitted.connect(
            self._template_canvas_placement_committed
        )
        self.workspace.deleteRequested.connect(self.delete_selection)
        self.workspace.pointPicked.connect(self._trace_point_picked)
        self.workspace.creationToolChanged.connect(self._creation_tool_changed)
        self.workspace.rectangleDraftChanged.connect(self._rectangle_draft_changed)
        self.workspace.rectangleDrawCommitted.connect(
            self._rectangle_draw_committed
        )

        self.palette.layerSelected.connect(self._palette_layer_selected)
        self.palette.addLayerRequested.connect(self.add_layer)
        self.palette.presetLayerRequested.connect(self.add_palette_layer)
        self.layer_panel.activeLayerChanged.connect(self.set_active_layer)
        # Layer table checkbox edits originate inside QTreeWidget::itemChanged.
        # Updating the project synchronously rebuilds that same tree, so defer
        # the edit until Qt has returned from the native itemChanged signal.
        self.layer_panel.layerEdited.connect(
            self._layer_edited,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.layer_panel.addLayerRequested.connect(self.add_layer)
        self.layer_panel.removeLayerRequested.connect(self.remove_layer)
        self.layer_panel.moveLayerRequested.connect(self.move_layer)

        self.object_panel.selectionRequested.connect(self.workspace.select_objects)
        # Editing a tree item executes a history command, whose synchronous
        # refresh rebuilds this same tree.  Queue the edit until Qt has returned
        # from QTreeWidget::itemChanged so the emitting item is never deleted
        # while its native signal frame is still active.
        self.object_panel.objectEdited.connect(
            self._object_edited,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.object_panel.layerColorEditRequested.connect(
            self.layer_panel.choose_color
        )
        self.object_panel.rasterVectorizeRequested.connect(
            self.vectorize_raster_image
        )
        self.transform_panel.transformEdited.connect(self._transform_edited)
        self.transform_panel.rectangleShapeEdited.connect(
            self._rectangle_shape_edited
        )
        self.transform_panel.assignLayerRequested.connect(self._assign_layer)
        self.transform_panel.straightenRequested.connect(
            self._straighten_selected_trace_objects
        )
        self.context_bar.transformEdited.connect(self._transform_edited)
        self.context_bar.rectangleShapeEdited.connect(
            self._rectangle_shape_edited
        )
        self.stock_layout_toolbar.centerHorizontalRequested.connect(
            lambda: self._center_selection_in_stock(horizontal=True)
        )
        self.stock_layout_toolbar.centerVerticalRequested.connect(
            lambda: self._center_selection_in_stock(vertical=True)
        )
        self.stock_layout_toolbar.centerBothRequested.connect(
            lambda: self._center_selection_in_stock(
                horizontal=True,
                vertical=True,
            )
        )
        self.stock_layout_toolbar.snapRotationRequested.connect(
            self._snap_selection_rotation_to_stock
        )
        self.stock_layout_toolbar.fitRequested.connect(
            self._fit_selection_to_stock
        )
        self.stock_layout_toolbar.customMarginRequested.connect(
            self._set_custom_stock_margin
        )

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
        self.trace_panel.generateRequested.connect(self.actions["generate"].trigger)
        self.trace_panel.selectionChanged.connect(
            self._trace_selection_changed
        )
        self.trace_panel.rasterPreviewModeChanged.connect(
            self._trace_raster_preview_mode_changed
        )
        self.workspace.traceSelectionIdsChanged.connect(
            self._trace_canvas_selection_changed
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
        self.template_panel.generateRequested.connect(
            self.actions["generate"].trigger
        )
        self.template_panel.clearRequested.connect(self._clear_template_preview)

        self.camera_panel.refreshRequested.connect(self.controller.retry_camera_image)
        self.camera_panel.monitorRequested.connect(self.open_live_monitor)
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
        self.camera_panel.focusSweepRequested.connect(
            self.controller.test_camera_focus_range
        )
        self.camera_panel.lensCalibrationRequested.connect(
            lambda: self.open_machine_setup(1)
        )
        self.camera_panel.bedCalibrationRequested.connect(
            lambda: self.open_machine_setup(2)
        )

        self.machine_panel.parkRequested.connect(self.controller.park_at_camera_pose)
        self.machine_panel.jogRequested.connect(self.controller.jog)
        self.console_panel.commandSubmitted.connect(self.controller.send_diagnostic)
        self.material_panel.applyPresetRequested.connect(self.apply_material_preset)
        self.material_panel.notice.connect(self.show_notice)
        self.material_panel.error.connect(self.show_error)

        self.runtime_strip.connectRequested.connect(self.controller.connect_machine)
        self.runtime_strip.reconnectRequested.connect(
            self.controller.reconnect_machine
        )
        self.runtime_strip.disconnectRequested.connect(
            self.controller.disconnect_machine
        )
        self.runtime_strip.pauseRequested.connect(self.controller.pause_resume)
        self.runtime_strip.stopRequested.connect(self.controller.emergency_stop)
        self.actions["generate"].changed.connect(self._sync_generate_controls)
        self._sync_generate_controls()

        self.controller.statusChanged.connect(self._runtime_status)
        self.controller.cameraImageReady.connect(self._camera_image_ready)
        self.controller.cameraImageInvalidated.connect(
            self._camera_image_invalidated
        )
        self.controller.cameraFocusChanged.connect(
            self._camera_focus_changed
        )
        self.controller.traceResultReady.connect(
            self._trace_result_ready
        )
        self.controller.traceRasterPreviewReady.connect(
            self._trace_raster_preview_ready,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.controller.traceDetectionFailed.connect(
            self._trace_detection_failed
        )
        self.controller.traceColorReady.connect(
            self._trace_color_ready
        )
        self.controller.traceColorFailed.connect(
            self._trace_color_failed
        )
        self.controller.templateMatchReady.connect(
            self._template_match_ready
        )
        self.controller.reviewEvidenceInvalidated.connect(
            self._calibration_review_evidence_invalidated
        )
        self.controller.errorOccurred.connect(self.show_error)
        self.controller.cameraErrorOccurred.connect(self.show_camera_error)
        self.controller.cameraMappingRequired.connect(
            self.show_camera_mapping_required
        )
        self.controller.cameraOverlayErrorOccurred.connect(
            self.show_camera_overlay_error
        )
        self.controller.notice.connect(self.show_notice)
        self.controller.busyChanged.connect(self._busy_changed)
        self.controller.stopInitiated.connect(self._software_stop_started)
        self.controller.jobStarted.connect(self._job_started)
        self.controller.tasksDrained.connect(self._background_tasks_drained)

    def _refresh_document(self, selected_ids: list[str] | None = None) -> None:
        self.controller.set_workspace_coordinate_space(
            self.document.coordinate_space.value
        )
        if not any(layer.id == self.active_layer_id for layer in self.document.layers):
            self.active_layer_id = self.document.active_layer_id
        if self.workspace.creation_tool == "rectangle":
            self.workspace.set_creation_color(
                self.document.get_layer(self.active_layer_id).color
            )
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

    def _set_cursor_status(self, x: float, y: float) -> None:
        prefix = (
            "Honeycomb "
            if self.document.coordinate_space is CoordinateSpace.HONEYCOMB_LOCAL
            else ""
        )
        self.cursor_label.setText(f"{prefix}X {x:8.3f}  Y {y:8.3f} mm")
        self._update_status_bar_layout()

    def _set_zoom_status(self, zoom: float) -> None:
        self.zoom_label.setText(f"Zoom {zoom * 100:.0f}%")
        self._update_status_bar_layout()

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
        if not objects:
            selection_text = "0 objects selected"
        else:
            bounds = objects[0].bounds()
            for item in objects[1:]:
                bounds = bounds.union(item.bounds())
            selection_text = (
                f"{count} object{'s' if count != 1 else ''} selected · "
                f"{bounds.width:.3f} × {bounds.height:.3f} mm"
            )
        self.selection_label.setText(selection_text)
        self._update_status_bar_layout()
        self.object_panel.set_selection(object_ids)
        self.transform_panel.set_selection(objects, self.document)
        self.context_bar.set_selection(objects, self.document)
        layout_selection_count = sum(
            1
            for item in objects
            if not is_stock_boundary(item) and not item.locked
        )
        self.stock_layout_toolbar.set_context(
            has_stock=bool(stock_boundaries(self.document)),
            selection_count=layout_selection_count,
        )
        self._update_selected_trace_orientation(objects)

    def _history_changed(self, stack: CommandStack) -> None:
        if getattr(self, "_job_preparation_busy", False):
            self._cancel_job_preparation(
                "Project changed; discarded the unfinished job preparation"
            )
        blocked_preflight = getattr(self, "_job_preflight_dialog", None)
        if blocked_preflight is not None:
            self._job_preflight_dialog = None
            self.last_job_preflight_report = None
            blocked_preflight.close()
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

    def _invalidate_generated_job(self, *, cancel_preparation: bool = True) -> None:
        self._pending_calibration_capture = None
        if cancel_preparation and getattr(self, "_job_preparation_busy", False):
            self._cancel_job_preparation(
                "Project changed; discarded the unfinished job preparation"
            )
        cancel_render = getattr(self, "_cancel_job_render", None)
        if cancel_render is not None:
            cancel_render()
        self.last_job = None
        self.last_job_name = ""
        self.last_job_powered = False
        self.last_job_revision = None
        self.last_job_work_area = None
        self.last_job_coordinate_frame = None
        self.last_job_preview_data = None
        self.last_job_preflight_report = None
        preflight_dialog = getattr(self, "_job_preflight_dialog", None)
        if preflight_dialog is not None:
            preflight_dialog.close()
            self._job_preflight_dialog = None
        preview_dialog = getattr(self, "_job_preview_dialog", None)
        if preview_dialog is not None:
            preview_dialog.close()
            self._job_preview_dialog = None
        if hasattr(self, "workspace"):
            self.workspace.clear_toolpath_preview()
        clear_prepared_job = getattr(
            getattr(self, "job_progress", None), "clear_prepared_job", None
        )
        if clear_prepared_job is not None:
            clear_prepared_job()
        if hasattr(self, "actions") and "export_gcode" in self.actions:
            self.actions["export_gcode"].setEnabled(False)
            if "preview_job" in self.actions:
                self.actions["preview_job"].setEnabled(False)

    def _document_center(self) -> tuple[float, float]:
        return self.document.work_area.center

    def set_active_layer(self, layer_id: str) -> None:
        layer = self.document.get_layer(layer_id)
        self.active_layer_id = layer_id
        if self.workspace.creation_tool == "rectangle":
            self.workspace.set_creation_color(layer.color)
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
        machine_profile_id, tool_head_profile_id = (
            E3MainWindow._running_material_profile_ids(self)
        )
        compatibility = preset.compatibility(
            machine_profile_id=machine_profile_id,
            tool_head_profile_id=tool_head_profile_id,
        )
        if not compatibility.can_apply:
            self.show_error(
                f"Cannot apply {preset.material} \N{MIDDLE DOT} {preset.name}: "
                f"the recipe is {compatibility.label.lower()} for the running "
                "machine and tool-head profiles."
            )
            return
        layer = self.document.get_layer(self.active_layer_id)
        replacement = preset.apply_to_layer(
            layer,
            machine_profile_id=machine_profile_id,
            tool_head_profile_id=tool_head_profile_id,
        )
        self.history.execute(
            UpdateLayerCommand(
                self.document,
                layer.id,
                replacement,
                description=f"Apply {preset.material} recipe",
            )
        )
        self._refresh_document(self.workspace.selected_object_ids())
        self.show_notice(
            f"Applied {preset.material} · {preset.name} to {layer.name}"
        )

    def add_layer(self) -> None:
        self._create_layer()

    def add_palette_layer(self, color: str) -> None:
        self._create_layer(color=str(color))

    def _create_layer(self, color: str | None = None) -> None:
        selected = self.workspace.selected_object_ids()
        index = len(self.document.layers)
        layer = OperationLayer(
            name=f"Layer {index + 1:02d}",
            color=color or self.document.next_layer_color(),
            priority=index,
        )
        self.history.execute(AddLayerCommand(self.document, layer))
        self.active_layer_id = layer.id
        self._refresh_document()
        if selected:
            self._assign_layer(selected, layer.id)
            self.show_notice(
                f"Created {layer.name} and assigned {len(selected)} selected object"
                f"{'s' if len(selected) != 1 else ''}"
            )

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

    def _activate_selection_tool(self, *, show_message: bool = True) -> None:
        self.actions["select_tool"].setChecked(True)
        self.workspace.set_creation_tool(None)
        self.workspace.cancel_point_pick()
        self.workspace.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        if show_message:
            self.show_notice("Selection tool active")

    def add_rectangle(self) -> None:
        layer = self.document.get_layer(self.active_layer_id)
        self.actions["rectangle"].setChecked(True)
        self.workspace.set_creation_tool("rectangle", color=layer.color)
        self.show_notice(
            "Rectangle tool: drag between opposite corners; choose Select or "
            "right-click the canvas to finish."
        )

    def _creation_tool_changed(self, tool: str) -> None:
        drawing_rectangle = tool == "rectangle"
        self.actions["rectangle"].setChecked(drawing_rectangle)
        self.actions["select_tool"].setChecked(not drawing_rectangle)
        if drawing_rectangle:
            self.direct_edit_label.setText("Rectangle | drag to draw")
            self.direct_edit_label.setToolTip(
                "Drag between opposite corners. The active operation color is "
                "used; choose Select or right-click the canvas to finish."
            )
        else:
            self.direct_edit_label.setText("Move | Size | Rotate")
            self.direct_edit_label.setToolTip(
                "Drag an object to move it; use its corner and rotation handles "
                "for direct editing. Hold Shift while rotating for 15-degree snapping."
            )
        self._update_status_bar_layout()

    def _rectangle_draft_changed(self, bounds: Bounds | None) -> None:
        if bounds is None:
            if self.workspace.creation_tool == "rectangle":
                self.direct_edit_label.setText("Rectangle | drag to draw")
                self._update_status_bar_layout()
            return
        self.direct_edit_label.setText(
            f"Rectangle | W {bounds.width:.3f}  H {bounds.height:.3f} mm"
        )
        self._update_status_bar_layout()

    def _rectangle_draw_committed(
        self,
        center_x_mm: float,
        center_y_mm: float,
        width_mm: float,
        height_mm: float,
    ) -> None:
        self._add_object(
            SceneObject.rectangle(
                self.active_layer_id,
                center=(center_x_mm, center_y_mm),
                width_mm=width_mm,
                height_mm=height_mm,
                corner_radius_mm=0.0,
            ),
            "Add rectangle",
        )
        self.show_notice(
            f"Rectangle created: {width_mm:.3f} x {height_mm:.3f} mm"
        )

    def add_ellipse(self) -> None:
        self._activate_selection_tool(show_message=False)
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
        self._activate_selection_tool(show_message=False)
        self._add_object(
            SceneObject.line(
                self.active_layer_id,
                center=self._document_center(),
                length_mm=40.0,
            ),
            "Add line",
        )

    def add_text(self) -> None:
        self._activate_selection_tool(show_message=False)
        dialog = VectorTextDialog(self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        try:
            item = create_vector_text_object(
                self.active_layer_id,
                dialog.options(),
                center=self._document_center(),
            )
        except Exception as exc:
            self.show_error(f"Could not create vector text: {exc}")
            return
        self._add_object(item, "Add vector text")
        if item.metadata.get("text_vector_mode") == "stencil":
            self.show_notice(
                "Created stencil-safe vector text with "
                f"{int(item.metadata.get('text_bridge_count', 0))} material "
                "bridge"
                f"{'s' if int(item.metadata.get('text_bridge_count', 0)) != 1 else ''}"
            )

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

    def _object_transform_committed(
        self,
        object_id: str,
        before: Transform,
        after: Transform,
    ) -> None:
        item = self.document.get_object(object_id)
        if item.transform.to_dict() != before.to_dict():
            self.workspace.refresh_object(object_id)
            self.show_notice("The object changed; direct transform was cancelled")
            return
        if item.kind == ObjectKind.RECTANGLE:
            radius = min(
                float(item.geometry.get("corner_radius_mm", 0.0)),
                min(after.width_mm, after.height_mm) / 2.0,
            )
            self._rectangle_shape_edited(object_id, after, radius)
            return
        self._transform_edited(object_id, after)

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

    def _center_selection_in_stock(
        self,
        *,
        horizontal: bool = False,
        vertical: bool = False,
    ) -> None:
        selected = self.workspace.selected_object_ids()
        try:
            transforms = center_selection_on_stock(
                self.document,
                selected,
                horizontal=horizontal,
                vertical=vertical,
            )
        except ValueError as exc:
            self.show_notice(str(exc))
            return
        description = (
            "Center selection in stock"
            if horizontal and vertical
            else "Center selection horizontally in stock"
            if horizontal
            else "Center selection vertically in stock"
        )
        self.history.execute(
            UpdateTransformsCommand(
                self.document,
                transforms,
                description=description,
            )
        )
        self.workspace.select_objects(selected)
        self.show_notice(description)

    def _snap_selection_rotation_to_stock(self, edge_mode: str) -> None:
        selected = self.workspace.selected_object_ids()
        try:
            transforms, edge = snap_selection_rotation_to_stock(
                self.document,
                selected,
                edge_mode=edge_mode,
            )
        except ValueError as exc:
            self.show_notice(str(exc))
            return
        self.history.execute(
            UpdateTransformsCommand(
                self.document,
                transforms,
                description="Snap rotation to stock edge",
            )
        )
        self.workspace.select_objects(selected)
        self.show_notice(
            f"Rotated selection parallel to the {edge_mode} stock edge "
            f"({edge.angle_deg:.2f}°)"
        )

    def _fit_selection_to_stock(self, margin_mm: float) -> None:
        selected = self.workspace.selected_object_ids()
        try:
            transforms = fit_selection_to_stock(
                self.document,
                selected,
                margin_mm=margin_mm,
            )
        except ValueError as exc:
            self.show_notice(str(exc))
            return
        self.history.execute(
            UpdateTransformsCommand(
                self.document,
                transforms,
                description=f"Fit selection to stock with {margin_mm:g} mm margin",
            )
        )
        self.workspace.select_objects(selected)
        self.show_notice(
            f"Fit selection inside the stock boundary with a {margin_mm:g} mm margin"
        )

    def _set_custom_stock_margin(self) -> None:
        margin, accepted = QtWidgets.QInputDialog.getDouble(
            self,
            "Fit to stock margin",
            "Uncut edge margin:",
            self.stock_layout_toolbar.fit_margin_mm,
            0.0,
            1000.0,
            2,
        )
        if not accepted:
            return
        self.stock_layout_toolbar.set_fit_margin(margin)
        self._fit_selection_to_stock(margin)

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
            SVG_FILE_DIALOG_FILTER,
        )
        if not filename:
            return
        try:
            manifest = scan_svg_file(filename)
        except Exception as exc:
            self.show_error(f"Could not inspect SVG: {exc}")
            return
        if (
            not review_import_manifest(manifest, self)
            or not manifest.ready_for_parse
        ):
            return
        try:
            result = load_svg_project(
                filename,
                expected_source_sha256=manifest.source_sha256,
            )
            geometry = result.geometry
            polylines = [
                {
                    "points": [[float(x), float(-y)] for x, y in line.points],
                    "closed": line.closed,
                }
                for line in geometry.physical_polylines()
            ]
            item = SceneObject.path(
                self.active_layer_id,
                polylines,
                name=Path(result.source_name).stem,
                center=self._document_center(),
                source_name=result.source_name,
                source_svg=result.source_text,
            )
            self._add_object(item, "Import SVG")
        except Exception as exc:
            self.show_error(f"Could not import SVG: {exc}")

    def import_gcode(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import G-code",
            str(Path.home()),
            GCODE_FILE_DIALOG_FILTER,
        )
        if not filename:
            return
        try:
            manifest = scan_gcode_file(filename)
        except Exception as exc:
            self.show_error(f"Could not inspect G-code: {exc}")
            return
        if (
            not review_import_manifest(manifest, self)
            or not manifest.ready_for_parse
        ):
            return
        try:
            result = load_gcode_project(
                filename,
                center=self._document_center(),
                expected_source_sha256=manifest.source_sha256,
            )
            layer_start = len(self.document.layers)
            for offset, layer in enumerate(result.layers):
                layer.priority = layer_start + offset
            layer_commands = [
                AddLayerCommand(
                    self.document,
                    layer,
                    index=layer_start + offset,
                    description="Import G-code layer",
                )
                for offset, layer in enumerate(result.layers)
            ]
            object_command = AddObjectsCommand(
                self.document,
                result.objects,
                description="Import G-code objects",
            )
            previous_active_layer_id = self.active_layer_id
            imported_layer_id = result.layers[0].id

            def redo_import() -> None:
                for command in layer_commands:
                    command.redo()
                object_command.redo()
                self.active_layer_id = imported_layer_id

            def undo_import() -> None:
                object_command.undo()
                for command in reversed(layer_commands):
                    command.undo()
                self.active_layer_id = previous_active_layer_id

            self.history.execute(
                FunctionalCommand(
                    "Import G-code",
                    redo_import,
                    undo_import,
                )
            )
        except Exception as exc:
            self.show_error(f"Could not import G-code: {exc}")
            return

        self._activate_selection_tool(show_message=False)
        object_ids = [item.id for item in result.objects]
        self.workspace.select_objects(object_ids)
        if result.warnings:
            details = "\n".join(f"• {warning}" for warning in result.warnings[:12])
            if len(result.warnings) > 12:
                details += f"\n• …and {len(result.warnings) - 12} more warning(s)"
            QtWidgets.QMessageBox.warning(
                self,
                "G-code import review required",
                "The design was imported, but these items need review:\n\n" + details,
            )
        self.show_notice(
            f"Imported {len(result.objects)} G-code operation object"
            f"{'s' if len(result.objects) != 1 else ''} on {len(result.layers)} "
            f"output-disabled layer{'s' if len(result.layers) != 1 else ''}; "
            "review every speed and power value before enabling output"
        )

    def import_lightburn(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import LightBurn project",
            str(Path.home()),
            LIGHTBURN_FILE_DIALOG_FILTER,
        )
        if not filename:
            return
        try:
            manifest = scan_lightburn_file(filename)
        except Exception as exc:
            self.show_error(f"Could not inspect LightBurn project: {exc}")
            return
        if (
            not review_import_manifest(manifest, self)
            or not manifest.ready_for_parse
        ):
            return
        try:
            result = load_lightburn_project(
                filename,
                center=self._document_center(),
                expected_source_sha256=manifest.source_sha256,
            )
            layer_start = len(self.document.layers)
            for offset, layer in enumerate(result.layers):
                layer.priority = layer_start + offset
            layer_commands = [
                AddLayerCommand(
                    self.document,
                    layer,
                    index=layer_start + offset,
                    description="Import LightBurn layer",
                )
                for offset, layer in enumerate(result.layers)
            ]
            object_command = AddObjectsCommand(
                self.document,
                result.objects,
                description="Import LightBurn objects",
            )
            previous_active_layer_id = self.active_layer_id
            imported_layer_id = result.layers[0].id

            def redo_import() -> None:
                for command in layer_commands:
                    command.redo()
                object_command.redo()
                self.active_layer_id = imported_layer_id

            def undo_import() -> None:
                object_command.undo()
                for command in reversed(layer_commands):
                    command.undo()
                self.active_layer_id = previous_active_layer_id

            self.history.execute(
                FunctionalCommand(
                    "Import LightBurn project",
                    redo_import,
                    undo_import,
                )
            )
        except Exception as exc:
            self.show_error(f"Could not import LightBurn project: {exc}")
            return

        self._activate_selection_tool(show_message=False)
        object_ids = [item.id for item in result.objects]
        self.workspace.select_objects(object_ids)
        if result.warnings:
            details = "\n".join(f"• {warning}" for warning in result.warnings[:12])
            if len(result.warnings) > 12:
                details += f"\n• …and {len(result.warnings) - 12} more warning(s)"
            QtWidgets.QMessageBox.warning(
                self,
                "LightBurn import review required",
                "The project was imported, but these items need review:\n\n" + details,
            )
        self.show_notice(
            f"Imported {len(result.objects)} LightBurn object"
            f"{'s' if len(result.objects) != 1 else ''} on {len(result.layers)} "
            f"output-disabled layer{'s' if len(result.layers) != 1 else ''}; "
            "review every setting before enabling output"
        )

    def import_image(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import raster image",
            str(Path.home()),
            RASTER_FILE_DIALOG_FILTER,
        )
        if not filename:
            return
        try:
            manifest = scan_raster_file(filename)
        except Exception as exc:
            self.show_error(f"Could not inspect raster image: {exc}")
            return
        if (
            not review_import_manifest(manifest, self)
            or not manifest.ready_for_parse
        ):
            return
        try:
            payload = read_raster_asset_payload(
                filename,
                expected_source_sha256=manifest.source_sha256,
            )
        except Exception as exc:
            self.show_error(f"Could not import raster image: {exc}")
            return
        metadata = payload.metadata
        image_size = QtCore.QSize(metadata.width, metadata.height)
        layer = self.document.get_layer(self.active_layer_id)
        if layer.mode != LayerMode.RASTER:
            layer = OperationLayer(
                name=f"Raster {len(self.document.layers) + 1:02d}",
                color=self.document.next_layer_color(),
                mode=LayerMode.RASTER,
                priority=len(self.document.layers),
                output_enabled=False,
            )
            self.history.execute(
                AddLayerCommand(self.document, layer, description="Add raster layer")
            )
            self.active_layer_id = layer.id
        width_mm = min(80.0, self.document.work_area.width * 0.5)
        height_mm = width_mm * image_size.height() / image_size.width()
        if height_mm > self.document.work_area.height * 0.5:
            height_mm = self.document.work_area.height * 0.5
            width_mm = height_mm * image_size.width() / image_size.height()
        item = SceneObject(
            name=Path(filename).stem,
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(
                *self._document_center(),
                width_mm=width_mm,
                height_mm=height_mm,
            ),
            geometry={"asset": str(Path(filename).resolve())},
        )
        self._add_object(item, "Import raster image")
        self.show_notice(
            "Imported grayscale raster image with deterministic ordered dithering"
        )

    def vectorize_raster_image(self, object_id: str) -> None:
        """Open the offline raster-to-vector review for one selected image."""

        selected = self.workspace.selected_object_ids()
        if selected != [object_id]:
            self.show_error("Select exactly one raster image to trace to vectors")
            return
        try:
            source = self.document.get_object(object_id)
        except KeyError:
            self.show_error("The selected raster image no longer exists")
            return
        if source.kind is not ObjectKind.IMAGE:
            self.show_error("Select exactly one raster image to trace to vectors")
            return

        asset = str(source.geometry.get("asset", "")).strip()
        if not asset:
            self.show_error("The selected raster image has no source asset")
            return
        preview_identity = self.workspace.raster_preview_identity_for_object(
            object_id
        )
        if preview_identity is None:
            self.show_error(
                "The selected raster source is missing, unreadable, or has no "
                "identity-verified canvas preview"
            )
            return
        preview_path, preview_sha256 = preview_identity
        try:
            payload = read_raster_asset_payload(
                asset,
                expected_source_sha256=preview_sha256,
            )
        except Exception as exc:
            self.show_error(
                "Could not open Raster Vectorization because the exact source no "
                f"longer matches its canvas preview: {exc}"
            )
            return
        if payload.identity.path != preview_path:
            self.show_error(
                "The raster canvas preview and selected image refer to different "
                "source assets"
            )
            return

        dialog = RasterVectorizationDialog(
            payload,
            source.transform.width_mm,
            source.transform.height_mm,
            self,
        )
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        result = dialog.vectorization_result
        options = dialog.accepted_options
        if result is None or options is None:
            self.show_error("Raster Vectorization closed without a current result")
            return

        # The dialog works only from the immutable bounded payload. Re-read the
        # source through the same strict path immediately before the history
        # operation so replacement on disk cannot be committed under stale review.
        try:
            current = read_raster_asset_payload(
                asset,
                expected_source_sha256=payload.identity.sha256,
            )
        except Exception as exc:
            self.show_error(
                "Could not create vectors because the raster source changed while "
                f"the dialog was open: {exc}"
            )
            return
        if current.identity.sha256 != result.source_sha256:
            self.show_error(
                "Could not create vectors because the reviewed raster identity is stale"
            )
            return
        try:
            self._commit_raster_vectorization(
                source,
                result,
                options,
                source_handling=dialog.source_handling,
                hide_source_after=dialog.hide_source_after,
            )
        except Exception as exc:
            self.show_error(f"Could not create raster vectors: {exc}")

    @staticmethod
    def _raster_vector_layer_is_appropriate(layer: OperationLayer) -> bool:
        return (
            layer.mode is LayerMode.LINE
            and layer.power_percent == 0.0
            and not layer.output_enabled
            and layer.visible
        )

    def _commit_raster_vectorization(
        self,
        source: SceneObject,
        result: RasterVectorizationResult,
        options: RasterVectorizationOptions,
        *,
        source_handling: str,
        hide_source_after: bool,
    ) -> SceneObject:
        """Commit the safe layer, source choice, and compound PATH atomically."""

        if source.kind is not ObjectKind.IMAGE:
            raise ValueError("Raster vectorization source must be an image object")
        if self.document.get_object(source.id) is not source:
            raise ValueError("Raster vectorization source is no longer current")
        handling = str(source_handling).strip().casefold()
        if handling not in {"replace", "keep"}:
            raise ValueError("Raster source handling must be replace or keep")
        if not isinstance(result, RasterVectorizationResult):
            raise TypeError("result must be a RasterVectorizationResult")
        if not isinstance(options, RasterVectorizationOptions):
            raise TypeError("options must be RasterVectorizationOptions")

        previous_active_layer_id = self.active_layer_id
        active_layer = self.document.get_layer(previous_active_layer_id)
        layer_command: AddLayerCommand | None = None
        if E3MainWindow._raster_vector_layer_is_appropriate(active_layer):
            output_layer = active_layer
        else:
            output_layer = OperationLayer(
                name=f"{source.name} trace",
                color=self.document.next_layer_color(),
                mode=LayerMode.LINE,
                power_percent=0.0,
                output_enabled=False,
                visible=True,
                priority=len(self.document.layers),
            )
            layer_command = AddLayerCommand(
                self.document,
                output_layer,
                index=len(self.document.layers),
                description="Add raster trace layer",
            )

        metadata = result.metadata()
        metadata.update(
            {
                "source_name": source.name,
                "source_asset": str(source.geometry.get("asset", "")),
                "raster_vectorization_detection_mode": options.detection_mode.value,
                "raster_vectorization_manual_threshold": options.threshold,
                "raster_vectorization_invert": options.invert,
                "raster_vectorization_alpha_cutoff": options.alpha_cutoff,
                "raster_vectorization_minimum_feature_area_mm2": (
                    options.minimum_feature_area_mm2
                ),
                "raster_vectorization_smoothing_mm": options.smoothing_mm,
                "raster_vectorization_simplification_tolerance_mm": (
                    options.simplification_tolerance_mm
                ),
                "raster_vectorization_contour_output": options.contour_output.value,
                "raster_vectorization_source_handling": handling,
            }
        )
        vector = SceneObject.native_path(
            output_layer.id,
            result.project_path_geometry(),
            name=f"{source.name} trace",
            transform=source.transform.copy(),
        )
        vector.metadata.update(metadata)

        if handling == "replace":
            object_command: AddObjectCommand | ReplaceObjectsCommand = (
                ReplaceObjectsCommand(
                    self.document,
                    [source.id],
                    [vector],
                    description="Replace raster with vectors",
                )
            )
            visibility_command = None
        else:
            object_command = AddObjectCommand(
                self.document,
                vector,
                description="Add raster vectors",
            )
            visibility_command = (
                UpdateObjectPropertiesCommand(
                    self.document,
                    source.id,
                    {"visible": False},
                    description="Hide raster source",
                )
                if hide_source_after and source.visible
                else None
            )

        def redo_vectorization() -> None:
            completed: list[Any] = []
            try:
                if layer_command is not None:
                    layer_command.redo()
                    completed.append(layer_command)
                object_command.redo()
                completed.append(object_command)
                if visibility_command is not None:
                    visibility_command.redo()
                    completed.append(visibility_command)
            except Exception:
                for command in reversed(completed):
                    command.undo()
                self.active_layer_id = previous_active_layer_id
                raise
            self.active_layer_id = output_layer.id

        def undo_vectorization() -> None:
            if visibility_command is not None:
                visibility_command.undo()
            object_command.undo()
            if layer_command is not None:
                layer_command.undo()
            self.active_layer_id = previous_active_layer_id

        self.history.execute(
            FunctionalCommand(
                "Vectorize raster image",
                redo_vectorization,
                undo_vectorization,
            )
        )
        self.workspace.select_objects([vector.id])
        self.show_notice(
            f"Created {len(result.contours)} contour"
            f"{'s' if len(result.contours) != 1 else ''} as one native path on "
            f"output-disabled 0% layer {output_layer.name}"
        )
        return vector

    def new_project(self) -> None:
        if not self._confirm_discard_changes():
            return
        document = self._new_document()
        self.document = document
        self.project_path = None
        self.active_layer_id = self.document.active_layer_id
        self.history.clear()
        self.history.mark_clean()
        self._invalidate_generated_job()
        self._clear_trace_preview()
        self._clear_template_preview(show_message=False)
        self._refresh_document()
        if self._new_project_defaults_notice is not None:
            self.show_notice(self._new_project_defaults_notice)

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
        except Exception as exc:
            self.show_error(f"Could not open project: {exc}")
            return
        self.document = document
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
        self._begin_job_generation()

    def _begin_job_generation(self) -> None:
        if self._job_preparation_owner is not None:
            self.show_notice("A job preparation is already in progress")
            return

        source_document = self.document
        revision = self.document.revision
        try:
            machine = copy.deepcopy(self.runtime.settings.machine)
            laser = copy.deepcopy(self.runtime.settings.laser)
            machine_work_area = machine.work_area
            work_area = self._work_area_signature(machine_work_area)
            air_assist_commands = resolve_air_assist_commands(
                machine.air_assist,
                protocol=machine.protocol,
            )
            coordinate_frame, coordinate_frame_signature = (
                self._capture_job_coordinate_authority()
            )
            coordinate_readiness = self._capture_job_coordinate_readiness()
            calibration_guidance = self._capture_job_calibration_guidance()
            guarded_output_polygon_mm = (
                laser.guarded_output_polygon_mm
                if coordinate_frame_signature is not None
                else None
            )
            start_position = (float(machine.photo_x), float(machine.photo_y))
            optimize_order = self.actions["optimize_paths"].isChecked()
            app_context = self.runtime.context
            identity = app_context.machine_identity
            active_calibration_profile_id = (
                app_context.calibration_profiles.current.key
            )
            preflight_context = JobPreflightContext(
                machine_work_area=machine_work_area,
                controller_power_max=laser.power_max,
                machine_max_work_feed_mm_min=machine.max_work_feed_mm_min,
                machine_max_travel_feed_mm_min=machine.max_travel_feed_mm_min,
                planned_travel_feed_mm_min=laser.travel_feed_mm_min,
                spot_offset_x_mm=laser.spot_offset_x_mm,
                spot_offset_y_mm=laser.spot_offset_y_mm,
                air_assist_commands=air_assist_commands,
                coordinate_frame=coordinate_frame,
                honeycomb_execution_signature=coordinate_frame_signature,
                guarded_output_polygon_mm=guarded_output_polygon_mm,
                machine_id=identity.machine_id,
                machine_profile_id=identity.machine_profile_id,
                expected_calibration_profile_id=(
                    identity.expected_calibration_profile_id
                ),
                active_calibration_profile_id=active_calibration_profile_id,
                bed_calibration_state=coordinate_readiness[0],
                bed_calibration_reasons=coordinate_readiness[1],
                bed_calibration_reason_codes=calibration_guidance[
                    "bed_reason_codes"
                ],
                honeycomb_support_state=coordinate_readiness[2],
                honeycomb_support_reasons=coordinate_readiness[3],
                honeycomb_support_reason_codes=calibration_guidance[
                    "support_reason_codes"
                ],
                camera_readiness_state=calibration_guidance["camera_state"],
                camera_readiness_reasons=calibration_guidance["camera_reasons"],
                camera_readiness_reason_codes=calibration_guidance[
                    "camera_reason_codes"
                ],
                lens_model_state=calibration_guidance["lens_state"],
                lens_model_reasons=calibration_guidance["lens_reasons"],
                lens_model_reason_codes=calibration_guidance[
                    "lens_reason_codes"
                ],
                physical_honeycomb_span_configured=calibration_guidance[
                    "physical_span_configured"
                ],
                execution_ready=bool(machine.allow_motion),
                execution_unready_reason=(
                    "Motion is blocked in the running machine configuration."
                    if not machine.allow_motion
                    else ""
                ),
            )
        except Exception as exc:
            self.show_error(f"Job preflight failed: {exc}")
            return
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{source_document.name}-{timestamp}.gcode"

        self._invalidate_generated_job(cancel_preparation=False)
        self._job_request_id += 1
        request_id = self._job_request_id
        cancellation = threading.Event()
        self._job_worker_requests[request_id] = cancellation
        self._job_worker_phases[request_id] = "snapshot"
        self._job_cancel_reason = ""
        owner = ("worker", request_id)
        self._claim_job_preparation(owner, "Snapshotting project…")
        self._set_authoring_frozen(request_id, True)
        context = {
            "source_document": source_document,
            "revision": revision,
            "project_work_area": source_document.work_area,
            "coordinate_space": source_document.coordinate_space,
            "work_area": work_area,
            "machine_work_area": machine_work_area,
            "air_assist_commands": air_assist_commands,
            "coordinate_frame": coordinate_frame,
            "coordinate_frame_signature": coordinate_frame_signature,
            "guarded_output_polygon_mm": guarded_output_polygon_mm,
            "start_position": start_position,
            "optimize_order": optimize_order,
            "laser": laser,
            "filename": filename,
            "planning_cache": self._planning_cache,
            "preflight_context": preflight_context,
            "machine_id": identity.machine_id,
            "machine_profile_id": identity.machine_profile_id,
            "expected_calibration_profile_id": (
                identity.expected_calibration_profile_id
            ),
            "active_calibration_profile_id": active_calibration_profile_id,
            "allow_motion": bool(machine.allow_motion),
            "max_work_feed_mm_min": float(machine.max_work_feed_mm_min),
            "max_travel_feed_mm_min": float(machine.max_travel_feed_mm_min),
            "coordinate_readiness": coordinate_readiness,
            "calibration_guidance_signature": (
                self._calibration_guidance_signature(calibration_guidance)
            ),
        }

        def snapshot_operation() -> ProjectDocument | None:
            if cancellation.is_set():
                return None
            snapshot = source_document.clone()
            if cancellation.is_set() or source_document.revision != revision:
                return None
            return snapshot

        self.controller.run_background(
            snapshot_operation,
            on_success=lambda snapshot, request_id=request_id, context=context: (
                self._job_snapshot_ready(request_id, context, snapshot)
            ),
            on_failure=lambda message, request_id=request_id: (
                self._job_worker_failed(request_id, "Project snapshot", message)
            ),
            cancel=cancellation.set,
            label="Snapshot project for job generation",
        )

    def _job_snapshot_ready(
        self,
        request_id: int,
        context: dict[str, Any],
        snapshot: ProjectDocument | None,
    ) -> None:
        if self._job_worker_phases.get(request_id) != "snapshot":
            return
        self._set_authoring_frozen(request_id, False)
        cancellation = self._job_worker_requests.get(request_id)
        owner = ("worker", request_id)
        if (
            cancellation is None
            or cancellation.is_set()
            or snapshot is None
            or request_id != self._job_request_id
            or self._job_preparation_owner != owner
            or not self._job_request_context_is_current(context)
        ):
            self._finish_stale_job_worker(request_id)
            return

        self._job_worker_phases[request_id] = "preflight"
        stage = "Checking project and machine readiness"
        self._update_job_preparation(owner, stage)

        def operation() -> JobPreflightReport | None:
            if cancellation.is_set():
                return None
            try:
                report = build_job_preflight_report(
                    snapshot,
                    context["preflight_context"],
                    cancel_check=cancellation.is_set,
                )
            except JobPreflightCancelled:
                return None
            return None if cancellation.is_set() else report

        self.controller.run_background(
            operation,
            on_success=lambda report, request_id=request_id, context=context, snapshot=snapshot: (
                self._job_preflight_ready(request_id, context, snapshot, report)
            ),
            on_failure=lambda message, request_id=request_id: (
                self._job_preflight_failed(request_id, message)
            ),
            cancel=cancellation.set,
            label=stage,
        )

    @QtCore.Slot(int, object, object, object)
    def _job_preflight_ready(
        self,
        request_id: int,
        context: dict[str, Any],
        snapshot: ProjectDocument,
        report: JobPreflightReport | None,
    ) -> None:
        if self._job_worker_phases.get(request_id) != "preflight":
            return
        cancellation = self._job_worker_requests.get(request_id)
        owner = ("worker", request_id)
        if (
            cancellation is None
            or cancellation.is_set()
            or report is None
            or request_id != self._job_request_id
            or self._job_preparation_owner != owner
            or not self._job_request_context_is_current(context)
        ):
            self._finish_stale_job_worker(request_id)
            return
        if report.has_blockers:
            self._finish_job_worker(request_id)
            self._release_job_preparation(owner)
            self.last_job_preflight_report = report
            self._show_blocked_job_preflight(report)
            return
        context["preflight_report"] = report
        self._start_exact_job_generation(request_id, context, snapshot)

    @QtCore.Slot(int, str)
    def _job_preflight_failed(self, request_id: int, message: str) -> None:
        if self._job_worker_phases.get(request_id) != "preflight":
            return
        self._job_worker_failed(request_id, "Job preflight", message)

    def _show_blocked_job_preflight(self, report: JobPreflightReport) -> None:
        previous = self._job_preflight_dialog
        if previous is not None:
            previous.close()
        dialog = JobPreflightDialog(report, self)
        dialog.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setWindowTitle("Job preflight blocked")
        dialog.cancel_button.setText("Close")
        dialog.navigationRequested.connect(
            lambda target, source=dialog: self._preflight_navigation_requested(
                source,
                target,
            )
        )
        dialog.destroyed.connect(
            lambda _object=None, target=dialog: (
                self._preflight_dialog_destroyed(target)
            )
        )
        self._job_preflight_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _preflight_navigation_requested(
        self,
        dialog: JobPreflightDialog,
        target: str,
    ) -> None:
        """Dismiss one blocked report and route an allowlisted UI-only target."""

        if dialog is not self._job_preflight_dialog:
            return
        target_id = str(target).strip()
        setup_tabs = {
            "machine_setup.camera": 0,
            "machine_setup.lens": 1,
            "machine_setup.bed_mapping": 2,
            "machine_setup.fine_registration": 3,
            "machine_setup.accuracy_validation": 4,
            "machine_setup.coordinate_audit": 5,
        }
        if target_id not in setup_tabs and target_id != "machine_manager":
            return

        finding = dialog.preflight_view.selected_finding()
        dialog.close()
        if target_id == "machine_manager":
            finding_code = str(getattr(finding, "code", ""))
            if finding_code == "honeycomb.output_polygon_invalid":
                focus_target = "guarded_output_polygon"
            elif finding_code in {
                "work_area.machine_missing",
                "work_area.mismatch",
                "honeycomb.machine_work_area_missing",
                "coordinate_space.unsupported",
            }:
                focus_target = "work_area"
            else:
                focus_target = "honeycomb_span"
            QtCore.QTimer.singleShot(
                0,
                lambda: self.open_machine_manager(focus_target=focus_target),
            )
            return
        QtCore.QTimer.singleShot(
            0,
            lambda target_id=target_id: self.open_machine_setup(
                setup_tabs[target_id],
                navigation_target=target_id,
            ),
        )

    def _preflight_dialog_destroyed(self, dialog: JobPreflightDialog) -> None:
        if self._job_preflight_dialog is dialog:
            self._job_preflight_dialog = None
            if self.last_job is None:
                self.last_job_preflight_report = None

    def _start_exact_job_generation(
        self,
        request_id: int,
        context: dict[str, Any],
        snapshot: ProjectDocument,
    ) -> None:
        cancellation = self._job_worker_requests.get(request_id)
        owner = ("worker", request_id)
        if (
            cancellation is None
            or cancellation.is_set()
            or request_id != self._job_request_id
            or self._job_preparation_owner != owner
            or not self._job_request_context_is_current(context)
        ):
            self._finish_stale_job_worker(request_id)
            return
        self._job_worker_phases[request_id] = "planning"
        stage = "Generating exact toolpath"
        self._update_job_preparation(owner, stage)

        def operation() -> dict[str, Any] | None:
            if cancellation.is_set():
                return None
            laser = context["laser"]
            start_position = context["start_position"]
            try:
                job = generate_project_gcode(
                    snapshot,
                    laser,
                    optimize_order=bool(context["optimize_order"]),
                    start_position=start_position,
                    coordinate_frame=context["coordinate_frame"],
                    machine_work_area=context["machine_work_area"],
                    guarded_output_polygon_mm=context["guarded_output_polygon_mm"],
                    planning_cache=context["planning_cache"],
                    air_assist_commands=context["air_assist_commands"],
                    cancel_check=cancellation.is_set,
                )
                plan = job.plan
                if plan is None:
                    plan = build_job_plan(
                        job.text,
                        power_max=laser.power_max,
                        default_feed_mm_min=laser.travel_feed_mm_min,
                        start_position=start_position,
                        acceleration_mm_s2=laser.preview_acceleration_mm_s2,
                        command_delay_ms=laser.preview_command_delay_ms,
                        air_assist_commands=context["air_assist_commands"],
                        cancel_check=cancellation.is_set,
                    )
                    job.plan = plan
                if cancellation.is_set():
                    return None
                prepared = prepare_job_preview(
                    plan,
                    cancel_check=cancellation.is_set,
                )
            except (
                JobPlanCancelled,
                JobPreviewPreparationCancelled,
                ToolpathGenerationCancelled,
            ):
                return None
            powered = plan.powered
            if cancellation.is_set():
                return None
            verify_project_job_assets(job)
            return {
                "job": job,
                "prepared": prepared,
                "filename": context["filename"],
                "powered": powered,
                "revision": context["revision"],
                "work_area": context["work_area"],
                "coordinate_frame_signature": context[
                    "coordinate_frame_signature"
                ],
                "preflight_report": context["preflight_report"],
                "request_context": context,
            }

        self.controller.run_background(
            operation,
            on_success=lambda payload, request_id=request_id: (
                self._job_generation_ready(request_id, payload)
            ),
            on_failure=lambda message, request_id=request_id: (
                self._job_generation_failed(request_id, message)
            ),
            cancel=cancellation.set,
            label=stage,
        )

    @QtCore.Slot(int, object)
    def _job_generation_ready(
        self,
        request_id: int,
        payload: dict[str, Any] | None,
    ) -> None:
        if self._job_worker_phases.get(request_id) != "planning":
            return
        cancellation = self._job_worker_requests.get(request_id)
        self._finish_job_worker(request_id)
        owner = ("worker", request_id)
        try:
            current_frame_signature = self._project_execution_signature()
        except Exception:
            current_frame_signature = object()
        authority_current = False
        request_context_current = True
        if payload is not None:
            authority_current = self._prepared_output_authority_is_current(
                payload["job"]
            )
            request_context = payload.get("request_context")
            if request_context is not None:
                request_context_current = self._job_request_context_is_current(
                    request_context
                )
            start_here_context = payload.get("start_here_request_context")
            if start_here_context is not None:
                request_context_current = (
                    request_context_current
                    and self._start_here_request_context_is_current(
                        start_here_context
                    )
                )
        if (
            payload is None
            or cancellation is None
            or cancellation.is_set()
            or request_id != self._job_request_id
            or self._job_preparation_owner != owner
            or int(payload["revision"]) != self.document.revision
            or payload.get("coordinate_frame_signature")
            != current_frame_signature
            or not authority_current
            or not request_context_current
        ):
            if self._job_preparation_owner == owner:
                self._release_job_preparation(owner)
                self.show_notice(
                    self._job_cancel_reason
                    or "Project changed; discarded the stale generated result"
                )
            return
        if not self._prepared_raster_preview_matches(payload["job"], owner):
            return
        self._install_generated_job(request_id, payload)

    @QtCore.Slot(int, bool, str)
    def _job_generation_failed(
        self,
        request_id: int,
        message: str,
    ) -> None:
        if self._job_worker_phases.get(request_id) != "planning":
            return
        if request_id != self._job_request_id:
            self._finish_stale_job_worker(request_id)
            return
        self._finish_job_worker(request_id)
        owner = ("worker", request_id)
        if self._job_preparation_owner != owner:
            return
        self._release_job_preparation(owner)
        self.show_error(f"Toolpath generation failed: {message}")

    def _job_worker_failed(self, request_id: int, label: str, message: str) -> None:
        if request_id not in self._job_worker_requests:
            return
        if request_id != self._job_request_id:
            self._finish_stale_job_worker(request_id)
            return
        self._set_authoring_frozen(request_id, False)
        self._finish_job_worker(request_id)
        owner = ("worker", request_id)
        if self._job_preparation_owner != owner:
            return
        self._release_job_preparation(owner)
        self._invalidate_generated_job(cancel_preparation=False)
        self.show_error(f"{label} failed: {message}")

    def _finish_job_worker(self, request_id: int) -> None:
        self._job_worker_requests.pop(request_id, None)
        self._job_worker_phases.pop(request_id, None)

    def _finish_stale_job_worker(self, request_id: int) -> None:
        self._set_authoring_frozen(request_id, False)
        self._finish_job_worker(request_id)
        owner = ("worker", request_id)
        if self._job_preparation_owner == owner:
            self._release_job_preparation(owner)
            self.show_notice(self._job_cancel_reason or "Job preparation cancelled")

    def _install_generated_job(
        self,
        request_id: int,
        payload: dict[str, Any],
    ) -> None:
        job = payload["job"]
        prepared_space = getattr(job, "coordinate_space", CoordinateSpace.MACHINE)
        prepared_signature = payload.get("coordinate_frame_signature")
        if prepared_space is CoordinateSpace.HONEYCOMB_LOCAL:
            if prepared_signature is None:
                raise ValueError(
                    "A honeycomb-local prepared job is missing its execution binding"
                )
            frame_signature = getattr(job, "coordinate_frame_signature", None)
            if tuple(prepared_signature[:3]) != tuple(frame_signature or ()):
                raise ValueError(
                    "Prepared honeycomb job and execution binding disagree"
                )
        elif prepared_signature is not None:
            raise ValueError(
                "A machine-coordinate prepared job cannot carry a honeycomb binding"
            )
        if not self._prepared_output_authority_is_current(job):
            raise ValueError(
                "Prepared job and configured laser-output authority disagree"
            )
        job.execution_signature = prepared_signature
        self.last_job = job
        self.last_job_name = str(payload["filename"])
        self.last_job_powered = bool(payload["powered"])
        self.last_job_revision = int(payload["revision"])
        self.last_job_work_area = tuple(payload["work_area"])
        self.last_job_coordinate_frame = prepared_signature
        self.last_job_preview_data = payload["prepared"]
        self.last_job_preflight_report = payload.get("preflight_report")
        if payload.get("summary"):
            summary = str(payload["summary"])
        else:
            summary = (
                f"{job.path_count} paths · {job.cut_length_mm:.1f} mm cut · "
                f"{job.travel_length_mm:.1f} mm travel · "
                f"estimated {job.estimated_seconds:.1f} s"
                + self._laser_spot_offset_summary()
            )
        self._set_prepared_job_status(summary)
        if payload.get("notice"):
            self.show_notice(str(payload["notice"]))
        else:
            self.show_notice("Generated and validated exact in-memory job")
        self._start_job_render(request_id, open_preview=True)

    def _start_job_render(self, request_id: int, *, open_preview: bool) -> None:
        if (
            self.last_job is None
            or self.last_job.plan is None
            or self.last_job_preview_data is None
        ):
            owner = self._job_preparation_owner
            if owner is not None:
                self._release_job_preparation(owner)
            self._invalidate_generated_job(cancel_preparation=False)
            self.show_error("The generated job has no exact preview plan")
            return
        self._cancel_job_render()
        self._job_render_request_id = request_id
        owner = ("render", request_id)
        self._job_render_pending = {"workspace"}
        if open_preview:
            self._job_render_pending.add("dialog")
        self._job_render_progress = {
            stage: 0.0 for stage in self._job_render_pending
        }
        self._claim_job_preparation(owner, "Building exact previews")
        self.actions["export_gcode"].setEnabled(False)

        try:
            plan = self.last_job.plan
            preview_kwargs = {
                "on_progress": lambda completed, total, request_id=request_id: (
                    self._job_render_progressed(
                        request_id,
                        "workspace",
                        completed,
                        total,
                    )
                ),
                "on_finished": lambda completed, request_id=request_id: (
                    self._job_render_stage_finished(request_id, "workspace")
                    if completed
                    else None
                ),
                "on_failed": lambda message, request_id=request_id: (
                    self._job_render_failed(
                        request_id,
                        f"Workspace preview failed: {message}",
                    )
                ),
            }
            coordinate_frame = self._job_preview_coordinate_frame()
            if coordinate_frame is not None:
                preview_kwargs["coordinate_frame"] = coordinate_frame
            self.workspace.start_toolpath_preview(
                plan,
                **preview_kwargs,
            )
        except Exception as exc:
            self._job_render_failed(
                request_id,
                f"Exact job preview startup failed: {exc}",
            )
            return
        if open_preview and request_id == self._job_render_request_id:
            self._open_job_preview_dialog(request_id, deferred=True)

    def _open_job_preview_dialog(
        self,
        request_id: int,
        *,
        deferred: bool,
    ) -> None:
        if (
            self.last_job is None
            or self.last_job.plan is None
            or self.last_job_preview_data is None
        ):
            self._job_render_failed(
                request_id,
                "Exact job Preview lost its prepared plan before construction",
            )
            return
        if not self._prepared_frame_is_current():
            self._job_render_failed(
                request_id,
                "Exact job Preview authority changed before construction; regenerate",
            )
            return
        previous = self._job_preview_dialog
        if previous is not None:
            previous.close()
        area = self.document.work_area
        try:
            coordinate_frame = self._job_preview_coordinate_frame()
            dialog = JobPreviewDialog(
                self.last_job.plan,
                (area.x_min, area.x_max, area.y_min, area.y_max),
                self.last_job_name or self.document.name,
                self,
                prepared=self.last_job_preview_data,
                defer_render=deferred,
                max_work_feed_mm_min=(
                    self.runtime.settings.machine.max_work_feed_mm_min
                ),
                max_travel_feed_mm_min=(
                    self.runtime.settings.machine.max_travel_feed_mm_min
                ),
                coordinate_frame=coordinate_frame,
                preflight_report=self.last_job_preflight_report,
            )
        except Exception as exc:
            self._job_render_failed(
                request_id,
                f"Exact job Preview failed: {exc}",
            )
            return
        dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.destroyed.connect(
            lambda _object=None, target=dialog, request_id=request_id: (
                self._preview_dialog_destroyed(target, request_id)
            )
        )
        dialog.startHereRequested.connect(self._prepare_start_here)
        dialog.runRequested.connect(
            lambda target=dialog: self._run_from_job_preview(target)
        )
        if deferred:
            dialog.renderProgress.connect(
                lambda completed, total, request_id=request_id: (
                    self._job_render_progressed(
                        request_id,
                        "dialog",
                        completed,
                        total,
                    )
                )
            )
            dialog.renderFinished.connect(
                lambda request_id=request_id: self._job_render_stage_finished(
                    request_id,
                    "dialog",
                )
            )
            dialog.renderCancelled.connect(
                lambda request_id=request_id: self._job_render_cancelled(
                    request_id
                )
            )
            dialog.renderFailed.connect(
                lambda message, request_id=request_id: self._job_render_failed(
                    request_id,
                    f"Exact job Preview failed: {message}",
                )
            )
        self._job_preview_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _job_render_progressed(
        self,
        request_id: int,
        stage: str,
        completed: int,
        total: int,
    ) -> None:
        owner = ("render", request_id)
        if (
            request_id != self._job_render_request_id
            or self._job_preparation_owner != owner
        ):
            return
        self._job_render_progress[stage] = max(
            0.0,
            min(1.0, float(completed) / max(1, int(total))),
        )
        overall = sum(self._job_render_progress.values()) / max(
            1,
            len(self._job_render_progress),
        )
        self._update_job_preparation(
            owner,
            f"Building exact previews · {overall * 100:.0f}%",
            completed=int(round(overall * 1000)),
            total=1000,
        )

    def _job_render_stage_finished(self, request_id: int, stage: str) -> None:
        owner = ("render", request_id)
        if (
            request_id != self._job_render_request_id
            or self._job_preparation_owner != owner
        ):
            return
        self._job_render_pending.discard(stage)
        self._job_render_progress[stage] = 1.0
        if self._job_render_pending:
            self._job_render_progressed(request_id, stage, 1, 1)
            return
        self._job_render_request_id = None
        self._job_render_progress = {}
        self._release_job_preparation(owner)
        self.actions["export_gcode"].setEnabled(self.last_job is not None)
        self.show_notice("Exact job and previews are ready for review")

    def _cancel_job_render(self) -> None:
        request_id = self._job_render_request_id
        owner = None if request_id is None else ("render", request_id)
        self._job_render_request_id = None
        self._job_render_pending.clear()
        self._job_render_progress.clear()
        if hasattr(self, "workspace"):
            self.workspace.clear_toolpath_preview()
        dialog = getattr(self, "_job_preview_dialog", None)
        if dialog is not None:
            self._job_preview_dialog = None
            dialog.close()
        if owner is not None:
            self._release_job_preparation(owner)

    def _job_render_cancelled(self, request_id: int) -> None:
        if request_id != self._job_render_request_id:
            return
        self._job_request_id += 1
        self._cancel_job_render()
        self._invalidate_generated_job(cancel_preparation=False)
        self.show_notice(
            "Preview closed before preparation finished; regenerate before run or export"
        )

    def _job_render_failed(self, request_id: int, message: str) -> None:
        if request_id != self._job_render_request_id:
            return
        self._job_request_id += 1
        self._cancel_job_render()
        self._invalidate_generated_job(cancel_preparation=False)
        self.show_error(message)

    def _cancel_job_preparation(self, reason: str) -> None:
        owner = self._job_preparation_owner
        if owner is None:
            return
        self._job_request_id += 1
        self._job_cancel_reason = str(reason)
        if owner[0] == "worker":
            cancellation = self._job_worker_requests.get(owner[1])
            if cancellation is not None:
                cancellation.set()
            self._update_job_preparation(owner, "Cancelling job preparation…")
        else:
            self._cancel_job_render()
            self._invalidate_generated_job(cancel_preparation=False)

    def _software_stop_started(self) -> None:
        self._pending_calibration_capture = None
        if self._job_preparation_owner is None:
            return
        self._cancel_job_preparation(
            "Software Stop cancelled the unfinished job preparation"
        )
        self.show_notice("Software Stop cancelled the unfinished job preparation")

    def _planned_job_start_position(self) -> tuple[float, float]:
        machine = self.runtime.settings.machine
        return float(machine.photo_x), float(machine.photo_y)

    def _capture_job_coordinate_authority(
        self,
    ) -> tuple[Any | None, tuple[Any, ...] | None]:
        """Capture current coordinate authority without rejecting missing setup."""

        coordinate_space = getattr(
            self.document,
            "coordinate_space",
            CoordinateSpace.MACHINE,
        )
        if coordinate_space is CoordinateSpace.MACHINE:
            return None, None
        support = self.runtime.context._current_honeycomb_support()
        coordinate_frame = (
            support.coordinate_frame
            if support is not None and support.is_execution_verifiable
            else None
        )
        signature = self.runtime.context.honeycomb_execution_signature()
        return (
            coordinate_frame,
            None if signature is None else tuple(signature),
        )

    def _capture_job_coordinate_readiness(
        self,
    ) -> tuple[str | None, tuple[str, ...], str | None, tuple[str, ...]]:
        """Capture read-only calibration/support readiness for local projects."""

        if self.document.coordinate_space is CoordinateSpace.MACHINE:
            return None, (), None, ()
        app_context = self.runtime.context
        bed = app_context.bed_calibration_validity()
        support = app_context.honeycomb_support_status()

        def status(value: Any) -> tuple[str, tuple[str, ...]]:
            state = str(value.get("state") or "UNKNOWN").strip().upper()
            reasons = tuple(
                message
                for item in value.get("reasons", ())
                if (message := str(item).strip())
            )
            return state, reasons

        bed_state, bed_reasons = status(bed)
        support_state, support_reasons = status(support)
        return bed_state, bed_reasons, support_state, support_reasons

    def _capture_job_calibration_guidance(self) -> dict[str, Any]:
        """Capture detached recovery facts without changing execution authority."""

        empty: dict[str, Any] = {
            "bed_reason_codes": (),
            "support_reason_codes": (),
            "camera_state": None,
            "camera_reasons": (),
            "camera_reason_codes": (),
            "lens_state": None,
            "lens_reasons": (),
            "lens_reason_codes": (),
            "physical_span_configured": None,
        }
        if self.document.coordinate_space is CoordinateSpace.MACHINE:
            return empty

        app_context = self.runtime.context
        bed = app_context.bed_calibration_validity()
        support = app_context.honeycomb_support_status()
        camera = app_context.camera_calibration_readiness()

        def values(value: Any, key: str) -> tuple[str, ...]:
            return tuple(
                text
                for item in value.get(key, ())
                if (text := str(item).strip())
            )

        model = app_context.lens.model
        if model is None:
            lens_state = "MISSING"
            lens_reasons = ("No accepted lens model is active.",)
            lens_reason_codes = ("lens.model_missing",)
        else:
            gate = str(model.quality.get("gate") or "").strip().casefold()
            if gate in {"pass", "warning"}:
                lens_state = "QUALIFIED"
                lens_reasons = ()
                lens_reason_codes = ()
            else:
                lens_state = "UNQUALIFIED"
                lens_reasons = (
                    "The active lens model does not have accepted pose-diversity "
                    "and coverage diagnostics.",
                )
                lens_reason_codes = ("lens.model_unqualified",)

        return {
            "bed_reason_codes": values(bed, "reason_codes"),
            "support_reason_codes": values(support, "reason_codes"),
            "camera_state": str(camera.get("state") or "UNKNOWN").strip().upper(),
            "camera_reasons": values(camera, "reasons"),
            "camera_reason_codes": values(camera, "reason_codes"),
            "lens_state": lens_state,
            "lens_reasons": lens_reasons,
            "lens_reason_codes": lens_reason_codes,
            "physical_span_configured": (
                self.runtime.settings.machine.honeycomb_span_mm is not None
            ),
        }

    @staticmethod
    def _calibration_guidance_signature(
        guidance: dict[str, Any],
    ) -> tuple[Any, ...]:
        """Return only stable state/code facts; diagnostic prose may contain ages."""

        return (
            guidance["bed_reason_codes"],
            guidance["support_reason_codes"],
            guidance["camera_state"],
            guidance["camera_reason_codes"],
            guidance["lens_state"],
            guidance["lens_reason_codes"],
            guidance["physical_span_configured"],
        )

    def _job_request_context_is_current(self, context: dict[str, Any]) -> bool:
        """Reject detached preflight/planning inputs after any authority change."""

        try:
            if (
                context["source_document"] is not self.document
                or int(context["revision"]) != self.document.revision
                or context["project_work_area"] != self.document.work_area
                or context["coordinate_space"] is not self.document.coordinate_space
            ):
                return False
            machine = self.runtime.settings.machine
            if self._work_area_signature(machine.work_area) != tuple(
                context["work_area"]
            ):
                return False
            if (float(machine.photo_x), float(machine.photo_y)) != tuple(
                context["start_position"]
            ):
                return False
            if bool(machine.allow_motion) != bool(context["allow_motion"]):
                return False
            if resolve_air_assist_commands(
                machine.air_assist,
                protocol=machine.protocol,
            ) != context["air_assist_commands"]:
                return False
            if (
                float(machine.max_work_feed_mm_min)
                != float(context["max_work_feed_mm_min"])
                or float(machine.max_travel_feed_mm_min)
                != float(context["max_travel_feed_mm_min"])
                or self._capture_job_coordinate_readiness()
                != context["coordinate_readiness"]
                or self._calibration_guidance_signature(
                    self._capture_job_calibration_guidance()
                )
                != context["calibration_guidance_signature"]
            ):
                return False
            if self.runtime.settings.laser != context["laser"]:
                return False
            if self.actions["optimize_paths"].isChecked() != bool(
                context["optimize_order"]
            ):
                return False
            coordinate_frame, signature = self._capture_job_coordinate_authority()
            if (
                coordinate_frame != context["coordinate_frame"]
                or signature != context["coordinate_frame_signature"]
            ):
                return False
            app_context = self.runtime.context
            identity = app_context.machine_identity
            return (
                identity.machine_id == context["machine_id"]
                and identity.machine_profile_id == context["machine_profile_id"]
                and identity.expected_calibration_profile_id
                == context["expected_calibration_profile_id"]
                and app_context.calibration_profiles.current.key
                == context["active_calibration_profile_id"]
            )
        except Exception:
            return False

    def _capture_start_here_request_context(
        self,
        plan: Any,
        source_job: ProjectJob,
    ) -> dict[str, Any]:
        """Snapshot the machine facts used to rebuild a Start Here program."""

        machine = self.runtime.settings.machine
        air_assist_commands = resolve_air_assist_commands(
            machine.air_assist,
            protocol=machine.protocol,
        )
        if air_assist_commands != getattr(plan, "air_assist_commands", None):
            raise ValueError(
                "The configured Air Assist mapping changed; regenerate the job "
                "before using Start Here"
            )
        laser = self.runtime.settings.laser
        spot_offset_mm = (
            float(laser.spot_offset_x_mm),
            float(laser.spot_offset_y_mm),
        )
        prepared_spot_offset_mm = tuple(
            float(value) for value in source_job.spot_offset_mm
        )
        if len(prepared_spot_offset_mm) != 2:
            raise ValueError("The prepared job has an invalid laser spot offset")
        if spot_offset_mm != prepared_spot_offset_mm:
            raise ValueError(
                "The configured laser spot offset changed; regenerate the job "
                "before using Start Here"
            )
        return {
            "air_assist_commands": air_assist_commands,
            "power_mode": str(laser.power_mode),
            "spot_offset_mm": spot_offset_mm,
            "start_position": tuple(self._planned_job_start_position()),
            "work_area": self._work_area_signature(machine.work_area),
        }

    def _start_here_request_context_is_current(
        self,
        context: dict[str, Any],
    ) -> bool:
        """Reject a completed Start Here rebuild after its machine facts change."""

        try:
            machine = self.runtime.settings.machine
            return (
                resolve_air_assist_commands(
                    machine.air_assist,
                    protocol=machine.protocol,
                )
                == context["air_assist_commands"]
                and str(self.runtime.settings.laser.power_mode)
                == str(context["power_mode"])
                and (
                    float(self.runtime.settings.laser.spot_offset_x_mm),
                    float(self.runtime.settings.laser.spot_offset_y_mm),
                )
                == tuple(context["spot_offset_mm"])
                and tuple(self._planned_job_start_position())
                == tuple(context["start_position"])
                and self._work_area_signature(machine.work_area)
                == tuple(context["work_area"])
            )
        except Exception:
            return False

    @staticmethod
    def _work_area_signature(area: Any) -> tuple[float, float, float, float]:
        return (
            float(area.x_min),
            float(area.x_max),
            float(area.y_min),
            float(area.y_max),
        )

    def _project_coordinate_frame(self) -> Any | None:
        coordinate_space = getattr(
            self.document,
            "coordinate_space",
            CoordinateSpace.MACHINE,
        )
        if coordinate_space is CoordinateSpace.MACHINE:
            return None
        support = self.runtime.context._current_honeycomb_support()
        if support is None or not support.is_execution_verifiable:
            raise ValueError(
                "This project uses honeycomb-local coordinates, but no current "
                "automatic four-corner honeycomb reference is available. "
                "Re-detect the honeycomb."
            )
        expected = (support.support_width_mm, support.support_height_mm)
        actual = (self.document.work_area.width, self.document.work_area.height)
        if any(abs(left - right) > 1e-6 for left, right in zip(expected, actual, strict=True)):
            raise ValueError(
                "The project honeycomb dimensions do not match the current "
                f"reference ({actual[0]:g} × {actual[1]:g} mm project; "
                f"{expected[0]:g} × {expected[1]:g} mm detected)."
            )
        if abs(self.document.work_area.x_min) > 1e-6 or abs(self.document.work_area.y_min) > 1e-6:
            raise ValueError("Honeycomb-local projects must start at X0 Y0")
        return support.coordinate_frame

    def _job_preview_coordinate_frame(self) -> Any | None:
        """Map every machine-space plan into the active local canvas."""
        if self.document.coordinate_space is CoordinateSpace.HONEYCOMB_LOCAL:
            return self._project_coordinate_frame()
        return None

    def _project_execution_signature(self) -> tuple[Any, ...] | None:
        if getattr(
            self.document,
            "coordinate_space",
            CoordinateSpace.MACHINE,
        ) is CoordinateSpace.MACHINE:
            return None
        self._project_coordinate_frame()
        signature = self.runtime.context.honeycomb_execution_signature()
        if signature is None:
            raise ValueError(
                "The active honeycomb or camera-to-machine mapping is not current"
            )
        return tuple(signature)

    def _configured_guarded_output_polygon(
        self,
    ) -> tuple[tuple[float, float], ...] | None:
        polygon = getattr(
            getattr(getattr(self, "runtime", None), "settings", None),
            "laser",
            None,
        )
        polygon = getattr(polygon, "guarded_output_polygon_mm", None)
        if polygon is None:
            return None
        return E3MainWindow._canonical_guarded_output_polygon(
            polygon,
            label="laser.guarded_output_polygon_mm",
        )

    @staticmethod
    def _canonical_guarded_output_polygon(
        polygon: Any,
        *,
        label: str,
    ) -> tuple[tuple[float, float], ...]:
        normalized = normalize_convex_polygon(
            polygon,
            label=label,
        )
        start = min(range(len(normalized)), key=lambda index: normalized[index])
        return normalized[start:] + normalized[:start]

    def _prepared_output_authority_is_current(self, job: ProjectJob) -> bool:
        coordinate_space = getattr(job, "coordinate_space", CoordinateSpace.MACHINE)
        prepared_polygon = getattr(job, "guarded_output_polygon_mm", None)
        if coordinate_space is CoordinateSpace.MACHINE:
            return prepared_polygon is None
        if coordinate_space is not CoordinateSpace.HONEYCOMB_LOCAL:
            return False
        try:
            if prepared_polygon is not None:
                prepared_polygon = E3MainWindow._canonical_guarded_output_polygon(
                    prepared_polygon,
                    label="prepared guarded output polygon",
                )
            return prepared_polygon == E3MainWindow._configured_guarded_output_polygon(
                self
            )
        except (TypeError, ValueError):
            return False

    def _prepared_frame_is_current(self) -> bool:
        job = self.last_job
        if job is None:
            return False
        try:
            laser = self.runtime.settings.laser
            prepared_spot_offset_mm = tuple(
                float(value) for value in job.spot_offset_mm
            )
            if len(prepared_spot_offset_mm) != 2 or prepared_spot_offset_mm != (
                float(laser.spot_offset_x_mm),
                float(laser.spot_offset_y_mm),
            ):
                return False
            prepared_air_assist = getattr(job, "air_assist_commands", None)
            if prepared_air_assist is not None:
                machine = self.runtime.settings.machine
                if resolve_air_assist_commands(
                    machine.air_assist,
                    protocol=machine.protocol,
                ) != prepared_air_assist:
                    return False
        except (AttributeError, TypeError, ValueError):
            return False
        coordinate_space = getattr(job, "coordinate_space", CoordinateSpace.MACHINE)
        if coordinate_space is CoordinateSpace.MACHINE:
            return (
                self.last_job_coordinate_frame is None
                and E3MainWindow._prepared_output_authority_is_current(self, job)
            )
        if (
            coordinate_space is not CoordinateSpace.HONEYCOMB_LOCAL
            or self.last_job_coordinate_frame is None
            or getattr(job, "execution_signature", None)
            != self.last_job_coordinate_frame
        ):
            return False
        try:
            frame_signature = getattr(job, "coordinate_frame_signature", None)
            if tuple(self.last_job_coordinate_frame[:3]) != tuple(
                frame_signature or ()
            ):
                return False
            if not E3MainWindow._prepared_output_authority_is_current(self, job):
                return False
            return self.last_job_coordinate_frame == self._project_execution_signature()
        except Exception:
            return False

    def _require_project_machine_work_area_match(self) -> None:
        if getattr(
            self.document,
            "coordinate_space",
            CoordinateSpace.MACHINE,
        ) is CoordinateSpace.HONEYCOMB_LOCAL:
            self._project_coordinate_frame()
            return
        project = self._work_area_signature(self.document.work_area)
        machine = self._work_area_signature(self.runtime.settings.machine.work_area)
        if any(abs(left - right) > 1e-6 for left, right in zip(project, machine, strict=True)):
            raise ValueError(
                "Project work area does not match the configured machine work area. "
                f"Project X{project[0]:g}..{project[1]:g} "
                f"Y{project[2]:g}..{project[3]:g}; machine "
                f"X{machine[0]:g}..{machine[1]:g} Y{machine[2]:g}..{machine[3]:g}."
            )

    def _current_job_plan(self) -> Any | None:
        if self.last_job is None:
            return None
        plan = getattr(self.last_job, "plan", None)
        if plan is not None:
            return plan
        return build_job_plan(
            self.last_job.text,
            power_max=self.runtime.settings.laser.power_max,
            default_feed_mm_min=self.runtime.settings.laser.travel_feed_mm_min,
            start_position=self._planned_job_start_position(),
            air_assist_commands=getattr(
                self.last_job,
                "air_assist_commands",
                None,
            ),
        )

    def _set_prepared_job_status(self, summary: str) -> None:
        plan = self._current_job_plan()
        controller_power = 0.0 if plan is None else float(plan.maximum_power)
        power_percent = (
            0.0
            if plan is None
            else controller_power / max(1, plan.power_max) * 100.0
        )
        self.job_progress.set_prepared_job(
            summary,
            power_percent=power_percent,
            controller_power=controller_power,
        )
        self._update_status_bar_layout()

    def _verify_prepared_job_assets(self, action: str) -> bool:
        job = self.last_job
        if job is None:
            return False
        try:
            verify_project_job_assets(job)
        except ValueError as exc:
            self._invalidate_generated_job()
            self.show_error(
                f"Could not continue {action}: {exc} Regenerate the job and "
                "review its exact Preview again."
            )
            return False
        return True

    def _prepared_raster_preview_matches(
        self,
        job: ProjectJob,
        owner: tuple[str, int],
    ) -> bool:
        mismatches = self.workspace.raster_preview_mismatches(job.raster_assets)
        if not mismatches:
            return True
        refreshed = self.workspace.refresh_raster_previews(mismatches)
        self._job_request_id += 1
        self._release_job_preparation(owner)
        self._invalidate_generated_job(cancel_preparation=False)
        detail = ", ".join(Path(path).name for path in mismatches)
        if refreshed:
            self.show_error(
                "Raster source content changed after its canvas preview was "
                f"loaded ({detail}). The canvas has been refreshed; Generate "
                "again and review the replacement exact Preview."
            )
        else:
            self.show_error(
                "Raster source content no longer matches its canvas preview "
                f"and could not be refreshed ({detail}). Restore the source, "
                "then Generate again."
            )
        return False

    def show_job_preview(self) -> None:
        if self.last_job is None:
            self.show_error("Generate a project toolpath first")
            return
        if self.last_job_revision != self.document.revision:
            self._invalidate_generated_job()
            self.show_error("The project changed; regenerate before previewing")
            return
        if not self._prepared_frame_is_current():
            self._invalidate_generated_job()
            self.show_error(
                "The prepared coordinate, output-boundary, laser spot-offset, or "
                "Air Assist authority changed; regenerate before previewing"
            )
            return
        if not self._verify_prepared_job_assets("opening Preview"):
            return
        if self._job_preparation_busy:
            self.show_notice("The exact Preview is still being prepared")
            return
        plan = self.last_job.plan
        self._job_request_id += 1
        request_id = self._job_request_id
        if plan is not None and self.last_job_preview_data is not None:
            self._job_render_request_id = request_id
            self._job_render_pending = {"dialog"}
            self._job_render_progress = {"dialog": 0.0}
            self._claim_job_preparation(
                ("render", request_id),
                "Building exact Preview",
            )
            self._open_job_preview_dialog(request_id, deferred=True)
            return

        revision = self.document.revision
        text = self.last_job.text
        air_assist_commands = getattr(
            self.last_job,
            "air_assist_commands",
            None,
        )
        laser = self.runtime.settings.laser
        start_position = self._planned_job_start_position()
        cancellation = threading.Event()
        self._job_worker_requests[request_id] = cancellation
        self._job_worker_phases[request_id] = "preview"
        owner = ("worker", request_id)
        self._claim_job_preparation(owner, "Indexing exact Preview")

        def prepare() -> tuple[Any, PreparedJobPreview] | None:
            if cancellation.is_set():
                return None
            try:
                exact_plan = plan or build_job_plan(
                    text,
                    power_max=laser.power_max,
                    default_feed_mm_min=laser.travel_feed_mm_min,
                    start_position=start_position,
                    acceleration_mm_s2=laser.preview_acceleration_mm_s2,
                    command_delay_ms=laser.preview_command_delay_ms,
                    air_assist_commands=air_assist_commands,
                    cancel_check=cancellation.is_set,
                )
                prepared = prepare_job_preview(
                    exact_plan,
                    cancel_check=cancellation.is_set,
                )
            except (JobPlanCancelled, JobPreviewPreparationCancelled):
                return None
            return None if cancellation.is_set() else (exact_plan, prepared)

        self.controller.run_background(
            prepare,
            on_success=lambda result, request_id=request_id, revision=revision: (
                self._preview_index_ready(request_id, revision, result)
            ),
            on_failure=lambda message, request_id=request_id: (
                self._preview_index_failed(request_id, message)
            ),
            cancel=cancellation.set,
            label="Index exact job Preview",
        )

    def _preview_index_ready(
        self,
        request_id: int,
        revision: int,
        result: tuple[Any, PreparedJobPreview] | None,
    ) -> None:
        if self._job_worker_phases.get(request_id) != "preview":
            return
        cancellation = self._job_worker_requests.get(request_id)
        self._finish_job_worker(request_id)
        owner = ("worker", request_id)
        if (
            result is None
            or cancellation is None
            or cancellation.is_set()
            or request_id != self._job_request_id
            or revision != self.document.revision
            or self._job_preparation_owner != owner
        ):
            if self._job_preparation_owner == owner:
                self._release_job_preparation(owner)
                self.show_notice(self._job_cancel_reason or "Discarded stale Preview")
            return
        plan, prepared = result
        if self.last_job is None:
            self._release_job_preparation(owner)
            return
        self.last_job.plan = plan
        self.last_job_preview_data = prepared
        self._job_render_request_id = request_id
        self._job_render_pending = {"dialog"}
        self._job_render_progress = {"dialog": 0.0}
        self._claim_job_preparation(("render", request_id), "Building exact Preview")
        self._open_job_preview_dialog(request_id, deferred=True)

    def _preview_index_failed(self, request_id: int, message: str) -> None:
        if self._job_worker_phases.get(request_id) != "preview":
            return
        self._finish_job_worker(request_id)
        owner = ("worker", request_id)
        if request_id != self._job_request_id or self._job_preparation_owner != owner:
            return
        self._release_job_preparation(owner)
        self.show_error(f"Preview preparation failed: {message}")

    def _preview_dialog_destroyed(
        self,
        dialog: JobPreviewDialog,
        request_id: int | None = None,
    ) -> None:
        if getattr(self, "_job_preview_dialog", None) is dialog:
            self._job_preview_dialog = None

    def _run_from_job_preview(self, dialog: JobPreviewDialog) -> None:
        if getattr(self, "_job_preview_dialog", None) is not dialog:
            return
        dialog.close()
        self.run_current_job()

    def _prepare_start_here(self, move_index: int) -> None:
        plan = self._current_job_plan()
        source_job = self.last_job
        if (
            plan is None
            or source_job is None
            or self.last_job_revision != self.document.revision
        ):
            self._invalidate_generated_job()
            self.show_error("The project changed; regenerate before using Start Here")
            return
        if not self._prepared_frame_is_current():
            self._invalidate_generated_job()
            self.show_error(
                "The prepared coordinate, laser spot-offset, or Air Assist authority "
                "changed; regenerate before using Start Here"
            )
            return
        preview = getattr(self, "_job_preview_dialog", None)
        message_parent = (
            preview
            if preview is not None and preview.isVisible()
            else self
        )
        answer = QtWidgets.QMessageBox.warning(
            message_parent,
            "Prepare Start Here job",
            f"This will replace the prepared job with a guarded program beginning "
            f"at preview move {move_index + 1}. Earlier moves will not run.\n\n"
            "The machine will not start. Review the replacement Preview before "
            "execution.",
            QtWidgets.QMessageBox.StandardButton.Ok
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Ok:
            return
        try:
            start_here_request_context = (
                self._capture_start_here_request_context(plan, source_job)
            )
        except (TypeError, ValueError) as exc:
            self._invalidate_generated_job()
            self.show_error(str(exc))
            return
        revision = self.document.revision
        work_area = start_here_request_context["work_area"]
        coordinate_frame_signature = self.last_job_coordinate_frame
        power_mode = start_here_request_context["power_mode"]
        controller_start_position = start_here_request_context[
            "start_position"
        ]
        source_preflight_report = getattr(
            self,
            "last_job_preflight_report",
            None,
        )
        self._invalidate_generated_job(cancel_preparation=False)
        self._job_request_id += 1
        request_id = self._job_request_id
        cancellation = threading.Event()
        self._job_worker_requests[request_id] = cancellation
        self._job_worker_phases[request_id] = "planning"
        self._job_cancel_reason = ""
        owner = ("worker", request_id)
        self._claim_job_preparation(owner, "Preparing guarded Start Here job")

        def operation() -> dict[str, Any] | None:
            if cancellation.is_set():
                return None
            try:
                text, restarted = restart_program_from_move(
                    plan,
                    move_index,
                    power_mode=power_mode,
                    start_position=controller_start_position,
                    cancel_check=cancellation.is_set,
                )
            except JobPlanCancelled:
                return None
            job = ProjectJob(
                text=text,
                bounds_mm=restarted.bounds_mm,
                cut_length_mm=restarted.cut_distance_mm,
                travel_length_mm=restarted.travel_distance_mm,
                estimated_seconds=restarted.total_seconds,
                path_count=sum(1 for move in restarted.moves if move.laser_on),
                point_count=len(restarted.moves),
                plan=restarted,
                spot_offset_mm=tuple(
                    start_here_request_context["spot_offset_mm"]
                ),
                air_assist_commands=restarted.air_assist_commands,
                raster_assets=source_job.raster_assets,
                coordinate_space=getattr(
                    source_job,
                    "coordinate_space",
                    CoordinateSpace.MACHINE,
                ),
                coordinate_frame_signature=getattr(
                    source_job,
                    "coordinate_frame_signature",
                    None,
                ),
                guarded_output_polygon_mm=getattr(
                    source_job,
                    "guarded_output_polygon_mm",
                    None,
                ),
            )
            job.execution_signature = coordinate_frame_signature
            if cancellation.is_set():
                return None
            verify_project_job_assets(job)
            try:
                prepared = prepare_job_preview(
                    restarted,
                    cancel_check=cancellation.is_set,
                )
            except JobPreviewPreparationCancelled:
                return None
            if cancellation.is_set():
                return None
            return {
                "job": job,
                "prepared": prepared,
                "filename": f"start-here-move-{move_index + 1}.gcode",
                "powered": restarted.powered,
                "revision": revision,
                "work_area": work_area,
                "coordinate_frame_signature": coordinate_frame_signature,
                "preflight_report": source_preflight_report,
                "start_here_request_context": start_here_request_context,
                "frame": False,
                "summary": (
                    f"Start Here at original move {move_index + 1} · "
                    f"{restarted.cut_distance_mm:.1f} mm cut · "
                    f"estimated {restarted.total_seconds:.1f} s"
                ),
                "notice": "Prepared Start Here job; machine has not started",
            }

        self.controller.run_background(
            operation,
            on_success=lambda payload, request_id=request_id: (
                self._job_generation_ready(request_id, payload)
            ),
            on_failure=lambda message, request_id=request_id: (
                self._start_here_failed(request_id, message)
            ),
            cancel=cancellation.set,
            label="Prepare Start Here job",
        )

    def _start_here_failed(self, request_id: int, message: str) -> None:
        if self._job_worker_phases.get(request_id) != "planning":
            return
        if request_id != self._job_request_id:
            self._finish_stale_job_worker(request_id)
            return
        self._finish_job_worker(request_id)
        owner = ("worker", request_id)
        if self._job_preparation_owner != owner:
            return
        self._release_job_preparation(owner)
        self.show_error(message)

    def _laser_spot_offset_summary(self) -> str:
        laser = self.runtime.settings.laser
        if (
            abs(laser.spot_offset_x_mm) < 1e-12
            and abs(laser.spot_offset_y_mm) < 1e-12
        ):
            return ""
        return (
            f" · spot offset X{laser.spot_offset_x_mm:g} "
            f"Y{laser.spot_offset_y_mm:g} mm"
        )

    def export_gcode(self) -> None:
        if self.last_job is None:
            self.show_error("Generate a project toolpath first")
            return
        if self._job_preparation_busy:
            self.show_error("Wait for exact job Preview preparation before exporting")
            return
        if self.last_job_revision != self.document.revision:
            self._invalidate_generated_job()
            self.show_error("The project changed; regenerate before exporting")
            return
        if not self._prepared_frame_is_current():
            self._invalidate_generated_job()
            self.show_error(
                "The prepared coordinate, laser spot-offset, or Air Assist authority "
                "changed; regenerate before exporting"
            )
            return
        machine_area = self._work_area_signature(
            self.runtime.settings.machine.work_area
        )
        if self.last_job_work_area != machine_area:
            self.show_error(
                "The prepared job work area does not match the configured machine. "
                "Regenerate or prepare the job for this machine before exporting."
            )
            return
        if not self._verify_prepared_job_assets("exporting"):
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export validated G-code",
            self.last_job_name or "job.gcode",
            "G-code (*.gcode *.nc);;All files (*)",
        )
        if not path:
            return
        try:
            atomic_write_text(Path(path), self.last_job.text)
        except OSError as exc:
            self.show_error(f"Could not export G-code: {exc}")
            return
        self.show_notice(f"Exported {Path(path).name}")

    def run_current_job(self) -> None:
        if self.last_job is None:
            self.show_error("Generate a project toolpath first")
            return
        if getattr(self, "_job_preparation_busy", False):
            self.show_error("Wait for exact job Preview preparation before running")
            return
        if self.last_job_revision != self.document.revision:
            self._invalidate_generated_job()
            self.show_error("The project changed; regenerate the toolpath before running")
            return
        if not self._prepared_frame_is_current():
            self._invalidate_generated_job()
            self.show_error(
                "The prepared coordinate, laser spot-offset, or Air Assist authority "
                "changed; regenerate the toolpath before running"
            )
            return
        if not self._verify_prepared_job_assets("running"):
            return
        machine_area = self._work_area_signature(
            self.runtime.settings.machine.work_area
        )
        if self.last_job_work_area != machine_area:
            self.show_error(
                "The prepared job work area does not match the configured machine. "
                "Regenerate or prepare the job for this machine before running."
            )
            return
        if not self.runtime.settings.machine.allow_motion:
            self.show_error("Motion is blocked in the local configuration")
            return

        machine = self.runtime.context.machine.status()
        phrase: str | None = None
        if self.last_job_powered:
            phrase = str(machine.get("arm_phrase", "ENABLE LASER CONTROL"))

        pending = self._pending_calibration_capture
        if pending is not None and self.last_job_name == pending.get("filename"):
            pending["submitted"] = True
            pending["baseline_job"] = self._machine_job_identity(machine.get("job"))
            pending.pop("started_at", None)
            pending.pop("program_digest", None)

        run_options: dict[str, Any] = {"arm_phrase": phrase}
        if self.last_job_coordinate_frame is not None:
            run_options["honeycomb_signature"] = self.last_job_coordinate_frame
            run_options["guarded_output_polygon_mm"] = getattr(
                self.last_job,
                "guarded_output_polygon_mm",
                None,
            )
        elif self.last_job_powered:
            calibration_signature = (
                self.runtime.context.calibration_job_honeycomb_signature(
                    self.last_job_name
                )
            )
            if calibration_signature is not None:
                run_options["honeycomb_signature"] = calibration_signature
                run_options["guarded_output_polygon_mm"] = (
                    self.runtime.context.calibration_job_guarded_output_polygon(
                        self.last_job_name
                    )
                )
        self.controller.run_job(
            self.last_job.text,
            self.last_job_name,
            **run_options,
        )
        if bool(
            getattr(self.runtime.context.machine, "pi_owned_execution", False)
        ):
            self.show_notice(
                "Preparing and uploading the exact job to the Raspberry Pi…"
            )
        elif self.runtime.settings.machine.backend == "serial":
            self.show_notice("Homing and parking before job start…")

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
            if payload.get("camera_image_area") is None:
                self._camera_image_ready(camera_image)
            else:
                self._camera_image_ready(
                    camera_image,
                    image_area=E3MainWindow._camera_image_area(
                        payload.get("camera_image_area")
                    ),
                    fit=True,
                )
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
            signature = self._template_match_result.get("review_signature")
            if signature is not None and not self.controller.review_signature_is_current(
                signature
            ):
                self._calibration_review_evidence_invalidated()
                self.show_notice(
                    "The honeycomb or bed mapping changed; run template alignment again"
                )
                return
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
        if self._template_match_result is not None:
            signature = self._template_match_result.get("review_signature")
            if signature is not None and not self.controller.review_signature_is_current(
                signature
            ):
                self._calibration_review_evidence_invalidated()
                self.show_error(
                    "The honeycomb or bed mapping changed; run template alignment again"
                )
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
            "Trace mode: detect candidates, then click, Ctrl-click, or drag on "
            "the camera image to choose the outlines you want."
        )

    def _detect_trace_objects(self, raw_options: dict[str, Any]) -> None:
        # A new request immediately retires any old, non-project candidate
        # overlays. Workspace project objects are owned separately and remain.
        E3MainWindow._retire_trace_preview_ui(self)
        self._reconcile_pristine_project_frame()
        try:
            self._require_project_machine_work_area_match()
        except Exception as exc:
            self.controller.cancel_trace_detection()
            self.show_error(f"Object detection unavailable: {exc}")
            return
        self._clear_template_preview(show_message=False)
        if hasattr(self, "trace_panel"):
            self.trace_panel.begin_detection()
        self._active_trace_request_id = self.controller.detect_trace_objects(
            raw_options
        )

    def _begin_trace_color_pick(self) -> None:
        if self.runtime.context.bed.calibration is None:
            self.show_error("Bed mapping is required before sampling camera color")
            return
        self._reconcile_pristine_project_frame()
        try:
            self._require_project_machine_work_area_match()
        except Exception as exc:
            self.show_error(f"Color sampling unavailable: {exc}")
            return
        if self.workspace.point_pick_active:
            self.workspace.cancel_point_pick()
            self.trace_panel.set_color_pick_active(False)
            self.show_notice("Color picking cancelled")
            return
        self._activate_selection_tool(show_message=False)
        self._clear_template_preview(show_message=False)
        self.inspector_tabs.select_panel("trace")
        self.workspace.begin_point_pick()
        self.trace_panel.set_color_pick_active(True)
        self.show_notice("Click the center of one target object in the camera image")

    def _trace_point_picked(self, x_mm: float, y_mm: float) -> None:
        self.trace_panel.set_color_pick_active(True, sampling=True)
        self.controller.sample_trace_color(x_mm, y_mm)

    def _trace_color_ready(self, payload: dict[str, Any]) -> None:
        self.trace_panel.set_color_sample(payload)
        self.inspector_tabs.select_panel("trace")
        if "honeycomb_x" in payload:
            self.show_notice(
                f"Sampled target color at honeycomb X{payload['honeycomb_x']:.2f} "
                f"Y{payload['honeycomb_y']:.2f}"
            )
            return
        self.show_notice(
            f"Sampled target color at X{payload['machine_x']:.2f} "
            f"Y{payload['machine_y']:.2f}"
        )

    def _trace_color_failed(self, message: str) -> None:
        self.trace_panel.set_color_pick_failed(message)
        self.show_error(f"Sample trace color failed: {message}")

    @staticmethod
    def _trace_raster_preview_value(preview: object, name: str) -> object:
        """Adapt the frozen vision value without coupling the UI to its class."""

        if isinstance(preview, dict):
            return preview.get(name)
        return getattr(preview, name, None)

    @QtCore.Slot(int, object)
    def _trace_raster_preview_ready(
        self,
        request_id: int,
        payload: object,
    ) -> None:
        if (
            getattr(self, "_close_requested", False)
            or getattr(self, "_closing", False)
            or getattr(self.controller, "_shutdown_started", False)
        ):
            return
        if request_id != self._active_trace_request_id:
            return
        if not isinstance(payload, dict):
            return
        signature = payload.get("review_signature")
        if signature is None or not self.controller.review_signature_is_current(
            signature
        ):
            return
        preview = payload.get("preview")
        if preview is None:
            return
        arrays = {
            "camera": self._trace_raster_preview_value(preview, "camera_bgr"),
            "exposed_bed": self._trace_raster_preview_value(
                preview,
                "exposed_bed_mask",
            ),
            "eligible": self._trace_raster_preview_value(
                preview,
                "eligible_mask",
            ),
            "normalized": self._trace_raster_preview_value(
                preview,
                "normalized_grayscale",
            ),
            "mask": self._trace_raster_preview_value(
                preview,
                "contour_mask",
            ),
        }
        if any(value is None for value in arrays.values()):
            return
        try:
            images = {
                name: image_to_qimage(value)
                for name, value in arrays.items()
            }
        except (AttributeError, TypeError, ValueError):
            return
        if any(image.isNull() for image in images.values()):
            return
        area = self._camera_image_area(payload.get("camera_image_area"))
        if area is None:
            return
        # Temporary physical-build evidence for the Camera-to-Mask display
        # boundary. Avoid hashing full frames when INFO diagnostics are disabled.
        if LOGGER.isEnabledFor(logging.INFO):
            for name, image in images.items():
                image_format = image.format()
                LOGGER.info(
                    "Camera Trace preview slot %s: %d x %d, format=%s, "
                    "bytes=%d, pixel_sha256=%s",
                    name,
                    image.width(),
                    image.height(),
                    getattr(image_format, "name", str(image_format)),
                    image.sizeInBytes(),
                    _qimage_content_sha256(image),
                )
        self._trace_raster_preview_images = images
        self._trace_raster_preview_area = area
        self._trace_raster_preview_signature = signature
        strategy = self._trace_raster_preview_value(preview, "strategy")
        selected_strategy = bool(
            self._trace_raster_preview_value(preview, "selected_strategy")
        )
        native_fitting_completed = bool(
            self._trace_raster_preview_value(preview, "native_fitting_completed")
        )
        self.trace_panel.set_raster_preview_available(
            "" if strategy is None else str(strategy),
            selected_strategy=selected_strategy,
            native_fitting_completed=native_fitting_completed,
        )
        self._trace_raster_preview_mode_changed(
            self.trace_panel.raster_preview_mode()
        )

    def _trace_raster_preview_mode_changed(self, mode: str) -> None:
        image = self._trace_raster_preview_images.get(str(mode))
        if image is None or image.isNull():
            return
        camera = self._trace_raster_preview_images.get("camera")
        display_ppm: float | None = None
        source_resolution_multiplier = 1
        if camera is not None and not camera.isNull() and camera.width() > 0:
            if (
                camera.height() <= 0
                or image.width() % camera.width() != 0
                or image.height() % camera.height() != 0
            ):
                self._trace_raster_preview_display_failed(
                    mode,
                    "the selected image is not an integer-resolution derivative "
                    "of the corrected Camera frame",
                )
                return
            source_resolution_multiplier = image.width() // camera.width()
            if (
                source_resolution_multiplier < 1
                or image.height() // camera.height()
                != source_resolution_multiplier
            ):
                self._trace_raster_preview_display_failed(
                    mode,
                    "the selected image has inconsistent horizontal and vertical "
                    "resolution multipliers",
                )
                return
            scale = float(source_resolution_multiplier)
            runtime = getattr(self, "runtime", None)
            settings = getattr(runtime, "settings", None)
            calibration = getattr(settings, "calibration", None)
            bed = getattr(calibration, "bed", None)
            base_ppm = getattr(bed, "pixels_per_mm", None)
            if base_ppm is not None:
                display_ppm = float(base_ppm) * scale
            elif self._trace_raster_preview_area is not None:
                display_ppm = (
                    camera.width() / self._trace_raster_preview_area.width * scale
                )
        try:
            self._camera_image_ready(
                image,
                image_area=self._trace_raster_preview_area,
                pixels_per_mm=display_ppm,
                source_resolution_multiplier=source_resolution_multiplier,
            )
        except ValueError as exc:
            self._trace_raster_preview_display_failed(mode, str(exc))

    def _trace_raster_preview_display_failed(self, mode: str, reason: str) -> None:
        message = f"Could not display Camera Trace {str(mode).title()} preview: {reason}"
        LOGGER.error(message)
        workspace = getattr(self, "workspace", None)
        image_setter = getattr(workspace, "set_camera_image", None)
        if callable(image_setter):
            image_setter(None)
        notice = getattr(self, "show_notice", None)
        if callable(notice):
            notice(message)

    @QtCore.Slot(int, str, bool)
    def _trace_detection_failed(
        self,
        request_id: int,
        message: str,
        retain_preview: bool,
    ) -> None:
        if request_id != self._active_trace_request_id:
            return
        self._active_trace_request_id = None
        self._trace_result = None
        retained = bool(
            retain_preview
            and self._trace_raster_preview_images
            and self._trace_raster_preview_area is not None
        )
        if not retained:
            self._trace_raster_preview_images = {}
            self._trace_raster_preview_area = None
            self._trace_raster_preview_signature = None
            if retain_preview:
                # A retained controller hold without a delivered diagnostic
                # would be invisible to the operator, so fail back to live view.
                self.controller.cancel_trace_detection()
        self.workspace.clear_trace_preview()
        self.trace_panel.set_detection_failed(
            message,
            retain_preview=retained,
        )
        self.inspector_tabs.select_panel("trace")

    def _trace_result_ready(self, result: dict[str, Any]) -> None:
        request_id = result.get("request_id")
        if request_id is not None and request_id != self._active_trace_request_id:
            return
        self._active_trace_request_id = None
        camera_image = result.get("camera_image")
        if isinstance(camera_image, QtGui.QImage) and not camera_image.isNull():
            if self._trace_raster_preview_images:
                self._trace_raster_preview_images["camera"] = camera_image.copy()
                area = E3MainWindow._camera_image_area(
                    result.get("camera_image_area")
                )
                if area is not None:
                    self._trace_raster_preview_area = area
                self._trace_raster_preview_mode_changed(
                    self.trace_panel.raster_preview_mode()
                )
            elif result.get("camera_image_area") is None:
                self._camera_image_ready(camera_image)
            else:
                self._camera_image_ready(
                    camera_image,
                    image_area=E3MainWindow._camera_image_area(
                        result.get("camera_image_area")
                    ),
                    fit=True,
                )
        self._trace_result = result
        self.trace_panel.set_result(result)
        self.inspector_tabs.select_panel("trace")
        preview_args = (
            list(result.get("detections", [])),
            self.trace_panel.selected_ids(),
            result.get("honeycomb_support"),
        )
        if result.get("output_work_area") is None:
            output_polygon = result.get("output_polygon_local_mm")
            if output_polygon is None:
                self.workspace.set_trace_preview(*preview_args)
            else:
                self.workspace.set_trace_preview(
                    *preview_args,
                    output_polygon=output_polygon,
                )
        else:
            self.workspace.set_trace_preview(
                *preview_args,
                result.get("output_work_area"),
            )
        self.show_notice(str(result.get("message", "Object detection complete")))

    def _trace_selection_changed(self, selected_ids: list[str]) -> None:
        if self._trace_result is None:
            return
        self.workspace.set_trace_selected_ids(selected_ids)

    def _trace_canvas_selection_changed(self, selected_ids: list[str]) -> None:
        if self._trace_result is None:
            return
        self.trace_panel.set_selected_ids(selected_ids)

    @staticmethod
    def _trace_object_world_geometry(item: SceneObject) -> NativePathGeometry:
        """Return one finished native project object in current world coordinates."""

        transform = item.transform
        affine = PathAffineTransform.from_components(
            scale_x=transform.width_mm * (-1.0 if transform.mirror_x else 1.0),
            scale_y=transform.height_mm * (-1.0 if transform.mirror_y else 1.0),
            rotation_deg=transform.rotation_deg,
            translate_x=transform.x_mm,
            translate_y=transform.y_mm,
        )
        return transform_native_path(item.path_geometry(), affine)

    def _selected_trace_orientation_geometry(
        self,
        objects: list[SceneObject],
    ) -> tuple[TraceOrientationGeometry, ...]:
        """Adapt an eligible normal project selection to the image-free estimator."""

        if not objects:
            return ()
        candidates: list[tuple[SceneObject, str]] = []
        artwork_members: dict[str, list[tuple[int, int, str]]] = {}
        total_subpaths = 0
        total_segments = 0
        for item in objects:
            metadata = item.metadata
            trace_source = metadata.get("trace_source")
            artwork_id = metadata.get("trace_artwork_id")
            member_index = metadata.get("trace_artwork_member_index")
            member_count = metadata.get("trace_artwork_member_count")
            if (
                metadata.get("trace_orientation_eligible") is not True
                or metadata.get("trace_output_mode") != "native"
                or not isinstance(trace_source, str)
                or not trace_source.strip()
                or metadata.get("trace_grid_normalized") is True
                or is_stock_boundary(item)
                or item.kind not in {ObjectKind.PATH, ObjectKind.POLYGON}
                or not isinstance(artwork_id, str)
                or not artwork_id.strip()
                or type(member_index) is not int
                or type(member_count) is not int
                or member_count < 1
                or not 0 <= member_index < member_count
                or metadata.get("trace_creation_mode") not in {"combined", "separate"}
            ):
                return ()

            raw_subpaths = item.geometry.get("subpaths")
            if not isinstance(raw_subpaths, list):
                return ()
            total_subpaths += len(raw_subpaths)
            if total_subpaths > MAX_TRACE_ORIENTATION_SUBPATHS:
                return ()
            for raw_subpath in raw_subpaths:
                if not isinstance(raw_subpath, dict):
                    return ()
                raw_segments = raw_subpath.get("segments")
                if not isinstance(raw_segments, list):
                    return ()
                total_segments += len(raw_segments)
                if total_segments > MAX_TRACE_ORIENTATION_SEGMENTS:
                    return ()

            candidates.append((item, artwork_id))
            artwork_members.setdefault(artwork_id, []).append(
                (
                    member_index,
                    member_count,
                    str(metadata["trace_creation_mode"]),
                )
            )
        for members in artwork_members.values():
            member_counts = {member[1] for member in members}
            creation_modes = {member[2] for member in members}
            if len(member_counts) != 1 or len(creation_modes) != 1:
                return ()
            member_count = next(iter(member_counts))
            creation_mode = next(iter(creation_modes))
            if len(members) != member_count or {
                member[0] for member in members
            } != set(range(member_count)):
                return ()
            if creation_mode == "combined" and member_count != 1:
                return ()

        adapted: list[TraceOrientationGeometry] = []
        for item, artwork_id in candidates:
            try:
                world_geometry = self._trace_object_world_geometry(item)
            except (TypeError, ValueError):
                return ()
            adapted.append(
                TraceOrientationGeometry(
                    object_id=item.id,
                    artwork_id=artwork_id,
                    geometry=world_geometry,
                )
            )
        return tuple(adapted)

    def _estimate_selected_trace_orientation(
        self,
        objects: list[SceneObject],
    ) -> TraceOrientationEstimate | None:
        geometry = self._selected_trace_orientation_geometry(objects)
        if not geometry:
            return None
        estimate = estimate_trace_orientation(geometry)
        LOGGER.info(
            "Selected Camera Trace artwork orientation estimate: %s",
            estimate.to_diagnostics(),
        )
        return estimate

    def _update_selected_trace_orientation(
        self,
        objects: list[SceneObject],
    ) -> TraceOrientationEstimate | None:
        panel = getattr(self, "transform_panel", None)
        if panel is None:
            return None
        estimate = self._estimate_selected_trace_orientation(objects)
        if estimate is None:
            clearer = getattr(panel, "clear_straighten_review", None)
            if callable(clearer):
                clearer()
            return None
        setter = getattr(panel, "set_straighten_review", None)
        if callable(setter):
            setter(estimate, eligible=True)
        return estimate

    def _straighten_selected_trace_objects(self) -> None:
        selected = set(self.workspace.selected_object_ids())
        objects = [item for item in self.document.objects if item.id in selected]
        estimate = self._update_selected_trace_orientation(objects)
        ordered_ids = tuple(item.id for item in objects)
        if (
            estimate is None
            or not estimate.offered
            or estimate.selected_ids != ordered_ids
            or estimate.correction_deg is None
            or estimate.pivot_mm is None
        ):
            return

        rotation = trace_rotation_transform(
            estimate.correction_deg,
            estimate.pivot_mm,
        )
        transforms: dict[str, Transform] = {}
        for item in objects:
            center_x, center_y = rotation.apply(
                (item.transform.x_mm, item.transform.y_mm)
            )
            transforms[item.id] = item.transform.copy(
                x_mm=center_x,
                y_mm=center_y,
                rotation_deg=item.transform.rotation_deg + estimate.correction_deg,
            )
        self.history.execute(
            UpdateTransformsCommand(
                self.document,
                transforms,
                description="Straighten Trace artwork",
            )
        )
        self.workspace.select_objects(list(ordered_ids))

    def _retire_trace_preview_ui(self) -> None:
        preview_camera = getattr(self, "_trace_raster_preview_images", {}).get(
            "camera"
        )
        preview_area = getattr(self, "_trace_raster_preview_area", None)
        preview_signature = getattr(
            self,
            "_trace_raster_preview_signature",
            None,
        )
        signature_checker = getattr(
            self.controller,
            "review_signature_is_current",
            None,
        )
        restore_camera = bool(
            isinstance(preview_camera, QtGui.QImage)
            and not preview_camera.isNull()
            and preview_signature is not None
            and callable(signature_checker)
            and signature_checker(preview_signature)
        )
        self._active_trace_request_id = None
        self._trace_raster_preview_images = {}
        self._trace_raster_preview_area = None
        self._trace_raster_preview_signature = None
        self._trace_result = None
        workspace = getattr(self, "workspace", None)
        workspace_clear = getattr(workspace, "clear_trace_preview", None)
        if callable(workspace_clear):
            workspace_clear()
        if hasattr(self, "trace_panel"):
            self.trace_panel.clear_result()
        camera_publisher = getattr(self, "_camera_image_ready", None)
        if restore_camera and callable(camera_publisher):
            camera_publisher(preview_camera, image_area=preview_area)

    def _clear_trace_preview(self) -> None:
        E3MainWindow._retire_trace_preview_ui(self)
        self.controller.cancel_trace_detection()

    def _trace_detection_to_object(
        self,
        detection: dict[str, Any],
        output_mode: str,
    ) -> SceneObject:
        index = int(detection.get("index", 0))
        name = f"Traced object {index:02d}"
        diagnostics = detection.get("diagnostics") or {}
        if diagnostics.get("grid_normalized"):
            name = (
                f"Grid R{int(diagnostics.get('grid_row', 0)) + 1} "
                f"C{int(diagnostics.get('grid_column', 0)) + 1}"
            )
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
        elif (
            output_mode == "rounded"
            and detection.get("shape") in {"circle", "ellipse"}
        ):
            item = SceneObject.ellipse(
                self.active_layer_id,
                name=name,
                center=center,
                width_mm=float(detection["width_mm"]),
                height_mm=float(detection["height_mm"]),
            )
            item.transform = item.transform.copy(
                rotation_deg=float(detection.get("rotation_deg", 0.0))
            )
        elif detection.get("native_verified") and detection.get("native_path"):
            item = SceneObject.native_path(
                self.active_layer_id,
                detection["native_path"],
                name=name,
                center=tuple(
                    float(value)
                    for value in detection.get("native_center_mm", center)
                ),
                width_mm=float(detection["native_width_mm"]),
                height_mm=float(detection["native_height_mm"]),
                source_name="camera trace",
            )
            item.metadata["trace_detector_center_mm"] = list(center)
        else:
            raw_contours = detection.get("vector_contours_mm") or [
                detection.get("vector_contour_mm")
                or detection.get("contour_mm")
                or []
            ]
            contours = [
                [[float(point[0]), float(point[1])] for point in contour]
                for contour in raw_contours
                if len(contour) >= 3
            ]
            points = [point for contour in contours for point in contour]
            if len(points) < 3:
                raise ValueError(f"Trace {index} has no usable contour")
            path_center = (
                (min(point[0] for point in points) + max(point[0] for point in points))
                / 2.0,
                (min(point[1] for point in points) + max(point[1] for point in points))
                / 2.0,
            )
            item = SceneObject.path(
                self.active_layer_id,
                [
                    {"points": contour, "closed": True}
                    for contour in contours
                ],
                name=name,
                center=path_center,
                source_name="camera trace",
            )
            item.metadata["trace_detector_center_mm"] = list(center)
        item.metadata.update(
            {
                "trace_source": detection.get("source", "direct"),
                "trace_confidence": float(detection.get("confidence", 0.0)),
                "trace_shape": detection.get("shape", output_mode),
                "shape_kind": detection.get("shape", "freeform_contour"),
            }
        )
        diagnostics = detection.get("diagnostics") or {}
        if detection.get("shape") == "washer" and "hole_ratio" in diagnostics:
            item.metadata["hole_ratio"] = float(diagnostics["hole_ratio"])
        if diagnostics.get("grid_normalized"):
            item.metadata.update(
                {
                    "trace_grid_normalized": True,
                    "trace_grid_row": int(diagnostics.get("grid_row", 0)),
                    "trace_grid_column": int(diagnostics.get("grid_column", 0)),
                }
            )
        return item

    @staticmethod
    def _trace_detection_world_geometry(
        detection: dict[str, Any],
    ) -> NativePathGeometry:
        if detection.get("native_verified") and detection.get("native_path"):
            geometry = NativePathGeometry.from_dict(detection["native_path"])
            center = detection.get("native_center_mm") or detection["center_mm"]
            return transform_native_path(
                geometry,
                PathAffineTransform.from_components(
                    scale_x=float(detection["native_width_mm"]),
                    scale_y=float(detection["native_height_mm"]),
                    translate_x=float(center[0]),
                    translate_y=float(center[1]),
                ),
            )
        raw_contours = detection.get("vector_contours_mm") or [
            detection.get("vector_contour_mm")
            or detection.get("contour_mm")
            or []
        ]
        contours = [
            {
                "points": [
                    [float(point[0]), float(point[1])] for point in contour
                ],
                "closed": True,
            }
            for contour in raw_contours
            if len(contour) >= 3
        ]
        if not contours:
            raise ValueError(
                f"Trace {detection.get('index', 0)} has no usable contour"
            )
        return NativePathGeometry.from_legacy_polylines(contours)

    def _combined_trace_object(
        self,
        detections: list[dict[str, Any]],
    ) -> SceneObject:
        world_subpaths = []
        for detection in detections:
            world_subpaths.extend(
                self._trace_detection_world_geometry(detection).subpaths
            )
        world_geometry = NativePathGeometry(
            tuple(world_subpaths),
            fill_rule=PathFillRule.EVENODD,
        )
        min_x, min_y, max_x, max_y = native_path_bounds(world_geometry)
        width = max_x - min_x
        height = max_y - min_y
        if width <= 0.0 or height <= 0.0:
            raise ValueError("Combined camera trace has zero width or height")
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        normalized = transform_native_path(
            world_geometry,
            PathAffineTransform.from_components(
                scale_x=1.0 / width,
                scale_y=1.0 / height,
                translate_x=-center_x / width,
                translate_y=-center_y / height,
            ),
        )
        item = SceneObject.native_path(
            self.active_layer_id,
            normalized,
            name="Combined camera trace",
            center=(center_x, center_y),
            width_mm=width,
            height_mm=height,
            source_name="combined camera trace",
        )
        item.metadata.update(
            {
                "trace_source": "combined",
                "trace_compound": True,
                "trace_detection_ids": [
                    str(detection.get("id")) for detection in detections
                ],
                "trace_detection_count": len(detections),
            }
        )
        return item

    def _create_traced_objects(self, payload: dict[str, Any]) -> None:
        if self._trace_result is None:
            self.show_error("Run object detection before creating vector paths")
            return
        signature = self._trace_result.get("review_signature")
        if signature is not None and not self.controller.review_signature_is_current(
            signature
        ):
            self._clear_trace_preview()
            self.show_error(
                "The honeycomb or bed mapping changed; run object detection again"
            )
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
        purpose = str(payload.get("purpose", "cut"))
        combine = bool(payload.get("combine", False)) and purpose == "cut"
        if purpose == "stock" and len(detections) != 1:
            self.show_notice("Select exactly one outline for the Stock boundary")
            return
        if output_mode == "native" and any(
            detection.get("native_verified") is not True
            or not detection.get("native_path")
            for detection in detections
        ):
            self.show_error(
                "The selected native Trace geometry is not verified; run detection again"
            )
            return
        options = self._trace_result.get("options") or {}
        trace_detail = str(options.get("trace_detail", "full")).strip().lower()
        if trace_detail not in {"full", "outer_silhouette"}:
            trace_detail = "full"
        grid_result = bool(
            options.get("regular_grid", False)
            or self._trace_result.get("grid")
            or any(
                bool((detection.get("diagnostics") or {}).get("grid_normalized"))
                for detection in detections
            )
        )
        orientation_eligible = bool(
            self._trace_result.get("detected") is True
            and purpose == "cut"
            and output_mode == "native"
            and not grid_result
        )
        replaced_count = 0
        try:
            objects = (
                [self._combined_trace_object(detections)]
                if combine
                else [
                    self._trace_detection_to_object(item, output_mode)
                    for item in detections
                ]
            )
            if purpose == "stock":
                objects = [mark_stock_boundary(objects[0])]
            if trace_detail == "outer_silhouette":
                for item in objects:
                    item.metadata["trace_detail"] = trace_detail
            if orientation_eligible and all(
                item.kind in {ObjectKind.PATH, ObjectKind.POLYGON}
                for item in objects
            ):
                artwork_id = f"trace-artwork-{uuid.uuid4().hex}"
                creation_mode = "combined" if combine else "separate"
                member_count = len(objects)
                for member_index, item in enumerate(objects):
                    item.metadata.update(
                        {
                            "trace_orientation_eligible": True,
                            "trace_output_mode": "native",
                            "trace_artwork_id": artwork_id,
                            "trace_artwork_member_index": member_index,
                            "trace_artwork_member_count": member_count,
                            "trace_creation_mode": creation_mode,
                        }
                    )
            replace_previous = bool(payload.get("replace_previous", True))
            previous_trace_ids = [
                item.id
                for item in self.document.objects
                if (
                    is_stock_boundary(item)
                    if purpose == "stock"
                    else "trace_source" in item.metadata
                    and not is_stock_boundary(item)
                )
            ]
            if purpose == "stock":
                create_description = "Create Stock boundary"
                replace_description = "Replace previous Stock boundary"
            else:
                if combine:
                    create_description = "Create combined Trace vector"
                    replace_description = "Replace previous Trace objects with combined vector"
                else:
                    suffix = "" if len(objects) == 1 else "s"
                    create_description = f"Create {len(objects)} traced object{suffix}"
                    replace_description = (
                        "Replace previous Trace objects with "
                        f"{len(objects)} object{suffix}"
                    )
            if replace_previous and previous_trace_ids:
                replaced_count = len(previous_trace_ids)
                command = ReplaceObjectsCommand(
                    self.document,
                    previous_trace_ids,
                    objects,
                    description=replace_description,
                )
            else:
                command = AddObjectsCommand(
                    self.document,
                    objects,
                    description=create_description,
                )
            self.history.execute(command)
        except Exception as exc:
            self.show_error(f"Could not create traced objects: {exc}")
            return
        self._clear_trace_preview()
        self.workspace.select_objects([item.id for item in objects])
        if purpose == "cut":
            inspector = getattr(self, "inspector_tabs", None)
            select_panel = getattr(inspector, "select_panel", None)
            if callable(select_panel):
                select_panel("transform")
        if purpose == "stock":
            self.show_notice(
                "Created a locked Stock boundary. It is visible for layout, "
                "never included in laser output, and enables the Stock layout "
                "toolbar when artwork is selected."
            )
        elif replaced_count:
            self.show_notice(
                f"Replaced {replaced_count} earlier Trace object"
                f"{'s' if replaced_count != 1 else ''} with {len(objects)} new "
                f"object{'s' if len(objects) != 1 else ''}"
            )
        else:
            self.show_notice(
                f"Created {len(objects)} editable vector object"
                f"{'s' if len(objects) != 1 else ''}"
            )

    @staticmethod
    def _camera_image_area(payload: object) -> Bounds | None:
        if not isinstance(payload, dict):
            return None
        try:
            return Bounds(
                float(payload["x_min"]),
                float(payload["y_min"]),
                float(payload["x_max"]),
                float(payload["y_max"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _camera_image_ready(
        self,
        payload: object,
        *,
        image_area: Bounds | None = None,
        pixels_per_mm: float | None = None,
        source_resolution_multiplier: int = 1,
        fit: bool = False,
    ) -> None:
        image = payload
        if isinstance(payload, dict):
            image = payload.get("image")
            if image_area is None:
                image_area = self._camera_image_area(
                    payload.get("camera_image_area")
                )
        if not isinstance(image, QtGui.QImage) or image.isNull():
            return
        effective_area = (
            self.workspace.workspace_scene.work_area
            if image_area is None
            else image_area
        )
        area_changed = getattr(
            self.workspace,
            "_camera_image_area",
            None,
        ) != effective_area
        self.workspace.set_camera_image(
            image,
            pixels_per_mm=(
                self.runtime.settings.calibration.bed.pixels_per_mm
                * source_resolution_multiplier
                if pixels_per_mm is None
                else pixels_per_mm
            ),
            image_area=image_area,
            source_resolution_multiplier=source_resolution_multiplier,
        )
        if fit or area_changed:
            self.workspace.fit_camera_image()
        self.camera_panel.set_image_updated()

    def _camera_image_invalidated(self) -> None:
        self.workspace.set_camera_image(None)

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
        machine_profile_id, tool_head_profile_id = (
            E3MainWindow._running_material_profile_ids(self)
        )
        self.material_panel.set_profile_context(
            machine_profile_id,
            tool_head_profile_id,
        )
        self.camera_panel.set_status(camera)
        self.camera_panel.set_calibration_profile(status.get("calibration_profile"))
        calibration_ready = bool((status.get("bed") or {}).get("calibrated", False))
        self.camera_panel.set_calibration_ready(calibration_ready)
        self.trace_panel.set_calibration_ready(calibration_ready)
        self.template_panel.set_calibration_ready(calibration_ready)
        self.machine_panel.set_status(machine)
        self.runtime_strip.set_status(status)
        if machine:
            self.console_panel.set_lines(list(machine.get("log", [])))
            self.job_progress.set_machine_status(machine)
            self.job_progress.set_job_status(machine.get("job"))
        if state == "running":
            camera_state = "camera online" if camera and camera.get("connected") else "camera offline"
            machine_job = (machine or {}).get("job") or {}
            if (
                machine_job.get("running")
                and machine_job.get("execution_owner") == "pi"
                and machine
                and machine.get("monitor_connected") is False
            ):
                machine_state = "Pi job running · connection lost"
            else:
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
        self._update_status_bar_layout()
        self._maybe_start_calibration_capture(machine)

    def _maybe_start_calibration_capture(
        self,
        machine: dict[str, Any] | None,
    ) -> None:
        pending = self._pending_calibration_capture
        if (
            pending is None
            or not machine
            or not pending.get("submitted", False)
            or pending.get("started_at") is None
            or not pending.get("program_digest")
        ):
            return
        job = machine.get("job") or {}
        if job.get("running") or job.get("finished_at") is None:
            return
        if str(job.get("name") or "") != str(pending["filename"]):
            return
        if (
            job.get("started_at") != pending["started_at"]
            or str(job.get("program_digest") or "") != pending["program_digest"]
            or self._machine_job_identity(job) == pending.get("baseline_job")
        ):
            return
        if job.get("error") or str(job.get("phase") or "") != "complete":
            self._pending_calibration_capture = None
            return
        self._pending_calibration_capture = None
        tab_index = int(pending["tab_index"])
        capture_action = str(pending["capture_action"])
        QtCore.QTimer.singleShot(
            0,
            lambda: self._open_automatic_calibration_capture(
                tab_index, capture_action
            ),
        )

    @staticmethod
    def _machine_job_identity(job: Any) -> tuple[Any, Any, str, str]:
        payload = job if isinstance(job, dict) else {}
        job_id = payload.get("job_id")
        if isinstance(job_id, str) and job_id:
            return (
                job_id,
                None,
                str(payload.get("program_digest") or ""),
                str(payload.get("name") or ""),
            )
        return (
            payload.get("started_at"),
            payload.get("finished_at"),
            str(payload.get("program_digest") or ""),
            str(payload.get("name") or ""),
        )

    def _job_started(self, job: dict[str, Any]) -> None:
        pending = self._pending_calibration_capture
        if (
            pending is None
            or not pending.get("submitted", False)
            or str(job.get("name") or "") != str(pending.get("filename") or "")
            or job.get("started_at") is None
            or not job.get("program_digest")
        ):
            return
        identity = self._machine_job_identity(job)
        if identity == pending.get("baseline_job"):
            return
        pending["started_at"] = job["started_at"]
        pending["program_digest"] = str(job["program_digest"])
        # A fast controller peer can finish before the queued start callback
        # reaches Qt. Poll once so the matching terminal state is not lost.
        self.controller.poll_status()

    def _open_automatic_calibration_capture(
        self,
        tab_index: int,
        capture_action: str,
    ) -> None:
        if self._closing:
            return
        self.open_machine_setup(
            tab_index,
            automatic_capture=capture_action,
        )

    def _busy_changed(self, busy: bool) -> None:
        self._controller_busy = bool(busy)
        self._sync_busy_indicators()

    def _claim_job_preparation(
        self,
        owner: tuple[str, int],
        label: str,
    ) -> None:
        self._job_preparation_owner = owner
        self._set_job_preparation_busy(True, label)

    def _update_job_preparation(
        self,
        owner: tuple[str, int],
        label: str,
        *,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        if self._job_preparation_owner != owner:
            return
        self._set_job_preparation_busy(
            True,
            label,
            completed=completed,
            total=total,
        )

    def _release_job_preparation(self, owner: tuple[str, int]) -> None:
        if self._job_preparation_owner != owner:
            return
        self._job_preparation_owner = None
        self._set_job_preparation_busy(False)

    def _set_authoring_frozen(self, request_id: int, frozen: bool) -> None:
        if frozen:
            if self._authoring_freeze_owner is not None:
                raise RuntimeError("Another project snapshot already owns authoring")
            self._authoring_freeze_owner = request_id
            self._authoring_action_states = {
                key: self.actions[key].isEnabled()
                for key in _AUTHORING_ACTION_KEYS
                if key in self.actions
            }
            for key in self._authoring_action_states:
                self.actions[key].setEnabled(False)
            for widget in (
                self.workspace,
                self.inspector_tabs,
                self.context_bar,
                self.stock_layout_toolbar,
                self.palette,
            ):
                widget.setEnabled(False)
            return
        if self._authoring_freeze_owner != request_id:
            return
        self._authoring_freeze_owner = None
        for key, enabled in self._authoring_action_states.items():
            if key in self.actions:
                if key == "undo":
                    enabled = self.history.can_undo
                elif key == "redo":
                    enabled = self.history.can_redo
                self.actions[key].setEnabled(enabled)
        self._authoring_action_states = {}
        for widget in (
            self.workspace,
            self.inspector_tabs,
            self.context_bar,
            self.stock_layout_toolbar,
            self.palette,
        ):
            widget.setEnabled(True)

    def _set_job_preparation_busy(
        self,
        busy: bool,
        label: str = "",
        *,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        self._job_preparation_busy = bool(busy)
        self._job_preparation_label = str(label) if busy else ""
        self.job_progress.set_preparing(
            busy,
            label or "Preparing exact job preview",
            completed=completed,
            total=total,
        )
        self._update_status_bar_layout()
        self.actions["generate"].setEnabled(not busy)
        ready = self.last_job is not None and not busy
        self.actions["preview_job"].setEnabled(ready)
        self.actions["export_gcode"].setEnabled(ready)
        self._sync_busy_indicators()

    def _sync_generate_controls(self) -> None:
        enabled = self.actions["generate"].isEnabled()
        self.template_panel.set_generate_enabled(enabled)
        self.trace_panel.set_generate_enabled(enabled)

    def _sync_busy_indicators(self) -> None:
        busy = self._controller_busy or self._job_preparation_busy
        self._busy = busy
        self.template_panel.set_busy(busy)
        self.machine_panel.set_busy(busy)
        self.runtime_strip.set_busy(busy)
        if self._job_preparation_busy:
            self.statusBar().showMessage(
                self._job_preparation_label or "Preparing exact job preview…"
            )
        else:
            self.statusBar().showMessage(
                "Working…" if self._controller_busy else "",
                0 if self._controller_busy else 1000,
            )


    def _refresh_machine_selector(self, selected_id: str | None = None) -> None:
        if not hasattr(self, "machine_selector"):
            return
        registry = self.runtime.machine_registry
        next_launch_id = registry.active_machine_id
        target_id = selected_id or next_launch_id
        running_identity = self.runtime.context.machine_identity
        self._updating_machine_selector = True
        try:
            self.machine_selector.clear()
            selected_index = 0
            for index, machine in enumerate(registry.machines()):
                badges: list[str] = []
                if machine.id == self.runtime.running_machine_id:
                    if machine.name == running_identity.machine_name:
                        badges.append("running")
                    else:
                        badges.append(
                            f"running as {running_identity.machine_name}"
                        )
                if machine.id == next_launch_id:
                    badges.append("next launch")
                suffix = f" ({', '.join(badges)})" if badges else ""
                self.machine_selector.addItem(machine.name + suffix, machine.id)
                if machine.id == target_id:
                    selected_index = index
            self.machine_selector.setCurrentIndex(selected_index)
            self.machine_selector.setToolTip(
                "Select the saved machine E3 should use on its next launch. "
                "Use Manage Machines to edit controller, work-area, laser, camera, "
                "and calibration bindings."
            )
        finally:
            self._updating_machine_selector = False

    @QtCore.Slot(int)
    def _machine_selector_activated(self, index: int) -> None:
        if self._updating_machine_selector or index < 0:
            return
        machine_id = self.machine_selector.itemData(index)
        if not machine_id:
            return
        try:
            self.runtime.machine_registry.set_active(str(machine_id))
            machine = self.runtime.machine_registry.get_machine(str(machine_id))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Machine selection", str(exc))
            self._refresh_machine_selector()
            return
        self._refresh_machine_selector(str(machine_id))
        self.statusBar().showMessage(
            f"{machine.name} selected for the next E3 launch. "
            "The running controller was not changed.",
            8000,
        )

    def open_machine_manager(
        self,
        *,
        focus_honeycomb_span: bool = False,
        focus_target: str | None = None,
    ) -> None:
        dialog = MachineManagerDialog(self.runtime, self)
        self._machine_manager_dialog = dialog
        dialog.registryChanged.connect(self._refresh_machine_selector)
        try:
            if focus_honeycomb_span:
                focus_target = "honeycomb_span"
            if focus_target is not None:
                QtCore.QTimer.singleShot(
                    0,
                    lambda: dialog.focus_navigation_target(focus_target),
                )
            dialog.exec()
        finally:
            self._machine_manager_dialog = None
            self._refresh_machine_selector()

    def open_machine_setup(
        self,
        tab_index: int = 0,
        *,
        automatic_capture: str | None = None,
        navigation_target: str | None = None,
    ) -> None:
        existing = self._machine_setup_dialog
        if existing is not None:
            existing.tabs.setCurrentIndex(int(tab_index))
            if navigation_target is not None:
                QtCore.QTimer.singleShot(
                    0,
                    lambda: existing.focus_navigation_target(navigation_target),
                )
            if automatic_capture is not None:
                capture = getattr(existing, automatic_capture)
                QtCore.QTimer.singleShot(0, capture)
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        was_live = self.camera_panel.live_enabled()
        self.controller.set_live_camera(False)
        dialog = MachineSetupDialog(
            self.runtime,
            self,
            navigation_only=navigation_target is not None,
        )
        self._machine_setup_dialog = dialog
        dialog.tabs.setCurrentIndex(tab_index)
        if navigation_target is not None:
            QtCore.QTimer.singleShot(
                0,
                lambda: dialog.focus_navigation_target(navigation_target),
            )
        dialog.calibrationChanged.connect(self.controller.poll_status)
        dialog.calibrationChanged.connect(self.controller.calibration_changed)
        dialog.calibrationChanged.connect(self._calibration_project_frame_changed)
        # Build the Preview only after the modal Setup event loop has unwound.
        # Constructing it synchronously from accept() can strand the first render
        # behind the closing modal dialog.
        dialog.registrationJobPrepared.connect(
            self._load_fine_registration_job,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        dialog.validationJobPrepared.connect(
            self._load_fine_registration_job,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.controller.set_calibration_review_active(True)
        try:
            if automatic_capture is not None:
                capture = getattr(dialog, automatic_capture)
                QtCore.QTimer.singleShot(0, capture)
            dialog.exec()
        finally:
            self.controller.set_calibration_review_active(False)
            if self._machine_setup_dialog is dialog:
                self._machine_setup_dialog = None
            dialog.deleteLater()
        self.controller.set_live_camera(was_live, self.camera_panel.refresh_interval_ms())
        self.controller.poll_status()

    def _reconcile_pristine_project_frame(self) -> bool:
        """Move only a disposable empty project into the current coordinate frame."""

        if (
            self.project_path is not None
            or self.document.objects
            or not self.history.is_clean
        ):
            return False
        replacement = self._new_document()
        current_frame = (
            self.document.coordinate_space,
            self._work_area_signature(self.document.work_area),
        )
        replacement_frame = (
            replacement.coordinate_space,
            self._work_area_signature(replacement.work_area),
        )
        if current_frame == replacement_frame:
            return False
        self._invalidate_generated_job()
        self.document = replacement
        self.active_layer_id = self.document.active_layer_id
        self.history.clear()
        self.history.mark_clean()
        self._clear_trace_preview()
        self._clear_template_preview(show_message=False)
        self._refresh_document()
        if self.document.coordinate_space is CoordinateSpace.HONEYCOMB_LOCAL:
            self.show_notice(
                "Updated the empty project to the detected honeycomb X0 Y0 frame "
                f"({self.document.work_area.width:g} × "
                f"{self.document.work_area.height:g} mm)"
            )
        else:
            self.show_notice(
                "Returned the empty project to machine coordinates because no "
                "honeycomb reference is current"
            )
        return True

    def _calibration_project_frame_changed(self) -> None:
        """Reconcile prepared work and a pristine project with a new fixture pose."""

        if not self._reconcile_pristine_project_frame():
            self._invalidate_generated_job()

    def _calibration_review_evidence_invalidated(self) -> None:
        """Remove camera evidence whose coordinates belonged to the old map/pose."""

        self._active_trace_request_id = None
        self._trace_raster_preview_images = {}
        self._trace_raster_preview_area = None
        self._trace_raster_preview_signature = None
        self._trace_result = None
        self._template_match_result = None
        self.workspace.clear_trace_preview()
        self.workspace.clear_template_preview()
        if hasattr(self, "trace_panel"):
            self.trace_panel.clear_result()
        if hasattr(self, "template_panel"):
            self.template_panel.set_busy(False)
            self.template_panel.clear_placement()
            self.template_panel.set_match_message(
                "Camera alignment changed. Run template alignment again."
            )

    def _load_fine_registration_job(self, registration_job: Any) -> None:
        source_job = registration_job.program
        registration_spot_offset_mm = (
            float(self.runtime.settings.laser.spot_offset_x_mm),
            float(self.runtime.settings.laser.spot_offset_y_mm),
        )
        plan = getattr(source_job, "plan", None) or build_job_plan(
            source_job.text,
            power_max=self.runtime.settings.laser.power_max,
            default_feed_mm_min=self.runtime.settings.laser.travel_feed_mm_min,
            start_position=self._planned_job_start_position(),
        )
        if isinstance(source_job, ProjectJob):
            job = source_job
            job.plan = plan
            job.coordinate_space = CoordinateSpace.MACHINE
            job.coordinate_frame_signature = None
            job.execution_signature = None
            job.guarded_output_polygon_mm = getattr(
                registration_job,
                "guarded_output_polygon_mm",
                None,
            )
        else:
            job = ProjectJob(
                text=source_job.text,
                bounds_mm=source_job.bounds_mm,
                cut_length_mm=source_job.cut_length_mm,
                travel_length_mm=source_job.travel_length_mm,
                estimated_seconds=plan.total_seconds,
                path_count=source_job.path_count,
                point_count=source_job.point_count,
                plan=plan,
                spot_offset_mm=registration_spot_offset_mm,
                coordinate_space=CoordinateSpace.MACHINE,
            )
        job.spot_offset_mm = registration_spot_offset_mm
        exact_powered = plan.powered
        mode = (
            f"powered at {registration_job.power_percent:g}%"
            if exact_powered
            else "laser power 0%"
        )
        display_name = str(
            getattr(registration_job, "display_name", "Fine registration")
        )
        self._invalidate_generated_job()
        automatic_captures = {
            "Base bed mapping": (2, "capture_base_bed_mapping"),
            "Fine registration": (3, "capture_fine_registration"),
            "Dense local correction": (3, "capture_dense_calibration"),
            "Accuracy validation": (4, "capture_accuracy_validation"),
            "Dense mesh validation": (4, "capture_dense_validation"),
            "Dense mesh confirmation": (4, "capture_dense_confirmation"),
        }
        capture = automatic_captures.get(display_name)
        self._pending_calibration_capture = (
            {
                "filename": str(registration_job.filename),
                "tab_index": capture[0],
                "capture_action": capture[1],
                "submitted": False,
                "baseline_job": None,
            }
            if exact_powered and capture is not None
            else None
        )
        self._job_request_id += 1
        request_id = self._job_request_id
        self._install_generated_job(
            request_id,
            {
                "job": job,
                "prepared": prepare_job_preview(plan),
                "filename": registration_job.filename,
                "powered": exact_powered,
                "revision": self.document.revision,
                "work_area": self._work_area_signature(
                    self.runtime.settings.machine.work_area
                ),
                "coordinate_frame_signature": None,
                "frame": not exact_powered,
                "summary": (
                    f"{display_name} · {len(registration_job.targets)} crosses · "
                    f"{mode} · bounds X{job.bounds_mm[0]:.2f}..{job.bounds_mm[2]:.2f} "
                    f"Y{job.bounds_mm[1]:.2f}..{job.bounds_mm[3]:.2f}"
                ),
                "notice": (
                    f"{display_name} job loaded. Review the G-code preview and "
                    "run the prepared job when ready."
                ),
            },
        )

    def show_notice(self, message: str) -> None:
        self.statusBar().showMessage(message, 7000)

    def open_live_monitor(self) -> None:
        from .live_monitor import LiveMonitorWindow

        monitor = getattr(self, "_live_monitor_window", None)
        if monitor is None:
            monitor = LiveMonitorWindow(self.runtime.context.camera, self)
            monitor.destroyed.connect(
                lambda: setattr(self, "_live_monitor_window", None)
            )
            self._live_monitor_window = monitor
        monitor.show()
        monitor.raise_()
        monitor.activateWindow()

    def show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 10000)
        QtWidgets.QMessageBox.critical(self, "E3 Positioning System", message)

    def show_camera_error(self, message: str) -> None:
        """Acknowledge one latched camera fault without recurring focus theft."""
        self.statusBar().showMessage(message.split("\n", 1)[0], 15000)
        QtWidgets.QMessageBox.warning(
            self,
            "Camera unavailable",
            message,
            QtWidgets.QMessageBox.StandardButton.Ok,
        )

    def show_camera_mapping_required(self, payload: dict[str, Any]) -> None:
        """Explain a calibration block without misreporting the camera source."""
        camera_online = payload.get("camera_online") is True
        setup_tab = 2 if payload.get("setup_tab") == 2 else 1
        reasons = [str(reason) for reason in payload.get("reasons", []) if reason]
        details = "; ".join(reasons) or "The saved bed map is not current"
        if setup_tab == 1:
            recovery = (
                "Open Machine Setup at Lens, complete an accepted lens solve, then "
                "continue to Bed mapping and use Fresh automatic base mapping."
            )
        else:
            recovery = (
                "Open Machine Setup at Bed mapping and use Fresh automatic base "
                "mapping."
            )
        status = (
            "Camera is online; the corrected overlay needs a new bed map."
            if camera_online
            else "The corrected overlay needs a new bed map."
        )
        self.statusBar().showMessage(status, 15000)
        if not camera_online:
            return
        choice = QtWidgets.QMessageBox.question(
            self,
            "Bed mapping required",
            f"{status}\n\n{recovery} No coordinate entry is required.\n\n"
            f"Details: {details}",
            (
                QtWidgets.QMessageBox.StandardButton.Open
                | QtWidgets.QMessageBox.StandardButton.Cancel
            ),
            QtWidgets.QMessageBox.StandardButton.Open,
        )
        if choice == QtWidgets.QMessageBox.StandardButton.Open:
            QtCore.QTimer.singleShot(
                0,
                lambda setup_tab=setup_tab: self.open_machine_setup(setup_tab),
            )

    def show_camera_overlay_error(self, message: str) -> None:
        """Report corrected-view processing separately from camera ownership."""
        self.statusBar().showMessage(message.split("\n", 1)[0], 15000)
        QtWidgets.QMessageBox.warning(
            self,
            "Corrected overlay unavailable",
            message,
            QtWidgets.QMessageBox.StandardButton.Ok,
        )

    def show_about(self) -> None:
        QtWidgets.QMessageBox.about(
            self,
            "About E3 Positioning System",
            "<h2>E3 Positioning System</h2>"
            f"<p>{self._application_identity}</p>"
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
        filename = self.project_path.name if self.project_path else self.document.name
        self.setWindowTitle(
            application_window_title(filename, dirty=not self.history.is_clean)
        )

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
            width = min(1320, maximum_width)
            height = min(820, maximum_height)
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

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "safety_toolbar"):
            self._update_chrome_toolbar_layout()
        if hasattr(self, "job_progress"):
            self._update_status_bar_layout()

    def _update_status_bar_layout(self) -> None:
        """Preserve job progress and temporary messages before compact details."""

        status_bar = self.statusBar()
        temporary_message = status_bar.currentMessage()
        detail_labels = (
            self.direct_edit_label,
            self.cursor_label,
            self.selection_label,
        )
        progress_width = self.job_progress.minimumWidth()
        progress_bar = self.job_progress.currentWidget()
        if isinstance(progress_bar, QtWidgets.QProgressBar):
            progress_width = max(
                progress_width,
                progress_bar.fontMetrics().horizontalAdvance(
                    progress_bar.format()
                )
                + 8,
            )
        self.job_progress.setMaximumWidth(
            progress_width
            if temporary_message
            else self._job_progress_maximum_width
        )
        message_width = 0
        if temporary_message:
            message_width = (
                status_bar.fontMetrics().horizontalAdvance(temporary_message)
                + _STATUS_MESSAGE_PADDING
            )
        required_width = progress_width + _STATUS_LAYOUT_RESERVE + message_width
        show_runtime = (
            required_width + self.runtime_label.sizeHint().width()
            <= self.width()
        )
        self.runtime_label.setVisible(show_runtime)
        if show_runtime:
            required_width += self.runtime_label.sizeHint().width()

        show_zoom = (
            required_width + self.zoom_label.sizeHint().width() <= self.width()
        )
        self.zoom_label.setVisible(show_zoom)
        if show_zoom:
            required_width += self.zoom_label.sizeHint().width()

        show_details = (
            not temporary_message
            and required_width
            + sum(label.sizeHint().width() for label in detail_labels)
            <= self.width()
        )
        for label in detail_labels:
            label.setVisible(show_details)
        tooltip_lines: list[str] = []
        if (
            temporary_message
            and required_width > self.width()
        ):
            tooltip_lines.append(temporary_message)
        if not show_runtime:
            tooltip_lines.append(f"Runtime: {self.runtime_label.text()}")
        status_bar.setToolTip("\n".join(tooltip_lines))

    def _update_chrome_toolbar_layout(self, *, force: bool = False) -> None:
        """Keep STOP inline on wide screens and guaranteed visible on narrow ones."""

        own_row = self.width() < _PRIMARY_CONTROLS_INLINE_WIDTH
        if not force and own_row == self._safety_on_own_row:
            return
        self.removeToolBarBreak(self.safety_toolbar)
        if own_row:
            self.insertToolBarBreak(self.safety_toolbar)
        self._safety_on_own_row = own_row

    def _reset_window_size(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        settings.remove("mainWindow/geometry-v7")
        settings.remove("mainWindow/geometry-v6")
        settings.remove("mainWindow/geometry-v5")
        settings.remove("mainWindow/geometry-v4")
        settings.remove("mainWindow/geometry-v3")
        self.showNormal()
        self._ensure_window_visible(reset_size=True)

    def _reset_workspace_layout(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        settings.remove("mainWindow/state-v7")
        settings.remove("mainWindow/state-v6")
        settings.remove("mainWindow/state-v5")
        settings.remove("mainWindow/state-v4")
        settings.remove("mainWindow/state-v3")
        settings.remove("mainWindow/inspector-tab-v7")
        settings.remove("mainWindow/inspector-tab-v6")
        settings.remove("mainWindow/inspector-tab-v5")
        settings.remove("mainWindow/inspector-tab-v4")
        settings.remove("mainWindow/inspector-tab-v3")
        settings.remove("mainWindow/job-tab-v6")
        settings.remove("mainWindow/job-tab-v5")
        self.restoreState(self._default_window_state, 7)
        self.inspector_tabs.setCurrentIndex(0)
        self.layer_dock.show()
        self.layer_dock.raise_()
        self.console_dock.hide()
        # The captured default predates responsive toolbar breaks. Recompute
        # the non-hideable primary-control row after restoring those dock bytes
        # so compact Reset Layout cannot clip software STOP.
        self._update_chrome_toolbar_layout(force=True)
        self._update_status_bar_layout()
        self.show_notice("Workspace layout reset")

    def _restore_window_state(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        geometry = settings.value("mainWindow/geometry-v7")
        if geometry is None:
            geometry = settings.value("mainWindow/geometry-v6")
        if geometry is None:
            geometry = settings.value("mainWindow/geometry-v5")
        if geometry is None:
            geometry = settings.value("mainWindow/geometry-v4")
        if geometry is None:
            geometry = settings.value("mainWindow/geometry-v3")
        # Dock topology changed in v7. Deliberately do not restore opaque v6
        # dock bytes, which would recreate the removed bottom panel row.
        state = settings.value("mainWindow/state-v7")
        restored_geometry = bool(geometry and self.restoreGeometry(geometry))
        if state:
            self.restoreState(state, 7)
        # The runtime authority and software-stop control are intentionally not
        # user-hideable, including when an older saved layout says otherwise.
        self.safety_toolbar.show()
        self.safety_toolbar.toggleViewAction().setEnabled(False)
        inspector_index = int(
            settings.value(
                "mainWindow/inspector-tab-v6",
                settings.value("mainWindow/inspector-tab-v5", 0),
            )
        )
        inspector_index = int(
            settings.value("mainWindow/inspector-tab-v7", inspector_index)
        )
        if hasattr(self, "inspector_tabs"):
            self.inspector_tabs.setCurrentIndex(
                max(0, min(inspector_index, self.inspector_tabs.count() - 1))
            )
        self._update_chrome_toolbar_layout(force=True)
        self._ensure_window_visible(reset_size=not restored_geometry)

    def _save_window_state(self) -> None:
        settings = QtCore.QSettings("E3", "E3 Positioning System")
        settings.setValue("mainWindow/geometry-v7", self.saveGeometry())
        settings.setValue("mainWindow/state-v7", self.saveState(7))
        if hasattr(self, "inspector_tabs"):
            settings.setValue(
                "mainWindow/inspector-tab-v7",
                self.inspector_tabs.currentIndex(),
            )

    def _prepare_close_request(
        self,
        *,
        before_shutdown_cleanup: Callable[[], None] | None = None,
    ) -> bool:
        if self._close_requested:
            return True
        if not self._confirm_discard_changes():
            return False
        self._close_requested = True
        shutdown_deadline = time.monotonic() + DESKTOP_SHUTDOWN_TIMEOUT_SECONDS
        # Arm the production process boundary before any service or worker
        # cleanup can block. Unit-created windows have no watchdog connection.
        self.shutdownStarted.emit(shutdown_deadline)
        # The updater uses this acceptance boundary to spawn its already-
        # verified detached installer. It must happen before QSettings, dialogs,
        # worker cancellation, or hardware-service teardown can consume the
        # process deadline. Ordinary Close has no callback here.
        if before_shutdown_cleanup is not None:
            before_shutdown_cleanup()
        self._save_window_state()
        self._cancel_job_preparation("Application is closing")
        self._cancel_job_render()
        self._invalidate_generated_job(cancel_preparation=False)
        machine_setup_dialog = getattr(self, "_machine_setup_dialog", None)
        if machine_setup_dialog is not None:
            try:
                machine_setup_dialog.begin_shutdown()
            except Exception:
                LOGGER.exception("Could not cancel Machine Setup during shutdown")
        self.controller.begin_shutdown(shutdown_deadline)
        return True

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        if not self._prepare_close_request():
            event.ignore()
            return
        self._closing = True
        self.statusBar().showMessage("Closing E3…")
        self.controller.stop(deadline=self.controller.begin_shutdown())
        event.accept()

    def _background_tasks_drained(self) -> None:
        if getattr(self, "_e3_update_idle_handoff", None) is not None:
            return
        if self._close_requested and not self._closing:
            QtCore.QTimer.singleShot(0, self.close)
