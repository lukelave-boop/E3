import math

import numpy as np
import pytest

import laser_aligner.templates.synthetic as synthetic_renderer
from laser_aligner.config import WorkArea
from laser_aligner.templates import (
    RectangleGridSpec,
    generate_template_test_frame,
    template_from_rectangle_grid,
)
from laser_aligner.vision.object_trace import TraceOptions, detect_objects
from laser_aligner.vision.template_alignment import align_template


def _grid_template(*, detection_mode: str = "color"):
    options = TraceOptions(
        detection_mode=detection_mode,
        target_hue=2 if detection_mode == "color" else None,
        min_saturation=35,
        min_area_mm2=40.0,
        min_width_mm=5.0,
        min_height_mm=4.0,
        regular_grid=True,
        infer_missing=True,
    )
    return template_from_rectangle_grid(
        RectangleGridSpec(
            name=f"Synthetic {detection_mode} grid",
            rows=2,
            columns=3,
            width_mm=18.0,
            height_mm=9.0,
            corner_radius_mm=1.5,
            horizontal_gap_mm=8.0,
            vertical_gap_mm=7.0,
        ),
        trace_options=options.to_dict(),
    )


def _sample_closed_polyline(
    points: list[list[float]],
    maximum_step_mm: float = 0.05,
) -> np.ndarray:
    polygon = np.asarray(points, dtype=np.float64)
    samples: list[np.ndarray] = []
    for start, end in zip(polygon, np.roll(polygon, -1, axis=0), strict=True):
        distance = float(np.linalg.norm(end - start))
        count = max(1, int(math.ceil(distance / maximum_step_mm)))
        fractions = np.arange(count, dtype=np.float64)[:, None] / count
        samples.append(start + (end - start) * fractions)
    return np.vstack(samples)


def _rounded_rectangle_boundary_error(
    points: list[list[float]],
    *,
    center: tuple[float, float],
    width: float,
    height: float,
    rotation_deg: float,
    radius: float,
) -> float:
    samples = _sample_closed_polyline(points)
    angle = math.radians(rotation_deg)
    rotation = np.asarray(
        ((math.cos(angle), -math.sin(angle)), (math.sin(angle), math.cos(angle))),
        dtype=np.float64,
    )
    local = (samples - np.asarray(center, dtype=np.float64)) @ rotation
    fitted_radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    straight_half_extents = np.asarray(
        (width / 2.0 - fitted_radius, height / 2.0 - fitted_radius),
        dtype=np.float64,
    )
    delta = np.abs(local) - straight_half_extents
    signed_distance = (
        np.linalg.norm(np.maximum(delta, 0.0), axis=1)
        + np.minimum(np.maximum(delta[:, 0], delta[:, 1]), 0.0)
        - fitted_radius
    )
    return float(np.max(np.abs(signed_distance)))


def test_generated_frame_has_correct_size_pose_and_json_metadata():
    template = _grid_template()
    area = WorkArea(10.0, 110.0, -5.0, 75.0)
    frame = generate_template_test_frame(
        template,
        area,
        3.0,
        center_x_mm=54.0,
        center_y_mm=33.0,
        rotation_deg=30.0,
        noise_stddev=0.0,
    )

    assert frame.image.shape == (240, 300, 3)
    assert frame.image.dtype == np.uint8
    assert frame.ground_truth.image_size_px == (300, 240)
    assert frame.ground_truth.center_mm == pytest.approx((54.0, 33.0))
    assert frame.ground_truth.rotation_deg == pytest.approx(30.0)
    first_source = template.features[0]
    angle = math.radians(30.0)
    expected_center = (
        54.0
        + first_source.center_x_mm * math.cos(angle)
        - first_source.center_y_mm * math.sin(angle),
        33.0
        + first_source.center_x_mm * math.sin(angle)
        + first_source.center_y_mm * math.cos(angle),
    )
    first = frame.ground_truth.features[0]
    assert first.center_mm == pytest.approx(expected_center)
    assert first.rotation_deg == pytest.approx(30.0)
    assert first.rendered
    assert not first.clipped
    assert frame.metadata["features"][0]["center_mm"] == pytest.approx(expected_center)
    assert frame.metadata["work_area"] == {
        "x_min": 10.0,
        "x_max": 110.0,
        "y_min": -5.0,
        "y_max": 75.0,
    }


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("pixels_per_mm", "3.0"),
        ("center_x_mm", True),
        ("rotation_deg", "30"),
    ],
)
def test_synthetic_template_numeric_inputs_reject_coerced_types(
    argument: str,
    value: object,
) -> None:
    kwargs = {
        "pixels_per_mm": 3.0,
        "center_x_mm": 50.0,
        "rotation_deg": 0.0,
    }
    kwargs[argument] = value

    with pytest.raises(ValueError, match="finite number"):
        generate_template_test_frame(
            _grid_template(),
            WorkArea(0.0, 100.0, 0.0, 100.0),
            **kwargs,
        )


