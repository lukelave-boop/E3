from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..camera.controls import apply_controls
from ..core import CoreRuntime
from ..templates import CutTemplate
from ..vision.object_trace import TraceOptions, detect_objects, sample_color
from ..vision.template_alignment import TemplateAlignment, rank_templates
from .qt import require_qt
from .tasks import FunctionTask

QtCore, QtGui, QtWidgets = require_qt()

_MIN_TEMPLATE_MATCHES = 3
_MIN_TEMPLATE_DIRECT_MATCHES = 2
_MIN_TEMPLATE_COVERAGE = 0.50
_MIN_TEMPLATE_CONFIDENCE = 0.55
_MAX_TEMPLATE_RMS_ERROR_MM = 1.0
_MAX_TEMPLATE_POINT_ERROR_MM = 2.0
_MAX_TEMPLATE_SCALE_ERROR = 0.035


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
    errorOccurred = QtCore.Signal(str)
    notice = QtCore.Signal(str)
    busyChanged = QtCore.Signal(bool)
    cameraFocusChanged = QtCore.Signal(dict)
    traceResultReady = QtCore.Signal(dict)
    traceColorReady = QtCore.Signal(dict)
    templateMatchReady = QtCore.Signal(dict)
    simulationFrameChanged = QtCore.Signal(dict)

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
        self._camera_refresh_in_flight = False
        self._camera_refresh_generation: int | None = None
        self._camera_source_generation = 0
        self._trace_request_id = 0
        self._trace_review_active = False
        self._template_match_request_id = 0
        self._template_review_active = False
        self._live_camera_enabled = False
        self._live_camera_interval_ms = 1000
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

    def _started(self) -> None:
        self._poll_timer.start()
        self.poll_status()
        if self.runtime.context.bed.calibration is not None:
            self.refresh_camera_image()
            self._sync_camera_timer()
        self.notice.emit("Core services started")

    def stop(self) -> None:
        self._poll_timer.stop()
        self._camera_live_timer.stop()
        self._trace_request_id += 1
        self._trace_review_active = False
        self._template_match_request_id += 1
        self._template_review_active = False
        self.thread_pool.waitForDone(5000)
        self._tasks.clear()
        self.runtime.stop()

    def _set_busy(self, delta: int) -> None:
        self._active_tasks = max(0, self._active_tasks + delta)
        self.busyChanged.emit(self._active_tasks > 0)

    def _run(
        self,
        callback: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
        label: str = "Operation",
        show_busy: bool = True,
    ) -> FunctionTask:
        if show_busy:
            self._set_busy(1)
        task = FunctionTask(callback)
        self._tasks.add(task)

        if on_success is not None:
            task.signals.succeeded.connect(on_success)

        if on_failure is None:
            task.signals.failed.connect(
                lambda message: self.errorOccurred.emit(f"{label} failed: {message}")
            )
        else:
            task.signals.failed.connect(on_failure)

        # Route cleanup through a QObject slot in the GUI thread. Do not drop
        # the final Python reference from the worker thread.
        task.signals.finished.connect(
            lambda task=task, show_busy=show_busy: self._task_finished(
                task, show_busy
            ),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self.thread_pool.start(task)
        return task

    @QtCore.Slot(object, bool)
    def _task_finished(
        self,
        task: FunctionTask,
        show_busy: bool = True,
    ) -> None:
        if show_busy:
            self._set_busy(-1)
        self._tasks.discard(task)

    def poll_status(self) -> None:
        if not self.runtime.running:
            return
        try:
            status = self.runtime.status()
        except Exception as exc:
            self.errorOccurred.emit(f"Status refresh failed: {exc}")
            return
        self.statusChanged.emit(status)

    def refresh_camera_image(self) -> None:
        if (
            not self.runtime.running
            or self._camera_refresh_in_flight
            or self._camera_review_active()
            or self.runtime.context.bed.calibration is None
        ):
            return
        if self.runtime.context.has_simulation_workspace_frame:
            self.cameraImageReady.emit(
                image_to_qimage(self.runtime.context.rectified_frame(refresh=True))
            )
            return
        source_generation = self._camera_source_generation
        self._camera_refresh_in_flight = True
        self._camera_refresh_generation = source_generation
        task = self._run(
            lambda: image_to_qimage(self.runtime.context.rectified_frame(refresh=True)),
            on_success=lambda image, source_generation=source_generation: (
                self._camera_refresh_ready(image, source_generation)
            ),
            on_failure=lambda message, source_generation=source_generation: (
                self._camera_refresh_failed(message, source_generation)
            ),
            label="Corrected bed-image refresh",
            show_busy=False,
        )
        task.signals.finished.connect(
            lambda source_generation=source_generation: (
                self._camera_refresh_finished(source_generation)
            ),
            QtCore.Qt.ConnectionType.QueuedConnection,
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

    def _camera_refresh_ready(
        self,
        image: QtGui.QImage,
        source_generation: int | None = None,
    ) -> None:
        if (
            source_generation is not None
            and source_generation != self._camera_source_generation
        ):
            return
        if not self._camera_review_active():
            self.cameraImageReady.emit(image)

    def _camera_refresh_failed(
        self,
        message: str,
        source_generation: int,
    ) -> None:
        if source_generation != self._camera_source_generation:
            return
        self.errorOccurred.emit(f"Corrected bed-image refresh failed: {message}")

    def _sync_camera_timer(self) -> None:
        context = getattr(self.runtime, "context", None)
        test_frame_active = bool(
            context is not None
            and getattr(context, "has_simulation_workspace_frame", False)
        )
        should_run = bool(
            self._live_camera_enabled
            and self.runtime.running
            and not self._camera_review_active()
            and not test_frame_active
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

    def activate_simulation_workspace_frame(
        self,
        image: np.ndarray,
        *,
        source_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Activate one frozen corrected frame behind all desktop vision tools."""

        info = self.runtime.context.set_simulation_workspace_frame(
            image,
            source_name=source_name,
            metadata=metadata,
        )
        self._camera_source_generation += 1
        self._camera_refresh_in_flight = False
        self._camera_refresh_generation = None
        self._trace_request_id += 1
        self._trace_review_active = False
        self._template_match_request_id += 1
        self._template_review_active = False
        self._sync_camera_timer()
        self.cameraImageReady.emit(image_to_qimage(self.runtime.context.rectified_frame()))
        self.simulationFrameChanged.emit(info)
        self.notice.emit(f"Using frozen test image: {source_name}")
        return info

    def return_to_synthetic_camera(self) -> None:
        """Clear the ephemeral corrected-frame override and resume live simulation."""

        if not self.runtime.context.has_simulation_workspace_frame:
            return
        self.runtime.context.clear_simulation_workspace_frame()
        self._camera_source_generation += 1
        self._camera_refresh_in_flight = False
        self._camera_refresh_generation = None
        self._trace_request_id += 1
        self._trace_review_active = False
        self._template_match_request_id += 1
        self._template_review_active = False
        self.simulationFrameChanged.emit(
            {"active": False, "source_name": "Synthetic camera", "metadata": {}}
        )
        self._sync_camera_timer()
        self.refresh_camera_image()
        self.notice.emit("Returned to the synthetic camera")

    def set_template_review_active(self, active: bool) -> None:
        """Freeze live-camera replacement while an alignment overlay is reviewed."""

        was_held = self._camera_review_active()
        self._template_review_active = bool(active)
        self._resume_live_camera_after_review(was_held)

    def cancel_template_match(self) -> None:
        """Invalidate any in-flight match result and resume normal camera updates."""

        self._template_match_request_id += 1
        self.set_template_review_active(False)

    def cancel_trace_detection(self) -> None:
        """Invalidate trace work and release only the trace camera hold."""

        self._trace_request_id += 1
        was_held = self._camera_review_active()
        self._trace_review_active = False
        self._resume_live_camera_after_review(was_held)

    def set_live_camera(self, enabled: bool, interval_ms: int | None = None) -> None:
        self._live_camera_enabled = bool(enabled)
        if interval_ms is not None:
            self.set_live_camera_interval(interval_ms)
        self._sync_camera_timer()
        if self._camera_live_timer.isActive():
            self.refresh_camera_image()

    def set_live_camera_interval(self, interval_ms: int) -> None:
        self._live_camera_interval_ms = max(250, min(10_000, int(interval_ms)))
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
        status = camera.status()
        value = max(0, min(250, int(focus_value)))
        if status.synthetic:
            return {
                "autofocus": bool(autofocus),
                "focus_value": value,
                "sharpness": self._sharpness_score(camera.snapshot()),
                "applied": {},
                "skipped": {"camera": "synthetic camera"},
                "changed": True,
            }

        automatic = 1 if autofocus else 0
        requested: dict[str, int] = {
            "focus_automatic_continuous": automatic,
            "focus_auto": automatic,
        }
        if not autofocus:
            requested["focus_absolute"] = value

        result = apply_controls(camera.settings.device, requested)
        camera.settings.controls.update(requested)
        time.sleep(0.35)
        return {
            "autofocus": bool(autofocus),
            "focus_value": value,
            "sharpness": self._sharpness_score(camera.snapshot()),
            "applied": dict(result.applied),
            "skipped": dict(result.skipped),
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
            self.notice.emit(f"Saved locked camera focus to {saved_path}")
        else:
            self.notice.emit("Applied camera focus")
        if self.runtime.context.bed.calibration is not None:
            self.refresh_camera_image()

    def detect_trace_objects(self, raw_options: dict[str, Any]) -> int:
        self._trace_request_id += 1
        request_id = self._trace_request_id
        self._trace_review_active = True
        self._sync_camera_timer()

        def operation() -> dict[str, Any]:
            context = self.runtime.context
            if context.bed.calibration is None:
                raise ValueError(
                    "Bed mapping is required before tracing camera objects"
                )
            image = context.rectified_frame(refresh=True)
            options = TraceOptions.from_mapping(raw_options)
            result = detect_objects(
                image,
                options,
                self.runtime.settings.machine.work_area,
                self.runtime.settings.calibration.bed.pixels_per_mm,
            )
            payload = result.to_dict()
            payload["request_id"] = request_id
            payload["camera_image"] = image_to_qimage(image)
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
            ),
            label="Detect and trace objects",
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
        self.traceResultReady.emit(payload)

    @QtCore.Slot(int, str)
    def _trace_detection_failed(self, request_id: int, message: str) -> None:
        if request_id != self._trace_request_id:
            return
        was_held = self._camera_review_active()
        self._trace_review_active = False
        self._resume_live_camera_after_review(was_held)
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

        # One corrected camera frame is shared across every options group so an
        # automatic ranking never compares sheets captured at different times.
        image = context.rectified_frame(refresh=True)
        if image is None or image.size == 0:
            raise ValueError("The corrected camera frame is empty")

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
        for options, grouped_templates in option_groups.values():
            trace_result = detect_objects(
                image,
                options,
                self.runtime.settings.machine.work_area,
                pixels_per_mm,
            )
            for template in grouped_templates:
                traces_by_template[template.id] = trace_result
            alignments.extend(
                rank_templates(grouped_templates, trace_result.detections)
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

        candidates = [
            self._template_alignment_payload(
                alignment,
                traces_by_template[alignment.template_id],
            )
            for alignment in alignments
        ]
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
            message = "No cutting template features matched the camera detections."
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
        return {
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
            "detections": [
                detection.to_dict()
                for detection in winning_trace.detections
            ],
            "candidates": candidates,
        }

    def match_cut_templates(
        self,
        templates: list[CutTemplate] | tuple[CutTemplate, ...],
        template_id: str | None = None,
    ) -> int:
        """Match one camera frame against all templates or one selected ID."""
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
            ),
            on_success=lambda payload: self._template_match_complete(
                request_id,
                payload,
            ),
            on_failure=lambda message: self._template_match_failed(
                request_id,
                message,
            ),
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
        def operation() -> dict[str, Any]:
            context = self.runtime.context
            if context.bed.calibration is None:
                raise ValueError(
                    "Bed mapping is required before sampling camera color"
                )
            image = context.rectified_frame(refresh=True)
            area = self.runtime.settings.machine.work_area
            ppm = float(
                self.runtime.settings.calibration.bed.pixels_per_mm
            )
            pixel_x = (float(x_mm) - area.x_min) * ppm
            pixel_y = (area.y_max - float(y_mm)) * ppm
            payload = sample_color(image, pixel_x, pixel_y, radius_px=6)
            payload["machine_x"] = float(x_mm)
            payload["machine_y"] = float(y_mm)
            return payload

        self._run(
            operation,
            on_success=self.traceColorReady.emit,
            label="Sample trace color",
        )

    def connect_machine(self) -> None:
        self._run(
            self.runtime.context.machine.connect,
            on_success=lambda _: self._machine_changed("Controller connected"),
            label="Controller connection",
        )

    def disconnect_machine(self) -> None:
        self._run(
            self.runtime.context.machine.disconnect,
            on_success=lambda _: self._machine_changed("Controller disconnected"),
            label="Controller disconnect",
        )

    def park_at_camera_pose(self) -> None:
        self._run(
            self.runtime.context.machine.prepare_photo_position,
            on_success=lambda result: self._machine_changed(
                f"Parked at X{result['position']['x']:.2f} Y{result['position']['y']:.2f}"
            ),
            label="Home and park",
        )


    def run_job(self, gcode: str, name: str, *, arm_phrase: str | None = None) -> None:
        def operation() -> dict[str, Any]:
            if arm_phrase is not None:
                self.runtime.context.machine.arm(arm_phrase)
            return self.runtime.context.machine.start_job(gcode, name)

        self._run(
            operation,
            on_success=lambda _: self._machine_changed(f"Started {name}"),
            label="Start job",
        )

    def pause_resume(self) -> None:
        self.errorOccurred.emit(
            "Pause/resume is reserved in the desktop UI but will remain disabled "
            "until the Falcon controller's realtime hold/resume behavior is tested."
        )

    def emergency_stop(self) -> None:
        self._run(
            lambda: self.runtime.context.machine.stop_job(emergency=True),
            on_success=lambda _: self._machine_changed("Software stop sent; laser-off requested"),
            label="Software stop",
        )

    def send_diagnostic(self, command: str) -> None:
        self._run(
            lambda: self.runtime.context.machine.send_command(command),
            on_success=lambda responses: self._diagnostic_complete(command, responses),
            label="Diagnostic command",
        )

    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> None:
        del dx_mm, dy_mm, feed_mm_min
        self.errorOccurred.emit(
            "Jogging is visible in the desktop shell but remains disabled until "
            "the core exposes a separately tested guarded jog operation."
        )

    def _diagnostic_complete(self, command: str, responses: list[str]) -> None:
        self.notice.emit(f"{command}: {' · '.join(responses) if responses else 'acknowledged'}")
        self.poll_status()

    def _machine_changed(self, message: str) -> None:
        self.notice.emit(message)
        self.poll_status()
