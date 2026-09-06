from __future__ import annotations

import json
from dataclasses import replace

import cv2
import numpy as np
import pytest

from laser_aligner.calibration.bed import BedCalibration, BedPoint
from laser_aligner.calibration.lens import LensModel
from laser_aligner.calibration.surface import (
    SurfaceCalibrationStore,
    SurfaceEvidence,
    fit_surface_model,
)
from laser_aligner.config import WorkArea
from laser_aligner.errors import CalibrationError


@pytest.fixture
def rig():
    matrix = np.array([[1600., 0, 960], [0, 1600., 540], [0, 0, 1]])
    lens = LensModel(matrix, np.zeros(5), 1920, 1080, 0, 0, 12, 1)
    rotation = cv2.Rodrigues(np.array([.12, -.07, .03]))[0] @ np.diag([1., -1., -1.])
    translation = -rotation @ np.array([110., 110., 600.])
    rvec = cv2.Rodrigues(rotation)[0]
    area = WorkArea(0, 220, 0, 220)
    binding = {"lens_model_id": lens.model_id, "machine": "test", "focus": 30}
    xy = np.array([(x, y) for x in np.linspace(15, 205, 5) for y in np.linspace(15, 205, 5)])

    def project(points, height):
        objects = np.column_stack((points, np.full(len(points), height)))
        return cv2.projectPoints(objects, rvec, translation, matrix, np.zeros(5))[0].reshape(-1, 2)

    def evidence(height, *, noise=0., shift=0.):
        image = project(xy, height)
        image += np.random.default_rng(17).normal(0, noise, image.shape)
        image[:, 0] += shift
        return SurfaceEvidence.from_dict({
            "height_mm": height, "reference": "Home / park border", "binding": binding,
            "source_digest": f"{int((height + 100) * 1000):064x}",
            "points": np.column_stack((image, xy)).tolist(),
        })

    def calibration(height):
        forward = matrix @ np.column_stack((rotation[:, :2], translation + height * rotation[:, 2]))
        return BedCalibration(
            np.linalg.inv(forward), forward, 1920, 1080, 0, 0, 25, 25, height + 100,
        )

    return lens, area, binding, xy, project, evidence, calibration


def solve(rig, lower=None, upper=None, check=None):
    lens, area, binding, _, _, evidence, _ = rig
    return fit_surface_model(
        evidence(-2.) if lower is None else lower, evidence(10.) if upper is None else upper,
        lens=lens, work_area=area, binding=binding, check=check,
    )


def test_two_planes_predict_unseen_heights_across_bed(rig):
    _, _, _, _, project, evidence, _ = rig
    model = solve(rig, check=evidence(4.))
    assert model.check_passed
    # Independent heights and positions, including the corners not used in fitting.
    positions = np.array([(0., 0.), (220., 220.), (47.3, 189.2), (180., 33.)])
    for height in (-2., -0.5, 1.7, 4., 8.2, 10.):
        measured = model.image_to_machine(project(positions, height), height)
        assert np.allclose(measured, positions, atol=1e-6)
        inverse, forward = model.homographies(height)
        assert np.allclose(inverse @ forward, np.eye(3), atol=1e-8)
    assert model.fit_max_mm < 1e-6


def test_noisy_evidence_and_independent_check(rig):
    evidence = rig[5]
    model = solve(rig, evidence(-2., noise=.10), evidence(10., noise=.10), evidence(4., noise=.10))
    assert model.check_passed
    assert 0 < model.fit_rms_mm < .1


def test_missing_and_failed_check_never_report_pass(rig):
    assert not solve(rig).check_passed
    model = solve(rig, check=rig[5](4., shift=5.))
    assert not model.check_passed
    assert model.check_max_mm > .6
    # A holdout failure cannot alter the fitted geometry.
    assert np.allclose(model.homographies(4)[1], solve(rig).homographies(4)[1])


@pytest.mark.parametrize("height", [-2.001, 10.001, float("nan"), float("inf"), True, "4"])
def test_rejects_invalid_or_extrapolated_height(rig, height):
    with pytest.raises(CalibrationError):
        solve(rig).homographies(height)


