from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from laser_aligner.project import raster_vectorize
from laser_aligner.project.native_contour_fit import (
    PhysicalContourFitResult,
    fit_physical_contours_to_native_path,
)
from laser_aligner.project.path_geometry import (
    NativePathGeometry,
    PathCubicSegment,
    PathLineSegment,
)
from laser_aligner.project.primitive_recovery import fit_circular_arc_hypothesis
from laser_aligner.project.raster_vectorize import (
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationOptions,
    prepare_pixel_vectorization_source,
    vectorize_pixel_source,
)

_BAR_LENGTH_MM = 40.0
_BAR_WIDTH_MM = 4.0
_BAR_CENTER_MM = np.asarray((50.0, 40.0), dtype=np.float64)


def _deduplicate_closed_contour(points: np.ndarray) -> np.ndarray:
    values = np.asarray(points, dtype=np.float64)
    keep = np.concatenate(
        (
            np.asarray((True,)),
            np.any(np.diff(values, axis=0) != 0.0, axis=1),
        )
    )
    values = values[keep]
    if len(values) > 1 and np.array_equal(values[0], values[-1]):
        values = values[:-1]
    return values


def _quantized_bar(angle_degrees: float, source_pitch_mm: float) -> np.ndarray:
    corners = np.asarray(
        (
            (-_BAR_LENGTH_MM / 2.0, -_BAR_WIDTH_MM / 2.0),
            (_BAR_LENGTH_MM / 2.0, -_BAR_WIDTH_MM / 2.0),
            (_BAR_LENGTH_MM / 2.0, _BAR_WIDTH_MM / 2.0),
            (-_BAR_LENGTH_MM / 2.0, _BAR_WIDTH_MM / 2.0),
        ),
        dtype=np.float64,
    )
    sides: list[np.ndarray] = []
    for start, end in zip(corners, np.roll(corners, -1, axis=0), strict=True):
        sample_count = max(
            24,
            int(np.linalg.norm(end - start) / source_pitch_mm * 4.0),
        )
        parameter = np.linspace(0.0, 1.0, sample_count, endpoint=False)
        sides.append(start + parameter[:, None] * (end - start))
    angle = math.radians(angle_degrees)
    rotation = np.asarray(
        (
            (math.cos(angle), -math.sin(angle)),
            (math.sin(angle), math.cos(angle)),
        ),
        dtype=np.float64,
    )
    physical = np.vstack(sides) @ rotation.T + _BAR_CENTER_MM
    quantized = np.round(physical / source_pitch_mm) * source_pitch_mm
    return _deduplicate_closed_contour(quantized)


def _circle_contour(*, radial_noise_mm: float = 0.0) -> np.ndarray:
    phase = np.linspace(0.0, 1.0, 161, endpoint=False)
    angles = 2.0 * math.pi * phase
    radius = 10.0 + radial_noise_mm * np.sin(6.0 * math.pi * phase)
    return np.column_stack(
        (
            _BAR_CENTER_MM[0] + radius * np.cos(angles),
            _BAR_CENTER_MM[1] + radius * np.sin(angles),
        )
    )


def _quantized_broad_c_contour(source_pitch_mm: float) -> np.ndarray:
    outer_radius_mm = 15.0
    inner_radius_mm = 9.0
    start_angle = math.radians(30.0)
    end_angle = math.radians(330.0)
    outer_angles = np.linspace(start_angle, end_angle, 721)
    inner_angles = np.linspace(end_angle, start_angle, 481)
    outer = _BAR_CENTER_MM + outer_radius_mm * np.column_stack(
        (np.cos(outer_angles), np.sin(outer_angles))
    )
    inner = _BAR_CENTER_MM + inner_radius_mm * np.column_stack(
        (np.cos(inner_angles), np.sin(inner_angles))
    )
    radial_end = np.linspace(outer[-1], inner[0], 65, endpoint=False)[1:]
    radial_start = np.linspace(inner[-1], outer[0], 65, endpoint=False)[1:]
    quantized = (
        np.round(
            np.vstack((outer, radial_end, inner, radial_start))
            / source_pitch_mm
        )
        * source_pitch_mm
    )
    return _deduplicate_closed_contour(quantized)


def _semicircle_and_chord_contour(*, radial_noise_mm: float = 0.0) -> np.ndarray:
    phase = np.linspace(0.0, 1.0, 101)
    angles = -math.pi / 2.0 + math.pi * phase
    radius = 8.0 + radial_noise_mm * np.sin(4.0 * math.pi * phase)
    arc = np.column_stack(
        (
            _BAR_CENTER_MM[0] + radius * np.cos(angles),
            _BAR_CENTER_MM[1] + radius * np.sin(angles),
        )
    )
    chord_parameter = np.linspace(0.0, 1.0, 65, endpoint=False)[1:]
    chord = arc[-1] + chord_parameter[:, None] * (arc[0] - arc[-1])
    return _deduplicate_closed_contour(np.vstack((arc, chord)))


