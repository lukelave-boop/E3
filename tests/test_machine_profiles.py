from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.calibration.reach import (
    FixtureReachEvidence,
    FixtureReachStore,
)
from laser_aligner.config import (
    LaserSettings,
    MachineSettings,
    WorkArea,
    load_settings,
)
from laser_aligner.machine import profiles as profiles_module
from laser_aligner.machine.profiles import (
    MACHINE_REGISTRY_SCHEMA_VERSION,
    REMOVED_SIMULATOR_BACKUP_SUFFIX,
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
                    "honeycomb_span_mm": 191.25,
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
    assert migrated.machine.honeycomb_span_mm == pytest.approx(191.25)
    assert migrated.laser.default_power == 275
    assert migrated.laser.guarded_output_polygon_mm == (
        (15.0, 25.0),
        (225.0, 25.0),
        (225.0, 235.0),
        (15.0, 235.0),
    )

    persisted = registry.path.read_bytes()
    second = MachineRegistry.load_or_migrate(settings)
    assert [machine.id for machine in second.machines()] == [
        "existing-machine"
    ]
    assert registry.path.read_bytes() == persisted


def test_builtin_profile_ids_are_stable_and_complete(tmp_path: Path) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))

    assert {profile.id for profile in registry.machine_profiles()} == {
        "generic-grbl",
        "generic-marlin",
        "ender-3-s1-pro",
        "custom-machine",
    }
    assert {profile.id for profile in registry.tool_head_profiles()} == {
        "generic-diode-10w",
        "custom-laser-head",
    }


