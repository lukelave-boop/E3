from __future__ import annotations

# ruff: noqa: E402
import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtWidgets

from laser_aligner.config import CalibrationSettings, LaserSettings, MachineSettings, WorkArea
from laser_aligner.desktop import controller as controller_module
from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.template_panel import TemplatePanel
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.errors import CalibrationError
from laser_aligner.project import Bounds, ProjectDocument, SceneObject
from laser_aligner.templates import template_from_project


@pytest.fixture
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_template_display_rounding_does_not_change_pose_or_nudges(application):
    panel = TemplatePanel()
    panel.set_templates([{"id": "precision", "name": "Precision"}])
    panel.set_placement(25.123456789, 36.987654321, 1.23456789)
    assert panel.x_spin.value() == 25.123
    assert panel.placement()["center_x_mm"] == 25.123456789
    panel.y_spin.setValue(40.125)
    assert panel.placement()["center_y_mm"] == 40.125
    assert panel.placement()["rotation_deg"] == 1.23456789
    panel._nudge("x", 1)
    assert panel.placement()["center_x_mm"] == pytest.approx(25.223456789, abs=1e-12)
    assert panel.placement()["rotation_deg"] == 1.23456789
    panel.close()


@pytest.mark.parametrize("snap", [False, True])
def test_precision_suspends_snap_and_restores_preference(application, snap):
    view = WorkspaceView(Bounds(0, 0, 100, 100))
    view.set_snap_enabled(snap)
    view.set_precision_placement_enabled(True)
    assert view.snap_enabled is False
    assert view._snap_and_clamp_machine_point((12.123456, 34.987654)) == (12.123456, 34.987654)
    view.set_precision_placement_enabled(True)
    view.set_precision_placement_enabled(False)
    assert view.snap_enabled is snap
    view.close()


def _controller():
    settings = SimpleNamespace(
        machine=MachineSettings(work_area=WorkArea(0, 100, 0, 80)),
        calibration=CalibrationSettings(), laser=LaserSettings(),
    )
    state = {"surface": ("model-a", "measurement-a", 12.5)}
    context = SimpleNamespace(
        bed=SimpleNamespace(calibration=SimpleNamespace(
            image_to_machine=np.eye(3), image_width=800, image_height=640,
        )),
        bed_mapping_digest=lambda: "bed-map",
        material_surface_signature=lambda: state["surface"],
        material_surface_metadata=lambda: {"model_id": "model-a", "elevation_mm": 12.5},
        precision_pixels_per_mm=lambda: 8.0,
        trace_camera_work_area=lambda: settings.machine.work_area,
    )
    return DesktopController(SimpleNamespace(context=context, settings=settings, running=True)), state


def test_height_identity_invalidates_review_without_changing_support(application):
    controller, state = _controller()
    before = controller._current_review_signature()
    state["surface"] = ("model-a", "measurement-b", 22.0)
    assert not controller.review_signature_is_current(before)
    assert controller._current_review_signature()[:3] == before[:3]
    state["surface"] = None
    assert not controller.review_signature_is_current(before)
    assert controller._precision_capture_options(WorkArea(0, 100, 0, 80)) == {"pixels_per_mm": 8.0}
    controller.deleteLater()


def test_measured_template_alignment_uses_exact_frozen_capture(application, monkeypatch):
    controller, state = _controller()
    document = ProjectDocument.new(work_area=Bounds(0, 0, 100, 80))
    for x in (20, 40, 60):
        document.add_object(SceneObject.rectangle(document.active_layer_id, center=(x, 30)))
    template = template_from_project(document, "Measured surface")
    with pytest.raises(ValueError, match="Capture for placement"):
        controller._match_cut_templates_once(1, (template,), template.id)
    source = np.zeros((640, 800, 3), np.uint8)
    source.setflags(write=False)
    controller._placement_capture = {
        "image": source, "area": WorkArea(0, 100, 0, 80), "pixels_per_mm": 8.0,
        "review_signature": controller._current_review_signature(),
    }

    def detect(image, options, area, ppm, **kwargs):
        assert image is source
        assert options.precision_placement
        assert ppm == 8.0
        raise RuntimeError("Reached detector with frozen evidence")

    monkeypatch.setattr(controller_module, "detect_objects", detect)
    with pytest.raises(RuntimeError, match="frozen evidence"):
        controller._match_cut_templates_once(2, (template,), template.id)
    state["surface"] = ("model-a", "new-measurement", 15.0)
    with pytest.raises(ValueError, match="Capture for placement"):
        controller._match_cut_templates_once(3, (template,), template.id)
    controller.deleteLater()


