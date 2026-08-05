import json
from pathlib import Path

from laser_aligner.app import AppContext
from laser_aligner.config import load_settings


def test_simulation_auto_maps_bed_and_generates_workspace(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "simulation": True, "open_browser": False},
                "camera": {"width": 800, "height": 600, "fps": 2},
                "calibration": {"bed": {"pixels_per_mm": 2}},
                "machine": {"backend": "simulator"},
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    context = AppContext(settings)
    context.start()
    try:
        assert context.bed.calibration is not None
        assert context.machine.connected
        image = context.rectified_frame(refresh=True)
        assert image.shape[:2] == (440, 440)
        detection = context.detect_workpiece()
        assert detection["detected"]
    finally:
        context.stop()
