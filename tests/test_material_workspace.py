from __future__ import annotations

import copy

import numpy as np
import pytest
from test_surface_height_calibration import install_context_map
from test_surface_height_calibration import rig as rig
from test_surface_height_calibration import surface_context as surface_context

from laser_aligner.calibration.bed import BedPoint
from laser_aligner.errors import CalibrationError


@pytest.fixture
def active_surface(surface_context, rig, monkeypatch):
    context = surface_context
    install_context_map(context, rig, -2.)
    context.begin_surface_height_calibration("Home border")
    binding = context.production_surface_binding()
    for slot, height in (("lower", -2.), ("upper", 10.), ("check", 4.)):
        evidence = rig[5](height)
        context.surface_calibration.save_observations(
            slot, height_mm=height, reference="Home border", source_digest=evidence.source_digest,
            points=[BedPoint(*point) for point in evidence.points], lens=context.lens.model,
            work_area=context.settings.machine.work_area, binding=binding,
        )
    plan = dict(id="2ece9841-f849-4abc-a9ec-2793b7813fc9", measurement_id="surface-one",
                reusable=True, surface_elevation_mm=4., honeycomb_height_mm=-2.,
                session=[1, 2, 3], reference={"id": "border-one", "contact_z_mm": 0.})
    monkeypatch.setattr(context.machine, "material_surface_snapshot", lambda: copy.deepcopy(plan), raising=False)
    context.enable_material_height_correction()
    return context, plan


def test_selected_height_maps_same_xy_without_changing_support(active_surface, rig):
    context, plan = active_surface
    before, original = context.bed_mapping_digest(), context.bed.calibration
    xy = np.array([[20., 25.], [110., 105.], [195., 190.]])
    for height in (-2., 4., 10.):
        plan["surface_elevation_mm"] = height
        mapper = context.material_mapper()
        observed = np.asarray([mapper.image_to_mm(*point) for point in rig[4](xy, height)])
        assert np.allclose(observed, xy, atol=1e-5)
        assert context.bed.calibration is original
        assert context.bed_mapping_digest() == before


def test_surface_change_invalidates_cached_workspace_and_selection(active_surface):
    context, plan = active_surface
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    context._cache_workspace(image)
    first = context.material_surface_signature()
    assert context._cached_workspace() is not None
    plan["surface_elevation_mm"] = 5.
    assert context.material_surface_signature() != first
    assert context._cached_workspace() is None


def test_lost_measurement_rejects_active_correction(active_surface, monkeypatch):
    context, _ = active_surface
    context.material_mapper()
    monkeypatch.setattr(context.machine, "material_surface_snapshot", lambda: None)
    with pytest.raises(CalibrationError, match="Measure the surface"):
        context.material_mapper()
    with pytest.raises(CalibrationError):
        context._cached_workspace()


def test_camera_pose_changes_reject_saved_surface_model(active_surface):
    context, _ = active_surface
    context.settings.machine.photo_x += 1
    with pytest.raises(CalibrationError, match="provenance"):
        context.material_mapper()


def test_saved_honeycomb_change_invalidates_model_and_qualification_binding(active_surface):
    context, _ = active_surface
    old_binding = context.precision_setup_binding()
    context.machine._laser_focus.honeycomb_height_mm -= .1
    assert context.production_surface_binding()["honeycomb_height_mm"] != old_binding["honeycomb_height_mm"]
    with pytest.raises(CalibrationError, match="provenance"):
        context.material_mapper()
    with pytest.raises(CalibrationError, match="provenance"):
        context.precision_setup_binding()


def test_native_detail_rectification_keeps_original_bed_and_dimensions(active_surface):
    context, _ = active_surface
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    before = context.bed_mapping_digest()
    result = context._rectify_camera_image(image, pixels_per_mm=8.)
    assert result.shape == (1760, 1760, 3)
    assert context.bed_mapping_digest() == before
    assert context.precision_pixels_per_mm() >= 4.


def test_surface_change_during_acquisition_rejects_image(active_surface, monkeypatch):
    context, plan = active_surface
    def camera(**kwargs):
        plan["surface_elevation_mm"] = 5.
        return np.zeros((1080, 1920, 3), dtype=np.uint8)
    monkeypatch.setattr(context, "camera_frame", camera)
    with pytest.raises(CalibrationError, match="surface changed"):
        context.rectified_frame()


