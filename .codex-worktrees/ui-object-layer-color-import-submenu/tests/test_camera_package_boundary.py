from __future__ import annotations

import importlib.util
from pathlib import Path

import laser_aligner.camera as camera


def test_production_camera_package_has_no_test_image_api() -> None:
    package_root = Path(camera.__file__).resolve().parent

    assert not (package_root / "test_frame.py").exists()
    assert importlib.util.find_spec("laser_aligner.camera.test_frame") is None
    for name in (
        "corrected_frame_size",
        "load_corrected_test_image",
        "prepare_corrected_test_image",
    ):
        assert not hasattr(camera, name)
        assert name not in camera.__all__
