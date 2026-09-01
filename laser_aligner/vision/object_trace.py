from __future__ import annotations

import hashlib
import math
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

import cv2
import numpy as np

from ..config import WorkArea
from ..geometry.foreground import (
    ForegroundContourLimitError,
    ForegroundContourTree,
    extract_foreground_contours,
    foreground_contour_trees,
)
from ..geometry.polygon import (
    ConvexPolygon,
    convex_polygon_violation_normalized_mm,
    normalize_convex_polygon,
)
from ..project.native_contour_fit import fit_physical_contours_to_native_path
from ..project.path_geometry import (
    NativePathGeometry,
    PathAffineTransform,
    PathFillRule,
    native_path_bounds,
    transform_native_path,
)
from ..project.raster_vectorize import (
    RASTER_VECTORIZATION_OVERSAMPLE_FACTOR,
    PixelVectorizationForestResult,
    PixelVectorizationMaskPreview,
    PixelVectorizationResult,
    PixelVectorizationRootReviewFilter,
    PixelVectorizationSource,
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationCancelledError,
    RasterVectorizationError,
    RasterVectorizationOptions,
    RasterVectorizationTiming,
    prepare_pixel_vectorization_source,
    select_pixel_vectorization_auto_threshold,
    vectorize_pixel_source_forest,
)
from .camera_raster_normalization import (
    CameraRasterNormalizationResult,
    CameraRasterNormalizationTiming,
    normalize_camera_trace_frame,
)
from .camera_trace_eligibility import (
    CameraTraceEligibilityResult,
    CameraTraceEligibilityTiming,
    prepare_camera_trace_eligibility,
)

TRACE_MODES = {"auto", "color", "contrast"}
OUTPUT_MODES = {"rounded", "native", "smoothed", "exact"}
BORDER_OFFSET_MODES = {"uniform", "custom"}
NORMALIZE_ANCHORS = {"center", "top"}
CONTRAST_THRESHOLD_MODES = {"auto", "manual"}
MAX_TRACE_CONTOURS = 8_192
AUTO_MINIMUM_CREDIBLE_SCORE = 70.0
AUTO_COLOR_RASTER_MARGIN = 8.0
TraceDetectionCancelledError = RasterVectorizationCancelledError

_TRACE_CANCEL_CHECK = ContextVar["Callable[[], bool] | None"](
    "trace_detection_cancel_check",
    default=None,
)


def _trace_cancel_requested() -> bool:
    cancel_check = _TRACE_CANCEL_CHECK.get()
    return bool(cancel_check is not None and cancel_check())


def _check_trace_cancelled() -> None:
    if _trace_cancel_requested():
        raise TraceDetectionCancelledError("Trace detection was cancelled")


def _trace_cancel_kwargs() -> dict[str, Callable[[], bool]]:
    return (
        {}
        if _TRACE_CANCEL_CHECK.get() is None
        else {"cancel_check": _trace_cancel_requested}
    )


def _finite_option(value: Any, label: str) -> float:
    if type(value) is bool:
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _require_bgr_image(image: np.ndarray, label: str) -> None:
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim != 3
        or image.shape[2] != 3
    ):
        raise ValueError(f"{label} must be a uint8 BGR image")


@dataclass(slots=True)
class TraceOptions:
    detection_mode: str = "auto"
    target_hue: float | None = None
    target_bgr: tuple[int, int, int] | list[int] | None = None
    hue_tolerance: float = 14.0
    min_saturation: int = 45
    contrast_threshold_mode: str = "auto"
    contrast_threshold: int = 128
    contrast_invert: bool = False
    min_area_mm2: float = 30.0
    max_area_mm2: float = 20_000.0
    min_width_mm: float = 4.0
    min_height_mm: float = 3.0
    confidence_threshold: float = 0.55
    regular_grid: bool = True
    infer_missing: bool = True
    normalize_grid: bool = True
    snap_grid_cells: bool = True
    repair_grid_edges: bool = True
    normalize_anchor: str = "center"
    output_mode: str = "rounded"
    border_offset_mode: str = "uniform"
    border_offset_mm: float = 0.0
    border_offset_top_mm: float = 0.0
    border_offset_right_mm: float = 0.0
    border_offset_bottom_mm: float = 0.0
    border_offset_left_mm: float = 0.0
    smoothing_mm: float = 0.25
    native_fitting_tolerance_mm: float = 0.10

    def __post_init__(self) -> None:
        self.detection_mode = str(self.detection_mode).lower()
        if self.detection_mode not in TRACE_MODES:
            raise ValueError(f"Unknown detection mode: {self.detection_mode}")
        self.target_hue = None if self.target_hue is None else _finite_option(
            self.target_hue,
            "target_hue",
        ) % 180.0
        if self.target_bgr is not None:
            if not isinstance(self.target_bgr, (list, tuple)) or len(self.target_bgr) != 3:
                raise ValueError("target_bgr must contain exactly three channels")
            self.target_bgr = tuple(
                max(0, min(255, int(_finite_option(value, "target_bgr channel"))))
                for value in self.target_bgr
            )
        self.hue_tolerance = max(
            1.0,
            min(90.0, _finite_option(self.hue_tolerance, "hue_tolerance")),
        )
        self.min_saturation = max(
            0,
            min(255, int(_finite_option(self.min_saturation, "min_saturation"))),
        )
        self.contrast_threshold_mode = str(self.contrast_threshold_mode).lower()
        if self.contrast_threshold_mode not in CONTRAST_THRESHOLD_MODES:
            raise ValueError(
                "contrast_threshold_mode must be either 'auto' or 'manual'"
            )
        if type(self.contrast_threshold) is not int or not 0 <= self.contrast_threshold <= 255:
            raise ValueError("contrast_threshold must be an integer from 0 through 255")
        if type(self.contrast_invert) is not bool:
            raise ValueError("contrast_invert must be a JSON boolean")
        self.min_area_mm2 = max(
            0.01,
            _finite_option(self.min_area_mm2, "min_area_mm2"),
        )
        self.max_area_mm2 = max(
            self.min_area_mm2,
            _finite_option(self.max_area_mm2, "max_area_mm2"),
        )
        self.min_width_mm = max(
            0.1,
            _finite_option(self.min_width_mm, "min_width_mm"),
        )
        self.min_height_mm = max(
            0.1,
            _finite_option(self.min_height_mm, "min_height_mm"),
        )
        self.confidence_threshold = max(
            0.0,
            min(
                1.0,
                _finite_option(self.confidence_threshold, "confidence_threshold"),
            ),
        )
        for field_name in (
            "regular_grid",
            "infer_missing",
            "normalize_grid",
            "snap_grid_cells",
            "repair_grid_edges",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a JSON boolean")
        self.normalize_anchor = str(self.normalize_anchor).lower()
        if self.normalize_anchor not in NORMALIZE_ANCHORS:
            raise ValueError(
                f"Unknown identical-cell anchor: {self.normalize_anchor}"
            )
        self.output_mode = str(self.output_mode).lower()
        if self.output_mode not in OUTPUT_MODES:
            raise ValueError(f"Unknown output mode: {self.output_mode}")
        self.border_offset_mode = str(self.border_offset_mode).lower()
        if self.border_offset_mode not in BORDER_OFFSET_MODES:
            raise ValueError(
                f"Unknown border offset mode: {self.border_offset_mode}"
            )
        self.border_offset_mm = max(
            -25.0,
            min(25.0, _finite_option(self.border_offset_mm, "border_offset_mm")),
        )
        for field_name in (
            "border_offset_top_mm",
            "border_offset_right_mm",
            "border_offset_bottom_mm",
            "border_offset_left_mm",
        ):
            setattr(
                self,
                field_name,
                max(
                    -25.0,
                    min(25.0, _finite_option(getattr(self, field_name), field_name)),
                ),
            )
        self.smoothing_mm = max(
            0.0,
            min(10.0, _finite_option(self.smoothing_mm, "smoothing_mm")),
        )
        self.native_fitting_tolerance_mm = max(
            0.01,
            min(
                2.0,
                _finite_option(
                    self.native_fitting_tolerance_mm,
                    "native_fitting_tolerance_mm",
                ),
            ),
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> TraceOptions:
        raw = dict(raw or {})
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TraceDetection:
    id: str
    index: int
    source: str
    confidence: float
    score: float
    shape: str
    center_mm: tuple[float, float]
    width_mm: float
    height_mm: float
    rotation_deg: float
    corner_radius_mm: float
    area_mm2: float
    contour_mm: list[list[float]]
    vector_contour_mm: list[list[float]]
    vector_contours_mm: list[list[list[float]]]
    box_mm: list[list[float]]
    selected_default: bool
    diagnostics: dict[str, Any] = field(default_factory=dict)
    raw_contours_mm: list[list[list[float]]] = field(default_factory=list)
    native_path: dict[str, Any] | None = None
    native_center_mm: tuple[float, float] | None = None
    native_width_mm: float | None = None
    native_height_mm: float | None = None
    native_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["center_mm"] = list(self.center_mm)
        if self.native_center_mm is not None:
            payload["native_center_mm"] = list(self.native_center_mm)
        for key in (
            "raw_contours_mm",
            "native_path",
            "native_center_mm",
            "native_width_mm",
            "native_height_mm",
        ):
            if payload.get(key) in (None, []):
                payload.pop(key, None)
        if not self.native_verified:
            payload.pop("native_verified", None)
        return payload


@dataclass(slots=True)
class TraceResult:
    detected: bool
    detections: list[TraceDetection]
    mode_used: str
    target_hue: float | None
    image_width: int
    image_height: int
    direct_count: int
    inferred_count: int
    grid: dict[str, Any] | None
    message: str
    options: TraceOptions
    camera_work_area: dict[str, float] = field(default_factory=dict)
    output_work_area: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "detections": [item.to_dict() for item in self.detections],
            "mode_used": self.mode_used,
            "target_hue": self.target_hue,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "direct_count": self.direct_count,
            "inferred_count": self.inferred_count,
            "grid": self.grid,
            "message": self.message,
            "options": self.options.to_dict(),
            "camera_work_area": self.camera_work_area,
            "output_work_area": self.output_work_area,
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True, slots=True, eq=False)
class CameraTraceRasterPreview:
    """Authority-free pixels from the exact non-grid Trace raster preparation.

    ``foreground_mask`` is the source-resolution result representation;
    ``contour_mask`` is the exact production workspace passed to RETR_TREE.
    """

    strategy: str
    polarity: str
    camera_bgr: np.ndarray
    exposed_bed_mask: np.ndarray
    eligible_mask: np.ndarray
    normalized_grayscale: np.ndarray
    foreground_mask: np.ndarray
    contour_mask: np.ndarray
    threshold_used: int | None
    connected_component_count: int
    selected_strategy: bool = False
    native_fitting_completed: bool = False

    def __post_init__(self) -> None:
        strategy = str(self.strategy).strip()
        if not strategy:
            raise ValueError("A Camera Trace raster preview requires a strategy")
        polarity = str(self.polarity).strip().lower()
        if polarity not in {"dark", "light", "color"}:
            raise ValueError("Unknown Camera Trace preview polarity")
        arrays = (
            (self.camera_bgr, "camera_bgr", 3),
            (self.exposed_bed_mask, "exposed_bed_mask", 2),
            (self.eligible_mask, "eligible_mask", 2),
            (self.normalized_grayscale, "normalized_grayscale", 2),
            (self.foreground_mask, "foreground_mask", 2),
        )
        for values, label, dimensions in arrays:
            if not isinstance(values, np.ndarray):
                raise TypeError(f"{label} must be a numpy array")
            if values.dtype != np.uint8 or values.ndim != dimensions:
                raise ValueError(f"{label} must be a {dimensions}D uint8 array")
            if values.flags.writeable:
                raise ValueError(f"{label} must be read-only")
            if not values.flags.c_contiguous:
                raise ValueError(f"{label} must be C-contiguous")
        if self.camera_bgr.shape[2] != 3:
            raise ValueError("camera_bgr must contain three BGR channels")
        shape = self.camera_bgr.shape[:2]
        if (
            self.exposed_bed_mask.shape != shape
            or self.eligible_mask.shape != shape
            or self.normalized_grayscale.shape != shape
            or self.foreground_mask.shape != shape
        ):
            raise ValueError("Camera Trace preview arrays must have one image shape")
        if not isinstance(self.contour_mask, np.ndarray):
            raise TypeError("contour_mask must be a numpy array")
        if self.contour_mask.dtype != np.uint8 or self.contour_mask.ndim != 2:
            raise ValueError("contour_mask must be a 2D uint8 array")
        if self.contour_mask.flags.writeable:
            raise ValueError("contour_mask must be read-only")
        if not self.contour_mask.flags.c_contiguous:
            raise ValueError("contour_mask must be C-contiguous")
        expected_contour_shape = (
            shape
            if polarity == "color"
            else (
                shape[0] * RASTER_VECTORIZATION_OVERSAMPLE_FACTOR,
                shape[1] * RASTER_VECTORIZATION_OVERSAMPLE_FACTOR,
            )
        )
        if self.contour_mask.shape != expected_contour_shape:
            raise ValueError(
                "contour_mask must be the exact production contour workspace"
            )
        if self.threshold_used is not None:
            threshold = int(self.threshold_used)
            if threshold != self.threshold_used or not 0 <= threshold <= 255:
                raise ValueError("threshold_used must be a byte or None")
        if (
            type(self.connected_component_count) is not int
            or self.connected_component_count < 0
        ):
            raise ValueError("connected_component_count must be non-negative")
        if type(self.selected_strategy) is not bool:
            raise ValueError("selected_strategy must be a boolean")
        if type(self.native_fitting_completed) is not bool:
            raise ValueError("native_fitting_completed must be a boolean")
        object.__setattr__(self, "strategy", strategy)
        object.__setattr__(self, "polarity", polarity)


CameraTraceRasterPreviewCallback = Callable[[CameraTraceRasterPreview], None]


def _immutable_trace_preview_array(values: np.ndarray) -> np.ndarray:
    contiguous = np.ascontiguousarray(values)
    return np.frombuffer(
        contiguous.tobytes(order="C"),
        dtype=contiguous.dtype,
    ).reshape(contiguous.shape)


def _new_id() -> str:
    return f"trace-{uuid.uuid4().hex}"


def _hue_distance(hue: np.ndarray, target: float) -> np.ndarray:
    delta = np.abs(hue.astype(np.float32) - float(target))
    return np.minimum(delta, 180.0 - delta)


def auto_target_hue(
    image: np.ndarray,
    min_saturation: int = 45,
    *,
    eligibility_mask: np.ndarray | None = None,
) -> float | None:
    """Find the dominant chromatic hue while discounting neutral backgrounds."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1].astype(np.float32)
    a = lab[:, :, 1].astype(np.float32) - 128.0
    b = lab[:, :, 2].astype(np.float32) - 128.0
    chroma = np.hypot(a, b)
    weight = np.clip(
        (saturation - max(15.0, min_saturation * 0.55)) / 180.0,
        0.0,
        1.0,
    ) * np.clip((chroma - 5.0) / 70.0, 0.0, 1.0)
    pixel_count = image.shape[0] * image.shape[1]
    if eligibility_mask is not None:
        eligible = np.asarray(eligibility_mask) > 0
        if eligible.shape != image.shape[:2]:
            raise ValueError("Color eligibility must match the camera image")
        weight[~eligible] = 0.0
        pixel_count = int(np.count_nonzero(eligible))
    if not pixel_count or float(weight.sum()) < pixel_count * 0.005:
        return None
    histogram = np.bincount(
        hue.reshape(-1), weights=weight.reshape(-1), minlength=180
    ).astype(np.float64)
    radius = 5
    padded = np.concatenate((histogram[-radius:], histogram, histogram[:radius]))
    kernel = np.hanning(radius * 2 + 1)
    kernel /= max(float(kernel.sum()), 1e-12)
    smoothed = np.convolve(padded, kernel, mode="same")[radius:-radius]
    return float(np.argmax(smoothed))


@dataclass(frozen=True, slots=True)
class _AutoColorSuitability:
    suitable: bool
    target_hue: float | None
    reason: str
    chromatic_fraction: float
    dominant_weight_fraction: float
    dominance_ratio: float
    foreground_fraction: float
    border_foreground_fraction: float

    def diagnostics(self) -> dict[str, Any]:
        return {
            "chromatic_fraction": self.chromatic_fraction,
            "dominant_weight_fraction": self.dominant_weight_fraction,
            "dominance_ratio": self.dominance_ratio,
            "foreground_fraction": self.foreground_fraction,
            "border_foreground_fraction": self.border_foreground_fraction,
        }


def _mask_occupancy(
    mask: np.ndarray,
    eligibility_mask: np.ndarray | None = None,
) -> tuple[float, float]:
    """Return eligible-area and eligible-boundary foreground occupancy."""

    values = np.asarray(mask) > 0
    if values.ndim != 2 or not values.size:
        raise ValueError("Auto strategy masks must be non-empty and two-dimensional")
    if eligibility_mask is None:
        eligible = np.ones(values.shape, dtype=bool)
    else:
        eligible = np.asarray(eligibility_mask) > 0
        if eligible.shape != values.shape:
            raise ValueError("Auto strategy eligibility must match its mask")
    eligible_count = int(np.count_nonzero(eligible))
    if not eligible_count:
        return 0.0, 0.0
    eroded = cv2.erode(
        eligible.astype(np.uint8) * 255,
        np.ones((3, 3), dtype=np.uint8),
        borderType=cv2.BORDER_CONSTANT,
        borderValue=0,
    ) > 0
    border = eligible & ~eroded
    return (
        float(np.count_nonzero(values & eligible)) / float(eligible_count),
        float(np.count_nonzero(values & border))
        / float(max(1, np.count_nonzero(border))),
    )


def _analyze_auto_color_suitability(
    image: np.ndarray,
    options: TraceOptions,
    pixels_per_mm: float,
    eligibility_mask: np.ndarray,
) -> _AutoColorSuitability:
    """Gate Auto's Color attempt on coherent, non-background chroma evidence."""

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1].astype(np.float32)
    a = lab[:, :, 1].astype(np.float32) - 128.0
    b = lab[:, :, 2].astype(np.float32) - 128.0
    chroma = np.hypot(a, b)
    weight = np.clip((saturation - 24.75) / 180.0, 0.0, 1.0) * np.clip(
        (chroma - 5.0) / 70.0,
        0.0,
        1.0,
    )
    eligible = np.asarray(eligibility_mask) > 0
    if eligible.shape != image.shape[:2]:
        raise ValueError("Auto Color eligibility must match the camera image")
    weight[~eligible] = 0.0
    pixel_count = int(np.count_nonzero(eligible))
    total_weight = float(weight.sum())
    chromatic_fraction = float(np.count_nonzero(weight > 0.0)) / float(pixel_count)
    if total_weight < pixel_count * 0.005:
        return _AutoColorSuitability(
            False,
            None,
            "insufficient chroma",
            chromatic_fraction,
            0.0,
            0.0,
            0.0,
            0.0,
        )

    histogram = np.bincount(
        hue.reshape(-1), weights=weight.reshape(-1), minlength=180
    ).astype(np.float64)
    tolerance = 14
    window_mass = np.asarray(
        [
            sum(histogram[(center + offset) % 180] for offset in range(-tolerance, tolerance + 1))
            for center in range(180)
        ],
        dtype=np.float64,
    )
    target = int(np.argmax(window_mass))
    dominant_weight = float(window_mass[target])
    dominant_fraction = dominant_weight / max(total_weight, 1e-12)
    competitors = [
        float(window_mass[index])
        for index in range(180)
        if min(abs(index - target), 180 - abs(index - target)) > tolerance * 2
    ]
    competing_weight = max(competitors, default=0.0)
    dominance_ratio = dominant_weight / max(competing_weight, 1e-12)
    color_options = replace(
        options,
        target_hue=float(target),
        target_bgr=None,
        hue_tolerance=float(tolerance),
        min_saturation=45,
    )
    color_mask = _color_mask(
        image,
        float(target),
        color_options,
        pixels_per_mm,
        eligibility_mask=eligibility_mask,
    )
    foreground_fraction, border_fraction = _mask_occupancy(
        color_mask,
        eligibility_mask,
    )
    reason = "suitable"
    suitable = True
    if dominant_fraction < 0.60:
        suitable = False
        reason = "no coherent dominant hue"
    elif dominance_ratio < 1.50:
        suitable = False
        reason = "dominant hue is not distinct"
    elif foreground_fraction < 0.002:
        suitable = False
        reason = "chromatic foreground is too small"
    elif foreground_fraction > 0.35:
        suitable = False
        reason = "chromatic foreground is background-dominated"
    elif border_fraction > 0.25:
        suitable = False
        reason = "chromatic foreground dominates the eligible-material boundary"
    return _AutoColorSuitability(
        suitable,
        float(target),
        reason,
        chromatic_fraction,
        dominant_fraction,
        dominance_ratio,
        foreground_fraction,
        border_fraction,
    )


def _auto_strategy_quality(
    metrics: Mapping[str, Any],
) -> tuple[float | None, dict[str, float], str | None]:
    """Score one completed Auto strategy without rewarding candidate count."""

    valid_count = max(0, int(metrics.get("valid_candidate_count", 0)))
    invalid_count = max(0, int(metrics.get("invalid_candidate_count", 0)))
    root_count = max(valid_count + invalid_count, int(metrics.get("root_tree_count", 0)))
    unusable_count = max(invalid_count, root_count - valid_count)
    verified_count = max(0, int(metrics.get("verified_candidate_count", 0)))
    if not valid_count or verified_count != valid_count:
        return None, {}, "no authoritative verified native candidates"

    foreground_fraction = min(
        1.0, max(0.0, float(metrics.get("foreground_fraction", 0.0)))
    )
    border_fraction = min(
        1.0, max(0.0, float(metrics.get("border_foreground_fraction", 0.0)))
    )
    frame_area_mm2 = max(1e-12, float(metrics.get("frame_area_mm2", 0.0)))
    minimum_area_mm2 = max(1e-12, float(metrics.get("minimum_area_mm2", 0.0)))
    valid_area_mm2 = max(0.0, float(metrics.get("valid_area_mm2", 0.0)))
    microscopic_count = min(
        valid_count,
        max(0, int(metrics.get("microscopic_candidate_count", 0))),
    )
    within_count = min(
        valid_count,
        max(0, int(metrics.get("within_frame_candidate_count", 0))),
    )

    useful_floor = min(
        0.08,
        max(0.002, 0.25 * minimum_area_mm2 / frame_area_mm2),
    )
    if foreground_fraction < useful_floor:
        foreground_quality = foreground_fraction / useful_floor
    elif foreground_fraction <= 0.60:
        foreground_quality = 1.0
    elif foreground_fraction < 0.95:
        foreground_quality = (0.95 - foreground_fraction) / 0.35
    else:
        foreground_quality = 0.0
    valid_ratio = valid_count / max(1, valid_count + unusable_count)
    border_quality = 1.0 - border_fraction
    area_quality = min(
        1.0,
        valid_area_mm2 / max(4.0 * minimum_area_mm2, 0.01 * frame_area_mm2),
    )
    significant_root_quality = 1.0 - microscopic_count / valid_count
    within_quality = within_count / valid_count
    frame_penalty = (
        35.0
        if foreground_fraction >= 0.75 and border_fraction >= 0.75
        else 0.0
    )
    terms = {
        "valid_ratio": float(valid_ratio),
        "foreground_quality": float(foreground_quality),
        "border_quality": float(border_quality),
        "useful_area_quality": float(area_quality),
        "significant_root_quality": float(significant_root_quality),
        "within_frame_quality": float(within_quality),
        "frame_background_penalty": float(frame_penalty),
    }
    score = (
        40.0 * valid_ratio
        + 20.0 * foreground_quality
        + 15.0 * border_quality
        + 10.0 * area_quality
        + 10.0 * significant_root_quality
        + 5.0 * within_quality
        - frame_penalty
    )
    if foreground_fraction >= 0.95 and border_fraction >= 0.75:
        return None, terms, "foreground is dominated by the frame background"
    if score < AUTO_MINIMUM_CREDIBLE_SCORE:
        return None, terms, (
            f"quality score {score:.1f} is below the absolute credible floor "
            f"{AUTO_MINIMUM_CREDIBLE_SCORE:.1f}"
        )
    return round(float(score), 9), terms, None


