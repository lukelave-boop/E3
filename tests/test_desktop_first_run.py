from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.desktop import first_run as first_run_module
from laser_aligner.desktop.first_run import FirstRunWizard


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


def test_first_run_offers_simulator_and_all_hardware_machine_profiles(
    qt_application: QtWidgets.QApplication,
) -> None:
    wizard = FirstRunWizard(Path("config/default.json"))
    try:
        assert _profile_ids(wizard.profile.machine_profile) == (
            "simulator",
            "custom-machine",
            "ender-3-s1-pro",
            "generic-grbl",
            "generic-marlin",
        )
        assert wizard.profile.machine_profile.currentData() == "simulator"
        assert _profile_ids(wizard.profile.tool_head_profile) == (
            "simulated-laser-head",
        )
        assert not wizard.profile.tool_head_profile.isEnabled()
        assert wizard.profile.nextId() == first_run_module._FINISH
        assert wizard.open_machine_setup is False

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


def test_simulator_accept_saves_only_safe_profile_selection_without_io(
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
        return tmp_path / "simulator.json"

    monkeypatch.setattr(
        first_run_module,
        "save_profile_setup",
        save_profile_setup,
    )
    monkeypatch.setattr(
        first_run_module.socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail(
            "accept must not probe a network endpoint"
        ),
    )
    wizard = FirstRunWizard(Path("config/default.json"))
    wizard.profile.machine_name.setText("Design simulator")
    try:
        wizard.finish.initializePage()
        assert "No hardware endpoint" in wizard.finish.message.text()
        wizard.accept()

        assert calls == [
            {
                "template": Path("config/default.json"),
                "machine_name": "Design simulator",
                "machine_profile_id": "simulator",
                "tool_head_profile_id": "simulated-laser-head",
            }
        ]
        assert wizard.saved_config == tmp_path / "simulator.json"
        assert wizard.result() == QtWidgets.QDialog.DialogCode.Accepted
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
