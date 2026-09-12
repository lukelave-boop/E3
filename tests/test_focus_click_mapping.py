"""Raw focus clicks use the current camera calibration and never reach hardware."""
from dataclasses import asdict, replace

import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.bed import BedPoint, BedResidualMesh
from laser_aligner.calibration.lens import LensModel
from laser_aligner.camera.service import CameraStatus
from laser_aligner.errors import CalibrationError
from tests.test_app_simulation import _settings


@pytest.fixture
def mapped_context(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.camera.width = settings.camera.height = 440
    context = AppContext(settings)
    context.lens._model = LensModel(
        np.array([[400., 0., 220.], [0., 400., 220.], [0., 0., 1.]]),
        np.zeros(5), 440, 440, 0.1, 0.1, 12, 1.,
    )
    context.bed.replace_points_and_solve(
        [BedPoint(20., 420., 0., 0.), BedPoint(420., 420., 200., 0.),
         BedPoint(420., 20., 200., 200.), BedPoint(20., 20., 0., 200.)],
        440, 440, provenance=context._bed_provenance(),
    )
    status = CameraStatus(True, "test", 440, 440, 15., 10, None, frame_age_seconds=0.1)
    monkeypatch.setattr(context.camera, "status", lambda: status)
    monkeypatch.setattr(context.camera, "snapshot", lambda: pytest.fail("No camera capture allowed"))
    monkeypatch.setattr(context.machine, "connect", lambda: pytest.fail("No machine connection allowed"))
    return context, status


def click(context, x=220., y=220., **changes):
    args = {
        "source_image_size": [440, 440], "frame_age_seconds": 0.1,
        "frame_metadata": {"source_width": 440, "source_height": 440,
                           "camera_settings": asdict(context.camera.settings)},
    }
    args.update(changes)
    return context.focus_probe_target(x, y, **args)


def test_click_maps_raw_pixel_and_revalidates_same_signature(mapped_context):
    context, _ = mapped_context
    result = click(context)
    assert result["target_machine_xy_mm"] == pytest.approx([100, 100])
    assert result["raw_image_xy"] == [220., 220.]
    assert result["mapping_plane"] == "bed"
    assert result["height_corrected"] is False
    assert click(context, mapping_signature=result["mapping_signature"]) == result
    assert not context.machine.status()["connected"]


def test_lens_distortion_is_removed_before_bed_mapping(mapped_context):
    context, _ = mapped_context
    context.lens._model = replace(context.lens.model, distortion=np.array([0.08, 0, 0, 0, 0]), model_id="")
    context.bed.calibration.provenance = context._bed_provenance()
    raw = context.lens.model.distort_points(np.array([[100., 140.]]))[0]
    result = click(context, *raw.tolist())
    assert result["corrected_image_xy"] == pytest.approx([100., 140.], abs=1e-5)
    assert result["target_machine_xy_mm"] == pytest.approx([40., 140.], abs=1e-5)


def test_fine_registration_is_used_and_old_selection_rejected(mapped_context):
    context, _ = mapped_context
    result = click(context)
    context.bed.apply_registration_translation(2., -3.)
    with pytest.raises(CalibrationError, match="changed after selection"):
        click(context, mapping_signature=result["mapping_signature"])
    assert click(context)["target_machine_xy_mm"] == pytest.approx([102., 97.])


def test_residual_mesh_changes_coordinates_and_selection_identity(mapped_context):
    context, _ = mapped_context
    old = click(context)
    context.bed.calibration.residual_mesh = BedResidualMesh(
        np.array([0., 200.]), np.array([0., 200.]),
        np.array([[[0.5, -0.25], [0.5, -0.25]], [[0.5, -0.25], [0.5, -0.25]]]),
        2., 0.05, 0.1,
    )
    assert click(context)["target_machine_xy_mm"] == pytest.approx([100.5, 99.75])
    with pytest.raises(CalibrationError, match="changed after selection"):
        click(context, mapping_signature=old["mapping_signature"])


@pytest.mark.parametrize("age", [-1., 2.01, float("inf"), float("nan"), True, "0.1", None])
def test_stale_or_invalid_displayed_frame_rejected(mapped_context, age):
    with pytest.raises(CalibrationError):
        click(mapped_context[0], frame_age_seconds=age)


@pytest.mark.parametrize("field,value", [
    ("connected", False), ("frame_age_seconds", 3.), ("frame_age_seconds", None),
    ("frame_age_seconds", float("nan")), ("frame_age_seconds", -0.1),
    ("width", 1280), ("controls_critical_unverified", {"focus_auto": "enabled"}),
])
def test_actual_camera_readiness_required(mapped_context, field, value):
    context, status = mapped_context
    setattr(status, field, value)
    with pytest.raises(CalibrationError):
        click(context)


@pytest.mark.parametrize("size", [[220, 220], [True, 440], [440], None])
def test_invalid_or_scaled_image_rejected(mapped_context, size):
    with pytest.raises(CalibrationError):
        click(mapped_context[0], source_image_size=size)


@pytest.mark.parametrize("metadata", [None, {}, {"source_width": 880, "source_height": 880}])
def test_missing_or_resized_source_metadata_rejected(mapped_context, metadata):
    with pytest.raises(CalibrationError):
        click(mapped_context[0], frame_metadata=metadata)


def test_camera_setting_change_rejects_saved_frame(mapped_context):
    context, _ = mapped_context
    metadata = {"source_width": 440, "source_height": 440,
                "camera_settings": asdict(context.camera.settings)}
    context.camera.settings.controls["focus_absolute"] = 25
    with pytest.raises(CalibrationError, match="Camera settings changed"):
        click(context, frame_metadata=metadata)
    with pytest.raises(CalibrationError, match="STALE"):
        click(context)


@pytest.mark.parametrize("x,y", [(10., 200.), (-1., 200.), (440., 200.),
                               (200., 440.), (float("nan"), 200.), (True, 200.)])
def test_image_and_measured_coverage_bounds(mapped_context, x, y):
    with pytest.raises(CalibrationError):
        click(mapped_context[0], x, y)


def test_work_bounds_apply_after_fine_registration(mapped_context):
    context, _ = mapped_context
    context.bed.apply_registration_translation(-3., 0.)
    with pytest.raises(CalibrationError, match="machine work area"):
        click(context, 22., 220.)


def test_missing_bed_or_lens_mapping_cannot_use_pixel_scaling(mapped_context):
    context, _ = mapped_context
    context.bed._calibration = None
    with pytest.raises(CalibrationError, match="MISSING"):
        click(context)


def test_legacy_map_requires_provenance(mapped_context):
    context, _ = mapped_context
    context.bed.calibration.provenance = None
    with pytest.raises(CalibrationError, match="UNKNOWN"):
        click(context)


def test_missing_lens_rejected_even_with_consistent_raw_bed_map(mapped_context):
    context, _ = mapped_context
    context.lens._model = None
    context.bed.calibration.provenance = context._bed_provenance()
    with pytest.raises(CalibrationError, match="Lens and bed calibration"):
        click(context)


def test_wrong_active_optical_profile_rejected(mapped_context):
    context, _ = mapped_context
    context.calibration_profiles.current = replace(context.calibration_profiles.current, focus_absolute=30)
    with pytest.raises(CalibrationError, match="profile changed"):
        click(context)


def test_mapping_change_during_calculation_rejected(mapped_context, monkeypatch):
    context, _ = mapped_context
    original = context.bed.image_to_mm

    def changed(x, y):
        result = original(x, y)
        context.bed.calibration.image_to_machine[0, 2] += 1.
        return result

    monkeypatch.setattr(context.bed, "image_to_mm", changed)
    with pytest.raises(CalibrationError, match="changed during"):
        click(context)
