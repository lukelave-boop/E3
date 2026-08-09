from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..templates import CutTemplate


_EPSILON = 1e-9


@dataclass(slots=True)
class TemplateAlignment:
    """A rigid camera-to-template alignment candidate.

    ``rotation_deg`` and ``translation_mm`` map a template-space point into
    machine coordinates.  Scale is deliberately diagnostic-only: callers must
    never apply ``scale_ratio`` to cut geometry automatically.
    """

    template_id: str
    template_name: str
    rotation_deg: float
    translation_mm: tuple[float, float]
    matched_count: int
    direct_match_count: int
    inferred_match_count: int
    feature_count: int
    detection_count: int
    coverage: float
    weighted_coverage: float
    detection_coverage: float
    rms_error_mm: float | None
    max_error_mm: float | None
    scale_ratio: float | None
    dimension_scale_ratio: float | None
    confidence: float
    score: float
    matches: tuple[tuple[int, int, float], ...]
    warnings: tuple[str, ...] = ()
    ambiguous: bool = False
    pose_ambiguous: bool = False

    def transform_point(self, point_mm: Sequence[float]) -> tuple[float, float]:
        """Apply the rigid result to one template-space point."""
        point = np.asarray(point_mm, dtype=np.float64)
        transformed = _rotation_matrix(self.rotation_deg) @ point
        transformed += np.asarray(self.translation_mm, dtype=np.float64)
        return float(transformed[0]), float(transformed[1])


@dataclass(slots=True)
class _ObservedFeature:
    center: np.ndarray
    width: float
    height: float
    rotation: float
    source: str
    confidence: float
    weight: float


@dataclass(slots=True)
class _Fit:
    rotation: float
    translation: np.ndarray
    pairs: list[tuple[int, int, float]]
    match_weights: np.ndarray
    dimension_quality: np.ndarray
    orientation_quality: np.ndarray
    tolerance: float
    objective: tuple[float, ...]


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _template_identity(template: Any) -> tuple[str, str]:
    name = str(_value(template, "name", "Unnamed template"))
    identifier = _value(template, "id", None)
    if identifier is None:
        identifier = _value(template, "template_id", None)
    return str(identifier if identifier is not None else name), name


def _normalise_axis_angle(angle: float) -> float:
    """Return a rectangle-axis angle in [-90, 90)."""
    return (float(angle) + 90.0) % 180.0 - 90.0


def _normalise_rotation(angle: float) -> float:
    return (float(angle) + 180.0) % 360.0 - 180.0


def _axis_angle_error(first: float, second: float) -> float:
    return abs(_normalise_axis_angle(first - second))


def _rotation_matrix(angle_deg: float) -> np.ndarray:
    angle = math.radians(float(angle_deg))
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.asarray(((cosine, -sine), (sine, cosine)), dtype=np.float64)


def _as_features(items: Sequence[Any], *, detections: bool) -> list[_ObservedFeature]:
    output: list[_ObservedFeature] = []
    for item in items:
        raw_center = _value(item, "center_mm", None)
        if raw_center is None:
            continue
        center = np.asarray(raw_center, dtype=np.float64).reshape(-1)
        if len(center) != 2 or not np.all(np.isfinite(center)):
            continue
        width = abs(float(_value(item, "width_mm", 0.0)))
        height = abs(float(_value(item, "height_mm", 0.0)))
        rotation = float(_value(item, "rotation_deg", 0.0))
        source = str(_value(item, "source", "direct")).lower() if detections else "template"
        confidence = max(0.0, min(1.0, float(_value(item, "confidence", 1.0))))
        if detections:
            source_weight = 0.32 if source == "inferred" else 1.0
            weight = source_weight * (0.60 + 0.40 * confidence)
        else:
            weight = 1.0
        output.append(
            _ObservedFeature(
                center=center,
                width=width,
                height=height,
                rotation=rotation,
                source=source,
                confidence=confidence,
                weight=weight,
            )
        )
    return output


def _feature_sequence(template: Any) -> Sequence[Any]:
    features = _value(template, "features", ())
    return features if isinstance(features, Sequence) else tuple(features)


def _dimension_quality(template: _ObservedFeature, detection: _ObservedFeature) -> float:
    template_dimensions = sorted((template.width, template.height), reverse=True)
    detection_dimensions = sorted((detection.width, detection.height), reverse=True)
    if min(template_dimensions + detection_dimensions) <= _EPSILON:
        return 0.75
    error = sum(
        abs(math.log(observed / expected))
        for expected, observed in zip(template_dimensions, detection_dimensions, strict=True)
    )
    return math.exp(-error / 0.55)


