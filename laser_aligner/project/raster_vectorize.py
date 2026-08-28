from __future__ import annotations

import hashlib
import math
import struct
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from numbers import Real
from pathlib import Path
from typing import Any, TypeAlias, TypeVar

import cv2
import numpy as np

from .path_geometry import (
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    flatten_native_path,
    native_path_bounds,
    reverse_subpath,
)
from .raster_asset import (
    RasterAssetIdentity,
    RasterAssetPayload,
    verify_raster_asset_identity,
)

RASTER_VECTORIZATION_OVERSAMPLE_FACTOR = 4
MAX_RASTER_VECTORIZATION_OVERSAMPLED_PIXELS = 64 * 1024 * 1024
MAX_RASTER_VECTORIZATION_CONNECTED_COMPONENTS = 4_096
MAX_RASTER_VECTORIZATION_CONTOURS = 8_192
MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION = 1_000_000
MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS = 100_000
MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION = 250_000
MAX_RASTER_VECTORIZATION_FIT_VALIDATION_STEPS = 5_000_000

_CORNER_MINIMUM_TURN_DEGREES = 35.0
_CORNER_INNER_MINIMUM_TURN_DEGREES = 22.5
_CORNER_MINIMUM_SCALE_AGREEMENT = 0.60
_STRAIGHT_MAXIMUM_TURN_DEGREES = 6.0
_STRAIGHT_LOCAL_MAXIMUM_TURN_DEGREES = 12.0
_STRAIGHT_MAXIMUM_DIRECTION_RANGE_DEGREES = 24.0
_STRAIGHT_QUANTIZATION_ALLOWANCE_STEPS = 1.10
_STRAIGHT_LOCAL_QUANTIZATION_ALLOWANCE_STEPS = 1.50
_STRAIGHT_MINIMUM_TOLERANCE_MULTIPLE = 4.0
_STRAIGHT_MINIMUM_SOURCE_PIXEL_MULTIPLE = 4.0
_STRAIGHT_MINIMUM_OVERSAMPLED_STEP_MULTIPLE = 12.0
_STRAIGHT_MINIMUM_PERIMETER_FRACTION = 1.0 / 16.0
_MAX_CORNER_WINDOW_POINTS = 12
_MAX_SMOOTHING_RADIUS_POINTS = 64
_MAX_FIT_TANGENT_WINDOW_POINTS = 12
_LINE_MAXIMUM_TANGENT_DEVIATION_DEGREES = 2.0
_CUBIC_DISTRIBUTION_MINIMUM_TOLERANCE_MULTIPLE = 4.0
_CUBIC_DISTRIBUTION_RMS_TRIGGER_TOLERANCE_FRACTION = 0.45
_CUBIC_DISTRIBUTION_BIAS_TRIGGER_TOLERANCE_FRACTION = 0.04
_CUBIC_DISTRIBUTION_ONE_SIDED_TRIGGER_FRACTION = 0.67
_CUBIC_DISTRIBUTION_MAX_REPARAMETERIZATIONS = 3
_SOURCE_EDGE_NORMAL_WINDOW_SOURCE_PIXELS = 1.25
_SOURCE_EDGE_PROFILE_RADIUS_SOURCE_PIXELS = 1.25
_SOURCE_EDGE_PROFILE_STEP_SOURCE_PIXELS = 0.125
_SOURCE_EDGE_PROFILE_CHUNK_SIZE = 8_192
_SOURCE_EDGE_MAXIMUM_DISPLACEMENT_SOURCE_PIXELS = 0.60
_SOURCE_EDGE_MINIMUM_ENDPOINT_MARGIN = 8.0
_SOURCE_EDGE_MINIMUM_CONTRAST = 32.0
_SOURCE_EDGE_MINIMUM_SLOPE_PER_SOURCE_PIXEL = 24.0
_SOURCE_EDGE_MAXIMUM_REVERSE_VARIATION_FRACTION = 0.20
_MAX_REPARAMETERIZATION_ITERATIONS = 8
_MAX_FIT_RECURSION = 18
_MAX_FIT_VALIDATION_RECURSION = 18
_MAX_FLATTEN_RECURSION = 18
_NATIVE_FRAME_EPSILON_MM = 1e-9
_NATIVE_TOPOLOGY_INITIAL_TOLERANCE_MM = 0.025
_NATIVE_TOPOLOGY_MIN_TOLERANCE_MM = 0.001
_NATIVE_TOPOLOGY_MAX_SEGMENT_PAIR_CHECKS = 1_000_000

_COMPLEXITY_ADVICE = (
    "Increase the minimum feature size, increase simplification by raising the "
    "native fitting tolerance, adjust the threshold, or use cleaner source artwork."
)

_TIMING_STAGE = ContextVar["RasterVectorizationTiming | None"](
    "raster_vectorization_timing",
    default=None,
)
_TimedResult = TypeVar("_TimedResult")


@dataclass(slots=True)
class RasterVectorizationTiming:
    """Opt-in, non-persistent development timing for vectorization stages."""

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


def _timed_stage(
    stage: str,
) -> Callable[[Callable[..., _TimedResult]], Callable[..., _TimedResult]]:
    """Accumulate inclusive stage time only when a collector is active."""

    def decorate(
        function: Callable[..., _TimedResult],
    ) -> Callable[..., _TimedResult]:
        @wraps(function)
        def measured(*args: Any, **kwargs: Any) -> _TimedResult:
            timing = _TIMING_STAGE.get()
            if timing is None:
                return function(*args, **kwargs)
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                timing.record(stage, time.perf_counter() - started)

        return measured

    return decorate


class RasterVectorizationError(ValueError):
    """Raised when reviewed raster artwork cannot be vectorized safely."""


class RasterVectorizationComplexityError(RasterVectorizationError):
    """Raised when raster-derived geometry exceeds a bounded workload."""


class RasterDetectionMode(str, Enum):
    AUTO_THRESHOLD = "auto_threshold"
    MANUAL_THRESHOLD = "manual_threshold"
    ALPHA = "alpha"