def test_trace_keeps_detail_ppm_source_and_surface_identity(application, monkeypatch):
    controller, state = _controller()
    source = np.zeros((640, 800, 3), np.uint8)
    captures = []

    def capture(**kwargs):
        captures.append(kwargs)
        return source

    controller.runtime.context.capture_parked_trace_frame = capture
    observed = []

    def detect(image, options, area, ppm, **kwargs):
        observed.append((image, options.precision_placement, ppm))
        return SimpleNamespace(
            detections=[],
            to_dict=lambda: {"detections": [], "message": "No objects", "diagnostics": {}},
        )

    monkeypatch.setattr(controller_module, "detect_objects", detect)
    monkeypatch.setattr(controller, "_run", lambda operation, **kwargs: kwargs["on_success"](operation()))
    results = []
    controller.traceResultReady.connect(results.append)
    controller.detect_trace_objects({"regular_grid": False})
    assert captures[0]["pixels_per_mm"] == 8.0
    assert observed[0][0] is source
    assert observed[0][1:] == (True, 8.0)
    assert results[0]["pixels_per_mm"] == 8.0
    assert results[0]["material_surface"]["elevation_mm"] == 12.5
    assert results[0]["review_signature"][-1] == state["surface"]
    assert controller._trace_sample_pixels_per_mm == 8.0
    samples = []

    def sample(image, x, y, **kwargs):
        samples.append((x, y))
        return {}

    monkeypatch.setattr(controller_module, "sample_color", sample)
    controller.sample_trace_color(10, 20)
    assert samples == [(80, 480)]
    controller.deleteLater()


def test_precision_detail_rejects_invalid_model_and_can_be_disabled(application):
    controller, _ = _controller()
    controller.runtime.context.precision_pixels_per_mm = lambda: float("nan")
    with pytest.raises(ValueError, match="source resolution"):
        controller._precision_capture_options(WorkArea(0, 100, 0, 80))
    controller.runtime.context.precision_pixels_per_mm = lambda: 80.0
    with pytest.raises(ValueError, match="16-million-pixel"):
        controller._precision_capture_options(WorkArea(0, 100, 0, 80))
    controller.set_precision_placement_enabled(False)
    assert controller._precision_capture_options(WorkArea(0, 100, 0, 80)) == {}
    controller.deleteLater()


def test_approximate_support_signature_is_accepted_only_for_probe_review(application):
    controller, _ = _controller()

    def unmeasured():
        raise CalibrationError("Measure the surface")

    controller.runtime.context.material_surface_signature = unmeasured
    controller.runtime.context.material_preview_signature = lambda: ("approximate-support", "model-a")
    signature = controller._current_review_signature(preview_only=True)
    assert controller.focus_review_signature_is_current(signature)
    assert not controller.review_signature_is_current(signature)
    controller.deleteLater()


