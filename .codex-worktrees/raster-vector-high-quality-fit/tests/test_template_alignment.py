from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pytest

from laser_aligner.project import SceneObject
from laser_aligner.templates import TemplateFeature
from laser_aligner.vision.template_alignment import (
    _as_features,
    _orientation_quality,
    _shape_quality,
    align_template,
    rank_templates,
)


@dataclass(frozen=True)
class _CutObject:
    kind: str


@dataclass(frozen=True)
class _Template:
    id: str
    name: str
    features: tuple[TemplateFeature, ...]
    alignment_tolerance_mm: float = 7.0
    objects: tuple[object, ...] = (_CutObject("rectangle"),)


def _grid(*, x_spacing: float = 40.0, y_spacing: float = 30.0) -> tuple[TemplateFeature, ...]:
    features = []
    for row in range(2):
        for column in range(3):
            width = 30.0 if (row, column) == (0, 0) else 24.0
            features.append(
                TemplateFeature(
                    (column * x_spacing, row * y_spacing),
                    width_mm=width,
                    height_mm=10.0,
                )
            )
    return tuple(features)


def test_shape_quality_discriminates_equal_size_shapes_and_preserves_legacy() -> None:
    template_circle = _as_features(
        [TemplateFeature((0, 0), 20, 20, kind="circle")], detections=False
    )[0]
    circle, square, legacy = _as_features(
        [
            {"center_mm": (0, 0), "width_mm": 20, "height_mm": 20, "shape": "circle"},
            {"center_mm": (0, 0), "width_mm": 20, "height_mm": 20, "shape": "rectangle"},
            {"center_mm": (0, 0), "width_mm": 20, "height_mm": 20},
        ],
        detections=True,
    )
    assert _shape_quality(template_circle, circle) == 1.0
    assert _shape_quality(template_circle, square) < 0.1
    assert _shape_quality(template_circle, legacy) == 1.0
    assert _orientation_quality(template_circle, circle, 73.0) == 1.0


def _symmetric_grid() -> tuple[TemplateFeature, ...]:
    return tuple(
        TemplateFeature(
            (column * 40.0, row * 30.0),
            width_mm=24.0,
            height_mm=10.0,
        )
        for row in range(2)
        for column in range(3)
    )


def _detections(
    features: tuple[TemplateFeature, ...],
    *,
    rotation_deg: float,
    translation: tuple[float, float],
    scale: float = 1.0,
    omit: set[int] | None = None,
    inferred: set[int] | None = None,
) -> list[dict[str, object]]:
    angle = math.radians(rotation_deg)
    matrix = np.asarray(
        ((math.cos(angle), -math.sin(angle)), (math.sin(angle), math.cos(angle))),
        dtype=np.float64,
    )
    translation_array = np.asarray(translation, dtype=np.float64)
    output = []
    for index, feature in enumerate(features):
        if index in (omit or set()):
            continue
        center = matrix @ (np.asarray(feature.center_mm) * scale) + translation_array
        output.append(
            {
                "center_mm": [float(center[0]), float(center[1])],
                "width_mm": feature.width_mm * scale,
                "height_mm": feature.height_mm * scale,
                "rotation_deg": feature.rotation_deg + rotation_deg,
                "source": "inferred" if index in (inferred or set()) else "direct",
                "confidence": 0.91,
            }
        )
    # The matcher must not rely on detector ordering.
    return [output[index] for index in np.random.default_rng(17).permutation(len(output))]


def test_recovers_translated_rotated_unordered_grid():
    template = _Template("warning-3x2", "Warning labels 3x2", _grid())
    detections = _detections(
        template.features,
        rotation_deg=17.5,
        translation=(96.0, 42.0),
    )

    result = align_template(template, detections)

    assert result.matched_count == 6
    assert result.direct_match_count == 6
    assert result.coverage == 1.0
    assert result.rms_error_mm is not None and result.rms_error_mm < 1e-6
    assert abs(result.rotation_deg - 17.5) < 1e-4
    assert np.allclose(result.translation_mm, (96.0, 42.0), atol=1e-4)
    assert result.score > 90.0


