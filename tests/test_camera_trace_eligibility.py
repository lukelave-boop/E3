from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import pytest

from laser_aligner.config import WorkArea
from laser_aligner.project.raster_vectorize import (
    PixelVectorizationMaskPreview,
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationOptions,
    prepare_pixel_vectorization_source,
    vectorize_pixel_source,
)
from laser_aligner.vision.camera_raster_normalization import (
    normalize_camera_trace_frame,
)
from laser_aligner.vision.camera_trace_eligibility import (
    prepare_camera_trace_eligibility,
)
from laser_aligner.vision.object_trace import TraceOptions, detect_objects

_PPM = 2.0
_AREA = WorkArea(0.0, 320.0, 0.0, 240.0)
_ROI = (((20.0, 20.0), (300.0, 20.0), (300.0, 220.0), (20.0, 220.0)),)


@dataclass(frozen=True)
class _Scene:
    reference: np.ndarray
    current: np.ndarray
    roi_mask: np.ndarray
    sheet_mask: np.ndarray
    artwork_mask: np.ndarray


def _reflective_honeycomb_scene(
    *,
    stock_fraction: float = 0.50,
    artwork: str | None = None,
    outside_variant: int = 0,
    brightness: float = 1.07,
    blur_sigma: float = 0.0,
    noise_sigma: float = 1.2,
) -> _Scene:
    height, width = 480, 640
    reference = np.full((height, width, 3), (34, 39, 43), dtype=np.uint8)
    roi = np.zeros((height, width), dtype=np.uint8)
    roi[40:441, 40:601] = 255
    reference[roi > 0] = (72, 83, 96)
    rib = (170, 185, 204)
    for row, y in enumerate(range(48, 441, 24)):
        offset = 0 if row % 2 == 0 else 14
        for x in range(46 - offset, 616, 28):
            points = np.asarray(
                (
                    (x, y),
                    (x + 7, y - 10),
                    (x + 21, y - 10),
                    (x + 28, y),
                    (x + 21, y + 10),
                    (x + 7, y + 10),
                ),
                dtype=np.int32,
            )
            cv2.polylines(reference, [points], True, rib, 2, cv2.LINE_AA)
    highlight = np.zeros((height, width), dtype=np.float32)
    yy, xx = np.indices((height, width), dtype=np.float32)
    highlight = 18.0 * np.exp(-((xx - 505.0) ** 2 + (yy - 105.0) ** 2) / 4200.0)
    reference = np.clip(
        reference.astype(np.float32) + highlight[:, :, None], 0, 255
    ).astype(np.uint8)

    current = reference.astype(np.float32) * brightness
    current += np.asarray((7.0, 1.0, -3.0), dtype=np.float32)
    current += ((xx / width) * 10.0 - (yy / height) * 4.0)[:, :, None]
    sheet = np.zeros((height, width), dtype=np.uint8)
    sheet_width = int(round(520 * stock_fraction))
    left = 150
    right = min(585, left + max(90, sheet_width))
    if stock_fraction > 0.0:
        sheet[105:405, left:right] = 255
    sheet_level = np.asarray((194.0, 199.0, 214.0), dtype=np.float32)
    current[sheet > 0] = sheet_level + (
        (xx[sheet > 0] / width) * 9.0 - (yy[sheet > 0] / height) * 5.0
    )[:, None]

    artwork_mask = np.zeros((height, width), dtype=np.uint8)
    if artwork is not None:
        color = (42.0, 47.0, 55.0) if artwork == "dark" else (230.0, 237.0, 245.0)
        if artwork == "light":
            current[sheet > 0] = (58.0, 66.0, 77.0)
        cv2.putText(
            artwork_mask,
            "CO",
            (left + 18, 235),
            cv2.FONT_HERSHEY_SIMPLEX,
            2.4,
            255,
            12,
            cv2.LINE_AA,
        )
        cv2.rectangle(
            artwork_mask,
            (left + 20, 275),
            (min(right - 15, left + 210), 287),
            255,
            -1,
        )
        # Preserve a narrow stencil gap through the underline.
        artwork_mask[270:293, left + 105 : left + 111] = 0
        current[artwork_mask > 0] = color

    if outside_variant:
        current[:35] = (255, 255, 255)
        current[:, :30] = (0, 0, 0)
        cv2.circle(current, (625, 440), 28, (255, 30, 240), -1)
    if blur_sigma > 0.0:
        current = cv2.GaussianBlur(current, (0, 0), blur_sigma)
    rng = np.random.default_rng(20260829)
    current += rng.normal(0.0, noise_sigma, current.shape).astype(np.float32)
    return _Scene(
        reference=reference,
        current=np.clip(np.rint(current), 0, 255).astype(np.uint8),
        roi_mask=roi,
        sheet_mask=sheet,
        artwork_mask=artwork_mask,
    )


