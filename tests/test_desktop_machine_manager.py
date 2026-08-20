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
from laser_aligner.desktop.machine_manager import (
    MachineManagerDialog,
    _NewMachineDialog,
)
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
    running = registry.active_machine
    return SimpleNamespace(
        settings=settings,
        machine_registry=registry,
        running_machine_id=running.id,
        context=SimpleNamespace(
            machine_identity=SimpleNamespace(
                machine_id=running.id,
                machine_name=running.name,
                machine_profile_id=running.machine_profile_id,
                tool_head_profile_id=running.tool_head_profile_id,
            )
        ),
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
    assert dialog.camera_binding.text() == expected
    assert dialog.calibration_binding.text() == expected
    assert "Running now" in dialog.lifecycle_summary.text()
    assert "Use on next launch" in dialog.lifecycle_summary.text()

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
    dialog.honeycomb_span.setText("189.5")

    assert dialog._save_selected() is True

    saved = runtime.machine_registry.active_machine
    assert saved.name == "Home Ender laser"
    assert saved.machine.port == "e3bridge://home-pi:8765"
    assert saved.machine.allow_motion is True
    assert saved.machine.honeycomb_span_mm == pytest.approx(189.5)
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
    before.machine.honeycomb_span_mm = 190.25
    runtime.machine_registry.update_machine(before)
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
    assert dialog.honeycomb_span.text() == "190.25"

    assert dialog._save_selected() is True
    saved = runtime.machine_registry.active_machine
    assert saved.machine_profile_id == "ender-3-s1-pro"
    assert saved.tool_head_profile_id == "generic-diode-10w"
    assert saved.machine.port == before.machine.port
    assert saved.machine.work_area == before.machine.work_area
    assert saved.laser.power_max == before.laser.power_max
    assert saved.machine.honeycomb_span_mm == pytest.approx(190.25)
    dialog.close()


def test_apply_machine_profile_defaults_is_explicit_and_does_not_touch_laser(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    before = runtime.machine_registry.active_machine
    before.machine.honeycomb_span_mm = 191.0
    runtime.machine_registry.update_machine(before)
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
    assert dialog.honeycomb_span.text() == "191"
    unchanged = runtime.machine_registry.active_machine
    assert unchanged.machine.port == before.machine.port
    assert (
        unchanged.machine.max_travel_feed_mm_min
        == before.machine.max_travel_feed_mm_min
    )
    assert unchanged.machine.honeycomb_span_mm == pytest.approx(191.0)

    assert dialog._save_selected() is True
    saved = runtime.machine_registry.active_machine
    assert saved.machine.port == "SELECT_CONTROLLER_PORT"
    assert saved.machine.max_travel_feed_mm_min == pytest.approx(3000.0)
    assert saved.machine.honeycomb_span_mm == pytest.approx(191.0)
    assert saved.laser.power_max == before.laser.power_max
    dialog.close()


def test_manager_can_clear_physical_honeycomb_span(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    machine = runtime.machine_registry.active_machine
    machine.machine.honeycomb_span_mm = 191.0
    runtime.machine_registry.update_machine(machine)
    dialog = MachineManagerDialog(runtime)

    assert dialog.honeycomb_span.text() == "191"
    assert "Not configured" in dialog.honeycomb_span.placeholderText()
    dialog.honeycomb_span.clear()

    assert dialog._save_selected() is True
    assert runtime.machine_registry.active_machine.machine.honeycomb_span_mm is None
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


def test_new_machine_dialog_starts_with_safe_simulator_profiles(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = _NewMachineDialog(runtime.machine_registry)
    qt_application.processEvents()

    assert dialog.machine_profile.currentData() == "simulator"
    assert dialog.tool_head_profile.currentData() == "simulated-laser-head"
    assert "simulator backend" in dialog.machine_profile_info.text()
    assert "copied as the starting settings" in dialog.machine_profile_info.text()

    dialog.close()


def test_new_machine_dialog_keeps_simulated_and_physical_profiles_separate(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = _NewMachineDialog(runtime.machine_registry)

    assert dialog.machine_profile.currentData() == "simulator"
    assert dialog.tool_head_profile.count() == 1
    assert dialog.tool_head_profile.currentData() == "simulated-laser-head"

    dialog.machine_profile.setCurrentIndex(
        dialog.machine_profile.findData("generic-marlin")
    )
    qt_application.processEvents()

    physical_ids = {
        dialog.tool_head_profile.itemData(index)
        for index in range(dialog.tool_head_profile.count())
    }
    assert physical_ids == {"custom-laser-head", "generic-diode-10w"}
    assert dialog.tool_head_profile.currentData() == "custom-laser-head"

    dialog.machine_profile.setCurrentIndex(
        dialog.machine_profile.findData("simulator")
    )
    qt_application.processEvents()
    assert dialog.tool_head_profile.count() == 1
    assert dialog.tool_head_profile.currentData() == "simulated-laser-head"
    dialog.close()


@pytest.mark.parametrize(
    ("machine_profile_id", "tool_head_profile_id"),
    (
        ("generic-grbl", "simulated-laser-head"),
        ("simulator", "generic-diode-10w"),
    ),
)
def test_manager_rejects_programmatic_incompatible_profile_pair(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    machine_profile_id: str,
    tool_head_profile_id: str,
) -> None:
    runtime = _runtime(tmp_path)
    before = [machine.to_dict() for machine in runtime.machine_registry.machines()]
    warnings: list[str] = []
    monkeypatch.setattr(
        _NewMachineDialog,
        "exec",
        lambda _self: QtWidgets.QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        _NewMachineDialog,
        "values",
        lambda _self: (
            "Invalid profile pair",
            machine_profile_id,
            tool_head_profile_id,
        ),
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)),
    )
    dialog = MachineManagerDialog(runtime)

    dialog._add_machine()

    assert [
        machine.to_dict() for machine in runtime.machine_registry.machines()
    ] == before
    assert warnings
    assert "simulated laser head" in warnings[-1].casefold()
    dialog.close()


def test_manager_running_label_uses_immutable_runtime_name(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    immutable_name = runtime.context.machine_identity.machine_name
    saved = runtime.machine_registry.get_machine(runtime.running_machine_id)
    saved.name = "Renamed for next launch"
    runtime.machine_registry.update_machine(saved)

    dialog = MachineManagerDialog(runtime)

    assert immutable_name in dialog.lifecycle_summary.text()
    assert "Renamed for next launch" in dialog.lifecycle_summary.text()
    assert runtime.context.machine_identity.machine_name == immutable_name
    dialog.close()


def test_manager_connection_fields_follow_backend_and_protocol(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineManagerDialog(runtime)
    qt_application.processEvents()

    assert dialog.backend.currentData() == "serial"
    assert not dialog.port.isHidden()
    assert not dialog.baudrate.isHidden()
    assert dialog.protocol.currentData() == "grbl"
    assert not dialog.grbl_idle_delay.isHidden()
    assert dialog.grbl_idle_delay.maximum() == 254

    dialog.protocol.setCurrentIndex(dialog.protocol.findData("marlin"))
    assert dialog.grbl_idle_delay.isHidden()

    dialog.protocol.setCurrentIndex(dialog.protocol.findData("auto"))
    assert not dialog.grbl_idle_delay.isHidden()
    assert "if detected" in dialog.grbl_idle_label.text()

    dialog.backend.setCurrentIndex(dialog.backend.findData("simulator"))
    assert dialog.port.isHidden()
    assert dialog.baudrate.isHidden()
    assert not dialog.protocol.isHidden()

    dialog.close()


def test_manager_profile_defaults_update_backend_and_keep_motion_disabled(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineManagerDialog(runtime)
    simulator_index = dialog.machine_profile.findData("simulator")
    dialog.machine_profile.setCurrentIndex(simulator_index)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )

    dialog._apply_machine_profile_defaults()

    assert dialog.backend.currentData() == "simulator"
    assert dialog.protocol.currentData() == "auto"
    assert dialog.port.text() == "simulator"
    assert dialog.port.isHidden()
    assert not dialog.allow_motion.isChecked()
    assert runtime.machine_registry.active_machine.machine.backend == "serial"
    assert runtime.machine_registry.active_machine.machine.allow_motion is True

    dialog.close()


def test_manager_add_from_profiles_is_safe_unbound_and_does_not_change_running(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    running_id = runtime.running_machine_id
    active_id = runtime.machine_registry.active_machine_id

    class ForbiddenActions:
        def __init__(self, machine_identity: object | None = None) -> None:
            if machine_identity is not None:
                self.machine_identity = machine_identity

        def __getattr__(self, name: str):
            def fail(*_args, **_kwargs) -> None:
                pytest.fail(f"Machine/controller action was invoked: {name}")

            return fail

    runtime.context = ForbiddenActions(runtime.context.machine_identity)
    runtime.controller = ForbiddenActions()
    monkeypatch.setattr(
        _NewMachineDialog,
        "exec",
        lambda _self: QtWidgets.QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        _NewMachineDialog,
        "values",
        lambda _self: (
            "Safe GRBL profile",
            "generic-grbl",
            "generic-diode-10w",
        ),
    )
    dialog = MachineManagerDialog(runtime)

    dialog._add_machine()
    qt_application.processEvents()

    created = next(
        machine
        for machine in runtime.machine_registry.machines()
        if machine.name == "Safe GRBL profile"
    )
    assert created.machine_profile_id == "generic-grbl"
    assert created.tool_head_profile_id == "generic-diode-10w"
    assert created.machine.backend == "serial"
    assert created.machine.protocol == "grbl"
    assert created.machine.allow_motion is False
    assert created.laser.default_power == 0
    assert created.laser.frame_power == 0
    assert created.laser.allow_low_power_frame is False
    assert created.camera_profile_id is None
    assert created.calibration_profile_id is None
    assert created.machine.honeycomb_span_mm is None
    assert runtime.running_machine_id == running_id
    assert runtime.machine_registry.active_machine_id == active_id
    assert dialog.camera_binding.text() == "Not configured"
    assert dialog.calibration_binding.text() == "Not configured"

    dialog.name.setText("Edited safe GRBL profile")
    assert dialog._save_selected() is True
    dialog._set_active_selected()
    assert runtime.running_machine_id == running_id
    assert runtime.machine_registry.active_machine_id == created.id

    dialog.close()


def test_manager_duplicate_clears_machine_specific_bindings(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    source = runtime.machine_registry.active_machine
    source.camera_profile_id = "camera-binding"
    source.calibration_profile_id = "calibration-binding"
    source.machine.honeycomb_span_mm = 191.0
    source.laser.guarded_output_polygon_mm = (
        (1.0, 1.0),
        (10.0, 1.0),
        (10.0, 10.0),
        (1.0, 10.0),
    )
    runtime.machine_registry.update_machine(source)
    dialog = MachineManagerDialog(runtime)

    dialog._duplicate_machine()
    qt_application.processEvents()

    duplicated = next(
        machine
        for machine in runtime.machine_registry.machines()
        if machine.created_from == f"duplicate:{source.id}"
    )
    assert duplicated.camera_profile_id is None
    assert duplicated.calibration_profile_id is None
    assert duplicated.machine.honeycomb_span_mm is None
    assert (
        duplicated.laser.guarded_output_polygon_mm
        == source.laser.guarded_output_polygon_mm
    )
    assert duplicated.machine.port == source.machine.port
    assert duplicated.id != source.id

    dialog.close()


def test_manager_saves_backend_and_explicit_motion_permission_across_reopen(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineManagerDialog(runtime)
    dialog.backend.setCurrentIndex(dialog.backend.findData("simulator"))
    dialog.protocol.setCurrentIndex(dialog.protocol.findData("marlin"))
    dialog.allow_motion.setChecked(False)

    assert dialog._save_selected() is True
    dialog.close()

    saved = runtime.machine_registry.active_machine
    assert saved.machine.backend == "simulator"
    assert saved.machine.protocol == "marlin"
    assert saved.machine.allow_motion is False

    reopened = MachineManagerDialog(runtime)
    qt_application.processEvents()
    assert reopened.backend.currentData() == "simulator"
    assert reopened.protocol.currentData() == "marlin"
    assert not reopened.allow_motion.isChecked()
    assert reopened.port.isHidden()
    assert reopened.grbl_idle_delay.isHidden()
    reopened.close()


@pytest.mark.parametrize(
    ("endpoint", "message"),
    (
        (
            "e3bridge://name:secret@controller.local:8765",
            "must not be embedded",
        ),
        ("e3bridge://controller.local:8765/path", "only a host"),
        ("e3bridge://controller.local:8765?mode=test", "only a host"),
        ("e3bridge://:8765", "include a host"),
        ("e3bridge://controller.local:70000", "port out of range"),
    ),
)
def test_manager_rejects_malformed_bridge_before_registry_mutation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    message: str,
) -> None:
    runtime = _runtime(tmp_path)
    before = runtime.machine_registry.active_machine.to_dict()
    dialog = MachineManagerDialog(runtime)
    warnings: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, text: warnings.append(str(text)),
    )
    monkeypatch.setattr(
        runtime.machine_registry,
        "update_machine",
        lambda *_args, **_kwargs: pytest.fail(
            "Malformed bridge endpoint reached registry mutation"
        ),
    )
    dialog.backend.setCurrentIndex(dialog.backend.findData("serial"))
    dialog.port.setText(endpoint)

    assert dialog._save_selected() is False

    assert runtime.machine_registry.active_machine.to_dict() == before
    assert warnings
    assert message in warnings[-1].lower()
    dialog.close()


@pytest.mark.parametrize(
    "endpoint",
    (
        "COM17",
        "/dev/ttyUSB0",
        "SELECT_CONTROLLER_PORT",
    ),
)
def test_manager_preserves_local_serial_endpoint_behavior(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    endpoint: str,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineManagerDialog(runtime)
    dialog.backend.setCurrentIndex(dialog.backend.findData("serial"))
    dialog.port.setText(endpoint)

    assert dialog._save_selected() is True

    assert runtime.machine_registry.active_machine.machine.port == endpoint
    dialog.close()


def test_manager_rejects_invalid_work_area_before_registry_mutation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    before = runtime.machine_registry.active_machine.to_dict()
    dialog = MachineManagerDialog(runtime)
    warnings: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda _parent, _title, message: warnings.append(str(message)),
    )
    dialog.x_min.setValue(200.0)
    dialog.x_max.setValue(100.0)

    assert dialog._save_selected() is False

    assert runtime.machine_registry.active_machine.to_dict() == before
    assert warnings
    assert "work_area" in warnings[-1].lower()
    dialog.close()
