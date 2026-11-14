from __future__ import annotations

# ruff: noqa: E402
import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.config import CalibrationSettings, LaserSettings, MachineSettings, WorkArea
from laser_aligner.desktop import controller as controller_module
from laser_aligner.desktop.controller import (
    DesktopController,
    _trace_detection_area_polygon,
    _trace_roi_polygons,
)


@pytest.fixture
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize("area", [
    (), (0, 0, 1), (0, 0, 1, 1, 2), "0011", 7, {0: 0, 1: 0, 2: 1, 3: 1},
    (False, 0, 1, 1), (None, 0, 1, 1), (float("nan"), 0, 1, 1),
    (0, 0, float("inf"), 1), (0, 0, 0, 1), (0, 0, 1, 0),
    (2, 0, 1, 1), (0, 2, 1, 1), (-1e308, 0, 1e308, 1),
])
def test_detection_rectangle_rejects_invalid_or_collapsed_coordinates(area):
    with pytest.raises(ValueError, match="Detection area"):
        _trace_detection_area_polygon(area)


def _controller():
    settings = SimpleNamespace(
        machine=MachineSettings(work_area=WorkArea(0, 100, 0, 80)),
        calibration=CalibrationSettings(),
        laser=LaserSettings(),
    )
    source = np.zeros((160, 200, 3), np.uint8)
    captures = []

    def capture(**kwargs):
        captures.append(kwargs)
        return source

    context = SimpleNamespace(
        bed=SimpleNamespace(calibration=object()),
        bed_mapping_digest=lambda: "bed-map",
        trace_camera_work_area=lambda: settings.machine.work_area,
        precision_pixels_per_mm=lambda: 2.0,
        capture_parked_trace_frame=capture,
    )
    controller = DesktopController(SimpleNamespace(context=context, settings=settings, running=False))
    return controller, source, captures


@pytest.mark.parametrize("local_frame", [False, True])
def test_controller_intersects_selection_without_cropping_or_changing_output_authority(
    application, monkeypatch, local_frame,
):
    controller, source, captures = _controller()
    frame = None
    background = np.full_like(source, 80)
    if local_frame:
        frame = SimpleNamespace(
            width_mm=100.0, height_mm=80.0, provenance_digest="support",
            machine_to_local=lambda x, y: (y - 3.0, 100.0 - x),
            local_to_machine=lambda x, y: (100.0 - y, x + 3.0),
        )
        controller._workspace_coordinate_space = "honeycomb_local"
        controller.runtime.context.current_honeycomb_coordinate_frame = lambda: frame
        controller.runtime.context.honeycomb_trace_background = lambda **kwargs: background
        monkeypatch.setattr(controller, "_current_review_signature", lambda *_args: ("local",))
    observed = []

    def detect(image, options, area, ppm, **kwargs):
        observed.append((image, options, area, ppm, kwargs))
        kwargs["raster_preview_callback"](object())
        return SimpleNamespace(
            detections=[], message="No objects",
            to_dict=lambda: {"detections": [], "message": "No objects", "diagnostics": {}},
        )

    monkeypatch.setattr(controller_module, "detect_objects", detect)
    queued = []
    monkeypatch.setattr(controller, "_run", lambda operation, **kwargs: queued.append(operation))
    previews = []
    controller.traceRasterPreviewReady.connect(lambda _request_id, payload: previews.append(payload))
    area = [10.0, 12.0, 45.0, 60.0]
    controller.detect_trace_objects({"regular_grid": False}, detection_area_mm=area)
    area[0] = 90.0  # Queued work owns its request snapshot.
    payload = queued.pop()()
    trusted = _trace_roi_polygons(controller.runtime, frame)
    image, options, camera_area, ppm, kwargs = observed[0]
    assert image is source
    assert options.regular_grid is False
    assert ppm == 2.0
    assert camera_area == captures[0]["work_area"]
    assert (camera_area.width, camera_area.height) == (100.0, 80.0)
    assert kwargs["trace_roi_polygons_mm"] == (
        *trusted, ((10.0, 12.0), (45.0, 12.0), (45.0, 60.0), (10.0, 60.0)),
    )
    assert kwargs["trace_output_polygon_mm"] == trusted[-1]
    assert "selected detection area" in kwargs["trace_roi_source"]
    assert kwargs["reference_required"] is local_frame
    assert kwargs["background_image"] is (background if local_frame else None)
    assert payload["detection_area_mm"] == [10.0, 12.0, 45.0, 60.0]
    assert previews[0]["detection_area_mm"] == payload["detection_area_mm"]

    controller.detect_trace_objects({"regular_grid": False})
    cleared_payload = queued.pop()()
    assert observed[-1][4]["trace_roi_polygons_mm"] == trusted
    assert cleared_payload["detection_area_mm"] is None
    controller.deleteLater()


@pytest.mark.parametrize(("area", "grid", "message"), [
    ((0, 0, 0, 10), False, "positive width"),
    ((0, 0, 10, 10), True, "Turn off Use grid"),
])
def test_controller_rejects_invalid_area_before_capturing(application, monkeypatch, area, grid, message):
    controller, _source, captures = _controller()
    monkeypatch.setattr(controller, "_run", lambda operation, **_kwargs: operation())
    with pytest.raises(ValueError, match=message):
        controller.detect_trace_objects({"regular_grid": grid}, detection_area_mm=area)
    assert captures == []
    controller.deleteLater()