class RasterContourOutput(str, Enum):
    OUTER_ONLY = "outer_only"
    ALL_CONTOURS = "all_contours"


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _bounded_byte(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError(f"{label} must be an integer from 0 through 255")
    return value


@dataclass(frozen=True, slots=True)
class RasterVectorizationOptions:
    detection_mode: RasterDetectionMode = RasterDetectionMode.AUTO_THRESHOLD
    threshold: int = 128
    invert: bool = False
    alpha_cutoff: int = 1
    minimum_feature_area_mm2: float = 0.05
    smoothing_mm: float = 0.0
    simplification_tolerance_mm: float = 0.10
    contour_output: RasterContourOutput = RasterContourOutput.ALL_CONTOURS

    def __post_init__(self) -> None:
        try:
            mode = (
                self.detection_mode
                if isinstance(self.detection_mode, RasterDetectionMode)
                else RasterDetectionMode(str(self.detection_mode))
            )
        except ValueError as exc:
            raise ValueError(
                f"Unsupported raster detection mode: {self.detection_mode!r}"
            ) from exc
        try:
            output = (
                self.contour_output
                if isinstance(self.contour_output, RasterContourOutput)
                else RasterContourOutput(str(self.contour_output))
            )
        except ValueError as exc:
            raise ValueError(
                f"Unsupported raster contour output: {self.contour_output!r}"
            ) from exc
        if type(self.invert) is not bool:
            raise ValueError("invert must be a JSON boolean")
        minimum_area = _finite(
            self.minimum_feature_area_mm2,
            "minimum_feature_area_mm2",
        )
        smoothing = _finite(self.smoothing_mm, "smoothing_mm")
        tolerance = _finite(
            self.simplification_tolerance_mm,
            "simplification_tolerance_mm",
        )
        if minimum_area < 0.0:
            raise ValueError("minimum_feature_area_mm2 cannot be negative")
        if smoothing < 0.0:
            raise ValueError("smoothing_mm cannot be negative")
        if tolerance <= 0.0:
            raise ValueError("simplification_tolerance_mm must be positive")
        object.__setattr__(self, "detection_mode", mode)
        object.__setattr__(self, "contour_output", output)
        object.__setattr__(self, "threshold", _bounded_byte(self.threshold, "threshold"))
        object.__setattr__(
            self,
            "alpha_cutoff",
            _bounded_byte(self.alpha_cutoff, "alpha_cutoff"),
        )
        object.__setattr__(self, "minimum_feature_area_mm2", minimum_area)
        object.__setattr__(self, "smoothing_mm", smoothing)
        object.__setattr__(self, "simplification_tolerance_mm", tolerance)


@dataclass(frozen=True, slots=True, eq=False)
class RasterVectorizationSource:
    identity: RasterAssetIdentity
    source_rgba: np.ndarray
    grayscale: np.ndarray
    composited_grayscale: np.ndarray
    alpha: np.ndarray
    has_usable_alpha: bool

    @property
    def width_px(self) -> int:
        return int(self.source_rgba.shape[1])

    @property
    def height_px(self) -> int:
        return int(self.source_rgba.shape[0])


@dataclass(frozen=True, slots=True, eq=False)
class _RasterMaskPreparation:
    threshold_used: int | None
    source_mask: np.ndarray
    cleaned_mask: np.ndarray
    working_mask: np.ndarray
    component_count: int


@dataclass(frozen=True, slots=True, eq=False)
class _RasterTracePreparation:
    source_identity: RasterAssetIdentity
    options: RasterVectorizationOptions
    width_mm: float
    height_mm: float
    masks: _RasterMaskPreparation
    raw_contours: tuple[np.ndarray, ...]
    hierarchy: np.ndarray


@dataclass(frozen=True, slots=True)
class RasterVectorizationQuickContour:
    """Preview-only raster outline that can never become project geometry."""

    points: tuple[tuple[float, float], ...]
    parent_index: int | None
    depth: int
    is_hole: bool

    def __post_init__(self) -> None:
        points = tuple(
            (_finite(point[0], "quick preview x"), _finite(point[1], "quick preview y"))
            for point in self.points
        )
        if len(points) < 3:
            raise ValueError("quick preview contours require at least three points")
        if any(abs(value) > 0.500000001 for point in points for value in point):
            raise ValueError("quick preview contours must stay in the source frame")
        if self.parent_index is not None and (
            type(self.parent_index) is not int or self.parent_index < 0
        ):
            raise ValueError("quick preview parent_index must be non-negative or None")
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("quick preview depth must be a non-negative integer")
        if type(self.is_hole) is not bool:
            raise ValueError("quick preview is_hole must be a boolean")
        object.__setattr__(self, "points", points)


@dataclass(frozen=True, slots=True, eq=False)
class RasterVectorizationQuickPreview:
    """Fast display geometry with no persistence or planning authority."""

    source_identity: RasterAssetIdentity
    source_rgba: np.ndarray
    foreground_mask: np.ndarray
    contours: tuple[RasterVectorizationQuickContour, ...]
    threshold_used: int | None
    has_usable_alpha: bool
    connected_component_count: int
    raw_contour_point_count: int
    preview_point_count: int
    _prepared_trace: _RasterTracePreparation = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        contours = tuple(self.contours)
        if not contours:
            raise ValueError("quick preview requires at least one contour")
        if type(self.has_usable_alpha) is not bool:
            raise ValueError("quick preview has_usable_alpha must be a boolean")
        if self.threshold_used is not None:
            _bounded_byte(self.threshold_used, "quick preview threshold")
        for name in (
            "connected_component_count",
            "raw_contour_point_count",
            "preview_point_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"quick preview {name} must be non-negative")
        if self.preview_point_count != sum(len(contour.points) for contour in contours):
            raise ValueError("quick preview point count does not match its contours")
        if not isinstance(self._prepared_trace, _RasterTracePreparation):
            raise TypeError("quick preview requires its immutable prepared trace")
        object.__setattr__(self, "contours", contours)


@dataclass(frozen=True, slots=True)
class RasterVectorizedContour:
    native_subpath: PathSubpath
    preview_points: tuple[tuple[float, float], ...]
    parent_index: int | None
    depth: int
    is_hole: bool
    raw_point_count: int
    fitted_segment_count: int
    preview_flattened_point_count: int
    max_fitting_error_mm: float
    smoothing_displacement_mm: float
    max_estimated_deviation_mm: float
    mean_fitting_error_mm: float = 0.0
    rms_fitting_error_mm: float = 0.0
    fitting_error_sample_count: int = 0
    hard_corner_count: int = 0
    recursive_split_count: int = 0
    merged_segment_count: int = 0
    longest_smooth_span_segment_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.native_subpath, PathSubpath):
            raise TypeError("native_subpath must be a PathSubpath")
        if not self.native_subpath.closed:
            raise ValueError("A raster-vectorized native subpath must be closed")
        points = tuple(
            (
                _finite(point[0], "contour point x"),
                _finite(point[1], "contour point y"),
            )
            for point in self.preview_points
        )
        if len(points) < 3:
            raise ValueError(
                "A vectorized contour preview requires at least three points"
            )
        if any(abs(value) > 0.500000001 for point in points for value in point):
            raise ValueError(
                "Vectorized contour preview points must remain in the image frame"
            )
        if self.parent_index is not None and (
            type(self.parent_index) is not int or self.parent_index < 0
        ):
            raise ValueError("contour parent_index must be null or non-negative")
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("contour depth must be a non-negative integer")
        if type(self.is_hole) is not bool or self.is_hole != bool(self.depth % 2):
            raise ValueError("contour hole state must match its hierarchy depth")
        for value, label, minimum in (
            (self.raw_point_count, "raw_point_count", 3),
            (self.fitted_segment_count, "fitted_segment_count", 1),
            (
                self.preview_flattened_point_count,
                "preview_flattened_point_count",
                3,
            ),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"contour {label} must be an integer >= {minimum}")
        if self.fitted_segment_count != len(self.native_subpath.segments):
            raise ValueError(
                "contour fitted_segment_count must match its native subpath"
            )
        if self.preview_flattened_point_count != len(points):
            raise ValueError(
                "contour preview_flattened_point_count must match its preview data"
            )
        fitting_error = _finite(
            self.max_fitting_error_mm,
            "contour max_fitting_error_mm",
        )
        mean_fitting_error = _finite(
            self.mean_fitting_error_mm,
            "contour mean_fitting_error_mm",
        )
        rms_fitting_error = _finite(
            self.rms_fitting_error_mm,
            "contour rms_fitting_error_mm",
        )
        smoothing_displacement = _finite(
            self.smoothing_displacement_mm,
            "contour smoothing_displacement_mm",
        )
        deviation = _finite(
            self.max_estimated_deviation_mm,
            "contour max_estimated_deviation_mm",
        )
        if min(fitting_error, mean_fitting_error, rms_fitting_error) < 0.0:
            raise ValueError("contour fitting errors cannot be negative")
        if mean_fitting_error > rms_fitting_error + 1e-12:
            raise ValueError("contour mean fitting error cannot exceed RMS error")
        if rms_fitting_error > fitting_error + 1e-12:
            raise ValueError("contour RMS fitting error cannot exceed maximum error")
        if smoothing_displacement < 0.0:
            raise ValueError("contour smoothing displacement cannot be negative")
        if deviation < max(fitting_error, smoothing_displacement):
            raise ValueError(
                "contour estimated deviation cannot be smaller than its components"
            )
        object.__setattr__(self, "preview_points", points)
        object.__setattr__(self, "max_fitting_error_mm", fitting_error)
        object.__setattr__(self, "mean_fitting_error_mm", mean_fitting_error)
        object.__setattr__(self, "rms_fitting_error_mm", rms_fitting_error)
        for field_name in (
            "fitting_error_sample_count",
            "hard_corner_count",
            "recursive_split_count",
            "merged_segment_count",
            "longest_smooth_span_segment_count",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"contour {field_name} must be a non-negative integer")
        object.__setattr__(
            self,
            "smoothing_displacement_mm",
            smoothing_displacement,
        )
        object.__setattr__(self, "max_estimated_deviation_mm", deviation)

    @property
    def points(self) -> tuple[tuple[float, float], ...]:
        """Compatibility view of the ephemeral preview-flattened contour."""

        return self.preview_points

    @property
    def final_point_count(self) -> int:
        """Compatibility count for callers predating native path persistence."""

        return self.preview_flattened_point_count


@dataclass(frozen=True, slots=True, eq=False)
class RasterVectorizationResult:
    source_identity: RasterAssetIdentity
    source_sha256: str
    source_rgba: np.ndarray
    foreground_mask: np.ndarray
    overlay_rgba: np.ndarray
    contours: tuple[RasterVectorizedContour, ...]
    threshold_used: int | None
    has_usable_alpha: bool
    connected_component_count: int
    raw_contour_point_count: int
    fitted_segment_count: int
    preview_flattened_point_count: int
    max_estimated_deviation_mm: float

    def __post_init__(self) -> None:
        if not isinstance(self.source_identity, RasterAssetIdentity):
            raise TypeError("source_identity must be a RasterAssetIdentity")
        if self.source_sha256 != self.source_identity.sha256:
            raise ValueError("Vectorization result SHA-256 must match its source identity")
        arrays = (
            (self.source_rgba, "source_rgba", 3),
            (self.foreground_mask, "foreground_mask", 2),
            (self.overlay_rgba, "overlay_rgba", 3),
        )
        for values, label, dimensions in arrays:
            if not isinstance(values, np.ndarray):
                raise TypeError(f"{label} must be a numpy array")
            if values.dtype != np.uint8 or values.ndim != dimensions:
                raise ValueError(f"{label} must be an {dimensions}D uint8 array")
            if values.flags.writeable:
                raise ValueError(f"{label} must be read-only")
        if self.source_rgba.shape[2] != 4 or self.overlay_rgba.shape[2] != 4:
            raise ValueError("Raster result source and overlay must be RGBA")
        if self.foreground_mask.shape != self.source_rgba.shape[:2]:
            raise ValueError("Raster result mask dimensions must match its source")
        if self.overlay_rgba.shape != self.source_rgba.shape:
            raise ValueError("Raster result overlay dimensions must match its source")
        contours = tuple(self.contours)
        if not contours or any(
            not isinstance(contour, RasterVectorizedContour)
            for contour in contours
        ):
            raise ValueError("Raster vectorization requires validated contours")
        if self.threshold_used is not None:
            _bounded_byte(self.threshold_used, "threshold_used")
        if type(self.has_usable_alpha) is not bool:
            raise ValueError("has_usable_alpha must be a boolean")
        for value, label, minimum in (
            (self.connected_component_count, "connected_component_count", 1),
            (self.raw_contour_point_count, "raw_contour_point_count", 3),
            (self.fitted_segment_count, "fitted_segment_count", 1),
            (
                self.preview_flattened_point_count,
                "preview_flattened_point_count",
                3,
            ),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{label} must be an integer >= {minimum}")
        expected_counts = (
            sum(contour.raw_point_count for contour in contours),
            sum(contour.fitted_segment_count for contour in contours),
            sum(contour.preview_flattened_point_count for contour in contours),
        )
        if expected_counts != (
            self.raw_contour_point_count,
            self.fitted_segment_count,
            self.preview_flattened_point_count,
        ):
            raise ValueError("Raster vectorization result counts are inconsistent")
        deviation = _finite(
            self.max_estimated_deviation_mm,
            "max_estimated_deviation_mm",
        )
        if deviation < 0.0 or not math.isclose(
            deviation,
            max(contour.max_estimated_deviation_mm for contour in contours),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("Raster vectorization result deviation is inconsistent")
        object.__setattr__(self, "contours", contours)
        object.__setattr__(self, "max_estimated_deviation_mm", deviation)

    @property
    def final_point_count(self) -> int:
        """Compatibility count for callers predating native path persistence."""

        return self.preview_flattened_point_count

    def project_path_geometry(self) -> NativePathGeometry:
        """Return the canonical immutable compound native project path."""

        return NativePathGeometry(
            subpaths=tuple(contour.native_subpath for contour in self.contours),
            fill_rule=PathFillRule.EVENODD,
        )

    def metadata(self) -> dict[str, object]:
        """Return a compact JSON-ready provenance and quality summary."""

        fitting_sample_count = sum(
            contour.fitting_error_sample_count for contour in self.contours
        )
        mean_fitting_error = (
            sum(
                contour.mean_fitting_error_mm * contour.fitting_error_sample_count
                for contour in self.contours
            )
            / fitting_sample_count
            if fitting_sample_count
            else 0.0
        )
        rms_fitting_error = (
            math.sqrt(
                sum(
                    contour.rms_fitting_error_mm**2
                    * contour.fitting_error_sample_count
                    for contour in self.contours
                )
                / fitting_sample_count
            )
            if fitting_sample_count
            else 0.0
        )
        return {
            "raster_vectorization_source_sha256": self.source_sha256,
            "raster_vectorization_threshold": self.threshold_used,
            "raster_vectorization_has_usable_alpha": self.has_usable_alpha,
            "raster_vectorization_connected_components": (
                self.connected_component_count
            ),
            "raster_vectorization_contours": len(self.contours),
            "raster_vectorization_raw_contour_points": (
                self.raw_contour_point_count
            ),
            "raster_vectorization_fitted_segments": self.fitted_segment_count,
            "raster_vectorization_preview_flattened_points": (
                self.preview_flattened_point_count
            ),
            "raster_vectorization_max_fitting_error_mm": max(
                contour.max_fitting_error_mm for contour in self.contours
            ),
            "raster_vectorization_mean_fitting_error_mm": mean_fitting_error,
            "raster_vectorization_rms_fitting_error_mm": rms_fitting_error,
            "raster_vectorization_fitting_error_samples": fitting_sample_count,
            "raster_vectorization_max_rms_fitting_error_mm": max(
                contour.rms_fitting_error_mm for contour in self.contours
            ),
            "raster_vectorization_detected_hard_corners": sum(
                contour.hard_corner_count for contour in self.contours
            ),
            "raster_vectorization_recursive_splits": sum(
                contour.recursive_split_count for contour in self.contours
            ),
            "raster_vectorization_merged_segments": sum(
                contour.merged_segment_count for contour in self.contours
            ),
            "raster_vectorization_longest_smooth_span_segments": max(
                contour.longest_smooth_span_segment_count for contour in self.contours
            ),
            "raster_vectorization_max_smoothing_displacement_mm": max(
                contour.smoothing_displacement_mm for contour in self.contours
            ),
            "raster_vectorization_max_estimated_deviation_mm": (
                self.max_estimated_deviation_mm
            ),
            "raster_vectorization_hierarchy": [
                {
                    "parent_index": contour.parent_index,
                    "depth": contour.depth,
                    "is_hole": contour.is_hole,
                }
                for contour in self.contours
            ],
        }


@dataclass(frozen=True, slots=True)
class _LineSegment:
    start: np.ndarray
    end: np.ndarray
    fitting_error_mm: float


@dataclass(frozen=True, slots=True)
class _CubicSegment:
    start: np.ndarray
    control_1: np.ndarray
    control_2: np.ndarray
    end: np.ndarray
    fitting_error_mm: float


_FittedSegment: TypeAlias = _LineSegment | _CubicSegment


@dataclass(frozen=True, slots=True)
class _FittedContour:
    segments: tuple[_FittedSegment, ...]
    smoothing_displacement_mm: float
    max_fitting_error_mm: float
    mean_fitting_error_mm: float = 0.0
    rms_fitting_error_mm: float = 0.0
    fitting_error_sample_count: int = 0
    hard_corner_count: int = 0
    recursive_split_count: int = 0
    merged_segment_count: int = 0
    longest_smooth_span_segment_count: int = 0


@dataclass(frozen=True, slots=True)
class _FitValidation:
    accepted: bool
    max_error_mm: float
    split_index: int
    sample_error_sum_mm: float
    sample_squared_error_sum_mm2: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class _FittedPiece:
    segment: _FittedSegment
    target_points: np.ndarray
    target_parameters: np.ndarray
    start_tangent: np.ndarray
    end_tangent: np.ndarray
    hard_start: bool
    hard_end: bool
    sample_error_sum_mm: float
    sample_squared_error_sum_mm2: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class _StraightRun:
    start_index: int
    end_index: int
    length_mm: float
    max_orthogonal_residual_mm: float
    maximum_directional_turn_degrees: float
    directional_range_degrees: float


@dataclass(frozen=True, slots=True, eq=False)
class _SourceEdgeRefinement:
    points: np.ndarray
    signed_displacements_mm: np.ndarray
    eligible: np.ndarray
    protected: np.ndarray

    @property
    def maximum_displacement_mm(self) -> float:
        if not len(self.signed_displacements_mm):
            return 0.0
        return float(np.max(np.abs(self.signed_displacements_mm)))


def _unchanged_source_edge(points: np.ndarray) -> _SourceEdgeRefinement:
    values = np.asarray(points, dtype=np.float64)
    return _SourceEdgeRefinement(
        points=values.copy(),
        signed_displacements_mm=np.zeros(len(values), dtype=np.float64),
        eligible=np.zeros(len(values), dtype=bool),
        protected=np.ones(len(values), dtype=bool),
    )


@dataclass(slots=True)
class _ComplexityBudget:
    fitted_segments: int = 0
    preview_points: int = 0
    fit_validation_steps: int = 0
    recursive_splits: int = 0
    merged_segments: int = 0

    def add_fitted_segments(self, count: int = 1) -> None:
        if self.fitted_segments + count > MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS:
            _raise_complexity(
                "Raster vectorization requires more than "
                f"{MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS:,} fitted segments"
            )
        self.fitted_segments += count

    def consume_fit_validation_step(self, count: int = 1) -> None:
        if (
            self.fit_validation_steps + count
            > MAX_RASTER_VECTORIZATION_FIT_VALIDATION_STEPS
        ):
            _raise_complexity(
                "Raster vectorization requires more than "
                f"{MAX_RASTER_VECTORIZATION_FIT_VALIDATION_STEPS:,} "
                "bounded curve-fit validation steps"
            )
        self.fit_validation_steps += count

    def add_preview_points(self, count: int = 1) -> None:
        if (
            self.preview_points + count
            > MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION
        ):
            _raise_complexity(
                "Raster vectorization requires more than "
                f"{MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION:,} "
                "preview-flattened points"
            )
        self.preview_points += count


def _raise_complexity(message: str) -> None:
    raise RasterVectorizationComplexityError(f"{message}. {_COMPLEXITY_ADVICE}")


def _readonly(array: np.ndarray) -> np.ndarray:
    output = np.ascontiguousarray(array)
    output.setflags(write=False)
    return output


def _validate_payload(payload: RasterAssetPayload) -> None:
    if not isinstance(payload, RasterAssetPayload):
        raise TypeError("payload must be a RasterAssetPayload")
    if type(payload.encoded) is not bytes:
        raise RasterVectorizationError("Raster payload encoded content must be bytes")
    digest = hashlib.sha256(payload.encoded).hexdigest()
    identity = payload.identity
    metadata = payload.metadata
    if digest != identity.sha256.casefold():
        raise RasterVectorizationError(
            "Raster vectorization payload identity does not match its exact encoded bytes"
        )
    if len(payload.encoded) != identity.encoded_bytes:
        raise RasterVectorizationError(
            "Raster vectorization payload length does not match its identity"
        )
    if (
        metadata.path != identity.path
        or metadata.format != identity.format
        or metadata.width != identity.width
        or metadata.height != identity.height
        or metadata.encoded_bytes != identity.encoded_bytes
    ):
        raise RasterVectorizationError(
            "Raster vectorization metadata and identity describe different sources"
        )
    try:
        verify_raster_asset_identity(identity)
    except ValueError as exc:
        raise RasterVectorizationError(
            "Raster vectorization source identity mismatch; reopen the image and try again"
        ) from exc


def _png_transparent_gray(encoded: bytes, source: Path) -> int | None:
    if len(encoded) < 29 or not encoded.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    bit_depth = int(encoded[24])
    if int(encoded[25]) != 0:
        return None
    offset = 8
    while offset + 12 <= len(encoded):
        length = int(struct.unpack_from(">I", encoded, offset)[0])
        chunk_end = offset + 12 + length
        if chunk_end > len(encoded):
            raise RasterVectorizationError(
                f"Raster PNG contains a truncated chunk: {source}"
            )
        kind = encoded[offset + 4 : offset + 8]
        data = encoded[offset + 8 : offset + 8 + length]
        if kind == b"tRNS":
            if length != 2:
                raise RasterVectorizationError(
                    f"Raster grayscale PNG has an invalid tRNS chunk: {source}"
                )
            sample = int(struct.unpack(">H", data)[0])
            maximum = (1 << bit_depth) - 1
            if sample > maximum:
                raise RasterVectorizationError(
                    f"Raster grayscale PNG tRNS sample exceeds its bit depth: {source}"
                )
            return int(round(sample * 255.0 / maximum))
        if kind in {b"IDAT", b"IEND"}:
            return None
        offset = chunk_end
    return None


def _decode_payload(payload: RasterAssetPayload) -> RasterVectorizationSource:
    metadata = payload.metadata
    source = Path(metadata.path)
    flags = cv2.IMREAD_COLOR if metadata.format == "jpeg" else cv2.IMREAD_UNCHANGED
    image = cv2.imdecode(np.frombuffer(payload.encoded, dtype=np.uint8), flags)
    if image is None or image.size == 0:
        raise RasterVectorizationError(
            f"Raster image asset could not be decoded: {source}"
        )
    if image.dtype != np.uint8:
        raise RasterVectorizationError(
            f"Raster image must decode to 8-bit pixels: {source}"
        )
    decoded_height, decoded_width = image.shape[:2]
    if (decoded_width, decoded_height) != (metadata.width, metadata.height):
        raise RasterVectorizationError(
            "Raster image dimensions do not match the bounded header metadata: "
            f"{source}"
        )

    if image.ndim == 2:
        rgb = np.repeat(image[:, :, None], 3, axis=2)
        alpha = np.full(image.shape, 255, dtype=np.uint8)
        transparent_gray = _png_transparent_gray(payload.encoded, source)
        if transparent_gray is not None:
            alpha[image == transparent_gray] = 0
    elif image.ndim == 3 and image.shape[2] == 2:
        rgb = np.repeat(image[:, :, :1], 3, axis=2)
        alpha = image[:, :, 1].copy()
    elif image.ndim == 3 and image.shape[2] == 3:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        alpha = np.full(image.shape[:2], 255, dtype=np.uint8)
    elif image.ndim == 3 and image.shape[2] == 4:
        rgb = cv2.cvtColor(image[:, :, :3], cv2.COLOR_BGR2RGB)
        alpha = image[:, :, 3].copy()
    else:
        raise RasterVectorizationError(
            f"Raster image has an unsupported channel layout: {source}"
        )

    rgba = np.dstack((rgb, alpha)).astype(np.uint8, copy=False)
    grayscale = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    opacity = alpha.astype(np.float32) / 255.0
    composited_grayscale = np.rint(
        grayscale.astype(np.float32) * opacity + 255.0 * (1.0 - opacity)
    ).astype(np.uint8)
    alpha_min = int(np.min(alpha))
    alpha_max = int(np.max(alpha))
    has_usable_alpha = alpha_min < alpha_max and alpha_min < 255 and alpha_max > 0
    return RasterVectorizationSource(
        identity=payload.identity,
        source_rgba=_readonly(rgba),
        grayscale=_readonly(grayscale),
        composited_grayscale=_readonly(composited_grayscale),
        alpha=_readonly(alpha),
        has_usable_alpha=has_usable_alpha,
    )


@_timed_stage("image_decode_preparation")
def _prepare_raster_vectorization_source(
    payload: RasterAssetPayload,
) -> RasterVectorizationSource:
    _validate_payload(payload)
    return _decode_payload(payload)


def prepare_raster_vectorization_source(
    payload: RasterAssetPayload,
    *,
    timing: RasterVectorizationTiming | None = None,
) -> RasterVectorizationSource:
    """Verify and decode one exact bounded payload for repeated preview work."""

    token = _TIMING_STAGE.set(timing)
    try:
        return _prepare_raster_vectorization_source(payload)
    finally:
        _TIMING_STAGE.reset(token)


def raster_payload_has_usable_alpha(payload: RasterAssetPayload) -> bool:
    """Return whether exact reviewed pixels contain spatially useful alpha data."""

    return prepare_raster_vectorization_source(payload).has_usable_alpha


def _display_dimensions(width: object, height: object) -> tuple[float, float]:
    width_mm = _finite(width, "displayed_width_mm")
    height_mm = _finite(height, "displayed_height_mm")
    if width_mm <= 0.0 or height_mm <= 0.0:
        raise ValueError("Displayed raster width and height must be positive")
    return width_mm, height_mm


def _composited_grayscale(source: RasterVectorizationSource) -> np.ndarray:
    return source.composited_grayscale


def _threshold_value(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    grayscale: np.ndarray,
) -> int | None:
    if options.detection_mode is RasterDetectionMode.ALPHA:
        if not source.has_usable_alpha:
            raise RasterVectorizationError(
                "Transparency / alpha tracing is unavailable because the source has "
                "no spatially useful alpha data"
            )
        return None
    if options.detection_mode is RasterDetectionMode.MANUAL_THRESHOLD:
        return options.threshold
    # Otsu must see the white-composited transparent background as well as the
    # opaque artwork.  Sampling only alpha-eligible pixels makes a common
    # transparent silhouette appear single-valued, which yields threshold zero
    # and incorrectly removes every non-black foreground pixel.  Alpha remains
    # an independent gate when the mask is built below.
    if not np.any(source.alpha >= options.alpha_cutoff):
        raise RasterVectorizationError(
            "The alpha cutoff excludes every source pixel"
        )
    value, _mask = cv2.threshold(
        grayscale,
        0,
        255,
        cv2.THRESH_BINARY | cv2.THRESH_OTSU,
    )
    return int(round(float(value)))


def _mask_at_resolution(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    grayscale: np.ndarray,
    threshold_used: int | None,
    *,
    size: tuple[int, int] | None = None,
) -> np.ndarray:
    width = source.width_px if size is None else int(size[0])
    height = source.height_px if size is None else int(size[1])
    if size is None:
        gray = grayscale
        alpha = source.alpha
    else:
        gray = cv2.resize(grayscale, (width, height), interpolation=cv2.INTER_CUBIC)
        alpha = cv2.resize(
            source.alpha,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        )

    if options.detection_mode is RasterDetectionMode.ALPHA:
        foreground = alpha >= options.alpha_cutoff
        if options.invert:
            foreground = ~foreground
    else:
        assert threshold_used is not None
        foreground = gray <= threshold_used
        if options.invert:
            foreground = ~foreground
        # Transparency remains background for grayscale threshold modes even
        # when light/dark polarity is inverted.
        foreground &= alpha >= options.alpha_cutoff
    return foreground.astype(np.uint8) * 255


def _clean_components(
    mask: np.ndarray,
    minimum_area_mm2: float,
    width_mm: float,
    height_mm: float,
) -> tuple[np.ndarray, int]:
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask,
        connectivity=8,
        ltype=cv2.CV_32S,
    )
    pixel_area_mm2 = width_mm * height_mm / float(mask.shape[0] * mask.shape[1])
    retained_labels = [
        index
        for index in range(1, count)
        if int(stats[index, cv2.CC_STAT_AREA]) * pixel_area_mm2
        >= minimum_area_mm2
    ]
    if len(retained_labels) > MAX_RASTER_VECTORIZATION_CONNECTED_COMPONENTS:
        _raise_complexity(
            "Raster vectorization found "
            f"{len(retained_labels):,} connected foreground components, exceeding "
            f"the {MAX_RASTER_VECTORIZATION_CONNECTED_COMPONENTS:,}-component limit"
        )
    if not retained_labels:
        raise RasterVectorizationError(
            "No foreground features remain at the selected threshold and minimum "
            "feature size"
        )
    retained = np.zeros(count, dtype=np.uint8)
    retained[np.asarray(retained_labels, dtype=np.int64)] = 255
    cleaned = retained[labels]
    if minimum_area_mm2 > 0.0:
        background_count, background_labels, background_stats, _centroids = (
            cv2.connectedComponentsWithStats(
                cv2.bitwise_not(cleaned),
                connectivity=8,
                ltype=cv2.CV_32S,
            )
        )
        border_labels = set(
            int(value)
            for value in np.unique(
                np.concatenate(
                    (
                        background_labels[0],
                        background_labels[-1],
                        background_labels[:, 0],
                        background_labels[:, -1],
                    )
                )
            )
        )
        fill_labels = [
            index
            for index in range(1, background_count)
            if index not in border_labels
            and int(background_stats[index, cv2.CC_STAT_AREA]) * pixel_area_mm2
            < minimum_area_mm2
        ]
        if fill_labels:
            fill = np.zeros(background_count, dtype=bool)
            fill[np.asarray(fill_labels, dtype=np.int64)] = True
            cleaned = cleaned.copy()
            cleaned[fill[background_labels]] = 255
    return cleaned, len(retained_labels)


def _oversampled_mask(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    grayscale: np.ndarray,
    threshold_used: int | None,
    source_mask: np.ndarray,
    cleaned_mask: np.ndarray,
) -> np.ndarray:
    factor = RASTER_VECTORIZATION_OVERSAMPLE_FACTOR
    width = source.width_px * factor
    height = source.height_px * factor
    work_pixels = width * height
    if work_pixels > MAX_RASTER_VECTORIZATION_OVERSAMPLED_PIXELS:
        _raise_complexity(
            "The 4x contour workspace requires "
            f"{work_pixels:,} pixels, exceeding the "
            f"{MAX_RASTER_VECTORIZATION_OVERSAMPLED_PIXELS:,}-pixel internal limit"
        )
    mask = _mask_at_resolution(
        source,
        options,
        grayscale,
        threshold_used,
        size=(width, height),
    )
    # The one-source-pixel halo lets interpolation localize the threshold on
    # both sides of a retained edge. It cannot resurrect a removed isolated
    # component because the thresholded signal is still required as well.
    component_halo = cv2.dilate(
        cleaned_mask,
        np.ones((3, 3), dtype=np.uint8),
    )
    component_gate = cv2.resize(
        component_halo,
        (width, height),
        interpolation=cv2.INTER_NEAREST,
    )
    cv2.bitwise_and(mask, component_gate, dst=mask)
    filled_holes = cv2.bitwise_and(
        cleaned_mask,
        cv2.bitwise_not(source_mask),
    )
    if np.any(filled_holes):
        fill_halo = cv2.dilate(
            filled_holes,
            np.ones((3, 3), dtype=np.uint8),
        )
        fill_gate = cv2.resize(
            fill_halo,
            (width, height),
            interpolation=cv2.INTER_NEAREST,
        )
        cv2.bitwise_or(mask, fill_gate, dst=mask)
    return mask


def _boundary_transition_count(mask: np.ndarray, *, stop_after: int) -> int:
    """Count digital boundary edges with bounded temporary storage."""

    if mask.ndim != 2 or mask.dtype != np.uint8:
        raise TypeError("Raster vectorization masks must be 8-bit single-channel arrays")
    height, width = mask.shape
    total = int(np.count_nonzero(mask[0]))
    if height > 1:
        total += int(np.count_nonzero(mask[-1]))
    total += int(np.count_nonzero(mask[:, 0]))
    if width > 1:
        total += int(np.count_nonzero(mask[:, -1]))
    block_rows = 256
    for start in range(0, height, block_rows):
        end = min(height, start + block_rows)
        block = mask[start:end]
        if width > 1:
            total += int(np.count_nonzero(block[:, 1:] != block[:, :-1]))
        first_row = max(1, start)
        if first_row < end:
            total += int(
                np.count_nonzero(
                    mask[first_row:end] != mask[first_row - 1 : end - 1]
                )
            )
        if total > stop_after:
            return total
    return total


def _preflight_contour_complexity(mask: np.ndarray) -> None:
    """Reject excessive edge work before allocating the 4x trace."""

    base_limit = math.ceil(
        MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION
        / RASTER_VECTORIZATION_OVERSAMPLE_FACTOR
    )
    base_transitions = _boundary_transition_count(mask, stop_after=base_limit)
    projected_points = (
        base_transitions * RASTER_VECTORIZATION_OVERSAMPLE_FACTOR
    )
    if projected_points > MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION:
        _raise_complexity(
            "Raster vectorization base-resolution edges project to at least "
            f"{projected_points:,} points in the 4x contour workspace, exceeding "
            f"the {MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION:,}-point "
            "pre-simplification limit"
        )

    contours, hierarchy = cv2.findContours(
        mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if hierarchy is None or not contours:
        raise RasterVectorizationError(
            "No closed contours were produced by the selected raster settings"
        )
    if len(contours) > MAX_RASTER_VECTORIZATION_CONTOURS:
        _raise_complexity(
            "Raster vectorization found "
            f"{len(contours):,} base-resolution contours, exceeding the "
            f"{MAX_RASTER_VECTORIZATION_CONTOURS:,}-contour limit"
        )


def _enforce_oversampled_edge_budget(mask: np.ndarray) -> None:
    transitions = _boundary_transition_count(
        mask,
        stop_after=MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION,
    )
    if transitions > MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION:
        _raise_complexity(
            "Raster vectorization produced more than "
            f"{MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION:,} boundary "
            "edges in the 4x contour workspace"
        )


def _hierarchy_depth(index: int, parents: np.ndarray) -> int:
    depth = 0
    parent = int(parents[index])
    visited = 0
    while parent >= 0:
        if parent >= len(parents):
            raise RasterVectorizationError(
                "OpenCV returned an out-of-range contour parent"
            )
        depth += 1
        visited += 1
        if visited > len(parents):
            raise RasterVectorizationError("OpenCV returned a cyclic contour hierarchy")
        parent = int(parents[parent])
    return depth


def _signed_area(points: np.ndarray) -> float:
    following = np.roll(points, -1, axis=0)
    return 0.5 * float(
        np.sum(points[:, 0] * following[:, 1] - following[:, 0] * points[:, 1])
    )


def _physical_contour(
    contour: np.ndarray,
    width_px: int,
    height_px: int,
    width_mm: float,
    height_mm: float,
) -> np.ndarray:
    factor = float(RASTER_VECTORIZATION_OVERSAMPLE_FACTOR)
    pixels = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    points = np.empty_like(pixels)
    points[:, 0] = (
        (pixels[:, 0] + 0.5) / (factor * width_px) - 0.5
    ) * width_mm
    points[:, 1] = (
        0.5 - (pixels[:, 1] + 0.5) / (factor * height_px)
    ) * height_mm
    if len(points) > 1:
        keep = np.ones(len(points), dtype=bool)
        keep[1:] = np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-12
        points = points[keep]
    return points


def _minimal_cyclic_rotation_index(points: np.ndarray) -> int:
    """Return the lexicographically least full cyclic rotation in O(n)."""

    count = len(points)
    if count < 2:
        return 0
    first = 0
    second = 1
    offset = 0
    while first < count and second < count and offset < count:
        left = points[(first + offset) % count]
        right = points[(second + offset) % count]
        if left[0] == right[0] and left[1] == right[1]:
            offset += 1
            continue
        left_is_less = bool(
            left[0] < right[0]
            or (left[0] == right[0] and left[1] < right[1])
        )
        if left_is_less:
            second += offset + 1
            if second <= first:
                second = first + 1
        else:
            first += offset + 1
            if first <= second:
                first = second + 1
        offset = 0
    return min(first, second) % count


def _canonicalize_closed_contour(points: np.ndarray) -> np.ndarray:
    """Rotate a closed contour to a coordinate-defined, seam-independent start."""

    values = np.asarray(points, dtype=np.float64)
    if len(values) < 2:
        return values.copy()
    # A coordinate minimum can occur at several disconnected boundary samples.
    # Comparing the complete cyclic sequence makes the tie-break independent of
    # whichever seam OpenCV happened to return.
    start = _minimal_cyclic_rotation_index(values)
    return np.roll(values, -start, axis=0).copy()


def _closed_arc_positions(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    steps = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    positions = np.concatenate(([0.0], np.cumsum(steps[:-1])))
    return positions, steps, float(np.sum(steps))


def _sample_closed_contour(
    points: np.ndarray,
    positions: np.ndarray,
    steps: np.ndarray,
    perimeter: float,
    targets: np.ndarray,
) -> np.ndarray:
    wrapped = np.mod(targets, perimeter)
    starts = np.searchsorted(positions, wrapped, side="right") - 1
    starts = np.clip(starts, 0, len(points) - 1)
    lengths = steps[starts]
    ratios = np.divide(
        wrapped - positions[starts],
        lengths,
        out=np.zeros_like(wrapped),
        where=lengths > 1e-15,
    )
    following = (starts + 1) % len(points)
    return points[starts] + ratios[:, None] * (points[following] - points[starts])


def _point_to_corresponding_lines(
    samples: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
) -> np.ndarray:
    delta = ends - starts
    lengths = np.linalg.norm(delta, axis=1)
    cross = np.abs(
        delta[:, 0] * (samples[:, 1] - starts[:, 1])
        - delta[:, 1] * (samples[:, 0] - starts[:, 0])
    )
    return np.divide(
        cross,
        lengths,
        out=np.full_like(cross, math.inf),
        where=lengths > 1e-15,
    )


def _corner_scale_metrics(
    points: np.ndarray,
    positions: np.ndarray,
    steps: np.ndarray,
    perimeter: float,
    distance_mm: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    previous = _sample_closed_contour(
        points,
        positions,
        steps,
        perimeter,
        positions - distance_mm,
    )
    following = _sample_closed_contour(
        points,
        positions,
        steps,
        perimeter,
        positions + distance_mm,
    )
    incoming = points - previous
    outgoing = following - points
    denominator = np.maximum(
        np.linalg.norm(incoming, axis=1) * np.linalg.norm(outgoing, axis=1),
        1e-15,
    )
    turns = np.arccos(
        np.clip(np.sum(incoming * outgoing, axis=1) / denominator, -1.0, 1.0)
    )
    orientation = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    residual = np.zeros(len(points), dtype=np.float64)
    for fraction in (1.0 / 3.0, 2.0 / 3.0):
        before = _sample_closed_contour(
            points,
            positions,
            steps,
            perimeter,
            positions - distance_mm * fraction,
        )
        after = _sample_closed_contour(
            points,
            positions,
            steps,
            perimeter,
            positions + distance_mm * fraction,
        )
        residual = np.maximum(
            residual,
            _point_to_corresponding_lines(before, previous, points),
        )
        residual = np.maximum(
            residual,
            _point_to_corresponding_lines(after, points, following),
        )
    return turns, orientation, residual


@_timed_stage("corner_classification")
def _corner_indices(
    points: np.ndarray,
    tolerance_mm: float | None = None,
) -> list[int]:
    """Return sharp turns that persist across two physical arc-length scales."""

    count = len(points)
    if count < 8:
        return list(range(count))
    positions, steps, perimeter = _closed_arc_positions(points)
    positive_steps = steps[steps > 1e-12]
    if not len(positive_steps) or perimeter <= 1e-12:
        return []
    step_mm = float(np.median(positive_steps))
    tolerance = step_mm if tolerance_mm is None else max(0.0, float(tolerance_mm))
    inner_distance = min(
        perimeter / 12.0,
        max(3.0 * step_mm, tolerance),
    )
    outer_distance = min(
        perimeter / 8.0,
        max(8.0 * step_mm, 3.0 * tolerance),
    )
    if outer_distance <= inner_distance:
        outer_distance = min(perimeter / 6.0, inner_distance * 1.5)
    inner_turns, inner_orientation, inner_residual = _corner_scale_metrics(
        points,
        positions,
        steps,
        perimeter,
        inner_distance,
    )
    outer_turns, outer_orientation, outer_residual = _corner_scale_metrics(
        points,
        positions,
        steps,
        perimeter,
        outer_distance,
    )
    maximum_turn = np.maximum(inner_turns, outer_turns)
    scale_agreement = np.divide(
        np.minimum(inner_turns, outer_turns),
        maximum_turn,
        out=np.zeros_like(maximum_turn),
        where=maximum_turn > 1e-12,
    )
    # A raster stair-step remains close to either supporting arm, while a
    # rounded cap (and especially a one-pixel spur on one) does not remain
    # linear at the outer scale.  Keep this tied to both trace pitch and the
    # requested physical tolerance: the former admits the half-pixel contour
    # bevel at a real corner and the latter prevents a larger fit tolerance
    # from turning a curved cap into a hard join.
    arm_allowance = max(0.75 * step_mm, 0.30 * tolerance)
    candidates = np.flatnonzero(
        (inner_turns >= math.radians(_CORNER_INNER_MINIMUM_TURN_DEGREES))
        & (outer_turns >= math.radians(_CORNER_MINIMUM_TURN_DEGREES))
        & (scale_agreement >= _CORNER_MINIMUM_SCALE_AGREEMENT)
        & (inner_orientation * outer_orientation > 0.0)
        & (inner_residual <= arm_allowance)
        & (outer_residual <= arm_allowance)
    )
    strengths = np.minimum(inner_turns, outer_turns) * scale_agreement
    minimum_separation = max(
        2,
        min(
            _MAX_CORNER_WINDOW_POINTS,
            int(math.ceil(inner_distance / max(step_mm, 1e-15))),
        ),
    )
    selected: list[int] = []
    blocked = np.zeros(count, dtype=bool)
    offsets = np.arange(
        -minimum_separation,
        minimum_separation + 1,
        dtype=np.int64,
    )
    ordered = candidates[np.argsort(strengths[candidates], kind="stable")[::-1]]
    for raw_index in ordered:
        index = int(raw_index)
        if blocked[index]:
            continue
        selected.append(index)
        blocked[(index + offsets) % count] = True

    # Preserve the bounded-behaviour contract for malformed synthetic inputs
    # whose final-to-first edge is not a normal closed-contour sample.
    if float(steps[-1]) > max(8.0 * step_mm, outer_distance * 2.0):
        selected.append(0)
    return sorted(set(selected))


def _gaussian_kernel(radius: int) -> np.ndarray:
    if radius <= 0:
        return np.ones(1, dtype=np.float64)
    sigma = max(0.75, radius / 2.5)
    positions = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(positions**2) / (2.0 * sigma**2))
    return kernel / np.sum(kernel)


def _smooth_open_span(points: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0 or len(points) <= 3:
        return points.copy()
    radius = min(radius, max(1, (len(points) - 2) // 2))
    kernel = _gaussian_kernel(radius)
    padded = np.pad(points, ((radius, radius), (0, 0)), mode="edge")
    output = np.column_stack(
        [np.convolve(padded[:, axis], kernel, mode="valid") for axis in range(2)]
    )
    output[0] = points[0]
    output[-1] = points[-1]
    return output


def _smooth_contour(
    points: np.ndarray,
    corners: list[int],
    smoothing_mm: float,
) -> tuple[np.ndarray, float]:
    if smoothing_mm <= 0.0 or len(points) < 5:
        return points.copy(), 0.0
    steps = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    positive_steps = steps[steps > 1e-12]
    if not len(positive_steps):
        return points.copy(), 0.0
    radius = int(math.ceil(smoothing_mm / float(np.median(positive_steps))))
    radius = max(1, min(_MAX_SMOOTHING_RADIUS_POINTS, radius))
    if not corners:
        kernel = _gaussian_kernel(radius)
        padded = np.vstack((points[-radius:], points, points[:radius]))
        smoothed = np.column_stack(
            [np.convolve(padded[:, axis], kernel, mode="valid") for axis in range(2)]
        )
    else:
        smoothed = points.copy()
        ordered = sorted(corners)
        for offset, start in enumerate(ordered):
            end = ordered[(offset + 1) % len(ordered)]
            indices = (
                list(range(start, end + 1))
                if end > start
                else [*range(start, len(points)), *range(0, end + 1)]
            )
            segment = _smooth_open_span(points[indices], radius)
            smoothed[indices] = segment
        smoothed[ordered] = points[ordered]
    displacement = float(np.max(np.linalg.norm(smoothed - points, axis=1)))
    return smoothed, displacement


def _fitting_anchors(
    points_or_count: np.ndarray | int,
    corners: list[int],
    tolerance_mm: float | None = None,
    *,
    straight_runs: tuple[_StraightRun, ...] | None = None,
    source_pixel_spacing_mm: tuple[float, float] | None = None,
) -> list[int]:
    """Combine hard corners with deterministic straight-run and soft anchors."""

    if isinstance(points_or_count, np.ndarray):
        points = np.asarray(points_or_count, dtype=np.float64)
        point_count = len(points)
    else:
        points = None
        point_count = int(points_or_count)
    if point_count < 4:
        return list(range(point_count))
    anchors = sorted(set(int(index) % point_count for index in corners))
    runs: tuple[_StraightRun, ...] = ()
    if points is not None:
        tolerance = 0.0 if tolerance_mm is None else max(0.0, float(tolerance_mm))
        runs = (
            _persistent_straight_runs(
                points,
                tolerance,
                source_pixel_spacing_mm=source_pixel_spacing_mm,
            )
            if straight_runs is None
            else straight_runs
        )
        if anchors:
            anchors.extend(
                index
                for run in runs
                for index in (run.start_index, run.end_index)
            )
            anchors = sorted(set(anchors))
        elif runs:
            return _long_straight_run_anchors(
                points,
                tolerance,
                straight_runs=runs,
                source_pixel_spacing_mm=source_pixel_spacing_mm,
            )
    if len(anchors) >= 2:
        return anchors

    target_count = 8 if points is not None and not anchors and not runs else 4
    if anchors:
        target_count = 2
    if points is None:
        for index in np.linspace(
            0,
            point_count,
            target_count,
            endpoint=False,
            dtype=int,
        ):
            anchors.append(int(index) % point_count)
        return sorted(set(anchors))

    positions, _steps, perimeter = _closed_arc_positions(points)
    if perimeter <= 1e-12:
        return list(range(min(point_count, target_count)))
    origin = positions[anchors[0]] if anchors else 0.0
    targets = origin + np.arange(target_count, dtype=np.float64) * (
        perimeter / target_count
    )
    for target in np.mod(targets, perimeter):
        distance = np.abs(positions - target)
        circular = np.minimum(distance, perimeter - distance)
        anchors.append(int(np.argmin(circular)))
    return sorted(set(anchors))


def _circular_true_runs(mask: np.ndarray) -> list[np.ndarray]:
    count = len(mask)
    if count == 0 or not np.any(mask):
        return []
    if np.all(mask):
        return [np.arange(count, dtype=np.int64)]
    false_index = int(np.flatnonzero(~mask)[0])
    order = (np.arange(count, dtype=np.int64) + false_index + 1) % count
    values = mask[order]
    runs: list[np.ndarray] = []
    start = 0
    while start < count:
        if not values[start]:
            start += 1
            continue
        end = start + 1
        while end < count and values[end]:
            end += 1
        runs.append(order[start:end])
        start = end
    return runs


def _run_arc_length(run: np.ndarray, steps: np.ndarray) -> float:
    if len(run) < 2:
        return 0.0
    return float(np.sum(steps[run[:-1]]))


def _circular_indices(start: int, end: int, point_count: int) -> np.ndarray:
    if end >= start:
        return np.arange(start, end + 1, dtype=np.int64)
    return np.concatenate(
        (
            np.arange(start, point_count, dtype=np.int64),
            np.arange(0, end + 1, dtype=np.int64),
        )
    )


def _normal_quantization_spacing(
    direction: np.ndarray,
    fallback_step_mm: float,
    source_pixel_spacing_mm: tuple[float, float] | None,
) -> float:
    if source_pixel_spacing_mm is None:
        return fallback_step_mm
    normal = np.asarray((-direction[1], direction[0]), dtype=np.float64)
    x_spacing, y_spacing = source_pixel_spacing_mm
    return (
        abs(float(normal[0])) * x_spacing
        + abs(float(normal[1])) * y_spacing
    ) / float(RASTER_VECTORIZATION_OVERSAMPLE_FACTOR)


def _directional_turn_metrics(
    points: np.ndarray,
    sampling_distance_mm: float,
) -> tuple[float, float]:
    distances = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(distances)))
    length_mm = float(cumulative[-1])
    if length_mm <= 1e-15:
        return math.inf, math.inf
    sample_count = max(2, int(math.ceil(length_mm / sampling_distance_mm)) + 1)
    target_positions = np.linspace(0.0, length_mm, sample_count)
    starts = np.searchsorted(cumulative, target_positions, side="right") - 1
    starts = np.clip(starts, 0, len(points) - 2)
    local_lengths = distances[starts]
    ratios = np.divide(
        target_positions - cumulative[starts],
        local_lengths,
        out=np.zeros_like(target_positions),
        where=local_lengths > 1e-15,
    )
    sampled = points[starts] + ratios[:, None] * (
        points[starts + 1] - points[starts]
    )
    directions = np.diff(sampled, axis=0)
    direction_lengths = np.linalg.norm(directions, axis=1)
    directions = directions[direction_lengths > 1e-15]
    if not len(directions):
        return math.inf, math.inf
    angles = np.unwrap(np.arctan2(directions[:, 1], directions[:, 0]))
    chord = points[-1] - points[0]
    chord_angle = math.atan2(float(chord[1]), float(chord[0]))
    deviations = np.abs(
        np.arctan2(np.sin(angles - chord_angle), np.cos(angles - chord_angle))
    )
    directional_range = float(np.max(angles) - np.min(angles))
    return math.degrees(float(np.max(deviations))), math.degrees(directional_range)


def _straight_run_evidence(
    points: np.ndarray,
    indices: np.ndarray,
    tolerance_mm: float,
    oversampled_step_mm: float,
    source_pixel_spacing_mm: tuple[float, float] | None,
) -> _StraightRun | None:
    run_points = points[indices]
    delta = run_points[-1] - run_points[0]
    direction = _unit_direction(delta)
    if direction is None:
        return None
    length_mm = float(np.sum(np.linalg.norm(np.diff(run_points, axis=0), axis=1)))
    quantization_spacing = _normal_quantization_spacing(
        direction,
        oversampled_step_mm,
        source_pixel_spacing_mm,
    )
    residual_allowance = min(
        tolerance_mm,
        max(
            _STRAIGHT_QUANTIZATION_ALLOWANCE_STEPS * quantization_spacing,
            1e-12,
        ),
    )
    max_residual = float(
        np.max(_distance_to_segment(run_points, run_points[0], run_points[-1]))
    )
    if max_residual > residual_allowance:
        return None
    source_spacing = (
        max(source_pixel_spacing_mm)
        if source_pixel_spacing_mm is not None
        else oversampled_step_mm * RASTER_VECTORIZATION_OVERSAMPLE_FACTOR
    )
    sampling_distance = max(
        4.0 * oversampled_step_mm,
        2.5 * tolerance_mm,
        source_spacing,
    )
    maximum_turn, directional_range = _directional_turn_metrics(
        run_points,
        sampling_distance,
    )
    if (
        maximum_turn > _STRAIGHT_MAXIMUM_TURN_DEGREES
        or directional_range > _STRAIGHT_MAXIMUM_DIRECTION_RANGE_DEGREES
    ):
        return None
    return _StraightRun(
        start_index=int(indices[0]),
        end_index=int(indices[-1]),
        length_mm=length_mm,
        max_orthogonal_residual_mm=max_residual,
        maximum_directional_turn_degrees=maximum_turn,
        directional_range_degrees=directional_range,
    )


def _minimum_straight_run_length(
    tolerance_mm: float,
    oversampled_step_mm: float,
    source_pixel_spacing_mm: tuple[float, float] | None,
) -> float:
    source_spacing = (
        max(source_pixel_spacing_mm)
        if source_pixel_spacing_mm is not None
        else oversampled_step_mm * RASTER_VECTORIZATION_OVERSAMPLE_FACTOR
    )
    return max(
        _STRAIGHT_MINIMUM_TOLERANCE_MULTIPLE * tolerance_mm,
        _STRAIGHT_MINIMUM_SOURCE_PIXEL_MULTIPLE * source_spacing,
        _STRAIGHT_MINIMUM_OVERSAMPLED_STEP_MULTIPLE * oversampled_step_mm,
    )


def _circular_span_contains(
    outer_start: int,
    outer_end: int,
    inner_start: int,
    inner_end: int,
    point_count: int,
) -> bool:
    outer_length = (outer_end - outer_start) % point_count
    inner_start_offset = (inner_start - outer_start) % point_count
    inner_end_offset = (inner_end - outer_start) % point_count
    return inner_start_offset <= inner_end_offset <= outer_length


def _persistent_straight_runs(
    points: np.ndarray,
    tolerance_mm: float,
    *,
    source_pixel_spacing_mm: tuple[float, float] | None = None,
) -> tuple[_StraightRun, ...]:
    """Classify scale-aware full contour runs with positive line evidence."""

    if len(points) < 8 or tolerance_mm <= 0.0:
        return ()
    positions, steps, perimeter = _closed_arc_positions(points)
    positive_steps = steps[steps > 1e-12]
    if not len(positive_steps) or perimeter <= 1e-12:
        return ()
    oversampled_step = float(np.median(positive_steps))
    window_mm = min(
        perimeter / 16.0,
        max(8.0 * oversampled_step, 2.5 * tolerance_mm),
    )
    turns, _orientation, residuals = _corner_scale_metrics(
        points,
        positions,
        steps,
        perimeter,
        window_mm,
    )
    local_quantization = (
        max(source_pixel_spacing_mm) / RASTER_VECTORIZATION_OVERSAMPLE_FACTOR
        if source_pixel_spacing_mm is not None
        else oversampled_step
    )
    residual_allowance = min(
        tolerance_mm,
        max(
            _STRAIGHT_LOCAL_QUANTIZATION_ALLOWANCE_STEPS * local_quantization,
            1e-12,
        ),
    )
    permissive_mask = (
        turns <= math.radians(_STRAIGHT_LOCAL_MAXIMUM_TURN_DEGREES)
    ) & (residuals <= residual_allowance)
    strict_mask = (
        turns <= math.radians(_STRAIGHT_MAXIMUM_TURN_DEGREES)
    ) & (residuals <= residual_allowance)
    minimum_run_length = _minimum_straight_run_length(
        tolerance_mm,
        oversampled_step,
        source_pixel_spacing_mm,
    )
    candidates: list[_StraightRun] = []
    for mask in (permissive_mask, strict_mask):
        for indices in _circular_true_runs(mask):
            if _run_arc_length(indices, steps) < minimum_run_length:
                continue
            evidence = _straight_run_evidence(
                points,
                indices,
                tolerance_mm,
                oversampled_step,
                source_pixel_spacing_mm,
            )
            if evidence is not None:
                candidates.append(evidence)
    runs: list[_StraightRun] = []
    for candidate in sorted(candidates, key=lambda run: run.length_mm, reverse=True):
        if any(
            _circular_span_contains(
                retained.start_index,
                retained.end_index,
                candidate.start_index,
                candidate.end_index,
                len(points),
            )
            for retained in runs
        ):
            continue
        runs.append(candidate)
    runs.sort(key=lambda run: run.start_index)

    gap_allowance = max(1.5 * tolerance_mm, 6.0 * oversampled_step)
    index = 0
    while index + 1 < len(runs):
        first = runs[index]
        second = runs[index + 1]
        gap_indices = _circular_indices(
            first.end_index,
            second.start_index,
            len(points),
        )
        if _run_arc_length(gap_indices, steps) > gap_allowance:
            index += 1
            continue
        combined_indices = _circular_indices(
            first.start_index,
            second.end_index,
            len(points),
        )
        combined = _straight_run_evidence(
            points,
            combined_indices,
            tolerance_mm,
            oversampled_step,
            source_pixel_spacing_mm,
        )
        if combined is None:
            index += 1
            continue
        runs[index : index + 2] = [combined]
        if index:
            index -= 1
    # A locally flat pixel plateau on a genuine curve is not source evidence of
    # a line. Require persistence across at least one complete contour-scale
    # classification interval (the same one-sixteenth scale used to cap the
    # local evidence window), in addition to the physical tolerance/pixel
    # minimum.
    final_minimum_length = max(
        minimum_run_length,
        _STRAIGHT_MINIMUM_PERIMETER_FRACTION * perimeter,
    )
    return tuple(run for run in runs if run.length_mm >= final_minimum_length)


def _source_edge_normal_pitch_mm(
    normals: np.ndarray,
    source_pixel_spacing_mm: tuple[float, float],
) -> np.ndarray:
    x_spacing, y_spacing = source_pixel_spacing_mm
    inverse_pitch = np.sqrt(
        (normals[:, 0] / x_spacing) ** 2
        + (normals[:, 1] / y_spacing) ** 2
    )
    return np.divide(
        1.0,
        inverse_pitch,
        out=np.zeros_like(inverse_pitch),
        where=inverse_pitch > 1e-15,
    )


def _source_edge_profiles(
    points: np.ndarray,
    normals: np.ndarray,
    normal_pitch_mm: np.ndarray,
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    threshold_used: int | None,
    width_mm: float,
    height_mm: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fractions = np.arange(
        -_SOURCE_EDGE_PROFILE_RADIUS_SOURCE_PIXELS,
        _SOURCE_EDGE_PROFILE_RADIUS_SOURCE_PIXELS
        + _SOURCE_EDGE_PROFILE_STEP_SOURCE_PIXELS * 0.5,
        _SOURCE_EDGE_PROFILE_STEP_SOURCE_PIXELS,
        dtype=np.float64,
    )
    offsets = normal_pitch_mm[:, None] * fractions[None, :]
    samples = points[:, None, :] + normals[:, None, :] * offsets[:, :, None]
    map_x = (
        (samples[:, :, 0] / width_mm + 0.5) * source.width_px - 0.5
    )
    map_y = (
        (0.5 - samples[:, :, 1] / height_mm) * source.height_px - 0.5
    )
    valid = (
        (map_x >= 0.0).all(axis=1)
        & (map_x <= source.width_px - 1.0).all(axis=1)
        & (map_y >= 0.0).all(axis=1)
        & (map_y <= source.height_px - 1.0).all(axis=1)
    )
    map_x_32 = map_x.astype(np.float32)
    map_y_32 = map_y.astype(np.float32)
    alpha = cv2.remap(
        source.alpha,
        map_x_32,
        map_y_32,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    ).astype(np.float64)
    if options.detection_mode is RasterDetectionMode.ALPHA:
        profiles = alpha - options.alpha_cutoff
        if options.invert:
            profiles = -profiles
        return fractions, profiles, valid

    assert threshold_used is not None
    grayscale = cv2.remap(
        source.composited_grayscale,
        map_x_32,
        map_y_32,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    ).astype(np.float64)
    intensity_margin = threshold_used - grayscale
    if options.invert:
        intensity_margin = -intensity_margin
    alpha_margin = alpha - options.alpha_cutoff
    return fractions, np.minimum(intensity_margin, alpha_margin), valid


@_timed_stage("source_edge_refinement")
def _refine_contour_source_edges(
    points: np.ndarray,
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    threshold_used: int | None,
    width_mm: float,
    height_mm: float,
) -> _SourceEdgeRefinement:
    """Move eligible contour samples to supported source-raster crossings.

    The extracted contour remains the topology authority. Each accepted update
    is one bounded displacement along a locally estimated normal. Hard-corner
    support and every positively classified straight run remain byte-for-byte
    at their extracted positions; weak, flat, multi-crossing, or non-monotone
    source profiles are left unchanged.
    """

    values = np.asarray(points, dtype=np.float64)
    count = len(values)
    if count < 8:
        return _unchanged_source_edge(values)
    source_spacing = (
        width_mm / source.width_px,
        height_mm / source.height_px,
    )
    positions, steps, perimeter = _closed_arc_positions(values)
    positive_steps = steps[steps > 1e-12]
    if perimeter <= 1e-12 or not len(positive_steps):
        return _unchanged_source_edge(values)
    normal_window_mm = min(
        perimeter / 16.0,
        max(source_spacing) * _SOURCE_EDGE_NORMAL_WINDOW_SOURCE_PIXELS,
    )
    previous = _sample_closed_contour(
        values,
        positions,
        steps,
        perimeter,
        positions - normal_window_mm,
    )
    following = _sample_closed_contour(
        values,
        positions,
        steps,
        perimeter,
        positions + normal_window_mm,
    )
    tangents = following - previous
    tangent_lengths = np.linalg.norm(tangents, axis=1)
    normals = np.zeros_like(tangents)
    usable_tangent = tangent_lengths > 1e-15
    normals[usable_tangent, 0] = (
        tangents[usable_tangent, 1] / tangent_lengths[usable_tangent]
    )
    normals[usable_tangent, 1] = (
        -tangents[usable_tangent, 0] / tangent_lengths[usable_tangent]
    )
    normal_pitch_mm = _source_edge_normal_pitch_mm(normals, source_spacing)

    corner_tolerance = options.simplification_tolerance_mm * 0.65
    fit_tolerance = options.simplification_tolerance_mm * 0.80
    corners = _corner_indices(values, corner_tolerance)
    protected = np.zeros(count, dtype=bool)
    for corner in corners:
        protected[(corner - 1) % count] = True
        protected[corner] = True
        protected[(corner + 1) % count] = True
    straight_runs = _persistent_straight_runs(
        values,
        fit_tolerance,
        source_pixel_spacing_mm=source_spacing,
    )
    oversampled_step = float(np.median(positive_steps))
    minimum_run_length = _minimum_straight_run_length(
        fit_tolerance,
        oversampled_step,
        source_spacing,
    )
    hard_anchors = np.flatnonzero(protected)
    promoted_runs: list[_StraightRun] = []
    if len(hard_anchors) >= 2:
        for offset, start_value in enumerate(hard_anchors):
            start = int(start_value)
            end = int(hard_anchors[(offset + 1) % len(hard_anchors)])
            indices = _circular_indices(start, end, count)
            if _run_arc_length(indices, steps) < minimum_run_length:
                continue
            evidence = _straight_run_evidence(
                values,
                indices,
                fit_tolerance,
                oversampled_step,
                source_spacing,
            )
            if evidence is not None:
                promoted_runs.append(evidence)
    for run in (*straight_runs, *promoted_runs):
        protected[_circular_indices(run.start_index, run.end_index, count)] = True

    eligible = np.zeros(count, dtype=bool)
    signed_displacements = np.zeros(count, dtype=np.float64)
    for start in range(0, count, _SOURCE_EDGE_PROFILE_CHUNK_SIZE):
        end = min(count, start + _SOURCE_EDGE_PROFILE_CHUNK_SIZE)
        fractions, profiles, in_frame = _source_edge_profiles(
            values[start:end],
            normals[start:end],
            normal_pitch_mm[start:end],
            source,
            options,
            threshold_used,
            width_mm,
            height_mm,
        )
        transitions = (profiles[:, :-1] >= 0.0) & (profiles[:, 1:] < 0.0)
        transition_counts = np.count_nonzero(transitions, axis=1)
        transition_indices = np.argmax(transitions, axis=1)
        rows = np.arange(end - start)
        left = profiles[rows, transition_indices]
        right = profiles[rows, transition_indices + 1]
        denominator = left - right
        crossing_fraction = fractions[transition_indices] + np.divide(
            left,
            denominator,
            out=np.zeros_like(left),
            where=denominator > 1e-15,
        ) * _SOURCE_EDGE_PROFILE_STEP_SOURCE_PIXELS
        profile_differences = np.diff(profiles, axis=1)
        reverse_variation = np.sum(
            np.maximum(profile_differences, 0.0),
            axis=1,
        )
        total_decline = profiles[:, 0] - profiles[:, -1]
        contrast = np.max(profiles, axis=1) - np.min(profiles, axis=1)
        crossing_slope = np.divide(
            denominator,
            _SOURCE_EDGE_PROFILE_STEP_SOURCE_PIXELS,
        )
        chunk_eligible = (
            usable_tangent[start:end]
            & in_frame
            & ~protected[start:end]
            & (normal_pitch_mm[start:end] > 1e-15)
            & (transition_counts == 1)
            & (profiles[:, 0] >= _SOURCE_EDGE_MINIMUM_ENDPOINT_MARGIN)
            & (profiles[:, -1] <= -_SOURCE_EDGE_MINIMUM_ENDPOINT_MARGIN)
            & (contrast >= _SOURCE_EDGE_MINIMUM_CONTRAST)
            & (crossing_slope >= _SOURCE_EDGE_MINIMUM_SLOPE_PER_SOURCE_PIXEL)
            & (
                reverse_variation
                <= _SOURCE_EDGE_MAXIMUM_REVERSE_VARIATION_FRACTION
                * np.maximum(total_decline, 0.0)
            )
            & (
                np.abs(crossing_fraction)
                <= _SOURCE_EDGE_MAXIMUM_DISPLACEMENT_SOURCE_PIXELS
            )
        )
        eligible[start:end] = chunk_eligible
        eligible_indices = np.flatnonzero(chunk_eligible) + start
        signed_displacements[eligible_indices] = (
            crossing_fraction[chunk_eligible]
            * normal_pitch_mm[eligible_indices]
        )
    refined = values + normals * signed_displacements[:, None]
    return _SourceEdgeRefinement(
        points=refined,
        signed_displacements_mm=signed_displacements,
        eligible=eligible,
        protected=protected,
    )


def _arc_midpoint_index(
    start: int,
    end: int,
    point_count: int,
    steps: np.ndarray,
) -> int:
    indices = (
        np.arange(start, end + 1, dtype=np.int64)
        if end > start
        else np.concatenate(
            (
                np.arange(start, point_count, dtype=np.int64),
                np.arange(0, end + 1, dtype=np.int64),
            )
        )
    )
    cumulative = np.concatenate(
        (
            np.asarray((0.0,), dtype=np.float64),
            np.cumsum(steps[indices[:-1]]),
        )
    )
    return int(indices[int(np.argmin(np.abs(cumulative - cumulative[-1] / 2.0)))])


def _long_straight_run_anchors(
    points: np.ndarray,
    tolerance_mm: float,
    *,
    straight_runs: tuple[_StraightRun, ...] | None = None,
    source_pixel_spacing_mm: tuple[float, float] | None = None,
) -> list[int]:
    """Anchor every persistent straight run and subdivide the curved gaps."""

    if len(points) < 8:
        return []
    _positions, steps, perimeter = _closed_arc_positions(points)
    positive_steps = steps[steps > 1e-12]
    if not len(positive_steps) or perimeter <= 1e-12:
        return []
    runs = (
        _persistent_straight_runs(
            points,
            tolerance_mm,
            source_pixel_spacing_mm=source_pixel_spacing_mm,
        )
        if straight_runs is None
        else straight_runs
    )
    if not runs:
        return []

    run_endpoints = sorted(
        {run.start_index for run in runs}
        | {run.end_index for run in runs}
    )
    if len(run_endpoints) < 2:
        return []

    anchors = list(run_endpoints)
    for offset, start in enumerate(run_endpoints):
        end = run_endpoints[(offset + 1) % len(run_endpoints)]
        indices = (
            np.arange(start, end + 1, dtype=np.int64)
            if end > start
            else np.concatenate(
                (
                    np.arange(start, len(points), dtype=np.int64),
                    np.arange(0, end + 1, dtype=np.int64),
                )
            )
        )
        span = points[indices]
        if float(np.max(_distance_to_segment(span, span[0], span[-1]))) > max(
            tolerance_mm,
            1e-12,
        ):
            anchors.append(
                _arc_midpoint_index(start, end, len(points), steps)
            )
    return sorted(set(anchors))


def _distance_to_segment(
    points: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
) -> np.ndarray:
    delta = end - start
    denominator = float(np.dot(delta, delta))
    if denominator <= 1e-18:
        return np.linalg.norm(points - start, axis=1)
    ratios = np.clip(((points - start) @ delta) / denominator, 0.0, 1.0)
    projections = start + ratios[:, None] * delta
    return np.linalg.norm(points - projections, axis=1)


def _cubic_values(
    start: np.ndarray,
    control_1: np.ndarray,
    control_2: np.ndarray,
    end: np.ndarray,
    parameters: np.ndarray,
) -> np.ndarray:
    inverse = 1.0 - parameters
    return (
        (inverse**3)[:, None] * start
        + (3.0 * inverse**2 * parameters)[:, None] * control_1
        + (3.0 * inverse * parameters**2)[:, None] * control_2
        + (parameters**3)[:, None] * end
    )


def _unit_direction(vector: np.ndarray) -> np.ndarray | None:
    length = float(np.linalg.norm(vector))
    if length <= 1e-15:
        return None
    return vector / length


def _endpoint_tangent(points: np.ndarray, *, at_start: bool) -> np.ndarray:
    offset = min(_MAX_FIT_TANGENT_WINDOW_POINTS, len(points) - 1)
    if at_start:
        vector = points[offset] - points[0]
        fallback = points[-1] - points[0]
    else:
        vector = points[-offset - 1] - points[-1]
        fallback = points[0] - points[-1]
    tangent = _unit_direction(vector)
    if tangent is None:
        tangent = _unit_direction(fallback)
    if tangent is None:
        return np.asarray((1.0, 0.0), dtype=np.float64)
    return tangent


def _closed_contour_tangent(
    points: np.ndarray,
    index: int,
    tolerance_mm: float,
) -> np.ndarray:
    positions, steps, perimeter = _closed_arc_positions(points)
    positive_steps = steps[steps > 1e-12]
    step_mm = float(np.median(positive_steps)) if len(positive_steps) else 0.0
    distance = min(
        perimeter / 16.0,
        max(4.0 * step_mm, 2.0 * tolerance_mm),
    )
    samples = _sample_closed_contour(
        points,
        positions,
        steps,
        perimeter,
        np.asarray((positions[index] - distance, positions[index] + distance)),
    )
    tangent = _unit_direction(samples[1] - samples[0])
    if tangent is None:
        tangent = _unit_direction(
            points[(index + 1) % len(points)]
            - points[(index - 1) % len(points)]
        )
    if tangent is None:
        return np.asarray((1.0, 0.0), dtype=np.float64)
    return tangent


def _chord_parameters(points: np.ndarray) -> np.ndarray:
    distances = np.linalg.norm(np.diff(points, axis=0), axis=1)
    cumulative = np.concatenate(([0.0], np.cumsum(distances)))
    total = float(cumulative[-1])
    if total <= 1e-15:
        return np.linspace(0.0, 1.0, len(points))
    return cumulative / total


def _maximum_control_distance(
    origin: np.ndarray,
    direction: np.ndarray,
    minimum: np.ndarray,
    maximum: np.ndarray,
) -> float:
    limit = math.inf
    for axis in range(2):
        component = float(direction[axis])
        if component > 1e-15:
            limit = min(limit, float((maximum[axis] - origin[axis]) / component))
        elif component < -1e-15:
            limit = min(limit, float((minimum[axis] - origin[axis]) / component))
    return max(0.0, limit)


def _generate_bezier_controls(
    points: np.ndarray,
    parameters: np.ndarray,
    start_tangent: np.ndarray,
    end_tangent: np.ndarray,
    tolerance_mm: float,
    control_minimum: np.ndarray | None = None,
    control_maximum: np.ndarray | None = None,
) -> np.ndarray:
    """Solve positive tangent-constrained handles inside current-main bounds."""

    inverse = 1.0 - parameters
    basis_0 = inverse**3
    basis_1 = 3.0 * inverse**2 * parameters
    basis_2 = 3.0 * inverse * parameters**2
    basis_3 = parameters**3
    residual = (
        points
        - (basis_0 + basis_1)[:, None] * points[0]
        - (basis_2 + basis_3)[:, None] * points[-1]
    )
    matrix = np.column_stack(
        (
            (basis_1[:, None] * start_tangent).reshape(-1),
            (basis_2[:, None] * end_tangent).reshape(-1),
        )
    )
    handle_distances, _residuals, rank, _singular = np.linalg.lstsq(
        matrix,
        residual.reshape(-1),
        rcond=None,
    )
    chord_length = float(np.linalg.norm(points[-1] - points[0]))
    fallback_distance = chord_length / 3.0
    if (
        rank < 2
        or not np.all(np.isfinite(handle_distances))
        or np.any(handle_distances <= chord_length * 1e-6)
    ):
        handle_distances = np.asarray(
            (fallback_distance, fallback_distance),
            dtype=np.float64,
        )
    minimum = (
        np.min(points, axis=0) - tolerance_mm
        if control_minimum is None
        else control_minimum
    )
    maximum = (
        np.max(points, axis=0) + tolerance_mm
        if control_maximum is None
        else control_maximum
    )
    handle_distances[0] = min(
        float(handle_distances[0]),
        _maximum_control_distance(points[0], start_tangent, minimum, maximum),
    )
    handle_distances[1] = min(
        float(handle_distances[1]),
        _maximum_control_distance(points[-1], end_tangent, minimum, maximum),
    )
    return np.vstack(
        (
            points[0],
            points[0] + handle_distances[0] * start_tangent,
            points[-1] + handle_distances[1] * end_tangent,
            points[-1],
        )
    )


def _cubic_derivatives(
    controls: np.ndarray,
    parameter: float,
) -> tuple[np.ndarray, np.ndarray]:
    inverse = 1.0 - parameter
    first = 3.0 * (
        inverse**2 * (controls[1] - controls[0])
        + 2.0 * inverse * parameter * (controls[2] - controls[1])
        + parameter**2 * (controls[3] - controls[2])
    )
    second = 6.0 * (
        inverse * (controls[2] - 2.0 * controls[1] + controls[0])
        + parameter * (controls[3] - 2.0 * controls[2] + controls[1])
    )
    return first, second


def _cubic_first_derivative_values(
    controls: np.ndarray,
    parameters: np.ndarray,
) -> np.ndarray:
    inverse = 1.0 - parameters
    return 3.0 * (
        (inverse**2)[:, None] * (controls[1] - controls[0])
        + (2.0 * inverse * parameters)[:, None] * (controls[2] - controls[1])
        + (parameters**2)[:, None] * (controls[3] - controls[2])
    )


@_timed_stage("newton_reparameterization")
def _reparameterize(
    points: np.ndarray,
    parameters: np.ndarray,
    controls: np.ndarray,
) -> np.ndarray:
    """Refine point parameters with bounded Newton-Raphson projection."""

    refined = parameters.copy()
    first_start = controls[1] - controls[0]
    first_middle = controls[2] - controls[1]
    first_end = controls[3] - controls[2]
    second_start = controls[2] - 2.0 * controls[1] + controls[0]
    second_end = controls[3] - 2.0 * controls[2] + controls[1]
    for index in range(1, len(points) - 1):
        parameter = float(parameters[index])
        value = _cubic_values(
            controls[0],
            controls[1],
            controls[2],
            controls[3],
            np.asarray((parameter,), dtype=np.float64),
        )[0]
        inverse = 1.0 - parameter
        first = 3.0 * (
            inverse**2 * first_start
            + 2.0 * inverse * parameter * first_middle
            + parameter**2 * first_end
        )
        second = 6.0 * (inverse * second_start + parameter * second_end)
        delta = value - points[index]
        denominator = float(np.dot(first, first) + np.dot(delta, second))
        if abs(denominator) > 1e-15:
            candidate = parameter - float(np.dot(delta, first)) / denominator
            refined[index] = min(1.0, max(0.0, candidate))
    if np.any(np.diff(refined) <= 1e-9):
        return parameters
    return refined


def _split_cubic_controls_at(
    controls: np.ndarray,
    parameter: float,
) -> tuple[np.ndarray, np.ndarray]:
    parameter = float(np.clip(parameter, 0.0, 1.0))
    first = controls[:-1] + parameter * (controls[1:] - controls[:-1])
    second = first[:-1] + parameter * (first[1:] - first[:-1])
    midpoint = second[0] + parameter * (second[1] - second[0])
    return (
        np.vstack((controls[0], first[0], second[0], midpoint)),
        np.vstack((midpoint, second[1], first[2], controls[3])),
    )


def _subcurve_controls(
    controls: np.ndarray,
    start_parameter: float,
    end_parameter: float,
) -> np.ndarray:
    if start_parameter <= 0.0 and end_parameter >= 1.0:
        return controls.copy()
    left, _right = _split_cubic_controls_at(controls, end_parameter)
    if start_parameter <= 0.0:
        return left
    relative = start_parameter / end_parameter
    _prefix, interval = _split_cubic_controls_at(left, relative)
    return interval


@_timed_stage("continuous_fit_validation")
def _validate_curve_fit(
    target: np.ndarray,
    parameters: np.ndarray,
    controls: np.ndarray,
    tolerance_mm: float,
    budget: _ComplexityBudget,
) -> _FitValidation:
    """Conservatively bound the complete curve against its target polyline.

    Each target edge is represented as a cubic line over the fitted edge's
    parameter interval.  The difference between that line and the restricted
    fitted cubic is itself a cubic, so the convex hull of its four difference
    controls bounds every between-sample deviation in both directions.
    """

    values = _cubic_values(
        controls[0],
        controls[1],
        controls[2],
        controls[3],
        parameters,
    )
    assigned_errors = np.linalg.norm(values - target, axis=1)
    maximum = float(np.max(assigned_errors))
    sample_sum = float(np.sum(assigned_errors))
    sample_squared_sum = float(np.sum(assigned_errors**2))
    sample_count = len(assigned_errors)
    if maximum > tolerance_mm:
        split_index = int(np.argmax(assigned_errors))
        return _FitValidation(
            False,
            maximum,
            max(1, min(len(target) - 2, split_index)),
            sample_sum,
            sample_squared_sum,
            sample_count,
        )

    starts = target[:-1]
    ends = target[1:]
    first_parameters = parameters[:-1]
    last_parameters = parameters[1:]
    parameter_widths = last_parameters - first_parameters
    derivatives = _cubic_first_derivative_values(controls, parameters)
    curve_controls = np.stack(
        (
            values[:-1],
            values[:-1] + parameter_widths[:, None] * derivatives[:-1] / 3.0,
            values[1:] - parameter_widths[:, None] * derivatives[1:] / 3.0,
            values[1:],
        ),
        axis=1,
    )
    target_deltas = ends - starts
    target_controls = np.stack(
        (
            starts,
            starts + target_deltas / 3.0,
            starts + 2.0 * target_deltas / 3.0,
            ends,
        ),
        axis=1,
    )
    upper_bounds = np.max(
        np.linalg.norm(curve_controls - target_controls, axis=2),
        axis=1,
    )
    budget.consume_fit_validation_step(len(upper_bounds))
    accepted = upper_bounds <= tolerance_mm
    if np.any(accepted):
        maximum = max(maximum, float(np.max(upper_bounds[accepted])))
        midpoint_parameters = (
            first_parameters[accepted] + last_parameters[accepted]
        ) / 2.0
        curve_midpoints = _cubic_values(
            controls[0],
            controls[1],
            controls[2],
            controls[3],
            midpoint_parameters,
        )
        target_midpoints = (starts[accepted] + ends[accepted]) / 2.0
        midpoint_errors = np.linalg.norm(
            curve_midpoints - target_midpoints,
            axis=1,
        )
        sample_sum += float(np.sum(midpoint_errors))
        sample_squared_sum += float(np.sum(midpoint_errors**2))
        sample_count += len(midpoint_errors)

    for raw_index in np.flatnonzero(~accepted):
        index = int(raw_index)
        start = starts[index]
        end = ends[index]
        stack: list[tuple[np.ndarray, np.ndarray, float, float, int]] = [
            (
                start,
                end,
                float(first_parameters[index]),
                float(last_parameters[index]),
                0,
            )
        ]
        while stack:
            first, last, first_u, last_u, depth = stack.pop()
            budget.consume_fit_validation_step()
            curve_controls = _subcurve_controls(controls, first_u, last_u)
            delta = last - first
            target_controls = np.vstack(
                (
                    first,
                    first + delta / 3.0,
                    first + 2.0 * delta / 3.0,
                    last,
                )
            )
            upper = float(
                np.max(np.linalg.norm(curve_controls - target_controls, axis=1))
            )
            midpoint_u = (first_u + last_u) / 2.0
            curve_midpoint = _cubic_values(
                controls[0],
                controls[1],
                controls[2],
                controls[3],
                np.asarray((midpoint_u,), dtype=np.float64),
            )[0]
            midpoint = (first + last) / 2.0
            midpoint_error = float(np.linalg.norm(curve_midpoint - midpoint))
            sample_sum += midpoint_error
            sample_squared_sum += midpoint_error * midpoint_error
            sample_count += 1
            if midpoint_error > tolerance_mm:
                return _FitValidation(
                    False,
                    max(maximum, midpoint_error),
                    max(1, min(len(target) - 2, index + 1)),
                    sample_sum,
                    sample_squared_sum,
                    sample_count,
                )
            if upper <= tolerance_mm:
                maximum = max(maximum, upper)
                continue
            if depth >= _MAX_FIT_VALIDATION_RECURSION:
                return _FitValidation(
                    False,
                    max(maximum, upper),
                    max(1, min(len(target) - 2, index + 1)),
                    sample_sum,
                    sample_squared_sum,
                    sample_count,
                )
            stack.append((midpoint, last, midpoint_u, last_u, depth + 1))
            stack.append((first, midpoint, first_u, midpoint_u, depth + 1))
    return _FitValidation(
        True,
        maximum,
        0,
        sample_sum,
        sample_squared_sum,
        sample_count,
    )


def _cubic_controls_have_ambiguous_topology(controls: np.ndarray) -> bool:
    segment = PathCubicSegment(
        control_1=(float(controls[1, 0]), float(controls[1, 1])),
        control_2=(float(controls[2, 0]), float(controls[2, 1])),
        to=(float(controls[3, 0]), float(controls[3, 1])),
    )
    return _cubic_self_topology_is_ambiguous(
        (float(controls[0, 0]), float(controls[0, 1])),
        segment,
        PathAffineTransform(),
    )


def _cubic_fit_distribution_is_centered(
    target: np.ndarray,
    parameters: np.ndarray,
    values: np.ndarray,
    controls: np.ndarray,
    tolerance_mm: float,
) -> bool:
    """Require material curved spans to distribute error around the evidence.

    The conservative continuous proof remains the maximum-error authority. This
    additional test prevents a long cubic from passing that proof on its first
    chord-length correspondence while bowing consistently to one side of the
    dense threshold contour. An otherwise bounded candidate receives up to
    three centering-driven passes through the existing Newton path; the cap
    preserves exact-fit responsiveness while the continuous proof still guards
    every accepted candidate.
    """

    if len(target) < 3:
        return True
    steps = np.linalg.norm(np.diff(target, axis=0), axis=1)
    span_length = float(np.sum(steps))
    if span_length < (
        _CUBIC_DISTRIBUTION_MINIMUM_TOLERANCE_MULTIPLE * tolerance_mm
    ):
        return True
    weights = np.empty(len(target), dtype=np.float64)
    weights[0] = steps[0] / 2.0
    weights[-1] = steps[-1] / 2.0
    weights[1:-1] = (steps[:-1] + steps[1:]) / 2.0
    weight_sum = float(np.sum(weights))
    if weight_sum <= 1e-15:
        return True

    errors = values - target
    squared_errors = np.sum(errors * errors, axis=1)
    rms_error = math.sqrt(float(np.dot(weights, squared_errors)) / weight_sum)
    derivatives = _cubic_first_derivative_values(controls, parameters)
    derivative_lengths = np.linalg.norm(derivatives, axis=1)
    usable = derivative_lengths > 1e-15
    if not np.any(usable):
        return False
    signed_normal_errors = np.zeros(len(target), dtype=np.float64)
    signed_normal_errors[usable] = (
        derivatives[usable, 1] * errors[usable, 0]
        - derivatives[usable, 0] * errors[usable, 1]
    ) / derivative_lengths[usable]
    usable_weights = weights * usable
    usable_weight_sum = float(np.sum(usable_weights))
    if usable_weight_sum <= 1e-15:
        return False
    normal_bias = float(np.dot(usable_weights, signed_normal_errors)) / (
        usable_weight_sum
    )
    positive_weight = float(
        np.sum(usable_weights[signed_normal_errors > 1e-15])
    )
    negative_weight = float(
        np.sum(usable_weights[signed_normal_errors < -1e-15])
    )
    signed_weight = positive_weight + negative_weight
    same_side_fraction = (
        max(positive_weight, negative_weight) / signed_weight
        if signed_weight > 1e-15
        else 0.0
    )
    biased = bool(
        abs(normal_bias)
        > _CUBIC_DISTRIBUTION_BIAS_TRIGGER_TOLERANCE_FRACTION * tolerance_mm
    )
    return not bool(
        biased
        and (
            rms_error
            > _CUBIC_DISTRIBUTION_RMS_TRIGGER_TOLERANCE_FRACTION * tolerance_mm
            or same_side_fraction
            >= _CUBIC_DISTRIBUTION_ONE_SIDED_TRIGGER_FRACTION
        )
    )


@_timed_stage("cubic_fitting")
def _attempt_cubic_piece(
    points: np.ndarray,
    tolerance_mm: float,
    start_tangent: np.ndarray,
    end_tangent: np.ndarray,
    budget: _ComplexityBudget,
    *,
    hard_start: bool = False,
    hard_end: bool = False,
    control_minimum: np.ndarray | None = None,
    control_maximum: np.ndarray | None = None,
    max_reparameterization_iterations: int = _MAX_REPARAMETERIZATION_ITERATIONS,
) -> tuple[_FittedPiece | None, int, _CubicSegment]:
    parameters = _chord_parameters(points)
    split_index = max(1, min(len(points) - 2, len(points) // 2))
    best_error = math.inf
    best_segment: _CubicSegment | None = None
    for _iteration in range(max_reparameterization_iterations + 1):
        controls = _generate_bezier_controls(
            points,
            parameters,
            start_tangent,
            end_tangent,
            tolerance_mm,
            control_minimum,
            control_maximum,
        )
        values = _cubic_values(
            controls[0],
            controls[1],
            controls[2],
            controls[3],
            parameters,
        )
        errors = np.linalg.norm(values - points, axis=1)
        maximum_error = float(np.max(errors))
        if maximum_error < best_error:
            best_error = maximum_error
            split_index = int(np.argmax(errors))
            best_segment = _CubicSegment(
                controls[0].copy(),
                controls[1].copy(),
                controls[2].copy(),
                controls[3].copy(),
                maximum_error,
            )
        candidate_is_bounded = maximum_error <= tolerance_mm
        controls_are_unambiguous = bool(
            not candidate_is_bounded
            or not _cubic_controls_have_ambiguous_topology(controls)
        )
        distribution_is_centered = bool(
            not candidate_is_bounded
            or not controls_are_unambiguous
            or _iteration >= _CUBIC_DISTRIBUTION_MAX_REPARAMETERIZATIONS
            or _cubic_fit_distribution_is_centered(
                points,
                parameters,
                values,
                controls,
                tolerance_mm,
            )
        )
        if (
            candidate_is_bounded
            and controls_are_unambiguous
            and distribution_is_centered
        ):
            validation = _validate_curve_fit(
                points,
                parameters,
                controls,
                tolerance_mm,
                budget,
            )
            if validation.accepted:
                segment = _CubicSegment(
                    controls[0].copy(),
                    controls[1].copy(),
                    controls[2].copy(),
                    controls[3].copy(),
                    validation.max_error_mm,
                )
                return (
                    _FittedPiece(
                        segment=segment,
                        target_points=points,
                        target_parameters=parameters,
                        start_tangent=start_tangent,
                        end_tangent=end_tangent,
                        hard_start=hard_start,
                        hard_end=hard_end,
                        sample_error_sum_mm=validation.sample_error_sum_mm,
                        sample_squared_error_sum_mm2=(
                            validation.sample_squared_error_sum_mm2
                        ),
                        sample_count=validation.sample_count,
                    ),
                    split_index,
                    segment,
                )
            split_index = validation.split_index
        refined = _reparameterize(points, parameters, controls)
        if np.array_equal(refined, parameters):
            break
        parameters = refined
    if best_segment is None:
        best_segment = _CubicSegment(
            points[0].copy(),
            points[0].copy(),
            points[-1].copy(),
            points[-1].copy(),
            0.0,
        )
    return (
        None,
        max(1, min(len(points) - 2, split_index)),
        best_segment,
    )


def _fit_cubic(
    points: np.ndarray,
    tolerance_mm: float,
    start_tangent: np.ndarray | None = None,
    end_tangent: np.ndarray | None = None,
    control_minimum: np.ndarray | None = None,
    control_maximum: np.ndarray | None = None,
) -> _CubicSegment:
    """Compatibility seam returning the accepted or best bounded cubic."""

    if start_tangent is None:
        start_tangent = _endpoint_tangent(points, at_start=True)
    if end_tangent is None:
        end_tangent = _endpoint_tangent(points, at_start=False)
    start_tangent = _unit_direction(start_tangent)
    end_tangent = _unit_direction(end_tangent)
    if start_tangent is None or end_tangent is None:
        raise RasterVectorizationError("A fitted span has an undefined endpoint tangent")
    piece, _split, best = _attempt_cubic_piece(
        points,
        tolerance_mm,
        start_tangent,
        end_tangent,
        _ComplexityBudget(),
        control_minimum=control_minimum,
        control_maximum=control_maximum,
    )
    return best if piece is None else piece.segment


def _attempt_line_piece(
    points: np.ndarray,
    tolerance_mm: float,
    budget: _ComplexityBudget,
    *,
    hard_start: bool = False,
    hard_end: bool = False,
) -> _FittedPiece | None:
    delta = points[-1] - points[0]
    controls = np.vstack(
        (
            points[0],
            points[0] + delta / 3.0,
            points[0] + 2.0 * delta / 3.0,
            points[-1],
        )
    )
    parameters = _chord_parameters(points)
    validation = _validate_curve_fit(
        points,
        parameters,
        controls,
        tolerance_mm,
        budget,
    )
    if not validation.accepted:
        return None
    forward = _unit_direction(delta)
    if forward is None:
        forward = np.asarray((1.0, 0.0), dtype=np.float64)
    return _FittedPiece(
        segment=_LineSegment(
            points[0].copy(),
            points[-1].copy(),
            validation.max_error_mm,
        ),
        target_points=points,
        target_parameters=parameters,
        start_tangent=forward,
        end_tangent=-forward,
        hard_start=hard_start,
        hard_end=hard_end,
        sample_error_sum_mm=validation.sample_error_sum_mm,
        sample_squared_error_sum_mm2=validation.sample_squared_error_sum_mm2,
        sample_count=validation.sample_count,
    )


def _line_matches_tangents(
    start: np.ndarray,
    end: np.ndarray,
    start_tangent: np.ndarray,
    end_tangent: np.ndarray,
) -> bool:
    chord = _unit_direction(end - start)
    if chord is None:
        return True
    minimum_alignment = math.cos(
        math.radians(_LINE_MAXIMUM_TANGENT_DEVIATION_DEGREES)
    )
    return bool(
        np.dot(start_tangent, chord) >= minimum_alignment
        and np.dot(-end_tangent, chord) >= minimum_alignment
    )


def _fit_span_pieces(
    points: np.ndarray,
    tolerance_mm: float,
    budget: _ComplexityBudget,
    depth: int = 0,
    *,
    start_tangent: np.ndarray | None = None,
    end_tangent: np.ndarray | None = None,
    prefer_cubic_leaves: bool = False,
    allow_unconstrained_line: bool = False,
    allow_tangent_line: bool = True,
    control_minimum: np.ndarray | None = None,
    control_maximum: np.ndarray | None = None,
    hard_start: bool = False,
    hard_end: bool = False,
) -> list[_FittedPiece]:
    if start_tangent is None:
        start_tangent = _endpoint_tangent(points, at_start=True)
    if end_tangent is None:
        end_tangent = _endpoint_tangent(points, at_start=False)
    start_tangent = _unit_direction(start_tangent)
    end_tangent = _unit_direction(end_tangent)
    if start_tangent is None or end_tangent is None:
        raise RasterVectorizationError("A fitted span has an undefined endpoint tangent")
    polyline_steps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    polyline_length = float(np.sum(polyline_steps))
    closed_span = bool(
        polyline_length > 1e-15
        and np.linalg.norm(points[-1] - points[0]) <= 1e-15
    )
    if closed_span:
        if depth >= _MAX_FIT_RECURSION:
            _raise_complexity(
                "A non-degenerate closed fitting span could not be split within "
                "the bounded recursion limit"
            )
        cumulative = np.concatenate(([0.0], np.cumsum(polyline_steps)))
        split = int(np.searchsorted(cumulative, polyline_length / 2.0))
        split = max(1, min(len(points) - 2, split))
    else:
        split = -1
    if len(points) <= 2:
        if prefer_cubic_leaves and len(points) == 2:
            cubic, _split, _best = _attempt_cubic_piece(
                points,
                tolerance_mm,
                start_tangent,
                end_tangent,
                budget,
                hard_start=hard_start,
                hard_end=hard_end,
                control_minimum=control_minimum,
                control_maximum=control_maximum,
            )
            if cubic is not None:
                budget.add_fitted_segments()
                return [cubic]
        line = _attempt_line_piece(
            points,
            tolerance_mm,
            budget,
            hard_start=hard_start,
            hard_end=hard_end,
        )
        if line is None:
            raise RasterVectorizationError(
                "A source contour edge could not satisfy the fitting tolerance"
            )
        budget.add_fitted_segments()
        return [line]
    line_errors = _distance_to_segment(points, points[0], points[-1])
    line_error = float(np.max(line_errors))
    if not closed_span:
        if line_error <= tolerance_mm and (
            allow_unconstrained_line
            or (
                allow_tangent_line
                and _line_matches_tangents(
                    points[0],
                    points[-1],
                    start_tangent,
                    end_tangent,
                )
            )
        ):
            line = _attempt_line_piece(
                points,
                tolerance_mm,
                budget,
                hard_start=hard_start,
                hard_end=hard_end,
            )
            if line is not None:
                budget.add_fitted_segments()
                return [line]

        cubic, candidate_split, _best = _attempt_cubic_piece(
            points,
            tolerance_mm,
            start_tangent,
            end_tangent,
            budget,
            hard_start=hard_start,
            hard_end=hard_end,
            control_minimum=control_minimum,
            control_maximum=control_maximum,
        )
        if cubic is not None:
            budget.add_fitted_segments()
            return [cubic]
        if depth >= _MAX_FIT_RECURSION:
            pieces: list[_FittedPiece] = []
            for index in range(len(points) - 1):
                line = _attempt_line_piece(
                    points[index : index + 2],
                    tolerance_mm,
                    budget,
                    hard_start=hard_start and index == 0,
                    hard_end=hard_end and index == len(points) - 2,
                )
                if line is None:
                    raise RasterVectorizationError(
                        "A source contour edge could not satisfy the fitting tolerance"
                    )
                budget.add_fitted_segments()
                pieces.append(line)
            return pieces
        split = candidate_split
    tangent_window = min(
        _MAX_FIT_TANGENT_WINDOW_POINTS,
        split,
        len(points) - split - 1,
    )
    center_tangent = _unit_direction(
        points[split + tangent_window] - points[split - tangent_window]
    )
    if center_tangent is None:
        center_tangent = _unit_direction(points[-1] - points[0])
    if center_tangent is None:
        center_tangent = -end_tangent
    budget.recursive_splits += 1
    return [
        *_fit_span_pieces(
            points[: split + 1],
            tolerance_mm,
            budget,
            depth + 1,
            start_tangent=start_tangent,
            end_tangent=-center_tangent,
            prefer_cubic_leaves=prefer_cubic_leaves,
            allow_tangent_line=allow_tangent_line,
            control_minimum=control_minimum,
            control_maximum=control_maximum,
            hard_start=hard_start,
            hard_end=False,
        ),
        *_fit_span_pieces(
            points[split:],
            tolerance_mm,
            budget,
            depth + 1,
            start_tangent=center_tangent,
            end_tangent=end_tangent,
            prefer_cubic_leaves=prefer_cubic_leaves,
            allow_tangent_line=allow_tangent_line,
            control_minimum=control_minimum,
            control_maximum=control_maximum,
            hard_start=False,
            hard_end=hard_end,
        ),
    ]


def _fit_span(
    points: np.ndarray,
    tolerance_mm: float,
    budget: _ComplexityBudget,
    depth: int = 0,
    *,
    start_tangent: np.ndarray | None = None,
    end_tangent: np.ndarray | None = None,
    prefer_cubic_leaves: bool = False,
    allow_unconstrained_line: bool = False,
    control_minimum: np.ndarray | None = None,
    control_maximum: np.ndarray | None = None,
) -> list[_FittedSegment]:
    """Compatibility wrapper retaining current-main's private fit seam."""

    return [
        piece.segment
        for piece in _fit_span_pieces(
            points,
            tolerance_mm,
            budget,
            depth,
            start_tangent=start_tangent,
            end_tangent=end_tangent,
            prefer_cubic_leaves=prefer_cubic_leaves,
            allow_unconstrained_line=allow_unconstrained_line,
            control_minimum=control_minimum,
            control_maximum=control_maximum,
        )
    ]


def _contour_spans(points: np.ndarray, anchors: list[int]) -> list[np.ndarray]:
    spans: list[np.ndarray] = []
    for offset, start in enumerate(anchors):
        end = anchors[(offset + 1) % len(anchors)]
        indices = (
            list(range(start, end + 1))
            if end > start
            else [*range(start, len(points)), *range(0, end + 1)]
        )
        if len(indices) >= 2:
            spans.append(points[indices])
    return spans


def _piece_control_arc(piece: _FittedPiece) -> np.ndarray:
    segment = piece.segment
    if isinstance(segment, _LineSegment):
        return np.vstack((segment.start, segment.end))
    return np.vstack(
        (
            segment.start,
            segment.control_1,
            segment.control_2,
            segment.end,
        )
    )


def _merge_preserves_adjacent_topology(
    pieces: list[_FittedPiece],
    index: int,
    merged: _FittedPiece,
) -> bool:
    candidate = [*pieces[:index], merged, *pieces[index + 2 :]]
    if len(candidate) < 2:
        return False
    merged_index = index
    previous = candidate[(merged_index - 1) % len(candidate)]
    following = candidate[(merged_index + 1) % len(candidate)]
    topology_budget = _TopologyWorkBudget()
    return bool(
        _adjacent_control_arcs_share_only_endpoint(
            _piece_control_arc(previous),
            _piece_control_arc(merged),
            topology_budget,
        )
        and _adjacent_control_arcs_share_only_endpoint(
            _piece_control_arc(merged),
            _piece_control_arc(following),
            topology_budget,
        )
    )


@_timed_stage("adjacent_merging")
def _merge_smooth_pieces(
    pieces: list[_FittedPiece],
    tolerance_mm: float,
    control_minimum: np.ndarray,
    control_maximum: np.ndarray,
    budget: _ComplexityBudget,
    *,
    minimum_segment_count: int,
) -> list[_FittedPiece]:
    """Merge adjacent like-kind pieces only after full fit revalidation."""

    index = 0
    while len(pieces) > minimum_segment_count and index + 1 < len(pieces):
        first = pieces[index]
        second = pieces[index + 1]
        if first.hard_end or second.hard_start:
            index += 1
            continue
        if isinstance(first.segment, _LineSegment) != isinstance(
            second.segment,
            _LineSegment,
        ):
            index += 1
            continue
        target = np.vstack((first.target_points[:-1], second.target_points))
        merged: _FittedPiece | None = None
        if isinstance(first.segment, _LineSegment):
            merged = _attempt_line_piece(
                target,
                tolerance_mm,
                budget,
                hard_start=first.hard_start,
                hard_end=second.hard_end,
            )
        else:
            merged, _split, _best = _attempt_cubic_piece(
                target,
                tolerance_mm,
                first.start_tangent,
                second.end_tangent,
                budget,
                hard_start=first.hard_start,
                hard_end=second.hard_end,
                control_minimum=control_minimum,
                control_maximum=control_maximum,
                max_reparameterization_iterations=2,
            )
        if merged is None:
            index += 1
            continue
        if not _merge_preserves_adjacent_topology(pieces, index, merged):
            index += 1
            continue
        pieces[index : index + 2] = [merged]
        budget.fitted_segments -= 1
        budget.merged_segments += 1
        if index:
            index -= 1
    return pieces


def _longest_smooth_span_segment_count(pieces: list[_FittedPiece]) -> int:
    if not pieces:
        return 0
    boundaries = [
        index
        for index, piece in enumerate(pieces)
        if piece.hard_end or pieces[(index + 1) % len(pieces)].hard_start
    ]
    if not boundaries:
        return len(pieces)
    ordered = sorted(set(boundaries))
    return max(
        (ordered[(index + 1) % len(ordered)] - boundary) % len(pieces) or 1
        for index, boundary in enumerate(ordered)
    )


def _fit_contour(
    raw_points: np.ndarray,
    options: RasterVectorizationOptions,
    width_mm: float,
    height_mm: float,
    budget: _ComplexityBudget,
    *,
    source_pixel_spacing_mm: tuple[float, float] | None = None,
    classification_points: np.ndarray | None = None,
) -> _FittedContour:
    if classification_points is None:
        raw_points = _canonicalize_closed_contour(raw_points)
        classification = raw_points
    else:
        raw_points = np.asarray(raw_points, dtype=np.float64)
        classification = np.asarray(classification_points, dtype=np.float64)
        if classification.shape != raw_points.shape:
            raise ValueError(
                "classification_points must match the fitted contour shape"
            )
        start = _minimal_cyclic_rotation_index(classification)
        raw_points = np.roll(raw_points, -start, axis=0).copy()
        classification = np.roll(classification, -start, axis=0).copy()
    corner_tolerance = options.simplification_tolerance_mm * 0.65
    fit_tolerance = options.simplification_tolerance_mm * 0.80
    corners = _corner_indices(classification, corner_tolerance)
    # A dense raster trace represents a mathematically sharp corner with a
    # one-sample bevel.  Keep the two support samples beside the classified
    # corner as hard fitting anchors; this yields the expected short bevel plus
    # straight arms instead of asking a tangent-constrained cubic to round the
    # corner or recursively chase the stair-step.
    corner_set = {
        index
        for corner in corners
        for index in (
            (corner - 1) % len(raw_points),
            corner,
            (corner + 1) % len(raw_points),
        )
    }
    straight_runs = _persistent_straight_runs(
        classification,
        fit_tolerance,
        source_pixel_spacing_mm=source_pixel_spacing_mm,
    )
    if len(corner_set) >= 2:
        raw_steps = np.linalg.norm(
            np.roll(classification, -1, axis=0) - classification,
            axis=1,
        )
        positive_raw_steps = raw_steps[raw_steps > 1e-12]
        oversampled_step = (
            float(np.median(positive_raw_steps))
            if len(positive_raw_steps)
            else 0.0
        )
        minimum_run_length = _minimum_straight_run_length(
            fit_tolerance,
            oversampled_step,
            source_pixel_spacing_mm,
        )
        hard_anchors = sorted(corner_set)
        promoted_runs: list[_StraightRun] = []
        for offset, start in enumerate(hard_anchors):
            end = hard_anchors[(offset + 1) % len(hard_anchors)]
            indices = _circular_indices(start, end, len(raw_points))
            if _run_arc_length(indices, raw_steps) < minimum_run_length:
                continue
            evidence = _straight_run_evidence(
                classification,
                indices,
                fit_tolerance,
                oversampled_step,
                source_pixel_spacing_mm,
            )
            if evidence is not None:
                promoted_runs.append(evidence)
        if promoted_runs:
            straight_runs = tuple(
                run
                for run in straight_runs
                if not any(
                    _circular_span_contains(
                        promoted.start_index,
                        promoted.end_index,
                        run.start_index,
                        run.end_index,
                        len(raw_points),
                    )
                    for promoted in promoted_runs
                )
            ) + tuple(promoted_runs)
    straight_boundaries = {
        index
        for run in straight_runs
        for index in (run.start_index, run.end_index)
    }
    if (
        budget.fitted_segments + len(corner_set | straight_boundaries)
        > MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS
    ):
        _raise_complexity(
            "Raster vectorization requires more than "
            f"{MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS:,} fitted segments"
        )
    smoothed, smoothing_displacement = _smooth_contour(
        raw_points,
        sorted(set(corners) | straight_boundaries),
        options.smoothing_mm,
    )
    anchors = _fitting_anchors(
        smoothed,
        sorted(corner_set),
        fit_tolerance,
        straight_runs=straight_runs,
        source_pixel_spacing_mm=source_pixel_spacing_mm,
    )
    if (
        budget.fitted_segments + len(anchors)
        > MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS
    ):
        _raise_complexity(
            "Raster vectorization requires more than "
            f"{MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS:,} fitted segments"
        )
    smooth_anchor_tangents = {
        anchor: _closed_contour_tangent(smoothed, anchor, fit_tolerance)
        for anchor in anchors
        if anchor not in corner_set
    }
    control_minimum = np.asarray((-width_mm / 2.0, -height_mm / 2.0))
    control_maximum = np.asarray((width_mm / 2.0, height_mm / 2.0))
    split_start = budget.recursive_splits
    merged_start = budget.merged_segments
    pieces: list[_FittedPiece] = []
    spans = _contour_spans(smoothed, anchors)
    contour_steps = np.linalg.norm(
        np.roll(smoothed, -1, axis=0) - smoothed,
        axis=1,
    )
    positive_contour_steps = contour_steps[contour_steps > 1e-12]
    oversampled_step = (
        float(np.median(positive_contour_steps))
        if len(positive_contour_steps)
        else 0.0
    )
    straight_span_flags: list[bool] = []
    for offset, _span in enumerate(spans):
        start = anchors[offset]
        end = anchors[(offset + 1) % len(anchors)]
        classified = any(
            (start - run.start_index) % len(smoothed)
            <= (end - run.start_index) % len(smoothed)
            <= (run.end_index - run.start_index) % len(smoothed)
            for run in straight_runs
        )
        if classified:
            classified = (
                _straight_run_evidence(
                    smoothed,
                    _circular_indices(start, end, len(smoothed)),
                    fit_tolerance,
                    oversampled_step,
                    source_pixel_spacing_mm,
                )
                is not None
            )
        straight_span_flags.append(classified)
    # Persistent straight spans define their own derivative more accurately
    # than independently sampled endpoint windows do.  Share that chord
    # tangent with each adjoining smooth span so the join stays G1 across
    # differently phased raster samples.
    for offset, span in enumerate(spans):
        start = anchors[offset]
        end = anchors[(offset + 1) % len(anchors)]
        if (
            start not in corner_set
            and end not in corner_set
            and straight_span_flags[offset]
        ):
            tangent = _unit_direction(span[-1] - span[0])
            if tangent is not None:
                smooth_anchor_tangents[start] = tangent
                smooth_anchor_tangents[end] = tangent
    for offset, span in enumerate(spans):
        start = anchors[offset]
        end = anchors[(offset + 1) % len(anchors)]
        hard_start = start in corner_set
        hard_end = end in corner_set
        start_tangent = (
            _endpoint_tangent(span, at_start=True)
            if hard_start
            else smooth_anchor_tangents[start]
        )
        end_tangent = (
            _endpoint_tangent(span, at_start=False)
            if hard_end
            else -smooth_anchor_tangents[end]
        )
        straight_hard_span = bool(
            hard_start
            and hard_end
            and (len(span) <= 2 or straight_span_flags[offset])
        )
        if straight_span_flags[offset]:
            line = _attempt_line_piece(
                span,
                fit_tolerance,
                budget,
                hard_start=hard_start,
                hard_end=hard_end,
            )
            if line is not None:
                budget.add_fitted_segments()
                pieces.append(line)
                continue
        pieces.extend(
            _fit_span_pieces(
                span,
                fit_tolerance,
                budget,
                start_tangent=start_tangent,
                end_tangent=end_tangent,
                prefer_cubic_leaves=not straight_hard_span,
                allow_unconstrained_line=straight_hard_span,
                allow_tangent_line=False,
                control_minimum=control_minimum,
                control_maximum=control_maximum,
                hard_start=hard_start,
                hard_end=hard_end,
            )
        )
    pieces = _merge_smooth_pieces(
        pieces,
        fit_tolerance,
        control_minimum,
        control_maximum,
        budget,
        minimum_segment_count=max(4, len(corner_set)),
    )
    if not pieces:
        raise RasterVectorizationError("A contour could not be fitted to vector geometry")
    sample_count = sum(piece.sample_count for piece in pieces)
    sample_sum = sum(piece.sample_error_sum_mm for piece in pieces)
    sample_squared_sum = sum(
        piece.sample_squared_error_sum_mm2 for piece in pieces
    )
    segments = tuple(piece.segment for piece in pieces)
    return _FittedContour(
        segments=segments,
        smoothing_displacement_mm=smoothing_displacement,
        max_fitting_error_mm=max(
            segment.fitting_error_mm for segment in segments
        ),
        mean_fitting_error_mm=sample_sum / sample_count if sample_count else 0.0,
        rms_fitting_error_mm=(
            math.sqrt(sample_squared_sum / sample_count) if sample_count else 0.0
        ),
        fitting_error_sample_count=sample_count,
        hard_corner_count=len(corners),
        recursive_split_count=budget.recursive_splits - split_start,
        merged_segment_count=budget.merged_segments - merged_start,
        longest_smooth_span_segment_count=(
            _longest_smooth_span_segment_count(pieces)
        ),
    )


def _normalized_point(
    point: np.ndarray,
    width_mm: float,
    height_mm: float,
) -> tuple[float, float]:
    return float(point[0] / width_mm), float(point[1] / height_mm)


def _native_subpath_from_fitted_contour(
    fitted: _FittedContour,
    width_mm: float,
    height_mm: float,
) -> PathSubpath:
    """Convert the mathematical fit exactly once into canonical native types."""

    segments: list[PathLineSegment | PathCubicSegment] = []
    for segment in fitted.segments:
        if isinstance(segment, _LineSegment):
            segments.append(
                PathLineSegment(to=_normalized_point(segment.end, width_mm, height_mm))
            )
        else:
            segments.append(
                PathCubicSegment(
                    control_1=_normalized_point(
                        segment.control_1,
                        width_mm,
                        height_mm,
                    ),
                    control_2=_normalized_point(
                        segment.control_2,
                        width_mm,
                        height_mm,
                    ),
                    to=_normalized_point(segment.end, width_mm, height_mm),
                )
            )
    subpath = PathSubpath(
        start=_normalized_point(
            fitted.segments[0].start,
            width_mm,
            height_mm,
        ),
        segments=tuple(segments),
        closed=True,
    )
    # Constructing one authoritative path validates finite JSON coordinates,
    # segment types, and native complexity before preview flattening.
    return NativePathGeometry(
        subpaths=(subpath,),
        fill_rule=PathFillRule.EVENODD,
    ).subpaths[0]


def _physical_native_transform(
    width_mm: float,
    height_mm: float,
) -> PathAffineTransform:
    return PathAffineTransform.from_components(scale_x=width_mm, scale_y=height_mm)


def _validate_native_subpath_in_frame(
    native_subpath: PathSubpath,
    width_mm: float,
    height_mm: float,
) -> None:
    bounds = native_path_bounds(
        NativePathGeometry((native_subpath,), fill_rule=PathFillRule.EVENODD),
        _physical_native_transform(width_mm, height_mm),
    )
    x_min, y_min, x_max, y_max = bounds
    epsilon = max(
        _NATIVE_FRAME_EPSILON_MM,
        max(width_mm, height_mm) * 1e-12,
    )
    if (
        x_min < -width_mm / 2.0 - epsilon
        or x_max > width_mm / 2.0 + epsilon
        or y_min < -height_mm / 2.0 - epsilon
        or y_max > height_mm / 2.0 + epsilon
    ):
        raise RasterVectorizationError(
            "A fitted native curve leaves the source image frame; reduce smoothing "
            "or simplification"
        )


@_timed_stage("preview_flattening")
def _flatten_native_subpath_for_preview(
    native_subpath: PathSubpath,
    tolerance_mm: float,
    width_mm: float,
    height_mm: float,
    budget: _ComplexityBudget,
) -> np.ndarray:
    """Flatten the authoritative native path in physical millimetres."""

    remaining = (
        MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION
        - budget.preview_points
    )
    if remaining < 3:
        _raise_complexity(
            "Raster vectorization requires more than "
            f"{MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION:,} "
            "preview-flattened points"
        )
    try:
        flattened = flatten_native_path(
            NativePathGeometry((native_subpath,), fill_rule=PathFillRule.EVENODD),
            tolerance_mm,
            transform=_physical_native_transform(width_mm, height_mm),
            max_points=remaining,
            max_depth=_MAX_FLATTEN_RECURSION,
        )[0]
    except ValueError as exc:
        _raise_complexity(
            "Raster vectorization could not produce bounded preview-flattened points"
        )
        raise AssertionError("unreachable") from exc
    points = np.asarray(flattened, dtype=np.float64)
    if len(points) > 1 and np.linalg.norm(points[0] - points[-1]) <= 1e-9:
        points = points[:-1]
    if len(points) < 3:
        raise RasterVectorizationError(
            "Simplification collapsed a closed contour below three points"
        )
    keep = np.ones(len(points), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-12
    points = points[keep]
    if len(points) > 1 and np.linalg.norm(points[0] - points[-1]) <= 1e-12:
        points = points[:-1]
    if len(points) < 3 or abs(_signed_area(points)) <= 1e-15:
        raise RasterVectorizationError(
            "Fitting at the selected tolerance collapsed a closed contour; reduce "
            "smoothing or simplification"
        )
    budget.add_preview_points(len(points))
    return points


def _preview_mask(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    reduced = cv2.resize(mask, (width, height), interpolation=cv2.INTER_AREA)
    return (reduced >= 128).astype(np.uint8) * 255


def _overlay_preview(
    source_rgba: np.ndarray,
    contours: tuple[RasterVectorizedContour, ...],
) -> np.ndarray:
    height, width = source_rgba.shape[:2]
    overlay = source_rgba.copy()
    thickness = max(1, int(round(min(width, height) / 400.0)))
    for contour in contours:
        pixels = np.asarray(
            [
                [
                    int(round((x + 0.5) * width)),
                    int(round((0.5 - y) * height)),
                ]
                for x, y in contour.preview_points
            ],
            dtype=np.int32,
        ).reshape(-1, 1, 2)
        color = (255, 79, 159, 255) if contour.is_hole else (43, 227, 126, 255)
        cv2.polylines(
            overlay,
            [pixels],
            True,
            color,
            thickness=thickness,
            lineType=cv2.LINE_AA,
        )
    return overlay


@dataclass(slots=True)
class _TopologyWorkBudget:
    segment_pair_checks: int = 0

    def consume(self, count: int = 1) -> None:
        if (
            self.segment_pair_checks + count
            > _NATIVE_TOPOLOGY_MAX_SEGMENT_PAIR_CHECKS
        ):
            _raise_complexity(
                "Authoritative native-path topology validation requires more than "
                f"{_NATIVE_TOPOLOGY_MAX_SEGMENT_PAIR_CHECKS:,} bounded comparisons"
            )
        self.segment_pair_checks += count


def _cubic_self_topology_is_ambiguous(
    start: tuple[float, float],
    segment: PathCubicSegment,
    transform: PathAffineTransform,
) -> bool:
    """Detect the one possible double point of a planar cubic exactly."""

    p0, p1, p2, p3 = (
        np.asarray(transform.apply(point), dtype=np.float64)
        for point in (
            start,
            segment.control_1,
            segment.control_2,
            segment.to,
        )
    )
    cubic = -p0 + 3.0 * p1 - 3.0 * p2 + p3
    quadratic = 3.0 * p0 - 6.0 * p1 + 3.0 * p2
    linear = -3.0 * p0 + 3.0 * p1
    determinant = float(
        cubic[0] * quadratic[1] - cubic[1] * quadratic[0]
    )
    coefficient_scale = max(
        1.0,
        float(np.linalg.norm(cubic)),
        float(np.linalg.norm(quadratic)),
        float(np.linalg.norm(linear)),
    )
    if abs(determinant) <= coefficient_scale * coefficient_scale * 1e-12:
        chord = p3 - p0
        chord_length_squared = float(np.dot(chord, chord))
        if chord_length_squared <= 1e-24:
            return True
        first_projection = float(np.dot(p1 - p0, chord)) / chord_length_squared
        second_projection = float(np.dot(p2 - p0, chord)) / chord_length_squared
        return not 0.0 <= first_projection <= second_projection <= 1.0

    summed_parameters = float(
        (cubic[1] * linear[0] - cubic[0] * linear[1]) / determinant
    )
    squared_sum_minus_product = float(
        (quadratic[0] * linear[1] - linear[0] * quadratic[1])
        / determinant
    )
    discriminant = (
        4.0 * squared_sum_minus_product - 3.0 * summed_parameters**2
    )
    if discriminant <= 0.0:
        return False
    square_root = math.sqrt(discriminant)
    parameters = tuple(
        sorted(
            (
                (summed_parameters - square_root) / 2.0,
                (summed_parameters + square_root) / 2.0,
            )
        )
    )
    if parameters[0] < 0.0 or parameters[1] > 1.0:
        return False
    intended_closure = (
        parameters[0] <= 1e-12
        and parameters[1] >= 1.0 - 1e-12
        and float(np.linalg.norm(p3 - p0)) <= coefficient_scale * 1e-12
    )
    return not intended_closure


def _native_self_topology_is_ambiguous(
    contours: tuple[RasterVectorizedContour, ...],
    transform: PathAffineTransform,
) -> bool:
    for contour in contours:
        current = contour.native_subpath.start
        for segment in contour.native_subpath.segments:
            if isinstance(segment, PathCubicSegment) and (
                _cubic_self_topology_is_ambiguous(current, segment, transform)
            ):
                return True
            current = segment.to
    return False


def _split_control_polygon(
    controls: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    levels = [controls]
    while len(levels[-1]) > 1:
        previous = levels[-1]
        levels.append((previous[:-1] + previous[1:]) / 2.0)
    return (
        np.asarray([level[0] for level in levels], dtype=np.float64),
        np.asarray([level[-1] for level in reversed(levels)], dtype=np.float64),
    )


def _control_hulls_are_disjoint(first: np.ndarray, second: np.ndarray) -> bool:
    origin = first[0]
    translated = (first - origin, second - origin)
    scale = max(
        1.0,
        *(float(np.linalg.norm(point)) for controls in translated for point in controls),
    )
    epsilon = scale * 1e-12
    for controls in translated:
        for first_index in range(len(controls)):
            for second_index in range(first_index + 1, len(controls)):
                delta = controls[second_index] - controls[first_index]
                length = float(np.linalg.norm(delta))
                if length <= 1e-24:
                    continue
                direction = delta / length
                for axis in (direction, np.asarray((-direction[1], direction[0]))):
                    first_projection = translated[0] @ axis
                    second_projection = translated[1] @ axis
                    if (
                        float(np.max(first_projection))
                        < float(np.min(second_projection)) - epsilon
                        or float(np.max(second_projection))
                        < float(np.min(first_projection)) - epsilon
                    ):
                        return True
    return False


def _control_hulls_share_only_endpoint(
    first: np.ndarray,
    second: np.ndarray,
) -> bool:
    shared = first[-1]
    first_vectors = first[:-1] - shared
    second_vectors = second[1:] - shared
    vectors = (first_vectors, second_vectors)
    scale = max(
        1.0,
        *(float(np.linalg.norm(vector)) for group in vectors for vector in group),
    )
    epsilon = scale * 1e-12
    for group in vectors:
        for vector in group:
            length = float(np.linalg.norm(vector))
            if length <= 1e-24:
                continue
            direction = vector / length
            for normal in (direction, np.asarray((-direction[1], direction[0]))):
                first_sides = first_vectors @ normal
                second_sides = second_vectors @ normal
                separated = (
                    float(np.max(first_sides)) <= epsilon
                    and float(np.min(second_sides)) >= -epsilon
                ) or (
                    float(np.max(second_sides)) <= epsilon
                    and float(np.min(first_sides)) >= -epsilon
                )
                if not separated:
                    continue
                tangent = np.asarray((-normal[1], normal[0]))
                first_on_line = [
                    0.0,
                    *(
                        float(np.dot(value, tangent))
                        for value, side in zip(
                            first_vectors,
                            first_sides,
                            strict=True,
                        )
                        if abs(float(side)) <= epsilon
                    ),
                ]
                second_on_line = [
                    0.0,
                    *(
                        float(np.dot(value, tangent))
                        for value, side in zip(
                            second_vectors,
                            second_sides,
                            strict=True,
                        )
                        if abs(float(side)) <= epsilon
                    ),
                ]
                overlap_minimum = max(min(first_on_line), min(second_on_line))
                overlap_maximum = min(max(first_on_line), max(second_on_line))
                if overlap_minimum >= -epsilon and overlap_maximum <= epsilon:
                    return True
    return False


def _control_arcs_are_disjoint(
    first: np.ndarray,
    second: np.ndarray,
    budget: _TopologyWorkBudget,
    depth: int,
) -> bool:
    budget.consume()
    if _control_hulls_are_disjoint(first, second):
        return True
    if depth >= _MAX_FLATTEN_RECURSION:
        return False
    first_span = float(np.max(np.ptp(first, axis=0)))
    second_span = float(np.max(np.ptp(second, axis=0)))
    if first_span >= second_span:
        first_half, second_half = _split_control_polygon(first)
        return _control_arcs_are_disjoint(
            first_half,
            second,
            budget,
            depth + 1,
        ) and _control_arcs_are_disjoint(
            second_half,
            second,
            budget,
            depth + 1,
        )
    first_half, second_half = _split_control_polygon(second)
    return _control_arcs_are_disjoint(
        first,
        first_half,
        budget,
        depth + 1,
    ) and _control_arcs_are_disjoint(
        first,
        second_half,
        budget,
        depth + 1,
    )


def _adjacent_control_arcs_share_only_endpoint(
    first: np.ndarray,
    second: np.ndarray,
    budget: _TopologyWorkBudget,
    depth: int = 0,
) -> bool:
    budget.consume()
    if _control_hulls_share_only_endpoint(first, second):
        return True
    if depth >= _MAX_FLATTEN_RECURSION:
        return False
    first_far, first_near = _split_control_polygon(first)
    second_near, second_far = _split_control_polygon(second)
    next_depth = depth + 1
    return (
        _control_arcs_are_disjoint(first_far, second_near, budget, next_depth)
        and _control_arcs_are_disjoint(first_far, second_far, budget, next_depth)
        and _control_arcs_are_disjoint(first_near, second_far, budget, next_depth)
        and _adjacent_control_arcs_share_only_endpoint(
            first_near,
            second_near,
            budget,
            next_depth,
        )
    )


def _native_control_arcs(
    subpath: PathSubpath,
    transform: PathAffineTransform,
) -> tuple[np.ndarray, ...]:
    start = np.asarray(transform.apply(subpath.start), dtype=np.float64)
    current = start
    arcs: list[np.ndarray] = []
    for segment in subpath.segments:
        end = np.asarray(transform.apply(segment.to), dtype=np.float64)
        if isinstance(segment, PathLineSegment):
            controls = np.asarray((current, end), dtype=np.float64)
        else:
            controls = np.asarray(
                (
                    current,
                    transform.apply(segment.control_1),
                    transform.apply(segment.control_2),
                    end,
                ),
                dtype=np.float64,
            )
        arcs.append(controls)
        current = end
    if subpath.closed and not np.array_equal(current, start):
        arcs.append(np.asarray((current, start), dtype=np.float64))
    return tuple(arcs)


def _native_adjacent_topology_is_ambiguous(
    contours: tuple[RasterVectorizedContour, ...],
    transform: PathAffineTransform,
    budget: _TopologyWorkBudget,
) -> bool:
    for contour in contours:
        arcs = _native_control_arcs(contour.native_subpath, transform)
        if len(arcs) < 2:
            continue
        for index, first in enumerate(arcs):
            second = arcs[(index + 1) % len(arcs)]
            if not _adjacent_control_arcs_share_only_endpoint(
                first,
                second,
                budget,
            ):
                return True
    return False


def _topology_points(flattened: tuple[tuple[float, float], ...]) -> np.ndarray:
    points = np.asarray(flattened, dtype=np.float64)
    if len(points) > 1:
        keep = np.ones(len(points), dtype=bool)
        keep[1:] = np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-12
        points = points[keep]
    if len(points) and np.linalg.norm(points[0] - points[-1]) > 1e-12:
        points = np.vstack((points, points[0]))
    if len(points) < 4:
        raise RasterVectorizationError(
            "Authoritative native-path topology validation found a collapsed contour"
        )
    return points


def _topology_segments(
    points: np.ndarray,
) -> list[tuple[int, np.ndarray, np.ndarray, float, float, float, float]]:
    return [
        (
            index,
            start,
            end,
            min(start[0], end[0]),
            max(start[0], end[0]),
            min(start[1], end[1]),
            max(start[1], end[1]),
        )
        for index, (start, end) in enumerate(
            zip(points[:-1], points[1:], strict=True)
        )
    ]


def _segment_distance_squared(
    first_start: np.ndarray,
    first_end: np.ndarray,
    second_start: np.ndarray,
    second_end: np.ndarray,
) -> float:
    def orientation(start: np.ndarray, end: np.ndarray, point: np.ndarray) -> float:
        direction = end - start
        offset = point - start
        return float(direction[0] * offset[1] - direction[1] * offset[0])

    scale = max(
        1.0,
        *(abs(float(value)) for point in (
            first_start,
            first_end,
            second_start,
            second_end,
        ) for value in point),
    )
    epsilon = scale * scale * 1e-12
    first_sides = (
        orientation(first_start, first_end, second_start),
        orientation(first_start, first_end, second_end),
    )
    second_sides = (
        orientation(second_start, second_end, first_start),
        orientation(second_start, second_end, first_end),
    )
    boxes_overlap = (
        max(min(first_start[0], first_end[0]), min(second_start[0], second_end[0]))
        <= min(max(first_start[0], first_end[0]), max(second_start[0], second_end[0]))
        + epsilon
        and max(min(first_start[1], first_end[1]), min(second_start[1], second_end[1]))
        <= min(max(first_start[1], first_end[1]), max(second_start[1], second_end[1]))
        + epsilon
    )
    if boxes_overlap and all(
        not (first > epsilon and second > epsilon)
        and not (first < -epsilon and second < -epsilon)
        for first, second in (first_sides, second_sides)
    ):
        return 0.0

    def point_distance(
        point: np.ndarray,
        start: np.ndarray,
        end: np.ndarray,
    ) -> float:
        delta = end - start
        length_squared = float(np.dot(delta, delta))
        if length_squared <= 1e-30:
            return float(np.dot(point - start, point - start))
        ratio = float(np.dot(point - start, delta)) / length_squared
        difference = point - (start + max(0.0, min(1.0, ratio)) * delta)
        return float(np.dot(difference, difference))

    return min(
        point_distance(first_start, second_start, second_end),
        point_distance(first_end, second_start, second_end),
        point_distance(second_start, first_start, first_end),
        point_distance(second_end, first_start, first_end),
    )


def _self_topology_is_ambiguous(
    points: np.ndarray,
    clearance_mm: float,
    budget: _TopologyWorkBudget,
) -> bool:
    segments = _topology_segments(points)
    count = len(segments)
    if count < 3:
        return True

    # Adjacent arcs share one intended endpoint. A same-ray departure is instead
    # a retrace and is not an admissible closed boundary.
    for index, segment in enumerate(segments):
        following = segments[(index + 1) % count]
        shared = segment[2]
        first_away = segment[1] - shared
        second_away = following[2] - shared
        product = float(np.linalg.norm(first_away) * np.linalg.norm(second_away))
        cross = abs(
            float(
                first_away[0] * second_away[1]
                - first_away[1] * second_away[0]
            )
        )
        if product <= 1e-24 or (
            cross <= product * 1e-12
            and float(np.dot(first_away, second_away)) > 0.0
        ):
            return True

    ordered = sorted(segments, key=lambda segment: segment[3])
    clearance_squared = clearance_mm * clearance_mm
    for offset, first in enumerate(ordered):
        for second in ordered[offset + 1 :]:
            if second[3] > first[4] + clearance_mm:
                break
            if (
                abs(first[0] - second[0]) == 1
                or {first[0], second[0]} == {0, count - 1}
            ):
                continue
            budget.consume()
            if second[5] > first[6] + clearance_mm or first[5] > second[6] + clearance_mm:
                continue
            if _segment_distance_squared(first[1], first[2], second[1], second[2]) <= (
                clearance_squared
            ):
                return True
    return False


def _boundaries_are_too_close(
    first: np.ndarray,
    second: np.ndarray,
    clearance_mm: float,
    budget: _TopologyWorkBudget,
) -> bool:
    first_segments = sorted(_topology_segments(first), key=lambda segment: segment[3])
    second_segments = sorted(_topology_segments(second), key=lambda segment: segment[3])
    clearance_squared = clearance_mm * clearance_mm
    for first_segment in first_segments:
        for second_segment in second_segments:
            if second_segment[3] > first_segment[4] + clearance_mm:
                break
            if second_segment[4] < first_segment[3] - clearance_mm:
                continue
            budget.consume()
            if (
                second_segment[5] > first_segment[6] + clearance_mm
                or first_segment[5] > second_segment[6] + clearance_mm
            ):
                continue
            if _segment_distance_squared(
                first_segment[1],
                first_segment[2],
                second_segment[1],
                second_segment[2],
            ) <= clearance_squared:
                return True
    return False


def _is_ancestor(
    ancestor: int,
    descendant: int,
    contours: tuple[RasterVectorizedContour, ...],
    budget: _TopologyWorkBudget,
) -> bool:
    parent = contours[descendant].parent_index
    while parent is not None:
        budget.consume()
        if parent == ancestor:
            return True
        parent = contours[parent].parent_index
    return False


def _point_inside_closed_polyline(point: np.ndarray, polygon: np.ndarray) -> bool:
    """Return deterministic even/odd containment using float64 coordinates."""

    inside = False
    for start, end in zip(polygon[:-1], polygon[1:], strict=True):
        if (start[1] > point[1]) == (end[1] > point[1]):
            continue
        crossing_x = start[0] + (
            (point[1] - start[1]) * (end[0] - start[0])
            / (end[1] - start[1])
        )
        if point[0] < crossing_x:
            inside = not inside
    return inside


def _topology_is_ambiguous(
    physical: tuple[np.ndarray, ...],
    contours: tuple[RasterVectorizedContour, ...],
    clearance_mm: float,
    budget: _TopologyWorkBudget,
) -> bool:
    if any(
        _self_topology_is_ambiguous(points, clearance_mm, budget)
        for points in physical
    ):
        return True

    bounds = [
        (
            float(np.min(points[:, 0])),
            float(np.min(points[:, 1])),
            float(np.max(points[:, 0])),
            float(np.max(points[:, 1])),
        )
        for points in physical
    ]
    candidates = {
        tuple(sorted((index, contour.parent_index)))
        for index, contour in enumerate(contours)
        if contour.parent_index is not None
    }
    order = sorted(range(len(contours)), key=lambda index: bounds[index][0])
    for offset, first_index in enumerate(order):
        for second_index in order[offset + 1 :]:
            if bounds[second_index][0] > bounds[first_index][2] + clearance_mm:
                break
            budget.consume()
            if (
                bounds[second_index][1] <= bounds[first_index][3] + clearance_mm
                and bounds[first_index][1] <= bounds[second_index][3] + clearance_mm
            ):
                candidates.add((min(first_index, second_index), max(first_index, second_index)))

    for first_index, second_index in sorted(candidates):
        budget.consume()
        first = physical[first_index]
        second = physical[second_index]
        if _boundaries_are_too_close(first, second, clearance_mm, budget):
            return True
        first_contains_second = _point_inside_closed_polyline(second[0], first)
        second_contains_first = _point_inside_closed_polyline(first[0], second)
        first_is_ancestor = _is_ancestor(
            first_index,
            second_index,
            contours,
            budget,
        )
        second_is_ancestor = _is_ancestor(
            second_index,
            first_index,
            contours,
            budget,
        )
        if first_is_ancestor:
            if not first_contains_second or second_contains_first:
                return True
        elif second_is_ancestor:
            if not second_contains_first or first_contains_second:
                return True
        elif first_contains_second or second_contains_first:
            return True
    return False


@_timed_stage("topology_validation")
def _validate_authoritative_native_topology(
    contours: tuple[RasterVectorizedContour, ...],
    width_mm: float,
    height_mm: float,
) -> None:
    for index, contour in enumerate(contours):
        parent = contour.parent_index
        if parent is not None and (parent < 0 or parent >= index):
            raise RasterVectorizationError(
                "Raster contour hierarchy contains an invalid parent relationship"
            )

    geometry = NativePathGeometry(
        tuple(contour.native_subpath for contour in contours),
        fill_rule=PathFillRule.EVENODD,
    )
    transform = _physical_native_transform(width_mm, height_mm)
    budget = _TopologyWorkBudget()
    if _native_self_topology_is_ambiguous(contours, transform):
        raise RasterVectorizationError(
            "Authoritative native-path topology remains ambiguous within a cubic "
            "segment; reduce smoothing or simplification"
        )
    if _native_adjacent_topology_is_ambiguous(contours, transform, budget):
        raise RasterVectorizationError(
            "Authoritative native-path topology remains ambiguous between adjacent "
            "native arcs; reduce smoothing or simplification"
        )
    tolerance_mm = _NATIVE_TOPOLOGY_INITIAL_TOLERANCE_MM
    while True:
        try:
            flattened = flatten_native_path(
                geometry,
                tolerance_mm,
                transform=transform,
                max_points=min(
                    MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION,
                    250_000,
                ),
                max_depth=_MAX_FLATTEN_RECURSION,
            )
        except ValueError as exc:
            raise RasterVectorizationComplexityError(
                "Authoritative native-path topology validation could not be bounded. "
                + _COMPLEXITY_ADVICE
            ) from exc
        physical = tuple(_topology_points(points) for points in flattened)
        numeric_margin = max(
            _NATIVE_FRAME_EPSILON_MM,
            max(width_mm, height_mm) * 1e-12,
        )
        if not _topology_is_ambiguous(
            physical,
            contours,
            2.0 * tolerance_mm + numeric_margin,
            budget,
        ):
            return
        if tolerance_mm <= _NATIVE_TOPOLOGY_MIN_TOLERANCE_MM:
            raise RasterVectorizationError(
                "Authoritative native-path topology remains ambiguous at the bounded "
                "validation tolerance; reduce smoothing or simplification"
            )
        tolerance_mm = max(
            _NATIVE_TOPOLOGY_MIN_TOLERANCE_MM,
            tolerance_mm / 2.0,
        )


def _hierarchy_signature(parents: np.ndarray) -> tuple[bytes, ...]:
    count = len(parents)
    children: list[list[int]] = [[] for _index in range(count)]
    depths = [_hierarchy_depth(index, parents) for index in range(count)]
    for index, raw_parent in enumerate(parents):
        parent = int(raw_parent)
        if parent >= count:
            raise RasterVectorizationError(
                "Raster contour hierarchy contains an out-of-range parent"
            )
        if parent >= 0:
            children[parent].append(index)
    digests = [b""] * count
    for index in sorted(range(count), key=lambda item: depths[item], reverse=True):
        digest = hashlib.sha256()
        digest.update(b"contour")
        for child_digest in sorted(digests[child] for child in children[index]):
            digest.update(child_digest)
        digests[index] = digest.digest()
    return tuple(
        sorted(
            digests[index]
            for index, raw_parent in enumerate(parents)
            if int(raw_parent) < 0
        )
    )


@_timed_stage("raster_hierarchy_validation")
def _validate_rasterized_topology(
    contours: tuple[RasterVectorizedContour, ...],
    shape: tuple[int, int],
) -> None:
    """Rebuild the fitted forest at trace resolution and compare its topology."""

    height, width = shape
    rendered = np.zeros((height, width), dtype=np.uint8)
    ordered = sorted(enumerate(contours), key=lambda item: (item[1].depth, item[0]))
    for _index, contour in ordered:
        normalized = np.asarray(contour.preview_points, dtype=np.float64)
        pixels = np.empty_like(normalized)
        pixels[:, 0] = (normalized[:, 0] + 0.5) * width - 0.5
        pixels[:, 1] = (0.5 - normalized[:, 1]) * height - 0.5
        polygon = np.rint(pixels).astype(np.int64)
        polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
        polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
        cv2.fillPoly(
            rendered,
            [polygon.astype(np.int32).reshape(-1, 1, 2)],
            0 if contour.is_hole else 255,
            lineType=cv2.LINE_8,
        )
    _enforce_oversampled_edge_budget(rendered)
    rebuilt, hierarchy = cv2.findContours(
        rendered,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if hierarchy is None or not rebuilt:
        raise RasterVectorizationError(
            "Vector fitting collapsed the raster contour topology; reduce smoothing "
            "or simplification"
        )
    expected_parents = np.asarray(
        [
            -1 if contour.parent_index is None else contour.parent_index
            for contour in contours
        ],
        dtype=np.int32,
    )
    rebuilt_parents = hierarchy[0, :, 3].astype(np.int32, copy=False)
    if _hierarchy_signature(expected_parents) != _hierarchy_signature(
        rebuilt_parents
    ):
        raise RasterVectorizationError(
            "Vector fitting could not preserve nested contour topology at the 4x "
            "trace resolution; reduce smoothing or simplification"
        )


@_timed_stage("mask_generation")
def _prepare_vectorization_masks(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    width_mm: float,
    height_mm: float,
) -> _RasterMaskPreparation:
    grayscale = _composited_grayscale(source)
    threshold_used = _threshold_value(source, options, grayscale)
    source_mask = _mask_at_resolution(
        source,
        options,
        grayscale,
        threshold_used,
    )
    cleaned_mask, component_count = _clean_components(
        source_mask,
        options.minimum_feature_area_mm2,
        width_mm,
        height_mm,
    )
    _preflight_contour_complexity(cleaned_mask)
    working_mask = _oversampled_mask(
        source,
        options,
        grayscale,
        threshold_used,
        source_mask,
        cleaned_mask,
    )
    _enforce_oversampled_edge_budget(working_mask)
    return _RasterMaskPreparation(
        threshold_used=threshold_used,
        source_mask=_readonly(source_mask),
        cleaned_mask=_readonly(cleaned_mask),
        working_mask=_readonly(working_mask),
        component_count=component_count,
    )


@_timed_stage("contour_extraction")
def _extract_vectorization_contours(
    mask: np.ndarray,
    approximation: int,
) -> tuple[tuple[np.ndarray, ...], np.ndarray]:
    raw_contours, hierarchy = cv2.findContours(
        mask,
        cv2.RETR_TREE,
        approximation,
    )
    if hierarchy is None or not raw_contours:
        raise RasterVectorizationError(
            "No closed contours were produced by the selected raster settings"
        )
    if len(raw_contours) > MAX_RASTER_VECTORIZATION_CONTOURS:
        _raise_complexity(
            "Raster vectorization found "
            f"{len(raw_contours):,} contours, exceeding the "
            f"{MAX_RASTER_VECTORIZATION_CONTOURS:,}-contour limit"
        )
    return tuple(_readonly(contour) for contour in raw_contours), _readonly(hierarchy)


@_timed_stage("quick_preview_total")
def _quick_preview_prepared_raster(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    width_mm: float,
    height_mm: float,
) -> RasterVectorizationQuickPreview:
    masks = _prepare_vectorization_masks(source, options, width_mm, height_mm)
    raw_contours, hierarchy = _extract_vectorization_contours(
        masks.working_mask,
        cv2.CHAIN_APPROX_NONE,
    )
    prepared_trace = _RasterTracePreparation(
        source_identity=source.identity,
        options=options,
        width_mm=width_mm,
        height_mm=height_mm,
        masks=masks,
        raw_contours=raw_contours,
        hierarchy=hierarchy,
    )
    parents = hierarchy[0, :, 3]
    depths = [_hierarchy_depth(index, parents) for index in range(len(raw_contours))]
    selected_indices = sorted(
        (
            index
            for index, depth in enumerate(depths)
            if options.contour_output is RasterContourOutput.ALL_CONTOURS
            or depth == 0
        ),
        key=lambda index: (depths[index], index),
    )
    selected_map = {
        original_index: result_index
        for result_index, original_index in enumerate(selected_indices)
    }
    factor = RASTER_VECTORIZATION_OVERSAMPLE_FACTOR
    pitch_mm = min(
        width_mm / float(source.width_px * factor),
        height_mm / float(source.height_px * factor),
    )
    approximation_px = max(
        0.75,
        min(
            4.0,
            options.simplification_tolerance_mm / max(pitch_mm, 1e-15) * 0.25,
        ),
    )
    contours: list[RasterVectorizationQuickContour] = []
    raw_point_count = 0
    preview_point_count = 0
    for original_index in selected_indices:
        raw = raw_contours[original_index]
        raw_point_count += len(raw)
        approximated = cv2.approxPolyDP(raw, approximation_px, True)
        if len(approximated) < 3:
            approximated = raw
        physical = _physical_contour(
            approximated,
            source.width_px,
            source.height_px,
            width_mm,
            height_mm,
        )
        if len(physical) < 3:
            raise RasterVectorizationError(
                "A retained raster quick-preview contour has fewer than three "
                "distinct points"
            )
        normalized = tuple(
            (float(point[0] / width_mm), float(point[1] / height_mm))
            for point in physical
        )
        preview_point_count += len(normalized)
        if preview_point_count > MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION:
            _raise_complexity(
                "Raster quick preview requires more than "
                f"{MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION:,} points"
            )
        depth = depths[original_index]
        parent_original = int(parents[original_index])
        parent_index = selected_map.get(parent_original) if parent_original >= 0 else None
        contours.append(
            RasterVectorizationQuickContour(
                points=normalized,
                parent_index=parent_index,
                depth=(
                    depth
                    if options.contour_output is RasterContourOutput.ALL_CONTOURS
                    else 0
                ),
                is_hole=(
                    bool(depth % 2)
                    if options.contour_output is RasterContourOutput.ALL_CONTOURS
                    else False
                ),
            )
        )
    if not contours:
        raise RasterVectorizationError(
            "Raster quick preview produced no non-degenerate closed paths"
        )
    return RasterVectorizationQuickPreview(
        source_identity=source.identity,
        source_rgba=source.source_rgba,
        foreground_mask=_readonly(
            _preview_mask(
                masks.working_mask,
                source.width_px,
                source.height_px,
            )
        ),
        contours=tuple(contours),
        threshold_used=masks.threshold_used,
        has_usable_alpha=source.has_usable_alpha,
        connected_component_count=masks.component_count,
        raw_contour_point_count=raw_point_count,
        preview_point_count=preview_point_count,
        _prepared_trace=prepared_trace,
    )


def quick_preview_prepared_raster(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    *,
    displayed_width_mm: float,
    displayed_height_mm: float,
    timing: RasterVectorizationTiming | None = None,
) -> RasterVectorizationQuickPreview:
    """Build bounded display-only mask/outline geometry without fitting."""

    if not isinstance(source, RasterVectorizationSource):
        raise TypeError("source must be a RasterVectorizationSource")
    options = (
        options
        if isinstance(options, RasterVectorizationOptions)
        else RasterVectorizationOptions(**dict(options))
    )
    width_mm, height_mm = _display_dimensions(
        displayed_width_mm,
        displayed_height_mm,
    )
    token = _TIMING_STAGE.set(timing)
    try:
        return _quick_preview_prepared_raster(
            source,
            options,
            width_mm,
            height_mm,
        )
    finally:
        _TIMING_STAGE.reset(token)


def _reusable_prepared_trace(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    width_mm: float,
    height_mm: float,
    prepared_preview: RasterVectorizationQuickPreview | None,
) -> _RasterTracePreparation | None:
    if prepared_preview is None:
        return None
    trace = prepared_preview._prepared_trace
    if (
        trace.source_identity != source.identity
        or trace.options != options
        or trace.width_mm != width_mm
        or trace.height_mm != height_mm
    ):
        return None
    return trace


@_timed_stage("verified_vectorization_total")
def _vectorize_prepared_raster(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    *,
    displayed_width_mm: float,
    displayed_height_mm: float,
    prepared_preview: RasterVectorizationQuickPreview | None = None,
) -> RasterVectorizationResult:
    """Vectorize one verified decoded source without reopening its asset."""

    if not isinstance(source, RasterVectorizationSource):
        raise TypeError("source must be a RasterVectorizationSource")
    options = (
        options
        if isinstance(options, RasterVectorizationOptions)
        else RasterVectorizationOptions(**dict(options))
    )
    width_mm, height_mm = _display_dimensions(
        displayed_width_mm,
        displayed_height_mm,
    )
    prepared_trace = _reusable_prepared_trace(
        source,
        options,
        width_mm,
        height_mm,
        prepared_preview,
    )
    if prepared_trace is None:
        masks = _prepare_vectorization_masks(source, options, width_mm, height_mm)
        raw_contours, hierarchy = _extract_vectorization_contours(
            masks.working_mask,
            cv2.CHAIN_APPROX_NONE,
        )
    else:
        masks = prepared_trace.masks
        raw_contours = prepared_trace.raw_contours
        hierarchy = prepared_trace.hierarchy
    threshold_used = masks.threshold_used
    component_count = masks.component_count
    working_mask = masks.working_mask
    parents = hierarchy[0, :, 3]
    depths = [_hierarchy_depth(index, parents) for index in range(len(raw_contours))]
    total_raw_point_count = sum(len(contour) for contour in raw_contours)
    if (
        total_raw_point_count
        > MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION
    ):
        _raise_complexity(
            "Raster vectorization produced "
            f"{total_raw_point_count:,} raw contour points, exceeding the "
            f"{MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION:,}-point "
            "pre-simplification limit"
        )
    selected_indices = sorted(
        (
        index
        for index, depth in enumerate(depths)
        if options.contour_output is RasterContourOutput.ALL_CONTOURS or depth == 0
        ),
        key=lambda index: (depths[index], index),
    )
    raw_point_count = sum(len(raw_contours[index]) for index in selected_indices)

    selected_map = {
        original_index: result_index
        for result_index, original_index in enumerate(selected_indices)
    }
    budget = _ComplexityBudget()
    results: list[RasterVectorizedContour] = []
    for original_index in selected_indices:
        depth = depths[original_index]
        is_hole = bool(depth % 2)
        physical = _physical_contour(
            raw_contours[original_index],
            source.width_px,
            source.height_px,
            width_mm,
            height_mm,
        )
        if len(physical) < 3:
            raise RasterVectorizationError(
                "A retained raster contour has fewer than three distinct points"
            )
        area = _signed_area(physical)
        if abs(area) <= 1e-15:
            raise RasterVectorizationError(
                "A retained raster contour has zero physical area"
            )
        if (not is_hole and area < 0.0) or (is_hole and area > 0.0):
            physical = physical[::-1].copy()
        hierarchy_entry = hierarchy[0, original_index]
        # Keep every contour that participates in nesting on the exact legacy
        # target. This conservatively preserves hole/parent clearances and the
        # established rejection boundary while source-edge localization is
        # limited to independent stencil components and silhouettes.
        threshold_physical = physical
        source_edge = (
            _unchanged_source_edge(physical)
            if int(hierarchy_entry[2]) >= 0 or int(hierarchy_entry[3]) >= 0
            else _refine_contour_source_edges(
                physical,
                source,
                options,
                threshold_used,
                width_mm,
                height_mm,
            )
        )
        physical = source_edge.points
        fitted = _fit_contour(
            physical,
            options,
            width_mm,
            height_mm,
            budget,
            source_pixel_spacing_mm=(
                width_mm / source.width_px,
                height_mm / source.height_px,
            ),
            classification_points=threshold_physical,
        )
        native_subpath = _native_subpath_from_fitted_contour(
            fitted,
            width_mm,
            height_mm,
        )
        _validate_native_subpath_in_frame(native_subpath, width_mm, height_mm)
        preview_tolerance_mm = options.simplification_tolerance_mm * 0.20
        final_physical = _flatten_native_subpath_for_preview(
            native_subpath,
            preview_tolerance_mm,
            width_mm,
            height_mm,
            budget,
        )
        deviation = (
            source_edge.maximum_displacement_mm
            + fitted.smoothing_displacement_mm
            + fitted.max_fitting_error_mm
            + preview_tolerance_mm
        )
        final_area = _signed_area(final_physical)
        if (not is_hole and final_area < 0.0) or (is_hole and final_area > 0.0):
            native_subpath = reverse_subpath(native_subpath)
            final_physical = final_physical[::-1].copy()
        parent_original = int(parents[original_index])
        parent_index = selected_map.get(parent_original) if parent_original >= 0 else None
        if (
            options.contour_output is RasterContourOutput.ALL_CONTOURS
            and parent_original >= 0
            and parent_index is None
        ):
            raise RasterVectorizationError(
                "Raster contour hierarchy lost a selected parent relationship"
            )
        normalized = tuple(
            (float(point[0] / width_mm), float(point[1] / height_mm))
            for point in final_physical
        )
        results.append(
            RasterVectorizedContour(
                native_subpath=native_subpath,
                preview_points=normalized,
                parent_index=parent_index,
                depth=depth if options.contour_output is RasterContourOutput.ALL_CONTOURS else 0,
                is_hole=is_hole if options.contour_output is RasterContourOutput.ALL_CONTOURS else False,
                raw_point_count=len(raw_contours[original_index]),
                fitted_segment_count=len(native_subpath.segments),
                preview_flattened_point_count=len(normalized),
                max_fitting_error_mm=fitted.max_fitting_error_mm,
                smoothing_displacement_mm=fitted.smoothing_displacement_mm,
                max_estimated_deviation_mm=deviation,
                mean_fitting_error_mm=fitted.mean_fitting_error_mm,
                rms_fitting_error_mm=fitted.rms_fitting_error_mm,
                fitting_error_sample_count=fitted.fitting_error_sample_count,
                hard_corner_count=fitted.hard_corner_count,
                recursive_split_count=fitted.recursive_split_count,
                merged_segment_count=fitted.merged_segment_count,
                longest_smooth_span_segment_count=(
                    fitted.longest_smooth_span_segment_count
                ),
            )
        )
    if not results:
        raise RasterVectorizationError(
            "Raster vectorization produced no non-degenerate closed paths"
        )
    result_contours = tuple(results)
    _validate_authoritative_native_topology(result_contours, width_mm, height_mm)
    _validate_rasterized_topology(result_contours, working_mask.shape)
    mask_preview = _preview_mask(
        working_mask,
        source.width_px,
        source.height_px,
    )
    overlay = _overlay_preview(source.source_rgba, result_contours)
    maximum_deviation = max(
        contour.max_estimated_deviation_mm for contour in result_contours
    )
    return RasterVectorizationResult(
        source_identity=source.identity,
        source_sha256=source.identity.sha256,
        source_rgba=source.source_rgba,
        foreground_mask=_readonly(mask_preview),
        overlay_rgba=_readonly(overlay),
        contours=result_contours,
        threshold_used=threshold_used,
        has_usable_alpha=source.has_usable_alpha,
        connected_component_count=component_count,
        raw_contour_point_count=raw_point_count,
        fitted_segment_count=budget.fitted_segments,
        preview_flattened_point_count=budget.preview_points,
        max_estimated_deviation_mm=maximum_deviation,
    )


def vectorize_prepared_raster(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    *,
    displayed_width_mm: float,
    displayed_height_mm: float,
    prepared_preview: RasterVectorizationQuickPreview | None = None,
    timing: RasterVectorizationTiming | None = None,
) -> RasterVectorizationResult:
    """Vectorize one verified source with optional non-persistent timing."""

    token = _TIMING_STAGE.set(timing)
    try:
        return _vectorize_prepared_raster(
            source,
            options,
            displayed_width_mm=displayed_width_mm,
            displayed_height_mm=displayed_height_mm,
            prepared_preview=prepared_preview,
        )
    finally:
        _TIMING_STAGE.reset(token)


def vectorize_raster_payload(
    payload: RasterAssetPayload,
    options: RasterVectorizationOptions,
    *,
    displayed_width_mm: float,
    displayed_height_mm: float,
    timing: RasterVectorizationTiming | None = None,
) -> RasterVectorizationResult:
    """Verify, decode, and vectorize an exact bounded raster payload."""

    source = prepare_raster_vectorization_source(payload, timing=timing)
    return vectorize_prepared_raster(
        source,
        options,
        displayed_width_mm=displayed_width_mm,
        displayed_height_mm=displayed_height_mm,
        timing=timing,
    )


__all__ = [
    "MAX_RASTER_VECTORIZATION_CONNECTED_COMPONENTS",
    "MAX_RASTER_VECTORIZATION_CONTOURS",
    "MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS",
    "MAX_RASTER_VECTORIZATION_OVERSAMPLED_PIXELS",
    "MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION",
    "MAX_RASTER_VECTORIZATION_POINTS_BEFORE_SIMPLIFICATION",
    "RASTER_VECTORIZATION_OVERSAMPLE_FACTOR",
    "RasterContourOutput",
    "RasterDetectionMode",
    "RasterVectorizationComplexityError",
    "RasterVectorizationError",
    "RasterVectorizationOptions",
    "RasterVectorizationQuickContour",
    "RasterVectorizationQuickPreview",
    "RasterVectorizationResult",
    "RasterVectorizationSource",
    "RasterVectorizationTiming",
    "RasterVectorizedContour",
    "prepare_raster_vectorization_source",
    "quick_preview_prepared_raster",
    "raster_payload_has_usable_alpha",
    "vectorize_prepared_raster",
    "vectorize_raster_payload",
]