def _freeform_semicircle_and_chord_contour() -> np.ndarray:
    phase = np.linspace(0.0, 1.0, 161)
    angles = -math.pi / 2.0 + math.pi * phase
    radius = 8.0 + 0.5 * np.sin(5.0 * math.pi * phase)
    arc = np.column_stack(
        (
            _BAR_CENTER_MM[0] + radius * np.cos(angles),
            _BAR_CENTER_MM[1] + radius * np.sin(angles),
        )
    )
    chord_parameter = np.linspace(0.0, 1.0, 101, endpoint=False)[1:]
    chord = arc[-1] + chord_parameter[:, None] * (arc[0] - arc[-1])
    return np.vstack((arc, chord))


def _exact_capsule_contour() -> np.ndarray:
    half_straight_length_mm = 12.0
    radius_mm = 5.0
    top = np.column_stack(
        (
            np.linspace(
                -half_straight_length_mm,
                half_straight_length_mm,
                161,
                endpoint=False,
            ),
            np.full(161, -radius_mm),
        )
    )
    right_angles = np.linspace(-math.pi / 2.0, math.pi / 2.0, 101, endpoint=False)
    right = np.column_stack(
        (
            half_straight_length_mm + radius_mm * np.cos(right_angles),
            radius_mm * np.sin(right_angles),
        )
    )
    bottom = np.column_stack(
        (
            np.linspace(
                half_straight_length_mm,
                -half_straight_length_mm,
                161,
                endpoint=False,
            ),
            np.full(161, radius_mm),
        )
    )
    left_angles = np.linspace(
        math.pi / 2.0,
        3.0 * math.pi / 2.0,
        101,
        endpoint=False,
    )
    left = np.column_stack(
        (
            -half_straight_length_mm + radius_mm * np.cos(left_angles),
            radius_mm * np.sin(left_angles),
        )
    )
    return np.vstack((top, right, bottom, left))


def _nonprimitive_contour(kind: str) -> np.ndarray:
    phase = np.linspace(0.0, 2.0 * math.pi, 201, endpoint=False)
    if kind == "ellipse":
        x_coordinates = 12.0 * np.cos(phase)
        y_coordinates = 7.0 * np.sin(phase)
    elif kind == "wavy":
        radius = 9.0 + 0.7 * np.sin(5.0 * phase)
        x_coordinates = radius * np.cos(phase)
        y_coordinates = radius * np.sin(phase)
    elif kind == "freeform":
        x_coordinates = 9.0 * np.cos(phase) + 1.1 * np.cos(2.0 * phase)
        y_coordinates = 6.5 * np.sin(phase) + 0.8 * np.sin(3.0 * phase)
    else:  # pragma: no cover - local fixture guard
        raise AssertionError(f"Unsupported contour kind: {kind}")
    return np.column_stack(
        (
            _BAR_CENTER_MM[0] + x_coordinates,
            _BAR_CENTER_MM[1] + y_coordinates,
        )
    )


def _frame_with_margin(points: np.ndarray) -> tuple[float, float, float, float]:
    minimum = np.min(points, axis=0) - 1.0
    maximum = np.max(points, axis=0) + 1.0
    return (
        float(minimum[0]),
        float(minimum[1]),
        float(maximum[0]),
        float(maximum[1]),
    )


def _fit_physical(
    points: np.ndarray,
    *,
    source_pitch_mm: float,
    fitting_tolerance_mm: float,
    recover_primitives: bool = True,
    frame_bounds_mm: tuple[float, float, float, float] | None = None,
) -> PhysicalContourFitResult:
    return fit_physical_contours_to_native_path(
        (points,),
        (None,),
        source_pixel_spacing_mm=(source_pitch_mm, source_pitch_mm),
        fitting_tolerance_mm=fitting_tolerance_mm,
        frame_bounds_mm=frame_bounds_mm or _frame_with_margin(points),
        recover_primitives=recover_primitives,
    )


def _physical_segment_vectors(result: PhysicalContourFitResult) -> tuple[np.ndarray, ...]:
    subpath = result.geometry.subpaths[0]
    scale = np.asarray((result.width_mm, result.height_mm), dtype=np.float64)
    previous = np.asarray(subpath.start, dtype=np.float64)
    vectors: list[np.ndarray] = []
    for segment in subpath.segments:
        endpoint = np.asarray(segment.to, dtype=np.float64)
        vectors.append((endpoint - previous) * scale)
        previous = endpoint
    return tuple(vectors)


def _guardrail_piece(index: int, *, line: bool) -> raster_vectorize._FittedPiece:
    start = np.asarray((float(index), 0.0), dtype=np.float64)
    end = np.asarray((float(index + 1), 0.0), dtype=np.float64)
    segment: raster_vectorize._FittedSegment
    if line:
        segment = raster_vectorize._LineSegment(start, end, 0.0)
    else:
        step = (end - start) / 3.0
        segment = raster_vectorize._CubicSegment(
            start,
            start + step,
            start + 2.0 * step,
            end,
            0.0,
        )
    return raster_vectorize._FittedPiece(
        segment=segment,
        target_points=np.vstack((start, end)),
        target_parameters=np.asarray((0.0, 1.0), dtype=np.float64),
        start_tangent=np.asarray((1.0, 0.0), dtype=np.float64),
        end_tangent=np.asarray((-1.0, 0.0), dtype=np.float64),
        hard_start=False,
        hard_end=False,
        sample_error_sum_mm=0.0,
        sample_squared_error_sum_mm2=0.0,
        sample_count=2,
        target_indices=np.asarray((index, index + 1), dtype=np.int64),
    )


