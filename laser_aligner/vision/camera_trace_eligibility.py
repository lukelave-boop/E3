from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from ..config import WorkArea
from ..geometry.polygon import normalize_convex_polygon

CAMERA_TRACE_ELIGIBILITY_VERSION = "e3-camera-trace-eligibility-v1"
CAMERA_TRACE_REFERENCE_MODEL_MAX_DIMENSION_PX = 800
CAMERA_TRACE_REFERENCE_MODEL_MAX_PIXELS_PER_MM = 2.0
CAMERA_TRACE_REFERENCE_LOCAL_SCALE_MM = 5.0
CAMERA_TRACE_REFERENCE_PATCH_SCALE_MM = 1.5
CAMERA_TRACE_REFERENCE_MIN_CORRELATION = 0.52
CAMERA_TRACE_REFERENCE_MAX_NORMALIZED_RMSE = 1.15
CAMERA_TRACE_REFERENCE_MIN_TEXTURE_LEVEL = 1.5


@dataclass(slots=True)
class CameraTraceEligibilityTiming:
    """Opt-in, non-persistent timing for hard ROI and reference suppression."""

    stage_seconds: dict[str, float] = field(default_factory=dict, init=False)
    stage_calls: dict[str, int] = field(default_factory=dict, init=False)

    def record(self, stage: str, elapsed_seconds: float) -> None:
        name = str(stage)
        self.stage_seconds[name] = self.stage_seconds.get(name, 0.0) + max(
            0.0, float(elapsed_seconds)
        )
        self.stage_calls[name] = self.stage_calls.get(name, 0) + 1

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        return {
            name: {
                "seconds": self.stage_seconds[name],
                "calls": self.stage_calls[name],
            }
            for name in sorted(self.stage_seconds)
        }


@dataclass(frozen=True, slots=True)
class CameraTraceEligibilityDiagnostics:
    algorithm_version: str
    eligibility_key: str
    roi_source: str
    roi_polygon_count: int
    hard_roi_pixel_count: int
    hard_roi_fraction: float
    reference_status: str
    reference_identity: str | None
    reference_model_width_px: int | None
    reference_model_height_px: int | None
    exposed_bed_pixel_count: int
    exposed_bed_fraction_of_roi: float
    material_eligible_pixel_count: int
    material_eligible_fraction_of_roi: float
    local_scale_mm: float
    patch_scale_mm: float
    minimum_correlation: float
    maximum_normalized_rmse: float
    minimum_texture_level: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True, eq=False)
class CameraTraceEligibilityResult:
    """Immutable, authority-free pixels allowed to participate in camera tracing."""

    hard_roi_mask: np.ndarray
    exposed_bed_mask: np.ndarray
    material_eligible_mask: np.ndarray
    diagnostics: CameraTraceEligibilityDiagnostics

    def __post_init__(self) -> None:
        arrays = (
            (self.hard_roi_mask, "hard_roi_mask"),
            (self.exposed_bed_mask, "exposed_bed_mask"),
            (self.material_eligible_mask, "material_eligible_mask"),
        )
        shape: tuple[int, int] | None = None
        for values, label in arrays:
            if not isinstance(values, np.ndarray):
                raise TypeError(f"{label} must be a numpy array")
            if values.dtype != np.uint8 or values.ndim != 2:
                raise ValueError(f"{label} must be a 2D uint8 array")
            if values.flags.writeable or not values.flags.c_contiguous:
                raise ValueError(f"{label} must be immutable and C-contiguous")
            if shape is None:
                shape = values.shape
            elif values.shape != shape:
                raise ValueError("Camera Trace eligibility arrays must have one shape")
        if not np.any(self.hard_roi_mask):
            raise ValueError("The hard Camera Trace ROI contains no image pixels")
        if np.any((self.exposed_bed_mask > 0) & (self.hard_roi_mask == 0)):
            raise ValueError("Exposed-bed evidence cannot extend outside the hard ROI")
        if np.any((self.material_eligible_mask > 0) & (self.hard_roi_mask == 0)):
            raise ValueError("Material eligibility cannot extend outside the hard ROI")


