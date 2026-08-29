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

CAMERA_TRACE_ELIGIBILITY_VERSION = "e3-camera-trace-eligibility-v2"
CAMERA_TRACE_REFERENCE_MODEL_MAX_DIMENSION_PX = 800
CAMERA_TRACE_REFERENCE_MODEL_MAX_PIXELS_PER_MM = 2.0
CAMERA_TRACE_REFERENCE_LOCAL_SCALE_MM = 5.0
CAMERA_TRACE_REFERENCE_PATCH_SCALE_MM = 1.5
CAMERA_TRACE_REFERENCE_MIN_CORRELATION = 0.52
CAMERA_TRACE_REFERENCE_MAX_NORMALIZED_RMSE = 1.15
CAMERA_TRACE_REFERENCE_MIN_TEXTURE_LEVEL = 1.5
CAMERA_TRACE_REFERENCE_STRONG_MIN_CORRELATION = 0.68
CAMERA_TRACE_REFERENCE_STRONG_MAX_NORMALIZED_RMSE = 0.90
CAMERA_TRACE_REFERENCE_PHOTOMETRIC_SEED_MAX_L_DELTA = 32.0
CAMERA_TRACE_REFERENCE_PHOTOMETRIC_SEED_MAX_CHROMA_DELTA = 24.0
CAMERA_TRACE_REFERENCE_PHOTOMETRIC_MIN_SEED_PIXELS = 32
CAMERA_TRACE_REFERENCE_PHOTOMETRIC_MAX_SAMPLE_PIXELS = 50_000
CAMERA_TRACE_REFERENCE_LUMINANCE_GAIN_MIN = 0.72
CAMERA_TRACE_REFERENCE_LUMINANCE_GAIN_MAX = 1.28
CAMERA_TRACE_REFERENCE_LUMINANCE_OFFSET_MAX = 48.0
CAMERA_TRACE_REFERENCE_LUMINANCE_GRADIENT_MAX = 32.0
CAMERA_TRACE_REFERENCE_CHROMA_OFFSET_MAX = 24.0
CAMERA_TRACE_REFERENCE_CHROMA_GRADIENT_MAX = 16.0
CAMERA_TRACE_REFERENCE_APPEARANCE_BLUR_SCALE_MM = 0.35
CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_L_DELTA = 34.0
CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_CHROMA_DELTA = 22.0
CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_COMBINED_DELTA = 38.0
CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_L_DELTA = 26.0
CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_CHROMA_DELTA = 18.0
CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_COMBINED_DELTA = 30.0
CAMERA_TRACE_REFERENCE_CLOSE_RADIUS_MM = 3.0


@dataclass(slots=True)
class CameraTraceEligibilityTiming:
    """Opt-in, non-persistent timing for hard ROI and reference suppression."""

    stage_seconds: dict[str, float] = field(default_factory=dict, init=False)
    stage_calls: dict[str, int] = field(default_factory=dict, init=False)

    def record(self, stage: str, elapsed_seconds: float) -> None:
        name = str(stage)
        self.stage_seconds[name] = self.stage_seconds.get(name, 0.0) + max(0.0, float(elapsed_seconds))
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
    strong_minimum_correlation: float
    strong_maximum_normalized_rmse: float
    structural_match_pixel_count: int
    strong_structural_seed_pixel_count: int
    photometric_compensation_status: str
    photometric_seed_pixel_count: int
    photometric_fit_sample_pixel_count: int
    photometric_luminance_gain: float
    photometric_luminance_offset: float
    photometric_luminance_gradient_x: float
    photometric_luminance_gradient_y: float
    photometric_chroma_a_offset: float
    photometric_chroma_a_gradient_x: float
    photometric_chroma_a_gradient_y: float
    photometric_chroma_b_offset: float
    photometric_chroma_b_gradient_x: float
    photometric_chroma_b_gradient_y: float
    appearance_match_pixel_count: int
    appearance_max_luminance_delta: float
    appearance_max_chroma_delta: float
    appearance_max_combined_delta: float
    appearance_max_local_luminance_delta: float
    appearance_max_local_chroma_delta: float
    appearance_max_local_combined_delta: float
    closing_radius_mm: float

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
    output = np.frombuffer(contiguous.tobytes(order="C"), dtype=np.uint8).reshape(contiguous.shape)
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
    if (
        abs(width - float(work_area.width) * pixels_per_mm) > 0.51
        or abs(height - float(work_area.height) * pixels_per_mm) > 0.51
    ):
        raise ValueError("Corrected Camera Trace dimensions do not match its calibrated work area")
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
            cross = (end[0] - start[0]) * (yy - start[1]) - (end[1] - start[1]) * (xx - start[0])
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


