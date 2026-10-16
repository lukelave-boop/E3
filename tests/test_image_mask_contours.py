from __future__ import annotations

import cv2
import numpy as np
import pytest

from laser_aligner.project import image_mask_contours as module
from laser_aligner.project.image_edit import outline_from_mask, validate_outline
from laser_aligner.project.image_mask_contours import mask_contours
from laser_aligner.project.raster_vectorize import RasterVectorizationCancelledError


def assert_samples_match(mask, contours):
    for y, x in np.ndindex(mask.shape):
        inside = sum(cv2.pointPolygonTest(c, (float(x), float(y)), False) > 0 for c in contours) % 2
        assert inside == bool(mask[y, x]), (x, y)


@pytest.mark.parametrize("bits", range(1, 16))
def test_every_marching_case_is_closed_and_preserves_pixel_classification(bits):
    mask = np.array([[bits & 1, bits & 2], [bits & 8, bits & 4]], dtype=np.uint8)
    contours, parents = mask_contours(mask, include_holes=True)
    assert_samples_match(mask, contours)
    assert all(parent is None or parent < i for i, parent in enumerate(parents))
    for contour in contours:
        assert len(np.unique(contour, axis=0)) == len(contour)
    path = outline_from_mask(mask, {"minimum_component_area": 1, "detail": .05})
    assert validate_outline(path) == ()


def test_nested_holes_islands_and_frame_edges_keep_parent_order():
    mask = np.ones((27, 31), np.uint8)
    mask[3:24, 3:28] = 0
    mask[7:20, 7:24] = 1
    mask[10:17, 10:21] = 0
    contours, parents = mask_contours(mask, include_holes=True)
    assert parents == [None, 0, 1, 2]
    assert_samples_match(mask, contours)
    assert contours[0].min(axis=0) == pytest.approx((-.5, -.5))
    assert contours[0].max(axis=0) == pytest.approx((30.5, 26.5))
    external, parents = mask_contours(mask, include_holes=False)
    assert len(external) == 1 and parents == [None]


def test_seeded_noisy_masks_preserve_all_source_samples():
    rng = np.random.default_rng(314)
    for _ in range(12):
        mask = (rng.random((12, 17)) > .5).astype(np.uint8)
        contours, _ = mask_contours(mask, include_holes=True)
        assert_samples_match(mask, contours)


def test_one_pixel_whisker_no_longer_retraces_a_boundary():
    mask = np.zeros((80, 100), np.uint8)
    cv2.ellipse(mask, (45, 40), (30, 25), 0, 0, 360, 1, -1)
    mask[40, 70:92] = 1
    # The old centre walk visited the same points twice along this whisker.
    old, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    assert len(np.unique(old[0].reshape(-1, 2), axis=0)) < len(old[0])
    path = outline_from_mask(mask, {"detail": .1})
    assert validate_outline(path) == ()
    assert len(path.subpaths) == 1


def test_empty_complex_and_cancelled_masks_fail_explicitly(monkeypatch):
    with pytest.raises(ValueError, match="No closed"):
        mask_contours(np.zeros((5, 5), np.uint8), include_holes=True)
    with pytest.raises(RasterVectorizationCancelledError):
        mask_contours(np.ones((5, 5), np.uint8), include_holes=True, cancel_check=lambda: True)
    monkeypatch.setattr(module, "MAX_BOUNDARY_POINTS", 4)
    with pytest.raises(ValueError, match="contour-point limit"):
        mask_contours(np.ones((5, 5), np.uint8), include_holes=True)


def test_contour_limit_is_enforced(monkeypatch):
    mask = np.zeros((10, 10), np.uint8)
    mask[1, 1] = mask[7, 7] = 1
    monkeypatch.setattr(module, "MAX_CONTOURS", 1)
    with pytest.raises(ValueError, match="contour-count limit"):
        mask_contours(mask, include_holes=True)
