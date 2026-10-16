"""Synthetic first-stage editor acceptance without hardware or Qt."""
from __future__ import annotations

import copy
import json

import cv2
import numpy as np
import pytest

from laser_aligner.project.image_edit import (
    default_edit,
    derive_vector_outline,
    offset_outline,
    outline_from_mask,
    process_image,
    rasterize_outline,
    trace_foreground,
    trace_settings,
    validate_edit,
    validate_outline,
)
from laser_aligner.project.path_geometry import (
    NativePathGeometry,
    PathAffineTransform,
    PathCubicSegment,
    PathLineSegment,
    PathSubpath,
    native_path_bounds,
    transform_native_path,
)


def polygon(points, closed=True):
    return NativePathGeometry((PathSubpath(points[0], tuple(PathLineSegment(point) for point in points[1:]), closed),))


def rectangle(left=-0.4, bottom=-0.3, right=0.4, top=0.3):
    return polygon(((left, bottom), (right, bottom), (right, top), (left, top)))


def edit(gray):
    return default_edit("a" * 64, [gray.shape[1], gray.shape[0]])


def region(kind, points, operation="subtract", radius=0):
    return {"operation": operation, "kind": kind, "points": points, "radius": radius}


def synthetic_barrel():
    """Grayscale texture/lettering plus a separate gray cast shadow."""
    gray = np.full((160, 200), 252, np.uint8)
    for x in range(50, 150):
        gray[25:115, x] = 50 + (x * 17) % 140
    cv2.putText(gray, "E3", (64, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, 8, 2)
    cv2.ellipse(gray, (100, 137), (60, 10), 0, 0, 360, 160, -1)
    return gray


def test_shadow_is_removed_without_changing_retained_grayscale_or_source():
    gray = synthetic_barrel()
    original = gray.copy()
    settings = edit(gray)
    settings["regions"] = [region("rectangle", [[-0.5, -0.25], [0.5, -0.5]])]
    geometry = {"background_cleanup": {"enabled": True, "tolerance": 5}, "image_edit": settings}
    processed, allowed = process_image(gray, geometry)
    assert np.array_equal(gray, original)
    assert np.array_equal(processed[25:115, 50:150], original[25:115, 50:150])
    assert np.all(processed[125:] == 255)
    assert not allowed[137, 100]
    assert processed.shape == original.shape


@pytest.mark.parametrize("kind,points,radius", [
    ("rectangle", [[-0.2, 0.2], [0.2, -0.2]], 0),
    ("ellipse", [[-0.2, 0.2], [0.2, -0.2]], 0),
    ("polygon", [[-0.2, 0.2], [0.2, 0.2], [0, -0.2]], 0),
    ("brush", [[-0.1, 0], [0.1, 0]], 0.06),
])
def test_selection_and_restore_are_ordered_and_non_destructive(kind, points, radius):
    gray = np.full((100, 100), 37, np.uint8)
    settings = edit(gray)
    settings["regions"] = [region(kind, points, radius=radius)]
    erased, allowed = process_image(gray, {"image_edit": settings})
    assert not allowed[50, 50]
    assert erased[50, 50] == 255
    settings["regions"].append(region("brush", [[0, 0]], "restore", 0.03))
    restored, allowed = process_image(gray, {"image_edit": settings})
    assert allowed[50, 50]
    assert restored[50, 50] == 37


def test_restoring_cleanup_region_restores_original_eligibility():
    gray = np.full((40, 40), 252, np.uint8)
    settings = edit(gray)
    settings["regions"] = [region("brush", [[0, 0]], "restore", 0.1)]
    output, allowed = process_image(gray, {
        "image_edit": settings, "background_cleanup": {"enabled": True, "tolerance": 5},
    })
    assert output[20, 20] == 252 and allowed[20, 20]
    assert output[0, 0] == 255 and not allowed[0, 0]


def test_outline_inversion_keeps_exclusions_white_and_original_unchanged():
    gray = np.full((50, 90), 80, np.uint8)
    settings = edit(gray)
    settings.update(outline=rectangle().to_dict(), invert=True)
    output, allowed = process_image(gray, {"image_edit": settings})
    assert np.all(output[~allowed] == 255)
    assert np.all(output[allowed] == 175)
    assert np.all(gray == 80)


def test_cleanup_only_returns_hard_eligibility_for_later_inversion():
    gray = np.full((25, 25), 252, np.uint8)
    gray[8:17, 8:17] = 70
    output, allowed = process_image(gray, {"background_cleanup": {"enabled": True, "tolerance": 5}})
    assert np.all(output[~allowed] == 255)
    assert allowed[12, 12] and not allowed[0, 0]


def test_original_transparency_remains_ineligible_after_inversion_and_restore():
    gray = np.full((40, 40), 255, np.uint8)
    source_allowed = np.zeros(gray.shape, bool)
    source_allowed[10:30, 10:30] = True
    gray[source_allowed] = 70
    settings = edit(gray)
    settings["invert"] = True
    settings["regions"] = [region("rectangle", [[-.5, -.5], [.5, .5]], "restore")]
    output, allowed = process_image(gray, {"image_edit": settings}, source_allowed=source_allowed)
    assert np.array_equal(allowed, source_allowed)
    assert np.all(output[~source_allowed] == 255)
    assert np.all(output[source_allowed] == 185)
    for invalid in (np.ones((1, 1), bool), np.ones(gray.shape, np.uint8)):
        with pytest.raises(ValueError, match="Source eligibility"):
            process_image(gray, {"image_edit": settings}, source_allowed=invalid)


def test_accept_refine_undo_redo_json_roundtrip_preserves_editable_curve():
    gray = np.zeros((90, 120), np.uint8)
    before = edit(gray)
    before["outline"] = rectangle().to_dict()
    after = copy.deepcopy(before)
    path = NativePathGeometry((PathSubpath(
        (-0.4, -0.3), (
            PathCubicSegment((-0.1, -0.4), (0.2, -0.4), (0.4, -0.3)),
            PathLineSegment((0.4, 0.3)), PathLineSegment((-0.4, 0.3)),
        ), True,
    ),))
    after["outline"] = path.to_dict()
    after["regions"] = [region("brush", [[0.1, 0.1]], radius=0.01)]
    history = [before, after]
    assert history[0] == before
    restored = json.loads(json.dumps(history[1]))
    validate_edit(restored)
    assert restored == after
    assert NativePathGeometry.from_dict(restored["outline"]) == path
    assert validate_outline(path) == ()


def test_disconnected_components_holes_and_small_noise_are_retained_or_filtered():
    gray = np.full((140, 190), 255, np.uint8)
    cv2.rectangle(gray, (15, 20), (80, 110), 50, -1)
    cv2.rectangle(gray, (35, 45), (60, 85), 255, -1)
    cv2.rectangle(gray, (110, 35), (170, 100), 90, -1)
    gray[5, 5] = 0
    path = trace_foreground(gray, {"include_holes": True, "minimum_component_area": 8})
    assert len(path.subpaths) == 3
    allowed = rasterize_outline(path, gray.shape)
    assert allowed[30, 30] and allowed[60, 130]
    assert not allowed[60, 45] and not allowed[5, 5]
    silhouette = trace_foreground(gray, {"include_holes": False})
    assert len(silhouette.subpaths) == 2
    assert rasterize_outline(silhouette, gray.shape)[60, 45]


def test_threshold_preview_does_not_mutate_existing_manual_refinements():
    gray = synthetic_barrel()
    accepted = rectangle().to_dict()
    original = copy.deepcopy(accepted)
    candidate = trace_foreground(gray, {"tolerance": 10})
    assert len(candidate.subpaths) == 2  # Shadow is a distinct, reviewable component.
    assert accepted == original


def test_fine_detail_and_noisy_background_have_explicit_parameters():
    gray = np.full((90, 120), 250, np.uint8)
    gray[20:75, 25:90] = 80
    gray[15:25, 55:60] = 80
    gray[1:3, 1:3] = 100
    path = trace_foreground(gray, {"tolerance": 10, "minimum_component_area": 8, "detail": 0.15})
    assert len(path.subpaths) == 1
    assert rasterize_outline(path, gray.shape)[18, 57]


@pytest.mark.parametrize("path,expected", [
    (polygon(((-.4, -.3), (.4, -.3), (.4, .3)), closed=False), "open"),
    (polygon(((-.4, -.3), (.4, .3), (-.4, .3), (.4, -.3))), "self-intersect"),
    (polygon(((-.4, -.3), (.4, -.3), (-.4, -.3), (-.4, .3))), "duplicate"),
    (polygon(((-.4, -.3), (-.4, -.3), (.4, -.3), (.4, .3))), "zero-length"),
])
def test_invalid_outlines_flagged_without_silent_repair(path, expected):
    original = path.to_dict()
    assert expected in "; ".join(validate_outline(path))
    with pytest.raises(ValueError):
        rasterize_outline(path, (100, 100))
    assert path.to_dict() == original


def test_overlapping_or_duplicate_contours_rejected():
    path = NativePathGeometry(rectangle().subpaths + rectangle().subpaths)
    assert "duplicate" in "; ".join(validate_outline(path))


def test_physical_offset_alignment_with_anisotropic_dimensions_and_transform():
    path = rectangle()
    result = offset_outline(path, 100, 50, 2)
    assert native_path_bounds(result) == pytest.approx((-.42, -.34, .42, .34))
    world = transform_native_path(result, PathAffineTransform.from_components(
        scale_x=100, scale_y=50, rotation_deg=90, translate_x=120, translate_y=70,
    ))
    assert native_path_bounds(world) == pytest.approx((103, 28, 137, 112))
    assert offset_outline(path, 100, 50, 0) == path
    inside = offset_outline(path, 100, 50, -2)
    assert native_path_bounds(inside) == pytest.approx((-.38, -.26, .38, .26))


def test_offsets_preserve_holes_and_reject_collapses_or_merged_components():
    outer = rectangle()
    hole = rectangle(-.1, -.1, .1, .1)
    compound = NativePathGeometry(outer.subpaths + hole.subpaths)
    expanded = offset_outline(compound, 100, 100, 2)
    assert len(expanded.subpaths) == 2
    assert native_path_bounds(NativePathGeometry((expanded.subpaths[1],))) == pytest.approx((-.08, -.08, .08, .08))
    assert len(offset_outline(compound, 100, 100, 0, False).subpaths) == 1
    with pytest.raises(ValueError, match="collapses"):
        offset_outline(compound, 100, 100, 11)
    with pytest.raises(ValueError, match="collapses"):
        offset_outline(outer, 100, 100, -35)
    disconnected = NativePathGeometry(rectangle(-.4, -.2, -.1, .2).subpaths + rectangle(.1, -.2, .4, .2).subpaths)
    with pytest.raises(ValueError, match="Invalid offset"):
        offset_outline(disconnected, 100, 100, 11)


@pytest.mark.parametrize("offset", [.5, -.5])
def test_smooth_traced_barrel_accepts_half_mm_inside_and_outside_offsets(offset):
    gray = np.full((600, 800), 250, np.uint8)
    barrel = np.zeros(gray.shape, np.uint8)
    cv2.ellipse(barrel, (400, 270), (225, 205), 0, 0, 360, 255, -1)
    rows, columns = np.indices(gray.shape)
    grain = np.clip(110 + 45 * np.sin(columns / 18) + 15 * np.sin((columns + rows / 4) / 5), 20, 220)
    gray[barrel > 0] = grain[barrel > 0].astype(np.uint8)
    path = trace_foreground(gray, {"minimum_component_area": 50, "detail": 1.5})
    assert any(isinstance(segment, PathCubicSegment) for segment in path.subpaths[0].segments)
    result = offset_outline(path, 100, 75, offset)
    assert validate_outline(result, width_mm=100, height_mm=75) == ()
    before = native_path_bounds(transform_native_path(path, PathAffineTransform(m11=100, m22=75)))
    after = native_path_bounds(transform_native_path(result, PathAffineTransform(m11=100, m22=75)))
    assert after == pytest.approx((before[0] - offset, before[1] - offset,
                                   before[2] + offset, before[3] + offset), abs=.025)


def test_offset_rejects_narrow_notch_instead_of_trimming_its_loops():
    # An outward offset larger than this 0.2mm notch consumes its edges.
    # Preserving the deliberate rejection is preferable to deleting that detail.
    path = polygon(((-.4, -.3), (.4, -.3), (.4, .3), (.001, .3),
                    (.001, .25), (-.001, .25), (-.001, .3), (-.4, .3)))
    original = path.to_dict()
    with pytest.raises(ValueError, match="local detail.*Reduce the offset"):
        offset_outline(path, 100, 75, .5)
    assert path.to_dict() == original


def test_region_derived_vector_is_explicit_snapshot_and_does_not_rewrite_outline():
    gray = np.full((120, 120), 60, np.uint8)
    settings = edit(gray)
    settings["outline"] = rectangle().to_dict()
    assert derive_vector_outline(gray, {"image_edit": settings}) == rectangle()
    settings["regions"] = [region("rectangle", [[-.5, -.1], [.5, -.5]])]
    before = copy.deepcopy(settings)
    with pytest.raises(ValueError, match="Preview the edited mask outline"):
        derive_vector_outline(gray, {"image_edit": settings})
    assert settings == before
    _output, allowed = process_image(gray, {"image_edit": settings})
    candidate = outline_from_mask(allowed)
    assert native_path_bounds(candidate)[1] > -.15
    settings["outline"] = candidate.to_dict()  # Explicit reviewed replacement.
    settings["regions"] = []
    settings["mask_enabled"] = False
    assert derive_vector_outline(gray, {"image_edit": settings}) == candidate


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("source_sha256", "bad"),
    ("source_size", [0, 10]), ("source_size", [9000, 10]), ("source_size", [3000, 3000]),
    ("mask_enabled", 1), ("invert", "no"),
    ("regions", [{"operation": "unknown"}]), ("trace", {"unexpected": 2}),
])
def test_bad_persistence_settings_rejected(field, value):
    settings = default_edit("a" * 64, [10, 10])
    settings[field] = value
    with pytest.raises(ValueError):
        validate_edit(settings)


