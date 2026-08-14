from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import cv2
import numpy as np

from ..calibration.bed import BedPoint
from ..calibration.registration import base_bed_grid_mark_sizes, base_bed_grid_targets
from ..calibration.support import HoneycombSupportReference
from ..config import effective_laser_output_area
from ..core import CoreRuntime
from ..units import parse_to_mm
from .controls import MeasurementSpinBox
from .qt import require_qt
from .setup_guide import show_setup_guide
from .tasks import FunctionTask

QtCore, QtGui, QtWidgets = require_qt()


def _work_area_reference_overlay(
    image: np.ndarray,
    bed: Any,
    work_area: Any,
    boundary_margin_mm: float,
    spot_offset_x_mm: float = 0.0,
    spot_offset_y_mm: float = 0.0,
    guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None,
    support_reference: HoneycombSupportReference | None = None,
    picked_image_points: tuple[tuple[float, float], ...] = (),
) -> np.ndarray:
    """Draw machine-coordinate and guarded-output references on a raw frame."""

    if image is None or image.size == 0:
        raise ValueError("Work-area reference image is empty")
    margin = float(boundary_margin_mm)
    if not math.isfinite(margin) or margin < 0.0:
        raise ValueError("Laser boundary margin must be finite and non-negative")
    guarded_area = effective_laser_output_area(
        work_area,
        margin,
        spot_offset_x_mm,
        spot_offset_y_mm,
    )
    guarded = (
        guarded_area.x_min,
        guarded_area.x_max,
        guarded_area.y_min,
        guarded_area.y_max,
    )

    def project(points: list[tuple[float, float]]) -> np.ndarray:
        projected = []
        for machine_x, machine_y in points:
            image_x, image_y = bed.mm_to_image(machine_x, machine_y)
            if not math.isfinite(image_x) or not math.isfinite(image_y):
                raise ValueError("Bed mapping produced a non-finite ruler overlay")
            projected.append(
                (
                    int(round(max(-1_000_000.0, min(1_000_000.0, image_x)))),
                    int(round(max(-1_000_000.0, min(1_000_000.0, image_y)))),
                )
            )
        return np.asarray(projected, dtype=np.int32).reshape(-1, 1, 2)

    def sampled_line(
        start: tuple[float, float],
        end: tuple[float, float],
        samples: int = 65,
    ) -> np.ndarray:
        return project(
            [
                (
                    start[0] + (end[0] - start[0]) * step / (samples - 1),
                    start[1] + (end[1] - start[1]) * step / (samples - 1),
                )
                for step in range(samples)
            ]
        )

    preview = image.copy()
    image_height, image_width = preview.shape[:2]
    short_edge = min(image_height, image_width)
    label_scale = max(0.8, min(1.8, short_edge / 650.0))
    label_thickness = 2 if short_edge >= 400 else 1
    border_width = max(2, int(round(short_edge / 270.0)))
    grid_width = max(1, int(round(short_edge / 700.0)))
    grid_layer = image.copy()
    x_values = np.arange(
        math.ceil(float(work_area.x_min) / 10.0) * 10.0,
        float(work_area.x_max) + 1e-9,
        10.0,
    )
    y_values = np.arange(
        math.ceil(float(work_area.y_min) / 10.0) * 10.0,
        float(work_area.y_max) + 1e-9,
        10.0,
    )
    for value in x_values:
        cv2.polylines(
            grid_layer,
            [
                sampled_line(
                    (float(value), float(work_area.y_min)),
                    (float(value), float(work_area.y_max)),
                )
            ],
            False,
            (190, 150, 65),
            grid_width,
            cv2.LINE_AA,
        )
    for value in y_values:
        cv2.polylines(
            grid_layer,
            [
                sampled_line(
                    (float(work_area.x_min), float(value)),
                    (float(work_area.x_max), float(value)),
                )
            ],
            False,
            (190, 150, 65),
            grid_width,
            cv2.LINE_AA,
        )
    cv2.addWeighted(grid_layer, 0.45, preview, 0.55, 0.0, preview)

    def rectangle(bounds: tuple[float, float, float, float], color: tuple[int, int, int], width: int) -> None:
        x_min, x_max, y_min, y_max = bounds
        points = []
        for start, end in (
            ((x_min, y_min), (x_max, y_min)),
            ((x_max, y_min), (x_max, y_max)),
            ((x_max, y_max), (x_min, y_max)),
            ((x_min, y_max), (x_min, y_min)),
        ):
            segment = sampled_line(start, end)
            points.extend(tuple(point[0]) for point in segment)
        cv2.polylines(
            preview,
            [np.asarray(points, dtype=np.int32).reshape(-1, 1, 2)],
            True,
            color,
            width,
            cv2.LINE_AA,
        )

    rectangle(
        (
            float(work_area.x_min),
            float(work_area.x_max),
            float(work_area.y_min),
            float(work_area.y_max),
        ),
        (0, 165, 255),
        border_width,
    )
    if guarded_output_polygon_mm is None:
        rectangle(guarded, (70, 220, 90), border_width)
    else:
        guarded_points: list[tuple[int, int]] = []
        for index, start in enumerate(guarded_output_polygon_mm):
            end = guarded_output_polygon_mm[
                (index + 1) % len(guarded_output_polygon_mm)
            ]
            segment = sampled_line(start, end)
            guarded_points.extend(tuple(point[0]) for point in segment)
        cv2.polylines(
            preview,
            [np.asarray(guarded_points, dtype=np.int32).reshape(-1, 1, 2)],
            True,
            (70, 220, 90),
            border_width,
            cv2.LINE_AA,
        )

    if support_reference is not None:
        support_points = support_reference.support_corners_machine_mm
        segments: list[tuple[int, int]] = []
        for index, start in enumerate(support_points):
            end = support_points[(index + 1) % len(support_points)]
            segment = sampled_line(start, end)
            segments.extend(tuple(point[0]) for point in segment)
        cv2.polylines(
            preview,
            [np.asarray(segments, dtype=np.int32).reshape(-1, 1, 2)],
            True,
            (220, 95, 205),
            border_width,
            cv2.LINE_AA,
        )

    marker_radius = max(7, int(round(short_edge / 90.0)))
    for index, (image_x, image_y) in enumerate(picked_image_points, start=1):
        point = (int(round(image_x)), int(round(image_y)))
        cv2.circle(preview, point, marker_radius, (0, 225, 255), -1, cv2.LINE_AA)
        cv2.circle(preview, point, marker_radius, (10, 20, 30), 2, cv2.LINE_AA)
        cv2.putText(
            preview,
            str(index),
            (point[0] + marker_radius + 3, point[1] - marker_radius - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.7, label_scale * 0.7),
            (0, 225, 255),
            label_thickness,
            cv2.LINE_AA,
        )

    def label_values(values: np.ndarray) -> list[float]:
        result = [float(value) for value in values[::4]]
        if len(values) and (not result or result[-1] != float(values[-1])):
            result.append(float(values[-1]))
        return result

    def draw_coordinate_label(
        text: str,
        point: np.ndarray,
        *,
        scale: float = label_scale,
    ) -> None:
        (text_width, text_height), baseline = cv2.getTextSize(
            text,
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            label_thickness,
        )
        padding = max(3, int(round(short_edge / 300.0)))
        x = int(point[0]) + padding
        y = int(point[1]) - padding
        x = max(padding, min(image_width - text_width - padding, x))
        y = max(text_height + padding, min(image_height - baseline - padding, y))
        cv2.rectangle(
            preview,
            (x - padding, y - text_height - padding),
            (x + text_width + padding, y + baseline + padding),
            (24, 28, 32),
            -1,
        )
        cv2.putText(
            preview,
            text,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            (238, 238, 238),
            label_thickness,
            cv2.LINE_AA,
        )

    x_label_values = label_values(x_values)
    y_label_values = label_values(y_values)
    if x_label_values and y_label_values:
        first_x_point = project(
            [(x_label_values[0], float(work_area.y_min))]
        )[0, 0]
        first_y_point = project(
            [(float(work_area.x_min), y_label_values[0])]
        )[0, 0]
        if float(np.linalg.norm(first_x_point - first_y_point)) <= 2.0:
            first_x_value = x_label_values.pop(0)
            first_y_value = y_label_values.pop(0)
            origin_text = (
                f"X/Y {first_x_value:g}"
                if abs(first_x_value - first_y_value) <= 1e-9
                else f"X{first_x_value:g}/Y{first_y_value:g}"
            )
            draw_coordinate_label(
                origin_text,
                first_x_point,
                scale=label_scale * 0.78,
            )

    for value in x_label_values:
        point = project([(float(value), float(work_area.y_min))])[0, 0]
        draw_coordinate_label(f"X{value:g}", point)
    for value in y_label_values:
        point = project([(float(work_area.x_min), float(value))])[0, 0]
        draw_coordinate_label(f"Y{value:g}", point)
    return preview

def _qimage(image: np.ndarray) -> QtGui.QImage:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width, channels = rgb.shape
    return QtGui.QImage(rgb.data, width, height, channels * width, QtGui.QImage.Format.Format_RGB888).copy()


class ImagePicker(QtWidgets.QLabel):
    pointPicked = QtCore.Signal(float, float)

    _MAX_ZOOM = 12.0

    def __init__(self, *, rotation_degrees: int = 0) -> None:
        super().__init__("No image captured")
        if type(rotation_degrees) is not int or rotation_degrees not in {
            0,
            90,
            180,
            270,
        }:
            raise ValueError("Image view rotation must be 0, 90, 180, or 270 degrees")
        self._rotation_degrees = rotation_degrees
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(520, 320)
        self.setStyleSheet("background: #15191e; border: 1px solid #4b5563;")
        self._image: QtGui.QImage | None = None
        self._source_width = 0
        self._source_height = 0
        self._display_rect = QtCore.QRectF()
        self._zoom = 1.0
        self._pan = QtCore.QPointF()
        self._pan_anchor: QtCore.QPointF | None = None
        self._pan_origin = QtCore.QPointF()
        self.setToolTip("Wheel to zoom; middle- or right-drag to pan; double-click to fit")

    def set_image(self, image: np.ndarray, *, preserve_view: bool = False) -> None:
        previous_size = self._image.size() if self._image is not None else None
        self._source_height, self._source_width = image.shape[:2]
        if self._rotation_degrees == 90:
            displayed = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        elif self._rotation_degrees == 180:
            displayed = cv2.rotate(image, cv2.ROTATE_180)
        elif self._rotation_degrees == 270:
            displayed = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        else:
            displayed = image
        self._image = _qimage(displayed)
        if not preserve_view or previous_size != self._image.size():
            self.reset_view()
        self._render()

    @property
    def zoom_factor(self) -> float:
        return self._zoom

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._pan = QtCore.QPointF()
        self._pan_anchor = None
        self.update()

    def _render(self) -> None:
        if self._image is None:
            return
        fit_scale = min(
            self.width() / self._image.width(),
            self.height() / self._image.height(),
        )
        scale = fit_scale * self._zoom
        width = self._image.width() * scale
        height = self._image.height() * scale
        center = QtCore.QPointF(self.width() / 2.0, self.height() / 2.0) + self._pan
        self._display_rect = QtCore.QRectF(
            center.x() - width / 2.0,
            center.y() - height / 2.0,
            width,
            height,
        )
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if self._image is None:
            super().paintEvent(event)
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(self._display_rect, self._image)
        painter.end()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() in (
            QtCore.Qt.MouseButton.MiddleButton,
            QtCore.Qt.MouseButton.RightButton,
        ):
            self._pan_anchor = event.position()
            self._pan_origin = QtCore.QPointF(self._pan)
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self._image is None or not self._display_rect.contains(event.position()):
            return
        display_x = (
            (event.position().x() - self._display_rect.x())
            * self._image.width()
            / self._display_rect.width()
        )
        display_y = (
            (event.position().y() - self._display_rect.y())
            * self._image.height()
            / self._display_rect.height()
        )
        if self._rotation_degrees == 90:
            source_x = display_y
            source_y = self._source_height - 1.0 - display_x
        elif self._rotation_degrees == 180:
            source_x = self._source_width - 1.0 - display_x
            source_y = self._source_height - 1.0 - display_y
        elif self._rotation_degrees == 270:
            source_x = self._source_width - 1.0 - display_y
            source_y = display_x
        else:
            source_x = display_x
            source_y = display_y
        self.pointPicked.emit(float(source_x), float(source_y))

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._pan_anchor is None:
            super().mouseMoveEvent(event)
            return
        self._pan = self._pan_origin + event.position() - self._pan_anchor
        self._render()
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._pan_anchor is not None and event.button() in (
            QtCore.Qt.MouseButton.MiddleButton,
            QtCore.Qt.MouseButton.RightButton,
        ):
            self._pan_anchor = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        self.reset_view()
        event.accept()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self._image is None or event.angleDelta().y() == 0:
            super().wheelEvent(event)
            return
        old_rect = QtCore.QRectF(self._display_rect)
        cursor = event.position()
        image_x = (cursor.x() - old_rect.x()) / old_rect.width()
        image_y = (cursor.y() - old_rect.y()) / old_rect.height()
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self._zoom = min(self._MAX_ZOOM, max(1.0, self._zoom * factor))
        self._render()
        anchored = QtCore.QPointF(
            self._display_rect.x() + image_x * self._display_rect.width(),
            self._display_rect.y() + image_y * self._display_rect.height(),
        )
        self._pan += cursor - anchored
        self._render()
        event.accept()


