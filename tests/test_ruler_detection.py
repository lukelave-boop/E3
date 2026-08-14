from __future__ import annotations

import cv2
import numpy as np
import pytest

from laser_aligner.vision.ruler import (
    _Line,
    _periodicity,
    _ruler_extent_from_corner,
    detect_honeycomb_frame,
    detect_honeycomb_rulers,
    register_honeycomb_reference,
)


def test_automatic_frame_segmentation_finds_dominant_honeycomb_rectangle() -> None:
    image = np.full((700, 900, 3), 25, dtype=np.uint8)
    for y in range(110, 591, 14):
        for x in range(170, 731, 16):
            cv2.circle(image, (x, y), 5, (210, 210, 210), 1)
    cv2.rectangle(image, (155, 95), (745, 605), (180, 180, 180), 5)

    frame = detect_honeycomb_frame(image)

    assert frame[:, 0].min() == pytest.approx(155, abs=25)
    assert frame[:, 0].max() == pytest.approx(745, abs=25)
    assert frame[:, 1].min() == pytest.approx(95, abs=25)
    assert frame[:, 1].max() == pytest.approx(605, abs=25)


def test_automatic_frame_refines_outer_envelope_to_inner_ruler_square() -> None:
    image = np.full((800, 900, 3), 30, dtype=np.uint8)
    cv2.rectangle(image, (120, 70), (780, 730), (12, 12, 12), 10)
    cv2.rectangle(image, (145, 95), (755, 705), (225, 225, 225), 5)
    for y in range(110, 691, 15):
        for x in range(160, 741, 17):
            cv2.circle(image, (x, y), 6, (185, 185, 185), 1)

    frame = detect_honeycomb_frame(image)

    assert frame[:, 0].min() == pytest.approx(145, abs=12)
    assert frame[:, 0].max() == pytest.approx(755, abs=12)
    assert frame[:, 1].min() == pytest.approx(95, abs=12)
    assert frame[:, 1].max() == pytest.approx(705, abs=12)


def test_taught_honeycomb_reference_projects_known_square_into_live_frame() -> None:
    reference = np.full((700, 900, 3), 25, dtype=np.uint8)
    rng = np.random.default_rng(14)
    for _ in range(600):
        center = tuple(int(value) for value in rng.integers((80, 70), (820, 630)))
        radius = int(rng.integers(2, 8))
        color = int(rng.integers(70, 240))
        cv2.circle(reference, center, radius, (color, color, color), 1)
    corners = np.float32(((160, 590), (740, 590), (740, 110), (160, 110)))
    source = np.float32(((0, 0), (899, 0), (899, 699), (0, 699)))
    destination = np.float32(((40, 30), (830, 10), (860, 650), (20, 680)))
    transform = cv2.getPerspectiveTransform(source, destination)
    live = cv2.warpPerspective(reference, transform, (900, 700))

    detected = register_honeycomb_reference(live, reference, corners)
    expected = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), transform)[
        :, 0, :
    ]

    assert detected == pytest.approx(expected, abs=2.0)


def test_taught_registration_follows_support_not_stationary_background() -> None:
    rng = np.random.default_rng(91)
    reference = np.full((600, 800, 3), 24, dtype=np.uint8)
    corners = np.float32(((170, 500), (650, 500), (650, 100), (170, 100)))
    # Give the stationary area many tempting features.
    for _ in range(900):
        center = tuple(int(value) for value in rng.integers((10, 10), (790, 590)))
        if cv2.pointPolygonTest(corners.reshape(-1, 1, 2), center, False) >= 0:
            continue
        cv2.circle(reference, center, 2, (180, 180, 180), 1)
    # The movable support has its own broad, non-repeating feature field.
    for index in range(500):
        center = tuple(int(value) for value in rng.integers((185, 115), (635, 485)))
        radius = int(rng.integers(2, 6))
        shade = int(70 + index % 160)
        cv2.circle(reference, center, radius, (shade, shade, shade), 1)

    shift = np.float32(((1.0, 0.0, 11.0), (0.0, 1.0, -7.0)))
    moved = cv2.warpAffine(reference, shift, (800, 600))
    live = reference.copy()
    cv2.fillConvexPoly(live, corners.astype(np.int32), (24, 24, 24))
    shifted_corners = corners + np.float32((11.0, -7.0))
    mask = np.zeros(live.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, shifted_corners.astype(np.int32), 255)
    live[mask > 0] = moved[mask > 0]

    detected = register_honeycomb_reference(live, reference, corners)

    assert detected == pytest.approx(shifted_corners, abs=2.0)


