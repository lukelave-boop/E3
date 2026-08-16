from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from ..identity import APPLICATION_NAME
from .qt import PYSIDE6_IMPORT_ERROR, require_qt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e3-positioning-system",
        description="Native E3 camera-assisted laser workspace",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="JSON configuration file; defaults to config/local.json when present",
    )
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="Allow the process to open configured serial hardware",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Force serial hardware locked even when --hardware is also supplied",
    )
    parser.add_argument(
        "--laser-lockout",
        action="store_true",
        help="Allow hardware and motion while rejecting laser-enable programs",
    )
    return parser


def _default_config() -> Path | None:
    project_root = Path(__file__).resolve().parents[2]
    local = project_root / "config" / "local.json"
    if local.exists():
        return local
    default = project_root / "config" / "default.json"
    return default if default.exists() else None


def configure_application_identity(application: Any) -> None:
    application.setApplicationName(APPLICATION_NAME)
    # X11 appends a non-empty application display name to native window titles.
    # Each E3 window supplies its own complete caption, so leave this empty to
    # avoid a second "— E3 Positioning System" suffix in the title bar.
    application.setApplicationDisplayName("")
    application.setApplicationVersion(__version__)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)

    if PYSIDE6_IMPORT_ERROR is not None:
        print(
            "PySide6 is not installed.\n"
            "Install the native desktop dependencies with:\n\n"
            '  python -m pip install -e ".[desktop]"\n',
            file=sys.stderr,
        )
        return 2

    QtCore, QtGui, QtWidgets = require_qt()
    from ..core import CoreRuntime
    from .dialogs import install_modal_dialog_first_paint_fix
    from .main_window import E3MainWindow
    from .theme import apply_dark_theme
    from .update_ui import install_update_menu

    application = QtWidgets.QApplication([sys.argv[0]])
    configure_application_identity(application)
    application.setOrganizationName("E3")
    application.setOrganizationDomain("local.e3-positioning-system")
    apply_dark_theme(application)
    install_modal_dialog_first_paint_fix(application)

    config = arguments.config or _default_config()
    hardware_enabled = bool(arguments.hardware and not arguments.safe)
    try:
        runtime = CoreRuntime.from_config(
            config,
            hardware_enabled=hardware_enabled,
            laser_lockout=arguments.laser_lockout,
        )
    except Exception as exc:
        QtWidgets.QMessageBox.critical(
            None,
            "E3 Positioning System",
            f"Could not load the configuration:\n{exc}",
        )
        return 1

    window = E3MainWindow(runtime)
    install_update_menu(window)
    application.aboutToQuit.connect(window.controller.stop)
    window.show()
    return int(application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