def _eligibility(scene: _Scene):
    return prepare_camera_trace_eligibility(
        scene.current,
        _AREA,
        _PPM,
        roi_polygons_mm=_ROI,
        roi_source="synthetic support intersected with guarded output",
        reference_bgr=scene.reference,
        reference_identity="synthetic-reference-v1",
    )


def test_hard_roi_uses_rectified_pixel_centers_and_is_immutable() -> None:
    scene = _reflective_honeycomb_scene()
    result = _eligibility(scene)

    assert result.hard_roi_mask[40, 40] == 255
    assert result.hard_roi_mask[440, 600] == 255
    assert result.hard_roi_mask[39, 40] == 0
    assert result.hard_roi_mask[40, 39] == 0
    assert not result.hard_roi_mask.flags.writeable
    assert not result.material_eligible_mask.flags.writeable


@pytest.mark.parametrize("stock_fraction", (0.20, 0.50, 0.84))
@pytest.mark.parametrize(
    ("brightness", "blur_sigma", "noise_sigma"),
    ((0.91, 0.0, 1.0), (1.12, 0.7, 1.8)),
)
def test_reference_suppresses_reflective_bed_across_stock_coverage_and_drift(
    stock_fraction: float,
    brightness: float,
    blur_sigma: float,
    noise_sigma: float,
) -> None:
    scene = _reflective_honeycomb_scene(
        stock_fraction=stock_fraction,
        brightness=brightness,
        blur_sigma=blur_sigma,
        noise_sigma=noise_sigma,
    )
    result = _eligibility(scene)
    bed = (scene.roi_mask > 0) & (scene.sheet_mask == 0)
    sheet = scene.sheet_mask > 0

    assert np.count_nonzero(result.exposed_bed_mask[bed]) / np.count_nonzero(bed) > 0.72
    assert np.count_nonzero(result.material_eligible_mask[sheet]) / np.count_nonzero(sheet) > 0.88


def test_blank_sheet_is_eligible_but_not_automatically_foreground() -> None:
    scene = _reflective_honeycomb_scene(stock_fraction=0.50)
    eligibility = _eligibility(scene)
    normalized = normalize_camera_trace_frame(
        scene.current,
        _PPM,
        eligibility_mask=eligibility.material_eligible_mask,
    )
    sheet_values = normalized.dark_raster[scene.sheet_mask > 0]

    assert float(np.percentile(sheet_values, 1.0)) > 185.0
    assert np.count_nonzero(sheet_values < 160) / sheet_values.size < 0.002

    traced = detect_objects(
        scene.current,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="auto",
            regular_grid=False,
            output_mode="native",
            min_area_mm2=1.0,
            max_area_mm2=8_000.0,
            min_width_mm=0.5,
            min_height_mm=0.5,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.20,
        ),
        _AREA,
        _PPM,
        output_work_area=_AREA,
        background_image=scene.reference,
        trace_roi_polygons_mm=_ROI,
        reference_required=True,
    )
    assert traced.detected is False
    assert traced.direct_count == 0


