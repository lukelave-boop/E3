from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.bed import BedCalibration, BedPoint
from laser_aligner.calibration.lens import LensModel
from laser_aligner.config import load_settings
from laser_aligner.errors import CalibrationError


def _settings(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "simulation": True,
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600, "fps": 2},
                "calibration": {"bed": {"pixels_per_mm": 2}},
                "machine": {"backend": "simulator"},
            }
        ),
        encoding="utf-8",
    )
    return load_settings(path)


def _lens(distortion: float) -> LensModel:
    return LensModel(
        camera_matrix=np.asarray(
            [[900.0, 0.0, 400.0], [0.0, 900.0, 300.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        ),
        distortion=np.asarray([distortion, 0.0, 0.0, 0.0, 0.0]),
        image_width=800,
        image_height=600,
        rms_error=0.4,
        mean_reprojection_error=0.3,
        images_used=12,
        created_at=1.0,
    )


def test_bed_provenance_survives_unchanged_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = AppContext(settings)
    first.start()
    try:
        assert first.bed_calibration_validity() == {"state": "VALID", "reasons": []}
    finally:
        first.stop()

    second = AppContext(_settings(tmp_path))
    second.start()
    try:
        assert second.bed_calibration_validity() == {"state": "VALID", "reasons": []}
        assert second.rectified_frame(refresh=True).shape[:2] == (440, 440)
    finally:
        second.stop()


def test_edited_bed_points_report_stale_model_until_resolved(tmp_path: Path) -> None:
    context = AppContext(_settings(tmp_path))
    context.start()
    try:
        first = context.bed.points[0]
        context.bed.add_point(
            BedPoint(
                first.image_x,
                first.image_y,
                first.machine_x,
                first.machine_y,
                "edited generation",
            )
        )

        validity = context.bed_calibration_validity()
        status = context.bed_status()
        assert validity["state"] == "STALE"
        assert "points changed" in validity["reasons"][0]
        assert status["calibrated"] is False
        assert status["model_present"] is True
        with pytest.raises(CalibrationError, match="Bed calibration is STALE"):
            context.rectified_frame(refresh=True)

        context.solve_bed()
        assert context.bed_calibration_validity() == {"state": "VALID", "reasons": []}
    finally:
        context.stop()


def test_lens_or_focus_change_marks_same_resolution_bed_stale(tmp_path: Path) -> None:
    context = AppContext(_settings(tmp_path))
    context.start()
    try:
        context.lens._model = _lens(0.0)
        context.solve_bed()
        assert context.bed_calibration_validity()["state"] == "VALID"

        context.lens._model = _lens(0.01)
        validity = context.bed_calibration_validity()
        assert validity["state"] == "STALE"
        assert "lens_model_id" in validity["reasons"][0]
        assert context.status()["bed"]["calibrated"] is False
        assert context.status()["bed"]["model_present"] is True
        with pytest.raises(CalibrationError, match="Bed calibration is STALE"):
            context.rectified_frame(refresh=True)

        context.lens._model = _lens(0.0)
        context.settings.camera.controls["focus_absolute"] = 99
        validity = context.bed_calibration_validity()
        assert validity["state"] == "STALE"
        assert "camera" in validity["reasons"][0]
    finally:
        context.stop()


def test_clearing_lens_blocks_bed_map_bound_to_that_model(tmp_path: Path) -> None:
    context = AppContext(_settings(tmp_path))
    context.start()
    try:
        context.lens._model = _lens(0.0)
        context.solve_bed()
        context.lens.clear(delete_images=False)

        assert context.bed_calibration_validity()["state"] == "STALE"
    finally:
        context.stop()


def test_legacy_bed_map_is_provenance_unknown_and_blocked(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = AppContext(settings)
    first.start()
    first.stop()
    payload = json.loads(first.bed.model_path.read_text(encoding="utf-8"))
    payload.pop("provenance")
    first.bed.model_path.write_text(json.dumps(payload), encoding="utf-8")

    second = AppContext(_settings(tmp_path))
    second.start()
    try:
        assert second.bed_calibration_validity()["state"] == "UNKNOWN"
        with pytest.raises(CalibrationError, match="Bed calibration is UNKNOWN"):
            second.rectified_frame(refresh=True)
    finally:
        second.stop()


@pytest.mark.parametrize(
    "mutation",
    [
        "shape",
        "inverse",
        "axis",
        "registration",
        "registration_time",
        "backup_metrics",
        "mesh_metadata",
        "mesh_bound",
    ],
)
def test_bed_calibration_load_rejects_malformed_models(
    tmp_path: Path,
    mutation: str,
) -> None:
    context = AppContext(_settings(tmp_path))
    context.start()
    try:
        assert context.bed.calibration is not None
        payload = context.bed.calibration.to_dict()
    finally:
        context.stop()

    if mutation == "shape":
        payload["image_to_machine"] = [[1.0, 0.0], [0.0, 1.0]]
    elif mutation == "inverse":
        payload["machine_to_image"][0][2] += 10.0
    elif mutation == "axis":
        payload["axis_mapping"]["reverse_x"] = "false"
    elif mutation == "registration":
        payload["fine_registration"]["translation_x_mm"] = 6.0
    elif mutation == "registration_time":
        payload["fine_registration"]["created_at"] = float("nan")
    elif mutation == "backup_metrics":
        backup = {
            key: payload[key]
            for key in (
                "image_to_machine",
                "machine_to_image",
                "rms_error_mm",
                "max_error_mm",
                "inlier_count",
                "point_count",
                "created_at",
            )
        }
        backup["rms_error_mm"] = float("nan")
        payload["fine_registration"]["homography_refinement"] = {
            "created_at": 1.0,
            "base_calibration": backup,
        }
    else:
        corrections = np.zeros((2, 2, 2), dtype=np.float64)
        if mutation == "mesh_bound":
            corrections[0, 0, 0] = 4.0
        payload["residual_mesh"] = {
            "schema_version": 1,
            "x_nodes_mm": [0.0, 100.0],
            "y_nodes_mm": [0.0, 100.0],
            "corrections_mm": corrections.tolist(),
            "created_at": 1.0,
            "fit_rms_mm": (float("nan") if mutation == "mesh_metadata" else 0.1),
            "fit_max_mm": 0.2,
            "refinement_count": 0,
        }

    with pytest.raises(ValueError):
        BedCalibration.from_dict(payload)