def sample_color(
    image: np.ndarray,
    pixel_x: float,
    pixel_y: float,
    radius_px: int = 5,
) -> dict[str, Any]:
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("No image is available for color sampling")
    _require_bgr_image(image, "Color-sampling image")
    x_value = _finite_option(pixel_x, "pixel_x")
    y_value = _finite_option(pixel_y, "pixel_y")
    if type(radius_px) is not int or radius_px < 1:
        raise ValueError("Color-sampling radius must be a positive integer")
    height, width = image.shape[:2]
    x = int(round(x_value))
    y = int(round(y_value))
    radius = radius_px
    x0, x1 = max(0, x - radius), min(width, x + radius + 1)
    y0, y1 = max(0, y - radius), min(height, y + radius + 1)
    if x0 >= x1 or y0 >= y1:
        raise ValueError("Color sample lies outside the image")
    patch = image[y0:y1, x0:x1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    bgr = patch.reshape(-1, 3)
    median_bgr = np.median(bgr, axis=0)
    return {
        "hue": float(np.median(hsv[:, 0])),
        "saturation": float(np.median(hsv[:, 1])),
        "value": float(np.median(hsv[:, 2])),
        "bgr": [int(round(value)) for value in median_bgr],
        "rgb": [
            int(round(median_bgr[2])),
            int(round(median_bgr[1])),
            int(round(median_bgr[0])),
        ],
    }


def _color_mask(
    image: np.ndarray,
    target_hue: float,
    options: TraceOptions,
    pixels_per_mm: float,
    *,
    eligibility_mask: np.ndarray | None = None,
) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    a = lab[:, :, 1].astype(np.float32) - 128.0
    b = lab[:, :, 2].astype(np.float32) - 128.0
    chroma = np.hypot(a, b)
    hue_mask = (
        (_hue_distance(hsv[:, :, 0], target_hue) <= options.hue_tolerance)
        & (hsv[:, :, 1] >= options.min_saturation)
        & (hsv[:, :, 2] >= 20)
        & (chroma >= 7.0)
    )
    if options.target_bgr is not None:
        target = np.asarray(options.target_bgr, dtype=np.uint8).reshape(1, 1, 3)
        target_lab = cv2.cvtColor(target, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)
        lab_float = lab.astype(np.float32)
        delta = lab_float - target_lab
        # Lightness varies across a real bed much more than chroma. Weight it
        # less heavily so one sampled neutral or colored surface remains
        # selectable under uneven illumination while still separating it from
        # the warmer wood backing.
        distance = np.sqrt(
            0.16 * delta[:, :, 0] ** 2
            + delta[:, :, 1] ** 2
            + delta[:, :, 2] ** 2
        )
        lab_tolerance = max(10.0, options.hue_tolerance)
        mask_bool = (distance <= lab_tolerance) & (hsv[:, :, 2] >= 12)
    else:
        mask_bool = hue_mask
    mask = mask_bool.astype(np.uint8) * 255
    radius = max(1, int(round(max(1.0, pixels_per_mm * 0.45))))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    if eligibility_mask is not None:
        eligible = np.asarray(eligibility_mask) > 0
        if eligible.shape != mask.shape:
            raise ValueError("Color eligibility must match the camera image")
        mask[~eligible] = 0
    return mask


def _contrast_mask(image: np.ndarray, pixels_per_mm: float) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = max(15, int(round(pixels_per_mm * 8.0)))
    if scale % 2 == 0:
        scale += 1
    background = cv2.GaussianBlur(gray, (scale, scale), 0)
    contrast = cv2.absdiff(gray, background)
    threshold = max(8.0, float(np.percentile(contrast, 84)))
    _, mask = cv2.threshold(contrast, threshold, 255, cv2.THRESH_BINARY)
    radius = max(1, int(round(max(1.0, pixels_per_mm * 0.75))))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=3)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)


