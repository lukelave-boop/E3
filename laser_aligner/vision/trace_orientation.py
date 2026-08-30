"""Conservative orientation review for authoritative Camera Trace geometry.

This module is deliberately Qt-free and image-free.  It accepts only successful
native Trace candidates, derives bounded geometric evidence, and returns an
optional small rotation relative to the nearest cardinal axis.  Thresholding,
native fitting, and topology validation remain upstream authorities.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..project.path_geometry import (
    NativePathGeometry,
    PathAffineTransform,
    PathLineSegment,
    evaluate_cubic,
    native_path_bounds,
    transform_native_path,
)

_MIN_OFFER_ANGLE_DEG = 0.4
_NORMAL_MAX_OFFER_ANGLE_DEG = 10.0
_EXCEPTIONAL_MAX_OFFER_ANGLE_DEG = 15.0
_CONSENSUS_INLIER_DEG = 3.0
_CONSENSUS_BIN_WIDTH_DEG = 0.5
_MAX_CONSENSUS_SEED_BINS = 16
_MAX_ANALYSIS_SEGMENTS = 20_000
_MAX_ANALYSIS_SUBPATHS = 8_192
_CUBIC_SAMPLE_STEPS = 12


@dataclass(frozen=True, slots=True)
class TraceOrientationEstimate:
    """One deterministic orientation review for the current selection.

    Positive ``detected_skew_deg`` is counterclockwise in E3's ordinary
    X-right/Y-up geometry.  ``correction_deg`` is always its negative.
    """

    selected_ids: tuple[str, ...]
    offered: bool
    detected_skew_deg: float | None
    correction_deg: float | None
    pivot_mm: tuple[float, float] | None
    confidence: float
    suppression_reason: str
    evidence_count: int
    supporting_candidate_count: int
    evidence_families: tuple[str, ...]
    total_supporting_geometry_length_mm: float
    line_evidence_weight: float
    near_linear_cubic_evidence_weight: float
    component_axis_evidence_weight: float
    component_alignment_evidence_weight: float
    angular_spread_deg: float | None
    inlier_fraction: float
    elapsed_seconds: float

    def to_diagnostics(self) -> dict[str, Any]:
        """Return bounded logging/test diagnostics without sampled point arrays."""

        return {
            "selected_candidate_count": len(self.selected_ids),
            "valid_orientation_evidence_count": self.evidence_count,
            "supporting_candidate_count": self.supporting_candidate_count,
            "evidence_families": list(self.evidence_families),
            "total_supporting_geometry_length_mm": (
                self.total_supporting_geometry_length_mm
            ),
            "line_evidence_weight": self.line_evidence_weight,
            "near_linear_cubic_evidence_weight": (
                self.near_linear_cubic_evidence_weight
            ),
            "component_axis_evidence_weight": self.component_axis_evidence_weight,
            "component_alignment_evidence_weight": (
                self.component_alignment_evidence_weight
            ),
            "winning_skew_deg": self.detected_skew_deg,
            "correction_deg": self.correction_deg,
            "angular_spread_deg": self.angular_spread_deg,
            "inlier_fraction": self.inlier_fraction,
            "confidence_score": self.confidence,
            "offered": self.offered,
            "suppression_reason": self.suppression_reason,
            "elapsed_estimation_seconds": self.elapsed_seconds,
        }


@dataclass(frozen=True, slots=True)
class _Evidence:
    angle_deg: float
    weight: float
    family: str
    candidate_ids: tuple[str, ...]
    length_mm: float = 0.0


@dataclass(frozen=True, slots=True)
class _CandidateAnalysis:
    candidate_id: str
    geometry: NativePathGeometry
    center_mm: tuple[float, float]
    diagonal_mm: float
    evidence: tuple[_Evidence, ...]
    meaningful_for_alignment: bool


def _finite_pair(value: object, label: str) -> tuple[float, float]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != 2
    ):
        raise ValueError(f"{label} must contain exactly two coordinates")
    pair = (float(value[0]), float(value[1]))
    if not all(math.isfinite(item) for item in pair):
        raise ValueError(f"{label} must contain finite coordinates")
    return pair


def _raw_native_path_complexity(
    detection: Mapping[str, Any],
) -> tuple[int, int]:
    """Count native structure before parsing/transforming a potentially huge path."""

    if detection.get("native_verified") is not True:
        raise ValueError("Trace candidate native geometry is not verified")
    raw_path = detection.get("native_path")
    if not isinstance(raw_path, Mapping):
        raise ValueError("Trace candidate has no authoritative native path")
    raw_subpaths = raw_path.get("subpaths")
    if (
        not isinstance(raw_subpaths, Sequence)
        or isinstance(raw_subpaths, (str, bytes, bytearray))
    ):
        raise ValueError("Trace candidate native subpaths are invalid")
    segment_count = 0
    for raw_subpath in raw_subpaths:
        if not isinstance(raw_subpath, Mapping):
            raise ValueError("Trace candidate native subpath is invalid")
        raw_segments = raw_subpath.get("segments")
        if (
            not isinstance(raw_segments, Sequence)
            or isinstance(raw_segments, (str, bytes, bytearray))
        ):
            raise ValueError("Trace candidate native segments are invalid")
        segment_count += len(raw_segments)
    return len(raw_subpaths), segment_count


def trace_native_world_geometry(detection: Mapping[str, Any]) -> NativePathGeometry:
    """Reconstruct one authoritative candidate in physical Trace coordinates.

    No contour, mask, quick-preview, or failed-fit fallback is accepted here.
    """

    if not isinstance(detection, Mapping):
        raise TypeError("A Trace orientation candidate must be a mapping")
    if detection.get("native_verified") is not True:
        raise ValueError("Trace candidate native geometry is not verified")
    raw_path = detection.get("native_path")
    if not isinstance(raw_path, Mapping):
        raise ValueError("Trace candidate has no authoritative native path")
    diagnostics = detection.get("diagnostics") or {}
    if isinstance(diagnostics, Mapping) and diagnostics.get("grid_normalized"):
        raise ValueError("Grid Trace candidates are not supported by Straighten")
    if str(detection.get("source", "direct")) == "inferred":
        raise ValueError("Inferred Trace candidates are not orientation evidence")

    geometry = NativePathGeometry.from_dict(raw_path)
    center = _finite_pair(
        detection.get("native_center_mm") or detection.get("center_mm"),
        "native_center_mm",
    )
    width = float(detection.get("native_width_mm"))
    height = float(detection.get("native_height_mm"))
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0.0
        or height <= 0.0
    ):
        raise ValueError("Trace candidate native bounds must have positive extent")
    return transform_native_path(
        geometry,
        PathAffineTransform.from_components(
            scale_x=width,
            scale_y=height,
            translate_x=center[0],
            translate_y=center[1],
        ),
    )


def trace_rotation_transform(
    rotation_deg: float,
    pivot_mm: Sequence[float],
) -> PathAffineTransform:
    """Return a rigid rotation around one physical group pivot."""

    angle = float(rotation_deg)
    if not math.isfinite(angle):
        raise ValueError("Trace rotation must be finite")
    pivot_x, pivot_y = _finite_pair(pivot_mm, "pivot_mm")
    radians = math.radians(angle)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return PathAffineTransform(
        m11=cosine,
        m12=-sine,
        m21=sine,
        m22=cosine,
        dx=pivot_x - cosine * pivot_x + sine * pivot_y,
        dy=pivot_y - sine * pivot_x - cosine * pivot_y,
    )


def _modulo_cardinal_angle(angle_deg: float) -> float:
    return (float(angle_deg) + 45.0) % 90.0 - 45.0


def _angular_residual(first_deg: float, second_deg: float) -> float:
    return abs(_modulo_cardinal_angle(first_deg - second_deg))


def _candidate_angles_conflict(angles_deg: Sequence[float]) -> bool:
    """Return whether reliable candidate angles do not fit one 3-degree arc."""

    if len(angles_deg) < 2:
        return False
    ordered = sorted((float(angle) + 45.0) % 90.0 for angle in angles_deg)
    largest_gap = max(
        second - first
        for first, second in zip(
            ordered,
            (*ordered[1:], ordered[0] + 90.0),
            strict=True,
        )
    )
    minimum_covering_arc = 90.0 - largest_gap
    return minimum_covering_arc > _CONSENSUS_INLIER_DEG + 1e-9


def _segment_angle(start: tuple[float, float], end: tuple[float, float]) -> float:
    return _modulo_cardinal_angle(
        math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
    )


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(second[0] - first[0], second[1] - first[1])


def _distance_to_chord(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    denominator = math.hypot(dx, dy)
    if denominator <= 1e-12:
        return _distance(point, start)
    return abs(dx * (start[1] - point[1]) - (start[0] - point[0]) * dy) / denominator


def _principal_axis(
    weighted_points: Sequence[tuple[tuple[float, float], float]],
) -> tuple[float, float, float, float] | None:
    total_weight = sum(weight for _point, weight in weighted_points if weight > 0.0)
    if total_weight <= 0.0:
        return None
    mean_x = sum(point[0] * weight for point, weight in weighted_points) / total_weight
    mean_y = sum(point[1] * weight for point, weight in weighted_points) / total_weight
    xx = sum(
        weight * (point[0] - mean_x) ** 2 for point, weight in weighted_points
    ) / total_weight
    xy = sum(
        weight * (point[0] - mean_x) * (point[1] - mean_y)
        for point, weight in weighted_points
    ) / total_weight
    yy = sum(
        weight * (point[1] - mean_y) ** 2 for point, weight in weighted_points
    ) / total_weight
    trace = xx + yy
    discriminant = math.sqrt(max(0.0, (xx - yy) ** 2 + 4.0 * xy * xy))
    major = (trace + discriminant) / 2.0
    minor = max(0.0, (trace - discriminant) / 2.0)
    if major <= 1e-12:
        return None
    raw_angle = math.degrees(0.5 * math.atan2(2.0 * xy, xx - yy))
    angle = _modulo_cardinal_angle(raw_angle)
    ratio = major / max(minor, major * 1e-9)
    return angle, ratio, math.sqrt(major), raw_angle


def _cap_family_weight(
    evidence: list[_Evidence],
    maximum_weight: float,
) -> list[_Evidence]:
    total = sum(item.weight for item in evidence)
    if total <= maximum_weight or total <= 0.0:
        return evidence
    scale = maximum_weight / total
    return [
        _Evidence(
            angle_deg=item.angle_deg,
            weight=item.weight * scale,
            family=item.family,
            candidate_ids=item.candidate_ids,
            length_mm=item.length_mm,
        )
        for item in evidence
    ]


def _analyze_candidate(
    candidate_id: str,
    geometry: NativePathGeometry,
) -> _CandidateAnalysis:
    x_min, y_min, x_max, y_max = native_path_bounds(geometry)
    width = x_max - x_min
    height = y_max - y_min
    diagonal = math.hypot(width, height)
    center = ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)
    minimum_linear_length = max(0.75, min(2.0, diagonal * 0.025))
    line_evidence: list[_Evidence] = []
    cubic_evidence: list[_Evidence] = []
    axis_samples: list[tuple[tuple[float, float], float]] = []

    for subpath in geometry.subpaths:
        current = subpath.start
        for segment in subpath.segments:
            if isinstance(segment, PathLineSegment):
                length = _distance(current, segment.to)
                if length > 0.0:
                    axis_samples.append(
                        (
                            (
                                (current[0] + segment.to[0]) / 2.0,
                                (current[1] + segment.to[1]) / 2.0,
                            ),
                            length,
                        )
                    )
                if length >= minimum_linear_length:
                    line_evidence.append(
                        _Evidence(
                            _segment_angle(current, segment.to),
                            length,
                            "line",
                            (candidate_id,),
                            length,
                        )
                    )
                current = segment.to
                continue

            points = [current]
            points.extend(
                evaluate_cubic(current, segment, index / _CUBIC_SAMPLE_STEPS)
                for index in range(1, _CUBIC_SAMPLE_STEPS + 1)
            )
            flattened_length = 0.0
            for first, second in zip(points, points[1:], strict=False):
                sample_length = _distance(first, second)
                flattened_length += sample_length
                if sample_length > 0.0:
                    axis_samples.append(
                        (
                            (
                                (first[0] + second[0]) / 2.0,
                                (first[1] + second[1]) / 2.0,
                            ),
                            sample_length,
                        )
                    )
            chord_length = _distance(current, segment.to)
            maximum_deviation = max(
                _distance_to_chord(point, current, segment.to)
                for point in points[1:-1]
            )
            deviation_limit = max(0.04, min(0.20, 0.02 * chord_length))
            near_linear = (
                chord_length >= minimum_linear_length
                and flattened_length <= chord_length * 1.0125 + 1e-9
                and maximum_deviation <= deviation_limit
            )
            if near_linear:
                cubic_evidence.append(
                    _Evidence(
                        _segment_angle(current, segment.to),
                        chord_length * 0.75,
                        "near_linear_cubic",
                        (candidate_id,),
                        flattened_length,
                    )
                )
            current = segment.to

    family_cap = max(8.0, min(40.0, diagonal * 1.5))
    line_evidence = _cap_family_weight(line_evidence, family_cap)
    cubic_evidence = _cap_family_weight(cubic_evidence, family_cap * 0.75)
    evidence = [*line_evidence, *cubic_evidence]

    axis = _principal_axis(axis_samples)
    axis_added = False
    if axis is not None:
        angle, anisotropy, major_sigma, _raw_angle = axis
        if diagonal >= 3.0 and major_sigma >= 1.0 and anisotropy >= 3.0:
            anisotropy_strength = min(1.0, (anisotropy - 2.0) / 6.0)
            evidence.append(
                _Evidence(
                    angle,
                    min(20.0, diagonal) * 0.75 * anisotropy_strength,
                    "component_axis",
                    (candidate_id,),
                )
            )
            axis_added = True

    return _CandidateAnalysis(
        candidate_id=candidate_id,
        geometry=geometry,
        center_mm=center,
        diagonal_mm=diagonal,
        evidence=tuple(evidence),
        meaningful_for_alignment=(
            diagonal >= 2.5
            and bool(line_evidence or cubic_evidence or axis_added)
        ),
    )


def _alignment_evidence(
    candidates: Sequence[_CandidateAnalysis],
) -> _Evidence | None:
    aligned = [item for item in candidates if item.meaningful_for_alignment]
    if len(aligned) < 3:
        return None
    axis = _principal_axis([(item.center_mm, 1.0) for item in aligned])
    if axis is None:
        return None
    angle, anisotropy, major_sigma, raw_angle = axis
    if anisotropy < 3.0 or major_sigma < 2.0:
        return None
    radians = math.radians(raw_angle)
    direction = (math.cos(radians), math.sin(radians))
    projections = [
        item.center_mm[0] * direction[0] + item.center_mm[1] * direction[1]
        for item in aligned
    ]
    extent = max(projections) - min(projections)
    if extent < 5.0:
        return None
    anisotropy_strength = min(1.0, (anisotropy - 2.0) / 8.0)
    count_strength = min(1.0, len(aligned) / 5.0)
    return _Evidence(
        angle,
        min(30.0, extent) * anisotropy_strength * count_strength,
        "component_alignment",
        tuple(item.candidate_id for item in aligned),
    )


def _circular_mean(evidence: Sequence[_Evidence], center_deg: float) -> float:
    estimate = center_deg
    for _iteration in range(4):
        cosine = 0.0
        sine = 0.0
        for item in evidence:
            residual = _angular_residual(item.angle_deg, estimate)
            robust_weight = item.weight * min(1.0, 1.5 / max(1e-12, residual))
            radians = math.radians(item.angle_deg * 4.0)
            cosine += robust_weight * math.cos(radians)
            sine += robust_weight * math.sin(radians)
        if abs(cosine) + abs(sine) <= 1e-12:
            break
        estimate = _modulo_cardinal_angle(
            math.degrees(math.atan2(sine, cosine)) / 4.0
        )
    return estimate


def _bounded_consensus_seed_angles(
    evidence: Sequence[_Evidence],
) -> tuple[float, ...]:
    """Shortlist deterministic modulo-90 seeds without an all-pairs scan."""

    bin_count = int(round(90.0 / _CONSENSUS_BIN_WIDTH_DEG))
    bin_weights = [0.0] * bin_count
    representatives: list[_Evidence | None] = [None] * bin_count

    def center(index: int) -> float:
        return -45.0 + (index + 0.5) * _CONSENSUS_BIN_WIDTH_DEG

    for item in evidence:
        index = int(
            math.floor((item.angle_deg + 45.0) / _CONSENSUS_BIN_WIDTH_DEG)
        ) % bin_count
        bin_weights[index] += item.weight
        current = representatives[index]
        if current is None or (
            item.weight,
            -_angular_residual(item.angle_deg, center(index)),
            -abs(item.angle_deg),
            -item.angle_deg,
        ) > (
            current.weight,
            -_angular_residual(current.angle_deg, center(index)),
            -abs(current.angle_deg),
            -current.angle_deg,
        ):
            representatives[index] = item
    radius = int(math.ceil(_CONSENSUS_INLIER_DEG / _CONSENSUS_BIN_WIDTH_DEG))
    window_weights = [
        sum(
            bin_weights[(index + offset) % bin_count]
            for offset in range(-radius, radius + 1)
        )
        for index in range(bin_count)
    ]

    ranked = sorted(
        (index for index in range(bin_count) if representatives[index] is not None),
        key=lambda index: (
            -window_weights[index],
            abs(center(index)),
            center(index),
        ),
    )
    seeds = []
    for index in ranked[:_MAX_CONSENSUS_SEED_BINS]:
        representative = representatives[index]
        if representative is not None:
            seeds.append(representative.angle_deg)
    return tuple(seeds)


def _candidate_axis_cluster_angles(
    analyses: Sequence[_CandidateAnalysis],
) -> tuple[float, ...]:
    """Return tight multi-candidate axis modes used only as conflict witnesses."""

    axes = [
        item
        for analysis in analyses
        for item in analysis.evidence
        if item.family == "component_axis" and item.weight >= 1.0
    ]
    if len(axes) < 3:
        return ()
    cluster_angles: list[float] = []
    for seed_angle in _bounded_consensus_seed_angles(axes):
        cluster = tuple(
            item
            for item in axes
            if _angular_residual(item.angle_deg, seed_angle)
            <= _CONSENSUS_INLIER_DEG
        )
        if len(cluster) < 3:
            continue
        angle = _circular_mean(cluster, seed_angle)
        if not any(
            _angular_residual(angle, existing) <= _CONSENSUS_BIN_WIDTH_DEG
            for existing in cluster_angles
        ):
            cluster_angles.append(angle)
    return tuple(cluster_angles)


def _empty_estimate(
    *,
    selected_ids: tuple[str, ...],
    reason: str,
    started_at: float,
    pivot_mm: tuple[float, float] | None = None,
) -> TraceOrientationEstimate:
    return TraceOrientationEstimate(
        selected_ids=selected_ids,
        offered=False,
        detected_skew_deg=None,
        correction_deg=None,
        pivot_mm=pivot_mm,
        confidence=0.0,
        suppression_reason=reason,
        evidence_count=0,
        supporting_candidate_count=0,
        evidence_families=(),
        total_supporting_geometry_length_mm=0.0,
        line_evidence_weight=0.0,
        near_linear_cubic_evidence_weight=0.0,
        component_axis_evidence_weight=0.0,
        component_alignment_evidence_weight=0.0,
        angular_spread_deg=None,
        inlier_fraction=0.0,
        elapsed_seconds=time.perf_counter() - started_at,
    )


def estimate_trace_orientation(
    detections: Sequence[Mapping[str, Any]],
) -> TraceOrientationEstimate:
    """Estimate optional small skew from selected authoritative native geometry."""

    started_at = time.perf_counter()
    if not detections:
        return _empty_estimate(
            selected_ids=(),
            reason="no_selection",
            started_at=started_at,
        )
    if any(not isinstance(item, Mapping) for item in detections):
        return _empty_estimate(
            selected_ids=(),
            reason="non_authoritative_native_geometry",
            started_at=started_at,
        )
    selected_ids = tuple(str(item.get("id", "")) for item in detections)
    if any(not candidate_id for candidate_id in selected_ids) or len(
        set(selected_ids)
    ) != len(selected_ids):
        return _empty_estimate(
            selected_ids=selected_ids,
            reason="invalid_candidate_identity",
            started_at=started_at,
        )

    raw_subpath_count = 0
    raw_segment_count = 0
    try:
        for item in detections:
            subpath_count, segment_count = _raw_native_path_complexity(item)
            raw_subpath_count += subpath_count
            raw_segment_count += segment_count
            if (
                raw_subpath_count > _MAX_ANALYSIS_SUBPATHS
                or raw_segment_count > _MAX_ANALYSIS_SEGMENTS
            ):
                return _empty_estimate(
                    selected_ids=selected_ids,
                    reason="analysis_complexity_limit",
                    started_at=started_at,
                )
    except (TypeError, ValueError):
        return _empty_estimate(
            selected_ids=selected_ids,
            reason="non_authoritative_native_geometry",
            started_at=started_at,
        )

    try:
        geometries = [trace_native_world_geometry(item) for item in detections]
    except (KeyError, TypeError, ValueError, IndexError):
        return _empty_estimate(
            selected_ids=selected_ids,
            reason="non_authoritative_native_geometry",
            started_at=started_at,
        )
    segment_count = sum(
        len(subpath.segments)
        for geometry in geometries
        for subpath in geometry.subpaths
    )
    subpath_count = sum(len(geometry.subpaths) for geometry in geometries)
    if (
        segment_count > _MAX_ANALYSIS_SEGMENTS
        or subpath_count > _MAX_ANALYSIS_SUBPATHS
    ):
        return _empty_estimate(
            selected_ids=selected_ids,
            reason="analysis_complexity_limit",
            started_at=started_at,
        )

    geometry_bounds = [native_path_bounds(geometry) for geometry in geometries]
    x_min = min(bounds[0] for bounds in geometry_bounds)
    y_min = min(bounds[1] for bounds in geometry_bounds)
    x_max = max(bounds[2] for bounds in geometry_bounds)
    y_max = max(bounds[3] for bounds in geometry_bounds)
    pivot = ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)
    analyses = [
        _analyze_candidate(candidate_id, geometry)
        for candidate_id, geometry in zip(selected_ids, geometries, strict=True)
    ]
    evidence = [item for analysis in analyses for item in analysis.evidence]
    alignment = _alignment_evidence(analyses)
    if alignment is not None:
        evidence.append(alignment)
    if len(evidence) < 3 or sum(item.weight for item in evidence) <= 0.0:
        return _empty_estimate(
            selected_ids=selected_ids,
            reason="insufficient_orientation_evidence",
            started_at=started_at,
            pivot_mm=pivot,
        )

    def seed_score(
        seed: _Evidence,
        pool: Sequence[_Evidence],
    ) -> tuple[float, int, int, float, float]:
        inliers = [
            item
            for item in pool
            if _angular_residual(item.angle_deg, seed.angle_deg)
            <= _CONSENSUS_INLIER_DEG
        ]
        weight = sum(item.weight for item in inliers)
        families = len({item.family for item in inliers})
        candidates = len(
            {candidate_id for item in inliers for candidate_id in item.candidate_ids}
        )
        residual = sum(
            item.weight * _angular_residual(item.angle_deg, seed.angle_deg)
            for item in inliers
        )
        return weight, families, candidates, -residual, -abs(seed.angle_deg)

    seed_candidates = tuple(
        _Evidence(angle, 0.0, "bounded_seed", ())
        for angle in _bounded_consensus_seed_angles(evidence)
    )
    seed = max(seed_candidates, key=lambda item: seed_score(item, evidence))
    initial_inliers = [
        item
        for item in evidence
        if _angular_residual(item.angle_deg, seed.angle_deg)
        <= _CONSENSUS_INLIER_DEG
    ]
    winning_angle = _circular_mean(initial_inliers, seed.angle_deg)
    inliers = [
        item
        for item in evidence
        if _angular_residual(item.angle_deg, winning_angle)
        <= _CONSENSUS_INLIER_DEG
    ]
    total_weight = sum(item.weight for item in evidence)
    inlier_weight = sum(item.weight for item in inliers)
    inlier_fraction = inlier_weight / total_weight
    spread = math.sqrt(
        sum(
            item.weight * _angular_residual(item.angle_deg, winning_angle) ** 2
            for item in inliers
        )
        / max(inlier_weight, 1e-12)
    )
    inlier_families = tuple(sorted({item.family for item in inliers}))
    supporting_candidates = {
        candidate_id
        for item in inliers
        if item.family != "component_alignment"
        for candidate_id in item.candidate_ids
    }
    linear_inliers = [
        item for item in inliers if item.family in {"line", "near_linear_cubic"}
    ]
    supporting_length = sum(item.length_mm for item in linear_inliers)

    fraction_score = max(0.0, min(1.0, (inlier_fraction - 0.55) / 0.35))
    spread_score = max(0.0, min(1.0, 1.0 - spread / 2.5))
    length_score = min(1.0, supporting_length / 12.0)
    family_score = min(1.0, len(inlier_families) / 3.0)
    candidate_score = min(1.0, len(supporting_candidates) / 3.0)
    feature_score = min(1.0, len(inliers) / 6.0)
    confidence = (
        0.28 * fraction_score
        + 0.20 * spread_score
        + 0.18 * length_score
        + 0.15 * family_score
        + 0.10 * candidate_score
        + 0.09 * feature_score
    )

    family_weights = {
        family: sum(item.weight for item in evidence if item.family == family)
        for family in (
            "line",
            "near_linear_cubic",
            "component_axis",
            "component_alignment",
        )
    }

    def reliable_candidate_angle(analysis: _CandidateAnalysis) -> float | None:
        pool = analysis.evidence
        if len(pool) < 3:
            return None
        local_seed_candidates = tuple(
            _Evidence(angle, 0.0, "bounded_seed", ())
            for angle in _bounded_consensus_seed_angles(pool)
        )
        local_seed = max(
            local_seed_candidates,
            key=lambda item: seed_score(item, pool),
        )
        local_initial = [
            item
            for item in pool
            if _angular_residual(item.angle_deg, local_seed.angle_deg)
            <= _CONSENSUS_INLIER_DEG
        ]
        local_angle = _circular_mean(local_initial, local_seed.angle_deg)
        local_inliers = [
            item
            for item in pool
            if _angular_residual(item.angle_deg, local_angle)
            <= _CONSENSUS_INLIER_DEG
        ]
        local_total_weight = sum(item.weight for item in pool)
        local_inlier_weight = sum(item.weight for item in local_inliers)
        local_fraction = local_inlier_weight / max(local_total_weight, 1e-12)
        local_spread = math.sqrt(
            sum(
                item.weight * _angular_residual(item.angle_deg, local_angle) ** 2
                for item in local_inliers
            )
            / max(local_inlier_weight, 1e-12)
        )
        local_linear = [
            item
            for item in local_inliers
            if item.family in {"line", "near_linear_cubic"}
        ]
        minimum_linear_features = (
            2
            if any(item.family == "component_axis" for item in local_inliers)
            else 3
        )
        if (
            local_fraction < 0.68
            or local_spread > 2.0
            or len(local_linear) < minimum_linear_features
            or sum(item.length_mm for item in local_linear) < 3.0
        ):
            return None
        return local_angle

    reliable_candidate_angles = (
        *(
            angle
            for analysis in analyses
            if (angle := reliable_candidate_angle(analysis)) is not None
        ),
        *_candidate_axis_cluster_angles(analyses),
    )
    candidate_orientation_conflict = _candidate_angles_conflict(
        reliable_candidate_angles
    )
    absolute_angle = abs(winning_angle)
    strong_single_component = (
        len(selected_ids) == 1
        and len(linear_inliers) >= 3
        and supporting_length >= 12.0
        and "component_axis" in inlier_families
    )
    suppression_reason = ""
    if candidate_orientation_conflict:
        suppression_reason = "conflicting_candidate_orientations"
    elif inlier_fraction < 0.68:
        suppression_reason = "conflicting_orientation_evidence"
    elif spread > 2.0:
        suppression_reason = "diffuse_orientation_evidence"
    elif absolute_angle < _MIN_OFFER_ANGLE_DEG:
        suppression_reason = "trivial_skew"
    elif absolute_angle > _EXCEPTIONAL_MAX_OFFER_ANGLE_DEG:
        suppression_reason = "outside_skew_correction_range"
    elif len(inlier_families) < 2:
        suppression_reason = "insufficient_independent_evidence_families"
    elif len(supporting_candidates) < 2 and not strong_single_component:
        suppression_reason = "insufficient_independent_candidate_support"
    elif confidence < 0.78:
        suppression_reason = "low_orientation_confidence"
    elif absolute_angle > _NORMAL_MAX_OFFER_ANGLE_DEG and not (
        confidence >= 0.92
        and inlier_fraction >= 0.90
        and len(inlier_families) >= 3
    ):
        suppression_reason = "large_skew_requires_exceptional_confidence"

    return TraceOrientationEstimate(
        selected_ids=selected_ids,
        offered=not suppression_reason,
        detected_skew_deg=winning_angle,
        correction_deg=-winning_angle,
        pivot_mm=pivot,
        confidence=confidence,
        suppression_reason=suppression_reason,
        evidence_count=len(evidence),
        supporting_candidate_count=len(supporting_candidates),
        evidence_families=inlier_families,
        total_supporting_geometry_length_mm=supporting_length,
        line_evidence_weight=family_weights["line"],
        near_linear_cubic_evidence_weight=family_weights["near_linear_cubic"],
        component_axis_evidence_weight=family_weights["component_axis"],
        component_alignment_evidence_weight=family_weights["component_alignment"],
        angular_spread_deg=spread,
        inlier_fraction=inlier_fraction,
        elapsed_seconds=time.perf_counter() - started_at,
    )


__all__ = [
    "TraceOrientationEstimate",
    "estimate_trace_orientation",
    "trace_native_world_geometry",
    "trace_rotation_transform",
]
