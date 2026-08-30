from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass

import cv2
import numpy as np
import pytest

from laser_aligner.project.raster_vectorize import (
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationOptions,
    prepare_pixel_vectorization_mask,
    prepare_pixel_vectorization_source,
)
from laser_aligner.vision.camera_raster_normalization import (
    CAMERA_BACKGROUND_ENVELOPE_DIAMETER_MM,
    CAMERA_BACKGROUND_MODEL_MAX_DIMENSION_PX,
    CAMERA_BACKGROUND_MODEL_MAX_PIXELS_PER_MM,
    CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM,
    CAMERA_FLAT_FIELD_HISTOGRAM_BIN_WIDTH_LEVELS,
    CAMERA_FLAT_FIELD_MIN_BACKGROUND_COVERAGE,
    CAMERA_FLAT_FIELD_MIN_BORDER_COVERAGE,
    CAMERA_FLAT_FIELD_MIN_PALETTE_COVERAGE,
    CAMERA_FLAT_FIELD_MIN_SEPARATION_RATIO,
    CAMERA_NORMALIZATION_MAX_RESPONSE_SCALE,
    CAMERA_NORMALIZATION_MIN_RESPONSE_SCALE,
    CAMERA_NORMALIZATION_NOISE_FLOOR,
    CAMERA_NORMALIZATION_RESPONSE_MODEL,
    CAMERA_NORMALIZATION_RESPONSE_TRANSFER,
    CameraRasterNormalizationTiming,
    normalize_camera_trace_frame,
)

_PIXELS_PER_MM = 4.0


@dataclass(frozen=True, slots=True)
class _SyntheticCameraScene:
    image: np.ndarray
    illumination: np.ndarray
    stencil: np.ndarray
    sheet_background: np.ndarray
    shadow_background: np.ndarray
    machine_background: np.ndarray
    narrow_gap: np.ndarray
    hole: np.ndarray


def _stencil_mask(height: int, width: int) -> np.ndarray:
    stencil = np.zeros((height, width), dtype=np.uint8)

    # An H with a narrow gap to the neighboring O.
    cv2.rectangle(stencil, (160, 115), (178, 255), 255, thickness=-1)
    cv2.rectangle(stencil, (222, 115), (240, 255), 255, thickness=-1)
    cv2.rectangle(stencil, (178, 176), (222, 194), 255, thickness=-1)

    # An O supplies one real hole.
    cv2.ellipse(stencil, (299, 185), (51, 70), 0, 0, 360, 255, thickness=-1)
    cv2.ellipse(stencil, (299, 185), (25, 43), 0, 0, 360, 0, thickness=-1)

    # A separate I and underline provide nearby independent components.
    cv2.rectangle(stencil, (364, 115), (426, 132), 255, thickness=-1)
    cv2.rectangle(stencil, (387, 132), (404, 238), 255, thickness=-1)
    cv2.rectangle(stencil, (364, 238), (426, 255), 255, thickness=-1)
    cv2.rectangle(stencil, (167, 281), (425, 292), 255, thickness=-1)
    return stencil