def test_qualification_binding_stable_across_measurements(active_surface):
    context, plan = active_surface
    binding = context.precision_setup_binding()
    plan.update(surface_elevation_mm=8., measurement_id="new-measurement")
    assert context.precision_setup_binding() == binding
    assert context.material_surface_metadata()["measurement_id"] == "new-measurement"


def test_sampling_reports_numerical_allowances_without_invented_physical_errors(active_surface):
    context, _ = active_surface
    result = context.precision_sampling_diagnostics()
    budget = result["numerical_error_budget"]
    assert budget["optical_fit_max_mm"] is not None
    assert budget["optical_holdout_max_mm"] is not None
    assert 0 < budget["remaining_target_mm"] < .075
    assert result["remaining_xy_budget_mm"] == budget["remaining_target_mm"]
    assert result["height_error_from_remaining_xy_budget_mm"] == pytest.approx(
        budget["remaining_target_mm"] / result["maximum_xy_mm_per_height_mm"])
    assert all(part["value_mm"] is None and part["status"] == "unmeasured"
               for part in budget["physical_components"].values())
    assert budget["physically_qualified"] is False
    context.enable_material_height_correction(False)
    flat = context.precision_sampling_diagnostics()["numerical_error_budget"]
    assert flat["optical_holdout_max_mm"] is None
    assert flat["physical_total_mm"] is None


def test_disabled_height_correction_keeps_legacy_mapping(active_surface, monkeypatch):
    context, _ = active_surface
    context.enable_material_height_correction(False)
    def forbidden():
        pytest.fail("Disabled correction must not read a measurement")
    monkeypatch.setattr(context.machine, "material_surface_snapshot", forbidden)
    assert context.material_surface_signature() is None
    assert context.material_mapper() is context.bed


def test_project_surface_metadata_roundtrip_retains_geometry_without_authority(active_surface):
    from laser_aligner.project.model import ProjectDocument, SceneObject
    context, _ = active_surface
    project = ProjectDocument.new()
    item = SceneObject.rectangle(project.active_layer_id)
    project.add_object(item)
    geometry = item.to_dict()
    project.metadata["material_surface"] = context.material_surface_metadata()
    restored = ProjectDocument.from_dict(project.to_dict())
    assert restored.objects[0].to_dict() == geometry
    assert restored.metadata["material_surface"] == project.metadata["material_surface"]
    assert restored.to_dict()["schema_version"] == 4


def test_schema3_migration_preserves_existing_project_geometry():
    from laser_aligner.project.model import ProjectDocument, SceneObject
    project = ProjectDocument.new()
    project.add_object(SceneObject.rectangle(project.active_layer_id))
    raw = project.to_dict()
    raw["schema_version"] = 3
    restored = ProjectDocument.from_dict(raw).to_dict()
    assert restored["objects"] == raw["objects"]
    assert restored["metadata"] == raw["metadata"]
    assert restored["schema_version"] == 4


@pytest.mark.parametrize("field,value", [("schema_version", 2), ("surface_elevation_mm", float("nan")),
                                        ("surface_elevation_mm", 100), ("model_id", "bad")])
def test_project_rejects_malformed_material_provenance(active_surface, field, value):
    from laser_aligner.project.model import ProjectDocument, ProjectFormatError
    context, _ = active_surface
    project = ProjectDocument.new()
    raw = project.to_dict()
    raw["metadata"]["material_surface"] = context.material_surface_metadata()
    raw["metadata"]["material_surface"][field] = value
    with pytest.raises(ProjectFormatError):
        ProjectDocument.from_dict(raw)


def test_browser_capture_and_generation_bind_exact_surface(active_surface, monkeypatch):
    from test_app_simulation import _browser_generation_payload
    context, plan = active_surface
    image = np.zeros((880, 880, 3), dtype=np.uint8)
    monkeypatch.setattr(context, "capture_parked_trace_frame", lambda **kwargs: image)
    with pytest.raises(CalibrationError, match="Capture the measured surface"):
        context.generate_gcode(_browser_generation_payload())
    capture = context.capture_browser_placement()
    assert np.array_equal(context.browser_placement_image(capture["capture_id"]), image)
    assert context.browser_placement_pixels_per_mm(capture["capture_id"]) == context.precision_pixels_per_mm()
    payload = {**_browser_generation_payload(), "capture_id": capture["capture_id"]}
    generated = context.generate_gcode(payload)
    context.validate_material_surface_program(generated["gcode"])
    assert "E3SURFACE 1 " in generated["gcode"]
    plan["surface_elevation_mm"] = 5.
    with pytest.raises(CalibrationError, match="Capture the measured surface"):
        context.generate_gcode(payload)
    with pytest.raises(CalibrationError):
        context.browser_placement_image(capture["capture_id"])
    with pytest.raises(CalibrationError, match="changed after preview"):
        context.validate_material_surface_program(generated["gcode"])