def test_empty_bed_is_suppressed_and_auto_fails_closed() -> None:
    scene = _reflective_honeycomb_scene(stock_fraction=0.0)
    eligibility = _eligibility(scene)

    assert eligibility.diagnostics.exposed_bed_fraction_of_roi > 0.92
    with pytest.raises(ValueError, match="No credible trace|no eligible material"):
        detect_objects(
            scene.current,
            TraceOptions(
                detection_mode="auto",
                regular_grid=False,
                output_mode="native",
                min_area_mm2=1.0,
                max_area_mm2=8_000.0,
                min_width_mm=0.5,
                min_height_mm=0.5,
                confidence_threshold=0.0,
                native_fitting_tolerance_mm=0.20,
            ),
            _AREA,
            _PPM,
            output_work_area=_AREA,
            background_image=scene.reference,
            trace_roi_polygons_mm=_ROI,
            reference_required=True,
        )


@pytest.mark.parametrize("polarity", ("dark", "light"))
def test_sheet_artwork_reaches_exact_eligibility_gated_production_mask(
    polarity: str,
) -> None:
    scene = _reflective_honeycomb_scene(stock_fraction=0.70, artwork=polarity)
    previews = []
    result = detect_objects(
        scene.current,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="auto",
            contrast_invert=polarity == "light",
            regular_grid=False,
            output_mode="native",
            min_area_mm2=1.0,
            max_area_mm2=8_000.0,
            min_width_mm=0.5,
            min_height_mm=0.5,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.20,
        ),
        _AREA,
        _PPM,
        output_work_area=_AREA,
        background_image=scene.reference,
        trace_roi_polygons_mm=_ROI,
        trace_roi_source="synthetic ROI",
        reference_required=True,
        reference_identity="synthetic-reference-v1",
        raster_preview_callback=previews.append,
    )
    preview = previews[-1]
    mask = preview.foreground_mask > 0
    artwork = scene.artwork_mask > 0
    bed = (scene.roi_mask > 0) & (scene.sheet_mask == 0)

    assert result.detected
    assert np.count_nonzero(mask & artwork) / np.count_nonzero(artwork) > 0.72
    assert np.count_nonzero(mask & bed) / np.count_nonzero(bed) < 0.005
    assert np.count_nonzero(mask & (scene.roi_mask == 0)) == 0
    assert preview.contour_mask.shape == (1920, 2560)
    assert np.count_nonzero(preview.contour_mask[:160]) == 0
    timing = result.diagnostics["timing"]
    assert {
        "hard_roi_preparation",
        "reference_comparison",
        "material_eligibility",
        "trace_eligibility_total",
    } <= timing["trace_eligibility"].keys()
    assert {
        "eligibility_fill",
        "background_estimation",
        "normalization",
        "camera_normalization_total",
    } <= timing["camera_normalization"].keys()
    assert {
        "threshold",
        "component_cleanup",
        "raster_4x_preparation",
        "contour_extraction",
        "native_fitting",
        "topology_validation",
        "raster_hierarchy_validation",
    } <= timing["raster_vectorization"].keys()
    assert timing["trace_detection_total_seconds"] >= 0.0


def test_outside_machine_features_cannot_change_eligible_otsu_or_mask() -> None:
    first = _reflective_honeycomb_scene(artwork="dark", outside_variant=0)
    second = _reflective_honeycomb_scene(artwork="dark", outside_variant=1)
    previews: list[list[object]] = [[], []]
    results = []
    for scene, captured in zip((first, second), previews, strict=True):
        results.append(
            detect_objects(
                scene.current,
                TraceOptions(
                    detection_mode="contrast",
                    regular_grid=False,
                    output_mode="native",
                    min_area_mm2=1.0,
                    max_area_mm2=8_000.0,
                    min_width_mm=0.5,
                    min_height_mm=0.5,
                    confidence_threshold=0.0,
                    native_fitting_tolerance_mm=0.20,
                ),
                _AREA,
                _PPM,
                output_work_area=_AREA,
                background_image=scene.reference,
                trace_roi_polygons_mm=_ROI,
                raster_preview_callback=captured.append,
            )
        )

    assert previews[0][-1].threshold_used == previews[1][-1].threshold_used
    assert np.array_equal(
        previews[0][-1].foreground_mask,
        previews[1][-1].foreground_mask,
    )
    assert [item.area_mm2 for item in results[0].detections] == pytest.approx(
        [item.area_mm2 for item in results[1].detections]
    )