def _orientation_quality(
    template: _ObservedFeature,
    detection: _ObservedFeature,
    rotation_deg: float,
) -> float:
    # Orientation is not informative for nearly square features.
    maximum = max(template.width, template.height, detection.width, detection.height, _EPSILON)
    minimum = min(template.width, template.height, detection.width, detection.height)
    if minimum / maximum >= 0.86:
        return 1.0
    error = _axis_angle_error(template.rotation + rotation_deg, detection.rotation)
    return math.exp(-0.5 * (error / 16.0) ** 2)


def _representative_pairs(points: np.ndarray, limit: int = 96) -> list[tuple[int, int, float, float]]:
    pairs: list[tuple[int, int, float, float]] = []
    for first in range(len(points)):
        for second in range(first + 1, len(points)):
            vector = points[second] - points[first]
            distance = float(np.linalg.norm(vector))
            if distance > _EPSILON:
                pairs.append((first, second, distance, math.degrees(math.atan2(vector[1], vector[0]))))
    if len(pairs) <= limit:
        return pairs
    pairs.sort(key=lambda item: item[2])
    indices = np.linspace(0, len(pairs) - 1, limit, dtype=int)
    return [pairs[int(index)] for index in dict.fromkeys(indices.tolist())]


def _rotation_hypotheses(
    template_features: Sequence[_ObservedFeature],
    detections: Sequence[_ObservedFeature],
) -> list[float]:
    hypotheses: list[tuple[float, float]] = [(0.0, 0.05)]

    # The long-axis orientation is a strong and inexpensive hypothesis source.
    for feature in template_features[:24]:
        for detection in detections[:24]:
            difference = _normalise_axis_angle(detection.rotation - feature.rotation)
            quality = _dimension_quality(feature, detection)
            hypotheses.append((difference, 0.6 + 0.4 * quality))
            hypotheses.append((_normalise_rotation(difference + 180.0), 0.4 + 0.3 * quality))

    template_points = np.asarray([item.center for item in template_features])
    detection_points = np.asarray([item.center for item in detections])
    template_pairs = _representative_pairs(template_points)
    detection_pairs = _representative_pairs(detection_points)
    for _, _, template_distance, template_angle in template_pairs:
        for _, _, detection_distance, detection_angle in detection_pairs:
            ratio = detection_distance / template_distance
            if not 0.55 <= ratio <= 1.80:
                continue
            support = math.exp(-abs(math.log(ratio)) / 0.28)
            difference = _normalise_rotation(detection_angle - template_angle)
            hypotheses.append((difference, support))
            hypotheses.append((_normalise_rotation(difference + 180.0), support))

    # Accumulate nearby hypotheses so repeated grid vectors dominate accidental
    # pairings.  Exact orientation values are retained to avoid quantisation loss.
    bins: dict[int, float] = {}
    for angle, weight in hypotheses:
        key = int(round(_normalise_rotation(angle)))
        bins[key] = bins.get(key, 0.0) + weight
    selected_bins = sorted(bins, key=lambda key: bins[key], reverse=True)[:48]
    selected = [float(key) for key in selected_bins]
    selected.extend(angle for angle, _ in hypotheses[: min(120, len(hypotheses))])

    unique: list[float] = []
    for angle in selected:
        angle = _normalise_rotation(angle)
        if all(abs(_normalise_rotation(angle - existing)) > 0.20 for existing in unique):
            unique.append(angle)
    return unique[:72]


def _alignment_tolerance(template: Any, features: Sequence[_ObservedFeature]) -> float:
    configured = _value(template, "alignment_tolerance_mm", None)
    if configured is None:
        configured = _value(template, "match_tolerance_mm", None)
    if configured is not None:
        return max(0.25, min(25.0, float(configured)))

    diagonals = [math.hypot(item.width, item.height) for item in features if item.width and item.height]
    feature_term = 0.22 * float(np.median(diagonals)) if diagonals else 3.0
    points = np.asarray([item.center for item in features])
    pairs = _representative_pairs(points, limit=256)
    spacing_term = 0.35 * min((item[2] for item in pairs), default=20.0)
    return max(1.25, min(10.0, feature_term, spacing_term))