def test_browser_capture_rejects_surface_change_during_capture(active_surface, monkeypatch):
    context, plan = active_surface
    def changed(**kwargs):
        plan["measurement_id"] = "next-measurement"
        return np.zeros((100, 100, 3), dtype=np.uint8)
    monkeypatch.setattr(context, "capture_parked_trace_frame", changed)
    with pytest.raises(CalibrationError, match="during browser capture"):
        context.capture_browser_placement()
    assert getattr(context, "_browser_placement_capture", None) is None


def test_browser_capture_rejects_replaced_capture_id(active_surface, monkeypatch):
    context, _ = active_surface
    monkeypatch.setattr(context, "capture_parked_trace_frame", lambda **kwargs: np.zeros((10, 10, 3), dtype=np.uint8))
    first = context.capture_browser_placement()
    second = context.capture_browser_placement()
    assert first["capture_id"] != second["capture_id"]
    with pytest.raises(CalibrationError):
        context.validate_browser_placement(first["capture_id"])


def test_disabling_correction_rejects_old_height_photograph(active_surface, monkeypatch):
    context, _ = active_surface
    monkeypatch.setattr(context, "capture_parked_trace_frame", lambda **kwargs: np.zeros((10, 10, 3), dtype=np.uint8))
    capture = context.capture_browser_placement()
    context.enable_material_height_correction(False)
    with pytest.raises(CalibrationError):
        context.browser_placement_image(capture["capture_id"])
    assert context.validate_browser_placement(None) is None


def test_replaced_photo_during_generation_rejects_same_height_job(active_surface, monkeypatch):
    from test_app_simulation import _browser_generation_payload

    import laser_aligner.app as app_module
    context, _ = active_surface
    monkeypatch.setattr(context, "capture_parked_trace_frame", lambda **kwargs: np.zeros((10, 10, 3), dtype=np.uint8))
    capture = context.capture_browser_placement()
    original = app_module.generate_vector_gcode
    def replaced(*args, **kwargs):
        program = original(*args, **kwargs)
        context.capture_browser_placement()
        return program
    monkeypatch.setattr(app_module, "generate_vector_gcode", replaced)
    with pytest.raises(CalibrationError, match="Capture the measured surface"):
        context.generate_gcode({**_browser_generation_payload(), "capture_id": capture["capture_id"]})


def test_surface_calibration_job_uses_its_own_desktop_capture_callback(active_surface, monkeypatch):
    from types import SimpleNamespace
    context, _ = active_surface
    monkeypatch.setattr(context.machine, "preflight_program", lambda _text: SimpleNamespace(digest="reviewed-job"))
    job = context.prepare_surface_height_capture(
        "check", 4., "Home border", .01, powered=False, power_percent=0., mark_size_mm=2., speed_mm_min=1000.,
    )
    assert job.display_name == "Surface height calibration"


def test_replaced_photo_during_unsuccessful_detection_rejects_result(active_surface, monkeypatch):
    import laser_aligner.app as app_module
    context, _ = active_surface
    monkeypatch.setattr(context, "capture_parked_trace_frame", lambda **kwargs: np.zeros((10, 10, 3), dtype=np.uint8))
    capture = context.capture_browser_placement()
    def replaced(*args, **kwargs):
        context.capture_browser_placement()
        return None
    monkeypatch.setattr(app_module, "detect_workpiece", replaced)
    with pytest.raises(CalibrationError, match="Capture the measured surface"):
        context.detect_workpiece(capture_id=capture["capture_id"])


def test_status_reports_blocked_surface_without_throwing(active_surface, monkeypatch):
    context, _ = active_surface
    monkeypatch.setattr(context.machine, "material_surface_snapshot", lambda: None)
    status = context.material_placement_status()
    assert status["enabled"] and not status["ready"]
    assert "Measure the surface" in status["reason"]


@pytest.mark.parametrize("field,value", [("schema_version", True), ("enabled", 1), ("reference", " ")])
def test_material_preference_rejects_malformed_persistence(active_surface, field, value):
    from laser_aligner.storage import atomic_write_json
    context, _ = active_surface
    state = context._surface_placement_state()
    state[field] = value
    atomic_write_json(context.surface_placement_path, state)
    with pytest.raises(CalibrationError, match="Invalid material-placement setup"):
        context.selected_material_surface()
