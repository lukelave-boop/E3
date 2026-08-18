from __future__ import annotations

import json
from pathlib import Path

import pytest

from laser_aligner.config import (
    LaserSettings,
    MachineSettings,
    WorkArea,
    load_settings,
)
from laser_aligner.machine import profiles as profiles_module
from laser_aligner.machine.profiles import (
    MACHINE_REGISTRY_SCHEMA_VERSION,
    MachineInstance,
    MachineProfile,
    MachineRegistry,
    MachineRegistryError,
    ToolHeadProfile,
)


def _settings(tmp_path: Path, *, simulator: bool = False):
    config = tmp_path / "local.json"
    config.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "runtime",
                    "simulation": simulator,
                },
                "machine": {
                    "backend": "simulator" if simulator else "serial",
                    "protocol": "auto" if simulator else "grbl",
                    "port": (
                        "simulator"
                        if simulator
                        else "e3bridge://pi-controller:8765"
                    ),
                    "baudrate": 115200 if simulator else 250000,
                    "read_timeout": 3.5,
                    "work_area": {
                        "x_min": 10.0,
                        "x_max": 230.0,
                        "y_min": 20.0,
                        "y_max": 240.0,
                    },
                    "photo_position": {
                        "x": 118.0,
                        "y": 129.0,
                        "z": 4.0,
                    },
                    "home_before_photo": True,
                    "home_and_release_after_powered_job": True,
                    "grbl_step_idle_delay_ms": 123,
                    "allow_motion": not simulator,
                    "controller_startup_delay": 1.25,
                    "max_travel_feed_mm_min": 4500.0,
                    "max_work_feed_mm_min": 3200.0,
                },
                "laser": {
                    "power_mode": "M4",
                    "power_max": 1000,
                    "default_power": 275,
                    "frame_power": 0,
                    "travel_feed_mm_min": 3000.0,
                    "engrave_feed_mm_min": 1200.0,
                    "curve_tolerance_mm": 0.12,
                    "boundary_margin_mm": 3.0,
                    "guarded_output_polygon_mm": [
                        [15.0, 25.0],
                        [225.0, 25.0],
                        [225.0, 235.0],
                        [15.0, 235.0],
                    ],
                    "spot_offset_x_mm": -2.5,
                    "spot_offset_y_mm": 1.25,
                    "arm_timeout_seconds": 45,
                    "allow_low_power_frame": False,
                    "return_to_photo_position": True,
                    "preview_acceleration_mm_s2": 420.0,
                    "preview_command_delay_ms": 1.5,
                },
            }
        ),
        encoding="utf-8",
    )
    return load_settings(config)


