from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pytest

import laser_aligner.calibration.lens as lens_module
import laser_aligner.imaging as imaging_module
from laser_aligner.app import AppContext
from laser_aligner.calibration.bed import BedPoint
from laser_aligner.calibration.lens import LensCalibrator, LensModel
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.service import FrameBurst
from laser_aligner.config import LensCalibrationSettings, load_settings
from laser_aligner.errors import CalibrationError


def _model(*, focal_length: float, marker: str) -> LensModel:
    return LensModel(
        camera_matrix=np.asarray(
            [[focal_length, 0.0, 80.0], [0.0, focal_length, 60.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        ),
        distortion=np.zeros((1, 5), dtype=np.float64),
        image_width=160,
        image_height=120,
        rms_error=0.2,
        mean_reprojection_error=0.15,
        images_used=1,
        created_at=focal_length,
        quality={"gate": "pass", "marker": marker},
    )


def _calibrator_with_capture(tmp_path: Path) -> tuple[LensCalibrator, str]:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    corners = np.asarray(
        [[[20, 20]], [[120, 20]], [[20, 90]], [[120, 90]]],
        dtype=np.float32,
    )
    with patch.object(calibrator, "detect_corners", return_value=(True, corners)):
        capture = calibrator.capture(np.zeros((120, 160, 3), dtype=np.uint8))
    return calibrator, str(capture["name"])


def _large_model(width: int, height: int) -> LensModel:
    focal = float(max(width, height))
    return LensModel(
        camera_matrix=np.asarray(
            [
                [focal, 0.0, width / 2.0],
                [0.0, focal, height / 2.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        ),
        distortion=np.zeros((1, 5), dtype=np.float64),
        image_width=width,
        image_height=height,
        rms_error=0.2,
        mean_reprojection_error=0.15,
        images_used=1,
        created_at=1.0,
        quality={"gate": "pass"},
    )


def _mutate_bytes_preserving_stat(path: Path) -> None:
    before = path.stat()
    payload = bytearray(path.read_bytes())
    payload[len(payload) // 2] ^= 0x01
    path.write_bytes(payload)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


def _same_size_png_payloads() -> tuple[bytes, bytes]:
    first = np.zeros((120, 160, 3), dtype=np.uint8)
    first[:, ::8] = 255
    second = np.full((120, 160, 3), 180, dtype=np.uint8)
    second[::8, :] = 0
    ok_first, encoded_first = cv2.imencode(".png", first)
    ok_second, encoded_second = cv2.imencode(".png", second)
    assert ok_first and ok_second
    first_bytes = encoded_first.tobytes()
    second_bytes = encoded_second.tobytes()
    target_size = max(len(first_bytes), len(second_bytes))
    return (
        first_bytes.ljust(target_size, b"\0"),
        second_bytes.ljust(target_size, b"\0"),
    )


def _replace_payload_preserving_mtime(
    path: Path,
    payload: bytes,
    *,
    mtime_ns: int,
) -> None:
    before = path.stat()
    path.write_bytes(payload)
    os.utime(path, ns=(before.st_atime_ns, mtime_ns))
    assert path.stat().st_size == len(payload)
    assert path.stat().st_mtime_ns == mtime_ns


def test_cold_status_only_probes_headers_and_leaves_detection_pending(
    tmp_path: Path,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    path = calibrator.image_dir / "legacy-1920x1080.jpg"
    assert cv2.imwrite(str(path), np.zeros((1080, 1920, 3), dtype=np.uint8))

    with (
        patch(
            "laser_aligner.calibration.lens.read_encoded_image_payload",
            side_effect=AssertionError("status read encoded image bodies"),
        ),
        patch(
            "laser_aligner.calibration.lens.decode_image_payload",
            side_effect=AssertionError("status decoded image pixels"),
        ),
        patch.object(
            calibrator,
            "detect_corners",
            side_effect=AssertionError("status ran checkerboard detection"),
        ),
    ):
        status = calibrator.status((1920, 1080))

    assert status["image_count"] == 1
    assert status["images"][0]["width"] == 1920
    assert status["images"][0]["height"] == 1080
    assert status["images"][0]["found"] is None
    assert status["index"]["state"] == "pending"
    assert status["index"]["pending_count"] == 1
    assert not calibrator.image_index_path.exists()


def test_bounded_index_preserves_exact_resolution_groups_and_detector_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    assert cv2.imwrite(
        str(calibrator.image_dir / "wide.jpg"),
        np.zeros((1080, 1920, 3), dtype=np.uint8),
    )
    assert cv2.imwrite(
        str(calibrator.image_dir / "four-three.jpg"),
        np.zeros((1200, 1600, 3), dtype=np.uint8),
    )
    detector_shapes: list[tuple[int, int]] = []

    def detect(image: np.ndarray) -> tuple[bool, np.ndarray]:
        height, width = image.shape[:2]
        detector_shapes.append((height, width))
        corners = np.asarray(
            [
                [[width * 0.2, height * 0.2]],
                [[width * 0.8, height * 0.2]],
                [[width * 0.2, height * 0.8]],
                [[width * 0.8, height * 0.8]],
            ],
            dtype=np.float32,
        )
        return True, corners

    monkeypatch.setattr(calibrator, "detect_corners", detect)
    result = calibrator.index_pending_captures()
    status = calibrator.status()

    assert result["indexed_count"] == 2
    assert len(detector_shapes) == 2
    assert all(height <= 360 and width <= 640 for height, width in detector_shapes)
    assert {
        (int(group["width"]), int(group["height"]))
        for group in status["resolution_groups"]
    } == {(1600, 1200), (1920, 1080)}
    assert status["index"]["state"] == "ready"
    assert all(
        int(item["detector"]["working_width"]) <= 640
        and int(item["detector"]["working_height"]) <= 360
        for item in status["images"]
    )


def test_bounded_index_rejects_same_stat_content_change_without_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    path = calibrator.image_dir / "same-stat.png"
    assert cv2.imwrite(str(path), np.zeros((120, 160, 3), dtype=np.uint8))
    detection_entered = threading.Event()
    release_detection = threading.Event()
    errors: list[BaseException] = []

    def detect(_image: np.ndarray) -> tuple[bool, None]:
        detection_entered.set()
        assert release_detection.wait(2.0)
        return False, None

    monkeypatch.setattr(calibrator, "detect_corners", detect)

    def index() -> None:
        try:
            calibrator.index_pending_captures()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=index, name="test-lens-same-stat-index")
    worker.start()
    assert detection_entered.wait(1.0)
    _mutate_bytes_preserving_stat(path)
    release_detection.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CalibrationError)
    assert "changed" in str(errors[0]).lower()
    assert not calibrator.image_index_path.exists()


def test_bounded_index_rejects_decode_aba_without_stale_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    path = calibrator.image_dir / "decode-aba.png"
    first_payload, second_payload = _same_size_png_payloads()
    path.write_bytes(first_payload)
    original_stat = path.stat()
    original_decode = imaging_module.cv2.imdecode
    attacked = False

    def attack(buffer: np.ndarray, flags: int) -> np.ndarray:
        nonlocal attacked
        if attacked:
            return original_decode(buffer, flags)
        attacked = True
        _replace_payload_preserving_mtime(
            path,
            second_payload,
            mtime_ns=original_stat.st_mtime_ns,
        )
        decoded = original_decode(buffer, flags)
        _replace_payload_preserving_mtime(
            path,
            first_payload,
            mtime_ns=original_stat.st_mtime_ns,
        )
        return decoded

    monkeypatch.setattr(imaging_module.cv2, "imdecode", attack)

    with pytest.raises(CalibrationError, match="changed"):
        calibrator.index_pending_captures()

    assert attacked
    assert path.read_bytes() == first_payload
    assert not calibrator.image_index_path.exists()


def test_full_solve_rechecks_original_pixels_after_bounded_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    assert cv2.imwrite(
        str(calibrator.image_dir / "full-resolution.jpg"),
        np.zeros((1080, 1920, 3), dtype=np.uint8),
    )
    corners = np.asarray(
        [[[200, 200]], [[1500, 200]], [[200, 800]], [[1500, 800]]],
        dtype=np.float32,
    )
    preview_corners = np.asarray(
        [[[80, 70]], [[540, 70]], [[80, 290]], [[540, 290]]],
        dtype=np.float32,
    )
    with patch.object(
        calibrator,
        "detect_corners",
        return_value=(True, preview_corners),
    ):
        calibrator.index_pending_captures()

    solve_detector_shapes: list[tuple[int, int]] = []

    def detect_full(image: np.ndarray) -> tuple[bool, np.ndarray]:
        solve_detector_shapes.append(tuple(image.shape[:2]))
        return True, corners

    monkeypatch.setattr(calibrator, "detect_corners", detect_full)
    monkeypatch.setattr(
        calibrator,
        "_solve_observations",
        lambda _observations, _size: _large_model(1920, 1080),
    )

    model = calibrator.solve((1920, 1080))

    assert model.image_size == (1920, 1080)
    assert solve_detector_shapes == [(1080, 1920)]
    solved_group = model.quality["resolution_groups"][0]
    assert solved_group["preview_usable_image_count"] == 1
    assert solved_group["full_resolution_usable_image_count"] == 1


def test_index_progress_is_coherent_and_conflicting_mutations_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    for name, shape in (("a.png", (480, 640)), ("b.png", (240, 320))):
        assert cv2.imwrite(
            str(calibrator.image_dir / name),
            np.zeros((*shape, 3), dtype=np.uint8),
        )
    second_detection_entered = threading.Event()
    release_detection = threading.Event()
    calls = 0

    def detect(_image: np.ndarray) -> tuple[bool, None]:
        nonlocal calls
        calls += 1
        if calls == 2:
            second_detection_entered.set()
            assert release_detection.wait(2.0)
        return False, None

    monkeypatch.setattr(calibrator, "detect_corners", detect)
    results: list[dict[str, object]] = []
    errors: list[BaseException] = []

    def index() -> None:
        try:
            results.append(calibrator.index_pending_captures())
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=index, name="test-lens-index")
    worker.start()
    assert second_detection_entered.wait(1.0)

    status_result: list[dict[str, object]] = []
    status_done = threading.Event()

    def read_status() -> None:
        status_result.append(calibrator.status((640, 480)))
        status_done.set()

    status_worker = threading.Thread(target=read_status, name="test-lens-index-status")
    status_worker.start()
    assert status_done.wait(0.5), "status blocked behind bounded checkerboard detection"
    interim = status_result[0]
    assert interim["busy"] is True
    assert interim["active_operation"] == "index lens evidence"
    assert interim["index"]["completed_count"] == 1
    assert interim["index"]["run_total_count"] == 2
    assert interim["index"]["current_name"] == "b.png"
    assert interim["resolution_groups"] == [
        {
            "width": 320,
            "height": 240,
            "image_count": 1,
            "usable_image_count": 0,
            "pending_image_count": 1,
            "error_image_count": 0,
            "selected": False,
        },
        {
            "width": 640,
            "height": 480,
            "image_count": 1,
            "usable_image_count": 0,
            "pending_image_count": 1,
            "error_image_count": 0,
            "selected": True,
        }
    ]
    for operation in (
        lambda: calibrator.capture(np.zeros((480, 640, 3), dtype=np.uint8)),
        lambda: calibrator.delete_capture("a.png"),
        calibrator.clear_captures,
        lambda: calibrator.solve((640, 480)),
    ):
        with pytest.raises(CalibrationError, match="index lens evidence is in progress"):
            operation()
    assert calibrator.status()["active_operation"] == "index lens evidence"

    release_detection.set()
    worker.join(2.0)
    status_worker.join(2.0)
    assert not worker.is_alive()
    assert not status_worker.is_alive()
    assert not errors
    assert results[0]["indexed_count"] == 2
    final = calibrator.status((640, 480))
    assert final["busy"] is False
    assert final["index"]["ready_count"] == 2
    assert {
        (int(group["width"]), int(group["height"]))
        for group in final["resolution_groups"]
    } == {(320, 240), (640, 480)}


def test_external_evidence_mutation_aborts_index_without_stale_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    assert cv2.imwrite(
        str(calibrator.image_dir / "original.png"),
        np.zeros((480, 640, 3), dtype=np.uint8),
    )
    detection_entered = threading.Event()
    release_detection = threading.Event()
    errors: list[BaseException] = []

    def detect(_image: np.ndarray) -> tuple[bool, None]:
        detection_entered.set()
        assert release_detection.wait(2.0)
        return False, None

    monkeypatch.setattr(calibrator, "detect_corners", detect)

    def index() -> None:
        try:
            calibrator.index_pending_captures()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=index, name="test-lens-index-mutation")
    worker.start()
    assert detection_entered.wait(1.0)
    assert cv2.imwrite(
        str(calibrator.image_dir / "externally-added.png"),
        np.zeros((480, 640, 3), dtype=np.uint8),
    )
    release_detection.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CalibrationError)
    assert "evidence changed" in str(errors[0]).lower()
    assert not calibrator.image_index_path.exists()
    status = calibrator.status((640, 480))
    assert status["index"]["pending_count"] == 2
    assert status["index"]["ready_count"] == 0


def test_status_stays_responsive_and_model_commit_is_atomic_during_solve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator, capture_name = _calibrator_with_capture(tmp_path)
    old_model = _model(focal_length=300.0, marker="old")
    replacement = _model(focal_length=340.0, marker="new")
    calibrator.save_model(old_model)
    with calibrator._state_lock:
        calibrator._last_solve_quality = {"gate": "pass", "marker": "old"}

    solve_entered = threading.Event()
    release_solve = threading.Event()
    solved: list[LensModel] = []
    errors: list[BaseException] = []

    def fake_solve(_observations: object, _image_size: object) -> LensModel:
        calibrator._record_solve_quality({"gate": "warning", "marker": "pending"})
        solve_entered.set()
        assert release_solve.wait(2.0)
        return replacement

    monkeypatch.setattr(
        calibrator,
        "_collect_observations",
        lambda _size, *, payloads=None: [],
    )
    monkeypatch.setattr(calibrator, "_solve_observations", fake_solve)

    def run_solve() -> None:
        try:
            solved.append(calibrator.solve((160, 120)))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=run_solve, name="test-lens-solve")
    worker.start()
    assert solve_entered.wait(1.0)

    status_result: list[dict[str, object]] = []
    status_done = threading.Event()

    def read_status() -> None:
        status_result.append(calibrator.status((160, 120)))
        status_done.set()

    status_worker = threading.Thread(target=read_status, name="test-lens-status")
    status_worker.start()
    assert status_done.wait(0.5), "status blocked behind the OpenCV solve"
    interim = status_result[0]
    assert interim["busy"] is True
    assert interim["active_operation"] == "solve lens calibration"
    assert interim["model"]["model_id"] == old_model.model_id  # type: ignore[index]
    assert interim["last_solve_quality"] == {"gate": "pass", "marker": "old"}

    with pytest.raises(CalibrationError, match="solve lens calibration is in progress"):
        calibrator.delete_capture(capture_name)
    with pytest.raises(CalibrationError, match="solve lens calibration is in progress"):
        calibrator.clear_captures()
    with pytest.raises(CalibrationError, match="solve lens calibration is in progress"):
        calibrator.capture(np.zeros((120, 160, 3), dtype=np.uint8))
    with pytest.raises(CalibrationError, match="solve lens calibration is in progress"):
        calibrator.solve((160, 120))
    still_solving = calibrator.status()
    assert still_solving["active_operation"] == "solve lens calibration"
    assert still_solving["last_solve_quality"] == {"gate": "pass", "marker": "old"}

    release_solve.set()
    worker.join(2.0)
    status_worker.join(2.0)
    assert not worker.is_alive()
    assert not errors
    assert solved == [replacement]
    final = calibrator.status((160, 120))
    assert final["busy"] is False
    assert final["model"]["model_id"] == replacement.model_id
    assert final["last_solve_quality"]["marker"] == "new"


