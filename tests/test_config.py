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
    assert (settings.app.data_dir / "captures").is_dir()


def test_invalid_work_area_is_rejected(tmp_path: Path) -> None:
    config = tmp_path / "bad.json"
    config.write_text(
        json.dumps({"machine": {"work_area": {"x_min": 10, "x_max": 10, "y_min": 0, "y_max": 100}}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(config)