def _immutable_mask(values: np.ndarray) -> np.ndarray:
    mask = (np.asarray(values) > 0).astype(np.uint8) * 255
    contiguous = np.ascontiguousarray(mask)
    output = np.frombuffer(contiguous.tobytes(order="C"), dtype=np.uint8).reshape(
        contiguous.shape
    )
    output.setflags(write=False)
    return output


def _require_bgr(image: np.ndarray, label: str) -> np.ndarray:
    values = np.asarray(image)
    if (
        values.dtype != np.uint8
        or values.ndim != 3
        or values.shape[2] != 3
        or not values.shape[0]
        or not values.shape[1]
    ):
        raise ValueError(f"{label} must be a non-empty uint8 BGR image")
    return np.ascontiguousarray(values)


def _require_pixels_per_mm(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("pixels_per_mm must be positive and finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("pixels_per_mm must be positive and finite") from exc
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError("pixels_per_mm must be positive and finite")
    return result


def _hard_roi_mask(
    shape: tuple[int, int],
    work_area: WorkArea,
    pixels_per_mm: float,
    roi_polygons_mm: Sequence[Sequence[Sequence[float]]],
) -> np.ndarray:
    height, width = shape
    if abs(width - float(work_area.width) * pixels_per_mm) > 0.51 or abs(
        height - float(work_area.height) * pixels_per_mm
    ) > 0.51:
        raise ValueError(
            "Corrected Camera Trace dimensions do not match its calibrated work area"
        )
    if not roi_polygons_mm:
        raise ValueError("Camera Trace requires at least one trusted ROI polygon")
    x = float(work_area.x_min) + np.arange(width, dtype=np.float64) / pixels_per_mm
    y = float(work_area.y_max) - np.arange(height, dtype=np.float64) / pixels_per_mm
    xx, yy = np.meshgrid(x, y)
    eligible = np.ones(shape, dtype=bool)
    for index, raw_polygon in enumerate(roi_polygons_mm):
        polygon = normalize_convex_polygon(
            raw_polygon,
            label=f"Camera Trace ROI polygon {index + 1}",
        )
        inside = np.ones(shape, dtype=bool)
        for point_index, start in enumerate(polygon):
            end = polygon[(point_index + 1) % len(polygon)]
            cross = (end[0] - start[0]) * (yy - start[1]) - (
                end[1] - start[1]
            ) * (xx - start[0])
            inside &= cross >= -1e-9
        eligible &= inside
    return eligible.astype(np.uint8) * 255


def _reference_model_dimensions(
    width: int,
    height: int,
    pixels_per_mm: float,
) -> tuple[int, int]:
    scale = min(
        1.0,
        CAMERA_TRACE_REFERENCE_MODEL_MAX_PIXELS_PER_MM / pixels_per_mm,
        CAMERA_TRACE_REFERENCE_MODEL_MAX_DIMENSION_PX / max(width, height),
    )
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _gaussian_local_statistics(
    values: np.ndarray,
    sigma: float,
) -> tuple[np.ndarray, np.ndarray]:
    mean = cv2.GaussianBlur(
        values,
        (0, 0),
        sigmaX=sigma,
        sigmaY=sigma,
        borderType=cv2.BORDER_REFLECT_101,
    )
    square_mean = cv2.GaussianBlur(
        values * values,
        (0, 0),
        sigmaX=sigma,
        sigmaY=sigma,
        borderType=cv2.BORDER_REFLECT_101,
    )
    variance = np.maximum(square_mean - mean * mean, np.float32(0.0))
    return mean, variance


def _confident_exposed_bed_model(
    current_bgr: np.ndarray,
    reference_bgr: np.ndarray,
    hard_roi_mask: np.ndarray,
    pixels_per_mm: float,
) -> tuple[np.ndarray, int, int]:
    height, width = hard_roi_mask.shape
    model_width, model_height = _reference_model_dimensions(
        width, height, pixels_per_mm
    )
    model_ppm = min(model_width / (width / pixels_per_mm), model_height / (height / pixels_per_mm))
    current = cv2.resize(
        cv2.cvtColor(current_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32),
        (model_width, model_height),
        interpolation=cv2.INTER_AREA,
    )
    reference = cv2.resize(
        cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32),
        (model_width, model_height),
        interpolation=cv2.INTER_AREA,
    )
    roi = cv2.resize(
        hard_roi_mask,
        (model_width, model_height),
        interpolation=cv2.INTER_NEAREST,
    ) > 0
    local_sigma = max(1.0, CAMERA_TRACE_REFERENCE_LOCAL_SCALE_MM * model_ppm)
    current_mean, current_variance = _gaussian_local_statistics(current, local_sigma)
    reference_mean, reference_variance = _gaussian_local_statistics(
        reference, local_sigma
    )
    current_residual = current - current_mean
    reference_residual = reference - reference_mean
    patch_sigma = max(0.8, CAMERA_TRACE_REFERENCE_PATCH_SCALE_MM * model_ppm)
    covariance = cv2.GaussianBlur(
        current_residual * reference_residual,
        (0, 0),
        sigmaX=patch_sigma,
        sigmaY=patch_sigma,
        borderType=cv2.BORDER_REFLECT_101,
    )
    current_energy = cv2.GaussianBlur(
        current_residual * current_residual,
        (0, 0),
        sigmaX=patch_sigma,
        sigmaY=patch_sigma,
        borderType=cv2.BORDER_REFLECT_101,
    )
    reference_energy = cv2.GaussianBlur(
        reference_residual * reference_residual,
        (0, 0),
        sigmaX=patch_sigma,
        sigmaY=patch_sigma,
        borderType=cv2.BORDER_REFLECT_101,
    )
    denominator = np.sqrt(
        np.maximum(current_energy * reference_energy, np.float32(1e-6))
    )
    correlation = covariance / denominator
    current_scale = np.sqrt(np.maximum(current_variance, np.float32(1.0)))
    reference_scale = np.sqrt(np.maximum(reference_variance, np.float32(1.0)))
    normalized_difference = (
        current_residual / current_scale - reference_residual / reference_scale
    )
    normalized_rmse = np.sqrt(
        cv2.GaussianBlur(
            normalized_difference * normalized_difference,
            (0, 0),
            sigmaX=patch_sigma,
            sigmaY=patch_sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
    )
    current_texture = np.sqrt(np.maximum(current_energy, np.float32(0.0)))
    reference_texture = np.sqrt(np.maximum(reference_energy, np.float32(0.0)))
    exposed = (
        roi
        & (correlation >= CAMERA_TRACE_REFERENCE_MIN_CORRELATION)
        & (normalized_rmse <= CAMERA_TRACE_REFERENCE_MAX_NORMALIZED_RMSE)
        & (reference_texture >= CAMERA_TRACE_REFERENCE_MIN_TEXTURE_LEVEL)
        & (
            current_texture
            >= np.maximum(
                np.float32(CAMERA_TRACE_REFERENCE_MIN_TEXTURE_LEVEL),
                reference_texture * np.float32(0.35),
            )
        )
        & (current_texture <= reference_texture * np.float32(3.5) + np.float32(4.0))
    )
    exposed_full = cv2.resize(
        exposed.astype(np.uint8) * 255,
        (width, height),
        interpolation=cv2.INTER_NEAREST,
    )
    close_radius = max(1, int(round(3.0 * pixels_per_mm)))
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (close_radius * 2 + 1, close_radius * 2 + 1),
    )
    exposed_full = cv2.morphologyEx(
        exposed_full,
        cv2.MORPH_CLOSE,
        close_kernel,
        iterations=1,
    )
    exposed_full[hard_roi_mask == 0] = 0
    return exposed_full, model_width, model_height


def prepare_camera_trace_eligibility(
    corrected_bgr: np.ndarray,
    work_area: WorkArea,
    pixels_per_mm: float,
    *,
    roi_polygons_mm: Sequence[Sequence[Sequence[float]]],
    roi_source: str,
    reference_bgr: np.ndarray | None = None,
    reference_identity: str | None = None,
    timing: CameraTraceEligibilityTiming | None = None,
) -> CameraTraceEligibilityResult:
    """Build the exact hard-ROI and reference-aware material eligibility masks.

    The comparison is deliberately conservative: only patches with correlated,
    locally normalized reference structure and compatible texture are classified
    as exposed bed. Changed or uncertain pixels remain eligible; this stage never
    creates foreground artwork.
    """

    total_started = time.perf_counter()
    pixels = _require_bgr(corrected_bgr, "Corrected Camera Trace image")
    ppm = _require_pixels_per_mm(pixels_per_mm)
    roi_started = time.perf_counter()
    hard_roi = _hard_roi_mask(pixels.shape[:2], work_area, ppm, roi_polygons_mm)
    if timing is not None:
        timing.record("hard_roi_preparation", time.perf_counter() - roi_started)

    reference_status = "unavailable"
    model_width: int | None = None
    model_height: int | None = None
    reference_started = time.perf_counter()
    if reference_bgr is None:
        exposed = np.zeros(hard_roi.shape, dtype=np.uint8)
    else:
        reference = _require_bgr(
            reference_bgr, "Trusted empty-honeycomb reference"
        )
        if reference.shape != pixels.shape:
            raise ValueError(
                "Trusted empty-honeycomb reference dimensions do not match the "
                "current corrected Camera Trace frame"
            )
        exposed, model_width, model_height = _confident_exposed_bed_model(
            pixels,
            reference,
            hard_roi,
            ppm,
        )
        reference_status = "validated_and_used"
    if timing is not None:
        timing.record("reference_comparison", time.perf_counter() - reference_started)

    eligibility_started = time.perf_counter()
    material = cv2.bitwise_and(hard_roi, cv2.bitwise_not(exposed))
    if timing is not None:
        timing.record("material_eligibility", time.perf_counter() - eligibility_started)

    digest = hashlib.sha256()
    digest.update(CAMERA_TRACE_ELIGIBILITY_VERSION.encode("ascii"))
    digest.update(b"\0")
    digest.update(hard_roi.tobytes(order="C"))
    digest.update(exposed.tobytes(order="C"))
    hard_count = int(np.count_nonzero(hard_roi))
    exposed_count = int(np.count_nonzero(exposed))
    material_count = int(np.count_nonzero(material))
    diagnostics = CameraTraceEligibilityDiagnostics(
        algorithm_version=CAMERA_TRACE_ELIGIBILITY_VERSION,
        eligibility_key=digest.hexdigest(),
        roi_source=str(roi_source),
        roi_polygon_count=len(roi_polygons_mm),
        hard_roi_pixel_count=hard_count,
        hard_roi_fraction=hard_count / float(hard_roi.size),
        reference_status=reference_status,
        reference_identity=reference_identity,
        reference_model_width_px=model_width,
        reference_model_height_px=model_height,
        exposed_bed_pixel_count=exposed_count,
        exposed_bed_fraction_of_roi=exposed_count / float(max(1, hard_count)),
        material_eligible_pixel_count=material_count,
        material_eligible_fraction_of_roi=material_count / float(max(1, hard_count)),
        local_scale_mm=CAMERA_TRACE_REFERENCE_LOCAL_SCALE_MM,
        patch_scale_mm=CAMERA_TRACE_REFERENCE_PATCH_SCALE_MM,
        minimum_correlation=CAMERA_TRACE_REFERENCE_MIN_CORRELATION,
        maximum_normalized_rmse=CAMERA_TRACE_REFERENCE_MAX_NORMALIZED_RMSE,
        minimum_texture_level=CAMERA_TRACE_REFERENCE_MIN_TEXTURE_LEVEL,
    )
    if not material_count:
        # A completely matched empty bed is a valid fail-closed result. Keep one
        # immutable zero mask; the vectorizer is never invoked for it.
        material = np.zeros_like(material)
    result = CameraTraceEligibilityResult(
        hard_roi_mask=_immutable_mask(hard_roi),
        exposed_bed_mask=_immutable_mask(exposed),
        material_eligible_mask=_immutable_mask(material),
        diagnostics=diagnostics,
    )
    if timing is not None:
        timing.record("trace_eligibility_total", time.perf_counter() - total_started)
    return result


__all__ = [
    "CAMERA_TRACE_ELIGIBILITY_VERSION",
    "CameraTraceEligibilityDiagnostics",
    "CameraTraceEligibilityResult",
    "CameraTraceEligibilityTiming",
    "prepare_camera_trace_eligibility",
]
