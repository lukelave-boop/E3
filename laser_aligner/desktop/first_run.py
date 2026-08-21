from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

from ..deployment import application_root, user_config_path
from ..first_run import (
    SimulatorRecoveryPlan,
    save_profile_setup,
    save_simulator_recovery_selection,
)
from ..machine.profiles import (
    builtin_machine_profiles,
    builtin_tool_head_profiles,
)
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()

_WELCOME = 0
_PROFILE = 1
_CONNECTION = 2
_MACHINE = 3
_CAMERA = 4
_FINISH = 5
_RECOVERY_CHOICE = 6
_CREATE_NEW_MACHINE = "__create_new_real_machine__"


@dataclass(frozen=True, slots=True)
class FirstRunResult:
    config_path: Path
    open_machine_setup: bool


def _default_template_config() -> Path:
    packaged = application_root() / "config" / "default.json"
    if packaged.is_file():
        return packaged
    source = Path(__file__).resolve().parents[2] / "config" / "default.json"
    if source.is_file():
        return source
    raise FileNotFoundError("E3 default configuration is missing")


class _WelcomePage(QtWidgets.QWizardPage):
    def __init__(self, *, recovering_simulator: bool = False) -> None:
        super().__init__()
        self._recovering_simulator = recovering_simulator
        self.setTitle(
            "Replace the removed simulator"
            if recovering_simulator
            else "Welcome to E3"
        )
        self.setSubTitle(
            "Explicitly select or configure a real saved machine."
            if recovering_simulator
            else "Configure a real saved machine."
        )
        layout = QtWidgets.QVBoxLayout(self)
        prefix = (
            (
                "E3 no longer provides a simulator runtime. Recovery requires "
                "an explicit real-machine choice; it never converts a simulator "
                "or transfers simulator camera or calibration evidence. "
            )
            if recovering_simulator
            else (
                "This guided setup creates machine-specific files that E3 keeps "
                "separate from the application. "
            )
        )
        intro = QtWidgets.QLabel(
            prefix
            + "Choosing or saving a profile does not connect, Home, jog, arm, "
            "move, or enable laser output. Hardware settings remain unverified "
            "until they are tested physically."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addStretch(1)

    def nextId(self) -> int:
        return _RECOVERY_CHOICE if self._recovering_simulator else _PROFILE


class _RecoveryChoicePage(QtWidgets.QWizardPage):
    def __init__(self, recovery: SimulatorRecoveryPlan) -> None:
        super().__init__()
        self.recovery = recovery
        self.setTitle("Real saved machine")
        self.setSubTitle(
            "Choose an existing configured physical machine or configure a new one."
        )
        layout = QtWidgets.QFormLayout(self)
        self.choice = QtWidgets.QComboBox()
        self.choice.setObjectName("simulatorRecoveryMachineChoice")
        self.choice.addItem("Choose a recovery action…", None)
        self.choice.addItem(
            "Configure a new real machine",
            _CREATE_NEW_MACHINE,
        )
        for machine in recovery.configured_physical_machines:
            self.choice.addItem(
                f"Select existing: {machine.name} — {machine.machine.port}",
                machine.id,
            )
        layout.addRow("Recovery action", self.choice)
        note = QtWidgets.QLabel(
            "No existing physical machine is selected automatically. Simulator "
            "records are retired only after Finish succeeds; cancel leaves all "
            "configuration and saved machines unchanged."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addRow(note)
        self.choice.currentIndexChanged.connect(self.completeChanged)

    @property
    def selected_machine_id(self) -> str | None:
        selected = self.choice.currentData()
        if selected in {None, _CREATE_NEW_MACHINE}:
            return None
        return str(selected)

    @property
    def creating_new_machine(self) -> bool:
        return self.choice.currentData() == _CREATE_NEW_MACHINE

    def isComplete(self) -> bool:
        return self.choice.currentData() is not None

    def nextId(self) -> int:
        return _PROFILE if self.creating_new_machine else _FINISH


class _ProfilePage(QtWidgets.QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Saved machine profile")
        self.setSubTitle(
            "Create one concrete saved machine from reusable machine and tool-head profiles."
        )
        self._machine_profiles = builtin_machine_profiles()
        self._tool_head_profiles = builtin_tool_head_profiles()
        self._hardware_tool_head_id = "custom-laser-head"

        layout = QtWidgets.QFormLayout(self)
        self.machine_name = QtWidgets.QLineEdit("My E3 machine")
        self.machine_profile = QtWidgets.QComboBox()
        ordered_machine_ids = tuple(sorted(self._machine_profiles))
        for profile_id in ordered_machine_ids:
            profile = self._machine_profiles[profile_id]
            self.machine_profile.addItem(profile.name, profile.id)
        self.tool_head_profile = QtWidgets.QComboBox()
        self.profile_summary = QtWidgets.QLabel()
        self.profile_summary.setWordWrap(True)
        self.profile_summary.setObjectName("firstRunProfileSummary")
        safety = QtWidgets.QLabel(
            "New saved machines start with motion disabled, 0 default/frame "
            "power, low-power framing disabled, and no inherited camera or "
            "calibration binding."
        )
        safety.setWordWrap(True)
        safety.setObjectName("mutedLabel")
        layout.addRow("Saved machine name", self.machine_name)
        layout.addRow("Machine profile", self.machine_profile)
        layout.addRow("Laser / tool-head profile", self.tool_head_profile)
        layout.addRow(self.profile_summary)
        layout.addRow(safety)

        self.machine_name.textChanged.connect(self.completeChanged)
        self.machine_profile.currentIndexChanged.connect(
            self._machine_profile_changed
        )
        self.tool_head_profile.currentIndexChanged.connect(
            self._tool_head_changed
        )
        self._machine_profile_changed()

    def isComplete(self) -> bool:
        return bool(self.machine_name.text().strip())

    def validatePage(self) -> bool:
        if not self.machine_name.text().strip():
            QtWidgets.QMessageBox.warning(
                self,
                "E3 Setup",
                "Enter a name for the saved machine.",
            )
            return False
        return True

    def nextId(self) -> int:
        return _CONNECTION

    def _machine_profile_changed(self) -> None:
        selected_tool = self.tool_head_profile.currentData()
        if selected_tool:
            self._hardware_tool_head_id = str(selected_tool)
        self.tool_head_profile.blockSignals(True)
        self.tool_head_profile.clear()
        for profile_id in ("custom-laser-head", "generic-diode-10w"):
            profile = self._tool_head_profiles[profile_id]
            self.tool_head_profile.addItem(profile.name, profile.id)
        index = self.tool_head_profile.findData(self._hardware_tool_head_id)
        self.tool_head_profile.setCurrentIndex(max(index, 0))
        self.tool_head_profile.setEnabled(True)
        self.tool_head_profile.blockSignals(False)
        self._refresh_summary()
        self.completeChanged.emit()

    def _tool_head_changed(self) -> None:
        selected = self.tool_head_profile.currentData()
        if selected:
            self._hardware_tool_head_id = str(selected)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        machine_id = self.machine_profile.currentData()
        tool_id = self.tool_head_profile.currentData()
        if machine_id is None or tool_id is None:
            self.profile_summary.clear()
            return
        machine = self._machine_profiles[str(machine_id)]
        tool = self._tool_head_profiles[str(tool_id)]
        area = machine.machine_defaults.work_area
        mode = (
            f"{machine.machine_defaults.protocol.upper()} controller "
            "policy over the existing E3 bridge transport."
        )
        self.profile_summary.setText(
            f"<b>{machine.name}</b> + <b>{tool.name}</b><br>"
            f"{mode}<br>Starting work area: {area.width:g} × "
            f"{area.height:g} mm. Review hardware values on the following pages."
        )


class _ConnectionPage(QtWidgets.QWizardPage):
    def __init__(self, *, allow_reachability_test: bool = True) -> None:
        super().__init__()
        self._allow_reachability_test = allow_reachability_test
        self.setTitle("Hardware-node connection")
        self.setSubTitle(
            "Enter the Raspberry Pi address and bridge credential configured on the Pi."
        )
        layout = QtWidgets.QFormLayout(self)
        self.host = QtWidgets.QLineEdit()
        self.host.setPlaceholderText("Example: 192.168.1.50 or e3.local")
        self.controller_port = QtWidgets.QSpinBox()
        self.controller_port.setRange(1, 65535)
        self.controller_port.setValue(8765)
        self.camera_port = QtWidgets.QSpinBox()
        self.camera_port.setRange(1, 65535)
        self.camera_port.setValue(8766)
        self.token = QtWidgets.QLineEdit()
        self.token.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Bridge credential (24+ characters)")
        layout.addRow("Raspberry Pi address", self.host)
        layout.addRow("Controller bridge port", self.controller_port)
        layout.addRow("Camera bridge port", self.camera_port)
        layout.addRow("Bridge credential", self.token)
        row = QtWidgets.QHBoxLayout()
        self.test_button = QtWidgets.QPushButton("Test network reachability")
        self.test_button.setVisible(allow_reachability_test)
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        row.addWidget(self.test_button)
        row.addWidget(self.status, 1)
        layout.addRow(row)
        note = QtWidgets.QLabel(
            "The optional test checks only that both bridge ports are reachable. "
            "It does not connect a controller, send commands, or physically "
            "verify the machine, motion, camera, or laser."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addRow(note)
        self.test_button.clicked.connect(self._test_connection)
        self.host.textChanged.connect(self.completeChanged)
        self.token.textChanged.connect(self.completeChanged)

    def isComplete(self) -> bool:
        return bool(self.host.text().strip()) and len(
            self.token.text().strip()
        ) >= 24

    def validatePage(self) -> bool:
        if not self.host.text().strip():
            QtWidgets.QMessageBox.warning(
                self,
                "E3 Setup",
                "Enter the Raspberry Pi address.",
            )
            return False
        if len(self.token.text().strip()) < 24:
            QtWidgets.QMessageBox.warning(
                self,
                "E3 Setup",
                "The bridge credential must contain at least 24 characters.",
            )
            return False
        return True

    @staticmethod
    def _reachable(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    def _test_connection(self) -> None:
        if not self._allow_reachability_test:
            self.status.setText(
                "Reachability testing is disabled until simulator recovery "
                "has completed."
            )
            return
        host = self.host.text().strip()
        if not host:
            self.status.setText("Enter the Raspberry Pi address first.")
            return
        self.test_button.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(
            QtCore.Qt.CursorShape.WaitCursor
        )
        try:
            controller = self._reachable(
                host,
                self.controller_port.value(),
            )
            camera = self._reachable(host, self.camera_port.value())
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.test_button.setEnabled(True)
        if controller and camera:
            self.status.setText(
                "Both bridge ports are reachable; hardware remains unverified."
            )
        else:
            missing = []
            if not controller:
                missing.append("controller")
            if not camera:
                missing.append("camera")
            self.status.setText(
                "Could not reach: " + " and ".join(missing) + "."
            )


class _MachinePage(QtWidgets.QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Machine work area")
        self.setSubTitle(
            "Review the usable X/Y area. The initial photo position is its center."
        )
        self._initialized_profile_id: str | None = None
        layout = QtWidgets.QFormLayout(self)
        self.width = QtWidgets.QDoubleSpinBox()
        self.width.setRange(1.0, 5000.0)
        self.width.setDecimals(2)
        self.width.setSuffix(" mm")
        self.height = QtWidgets.QDoubleSpinBox()
        self.height.setRange(1.0, 5000.0)
        self.height.setDecimals(2)
        self.height.setSuffix(" mm")
        layout.addRow("X width", self.width)
        layout.addRow("Y height", self.height)
        note = QtWidgets.QLabel(
            "Saving these values does not enable motion. Verify physical travel "
            "and limits separately before opting in later."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addRow(note)

    def initializePage(self) -> None:
        wizard = self.wizard()
        profile_page = getattr(wizard, "profile", None)
        if profile_page is None:
            return
        profile_id = str(profile_page.machine_profile.currentData())
        if profile_id == self._initialized_profile_id:
            return
        defaults = builtin_machine_profiles()[profile_id].machine_defaults
        self.width.setValue(defaults.work_area.width)
        self.height.setValue(defaults.work_area.height)
        self._initialized_profile_id = profile_id


class _CameraPage(QtWidgets.QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Camera starting settings")
        self.setSubTitle(
            "These values configure the camera endpoint; calibration remains a separate review."
        )
        layout = QtWidgets.QFormLayout(self)
        self.width = QtWidgets.QSpinBox()
        self.width.setRange(320, 7680)
        self.width.setValue(1920)
        self.height = QtWidgets.QSpinBox()
        self.height.setRange(240, 4320)
        self.height.setValue(1080)
        self.autofocus = QtWidgets.QCheckBox("Start with camera autofocus")
        self.autofocus.setChecked(False)
        self.focus = QtWidgets.QSpinBox()
        self.focus.setRange(0, 250)
        self.focus.setValue(40)
        layout.addRow("Camera width", self.width)
        layout.addRow("Camera height", self.height)
        layout.addRow(self.autofocus)
        layout.addRow("Manual focus", self.focus)
        self.autofocus.toggled.connect(
            lambda checked: self.focus.setEnabled(not checked)
        )
        note = QtWidgets.QLabel(
            "No camera or calibration evidence is inherited from another saved "
            "machine. Complete and review calibration for this machine separately."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addRow(note)


class _FinishPage(QtWidgets.QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Ready to save")
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.message)
        layout.addStretch(1)

    def initializePage(self) -> None:
        wizard = self.wizard()
        recovery_choice = getattr(wizard, "recovery_choice", None)
        selected_machine_id = (
            recovery_choice.selected_machine_id
            if recovery_choice is not None
            else None
        )
        if selected_machine_id is not None:
            selected = next(
                machine
                for machine in recovery_choice.recovery.physical_machines
                if machine.id == selected_machine_id
            )
            self.message.setText(
                f"Click Finish to explicitly select {selected.name} and retire "
                "the legacy simulator records. No connection, Home, jog, arming, "
                "motion, output, or physical verification is performed."
            )
            return
        self.message.setText(
            "Click Finish to save and select this machine for the next E3 "
            "launch. Motion and laser output remain disabled. No connection, "
            "Home, jog, arming, output, or physical verification is performed."
        )


class FirstRunWizard(QtWidgets.QWizard):
    def __init__(
        self,
        template_config: Path,
        parent: QtWidgets.QWidget | None = None,
        *,
        recovery: SimulatorRecoveryPlan | None = None,
    ) -> None:
        super().__init__(parent)
        self.template_config = Path(template_config)
        self.recovery = recovery
        self.saved_config: Path | None = None
        self.setWindowTitle(
            "E3 Simulator Recovery"
            if recovery is not None
            else "E3 First-Run Setup"
        )
        self.setWizardStyle(QtWidgets.QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(660, 480)
        self.welcome = _WelcomePage(
            recovering_simulator=recovery is not None
        )
        self.recovery_choice = (
            _RecoveryChoicePage(recovery)
            if recovery is not None
            else None
        )
        self.profile = _ProfilePage()
        self.connection = _ConnectionPage(
            allow_reachability_test=recovery is None
        )
        self.machine = _MachinePage()
        self.camera = _CameraPage()
        self.finish = _FinishPage()
        self.setPage(_WELCOME, self.welcome)
        self.setPage(_PROFILE, self.profile)
        self.setPage(_CONNECTION, self.connection)
        self.setPage(_MACHINE, self.machine)
        self.setPage(_CAMERA, self.camera)
        self.setPage(_FINISH, self.finish)
        if self.recovery_choice is not None:
            self.setPage(_RECOVERY_CHOICE, self.recovery_choice)
        self.setStartId(_WELCOME)

    @property
    def open_machine_setup(self) -> bool:
        return True

    def accept(self) -> None:
        if self.recovery_choice is not None:
            selected_machine_id = self.recovery_choice.selected_machine_id
            if selected_machine_id is not None:
                try:
                    self.saved_config = save_simulator_recovery_selection(
                        self.recovery_choice.recovery,
                        selected_machine_id,
                    )
                except Exception as exc:
                    QtWidgets.QMessageBox.critical(self, "E3 Setup", str(exc))
                    return
                super().accept()
                return
            if not self.recovery_choice.creating_new_machine:
                QtWidgets.QMessageBox.warning(
                    self,
                    "E3 Setup",
                    "Choose an existing physical machine or configure a new one.",
                )
                return
        machine_profile_id = str(
            self.profile.machine_profile.currentData()
        )
        tool_head_profile_id = str(
            self.profile.tool_head_profile.currentData()
        )
        options: dict[str, object] = {
            "bridge_token": self.connection.token.text(),
            "host": self.connection.host.text(),
            "controller_port": self.connection.controller_port.value(),
            "camera_port": self.connection.camera_port.value(),
            "width_mm": self.machine.width.value(),
            "height_mm": self.machine.height.value(),
            "camera_width": self.camera.width.value(),
            "camera_height": self.camera.height.value(),
            "autofocus": self.camera.autofocus.isChecked(),
            "focus_value": self.camera.focus.value(),
        }
        try:
            self.saved_config = save_profile_setup(
                self.template_config,
                machine_name=self.profile.machine_name.text(),
                machine_profile_id=machine_profile_id,
                tool_head_profile_id=tool_head_profile_id,
                simulator_recovery=self.recovery,
                **options,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "E3 Setup", str(exc))
            return
        super().accept()


def run_first_run_setup(
    template_config: Path | None = None,
    parent: QtWidgets.QWidget | None = None,
) -> FirstRunResult | None:
    template = (
        Path(template_config)
        if template_config is not None
        else _default_template_config()
    )
    wizard = FirstRunWizard(template, parent)
    try:
        if wizard.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        if wizard.saved_config is None:
            return None
        return FirstRunResult(
            wizard.saved_config,
            wizard.open_machine_setup,
        )
    finally:
        wizard.deleteLater()


def run_simulator_recovery(
    recovery: SimulatorRecoveryPlan,
    parent: QtWidgets.QWidget | None = None,
) -> FirstRunResult | None:
    wizard = FirstRunWizard(
        recovery.source_config_path,
        parent,
        recovery=recovery,
    )
    try:
        if wizard.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        if wizard.saved_config is None:
            return None
        return FirstRunResult(
            wizard.saved_config,
            wizard.open_machine_setup,
        )
    finally:
        wizard.deleteLater()


def _help_menu(window: QtWidgets.QMainWindow) -> QtWidgets.QMenu:
    retained = getattr(window, "help_menu", None)
    if isinstance(retained, QtWidgets.QMenu):
        return retained
    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if (
            menu is not None
            and menu.title().replace("&", "").strip().lower() == "help"
        ):
            window.help_menu = menu
            return menu
    menu = window.menuBar().addMenu("&Help")
    window.help_menu = menu
    return menu


def install_first_run_menu(
    window: QtWidgets.QMainWindow,
) -> QtGui.QAction | None:
    if user_config_path().is_file():
        return None
    action = QtGui.QAction("Set Up Machine…", window)
    action.setObjectName("firstRunHardwareSetupAction")

    def begin_setup(checked: bool = False) -> None:
        del checked
        result = run_first_run_setup(parent=window)
        if result is None:
            return
        action.setEnabled(False)
        next_step = (
            "After restart, review this machine in Machine Setup before "
            "enabling motion or output."
        )
        QtWidgets.QMessageBox.information(
            window,
            "E3 Setup Saved",
            "The saved machine profile was selected for the next E3 launch.\n\n"
            f"{next_step}\n\n"
            "No controller action or physical verification was performed.",
        )

    action.triggered.connect(begin_setup)
    menu = _help_menu(window)
    before = menu.actions()[0] if menu.actions() else None
    if before is None:
        menu.addAction(action)
    else:
        menu.insertAction(before, action)
        menu.insertSeparator(before)
    return action
