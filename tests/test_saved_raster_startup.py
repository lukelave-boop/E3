"""Saved raster-envelope compatibility at the desktop's pre-runtime boundary."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from laser_aligner.config import ConfigError, load_settings
from laser_aligner.desktop.main import _prepare_runtime_startup
from laser_aligner.first_run import inspect_simulator_recovery
from laser_aligner.machine.profiles import MachineRegistry, MachineRegistryError
from laser_aligner.raster_safety import COORDINATE_REFERENCE


def _synthetic_envelope() -> dict[str, object]:
    # Synthetic validation data only; no hardware verification is claimed.
    return {
        "x_min": -4.0,
        "x_max": 224.0,
        "y_min": 0.0,
        "y_max": 220.0,
        "verified": True,
        "controller": "synthetic controller fixture",
        "firmware": "synthetic firmware fixture",
        "configuration": "synthetic configuration fixture",
        "reference": COORDINATE_REFERENCE,
        "tested_at": "2026-01-01",
    }


def _saved_setup(tmp_path: Path, *, with_envelope: bool):
    path = tmp_path / "network-local.json"
    payload = {
        "app": {"data_dir": "data", "simulation": False},
        "camera": {"autostart": False},
        "machine": {
            "backend": "serial",
            "port": "COM_OFFLINE_STARTUP_FIXTURE",
            "allow_motion": False,
        },
        "laser": {"default_power": 0, "frame_power": 0},
    }
    if with_envelope:
        payload["machine"]["raster_travel_envelope"] = _synthetic_envelope()
    path.write_text(json.dumps(payload), encoding="utf-8")
    settings = load_settings(path)
    registry = MachineRegistry.load_or_migrate(settings)
    saved = json.loads(registry.path.read_text(encoding="utf-8"))
    legacy = copy.deepcopy(saved["machines"][0])
    legacy["id"] = "other-legacy-machine"
    legacy["name"] = "Other legacy fixture"
    legacy["machine"].pop("raster_travel_envelope", None)
    saved["machines"].append(legacy)
    if not with_envelope:
        saved["machines"][0]["machine"].pop("raster_travel_envelope", None)
    registry.path.write_text(json.dumps(saved), encoding="utf-8")
    return path, settings, registry


def _snapshot_files(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _validate_startup(path: Path, events: list[str]):
    def forbidden(_value):
        pytest.fail("Existing real settings must not enter interactive setup/recovery")

    def inspect(config: Path):
        events.append("inspect recovery")
        return inspect_simulator_recovery(config)

    def validate_only(config: Path):
        # Exercise the loader and saved-profile resolver without CoreRuntime,
        # AppContext, credential loading, camera services, or controller services.
        events.append("validate runtime settings")
        settings = load_settings(config)
        recovery = MachineRegistry.load_for_recovery(
            settings.app.data_dir / "machines.json",
            machine_state_root=settings.app.data_dir / "machine_state",
        )
        resolved = recovery.registry.resolve_machine(recovery.original_active_machine_id)
        return SimpleNamespace(settings=settings, resolved=resolved)

    return _prepare_runtime_startup(
        path,
        preserved_user_config=path,
        explicit_config=True,
        first_run_runner=forbidden,
        recovery_inspector=inspect,
        recovery_runner=forbidden,
        before_runtime=lambda: events.append("before runtime"),
        runtime_factory=validate_only,
    )


@pytest.mark.parametrize("with_envelope", [True, False])
def test_saved_raster_startup_preserves_settings_and_profiles(
    tmp_path: Path, with_envelope: bool,
) -> None:
    path, original_settings, original_registry = _saved_setup(
        tmp_path, with_envelope=with_envelope,
    )
    original_settings_payload = asdict(original_settings)
    original_profile = original_registry.active_machine.to_dict()
    before = _snapshot_files(tmp_path)
    events: list[str] = []

    prepared, open_setup = _validate_startup(path, events)

    assert not open_setup
    assert events == [
        "inspect recovery", "before runtime", "validate runtime settings",
    ]
    assert prepared.settings.machine.allow_motion is False
    assert prepared.resolved.machine.allow_motion is False
    assert prepared.resolved.laser.default_power == 0
    assert prepared.resolved.laser.frame_power == 0
    configured = prepared.settings.machine.raster_travel_envelope
    selected = prepared.resolved.machine.raster_travel_envelope
    assert configured == selected
    if with_envelope:
        assert selected.to_dict() == _synthetic_envelope()
    else:
        assert selected is None
    assert asdict(original_settings) == original_settings_payload
    assert original_registry.active_machine.to_dict() == original_profile
    assert _snapshot_files(tmp_path) == before


@pytest.mark.parametrize("location", ["config", "registry"])
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unexpected_authority", True, "requires exact bounds"),
        ("x_min", 300.0, "minima must be less than maxima"),
        ("verified", "true", "verified must be an exact boolean"),
        ("reference", "unverified work offset", "reference must be"),
    ],
)
def test_saved_raster_startup_rejects_invalid_envelopes_without_changes(
    tmp_path: Path, location: str, field: str, value: object, message: str,
) -> None:
    path, _settings, registry = _saved_setup(tmp_path, with_envelope=True)
    target = path if location == "config" else registry.path
    payload = json.loads(target.read_text(encoding="utf-8"))
    machine = payload["machine"] if location == "config" else payload["machines"][0]["machine"]
    machine["raster_travel_envelope"][field] = value
    target.write_text(json.dumps(payload), encoding="utf-8")
    before = _snapshot_files(tmp_path)
    events: list[str] = []

    expected_error = ConfigError if location == "config" else MachineRegistryError
    with pytest.raises(expected_error, match=message):
        _validate_startup(path, events)

    if location == "registry":
        # Recovery inspects saved physical profiles before any startup callback.
        assert events == ["inspect recovery"]
    else:
        assert events == [
            "inspect recovery", "before runtime", "validate runtime settings",
        ]
    assert _snapshot_files(tmp_path) == before


@pytest.mark.parametrize("location", ["config", "registry"])
def test_saved_raster_startup_still_rejects_unknown_machine_keys(
    tmp_path: Path, location: str,
) -> None:
    path, _settings, registry = _saved_setup(tmp_path, with_envelope=True)
    target = path if location == "config" else registry.path
    payload = json.loads(target.read_text(encoding="utf-8"))
    machine = payload["machine"] if location == "config" else payload["machines"][0]["machine"]
    machine["unknown_future_authority"] = True
    target.write_text(json.dumps(payload), encoding="utf-8")
    before = _snapshot_files(tmp_path)

    with pytest.raises(ValueError, match="Unknown configuration key: machine.unknown_future_authority"):
        _validate_startup(path, [])

    assert _snapshot_files(tmp_path) == before
