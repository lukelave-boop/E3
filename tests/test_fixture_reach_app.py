from __future__ import annotations

import json
from pathlib import Path

import pytest

from laser_aligner.app import AppContext
from laser_aligner.config import load_settings
from laser_aligner.errors import CalibrationError, SafetyError


def _context(tmp_path: Path, *, laser_lockout: bool) -> AppContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
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
                    "backend": "simulator",
                    "allow_motion": True,
                    "photo_position": {"x": 110.0, "y": 110.0, "z": None},
                },
            }
        ),
        encoding="utf-8",
    )
    return AppContext(load_settings(config), laser_lockout=laser_lockout)


def test_app_records_permanent_fixture_limits_without_changing_settings(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, laser_lockout=True)
    original_work = context.settings.machine.work_area
    original_polygon = context.settings.laser.guarded_output_polygon_mm
    context.machine.connect()
    context.machine.prepare_photo_position()

    context.set_honeycomb_fixture_mode("permanent")
    recorded = context.record_honeycomb_safe_travel_limit("x_min")
    saved = context.save_honeycomb_safe_travel_area(
        x_min_mm=5.0,
        x_max_mm=245.0,
        y_min_mm=5.0,
        y_max_mm=215.0,
    )

    assert recorded["safe_travel_limits_mm"]["x_min"] == pytest.approx(110.0)
    assert saved["safe_travel_limits_mm"] == {
        "x_min": 5.0,
        "x_max": 245.0,
        "y_min": 5.0,
        "y_max": 215.0,
    }
    assert context.settings.machine.work_area is original_work
    assert context.settings.laser.guarded_output_polygon_mm is original_polygon
    assert (context.settings.app.data_dir / "fixture_reach.json").exists()


def test_app_reach_recording_requires_permanent_mode_and_lockout(
    tmp_path: Path,
) -> None:
    unlocked = _context(tmp_path / "unlocked", laser_lockout=False)
    unlocked.machine.connect()
    unlocked.machine.prepare_photo_position()
    unlocked.set_honeycomb_fixture_mode("permanent")

    with pytest.raises(SafetyError, match="--laser-lockout"):
        unlocked.record_honeycomb_safe_travel_limit("x_min")

    locked = _context(tmp_path / "locked", laser_lockout=True)
    locked.machine.connect()
    locked.machine.prepare_photo_position()
    with pytest.raises(CalibrationError, match="permanent fixture"):
        locked.record_honeycomb_safe_travel_limit("x_min")


def test_clearing_reach_limits_retains_permanent_classification(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, laser_lockout=True)
    context.set_honeycomb_fixture_mode("permanent")
    context.save_honeycomb_safe_travel_area(
        x_min_mm=5.0,
        x_max_mm=245.0,
        y_min_mm=5.0,
        y_max_mm=215.0,
    )

    cleared = context.clear_honeycomb_safe_travel_limits()

    assert cleared["fixture_mode"] == "permanent"
    assert all(
        value is None
        for value in cleared["safe_travel_limits_mm"].values()
    )