def _has_half_turn_feature_symmetry(
    features: Sequence[_ObservedFeature],
    tolerance: float,
) -> bool:
    """Return whether observable centers and rectangles survive a half turn."""
    if not features:
        return False
    points = np.asarray([item.center for item in features], dtype=np.float64)
    center = np.mean(points, axis=0)
    half_turned = 2.0 * center - points
    candidates: list[tuple[float, int, int]] = []
    for source_index, predicted in enumerate(half_turned):
        source = features[source_index]
        for target_index, target in enumerate(features):
            distance = float(np.linalg.norm(predicted - target.center))
            if distance > tolerance:
                continue
            if _dimension_quality(source, target) < 0.90:
                continue
            maximum = max(
                source.width,
                source.height,
                target.width,
                target.height,
                _EPSILON,
            )
            minimum = min(
                source.width,
                source.height,
                target.width,
                target.height,
            )
            if (
                minimum / maximum < 0.86
                and _axis_angle_error(source.rotation, target.rotation) > 2.0
            ):
                continue
            candidates.append((distance, source_index, target_index))

    used_sources: set[int] = set()
    used_targets: set[int] = set()
    for _, source_index, target_index in sorted(candidates):
        if source_index in used_sources or target_index in used_targets:
            continue
        used_sources.add(source_index)
        used_targets.add(target_index)
    return len(used_sources) == len(features)


def _template_may_have_directional_geometry(template: Any) -> bool:
    """Conservatively classify cut objects whose half-turn pose can matter."""
    objects = _value(template, "objects", None)
    if not objects:
        return True
    harmless_kinds = {"rectangle", "ellipse", "line"}
    for item in objects:
        raw_kind = _value(item, "kind", None)
        kind = getattr(raw_kind, "value", raw_kind)
        if kind is None or str(kind).strip().lower() not in harmless_kinds:
            return True
    return False


def _translation_hypotheses(
    rotated_template: np.ndarray,
    detections: Sequence[_ObservedFeature],
    tolerance: float,
) -> list[np.ndarray]:
    bin_size = max(0.5, tolerance * 0.65)
    candidate_rows: list[tuple[np.ndarray, float]] = []
    for point in rotated_template:
        for detection in detections:
            candidate_rows.append((detection.center - point, detection.weight))

    outputs: list[np.ndarray] = []
    for offset_x, offset_y in ((0.0, 0.0), (0.5, 0.5), (0.5, 0.0), (0.0, 0.5)):
        groups: dict[tuple[int, int], list[tuple[np.ndarray, float]]] = {}
        for translation, weight in candidate_rows:
            key = (
                int(math.floor(translation[0] / bin_size + offset_x)),
                int(math.floor(translation[1] / bin_size + offset_y)),
            )
            groups.setdefault(key, []).append((translation, weight))
        ordered = sorted(groups.values(), key=lambda rows: sum(row[1] for row in rows), reverse=True)
        for rows in ordered[:5]:
            weights = np.asarray([row[1] for row in rows], dtype=np.float64)
            translations = np.asarray([row[0] for row in rows], dtype=np.float64)
            candidate = np.average(translations, axis=0, weights=weights)
            if all(float(np.linalg.norm(candidate - existing)) > bin_size * 0.25 for existing in outputs):
                outputs.append(candidate)
    return outputs[:16]


def _assign_matches(
    transformed: np.ndarray,
    rotation_deg: float,
    template_features: Sequence[_ObservedFeature],
    detections: Sequence[_ObservedFeature],
    tolerance: float,
) -> tuple[list[tuple[int, int, float]], np.ndarray, np.ndarray, np.ndarray]:
    candidates: list[tuple[float, float, int, int, float, float]] = []
    detection_centers = np.asarray([item.center for item in detections])
    distances = np.linalg.norm(transformed[:, np.newaxis, :] - detection_centers[np.newaxis, :, :], axis=2)
    for feature_index, detection_index in np.argwhere(distances <= tolerance):
        feature_index = int(feature_index)
        detection_index = int(detection_index)
        detection = detections[detection_index]
        distance = float(distances[feature_index, detection_index])
        dimension = _dimension_quality(template_features[feature_index], detection)
        orientation = _orientation_quality(template_features[feature_index], detection, rotation_deg)
        cost = distance / tolerance + 0.22 * (1.0 - dimension) + 0.10 * (1.0 - orientation)
        candidates.append((cost, distance, feature_index, detection_index, dimension, orientation))

    used_features: set[int] = set()
    used_detections: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    weights: list[float] = []
    dimension_quality: list[float] = []
    orientation_quality: list[float] = []
    for _, distance, feature_index, detection_index, dimension, orientation in sorted(candidates):
        if feature_index in used_features or detection_index in used_detections:
            continue
        used_features.add(feature_index)
        used_detections.add(detection_index)
        detection = detections[detection_index]
        pairs.append((feature_index, detection_index, distance))
        weights.append(detection.weight * (0.65 + 0.35 * dimension) * (0.75 + 0.25 * orientation))
        dimension_quality.append(dimension)
        orientation_quality.append(orientation)
    return (
        pairs,
        np.asarray(weights, dtype=np.float64),
        np.asarray(dimension_quality, dtype=np.float64),
        np.asarray(orientation_quality, dtype=np.float64),
    )


