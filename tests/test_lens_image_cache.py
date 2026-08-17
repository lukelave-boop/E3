import json
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.lens import LensCalibrator
from laser_aligner.config import LensCalibrationSettings
from laser_aligner.errors import CalibrationError


def test_capture_caches_detection_and_status_does_not_redetect(tmp_path: Path) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=7, rows=7, square_size_mm=35.0),
    )
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    corners = np.asarray(
        [
            [[20.0 + column * 15.0, 15.0 + row * 12.0]]
            for row in range(7)
            for column in range(7)
        ],
        dtype=np.float32,
    )

    with patch.object(calibrator, "detect_corners", return_value=(True, corners)) as detect:
        result = calibrator.capture(image)
        assert result["found"] is True
        assert detect.call_count == 1

    with patch.object(calibrator, "detect_corners", side_effect=AssertionError("cache was bypassed")):
        first = calibrator.status()
        second = calibrator.status()

    assert first["image_count"] == 1
    assert first["usable_image_count"] == 1
    assert second["usable_image_count"] == 1


def test_existing_image_is_cataloged_then_explicitly_indexed_once(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=7, rows=7))
    path = calibrator.image_dir / "existing.jpg"
    assert cv2.imwrite(str(path), np.zeros((80, 100, 3), dtype=np.uint8))

    with patch.object(calibrator, "detect_corners", return_value=(False, None)) as detect:
        cold = calibrator.status()
        assert cold["usable_image_count"] == 0
        assert cold["index"]["state"] == "pending"
        assert detect.call_count == 0

        calibrator.index_pending_captures()
        assert detect.call_count == 1
        warm = calibrator.status()
        assert warm["usable_image_count"] == 0
        assert warm["index"]["state"] == "ready"
        assert detect.call_count == 1


def test_pattern_change_invalidates_detection_cache(tmp_path: Path) -> None:
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    first = LensCalibrator(tmp_path, LensCalibrationSettings(columns=7, rows=7))
    with patch.object(first, "detect_corners", return_value=(True, np.zeros((49, 1, 2)))):
        first.capture(image)

    changed = LensCalibrator(tmp_path, LensCalibrationSettings(columns=9, rows=6))
    with patch.object(changed, "detect_corners", return_value=(False, None)) as detect:
        status = changed.status()
        assert detect.call_count == 0
        assert status["index"]["state"] == "pending"
        changed.index_pending_captures()

    assert detect.call_count == 1
    assert changed.status()["usable_image_count"] == 0


