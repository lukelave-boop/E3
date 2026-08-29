from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


def test_camera_raster_diagnostic_script_writes_exact_stage_artifacts(
    tmp_path: Path,
) -> None:
    image = np.full((100, 160, 3), 225, dtype=np.uint8)
    cv2.rectangle(image, (50, 25), (110, 75), (25, 25, 25), -1)
    frame = tmp_path / "local-frame.png"
    output_dir = tmp_path / "diagnostics"
    assert cv2.imwrite(str(frame), image)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/diagnose_camera_trace_raster.py",
            str(frame),
            "--pixels-per-mm",
            "4",
            "--threshold",
            "manual",
            "--threshold-value",
            "128",
            "--minimum-area-mm2",
            "0.5",
            "--output-dir",
            str(output_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["polarity"] == "dark"
    assert payload["threshold_used"] == 128
    assert payload["connected_component_count"] == 1
    for name in ("corrected", "background", "normalized"):
        artifact = cv2.imread(str(output_dir / f"{name}.png"), cv2.IMREAD_UNCHANGED)
        assert artifact is not None
        assert artifact.shape[:2] == image.shape[:2]
    mask = cv2.imread(str(output_dir / "mask.png"), cv2.IMREAD_GRAYSCALE)
    assert mask is not None
    assert mask.shape == (image.shape[0] * 4, image.shape[1] * 4)
    assert mask[200, 320] == 255
    assert mask[40, 40] == 0
    saved = json.loads((output_dir / "diagnostics.json").read_text(encoding="utf-8"))
    assert saved == payload
    assert saved["normalized_shape_px"] == [160, 100]
    assert saved["contour_mask_shape_px"] == [640, 400]
    assert saved["normalization_timing"]["background_estimation"]["calls"] == 1
    assert saved["mask_timing"]["mask_preparation_total"]["calls"] == 1
