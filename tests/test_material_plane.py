from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.bed import BedCalibration, BedPoint
from laser_aligner.calibration.lens import LensModel
from laser_aligner.calibration.material_plane import (
    MaterialPlaneSelection,
    binding_digest,
    camera_sampling_diagnostics,
    require_precision_model,
)
from laser_aligner.calibration.surface import SurfaceCalibrationStore
from laser_aligner.config import WorkArea
from laser_aligner.errors import CalibrationError


@pytest.fixture
def plane_rig(tmp_path):
    matrix = np.array([[1800., 0, 960], [0, 1750., 540], [0, 0, 1]])
    lens = LensModel(matrix, np.array([.05, -.02, .001, -.001, 0]), 1920, 1080, 0, 0, 20, 1)
    rotation = cv2.Rodrigues(np.array([.12, -.07, .03]))[0] @ np.diag([1., -1., -1.])
    translation = -rotation @ np.array([110., 110., 600.])
    rvec = cv2.Rodrigues(rotation)[0]
    area = WorkArea(0, 220, 0, 220)
    binding = {
        "machine_id": "test-machine", "calibration_profile": "fixed-focus",
        "camera_geometry": {"lens_model_id": lens.model_id, "image_size": [1920, 1080]},
        "datum_id": "Home border", "mount_revision": "fixed-mount-1", "capture_pose": [110., 220., None],
    }
    xy = np.array([(x, y) for x in np.linspace(15, 205, 5) for y in np.linspace(15, 205, 5)])

    def observations(z, positions=xy):
        objects = np.column_stack((positions, np.full(len(positions), z)))
        image = cv2.projectPoints(objects, rvec, translation, matrix, np.zeros(5))[0].reshape(-1, 2)
        return [BedPoint(*pixel, *point) for pixel, point in zip(image, positions, strict=True)]

    store = SurfaceCalibrationStore(tmp_path)
    for slot, height in (("lower", -1.5), ("upper", 18.5), ("check", 8.5)):
        store.save_observations(
            slot, height_mm=height, reference="Home border", points=observations(height),
            source_digest=f"{int((height + 100) * 1000):064x}", lens=lens, work_area=area, binding=binding,
        )
    model = store.solve(lens=lens, work_area=area, binding=binding)
    measurement = {
        "id": "plan-1", "measurement_id": "measurement-1", "surface_elevation_mm": 8.5,
        "honeycomb_height_mm": -1.5, "reference": {"id": "reference-1", "border_z_mm": -2},
        "session": [1, "firmware-1"], "reusable": True,
    }
    return model, measurement, binding, lens, area, store, observations


def select(rig, model=None, measurement=None, **changes):
    actual = rig[0] if model is None else model
    arguments = {"datum_id": "Home border", "binding_id": binding_digest(rig[2]), "capture_pose": (110., 220., None)}
    arguments.update(changes)
    return MaterialPlaneSelection.from_measurement(actual, rig[1] if measurement is None else measurement, **arguments)


def test_intermediate_material_planes_preserve_machine_coordinates_and_dimensions(plane_rig):
    model, measurement, *_ = plane_rig
    positions = np.array([[2.5, 5.], [182.5, 5.], [182.5, 190.], [2.5, 190.]])
    for height in (-1.5, 0., 3.25, 8.5, 15., 18.5):
        selected = select(plane_rig, measurement={**measurement, "surface_elevation_mm": height})
        observations = plane_rig[-1](height, positions)
        image = np.array([[p.image_x, p.image_y] for p in observations])
        inverse, forward = selected.homographies()
        restored = cv2.perspectiveTransform(image.reshape(-1, 1, 2), inverse).reshape(-1, 2)
        assert restored == pytest.approx(positions, abs=1e-6)
        assert inverse @ forward == pytest.approx(np.eye(3), abs=1e-8)
        assert selected.model is model