def test_missing_grid_point_still_aligns_and_reports_coverage():
    template = _Template("warning-3x2", "Warning labels 3x2", _grid())
    detections = _detections(
        template.features,
        rotation_deg=-11.0,
        translation=(73.0, 118.0),
        omit={4},
    )

    result = align_template(template, detections)

    assert result.matched_count == 5
    assert result.coverage == 5 / 6
    assert result.rms_error_mm is not None and result.rms_error_mm < 1e-6
    assert any("5 of 6" in warning for warning in result.warnings)
    assert result.confidence > 0.70


def test_alignment_tolerates_position_noise_and_an_unrelated_detection():
    template = _Template("warning-3x2", "Warning labels 3x2", _grid())
    detections = _detections(
        template.features,
        rotation_deg=13.0,
        translation=(80.0, 40.0),
    )
    offsets = ((0.2, -0.1), (-0.25, 0.3), (0.1, 0.15), (-0.15, -0.2), (0.3, 0.1), (-0.1, 0.2))
    for detection, offset in zip(detections, offsets, strict=True):
        center = detection["center_mm"]
        detection["center_mm"] = [center[0] + offset[0], center[1] + offset[1]]
    detections.append(
        {
            "center_mm": [182.0, 149.0],
            "width_mm": 15.0,
            "height_mm": 6.0,
            "rotation_deg": -40.0,
            "source": "direct",
            "confidence": 0.8,
        }
    )

    result = align_template(template, detections)

    assert result.matched_count == 6
    assert result.rms_error_mm is not None and result.rms_error_mm < 0.35
    assert abs(result.rotation_deg - 13.0) < 0.5
    assert np.allclose(result.translation_mm, (80.0, 40.0), atol=0.35)
    assert result.score > 85.0


def test_wrong_grid_geometry_ranks_below_correct_template():
    correct = _Template("correct", "40 mm pitch", _grid())
    wrong = _Template("wrong", "52 mm pitch", _grid(x_spacing=52.0, y_spacing=38.0))
    detections = _detections(
        correct.features,
        rotation_deg=9.0,
        translation=(105.0, 67.0),
    )

    ranked = rank_templates([wrong, correct], detections)

    assert [candidate.template_id for candidate in ranked] == ["correct", "wrong"]
    assert ranked[0].score > ranked[1].score + 20.0
    assert ranked[0].coverage == 1.0


def test_scale_mismatch_is_diagnostic_and_never_applied():
    template = _Template("scaled", "Scaled capture", _grid(), alignment_tolerance_mm=8.0)
    detections = _detections(
        template.features,
        rotation_deg=6.0,
        translation=(88.0, 54.0),
        scale=1.08,
    )

    result = align_template(template, detections)

    assert result.matched_count == 6
    assert result.scale_ratio is not None and abs(result.scale_ratio - 1.08) < 0.01
    assert any("scale mismatch" in warning and "not applied" in warning for warning in result.warnings)
    # A returned transform is rigid: it preserves the original template spacing.
    first = np.asarray(result.transform_point(template.features[0].center_mm))
    second = np.asarray(result.transform_point(template.features[1].center_mm))
    assert abs(float(np.linalg.norm(second - first)) - 40.0) < 1e-6


def test_feature_dimension_scale_mismatch_is_diagnostic():
    template = _Template("wide", "Larger printed labels", _grid())
    detections = _detections(
        template.features,
        rotation_deg=4.0,
        translation=(92.0, 61.0),
    )
    for detection in detections:
        detection["width_mm"] = float(detection["width_mm"]) * 1.08
        detection["height_mm"] = float(detection["height_mm"]) * 1.08

    result = align_template(template, detections)

    assert result.dimension_scale_ratio is not None
    assert abs(result.dimension_scale_ratio - 1.08) < 0.01
    assert any(
        "feature dimensions" in warning and "not applied" in warning
        for warning in result.warnings
    )