def test_capture_reserves_evidence_while_corner_detection_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator, old_name = _calibrator_with_capture(tmp_path)
    detection_entered = threading.Event()
    release_detection = threading.Event()
    errors: list[BaseException] = []
    captures: list[dict[str, object]] = []
    corners = np.asarray(
        [[[18, 18]], [[125, 18]], [[18, 94]], [[125, 94]]],
        dtype=np.float32,
    )

    def detect(_image: np.ndarray) -> tuple[bool, np.ndarray]:
        detection_entered.set()
        assert release_detection.wait(2.0)
        return True, corners

    monkeypatch.setattr(calibrator, "detect_corners", detect)

    def run_capture() -> None:
        try:
            captures.append(calibrator.capture(np.full((120, 160, 3), 80, dtype=np.uint8)))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=run_capture, name="test-lens-capture")
    worker.start()
    assert detection_entered.wait(1.0)

    interim = calibrator.status((160, 120))
    assert interim["busy"] is True
    assert interim["active_operation"] == "capture lens evidence"
    assert interim["image_count"] == 1
    with pytest.raises(CalibrationError, match="capture lens evidence is in progress"):
        calibrator.delete_capture(old_name)
    with pytest.raises(CalibrationError, match="capture lens evidence is in progress"):
        calibrator.clear(delete_images=True)

    release_detection.set()
    worker.join(2.0)
    assert not worker.is_alive()
    assert not errors
    assert len(captures) == 1
    final = calibrator.status((160, 120))
    assert final["busy"] is False
    assert final["image_count"] == 2
    assert {item["name"] for item in final["images"]} == {
        old_name,
        captures[0]["name"],
    }


