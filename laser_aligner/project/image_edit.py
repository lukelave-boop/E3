"""Reversible, source-frame image edits shared by preview and engraving.

No image is decoded here. Callers retain one decoded, white-composited source.
Coordinates use the ordinary native-path frame: centred, X right and Y up.
Excluded pixels are an independent eligibility plane, never just white paint.
"""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Mapping
from functools import lru_cache
from typing import Any

import cv2
import numpy as np

from ..geometry.foreground import (
    clean_foreground_components,
)
from .image_mask_contours import mask_contours
from .path_geometry import (
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    flatten_native_path,
)
from .raster_cleanup import background_mask, cleanup_settings

EDIT_VERSION = 1
MAX_REGIONS = 10_000
MAX_REGION_POINTS = 250_000
DEFAULT_TRACE = {
    "tolerance": 20,
    "detail": 0.5,
    "smoothing": 0.0,
    "minimum_component_area": 8,
    "include_holes": False,
}


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
    return result


def trace_settings(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_TRACE)
    if value is not None:
        if not isinstance(value, Mapping) or set(value) - set(result):
            raise ValueError("Unsupported image trace settings")
        result.update(value)
    for name, lower, upper in (("tolerance", 0, 255), ("minimum_component_area", 1, 100_000_000)):
        if type(result[name]) is not int or not lower <= result[name] <= upper:
            raise ValueError(f"Trace {name} must be an integer from {lower} to {upper}")
    result["detail"] = _number(result["detail"], "Trace detail", 0.05, 20)
    result["smoothing"] = _number(result["smoothing"], "Trace smoothing", 0, 10)
    if type(result["include_holes"]) is not bool:
        raise ValueError("Trace include_holes must be a boolean")
    return result


def default_edit(source_sha256: str, source_size: tuple[int, int] | list[int]) -> dict[str, Any]:
    result = {
        "version": EDIT_VERSION,
        "source_sha256": source_sha256,
        "source_size": list(source_size),
        "outline": None,
        "mask_enabled": True,
        "regions": [],
        "invert": False,
        "trace": dict(DEFAULT_TRACE),
    }
    validate_edit(result)
    return result