@pytest.mark.parametrize("settings", [
    {"tolerance": -1}, {"tolerance": 256}, {"tolerance": True},
    {"detail": 0}, {"smoothing": float("nan")}, {"include_holes": 1},
])
def test_trace_bad_settings_rejected(settings):
    with pytest.raises(ValueError):
        trace_settings(settings)


def test_source_dimensions_and_empty_mask_rejected():
    with pytest.raises(ValueError, match="dimensions"):
        process_image(np.zeros((10, 10), np.uint8), {"image_edit": default_edit("a" * 64, [11, 10])})
    with pytest.raises(ValueError, match="No closed"):
        outline_from_mask(np.zeros((20, 20), bool))


def test_committed_edit_processing_reuses_decoded_pixels_and_never_plans_jobs(monkeypatch):
    import laser_aligner.project.raster_asset as raster_asset

    def forbidden(*args, **kwargs):
        raise AssertionError("Editor attempted to decode its already decoded source")

    # cv2.imdecode is the shared decoder boundary; no preview render may call it.
    monkeypatch.setattr(cv2, "imdecode", forbidden)
    monkeypatch.setattr(raster_asset, "decode_raster_payload", forbidden, raising=False)
    gray = np.full((800, 1000), 92, np.uint8)
    settings = edit(gray)
    settings["regions"] = [region("brush", [[-.2, .1], [.2, .1]], radius=.02)]
    for _ in range(10):
        output, allowed = process_image(gray, {"image_edit": settings})
    assert output.shape == gray.shape and not allowed[320, 500]