def test_status_stays_responsive_while_lossless_capture_is_encoded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator, _old_name = _calibrator_with_capture(tmp_path)
    encode_entered = threading.Event()
    release_encode = threading.Event()
    original_encode = lens_module.encode_image
    corners = np.asarray(
        [[[20, 20]], [[120, 20]], [[20, 90]], [[120, 90]]],
        dtype=np.float32,
    )

    def encode(
        image: np.ndarray,
        suffix: str,
        params: list[int] | None = None,
    ) -> bytes:
        encode_entered.set()
        assert release_encode.wait(2.0)
        return original_encode(image, suffix, params)

    monkeypatch.setattr(calibrator, "detect_corners", lambda _image: (True, corners))
    monkeypatch.setattr(lens_module, "encode_image", encode)
    errors: list[BaseException] = []

    def capture() -> None:
        try:
            calibrator.capture(np.full((120, 160, 3), 80, dtype=np.uint8))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    worker = threading.Thread(target=capture, name="test-lens-encode")
    worker.start()
    assert encode_entered.wait(1.0)

    status_result: list[dict[str, object]] = []
    status_done = threading.Event()

    def read_status() -> None:
        status_result.append(calibrator.status((160, 120)))
        status_done.set()

    status_worker = threading.Thread(target=read_status, name="test-lens-encode-status")
    status_worker.start()
    assert status_done.wait(0.5), "status blocked behind lossless PNG encoding"
    assert status_result[0]["active_operation"] == "capture lens evidence"
    assert status_result[0]["image_count"] == 1

    release_encode.set()
    worker.join(2.0)
    status_worker.join(2.0)
    assert not worker.is_alive()
    assert not status_worker.is_alive()
    assert not errors
    assert calibrator.status((160, 120))["image_count"] == 2


