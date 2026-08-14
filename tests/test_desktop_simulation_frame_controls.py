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

from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.config import WorkArea
from laser_aligner.desktop.controller import (
    DesktopController,
    _apply_local_output_review,
)
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

        def prepare_job_start(self) -> None:
            calls.append("home")

        def arm_program(self, phrase: str, program: str) -> None:
            assert program == "validated"
            calls.append(f"arm:{phrase}")

        def start_validated_program(
            self,
            program: str,
            name: str,
        ) -> dict[str, object]:
            calls.append(f"start:{name}:{program}")
            return {
                "running": True,
                "name": name,
                "started_at": 123.0,
                "program_digest": "digest",
            }

        def disarm(self) -> None:
            calls.append("disarm")

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            machine=Machine(),
            validate_powered_calibration_support=lambda _gcode, _name: None,
        ),
        running=False,
    )
    controller = DesktopController(runtime)
    started: list[dict[str, object]] = []
    controller.jobStarted.connect(started.append)

    def run(callback, **kwargs):
        if kwargs.get("requires_controller"):
            runtime.context.machine.ensure_connected()
        kwargs["on_success"](callback())

    controller._run = run  # type: ignore[method-assign]

    controller.run_job("program", "job.gcode", arm_phrase="phrase")

    assert calls == [
        "connect",
        "validate:program",
        "home",
        "arm:phrase",
        "start:job.gcode:validated",
    ]
    assert started == [
        {
            "running": True,
            "name": "job.gcode",
            "started_at": 123.0,
            "program_digest": "digest",
        }
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

        def prepare_job_start(self) -> None:
            calls.append("home")

        def arm_program(self, _phrase: str, _program: str) -> None:
            calls.append("arm")

        def start_validated_program(self, _program: str, _name: str) -> None:
            calls.append("start")
            raise RuntimeError("rejected")

        def disarm(self) -> None:
            calls.append("disarm-m5")

    runtime = SimpleNamespace(
        context=SimpleNamespace(
            machine=Machine(),
            validate_powered_calibration_support=lambda _gcode, _name: None,
        )
    )
    controller = DesktopController(runtime)
    def run(callback, **kwargs):
        if kwargs.get("requires_controller"):
            runtime.context.machine.ensure_connected()
        return callback()

    controller._run = run  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="rejected"):
        controller.run_job("program", "job.gcode", arm_phrase="phrase")

    assert calls == ["connect", "validate", "home", "arm", "start", "disarm-m5"]


def test_job_controller_preflights_then_homes_once_without_camera_capture(
    qt_application: QtWidgets.QApplication,
) -> None:
    calls: list[str] = []
    signature = ("honeycomb-rigid-frame", 1, "pose", "bed-map")
    polygon = ((18.0, 30.0), (228.0, 30.0), (228.0, 240.0), (18.0, 240.0))

    class Machine:
        settings = SimpleNamespace(backend="serial")

        def ensure_connected(self) -> None:
            calls.append("connect")

        def preflight_program(self, gcode: str, **kwargs: Any) -> str:
            calls.append(
                f"preflight:{gcode}:{kwargs['guarded_output_polygon_mm']!r}"
            )
            return "validated"

        def prepare_job_start(self) -> None:
            calls.append("start-home")

        def arm_program(self, phrase: str, program: str) -> None:
            calls.append(f"arm:{phrase}:{program}")

        def start_validated_program(
            self,
            program: str,
            name: str,
        ) -> dict[str, object]:
            calls.append(f"start:{name}:{program}")
            return {"running": True}

        def disarm(self) -> None:
            calls.append("disarm")

    context = SimpleNamespace(
        machine=Machine(),
        validate_honeycomb_execution_binding=lambda value: calls.append(
            f"binding:{tuple(value)!r}"
        ),
        validate_powered_calibration_support=lambda _gcode, _name: calls.append(
            "calibration-guard"
        ),
    )
    runtime = SimpleNamespace(
        context=context,
        running=False,
        settings=SimpleNamespace(
            laser=SimpleNamespace(guarded_output_polygon_mm=polygon)
        ),
    )
    controller = DesktopController(runtime)

    def run(callback: Any, **kwargs: Any) -> None:
        if kwargs.get("requires_controller"):
            context.machine.ensure_connected()
        kwargs["on_success"](callback())

    controller._run = run  # type: ignore[method-assign]
    controller.run_job(
        "program",
        "local.gcode",
        arm_phrase="phrase",
        honeycomb_signature=signature,
        guarded_output_polygon_mm=polygon,
    )

    assert calls == [
        "connect",
        "calibration-guard",
        f"preflight:program:{polygon!r}",
        f"binding:{signature!r}",
        "start-home",
        "arm:phrase:validated",
        "start:local.gcode:validated",
    ]


