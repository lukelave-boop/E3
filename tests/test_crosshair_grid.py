import cv2
import numpy as np
import pytest

from laser_aligner.vision.fiducials import (
    detect_crosshair_grid,
    detect_crosshairs_near,
    detect_keyed_crosshair_grid,
)


def _keyed_grid_image(*, include_keys: bool = True) -> tuple[np.ndarray, list[dict[str, float | int]]]:
    image = np.full((900, 1200, 3), 190, dtype=np.uint8)
    plate = np.full((700, 700, 3), 235, dtype=np.uint8)
    source_coordinates = [105, 227, 350, 472, 595]
    machine_coordinates = [40.0, 75.0, 110.0, 145.0, 180.0]
    targets: list[dict[str, float | int]] = []
    for row, (source_y, machine_y) in enumerate(zip(source_coordinates, machine_coordinates, strict=True)):
        for column, (source_x, machine_x) in enumerate(zip(source_coordinates, machine_coordinates, strict=True)):
            identifier = row * 5 + column + 1
            arm = 14
            if include_keys and (row, column) == (1, 1):
                arm = 28
            elif include_keys and (row, column) == (1, 2):
                arm = 21
            cv2.line(plate, (source_x - arm, source_y), (source_x + arm, source_y), (25, 25, 25), 3)
            cv2.line(plate, (source_x, source_y - arm), (source_x, source_y + arm), (25, 25, 25), 3)
            targets.append(
                {
                    "id": identifier,
                    "machine_x": machine_x,
                    "machine_y": machine_y,
                }
            )
    source = np.float32([[0, 0], [699, 0], [699, 699], [0, 699]])
    destination = np.float32([[230, 90], [1000, 130], [940, 830], [160, 790]])
    transform = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(plate, transform, (1200, 900), borderValue=(190, 190, 190))
    mask = cv2.warpPerspective(np.full((700, 700), 255, np.uint8), transform, (1200, 900))
    image[mask > 0] = warped[mask > 0]
    return image, targets


def test_detect_crosshair_grid_on_synthetic_plate():
    image = np.full((900, 1200, 3), 190, dtype=np.uint8)
    plate = np.full((700, 700, 3), 225, dtype=np.uint8)
    coordinates = [32, 191, 350, 509, 668]
    for y in coordinates:
        for x in coordinates:
            cv2.line(plate, (x - 14, y), (x + 14, y), (35, 35, 35), 3)
            cv2.line(plate, (x, y - 14), (x, y + 14), (35, 35, 35), 3)
            cv2.circle(plate, (x, y), 8, (35, 35, 35), 2)
    cv2.rectangle(plate, (1, 1), (698, 698), (45, 45, 45), 4)
    source = np.float32([[0, 0], [699, 0], [699, 699], [0, 699]])
    destination = np.float32([[230, 90], [1000, 130], [940, 830], [160, 790]])
    transform = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(plate, transform, (1200, 900), borderValue=(190, 190, 190))
    mask = cv2.warpPerspective(np.full((700, 700), 255, np.uint8), transform, (1200, 900))
    image[mask > 0] = warped[mask > 0]
    result = detect_crosshair_grid(image)
    assert result['detected'] is True
    assert len(result['points']) == 25
    assert result['points'][0]['machine_x'] == 10.0
    assert result['points'][0]['machine_y'] == 10.0
    assert result['points'][-1]['machine_x'] == 210.0
    assert result['points'][-1]['machine_y'] == 210.0


@pytest.mark.parametrize(
    "transform",
    (
        lambda image: image,
        lambda image: cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
        lambda image: cv2.rotate(image, cv2.ROTATE_180),
        lambda image: cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE),
        lambda image: cv2.flip(image, 1),
        lambda image: cv2.flip(image, 0),
        lambda image: cv2.transpose(image),
        lambda image: cv2.flip(cv2.transpose(image), -1),
    ),
)
def test_detect_keyed_crosshair_grid_resolves_all_rotations_and_reflections(transform):
    image, targets = _keyed_grid_image()

    result = detect_keyed_crosshair_grid(transform(image), targets)

    assert result["detected"] is True
    assert result["confidence"] == "high"
    assert len(result["points"]) == 25
    assert [(item["machine_x"], item["machine_y"]) for item in result["points"]] == [
        (item["machine_x"], item["machine_y"]) for item in targets
    ]


def test_detect_keyed_crosshair_grid_rejects_symmetric_unkeyed_pattern():
    image, targets = _keyed_grid_image(include_keys=False)

    result = detect_keyed_crosshair_grid(image, targets)

    assert result["detected"] is False
    assert "orientation-key" in result["reason"]