@dataclass(frozen=True, slots=True)
class _ReferenceModelEvidence:
    structural_match_pixel_count: int
    strong_structural_seed_pixel_count: int
    photometric_compensation_status: str
    photometric_seed_pixel_count: int
    photometric_fit_sample_pixel_count: int
    photometric_luminance_gain: float
    photometric_luminance_offset: float
    photometric_luminance_gradient_x: float
    photometric_luminance_gradient_y: float
    photometric_chroma_a_offset: float
    photometric_chroma_a_gradient_x: float
    photometric_chroma_a_gradient_y: float
    photometric_chroma_b_offset: float
    photometric_chroma_b_gradient_x: float
    photometric_chroma_b_gradient_y: float
    appearance_match_pixel_count: int


def _robust_linear_fit(features: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Return a deterministic Tukey-weighted least-squares fit."""

    design = np.asarray(features, dtype=np.float64)
    values = np.asarray(target, dtype=np.float64).reshape(-1)
    if design.ndim != 2 or design.shape[0] != values.size:
        raise ValueError("Photometric fit arrays do not align")
    if values.size < design.shape[1]:
        raise ValueError("Photometric fit has insufficient samples")
    coefficients = np.linalg.lstsq(design, values, rcond=None)[0]
    for _iteration in range(5):
        residual = values - design @ coefficients
        center = float(np.median(residual))
        scale = max(
            0.75,
            1.4826 * float(np.median(np.abs(residual - center))),
        )
        normalized = (residual - center) / (4.685 * scale)
        weights = np.zeros_like(normalized)
        retained = np.abs(normalized) < 1.0
        weights[retained] = (1.0 - normalized[retained] ** 2) ** 2
        if np.count_nonzero(weights > 1e-6) < design.shape[1] * 4:
            break
        root_weights = np.sqrt(weights)
        next_coefficients = np.linalg.lstsq(
            design * root_weights[:, None],
            values * root_weights,
            rcond=None,
        )[0]
        if np.max(np.abs(next_coefficients - coefficients)) < 1e-5:
            coefficients = next_coefficients
            break
        coefficients = next_coefficients
    return coefficients


def _bounded_photometric_model(
    current_lab: np.ndarray,
    reference_lab: np.ndarray,
    seed_mask: np.ndarray,
) -> tuple[np.ndarray | None, _ReferenceModelEvidence]:
    seed_indices = np.flatnonzero(seed_mask.reshape(-1))
    seed_count = int(seed_indices.size)
    empty_evidence = _ReferenceModelEvidence(
        structural_match_pixel_count=0,
        strong_structural_seed_pixel_count=0,
        photometric_compensation_status="insufficient_reference_like_seeds",
        photometric_seed_pixel_count=seed_count,
        photometric_fit_sample_pixel_count=0,
        photometric_luminance_gain=1.0,
        photometric_luminance_offset=0.0,
        photometric_luminance_gradient_x=0.0,
        photometric_luminance_gradient_y=0.0,
        photometric_chroma_a_offset=0.0,
        photometric_chroma_a_gradient_x=0.0,
        photometric_chroma_a_gradient_y=0.0,
        photometric_chroma_b_offset=0.0,
        photometric_chroma_b_gradient_x=0.0,
        photometric_chroma_b_gradient_y=0.0,
        appearance_match_pixel_count=0,
    )
    if seed_count < CAMERA_TRACE_REFERENCE_PHOTOMETRIC_MIN_SEED_PIXELS:
        return None, empty_evidence
    if seed_count > CAMERA_TRACE_REFERENCE_PHOTOMETRIC_MAX_SAMPLE_PIXELS:
        stride = int(math.ceil(seed_count / CAMERA_TRACE_REFERENCE_PHOTOMETRIC_MAX_SAMPLE_PIXELS))
        seed_indices = seed_indices[::stride]

    height, width = seed_mask.shape
    yy, xx = np.indices((height, width), dtype=np.float32)
    x_coordinate = xx / max(1.0, float(width - 1)) - 0.5
    y_coordinate = yy / max(1.0, float(height - 1)) - 0.5
    flat_current = current_lab.reshape(-1, 3)
    flat_reference = reference_lab.reshape(-1, 3)
    sample_x = x_coordinate.reshape(-1)[seed_indices].astype(np.float64)
    sample_y = y_coordinate.reshape(-1)[seed_indices].astype(np.float64)
    plane_features = np.column_stack((np.ones(seed_indices.size, dtype=np.float64), sample_x, sample_y))

    luminance_features = np.column_stack(
        (
            flat_reference[seed_indices, 0].astype(np.float64),
            plane_features,
        )
    )
    luminance_fit = _robust_linear_fit(
        luminance_features,
        flat_current[seed_indices, 0],
    )
    luminance_gain = float(
        np.clip(
            luminance_fit[0],
            CAMERA_TRACE_REFERENCE_LUMINANCE_GAIN_MIN,
            CAMERA_TRACE_REFERENCE_LUMINANCE_GAIN_MAX,
        )
    )
    luminance_plane = _robust_linear_fit(
        plane_features,
        flat_current[seed_indices, 0] - luminance_gain * flat_reference[seed_indices, 0],
    )
    luminance_plane = np.asarray(
        (
            np.clip(
                luminance_plane[0],
                -CAMERA_TRACE_REFERENCE_LUMINANCE_OFFSET_MAX,
                CAMERA_TRACE_REFERENCE_LUMINANCE_OFFSET_MAX,
            ),
            np.clip(
                luminance_plane[1],
                -CAMERA_TRACE_REFERENCE_LUMINANCE_GRADIENT_MAX,
                CAMERA_TRACE_REFERENCE_LUMINANCE_GRADIENT_MAX,
            ),
            np.clip(
                luminance_plane[2],
                -CAMERA_TRACE_REFERENCE_LUMINANCE_GRADIENT_MAX,
                CAMERA_TRACE_REFERENCE_LUMINANCE_GRADIENT_MAX,
            ),
        ),
        dtype=np.float32,
    )

    chroma_planes: list[np.ndarray] = []
    for channel in (1, 2):
        channel_plane = _robust_linear_fit(
            plane_features,
            flat_current[seed_indices, channel] - flat_reference[seed_indices, channel],
        )
        chroma_planes.append(
            np.asarray(
                (
                    np.clip(
                        channel_plane[0],
                        -CAMERA_TRACE_REFERENCE_CHROMA_OFFSET_MAX,
                        CAMERA_TRACE_REFERENCE_CHROMA_OFFSET_MAX,
                    ),
                    np.clip(
                        channel_plane[1],
                        -CAMERA_TRACE_REFERENCE_CHROMA_GRADIENT_MAX,
                        CAMERA_TRACE_REFERENCE_CHROMA_GRADIENT_MAX,
                    ),
                    np.clip(
                        channel_plane[2],
                        -CAMERA_TRACE_REFERENCE_CHROMA_GRADIENT_MAX,
                        CAMERA_TRACE_REFERENCE_CHROMA_GRADIENT_MAX,
                    ),
                ),
                dtype=np.float32,
            )
        )

    predicted = np.empty_like(reference_lab, dtype=np.float32)
    predicted[:, :, 0] = (
        luminance_gain * reference_lab[:, :, 0]
        + luminance_plane[0]
        + luminance_plane[1] * x_coordinate
        + luminance_plane[2] * y_coordinate
    )
    for output_channel, channel_plane in zip((1, 2), chroma_planes, strict=True):
        predicted[:, :, output_channel] = (
            reference_lab[:, :, output_channel]
            + channel_plane[0]
            + channel_plane[1] * x_coordinate
            + channel_plane[2] * y_coordinate
        )
    predicted = np.clip(predicted, 0.0, 255.0)
    evidence = _ReferenceModelEvidence(
        structural_match_pixel_count=0,
        strong_structural_seed_pixel_count=0,
        photometric_compensation_status="bounded_robust_lab_affine",
        photometric_seed_pixel_count=seed_count,
        photometric_fit_sample_pixel_count=int(seed_indices.size),
        photometric_luminance_gain=luminance_gain,
        photometric_luminance_offset=float(luminance_plane[0]),
        photometric_luminance_gradient_x=float(luminance_plane[1]),
        photometric_luminance_gradient_y=float(luminance_plane[2]),
        photometric_chroma_a_offset=float(chroma_planes[0][0]),
        photometric_chroma_a_gradient_x=float(chroma_planes[0][1]),
        photometric_chroma_a_gradient_y=float(chroma_planes[0][2]),
        photometric_chroma_b_offset=float(chroma_planes[1][0]),
        photometric_chroma_b_gradient_x=float(chroma_planes[1][1]),
        photometric_chroma_b_gradient_y=float(chroma_planes[1][2]),
        appearance_match_pixel_count=0,
    )
    return predicted, evidence


def _appearance_guarded_closing(
    direct_evidence: np.ndarray,
    strong_seeds: np.ndarray,
    bridge_support: np.ndarray,
    model_pixels_per_mm: float,
) -> np.ndarray:
    """Close only strong seeds and admit bridges through supported appearance."""

    close_radius = max(
        1,
        int(round(CAMERA_TRACE_REFERENCE_CLOSE_RADIUS_MM * model_pixels_per_mm)),
    )
    close_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (close_radius * 2 + 1, close_radius * 2 + 1),
    )
    closed_strong = (
        cv2.morphologyEx(
            strong_seeds.astype(np.uint8) * 255,
            cv2.MORPH_CLOSE,
            close_kernel,
            iterations=1,
        )
        > 0
    )
    return direct_evidence | (closed_strong & bridge_support)


def _confident_exposed_bed_model(
    current_bgr: np.ndarray,
    reference_bgr: np.ndarray,
    hard_roi_mask: np.ndarray,
    pixels_per_mm: float,
    *,
    timing: CameraTraceEligibilityTiming | None = None,
) -> tuple[np.ndarray, int, int, _ReferenceModelEvidence]:
    structural_started = time.perf_counter()
    height, width = hard_roi_mask.shape
    model_width, model_height = _reference_model_dimensions(width, height, pixels_per_mm)
    model_ppm = min(
        model_width / (width / pixels_per_mm),
        model_height / (height / pixels_per_mm),
    )
    current_model_bgr = cv2.resize(
        current_bgr,
        (model_width, model_height),
        interpolation=cv2.INTER_AREA,
    )
    reference_model_bgr = cv2.resize(
        reference_bgr,
        (model_width, model_height),
        interpolation=cv2.INTER_AREA,
    )
    current = cv2.cvtColor(current_model_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    reference = cv2.cvtColor(reference_model_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    roi = (
        cv2.resize(
            hard_roi_mask,
            (model_width, model_height),
            interpolation=cv2.INTER_NEAREST,
        )
        > 0
    )
    local_sigma = max(1.0, CAMERA_TRACE_REFERENCE_LOCAL_SCALE_MM * model_ppm)
    current_mean, current_variance = _gaussian_local_statistics(current, local_sigma)
    reference_mean, reference_variance = _gaussian_local_statistics(reference, local_sigma)
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
    denominator = np.sqrt(np.maximum(current_energy * reference_energy, np.float32(1e-6)))
    correlation = covariance / denominator
    current_scale = np.sqrt(np.maximum(current_variance, np.float32(1.0)))
    reference_scale = np.sqrt(np.maximum(reference_variance, np.float32(1.0)))
    normalized_difference = current_residual / current_scale - reference_residual / reference_scale
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
    structural_match = (
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
    strong_structural = (
        structural_match
        & (correlation >= CAMERA_TRACE_REFERENCE_STRONG_MIN_CORRELATION)
        & (normalized_rmse <= CAMERA_TRACE_REFERENCE_STRONG_MAX_NORMALIZED_RMSE)
        & (
            current_texture
            >= np.maximum(
                np.float32(CAMERA_TRACE_REFERENCE_MIN_TEXTURE_LEVEL),
                reference_texture * np.float32(0.50),
            )
        )
        & (current_texture <= reference_texture * np.float32(2.5) + np.float32(3.0))
    )
    if timing is not None:
        timing.record(
            "structural_reference_match",
            time.perf_counter() - structural_started,
        )

    compensation_started = time.perf_counter()
    current_lab = cv2.cvtColor(current_model_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    reference_lab = cv2.cvtColor(reference_model_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    raw_l_delta = np.abs(current_lab[:, :, 0] - reference_lab[:, :, 0])
    raw_chroma_delta = np.hypot(
        current_lab[:, :, 1] - reference_lab[:, :, 1],
        current_lab[:, :, 2] - reference_lab[:, :, 2],
    )
    compensation_seeds = (
        strong_structural
        & (raw_l_delta <= CAMERA_TRACE_REFERENCE_PHOTOMETRIC_SEED_MAX_L_DELTA)
        & (raw_chroma_delta <= CAMERA_TRACE_REFERENCE_PHOTOMETRIC_SEED_MAX_CHROMA_DELTA)
    )
    predicted_lab, evidence = _bounded_photometric_model(
        current_lab,
        reference_lab,
        compensation_seeds,
    )
    if timing is not None:
        timing.record(
            "photometric_compensation",
            time.perf_counter() - compensation_started,
        )

    appearance_started = time.perf_counter()
    if predicted_lab is None:
        appearance_match = np.zeros_like(roi)
    else:
        appearance_sigma = max(
            0.45,
            CAMERA_TRACE_REFERENCE_APPEARANCE_BLUR_SCALE_MM * model_ppm,
        )
        current_smoothed = cv2.GaussianBlur(
            current_lab,
            (0, 0),
            sigmaX=appearance_sigma,
            sigmaY=appearance_sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
        predicted_smoothed = cv2.GaussianBlur(
            predicted_lab,
            (0, 0),
            sigmaX=appearance_sigma,
            sigmaY=appearance_sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
        luminance_delta = np.abs(current_smoothed[:, :, 0] - predicted_smoothed[:, :, 0])
        chroma_delta = np.hypot(
            current_smoothed[:, :, 1] - predicted_smoothed[:, :, 1],
            current_smoothed[:, :, 2] - predicted_smoothed[:, :, 2],
        )
        combined_delta = np.sqrt(luminance_delta * luminance_delta + np.float32(0.5) * chroma_delta * chroma_delta)
        local_luminance_delta = cv2.GaussianBlur(
            luminance_delta,
            (0, 0),
            sigmaX=patch_sigma,
            sigmaY=patch_sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
        local_chroma_delta = cv2.GaussianBlur(
            chroma_delta,
            (0, 0),
            sigmaX=patch_sigma,
            sigmaY=patch_sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
        local_combined_delta = cv2.GaussianBlur(
            combined_delta,
            (0, 0),
            sigmaX=patch_sigma,
            sigmaY=patch_sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
        point_match = (
            (luminance_delta <= CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_L_DELTA)
            & (chroma_delta <= CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_CHROMA_DELTA)
            & (combined_delta <= CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_COMBINED_DELTA)
        )
        local_match = (
            (local_luminance_delta <= CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_L_DELTA)
            & (local_chroma_delta <= CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_CHROMA_DELTA)
            & (local_combined_delta <= CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_COMBINED_DELTA)
        )
        # A bed pixel immediately beside a changed sheet can have a large patch
        # mean even though its own compensated appearance is an excellent match.
        # Keep that exact evidence without allowing merely borderline pixels to
        # bypass the local appearance check.
        unambiguous_point_match = (
            (luminance_delta <= np.float32(12.0))
            & (chroma_delta <= np.float32(8.0))
            & (combined_delta <= np.float32(14.0))
        )
        appearance_match = roi & point_match & (local_match | unambiguous_point_match)
    if timing is not None:
        timing.record("appearance_veto", time.perf_counter() - appearance_started)

    closing_started = time.perf_counter()
    direct_evidence = structural_match & appearance_match
    strong_seeds = strong_structural & appearance_match
    bridge_support = (
        roi
        & appearance_match
        & (correlation >= np.float32(0.30))
        & (normalized_rmse <= np.float32(1.55))
        & (reference_texture >= np.float32(1.0))
        & (current_texture >= reference_texture * np.float32(0.20))
        & (current_texture <= reference_texture * np.float32(5.0) + np.float32(8.0))
    )
    exposed = _appearance_guarded_closing(
        direct_evidence,
        strong_seeds,
        bridge_support,
        model_ppm,
    )
    if timing is not None:
        timing.record(
            "morphology_closing",
            time.perf_counter() - closing_started,
        )

    exposed_full = cv2.resize(
        exposed.astype(np.uint8) * 255,
        (width, height),
        interpolation=cv2.INTER_NEAREST,
    )
    exposed_full[hard_roi_mask == 0] = 0
    evidence = _ReferenceModelEvidence(
        **{
            **asdict(evidence),
            "structural_match_pixel_count": int(np.count_nonzero(structural_match)),
            "strong_structural_seed_pixel_count": int(np.count_nonzero(strong_structural)),
            "appearance_match_pixel_count": int(np.count_nonzero(appearance_match)),
        }
    )
    return exposed_full, model_width, model_height, evidence


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
    reference_evidence = _ReferenceModelEvidence(
        structural_match_pixel_count=0,
        strong_structural_seed_pixel_count=0,
        photometric_compensation_status="not_used",
        photometric_seed_pixel_count=0,
        photometric_fit_sample_pixel_count=0,
        photometric_luminance_gain=1.0,
        photometric_luminance_offset=0.0,
        photometric_luminance_gradient_x=0.0,
        photometric_luminance_gradient_y=0.0,
        photometric_chroma_a_offset=0.0,
        photometric_chroma_a_gradient_x=0.0,
        photometric_chroma_a_gradient_y=0.0,
        photometric_chroma_b_offset=0.0,
        photometric_chroma_b_gradient_x=0.0,
        photometric_chroma_b_gradient_y=0.0,
        appearance_match_pixel_count=0,
    )
    reference_started = time.perf_counter()
    if reference_bgr is None:
        exposed = np.zeros(hard_roi.shape, dtype=np.uint8)
    else:
        reference = _require_bgr(reference_bgr, "Trusted empty-honeycomb reference")
        if reference.shape != pixels.shape:
            raise ValueError(
                "Trusted empty-honeycomb reference dimensions do not match the current corrected Camera Trace frame"
            )
        exposed, model_width, model_height, reference_evidence = _confident_exposed_bed_model(
            pixels,
            reference,
            hard_roi,
            ppm,
            timing=timing,
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
        strong_minimum_correlation=CAMERA_TRACE_REFERENCE_STRONG_MIN_CORRELATION,
        strong_maximum_normalized_rmse=(CAMERA_TRACE_REFERENCE_STRONG_MAX_NORMALIZED_RMSE),
        structural_match_pixel_count=(reference_evidence.structural_match_pixel_count),
        strong_structural_seed_pixel_count=(reference_evidence.strong_structural_seed_pixel_count),
        photometric_compensation_status=(reference_evidence.photometric_compensation_status),
        photometric_seed_pixel_count=reference_evidence.photometric_seed_pixel_count,
        photometric_fit_sample_pixel_count=(
            reference_evidence.photometric_fit_sample_pixel_count
        ),
        photometric_luminance_gain=reference_evidence.photometric_luminance_gain,
        photometric_luminance_offset=(reference_evidence.photometric_luminance_offset),
        photometric_luminance_gradient_x=(reference_evidence.photometric_luminance_gradient_x),
        photometric_luminance_gradient_y=(reference_evidence.photometric_luminance_gradient_y),
        photometric_chroma_a_offset=(reference_evidence.photometric_chroma_a_offset),
        photometric_chroma_a_gradient_x=(reference_evidence.photometric_chroma_a_gradient_x),
        photometric_chroma_a_gradient_y=(reference_evidence.photometric_chroma_a_gradient_y),
        photometric_chroma_b_offset=(reference_evidence.photometric_chroma_b_offset),
        photometric_chroma_b_gradient_x=(reference_evidence.photometric_chroma_b_gradient_x),
        photometric_chroma_b_gradient_y=(reference_evidence.photometric_chroma_b_gradient_y),
        appearance_match_pixel_count=reference_evidence.appearance_match_pixel_count,
        appearance_max_luminance_delta=(CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_L_DELTA),
        appearance_max_chroma_delta=(CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_CHROMA_DELTA),
        appearance_max_combined_delta=(CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_COMBINED_DELTA),
        appearance_max_local_luminance_delta=(CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_L_DELTA),
        appearance_max_local_chroma_delta=(CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_CHROMA_DELTA),
        appearance_max_local_combined_delta=(CAMERA_TRACE_REFERENCE_APPEARANCE_MAX_LOCAL_COMBINED_DELTA),
        closing_radius_mm=CAMERA_TRACE_REFERENCE_CLOSE_RADIUS_MM,
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