def test_job_controller_binding_check_does_not_imply_polygon_authority(
    qt_application: QtWidgets.QApplication,
) -> None:
    calls: list[str] = []

    class Machine:
        settings = SimpleNamespace(backend="simulator")

        def preflight_program(self, gcode: str) -> str:
            calls.append(f"preflight:{gcode}")
            return "validated"

        def start_validated_program(self, program: str, name: str) -> dict[str, bool]:
            calls.append(f"start:{name}:{program}")
            return {"running": True}

        def disarm(self) -> None:
            calls.append("disarm")

    context = SimpleNamespace(
        machine=Machine(),
        validate_powered_calibration_support=lambda _gcode, _name: calls.append(
            "calibration-guard"
        ),
        validate_honeycomb_execution_binding=lambda signature: calls.append(
            f"binding:{signature!r}"
        ),
    )
    controller = DesktopController(SimpleNamespace(context=context, running=False))
    controller._run = (  # type: ignore[method-assign]
        lambda callback, **kwargs: kwargs["on_success"](callback())
    )

    controller.run_job(
        "calibration-program",
        "accuracy-validation.gcode",
        honeycomb_signature=("bound-pose",),
    )

    assert calls == [
        "calibration-guard",
        "preflight:calibration-program",
        "binding:('bound-pose',)",
        "start:accuracy-validation.gcode:validated",
    ]


def test_job_controller_binding_failure_disarms_after_static_preflight(
    qt_application: QtWidgets.QApplication,
) -> None:
    calls: list[str] = []

    class Machine:
        settings = SimpleNamespace(backend="serial")

        def ensure_connected(self) -> None:
            calls.append("connect")

        def preflight_program(self, _gcode: str, **_kwargs: Any) -> str:
            calls.append("preflight")
            return "validated"

        def disarm(self) -> None:
            calls.append("disarm-m5")

    def reject_pose(_signature: tuple[object, ...]) -> None:
        calls.append("binding")
        raise RuntimeError("honeycomb moved")

    context = SimpleNamespace(
        machine=Machine(),
        validate_honeycomb_execution_binding=reject_pose,
        validate_powered_calibration_support=lambda _gcode, _name: calls.append(
            "calibration-guard"
        ),
    )
    runtime = SimpleNamespace(
        context=context,
        running=False,
        settings=SimpleNamespace(
            laser=SimpleNamespace(guarded_output_polygon_mm=None)
        ),
    )
    controller = DesktopController(runtime)

    def run(callback: Any, **kwargs: Any) -> None:
        if kwargs.get("requires_controller"):
            context.machine.ensure_connected()
        callback()

    controller._run = run  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="honeycomb moved"):
        controller.run_job(
            "program",
            "local.gcode",
            honeycomb_signature=("bound-pose",),
        )

    assert calls == [
        "connect",
        "calibration-guard",
        "preflight",
        "binding",
        "disarm-m5",
    ]


