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
    runtime_paths: list[Path] = []
    recovered_config = tmp_path / "recovered.json"
    runtime = object()

    def inspect(path: Path):
        events.append("inspect recovery")
        return inspect_simulator_recovery(path)

    def recover(plan):
        assert plan.config_simulation_enabled is True
        assert "construct runtime" not in events
        events.append("complete recovery")
        return SimpleNamespace(
            config_path=recovered_config,
            open_machine_setup=True,
        )

    prepared = _prepare_runtime_startup(
        config,
        preserved_user_config=tmp_path / "absent.json",
        explicit_config=True,
        first_run_runner=lambda _path: pytest.fail(
            "legacy configuration entered first-run"
        ),
        recovery_inspector=inspect,
        recovery_runner=recover,
        before_runtime=lambda: events.append("configure credential"),
        runtime_factory=lambda _path: (
            runtime_paths.append(_path)
            or events.append("construct runtime")
            or runtime
        ),
    )

    assert prepared == (runtime, True)
    assert runtime_paths == [recovered_config]
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
        explicit_config=True,
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


@pytest.mark.parametrize("explicit_config", [False, True])
def test_default_named_legacy_config_recovers_before_first_run(
    tmp_path: Path,
    explicit_config: bool,
) -> None:
    config = _legacy_simulation_config(tmp_path)
    default_named_config = config.with_name("default.json")
    config.replace(default_named_config)
    inspected: list[Path] = []

    prepared = _prepare_runtime_startup(
        default_named_config,
        preserved_user_config=tmp_path / "absent.json",
        explicit_config=explicit_config,
        first_run_runner=lambda _path: pytest.fail(
            "explicit legacy configuration entered first-run"
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
    assert inspected == [default_named_config]


def test_clean_packaged_default_runs_first_run_then_rechecks_recovery(
    tmp_path: Path,
) -> None:
    template = tmp_path / "default.json"
    template.write_text("{}", encoding="utf-8")
    saved = tmp_path / "saved.json"
    saved.write_text("{}", encoding="utf-8")
    events: list[tuple[str, Path | None]] = []
    runtime = object()

    def inspect(path: Path):
        events.append(("inspect", path))
        return None

    prepared = _prepare_runtime_startup(
        template,
        preserved_user_config=tmp_path / "absent.json",
        explicit_config=False,
        first_run_runner=lambda path: (
            events.append(("first run", path))
            or SimpleNamespace(config_path=saved, open_machine_setup=True)
        ),
        recovery_inspector=inspect,
        recovery_runner=lambda _plan: pytest.fail(
            "clean first-run output entered simulator recovery"
        ),
        before_runtime=lambda: events.append(("before runtime", None)),
        runtime_factory=lambda path: (
            events.append(("runtime", path)) or runtime
        ),
    )

    assert prepared == (runtime, True)
    assert events == [
        ("inspect", template),
        ("first run", template),
        ("inspect", saved),
        ("before runtime", None),
        ("runtime", saved),
    ]


def test_canceling_first_run_constructs_no_runtime(tmp_path: Path) -> None:
    packaged_template = Path(__file__).resolve().parents[1] / "config" / "default.json"
    template = tmp_path / "default.json"
    template.write_bytes(packaged_template.read_bytes())
    events: list[tuple[str, Path]] = []

    def inspect(path: Path):
        events.append(("inspect", path))
        return inspect_simulator_recovery(path)

    prepared = _prepare_runtime_startup(
        template,
        preserved_user_config=tmp_path / "absent.json",
        explicit_config=False,
        first_run_runner=lambda path: events.append(("first run", path)),
        recovery_inspector=inspect,
        recovery_runner=lambda _plan: pytest.fail(
            "clean packaged default entered simulator recovery"
        ),
        before_runtime=lambda: pytest.fail(
            "cancelled first-run configured runtime credentials"
        ),
        runtime_factory=lambda _path: pytest.fail(
            "cancelled first-run constructed CoreRuntime/AppContext"
        ),
    )

    assert prepared is None
    assert events == [
        ("inspect", template),
        ("first run", template),
    ]


def test_first_run_output_requiring_recovery_cancels_before_runtime(
    tmp_path: Path,
) -> None:
    template = tmp_path / "default.json"
    template.write_text("{}", encoding="utf-8")
    saved = _legacy_simulation_config(tmp_path)
    events: list[tuple[str, Path]] = []

    def inspect(path: Path):
        events.append(("inspect", path))
        return None if path == template else inspect_simulator_recovery(path)

    def cancel_recovery(plan):
        assert plan.source_config_path == saved.resolve()
        events.append(("cancel recovery", plan.source_config_path))
        return None

    prepared = _prepare_runtime_startup(
        template,
        preserved_user_config=tmp_path / "absent.json",
        explicit_config=False,
        first_run_runner=lambda path: (
            events.append(("first run", path))
            or SimpleNamespace(config_path=saved, open_machine_setup=True)
        ),
        recovery_inspector=inspect,
        recovery_runner=cancel_recovery,
        before_runtime=lambda: pytest.fail(
            "cancelled post-first-run recovery configured runtime credentials"
        ),
        runtime_factory=lambda _path: pytest.fail(
            "cancelled post-first-run recovery constructed CoreRuntime/AppContext"
        ),
    )

    assert prepared is None
    assert events == [
        ("inspect", template),
        ("first run", template),
        ("inspect", saved),
        ("cancel recovery", saved.resolve()),
    ]


def test_packaged_default_prefers_preserved_config_before_recovery(
    tmp_path: Path,
) -> None:
    template = tmp_path / "default.json"
    template.write_text("{}", encoding="utf-8")
    preserved = tmp_path / "network-local.json"
    preserved.write_text("{}", encoding="utf-8")
    inspected: list[Path] = []
    runtime = object()

    prepared = _prepare_runtime_startup(
        template,
        preserved_user_config=preserved,
        explicit_config=False,
        first_run_runner=lambda _path: pytest.fail(
            "preserved configuration entered first-run"
        ),
        recovery_inspector=lambda path: inspected.append(path),
        recovery_runner=lambda _plan: pytest.fail(
            "clean preserved configuration entered simulator recovery"
        ),
        before_runtime=lambda: None,
        runtime_factory=lambda path: runtime if path == preserved else None,
    )

    assert prepared == (runtime, False)
    assert inspected == [preserved]


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