def _append_legacy_simulator(path: Path, *, active: bool) -> bytes:
    original = json.loads(path.read_text(encoding="utf-8"))
    legacy = copy.deepcopy(original["machines"][0])
    legacy["id"] = "legacy-simulator"
    legacy["name"] = "Legacy simulator"
    legacy["machine_profile_id"] = "simulator"
    legacy["tool_head_profile_id"] = "simulated-laser-head"
    legacy["machine"]["backend"] = "simulator"
    legacy["machine"]["port"] = "simulator"
    original["machines"].append(legacy)
    if active:
        original["active_machine_id"] = legacy["id"]
    encoded = (json.dumps(original, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(encoded)
    return encoded


def test_inactive_legacy_simulator_is_removed_with_atomic_backup(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    path = MachineRegistry.load_or_migrate(settings).path
    legacy_bytes = _append_legacy_simulator(path, active=False)

    registry = MachineRegistry.load_or_migrate(settings)

    assert registry.active_machine_id == "existing-machine"
    assert [machine.id for machine in registry.machines()] == ["existing-machine"]
    backup = path.with_name(path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX)
    assert backup.read_bytes() == legacy_bytes


def test_active_legacy_simulator_requires_explicit_selection_without_rewrite(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    path = MachineRegistry.load_or_migrate(settings).path
    legacy_bytes = _append_legacy_simulator(path, active=True)

    with pytest.raises(MachineRegistryError, match="explicitly select a real machine"):
        MachineRegistry.load_or_migrate(settings)

    assert path.read_bytes() == legacy_bytes
    assert not path.with_name(
        path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
    ).exists()


def test_recovery_load_is_read_only_unselected_and_reserves_simulator_ids(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    path = MachineRegistry.load_or_migrate(settings).path
    legacy_bytes = _append_legacy_simulator(path, active=True)

    state = MachineRegistry.load_for_recovery(
        path,
        machine_state_root=settings.app.data_dir / "machine_state",
    )

    assert state.original_bytes == legacy_bytes
    assert state.simulator_machine_ids == ("legacy-simulator",)
    assert state.original_active_machine_id == "legacy-simulator"
    assert [machine.id for machine in state.physical_machines] == [
        "existing-machine"
    ]
    with pytest.raises(MachineRegistryError, match="no active machine"):
        _ = state.registry.active_machine_id
    with pytest.raises(MachineRegistryError, match="retired simulator"):
        state.registry.create_machine(
            "Replacement",
            "generic-grbl",
            "custom-laser-head",
            machine_id="legacy-simulator",
            persist=False,
        )

    created = state.registry.create_machine(
        "Legacy simulator",
        "generic-grbl",
        "custom-laser-head",
        persist=False,
    )
    assert created.id == "legacy-simulator-2"
    assert path.read_bytes() == legacy_bytes


def test_recovery_load_accepts_simulator_only_without_selecting_it(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    path = MachineRegistry.load_or_migrate(settings).path
    _append_legacy_simulator(path, active=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["machines"] = [
        machine
        for machine in payload["machines"]
        if machine["id"] == "legacy-simulator"
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")

    state = MachineRegistry.load_for_recovery(path)

    assert state.physical_machines == ()
    assert state.simulator_machine_ids == ("legacy-simulator",)
    assert state.original_active_machine_id == "legacy-simulator"


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
    assert created.machine.honeycomb_span_mm is None
    assert created.camera_profile_id is None
    assert created.calibration_profile_id is None
    assert created.laser.default_power == 0
    assert created.laser.frame_power == 0
    assert created.laser.allow_low_power_frame is False


def test_profile_created_instances_are_detached_from_each_other_and_profiles(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))
    profile_before = registry.get_machine_profile("generic-marlin")
    first = registry.create_machine(
        "First Marlin",
        "generic-marlin",
        "custom-laser-head",
    )
    second = registry.create_machine(
        "Second Marlin",
        "generic-marlin",
        "custom-laser-head",
    )

    first.machine.port = "COM77"
    first.machine.work_area.x_max = 333.0
    first.laser.power_max = 255

    assert second.machine.port == "SELECT_CONTROLLER_PORT"
    assert second.machine.work_area.x_max == 220.0
    assert second.laser.power_max == 1000
    assert registry.get_machine_profile("generic-marlin") == profile_before
    assert first.camera_profile_id is None
    assert first.calibration_profile_id is None
    assert second.camera_profile_id is None
    assert second.calibration_profile_id is None


def test_saved_machine_air_assist_mapping_round_trips_and_is_detached(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))
    created = registry.create_machine(
        "Marlin air machine",
        "generic-marlin",
        "custom-laser-head",
    )
    created.machine.port = "COM11"
    created.machine.air_assist = AirAssistSettings(
        mode=AirAssistMode.MARLIN_FAN,
        fan_index=3,
    )

    saved = registry.update_machine(created)
    created.machine.air_assist.fan_index = 4
    reloaded = MachineRegistry.load_or_migrate(_settings(tmp_path)).get_machine(
        saved.id
    )

    assert saved.machine.air_assist == AirAssistSettings(
        mode=AirAssistMode.MARLIN_FAN,
        fan_index=3,
    )
    assert reloaded.machine.air_assist == saved.machine.air_assist
    assert reloaded.to_dict()["machine"]["air_assist"] == {
        "mode": "marlin_fan",
        "fan_index": 3,
        "port": "",
        "baudrate": 115200,
    }


def test_saved_secondary_marlin_mapping_round_trips_with_grbl_primary(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))
    created = registry.create_machine(
        "GRBL with Pi auxiliary fan",
        "generic-grbl",
        "custom-laser-head",
    )
    endpoint = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
    created.machine.protocol = "grbl"
    created.machine.port = "e3bridge://192.168.5.18:8765"
    created.machine.air_assist = AirAssistSettings(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN,
        port=endpoint,
        baudrate=115200,
    )

    saved = registry.update_machine(created)
    reloaded = MachineRegistry.load_or_migrate(_settings(tmp_path)).get_machine(
        saved.id
    )

    assert reloaded.machine.protocol == "grbl"
    assert reloaded.machine.port == "e3bridge://192.168.5.18:8765"
    assert reloaded.machine.air_assist == AirAssistSettings(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN,
        port=endpoint,
        baudrate=115200,
    )
    assert reloaded.to_dict()["machine"]["air_assist"] == {
        "mode": "secondary_marlin_fan",
        "fan_index": 0,
        "port": endpoint,
        "baudrate": 115200,
    }


def test_schema_one_machine_without_air_assist_defaults_disabled(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    registry = MachineRegistry.load_or_migrate(settings)
    payload = json.loads(registry.path.read_text(encoding="utf-8"))
    payload["machines"][0]["machine"].pop("air_assist")
    registry.path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = MachineRegistry.load_or_migrate(settings)

    assert reloaded.active_machine.machine.air_assist == AirAssistSettings()


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


def test_all_generic_machine_profiles_leave_honeycomb_span_unset(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))

    for profile in registry.machine_profiles():
        assert profile.machine_defaults.honeycomb_span_mm is None
        assert profile.machine_defaults.air_assist == AirAssistSettings()


@pytest.mark.parametrize(
    "value",
    [True, "191", 0, -1.0, float("nan"), float("inf")],
)
def test_honeycomb_span_rejects_non_positive_or_non_finite_values(
    value: object,
) -> None:
    with pytest.raises(MachineRegistryError, match="honeycomb_span_mm"):
        MachineInstance(
            id="invalid-span",
            name="Invalid span",
            machine_profile_id="generic-grbl",
            tool_head_profile_id="custom-laser-head",
            machine=MachineSettings(honeycomb_span_mm=value),  # type: ignore[arg-type]
            laser=LaserSettings(),
        )


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


def test_create_machine_does_not_reuse_deleted_machine_state_scope(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    registry = MachineRegistry.load_or_migrate(settings)
    first = registry.create_machine(
        "Workshop laser",
        "generic-grbl",
        "custom-laser-head",
    )
    registry.set_active(first.id)
    first_store = FixtureReachStore(
        settings.app.data_dir,
        machine_id=first.id,
    )
    first_store.save(
        FixtureReachEvidence(fixture_mode="permanent", x_min_mm=5.0)
    )
    registry.set_active("existing-machine")
    registry.remove_machine(first.id)

    replacement = registry.create_machine(
        "Workshop laser",
        "generic-grbl",
        "custom-laser-head",
    )
    replacement_store = FixtureReachStore(
        settings.app.data_dir,
        machine_id=replacement.id,
    )

    assert replacement.id == "workshop-laser-2"
    assert first_store.path.exists()
    assert replacement_store.path != first_store.path
    assert replacement_store.evidence.fixture_mode == "unclassified"
    assert not replacement_store.path.exists()


def test_duplicate_machine_does_not_reuse_deleted_machine_state_scope(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    registry = MachineRegistry.load_or_migrate(settings)
    source = registry.active_machine
    first = registry.duplicate_machine(source.id, name="Workshop copy")
    registry.set_active(first.id)
    first_store = FixtureReachStore(
        settings.app.data_dir,
        machine_id=first.id,
    )
    first_store.save(
        FixtureReachEvidence(fixture_mode="permanent", x_min_mm=7.0)
    )
    registry.set_active(source.id)
    registry.remove_machine(first.id)

    replacement = registry.duplicate_machine(
        source.id,
        name="Workshop copy",
    )
    replacement_store = FixtureReachStore(
        settings.app.data_dir,
        machine_id=replacement.id,
    )

    assert replacement.id == "workshop-copy-2"
    assert first_store.path.exists()
    assert replacement_store.path != first_store.path
    assert replacement_store.evidence.fixture_mode == "unclassified"
    assert not replacement_store.path.exists()


def test_explicit_machine_id_rejects_orphaned_machine_state_scope(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    registry = MachineRegistry.load_or_migrate(
        settings,
        path=tmp_path / "alternate-registry" / "machines.json",
    )
    scope = settings.app.data_dir / "machine_state" / "retired-machine"
    scope.mkdir(parents=True)

    with pytest.raises(
        MachineRegistryError,
        match="preserved machine-state evidence",
    ):
        registry.create_machine(
            "Replacement",
            "generic-grbl",
            "custom-laser-head",
            machine_id="retired-machine",
        )


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
    assert resolved.created_from == "legacy-config"
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
    created.machine.honeycomb_span_mm = 187.5

    updated = registry.update_machine(created)

    assert updated.machine.port == "COM7"
    reloaded = MachineRegistry.load_or_migrate(_settings(tmp_path))
    saved = reloaded.get_machine(created.id)
    assert saved.machine.port == "COM7"
    assert saved.machine.work_area == WorkArea(0.0, 300.0, 0.0, 400.0)
    assert saved.machine.honeycomb_span_mm == pytest.approx(187.5)
    assert registry.resolve_machine(created.id).machine.honeycomb_span_mm == pytest.approx(
        187.5
    )
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


def test_migrated_machine_records_current_optical_profile(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.camera.width = 1920
    settings.camera.height = 1080
    settings.camera.controls["focus_automatic_continuous"] = 0
    settings.camera.controls["focus_absolute"] = 10

    registry = MachineRegistry.load_or_migrate(settings)

    machine = registry.active_machine
    assert machine.camera_profile_id == "1920x1080-manual-focus-010"
    assert machine.calibration_profile_id == "1920x1080-manual-focus-010"


def test_duplicate_machine_preserves_exact_settings_and_bindings(
    tmp_path: Path,
) -> None:
    registry = MachineRegistry.load_or_migrate(_settings(tmp_path))
    source = registry.active_machine
    source.camera_profile_id = "camera-profile"
    source.calibration_profile_id = "calibration-profile"
    source.machine.honeycomb_span_mm = 188.75
    registry.update_machine(source)

    duplicated = registry.duplicate_machine(source.id, name="House copy")

    assert duplicated.id == "house-copy"
    assert duplicated.name == "House copy"
    assert duplicated.machine == source.machine
    assert duplicated.machine.honeycomb_span_mm == pytest.approx(188.75)
    assert duplicated.laser == source.laser
    assert duplicated.camera_profile_id == "camera-profile"
    assert duplicated.calibration_profile_id == "calibration-profile"
    assert duplicated.created_from == f"duplicate:{source.id}"
