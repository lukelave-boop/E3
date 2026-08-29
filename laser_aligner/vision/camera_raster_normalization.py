from __future__ import annotations

import hashlib
import math
import struct
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

CAMERA_RASTER_NORMALIZATION_VERSION = "e3-camera-raster-normalization-v5"
CAMERA_BACKGROUND_MODEL_MAX_DIMENSION_PX = 512
CAMERA_BACKGROUND_MODEL_MAX_PIXELS_PER_MM = 1.0
CAMERA_BACKGROUND_ENVELOPE_DIAMETER_MM = 35.0
CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM = 4.0
CAMERA_FLAT_FIELD_HISTOGRAM_BIN_WIDTH_LEVELS = 4
CAMERA_FLAT_FIELD_PALETTE_BIN_COUNT = 8
CAMERA_FLAT_FIELD_MIN_PALETTE_COVERAGE = 0.995
CAMERA_FLAT_FIELD_BORDER_BAND_MM = 2.0
CAMERA_FLAT_FIELD_BORDER_TOLERANCE_LEVELS = 3.0
CAMERA_FLAT_FIELD_MIN_BORDER_COVERAGE = 0.995
CAMERA_FLAT_FIELD_FEATURE_DISTANCE_LEVELS = 32.0
CAMERA_FLAT_FIELD_MIN_BACKGROUND_COVERAGE = 0.50
CAMERA_FLAT_FIELD_MIN_SEPARATION_RATIO = 0.80
CAMERA_NORMALIZATION_NOISE_FLOOR = 3.0
CAMERA_NORMALIZATION_ROBUST_PERCENTILE = 99.5
CAMERA_NORMALIZATION_MIN_RESPONSE_SCALE = 32.0
CAMERA_NORMALIZATION_MAX_RESPONSE_SCALE = 64.0
CAMERA_NORMALIZATION_RESPONSE_TRANSFER = "reciprocal"


@dataclass(slots=True)
class CameraRasterNormalizationTiming:
    """Opt-in, non-persistent timing for camera-raster preparation."""

    stage_seconds: dict[str, float] = field(default_factory=dict, init=False)
    stage_calls: dict[str, int] = field(default_factory=dict, init=False)

    def record(self, stage: str, elapsed_seconds: float) -> None:
        name = str(stage)
        elapsed = max(0.0, float(elapsed_seconds))
        self.stage_seconds[name] = self.stage_seconds.get(name, 0.0) + elapsed
        self.stage_calls[name] = self.stage_calls.get(name, 0) + 1

    def reset(self) -> None:
        self.stage_seconds.clear()
        self.stage_calls.clear()

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        return {
            name: {
                "seconds": self.stage_seconds[name],
                "calls": self.stage_calls[name],
            }
            for name in sorted(self.stage_seconds)
        }


@dataclass(frozen=True, slots=True)
class CameraRasterNormalizationDiagnostics:
    """Immutable parameters and identities for one normalization result."""

    algorithm_version: str
    normalization_key: str
    width_px: int
    height_px: int
    width_mm: float
    height_mm: float
    pixels_per_mm: float
    model_width_px: int
    model_height_px: int
    model_pixels_per_mm_x: float
    model_pixels_per_mm_y: float
    background_model_kind: str
    background_envelope_diameter_mm: float
    background_envelope_kernel_width_px: int
    background_envelope_kernel_height_px: int
    background_smoothing_sigma_mm: float
    background_smoothing_sigma_model_px_x: float
    background_smoothing_sigma_model_px_y: float
    flat_field_histogram_bin_width_levels: int
    flat_field_palette_bin_count: int
    flat_field_palette_coverage: float
    flat_field_border_band_mm: float
    flat_field_border_band_width_px: int
    flat_field_border_band_height_px: int
    flat_field_border_tolerance_levels: float
    flat_field_border_coverage: float
    flat_field_border_level: float
    flat_field_background_coverage: float
    flat_field_feature_distance_levels: float
    flat_field_separation_ratio: float
    noise_floor_levels: float
    robust_percentile: float
    robust_response_level: float
    response_scale_levels: float
    response_transfer: str
    eligibility_supplied: bool
    eligible_pixel_count: int
    eligible_fraction: float
    eligibility_fill_kind: str


@dataclass(frozen=True, slots=True)
class _FlatFieldAssessment:
    applies: bool
    palette_coverage: float
    border_band_width_px: int
    border_band_height_px: int
    border_coverage: float
    border_level: float
    background_coverage: float
    separation_ratio: float