def test_new_capture_is_lossless_png_with_quality_and_coverage(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    image = np.zeros((100, 140, 3), dtype=np.uint8)
    image[:, 70:] = 255
    corners = np.array([[[20, 20]], [[100, 20]], [[20, 80]], [[100, 80]]], dtype=np.float32)

    with patch.object(calibrator, "detect_corners", return_value=(True, corners)):
        result = calibrator.capture(image)

    path = calibrator.image_dir / result["name"]
    assert path.suffix == ".png"
    assert np.array_equal(cv2.imread(str(path), cv2.IMREAD_COLOR), image)
    assert result["quality"]["contrast_span"] == pytest.approx(255.0)
    assert result["board_coverage_percent"] > 30.0
    assert result["board_center"] == pytest.approx([60 / 140, 0.5])


def test_failed_persisted_capture_analysis_leaves_no_staging_or_index(
    tmp_path: Path,
) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    image = np.zeros((100, 140, 3), dtype=np.uint8)

    with patch.object(calibrator, "detect_corners", side_effect=RuntimeError("detector failed")):
        with pytest.raises(CalibrationError, match="persist and analyze"):
            calibrator.capture(image)

    assert not list(calibrator.image_dir.iterdir())
    assert not list(tmp_path.glob(".lens-capture-*.pending"))
    assert not calibrator.image_index_path.exists()


def test_png_is_preferred_when_legacy_jpeg_has_the_same_stem(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    jpg = calibrator.image_dir / "same.jpg"
    png = calibrator.image_dir / "same.png"
    assert cv2.imwrite(str(jpg), np.zeros((20, 20, 3), dtype=np.uint8))
    assert cv2.imwrite(str(png), np.full((20, 20, 3), 200, dtype=np.uint8))

    with patch.object(calibrator, "detect_corners", return_value=(False, None)) as detect:
        images = calibrator.list_images()
        assert detect.call_count == 0
        calibrator.index_pending_captures()

    assert [item["name"] for item in images] == ["same.png"]
    assert detect.call_count == 1


def test_delete_and_clear_captures_include_legacy_formats(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings())
    for name in ("first.jpg", "second.jpeg", "third.png"):
        assert cv2.imwrite(str(calibrator.image_dir / name), np.zeros((8, 8, 3), dtype=np.uint8))

    assert calibrator.delete_capture("first.jpg") is True
    assert calibrator.delete_capture("first.jpg") is False
    assert calibrator.clear_captures() == 2
    assert not any(calibrator.image_dir.iterdir())

    with pytest.raises(CalibrationError, match="filename"):
        calibrator.delete_capture("../outside.png")


def test_stale_detector_index_is_not_coerced_or_rewritten_by_status(
    tmp_path: Path,
) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    path = calibrator.image_dir / "stale.jpg"
    assert cv2.imwrite(str(path), np.zeros((80, 100, 3), dtype=np.uint8))
    stat = path.stat()
    stale = {
        "schema_version": 3,
        "pattern": {"columns": 2, "rows": 2},
        "detector": {
            "method": "bounded-checkerboard-preview",
            "version": 999,
            "max_width": 640,
            "max_height": 360,
        },
        "images": {
            path.name: {
                "name": path.name,
                "found": True,
                "corner_count": 4,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "width": 100,
                "height": 80,
                "index_state": "ready",
            }
        },
    }
    calibrator.image_index_path.write_text(json.dumps(stale), encoding="utf-8")
    original_bytes = calibrator.image_index_path.read_bytes()

    with patch.object(
        calibrator,
        "detect_corners",
        side_effect=AssertionError("status should not migrate stale detector evidence"),
    ):
        status = calibrator.status()

    assert status["images"][0]["index_state"] == "pending"
    assert status["images"][0]["found"] is None
    assert calibrator.image_index_path.read_bytes() == original_bytes


def test_legacy_stat_only_index_remains_read_only_but_requires_digest_indexing(
    tmp_path: Path,
) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    path = calibrator.image_dir / "legacy-index.jpg"
    assert cv2.imwrite(str(path), np.zeros((80, 100, 3), dtype=np.uint8))
    stat = path.stat()
    legacy = {
        "schema_version": 2,
        "pattern": {"columns": 2, "rows": 2},
        "images": {
            path.name: {
                "name": path.name,
                "found": True,
                "corner_count": 4,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "width": 100,
                "height": 80,
                "quality": {},
            }
        },
    }
    calibrator.image_index_path.write_text(json.dumps(legacy), encoding="utf-8")
    original_bytes = calibrator.image_index_path.read_bytes()

    with patch.object(
        calibrator,
        "detect_corners",
        side_effect=AssertionError("compatible legacy evidence was rescanned"),
    ):
        status = calibrator.status((100, 80))

    assert status["index"]["state"] == "pending"
    assert status["usable_image_count"] == 0
    assert status["images"][0]["name"] == path.name
    assert calibrator.image_index_path.read_bytes() == original_bytes


@pytest.mark.parametrize(
    ("field_path", "bad_value"),
    [
        (("width",), "not-an-int"),
        (("found",), 1),
        (("corner_count",), True),
        (("quality",), 7),
        (("quality", "sharpness"), float("nan")),
        (("quality", "nested"), {"value": 7}),
        (("quality", "width"), 10),
        (("quality", "contrast_span"), 1.0),
        (("detector", "working_width"), False),
        (("board_center",), [0.5, float("inf")]),
        (("board_coverage_percent",), -1.0),
    ],
)
def test_semantically_invalid_advisory_entry_degrades_to_pending_without_rewrite(
    tmp_path: Path,
    field_path: tuple[str, ...],
    bad_value: object,
) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    path = calibrator.image_dir / "evidence.PNG"
    assert cv2.imwrite(str(path), np.zeros((80, 100, 3), dtype=np.uint8))
    corners = np.asarray(
        [[[20, 20]], [[80, 20]], [[20, 60]], [[80, 60]]],
        dtype=np.float32,
    )
    with patch.object(calibrator, "detect_corners", return_value=(True, corners)):
        calibrator.index_pending_captures()
    raw = json.loads(calibrator.image_index_path.read_text(encoding="utf-8"))
    target = raw["images"][path.name]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = bad_value
    calibrator.image_index_path.write_text(json.dumps(raw), encoding="utf-8")
    original_bytes = calibrator.image_index_path.read_bytes()

    status = calibrator.status((100, 80))

    assert status["images"][0]["name"] == path.name
    assert status["images"][0]["index_state"] == "pending"
    assert status["images"][0]["found"] is None
    assert calibrator.image_index_path.read_bytes() == original_bytes


def test_cached_name_is_never_used_as_a_capture_path(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    path = calibrator.image_dir / "actual.png"
    outside = tmp_path / "outside.png"
    assert cv2.imwrite(str(path), np.zeros((80, 100, 3), dtype=np.uint8))
    assert cv2.imwrite(str(outside), np.zeros((8, 8, 3), dtype=np.uint8))
    with patch.object(calibrator, "detect_corners", return_value=(False, None)):
        calibrator.index_pending_captures()
    raw = json.loads(calibrator.image_index_path.read_text(encoding="utf-8"))
    raw["images"][path.name]["name"] = "../outside.png"
    calibrator.image_index_path.write_text(json.dumps(raw), encoding="utf-8")

    item = calibrator.status()["images"][0]

    assert item["name"] == path.name
    assert item["index_state"] == "pending"
    assert calibrator.delete_capture(item["name"]) is True
    assert outside.exists()


def test_mixed_case_extensions_are_discovered_with_deterministic_png_preference(
    tmp_path: Path,
) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    for name, value in (("alpha.JPG", 10), ("alpha.PnG", 20), ("beta.JpEg", 30)):
        assert cv2.imwrite(
            str(calibrator.image_dir / name),
            np.full((20, 20, 3), value, dtype=np.uint8),
        )

    assert [path.name for path in calibrator._image_paths()] == [
        "alpha.PnG",
        "beta.JpEg",
    ]


def test_undecodable_file_commits_a_stable_error_instead_of_reindexing_forever(
    tmp_path: Path,
) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    path = calibrator.image_dir / "broken.PNG"
    path.write_bytes(b"not an image")

    assert calibrator.status()["images"][0]["index_state"] == "pending"
    result = calibrator.index_pending_captures()
    first = calibrator.status()["images"][0]
    second = calibrator.status()["images"][0]

    assert result["error_count"] == 1
    assert first["index_state"] == "error"
    assert second["index_state"] == "error"
    assert first["width"] == first["height"] == 0
    assert first["index_error"]


def test_capture_and_index_sharpness_share_bounded_measurement_dimensions(
    tmp_path: Path,
) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[:, ::16] = 255
    with patch.object(calibrator, "detect_corners", return_value=(False, None)):
        captured = calibrator.capture(image)
        legacy = calibrator.image_dir / "legacy.JPG"
        assert cv2.imwrite(str(legacy), image)
        calibrator.index_pending_captures()

    qualities = [item["quality"] for item in calibrator.status()["images"]]
    assert captured["quality"]["width"] == 640
    assert captured["quality"]["height"] == 360
    assert {(quality["width"], quality["height"]) for quality in qualities} == {
        (640, 360)
    }
    assert all(
        quality["measurement_source"] == "exact-encoded-byte-bounded-preview"
        for quality in qualities
    )
    assert all("variance-of-laplacian" in quality["sharpness_method"] for quality in qualities)


def test_fresh_png_metrics_equal_immediate_force_reindex_metrics(tmp_path: Path) -> None:
    calibrator = LensCalibrator(tmp_path, LensCalibrationSettings(columns=2, rows=2))
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[:, ::12] = 255
    corners = np.asarray(
        [[[90, 60]], [[550, 60]], [[90, 300]], [[550, 300]]],
        dtype=np.float32,
    )
    with patch.object(calibrator, "detect_corners", return_value=(True, corners)):
        captured = calibrator.capture(image)
        before = calibrator.status()["images"][0]
        result = calibrator.reindex_all_captures()
        after = calibrator.status()["images"][0]

    assert result["indexed_count"] == 1
    for key in (
        "found",
        "corner_count",
        "quality",
        "board_coverage_percent",
        "board_center",
        "detector",
    ):
        assert captured[key] == before[key] == after[key]
