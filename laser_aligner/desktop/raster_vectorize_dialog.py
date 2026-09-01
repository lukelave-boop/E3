from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..project.raster_asset import RasterAssetPayload
from ..project.raster_vectorize import (
    RasterContourOutput,
    RasterDetectionMode,
    RasterVectorizationOptions,
    RasterVectorizationQuickPreview,
    RasterVectorizationResult,
    RasterVectorizationSource,
    prepare_raster_vectorization_source,
    quick_preview_prepared_raster,
    vectorize_prepared_raster,
)
from .qt import require_qt
from .tasks import FunctionTask

QtCore, QtGui, QtWidgets = require_qt()

_DEFAULT_DEBOUNCE_MS = 160
_DEFAULT_OVERLAY_COLOR = "#FF4F9F"
_DEFAULT_OVERLAY_OPACITY_PERCENT = 100
_OVERLAY_COLOR_PRESETS = (
    ("Magenta", _DEFAULT_OVERLAY_COLOR),
    ("Cyan", "#00D9FF"),
    ("Yellow", "#FFD84D"),
    ("White", "#FFFFFF"),
    ("Black", "#000000"),
)

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
class _ExactPreviewOutcome:
    result: RasterVectorizationResult | None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class _QuickPreviewOutcome:
    preview: RasterVectorizationQuickPreview | None
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
        self._overlay_path = QtGui.QPainterPath()
        self._overlay_color = QtGui.QColor(_DEFAULT_OVERLAY_COLOR)
        self._overlay_opacity = 1.0
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

    def set_vector_overlay(self, contours: Iterable[Any]) -> None:
        path = QtGui.QPainterPath()
        for contour in contours:
            points = tuple(getattr(contour, "points", ()))
            if len(points) < 2:
                continue
            first = points[0]
            path.moveTo(float(first[0]) + 0.5, 0.5 - float(first[1]))
            for point in points[1:]:
                path.lineTo(float(point[0]) + 0.5, 0.5 - float(point[1]))
            path.closeSubpath()
        self._overlay_path = path
        self.update()

    def set_overlay_style(self, color: str, opacity: float) -> None:
        selected = QtGui.QColor(str(color))
        opacity = float(opacity)
        if not selected.isValid():
            raise ValueError(f"Invalid vector overlay color: {color!r}")
        if not math.isfinite(opacity) or not 0.0 <= opacity <= 1.0:
            raise ValueError("Vector overlay opacity must be between 0 and 1")
        self._overlay_color = selected
        self._overlay_opacity = opacity
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt override
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
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
            if not self._overlay_path.isEmpty() and self._overlay_opacity > 0.0:
                painter.save()
                painter.setClipRect(target)
                painter.translate(target.left(), target.top())
                painter.scale(float(target.width()), float(target.height()))
                color = QtGui.QColor(self._overlay_color)
                color.setAlphaF(self._overlay_opacity)
                pen = QtGui.QPen(color, 2.0)
                pen.setCosmetic(True)
                pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawPath(self._overlay_path)
                painter.restore()
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
        quick_vectorizer: Callable[..., RasterVectorizationQuickPreview]
        | None = None,
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
        self._quick_vectorizer = quick_vectorizer
        self._prepared_source: RasterVectorizationSource | None = None
        self._request_serial = 0
        self._latest_requested_id = 0
        self._quick_request_id: int | None = None
        self._quick_options: RasterVectorizationOptions | None = None
        self._quick_task: FunctionTask | None = None
        self._quick_cancel_event: threading.Event | None = None
        self._pending_quick_request: (
            tuple[int, RasterVectorizationOptions] | None
        ) = None
        self._exact_request_id: int | None = None
        self._exact_options: RasterVectorizationOptions | None = None
        self._exact_task: FunctionTask | None = None
        self._exact_cancel_event: threading.Event | None = None
        self._pending_exact_request: (
            tuple[int, RasterVectorizationOptions] | None
        ) = None
        self._current_quick_id: int | None = None
        self._current_quick_preview: RasterVectorizationQuickPreview | None = None
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
            "validated fit —  ·  maximum estimated deviation —"
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
        self._sync_overlay_style()

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
            "Maximum continuously validated native line/Bezier fitting error in "
            "millimetres at the image's displayed size. Preview flattening and "
            "intentional smoothing are measured separately; machine planning uses "
            "its own controlled flattening tolerance."
        )
        form.addRow("Native fitting tolerance", self.simplification_spin)
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

        self.overlay_color_combo = QtWidgets.QComboBox()
        for name, color in _OVERLAY_COLOR_PRESETS:
            swatch = QtGui.QPixmap(14, 14)
            swatch.fill(QtGui.QColor(color))
            self.overlay_color_combo.addItem(QtGui.QIcon(swatch), name, color)
        self.overlay_color_combo.setCurrentIndex(
            self.overlay_color_combo.findData(_DEFAULT_OVERLAY_COLOR)
        )
        self.overlay_color_combo.setToolTip(
            "Choose a high-contrast display color for the fitted vector preview. "
            "This does not change project layers or output settings."
        )
        form.addRow("Overlay color", self.overlay_color_combo)

        (
            self.overlay_opacity_row,
            self.overlay_opacity_slider,
            self.overlay_opacity_spin,
        ) = _slider_row(0, 100, _DEFAULT_OVERLAY_OPACITY_PERCENT)
        self.overlay_opacity_spin.setSuffix(" %")
        self.overlay_opacity_row.setToolTip(
            "Adjust only the fitted vector preview opacity. This does not change "
            "the traced geometry or project output settings."
        )
        form.addRow("Overlay opacity", self.overlay_opacity_row)

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
            "Overlay color and opacity are preview-only. Vectors are committed "
            "only after Create vectors; their project layer and output "
            "authorization are chosen by the Objects workflow."
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
        self.overlay_color_combo.currentIndexChanged.connect(
            lambda _index: self._sync_overlay_style()
        )
        self.overlay_opacity_spin.valueChanged.connect(
            lambda _value: self._sync_overlay_style()
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
    def overlay_color(self) -> str:
        return str(self.overlay_color_combo.currentData())

    @property
    def overlay_opacity(self) -> float:
        return float(self.overlay_opacity_spin.value()) / 100.0

    def _sync_overlay_style(self) -> None:
        self.overlay_preview.set_overlay_style(
            self.overlay_color,
            self.overlay_opacity,
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

    @property
    def _active_task(self) -> FunctionTask | None:
        """Compatibility view for tests that only need any active preview work."""

        return self._exact_task or self._quick_task

    @property
    def _pending_request(
        self,
    ) -> tuple[int, RasterVectorizationOptions] | None:
        """Compatibility view of the newest queued work across both stages."""

        return self._pending_quick_request or self._pending_exact_request

    def _sync_create_button(self) -> None:
        self.create_button.setEnabled(
            bool(
                not self._closed
                and self._quick_task is None
                and self._exact_task is None
                and self._pending_quick_request is None
                and self._pending_exact_request is None
                and self._current_result is not None
                and self._current_result_id == self._latest_requested_id
            )
        )

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
        if self._quick_cancel_event is not None:
            self._quick_cancel_event.set()
        if self._exact_cancel_event is not None:
            self._exact_cancel_event.set()
        request = (self._request_serial, options)
        self._latest_requested_id = self._request_serial
        self._pending_quick_request = request
        self._pending_exact_request = None
        self._current_quick_id = None
        self._current_quick_preview = None
        self._current_result = None
        self._current_result_id = None
        self._current_options = None
        self.create_button.setEnabled(False)
        self._set_status("Updating quick preview…")
        if self._quick_task is None:
            self._debounce_timer.start()

    def _debounce_elapsed(self) -> None:
        if self._closed or self._quick_task is not None:
            return
        request = self._pending_quick_request
        self._pending_quick_request = None
        if request is not None:
            self._start_quick_preview(*request)

    def _start_quick_preview(
        self,
        request_id: int,
        options: RasterVectorizationOptions,
    ) -> None:
        if self._closed:
            return
        self._quick_request_id = request_id
        self._quick_options = options
        if request_id == self._latest_requested_id:
            self._set_status("Building quick preview…")
        quick_vectorizer = self._quick_vectorizer
        payload = self.payload
        prepared_source = self._prepared_source
        width_mm = self.width_mm
        height_mm = self.height_mm
        cancellation = threading.Event()
        self._quick_cancel_event = cancellation

        def operation() -> _QuickPreviewOutcome:
            source = prepared_source
            try:
                if cancellation.is_set():
                    raise RuntimeError("Raster quick preview was cancelled")
                if source is None:
                    source = prepare_raster_vectorization_source(payload)
                if cancellation.is_set():
                    raise RuntimeError("Raster quick preview was cancelled")
                if quick_vectorizer is None:
                    preview = quick_preview_prepared_raster(
                        source,
                        options,
                        displayed_width_mm=width_mm,
                        displayed_height_mm=height_mm,
                        cancel_check=cancellation.is_set,
                    )
                else:
                    preview = quick_vectorizer(
                        source,
                        options,
                        displayed_width_mm=width_mm,
                        displayed_height_mm=height_mm,
                    )
            except Exception as exc:
                return _QuickPreviewOutcome(
                    preview=None,
                    prepared_source=source,
                    error_message=_exception_message(exc),
                )
            return _QuickPreviewOutcome(
                preview=preview,
                prepared_source=source,
            )

        task = FunctionTask(
            operation,
            label="Raster quick preview",
            cancel=cancellation.set,
        )
        self._quick_task = task
        task.signals.succeeded.connect(
            self._active_quick_succeeded,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            self._active_quick_failed,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            self._active_quick_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        _retain_preview_task(task)
        task.start_on(QtCore.QThreadPool.globalInstance())

    @QtCore.Slot(object)
    def _active_quick_succeeded(self, outcome: object) -> None:
        if self._closed:
            return
        request_id = self._quick_request_id
        options = self._quick_options
        if request_id is None or options is None:
            return
        if not isinstance(outcome, _QuickPreviewOutcome):
            self._quick_preview_failed(
                request_id,
                "Quick preview worker returned an invalid result",
            )
            return
        if outcome.prepared_source is not None:
            self._prepared_source = outcome.prepared_source
            self._show_prepared_source(outcome.prepared_source)
        if outcome.error_message is not None:
            self._quick_preview_failed(request_id, outcome.error_message)
            return
        if outcome.preview is None:
            self._quick_preview_failed(
                request_id,
                "Quick preview worker returned no result",
            )
            return
        self._quick_preview_succeeded(request_id, options, outcome.preview)

    @QtCore.Slot(str)
    def _active_quick_failed(self, message: str) -> None:
        request_id = self._quick_request_id
        if request_id is not None:
            self._quick_preview_failed(request_id, message)

    @QtCore.Slot()
    def _active_quick_finished(self) -> None:
        task = self._quick_task
        request_id = self._quick_request_id
        if task is None or request_id is None:
            return
        self._quick_task = None
        self._quick_cancel_event = None
        self._quick_request_id = None
        self._quick_options = None
        if self._closed:
            self._pending_quick_request = None
            return
        if self._pending_quick_request is not None:
            pending = self._pending_quick_request
            self._pending_quick_request = None
            self._debounce_timer.stop()
            self._start_quick_preview(*pending)
        self._sync_create_button()

    def _quick_preview_succeeded(
        self,
        request_id: int,
        options: RasterVectorizationOptions,
        preview: RasterVectorizationQuickPreview,
    ) -> None:
        if self._closed or request_id != self._latest_requested_id:
            return
        try:
            original = _rgba_qimage(preview.source_rgba)
            mask = _mask_qimage(preview.foreground_mask)
            overlay = _rgba_qimage(preview.source_rgba)
        except (TypeError, ValueError) as exc:
            self._quick_preview_failed(request_id, str(exc))
            return
        self._has_usable_alpha = bool(preview.has_usable_alpha)
        self._sync_control_state()
        self.original_preview.set_image(original)
        self.mask_preview.set_image(mask)
        self.overlay_preview.set_image(overlay)
        self.overlay_preview.set_vector_overlay(preview.contours)
        self._sync_overlay_style()
        self.stats_label.setText(
            f"Quick preview · raw contour points {preview.raw_contour_point_count:,}  ·  "
            f"display points {preview.preview_point_count:,}  ·  verified fit pending"
        )
        self._current_quick_id = request_id
        self._current_quick_preview = preview
        self._pending_exact_request = (request_id, options)
        self._set_status("Quick preview · Refining verified vectors…")
        self._start_pending_exact_if_possible()

    def _start_pending_exact_if_possible(self) -> None:
        if self._closed or self._exact_task is not None:
            return
        pending = self._pending_exact_request
        self._pending_exact_request = None
        if pending is None:
            return
        request_id, options = pending
        if (
            request_id != self._latest_requested_id
            or request_id != self._current_quick_id
        ):
            return
        self._start_exact_preview(request_id, options)

    def _start_exact_preview(
        self,
        request_id: int,
        options: RasterVectorizationOptions,
    ) -> None:
        if self._closed or request_id != self._latest_requested_id:
            return
        source = self._prepared_source
        if source is None:
            self._quick_preview_failed(
                request_id,
                "Verified fitting has no prepared raster source",
            )
            return
        self._exact_request_id = request_id
        self._exact_options = options
        self._set_status("Quick preview · Refining verified vectors…")
        vectorizer = self._vectorizer
        prepared_preview = self._current_quick_preview
        payload = self.payload
        width_mm = self.width_mm
        height_mm = self.height_mm
        cancellation = threading.Event()
        self._exact_cancel_event = cancellation

        def operation() -> _ExactPreviewOutcome:
            try:
                if cancellation.is_set():
                    raise RuntimeError("Raster vector fitting was cancelled")
                if vectorizer is not None:
                    result = vectorizer(
                        payload,
                        options,
                        displayed_width_mm=width_mm,
                        displayed_height_mm=height_mm,
                    )
                else:
                    result = vectorize_prepared_raster(
                        source,
                        options,
                        displayed_width_mm=width_mm,
                        displayed_height_mm=height_mm,
                        prepared_preview=prepared_preview,
                        cancel_check=cancellation.is_set,
                    )
                if cancellation.is_set():
                    raise RuntimeError("Raster vector fitting was cancelled")
            except Exception as exc:
                return _ExactPreviewOutcome(
                    result=None,
                    error_message=_exception_message(exc),
                )
            return _ExactPreviewOutcome(result=result)

        task = FunctionTask(
            operation,
            label="Raster verified vector fit",
            cancel=cancellation.set,
        )
        self._exact_task = task
        task.signals.succeeded.connect(
            self._active_exact_succeeded,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            self._active_exact_failed,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            self._active_exact_finished,
            QtCore.Qt.ConnectionType.QueuedConnection,
        )
        _retain_preview_task(task)
        task.start_on(QtCore.QThreadPool.globalInstance())

    @QtCore.Slot(object)
    def _active_exact_succeeded(self, outcome: object) -> None:
        if self._closed:
            return
        request_id = self._exact_request_id
        options = self._exact_options
        if request_id is None or options is None:
            return
        if not isinstance(outcome, _ExactPreviewOutcome):
            self._exact_preview_failed(
                request_id,
                "Verified vector worker returned an invalid result",
            )
            return
        if outcome.error_message is not None:
            self._exact_preview_failed(request_id, outcome.error_message)
            return
        if outcome.result is None:
            self._exact_preview_failed(
                request_id,
                "Verified vector worker returned no result",
            )
            return
        self._verified_preview_succeeded(request_id, options, outcome.result)

    @QtCore.Slot(str)
    def _active_exact_failed(self, message: str) -> None:
        request_id = self._exact_request_id
        if request_id is not None:
            self._exact_preview_failed(request_id, message)

    @QtCore.Slot()
    def _active_exact_finished(self) -> None:
        task = self._exact_task
        request_id = self._exact_request_id
        if task is None or request_id is None:
            return
        self._exact_task = None
        self._exact_cancel_event = None
        self._exact_request_id = None
        self._exact_options = None
        if self._closed:
            self._pending_exact_request = None
            return
        self._start_pending_exact_if_possible()
        self._sync_create_button()

    def _verified_preview_succeeded(
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
            overlay = _rgba_qimage(result.source_rgba)
        except (TypeError, ValueError) as exc:
            self._exact_preview_failed(request_id, str(exc))
            return

        self._has_usable_alpha = bool(result.has_usable_alpha)
        self._sync_control_state()
        self.original_preview.set_image(original)
        self.mask_preview.set_image(mask)
        self.overlay_preview.set_image(overlay)
        self.overlay_preview.set_vector_overlay(result.contours)
        self._sync_overlay_style()
        fitting_samples = sum(
            contour.fitting_error_sample_count for contour in result.contours
        )
        rms_fitting_error = (
            math.sqrt(
                sum(
                    contour.rms_fitting_error_mm**2
                    * contour.fitting_error_sample_count
                    for contour in result.contours
                )
                / fitting_samples
            )
            if fitting_samples
            else 0.0
        )
        self.stats_label.setText(
            f"Raw contour points {result.raw_contour_point_count:,}  ·  "
            f"fitted segments {result.fitted_segment_count:,}  ·  "
            "preview-flattened points "
            f"{result.preview_flattened_point_count:,}  ·  "
            "validated fit max / RMS "
            f"{max(c.max_fitting_error_mm for c in result.contours):.3f} / "
            f"{rms_fitting_error:.3f} mm  ·  "
            "maximum estimated deviation "
            f"{result.max_estimated_deviation_mm:.3f} mm  ·  "
            f"hard corners {sum(c.hard_corner_count for c in result.contours):,}  ·  "
            f"recursive splits {sum(c.recursive_split_count for c in result.contours):,}  ·  "
            f"verified merges {sum(c.merged_segment_count for c in result.contours):,}"
        )
        self._current_result = result
        self._current_result_id = request_id
        self._current_options = options
        if result.threshold_used is None:
            method = f"alpha cutoff {options.alpha_cutoff}"
        else:
            method = f"threshold {result.threshold_used}"
        self._set_status(f"Verified · {method}", "good")

    def _show_prepared_source(self, source: RasterVectorizationSource) -> None:
        try:
            original = _rgba_qimage(source.source_rgba)
        except (TypeError, ValueError):
            return
        self.original_preview.set_image(original)
        self._has_usable_alpha = bool(source.has_usable_alpha)
        self._sync_control_state()

    def _quick_preview_failed(self, request_id: int, message: str) -> None:
        if self._closed or request_id != self._latest_requested_id:
            return
        self._current_quick_id = None
        self._current_quick_preview = None
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
            f"Could not build quick preview: {message}{suggestion}",
            "bad",
        )

    def _exact_preview_failed(self, request_id: int, message: str) -> None:
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
            f"Quick preview · verified vectors unavailable: {message}{suggestion}",
            "bad",
        )

    def _accept_latest(self) -> None:
        if (
            self._closed
            or self._exact_task is not None
            or self._quick_task is not None
            or self._pending_exact_request is not None
            or self._pending_quick_request is not None
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
        if self._quick_cancel_event is not None:
            self._quick_cancel_event.set()
        if self._exact_cancel_event is not None:
            self._exact_cancel_event.set()
        self._debounce_timer.stop()
        self._pending_quick_request = None
        self._pending_exact_request = None
        self.create_button.setEnabled(False)


    def reject(self) -> None:
        self._accepted_result = None
        self._accepted_options = None
        self._abandon_pending_work()
        super().reject()


__all__ = ["RasterVectorizationDialog"]
