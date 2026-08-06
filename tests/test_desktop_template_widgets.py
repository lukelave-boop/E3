from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

import laser_aligner.desktop.controller as controller_module
from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.template_panel import TemplatePanel
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import Bounds, CommandStack, ProjectDocument, SceneObject
from laser_aligner.templates import (
    CutTemplate,
    RectangleGridSpec,
    template_from_project,
)
from laser_aligner.vision.object_trace import TraceOptions
from laser_aligner.vision.template_alignment import TemplateAlignment


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _template_summary(template_id: str, name: str) -> dict[str, object]:
    return {
        "id": template_id,
        "name": name,
        "description": f"{name} description",
        "feature_count": 12,
        "width_mm": 90.0,
        "height_mm": 60.0,
    }


class _WorkspaceHarness:
    def __init__(self) -> None:
        self.selected_ids: list[str] = []
        self.clear_count = 0
        self.toolpath_clear_count = 0

    def select_objects(self, object_ids: list[str]) -> None:
        self.selected_ids = list(object_ids)

    def clear_template_preview(self) -> None:
        self.clear_count += 1

    def clear_toolpath_preview(self) -> None:
        self.toolpath_clear_count += 1

    def selected_object_ids(self) -> list[str]:
        return list(self.selected_ids)


class _WindowHarness:
    def __init__(self, template: CutTemplate) -> None:
        self.document = ProjectDocument.new(
            work_area=Bounds(0.0, 0.0, 220.0, 220.0)
        )
        self.active_layer_id = self.document.active_layer_id
        self.history = CommandStack()
        self.workspace = _WorkspaceHarness()
        self.controller = SimpleNamespace(cancel_template_match=lambda: None)
        self._templates = {template.id: template}
        self._template_match_result: dict[str, Any] | None = None
        self.notices: list[str] = []
        self.trace_panel = SimpleNamespace(options=lambda: TraceOptions().to_dict())
        self.selected_panels: list[str] = []
        self.inspector_tabs = SimpleNamespace(
            select_panel=self.selected_panels.append
        )

    def _clear_template_preview(self, show_message: bool = True) -> None:
        E3MainWindow._clear_template_preview(self, show_message)

    def show_notice(self, message: str) -> None:
        self.notices.append(message)

    def show_error(self, message: str) -> None:
        raise AssertionError(message)

    def _document_center(self) -> tuple[float, float]:
        return self.document.work_area.center


class _ActionHarness:
    def __init__(self) -> None:
        self.enabled = False
        self.text = ""

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def setText(self, text: str) -> None:
        self.text = str(text)


class _StaleJobHarness:
    def __init__(self) -> None:
        self.document = ProjectDocument.new(
            work_area=Bounds(0.0, 0.0, 220.0, 220.0)
        )
        self.history = CommandStack()
        self.workspace = _WorkspaceHarness()
        self.actions = {"undo": _ActionHarness(), "redo": _ActionHarness()}
        self.last_job: Any | None = SimpleNamespace(text="G21\nG90\nM5\n")
        self.last_job_name = "stale.gcode"
        self.last_job_powered = False
        self.last_job_revision: int | None = self.document.revision
        self.errors: list[str] = []
        self.refreshes: list[list[str]] = []
        self.machine_status_calls = 0
        self.run_calls: list[tuple[object, ...]] = []
        self.gcode_preview = SimpleNamespace(clear=lambda: None)
        self.job_panel = SimpleNamespace(
            summary=SimpleNamespace(setText=lambda text: None)
        )

        def machine_status() -> dict[str, object]:
            self.machine_status_calls += 1
            return {"connected": True}

        self.runtime = SimpleNamespace(
            context=SimpleNamespace(
                machine=SimpleNamespace(status=machine_status)
            ),
            settings=SimpleNamespace(
                machine=SimpleNamespace(allow_motion=True)
            ),
        )
        self.controller = SimpleNamespace(
            run_job=lambda *args, **kwargs: self.run_calls.append((args, kwargs))
        )

    def _invalidate_generated_job(self) -> None:
        E3MainWindow._invalidate_generated_job(self)

    def _refresh_document(self, selected_ids: list[str] | None = None) -> None:
        self.refreshes.append(list(selected_ids or []))

    def show_error(self, message: str) -> None:
        self.errors.append(message)


