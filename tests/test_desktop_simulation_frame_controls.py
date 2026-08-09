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
from laser_aligner.desktop.theme import DEFAULT_CAMERA_OVERLAY_OPACITY
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
        self.lens = SimpleNamespace(model=None)
        self.bed_validity = {"state": "VALID", "reasons": []}
        self.camera = SimpleNamespace(
            status=lambda: SimpleNamespace(
                connected=True,
                frames_read=1,
                last_error=None,
            )
        )
        self.frame = np.full((8, 8, 3), (20, 80, 180), dtype=np.uint8)

    def bed_calibration_validity(self) -> dict[str, Any]:
        return dict(self.bed_validity)

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

    def rectified_frame(
        self,
        refresh: bool = True,
        *,
        precision: bool = False,
    ) -> np.ndarray:
        del refresh
        del precision
        return self.frame.copy()


def _controller() -> tuple[DesktopController, _SimulationFrameContext]:
    context = _SimulationFrameContext()
    runtime = SimpleNamespace(running=True, context=context, status=lambda: {})
    return DesktopController(runtime), context


def test_job_controller_preflights_before_motion_and_arming(
    qt_application: QtWidgets.QApplication,
) -> None:
    calls: list[str] = []

    class Machine:
        settings = SimpleNamespace(backend="serial")

        def ensure_connected(self) -> None:
            calls.append("connect")

        def preflight_program(self, gcode: str) -> str:
            calls.append(f"validate:{gcode}")
            return "validated"

        def prepare_photo_position(self) -> None:
            calls.append("park")

        def arm_program(self, phrase: str, program: str) -> None:
            assert program == "validated"
            calls.append(f"arm:{phrase}")

        def start_validated_program(
            self,
            program: str,
            name: str,
        ) -> dict[str, bool]:
            calls.append(f"start:{name}:{program}")
            return {"running": True}

        def disarm(self) -> None:
            calls.append("disarm")

    runtime = SimpleNamespace(context=SimpleNamespace(machine=Machine()))
    controller = DesktopController(runtime)
    controller._run = lambda callback, **_kwargs: callback()  # type: ignore[method-assign]

    controller.run_job("program", "job.gcode", arm_phrase="phrase")

    assert calls == [
        "connect",
        "validate:program",
        "park",
        "arm:phrase",
        "start:job.gcode:validated",
    ]


def test_job_controller_disarms_when_start_fails_after_arming(
    qt_application: QtWidgets.QApplication,
) -> None:
    calls: list[str] = []

    class Machine:
        settings = SimpleNamespace(backend="serial")

        def ensure_connected(self) -> None:
            calls.append("connect")

        def preflight_program(self, _gcode: str) -> str:
            calls.append("validate")
            return "validated"

        def prepare_photo_position(self) -> None:
            calls.append("park")

        def arm_program(self, _phrase: str, _program: str) -> None:
            calls.append("arm")

        def start_validated_program(self, _program: str, _name: str) -> None:
            calls.append("start")
            raise RuntimeError("rejected")

        def disarm(self) -> None:
            calls.append("disarm-m5")

    runtime = SimpleNamespace(context=SimpleNamespace(machine=Machine()))
    controller = DesktopController(runtime)
    controller._run = lambda callback, **_kwargs: callback()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="rejected"):
        controller.run_job("program", "job.gcode", arm_phrase="phrase")

    assert calls == ["connect", "validate", "park", "arm", "start", "disarm-m5"]


def test_software_stop_bypasses_shared_worker_pool(
    qt_application: QtWidgets.QApplication,
) -> None:
    calls: list[bool] = []
    machine = SimpleNamespace(request_stop=lambda emergency=False: calls.append(emergency))
    runtime = SimpleNamespace(
        running=False,
        context=SimpleNamespace(machine=machine),
    )
    controller = DesktopController(runtime)
    controller._run = lambda *_args, **_kwargs: pytest.fail(  # type: ignore[method-assign]
        "STOP must not enter the shared worker pool"
    )

    controller.emergency_stop()

    assert calls == [True]


def test_project_machine_work_area_mismatch_is_rejected() -> None:
    fake = SimpleNamespace(
        document=SimpleNamespace(work_area=Bounds(0.0, 0.0, 220.0, 220.0)),
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                machine=SimpleNamespace(
                    work_area=WorkArea(15.0, 205.0, 15.0, 205.0)
                )
            )
        ),
        _work_area_signature=E3MainWindow._work_area_signature,
    )

    with pytest.raises(ValueError, match="Project work area does not match"):
        E3MainWindow._require_project_machine_work_area_match(fake)