@pytest.mark.parametrize("angle_degrees", (0.0, 90.0, 1.7, 13.0, 45.0))
def test_quantized_closed_bar_recovers_one_tls_line_per_side(
    angle_degrees: float,
) -> None:
    source_pitch_mm = 0.2
    contour = _quantized_bar(angle_degrees, source_pitch_mm)

    result = _fit_physical(
        contour,
        source_pitch_mm=source_pitch_mm,
        fitting_tolerance_mm=0.35,
    )

    subpath = result.geometry.subpaths[0]
    metrics = result.primitive_recovery
    assert subpath.closed
    assert len(subpath.segments) == 4
    assert all(isinstance(segment, PathLineSegment) for segment in subpath.segments)
    assert subpath.segments[-1].to == subpath.start
    assert metrics.recovered_line_count == 4
    assert metrics.recovered_arc_count == 0
    assert metrics.baseline_segment_count > metrics.final_segment_count == 4
    assert metrics.recovered_line_length_mm == pytest.approx(
        2.0 * (_BAR_LENGTH_MM + _BAR_WIDTH_MM),
        abs=0.6,
    )
    assert metrics.maximum_residual_mm <= 0.75 * source_pitch_mm
    assert metrics.rms_residual_mm <= 0.40 * source_pitch_mm
    assert metrics.maximum_endpoint_adjustment_mm <= 0.75 * source_pitch_mm

    vectors = _physical_segment_vectors(result)
    lengths = np.asarray([np.linalg.norm(vector) for vector in vectors])
    np.testing.assert_allclose(np.sort(lengths)[:2], _BAR_WIDTH_MM, atol=0.45)
    np.testing.assert_allclose(np.sort(lengths)[2:], _BAR_LENGTH_MM, atol=0.45)
    angle = math.radians(angle_degrees)
    long_direction = np.asarray((math.cos(angle), math.sin(angle)))
    short_direction = np.asarray((-math.sin(angle), math.cos(angle)))
    for vector, length in zip(vectors, lengths, strict=True):
        is_long_side = length > 0.5 * _BAR_LENGTH_MM
        expected = long_direction if is_long_side else short_direction
        angular_tolerance_degrees = (
            0.4
            if is_long_side
            else math.degrees(math.atan2(source_pitch_mm, _BAR_WIDTH_MM))
        )
        alignment = abs(float(np.dot(vector / length, expected)))
        assert alignment > math.cos(math.radians(angular_tolerance_degrees))


def test_recovered_line_follows_eligible_displaced_source_edge_evidence() -> None:
    raw_points = np.column_stack(
        (
            np.linspace(-12.0, 12.0, 97),
            np.zeros(97, dtype=np.float64),
        )
    )
    evidence_points = raw_points.copy()
    evidence_points[1:-1, 1] = 0.04
    audit = raster_vectorize._PrimitiveRecoveryAudit()

    candidate = raster_vectorize._attempt_recovered_line(
        np.arange(len(raw_points), dtype=np.int64),
        raw_points,
        evidence_points,
        np.ones(len(raw_points), dtype=bool),
        0.2,
        (0.1, 0.1),
        audit,
        hard_start=True,
        hard_end=True,
    )

    assert candidate is not None
    hypothesis = candidate.line_hypothesis
    assert hypothesis is not None
    midpoint = 0.5 * (hypothesis.start_projection + hypothesis.end_projection)
    assert abs(float(midpoint[1]) - 0.04) < abs(float(midpoint[1]))
    piece = raster_vectorize._line_piece_from_candidate(
        candidate,
        hypothesis.start_projection,
        hypothesis.end_projection,
        0.2,
        0,
    )
    assert piece is not None
    raw_maximum, raw_rms, _raw_errors = raster_vectorize._line_geometry_metrics(
        raw_points,
        piece.segment.start,
        piece.segment.end,
    )
    model_maximum, model_rms, _model_errors = (
        raster_vectorize._line_geometry_metrics(
            candidate.model_points,
            piece.segment.start,
            piece.segment.end,
        )
    )
    assert raw_maximum <= 0.2
    assert raw_rms <= max(hypothesis.error_budget.rms_mm, 0.35 * 0.2)
    assert model_maximum <= hypothesis.error_budget.maximum_mm
    assert model_rms <= hypothesis.error_budget.rms_mm
    assert max(
        float(np.linalg.norm(piece.segment.start - raw_points[0])),
        float(np.linalg.norm(piece.segment.end - raw_points[-1])),
    ) <= hypothesis.error_budget.endpoint_adjustment_mm


@pytest.mark.parametrize("radial_noise_mm", (0.0, 0.015), ids=("exact", "noisy"))
def test_full_circle_recovers_canonical_cubics_and_diagnostics(
    radial_noise_mm: float,
) -> None:
    result = _fit_physical(
        _circle_contour(radial_noise_mm=radial_noise_mm),
        source_pitch_mm=0.1,
        fitting_tolerance_mm=0.2,
    )

    subpath = result.geometry.subpaths[0]
    metrics = result.primitive_recovery
    assert subpath.closed
    assert len(subpath.segments) == 4
    assert all(isinstance(segment, PathCubicSegment) for segment in subpath.segments)
    assert subpath.segments[-1].to == subpath.start
    assert metrics.recovered_line_count == 0
    assert metrics.recovered_arc_count == 1
    assert metrics.canonical_arc_cubic_count == 4
    assert metrics.freeform_cubic_count == 0
    assert metrics.final_segment_count == len(subpath.segments)
    assert metrics.recovered_arc_length_mm == pytest.approx(20.0 * math.pi, abs=0.02)
    assert metrics.maximum_residual_mm <= 0.02
    if radial_noise_mm:
        assert metrics.rms_residual_mm > 0.0
    else:
        assert metrics.rms_residual_mm < 1e-10