def _synthetic_camera_scene(polarity: str) -> _SyntheticCameraScene:
    height, width = 480, 760
    rows, columns = np.mgrid[0:height, 0:width].astype(np.float32)
    x = columns / np.float32(width - 1)
    y = rows / np.float32(height - 1)
    stencil = _stencil_mask(height, width)

    localized_shadow = np.exp(
        np.float32(-0.5)
        * (
            ((x - np.float32(0.38)) / np.float32(0.12)) ** 2
            + ((y - np.float32(0.62)) / np.float32(0.19)) ** 2
        )
    )
    machine_weight = np.float32(1.0) / (
        np.float32(1.0)
        + np.exp((columns - np.float32(105.0)) / np.float32(16.0))
    )
    if polarity == "dark":
        illumination = (
            np.float32(210.0)
            + np.float32(34.0) * (x - np.float32(0.5))
            - np.float32(18.0)
            * (
                (x - np.float32(0.5)) ** 2
                + (y - np.float32(0.5)) ** 2
            )
            / np.float32(0.5)
            - np.float32(75.0) * localized_shadow
        )
        illumination = (
            illumination * (np.float32(1.0) - machine_weight)
            + np.float32(78.0) * machine_weight
        )
        photo = illumination.copy()
        photo[stencil > 0] = (
            photo[stencil > 0] * np.float32(0.38) + np.float32(3.0)
        )
    elif polarity == "light":
        illumination = (
            np.float32(61.0)
            + np.float32(28.0) * (x - np.float32(0.5))
            - np.float32(12.0)
            * (
                (x - np.float32(0.5)) ** 2
                + (y - np.float32(0.5)) ** 2
            )
            / np.float32(0.5)
            - np.float32(24.0) * localized_shadow
        )
        illumination = (
            illumination * (np.float32(1.0) - machine_weight)
            + np.float32(136.0) * machine_weight
        )
        photo = illumination.copy()
        photo[stencil > 0] += np.float32(0.72) * (
            np.float32(255.0) - photo[stencil > 0]
        )
    else:
        raise ValueError(f"Unknown synthetic polarity: {polarity}")

    photo = cv2.GaussianBlur(
        photo,
        (0, 0),
        sigmaX=0.55,
        sigmaY=0.55,
        borderType=cv2.BORDER_REFLECT_101,
    )
    noise = np.random.default_rng(20260829).normal(
        0.0,
        1.4,
        photo.shape,
    )
    grayscale = np.clip(np.rint(photo + noise), 0.0, 255.0).astype(np.uint8)
    image = np.repeat(grayscale[:, :, None], 3, axis=2)

    stencil_core = cv2.erode(stencil, np.ones((5, 5), dtype=np.uint8)) > 0
    safe_background = cv2.erode(
        (stencil == 0).astype(np.uint8),
        np.ones((7, 7), dtype=np.uint8),
    ) > 0
    sheet_background = safe_background & (columns > 145)
    shadow_background = (
        sheet_background
        & (columns > 180)
        & (columns < 380)
        & (rows > 210)
        & (rows < 420)
    )
    machine_background = safe_background & (columns < 50)
    narrow_gap = (
        safe_background
        & (columns > 241)
        & (columns < 247)
        & (rows > 120)
        & (rows < 250)
    )
    hole = (
        safe_background
        & (columns > 282)
        & (columns < 316)
        & (rows > 150)
        & (rows < 220)
    )
    assert np.any(stencil_core)
    assert all(
        np.any(region)
        for region in (
            sheet_background,
            shadow_background,
            machine_background,
            narrow_gap,
            hole,
        )
    )
    return _SyntheticCameraScene(
        image=image,
        illumination=illumination.astype(np.float32),
        stencil=stencil,
        sheet_background=sheet_background,
        shadow_background=shadow_background,
        machine_background=machine_background,
        narrow_gap=narrow_gap,
        hole=hole,
    )


def _dense_label_camera_scene() -> np.ndarray:
    """Two dense columns of 21.5 mm labels on a mild camera gradient."""

    size_px = 880
    rows, columns = np.indices((size_px, size_px), dtype=np.float32)
    photo = (
        np.float32(225.0)
        + np.float32(18.0) * columns / np.float32(size_px - 1)
        - np.float32(24.0) * rows / np.float32(size_px - 1)
        - np.float32(12.0)
        * (
            (columns - np.float32(size_px * 0.55)) ** 2
            + (rows - np.float32(size_px * 0.45)) ** 2
        )
        / np.float32(size_px * size_px)
    )
    width_px = int(81.7 * _PIXELS_PER_MM)
    height_px = int(21.5 * _PIXELS_PER_MM)
    radius_px = 14
    x_centers = tuple(int(value * _PIXELS_PER_MM) for value in (64.05, 155.95))
    y_centers = tuple(
        int((220.0 - (199.25 - row * 25.5)) * _PIXELS_PER_MM)
        for row in range(8)
    )
    for row, center_y_px in enumerate(y_centers):
        for column, center_x_px in enumerate(x_centers):
            label = np.zeros((size_px, size_px), dtype=np.uint8)
            cv2.rectangle(
                label,
                (
                    center_x_px - width_px // 2 + radius_px,
                    center_y_px - height_px // 2,
                ),
                (
                    center_x_px + width_px // 2 - radius_px,
                    center_y_px + height_px // 2,
                ),
                255,
                thickness=-1,
            )
            cv2.rectangle(
                label,
                (
                    center_x_px - width_px // 2,
                    center_y_px - height_px // 2 + radius_px,
                ),
                (
                    center_x_px + width_px // 2,
                    center_y_px + height_px // 2 - radius_px,
                ),
                255,
                thickness=-1,
            )
            for corner_x in (
                center_x_px - width_px // 2 + radius_px,
                center_x_px + width_px // 2 - radius_px,
            ):
                for corner_y in (
                    center_y_px - height_px // 2 + radius_px,
                    center_y_px + height_px // 2 - radius_px,
                ):
                    cv2.circle(
                        label,
                        (corner_x, corner_y),
                        radius_px,
                        255,
                        thickness=-1,
                    )
            photo[label > 0] = np.float32(62 + row * 4 + column * 2)

    photo += np.random.default_rng(20260829).normal(0.0, 1.2, photo.shape)
    grayscale = np.clip(np.rint(photo), 0.0, 255.0).astype(np.uint8)
    grayscale = cv2.GaussianBlur(grayscale, (5, 5), 0.9)
    return np.repeat(grayscale[:, :, None], 3, axis=2)