@pytest.mark.parametrize("kind", ["same_map", "same_height", "reversed", "reference", "binding", "collinear", "outside", "camera_move", "wrong_height"])
def test_bad_fit_evidence_is_rejected(rig, kind):
    evidence = rig[5]
    low, high = evidence(-2.), evidence(10.)
    if kind == "same_map":
        high = replace(high, source_digest=low.source_digest)
    elif kind == "same_height":
        high = replace(high, height_mm=-1.9)
    elif kind == "reversed":
        low, high = high, low
    elif kind == "reference":
        high = replace(high, reference="Different border")
    elif kind == "binding":
        high = replace(high, binding_json=json.dumps({"machine": "other"}))
    elif kind == "collinear":
        high = replace(high, points=tuple((p[0], p[1], p[2], p[2]) for p in high.points))
    elif kind == "outside":
        high = replace(high, points=tuple((p[0], p[1], p[2] + 1000, p[3]) for p in high.points))
    elif kind == "camera_move":
        high = evidence(10., shift=20.)
    elif kind == "wrong_height":
        high = replace(high, height_mm=40.)
    with pytest.raises(CalibrationError):
        solve(rig, low, high)


@pytest.mark.parametrize("height", [-2., -.1, 9., 10., 11.])
def test_check_requires_an_independent_middle_height(rig, height):
    with pytest.raises(CalibrationError):
        solve(rig, check=rig[5](height))


def test_stale_current_binding_rejected(rig):
    lens, area, binding, _, _, evidence, _ = rig
    with pytest.raises(CalibrationError, match="provenance"):
        fit_surface_model(evidence(-2.), evidence(10.), lens=lens, work_area=area, binding={**binding, "focus": 40})


def save(store, rig, slot, height, calibration=None):
    lens, area, binding, _, _, evidence, make_calibration = rig
    entry = evidence(height)
    return store.save_map(
        slot, height_mm=height, reference=entry.reference,
        calibration=make_calibration(height) if calibration is None else calibration,
        points=[BedPoint(*row) for row in entry.points], lens=lens, work_area=area, binding=binding,
    )


def test_atomic_evidence_roundtrip_clears_old_check_without_touching_base_map(tmp_path, rig):
    store = SurfaceCalibrationStore(tmp_path)
    base_path = tmp_path / "bed_calibration.json"
    base_path.write_bytes(b"existing production calibration")
    for slot, height in (("lower", -2.), ("upper", 10.), ("check", 4.)):
        save(store, rig, slot, height)
    reloaded = SurfaceCalibrationStore(tmp_path)
    lens, area, binding = rig[:3]
    assert reloaded.solve(lens=lens, work_area=area, binding=binding).check_passed
    assert base_path.read_bytes() == b"existing production calibration"
    save(reloaded, rig, "lower", -2.)
    assert "check" not in reloaded.load()
    assert not reloaded.solve(lens=lens, work_area=area, binding=binding).check_passed


@pytest.mark.parametrize("change", ["translation", "inliers", "resolution", "points_changed"])
def test_snapshot_rejects_corrected_or_inconsistent_base_map(tmp_path, rig, change):
    calibration = rig[6](-2.)
    if change == "translation":
        calibration.registration_x_mm = .5
    elif change == "inliers":
        calibration.inlier_count = 24
    elif change == "resolution":
        calibration.image_width = 1280
    elif change == "points_changed":
        calibration.image_to_machine = np.eye(3)
    with pytest.raises(CalibrationError):
        save(SurfaceCalibrationStore(tmp_path), rig, "lower", -2., calibration)
    assert not (tmp_path / "surface_height_calibration.json").exists()


@pytest.mark.parametrize("raw", [{}, {"schema_version": True, "evidence": {}}, {"schema_version": 2, "evidence": {}}, {"schema_version": 1, "evidence": {"bad": {}}}])
def test_rejects_invalid_and_future_schema_without_overwriting(tmp_path, rig, raw):
    store = SurfaceCalibrationStore(tmp_path)
    store.path.write_text(json.dumps(raw))
    before = store.path.read_bytes()
    with pytest.raises(CalibrationError):
        save(store, rig, "lower", -2.)
    assert store.path.read_bytes() == before


