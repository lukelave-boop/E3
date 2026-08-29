from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..project.raster_asset import RasterAssetPayload
from ..project.raster_vectorize import (
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationOptions,
    RasterVectorizationResult,
    RasterVectorizationSource,
    prepare_raster_vectorization_source,
    vectorize_prepared_raster,
)
from .qt import require_qt
from .tasks import FunctionTask

QtCore, QtGui, QtWidgets = require_qt()

_DEFAULT_DEBOUNCE_MS = 160

# FunctionTask deliberately disables QThreadPool auto-deletion. Keep every task
# alive independently of the dialog until its queued finished signal is handled;
# a caller is allowed to release a rejected dialog while a preview is running.
_LIVE_PREVIEW_TASKS: set[FunctionTask] = set()


def _retain_preview_task(task: FunctionTask) -> None:
    _LIVE_PREVIEW_TASKS.add(task)

    def release() -> None:
        _LIVE_PREVIEW_TASKS.discard(task)
        try:
            task.signals.finished.disconnect(release)
        except (RuntimeError, TypeError):
            pass

    task.signals.finished.connect(
        release,
        QtCore.Qt.ConnectionType.QueuedConnection,
    )


@dataclass(frozen=True, slots=True)
class _PreviewOutcome:
    result: RasterVectorizationResult | None
    prepared_source: RasterVectorizationSource | None = None
    error_message: str | None = None


def _exception_message(exc: Exception) -> str:
    parts = [str(exc) or exc.__class__.__name__]
    seen = {parts[0]}
    for raw_note in getattr(exc, "__notes__", ()):
        note = str(raw_note).strip()
        if note and note not in seen:
            parts.append(note)
            seen.add(note)
    return "\n".join(parts)


