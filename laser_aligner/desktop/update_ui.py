from __future__ import annotations

from pathlib import Path
from typing import Any

from ..deployment import load_build_info
from ..updates import (
    UpdateCheckResult,
    check_for_update,
    download_update,
    launch_downloaded_update,
)
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _menu_title(menu: Any) -> str:
    return str(menu.title()).replace("&", "").strip().lower()


def _help_menu(window: QtWidgets.QMainWindow) -> QtWidgets.QMenu:
    retained = getattr(window, "help_menu", None)
    if isinstance(retained, QtWidgets.QMenu):
        return retained
    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if menu is not None and _menu_title(menu) == "help":
            window.help_menu = menu
            return menu
    menu = window.menuBar().addMenu("&Help")
    window.help_menu = menu
    return menu


def install_update_menu(window: QtWidgets.QMainWindow) -> QtGui.QAction:
    """Add Help > Check for Updates without coupling the main window to updates."""

    existing = getattr(window, "actions", {}).get("check_updates")
    if isinstance(existing, QtGui.QAction):
        return existing
    action = QtGui.QAction("Check for Updates…", window)
    action.setObjectName("checkForUpdatesAction")
    action.triggered.connect(lambda checked=False: _begin_check(window, action))
    actions = getattr(window, "actions", None)
    if isinstance(actions, dict):
        actions["check_updates"] = action
    menu = _help_menu(window)
    before = next(
        (
            item
            for item in menu.actions()
            if "about e3" in item.text().replace("&", "").strip().lower()
        ),
        None,
    )
    if before is not None:
        menu.insertAction(before, action)
        menu.insertSeparator(before)
    else:
        menu.addAction(action)
    return action


def _progress_dialog(
    window: QtWidgets.QMainWindow,
    label: str,
) -> QtWidgets.QProgressDialog:
    dialog = QtWidgets.QProgressDialog(label, "", 0, 0, window)
    dialog.setWindowTitle("E3 Update")
    dialog.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
    dialog.setCancelButton(None)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.show()
    window._e3_update_progress = dialog  # type: ignore[attr-defined]
    return dialog


def _close_progress(window: QtWidgets.QMainWindow) -> None:
    dialog = getattr(window, "_e3_update_progress", None)
    if isinstance(dialog, QtWidgets.QProgressDialog):
        dialog.close()
        dialog.deleteLater()
    window._e3_update_progress = None  # type: ignore[attr-defined]


def _show_failure(
    window: QtWidgets.QMainWindow,
    action: QtGui.QAction,
    message: str,
) -> None:
    _close_progress(window)
    action.setEnabled(True)
    QtWidgets.QMessageBox.critical(
        window,
        "E3 Update",
        f"The update operation could not be completed.\n\n{message}",
    )


def _begin_check(window: QtWidgets.QMainWindow, action: QtGui.QAction) -> None:
    build = load_build_info()
    if not build.manifest_url:
        QtWidgets.QMessageBox.information(
            window,
            "E3 Update",
            "This source/development launch does not contain packaged update "
            "metadata.\n\nInstall an updater-enabled E3 package to use automatic "
            "updates.",
        )
        return
    action.setEnabled(False)
    _progress_dialog(window, "Checking GitHub for an E3 update…")
    window.controller.run_background(
        lambda: check_for_update(build),
        on_success=lambda result: _check_complete(window, action, result),
        on_failure=lambda message: _show_failure(window, action, message),
        label="Check for E3 updates",
        show_busy=False,
    )


def _check_complete(
    window: QtWidgets.QMainWindow,
    action: QtGui.QAction,
    result: UpdateCheckResult,
) -> None:
    _close_progress(window)
    action.setEnabled(True)
    if not result.available:
        QtWidgets.QMessageBox.information(
            window,
            "E3 is up to date",
            f"E3 {result.current.version} · build "
            f"{result.current.short_revision}\n"
            f"Update channel: {result.current.channel}\n\n"
            "This installation already matches the newest published build.",
        )
        return
    message = QtWidgets.QMessageBox(window)
    message.setWindowTitle("E3 Update Available")
    message.setIcon(QtWidgets.QMessageBox.Icon.Information)
    message.setText(
        f"E3 {result.manifest.version} · build "
        f"{result.manifest.revision[:8]} is available."
    )
    message.setInformativeText(
        f"Installed build: {result.current.short_revision}\n"
        f"Channel: {result.current.channel}\n\n"
        "The update replaces application files only. Your machine configuration, "
        "bridge credential, camera calibration, bed mapping, templates, materials, "
        "and projects remain in the separate E3 user-data folder."
    )
    install_button = message.addButton(
        "Download and Install",
        QtWidgets.QMessageBox.ButtonRole.AcceptRole,
    )
    message.addButton("Later", QtWidgets.QMessageBox.ButtonRole.RejectRole)
    message.exec()
    if message.clickedButton() is install_button:
        _begin_download(window, action, result)