def test_trace_detection_and_color_pick_reject_work_area_mismatch() -> None:
    errors: list[str] = []
    detection_requests: list[dict[str, Any]] = []
    fake = SimpleNamespace(
        document=SimpleNamespace(work_area=Bounds(0.0, 0.0, 220.0, 220.0)),
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                machine=SimpleNamespace(
                    work_area=WorkArea(15.0, 205.0, 15.0, 205.0)
                )
            ),
            context=SimpleNamespace(bed=SimpleNamespace(calibration=object())),
        ),
        controller=SimpleNamespace(
            detect_trace_objects=detection_requests.append,
        ),
        show_error=errors.append,
        _work_area_signature=E3MainWindow._work_area_signature,
    )
    fake._require_project_machine_work_area_match = lambda: (
        E3MainWindow._require_project_machine_work_area_match(fake)
    )

    E3MainWindow._detect_trace_objects(fake, {"detection_mode": "auto"})
    E3MainWindow._begin_trace_color_pick(fake)

    assert detection_requests == []
    assert len(errors) == 2
    assert all("Project work area does not match" in error for error in errors)


def test_controller_reports_terminal_job_error_only_once(
    qt_application: QtWidgets.QApplication,
) -> None:
    status = {
        "machine": {
            "job": {
                "running": False,
                "started_at": 10.0,
                "finished_at": 20.0,
                "error": "Command '$H' failed: error:8",
            }
        }
    }
    runtime = SimpleNamespace(running=True, status=lambda: status)
    controller = DesktopController(runtime)
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)

    controller.poll_status()
    controller.poll_status()

    assert errors == ["Controller job failed: Command '$H' failed: error:8"]
    controller.deleteLater()
    qt_application.processEvents()


def test_controller_drops_stale_camera_refresh_delivery_and_cleanup(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    delivered: list[QtGui.QImage] = []
    controller.cameraImageReady.connect(delivered.append)
    image = QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888)
    controller._camera_source_generation = 4

    controller._camera_refresh_ready(image, 3)
    assert delivered == []
    controller._camera_refresh_ready(image, 4)
    assert delivered == [image]

    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)
    context.camera.status = lambda: SimpleNamespace(
        connected=False,
        frames_read=0,
        last_error="device unavailable",
    )
    controller._camera_refresh_failed("old source unavailable", 3)
    assert errors == []
    controller._camera_refresh_failed("current source unavailable", 4)
    assert len(errors) == 1
    assert "current source unavailable" in errors[0]

    controller._camera_refresh_failed("current source unavailable", 4)
    controller._camera_refresh_failed("a different recurring read detail", 4)
    assert len(errors) == 1

    recoveries: list[str] = []
    controller.notice.connect(recoveries.append)
    controller._camera_refresh_ready(image, 4)
    assert recoveries == ["Camera image updates recovered"]
    controller._camera_refresh_failed("camera busy again", 4)
    assert len(errors) == 2

    retries: list[bool] = []
    controller.refresh_camera_image = lambda: retries.append(True)  # type: ignore[method-assign]
    context.camera.status = lambda: SimpleNamespace(
        connected=True,
        frames_read=1,
        last_error=None,
    )
    controller.retry_camera_image()
    assert controller._camera_error_latched is None
    assert retries == [True]

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


