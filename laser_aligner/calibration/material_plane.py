"""Pure, immutable material-plane selection and camera geometry diagnostics.

Passing these numerical gates is not physical qualification or motion authority.
The caller must supply a freshly validated machine measurement and independently
qualify actual placement. A stored selection is provenance, never live authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, replace
from typing import Any

import cv2
import numpy as np

from ..errors import CalibrationError
from .bed import BedCalibration
from .lens import LensModel
from .surface import SurfaceHeightModel

PRECISION_TARGET_MM = 0.1
MAX_MATERIAL_ELEVATION_MM = 20.0


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CalibrationError(f"{label} must be a finite number")
    return float(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise CalibrationError(f"{label} requires a nonempty identity")
    return value


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CalibrationError("Material-plane provenance must contain finite JSON values") from exc


def binding_digest(binding: dict[str, Any]) -> str:
    return hashlib.sha256(_json(binding).encode("utf-8")).hexdigest()


def _pose(value: Any) -> tuple[float, float, float | None]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise CalibrationError("Capture pose must contain X, Y and Z (or null for fixed-camera Z)")
    return (
        _number(value[0], "Capture X"), _number(value[1], "Capture Y"),
        None if value[2] is None else _number(value[2], "Capture Z"),
    )


def validate_production_binding(binding: Any, *, reference: str | None = None) -> None:
    if not isinstance(binding, dict):
        raise CalibrationError("Material-plane production provenance is required")
    for key in ("machine_id", "calibration_profile", "datum_id", "mount_revision"):
        _text(binding.get(key), key)
    if reference is not None and binding["datum_id"] != reference:
        raise CalibrationError("Surface observations and production datum identity differ")
    geometry = binding.get("camera_geometry")
    if not isinstance(geometry, dict) or not geometry:
        raise CalibrationError("Material-plane camera geometry provenance is required")
    _pose(binding.get("capture_pose"))
    _json(binding)


def require_precision_model(model: SurfaceHeightModel) -> None:
    """Require new evidence and <=0.1 mm numerical fit/holdout error only."""
    if not isinstance(model, SurfaceHeightModel) or model.evidence_schema_version != 2:
        raise CalibrationError("Legacy height studies are diagnostic only; reacquire production evidence")
    try:
        binding = json.loads(model.binding_json)
    except (ValueError, TypeError) as exc:
        raise CalibrationError("Invalid material-plane model provenance") from exc
    validate_production_binding(binding, reference=model.reference)
    if len(model.evidence_source_ids) != 3 or len(set(model.evidence_source_ids)) != 3:
        raise CalibrationError("Independent lower, upper and middle camera evidence is required")
    for identity in (model.model_id, *model.evidence_source_ids):
        if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise CalibrationError("Material-plane evidence has an invalid source identity")
    if not model.check_passed:
        raise CalibrationError("Material-plane correction requires a passing independent middle-height check")
    for label, value in (
        ("Fit RMS", model.fit_rms_mm), ("Fit maximum", model.fit_max_mm),
        ("Independent RMS", model.check_rms_mm), ("Independent maximum", model.check_max_mm),
    ):
        error = _number(value, label)
        if not 0 <= error <= PRECISION_TARGET_MM:
            raise CalibrationError(f"{label} exceeds the {PRECISION_TARGET_MM:g} mm precision target")
    model.homographies(model.lower_mm)
    model.homographies(model.upper_mm)


@dataclass(frozen=True, slots=True)
class MaterialPlaneSelection:
    model: SurfaceHeightModel
    datum_id: str
    binding_id: str
    capture_pose: tuple[float, float, float | None]
    plan_id: str
    measurement_id: str
    elevation_mm: float
    honeycomb_height_mm: float
    reference_json: str
    session_json: str

    def __post_init__(self) -> None:
        require_precision_model(self.model)
        binding = json.loads(self.model.binding_json)
        if (
            self.datum_id != self.model.reference or self.binding_id != binding_digest(binding)
            or not isinstance(self.capture_pose, tuple) or self.capture_pose != _pose(binding["capture_pose"])
        ):
            raise CalibrationError("Measured material plane has stale datum, camera pose or model provenance")
        _text(self.plan_id, "Surface plan")
        _text(self.measurement_id, "Surface measurement")
        elevation = _number(self.elevation_mm, "Measured surface elevation")
        honeycomb = _number(self.honeycomb_height_mm, "Honeycomb height")
        if not 0 <= elevation - honeycomb <= MAX_MATERIAL_ELEVATION_MM:
            raise CalibrationError("Material top must be 0 to 20 mm above the saved honeycomb, including supports")
        try:
            reference, session = json.loads(self.reference_json), json.loads(self.session_json)
        except (ValueError, TypeError) as exc:
            raise CalibrationError("Invalid material-plane measurement provenance") from exc
        if not isinstance(reference, dict) or not reference or not isinstance(session, list) or not session:
            raise CalibrationError("Current border reference and controller session provenance are required")
        _json(reference)
        _json(session)
        self.model.homographies(elevation)

    @classmethod
    def from_measurement(
        cls, model: SurfaceHeightModel, measurement: dict[str, Any], *,
        datum_id: str, binding_id: str, capture_pose: tuple[float, float, float | None] | list[Any],
    ) -> MaterialPlaneSelection:
        require_precision_model(model)
        binding = json.loads(model.binding_json)
        pose = _pose(capture_pose)
        if datum_id != model.reference or binding_id != binding_digest(binding) or pose != _pose(binding["capture_pose"]):
            raise CalibrationError("Measured material plane has stale datum, camera pose or model provenance")
        if not isinstance(measurement, dict) or measurement.get("reusable") is not True:
            raise CalibrationError("A currently validated reusable surface measurement is required")
        plan_id = _text(measurement.get("id"), "Surface plan")
        measurement_id = _text(measurement.get("measurement_id"), "Surface measurement")
        elevation = _number(measurement.get("surface_elevation_mm"), "Measured surface elevation")
        honeycomb = _number(measurement.get("honeycomb_height_mm"), "Honeycomb height")
        if not 0 <= elevation - honeycomb <= MAX_MATERIAL_ELEVATION_MM:
            raise CalibrationError("Material top must be 0 to 20 mm above the saved honeycomb, including supports")
        if not isinstance(measurement.get("reference"), dict) or not measurement["reference"]:
            raise CalibrationError("Current border reference provenance is required")
        if not isinstance(measurement.get("session"), (list, tuple)) or not measurement["session"]:
            raise CalibrationError("Current controller session provenance is required")
        model.homographies(elevation)
        return cls(
            model, datum_id, binding_id, pose, plan_id, measurement_id, elevation, honeycomb,
            _json(measurement["reference"]), _json(measurement["session"]),
        )

    @property
    def signature(self) -> tuple[Any, ...]:
        return (
            self.model.model_id, self.datum_id, self.binding_id, self.capture_pose,
            self.plan_id, self.measurement_id, self.elevation_mm, self.honeycomb_height_mm,
            self.reference_json, self.session_json,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "model_id": self.model.model_id,
            "datum_id": self.datum_id, "binding_id": self.binding_id,
            "capture_pose": list(self.capture_pose), "plan_id": self.plan_id,
            "measurement_id": self.measurement_id, "surface_elevation_mm": self.elevation_mm,
            "honeycomb_height_mm": self.honeycomb_height_mm,
            "reference": json.loads(self.reference_json), "session": json.loads(self.session_json),
            "evidence_source_ids": list(self.model.evidence_source_ids),
        }

    def homographies(self) -> tuple[np.ndarray, np.ndarray]:
        return self.model.homographies(self.elevation_mm)

    def as_bed_calibration(self, base: BedCalibration) -> BedCalibration:
        """Make a transient mapper adapter without transferring 2D refinements."""
        if (base.image_width, base.image_height) != self.model.image_size:
            raise CalibrationError("Base and material-plane camera resolutions differ")
        inverse, forward = self.homographies()
        inverse.setflags(write=False)
        forward.setflags(write=False)
        return replace(
            base, image_to_machine=inverse, machine_to_image=forward,
            rms_error_mm=max(self.model.fit_rms_mm, self.model.check_rms_mm or 0),
            max_error_mm=max(self.model.fit_max_mm, self.model.check_max_mm or 0),
            registration_x_mm=0, registration_y_mm=0, registration_created_at=None,
            refinement_base=None, refinement_created_at=None, residual_mesh=None,
            provenance=copy.deepcopy(base.provenance),
        )


def camera_sampling_diagnostics(
    model: SurfaceHeightModel, lens: LensModel, *, height_mm: float,
    points_mm: np.ndarray | None = None,
) -> dict[str, Any]:
    """Report source-camera sampling and XY sensitivity; no accuracy claim.

    Finite differences use raw sensor pixels, including the lens distortion.
    Sampling does not measure optical sharpness or feature-localization error.
    """
    if lens.image_size != model.image_size or not np.allclose(lens.camera_matrix, model.camera_matrix):
        raise CalibrationError("Sampling lens and surface model do not match")
    binding = json.loads(model.binding_json)
    recorded_lens = binding.get("camera_geometry", {}).get("lens_model_id")
    if recorded_lens is not None and recorded_lens != lens.model_id:
        raise CalibrationError("Sampling lens identity changed")
    inverse, forward = model.homographies(height_mm)
    del inverse
    xmin, xmax, ymin, ymax = model.area
    if points_mm is None:
        points = np.array([(x, y) for y in (ymin, (ymin + ymax) / 2, ymax)
                           for x in (xmin, (xmin + xmax) / 2, xmax)], dtype=np.float64)
    else:
        points = np.asarray(points_mm, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2 or not len(points) or not np.isfinite(points).all():
        raise CalibrationError("Sampling points must be finite nonempty Nx2 coordinates")
    if np.any(points < [xmin, ymin]) or np.any(points > [xmax, ymax]):
        raise CalibrationError("Sampling points must be inside the calibrated work area")

    def raw_pixels(xy: np.ndarray) -> np.ndarray:
        corrected = cv2.perspectiveTransform(xy.reshape(-1, 1, 2), forward).reshape(-1, 2)
        return lens.distort_points(corrected)

    raw = raw_pixels(points)
    step = 0.01
    derivative_x = (raw_pixels(points + [step, 0]) - raw_pixels(points - [step, 0])) / (2 * step)
    derivative_y = (raw_pixels(points + [0, step]) - raw_pixels(points - [0, step])) / (2 * step)
    jacobians = np.stack((derivative_x, derivative_y), axis=2)
    singular_values = np.linalg.svd(jacobians, compute_uv=False)
    if not np.isfinite(singular_values).all() or np.any(singular_values <= 1e-9):
        raise CalibrationError("Source-camera sampling is singular")
    camera_center = -np.asarray(model.rotation).T @ np.asarray(model.translation)
    distance = camera_center[2] - height_mm
    if distance <= 0:
        raise CalibrationError("Material plane is not below the camera")
    sensitivity = np.linalg.norm(points - camera_center[:2], axis=1) / distance
    width, height = model.image_size
    visible = np.all((raw >= [0, 0]) & (raw <= [width - 1, height - 1]), axis=1)
    samples = [{
        "machine_xy_mm": point.tolist(), "source_xy_px": pixel.tolist(), "inside_image": bool(seen),
        "best_mm_per_source_pixel": float(1 / scales[0]),
        "worst_mm_per_source_pixel": float(1 / scales[-1]),
        "xy_mm_per_height_mm": float(effect),
    } for point, pixel, seen, scales, effect in zip(points, raw, visible, singular_values, sensitivity, strict=True)]
    maximum_sensitivity = float(np.max(sensitivity))
    return {
        "model_id": model.model_id, "height_mm": float(height_mm), "samples": samples,
        "worst_mm_per_source_pixel": float(np.max(1 / singular_values[:, -1])),
        "maximum_xy_mm_per_height_mm": maximum_sensitivity,
        "height_error_for_0_1mm_xy_mm": (None if maximum_sensitivity == 0 else PRECISION_TARGET_MM / maximum_sensitivity),
        "all_samples_visible": bool(np.all(visible)), "physical_accuracy_verified": False,
    }