def test_taught_registration_rejects_features_only_outside_support() -> None:
    rng = np.random.default_rng(103)
    reference = np.full((500, 700, 3), 24, dtype=np.uint8)
    corners = np.float32(((180, 410), (570, 410), (570, 90), (180, 90)))
    for _ in range(800):
        center = tuple(int(value) for value in rng.integers((10, 10), (690, 490)))
        if cv2.pointPolygonTest(corners.reshape(-1, 1, 2), center, False) < 0:
            cv2.circle(reference, center, 2, (190, 190, 190), 1)

    with pytest.raises(ValueError, match="support-local matches"):
        register_honeycomb_reference(reference.copy(), reference, corners)


def test_registered_corners_fit_inset_border_not_outer_dense_rectangle() -> None:
    image = np.full((620, 760, 3), 22, dtype=np.uint8)
    # A large dense outer rectangle and image border are deliberately stronger
    # than the inset cutting border. Seeded execution verification must stay in
    # four narrow taught-edge strips instead of selecting that outer envelope.
    cv2.rectangle(image, (35, 25), (725, 590), (235, 235, 235), 9)
    for y in range(45, 581, 13):
        for x in range(55, 711, 15):
            cv2.circle(image, (x, y), 5, (170, 170, 170), 1)
    expected = np.asarray(
        ((130.0, 500.0), (630.0, 500.0), (630.0, 120.0), (130.0, 120.0)),
    )
    cv2.polylines(
        image,
        [np.rint(expected).astype(np.int32).reshape(-1, 1, 2)],
        True,
        (245, 245, 245),
        5,
        cv2.LINE_AA,
    )
    seed = expected + np.asarray(
        ((3.0, -2.0), (3.0, -2.0), (3.0, -2.0), (3.0, -2.0)),
    )

    measured = detect_honeycomb_frame(image, seed_corners=seed)

    assert measured == pytest.approx(expected, abs=6.0)
    assert np.max(np.linalg.norm(measured - seed, axis=1)) > 1.0
    assert measured[:, 0].min() > 100.0
    assert measured[:, 0].max() < 660.0


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


def test_ruler_periodicity_refines_fractional_pixel_pitch() -> None:
    gray = np.full((500, 500), 220, dtype=np.uint8)
    for image_y in np.arange(50.0, 451.0, 5.5):
        y = int(round(float(image_y)))
        cv2.line(gray, (202, y), (225, y), 25, 2)
    line = _Line(np.asarray((200.0, 250.0)), np.asarray((0.0, 1.0)))

    pitch, score = _periodicity(
        gray,
        line,
        np.asarray((1.0, 0.0)),
        -200.0,
        200.0,
        5.5,
    )

    assert pitch == pytest.approx(5.5, abs=0.25)
    assert score >= 0.60


def test_three_hints_without_detectable_rulers_fail_closed() -> None:
    image = np.full((900, 900, 3), 180, dtype=np.uint8)

    with pytest.raises(ValueError, match="ruler edge|tick pattern"):
        detect_honeycomb_rulers(
            image,
            ((80.0, 80.0), (80.0, 800.0), (800.0, 800.0)),
            ruler_span_mm=190.0,
        )


def test_ruler_extent_comes_from_detected_corner_pitch_and_physical_span() -> None:
    corner = np.asarray((700.0, 600.0))

    first = _ruler_extent_from_corner(
        corner,
        np.asarray((1.0, 0.0)),
        5.5,
        190.0,
        reverse=True,
    )
    second = _ruler_extent_from_corner(
        corner,
        np.asarray((0.0, -1.0)),
        5.5,
        190.0,
        reverse=False,
    )

    assert first == pytest.approx((-345.0, 600.0))
    assert second == pytest.approx((700.0, -445.0))


def test_ruler_detection_rejects_malformed_images_hints_and_span() -> None:
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    hints = ((1.0, 1.0), (10.0, 1.0), (10.0, 10.0))

    with pytest.raises(ValueError, match="grayscale or color"):
        detect_honeycomb_rulers(image.astype(np.float32), hints)
    with pytest.raises(ValueError, match="exactly three"):
        detect_honeycomb_rulers(image, hints[:2])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite positive"):
        detect_honeycomb_rulers(image, hints, ruler_span_mm=float("nan"))