class _ImagePreview(QtWidgets.QWidget):
    """Small aspect-preserving image well with a transparency checkerboard."""

    def __init__(
        self,
        placeholder: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._image = QtGui.QImage()
        self._placeholder = str(placeholder)
        self.setMinimumSize(180, 150)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 - Qt override
        return QtCore.QSize(300, 220)

    def set_image(self, image: QtGui.QImage) -> None:
        self._image = QtGui.QImage(image)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        bounds = self.rect().adjusted(1, 1, -1, -1)

        tile = 12
        colors = (QtGui.QColor("#242A33"), QtGui.QColor("#313844"))
        for row, y in enumerate(range(bounds.top(), bounds.bottom() + 1, tile)):
            for column, x in enumerate(
                range(bounds.left(), bounds.right() + 1, tile)
            ):
                painter.fillRect(
                    QtCore.QRect(x, y, tile, tile),
                    colors[(row + column) % 2],
                )

        if not self._image.isNull():
            scaled = self._image.scaled(
                bounds.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            target = QtCore.QRect(
                bounds.center().x() - scaled.width() // 2,
                bounds.center().y() - scaled.height() // 2,
                scaled.width(),
                scaled.height(),
            )
            painter.drawImage(target, scaled)
        else:
            painter.setPen(QtGui.QColor("#AAB3C2"))
            painter.drawText(
                bounds,
                QtCore.Qt.AlignmentFlag.AlignCenter
                | QtCore.Qt.TextFlag.TextWordWrap,
                self._placeholder,
            )

        painter.setPen(QtGui.QPen(QtGui.QColor("#586273"), 1.0))
        painter.drawRect(bounds)


def _image_group(title: str, placeholder: str) -> tuple[Any, _ImagePreview]:
    group = QtWidgets.QGroupBox(title)
    layout = QtWidgets.QVBoxLayout(group)
    layout.setContentsMargins(6, 8, 6, 6)
    preview = _ImagePreview(placeholder, group)
    layout.addWidget(preview, 1)
    return group, preview


def _slider_row(
    minimum: int,
    maximum: int,
    value: int,
) -> tuple[Any, Any, Any]:
    widget = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(value)
    spin = QtWidgets.QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    spin.setMaximumWidth(78)
    slider.valueChanged.connect(spin.setValue)
    spin.valueChanged.connect(slider.setValue)
    layout.addWidget(slider, 1)
    layout.addWidget(spin)
    return widget, slider, spin


def _rgba_qimage(values: np.ndarray) -> QtGui.QImage:
    rgba = np.asarray(values)
    if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("Vectorization preview source must be an 8-bit RGBA image")
    contiguous = np.ascontiguousarray(rgba)
    image = QtGui.QImage(
        contiguous.data,
        contiguous.shape[1],
        contiguous.shape[0],
        contiguous.strides[0],
        QtGui.QImage.Format.Format_RGBA8888,
    )
    return image.copy()


def _mask_qimage(values: np.ndarray) -> QtGui.QImage:
    mask = np.asarray(values)
    if mask.dtype != np.uint8 or mask.ndim != 2:
        raise ValueError("Vectorization preview mask must be an 8-bit image")
    contiguous = np.ascontiguousarray(mask)
    image = QtGui.QImage(
        contiguous.data,
        contiguous.shape[1],
        contiguous.shape[0],
        contiguous.strides[0],
        QtGui.QImage.Format.Format_Grayscale8,
    )
    return image.copy()


def _overlay_qimage(result: RasterVectorizationResult) -> QtGui.QImage:
    return _rgba_qimage(result.overlay_rgba)


class RasterVectorizationDialog(QtWidgets.QDialog):
    """Window-modal, bounded preview and approval UI for raster vectorization."""

    def __init__(
        self,
        payload: RasterAssetPayload,
        width_mm: float,
        height_mm: float,
        parent: QtWidgets.QWidget | None = None,
        *,
        debounce_ms: int = _DEFAULT_DEBOUNCE_MS,
        vectorizer: Callable[..., RasterVectorizationResult] | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(payload, RasterAssetPayload):
            raise TypeError("payload must be a RasterAssetPayload")
        self.payload = payload
        self.width_mm = float(width_mm)
        self.height_mm = float(height_mm)
        if (
            not math.isfinite(self.width_mm)
            or not math.isfinite(self.height_mm)
            or self.width_mm <= 0.0
            or self.height_mm <= 0.0
        ):
            raise ValueError("Displayed raster width and height must be positive")

        self._vectorizer = vectorizer
        self._prepared_source: RasterVectorizationSource | None = None
        self._request_serial = 0
        self._latest_requested_id = 0
        self._active_request_id: int | None = None
        self._active_options: RasterVectorizationOptions | None = None
        self._active_task: FunctionTask | None = None
        self._pending_request: tuple[int, RasterVectorizationOptions] | None = None
        self._current_result: RasterVectorizationResult | None = None
        self._current_result_id: int | None = None
        self._current_options: RasterVectorizationOptions | None = None
        self._accepted_result: RasterVectorizationResult | None = None
        self._accepted_options: RasterVectorizationOptions | None = None
        self._closed = False
        self._preview_started = False
        self._has_usable_alpha = False

        interval = int(debounce_ms)
        if interval < 0 or interval > 5_000:
            raise ValueError("debounce_ms must be between 0 and 5000")
        self._debounce_timer = QtCore.QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(interval)
        self._debounce_timer.timeout.connect(self._debounce_elapsed)

        self.setWindowTitle("Raster Vectorization")
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.setModal(True)
        self.setMinimumSize(980, 700)
        self.resize(1120, 780)
        self.setSizeGripEnabled(True)

        layout = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QLabel("Trace image to vectors")
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)
        explanation = QtWidgets.QLabel(
            "Build native E3 line and cubic path geometry from this single-color "
            "image. Review the mask and vector overlay before creating anything."
        )
        explanation.setObjectName("mutedLabel")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        previews = QtWidgets.QHBoxLayout()
        original_group, self.original_preview = _image_group(
            "Original raster",
            "Decoding bounded source…",
        )
        mask_group, self.mask_preview = _image_group(
            "Foreground mask",
            "Adjust detection settings to build a mask",
        )
        overlay_group, self.overlay_preview = _image_group(
            "Vector overlay",
            "Generated vector paths will appear here",
        )
        previews.addWidget(original_group, 1)
        previews.addWidget(mask_group, 1)
        previews.addWidget(overlay_group, 1)
        layout.addLayout(previews, 1)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self._build_detection_group(), 3)
        controls.addWidget(self._build_output_group(), 2)
        layout.addLayout(controls)

        self.status_label = QtWidgets.QLabel("Waiting to build preview…")
        self.status_label.setObjectName("mutedLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.status_label)

        self.stats_label = QtWidgets.QLabel(
            "Raw contour points —  ·  fitted segments —  ·  "
            "preview-flattened points —  ·  "
            "maximum deviation —"
        )
        self.stats_label.setObjectName("mutedLabel")
        self.stats_label.setWordWrap(True)
        self.stats_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.stats_label)

        self.button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.cancel_button = self.button_box.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.create_button = self.button_box.addButton(
            "Create vectors",
            QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.create_button.setObjectName("primaryActionButton")
        self.create_button.setEnabled(False)
        self.create_button.setDefault(True)
        self.button_box.accepted.connect(self._accept_latest)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self._connect_option_signals()
        self._sync_control_state()

    def _build_detection_group(self) -> Any:
        defaults = RasterVectorizationOptions()
        group = QtWidgets.QGroupBox("Detection and geometry")
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self.detection_combo = QtWidgets.QComboBox()
        self.detection_combo.addItem(
            "Auto threshold",
            RasterDetectionMode.AUTO_THRESHOLD,
        )
        self.detection_combo.addItem(
            "Manual threshold",
            RasterDetectionMode.MANUAL_THRESHOLD,
        )
        self.detection_combo.addItem(
            "Transparency / alpha",
            RasterDetectionMode.ALPHA,
        )
        self._alpha_mode_index = self.detection_combo.count() - 1
        self.detection_combo.setCurrentIndex(
            self.detection_combo.findData(defaults.detection_mode)
        )
        form.addRow("Detection mode", self.detection_combo)

        (
            self.threshold_row,
            self.threshold_slider,
            self.threshold_spin,
        ) = _slider_row(0, 255, int(defaults.threshold))
        self.threshold_row.setToolTip(
            "Manual 8-bit threshold. It is unavailable while automatic or alpha "
            "detection is selected."
        )
        form.addRow("Threshold", self.threshold_row)

        self.invert_check = QtWidgets.QCheckBox("Invert foreground and background")
        self.invert_check.setChecked(bool(defaults.invert))
        form.addRow("", self.invert_check)

        (
            self.alpha_row,
            self.alpha_slider,
            self.alpha_spin,
        ) = _slider_row(0, 255, int(defaults.alpha_cutoff))
        self.alpha_row.setToolTip(
            "Pixels below this opacity are excluded. Available only when the "
            "decoded source contains useful transparency."
        )
        form.addRow("Alpha cutoff", self.alpha_row)

        self.minimum_feature_spin = QtWidgets.QDoubleSpinBox()
        self.minimum_feature_spin.setRange(0.0, 100_000.0)
        self.minimum_feature_spin.setDecimals(3)
        self.minimum_feature_spin.setSingleStep(0.05)
        self.minimum_feature_spin.setSuffix(" mm²")
        self.minimum_feature_spin.setValue(defaults.minimum_feature_area_mm2)
        self.minimum_feature_spin.setToolTip(
            "Remove isolated connected features smaller than this physical area."
        )
        form.addRow("Minimum feature / speck area", self.minimum_feature_spin)

        self.smoothing_spin = QtWidgets.QDoubleSpinBox()
        self.smoothing_spin.setRange(0.0, 25.0)
        self.smoothing_spin.setDecimals(3)
        self.smoothing_spin.setSingleStep(0.05)
        self.smoothing_spin.setSuffix(" mm")
        self.smoothing_spin.setValue(defaults.smoothing_mm)
        self.smoothing_spin.setToolTip(
            "Smooth curved regions while retaining detected sharp corners."
        )
        form.addRow("Smoothing", self.smoothing_spin)

        self.simplification_spin = QtWidgets.QDoubleSpinBox()
        self.simplification_spin.setRange(0.001, 25.0)
        self.simplification_spin.setDecimals(3)
        self.simplification_spin.setSingleStep(0.025)
        self.simplification_spin.setSuffix(" mm")
        self.simplification_spin.setValue(defaults.simplification_tolerance_mm)
        self.simplification_spin.setToolTip(
            "Maximum raster trace-fitting and preview-flattening error in "
            "millimetres at the image's displayed size. Smaller values retain "
            "more native segments and preview detail; machine planning uses its "
            "separate controlled flattening tolerance."
        )
        form.addRow("Simplification / max fitting error", self.simplification_spin)
        return group

    def _build_output_group(self) -> Any:
        defaults = RasterVectorizationOptions()
        group = QtWidgets.QGroupBox("Contours and source")
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        self.contour_combo = QtWidgets.QComboBox()
        self.contour_combo.addItem(
            "Outer outline only",
            RasterContourOutput.OUTER_ONLY,
        )
        self.contour_combo.addItem(
            "Preserve all contours and holes",
            RasterContourOutput.ALL_CONTOURS,
        )
        self.contour_combo.setCurrentIndex(
            self.contour_combo.findData(defaults.contour_output)
        )
        self.contour_combo.setToolTip(
            "Preserving all contours retains counters in letters and nested holes."
        )
        form.addRow("Contour output", self.contour_combo)

        self.source_combo = QtWidgets.QComboBox()
        self.source_combo.addItem("Replace source image", "replace")
        self.source_combo.addItem("Keep source image", "keep")
        form.addRow("Source handling", self.source_combo)

        self.hide_source_check = QtWidgets.QCheckBox(
            "Hide the original after vector creation"
        )
        self.hide_source_check.setChecked(True)
        form.addRow("", self.hide_source_check)

        note = QtWidgets.QLabel(
            "Vectors are committed only after Create vectors. Their project layer "
            "and output authorization are chosen by the Objects workflow."
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        form.addRow(note)
        return group

    def _connect_option_signals(self) -> None:
        self.detection_combo.currentIndexChanged.connect(
            lambda _index: self._detection_mode_changed()
        )
        self.threshold_spin.valueChanged.connect(
            lambda _value: self._schedule_preview()
        )
        self.invert_check.toggled.connect(lambda _value: self._schedule_preview())
        self.alpha_spin.valueChanged.connect(lambda _value: self._schedule_preview())
        self.minimum_feature_spin.valueChanged.connect(
            lambda _value: self._schedule_preview()
        )
        self.smoothing_spin.valueChanged.connect(
            lambda _value: self._schedule_preview()
        )
        self.simplification_spin.valueChanged.connect(
            lambda _value: self._schedule_preview()
        )
        self.contour_combo.currentIndexChanged.connect(
            lambda _index: self._schedule_preview()
        )
        self.source_combo.currentIndexChanged.connect(
            lambda _index: self._sync_control_state()
        )

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._preview_started:
            self._preview_started = True
            self._schedule_preview()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt override
        self._abandon_pending_work()
        super().closeEvent(event)

    def _detection_mode_changed(self) -> None:
        self._sync_control_state()
        self._schedule_preview()

    def _sync_control_state(self) -> None:
        mode = self.detection_combo.currentData()
        manual = mode == RasterDetectionMode.MANUAL_THRESHOLD
        self.threshold_row.setEnabled(manual)
        self.alpha_row.setEnabled(self._has_usable_alpha)
        self.hide_source_check.setEnabled(self.source_handling == "keep")

        model = self.detection_combo.model()
        alpha_item = model.item(self._alpha_mode_index)
        if alpha_item is not None:
            alpha_item.setEnabled(self._has_usable_alpha)
            alpha_item.setToolTip(
                "Use decoded pixel opacity as the foreground mask."
                if self._has_usable_alpha
                else "The decoded source has no useful transparency."
            )

    def selected_options(self) -> RasterVectorizationOptions:
        detection_mode = self.detection_combo.currentData()
        if not isinstance(detection_mode, RasterDetectionMode):
            detection_mode = RasterDetectionMode(str(detection_mode))
        contour_output = self.contour_combo.currentData()
        if not isinstance(contour_output, RasterContourOutput):
            contour_output = RasterContourOutput(str(contour_output))
        return RasterVectorizationOptions(
            detection_mode=detection_mode,
            threshold=int(self.threshold_spin.value()),
            invert=bool(self.invert_check.isChecked()),
            alpha_cutoff=int(self.alpha_spin.value()),
            minimum_feature_area_mm2=float(self.minimum_feature_spin.value()),
            smoothing_mm=float(self.smoothing_spin.value()),
            simplification_tolerance_mm=float(self.simplification_spin.value()),
            contour_output=contour_output,
        )

    @property
    def vectorization_result(self) -> RasterVectorizationResult | None:
        return self._accepted_result

    @property
    def accepted_options(self) -> RasterVectorizationOptions | None:
        return self._accepted_options

    @property
    def source_handling(self) -> str:
        return str(self.source_combo.currentData())

    @property
    def hide_source_after(self) -> bool:
        return self.source_handling == "keep" and self.hide_source_check.isChecked()

    def _set_status(self, message: str, state: str = "muted") -> None:
        names = {
            "muted": "mutedLabel",
            "good": "statusGood",
            "warning": "statusWarning",
            "bad": "statusBad",
        }
        self.status_label.setObjectName(names.get(state, "mutedLabel"))
        self.status_label.setText(str(message))
        style = self.status_label.style()
        style.unpolish(self.status_label)
        style.polish(self.status_label)

    def _schedule_preview(self) -> None:
        if self._closed:
            return
        try:
            options = self.selected_options()
        except (TypeError, ValueError) as exc:
            self.create_button.setEnabled(False)
            self._set_status(str(exc), "bad")
            return
        self._request_serial += 1
        request = (self._request_serial, options)
        self._latest_requested_id = self._request_serial
        self._pending_request = request
        self._current_result = None
        self._current_result_id = None
        self._current_options = None
        self.create_button.setEnabled(False)
        self._set_status("Updating vector preview…")
        if self._active_task is None:
            self._debounce_timer.start()

    def _debounce_elapsed(self) -> None:
        if self._closed or self._active_task is not None:
            return
        request = self._pending_request
        self._pending_request = None
        if request is not None:
            self._start_preview(*request)

    def _start_preview(
        self,
        request_id: int,
        options: RasterVectorizationOptions,
    ) -> None:
        if self._closed:
            return
        self._active_request_id = request_id
        self._set_status("Building mask and fitting vector paths…")
        vectorizer = self._vectorizer
        payload = self.payload
        prepared_source = self._prepared_source
        width_mm = self.width_mm
        height_mm = self.height_mm

        def operation() -> _PreviewOutcome:
            if vectorizer is not None:
                result = vectorizer(
                    payload,
                    options,
                    displayed_width_mm=width_mm,
                    displayed_height_mm=height_mm,
                )
                return _PreviewOutcome(result=result)
            source = prepared_source
            if source is None:
                source = prepare_raster_vectorization_source(payload)
            try:
                result = vectorize_prepared_raster(
                    source,
                    options,
                    displayed_width_mm=width_mm,
                    displayed_height_mm=height_mm,
                )
            except Exception as exc:
                return _PreviewOutcome(
                    result=None,
                    prepared_source=source,
                    error_message=_exception_message(exc),
                )
            return _PreviewOutcome(result=result, prepared_source=source)

        task = FunctionTask(operation)
        self._active_task = task
        self._active_options = options
        task.signals.succeeded.connect(
            self._active_preview_succeeded,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            self._active_preview_failed,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            self._active_preview_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        _retain_preview_task(task)
        QtCore.QThreadPool.globalInstance().start(task)

    @QtCore.Slot(object)
    def _active_preview_succeeded(self, outcome: object) -> None:
        if self._closed:
            return
        request_id = self._active_request_id
        options = self._active_options
        if request_id is None or options is None:
            return
        if not isinstance(outcome, _PreviewOutcome):
            self._preview_failed(
                request_id,
                "Vector preview worker returned an invalid result",
            )
            return
        if outcome.prepared_source is not None:
            self._prepared_source = outcome.prepared_source
            self._show_prepared_source(outcome.prepared_source)
        if outcome.error_message is not None:
            self._preview_failed(request_id, outcome.error_message)
            return
        if outcome.result is None:
            self._preview_failed(
                request_id,
                "Vector preview worker returned no result",
            )
            return
        self._preview_succeeded(request_id, options, outcome.result)

    @QtCore.Slot(str)
    def _active_preview_failed(self, message: str) -> None:
        request_id = self._active_request_id
        if request_id is not None:
            self._preview_failed(request_id, message)

    @QtCore.Slot()
    def _active_preview_finished(self) -> None:
        task = self._active_task
        request_id = self._active_request_id
        if task is not None and request_id is not None:
            self._preview_finished(request_id, task)

    def _preview_succeeded(
        self,
        request_id: int,
        options: RasterVectorizationOptions,
        result: RasterVectorizationResult,
    ) -> None:
        if self._closed or request_id != self._latest_requested_id:
            return
        try:
            original = _rgba_qimage(result.source_rgba)
            mask = _mask_qimage(result.foreground_mask)
            overlay = _overlay_qimage(result)
        except (TypeError, ValueError) as exc:
            self._preview_failed(request_id, str(exc))
            return

        self._has_usable_alpha = bool(result.has_usable_alpha)
        self._sync_control_state()
        self.original_preview.set_image(original)
        self.mask_preview.set_image(mask)
        self.overlay_preview.set_image(overlay)
        self.stats_label.setText(
            f"Raw contour points {result.raw_contour_point_count:,}  ·  "
            f"fitted segments {result.fitted_segment_count:,}  ·  "
            "preview-flattened points "
            f"{result.preview_flattened_point_count:,}  ·  "
            "maximum estimated deviation "
            f"{result.max_estimated_deviation_mm:.3f} mm"
        )
        self._current_result = result
        self._current_result_id = request_id
        self._current_options = options
        if result.threshold_used is None:
            method = f"alpha cutoff {options.alpha_cutoff}"
        else:
            method = f"threshold {result.threshold_used}"
        self._set_status(
            f"Preview ready · {method}",
            "good",
        )

    def _show_prepared_source(self, source: RasterVectorizationSource) -> None:
        try:
            original = _rgba_qimage(source.source_rgba)
        except (TypeError, ValueError):
            return
        self.original_preview.set_image(original)
        self._has_usable_alpha = bool(source.has_usable_alpha)
        self._sync_control_state()

    def _preview_failed(self, request_id: int, message: str) -> None:
        if self._closed or request_id != self._latest_requested_id:
            return
        self._current_result = None
        self._current_result_id = None
        self._current_options = None
        self._accepted_options = None
        self.create_button.setEnabled(False)
        suggestion = ""
        if (
            self._has_usable_alpha
            and self.detection_combo.currentData() != RasterDetectionMode.ALPHA
        ):
            suggestion = " Try Transparency / alpha detection."
        self._set_status(
            f"Could not vectorize this image: {message}{suggestion}",
            "bad",
        )

    def _preview_finished(self, request_id: int, task: FunctionTask) -> None:
        if task is not self._active_task or request_id != self._active_request_id:
            return
        self._active_task = None
        self._active_request_id = None
        self._active_options = None
        if self._closed:
            self._pending_request = None
            return
        if self._pending_request is not None:
            pending = self._pending_request
            self._pending_request = None
            self._debounce_timer.stop()
            self._start_preview(*pending)
            return
        if (
            self._current_result is not None
            and self._current_result_id == self._latest_requested_id
        ):
            self.create_button.setEnabled(True)

    def _accept_latest(self) -> None:
        if (
            self._closed
            or self._active_task is not None
            or self._pending_request is not None
            or self._current_result is None
            or self._current_result_id != self._latest_requested_id
        ):
            return
        self._accepted_result = self._current_result
        self._accepted_options = self._current_options
        self._closed = True
        self._debounce_timer.stop()
        super().accept()

    def _abandon_pending_work(self) -> None:
        self._closed = True
        self._debounce_timer.stop()
        self._pending_request = None
        self.create_button.setEnabled(False)

    def reject(self) -> None:
        self._accepted_result = None
        self._accepted_options = None
        self._abandon_pending_work()
        super().reject()


__all__ = ["RasterVectorizationDialog"]