def _refine_rigid_fit(
    rotation_deg: float,
    translation: np.ndarray,
    pairs: Sequence[tuple[int, int, float]],
    weights: np.ndarray,
    template_features: Sequence[_ObservedFeature],
    detections: Sequence[_ObservedFeature],
) -> tuple[float, np.ndarray]:
    if not pairs:
        return rotation_deg, translation
    template_points = np.asarray([template_features[pair[0]].center for pair in pairs])
    detection_points = np.asarray([detections[pair[1]].center for pair in pairs])
    safe_weights = weights if float(weights.sum()) > _EPSILON else np.ones(len(pairs))
    template_mean = np.average(template_points, axis=0, weights=safe_weights)
    detection_mean = np.average(detection_points, axis=0, weights=safe_weights)

    refined_rotation = rotation_deg
    if len(pairs) >= 2:
        template_centered = template_points - template_mean
        detection_centered = detection_points - detection_mean
        cross = float(
            np.sum(safe_weights * (template_centered[:, 0] * detection_centered[:, 1]
                                   - template_centered[:, 1] * detection_centered[:, 0]))
        )
        dot = float(
            np.sum(safe_weights * (template_centered[:, 0] * detection_centered[:, 0]
                                   + template_centered[:, 1] * detection_centered[:, 1]))
        )
        if abs(cross) + abs(dot) > _EPSILON:
            candidate = _normalise_rotation(math.degrees(math.atan2(cross, dot)))
            if abs(_normalise_rotation(candidate - rotation_deg)) <= 18.0:
                refined_rotation = candidate
    refined_translation = detection_mean - _rotation_matrix(refined_rotation) @ template_mean
    return refined_rotation, refined_translation


def _evaluate_fit(
    rotation_deg: float,
    translation: np.ndarray,
    template_features: Sequence[_ObservedFeature],
    detections: Sequence[_ObservedFeature],
    tolerance: float,
) -> _Fit:
    for _ in range(3):
        transformed = np.asarray([item.center for item in template_features]) @ _rotation_matrix(rotation_deg).T
        transformed += translation
        pairs, weights, dimensions, orientations = _assign_matches(
            transformed, rotation_deg, template_features, detections, tolerance
        )
        next_rotation, next_translation = _refine_rigid_fit(
            rotation_deg, translation, pairs, weights, template_features, detections
        )
        if (
            abs(_normalise_rotation(next_rotation - rotation_deg)) < 1e-5
            and float(np.linalg.norm(next_translation - translation)) < 1e-5
        ):
            break
        rotation_deg, translation = next_rotation, next_translation

    transformed = np.asarray([item.center for item in template_features]) @ _rotation_matrix(rotation_deg).T
    transformed += translation
    pairs, weights, dimensions, orientations = _assign_matches(
        transformed, rotation_deg, template_features, detections, tolerance
    )
    if pairs:
        residuals = np.asarray([pair[2] for pair in pairs], dtype=np.float64)
        effective = float(weights.sum())
        rms = math.sqrt(float(np.average(residuals**2, weights=weights))) if effective > _EPSILON else math.inf
        direct = sum(detections[pair[1]].source != "inferred" for pair in pairs)
        weighted_coverage = effective / max(1, len(template_features))
        positional_quality = math.exp(-0.5 * (rms / max(tolerance * 0.45, 0.25)) ** 2)
        dimension_mean = float(np.average(dimensions, weights=weights))
        orientation_mean = float(np.average(orientations, weights=weights))
        objective = (
            weighted_coverage * positional_quality * (0.70 + 0.30 * dimension_mean),
            float(direct),
            -rms,
            float(len(pairs)),
            orientation_mean,
        )
    else:
        objective = (0.0, 0.0, -math.inf, 0.0, 0.0)
    return _Fit(
        rotation=_normalise_rotation(rotation_deg),
        translation=translation,
        pairs=pairs,
        match_weights=weights,
        dimension_quality=dimensions,
        orientation_quality=orientations,
        tolerance=tolerance,
        objective=objective,
    )


