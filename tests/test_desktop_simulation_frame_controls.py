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

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.config import WorkArea
from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import CameraPanel
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import Bounds
from laser_aligner.templates import (
    RectangleGridSpec,
    generate_template_test_frame,
    template_from_rectangle_grid,
)
from laser_aligner.vision.object_trace import TraceOptions


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _SimulationFrameContext:
    def __init__(self) -> None:
        self.has_simulation_workspace_frame = False
        self.bed = SimpleNamespace(calibration=object())
        self.frame = np.full((8, 8, 3), (20, 80, 180), dtype=np.uint8)

    def set_simulation_workspace_frame(
        self,
        image: np.ndarray,
        *,
        source_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.has_simulation_workspace_frame = True
        self.frame = image.copy()
        return {
            "active": True,
            "source_name": source_name,
            "metadata": dict(metadata or {}),
        }

    def clear_simulation_workspace_frame(self) -> None:
        self.has_simulation_workspace_frame = False

    def rectified_frame(self, refresh: bool = True) -> np.ndarray:
        del refresh
        return self.frame.copy()


def _controller() -> tuple[DesktopController, _SimulationFrameContext]:
    context = _SimulationFrameContext()
    runtime = SimpleNamespace(running=True, context=context)
    return DesktopController(runtime), context


def test_controller_drops_stale_camera_refresh_delivery_and_cleanup(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    delivered: list[QtGui.QImage] = []
    controller.cameraImageReady.connect(delivered.append)
    image = QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888)
    controller._camera_source_generation = 4

    controller._camera_refresh_ready(image, 3)
    assert delivered == []
    controller._camera_refresh_ready(image, 4)
    assert delivered == [image]

    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    controller._camera_refresh_failed("old source unavailable", 3)
    assert errors == []
    controller._camera_refresh_failed("current source unavailable", 4)
    assert errors == [
        "Corrected bed-image refresh failed: current source unavailable"
    ]

    controller._camera_refresh_in_flight = True
    controller._camera_refresh_generation = 4
    controller._camera_refresh_finished(3)
    assert controller._camera_refresh_in_flight
    assert controller._camera_refresh_generation == 4
    controller._camera_refresh_finished(4)
    assert not controller._camera_refresh_in_flight
    assert controller._camera_refresh_generation is None

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_trace_requests_hold_camera_until_clear_and_reject_stale_callbacks(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    controller._live_camera_enabled = True
    controller._sync_camera_timer()
    assert controller._camera_live_timer.isActive()

    launched: list[dict[str, Any]] = []

    def fake_run(callback: Any, **kwargs: Any) -> object:
        del callback
        launched.append(kwargs)
        return object()

    controller._run = fake_run  # type: ignore[method-assign]
    refreshes: list[bool] = []
    controller.refresh_camera_image = lambda: refreshes.append(True)  # type: ignore[method-assign]
    delivered_images: list[QtGui.QImage] = []
    delivered_results: list[dict[str, Any]] = []
    errors: list[str] = []
    controller.cameraImageReady.connect(delivered_images.append)
    controller.traceResultReady.connect(delivered_results.append)
    controller.errorOccurred.connect(errors.append)

    first_id = controller.detect_trace_objects({"detection_mode": "auto"})
    second_id = controller.detect_trace_objects({"detection_mode": "contrast"})

    assert (first_id, second_id) == (1, 2)
    assert controller._trace_review_active
    assert not controller._camera_live_timer.isActive()

    image = QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888)
    controller._camera_refresh_ready(image, controller._camera_source_generation)
    launched[0]["on_success"]({"request_id": first_id, "message": "stale"})
    launched[0]["on_failure"]("stale failure")
    assert delivered_images == []
    assert delivered_results == []
    assert errors == []
    assert controller._trace_review_active

    current = {"request_id": second_id, "message": "ready", "detections": []}
    launched[1]["on_success"](current)
    assert delivered_results == [current]
    assert controller._trace_review_active
    assert not controller._camera_live_timer.isActive()

    controller.cancel_trace_detection()

    assert controller._trace_request_id == 3
    assert not controller._trace_review_active
    assert controller._camera_live_timer.isActive()
    assert refreshes == [True]
    controller._camera_refresh_ready(image, controller._camera_source_generation)
    assert delivered_images == [image]

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_current_trace_failure_releases_camera_but_stale_failure_does_not(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    controller._live_camera_enabled = True
    launched: list[dict[str, Any]] = []

    def fake_run(callback: Any, **kwargs: Any) -> object:
        del callback
        launched.append(kwargs)
        return object()

    controller._run = fake_run  # type: ignore[method-assign]
    refreshes: list[bool] = []
    controller.refresh_camera_image = lambda: refreshes.append(True)  # type: ignore[method-assign]
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)

    controller.detect_trace_objects({"detection_mode": "auto"})
    controller.detect_trace_objects({"detection_mode": "color"})
    launched[0]["on_failure"]("old camera failure")

    assert controller._trace_review_active
    assert errors == []
    assert refreshes == []

    launched[1]["on_failure"]("current camera failure")

    assert not controller._trace_review_active
    assert controller._camera_live_timer.isActive()
    assert refreshes == [True]
    assert errors == [
        "Detect and trace objects failed: current camera failure"
    ]

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_trace_and_template_camera_holds_release_independently(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    controller._live_camera_enabled = True
    controller._run = lambda callback, **kwargs: object()  # type: ignore[method-assign]
    refreshes: list[bool] = []
    controller.refresh_camera_image = lambda: refreshes.append(True)  # type: ignore[method-assign]

    controller.set_template_review_active(True)
    controller.detect_trace_objects({"detection_mode": "auto"})
    controller.cancel_trace_detection()

    assert controller._template_review_active
    assert not controller._trace_review_active
    assert not controller._camera_live_timer.isActive()
    assert refreshes == []

    controller.set_template_review_active(False)

    assert controller._camera_live_timer.isActive()
    assert refreshes == [True]

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_test_source_changes_invalidate_in_flight_trace_requests(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    launched: list[dict[str, Any]] = []

    def fake_run(callback: Any, **kwargs: Any) -> object:
        del callback
        launched.append(kwargs)
        return object()

    controller._run = fake_run  # type: ignore[method-assign]
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    controller.traceResultReady.connect(results.append)
    controller.errorOccurred.connect(errors.append)

    first_id = controller.detect_trace_objects({"detection_mode": "auto"})
    controller.activate_simulation_workspace_frame(
        np.zeros((8, 8, 3), dtype=np.uint8),
        source_name="Loaded labels",
    )

    assert first_id == 1
    assert controller._trace_request_id == 2
    assert not controller._trace_review_active
    launched[0]["on_success"]({"request_id": first_id})
    launched[0]["on_failure"]("old source failed")
    assert results == []
    assert errors == []

    second_id = controller.detect_trace_objects({"detection_mode": "contrast"})
    controller.refresh_camera_image = lambda: None  # type: ignore[method-assign]
    controller.return_to_synthetic_camera()

    assert second_id == 3
    assert controller._trace_request_id == 4
    assert not controller._trace_review_active
    launched[1]["on_success"]({"request_id": second_id})
    launched[1]["on_failure"]("removed source failed")
    assert results == []
    assert errors == []

    controller.deleteLater()
    qt_application.processEvents()


@pytest.mark.parametrize("live_enabled", [False, True], ids=["still", "live"])
def test_controller_test_frame_pauses_and_restores_live_timer_preference(
    qt_application: QtWidgets.QApplication,
    live_enabled: bool,
) -> None:
    controller, context = _controller()
    controller._live_camera_enabled = live_enabled
    controller._template_match_request_id = 5
    controller._sync_camera_timer()
    assert controller._camera_live_timer.isActive() is live_enabled

    refreshes: list[bool] = []
    controller.refresh_camera_image = lambda: refreshes.append(True)  # type: ignore[method-assign]
    source_changes: list[dict[str, Any]] = []
    camera_images: list[QtGui.QImage] = []
    template_matches: list[dict[str, Any]] = []
    controller.simulationFrameChanged.connect(source_changes.append)
    controller.cameraImageReady.connect(camera_images.append)
    controller.templateMatchReady.connect(template_matches.append)
    frame = np.full((8, 8, 3), (5, 100, 220), dtype=np.uint8)

    info = controller.activate_simulation_workspace_frame(
        frame,
        source_name="Generated Alpha labels",
        metadata={"rotation_deg": 7.5},
    )

    assert context.has_simulation_workspace_frame
    assert not controller._camera_live_timer.isActive()
    assert controller._live_camera_enabled is live_enabled
    assert controller._template_match_request_id == 6
    assert controller._camera_source_generation == 1
    assert info == source_changes[-1]
    assert info["metadata"] == {"rotation_deg": 7.5}
    assert len(camera_images) == 1

    controller._template_match_complete(5, {"request_id": 5, "stale": True})
    assert template_matches == []
    controller._template_match_complete(6, {"request_id": 6})
    assert template_matches == [{"request_id": 6}]

    stale_image = QtGui.QImage(2, 2, QtGui.QImage.Format.Format_RGB888)
    controller._camera_refresh_ready(stale_image, 0)
    assert len(camera_images) == 1

    controller.return_to_synthetic_camera()

    assert not context.has_simulation_workspace_frame
    assert controller._camera_live_timer.isActive() is live_enabled
    assert controller._live_camera_enabled is live_enabled
    assert controller._template_match_request_id == 7
    assert controller._camera_source_generation == 2
    assert source_changes[-1] == {
        "active": False,
        "source_name": "Synthetic camera",
        "metadata": {},
    }
    assert refreshes == [True]

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_camera_panel_test_source_disables_camera_actions_and_preserves_status(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = CameraPanel()
    panel.set_status(
        {
            "connected": True,
            "width": 1920,
            "height": 1080,
            "fps": 15,
            "device": "synthetic",
        }
    )
    panel.set_calibration_ready(True)
    assert panel.live_check.isChecked()
    assert panel.live_check.isEnabled()
    assert panel.refresh_button.isEnabled()
    assert panel.capture_button.isEnabled()
    assert panel.focus_spin.isEnabled()

    panel.set_test_frame_source(True, "Generated Alpha labels at R 7.5°")

    assert "TEST IMAGE" in panel.image_state.text()
    assert "FROZEN" in panel.image_state.text()
    assert "Alpha labels" in panel.image_state.text()
    for widget in (
        panel.live_check,
        panel.live_rate,
        panel.refresh_button,
        panel.capture_button,
        panel.lens_button,
        panel.bed_button,
        panel.autofocus_check,
        panel.apply_focus_button,
        panel.save_focus_button,
        panel.measure_button,
        panel.focus_slider,
        panel.focus_spin,
    ):
        assert not widget.isEnabled(), type(widget).__name__
    assert panel.opacity_slider.isEnabled()

    frozen_status = panel.image_state.text()
    panel.set_image_updated()
    assert panel.image_state.text() == frozen_status

    panel.set_test_frame_source(False)

    assert panel.image_state.text() == "Waiting for synthetic camera image"
    assert panel.live_check.isChecked()
    for widget in (
        panel.live_check,
        panel.live_rate,
        panel.refresh_button,
        panel.capture_button,
        panel.lens_button,
        panel.bed_button,
        panel.autofocus_check,
        panel.apply_focus_button,
        panel.save_focus_button,
        panel.measure_button,
        panel.focus_slider,
        panel.focus_spin,
    ):
        assert widget.isEnabled(), type(widget).__name__

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_camera_panel_return_respects_missing_calibration(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = CameraPanel()
    panel.set_status(
        {
            "connected": True,
            "width": 640,
            "height": 480,
            "fps": 10,
            "device": "synthetic",
        }
    )
    panel.set_calibration_ready(False)
    panel.set_test_frame_source(True)
    assert panel.image_state.text().startswith("TEST IMAGE")

    panel.set_test_frame_source(False)

    assert panel.image_state.text() == "Bed mapping is required for a corrected overlay"
    assert panel.image_state.toolTip() == ""
    assert not panel.live_check.isEnabled()
    assert not panel.live_rate.isEnabled()
    assert not panel.refresh_button.isEnabled()
    assert panel.capture_button.isEnabled()
    assert panel.focus_spin.isEnabled()

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_workspace_keeps_a_persistent_frozen_source_badge(
    qt_application: QtWidgets.QApplication,
) -> None:
    workspace = WorkspaceView(Bounds(0.0, 0.0, 220.0, 220.0))

    workspace.set_test_frame_source(True, "Generated: Alpha labels · R 7.00°")

    assert not workspace._test_frame_badge.isHidden()
    assert workspace._test_frame_badge.text() == "TEST IMAGE · FROZEN"
    assert "Alpha labels" in workspace._test_frame_badge.toolTip()

    workspace.set_test_frame_source(False)

    assert workspace._test_frame_badge.isHidden()
    workspace.close()
    workspace.deleteLater()
    qt_application.processEvents()


def test_camera_overlay_registers_opencv_pixel_centers_to_machine_coordinates(
    qt_application: QtWidgets.QApplication,
) -> None:
    area = Bounds(10.0, -5.0, 110.0, 75.0)
    workspace = WorkspaceView(area)
    image = QtGui.QImage(400, 320, QtGui.QImage.Format.Format_RGB888)
    workspace.set_camera_image(image)

    item = workspace._camera_item
    offset = item.offset()
    scale_x = area.width / image.width()
    scale_y = area.height / image.height()
    for pixel_x, pixel_y in ((0, 0), (173, 91), (399, 319)):
        # A source pixel occupies one Qt cell beginning at item.offset(); its
        # visual center must land at the OpenCV/BedMapper coordinate (i, j).
        displayed_center = item.mapToScene(
            offset + QtCore.QPointF(pixel_x + 0.5, pixel_y + 0.5)
        )
        expected = workspace.workspace_scene.machine_to_scene(
            area.x_min + pixel_x * scale_x,
            area.y_max - pixel_y * scale_y,
        )
        assert displayed_center.x() == pytest.approx(expected.x(), abs=1e-9)
        assert displayed_center.y() == pytest.approx(expected.y(), abs=1e-9)

    workspace.close()
    workspace.deleteLater()
    qt_application.processEvents()


def test_returning_from_test_source_clears_pixels_before_hiding_badge(
    qt_application: QtWidgets.QApplication,
) -> None:
    workspace = WorkspaceView(Bounds(0.0, 0.0, 220.0, 220.0))
    image = QtGui.QImage(32, 32, QtGui.QImage.Format.Format_RGB888)
    image.fill(QtGui.QColor("#A52A2A"))
    workspace.set_camera_image(image)
    workspace.set_test_frame_source(True, "Loaded test sheet")
    assert workspace._camera_item.isVisible()
    assert not workspace._test_frame_badge.isHidden()

    panel_updates: list[tuple[bool, str]] = []
    harness = SimpleNamespace(
        template_panel=SimpleNamespace(
            set_test_image_source=lambda active, label: panel_updates.append(
                (active, label)
            )
        ),
        camera_panel=SimpleNamespace(
            set_test_frame_source=lambda active, label: panel_updates.append(
                (active, label)
            )
        ),
        workspace=workspace,
    )

    E3MainWindow._simulation_frame_changed(
        harness,
        {"active": False, "source_name": "Synthetic camera"},
    )

    assert panel_updates == [
        (False, "Synthetic camera"),
        (False, "Synthetic camera"),
    ]
    assert not workspace._camera_item.isVisible()
    assert workspace._test_frame_badge.isHidden()

    workspace.close()
    workspace.deleteLater()
    qt_application.processEvents()


def test_main_window_trace_review_uses_captured_frame_and_clear_cancels_request(
    qt_application: QtWidgets.QApplication,
) -> None:
    image = QtGui.QImage(32, 24, QtGui.QImage.Format.Format_RGB888)
    image.fill(QtGui.QColor("#A52A2A"))
    camera_images: list[QtGui.QImage] = []
    panel_results: list[dict[str, Any]] = []
    panel_clears: list[bool] = []
    preview_updates: list[tuple[list[dict[str, Any]], list[str]]] = []
    preview_clears: list[bool] = []
    selected_panels: list[str] = []
    notices: list[str] = []
    cancellations: list[bool] = []
    result = {
        "request_id": 4,
        "message": "Trace ready",
        "camera_image": image,
        "detections": [{"id": "trace-1"}],
    }
    harness = SimpleNamespace(
        _trace_result=None,
        _camera_image_ready=camera_images.append,
        controller=SimpleNamespace(
            cancel_trace_detection=lambda: cancellations.append(True)
        ),
        trace_panel=SimpleNamespace(
            set_result=panel_results.append,
            selected_ids=lambda: ["trace-1"],
            clear_result=lambda: panel_clears.append(True),
        ),
        workspace=SimpleNamespace(
            set_trace_preview=lambda detections, selected: preview_updates.append(
                (detections, selected)
            ),
            clear_trace_preview=lambda: preview_clears.append(True),
        ),
        inspector_tabs=SimpleNamespace(select_panel=selected_panels.append),
        show_notice=notices.append,
    )

    E3MainWindow._trace_result_ready(harness, result)

    assert camera_images == [image]
    assert harness._trace_result is result
    assert panel_results == [result]
    assert preview_updates == [([{"id": "trace-1"}], ["trace-1"])]
    assert selected_panels == ["trace"]
    assert notices == ["Trace ready"]

    E3MainWindow._clear_trace_preview(harness)

    assert cancellations == [True]
    assert harness._trace_result is None
    assert preview_clears == [True]
    assert panel_clears == [True]

    qt_application.processEvents()


def test_project_replacement_ends_an_active_test_image_session() -> None:
    returns: list[bool] = []
    context = SimpleNamespace(has_simulation_workspace_frame=True)
    harness = SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        controller=SimpleNamespace(
            return_to_synthetic_camera=lambda: returns.append(True)
        ),
    )

    E3MainWindow._end_test_image_for_project_replacement(harness)
    assert returns == [True]

    context.has_simulation_workspace_frame = False
    E3MainWindow._end_test_image_for_project_replacement(harness)
    assert returns == [True]


def test_generated_frame_runs_through_the_controller_alignment_pipeline(
    qt_application: QtWidgets.QApplication,
) -> None:
    work_area = WorkArea(0.0, 220.0, 0.0, 220.0)
    pixels_per_mm = 2.0
    options = TraceOptions(
        detection_mode="color",
        target_hue=2,
        min_saturation=35,
        min_area_mm2=40.0,
        min_width_mm=5.0,
        min_height_mm=4.0,
        regular_grid=True,
        infer_missing=True,
    )
    template = template_from_rectangle_grid(
        RectangleGridSpec(
            name="Controller alignment grid",
            rows=3,
            columns=2,
            width_mm=60.0,
            height_mm=40.0,
            corner_radius_mm=3.0,
            horizontal_gap_mm=15.0,
            vertical_gap_mm=10.0,
        ),
        trace_options=options.to_dict(),
    )
    generated = generate_template_test_frame(
        template,
        work_area,
        pixels_per_mm,
        center_x_mm=110.0,
        center_y_mm=110.0,
        rotation_deg=7.0,
        seed=1729,
        noise_stddev=1.0,
    )

    context = _SimulationFrameContext()
    context.frame = generated.image
    context.has_simulation_workspace_frame = True
    context.bed = SimpleNamespace(
        calibration=SimpleNamespace(
            image_to_machine=np.eye(3),
            image_width=generated.image.shape[1],
            image_height=generated.image.shape[0],
        )
    )
    runtime = SimpleNamespace(
        running=True,
        context=context,
        settings=SimpleNamespace(
            calibration=SimpleNamespace(
                bed=SimpleNamespace(pixels_per_mm=pixels_per_mm)
            ),
            machine=SimpleNamespace(work_area=work_area),
        ),
    )
    controller = DesktopController(runtime)

    payload = controller._match_cut_templates_once(
        41,
        (template,),
        template.id,
    )

    assert payload["request_id"] == 41
    assert payload["mode"] == "selected"
    assert payload["matched"] is True, (
        payload["viability_reasons"],
        payload["dimension_scale_ratio"],
    )
    assert payload["template_id"] == template.id
    assert payload["matched_count"] == len(template.features)
    assert payload["center_x_mm"] == pytest.approx(110.0, abs=0.5)
    assert payload["center_y_mm"] == pytest.approx(110.0, abs=0.5)
    rotation_error = (payload["rotation_deg"] - 7.0 + 90.0) % 180.0 - 90.0
    assert rotation_error == pytest.approx(0.0, abs=1.0)
    assert not payload["camera_image"].isNull()

    controller.deleteLater()
    qt_application.processEvents()
