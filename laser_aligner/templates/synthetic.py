from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from ..config import WorkArea
from ..vision.object_trace import TraceOptions
from .model import CutTemplate, TemplateFeature


@dataclass(slots=True, frozen=True)
class SyntheticFeatureGroundTruth:
    """Known machine-space pose for one rendered template feature."""

    index: int
    object_id: str
    center_mm: tuple[float, float]
    width_mm: float
    height_mm: float
    rotation_deg: float
    polygon_mm: tuple[tuple[float, float], ...]
    rendered: bool
    occluded: bool
    clipped: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "object_id": self.object_id,
            "center_mm": list(self.center_mm),
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "rotation_deg": self.rotation_deg,
            "polygon_mm": [list(point) for point in self.polygon_mm],
            "rendered": self.rendered,
            "occluded": self.occluded,
            "clipped": self.clipped,
        }


@dataclass(slots=True, frozen=True)
class SyntheticTemplateGroundTruth:
    """Ground truth accompanying a generated corrected camera frame."""

    template_id: str
    template_name: str
    center_mm: tuple[float, float]
    rotation_deg: float
    work_area: tuple[float, float, float, float]
    pixels_per_mm: float
    image_size_px: tuple[int, int]
    trace_options: dict[str, Any]
    target_hue: float | None
    seed: int
    noise_stddev: float
    missing_feature_indices: tuple[int, ...]
    occluded_feature_indices: tuple[int, ...]
    features: tuple[SyntheticFeatureGroundTruth, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "template_name": self.template_name,
            "center_mm": list(self.center_mm),
            "rotation_deg": self.rotation_deg,
            "work_area": {
                "x_min": self.work_area[0],
                "x_max": self.work_area[1],
                "y_min": self.work_area[2],
                "y_max": self.work_area[3],
            },
            "pixels_per_mm": self.pixels_per_mm,
            "image_size_px": list(self.image_size_px),
            "trace_options": dict(self.trace_options),
            "target_hue": self.target_hue,
            "seed": self.seed,
            "noise_stddev": self.noise_stddev,
            "missing_feature_indices": list(self.missing_feature_indices),
            "occluded_feature_indices": list(self.occluded_feature_indices),
            "features": [feature.to_dict() for feature in self.features],
        }


@dataclass(slots=True, frozen=True)
class SyntheticTemplateFrame:
    """A generated BGR corrected frame and its known placement metadata."""

    image: np.ndarray
    ground_truth: SyntheticTemplateGroundTruth

    @property
    def metadata(self) -> dict[str, Any]:
        """Return JSON-compatible ground truth for diagnostics or sidecars."""

        return self.ground_truth.to_dict()


