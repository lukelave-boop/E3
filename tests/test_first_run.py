from __future__ import annotations

import json
from pathlib import Path

import pytest

from laser_aligner import first_run as first_run_module
from laser_aligner.config import load_settings
from laser_aligner.errors import MachineError
from laser_aligner.first_run import (
    build_hardware_config,
    build_simulator_config,
    save_hardware_setup,
    save_profile_setup,
)
from laser_aligner.machine.profiles import (
    MACHINE_REGISTRY_SCHEMA_VERSION,
    MachineRegistry,
    MachineRegistryError,
)
from laser_aligner.storage import read_json


def _assert_no_first_run_persistence(root: Path) -> None:
    assert not (root / "config" / "network-local.json").exists()
    assert not (root / "data" / "machines.json").exists()
    assert not (root / "secrets" / "bridge-token.txt").exists()
    assert not (root / "first-run.json").exists()


def _first_run_snapshot(root: Path) -> dict[Path, bytes | None]:
    paths = (
        root / "config" / "network-local.json",
        root / "data" / "machines.json",
        root / "secrets" / "bridge-token.txt",
        root / "first-run.json",
    )
    return {
        path: path.read_bytes() if path.exists() else None
        for path in paths
    }


def _save_hardware_variant(*, token: str, name: str) -> Path:
    return save_hardware_setup(
        Path("config/default.json"),
        bridge_token=token,
        machine_name=name,
        host="controller.local",
        controller_port=8765,
        camera_port=8766,
        width_mm=300.0,
        height_mm=200.0,
    )


def test_build_hardware_config_uses_network_bridges() -> None:
    payload = build_hardware_config(
        Path("config/default.json"),
        host="10.0.0.42",
        controller_port=8765,
        camera_port=8766,
        width_mm=300.0,
        height_mm=200.0,
        camera_width=1920,
        camera_height=1080,
        autofocus=False,
        focus_value=25,
        machine_profile_id="generic-marlin",
        tool_head_profile_id="generic-diode-10w",
    )
    assert payload["app"]["simulation"] is False
    assert payload["app"]["data_dir"] == "../data"
    assert payload["camera"]["device"] == "e3camera://10.0.0.42:8766"
    assert payload["camera"]["controls"]["focus_auto"] == 0
    assert payload["camera"]["controls"]["focus_absolute"] == 25
    assert payload["machine"]["backend"] == "serial"
    assert payload["machine"]["protocol"] == "marlin"
    assert payload["machine"]["port"] == "e3bridge://10.0.0.42:8765"
    assert payload["machine"]["work_area"]["x_max"] == 300.0
    assert payload["machine"]["work_area"]["y_max"] == 200.0
    assert payload["machine"]["photo_position"]["x"] == 150.0
    assert payload["machine"]["photo_position"]["y"] == 100.0
    assert payload["machine"]["allow_motion"] is False
    assert payload["laser"]["default_power"] == 0
    assert payload["laser"]["frame_power"] == 0
    assert payload["laser"]["allow_low_power_frame"] is False


def test_build_simulator_config_is_safe_and_uses_no_hardware_endpoint() -> None:
    payload = build_simulator_config(Path("config/default.json"))

    assert payload["app"]["simulation"] is True
    assert payload["machine"]["backend"] == "simulator"
    assert payload["machine"]["port"] == "simulator"
    assert payload["machine"]["allow_motion"] is False
    assert payload["laser"]["default_power"] == 0
    assert payload["laser"]["frame_power"] == 0
    assert payload["laser"]["allow_low_power_frame"] is False


def test_save_hardware_setup_is_user_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    config_path = save_hardware_setup(
        Path("config/default.json"),
        bridge_token="x" * 32,
        host="10.0.0.42",
        controller_port=8765,
        camera_port=8766,
        width_mm=220.0,
        height_mm=220.0,
    )
    assert config_path == tmp_path / "config" / "network-local.json"
    assert config_path.is_file()
    assert (tmp_path / "secrets" / "bridge-token.txt").read_text() == "x" * 32
    state = read_json(tmp_path / "first-run.json")
    assert state["configured"] is True
    assert state["deferred"] is False

    settings = load_settings(config_path)
    registry = MachineRegistry.load_or_migrate(settings)
    saved = registry.active_machine
    registry_payload = json.loads(registry.path.read_text(encoding="utf-8"))
    assert registry_payload["schema_version"] == MACHINE_REGISTRY_SCHEMA_VERSION
    assert len(registry.machines()) == 1
    assert saved.name == "Network laser"
    assert saved.created_from == "profile"
    assert saved.machine_profile_id == "generic-grbl"
    assert saved.tool_head_profile_id == "custom-laser-head"
    assert saved.machine.port == "e3bridge://10.0.0.42:8765"
    assert saved.machine.allow_motion is False
    assert saved.laser.default_power == 0
    assert saved.laser.frame_power == 0
    assert saved.laser.allow_low_power_frame is False
    assert saved.camera_profile_id is None
    assert saved.calibration_profile_id is None


