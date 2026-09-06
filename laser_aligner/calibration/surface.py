"""Two-height camera geometry, independent of machine or UI authority.

Evidence contains measured correspondences in the *undistorted* image domain.
One physical camera pose is fitted to both planes; homography coefficients are
never blended. A third plane is held out of the fit. This module grants no
motion, probing, job, or active-calibration authority.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import WorkArea
from ..errors import CalibrationError
from ..storage import atomic_write_json, read_json
from .bed import BedCalibration, BedPoint
from .lens import LensModel

_SLOTS = ("lower", "upper", "check")
_MAX_POINTS = 1000
_MAX_FILE_BYTES = 2_000_000
_FIT_RMS_MM = 0.50
_FIT_MAX_MM = 0.80
_CHECK_RMS_MM = 0.30
_CHECK_MAX_MM = 0.60


def _number(value: Any, label: str) -> float:
    if isinstance(value, (bool, str)) or not isinstance(value, (int, float)):
        raise CalibrationError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise CalibrationError(f"{label} must be a finite number")
    return result


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _project(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    homogeneous = np.column_stack((points, np.ones(len(points)))) @ matrix.T
    if not np.isfinite(homogeneous).all() or np.any(np.abs(homogeneous[:, 2]) < 1e-9):
        raise CalibrationError("Surface mapping approaches an undefined projection")
    return homogeneous[:, :2] / homogeneous[:, 2, None]


@dataclass(frozen=True, slots=True)
class SurfaceEvidence:
    height_mm: float
    reference: str
    source_digest: str
    binding_json: str
    # image_x, image_y, machine_x, machine_y, all from the original observations.
    points: tuple[tuple[float, float, float, float], ...]

    @classmethod
    def from_dict(cls, raw: Any) -> SurfaceEvidence:
        if not isinstance(raw, dict) or set(raw) != {
            "height_mm", "reference", "source_digest", "binding", "points",
        }:
            raise CalibrationError("Malformed surface-height evidence")
        height = _number(raw["height_mm"], "Surface height")
        if not -1000 <= height <= 1000:
            raise CalibrationError("Surface height must be between -1000 and 1000 mm relative to the datum")
        reference = raw["reference"]
        if not isinstance(reference, str) or not reference.strip() or len(reference) > 160:
            raise CalibrationError("Name the fixed height reference (at most 160 characters)")
        digest = raw["source_digest"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise CalibrationError("Surface evidence has an invalid source identity")
        if not isinstance(raw["binding"], dict) or not raw["binding"]:
            raise CalibrationError("Surface evidence requires camera and machine provenance")
        points = raw["points"]
        if not isinstance(points, list) or not 9 <= len(points) <= _MAX_POINTS:
            raise CalibrationError("Surface evidence needs 9 to 1000 measured points")
        rows = []
        for row in points:
            if not isinstance(row, list) or len(row) != 4:
                raise CalibrationError("Each surface observation requires four coordinates")
            rows.append(tuple(_number(value, "Surface observation") for value in row))
        if len({row[:2] for row in rows}) != len(rows) or len({row[2:] for row in rows}) != len(rows):
            raise CalibrationError("Surface observations must have unique image and machine points")
        try:
            binding_json = _json(raw["binding"])
        except (TypeError, ValueError) as exc:
            raise CalibrationError("Surface provenance must contain finite JSON values") from exc
        return cls(height, reference.strip(), digest, binding_json, tuple(rows))

    def to_dict(self) -> dict[str, Any]:
        return {
            "height_mm": self.height_mm,
            "reference": self.reference,
            "source_digest": self.source_digest,
            "binding": json.loads(self.binding_json),
            "points": [list(row) for row in self.points],
        }


@dataclass(frozen=True, slots=True)
class SurfaceHeightModel:
    """Immutable diagnostic model; validity is derived, never restored as a flag."""

    camera_matrix: tuple[tuple[float, ...], ...]
    rotation: tuple[tuple[float, ...], ...]
    translation: tuple[float, ...]
    lower_mm: float
    upper_mm: float
    image_size: tuple[int, int]
    area: tuple[float, float, float, float]
    model_id: str
    fit_rms_mm: float
    fit_max_mm: float
    check_rms_mm: float | None = None
    check_max_mm: float | None = None

    @property
    def check_passed(self) -> bool:
        return (
            self.check_rms_mm is not None and self.check_max_mm is not None
            and self.check_rms_mm <= _CHECK_RMS_MM and self.check_max_mm <= _CHECK_MAX_MM
        )

    def homographies(self, height_mm: float) -> tuple[np.ndarray, np.ndarray]:
        height = _number(height_mm, "Material-top height")
        if not self.lower_mm <= height <= self.upper_mm:
            raise CalibrationError(
                f"Height must be inside the measured range {self.lower_mm:g}–{self.upper_mm:g} mm; "
                "extrapolation is unavailable"
            )
        rotation = np.asarray(self.rotation)
        translation = np.asarray(self.translation) + height * rotation[:, 2]
        forward = np.asarray(self.camera_matrix) @ np.column_stack((rotation[:, :2], translation))
        if abs(float(np.linalg.det(forward))) < 1e-9:
            raise CalibrationError("Surface plane has a singular camera mapping")
        return np.linalg.inv(forward), forward

    def image_to_machine(self, points: np.ndarray, height_mm: float) -> np.ndarray:
        points = np.asarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
            raise CalibrationError("Image points must be finite Nx2 coordinates")
        return _project(self.homographies(height_mm)[0], points)

    def rectify(self, image: np.ndarray, height_mm: float, *, pixels_per_mm: float = 3.0) -> np.ndarray:
        """Warp an already-undistorted image for diagnostic review only."""
        if (
            not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] != 3)
            or (image.shape[1], image.shape[0]) != self.image_size
        ):
            raise CalibrationError("Height preview needs an undistorted image at the calibrated resolution")
        ppm = _number(pixels_per_mm, "Preview pixels per millimetre")
        xmin, xmax, ymin, ymax = self.area
        if not 0 < ppm <= 100:
            raise CalibrationError("Preview pixels per millimetre must be positive and at most 100")
        width, height = max(1, round((xmax - xmin) * ppm)), max(1, round((ymax - ymin) * ppm))
        if width * height > 16_000_000:
            raise CalibrationError("Height preview exceeds the 16-million-pixel limit")
        to_canvas = np.array([[ppm, 0, -xmin * ppm], [0, -ppm, ymax * ppm], [0, 0, 1]])
        return cv2.warpPerspective(
            image, to_canvas @ self.homographies(height_mm)[0], (width, height),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(35, 35, 35),
        )


def _validate_coverage(evidence: SurfaceEvidence, lens: LensModel, area: WorkArea) -> np.ndarray:
    values = (area.x_min, area.x_max, area.y_min, area.y_max)
    if not all(math.isfinite(v) for v in values) or area.width <= 0 or area.height <= 0:
        raise CalibrationError("Surface calibration needs a finite positive work area")
    rows = np.asarray(evidence.points, dtype=np.float64)
    image, xy = rows[:, :2], rows[:, 2:]
    if np.any(image < 0) or np.any(image >= np.asarray(lens.image_size)):
        raise CalibrationError("A surface observation is outside the calibrated image")
    if any(not area.contains(float(x), float(y)) for x, y in xy):
        raise CalibrationError("A surface observation is outside the configured work area")
    span = np.ptp(xy, axis=0)
    hull_area = cv2.contourArea(cv2.convexHull(xy.astype(np.float32)))
    if span[0] < area.width * 0.6 or span[1] < area.height * 0.6 or hull_area < area.width * area.height * 0.35:
        raise CalibrationError("Surface observations must cover the bed, including its edges")
    return rows


def fit_surface_model(
    lower: SurfaceEvidence, upper: SurfaceEvidence, *, lens: LensModel,
    work_area: WorkArea, binding: dict[str, Any], check: SurfaceEvidence | None = None,
) -> SurfaceHeightModel:
    """Fit physical R,t with fixed K; score every observation in machine mm."""
    evidence = (lower, upper) if check is None else (lower, upper, check)
    # Validate even directly-constructed dataclass inputs.
    evidence = tuple(SurfaceEvidence.from_dict(item.to_dict()) for item in evidence)
    lower, upper = evidence[:2]
    check = evidence[2] if len(evidence) == 3 else None
    if upper.height_mm - lower.height_mm < 1.0:
        raise CalibrationError("Upper and lower measured heights must differ by at least 1 mm")
    if len({item.reference for item in evidence}) != 1:
        raise CalibrationError("All maps must use the same fixed height reference")
    if len({item.source_digest for item in evidence}) != len(evidence):
        raise CalibrationError("Each height needs a separately measured bed map")
    expected = _json(binding)
    if any(item.binding_json != expected for item in evidence):
        raise CalibrationError("Camera, lens, machine, or work-area provenance changed; acquire new evidence")
    rows = [_validate_coverage(item, lens, work_area) for item in evidence]
    objects = np.concatenate([
        np.column_stack((row[:, 2:], np.full(len(row), item.height_mm)))
        for item, row in zip(evidence[:2], rows[:2], strict=True)
    ])
    image = np.concatenate([row[:, :2] for row in rows[:2]])
    try:
        success, rvec, tvec = cv2.solvePnP(
            objects, image, lens.camera_matrix, np.zeros(5), flags=cv2.SOLVEPNP_SQPNP,
        )
        if not success:
            raise CalibrationError("The two-height camera pose could not be solved")
        rvec, tvec = cv2.solvePnPRefineLM(objects, image, lens.camera_matrix, np.zeros(5), rvec, tvec)
        rotation = cv2.Rodrigues(rvec)[0]
    except cv2.error as exc:
        raise CalibrationError("The two-height camera pose is degenerate or inconsistent") from exc
    if not np.isfinite(rotation).all() or not np.isfinite(tvec).all():
        raise CalibrationError("The camera pose contains non-finite values")
    camera_center = -rotation.T @ tvec.reshape(3)
    if camera_center[2] <= upper.height_mm + 1.0:
        raise CalibrationError("The solved camera must be above both material planes")
    # Depth is affine in X,Y,Z: checking all eight corners bounds the whole prism.
    corners = np.asarray([
        (x, y, z) for x in (work_area.x_min, work_area.x_max)
        for y in (work_area.y_min, work_area.y_max) for z in (lower.height_mm, upper.height_mm)
    ])
    if np.min((corners @ rotation.T + tvec.reshape(3))[:, 2]) <= 1.0:
        raise CalibrationError("The calibrated volume crosses the camera projection plane")
    if check is not None:
        fraction = (check.height_mm - lower.height_mm) / (upper.height_mm - lower.height_mm)
        if not 0.2 <= fraction <= 0.8:
            raise CalibrationError("Independent check height must be in the middle 60% of the measured range")
    model = SurfaceHeightModel(
        tuple(tuple(float(v) for v in row) for row in lens.camera_matrix),
        tuple(tuple(float(v) for v in row) for row in rotation),
        tuple(float(v) for v in tvec.reshape(3)), lower.height_mm, upper.height_mm,
        lens.image_size, (work_area.x_min, work_area.x_max, work_area.y_min, work_area.y_max),
        _digest({"evidence": [item.to_dict() for item in evidence], "lens": lens.model_id}), 0, 0,
    )
    errors = [
        np.linalg.norm(model.image_to_machine(row[:, :2], item.height_mm) - row[:, 2:], axis=1)
        for item, row in zip(evidence, rows, strict=True)
    ]
    fit_errors = np.concatenate(errors[:2])
    rms, maximum = float(np.sqrt(np.mean(fit_errors ** 2))), float(np.max(fit_errors))
    if rms > _FIT_RMS_MM or maximum > _FIT_MAX_MM:
        raise CalibrationError(
            f"Two-height fit rejected: RMS {rms:.3f} mm / max {maximum:.3f} mm "
            f"(limits {_FIT_RMS_MM:.2f} / {_FIT_MAX_MM:.2f}); check height, XY reference and camera pose"
        )
    data = asdict(model)
    data.update(fit_rms_mm=rms, fit_max_mm=maximum)
    if check is not None:
        data.update(
            check_rms_mm=float(np.sqrt(np.mean(errors[2] ** 2))), check_max_mm=float(np.max(errors[2])),
        )
    return SurfaceHeightModel(**data)


class SurfaceCalibrationStore:
    """Atomic evidence slots; solving never replaces the production bed map."""

    def __init__(self, directory: Path):
        self.path = directory / "surface_height_calibration.json"

    def load(self) -> dict[str, SurfaceEvidence]:
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > _MAX_FILE_BYTES:
            raise CalibrationError("Surface-height evidence file exceeds its size limit")
        try:
            raw = read_json(self.path, None)
            if not isinstance(raw, dict) or set(raw) != {"schema_version", "evidence"}:
                raise ValueError("Malformed surface-height calibration")
            if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
                raise ValueError("Unsupported surface-height calibration schema")
            entries = raw["evidence"]
            if not isinstance(entries, dict) or any(key not in _SLOTS for key in entries):
                raise ValueError("Invalid surface-height evidence slots")
            return {key: SurfaceEvidence.from_dict(value) for key, value in entries.items()}
        except (ValueError, KeyError, TypeError) as exc:
            raise CalibrationError(f"Cannot load surface-height calibration: {exc}") from exc

    def save_map(
        self, slot: str, *, height_mm: float, reference: str, calibration: BedCalibration,
        points: list[BedPoint], lens: LensModel, work_area: WorkArea, binding: dict[str, Any],
    ) -> SurfaceEvidence:
        if slot not in _SLOTS:
            raise CalibrationError("Choose lower, upper, or check evidence")
        if (
            calibration.residual_mesh is not None or calibration.refinement_base is not None
            or calibration.registration_x_mm != 0 or calibration.registration_y_mm != 0
        ):
            raise CalibrationError("Save a fresh base map before fine registration or residual-mesh correction")
        if (calibration.image_width, calibration.image_height) != lens.image_size:
            raise CalibrationError("Bed map and lens resolution do not match")
        if calibration.point_count != len(points) or calibration.inlier_count != len(points):
            raise CalibrationError("Every original surface observation must belong to the solved base map")
        result = SurfaceEvidence.from_dict({
            "height_mm": height_mm, "reference": reference,
            "source_digest": _digest(calibration.to_dict()), "binding": binding,
            "points": [[p.image_x, p.image_y, p.machine_x, p.machine_y] for p in points],
        })
        rows = _validate_coverage(result, lens, work_area)
        errors = np.linalg.norm(_project(calibration.image_to_machine, rows[:, :2]) - rows[:, 2:], axis=1)
        if np.sqrt(np.mean(errors ** 2)) > _FIT_RMS_MM or np.max(errors) > _FIT_MAX_MM:
            raise CalibrationError("Original observations do not pass the base-map fit limits")
        entries = self.load()
        entries[slot] = result
        # Replacing a fit plane always requires new independent check evidence.
        if slot != "check":
            entries.pop("check", None)
        payload = {"schema_version": 1, "evidence": {key: value.to_dict() for key, value in entries.items()}}
        if len(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False).encode("utf-8")) + 1 > _MAX_FILE_BYTES:
            raise CalibrationError("Surface-height evidence exceeds its storage limit")
        atomic_write_json(self.path, payload)
        return result

    def solve(self, *, lens: LensModel, work_area: WorkArea, binding: dict[str, Any]) -> SurfaceHeightModel:
        entries = self.load()
        if "lower" not in entries or "upper" not in entries:
            raise CalibrationError("Save separately measured lower and upper bed maps first")
        return fit_surface_model(
            entries["lower"], entries["upper"], check=entries.get("check"),
            lens=lens, work_area=work_area, binding=binding,
        )