def test_seeded_noise_missing_and_occlusion_are_deterministic():
    template = _grid_template()
    area = WorkArea(0.0, 100.0, 0.0, 80.0)
    arguments = dict(
        center_x_mm=50.0,
        center_y_mm=40.0,
        rotation_deg=-8.0,
        seed=991,
        noise_stddev=2.0,
        missing_feature_indices=(1,),
        occluded_feature_indices=(4,),
    )

    first = generate_template_test_frame(template, area, 4.0, **arguments)
    second = generate_template_test_frame(template, area, 4.0, **arguments)
    different_seed = generate_template_test_frame(
        template,
        area,
        4.0,
        **{**arguments, "seed": 992},
    )

    assert np.array_equal(first.image, second.image)
    assert not np.array_equal(first.image, different_seed.image)
    assert first.ground_truth.missing_feature_indices == (1,)
    assert first.ground_truth.occluded_feature_indices == (4,)
    assert not first.ground_truth.features[1].rendered
    assert first.ground_truth.features[4].rendered
    assert first.ground_truth.features[4].occluded


def test_color_frame_is_detected_and_aligned_at_the_known_pose():
    template = _grid_template(detection_mode="color")
    area = WorkArea(0.0, 100.0, 0.0, 80.0)
    frame = generate_template_test_frame(
        template,
        area,
        5.0,
        center_x_mm=47.0,
        center_y_mm=38.0,
        rotation_deg=11.0,
        seed=7,
        noise_stddev=1.0,
    )

    result = detect_objects(frame.image, template.trace_options, area, 5.0)
    alignment = align_template(template, result.detections)

    assert result.direct_count == len(template.features)
    assert result.inferred_count == 0
    assert alignment.matched_count == len(template.features)
    assert alignment.translation_mm == pytest.approx((47.0, 38.0), abs=0.35)
    rotation_error = (alignment.rotation_deg - 11.0 + 90.0) % 180.0 - 90.0
    assert rotation_error == pytest.approx(0.0, abs=0.75)
    assert alignment.rms_error_mm is not None
    assert alignment.rms_error_mm < 0.35


def test_rounded_trace_vector_follows_the_fitted_geometry_without_faceting():
    options = TraceOptions(
        detection_mode="color",
        target_hue=2,
        min_saturation=35,
        min_area_mm2=40.0,
        min_width_mm=5.0,
        min_height_mm=4.0,
        regular_grid=False,
        infer_missing=False,
        output_mode="rounded",
        smoothing_mm=0.25,
    )
    template = template_from_rectangle_grid(
        RectangleGridSpec(
            name="One rounded label",
            rows=1,
            columns=1,
            width_mm=78.0,
            height_mm=21.0,
            corner_radius_mm=3.0,
        ),
        trace_options=options.to_dict(),
    )
    area = WorkArea(0.0, 220.0, 0.0, 220.0)
    frame = generate_template_test_frame(
        template,
        area,
        4.0,
        center_x_mm=110.0,
        center_y_mm=110.0,
        rotation_deg=13.0,
        seed=1729,
        noise_stddev=0.0,
    )

    result = detect_objects(frame.image, options, area, 4.0)

    assert result.direct_count == 1
    detection = result.detections[0]
    assert detection.shape == "rounded_rectangle"
    assert detection.center_mm == pytest.approx((110.0, 110.0), abs=0.3)
    assert detection.width_mm == pytest.approx(78.0, abs=0.75)
    assert detection.height_mm == pytest.approx(21.0, abs=0.75)
    assert detection.corner_radius_mm == pytest.approx(3.0, abs=0.75)
    assert _rounded_rectangle_boundary_error(
        detection.vector_contour_mm,
        center=detection.center_mm,
        width=detection.width_mm,
        height=detection.height_mm,
        rotation_deg=detection.rotation_deg,
        radius=detection.corner_radius_mm,
    ) < 0.08