def _scale_diagnostics(
    fit: _Fit,
    template_features: Sequence[_ObservedFeature],
    detections: Sequence[_ObservedFeature],
) -> tuple[float | None, float | None]:
    pair_ratios: list[float] = []
    for first in range(len(fit.pairs)):
        for second in range(first + 1, len(fit.pairs)):
            first_feature, first_detection, _ = fit.pairs[first]
            second_feature, second_detection, _ = fit.pairs[second]
            template_distance = float(
                np.linalg.norm(template_features[first_feature].center - template_features[second_feature].center)
            )
            detection_distance = float(
                np.linalg.norm(detections[first_detection].center - detections[second_detection].center)
            )
            if template_distance > _EPSILON:
                pair_ratios.append(detection_distance / template_distance)

    dimension_ratios: list[float] = []
    for feature_index, detection_index, _ in fit.pairs:
        feature = template_features[feature_index]
        detection = detections[detection_index]
        if feature.width > _EPSILON and feature.height > _EPSILON:
            dimension_ratios.append(math.sqrt((detection.width / feature.width) * (detection.height / feature.height)))
    scale = float(np.median(pair_ratios)) if pair_ratios else None
    dimension_scale = float(np.median(dimension_ratios)) if dimension_ratios else None
    return scale, dimension_scale


def _empty_alignment(template: Any, feature_count: int, detection_count: int, warning: str) -> TemplateAlignment:
    template_id, template_name = _template_identity(template)
    return TemplateAlignment(
        template_id=template_id,
        template_name=template_name,
        rotation_deg=0.0,
        translation_mm=(0.0, 0.0),
        matched_count=0,
        direct_match_count=0,
        inferred_match_count=0,
        feature_count=feature_count,
        detection_count=detection_count,
        coverage=0.0,
        weighted_coverage=0.0,
        detection_coverage=0.0,
        rms_error_mm=None,
        max_error_mm=None,
        scale_ratio=None,
        dimension_scale_ratio=None,
        confidence=0.0,
        score=0.0,
        matches=(),
        warnings=(warning,),
    )