def test_noisy_curved_trace_preserves_fine_detail_with_bounded_edge_error():
    mask = np.zeros((240, 320), np.uint8)
    angles = np.linspace(0, 2 * np.pi, 500, endpoint=False)
    radii = 65 + 12 * np.cos(6 * angles)
    points = np.column_stack((160 + radii * np.cos(angles), 120 + radii * np.sin(angles)))
    cv2.fillPoly(mask, [np.rint(points).astype(np.int32)], 255)
    mask[115:120, 232:241] = 255  # A fine, connected protrusion.
    gray = np.where(mask, 80, 252).astype(np.uint8)
    gray[3:5, 3:5] = 70
    gray[20:22, 280:282] = 90
    path = trace_foreground(gray, {"tolerance": 10, "minimum_component_area": 8, "detail": .2})
    assert len(path.subpaths) == 1
    assert any(isinstance(segment, PathCubicSegment) for segment in path.subpaths[0].segments)
    actual = rasterize_outline(path, gray.shape)
    assert actual[117, 237]
    expected = mask != 0
    distances_inside = cv2.distanceTransform(mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    distances_outside = cv2.distanceTransform(255 - mask, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    differing = actual != expected
    assert np.max(np.maximum(distances_inside, distances_outside)[differing], initial=0) <= 1.5
    settings = edit(gray)
    settings["outline"] = path.to_dict()
    processed, allowed = process_image(gray, {"image_edit": settings})
    assert np.array_equal(processed[allowed], gray[allowed])
