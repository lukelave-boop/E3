from __future__ import annotations

import cv2
import numpy as np
import pytest

from laser_aligner.vision.ruler import _Line, _periodicity, detect_honeycomb_rulers


def test_ruler_periodicity_accepts_repeated_one_millimeter_ticks() -> None:
    gray = np.full((500, 500), 220, dtype=np.uint8)
    for image_y in range(50, 451, 5):
        cv2.line(gray, (202, image_y), (225, image_y), 25, 2)
    line = _Line(np.asarray((200.0, 250.0)), np.asarray((0.0, 1.0)))

    pitch, score = _periodicity(
        gray,
        line,
        np.asarray((1.0, 0.0)),
        -200.0,
        200.0,
        5.0,
    )

    assert pitch == pytest.approx(5.0, abs=1.0)
    assert score >= 0.85


def test_three_hints_without_detectable_rulers_fail_closed() -> None:
    image = np.full((900, 900, 3), 180, dtype=np.uint8)

    with pytest.raises(ValueError, match="ruler edge|tick pattern"):
        detect_honeycomb_rulers(
            image,
            ((80.0, 80.0), (80.0, 800.0), (800.0, 800.0)),
            ruler_span_mm=190.0,
        )


def test_ruler_detection_rejects_malformed_images_hints_and_span() -> None:
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    hints = ((1.0, 1.0), (10.0, 1.0), (10.0, 10.0))

    with pytest.raises(ValueError, match="grayscale or color"):
        detect_honeycomb_rulers(image.astype(np.float32), hints)
    with pytest.raises(ValueError, match="exactly three"):
        detect_honeycomb_rulers(image, hints[:2])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite positive"):
        detect_honeycomb_rulers(image, hints, ruler_span_mm=float("nan"))
