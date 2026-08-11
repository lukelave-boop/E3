from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from ..templates import MAX_GRID_OBJECTS
from .controls import MeasurementSpinBox
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


_MAX_PREVIEW_CUTS = 5_000
WORK_AREA_TOLERANCE_MM = 1e-6
_ACTION_TEXT_RESERVE_PX = 8
_DEFAULT_DIALOG_WIDTH = 940
_DEFAULT_DIALOG_HEIGHT = 640
_MIN_DIALOG_WIDTH = 588
_MIN_DIALOG_HEIGHT = 382
_SCREEN_WIDTH_RESERVE = 32
_SCREEN_HEIGHT_RESERVE = 48


def _value(source: Mapping[str, Any] | object, *names: str, default: Any) -> Any:
    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source[name]
        if not isinstance(source, Mapping) and hasattr(source, name):
            return getattr(source, name)
    return default


def _spin(
    minimum: float,
    maximum: float,
    *,
    value: float,
    suffix: str = " mm",
) -> QtWidgets.QDoubleSpinBox:
    widget = MeasurementSpinBox()
    widget.setRange(minimum, maximum)
    widget.setDecimals(3)
    widget.setSingleStep(0.1)
    widget.setValue(value)
    widget.setSuffix(suffix)
    widget.setKeyboardTracking(False)
    return widget


