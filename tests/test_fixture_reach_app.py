from __future__ import annotations

import json
from pathlib import Path

from laser_aligner.app import AppContext, RunningMachineIdentity
from laser_aligner.calibration.reach import FixtureReachEvidence
from laser_aligner.config import load_settings


def _settings(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "simulation": True,
                    "open_browser": False,
                },
                "machine": {
                    "backend": "serial",
                    "protocol": "grbl",
                    "allow_motion": False,
                },
            }
        ),
        encoding="utf-8",
    )
    return load_settings(config)


def _identity(
    machine_id: str,
    name: str,
    *,
    created_from: str = "profile",
) -> RunningMachineIdentity:
    return RunningMachineIdentity(
        machine_id=machine_id,
        machine_name=name,
        created_from=created_from,
        machine_profile_id="generic-grbl",
        tool_head_profile_id="custom-laser-head",
        expected_camera_profile_id="camera-profile",
        expected_calibration_profile_id="calibration-profile",
    )


def test_app_context_exposes_detached_running_machine_identity(
    tmp_path: Path,
) -> None:
    identity = _identity("physical-a", "Garage laser")
    context = AppContext(_settings(tmp_path), machine_identity=identity)

    assert context.machine_identity is identity
    assert context.machine_id == "physical-a"
    assert context.machine_name == "Garage laser"
    assert context.machine_created_from == "profile"
    assert context.machine_profile_id == "generic-grbl"
    assert context.tool_head_profile_id == "custom-laser-head"
    assert context.expected_camera_profile_id == "camera-profile"
    assert context.expected_calibration_profile_id == "calibration-profile"
    assert context.fixture_reach.path == (
        context.settings.app.data_dir
        / "machine_state"
        / "physical-a"
        / "fixture_reach.json"
    )


def test_only_legacy_config_machine_can_claim_global_evidence(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    legacy = settings.app.data_dir / "fixture_reach.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy_bytes = json.dumps(
        FixtureReachEvidence(
            fixture_mode="permanent",
            x_min_mm=5.0,
        ).to_dict()
    ).encode("utf-8")
    legacy.write_bytes(legacy_bytes)

    new_machine = AppContext(
        settings,
        machine_identity=_identity("new-machine", "New machine"),
    )
    duplicate = AppContext(
        _settings(tmp_path),
        machine_identity=_identity(
            "new-machine-copy",
            "New machine copy",
            created_from="duplicate:new-machine",
        ),
    )

    assert not new_machine.fixture_reach.path.exists()
    assert not duplicate.fixture_reach.path.exists()
    assert not new_machine.fixture_reach.migration_path.exists()

    context = AppContext(
        _settings(tmp_path),
        machine_identity=_identity(
            "physical-a",
            "Garage laser",
            created_from="legacy-config",
        ),
    )

    assert context.fixture_reach.evidence.x_min_mm == 5.0
    assert context.fixture_reach.path.exists()
    assert legacy.read_bytes() == legacy_bytes
    claim = json.loads(
        context.fixture_reach.migration_path.read_text(encoding="utf-8")
    )
    assert claim["claimed_machine_id"] == "physical-a"

    later_new_machine = AppContext(
        _settings(tmp_path),
        machine_identity=_identity("later-machine", "Later machine"),
    )
    assert not later_new_machine.fixture_reach.path.exists()
    assert later_new_machine.fixture_reach.evidence.fixture_mode == "unclassified"
    assert legacy.read_bytes() == legacy_bytes


def test_renaming_machine_retains_fixture_reach_evidence(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = AppContext(
        settings,
        machine_identity=_identity("physical-a", "Old name"),
    )
    first.fixture_reach.save(
        FixtureReachEvidence(fixture_mode="permanent", x_min_mm=5.0)
    )

    renamed = AppContext(
        _settings(tmp_path),
        machine_identity=_identity("physical-a", "New name"),
    )

    assert renamed.fixture_reach.evidence.fixture_mode == "permanent"
    assert renamed.fixture_reach.evidence.x_min_mm == 5.0
    assert renamed.fixture_reach.path == first.fixture_reach.path


def test_duplicate_machine_id_starts_without_source_fixture_evidence(
    tmp_path: Path,
) -> None:
    source = AppContext(
        _settings(tmp_path),
        machine_identity=_identity("physical-a", "Source"),
    )
    source.fixture_reach.save(
        FixtureReachEvidence(fixture_mode="permanent", x_min_mm=5.0)
    )

    duplicate = AppContext(
        _settings(tmp_path),
        machine_identity=_identity(
            "physical-a-copy",
            "Source copy",
            created_from="duplicate:physical-a",
        ),
    )

    assert duplicate.fixture_reach.evidence.fixture_mode == "unclassified"
    assert duplicate.fixture_reach.evidence.x_min_mm is None
    assert duplicate.fixture_reach.path != source.fixture_reach.path
    assert not duplicate.fixture_reach.path.exists()


def test_direct_context_uses_explicit_standalone_legacy_scope(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    legacy = settings.app.data_dir / "fixture_reach.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy_bytes = json.dumps(
        FixtureReachEvidence(
            fixture_mode="permanent",
            x_min_mm=5.0,
        ).to_dict()
    ).encode("utf-8")
    legacy.write_bytes(legacy_bytes)

    context = AppContext(
        settings,
        machine_identity=RunningMachineIdentity.standalone(),
    )

    assert context.machine_id == "standalone"
    assert context.fixture_reach.path == (
        context.settings.app.data_dir
        / "machine_state"
        / "standalone"
        / "fixture_reach.json"
    )
    assert not context.fixture_reach.path.exists()
    assert not context.fixture_reach.migration_path.exists()
    assert context.fixture_reach.evidence.fixture_mode == "unclassified"
    assert legacy.read_bytes() == legacy_bytes