def test_job_controller_static_preflight_failure_never_homes_or_checks_binding(
    qt_application: QtWidgets.QApplication,
) -> None:
    calls: list[str] = []

    class Machine:
        settings = SimpleNamespace(backend="serial")

        def ensure_connected(self) -> None:
            calls.append("connect")

        def preflight_program(self, _gcode: str, **_kwargs: Any) -> None:
            calls.append("preflight-reject")
            raise RuntimeError("outside configured output")

        def disarm(self) -> None:
            calls.append("disarm-m5")

    context = SimpleNamespace(
        machine=Machine(),
        validate_honeycomb_execution_binding=lambda _signature: pytest.fail(
            "Invalid G-code must not reach the prepared binding check"
        ),
        validate_powered_calibration_support=lambda _gcode, _name: calls.append(
            "calibration-guard"
        ),
    )
    runtime = SimpleNamespace(
        context=context,
        running=False,
        settings=SimpleNamespace(
            laser=SimpleNamespace(guarded_output_polygon_mm=None)
        ),
    )
    controller = DesktopController(runtime)

    def run(callback: Any, **kwargs: Any) -> None:
        if kwargs.get("requires_controller"):
            context.machine.ensure_connected()
        callback()

    controller._run = run  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="outside configured output"):
        controller.run_job(
            "bad-program",
            "local.gcode",
            honeycomb_signature=("bound-pose",),
        )

    assert calls == [
        "connect",
        "calibration-guard",
        "preflight-reject",
        "disarm-m5",
    ]


def test_trace_color_sampling_uses_the_expanded_review_frame(
    qt_application: QtWidgets.QApplication,
) -> None:
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    image[:, 10:] = (0, 0, 255)
    context = SimpleNamespace(
        bed=SimpleNamespace(calibration=object()),
        rectified_frame=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("expanded review sampling must not recapture")
        ),
    )
    runtime = SimpleNamespace(
        context=context,
        settings=SimpleNamespace(
            machine=SimpleNamespace(work_area=WorkArea(0.0, 10.0, 0.0, 10.0)),
            calibration=SimpleNamespace(bed=SimpleNamespace(pixels_per_mm=2.0)),
        ),
    )
    controller = DesktopController(runtime)
    controller._trace_sample_image = image
    controller._trace_sample_area = WorkArea(10.0, 20.0, 10.0, 20.0)
    controller._trace_sample_signature = controller._current_review_signature()
    results: list[dict[str, Any]] = []
    controller.traceColorReady.connect(results.append)

    def run(callback, **kwargs):
        kwargs["on_success"](callback())

    controller._run = run  # type: ignore[method-assign]
    controller.sample_trace_color(18.0, 18.0)

    assert len(results) == 1
    assert results[0]["rgb"][0] > 240
    assert results[0]["machine_x"] == 18.0
    assert results[0]["machine_y"] == 18.0


def test_local_trace_review_returns_rotated_machine_output_polygon_and_rejects_escape() -> None:
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(20.0, 30.0),
        x_axis_machine=(0.0, 1.0),
        y_axis_machine=(-1.0, 0.0),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="4" * 64,
    )
    inside = SimpleNamespace(
        vector_contours_mm=[[(20.0, 10.0), (30.0, 10.0), (30.0, 20.0)]],
        vector_contour_mm=[],
        diagnostics={},
        selected_default=True,
    )
    outside = SimpleNamespace(
        vector_contours_mm=[[(80.0, 100.0), (90.0, 100.0), (90.0, 110.0)]],
        vector_contour_mm=[],
        diagnostics={},
        selected_default=True,
    )

    polygon, outside_count = _apply_local_output_review(
        [inside, outside],
        frame,
        WorkArea(0.0, 60.0, 0.0, 80.0),
    )

    assert np.asarray(polygon) == pytest.approx(
        np.asarray(((-30.0, 20.0), (-30.0, -40.0), (50.0, -40.0), (50.0, 20.0)))
    )
    assert outside_count == 1
    assert inside.diagnostics["within_work_area"] is True
    assert outside.diagnostics["within_work_area"] is False
    assert outside.diagnostics["output_review_frame"] == "machine"
    assert outside.selected_default is False


