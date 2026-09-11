from __future__ import annotations

# Qt must use the offscreen platform before desktop imports.
# ruff: noqa: E402, I001

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.project import OperationLayer, load_project
from laser_aligner.project.job_preflight import JobPreflightReport
from tests.test_desktop_job_async import _add_line_output, _dispose, _wait_until, _window


@pytest.mark.parametrize("action", ["save_shortcut", "save_toolbar", "generate_shortcut"])
@pytest.mark.parametrize(("entered", "expected"), [("12", 12), ("0", 1)])
def test_project_action_commits_pending_layer_value_before_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    entered: str,
    expected: int,
) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window, errors, _notices = _window(tmp_path, monkeypatch)
    try:
        window.resize(1600, 1000)
        file_toolbar = window.findChild(QtWidgets.QToolBar, "fileToolbar")
        file_toolbar.show()
        _add_line_output(window)
        window.project_path = tmp_path / "pending-value.e3laser"
        spin = window.layer_panel.passes_spin
        spin.setFocus()
        app.processEvents()
        spin.selectAll()
        QtTest.QTest.keyClicks(spin, entered)
        assert spin.hasPendingEdit()

        if action.startswith("save"):
            if action == "save_shortcut":
                QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_S, QtCore.Qt.KeyboardModifier.ControlModifier)
            else:
                button = file_toolbar.widgetForAction(window.actions["save"])
                QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
            _wait_until(app, window.project_path.exists)
            assert load_project(window.project_path).layers[0].passes == expected
        else:
            snapshots: list[int] = []

            def preflight(document, _context, **_kwargs):
                snapshots.append(document.layers[0].passes)
                return JobPreflightReport()

            monkeypatch.setattr(main_window_module, "build_job_preflight_report", preflight)
            QtTest.QTest.keyClick(
                spin, QtCore.Qt.Key.Key_Enter,
                QtCore.Qt.KeyboardModifier.ControlModifier | QtCore.Qt.KeyboardModifier.AltModifier,
            )
            _wait_until(app, lambda: bool(snapshots))
            assert snapshots == [expected]
        assert not errors
    finally:
        _dispose(app, window)


def test_palette_choice_commits_pending_power_to_original_layer(tmp_path, monkeypatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window, errors, _notices = _window(tmp_path, monkeypatch)
    try:
        old_id = window.document.layers[0].id
        new_layer = OperationLayer(name="Second layer", power_percent=7)
        window.document.layers.append(new_layer)
        window._refresh_document()
        window.resize(1600, 1000)
        spin = window.layer_panel.power_spin
        spin.setFocus()
        app.processEvents()
        spin.selectAll()
        QtTest.QTest.keyClicks(spin, "50")
        button = window.palette._buttons[new_layer.id]
        QtTest.QTest.mouseClick(button, QtCore.Qt.MouseButton.LeftButton)
        app.processEvents()
        assert window.document.get_layer(old_id).power_percent == 50
        assert window.document.get_layer(new_layer.id).power_percent == 7
        assert window.active_layer_id == new_layer.id
        assert not errors
    finally:
        _dispose(app, window)
