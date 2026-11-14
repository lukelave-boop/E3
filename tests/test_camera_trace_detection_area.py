from __future__ import annotations

import cv2
import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.vision.camera_trace_eligibility import prepare_camera_trace_eligibility
from laser_aligner.vision.object_trace import TraceOptions, detect_objects

AREA = WorkArea(0.0, 100.0, 0.0, 100.0)
TRUSTED = ((5.0, 5.0), (95.0, 5.0), (95.0, 95.0), (5.0, 95.0))
SELECTED = ((20.0, 20.0), (70.0, 20.0), (70.0, 80.0), (20.0, 80.0))


def _options():
    return TraceOptions(
        detection_mode="contrast", contrast_threshold_mode="manual", contrast_threshold=180,
        regular_grid=False, output_mode="native", min_area_mm2=5.0, max_area_mm2=900.0,
        min_width_mm=2.0, min_height_mm=2.0, confidence_threshold=0.0,
        native_fitting_tolerance_mm=0.10,
    )


def _detect(image, polygons):
    return detect_objects(
        image, _options(), AREA, 2.0, output_work_area=AREA,
        trace_roi_polygons_mm=polygons, trace_output_polygon_mm=TRUSTED,
    )


def test_selected_area_preserves_inside_coordinates_and_omits_cut_boundary_objects():
    image = np.full((200, 200, 3), 220, np.uint8)
    # Complete interior target, separate outside target, and a target cut by the left edge.
    cv2.rectangle(image, (70, 80), (100, 110), (35, 35, 35), -1)
    cv2.rectangle(image, (160, 80), (180, 110), (35, 35, 35), -1)
    cv2.rectangle(image, (30, 130), (50, 150), (35, 35, 35), -1)
    full = _detect(image, (TRUSTED,))
    selected = _detect(image, (TRUSTED, SELECTED))
    assert len(full.detections) == 3
    assert len(selected.detections) == 1
    matching = min(full.detections, key=lambda item: abs(item.center_mm[0] - 42.5))
    assert selected.detections[0].center_mm == pytest.approx(matching.center_mm, abs=0.01)
    assert selected.detections[0].width_mm == pytest.approx(matching.width_mm, abs=0.01)
    assert selected.detections[0].height_mm == pytest.approx(matching.height_mm, abs=0.01)
    assert (selected.image_width, selected.image_height) == (200, 200)


def test_selection_intersects_trusted_roi_and_keeps_empty_bed_suppression():
    yy, xx = np.indices((200, 200))
    gray = np.where((xx // 6 + yy // 6) % 2, 60, 160).astype(np.uint8)
    reference = np.repeat(gray[..., None], 3, axis=2)
    current = reference.copy()
    current[70:130, 70:110] = 235
    selection = ((-10.0, 20.0), (70.0, 20.0), (70.0, 80.0), (-10.0, 80.0))
    result = prepare_camera_trace_eligibility(
        current, AREA, 2.0, roi_polygons_mm=(TRUSTED, selection),
        roi_source="selected detection area", reference_bgr=reference,
        reference_identity="empty-bed",
    )
    assert result.hard_roi_mask.shape == (200, 200)
    assert result.hard_roi_mask[40, 10] == 255
    assert result.hard_roi_mask[40, 9] == 0  # Trusted ROI still wins.
    assert result.hard_roi_mask[39, 30] == 0  # Selection top edge still wins.
    assert np.count_nonzero(result.exposed_bed_mask) > 1000
    assert result.material_eligible_mask[100, 90] == 255
    assert np.count_nonzero(result.material_eligible_mask[result.hard_roi_mask == 0]) == 0
    assert result.diagnostics.reference_status == "validated_and_used"
    assert not result.hard_roi_mask.flags.writeable


def test_selection_with_no_overlap_never_falls_back_to_full_frame():
    image = np.full((200, 200, 3), 220, np.uint8)
    cv2.rectangle(image, (70, 80), (100, 110), (35, 35, 35), -1)
    outside = ((110.0, 20.0), (120.0, 20.0), (120.0, 80.0), (110.0, 80.0))
    with pytest.raises(ValueError, match="ROI contains no image pixels"):
        _detect(image, (TRUSTED, outside))
