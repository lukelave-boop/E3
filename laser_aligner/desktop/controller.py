from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..calibration.profiles import signature_from_values
from ..config import WorkArea, effective_laser_output_area
from ..core import CoreRuntime
from ..geometry.polygon import (
    convex_polygon_violation_normalized_mm,
    normalize_convex_polygon,
)
from ..templates import CutTemplate
from ..vision.object_trace import (
    TraceOptions,
    detect_objects,
    sample_color,
)
from ..vision.template_alignment import TemplateAlignment, rank_templates
from .machine_state import (
    controller_node_boot_id,
    controller_session_generation,
    controller_state_revision,
    project_machine_state,
)
from .qt import require_qt
from .tasks import FunctionTask

QtCore, QtGui, QtWidgets = require_qt()

LOGGER = logging.getLogger(__name__)

DESKTOP_SHUTDOWN_TIMEOUT_SECONDS = 4.0
DESKTOP_WORKER_DRAIN_SECONDS = 1.0

_MIN_TEMPLATE_MATCHES = 3
_MIN_TEMPLATE_DIRECT_MATCHES = 2
_MIN_TEMPLATE_COVERAGE = 0.50
_MIN_TEMPLATE_CONFIDENCE = 0.55
_MAX_TEMPLATE_RMS_ERROR_MM = 1.0
_MAX_TEMPLATE_POINT_ERROR_MM = 2.0
_MAX_TEMPLATE_SCALE_ERROR = 0.035
_DEFAULT_LIVE_CAMERA_INTERVAL_MS = round(1000 / 2)
_MIN_LIVE_CAMERA_INTERVAL_MS = round(1000 / 15)
_MAX_LIVE_CAMERA_INTERVAL_MS = 10_000


@dataclass(slots=True)
class _MachineTaskAuthority:
    requested_operation_generation: int
    initial_session_generation: int | None
    initial_state_revision: int | None
    initial_node_boot_id: str | None
    session_lifecycle_operation: bool
    completed_operation_generation: int | None = None
    completed_session_generation: int | None = None
    completed_state_revision: int | None = None
    completed_node_boot_id: str | None = None
    completed_controller_state: str | None = None
    invalidated_before_execution: bool = False
    captured: bool = False


@dataclass(slots=True)
class _TaskCallbacks:
    on_success: Callable[[Any], None] | None
    on_failure: Callable[[str], None] | None
    on_finished: Callable[[], None] | None
    show_busy: bool
    machine_authority: _MachineTaskAuthority | None


def _validate_shutdown_deadline(deadline: object) -> float:
    if (
        type(deadline) not in {int, float}
        or not math.isfinite(float(deadline))
    ):
        raise ValueError("Shutdown deadline must be a finite monotonic timestamp")
    return float(deadline)


def _guarded_output_work_area(runtime: CoreRuntime) -> WorkArea:
    laser = runtime.settings.laser
    return effective_laser_output_area(
        runtime.settings.machine.work_area,
        laser.boundary_margin_mm,
        laser.spot_offset_x_mm,
        laser.spot_offset_y_mm,
    )


def _configured_guarded_output_polygon(
    runtime: CoreRuntime,
) -> tuple[tuple[float, float], ...] | None:
    settings = getattr(runtime, "settings", None)
    laser = getattr(settings, "laser", None)
    configured = getattr(laser, "guarded_output_polygon_mm", None)
    return (
        None
        if configured is None
        else normalize_convex_polygon(
            configured,
            label="laser.guarded_output_polygon_mm",
        )
    )


def _work_area_polygon(area: WorkArea) -> tuple[tuple[float, float], ...]:
    return (
        (float(area.x_min), float(area.y_min)),
        (float(area.x_max), float(area.y_min)),
        (float(area.x_max), float(area.y_max)),
        (float(area.x_min), float(area.y_max)),
    )


