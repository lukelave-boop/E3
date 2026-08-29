from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.core import CoreRuntime
from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.materials import MaterialDatabase, MaterialPreset
from laser_aligner.project import LayerMode


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _runtime(tmp_path: Path) -> CoreRuntime:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "default.json").read_text())
    payload["app"]["data_dir"] = str(tmp_path / "data")
    payload["app"]["open_browser"] = False
    payload["machine"]["port"] = "COM_TEST"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return CoreRuntime.from_config(path, hardware_enabled=False)


def _wait_until(
    application: QtWidgets.QApplication,
    predicate,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for desktop material test")
        time.sleep(0.002)


def _dispose(
    application: QtWidgets.QApplication,
    window: E3MainWindow,
) -> None:
    window.history.mark_clean()
    window._cancel_job_preparation("Test cleanup")
    window._cancel_job_render()
    window.controller.stop()
    window._closing = True
    window.close()
    window.deleteLater()
    application.processEvents()


def test_browsing_and_applying_recipe_after_startup_invokes_no_machine_actions(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setattr(
        main_window_module,
        "MaterialDatabase",
        lambda: MaterialDatabase(tmp_path / "materials.sqlite"),
    )
    window = E3MainWindow(_runtime(tmp_path))
    errors: list[str] = []
    window.show_error = errors.append  # type: ignore[method-assign]
    window.show()
    _wait_until(
        qt_application,
        lambda: window.runtime.running and not window.controller.has_active_tasks,
    )

    identity = window.runtime.context.machine_identity
    recipe = window.material_database.save(
        MaterialPreset(
            material="Action gate",
            name="Desktop apply",
            mode=LayerMode.RASTER,
            speed_mm_min=1234.0,
            power_percent=23.0,
            passes=2,
            line_interval_mm=0.08,
            scan_angle_deg=45.0,
            overscan_percent=7.0,
            air_assist=True,
            machine_profile_id=identity.machine_profile_id,
            tool_head_profile_id=identity.tool_head_profile_id,
        )
    )
    active_layer_id = window.active_layer_id
    window.document.get_layer(active_layer_id).output_enabled = False
    history_depth = window.history.depth
    action_calls: list[str] = []
    exact_calls: list[object] = []

    def record_action(name: str):
        def operation(*_args, **_kwargs) -> None:
            action_calls.append(name)

        return operation

    try:
        with monkeypatch.context() as action_patch:
            action_patch.setattr(
                main_window_module,
                "generate_project_gcode",
                lambda *args, **kwargs: exact_calls.append((args, kwargs)),
            )
            machine = window.runtime.context.machine
            for name in (
                "connect",
                "disconnect",
                "replace_connection",
                "arm",
                "arm_program",
                "disarm",
                "send_command",
                "prepare_photo_position",
                "prepare_job_start",
                "jog",
                "preflight_program",
                "start_validated_program",
                "start_job",
                "request_stop",
                "stop_job",
            ):
                action_patch.setattr(machine, name, record_action(f"machine.{name}"))
            for name in (
                "connect_machine",
                "reconnect_machine",
                "disconnect_machine",
                "park_at_camera_pose",
                "run_job",
                "pause_resume",
                "emergency_stop",
                "send_diagnostic",
                "jog",
            ):
                action_patch.setattr(
                    window.controller,
                    name,
                    record_action(f"controller.{name}"),
                )

            window.material_panel.search.setText("Action gate")
            qt_application.processEvents()
            assert window.material_panel.list.count() == 1
            item = window.material_panel.list.item(0)
            assert item.data(QtCore.Qt.ItemDataRole.UserRole) == recipe.id
            window.material_panel.list.setCurrentItem(item)
            qt_application.processEvents()
            assert window.material_panel.apply_button.isEnabled()

            window.material_panel.apply_button.click()
            qt_application.processEvents()

            applied = window.document.get_layer(active_layer_id)
            assert window.history.depth == history_depth + 1
            assert window.active_layer_id == active_layer_id
            assert applied.output_enabled is False
            assert applied.speed_mm_min == 1234.0
            assert applied.power_percent == 23.0
            assert applied.scan_angle_deg == 45.0
            assert applied.air_assist is True
            assert exact_calls == []
            assert action_calls == []
            assert errors == []
    finally:
        _dispose(qt_application, window)