def test_quantized_broad_c_recovers_bounded_conceptual_arcs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_pitch_mm = 0.1
    captured_hypotheses: dict[int, raster_vectorize.CircularArcHypothesis] = {}
    original_recovery = raster_vectorize._recover_geometric_primitives

    def recording_recovery(
        pieces: list[raster_vectorize._FittedPiece],
        raw_points: np.ndarray,
        evidence_points: np.ndarray,
        evidence_eligible: np.ndarray,
        corners: list[int],
        tolerance_mm: float,
        source_pixel_spacing_mm: tuple[float, float] | None,
        width_mm: float,
        height_mm: float,
        budget: raster_vectorize._ComplexityBudget,
        *,
        enabled: bool,
    ) -> tuple[
        list[raster_vectorize._FittedPiece],
        raster_vectorize.PrimitiveRecoveryMetrics,
    ]:
        recovered, metrics = original_recovery(
            pieces,
            raw_points,
            evidence_points,
            evidence_eligible,
            corners,
            tolerance_mm,
            source_pixel_spacing_mm,
            width_mm,
            height_mm,
            budget,
            enabled=enabled,
        )
        for piece in recovered:
            if piece.primitive_id is not None and piece.arc_hypothesis is not None:
                captured_hypotheses[piece.primitive_id] = piece.arc_hypothesis
        return recovered, metrics

    monkeypatch.setattr(
        raster_vectorize,
        "_recover_geometric_primitives",
        recording_recovery,
    )
    result = _fit_physical(
        _quantized_broad_c_contour(source_pitch_mm),
        source_pitch_mm=source_pitch_mm,
        fitting_tolerance_mm=0.2,
    )

    metrics = result.primitive_recovery
    segments = result.geometry.subpaths[0].segments
    assert metrics.recovered_line_count == 2
    assert metrics.recovered_arc_count == len(captured_hypotheses) == 2
    assert metrics.canonical_arc_cubic_count == 8
    assert metrics.freeform_cubic_count == 0
    assert metrics.final_segment_count == len(segments) == 10
    assert sum(isinstance(segment, PathLineSegment) for segment in segments) == 2
    assert sum(isinstance(segment, PathCubicSegment) for segment in segments) == 8
    assert metrics.maximum_residual_mm <= 0.075
    assert metrics.rms_residual_mm <= 0.04
    assert metrics.maximum_endpoint_adjustment_mm <= 0.075

    hypotheses = sorted(
        captured_hypotheses.values(),
        key=lambda hypothesis: hypothesis.radius_mm,
    )
    np.testing.assert_allclose(
        [hypothesis.radius_mm for hypothesis in hypotheses],
        (9.0, 15.0),
        atol=0.04,
    )
    expected_local_center = _BAR_CENTER_MM - np.asarray(result.center_mm)
    for hypothesis in hypotheses:
        assert float(np.linalg.norm(hypothesis.center - expected_local_center)) <= 0.04
        assert math.degrees(abs(hypothesis.sweep_radians)) == pytest.approx(
            300.0,
            abs=0.35,
        )


@pytest.mark.parametrize("radial_noise_mm", (0.0, 0.01), ids=("exact", "noisy"))
def test_partial_arc_and_line_contour_recovers_both_primitive_kinds(
    radial_noise_mm: float,
) -> None:
    result = _fit_physical(
        _semicircle_and_chord_contour(radial_noise_mm=radial_noise_mm),
        source_pitch_mm=0.1,
        fitting_tolerance_mm=0.2,
    )

    segments = result.geometry.subpaths[0].segments
    metrics = result.primitive_recovery
    line_count = sum(isinstance(segment, PathLineSegment) for segment in segments)
    cubic_count = sum(isinstance(segment, PathCubicSegment) for segment in segments)
    assert line_count == metrics.recovered_line_count == 1
    assert metrics.recovered_arc_count == 1
    assert cubic_count == metrics.canonical_arc_cubic_count >= 2
    assert metrics.freeform_cubic_count == 0
    assert metrics.final_segment_count == line_count + cubic_count
    assert metrics.baseline_segment_count > metrics.final_segment_count
    assert metrics.recovered_line_length_mm == pytest.approx(16.0, abs=0.02)
    assert metrics.recovered_arc_length_mm == pytest.approx(8.0 * math.pi, abs=0.03)
    assert metrics.maximum_residual_mm <= 0.015

    subpath = result.geometry.subpaths[0]
    scale = np.asarray((result.width_mm, result.height_mm), dtype=np.float64)
    starts = [
        np.asarray(subpath.start, dtype=np.float64),
        *(
            np.asarray(segment.to, dtype=np.float64)
            for segment in subpath.segments[:-1]
        ),
    ]
    corner_alignments: list[float] = []
    for index, (start, segment) in enumerate(
        zip(starts, subpath.segments, strict=True)
    ):
        following = subpath.segments[(index + 1) % len(subpath.segments)]
        if isinstance(segment, PathLineSegment) == isinstance(
            following,
            PathLineSegment,
        ):
            continue
        endpoint = np.asarray(segment.to, dtype=np.float64)
        following_endpoint = np.asarray(following.to, dtype=np.float64)
        outgoing = (
            endpoint - np.asarray(segment.control_2, dtype=np.float64)
            if isinstance(segment, PathCubicSegment)
            else endpoint - start
        ) * scale
        incoming = (
            np.asarray(following.control_1, dtype=np.float64) - endpoint
            if isinstance(following, PathCubicSegment)
            else following_endpoint - endpoint
        ) * scale
        corner_alignments.append(
            float(
                np.dot(outgoing, incoming)
                / (np.linalg.norm(outgoing) * np.linalg.norm(incoming))
            )
        )
    assert len(corner_alignments) == 2
    assert max(abs(alignment) for alignment in corner_alignments) < 0.01


