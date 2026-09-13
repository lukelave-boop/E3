"""Only current corrected camera captures supply probe-selection evidence."""

from __future__ import annotations

import os
from dataclasses import asdict
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtGui, QtWidgets

from laser_aligner.config import CameraSettings, WorkArea
from laser_aligner.desktop import controller as controller_module
from laser_aligner.desktop.controller import DesktopController


@pytest.fixture
def capture(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    camera_settings = CameraSettings(device="test-camera", width=640, height=480)
    camera_status = SimpleNamespace(
        connected=True, last_error=None, frame_age_seconds=0.25, width=640, height=480,
    )
    area = WorkArea(10., 110., 20., 80.)
    now = [100.]
    settings = SimpleNamespace(
        camera=camera_settings,
        calibration=SimpleNamespace(bed=SimpleNamespace(pixels_per_mm=2.)),
        machine=SimpleNamespace(work_area=area),
    )
    context = SimpleNamespace(
        camera=SimpleNamespace(settings=camera_settings, status=lambda: camera_status),
        lens=SimpleNamespace(model=SimpleNamespace(model_id="lens-1")),
        bed=SimpleNamespace(calibration=object()),
        bed_calibration_validity=lambda: {"state": "VALID", "reasons": []},
        bed_mapping_digest=lambda: "mapping-1",
        trace_camera_work_area=lambda: area,
        current_honeycomb_coordinate_frame=lambda: None,
    )
    runtime = SimpleNamespace(running=True, context=context, settings=settings)
    controller = DesktopController(runtime)
    calls, delivered, launches = [], [], []

    def rectify(**kwargs):
        calls.append(kwargs)
        now[0] = 101.
        camera_status.frame_age_seconds = 0.05
        return np.zeros((120, 200, 3), dtype=np.uint8)

    context.rectified_frame = rectify
    monkeypatch.setattr(controller_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(controller, "_run", lambda operation, **kwargs: launches.append((operation, kwargs)))
    controller.cameraImageReady.connect(delivered.append)
    fixture = SimpleNamespace(
        app=app, controller=controller, context=context, settings=settings, status=camera_status,
        now=now, calls=calls, delivered=delivered, launches=launches, area=area,
    )
    yield fixture
    controller._camera_live_timer.stop()
    controller.deleteLater()
    app.processEvents()


def prepare(capture):
    capture.controller.refresh_camera_image()
    assert len(capture.launches) == 1
    operation, callbacks = capture.launches[0]
    return operation(), callbacks


def deliver(capture):
    image, callbacks = prepare(capture)
    callbacks["on_success"](image)
    assert len(capture.delivered) == 1
    return capture.delivered[0]


def test_capture_metadata_uses_pre_capture_age_and_immutable_mapping(capture):
    payload = deliver(capture)
    metadata = payload["focus_frame_metadata"]
    assert isinstance(payload["image"], QtGui.QImage)
    assert capture.calls == [{"refresh": True, "work_area": capture.area}]
    assert metadata == {
        "width": 640, "height": 480, "source_width": 640, "source_height": 480,
        "source_mode": None, "camera_settings": asdict(capture.settings.camera),
        "review_signature": ("machine", None, "mapping-1"),
        "source_generation": 0, "lens_model_id": "lens-1", "pixels_per_mm": 2.,
        "camera_image_area": {"x_min": 10., "x_max": 110., "y_min": 20., "y_max": 80.},
        "received_monotonic": 100., "frame_age_seconds": 0.25,
        "corrected_width": 200, "corrected_height": 120,
    }
    capture.settings.camera.controls["focus_absolute"] = 20
    assert metadata["camera_settings"]["controls"] == {}


@pytest.mark.parametrize("age", [None, -0.1, 2.01, float("inf"), float("nan"), True, "0.1"])
def test_unknown_or_stale_pre_capture_age_cannot_gain_authority_from_new_status(capture, age):
    capture.status.frame_age_seconds = age
    assert "focus_frame_metadata" not in deliver(capture)


@pytest.mark.parametrize("change", ["settings", "dimensions", "mapping", "lens", "ppm", "offline"])
def test_provenance_change_during_rectification_keeps_image_without_probe_authority(capture, change):
    original = capture.context.rectified_frame

    def rectify(**kwargs):
        result = original(**kwargs)
        mutate(capture, change)
        return result

    capture.context.rectified_frame = rectify
    assert "focus_frame_metadata" not in deliver(capture)


def mutate(capture, change):
    if change == "settings":
        capture.settings.camera.controls["focus_absolute"] = 20
    elif change == "dimensions":
        capture.status.width = 1280
    elif change == "mapping":
        capture.context.bed_mapping_digest = lambda: "mapping-2"
    elif change == "lens":
        capture.context.lens.model.model_id = "lens-2"
    elif change == "ppm":
        capture.settings.calibration.bed.pixels_per_mm = 3.
    elif change == "offline":
        capture.status.connected = False


@pytest.mark.parametrize("change", ["settings", "dimensions", "mapping", "lens", "ppm", "offline"])
def test_provenance_change_while_completion_is_queued_removes_probe_authority(capture, change):
    image, callbacks = prepare(capture)
    mutate(capture, change)
    callbacks["on_success"](image)
    assert len(capture.delivered) == 1
    assert "focus_frame_metadata" not in capture.delivered[0]


def test_source_generation_change_drops_queued_capture(capture):
    image, callbacks = prepare(capture)
    capture.controller._camera_source_generation += 1
    callbacks["on_success"](image)
    assert capture.delivered == []


@pytest.mark.parametrize("review", ["_trace_review_active", "_template_review_active"])
def test_review_prevents_pending_capture_publication(capture, review):
    image, callbacks = prepare(capture)
    setattr(capture.controller, review, True)
    callbacks["on_success"](image)
    assert capture.delivered == []


def test_calibration_review_removes_probe_evidence_from_queued_capture(capture):
    image, callbacks = prepare(capture)
    capture.controller._calibration_review_active = True
    callbacks["on_success"](image)
    assert len(capture.delivered) == 1
    assert "focus_frame_metadata" not in capture.delivered[0]


def test_plain_restored_or_review_image_has_no_probe_evidence(capture):
    image = QtGui.QImage(200, 120, QtGui.QImage.Format.Format_RGB888)
    capture.controller._publish_camera_image(image, image_area=capture.area)
    assert "focus_frame_metadata" not in capture.delivered[0]


def test_honeycomb_capture_binds_current_local_coordinate_frame(capture):
    frame = SimpleNamespace(width_mm=100., height_mm=60., provenance_signature=("frame", 1, "pose-1"))
    capture.context.current_honeycomb_coordinate_frame = lambda: frame
    capture.controller._workspace_coordinate_space = "honeycomb_local"
    payload = deliver(capture)
    metadata = payload["focus_frame_metadata"]
    assert metadata["review_signature"] == ("honeycomb_local", ("frame", 1, "pose-1"), "mapping-1")
    assert metadata["camera_image_area"] == {"x_min": 0., "x_max": 100., "y_min": 0., "y_max": 60.}
    assert capture.calls[0]["coordinate_frame"] is frame


def test_changed_honeycomb_frame_discards_queued_capture(capture):
    frame = SimpleNamespace(width_mm=100., height_mm=60., provenance_signature=("frame", 1, "pose-1"))
    capture.context.current_honeycomb_coordinate_frame = lambda: frame
    capture.controller._workspace_coordinate_space = "honeycomb_local"
    image, callbacks = prepare(capture)
    frame.provenance_signature = ("frame", 1, "pose-2")
    callbacks["on_success"](image)
    assert capture.delivered == []