def align_template(
    template: CutTemplate,
    detections: Sequence[Mapping[str, Any] | Any],
) -> TemplateAlignment:
    """Find the best rigid alignment from ``template`` to unordered detections.

    The solver uses center and rectangle-orientation hypotheses, unique nearest
    correspondences, and a weighted rigid refinement.  Inferred grid detections
    participate at lower weight.  Any observed scale difference is reported but
    is never included in the returned transform.
    """
    template_features = _as_features(_feature_sequence(template), detections=False)
    observed = _as_features(detections, detections=True)
    if not template_features:
        return _empty_alignment(template, 0, len(observed), "Template has no alignment features.")
    if not observed:
        return _empty_alignment(template, len(template_features), 0, "No detections are available for alignment.")

    tolerance = _alignment_tolerance(template, template_features)
    best: _Fit | None = None
    template_points = np.asarray([item.center for item in template_features])
    for rotation in _rotation_hypotheses(template_features, observed):
        rotated = template_points @ _rotation_matrix(rotation).T
        for translation in _translation_hypotheses(rotated, observed, tolerance):
            fit = _evaluate_fit(rotation, translation, template_features, observed, tolerance)
            if best is None or fit.objective > best.objective:
                best = fit
    if best is None or not best.pairs:
        return _empty_alignment(
            template,
            len(template_features),
            len(observed),
            "No detections could be matched within the alignment tolerance.",
        )

    residuals = np.asarray([pair[2] for pair in best.pairs], dtype=np.float64)
    weights = best.match_weights
    effective_weight = float(weights.sum())
    rms = math.sqrt(float(np.average(residuals**2, weights=weights)))
    maximum = float(np.max(residuals))
    matched_detection_weight = sum(observed[pair[1]].weight for pair in best.pairs)
    total_detection_weight = sum(item.weight for item in observed)
    feature_count = len(template_features)
    coverage = len(best.pairs) / feature_count
    weighted_coverage = min(1.0, effective_weight / feature_count)
    detection_coverage = min(1.0, matched_detection_weight / max(total_detection_weight, _EPSILON))
    dimension_quality = float(np.average(best.dimension_quality, weights=weights))
    orientation_quality = float(np.average(best.orientation_quality, weights=weights))
    positional_quality = math.exp(-0.5 * (rms / max(tolerance * 0.45, 0.25)) ** 2)
    constraint = 1.0 if len(best.pairs) >= 3 else 0.62 if len(best.pairs) == 2 else 0.25
    score_fraction = (
        math.sqrt(max(0.0, weighted_coverage * detection_coverage))
        * positional_quality
        * (0.62 + 0.38 * dimension_quality)
        * (0.86 + 0.14 * orientation_quality)
        * constraint
    )
    scale, dimension_scale = _scale_diagnostics(best, template_features, observed)

    warnings: list[str] = []
    inferred_count = sum(observed[pair[1]].source == "inferred" for pair in best.pairs)
    direct_count = len(best.pairs) - inferred_count
    if len(best.pairs) < 3:
        warnings.append("Fewer than three features matched; the alignment is weakly constrained.")
    if coverage < 0.999:
        warnings.append(f"Matched {len(best.pairs)} of {feature_count} template features.")
    if inferred_count:
        warnings.append(
            f"{inferred_count} inferred detection{'s' if inferred_count != 1 else ''} contributed at reduced weight."
        )
    if scale is not None and abs(scale - 1.0) > 0.035:
        warnings.append(f"Detected center spacing suggests a {scale:.3f}x scale mismatch; scaling was not applied.")
        score_fraction *= max(0.45, math.exp(-abs(math.log(scale)) / 0.20))
    if dimension_scale is not None and abs(dimension_scale - 1.0) > 0.035:
        warnings.append(
            "Detected feature dimensions suggest a "
            f"{dimension_scale:.3f}x scale mismatch; scaling was not applied."
        )
        score_fraction *= max(
            0.45,
            math.exp(-abs(math.log(dimension_scale)) / 0.20),
        )
    if maximum > tolerance * 0.70:
        warnings.append("One or more matched features have a high positional residual.")
    symmetry_tolerance = max(0.05, min(0.75, tolerance * 0.10))
    pose_ambiguous = (
        _has_half_turn_feature_symmetry(
            template_features,
            symmetry_tolerance,
        )
        and _template_may_have_directional_geometry(template)
    )
    if pose_ambiguous:
        warnings.append(
            "The observable feature layout has a 180-degree pose ambiguity for "
            "directional or unknown cut geometry; confirm sheet orientation manually."
        )

    template_id, template_name = _template_identity(template)
    confidence = max(0.0, min(1.0, score_fraction))
    return TemplateAlignment(
        template_id=template_id,
        template_name=template_name,
        rotation_deg=best.rotation,
        translation_mm=(float(best.translation[0]), float(best.translation[1])),
        matched_count=len(best.pairs),
        direct_match_count=direct_count,
        inferred_match_count=inferred_count,
        feature_count=feature_count,
        detection_count=len(observed),
        coverage=coverage,
        weighted_coverage=weighted_coverage,
        detection_coverage=detection_coverage,
        rms_error_mm=rms,
        max_error_mm=maximum,
        scale_ratio=scale,
        dimension_scale_ratio=dimension_scale,
        confidence=confidence,
        score=100.0 * confidence,
        matches=tuple(best.pairs),
        warnings=tuple(warnings),
        pose_ambiguous=pose_ambiguous,
    )


def rank_templates(
    templates: Sequence[CutTemplate],
    detections: Sequence[Mapping[str, Any] | Any],
) -> list[TemplateAlignment]:
    """Align and rank templates, marking candidates that are too close to call."""
    ranked = [align_template(template, detections) for template in templates]
    ranked.sort(
        key=lambda item: (
            item.score,
            item.coverage,
            item.direct_match_count,
            -(item.rms_error_mm if item.rms_error_mm is not None else math.inf),
        ),
        reverse=True,
    )
    if len(ranked) < 2 or ranked[0].score < 15.0:
        return ranked
    threshold = max(3.0, ranked[0].score * 0.06)
    close_count = 1
    for candidate in ranked[1:]:
        if ranked[0].score - candidate.score <= threshold:
            close_count += 1
        else:
            break
    if close_count > 1:
        message = f"Template match is ambiguous: {close_count} candidates score within {threshold:.1f} points."
        for index in range(close_count):
            ranked[index] = replace(
                ranked[index],
                ambiguous=True,
                warnings=(*ranked[index].warnings, message),
            )
    return ranked


__all__ = ["TemplateAlignment", "align_template", "rank_templates"]
