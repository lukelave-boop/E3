from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.calibration.profiles import signature_from_camera_settings
from laser_aligner.config import load_settings
from laser_aligner.desktop.machine_manager import MachineManagerDialog
from laser_aligner.machine.profiles import MachineRegistry


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _runtime(tmp_path: Path):
    config = tmp_path / "network-local.json"
    config.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "simulation": False},
                "camera": {
                    "device": "e3camera://192.168.5.18:8766",
                    "width": 1920,
                    "height": 1080,
                    "fps": 15,
                    "fourcc": "MJPG",
                    "controls": {
                        "focus_automatic_continuous": 0,
                        "focus_auto": 0,
                        "focus_absolute": 10,
                    },
                },
                "machine": {
                    "backend": "serial",
                    "protocol": "grbl",
                    "port": "e3bridge://192.168.5.18:8765",
                    "allow_motion": True,
                },
                "laser": {
                    "power_mode": "M4",
                    "power_max": 1000,
                    "default_power": 100,
                },
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(config)
    registry = MachineRegistry.load_or_migrate(settings)
    return SimpleNamespace(
        settings=settings,
        machine_registry=registry,
        running_machine_id=registry.active_machine_id,
    )


def test_manager_preloads_current_machine_camera_and_calibration(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineManagerDialog(runtime)
    qt_application.processEvents()

    assert dialog.machine_list.count() == 1
    assert dialog.port.text() == "e3bridge://192.168.5.18:8765"
    assert dialog.camera_endpoint.text() == "e3camera://192.168.5.18:8766"
    assert "manual focus 10" in dialog.camera_optics.text()
    expected = signature_from_camera_settings(runtime.settings.camera).key
    saved = runtime.machine_registry.active_machine
    assert saved.camera_profile_id == expected
    assert saved.calibration_profile_id == expected

    dialog.close()


def test_manager_saves_edits_without_resetting_unexposed_settings(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    original = runtime.machine_registry.active_machine
    original.laser.guarded_output_polygon_mm = (
        (1.0, 1.0),
        (10.0, 1.0),
        (10.0, 10.0),
        (1.0, 10.0),
    )
    runtime.machine_registry.update_machine(original)
    dialog = MachineManagerDialog(runtime)
    dialog.name.setText("Home Ender laser")
    dialog.port.setText("e3bridge://home-pi:8765")

    assert dialog._save_selected() is True

    saved = runtime.machine_registry.active_machine
    assert saved.name == "Home Ender laser"
    assert saved.machine.port == "e3bridge://home-pi:8765"
    assert saved.machine.allow_motion is True
    assert saved.laser.guarded_output_polygon_mm == original.laser.guarded_output_polygon_mm
    dialog.close()


def test_manager_selection_persists_for_next_launch(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    created = runtime.machine_registry.create_machine(
        "Second machine",
        "generic-grbl",
        "generic-diode-10w",
    )
    created.machine.allow_motion = True
    runtime.machine_registry.update_machine(created)

    dialog = MachineManagerDialog(runtime)
    dialog._reload_list(created.id)
    dialog._set_active_selected()

    assert runtime.machine_registry.active_machine_id == created.id
    assert runtime.running_machine_id != created.id
    dialog.close()


def test_profile_identity_change_does_not_overwrite_concrete_settings(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    before = runtime.machine_registry.active_machine
    dialog = MachineManagerDialog(runtime)

    ender_index = dialog.machine_profile.findData("ender-3-s1-pro")
    diode_index = dialog.tool_head_profile.findData("generic-diode-10w")
    assert ender_index >= 0
    assert diode_index >= 0

    dialog.machine_profile.setCurrentIndex(ender_index)
    dialog.tool_head_profile.setCurrentIndex(diode_index)

    assert "does not change the current settings below" in dialog.machine_profile_info.text()
    assert "does not change power, feeds, offsets" in dialog.tool_head_profile_info.text()
    assert dialog.port.text() == before.machine.port
    assert dialog.x_min.value() == before.machine.work_area.x_min
    assert dialog.power_max.value() == before.laser.power_max

    assert dialog._save_selected() is True
    saved = runtime.machine_registry.active_machine
    assert saved.machine_profile_id == "ender-3-s1-pro"
    assert saved.tool_head_profile_id == "generic-diode-10w"
    assert saved.machine.port == before.machine.port
    assert saved.machine.work_area == before.machine.work_area
    assert saved.laser.power_max == before.laser.power_max
    dialog.close()


def test_apply_machine_profile_defaults_is_explicit_and_does_not_touch_laser(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    before = runtime.machine_registry.active_machine
    dialog = MachineManagerDialog(runtime)
    ender_index = dialog.machine_profile.findData("ender-3-s1-pro")
    dialog.machine_profile.setCurrentIndex(ender_index)

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    dialog._apply_machine_profile_defaults()

    assert dialog.max_travel_feed.value() == 3000.0
    assert dialog.port.text() == "SELECT_CONTROLLER_PORT"
    assert dialog.power_max.value() == before.laser.power_max
    unchanged = runtime.machine_registry.active_machine
    assert unchanged.machine.port == before.machine.port
    assert (
        unchanged.machine.max_travel_feed_mm_min
        == before.machine.max_travel_feed_mm_min
    )
    dialog.close()


def test_machine_manager_profile_help_explains_what_e3_uses(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineManagerDialog(runtime)

    authority = dialog.findChild(
        QtWidgets.QLabel, "profileAuthorityExplanation"
    )
    assert authority is not None
    assert "concrete settings shown below are what E3 actually uses" in authority.text()
    assert dialog.machine_list.parentWidget().minimumWidth() >= 360
    dialog.close()
