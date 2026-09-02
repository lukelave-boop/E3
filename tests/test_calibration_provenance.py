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
                    "open_browser": False,
                },
                "camera": {"width": 800, "height": 600, "fps": 2},
                "calibration": {"bed": {"pixels_per_mm": 2}},
                "machine": {"backend": "serial"},
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


def _install_test_bed(context: AppContext) -> None:
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 599.0, 0.0, 0.0),
            BedPoint(799.0, 599.0, 220.0, 0.0),
            BedPoint(799.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        800,
        600,
        provenance=context._bed_provenance(),
    )


def _context(tmp_path: Path) -> AppContext:
    context = AppContext(_settings(tmp_path))
    _install_test_bed(context)
    context.bed_reference = lambda: np.zeros((600, 800, 3), dtype=np.uint8)  # type: ignore[method-assign]
    return context


def test_bed_provenance_survives_unchanged_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = AppContext(settings)
    _install_test_bed(first)
    first.start()
    try:
        assert first.bed_calibration_validity() == {
            "state": "VALID",
            "reasons": [],
            "reason_codes": [],
        }
    finally:
        first.stop()

    second = AppContext(_settings(tmp_path))
    second.start()
    try:
        assert second.bed_calibration_validity() == {
            "state": "VALID",
            "reasons": [],
            "reason_codes": [],
        }
    finally:
        second.stop()


def test_edited_bed_points_report_stale_model_until_resolved(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _install_test_bed(context)
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
        assert validity["reason_codes"] == ["bed_map.unavailable"]
        assert status["calibrated"] is False
        assert status["model_present"] is True
        with pytest.raises(CalibrationError, match="Bed calibration is STALE"):
            context.rectified_frame(refresh=True)

        context.solve_bed()
        assert context.bed_calibration_validity() == {
            "state": "VALID",
            "reasons": [],
            "reason_codes": [],
        }
    finally:
        context.stop()


def test_lens_or_focus_change_marks_same_resolution_bed_stale(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _install_test_bed(context)
    context.start()
    try:
        context.lens._model = _lens(0.0)
        context.solve_bed()
        assert context.bed_calibration_validity()["state"] == "VALID"

        context.lens._model = _lens(0.01)
        validity = context.bed_calibration_validity()
        assert validity["state"] == "STALE"
        assert "lens_model_id" in validity["reasons"][0]
        assert validity["reason_codes"] == ["bed_map.dependency_changed"]
        assert context.status()["bed"]["calibrated"] is False
        assert context.status()["bed"]["model_present"] is True
        with pytest.raises(CalibrationError, match="Bed calibration is STALE"):
            context.rectified_frame(refresh=True)

        context.lens._model = _lens(0.0)
        context.settings.camera.controls["focus_absolute"] = 99
        validity = context.bed_calibration_validity()
        assert validity["state"] == "STALE"
        assert "camera" in validity["reasons"][0]
        assert validity["reason_codes"] == ["bed_map.dependency_changed"]
    finally:
        context.stop()


def test_clearing_lens_blocks_bed_map_bound_to_that_model(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _install_test_bed(context)
    context.start()
    try:
        context.lens._model = _lens(0.0)
        context.solve_bed()
        context.lens.clear(delete_images=False)

        validity = context.bed_calibration_validity()
        assert validity["state"] == "STALE"
        assert validity["reason_codes"] == ["bed_map.dependency_changed"]
    finally:
        context.stop()


def test_legacy_bed_map_is_provenance_unknown_and_blocked(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = AppContext(settings)
    _install_test_bed(first)
    first.start()
    first.stop()
    payload = json.loads(first.bed.model_path.read_text(encoding="utf-8"))
    payload.pop("provenance")
    first.bed.model_path.write_text(json.dumps(payload), encoding="utf-8")

    second = AppContext(_settings(tmp_path))
    second.start()
    try:
        validity = second.bed_calibration_validity()
        assert validity["state"] == "UNKNOWN"
        assert validity["reason_codes"] == ["bed_map.legacy_provenance"]
        with pytest.raises(CalibrationError, match="Bed calibration is UNKNOWN"):
            second.rectified_frame(refresh=True)
    finally:
        second.stop()


def test_missing_bed_map_has_stable_diagnostic_reason(tmp_path: Path) -> None:
    context = AppContext(_settings(tmp_path))

    assert context.bed_calibration_validity() == {
        "state": "MISSING",
        "reasons": ["No bed map is installed"],
        "reason_codes": ["bed_map.missing"],
    }


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
        "registration_type",
        "residual_mesh_type",
        "refinement_base_type",
    ],
)
def test_bed_calibration_load_rejects_malformed_models(
    tmp_path: Path,
    mutation: str,
) -> None:
    context = _context(tmp_path)
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
    elif mutation == "registration_type":
        payload["fine_registration"] = []
    elif mutation == "residual_mesh_type":
        payload["residual_mesh"] = []
    elif mutation == "refinement_base_type":
        payload["fine_registration"]["homography_refinement"] = {
            "created_at": 1.0,
            "base_calibration": [],
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


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("image_width", True),
        ("image_height", 600.5),
        ("inlier_count", "8"),
        ("point_count", 8.0),
    ),
)
def test_bed_calibration_rejects_coerced_integer_metadata(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    context = _context(tmp_path)
    context.start()
    try:
        assert context.bed.calibration is not None
        payload = context.bed.calibration.to_dict()
    finally:
        context.stop()
    payload[field] = value

    with pytest.raises(ValueError, match=rf"{field} must be an integer"):
        BedCalibration.from_dict(payload)


def test_bed_calibration_rejects_nonfinite_provenance(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.start()
    try:
        assert context.bed.calibration is not None
        payload = context.bed.calibration.to_dict()
    finally:
        context.stop()
    payload["provenance"] = {"quality": float("nan")}

    with pytest.raises(ValueError, match="finite JSON"):
        BedCalibration.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("schema_version", 2, "Unsupported residual"),
        ("schema_version", "1", "schema_version must be an integer"),
        ("refinement_count", 0.0, "refinement_count must be an integer"),
    ),
)
def test_bed_calibration_rejects_malformed_residual_mesh_schema(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    context = _context(tmp_path)
    context.start()
    try:
        assert context.bed.calibration is not None
        payload = context.bed.calibration.to_dict()
    finally:
        context.stop()
    payload["residual_mesh"] = {
        "schema_version": 1,
        "x_nodes_mm": [0.0, 100.0],
        "y_nodes_mm": [0.0, 100.0],
        "corrections_mm": np.zeros((2, 2, 2), dtype=np.float64).tolist(),
        "created_at": 1.0,
        "fit_rms_mm": 0.1,
        "fit_max_mm": 0.2,
        "refinement_count": 0,
    }
    payload["residual_mesh"][field] = value

    with pytest.raises(ValueError, match=message):
        BedCalibration.from_dict(payload)