class MachineSetupDialog(QtWidgets.QDialog):
    """Native access to every shared camera/calibration inspection operation."""

    calibrationChanged = QtCore.Signal()
    registrationJobPrepared = QtCore.Signal(object)
    validationJobPrepared = QtCore.Signal(object)

    def __init__(self, runtime: CoreRuntime, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.context = runtime.context
        self._bed_image: np.ndarray | None = None
        self._work_area_reference_calibration: Any | None = None
        self._honeycomb_pick_active = False
        self._honeycomb_pick_points: list[tuple[float, float]] = []
        self._honeycomb_candidate_reference: HoneycombSupportReference | None = None
        self._honeycomb_dimensions_initialized = False
        self._bed_targets: list[dict[str, Any]] = []
        self._bed_target_index = 0
        self._fine_registration_analysis: dict[str, Any] | None = None
        self._fine_registration_measurements: list[dict[str, Any]] = []
        self._dense_analysis: dict[str, Any] | None = None
        self._dense_validation_analysis: dict[str, Any] | None = None
        self._photo_pose_confirmed = False
        self._registration_table_updating = False
        self._bed_map_valid = False
        self._bed_dependent_actions: list[QtWidgets.QWidget] = []
        self._bed_dependent_result_actions: list[QtWidgets.QWidget] = []
        self._thread_pool = QtCore.QThreadPool.globalInstance()
        self._active_task: FunctionTask | None = None
        self._active_operation_name: str | None = None
        self._operation_generation = 0
        self._stop_requested_generation: int | None = None
        self._operation_outcome = "Ready"
        self._lens_index_task: FunctionTask | None = None
        self._lens_index_generation = 0
        self._lens_index_auto_signature: tuple[tuple[str, int, int], ...] = ()
        self._lens_index_outcome = "Checkerboard evidence catalog is ready."
        self._lens_index_error: str | None = None
        self._lens_mutation_blocked = False
        self.setWindowTitle("Machine Setup")
        self.setMinimumSize(900, 680)
        self.resize(1080, 780)
        self._settings = QtCore.QSettings(
            str(self.context.settings.app.data_dir / "desktop-settings.ini"),
            QtCore.QSettings.Format.IniFormat,
        )
        self._camera_view_rotation = self.context.settings.camera.view_rotation_degrees

        layout = QtWidgets.QVBoxLayout(self)
        self.calibration_warning = QtWidgets.QLabel(
            "Calibration is not a safety function. Keep the laser incapable of emission while "
            "setting up the camera. Parking is available only when normal hardware and motion gates allow it."
        )
        self.calibration_warning.setWordWrap(True)
        self.calibration_warning.setObjectName("warningLabel")
        layout.addWidget(self.calibration_warning)
        connection_row = QtWidgets.QHBoxLayout()
        self.machine_connection_status = QtWidgets.QLabel()
        self.machine_connection_status.setObjectName("machineSetupConnectionStatus")
        self.machine_connection_button = QtWidgets.QPushButton("Connect machine")
        self.machine_connection_button.setMinimumWidth(170)
        self.machine_connection_button.clicked.connect(self.toggle_machine_connection)
        self.machine_stop_button = QtWidgets.QPushButton("STOP / LASER OFF")
        self.machine_stop_button.setObjectName("dangerButton")
        self.machine_stop_button.setToolTip(
            "Software stop; use the physical emergency stop in an actual emergency."
        )
        self.machine_stop_button.clicked.connect(self.request_software_stop)
        connection_row.addWidget(self.machine_connection_status, 1)
        connection_row.addWidget(self.machine_connection_button)
        connection_row.addWidget(self.machine_stop_button)
        layout.addLayout(connection_row)
        self.preferences_note = QtWidgets.QLabel(
            "Setup remembers this window, selected tab, simulation scene, cross sizes, "
            "marking speeds, and saved axis orientation. Marking power intentionally "
            "returns to 0% whenever Setup opens."
        )
        self.preferences_note.setWordWrap(True)
        self.preferences_note.setObjectName("mutedLabel")
        layout.addWidget(self.preferences_note)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_camera_tab()
        self._build_lens_tab()
        self._build_bed_tab()
        self._build_registration_tab()
        self._build_check_tab()
        self._restore_preferences()
        footer = QtWidgets.QHBoxLayout()
        self.operation_status = QtWidgets.QLabel(self._operation_outcome)
        self.operation_status.setObjectName("machineSetupOperationStatus")
        self.operation_status.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.operation_progress = QtWidgets.QProgressBar()
        self.operation_progress.setObjectName("machineSetupOperationProgress")
        self.operation_progress.setRange(0, 0)
        self.operation_progress.setTextVisible(False)
        self.operation_progress.setMaximumWidth(180)
        self.operation_progress.hide()
        self.dialog_buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        self.setup_guide_button = self.dialog_buttons.addButton(
            "Setup guide",
            QtWidgets.QDialogButtonBox.ButtonRole.HelpRole,
        )
        self.setup_guide_button.clicked.connect(
            lambda: show_setup_guide(self, self.tabs.currentIndex())
        )
        self.close_button = self.dialog_buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        self.dialog_buttons.rejected.connect(self.reject)
        footer.addWidget(self.operation_status, 1)
        footer.addWidget(self.operation_progress)
        footer.addWidget(self.dialog_buttons)
        layout.addLayout(footer)
        self._lens_index_start_timer = QtCore.QTimer(self)
        self._lens_index_start_timer.setSingleShot(True)
        self._lens_index_start_timer.timeout.connect(self._start_scheduled_lens_index)
        self._lens_index_poll_timer = QtCore.QTimer(self)
        self._lens_index_poll_timer.setInterval(100)
        self._lens_index_poll_timer.timeout.connect(self._poll_lens_index_progress)
        self.refresh_all()

    def _message(self, title: str, operation: Any) -> Any | None:
        try:
            result = operation()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, title, str(exc))
            return None
        return result

    @property
    def operation_busy(self) -> bool:
        return self._active_task is not None

    @property
    def lens_index_busy(self) -> bool:
        return self._lens_index_task is not None

    def _start_operation(
        self,
        name: str,
        operation: Callable[[], Any],
        on_success: Callable[[Any], None],
        *,
        requires_controller: bool = False,
        invalidate: Callable[[], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
    ) -> bool:
        """Own one slow setup operation while keeping the Qt event loop responsive."""
        if self.operation_busy:
            return False
        machine = self.context.machine
        machine_generation = machine.operation_generation()
        if invalidate is not None:
            invalidate()
        self._operation_generation += 1
        generation = self._operation_generation
        self._stop_requested_generation = None
        self._active_operation_name = name
        self._operation_outcome = f"{name} in progress. Software STOP remains available."
        self.operation_status.setText(self._operation_outcome)
        self.operation_progress.show()
        self.tabs.setEnabled(False)
        self.machine_connection_button.setEnabled(False)
        self.close_button.setEnabled(False)

        def scoped_operation() -> Any:
            with machine.operation_scope(machine_generation):
                if requires_controller:
                    machine.ensure_connected()
                return operation()

        task = FunctionTask(scoped_operation)
        self._active_task = task
        task.signals.succeeded.connect(
            lambda result, generation=generation: self._operation_succeeded(
                generation,
                name,
                result,
                on_success,
            ),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            lambda message, generation=generation: self._operation_failed(
                generation,
                name,
                message,
                on_failure,
            ),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            lambda generation=generation: self._operation_finished(generation),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self._thread_pool.start(task)
        return True

    def _operation_succeeded(
        self,
        generation: int,
        name: str,
        result: Any,
        on_success: Callable[[Any], None],
    ) -> None:
        if generation != self._operation_generation or not self.operation_busy:
            return
        if generation == self._stop_requested_generation:
            self._operation_outcome = (
                f"{name} was interrupted by Software STOP; its result was discarded."
            )
            return
        try:
            on_success(result)
        except Exception as exc:
            self._operation_outcome = f"{name} failed while presenting its result: {exc}"
            QtWidgets.QMessageBox.critical(self, name, str(exc))
        else:
            self._operation_outcome = f"{name} complete."

    def _operation_failed(
        self,
        generation: int,
        name: str,
        message: str,
        on_failure: Callable[[str], None] | None,
    ) -> None:
        if generation != self._operation_generation or not self.operation_busy:
            return
        if generation == self._stop_requested_generation:
            if on_failure is not None:
                on_failure(message)
            self._operation_outcome = (
                f"{name} stopped. Cleanup reported: {message}"
            )
            return
        self._operation_outcome = f"{name} failed: {message}"
        if on_failure is not None:
            on_failure(message)
        QtWidgets.QMessageBox.critical(self, name, message)

    def _operation_finished(self, generation: int) -> None:
        if generation != self._operation_generation or not self.operation_busy:
            return
        self._active_task = None
        self._active_operation_name = None
        self._stop_requested_generation = None
        self.operation_progress.hide()
        self.operation_status.setText(self._operation_outcome)
        self.tabs.setEnabled(True)
        self.machine_connection_button.setEnabled(True)
        self.close_button.setEnabled(True)

    def request_software_stop(self) -> None:
        """Use the same non-waiting stop latch as the main desktop STOP control."""
        try:
            self.context.machine.request_stop(emergency=True)
        except Exception as exc:
            self.operation_status.setText(f"Software STOP failed: {exc}")
            QtWidgets.QMessageBox.critical(self, "Software STOP", str(exc))
            return
        self._set_photo_pose_confirmed(False)
        if self.operation_busy:
            self._stop_requested_generation = self._operation_generation
        suffix = (
            f" Waiting for {self._active_operation_name} to finish cleanup."
            if self.operation_busy
            else ""
        )
        self.operation_status.setText(f"Software STOP requested.{suffix}")

    def _close_blocked(self) -> bool:
        if self.operation_busy:
            self.operation_status.setText(
                f"{self._active_operation_name} is still running. Use STOP / LASER OFF "
                "for motion, then wait for cleanup before closing."
            )
            return True
        if self.lens_index_busy:
            self.operation_status.setText(
                "Checkerboard evidence indexing is still running. Wait for it to finish "
                "before closing Setup."
            )
            return True
        return False

    @staticmethod
    def _pending_lens_index_signature(
        lens: dict[str, Any],
    ) -> tuple[tuple[str, int, int], ...]:
        return tuple(
            sorted(
                (
                    str(item.get("name") or ""),
                    int(item.get("size", 0)),
                    int(item.get("mtime_ns", 0)),
                )
                for item in lens.get("images") or []
                if item.get("index_state") == "pending"
            )
        )

    def _schedule_lens_index(self, lens: dict[str, Any]) -> None:
        signature = self._pending_lens_index_signature(lens)
        if (
            not signature
            or self.lens_index_busy
            or self._lens_index_start_timer.isActive()
            or signature == self._lens_index_auto_signature
        ):
            return
        self._lens_index_auto_signature = signature
        self._lens_index_start_timer.start(0)

    def _start_scheduled_lens_index(self) -> None:
        self._start_lens_index(retry_errors=False, force_all=False)

    def retry_lens_index(self) -> None:
        self._start_lens_index(retry_errors=True, force_all=True)

    def _start_lens_index(self, *, retry_errors: bool, force_all: bool) -> bool:
        if self.lens_index_busy:
            return False
        self._lens_index_generation += 1
        generation = self._lens_index_generation
        self._lens_index_error = None
        self._lens_index_outcome = "Checkerboard evidence indexing is queued."
        operation = (
            self.context.lens.reindex_all_captures
            if force_all
            else lambda: self.context.lens.index_pending_captures(
                retry_errors=retry_errors
            )
        )
        task = FunctionTask(operation)
        self._lens_index_task = task
        task.signals.succeeded.connect(
            lambda result, generation=generation: self._lens_index_succeeded(
                generation, result
            ),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            lambda message, generation=generation: self._lens_index_failed(
                generation, message
            ),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            lambda generation=generation: self._lens_index_finished(generation),
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        self._lens_index_poll_timer.start()
        self.refresh_all()
        self._thread_pool.start(task)
        return True

    def _lens_index_succeeded(self, generation: int, result: Any) -> None:
        if generation != self._lens_index_generation or not self.lens_index_busy:
            return
        summary = result if isinstance(result, dict) else {}
        indexed = int(summary.get("indexed_count", 0))
        usable = int(summary.get("usable_count", 0))
        errors = int(summary.get("error_count", 0))
        self._lens_index_outcome = (
            f"Evidence index complete: {indexed} updated, {usable} preview-detected, "
            f"{errors} errors."
        )

    def _lens_index_failed(self, generation: int, message: str) -> None:
        if generation != self._lens_index_generation or not self.lens_index_busy:
            return
        self._lens_index_error = message
        self._lens_index_outcome = f"Evidence indexing stopped: {message}"

    def _lens_index_finished(self, generation: int) -> None:
        if generation != self._lens_index_generation or not self.lens_index_busy:
            return
        self._lens_index_task = None
        self._lens_index_poll_timer.stop()
        if not self.operation_busy:
            self.operation_status.setText(self._lens_index_outcome)
        self.refresh_all()

    def _poll_lens_index_progress(self) -> None:
        if not self.lens_index_busy:
            self._lens_index_poll_timer.stop()
            return
        try:
            lens = self.context.lens.status()
        except Exception as exc:
            self._lens_index_status_text(
                {"state": "indexing", "indexing": True},
                status_error=str(exc),
            )
            return
        self._lens_index_status_text(lens.get("index") or {})

    def _lens_index_status_text(
        self,
        index: dict[str, Any],
        *,
        status_error: str | None = None,
    ) -> None:
        pending = int(index.get("pending_count", 0))
        errors = int(index.get("error_count", 0))
        ready = int(index.get("ready_count", 0))
        total = int(index.get("total_count", ready + pending + errors))
        indexing = bool(index.get("indexing"))
        working_width = int(index.get("working_width", 640))
        working_height = int(index.get("working_height", 360))
        busy = self.lens_index_busy or indexing
        if busy:
            completed = int(index.get("completed_count", 0))
            run_total = int(index.get("run_total_count", 0)) or pending
            current = str(index.get("current_name") or "")
            detail = f" · {current}" if current else ""
            self.lens_index_status.setText(
                f"Indexing checkerboard evidence {completed}/{run_total}{detail}. "
                f"Preview analysis is bounded to {working_width} x {working_height}; "
                "the final solve rechecks the original full-resolution pixels."
                + (f" Status update failed: {status_error}" if status_error else "")
            )
            if run_total > 0:
                self.lens_index_progress.setRange(0, run_total)
                self.lens_index_progress.setValue(min(completed, run_total))
            else:
                self.lens_index_progress.setRange(0, 0)
            self.lens_index_progress.show()
        else:
            self.lens_index_progress.hide()
            if self._lens_index_error:
                text = self._lens_index_outcome
            elif pending:
                text = (
                    f"{pending}/{total} captures await bounded preview indexing. "
                    "Indexing starts in the background."
                )
            elif errors:
                text = (
                    f"Evidence catalog: {ready}/{total} indexed, {errors} could not be "
                    "previewed. Retry the index or let the full-resolution solve report "
                    "whether enough originals are usable."
                )
            else:
                text = (
                    f"Evidence catalog ready: {ready}/{total} indexed. Preview analysis "
                    f"uses at most {working_width} x {working_height}; the final solve "
                    "always uses the originals."
                )
            self.lens_index_status.setText(text)
        self.lens_retry_index_button.setEnabled(
            not busy and total > 0
        )

    def _add_scrollable_tab(
        self,
        page: QtWidgets.QWidget,
        title: str,
    ) -> QtWidgets.QScrollArea:
        """Keep large setup pages inside the tab viewport at compact sizes."""
        page.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        if page.layout() is not None:
            page.layout().setSizeConstraint(
                QtWidgets.QLayout.SizeConstraint.SetMinimumSize
            )

        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("machineSetupTabScroll")
        scroll.setProperty("setupTabScroll", True)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        return scroll

    def _build_camera_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        self.camera_preview = ImagePicker(
            rotation_degrees=self._camera_view_rotation
        )
        layout.addWidget(self.camera_preview, 1)
        self.camera_status = QtWidgets.QLabel()
        self.camera_status.setWordWrap(True)
        layout.addWidget(self.camera_status)
        row = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton("Refresh raw preview")
        apply_controls = QtWidgets.QPushButton("Apply all configured controls")
        save = QtWidgets.QPushButton("Save corrected still")
        row.addWidget(refresh)
        row.addWidget(apply_controls)
        row.addWidget(save)
        layout.addLayout(row)
        precision = self.context.settings.camera.precision_capture
        precision_note = QtWidgets.QLabel(
            f"Precision stills wait {precision.settle_seconds:g} s, discard "
            f"{precision.discard_frames} fresh frames, then analyze "
            f"{precision.sample_frames} frames and use "
            f"{'a clarity-ranked stable-frame consensus' if precision.coordinate_strategy == 'stable_clarity_consensus' else 'the sharpest all-mark inlier frame' if precision.coordinate_strategy == 'sharpest_inlier_frame' else 'median mark coordinates'}. "
            "Live preview remains immediate."
        )
        precision_note.setWordWrap(True)
        precision_note.setObjectName("mutedLabel")
        layout.addWidget(precision_note)
        scene_row = QtWidgets.QHBoxLayout()
        scene_row.addWidget(QtWidgets.QLabel("Simulation scene"))
        self.synthetic_scene = QtWidgets.QComboBox()
        for label, value in (("Perspective bed", "bed"), ("Checkerboard", "checkerboard"), ("Workpiece", "workpiece")):
            self.synthetic_scene.addItem(label, value)
        self.synthetic_scene.setEnabled(self.context.settings.app.simulation)
        scene_row.addWidget(self.synthetic_scene, 1)
        set_scene = QtWidgets.QPushButton("Set scene")
        set_scene.setEnabled(self.context.settings.app.simulation)
        scene_row.addWidget(set_scene)
        layout.addLayout(scene_row)
        refresh.clicked.connect(self.refresh_camera)
        apply_controls.clicked.connect(self.apply_controls)
        save.clicked.connect(self.save_still)
        set_scene.clicked.connect(self.set_synthetic_scene)
        self.camera_scroll_area = self._add_scrollable_tab(tab, "1 · Camera")

    def _build_lens_tab(self) -> None:
        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 8, 0)
        instructions = QtWidgets.QLabel(
            "Print targets/checkerboard_9x6_20mm.svg at 100%. Capture varied views at the "
            "center, edges and corners with the complete flat checkerboard visible."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.lens_preview = ImagePicker(
            rotation_degrees=self._camera_view_rotation
        )
        self.lens_preview.setMinimumSize(520, 220)
        layout.addWidget(self.lens_preview)
        self.lens_status = QtWidgets.QLabel()
        self.lens_status.setWordWrap(True)
        layout.addWidget(self.lens_status)
        self.lens_resolution_status = QtWidgets.QLabel()
        self.lens_resolution_status.setWordWrap(True)
        self.lens_resolution_status.setObjectName("mutedLabel")
        layout.addWidget(self.lens_resolution_status)
        self.lens_bed_status = QtWidgets.QLabel()
        self.lens_bed_status.setWordWrap(True)
        layout.addWidget(self.lens_bed_status)

        captures_group = QtWidgets.QGroupBox("Checkerboard captures")
        captures_layout = QtWidgets.QVBoxLayout(captures_group)
        self.lens_index_status = QtWidgets.QLabel()
        self.lens_index_status.setWordWrap(True)
        self.lens_index_status.setObjectName("mutedLabel")
        captures_layout.addWidget(self.lens_index_status)
        self.lens_index_progress = QtWidgets.QProgressBar()
        self.lens_index_progress.setTextVisible(True)
        self.lens_index_progress.setFormat("%v / %m")
        self.lens_index_progress.hide()
        captures_layout.addWidget(self.lens_index_progress)
        self.lens_captures = QtWidgets.QTableWidget(0, 7)
        self.lens_captures.setHorizontalHeaderLabels(
            (
                "Capture",
                "Resolution",
                "Found",
                "Preview sharpness",
                "Coverage",
                "Region",
                "Quality",
            )
        )
        self.lens_captures.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.lens_captures.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.lens_captures.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.lens_captures.setAlternatingRowColors(True)
        self.lens_captures.setMinimumHeight(170)
        capture_header = self.lens_captures.horizontalHeader()
        for column in range(1, 6):
            capture_header.setSectionResizeMode(
                column,
                QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
            )
        capture_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        capture_header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeMode.Stretch)
        captures_layout.addWidget(self.lens_captures)
        evidence_actions = QtWidgets.QHBoxLayout()
        self.lens_delete_capture_button = QtWidgets.QPushButton("Delete selected capture")
        self.lens_clear_captures_button = QtWidgets.QPushButton("Clear all captures")
        self.lens_retry_index_button = QtWidgets.QPushButton("Re-index all captures")
        evidence_actions.addWidget(self.lens_delete_capture_button)
        evidence_actions.addWidget(self.lens_clear_captures_button)
        evidence_actions.addWidget(self.lens_retry_index_button)
        evidence_actions.addStretch(1)
        captures_layout.addLayout(evidence_actions)
        layout.addWidget(captures_group)

        quality_group = QtWidgets.QGroupBox("Solve quality")
        quality_layout = QtWidgets.QVBoxLayout(quality_group)
        self.lens_gate_status = QtWidgets.QLabel("No lens solve has been attempted")
        self.lens_gate_status.setWordWrap(True)
        quality_layout.addWidget(self.lens_gate_status)
        self.lens_view_errors = QtWidgets.QTableWidget(0, 5)
        self.lens_view_errors.setHorizontalHeaderLabels(
            ("Worst capture", "Used", "RMS px", "P95 px", "Max px")
        )
        self.lens_view_errors.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.lens_view_errors.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection
        )
        self.lens_view_errors.setMaximumHeight(155)
        error_header = self.lens_view_errors.horizontalHeader()
        error_header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            error_header.setSectionResizeMode(
                column,
                QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
            )
        quality_layout.addWidget(self.lens_view_errors)
        layout.addWidget(quality_group)

        controls = QtWidgets.QGridLayout()
        self.lens_preview_button = QtWidgets.QPushButton("Refresh preview")
        self.lens_capture_button = QtWidgets.QPushButton("Capture checkerboard view")
        self.lens_solve_button = QtWidgets.QPushButton("Solve current-resolution calibration")
        self.lens_clear_model_button = QtWidgets.QPushButton("Clear solved model")
        for index, button in enumerate(
            (
                self.lens_preview_button,
                self.lens_capture_button,
                self.lens_solve_button,
                self.lens_clear_model_button,
            )
        ):
            controls.addWidget(button, index // 2, index % 2)
        layout.addLayout(controls)
        self.lens_preview_button.clicked.connect(self.refresh_lens_preview)
        self.lens_capture_button.clicked.connect(self.capture_lens)
        self.lens_solve_button.clicked.connect(self.solve_lens)
        self.lens_clear_model_button.clicked.connect(self.clear_lens)
        self.lens_delete_capture_button.clicked.connect(self.delete_lens_capture)
        self.lens_clear_captures_button.clicked.connect(self.clear_lens_captures)
        self.lens_retry_index_button.clicked.connect(self.retry_lens_index)
        self.lens_captures.itemSelectionChanged.connect(self._refresh_lens_capture_actions)
        self.lens_scroll_area = self._add_scrollable_tab(body, "2 · Lens")

    def _build_bed_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)
        left_widget = QtWidgets.QWidget()
        left_widget.setMinimumWidth(360)
        left = QtWidgets.QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        self.bed_preview = ImagePicker(
            rotation_degrees=self._camera_view_rotation
        )
        self.bed_preview.setMinimumSize(360, 240)
        self.bed_preview.setMinimumHeight(270)
        self.bed_preview.pointPicked.connect(self._bed_point_picked)
        left.addWidget(self.bed_preview, 1)
        self.bed_status = QtWidgets.QLabel()
        self.bed_status.setWordWrap(True)
        left.addWidget(self.bed_status)

        automatic = QtWidgets.QGroupBox("Fresh automatic base mapping (keyed 5 x 5)")
        automatic.setMinimumHeight(145)
        automatic_layout = QtWidgets.QVBoxLayout(automatic)
        self.base_grid_status = QtWidgets.QLabel()
        self.base_grid_status.setWordWrap(True)
        automatic_layout.addWidget(self.base_grid_status)
        automatic_form = QtWidgets.QHBoxLayout()
        self.base_grid_power = QtWidgets.QDoubleSpinBox()
        self.base_grid_power.setRange(0.0, 100.0)
        self.base_grid_power.setDecimals(1)
        self.base_grid_power.setSuffix(" %")
        self.base_grid_power.setValue(0.0)
        self.base_grid_mark_size = MeasurementSpinBox()
        self.base_grid_mark_size.setRange(2.0, 5.0)
        self.base_grid_mark_size.setDecimals(1)
        self.base_grid_mark_size.setSuffix(" mm")
        self.base_grid_mark_size.setValue(4.0)
        self.base_grid_mark_size.valueChanged.connect(self._refresh_base_grid_geometry_status)
        self.base_grid_speed = MeasurementSpinBox("speed")
        self.base_grid_speed.setRange(1.0, 50000.0)
        self.base_grid_speed.setDecimals(0)
        self.base_grid_speed.setSuffix(" mm/min")
        self.base_grid_speed.setValue(self.context.settings.laser.engrave_feed_mm_min)
        for label, widget in (
            ("Verified power", self.base_grid_power),
            ("Regular cross", self.base_grid_mark_size),
            ("Speed", self.base_grid_speed),
        ):
            automatic_form.addWidget(QtWidgets.QLabel(label))
            automatic_form.addWidget(widget)
        automatic_layout.addLayout(automatic_form)
        automatic_buttons = QtWidgets.QGridLayout()
        base_powered = QtWidgets.QPushButton("Prepare powered base-map job")
        self.base_grid_capture_button = QtWidgets.QPushButton(
            "Home / park, capture and detect base grid"
        )
        automatic_buttons.addWidget(base_powered, 0, 0, 1, 2)
        automatic_buttons.addWidget(self.base_grid_capture_button, 1, 0, 1, 2)
        automatic_layout.addLayout(automatic_buttons)
        left.addWidget(automatic)
        base_powered.clicked.connect(lambda: self.prepare_base_bed_mapping_job(True))
        self.base_grid_capture_button.clicked.connect(self.capture_base_bed_mapping)
        self._refresh_base_grid_geometry_status()
        layout.addWidget(left_widget, 3)

        right_widget = QtWidgets.QWidget()
        right_widget.setMinimumWidth(280)
        right_widget.setMaximumWidth(430)
        right_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        right = QtWidgets.QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
        reference = QtWidgets.QGroupBox("Work-area ruler check")
        reference_layout = QtWidgets.QVBoxLayout(reference)
        self.work_area_reference_status = QtWidgets.QLabel()
        self.work_area_reference_status.setWordWrap(True)
        self.work_area_reference_status.setMinimumWidth(0)
        self.work_area_reference_status.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        reference_layout.addWidget(self.work_area_reference_status)
        reference_legend = QtWidgets.QGridLayout()
        reference_legend.setContentsMargins(0, 0, 0, 0)
        for row, (label, color) in enumerate(
            (
                ("Camera / work boundary", "rgb(255, 165, 0)"),
                ("Guarded laser output", "rgb(90, 220, 70)"),
                ("Detected honeycomb rulers", "rgb(205, 95, 220)"),
            )
        ):
            swatch = QtWidgets.QFrame()
            swatch.setFixedSize(28, 8)
            swatch.setStyleSheet(f"background: {color}; border: 0;")
            reference_legend.addWidget(swatch, row, 0)
            reference_legend.addWidget(QtWidgets.QLabel(label), row, 1)
        reference_legend.setColumnStretch(1, 1)
        reference_layout.addLayout(reference_legend)
        reference_note = QtWidgets.QLabel(
            "Compare both rigid honeycomb rulers to the 10 mm machine grid; large "
            "coordinate labels mark every 40 mm. This diagnoses origin, scale, and "
            "crop discrepancies; a movable ruler does not prove laser reach and "
            "this visual annotation never calibrates or authorizes output."
        )
        reference_note.setWordWrap(True)
        reference_note.setMinimumWidth(0)
        reference_note.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        reference_layout.addWidget(reference_note)
        self.work_area_reference_button = QtWidgets.QPushButton("Capture view")
        work_area_reference_tooltip = (
            "Home and park the machine, then capture the work-area ruler overlay."
        )
        self.work_area_reference_button.setProperty(
            "availableToolTip",
            work_area_reference_tooltip,
        )
        self.work_area_reference_button.setToolTip(work_area_reference_tooltip)
        self.work_area_reference_button.setAccessibleDescription(
            "Homes and parks the machine before capturing the work-area ruler overlay."
        )
        self.work_area_reference_button.clicked.connect(
            self.capture_work_area_reference
        )
        reference_layout.addWidget(self.work_area_reference_button)

        support_geometry = QtWidgets.QGridLayout()
        support_geometry.setContentsMargins(0, 0, 0, 0)
        self.honeycomb_ruler_mark = MeasurementSpinBox()
        self.honeycomb_ruler_mark.setRange(10.0, 1_000.0)
        self.honeycomb_ruler_mark.setDecimals(1)
        self.honeycomb_ruler_mark.setSuffix(" mm")
        self.honeycomb_ruler_mark.setValue(190.0)
        support_geometry.addWidget(QtWidgets.QLabel("Physical ruler span"), 0, 0)
        support_geometry.addWidget(self.honeycomb_ruler_mark, 0, 1)
        support_geometry.setColumnStretch(1, 1)
        reference_layout.addLayout(support_geometry)

        self.honeycomb_support_status = QtWidgets.QLabel()
        self.honeycomb_support_status.setWordWrap(True)
        self.honeycomb_support_status.setMinimumWidth(0)
        self.honeycomb_support_status.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )
        reference_layout.addWidget(self.honeycomb_support_status)
        support_buttons = QtWidgets.QHBoxLayout()
        self.honeycomb_support_auto_button = QtWidgets.QPushButton(
            "Detect honeycomb automatically"
        )
        self.honeycomb_support_auto_button.clicked.connect(
            self.detect_honeycomb_support_automatically
        )
        self.honeycomb_support_record_button = QtWidgets.QPushButton(
            "Fallback: detect with 3 hints"
        )
        self.honeycomb_support_record_button.clicked.connect(
            self.toggle_honeycomb_support_picking
        )
        self.honeycomb_support_clear_button = QtWidgets.QPushButton(
            "Clear visual reference"
        )
        self.honeycomb_support_clear_button.clicked.connect(
            self.clear_honeycomb_support_reference
        )
        support_buttons.addWidget(self.honeycomb_support_auto_button, 1)
        support_buttons.addWidget(self.honeycomb_support_record_button)
        support_buttons.addWidget(self.honeycomb_support_clear_button)
        reference_layout.addLayout(support_buttons)
        right.addWidget(reference)
        self._bed_dependent_actions.append(self.work_area_reference_button)
        self._bed_dependent_actions.append(self.honeycomb_support_auto_button)
        self._bed_dependent_actions.append(self.honeycomb_support_record_button)
        self._refresh_work_area_reference_status()

        manual_toggle = QtWidgets.QPushButton("Show manual / CSV fallback")
        manual_toggle.setCheckable(True)
        manual_body = QtWidgets.QWidget()
        manual_layout = QtWidgets.QVBoxLayout(manual_body)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_scroll = QtWidgets.QScrollArea()
        manual_scroll.setWidgetResizable(True)
        manual_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        manual_scroll.setWidget(manual_body)
        manual_scroll.setVisible(False)
        manual_toggle.toggled.connect(manual_scroll.setVisible)
        manual_toggle.toggled.connect(
            lambda checked: manual_toggle.setText(
                "Hide manual / CSV fallback" if checked else "Show manual / CSV fallback"
            )
        )
        right.addWidget(manual_toggle)
        right.addWidget(manual_scroll, 1)
        explanation = QtWidgets.QLabel(
            "Manual/CSV fallback: capture only at the repeatable photography pose. Click the exact center of a "
            "known mark, enter its commanded machine coordinates, then add the pair."
        )
        explanation.setWordWrap(True)
        manual_layout.addWidget(explanation)
        form = QtWidgets.QFormLayout()
        self.image_x = QtWidgets.QDoubleSpinBox()
        self.image_x.setRange(0, 10000)
        self.image_x.setDecimals(2)
        self.image_y = QtWidgets.QDoubleSpinBox()
        self.image_y.setRange(0, 10000)
        self.image_y.setDecimals(2)
        self.machine_x = MeasurementSpinBox()
        self.machine_x.setRange(-10000, 10000)
        self.machine_x.setDecimals(3)
        self.machine_y = MeasurementSpinBox()
        self.machine_y.setRange(-10000, 10000)
        self.machine_y.setDecimals(3)
        self.point_label = QtWidgets.QLineEdit()
        form.addRow("Image X (px)", self.image_x)
        form.addRow("Image Y (px)", self.image_y)
        form.addRow("Machine X (mm)", self.machine_x)
        form.addRow("Machine Y (mm)", self.machine_y)
        form.addRow("Label", self.point_label)
        manual_layout.addLayout(form)
        add = QtWidgets.QPushButton("Add point pair")
        add.clicked.connect(self.add_bed_point)
        manual_layout.addWidget(add)
        target_row = QtWidgets.QHBoxLayout()
        previous_target = QtWidgets.QPushButton("Previous target")
        next_target = QtWidgets.QPushButton("Next target")
        self.target_status = QtWidgets.QLabel("No coordinate CSV loaded")
        self.target_status.setWordWrap(True)
        target_row.addWidget(previous_target)
        target_row.addWidget(next_target)
        manual_layout.addLayout(target_row)
        manual_layout.addWidget(self.target_status)
        previous_target.clicked.connect(lambda: self.move_bed_target(-1))
        next_target.clicked.connect(lambda: self.move_bed_target(1))
        self.points = QtWidgets.QTableWidget(0, 5)
        self.points.setHorizontalHeaderLabels(("Label", "Image X", "Image Y", "Machine X", "Machine Y"))
        self.points.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.points.setMinimumHeight(150)
        manual_layout.addWidget(self.points, 1)
        controls = QtWidgets.QGridLayout()
        park = QtWidgets.QPushButton("Park at camera pose")
        capture = QtWidgets.QPushButton("Capture fixed bed image")
        remove = QtWidgets.QPushButton("Delete selected point")
        self.rough_grid_detect_button = QtWidgets.QPushButton(
            "Detect grid using current rough map"
        )
        import_csv = QtWidgets.QPushButton("Import coordinate CSV")
        solve = QtWidgets.QPushButton("Solve bed mapping")
        axis_group = QtWidgets.QGroupBox("Persistent mapping orientation")
        axis_group.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Maximum,
        )
        axis_layout = QtWidgets.QVBoxLayout(axis_group)
        self.reverse_x = QtWidgets.QCheckBox("Reverse X mapping — OFF")
        self.reverse_y = QtWidgets.QCheckBox("Reverse Y mapping — OFF")
        for toggle in (self.reverse_x, self.reverse_y):
            toggle.setMinimumHeight(30)
        self.axis_mapping_status = QtWidgets.QLabel()
        self.axis_mapping_status.setWordWrap(True)
        self.axis_mapping_status.setMinimumHeight(42)
        self.save_axis_mapping = QtWidgets.QPushButton("Confirm and save displayed axis states")
        axis_layout.addWidget(self.reverse_x)
        axis_layout.addWidget(self.reverse_y)
        axis_layout.addWidget(self.axis_mapping_status)
        axis_layout.addWidget(self.save_axis_mapping)
        right.addWidget(axis_group)
        clear = QtWidgets.QPushButton("Clear mapping and points")
        park.clicked.connect(self.park)
        capture.clicked.connect(self.capture_bed)
        remove.clicked.connect(self.delete_bed_point)
        self.rough_grid_detect_button.clicked.connect(self.detect_cross_grid)
        import_csv.clicked.connect(self.import_coordinate_csv)
        solve.clicked.connect(self.solve_bed)
        self.reverse_x.toggled.connect(lambda checked: self.set_bed_axis_reversed("x", checked))
        self.reverse_y.toggled.connect(lambda checked: self.set_bed_axis_reversed("y", checked))
        self.save_axis_mapping.clicked.connect(self.confirm_axis_mapping_state)
        clear.clicked.connect(self.clear_bed)
        manual_controls = QtWidgets.QGridLayout()
        for index, button in enumerate(
            (capture, remove, self.rough_grid_detect_button, import_csv, solve)
        ):
            manual_controls.addWidget(button, index // 2, index % 2)
        manual_layout.addLayout(manual_controls)
        controls.addWidget(park, 0, 0)
        controls.addWidget(clear, 0, 1)
        right.addLayout(controls)
        layout.addWidget(right_widget, 2)
        self.bed_scroll_area = self._add_scrollable_tab(tab, "3 · Bed mapping")

    def _build_registration_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)
        left = QtWidgets.QVBoxLayout()
        self.registration_preview = ImagePicker(
            rotation_degrees=self._camera_view_rotation
        )
        left.addWidget(self.registration_preview, 1)
        self.registration_status = QtWidgets.QLabel(
            "Prepare the powered registration marks, review the exact Preview, then run them."
        )
        self.registration_status.setWordWrap(True)
        left.addWidget(self.registration_status)
        layout.addLayout(left, 3)

        right = QtWidgets.QVBoxLayout()
        instructions = QtWidgets.QLabel(
            "This uses eight fresh crosses between the common 5×5 grid locations. "
            "Rigidly restrain a clean sacrificial surface at the calibrated height. "
            "The analysis can offer either a repeatable global translation or a "
            "strictly gated full-bed refinement. Both require explicit review and "
            "the independent holdout check on the next tab."
        )
        instructions.setWordWrap(True)
        right.addWidget(instructions)
        form = QtWidgets.QFormLayout()
        self.registration_power = QtWidgets.QDoubleSpinBox()
        self.registration_power.setRange(0.0, 100.0)
        self.registration_power.setDecimals(1)
        self.registration_power.setSuffix(" %")
        self.registration_power.setValue(0.0)
        self.registration_mark_size = MeasurementSpinBox()
        self.registration_mark_size.setRange(2.0, 10.0)
        self.registration_mark_size.setDecimals(1)
        self.registration_mark_size.setSuffix(" mm")
        self.registration_mark_size.setValue(5.0)
        self.registration_speed = MeasurementSpinBox("speed")
        self.registration_speed.setRange(1.0, 50000.0)
        self.registration_speed.setDecimals(0)
        self.registration_speed.setSuffix(" mm/min")
        self.registration_speed.setValue(self.context.settings.laser.engrave_feed_mm_min)
        form.addRow("Verified marking power", self.registration_power)
        form.addRow("Cross size", self.registration_mark_size)
        form.addRow("Marking speed", self.registration_speed)
        right.addLayout(form)

        prepare_row = QtWidgets.QHBoxLayout()
        self.registration_prepare_button = QtWidgets.QPushButton(
            "Prepare powered mark job"
        )
        prepare_row.addWidget(self.registration_prepare_button)
        right.addLayout(prepare_row)
        capture_row = QtWidgets.QHBoxLayout()
        capture = QtWidgets.QPushButton("Home / park, precision capture")
        self.registration_recapture_button = QtWidgets.QPushButton("Recapture without homing")
        self.registration_recapture_button.setEnabled(False)
        self.registration_recapture_button.setToolTip(
            "Capture another precision burst at the current camera pose. Use this to "
            "measure camera/detector repeatability separately from homing repeatability."
        )
        capture_row.addWidget(capture)
        capture_row.addWidget(self.registration_recapture_button)
        right.addLayout(capture_row)
        self.registration_results = QtWidgets.QTableWidget(0, 8)
        self.registration_results.setHorizontalHeaderLabels(
            ("Use", "#", "Command X", "Command Y", "Observed X", "Observed Y", "ΔX", "ΔY")
        )
        self.registration_results.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        right.addWidget(self.registration_results, 1)
        correction_row = QtWidgets.QHBoxLayout()
        self.apply_registration_button = QtWidgets.QPushButton("Apply reviewed translation")
        self.apply_registration_button.setEnabled(False)
        reset = QtWidgets.QPushButton("Reset fine translation")
        correction_row.addWidget(self.apply_registration_button)
        correction_row.addWidget(reset)
        right.addLayout(correction_row)
        map_row = QtWidgets.QHBoxLayout()
        self.apply_registration_map_button = QtWidgets.QPushButton("Apply reviewed full-bed map")
        self.apply_registration_map_button.setEnabled(False)
        reset_map = QtWidgets.QPushButton("Reset full-bed refinement")
        map_row.addWidget(self.apply_registration_map_button)
        map_row.addWidget(reset_map)
        right.addLayout(map_row)
        dense_box = QtWidgets.QGroupBox("Dense local correction (5 × 5)")
        dense_layout = QtWidgets.QVBoxLayout(dense_box)
        dense_note = QtWidgets.QLabel(
            "Use after the full-bed map and translation are stable. Burns 25 crosses, "
            "fits a bounded local residual mesh, and keeps the current map as its reset base."
        )
        dense_note.setWordWrap(True)
        dense_layout.addWidget(dense_note)
        dense_prepare = QtWidgets.QHBoxLayout()
        dense_powered = QtWidgets.QPushButton("Prepare powered 5×5 job")
        dense_prepare.addWidget(dense_powered)
        dense_layout.addLayout(dense_prepare)
        dense_capture = QtWidgets.QPushButton("Home / park, capture and fit 25 marks")
        dense_layout.addWidget(dense_capture)
        self.dense_status = QtWidgets.QLabel("No dense-grid capture analyzed")
        self.dense_status.setWordWrap(True)
        dense_layout.addWidget(self.dense_status)
        dense_actions = QtWidgets.QHBoxLayout()
        self.apply_dense_button = QtWidgets.QPushButton("Apply reviewed local mesh")
        self.apply_dense_button.setEnabled(False)
        reset_dense = QtWidgets.QPushButton("Reset local mesh")
        dense_actions.addWidget(self.apply_dense_button)
        dense_actions.addWidget(reset_dense)
        dense_layout.addLayout(dense_actions)
        right.addWidget(dense_box)
        layout.addLayout(right, 2)

        self.registration_prepare_button.clicked.connect(
            lambda: self.prepare_registration_job(True)
        )
        capture.clicked.connect(lambda: self.capture_fine_registration(home_first=True))
        self.registration_recapture_button.clicked.connect(lambda: self.capture_fine_registration(home_first=False))
        self.apply_registration_button.clicked.connect(self.apply_fine_registration)
        self.apply_registration_map_button.clicked.connect(self.apply_fine_registration_homography)
        reset.clicked.connect(self.reset_fine_registration)
        reset_map.clicked.connect(self.reset_fine_registration_homography)
        dense_powered.clicked.connect(lambda: self.prepare_dense_job(True))
        dense_capture.clicked.connect(self.capture_dense_calibration)
        self.apply_dense_button.clicked.connect(self.apply_dense_calibration)
        reset_dense.clicked.connect(self.reset_dense_calibration)
        self.registration_results.itemChanged.connect(self.registration_measurement_changed)
        self._bed_dependent_actions.extend(
            (
                self.registration_prepare_button,
                capture,
                reset,
                reset_map,
                dense_powered,
                dense_capture,
                reset_dense,
            )
        )
        self._bed_dependent_result_actions.extend(
            (
                self.apply_registration_button,
                self.apply_registration_map_button,
                self.apply_dense_button,
            )
        )
        self.registration_scroll_area = self._add_scrollable_tab(
            tab,
            "4 · Fine registration",
        )

    def _build_check_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)
        self.validation_preview = ImagePicker(
            rotation_degrees=self._camera_view_rotation
        )
        self.validation_preview.setText("No accuracy-validation capture")
        self.validation_preview.setMinimumSize(420, 360)
        layout.addWidget(self.validation_preview, 3)

        right = QtWidgets.QVBoxLayout()
        text = QtWidgets.QLabel(
            "This uses five new holdout crosses that are not part of the eight-mark "
            "fine-registration fit. Prepare, review, and run the guarded powered job "
            "on a clean restrained surface. Capture reports accuracy automatically; "
            "it never changes calibration."
        )
        text.setWordWrap(True)
        right.addWidget(text)

        form = QtWidgets.QFormLayout()
        self.validation_power = QtWidgets.QDoubleSpinBox()
        self.validation_power.setRange(0.0, 100.0)
        self.validation_power.setDecimals(1)
        self.validation_power.setSuffix(" %")
        self.validation_power.setValue(0.0)
        self.validation_mark_size = MeasurementSpinBox()
        self.validation_mark_size.setRange(2.0, 10.0)
        self.validation_mark_size.setDecimals(1)
        self.validation_mark_size.setSuffix(" mm")
        self.validation_mark_size.setValue(5.0)
        self.validation_speed = MeasurementSpinBox("speed")
        self.validation_speed.setRange(1.0, 50000.0)
        self.validation_speed.setDecimals(0)
        self.validation_speed.setSuffix(" mm/min")
        self.validation_speed.setValue(self.context.settings.laser.engrave_feed_mm_min)
        form.addRow("Verified marking power", self.validation_power)
        form.addRow("Cross size", self.validation_mark_size)
        form.addRow("Marking speed", self.validation_speed)
        right.addLayout(form)

        prepare_row = QtWidgets.QHBoxLayout()
        self.validation_prepare_button = QtWidgets.QPushButton(
            "Prepare powered validation job"
        )
        prepare_row.addWidget(self.validation_prepare_button)
        right.addLayout(prepare_row)
        validation_capture_row = QtWidgets.QHBoxLayout()
        validation_capture = QtWidgets.QPushButton("Home / park, precision capture")
        self.validation_recapture_button = QtWidgets.QPushButton("Recapture without homing")
        self.validation_recapture_button.setEnabled(False)
        self.validation_recapture_button.setToolTip(
            "Capture another precision burst without moving the machine. The machine "
            "must still be parked at the calibrated camera pose."
        )
        validation_capture_row.addWidget(validation_capture)
        validation_capture_row.addWidget(self.validation_recapture_button)
        right.addLayout(validation_capture_row)
        dense_validation_row = QtWidgets.QHBoxLayout()
        dense_validation_powered = QtWidgets.QPushButton("Prepare powered 4×4 mesh check")
        dense_validation_row.addWidget(dense_validation_powered)
        right.addLayout(dense_validation_row)
        dense_validation_capture = QtWidgets.QPushButton("Home / park, capture and score 16 interstitial marks")
        right.addWidget(dense_validation_capture)
        self.apply_dense_validation_refinement_button = QtWidgets.QPushButton("Apply reviewed validation refinement")
        self.apply_dense_validation_refinement_button.setEnabled(False)
        right.addWidget(self.apply_dense_validation_refinement_button)
        confirmation_row = QtWidgets.QHBoxLayout()
        confirmation_powered = QtWidgets.QPushButton("Prepare powered shifted confirmation")
        confirmation_row.addWidget(confirmation_powered)
        right.addLayout(confirmation_row)
        confirmation_capture = QtWidgets.QPushButton(
            "Home / park, capture and score shifted confirmation"
        )
        right.addWidget(confirmation_capture)
        self.validation_status = QtWidgets.QLabel("No validation capture analyzed")
        self.validation_status.setWordWrap(True)
        right.addWidget(self.validation_status)
        self.validation_results = QtWidgets.QTableWidget(0, 8)
        self.validation_results.setHorizontalHeaderLabels(
            (
                "#",
                "Command X",
                "Command Y",
                "Observed X",
                "Observed Y",
                "ΔX",
                "ΔY",
                "Error",
            )
        )
        self.validation_results.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        right.addWidget(self.validation_results, 1)

        diagnostics = QtWidgets.QGroupBox("Other camera diagnostics")
        diagnostics_layout = QtWidgets.QVBoxLayout(diagnostics)
        row = QtWidgets.QHBoxLayout()
        workpiece = QtWidgets.QPushButton("Detect workpiece")
        fiducials = QtWidgets.QPushButton("Detect ArUco fiducials")
        row.addWidget(workpiece)
        row.addWidget(fiducials)
        diagnostics_layout.addLayout(row)
        self.check_results = QtWidgets.QPlainTextEdit()
        self.check_results.setReadOnly(True)
        self.check_results.setMaximumHeight(110)
        diagnostics_layout.addWidget(self.check_results)
        right.addWidget(diagnostics)
        layout.addLayout(right, 2)

        self.validation_prepare_button.clicked.connect(
            lambda: self.prepare_accuracy_validation_job(True)
        )
        validation_capture.clicked.connect(lambda: self.capture_accuracy_validation(home_first=True))
        self.validation_recapture_button.clicked.connect(lambda: self.capture_accuracy_validation(home_first=False))
        dense_validation_powered.clicked.connect(lambda: self.prepare_dense_validation_job(True))
        dense_validation_capture.clicked.connect(self.capture_dense_validation)
        self.apply_dense_validation_refinement_button.clicked.connect(self.apply_dense_validation_refinement)
        confirmation_powered.clicked.connect(lambda: self.prepare_dense_validation_job(True, confirmation=True))
        confirmation_capture.clicked.connect(lambda: self.capture_dense_validation(confirmation=True))
        workpiece.clicked.connect(self.detect_workpiece)
        fiducials.clicked.connect(self.detect_fiducials)
        self._bed_dependent_actions.extend(
            (
                self.validation_prepare_button,
                validation_capture,
                dense_validation_powered,
                dense_validation_capture,
                confirmation_powered,
                confirmation_capture,
                workpiece,
            )
        )
        self._bed_dependent_result_actions.append(
            self.apply_dense_validation_refinement_button
        )
        self.validation_scroll_area = self._add_scrollable_tab(
            tab,
            "5 · Accuracy validation",
        )

    @staticmethod
    def _lens_capture_region(center: Any) -> str:
        if not isinstance(center, list) or len(center) != 2:
            return "n/a"
        horizontal = (
            "left"
            if float(center[0]) < 1 / 3
            else "right"
            if float(center[0]) >= 2 / 3
            else "center"
        )
        vertical = (
            "top"
            if float(center[1]) < 1 / 3
            else "bottom"
            if float(center[1]) >= 2 / 3
            else "middle"
        )
        return (
            "center"
            if horizontal == "center" and vertical == "middle"
            else f"{vertical}-{horizontal}"
        )

    @staticmethod
    def _lens_capture_quality(quality: Any) -> str:
        if not isinstance(quality, dict) or not quality:
            return "n/a"
        contrast = float(quality.get("contrast_span", 0.0))
        shadows = float(quality.get("shadow_clip_percent", 0.0))
        highlights = float(quality.get("highlight_clip_percent", 0.0))
        return f"Contrast {contrast:.0f}; clipped {shadows:.1f}/{highlights:.1f}%"

    @staticmethod
    def _lens_capture_sharpness(quality: Any) -> str:
        if not isinstance(quality, dict) or not quality:
            return "n/a"
        width = int(quality.get("width", quality.get("measurement_width", 0)))
        height = int(quality.get("height", quality.get("measurement_height", 0)))
        score = float(quality.get("sharpness", 0.0))
        return f"{score:.1f} @ {width}x{height}" if width > 0 and height > 0 else f"{score:.1f}"

    def _populate_lens_captures(self, lens: dict[str, Any]) -> None:
        selected_name = None
        selected_items = self.lens_captures.selectedItems()
        if selected_items:
            selected_name = self.lens_captures.item(selected_items[0].row(), 0).data(
                QtCore.Qt.ItemDataRole.UserRole
            )
        images = list(lens.get("images") or [])
        self.lens_captures.setRowCount(len(images))
        selected_row = -1
        for row, image in enumerate(images):
            quality = image.get("quality") or {}
            found = bool(image.get("found"))
            index_state = str(image.get("index_state") or "ready")
            found_text = (
                "Pending"
                if index_state == "pending"
                else "Index error"
                if index_state == "error"
                else f"Yes ({int(image.get('corner_count', 0))})"
                if found
                else "No"
            )
            values = (
                str(image.get("name", "")),
                f"{int(image.get('width', 0))} x {int(image.get('height', 0))}",
                found_text,
                self._lens_capture_sharpness(quality),
                f"{float(image.get('board_coverage_percent', 0.0)):.1f}%" if found else "n/a",
                self._lens_capture_region(image.get("board_center")),
                self._lens_capture_quality(quality),
            )
            current_resolution = bool(image.get("selected_for_active_resolution"))
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, values[0])
                if current_resolution:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    tooltip = "Capture matches the current camera resolution"
                else:
                    item.setForeground(QtGui.QColor("#8f98a3"))
                    tooltip = "Stored capture from a different camera resolution"
                if index_state == "pending":
                    tooltip += ". Awaiting bounded background preview indexing"
                elif index_state == "error":
                    tooltip += ". " + str(
                        image.get("index_error") or "Preview indexing failed"
                    )
                if column == 3 and quality:
                    tooltip += (
                        ". Exact encoded-file variance-of-Laplacian preview score; "
                        "compare captures measured at the same displayed dimensions"
                    )
                item.setToolTip(tooltip)
                self.lens_captures.setItem(row, column, item)
            if values[0] == selected_name:
                selected_row = row
        if selected_row >= 0:
            self.lens_captures.selectRow(selected_row)
        self._refresh_lens_capture_actions()

    def _populate_lens_quality(self, lens: dict[str, Any]) -> None:
        model = lens.get("model") or {}
        last_quality = lens.get("last_solve_quality")
        quality = last_quality or model.get("quality") or {}
        model_views = model.get("views") or []
        views = quality.get("views") if isinstance(quality, dict) else None
        if not isinstance(views, list):
            failed_attempt = (
                isinstance(last_quality, dict)
                and str(last_quality.get("gate") or "").lower() == "reject"
            )
            views = [] if failed_attempt else model_views if isinstance(model_views, list) else []
        if not quality:
            self.lens_gate_status.setText("No lens solve diagnostics are available yet.")
            self.lens_view_errors.setRowCount(0)
            return

        gate = str(quality.get("gate") or "unknown").upper()
        reason_lines: list[str] = []
        for label, key in (
            ("REJECT", "reject_reasons"),
            ("WARNING", "warning_reasons"),
        ):
            for item in quality.get(key) or []:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or "unspecified")
                message = str(item.get("message") or "Unspecified quality finding")
                reason_lines.append(f"{label} [{code}]: {message}")
        metrics = quality.get("metrics") or {}
        metric_parts: list[str] = []
        if "corner_hull_ratio" in metrics:
            metric_parts.append(f"coverage {float(metrics['corner_hull_ratio']) * 100.0:.1f}%")
        if "pose_span_major_deg" in metrics and "pose_span_minor_deg" in metrics:
            metric_parts.append(
                f"pose span {float(metrics['pose_span_major_deg']):.1f} x "
                f"{float(metrics['pose_span_minor_deg']):.1f} deg"
            )
        if "overall_rms_px" in metrics:
            metric_parts.append(f"RMS {float(metrics['overall_rms_px']):.4f} px")
        counts = ""
        if "accepted_count" in quality:
            counts = (
                f" | {int(quality.get('accepted_count', 0))}/{int(quality.get('input_count', 0))} "
                "views used"
            )
        summary = f"Gate: {gate}{counts}"
        if metric_parts:
            summary += " | " + " | ".join(metric_parts)
        if reason_lines:
            summary += "\n" + "\n".join(reason_lines)
        self.lens_gate_status.setText(summary)

        ranked_views = sorted(
            (item for item in views if isinstance(item, dict)),
            key=lambda item: float(item.get("reprojection_rms_px", 0.0)),
            reverse=True,
        )[:5]
        self.lens_view_errors.setRowCount(len(ranked_views))
        for row, view in enumerate(ranked_views):
            values = (
                str(view.get("name", "")),
                "Yes" if view.get("accepted") else "No",
                f"{float(view.get('reprojection_rms_px', 0.0)):.4f}",
                f"{float(view.get('reprojection_p95_px', 0.0)):.4f}",
                f"{float(view.get('reprojection_max_px', 0.0)):.4f}",
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if not view.get("accepted"):
                    item.setForeground(QtGui.QColor("#e4a64a"))
                    item.setToolTip(
                        str(view.get("exclusion_reason") or "Excluded from the final fit")
                    )
                self.lens_view_errors.setItem(row, column, item)

    def _refresh_lens_capture_actions(self) -> None:
        if not hasattr(self, "lens_captures"):
            return
        self.lens_delete_capture_button.setEnabled(
            not self._lens_mutation_blocked
            and bool(self.lens_captures.selectionModel().selectedRows())
        )
        self.lens_clear_captures_button.setEnabled(
            not self._lens_mutation_blocked and self.lens_captures.rowCount() > 0
        )

    def _refresh_lens_status(
        self,
        lens: dict[str, Any],
        camera: dict[str, Any],
        readiness: dict[str, Any],
        bed: dict[str, Any],
    ) -> None:
        model = lens.get("model") or {}
        model_quality = model.get("quality") if isinstance(model, dict) else None
        model_gate = (
            str(model_quality.get("gate") or "").strip().lower()
            if isinstance(model_quality, dict)
            else ""
        )
        model_accepted = model_gate in {"pass", "warning"}
        minimum = int((lens.get("pattern") or {}).get("minimum_images", 0))
        usable = int(lens.get("usable_image_count", 0))
        index = lens.get("index") or {}
        pending = int(index.get("pending_count", 0))
        index_busy = self.lens_index_busy or bool(index.get("indexing"))
        lens_busy = bool(lens.get("busy")) or index_busy
        self._lens_mutation_blocked = lens_busy
        groups = list(lens.get("resolution_groups") or [])
        active_group = next((item for item in groups if item.get("selected")), {})
        active_capture_count = int(active_group.get("image_count", 0))
        active_pending_count = int(active_group.get("pending_image_count", 0))
        model_text = (
            f"Solved {int(model.get('image_width', 0))} x {int(model.get('image_height', 0))}: "
            f"{float(model.get('rms_error', 0.0)):.4f} px RMS, "
            f"{float(model.get('mean_reprojection_error', 0.0)):.4f} px mean"
            if lens.get("calibrated")
            else "No solved lens model"
        )
        self.lens_status.setText(
            f"Current-resolution evidence: {active_capture_count}/{minimum} captures, "
            f"{usable} preview-detected, {active_pending_count} indexing | {model_text}"
        )

        width = int(camera.get("width", 0))
        height = int(camera.get("height", 0))
        group_text = "; ".join(
            f"{'CURRENT ' if item.get('selected') else ''}{int(item.get('width', 0))} x "
            f"{int(item.get('height', 0))}: {int(item.get('usable_image_count', 0))}/"
            f"{int(item.get('image_count', 0))} preview-detected"
            + (
                f", {int(item.get('pending_image_count', 0))} indexing"
                if int(item.get("pending_image_count", 0))
                else ""
            )
            for item in groups
        ) or "no stored capture groups"
        readiness_state = str(readiness.get("state") or "UNKNOWN")
        readiness_reasons = "; ".join(str(item) for item in readiness.get("reasons") or [])
        readiness_text = readiness_state + (f": {readiness_reasons}" if readiness_reasons else "")
        self.lens_resolution_status.setText(
            f"Current camera: {width} x {height} | {group_text}\n"
            f"Calibration readiness: {readiness_text}"
        )

        validity = bed.get("validity") or {}
        bed_state = str(validity.get("state") or "UNKNOWN")
        bed_reasons = "; ".join(str(item) for item in validity.get("reasons") or [])
        dependency_text = f"Bed map dependency: {bed_state}"
        if bed_reasons:
            dependency_text += f" - {bed_reasons}"
        if bed_state != "VALID":
            dependency_text += ". Registration and validation actions are disabled until bed mapping is redone."
        self.lens_bed_status.setText(dependency_text)

        self._lens_index_status_text(index)
        ready = readiness_state == "READY"
        self.lens_capture_button.setEnabled(ready and not lens_busy)
        self.lens_solve_button.setEnabled(
            ready
            and not lens_busy
            and pending == 0
            and active_capture_count >= minimum
        )
        self.lens_clear_model_button.setEnabled(
            bool(lens.get("calibrated")) and not lens_busy
        )
        self.base_grid_capture_button.setEnabled(model_accepted and ready)
        readiness_tooltip = (
            "Checkerboard evidence indexing is in progress"
            if lens_busy
            else "" if ready else readiness_text
        )
        self.lens_capture_button.setToolTip(readiness_tooltip)
        self.lens_solve_button.setToolTip(
            readiness_tooltip
            if not ready or lens_busy
            else "Finish indexing the stored checkerboard evidence before solving"
            if pending
            else (
                ""
                if active_capture_count >= minimum
                else (
                    f"Need {minimum - active_capture_count} more current-resolution "
                    "captures; the full-resolution solve makes the final usability decision"
                )
            )
        )
        self.base_grid_capture_button.setToolTip(
            readiness_tooltip
            if not ready
            else (
                ""
                if model_accepted
                else (
                    "Solve a lens model that passes the pose-diversity and frame-coverage "
                    "quality gate before capturing a base map. Use genuinely tilted "
                    "checkerboard views across the center, edges, and corners."
                    if lens.get("calibrated")
                    else "Solve the current lens calibration before capturing a base map"
                )
            )
        )
        self._populate_lens_captures(lens)
        self._populate_lens_quality(lens)
        self._schedule_lens_index(lens)

    def _refresh_bed_dependency_actions(self, bed: dict[str, Any]) -> None:
        validity = bed.get("validity") or {}
        valid = bool(bed.get("calibrated")) and validity.get("state") == "VALID"
        self._bed_map_valid = valid
        reasons = "; ".join(str(item) for item in validity.get("reasons") or [])
        unavailable = f"Requires a VALID bed map{': ' + reasons if reasons else ''}"
        for action in self._bed_dependent_actions:
            action.setEnabled(valid)
            available_tooltip = str(action.property("availableToolTip") or "")
            if valid:
                action.setToolTip(available_tooltip)
            else:
                action.setToolTip(
                    f"{available_tooltip}\n\n{unavailable}"
                    if available_tooltip
                    else unavailable
                )
        self.rough_grid_detect_button.setEnabled(valid)
        self.rough_grid_detect_button.setToolTip("" if valid else unavailable)
        if not valid:
            for action in self._bed_dependent_result_actions:
                action.setEnabled(False)
                action.setToolTip(unavailable)
        else:
            for action in self._bed_dependent_result_actions:
                action.setToolTip("")
        self.registration_recapture_button.setEnabled(
            valid and self._photo_pose_confirmed
        )
        self.validation_recapture_button.setEnabled(valid and self._photo_pose_confirmed)
        self.registration_results.setEnabled(valid)
        self.validation_results.setEnabled(valid)
        for tab_index in (3, 4):
            self.tabs.setTabToolTip(tab_index, "" if valid else unavailable)

    def _invalidate_lens_review(self, status: str) -> None:
        self.lens_gate_status.setText(status)
        self.lens_view_errors.setRowCount(0)

    def _invalidate_registration_review(self, status: str) -> None:
        self._fine_registration_analysis = None
        self._fine_registration_measurements = []
        self._dense_analysis = None
        self._registration_table_updating = True
        self.registration_results.setRowCount(0)
        self._registration_table_updating = False
        self.apply_registration_button.setEnabled(False)
        self.apply_registration_map_button.setEnabled(False)
        self.apply_dense_button.setEnabled(False)
        self.registration_status.setText(status)
        self.dense_status.setText(status)

    def _invalidate_validation_review(self, status: str) -> None:
        self._dense_validation_analysis = None
        self.validation_results.setRowCount(0)
        self.apply_dense_validation_refinement_button.setEnabled(False)
        self.validation_status.setText(status)

    def _invalidate_for_lens_solve(self) -> None:
        self._invalidate_lens_review("Lens solve in progress.")
        self._invalidate_registration_review(
            "Prior registration review invalidated by the new lens solve."
        )
        self._invalidate_validation_review(
            "Prior validation review invalidated by the new lens solve."
        )

    def _invalidate_for_home_park(self) -> None:
        self._set_photo_pose_confirmed(False)
        self._invalidate_registration_review(
            "Prior registration review invalidated by Home / park."
        )
        self._invalidate_validation_review(
            "Prior validation review invalidated by Home / park."
        )

    def refresh_all(self) -> None:
        machine = self.context.machine.status()
        connected = bool(machine.get("connected"))
        reconnect_required = bool(
            machine.get("controller_reconnect_required", False)
        )
        protocol = str(machine.get("protocol") or "controller")
        port = str(machine.get("port") or "")
        if connected and reconnect_required:
            machine_status = (
                f"RECONNECT REQUIRED · {protocol} · {port} · controller command "
                "state is untrusted"
            )
        elif connected:
            machine_status = f"Machine connected · {protocol} · {port}"
        else:
            machine_status = f"Machine offline · {port or 'no active connection'}"
        self.machine_connection_status.setText(machine_status)
        self.machine_connection_button.setText("Disconnect machine" if connected else "Connect machine")
        camera = asdict(self.context.camera.status())
        observed_fps = float(camera.get("fps") or 0.0)
        negotiated_fps = float(camera.get("negotiated_fps") or 0.0)
        fps_text = (
            f" · {observed_fps:.1f} fps observed · {negotiated_fps:.1f} fps negotiated"
            if observed_fps > 0.0 or negotiated_fps > 0.0
            else ""
        )
        self.camera_status.setText(
            f"{'Online' if camera.get('connected') else 'Offline'} · {camera.get('width', 0)} × "
            f"{camera.get('height', 0)}{fps_text} · {camera.get('device', '')}\n"
            f"{camera.get('last_error') or ''}"
        )
        camera_size = (int(camera.get("width", 0)), int(camera.get("height", 0)))
        lens = (
            self.context.lens.status(image_size=camera_size)
            if camera_size[0] > 0 and camera_size[1] > 0
            else self.context.lens.status()
        )
        readiness = self.context.camera_calibration_readiness()
        bed = self.context.bed_status()
        self._refresh_lens_status(lens, camera, readiness, bed)
        calibration = bed.get("calibration") or {}
        validity = bed.get("validity") or {}
        fine = calibration.get("fine_registration") or {}
        fine_x = float(fine.get("translation_x_mm", 0.0))
        fine_y = float(fine.get("translation_y_mm", 0.0))
        self.bed_status.setText(
            f"{len(bed['points'])}/{bed['minimum_points']} point pairs · "
            + (
                f"Solved: {calibration.get('rms_error_mm', 0):.4f} mm RMS, "
                f"{calibration.get('max_error_mm', 0):.4f} mm max"
                + (
                    f" · fine translation X{fine_x:+.3f} Y{fine_y:+.3f} mm"
                    if abs(fine_x) > 1e-12 or abs(fine_y) > 1e-12
                    else ""
                )
                if bed["calibrated"]
                else (
                    f"{validity.get('state')}: "
                    + "; ".join(validity.get("reasons") or [])
                    if bed.get("model_present")
                    else "Not solved"
                )
            )
        )
        self._refresh_axis_mapping(bed)
        self._refresh_bed_dependency_actions(bed)
        self._refresh_work_area_reference_status()
        self.points.setRowCount(len(bed["points"]))
        for row, point in enumerate(bed["points"]):
            values = (point["label"], point["image_x"], point["image_y"], point["machine_x"], point["machine_y"])
            for column, value in enumerate(values):
                self.points.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    def toggle_machine_connection(self) -> None:
        connected = bool(self.context.machine.status().get("connected"))
        result = self._message(
            "Machine connection",
            self.context.machine.disconnect if connected else self.context.machine.connect,
        )
        if result is not None or connected:
            self.refresh_all()

    def refresh_camera(self) -> None:
        image = self._message("Camera preview", lambda: self.context.camera_frame(undistort=False))
        if image is not None:
            self.camera_preview.set_image(image)

    def refresh_lens_preview(self) -> None:
        image = self._message("Lens preview", lambda: self.context.camera_frame(undistort=False))
        if image is not None:
            self.lens_preview.set_image(image)

    def apply_controls(self) -> None:
        result = self._message("Camera controls", self.context.camera.apply_configured_controls)
        if result is not None:
            QtWidgets.QMessageBox.information(
                self,
                "Camera controls",
                f"Applied: {result.applied}\n\nVerified: {result.verified}\n\nSkipped: {result.skipped}",
            )

    def save_still(self) -> None:
        self._start_operation(
            "Save corrected still",
            lambda: self.context.save_capture("desktop-camera", undistort=True),
            lambda path: QtWidgets.QMessageBox.information(self, "Saved", str(path)),
        )

    def set_synthetic_scene(self) -> None:
        result = self._message(
            "Simulation scene",
            lambda: self.context.synthetic_scene(str(self.synthetic_scene.currentData())),
        )
        if result is None:
            self.refresh_camera()

    def capture_lens(self) -> None:
        def operation() -> tuple[dict[str, Any], np.ndarray]:
            result = self.context.capture_lens_calibration()
            return result, self.context.camera_frame(undistort=False)

        self._start_operation(
            "Checkerboard capture",
            operation,
            self._lens_capture_succeeded,
            invalidate=lambda: self._invalidate_lens_review(
                "Checkerboard capture in progress. Solve again after reviewing the new evidence."
            ),
            on_failure=lambda message: self._invalidate_lens_review(
                f"Checkerboard capture failed: {message}"
            ),
        )

    def _lens_capture_succeeded(
        self,
        captured: tuple[dict[str, Any], np.ndarray],
    ) -> None:
        result, preview = captured
        self.refresh_all()
        self.lens_preview.set_image(preview)
        self.lens_gate_status.setText(
            "Evidence changed. Solve the current-resolution calibration again before "
            "using prior quality conclusions."
        )
        self.lens_view_errors.setRowCount(0)
        QtWidgets.QMessageBox.information(
            self,
            "Checkerboard capture",
            "Checkerboard found."
            if result["found"]
            else "Image saved, but the checkerboard was not detected.",
        )

    def solve_lens(self) -> None:
        self._start_operation(
            "Lens calibration",
            self.context.solve_lens_calibration,
            self._lens_solve_succeeded,
            invalidate=self._invalidate_for_lens_solve,
            on_failure=lambda message: self._invalidate_lens_review(
                f"Gate: REJECT\nLens calibration failed: {message}"
            ),
        )

    def _lens_solve_succeeded(self, _model: Any) -> None:
        self.refresh_all()
        self.calibrationChanged.emit()

    def clear_lens(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self, "Clear lens model", "Clear the solved lens model? Captured images will be retained."
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        def operation() -> bool:
            self.context.clear_lens_calibration(delete_images=False)
            return True

        if self._message("Clear lens model", operation):
            self.refresh_all()
            self.calibrationChanged.emit()

    def delete_lens_capture(self) -> None:
        rows = self.lens_captures.selectionModel().selectedRows()
        if not rows:
            return
        item = self.lens_captures.item(rows[0].row(), 0)
        name = str(item.data(QtCore.Qt.ItemDataRole.UserRole) or item.text())
        answer = QtWidgets.QMessageBox.question(
            self,
            "Delete checkerboard capture",
            f"Permanently delete {name}? The solved lens model, if any, will be retained.",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        deleted = self._message(
            "Delete checkerboard capture",
            lambda: self.context.lens.delete_capture(name),
        )
        if deleted:
            self.refresh_all()

    def clear_lens_captures(self) -> None:
        count = self.lens_captures.rowCount()
        if count <= 0:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Clear checkerboard captures",
            f"Permanently delete all {count} checkerboard captures? "
            "The solved lens model, if any, will be retained.",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        def operation() -> int:
            return self.context.lens.clear_captures()

        if self._message("Clear checkerboard captures", operation) is not None:
            self.refresh_all()

    def _bed_point_picked(self, x: float, y: float) -> None:
        if self._honeycomb_pick_active:
            self._honeycomb_support_point_picked(x, y)
            return
        self.image_x.setValue(x)
        self.image_y.setValue(y)

    def park(self) -> None:
        def succeeded(result: dict[str, Any]) -> None:
            self._set_photo_pose_confirmed(True)
            position = result["position"]
            QtWidgets.QMessageBox.information(
                self,
                "Camera pose",
                f"Machine idle at X{position['x']} Y{position['y']}.",
            )

        self._start_operation(
            "Home / park",
            self.context.machine.prepare_photo_position,
            succeeded,
            requires_controller=True,
            invalidate=self._invalidate_for_home_park,
        )

    def capture_bed(self) -> None:
        def operation() -> np.ndarray:
            self.context.capture_bed_reference()
            return self.context.bed_reference()

        def succeeded(image: np.ndarray) -> None:
            self._cancel_honeycomb_support_picking()
            self._work_area_reference_calibration = None
            self._bed_image = image
            self.bed_preview.set_image(self._bed_image)

        self._start_operation("Precision bed capture", operation, succeeded)

    def capture_work_area_reference(self) -> None:
        def operation() -> tuple[np.ndarray, Any]:
            calibration = self.context.bed.calibration
            image = self.context.capture_parked_work_area_reference()
            if self.context.bed.calibration is not calibration:
                raise ValueError(
                    "Bed calibration changed while the ruler overlay was prepared"
                )
            return image, calibration

        def succeeded(result: tuple[np.ndarray, Any]) -> None:
            image, calibration = result
            self._cancel_honeycomb_support_picking()
            self._bed_image = image
            self._work_area_reference_calibration = calibration
            self._render_work_area_reference_preview()
            self._refresh_work_area_reference_status()

        self._start_operation(
            "Work-area ruler reference",
            operation,
            succeeded,
            requires_controller=True,
            invalidate=self._invalidate_for_home_park,
        )

    def _render_work_area_reference_preview(self) -> None:
        if self._bed_image is None or self._work_area_reference_calibration is None:
            return
        if self._honeycomb_pick_active:
            preview = self._bed_image.copy()
            marker_radius = max(7, round(min(preview.shape[:2]) / 90.0))
            for index, (image_x, image_y) in enumerate(
                self._honeycomb_pick_points, start=1
            ):
                center = (round(image_x), round(image_y))
                cv2.circle(preview, center, marker_radius + 3, (15, 15, 15), -1)
                cv2.circle(preview, center, marker_radius, (0, 225, 255), 3)
                cv2.putText(
                    preview,
                    str(index),
                    (center[0] + marker_radius + 5, center[1] - marker_radius - 3),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 225, 255),
                    2,
                    cv2.LINE_AA,
                )
            self.bed_preview.set_image(preview, preserve_view=True)
            return
        preview = _work_area_reference_overlay(
            self._bed_image,
            self.context.bed,
            self.context.settings.machine.work_area,
            self.context.settings.laser.boundary_margin_mm,
            self.context.settings.laser.spot_offset_x_mm,
            self.context.settings.laser.spot_offset_y_mm,
            self.context.settings.laser.guarded_output_polygon_mm,
            support_reference=(
                self._honeycomb_candidate_reference
                or self.context.honeycomb_support.reference
            ),
            picked_image_points=tuple(self._honeycomb_pick_points),
        )
        self.bed_preview.set_image(preview)

    def _cancel_honeycomb_support_picking(self) -> None:
        self._honeycomb_pick_active = False
        self._honeycomb_pick_points.clear()
        self._honeycomb_candidate_reference = None
        if hasattr(self, "honeycomb_support_record_button"):
            self.honeycomb_support_record_button.setText(
                "Fallback: detect with 3 hints"
            )

    def detect_honeycomb_support_automatically(self) -> None:
        calibration = self.context.bed.calibration
        if (
            self._bed_image is None
            or calibration is None
            or self._work_area_reference_calibration is not calibration
        ):
            QtWidgets.QMessageBox.information(
                self,
                "Honeycomb support",
                "Capture a fresh work-area ruler overlay before automatic detection.",
            )
            return
        image = self._bed_image.copy()
        span = self.honeycomb_ruler_mark.value()
        self._start_operation(
            "Automatic honeycomb detection",
            lambda: self.context.detect_honeycomb_support_reference_automatically(
                image,
                ruler_mark_mm=span,
            ),
            lambda result: self._honeycomb_detection_succeeded(
                result,
                automatic=True,
                teaching_image=image,
            ),
            on_failure=lambda _message: self._refresh_work_area_reference_status(),
        )

    def toggle_honeycomb_support_picking(self) -> None:
        if self._honeycomb_pick_active:
            self._cancel_honeycomb_support_picking()
            self._render_work_area_reference_preview()
            self._refresh_work_area_reference_status()
            return
        calibration = self.context.bed.calibration
        if (
            self._bed_image is None
            or calibration is None
            or self._work_area_reference_calibration is not calibration
        ):
            QtWidgets.QMessageBox.information(
                self,
                "Honeycomb support",
                "Capture a fresh work-area ruler overlay before recording the support.",
            )
            return
        self._honeycomb_pick_active = True
        self._honeycomb_pick_points.clear()
        self.honeycomb_support_record_button.setText("Cancel support picking")
        self._render_work_area_reference_preview()
        self._refresh_work_area_reference_status()

    def _honeycomb_support_point_picked(self, x: float, y: float) -> None:
        calibration = self.context.bed.calibration
        if calibration is None or self._work_area_reference_calibration is not calibration:
            self._cancel_honeycomb_support_picking()
            self._refresh_work_area_reference_status()
            QtWidgets.QMessageBox.warning(
                self,
                "Honeycomb support",
                "The bed calibration changed. Capture a new ruler overlay and try again.",
            )
            return
        self._honeycomb_pick_points.append((float(x), float(y)))
        self._render_work_area_reference_preview()
        self._refresh_work_area_reference_status()
        if len(self._honeycomb_pick_points) < 3:
            return
        points = tuple(self._honeycomb_pick_points)
        image = self._bed_image.copy()
        mark = self.honeycomb_ruler_mark.value()
        self._cancel_honeycomb_support_picking()
        self._render_work_area_reference_preview()
        self._refresh_work_area_reference_status()

        def operation() -> tuple[HoneycombSupportReference, Any]:
            return self.context.detect_honeycomb_support_reference(
                image,
                points,
                ruler_mark_mm=mark,
            )

        def failed(message: str) -> None:
            self._cancel_honeycomb_support_picking()
            self._render_work_area_reference_preview()
            self._refresh_work_area_reference_status()

        self._start_operation(
            "Honeycomb ruler detection",
            operation,
            lambda result: self._honeycomb_detection_succeeded(
                result,
                automatic=False,
            ),
            on_failure=failed,
        )

    def _honeycomb_detection_succeeded(
        self,
        result: tuple[HoneycombSupportReference, Any],
        *,
        automatic: bool,
        teaching_image: np.ndarray | None = None,
    ) -> None:
        candidate, detection = result
        self._honeycomb_candidate_reference = candidate
        self._render_work_area_reference_preview()
        x_span, y_span = candidate.measured_ruler_span_mm
        corners = candidate.support_corners_machine_mm
        corner_text = ", ".join(
            f"X{machine_x:.1f}/Y{machine_y:.1f}"
            for machine_x, machine_y in corners
        )
        method = (
            "Vision automatically segmented the honeycomb rectangle, fitted all four "
            "frame edges, and used the active bed map to identify lower-left, +X, +Y, "
            "and the opposite corner."
            if automatic
            else "The fallback hints selected the X/Y ruler corridors and approximate shared zero."
        )
        evidence = (
            f"Detected frame angle: {detection.axis_angle_deg:.1f} deg. The entered "
            f"{candidate.ruler_mark_mm:g} × {candidate.ruler_mark_mm:g} mm physical "
            "span defines the ideal square.\n"
            if automatic
            else (
                f"X/Y ticks found: {detection.axis_x.tick_candidate_count}/"
                f"{detection.axis_y.tick_candidate_count}; X/Y periodicity "
                f"{detection.axis_x.periodicity_score:.2f}/"
                f"{detection.axis_y.periodicity_score:.2f}; axis angle "
                f"{detection.axis_angle_deg:.1f} deg.\n"
            )
        )
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Review detected honeycomb reference",
            method
            + " The magenta outline comes from the detected four-corner rectangle.\n\n"
            + evidence
            + f"The {candidate.ruler_mark_mm:g} mm spans map to "
            f"{x_span:.1f} mm and {y_span:.1f} mm.\n"
            f"Detected outline: {corner_text}.\n\n"
            "This establishes the movable honeycomb-local X0/Y0 job frame for "
            "the camera, grid, Trace, templates, and newly generated paths. It "
            "does not alter camera calibration, configured machine bounds, "
            "guarded laser limits, or authorize output. Save it?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            try:
                save_options: dict[str, Any] = {}
                if automatic:
                    save_options = {
                        "teaching_image": teaching_image,
                        "teaching_corners_px": detection.frame_corners_image_px,
                    }
                self.context.save_honeycomb_support_reference(
                    candidate,
                    **save_options,
                )
                self.calibrationChanged.emit()
            except Exception as exc:
                QtWidgets.QMessageBox.critical(
                    self, "Honeycomb support was not saved", str(exc)
                )
        self._cancel_honeycomb_support_picking()
        self._render_work_area_reference_preview()
        self._refresh_work_area_reference_status()

    def clear_honeycomb_support_reference(self) -> None:
        if self.context.honeycomb_support.reference is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Clear honeycomb support",
            "Remove the recorded movable honeycomb-support reference? Machine and "
            "laser limits are not changed.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.context.clear_honeycomb_support_reference()
        self.calibrationChanged.emit()
        self._cancel_honeycomb_support_picking()
        self._render_work_area_reference_preview()
        self._refresh_work_area_reference_status()

    def add_bed_point(self) -> None:
        result = self._message(
            "Add point",
            lambda: self.context.bed.add_point(
                BedPoint(
                    self.image_x.value(),
                    self.image_y.value(),
                    self.machine_x.value(),
                    self.machine_y.value(),
                    self.point_label.text()[:80],
                )
            ),
        )
        if result is not None:
            self.refresh_all()
            self.move_bed_target(1)

    def delete_bed_point(self) -> None:
        row = self.points.currentRow()
        if row < 0:
            return
        try:
            self.context.bed.delete_point(row)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Delete point", str(exc))
        else:
            self.refresh_all()

    def import_coordinate_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import bed coordinates", "", "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            normalized = {name.lower(): name for name in (rows[0] if rows else {})}
            x_key = next((normalized[name] for name in ("x_mm", "x_in", "machine_x", "x") if name in normalized), None)
            y_key = next((normalized[name] for name in ("y_mm", "y_in", "machine_y", "y") if name in normalized), None)
            label_key = next(
                (normalized[name] for name in ("fiducial", "index", "id", "label") if name in normalized), None
            )
            if x_key is None or y_key is None:
                raise ValueError("CSV headers must include x_mm/y_mm or x_in/y_in")
            x_unit = "in" if x_key.lower() == "x_in" else "mm"
            y_unit = "in" if y_key.lower() == "y_in" else "mm"
            targets = []
            for index, row in enumerate(rows):
                identifier = row.get(label_key, "") if label_key else str(index + 1)
                targets.append(
                    {
                        "machine_x": parse_to_mm(row[x_key], x_unit),
                        "machine_y": parse_to_mm(row[y_key], y_unit),
                        "label": f"Fiducial {identifier or index + 1}",
                    }
                )
            if len(targets) < self.context.settings.calibration.bed.minimum_points:
                raise ValueError("The CSV does not contain enough coordinates")
            self._bed_targets = targets
            self._bed_target_index = min(len(self.context.bed.points), len(targets) - 1)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import coordinates", str(exc))
            return
        self.show_bed_target()

    def move_bed_target(self, offset: int) -> None:
        if not self._bed_targets:
            return
        self._bed_target_index = max(0, min(self._bed_target_index + offset, len(self._bed_targets) - 1))
        self.show_bed_target()

    def show_bed_target(self) -> None:
        if not self._bed_targets:
            self.target_status.setText("No coordinate CSV loaded")
            return
        target = self._bed_targets[self._bed_target_index]
        self.machine_x.setValue(target["machine_x"])
        self.machine_y.setValue(target["machine_y"])
        self.point_label.setText(target["label"])
        self.target_status.setText(
            f"{self._bed_target_index + 1} of {len(self._bed_targets)}: {target['label']} · "
            f"X{target['machine_x']} Y{target['machine_y']}"
        )

    def detect_cross_grid(self) -> None:
        result = self._message("Cross-grid detection", self.context.detect_bed_cross_grid)
        if result is None:
            return
        if not result.get("detected"):
            QtWidgets.QMessageBox.warning(self, "Cross-grid detection", result.get("reason", "Grid not detected"))
            return
        points = result.get("points", [])
        if (
            QtWidgets.QMessageBox.question(
                self, "Accept detected grid", f"Replace current points with {len(points)} detected points?"
            )
            == QtWidgets.QMessageBox.StandardButton.Yes
        ):
            self.context.replace_bed_points({"points": points})
            self.refresh_all()

    def solve_bed(self) -> None:
        result = self._message("Bed mapping", self.context.solve_bed)
        if result is not None:
            self.refresh_all()
            self.calibrationChanged.emit()

    def _refresh_axis_mapping(self, bed: dict[str, Any]) -> None:
        mapping = bed.get("axis_mapping") or {}
        messages: list[str] = []
        for axis, toggle in (("x", self.reverse_x), ("y", self.reverse_y)):
            state = mapping.get(axis) or {}
            reversed_axis = bool(state.get("reversed", False))
            recorded = bool(state.get("recorded", False))
            blocker = QtCore.QSignalBlocker(toggle)
            toggle.setChecked(reversed_axis)
            del blocker
            toggle.setText(f"Reverse {axis.upper()} mapping — {'ON' if reversed_axis else 'OFF'}")
            if mapping and not recorded:
                messages.append(f"{axis.upper()} orientation is not operator-confirmed")
            toggle.setEnabled(bool(bed.get("calibrated")))
        saved_orientation = bool(mapping) and all(
            bool((mapping.get(axis) or {}).get("recorded", False))
            for axis in ("x", "y")
        )
        self.axis_mapping_status.setText(
            (
                "; ".join(messages)
                + ". After a laser-off direction check, use the confirmation button to record these states without changing the map."
            )
            if messages
            else (
                "Axis states are saved in the bed calibration and restored when Setup reopens."
                + (
                    " Re-solve the bed mapping before changing them."
                    if not bed.get("calibrated")
                    else ""
                )
                if saved_orientation
                else "Solve a bed mapping before selecting axis orientation."
            )
        )
        self.save_axis_mapping.setVisible(bool(messages))
        self.save_axis_mapping.setEnabled(bool(messages) and bool(bed.get("calibrated")))

    def set_bed_axis_reversed(self, axis: str, enabled: bool) -> None:
        axis = axis.upper()
        mode = "REVERSED" if enabled else "NORMAL"
        answer = QtWidgets.QMessageBox.warning(
            self,
            f"Set {axis} mapping {mode}",
            f"This sets the saved {axis} mapping to {mode}. If that differs from the "
            f"current effective state, every saved machine-{axis} point is mirrored and the bed "
            "mapping is re-solved. Use it only when a laser-off direction check or repeated "
            "homed measurements prove that camera and controller directions are "
            f"opposite on {axis}. The state persists across restarts.\n\nContinue?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            self.refresh_all()
            return
        result = self._message(
            f"Set {axis} mapping {mode}",
            lambda: self.context.bed.set_machine_axis_reversed(axis, enabled),
        )
        if result is not None:
            self.refresh_all()
            self.calibrationChanged.emit()

    def reverse_bed_axis(self, axis: str) -> None:
        """Retain the prior toggle entry point for compatibility."""
        state = self.context.bed.axis_mapping_state()[axis.lower()]
        self.set_bed_axis_reversed(axis, not state["reversed"])

    def confirm_axis_mapping_state(self) -> None:
        state = self.context.bed.axis_mapping_state()
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Confirm unrecorded axis orientation",
            "This records the displayed unconfirmed orientation without mirroring any "
            "points. Confirm only after a laser-off direction check.\n\n"
            f"X: {'REVERSED' if state['x']['reversed'] else 'NORMAL'}\n"
            f"Y: {'REVERSED' if state['y']['reversed'] else 'NORMAL'}\n\nContinue?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Save axis orientation",
            lambda: (
                self.context.bed.set_machine_axis_reversed("x", state["x"]["reversed"]),
                self.context.bed.set_machine_axis_reversed("y", state["y"]["reversed"]),
            ),
        )
        if result is not None:
            self.refresh_all()
            self.calibrationChanged.emit()

    def _restore_preferences(self) -> None:
        geometry = self._settings.value("machineSetup/geometry-v1")
        if geometry:
            self.restoreGeometry(geometry)
        self.tabs.setCurrentIndex(max(0, min(self.tabs.count() - 1, int(self._settings.value("machineSetup/tab", 0)))))
        scene = str(self._settings.value("machineSetup/syntheticScene", "bed"))
        scene_index = self.synthetic_scene.findData(scene)
        if scene_index >= 0:
            self.synthetic_scene.setCurrentIndex(scene_index)
        for key, widget in (
            ("baseGridMarkSize", self.base_grid_mark_size),
            ("baseGridSpeed", self.base_grid_speed),
            ("registrationMarkSize", self.registration_mark_size),
            ("registrationSpeed", self.registration_speed),
            ("validationMarkSize", self.validation_mark_size),
            ("validationSpeed", self.validation_speed),
        ):
            value = self._settings.value(f"machineSetup/{key}")
            if value is not None:
                widget.setValue(float(value))

    def _save_preferences(self) -> None:
        self._settings.setValue("machineSetup/geometry-v1", self.saveGeometry())
        self._settings.setValue("machineSetup/tab", self.tabs.currentIndex())
        self._settings.setValue("machineSetup/syntheticScene", self.synthetic_scene.currentData())
        for key, widget in (
            ("baseGridMarkSize", self.base_grid_mark_size),
            ("baseGridSpeed", self.base_grid_speed),
            ("registrationMarkSize", self.registration_mark_size),
            ("registrationSpeed", self.registration_speed),
            ("validationMarkSize", self.validation_mark_size),
            ("validationSpeed", self.validation_speed),
        ):
            self._settings.setValue(f"machineSetup/{key}", widget.value())
        self._settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._close_blocked():
            event.ignore()
            return
        self._save_preferences()
        super().closeEvent(event)

    def accept(self) -> None:
        if self._close_blocked():
            return
        self._save_preferences()
        super().accept()

    def reject(self) -> None:
        if self._close_blocked():
            return
        self._save_preferences()
        super().reject()

    def clear_bed(self) -> None:
        if (
            QtWidgets.QMessageBox.question(self, "Clear bed mapping", "Clear all bed points and the solved mapping?")
            == QtWidgets.QMessageBox.StandardButton.Yes
        ):
            self.context.bed.clear()
            self.refresh_all()
            self.calibrationChanged.emit()

    def _refresh_base_grid_geometry_status(self) -> None:
        if not hasattr(self, "base_grid_mark_size"):
            return
        area = self.context.settings.machine.work_area
        laser = self.context.settings.laser
        try:
            targets = base_bed_grid_targets(
                area,
                mark_size_mm=self.base_grid_mark_size.value(),
                boundary_margin_mm=laser.boundary_margin_mm,
            )
            keyed_sizes = base_bed_grid_mark_sizes(self.base_grid_mark_size.value())
        except Exception as exc:
            self.base_grid_status.setText(str(exc))
            return
        xs = sorted({target.machine_x for target in targets})
        ys = sorted({target.machine_y for target in targets})
        extents = [
            (
                target.machine_x - keyed_sizes.get(target.id, self.base_grid_mark_size.value()) * 0.5,
                target.machine_y - keyed_sizes.get(target.id, self.base_grid_mark_size.value()) * 0.5,
                target.machine_x + keyed_sizes.get(target.id, self.base_grid_mark_size.value()) * 0.5,
                target.machine_y + keyed_sizes.get(target.id, self.base_grid_mark_size.value()) * 0.5,
            )
            for target in targets
        ]
        bounds = (
            min(item[0] for item in extents),
            min(item[1] for item in extents),
            max(item[2] for item in extents),
            max(item[3] for item in extents),
        )
        centers = (
            f"X/Y {', '.join(f'{value:g}' for value in xs)}"
            if xs == ys
            else (
                f"X {', '.join(f'{value:g}' for value in xs)}; "
                f"Y {', '.join(f'{value:g}' for value in ys)}"
            )
        )
        self.base_grid_status.setText(
            f"Centers {centers} mm | marked X{bounds[0]:g}..{bounds[2]:g}, "
            f"Y{bounds[1]:g}..{bounds[3]:g} | margin {laser.boundary_margin_mm:g} mm"
        )

    def _refresh_work_area_reference_status(self) -> None:
        if not hasattr(self, "work_area_reference_status"):
            return
        area = self.context.settings.machine.work_area
        laser = self.context.settings.laser
        margin = float(laser.boundary_margin_mm)
        output = effective_laser_output_area(
            area,
            margin,
            laser.spot_offset_x_mm,
            laser.spot_offset_y_mm,
        )
        offset_note = ""
        if (
            abs(float(laser.spot_offset_x_mm)) > 1e-12
            or abs(float(laser.spot_offset_y_mm)) > 1e-12
        ):
            offset_note = (
                f" and spot offset X{laser.spot_offset_x_mm:g}/"
                f"Y{laser.spot_offset_y_mm:g}"
            )
        self.work_area_reference_status.setText(
            f"Camera/calibration map: X{area.x_min:g}..{area.x_max:g}, "
            f"Y{area.y_min:g}..{area.y_max:g} mm\nGuarded laser output: "
            "explicit 210 × 210 mm four-corner machine polygon"
            if laser.guarded_output_polygon_mm is not None
            else (
                f"Camera/work: X{area.x_min:g}..{area.x_max:g}, "
                f"Y{area.y_min:g}..{area.y_max:g} mm\nGuarded laser output after "
                f"{margin:g} mm margin{offset_note}: X{output.x_min:g}.."
                f"{output.x_max:g}, Y{output.y_min:g}..{output.y_max:g} mm"
            )
        )
        reference = self.context.honeycomb_support.reference
        if reference is not None and not self._honeycomb_dimensions_initialized:
            self.honeycomb_ruler_mark.setValue(reference.ruler_mark_mm)
        self._honeycomb_dimensions_initialized = True

        if self._honeycomb_pick_active:
            instructions = (
                "Hint 1: anywhere along the X ruler, away from its zero.",
                "Hint 2: near the shared zero/intersection of both rulers.",
                "Hint 3: anywhere along the Y ruler, away from its zero.",
            )
            index = min(len(self._honeycomb_pick_points), len(instructions) - 1)
            support_text = instructions[index]
        elif reference is None:
            support_text = (
                self.context.honeycomb_support.load_error
                or "Detected honeycomb reference: not recorded. Capture the ruler "
                "overlay, then run automatic detection. Use the three-hint control "
                "only if automatic detection fails or is ambiguous."
            )
        else:
            corners = reference.support_corners_machine_mm
            xs = [point[0] for point in corners]
            ys = [point[1] for point in corners]
            x_span, y_span = reference.measured_ruler_span_mm
            stale_note = ""
            calibration = self.context.bed.calibration
            if (
                calibration is not None
                and abs(
                    calibration.created_at - reference.bed_calibration_created_at
                )
                > 1e-9
            ):
                stale_note = " Bed map changed since recording; review and re-record."
            support_text = (
                f"Honeycomb-local job frame: X0..{reference.support_width_mm:g}, "
                f"Y0..{reference.support_height_mm:g} mm; "
                "ruler 0/0 maps to "
                f"X{reference.ruler_origin_machine_mm[0]:.1f}/"
                f"Y{reference.ruler_origin_machine_mm[1]:.1f}. Measured "
                f"{reference.ruler_mark_mm:g} mm spans: X{x_span:.1f}, Y{y_span:.1f}. "
                f"Mapped outline X{min(xs):.1f}..{max(xs):.1f}, "
                f"Y{min(ys):.1f}..{max(ys):.1f}.{stale_note}"
            )
        self.honeycomb_support_status.setText(support_text)

        calibration = self.context.bed.calibration
        fresh_reference_image = (
            self._bed_image is not None
            and calibration is not None
            and self._work_area_reference_calibration is calibration
        )
        can_record = self._bed_map_valid and fresh_reference_image
        self.honeycomb_support_record_button.setEnabled(
            self._honeycomb_pick_active or can_record
        )
        self.honeycomb_support_record_button.setToolTip(
            ""
            if self._honeycomb_pick_active or can_record
            else "Capture a fresh ruler overlay before recording the support"
        )
        self.honeycomb_support_clear_button.setEnabled(
            reference is not None and not self._honeycomb_pick_active
        )
        for field in (self.honeycomb_ruler_mark,):
            field.setEnabled(not self._honeycomb_pick_active)

    def prepare_base_bed_mapping_job(self, powered: bool) -> None:
        if powered:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Prepare powered base mapping",
                "This prepares 25 powered calibration crosses, including two larger "
                "orientation keys, on a clean restrained sacrificial sheet. Run and "
                "inspect the exact Preview first. Use only a previously verified "
                "visible-marking power inside the required enclosure. The normal Preview, "
                "confirmation, and temporary arming gates still apply.\n\nContinue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        job = self._message(
            "Base bed mapping",
            lambda: self.context.prepare_base_bed_mapping_job(
                powered=powered,
                power_percent=self.base_grid_power.value(),
                mark_size_mm=self.base_grid_mark_size.value(),
                speed_mm_min=self.base_grid_speed.value(),
            ),
        )
        if job is not None:
            self.registrationJobPrepared.emit(job)
            self.accept()

    def capture_base_bed_mapping(self) -> None:
        self._start_operation(
            "Base bed mapping",
            self.context.capture_base_bed_mapping,
            self._base_bed_mapping_captured,
            requires_controller=True,
            invalidate=self._invalidate_for_home_park,
            on_failure=lambda message: self.base_grid_status.setText(
                f"Base-map capture failed: {message}"
            ),
        )

    def _base_bed_mapping_captured(
        self,
        result: tuple[np.ndarray, dict[str, Any]],
    ) -> None:
        image, detection = result
        self._cancel_honeycomb_support_picking()
        self._work_area_reference_calibration = None
        preview = image.copy()
        for point in detection.get("points", []):
            identifier = int(point["id"])
            center = (int(round(point["image_x"])), int(round(point["image_y"])))
            color = (0, 165, 255) if identifier == 7 else (255, 220, 0) if identifier == 8 else (0, 220, 0)
            cv2.circle(preview, center, 14, color, 2, cv2.LINE_AA)
            cv2.putText(
                preview,
                str(identifier),
                (center[0] + 15, center[1] - 7),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                2,
                cv2.LINE_AA,
            )
        self._bed_image = image
        self.bed_preview.set_image(preview)
        if not detection.get("detected"):
            self.base_grid_status.setText(str(detection.get("reason", "Base grid was not detected")))
            QtWidgets.QMessageBox.warning(
                self,
                "Base-grid detection rejected",
                self.base_grid_status.text(),
            )
            return
        candidate = detection.get("candidate") or {}
        status = (
            f"25/25 keyed marks detected; {int(candidate.get('inlier_count', 0))}/25 inliers; "
            f"{float(candidate.get('rms_error_mm', 0.0)):.3f} mm RMS; "
            f"{float(candidate.get('candidate_max_error_mm', 0.0)):.3f} mm maximum."
        )
        self.base_grid_status.setText(status + " " + str(candidate.get("reason", "")))
        if not candidate.get("can_apply"):
            QtWidgets.QMessageBox.warning(self, "Base-map fit rejected", self.base_grid_status.text())
            return
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Apply fresh base map",
            self.base_grid_status.text()
            + "\n\nThe numbered overlay must sit on every cross. Applying replaces the old "
            "base points and clears its fine translation, full-map refinement, and "
            "dense correction. The keyed marks record the generated coordinate labels. "
            "A separate laser-off direction and bounds check remains required before "
            "normal production; it does not block Fine registration.\n\nApply this "
            "reviewed base map?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        applied = self._message(
            "Apply fresh base map",
            lambda: self.context.apply_base_bed_mapping(detection),
        )
        if applied is not None:
            self.refresh_all()
            self.calibrationChanged.emit()
            QtWidgets.QMessageBox.information(
                self,
                "Base map applied",
                "The fresh 25-point base map is installed. Continue with "
                "4 · Fine registration. Complete the separate laser-off direction and "
                "bounds check before normal production.",
            )

    def prepare_registration_job(self, powered: bool) -> None:
        if powered:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Prepare powered registration marks",
                "This prepares eight powered crosses. Use only a previously verified "
                "visible-marking power on a restrained sacrificial surface inside the "
                "required enclosure. After reviewing and closing Preview, the main "
                "window's Start button submits this prepared powered job immediately."
                "\n\nContinue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        job = self._message(
            "Fine registration",
            lambda: self.context.prepare_fine_registration_job(
                powered=powered,
                power_percent=self.registration_power.value(),
                mark_size_mm=self.registration_mark_size.value(),
                speed_mm_min=self.registration_speed.value(),
            ),
        )
        if job is not None:
            self.registrationJobPrepared.emit(job)
            self.accept()

    def _set_photo_pose_confirmed(self, confirmed: bool) -> None:
        self._photo_pose_confirmed = bool(confirmed)
        enabled = self._photo_pose_confirmed and self._bed_map_valid
        self.registration_recapture_button.setEnabled(enabled)
        self.validation_recapture_button.setEnabled(enabled)

    def capture_fine_registration(self, *, home_first: bool = True) -> None:
        if not home_first and not self._photo_pose_confirmed:
            QtWidgets.QMessageBox.critical(
                self,
                "Fine registration",
                "Home / park and complete one precision capture before using recapture "
                "without homing",
            )
            return

        def invalidate() -> None:
            if home_first:
                self._invalidate_for_home_park()
            else:
                self._invalidate_registration_review(
                    "Prior registration review invalidated by precision recapture."
                )

        self._start_operation(
            "Fine registration precision capture",
            lambda: self.context.capture_fine_registration(home_first=home_first),
            lambda result: self._fine_registration_captured(
                result,
                home_first=home_first,
            ),
            requires_controller=True,
            invalidate=invalidate,
            on_failure=lambda message: self.registration_status.setText(
                f"Fine registration capture failed: {message}"
            ),
        )

    def _fine_registration_captured(
        self,
        result: tuple[np.ndarray, dict[str, Any]],
        *,
        home_first: bool,
    ) -> None:
        if home_first:
            self._set_photo_pose_confirmed(True)
        image, payload = result
        preview = image.copy()
        for point in payload.get("points", []):
            center = (int(round(point["image_x"])), int(round(point["image_y"])))
            cv2.circle(preview, center, 14, (0, 220, 0), 2, cv2.LINE_AA)
            cv2.putText(
                preview,
                str(point["id"]),
                (center[0] + 16, center[1] - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 220, 0),
                2,
                cv2.LINE_AA,
            )
        self.registration_preview.set_image(preview)
        analysis = payload.get("analysis")
        if not isinstance(analysis, dict):
            self._fine_registration_analysis = None
            self._fine_registration_measurements = []
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            status = payload.get("reason", "Registration marks were not detected")
            precision = self._precision_capture_summary(payload.get("precision_capture"))
            self.registration_status.setText(status + (f"\n{precision}" if precision else ""))
            return
        self._fine_registration_measurements = list(payload.get("measurements", []))
        self._populate_registration_results(
            self._fine_registration_measurements,
            set(int(value) for value in analysis.get("excluded_ids", [])),
        )
        self._show_registration_analysis(analysis)

    def _show_registration_analysis(self, analysis: dict[str, Any]) -> None:
        self._fine_registration_analysis = analysis
        self.apply_registration_button.setEnabled(bool(analysis.get("can_apply_translation")))
        refinement = analysis.get("full_map_refinement")
        can_apply_map = isinstance(refinement, dict) and bool(refinement.get("can_apply_full_map"))
        self.apply_registration_map_button.setEnabled(can_apply_map)
        excluded = [int(value) for value in analysis.get("excluded_ids", [])]
        exclusion_text = " · excluded " + ", ".join(f"#{value}" for value in excluded) if excluded else ""
        status = (
            f"{analysis['classification'].replace('_', ' ').title()} · "
            f"proposed correction X{analysis['correction_x_mm']:+.3f} "
            f"Y{analysis['correction_y_mm']:+.3f} mm · "
            f"scatter {analysis['scatter_rms_mm']:.3f} mm RMS{exclusion_text}\n"
            f"{analysis['reason']}"
        )
        if isinstance(refinement, dict):
            ransac_outliers = [int(value) for value in refinement.get("ransac_outlier_ids", [])]
            outlier_text = (
                " · geometric outlier " + ", ".join(f"#{value}" for value in ransac_outliers) if ransac_outliers else ""
            )
            status += (
                f"\nFull-bed fit: {refinement['inlier_count']}/"
                f"{refinement['selected_count']} inliers · "
                f"{refinement['rms_error_mm']:.3f} mm RMS · "
                f"{refinement['coverage_ratio']:.0%} coverage · "
                f"{refinement['correction_max_mm']:.3f} mm maximum correction"
                f"{outlier_text}\n{refinement['reason']}"
            )
        precision = self._precision_capture_summary(analysis.get("precision_capture"))
        if precision:
            status += f"\n{precision}"
        self.registration_status.setText(status)

    @staticmethod
    def _precision_capture_summary(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        camera = payload.get("camera")
        aggregation = payload.get("aggregation")
        if not isinstance(camera, dict) or not isinstance(aggregation, dict):
            return ""
        samples = int(camera.get("sample_frames", 0))
        discarded = int(camera.get("discarded_frames", 0))
        jitter = float(aggregation.get("worst_jitter_rms_px", 0.0))
        rejected = int(aggregation.get("rejected_frame_count", 0))
        selected = aggregation.get("selected_frame_index")
        selected_note = f" | selected frame {int(selected) + 1}" if selected is not None else ""
        controls_skipped = camera.get("controls_skipped")
        control_note = (
            f" | {len(controls_skipped)} camera controls unavailable"
            if isinstance(controls_skipped, dict) and controls_skipped
            else ""
        )
        return (
            f"Precision capture: {samples} fresh frames after {discarded} discarded | "
            f"worst jitter {jitter:.3f} px | {rejected} outlier frames"
            f"{selected_note}{control_note}"
        )

    def _populate_registration_results(
        self,
        measurements: list[dict[str, Any]],
        excluded_ids: set[int],
    ) -> None:
        self._registration_table_updating = True
        self.registration_results.setRowCount(len(measurements))
        for row, item in enumerate(measurements):
            use = QtWidgets.QTableWidgetItem()
            use.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            use.setCheckState(
                QtCore.Qt.CheckState.Unchecked if int(item["id"]) in excluded_ids else QtCore.Qt.CheckState.Checked
            )
            use.setToolTip(
                "Uncheck only when the preview clearly shows an obstructed, damaged, "
                "or incorrectly detected cross. At most two may be excluded."
            )
            self.registration_results.setItem(row, 0, use)
            values = (
                item["id"],
                f"{item['machine_x']:.3f}",
                f"{item['machine_y']:.3f}",
                f"{item['observed_x']:.3f}",
                f"{item['observed_y']:.3f}",
                f"{item['error_x_mm']:+.3f}",
                f"{item['error_y_mm']:+.3f}",
            )
            for column, value in enumerate(values):
                self.registration_results.setItem(row, column + 1, QtWidgets.QTableWidgetItem(str(value)))
        self._registration_table_updating = False

    def registration_measurement_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._registration_table_updating or item.column() != 0 or not self._fine_registration_measurements:
            return
        excluded_ids = []
        for row, measurement in enumerate(self._fine_registration_measurements):
            use = self.registration_results.item(row, 0)
            if use is None or use.checkState() != QtCore.Qt.CheckState.Checked:
                excluded_ids.append(int(measurement["id"]))
        analysis = self._message(
            "Review fine registration",
            lambda: self.context.review_fine_registration_measurements(
                self._fine_registration_measurements,
                excluded_ids,
            ),
        )
        if analysis is None:
            previous = set(int(value) for value in (self._fine_registration_analysis or {}).get("excluded_ids", []))
            self._populate_registration_results(self._fine_registration_measurements, previous)
            return
        self._show_registration_analysis(analysis)

    def apply_fine_registration(self) -> None:
        analysis = self._fine_registration_analysis
        if not analysis or not analysis.get("can_apply_translation"):
            return
        correction_x = float(analysis["correction_x_mm"])
        correction_y = float(analysis["correction_y_mm"])
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Apply fine registration",
            f"Apply the reviewed camera-map translation X{correction_x:+.3f} "
            f"Y{correction_y:+.3f} mm?\n\nThis changes camera placement, not "
            "laser-head offset configuration. It can be reset from this tab.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Apply fine registration",
            lambda: self.context.apply_fine_registration(analysis),
        )
        if result is not None:
            self._fine_registration_analysis = None
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def apply_fine_registration_homography(self) -> None:
        analysis = self._fine_registration_analysis
        refinement = analysis.get("full_map_refinement") if isinstance(analysis, dict) else None
        if not isinstance(refinement, dict) or not refinement.get("can_apply_full_map"):
            return
        outliers = [int(value) for value in refinement.get("ransac_outlier_ids", [])]
        outlier_text = (
            "\nGeometric outlier excluded by the fit: " + ", ".join(f"#{value}" for value in outliers)
            if outliers
            else ""
        )
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Apply full-bed refinement",
            f"Replace the camera-to-bed map with this reviewed {refinement['inlier_count']}-"
            f"point fit?\n\nFit error: {refinement['rms_error_mm']:.3f} mm RMS\n"
            f"Maximum modeled bed correction: {refinement['correction_max_mm']:.3f} mm"
            f"{outlier_text}\n\nThis is calibration, not a safety function. The previous "
            "solved map will be retained for Reset full-bed refinement.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Apply full-bed refinement",
            lambda: self.context.apply_fine_registration_homography(analysis),
        )
        if result is not None:
            self._fine_registration_analysis = None
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def reset_fine_registration(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Reset fine registration",
            "Remove the applied fine-registration translation and restore the solved bed map?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        measurements = list(self._fine_registration_measurements)
        excluded_ids: list[int] = []
        for row, measurement in enumerate(measurements):
            use = self.registration_results.item(row, 0)
            if use is None or use.checkState() != QtCore.Qt.CheckState.Checked:
                excluded_ids.append(int(measurement["id"]))
        result = self._message("Reset fine registration", self.context.reset_fine_registration)
        if result is not None:
            persisted_measurements = result.get("review_measurements") if isinstance(result, dict) else None
            if isinstance(persisted_measurements, list) and persisted_measurements:
                measurements = persisted_measurements
                self._fine_registration_measurements = measurements
                analysis = result.get("review_analysis")
                reviewed_excluded = (
                    set(int(value) for value in analysis.get("excluded_ids", []))
                    if isinstance(analysis, dict)
                    else set()
                )
                self._populate_registration_results(measurements, reviewed_excluded)
            else:
                analysis = None
            if analysis is None and measurements:
                analysis = self._message(
                    "Review full-bed refinement",
                    lambda: self.context.review_fine_registration_measurements(
                        measurements,
                        excluded_ids,
                    ),
                )
            if isinstance(analysis, dict):
                self._show_registration_analysis(analysis)
            else:
                self._fine_registration_analysis = None
                self.apply_registration_button.setEnabled(False)
                self.apply_registration_map_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def reset_fine_registration_homography(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Reset full-bed refinement",
            "Restore the solved bed map saved immediately before the reviewed full-bed refinement?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Reset full-bed refinement",
            self.context.reset_fine_registration_homography,
        )
        if result is not None:
            self._fine_registration_analysis = None
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def prepare_dense_job(self, powered: bool) -> None:
        if powered:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Prepare powered dense calibration",
                "This prepares 25 powered crosses on a clean, restrained sacrificial "
                "surface. Use only a previously verified marking power. Continue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        job = self._message(
            "Dense local correction",
            lambda: self.context.prepare_dense_calibration_job(
                powered=powered,
                power_percent=self.registration_power.value(),
                mark_size_mm=self.registration_mark_size.value(),
                speed_mm_min=self.registration_speed.value(),
            ),
        )
        if job is not None:
            self.registrationJobPrepared.emit(job)
            self.accept()

    def capture_dense_calibration(self) -> None:
        self._start_operation(
            "Dense local-correction precision capture",
            lambda: self.context.capture_dense_calibration(
                validation=False,
                confirmation=False,
            ),
            self._dense_calibration_captured,
            requires_controller=True,
            invalidate=self._invalidate_for_home_park,
            on_failure=lambda message: self.dense_status.setText(
                f"Dense local-correction capture failed: {message}"
            ),
        )

    def _dense_calibration_captured(
        self,
        result: tuple[np.ndarray, dict[str, Any]],
    ) -> None:
        image, payload = result
        analysis = payload.get("analysis")
        inferred_ids = (
            set(int(value) for value in analysis.get("inferred_ids", [])) if isinstance(analysis, dict) else set()
        )
        rejected_ids = (
            set(int(value) for value in analysis.get("rejected_ids", [])) if isinstance(analysis, dict) else set()
        )
        preview = image.copy()
        for point in payload.get("points", []):
            center = (int(round(point["image_x"])), int(round(point["image_y"])))
            identifier = int(point["id"])
            color = (
                (0, 0, 255)
                if identifier in rejected_ids
                else (0, 165, 255)
                if identifier in inferred_ids
                else (0, 220, 0)
            )
            cv2.circle(preview, center, 11, color, 2, cv2.LINE_AA)
            cv2.putText(
                preview,
                str(point["id"]),
                (center[0] + 12, center[1] - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                color,
                1,
                cv2.LINE_AA,
            )
        self.registration_preview.set_image(preview)
        if not isinstance(analysis, dict):
            self._dense_analysis = None
            self.apply_dense_button.setEnabled(False)
            self.dense_status.setText(payload.get("reason", "Dense marks were not detected"))
            return
        self._populate_dense_results(list(payload.get("measurements", [])))
        self._dense_analysis = analysis
        self.apply_dense_button.setEnabled(bool(analysis.get("can_apply")))
        self.dense_status.setText(
            f"{analysis['classification'].replace('_', ' ').title()} · fit "
            f"{analysis['fit_rms_mm']:.3f} mm RMS · "
            f"{analysis['fit_max_mm']:.3f} mm maximum · proposed mesh correction "
            f"{analysis['correction_max_mm']:.3f} mm maximum\n{analysis['reason']}"
        )

    def _populate_dense_results(self, measurements: list[dict[str, Any]]) -> None:
        self._registration_table_updating = True
        self.registration_results.setRowCount(len(measurements))
        for row, item in enumerate(measurements):
            inferred = bool(item.get("inferred"))
            rejected = bool(item.get("rejected"))
            indicator = QtWidgets.QTableWidgetItem("REJECTED" if rejected else "INFERRED" if inferred else "OK")
            if inferred:
                indicator.setForeground(QtGui.QColor("#ffb347"))
                indicator.setToolTip("This camera detection was excluded; the mesh node is inferred from its neighbors")
            elif rejected:
                indicator.setForeground(QtGui.QColor("#ff5c5c"))
                indicator.setToolTip(
                    "This detection is unreliable and is not being inferred because the complete result is invalid"
                )
            self.registration_results.setItem(row, 0, indicator)
            values = (
                item["id"],
                f"{item['machine_x']:.3f}",
                f"{item['machine_y']:.3f}",
                f"{item['observed_x']:.3f}",
                f"{item['observed_y']:.3f}",
                f"{item['error_x_mm']:+.3f}",
                f"{item['error_y_mm']:+.3f}",
            )
            for column, value in enumerate(values, start=1):
                cell = QtWidgets.QTableWidgetItem(str(value))
                if inferred:
                    cell.setForeground(QtGui.QColor("#ffb347"))
                elif rejected:
                    cell.setForeground(QtGui.QColor("#ff5c5c"))
                self.registration_results.setItem(row, column, cell)
        self._registration_table_updating = False

    def apply_dense_calibration(self) -> None:
        analysis = self._dense_analysis
        if not analysis or not analysis.get("can_apply"):
            return
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Apply dense local correction",
            "Apply this reviewed nonlinear 5×5 correction mesh? The current homography "
            "remains underneath and Reset local mesh removes only this layer.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if (
            self._message("Apply dense local correction", lambda: self.context.apply_dense_calibration(analysis))
            is not None
        ):
            self._dense_analysis = None
            self.apply_dense_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def reset_dense_calibration(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Reset local correction mesh",
            "Remove only the dense local correction layer?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        if self._message("Reset local correction mesh", self.context.reset_dense_calibration) is not None:
            self._dense_analysis = None
            self.apply_dense_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def prepare_accuracy_validation_job(self, powered: bool) -> None:
        if powered:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Prepare powered accuracy validation",
                "This prepares five powered holdout crosses. Use a clean, restrained "
                "sacrificial surface at the calibrated height and only a previously "
                "verified visible-marking power. After reviewing and closing Preview, "
                "the main window's Start button submits this prepared powered job "
                "immediately.\n\nContinue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        job = self._message(
            "Accuracy validation",
            lambda: self.context.prepare_accuracy_validation_job(
                powered=powered,
                power_percent=self.validation_power.value(),
                mark_size_mm=self.validation_mark_size.value(),
                speed_mm_min=self.validation_speed.value(),
            ),
        )
        if job is not None:
            self.validationJobPrepared.emit(job)
            self.accept()

    def capture_accuracy_validation(self, *, home_first: bool = True) -> None:
        if not home_first and not self._photo_pose_confirmed:
            QtWidgets.QMessageBox.critical(
                self,
                "Accuracy validation",
                "Home / park and complete one precision capture before using recapture "
                "without homing",
            )
            return

        def invalidate() -> None:
            if home_first:
                self._invalidate_for_home_park()
            else:
                self._invalidate_validation_review(
                    "Prior validation review invalidated by precision recapture."
                )

        self._start_operation(
            "Accuracy validation precision capture",
            lambda: self.context.capture_accuracy_validation(home_first=home_first),
            lambda result: self._accuracy_validation_captured(
                result,
                home_first=home_first,
            ),
            requires_controller=True,
            invalidate=invalidate,
            on_failure=lambda message: self.validation_status.setText(
                f"Accuracy validation capture failed: {message}"
            ),
        )

    def _accuracy_validation_captured(
        self,
        result: tuple[np.ndarray, dict[str, Any]],
        *,
        home_first: bool,
    ) -> None:
        if home_first:
            self._set_photo_pose_confirmed(True)
        image, payload = result
        analysis = payload.get("analysis")
        if not isinstance(analysis, dict):
            self.validation_preview.set_image(image)
            self.validation_results.setRowCount(0)
            status = payload.get("reason", "Validation holdouts were not detected")
            precision = self._precision_capture_summary(payload.get("precision_capture"))
            self.validation_status.setText(status + (f"\n{precision}" if precision else ""))
            return

        measurements = list(analysis.get("measurements", []))
        by_id = {int(item["id"]): item for item in measurements}
        preview = image.copy()
        for point in payload.get("points", []):
            item = by_id.get(int(point["id"]))
            error = float(item.get("error_mm", float("inf"))) if item else float("inf")
            if analysis.get("passed"):
                color = (0, 220, 0)
            elif error <= float(analysis["max_limit_mm"]):
                color = (0, 165, 255)
            else:
                color = (0, 0, 255)
            center = (int(round(point["image_x"])), int(round(point["image_y"])))
            cv2.circle(preview, center, 14, color, 2, cv2.LINE_AA)
            cv2.putText(
                preview,
                str(point["id"]),
                (center[0] + 16, center[1] - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
        self.validation_preview.set_image(preview)
        self.validation_results.setRowCount(len(measurements))
        for row, item in enumerate(measurements):
            values = (
                item["id"],
                f"{item['machine_x']:.3f}",
                f"{item['machine_y']:.3f}",
                f"{item['observed_x']:.3f}",
                f"{item['observed_y']:.3f}",
                f"{item['error_x_mm']:+.3f}",
                f"{item['error_y_mm']:+.3f}",
                f"{item['error_mm']:.3f}",
            )
            for column, value in enumerate(values):
                self.validation_results.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        self.validation_status.setText(
            f"{analysis['classification'].upper()} · RMS "
            f"{analysis['rms_error_mm']:.3f} / ≤{analysis['rms_limit_mm']:.3f} mm · "
            f"maximum {analysis['max_error_mm']:.3f} / "
            f"≤{analysis['max_limit_mm']:.3f} mm · mean X"
            f"{analysis['mean_error_x_mm']:+.3f} Y"
            f"{analysis['mean_error_y_mm']:+.3f} mm\n{analysis['reason']}"
        )
        precision = self._precision_capture_summary(analysis.get("precision_capture"))
        if precision:
            self.validation_status.setText(self.validation_status.text() + f"\n{precision}")

    def prepare_dense_validation_job(self, powered: bool, *, confirmation: bool = False) -> None:
        if powered:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Prepare powered dense confirmation" if confirmation else "Prepare powered dense validation",
                "This prepares 16 fresh shifted confirmation crosses. Use a new or clean "
                "restrained surface and verified marking power. Continue?"
                if confirmation
                else "This prepares 16 independent interstitial crosses. Use a clean, "
                "restrained sacrificial surface and verified marking power. Continue?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        job = self._message(
            "Dense mesh validation",
            lambda: self.context.prepare_dense_calibration_job(
                powered=powered,
                power_percent=self.validation_power.value(),
                mark_size_mm=self.validation_mark_size.value(),
                speed_mm_min=self.validation_speed.value(),
                validation=not confirmation,
                confirmation=confirmation,
            ),
        )
        if job is not None:
            self.validationJobPrepared.emit(job)
            self.accept()

    def capture_dense_validation(self, *, confirmation: bool = False) -> None:
        name = "Dense mesh confirmation" if confirmation else "Dense mesh validation"
        self._start_operation(
            f"{name} precision capture",
            lambda: self.context.capture_dense_calibration(
                validation=not confirmation,
                confirmation=confirmation,
            ),
            self._dense_validation_captured,
            requires_controller=True,
            invalidate=self._invalidate_for_home_park,
            on_failure=lambda message: self.validation_status.setText(
                f"{name} capture failed: {message}"
            ),
        )

    def capture_dense_confirmation(self) -> None:
        self.capture_dense_validation(confirmation=True)

    def _dense_validation_captured(
        self,
        result: tuple[np.ndarray, dict[str, Any]],
    ) -> None:
        image, payload = result
        analysis = payload.get("analysis")
        if not isinstance(analysis, dict):
            self.validation_preview.set_image(image)
            self.validation_status.setText(payload.get("reason", "Dense holdouts were not detected"))
            return
        self._dense_validation_analysis = analysis
        refinement = analysis.get("refinement")
        can_refine = isinstance(refinement, dict) and bool(refinement.get("can_refine"))
        self.apply_dense_validation_refinement_button.setEnabled(can_refine)
        measurements = list(analysis.get("measurements", []))
        by_id = {int(item["id"]): item for item in measurements}
        preview = image.copy()
        rms_limit = float(analysis["rms_limit_mm"])
        max_limit = float(analysis["max_limit_mm"])
        for point in payload.get("points", []):
            identifier = int(point["id"])
            measurement = by_id.get(identifier)
            if measurement is None:
                continue
            expected_x, expected_y = self.context.bed.mm_to_image(
                float(measurement["machine_x"]),
                float(measurement["machine_y"]),
            )
            expected = (int(round(expected_x)), int(round(expected_y)))
            detected = (
                int(round(float(point["image_x"]))),
                int(round(float(point["image_y"]))),
            )
            error = float(measurement["error_mm"])
            color = (0, 220, 0) if error <= rms_limit else (0, 165, 255) if error <= max_limit else (0, 0, 255)
            # Cyan is the commanded location predicted by the active map;
            # the colored circle is the cross center actually detected.
            cv2.drawMarker(
                preview,
                expected,
                (255, 255, 0),
                cv2.MARKER_TILTED_CROSS,
                18,
                2,
                cv2.LINE_AA,
            )
            cv2.line(preview, expected, detected, color, 2, cv2.LINE_AA)
            cv2.circle(preview, detected, 11, color, 2, cv2.LINE_AA)
            cv2.putText(
                preview,
                str(identifier),
                (detected[0] + 13, detected[1] - 7),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                color,
                2,
                cv2.LINE_AA,
            )
        cv2.putText(
            preview,
            "cyan X = commanded  colored circle = detected",
            (18, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.58,
            (255, 255, 0),
            2,
            cv2.LINE_AA,
        )
        self.validation_preview.set_image(preview)
        self.validation_results.setRowCount(len(measurements))
        for row, item in enumerate(measurements):
            values = (
                item["id"],
                f"{item['machine_x']:.3f}",
                f"{item['machine_y']:.3f}",
                f"{item['observed_x']:.3f}",
                f"{item['observed_y']:.3f}",
                f"{item['error_x_mm']:+.3f}",
                f"{item['error_y_mm']:+.3f}",
                f"{item['error_mm']:.3f}",
            )
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(str(value))
                error = float(item["error_mm"])
                if error > max_limit:
                    cell.setForeground(QtGui.QColor("#ff6666"))
                elif error > rms_limit:
                    cell.setForeground(QtGui.QColor("#ffb347"))
                self.validation_results.setItem(row, column, cell)
        self.validation_status.setText(
            f"{analysis['classification'].upper()} · RMS {analysis['rms_error_mm']:.3f} "
            f"/ ≤{analysis['rms_limit_mm']:.3f} mm · maximum "
            f"{analysis['max_error_mm']:.3f} / ≤{analysis['max_limit_mm']:.3f} mm\n"
            "Preview: cyan X = commanded position; numbered colored circle = detected cross; "
            "line = measured error"
            + (
                f"\nRefinement available · predicted RMS "
                f"{refinement['predicted_rms_mm']:.3f} mm · predicted maximum "
                f"{refinement['predicted_max_mm']:.3f} mm"
                if can_refine
                else ""
            )
        )

    def apply_dense_validation_refinement(self) -> None:
        analysis = self._dense_validation_analysis
        refinement = analysis.get("refinement") if isinstance(analysis, dict) else None
        if not isinstance(refinement, dict) or not refinement.get("can_refine"):
            return
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Apply validation refinement",
            "Apply this bounded update to the existing local mesh? This result must then "
            "be checked using fresh marks from the shifted confirmation job. The original "
            "base map remains recoverable with Reset local mesh.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Apply validation refinement",
            lambda: self.context.apply_dense_validation_refinement(analysis),
        )
        if result is not None:
            self._dense_validation_analysis = None
            self.apply_dense_validation_refinement_button.setEnabled(False)
            self.validation_status.setText(
                "Validation refinement applied. Use a fresh sheet or clean side, then run "
                "the shifted confirmation job."
            )
            self.refresh_all()
            self.calibrationChanged.emit()

    def detect_workpiece(self) -> None:
        self._start_operation(
            "Workpiece precision capture",
            self.context.detect_workpiece,
            lambda result: self.check_results.setPlainText(json.dumps(result, indent=2)),
        )

    def detect_fiducials(self) -> None:
        self._start_operation(
            "Fiducial detection",
            self.context.detect_fiducials,
            lambda result: self.check_results.setPlainText(json.dumps(result, indent=2)),
        )