def test_save_simulator_profile_needs_no_bridge_secret_and_selects_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))

    config_path = save_profile_setup(
        Path("config/default.json"),
        machine_name="Safe simulator",
        machine_profile_id="simulator",
        tool_head_profile_id="simulated-laser-head",
    )

    settings = load_settings(config_path)
    registry = MachineRegistry.load_or_migrate(settings)
    saved = registry.active_machine
    assert settings.app.simulation is True
    assert settings.machine.backend == "simulator"
    assert not (tmp_path / "secrets" / "bridge-token.txt").exists()
    assert len(registry.machines()) == 1
    assert saved.name == "Safe simulator"
    assert saved.created_from == "profile"
    assert saved.machine_profile_id == "simulator"
    assert saved.tool_head_profile_id == "simulated-laser-head"
    assert saved.machine.allow_motion is False
    assert saved.laser.default_power == 0
    assert saved.camera_profile_id is None
    assert saved.calibration_profile_id is None


def test_invalid_machine_name_leaves_existing_first_run_state_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    config_path = save_hardware_setup(
        Path("config/default.json"),
        bridge_token="a" * 32,
        machine_name="Existing hardware",
        host="controller.local",
        controller_port=8765,
        camera_port=8766,
        width_mm=220.0,
        height_mm=220.0,
    )
    persisted_paths = (
        config_path,
        tmp_path / "data" / "machines.json",
        tmp_path / "secrets" / "bridge-token.txt",
        tmp_path / "first-run.json",
    )
    before = {path: path.read_bytes() for path in persisted_paths}

    with pytest.raises(MachineRegistryError, match="120 characters or fewer"):
        save_hardware_setup(
            Path("config/default.json"),
            bridge_token="b" * 32,
            machine_name="x" * 121,
            host="replacement.local",
            controller_port=8765,
            camera_port=8766,
            width_mm=300.0,
            height_mm=200.0,
        )

    assert {path: path.read_bytes() for path in persisted_paths} == before
    assert not any(
        ".validate-" in path.name
        for path in (tmp_path / "config").iterdir()
    )


def test_invalid_final_settings_leave_all_first_run_domains_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="Out of range float"):
        save_hardware_setup(
            Path("config/default.json"),
            bridge_token="x" * 32,
            host="controller.local",
            controller_port=8765,
            camera_port=8766,
            width_mm=float("nan"),
            height_mm=220.0,
        )

    _assert_no_first_run_persistence(tmp_path)


def test_malformed_constructed_bridge_uri_is_rejected_before_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))

    with pytest.raises(MachineError, match="only a host and optional port"):
        save_hardware_setup(
            Path("config/default.json"),
            bridge_token="x" * 32,
            host="controller.local/extra-path",
            controller_port=8765,
            camera_port=8766,
            width_mm=220.0,
            height_mm=220.0,
        )

    _assert_no_first_run_persistence(tmp_path)


@pytest.mark.parametrize("preexisting", [False, True])
@pytest.mark.parametrize("failure_stage", ["registry", "token", "state"])
def test_post_validation_write_failure_restores_every_persistence_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    preexisting: bool,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    if preexisting:
        _save_hardware_variant(token="a" * 32, name="Existing hardware")
    before = _first_run_snapshot(tmp_path)

    def injected_failure(*_args: object, **_kwargs: object) -> None:
        raise OSError(f"injected {failure_stage} write failure")

    if failure_stage == "registry":
        monkeypatch.setattr(MachineRegistry, "save", injected_failure)
    elif failure_stage == "token":
        monkeypatch.setattr(
            first_run_module,
            "atomic_write_text",
            injected_failure,
        )
    else:
        monkeypatch.setattr(
            first_run_module,
            "mark_setup_complete",
            injected_failure,
        )

    with pytest.raises(
        OSError,
        match=f"injected {failure_stage} write failure",
    ):
        _save_hardware_variant(token="b" * 32, name="Replacement hardware")

    assert _first_run_snapshot(tmp_path) == before
    assert not any(
        ".validate-" in path.name
        for path in (tmp_path / "config").iterdir()
    )


def test_rollback_failure_reports_it_and_chains_original_write_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    _save_hardware_variant(token="a" * 32, name="Existing hardware")

    def state_failure(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected state write failure")

    def rollback_failure(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected rollback failure")

    monkeypatch.setattr(
        first_run_module,
        "mark_setup_complete",
        state_failure,
    )
    monkeypatch.setattr(
        first_run_module,
        "atomic_write_bytes",
        rollback_failure,
    )

    with pytest.raises(RuntimeError, match="rollback was incomplete") as raised:
        _save_hardware_variant(token="b" * 32, name="Replacement hardware")

    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "injected state write failure"
    assert "injected rollback failure" in str(raised.value)