@pytest.mark.parametrize("change_surface", [False, True])
def test_placement_capture_is_frozen_or_rejected_when_surface_changes(application, monkeypatch, change_surface):
    controller, state = _controller()
    requests = []

    def capture(**options):
        requests.append(options)
        if change_surface:
            state["surface"] = ("model-a", "new-measurement", 15.0)
        return np.zeros((640, 800, 3), np.uint8)

    controller.runtime.context.capture_parked_trace_frame = capture

    def run(operation, **callbacks):
        assert callbacks["requires_controller"] is True
        try:
            callbacks["on_success"](operation())
        except Exception as exc:
            callbacks["on_failure"](str(exc))

    monkeypatch.setattr(controller, "_run", run)
    images, errors = [], []
    controller.cameraImageReady.connect(images.append)
    controller.errorOccurred.connect(errors.append)
    controller.capture_for_placement()
    assert requests[0]["pixels_per_mm"] == 8.0
    assert bool(images) is not change_surface
    assert bool(errors) is change_surface
    assert controller._camera_review_active() is not change_surface
    controller.release_placement_capture()
    assert not controller._camera_review_active()
    controller.deleteLater()


@pytest.mark.parametrize("change", [None, "surface", "capture", "missing"])
def test_manual_template_review_binds_surface_and_frozen_capture(application, change):
    from test_desktop_template_widgets import _WindowHarness
    controller, state = _controller()
    source = ProjectDocument.new(work_area=Bounds(0, 0, 100, 80))
    source.add_object(SceneObject.rectangle(source.active_layer_id, width_mm=10.123456, height_mm=8.654321))
    template = template_from_project(source, "Known dimensions")
    window = _WindowHarness(template)
    window.controller = controller
    window.runtime = controller.runtime
    controller._placement_capture = {"review_signature": controller._current_review_signature()}
    errors = []
    window.show_error = errors.append
    window.workspace.set_template_preview = lambda *args, **kwargs: None
    window._update_template_match_adjustment = lambda payload: None
    payload = {"template_id": template.id, "center_x_mm": 40.123456, "center_y_mm": 40., "rotation_deg": .123456}
    E3MainWindow._template_placement_changed(window, payload)
    if change == "surface":
        state["surface"] = ("changed-model", "next-measurement", 15.)
    elif change == "capture":
        controller._placement_capture_epoch += 1
    elif change == "missing":
        controller._placement_capture = None
    E3MainWindow._apply_template_objects(window, payload)
    if change is not None:
        assert errors and not window.document.objects
    else:
        assert not errors
        created = window.document.objects[0]
        assert created.transform.width_mm == pytest.approx(10.123456)
        assert created.transform.height_mm == pytest.approx(8.654321)
        assert created.metadata["material_surface"] == controller.runtime.context.material_surface_metadata()
        assert window.document.metadata["material_surface"] == created.metadata["material_surface"]
    controller.deleteLater()


@pytest.mark.parametrize("changed", [False, True])
def test_start_rechecks_capture_epoch_before_controller_preflight(application, monkeypatch, changed):
    controller, _ = _controller()
    controller._placement_capture = {"review_signature": controller._current_review_signature()}
    signature = controller.placement_capture_signature()
    events, queued = [], []
    context = controller.runtime.context
    context.validate_powered_calibration_support = lambda *args: None
    context.validate_material_surface_program = lambda text: None
    context.machine = SimpleNamespace(
        preflight_program=lambda text: events.append("preflight") or object(),
        start_preflighted_program=lambda *args, **kwargs: events.append("start") or {},
        disarm=lambda: events.append("disarm"),
    )
    monkeypatch.setattr(controller, "_run", lambda operation, **kwargs: queued.append(operation))
    controller.run_job("G21\nG90\nM5\nM5", "reviewed", placement_capture_signature=signature)
    if changed:
        controller._placement_capture_epoch += 1
        with pytest.raises(ValueError, match="photograph changed"):
            queued[0]()
        assert events == ["disarm"]
    else:
        queued[0]()
        assert events == ["preflight", "start"]
    controller.deleteLater()


def test_height_aware_capture_signature_requires_photo_and_preserves_legacy(application):
    controller, state = _controller()
    with pytest.raises(ValueError, match="Capture for placement"):
        controller.placement_capture_signature()
    controller._placement_capture = {"review_signature": controller._current_review_signature()}
    assert controller.placement_capture_signature() is not None
    state["surface"] = None
    assert controller.placement_capture_signature() is None
    controller.deleteLater()