def test_local_trace_review_maps_explicit_padded_support_square_exactly() -> None:
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(28.0, 40.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="5" * 64,
    )
    machine_polygon = (
        (18.0, 30.0),
        (228.0, 30.0),
        (228.0, 240.0),
        (18.0, 240.0),
    )
    inside = SimpleNamespace(
        vector_contours_mm=[[(0.0, 0.0), (190.0, 0.0), (190.0, 190.0)]],
        vector_contour_mm=[],
        diagnostics={},
        selected_default=True,
    )
    outside = SimpleNamespace(
        vector_contours_mm=[[(200.1, 100.0), (201.0, 100.0), (201.0, 101.0)]],
        vector_contour_mm=[],
        diagnostics={},
        selected_default=True,
    )

    polygon, outside_count = _apply_local_output_review(
        [inside, outside],
        frame,
        machine_polygon,
    )

    assert np.asarray(polygon) == pytest.approx(
        np.asarray(((-10.0, -10.0), (200.0, -10.0), (200.0, 200.0), (-10.0, 200.0)))
    )
    assert outside_count == 1
    assert inside.diagnostics["within_work_area"] is True
    assert inside.selected_default is True
    assert outside.diagnostics["within_work_area"] is False
    assert outside.diagnostics["work_area_overrun_mm"] == pytest.approx(11.0)
    assert outside.diagnostics["support_overruns_mm"]["right"] == pytest.approx(
        11.0
    )
    assert outside.selected_default is False