class _FocusHarness:
    def __init__(self) -> None:
        self._template_match_result = None
        self.focus_payloads: list[dict[str, Any]] = []
        self.clear_count = 0
        self.notices: list[str] = []
        self.camera_panel = SimpleNamespace(
            set_focus_result=lambda payload: self.focus_payloads.append(payload)
        )

    def _clear_template_preview(self, show_message: bool = True) -> None:
        del show_message
        self.clear_count += 1

    def show_notice(self, message: str) -> None:
        self.notices.append(message)


def _alignment(**changes: Any) -> TemplateAlignment:
    payload: dict[str, Any] = {
        "template_id": "template-a",
        "template_name": "Alpha labels",
        "rotation_deg": 0.0,
        "translation_mm": (50.0, 50.0),
        "matched_count": 3,
        "direct_match_count": 3,
        "inferred_match_count": 0,
        "feature_count": 3,
        "detection_count": 3,
        "coverage": 1.0,
        "weighted_coverage": 1.0,
        "detection_coverage": 1.0,
        "rms_error_mm": 0.1,
        "max_error_mm": 0.2,
        "scale_ratio": 1.0,
        "dimension_scale_ratio": 1.0,
        "confidence": 0.9,
        "score": 90.0,
        "matches": ((0, 0, 0.1), (1, 1, 0.1), (2, 2, 0.1)),
    }
    payload.update(changes)
    return TemplateAlignment(**payload)


