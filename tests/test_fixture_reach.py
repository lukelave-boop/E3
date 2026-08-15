from __future__ import annotations

import json
from pathlib import Path

import pytest

from laser_aligner.calibration.reach import (
    FixtureReachEvidence,
    FixtureReachStore,
    build_fixture_reachability,
    intersect_convex_polygons,
    polygon_area,
)
from laser_aligner.calibration.support import HoneycombSupportReference
from laser_aligner.config import load_settings


def _settings(tmp_path: Path, *, work_max: float = 210.0):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "simulation": True,
                    "open_browser": False,
                },
                "machine": {
                    "work_area": {
                        "x_min": 10.0,
                        "x_max": work_max,
                        "y_min": 10.0,
                        "y_max": work_max,
                    }
                },
                "calibration": {"bed": {"honeycomb_span_mm": 191.0}},
                "laser": {
                    "boundary_margin_mm": 5.0,
                    "guarded_output_polygon_mm": [
                        [18.218005, 29.679375],
                        [228.217364, 30.198421],
                        [227.698319, 240.197779],
                        [17.698960, 239.678734],
                    ],
                    "spot_offset_x_mm": 0.0,
                    "spot_offset_y_mm": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    return load_settings(path)


def _support() -> HoneycombSupportReference:
    return HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            (27.9795839024, 39.2569965679),
            (218.3147283051, 38.9210588812),
            (217.3546446865, 229.7653483327),
            (27.5947133865, 229.9231820650),
        ),
        corner_topology=(0, 1, 2, 3),
        support_width_mm=191.0,
        support_height_mm=191.0,
        bed_calibration_created_at=7.0,
    )


def test_fixture_reach_store_round_trips_permanent_partial_and_complete(
    tmp_path: Path,
) -> None:
    store = FixtureReachStore(tmp_path)

    assert store.evidence.fixture_mode == "unclassified"
    assert store.evidence.safe_travel_area_mm is None

    store.set_fixture_mode("permanent")
    store.record_limit(
        "x_min",
        value_mm=5.0,
        position_mm=(5.0, 195.0),
        machine_port="/dev/controller",
        protocol="grbl",
    )
    assert store.evidence.fixture_mode == "permanent"
    assert not store.evidence.complete
    assert store.evidence.observations["x_min"]["source"] == (
        "trusted_jog_position"
    )

    store.set_safe_travel_area(
        x_min_mm=5.0,
        x_max_mm=245.0,
        y_min_mm=5.0,
        y_max_mm=215.0,
        source="operator_entry",
        machine_port="/dev/controller",
        protocol="grbl",
    )
    reloaded = FixtureReachStore(tmp_path)

    assert reloaded.load_error is None
    assert reloaded.evidence.fixture_mode == "permanent"
    assert reloaded.evidence.safe_travel_area_mm == (5.0, 245.0, 5.0, 215.0)
    assert set(reloaded.evidence.observations) == {
        "x_min",
        "x_max",
        "y_min",
        "y_max",
    }


def test_fixture_reach_evidence_rejects_inverted_limits() -> None:
    with pytest.raises(ValueError, match="x_max_mm"):
        FixtureReachEvidence(x_min_mm=10.0, x_max_mm=5.0)
    with pytest.raises(ValueError, match="fixture_mode"):
        FixtureReachEvidence(fixture_mode="fixed")


def test_convex_intersection_and_area_are_stable() -> None:
    first = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
    second = ((5.0, -2.0), (12.0, -2.0), (12.0, 8.0), (5.0, 8.0))

    intersection = intersect_convex_polygons(first, second)

    assert polygon_area(intersection) == pytest.approx(40.0)
    assert set(intersection) == {
        (5.0, 0.0),
        (10.0, 0.0),
        (10.0, 8.0),
        (5.0, 8.0),
    }


def test_permanent_fixture_reports_registration_separately_from_reach(
    tmp_path: Path,
) -> None:
    status = build_fixture_reachability(
        _settings(tmp_path),
        support_reference=_support(),
        evidence=FixtureReachEvidence(fixture_mode="permanent"),
        grbl_settings={"20": 0.0, "21": 0.0, "130": 220.0, "131": 220.0},
    )

    assert status["state"] == "NOT_MEASURED"
    assert status["permanent_fixture"] is True
    assert status["configured_work"]["coverage_percent"] == pytest.approx(
        85.646984, abs=1e-5
    )
    assert status["guarded_output"]["coverage_percent"] == pytest.approx(100.0)
    assert status["controller_settings"]["soft_limits_enabled"] is False
    assert status["controller_settings"]["hard_limits_enabled"] is False
    assert any("do not reposition" in reason for reason in status["reasons"])


def test_measured_limits_report_partial_fixed_fixture_and_unsafe_polygon(
    tmp_path: Path,
) -> None:
    status = build_fixture_reachability(
        _settings(tmp_path),
        support_reference=_support(),
        evidence=FixtureReachEvidence(
            fixture_mode="permanent",
            x_min_mm=5.0,
            x_max_mm=245.0,
            y_min_mm=5.0,
            y_max_mm=215.0,
        ),
    )

    assert status["state"] == "PARTIAL"
    assert status["measured_travel"]["coverage_percent"] == pytest.approx(
        92.164086, abs=1e-5
    )
    assert status["combined"]["coverage_percent"] == pytest.approx(
        85.646984, abs=1e-5
    )
    assert status["output_authority_within_measured_travel"]["within"] is False
    assert status["output_authority_within_measured_travel"][
        "maximum_escape_mm"
    ] == pytest.approx(25.197779, abs=1e-6)


def test_full_fixture_reach_requires_all_recorded_and_configured_authorities(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, work_max=250.0)
    settings.laser.guarded_output_polygon_mm = (
        (0.0, 0.0),
        (250.0, 0.0),
        (250.0, 250.0),
        (0.0, 250.0),
    )
    status = build_fixture_reachability(
        settings,
        support_reference=_support(),
        evidence=FixtureReachEvidence(
            fixture_mode="permanent",
            x_min_mm=0.0,
            x_max_mm=250.0,
            y_min_mm=0.0,
            y_max_mm=250.0,
        ),
    )

    assert status["state"] == "FULL"
    assert status["combined"]["full_support"] is True
    assert status["combined"]["coverage_percent"] == pytest.approx(100.0)


def test_fixture_reach_store_surfaces_invalid_saved_evidence(tmp_path: Path) -> None:
    path = tmp_path / "fixture_reach.json"
    path.write_text('{"fixture_mode": "permanent",', encoding="utf-8")

    store = FixtureReachStore(tmp_path)

    assert store.evidence.fixture_mode == "unclassified"
    assert store.load_error is not None
    assert "invalid" in store.load_error.lower()


def test_reach_evidence_warns_when_controller_identity_changes(tmp_path: Path) -> None:
    evidence = FixtureReachEvidence(
        fixture_mode="permanent",
        x_min_mm=5.0,
        x_max_mm=245.0,
        y_min_mm=5.0,
        y_max_mm=215.0,
        observations={
            key: {
                "machine_port": "/dev/serial/by-id/old-controller",
                "protocol": "grbl",
            }
            for key in ("x_min", "x_max", "y_min", "y_max")
        },
    )

    status = build_fixture_reachability(
        _settings(tmp_path),
        support_reference=_support(),
        evidence=evidence,
    )

    assert status["evidence_matches_current_machine"] is False
    assert any("different controller" in reason for reason in status["reasons"])