def test_mixed_line_and_freeform_recovery_keeps_failed_group_pieces_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contour = _freeform_semicircle_and_chord_contour()
    captured_baseline: tuple[raster_vectorize._FittedPiece, ...] = ()
    captured_recovered: tuple[raster_vectorize._FittedPiece, ...] = ()
    original_recovery = raster_vectorize._recover_geometric_primitives

    def recording_recovery(
        pieces: list[raster_vectorize._FittedPiece],
        raw_points: np.ndarray,
        evidence_points: np.ndarray,
        evidence_eligible: np.ndarray,
        corners: list[int],
        tolerance_mm: float,
        source_pixel_spacing_mm: tuple[float, float] | None,
        width_mm: float,
        height_mm: float,
        budget: raster_vectorize._ComplexityBudget,
        *,
        enabled: bool,
    ) -> tuple[
        list[raster_vectorize._FittedPiece],
        raster_vectorize.PrimitiveRecoveryMetrics,
    ]:
        nonlocal captured_baseline, captured_recovered
        captured_baseline = tuple(pieces)
        recovered, metrics = original_recovery(
            pieces,
            raw_points,
            evidence_points,
            evidence_eligible,
            corners,
            tolerance_mm,
            source_pixel_spacing_mm,
            width_mm,
            height_mm,
            budget,
            enabled=enabled,
        )
        captured_recovered = tuple(recovered)
        return recovered, metrics

    monkeypatch.setattr(
        raster_vectorize,
        "_recover_geometric_primitives",
        recording_recovery,
    )
    result = _fit_physical(
        contour,
        source_pitch_mm=0.1,
        fitting_tolerance_mm=0.2,
    )

    metrics = result.primitive_recovery
    retained_freeform = tuple(
        piece for piece in captured_recovered if piece.primitive_id is None
    )
    assert len(captured_baseline) == metrics.baseline_segment_count == 9
    assert len(captured_recovered) == metrics.final_segment_count == 7
    assert len(retained_freeform) == 6
    assert all(
        any(piece is baseline_piece for baseline_piece in captured_baseline)
        for piece in retained_freeform
    )
    assert metrics.recovered_line_count == 1
    assert metrics.recovered_line_length_mm == pytest.approx(16.0, abs=1e-10)
    assert metrics.recovered_arc_count == 0
    assert metrics.freeform_cubic_count == 4
    assert metrics.hypothesis_count == 3


def test_line_geometry_rejects_reversed_emitted_endpoint_order() -> None:
    points = np.column_stack(
        (
            np.linspace(-10.0, 10.0, 41),
            np.linspace(-3.0, 5.0, 41),
        )
    )
    hypothesis = raster_vectorize.fit_line_hypothesis(
        points,
        0.2,
        (0.1, 0.1),
    )
    assert hypothesis is not None
    reversed_points = points[::-1].copy()
    candidate = raster_vectorize._PrimitiveCandidate(
        target_indices=np.arange(len(points) - 1, -1, -1, dtype=np.int64),
        raw_points=reversed_points,
        model_points=reversed_points.copy(),
        hard_start=True,
        hard_end=True,
        line_hypothesis=hypothesis,
    )

    assert not raster_vectorize._line_geometry_is_supported(
        candidate,
        hypothesis.end_projection,
        hypothesis.start_projection,
        0.2,
    )