def test_cached_local_trace_sample_rejects_changed_support_frame(
    qt_application: QtWidgets.QApplication,
) -> None:
    first = HoneycombCoordinateFrame(
        origin_machine_mm=(20.0, 30.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="5" * 64,
    )
    moved = HoneycombCoordinateFrame(
        origin_machine_mm=(21.0, 30.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="6" * 64,
    )
    current = [first]
    context = SimpleNamespace(
        bed=SimpleNamespace(calibration=object()),
        bed_mapping_digest=lambda: "bed-map",
        current_honeycomb_coordinate_frame=lambda: current[0],
    )
    runtime = SimpleNamespace(
        context=context,
        settings=SimpleNamespace(
            machine=SimpleNamespace(work_area=WorkArea(0.0, 220.0, 0.0, 220.0)),
            calibration=SimpleNamespace(bed=SimpleNamespace(pixels_per_mm=2.0)),
        ),
    )
    controller = DesktopController(runtime)
    controller._workspace_coordinate_space = "honeycomb_local"
    controller._trace_sample_image = np.zeros((380, 380, 3), dtype=np.uint8)
    controller._trace_sample_area = WorkArea(0.0, 190.0, 0.0, 190.0)
    controller._trace_sample_signature = controller._current_review_signature(first)
    current[0] = moved
    failures: list[str] = []
    controller.traceColorFailed.connect(failures.append)

    def run(callback: Any, **kwargs: Any) -> None:
        try:
            callback()
        except Exception as exc:
            kwargs["on_failure"](str(exc))

    controller._run = run  # type: ignore[method-assign]
    controller.sample_trace_color(10.0, 10.0)

    assert len(failures) == 1
    assert "changed after Trace capture" in failures[0]

    controller.deleteLater()
    qt_application.processEvents()


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


def test_controller_workspace_coordinate_space_switch_invalidates_camera_and_trace() -> None:
    controller, _context = _controller()
    invalidations: list[bool] = []
    refreshes: list[bool] = []
    controller.request_camera_refresh = lambda: refreshes.append(True)  # type: ignore[method-assign]
    controller._camera_image_published = True
    controller.cameraImageInvalidated.connect(lambda: invalidations.append(True))
    controller._trace_sample_image = np.zeros((2, 2, 3), dtype=np.uint8)
    controller._trace_sample_area = WorkArea(0.0, 1.0, 0.0, 1.0)
    generation = controller._camera_source_generation
    trace_request = controller._trace_request_id

    controller.set_workspace_coordinate_space("honeycomb_local")

    assert controller._workspace_coordinate_space == "honeycomb_local"
    assert controller._camera_source_generation == generation + 1
    assert controller._trace_request_id == trace_request + 1
    assert controller._trace_sample_image is None
    assert controller._trace_sample_area is None
    assert invalidations == [True]
    assert refreshes == [True]


def test_controller_rejects_unknown_workspace_coordinate_space() -> None:
    controller, _context = _controller()

    with pytest.raises(ValueError, match="Unsupported workspace coordinate space"):
        controller.set_workspace_coordinate_space("maybe-square")


def test_switching_to_local_clears_machine_coordinate_test_frame() -> None:
    controller, context = _controller()
    context.has_simulation_workspace_frame = True
    source_changes: list[dict[str, Any]] = []
    controller.simulationFrameChanged.connect(source_changes.append)
    controller.request_camera_refresh = lambda: None  # type: ignore[method-assign]

    controller.set_workspace_coordinate_space("honeycomb_local")

    assert not context.has_simulation_workspace_frame
    assert source_changes == [
        {
            "active": False,
            "source_name": "",
            "reason": (
                "Machine-coordinate test image cleared for a "
                "honeycomb-local project"
            ),
        }
    ]


def test_machine_coordinate_test_frame_cannot_activate_on_local_canvas() -> None:
    controller, context = _controller()
    controller._workspace_coordinate_space = "honeycomb_local"

    with pytest.raises(ValueError, match="machine coordinates"):
        controller.activate_simulation_workspace_frame(
            np.zeros((8, 8, 3), dtype=np.uint8),
            source_name="Wrong coordinate domain",
        )

    assert not context.has_simulation_workspace_frame


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
        _reconcile_pristine_project_frame=lambda: False,
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


def test_controller_drops_local_camera_raster_when_support_pose_changes(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    controller._workspace_coordinate_space = "honeycomb_local"
    first = HoneycombCoordinateFrame(
        origin_machine_mm=(10.0, 20.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="1" * 64,
    )
    moved = HoneycombCoordinateFrame(
        origin_machine_mm=(11.0, 20.0),
        x_axis_machine=(1.0, 0.0),
        y_axis_machine=(0.0, 1.0),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="2" * 64,
    )
    current = [first]
    context.current_honeycomb_coordinate_frame = lambda: current[0]  # type: ignore[attr-defined]
    expected_revision = (
        context.lens.model,
        context.bed.calibration,
        tuple(first.provenance_signature),
    )
    delivered: list[object] = []
    controller.cameraImageReady.connect(delivered.append)
    image = QtGui.QImage(190, 190, QtGui.QImage.Format.Format_RGB888)

    current[0] = moved
    controller._camera_refresh_ready(
        image,
        controller._camera_source_generation,
        expected_revision,
        image_area=WorkArea(0.0, 190.0, 0.0, 190.0),
    )

    assert delivered == []
    assert controller._camera_refresh_pending

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_live_camera_refresh_publishes_expanded_frame_with_its_exact_area(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    expanded = WorkArea(10.0, 221.0, 10.0, 233.0)
    calls: list[tuple[bool, WorkArea | None]] = []
    launches: list[dict[str, Any]] = []

    context.trace_camera_work_area = lambda: expanded  # type: ignore[attr-defined]

    def rectified_frame(
        refresh: bool = True,
        *,
        precision: bool = False,
        work_area: WorkArea | None = None,
    ) -> np.ndarray:
        del precision
        calls.append((refresh, work_area))
        return np.zeros((892, 844, 3), dtype=np.uint8)

    context.rectified_frame = rectified_frame  # type: ignore[method-assign]

    class FinishedSignal:
        def connect(self, _callback: Any, _connection: Any) -> None:
            return

    def fake_run(operation: Any, **kwargs: Any) -> object:
        launches.append({"operation": operation, **kwargs})
        return SimpleNamespace(signals=SimpleNamespace(finished=FinishedSignal()))

    controller._run = fake_run  # type: ignore[method-assign]
    delivered: list[object] = []
    controller.cameraImageReady.connect(delivered.append)

    controller.refresh_camera_image()
    image = launches[0]["operation"]()
    launches[0]["on_success"](image)

    assert calls == [(True, expanded)]
    assert len(delivered) == 1
    payload = delivered[0]
    assert isinstance(payload, dict)
    assert payload["image"].size() == QtCore.QSize(844, 892)
    assert payload["camera_image_area"] == {
        "x_min": 10.0,
        "x_max": 221.0,
        "y_min": 10.0,
        "y_max": 233.0,
    }

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_live_camera_refresh_uses_local_honeycomb_raster_and_area(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    controller._workspace_coordinate_space = "honeycomb_local"
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(29.0, 37.0),
        x_axis_machine=(0.8, 0.6),
        y_axis_machine=(-0.6, 0.8),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="3" * 64,
    )
    context.current_honeycomb_coordinate_frame = lambda: frame  # type: ignore[attr-defined]
    calls: list[dict[str, Any]] = []
    launches: list[dict[str, Any]] = []

    def rectified_frame(**kwargs: Any) -> np.ndarray:
        calls.append(dict(kwargs))
        return np.zeros((380, 380, 3), dtype=np.uint8)

    context.rectified_frame = rectified_frame  # type: ignore[method-assign]

    class FinishedSignal:
        def connect(self, _callback: Any, _connection: Any) -> None:
            return

    def fake_run(operation: Any, **kwargs: Any) -> object:
        launches.append({"operation": operation, **kwargs})
        return SimpleNamespace(signals=SimpleNamespace(finished=FinishedSignal()))

    controller._run = fake_run  # type: ignore[method-assign]
    delivered: list[object] = []
    controller.cameraImageReady.connect(delivered.append)

    controller.refresh_camera_image()
    image = launches[0]["operation"]()
    launches[0]["on_success"](image)

    assert calls == [
        {
            "refresh": True,
            "work_area": WorkArea(0.0, 190.0, 0.0, 190.0),
            "coordinate_frame": frame,
        }
    ]
    assert len(delivered) == 1
    payload = delivered[0]
    assert isinstance(payload, dict)
    assert payload["image"].size() == QtCore.QSize(380, 380)
    assert payload["camera_image_area"] == {
        "x_min": 0.0,
        "x_max": 190.0,
        "y_min": 0.0,
        "y_max": 190.0,
    }

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_live_camera_refresh_fails_closed_without_local_honeycomb_frame(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    controller._workspace_coordinate_space = "honeycomb_local"
    context.current_honeycomb_coordinate_frame = lambda: None  # type: ignore[attr-defined]
    context.rectified_frame = lambda **_kwargs: pytest.fail(  # type: ignore[method-assign]
        "Machine-space pixels must not be published on a local canvas"
    )
    errors: list[str] = []
    delivered: list[object] = []
    controller.cameraOverlayErrorOccurred.connect(errors.append)
    controller.cameraImageReady.connect(delivered.append)

    controller.refresh_camera_image()
    controller.refresh_camera_image()

    assert delivered == []
    assert len(errors) == 1
    assert "requires a current honeycomb reference" in errors[0]
    assert not controller._camera_refresh_in_flight

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


def test_focus_sweep_ranks_samples_and_restores_original_without_mutating_settings(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    calls: list[int] = []
    controls = {
        "focus_automatic_continuous": 0,
        "focus_auto": 0,
        "focus_absolute": 5,
    }

    class Camera:
        settings = SimpleNamespace(controls=controls)

        def apply_controls_and_snapshot(
            self,
            requested: dict[str, int],
            *,
            settle_seconds: float,
            timeout_seconds: float,
        ) -> tuple[SimpleNamespace, np.ndarray]:
            del settle_seconds, timeout_seconds
            value = requested["focus_absolute"]
            calls.append(value)
            return SimpleNamespace(), np.full((4, 4), value, dtype=np.uint8)

    context.camera = Camera()
    controller._sharpness_score = lambda frame: float(frame[0, 0])  # type: ignore[method-assign]

    result = controller._camera_focus_sweep(5, 15, 5)

    assert calls == [5, 5, 5, 10, 10, 10, 15, 15, 15, 5]
    assert [item["median_sharpness"] for item in result["focus_sweep"]] == [
        5.0,
        10.0,
        15.0,
    ]
    assert result["restored_focus"] == 5
    assert result["changed"] is False
    assert controls["focus_absolute"] == 5

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_focus_sweep_restores_original_after_mid_sweep_failure(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, context = _controller()
    calls: list[int] = []
    controls = {
        "focus_automatic_continuous": 0,
        "focus_auto": 0,
        "focus_absolute": 5,
    }

    class Camera:
        settings = SimpleNamespace(controls=controls)

        def apply_controls_and_snapshot(
            self,
            requested: dict[str, int],
            *,
            settle_seconds: float,
            timeout_seconds: float,
        ) -> tuple[SimpleNamespace, np.ndarray]:
            del settle_seconds, timeout_seconds
            value = requested["focus_absolute"]
            calls.append(value)
            if value == 10:
                raise RuntimeError("forced focus failure")
            return SimpleNamespace(), np.full((4, 4), value, dtype=np.uint8)

    context.camera = Camera()

    with pytest.raises(RuntimeError, match="forced focus failure"):
        controller._camera_focus_sweep(5, 15, 5)

    assert calls == [5, 5, 5, 10, 5]
    assert controls["focus_absolute"] == 5

    controller._camera_live_timer.stop()
    controller.deleteLater()
    qt_application.processEvents()


def test_camera_panel_displays_ranked_focus_sweep_without_change_warning(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = CameraPanel()

    panel.set_focus_result(
        {
            "focus_sweep": [
                {"focus": 5, "median_sharpness": 580.0},
                {"focus": 10, "median_sharpness": 1_025.0},
            ],
            "restored_focus": 5,
            "changed": False,
        }
    )

    assert "Best tested focus: 10 (1025.0)" in panel.sharpness_label.text()
    assert "Restored focus 5; calibration unchanged" in panel.sharpness_label.text()
    assert not panel.focus_warning.isVisible()

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_camera_panel_reports_active_and_pending_focus_profiles(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = CameraPanel()
    panel.set_calibration_profile(
        {
            "active_label": "1920 x 1080, manual focus 5",
            "profiles": [{"key": "focus-5"}],
        }
    )
    assert "Active profile: 1920 x 1080, manual focus 5" in (
        panel.calibration_profile_label.text()
    )

    panel.set_focus_result(
        {
            "calibration_profile_label": "1920 x 1080, manual focus 10",
            "profile_restart_required": True,
        }
    )
    assert "Saved profile: 1920 x 1080, manual focus 10" in (
        panel.calibration_profile_label.text()
    )
    assert "Restart the app to activate it" in panel.calibration_profile_label.text()

    panel.close()
    panel.deleteLater()
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


def test_calibration_change_invalidates_cached_trace_and_template_evidence(
    qt_application: QtWidgets.QApplication,
) -> None:
    controller, _context = _controller()
    controller.request_camera_refresh = lambda: None  # type: ignore[method-assign]
    controller._trace_review_active = True
    controller._trace_sample_image = np.zeros((2, 2, 3), dtype=np.uint8)
    controller._trace_sample_area = WorkArea(0.0, 1.0, 0.0, 1.0)
    controller._trace_sample_signature = ("machine", None, "old-bed")
    controller._template_review_active = True
    controller._template_review_signature = ("machine", None, "old-bed")
    trace_request = controller._trace_request_id
    template_request = controller._template_match_request_id
    invalidations: list[bool] = []
    controller.reviewEvidenceInvalidated.connect(lambda: invalidations.append(True))

    controller.calibration_changed()

    assert controller._trace_request_id == trace_request + 1
    assert controller._template_match_request_id == template_request + 1
    assert not controller._trace_review_active
    assert not controller._template_review_active
    assert controller._trace_sample_image is None
    assert controller._trace_sample_area is None
    assert controller._trace_sample_signature is None
    assert controller._template_review_signature is None
    assert invalidations == [True]

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
    assert all(item["requires_controller"] is True for item in launched)
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
        panel.focus_sweep_button,
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
        panel.focus_sweep_button,
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


def test_camera_overlay_places_and_fits_an_expanded_display_area(
    qt_application: QtWidgets.QApplication,
) -> None:
    workspace = WorkspaceView(Bounds(10.0, 10.0, 210.0, 210.0))
    expanded = Bounds(10.0, 10.0, 221.0, 233.0)
    image = QtGui.QImage(844, 892, QtGui.QImage.Format.Format_RGB888)
    image.fill(QtGui.QColor("#2468AC"))

    workspace.set_camera_image(
        image,
        pixels_per_mm=4.0,
        image_area=expanded,
    )
    workspace.fit_camera_image()

    assert workspace._camera_image_area == expanded
    assert workspace._camera_item.pos() == QtCore.QPointF(10.0, -233.0)
    visible = workspace.mapToScene(workspace.viewport().rect()).boundingRect()
    image_rect = workspace._camera_item.sceneBoundingRect()
    assert visible.contains(image_rect)

    invalid = QtGui.QImage(843, 892, QtGui.QImage.Format.Format_RGB888)
    with pytest.raises(ValueError, match="received 843 x 892, expected 844 x 892"):
        workspace.set_camera_image(
            invalid,
            pixels_per_mm=4.0,
            image_area=expanded,
        )

    workspace.close()
    workspace.deleteLater()
    qt_application.processEvents()


def test_main_window_live_camera_payload_fits_only_when_area_changes() -> None:
    configured = Bounds(10.0, 10.0, 210.0, 210.0)
    expanded = Bounds(10.0, 10.0, 221.0, 233.0)
    changed = Bounds(9.0, 10.0, 221.0, 233.0)
    set_calls: list[tuple[QtCore.QSize, float, Bounds | None]] = []
    fits: list[bool] = []
    updates: list[bool] = []
    workspace = SimpleNamespace(
        workspace_scene=SimpleNamespace(work_area=configured),
        _camera_image_area=None,
    )

    def set_camera_image(
        image: QtGui.QImage,
        *,
        pixels_per_mm: float,
        image_area: Bounds | None,
    ) -> None:
        set_calls.append((image.size(), pixels_per_mm, image_area))
        workspace._camera_image_area = configured if image_area is None else image_area

    workspace.set_camera_image = set_camera_image
    workspace.fit_camera_image = lambda: fits.append(True)
    harness = SimpleNamespace(
        workspace=workspace,
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                calibration=SimpleNamespace(
                    bed=SimpleNamespace(pixels_per_mm=4.0)
                )
            )
        ),
        camera_panel=SimpleNamespace(
            set_image_updated=lambda: updates.append(True)
        ),
        _camera_image_area=E3MainWindow._camera_image_area,
    )

    def payload(area: Bounds) -> dict[str, object]:
        return {
            "image": QtGui.QImage(
                int(round(area.width * 4.0)),
                int(round(area.height * 4.0)),
                QtGui.QImage.Format.Format_RGB888,
            ),
            "camera_image_area": {
                "x_min": area.x_min,
                "x_max": area.x_max,
                "y_min": area.y_min,
                "y_max": area.y_max,
            },
        }

    E3MainWindow._camera_image_ready(harness, payload(expanded))
    E3MainWindow._camera_image_ready(harness, payload(expanded))
    E3MainWindow._camera_image_ready(harness, payload(changed))

    assert [call[2] for call in set_calls] == [expanded, expanded, changed]
    assert fits == [True, True]
    assert updates == [True, True, True]


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
