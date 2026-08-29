from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "laser_aligner" / "desktop"


def source(name: str) -> str:
    text = (DESKTOP / name).read_text(encoding="utf-8")
    ast.parse(text, filename=name)
    return text


def test_controller_matches_templates_off_thread_and_ignores_stale_results() -> None:
    controller = source("controller.py")

    assert "templateMatchReady = QtCore.Signal(dict)" in controller
    assert "def match_cut_templates(" in controller
    assert "def _match_cut_templates_once(" in controller
    assert "refresh=True," in controller
    assert "_usable_template_detections(trace_result.detections)" in controller
    assert "rank_templates(grouped_templates, usable_detections)" in controller
    assert "CutTemplate.from_dict(template.to_dict())" in controller
    assert "self._run(" in controller
    assert "request_id != self._template_match_request_id" in controller
    assert "def cancel_template_match(" in controller
    assert "def set_template_review_active(" in controller
    assert "def _camera_review_active(" in controller
    assert "self._template_review_active or self._trace_review_active" in controller
    assert '"refresh": True' in controller
    assert '"precision": True' in controller
    assert "image = context.rectified_frame(**frame_options)" in controller
    assert "image_to_qimage(image)" in controller


def test_hardware_job_start_homes_once_without_camera_parking_before_arming() -> None:
    controller = source("controller.py")
    operation = controller[controller.index("    def run_job(") : controller.index("    def pause_resume(")]

    assert "machine.prepare_photo_position()" not in operation
    assert operation.index("machine.preflight_program(gcode)") < operation.index(
        "machine.start_preflighted_program("
    )
    assert "authorization_phrase=arm_phrase" in operation
    assert "abandon_start_attempt" in operation
    assert "machine.disarm()" in operation


def test_main_window_wires_template_review_and_one_batch_application() -> None:
    window = source("main_window.py")
    workspace = source("workspace.py")

    assert 'action("save_template", "Save project as cutting template' in window
    assert 'action("grid_template_designer", "Design grid cutting template' in window
    assert 'add_panel("templates", "Templates", self.template_panel)' in window
    assert "self.template_panel.newGridRequested.connect(" in window
    assert "self.template_panel.editGridRequested.connect(" in window
    assert "self.template_panel.autoMatchRequested.connect(" in window
    assert "self.template_panel.templateSelected.connect(self._template_selected)" in window
    assert "self.template_panel.placementChanged.connect(" in window
    assert "self.template_panel.applyRequested.connect(self._apply_template_objects)" in window
    assert "self.controller.templateMatchReady.connect(" in window
    assert "template_from_project(" in window
    assert "template_from_rectangle_grid(" in window
    assert "self.template_library.replace(" in window
    assert "expected_modified_at=existing.modified_at" in window
    assert "self.document.clone()" in window
    assert "catalog = self.template_library.scan()" in window
    assert "catalog.diagnostics" in window
    assert "instantiate_template(" in window
    assert "target_layer_id=self.active_layer_id" in window
    assert "AddObjectsCommand(" in window
    assert "self.transform_panel.rectangleShapeEdited.connect(" in window
    assert "UpdateObjectShapeCommand(" in window
    assert 'description=f"Apply {template.name} template"' in window
    assert "def set_template_preview(" in workspace
    assert "detections: list[dict[str, Any]] | None = None" in workspace
    assert "last_job_revision != self.document.revision" in window
    assert "def _invalidate_generated_job(" in window
    assert "regenerate the toolpath before running" in window


def test_desktop_has_no_generated_test_image_camera_pipeline() -> None:
    window = source("main_window.py")
    controller = source("controller.py")

    for removed in (
        "loadTestImageRequested",
        "generateTestImageRequested",
        "returnToCameraRequested",
        "simulationFrameChanged",
        "load_corrected_test_image",
        "generate_template_test_frame",
        "activate_simulation_workspace_frame",
        "return_to_synthetic_camera",
    ):
        assert removed not in window
        assert removed not in controller
