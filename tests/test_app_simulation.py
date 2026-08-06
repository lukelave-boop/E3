import json
from pathlib import Path

import numpy as np
import pytest

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


def _settings(
    tmp_path: Path,
    *,
    simulation: bool = True,
    machine_backend: str = "simulator",
):
    config_path = tmp_path / f"config-{simulation}-{machine_backend}.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": f"data-{simulation}-{machine_backend}",
                    "simulation": simulation,
                    "open_browser": False,
                },
                "camera": {"autostart": False},
                "calibration": {"bed": {"pixels_per_mm": 2}},
                "machine": {"backend": machine_backend},
            }
        ),
        encoding="utf-8",
    )
    return load_settings(config_path)


def test_simulation_workspace_frame_is_memory_only_and_copy_isolated(
    tmp_path: Path,
) -> None:
    context = AppContext(_settings(tmp_path))
    image = np.full((440, 440, 3), (25, 80, 210), dtype=np.uint8)

    info = context.set_simulation_workspace_frame(
        image,
        source_name="generated Alpha labels",
        metadata={"center_x_mm": 117.0, "rotation_deg": 8.0},
    )
    image[:] = 0
    first = context.rectified_frame(refresh=True)
    first[:] = 1
    second = context.rectified_frame(refresh=False)

    assert info["source_name"] == "generated Alpha labels"
    assert info["width"] == 440
    assert context.has_simulation_workspace_frame
    assert tuple(second[0, 0]) == (25, 80, 210)
    assert context.simulation_workspace_frame_info()["metadata"] == {
        "center_x_mm": 117.0,
        "rotation_deg": 8.0,
    }
    assert context.simulation_workspace_frame_status() == {
        "active": True,
        "source_name": "generated Alpha labels",
        "width": 440,
        "height": 440,
        "pixels_per_mm": 2.0,
    }
    assert "metadata" not in context.status()["simulation_workspace_frame"]
    assert not context.workspace_path.exists()

    context.clear_simulation_workspace_frame()
    assert not context.has_simulation_workspace_frame
    assert context.simulation_workspace_frame_info() is None
    assert context.simulation_workspace_frame_status() is None


@pytest.mark.parametrize(
    ("simulation", "machine_backend", "hardware_enabled"),
    (
        (False, "simulator", False),
        (True, "serial", False),
        (True, "simulator", True),
    ),
)
def test_simulation_workspace_frame_enforces_the_full_safety_gate(
    tmp_path: Path,
    simulation: bool,
    machine_backend: str,
    hardware_enabled: bool,
) -> None:
    context = AppContext(
        _settings(
            tmp_path,
            simulation=simulation,
            machine_backend=machine_backend,
        ),
        hardware_enabled=hardware_enabled,
    )

    with pytest.raises(RuntimeError, match="Test images require simulation mode"):
        context.set_simulation_workspace_frame(
            np.zeros((440, 440, 3), dtype=np.uint8),
            source_name="unsafe",
        )


def test_simulation_workspace_frame_rejects_wrong_corrected_dimensions(
    tmp_path: Path,
) -> None:
    context = AppContext(_settings(tmp_path))

    with pytest.raises(ValueError, match="expected 440x440, got 640x480"):
        context.set_simulation_workspace_frame(
            np.zeros((480, 640, 3), dtype=np.uint8),
            source_name="wrong size",
        )