def _auto_options() -> TraceOptions:
    return TraceOptions(
        detection_mode="auto",
        regular_grid=False,
        output_mode="native",
        min_area_mm2=10.0,
        max_area_mm2=8_000.0,
        min_width_mm=0.5,
        min_height_mm=0.5,
        confidence_threshold=0.0,
        native_fitting_tolerance_mm=0.20,
    )


def test_warm_background_color_cannot_override_credible_dark_raster() -> None:
    scene = _reflective_honeycomb_scene(stock_fraction=0.70, artwork="dark")
    result = detect_objects(
        scene.current,
        _auto_options(),
        _AREA,
        _PPM,
        output_work_area=_AREA,
        background_image=scene.reference,
        trace_roi_polygons_mm=_ROI,
        reference_required=True,
    )

    auto = result.diagnostics["auto"]
    color = next(item for item in auto["attempts"] if item["name"] == "color")
    assert auto["selected_strategy"] == "raster_dark"
    assert auto["background_estimate_count"] == 1
    assert color["status"] in {"skipped", "rejected"}
    assert color["foreground_fraction"] is None or color["foreground_fraction"] <= 0.35
    timing = result.diagnostics["timing"]
    assert "threshold" in timing["auto_raster_attempts"]["raster_dark"]
    assert "threshold" in timing["auto_raster_attempts"]["raster_light"]
    assert timing["trace_detection_total_seconds"] >= 0.0


def test_real_bounded_color_can_win_when_luminance_evidence_is_weak() -> None:
    scene = _reflective_honeycomb_scene(stock_fraction=0.70)
    current = scene.current.copy()
    current[scene.sheet_mask > 0] = (132, 132, 132)
    color_mask = np.zeros(scene.sheet_mask.shape, dtype=np.uint8)
    cv2.rectangle(color_mask, (245, 185), (340, 265), 255, -1)
    # OpenCV luminance is approximately 132 for this saturated purple, matching
    # the neutral stock while retaining strong chroma evidence.
    current[color_mask > 0] = (220, 100, 161)
    colored = _Scene(
        reference=scene.reference,
        current=current,
        roi_mask=scene.roi_mask,
        sheet_mask=scene.sheet_mask,
        artwork_mask=color_mask,
    )
    result = detect_objects(
        colored.current,
        _auto_options(),
        _AREA,
        _PPM,
        output_work_area=_AREA,
        background_image=colored.reference,
        trace_roi_polygons_mm=_ROI,
        reference_required=True,
    )

    assert result.diagnostics["auto"]["selected_strategy"] == "color"


def test_explicit_by_color_remains_available_but_obeys_hard_roi() -> None:
    image = np.full((200, 200, 3), 132, dtype=np.uint8)
    target = (220, 100, 161)
    cv2.rectangle(image, (80, 80), (120, 120), target, -1)
    cv2.rectangle(image, (2, 80), (35, 120), target, -1)
    roi = (((20.0, 20.0), (80.0, 20.0), (80.0, 80.0), (20.0, 80.0)),)
    previews = []
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="color",
            target_bgr=target,
            target_hue=150.0,
            hue_tolerance=16.0,
            regular_grid=False,
            output_mode="native",
            min_area_mm2=5.0,
            max_area_mm2=2_000.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.10,
        ),
        WorkArea(0.0, 100.0, 0.0, 100.0),
        _PPM,
        trace_roi_polygons_mm=roi,
        trace_output_polygon_mm=roi[0],
        raster_preview_callback=previews.append,
    )

    assert result.direct_count == 1
    assert np.count_nonzero(previews[-1].foreground_mask[:, :40]) == 0