def test_smooth_capsule_refines_legacy_partitions_to_source_backed_tangencies() -> None:
    result = _fit_physical(
        _exact_capsule_contour(),
        source_pitch_mm=0.1,
        fitting_tolerance_mm=0.2,
        frame_bounds_mm=(-20.0, -8.0, 20.0, 8.0),
    )

    subpath = result.geometry.subpaths[0]
    metrics = result.primitive_recovery
    assert len(subpath.segments) == 6
    assert (
        sum(isinstance(segment, PathLineSegment) for segment in subpath.segments)
        == 2
    )
    assert (
        sum(isinstance(segment, PathCubicSegment) for segment in subpath.segments)
        == 4
    )
    assert metrics.recovered_line_count == 2
    assert metrics.recovered_arc_count == 2
    assert metrics.canonical_arc_cubic_count == 4
    assert metrics.freeform_cubic_count == 0
    assert metrics.final_segment_count == 6
    assert subpath.segments[-1].to == subpath.start

    scale = np.asarray((result.width_mm, result.height_mm), dtype=np.float64)
    starts = [
        np.asarray(subpath.start, dtype=np.float64),
        *(
            np.asarray(segment.to, dtype=np.float64)
            for segment in subpath.segments[:-1]
        ),
    ]
    for index, (start, segment) in enumerate(
        zip(starts, subpath.segments, strict=True)
    ):
        following = subpath.segments[(index + 1) % len(subpath.segments)]
        endpoint = np.asarray(segment.to, dtype=np.float64)
        following_endpoint = np.asarray(following.to, dtype=np.float64)
        outgoing = (
            endpoint - np.asarray(segment.control_2, dtype=np.float64)
            if isinstance(segment, PathCubicSegment)
            else endpoint - start
        ) * scale
        incoming = (
            np.asarray(following.control_1, dtype=np.float64) - endpoint
            if isinstance(following, PathCubicSegment)
            else following_endpoint - endpoint
        ) * scale
        alignment = float(
            np.dot(outgoing, incoming)
            / (np.linalg.norm(outgoing) * np.linalg.norm(incoming))
        )
        assert alignment > 1.0 - 1e-12


def test_adjusted_arc_sweep_reports_bounded_angular_endpoint_deviation() -> None:
    angles = np.linspace(0.0, math.pi / 2.0, 101)
    points = np.column_stack((10.0 * np.cos(angles), 10.0 * np.sin(angles)))
    hypothesis = fit_circular_arc_hypothesis(
        points,
        0.2,
        (0.1, 0.1),
        closed=False,
    )
    assert hypothesis is not None
    candidate = raster_vectorize._PrimitiveCandidate(
        target_indices=np.arange(len(points), dtype=np.int64),
        raw_points=points.copy(),
        model_points=points.copy(),
        hard_start=False,
        hard_end=False,
        arc_hypothesis=hypothesis,
    )
    angular_shift = 0.04 / hypothesis.radius_mm
    adjusted_start = hypothesis.center + hypothesis.radius_mm * np.asarray(
        (
            math.cos(hypothesis.start_angle_radians + angular_shift),
            math.sin(hypothesis.start_angle_radians + angular_shift),
        )
    )
    end_angle = hypothesis.start_angle_radians + hypothesis.sweep_radians
    unchanged_end = hypothesis.center + hypothesis.radius_mm * np.asarray(
        (math.cos(end_angle), math.sin(end_angle))
    )
    endpoint_deviation = float(np.linalg.norm(points[0] - adjusted_start))
    assert endpoint_deviation < hypothesis.error_budget.endpoint_adjustment_mm

    pieces = raster_vectorize._arc_pieces_from_candidate(
        candidate,
        adjusted_start,
        unchanged_end,
        0.2,
        0,
        closed=False,
    )

    assert pieces is not None
    assert (
        max(piece.segment.fitting_error_mm for piece in pieces)
        >= endpoint_deviation - 1e-12
    )
    assert (
        sum(piece.sample_error_sum_mm for piece in pieces)
        >= endpoint_deviation - 1e-12
    )
    emitted_hypothesis = pieces[0].arc_hypothesis
    assert emitted_hypothesis is not None
    assert emitted_hypothesis.maximum_residual_mm >= endpoint_deviation - 1e-12
    metrics = raster_vectorize._recovered_primitive_metrics(
        pieces,
        pieces,
        raster_vectorize._PrimitiveRecoveryAudit(),
        (0.1, 0.1),
        endpoint_deviation,
    )
    assert metrics.maximum_residual_mm >= endpoint_deviation - 1e-12
    assert metrics.rms_residual_mm > 0.0


@pytest.mark.parametrize("kind", ("ellipse", "wavy", "freeform"))
def test_nonprimitive_contours_fall_back_to_the_unchanged_freeform_fit(
    kind: str,
) -> None:
    contour = _nonprimitive_contour(kind)
    frame = _frame_with_margin(contour)

    enabled = _fit_physical(
        contour,
        source_pitch_mm=0.1,
        fitting_tolerance_mm=0.2,
        frame_bounds_mm=frame,
    )
    disabled = _fit_physical(
        contour,
        source_pitch_mm=0.1,
        fitting_tolerance_mm=0.2,
        recover_primitives=False,
        frame_bounds_mm=frame,
    )

    metrics = enabled.primitive_recovery
    assert enabled.geometry.to_dict() == disabled.geometry.to_dict()
    assert metrics.recovered_line_count == 0
    assert metrics.recovered_arc_count == 0
    assert metrics.baseline_segment_count == metrics.final_segment_count
    assert metrics.final_segment_count == enabled.fitted_segment_count
    assert metrics.freeform_cubic_count > 0
    assert metrics.hypothesis_count > 0


