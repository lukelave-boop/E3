from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

from ..deployment import application_root, user_config_path
from ..first_run import save_hardware_setup
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()

_WELCOME = 0
_CONNECTION = 1
_MACHINE = 2
_CAMERA = 3
_FINISH = 4


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
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome to E3")
        self.setSubTitle("Set up the controller and camera for this machine.")
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "This guided setup creates the machine-specific files that E3 keeps "
            "separate from the application. Future E3 updates will not replace "
            "these settings or calibration data."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addStretch(1)

    def nextId(self) -> int:
        return _CONNECTION


class _ConnectionPage(QtWidgets.QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Connect to the E3 hardware node")
        self.setSubTitle(
            "Enter the Raspberry Pi address and the bridge credential configured on the Pi."
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
        self.test_button = QtWidgets.QPushButton("Test network connection")
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        row.addWidget(self.test_button)
        row.addWidget(self.status, 1)
        layout.addRow(row)
        note = QtWidgets.QLabel(
            "The test checks that both E3 bridge ports are reachable. The credential "
            "itself is validated when E3 starts the hardware connection."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addRow(note)
        self.test_button.clicked.connect(self._test_connection)
        self.host.textChanged.connect(self.completeChanged)
        self.token.textChanged.connect(self.completeChanged)

    def isComplete(self) -> bool:
        return bool(self.host.text().strip()) and len(self.token.text().strip()) >= 24

    def validatePage(self) -> bool:
        if not self.host.text().strip():
            QtWidgets.QMessageBox.warning(self, "E3 Setup", "Enter the Raspberry Pi address.")
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
        host = self.host.text().strip()
        if not host:
            self.status.setText("Enter the Raspberry Pi address first.")
            return
        self.test_button.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            controller = self._reachable(host, self.controller_port.value())
            camera = self._reachable(host, self.camera_port.value())
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.test_button.setEnabled(True)
        if controller and camera:
            self.status.setText("Controller and camera bridge ports are reachable.")
        else:
            missing = []
            if not controller:
                missing.append("controller")
            if not camera:
                missing.append("camera")
            self.status.setText("Could not reach: " + " and ".join(missing) + ".")


class _MachinePage(QtWidgets.QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Machine work area")
        self.setSubTitle(
            "Enter the usable X/Y travel area. E3 will start the photo position at the center."
        )
        layout = QtWidgets.QFormLayout(self)
        self.width = QtWidgets.QDoubleSpinBox()
        self.width.setRange(1.0, 5000.0)
        self.width.setDecimals(2)
        self.width.setSuffix(" mm")
        self.width.setValue(220.0)
        self.height = QtWidgets.QDoubleSpinBox()
        self.height.setRange(1.0, 5000.0)
        self.height.setDecimals(2)
        self.height.setSuffix(" mm")
        self.height.setValue(220.0)
        layout.addRow("X width", self.width)
        layout.addRow("Y height", self.height)


class _CameraPage(QtWidgets.QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Camera starting settings")
        self.setSubTitle(
            "These values get the camera online. Fine focus and calibration are completed "
            "inside Machine Setup."
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
        self.autofocus.toggled.connect(lambda checked: self.focus.setEnabled(not checked))
        note = QtWidgets.QLabel(
            "For precision positioning, lock focus before performing lens and bed calibration. "
            "Machine Setup provides the focus tools and calibration steps."
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addRow(note)


class _FinishPage(QtWidgets.QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Ready")
        self.message = QtWidgets.QLabel(
            "Click Finish to save the machine connection and open E3. Machine Setup will "
            "then open at the Camera tab so you can verify focus, complete lens calibration, "
            "and create the bed mapping."
        )
        self.message.setWordWrap(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.message)
        layout.addStretch(1)


class FirstRunWizard(QtWidgets.QWizard):
    def __init__(self, template_config: Path, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.template_config = Path(template_config)
        self.saved_config: Path | None = None
        self.setWindowTitle("E3 First-Run Setup")
        self.setWizardStyle(QtWidgets.QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(620, 440)
        self.welcome = _WelcomePage()
        self.connection = _ConnectionPage()
        self.machine = _MachinePage()
        self.camera = _CameraPage()
        self.finish = _FinishPage()
        self.setPage(_WELCOME, self.welcome)
        self.setPage(_CONNECTION, self.connection)
        self.setPage(_MACHINE, self.machine)
        self.setPage(_CAMERA, self.camera)
        self.setPage(_FINISH, self.finish)
        self.setStartId(_WELCOME)

    def accept(self) -> None:
        try:
            self.saved_config = save_hardware_setup(
                self.template_config,
                bridge_token=self.connection.token.text(),
                host=self.connection.host.text(),
                controller_port=self.connection.controller_port.value(),
                camera_port=self.connection.camera_port.value(),
                width_mm=self.machine.width.value(),
                height_mm=self.machine.height.value(),
                camera_width=self.camera.width.value(),
                camera_height=self.camera.height.value(),
                autofocus=self.camera.autofocus.isChecked(),
                focus_value=self.camera.focus.value(),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "E3 Setup", str(exc))
            return
        super().accept()


def run_first_run_setup(
    template_config: Path | None = None,
    parent: QtWidgets.QWidget | None = None,
) -> FirstRunResult | None:
    template = Path(template_config) if template_config is not None else _default_template_config()
    wizard = FirstRunWizard(template, parent)
    try:
        if wizard.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return None
        if wizard.saved_config is None:
            return None
        return FirstRunResult(wizard.saved_config, True)
    finally:
        wizard.deleteLater()


def _help_menu(window: QtWidgets.QMainWindow) -> QtWidgets.QMenu:
    retained = getattr(window, "help_menu", None)
    if isinstance(retained, QtWidgets.QMenu):
        return retained
    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if menu is not None and menu.title().replace("&", "").strip().lower() == "help":
            window.help_menu = menu
            return menu
    menu = window.menuBar().addMenu("&Help")
    window.help_menu = menu
    return menu


def install_first_run_menu(window: QtWidgets.QMainWindow) -> QtGui.QAction | None:
    if user_config_path().is_file():
        return None
    action = QtGui.QAction("Set Up Hardware…", window)
    action.setObjectName("firstRunHardwareSetupAction")

    def begin_setup(checked: bool = False) -> None:
        del checked
        result = run_first_run_setup(parent=window)
        if result is None:
            return
        action.setEnabled(False)
        QtWidgets.QMessageBox.information(
            window,
            "E3 Setup Saved",
            "The machine configuration and bridge credential were saved.\n\n"
            "Close and reopen E3 to start using the hardware. Your setup files will be "
            "preserved across future E3 updates.",
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