def _trace_roi_polygons(
    runtime: CoreRuntime,
    coordinate_frame: Any | None,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Return trusted physical polygons whose intersection is the hard Trace ROI."""

    guarded_polygon = _configured_guarded_output_polygon(runtime)
    guarded_output = _guarded_output_work_area(runtime)
    if coordinate_frame is None:
        return (guarded_polygon or _work_area_polygon(guarded_output),)
    support = (
        (0.0, 0.0),
        (float(coordinate_frame.width_mm), 0.0),
        (float(coordinate_frame.width_mm), float(coordinate_frame.height_mm)),
        (0.0, float(coordinate_frame.height_mm)),
    )
    machine_authority = guarded_polygon or _work_area_polygon(guarded_output)
    local_authority = tuple(
        coordinate_frame.machine_to_local(*point) for point in machine_authority
    )
    return support, local_authority


def _honeycomb_support_metadata(
    runtime: CoreRuntime,
    *,
    coordinate_space: str = "machine",
) -> dict[str, Any] | None:
    """Expose an approximate physical reference without affecting output."""

    context = runtime.context
    support_store = getattr(context, "honeycomb_support", None)
    reference = getattr(support_store, "reference", None)
    if reference is None:
        return None
    calibration = context.bed.calibration
    current = bool(
        calibration is not None
        and abs(calibration.created_at - reference.bed_calibration_created_at) <= 1e-9
    )
    metadata: dict[str, Any] = {
        "corners_machine_mm": [
            list(point) for point in reference.support_corners_machine_mm
        ],
        "support_width_mm": reference.support_width_mm,
        "support_height_mm": reference.support_height_mm,
        "created_at": reference.created_at,
        "bed_map_current": current,
        "reference_only": True,
    }
    if coordinate_space == "honeycomb_local":
        metadata["corners_local_mm"] = [
            [0.0, 0.0],
            [float(reference.support_width_mm), 0.0],
            [float(reference.support_width_mm), float(reference.support_height_mm)],
            [0.0, float(reference.support_height_mm)],
        ]
    if not current:
        metadata["message"] = (
            "Recorded honeycomb support was measured with a different bed map; "
            "re-record it before visually comparing the support outline."
        )
        return metadata
    output_polygon = _configured_guarded_output_polygon(runtime)
    output = _guarded_output_work_area(runtime)
    corners = np.asarray(reference.support_corners_machine_mm, dtype=np.float64)
    if output_polygon is None:
        overruns = {
            "left": max(0.0, float(output.x_min - np.min(corners[:, 0]))),
            "right": max(0.0, float(np.max(corners[:, 0]) - output.x_max)),
            "bottom": max(0.0, float(output.y_min - np.min(corners[:, 1]))),
            "top": max(0.0, float(np.max(corners[:, 1]) - output.y_max)),
        }
    else:
        maximum = max(
            convex_polygon_violation_normalized_mm(point, output_polygon)
            for point in corners
        )
        overruns = {"polygon": maximum}
    metadata["guarded_output_overruns_mm"] = overruns
    outside = {side: value for side, value in overruns.items() if value > 1e-9}
    if outside:
        details = ", ".join(
            f"{side} {value:.1f} mm" for side, value in outside.items()
        )
        metadata["message"] = (
            "The full honeycomb is shown, but it extends beyond guarded laser "
            f"output ({details}); those portions remain blocked and unselected."
        )
    else:
        metadata["message"] = (
            "The approximate honeycomb outline is shown for visual comparison only; "
            "it does not classify detections or change laser limits."
        )
    return metadata


def _apply_local_output_review(
    detections: list[Any],
    coordinate_frame: Any,
    output: WorkArea | tuple[tuple[float, float], ...],
) -> tuple[list[list[float]], int]:
    """Review local Trace contours against the configured machine authority."""

    machine_corners = (
        (
            (output.x_min, output.y_min),
            (output.x_max, output.y_min),
            (output.x_max, output.y_max),
            (output.x_min, output.y_max),
        )
        if isinstance(output, WorkArea)
        else normalize_convex_polygon(output, label="guarded output polygon")
    )
    output_polygon = [
        list(coordinate_frame.machine_to_local(x, y))
        for x, y in machine_corners
    ]
    outside = 0
    for detection in detections:
        contours = detection.vector_contours_mm or [detection.vector_contour_mm]
        local_points = [point for contour in contours for point in contour]
        local_overruns = {
            "left": max(
                0.0,
                max((-float(point[0]) for point in local_points), default=0.0),
            ),
            "right": max(
                0.0,
                max(
                    (
                        float(point[0]) - float(coordinate_frame.width_mm)
                        for point in local_points
                    ),
                    default=0.0,
                ),
            ),
            "bottom": max(
                0.0,
                max((-float(point[1]) for point in local_points), default=0.0),
            ),
            "top": max(
                0.0,
                max(
                    (
                        float(point[1]) - float(coordinate_frame.height_mm)
                        for point in local_points
                    ),
                    default=0.0,
                ),
            ),
        }
        machine_points = [
            coordinate_frame.local_to_machine(float(point[0]), float(point[1]))
            for point in local_points
        ]
        if machine_points and isinstance(output, WorkArea):
            coordinates = np.asarray(machine_points, dtype=np.float64)
            overruns = {
                "left": max(0.0, float(output.x_min - np.min(coordinates[:, 0]))),
                "right": max(0.0, float(np.max(coordinates[:, 0]) - output.x_max)),
                "bottom": max(0.0, float(output.y_min - np.min(coordinates[:, 1]))),
                "top": max(0.0, float(np.max(coordinates[:, 1]) - output.y_max)),
            }
        elif machine_points:
            maximum = max(
                convex_polygon_violation_normalized_mm(point, machine_corners)
                for point in machine_points
            )
            overruns = {"polygon": maximum}
        else:
            overruns = {side: 0.0 for side in ("left", "right", "bottom", "top")}
        maximum = max(overruns.values(), default=0.0)
        support_escape = max(local_overruns.values(), default=0.0)
        review_escape = max(maximum, support_escape)
        detection.diagnostics["within_work_area"] = review_escape <= 1e-9
        detection.diagnostics["work_area_overrun_mm"] = review_escape
        detection.diagnostics["work_area_overruns_mm"] = overruns
        detection.diagnostics["support_overruns_mm"] = local_overruns
        detection.diagnostics["output_review_frame"] = "machine"
        if review_escape > 1e-9:
            detection.selected_default = False
            outside += 1
    return output_polygon, outside


def _usable_template_detections(
    detections: Sequence[Any],
) -> tuple[list[Any], list[int], dict[str, Any]]:
    """Keep incomplete or unsafe camera evidence out of template alignment."""

    usable: list[Any] = []
    usable_indices: list[int] = []
    outside_count = 0
    cropped_count = 0
    excluded_count = 0
    for index, detection in enumerate(detections):
        diagnostics = (
            detection.get("diagnostics", {})
            if isinstance(detection, Mapping)
            else getattr(detection, "diagnostics", {})
        )
        if not isinstance(diagnostics, Mapping):
            diagnostics = {}
        outside = not bool(diagnostics.get("within_work_area", True))
        cropped = bool(diagnostics.get("touches_image_edge", False))
        outside_count += int(outside)
        cropped_count += int(cropped)
        if outside or cropped:
            excluded_count += 1
            continue
        usable.append(detection)
        usable_indices.append(index)

    warning = ""
    if excluded_count:
        qualifier = "all " if not usable else ""
        details = []
        if outside_count:
            details.append(
                f"{outside_count} outside the guarded output area"
            )
        if cropped_count:
            details.append(
                f"{cropped_count} cropped at the corrected image boundary"
            )
        warning = (
            f"Excluded {qualifier}{excluded_count} camera detection"
            f"{'s' if excluded_count != 1 else ''} from template alignment"
            f" ({' and '.join(details)})."
        )
    return usable, usable_indices, {
        "usable_detection_count": len(usable),
        "excluded_detection_count": excluded_count,
        "excluded_outside_count": outside_count,
        "excluded_cropped_count": cropped_count,
        "template_evidence_warning": warning,
    }


def image_to_qimage(image: np.ndarray) -> QtGui.QImage:
    if image is None or image.size == 0:
        return QtGui.QImage()
    if image.ndim == 2:
        contiguous = np.ascontiguousarray(image)
        output = QtGui.QImage(
            contiguous.data,
            contiguous.shape[1],
            contiguous.shape[0],
            contiguous.strides[0],
            QtGui.QImage.Format.Format_Grayscale8,
        )
        return output.copy()
    if image.shape[2] == 4:
        converted = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
        fmt = QtGui.QImage.Format.Format_RGBA8888
    else:
        converted = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        fmt = QtGui.QImage.Format.Format_RGB888
    contiguous = np.ascontiguousarray(converted)
    output = QtGui.QImage(
        contiguous.data,
        contiguous.shape[1],
        contiguous.shape[0],
        contiguous.strides[0],
        fmt,
    )
    return output.copy()


class DesktopController(QtCore.QObject):
    statusChanged = QtCore.Signal(dict)
    cameraImageReady = QtCore.Signal(object)
    cameraImageInvalidated = QtCore.Signal()
    errorOccurred = QtCore.Signal(str)
    cameraErrorOccurred = QtCore.Signal(str)
    cameraMappingRequired = QtCore.Signal(dict)
    cameraOverlayErrorOccurred = QtCore.Signal(str)
    notice = QtCore.Signal(str)
    busyChanged = QtCore.Signal(bool)
    cameraFocusChanged = QtCore.Signal(dict)
    traceResultReady = QtCore.Signal(dict)
    traceRasterPreviewReady = QtCore.Signal(int, object)
    traceDetectionFailed = QtCore.Signal(int, str, bool)
    traceColorReady = QtCore.Signal(dict)
    traceColorFailed = QtCore.Signal(str)
    templateMatchReady = QtCore.Signal(dict)
    reviewEvidenceInvalidated = QtCore.Signal()
    stopInitiated = QtCore.Signal()
    tasksDrained = QtCore.Signal()
    jobStarted = QtCore.Signal(dict)

    def __init__(
        self,
        runtime: CoreRuntime,
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self._active_tasks = 0
        self._tasks: set[FunctionTask] = set()
        self._task_callbacks: dict[FunctionTask, _TaskCallbacks] = {}
        self._camera_refresh_in_flight = False
        self._camera_refresh_generation: int | None = None
        self._camera_refresh_pending = False
        self._camera_image_published = False
        self._calibration_review_active = False
        self._camera_source_generation = 0
        self._camera_error_latched: str | None = None
        self._camera_mapping_latched: str | None = None
        self._camera_overlay_error_latched: str | None = None
        self._camera_reconnect_in_flight = False
        self._camera_reconnect_generation: int | None = None
        self._trace_request_id = 0
        self._trace_cancel_event = threading.Event()
        self._trace_review_active = False
        self._trace_sample_image: np.ndarray | None = None
        self._trace_sample_area: WorkArea | None = None
        self._trace_sample_signature: tuple[object, ...] | None = None
        self._template_match_request_id = 0
        self._template_match_cancel_event = threading.Event()
        self._template_review_active = False
        self._template_review_signature: tuple[object, ...] | None = None
        self._live_camera_enabled = False
        self._live_camera_interval_ms = _DEFAULT_LIVE_CAMERA_INTERVAL_MS
        self._reported_terminal_job: tuple[object, object] | None = None
        self._reported_terminal_jobs: dict[tuple[object, object], None] = {}
        self._last_controller_node_boot_id: str | None = None
        self._last_controller_session_generation: int | None = None
        self._last_controller_state_revision: int | None = None
        self._workspace_coordinate_space = "machine"
        self._shutdown_started = False
        self._shutdown_finalized = False
        self._shutdown_deadline_monotonic: float | None = None
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(750)
        self._poll_timer.timeout.connect(self.poll_status)
        self._camera_live_timer = QtCore.QTimer(self)
        self._camera_live_timer.setInterval(self._live_camera_interval_ms)
        self._camera_live_timer.timeout.connect(self.refresh_camera_image)

    def start(self) -> None:
        self._run(
            self.runtime.start,
            on_success=lambda _: self._started(),
            label="Start core services",
        )

    def set_workspace_coordinate_space(self, coordinate_space: str) -> None:
        """Select the persisted project frame used by live camera and Trace."""

        value = str(coordinate_space)
        if value not in {"machine", "honeycomb_local"}:
            raise ValueError(f"Unsupported workspace coordinate space: {value}")
        if value == self._workspace_coordinate_space:
            return
        self._workspace_coordinate_space = value
        self._camera_source_generation += 1
        self._invalidate_camera_image()
        self._trace_request_id += 1
        self._trace_cancel_event.set()
        self._trace_review_active = False
        self._trace_sample_image = None
        self._trace_sample_area = None
        self._trace_sample_signature = None
        self._template_match_request_id += 1
        self._template_match_cancel_event.set()
        self._template_review_active = False
        self._template_review_signature = None
        if self.runtime.running:
            self.request_camera_refresh()

    def _started(self) -> None:
        if self._shutdown_started:
            return
        self._poll_timer.start()
        self.poll_status()
        if self.runtime.context.bed.calibration is not None:
            self.refresh_camera_image()
            self._sync_camera_timer()
        self.notice.emit("Core services started")

    def stop(self, deadline: float | None = None) -> None:
        shutdown_deadline = self.begin_shutdown(deadline)
        if self._shutdown_finalized:
            return
        self._shutdown_finalized = True

        # Use one absolute deadline throughout shutdown. Give worker callbacks a
        # short bounded chance to return, but reserve the rest of the process
        # budget for camera/machine/runtime cleanup. FunctionTask retains every
        # Python wrapper independently until its run() method actually exits.
        drain_deadline = min(
            shutdown_deadline,
            time.monotonic() + DESKTOP_WORKER_DRAIN_SECONDS,
        )
        remaining_ms = max(
            0,
            math.ceil((drain_deadline - time.monotonic()) * 1000.0),
        )
        self.thread_pool.waitForDone(remaining_ms)
        unfinished = FunctionTask.unfinished_labels()
        if unfinished:
            LOGGER.warning(
                "Desktop shutdown worker drain expired with %d task(s) still "
                "running: %s",
                len(unfinished),
                ", ".join(unfinished),
            )
        self.runtime.stop(deadline=shutdown_deadline)

    def begin_shutdown(self, deadline: float | None = None) -> float:
        requested_deadline = (
            time.monotonic() + DESKTOP_SHUTDOWN_TIMEOUT_SECONDS
            if deadline is None
            else _validate_shutdown_deadline(deadline)
        )
        previous_deadline = self._shutdown_deadline_monotonic
        if previous_deadline is None:
            self._shutdown_deadline_monotonic = requested_deadline
        elif deadline is not None and requested_deadline < previous_deadline:
            # A caller may tighten a shutdown budget, but no repeated signal or
            # nested close path may silently reset/extend the original deadline.
            self._shutdown_deadline_monotonic = requested_deadline

        shutdown_deadline = self._shutdown_deadline_monotonic
        assert shutdown_deadline is not None
        if self._shutdown_started:
            return shutdown_deadline
        self._shutdown_started = True
        self._poll_timer.stop()
        self._camera_live_timer.stop()
        self._active_tasks = 0
        self._trace_request_id += 1
        self._trace_cancel_event.set()
        self._trace_review_active = False
        self._trace_sample_image = None
        self._trace_sample_area = None
        self._trace_sample_signature = None
        self._template_match_request_id += 1
        self._template_match_cancel_event.set()
        self._template_review_active = False
        self._template_review_signature = None
        # Cut off both controller-owned and dialog-owned FunctionTask outcome
        # signals before revoking services. Internal completion remains enabled
        # so ownership can be released if the Qt event loop is still alive.
        FunctionTask.suppress_all_callbacks()
        for task in tuple(self._tasks):
            task.suppress_callbacks()

        camera = getattr(self.runtime.context, "camera", None)
        cancel_camera_requests = getattr(camera, "cancel_pending_requests", None)
        if callable(cancel_camera_requests):
            try:
                cancel_camera_requests(terminal=True)
            except Exception:
                LOGGER.exception(
                    "Could not cancel pending camera requests during shutdown"
                )

        machine = self.runtime.context.machine
        # Pi execution is deliberately independent of this process.  Always
        # detach a remote facade on ordinary desktop shutdown, even before its
        # first status refresh: an empty cache cannot prove that this Pi is idle.
        # Only the explicit red STOP action may cancel Pi-owned execution. Local
        # serial retains the longstanding shutdown laser-off path.
        try:
            if bool(getattr(machine, "pi_owned_execution", False)):
                machine.detach(
                    deadline=min(shutdown_deadline, time.monotonic() + 0.05),
                    remember_idle_for_shutdown=True,
                )
            else:
                machine.request_stop(emergency=False)
        except Exception as exc:
            LOGGER.warning("Shutdown machine cleanup failed: %s", exc)
        return shutdown_deadline

    @property
    def has_active_tasks(self) -> bool:
        return bool(self._tasks)

    def _set_busy(self, delta: int) -> None:
        self._active_tasks = max(0, self._active_tasks + delta)
        self.busyChanged.emit(self._active_tasks > 0)

    def _run(
        self,
        callback: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
        on_finished: Callable[[], None] | None = None,
        cancel: Callable[[], None] | None = None,
        label: str = "Operation",
        show_busy: bool = True,
        requires_controller: bool = False,
        machine_bound: bool = False,
    ) -> FunctionTask:
        publish_ui = not self._shutdown_started
        if show_busy and publish_ui:
            self._set_busy(1)
        machine = self.runtime.context.machine
        operation_generation = machine.operation_generation()
        machine_authority = None
        if machine_bound or requires_controller:
            try:
                initial_machine_status = machine.status()
            except Exception:
                initial_machine_status = None
            machine_authority = _MachineTaskAuthority(
                requested_operation_generation=operation_generation,
                initial_session_generation=controller_session_generation(
                    initial_machine_status
                ),
                initial_state_revision=controller_state_revision(
                    initial_machine_status
                ),
                initial_node_boot_id=controller_node_boot_id(initial_machine_status),
                session_lifecycle_operation=machine_bound,
            )

        def require_initial_machine_authority() -> None:
            if (
                machine_authority is None
                or machine_authority.session_lifecycle_operation
            ):
                return
            try:
                current_status = machine.status()
            except Exception as exc:
                machine_authority.invalidated_before_execution = True
                raise RuntimeError(
                    "Controller authority could not be verified before execution"
                ) from exc
            current_authority = (
                controller_node_boot_id(current_status),
                controller_session_generation(current_status),
                controller_state_revision(current_status),
            )
            requested_authority = (
                machine_authority.initial_node_boot_id,
                machine_authority.initial_session_generation,
                machine_authority.initial_state_revision,
            )
            if current_authority != requested_authority:
                machine_authority.invalidated_before_execution = True
                raise RuntimeError(
                    "Controller authority changed before the queued operation began"
                )

        def capture_machine_authority() -> None:
            if machine_authority is None:
                return
            try:
                machine_authority.completed_operation_generation = (
                    machine.operation_generation()
                )
            except Exception:
                machine_authority.completed_operation_generation = None
            try:
                machine_status = machine.status()
            except Exception:
                machine_status = None
            machine_authority.completed_session_generation = (
                controller_session_generation(machine_status)
            )
            machine_authority.completed_state_revision = controller_state_revision(
                machine_status
            )
            machine_authority.completed_node_boot_id = controller_node_boot_id(
                machine_status
            )
            machine_authority.completed_controller_state = (
                None
                if machine_status is None
                else project_machine_state(machine_status).controller_state
            )
            machine_authority.captured = True

        def guarded_callback() -> Any:
            if self._shutdown_started:
                return None
            try:
                with machine.operation_scope(operation_generation):
                    require_initial_machine_authority()
                    if requires_controller:
                        machine.ensure_connected()
                        # A remote idempotent Connect can observe or adopt a
                        # session recovered after this task was queued. Never
                        # let the old task continue on that fresh generation.
                        require_initial_machine_authority()
                    return callback()
            finally:
                capture_machine_authority()

        task = FunctionTask(guarded_callback, label=label, cancel=cancel)
        if not publish_ui:
            task.suppress_callbacks()
        self._tasks.add(task)
        self._task_callbacks[task] = _TaskCallbacks(
            on_success=on_success,
            on_failure=on_failure,
            on_finished=on_finished,
            show_busy=show_busy and publish_ui,
            machine_authority=machine_authority,
        )
        # QObject-bound slots are automatically disconnected if the controller
        # is destroyed. This avoids receiverless lambdas that could outlive the
        # desktop while a bounded shutdown leaves a worker finishing in-place.
        task.signals.resultReady.connect(
            self._task_succeeded,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.errorReady.connect(
            self._task_failed,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.completed.connect(
            self._task_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        try:
            task.start_on(self.thread_pool)
        except BaseException:
            registration = self._task_callbacks.pop(task)
            self._tasks.discard(task)
            if registration.show_busy:
                self._set_busy(-1)
            raise
        return task

    def run_background(
        self,
        callback: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
        cancel: Callable[[], None] | None = None,
        label: str = "Operation",
        show_busy: bool = True,
    ) -> FunctionTask:
        """Run Qt-free preparation using the desktop's owned task lifecycle."""

        return self._run(
            callback,
            on_success=on_success,
            on_failure=on_failure,
            cancel=cancel,
            label=label,
            show_busy=show_busy,
        )

    @QtCore.Slot(object, object)
    def _task_succeeded(self, task: FunctionTask, result: Any) -> None:
        if self._shutdown_started:
            return
        registration = self._task_callbacks.get(task)
        if (
            registration is not None
            and self._task_authority_is_current(registration)
            and registration.on_success is not None
        ):
            registration.on_success(result)

    @QtCore.Slot(object, str)
    def _task_failed(self, task: FunctionTask, message: str) -> None:
        if self._shutdown_started:
            return
        registration = self._task_callbacks.get(task)
        if registration is None:
            return
        current = self._task_authority_is_current(registration)
        # A Pi START failure can itself invalidate the remote controller session.
        # Report that failure, but retain suppression after a local operator STOP.
        authority = registration.machine_authority
        if (
            not current
            and task.label == "Start job"
            and bool(getattr(self.runtime.context.machine, "pi_owned_execution", False))
            and authority is not None
            and not authority.invalidated_before_execution
        ):
            current = (
                self.runtime.context.machine.operation_generation()
                == authority.requested_operation_generation
            )
        if not current:
            return
        if task.label == "Start job" and bool(
            getattr(self.runtime.context.machine, "pi_owned_execution", False)
        ):
            job = self.runtime.context.machine.status().get("job") or {}
            job_id = job.get("job_id")
            if job_id:
                reported = getattr(self, "_reported_terminal_job", None)
                if reported is not None and reported[0] == job_id:
                    return
                self._reported_start_failure_job = job_id
        if registration.on_failure is None:
            self.errorOccurred.emit(f"{task.label} failed: {message}")
        else:
            registration.on_failure(message)

    @QtCore.Slot(object)
    def _task_finished(
        self,
        task: FunctionTask,
    ) -> None:
        registration = self._task_callbacks.pop(task, None)
        authority_is_current = (
            registration is not None
            and self._task_authority_is_current(registration)
        )
        if registration is not None and registration.show_busy:
            if self._shutdown_started:
                self._active_tasks = max(0, self._active_tasks - 1)
            else:
                self._set_busy(-1)
        self._tasks.discard(task)
        if (
            registration is not None
            and authority_is_current
            and registration.on_finished is not None
            and not self._shutdown_started
        ):
            registration.on_finished()
        if (
            not self._tasks
            and not self._shutdown_started
            and not self._shutdown_finalized
        ):
            self.tasksDrained.emit()

    def _task_authority_is_current(self, registration: _TaskCallbacks) -> bool:
        authority = registration.machine_authority
        if authority is None:
            return True
        if (
            not authority.captured
            or authority.completed_controller_state is None
            or authority.invalidated_before_execution
        ):
            return False
        if authority.completed_controller_state in {
            "STOPPING",
            "RECOVERING",
            "SHUTTING_DOWN",
        }:
            return False
        if (
            not authority.session_lifecycle_operation
            and authority.completed_controller_state == "RECONNECT_REQUIRED"
        ):
            return False
        machine = self.runtime.context.machine
        completed_operation_generation = authority.completed_operation_generation
        if completed_operation_generation is None:
            return False
        if (
            not authority.session_lifecycle_operation
            and completed_operation_generation
            != authority.requested_operation_generation
        ):
            return False
        try:
            if machine.operation_generation() != completed_operation_generation:
                return False
        except Exception:
            return False
        try:
            status = machine.status()
        except Exception:
            return False
        current_boot_id = controller_node_boot_id(status)
        if (
            authority.completed_node_boot_id is not None
            and current_boot_id != authority.completed_node_boot_id
        ):
            return False
        current_session = controller_session_generation(status)
        if (
            authority.completed_session_generation is not None
            and current_session != authority.completed_session_generation
        ):
            return False
        if not authority.session_lifecycle_operation:
            if (
                authority.initial_node_boot_id is not None
                and authority.completed_node_boot_id
                != authority.initial_node_boot_id
            ):
                return False
            if (
                authority.initial_session_generation is not None
                and authority.completed_session_generation
                != authority.initial_session_generation
            ):
                return False
        current_revision = controller_state_revision(status)
        return current_revision == authority.completed_state_revision

    def _accept_machine_status_revision(self, status: Mapping[str, Any]) -> bool:
        machine = status.get("machine")
        if not isinstance(machine, Mapping):
            return True
        boot_id = controller_node_boot_id(machine)
        session_generation = controller_session_generation(machine)
        state_revision = controller_state_revision(machine)
        if session_generation is None and state_revision is None:
            return True

        previous_boot_id = self._last_controller_node_boot_id
        if previous_boot_id is not None and boot_id is None:
            return False
        if (
            previous_boot_id is not None
            and boot_id is not None
            and boot_id != previous_boot_id
        ):
            self._last_controller_session_generation = None
            self._last_controller_state_revision = None
        elif (
            self._last_controller_state_revision is not None
            and state_revision is not None
            and state_revision < self._last_controller_state_revision
        ):
            return False
        elif (
            self._last_controller_session_generation is not None
            and session_generation is not None
            and session_generation < self._last_controller_session_generation
        ):
            return False

        if boot_id is not None:
            self._last_controller_node_boot_id = boot_id
        if session_generation is not None:
            self._last_controller_session_generation = session_generation
        if state_revision is not None:
            self._last_controller_state_revision = state_revision
        return True

    def poll_status(self) -> None:
        if not self.runtime.running:
            return
        try:
            status = self.runtime.status()
        except Exception as exc:
            self.errorOccurred.emit(f"Status refresh failed: {exc}")
            return
        if not self._accept_machine_status_revision(status):
            return
        self.statusChanged.emit(status)
        machine = status.get("machine") or {}
        job = machine.get("job") or {}
        pi_job = machine.get("pi_owned_execution") is True or bool(job.get("job_id"))
        if pi_job and (
            not job.get("job_id")
            or job.get("status_stale", False)
            or job.get("state") not in {"complete", "failed", "stopped", "interrupted"}
        ):
            return
        # STOP is an expected terminal outcome. Preserve any distinct cleanup
        # failure instead of suppressing all errors on stopped records.
        if pi_job and job.get("state") == "stopped" and job.get("error") in (None, "", "Job stopped"):
            return
        terminal_key = (
            job.get("job_id") or job.get("started_at"),
            None if pi_job else job.get("finished_at"),
        )
        reported_jobs = getattr(self, "_reported_terminal_jobs", {})
        if (
            not job.get("running", False)
            and job.get("finished_at") is not None
            and job.get("error")
            and terminal_key != self._reported_terminal_job
            and terminal_key not in reported_jobs
        ):
            self._reported_terminal_job = terminal_key
            reported_jobs[terminal_key] = None
            if len(reported_jobs) > 64:
                del reported_jobs[next(iter(reported_jobs))]
            self._reported_terminal_jobs = reported_jobs
            if not (
                job.get("job_id") is not None
                and job.get("job_id") == getattr(self, "_reported_start_failure_job", None)
            ):
                label = (
                    f"Pi job {job.get('name') or 'unnamed'} [{str(job['job_id'])[:8]}]"
                    if pi_job else "Controller job"
                )
                self.errorOccurred.emit(f"{label} failed: {job['error']}")

    def refresh_camera_image(self) -> None:
        # Repeating live-overlay timer ticks are intentionally dropped while
        # correction is in flight. Never overlap rectification jobs or turn a
        # high requested frame rate into an accumulating work queue.
        if (
            not self.runtime.running
            or self._camera_refresh_in_flight
            or self._camera_reconnect_in_flight
            or self._camera_review_active()
        ):
            return
        if self.runtime.context.bed.calibration is None:
            self._invalidate_camera_image()
            return
        validity = self.runtime.context.bed_calibration_validity()
        if str(validity.get("state", "UNKNOWN")) != "VALID":
            self._invalidate_camera_image()
            self._report_camera_mapping_required(validity)
            return
        area_provider = getattr(
            self.runtime.context,
            "trace_camera_work_area",
            None,
        )
        frame_provider = getattr(
            self.runtime.context,
            "current_honeycomb_coordinate_frame",
            None,
        )
        coordinate_frame = (
            frame_provider()
            if self._workspace_coordinate_space == "honeycomb_local"
            and callable(frame_provider)
            else None
        )
        if (
            self._workspace_coordinate_space == "honeycomb_local"
            and coordinate_frame is None
        ):
            self._invalidate_camera_image()
            message = (
                "The honeycomb-local camera view requires a current honeycomb "
                "reference; re-detect the honeycomb"
            )
            if self._camera_overlay_error_latched != message:
                self._camera_overlay_error_latched = message
                self.cameraOverlayErrorOccurred.emit(message)
            return
        expected_revision = (
            getattr(getattr(self.runtime.context, "lens", None), "model", None),
            self.runtime.context.bed.calibration,
            (
                None
                if coordinate_frame is None
                else tuple(coordinate_frame.provenance_signature)
            ),
        )
        camera_area = (
            WorkArea(
                0.0,
                coordinate_frame.width_mm,
                0.0,
                coordinate_frame.height_mm,
            )
            if coordinate_frame is not None
            else area_provider() if callable(area_provider) else None
        )
        source_generation = self._camera_source_generation
        self._camera_refresh_in_flight = True
        self._camera_refresh_generation = source_generation

        def corrected_image() -> QtGui.QImage:
            options: dict[str, Any] = {}
            if camera_area is not None:
                options["work_area"] = camera_area
            if coordinate_frame is not None:
                options["coordinate_frame"] = coordinate_frame
            frame = self.runtime.context.rectified_frame(
                refresh=True,
                **options,
            )
            return image_to_qimage(frame)

        self._run(
            corrected_image,
            on_success=lambda image, source_generation=source_generation,
            expected_revision=expected_revision,
            camera_area=camera_area: (
                self._camera_refresh_ready(
                    image,
                    source_generation,
                    expected_revision,
                    image_area=camera_area,
                )
            ),
            on_failure=lambda message, source_generation=source_generation,
            expected_revision=expected_revision: (
                self._camera_refresh_failed(
                    message,
                    source_generation,
                    expected_revision,
                )
            ),
            on_finished=lambda source_generation=source_generation: (
                self._camera_refresh_finished(source_generation)
            ),
            label="Corrected bed-image refresh",
            show_busy=False,
        )

    def retry_camera_image(self) -> None:
        """Refresh a healthy camera or release/reopen a failed camera device."""
        self._camera_error_latched = None
        self._camera_mapping_latched = None
        self._camera_overlay_error_latched = None
        if not self.runtime.running or self._camera_reconnect_in_flight:
            return
        status = self.runtime.context.camera.status()
        needs_reconnect = bool(
            not status.connected
            or status.frames_read <= 0
            or status.last_error
        )
        if not needs_reconnect:
            self.refresh_camera_image()
            return
        self._camera_source_generation += 1
        source_generation = self._camera_source_generation
        self._camera_refresh_in_flight = False
        self._camera_refresh_generation = None
        self._camera_refresh_pending = False
        self._camera_reconnect_in_flight = True
        self._camera_reconnect_generation = source_generation
        self.notice.emit("Reopening camera…")
        self._run(
            self.runtime.context.restart_camera,
            on_success=lambda _status, generation=source_generation: (
                self._camera_reconnect_ready(generation)
            ),
            on_failure=lambda message, generation=source_generation: (
                self._camera_reconnect_failed(message, generation)
            ),
            label="Camera reconnect",
            show_busy=False,
        )

    def _camera_reconnect_ready(self, source_generation: int) -> None:
        if source_generation != self._camera_reconnect_generation:
            return
        self._camera_reconnect_in_flight = False
        self._camera_reconnect_generation = None
        if source_generation != self._camera_source_generation:
            if self._camera_refresh_pending:
                self.request_camera_refresh()
            return
        self._camera_error_latched = None
        self._camera_mapping_latched = None
        self._camera_overlay_error_latched = None
        self.notice.emit("Camera reopened successfully")
        self.poll_status()
        self.request_camera_refresh()

    def _camera_reconnect_failed(self, message: str, source_generation: int) -> None:
        if source_generation != self._camera_reconnect_generation:
            return
        self._camera_reconnect_in_flight = False
        self._camera_reconnect_generation = None
        if source_generation != self._camera_source_generation:
            if self._camera_refresh_pending:
                self.request_camera_refresh()
            return
        self._camera_refresh_failed(
            message,
            source_generation,
            manual=True,
        )

    @QtCore.Slot()
    def _camera_refresh_finished(
        self,
        source_generation: int | None = None,
    ) -> None:
        if (
            source_generation is not None
            and source_generation != self._camera_refresh_generation
        ):
            return
        self._camera_refresh_in_flight = False
        self._camera_refresh_generation = None
        if self._camera_refresh_pending:
            QtCore.QTimer.singleShot(0, self.request_camera_refresh)

    def calibration_changed(self) -> None:
        """Invalidate an old corrected-frame result and request one replacement."""
        self._camera_source_generation += 1
        self._camera_error_latched = None
        self._camera_mapping_latched = None
        self._camera_overlay_error_latched = None
        self._invalidate_camera_image()
        self._trace_request_id += 1
        self._trace_cancel_event.set()
        self._trace_review_active = False
        self._trace_sample_image = None
        self._trace_sample_area = None
        self._trace_sample_signature = None
        self._template_match_request_id += 1
        self._template_match_cancel_event.set()
        self._template_review_active = False
        self._template_review_signature = None
        self.reviewEvidenceInvalidated.emit()
        self._camera_refresh_pending = True
        self.request_camera_refresh()

    def set_calibration_review_active(self, active: bool) -> None:
        self._calibration_review_active = bool(active)
        if not self._calibration_review_active and self._camera_refresh_pending:
            self.request_camera_refresh()

    def request_camera_refresh(self) -> None:
        if (
            self._calibration_review_active
            or self._camera_refresh_in_flight
            or self._camera_reconnect_in_flight
        ):
            # A boolean deliberately coalesces any number of explicit refresh
            # requests into at most one replacement after the current job.
            self._camera_refresh_pending = True
            return
        self._camera_refresh_pending = False
        self.refresh_camera_image()

    def _camera_refresh_ready(
        self,
        image: QtGui.QImage,
        source_generation: int | None = None,
        expected_revision: tuple[object | None, ...] | None = None,
        *,
        image_area: WorkArea | None = None,
    ) -> None:
        if (
            source_generation is not None
            and source_generation != self._camera_source_generation
        ):
            return
        if expected_revision is not None and not self._camera_revision_is_current(
            expected_revision
        ):
            self._invalidate_camera_image()
            self._camera_refresh_pending = True
            return
        if not self._camera_review_active():
            self._publish_camera_image(image, image_area=image_area)
        camera_recovered = self._camera_error_latched is not None
        mapping_recovered = self._camera_mapping_latched is not None
        overlay_recovered = self._camera_overlay_error_latched is not None
        self._camera_error_latched = None
        self._camera_mapping_latched = None
        self._camera_overlay_error_latched = None
        if camera_recovered:
            self.notice.emit("Camera image updates recovered")
        elif overlay_recovered:
            self.notice.emit("Corrected camera overlay recovered")
        elif mapping_recovered:
            self.notice.emit("Corrected camera overlay recovered")

    def _camera_revision_is_current(
        self,
        expected_revision: tuple[object | None, ...],
    ) -> bool:
        if (
            len(expected_revision) < 2
            or getattr(getattr(self.runtime.context, "lens", None), "model", None)
            is not expected_revision[0]
            or self.runtime.context.bed.calibration is not expected_revision[1]
        ):
            return False
        if len(expected_revision) < 3:
            return True
        frame_provider = getattr(
            self.runtime.context,
            "current_honeycomb_coordinate_frame",
            None,
        )
        frame = (
            frame_provider()
            if self._workspace_coordinate_space == "honeycomb_local"
            and callable(frame_provider)
            else None
        )
        current_signature = (
            None if frame is None else tuple(frame.provenance_signature)
        )
        return current_signature == expected_revision[2]

    def _current_review_signature(
        self,
        coordinate_frame: Any | None = None,
    ) -> tuple[object, ...]:
        context = self.runtime.context
        if coordinate_frame is None and self._workspace_coordinate_space == "honeycomb_local":
            frame_provider = getattr(
                context,
                "current_honeycomb_coordinate_frame",
                None,
            )
            coordinate_frame = frame_provider() if callable(frame_provider) else None
            if coordinate_frame is None:
                raise ValueError(
                    "The honeycomb-local review requires a current honeycomb reference"
                )
        mapping_provider = getattr(context, "bed_mapping_digest", None)
        mapping_digest = mapping_provider() if callable(mapping_provider) else None
        if mapping_digest is None:
            calibration = context.bed.calibration
            mapping_digest = (
                None
                if calibration is None
                else id(calibration)
            )
        frame_signature = (
            None
            if coordinate_frame is None
            else tuple(coordinate_frame.provenance_signature)
        )
        return (
            self._workspace_coordinate_space,
            frame_signature,
            mapping_digest,
        )

    def review_signature_is_current(self, signature: object) -> bool:
        if not isinstance(signature, (tuple, list)):
            return False
        try:
            return tuple(signature) == self._current_review_signature()
        except Exception:
            return False

    def _publish_camera_image(
        self,
        image: QtGui.QImage,
        *,
        image_area: WorkArea | None = None,
    ) -> None:
        self._camera_image_published = True
        if image_area is None:
            self.cameraImageReady.emit(image)
            return
        self.cameraImageReady.emit(
            {
                "image": image,
                "camera_image_area": {
                    "x_min": float(image_area.x_min),
                    "x_max": float(image_area.x_max),
                    "y_min": float(image_area.y_min),
                    "y_max": float(image_area.y_max),
                },
            }
        )

    def _invalidate_camera_image(self) -> None:
        if not self._camera_image_published:
            return
        self._camera_image_published = False
        self.cameraImageInvalidated.emit()

    def _report_camera_mapping_required(self, validity: dict[str, Any]) -> None:
        state = str(validity.get("state") or "UNKNOWN").upper()
        reasons = tuple(
            str(reason).strip()
            for reason in validity.get("reasons", [])
            if str(reason).strip()
        )
        lens = getattr(getattr(self.runtime.context, "lens", None), "model", None)
        quality = getattr(lens, "quality", {}) if lens is not None else {}
        gate = (
            str(quality.get("gate") or "").strip().lower()
            if isinstance(quality, dict)
            else ""
        )
        setup_tab = 2 if gate in {"pass", "warning"} else 1
        latch = "\0".join((state, *reasons, str(setup_tab)))
        if self._camera_mapping_latched == latch:
            return
        self._camera_mapping_latched = latch
        camera_status = self.runtime.context.camera.status()
        camera_online = bool(
            camera_status.connected
            and camera_status.frames_read > 0
            and not camera_status.last_error
        )
        self.cameraMappingRequired.emit(
            {
                "state": state,
                "reasons": list(reasons),
                "camera_online": camera_online,
                "setup_tab": setup_tab,
            }
        )

    def _remote_camera_configured(self) -> bool:
        settings = getattr(self.runtime, "settings", None)
        camera = getattr(settings, "camera", None)
        device = getattr(camera, "device", "")
        return isinstance(device, str) and device.lower().startswith("e3camera://")

    def _expected_remote_camera_offline(self, message: str) -> bool:
        # RemoteCameraService deliberately uses this prefix only for
        # network/socket-level failures. Profile, authentication, protocol,
        # and camera-side errors use different messages and remain visible.
        return (
            self._remote_camera_configured()
            and str(message).startswith(
                "Could not communicate with remote camera at "
            )
        )

    def _camera_refresh_failed(
        self,
        message: str,
        source_generation: int,
        expected_revision: tuple[object | None, ...] | None = None,
        *,
        manual: bool = False,
    ) -> None:
        if source_generation != self._camera_source_generation:
            return
        if expected_revision is not None and not self._camera_revision_is_current(
            expected_revision
        ):
            self._invalidate_camera_image()
            self._camera_refresh_pending = True
            return
        try:
            validity = self.runtime.context.bed_calibration_validity()
        except Exception:
            validity = None
        if (
            self.runtime.context.bed.calibration is not None
            and isinstance(validity, dict)
            and str(validity.get("state", "UNKNOWN")) != "VALID"
        ):
            self._invalidate_camera_image()
            self._report_camera_mapping_required(validity)
            return
        try:
            status = self.runtime.context.camera.status()
            camera_online = bool(
                status.connected and status.frames_read > 0 and not status.last_error
            )
        except Exception:
            camera_online = False
        if camera_online:
            if self._camera_overlay_error_latched is not None:
                return
            self._camera_overlay_error_latched = str(message)
            self.cameraOverlayErrorOccurred.emit(
                "The camera is online, but the corrected overlay could not be "
                "prepared. Automatic refresh will continue silently.\n\n"
                f"Details: {message}"
            )
            return
        if not manual and self._expected_remote_camera_offline(message):
            # Being away from the Pi is a normal operating condition. The
            # persistent OFFLINE status plus background probe backoff is enough;
            # do not steal focus with a modal startup warning.
            self._camera_error_latched = str(message)
            return

        if self._camera_error_latched is not None:
            return
        self._camera_error_latched = str(message)

        if self._remote_camera_configured():
            self.cameraErrorOccurred.emit(
                "Remote camera is unavailable. E3 will remain usable offline. "
                "Background connection checks will continue automatically; use "
                "Refresh camera to retry now.\n\n"
                f"Details: {message}"
            )
            return

        self.cameraErrorOccurred.emit(
            "Camera image updates failed. Another application may have exclusive "
            "control of the camera. Automatic refresh will continue silently; close "
            "the other application and use Refresh camera to retry.\n\n"
            f"Details: {message}"
        )

    def _sync_camera_timer(self) -> None:
        should_run = bool(
            self._live_camera_enabled
            and self.runtime.running
            and not self._camera_review_active()
        )
        if should_run:
            self._camera_live_timer.start()
        else:
            self._camera_live_timer.stop()

    def _camera_review_active(self) -> bool:
        return self._template_review_active or self._trace_review_active

    def _resume_live_camera_after_review(self, was_held: bool) -> None:
        self._sync_camera_timer()
        if (
            was_held
            and not self._camera_review_active()
            and self._live_camera_enabled
        ):
            self.refresh_camera_image()

    def set_template_review_active(self, active: bool) -> None:
        """Freeze live-camera replacement while an alignment overlay is reviewed."""

        was_held = self._camera_review_active()
        self._template_review_active = bool(active)
        self._resume_live_camera_after_review(was_held)

    def cancel_template_match(self) -> None:
        """Invalidate any in-flight match result and resume normal camera updates."""

        self._template_match_request_id += 1
        self._template_match_cancel_event.set()
        self._template_review_signature = None
        self.set_template_review_active(False)

    def cancel_trace_detection(self) -> None:
        """Invalidate trace work and release only the trace camera hold."""

        self._trace_request_id += 1
        self._trace_cancel_event.set()
        was_held = self._camera_review_active()
        self._trace_review_active = False
        self._trace_sample_image = None
        self._trace_sample_area = None
        self._trace_sample_signature = None
        self._resume_live_camera_after_review(was_held)

    def set_live_camera(self, enabled: bool, interval_ms: int | None = None) -> None:
        self._live_camera_enabled = bool(enabled)
        if interval_ms is not None:
            self.set_live_camera_interval(interval_ms)
        self._sync_camera_timer()
        if self._camera_live_timer.isActive():
            self.refresh_camera_image()

    def set_live_camera_interval(self, interval_ms: int) -> None:
        self._live_camera_interval_ms = max(
            _MIN_LIVE_CAMERA_INTERVAL_MS,
            min(_MAX_LIVE_CAMERA_INTERVAL_MS, int(interval_ms)),
        )
        self._camera_live_timer.setInterval(self._live_camera_interval_ms)
        self._sync_camera_timer()

    def capture_camera_still(self) -> None:
        self._run(
            lambda: self.runtime.context.save_capture(
                prefix="desktop-capture",
                undistort=True,
            ),
            on_success=lambda path: self.notice.emit(f"Saved {path.name}"),
            label="Camera capture",
        )

    @staticmethod
    def _sharpness_score(image: np.ndarray) -> float:
        """Variance-of-Laplacian focus metric; higher is sharper."""
        if image is None or image.size == 0:
            return 0.0
        gray = (
            cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            if image.ndim == 3
            else image
        )
        height, width = gray.shape[:2]
        if width >= 40 and height >= 40:
            margin_x = max(1, int(width * 0.10))
            margin_y = max(1, int(height * 0.10))
            region = gray[
                margin_y : height - margin_y,
                margin_x : width - margin_x,
            ]
        else:
            region = gray
        if region.size == 0:
            return 0.0
        return float(cv2.Laplacian(region, cv2.CV_64F).var())

    def _apply_camera_focus(
        self,
        autofocus: bool,
        focus_value: int,
    ) -> dict[str, Any]:
        camera = self.runtime.context.camera
        value = max(0, min(250, int(focus_value)))
        automatic = 1 if autofocus else 0
        requested: dict[str, int] = {
            "focus_automatic_continuous": automatic,
            "focus_auto": automatic,
        }
        if not autofocus:
            requested["focus_absolute"] = value

        configured_controls = dict(camera.settings.controls)
        configured_controls.update(requested)
        result, focus_frame = camera.apply_controls_and_snapshot(
            configured_controls,
            settle_seconds=0.35,
            timeout_seconds=2.0,
        )
        camera.settings.controls.update(requested)
        return {
            "autofocus": bool(autofocus),
            "focus_value": value,
            "sharpness": self._sharpness_score(focus_frame),
            "applied": dict(result.applied),
            "skipped": dict(result.skipped),
            "verified": dict(result.verified),
            "changed": True,
        }

    def apply_camera_focus(
        self,
        autofocus: bool,
        focus_value: int,
    ) -> None:
        self._run(
            lambda: self._apply_camera_focus(autofocus, focus_value),
            on_success=self._camera_focus_complete,
            label="Apply camera focus",
        )

    def measure_camera_sharpness(self) -> None:
        self._run(
            lambda: {
                "sharpness": self._sharpness_score(
                    self.runtime.context.camera.snapshot()
                ),
                "changed": False,
            },
            on_success=self.cameraFocusChanged.emit,
            label="Measure camera sharpness",
            show_busy=False,
        )

    def _camera_focus_sweep(
        self,
        start: int,
        end: int,
        step: int,
    ) -> dict[str, Any]:
        if any(type(value) is not int for value in (start, end, step)):
            raise ValueError("Focus sweep values must be integers")
        if not 0 <= start <= end <= 250 or not 1 <= step <= 50:
            raise ValueError("Focus sweep must stay within 0..250 with a positive step")
        values = list(range(start, end + 1, step))
        if values[-1] != end:
            values.append(end)
        if len(values) > 51:
            raise ValueError("Focus sweep is limited to 51 tested values")

        camera = self.runtime.context.camera
        controls = dict(camera.settings.controls)
        automatic = int(
            controls.get(
                "focus_automatic_continuous",
                controls.get("focus_auto", 0),
            )
        )
        original_focus = int(controls.get("focus_absolute", 0))

        def request(value: int, *, autofocus: int = 0) -> dict[str, int]:
            requested = dict(controls)
            requested["focus_automatic_continuous"] = autofocus
            requested["focus_auto"] = autofocus
            requested["focus_absolute"] = value
            return requested

        results: list[dict[str, Any]] = []
        try:
            for value in values:
                scores: list[float] = []
                for sample in range(3):
                    _result, frame = camera.apply_controls_and_snapshot(
                        request(value),
                        settle_seconds=0.35 if sample == 0 else 0.10,
                        timeout_seconds=2.0,
                    )
                    scores.append(self._sharpness_score(frame))
                results.append(
                    {
                        "focus": value,
                        "median_sharpness": float(np.median(scores)),
                        "scores": scores,
                    }
                )
        finally:
            camera.apply_controls_and_snapshot(
                request(original_focus, autofocus=automatic),
                settle_seconds=0.35,
                timeout_seconds=2.0,
            )

        return {
            "focus_sweep": results,
            "restored_focus": original_focus,
            "changed": False,
        }

    def test_camera_focus_range(self, start: int, end: int, step: int) -> None:
        self._run(
            lambda: self._camera_focus_sweep(start, end, step),
            on_success=self.cameraFocusChanged.emit,
            label="Test camera focus range",
        )

    def save_camera_focus(
        self,
        autofocus: bool,
        focus_value: int,
    ) -> None:
        def operation() -> dict[str, Any]:
            payload = self._apply_camera_focus(autofocus, focus_value)
            payload["saved_path"] = str(
                self._persist_camera_focus(autofocus, focus_value)
            )
            signature = signature_from_values(
                width=self.runtime.settings.camera.width,
                height=self.runtime.settings.camera.height,
                controls={
                    "focus_automatic_continuous": 1 if autofocus else 0,
                    "focus_absolute": focus_value,
                },
            )
            active = self.runtime.context.calibration_profiles.current
            payload.update(
                {
                    "calibration_profile_key": signature.key,
                    "calibration_profile_label": signature.label,
                    "active_calibration_profile_key": active.key,
                    "profile_restart_required": signature != active,
                }
            )
            return payload

        self._run(
            operation,
            on_success=self._camera_focus_complete,
            label="Save locked camera focus",
        )

    def _persist_camera_focus(
        self,
        autofocus: bool,
        focus_value: int,
    ) -> Path:
        settings = self.runtime.settings
        source = settings.source_path
        target = source
        if source.name != "local.json":
            target = settings.project_root / "config" / "local.json"

        payload: dict[str, Any] = {}
        if target.exists():
            payload = json.loads(target.read_text(encoding="utf-8"))
        elif source.exists():
            payload = json.loads(source.read_text(encoding="utf-8"))

        camera_payload = payload.setdefault("camera", {})
        controls = camera_payload.setdefault("controls", {})
        automatic = 1 if autofocus else 0
        controls["focus_automatic_continuous"] = automatic
        controls["focus_auto"] = automatic
        controls["focus_absolute"] = max(0, min(250, int(focus_value)))

        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return target

    def _camera_focus_complete(self, payload: dict[str, Any]) -> None:
        self.cameraFocusChanged.emit(payload)
        saved_path = payload.get("saved_path")
        if saved_path:
            profile = payload.get("calibration_profile_label")
            suffix = (
                f"; restart to activate calibration profile {profile}"
                if payload.get("profile_restart_required") and profile
                else ""
            )
            self.notice.emit(f"Saved locked camera focus to {saved_path}{suffix}")
        else:
            self.notice.emit("Applied camera focus")
        if self.runtime.context.bed.calibration is not None:
            if payload.get("changed") is True:
                self.calibration_changed()
            else:
                self.request_camera_refresh()

    def detect_trace_objects(self, raw_options: dict[str, Any]) -> int:
        self._trace_cancel_event.set()
        trace_cancel_event = threading.Event()
        self._trace_cancel_event = trace_cancel_event
        self._trace_request_id += 1
        request_id = self._trace_request_id
        preview_emitted = False
        self._trace_review_active = True
        self._trace_sample_image = None
        self._trace_sample_area = None
        self._trace_sample_signature = None
        self._sync_camera_timer()

        def operation() -> dict[str, Any]:
            request_started = time.perf_counter()
            context = self.runtime.context
            if context.bed.calibration is None:
                raise ValueError(
                    "Bed mapping is required before tracing camera objects"
                )
            frame_provider = getattr(
                context,
                "current_honeycomb_coordinate_frame",
                None,
            )
            coordinate_frame = (
                frame_provider()
                if self._workspace_coordinate_space == "honeycomb_local"
                and callable(frame_provider)
                else None
            )
            if (
                self._workspace_coordinate_space == "honeycomb_local"
                and coordinate_frame is None
            ):
                raise ValueError(
                    "The honeycomb-local Trace view requires a current honeycomb reference"
                )
            review_signature = self._current_review_signature(coordinate_frame)
            if coordinate_frame is None:
                camera_area = context.trace_camera_work_area()
            else:
                guarded_polygon = _configured_guarded_output_polygon(self.runtime)
                if guarded_polygon is None:
                    camera_area = WorkArea(
                        0.0,
                        coordinate_frame.width_mm,
                        0.0,
                        coordinate_frame.height_mm,
                    )
                else:
                    local_authority = [
                        coordinate_frame.machine_to_local(*point)
                        for point in guarded_polygon
                    ]
                    camera_area = WorkArea(
                        min(0.0, *(point[0] for point in local_authority)),
                        max(
                            coordinate_frame.width_mm,
                            *(point[0] for point in local_authority),
                        ),
                        min(0.0, *(point[1] for point in local_authority)),
                        max(
                            coordinate_frame.height_mm,
                            *(point[1] for point in local_authority),
                        ),
                    )
            capture_options: dict[str, Any] = {"work_area": camera_area}
            if coordinate_frame is not None:
                capture_options["coordinate_frame"] = coordinate_frame
            capture_timing: dict[str, float] = {}
            capture_options["timing"] = capture_timing
            image = context.capture_parked_trace_frame(**capture_options)
            background_image = None
            background_provider = getattr(
                context,
                "honeycomb_trace_background",
                None,
            )
            if coordinate_frame is not None and callable(background_provider):
                background_image = background_provider(
                    work_area=camera_area,
                    coordinate_frame=coordinate_frame,
                )
            options = TraceOptions.from_mapping(raw_options)
            guarded_output = _guarded_output_work_area(self.runtime)
            guarded_polygon = _configured_guarded_output_polygon(self.runtime)

            def raster_preview_ready(preview: object) -> None:
                """Publish immutable production arrays without doing Qt image work."""

                nonlocal preview_emitted
                if self._shutdown_started or request_id != self._trace_request_id:
                    return
                preview_emitted = True
                self.traceRasterPreviewReady.emit(
                    request_id,
                    {
                        "preview": preview,
                        "camera_image_area": {
                            "x_min": float(camera_area.x_min),
                            "x_max": float(camera_area.x_max),
                            "y_min": float(camera_area.y_min),
                            "y_max": float(camera_area.y_max),
                        },
                        "review_signature": review_signature,
                    },
                )

            detection_started = time.perf_counter()
            result = detect_objects(
                image,
                options,
                camera_area,
                self.runtime.settings.calibration.bed.pixels_per_mm,
                output_work_area=(
                    guarded_output
                    if coordinate_frame is None
                    else camera_area
                ),
                background_image=background_image,
                trace_roi_polygons_mm=_trace_roi_polygons(
                    self.runtime,
                    coordinate_frame,
                ),
                trace_output_polygon_mm=_trace_roi_polygons(
                    self.runtime,
                    coordinate_frame,
                )[-1],
                trace_roi_source=(
                    "guarded output geometry"
                    if coordinate_frame is None
                    else "honeycomb support intersected with guarded output geometry"
                ),
                reference_required=coordinate_frame is not None,
                reference_identity=(
                    None
                    if coordinate_frame is None
                    else coordinate_frame.provenance_digest
                ),
                raster_preview_callback=raster_preview_ready,
                cancel_check=trace_cancel_event.is_set,
            )
            detection_seconds = time.perf_counter() - detection_started
            output_polygon = None
            if coordinate_frame is not None:
                output_polygon, outside = _apply_local_output_review(
                    result.detections,
                    coordinate_frame,
                    guarded_polygon or guarded_output,
                )
                if outside:
                    result.message += (
                        f"; WARNING: {outside} outline"
                        f"{'s are' if outside != 1 else ' is'} outside the "
                        "configured machine-output envelope and left unchecked"
                    )
            support = _honeycomb_support_metadata(
                self.runtime,
                coordinate_space=self._workspace_coordinate_space,
            )
            payload = result.to_dict()
            diagnostics = payload.setdefault("diagnostics", {})
            if not isinstance(diagnostics, dict):
                diagnostics = {}
                payload["diagnostics"] = diagnostics
            timing = diagnostics.get("timing")
            if not isinstance(timing, dict):
                timing = {}
                diagnostics["timing"] = timing
            timing.update(capture_timing)
            timing["detect_objects_seconds"] = detection_seconds
            if output_polygon is not None:
                payload["output_work_area"] = None
                payload["output_polygon_local_mm"] = output_polygon
                payload["coordinate_space"] = "honeycomb_local"
            if support is not None:
                payload["honeycomb_support"] = support
                payload["message"] = (
                    f"{payload['message']} {support['message']}"
                )
            payload["camera_image_area"] = {
                "x_min": float(camera_area.x_min),
                "x_max": float(camera_area.x_max),
                "y_min": float(camera_area.y_min),
                "y_max": float(camera_area.y_max),
            }
            payload["request_id"] = request_id
            payload["camera_image"] = image_to_qimage(image)
            payload["_trace_sample_image"] = image
            payload["_trace_sample_area"] = camera_area
            payload["_trace_sample_signature"] = review_signature
            payload["review_signature"] = review_signature
            timing["request_total_seconds"] = (
                time.perf_counter() - request_started
            )
            return payload

        self._run(
            operation,
            on_success=lambda payload: self._trace_detection_complete(
                request_id,
                payload,
            ),
            on_failure=lambda message: self._trace_detection_failed(
                request_id,
                message,
                preview_emitted,
            ),
            label="Detect and trace objects",
            requires_controller=True,
        )
        return request_id

    @QtCore.Slot(int, object)
    def _trace_detection_complete(
        self,
        request_id: int,
        payload: dict[str, Any],
    ) -> None:
        if request_id != self._trace_request_id:
            return
        sample_image = payload.pop("_trace_sample_image", None)
        sample_area = payload.pop("_trace_sample_area", None)
        sample_signature = payload.pop("_trace_sample_signature", None)
        if (
            sample_signature is None
            and sample_image is None
            and sample_area is None
        ):
            # Compatibility for injected/non-camera test payloads. The real
            # operation always supplies all three private evidence fields.
            self.traceResultReady.emit(payload)
            return
        try:
            current_signature = self._current_review_signature()
        except Exception:
            current_signature = None
        if sample_signature is None or tuple(sample_signature) != current_signature:
            was_held = self._camera_review_active()
            self._trace_review_active = False
            self._trace_sample_image = None
            self._trace_sample_area = None
            self._trace_sample_signature = None
            self._resume_live_camera_after_review(was_held)
            message = (
                "the honeycomb or bed mapping changed during capture; "
                "run detection again"
            )
            self.traceDetectionFailed.emit(request_id, message, False)
            self.errorOccurred.emit(f"Detect and trace objects failed: {message}")
            return
        if request_id != self._trace_request_id:
            return
        self._trace_sample_image = (
            np.ascontiguousarray(sample_image).copy()
            if isinstance(sample_image, np.ndarray)
            else None
        )
        self._trace_sample_area = (
            sample_area if isinstance(sample_area, WorkArea) else None
        )
        self._trace_sample_signature = tuple(sample_signature)
        self.traceResultReady.emit(payload)

    @QtCore.Slot(int, str, bool)
    def _trace_detection_failed(
        self,
        request_id: int,
        message: str,
        retain_preview: bool,
    ) -> None:
        if request_id != self._trace_request_id:
            return
        self._trace_sample_image = None
        self._trace_sample_area = None
        self._trace_sample_signature = None
        if retain_preview:
            # The exact production raster is useful failure evidence. Keep the
            # corrected camera review frozen until Clear or another Detect.
            self._trace_review_active = True
            self._sync_camera_timer()
        else:
            was_held = self._camera_review_active()
            self._trace_review_active = False
            self._resume_live_camera_after_review(was_held)
        self.traceDetectionFailed.emit(
            request_id,
            message,
            bool(retain_preview),
        )
        self.errorOccurred.emit(f"Detect and trace objects failed: {message}")

    @staticmethod
    def _template_viability_reasons(
        alignment: TemplateAlignment,
    ) -> list[str]:
        reasons: list[str] = []
        if alignment.matched_count < _MIN_TEMPLATE_MATCHES:
            reasons.append(
                f"at least {_MIN_TEMPLATE_MATCHES} matched features are required"
            )
        if alignment.direct_match_count < _MIN_TEMPLATE_DIRECT_MATCHES:
            reasons.append(
                f"at least {_MIN_TEMPLATE_DIRECT_MATCHES} direct detections are required"
            )
        if alignment.coverage < _MIN_TEMPLATE_COVERAGE:
            reasons.append(
                f"feature coverage must be at least {_MIN_TEMPLATE_COVERAGE * 100:.0f}%"
            )
        if alignment.confidence < _MIN_TEMPLATE_CONFIDENCE:
            reasons.append(
                f"confidence must be at least {_MIN_TEMPLATE_CONFIDENCE * 100:.0f}%"
            )
        if (
            alignment.rms_error_mm is None
            or alignment.rms_error_mm > _MAX_TEMPLATE_RMS_ERROR_MM
        ):
            reasons.append(
                f"RMS residual must be at most {_MAX_TEMPLATE_RMS_ERROR_MM:.1f} mm"
            )
        if (
            alignment.max_error_mm is None
            or alignment.max_error_mm > _MAX_TEMPLATE_POINT_ERROR_MM
        ):
            reasons.append(
                "every matched feature must be within "
                f"{_MAX_TEMPLATE_POINT_ERROR_MM:.1f} mm"
            )
        if (
            alignment.scale_ratio is not None
            and abs(alignment.scale_ratio - 1.0) > _MAX_TEMPLATE_SCALE_ERROR
        ):
            reasons.append(
                "detected spacing has too much scale mismatch for rigid placement"
            )
        if (
            alignment.dimension_scale_ratio is not None
            and abs(alignment.dimension_scale_ratio - 1.0)
            > _MAX_TEMPLATE_SCALE_ERROR
        ):
            reasons.append(
                "detected feature dimensions have too much scale mismatch for "
                "rigid placement"
            )
        if alignment.pose_ambiguous:
            reasons.append(
                "the visible geometry cannot distinguish the sheet's 180-degree orientation"
            )
        return reasons

    @staticmethod
    def _template_alignment_payload(
        alignment: TemplateAlignment,
        trace_result: Any,
    ) -> dict[str, Any]:
        rms_error = (
            0.0
            if alignment.rms_error_mm is None
            else float(alignment.rms_error_mm)
        )
        maximum_error = (
            0.0
            if alignment.max_error_mm is None
            else float(alignment.max_error_mm)
        )
        viability_reasons = DesktopController._template_viability_reasons(alignment)
        return {
            "template_id": alignment.template_id,
            "template_name": alignment.template_name,
            "center_x_mm": float(alignment.translation_mm[0]),
            "center_y_mm": float(alignment.translation_mm[1]),
            "translation_mm": [
                float(alignment.translation_mm[0]),
                float(alignment.translation_mm[1]),
            ],
            "rotation_deg": float(alignment.rotation_deg),
            "matched": alignment.matched_count > 0,
            "alignment_viable": not viability_reasons,
            "viability_reasons": viability_reasons,
            "matched_count": int(alignment.matched_count),
            "direct_match_count": int(alignment.direct_match_count),
            "inferred_match_count": int(alignment.inferred_match_count),
            "feature_count": int(alignment.feature_count),
            "detection_count": int(alignment.detection_count),
            "coverage": float(alignment.coverage),
            "weighted_coverage": float(alignment.weighted_coverage),
            "detection_coverage": float(alignment.detection_coverage),
            "rms_error_mm": rms_error,
            "max_error_mm": maximum_error,
            "has_residual": alignment.rms_error_mm is not None,
            "scale_ratio": alignment.scale_ratio,
            "dimension_scale_ratio": alignment.dimension_scale_ratio,
            "confidence": float(alignment.confidence),
            "score": float(alignment.score),
            "ambiguous": bool(alignment.ambiguous),
            "pose_ambiguous": bool(alignment.pose_ambiguous),
            "warnings": list(alignment.warnings),
            "matches": [
                {
                    "feature_index": int(feature_index),
                    "detection_index": int(detection_index),
                    "residual_mm": float(residual),
                }
                for feature_index, detection_index, residual in alignment.matches
            ],
            "trace_mode": str(trace_result.mode_used),
            "trace_message": str(trace_result.message),
            "direct_detection_count": int(trace_result.direct_count),
            "inferred_detection_count": int(trace_result.inferred_count),
            "trace_options": trace_result.options.to_dict(),
        }

    def _match_cut_templates_once(
        self,
        request_id: int,
        templates: tuple[CutTemplate, ...],
        selected_template_id: str | None,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        context = self.runtime.context
        calibration = context.bed.calibration
        if calibration is None:
            raise ValueError(
                "Bed mapping is required before matching cutting templates"
            )
        image_to_machine = np.asarray(
            getattr(calibration, "image_to_machine", None),
            dtype=np.float64,
        )
        if image_to_machine.shape != (3, 3) or not np.all(
            np.isfinite(image_to_machine)
        ):
            raise ValueError(
                "Bed mapping must contain a finite 3x3 image-to-machine transform"
            )
        if (
            int(getattr(calibration, "image_width", 0)) <= 0
            or int(getattr(calibration, "image_height", 0)) <= 0
        ):
            raise ValueError(
                "Bed mapping must record a positive calibration image size"
            )
        pixels_per_mm = float(
            self.runtime.settings.calibration.bed.pixels_per_mm
        )
        if not np.isfinite(pixels_per_mm) or pixels_per_mm <= 0.0:
            raise ValueError(
                "Bed calibration pixels_per_mm must be a positive finite value"
            )

        template_ids = [template.id for template in templates]
        if len(template_ids) != len(set(template_ids)):
            raise ValueError("Cutting template IDs must be unique")
        if selected_template_id is not None:
            templates = tuple(
                template
                for template in templates
                if template.id == selected_template_id
            )
            if not templates:
                raise ValueError(
                    f"No cutting template has ID {selected_template_id!r}"
                )
        if not templates:
            raise ValueError("No cutting templates are available to match")

        frame_provider = getattr(
            context,
            "current_honeycomb_coordinate_frame",
            None,
        )
        coordinate_frame = (
            frame_provider()
            if self._workspace_coordinate_space == "honeycomb_local"
            and callable(frame_provider)
            else None
        )
        if (
            self._workspace_coordinate_space == "honeycomb_local"
            and coordinate_frame is None
        ):
            raise ValueError(
                "The honeycomb-local template review requires a current honeycomb reference"
            )
        review_signature = self._current_review_signature(coordinate_frame)
        if coordinate_frame is None:
            camera_area = self.runtime.settings.machine.work_area
        else:
            configured_polygon = _configured_guarded_output_polygon(self.runtime)
            if configured_polygon is None:
                camera_area = WorkArea(
                    0.0,
                    coordinate_frame.width_mm,
                    0.0,
                    coordinate_frame.height_mm,
                )
            else:
                local_authority = [
                    coordinate_frame.machine_to_local(*point)
                    for point in configured_polygon
                ]
                camera_area = WorkArea(
                    min(0.0, *(point[0] for point in local_authority)),
                    max(
                        coordinate_frame.width_mm,
                        *(point[0] for point in local_authority),
                    ),
                    min(0.0, *(point[1] for point in local_authority)),
                    max(
                        coordinate_frame.height_mm,
                        *(point[1] for point in local_authority),
                    ),
                )

        # One corrected camera frame is shared across every options group so an
        # automatic ranking never compares sheets captured at different times.
        frame_options: dict[str, Any] = {
            "refresh": True,
            "precision": True,
        }
        if coordinate_frame is not None:
            frame_options["work_area"] = camera_area
            frame_options["coordinate_frame"] = coordinate_frame
        image = context.rectified_frame(**frame_options)
        if image is None or image.size == 0:
            raise ValueError("The corrected camera frame is empty")
        background_image = None
        background_provider = getattr(
            context,
            "honeycomb_trace_background",
            None,
        )
        if coordinate_frame is not None and callable(background_provider):
            background_image = background_provider(
                work_area=camera_area,
                coordinate_frame=coordinate_frame,
            )

        option_groups: dict[
            str,
            tuple[TraceOptions, list[CutTemplate]],
        ] = {}
        for template in templates:
            options = TraceOptions.from_mapping(template.trace_options)
            key = json.dumps(
                options.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
            )
            if key not in option_groups:
                option_groups[key] = (options, [])
            option_groups[key][1].append(template)

        alignments: list[TemplateAlignment] = []
        traces_by_template: dict[str, Any] = {}
        evidence_by_template: dict[str, dict[str, Any]] = {}
        guarded_output = _guarded_output_work_area(self.runtime)
        guarded_polygon = _configured_guarded_output_polygon(self.runtime)
        output_polygon: list[list[float]] | None = None
        for options, grouped_templates in option_groups.values():
            trace_result = detect_objects(
                image,
                options,
                camera_area,
                pixels_per_mm,
                output_work_area=(
                    guarded_output
                    if coordinate_frame is None
                    else camera_area
                ),
                background_image=background_image,
                trace_roi_polygons_mm=_trace_roi_polygons(
                    self.runtime,
                    coordinate_frame,
                ),
                trace_output_polygon_mm=_trace_roi_polygons(
                    self.runtime,
                    coordinate_frame,
                )[-1],
                trace_roi_source=(
                    "guarded output geometry"
                    if coordinate_frame is None
                    else "honeycomb support intersected with guarded output geometry"
                ),
                reference_required=coordinate_frame is not None,
                reference_identity=(
                    None
                    if coordinate_frame is None
                    else coordinate_frame.provenance_digest
                ),
                cancel_check=cancel_check,
            )
            if coordinate_frame is not None:
                output_polygon, _outside = _apply_local_output_review(
                    trace_result.detections,
                    coordinate_frame,
                    guarded_polygon or guarded_output,
                )
            usable_detections, usable_indices, evidence = (
                _usable_template_detections(trace_result.detections)
            )
            evidence["usable_detection_indices"] = usable_indices
            for template in grouped_templates:
                traces_by_template[template.id] = trace_result
                evidence_by_template[template.id] = evidence
            alignments.extend(
                rank_templates(
                    grouped_templates,
                    usable_detections,
                    cancel_check=cancel_check,
                )
            )

        alignments.sort(
            key=lambda item: (
                item.score,
                item.coverage,
                item.direct_match_count,
                -(
                    item.rms_error_mm
                    if item.rms_error_mm is not None
                    else float("inf")
                ),
            ),
            reverse=True,
        )
        if selected_template_id is None and len(alignments) > 1:
            top = alignments[0]
            ambiguity_threshold = max(3.0, top.score * 0.06)
            close = [
                item
                for item in alignments
                if top.score >= 15.0
                and top.score - item.score <= ambiguity_threshold
            ]
            if len(close) > 1:
                warning = (
                    f"Template match is ambiguous: {len(close)} candidates "
                    f"score within {ambiguity_threshold:.1f} points."
                )
                for item in close:
                    item.ambiguous = True
                    if warning not in item.warnings:
                        item.warnings = (*item.warnings, warning)

        candidates = []
        for alignment in alignments:
            candidate = self._template_alignment_payload(
                alignment,
                traces_by_template[alignment.template_id],
            )
            evidence = evidence_by_template[alignment.template_id]
            index_map = evidence["usable_detection_indices"]
            for match in candidate["matches"]:
                detection_index = int(match["detection_index"])
                if 0 <= detection_index < len(index_map):
                    match["detection_index"] = int(index_map[detection_index])
            candidate.update(
                {
                    key: value
                    for key, value in evidence.items()
                    if key != "usable_detection_indices"
                }
            )
            evidence_warning = str(candidate["template_evidence_warning"])
            if evidence_warning and evidence_warning not in candidate["warnings"]:
                candidate["warnings"].append(evidence_warning)
            candidates.append(candidate)
        best = candidates[0]
        winning_trace = traces_by_template[str(best["template_id"])]
        mode = "selected" if selected_template_id is not None else "automatic"
        feature_match_found = bool(best["matched"])
        alignment_viable = bool(best["alignment_viable"])
        selection_required = bool(
            selected_template_id is None and best["ambiguous"]
        )
        accepted = alignment_viable and not selection_required
        if not feature_match_found:
            if (
                int(best.get("usable_detection_count", 0)) == 0
                and int(best.get("excluded_detection_count", 0)) > 0
            ):
                message = (
                    "Template matching was blocked because no complete, in-bounds "
                    "camera evidence remains. Reposition the sheet and capture again."
                )
            else:
                message = (
                    "No cutting template features matched the camera detections."
                )
        elif not alignment_viable:
            message = (
                f"{best['template_name']} produced a possible match, but it is not "
                "sufficiently constrained: "
                + "; ".join(best["viability_reasons"])
                + ". Use manual placement or improve the camera detection."
            )
        elif selection_required:
            message = (
                f"{best['template_name']} is the leading match, but another "
                "template is too close to choose automatically. Choose a candidate "
                "and run Align selected template before applying cut geometry."
            )
        elif mode == "selected":
            message = f"Aligned selected template {best['template_name']}."
        else:
            message = f"Identified and aligned {best['template_name']}."
        evidence_warning = str(best.get("template_evidence_warning", ""))
        if evidence_warning:
            message = f"{message} {evidence_warning}"
        payload = {
            **best,
            "matched": accepted,
            "feature_match_found": feature_match_found,
            "selection_required": selection_required,
            "request_id": int(request_id),
            "mode": mode,
            "selected_template_id": selected_template_id,
            "review_required": True,
            "message": message,
            "camera_image": image_to_qimage(image),
            "camera_image_area": {
                "x_min": float(camera_area.x_min),
                "x_max": float(camera_area.x_max),
                "y_min": float(camera_area.y_min),
                "y_max": float(camera_area.y_max),
            },
            "review_signature": review_signature,
            "detections": [
                detection.to_dict()
                for detection in winning_trace.detections
            ],
            "candidates": candidates,
        }
        if output_polygon is not None:
            payload["output_work_area"] = None
            payload["output_polygon_local_mm"] = output_polygon
            payload["coordinate_space"] = "honeycomb_local"
        return payload

    def match_cut_templates(
        self,
        templates: list[CutTemplate] | tuple[CutTemplate, ...],
        template_id: str | None = None,
    ) -> int:
        """Match one camera frame against all templates or one selected ID."""
        self._template_match_cancel_event.set()
        template_cancel_event = threading.Event()
        self._template_match_cancel_event = template_cancel_event
        self._template_match_request_id += 1
        request_id = self._template_match_request_id
        try:
            snapshots = tuple(
                CutTemplate.from_dict(template.to_dict())
                for template in templates
            )
        except Exception as exc:
            self._template_match_failed(request_id, str(exc))
            return request_id

        self._run(
            lambda: self._match_cut_templates_once(
                request_id,
                snapshots,
                None
                if template_id is None
                else str(template_id),
                cancel_check=template_cancel_event.is_set,
            ),
            on_success=lambda payload: self._template_match_complete(
                request_id,
                payload,
            ),
            on_failure=lambda message: self._template_match_failed(
                request_id,
                message,
            ),
            cancel=template_cancel_event.set,
            label="Match cutting templates",
        )
        return request_id

    @QtCore.Slot(int, object)
    def _template_match_complete(
        self,
        request_id: int,
        payload: dict[str, Any],
    ) -> None:
        if request_id != self._template_match_request_id:
            return
        signature = payload.get("review_signature")
        if signature is None:
            # Compatibility for non-camera harnesses; production matching always
            # supplies a signature from _match_cut_templates_once().
            self.templateMatchReady.emit(payload)
            return
        try:
            current_signature = self._current_review_signature()
        except Exception:
            current_signature = None
        if signature is None or tuple(signature) != current_signature:
            self._template_review_active = False
            self._template_review_signature = None
            self.templateMatchReady.emit(
                {
                    "request_id": int(request_id),
                    "matched": False,
                    "message": (
                        "Template matching was discarded because the honeycomb "
                        "or bed mapping changed during capture. Run alignment again."
                    ),
                    "error": True,
                    "candidates": [],
                }
            )
            return
        if request_id != self._template_match_request_id:
            return
        self._template_review_signature = tuple(signature)
        self.templateMatchReady.emit(payload)

    @QtCore.Slot(int, str)
    def _template_match_failed(
        self,
        request_id: int,
        message: str,
    ) -> None:
        if request_id != self._template_match_request_id:
            return
        self.templateMatchReady.emit(
            {
                "request_id": int(request_id),
                "matched": False,
                "message": f"Template matching failed: {message}",
                "error": True,
                "candidates": [],
            }
        )
        self.errorOccurred.emit(f"Match cutting templates failed: {message}")

    def sample_trace_color(self, x_mm: float, y_mm: float) -> None:
        cached_image = (
            None
            if self._trace_sample_image is None
            else self._trace_sample_image.copy()
        )
        cached_area = self._trace_sample_area
        cached_signature = self._trace_sample_signature

        def operation() -> dict[str, Any]:
            context = self.runtime.context
            if context.bed.calibration is None:
                raise ValueError(
                    "Bed mapping is required before sampling camera color"
                )
            frame_provider = getattr(
                context,
                "current_honeycomb_coordinate_frame",
                None,
            )
            coordinate_frame = (
                frame_provider()
                if self._workspace_coordinate_space == "honeycomb_local"
                and callable(frame_provider)
                else None
            )
            if (
                self._workspace_coordinate_space == "honeycomb_local"
                and coordinate_frame is None
            ):
                raise ValueError(
                    "The honeycomb-local color sample requires a current honeycomb reference"
                )
            current_signature = self._current_review_signature(coordinate_frame)
            if (
                cached_image is not None
                and (
                    cached_signature is None
                    or tuple(cached_signature) != current_signature
                )
            ):
                raise ValueError(
                    "The honeycomb or bed mapping changed after Trace capture; "
                    "run detection again before sampling a color"
                )
            fresh_area = cached_area
            if fresh_area is None and coordinate_frame is not None:
                fresh_area = WorkArea(
                    0.0,
                    coordinate_frame.width_mm,
                    0.0,
                    coordinate_frame.height_mm,
                )
            image = (
                context.rectified_frame(
                    refresh=True,
                    precision=True,
                    work_area=fresh_area,
                    coordinate_frame=(
                        coordinate_frame
                        if cached_area is None
                        else None
                    ),
                )
                if cached_image is None
                else cached_image
            )
            area = (
                fresh_area
                if fresh_area is not None
                else self.runtime.settings.machine.work_area
            )
            ppm = float(
                self.runtime.settings.calibration.bed.pixels_per_mm
            )
            pixel_x = (float(x_mm) - area.x_min) * ppm
            pixel_y = (area.y_max - float(y_mm)) * ppm
            payload = sample_color(image, pixel_x, pixel_y, radius_px=6)
            if coordinate_frame is None:
                machine_x, machine_y = float(x_mm), float(y_mm)
            else:
                machine_x, machine_y = coordinate_frame.local_to_machine(x_mm, y_mm)
                payload["honeycomb_x"] = float(x_mm)
                payload["honeycomb_y"] = float(y_mm)
            payload["machine_x"] = machine_x
            payload["machine_y"] = machine_y
            return payload

        self._run(
            operation,
            on_success=self.traceColorReady.emit,
            on_failure=self.traceColorFailed.emit,
            label="Sample trace color",
        )

    def connect_machine(self) -> None:
        self._run(
            self.runtime.context.machine.connect,
            on_success=lambda _: self._machine_changed("Controller connected"),
            label="Controller connection",
            machine_bound=True,
        )

    def reconnect_machine(self) -> None:
        """Explicitly replace an untrusted session without restoring motion trust."""

        self._run(
            self.runtime.context.machine.replace_connection,
            on_success=lambda _: self._machine_changed(
                "Controller reconnected; Home / park required"
            ),
            label="Controller reconnection",
            machine_bound=True,
        )

    def disconnect_machine(self) -> None:
        self._run(
            self.runtime.context.machine.disconnect,
            on_success=lambda _: self._machine_changed("Controller disconnected"),
            label="Controller disconnect",
            machine_bound=True,
        )

    def park_at_camera_pose(self) -> None:
        self._run(
            self.runtime.context.machine.prepare_photo_position,
            on_success=lambda result: self._machine_changed(
                f"Parked at X{result['position']['x']:.2f} Y{result['position']['y']:.2f}"
            ),
            label="Home and park",
            requires_controller=True,
        )


    def run_job(
        self,
        gcode: str,
        name: str,
        *,
        arm_phrase: str | None = None,
        honeycomb_signature: tuple[Any, ...] | None = None,
        guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None,
    ) -> None:
        def operation() -> dict[str, Any]:
            machine = self.runtime.context.machine
            try:
                # Reject malformed, stale calibration, or out-of-envelope
                # programs before the already-homed controller starts motion.
                self.runtime.context.validate_powered_calibration_support(gcode, name)
                program = (
                    machine.preflight_program(gcode)
                    if guarded_output_polygon_mm is None
                    else machine.preflight_program(
                        gcode,
                        guarded_output_polygon_mm=guarded_output_polygon_mm,
                    )
                )
                if honeycomb_signature is not None:
                    # Recheck immutable preparation provenance without moving
                    # or capturing. The operator already reviewed the traced
                    # image before generation.
                    self.runtime.context.validate_honeycomb_execution_binding(
                        honeycomb_signature
                    )
                return machine.start_preflighted_program(
                    program,
                    name,
                    authorization_phrase=arm_phrase,
                )
            except Exception:
                # A local failure must consume arming and attempt M5.  A remote
                # START response can be lost after the Pi accepted ownership;
                # abandoning that client attempt must never become an implicit
                # STOP of an otherwise healthy Pi-owned job.
                abandon = getattr(machine, "abandon_start_attempt", None)
                if callable(abandon):
                    abandon()
                else:
                    machine.disarm()
                raise

        def started(result: dict[str, Any]) -> None:
            self.jobStarted.emit(dict(result))
            if result.get("execution_owner") == "pi":
                self._machine_changed(f"Pi accepted and started {name}")
            else:
                self._machine_changed(f"Started {name}")

        self._run(
            operation,
            on_success=started,
            label="Start job",
            requires_controller=True,
        )

    def pause_resume(self) -> None:
        self.errorOccurred.emit(
            "Pause/resume is reserved in the desktop UI but will remain disabled "
            "until the Falcon controller's realtime hold/resume behavior is tested."
        )

    def emergency_stop(self) -> None:
        self.stopInitiated.emit()
        try:
            self.runtime.context.machine.request_stop(emergency=True)
        except Exception as exc:
            self.errorOccurred.emit(f"Software stop failed: {exc}")
            return
        self._machine_changed("Software stop sent; laser-off requested")

    def send_diagnostic(self, command: str) -> None:
        self._run(
            lambda: self.runtime.context.machine.send_command(command),
            on_success=lambda responses: self._diagnostic_complete(command, responses),
            label="Diagnostic command",
            requires_controller=True,
        )

    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> None:
        self._run(
            lambda: self.runtime.context.machine.jog(
                dx_mm,
                dy_mm,
                feed_mm_min,
            ),
            on_success=self._jog_complete,
            label="Jog",
            requires_controller=True,
        )

    def _jog_complete(self, result: dict[str, Any]) -> None:
        position = result["position"]
        self._machine_changed(
            f"Jog complete: X{float(position['x']):.3f} "
            f"Y{float(position['y']):.3f} mm"
        )

    def _diagnostic_complete(self, command: str, responses: list[str]) -> None:
        self.notice.emit(f"{command}: {' · '.join(responses) if responses else 'acknowledged'}")
        self.poll_status()

    def _machine_changed(self, message: str) -> None:
        self.notice.emit(message)
        self.poll_status()
