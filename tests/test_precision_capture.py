from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.service import CameraService, FrameBurst
from laser_aligner.config import CameraSettings, PrecisionCaptureSettings
from laser_aligner.errors import CameraError
from laser_aligner.vision import fiducials


def _profile(**overrides: object) -> PrecisionCaptureSettings:
    values = {
        "settle_seconds": 0.0,
        "discard_frames": 2,
        "sample_frames": 3,
        "timeout_seconds": 1.0,
        "minimum_valid_frames": 2,
        "mad_multiplier": 3.5,
        "outlier_floor_px": 0.25,
        "max_jitter_rms_px": 0.75,
    }
    values.update(overrides)
    return PrecisionCaptureSettings(**values)


def test_camera_burst_waits_for_distinct_fresh_frames() -> None:
    service = CameraService(CameraSettings(precision_capture=_profile()))
    with service._frame_condition:
        service._connected = True
        service._frame = np.zeros((8, 8, 3), dtype=np.uint8)

    def produce() -> None:
        time.sleep(0.02)
        for value in range(1, 7):
            with service._frame_condition:
                service._frame = np.full((8, 8, 3), value, dtype=np.uint8)
                service._frames_read += 1
                service._frame_condition.notify_all()
            time.sleep(0.02)

    producer = threading.Thread(target=produce)
    producer.start()
    burst = service.capture_burst(reapply_controls=False)
    producer.join(timeout=1.0)

    assert burst.discarded_frames == 2
    assert len(burst.frames) == 3
    assert list(burst.sequence_numbers) == sorted(set(burst.sequence_numbers))
    assert [int(frame[0, 0, 0]) for frame in burst.frames] == [3, 4, 5]


def test_camera_burst_times_out_instead_of_reusing_a_stale_frame() -> None:
    service = CameraService(
        CameraSettings(
            precision_capture=_profile(
                discard_frames=0,
                sample_frames=1,
                minimum_valid_frames=1,
                timeout_seconds=0.02,
            )
        )
    )
    with service._frame_condition:
        service._connected = True
        service._frame = np.zeros((4, 4, 3), dtype=np.uint8)

    with pytest.raises(CameraError, match="fresh camera frames"):
        service.capture_burst(reapply_controls=False)


@pytest.mark.parametrize(
    ("capture_method", "analysis_method"),
    [
        ("capture_fine_registration", "analyze_fine_registration_burst"),
        ("capture_accuracy_validation", "analyze_accuracy_validation_burst"),
    ],
)
def test_registration_capture_homes_before_burst_and_can_skip_rehoming(
    capture_method: str,
    analysis_method: str,
) -> None:
    frame = np.full((4, 4, 3), 50, dtype=np.uint8)
    burst = FrameBurst(
        frames=(frame,),
        sequence_numbers=(7,),
        discarded_frames=0,
        settle_seconds=0.0,
        elapsed_seconds=0.1,
        sharpness_scores=(1.0,),
        controls=ControlResult({}, {}, {}),
    )
    calls: list[str] = []
    harness = SimpleNamespace(
        machine=SimpleNamespace(
            prepare_photo_position=lambda: calls.append("home")
        ),
        precision_camera_burst=lambda undistort=True: (
            calls.append(f"burst:{undistort}") or burst
        ),
    )
    setattr(
        harness,
        analysis_method,
        lambda value: calls.append("analyze") or {"burst": value},
    )

    image, payload = getattr(AppContext, capture_method)(harness, home_first=True)
    assert calls == ["home", "burst:True", "analyze"]
    assert image is not frame
    assert payload["burst"] is burst

    calls.clear()
    getattr(AppContext, capture_method)(harness, home_first=False)
    assert calls == ["burst:True", "analyze"]


def test_crosshair_burst_rejects_one_spatial_outlier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offsets = [
        -0.08,
        0.02,
        0.04,
        -0.03,
        0.01,
        -0.05,
        0.06,
        -0.02,
        0.03,
        -0.01,
        0.05,
        -0.04,
        0.00,
        0.02,
        4.0,
    ]
    images = [np.full((2, 2), index, dtype=np.uint8) for index in range(15)]
    expected = [
        {
            "id": 1,
            "image_x": 100.0,
            "image_y": 80.0,
            "machine_x": 25.0,
            "machine_y": 20.0,
        }
    ]

    def fake_detect(
        image: np.ndarray,
        expected_points: list[dict[str, object]],
        search_radius_px: int = 55,
    ) -> dict[str, object]:
        del expected_points, search_radius_px
        offset = offsets[int(image[0, 0])]
        return {
            "detected": True,
            "confidence": "high",
            "points": [
                {
                    "id": 1,
                    "image_x": 100.0 + offset,
                    "image_y": 80.0 - offset * 0.25,
                    "machine_x": 25.0,
                    "machine_y": 20.0,
                    "score": 100.0,
                    "shift_px": abs(offset),
                }
            ],
        }

    monkeypatch.setattr(fiducials, "detect_crosshairs_near", fake_detect)
    result = fiducials.detect_crosshairs_burst(
        images,
        expected,
        minimum_valid_frames=9,
        max_jitter_rms_px=0.75,
    )

    assert result["detected"] is True
    point = result["points"][0]
    assert point["inlier_count"] == 14
    assert point["outlier_count"] == 1
    assert point["image_x"] == pytest.approx(100.0, abs=0.03)
    assert result["capture_diagnostics"]["rejected_frame_count"] == 1


def test_crosshair_burst_rejects_consistently_jittery_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = [np.full((2, 2), index, dtype=np.uint8) for index in range(12)]
    expected = [
        {
            "id": 1,
            "image_x": 100.0,
            "image_y": 80.0,
            "machine_x": 25.0,
            "machine_y": 20.0,
        }
    ]

    def fake_detect(
        image: np.ndarray,
        expected_points: list[dict[str, object]],
        search_radius_px: int = 55,
    ) -> dict[str, object]:
        del expected_points, search_radius_px
        offset = -1.0 if int(image[0, 0]) % 2 else 1.0
        return {
            "detected": True,
            "confidence": "high",
            "points": [
                {
                    "id": 1,
                    "image_x": 100.0 + offset,
                    "image_y": 80.0,
                    "machine_x": 25.0,
                    "machine_y": 20.0,
                    "score": 100.0,
                    "shift_px": 1.0,
                }
            ],
        }

    monkeypatch.setattr(fiducials, "detect_crosshairs_near", fake_detect)
    result = fiducials.detect_crosshairs_burst(
        images,
        expected,
        minimum_valid_frames=9,
        max_jitter_rms_px=0.5,
    )

    assert result["detected"] is False
    assert "jitter" in result["reason"]
