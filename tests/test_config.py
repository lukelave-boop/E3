import json
from pathlib import Path

import pytest

from laser_aligner.config import ConfigError, load_settings


def test_load_partial_config_and_relative_data_dir(tmp_path: Path) -> None:
    config = tmp_path / "local.json"
    config.write_text(
        json.dumps(
            {
                "app": {"data_dir": "runtime", "simulation": True},
                "camera": {"width": 1280, "height": 720},
                "machine": {"work_area": {"x_min": 5, "x_max": 205, "y_min": 10, "y_max": 210}},
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(config)
    assert settings.app.data_dir == (tmp_path / "runtime").resolve()
    assert settings.camera.width == 1280
    assert settings.machine.work_area.width == 200
    assert settings.laser.spot_offset_x_mm == 0.0
    assert settings.laser.spot_offset_y_mm == 0.0
    assert (settings.app.data_dir / "captures").is_dir()


def test_invalid_work_area_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text(
        json.dumps({"machine": {"work_area": {"x_min": 10, "x_max": 10, "y_min": 0, "y_max": 100}}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(config)


def test_laser_spot_offsets_load_and_excessive_values_are_rejected(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "offset.json"
    valid.write_text(
        json.dumps(
            {
                "machine": {
                    "work_area": {
                        "x_min": 10,
                        "x_max": 210,
                        "y_min": 10,
                        "y_max": 210,
                    }
                },
                "laser": {
                    "spot_offset_x_mm": -28,
                    "spot_offset_y_mm": -8,
                },
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(valid)
    assert settings.laser.spot_offset_x_mm == -28.0
    assert settings.laser.spot_offset_y_mm == -8.0

    invalid = tmp_path / "unsafe-offset.json"
    invalid.write_text(
        json.dumps({"laser": {"spot_offset_x_mm": 221}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="work-area span"):
        load_settings(invalid)


@pytest.mark.parametrize(
    ("laser_override", "message"),
    [
        ({"preview_acceleration_mm_s2": 0}, "must be positive"),
        ({"preview_command_delay_ms": -1}, "cannot be negative"),
    ],
)
def test_invalid_preview_timing_model_is_rejected(
    tmp_path: Path,
    laser_override: dict[str, float],
    message: str,
) -> None:
    config = tmp_path / "bad-preview-timing.json"
    config.write_text(json.dumps({"laser": laser_override}), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_settings(config)


def test_preview_timing_model_is_exposed_in_public_settings(tmp_path: Path) -> None:
    config = tmp_path / "preview-timing.json"
    config.write_text(
        json.dumps(
            {
                "laser": {
                    "preview_acceleration_mm_s2": 420.0,
                    "preview_command_delay_ms": 12.0,
                }
            }
        ),
        encoding="utf-8",
    )

    public = load_settings(config).public_dict()["laser"]

    assert public["preview_acceleration_mm_s2"] == 420.0
    assert public["preview_command_delay_ms"] == 12.0


def test_precision_capture_settings_load_and_are_public(tmp_path: Path) -> None:
    config = tmp_path / "precision.json"
    config.write_text(
        json.dumps(
            {
                "camera": {
                    "precision_capture": {
                        "settle_seconds": 0.75,
                        "discard_frames": 4,
                        "sample_frames": 11,
                        "timeout_seconds": 6.0,
                        "minimum_valid_frames": 7,
                        "mad_multiplier": 4.0,
                        "outlier_floor_px": 0.2,
                        "max_jitter_rms_px": 0.6,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_settings(config)
    profile = settings.camera.precision_capture
    assert profile.sample_frames == 11
    assert profile.minimum_valid_frames == 7
    assert profile.settle_seconds == pytest.approx(0.75)
    assert settings.public_dict()["camera"]["precision_capture"][
        "max_jitter_rms_px"
    ] == pytest.approx(0.6)


@pytest.mark.parametrize(
    "override",
    [
        {"sample_frames": 0},
        {"sample_frames": 5, "minimum_valid_frames": 6},
        {"timeout_seconds": 0},
        {"settle_seconds": -0.1},
        {"max_jitter_rms_px": 0},
    ],
)
def test_invalid_precision_capture_settings_are_rejected(
    tmp_path: Path,
    override: dict[str, float | int],
) -> None:
    config = tmp_path / "bad-precision.json"
    config.write_text(
        json.dumps({"camera": {"precision_capture": override}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="precision_capture"):
        load_settings(config)
