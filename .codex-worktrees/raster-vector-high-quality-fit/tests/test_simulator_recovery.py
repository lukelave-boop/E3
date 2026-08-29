from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from laser_aligner import first_run as first_run_module
from laser_aligner.config import load_settings
from laser_aligner.first_run import (
    inspect_simulator_recovery,
    save_profile_setup,
    save_simulator_recovery_selection,
)
from laser_aligner.machine.profiles import (
    REMOVED_SIMULATOR_BACKUP_SUFFIX,
    MachineRegistry,
)


def _write_config(root: Path, *, simulation: bool = False) -> Path:
    path = root / "config" / "network-local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    app: dict[str, object] = {
        "data_dir": "../data",
        "open_browser": False,
    }
    if simulation:
        app["simulation"] = True
    path.write_text(
        json.dumps(
            {
                "app": app,
                "camera": {"autostart": False},
                "machine": {
                    "backend": "serial",
                    "protocol": "grbl",
                    "port": "e3bridge://physical-controller:8765",
                    "allow_motion": False,
                },
                "laser": {
                    "default_power": 0,
                    "frame_power": 0,
                    "allow_low_power_frame": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _physical_registry(config_path: Path) -> MachineRegistry:
    return MachineRegistry.load_or_migrate(load_settings(config_path))


def _append_simulator(
    registry: MachineRegistry,
    *,
    active: bool,
    simulator_only: bool = False,
) -> tuple[bytes, dict[str, object] | None]:
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    physical = copy.deepcopy(payload["machines"][0])
    simulator = copy.deepcopy(physical)
    simulator["id"] = "legacy-simulator"
    simulator["name"] = "Legacy simulator"
    simulator["machine_profile_id"] = "simulator"
    simulator["tool_head_profile_id"] = "simulated-laser-head"
    simulator["camera_profile_id"] = "simulator-camera-evidence"
    simulator["calibration_profile_id"] = "simulator-calibration-evidence"
    simulator["machine"]["backend"] = "simulator"
    simulator["machine"]["port"] = "simulator"
    payload["machines"] = [simulator] if simulator_only else [physical, simulator]
    if active or simulator_only:
        payload["active_machine_id"] = simulator["id"]
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    registry.path.write_bytes(encoded)
    return encoded, None if simulator_only else physical


def _new_machine_options() -> dict[str, object]:
    return {
        "machine_name": "Legacy simulator",
        "machine_profile_id": "generic-grbl",
        "tool_head_profile_id": "custom-laser-head",
        "bridge_token": "r" * 32,
        "host": "replacement.local",
        "controller_port": 8765,
        "camera_port": 8766,
        "width_mm": 300.0,
        "height_mm": 200.0,
    }


def test_simulation_true_requires_recovery_even_with_active_physical_machine(
    tmp_path: Path,
) -> None:
    config = _write_config(tmp_path)
    registry = _physical_registry(config)
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["app"]["simulation"] = True
    config.write_text(json.dumps(payload), encoding="utf-8")

    recovery = inspect_simulator_recovery(config)

    assert recovery is not None
    assert recovery.config_simulation_enabled is True
    assert recovery.active_simulator is False
    assert [
        machine.id for machine in recovery.configured_physical_machines
    ] == [registry.active_machine_id]


def test_active_simulator_requires_explicit_physical_selection_and_preserves_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    config = _write_config(tmp_path, simulation=True)
    # Temporarily remove the legacy flag so the physical registry can be seeded.
    config_payload = json.loads(config.read_text(encoding="utf-8"))
    del config_payload["app"]["simulation"]
    config.write_text(json.dumps(config_payload), encoding="utf-8")
    registry = _physical_registry(config)
    registry_payload = json.loads(registry.path.read_text(encoding="utf-8"))
    registry_payload["machines"][0]["machine"]["read_timeout"] = 2
    registry.path.write_text(
        json.dumps(registry_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    original_registry, physical_before = _append_simulator(registry, active=True)
    config_payload["app"]["simulation"] = True
    config.write_text(json.dumps(config_payload), encoding="utf-8")
    recovery = inspect_simulator_recovery(config)
    assert recovery is not None
    assert recovery.active_simulator is True
    assert recovery.original_active_machine_id == "legacy-simulator"
    assert len(recovery.configured_physical_machines) == 1

    selected_id = recovery.configured_physical_machines[0].id
    saved_config = save_simulator_recovery_selection(recovery, selected_id)

    assert saved_config == config
    saved_payload = json.loads(saved_config.read_text(encoding="utf-8"))
    assert "simulation" not in saved_payload["app"]
    saved_registry_payload = json.loads(registry.path.read_text(encoding="utf-8"))
    assert saved_registry_payload["active_machine_id"] == selected_id
    assert [item["id"] for item in saved_registry_payload["machines"]] == [
        selected_id
    ]
    assert saved_registry_payload["machines"][0] == physical_before
    assert isinstance(
        saved_registry_payload["machines"][0]["machine"]["read_timeout"],
        int,
    )
    backup = registry.path.with_name(
        registry.path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
    )
    assert backup.read_bytes() == original_registry
    reloaded = MachineRegistry.load_or_migrate(load_settings(saved_config))
    assert reloaded.active_machine_id == selected_id


def test_explicit_config_recovery_replaces_only_the_inspected_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "unrelated-user-state"
    monkeypatch.setenv("E3_USER_STATE_DIR", str(state_root))
    config = _write_config(tmp_path / "explicit", simulation=True)
    payload = json.loads(config.read_text(encoding="utf-8"))
    del payload["app"]["simulation"]
    config.write_text(json.dumps(payload), encoding="utf-8")
    registry = _physical_registry(config)
    _append_simulator(registry, active=True)
    payload["app"]["simulation"] = True
    config.write_text(json.dumps(payload), encoding="utf-8")
    recovery = inspect_simulator_recovery(config)
    assert recovery is not None

    saved = save_simulator_recovery_selection(
        recovery,
        recovery.configured_physical_machines[0].id,
    )

    assert saved == config.resolve()
    assert "simulation" not in json.loads(config.read_text())["app"]
    assert not (state_root / "config" / "network-local.json").exists()


def test_legacy_launch_fallback_recovers_into_preserved_user_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "preserved-state"
    monkeypatch.setenv("E3_USER_STATE_DIR", str(state_root))
    source = _write_config(tmp_path / "replaceable-app", simulation=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    del payload["app"]["simulation"]
    source.write_text(json.dumps(payload), encoding="utf-8")
    registry = _physical_registry(source)
    _append_simulator(registry, active=True)
    payload["app"]["simulation"] = True
    source.write_text(json.dumps(payload), encoding="utf-8")
    original_source = source.read_bytes()
    preserved = state_root / "config" / "network-local.json"
    recovery = inspect_simulator_recovery(
        source,
        replacement_config_path=preserved,
    )
    assert recovery is not None

    saved = save_simulator_recovery_selection(
        recovery,
        recovery.configured_physical_machines[0].id,
    )

    assert saved == preserved.resolve()
    assert source.read_bytes() == original_source
    assert "simulation" not in json.loads(
        preserved.read_text(encoding="utf-8")
    )["app"]


def test_recovery_does_not_overwrite_config_changed_during_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    config = _write_config(tmp_path, simulation=True)
    payload = json.loads(config.read_text(encoding="utf-8"))
    del payload["app"]["simulation"]
    config.write_text(json.dumps(payload), encoding="utf-8")
    registry = _physical_registry(config)
    original_registry, _physical = _append_simulator(registry, active=True)
    payload["app"]["simulation"] = True
    config.write_text(json.dumps(payload), encoding="utf-8")
    recovery = inspect_simulator_recovery(config)
    assert recovery is not None
    concurrent_payload = copy.deepcopy(payload)
    concurrent_payload["app"]["open_browser"] = True
    concurrent_bytes = json.dumps(concurrent_payload).encode()
    original_validate = first_run_module._validate_settings_payload

    def validate_then_change(
        replacement: dict[str, object],
        config_path: Path,
    ) -> object:
        settings = original_validate(replacement, config_path)
        config.write_bytes(concurrent_bytes)
        return settings

    monkeypatch.setattr(
        first_run_module,
        "_validate_settings_payload",
        validate_then_change,
    )

    with pytest.raises(RuntimeError, match="changed during simulator recovery"):
        save_simulator_recovery_selection(
            recovery,
            recovery.configured_physical_machines[0].id,
        )

    assert config.read_bytes() == concurrent_bytes
    assert registry.path.read_bytes() == original_registry
    assert not registry.path.with_name(
        registry.path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
    ).exists()


def test_simulator_only_registry_recovers_to_new_unbound_physical_machine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    config = _write_config(tmp_path)
    registry = _physical_registry(config)
    original_registry, _physical = _append_simulator(
        registry,
        active=True,
        simulator_only=True,
    )
    recovery = inspect_simulator_recovery(config)
    assert recovery is not None
    assert recovery.configured_physical_machines == ()

    saved_config = save_profile_setup(
        config,
        simulator_recovery=recovery,
        **_new_machine_options(),
    )

    saved_payload = json.loads(saved_config.read_text(encoding="utf-8"))
    assert "simulation" not in saved_payload["app"]
    saved_registry = json.loads(registry.path.read_text(encoding="utf-8"))
    assert len(saved_registry["machines"]) == 1
    created = saved_registry["machines"][0]
    assert created["id"] == "legacy-simulator-2"
    assert created["machine_profile_id"] == "generic-grbl"
    assert created["machine"]["backend"] == "serial"
    assert created["machine"]["port"] == "e3bridge://replacement.local:8765"
    assert created["machine"]["allow_motion"] is False
    assert created["laser"]["default_power"] == 0
    assert created["camera_profile_id"] is None
    assert created["calibration_profile_id"] is None
    backup = registry.path.with_name(
        registry.path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
    )
    assert backup.read_bytes() == original_registry


def test_inactive_simulator_with_active_physical_uses_automatic_migration(
    tmp_path: Path,
) -> None:
    config = _write_config(tmp_path)
    registry = _physical_registry(config)
    _append_simulator(registry, active=False)

    assert inspect_simulator_recovery(config) is None

    migrated = MachineRegistry.load_or_migrate(load_settings(config))
    assert migrated.active_machine_id == "existing-machine"
    assert [machine.id for machine in migrated.machines()] == [
        "existing-machine"
    ]
    assert registry.path.with_name(
        registry.path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
    ).is_file()


@pytest.mark.parametrize("failure_stage", ["backup", "registry", "token", "state"])
def test_recovery_write_failure_restores_every_persistence_domain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    config = _write_config(tmp_path)
    registry = _physical_registry(config)
    _append_simulator(registry, active=True, simulator_only=True)
    recovery = inspect_simulator_recovery(config)
    assert recovery is not None
    backup = registry.path.with_name(
        registry.path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
    )
    paths = (
        config,
        registry.path,
        backup,
        tmp_path / "secrets" / "bridge-token.txt",
        tmp_path / "first-run.json",
    )
    before = {
        path: path.read_bytes() if path.exists() else None for path in paths
    }

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError(f"injected {failure_stage} failure")

    if failure_stage == "backup":
        monkeypatch.setattr(first_run_module, "atomic_write_bytes_if_absent", fail)
    elif failure_stage == "registry":
        original_atomic_write_json = first_run_module.atomic_write_json

        def fail_registry(path: Path, payload: object) -> None:
            if Path(path) == registry.path:
                fail()
            original_atomic_write_json(path, payload)

        monkeypatch.setattr(
            first_run_module,
            "atomic_write_json",
            fail_registry,
        )
    elif failure_stage == "token":
        monkeypatch.setattr(first_run_module, "atomic_write_text", fail)
    else:
        monkeypatch.setattr(first_run_module, "mark_setup_complete", fail)

    with pytest.raises(OSError, match=f"injected {failure_stage} failure"):
        save_profile_setup(
            config,
            simulator_recovery=recovery,
            **_new_machine_options(),
        )

    assert {
        path: path.read_bytes() if path.exists() else None for path in paths
    } == before