@pytest.mark.parametrize("change", [
    {"evidence_schema_version": 1}, {"check_rms_mm": None, "check_max_mm": None},
    {"check_max_mm": .10001}, {"fit_max_mm": .10001}, {"fit_rms_mm": -.1},
    {"evidence_source_ids": ("0" * 64,) * 3}, {"model_id": "invalid"},
])
def test_diagnostic_and_unqualified_numerical_models_cannot_select(plane_rig, change):
    model = replace(plane_rig[0], **change)
    # Diagnostic geometry remains available even when precision selection fails.
    model.homographies(8.5)
    with pytest.raises(CalibrationError):
        select(plane_rig, model=model)


@pytest.mark.parametrize("change", [
    {"surface_elevation_mm": -1.5001}, {"surface_elevation_mm": 18.5001},
    {"surface_elevation_mm": float("nan")}, {"surface_elevation_mm": True},
    {"surface_elevation_mm": "8.5"}, {"honeycomb_height_mm": float("inf")},
    {"reusable": False}, {"id": ""}, {"measurement_id": None},
    {"reference": None}, {"reference": {}}, {"session": []}, {"session": [float("nan")]},
])
def test_invalid_measurement_rejects_before_creating_selected_plane(plane_rig, change):
    with pytest.raises(CalibrationError):
        select(plane_rig, measurement={**plane_rig[1], **change})


@pytest.mark.parametrize("change", [
    {"datum_id": "different border"}, {"binding_id": "0" * 64},
    {"capture_pose": (110., 210., None)}, {"capture_pose": (True, 220., None)},
])
def test_stale_datum_profile_or_capture_pose_rejects(plane_rig, change):
    with pytest.raises(CalibrationError):
        select(plane_rig, **change)


def test_selected_plane_is_immutable_and_exported_provenance_is_defensive(plane_rig):
    selected = select(plane_rig)
    with pytest.raises(FrozenInstanceError):
        selected.elevation_mm = 3
    before = selected.signature
    exported = selected.to_dict()
    exported["reference"]["border_z_mm"] = 999
    exported["session"][0] = 999
    plane_rig[1]["reference"]["border_z_mm"] = 998
    assert selected.signature == before
    assert selected.to_dict()["reference"]["border_z_mm"] == -2
    assert json.loads(json.dumps(selected.to_dict())) == selected.to_dict()
    with pytest.raises(CalibrationError):
        replace(selected, elevation_mm=80)


def test_direct_model_copies_mutable_geometry_before_selection(plane_rig):
    model = plane_rig[0]
    inputs = {field: np.asarray(getattr(model, field)).copy()
              for field in ("camera_matrix", "rotation", "translation", "area", "image_size")}
    sources = list(model.evidence_source_ids)
    copied = replace(model, **inputs, evidence_source_ids=sources)
    selected = select(plane_rig, model=copied)
    before = selected.homographies()[0].copy(), selected.signature, selected.to_dict()
    for values in inputs.values():
        values[:] = 0
    sources[:] = ["changed"]
    assert np.array_equal(selected.homographies()[0], before[0])
    assert selected.signature == before[1]
    assert selected.to_dict() == before[2]


def test_adapter_does_not_reuse_or_modify_base_refinements(plane_rig):
    selected = select(plane_rig)
    base = BedCalibration(
        np.eye(3), np.eye(3), 1920, 1080, .3, .5, 25, 25, 42,
        registration_x_mm=1., registration_y_mm=2., registration_created_at=43,
        provenance={"camera": {"device": "old-base"}},
    )
    before = base.to_dict()
    adapter = selected.as_bed_calibration(base)
    assert adapter.residual_mesh is None and adapter.refinement_base is None
    assert adapter.registration_x_mm == adapter.registration_y_mm == 0
    assert adapter.registration_created_at is None
    assert adapter.max_error_mm < 1e-6
    adapter.provenance["camera"]["device"] = "changed-adapter"
    assert base.to_dict() == before
    assert not adapter.image_to_machine.flags.writeable
    with pytest.raises(CalibrationError):
        selected.as_bed_calibration(replace(base, image_width=1280))


