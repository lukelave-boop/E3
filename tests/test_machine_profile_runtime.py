from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from laser_aligner.config import load_settings
from laser_aligner.core import runtime as runtime_module
from laser_aligner.core.runtime import CoreRuntime
from laser_aligner.machine.profiles import MachineRegistryError


def _settings(tmp_path: Path):
    config = tmp_path / "runtime-config.json"
    config.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "runtime",
                },
                "machine": {
                    "backend": "serial",
                    "protocol": "grbl",
                    "port": "e3bridge://pi-controller:8765",
                    "allow_motion": True,
                },
                "laser": {"default_power": 275},
            }
        ),
        encoding="utf-8",
    )
    return load_settings(config)


def test_runtime_bootstraps_registry_without_replacing_active_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    events: list[tuple[str, Any]] = []

    class FakeContext:
        def __init__(
            self,
            received_settings: Any,
            *,
            hardware_enabled: bool,
            laser_lockout: bool,
            machine_identity: Any,
        ) -> None:
            events.append(("context", (received_settings, machine_identity)))
            self.settings = received_settings
            self.machine_identity = machine_identity

        def start(self) -> None:
            events.append(("start", None))

        def stop(self) -> None:
            events.append(("stop", None))

        def status(self) -> dict[str, object]:
            return {}

    monkeypatch.setattr(runtime_module, "AppContext", FakeContext)

    runtime = CoreRuntime(
        settings,
        hardware_enabled=True,
        laser_lockout=False,
    )

    assert runtime.settings is settings
    assert runtime.context.settings is settings
    assert events == [("context", (settings, runtime.context.machine_identity))]
    active = runtime.machine_registry.active_machine
    assert active.machine.port == settings.machine.port
    assert active.laser.default_power == 275
    assert settings.machine.allow_motion is True
    assert settings.laser.default_power == 275
    identity = runtime.context.machine_identity
    assert identity.machine_id == "existing-machine"
    assert identity.machine_name == active.name
    assert identity.created_from == "legacy-config"
    assert identity.machine_profile_id == "generic-grbl"
    assert identity.tool_head_profile_id == "custom-laser-head"
    assert identity.expected_camera_profile_id == active.camera_profile_id
    assert (
        identity.expected_calibration_profile_id
        == active.calibration_profile_id
    )


def test_invalid_registry_blocks_context_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    path = settings.app.data_dir / "machines.json"
    path.write_text('{"schema_version": 99}', encoding="utf-8")
    constructed = False

    class UnusedContext:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            nonlocal constructed
            constructed = True

    monkeypatch.setattr(runtime_module, "AppContext", UnusedContext)

    with pytest.raises(MachineRegistryError, match="Unsupported"):
        CoreRuntime(settings)

    assert constructed is False


def test_runtime_uses_registry_active_machine_on_next_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    registry = runtime_module.MachineRegistry.load_or_migrate(settings)
    created = registry.create_machine(
        "Second machine",
        "generic-grbl",
        "generic-diode-10w",
    )
    created.machine.port = "e3bridge://second-controller:8765"
    created.machine.allow_motion = True
    created.laser.default_power = 0
    registry.update_machine(created)
    registry.set_active(created.id)

    received: list[Any] = []

    class FakeContext:
        def __init__(
            self,
            received_settings: Any,
            *,
            hardware_enabled: bool,
            laser_lockout: bool,
            machine_identity: Any,
        ) -> None:
            del hardware_enabled, laser_lockout
            received.append((received_settings, machine_identity))

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def status(self) -> dict[str, object]:
            return {}

    monkeypatch.setattr(runtime_module, "AppContext", FakeContext)

    runtime = CoreRuntime(
        settings,
        hardware_enabled=True,
        laser_lockout=False,
    )

    assert runtime.running_machine_id == created.id
    assert runtime.settings.machine.port == "e3bridge://second-controller:8765"
    assert received[0][0].machine.port == "e3bridge://second-controller:8765"
    assert received[0][1].machine_id == created.id
    assert received[0][1].created_from == "profile"
