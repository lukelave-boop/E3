from __future__ import annotations

from typing import Any

from ..calibration.profiles import signature_from_camera_settings
from ..config import WorkArea
from ..machine.profiles import MachineInstance, MachineRegistryError
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _double_spin(
    minimum: float,
    maximum: float,
    *,
    decimals: int = 3,
    step: float = 1.0,
) -> QtWidgets.QDoubleSpinBox:
    field = QtWidgets.QDoubleSpinBox()
    field.setRange(minimum, maximum)
    field.setDecimals(decimals)
    field.setSingleStep(step)
    field.setKeyboardTracking(False)
    return field


def _profile_capabilities(values: tuple[str, ...]) -> str:
    return ", ".join(value.replace("-", " ") for value in values) or "none listed"


def _machine_profile_text(profile: Any, *, new_machine: bool) -> str:
    defaults = profile.machine_defaults
    area = defaults.work_area
    identity = " / ".join(
        value for value in (profile.manufacturer, profile.model) if value
    ) or profile.name
    action = (
        "These values will be copied as the starting settings when you click Add."
        if new_machine
        else "Changing this profile only changes the identity. It does not change "
        "the current settings below unless you explicitly apply its defaults."
    )
    return (
        f"<b>{identity}</b> — {profile.description}<br>"
        f"<b>Profile defaults:</b> {defaults.protocol.upper()} · "
        f"{area.width:g} × {area.height:g} mm work area · "
        f"{defaults.max_travel_feed_mm_min:g} mm/min travel ceiling · "
        f"{defaults.max_work_feed_mm_min:g} mm/min work ceiling.<br>"
        f"<b>Capabilities:</b> {_profile_capabilities(profile.capabilities)}.<br>"
        f"<b>{action}</b>"
    )


def _tool_head_profile_text(profile: Any, *, new_machine: bool) -> str:
    defaults = profile.laser_defaults
    watts = (
        "optical wattage not specified"
        if profile.nominal_output_watts is None
        else f"{profile.nominal_output_watts:g} W nominal optical output"
    )
    action = (
        "These values will be copied as the starting laser settings when you click Add."
        if new_machine
        else "Changing this profile only changes the identity. It does not change "
        "power, feeds, offsets, framing, or other laser settings unless you "
        "explicitly apply its defaults."
    )
    return (
        f"<b>{profile.name}</b> — {profile.description}<br>"
        f"<b>Known hardware:</b> {watts} · "
        f"capabilities: {_profile_capabilities(profile.capabilities)}.<br>"
        f"<b>Profile defaults:</b> {defaults.power_mode} · S max "
        f"{defaults.power_max} · default power {defaults.default_power} · "
        f"frame power {defaults.frame_power}.<br>"
        f"<b>{action}</b>"
    )