def test_shared_vectorizer_otsu_and_4x_mask_ignore_ineligible_pixels() -> None:
    gray = np.full((40, 60), 235, dtype=np.uint8)
    gray[12:29, 24:39] = 38
    eligibility = np.zeros(gray.shape, dtype=np.uint8)
    eligibility[8:33, 18:45] = 255
    rgba = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGBA)
    source = prepare_pixel_vectorization_source(
        rgba,
        eligibility_mask=eligibility,
    )
    previews: list[PixelVectorizationMaskPreview] = []
    vectorize_pixel_source(
        source,
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.AUTO_THRESHOLD,
            minimum_feature_area_mm2=0.0,
            simplification_tolerance_mm=0.10,
            contour_output=RasterContourOutput.ALL_CONTOURS,
        ),
        displayed_width_mm=30.0,
        displayed_height_mm=20.0,
        mask_ready=previews.append,
    )
    preview = previews[-1]
    gate_4x = cv2.resize(
        eligibility,
        (240, 160),
        interpolation=cv2.INTER_NEAREST,
    )

    assert np.count_nonzero(preview.foreground_mask[eligibility == 0]) == 0
    assert np.count_nonzero(preview.contour_mask[gate_4x == 0]) == 0


def test_full_frame_eligibility_preserves_physical_mapping_near_every_roi_edge() -> None:
    area = WorkArea(0.0, 100.0, 0.0, 100.0)
    roi = (((10.0, 10.0), (90.0, 10.0), (90.0, 90.0), (10.0, 90.0)),)
    image = np.full((200, 200, 3), 220, dtype=np.uint8)

    def rectangle(x_min: float, y_min: float, x_max: float, y_max: float) -> None:
        cv2.rectangle(
            image,
            (round(x_min * _PPM), round((100.0 - y_max) * _PPM)),
            (round(x_max * _PPM), round((100.0 - y_min) * _PPM)),
            (35, 35, 35),
            -1,
        )

    rectangle(12.0, 45.0, 20.0, 55.0)
    rectangle(80.0, 45.0, 88.0, 55.0)
    rectangle(45.0, 12.0, 55.0, 20.0)
    rectangle(45.0, 80.0, 55.0, 88.0)
    result = detect_objects(
        image,
        TraceOptions(
            detection_mode="contrast",
            contrast_threshold_mode="manual",
            contrast_threshold=180,
            regular_grid=False,
            output_mode="native",
            min_area_mm2=5.0,
            max_area_mm2=500.0,
            min_width_mm=2.0,
            min_height_mm=2.0,
            confidence_threshold=0.0,
            native_fitting_tolerance_mm=0.10,
        ),
        area,
        _PPM,
        output_work_area=area,
        trace_roi_polygons_mm=roi,
        trace_output_polygon_mm=roi[0],
    )

    centers = sorted(detection.center_mm for detection in result.detections)
    expected = sorted(((16.0, 50.0), (84.0, 50.0), (50.0, 16.0), (50.0, 84.0)))
    assert len(centers) == 4
    assert np.asarray(centers) == pytest.approx(np.asarray(expected), abs=0.35)


def test_reference_shape_mismatch_is_rejected_and_auto_cannot_claim_fallback() -> None:
    scene = _reflective_honeycomb_scene()
    with pytest.raises(ValueError, match="dimensions do not match"):
        prepare_camera_trace_eligibility(
            scene.current,
            _AREA,
            _PPM,
            roi_polygons_mm=_ROI,
            roi_source="synthetic ROI",
            reference_bgr=scene.reference[:-1],
        )
    with pytest.raises(ValueError, match="Auto requires"):
        detect_objects(
            scene.current,
            TraceOptions(detection_mode="auto", regular_grid=False),
            _AREA,
            _PPM,
            trace_roi_polygons_mm=_ROI,
            reference_required=True,
        )
