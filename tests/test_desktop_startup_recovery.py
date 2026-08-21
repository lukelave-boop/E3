from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from laser_aligner.desktop.main import (
    _prepare_runtime_startup,
    _recovery_config_destination,
)
from laser_aligner.first_run import inspect_simulator_recovery


def _legacy_simulation_config(tmp_path: Path) -> Path:
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "runtime",
                    "simulation": True,
                },
                "machine": {
                    "backend": "simulator",
                    "port": "simulator",
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_simulation_true_recovers_before_runtime_construction(
    tmp_path: Path,
) -> None:
    config = _legacy_simulation_config(tmp_path)
    events: list[str] = []
    runtime = object()

    def inspect(path: Path):
        events.append("inspect recovery")
        return inspect_simulator_recovery(path)

    def recover(plan):
        assert plan.config_simulation_enabled is True
        assert "construct runtime" not in events
        events.append("complete recovery")
        return SimpleNamespace(
            config_path=config,
            open_machine_setup=True,
        )

    prepared = _prepare_runtime_startup(
        config,
        preserved_user_config=tmp_path / "absent.json",
        first_run_runner=lambda _path: pytest.fail(
            "legacy configuration entered first-run"
        ),
        recovery_inspector=inspect,
        recovery_runner=recover,
        before_runtime=lambda: events.append("configure credential"),
        runtime_factory=lambda _path: (
            events.append("construct runtime") or runtime
        ),
    )

    assert prepared == (runtime, True)
    assert events == [
        "inspect recovery",
        "complete recovery",
        "configure credential",
        "construct runtime",
    ]


def test_canceling_simulator_recovery_constructs_no_runtime(
    tmp_path: Path,
) -> None:
    config = _legacy_simulation_config(tmp_path)
    inspected = []

    prepared = _prepare_runtime_startup(
        config,
        preserved_user_config=tmp_path / "absent.json",
        first_run_runner=lambda _path: pytest.fail(
            "legacy configuration entered first-run"
        ),
        recovery_inspector=lambda path: (
            inspected.append(path) or inspect_simulator_recovery(path)
        ),
        recovery_runner=lambda _plan: None,
        before_runtime=lambda: pytest.fail(
            "cancelled recovery configured runtime credentials"
        ),
        runtime_factory=lambda _path: pytest.fail(
            "cancelled recovery constructed CoreRuntime/AppContext"
        ),
    )

    assert prepared is None
    assert inspected == [config]


def test_legacy_fallback_targets_preserved_config_but_explicit_path_stays_put(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "application" / "config" / "network-local.json"
    preserved = tmp_path / "state" / "config" / "network-local.json"

    assert _recovery_config_destination(
        legacy,
        preserved_user_config=preserved,
        explicit_config=False,
    ) == preserved.resolve()
    assert _recovery_config_destination(
        legacy,
        preserved_user_config=preserved,
        explicit_config=True,
    ) == legacy.resolve()
