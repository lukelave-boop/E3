from __future__ import annotations

import json
from pathlib import Path

import pytest

from laser_aligner.calibration.reach import (
    FixtureReachEvidence,
    FixtureReachStore,
)


def _complete_evidence() -> FixtureReachEvidence:
    return FixtureReachEvidence(
        fixture_mode="permanent",
        x_min_mm=5.0,
        x_max_mm=245.0,
        y_min_mm=5.0,
        y_max_mm=215.0,
    )


def test_fixture_reach_store_restarts_in_the_same_machine_scope(
    tmp_path: Path,
) -> None:
    store = FixtureReachStore(tmp_path, machine_id="machine-a")
    store.save(_complete_evidence())

    reloaded = FixtureReachStore(tmp_path, machine_id="machine-a")

    assert reloaded.path == (
        tmp_path / "machine_state" / "machine-a" / "fixture_reach.json"
    )
    assert reloaded.load_error is None
    assert reloaded.evidence.safe_travel_area_mm == (5.0, 245.0, 5.0, 215.0)


def test_fixture_reach_is_isolated_between_machine_ids(tmp_path: Path) -> None:
    first = FixtureReachStore(tmp_path, machine_id="machine-a")
    second = FixtureReachStore(tmp_path, machine_id="machine-b")
    first.save(_complete_evidence())

    assert first.path != second.path
    assert FixtureReachStore(
        tmp_path, machine_id="machine-a"
    ).evidence.complete
    assert not FixtureReachStore(
        tmp_path, machine_id="machine-b"
    ).evidence.complete
    assert not second.path.exists()


def test_fixture_reach_store_mutations_preserve_diagnostic_only_evidence(
    tmp_path: Path,
) -> None:
    store = FixtureReachStore(tmp_path, machine_id="machine-a")
    store.set_fixture_mode("permanent")
    store.record_limit(
        "x_min",
        value_mm=5.0,
        position_mm=(5.0, 195.0),
        machine_port="controller-a",
        protocol="grbl",
    )

    assert store.evidence.fixture_mode == "permanent"
    assert store.evidence.observations["x_min"]["source"] == (
        "trusted_jog_position"
    )
    assert not store.evidence.complete

    store.set_safe_travel_area(
        x_min_mm=5.0,
        x_max_mm=245.0,
        y_min_mm=5.0,
        y_max_mm=215.0,
        source="operator_entry",
        machine_port="controller-a",
        protocol="grbl",
    )
    assert store.evidence.complete
    assert store.clear_limits().fixture_mode == "permanent"
    assert not store.evidence.complete