@dataclass(frozen=True, slots=True, eq=False)
class CameraRasterNormalizationResult:
    """Temporary, authority-free camera pixels prepared for raster tracing."""

    corrected_bgr: np.ndarray
    grayscale: np.ndarray
    background: np.ndarray
    signed_residual: np.ndarray
    dark_raster: np.ndarray
    light_raster: np.ndarray
    diagnostics: CameraRasterNormalizationDiagnostics

    def __post_init__(self) -> None:
        arrays = (
            (self.corrected_bgr, "corrected_bgr", np.uint8, 3),
            (self.grayscale, "grayscale", np.uint8, 2),
            (self.background, "background", np.float32, 2),
            (self.signed_residual, "signed_residual", np.float32, 2),
            (self.dark_raster, "dark_raster", np.uint8, 2),
            (self.light_raster, "light_raster", np.uint8, 2),
        )
        for values, label, dtype, dimensions in arrays:
            if not isinstance(values, np.ndarray):
                raise TypeError(f"{label} must be a numpy array")
            if values.dtype != dtype or values.ndim != dimensions:
                raise ValueError(
                    f"{label} must be a {dimensions}D {np.dtype(dtype).name} array"
                )
            if values.flags.writeable:
                raise ValueError(f"{label} must be read-only")
            if not values.flags.c_contiguous:
                raise ValueError(f"{label} must be C-contiguous")
        if self.corrected_bgr.shape[2] != 3:
            raise ValueError("corrected_bgr must contain three BGR channels")
        shape = self.corrected_bgr.shape[:2]
        if not shape[0] or not shape[1]:
            raise ValueError("A camera-raster normalization result cannot be empty")
        if any(values.shape != shape for values, _label, _dtype, _dimensions in arrays[1:]):
            raise ValueError("Camera-raster normalization arrays must have one image shape")
        if not np.isfinite(self.background).all() or not np.isfinite(
            self.signed_residual
        ).all():
            raise ValueError("Camera-raster normalization float arrays must be finite")
        if not isinstance(self.diagnostics, CameraRasterNormalizationDiagnostics):
            raise TypeError(
                "diagnostics must be CameraRasterNormalizationDiagnostics"
            )

    def raster_for(self, polarity: str) -> np.ndarray:
        """Return the already-normalized artwork raster for one polarity."""

        selected = str(polarity).strip().lower()
        if selected == "dark":
            return self.dark_raster
        if selected == "light":
            return self.light_raster
        raise ValueError("Camera-raster polarity must be either 'dark' or 'light'")


def _immutable_array(values: np.ndarray) -> np.ndarray:
    """Copy values onto an immutable bytes backing store."""

    contiguous = np.ascontiguousarray(values)
    output = np.frombuffer(
        contiguous.tobytes(order="C"),
        dtype=contiguous.dtype,
    ).reshape(contiguous.shape)
    output.setflags(write=False)
    return output


def _require_corrected_bgr(corrected_bgr: np.ndarray) -> np.ndarray:
    values = np.asarray(corrected_bgr)
    if (
        values.dtype != np.uint8
        or values.ndim != 3
        or values.shape[2] != 3
        or not values.shape[0]
        or not values.shape[1]
    ):
        raise ValueError(
            "Corrected camera input must be a non-empty uint8 BGR image"
        )
    return np.ascontiguousarray(values).copy()