def _begin_download(
    window: QtWidgets.QMainWindow,
    action: QtGui.QAction,
    result: UpdateCheckResult,
) -> None:
    action.setEnabled(False)
    _progress_dialog(window, f"Downloading and verifying {result.asset.name}…")
    window.controller.run_background(
        lambda: download_update(result.asset),
        on_success=lambda path: _download_complete(window, action, path),
        on_failure=lambda message: _show_failure(window, action, message),
        label="Download E3 update",
        show_busy=False,
    )


def _handoff_downloaded_update(
    window: QtWidgets.QMainWindow,
    path: Path,
) -> bool:
    """Close E3 safely, launch the verified package, then terminate E3."""

    application = QtWidgets.QApplication.instance()
    if application is None:
        raise RuntimeError("E3 application instance is unavailable")

    previous_quit_on_last_window = application.quitOnLastWindowClosed()
    application.setQuitOnLastWindowClosed(False)
    closed = False

    try:
        # Keep the process alive while close() performs the normal unsaved-
        # project checks. Closing the last Qt window must not terminate E3
        # before the verified installer has actually been spawned.
        if not window.close():
            return False
        closed = True

        try:
            launch_downloaded_update(path)
        except Exception as exc:
            # E3MainWindow.closeEvent has already stopped the controller and
            # runtime. Do not present that terminal window as usable again.
            try:
                _show_terminal_handoff_failure(path, exc)
            finally:
                application.quit()
            return False

        application.quit()
        return True
    finally:
        # Only a rejected close leaves the current E3 runtime alive.
        if not closed:
            application.setQuitOnLastWindowClosed(
                previous_quit_on_last_window
            )


def _show_handoff_failure(
    window: QtWidgets.QMainWindow,
    error: Exception,
) -> None:
    QtWidgets.QMessageBox.critical(
        window if window.isVisible() else None,
        "E3 Update",
        "The verified package was downloaded, but could not be started.\n\n"
        f"{error}",
    )


def _show_terminal_handoff_failure(path: Path, error: Exception) -> None:
    installer_path = path.expanduser().resolve()
    QtWidgets.QMessageBox.critical(
        None,
        "E3 Update",
        "E3 could not start the verified installer after shutting down.\n\n"
        f"Installer:\n{installer_path}\n\n"
        "Run that installer manually to complete the update. "
        "E3 will now exit.\n\n"
        f"Details:\n{error}",
    )


def _perform_downloaded_update_handoff(
    window: QtWidgets.QMainWindow,
    path: Path,
) -> None:
    try:
        _handoff_downloaded_update(window, path)
    except Exception as exc:
        _show_handoff_failure(window, exc)


def _request_downloaded_update_handoff(
    window: QtWidgets.QMainWindow,
    path: Path,
) -> None:
    """Wait for controller task ownership to drain before closing E3."""

    controller = window.controller
    previous = getattr(window, "_e3_update_idle_handoff", None)
    if previous is not None:
        try:
            controller.tasksDrained.disconnect(previous)
        except (RuntimeError, TypeError):
            pass
        window._e3_update_idle_handoff = None  # type: ignore[attr-defined]

    prepare_close = getattr(window, "_prepare_close_request", None)
    if callable(prepare_close) and not prepare_close():
        return

    def attempt() -> None:
        if getattr(window, "_e3_update_idle_handoff", None) is not attempt:
            return
        if controller.has_active_tasks:
            return
        try:
            controller.tasksDrained.disconnect(attempt)
        except (RuntimeError, TypeError):
            pass
        window._e3_update_idle_handoff = None  # type: ignore[attr-defined]
        _perform_downloaded_update_handoff(window, path)

    if not controller.has_active_tasks:
        _perform_downloaded_update_handoff(window, path)
        return

    window._e3_update_idle_handoff = attempt  # type: ignore[attr-defined]
    # DesktopController emits tasksDrained from its GUI-thread cleanup slot.
    # MainWindow suppresses its ordinary close-after-drain timer while this
    # handler owns the pending updater handoff.
    controller.tasksDrained.connect(
        attempt,
        QtCore.Qt.ConnectionType.DirectConnection,
    )


def _download_complete(
    window: QtWidgets.QMainWindow,
    action: QtGui.QAction,
    path: Path,
) -> None:
    _close_progress(window)
    action.setEnabled(True)
    answer = QtWidgets.QMessageBox.question(
        window,
        "Install E3 Update",
        "The update package passed SHA-256 verification and is ready.\n\n"
        "E3 will close before starting the installer. If the current project has "
        "unsaved changes, E3 will ask you to save them first.\n\nInstall now?",
        QtWidgets.QMessageBox.StandardButton.Yes
        | QtWidgets.QMessageBox.StandardButton.No,
        QtWidgets.QMessageBox.StandardButton.Yes,
    )
    if answer != QtWidgets.QMessageBox.StandardButton.Yes:
        return
    _request_downloaded_update_handoff(window, path)
