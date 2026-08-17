import ast
from pathlib import Path

from laser_aligner import __version__
from laser_aligner.desktop.main import build_parser, configure_application_identity
from laser_aligner.desktop.qt import PYSIDE6_IMPORT_ERROR
from laser_aligner.identity import APPLICATION_NAME


def test_desktop_python_sources_parse():
    desktop = Path(__file__).resolve().parents[1] / "laser_aligner" / "desktop"
    for path in desktop.glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_desktop_cli_exposes_hardware_and_safe_modes():
    parser = build_parser()
    hardware = parser.parse_args(["--hardware"])
    safe = parser.parse_args(["--safe"])

    assert hardware.hardware is True
    assert safe.safe is True


def test_qt_import_guard_is_explicit():
    # CI does not need to install the large desktop dependency to validate core logic.
    assert PYSIDE6_IMPORT_ERROR is None or isinstance(PYSIDE6_IMPORT_ERROR, ImportError)


def test_desktop_identity_does_not_request_an_x11_title_suffix():
    class FakeApplication:
        def __init__(self) -> None:
            self.name = None
            self.display_name = None
            self.version = None

        def setApplicationName(self, value: str) -> None:
            self.name = value

        def setApplicationDisplayName(self, value: str) -> None:
            self.display_name = value

        def setApplicationVersion(self, value: str) -> None:
            self.version = value

    application = FakeApplication()
    configure_application_identity(application)

    assert application.name == APPLICATION_NAME
    assert application.display_name == ""
    assert application.version == __version__


def test_machine_setup_registration_job_uses_the_normal_main_job_pipeline():
    source = (Path(__file__).resolve().parents[1] / "laser_aligner" / "desktop" / "main_window.py").read_text(
        encoding="utf-8"
    )

    assert "dialog.registrationJobPrepared.connect(" in source
    assert "dialog.validationJobPrepared.connect(" in source
    assert source.count("QtCore.Qt.ConnectionType.QueuedConnection") >= 2
    assert "exact_powered = plan.powered" in source
    assert '"powered": exact_powered' in source
    assert "self._install_generated_job(" in source
    assert "self.workspace.start_toolpath_preview(" in source


def test_main_help_menu_exposes_the_packaged_setup_runbook():
    source = (Path(__file__).resolve().parents[1] / "laser_aligner" / "desktop" / "main_window.py").read_text(
        encoding="utf-8"
    )

    assert 'action("setup_guide", "Permanent camera setup guide…")' in source
    assert 'help_menu.addAction(self.actions["setup_guide"])' in source
    assert "show_setup_guide(self)" in source


def test_dense_validation_preview_identifies_commanded_and_detected_points():
    source = (Path(__file__).resolve().parents[1] / "laser_aligner" / "desktop" / "machine_setup.py").read_text(
        encoding="utf-8"
    )

    assert "cyan X = commanded" in source
    assert "colored circle = detected" in source
    assert "cv2.line(preview, expected, detected" in source