def validate_edit(value: Any) -> None:
    """Validate persisted settings, without repairing or rewriting user paths.

    Draft paths may be geometrically invalid so they can be reopened and fixed.
    Applying them as masks or creating vectors requires validate_outline too.
    """
    from .raster_asset import MAX_RASTER_DECODED_BYTES, MAX_RASTER_DIMENSION

    keys = {"version", "source_sha256", "source_size", "outline", "mask_enabled", "regions", "invert", "trace"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("Image edit settings contain missing or unsupported fields")
    if type(value["version"]) is not int or value["version"] != EDIT_VERSION:
        raise ValueError("Unsupported image edit settings version")
    if not isinstance(value["source_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["source_sha256"]):
        raise ValueError("Image edits require the original source SHA256")
    size = value["source_size"]
    if (not isinstance(size, list) or len(size) != 2
            or any(type(n) is not int or not 1 <= n <= MAX_RASTER_DIMENSION for n in size)):
        raise ValueError("Image edit source_size must contain two positive image dimensions")
    if size[0] * size[1] * 4 > MAX_RASTER_DECODED_BYTES:
        raise ValueError("Image edit source dimensions exceed the bounded raster decode limit")
    for name in ("mask_enabled", "invert"):
        if type(value[name]) is not bool:
            raise ValueError(f"Image edit {name} must be a boolean")
    if value["outline"] is not None:
        path = NativePathGeometry.from_dict(value["outline"])
        if path.fill_rule != PathFillRule.EVENODD:
            raise ValueError("Image outlines require even-odd fill")
        for subpath in path.subpaths:
            points = [subpath.start]
            for segment in subpath.segments:
                points.append(segment.to)
                if isinstance(segment, PathCubicSegment):
                    points.extend((segment.control_1, segment.control_2))
            if any(abs(coordinate) > 2 for point in points for coordinate in point):
                raise ValueError("Image outline coordinates exceed the editable source frame")
    trace_settings(value["trace"])
    regions = value["regions"]
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise ValueError("Image edit regions exceed the bounded edit limit")
    count = 0
    for region in regions:
        if not isinstance(region, dict) or set(region) != {"operation", "kind", "points", "radius"}:
            raise ValueError("Image region requires operation, kind, points and radius")
        if region["operation"] not in ("add", "subtract", "restore"):
            raise ValueError("Image region operation must be add, subtract or restore")
        kind = region["kind"]
        if kind not in ("rectangle", "ellipse", "polygon", "brush"):
            raise ValueError("Unsupported image selection kind")
        points = region["points"]
        minimum = 3 if kind == "polygon" else (1 if kind == "brush" else 2)
        if not isinstance(points, list) or len(points) < minimum:
            raise ValueError(f"Image {kind} requires at least {minimum} points")
        if kind in ("rectangle", "ellipse") and len(points) != 2:
            raise ValueError(f"Image {kind} requires exactly two corners")
        count += len(points)
        if count > MAX_REGION_POINTS:
            raise ValueError("Image selections exceed the bounded point limit")
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("Image selection point requires X and Y")
            for coordinate in point:
                _number(coordinate, "Image selection coordinate", -2, 2)
        _number(region["radius"], "Brush radius", 0.00001 if kind == "brush" else 0, 2)


def edit_settings(geometry: Mapping[str, Any]) -> dict[str, Any] | None:
    if "image_edit" not in geometry:
        return None
    value = geometry["image_edit"]
    validate_edit(value)
    return copy.deepcopy(value)


# Explicit persistence-hook name; retain validate_edit for dialog callers.
validate_image_edit = validate_edit


def _gray(gray: np.ndarray) -> None:
    from .raster_asset import MAX_RASTER_DECODED_BYTES, MAX_RASTER_DIMENSION

    if not isinstance(gray, np.ndarray) or gray.dtype != np.uint8 or gray.ndim != 2 or not gray.size:
        raise ValueError("Image editing requires a nonempty 8-bit grayscale source")
    if max(gray.shape) > MAX_RASTER_DIMENSION or gray.size * 4 > MAX_RASTER_DECODED_BYTES:
        raise ValueError("Image editing exceeds the bounded raster decode limit")


def _path(value: NativePathGeometry | Mapping[str, Any]) -> NativePathGeometry:
    return value if isinstance(value, NativePathGeometry) else NativePathGeometry.from_dict(value)


def validate_outline(
    value: NativePathGeometry | Mapping[str, Any], *, width_mm: float = 1.0, height_mm: float = 1.0,
) -> tuple[str, ...]:
    """Report invalid contours using the shared vectorizer topology checks.

    Curves are flattened to a bounded 1e-5 relative frame tolerance; cubic
    self-loops are additionally checked analytically. No repair is attempted.
    """
    from .raster_vectorize import (
        _adjacent_control_arcs_share_only_endpoint,
        _boundaries_are_too_close,
        _control_arcs_are_disjoint,
        _cubic_self_topology_is_ambiguous,
        _native_control_arcs,
        _self_topology_is_ambiguous,
        _TopologyWorkBudget,
    )

    errors: list[str] = []
    try:
        width = _number(width_mm, "Outline width", 1e-9, 1e9)
        height = _number(height_mm, "Outline height", 1e-9, 1e9)
        path = _path(value)
        if path.fill_rule != PathFillRule.EVENODD:
            errors.append("Outline must use even-odd fill")
        affine = PathAffineTransform(m11=width, m22=height)
        budget = _TopologyWorkBudget()
        flattened = flatten_native_path(path, min(width, height) * 1e-5, transform=affine)
        arrays: list[np.ndarray] = []
        native_arcs: list[tuple[int, int, int, np.ndarray]] = []
        seen_segments: set[tuple[tuple[float, float], ...]] = set()
        for index, (subpath, points) in enumerate(zip(path.subpaths, flattened, strict=True)):
            label = f"Contour {index + 1}"
            if not subpath.closed:
                errors.append(f"{label} is open")
                continue
            arcs = _native_control_arcs(subpath, affine)
            native_arcs.extend((index, position, len(arcs), arc) for position, arc in enumerate(arcs))
            current = subpath.start
            for segment in subpath.segments:
                controls = [current, segment.to]
                if isinstance(segment, PathCubicSegment):
                    controls = [current, segment.control_1, segment.control_2, segment.to]
                    if _cubic_self_topology_is_ambiguous(current, segment, affine):
                        errors.append(f"{label} contains a self-intersecting curve")
                key = tuple(controls)
                if key in seen_segments or tuple(reversed(key)) in seen_segments:
                    errors.append(f"{label} contains duplicate segments")
                seen_segments.add(key)
                if all(point == controls[0] for point in controls):
                    errors.append(f"{label} contains a zero-length segment")
                current = segment.to
            array = np.asarray(points, dtype=np.float64)
            if len(array) < 4 or abs(_signed_area(array[:-1])) <= min(width, height) ** 2 * 1e-12:
                errors.append(f"{label} is collapsed or has zero area")
            if _self_topology_is_ambiguous(array, min(width, height) * 1e-9, budget):
                errors.append(f"{label} self-intersects, retraces or has duplicate segments")
            for previous in arrays:
                if _boundaries_are_too_close(previous, array, min(width, height) * 1e-9, budget):
                    errors.append(f"{label} intersects or touches another contour")
                    break
            arrays.append(array)
        # Native hull subdivision catches crossings too small to survive the
        # display-flattening tolerance. The vectorizer bounds this work and
        # treats an unresolved relationship as invalid instead of repairing it.
        ordered_arcs = sorted(native_arcs, key=lambda entry: float(np.min(entry[3][:, 0])))
        for position, first in enumerate(ordered_arcs):
            max_x = float(np.max(first[3][:, 0]))
            for second in ordered_arcs[position + 1:]:
                if float(np.min(second[3][:, 0])) > max_x:
                    break
                if first[0] == second[0] and (first[1] + 1) % first[2] == second[1]:
                    separate = _adjacent_control_arcs_share_only_endpoint(first[3], second[3], budget)
                elif first[0] == second[0] and (second[1] + 1) % second[2] == first[1]:
                    separate = _adjacent_control_arcs_share_only_endpoint(second[3], first[3], budget)
                else:
                    separate = _control_arcs_are_disjoint(first[3], second[3], budget, 0)
                if not separate:
                    errors.append("Native curves intersect, retrace or have unresolved topology")
                    break
    except (ValueError, TypeError, RuntimeError) as exc:
        errors.append(str(exc))
    return tuple(dict.fromkeys(errors))


def _require_outline(path: NativePathGeometry) -> None:
    errors = validate_outline(path)
    if errors:
        raise ValueError("; ".join(errors))


def rasterize_outline(value: NativePathGeometry | Mapping[str, Any], shape: tuple[int, int]) -> np.ndarray:
    path = _path(value)
    return _rasterized_outline(path, shape)


@lru_cache(maxsize=8)
def _rasterized_outline(path: NativePathGeometry, shape: tuple[int, int]) -> np.ndarray:
    """Keep unchanged outlines reusable while brush regions are committed."""
    _require_outline(path)
    height, width = shape
    affine = PathAffineTransform(m11=width, m22=-height, dx=width / 2 - 0.5, dy=height / 2 - 0.5)
    lines = flatten_native_path(path, 0.1, transform=affine)
    mask = np.zeros(shape, dtype=np.uint8)
    contours = [np.rint(np.asarray(line) * 256).astype(np.int32) for line in lines]
    cv2.fillPoly(mask, contours, 255, lineType=cv2.LINE_8, shift=8)
    output = mask != 0
    output.setflags(write=False)
    return output


def region_mask(region: Mapping[str, Any], shape: tuple[int, int]) -> np.ndarray:
    """Rasterize one already validated edit; useful for incremental brush UI."""
    height, width = shape
    points = np.asarray(region["points"], dtype=float)
    pixels = np.column_stack(((points[:, 0] + 0.5) * width - 0.5, (0.5 - points[:, 1]) * height - 0.5))
    fixed = np.rint(pixels * 256).astype(np.int32)
    output = np.zeros(shape, dtype=np.uint8)
    kind = region["kind"]
    if kind == "rectangle":
        cv2.rectangle(output, tuple(fixed[0]), tuple(fixed[1]), 255, -1, shift=8)
    elif kind == "ellipse":
        center = tuple(np.rint((fixed[0] + fixed[1]) / 2).astype(int))
        axes = tuple(np.rint(np.abs(fixed[1] - fixed[0]) / 2).astype(int))
        cv2.ellipse(output, center, axes, 0, 0, 360, 255, -1, shift=8)
    elif kind == "polygon":
        cv2.fillPoly(output, [fixed], 255, shift=8)
    elif kind == "brush":
        radius = max(1, round(float(region["radius"]) * min(width, height) * 256))
        for point in fixed:
            cv2.circle(output, tuple(point), radius, 255, -1, shift=8)
        if len(fixed) > 1:
            cv2.polylines(output, [fixed], False, 255, max(1, round(radius * 2 / 256)), shift=8)
    else:
        raise ValueError("Unsupported image selection kind")
    return output != 0


def process_image(
    gray: np.ndarray, geometry: Mapping[str, Any], *, source_allowed: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply all reversible edits, preserving source dimensions and eligible pixels.

    Always propagate the returned boolean eligibility plane through resampling
    and reapply it after inversion/dithering in generated output. A white preview
    alone is insufficient because later inversion can turn white into power.
    """
    _gray(gray)
    if source_allowed is not None and (
        not isinstance(source_allowed, np.ndarray) or source_allowed.shape != gray.shape
        or source_allowed.dtype != np.bool_
    ):
        raise ValueError("Source eligibility must be a boolean mask matching the original image")
    settings = edit_settings(geometry)
    allowed = np.ones(gray.shape, dtype=bool)
    enabled, tolerance = cleanup_settings(geometry)
    if enabled:
        allowed &= ~background_mask(gray, tolerance)
    if settings is not None:
        if settings["source_size"] != [gray.shape[1], gray.shape[0]]:
            raise ValueError("Image edit source dimensions no longer match the original")
        if settings["mask_enabled"] and settings["outline"] is not None:
            allowed &= rasterize_outline(settings["outline"], gray.shape)
        for region in settings["regions"]:
            selected = region_mask(region, gray.shape)
            allowed[selected] = region["operation"] != "subtract"
    if source_allowed is not None:
        # Fully transparent original pixels are never eligible, including after
        # Restore. Composited grayscale cannot itself retain this distinction.
        allowed &= source_allowed
    output = 255 - gray if settings is not None and settings["invert"] else gray.copy()
    output[~allowed] = 255
    return output, allowed


def outline_from_mask(
    mask: np.ndarray,
    settings: Mapping[str, Any] | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
) -> NativePathGeometry:
    """Fit editable lines/Béziers using the existing shared authoritative fitter."""
    from .raster_vectorize import (
        RasterVectorizationCancelledError,
        fit_physical_contours_to_native_path,
    )

    def checkpoint():
        if cancel_check is not None and cancel_check():
            raise RasterVectorizationCancelledError("Image outline cancelled")

    options = trace_settings(settings)
    binary = np.asarray(mask, dtype=bool).astype(np.uint8) * 255
    _gray(binary)
    binary, _count = clean_foreground_components(
        binary, minimum_component_area_px=options["minimum_component_area"],
        minimum_hole_area_px=0, maximum_components=1024,
        cancellation_checkpoint=checkpoint,
    )
    contours, parents = mask_contours(
        binary, include_holes=options["include_holes"], cancel_check=cancel_check,
    )
    height, width = binary.shape
    physical: list[np.ndarray] = []
    for contour in contours:
        points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
        physical.append(np.column_stack((points[:, 0] + 0.5 - width / 2, height / 2 - points[:, 1] - 0.5)))
    result = fit_physical_contours_to_native_path(
        physical, parents, source_pixel_spacing_mm=(1.0, 1.0),
        fitting_tolerance_mm=options["detail"], smoothing_mm=options["smoothing"],
        frame_bounds_mm=(-width / 2, -height / 2, width / 2, height / 2),
        cancel_check=cancel_check,
    )
    _require_outline(result.geometry)
    return result.geometry


def trace_foreground(
    gray: np.ndarray, settings: Mapping[str, Any] | None = None,
    *, cancel_check: Callable[[], bool] | None = None,
) -> NativePathGeometry:
    """Propose all retained dark-on-light components; selection remains manual.

    Brightness cannot identify semantic subjects or cast shadows. The caller
    previews this candidate and lets the operator exclude unwanted components.
    """
    _gray(gray)
    options = trace_settings(settings)
    return outline_from_mask(gray < 255 - options["tolerance"], options, cancel_check=cancel_check)


def derive_vector_outline(
    gray: np.ndarray, geometry: Mapping[str, Any], *, include_holes: bool = True,
) -> NativePathGeometry:
    settings = edit_settings(geometry)
    if settings is None or settings["outline"] is None:
        raise ValueError("Accept or draw an outline before creating a vector")
    if settings["regions"]:
        raise ValueError(
            "Preview the edited mask outline and accept it before creating a vector. "
            "This conversion can approximate existing curves."
        )
    path = _path(settings["outline"])
    _require_outline(path)
    return path if include_holes else _exterior_only(path)


def _signed_area(points: np.ndarray) -> float:
    return float(np.sum(points[:, 0] * np.roll(points[:, 1], -1) - points[:, 1] * np.roll(points[:, 0], -1)) / 2)


def _depths(lines: tuple[tuple[tuple[float, float], ...], ...]) -> list[int]:
    from .raster_vectorize import _point_inside_closed_polyline

    arrays = [np.asarray(line, dtype=float) for line in lines]
    return [sum(_point_inside_closed_polyline(line[0], other) for j, other in enumerate(arrays) if i != j)
            for i, line in enumerate(arrays)]


def _exterior_only(path: NativePathGeometry) -> NativePathGeometry:
    depths = _depths(flatten_native_path(path, 1e-5))
    return NativePathGeometry(tuple(subpath for subpath, depth in zip(path.subpaths, depths, strict=True) if depth == 0))


def offset_outline(
    value: NativePathGeometry | Mapping[str, Any], width_mm: float, height_mm: float,
    offset_mm: float, include_holes: bool = True,
) -> NativePathGeometry:
    """Create a conservative physical miter offset, rejecting ambiguous results.

    Positive offsets expand foreground. Curves become line segments within
    0.01 mm before offsetting. No merging, trimming or topology repair occurs;
    collapsed holes, crossing contours and excessively long miters are errors.
    """
    path = _path(value)
    errors = validate_outline(path, width_mm=width_mm, height_mm=height_mm)
    if errors:
        raise ValueError("; ".join(errors))
    offset = _number(offset_mm, "Outline offset", -10_000, 10_000)
    if type(include_holes) is not bool:
        raise ValueError("include_holes must be a boolean")
    if not include_holes:
        path = _exterior_only(path)
    if offset == 0:
        return path
    affine = PathAffineTransform(m11=width_mm, m22=height_mm)
    lines = flatten_native_path(path, min(0.01, abs(offset) / 20), transform=affine)
    depths = _depths(lines)
    result: list[PathSubpath] = []
    for line, depth in zip(lines, depths, strict=True):
        points = np.asarray(line[:-1], dtype=float)
        area = _signed_area(points)
        edges = np.roll(points, -1, axis=0) - points
        lengths = np.linalg.norm(edges, axis=1)
        if np.any(lengths <= 1e-12):
            raise ValueError("Offset input has a duplicate point")
        directions = edges / lengths[:, None]
        normals = np.column_stack((directions[:, 1], -directions[:, 0])) * (1 if area > 0 else -1)
        distance = offset * (-1 if depth % 2 else 1)
        shifted: list[np.ndarray] = []
        for index, point in enumerate(points):
            previous = normals[index - 1]
            following = normals[index]
            denominator = 1 + float(np.dot(previous, following))
            if denominator <= 1e-10:
                raise ValueError("Offset cannot resolve a reversing corner")
            delta = (previous + following) * distance / denominator
            if float(np.linalg.norm(delta)) > abs(distance) * 20:
                raise ValueError("Offset produces an excessive miter; reduce offset or edit the corner")
            shifted.append(point + delta)
        shifted_array = np.asarray(shifted)
        new_edges = np.roll(shifted_array, -1, axis=0) - shifted_array
        if _signed_area(shifted_array) * area <= 0:
            raise ValueError("Offset collapses or reverses a contour; no geometry was repaired")
        if np.any(np.sum(edges * new_edges, axis=1) <= 1e-12):
            raise ValueError(
                "Offset collapses or folds over local detail. Reduce the offset or explicitly "
                "refine/retrace the outline; no geometry was repaired"
            )
        normalized = shifted_array / (width_mm, height_mm)
        result.append(PathSubpath(tuple(normalized[0]), tuple(PathLineSegment(tuple(point)) for point in normalized[1:]), True))
    output = NativePathGeometry(tuple(result))
    errors = validate_outline(output, width_mm=width_mm, height_mm=height_mm)
    if errors:
        raise ValueError("Invalid offset: " + "; ".join(errors))
    result_lines = flatten_native_path(output, 0.001, transform=affine)
    if _depths(result_lines) != depths:
        raise ValueError("Offset changes contour nesting; no geometry was repaired")
    return output
