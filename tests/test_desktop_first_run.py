from __future__ import annotations

import copy
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.desktop import first_run as first_run_module
from laser_aligner.desktop.first_run import FirstRunWizard
from laser_aligner.first_run import SimulatorRecoveryPlan
from laser_aligner.machine.profiles import (
    MachineInstance,
    builtin_machine_profiles,
    builtin_tool_head_profiles,
)


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
        []
    )
    yield application
    application.processEvents()


def _profile_ids(combo: QtWidgets.QComboBox) -> tuple[str, ...]:
    return tuple(str(combo.itemData(index)) for index in range(combo.count()))


def _dispose(
    application: QtWidgets.QApplication,
    wizard: FirstRunWizard,
) -> None:
    wizard.close()
    wizard.deleteLater()
    application.processEvents()


def _recovery_plan(
    tmp_path: Path,
    *,
    include_physical: bool,
) -> SimulatorRecoveryPlan:
    physical: tuple[MachineInstance, ...] = ()
    if include_physical:
        machine_profile = builtin_machine_profiles()["generic-grbl"]
        tool_profile = builtin_tool_head_profiles()["custom-laser-head"]
        machine = copy.deepcopy(machine_profile.machine_defaults)
        machine.port = "e3bridge://existing.local:8765"
        physical = (
            MachineInstance(
                id="existing-physical",
                name="Existing physical",
                machine_profile_id=machine_profile.id,
                tool_head_profile_id=tool_profile.id,
                machine=machine,
                laser=tool_profile.laser_defaults,
                created_from="profile",
            ),
        )
    return SimulatorRecoveryPlan(
        source_config_path=tmp_path / "legacy.json",
        source_config_bytes=b"{}",
        replacement_config_path=tmp_path / "legacy.json",
        replacement_config_bytes=b"{}",
        data_dir=tmp_path / "data",
        registry_path=tmp_path / "data" / "machines.json",
        registry_bytes=b"legacy registry",
        physical_machines=physical,
        simulator_machine_ids=("legacy-simulator",),
        original_active_machine_id="legacy-simulator",
        config_simulation_enabled=True,
        config_simulator_backend=True,
    )


def test_first_run_offers_only_physical_machine_profiles(
    qt_application: QtWidgets.QApplication,
) -> None:
    wizard = FirstRunWizard(Path("config/default.json"))
    try:
        assert _profile_ids(wizard.profile.machine_profile) == (
            "custom-machine",
            "ender-3-s1-pro",
            "generic-grbl",
            "generic-marlin",
        )
        assert "simulator" not in _profile_ids(wizard.profile.machine_profile)
        assert _profile_ids(wizard.profile.tool_head_profile) == (
            "custom-laser-head",
            "generic-diode-10w",
        )
        assert wizard.profile.tool_head_profile.isEnabled()
        assert wizard.profile.nextId() == first_run_module._CONNECTION
        assert wizard.open_machine_setup is True

        marlin = wizard.profile.machine_profile.findData("generic-marlin")
        wizard.profile.machine_profile.setCurrentIndex(marlin)
        qt_application.processEvents()

        assert wizard.profile.nextId() == first_run_module._CONNECTION
        assert wizard.profile.tool_head_profile.isEnabled()
        assert _profile_ids(wizard.profile.tool_head_profile) == (
            "custom-laser-head",
            "generic-diode-10w",
        )
        assert wizard.open_machine_setup is True
        wizard.machine.initializePage()
        assert wizard.machine.width.value() == 220.0
        assert wizard.machine.height.value() == 220.0
    finally:
        _dispose(qt_application, wizard)