def test_axis_aligned_generated_grid_has_no_antialias_detection_expansion():
    options = TraceOptions(
        detection_mode="color",
        target_hue=2,
        min_saturation=35,
        min_area_mm2=40.0,
        min_width_mm=5.0,
        min_height_mm=4.0,
        regular_grid=False,
        infer_missing=False,
        output_mode="rounded",
    )
    template = template_from_rectangle_grid(
        RectangleGridSpec(
            name="Axis-aligned 8 x 2 rounded labels",
            rows=8,
            columns=2,
            width_mm=78.0,
            height_mm=21.0,
            corner_radius_mm=3.0,
            horizontal_gap_mm=9.6,
            vertical_gap_mm=3.0,
        ),
        trace_options=options.to_dict(),
    )
    area = WorkArea(0.0, 220.0, 0.0, 220.0)
    frame = generate_template_test_frame(
        template,
        area,
        4.0,
        center_x_mm=110.0,
        center_y_mm=110.0,
        rotation_deg=0.0,
        noise_stddev=0.0,
    )

    result = detect_objects(frame.image, options, area, 4.0)
    alignment = align_template(template, result.detections)

    assert result.direct_count == 16
    assert all(item.width_mm == pytest.approx(78.0, abs=0.01) for item in result.detections)
    assert all(item.height_mm == pytest.approx(21.0, abs=0.01) for item in result.detections)
    assert all(
        item.corner_radius_mm == pytest.approx(3.0, abs=0.01)
        for item in result.detections
    )
    assert alignment.translation_mm == pytest.approx((110.0, 110.0), abs=0.01)
    assert alignment.dimension_scale_ratio == pytest.approx(1.0, abs=0.001)
    assert alignment.rms_error_mm == pytest.approx(0.05, abs=0.01)


def test_contrast_frame_exercises_the_templates_contrast_trace_options():
    template = _grid_template(detection_mode="contrast")
    area = WorkArea(0.0, 100.0, 0.0, 80.0)
    frame = generate_template_test_frame(
        template,
        area,
        5.0,
        center_x_mm=50.0,
        center_y_mm=40.0,
        rotation_deg=-6.0,
        seed=11,
        noise_stddev=0.75,
    )

    result = detect_objects(frame.image, template.trace_options, area, 5.0)

    assert frame.ground_truth.target_hue is None
    assert result.mode_used == "contrast"
    assert result.direct_count == len(template.features)


def test_maximum_grid_rasterizes_label_rois_instead_of_full_bed_masks(monkeypatch):
    template = template_from_rectangle_grid(
        RectangleGridSpec(
            name="Maximum synthetic grid",
            rows=20,
            columns=25,
            width_mm=2.0,
            height_mm=2.0,
            corner_radius_mm=0.4,
            horizontal_gap_mm=1.0,
            vertical_gap_mm=1.0,
        ),
        trace_options=TraceOptions(
            detection_mode="color",
            min_area_mm2=1.0,
            min_width_mm=1.0,
            min_height_mm=1.0,
        ).to_dict(),
    )
    assert len(template.features) == 500
    area = WorkArea(0.0, 100.0, 0.0, 80.0)
    blended_shapes: list[tuple[int, int]] = []
    original_fill = synthetic_renderer._fill_mask

    def record_fill(image, mask, color):
        blended_shapes.append(image.shape[:2])
        return original_fill(image, mask, color)

    monkeypatch.setattr(synthetic_renderer, "_fill_mask", record_fill)

    frame = generate_template_test_frame(
        template,
        area,
        4.0,
        center_x_mm=50.0,
        center_y_mm=40.0,
        rotation_deg=7.0,
        noise_stddev=0.0,
    )

    full_frame_pixels = frame.image.shape[0] * frame.image.shape[1]
    blended_pixels = sum(height * width for height, width in blended_shapes)
    assert frame.image.shape == (320, 400, 3)
    assert len(blended_shapes) == 2 * len(template.features)
    assert max(height * width for height, width in blended_shapes) < full_frame_pixels // 50
    assert blended_pixels < full_frame_pixels * 3


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"pixels_per_mm": 0.0}, "pixels_per_mm must be positive"),
        ({"noise_stddev": -0.1}, "noise_stddev must not be negative"),
        ({"missing_feature_indices": (999,)}, "out-of-range"),
        (
            {
                "missing_feature_indices": (1,),
                "occluded_feature_indices": (1,),
            },
            "both missing and occluded",
        ),
    ],
)
def test_invalid_generation_parameters_are_rejected(changes, message):
    template = _grid_template()
    area = WorkArea(0.0, 100.0, 0.0, 80.0)
    changes = dict(changes)
    pixels_per_mm = changes.pop("pixels_per_mm", 4.0)

    with pytest.raises(ValueError, match=message):
        generate_template_test_frame(
            template,
            area,
            pixels_per_mm,
            **changes,
        )
