from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..identity import APPLICATION_NAME, application_version
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
    application.setApplicationVersion(application_version())


def _recovery_config_destination(
    config: Path,
    *,
    preserved_user_config: Path,
    explicit_config: bool,
) -> Path:
    """Keep explicit configs in place; move legacy fallbacks to durable state."""

    config = Path(config).expanduser().resolve()
    preserved = Path(preserved_user_config).expanduser().resolve()
    if explicit_config or config == preserved or preserved.is_file():
        return config
    return preserved


def _prepare_runtime_startup(
    config: Path,
    *,
    preserved_user_config: Path,
    first_run_runner: Callable[[Path], Any],
    recovery_inspector: Callable[[Path], Any],
    recovery_runner: Callable[[Any], Any],
    before_runtime: Callable[[], None],
    runtime_factory: Callable[[Path], Any],
) -> tuple[Any, bool] | None:
    """Complete every required setup transaction before runtime construction."""

    open_machine_setup = False
    if config.name == "default.json":
        if preserved_user_config.is_file():
            config = preserved_user_config
        else:
            first_run = first_run_runner(config)
            if first_run is None:
                return None
            config = Path(first_run.config_path)
            open_machine_setup = bool(first_run.open_machine_setup)

    recovery = recovery_inspector(config)
    if recovery is not None:
        recovered = recovery_runner(recovery)
        if recovered is None:
            return None
        config = Path(recovered.config_path)
        open_machine_setup = bool(recovered.open_machine_setup)

    before_runtime()
    return runtime_factory(config), open_machine_setup


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
    from ..deployment import read_bridge_token, resolve_launch_profile, user_config_path
    from ..first_run import inspect_simulator_recovery
    from .dialogs import install_modal_dialog_first_paint_fix
    from .first_run import (
        install_first_run_menu,
        run_first_run_setup,
        run_simulator_recovery,
    )
    from .main_window import E3MainWindow
    from .theme import apply_dark_theme
    from .update_ui import install_update_menu

    application = QtWidgets.QApplication([sys.argv[0]])
    configure_application_identity(application)
    application.setOrganizationName("E3")
    application.setOrganizationDomain("local.e3-positioning-system")
    apply_dark_theme(application)
    install_modal_dialog_first_paint_fix(application)

    config = arguments.config
    explicit_config = config is not None
    if config is None:
        config = resolve_launch_profile().config_path

    try:
        def configure_bridge_token() -> None:
            token = read_bridge_token()
            if token:
                os.environ["E3_BRIDGE_TOKEN"] = token

        preserved_config = user_config_path()

        def inspect_recovery(prepared_config: Path) -> Any:
            return inspect_simulator_recovery(
                prepared_config,
                replacement_config_path=_recovery_config_destination(
                    prepared_config,
                    preserved_user_config=preserved_config,
                    explicit_config=explicit_config,
                ),
            )

        prepared = _prepare_runtime_startup(
            Path(config),
            preserved_user_config=preserved_config,
            first_run_runner=run_first_run_setup,
            recovery_inspector=inspect_recovery,
            recovery_runner=run_simulator_recovery,
            before_runtime=configure_bridge_token,
            runtime_factory=lambda prepared_config: CoreRuntime.from_config(
                prepared_config,
                hardware_enabled=True,
                laser_lockout=False,
            ),
        )
    except Exception as exc:
        QtWidgets.QMessageBox.critical(
            None,
            "E3 Positioning System",
            f"Could not load the configuration:\n{exc}",
        )
        return 1
    if prepared is None:
        return 0
    runtime, open_first_run_machine_setup = prepared

    window = E3MainWindow(runtime)
    install_update_menu(window)
    install_first_run_menu(window)
    application.aboutToQuit.connect(window.controller.stop)
    window.show()
    if open_first_run_machine_setup:
        QtCore.QTimer.singleShot(500, lambda: window.open_machine_setup(0))
    return int(application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