def test_external_evidence_change_aborts_solve_without_replacing_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator, _capture_name = _calibrator_with_capture(tmp_path)
    old_model = _model(focal_length=300.0, marker="old")
    replacement = _model(focal_length=340.0, marker="new")
    calibrator.save_model(old_model)
    solve_entered = threading.Event()
    release_solve = threading.Event()
    errors: list[BaseException] = []

    def fake_solve(_observations: object, _image_size: object) -> LensModel:
        solve_entered.set()
        assert release_solve.wait(2.0)
        return replacement

    monkeypatch.setattr(
        calibrator,
        "_collect_observations",
        lambda _size, *, payloads=None: [],
    )
    monkeypatch.setattr(calibrator, "_solve_observations", fake_solve)

    def run_solve() -> None:
        try:
            calibrator.solve((160, 120))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_solve, name="test-lens-external-change")
    worker.start()
    assert solve_entered.wait(1.0)
    assert cv2.imwrite(
        str(calibrator.image_dir / "externally-added.png"),
        np.zeros((120, 160, 3), dtype=np.uint8),
    )
    release_solve.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CalibrationError)
    assert "evidence changed" in str(errors[0]).lower()
    assert calibrator.model is old_model
    persisted = LensModel.from_dict(calibrator.load_model().to_dict())  # type: ignore[union-attr]
    assert persisted.model_id == old_model.model_id