def test_missing_registry_migrates_exact_current_machine_once(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    registry = MachineRegistry.load_or_migrate(settings)

    assert registry.path == settings.app.data_dir / "machines.json"
    assert registry.path.exists()
    assert registry.active_machine_id == "existing-machine"
    migrated = registry.active_machine
    assert migrated.created_from == "legacy-config"
    assert migrated.machine_profile_id == "generic-grbl"
    assert migrated.tool_head_profile_id == "custom-laser-head"
    assert migrated.machine.port == settings.machine.port
    assert migrated.machine.baudrate == 250000
    assert migrated.machine.allow_motion is True
    assert migrated.machine.work_area == settings.machine.work_area
    assert migrated.laser.default_power == 275
    assert migrated.laser.guarded_output_polygon_mm == (
        (15.0, 25.0),
        (225.0, 25.0),
        (225.0, 235.0),
        (15.0, 235.0),
    )

    second = MachineRegistry.load_or_migrate(settings)
    assert [machine.id for machine in second.machines()] == [
        "existing-machine"
    ]


def test_simulator_migration_does_not_infer_a_physical_machine(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(
        _settings(tmp_path, simulator=True)
    )

    machine = registry.active_machine
    assert machine.name == "Existing simulator"
    assert machine.machine_profile_id == "simulator"
    assert machine.tool_head_profile_id == "simulated-laser-head"


def test_auto_protocol_physical_migration_stays_custom(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.machine.protocol = "auto"

    registry = MachineRegistry.load_or_migrate(settings)

    assert registry.active_machine.machine_profile_id == "custom-machine"


def test_profile_created_machine_starts_with_motion_and_laser_defaults_disabled(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))

    created = registry.create_machine(
        "Dad's Ender laser",
        "ender-3-s1-pro",
        "generic-diode-10w",
    )

    assert created.id == "dad-s-ender-laser"
    assert created.machine.allow_motion is False
    assert created.machine.port == "SELECT_CONTROLLER_PORT"
    assert created.laser.default_power == 0
    assert created.laser.frame_power == 0
    assert created.laser.allow_low_power_frame is False


def test_profile_objects_force_safe_setup_defaults() -> None:
    machine_profile = MachineProfile(
        id="unsafe-source",
        name="Unsafe source",
        machine_defaults=MachineSettings(
            backend="serial",
            protocol="grbl",
            port="COM9",
            allow_motion=True,
        ),
    )
    head_profile = ToolHeadProfile(
        id="unsafe-head-source",
        name="Unsafe head source",
        laser_defaults=LaserSettings(
            default_power=900,
            frame_power=20,
            allow_low_power_frame=True,
        ),
    )

    assert machine_profile.machine_defaults.allow_motion is False
    assert head_profile.laser_defaults.default_power == 0
    assert head_profile.laser_defaults.frame_power == 0
    assert head_profile.laser_defaults.allow_low_power_frame is False


def test_multiple_machines_and_active_selection_persist(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))
    first = registry.create_machine(
        "Dad laser",
        "ender-3-s1-pro",
        "generic-diode-10w",
    )
    second = registry.create_machine(
        "Dad laser",
        "generic-grbl",
        "custom-laser-head",
    )
    registry.set_active(second.id)

    assert first.id == "dad-laser"
    assert second.id == "dad-laser-2"
    reloaded = MachineRegistry.load_or_migrate(_settings(tmp_path))
    assert reloaded.active_machine_id == "dad-laser-2"
    assert {machine.id for machine in reloaded.machines()} == {
        "existing-machine",
        "dad-laser",
        "dad-laser-2",
    }


def test_registry_returns_copies_not_mutable_internal_objects(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))

    machine = registry.active_machine
    machine.machine.port = "CHANGED"
    machine.laser.default_power = 0

    assert registry.active_machine.machine.port != "CHANGED"
    assert registry.active_machine.laser.default_power == 275


def test_resolved_machine_is_a_detached_complete_configuration(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))

    resolved = registry.resolve_machine()
    resolved.machine.port = "CHANGED"
    resolved.laser.default_power = 0

    assert resolved.machine_id == "existing-machine"
    assert resolved.machine_profile.id == "generic-grbl"
    assert resolved.tool_head_profile.id == "custom-laser-head"
    assert registry.active_machine.machine.port != "CHANGED"
    assert registry.active_machine.laser.default_power == 275


def test_instance_update_round_trips_without_switching_runtime(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))
    created = registry.create_machine(
        "Dad laser",
        "generic-grbl",
        "generic-diode-10w",
    )
    created.machine.port = "COM7"
    created.machine.work_area = WorkArea(0.0, 300.0, 0.0, 400.0)
    created.machine.photo_x = 150.0
    created.machine.photo_y = 200.0

    updated = registry.update_machine(created)

    assert updated.machine.port == "COM7"
    reloaded = MachineRegistry.load_or_migrate(_settings(tmp_path))
    saved = reloaded.get_machine(created.id)
    assert saved.machine.port == "COM7"
    assert saved.machine.work_area == WorkArea(0.0, 300.0, 0.0, 400.0)
    assert reloaded.active_machine_id == "existing-machine"