def _require_pixels_per_mm(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("pixels_per_mm must be positive and finite")
    try:
        pixels_per_mm = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("pixels_per_mm must be positive and finite") from exc
    if not math.isfinite(pixels_per_mm) or pixels_per_mm <= 0.0:
        raise ValueError("pixels_per_mm must be positive and finite")
    return pixels_per_mm


def _background_model_dimensions(
    width_px: int,
    height_px: int,
    pixels_per_mm: float,
) -> tuple[int, int]:
    scale = min(
        1.0,
        CAMERA_BACKGROUND_MODEL_MAX_PIXELS_PER_MM / pixels_per_mm,
        CAMERA_BACKGROUND_MODEL_MAX_DIMENSION_PX / max(width_px, height_px),
    )
    return (
        max(1, int(round(width_px * scale))),
        max(1, int(round(height_px * scale))),
    )


def _model_kernel_extent(distance_mm: float, model_pixels_per_mm: float) -> int:
    nominal = max(
        1,
        int(
            round(
                distance_mm
                * min(
                    CAMERA_BACKGROUND_MODEL_MAX_PIXELS_PER_MM,
                    model_pixels_per_mm,
                )
            )
        ),
    )
    if nominal % 2 == 0:
        nominal += 1
    maximum = int(
        math.ceil(
            distance_mm * CAMERA_BACKGROUND_MODEL_MAX_PIXELS_PER_MM
        )
    )
    if maximum % 2 == 0:
        maximum += 1
    return min(nominal, maximum)


def _flat_field_assessment(
    grayscale: np.ndarray,
    model: np.ndarray,
    model_pixels_per_mm_x: float,
    model_pixels_per_mm_y: float,
) -> _FlatFieldAssessment:
    histogram = np.bincount(
        np.ravel(grayscale) // CAMERA_FLAT_FIELD_HISTOGRAM_BIN_WIDTH_LEVELS,
        minlength=(
            256 + CAMERA_FLAT_FIELD_HISTOGRAM_BIN_WIDTH_LEVELS - 1
        )
        // CAMERA_FLAT_FIELD_HISTOGRAM_BIN_WIDTH_LEVELS,
    )
    palette_bin_count = min(
        CAMERA_FLAT_FIELD_PALETTE_BIN_COUNT,
        histogram.size,
    )
    palette_coverage = float(
        np.partition(histogram, -palette_bin_count)[-palette_bin_count:].sum()
    ) / float(grayscale.size)

    border_band_width_px = min(
        max(
            1,
            int(
                round(
                    CAMERA_FLAT_FIELD_BORDER_BAND_MM
                    * model_pixels_per_mm_x
                )
            ),
        ),
        max(1, (model.shape[1] + 1) // 2),
    )
    border_band_height_px = min(
        max(
            1,
            int(
                round(
                    CAMERA_FLAT_FIELD_BORDER_BAND_MM
                    * model_pixels_per_mm_y
                )
            ),
        ),
        max(1, (model.shape[0] + 1) // 2),
    )
    border_mask = np.zeros(model.shape, dtype=bool)
    border_mask[:border_band_height_px, :] = True
    border_mask[-border_band_height_px:, :] = True
    border_mask[:, :border_band_width_px] = True
    border_mask[:, -border_band_width_px:] = True
    border_values = model[border_mask]
    border_level = float(np.median(border_values))
    border_distance = np.abs(
        border_values - np.float32(border_level)
    )
    border_coverage = float(
        np.count_nonzero(
            border_distance
            <= np.float32(CAMERA_FLAT_FIELD_BORDER_TOLERANCE_LEVELS)
        )
    ) / float(border_values.size)
    model_distance = np.abs(model - np.float32(border_level))
    background_coverage = float(
        np.count_nonzero(
            model_distance
            <= np.float32(CAMERA_FLAT_FIELD_BORDER_TOLERANCE_LEVELS)
        )
    ) / float(model.size)
    intermediate_count = np.count_nonzero(
        (model_distance > np.float32(CAMERA_FLAT_FIELD_BORDER_TOLERANCE_LEVELS))
        & (
            model_distance
            < np.float32(CAMERA_FLAT_FIELD_FEATURE_DISTANCE_LEVELS)
        )
    )
    far_count = np.count_nonzero(
        model_distance >= np.float32(CAMERA_FLAT_FIELD_FEATURE_DISTANCE_LEVELS)
    )
    separated_count = int(intermediate_count) + int(far_count)
    separation_ratio = (
        1.0
        if separated_count == 0
        else float(far_count) / float(separated_count)
    )
    applies = (
        palette_coverage >= CAMERA_FLAT_FIELD_MIN_PALETTE_COVERAGE
        and border_coverage >= CAMERA_FLAT_FIELD_MIN_BORDER_COVERAGE
        and background_coverage >= CAMERA_FLAT_FIELD_MIN_BACKGROUND_COVERAGE
        and separation_ratio >= CAMERA_FLAT_FIELD_MIN_SEPARATION_RATIO
    )
    return _FlatFieldAssessment(
        applies=applies,
        palette_coverage=palette_coverage,
        border_band_width_px=border_band_width_px,
        border_band_height_px=border_band_height_px,
        border_coverage=border_coverage,
        border_level=border_level,
        background_coverage=background_coverage,
        separation_ratio=separation_ratio,
    )


def _robust_response_scale(
    response: np.ndarray,
    eligibility: np.ndarray | None = None,
) -> tuple[float, float]:
    flattened = (
        np.ravel(response)
        if eligibility is None
        else np.asarray(response)[np.asarray(eligibility) > 0]
    )
    rank = max(
        0,
        min(
            flattened.size - 1,
            int(
                math.ceil(
                    CAMERA_NORMALIZATION_ROBUST_PERCENTILE
                    / 100.0
                    * flattened.size
                )
            )
            - 1,
        ),
    )
    robust_level = float(np.partition(flattened, rank)[rank])
    scale = max(
        CAMERA_NORMALIZATION_MIN_RESPONSE_SCALE,
        min(CAMERA_NORMALIZATION_MAX_RESPONSE_SCALE, robust_level),
    )
    return robust_level, scale


def _artwork_raster(response: np.ndarray, scale: float) -> np.ndarray:
    response_scale = np.float32(scale)
    normalized = (
        np.float32(255.0)
        * response_scale
        / (response_scale + response)
    )
    return np.clip(np.rint(normalized), 0.0, 255.0).astype(np.uint8)


def _normalization_key(
    corrected_bgr: np.ndarray,
    pixels_per_mm: float,
    eligibility_mask: np.ndarray | None,
) -> str:
    digest = hashlib.sha256()
    digest.update(CAMERA_RASTER_NORMALIZATION_VERSION.encode("ascii"))
    digest.update(b"\0")
    digest.update(
        struct.pack(
            ">IId",
            int(corrected_bgr.shape[1]),
            int(corrected_bgr.shape[0]),
            pixels_per_mm,
        )
    )
    digest.update(corrected_bgr.tobytes(order="C"))
    if eligibility_mask is not None:
        digest.update(b"\0eligibility-v1\0")
        digest.update(eligibility_mask.tobytes(order="C"))
    return digest.hexdigest()


def normalize_camera_trace_frame(
    corrected_bgr: np.ndarray,
    pixels_per_mm: float,
    *,
    eligibility_mask: np.ndarray | None = None,
    timing: CameraRasterNormalizationTiming | None = None,
) -> CameraRasterNormalizationResult:
    """Remove low-frequency photographic variation before raster vectorization.

    One symmetric background model produces dark- and light-feature responses.
    Near-discrete flat-field inputs use their robust border level so clean solid
    interiors are independent of feature size. Other inputs use the physical
    rank-envelope model. Both returned rasters use ordinary artwork semantics:
    blank or opposite-polarity pixels are white, while a strong selected
    response tends toward black without reaching a clipped black endpoint.
    Background estimation never thresholds or repairs raster geometry.
    """

    total_started = time.perf_counter()
    try:
        grayscale_started = time.perf_counter()
        try:
            pixels = _require_corrected_bgr(corrected_bgr)
            ppm = _require_pixels_per_mm(pixels_per_mm)
            grayscale = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
            intensity = grayscale.astype(np.float32)
            eligibility: np.ndarray | None = None
            if eligibility_mask is not None:
                candidate = np.asarray(eligibility_mask)
                if candidate.ndim != 2 or candidate.shape != grayscale.shape:
                    raise ValueError(
                        "Camera normalization eligibility must match the corrected frame"
                    )
                if candidate.dtype not in (np.bool_, np.uint8):
                    raise ValueError(
                        "Camera normalization eligibility must be boolean or uint8"
                    )
                eligibility = (candidate > 0).astype(np.uint8) * 255
                if not np.any(eligibility):
                    raise ValueError(
                        "Camera normalization eligibility contains no material pixels"
                    )
                if np.all(eligibility):
                    eligibility = None
        finally:
            if timing is not None:
                timing.record(
                    "grayscale_preparation",
                    time.perf_counter() - grayscale_started,
                )

        height_px, width_px = grayscale.shape
        width_mm = width_px / ppm
        height_mm = height_px / ppm
        model_width_px, model_height_px = _background_model_dimensions(
            width_px,
            height_px,
            ppm,
        )
        model_ppm_x = model_width_px / width_mm
        model_ppm_y = model_height_px / height_mm
        envelope_kernel_width_px = _model_kernel_extent(
            CAMERA_BACKGROUND_ENVELOPE_DIAMETER_MM,
            model_ppm_x,
        )
        envelope_kernel_height_px = _model_kernel_extent(
            CAMERA_BACKGROUND_ENVELOPE_DIAMETER_MM,
            model_ppm_y,
        )
        smoothing_sigma_model_px_x = min(
            CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM,
            max(
                1e-6,
                CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM * model_ppm_x,
            ),
        )
        smoothing_sigma_model_px_y = min(
            CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM,
            max(
                1e-6,
                CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM * model_ppm_y,
            ),
        )

        background_started = time.perf_counter()
        try:
            fill_started = time.perf_counter()
            if eligibility is None or np.all(eligibility):
                model_input = intensity
                eligibility_fill_kind = "none"
            else:
                model_input = cv2.inpaint(
                    grayscale,
                    cv2.bitwise_not(eligibility),
                    max(1.0, min(12.0, 2.0 * ppm)),
                    cv2.INPAINT_TELEA,
                ).astype(np.float32)
                eligibility_fill_kind = "telea_model_only"
            if timing is not None and eligibility is not None:
                timing.record(
                    "eligibility_fill",
                    time.perf_counter() - fill_started,
                )
            model = cv2.resize(
                model_input,
                (model_width_px, model_height_px),
                interpolation=cv2.INTER_AREA,
            )
            flat_field = _flat_field_assessment(
                np.clip(np.rint(model_input), 0.0, 255.0).astype(np.uint8),
                model,
                model_ppm_x,
                model_ppm_y,
            )
            if flat_field.applies:
                background_model_kind = "flat_field_constant"
                background = np.full(
                    intensity.shape,
                    np.float32(flat_field.border_level),
                    dtype=np.float32,
                )
            else:
                background_model_kind = "rank_envelope"
                envelope_kernel = cv2.getStructuringElement(
                    cv2.MORPH_ELLIPSE,
                    (envelope_kernel_width_px, envelope_kernel_height_px),
                )
                lower_envelope = cv2.morphologyEx(
                    model,
                    cv2.MORPH_OPEN,
                    envelope_kernel,
                    borderType=cv2.BORDER_REFLECT_101,
                )
                upper_envelope = cv2.morphologyEx(
                    model,
                    cv2.MORPH_CLOSE,
                    envelope_kernel,
                    borderType=cv2.BORDER_REFLECT_101,
                )
                envelope_midpoint = (
                    lower_envelope + upper_envelope
                ) * np.float32(0.5)
                model_background = cv2.GaussianBlur(
                    envelope_midpoint,
                    (0, 0),
                    sigmaX=smoothing_sigma_model_px_x,
                    sigmaY=smoothing_sigma_model_px_y,
                    borderType=cv2.BORDER_REFLECT_101,
                )
                background = cv2.resize(
                    model_background,
                    (width_px, height_px),
                    interpolation=cv2.INTER_LINEAR,
                ).astype(np.float32, copy=False)
        finally:
            if timing is not None:
                timing.record(
                    "background_estimation",
                    time.perf_counter() - background_started,
                )

        normalization_started = time.perf_counter()
        try:
            signed_residual = intensity - background
            if eligibility is not None:
                signed_residual[eligibility == 0] = np.float32(0.0)
            noise_floor = np.float32(CAMERA_NORMALIZATION_NOISE_FLOOR)
            zero = np.float32(0.0)
            magnitude = np.maximum(np.abs(signed_residual) - noise_floor, zero)
            robust_level, response_scale = _robust_response_scale(
                magnitude,
                eligibility,
            )
            dark_response = np.maximum(-signed_residual - noise_floor, zero)
            light_response = np.maximum(signed_residual - noise_floor, zero)
            dark_raster = _artwork_raster(dark_response, response_scale)
            light_raster = _artwork_raster(light_response, response_scale)
            if eligibility is not None:
                dark_raster[eligibility == 0] = 255
                light_raster[eligibility == 0] = 255
            normalization_key = _normalization_key(pixels, ppm, eligibility)
        finally:
            if timing is not None:
                timing.record(
                    "normalization",
                    time.perf_counter() - normalization_started,
                )

        diagnostics = CameraRasterNormalizationDiagnostics(
            algorithm_version=CAMERA_RASTER_NORMALIZATION_VERSION,
            normalization_key=normalization_key,
            width_px=width_px,
            height_px=height_px,
            width_mm=width_mm,
            height_mm=height_mm,
            pixels_per_mm=ppm,
            model_width_px=model_width_px,
            model_height_px=model_height_px,
            model_pixels_per_mm_x=model_ppm_x,
            model_pixels_per_mm_y=model_ppm_y,
            background_model_kind=background_model_kind,
            background_envelope_diameter_mm=(
                CAMERA_BACKGROUND_ENVELOPE_DIAMETER_MM
            ),
            background_envelope_kernel_width_px=envelope_kernel_width_px,
            background_envelope_kernel_height_px=envelope_kernel_height_px,
            background_smoothing_sigma_mm=CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM,
            background_smoothing_sigma_model_px_x=(
                smoothing_sigma_model_px_x
            ),
            background_smoothing_sigma_model_px_y=(
                smoothing_sigma_model_px_y
            ),
            flat_field_histogram_bin_width_levels=(
                CAMERA_FLAT_FIELD_HISTOGRAM_BIN_WIDTH_LEVELS
            ),
            flat_field_palette_bin_count=CAMERA_FLAT_FIELD_PALETTE_BIN_COUNT,
            flat_field_palette_coverage=flat_field.palette_coverage,
            flat_field_border_band_mm=CAMERA_FLAT_FIELD_BORDER_BAND_MM,
            flat_field_border_band_width_px=(
                flat_field.border_band_width_px
            ),
            flat_field_border_band_height_px=(
                flat_field.border_band_height_px
            ),
            flat_field_border_tolerance_levels=(
                CAMERA_FLAT_FIELD_BORDER_TOLERANCE_LEVELS
            ),
            flat_field_border_coverage=flat_field.border_coverage,
            flat_field_border_level=flat_field.border_level,
            flat_field_background_coverage=(
                flat_field.background_coverage
            ),
            flat_field_feature_distance_levels=(
                CAMERA_FLAT_FIELD_FEATURE_DISTANCE_LEVELS
            ),
            flat_field_separation_ratio=flat_field.separation_ratio,
            noise_floor_levels=CAMERA_NORMALIZATION_NOISE_FLOOR,
            robust_percentile=CAMERA_NORMALIZATION_ROBUST_PERCENTILE,
            robust_response_level=robust_level,
            response_scale_levels=response_scale,
            response_transfer=CAMERA_NORMALIZATION_RESPONSE_TRANSFER,
            eligibility_supplied=eligibility is not None,
            eligible_pixel_count=(
                int(grayscale.size)
                if eligibility is None
                else int(np.count_nonzero(eligibility))
            ),
            eligible_fraction=(
                1.0
                if eligibility is None
                else float(np.count_nonzero(eligibility)) / float(eligibility.size)
            ),
            eligibility_fill_kind=eligibility_fill_kind,
        )
        return CameraRasterNormalizationResult(
            corrected_bgr=_immutable_array(pixels),
            grayscale=_immutable_array(grayscale),
            background=_immutable_array(background),
            signed_residual=_immutable_array(signed_residual),
            dark_raster=_immutable_array(dark_raster),
            light_raster=_immutable_array(light_raster),
            diagnostics=diagnostics,
        )
    finally:
        if timing is not None:
            timing.record(
                "camera_normalization_total",
                time.perf_counter() - total_started,
            )


__all__ = [
    "CAMERA_BACKGROUND_ENVELOPE_DIAMETER_MM",
    "CAMERA_BACKGROUND_MODEL_MAX_DIMENSION_PX",
    "CAMERA_BACKGROUND_MODEL_MAX_PIXELS_PER_MM",
    "CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM",
    "CAMERA_FLAT_FIELD_BORDER_BAND_MM",
    "CAMERA_FLAT_FIELD_BORDER_TOLERANCE_LEVELS",
    "CAMERA_FLAT_FIELD_FEATURE_DISTANCE_LEVELS",
    "CAMERA_FLAT_FIELD_HISTOGRAM_BIN_WIDTH_LEVELS",
    "CAMERA_FLAT_FIELD_MIN_BACKGROUND_COVERAGE",
    "CAMERA_FLAT_FIELD_MIN_BORDER_COVERAGE",
    "CAMERA_FLAT_FIELD_MIN_PALETTE_COVERAGE",
    "CAMERA_FLAT_FIELD_MIN_SEPARATION_RATIO",
    "CAMERA_FLAT_FIELD_PALETTE_BIN_COUNT",
    "CAMERA_NORMALIZATION_MAX_RESPONSE_SCALE",
    "CAMERA_NORMALIZATION_MIN_RESPONSE_SCALE",
    "CAMERA_NORMALIZATION_NOISE_FLOOR",
    "CAMERA_NORMALIZATION_RESPONSE_TRANSFER",
    "CAMERA_NORMALIZATION_ROBUST_PERCENTILE",
    "CAMERA_RASTER_NORMALIZATION_VERSION",
    "CameraRasterNormalizationDiagnostics",
    "CameraRasterNormalizationResult",
    "CameraRasterNormalizationTiming",
    "normalize_camera_trace_frame",
]