def _contrast_region_masks(
    image: np.ndarray,
    pixels_per_mm: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Segment darker and lighter regions without expanding their edges.

    The local absolute-contrast mask is useful for small artwork, but a filled
    object becomes a thick edge band whose external contour lies several
    millimetres outside the object. A much larger background estimate and a
    signed difference preserve the filled silhouette instead. Both masks are
    retained because real targets may be either darker or lighter than the bed.
    """

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    scale = max(31, int(round(pixels_per_mm * 36.0)))
    if scale % 2 == 0:
        scale += 1
    background = cv2.GaussianBlur(gray, (scale, scale), 0)
    radius = max(1, int(round(max(1.0, pixels_per_mm * 0.5))))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    masks: list[np.ndarray] = []
    for difference in (background - gray, gray - background):
        positive = difference[difference > 0]
        adaptive = 0.0 if positive.size == 0 else float(np.percentile(positive, 78))
        threshold = max(6.0, adaptive)
        mask = (difference >= threshold).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        masks.append(cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1))
    return masks[0], masks[1]


def _global_contrast_masks(
    image: np.ndarray,
    pixels_per_mm: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Segment whole dark/light regions at both raw and corrected contrast.

    Signed local contrast is intentionally retained for uneven beds, but its
    complementary polarity can describe the narrow highlight between two
    filled objects more cleanly than either object. Global luminance clustering
    supplies full-region hypotheses instead of forcing mask arbitration to
    choose between edge or gap bands. CLAHE provides the same hypotheses after
    bounded illumination correction when a broad gradient defeats raw Otsu.
    """

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur_size = max(3, int(round(pixels_per_mm * 1.25)))
    if blur_size % 2 == 0:
        blur_size += 1
    blurred = cv2.GaussianBlur(gray, (blur_size, blur_size), 0)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(blurred)
    radius = max(1, int(round(max(1.0, pixels_per_mm * 0.5))))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    masks: list[np.ndarray] = []
    for source in (blurred, clahe):
        _, dark = cv2.threshold(
            source,
            0,
            255,
            cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
        )
        for mask in (dark, cv2.bitwise_not(dark)):
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            masks.append(
                cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
            )
    return masks[0], masks[1], masks[2], masks[3]


def _adaptive_contrast_masks(
    image: np.ndarray,
    pixels_per_mm: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Segment filled regions against a slowly varying local background."""

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    minimum_dimension = min(gray.shape[:2])
    if minimum_dimension < 3:
        empty = np.zeros_like(gray)
        return empty, empty.copy()
    block_size = max(31, int(round(pixels_per_mm * 40.0)))
    if block_size % 2 == 0:
        block_size += 1
    maximum_block = (
        minimum_dimension
        if minimum_dimension % 2 == 1
        else minimum_dimension - 1
    )
    block_size = max(3, min(block_size, maximum_block))
    dark = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY_INV,
        block_size,
        4.0,
    )
    light = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        block_size,
        -4.0,
    )
    radius = max(1, int(round(max(1.0, pixels_per_mm * 0.5))))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    masks = []
    for mask in (dark, light):
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        masks.append(cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1))
    return masks[0], masks[1]


def _closed_outline_mask(
    image: np.ndarray,
    pixels_per_mm: float,
    options: TraceOptions,
) -> np.ndarray:
    """Fill strong closed outlines so hollow printed labels remain traceable."""

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(blurred))
    # Printed borders remain much thinner than filled targets. Keep the edge
    # thresholds deliberately below the later filled-region thresholds so a
    # pale label with dense interior text still contributes its outer loop.
    lower = max(20.0, min(40.0, median * 0.20))
    upper = max(lower + 35.0, min(120.0, median * 0.60))
    edges = cv2.Canny(blurred, lower, upper)
    kernel_width = max(3, int(round(pixels_per_mm * 1.25)))
    if kernel_width % 2 == 0:
        kernel_width += 1
    closed = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        np.ones((3, kernel_width), dtype=np.uint8),
        iterations=1,
    )
    contours, _ = cv2.findContours(
        closed,
        cv2.RETR_LIST,
        cv2.CHAIN_APPROX_NONE,
    )
    output = np.zeros_like(gray)
    minimum_area_px = options.min_area_mm2 * pixels_per_mm**2
    maximum_area_px = options.max_area_mm2 * pixels_per_mm**2
    minimum_width_px = options.min_width_mm * pixels_per_mm
    minimum_height_px = options.min_height_mm * pixels_per_mm
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if not minimum_area_px <= area <= maximum_area_px:
            continue
        rectangle = _long_axis_rect(contour)
        if rectangle is None:
            continue
        if (
            rectangle["width"] < minimum_width_px
            or rectangle["height"] < minimum_height_px
        ):
            continue
        cv2.drawContours(output, [contour], -1, 255, -1)
    return output


def _long_axis_rect(contour: np.ndarray) -> dict[str, Any] | None:
    rect = cv2.minAreaRect(contour.astype(np.float32))
    box = cv2.boxPoints(rect).astype(np.float64)
    vectors = np.roll(box, -1, axis=0) - box
    lengths = np.linalg.norm(vectors, axis=1)
    if float(lengths.min()) <= 1e-6:
        return None
    index = int(np.argmax(lengths))
    width = float(lengths[index])
    height = float(lengths[(index + 1) % 4])
    if height > width:
        width, height = height, width
        index = (index + 1) % 4
    vector = vectors[index] / max(float(lengths[index]), 1e-12)
    angle = math.degrees(math.atan2(float(vector[1]), float(vector[0])))
    while angle >= 90.0:
        angle -= 180.0
    while angle < -90.0:
        angle += 180.0
    return {
        "center": np.asarray(rect[0], dtype=np.float64),
        "width": width,
        "height": height,
        "angle_image_deg": angle,
        "box": box,
    }


def _normalize_long_axis_angle(angle_deg: float) -> float:
    angle = float(angle_deg)
    while angle >= 90.0:
        angle -= 180.0
    while angle < -90.0:
        angle += 180.0
    return angle


def _angle_delta_degrees(first: float, second: float) -> float:
    return abs(_normalize_long_axis_angle(float(first) - float(second)))


def _mean_long_axis_angle(
    values: Sequence[float],
    weights: Sequence[float] | None = None,
) -> float:
    if not values:
        raise ValueError("At least one angle is required")
    radians = np.radians(np.asarray(values, dtype=np.float64) * 2.0)
    if weights is None:
        weight_array = np.ones(len(values), dtype=np.float64)
    else:
        weight_array = np.asarray(weights, dtype=np.float64)
    sine = float(np.sum(np.sin(radians) * weight_array))
    cosine = float(np.sum(np.cos(radians) * weight_array))
    return _normalize_long_axis_angle(
        math.degrees(math.atan2(sine, cosine)) / 2.0
    )


def _straight_edge_angle(
    contour: np.ndarray,
    rectangle: Mapping[str, Any],
) -> tuple[float | None, dict[str, Any]]:
    """Estimate label rotation from straight sides instead of the whole hull.

    ``minAreaRect`` can rotate slightly when a corner is damaged, a shadow
    changes one boundary, or a rounded edge is incomplete.  For elongated
    rounded rectangles the long straight top/bottom segments are a stronger
    angle reference.  The short sides are fitted independently as a consistency
    check, but never bias the final long-edge angle.
    """

    points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    if len(points) < 16:
        return None, {"accepted": False, "reason": "too_few_contour_points"}

    width = float(rectangle["width"])
    height = float(rectangle["height"])
    if width <= 1e-6 or height <= 1e-6 or width / height < 1.35:
        return None, {"accepted": False, "reason": "shape_not_elongated"}

    initial = float(rectangle["angle_image_deg"])
    angle = math.radians(initial)
    u = np.array([math.cos(angle), math.sin(angle)], dtype=np.float64)
    v = np.array([-u[1], u[0]], dtype=np.float64)
    center = np.asarray(rectangle["center"], dtype=np.float64)
    local = points - center
    along_u = local @ u
    along_v = local @ v

    # Fit only central edge spans so rounded corners do not influence the line.
    long_center = np.abs(along_u) <= width * 0.34
    short_center = np.abs(along_v) <= height * 0.26
    masks = {
        "top": long_center & (along_v <= -height * 0.20),
        "bottom": long_center & (along_v >= height * 0.20),
        "left": short_center & (along_u <= -width * 0.20),
        "right": short_center & (along_u >= width * 0.20),
    }

    def fit_edge(
        name: str,
        expected_span: float,
        vertical: bool,
    ) -> dict[str, Any] | None:
        edge_points = points[masks[name]]
        if len(edge_points) < 6:
            return None
        line = cv2.fitLine(
            edge_points.astype(np.float32),
            cv2.DIST_WELSCH,
            0,
            0.01,
            0.01,
        ).reshape(-1)
        direction = np.asarray(line[:2], dtype=np.float64)
        norm = float(np.linalg.norm(direction))
        if norm <= 1e-9:
            return None
        direction /= norm
        origin = np.asarray(line[2:4], dtype=np.float64)
        relative = edge_points - origin
        span_values = relative @ direction
        span = float(
            np.percentile(span_values, 95) - np.percentile(span_values, 5)
        )
        normal = np.array([-direction[1], direction[0]], dtype=np.float64)
        residuals = np.abs(relative @ normal)
        residual = float(np.median(residuals))
        raw_angle = math.degrees(
            math.atan2(float(direction[1]), float(direction[0]))
        )
        long_angle = _normalize_long_axis_angle(
            raw_angle - (90.0 if vertical else 0.0)
        )
        minimum_span = expected_span * (0.36 if vertical else 0.50)
        maximum_residual = max(
            1.25,
            height * (0.030 if not vertical else 0.035),
        )
        accepted = span >= minimum_span and residual <= maximum_residual
        return {
            "name": name,
            "accepted": accepted,
            "angle_deg": long_angle,
            "span_px": span,
            "median_residual_px": residual,
            "point_count": int(len(edge_points)),
            "quality": (
                max(0.0, min(1.0, span / max(expected_span, 1e-9)))
                * max(0.0, min(1.0, maximum_residual / max(residual, 0.25)))
            ),
        }

    fits = {
        "top": fit_edge("top", width, False),
        "bottom": fit_edge("bottom", width, False),
        "left": fit_edge("left", height, True),
        "right": fit_edge("right", height, True),
    }

    def pair(
        first: str,
        second: str,
        tolerance: float,
    ) -> tuple[float, float] | None:
        a, b = fits[first], fits[second]
        if not a or not b or not a["accepted"] or not b["accepted"]:
            return None
        if (
            _angle_delta_degrees(
                float(a["angle_deg"]), float(b["angle_deg"])
            )
            > tolerance
        ):
            return None
        weights = [float(a["quality"]), float(b["quality"])]
        return (
            _mean_long_axis_angle(
                [float(a["angle_deg"]), float(b["angle_deg"])],
                weights,
            ),
            max(1e-6, sum(weights)),
        )

    long_pair = pair("top", "bottom", 1.75)
    short_pair = pair("left", "right", 2.25)
    chosen: float | None = None
    source = "fallback"
    if long_pair is not None and short_pair is not None:
        if _angle_delta_degrees(long_pair[0], short_pair[0]) <= 1.75:
            # Short sides only verify the result. Their baseline is much shorter
            # and therefore more sensitive to pixel stair-stepping.
            chosen = long_pair[0]
            source = "long_edges_crosschecked"
        else:
            # Long sides have much more baseline on label-shaped parts.
            chosen = long_pair[0]
            source = "long_edges"
    elif long_pair is not None:
        chosen = long_pair[0]
        source = "long_edges"
    elif short_pair is not None:
        # Short sides alone are intentionally insufficient.
        chosen = None
        source = "short_edges_only"

    diagnostics = {
        "accepted": chosen is not None,
        "source": source,
        "initial_angle_deg": initial,
        "refined_angle_deg": chosen,
        "change_deg": (
            None
            if chosen is None
            else _angle_delta_degrees(chosen, initial)
        ),
        "edges": fits,
    }
    return chosen, diagnostics


def _straight_edge_center(
    contour: np.ndarray,
    rectangle: Mapping[str, Any],
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Refine an elongated rounded-rectangle center from opposing straight edges.

    A small corner/shadow defect can move ``minAreaRect``'s center even after the
    long-edge angle is corrected.  Fit top/bottom and left/right independently,
    then use only trustworthy opposing pairs.  Each axis is accepted separately;
    large one-sided damage therefore falls back on that axis instead of pulling
    the cut toward a bad edge.
    """

    points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    if len(points) < 16:
        return None, {"accepted": False, "reason": "too_few_contour_points"}

    width = float(rectangle["width"])
    height = float(rectangle["height"])
    if width <= 1e-6 or height <= 1e-6 or width / height < 1.35:
        return None, {"accepted": False, "reason": "shape_not_elongated"}

    center = np.asarray(rectangle["center"], dtype=np.float64)
    angle = math.radians(float(rectangle["angle_image_deg"]))
    u = np.array([math.cos(angle), math.sin(angle)], dtype=np.float64)
    v = np.array([-u[1], u[0]], dtype=np.float64)
    local = points - center
    along_u = local @ u
    along_v = local @ v

    # Exclude rounded corners.  These spans mirror the rotation estimator so
    # center and angle are derived from the same physical straight-edge evidence.
    long_center = np.abs(along_u) <= width * 0.34
    short_center = np.abs(along_v) <= height * 0.26
    masks = {
        "top": long_center & (along_v <= -height * 0.20),
        "bottom": long_center & (along_v >= height * 0.20),
        "left": short_center & (along_u <= -width * 0.20),
        "right": short_center & (along_u >= width * 0.20),
    }

    def fit_edge(
        name: str,
        expected_span: float,
        *,
        horizontal: bool,
    ) -> dict[str, Any] | None:
        edge_points = points[masks[name]]
        if len(edge_points) < 6:
            return None
        line = cv2.fitLine(
            edge_points.astype(np.float32),
            cv2.DIST_WELSCH,
            0,
            0.01,
            0.01,
        ).reshape(-1)
        direction = np.asarray(line[:2], dtype=np.float64)
        norm = float(np.linalg.norm(direction))
        if norm <= 1e-9:
            return None
        direction /= norm
        origin = np.asarray(line[2:4], dtype=np.float64)

        tangent = u if horizontal else v
        center_axis = v if horizontal else u
        denominator = float(direction @ tangent)
        if abs(denominator) < 0.55:
            return None
        parameter = -float((origin - center) @ tangent) / denominator
        center_crossing = origin + direction * parameter
        coordinate = float((center_crossing - center) @ center_axis)

        relative = edge_points - origin
        span_values = relative @ direction
        span = float(
            np.percentile(span_values, 95) - np.percentile(span_values, 5)
        )
        line_normal = np.array(
            [-direction[1], direction[0]], dtype=np.float64
        )
        residual = float(np.median(np.abs(relative @ line_normal)))
        raw_angle = math.degrees(
            math.atan2(float(direction[1]), float(direction[0]))
        )
        edge_angle = _normalize_long_axis_angle(
            raw_angle - (0.0 if horizontal else 90.0)
        )
        minimum_span = expected_span * (0.50 if horizontal else 0.36)
        maximum_residual = max(
            1.25,
            height * (0.030 if horizontal else 0.035),
        )
        accepted = (
            span >= minimum_span
            and residual <= maximum_residual
            and _angle_delta_degrees(
                edge_angle, float(rectangle["angle_image_deg"])
            )
            <= 2.50
        )
        return {
            "name": name,
            "accepted": accepted,
            "coordinate_px": coordinate,
            "angle_deg": edge_angle,
            "span_px": span,
            "median_residual_px": residual,
            "point_count": int(len(edge_points)),
        }

    fits = {
        "top": fit_edge("top", width, horizontal=True),
        "bottom": fit_edge("bottom", width, horizontal=True),
        "left": fit_edge("left", height, horizontal=False),
        "right": fit_edge("right", height, horizontal=False),
    }

    def pair_offset(
        first: str,
        second: str,
        expected_separation: float,
        angle_tolerance: float,
    ) -> tuple[float | None, str | None]:
        first_fit = fits[first]
        second_fit = fits[second]
        if (
            first_fit is None
            or second_fit is None
            or not first_fit["accepted"]
            or not second_fit["accepted"]
        ):
            return None, "edge_fit_unavailable"
        if (
            _angle_delta_degrees(
                float(first_fit["angle_deg"]),
                float(second_fit["angle_deg"]),
            )
            > angle_tolerance
        ):
            return None, "opposing_edges_disagree"
        first_coordinate = float(first_fit["coordinate_px"])
        second_coordinate = float(second_fit["coordinate_px"])
        separation = second_coordinate - first_coordinate
        if separation <= 0.0:
            return None, "edge_order_invalid"
        separation_tolerance = max(3.0, expected_separation * 0.08)
        if abs(separation - expected_separation) > separation_tolerance:
            return None, "edge_separation_mismatch"
        offset = (first_coordinate + second_coordinate) / 2.0
        maximum_shift = max(3.0, expected_separation * 0.06)
        if abs(offset) > maximum_shift:
            return None, "center_shift_too_large"
        return offset, None

    offset_u, x_reason = pair_offset("left", "right", width, 2.25)
    offset_v, y_reason = pair_offset("top", "bottom", height, 1.75)
    if offset_u is None and offset_v is None:
        return None, {
            "accepted": False,
            "reason": "no_trustworthy_edge_pair",
            "x_rejection_reason": x_reason,
            "y_rejection_reason": y_reason,
            "edges": fits,
        }

    shift_u = 0.0 if offset_u is None else offset_u
    shift_v = 0.0 if offset_v is None else offset_v
    refined = center + u * shift_u + v * shift_v
    return refined, {
        "accepted": True,
        "initial_center_px": [float(center[0]), float(center[1])],
        "refined_center_px": [float(refined[0]), float(refined[1])],
        "offset_u_px": offset_u,
        "offset_v_px": offset_v,
        "shift_px": float(np.linalg.norm(refined - center)),
        "x_rejection_reason": x_reason,
        "y_rejection_reason": y_reason,
        "edges": fits,
    }


def _rounded_mask(width: int, height: int, radius: int) -> np.ndarray:
    width, height = max(2, int(width)), max(2, int(height))
    radius = max(0, min(int(radius), width // 2, height // 2))
    mask = np.zeros((height, width), dtype=np.uint8)
    if radius == 0:
        mask[:] = 255
        return mask
    cv2.rectangle(mask, (radius, 0), (width - radius - 1, height - 1), 255, -1)
    cv2.rectangle(mask, (0, radius), (width - 1, height - radius - 1), 255, -1)
    for center in (
        (radius, radius),
        (width - radius - 1, radius),
        (width - radius - 1, height - radius - 1),
        (radius, height - radius - 1),
    ):
        cv2.circle(mask, center, radius, 255, -1)
    return mask


def _rounded_fit(contour: np.ndarray, rectangle: Mapping[str, Any]) -> tuple[float, float]:
    # minAreaRect measures the center-to-center span of the outermost raster
    # samples. A run from pixel 0 through pixel N-1 therefore reports N-1, not
    # N. Reconstruct the discrete extent before comparing candidate masks.
    width = max(4, int(round(float(rectangle["width"]))) + 1)
    height = max(4, int(round(float(rectangle["height"]))) + 1)
    center = np.asarray(rectangle["center"], dtype=np.float64)
    angle = math.radians(float(rectangle["angle_image_deg"]))
    u = np.array([math.cos(angle), math.sin(angle)], dtype=np.float64)
    v = np.array([-u[1], u[0]], dtype=np.float64)
    local = contour.reshape(-1, 2).astype(np.float64) - center
    local = np.column_stack((local @ u, local @ v))
    margin = 4
    local[:, 0] += (width - 1) / 2.0 + margin
    local[:, 1] += (height - 1) / 2.0 + margin
    observed = np.zeros((height + 2 * margin, width + 2 * margin), dtype=np.uint8)
    cv2.fillPoly(observed, [np.round(local).astype(np.int32)], 255)
    best_radius, best_iou = 0.0, 0.0
    maximum = int(round(min(width, height) * 0.50))
    coarse_step = max(1, int(math.ceil(maximum / 24.0)))
    coarse_radii = list(range(0, maximum + 1, coarse_step))
    if not coarse_radii or coarse_radii[-1] != maximum:
        coarse_radii.append(maximum)

    def evaluate(radius: int) -> float:
        candidate = np.zeros_like(observed)
        candidate[margin:margin + height, margin:margin + width] = _rounded_mask(
            width, height, radius
        )
        intersection = np.count_nonzero((candidate > 0) & (observed > 0))
        union = np.count_nonzero((candidate > 0) | (observed > 0))
        return 0.0 if union == 0 else float(intersection / union)

    for radius in coarse_radii:
        iou = evaluate(radius)
        if iou > best_iou:
            best_radius, best_iou = float(radius), iou

    fine_start = max(0, int(best_radius) - coarse_step)
    fine_end = min(maximum, int(best_radius) + coarse_step)
    for radius in range(fine_start, fine_end + 1):
        iou = evaluate(radius)
        if iou > best_iou:
            best_radius, best_iou = float(radius), iou
    return best_radius, best_iou


def _offset_contour(
    contour: np.ndarray,
    offset_px: float,
    smoothing_px: float,
) -> np.ndarray:
    contour = contour.reshape(-1, 1, 2).astype(np.int32)
    x, y, width, height = cv2.boundingRect(contour)
    margin = max(8, int(math.ceil(abs(offset_px))) + 5)
    local = contour.copy()
    local[:, 0, 0] -= x - margin
    local[:, 0, 1] -= y - margin
    mask = np.zeros((height + 2 * margin, width + 2 * margin), dtype=np.uint8)
    cv2.fillPoly(mask, [local], 255)
    radius = int(round(abs(offset_px)))
    if radius:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)
        )
        mask = (cv2.dilate if offset_px > 0 else cv2.erode)(mask, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return contour.reshape(-1, 2).astype(np.float64)
    output = max(contours, key=cv2.contourArea)
    if smoothing_px > 0:
        output = cv2.approxPolyDP(output, smoothing_px, True)
    points = output.reshape(-1, 2).astype(np.float64)
    points[:, 0] += x - margin
    points[:, 1] += y - margin
    return points


def _pixel_to_machine(
    points: np.ndarray,
    work_area: WorkArea,
    pixels_per_mm: float,
) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    output = np.empty_like(points)
    output[:, 0] = work_area.x_min + points[:, 0] / pixels_per_mm
    output[:, 1] = work_area.y_max - points[:, 1] / pixels_per_mm
    return output


def _machine_geometry(
    rectangle: Mapping[str, Any],
    work_area: WorkArea,
    pixels_per_mm: float,
    offset_mm: float,
    *,
    edge_offsets_mm: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    center = _pixel_to_machine(
        np.asarray(rectangle["center"]).reshape(1, 2), work_area, pixels_per_mm
    )[0]
    rotation = -float(rectangle["angle_image_deg"])
    base_width = float(rectangle["width"]) / pixels_per_mm
    base_height = float(rectangle["height"]) / pixels_per_mm
    if edge_offsets_mm is None:
        top = right = bottom = left = float(offset_mm)
        radius_offset = float(offset_mm)
    else:
        top = float(edge_offsets_mm["top"])
        right = float(edge_offsets_mm["right"])
        bottom = float(edge_offsets_mm["bottom"])
        left = float(edge_offsets_mm["left"])
        # Per-edge adjustment moves the selected side and its adjoining
        # corners. Retaining the fitted radius keeps the other three sides
        # geometrically unchanged instead of applying a second global offset.
        radius_offset = 0.0
    width = max(0.01, base_width + left + right)
    height = max(0.01, base_height + bottom + top)
    angle = math.radians(rotation)
    u = np.array([math.cos(angle), math.sin(angle)])
    v = np.array([-u[1], u[0]])
    center = (
        center
        + u * (right - left) / 2.0
        + v * (top - bottom) / 2.0
    )
    radius = max(
        0.0,
        float(rectangle.get("radius_px", 0.0)) / pixels_per_mm + radius_offset,
    )
    radius = min(radius, width / 2.0, height / 2.0)
    box = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        point = center + u * sx * width / 2 + v * sy * height / 2
        box.append([float(point[0]), float(point[1])])
    return {
        "center_mm": (float(center[0]), float(center[1])),
        "width_mm": width,
        "height_mm": height,
        "rotation_deg": rotation,
        "corner_radius_mm": radius,
        "box_mm": box,
    }


def _custom_edge_offsets(options: TraceOptions) -> dict[str, float] | None:
    if (
        options.output_mode != "rounded"
        or options.border_offset_mode != "custom"
    ):
        return None
    return {
        "top": options.border_offset_top_mm,
        "right": options.border_offset_right_mm,
        "bottom": options.border_offset_bottom_mm,
        "left": options.border_offset_left_mm,
    }


def _work_area_overrun_mm(
    points: Sequence[Sequence[float]],
    work_area: WorkArea,
) -> float:
    return max(_work_area_overruns_mm(points, work_area).values(), default=0.0)


def _work_area_overruns_mm(
    points: Sequence[Sequence[float]],
    work_area: WorkArea,
) -> dict[str, float]:
    coordinates = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if not len(coordinates):
        return {"left": 0.0, "right": 0.0, "bottom": 0.0, "top": 0.0}
    return {
        "left": max(0.0, float(work_area.x_min - np.min(coordinates[:, 0]))),
        "right": max(0.0, float(np.max(coordinates[:, 0]) - work_area.x_max)),
        "bottom": max(0.0, float(work_area.y_min - np.min(coordinates[:, 1]))),
        "top": max(0.0, float(np.max(coordinates[:, 1]) - work_area.y_max)),
    }


def _detection_within_output_polygon(
    detection: TraceDetection,
    output_polygon: ConvexPolygon | None,
) -> bool:
    if output_polygon is None:
        return True
    contours = detection.vector_contours_mm or [detection.vector_contour_mm]
    return all(
        convex_polygon_violation_normalized_mm(point, output_polygon) <= 1e-9
        for contour in contours
        for point in contour
    )


def _hard_roi_boundary_component_labels(
    foreground_mask: np.ndarray,
    eligibility_mask: np.ndarray,
) -> tuple[np.ndarray, frozenset[int]]:
    eligible = np.asarray(eligibility_mask) > 0
    eroded = cv2.erode(
        eligible.astype(np.uint8) * 255,
        np.ones((3, 3), dtype=np.uint8),
        borderType=cv2.BORDER_CONSTANT,
        borderValue=0,
    ) > 0
    boundary = eligible & ~eroded
    _count, labels = cv2.connectedComponents(
        (np.asarray(foreground_mask) > 0).astype(np.uint8),
        connectivity=8,
    )
    touching = frozenset(
        int(value)
        for value in np.unique(labels[boundary & (labels > 0)])
    )
    return labels, touching


def _detection_uses_component_labels(
    detection: TraceDetection,
    labels: np.ndarray,
    rejected_labels: frozenset[int],
    work_area: WorkArea,
    pixels_per_mm: float,
) -> bool:
    if not rejected_labels:
        return False
    contours = detection.raw_contours_mm or [detection.contour_mm]
    height, width = labels.shape
    for contour in contours:
        points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
        columns = np.rint(
            (points[:, 0] - float(work_area.x_min)) * pixels_per_mm
        ).astype(np.int64)
        rows = np.rint(
            (float(work_area.y_max) - points[:, 1]) * pixels_per_mm
        ).astype(np.int64)
        inside = (
            (columns >= 0)
            & (columns < width)
            & (rows >= 0)
            & (rows < height)
        )
        for row, column in zip(rows[inside], columns[inside], strict=True):
            row_min = max(0, int(row) - 1)
            row_max = min(height, int(row) + 2)
            column_min = max(0, int(column) - 1)
            column_max = min(width, int(column) + 2)
            if any(
                int(value) in rejected_labels
                for value in np.unique(
                    labels[row_min:row_max, column_min:column_max]
                )
            ):
                return True
    return False


def _candidate(
    contour: np.ndarray,
    mask: np.ndarray,
    options: TraceOptions,
    work_area: WorkArea,
    output_work_area: WorkArea,
    pixels_per_mm: float,
) -> dict[str, Any] | None:
    area_px = float(cv2.contourArea(contour))
    area_mm2 = area_px / pixels_per_mm**2
    if not options.min_area_mm2 <= area_mm2 <= options.max_area_mm2:
        return None
    rectangle = _long_axis_rect(contour)
    if rectangle is None:
        return None
    if (
        rectangle["width"] / pixels_per_mm < options.min_width_mm
        or rectangle["height"] / pixels_per_mm < options.min_height_mm
    ):
        return None
    rect_area = max(1.0, rectangle["width"] * rectangle["height"])
    rectangularity = min(1.0, area_px / rect_area)
    hull_area = max(1.0, float(cv2.contourArea(cv2.convexHull(contour))))
    solidity = min(1.0, area_px / hull_area)
    radius_px, fit_iou = _rounded_fit(contour, rectangle)
    rectangle["radius_px"] = radius_px
    region = np.zeros_like(mask)
    cv2.drawContours(region, [contour], -1, 255, -1)
    pixels = region > 0
    coverage = 0.0 if not np.any(pixels) else float(np.mean(mask[pixels] > 0))
    perimeter = max(1.0, float(cv2.arcLength(contour, True)))
    compactness = max(0.0, min(1.0, 4.0 * math.pi * area_px / (perimeter * perimeter)))
    aspect = min(rectangle["width"], rectangle["height"]) / max(
        rectangle["width"], rectangle["height"]
    )
    approximation = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
    analytic_shape: str | None = None
    if compactness >= 0.84 and solidity >= 0.94 and aspect >= 0.90:
        analytic_shape = "circle"
    elif (
        compactness >= 0.66
        and solidity >= 0.94
        and rectangularity < 0.90
        and len(contour) >= 5
    ):
        analytic_shape = "ellipse"
    elif len(approximation) == 3 and solidity >= 0.90:
        analytic_shape = "triangle"
    elif (
        4 <= len(approximation) <= 12
        and solidity >= 0.90
        and rectangularity < 0.76
    ):
        analytic_shape = "regular_polygon"
    rounded = (
        analytic_shape is None
        and rectangularity >= 0.78
        and solidity >= 0.82
        and fit_iou >= 0.82
    )
    if analytic_shape is not None:
        shape = analytic_shape
        score = 0.44 * solidity + 0.28 * coverage + 0.28 * compactness
        confidence = max(0.0, min(1.0, (score - 0.45) / 0.5))
    elif rounded:
        shape = "rounded_rectangle"
        score = (
            0.48 * fit_iou
            + 0.18 * rectangularity
            + 0.14 * solidity
            + 0.20 * coverage
        )
        confidence = max(0.0, min(1.0, (score - 0.35) / 0.55))
    else:
        # Non-grid tracing may preserve arbitrary colored silhouettes. A regular
        # label grid remains intentionally strict so scratches and merged parts
        # cannot seed false inferred cells.
        if options.regular_grid or solidity < 0.42 or coverage < 0.42:
            return None
        shape = "contour"
        score = 0.42 * solidity + 0.33 * coverage + 0.25 * compactness
        confidence = max(0.0, min(1.0, (score - 0.30) / 0.55))
    straight_edge_diagnostics: dict[str, Any] | None = None
    straight_edge_center_diagnostics: dict[str, Any] | None = None
    if rounded:
        refined_angle, straight_edge_diagnostics = _straight_edge_angle(
            contour, rectangle
        )
        if refined_angle is not None:
            rectangle["angle_image_deg"] = refined_angle
        refined_center, straight_edge_center_diagnostics = _straight_edge_center(
            contour, rectangle
        )
        if refined_center is not None:
            rectangle["center"] = refined_center

    custom_edge_offsets = (
        _custom_edge_offsets(options)
        if options.output_mode == "rounded" and rounded
        else None
    )
    geometry = _machine_geometry(
        rectangle,
        work_area,
        pixels_per_mm,
        options.border_offset_mm,
        edge_offsets_mm=custom_edge_offsets,
    )
    contour_offset_mm = (
        options.border_offset_mm if custom_edge_offsets is None else 0.0
    )
    points = _offset_contour(
        contour,
        contour_offset_mm * pixels_per_mm,
        0.0 if options.output_mode == "exact" else options.smoothing_mm * pixels_per_mm,
    )
    contour_mm = _pixel_to_machine(points, work_area, pixels_per_mm)
    observed_contour_mm = [[float(x), float(y)] for x, y in contour_mm]
    vector_contour_mm = (
        _rounded_polyline(
            geometry["center_mm"],
            geometry["width_mm"],
            geometry["height_mm"],
            geometry["rotation_deg"],
            geometry["corner_radius_mm"],
        )
        if options.output_mode == "rounded" and shape == "rounded_rectangle"
        else observed_contour_mm
    )
    if options.output_mode == "rounded" and shape in {"circle", "ellipse"}:
        cx, cy = geometry["center_mm"]
        angle = math.radians(geometry["rotation_deg"])
        cosine, sine = math.cos(angle), math.sin(angle)
        vector_contour_mm = []
        for step in range(73):
            theta = 2.0 * math.pi * step / 72
            x = geometry["width_mm"] * 0.5 * math.cos(theta)
            y = geometry["height_mm"] * 0.5 * math.sin(theta)
            vector_contour_mm.append(
                [
                    cx + x * cosine - y * sine,
                    cy + x * sine + y * cosine,
                ]
            )
    elif options.output_mode == "rounded" and shape in {
        "triangle",
        "regular_polygon",
    }:
        converted = _pixel_to_machine(
            approximation.reshape(-1, 2), work_area, pixels_per_mm
        )
        vector_contour_mm = [[float(x), float(y)] for x, y in converted]
    contour_points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    image_height, image_width = mask.shape[:2]
    image_edge_sides = []
    if float(np.min(contour_points[:, 0])) <= 0.0:
        image_edge_sides.append("left")
    if float(np.max(contour_points[:, 0])) >= image_width - 1.0:
        image_edge_sides.append("right")
    if float(np.min(contour_points[:, 1])) <= 0.0:
        image_edge_sides.append("top")
    if float(np.max(contour_points[:, 1])) >= image_height - 1.0:
        image_edge_sides.append("bottom")
    camera_work_area_overruns_mm = _work_area_overruns_mm(
        vector_contour_mm,
        work_area,
    )
    work_area_overruns_mm = _work_area_overruns_mm(
        vector_contour_mm,
        output_work_area,
    )
    camera_work_area_overrun_mm = max(camera_work_area_overruns_mm.values())
    work_area_overrun_mm = max(work_area_overruns_mm.values())
    return {
        "source_contour_px": np.asarray(contour, dtype=np.float64).reshape(-1, 2),
        "center_px": np.asarray(rectangle["center"], dtype=np.float64),
        "width_px": float(rectangle["width"]),
        "height_px": float(rectangle["height"]),
        "angle_image_deg": float(rectangle["angle_image_deg"]),
        "straight_edge_rotation": straight_edge_diagnostics,
        "straight_edge_center": straight_edge_center_diagnostics,
        "radius_px": radius_px,
        "area_mm2": area_mm2,
        "score": score,
        "confidence": confidence,
        "rectangularity": rectangularity,
        "solidity": solidity,
        "fit_iou": fit_iou,
        "coverage": coverage,
        "compactness": compactness,
        "shape": shape,
        "touches_image_edge": bool(image_edge_sides),
        "image_edge_sides": image_edge_sides,
        "within_camera_work_area": camera_work_area_overrun_mm <= 1e-9,
        "camera_work_area_overrun_mm": camera_work_area_overrun_mm,
        "camera_work_area_overruns_mm": camera_work_area_overruns_mm,
        "within_work_area": work_area_overrun_mm <= 1e-9,
        "work_area_overrun_mm": work_area_overrun_mm,
        "work_area_overruns_mm": work_area_overruns_mm,
        "contour_mm": observed_contour_mm,
        "vector_contour_mm": vector_contour_mm,
        **geometry,
    }


def _mean_angle(angles: Sequence[float]) -> float:
    radians = np.radians(np.asarray(angles, dtype=float) * 2.0)
    return math.degrees(
        math.atan2(float(np.sin(radians).mean()), float(np.cos(radians).mean()))
        / 2.0
    )


def _clusters(values: Sequence[float], tolerance: float) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(float(item) for item in values):
        if not groups or abs(value - float(np.mean(groups[-1]))) > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [float(np.mean(group)) for group in groups]


def _rounded_polyline(
    center: tuple[float, float],
    width: float,
    height: float,
    rotation: float,
    radius: float,
    segments_per_corner: int = 8,
) -> list[list[float]]:
    """Sample the fitted rounded rectangle used by rounded-vector output."""

    radius = max(0.0, min(radius, width / 2, height / 2))
    segments = max(1, int(segments_per_corner))
    local = []
    for cx, cy, start in (
        (width / 2 - radius, height / 2 - radius, 0),
        (-width / 2 + radius, height / 2 - radius, 90),
        (-width / 2 + radius, -height / 2 + radius, 180),
        (width / 2 - radius, -height / 2 + radius, 270),
    ):
        for step in range(segments + 1):
            angle = math.radians(start + 90 * step / segments)
            local.append([cx + radius * math.cos(angle), cy + radius * math.sin(angle)])
    points = np.asarray(local)
    angle = math.radians(rotation)
    matrix = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    world = points @ matrix.T + np.asarray(center)
    return [[float(x), float(y)] for x, y in world]


def _partial_grid_recovery(
    image: np.ndarray,
    *,
    predicted_center_px: np.ndarray,
    width_px: float,
    height_px: float,
    angle_deg: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Look only for boundary evidence near an otherwise inferred grid cell.

    The grid is the geometry prior.  This deliberately never changes its size
    or angle, and requires support on both local axes before an image-derived
    center is used.  Long expected-edge samples make internal text and small
    texture insufficient evidence.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = cv2.GaussianBlur(gray, (0, 0), 1.0)
    radians = math.radians(angle_deg)
    u = np.array([math.cos(radians), math.sin(radians)])
    v = np.array([-u[1], u[0]])
    probe_px = max(1.5, min(4.0, min(width_px, height_px) * 0.045))
    sample_count = max(17, min(41, int(max(width_px, height_px) / 7.0)))

    def sample_values(points: np.ndarray) -> np.ndarray:
        return cv2.remap(
            gray,
            points[:, 0].astype(np.float32).reshape(-1, 1),
            points[:, 1].astype(np.float32).reshape(-1, 1),
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        ).reshape(-1)

    def side_measurements(center: np.ndarray) -> dict[str, tuple[float, float]]:
        sides = {
            "left": (-u * width_px / 2.0, v, u, height_px),
            "right": (u * width_px / 2.0, v, -u, height_px),
            "top": (-v * height_px / 2.0, u, v, width_px),
            "bottom": (v * height_px / 2.0, u, -v, width_px),
        }
        result = {}
        # Avoid the rounded corners: a straight side must supply the evidence.
        values = np.linspace(-0.31, 0.31, sample_count)
        for name, (offset, tangent, inward, extent) in sides.items():
            points = center + offset + np.outer(values * extent, tangent)
            inside = sample_values(points + inward * probe_px)
            outside = sample_values(points - inward * probe_px)
            contrast = np.abs(inside - outside)
            support = float(np.mean(contrast >= 20.0))
            result[name] = (support, float(np.median(contrast)))
        return result

    # Keep the correction small enough that interior print/noise cannot move a
    # lattice cell across a meaningful fraction of its known physical size.
    x_offsets = np.linspace(-width_px * 0.12, width_px * 0.12, 9)
    y_offsets = np.linspace(-height_px * 0.18, height_px * 0.18, 9)

    def axis_offset(offsets: np.ndarray, axis: np.ndarray, names: tuple[str, str]) -> float:
        scored = []
        for offset in offsets:
            measures = side_measurements(predicted_center_px + axis * offset)
            score = sum(measures[name][0] * min(1.0, measures[name][1] / 45.0) for name in names)
            scored.append((score, abs(float(offset)), float(offset)))
        return max(scored, key=lambda value: (value[0], -value[1]))[2]

    shift_u = axis_offset(x_offsets, u, ("left", "right"))
    shift_v = axis_offset(y_offsets, v, ("top", "bottom"))
    recovered_center = predicted_center_px + u * shift_u + v * shift_v
    measurements = side_measurements(recovered_center)
    supported = [
        name for name, (fraction, contrast) in measurements.items()
        if fraction >= 0.42 and contrast >= 20.0
    ]
    has_horizontal = bool({"left", "right"} & set(supported))
    has_vertical = bool({"top", "bottom"} & set(supported))
    evidence_supported = len(supported) >= 2 and has_horizontal and has_vertical
    if not evidence_supported:
        # Legacy blind gap inference remains available, but no image evidence
        # is claimed and it cannot nudge the predicted center.
        recovered_center = predicted_center_px.copy()
        shift_u = shift_v = 0.0
        measurements = side_measurements(recovered_center)
        supported = []
    evidence_score = float(np.mean([
        fraction * min(1.0, contrast / 45.0)
        for fraction, contrast in measurements.values()
    ]))
    return recovered_center, {
        "predicted_center_px": [float(value) for value in predicted_center_px],
        "recovered_center_px": [float(value) for value in recovered_center],
        "edge_support": {
            name: {"fraction": round(fraction, 4), "contrast": round(contrast, 3)}
            for name, (fraction, contrast) in measurements.items()
        },
        "supported_sides": supported,
        "supported_side_count": len(supported),
        "evidence_score": round(evidence_score, 4),
        "evidence_supported": evidence_supported,
        "recovery_shift_px": [float(shift_u * u[0] + shift_v * v[0]), float(shift_u * u[1] + shift_v * v[1])],
    }


def _infer_grid(
    candidates: list[dict[str, Any]],
    options: TraceOptions,
    work_area: WorkArea,
    output_work_area: WorkArea,
    pixels_per_mm: float,
    image: np.ndarray,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
]:
    if not options.regular_grid or len(candidates) < 4:
        return candidates, [], None

    def angle_distance(first: float, second: float) -> float:
        difference = abs(float(first) - float(second)) % 180.0
        return min(difference, 180.0 - difference)

    # Select the largest mutually similar shape family instead of taking a
    # global median that can be pulled toward sheet edges, shadows, or merged
    # components. A damaged member can still be recognized as a cell later,
    # but unrelated geometry must not define the grid's canonical shape.
    shape_families: list[list[dict[str, Any]]] = []
    for anchor in candidates:
        family = [
            item
            for item in candidates
            if 0.68 <= float(item["width_px"]) / float(anchor["width_px"]) <= 1.47
            and 0.68
            <= float(item["height_px"]) / float(anchor["height_px"])
            <= 1.47
            and angle_distance(
                float(item["angle_image_deg"]),
                float(anchor["angle_image_deg"]),
            )
            <= 12.0
        ]
        shape_families.append(family)
    filtered = max(
        shape_families,
        key=lambda family: (
            len(family),
            sum(float(item["score"]) for item in family),
        ),
    )
    if len(filtered) < 4:
        return candidates, [], None

    widths = np.asarray([item["width_px"] for item in filtered])
    heights = np.asarray([item["height_px"] for item in filtered])
    median_width = float(np.median(widths))
    median_height = float(np.median(heights))
    filtered = [
        item
        for item in filtered
        if 0.72 <= float(item["width_px"]) / median_width <= 1.38
        and 0.72 <= float(item["height_px"]) / median_height <= 1.38
    ]
    if len(filtered) < 4:
        return candidates, [], None
    median_width = float(np.median([item["width_px"] for item in filtered]))
    median_height = float(np.median([item["height_px"] for item in filtered]))
    median_radius = float(np.median([item["radius_px"] for item in filtered]))
    common_angle = _mean_angle([item["angle_image_deg"] for item in filtered])
    angle = math.radians(common_angle)
    u = np.array([math.cos(angle), math.sin(angle)])
    v = np.array([-u[1], u[0]])
    centers = np.asarray([item["center_px"] for item in filtered])
    x_values, y_values = centers @ u, centers @ v
    columns = _clusters(x_values, max(4.0, median_width * 0.45))
    rows = _clusters(y_values, max(4.0, median_height * 0.45))
    if len(columns) < 2 or len(rows) < 2 or len(columns) * len(rows) > 100:
        return candidates, [], None

    def regular_axis(
        values: Sequence[float], object_extent: float
    ) -> tuple[list[float], float, float]:
        observed = np.asarray(sorted(float(item) for item in values), dtype=np.float64)
        differences = np.diff(observed)
        if not len(differences) or float(np.min(differences)) <= 1e-6:
            return [], 0.0, 0.0
        seed = float(np.percentile(differences, 25))
        if seed <= object_extent * 0.82:
            return [], 0.0, 0.0
        steps = np.clip(np.rint(differences / seed), 1, 5).astype(int)
        indices = np.concatenate(([0], np.cumsum(steps))).astype(np.float64)
        centered_indices = indices - float(np.mean(indices))
        centered_values = observed - float(np.mean(observed))
        denominator = float(np.dot(centered_indices, centered_indices))
        if denominator <= 1e-9:
            return [], 0.0, 0.0
        spacing = float(np.dot(centered_indices, centered_values) / denominator)
        if spacing <= object_extent * 0.82:
            return [], 0.0, 0.0
        origin = float(np.mean(observed - indices * spacing))
        predicted = origin + indices * spacing
        residual_rms = float(
            np.sqrt(np.mean(np.square(observed - predicted)))
        )
        quality = max(0.0, 1.0 - residual_rms / max(spacing * 0.14, 1.0))
        expanded = [
            float(origin + index * spacing)
            for index in range(int(indices[-1]) + 1)
        ]
        return expanded, quality, spacing

    columns, x_quality, x_spacing = regular_axis(columns, median_width)
    rows, y_quality, y_spacing = regular_axis(rows, median_height)
    if len(columns) < 2 or len(rows) < 2 or len(columns) * len(rows) > 100:
        return candidates, [], None

    cell_members: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}
    for item in filtered:
        point = np.asarray(item["center_px"])
        x_value, y_value = float(point @ u), float(point @ v)
        column = int(np.argmin(np.abs(np.asarray(columns) - x_value)))
        row = int(np.argmin(np.abs(np.asarray(rows) - y_value)))
        distance = math.hypot(
            (x_value - columns[column]) / max(x_spacing, median_width),
            (y_value - rows[row]) / max(y_spacing, median_height),
        )
        if distance > 0.38:
            continue
        cell = (row, column)
        rank = float(item["score"]) - distance * 0.25
        previous = cell_members.get(cell)
        if previous is None or rank > previous[0]:
            cell_members[cell] = (rank, item)

    occupancy = len(cell_members) / (len(columns) * len(rows))
    observed_area_mm2 = sum(
        float(item["area_mm2"]) for _, item in cell_members.values()
    )
    grid_quality = min(x_quality, y_quality) * min(1.0, occupancy / 0.55)
    if occupancy < 0.45 or grid_quality < 0.45:
        return candidates, [], None

    # Rounded, softly focused silhouettes give minAreaRect a short and noisy
    # orientation baseline. Once cells have been assigned, the repeated center
    # lattice is a much stronger rotation measurement: every populated row
    # contributes a vector spanning the columns. Refit the regular axes in that
    # orientation so normalized outlines do not inherit a small contour-angle
    # bias that is visibly amplified across a long label.
    row_members: dict[int, list[tuple[int, np.ndarray]]] = {}
    for (row, column), (_, item) in cell_members.items():
        row_members.setdefault(row, []).append(
            (column, np.asarray(item["center_px"], dtype=np.float64))
        )
    lattice_angles = []
    for members in row_members.values():
        if len(members) < 2:
            continue
        first_column, first_point = min(members, key=lambda value: value[0])
        last_column, last_point = max(members, key=lambda value: value[0])
        if last_column == first_column:
            continue
        vector = last_point - first_point
        lattice_angles.append(math.degrees(math.atan2(float(vector[1]), float(vector[0]))))
    if len(lattice_angles) >= 2:
        common_angle = _mean_angle(lattice_angles)
        angle = math.radians(common_angle)
        u = np.array([math.cos(angle), math.sin(angle)])
        v = np.array([-u[1], u[0]])

        def refit_axis(axis: np.ndarray, cell_axis: int, count: int) -> tuple[list[float], float]:
            indices = []
            values = []
            for cell, (_, item) in cell_members.items():
                indices.append(float(cell[cell_axis]))
                values.append(float(np.asarray(item["center_px"]) @ axis))
            index_array = np.asarray(indices, dtype=np.float64)
            value_array = np.asarray(values, dtype=np.float64)
            centered = index_array - float(np.mean(index_array))
            denominator = float(np.dot(centered, centered))
            if denominator <= 1e-9:
                return [], 0.0
            spacing = float(
                np.dot(centered, value_array - float(np.mean(value_array))) / denominator
            )
            origin = float(np.mean(value_array - index_array * spacing))
            return [origin + index * spacing for index in range(count)], spacing

        refined_columns, refined_x_spacing = refit_axis(u, 1, len(columns))
        refined_rows, refined_y_spacing = refit_axis(v, 0, len(rows))
        if refined_x_spacing > 0.0 and refined_y_spacing > 0.0:
            columns = refined_columns
            rows = refined_rows
            x_spacing = refined_x_spacing
            y_spacing = refined_y_spacing

    normalize = bool(options.normalize_grid and options.output_mode == "rounded")

    # A weak or obscured side can shrink a single rounded-rectangle contour
    # even when the other three sides are clean.  In a repeated grid the fitted
    # lattice and the sibling-cell median provide strong independent evidence
    # for the missing side.  Repair only one-sided outliers: a genuinely
    # smaller centered cell moves both opposite edges, while a shifted cell
    # moves both edges in the same direction.  Neither case is silently
    # normalized by this conservative repair.
    edge_repair_plans: dict[tuple[int, int], dict[str, Any]] = {}
    if (
        options.repair_grid_edges
        and options.output_mode == "rounded"
        and len(cell_members) >= 4
        and grid_quality >= 0.55
    ):
        width_stable = max(1.5, pixels_per_mm * 0.35, median_width * 0.012)
        height_stable = max(1.5, pixels_per_mm * 0.35, median_height * 0.012)
        width_trigger = max(2.5, pixels_per_mm * 0.60, median_width * 0.020)
        height_trigger = max(2.5, pixels_per_mm * 0.60, median_height * 0.020)

        def projected_edges(item: Mapping[str, Any]) -> dict[str, float]:
            item_angle = math.radians(float(item["angle_image_deg"]))
            item_u = np.array([math.cos(item_angle), math.sin(item_angle)])
            item_v = np.array([-item_u[1], item_u[0]])
            center = np.asarray(item["center_px"], dtype=np.float64)
            half_width = float(item["width_px"]) / 2.0
            half_height = float(item["height_px"]) / 2.0
            corners = np.asarray(
                [
                    center + item_u * sx * half_width + item_v * sy * half_height
                    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
                ],
                dtype=np.float64,
            )
            along_u = corners @ u
            along_v = corners @ v
            return {
                "left": float(np.min(along_u)),
                "right": float(np.max(along_u)),
                "top": float(np.min(along_v)),
                "bottom": float(np.max(along_v)),
            }

        for cell, (_, item) in cell_members.items():
            if (
                item.get("shape") != "rounded_rectangle"
                or bool(item.get("touches_image_edge", False))
            ):
                continue
            row, column = cell
            observed = projected_edges(item)
            expected = {
                "left": float(columns[column] - median_width / 2.0),
                "right": float(columns[column] + median_width / 2.0),
                "top": float(rows[row] - median_height / 2.0),
                "bottom": float(rows[row] + median_height / 2.0),
            }
            deviations = {
                edge: observed[edge] - expected[edge]
                for edge in ("left", "right", "top", "bottom")
            }
            repairs: list[str] = []
            for edge, opposite, trigger, stable in (
                ("left", "right", width_trigger, width_stable),
                ("right", "left", width_trigger, width_stable),
                ("top", "bottom", height_trigger, height_stable),
                ("bottom", "top", height_trigger, height_stable),
            ):
                if (
                    abs(deviations[edge]) >= trigger
                    and abs(deviations[opposite]) <= stable
                ):
                    repairs.append(edge)
            # Never replace both opposing sides on one axis. That indicates a
            # legitimately shifted or differently sized cell, not one missing
            # boundary.
            if {"left", "right"}.issubset(repairs):
                repairs = [edge for edge in repairs if edge not in {"left", "right"}]
            if {"top", "bottom"}.issubset(repairs):
                repairs = [edge for edge in repairs if edge not in {"top", "bottom"}]
            if not repairs:
                continue
            repaired = dict(observed)
            for edge in repairs:
                repaired[edge] = expected[edge]
            repaired_width = repaired["right"] - repaired["left"]
            repaired_height = repaired["bottom"] - repaired["top"]
            if repaired_width <= 1.0 or repaired_height <= 1.0:
                continue
            repaired_center = (
                u * ((repaired["left"] + repaired["right"]) / 2.0)
                + v * ((repaired["top"] + repaired["bottom"]) / 2.0)
            )
            edge_repair_plans[cell] = {
                "edges": repairs,
                "center_px": repaired_center,
                "width_px": repaired_width,
                "height_px": repaired_height,
                "observed_edges_px": observed,
                "expected_edges_px": expected,
                "deviations_px": deviations,
            }

    def canonical_candidate(
        item: Mapping[str, Any] | None,
        row: int,
        column: int,
        *,
        inferred: bool,
    ) -> dict[str, Any]:
        lattice_center_px = u * columns[column] + v * rows[row]
        use_lattice_pose = inferred or (normalize and options.snap_grid_cells)
        center_px = (
            lattice_center_px
            if use_lattice_pose or item is None
            else np.asarray(item["center_px"], dtype=np.float64)
        )
        edge_repair = edge_repair_plans.get((row, column))
        repaired_center_axes: list[str] = []
        if item is not None and not use_lattice_pose and normalize:
            # A clipped/obscured edge biases minAreaRect's center toward the
            # surviving side. Expanding the shared size around that biased
            # center shifts the proposed cut off the real object. Borrow the
            # lattice center only on a materially malformed size axis.
            width_delta = abs(float(item["width_px"]) - median_width)
            height_delta = abs(float(item["height_px"]) - median_height)
            width_threshold = max(2.0 * pixels_per_mm, median_width * 0.03)
            height_threshold = max(2.0 * pixels_per_mm, median_height * 0.08)
            if width_delta > width_threshold:
                center_px = center_px + u * float((lattice_center_px - center_px) @ u)
                repaired_center_axes.append("width")
            if height_delta > height_threshold:
                center_px = center_px + v * float((lattice_center_px - center_px) @ v)
                repaired_center_axes.append("height")
        cell_angle = (
            common_angle
            if use_lattice_pose or item is None
            else float(item["angle_image_deg"])
        )
        if (
            item is not None
            and not use_lattice_pose
            and options.normalize_anchor == "top"
        ):
            angle = math.radians(cell_angle)
            local_y = np.array([-math.sin(angle), math.cos(angle)])
            # Keep the independently detected top fixed while replacing a
            # bottom-damage-biased observed height with the shared height.
            center_px = center_px + local_y * (
                median_height - float(item["height_px"])
            ) / 2.0
        if item is not None and edge_repair is not None and not normalize:
            center_px = np.asarray(edge_repair["center_px"], dtype=np.float64)
            cell_angle = common_angle
            rectangle_width = float(edge_repair["width_px"])
            rectangle_height = float(edge_repair["height_px"])
            rectangle_radius = min(
                median_radius,
                rectangle_width / 2.0,
                rectangle_height / 2.0,
            )
        elif item is not None and not normalize:
            rectangle_width = float(item["width_px"])
            rectangle_height = float(item["height_px"])
            rectangle_radius = float(item["radius_px"])
        else:
            rectangle_width = median_width
            rectangle_height = median_height
            rectangle_radius = median_radius
        rectangle = {
            "center": center_px,
            "width": rectangle_width,
            "height": rectangle_height,
            "angle_image_deg": cell_angle,
            "radius_px": rectangle_radius,
        }
        geometry = _machine_geometry(
            rectangle,
            work_area,
            pixels_per_mm,
            options.border_offset_mm,
            edge_offsets_mm=_custom_edge_offsets(options),
        )
        rounded_contour = _rounded_polyline(
            geometry["center_mm"],
            geometry["width_mm"],
            geometry["height_mm"],
            geometry["rotation_deg"],
            geometry["corner_radius_mm"],
        )
        camera_work_area_overruns_mm = _work_area_overruns_mm(
            rounded_contour,
            work_area,
        )
        work_area_overruns_mm = _work_area_overruns_mm(
            rounded_contour,
            output_work_area,
        )
        camera_work_area_overrun_mm = max(
            camera_work_area_overruns_mm.values()
        )
        work_area_overrun_mm = max(work_area_overruns_mm.values())
        if inferred:
            center_px, recovery_diagnostics = _partial_grid_recovery(
                image,
                predicted_center_px=lattice_center_px,
                width_px=median_width,
                height_px=median_height,
                angle_deg=common_angle,
            )
            rectangle["center"] = center_px
            geometry = _machine_geometry(
                rectangle,
                work_area,
                pixels_per_mm,
                options.border_offset_mm,
                edge_offsets_mm=_custom_edge_offsets(options),
            )
            rounded_contour = _rounded_polyline(
                geometry["center_mm"],
                geometry["width_mm"],
                geometry["height_mm"],
                geometry["rotation_deg"],
                geometry["corner_radius_mm"],
            )
            camera_work_area_overruns_mm = _work_area_overruns_mm(
                rounded_contour,
                work_area,
            )
            work_area_overruns_mm = _work_area_overruns_mm(
                rounded_contour,
                output_work_area,
            )
            camera_work_area_overrun_mm = max(
                camera_work_area_overruns_mm.values()
            )
            work_area_overrun_mm = max(work_area_overruns_mm.values())

            predicted_geometry = _machine_geometry(
                {**rectangle, "center": lattice_center_px},
                work_area,
                pixels_per_mm,
                0.0,
            )
            recovery_diagnostics["predicted_center_mm"] = list(
                predicted_geometry["center_mm"]
            )
            recovery_diagnostics["recovered_center_mm"] = list(
                geometry["center_mm"]
            )
            recovery_diagnostics["recovery_shift_mm"] = [
                geometry["center_mm"][0]
                - recovery_diagnostics["predicted_center_mm"][0],
                geometry["center_mm"][1]
                - recovery_diagnostics["predicted_center_mm"][1],
            ]

            median_score = float(
                np.median(
                    [value[1]["score"] for value in cell_members.values()]
                )
            )
            return {
                "center_px": center_px,
                "width_px": median_width,
                "height_px": median_height,
                "angle_image_deg": common_angle,
                "radius_px": median_radius,
                "area_mm2": median_width * median_height / pixels_per_mm**2,
                "score": median_score * grid_quality * 0.72,
                "confidence": min(0.68, grid_quality * 0.66),
                "rectangularity": 1.0,
                "solidity": 1.0,
                "fit_iou": 1.0,
                "coverage": 0.0,
                "compactness": 1.0,
                "shape": "rounded_rectangle",
                "touches_image_edge": False,
                "image_edge_sides": [],
                "within_camera_work_area": (
                    camera_work_area_overrun_mm <= 1e-9
                ),
                "camera_work_area_overrun_mm": camera_work_area_overrun_mm,
                "camera_work_area_overruns_mm": camera_work_area_overruns_mm,
                "within_work_area": work_area_overrun_mm <= 1e-9,
                "work_area_overrun_mm": work_area_overrun_mm,
                "work_area_overruns_mm": work_area_overruns_mm,
                "contour_mm": rounded_contour,
                "vector_contour_mm": rounded_contour,
                "grid_row": row,
                "grid_column": column,
                "grid_normalized": True,
                **recovery_diagnostics,
                **geometry,
            }

        assert item is not None
        output = dict(item)
        output.update(
            {
                "grid_row": row,
                "grid_column": column,
                "grid_normalized": normalize,
                "observed_center_mm": list(item["center_mm"]),
                "observed_width_mm": float(item["width_mm"]),
                "observed_height_mm": float(item["height_mm"]),
                "observed_rotation_deg": float(item["rotation_deg"]),
                "observed_corner_radius_mm": float(item["corner_radius_mm"]),
                "repaired_center_axes": repaired_center_axes,
                "grid_edge_repairs": (
                    list(edge_repair["edges"])
                    if edge_repair is not None
                    else []
                ),
                "grid_edge_observed_px": (
                    dict(edge_repair["observed_edges_px"])
                    if edge_repair is not None
                    else None
                ),
                "grid_edge_expected_px": (
                    dict(edge_repair["expected_edges_px"])
                    if edge_repair is not None
                    else None
                ),
                "grid_edge_deviations_px": (
                    dict(edge_repair["deviations_px"])
                    if edge_repair is not None
                    else None
                ),
                "observed_within_work_area": bool(
                    item.get("within_work_area", True)
                ),
                "observed_work_area_overrun_mm": float(
                    item.get("work_area_overrun_mm", 0.0)
                ),
                "observed_work_area_overruns_mm": dict(
                    item.get("work_area_overruns_mm", {})
                ),
            }
        )
        if normalize:
            output.update(
                {
                    "center_px": center_px,
                    "width_px": median_width,
                    "height_px": median_height,
                    "angle_image_deg": cell_angle,
                    "radius_px": median_radius,
                    "area_mm2": median_width * median_height / pixels_per_mm**2,
                    "shape": "rounded_rectangle",
                    "within_camera_work_area": (
                        camera_work_area_overrun_mm <= 1e-9
                    ),
                    "camera_work_area_overrun_mm": camera_work_area_overrun_mm,
                    "camera_work_area_overruns_mm": (
                        camera_work_area_overruns_mm
                    ),
                    "within_work_area": work_area_overrun_mm <= 1e-9,
                    "work_area_overrun_mm": work_area_overrun_mm,
                    "work_area_overruns_mm": work_area_overruns_mm,
                    "vector_contour_mm": rounded_contour,
                    **geometry,
                }
            )
        elif edge_repair is not None:
            output.update(
                {
                    "center_px": center_px,
                    "width_px": rectangle_width,
                    "height_px": rectangle_height,
                    "angle_image_deg": cell_angle,
                    "radius_px": rectangle_radius,
                    "area_mm2": (
                        rectangle_width * rectangle_height / pixels_per_mm**2
                    ),
                    "shape": "rounded_rectangle",
                    "within_camera_work_area": (
                        camera_work_area_overrun_mm <= 1e-9
                    ),
                    "camera_work_area_overrun_mm": camera_work_area_overrun_mm,
                    "camera_work_area_overruns_mm": camera_work_area_overruns_mm,
                    "within_work_area": work_area_overrun_mm <= 1e-9,
                    "work_area_overrun_mm": work_area_overrun_mm,
                    "work_area_overruns_mm": work_area_overruns_mm,
                    "vector_contour_mm": rounded_contour,
                    **geometry,
                }
            )
        return output

    direct = []
    for (row, column), (_, item) in sorted(cell_members.items()):
        candidate = canonical_candidate(item, row, column, inferred=False)
        direct.append(candidate)

    inferred = []
    if options.infer_missing:
        for row in range(len(rows)):
            for column in range(len(columns)):
                if (row, column) in cell_members:
                    continue
                candidate = canonical_candidate(None, row, column, inferred=True)
                inferred.append(candidate)

    canonical_geometry = _machine_geometry(
        {
            "center": u * columns[0] + v * rows[0],
            "width": median_width,
            "height": median_height,
            "angle_image_deg": common_angle,
            "radius_px": median_radius,
        },
        work_area,
        pixels_per_mm,
        options.border_offset_mm,
        edge_offsets_mm=_custom_edge_offsets(options),
    )
    output_cells = [*direct, *inferred]
    outside_cells = sum(
        not bool(item.get("within_work_area", True)) for item in output_cells
    )
    return direct, inferred, {
        "rows": len(rows),
        "columns": len(columns),
        "occupancy": occupancy,
        "observed_cells": len(cell_members),
        "observed_area_mm2": observed_area_mm2,
        "quality": grid_quality,
        "rotation_deg": -common_angle,
        "direct_cells": len(direct),
        "missing_cells": len(inferred),
        "missing_cells_total": len(rows) * len(columns) - len(cell_members),
        "inferred_cells": len(inferred),
        "rejected_candidates": len(candidates) - len(cell_members),
        "outside_cells": outside_cells,
        "max_work_area_overrun_mm": max(
            (
                float(item.get("work_area_overrun_mm", 0.0))
                for item in output_cells
            ),
            default=0.0,
        ),
        "normalized": normalize,
        "cells_snapped": normalize and options.snap_grid_cells,
        "normalization_anchor": options.normalize_anchor,
        "edge_repair_enabled": bool(options.repair_grid_edges),
        "repaired_edges": sum(
            len(plan["edges"]) for plan in edge_repair_plans.values()
        ),
        "repaired_cells": len(edge_repair_plans),
        "cell_width_mm": float(canonical_geometry["width_mm"]),
        "cell_height_mm": float(canonical_geometry["height_mm"]),
        "observed_cell_width_mm": median_width / pixels_per_mm,
        "observed_cell_height_mm": median_height / pixels_per_mm,
        "cell_corner_radius_mm": float(canonical_geometry["corner_radius_mm"]),
        "column_pitch_mm": x_spacing / pixels_per_mm,
        "row_pitch_mm": y_spacing / pixels_per_mm,
        "output_work_area": {
            "x_min": float(output_work_area.x_min),
            "x_max": float(output_work_area.x_max),
            "y_min": float(output_work_area.y_min),
            "y_max": float(output_work_area.y_max),
        },
        "camera_work_area": {
            "x_min": float(work_area.x_min),
            "x_max": float(work_area.x_max),
            "y_min": float(work_area.y_min),
            "y_max": float(work_area.y_max),
        },
        "edge_cropped_direct_cells": sum(
            bool(item.get("touches_image_edge", False)) for item in direct
        ),
    }


def _to_detection(
    item: Mapping[str, Any], source: str, options: TraceOptions
) -> TraceDetection:
    confidence = float(item["confidence"])
    within_work_area = bool(item.get("within_work_area", True))
    touches_image_edge = bool(item.get("touches_image_edge", False))
    damage_suspected = bool(item.get("damage_suspected", False))
    likely_open_cell = bool(item.get("likely_open_cell", False))
    return TraceDetection(
        id=_new_id(), index=0, source=source,
        confidence=confidence, score=float(item["score"]),
        shape=str(item.get("shape", "contour")),
        center_mm=tuple(item["center_mm"]),
        width_mm=float(item["width_mm"]), height_mm=float(item["height_mm"]),
        rotation_deg=float(item["rotation_deg"]),
        corner_radius_mm=float(item["corner_radius_mm"]),
        area_mm2=float(item["area_mm2"]),
        contour_mm=[list(point) for point in item["contour_mm"]],
        vector_contour_mm=[
            list(point)
            for point in item.get("vector_contour_mm", item["contour_mm"])
        ],
        vector_contours_mm=[
            [list(point) for point in contour]
            for contour in item.get(
                "vector_contours_mm",
                [item.get("vector_contour_mm", item["contour_mm"])],
            )
        ],
        box_mm=[list(point) for point in item["box_mm"]],
        selected_default=(
            source == "direct"
            and confidence >= options.confidence_threshold
            and within_work_area
            and not touches_image_edge
            and not damage_suspected
            and not likely_open_cell
        ),
        diagnostics={
            "rectangularity": float(item["rectangularity"]),
            "solidity": float(item["solidity"]),
            "fit_iou": float(item["fit_iou"]),
            "color_coverage": float(item["coverage"]),
            "compactness": float(item.get("compactness", 0.0)),
            "straight_edge_rotation": item.get("straight_edge_rotation"),
            "straight_edge_center": item.get("straight_edge_center"),
            "damage_suspected": damage_suspected,
            "damage_reasons": list(item.get("damage_reasons", [])),
            "grid_rotation_error_deg": float(item.get("grid_rotation_error_deg", 0.0)),
            "grid_width_error_ratio": float(item.get("grid_width_error_ratio", 0.0)),
            "grid_height_error_ratio": float(item.get("grid_height_error_ratio", 0.0)),
            "likely_open_cell": likely_open_cell,
            "open_cell_evidence": str(
                item.get("open_cell_evidence", "none")
            ),
            "background_match_available": bool(
                item.get("background_match_available", False)
            ),
            "background_lab_median_delta": float(
                item.get("background_lab_median_delta", 0.0)
            ),
            "background_match_fraction": float(
                item.get("background_match_fraction", 0.0)
            ),
            "cell_texture_score": float(item.get("cell_texture_score", 0.0)),
            "cell_texture_baseline": float(item.get("cell_texture_baseline", 0.0)),
            "cell_texture_threshold": float(item.get("cell_texture_threshold", 0.0)),
            "cell_intensity_stddev": float(item.get("cell_intensity_stddev", 0.0)),
            "cell_edge_density": float(item.get("cell_edge_density", 0.0)),
            **(
                {
                    "hole_ratio": float(item["hole_ratio"]),
                    "outer_circle_residual": float(item["outer_circle_residual"]),
                    "inner_circle_residual": float(item["inner_circle_residual"]),
                    "center_offset_mm": float(item["center_offset_mm"]),
                }
                if item.get("shape") == "washer"
                else {}
            ),
            "mask_source": str(item.get("mask_source", "unknown")),
            "touches_image_edge": touches_image_edge,
            "image_edge_sides": list(item.get("image_edge_sides", [])),
            "within_camera_work_area": bool(
                item.get("within_camera_work_area", True)
            ),
            "camera_work_area_overrun_mm": float(
                item.get("camera_work_area_overrun_mm", 0.0)
            ),
            "within_work_area": within_work_area,
            "work_area_overrun_mm": float(
                item.get("work_area_overrun_mm", 0.0)
            ),
            "work_area_overruns_mm": dict(
                item.get("work_area_overruns_mm", {})
            ),
            **(
                {
                    "grid_normalized": bool(item.get("grid_normalized", False)),
                    "grid_row": int(item["grid_row"]),
                    "grid_column": int(item["grid_column"]),
                }
                if "grid_row" in item and "grid_column" in item
                else {}
            ),
            **(
                {
                    "observed_center_mm": list(item["observed_center_mm"]),
                    "observed_width_mm": float(item["observed_width_mm"]),
                    "observed_height_mm": float(item["observed_height_mm"]),
                    "observed_rotation_deg": float(item["observed_rotation_deg"]),
                    "observed_corner_radius_mm": float(
                        item["observed_corner_radius_mm"]
                    ),
                    "repaired_center_axes": list(
                        item.get("repaired_center_axes", [])
                    ),
                    "grid_edge_repairs": list(
                        item.get("grid_edge_repairs", [])
                    ),
                    "grid_edge_observed_px": item.get(
                        "grid_edge_observed_px"
                    ),
                    "grid_edge_expected_px": item.get(
                        "grid_edge_expected_px"
                    ),
                    "grid_edge_deviations_px": item.get(
                        "grid_edge_deviations_px"
                    ),
                    "observed_within_work_area": bool(
                        item.get("observed_within_work_area", True)
                    ),
                    "observed_work_area_overrun_mm": float(
                        item.get("observed_work_area_overrun_mm", 0.0)
                    ),
                }
                if "observed_center_mm" in item
                else {}
            ),
            **(
                {
                    "predicted_center_px": list(item["predicted_center_px"]),
                    "recovered_center_px": list(item["recovered_center_px"]),
                    "predicted_center_mm": list(item["predicted_center_mm"]),
                    "recovered_center_mm": list(item["recovered_center_mm"]),
                    "edge_support": dict(item["edge_support"]),
                    "supported_sides": list(item["supported_sides"]),
                    "supported_side_count": int(item["supported_side_count"]),
                    "evidence_score": float(item["evidence_score"]),
                    "evidence_supported": bool(item["evidence_supported"]),
                    "recovery_shift_px": list(item["recovery_shift_px"]),
                    "recovery_shift_mm": list(item["recovery_shift_mm"]),
                }
                if "evidence_supported" in item
                else {}
            ),
        },
    )


def _circle_fit(contour: np.ndarray) -> dict[str, float] | None:
    points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    if len(points) < 12:
        return None
    center_x, center_y = np.mean(points, axis=0)
    radii = np.linalg.norm(points - (center_x, center_y), axis=1)
    radius = float(np.mean(radii))
    if radius <= 2.0:
        return None
    residual = float(np.sqrt(np.mean((radii - radius) ** 2)) / radius)
    perimeter = float(cv2.arcLength(contour, True))
    area = abs(float(cv2.contourArea(contour)))
    circularity = 0.0 if perimeter <= 0 else min(1.0, 4.0 * math.pi * area / perimeter**2)
    return {
        "center_x": float(center_x),
        "center_y": float(center_y),
        "radius": radius,
        "residual": residual,
        "circularity": circularity,
    }


def _circle_machine_contour(
    center_px: tuple[float, float],
    radius_px: float,
    work_area: WorkArea,
    pixels_per_mm: float,
    *,
    reverse: bool = False,
) -> list[list[float]]:
    steps = range(72, -1, -1) if reverse else range(73)
    pixels = np.asarray(
        [
            (
                center_px[0] + radius_px * math.cos(2.0 * math.pi * step / 72),
                center_px[1] + radius_px * math.sin(2.0 * math.pi * step / 72),
            )
            for step in steps
        ],
        dtype=np.float64,
    )
    return [
        [float(x), float(y)]
        for x, y in _pixel_to_machine(pixels, work_area, pixels_per_mm)
    ]


def _washer_candidates(
    mask: np.ndarray,
    options: TraceOptions,
    work_area: WorkArea,
    output_work_area: WorkArea,
    pixels_per_mm: float,
) -> list[dict[str, Any]]:
    """Recognize only strong circular parent/child contour pairs as washers."""
    contours, hierarchy = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    if hierarchy is None:
        return []
    parents = hierarchy[0, :, 3]
    output: list[dict[str, Any]] = []
    for inner_index, outer_index in enumerate(parents):
        if outer_index < 0:
            continue
        outer = contours[int(outer_index)]
        inner = contours[inner_index]
        outer_fit, inner_fit = _circle_fit(outer), _circle_fit(inner)
        if outer_fit is None or inner_fit is None:
            continue
        outer_radius, inner_radius = outer_fit["radius"], inner_fit["radius"]
        ratio = inner_radius / outer_radius
        center_offset = math.hypot(
            outer_fit["center_x"] - inner_fit["center_x"],
            outer_fit["center_y"] - inner_fit["center_y"],
        )
        residual_limit = max(0.035, 0.8 / outer_radius)
        inner_residual_limit = max(0.045, 0.8 / inner_radius)
        if not (
            0.10 <= ratio <= 0.85
            and center_offset <= max(1.0, outer_radius * 0.035)
            and outer_fit["residual"] <= residual_limit
            and inner_fit["residual"] <= inner_residual_limit
            and outer_fit["circularity"] >= 0.82
            and inner_fit["circularity"] >= 0.78
        ):
            continue
        candidate = _candidate(
            outer, mask, options, work_area, output_work_area, pixels_per_mm
        )
        if candidate is None:
            continue
        offset_px = options.border_offset_mm * pixels_per_mm
        corrected_outer = outer_radius + offset_px
        corrected_inner = inner_radius - offset_px
        if corrected_inner <= 1.0 or corrected_inner >= corrected_outer:
            continue
        center = (
            (outer_fit["center_x"] + inner_fit["center_x"]) / 2.0,
            (outer_fit["center_y"] + inner_fit["center_y"]) / 2.0,
        )
        outer_mm = _circle_machine_contour(
            center, corrected_outer, work_area, pixels_per_mm
        )
        inner_mm = _circle_machine_contour(
            center, corrected_inner, work_area, pixels_per_mm, reverse=True
        )
        overruns = _work_area_overruns_mm([*outer_mm, *inner_mm], output_work_area)
        camera_overruns = _work_area_overruns_mm([*outer_mm, *inner_mm], work_area)
        fit_quality = math.exp(
            -8.0 * (outer_fit["residual"] + inner_fit["residual"])
            -4.0 * center_offset / outer_radius
        )
        candidate.update(
            {
                "shape": "washer",
                "center_mm": tuple(
                    _pixel_to_machine(
                        np.asarray(center).reshape(1, 2), work_area, pixels_per_mm
                    )[0]
                ),
                "width_mm": 2.0 * corrected_outer / pixels_per_mm,
                "height_mm": 2.0 * corrected_outer / pixels_per_mm,
                "rotation_deg": 0.0,
                "corner_radius_mm": 0.0,
                "vector_contour_mm": outer_mm,
                "vector_contours_mm": [outer_mm, inner_mm],
                "contour_mm": outer_mm,
                "hole_ratio": corrected_inner / corrected_outer,
                "outer_circle_residual": outer_fit["residual"],
                "inner_circle_residual": inner_fit["residual"],
                "center_offset_mm": center_offset / pixels_per_mm,
                "score": max(candidate["score"], fit_quality),
                "confidence": max(candidate["confidence"], fit_quality),
                "within_camera_work_area": max(camera_overruns.values()) <= 1e-9,
                "camera_work_area_overrun_mm": max(camera_overruns.values()),
                "camera_work_area_overruns_mm": camera_overruns,
                "within_work_area": max(overruns.values()) <= 1e-9,
                "work_area_overrun_mm": max(overruns.values()),
                "work_area_overruns_mm": overruns,
            }
        )
        output.append(candidate)
    return output


def _axis_rotation_error(first_deg: float, second_deg: float) -> float:
    return abs((float(first_deg) - float(second_deg) + 90.0) % 180.0 - 90.0)


def _grid_cell_review_evidence(
    image: np.ndarray,
    candidates: list[dict[str, Any]],
    grid: Mapping[str, Any] | None,
    background_image: np.ndarray | None = None,
) -> None:
    """Annotate suspicious geometry and likely exposed-bed grid cells.

    This is a review gate, not a material classifier. It uses a shrunken cell
    interior so printed/damaged borders do not dominate the texture evidence.
    """
    if not grid or len(candidates) < 3:
        return
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    background_lab: np.ndarray | None = None
    current_lab: np.ndarray | None = None
    if background_image is not None:
        _require_bgr_image(background_image, "Honeycomb background image")
        if background_image.shape != image.shape:
            raise ValueError(
                "Honeycomb background image must match the rectified camera image"
            )
        current_lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
        background_lab = cv2.cvtColor(
            background_image,
            cv2.COLOR_BGR2LAB,
        ).astype(np.float32)
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient_image = cv2.magnitude(gradient_x, gradient_y)
    common_image_angle = -float(grid.get("rotation_deg", 0.0))
    canonical_width = float(
        grid.get("observed_cell_width_mm", grid.get("cell_width_mm", 0.0))
    )
    canonical_height = float(
        grid.get("observed_cell_height_mm", grid.get("cell_height_mm", 0.0))
    )
    texture_rows: list[
        tuple[dict[str, Any], float, float, float, bool]
    ] = []
    for item in candidates:
        if item.get("grid_row") is None or item.get("grid_column") is None:
            continue
        observed_width = float(item.get("observed_width_mm", item.get("width_mm", 0.0)))
        observed_height = float(item.get("observed_height_mm", item.get("height_mm", 0.0)))
        observed_rotation = float(
            item.get("observed_rotation_deg", item.get("rotation_deg", 0.0))
        )
        rotation_error = _axis_rotation_error(observed_rotation, -common_image_angle)
        width_error = abs(observed_width - canonical_width) / max(canonical_width, 1e-9)
        height_error = abs(observed_height - canonical_height) / max(canonical_height, 1e-9)
        reasons = []
        if rotation_error > 1.75:
            reasons.append(f"rotation differs by {rotation_error:.2f}°")
        if width_error > 0.045:
            reasons.append(f"width differs by {width_error * 100:.1f}%")
        if height_error > 0.09:
            reasons.append(f"height differs by {height_error * 100:.1f}%")
        if item.get("repaired_center_axes"):
            reasons.append("a damaged edge required center repair")
        repaired_edges = list(item.get("grid_edge_repairs", []))
        if repaired_edges:
            reasons.append(
                "weak "
                + "/".join(str(edge) for edge in repaired_edges)
                + " edge repaired from repeated-cell consensus"
            )
        item["damage_suspected"] = bool(reasons)
        item["damage_reasons"] = reasons
        item["grid_rotation_error_deg"] = rotation_error
        item["grid_width_error_ratio"] = width_error
        item["grid_height_error_ratio"] = height_error

        center = tuple(float(value) for value in item["center_px"])
        width_px = max(3.0, float(item["width_px"]) * 0.68)
        height_px = max(3.0, float(item["height_px"]) * 0.62)
        box = cv2.boxPoints((center, (width_px, height_px), float(item["angle_image_deg"])))
        mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.fillConvexPoly(mask, np.round(box).astype(np.int32), 255)
        pixels = gray[mask > 0]
        if len(pixels) < 25:
            continue
        gradient = gradient_image[mask > 0]
        deviation = float(np.std(pixels))
        edge_density = float(np.mean(gradient > 80.0))
        texture_score = deviation + 35.0 * edge_density
        background_match = False
        if current_lab is not None and background_lab is not None:
            difference = np.linalg.norm(
                current_lab[mask > 0] - background_lab[mask > 0],
                axis=1,
            )
            median_delta = float(np.median(difference))
            match_fraction = float(np.mean(difference <= 15.0))
            # The accepted empty-honeycomb photograph is bound to this exact
            # bed map and support pose. A strong per-pixel match therefore
            # means this grid position is already open, even when its exposed
            # honeycomb is darker or less textured than the printed labels.
            background_match = bool(
                median_delta <= 24.0 and match_fraction >= 0.55
            )
            item["background_match_available"] = True
            item["background_lab_median_delta"] = median_delta
            item["background_match_fraction"] = match_fraction
        else:
            item["background_match_available"] = False
        texture_rows.append(
            (item, texture_score, deviation, edge_density, background_match)
        )

    if len(texture_rows) < 3:
        return
    scores = np.asarray([row[1] for row in texture_rows], dtype=np.float64)
    lower = scores[scores <= np.percentile(scores, 50.0)]
    baseline = float(np.median(lower)) if len(lower) else float(np.median(scores))
    spread = float(np.median(np.abs(lower - baseline))) if len(lower) else 0.0
    threshold = max(baseline + max(12.0, 5.0 * spread), baseline * 1.65)
    for item, score, deviation, edge_density, background_match in texture_rows:
        likely_open = bool(
            background_match
            or (score > threshold and deviation > 18.0 and edge_density > 0.08)
        )
        item["likely_open_cell"] = likely_open
        item["open_cell_evidence"] = (
            "accepted_honeycomb_background"
            if background_match
            else "texture"
            if likely_open
            else "none"
        )
        item["cell_texture_score"] = score
        item["cell_texture_baseline"] = baseline
        item["cell_texture_threshold"] = threshold
        item["cell_intensity_stddev"] = deviation
        item["cell_edge_density"] = edge_density


def _localize_camera_intensity_edges(
    image: np.ndarray,
    points_px: np.ndarray,
) -> tuple[np.ndarray, float, int]:
    """Conservatively center eligible non-corner samples on camera intensity edges."""

    points = np.asarray(points_px, dtype=np.float64).reshape(-1, 2)
    if len(points) < 8:
        return points.copy(), 0.0, 0
    incoming = points - np.roll(points, 1, axis=0)
    outgoing = np.roll(points, -1, axis=0) - points
    incoming_norm = np.linalg.norm(incoming, axis=1)
    outgoing_norm = np.linalg.norm(outgoing, axis=1)
    cosine = np.divide(
        np.sum(incoming * outgoing, axis=1),
        incoming_norm * outgoing_norm,
        out=np.ones(len(points), dtype=np.float64),
        where=(incoming_norm * outgoing_norm) > 1e-12,
    )
    turns = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
    protected = turns >= 20.0
    protected |= np.roll(protected, 1) | np.roll(protected, -1)

    tangents = np.roll(points, -1, axis=0) - np.roll(points, 1, axis=0)
    lengths = np.linalg.norm(tangents, axis=1)
    valid = lengths > 1e-9
    normals = np.zeros_like(tangents)
    normals[valid, 0] = -tangents[valid, 1] / lengths[valid]
    normals[valid, 1] = tangents[valid, 0] / lengths[valid]
    offsets = np.arange(-1.25, 1.2501, 0.125, dtype=np.float32)
    sample_x = (
        points[:, 0, None] + normals[:, 0, None] * offsets[None, :]
    ).astype(np.float32)
    sample_y = (
        points[:, 1, None] + normals[:, 1, None] * offsets[None, :]
    ).astype(np.float32)
    lightness = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)[:, :, 0]
    profiles = cv2.remap(
        lightness,
        sample_x,
        sample_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    ).astype(np.float64)
    changes = np.abs(np.diff(profiles, axis=1))
    strongest = np.argmax(changes, axis=1)
    strongest_value = changes[np.arange(len(points)), strongest]
    endpoint_contrast = np.abs(profiles[:, -1] - profiles[:, 0])
    second = changes.copy()
    for row, index in enumerate(strongest):
        second[row, max(0, index - 2) : min(changes.shape[1], index + 3)] = 0.0
    second_value = np.max(second, axis=1)
    displacement = (offsets[strongest] + offsets[strongest + 1]) * 0.5
    accepted = (
        valid
        & ~protected
        & (endpoint_contrast >= 16.0)
        & (strongest_value >= 4.0)
        & (second_value <= strongest_value * 0.75)
        & (np.abs(displacement) <= 0.60)
    )
    refined = points.copy()
    refined[accepted] += normals[accepted] * displacement[accepted, None]
    maximum = float(np.max(np.abs(displacement[accepted]))) if np.any(accepted) else 0.0
    return refined, maximum, int(np.count_nonzero(accepted))


def _trace_candidate_tree(
    candidate: Mapping[str, Any],
    contours: tuple[np.ndarray, ...],
    trees: tuple[ForegroundContourTree, ...],
) -> ForegroundContourTree:
    source = np.asarray(candidate.get("source_contour_px"), dtype=np.float64).reshape(-1, 2)
    if len(source) < 3:
        raise ValueError("A direct Trace candidate has no source contour")
    bounds = tuple(int(value) for value in cv2.boundingRect(source.astype(np.float32)))
    matches = [tree for tree in trees if tree.bounds_px == bounds]
    if not matches:
        raise ValueError("A direct Trace candidate no longer matches its source contour tree")
    if len(matches) == 1:
        return matches[0]
    source_area = abs(float(cv2.contourArea(source.astype(np.float32))))
    return min(
        matches,
        key=lambda tree: abs(
            abs(float(cv2.contourArea(contours[tree.root_index]))) - source_area
        ),
    )


def _fit_trace_candidate_native(
    image: np.ndarray,
    detection: TraceDetection,
    options: TraceOptions,
    work_area: WorkArea,
    output_work_area: WorkArea,
    pixels_per_mm: float,
    contours: tuple[np.ndarray, ...],
    tree: ForegroundContourTree,
) -> None:
    """Fit one already-detected contour tree through the shared native fitter."""

    _check_trace_cancelled()
    pitch = 1.0 / pixels_per_mm
    offset_px = options.border_offset_mm * pixels_per_mm
    source_contours_px: list[np.ndarray] = []
    for depth, contour_index in zip(tree.depths, tree.contour_indices, strict=True):
        _check_trace_cancelled()
        points = np.asarray(contours[contour_index], dtype=np.float64).reshape(-1, 2)
        if abs(offset_px) > 1e-12:
            points = _offset_contour(
                points,
                offset_px if depth % 2 == 0 else -offset_px,
                0.0,
            )
        if len(points) < 3:
            raise ValueError("The requested Trace border offset collapses a contour")
        source_contours_px.append(points)

    classification_px = tuple(
        (
            lambda simplified, original: (
                simplified if len(simplified) >= 3 else original
            )
        )(
            cv2.approxPolyDP(
                contour.astype(np.float32).reshape(-1, 1, 2),
                1.0,
                True,
            ).reshape(-1, 2).astype(np.float64),
            contour,
        )
        for contour in source_contours_px
    )
    classification_physical = tuple(
        _pixel_to_machine(contour, work_area, pixels_per_mm)
        for contour in classification_px
    )
    physical = classification_physical
    localized_count = 0
    localization_shift_px = 0.0
    if len(classification_px) == 1:
        _check_trace_cancelled()
        localized_px, localization_shift_px, localized_count = (
            _localize_camera_intensity_edges(image, classification_px[0])
        )
        _check_trace_cancelled()
        physical = (_pixel_to_machine(localized_px, work_area, pixels_per_mm),)

    combined = np.concatenate(physical, axis=0)
    padding = max(pitch, options.native_fitting_tolerance_mm)
    fit_tolerance = max(options.native_fitting_tolerance_mm, pitch)
    fit = fit_physical_contours_to_native_path(
        physical,
        list(tree.parents),
        source_pixel_spacing_mm=(pitch, pitch),
        fitting_tolerance_mm=fit_tolerance,
        smoothing_mm=0.0,
        classification_contours_mm=classification_physical,
        frame_bounds_mm=(
            float(np.min(combined[:, 0]) - padding),
            float(np.min(combined[:, 1]) - padding),
            float(np.max(combined[:, 0]) + padding),
            float(np.max(combined[:, 1]) + padding),
        ),
        **_trace_cancel_kwargs(),
    )
    fitted_contours = [
        [list(point) for point in contour.preview_points_mm]
        for contour in fit.contours
    ]
    detection.vector_contours_mm = fitted_contours
    detection.vector_contour_mm = fitted_contours[0]
    detection.native_path = fit.geometry.to_dict()
    detection.native_center_mm = fit.center_mm
    detection.native_width_mm = fit.width_mm
    detection.native_height_mm = fit.height_mm
    detection.native_verified = True
    flattened_points = [point for contour in fitted_contours for point in contour]
    camera_overruns = _work_area_overruns_mm(flattened_points, work_area)
    output_overruns = _work_area_overruns_mm(flattened_points, output_work_area)
    within_camera = max(camera_overruns.values(), default=0.0) <= 1e-9
    within_output = max(output_overruns.values(), default=0.0) <= 1e-9
    detection.selected_default = bool(
        detection.selected_default
        and within_output
        and not detection.diagnostics.get("touches_image_edge", False)
    )
    detection.diagnostics.update(
        {
            "native_fit_status": "verified",
            "raw_contour_point_count": sum(len(contour) for contour in source_contours_px),
            "fit_input_point_count": fit.raw_point_count,
            "fitted_segment_count": fit.fitted_segment_count,
            "native_sequences": [
                "".join(
                    "L" if segment.__class__.__name__ == "PathLineSegment" else "C"
                    for segment in contour.native_subpath.segments
                )
                for contour in fit.contours
            ],
            "native_fitting_tolerance_mm": fit.fitting_tolerance_mm,
            "maximum_fitting_error_mm": max(
                contour.max_fitting_error_mm for contour in fit.contours
            ),
            "maximum_estimated_deviation_mm": pitch
            + localization_shift_px * pitch
            + max(contour.max_fitting_error_mm for contour in fit.contours),
            "rms_fitting_error_mm": max(
                contour.rms_fitting_error_mm for contour in fit.contours
            ),
            "contour_parents": list(tree.parents),
            "contour_depths": [contour.depth for contour in fit.contours],
            "localized_edge_sample_count": localized_count,
            "maximum_edge_localization_shift_mm": localization_shift_px * pitch,
            "within_camera_work_area": within_camera,
            "camera_work_area_overrun_mm": max(
                camera_overruns.values(), default=0.0
            ),
            "camera_work_area_overruns_mm": camera_overruns,
            "within_work_area": within_output,
            "work_area_overrun_mm": max(output_overruns.values(), default=0.0),
            "work_area_overruns_mm": output_overruns,
        }
    )


def _pixel_result_root_groups(
    result: PixelVectorizationResult,
) -> tuple[tuple[int, ...], ...]:
    """Return each raster root contour with all of its hierarchy descendants."""

    groups: list[tuple[int, ...]] = []
    for root_index, root in enumerate(result.contours):
        if root.parent_index is not None:
            continue
        descendants: list[int] = []
        for index, _contour in enumerate(result.contours):
            ancestor = index
            visited = 0
            while result.contours[ancestor].parent_index is not None:
                ancestor = int(result.contours[ancestor].parent_index)
                visited += 1
                if visited > len(result.contours):
                    raise ValueError("Raster vectorization returned a cyclic hierarchy")
            if ancestor == root_index:
                descendants.append(index)
        groups.append(tuple(descendants))
    return tuple(groups)


def _normalized_raster_points_to_camera(
    points: Sequence[Sequence[float]],
    *,
    raster_width_mm: float,
    raster_height_mm: float,
    camera_center_mm: tuple[float, float],
) -> list[list[float]]:
    """Apply the one final raster-local to corrected-camera affine transform."""

    values = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    output = np.empty_like(values)
    output[:, 0] = camera_center_mm[0] + values[:, 0] * raster_width_mm
    output[:, 1] = camera_center_mm[1] + values[:, 1] * raster_height_mm
    return [[float(x), float(y)] for x, y in output]


def _normalized_camera_candidate_geometry(
    result: PixelVectorizationResult,
    contour_indices: tuple[int, ...],
    *,
    raster_width_mm: float,
    raster_height_mm: float,
    camera_center_mm: tuple[float, float],
) -> tuple[NativePathGeometry, tuple[float, float], float, float]:
    source_geometry = NativePathGeometry(
        tuple(result.contours[index].native_subpath for index in contour_indices),
        fill_rule=PathFillRule.EVENODD,
    )
    world_geometry = transform_native_path(
        source_geometry,
        PathAffineTransform.from_components(
            scale_x=raster_width_mm,
            scale_y=raster_height_mm,
            translate_x=camera_center_mm[0],
            translate_y=camera_center_mm[1],
        ),
    )
    x_min, y_min, x_max, y_max = native_path_bounds(world_geometry)
    width_mm = float(x_max - x_min)
    height_mm = float(y_max - y_min)
    if width_mm <= 0.0 or height_mm <= 0.0:
        raise ValueError("A raster Trace candidate has zero physical extent")
    center_mm = ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)
    normalized = transform_native_path(
        world_geometry,
        PathAffineTransform.from_components(
            scale_x=1.0 / width_mm,
            scale_y=1.0 / height_mm,
            translate_x=-center_mm[0] / width_mm,
            translate_y=-center_mm[1] / height_mm,
        ),
    )
    return normalized, center_mm, width_mm, height_mm


def _camera_raster_vector_source(
    normalization: CameraRasterNormalizationResult,
    polarity: str,
    eligibility: CameraTraceEligibilityResult,
) -> PixelVectorizationSource:
    """Adapt one normalized polarity to the shared artwork-raster contract."""

    artwork_raster = normalization.raster_for(polarity)
    # The shared vectorizer's invert flag retains its established meaning. The
    # light response is therefore represented as light artwork on a dark field,
    # while the dark response remains dark artwork on a light field.
    vector_grayscale = (
        artwork_raster
        if polarity == "dark"
        else cv2.bitwise_not(artwork_raster)
    )
    rgba = cv2.cvtColor(vector_grayscale, cv2.COLOR_GRAY2RGBA)
    eligibility_mask = eligibility.material_eligible_mask
    return prepare_pixel_vectorization_source(
        rgba,
        eligibility_mask=(
            None if np.all(eligibility_mask) else eligibility_mask
        ),
    )


def _camera_raster_diagnostics(
    normalization: CameraRasterNormalizationResult,
    eligibility: CameraTraceEligibilityResult,
    *,
    polarity: str,
    normalization_reused: bool,
) -> dict[str, Any]:
    return {
        **asdict(normalization.diagnostics),
        "polarity": polarity,
        "normalization_reused": normalization_reused,
        "eligibility": eligibility.diagnostics.to_dict(),
    }


def _detect_non_grid_contrast_raster(
    image: np.ndarray,
    options: TraceOptions,
    work_area: WorkArea,
    output_work_area: WorkArea,
    pixels_per_mm: float,
    *,
    eligibility: CameraTraceEligibilityResult,
    output_polygon: ConvexPolygon | None = None,
    eligibility_timing: CameraTraceEligibilityTiming | None = None,
    prepared_normalization: CameraRasterNormalizationResult | None = None,
    normalization_timing: CameraRasterNormalizationTiming | None = None,
    vectorization_timing: RasterVectorizationTiming | None = None,
    raster_preview_callback: CameraTraceRasterPreviewCallback | None = None,
    preview_strategy: str | None = None,
) -> TraceResult:
    """Normalize corrected camera pixels, then use the shared raster vector path."""

    _check_trace_cancelled()
    trace_started = time.perf_counter()
    normalization_reused = prepared_normalization is not None
    if normalization_timing is None:
        normalization_timing = CameraRasterNormalizationTiming()
    normalization = prepared_normalization
    if normalization is None:
        normalization = normalize_camera_trace_frame(
            image,
            pixels_per_mm,
            eligibility_mask=eligibility.material_eligible_mask,
            timing=normalization_timing,
        )
        _check_trace_cancelled()
    polarity = "light" if options.contrast_invert else "dark"
    source = _camera_raster_vector_source(normalization, polarity, eligibility)
    raster_width_mm = source.width_px / pixels_per_mm
    raster_height_mm = source.height_px / pixels_per_mm
    camera_center_mm = (
        work_area.x_min + raster_width_mm / 2.0,
        work_area.y_max - raster_height_mm / 2.0,
    )
    effective_tolerance = options.native_fitting_tolerance_mm
    raster_options = RasterVectorizationOptions(
        detection_mode=(
            RasterDetectionMode.AUTO_THRESHOLD
            if options.contrast_threshold_mode == "auto"
            else RasterDetectionMode.MANUAL_THRESHOLD
        ),
        threshold=options.contrast_threshold,
        invert=options.contrast_invert,
        minimum_feature_area_mm2=options.min_area_mm2,
        smoothing_mm=0.0,
        simplification_tolerance_mm=effective_tolerance,
        contour_output=RasterContourOutput.ALL_CONTOURS,
    )
    if vectorization_timing is None:
        vectorization_timing = RasterVectorizationTiming()
    auto_threshold_selection = None
    if options.contrast_threshold_mode == "auto":
        auto_threshold_selection = select_pixel_vectorization_auto_threshold(
            source,
            raster_options,
            timing=vectorization_timing,
            **_trace_cancel_kwargs(),
        )
        raster_options = replace(
            raster_options,
            detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
            threshold=auto_threshold_selection.threshold,
        )
    strategy_name = preview_strategy or f"raster_{polarity}"

    def trace_metadata() -> dict[str, Any]:
        return {
            "camera_raster": _camera_raster_diagnostics(
                normalization,
                eligibility,
                polarity=polarity,
                normalization_reused=normalization_reused,
            ),
            "auto_threshold_selection": (
                None
                if auto_threshold_selection is None
                else auto_threshold_selection.to_dict()
            ),
            "timing": {
                "trace_eligibility": (
                    {}
                    if eligibility_timing is None
                    else eligibility_timing.snapshot()
                ),
                "camera_normalization": normalization_timing.snapshot(),
                "raster_vectorization": vectorization_timing.snapshot(),
                "trace_detection_total_seconds": time.perf_counter() - trace_started,
            },
        }

    def mask_ready(mask_preview: PixelVectorizationMaskPreview) -> None:
        _check_trace_cancelled()
        if raster_preview_callback is None:
            return
        raster_preview_callback(
            CameraTraceRasterPreview(
                strategy=strategy_name,
                polarity=polarity,
                camera_bgr=normalization.corrected_bgr,
                exposed_bed_mask=eligibility.exposed_bed_mask,
                eligible_mask=eligibility.material_eligible_mask,
                normalized_grayscale=source.composited_grayscale,
                foreground_mask=mask_preview.foreground_mask,
                contour_mask=mask_preview.contour_mask,
                threshold_used=mask_preview.threshold_used,
                connected_component_count=mask_preview.connected_component_count,
                selected_strategy=preview_strategy is None,
            )
        )
        _check_trace_cancelled()

    forest_result: PixelVectorizationForestResult
    try:
        forest_result = vectorize_pixel_source_forest(
            source,
            raster_options,
            displayed_width_mm=raster_width_mm,
            displayed_height_mm=raster_height_mm,
            timing=vectorization_timing,
            mask_ready=mask_ready,
            root_review_filter=PixelVectorizationRootReviewFilter(
                maximum_area_mm2=options.max_area_mm2,
                minimum_width_mm=options.min_width_mm,
                minimum_height_mm=options.min_height_mm,
            ),
            **_trace_cancel_kwargs(),
        )
        result = forest_result.result
        if result is None:
            reasons = [failure.reason for failure in forest_result.failures]
            message = (
                reasons[0]
                if reasons
                else "No root contour tree produced verified geometry"
            )
            return TraceResult(
                detected=False,
                detections=[],
                mode_used="contrast",
                target_hue=options.target_hue,
                image_width=image.shape[1],
                image_height=image.shape[0],
                direct_count=0,
                inferred_count=0,
                grid=None,
                message=message,
                options=options,
                camera_work_area={
                    "x_min": float(work_area.x_min),
                    "x_max": float(work_area.x_max),
                    "y_min": float(work_area.y_min),
                    "y_max": float(work_area.y_max),
                },
                output_work_area={
                    "x_min": float(output_work_area.x_min),
                    "x_max": float(output_work_area.x_max),
                    "y_min": float(output_work_area.y_min),
                    "y_max": float(output_work_area.y_max),
                },
                diagnostics={
                    "candidate_failures": [
                        failure.to_dict() for failure in forest_result.failures[:64]
                    ],
                    "candidate_failure_count": len(forest_result.failures),
                    "strategy_metrics": {
                        "valid_candidate_count": 0,
                        "verified_candidate_count": 0,
                        "invalid_candidate_count": len(forest_result.failures),
                        "root_tree_count": forest_result.root_tree_count,
                        "minimum_area_mm2": options.min_area_mm2,
                        "frame_area_mm2": raster_width_mm * raster_height_mm,
                    },
                    **trace_metadata(),
                },
            )
    except RasterVectorizationError as exc:
        message = str(exc)
        if not (
            message.startswith("No foreground features remain")
            or message.startswith("No closed contours were produced")
        ):
            raise
        return TraceResult(
            detected=False,
            detections=[],
            mode_used="contrast",
            target_hue=options.target_hue,
            image_width=image.shape[1],
            image_height=image.shape[0],
            direct_count=0,
            inferred_count=0,
            grid=None,
            message=message,
            options=options,
            camera_work_area={
                "x_min": float(work_area.x_min),
                "x_max": float(work_area.x_max),
                "y_min": float(work_area.y_min),
                "y_max": float(work_area.y_max),
            },
            output_work_area={
                "x_min": float(output_work_area.x_min),
                "x_max": float(output_work_area.x_max),
                "y_min": float(output_work_area.y_min),
                "y_max": float(output_work_area.y_max),
            },
            diagnostics={
                "candidate_failures": [],
                "candidate_failure_count": 0,
                "strategy_metrics": {
                    "valid_candidate_count": 0,
                    "verified_candidate_count": 0,
                    "invalid_candidate_count": 0,
                    "root_tree_count": 0,
                    "minimum_area_mm2": options.min_area_mm2,
                    "frame_area_mm2": raster_width_mm * raster_height_mm,
                },
                **trace_metadata(),
            },
        )
    assert result is not None
    root_groups = _pixel_result_root_groups(result)
    detections: list[TraceDetection] = []
    edge_tolerance_x = 1.0 / (source.width_px * 4.0) + 1e-12
    edge_tolerance_y = 1.0 / (source.height_px * 4.0) + 1e-12
    for contour_indices in root_groups:
        _check_trace_cancelled()
        normalized_geometry, center_mm, width_mm, height_mm = (
            _normalized_camera_candidate_geometry(
                result,
                contour_indices,
                raster_width_mm=raster_width_mm,
                raster_height_mm=raster_height_mm,
                camera_center_mm=camera_center_mm,
            )
        )
        raw_contours_mm: list[list[list[float]]] = []
        vector_contours_mm: list[list[list[float]]] = []
        local_parents: list[int | None] = []
        local_depths: list[int] = []
        index_map = {
            original: local for local, original in enumerate(contour_indices)
        }
        root_depth = result.contours[contour_indices[0]].depth
        image_edge_sides: set[str] = set()
        net_area_mm2 = 0.0
        for original_index in contour_indices:
            contour = result.contours[original_index]
            threshold_points = contour.threshold_points or contour.preview_points
            raw_contours_mm.append(
                _normalized_raster_points_to_camera(
                    threshold_points,
                    raster_width_mm=raster_width_mm,
                    raster_height_mm=raster_height_mm,
                    camera_center_mm=camera_center_mm,
                )
            )
            vector_contours_mm.append(
                _normalized_raster_points_to_camera(
                    contour.preview_points,
                    raster_width_mm=raster_width_mm,
                    raster_height_mm=raster_height_mm,
                    camera_center_mm=camera_center_mm,
                )
            )
            threshold_array = np.asarray(threshold_points, dtype=np.float64)
            if float(np.min(threshold_array[:, 0])) <= -0.5 + edge_tolerance_x:
                image_edge_sides.add("left")
            if float(np.max(threshold_array[:, 0])) >= 0.5 - edge_tolerance_x:
                image_edge_sides.add("right")
            if float(np.max(threshold_array[:, 1])) >= 0.5 - edge_tolerance_y:
                image_edge_sides.add("top")
            if float(np.min(threshold_array[:, 1])) <= -0.5 + edge_tolerance_y:
                image_edge_sides.add("bottom")
            physical = np.asarray(raw_contours_mm[-1], dtype=np.float32)
            signed_area = abs(float(cv2.contourArea(physical)))
            relative_depth = contour.depth - root_depth
            net_area_mm2 += signed_area if relative_depth % 2 == 0 else -signed_area
            parent = contour.parent_index
            local_parents.append(index_map.get(parent) if parent is not None else None)
            local_depths.append(relative_depth)

        if (
            net_area_mm2 > options.max_area_mm2
            or width_mm < options.min_width_mm
            or height_mm < options.min_height_mm
        ):
            continue
        flattened_points = [
            point for contour in vector_contours_mm for point in contour
        ]
        camera_overruns = _work_area_overruns_mm(flattened_points, work_area)
        output_overruns = _work_area_overruns_mm(flattened_points, output_work_area)
        within_camera = max(camera_overruns.values(), default=0.0) <= 1e-9
        within_output = max(output_overruns.values(), default=0.0) <= 1e-9
        touches_image_edge = bool(image_edge_sides)
        group_contours = [result.contours[index] for index in contour_indices]
        box_mm = [
            [center_mm[0] - width_mm / 2.0, center_mm[1] - height_mm / 2.0],
            [center_mm[0] + width_mm / 2.0, center_mm[1] - height_mm / 2.0],
            [center_mm[0] + width_mm / 2.0, center_mm[1] + height_mm / 2.0],
            [center_mm[0] - width_mm / 2.0, center_mm[1] + height_mm / 2.0],
        ]
        detections.append(
            TraceDetection(
                id=_new_id(),
                index=0,
                source="direct",
                confidence=1.0,
                score=1.0,
                shape="contour",
                center_mm=center_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                rotation_deg=0.0,
                corner_radius_mm=0.0,
                area_mm2=max(0.0, net_area_mm2),
                contour_mm=raw_contours_mm[0],
                vector_contour_mm=vector_contours_mm[0],
                vector_contours_mm=vector_contours_mm,
                box_mm=box_mm,
                selected_default=bool(
                    options.confidence_threshold <= 1.0
                    and within_output
                    and not touches_image_edge
                ),
                diagnostics={
                    "mask_source": "raster_non_grid",
                    "foreground_mask_sha256": hashlib.sha256(
                        result.foreground_mask.tobytes(order="C")
                    ).hexdigest(),
                    "foreground_pixel_count": int(
                        np.count_nonzero(result.foreground_mask)
                    ),
                    "pixel_source_key": result.source_key,
                    "camera_normalization_key": (
                        normalization.diagnostics.normalization_key
                    ),
                    "threshold_used": result.threshold_used,
                    "threshold_mode": options.contrast_threshold_mode,
                    "invert": options.contrast_invert,
                    "connected_component_count": result.connected_component_count,
                    "root_tree_count": (
                        forest_result.root_tree_count
                        if forest_result is not None
                        else len(root_groups)
                    ),
                    "raw_contour_count": len(result.contours),
                    "raw_contour_point_count": sum(
                        contour.raw_point_count for contour in group_contours
                    ),
                    "fitted_segment_count": sum(
                        contour.fitted_segment_count for contour in group_contours
                    ),
                    "native_sequences": [
                        "".join(
                            "L" if segment.__class__.__name__ == "PathLineSegment" else "C"
                            for segment in contour.native_subpath.segments
                        )
                        for contour in group_contours
                    ],
                    "native_fitting_tolerance_mm": effective_tolerance,
                    "maximum_fitting_error_mm": max(
                        contour.max_fitting_error_mm for contour in group_contours
                    ),
                    "rms_fitting_error_mm": max(
                        contour.rms_fitting_error_mm for contour in group_contours
                    ),
                    "maximum_estimated_deviation_mm": max(
                        contour.max_estimated_deviation_mm
                        for contour in group_contours
                    ),
                    "contour_parents": local_parents,
                    "contour_depths": local_depths,
                    "native_fit_status": "verified",
                    "touches_image_edge": touches_image_edge,
                    "image_edge_sides": sorted(image_edge_sides),
                    "within_camera_work_area": within_camera,
                    "camera_work_area_overrun_mm": max(
                        camera_overruns.values(), default=0.0
                    ),
                    "camera_work_area_overruns_mm": camera_overruns,
                    "within_work_area": within_output,
                    "work_area_overrun_mm": max(
                        output_overruns.values(), default=0.0
                    ),
                    "work_area_overruns_mm": output_overruns,
                    "border_offset_ignored": abs(options.border_offset_mm) > 1e-12,
                    "pruned_contour_count": getattr(
                        result,
                        "pruned_contour_count",
                        0,
                    ),
                    "degenerate_contour_count": getattr(
                        result,
                        "degenerate_contour_count",
                        0,
                    ),
                    "rejected_contour_tree_count": getattr(
                        result,
                        "rejected_contour_tree_count",
                        0,
                    ),
                },
                raw_contours_mm=raw_contours_mm,
                native_path=normalized_geometry.to_dict(),
                native_center_mm=center_mm,
                native_width_mm=width_mm,
                native_height_mm=height_mm,
                native_verified=True,
            )
        )

    component_labels, boundary_component_labels = (
        _hard_roi_boundary_component_labels(
            result.foreground_mask,
            eligibility.hard_roi_mask,
        )
    )
    hard_roi_boundary_rejected_count = sum(
        _detection_uses_component_labels(
            item,
            component_labels,
            boundary_component_labels,
            work_area,
            pixels_per_mm,
        )
        for item in detections
    )
    if hard_roi_boundary_rejected_count:
        detections = [
            item
            for item in detections
            if not _detection_uses_component_labels(
                item,
                component_labels,
                boundary_component_labels,
                work_area,
                pixels_per_mm,
            )
        ]
    polygon_rejected_count = sum(
        not _detection_within_output_polygon(item, output_polygon)
        for item in detections
    )
    if polygon_rejected_count:
        detections = [
            item
            for item in detections
            if _detection_within_output_polygon(item, output_polygon)
        ]
    detections.sort(key=lambda item: (-item.center_mm[1], item.center_mm[0]))
    for index, detection in enumerate(detections, 1):
        detection.index = index
    selected_count = sum(item.selected_default for item in detections)
    message = (
        f"Found {len(detections)} direct raster trace candidate"
        f"{'s' if len(detections) != 1 else ''}; "
        f"threshold {result.threshold_used}; {selected_count} selected"
    )
    outside_count = sum(
        not bool(item.diagnostics["within_work_area"]) for item in detections
    )
    if outside_count:
        guarded = output_work_area != work_area
        boundary_name = "guarded output area" if guarded else "work area"
        maximum_by_side = {
            side: max(
                float(item.diagnostics["work_area_overruns_mm"].get(side, 0.0))
                for item in detections
            )
            for side in ("left", "right", "bottom", "top")
        }
        side_summary = "; ".join(
            f"{side} by {amount:.2f} mm"
            for side, amount in maximum_by_side.items()
            if amount > 1e-9
        )
        message += (
            f"; WARNING: {outside_count} outline"
            f"{'s extend' if outside_count != 1 else ' extends'} outside the "
            f"{boundary_name} ({side_summary}) and "
            f"{'were' if outside_count != 1 else 'was'} not preselected"
        )
    cropped_count = sum(
        bool(item.diagnostics["touches_image_edge"]) for item in detections
    )
    if cropped_count:
        message += (
            f"; WARNING: {cropped_count} observed outline"
            f"{'s touch' if cropped_count != 1 else ' touches'} the camera/work-area "
            f"image edge, may be cropped, and "
            f"{'were' if cropped_count != 1 else 'was'} not preselected"
        )
    if not detections:
        message = "No raster trace candidates passed the current review filters"
    foreground_fraction, border_foreground_fraction = _mask_occupancy(
        result.foreground_mask,
        eligibility.material_eligible_mask,
    )
    failures = forest_result.failures
    root_tree_count = forest_result.root_tree_count
    prefit_review_failure_count = sum(
        failure.stage == "review_filter" for failure in failures
    )
    fitted_failure_count = len(failures) - prefit_review_failure_count
    review_filtered_candidate_count = max(
        prefit_review_failure_count,
        root_tree_count - fitted_failure_count - len(detections),
    )
    unavailable_candidate_count = max(0, root_tree_count - len(detections))
    strategy_metrics = {
        "foreground_fraction": foreground_fraction,
        "border_foreground_fraction": border_foreground_fraction,
        "root_tree_count": root_tree_count,
        "valid_candidate_count": len(detections),
        "verified_candidate_count": sum(item.native_verified for item in detections),
        "invalid_candidate_count": unavailable_candidate_count,
        "review_filtered_candidate_count": review_filtered_candidate_count,
        "output_polygon_rejected_candidate_count": polygon_rejected_count,
        "hard_roi_boundary_rejected_candidate_count": (
            hard_roi_boundary_rejected_count
        ),
        "prefit_review_filtered_candidate_count": prefit_review_failure_count,
        "valid_area_mm2": sum(item.area_mm2 for item in detections),
        "microscopic_candidate_count": sum(
            item.area_mm2 < 4.0 * options.min_area_mm2 for item in detections
        ),
        "within_frame_candidate_count": sum(
            bool(item.diagnostics.get("within_work_area", False))
            and not bool(item.diagnostics.get("touches_image_edge", False))
            for item in detections
        ),
        "minimum_area_mm2": options.min_area_mm2,
        "frame_area_mm2": (
            float(np.count_nonzero(eligibility.material_eligible_mask))
            / pixels_per_mm**2
        ),
        "threshold": result.threshold_used,
        "polarity": "light" if options.contrast_invert else "dark",
        "pruned_contour_count": getattr(
            result,
            "pruned_contour_count",
            0,
        ),
        "degenerate_contour_count": getattr(
            result,
            "degenerate_contour_count",
            0,
        ),
        "rejected_contour_tree_count": getattr(
            result,
            "rejected_contour_tree_count",
            0,
        ),
    }
    return TraceResult(
        detected=bool(detections),
        detections=detections,
        mode_used="contrast",
        target_hue=options.target_hue,
        image_width=image.shape[1],
        image_height=image.shape[0],
        direct_count=len(detections),
        inferred_count=0,
        grid=None,
        message=message,
        options=options,
        camera_work_area={
            "x_min": float(work_area.x_min),
            "x_max": float(work_area.x_max),
            "y_min": float(work_area.y_min),
            "y_max": float(work_area.y_max),
        },
        output_work_area={
            "x_min": float(output_work_area.x_min),
            "x_max": float(output_work_area.x_max),
            "y_min": float(output_work_area.y_min),
            "y_max": float(output_work_area.y_max),
        },
        diagnostics={
            "candidate_failures": [failure.to_dict() for failure in failures[:64]],
            "candidate_failure_count": len(failures),
            "strategy_metrics": strategy_metrics,
            **trace_metadata(),
        },
    )


def _concise_auto_reason(value: object) -> str:
    reason = " ".join(str(value).split())
    if not reason:
        return "strategy produced no verified candidates"
    return reason if len(reason) <= 240 else reason[:237] + "..."


def _auto_attempt_diagnostics(
    *,
    name: str,
    label: str,
    status: str,
    reason: str,
    metrics: Mapping[str, Any] | None = None,
    score: float | None = None,
    score_terms: Mapping[str, float] | None = None,
    threshold: int | None = None,
    polarity: str | None = None,
    target_hue: float | None = None,
    hue_tolerance: float | None = None,
) -> dict[str, Any]:
    values = dict(metrics or {})
    valid_count = int(values.get("valid_candidate_count", 0))
    invalid_count = int(values.get("invalid_candidate_count", 0))
    root_count = int(values.get("root_tree_count", valid_count + invalid_count))
    output: dict[str, Any] = {
        "name": name,
        "label": label,
        "status": status,
        "reason": _concise_auto_reason(reason),
        "candidate_count": root_count,
        "valid_candidate_count": valid_count,
        "invalid_candidate_count": invalid_count,
        "root_tree_count": root_count,
        "foreground_fraction": values.get("foreground_fraction"),
        "border_foreground_fraction": values.get("border_foreground_fraction"),
        "score": score,
        "score_terms": dict(score_terms or {}),
    }
    if threshold is not None:
        output["threshold"] = int(threshold)
    if polarity is not None:
        output["polarity"] = polarity
    if target_hue is not None:
        output["target_hue"] = float(target_hue)
    if hue_tolerance is not None:
        output["hue_tolerance"] = float(hue_tolerance)
    for key in (
        "valid_area_mm2",
        "microscopic_candidate_count",
        "within_frame_candidate_count",
        "review_filtered_candidate_count",
        "output_polygon_rejected_candidate_count",
        "hard_roi_boundary_rejected_candidate_count",
        "pruned_contour_count",
        "degenerate_contour_count",
        "rejected_contour_tree_count",
    ):
        if key in values:
            output[key] = values[key]
    return output


def _detect_non_grid_auto(
    image: np.ndarray,
    options: TraceOptions,
    work_area: WorkArea,
    output_work_area: WorkArea,
    pixels_per_mm: float,
    *,
    eligibility: CameraTraceEligibilityResult,
    output_polygon: ConvexPolygon | None = None,
    eligibility_timing: CameraTraceEligibilityTiming | None = None,
    raster_preview_callback: CameraTraceRasterPreviewCallback | None = None,
) -> TraceResult:
    """Choose among production non-grid strategies after one normalization."""

    _check_trace_cancelled()
    auto_started = time.perf_counter()
    normalization_timing = CameraRasterNormalizationTiming()
    normalization = normalize_camera_trace_frame(
        image,
        pixels_per_mm,
        eligibility_mask=eligibility.material_eligible_mask,
        timing=normalization_timing,
    )
    _check_trace_cancelled()
    attempts: list[dict[str, Any]] = []
    successful: list[tuple[float, int, str, TraceResult]] = []
    attempt_previews: dict[str, CameraTraceRasterPreview] = {}
    attempt_vector_timings: dict[str, RasterVectorizationTiming] = {}

    def auto_preview(preview: CameraTraceRasterPreview) -> None:
        _check_trace_cancelled()
        provisional = replace(preview, selected_strategy=False)
        attempt_previews[provisional.strategy] = provisional
        if raster_preview_callback is not None:
            raster_preview_callback(provisional)
        _check_trace_cancelled()

    for order, (name, polarity, invert) in enumerate(
        (
            ("raster_dark", "dark", False),
            ("raster_light", "light", True),
        )
    ):
        _check_trace_cancelled()
        label = f"Raster · {polarity} foreground"
        raster_options = replace(
            options,
            detection_mode="contrast",
            contrast_threshold_mode="auto",
            contrast_invert=invert,
            regular_grid=False,
            output_mode="native",
            border_offset_mode="uniform",
            border_offset_mm=0.0,
            border_offset_top_mm=0.0,
            border_offset_right_mm=0.0,
            border_offset_bottom_mm=0.0,
            border_offset_left_mm=0.0,
        )
        vectorization_timing = RasterVectorizationTiming()
        attempt_vector_timings[name] = vectorization_timing
        try:
            result = _detect_non_grid_contrast_raster(
                image,
                raster_options,
                work_area,
                output_work_area,
                pixels_per_mm,
                eligibility=eligibility,
                output_polygon=output_polygon,
                eligibility_timing=eligibility_timing,
                prepared_normalization=normalization,
                normalization_timing=normalization_timing,
                vectorization_timing=vectorization_timing,
                raster_preview_callback=auto_preview,
                preview_strategy=name,
            )
        except (RasterVectorizationError, ValueError) as exc:
            preview = attempt_previews.get(name)
            attempt = _auto_attempt_diagnostics(
                    name=name,
                    label=label,
                    status="failed",
                    reason=_concise_auto_reason(exc),
                    threshold=(
                        None if preview is None else preview.threshold_used
                    ),
                    polarity=polarity,
                )
            attempt["timing"] = vectorization_timing.snapshot()
            attempts.append(attempt)
            continue
        metrics = dict(result.diagnostics.get("strategy_metrics", {}))
        threshold = metrics.get("threshold")
        score, terms, rejection = _auto_strategy_quality(metrics)
        if not result.detected or score is None:
            reason = (
                result.message
                if not result.detected
                else rejection or result.message
            )
            attempt = _auto_attempt_diagnostics(
                    name=name,
                    label=label,
                    status="rejected",
                    reason=reason,
                    metrics=metrics,
                    score_terms=terms,
                    threshold=None if threshold is None else int(threshold),
                    polarity=polarity,
                )
            attempt["timing"] = vectorization_timing.snapshot()
            attempts.append(attempt)
            continue
        attempt = _auto_attempt_diagnostics(
                name=name,
                label=label,
                status="success",
                reason="verified native geometry",
                metrics=metrics,
                score=score,
                score_terms=terms,
                threshold=None if threshold is None else int(threshold),
                polarity=polarity,
            )
        attempt["timing"] = vectorization_timing.snapshot()
        attempts.append(attempt)
        successful.append((score, -order, name, result))

    color_evidence = _analyze_auto_color_suitability(
        image,
        options,
        pixels_per_mm,
        eligibility.material_eligible_mask,
    )
    _check_trace_cancelled()
    color_name = "color"
    color_label = "Color"
    color_order = 2
    if not color_evidence.suitable or color_evidence.target_hue is None:
        attempts.append(
            {
                **_auto_attempt_diagnostics(
                    name=color_name,
                    label=color_label,
                    status="skipped",
                    reason=color_evidence.reason,
                    metrics=color_evidence.diagnostics(),
                ),
                **color_evidence.diagnostics(),
            }
        )
    else:
        color_options = replace(
            options,
            detection_mode="color",
            target_hue=color_evidence.target_hue,
            target_bgr=None,
            hue_tolerance=14.0,
            min_saturation=45,
            regular_grid=False,
            output_mode="native",
            border_offset_mode="uniform",
            border_offset_mm=0.0,
            border_offset_top_mm=0.0,
            border_offset_right_mm=0.0,
            border_offset_bottom_mm=0.0,
            border_offset_left_mm=0.0,
        )
        try:
            color_result = detect_objects(
                image,
                color_options,
                work_area,
                pixels_per_mm,
                output_work_area=output_work_area,
                background_image=None,
                trace_roi_polygons_mm=None,
                trace_output_polygon_mm=output_polygon,
                raster_preview_callback=auto_preview,
                _isolate_native_candidates=True,
                _camera_normalization=normalization,
                _camera_eligibility=eligibility,
            )
        except (RasterVectorizationError, ValueError) as exc:
            attempts.append(
                {
                    **_auto_attempt_diagnostics(
                        name=color_name,
                        label=color_label,
                        status="failed",
                        reason=_concise_auto_reason(exc),
                        target_hue=color_evidence.target_hue,
                        hue_tolerance=14.0,
                    ),
                    **color_evidence.diagnostics(),
                }
            )
        else:
            color_metrics = dict(
                color_result.diagnostics.get("strategy_metrics", {})
            )
            color_metrics["foreground_fraction"] = color_evidence.foreground_fraction
            color_metrics["border_foreground_fraction"] = (
                color_evidence.border_foreground_fraction
            )
            color_score, color_terms, color_rejection = _auto_strategy_quality(
                color_metrics
            )
            if color_result.detected and color_score is not None:
                best_raster_score = max(
                    (
                        score
                        for score, _tie, name, _result in successful
                        if name.startswith("raster_")
                    ),
                    default=None,
                )
                if (
                    best_raster_score is not None
                    and color_score < best_raster_score + AUTO_COLOR_RASTER_MARGIN
                ):
                    color_rejection = (
                        "Color is not materially better than the credible raster "
                        f"interpretation (requires +{AUTO_COLOR_RASTER_MARGIN:.0f})"
                    )
                    color_score = None
            if not color_result.detected or color_score is None:
                color_reason = (
                    color_result.message
                    if not color_result.detected
                    else color_rejection or color_result.message
                )
                attempts.append(
                    {
                        **_auto_attempt_diagnostics(
                            name=color_name,
                            label=color_label,
                            status="rejected",
                            reason=color_reason,
                            metrics=color_metrics,
                            score_terms=color_terms,
                            target_hue=color_evidence.target_hue,
                            hue_tolerance=14.0,
                        ),
                        **color_evidence.diagnostics(),
                    }
                )
            else:
                attempts.append(
                    {
                        **_auto_attempt_diagnostics(
                            name=color_name,
                            label=color_label,
                            status="success",
                            reason="verified native geometry",
                            metrics=color_metrics,
                            score=color_score,
                            score_terms=color_terms,
                            target_hue=color_evidence.target_hue,
                            hue_tolerance=14.0,
                        ),
                        **color_evidence.diagnostics(),
                    }
                )
                successful.append(
                    (color_score, -color_order, color_name, color_result)
                )

    if not successful:
        summaries = "; ".join(
            f"{attempt['label']}: {attempt['reason']}" for attempt in attempts
        )
        raise ValueError(
            "No credible trace interpretation was found. " + summaries
        )

    selected_score, _tie_break, selected_name, selected = max(successful)
    selected_attempt = next(
        attempt for attempt in attempts if attempt["name"] == selected_name
    )
    effective_options = replace(selected.options, detection_mode="auto")
    if selected_name.startswith("raster_"):
        effective_options = replace(
            effective_options,
            contrast_threshold=int(selected_attempt["threshold"]),
        )
    selected.options = effective_options
    selected.diagnostics = {
        **selected.diagnostics,
        "timing": {
            **dict(selected.diagnostics.get("timing", {})),
            "trace_eligibility": (
                {}
                if eligibility_timing is None
                else eligibility_timing.snapshot()
            ),
            "camera_normalization": normalization_timing.snapshot(),
            "auto_raster_attempts": {
                name: timing.snapshot()
                for name, timing in attempt_vector_timings.items()
            },
            "trace_detection_total_seconds": time.perf_counter() - auto_started,
        },
        "auto": {
            "selected_strategy": selected_name,
            "selected_score": selected_score,
            "attempts": attempts,
            "normalization_key": normalization.diagnostics.normalization_key,
            "background_estimate_count": 1,
            "requested_options": options.to_dict(),
            "effective_options": effective_options.to_dict(),
        },
    }
    for detection in selected.detections:
        detection.diagnostics["auto_selected_strategy"] = selected_name
        detection.diagnostics["auto_selected_score"] = selected_score
    selected_preview = attempt_previews.get(selected_name)
    if selected_preview is not None and raster_preview_callback is not None:
        _check_trace_cancelled()
        raster_preview_callback(
            replace(
                selected_preview,
                selected_strategy=True,
                native_fitting_completed=True,
            )
        )
        _check_trace_cancelled()
    if selected_name.startswith("raster_"):
        polarity = str(selected_attempt["polarity"])
        threshold = int(selected_attempt["threshold"])
        strategy_summary = f"Raster · {polarity} foreground · Auto {threshold}"
    else:
        strategy_summary = (
            f"Color · hue {float(selected_attempt['target_hue']):.0f} · "
            f"tolerance {float(selected_attempt['hue_tolerance']):.0f}"
        )
    invalid_count = int(selected_attempt["invalid_candidate_count"])
    object_count = selected.direct_count
    selected.message = (
        f"Auto chose: {strategy_summary}; {object_count} valid object"
        f"{'s' if object_count != 1 else ''}"
    )
    if invalid_count:
        selected.message += (
            f"; {invalid_count} invalid or filtered independent candidate"
            f"{'s were' if invalid_count != 1 else ' was'} omitted"
        )
    return selected


def _detect_objects(
    image: np.ndarray,
    options: TraceOptions | Mapping[str, Any] | None,
    work_area: WorkArea,
    pixels_per_mm: float,
    *,
    output_work_area: WorkArea | None = None,
    background_image: np.ndarray | None = None,
    trace_roi_polygons_mm: Sequence[Sequence[Sequence[float]]] | None = None,
    trace_output_polygon_mm: Sequence[Sequence[float]] | None = None,
    trace_roi_source: str = "guarded output area",
    reference_required: bool = False,
    reference_identity: str | None = None,
    mask_override: np.ndarray | None = None,
    raster_preview_callback: CameraTraceRasterPreviewCallback | None = None,
    _isolate_native_candidates: bool = False,
    _camera_normalization: CameraRasterNormalizationResult | None = None,
    _camera_eligibility: CameraTraceEligibilityResult | None = None,
    _camera_eligibility_timing: CameraTraceEligibilityTiming | None = None,
) -> TraceResult:
    _check_trace_cancelled()
    options = (
        options
        if isinstance(options, TraceOptions)
        else TraceOptions.from_mapping(options)
    )
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("No rectified camera image is available")
    _require_bgr_image(image, "Rectified camera image")
    if raster_preview_callback is not None and not callable(
        raster_preview_callback
    ):
        raise TypeError("raster_preview_callback must be callable or None")
    if _camera_normalization is not None and not isinstance(
        _camera_normalization,
        CameraRasterNormalizationResult,
    ):
        raise TypeError("_camera_normalization must be a normalization result or None")
    pixels_per_mm = _finite_option(pixels_per_mm, "pixels_per_mm")
    if pixels_per_mm <= 0:
        raise ValueError("pixels_per_mm must be positive and finite")
    output_work_area = work_area if output_work_area is None else output_work_area
    camera_values = (
        work_area.x_min,
        work_area.x_max,
        work_area.y_min,
        work_area.y_max,
    )
    if not all(math.isfinite(float(value)) for value in camera_values):
        raise ValueError("camera work-area limits must be finite")
    if work_area.width <= 0 or work_area.height <= 0:
        raise ValueError("camera work area must have positive dimensions")
    output_values = (
        output_work_area.x_min,
        output_work_area.x_max,
        output_work_area.y_min,
        output_work_area.y_max,
    )
    if not all(math.isfinite(float(value)) for value in output_values):
        raise ValueError("output_work_area limits must be finite")
    if output_work_area.width <= 0 or output_work_area.height <= 0:
        raise ValueError("output_work_area must have positive dimensions")
    if (
        output_work_area.x_min < work_area.x_min
        or output_work_area.x_max > work_area.x_max
        or output_work_area.y_min < work_area.y_min
        or output_work_area.y_max > work_area.y_max
    ):
        raise ValueError("output_work_area must lie inside the camera work area")
    output_polygon = (
        None
        if trace_output_polygon_mm is None
        else normalize_convex_polygon(
            trace_output_polygon_mm,
            label="Camera Trace output polygon",
        )
    )
    camera_eligibility = _camera_eligibility
    eligibility_timing = _camera_eligibility_timing
    if not options.regular_grid:
        if camera_eligibility is None:
            if reference_required and options.detection_mode == "auto" and background_image is None:
                raise ValueError(
                    "Auto requires the validated empty-honeycomb reference for this "
                    "honeycomb-local Trace request"
                )
            polygons = trace_roi_polygons_mm
            if polygons is None:
                polygons = (
                    (
                        (output_work_area.x_min, output_work_area.y_min),
                        (output_work_area.x_max, output_work_area.y_min),
                        (output_work_area.x_max, output_work_area.y_max),
                        (output_work_area.x_min, output_work_area.y_max),
                    ),
                )
            eligibility_timing = CameraTraceEligibilityTiming()
            camera_eligibility = prepare_camera_trace_eligibility(
                image,
                work_area,
                pixels_per_mm,
                roi_polygons_mm=polygons,
                roi_source=trace_roi_source,
                reference_bgr=background_image,
                reference_identity=reference_identity,
                timing=eligibility_timing,
            )
            _check_trace_cancelled()
        elif camera_eligibility.material_eligible_mask.shape != image.shape[:2]:
            raise ValueError("Prepared Camera Trace eligibility does not match the image")
        if not np.any(camera_eligibility.material_eligible_mask):
            if options.detection_mode == "auto":
                raise ValueError(
                    "Auto found no eligible material after hard ROI and trusted "
                    "empty-bed suppression"
                )
            return TraceResult(
                detected=False,
                detections=[],
                mode_used=options.detection_mode,
                target_hue=options.target_hue,
                image_width=image.shape[1],
                image_height=image.shape[0],
                direct_count=0,
                inferred_count=0,
                grid=None,
                message=(
                    "No eligible material remains after hard ROI and trusted "
                    "empty-bed suppression"
                ),
                options=options,
                camera_work_area={
                    "x_min": float(work_area.x_min),
                    "x_max": float(work_area.x_max),
                    "y_min": float(work_area.y_min),
                    "y_max": float(work_area.y_max),
                },
                output_work_area={
                    "x_min": float(output_work_area.x_min),
                    "x_max": float(output_work_area.x_max),
                    "y_min": float(output_work_area.y_min),
                    "y_max": float(output_work_area.y_max),
                },
                diagnostics={
                    "camera_eligibility": camera_eligibility.diagnostics.to_dict(),
                    "timing": {
                        "trace_eligibility": (
                            {} if eligibility_timing is None else eligibility_timing.snapshot()
                        )
                    },
                },
            )
    camera_work_area_payload = {
        "x_min": float(work_area.x_min),
        "x_max": float(work_area.x_max),
        "y_min": float(work_area.y_min),
        "y_max": float(work_area.y_max),
    }
    output_work_area_payload = {
        "x_min": float(output_work_area.x_min),
        "x_max": float(output_work_area.x_max),
        "y_min": float(output_work_area.y_min),
        "y_max": float(output_work_area.y_max),
    }
    if (
        options.detection_mode == "auto"
        and not options.regular_grid
        and mask_override is None
    ):
        return _detect_non_grid_auto(
            image,
            options,
            work_area,
            output_work_area,
            pixels_per_mm,
            eligibility=camera_eligibility,
            output_polygon=output_polygon,
            eligibility_timing=eligibility_timing,
            raster_preview_callback=raster_preview_callback,
        )
    if (
        options.detection_mode == "contrast"
        and not options.regular_grid
        and mask_override is None
    ):
        return _detect_non_grid_contrast_raster(
            image,
            options,
            work_area,
            output_work_area,
            pixels_per_mm,
            eligibility=camera_eligibility,
            output_polygon=output_polygon,
            eligibility_timing=eligibility_timing,
            prepared_normalization=_camera_normalization,
            raster_preview_callback=raster_preview_callback,
        )
    target_hue = options.target_hue
    masks: list[tuple[str, str, np.ndarray, float | None]] = []
    if mask_override is not None:
        if (
            not isinstance(mask_override, np.ndarray)
            or mask_override.ndim != 2
            or mask_override.shape != image.shape[:2]
        ):
            raise ValueError(
                "mask_override must be a 2-D mask matching the image"
            )
        masks.append(
            (
                "mask",
                "exact_mask",
                cv2.bitwise_and(
                    (mask_override > 0).astype(np.uint8) * 255,
                    (
                        camera_eligibility.material_eligible_mask
                        if camera_eligibility is not None
                        else np.full(mask_override.shape, 255, dtype=np.uint8)
                    ),
                ),
                None,
            )
        )
    elif options.detection_mode in {"auto", "color"}:
        if target_hue is None:
            target_hue = auto_target_hue(
                image,
                options.min_saturation,
                eligibility_mask=(
                    None
                    if camera_eligibility is None
                    else camera_eligibility.material_eligible_mask
                ),
            )
        if target_hue is not None:
            masks.append(
                (
                    "color",
                    "color",
                    _color_mask(
                        image,
                        target_hue,
                        options,
                        pixels_per_mm,
                        eligibility_mask=(
                            None
                            if camera_eligibility is None
                            else camera_eligibility.material_eligible_mask
                        ),
                    ),
                    target_hue,
                )
            )
    if mask_override is None and options.detection_mode in {"auto", "contrast"}:
        masks.append(
            (
                "contrast",
                "closed_outline",
                _closed_outline_mask(image, pixels_per_mm, options),
                None,
            )
        )
        masks.append(
            (
                "contrast",
                "local_absolute",
                _contrast_mask(image, pixels_per_mm),
                None,
            )
        )
        for source, region_mask in zip(
            ("global_dark", "global_light", "clahe_dark", "clahe_light"),
            _global_contrast_masks(image, pixels_per_mm),
            strict=True,
        ):
            masks.append(("contrast", source, region_mask, None))
        for source, region_mask in zip(
            ("adaptive_dark", "adaptive_light"),
            _adaptive_contrast_masks(image, pixels_per_mm),
            strict=True,
        ):
            masks.append(("contrast", source, region_mask, None))
        for source, region_mask in zip(
            ("local_dark", "local_light"),
            _contrast_region_masks(image, pixels_per_mm),
            strict=True,
        ):
            masks.append(("contrast", source, region_mask, None))
    if not masks:
        return TraceResult(
            False, [], options.detection_mode, target_hue,
            image.shape[1], image.shape[0], 0, 0, None,
            "No suitable target color was found", options,
            camera_work_area_payload,
            output_work_area_payload,
        )

    filled_region_sources = {
        "color",
        "global_dark",
        "global_light",
        "clahe_dark",
        "clahe_light",
        "adaptive_dark",
        "adaptive_light",
        "closed_outline",
    }
    best = None
    for mode, mask_source, mask, hue in masks:
        _check_trace_cancelled()
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        candidates = []
        for contour_index, contour in enumerate(contours):
            if contour_index % 32 == 0:
                _check_trace_cancelled()
            candidate = _candidate(
                contour,
                mask,
                options,
                work_area,
                output_work_area,
                pixels_per_mm,
            )
            if candidate is not None:
                candidates.append(candidate)
        washers = (
            _washer_candidates(
                mask,
                options,
                work_area,
                output_work_area,
                pixels_per_mm,
            )
            if mask_source in filled_region_sources
            and mask_source != "closed_outline"
            else []
        )
        if washers:
            retained = []
            for candidate in candidates:
                if any(
                    math.hypot(
                        float(candidate["center_px"][0]) - float(washer["center_px"][0]),
                        float(candidate["center_px"][1]) - float(washer["center_px"][1]),
                    )
                    <= max(float(washer["width_px"]), float(washer["height_px"])) * 0.1
                    for washer in washers
                ):
                    continue
                retained.append(candidate)
            candidates = [*retained, *washers]
        raw_quality = sum(item["score"] for item in candidates) + 0.35 * len(
            candidates
        )
        if washers:
            direct, inferred, grid = candidates, [], None
        else:
            direct, inferred, grid = _infer_grid(
                candidates,
                options,
                work_area,
                output_work_area,
                pixels_per_mm,
                image,
            )
        family = direct if grid is not None else candidates
        family_area = sum(float(item["area_mm2"]) for item in family)
        family_score = (
            float(np.mean([item["score"] for item in family])) if family else 0.0
        )
        if options.regular_grid and grid is not None:
            rank = (
                2.0 if mask_source in filled_region_sources else 1.0,
                float(grid["observed_area_mm2"]) * float(grid["quality"]),
                float(grid["observed_cells"]) * float(grid["quality"]),
                family_score,
                raw_quality,
            )
        elif washers:
            rank = (
                3.0,
                float(len(washers)),
                float(np.mean([item["score"] for item in washers])),
                -float(len(candidates) - len(washers)),
            )
        else:
            rank = (
                1.0 if mask_source in filled_region_sources and family else 0.0,
                raw_quality,
                family_score,
                math.log1p(family_area),
            )
        for item in (*direct, *inferred):
            item["mask_source"] = mask_source
        if grid is not None:
            grid["mask_source"] = mask_source
        if best is None or rank > best[0]:
            best = (rank, mode, hue, direct, inferred, grid, mask)
    assert best is not None
    _, mode_used, chosen_hue, candidates, inferred, grid, chosen_mask = best
    if chosen_hue is not None:
        target_hue = chosen_hue
    if (
        raster_preview_callback is not None
        and not options.regular_grid
        and options.detection_mode == "color"
    ):
        if _camera_normalization is None:
            preview_camera = _immutable_trace_preview_array(image)
            preview_grayscale = _immutable_trace_preview_array(
                cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            )
        else:
            preview_camera = _camera_normalization.corrected_bgr
            preview_grayscale = _camera_normalization.dark_raster
        preview_mask = _immutable_trace_preview_array(chosen_mask)
        component_count, _labels = cv2.connectedComponents(preview_mask)
        raster_preview_callback(
            CameraTraceRasterPreview(
                strategy="color",
                polarity="color",
                camera_bgr=preview_camera,
                exposed_bed_mask=(
                    _immutable_trace_preview_array(
                        np.zeros(image.shape[:2], dtype=np.uint8)
                    )
                    if camera_eligibility is None
                    else camera_eligibility.exposed_bed_mask
                ),
                eligible_mask=(
                    _immutable_trace_preview_array(
                        np.full(image.shape[:2], 255, dtype=np.uint8)
                    )
                    if camera_eligibility is None
                    else camera_eligibility.material_eligible_mask
                ),
                normalized_grayscale=preview_grayscale,
                foreground_mask=preview_mask,
                contour_mask=preview_mask,
                threshold_used=None,
                connected_component_count=max(0, component_count - 1),
                selected_strategy=True,
            )
        )
        _check_trace_cancelled()
    _grid_cell_review_evidence(
        image,
        candidates,
        grid,
        background_image,
    )
    direct_detections = [_to_detection(item, "direct", options) for item in candidates]
    inferred_detections = [_to_detection(item, "inferred", options) for item in inferred]
    native_candidate_failures: list[dict[str, Any]] = []
    native_candidate_failure_count = 0
    chosen_root_tree_count = len(candidates)
    if options.output_mode == "native" and (
        direct_detections or _isolate_native_candidates
    ):
        try:
            native_contours, native_hierarchy = extract_foreground_contours(
                chosen_mask,
                maximum_contours=MAX_TRACE_CONTOURS,
            )
        except (ForegroundContourLimitError, ValueError) as exc:
            raise ValueError(
                "The selected trace result has no bounded contour hierarchy"
            ) from exc
        native_trees = foreground_contour_trees(
            native_contours,
            native_hierarchy,
            chosen_mask.shape,
        )
        chosen_root_tree_count = len(native_trees)
        retained_candidates: list[dict[str, Any]] = []
        retained_detections: list[TraceDetection] = []
        for candidate, detection in zip(
            candidates,
            direct_detections,
            strict=True,
        ):
            _check_trace_cancelled()
            tree: ForegroundContourTree | None = None
            try:
                tree = _trace_candidate_tree(candidate, native_contours, native_trees)
                _fit_trace_candidate_native(
                    image,
                    detection,
                    options,
                    work_area,
                    output_work_area,
                    pixels_per_mm,
                    native_contours,
                    tree,
                )
            except RasterVectorizationError as exc:
                if not _isolate_native_candidates:
                    raise
                native_candidate_failure_count += 1
                if len(native_candidate_failures) < 64:
                    native_candidate_failures.append(
                        {
                            "root_index": None if tree is None else tree.root_index,
                            "contour_count": (
                                0 if tree is None else len(tree.contour_indices)
                            ),
                            "reason": _concise_auto_reason(exc),
                        }
                    )
                continue
            retained_candidates.append(candidate)
            retained_detections.append(detection)
        if _isolate_native_candidates:
            candidates = retained_candidates
            direct_detections = retained_detections
        for detection in inferred_detections:
            detection.diagnostics["native_fit_status"] = "inferred_geometry"
    hard_roi_boundary_rejected_count = 0
    if camera_eligibility is not None and direct_detections:
        component_labels, boundary_component_labels = (
            _hard_roi_boundary_component_labels(
                chosen_mask,
                camera_eligibility.hard_roi_mask,
            )
        )
        retained = [
            (candidate, detection)
            for candidate, detection in zip(
                candidates,
                direct_detections,
                strict=True,
            )
            if not _detection_uses_component_labels(
                detection,
                component_labels,
                boundary_component_labels,
                work_area,
                pixels_per_mm,
            )
        ]
        hard_roi_boundary_rejected_count = len(direct_detections) - len(
            retained
        )
        candidates = [candidate for candidate, _detection in retained]
        direct_detections = [detection for _candidate, detection in retained]
    polygon_rejected_count = 0
    if output_polygon is not None and direct_detections:
        retained = [
            (candidate, detection)
            for candidate, detection in zip(
                candidates,
                direct_detections,
                strict=True,
            )
            if _detection_within_output_polygon(detection, output_polygon)
        ]
        polygon_rejected_count = len(direct_detections) - len(retained)
        candidates = [candidate for candidate, _detection in retained]
        direct_detections = [detection for _candidate, detection in retained]
    detections = [*direct_detections, *inferred_detections]
    detections.sort(
        key=lambda item: (
            (
                0,
                int(item.diagnostics["grid_row"]),
                int(item.diagnostics["grid_column"]),
            )
            if "grid_row" in item.diagnostics
            else (1, -float(item.center_mm[1]), float(item.center_mm[0]))
        )
    )
    for index, detection in enumerate(detections, 1):
        if index % 128 == 0:
            _check_trace_cancelled()
        detection.index = index

    direct_count = sum(item.source == "direct" for item in detections)
    inferred_count = sum(item.source == "inferred" for item in detections)
    selected_count = sum(item.selected_default for item in detections)
    message = (
        f"Found {direct_count} direct object{'s' if direct_count != 1 else ''}"
    )
    if inferred_count:
        message += (
            f" and inferred {inferred_count} missing grid "
            f"position{'s' if inferred_count != 1 else ''}"
        )
    if grid and grid.get("normalized"):
        if grid.get("cells_snapped"):
            message += (
                f"; fitted identical {int(grid['columns'])} × "
                f"{int(grid['rows'])} grid cells"
            )
        else:
            message += (
                f"; fitted shared dimensions across a "
                f"{int(grid['columns'])} × {int(grid['rows'])} grid"
            )
    if grid and int(grid.get("repaired_edges", 0)):
        repaired_edges = int(grid["repaired_edges"])
        repaired_cells = int(grid.get("repaired_cells", 0))
        message += (
            f"; repaired {repaired_edges} weak grid edge"
            f"{'s' if repaired_edges != 1 else ''} across {repaired_cells} cell"
            f"{'s' if repaired_cells != 1 else ''}"
        )
    message += f"; {selected_count} selected by confidence"
    outside_count = sum(
        not bool(item.diagnostics.get("within_work_area", True))
        for item in detections
    )
    if outside_count:
        outline_name = "cell" if grid is not None else "outline"
        maximum_by_side = {
            side: max(
                (
                    float(
                        item.diagnostics.get("work_area_overruns_mm", {}).get(
                            side,
                            0.0,
                        )
                    )
                    for item in detections
                ),
                default=0.0,
            )
            for side in ("left", "right", "bottom", "top")
        }
        side_summary = "; ".join(
            f"{side} by {amount:.2f} mm"
            for side, amount in maximum_by_side.items()
            if amount > 1e-9
        )
        guarded_output_area = any(
            abs(float(left) - float(right)) > 1e-9
            for left, right in zip(
                (
                    output_work_area.x_min,
                    output_work_area.x_max,
                    output_work_area.y_min,
                    output_work_area.y_max,
                ),
                (
                    work_area.x_min,
                    work_area.x_max,
                    work_area.y_min,
                    work_area.y_max,
                ),
                strict=True,
            )
        )
        boundary_name = (
            "guarded output area" if guarded_output_area else "work area"
        )
        message += (
            f"; WARNING: {outside_count} {outline_name}"
            f"{'s extend' if outside_count != 1 else ' extends'} outside the "
            f"{boundary_name} ({side_summary}) and "
            f"{'were' if outside_count != 1 else 'was'} not preselected"
        )
    edge_cropped_count = sum(
        item.source == "direct"
        and bool(item.diagnostics.get("touches_image_edge", False))
        for item in detections
    )
    if edge_cropped_count:
        edge_sides = sorted(
            {
                str(side)
                for item in detections
                if item.source == "direct"
                for side in item.diagnostics.get("image_edge_sides", [])
            }
        )
        edge_summary = "/".join(edge_sides) or "camera"
        message += (
            f"; WARNING: {edge_cropped_count} observed outline"
            f"{'s touch' if edge_cropped_count != 1 else ' touches'} the "
            f"{edge_summary} camera/work-area image edge, may be cropped, and "
            f"{'were' if edge_cropped_count != 1 else 'was'} not preselected"
        )
    damaged_count = sum(
        bool(item.diagnostics.get("damage_suspected", False)) for item in detections
    )
    open_count = sum(
        bool(item.diagnostics.get("likely_open_cell", False)) for item in detections
    )
    if damaged_count:
        message += (
            f"; {damaged_count} damaged/suspicious cell"
            f"{'s were' if damaged_count != 1 else ' was'} left unchecked"
        )
    if open_count:
        message += (
            f"; {open_count} likely already-cut/open cell"
            f"{'s were' if open_count != 1 else ' was'} left unchecked"
        )
    if not detections:
        message = "No objects passed the current filters"

    foreground_fraction, border_foreground_fraction = _mask_occupancy(
        chosen_mask,
        (
            None
            if camera_eligibility is None
            else camera_eligibility.material_eligible_mask
        ),
    )
    unavailable_candidate_count = max(
        native_candidate_failure_count,
        chosen_root_tree_count - direct_count,
    )
    review_filtered_candidate_count = max(
        0,
        unavailable_candidate_count - native_candidate_failure_count,
    )
    strategy_metrics = {
        "foreground_fraction": foreground_fraction,
        "border_foreground_fraction": border_foreground_fraction,
        "root_tree_count": chosen_root_tree_count,
        "valid_candidate_count": direct_count,
        "verified_candidate_count": sum(
            item.native_verified for item in direct_detections
        ),
        "invalid_candidate_count": unavailable_candidate_count,
        "review_filtered_candidate_count": review_filtered_candidate_count,
        "output_polygon_rejected_candidate_count": polygon_rejected_count,
        "hard_roi_boundary_rejected_candidate_count": (
            hard_roi_boundary_rejected_count
        ),
        "valid_area_mm2": sum(item.area_mm2 for item in direct_detections),
        "microscopic_candidate_count": sum(
            item.area_mm2 < 4.0 * options.min_area_mm2
            for item in direct_detections
        ),
        "within_frame_candidate_count": sum(
            bool(item.diagnostics.get("within_work_area", False))
            and not bool(item.diagnostics.get("touches_image_edge", False))
            for item in direct_detections
        ),
        "minimum_area_mm2": options.min_area_mm2,
        "frame_area_mm2": (
            image.shape[1] / pixels_per_mm * image.shape[0] / pixels_per_mm
            if camera_eligibility is None
            else float(np.count_nonzero(camera_eligibility.material_eligible_mask))
            / pixels_per_mm**2
        ),
    }
    _check_trace_cancelled()
    return TraceResult(
        bool(detections), detections, mode_used, target_hue,
        image.shape[1], image.shape[0], direct_count, inferred_count,
        grid, message, options,
        camera_work_area_payload,
        output_work_area_payload,
        {
            "candidate_failures": native_candidate_failures,
            "candidate_failure_count": native_candidate_failure_count,
            "strategy_metrics": strategy_metrics,
            "camera_eligibility": (
                None
                if camera_eligibility is None
                else camera_eligibility.diagnostics.to_dict()
            ),
            "timing": {
                "trace_eligibility": (
                    {} if eligibility_timing is None else eligibility_timing.snapshot()
                )
            },
        },
    )


def detect_objects(
    image: np.ndarray,
    options: TraceOptions | Mapping[str, Any] | None,
    work_area: WorkArea,
    pixels_per_mm: float,
    *,
    output_work_area: WorkArea | None = None,
    background_image: np.ndarray | None = None,
    trace_roi_polygons_mm: Sequence[Sequence[Sequence[float]]] | None = None,
    trace_output_polygon_mm: Sequence[Sequence[float]] | None = None,
    trace_roi_source: str = "guarded output area",
    reference_required: bool = False,
    reference_identity: str | None = None,
    mask_override: np.ndarray | None = None,
    raster_preview_callback: CameraTraceRasterPreviewCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    _isolate_native_candidates: bool = False,
    _camera_normalization: CameraRasterNormalizationResult | None = None,
    _camera_eligibility: CameraTraceEligibilityResult | None = None,
    _camera_eligibility_timing: CameraTraceEligibilityTiming | None = None,
) -> TraceResult:
    """Detect camera objects, optionally observing cooperative cancellation."""

    if cancel_check is not None and not callable(cancel_check):
        raise TypeError("cancel_check must be callable or None")
    inherited = _TRACE_CANCEL_CHECK.get()
    cancel_token = _TRACE_CANCEL_CHECK.set(
        inherited if cancel_check is None else cancel_check
    )
    try:
        return _detect_objects(
            image,
            options,
            work_area,
            pixels_per_mm,
            output_work_area=output_work_area,
            background_image=background_image,
            trace_roi_polygons_mm=trace_roi_polygons_mm,
            trace_output_polygon_mm=trace_output_polygon_mm,
            trace_roi_source=trace_roi_source,
            reference_required=reference_required,
            reference_identity=reference_identity,
            mask_override=mask_override,
            raster_preview_callback=raster_preview_callback,
            _isolate_native_candidates=_isolate_native_candidates,
            _camera_normalization=_camera_normalization,
            _camera_eligibility=_camera_eligibility,
            _camera_eligibility_timing=_camera_eligibility_timing,
        )
    finally:
        _TRACE_CANCEL_CHECK.reset(cancel_token)