def test_hardware_accept_saves_selected_profiles_without_controller_actions(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def save_profile_setup(
        template: Path,
        **options: object,
    ) -> Path:
        calls.append({"template": template, **options})
        return tmp_path / "hardware.json"

    monkeypatch.setattr(
        first_run_module,
        "save_profile_setup",
        save_profile_setup,
    )
    monkeypatch.setattr(
        first_run_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "saving must not probe or connect to hardware"
        ),
    )
    wizard = FirstRunWizard(Path("config/default.json"))
    machine_index = wizard.profile.machine_profile.findData("generic-marlin")
    wizard.profile.machine_profile.setCurrentIndex(machine_index)
    tool_index = wizard.profile.tool_head_profile.findData(
        "generic-diode-10w"
    )
    wizard.profile.tool_head_profile.setCurrentIndex(tool_index)
    wizard.profile.machine_name.setText("Workshop Marlin")
    wizard.connection.host.setText("e3-pi.local")
    wizard.connection.token.setText("z" * 32)
    wizard.machine.initializePage()
    try:
        wizard.finish.initializePage()
        finish_text = wizard.finish.message.text().lower()
        assert "motion and laser output remain disabled" in finish_text
        assert "no connection" in finish_text
        assert "physical verification" in finish_text
        wizard.accept()

        assert len(calls) == 1
        options = calls[0]
        assert options["machine_name"] == "Workshop Marlin"
        assert options["machine_profile_id"] == "generic-marlin"
        assert options["tool_head_profile_id"] == "generic-diode-10w"
        assert options["host"] == "e3-pi.local"
        assert options["bridge_token"] == "z" * 32
        assert options["controller_port"] == 8765
        assert options["camera_port"] == 8766
        assert options["width_mm"] == 220.0
        assert options["height_mm"] == 220.0
        assert wizard.saved_config == tmp_path / "hardware.json"
    finally:
        _dispose(qt_application, wizard)


def test_recovery_starts_unselected_and_explicit_existing_choice_skips_setup(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = _recovery_plan(tmp_path, include_physical=True)
    calls: list[tuple[SimulatorRecoveryPlan, str]] = []

    def save_selection(
        received: SimulatorRecoveryPlan,
        machine_id: str,
    ) -> Path:
        calls.append((received, machine_id))
        return tmp_path / "configured.json"

    monkeypatch.setattr(
        first_run_module,
        "save_simulator_recovery_selection",
        save_selection,
    )
    monkeypatch.setattr(
        first_run_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "selecting a saved machine must not probe hardware"
        ),
    )
    wizard = FirstRunWizard(
        recovery.source_config_path,
        recovery=recovery,
    )
    try:
        assert wizard.welcome.nextId() == first_run_module._RECOVERY_CHOICE
        assert wizard.recovery_choice is not None
        assert wizard.recovery_choice.choice.currentData() is None
        assert wizard.recovery_choice.selected_machine_id is None
        assert wizard.recovery_choice.isComplete() is False
        index = wizard.recovery_choice.choice.findData("existing-physical")
        assert index > 0
        wizard.recovery_choice.choice.setCurrentIndex(index)
        qt_application.processEvents()
        assert wizard.recovery_choice.nextId() == first_run_module._FINISH
        wizard.finish.initializePage()
        assert "explicitly select Existing physical" in wizard.finish.message.text()

        wizard.accept()

        assert calls == [(recovery, "existing-physical")]
        assert wizard.saved_config == tmp_path / "configured.json"
    finally:
        _dispose(qt_application, wizard)


def test_simulator_only_recovery_requires_explicit_new_machine_choice(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = _recovery_plan(tmp_path, include_physical=False)
    wizard = FirstRunWizard(
        recovery.source_config_path,
        recovery=recovery,
    )
    try:
        assert wizard.recovery_choice is not None
        assert wizard.recovery_choice.choice.count() == 2
        assert wizard.recovery_choice.isComplete() is False
        create_index = wizard.recovery_choice.choice.findData(
            first_run_module._CREATE_NEW_MACHINE
        )
        wizard.recovery_choice.choice.setCurrentIndex(create_index)
        qt_application.processEvents()
        assert wizard.recovery_choice.creating_new_machine is True
        assert wizard.recovery_choice.nextId() == first_run_module._PROFILE
        assert wizard.connection.test_button.isHidden() is True
        monkeypatch.setattr(
            wizard.connection,
            "_reachable",
            lambda *_args: pytest.fail(
                "simulator recovery must not probe hardware before Finish"
            ),
        )
        wizard.connection.host.setText("replacement.local")
        wizard.connection._test_connection()
        assert "disabled until simulator recovery" in (
            wizard.connection.status.text()
        )
    finally:
        _dispose(qt_application, wizard)