def test_directional_geometry_surfaces_half_turn_pose_ambiguity():
    directional_path = SceneObject.path(
        "layer",
        [
            {
                "points": [[0.0, 0.0], [8.0, 1.0], [2.0, 5.0]],
                "closed": True,
            }
        ],
    )
    template = _Template(
        "directional",
        "Directional symmetric sheet",
        _symmetric_grid(),
        objects=(directional_path,),
    )
    detections = _detections(
        template.features,
        rotation_deg=12.0,
        translation=(60.0, 44.0),
    )

    result = align_template(template, detections)

    assert result.matched_count == len(template.features)
    assert result.pose_ambiguous
    assert not result.ambiguous
    assert any("180-degree pose ambiguity" in warning for warning in result.warnings)


def test_half_turn_symmetry_is_harmless_for_nondirectional_real_objects():
    template = _Template(
        "primitives",
        "Primitive symmetric sheet",
        _symmetric_grid(),
        objects=(
            SceneObject.rectangle("layer"),
            SceneObject.ellipse("layer"),
            SceneObject.line("layer"),
        ),
    )
    detections = _detections(
        template.features,
        rotation_deg=-8.0,
        translation=(72.0, 51.0),
    )

    result = align_template(template, detections)

    assert result.matched_count == len(template.features)
    assert not result.pose_ambiguous
    assert not any("pose ambiguity" in warning for warning in result.warnings)


def test_unknown_test_double_geometry_is_conservatively_directional():
    template = _Template(
        "unknown",
        "Unknown object symmetric sheet",
        _symmetric_grid(),
        objects=(_CutObject("custom-cut"),),
    )
    result = align_template(
        template,
        _detections(
            template.features,
            rotation_deg=2.0,
            translation=(35.0, 48.0),
        ),
    )

    assert result.pose_ambiguous


def test_inferred_detection_counts_at_reduced_weight():
    template = _Template("warning-3x2", "Warning labels 3x2", _grid())
    direct = align_template(
        template,
        _detections(template.features, rotation_deg=4.0, translation=(20.0, 30.0)),
    )
    inferred = align_template(
        template,
        _detections(
            template.features,
            rotation_deg=4.0,
            translation=(20.0, 30.0),
            inferred={5},
        ),
    )

    assert inferred.matched_count == direct.matched_count == 6
    assert inferred.inferred_match_count == 1
    assert inferred.weighted_coverage < direct.weighted_coverage
    assert inferred.score < direct.score
    assert any("reduced weight" in warning for warning in inferred.warnings)


def test_empty_detections_return_non_viable_alignment():
    template = _Template("warning-3x2", "Warning labels 3x2", _grid())

    result = align_template(template, [])

    assert result.matched_count == 0
    assert result.score == 0.0
    assert result.confidence == 0.0
    assert result.rms_error_mm is None
    assert any("No detections" in warning for warning in result.warnings)


def test_nonfinite_detection_features_are_not_allowed_into_alignment_math():
    template = _Template("warning-3x2", "Warning labels 3x2", _grid())
    malformed = [
        {
            "center_mm": [float("nan"), 10.0],
            "width_mm": 20.0,
            "height_mm": 10.0,
        },
        {
            "center_mm": [10.0, 10.0],
            "width_mm": 20.0,
            "height_mm": 10.0,
            "confidence": float("nan"),
        },
    ]

    result = align_template(template, malformed)

    assert result.matched_count == 0
    assert result.detection_count == 0
    assert any("No detections" in warning for warning in result.warnings)


def test_nonfinite_template_alignment_tolerance_is_rejected():
    template = _Template(
        "warning-3x2",
        "Warning labels 3x2",
        _grid(),
        alignment_tolerance_mm=float("nan"),
    )

    with pytest.raises(ValueError, match="tolerance must be finite"):
        align_template(
            template,
            _detections(template.features, rotation_deg=0.0, translation=(0.0, 0.0)),
        )


def test_ranker_surfaces_indistinguishable_templates_as_ambiguous():
    first = _Template("first", "First", _grid())
    second = _Template("second", "Second", _grid())
    detections = _detections(first.features, rotation_deg=3.0, translation=(18.0, 27.0))

    ranked = rank_templates([first, second], detections)

    assert ranked[0].ambiguous
    assert ranked[1].ambiguous
    assert any("ambiguous" in warning.lower() for warning in ranked[0].warnings)