@pytest.mark.parametrize("state", ["UNKNOWN", "STALE"])
def test_invalid_bed_map_is_reported_as_mapping_not_camera_failure(
    qt_application: QtWidgets.QApplication,
    state: str,
) -> None:
    controller, context = _controller()
    context.bed_validity = {
        "state": state,
        "reasons": ["Legacy bed map has no camera/lens provenance"],
    }
    mapping_alerts: list[dict[str, Any]] = []
    camera_errors: list[str] = []
    controller.cameraMappingRequired.connect(mapping_alerts.append)
    controller.cameraErrorOccurred.connect(camera_errors.append)
    controller._run = lambda *_args, **_kwargs: pytest.fail(  # type: ignore[method-assign]
        "An invalid mapping must be rejected before camera refresh work starts"
    )

    controller.refresh_camera_image()
    controller.refresh_camera_image()

    assert camera_errors == []
    assert mapping_alerts == [
        {
            "state": state,
            "reasons": ["Legacy bed map has no camera/lens provenance"],
            "camera_online": True,
            "setup_tab": 1,
        }
    ]

    context.lens.model = SimpleNamespace(quality={"gate": "pass"})
    controller.retry_camera_image()
    assert mapping_alerts[-1]["setup_tab"] == 2
    assert len(mapping_alerts) == 2

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_missing_bed_map_remains_a_quiet_empty_workspace_state(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    context.bed.calibration = None
    context.bed_validity = {"state": "MISSING", "reasons": ["No bed map"]}
    mapping_alerts: list[dict[str, Any]] = []
    controller.cameraMappingRequired.connect(mapping_alerts.append)
    controller._run = lambda *_args, **_kwargs: pytest.fail(  # type: ignore[method-assign]
        "A missing clean-install map must not launch corrected-frame work"
    )

    controller.refresh_camera_image()

    assert mapping_alerts == []
    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_calibration_change_discards_inflight_frame_and_queues_one_replacement(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    launches: list[dict[str, Any]] = []

    class FinishedSignal:
        def __init__(self) -> None:
            self.callbacks: list[Any] = []

        def connect(self, callback: Any, _connection: Any) -> None:
            self.callbacks.append(callback)

    def fake_run(_callback: Any, **kwargs: Any) -> object:
        task = SimpleNamespace(signals=SimpleNamespace(finished=FinishedSignal()))
        launches.append({"task": task, **kwargs})
        return task

    controller._run = fake_run  # type: ignore[method-assign]
    delivered: list[QtGui.QImage] = []
    controller.cameraImageReady.connect(delivered.append)

    controller.refresh_camera_image()
    first_generation = controller._camera_source_generation
    assert len(launches) == 1
    assert controller._camera_refresh_in_flight

    controller.calibration_changed()
    assert controller._camera_source_generation == first_generation + 1
    assert controller._camera_refresh_pending
    assert len(launches) == 1

    stale = QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888)
    launches[0]["on_success"](stale)
    assert delivered == []
    launches[0]["task"].signals.finished.callbacks[0]()
    qt_application.processEvents()

    assert len(launches) == 2
    assert not controller._camera_refresh_pending
    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_focus_change_discards_frame_captured_with_the_old_focus(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    launches: list[dict[str, Any]] = []

    class FinishedSignal:
        def __init__(self) -> None:
            self.callbacks: list[Any] = []

        def connect(self, callback: Any, _connection: Any) -> None:
            self.callbacks.append(callback)

    def fake_run(_callback: Any, **kwargs: Any) -> object:
        task = SimpleNamespace(signals=SimpleNamespace(finished=FinishedSignal()))
        launches.append({"task": task, **kwargs})
        return task

    controller._run = fake_run  # type: ignore[method-assign]
    delivered: list[QtGui.QImage] = []
    controller.cameraImageReady.connect(delivered.append)

    controller.refresh_camera_image()
    old_generation = controller._camera_source_generation
    controller._camera_focus_complete({"changed": True})

    assert controller._camera_source_generation == old_generation + 1
    assert controller._camera_refresh_pending
    launches[0]["on_success"](
        QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888)
    )
    assert delivered == []
    launches[0]["task"].signals.finished.callbacks[0]()
    qt_application.processEvents()
    assert len(launches) == 2

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_refresh_result_is_bound_to_exact_lens_and_bed_objects(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    context.lens.model = SimpleNamespace(quality={"gate": "pass"})
    launches: list[dict[str, Any]] = []

    class FinishedSignal:
        def __init__(self) -> None:
            self.callbacks: list[Any] = []

        def connect(self, callback: Any, _connection: Any) -> None:
            self.callbacks.append(callback)

    def fake_run(_callback: Any, **kwargs: Any) -> object:
        task = SimpleNamespace(signals=SimpleNamespace(finished=FinishedSignal()))
        launches.append({"task": task, **kwargs})
        return task

    controller._run = fake_run  # type: ignore[method-assign]
    delivered: list[QtGui.QImage] = []
    invalidations: list[bool] = []
    controller.cameraImageReady.connect(delivered.append)
    controller.cameraImageInvalidated.connect(lambda: invalidations.append(True))
    controller._publish_camera_image(
        QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888)
    )

    controller.refresh_camera_image()
    assert len(launches) == 1
    context.lens.model = SimpleNamespace(quality={"gate": "pass"})
    launches[0]["on_success"](
        QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888)
    )

    assert len(delivered) == 1
    assert invalidations == [True]
    assert controller._camera_refresh_pending
    launches[0]["task"].signals.finished.callbacks[0]()
    qt_application.processEvents()
    assert len(launches) == 2

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_invalid_mapping_clears_an_already_visible_overlay(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    invalidations: list[bool] = []
    controller.cameraImageInvalidated.connect(lambda: invalidations.append(True))
    controller._publish_camera_image(
        QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888)
    )
    context.bed_validity = {"state": "STALE", "reasons": ["lens changed"]}

    controller.refresh_camera_image()
    controller.refresh_camera_image()

    assert invalidations == [True]
    assert not controller._camera_image_published
    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_calibration_change_during_reconnect_cannot_latch_reconnect_busy(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    context.camera.status = lambda: SimpleNamespace(
        connected=False,
        frames_read=0,
        last_error="offline",
    )
    context.restart_camera = lambda: {"connected": True}
    launches: list[dict[str, Any]] = []

    class FinishedSignal:
        def connect(self, _callback: Any, _connection: Any) -> None:
            return

    def fake_run(callback: Any, **kwargs: Any) -> object:
        launches.append({"callback": callback, **kwargs})
        return SimpleNamespace(signals=SimpleNamespace(finished=FinishedSignal()))

    controller._run = fake_run  # type: ignore[method-assign]
    controller.retry_camera_image()
    reconnect_generation = controller._camera_reconnect_generation
    assert reconnect_generation is not None
    controller.calibration_changed()
    assert controller._camera_refresh_pending

    context.camera.status = lambda: SimpleNamespace(
        connected=True,
        frames_read=2,
        last_error=None,
    )
    launches[0]["on_success"]({"connected": True})

    assert not controller._camera_reconnect_in_flight
    assert controller._camera_reconnect_generation is None
    assert len(launches) == 2
    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_machine_setup_review_defers_mapping_refresh_until_dialog_closes(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    refreshes: list[bool] = []
    controller.refresh_camera_image = lambda: refreshes.append(True)  # type: ignore[method-assign]

    controller.set_calibration_review_active(True)
    controller.calibration_changed()
    assert refreshes == []
    assert controller._camera_refresh_pending

    controller.set_calibration_review_active(False)
    assert refreshes == [True]
    assert not controller._camera_refresh_pending
    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_mapping_recovery_notice_is_distinct_from_camera_recovery(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    controller._camera_mapping_latched = "mapping"
    notices: list[str] = []
    controller.notice.connect(notices.append)

    controller._camera_refresh_ready(
        QtGui.QImage(4, 4, QtGui.QImage.Format.Format_RGB888),
        controller._camera_source_generation,
    )

    assert notices == ["Corrected camera overlay recovered"]
    assert controller._camera_mapping_latched is None
    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_healthy_camera_rectification_error_has_its_own_alert(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    overlay_errors: list[str] = []
    camera_errors: list[str] = []
    controller.cameraOverlayErrorOccurred.connect(overlay_errors.append)
    controller.cameraErrorOccurred.connect(camera_errors.append)

    controller._camera_refresh_failed(
        "Rectification map could not be built",
        controller._camera_source_generation,
    )
    controller._camera_refresh_failed(
        "A repeated rectification detail",
        controller._camera_source_generation,
    )

    assert camera_errors == []
    assert len(overlay_errors) == 1
    assert "camera is online" in overlay_errors[0]
    assert "Rectification map could not be built" in overlay_errors[0]
    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_frozen_corrected_frame_bypasses_invalid_live_mapping(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    context.has_simulation_workspace_frame = True
    context.bed_validity = {"state": "UNKNOWN", "reasons": ["legacy map"]}
    delivered: list[QtGui.QImage] = []
    mapping_alerts: list[dict[str, Any]] = []
    controller.cameraImageReady.connect(delivered.append)
    controller.cameraMappingRequired.connect(mapping_alerts.append)

    controller.refresh_camera_image()

    assert len(delivered) == 1
    assert mapping_alerts == []
    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_mapping_dialog_opens_the_recommended_setup_tab(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[tuple[str, str]] = []
    opened_tabs: list[int] = []

    class StatusBar:
        def showMessage(self, _message: str, _timeout: int) -> None:
            return

    fake_window = SimpleNamespace(
        statusBar=lambda: StatusBar(),
        open_machine_setup=opened_tabs.append,
    )

    def question(_parent: object, title: str, message: str, *_args: object) -> Any:
        messages.append((title, message))
        return QtWidgets.QMessageBox.StandardButton.Open

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", question)
    monkeypatch.setattr(
        QtCore.QTimer,
        "singleShot",
        lambda _delay, callback: callback(),
    )

    E3MainWindow.show_camera_mapping_required(
        fake_window,
        {
            "camera_online": True,
            "setup_tab": 2,
            "reasons": ["Legacy map has no provenance"],
        },
    )

    assert messages[0][0] == "Bed mapping required"
    assert "Camera is online" in messages[0][1]
    assert "No coordinate entry is required" in messages[0][1]
    assert opened_tabs == [2]


def test_explicit_camera_retry_reopens_failed_device_before_refresh(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    context.camera.status = lambda: SimpleNamespace(
        connected=False,
        frames_read=0,
        last_error="device busy",
    )
    context.restart_camera = lambda: {"connected": True}
    launched: list[dict[str, Any]] = []

    def fake_run(callback: Any, **kwargs: Any) -> object:
        launched.append({"callback": callback, **kwargs})
        return object()

    controller._run = fake_run  # type: ignore[method-assign]
    notices: list[str] = []
    controller.notice.connect(notices.append)
    refreshes: list[bool] = []
    controller.refresh_camera_image = lambda: refreshes.append(True)  # type: ignore[method-assign]

    controller.retry_camera_image()

    assert controller._camera_reconnect_in_flight
    assert notices == ["Reopening camera…"]
    assert launched[0]["callback"]() == {"connected": True}
    launched[0]["on_success"]({"connected": True})
    assert not controller._camera_reconnect_in_flight
    assert notices[-1] == "Camera reopened successfully"
    assert refreshes == [True]

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


def test_camera_overlay_defaults_to_seventy_percent_in_control_and_workspace(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = CameraPanel()
    workspace = WorkspaceView(Bounds(0.0, 0.0, 220.0, 220.0))

    assert DEFAULT_CAMERA_OVERLAY_OPACITY == pytest.approx(0.70)
    assert panel.opacity_slider.value() == 70
    assert workspace.camera_opacity == pytest.approx(0.70)

    panel.close()
    workspace.close()
    panel.deleteLater()
    workspace.deleteLater()
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
    area = Bounds(10.0, -5.0, 110.1, 75.1)
    workspace = WorkspaceView(area)
    image = QtGui.QImage(400, 320, QtGui.QImage.Format.Format_RGB888)
    workspace.set_camera_image(image, pixels_per_mm=4.0)

    item = workspace._camera_item
    offset = item.offset()
    scale_x = scale_y = 0.25
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


def test_camera_overlay_rejects_raster_dimensions_that_disagree_with_ppm(
    qt_application: QtWidgets.QApplication,
) -> None:
    workspace = WorkspaceView(Bounds(10.0, -5.0, 110.1, 75.1))
    valid_image = QtGui.QImage(400, 320, QtGui.QImage.Format.Format_RGB888)
    valid_image.fill(QtGui.QColor("#2468AC"))
    workspace.set_camera_image(valid_image, pixels_per_mm=4.0)
    item = workspace._camera_item
    previous_pixmap_key = item.pixmap().cacheKey()
    previous_transform = QtGui.QTransform(item.transform())
    previous_position = QtCore.QPointF(item.pos())
    invalid_image = QtGui.QImage(399, 320, QtGui.QImage.Format.Format_RGB888)

    with pytest.raises(ValueError, match="received 399 x 320, expected 400 x 320"):
        workspace.set_camera_image(invalid_image, pixels_per_mm=4.0)

    assert item.isVisible()
    assert item.pixmap().cacheKey() == previous_pixmap_key
    assert item.transform() == previous_transform
    assert item.pos() == previous_position

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
            set_trace_preview=lambda detections, selected, _support=None: (
                preview_updates.append((detections, selected))
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
            laser=SimpleNamespace(
                boundary_margin_mm=0.0,
                spot_offset_x_mm=0.0,
                spot_offset_y_mm=0.0,
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