@dataclass(slots=True, frozen=True)
class _LabelRegion:
    """One label mask stored only over its clipped pixel bounding box."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int
    mask: np.ndarray

    def image_view(self, image: np.ndarray) -> np.ndarray:
        return image[self.y_min : self.y_max, self.x_min : self.x_max]


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _normalise_rotation(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def _indices(values: Iterable[int], count: int, name: str) -> tuple[int, ...]:
    output: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError(f"{name} must contain integer feature indices")
        index = int(value)
        if not 0 <= index < count:
            raise ValueError(f"{name} contains out-of-range feature index {index}")
        output.append(index)
    if len(output) != len(set(output)):
        raise ValueError(f"{name} must not contain duplicate feature indices")
    return tuple(sorted(output))


def _object_corner_radii(template: CutTemplate) -> dict[str, float]:
    radii: dict[str, float] = {}
    for item in template.objects:
        radius = item.geometry.get("corner_radius_mm", 0.0)
        try:
            number = float(radius)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            radii[item.id] = max(0.0, number)
    return radii


def _feature_radius_mm(
    feature: TemplateFeature,
    object_corner_radii: dict[str, float],
) -> float:
    radius = object_corner_radii.get(feature.object_id)
    if radius is not None:
        return min(radius, feature.width_mm / 2.0, feature.height_mm / 2.0)
    return min(feature.width_mm, feature.height_mm) * 0.10


def _rounded_rectangle(
    width_mm: float,
    height_mm: float,
    radius_mm: float,
    samples_per_corner: int = 8,
) -> np.ndarray:
    half_width = width_mm / 2.0
    half_height = height_mm / 2.0
    radius = max(0.0, min(radius_mm, half_width, half_height))
    if radius <= 1e-9:
        return np.asarray(
            (
                (half_width, half_height),
                (-half_width, half_height),
                (-half_width, -half_height),
                (half_width, -half_height),
            ),
            dtype=np.float64,
        )
    points: list[tuple[float, float]] = []
    for center_x, center_y, start_deg in (
        (half_width - radius, half_height - radius, 0.0),
        (-half_width + radius, half_height - radius, 90.0),
        (-half_width + radius, -half_height + radius, 180.0),
        (half_width - radius, -half_height + radius, 270.0),
    ):
        for step in range(samples_per_corner + 1):
            angle = math.radians(start_deg + 90.0 * step / samples_per_corner)
            points.append(
                (
                    center_x + radius * math.cos(angle),
                    center_y + radius * math.sin(angle),
                )
            )
    return np.asarray(points, dtype=np.float64)


def _transform_points(
    points: np.ndarray,
    center_mm: tuple[float, float],
    rotation_deg: float,
) -> np.ndarray:
    angle = math.radians(rotation_deg)
    rotation = np.asarray(
        ((math.cos(angle), -math.sin(angle)), (math.sin(angle), math.cos(angle))),
        dtype=np.float64,
    )
    return np.asarray(points, dtype=np.float64) @ rotation.T + np.asarray(center_mm)


def _machine_to_pixels(
    points: np.ndarray,
    work_area: WorkArea,
    pixels_per_mm: float,
) -> np.ndarray:
    output = np.empty_like(points, dtype=np.float64)
    output[:, 0] = (points[:, 0] - work_area.x_min) * pixels_per_mm
    output[:, 1] = (work_area.y_max - points[:, 1]) * pixels_per_mm
    return output


def _canonical_rounded_mask(width: int, height: int, radius: int) -> np.ndarray:
    """Return the discrete rounded silhouette expected by object tracing."""

    width = max(2, int(width))
    height = max(2, int(height))
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


def _label_region(
    width_mm: float,
    height_mm: float,
    radius_mm: float,
    center_mm: tuple[float, float],
    rotation_deg: float,
    work_area: WorkArea,
    pixels_per_mm: float,
    image_width: int,
    image_height: int,
) -> _LabelRegion | None:
    """Place an exact rounded silhouette into a small clipped image region.

    A chromatic antialiased polygon fringe survives color segmentation and
    expands a generated label by one pixel.  Build the same discrete rounded
    mask used by the fitter, then rotate it with nearest-neighbour sampling so
    synthetic camera geometry remains a faithful test reference.
    """

    ppm = float(pixels_per_mm)
    mask_width = max(2, int(round(width_mm * ppm)) + 1)
    mask_height = max(2, int(round(height_mm * ppm)) + 1)
    mask_radius = max(0, int(round(radius_mm * ppm)))
    source = _canonical_rounded_mask(mask_width, mask_height, mask_radius)
    source_center = ((mask_width - 1) / 2.0, (mask_height - 1) / 2.0)
    target_center = _machine_to_pixels(
        np.asarray((center_mm,), dtype=np.float64),
        work_area,
        ppm,
    )[0]
    matrix = cv2.getRotationMatrix2D(source_center, float(rotation_deg), 1.0)
    matrix[0, 2] += float(target_center[0]) - source_center[0]
    matrix[1, 2] += float(target_center[1]) - source_center[1]

    corners = np.asarray(
        (
            (0.0, 0.0, 1.0),
            (mask_width - 1.0, 0.0, 1.0),
            (mask_width - 1.0, mask_height - 1.0, 1.0),
            (0.0, mask_height - 1.0, 1.0),
        ),
        dtype=np.float64,
    )
    transformed = corners @ matrix.T
    x_min = max(0, int(math.floor(float(transformed[:, 0].min()))) - 2)
    y_min = max(0, int(math.floor(float(transformed[:, 1].min()))) - 2)
    x_max = min(image_width, int(math.ceil(float(transformed[:, 0].max()))) + 3)
    y_max = min(image_height, int(math.ceil(float(transformed[:, 1].max()))) + 3)
    if x_min >= x_max or y_min >= y_max:
        return None
    local_matrix = matrix.copy()
    local_matrix[0, 2] -= x_min
    local_matrix[1, 2] -= y_min
    mask = cv2.warpAffine(
        source,
        local_matrix,
        (x_max - x_min, y_max - y_min),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return _LabelRegion(x_min, y_min, x_max, y_max, mask)


def _fill_mask(image: np.ndarray, mask: np.ndarray, color: tuple[int, int, int]) -> None:
    alpha = mask.astype(np.float32)[:, :, None] / 255.0
    image[:] = np.clip(
        image.astype(np.float32) * (1.0 - alpha)
        + np.asarray(color, dtype=np.float32) * alpha,
        0.0,
        255.0,
    ).astype(np.uint8)


def _label_color(hue: float, value: int) -> tuple[int, int, int]:
    hsv = np.asarray([[[int(round(hue)) % 180, 205, value]]], dtype=np.uint8)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def _draw_print_marks(
    image: np.ndarray,
    label_mask: np.ndarray,
    feature: SyntheticFeatureGroundTruth,
    work_area: WorkArea,
    pixels_per_mm: float,
    color: tuple[int, int, int],
    origin_px: tuple[int, int] = (0, 0),
) -> None:
    ink = np.zeros(label_mask.shape, dtype=np.uint8)
    width = feature.width_mm
    height = feature.height_mm
    for local_y, x_start, x_end in (
        (height * 0.18, -width * 0.30, width * 0.26),
        (-height * 0.02, -width * 0.36, width * 0.34),
        (-height * 0.20, -width * 0.22, width * 0.18),
    ):
        endpoints_mm = _transform_points(
            np.asarray(((x_start, local_y), (x_end, local_y)), dtype=np.float64),
            feature.center_mm,
            feature.rotation_deg,
        )
        endpoints_px = np.round(
            _machine_to_pixels(endpoints_mm, work_area, pixels_per_mm)
        ).astype(np.int32)
        endpoints_px -= np.asarray(origin_px, dtype=np.int32)
        cv2.line(
            ink,
            tuple(endpoints_px[0]),
            tuple(endpoints_px[1]),
            255,
            max(1, int(round(pixels_per_mm * 0.22))),
            cv2.LINE_AA,
        )
    ink = cv2.bitwise_and(ink, label_mask)
    _fill_mask(image, ink, color)


def _draw_contrast_texture(
    image: np.ndarray,
    label_mask: np.ndarray,
    pixels_per_mm: float,
    origin_px: tuple[int, int] = (0, 0),
) -> None:
    height, width = label_mask.shape
    y, x = np.indices((height, width))
    period = max(2, int(round(pixels_per_mm * 0.75)))
    bands = (
        (x + int(origin_px[0]) + 2 * (y + int(origin_px[1]))) // period
    ) % 2
    # Keep the texture's local mean near the bed background.  A large mean
    # brightness step casts a Gaussian halo into narrow cell gaps in the real
    # contrast detector and can incorrectly join neighbouring labels.
    offset = np.where(bands[:, :, None] == 0, -42.0, 42.0)
    texture = np.clip(image.astype(np.float32) + offset, 0.0, 255.0)
    alpha = label_mask.astype(np.float32)[:, :, None] / 255.0
    image[:] = np.clip(
        image.astype(np.float32) * (1.0 - alpha)
        + texture * alpha,
        0.0,
        255.0,
    ).astype(np.uint8)


def generate_template_test_frame(
    template: CutTemplate,
    work_area: WorkArea,
    pixels_per_mm: float,
    *,
    center_x_mm: float | None = None,
    center_y_mm: float | None = None,
    rotation_deg: float = 0.0,
    seed: int = 0,
    noise_stddev: float = 1.25,
    missing_feature_indices: Iterable[int] = (),
    occluded_feature_indices: Iterable[int] = (),
    occlusion_fraction: float = 0.55,
    label_hue: float | None = None,
) -> SyntheticTemplateFrame:
    """Render a template as a deterministic corrected-camera BGR frame.

    Template matching consumes visible label features rather than cut strokes,
    so each ``TemplateFeature`` is rendered as a printed, rounded label body at
    the requested rigid pose.  Machine coordinates use the same convention as
    ``BedMapper.rectify``: X increases right and Y increases up.

    Missing indices are not rendered.  Occluded indices are rendered and then
    partially covered by a neutral card.  These controls exist to exercise the
    detector and grid-inference paths; they do not model camera calibration or
    physical parallax.
    """

    if not isinstance(template, CutTemplate):
        raise TypeError("template must be a CutTemplate")
    if not isinstance(work_area, WorkArea):
        raise TypeError("work_area must be a WorkArea")
    ppm = _finite(pixels_per_mm, "pixels_per_mm")
    if ppm <= 0.0:
        raise ValueError("pixels_per_mm must be positive")
    x_min = _finite(work_area.x_min, "work_area.x_min")
    x_max = _finite(work_area.x_max, "work_area.x_max")
    y_min = _finite(work_area.y_min, "work_area.y_min")
    y_max = _finite(work_area.y_max, "work_area.y_max")
    if x_max <= x_min or y_max <= y_min:
        raise ValueError("work_area must have positive width and height")
    center_x = (
        (work_area.x_min + work_area.x_max) / 2.0
        if center_x_mm is None
        else _finite(center_x_mm, "center_x_mm")
    )
    center_y = (
        (work_area.y_min + work_area.y_max) / 2.0
        if center_y_mm is None
        else _finite(center_y_mm, "center_y_mm")
    )
    rotation = _normalise_rotation(_finite(rotation_deg, "rotation_deg"))
    noise = _finite(noise_stddev, "noise_stddev")
    if noise < 0.0:
        raise ValueError("noise_stddev must not be negative")
    occlusion = _finite(occlusion_fraction, "occlusion_fraction")
    if not 0.0 < occlusion < 1.0:
        raise ValueError("occlusion_fraction must be between zero and one")
    try:
        resolved_seed = int(seed)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("seed must be an integer") from exc
    if isinstance(seed, bool) or resolved_seed != seed:
        raise ValueError("seed must be an integer")
    if resolved_seed < 0:
        raise ValueError("seed must not be negative")

    missing = _indices(missing_feature_indices, len(template.features), "missing_feature_indices")
    occluded = _indices(
        occluded_feature_indices,
        len(template.features),
        "occluded_feature_indices",
    )
    overlap = set(missing).intersection(occluded)
    if overlap:
        raise ValueError("A feature cannot be both missing and occluded")
    missing_set = set(missing)
    occluded_set = set(occluded)

    options = TraceOptions.from_mapping(template.trace_options)
    # An explicit hue stored in the template is authoritative so the generated
    # frame always remains detectable with that template's own TraceOptions.
    target_hue = (
        options.target_hue
        if options.target_hue is not None
        else (2.0 if label_hue is None else _finite(label_hue, "label_hue") % 180.0)
    )
    image_width = max(1, int(round(work_area.width * ppm)))
    image_height = max(1, int(round(work_area.height * ppm)))
    y, x = np.indices((image_height, image_width), dtype=np.float32)
    x_gradient = x / max(1.0, float(image_width - 1))
    y_gradient = y / max(1.0, float(image_height - 1))
    base = 204.0 + 9.0 * x_gradient - 11.0 * y_gradient
    image = np.stack((base - 4.0, base, base + 3.0), axis=2)
    image = np.clip(image, 0.0, 255.0).astype(np.uint8)

    pose_angle = math.radians(rotation)
    pose_rotation = np.asarray(
        (
            (math.cos(pose_angle), -math.sin(pose_angle)),
            (math.sin(pose_angle), math.cos(pose_angle)),
        ),
        dtype=np.float64,
    )
    feature_truth: list[SyntheticFeatureGroundTruth] = []
    occluded_regions: dict[int, _LabelRegion] = {}
    object_corner_radii = _object_corner_radii(template)
    for index, feature in enumerate(template.features):
        local_center = np.asarray(feature.center_mm, dtype=np.float64)
        world_center = local_center @ pose_rotation.T + np.asarray((center_x, center_y))
        world_rotation = _normalise_rotation(feature.rotation_deg + rotation)
        local_polygon = _rounded_rectangle(
            feature.width_mm,
            feature.height_mm,
            _feature_radius_mm(feature, object_corner_radii),
        )
        world_polygon = _transform_points(
            local_polygon,
            (float(world_center[0]), float(world_center[1])),
            world_rotation,
        )
        clipped = any(
            not work_area.contains(float(point[0]), float(point[1]))
            for point in world_polygon
        )
        rendered = index not in missing_set
        truth = SyntheticFeatureGroundTruth(
            index=index,
            object_id=feature.object_id,
            center_mm=(float(world_center[0]), float(world_center[1])),
            width_mm=feature.width_mm,
            height_mm=feature.height_mm,
            rotation_deg=world_rotation,
            polygon_mm=tuple((float(point[0]), float(point[1])) for point in world_polygon),
            rendered=rendered,
            occluded=index in occluded_set,
            clipped=clipped,
        )
        feature_truth.append(truth)
        if not rendered:
            continue
        region = _label_region(
            feature.width_mm,
            feature.height_mm,
            _feature_radius_mm(feature, object_corner_radii),
            (float(world_center[0]), float(world_center[1])),
            world_rotation,
            work_area,
            ppm,
            image_width,
            image_height,
        )
        if region is None:
            continue
        label_image = region.image_view(image)
        origin_px = (region.x_min, region.y_min)
        if index in occluded_set:
            occluded_regions[index] = region
        if options.detection_mode == "contrast":
            _draw_contrast_texture(
                label_image,
                region.mask,
                ppm,
                origin_px,
            )
        else:
            value = max(155, 220 - (index * 11) % 55)
            _fill_mask(label_image, region.mask, _label_color(target_hue, value))
            _draw_print_marks(
                label_image,
                region.mask,
                truth,
                work_area,
                ppm,
                (30, 34, 39),
                origin_px,
            )

    for index in occluded:
        region = occluded_regions.get(index)
        if region is None:
            continue
        truth = feature_truth[index]
        cover_width = truth.width_mm * occlusion
        local_cover = np.asarray(
            (
                (truth.width_mm / 2.0 - cover_width, truth.height_mm / 2.0),
                (truth.width_mm / 2.0, truth.height_mm / 2.0),
                (truth.width_mm / 2.0, -truth.height_mm / 2.0),
                (truth.width_mm / 2.0 - cover_width, -truth.height_mm / 2.0),
            ),
            dtype=np.float64,
        )
        cover_mm = _transform_points(local_cover, truth.center_mm, truth.rotation_deg)
        cover_px = np.round(_machine_to_pixels(cover_mm, work_area, ppm)).astype(np.int32)
        cover_px -= np.asarray((region.x_min, region.y_min), dtype=np.int32)
        cover_mask = np.zeros(region.mask.shape, dtype=np.uint8)
        cv2.fillPoly(cover_mask, [cover_px], 255, cv2.LINE_AA)
        # Keep the occluder local to this label when two cells overlap.
        cover_mask = cv2.bitwise_and(cover_mask, region.mask)
        _fill_mask(region.image_view(image), cover_mask, (188, 192, 195))

    if noise > 0.0:
        generator = np.random.default_rng(resolved_seed)
        perturbation = generator.normal(0.0, noise, image.shape).astype(np.float32)
        image = np.clip(image.astype(np.float32) + perturbation, 0.0, 255.0).astype(np.uint8)

    ground_truth = SyntheticTemplateGroundTruth(
        template_id=template.id,
        template_name=template.name,
        center_mm=(center_x, center_y),
        rotation_deg=rotation,
        work_area=(work_area.x_min, work_area.x_max, work_area.y_min, work_area.y_max),
        pixels_per_mm=ppm,
        image_size_px=(image_width, image_height),
        trace_options=options.to_dict(),
        target_hue=(None if options.detection_mode == "contrast" else target_hue),
        seed=resolved_seed,
        noise_stddev=noise,
        missing_feature_indices=missing,
        occluded_feature_indices=occluded,
        features=tuple(feature_truth),
    )
    return SyntheticTemplateFrame(image=image, ground_truth=ground_truth)


__all__ = [
    "SyntheticFeatureGroundTruth",
    "SyntheticTemplateFrame",
    "SyntheticTemplateGroundTruth",
    "generate_template_test_frame",
]