def test_template_panel_gates_matching_and_emits_reviewed_placement(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TemplatePanel()
    panel.set_templates(
        [
            _template_summary("template-z", "Zulu labels"),
            _template_summary("template-a", "Alpha labels"),
        ],
        selected_id="template-z",
    )

    assert panel.template_combo.itemText(0) == "Alpha labels"
    assert panel.current_template_id() == "template-z"
    assert not panel.auto_button.isEnabled()
    assert not panel.match_selected_button.isEnabled()
    assert not panel.apply_button.isEnabled()

    automatic_requests: list[bool] = []
    selected_requests: list[str] = []
    placement_changes: list[dict[str, object]] = []
    apply_requests: list[dict[str, object]] = []
    panel.autoMatchRequested.connect(lambda: automatic_requests.append(True))
    panel.matchSelectedRequested.connect(selected_requests.append)
    panel.placementChanged.connect(placement_changes.append)
    panel.applyRequested.connect(apply_requests.append)

    panel.set_calibration_ready(True)
    assert panel.auto_button.isEnabled()
    assert panel.match_selected_button.isEnabled()
    panel.auto_button.click()
    panel.match_selected_button.click()
    assert automatic_requests == [True]
    assert selected_requests == ["template-z"]

    panel.set_busy(True)
    assert not panel.auto_button.isEnabled()
    assert not panel.match_selected_button.isEnabled()
    panel.set_busy(False)

    panel.set_match_result(
        {
            "template_id": "template-z",
            "center_x_mm": 42.5,
            "center_y_mm": 73.25,
            "rotation_deg": 2.0,
            "confidence": 0.94,
            "rms_error_mm": 0.18,
            "matched_count": 11,
            "feature_count": 12,
        }
    )
    assert panel.apply_button.isEnabled()
    assert panel.x_spin.value() == pytest.approx(42.5)
    assert panel.y_spin.value() == pytest.approx(73.25)
    assert panel.rotation_spin.value() == pytest.approx(2.0)
    assert "94%" in panel.match_status.text()
    assert placement_changes[-1]["template_id"] == "template-z"

    panel.nudge_step.setValue(0.25)
    x_plus = next(
        button
        for button in panel.findChildren(QtWidgets.QToolButton)
        if button.text() == "X+"
    )
    rotation_minus = next(
        button
        for button in panel.findChildren(QtWidgets.QToolButton)
        if button.text() == "R−"
    )
    x_plus.click()
    rotation_minus.click()
    assert panel.x_spin.value() == pytest.approx(42.75)
    assert panel.rotation_spin.value() == pytest.approx(1.75)
    assert placement_changes[-1]["rotation_deg"] == pytest.approx(1.75)

    panel.apply_button.click()
    assert apply_requests == [panel.placement()]

    panel.clear_placement()
    assert not panel.apply_button.isEnabled()
    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_template_panel_reports_manual_override_selection(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TemplatePanel()
    panel.set_templates(
        [
            _template_summary("template-a", "Alpha labels"),
            _template_summary("template-b", "Beta labels"),
        ],
        selected_id="template-a",
    )
    selected: list[str] = []
    panel.templateSelected.connect(selected.append)

    panel.template_combo.setCurrentIndex(panel.template_combo.findData("template-b"))

    assert selected == ["template-b"]
    assert panel.current_template_id() == "template-b"
    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_template_panel_rejects_unknown_or_deleted_match_ids(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TemplatePanel()
    panel.set_templates([_template_summary("template-a", "Alpha labels")])
    panel.set_placement(20.0, 30.0, 1.0, emit=False)
    assert panel.apply_button.isEnabled()

    apply_requests: list[dict[str, object]] = []
    panel.applyRequested.connect(apply_requests.append)
    panel.set_match_result(
        {
            "template_id": "template-deleted",
            "center_x_mm": 80.0,
            "center_y_mm": 90.0,
            "rotation_deg": 4.0,
            "confidence": 0.99,
        }
    )

    assert panel.current_template_id() == "template-a"
    assert not panel.apply_button.isEnabled()
    assert "no longer in the library" in panel.match_status.text()
    panel.apply_button.click()
    assert apply_requests == []

    panel.set_templates([])
    panel.set_match_result(
        {
            "template_id": "template-a",
            "center_x_mm": 50.0,
            "center_y_mm": 50.0,
            "rotation_deg": 0.0,
        }
    )
    assert panel.current_template_id() is None
    assert not panel.apply_button.isEnabled()
    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_controller_rejects_a_single_feature_template_match() -> None:
    alignment = _alignment(
        matched_count=1,
        direct_match_count=1,
        feature_count=6,
        detection_count=1,
        coverage=1.0 / 6.0,
        weighted_coverage=1.0 / 6.0,
        detection_coverage=1.0,
        matches=((0, 0, 0.1),),
    )

    reasons = DesktopController._template_viability_reasons(alignment)

    assert any("at least 3 matched features" in reason for reason in reasons)
    assert any("at least 2 direct detections" in reason for reason in reasons)
    assert any("feature coverage must be at least 50%" in reason for reason in reasons)


def test_controller_rejects_feature_dimension_scale_mismatch() -> None:
    alignment = _alignment(dimension_scale_ratio=1.08)

    reasons = DesktopController._template_viability_reasons(alignment)

    assert any("feature dimensions" in reason for reason in reasons)


def test_controller_cancellation_freezes_and_restores_camera_delivery(
    qt_application: QtWidgets.QApplication,
) -> None:
    runtime = SimpleNamespace(running=True)
    controller = DesktopController(runtime)
    controller._template_match_request_id = 7
    delivered_images: list[object] = []
    delivered_matches: list[dict[str, object]] = []
    controller.cameraImageReady.connect(delivered_images.append)
    controller.templateMatchReady.connect(delivered_matches.append)
    image = controller_module.QtGui.QImage(
        2,
        2,
        controller_module.QtGui.QImage.Format.Format_RGB888,
    )

    controller.set_template_review_active(True)
    controller._camera_refresh_ready(image)
    controller._template_match_complete(6, {"request_id": 6})
    assert controller._template_review_active
    assert delivered_images == []
    assert delivered_matches == []

    controller.cancel_template_match()
    assert controller._template_match_request_id == 8
    assert not controller._template_review_active
    controller._camera_refresh_ready(image)
    controller._template_match_complete(7, {"request_id": 7})
    controller._template_match_complete(8, {"request_id": 8})
    assert delivered_images == [image]
    assert delivered_matches == [{"request_id": 8}]
    controller.deleteLater()
    qt_application.processEvents()


def test_controller_uses_one_frozen_frame_for_all_template_option_groups(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_document = ProjectDocument.new(
        work_area=Bounds(0.0, 0.0, 220.0, 220.0)
    )
    for index in range(3):
        source_document.add_object(
            SceneObject.rectangle(
                source_document.active_layer_id,
                center=(20.0 + index * 20.0, 30.0),
                width_mm=10.0,
                height_mm=8.0,
            )
        )
    first = template_from_project(
        source_document,
        "Automatic labels",
        trace_options={"detection_mode": "auto"},
    )
    second = template_from_project(
        source_document,
        "Contrast labels",
        trace_options={"detection_mode": "contrast"},
    )
    frame = np.zeros((24, 24, 3), dtype=np.uint8)

    class Context:
        def __init__(self) -> None:
            self.frame_calls = 0
            self.bed = SimpleNamespace(
                calibration=SimpleNamespace(
                    image_to_machine=np.eye(3),
                    image_width=24,
                    image_height=24,
                )
            )

        def rectified_frame(self, refresh: bool = True) -> np.ndarray:
            assert refresh
            self.frame_calls += 1
            return frame

    context = Context()
    runtime = SimpleNamespace(
        running=True,
        context=context,
        settings=SimpleNamespace(
            calibration=SimpleNamespace(
                bed=SimpleNamespace(pixels_per_mm=1.0)
            ),
            machine=SimpleNamespace(
                work_area=SimpleNamespace(
                    x_min=0.0,
                    y_min=0.0,
                    x_max=220.0,
                    y_max=220.0,
                )
            ),
        ),
    )
    seen_images: list[np.ndarray] = []

    def fake_detect(
        image: np.ndarray,
        options: TraceOptions,
        work_area: object,
        pixels_per_mm: float,
    ) -> SimpleNamespace:
        del work_area
        assert pixels_per_mm == 1.0
        seen_images.append(image)
        return SimpleNamespace(
            detections=[],
            mode_used=options.detection_mode,
            message="Synthetic trace",
            direct_count=3,
            inferred_count=0,
            options=options,
        )

    def fake_rank(
        templates: list[CutTemplate],
        detections: list[object],
    ) -> list[TemplateAlignment]:
        del detections
        template = templates[0]
        score = 90.0 if template.id == first.id else 30.0
        return [
            _alignment(
                template_id=template.id,
                template_name=template.name,
                score=score,
            )
        ]

    monkeypatch.setattr(controller_module, "detect_objects", fake_detect)
    monkeypatch.setattr(controller_module, "rank_templates", fake_rank)
    controller = DesktopController(runtime)

    payload = controller._match_cut_templates_once(11, (first, second), None)

    assert context.frame_calls == 1
    assert len(seen_images) == 2
    assert all(image is frame for image in seen_images)
    assert payload["matched"] is True
    assert payload["template_id"] == first.id
    assert not payload["camera_image"].isNull()
    controller.deleteLater()
    qt_application.processEvents()


def test_main_window_applies_template_as_one_undoable_batch(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    source_document = ProjectDocument.new(
        work_area=Bounds(0.0, 0.0, 220.0, 220.0)
    )
    source_document.add_object(
        SceneObject.rectangle(
            source_document.active_layer_id,
            name="Left label",
            center=(20.0, 20.0),
            width_mm=10.0,
            height_mm=8.0,
        )
    )
    source_document.add_object(
        SceneObject.rectangle(
            source_document.active_layer_id,
            name="Right label",
            center=(40.0, 20.0),
            width_mm=10.0,
            height_mm=8.0,
        )
    )
    template = template_from_project(source_document, "Two-label sheet")
    window = _WindowHarness(template)

    E3MainWindow._apply_template_objects(
        window,
        {
            "template_id": template.id,
            "center_x_mm": 100.0,
            "center_y_mm": 100.0,
            "rotation_deg": 0.0,
        },
    )

    created_ids = [item.id for item in window.document.objects]
    assert len(created_ids) == 2
    assert window.history.depth == 1
    assert window.history.undo_text == "Apply Two-label sheet template"
    assert all(
        item.layer_id == window.active_layer_id
        for item in window.document.objects
    )
    assert sorted(item.transform.x_mm for item in window.document.objects) == [90.0, 110.0]
    assert window.workspace.selected_ids == created_ids
    assert window.workspace.clear_count == 1

    assert window.history.undo()
    assert window.document.objects == []
    assert window.history.redo()
    assert [item.id for item in window.document.objects] == created_ids


def test_main_window_adds_designed_grid_as_one_undoable_batch() -> None:
    source_document = ProjectDocument.new()
    source_document.add_object(
        SceneObject.rectangle(source_document.active_layer_id)
    )
    window = _WindowHarness(template_from_project(source_document, "Seed"))
    spec = RectangleGridSpec(
        name="Six labels",
        rows=2,
        columns=3,
        width_mm=20.0,
        height_mm=10.0,
        corner_radius_mm=2.0,
        horizontal_gap_mm=4.0,
        vertical_gap_mm=6.0,
    )

    E3MainWindow._add_rectangle_grid_to_project(window, spec)

    assert len(window.document.objects) == 6
    assert window.history.depth == 1
    assert window.history.undo_text == "Create Six labels grid"
    assert window.workspace.selected_ids == [
        item.id for item in window.document.objects
    ]
    assert window.workspace.clear_count == 1
    assert window.selected_panels == ["objects"]
    assert {
        item.geometry["corner_radius_mm"] for item in window.document.objects
    } == {2.0}
    assert window.history.undo()
    assert window.document.objects == []


def test_main_window_accepts_float_noise_at_work_area_boundary() -> None:
    source_document = ProjectDocument.new()
    source_document.add_object(
        SceneObject.rectangle(source_document.active_layer_id)
    )
    window = _WindowHarness(template_from_project(source_document, "Seed"))
    exact_fit = RectangleGridSpec(
        name="Exact fit",
        rows=1,
        columns=3,
        width_mm=66.668,
        height_mm=10.0,
        horizontal_gap_mm=9.998,
    )

    E3MainWindow._add_rectangle_grid_to_project(window, exact_fit)

    assert len(window.document.objects) == 3
    assert window.history.depth == 1

    outside = RectangleGridSpec(
        name="Outside",
        rows=1,
        columns=3,
        width_mm=66.668,
        height_mm=10.0,
        horizontal_gap_mm=9.999,
    )
    with pytest.raises(ValueError, match="does not fit"):
        E3MainWindow._add_rectangle_grid_to_project(window, outside)
    assert len(window.document.objects) == 3
    assert window.history.depth == 1


def test_main_window_edits_rectangle_size_and_radius_in_one_history_step() -> None:
    source_document = ProjectDocument.new()
    source_document.add_object(
        SceneObject.rectangle(source_document.active_layer_id)
    )
    window = _WindowHarness(template_from_project(source_document, "Seed"))
    rectangle = SceneObject.rectangle(
        window.document.active_layer_id,
        center=(50.0, 60.0),
        width_mm=30.0,
        height_mm=20.0,
        corner_radius_mm=3.0,
    )
    window.document.add_object(rectangle)
    before_revision = window.document.revision

    E3MainWindow._rectangle_shape_edited(
        window,
        rectangle.id,
        rectangle.transform.copy(width_mm=24.0, height_mm=16.0),
        5.0,
    )

    edited = window.document.get_object(rectangle.id)
    assert edited.transform.width_mm == pytest.approx(24.0)
    assert edited.transform.height_mm == pytest.approx(16.0)
    assert edited.geometry["corner_radius_mm"] == pytest.approx(5.0)
    assert window.document.revision == before_revision + 1
    assert window.history.depth == 1
    assert window.history.undo()
    restored = window.document.get_object(rectangle.id)
    assert restored.transform.width_mm == pytest.approx(30.0)
    assert restored.transform.height_mm == pytest.approx(20.0)
    assert restored.geometry["corner_radius_mm"] == pytest.approx(3.0)


def test_document_revision_change_invalidates_generated_job() -> None:
    window = _StaleJobHarness()
    generated_revision = window.document.revision
    window.last_job_revision = generated_revision
    window.document.add_object(
        SceneObject.rectangle(
            window.document.active_layer_id,
            center=(50.0, 50.0),
        )
    )
    assert window.document.revision != generated_revision

    E3MainWindow._history_changed(window, window.history)

    assert window.last_job is None
    assert window.last_job_revision is None
    assert window.last_job_name == ""
    assert window.workspace.toolpath_clear_count == 1
    assert len(window.refreshes) == 1


def test_run_current_job_refuses_a_stale_generated_revision() -> None:
    window = _StaleJobHarness()
    window.last_job_revision = window.document.revision - 1

    E3MainWindow.run_current_job(window)

    assert window.last_job is None
    assert window.machine_status_calls == 0
    assert window.run_calls == []
    assert window.errors == [
        "The project changed; regenerate the toolpath before running"
    ]


def test_focus_change_cancels_an_in_flight_template_match() -> None:
    window = _FocusHarness()
    payload = {"changed": True, "focus_absolute": 55}

    E3MainWindow._camera_focus_changed(window, payload)

    assert window.focus_payloads == [payload]
    assert window.clear_count == 1
    assert "Camera focus changed" in window.notices[0]


def test_workspace_template_preview_is_transient_and_independent(
    qt_application: QtWidgets.QApplication,
) -> None:
    view = WorkspaceView(Bounds(0.0, 0.0, 220.0, 220.0))
    layer_id = "preview-layer"
    rectangle = SceneObject.rectangle(
        layer_id,
        name="Template rectangle",
        center=(12.0, 34.0),
        width_mm=20.0,
        height_mm=10.0,
        corner_radius_mm=2.0,
    )
    rectangle.transform = rectangle.transform.copy(rotation_deg=17.0)
    ellipse = SceneObject.ellipse(
        layer_id,
        name="Template ellipse",
        center=(80.0, 55.0),
        width_mm=14.0,
        height_mm=9.0,
    )

    view.set_trace_preview(
        [
            {
                "id": "trace-1",
                "index": 1,
                "source": "direct",
                "center_mm": [20.0, 20.0],
                "contour_mm": [[15.0, 15.0], [25.0, 15.0], [25.0, 25.0]],
            }
        ],
        {"trace-1"},
    )
    trace_count = len(view._trace_items)
    view.set_toolpath_preview("G90\nM5\nG0 X5 Y5\nG1 X10 Y10\nM5\n")
    toolpath_count = len(view._toolpath_items)
    assert toolpath_count == 2

    view.set_template_preview(
        [rectangle, ellipse],
        detections=[
            {
                "contour_mm": [
                    [10.0, 10.0],
                    [20.0, 10.0],
                    [20.0, 20.0],
                ]
            }
        ],
    )

    assert len(view._template_items) == 3
    assert len(view._trace_items) == trace_count
    assert len(view._toolpath_items) == toolpath_count
    observed = next(
        item
        for item in view._template_items
        if item.toolTip() == "Observed camera feature"
    )
    first = next(
        item
        for item in view._template_items
        if item.toolTip() == "Template preview: Template rectangle"
    )
    assert observed.zValue() == pytest.approx(279.0)
    assert observed.acceptedMouseButtons() == QtCore.Qt.MouseButton.NoButton
    assert first.pos().x() == pytest.approx(12.0)
    assert first.pos().y() == pytest.approx(-34.0)
    assert first.rotation() == pytest.approx(-17.0)
    assert first.zValue() == pytest.approx(280.0)
    assert first.acceptedMouseButtons() == QtCore.Qt.MouseButton.NoButton
    assert not bool(
        first.flags()
        & QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    )
    assert view.selected_object_ids() == []

    previous_items = list(view._template_items)
    view.set_template_preview([ellipse])
    assert len(view._template_items) == 1
    assert all(item.scene() is None for item in previous_items)
    assert len(view._trace_items) == trace_count
    assert len(view._toolpath_items) == toolpath_count

    current_item = view._template_items[0]
    view.clear_template_preview()
    assert view._template_items == []
    assert current_item.scene() is None
    assert len(view._trace_items) == trace_count
    assert len(view._toolpath_items) == toolpath_count

    view.clear_trace_preview()
    view.clear_toolpath_preview()
    view.close()
    view.deleteLater()
    qt_application.processEvents()