def test_raw_pixel_sampling_and_height_sensitivity_match_independent_projection(plane_rig):
    model, _, _, lens, *_ = plane_rig
    result = camera_sampling_diagnostics(model, lens, height_mm=8.5)
    assert len(result["samples"]) == 9
    assert result["all_samples_visible"] == all(sample["inside_image"] for sample in result["samples"])
    assert any(sample["inside_image"] for sample in result["samples"])
    assert result["physical_accuracy_verified"] is False
    assert result["worst_mm_per_source_pixel"] > .1
    point = np.array([[220., 220.]])
    _, forward = model.homographies(8.5)
    image = cv2.perspectiveTransform(point.reshape(-1, 1, 2), forward).reshape(-1, 2)
    displaced = model.image_to_machine(image, 8.51)
    observed_sensitivity = np.linalg.norm(displaced - point) / .01
    assert result["samples"][-1]["xy_mm_per_height_mm"] == pytest.approx(observed_sensitivity, rel=1e-6)
    budget = result["height_error_for_0_1mm_xy_mm"]
    assert budget * result["maximum_xy_mm_per_height_mm"] == pytest.approx(.1)


def test_raw_sampling_detects_lens_change_and_invalid_points(plane_rig):
    model, _, _, lens, *_ = plane_rig
    changed = LensModel(lens.camera_matrix, np.zeros(5), 1920, 1080, 0, 0, 20, 1)
    with pytest.raises(CalibrationError, match="identity"):
        camera_sampling_diagnostics(model, changed, height_mm=8.5)
    for points in ([], [[-1, 10]], [[float("nan"), 2]], [[10, 5, 3]]):
        with pytest.raises(CalibrationError):
            camera_sampling_diagnostics(model, lens, height_mm=8.5, points_mm=points)


def test_raw_observation_save_never_touches_production_bed_map(plane_rig):
    model, _, binding, lens, area, store, observations = plane_rig
    base_path = store.path.parent / "bed_calibration.json"
    base_path.write_bytes(b"authoritative support map")
    store.save_observations(
        "lower", height_mm=-1.5, reference="Home border", points=observations(-1.5),
        source_digest="e" * 64, lens=lens, work_area=area, binding=binding,
    )
    assert base_path.read_bytes() == b"authoritative support map"
    assert store.schema_version == 2
    assert "check" not in store.load()
    with pytest.raises(CalibrationError, match="Independent"):
        require_precision_model(store.solve(lens=lens, work_area=area, binding=binding))
    assert model.check_passed


@pytest.mark.parametrize("field", ["machine_id", "calibration_profile", "camera_geometry", "datum_id", "capture_pose", "mount_revision"])
def test_raw_evidence_requires_explicit_production_provenance(plane_rig, field):
    _, _, binding, lens, area, store, observations = plane_rig
    before = store.path.read_bytes()
    with pytest.raises(CalibrationError):
        store.save_observations(
            "lower", height_mm=-1.5, reference="Home border", points=observations(-1.5),
            source_digest="e" * 64, lens=lens, work_area=area,
            binding={key: value for key, value in binding.items() if key != field},
        )
    assert store.path.read_bytes() == before


def test_legacy_evidence_remains_diagnostic_until_reacquired(plane_rig):
    _, _, binding, lens, area, store, observations = plane_rig
    legacy_binding = {key: binding[key] for key in ("machine_id", "calibration_profile", "camera_geometry")}
    document = json.loads(store.path.read_text())
    document["schema_version"] = 1
    for evidence in document["evidence"].values():
        evidence["binding"] = legacy_binding
    store.path.write_text(json.dumps(document))
    before = store.path.read_bytes()
    old = store.solve(lens=lens, work_area=area, binding=legacy_binding)
    assert old.check_passed
    with pytest.raises(CalibrationError, match="Legacy"):
        require_precision_model(old)
    assert store.path.read_bytes() == before
    for index, (slot, height) in enumerate((("lower", -1.5), ("upper", 18.5), ("check", 8.5))):
        store.save_observations(
            slot, height_mm=height, reference="Home border", points=observations(height),
            source_digest=f"{index + 50:064x}", lens=lens, work_area=area, binding=binding,
        )
        if index == 0:
            with pytest.raises(CalibrationError, match="provenance"):
                store.solve(lens=lens, work_area=area, binding=binding)
    require_precision_model(store.solve(lens=lens, work_area=area, binding=binding))