def test_recovery_enable_disable_seam_preserves_the_baseline_fit() -> None:
    contour = _quantized_bar(13.0, 0.2)
    frame = _frame_with_margin(contour)

    enabled = _fit_physical(
        contour,
        source_pitch_mm=0.2,
        fitting_tolerance_mm=0.35,
        frame_bounds_mm=frame,
    )
    disabled = _fit_physical(
        contour,
        source_pitch_mm=0.2,
        fitting_tolerance_mm=0.35,
        recover_primitives=False,
        frame_bounds_mm=frame,
    )

    enabled_metrics = enabled.primitive_recovery
    disabled_metrics = disabled.primitive_recovery
    assert enabled.geometry.to_dict() != disabled.geometry.to_dict()
    assert enabled_metrics.baseline_segment_count == disabled_metrics.final_segment_count
    assert enabled_metrics.final_segment_count < enabled_metrics.baseline_segment_count
    assert disabled_metrics.baseline_segment_count == disabled_metrics.final_segment_count
    assert disabled_metrics.recovered_line_count == 0
    assert disabled_metrics.recovered_arc_count == 0
    assert disabled_metrics.hypothesis_count == 0


def test_recovered_topology_rejection_retries_the_complete_baseline_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contour = _quantized_bar(13.0, 0.2)
    frame = _frame_with_margin(contour)
    validation_states: list[bool] = []
    original_validation = raster_vectorize._validate_authoritative_native_topology

    def reject_recovered_pass(
        contours: tuple[raster_vectorize.RasterVectorizedContour, ...],
        width_mm: float,
        height_mm: float,
    ) -> None:
        has_recovered_primitive = any(
            fitted.primitive_recovery.recovered_line_count
            or fitted.primitive_recovery.recovered_arc_count
            for fitted in contours
        )
        validation_states.append(bool(has_recovered_primitive))
        if has_recovered_primitive:
            raise raster_vectorize.RasterVectorizationError(
                "forced recovered topology rejection"
            )
        original_validation(contours, width_mm, height_mm)

    monkeypatch.setattr(
        raster_vectorize,
        "_validate_authoritative_native_topology",
        reject_recovered_pass,
    )
    fallback = _fit_physical(
        contour,
        source_pitch_mm=0.2,
        fitting_tolerance_mm=0.35,
        frame_bounds_mm=frame,
    )
    explicit_baseline = _fit_physical(
        contour,
        source_pitch_mm=0.2,
        fitting_tolerance_mm=0.35,
        recover_primitives=False,
        frame_bounds_mm=frame,
    )

    assert validation_states == [True, False, False]
    assert fallback == explicit_baseline
    metrics = fallback.primitive_recovery
    assert metrics.baseline_segment_count == metrics.final_segment_count
    assert metrics.recovered_line_count == 0
    assert metrics.recovered_arc_count == 0
    assert metrics.canonical_arc_cubic_count == 0
    assert metrics.hypothesis_count == 0


def test_disabled_recovery_never_calls_primitive_hypothesis_fitters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_fit(*_args: object, **_kwargs: object) -> None:
        pytest.fail("disabled primitive recovery must not fit hypotheses")

    monkeypatch.setattr(
        raster_vectorize,
        "fit_line_hypothesis",
        unexpected_fit,
    )
    monkeypatch.setattr(
        raster_vectorize,
        "fit_circular_arc_hypothesis",
        unexpected_fit,
    )

    result = _fit_physical(
        _quantized_bar(13.0, 0.2),
        source_pitch_mm=0.2,
        fitting_tolerance_mm=0.35,
        recover_primitives=False,
    )

    metrics = result.primitive_recovery
    assert metrics.hypothesis_count == 0
    assert metrics.recovered_line_count == 0
    assert metrics.recovered_arc_count == 0


def test_more_than_64_hard_corners_skip_primitive_hypothesis_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_fit(*_args: object, **_kwargs: object) -> None:
        pytest.fail("hard-corner overflow must skip primitive hypotheses")

    monkeypatch.setattr(
        raster_vectorize,
        "fit_line_hypothesis",
        unexpected_fit,
    )
    monkeypatch.setattr(
        raster_vectorize,
        "fit_circular_arc_hypothesis",
        unexpected_fit,
    )
    phase = np.linspace(0.0, 2.0 * math.pi, 65, endpoint=False)
    raw_points = np.column_stack((np.cos(phase), np.sin(phase)))
    baseline = [_guardrail_piece(0, line=True)]

    recovered, metrics = raster_vectorize._recover_geometric_primitives(
        baseline,
        raw_points,
        raw_points.copy(),
        np.ones(len(raw_points), dtype=bool),
        list(range(65)),
        0.2,
        (0.1, 0.1),
        4.0,
        4.0,
        raster_vectorize._ComplexityBudget(fitted_segments=1),
        enabled=True,
    )

    assert recovered == baseline
    assert metrics.baseline_segment_count == metrics.final_segment_count == 1
    assert metrics.hypothesis_count == 0