def test_same_stat_content_change_aborts_solve_without_replacing_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator, capture_name = _calibrator_with_capture(tmp_path)
    path = calibrator.image_dir / capture_name
    old_model = _model(focal_length=300.0, marker="old")
    replacement = _model(focal_length=340.0, marker="new")
    calibrator.save_model(old_model)
    solve_entered = threading.Event()
    release_solve = threading.Event()
    errors: list[BaseException] = []

    def fake_solve(_observations: object, _image_size: object) -> LensModel:
        solve_entered.set()
        assert release_solve.wait(2.0)
        return replacement

    monkeypatch.setattr(
        calibrator,
        "_collect_observations",
        lambda _size, *, payloads=None: [],
    )
    monkeypatch.setattr(calibrator, "_solve_observations", fake_solve)

    def run_solve() -> None:
        try:
            calibrator.solve((160, 120))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_solve, name="test-lens-same-stat-solve")
    worker.start()
    assert solve_entered.wait(1.0)
    _mutate_bytes_preserving_stat(path)
    release_solve.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CalibrationError)
    assert "changed" in str(errors[0]).lower()
    assert calibrator.model is old_model
    persisted = calibrator.load_model()
    assert persisted is not None
    assert persisted.model_id == old_model.model_id


def test_full_solve_rejects_decode_aba_without_replacing_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    path = calibrator.image_dir / "solve-aba.png"
    first_payload, second_payload = _same_size_png_payloads()
    path.write_bytes(first_payload)
    corners = np.asarray(
        [[[20, 20]], [[140, 20]], [[20, 100]], [[140, 100]]],
        dtype=np.float32,
    )
    with patch.object(calibrator, "detect_corners", return_value=(True, corners)):
        calibrator.index_pending_captures()
    old_model = _model(focal_length=300.0, marker="old")
    calibrator.save_model(old_model)
    original_stat = path.stat()
    original_decode = imaging_module.cv2.imdecode
    attacked = False

    def attack(buffer: np.ndarray, flags: int) -> np.ndarray:
        nonlocal attacked
        if attacked:
            return original_decode(buffer, flags)
        attacked = True
        _replace_payload_preserving_mtime(
            path,
            second_payload,
            mtime_ns=original_stat.st_mtime_ns,
        )
        decoded = original_decode(buffer, flags)
        _replace_payload_preserving_mtime(
            path,
            first_payload,
            mtime_ns=original_stat.st_mtime_ns,
        )
        return decoded

    monkeypatch.setattr(imaging_module.cv2, "imdecode", attack)
    monkeypatch.setattr(
        calibrator,
        "_solve_observations",
        lambda *_args: pytest.fail("ABA evidence reached model fitting"),
    )

    with pytest.raises(CalibrationError, match="changed"):
        calibrator.solve((160, 120))

    assert attacked
    assert path.read_bytes() == first_payload
    assert calibrator.model is old_model
    persisted = calibrator.load_model()
    assert persisted is not None
    assert persisted.model_id == old_model.model_id


