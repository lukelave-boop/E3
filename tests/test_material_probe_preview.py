from dataclasses import asdict

import numpy as np
import pytest

from laser_aligner.camera.service import CameraStatus
from laser_aligner.errors import CalibrationError
from tests.test_material_workspace import active_surface as active_surface
from tests.test_material_workspace import rig as rig
from tests.test_material_workspace import surface_context as surface_context


def test_first_probe_support_preview_grants_no_placement_authority(active_surface, monkeypatch):
    context, _ = active_surface
    monkeypatch.setattr(context.machine, "material_surface_snapshot", lambda: None)
    assert context.material_preview_is_approximate()
    assert context.material_preview_mapper() is context.bed
    with pytest.raises(CalibrationError, match="Measure the surface"):
        context.material_surface_signature()
    source = np.zeros((1080, 1920, 3), np.uint8)
    monkeypatch.setattr(context, "camera_frame", lambda **kwargs: source)
    preview = context.rectified_frame(approximate_support_preview=True)
    area, ppm = context.settings.machine.work_area, context.settings.calibration.bed.pixels_per_mm
    assert preview.shape[:2] == (round(area.height * ppm), round(area.width * ppm))
    assert context._workspace_image is None
    with pytest.raises(CalibrationError, match="Measure the surface"):
        context.rectified_frame()


def test_out_of_range_measured_height_never_falls_back_to_support(active_surface, monkeypatch):
    context, plan = active_surface
    plan["surface_elevation_mm"] = 11.0
    monkeypatch.setattr(context, "camera_frame", lambda **kwargs: pytest.fail("Invalid height must reject before capture"))
    with pytest.raises(CalibrationError, match="measured range"):
        context.material_preview_signature()
    with pytest.raises(CalibrationError, match="measured range"):
        context.rectified_frame(approximate_support_preview=True)


def test_first_probe_preview_rejects_stale_camera_pose(active_surface, monkeypatch):
    context, _ = active_surface
    monkeypatch.setattr(context.machine, "material_surface_snapshot", lambda: None)
    context.settings.machine.photo_x += .5
    with pytest.raises(CalibrationError, match="provenance"):
        context.material_preview_signature()


def test_new_measurement_during_approximate_capture_rejects_old_image(active_surface, monkeypatch):
    context, plan = active_surface
    state = {"measurement": None}
    monkeypatch.setattr(context.machine, "material_surface_snapshot", lambda: state["measurement"])

    def image(**kwargs):
        state["measurement"] = plan
        return np.zeros((1080, 1920, 3), np.uint8)

    monkeypatch.setattr(context, "camera_frame", image)
    with pytest.raises(CalibrationError, match="surface changed"):
        context.rectified_frame(approximate_support_preview=True)
    assert context._workspace_image is None


def test_first_probe_target_uses_explicit_support_estimate_then_measured_plane(active_surface, rig, monkeypatch):
    context, plan = active_surface
    state = {"measurement": None}
    monkeypatch.setattr(context.machine, "material_surface_snapshot", lambda: state["measurement"])
    status = CameraStatus(True, "test", 1920, 1080, 15., 10, None, frame_age_seconds=.1)
    monkeypatch.setattr(context.camera, "status", lambda: status)
    metadata = {"source_width": 1920, "source_height": 1080,
                "camera_settings": asdict(context.camera.settings)}
    point = np.array([[110., 110.]])
    support_pixel = rig[4](point, -2.)[0]
    result = context.focus_probe_target(*support_pixel, source_image_size=[1920, 1080],
                                        frame_age_seconds=.1, frame_metadata=metadata)
    assert result["approximate_support_preview"] is True
    assert result["height_corrected"] is False
    assert result["target_machine_xy_mm"] == pytest.approx(point[0], abs=1e-5)
    state["measurement"] = plan
    material_pixel = rig[4](point, plan["surface_elevation_mm"])[0]
    result = context.focus_probe_target(*material_pixel, source_image_size=[1920, 1080],
                                        frame_age_seconds=.1, frame_metadata=metadata)
    assert result["approximate_support_preview"] is False
    assert result["height_corrected"] is True
    assert result["target_machine_xy_mm"] == pytest.approx(point[0], abs=1e-5)