def test_invalid_machine_head_pair_is_rejected() -> None:
    with pytest.raises(MachineRegistryError, match="cannot exceed"):
        MachineInstance(
            id="bad-combination",
            name="Bad combination",
            machine_profile_id="generic-grbl",
            tool_head_profile_id="generic-diode-10w",
            machine=MachineSettings(
                backend="serial",
                protocol="grbl",
                port="COM3",
                max_travel_feed_mm_min=1000.0,
                max_work_feed_mm_min=500.0,
            ),
            laser=LaserSettings(
                travel_feed_mm_min=3000.0,
                engrave_feed_mm_min=1200.0,
            ),
        )


def test_malformed_registry_is_rejected_without_overwriting_it(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    path = settings.app.data_dir / "machines.json"
    original = b'{"schema_version": 1, "schema_version": 1}'
    path.write_bytes(original)

    with pytest.raises(MachineRegistryError, match="duplicate key"):
        MachineRegistry.load_or_migrate(settings)

    assert path.read_bytes() == original


def test_unknown_profile_reference_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    registry = MachineRegistry.load_or_migrate(settings)
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    payload["machines"][0]["machine_profile_id"] = "missing-profile"
    registry.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MachineRegistryError, match="unknown machine profile"):
        MachineRegistry.load_or_migrate(settings)


def test_unknown_saved_setting_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    registry = MachineRegistry.load_or_migrate(settings)
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    payload["machines"][0]["machine"]["mystery_limit"] = 12
    registry.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MachineRegistryError, match="Unknown configuration key"):
        MachineRegistry.load_or_migrate(settings)


def test_registry_schema_is_explicit_and_future_versions_are_rejected(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    registry = MachineRegistry.load_or_migrate(settings)
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == MACHINE_REGISTRY_SCHEMA_VERSION
    payload["schema_version"] += 1
    registry.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MachineRegistryError,
        match="Unsupported machine registry schema",
    ):
        MachineRegistry.load_or_migrate(settings)


def test_active_and_only_machine_removal_is_blocked(tmp_path: Path) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))

    with pytest.raises(MachineRegistryError, match="only saved machine"):
        registry.remove_machine("existing-machine")

    created = registry.create_machine(
        "Second machine",
        "generic-grbl",
        "custom-laser-head",
    )
    with pytest.raises(MachineRegistryError, match="active machine"):
        registry.remove_machine("existing-machine")
    registry.set_active(created.id)
    registry.remove_machine("existing-machine")
    assert [machine.id for machine in registry.machines()] == [created.id]


def test_invalid_guarded_polygon_is_rejected_during_load(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    registry = MachineRegistry.load_or_migrate(settings)
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    payload["machines"][0]["laser"]["guarded_output_polygon_mm"] = [
        [0.0, 0.0],
        [10.0, 0.0],
        [5.0, 5.0],
        [0.0, 10.0],
    ]
    registry.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MachineRegistryError, match="strictly convex|ordered"):
        MachineRegistry.load_or_migrate(settings)


def test_migration_race_loads_the_registry_published_by_the_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)

    def publish_winner(path: Path, data: bytes) -> bool:
        payload = json.loads(data.decode("utf-8"))
        payload["machines"][0]["name"] = "Winner's machine"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return False

    monkeypatch.setattr(
        profiles_module,
        "atomic_write_bytes_if_absent",
        publish_winner,
    )

    registry = MachineRegistry.load_or_migrate(settings)

    assert registry.active_machine.name == "Winner's machine"


def test_persistence_failure_rolls_back_in_memory_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))
    original = registry.active_machine

    def fail_save() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(registry, "save", fail_save)

    with pytest.raises(OSError, match="disk full"):
        registry.create_machine(
            "Not saved",
            "generic-grbl",
            "custom-laser-head",
        )
    assert [machine.id for machine in registry.machines()] == [
        "existing-machine"
    ]

    changed = registry.active_machine
    changed.name = "Changed"
    with pytest.raises(OSError, match="disk full"):
        registry.update_machine(changed)
    assert registry.active_machine.name == original.name