def test_ready_same_stat_replacement_recovers_via_force_reindex_and_solve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calibrator = LensCalibrator(
        tmp_path,
        LensCalibrationSettings(columns=2, rows=2, minimum_images=1),
    )
    path = calibrator.image_dir / "replace-ready.png"
    first_payload, second_payload = _same_size_png_payloads()
    path.write_bytes(first_payload)
    corners = np.asarray(
        [[[20, 20]], [[140, 20]], [[20, 100]], [[140, 100]]],
        dtype=np.float32,
    )
    monkeypatch.setattr(calibrator, "detect_corners", lambda _image: (True, corners))
    calibrator.index_pending_captures()
    before = calibrator.status((160, 120))
    old_digest = str(before["images"][0]["content_sha256"])
    original_stat = path.stat()
    _replace_payload_preserving_mtime(
        path,
        second_payload,
        mtime_ns=original_stat.st_mtime_ns,
    )

    # Cold status intentionally remains advisory, but solve verifies exact bytes.
    assert calibrator.status((160, 120))["index"]["state"] == "ready"
    with pytest.raises(CalibrationError, match="changed since its bounded index"):
        calibrator.solve((160, 120))

    result = calibrator.reindex_all_captures()
    recovered = calibrator.status((160, 120))
    assert result["indexed_count"] == 1
    assert recovered["index"]["state"] == "ready"
    assert recovered["usable_image_count"] == 1
    assert recovered["images"][0]["content_sha256"] != old_digest

    monkeypatch.setattr(
        calibrator,
        "_solve_observations",
        lambda observations, _size: (
            _large_model(160, 120)
            if len(observations) == 1
            else pytest.fail("Recovered evidence was not used")
        ),
    )
    solved = calibrator.solve((160, 120))
    assert solved.image_size == (160, 120)


