import json
from pathlib import Path

import pytest

from laser_aligner import config as config_module
from laser_aligner.config import (
    ConfigError,
    PrecisionCaptureSettings,
    WorkArea,
    effective_laser_output_area,
    load_settings,
)


def test_effective_laser_output_area_intersects_margin_and_spot_offset() -> None:
    result = effective_laser_output_area(
        WorkArea(10.0, 210.0, 20.0, 200.0),
        5.0,
        spot_offset_x_mm=-2.0,
        spot_offset_y_mm=3.0,
    )

    assert (result.x_min, result.x_max, result.y_min, result.y_max) == (
        15.0,
        203.0,
        28.0,
        195.0,
    )


@pytest.mark.parametrize(
    ("margin", "offset_x", "offset_y"),
    [
        (float("nan"), 0.0, 0.0),
        (-1.0, 0.0, 0.0),
        (5.0, 500.0, 0.0),
    ],
)
def test_effective_laser_output_area_rejects_invalid_or_empty_limits(
    margin: float,
    offset_x: float,
    offset_y: float,
) -> None:
    with pytest.raises(ValueError, match="finite|non-negative|no guarded"):
        effective_laser_output_area(
            WorkArea(0.0, 100.0, 0.0, 100.0),
            margin,
            offset_x,
            offset_y,
        )


def test_parked_bed_precision_defaults_use_large_stable_consensus_burst() -> None:
    profile = PrecisionCaptureSettings()
    assert profile.sample_frames == 45
    assert profile.minimum_valid_frames == 15
    assert profile.timeout_seconds == pytest.approx(8.0)
    assert profile.coordinate_strategy == "stable_clarity_consensus"
    assert profile.consensus_frames == 15


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
    assert settings.machine.home_and_release_after_powered_job
    assert settings.machine.grbl_step_idle_delay_ms == 250
    assert settings.machine.max_travel_feed_mm_min == pytest.approx(6000.0)
    assert settings.machine.max_work_feed_mm_min == pytest.approx(6000.0)
    assert settings.laser.spot_offset_x_mm == 0.0
    assert settings.laser.spot_offset_y_mm == 0.0
    assert (settings.app.data_dir / "captures").is_dir()


def test_builtin_install_defaults_use_writable_user_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = tmp_path / "site-packages" / "laser_aligner" / "config.py"
    user_data = tmp_path / "user-data" / "e3"
    monkeypatch.setattr(config_module, "__file__", str(fake_module))
    monkeypatch.setattr(config_module, "_default_user_data_dir", lambda: user_data)

    settings = config_module.load_settings()

    assert settings.app.data_dir == user_data
    assert (user_data / "captures").is_dir()