def test_smooth_group_hypotheses_are_capped_at_256_per_contour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert raster_vectorize._MAXIMUM_PRIMITIVE_RECOVERY_HYPOTHESES == 256
    attempt_count = 0

    def counted_attempt(*args: object, **_kwargs: object) -> object:
        nonlocal attempt_count
        attempt_count += 1
        audit = args[6]
        assert isinstance(audit, raster_vectorize._PrimitiveRecoveryAudit)
        audit.hypothesis_count += 1
        return object()

    monkeypatch.setattr(
        raster_vectorize,
        "_attempt_recovered_line",
        counted_attempt,
    )
    monkeypatch.setattr(
        raster_vectorize,
        "_attempt_recovered_arc",
        counted_attempt,
    )
    raw_points = np.column_stack(
        (
            np.arange(259, dtype=np.float64),
            np.zeros(259, dtype=np.float64),
        )
    )
    eligible = np.ones(len(raw_points), dtype=bool)
    at_limit = [
        _guardrail_piece(index, line=not index % 2)
        for index in range(256)
    ]
    audit = raster_vectorize._PrimitiveRecoveryAudit()

    candidates, closed = raster_vectorize._primitive_candidates_from_smooth_pieces(
        at_limit,
        raw_points,
        raw_points.copy(),
        eligible,
        0.2,
        (0.1, 0.1),
        audit,
    )

    assert not closed
    assert candidates is not None and len(candidates) == 256
    assert attempt_count == audit.hypothesis_count == 256

    overflow = [
        *at_limit,
        _guardrail_piece(256, line=True),
        _guardrail_piece(257, line=False),
    ]
    overflow_audit = raster_vectorize._PrimitiveRecoveryAudit()
    overflow_candidates, overflow_closed = (
        raster_vectorize._primitive_candidates_from_smooth_pieces(
            overflow,
            raw_points,
            raw_points.copy(),
            eligible,
            0.2,
            (0.1, 0.1),
            overflow_audit,
        )
    )

    assert overflow_candidates is None
    assert not overflow_closed
    assert overflow_audit.hypothesis_count == 0
    assert attempt_count == 256


def test_cyclic_contour_order_produces_identical_geometry_and_diagnostics() -> None:
    contour = _quantized_bar(13.0, 0.2)
    frame = _frame_with_margin(contour)
    results = tuple(
        _fit_physical(
            np.roll(contour, shift, axis=0),
            source_pitch_mm=0.2,
            fitting_tolerance_mm=0.35,
            frame_bounds_mm=frame,
        )
        for shift in (0, 17, 73, 199)
    )

    expected_geometry = results[0].geometry.to_dict()
    expected_metrics = results[0].primitive_recovery
    assert all(result.geometry.to_dict() == expected_geometry for result in results[1:])
    assert all(result.primitive_recovery == expected_metrics for result in results[1:])


@pytest.mark.parametrize(
    ("source_pitch_mm", "fitting_tolerance_mm"),
    ((0.1, 0.2), (0.2, 0.4)),
)
def test_line_recovery_budget_tracks_physical_source_pixel_scale(
    source_pitch_mm: float,
    fitting_tolerance_mm: float,
) -> None:
    result = _fit_physical(
        _quantized_bar(45.0, source_pitch_mm),
        source_pitch_mm=source_pitch_mm,
        fitting_tolerance_mm=fitting_tolerance_mm,
    )

    metrics = result.primitive_recovery
    assert metrics.source_pixel_scale_mm == pytest.approx(source_pitch_mm)
    assert metrics.recovered_line_count == 4
    assert metrics.final_segment_count == 4
    assert metrics.maximum_residual_mm <= 0.65 * source_pitch_mm
    assert metrics.rms_residual_mm <= 0.40 * source_pitch_mm


def test_large_tolerance_does_not_promote_an_ellipse_to_a_circle() -> None:
    contour = _nonprimitive_contour("ellipse")

    result = _fit_physical(
        contour,
        source_pitch_mm=0.1,
        fitting_tolerance_mm=2.0,
    )

    metrics = result.primitive_recovery
    assert metrics.recovered_arc_count == 0
    assert metrics.recovered_line_count == 0
    assert metrics.baseline_segment_count == metrics.final_segment_count


def test_recovered_mixed_geometry_serializes_as_native_lines_and_cubics_only() -> None:
    result = _fit_physical(
        _semicircle_and_chord_contour(radial_noise_mm=0.01),
        source_pitch_mm=0.1,
        fitting_tolerance_mm=0.2,
    )

    payload = result.geometry.to_dict()
    segment_types = {segment["type"] for subpath in payload["subpaths"] for segment in subpath["segments"]}
    assert payload["path_version"] == 1
    assert segment_types == {"line", "cubic"}
    assert "arc" not in repr(payload).lower()
    assert NativePathGeometry.from_dict(payload).to_dict() == payload


def test_pixel_vectorization_metadata_reports_primitive_before_and_final_counts() -> None:
    pixels = np.full((128, 160, 4), 255, dtype=np.uint8)
    box = cv2.boxPoints(((80.0, 64.0), (120.0, 20.0), 1.7))
    cv2.fillConvexPoly(pixels, np.rint(box).astype(np.int32), (0, 0, 0, 255))
    source = prepare_pixel_vectorization_source(pixels)
    options = RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=127,
        minimum_feature_area_mm2=0.0,
        smoothing_mm=0.0,
        simplification_tolerance_mm=0.35,
        contour_output=RasterContourOutput.ALL_CONTOURS,
    )

    result = vectorize_pixel_source(
        source,
        options,
        displayed_width_mm=32.0,
        displayed_height_mm=25.6,
    )

    metadata = result.metadata()
    assert (
        metadata["raster_vectorization_primitive_baseline_segments"]
        >= metadata["raster_vectorization_primitive_final_segments"]
    )
    assert (
        metadata["raster_vectorization_primitive_final_segments"]
        == metadata["raster_vectorization_fitted_segments"]
        == result.fitted_segment_count
    )
    assert metadata["raster_vectorization_recovered_lines"] > 0
    assert metadata["raster_vectorization_source_pixel_scale_mm"] == pytest.approx(0.2)