def test_detect_keyed_crosshair_grid_ignores_surrounding_hardware_clutter():
    image, targets = _keyed_grid_image()
    cv2.rectangle(image, (0, 0), (180, 899), (25, 25, 25), 20)
    cv2.rectangle(image, (1050, 0), (1199, 899), (15, 15, 15), -1)
    for index in range(18):
        x = 25 + (index % 3) * 48
        y = 35 + index * 45
        cv2.circle(image, (x, y), 5 + index % 7, (20, 20, 20), -1)

    result = detect_keyed_crosshair_grid(image, targets)

    assert result["detected"] is True
    assert len(result["points"]) == 25
    assert result["key_sizes_px"]["large"] > result["key_sizes_px"]["medium"]
    assert result["key_sizes_px"]["medium"] > result["key_sizes_px"]["regular_maximum"]


def test_detect_keyed_crosshair_grid_falls_back_from_clustered_assembly(monkeypatch):
    image, targets = _keyed_grid_image()
    original = cv2.findCirclesGrid
    attempted_flags: list[int] = []

    def find_grid(*args, **kwargs):
        flags = kwargs["flags"]
        attempted_flags.append(flags)
        if flags & cv2.CALIB_CB_CLUSTERING:
            return False, None
        return original(*args, **kwargs)

    monkeypatch.setattr(cv2, "findCirclesGrid", find_grid)

    result = detect_keyed_crosshair_grid(image, targets)

    assert result["detected"] is True
    assert any(flags & cv2.CALIB_CB_CLUSTERING for flags in attempted_flags)
    assert cv2.CALIB_CB_SYMMETRIC_GRID in attempted_flags


def test_detect_keyed_crosshair_grid_rejects_blank_image():
    _, targets = _keyed_grid_image()

    result = detect_keyed_crosshair_grid(np.full((900, 1200, 3), 230, dtype=np.uint8), targets)

    assert result["detected"] is False
    assert result["points"] == []


def test_detect_crosshairs_near_accepts_sparse_fine_registration_targets():
    image = np.full((600, 800, 3), 220, dtype=np.uint8)
    centers = (
        (100, 100),
        (700, 100),
        (300, 250),
        (700, 250),
        (100, 400),
        (500, 400),
        (100, 520),
        (700, 520),
    )
    expected = []
    for index, (x, y) in enumerate(centers, start=1):
        cv2.line(image, (x - 15, y), (x + 15, y), (25, 25, 25), 3)
        cv2.line(image, (x, y - 15), (x, y + 15), (25, 25, 25), 3)
        expected.append(
            {
                "id": index,
                "image_x": x - 4,
                "image_y": y + 3,
                "machine_x": float(index * 10),
                "machine_y": float(index * 12),
            }
        )

    result = detect_crosshairs_near(image, expected, search_radius_px=25)

    assert result["detected"] is True
    assert len(result["points"]) == 8
    for point, center in zip(result["points"], centers, strict=True):
        assert point["image_x"] == pytest.approx(center[0], abs=1.5)
        assert point["image_y"] == pytest.approx(center[1], abs=1.5)


@pytest.mark.parametrize(
    "image",
    (
        np.empty((0, 0, 3), dtype=np.uint8),
        np.zeros((20, 20, 3), dtype=np.float32),
        np.zeros((20, 20, 2), dtype=np.uint8),
    ),
)
def test_crosshair_detectors_reject_malformed_images(image: np.ndarray) -> None:
    _, targets = _keyed_grid_image()

    assert detect_crosshair_grid(image)["detected"] is False
    assert detect_keyed_crosshair_grid(image, targets)["detected"] is False
    assert detect_crosshairs_near(image, targets)["detected"] is False


@pytest.mark.parametrize(
    ("grid_size", "plate_size_mm", "coordinates_mm"),
    (
        (True, 220.0, (10.0, 60.0)),
        (2, float("nan"), (10.0, 60.0)),
        (2, 220.0, (60.0, 10.0)),
        (3, 220.0, (10.0, 60.0)),
    ),
)
def test_crosshair_grid_rejects_malformed_geometry(
    grid_size: object,
    plate_size_mm: object,
    coordinates_mm: tuple[float, ...],
) -> None:
    result = detect_crosshair_grid(
        np.zeros((20, 20, 3), dtype=np.uint8),
        grid_size=grid_size,  # type: ignore[arg-type]
        plate_size_mm=plate_size_mm,  # type: ignore[arg-type]
        coordinates_mm=coordinates_mm,
    )

    assert result["detected"] is False


def test_crosshairs_near_rejects_duplicate_or_nonfinite_expected_marks() -> None:
    image = np.zeros((30, 30, 3), dtype=np.uint8)
    duplicate = [
        {"id": 1, "image_x": 5.0, "image_y": 5.0, "machine_x": 1.0, "machine_y": 1.0},
        {"id": 1, "image_x": 8.0, "image_y": 8.0, "machine_x": 2.0, "machine_y": 2.0},
    ]
    nonfinite = [
        {"id": 1, "image_x": float("nan"), "image_y": 5.0, "machine_x": 1.0, "machine_y": 1.0}
    ]

    assert detect_crosshairs_near(image, duplicate)["detected"] is False
    assert detect_crosshairs_near(image, nonfinite)["detected"] is False
