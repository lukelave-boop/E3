from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from threading import Event, Lock
from typing import Any

from ..deployment import load_build_info
from ..updates import (
    DOWNLOAD_MAX_SECONDS,
    DOWNLOAD_STALL_SECONDS,
    UpdateCheckResult,
    check_for_update,
    download_update,
    launch_downloaded_update,
    record_download_problem,
)
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _download_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1000
    raise AssertionError("unreachable")


class _DownloadProgress(QtWidgets.QProgressDialog):
    """Poll bounded worker state; widgets and speed sampling stay on the UI thread."""

    stalled = QtCore.Signal(str)

    def __init__(self, window: QtWidgets.QMainWindow, name: str, size: int) -> None:
        super().__init__("", "", 0, 1000, window)
        self._name = name
        self._size = size
        self._received = 0
        self._lock = Lock()
        self.cancel_event = Event()
        self._started = self._last_progress = time.monotonic()
        self._samples: deque[tuple[float, int]] = deque([(self._started, 0)])
        self.setWindowTitle("E3 Update")
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.setCancelButtonText("Cancel download")
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setMinimumWidth(480)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh)
        self._refresh()
        self._timer.start()

    def report(self, received: int, total: int) -> None:
        # Called by the download worker: never touch Qt objects here.
        with self._lock:
            if received > self._received:
                self._last_progress = time.monotonic()
            self._received = received

    def _refresh(self) -> None:
        with self._lock:
            received = self._received
            last_progress = self._last_progress
        now = time.monotonic()
        if self.cancel_event.is_set():
            return
        reason = None
        if now - self._started >= DOWNLOAD_MAX_SECONDS:
            reason = "The update exceeded the 15-minute download time limit."
        elif now - last_progress >= DOWNLOAD_STALL_SECONDS:
            reason = "The update made no progress for 45 seconds."
        if reason is not None:
            self.cancel_event.set()
            self._timer.stop()
            self.stalled.emit(
                f"{reason}\nDownloaded {_download_size(received)} of "
                f"{_download_size(self._size)}.\n\n"
                "Check your connection and use Check for Updates to retry."
            )
            return
        self._samples.append((now, received))
        while len(self._samples) > 2 and self._samples[1][0] <= now - 3.0:
            self._samples.popleft()
        start, previous = self._samples[0]
        speed = (received - previous) / (now - start) if now > start else 0.0
        value = min(1000, received * 1000 // self._size)
        # setValue on a modal QProgressDialog can process events. Set text first.
        if received == self._size:
            label = (
                f"Download complete: {_download_size(self._size)} · 100%\n"
                "Verifying update and saving package…"
            )
        else:
            label = (
                f"Downloading {self._name}\n"
                f"{_download_size(received)} / {_download_size(self._size)}"
                f" · {received * 100 // self._size}%\n"
                f"{_download_size(speed)}/s"
            )
        self.setLabelText(label)
        self.setValue(value)

    def closeEvent(self, event: Any) -> None:
        self._timer.stop()
        super().closeEvent(event)


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
    window._e3_update_progress = None  # type: ignore[attr-defined]
    if isinstance(dialog, QtWidgets.QProgressDialog):
        dialog.close()
        dialog.deleteLater()


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
        f"Download size: {_download_size(result.asset.size)}\n\n"
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
    dialog = _DownloadProgress(window, result.asset.name, result.asset.size)
    window._e3_update_progress = dialog  # type: ignore[attr-defined]

    def active() -> bool:
        return getattr(window, "_e3_update_progress", None) is dialog

    def failure(message: str) -> None:
        if active():
            record_download_problem(result.asset, message)
            _show_failure(window, action, message)

    def success(path: Path) -> None:
        if active() and not dialog.cancel_event.is_set():
            _download_complete(window, action, path)

    def cancel() -> None:
        dialog.cancel_event.set()
        if active():
            _close_progress(window)
            action.setEnabled(True)

    dialog.canceled.connect(cancel)
    dialog.stalled.connect(failure)
    dialog.show()
    try:
        window.controller.run_background(
            lambda: download_update(
                result.asset, progress=dialog.report,
                cancelled=dialog.cancel_event.is_set,
            ),
            on_success=success,
            on_failure=failure,
            cancel=dialog.cancel_event.set,
            label="Download E3 update",
            show_busy=False,
        )
    except Exception as exc:
        cancel()
        _show_failure(window, action, str(exc))


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
    prepared_close = bool(
        getattr(window, "_e3_update_close_prepared", False)
    )
    installer_launched = bool(
        getattr(window, "_e3_update_installer_launched", False)
    )
    launch_error = getattr(window, "_e3_update_installer_launch_error", None)

    try:
        if prepared_close:
            # E3MainWindow has already accepted Close and armed the hard process
            # deadline. Its pre-cleanup acceptance hook has already spawned the
            # verified external installer, so normal close only performs the
            # remaining bounded teardown and cannot preempt the handoff.
            if isinstance(launch_error, Exception):
                try:
                    closed = bool(window.close())
                    _show_terminal_handoff_failure(path, launch_error)
                finally:
                    application.quit()
                return False
            if not installer_launched:
                raise RuntimeError(
                    "Accepted E3 update close did not attempt installer launch"
                )
            if not window.close():
                application.quit()
                return False
            closed = True
            application.quit()
            return True

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
    """Enter the bounded close path, then launch the verified installer."""

    previous = getattr(window, "_e3_update_idle_handoff", None)
    if previous is not None:
        try:
            window.controller.tasksDrained.disconnect(previous)
        except (RuntimeError, TypeError):
            pass
        window._e3_update_idle_handoff = None  # type: ignore[attr-defined]

    prepare_close = getattr(window, "_prepare_close_request", None)
    if callable(prepare_close):
        try:
            accepted = prepare_close(
                before_shutdown_cleanup=lambda: launch_downloaded_update(path)
            )
        except Exception as exc:
            _show_handoff_failure(window, exc)
            return
        if not accepted:
            return
        window._e3_update_close_prepared = True  # type: ignore[attr-defined]
        window._e3_update_installer_launched = True  # type: ignore[attr-defined]
        window._e3_update_installer_launch_error = None  # type: ignore[attr-defined]
    # CloseEvent now cancels producers, drains workers for a bounded slice, and
    # tears down the runtime under one absolute deadline. Waiting for arbitrary
    # workers here would bypass that contract and could let the process watchdog
    # fire before the verified installer is spawned.
    _perform_downloaded_update_handoff(window, path)


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