def test_rectification_and_pixel_limits(rig):
    model = solve(rig)
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    assert model.rectify(image, 4., pixels_per_mm=2.).shape == (440, 440, 3)
    for source, ppm in ((image[:20], 2), (image, 100), (image, -1), (image, float("nan"))):
        with pytest.raises(CalibrationError):
            model.rectify(source, 4., pixels_per_mm=ppm)


def test_evidence_defensive_copy_and_nonfinite_rejection(rig):
    source = rig[5](-2.).to_dict()
    evidence = SurfaceEvidence.from_dict(source)
    source["points"][0][0] = float("nan")
    with pytest.raises(CalibrationError):
        SurfaceEvidence.from_dict(source)
    assert np.isfinite(evidence.points).all()
    exported = evidence.to_dict()
    exported["binding"]["focus"] = 999
    assert evidence.to_dict()["binding"]["focus"] == 30


@pytest.fixture
def surface_context(tmp_path, rig):
    from laser_aligner.app import AppContext
    from laser_aligner.config import load_settings

    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "app": {"data_dir": str(tmp_path / "data"), "open_browser": False},
        "camera": {"width": 1920, "height": 1080, "autostart": False},
        "machine": {"backend": "serial", "port": "COM_TEST"},
    }))
    context = AppContext(load_settings(config))
    context.lens.save_model(rig[0])
    return context


def install_context_map(context, rig, height):
    context.bed.replace_points_and_solve(
        [BedPoint(*row) for row in rig[5](height).points], 1920, 1080,
        provenance=context._bed_provenance(),
    )


def test_context_collection_and_solve_are_controller_inert(surface_context, rig, monkeypatch):
    context = surface_context

    def forbidden(*args, **kwargs):
        pytest.fail("Height study must not query or command hardware")

    monkeypatch.setattr(context.machine, "status", forbidden)
    monkeypatch.setattr(context.machine, "prepare_photo_position", forbidden)
    monkeypatch.setattr(context, "camera_frame", forbidden)
    for slot, height in (("lower", -2.), ("upper", 10.), ("check", 4.)):
        install_context_map(context, rig, height)
        before = context.bed_mapping_digest()
        context.save_surface_height_map(slot, height, "Home border")
        assert context.bed_mapping_digest() == before
    before = context.bed_mapping_digest()
    assert context.solve_surface_height_model().check_passed
    assert context.bed_mapping_digest() == before
    context.settings.camera.device = "a different camera"
    with pytest.raises(CalibrationError, match="provenance"):
        context.solve_surface_height_model()


def test_context_rejects_missing_lens_or_stale_base_map(surface_context, rig):
    context = surface_context
    install_context_map(context, rig, -2.)
    context.settings.camera.width = 1280
    with pytest.raises(CalibrationError, match="STALE"):
        context.save_surface_height_map("lower", -2., "Home border")
    context.lens._model = None
    with pytest.raises(CalibrationError, match="lens"):
        context.surface_calibration_binding()


@pytest.mark.parametrize("control,value", [("focus_automatic_continuous", 1), ("focus_absolute", 93)])
def test_context_rejects_live_optical_profile_changes(surface_context, control, value):
    surface_context.settings.camera.controls[control] = value
    with pytest.raises(CalibrationError, match="focus|optical"):
        surface_context.surface_calibration_binding()


def test_interrupted_atomic_save_preserves_existing_evidence(tmp_path, rig, monkeypatch):
    from laser_aligner import storage

    store = SurfaceCalibrationStore(tmp_path)
    save(store, rig, "lower", -2.)
    before = store.path.read_bytes()

    def fail_replace(*args):
        raise OSError("simulated interrupted replace")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted"):
        save(store, rig, "upper", 10.)
    assert store.path.read_bytes() == before
    assert set(store.load()) == {"lower"}
