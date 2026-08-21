from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from laser_aligner import __main__ as browser_main
from laser_aligner.config import LEGACY_IMPLICIT_CONTROLLER_PORT, load_settings
from laser_aligner.core import runtime as runtime_module
from laser_aligner.machine.profiles import (
    REMOVED_SIMULATOR_BACKUP_SUFFIX,
    MachineRegistry,
)


def _write_config(
    tmp_path: Path,
    *,
    port: str | None = "e3bridge://raw-controller:8765",
    simulation: bool = False,
    backend: str = "serial",
) -> Path:
    payload: dict[str, Any] = {
        "app": {
            "data_dir": str(tmp_path / "runtime"),
            "open_browser": False,
            "simulation": simulation,
        },
        "camera": {"autostart": False},
    }
    if port is not None or backend != "serial":
        payload["machine"] = {
            "backend": backend,
            "port": port or "simulator",
        }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _patch_process_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        browser_main,
        "configure_logging",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(browser_main.signal, "signal", lambda *_args: None)


def test_cli_uses_active_saved_machine_through_core_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = _write_config(tmp_path)
    initial_settings = load_settings(config_path)
    registry = MachineRegistry.load_or_migrate(initial_settings)
    selected = registry.create_machine(
        "Selected controller",
        "generic-grbl",
        "generic-diode-10w",
    )
    selected.machine.port = "e3bridge://saved-controller:8765"
    selected.machine.allow_motion = True
    registry.update_machine(selected)
    registry.set_active(selected.id)
    observed: dict[str, Any] = {}

    class FakeContext:
        def __init__(
            self,
            settings: Any,
            *,
            hardware_enabled: bool,
            laser_lockout: bool,
            machine_identity: Any,
        ) -> None:
            observed["context"] = self
            observed["settings"] = settings
            observed["hardware_enabled"] = hardware_enabled
            observed["laser_lockout"] = laser_lockout
            observed["machine_identity"] = machine_identity

        def start(self) -> None:
            observed["context_started"] = True

        def stop(self) -> None:
            observed["context_stopped"] = True

    class FakeServer:
        def __init__(self, address: tuple[str, int], context: Any) -> None:
            observed["address"] = address
            observed["server_context"] = context

        def serve_forever(self, *, poll_interval: float) -> None:
            observed["poll_interval"] = poll_interval

        def server_close(self) -> None:
            observed["server_closed"] = True

    monkeypatch.setattr(runtime_module, "AppContext", FakeContext)
    monkeypatch.setattr(browser_main, "AppHTTPServer", FakeServer)
    _patch_process_edges(monkeypatch)

    result = browser_main.main(
        [
            "--config",
            str(config_path),
            "--hardware",
            "--laser-lockout",
        ]
    )

    assert result == 0
    assert observed["settings"].machine.port == (
        "e3bridge://saved-controller:8765"
    )
    assert observed["settings"].machine.allow_motion is True
    assert observed["machine_identity"].machine_id == selected.id
    assert observed["hardware_enabled"] is True
    assert observed["laser_lockout"] is True
    assert observed["server_context"] is observed["context"]
    assert observed["context_started"] is True
    assert observed["context_stopped"] is True
    assert observed["server_closed"] is True


@pytest.mark.parametrize(
    "unconfigured_port",
    [None, LEGACY_IMPLICIT_CONTROLLER_PORT],
    ids=["packaged-placeholder", "legacy-implicit-port"],
)
def test_cli_refuses_unconfigured_machine_before_context_or_registry_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unconfigured_port: str | None,
) -> None:
    config_path = _write_config(tmp_path, port=unconfigured_port)
    registry_path = tmp_path / "runtime" / "machines.json"
    monkeypatch.setattr(
        runtime_module,
        "AppContext",
        lambda *_args, **_kwargs: pytest.fail(
            "unconfigured machine reached AppContext"
        ),
    )
    monkeypatch.setattr(
        browser_main,
        "AppHTTPServer",
        lambda *_args, **_kwargs: pytest.fail(
            "unconfigured machine reached HTTP server"
        ),
    )
    _patch_process_edges(monkeypatch)

    result = browser_main.main(["--config", str(config_path)])

    assert result == 2
    assert "Real-machine setup required" in capsys.readouterr().err
    assert not registry_path.exists()


def test_cli_refuses_legacy_app_simulation_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, simulation=True)
    monkeypatch.setattr(
        browser_main,
        "CoreRuntime",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy simulation reached CoreRuntime"
        ),
    )
    _patch_process_edges(monkeypatch)

    result = browser_main.main(["--config", str(config_path)])

    assert result == 2
    stderr = capsys.readouterr().err
    assert "Real-machine setup required" in stderr
    assert "complete real-machine setup" in stderr


def test_cli_refuses_legacy_simulator_backend_before_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(
        tmp_path,
        port="simulator",
        backend="simulator",
    )
    monkeypatch.setattr(
        browser_main,
        "CoreRuntime",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy simulator backend reached CoreRuntime"
        ),
    )
    _patch_process_edges(monkeypatch)

    result = browser_main.main(["--config", str(config_path)])

    assert result == 2
    stderr = capsys.readouterr().err
    assert "Real-machine setup required" in stderr
    assert "complete real-machine setup" in stderr


@pytest.mark.parametrize("simulator_only", [False, True])
def test_cli_refuses_active_legacy_simulator_registry_without_rewriting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    simulator_only: bool,
) -> None:
    config_path = _write_config(tmp_path)
    settings = load_settings(config_path)
    registry_path = MachineRegistry.load_or_migrate(settings).path
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    simulator = copy.deepcopy(payload["machines"][0])
    simulator["id"] = "legacy-simulator"
    simulator["name"] = "Legacy simulator"
    simulator["machine_profile_id"] = "simulator"
    simulator["tool_head_profile_id"] = "simulated-laser-head"
    simulator["machine"]["backend"] = "simulator"
    simulator["machine"]["port"] = "simulator"
    payload["machines"] = (
        [simulator]
        if simulator_only
        else [payload["machines"][0], simulator]
    )
    payload["active_machine_id"] = simulator["id"]
    original = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    registry_path.write_bytes(original)
    monkeypatch.setattr(
        runtime_module,
        "AppContext",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy simulator registry reached AppContext"
        ),
    )
    monkeypatch.setattr(
        browser_main,
        "AppHTTPServer",
        lambda *_args, **_kwargs: pytest.fail(
            "legacy simulator registry reached HTTP server"
        ),
    )
    _patch_process_edges(monkeypatch)

    result = browser_main.main(["--config", str(config_path)])

    assert result == 2
    assert "Real-machine setup required" in capsys.readouterr().err
    assert registry_path.read_bytes() == original
    assert not registry_path.with_name(
        registry_path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
    ).exists()
