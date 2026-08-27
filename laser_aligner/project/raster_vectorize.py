from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from pathlib import Path
from typing import TypeAlias

import cv2
import numpy as np

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

_CORNER_MINIMUM_TURN_DEGREES = 35.0
_CORNER_INNER_MINIMUM_TURN_DEGREES = 22.5
_CORNER_MINIMUM_SCALE_AGREEMENT = 0.60
_STRAIGHT_MAXIMUM_TURN_DEGREES = 6.0
_MAX_CORNER_WINDOW_POINTS = 12
_MAX_SMOOTHING_RADIUS_POINTS = 64
_MAX_FIT_TANGENT_WINDOW_POINTS = 12
_LINE_MAXIMUM_TANGENT_DEVIATION_DEGREES = 2.0
_MAX_FIT_RECURSION = 18
_MAX_FLATTEN_RECURSION = 18

_COMPLEXITY_ADVICE = (
    "Increase the minimum feature size, increase simplification, adjust the "
    "threshold, or use cleaner source artwork."
)


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
    alpha: np.ndarray
    has_usable_alpha: bool

    @property
    def width_px(self) -> int:
        return int(self.source_rgba.shape[1])

    @property
    def height_px(self) -> int:
        return int(self.source_rgba.shape[0])


@dataclass(frozen=True, slots=True)
class RasterVectorizedContour:
    points: tuple[tuple[float, float], ...]
    parent_index: int | None
    depth: int
    is_hole: bool
    raw_point_count: int
    fitted_segment_count: int
    final_point_count: int
    max_estimated_deviation_mm: float

    def __post_init__(self) -> None:
        points = tuple(
            (
                _finite(point[0], "contour point x"),
                _finite(point[1], "contour point y"),
            )
            for point in self.points
        )
        if len(points) < 3:
            raise ValueError("A vectorized contour requires at least three points")
        if any(abs(value) > 0.500000001 for point in points for value in point):
            raise ValueError("Vectorized contour points must remain in the image frame")
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
            (self.final_point_count, "final_point_count", 3),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"contour {label} must be an integer >= {minimum}")
        if self.final_point_count != len(points):
            raise ValueError("contour final_point_count must match its point data")
        deviation = _finite(
            self.max_estimated_deviation_mm,
            "contour max_estimated_deviation_mm",
        )
        if deviation < 0.0:
            raise ValueError("contour estimated deviation cannot be negative")
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "max_estimated_deviation_mm", deviation)


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
    final_point_count: int
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
            (self.final_point_count, "final_point_count", 3),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{label} must be an integer >= {minimum}")
        expected_counts = (
            sum(contour.raw_point_count for contour in contours),
            sum(contour.fitted_segment_count for contour in contours),
            sum(contour.final_point_count for contour in contours),
        )
        if expected_counts != (
            self.raw_contour_point_count,
            self.fitted_segment_count,
            self.final_point_count,
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
    def polylines(self) -> tuple[dict[str, object], ...]:
        """Return fresh mappings accepted by ``SceneObject`` PATH geometry."""

        return tuple(
            {
                "points": [[float(x), float(y)] for x, y in contour.points],
                "closed": True,
            }
            for contour in self.contours
        )

    def project_polylines(self) -> list[dict[str, object]]:
        """Return JSON-ready PATH polylines without sharing mutable result state."""

        return [
            {
                "points": [[float(x), float(y)] for x, y in contour.points],
                "closed": True,
            }
            for contour in self.contours
        ]

    def metadata(self) -> dict[str, object]:
        """Return a compact JSON-ready provenance and quality summary."""

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
            "raster_vectorization_final_e3_points": self.final_point_count,
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


@dataclass(slots=True)
class _ComplexityBudget:
    fitted_segments: int = 0
    final_points: int = 0

    def add_fitted_segments(self, count: int = 1) -> None:
        if self.fitted_segments + count > MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS:
            _raise_complexity(
                "Raster vectorization requires more than "
                f"{MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS:,} fitted segments"
            )
        self.fitted_segments += count

    def add_final_points(self, count: int = 1) -> None:
        if self.final_points + count > MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION:
            _raise_complexity(
                "Raster vectorization requires more than "
                f"{MAX_RASTER_VECTORIZATION_POINTS_AFTER_SIMPLIFICATION:,} final E3 points"
            )
        self.final_points += count


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
    alpha_min = int(np.min(alpha))
    alpha_max = int(np.max(alpha))
    has_usable_alpha = alpha_min < alpha_max and alpha_min < 255 and alpha_max > 0
    return RasterVectorizationSource(
        identity=payload.identity,
        source_rgba=_readonly(rgba),
        grayscale=_readonly(grayscale),
        alpha=_readonly(alpha),
        has_usable_alpha=has_usable_alpha,
    )


def prepare_raster_vectorization_source(
    payload: RasterAssetPayload,
) -> RasterVectorizationSource:
    """Verify and decode one exact bounded payload for repeated preview work."""

    _validate_payload(payload)
    return _decode_payload(payload)


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
    opacity = source.alpha.astype(np.float32) / 255.0
    output = np.rint(
        source.grayscale.astype(np.float32) * opacity
        + 255.0 * (1.0 - opacity)
    )
    return output.astype(np.uint8)


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
) -> list[int]:
    """Choose hard corners or deterministic physical arc-length soft anchors."""

    if isinstance(points_or_count, np.ndarray):
        points = np.asarray(points_or_count, dtype=np.float64)
        point_count = len(points)
    else:
        points = None
        point_count = int(points_or_count)
    if point_count < 4:
        return list(range(point_count))
    anchors = sorted(set(int(index) % point_count for index in corners))
    if len(anchors) >= 2:
        return anchors

    if points is not None and not anchors:
        straight_anchors = _long_straight_run_anchors(
            points,
            0.0 if tolerance_mm is None else max(0.0, float(tolerance_mm)),
        )
        if straight_anchors:
            return straight_anchors

    target_count = 4 if not anchors else 2
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
) -> list[int]:
    """Anchor every persistent straight run and subdivide the curved gaps."""

    if len(points) < 8:
        return []
    positions, steps, perimeter = _closed_arc_positions(points)
    positive_steps = steps[steps > 1e-12]
    if not len(positive_steps) or perimeter <= 1e-12:
        return []
    step_mm = float(np.median(positive_steps))
    window_mm = min(
        perimeter / 16.0,
        max(8.0 * step_mm, 2.5 * tolerance_mm),
    )
    turns, _orientation, residuals = _corner_scale_metrics(
        points,
        positions,
        steps,
        perimeter,
        window_mm,
    )
    residual_allowance = max(0.75 * step_mm, 0.25 * tolerance_mm)
    straight_mask = (
        turns <= math.radians(_STRAIGHT_MAXIMUM_TURN_DEGREES)
    ) & (residuals <= residual_allowance)
    minimum_run_length = max(4.0 * tolerance_mm, 0.10 * perimeter)
    straight_runs = [
        run
        for run in _circular_true_runs(straight_mask)
        if _run_arc_length(run, steps) >= minimum_run_length
    ]
    if not straight_runs:
        return []

    run_endpoints = sorted(
        {int(run[0]) for run in straight_runs}
        | {int(run[-1]) for run in straight_runs}
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


def _fit_cubic(
    points: np.ndarray,
    tolerance_mm: float,
    start_tangent: np.ndarray | None = None,
    end_tangent: np.ndarray | None = None,
    control_minimum: np.ndarray | None = None,
    control_maximum: np.ndarray | None = None,
) -> _CubicSegment:
    polyline_length = float(np.sum(np.linalg.norm(np.diff(points, axis=0), axis=1)))
    parameters = _chord_parameters(points)
    if polyline_length <= 1e-15:
        return _CubicSegment(points[0], points[0], points[-1], points[-1], 0.0)
    if start_tangent is None:
        start_tangent = _endpoint_tangent(points, at_start=True)
    if end_tangent is None:
        end_tangent = _endpoint_tangent(points, at_start=False)
    start_tangent = _unit_direction(start_tangent)
    end_tangent = _unit_direction(end_tangent)
    if start_tangent is None or end_tangent is None:
        raise RasterVectorizationError("A fitted span has an undefined endpoint tangent")

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
    control_distances, _residuals, rank, _singular = np.linalg.lstsq(
        matrix,
        residual.reshape(-1),
        rcond=None,
    )
    chord_length = float(np.linalg.norm(points[-1] - points[0]))
    fallback_distance = chord_length / 3.0
    if (
        rank < 2
        or not np.all(np.isfinite(control_distances))
        or np.any(control_distances <= chord_length * 1e-6)
    ):
        control_distances = np.asarray(
            (fallback_distance, fallback_distance),
            dtype=np.float64,
        )
    minimum = np.min(points, axis=0) - tolerance_mm
    maximum = np.max(points, axis=0) + tolerance_mm
    if control_minimum is not None:
        minimum = np.maximum(minimum, control_minimum)
    if control_maximum is not None:
        maximum = np.minimum(maximum, control_maximum)
    control_distances[0] = min(
        float(control_distances[0]),
        _maximum_control_distance(points[0], start_tangent, minimum, maximum),
    )
    control_distances[1] = min(
        float(control_distances[1]),
        _maximum_control_distance(points[-1], end_tangent, minimum, maximum),
    )
    control_1 = points[0] + control_distances[0] * start_tangent
    control_2 = points[-1] + control_distances[1] * end_tangent
    fitted = _cubic_values(
        points[0],
        control_1,
        control_2,
        points[-1],
        parameters,
    )
    error = float(np.max(np.linalg.norm(fitted - points, axis=1)))
    if len(points) == 2:
        curve_samples = _cubic_values(
            points[0],
            control_1,
            control_2,
            points[-1],
            np.linspace(0.0, 1.0, 9),
        )
        error = max(
            error,
            float(
                np.max(
                    _distance_to_segment(curve_samples, points[0], points[-1])
                )
            ),
        )
    return _CubicSegment(
        points[0].copy(),
        control_1.copy(),
        control_2.copy(),
        points[-1].copy(),
        error,
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
    if len(points) <= 2:
        if prefer_cubic_leaves and len(points) == 2:
            cubic = _fit_cubic(
                points,
                tolerance_mm,
                start_tangent,
                end_tangent,
                control_minimum,
                control_maximum,
            )
            if cubic.fitting_error_mm <= tolerance_mm:
                budget.add_fitted_segments()
                return [cubic]
        budget.add_fitted_segments()
        return [_LineSegment(points[0].copy(), points[-1].copy(), 0.0)]
    if start_tangent is None:
        start_tangent = _endpoint_tangent(points, at_start=True)
    if end_tangent is None:
        end_tangent = _endpoint_tangent(points, at_start=False)
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
    line_errors = _distance_to_segment(points, points[0], points[-1])
    line_error = float(np.max(line_errors))
    if not closed_span:
        if line_error <= tolerance_mm and (
            allow_unconstrained_line
            or _line_matches_tangents(
                points[0],
                points[-1],
                start_tangent,
                end_tangent,
            )
        ):
            budget.add_fitted_segments()
            return [_LineSegment(points[0].copy(), points[-1].copy(), line_error)]

        cubic = _fit_cubic(
            points,
            tolerance_mm,
            start_tangent,
            end_tangent,
            control_minimum,
            control_maximum,
        )
        if cubic.fitting_error_mm <= tolerance_mm:
            budget.add_fitted_segments()
            return [cubic]
        if depth >= _MAX_FIT_RECURSION:
            segments: list[_FittedSegment] = []
            for start, end in zip(points[:-1], points[1:], strict=True):
                budget.add_fitted_segments()
                segments.append(_LineSegment(start.copy(), end.copy(), 0.0))
            return segments

        distances = np.linalg.norm(
            _cubic_values(
                cubic.start,
                cubic.control_1,
                cubic.control_2,
                cubic.end,
                _chord_parameters(points),
            )
            - points,
            axis=1,
        )
        split = int(np.argmax(distances))
        split = max(1, min(len(points) - 2, split))
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
    return [
        *_fit_span(
            points[: split + 1],
            tolerance_mm,
            budget,
            depth + 1,
            start_tangent=start_tangent,
            end_tangent=-center_tangent,
            prefer_cubic_leaves=prefer_cubic_leaves,
            control_minimum=control_minimum,
            control_maximum=control_maximum,
        ),
        *_fit_span(
            points[split:],
            tolerance_mm,
            budget,
            depth + 1,
            start_tangent=center_tangent,
            end_tangent=end_tangent,
            prefer_cubic_leaves=prefer_cubic_leaves,
            control_minimum=control_minimum,
            control_maximum=control_maximum,
        ),
    ]


def _control_flatness(segment: _CubicSegment) -> float:
    return float(
        max(
            _distance_to_segment(
                np.vstack((segment.control_1, segment.control_2)),
                segment.start,
                segment.end,
            )
        )
    )


def _split_cubic(segment: _CubicSegment) -> tuple[_CubicSegment, _CubicSegment]:
    p01 = (segment.start + segment.control_1) / 2.0
    p12 = (segment.control_1 + segment.control_2) / 2.0
    p23 = (segment.control_2 + segment.end) / 2.0
    p012 = (p01 + p12) / 2.0
    p123 = (p12 + p23) / 2.0
    middle = (p012 + p123) / 2.0
    return (
        _CubicSegment(
            segment.start,
            p01,
            p012,
            middle,
            segment.fitting_error_mm,
        ),
        _CubicSegment(
            middle,
            p123,
            p23,
            segment.end,
            segment.fitting_error_mm,
        ),
    )


def _flatten_segment(
    segment: _FittedSegment,
    tolerance_mm: float,
    budget: _ComplexityBudget,
    output: list[np.ndarray],
    depth: int = 0,
) -> float:
    if isinstance(segment, _LineSegment):
        budget.add_final_points()
        output.append(segment.end.copy())
        return 0.0
    flatness = _control_flatness(segment)
    if flatness <= tolerance_mm:
        budget.add_final_points()
        output.append(segment.end.copy())
        return flatness
    if depth >= _MAX_FLATTEN_RECURSION:
        _raise_complexity(
            "A fitted curve could not be flattened within the bounded recursion limit"
        )
    first, second = _split_cubic(segment)
    return max(
        _flatten_segment(first, tolerance_mm, budget, output, depth + 1),
        _flatten_segment(second, tolerance_mm, budget, output, depth + 1),
    )


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


def _fit_and_flatten_contour(
    raw_points: np.ndarray,
    options: RasterVectorizationOptions,
    width_mm: float,
    height_mm: float,
    budget: _ComplexityBudget,
) -> tuple[np.ndarray, int, float]:
    raw_points = _canonicalize_closed_contour(raw_points)
    corner_tolerance = options.simplification_tolerance_mm * 0.65
    fit_tolerance = options.simplification_tolerance_mm * 0.80
    corners = _corner_indices(raw_points, corner_tolerance)
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
    if (
        budget.fitted_segments + len(corner_set)
        > MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS
    ):
        _raise_complexity(
            "Raster vectorization requires more than "
            f"{MAX_RASTER_VECTORIZATION_FITTED_SEGMENTS:,} fitted segments"
        )
    smoothed, smoothing_displacement = _smooth_contour(
        raw_points,
        corners,
        options.smoothing_mm,
    )
    anchors = _fitting_anchors(
        smoothed,
        sorted(corner_set),
        fit_tolerance,
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
    flatten_tolerance = options.simplification_tolerance_mm * 0.20
    control_minimum = np.asarray((-width_mm / 2.0, -height_mm / 2.0))
    control_maximum = np.asarray((width_mm / 2.0, height_mm / 2.0))
    segments: list[_FittedSegment] = []
    spans = _contour_spans(smoothed, anchors)
    span_line_errors = [
        float(np.max(_distance_to_segment(span, span[0], span[-1])))
        for span in spans
    ]
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
            and span_line_errors[offset] <= fit_tolerance
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
            and span_line_errors[offset] <= fit_tolerance
        )
        segments.extend(
            _fit_span(
                span,
                fit_tolerance,
                budget,
                start_tangent=start_tangent,
                end_tangent=end_tangent,
                prefer_cubic_leaves=not straight_hard_span,
                allow_unconstrained_line=straight_hard_span,
                control_minimum=control_minimum,
                control_maximum=control_maximum,
            )
        )
    if not segments:
        raise RasterVectorizationError("A contour could not be fitted to vector geometry")

    output = [segments[0].start.copy()]
    budget.add_final_points()
    maximum_flatness = 0.0
    for segment in segments:
        maximum_flatness = max(
            maximum_flatness,
            _flatten_segment(
                segment,
                flatten_tolerance,
                budget,
                output,
            ),
        )
    points = np.asarray(output, dtype=np.float64)
    if len(points) > 1 and np.linalg.norm(points[0] - points[-1]) <= 1e-9:
        points = points[:-1]
        budget.final_points -= 1
    if len(points) < 3:
        raise RasterVectorizationError(
            "Simplification collapsed a closed contour below three points"
        )
    unclipped = points.copy()
    points[:, 0] = np.clip(points[:, 0], -width_mm / 2.0, width_mm / 2.0)
    points[:, 1] = np.clip(points[:, 1], -height_mm / 2.0, height_mm / 2.0)
    clipping_displacement = float(
        np.max(np.linalg.norm(points - unclipped, axis=1))
    )
    keep = np.ones(len(points), dtype=bool)
    keep[1:] = np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-12
    points = points[keep]
    if len(points) > 1 and np.linalg.norm(points[0] - points[-1]) <= 1e-12:
        points = points[:-1]
    removed_points = len(keep) - len(points)
    if removed_points:
        budget.final_points -= removed_points
    if len(points) < 3 or abs(_signed_area(points)) <= 1e-15:
        raise RasterVectorizationError(
            "Fitting at the selected tolerance collapsed a closed contour; reduce "
            "smoothing or simplification"
        )
    fitting_error = max(segment.fitting_error_mm for segment in segments)
    maximum_deviation = (
        smoothing_displacement
        + fitting_error
        + maximum_flatness
        + clipping_displacement
    )
    return points, len(segments), maximum_deviation


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
                for x, y in contour.points
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


def _validate_topology(
    contours: tuple[RasterVectorizedContour, ...],
    width_mm: float,
    height_mm: float,
    tolerance_mm: float,
) -> None:
    physical = [
        np.asarray(
            [[x * width_mm, y * height_mm] for x, y in contour.points],
            dtype=np.float32,
        )
        for contour in contours
    ]
    for index, contour in enumerate(contours):
        parent = contour.parent_index
        if parent is None:
            continue
        if parent < 0 or parent >= index:
            raise RasterVectorizationError(
                "Raster contour hierarchy contains an invalid parent relationship"
            )
        probe = tuple(float(value) for value in physical[index][0])
        inside = cv2.pointPolygonTest(physical[parent], probe, True)
        if inside < -max(1e-6, tolerance_mm):
            raise RasterVectorizationError(
                "Vector fitting could not preserve nested contour topology; reduce "
                "smoothing or simplification"
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


def _validate_rasterized_topology(
    contours: tuple[RasterVectorizedContour, ...],
    shape: tuple[int, int],
) -> None:
    """Rebuild the fitted forest at trace resolution and compare its topology."""

    height, width = shape
    rendered = np.zeros((height, width), dtype=np.uint8)
    ordered = sorted(enumerate(contours), key=lambda item: (item[1].depth, item[0]))
    for _index, contour in ordered:
        normalized = np.asarray(contour.points, dtype=np.float64)
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


def vectorize_prepared_raster(
    source: RasterVectorizationSource,
    options: RasterVectorizationOptions,
    *,
    displayed_width_mm: float,
    displayed_height_mm: float,
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
    raw_contours, hierarchy = cv2.findContours(
        working_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_NONE,
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
        final_physical, segment_count, deviation = _fit_and_flatten_contour(
            physical,
            options,
            width_mm,
            height_mm,
            budget,
        )
        final_area = _signed_area(final_physical)
        if (not is_hole and final_area < 0.0) or (is_hole and final_area > 0.0):
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
                points=normalized,
                parent_index=parent_index,
                depth=depth if options.contour_output is RasterContourOutput.ALL_CONTOURS else 0,
                is_hole=is_hole if options.contour_output is RasterContourOutput.ALL_CONTOURS else False,
                raw_point_count=len(raw_contours[original_index]),
                fitted_segment_count=segment_count,
                final_point_count=len(normalized),
                max_estimated_deviation_mm=deviation,
            )
        )
    if not results:
        raise RasterVectorizationError(
            "Raster vectorization produced no non-degenerate closed paths"
        )
    result_contours = tuple(results)
    _validate_topology(
        result_contours,
        width_mm,
        height_mm,
        options.simplification_tolerance_mm,
    )
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
        final_point_count=budget.final_points,
        max_estimated_deviation_mm=maximum_deviation,
    )


def vectorize_raster_payload(
    payload: RasterAssetPayload,
    options: RasterVectorizationOptions,
    *,
    displayed_width_mm: float,
    displayed_height_mm: float,
) -> RasterVectorizationResult:
    """Verify, decode, and vectorize an exact bounded raster payload."""

    source = prepare_raster_vectorization_source(payload)
    return vectorize_prepared_raster(
        source,
        options,
        displayed_width_mm=displayed_width_mm,
        displayed_height_mm=displayed_height_mm,
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
    "RasterVectorizationResult",
    "RasterVectorizationSource",
    "RasterVectorizedContour",
    "prepare_raster_vectorization_source",
    "raster_payload_has_usable_alpha",
    "vectorize_prepared_raster",
    "vectorize_raster_payload",
]
