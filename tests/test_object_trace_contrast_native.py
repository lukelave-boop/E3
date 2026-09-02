from __future__ import annotations

import cv2
import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.project import NativePathGeometry, PathCubicSegment, PathLineSegment
from laser_aligner.vision import object_trace
from laser_aligner.vision.object_trace import TraceOptions, detect_objects


def _independent_shapes_with_hole() -> np.ndarray:
    image = np.full((400, 600, 3), 225, dtype=np.uint8)
    cv2.rectangle(image, (60, 70), (190, 180), (45, 45, 45), -1)
    cv2.circle(image, (360, 125), 60, (40, 40, 40), -1)
    cv2.circle(image, (360, 125), 25, (225, 225, 225), -1)
    return image


def _native_contrast_options() -> TraceOptions:
    return TraceOptions(
        detection_mode="contrast",
        output_mode="native",
        regular_grid=False,
        min_area_mm2=50.0,
        min_width_mm=5.0,
        min_height_mm=5.0,
        confidence_threshold=0.0,
        native_fitting_tolerance_mm=0.10,
    )


def test_removed_cutout_mode_and_entrypoints_are_rejected() -> None:
    with pytest.raises(ValueError, match="detection mode"):
        TraceOptions.from_mapping({"detection_mode": "cutout"})
    assert not hasattr(object_trace, "prepare_cutout_frame")
    assert not hasattr(object_trace, "detect_prepared_cutouts")
    assert not hasattr(object_trace, "detect_seeded_cutouts")


def test_contrast_native_fits_independent_candidates_and_preserves_hole_tree() -> None:
    result = detect_objects(
        _independent_shapes_with_hole(),
        _native_contrast_options(),
        WorkArea(0.0, 150.0, 0.0, 100.0),
        4.0,
    )

    assert result.mode_used == "contrast"
    assert len(result.detections) == 2
    assert all(detection.source == "direct" for detection in result.detections)
    assert all(detection.selected_default for detection in result.detections)
    assert all(detection.native_verified for detection in result.detections)
    assert all(detection.diagnostics["native_fit_status"] == "verified" for detection in result.detections)

    rectangle = min(result.detections, key=lambda detection: detection.center_mm[0])
    washer = max(result.detections, key=lambda detection: detection.center_mm[0])
    assert rectangle.shape == "contour"
    assert washer.shape == "contour"
    rectangle_geometry = NativePathGeometry.from_dict(rectangle.native_path or {})
    washer_geometry = NativePathGeometry.from_dict(washer.native_path or {})

    assert len(rectangle_geometry.subpaths) == 1
    assert len(washer_geometry.subpaths) == 2
    assert washer.diagnostics["contour_depths"] == [0, 1]
    assert washer.diagnostics["contour_parents"] == [None, 0]
    assert washer.diagnostics["mask_source"] == "raster_non_grid"
    segment_types = {
        type(segment)
        for geometry in (rectangle_geometry, washer_geometry)
        for subpath in geometry.subpaths
        for segment in subpath.segments
    }
    assert PathLineSegment in segment_types
    assert PathCubicSegment in segment_types
    assert washer.diagnostics["maximum_estimated_deviation_mm"] < 0.13


def test_manual_contrast_outer_silhouette_emits_only_top_level_exteriors() -> None:
    common = {
        **_native_contrast_options().to_dict(),
        "contrast_threshold_mode": "manual",
        "contrast_threshold": 128,
    }
    image = _independent_shapes_with_hole()
    work_area = WorkArea(0.0, 150.0, 0.0, 100.0)
    full = detect_objects(image, TraceOptions(**common), work_area, 4.0)
    outer = detect_objects(
        image,
        TraceOptions(**{**common, "trace_detail": "outer_silhouette"}),
        work_area,
        4.0,
    )

    full_washer = max(full.detections, key=lambda detection: detection.center_mm[0])
    outer_washer = max(outer.detections, key=lambda detection: detection.center_mm[0])
    outer_geometry = NativePathGeometry.from_dict(outer_washer.native_path or {})

    assert len(full_washer.vector_contours_mm) == 2
    assert len(outer_washer.vector_contours_mm) == 1
    assert len(outer_geometry.subpaths) == 1
    assert outer_washer.diagnostics["contour_parents"] == [None]
    assert outer_washer.diagnostics["contour_depths"] == [0]
    assert outer_washer.diagnostics["trace_detail"] == "outer_silhouette"
    assert outer_washer.diagnostics["outer_only"] is True
    assert outer_washer.diagnostics["area_basis"] == "outer_silhouette_output"
    assert outer_washer.diagnostics["root_contour_count"] == 1
    assert outer_washer.diagnostics["output_contour_count"] == 1
    assert outer_washer.diagnostics["internal_contours_enumerated"] is False
    assert outer_washer.diagnostics["ignored_internal_contour_count"] is None
    assert outer_washer.area_mm2 > full_washer.area_mm2
    assert (
        outer_washer.diagnostics["foreground_mask_sha256"]
        == full_washer.diagnostics["foreground_mask_sha256"]
    )
    assert outer.options.trace_detail == "outer_silhouette"
    assert outer.diagnostics["trace_detail"] == "outer_silhouette"
    assert outer.diagnostics["outer_only"] is True


def test_native_contrast_preview_contours_match_fitted_geometry_envelopes() -> None:
    result = detect_objects(
        _independent_shapes_with_hole(),
        _native_contrast_options(),
        WorkArea(0.0, 150.0, 0.0, 100.0),
        4.0,
    )

    for detection in result.detections:
        assert detection.vector_contours_mm
        all_points = np.asarray(
            [
                point
                for contour in detection.vector_contours_mm
                for point in contour
            ],
            dtype=np.float64,
        )
        center = np.asarray(detection.native_center_mm, dtype=np.float64)
        assert all_points[:, 0].min() < center[0] < all_points[:, 0].max()
        assert all_points[:, 1].min() < center[1] < all_points[:, 1].max()
        assert detection.diagnostics["native_fitting_tolerance_mm"] == pytest.approx(
            0.10
        )


def test_native_contrast_excludes_candidate_clipped_by_hard_output_roi() -> None:
    result = detect_objects(
        _independent_shapes_with_hole(),
        _native_contrast_options(),
        WorkArea(0.0, 150.0, 0.0, 100.0),
        4.0,
        output_work_area=WorkArea(20.0, 140.0, 0.0, 100.0),
    )

    assert len(result.detections) == 1
    washer = result.detections[0]
    assert washer.diagnostics["within_work_area"] is True
    assert washer.selected_default is True
    assert (
        result.diagnostics["strategy_metrics"][
            "hard_roi_boundary_rejected_candidate_count"
        ]
        == 1
    )
