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
    assert "context.rectified_frame(refresh=True)" in controller
    assert "rank_templates(grouped_templates, trace_result.detections)" in controller
    assert "CutTemplate.from_dict(template.to_dict())" in controller
    assert "self._run(" in controller
    assert "request_id != self._template_match_request_id" in controller
    assert "def cancel_template_match(" in controller
    assert "def set_template_review_active(" in controller
    assert "or self._template_review_active" in controller
    assert "image = context.rectified_frame(refresh=True)" in controller
    assert "image_to_qimage(image)" in controller


def test_main_window_wires_template_review_and_one_batch_application() -> None:
    window = source("main_window.py")
    workspace = source("workspace.py")

    assert 'action("save_template", "Save project as cutting template' in window
    assert 'add_panel("templates", "Templates", self.template_panel)' in window
    assert "self.template_panel.autoMatchRequested.connect(" in window
    assert "self.template_panel.templateSelected.connect(self._template_selected)" in window
    assert "self.template_panel.placementChanged.connect(" in window
    assert "self.template_panel.applyRequested.connect(self._apply_template_objects)" in window
    assert "self.controller.templateMatchReady.connect(" in window
    assert "template_from_project(" in window
    assert "self.document.clone()" in window
    assert "catalog = self.template_library.scan()" in window
    assert "catalog.diagnostics" in window
    assert "instantiate_template(" in window
    assert "target_layer_id=self.active_layer_id" in window
    assert "AddObjectsCommand(" in window
    assert 'description=f"Apply {template.name} template"' in window
    assert "def set_template_preview(" in workspace
    assert "detections: list[dict[str, Any]] | None = None" in workspace
    assert "last_job_revision != self.document.revision" in window
    assert "def _invalidate_generated_job(" in window
    assert "regenerate the toolpath before running" in window
