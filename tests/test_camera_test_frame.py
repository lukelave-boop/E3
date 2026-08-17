from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.camera import (
    corrected_frame_size,
    load_corrected_test_image,
    prepare_corrected_test_image,
)
from laser_aligner.config import WorkArea


def test_corrected_test_image_resizes_without_changing_full_bed_aspect() -> None:
    area = WorkArea(0.0, 220.0, 0.0, 110.0)
    source = np.full((500, 1000, 3), (15, 90, 210), dtype=np.uint8)

    output = prepare_corrected_test_image(source, area, 2.0)

    assert corrected_frame_size(area, 2.0) == (440, 220)
    assert output.shape == (220, 440, 3)
    assert output.dtype == np.uint8
    assert tuple(output[0, 0]) == pytest.approx((15, 90, 210), abs=1)


def test_corrected_test_image_rejects_an_aspect_ratio_that_would_distort_mm() -> None:
    area = WorkArea(0.0, 220.0, 0.0, 220.0)

    with pytest.raises(ValueError, match="expected aspect ratio 1.0000"):
        prepare_corrected_test_image(
            np.zeros((480, 640, 3), dtype=np.uint8),
            area,
            4.0,
        )


def test_corrected_test_image_rejects_a_near_match_that_would_still_warp_mm() -> None:
    area = WorkArea(0.0, 220.0, 0.0, 220.0)

    with pytest.raises(ValueError, match="expected aspect ratio 1.0000"):
        prepare_corrected_test_image(
            np.zeros((1000, 1009, 3), dtype=np.uint8),
            area,
            4.0,
        )


def test_corrected_test_image_allows_integer_dimension_rounding_only() -> None:
    area = WorkArea(0.0, 300.0, 0.0, 200.0)
    source = np.full((667, 1000, 3), (30, 100, 200), dtype=np.uint8)

    output = prepare_corrected_test_image(source, area, 3.0)

    assert output.shape == (600, 900, 3)
    assert tuple(output[0, 0]) == pytest.approx((30, 100, 200), abs=1)


def test_load_corrected_test_image_decodes_png_from_a_path(tmp_path: Path) -> None:
    source = np.zeros((220, 220, 3), dtype=np.uint8)
    source[:, :, 2] = 240
    ok, encoded = cv2.imencode(".png", source)
    assert ok
    path = tmp_path / "lábel sheet 测试.png"
    path.write_bytes(encoded.tobytes())

    output = load_corrected_test_image(
        path,
        WorkArea(0.0, 220.0, 0.0, 220.0),
        2.0,
    )

    assert output.shape == (440, 440, 3)
    assert int(output[:, :, 2].min()) == 240


def test_load_corrected_test_image_reports_decode_failure(tmp_path: Path) -> None:
    path = tmp_path / "not-an-image.png"
    path.write_text("not pixels", encoding="utf-8")

    with pytest.raises(ValueError, match="Could not decode test image"):
        load_corrected_test_image(
            path,
            WorkArea(),
            4.0,
        )