def test_app_context_uses_one_lens_snapshot_for_a_complete_burst() -> None:
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    burst = FrameBurst(
        frames=(frame.copy(), frame.copy(), frame.copy()),
        sequence_numbers=(1, 2, 3),
        discarded_frames=0,
        settle_seconds=0.0,
        elapsed_seconds=0.1,
        sharpness_scores=(0.0, 0.0, 0.0),
        controls=ControlResult({}, {}, {}),
    )
    replacement = SimpleNamespace(calls=0)
    lens_holder = SimpleNamespace(model=None)

    class InitialModel:
        def __init__(self) -> None:
            self.calls = 0

        def undistort(self, image: np.ndarray) -> np.ndarray:
            self.calls += 1
            lens_holder.model = replacement
            return image + self.calls

    initial = InitialModel()
    lens_holder.model = initial
    harness = SimpleNamespace(
        lens=lens_holder,
        camera=SimpleNamespace(_sharpness_score=lambda image: float(np.mean(image))),
    )

    result = AppContext._prepare_camera_burst(harness, burst, undistort=True)

    assert initial.calls == 3
    assert replacement.calls == 0
    assert [int(image[0, 0, 0]) for image in result.frames] == [1, 2, 3]


def test_app_context_serializes_camera_capture_and_lens_solve_workflows() -> None:
    capture_camera_entered = threading.Event()
    release_capture_camera = threading.Event()
    solve_attempted = threading.Event()
    solve_called = threading.Event()
    errors: list[BaseException] = []
    results: list[object] = []
    replacement = _model(focal_length=340.0, marker="new")

    def stable_frame(*, undistort: bool) -> tuple[np.ndarray, dict[str, object]]:
        assert undistort is False
        capture_camera_entered.set()
        assert release_capture_camera.wait(2.0)
        return np.zeros((120, 160, 3), dtype=np.uint8), {"sample_frames": 1}

    lens = SimpleNamespace(
        capture=lambda _image: {"name": "capture.png", "found": True},
        solve=lambda *, image_size: solve_called.set() or replacement,
    )
    harness = SimpleNamespace(
        _lens_workflow_lock=threading.RLock(),
        _workspace_lock=threading.RLock(),
        _workspace_image=np.ones((1, 1, 3), dtype=np.uint8),
        _workspace_revision=(1,),
        _composed_map_cache={"old": object()},
        _require_camera_calibration_ready=lambda: None,
        stable_camera_frame=stable_frame,
        camera=SimpleNamespace(status=lambda: SimpleNamespace(width=160, height=120)),
        lens=lens,
    )

    def capture() -> None:
        try:
            results.append(AppContext.capture_lens_calibration(harness))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def solve() -> None:
        solve_attempted.set()
        try:
            results.append(AppContext.solve_lens_calibration(harness))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    capture_worker = threading.Thread(target=capture, name="test-app-lens-capture")
    solve_worker = threading.Thread(target=solve, name="test-app-lens-solve")
    capture_worker.start()
    assert capture_camera_entered.wait(1.0)
    solve_worker.start()
    assert solve_attempted.wait(1.0)
    assert not solve_called.wait(0.1), "solve overlapped the camera stage of lens capture"

    release_capture_camera.set()
    capture_worker.join(2.0)
    solve_worker.join(2.0)
    assert not capture_worker.is_alive()
    assert not solve_worker.is_alive()
    assert not errors
    assert solve_called.is_set()
    assert len(results) == 2
    assert harness._workspace_image is None
    assert harness._workspace_revision is None
    assert harness._composed_map_cache == {}