def _varied_long_glyph_camera_scene() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Long solid glyphs with dark surface marks on a mild camera gradient."""

    height, width = 640, 900
    rows, columns = np.mgrid[0:height, 0:width].astype(np.float32)
    illumination = (
        np.float32(195.0)
        + np.float32(14.0) * columns / np.float32(width - 1)
        - np.float32(10.0) * rows / np.float32(height - 1)
        + np.float32(4.0) * np.sin(columns / np.float32(150.0))
    )
    stencil = np.zeros((height, width), dtype=np.uint8)
    for center_x in (180, 360, 540, 720):
        cv2.rectangle(
            stencil,
            (center_x - 30, 100),
            (center_x + 30, 500),
            255,
            thickness=-1,
        )

    photo = illumination.copy()
    interior = (
        np.float32(88.0)
        + np.float32(10.0) * rows / np.float32(height)
        + np.float32(7.0) * np.sin(columns / np.float32(25.0))
    )
    photo[stencil > 0] = interior[stencil > 0]
    for center in ((180, 300), (360, 410), (540, 220), (720, 420)):
        cv2.ellipse(
            photo,
            center,
            (20, 32),
            25,
            0,
            360,
            45.0,
            thickness=-1,
        )
    photo += np.random.default_rng(4).normal(0.0, 1.5, photo.shape)
    grayscale = np.clip(np.rint(photo), 0.0, 255.0).astype(np.uint8)
    image = np.repeat(grayscale[:, :, None], 3, axis=2)
    stencil_core = cv2.erode(stencil, np.ones((9, 9), dtype=np.uint8)) > 0
    background_core = cv2.erode(
        (stencil == 0).astype(np.uint8),
        np.ones((15, 15), dtype=np.uint8),
    ) > 0
    return image, stencil_core, background_core


def _varied_long_light_glyph_camera_scene() -> (
    tuple[np.ndarray, np.ndarray, np.ndarray]
):
    """Long light glyphs with lighter surface marks on a mild camera gradient."""

    height, width = 640, 900
    rows, columns = np.mgrid[0:height, 0:width].astype(np.float32)
    illumination = (
        np.float32(58.0)
        + np.float32(10.0) * columns / np.float32(width - 1)
        - np.float32(8.0) * rows / np.float32(height - 1)
        + np.float32(3.0) * np.sin(columns / np.float32(150.0))
    )
    stencil = np.zeros((height, width), dtype=np.uint8)
    for center_x in (180, 360, 540, 720):
        cv2.rectangle(
            stencil,
            (center_x - 30, 100),
            (center_x + 30, 500),
            255,
            thickness=-1,
        )

    photo = illumination.copy()
    interior = (
        np.float32(174.0)
        + np.float32(8.0) * rows / np.float32(height)
        + np.float32(6.0) * np.sin(columns / np.float32(25.0))
    )
    photo[stencil > 0] = interior[stencil > 0]
    for center in ((180, 300), (360, 410), (540, 220), (720, 420)):
        cv2.ellipse(
            photo,
            center,
            (20, 32),
            25,
            0,
            360,
            225.0,
            thickness=-1,
        )
    photo += np.random.default_rng(7).normal(0.0, 1.5, photo.shape)
    grayscale = np.clip(np.rint(photo), 0.0, 255.0).astype(np.uint8)
    image = np.repeat(grayscale[:, :, None], 3, axis=2)
    stencil_core = cv2.erode(stencil, np.ones((9, 9), dtype=np.uint8)) > 0
    background_core = cv2.erode(
        (stencil == 0).astype(np.uint8),
        np.ones((15, 15), dtype=np.uint8),
    ) > 0
    return image, stencil_core, background_core


def _otsu_foreground(raster: np.ndarray) -> tuple[int, np.ndarray]:
    threshold, mask = cv2.threshold(
        raster,
        0,
        255,
        cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
    )
    return int(round(float(threshold))), mask


def _foreground_fraction(mask: np.ndarray, region: np.ndarray) -> float:
    return float(np.count_nonzero(mask[region])) / float(np.count_nonzero(region))


def test_dark_camera_normalization_removes_gradient_shadow_and_edge_background() -> None:
    scene = _synthetic_camera_scene("dark")
    result = normalize_camera_trace_frame(scene.image, _PIXELS_PER_MM)
    raw_threshold, raw_mask = _otsu_foreground(result.grayscale)
    normalized_threshold, normalized_mask = _otsu_foreground(result.dark_raster)
    stencil_core = cv2.erode(
        scene.stencil,
        np.ones((5, 5), dtype=np.uint8),
    ) > 0

    assert 0 < raw_threshold < 255
    assert 0 < normalized_threshold < 255
    assert _foreground_fraction(raw_mask, scene.shadow_background) > 0.20
    assert _foreground_fraction(normalized_mask, scene.shadow_background) < 0.005
    assert _foreground_fraction(raw_mask, scene.machine_background) > 0.95
    assert _foreground_fraction(normalized_mask, scene.machine_background) < 0.005
    assert _foreground_fraction(normalized_mask, scene.sheet_background) < 0.005
    assert _foreground_fraction(normalized_mask, stencil_core) > 0.98

    raw_background_std = float(np.std(result.grayscale[scene.sheet_background]))
    normalized_background_std = float(
        np.std(result.dark_raster[scene.sheet_background])
    )
    assert normalized_background_std < raw_background_std * 0.25
    assert normalized_background_std < 5.0
    assert (
        float(np.median(result.dark_raster[scene.sheet_background]))
        - float(np.median(result.dark_raster[stencil_core]))
        > 100.0
    )

    horizontal_step = np.abs(np.diff(result.background, axis=1))
    vertical_step = np.abs(np.diff(result.background, axis=0))
    assert float(np.percentile(horizontal_step, 99.0)) < 1.5
    assert float(np.percentile(vertical_step, 99.0)) < 1.5
    blank_error = np.abs(
        result.background[scene.sheet_background]
        - scene.illumination[scene.sheet_background]
    )
    assert float(np.median(blank_error)) < 6.0
    assert (
        float(
            np.corrcoef(
                result.background[scene.sheet_background],
                scene.illumination[scene.sheet_background],
            )[0, 1]
        )
        > 0.85
    )
    raw_stencil_contrast = float(
        np.median(
            scene.illumination[stencil_core]
            - result.grayscale[stencil_core].astype(np.float32)
        )
    )
    background_stencil_imprint = float(
        np.median(
            np.abs(
                result.background[stencil_core]
                - scene.illumination[stencil_core]
            )
        )
    )
    # A symmetric opening/closing midpoint may carry approximately half of an
    # isolated one-polarity feature into B, while the other half remains in S.
    assert background_stencil_imprint < raw_stencil_contrast * 0.52

    assert _foreground_fraction(normalized_mask, scene.narrow_gap) < 0.005
    assert _foreground_fraction(normalized_mask, scene.hole) < 0.005
    component_count, _labels = cv2.connectedComponents(normalized_mask)
    assert component_count - 1 == 4

    diagnostics = result.diagnostics
    assert diagnostics.background_model_kind == "rank_envelope"
    assert (
        diagnostics.flat_field_palette_coverage
        < CAMERA_FLAT_FIELD_MIN_PALETTE_COVERAGE
    )
    assert (
        diagnostics.flat_field_border_coverage
        < CAMERA_FLAT_FIELD_MIN_BORDER_COVERAGE
    )
    assert (
        diagnostics.flat_field_separation_ratio
        < CAMERA_FLAT_FIELD_MIN_SEPARATION_RATIO
    )
    assert diagnostics.background_envelope_diameter_mm == pytest.approx(35.0)
    assert diagnostics.background_envelope_kernel_width_px == 35
    assert diagnostics.background_envelope_kernel_height_px == 35
    assert diagnostics.background_smoothing_sigma_mm == pytest.approx(4.0)
    assert diagnostics.background_smoothing_sigma_model_px_x == pytest.approx(4.0)
    assert diagnostics.background_smoothing_sigma_model_px_y == pytest.approx(4.0)
    assert diagnostics.model_width_px == 190
    assert diagnostics.model_height_px == 120
    assert diagnostics.model_pixels_per_mm_x == pytest.approx(1.0)
    assert diagnostics.model_pixels_per_mm_y == pytest.approx(1.0)
    assert diagnostics.noise_floor_levels == CAMERA_NORMALIZATION_NOISE_FLOOR
    assert (
        CAMERA_NORMALIZATION_MIN_RESPONSE_SCALE
        <= diagnostics.response_scale_levels
        <= CAMERA_NORMALIZATION_MAX_RESPONSE_SCALE
    )
    assert diagnostics.response_model == CAMERA_NORMALIZATION_RESPONSE_MODEL
    assert diagnostics.response_transfer == CAMERA_NORMALIZATION_RESPONSE_TRANSFER


def test_light_camera_normalization_uses_the_same_exclusive_polarity_contract() -> None:
    scene = _synthetic_camera_scene("light")
    result = normalize_camera_trace_frame(scene.image, _PIXELS_PER_MM)
    _threshold, mask = _otsu_foreground(result.light_raster)
    stencil_core = cv2.erode(
        scene.stencil,
        np.ones((5, 5), dtype=np.uint8),
    ) > 0

    assert _foreground_fraction(mask, stencil_core) > 0.98
    assert _foreground_fraction(mask, scene.sheet_background) < 0.005
    assert _foreground_fraction(mask, scene.shadow_background) < 0.005
    assert _foreground_fraction(mask, scene.machine_background) < 0.005
    assert _foreground_fraction(mask, scene.narrow_gap) < 0.005
    assert _foreground_fraction(mask, scene.hole) < 0.005
    component_count, _labels = cv2.connectedComponents(mask)
    assert component_count - 1 == 4

    assert not np.any(
        (result.dark_raster < 255) & (result.light_raster < 255)
    )
    assert result.diagnostics.response_model == CAMERA_NORMALIZATION_RESPONSE_MODEL
    assert result.raster_for("dark") is result.dark_raster
    assert result.raster_for(" LIGHT ") is result.light_raster
    with pytest.raises(ValueError, match="polarity"):
        result.raster_for("absolute")


def test_flat_field_guard_rejects_a_quantized_low_frequency_shadow() -> None:
    height, width = 480, 760
    rows, columns = np.indices((height, width), dtype=np.float32)
    shadow = np.float32(80.0) * np.exp(
        np.float32(-0.5)
        * (
            ((columns - np.float32(width / 2)) / np.float32(64.0)) ** 2
            + ((rows - np.float32(height / 2)) / np.float32(64.0)) ** 2
        )
    )
    grayscale = np.clip(
        np.rint((np.float32(225.0) - shadow) / np.float32(16.0))
        * np.float32(16.0),
        0.0,
        255.0,
    ).astype(np.uint8)
    image = np.repeat(grayscale[:, :, None], 3, axis=2)

    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)

    assert result.diagnostics.flat_field_palette_coverage == pytest.approx(1.0)
    assert result.diagnostics.flat_field_border_coverage == pytest.approx(1.0)
    assert (
        result.diagnostics.flat_field_separation_ratio
        < CAMERA_FLAT_FIELD_MIN_SEPARATION_RATIO
    )
    assert result.diagnostics.background_model_kind == "rank_envelope"


def test_flat_field_guard_rejects_a_machine_colored_border_as_background() -> None:
    grayscale = np.full((1200, 1200), 40, dtype=np.uint8)
    cv2.rectangle(grayscale, (144, 144), (1055, 1055), 225, thickness=-1)
    cv2.rectangle(grayscale, (560, 560), (639, 639), 105, thickness=-1)
    image = np.repeat(grayscale[:, :, None], 3, axis=2)

    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    _threshold, mask = _otsu_foreground(result.dark_raster)
    object_core = np.zeros(grayscale.shape, dtype=bool)
    object_core[565:635, 565:635] = True
    machine_border = np.zeros(grayscale.shape, dtype=bool)
    machine_border[:100, :] = True
    machine_border[-100:, :] = True
    machine_border[:, :100] = True
    machine_border[:, -100:] = True

    assert result.diagnostics.flat_field_palette_coverage == pytest.approx(1.0)
    assert result.diagnostics.flat_field_border_coverage == pytest.approx(1.0)
    assert (
        result.diagnostics.flat_field_background_coverage
        < CAMERA_FLAT_FIELD_MIN_BACKGROUND_COVERAGE
    )
    assert result.diagnostics.background_model_kind == "rank_envelope"
    assert _foreground_fraction(mask, object_core) > 0.98
    assert _foreground_fraction(mask, machine_border) < 0.005


def test_rank_envelope_preserves_dense_21_5_mm_label_bodies() -> None:
    result = normalize_camera_trace_frame(
        _dense_label_camera_scene(),
        _PIXELS_PER_MM,
    )
    _threshold, mask = _otsu_foreground(result.dark_raster)
    component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask
    )
    minimum_area_px = int(30.0 * _PIXELS_PER_MM**2)
    retained = [
        stats[index]
        for index in range(1, component_count)
        if stats[index, cv2.CC_STAT_AREA] >= minimum_area_px
    ]

    assert len(retained) == 16
    assert all(325 <= values[cv2.CC_STAT_WIDTH] <= 328 for values in retained)
    assert all(85 <= values[cv2.CC_STAT_HEIGHT] <= 88 for values in retained)


def test_rank_envelope_preserves_varied_long_glyphs_at_manual_threshold() -> None:
    image, stencil_core, background_core = _varied_long_glyph_camera_scene()
    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    source_mask = (result.dark_raster <= 128).astype(np.uint8) * 255
    rgba = cv2.cvtColor(result.dark_raster, cv2.COLOR_GRAY2RGBA)
    source = prepare_pixel_vectorization_source(rgba)
    prepared = prepare_pixel_vectorization_mask(
        source,
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
            threshold=128,
            minimum_feature_area_mm2=0.5,
            smoothing_mm=0.0,
            simplification_tolerance_mm=0.1,
            contour_output=RasterContourOutput.ALL_CONTOURS,
        ),
        displayed_width_mm=225.0,
        displayed_height_mm=160.0,
    )
    contours, hierarchy = cv2.findContours(
        prepared.contour_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_NONE,
    )

    assert result.diagnostics.background_model_kind == "rank_envelope"
    grayscale = result.grayscale
    assert int(np.max(grayscale[stencil_core])) < int(
        np.min(grayscale[background_core])
    )
    assert _foreground_fraction(source_mask, stencil_core) > 0.995
    assert _foreground_fraction(source_mask, background_core) < 0.005
    assert prepared.threshold_used == 128
    assert prepared.connected_component_count == 4
    assert hierarchy is not None
    parents = hierarchy[0, :, 3].tolist()
    assert len(contours) == 4
    assert parents.count(-1) == 4
    assert sum(parent >= 0 for parent in parents) == 0


def test_light_rank_envelope_preserves_glyphs_beside_lighter_surface_marks() -> None:
    image, stencil_core, background_core = _varied_long_light_glyph_camera_scene()
    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    vector_grayscale = cv2.bitwise_not(result.light_raster)
    source_mask = vector_grayscale > 128
    rgba = cv2.cvtColor(vector_grayscale, cv2.COLOR_GRAY2RGBA)
    source = prepare_pixel_vectorization_source(rgba)
    prepared = prepare_pixel_vectorization_mask(
        source,
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
            threshold=128,
            invert=True,
            minimum_feature_area_mm2=0.5,
            smoothing_mm=0.0,
            simplification_tolerance_mm=0.1,
            contour_output=RasterContourOutput.ALL_CONTOURS,
        ),
        displayed_width_mm=225.0,
        displayed_height_mm=160.0,
    )
    contours, hierarchy = cv2.findContours(
        prepared.contour_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_NONE,
    )

    assert result.diagnostics.background_model_kind == "rank_envelope"
    assert _foreground_fraction(source_mask, stencil_core) > 0.995
    assert _foreground_fraction(source_mask, background_core) < 0.005
    assert prepared.threshold_used == 128
    assert prepared.connected_component_count == 4
    assert hierarchy is not None
    parents = hierarchy[0, :, 3].tolist()
    assert len(contours) == 4
    assert parents.count(-1) == 4
    assert sum(parent >= 0 for parent in parents) == 0


@pytest.mark.parametrize(
    ("polarity", "background_level", "feature_level"),
    [
        ("dark", 225, 45),
        ("light", 35, 215),
    ],
)
def test_flat_field_guard_preserves_solid_40_by_40_mm_fill(
    polarity: str,
    background_level: int,
    feature_level: int,
) -> None:
    grayscale = np.full((400, 600), background_level, dtype=np.uint8)
    cv2.rectangle(grayscale, (80, 80), (239, 239), feature_level, thickness=-1)
    image = np.repeat(grayscale[:, :, None], 3, axis=2)

    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    _threshold, mask = _otsu_foreground(result.raster_for(polarity))
    component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask
    )

    assert result.diagnostics.background_model_kind == "flat_field_constant"
    assert result.diagnostics.flat_field_palette_coverage == pytest.approx(1.0)
    assert result.diagnostics.flat_field_border_coverage == pytest.approx(1.0)
    assert np.all(result.background == np.float32(background_level))
    assert component_count == 2
    assert stats[1, cv2.CC_STAT_WIDTH] == 160
    assert stats[1, cv2.CC_STAT_HEIGHT] == 160
    assert stats[1, cv2.CC_STAT_AREA] == 160 * 160
    assert mask[160, 160] == 255


def test_flat_field_guard_preserves_a_low_noise_40_mm_clean_square() -> None:
    grayscale = np.full((400, 600), 225, dtype=np.float32)
    grayscale[80:240, 80:240] = np.float32(45.0)
    grayscale += np.random.default_rng(20260829).normal(
        0.0,
        1.5,
        grayscale.shape,
    ).astype(np.float32)
    grayscale_uint8 = np.clip(np.rint(grayscale), 0.0, 255.0).astype(np.uint8)
    image = np.repeat(grayscale_uint8[:, :, None], 3, axis=2)

    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    _threshold, mask = _otsu_foreground(result.dark_raster)
    square_core = np.zeros(grayscale.shape, dtype=bool)
    square_core[84:236, 84:236] = True
    background_core = np.ones(grayscale.shape, dtype=np.uint8)
    background_core[76:244, 76:244] = 0
    background_core = background_core > 0

    assert result.diagnostics.background_model_kind == "flat_field_constant"
    assert (
        result.diagnostics.flat_field_histogram_bin_width_levels
        == CAMERA_FLAT_FIELD_HISTOGRAM_BIN_WIDTH_LEVELS
        == 4
    )
    assert (
        result.diagnostics.flat_field_palette_coverage
        >= CAMERA_FLAT_FIELD_MIN_PALETTE_COVERAGE
    )
    assert _foreground_fraction(mask, square_core) > 0.999
    assert _foreground_fraction(mask, background_core) < 0.001
    assert mask[160, 160] == 255


def test_reciprocal_transfer_preserves_antialias_levels_without_black_clipping() -> None:
    grayscale = np.full((400, 600), 225, dtype=np.uint8)
    cv2.rectangle(grayscale, (80, 80), (239, 239), 45, thickness=-1)
    cv2.rectangle(grayscale, (80, 80), (239, 239), 120, thickness=1)
    grayscale[300:320, 300:320] = 158
    image = np.repeat(grayscale[:, :, None], 3, axis=2)

    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    _threshold, mask = _otsu_foreground(result.dark_raster)
    response = np.maximum(
        -result.signed_residual - np.float32(CAMERA_NORMALIZATION_NOISE_FLOOR),
        np.float32(0.0),
    )
    scale = np.float32(result.diagnostics.response_scale_levels)
    expected = np.rint(
        np.float32(255.0) * scale / (scale + response)
    ).astype(np.uint8)

    assert np.array_equal(result.dark_raster, expected)
    assert result.diagnostics.response_transfer == "reciprocal"
    assert result.diagnostics.response_scale_levels == pytest.approx(64.0)
    assert 0 < int(result.dark_raster[160, 160]) < 128
    assert (
        int(result.dark_raster[160, 160])
        < int(result.dark_raster[80, 160])
        < 128
    )
    assert result.dark_raster[0, 0] == 255
    assert result.dark_raster[310, 310] == 128
    assert np.unique(result.dark_raster).size == 4
    assert np.array_equal(mask > 0, grayscale < 225)


@pytest.mark.parametrize(
    (
        "polarity",
        "background_level",
        "feature_level",
        "expected_source_levels",
        "expected_threshold",
    ),
    [
        ("dark", 225, 40, [66, 255], 68),
        ("light", 35, 215, [0, 187], 2),
    ],
)
def test_flat_field_two_level_native_mask_keeps_one_real_hole_without_overshoot(
    polarity: str,
    background_level: int,
    feature_level: int,
    expected_source_levels: list[int],
    expected_threshold: int,
) -> None:
    image = np.full((400, 600, 3), background_level, dtype=np.uint8)
    feature_bgr = (feature_level, feature_level, feature_level)
    background_bgr = (background_level, background_level, background_level)
    cv2.rectangle(image, (60, 70), (190, 180), feature_bgr, thickness=-1)
    cv2.circle(image, (360, 125), 60, feature_bgr, thickness=-1)
    cv2.circle(image, (360, 125), 25, background_bgr, thickness=-1)

    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    artwork_raster = result.raster_for(polarity)
    vector_grayscale = (
        artwork_raster
        if polarity == "dark"
        else cv2.bitwise_not(artwork_raster)
    )
    rgba = cv2.cvtColor(vector_grayscale, cv2.COLOR_GRAY2RGBA)
    source = prepare_pixel_vectorization_source(rgba)
    prepared = prepare_pixel_vectorization_mask(
        source,
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.AUTO_THRESHOLD,
            invert=polarity == "light",
            minimum_feature_area_mm2=50.0,
            smoothing_mm=0.0,
            simplification_tolerance_mm=0.1,
            contour_output=RasterContourOutput.ALL_CONTOURS,
        ),
        displayed_width_mm=150.0,
        displayed_height_mm=100.0,
    )
    contours, hierarchy = cv2.findContours(
        prepared.contour_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    assert result.diagnostics.background_model_kind == "flat_field_constant"
    assert np.unique(source.composited_grayscale).tolist() == expected_source_levels
    assert prepared.threshold_used == expected_threshold
    assert prepared.connected_component_count == 2
    assert len(contours) == 3
    assert hierarchy is not None
    parents = hierarchy[0, :, 3].tolist()
    assert parents.count(-1) == 2
    assert sum(parent >= 0 for parent in parents) == 1


def test_manual_threshold_4x_mask_does_not_invent_holes_in_camera_glyphs() -> None:
    stencil = _stencil_mask(480, 760)
    grayscale = np.full(stencil.shape, 225, dtype=np.uint8)
    grayscale[stencil > 0] = 45
    plateau_origins = ((170, 150), (230, 150), (390, 170), (200, 285))
    for x, y in plateau_origins:
        assert np.all(stencil[y : y + 2, x : x + 2] == 255)
        # These samples remain foreground at the selected threshold after
        # normalization. The 4x reconstruction must not ring past that
        # threshold and turn them into phantom holes.
        grayscale[y : y + 2, x : x + 2] = 158
    image = np.repeat(grayscale[:, :, None], 3, axis=2)

    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    rgba = cv2.cvtColor(result.dark_raster, cv2.COLOR_GRAY2RGBA)
    source = prepare_pixel_vectorization_source(rgba)
    prepared = prepare_pixel_vectorization_mask(
        source,
        RasterVectorizationOptions(
            detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
            threshold=128,
            minimum_feature_area_mm2=0.0,
            smoothing_mm=0.0,
            simplification_tolerance_mm=0.1,
            contour_output=RasterContourOutput.ALL_CONTOURS,
        ),
        displayed_width_mm=190.0,
        displayed_height_mm=120.0,
    )
    contours, hierarchy = cv2.findContours(
        prepared.contour_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_NONE,
    )

    assert result.diagnostics.background_model_kind == "flat_field_constant"
    assert all(
        np.all(result.dark_raster[y : y + 2, x : x + 2] == 128)
        for x, y in plateau_origins
    )
    assert np.all(source.composited_grayscale[stencil > 0] <= 128)
    assert prepared.threshold_used == 128
    assert prepared.connected_component_count == 4
    assert len(contours) == 5
    assert hierarchy is not None
    parents = hierarchy[0, :, 3].tolist()
    assert parents.count(-1) == 4
    assert sum(parent >= 0 for parent in parents) == 1


def test_clean_high_contrast_camera_raster_retains_exact_otsu_geometry() -> None:
    height, width = 480, 760
    stencil = _stencil_mask(height, width)
    grayscale = np.full((height, width), 235, dtype=np.uint8)
    grayscale[stencil > 0] = 25
    image = np.repeat(grayscale[:, :, None], 3, axis=2)

    result = normalize_camera_trace_frame(image, _PIXELS_PER_MM)
    _raw_threshold, raw_mask = _otsu_foreground(grayscale)
    _normalized_threshold, normalized_mask = _otsu_foreground(result.dark_raster)

    assert np.array_equal(normalized_mask, raw_mask)
    raw_components, _labels = cv2.connectedComponents(raw_mask)
    normalized_components, _labels = cv2.connectedComponents(normalized_mask)
    assert raw_components == normalized_components == 5
    raw_contours, raw_hierarchy = cv2.findContours(
        raw_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    normalized_contours, normalized_hierarchy = cv2.findContours(
        normalized_mask,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    assert len(raw_contours) == len(normalized_contours) == 5
    assert raw_hierarchy is not None
    assert normalized_hierarchy is not None
    assert np.array_equal(raw_hierarchy, normalized_hierarchy)


def test_result_arrays_are_defensive_immutable_and_timing_is_bounded() -> None:
    scene = _synthetic_camera_scene("dark")
    source = scene.image.copy()
    expected = source.copy()
    timing = CameraRasterNormalizationTiming()

    result = normalize_camera_trace_frame(
        source,
        _PIXELS_PER_MM,
        timing=timing,
    )
    source.fill(0)

    assert np.array_equal(result.corrected_bgr, expected)
    for values in (
        result.corrected_bgr,
        result.grayscale,
        result.background,
        result.signed_residual,
        result.dark_raster,
        result.light_raster,
    ):
        assert values.flags.c_contiguous
        assert not values.flags.writeable
        with pytest.raises(ValueError):
            values.setflags(write=True)
    with pytest.raises(FrozenInstanceError):
        result.diagnostics.width_px = 1  # type: ignore[misc]

    expected_stages = {
        "camera_normalization_total",
        "grayscale_preparation",
        "background_estimation",
        "normalization",
    }
    assert timing.stage_calls == {stage: 1 for stage in expected_stages}
    assert expected_stages == timing.stage_seconds.keys()
    assert all(value >= 0.0 for value in timing.stage_seconds.values())
    assert timing.stage_seconds["camera_normalization_total"] >= max(
        timing.stage_seconds[stage]
        for stage in expected_stages - {"camera_normalization_total"}
    )
    assert timing.snapshot()["background_estimation"]["calls"] == 1
    timing.reset()
    assert timing.stage_calls == {}
    assert timing.stage_seconds == {}

    diagnostics = result.diagnostics
    assert len(diagnostics.normalization_key) == 64
    assert diagnostics.width_px == expected.shape[1]
    assert diagnostics.height_px == expected.shape[0]
    assert diagnostics.pixels_per_mm == _PIXELS_PER_MM
    assert diagnostics.model_width_px <= CAMERA_BACKGROUND_MODEL_MAX_DIMENSION_PX
    assert diagnostics.model_height_px <= CAMERA_BACKGROUND_MODEL_MAX_DIMENSION_PX
    assert (
        diagnostics.model_pixels_per_mm_x
        <= CAMERA_BACKGROUND_MODEL_MAX_PIXELS_PER_MM + 1e-12
    )
    assert (
        diagnostics.background_envelope_diameter_mm
        == CAMERA_BACKGROUND_ENVELOPE_DIAMETER_MM
    )
    assert (
        diagnostics.background_smoothing_sigma_mm
        == CAMERA_BACKGROUND_SMOOTHING_SIGMA_MM
    )
    assert diagnostics.background_envelope_kernel_width_px % 2 == 1
    assert diagnostics.background_envelope_kernel_height_px % 2 == 1


@pytest.mark.parametrize("pixels_per_mm", [0.0, -1.0, float("inf"), float("nan"), True])
def test_normalization_rejects_invalid_physical_scale(pixels_per_mm: object) -> None:
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="pixels_per_mm"):
        normalize_camera_trace_frame(image, pixels_per_mm)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "image",
    [
        np.zeros((0, 8, 3), dtype=np.uint8),
        np.zeros((8, 8), dtype=np.uint8),
        np.zeros((8, 8, 4), dtype=np.uint8),
        np.zeros((8, 8, 3), dtype=np.float32),
    ],
)
def test_normalization_rejects_non_bgr_camera_arrays(image: np.ndarray) -> None:
    with pytest.raises(ValueError, match="uint8 BGR"):
        normalize_camera_trace_frame(image, _PIXELS_PER_MM)