def _representative_indices(count: int) -> list[int]:
    return sorted(
        index
        for index in {0, 1, count // 2, count - 2, count - 1}
        if 0 <= index < count
    )


class _ActionButton(QtWidgets.QPushButton):
    """Push button whose layout hint leaves a small text-painting reserve."""

    def __init__(
        self,
        full_text: str,
        compact_text: str | None = None,
        *,
        tool_tip: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(full_text, parent)
        self.full_text = full_text
        self.compact_text = compact_text or full_text
        self._compact = False
        if tool_tip:
            self.setToolTip(tool_tip)

    def sizeHint(self) -> QtCore.QSize:
        hint = super().sizeHint()
        hint.setWidth(hint.width() + _ACTION_TEXT_RESERVE_PX)
        return hint

    def minimumSizeHint(self) -> QtCore.QSize:
        return self.sizeHint()

    def set_labels(self, full_text: str, compact_text: str | None = None) -> None:
        self.full_text = full_text
        self.compact_text = compact_text or full_text
        self.setText(self.compact_text if self._compact else self.full_text)

    def use_compact_text(self, compact: bool) -> None:
        self._compact = compact
        self.setText(self.compact_text if compact else self.full_text)


class _WrappedStatusLabel(QtWidgets.QLabel):
    """Wrapped status card that cannot be compressed below its text height."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWordWrap(True)

    def sync_minimum_height(self, width: int | None = None) -> None:
        available_width = max(1, self.width() if width is None else width)
        required_height = self.heightForWidth(available_width)
        if required_height >= 0 and required_height != self.minimumHeight():
            self.setMinimumHeight(required_height)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_minimum_height(event.size().width())


class GridTemplatePreview(QtWidgets.QGraphicsView):
    """Small, noninteractive preview for a regular rounded-rectangle grid."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setObjectName("templateDesignerPreview")
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QtGui.QColor("#0A1015"))
        self.setInteractive(False)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.TextAntialiasing
        )
        self._content_rect = QtCore.QRectF(-1.0, -1.0, 2.0, 2.0)

    def set_spec(self, spec: Mapping[str, Any]) -> None:
        scene = self.scene()
        scene.clear()

        rows = max(1, int(spec["rows"]))
        columns = max(1, int(spec["columns"]))
        width = max(0.001, float(spec["width_mm"]))
        height = max(0.001, float(spec["height_mm"]))
        radius = min(
            max(0.0, float(spec["corner_radius_mm"])),
            width / 2.0,
            height / 2.0,
        )
        pitch_x = max(width, float(spec["horizontal_pitch_mm"]))
        pitch_y = max(height, float(spec["vertical_pitch_mm"]))
        footprint_width = float(spec["footprint_width_mm"])
        footprint_height = float(spec["footprint_height_mm"])

        outline = QtGui.QPainterPath()
        total = rows * columns
        if total <= _MAX_PREVIEW_CUTS:
            row_indices = range(rows)
            column_indices = range(columns)
        else:
            # A very large grid remains editable without constructing thousands
            # of graphics items. Edge and representative interior cuts preserve
            # the footprint and spacing visually.
            row_indices = _representative_indices(rows)
            column_indices = _representative_indices(columns)
        for row in row_indices:
            center_y = (row - (rows - 1) / 2.0) * pitch_y
            for column in column_indices:
                center_x = (column - (columns - 1) / 2.0) * pitch_x
                outline.addRoundedRect(
                    QtCore.QRectF(
                        center_x - width / 2.0,
                        center_y - height / 2.0,
                        width,
                        height,
                    ),
                    radius,
                    radius,
                )
        cut_item = scene.addPath(outline)
        cut_pen = QtGui.QPen(QtGui.QColor("#45D7FF"))
        cut_pen.setWidthF(max(0.15, min(width, height) * 0.018))
        cut_pen.setCosmetic(True)
        cut_item.setPen(cut_pen)
        fill = QtGui.QColor("#45D7FF")
        fill.setAlpha(18)
        cut_item.setBrush(fill)

        footprint = QtCore.QRectF(
            -footprint_width / 2.0,
            -footprint_height / 2.0,
            footprint_width,
            footprint_height,
        )
        bounds_item = scene.addRect(footprint)
        bounds_pen = QtGui.QPen(QtGui.QColor("#52636E"))
        bounds_pen.setCosmetic(True)
        bounds_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        bounds_item.setPen(bounds_pen)
        bounds_item.setZValue(-1.0)

        extent = max(footprint_width, footprint_height, 1.0)
        padding = max(3.0, extent * 0.10)
        self._content_rect = footprint.adjusted(-padding, -padding, padding, padding)
        scene.setSceneRect(self._content_rect)
        self._fit_content()

    def _fit_content(self) -> None:
        if self.viewport().width() > 1 and self.viewport().height() > 1:
            self.fitInView(
                self._content_rect,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_content()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        event.ignore()


class GridTemplateDesignerDialog(QtWidgets.QDialog):
    """Transactional editor for a reusable, regular cutting-template grid."""

    saveRequested = QtCore.Signal(dict)
    addToProjectRequested = QtCore.Signal(dict)

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        initial_spec: Mapping[str, Any] | object | None = None,
        editing: bool = False,
        max_width_mm: float | None = None,
        max_height_mm: float | None = None,
        submit_handler: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Grid cutting template designer")
        self.setModal(True)
        self.setSizeGripEnabled(True)
        minimum_size, initial_size = self._screen_limited_sizes()
        self.setMinimumSize(minimum_size)
        self.resize(initial_size)

        self._updating = False
        self._spacing_mode = "gap"
        self._editing = bool(editing)
        self._selected_action: str | None = None
        self._source_extras: dict[str, Any] = {}
        self._max_width_mm = self._optional_positive(
            max_width_mm, "max_width_mm"
        )
        self._max_height_mm = self._optional_positive(
            max_height_mm, "max_height_mm"
        )
        self._submit_handler = submit_handler

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        heading = QtWidgets.QLabel("Design a label-sheet cutting grid")
        heading.setObjectName("panelHeading")
        root.addWidget(heading)
        intro = QtWidgets.QLabel(
            "Set cut size and spacing. The preview updates live; choose an action "
            "to change the project."
        )
        intro.setObjectName("mutedLabel")
        intro.setWordWrap(True)
        root.addWidget(intro)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        form_page = QtWidgets.QWidget()
        form_page.setObjectName("controlPanel")
        form_layout = QtWidgets.QVBoxLayout(form_page)
        form_layout.setContentsMargins(4, 4, 10, 4)
        form_layout.setSpacing(10)

        identity_group = QtWidgets.QGroupBox("Template details")
        identity_form = QtWidgets.QFormLayout(identity_group)
        identity_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        identity_form.setRowWrapPolicy(
            QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows
        )
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("Example: 3 × 8 rounded labels")
        self.description_edit = QtWidgets.QPlainTextEdit()
        self.description_edit.setPlaceholderText(
            "Optional notes such as stock name, product, or orientation"
        )
        self.description_edit.setMinimumHeight(64)
        self.description_edit.setMaximumHeight(96)
        identity_form.addRow("Name", self.name_edit)
        identity_form.addRow("Description", self.description_edit)
        form_layout.addWidget(identity_group)

        shape_group = QtWidgets.QGroupBox("Cut shape")
        shape_form = QtWidgets.QFormLayout(shape_group)
        shape_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        shape_form.setRowWrapPolicy(
            QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows
        )
        self.width_spin = _spin(0.001, 10000.0, value=50.0)
        self.height_spin = _spin(0.001, 10000.0, value=25.0)
        self.corner_radius_spin = _spin(0.0, 12.5, value=3.0)
        self.corner_radius_spin.setToolTip(
            "0 mm makes square corners; the maximum makes pill-shaped ends."
        )
        shape_form.addRow("Cut width", self.width_spin)
        shape_form.addRow("Cut height", self.height_spin)
        shape_form.addRow("Corner radius", self.corner_radius_spin)
        form_layout.addWidget(shape_group)

        grid_group = QtWidgets.QGroupBox("Grid layout")
        grid_form = QtWidgets.QFormLayout(grid_group)
        grid_form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        grid_form.setRowWrapPolicy(
            QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows
        )
        self.columns_spin = QtWidgets.QSpinBox()
        self.columns_spin.setRange(1, 999)
        self.columns_spin.setValue(3)
        self.columns_spin.setKeyboardTracking(False)
        self.rows_spin = QtWidgets.QSpinBox()
        self.rows_spin.setRange(1, 999)
        self.rows_spin.setValue(4)
        self.rows_spin.setKeyboardTracking(False)
        self.spacing_mode_combo = QtWidgets.QComboBox()
        self.spacing_mode_combo.addItem("Edge gap", "gap")
        self.spacing_mode_combo.addItem("Center pitch", "pitch")
        self.spacing_mode_combo.setToolTip(
            "Gap measures clear space between cuts. Pitch measures from one "
            "cut center to the next."
        )
        self.horizontal_spacing_spin = _spin(0.0, 10000.0, value=3.0)
        self.vertical_spacing_spin = _spin(0.0, 10000.0, value=3.0)
        self.horizontal_spacing_label = QtWidgets.QLabel("Horizontal gap")
        self.vertical_spacing_label = QtWidgets.QLabel("Vertical gap")
        grid_form.addRow("Columns", self.columns_spin)
        grid_form.addRow("Rows", self.rows_spin)
        grid_form.addRow("Spacing mode", self.spacing_mode_combo)
        grid_form.addRow(
            self.horizontal_spacing_label,
            self.horizontal_spacing_spin,
        )
        grid_form.addRow(
            self.vertical_spacing_label,
            self.vertical_spacing_spin,
        )
        form_layout.addWidget(grid_group)

        self.validation_label = QtWidgets.QLabel()
        self.validation_label.setObjectName("warningLabel")
        self.validation_label.setWordWrap(True)
        self.validation_label.hide()
        form_layout.addWidget(self.validation_label)
        form_layout.addStretch(1)

        form_scroll = QtWidgets.QScrollArea()
        form_scroll.setObjectName("inspectorScroll")
        form_scroll.setProperty("wheelScrollContainer", True)
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        form_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        form_scroll.setWidget(form_page)
        splitter.addWidget(form_scroll)

        preview_page = QtWidgets.QWidget()
        preview_layout = QtWidgets.QVBoxLayout(preview_page)
        preview_layout.setContentsMargins(10, 4, 4, 4)
        preview_layout.setSpacing(10)
        preview_heading = QtWidgets.QLabel("Live preview")
        preview_heading.setObjectName("panelHeading")
        preview_layout.addWidget(preview_heading)
        self.preview = GridTemplatePreview()
        # The preview expands in a normal window, but a smaller minimum lets
        # the same two-column workspace fit high-DPI screens whose logical
        # desktop is shorter than the 940 x 640 design size.
        self.preview.setMinimumSize(260, 40)
        preview_layout.addWidget(self.preview, 1)
        self.footprint_status = _WrappedStatusLabel()
        self.footprint_status.setObjectName("statusCard")
        self.footprint_status.setToolTip(
            "Cut count and overall footprint, center pitch, and edge gap."
        )
        preview_layout.addWidget(self.footprint_status)
        splitter.addWidget(preview_page)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 580])

        actions = QtWidgets.QHBoxLayout()
        # Align the group itself instead of inserting a stretch item. A stretch
        # also consumes an inter-item spacing slot, which needlessly compresses
        # the final label in a narrow but otherwise viable window.
        actions.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        actions.setSpacing(6)
        self.cancel_button = _ActionButton("Cancel")
        self.add_project_button = _ActionButton(
            "Add to project",
            "Add",
            tool_tip="Add this grid as editable objects in the current project.",
        )
        self.save_button = _ActionButton(
            "Update template" if self._editing else "Save template",
            "Update" if self._editing else "Save",
            tool_tip="Save this grid in the reusable cutting-template library.",
        )
        self.save_button.setObjectName("primaryActionButton")
        self.save_button.setDefault(True)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.add_project_button)
        actions.addWidget(self.save_button)
        self._actions_layout = actions
        self._action_buttons = (
            self.cancel_button,
            self.add_project_button,
            self.save_button,
        )
        root.addLayout(actions)

        self.name_edit.textChanged.connect(self._refresh)
        self.description_edit.textChanged.connect(self._refresh)
        self.columns_spin.valueChanged.connect(self._refresh)
        self.rows_spin.valueChanged.connect(self._refresh)
        self.width_spin.valueChanged.connect(self._dimensions_changed)
        self.height_spin.valueChanged.connect(self._dimensions_changed)
        self.corner_radius_spin.valueChanged.connect(self._refresh)
        self.spacing_mode_combo.currentIndexChanged.connect(
            self._spacing_mode_changed
        )
        self.horizontal_spacing_spin.valueChanged.connect(self._refresh)
        self.vertical_spacing_spin.valueChanged.connect(self._refresh)
        self.cancel_button.clicked.connect(self.reject)
        self.save_button.clicked.connect(lambda: self._finish("save"))
        self.add_project_button.clicked.connect(lambda: self._finish("project"))

        self.set_editing(editing)
        if initial_spec is not None:
            self.set_spec(initial_spec)
        else:
            self._sync_limits()
            self._refresh()

    @staticmethod
    def _optional_positive(value: float | None, name: str) -> float | None:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number) or number <= 0.0:
            raise ValueError(f"{name} must be a positive finite number")
        return number

    def _available_screen_size(self) -> QtCore.QSize:
        parent = self.parentWidget()
        screen = parent.screen() if parent is not None else self.screen()
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return QtCore.QSize(
                _DEFAULT_DIALOG_WIDTH + _SCREEN_WIDTH_RESERVE,
                _DEFAULT_DIALOG_HEIGHT + _SCREEN_HEIGHT_RESERVE,
            )
        return screen.availableGeometry().size()

    def _screen_limited_sizes(self) -> tuple[QtCore.QSize, QtCore.QSize]:
        available = self._available_screen_size()
        usable_width = max(
            _MIN_DIALOG_WIDTH,
            available.width() - _SCREEN_WIDTH_RESERVE,
        )
        usable_height = max(
            _MIN_DIALOG_HEIGHT,
            available.height() - _SCREEN_HEIGHT_RESERVE,
        )
        initial = QtCore.QSize(
            min(_DEFAULT_DIALOG_WIDTH, available.width(), usable_width),
            min(_DEFAULT_DIALOG_HEIGHT, available.height(), usable_height),
        )
        minimum = QtCore.QSize(
            min(_MIN_DIALOG_WIDTH, initial.width()),
            min(_MIN_DIALOG_HEIGHT, initial.height()),
        )
        return minimum, initial

    def set_editing(self, editing: bool) -> None:
        self._editing = bool(editing)
        self.save_button.set_labels(
            "Update template" if self._editing else "Save template",
            "Update" if self._editing else "Save",
        )
        self._update_action_labels()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_action_buttons"):
            QtCore.QTimer.singleShot(0, self._update_action_labels)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._update_action_labels)

    def _update_action_labels(self) -> None:
        for button in self._action_buttons:
            button.use_compact_text(False)
        margins = self.layout().contentsMargins()
        available_width = max(
            0,
            self.contentsRect().width() - margins.left() - margins.right(),
        )
        required_width = sum(
            button.sizeHint().width() for button in self._action_buttons
        ) + self._actions_layout.spacing() * (len(self._action_buttons) - 1)
        if required_width > available_width:
            for button in self._action_buttons:
                button.use_compact_text(True)

    def selected_action(self) -> str | None:
        return self._selected_action

    def set_spec(self, source: Mapping[str, Any] | object) -> None:
        if isinstance(source, Mapping):
            excluded = {
                "name",
                "description",
                "rows",
                "columns",
                "width_mm",
                "height_mm",
                "corner_radius_mm",
                "spacing_mode",
                "horizontal_spacing_mm",
                "vertical_spacing_mm",
                "horizontal_gap_mm",
                "vertical_gap_mm",
                "horizontal_pitch_mm",
                "vertical_pitch_mm",
                "column_pitch_mm",
                "row_pitch_mm",
                "footprint_width_mm",
                "footprint_height_mm",
                "cut_count",
            }
            self._source_extras = {
                str(key): value for key, value in source.items() if key not in excluded
            }
        else:
            self._source_extras = {}

        mode_value = str(
            _value(source, "spacing_mode", default="")
        ).strip().lower()
        if mode_value not in {"gap", "pitch"}:
            has_pitch = (
                _value(
                    source,
                    "horizontal_pitch_mm",
                    "column_pitch_mm",
                    default=None,
                )
                is not None
            )
            mode_value = "pitch" if has_pitch else "gap"

        self._updating = True
        try:
            self.name_edit.setText(
                str(_value(source, "name", default=self.name_edit.text()))
            )
            self.name_edit.setCursorPosition(0)
            self.description_edit.setPlainText(
                str(
                    _value(
                        source,
                        "description",
                        default=self.description_edit.toPlainText(),
                    )
                )
            )
            self.rows_spin.setValue(
                int(_value(source, "rows", default=self.rows_spin.value()))
            )
            self.columns_spin.setValue(
                int(_value(source, "columns", default=self.columns_spin.value()))
            )
            self.width_spin.setValue(
                float(
                    _value(
                        source,
                        "width_mm",
                        "label_width_mm",
                        "cell_width_mm",
                        default=self.width_spin.value(),
                    )
                )
            )
            self.height_spin.setValue(
                float(
                    _value(
                        source,
                        "height_mm",
                        "label_height_mm",
                        "cell_height_mm",
                        default=self.height_spin.value(),
                    )
                )
            )
            self._spacing_mode = mode_value
            self.spacing_mode_combo.setCurrentIndex(
                max(0, self.spacing_mode_combo.findData(mode_value))
            )
            self._sync_limits()
            if mode_value == "pitch":
                horizontal = _value(
                    source,
                    "horizontal_spacing_mm",
                    "horizontal_pitch_mm",
                    "column_pitch_mm",
                    default=self.width_spin.value()
                    + float(_value(source, "horizontal_gap_mm", default=3.0)),
                )
                vertical = _value(
                    source,
                    "vertical_spacing_mm",
                    "vertical_pitch_mm",
                    "row_pitch_mm",
                    default=self.height_spin.value()
                    + float(_value(source, "vertical_gap_mm", default=3.0)),
                )
            else:
                horizontal = _value(
                    source,
                    "horizontal_spacing_mm",
                    "horizontal_gap_mm",
                    default=max(
                        0.0,
                        float(
                            _value(
                                source,
                                "horizontal_pitch_mm",
                                "column_pitch_mm",
                                default=self.width_spin.value() + 3.0,
                            )
                        )
                        - self.width_spin.value(),
                    ),
                )
                vertical = _value(
                    source,
                    "vertical_spacing_mm",
                    "vertical_gap_mm",
                    default=max(
                        0.0,
                        float(
                            _value(
                                source,
                                "vertical_pitch_mm",
                                "row_pitch_mm",
                                default=self.height_spin.value() + 3.0,
                            )
                        )
                        - self.height_spin.value(),
                    ),
                )
            self.horizontal_spacing_spin.setValue(float(horizontal))
            self.vertical_spacing_spin.setValue(float(vertical))
            radius = float(
                _value(
                    source,
                    "corner_radius_mm",
                    default=self.corner_radius_spin.value(),
                )
            )
            self.corner_radius_spin.setValue(
                min(radius, self.corner_radius_spin.maximum())
            )
        finally:
            self._updating = False
        self._refresh()

    def spec(self) -> dict[str, Any]:
        width = self.width_spin.value()
        height = self.height_spin.value()
        horizontal = self.horizontal_spacing_spin.value()
        vertical = self.vertical_spacing_spin.value()
        if self._spacing_mode == "pitch":
            pitch_x = horizontal
            pitch_y = vertical
            gap_x = pitch_x - width
            gap_y = pitch_y - height
        else:
            gap_x = horizontal
            gap_y = vertical
            pitch_x = width + gap_x
            pitch_y = height + gap_y
        rows = self.rows_spin.value()
        columns = self.columns_spin.value()
        payload = dict(self._source_extras)
        payload.update(
            {
                "name": self.name_edit.text().strip(),
                "description": self.description_edit.toPlainText().strip(),
                "rows": rows,
                "columns": columns,
                "width_mm": width,
                "height_mm": height,
                "corner_radius_mm": self.corner_radius_spin.value(),
                "spacing_mode": self._spacing_mode,
                "horizontal_spacing_mm": horizontal,
                "vertical_spacing_mm": vertical,
                "horizontal_gap_mm": gap_x,
                "vertical_gap_mm": gap_y,
                "horizontal_pitch_mm": pitch_x,
                "vertical_pitch_mm": pitch_y,
                "footprint_width_mm": width + (columns - 1) * pitch_x,
                "footprint_height_mm": height + (rows - 1) * pitch_y,
                "cut_count": rows * columns,
            }
        )
        return payload

    def _sync_limits(self) -> None:
        radius_max = min(self.width_spin.value(), self.height_spin.value()) / 2.0
        self.corner_radius_spin.setMaximum(radius_max)
        if self.corner_radius_spin.value() > radius_max:
            self.corner_radius_spin.setValue(radius_max)

        if self._spacing_mode == "pitch":
            self.horizontal_spacing_spin.setMinimum(self.width_spin.value())
            self.vertical_spacing_spin.setMinimum(self.height_spin.value())
            self.horizontal_spacing_label.setText("Horizontal pitch")
            self.vertical_spacing_label.setText("Vertical pitch")
        else:
            self.horizontal_spacing_spin.setMinimum(0.0)
            self.vertical_spacing_spin.setMinimum(0.0)
            self.horizontal_spacing_label.setText("Horizontal gap")
            self.vertical_spacing_label.setText("Vertical gap")

    def _dimensions_changed(self, *args: Any) -> None:
        del args
        if self._updating:
            return
        self._sync_limits()
        self._refresh()

    def _spacing_mode_changed(self, index: int) -> None:
        if self._updating:
            return
        new_mode = str(self.spacing_mode_combo.itemData(index))
        if new_mode == self._spacing_mode:
            return
        width = self.width_spin.value()
        height = self.height_spin.value()
        horizontal = self.horizontal_spacing_spin.value()
        vertical = self.vertical_spacing_spin.value()
        self._updating = True
        try:
            self._spacing_mode = new_mode
            self._sync_limits()
            if new_mode == "pitch":
                self.horizontal_spacing_spin.setValue(width + horizontal)
                self.vertical_spacing_spin.setValue(height + vertical)
            else:
                self.horizontal_spacing_spin.setValue(max(0.0, horizontal - width))
                self.vertical_spacing_spin.setValue(max(0.0, vertical - height))
        finally:
            self._updating = False
        self._refresh()

    def _validation(self, spec: Mapping[str, Any]) -> tuple[bool, str]:
        if not str(spec["name"]).strip():
            return False, "Enter a clear template name before saving or adding the grid."
        if int(spec["cut_count"]) > MAX_GRID_OBJECTS:
            return (
                False,
                f"This grid has {spec['cut_count']} cuts; the maximum is "
                f"{MAX_GRID_OBJECTS}.",
            )
        width = float(spec["footprint_width_mm"])
        height = float(spec["footprint_height_mm"])
        problems: list[str] = []
        if (
            self._max_width_mm is not None
            and width > self._max_width_mm + WORK_AREA_TOLERANCE_MM
        ):
            problems.append(
                f"width {width:.2f} mm exceeds the {self._max_width_mm:.2f} mm work area"
            )
        if (
            self._max_height_mm is not None
            and height > self._max_height_mm + WORK_AREA_TOLERANCE_MM
        ):
            problems.append(
                f"height {height:.2f} mm exceeds the {self._max_height_mm:.2f} mm work area"
            )
        if problems:
            return False, "This grid does not fit: " + "; ".join(problems) + "."
        if int(spec["cut_count"]) < 3:
            return (
                True,
                "Manual placement only: automatic camera matching requires at least 3 cuts.",
            )
        return True, ""

    def _refresh(self, *args: Any) -> None:
        del args
        if self._updating:
            return
        spec = self.spec()
        self.preview.set_spec(spec)
        self.footprint_status.setText(
            f"{spec['cut_count']} cuts · "
            f"{spec['footprint_width_mm']:g} × "
            f"{spec['footprint_height_mm']:g} mm\n"
            f"Pitch {spec['horizontal_pitch_mm']:g} × "
            f"{spec['vertical_pitch_mm']:g} · "
            f"Gap {spec['horizontal_gap_mm']:g} × "
            f"{spec['vertical_gap_mm']:g} mm"
        )
        self.footprint_status.sync_minimum_height()
        valid, message = self._validation(spec)
        self.validation_label.setText(message)
        self.validation_label.setVisible(bool(message))
        self.save_button.setEnabled(valid)
        self.add_project_button.setEnabled(valid)
        self.horizontal_spacing_spin.setEnabled(self.columns_spin.value() > 1)
        self.vertical_spacing_spin.setEnabled(self.rows_spin.value() > 1)

    def _finish(self, action: str) -> None:
        spec = self.spec()
        valid, message = self._validation(spec)
        if not valid:
            self.validation_label.setText(message)
            self.validation_label.show()
            return
        if self._submit_handler is not None:
            try:
                self._submit_handler(action, dict(spec))
            except Exception as exc:
                self._selected_action = None
                self.validation_label.setText(
                    f"Could not complete that action: {exc}\n"
                    "Your grid values are still here. Correct the problem and try again."
                )
                self.validation_label.show()
                return
        self._selected_action = action
        if action == "save":
            self.saveRequested.emit(spec)
        else:
            self.addToProjectRequested.emit(spec)
        self.accept()

    def reject(self) -> None:
        self._selected_action = None
        super().reject()