def test_app_status_binds_bed_validity_to_its_lens_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {
                    "data_dir": "data",
                    "simulation": True,
                    "open_browser": False,
                },
                "camera": {"width": 160, "height": 120, "autostart": False},
                "machine": {"backend": "simulator"},
            }
        ),
        encoding="utf-8",
    )
    context = AppContext(load_settings(config_path))
    old_model = _model(focal_length=300.0, marker="old")
    replacement = _model(focal_length=340.0, marker="new")
    context.lens.save_model(old_model)
    for image_x, image_y, machine_x, machine_y in (
        (0.0, 0.0, 0.0, 220.0),
        (160.0, 0.0, 220.0, 220.0),
        (160.0, 120.0, 220.0, 0.0),
        (0.0, 120.0, 0.0, 0.0),
    ):
        context.bed.add_point(BedPoint(image_x, image_y, machine_x, machine_y))
    context.bed.solve(160, 120, provenance=context._bed_provenance())
    original_status = context.lens.status

    def replace_after_snapshot(*args: object, **kwargs: object) -> dict[str, object]:
        snapshot = original_status(*args, **kwargs)
        context.lens.save_model(replacement)
        return snapshot

    monkeypatch.setattr(context.lens, "status", replace_after_snapshot)

    status = context.status()

    assert status["lens"]["model"]["model_id"] == old_model.model_id
    assert status["bed"]["validity"]["state"] == "VALID"
    assert context.bed_calibration_validity()["state"] == "STALE"