@pytest.mark.parametrize(
    "app_override",
    [
        {"host": "192.168.1.50"},
        {"allow_remote_control": True},
    ],
)
def test_http_configuration_is_explicitly_local_only(
    tmp_path: Path,
    app_override: dict[str, object],
) -> None:
    config = tmp_path / "remote-http.json"
    config.write_text(json.dumps({"app": app_override}), encoding="utf-8")

    with pytest.raises(ConfigError, match="local-only"):
        load_settings(config)


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("app", "simulation"),
        ("camera", "autostart"),
        ("machine", "allow_motion"),
        ("laser", "allow_low_power_frame"),
    ],
)
def test_string_booleans_are_rejected(
    tmp_path: Path,
    section: str,
    key: str,
) -> None:
    config = tmp_path / f"bad-{section}-{key}.json"
    config.write_text(
        json.dumps({section: {key: "false"}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=rf"{section}\.{key} must be a JSON boolean"):
        load_settings(config)


def test_configured_feeds_must_fit_machine_ceilings(tmp_path: Path) -> None:
    config = tmp_path / "bad-feed-ceiling.json"
    config.write_text(
        json.dumps(
            {
                "machine": {
                    "max_travel_feed_mm_min": 2000,
                    "max_work_feed_mm_min": 1000,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="travel_feed_mm_min cannot exceed"):
        load_settings(config)


def test_invalid_work_area_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text(
        json.dumps({"machine": {"work_area": {"x_min": 10, "x_max": 10, "y_min": 0, "y_max": 100}}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(config)


@pytest.mark.parametrize(
    "override",
    [
        {"machine": {"work_area": {"x_min": float("-inf")}}},
        {"machine": {"max_work_feed_mm_min": float("nan")}},
        {"laser": {"travel_feed_mm_min": float("inf")}},
    ],
)
def test_nonfinite_configuration_numbers_are_rejected(
    tmp_path: Path,
    override: dict[str, object],
) -> None:
    config = tmp_path / "nonfinite.json"
    config.write_text(json.dumps(override), encoding="utf-8")

    with pytest.raises(ConfigError, match="must be finite"):
        load_settings(config)


@pytest.mark.parametrize(
    "override",
    [
        {"machine": {"max_travel_feed_mm_min": "nan"}},
        {"machine": {"max_work_feed_mm_min": "inf"}},
        {"machine": {"work_area": {"x_min": "-inf"}}},
        {"camera": {"precision_capture": {"max_jitter_rms_px": "nan"}}},
        {"camera": {"precision_capture": {"settle_seconds": "-inf"}}},
        {"calibration": {"lens": {"square_size_mm": "nan"}}},
        {"calibration": {"bed": {"ransac_threshold_mm": "inf"}}},
        {"laser": {"curve_tolerance_mm": "nan"}},
        {"vision": {"workpiece_min_area_ratio": "-inf"}},
    ],
)
def test_string_nonfinite_configuration_numbers_are_rejected(
    tmp_path: Path,
    override: dict[str, object],
) -> None:
    config = tmp_path / "string-nonfinite.json"
    config.write_text(json.dumps(override), encoding="utf-8")

    with pytest.raises(ConfigError, match="must be a finite JSON number"):
        load_settings(config)


@pytest.mark.parametrize(
    "override",
    [
        {"camera": {"width": "1920"}},
        {"laser": {"power_max": 1000.0}},
        {"machine": {"max_work_feed_mm_min": "6000"}},
    ],
)
def test_numeric_configuration_requires_json_number_types(
    tmp_path: Path,
    override: dict[str, object],
) -> None:
    config = tmp_path / "coerced-number.json"
    config.write_text(json.dumps(override), encoding="utf-8")

    with pytest.raises(ConfigError, match="must be a (finite JSON number|JSON integer)"):
        load_settings(config)


def test_continuous_grbl_idle_delay_is_reserved_for_scoped_camera_hold(
    tmp_path: Path,
) -> None:
    config = tmp_path / "continuous-hold.json"
    config.write_text(
        json.dumps({"machine": {"grbl_step_idle_delay_ms": 255}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="between 0 and 254"):
        load_settings(config)


@pytest.mark.parametrize("timeout", [0, 601])
def test_arm_timeout_must_remain_bounded(
    tmp_path: Path,
    timeout: int,
) -> None:
    config = tmp_path / "arm-timeout.json"
    config.write_text(
        json.dumps({"laser": {"arm_timeout_seconds": timeout}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="between 1 and 600 seconds"):
        load_settings(config)


@pytest.mark.parametrize("timeout", [1, 600])
def test_arm_timeout_accepts_bounded_endpoints(
    tmp_path: Path,
    timeout: int,
) -> None:
    config = tmp_path / "arm-timeout.json"
    config.write_text(
        json.dumps({"laser": {"arm_timeout_seconds": timeout}}),
        encoding="utf-8",
    )

    assert load_settings(config).laser.arm_timeout_seconds == timeout


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
                        "coordinate_strategy": "median",
                        "consensus_frames": 5,
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
    assert profile.coordinate_strategy == "median"
    assert profile.consensus_frames == 5
    assert settings.public_dict()["camera"]["precision_capture"]["max_jitter_rms_px"] == pytest.approx(0.6)


@pytest.mark.parametrize(
    "override",
    [
        {"sample_frames": 0},
        {"sample_frames": 5, "minimum_valid_frames": 6},
        {"timeout_seconds": 0},
        {"settle_seconds": -0.1},
        {"max_jitter_rms_px": 0},
        {"coordinate_strategy": "unknown"},
        {"sample_frames": 5, "consensus_frames": 6},
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