def test_legacy_fixture_reach_is_claimed_once_without_changing_source(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "fixture_reach.json"
    legacy_bytes = (
        json.dumps(_complete_evidence().to_dict(), indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    legacy.write_bytes(legacy_bytes)

    first = FixtureReachStore(
        tmp_path,
        machine_id="physical-a",
        migrate_legacy=True,
    )
    second = FixtureReachStore(
        tmp_path,
        machine_id="physical-b",
        migrate_legacy=True,
    )

    assert first.evidence.complete
    assert first.path.read_bytes() == legacy_bytes
    assert legacy.read_bytes() == legacy_bytes
    claim = json.loads(first.migration_path.read_text(encoding="utf-8"))
    assert claim["claimed_machine_id"] == "physical-a"
    assert not second.evidence.complete
    assert not second.path.exists()


def test_existing_machine_evidence_is_not_clobbered_by_legacy_migration(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "fixture_reach.json"
    legacy.write_text(
        json.dumps(_complete_evidence().to_dict()),
        encoding="utf-8",
    )
    existing = FixtureReachStore(tmp_path, machine_id="physical-a")
    existing.set_fixture_mode("movable")
    before = existing.path.read_bytes()

    migrated = FixtureReachStore(
        tmp_path,
        machine_id="physical-a",
        migrate_legacy=True,
    )

    assert migrated.evidence.fixture_mode == "movable"
    assert migrated.path.read_bytes() == before
    assert not migrated.migration_path.exists()


@pytest.mark.parametrize(
    "payload",
    [
        '{"fixture_mode": "permanent",',
        json.dumps(
            {
                "fixture_mode": "permanent",
                "safe_travel_limits_mm": {"x_min": 10.0, "x_max": 5.0},
            }
        ),
        *(
            json.dumps({"safe_travel_limits_mm": value})
            for value in ([], "", 0, False)
        ),
        *(
            json.dumps({"observations": value})
            for value in ([], "", 0, False)
        ),
        json.dumps(
            {
                "safe_travel_limits_mm": {
                    "x_min": 5.0,
                    "unexpected_axis": 10.0,
                }
            }
        ),
    ],
)
def test_malformed_machine_evidence_fails_safe(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "machine_state" / "machine-a" / "fixture_reach.json"
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")

    store = FixtureReachStore(tmp_path, machine_id="machine-a")

    assert store.evidence.fixture_mode == "unclassified"
    assert store.load_error is not None
    assert path.read_text(encoding="utf-8") == payload


def test_malformed_migration_metadata_blocks_claim_and_copy(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "fixture_reach.json"
    legacy.write_text(
        json.dumps(_complete_evidence().to_dict()),
        encoding="utf-8",
    )
    metadata = tmp_path / "machine_state" / ".fixture_reach_legacy_claim.json"
    metadata.parent.mkdir(parents=True)
    original = b'{"schema_version": true, "claimed_machine_id": "other"}'
    metadata.write_bytes(original)

    store = FixtureReachStore(
        tmp_path,
        machine_id="physical-a",
        migrate_legacy=True,
    )

    assert not store.evidence.complete
    assert store.migration_error is not None
    assert "metadata is invalid" in store.migration_error
    assert not store.path.exists()
    assert metadata.read_bytes() == original


def test_valid_scoped_save_clears_a_stale_migration_error(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "fixture_reach.json"
    legacy_bytes = json.dumps(_complete_evidence().to_dict()).encode("utf-8")
    legacy.write_bytes(legacy_bytes)
    metadata = tmp_path / "machine_state" / ".fixture_reach_legacy_claim.json"
    metadata.parent.mkdir(parents=True)
    metadata_bytes = b'{"schema_version": false}'
    metadata.write_bytes(metadata_bytes)
    store = FixtureReachStore(
        tmp_path,
        machine_id="physical-a",
        migrate_legacy=True,
    )
    assert store.migration_error is not None

    saved = store.save(
        FixtureReachEvidence(fixture_mode="movable", x_min_mm=3.0)
    )

    assert saved.fixture_mode == "movable"
    assert store.load_error is None
    assert store.migration_error is None
    assert legacy.read_bytes() == legacy_bytes
    assert metadata.read_bytes() == metadata_bytes
    reloaded = FixtureReachStore(
        tmp_path,
        machine_id="physical-a",
        migrate_legacy=True,
    )
    assert reloaded.load_error is None
    assert reloaded.evidence.fixture_mode == "movable"


@pytest.mark.parametrize(
    "payload",
    [
        '{"fixture_mode": "permanent",',
        *(json.dumps(value) for value in ([], "", 0, False)),
        *(
            json.dumps({"safe_travel_limits_mm": value})
            for value in ([], "", 0, False)
        ),
        *(
            json.dumps({"observations": value})
            for value in ([], "", 0, False)
        ),
        json.dumps(
            {
                "safe_travel_limits_mm": {
                    "x_min": 5.0,
                    "unexpected_axis": 10.0,
                }
            }
        ),
    ],
)
def test_malformed_legacy_evidence_is_not_claimed(
    tmp_path: Path,
    payload: str,
) -> None:
    legacy = tmp_path / "fixture_reach.json"
    original = payload.encode("utf-8")
    legacy.write_bytes(original)

    store = FixtureReachStore(
        tmp_path,
        machine_id="physical-a",
        migrate_legacy=True,
    )

    assert store.migration_error is not None
    assert not store.path.exists()
    assert not store.migration_path.exists()
    assert legacy.read_bytes() == original


def test_fixture_reach_scope_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="machine_id"):
        FixtureReachStore(tmp_path, machine_id="../shared")