class _NewMachineDialog(QtWidgets.QDialog):
    def __init__(self, registry: Any, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Add machine")
        self.setModal(True)
        self.setMinimumWidth(620)

        outer = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "<b>1. Name the machine &nbsp; 2. Choose the motion platform &nbsp; "
            "3. Choose the laser.</b><br>"
            "For a new machine, these profiles provide the starting values that E3 "
            "copies into the editable settings. You can review every value before "
            "using the machine."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit("New machine")
        self.machine_profile = QtWidgets.QComboBox()
        for profile in registry.machine_profiles():
            self.machine_profile.addItem(profile.name, profile.id)
        self.tool_head_profile = QtWidgets.QComboBox()
        for profile in registry.tool_head_profiles():
            self.tool_head_profile.addItem(profile.name, profile.id)
        form.addRow("Machine name", self.name)
        form.addRow("Motion-platform profile", self.machine_profile)
        outer.addLayout(form)

        self.machine_profile_info = QtWidgets.QLabel()
        self.machine_profile_info.setWordWrap(True)
        self.machine_profile_info.setObjectName("profileExplanation")
        outer.addWidget(self.machine_profile_info)

        tool_form = QtWidgets.QFormLayout()
        tool_form.addRow("Laser / tool-head profile", self.tool_head_profile)
        outer.addLayout(tool_form)
        self.tool_head_profile_info = QtWidgets.QLabel()
        self.tool_head_profile_info.setWordWrap(True)
        self.tool_head_profile_info.setObjectName("profileExplanation")
        outer.addWidget(self.tool_head_profile_info)

        note = QtWidgets.QLabel(
            "<b>What happens next:</b> clicking <b>Add</b> creates a saved machine "
            "using those starting values. After creation, changing a profile name "
            "only changes the machine's identity; it will not silently overwrite "
            "the concrete settings."
        )
        note.setWordWrap(True)
        outer.addWidget(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Add")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self.machine_profile.currentIndexChanged.connect(self._refresh_profile_help)
        self.tool_head_profile.currentIndexChanged.connect(self._refresh_profile_help)
        self._refresh_profile_help()

    def _refresh_profile_help(self) -> None:
        machine = self.registry.get_machine_profile(
            str(self.machine_profile.currentData())
        )
        head = self.registry.get_tool_head_profile(
            str(self.tool_head_profile.currentData())
        )
        self.machine_profile_info.setText(
            _machine_profile_text(machine, new_machine=True)
        )
        self.tool_head_profile_info.setText(
            _tool_head_profile_text(head, new_machine=True)
        )

    def _accept(self) -> None:
        if not self.name.text().strip():
            QtWidgets.QMessageBox.warning(self, "Add machine", "Enter a machine name.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (
            self.name.text().strip(),
            str(self.machine_profile.currentData()),
            str(self.tool_head_profile.currentData()),
        )


class MachineManagerDialog(QtWidgets.QDialog):
    """Edit the saved-machine registry without hot-swapping the running controller."""

    registryChanged = QtCore.Signal()

    def __init__(self, runtime: Any, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.registry = runtime.machine_registry
        self.running_machine_id = str(runtime.running_machine_id)
        self._current_machine_id: str | None = None
        self._working_machine: MachineInstance | None = None
        self._loading = False
        self._optical_profile_key = signature_from_camera_settings(
            runtime.settings.camera
        ).key

        self.setWindowTitle("Machine Manager")
        self.setModal(True)
        self.resize(1040, 720)
        self.setMinimumSize(880, 620)

        outer = QtWidgets.QVBoxLayout(self)

        heading = QtWidgets.QLabel("Saved machines")
        heading.setObjectName("dialogHeading")
        outer.addWidget(heading)

        intro = QtWidgets.QLabel(
            "Your current E3 machine was imported automatically from the existing "
            "controller, laser, camera, and calibration configuration. Selecting a "
            "different machine changes the default for the next E3 launch; the running "
            "controller is never hot-swapped from this dialog."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.machine_list = QtWidgets.QListWidget()
        self.machine_list.setObjectName("machineManagerList")
        self.machine_list.currentItemChanged.connect(self._selection_changed)
        left_layout.addWidget(self.machine_list, 1)

        left_buttons = QtWidgets.QGridLayout()
        self.add_button = QtWidgets.QPushButton("Add…")
        self.duplicate_button = QtWidgets.QPushButton("Duplicate")
        self.delete_button = QtWidgets.QPushButton("Delete")
        left_buttons.addWidget(self.add_button, 0, 0)
        left_buttons.addWidget(self.duplicate_button, 0, 1)
        left_buttons.addWidget(self.delete_button, 1, 0, 1, 2)
        left_layout.addLayout(left_buttons)
        splitter.addWidget(left)

        editor_scroll = QtWidgets.QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        editor = QtWidgets.QWidget()
        self.editor_layout = QtWidgets.QVBoxLayout(editor)
        editor_scroll.setWidget(editor)
        splitter.addWidget(editor_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        left.setMinimumWidth(360)
        splitter.setSizes([380, 660])

        self._build_identity_group()
        self._build_connection_group()
        self._build_geometry_group()
        self._build_laser_group()
        self._build_camera_group()
        self.editor_layout.addStretch(1)

        footer = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.save_button = QtWidgets.QPushButton("Save changes")
        self.use_button = QtWidgets.QPushButton("Use on next launch")
        self.close_button = QtWidgets.QPushButton("Close")
        footer.addWidget(self.save_button)
        footer.addWidget(self.use_button)
        footer.addWidget(self.close_button)
        outer.addLayout(footer)

        self.add_button.clicked.connect(self._add_machine)
        self.duplicate_button.clicked.connect(self._duplicate_machine)
        self.delete_button.clicked.connect(self._delete_machine)
        self.save_button.clicked.connect(self._save_selected)
        self.use_button.clicked.connect(self._set_active_selected)
        self.close_button.clicked.connect(self.accept)

        self._ensure_current_machine_binding()
        self._reload_list(self.running_machine_id)

    def _build_identity_group(self) -> None:
        group = QtWidgets.QGroupBox("Machine identity and profile defaults")
        layout = QtWidgets.QVBoxLayout(group)

        authority = QtWidgets.QLabel(
            "<b>Important:</b> the profile names describe what hardware this is. "
            "<b>The concrete settings shown below are what E3 actually uses.</b> "
            "Choosing another profile never silently changes those settings."
        )
        authority.setWordWrap(True)
        authority.setObjectName("profileAuthorityExplanation")
        layout.addWidget(authority)

        form = QtWidgets.QFormLayout()
        self.name = QtWidgets.QLineEdit()
        self.machine_profile = QtWidgets.QComboBox()
        for profile in self.registry.machine_profiles():
            self.machine_profile.addItem(profile.name, profile.id)
        self.tool_head_profile = QtWidgets.QComboBox()
        for profile in self.registry.tool_head_profiles():
            self.tool_head_profile.addItem(profile.name, profile.id)
        self.origin = QtWidgets.QLabel("")
        self.origin.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow("Machine name", self.name)
        form.addRow("Motion-platform identity", self.machine_profile)
        layout.addLayout(form)

        self.machine_profile_info = QtWidgets.QLabel()
        self.machine_profile_info.setWordWrap(True)
        self.machine_profile_info.setObjectName("profileExplanation")
        layout.addWidget(self.machine_profile_info)
        self.apply_machine_defaults_button = QtWidgets.QPushButton(
            "Apply this machine profile's defaults…"
        )
        self.apply_machine_defaults_button.setToolTip(
            "Explicitly replace the editable controller, work-area, homing, and "
            "motion defaults with values from the selected profile. Nothing is "
            "saved until Save changes is clicked."
        )
        layout.addWidget(self.apply_machine_defaults_button)

        tool_form = QtWidgets.QFormLayout()
        tool_form.addRow("Laser / tool-head identity", self.tool_head_profile)
        layout.addLayout(tool_form)
        self.tool_head_profile_info = QtWidgets.QLabel()
        self.tool_head_profile_info.setWordWrap(True)
        self.tool_head_profile_info.setObjectName("profileExplanation")
        layout.addWidget(self.tool_head_profile_info)
        self.apply_tool_defaults_button = QtWidgets.QPushButton(
            "Apply this laser profile's defaults…"
        )
        self.apply_tool_defaults_button.setToolTip(
            "Explicitly replace the editable laser settings with values from the "
            "selected profile. Nothing is saved until Save changes is clicked."
        )
        layout.addWidget(self.apply_tool_defaults_button)

        origin_form = QtWidgets.QFormLayout()
        origin_form.addRow("Created from", self.origin)
        layout.addLayout(origin_form)
        self.editor_layout.addWidget(group)

        self.machine_profile.currentIndexChanged.connect(self._refresh_profile_help)
        self.tool_head_profile.currentIndexChanged.connect(self._refresh_profile_help)
        self.apply_machine_defaults_button.clicked.connect(
            self._apply_machine_profile_defaults
        )
        self.apply_tool_defaults_button.clicked.connect(
            self._apply_tool_head_profile_defaults
        )

    def _build_connection_group(self) -> None:
        group = QtWidgets.QGroupBox("Controller connection")
        form = QtWidgets.QFormLayout(group)
        self.protocol = QtWidgets.QComboBox()
        for value in ("grbl", "marlin", "auto"):
            self.protocol.addItem(value.upper(), value)
        self.port = QtWidgets.QLineEdit()
        self.port.setPlaceholderText("e3bridge://host:8765, COM4, or serial device")
        self.baudrate = QtWidgets.QSpinBox()
        self.baudrate.setRange(1, 4_000_000)
        self.baudrate.setKeyboardTracking(False)
        self.read_timeout = _double_spin(0.01, 120.0, decimals=2, step=0.25)
        self.startup_delay = _double_spin(0.0, 120.0, decimals=2, step=0.25)
        self.grbl_idle_delay = QtWidgets.QSpinBox()
        self.grbl_idle_delay.setRange(0, 65_535)
        self.grbl_idle_delay.setSuffix(" ms")
        form.addRow("Protocol", self.protocol)
        form.addRow("Controller endpoint", self.port)
        form.addRow("Baud rate", self.baudrate)
        form.addRow("Read timeout", self.read_timeout)
        form.addRow("Startup delay", self.startup_delay)
        form.addRow("GRBL step-idle delay", self.grbl_idle_delay)
        self.editor_layout.addWidget(group)

    def _build_geometry_group(self) -> None:
        group = QtWidgets.QGroupBox("Work area and motion")
        grid = QtWidgets.QGridLayout(group)

        self.x_min = _double_spin(-100_000.0, 100_000.0)
        self.x_max = _double_spin(-100_000.0, 100_000.0)
        self.y_min = _double_spin(-100_000.0, 100_000.0)
        self.y_max = _double_spin(-100_000.0, 100_000.0)
        self.photo_x = _double_spin(-100_000.0, 100_000.0)
        self.photo_y = _double_spin(-100_000.0, 100_000.0)
        self.photo_z = QtWidgets.QLineEdit()
        self.photo_z.setPlaceholderText("blank = no Z move")
        self.max_travel_feed = _double_spin(0.01, 1_000_000.0, decimals=1, step=100.0)
        self.max_work_feed = _double_spin(0.01, 1_000_000.0, decimals=1, step=100.0)
        self.home_before_photo = QtWidgets.QCheckBox("Home before photo position")
        self.release_after_job = QtWidgets.QCheckBox(
            "Home and release motors after powered job"
        )

        rows = (
            ("X minimum", self.x_min, "X maximum", self.x_max),
            ("Y minimum", self.y_min, "Y maximum", self.y_max),
            ("Photo X", self.photo_x, "Photo Y", self.photo_y),
            ("Photo Z", self.photo_z, "Max travel feed", self.max_travel_feed),
            ("Max work feed", self.max_work_feed, "", QtWidgets.QLabel("")),
        )
        for row, (left_label, left, right_label, right) in enumerate(rows):
            grid.addWidget(QtWidgets.QLabel(left_label), row, 0)
            grid.addWidget(left, row, 1)
            if right_label:
                grid.addWidget(QtWidgets.QLabel(right_label), row, 2)
                grid.addWidget(right, row, 3)
        grid.addWidget(self.home_before_photo, len(rows), 0, 1, 2)
        grid.addWidget(self.release_after_job, len(rows), 2, 1, 2)
        self.editor_layout.addWidget(group)

    def _build_laser_group(self) -> None:
        group = QtWidgets.QGroupBox("Laser / tool-head settings")
        grid = QtWidgets.QGridLayout(group)

        self.power_mode = QtWidgets.QComboBox()
        self.power_mode.addItem("M4 dynamic power", "M4")
        self.power_mode.addItem("M3 constant power", "M3")
        self.power_max = QtWidgets.QSpinBox()
        self.power_max.setRange(1, 1_000_000)
        self.default_power = QtWidgets.QSpinBox()
        self.default_power.setRange(0, 1_000_000)
        self.frame_power = QtWidgets.QSpinBox()
        self.frame_power.setRange(0, 1_000_000)
        self.travel_feed = _double_spin(0.01, 1_000_000.0, decimals=1, step=100.0)
        self.engrave_feed = _double_spin(0.01, 1_000_000.0, decimals=1, step=100.0)
        self.curve_tolerance = _double_spin(0.0001, 1000.0, decimals=4, step=0.01)
        self.boundary_margin = _double_spin(0.0, 100_000.0, decimals=3, step=0.25)
        self.spot_offset_x = _double_spin(-100_000.0, 100_000.0)
        self.spot_offset_y = _double_spin(-100_000.0, 100_000.0)
        self.arm_timeout = QtWidgets.QSpinBox()
        self.arm_timeout.setRange(1, 600)
        self.arm_timeout.setSuffix(" s")
        self.allow_low_power_frame = QtWidgets.QCheckBox("Allow low-power framing")
        self.return_to_photo = QtWidgets.QCheckBox("Return to photo position after job")
        self.polygon_summary = QtWidgets.QLabel("")
        self.polygon_summary.setWordWrap(True)

        rows = (
            ("Power mode", self.power_mode, "Controller power max", self.power_max),
            ("Default power", self.default_power, "Frame power", self.frame_power),
            ("Travel feed", self.travel_feed, "Engrave feed", self.engrave_feed),
            ("Curve tolerance", self.curve_tolerance, "Boundary margin", self.boundary_margin),
            ("Spot offset X", self.spot_offset_x, "Spot offset Y", self.spot_offset_y),
            ("Arm timeout", self.arm_timeout, "", QtWidgets.QLabel("")),
        )
        for row, (left_label, left, right_label, right) in enumerate(rows):
            grid.addWidget(QtWidgets.QLabel(left_label), row, 0)
            grid.addWidget(left, row, 1)
            if right_label:
                grid.addWidget(QtWidgets.QLabel(right_label), row, 2)
                grid.addWidget(right, row, 3)
        grid.addWidget(self.allow_low_power_frame, len(rows), 0, 1, 2)
        grid.addWidget(self.return_to_photo, len(rows), 2, 1, 2)
        grid.addWidget(QtWidgets.QLabel("Guarded output polygon"), len(rows) + 1, 0)
        grid.addWidget(self.polygon_summary, len(rows) + 1, 1, 1, 3)
        self.editor_layout.addWidget(group)

    def _build_camera_group(self) -> None:
        group = QtWidgets.QGroupBox("Camera and calibration preloaded with this E3 setup")
        form = QtWidgets.QFormLayout(group)
        camera = self.runtime.settings.camera
        controls = camera.controls
        autofocus_raw = controls.get(
            "focus_automatic_continuous",
            controls.get("focus_auto", 0),
        )
        autofocus = autofocus_raw is True or autofocus_raw == 1
        focus = controls.get("focus_absolute", 0)
        self.camera_endpoint = QtWidgets.QLabel(str(camera.device))
        self.camera_endpoint.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.camera_optics = QtWidgets.QLabel(
            f"{camera.width} × {camera.height} @ {camera.fps} FPS, "
            f"{camera.fourcc}, "
            + ("autofocus" if autofocus else f"manual focus {focus}")
        )
        self.camera_optics.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.calibration_binding = QtWidgets.QLabel(self._optical_profile_key)
        self.calibration_binding.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        note = QtWidgets.QLabel(
            "The current network camera settings and calibration stack remain "
            "preloaded. New and duplicated machines inherit this binding unless a "
            "future camera-specific profile is assigned."
        )
        note.setWordWrap(True)
        form.addRow("Camera endpoint", self.camera_endpoint)
        form.addRow("Optical profile", self.camera_optics)
        form.addRow("Calibration profile", self.calibration_binding)
        form.addRow(note)
        self.editor_layout.addWidget(group)

    def _ensure_current_machine_binding(self) -> None:
        try:
            machine = self.registry.get_machine(self.running_machine_id)
        except MachineRegistryError:
            return
        changed = False
        if machine.camera_profile_id is None:
            machine.camera_profile_id = self._optical_profile_key
            changed = True
        if machine.calibration_profile_id is None:
            machine.calibration_profile_id = self._optical_profile_key
            changed = True
        if (
            machine.id == "existing-machine"
            and machine.name == "Imported existing machine"
        ):
            machine.name = "Current configured machine"
            changed = True
        if changed:
            self.registry.update_machine(machine)

    def _reload_list(self, selected_id: str | None = None) -> None:
        next_launch_id = self.registry.active_machine_id
        selected_id = selected_id or self._current_machine_id or next_launch_id
        self.machine_list.blockSignals(True)
        self.machine_list.clear()
        selected_row = 0
        for row, machine in enumerate(self.registry.machines()):
            badges: list[str] = []
            if machine.id == self.running_machine_id:
                badges.append("RUNNING")
            if machine.id == next_launch_id:
                badges.append("NEXT LAUNCH")
            badge = f"  [{' · '.join(badges)}]" if badges else ""
            item = QtWidgets.QListWidgetItem(machine.name + badge)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, machine.id)
            if machine.id == next_launch_id:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.machine_list.addItem(item)
            if machine.id == selected_id:
                selected_row = row
        self.machine_list.blockSignals(False)
        if self.machine_list.count():
            self.machine_list.setCurrentRow(selected_row)
            self._load_selected()
        self.registryChanged.emit()

    def _selection_changed(
        self,
        current: QtWidgets.QListWidgetItem | None,
        _previous: QtWidgets.QListWidgetItem | None,
    ) -> None:
        del _previous
        if current is None:
            return
        self._load_selected()

    def _selected_id(self) -> str | None:
        item = self.machine_list.currentItem()
        if item is None:
            return None
        value = item.data(QtCore.Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    @staticmethod
    def _set_combo_data(combo: QtWidgets.QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _load_selected(self) -> None:
        machine_id = self._selected_id()
        if machine_id is None:
            return
        machine = self.registry.get_machine(machine_id)
        self._current_machine_id = machine.id
        self._loading = True
        try:
            self._working_machine = machine
            self.name.setText(machine.name)
            self._set_combo_data(self.machine_profile, machine.machine_profile_id)
            self._set_combo_data(self.tool_head_profile, machine.tool_head_profile_id)
            self.origin.setText(machine.created_from)
            self._refresh_profile_help()
            self._set_combo_data(self.protocol, machine.machine.protocol)
            self.port.setText(machine.machine.port)
            self.baudrate.setValue(machine.machine.baudrate)
            self.read_timeout.setValue(machine.machine.read_timeout)
            self.startup_delay.setValue(machine.machine.controller_startup_delay)
            self.grbl_idle_delay.setValue(machine.machine.grbl_step_idle_delay_ms)
            area = machine.machine.work_area
            self.x_min.setValue(area.x_min)
            self.x_max.setValue(area.x_max)
            self.y_min.setValue(area.y_min)
            self.y_max.setValue(area.y_max)
            self.photo_x.setValue(machine.machine.photo_x)
            self.photo_y.setValue(machine.machine.photo_y)
            self.photo_z.setText(
                "" if machine.machine.photo_z is None else f"{machine.machine.photo_z:g}"
            )
            self.max_travel_feed.setValue(machine.machine.max_travel_feed_mm_min)
            self.max_work_feed.setValue(machine.machine.max_work_feed_mm_min)
            self.home_before_photo.setChecked(machine.machine.home_before_photo)
            self.release_after_job.setChecked(
                machine.machine.home_and_release_after_powered_job
            )
            self._set_combo_data(self.power_mode, machine.laser.power_mode)
            self.power_max.setValue(machine.laser.power_max)
            self.default_power.setValue(machine.laser.default_power)
            self.frame_power.setValue(machine.laser.frame_power)
            self.travel_feed.setValue(machine.laser.travel_feed_mm_min)
            self.engrave_feed.setValue(machine.laser.engrave_feed_mm_min)
            self.curve_tolerance.setValue(machine.laser.curve_tolerance_mm)
            self.boundary_margin.setValue(machine.laser.boundary_margin_mm)
            self.spot_offset_x.setValue(machine.laser.spot_offset_x_mm)
            self.spot_offset_y.setValue(machine.laser.spot_offset_y_mm)
            self.arm_timeout.setValue(machine.laser.arm_timeout_seconds)
            self.allow_low_power_frame.setChecked(
                machine.laser.allow_low_power_frame
            )
            self.return_to_photo.setChecked(machine.laser.return_to_photo_position)
            polygon = machine.laser.guarded_output_polygon_mm
            self.polygon_summary.setText(
                "Not configured"
                if polygon is None
                else f"{len(polygon)} points preserved from this machine profile"
            )
            self.delete_button.setEnabled(len(self.registry.machines()) > 1)
            self.use_button.setEnabled(
                machine.id != self.registry.active_machine_id
            )
            if machine.id == self.running_machine_id:
                self.status_label.setText(
                    "This is the machine currently running in E3."
                )
            elif machine.id == self.registry.active_machine_id:
                self.status_label.setText(
                    "This machine is selected for the next E3 launch."
                )
            else:
                self.status_label.setText("")
        finally:
            self._loading = False

    def _refresh_profile_help(self) -> None:
        machine_profile_id = self.machine_profile.currentData()
        tool_head_profile_id = self.tool_head_profile.currentData()
        if machine_profile_id is not None:
            profile = self.registry.get_machine_profile(str(machine_profile_id))
            self.machine_profile_info.setText(
                _machine_profile_text(profile, new_machine=False)
            )
        if tool_head_profile_id is not None:
            profile = self.registry.get_tool_head_profile(str(tool_head_profile_id))
            self.tool_head_profile_info.setText(
                _tool_head_profile_text(profile, new_machine=False)
            )

    def _apply_machine_profile_defaults(self) -> None:
        profile = self.registry.get_machine_profile(
            str(self.machine_profile.currentData())
        )
        answer = QtWidgets.QMessageBox.question(
            self,
            "Apply machine profile defaults",
            "Replace the editable controller, work-area, photo-position, homing, "
            "and motion settings with the defaults from "
            f"'{profile.name}'?\n\n"
            "This is explicit and reversible until you click Save changes. "
            "Camera/calibration and laser settings are not changed.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if self._working_machine is None:
            return
        self._working_machine.machine = profile.machine_defaults
        defaults = self._working_machine.machine
        self._set_combo_data(self.protocol, defaults.protocol)
        self.port.setText(defaults.port)
        self.baudrate.setValue(defaults.baudrate)
        self.read_timeout.setValue(defaults.read_timeout)
        self.startup_delay.setValue(defaults.controller_startup_delay)
        self.grbl_idle_delay.setValue(defaults.grbl_step_idle_delay_ms)
        area = defaults.work_area
        self.x_min.setValue(area.x_min)
        self.x_max.setValue(area.x_max)
        self.y_min.setValue(area.y_min)
        self.y_max.setValue(area.y_max)
        self.photo_x.setValue(defaults.photo_x)
        self.photo_y.setValue(defaults.photo_y)
        self.photo_z.setText("" if defaults.photo_z is None else f"{defaults.photo_z:g}")
        self.max_travel_feed.setValue(defaults.max_travel_feed_mm_min)
        self.max_work_feed.setValue(defaults.max_work_feed_mm_min)
        self.home_before_photo.setChecked(defaults.home_before_photo)
        self.release_after_job.setChecked(
            defaults.home_and_release_after_powered_job
        )
        self.status_label.setText(
            f"Loaded {profile.name} machine defaults into the form. "
            "Review them, then click Save changes if they are correct."
        )

    def _apply_tool_head_profile_defaults(self) -> None:
        profile = self.registry.get_tool_head_profile(
            str(self.tool_head_profile.currentData())
        )
        answer = QtWidgets.QMessageBox.question(
            self,
            "Apply laser profile defaults",
            "Replace the editable laser settings with the defaults from "
            f"'{profile.name}'?\n\n"
            "This is explicit and reversible until you click Save changes. "
            "Controller, camera, and calibration settings are not changed.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if self._working_machine is None:
            return
        self._working_machine.laser = profile.laser_defaults
        defaults = self._working_machine.laser
        self._set_combo_data(self.power_mode, defaults.power_mode)
        self.power_max.setValue(defaults.power_max)
        self.default_power.setValue(defaults.default_power)
        self.frame_power.setValue(defaults.frame_power)
        self.travel_feed.setValue(defaults.travel_feed_mm_min)
        self.engrave_feed.setValue(defaults.engrave_feed_mm_min)
        self.curve_tolerance.setValue(defaults.curve_tolerance_mm)
        self.boundary_margin.setValue(defaults.boundary_margin_mm)
        self.spot_offset_x.setValue(defaults.spot_offset_x_mm)
        self.spot_offset_y.setValue(defaults.spot_offset_y_mm)
        self.arm_timeout.setValue(defaults.arm_timeout_seconds)
        self.allow_low_power_frame.setChecked(defaults.allow_low_power_frame)
        self.return_to_photo.setChecked(defaults.return_to_photo_position)
        self.polygon_summary.setText("Not configured")
        self.status_label.setText(
            f"Loaded {profile.name} laser defaults into the form. "
            "Review them, then click Save changes if they are correct."
        )

    def _candidate_from_form(self) -> MachineInstance:
        machine_id = self._selected_id()
        if machine_id is None:
            raise MachineRegistryError("Select a saved machine")
        candidate = self._working_machine
        if candidate is None or candidate.id != machine_id:
            candidate = self.registry.get_machine(machine_id)
        candidate.name = self.name.text().strip()
        candidate.machine_profile_id = str(self.machine_profile.currentData())
        candidate.tool_head_profile_id = str(self.tool_head_profile.currentData())
        candidate.machine.protocol = str(self.protocol.currentData())
        candidate.machine.port = self.port.text().strip()
        candidate.machine.baudrate = self.baudrate.value()
        candidate.machine.read_timeout = self.read_timeout.value()
        candidate.machine.controller_startup_delay = self.startup_delay.value()
        candidate.machine.grbl_step_idle_delay_ms = self.grbl_idle_delay.value()
        candidate.machine.work_area = WorkArea(
            self.x_min.value(),
            self.x_max.value(),
            self.y_min.value(),
            self.y_max.value(),
        )
        candidate.machine.photo_x = self.photo_x.value()
        candidate.machine.photo_y = self.photo_y.value()
        photo_z = self.photo_z.text().strip()
        candidate.machine.photo_z = None if not photo_z else float(photo_z)
        candidate.machine.max_travel_feed_mm_min = self.max_travel_feed.value()
        candidate.machine.max_work_feed_mm_min = self.max_work_feed.value()
        candidate.machine.home_before_photo = self.home_before_photo.isChecked()
        candidate.machine.home_and_release_after_powered_job = (
            self.release_after_job.isChecked()
        )
        # E3 has one normal operating mode. Saved physical machines are not
        # converted into a second motion-disabled launch state.
        candidate.machine.allow_motion = True

        candidate.laser.power_mode = str(self.power_mode.currentData())
        candidate.laser.power_max = self.power_max.value()
        candidate.laser.default_power = self.default_power.value()
        candidate.laser.frame_power = self.frame_power.value()
        candidate.laser.travel_feed_mm_min = self.travel_feed.value()
        candidate.laser.engrave_feed_mm_min = self.engrave_feed.value()
        candidate.laser.curve_tolerance_mm = self.curve_tolerance.value()
        candidate.laser.boundary_margin_mm = self.boundary_margin.value()
        candidate.laser.spot_offset_x_mm = self.spot_offset_x.value()
        candidate.laser.spot_offset_y_mm = self.spot_offset_y.value()
        candidate.laser.arm_timeout_seconds = self.arm_timeout.value()
        candidate.laser.allow_low_power_frame = (
            self.allow_low_power_frame.isChecked()
        )
        candidate.laser.return_to_photo_position = self.return_to_photo.isChecked()
        candidate.camera_profile_id = (
            candidate.camera_profile_id or self._optical_profile_key
        )
        candidate.calibration_profile_id = (
            candidate.calibration_profile_id or self._optical_profile_key
        )
        return candidate

    def _save_selected(self) -> bool:
        try:
            candidate = self._candidate_from_form()
            saved = self.registry.update_machine(candidate)
        except (MachineRegistryError, ValueError) as exc:
            QtWidgets.QMessageBox.warning(self, "Machine Manager", str(exc))
            return False
        self.status_label.setText(f"Saved {saved.name}.")
        self._reload_list(saved.id)
        return True

    def _add_machine(self) -> None:
        dialog = _NewMachineDialog(self.registry, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        name, machine_profile_id, tool_head_profile_id = dialog.values()
        try:
            created = self.registry.create_machine(
                name,
                machine_profile_id,
                tool_head_profile_id,
            )
            created.machine.allow_motion = True
            created.camera_profile_id = self._optical_profile_key
            created.calibration_profile_id = self._optical_profile_key
            created = self.registry.update_machine(created)
        except MachineRegistryError as exc:
            QtWidgets.QMessageBox.warning(self, "Add machine", str(exc))
            return
        self._reload_list(created.id)

    def _duplicate_machine(self) -> None:
        machine_id = self._selected_id()
        if machine_id is None:
            return
        source = self.registry.get_machine(machine_id)
        try:
            duplicated = self.registry.duplicate_machine(
                machine_id,
                name=f"{source.name} copy",
            )
        except MachineRegistryError as exc:
            QtWidgets.QMessageBox.warning(self, "Duplicate machine", str(exc))
            return
        self._reload_list(duplicated.id)

    def _delete_machine(self) -> None:
        machine_id = self._selected_id()
        if machine_id is None:
            return
        machine = self.registry.get_machine(machine_id)
        answer = QtWidgets.QMessageBox.question(
            self,
            "Delete machine",
            f"Delete saved machine '{machine.name}'?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.registry.remove_machine(machine_id)
        except MachineRegistryError as exc:
            QtWidgets.QMessageBox.warning(self, "Delete machine", str(exc))
            return
        self._reload_list(self.registry.active_machine_id)

    def _set_active_selected(self) -> None:
        if not self._save_selected():
            return
        machine_id = self._selected_id()
        if machine_id is None:
            return
        try:
            self.registry.set_active(machine_id)
        except MachineRegistryError as exc:
            QtWidgets.QMessageBox.warning(self, "Machine Manager", str(exc))
            return
        machine = self.registry.get_machine(machine_id)
        self.status_label.setText(
            f"{machine.name} will be used the next time E3 starts. "
            "The current controller connection was not changed."
        )
        self._reload_list(machine_id)
