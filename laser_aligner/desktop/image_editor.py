"""Offline, non-destructive engraving image and native-outline editor.

Mouse movement changes only a lightweight overlay. Image processing runs once
per committed gesture; source decoding runs once for the entire dialog. No
controller, planner, or job generator is imported by this workspace.
"""
from __future__ import annotations

import copy
import threading

import numpy as np

from ..project.image_edit import (
    default_edit,
    derive_vector_outline,
    offset_outline,
    outline_from_mask,
    process_image,
    trace_foreground,
    validate_outline,
)
from ..project.path_geometry import (
    NativePathGeometry,
    PathCubicSegment,
    PathLineSegment,
    PathSubpath,
    split_cubic,
)
from ..project.raster_asset import RasterAssetPayload, decode_raster_payload_planes
from .image_processing import grayscale_image, verify_edit_source
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class _TraceSignals(QtCore.QObject):
    finished = QtCore.Signal(int, object, object)


class _TraceWorker(QtCore.QRunnable):
    def __init__(self, generation, gray, options, edited_mask=False):
        super().__init__()
        self.signals = _TraceSignals()
        self.generation = generation
        self.gray = gray
        self.options = options
        self.edited_mask = edited_mask
        self.cancelled = threading.Event()

    def run(self):
        result, error = None, None
        try:
            if self.cancelled.is_set():
                return
            result = (outline_from_mask(self.gray, self.options, cancel_check=self.cancelled.is_set) if self.edited_mask else
                      trace_foreground(self.gray, self.options, cancel_check=self.cancelled.is_set))
        except Exception as exc:
            error = str(exc)
        finally:
            self.signals.finished.emit(self.generation, result, error)


class ImageEditView(QtWidgets.QGraphicsView):
    """Pixel-space view; all persistent points are normalized Y-up coordinates."""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        scene = QtWidgets.QGraphicsScene(self)
        self.setScene(scene)
        self.image_item = scene.addPixmap(QtGui.QPixmap.fromImage(editor.original_image))
        scene.setSceneRect(0, 0, editor.width, editor.height)
        self.setBackgroundBrush(QtGui.QColor("#404449"))
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 300)
        self.tool = "nodes"
        self.points = []
        self.pen_handles = []
        self.cursor_point = None
        self.dragging = False
        self.panning = False
        self.pan_last = None
        self.selected = None
        self.drag_target = None
        self.drag_original = None
        self.drag_origin = None
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def mapped(self, point):
        return QtCore.QPointF((point[0] + .5) * self.editor.width,
                             (.5 - point[1]) * self.editor.height)

    def normalized(self, point, clamp=True):
        x, y = point.x() / self.editor.width - .5, .5 - point.y() / self.editor.height
        return [max(-.5, min(.5, x)), max(-.5, min(.5, y))] if clamp else [x, y]

    def set_tool(self, tool):
        self.cancel_gesture()
        self.tool = tool
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.NoDrag)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor if tool == "pan"
                       else QtCore.Qt.CursorShape.CrossCursor)
        self.viewport().update()

    def cancel_gesture(self):
        if self.drag_original is not None:
            self.editor._settings = self.drag_original
        self.points = []
        self.pen_handles = []
        self.dragging = False
        self.drag_target = None
        self.drag_original = None
        self.viewport().update()

    def clear_node_selection(self):
        self.selected = None
        self.drag_target = None
        self.drag_original = None
        self.drag_origin = None
        self.dragging = False
        self.viewport().update()

    def _valid_handle(self, key):
        native = self.editor._settings.get("outline")
        if native is None or key is None:
            return False
        si, ii, field = key
        if not 0 <= si < len(native["subpaths"]):
            return False
        subpath = native["subpaths"][si]
        if ii == -1:
            return field == "start"
        if not 0 <= ii < len(subpath["segments"]):
            return False
        return field in {"to", "control_1", "control_2"} and field in subpath["segments"][ii]

    def _current_selection(self):
        if not self._valid_handle(self.selected):
            self.clear_node_selection()
            self.editor.status.setText("Select a node on the current outline first.")
            return None
        return self.selected

    def _painter_path(self, native):
        path = QtGui.QPainterPath()
        path.setFillRule(QtCore.Qt.FillRule.OddEvenFill)
        if not native:
            return path
        for subpath in native["subpaths"]:
            path.moveTo(self.mapped(subpath["start"]))
            for segment in subpath["segments"]:
                if segment["type"] == "line":
                    path.lineTo(self.mapped(segment["to"]))
                else:
                    path.cubicTo(self.mapped(segment["control_1"]),
                                 self.mapped(segment["control_2"]), self.mapped(segment["to"]))
            if subpath["closed"]:
                path.closeSubpath()
        return path

    def _handles(self):
        native = self.editor._settings.get("outline")
        if not native:
            return
        for si, sub in enumerate(native["subpaths"]):
            yield (si, -1, "start"), sub["start"], None
            for ii, seg in enumerate(sub["segments"]):
                # Explicit closing endpoints share the first node.
                if not (ii == len(sub["segments"]) - 1 and seg["to"] == sub["start"]):
                    yield (si, ii, "to"), seg["to"], None
                if seg["type"] == "cubic":
                    start = sub["start"] if ii == 0 else sub["segments"][ii - 1]["to"]
                    yield (si, ii, "control_1"), seg["control_1"], start
                    yield (si, ii, "control_2"), seg["control_2"], seg["to"]

    def _hit(self, point):
        radius = 9 / max(.001, self.transform().m11())
        matches = []
        for key, value, anchor in self._handles():
            distance = QtCore.QLineF(point, self.mapped(value)).length()
            if distance <= radius:
                matches.append((distance, 0 if anchor is None else 1, key))
        return min(matches)[2] if matches else None

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if self.editor.before.isChecked():
            return
        painter.save()
        scale = max(.001, self.transform().m11())
        for native, color, dashed in (
            (self.editor._settings.get("outline"), "#00e6ff", False),
            (self.editor.candidate.to_dict() if self.editor.candidate else None, "#ffcc40", True),
            (self.editor._offset_preview.to_dict() if self.editor._offset_preview else None, "#57f269", True),
        ):
            pen = QtGui.QPen(QtGui.QColor(color), 1.8 / scale)
            if dashed:
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawPath(self._painter_path(native))
        if self.tool == "nodes":
            for key, value, anchor in self._handles():
                pt = self.mapped(value)
                painter.setPen(QtGui.QPen(QtGui.QColor("#3b6670"), 1 / scale))
                if anchor is not None:
                    painter.drawLine(self.mapped(anchor), pt)
                painter.setBrush(QtGui.QColor("#ff7b39" if key == self.selected else
                                            "#ffffff" if anchor is None else "#8fe5f5"))
                radius = (4 if anchor is None else 3) / scale
                painter.drawEllipse(pt, radius, radius)
        if self.points:
            pts = [self.mapped(point) for point in self.points]
            path = QtGui.QPainterPath(pts[0])
            if self.tool in {"rectangle", "ellipse"} and len(pts) > 1:
                bounds = QtCore.QRectF(pts[0], pts[-1]).normalized()
                path.addRect(bounds) if self.tool == "rectangle" else path.addEllipse(bounds)
            elif self.tool == "pen":
                for index in range(1, len(self.points)):
                    start, end = self.points[index - 1], self.points[index]
                    a, b = self.pen_handles[index - 1], self.pen_handles[index]
                    path.cubicTo(self.mapped([start[i] + a[i] for i in range(2)]),
                                 self.mapped([end[i] - b[i] for i in range(2)]), self.mapped(end))
            else:
                for point in pts[1:]:
                    path.lineTo(point)
            brush = self.tool in {"exclude", "restore"}
            color = QtGui.QColor("#30e990" if self.tool == "restore" else "#fa5968")
            color.setAlpha(130)
            pen = QtGui.QPen(color, self.editor.brush_pixels.value() if brush else 2 / scale)
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            if brush and len(pts) == 1:
                painter.drawPoint(pts[0])
            if self.tool == "pen":
                painter.setPen(QtGui.QPen(QtGui.QColor("#ff8b33"), 1 / scale))
                for point, handle in zip(self.points, self.pen_handles, strict=True):
                    before = self.mapped([point[i] - handle[i] for i in range(2)])
                    after = self.mapped([point[i] + handle[i] for i in range(2)])
                    painter.drawLine(before, after)
                    painter.drawEllipse(after, 3 / scale, 3 / scale)
        if self.cursor_point is not None and self.tool in {"exclude", "restore"}:
            radius = self.editor.brush_pixels.value() / 2
            painter.setPen(QtGui.QPen(QtGui.QColor("white"), 1 / scale))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawEllipse(self.cursor_point, radius, radius)
            painter.setPen(QtGui.QPen(QtGui.QColor("black"), 1 / scale, QtCore.Qt.PenStyle.DashLine))
            painter.drawEllipse(self.cursor_point, radius, radius)
        painter.restore()

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else .8
        if .02 <= self.transform().m11() * factor <= 64:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        self.setFocus()
        if event.button() == QtCore.Qt.MouseButton.MiddleButton or self.tool == "pan":
            self.panning = True
            self.pan_last = event.position()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == QtCore.Qt.MouseButton.RightButton:
            self.finish_pen()
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton or self.editor.before.isChecked():
            return
        self.editor._invalidate_trace()
        if self.tool == "nodes":
            self.selected = self._hit(point)
            self.drag_target = self.selected
            if self.drag_target:
                self.dragging = True
                self.drag_original = copy.deepcopy(self.editor._settings)
                self.drag_origin = self.normalized(point)
        elif self.tool in {"polygon", "pen"}:
            if len(self.points) >= 3 and QtCore.QLineF(point, self.mapped(self.points[0])).length() < 9 / self.transform().m11():
                self.finish_pen()
                return
            self.points.append(self.normalized(point))
            self.pen_handles.append([0., 0.])
            self.dragging = True
        else:
            self.points = [self.normalized(point)]
            self.dragging = True
        self.viewport().update()

    def mouseMoveEvent(self, event):
        point = self.mapToScene(event.position().toPoint())
        self.cursor_point = point
        if self.panning:
            delta = event.position() - self.pan_last
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(delta.y()))
            self.pan_last = event.position()
        elif self.dragging:
            value = self.normalized(point)
            if self.drag_target:
                self._move_node(self.drag_target, value)
            elif self.tool in {"rectangle", "ellipse"}:
                self.points = [self.points[0], value]
            elif self.tool == "pen":
                self.pen_handles[-1] = [value[i] - self.points[-1][i] for i in range(2)]
            elif self.tool not in {"polygon"}:
                last = self.mapped(self.points[-1])
                if QtCore.QLineF(last, point).length() >= 1.5 / self.transform().m11():
                    self.points.append(value)
        self.viewport().update()

    def _move_node(self, key, value):
        if not self._valid_handle(key):
            self.clear_node_selection()
            return
        si, ii, field = key
        sub = self.editor._settings["outline"]["subpaths"][si]
        target = sub if ii == -1 else sub["segments"][ii]
        old = target[field]
        delta = [value[axis] - old[axis] for axis in range(2)]
        target[field] = value
        if field.startswith("control"):
            return
        segments = sub["segments"]
        if ii >= 0 and segments[ii]["type"] == "cubic":
            segments[ii]["control_2"] = [segments[ii]["control_2"][i] + delta[i] for i in range(2)]
        following = ii + 1
        if following < len(segments) and segments[following]["type"] == "cubic":
            segments[following]["control_1"] = [segments[following]["control_1"][i] + delta[i] for i in range(2)]
        if ii == -1 and segments[-1]["to"] == old:
            segments[-1]["to"] = value[:]
            if segments[-1]["type"] == "cubic":
                segments[-1]["control_2"] = [segments[-1]["control_2"][i] + delta[i] for i in range(2)]

    def mouseReleaseEvent(self, event):
        if self.panning:
            self.panning = False
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor if self.tool == "pan"
                           else QtCore.Qt.CursorShape.CrossCursor)
            return
        if not self.dragging:
            return
        self.dragging = False
        if self.drag_target:
            before, current = self.drag_original, copy.deepcopy(self.editor._settings)
            self.drag_original = None
            self.drag_target = None
            self.editor._settings = before
            self.editor.commit(current, "Node edited")
        elif self.tool not in {"polygon", "pen"}:
            minimum = 3 if self.tool == "lasso" else 2 if self.tool in {"rectangle", "ellipse"} else 1
            if len(self.points) >= minimum:
                region = {"operation": "subtract" if self.tool == "exclude" else
                          "restore" if self.tool == "restore" else self.editor.selection_operation.currentData(),
                          "kind": "brush" if self.tool in {"exclude", "restore"} else
                          "polygon" if self.tool == "lasso" else self.tool,
                          "points": copy.deepcopy(self.points),
                          "radius": self.editor.brush_pixels.value() / (2 * min(self.editor.width, self.editor.height))}
                self.editor.add_region(region)
            self.points = []
        self.viewport().update()

    def finish_pen(self):
        if self.tool not in {"polygon", "pen"} or len(self.points) < 3:
            return
        points = [tuple(value) for value in self.points]
        segments = []
        for index in range(1, len(points) + 1):
            previous, current = (index - 1) % len(points), index % len(points)
            if self.tool == "pen":
                start, end = points[previous], points[current]
                a, b = self.pen_handles[previous], self.pen_handles[current]
                segments.append(PathCubicSegment(tuple(start[i] + a[i] for i in range(2)),
                                                 tuple(end[i] - b[i] for i in range(2)), end))
            elif index < len(points):
                segments.append(PathLineSegment(points[current]))
        subpath = PathSubpath(points[0], tuple(segments), closed=True)
        self.editor.add_subpath(subpath)
        self.points = []
        self.pen_handles = []
        self.dragging = False
        self.viewport().update()

    def delete_node(self):
        selected = self._current_selection()
        if selected is None:
            return
        si, ii, field = selected
        if field.startswith("control"):
            self.editor.status.setText("Select an anchor node to delete it; drag handles to change the curve.")
            return
        state = self.editor.settings
        sub = state["outline"]["subpaths"][si]
        segments = sub["segments"]
        explicit_close = segments[-1]["to"] == sub["start"]
        anchors = len(segments) if explicit_close else len(segments) + 1
        if anchors <= 3:
            self.editor.status.setText("A closed contour needs at least three anchors. Use Remove contour to remove it.")
            return
        if ii == -1:
            sub["start"] = segments[0]["to"][:]
            segments.pop(0)
            if explicit_close:
                segments[-1] = {"type": "line", "to": sub["start"][:]}
        else:
            segments.pop(ii)
        self.selected = None
        self.editor.commit(state, "Node deleted; inspect the changed contour")

    def add_node(self):
        selected = self._current_selection()
        if selected is None:
            self.editor.status.setText("Select an anchor first. Add node splits its following segment without changing the curve.")
            return
        si, ii, field = selected
        if field.startswith("control"):
            return
        state = self.editor.settings
        sub = state["outline"]["subpaths"][si]
        segments = sub["segments"]
        start = sub["start"] if ii == -1 else segments[ii]["to"]
        following = ii + 1
        if following == len(segments):
            target = sub["start"]
            segments.append({"type": "line", "to": [(start[i] + target[i]) / 2 for i in range(2)]})
        elif segments[following]["type"] == "cubic":
            native = NativePathGeometry.from_dict(state["outline"])
            first, second = split_cubic(tuple(start), native.subpaths[si].segments[following], .5)
            segments[following:following + 1] = [first.to_dict(), second.to_dict()]
        else:
            target = segments[following]["to"]
            segments.insert(following, {"type": "line", "to": [(start[i] + target[i]) / 2 for i in range(2)]})
        self.editor.commit(state, "Node added")

    def remove_contour(self):
        selected = self._current_selection()
        if selected is None:
            self.editor.status.setText("Select a node on the contour to remove.")
            return
        state = self.editor.settings
        state["outline"]["subpaths"].pop(selected[0])
        if not state["outline"]["subpaths"]:
            state["outline"] = None
        self.selected = None
        self.editor.commit(state, "Contour removed")

    def keyPressEvent(self, event):
        if event.key() in {QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter}:
            self.finish_pen()
        elif event.key() == QtCore.Qt.Key.Key_Escape:
            self.cancel_gesture()
        elif event.key() in {QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace}:
            self.delete_node()
        else:
            super().keyPressEvent(event)


class ImageEditorDialog(QtWidgets.QDialog):
    """Return a draft image_edit snapshot; caller commits it atomically to history."""

    def __init__(self, payload: RasterAssetPayload, geometry: dict, width_mm: float,
                 height_mm: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Engraving image editor")
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.resize(1180, 820)
        self.gray, self.source_allowed = decode_raster_payload_planes(payload)
        self.height, self.width = self.gray.shape
        self.original_image = grayscale_image(self.gray)
        self.after_image = self.original_image
        self.after_gray = self.gray
        self.allowed = self.source_allowed.copy()
        self.geometry = copy.deepcopy(geometry)
        self.width_mm, self.height_mm = float(width_mm), float(height_mm)
        self._default = default_edit(payload.identity.sha256, [self.width, self.height])
        self._settings = copy.deepcopy(geometry.get("image_edit") or self._default)
        verify_edit_source(payload, self._settings)
        self._initial = copy.deepcopy(self._settings)
        self._history = [copy.deepcopy(self._settings)]
        self._history_index = 0
        self._generation = 0
        self._worker = None
        self._closed = False
        self.candidate = None
        self._candidate_from_mask = False
        self.vector_outline = None
        self._offset_preview = None
        self._vector_preview_key = None
        self.preview_count = 0
        self._build_ui()
        self.refresh_preview()
        self._initial_fit_timer = QtCore.QTimer(self)
        self._initial_fit_timer.setSingleShot(True)
        self._initial_fit_timer.timeout.connect(self.fit)
        self._initial_fit_timer.start(0)

    @property
    def settings(self):
        return copy.deepcopy(self._settings)

    @property
    def create_vector(self):
        return self.vector_checkbox.isChecked()

    @property
    def offset_mm(self):
        return self.offset.value()

    @property
    def include_holes(self):
        return self.vector_holes.isChecked()

    @property
    def link_vector(self):
        return self.link_checkbox.isChecked()

    def _button(self, text, slot, layout):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(slot)
        layout.addWidget(button)
        return button

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel("Keep grayscale detail inside an editable outline. Exclude unwanted shadows locally. "
                                "The source and physical image frame stay unchanged.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        body = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        trace = QtWidgets.QGroupBox("1 · Preview foreground outline")
        form = QtWidgets.QFormLayout(trace)
        self.tolerance = QtWidgets.QSpinBox()
        self.tolerance.setRange(0, 255)
        self.tolerance.setValue(20)
        self.tolerance.setToolTip("Distance from white; larger values discard darker background. Local exclusion removes shadows.")
        self.detail = QtWidgets.QDoubleSpinBox()
        self.detail.setRange(.05, 10)
        self.detail.setDecimals(2)
        self.detail.setValue(.5)
        self.detail.setSuffix(" px")
        self.detail.setToolTip("Maximum contour simplification error. Smaller values preserve finer detail.")
        self.smoothing = QtWidgets.QDoubleSpinBox()
        self.smoothing.setRange(0, 10)
        self.smoothing.setValue(0)
        self.smoothing.setSuffix(" px")
        self.minimum = QtWidgets.QSpinBox()
        self.minimum.setRange(1, 1_000_000)
        self.minimum.setValue(8)
        self.trace_holes = QtWidgets.QCheckBox("Include interior holes")
        for label, control in (("Background tolerance", self.tolerance), ("Detail error", self.detail),
                               ("Smoothing", self.smoothing), ("Minimum component px²", self.minimum)):
            form.addRow(label, control)
        form.addRow(self.trace_holes)
        options = self._settings.get("trace") or {}
        for key, control in (("tolerance", self.tolerance), ("detail", self.detail),
                             ("smoothing", self.smoothing), ("minimum_component_area", self.minimum)):
            if key in options:
                control.setValue(options[key])
        self.trace_holes.setChecked(options.get("include_holes", False))
        trace_buttons = QtWidgets.QVBoxLayout()
        self.trace_button = self._button("Preview automatic trace", self.preview_trace, trace_buttons)
        self.mask_trace_button = self._button("Preview outline from edited mask", self.preview_mask_outline, trace_buttons)
        self.apply_candidate_button = self._button("Apply preview outline", self.apply_candidate, trace_buttons)
        self.apply_candidate_button.setEnabled(False)
        form.addRow(trace_buttons)
        left.addWidget(trace)
        editing = QtWidgets.QGroupBox("2 · Refine outline and mask")
        tools_layout = QtWidgets.QVBoxLayout(editing)
        self.tool = QtWidgets.QComboBox()
        for title, key in (("Edit nodes / curve handles", "nodes"), ("Polygon outline", "polygon"),
                           ("Curved pen outline", "pen"), ("Rectangle selection", "rectangle"),
                           ("Ellipse selection", "ellipse"), ("Lasso selection", "lasso"),
                           ("Exclude brush", "exclude"), ("Restore brush", "restore"), ("Pan", "pan")):
            self.tool.addItem(title, key)
        tools_layout.addWidget(self.tool)
        self.selection_operation = QtWidgets.QComboBox()
        self.selection_operation.addItem("Subtract / exclude selection", "subtract")
        self.selection_operation.addItem("Add / restore original pixels", "add")
        self.selection_operation.addItem("Restore original pixels", "restore")
        tools_layout.addWidget(self.selection_operation)
        brush_row = QtWidgets.QHBoxLayout()
        brush_row.addWidget(QtWidgets.QLabel("Brush diameter"))
        self.brush_pixels = QtWidgets.QSpinBox()
        self.brush_pixels.setRange(1, min(4096, 4 * min(self.width, self.height)))
        self.brush_pixels.setValue(max(2, min(48, min(self.width, self.height) // 15)))
        self.brush_pixels.setSuffix(" px")
        brush_row.addWidget(self.brush_pixels)
        tools_layout.addLayout(brush_row)
        nodes = QtWidgets.QHBoxLayout()
        self._button("Add node", lambda: self.view.add_node(), nodes)
        self._button("Delete node", lambda: self.view.delete_node(), nodes)
        tools_layout.addLayout(nodes)
        self._button("Remove selected contour", lambda: self.view.remove_contour(), tools_layout)
        self._button("Finish pen contour (Enter)", lambda: self.view.finish_pen(), tools_layout)
        self.mask_enabled = QtWidgets.QCheckBox("Use outline as mask")
        self.mask_enabled.setToolTip("Local exclusions remain active. Reset edits clears all editor edits; Undo can restore them.")
        self.mask_enabled.setChecked(self._settings["mask_enabled"])
        tools_layout.addWidget(self.mask_enabled)
        self.invert = QtWidgets.QCheckBox("Invert retained artwork")
        self.invert.setChecked(self._settings["invert"])
        tools_layout.addWidget(self.invert)
        left.addWidget(editing)
        vector = QtWidgets.QGroupBox("3 · Optional separate vector")
        vector_layout = QtWidgets.QFormLayout(vector)
        self.vector_checkbox = QtWidgets.QCheckBox("Create aligned vector (output off)")
        vector_layout.addRow(self.vector_checkbox)
        self.offset = QtWidgets.QDoubleSpinBox()
        self.offset.setRange(-1000, 1000)
        self.offset.setDecimals(3)
        self.offset.setSuffix(" mm")
        self.offset.setToolTip("Positive = outside; negative = inside. Nonzero offsets approximate curves with "
                               "lines (≤0.01 mm flattening tolerance) and mitered corners. Invalid offsets are rejected.")
        vector_layout.addRow("Inside − / outside +", self.offset)
        self.vector_preview_button = QtWidgets.QPushButton("Preview vector and offset")
        self.vector_preview_button.clicked.connect(self.preview_vector)
        vector_layout.addRow(self.vector_preview_button)
        self.vector_holes = QtWidgets.QCheckBox("Include interior contours")
        self.vector_holes.setChecked(True)
        vector_layout.addRow(self.vector_holes)
        self.link_checkbox = QtWidgets.QCheckBox("Link image and vector transforms")
        self.link_checkbox.setChecked(True)
        vector_layout.addRow(self.link_checkbox)
        note = QtWidgets.QLabel("After local edits, preview an outline from the edited mask and explicitly replace the outline. "
                               "Vector is a separate snapshot; later image edits never change it. Assign cutting after review.")
        note.setWordWrap(True)
        vector_layout.addRow(note)
        left.addWidget(vector)
        left.addStretch()
        left_widget = QtWidgets.QWidget()
        left_widget.setLayout(left)
        left_widget.setMaximumWidth(360)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left_widget)
        scroll.setMinimumWidth(335)
        scroll.setMaximumWidth(375)
        body.addWidget(scroll)
        right = QtWidgets.QVBoxLayout()
        bar = QtWidgets.QHBoxLayout()
        self.before = QtWidgets.QCheckBox("Before / original")
        bar.addWidget(self.before)
        self._button("Fit", self.fit, bar)
        self._button("100%", lambda: self.view.resetTransform(), bar)
        self.undo_button = self._button("Undo", self.undo, bar)
        self.redo_button = self._button("Redo", self.redo, bar)
        self._button("Reset edits", self.reset_edits, bar)
        right.addLayout(bar)
        self.view = ImageEditView(self)
        right.addWidget(self.view, 1)
        self.instructions = QtWidgets.QLabel()
        self.instructions.setWordWrap(True)
        right.addWidget(self.instructions)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Apply |
                                                QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).setText("Apply to project")
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.tool.currentIndexChanged.connect(self._tool_changed)
        self.before.toggled.connect(self._show_image)
        self.mask_enabled.toggled.connect(lambda value: self._toggle("mask_enabled", value))
        self.invert.toggled.connect(lambda value: self._toggle("invert", value))
        for control in (self.tolerance, self.detail, self.smoothing, self.minimum):
            control.valueChanged.connect(self._trace_options_changed)
        self.trace_holes.toggled.connect(self._trace_options_changed)
        self.offset.valueChanged.connect(self._clear_vector_preview)
        self.vector_holes.toggled.connect(self._clear_vector_preview)
        self.vector_checkbox.toggled.connect(self._clear_vector_preview)
        self.finished.connect(self._finish)
        for sequence, callback in ((QtGui.QKeySequence.StandardKey.Undo, self.undo),
                                   (QtGui.QKeySequence.StandardKey.Redo, self.redo)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
        self._tool_changed()

    def _tool_changed(self, *_args):
        key = self.tool.currentData()
        self.view.set_tool(key)
        self.instructions.setText({
            "nodes": "Drag cyan anchors or their curve handles. Add node splits the following segment. Delete removes an anchor. "
                     "Cyan = accepted outline; dashed gold = unapplied preview.",
            "polygon": "Click anchors, then Enter or click the first anchor to close. New contours are added; existing refinements remain.",
            "pen": "Click and drag each anchor to set its tangent; click for a corner. Enter closes the contour. Refine handles with Edit nodes.",
            "exclude": "Paint unwanted shadow/background. White areas are excluded without changing retained source pixels.",
            "restore": "Paint to restore original source pixels, including outside the outline. Undo reverses a complete stroke.",
            "pan": "Drag to pan; scroll to zoom. Middle-drag also pans in every tool.",
        }.get(key, "Drag a selection; choose subtract or restore above. Local changes commit on release. Scroll to zoom; middle-drag to pan."))

    def fit(self):
        self.view.fitInView(self.view.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def _toggle(self, name, value):
        state = self.settings
        state[name] = value
        self.commit(state, "Image setting changed")

    def _trace_options(self):
        return {"tolerance": self.tolerance.value(), "detail": self.detail.value(),
                "smoothing": self.smoothing.value(), "minimum_component_area": self.minimum.value(),
                "include_holes": self.trace_holes.isChecked()}

    def _trace_options_changed(self, *_args):
        self._invalidate_trace()
        self.status.setText("Trace settings changed. Preview again; your accepted outline and edits are preserved.")

    def _invalidate_trace(self):
        self._generation += 1
        if self._worker is not None:
            self._worker.cancelled.set()
        self.candidate = None
        self.apply_candidate_button.setEnabled(False)
        self.view.viewport().update()

    def preview_trace(self):
        self._start_trace(False)

    def preview_mask_outline(self):
        self._start_trace(True)

    def _start_trace(self, from_mask):
        if self._worker is not None:
            self.status.setText("Finishing the previous preview. Your latest edits remain unchanged; try Preview again shortly.")
            return
        if from_mask and not self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).isEnabled():
            self.status.setText("The current mask is invalid. Undo or refine its outline before tracing it.")
            return
        self._invalidate_trace()
        self._candidate_from_mask = from_mask
        worker = _TraceWorker(self._generation, self.allowed.copy() if from_mask else self.gray,
                              self._trace_options(), edited_mask=from_mask)
        worker.signals.finished.connect(self._trace_finished)
        self._worker = worker
        self.trace_button.setEnabled(False)
        self.mask_trace_button.setEnabled(False)
        self.status.setText("Building preview… Accepted outline remains unchanged. You can continue editing.")
        QtCore.QThreadPool.globalInstance().start(worker)

    @QtCore.Slot(int, object, object)
    def _trace_finished(self, generation, native, error):
        if self._worker is not None and generation == self._worker.generation:
            self._worker = None
            self.trace_button.setEnabled(True)
            self.mask_trace_button.setEnabled(True)
        if self._closed or generation != self._generation:
            return
        if error:
            self.status.setText(f"Trace could not be accepted: {error}")
            return
        self.candidate = native
        self.apply_candidate_button.setEnabled(True)
        self.apply_candidate_button.setText("Replace outline with preview" if self._settings.get("outline")
                                            else "Apply preview outline")
        issues = validate_outline(native)
        self.status.setText("; ".join(issues) if issues else
                            f"Preview: {len(native.subpaths)} contours / {native.segment_count} segments. "
                            "Gold is a preview only. Apply explicitly to replace the accepted outline.")
        self.view.viewport().update()

    def apply_candidate(self):
        if self.candidate is None or self.view.dragging or self.view.points:
            return
        state = self.settings
        state["outline"] = self.candidate.to_dict()
        state["trace"] = self._trace_options()
        state["mask_enabled"] = True
        if self._candidate_from_mask:
            state["regions"] = []
        self.view.clear_node_selection()
        self.commit(state, "Preview outline applied; original image pixels remain unchanged")

    def add_region(self, region):
        state = self.settings
        state["regions"].append(copy.deepcopy(region))
        state["mask_enabled"] = True
        self.commit(state, "Local edit committed; accepted outline unchanged. Preview the edited mask outline to update contours")

    def add_subpath(self, subpath):
        state = self.settings
        existing = NativePathGeometry.from_dict(state["outline"]) if state.get("outline") else None
        paths = existing.subpaths if existing else ()
        state["outline"] = NativePathGeometry((*paths, subpath)).to_dict()
        state["mask_enabled"] = True
        self.commit(state, "Manual contour added")

    def commit(self, state, message="Edit committed"):
        if state == self._settings:
            return
        def topology(native):
            return tuple((sub["closed"], tuple(segment["type"] for segment in sub["segments"]))
                         for sub in native["subpaths"]) if native else None

        if topology(state.get("outline")) != topology(self._settings.get("outline")):
            self.view.clear_node_selection()
        self._invalidate_trace()
        self._settings = copy.deepcopy(state)
        self._history = self._history[:self._history_index + 1]
        self._history.append(self.settings)
        self._history_index += 1
        self._clear_vector_preview()
        self.refresh_preview(message)

    def undo(self):
        self.view.cancel_gesture()
        self.view.clear_node_selection()
        if self._history_index > 0:
            self._history_index -= 1
            self._settings = copy.deepcopy(self._history[self._history_index])
            self._invalidate_trace()
            self._clear_vector_preview()
            self.refresh_preview("Undid edit")

    def redo(self):
        self.view.cancel_gesture()
        self.view.clear_node_selection()
        if self._history_index + 1 < len(self._history):
            self._history_index += 1
            self._settings = copy.deepcopy(self._history[self._history_index])
            self._invalidate_trace()
            self._clear_vector_preview()
            self.refresh_preview("Redid edit")

    def reset_edits(self):
        self.view.cancel_gesture()
        self.view.clear_node_selection()
        self.commit(copy.deepcopy(self._default), "Editor edits reset; Undo restores them")

    def refresh_preview(self, message=""):
        geometry = copy.deepcopy(self.geometry)
        geometry["image_edit"] = self.settings
        try:
            self.after_gray, self.allowed = process_image(self.gray, geometry, source_allowed=self.source_allowed)
            self.after_image = grayscale_image(self.after_gray)
            self.preview_count += 1
            self._show_image()
            issues = validate_outline(NativePathGeometry.from_dict(self._settings["outline"])) if self._settings.get("outline") else ()
            self.status.setText("; ".join(issues) if issues else
                                f"{message + '. ' if message else ''}{np.count_nonzero(~self.allowed):,} excluded pixels. "
                                f"Frame {self.width_mm:g} × {self.height_mm:g} mm; {self.width} × {self.height} source pixels.")
            self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).setEnabled(True)
        except (ValueError, TypeError) as exc:
            self.status.setText(f"Invalid edit: {exc}. Undo or refine the outline.")
            self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).setEnabled(False)
        for control, name in ((self.mask_enabled, "mask_enabled"), (self.invert, "invert")):
            blocker = QtCore.QSignalBlocker(control)
            control.setChecked(self._settings[name])
            del blocker
        self.undo_button.setEnabled(self._history_index > 0)
        self.redo_button.setEnabled(self._history_index + 1 < len(self._history))
        self.view.viewport().update()

    def _show_image(self, *_args):
        self.view.image_item.setPixmap(QtGui.QPixmap.fromImage(
            self.original_image if self.before.isChecked() else self.after_image))
        self.view.viewport().update()

    def _clear_vector_preview(self, *_args):
        self.vector_outline = None
        self._offset_preview = None
        self._vector_preview_key = None
        self.view.viewport().update()

    def _vector_key(self):
        return repr((self._settings, self.offset_mm, self.include_holes))

    def preview_vector(self):
        if self.view.dragging or self.view.points:
            self.status.setText("Finish or discard the current gesture before previewing vectors.")
            return
        geometry = copy.deepcopy(self.geometry)
        geometry["image_edit"] = self.settings
        try:
            if self._settings.get("outline"):
                errors = validate_outline(self._settings["outline"])
                if errors:
                    raise ValueError("; ".join(errors))
            native = derive_vector_outline(self.gray, geometry, include_holes=self.include_holes)
            errors = validate_outline(native)
            if errors:
                raise ValueError("; ".join(errors))
            self._offset_preview = offset_outline(native, self.width_mm, self.height_mm, self.offset_mm,
                                                  include_holes=self.include_holes)
            self.vector_outline = native
            self._vector_preview_key = self._vector_key()
            self.status.setText(f"Green dashed vector preview: {len(self._offset_preview.subpaths)} contours, "
                                f"{self.offset_mm:+g} mm offset. Review before Apply to project. "
                                + ("Nonzero offsets use line approximations (≤0.01 mm) and mitered corners. "
                                   if self.offset_mm else "") +
                                "Vector output will be off until explicitly assigned.")
        except (ValueError, TypeError) as exc:
            self._clear_vector_preview()
            self.status.setText(f"Cannot create vector: {exc}. No geometry was silently repaired.")
        self.view.viewport().update()

    def accept(self):
        if self.view.dragging or self.view.points:
            self.status.setText("Finish the current contour with Enter, or discard the draft gesture with Escape.")
            return
        geometry = copy.deepcopy(self.geometry)
        geometry["image_edit"] = self.settings
        try:
            process_image(self.gray, geometry, source_allowed=self.source_allowed)
            if self.create_vector:
                if self._vector_preview_key != self._vector_key():
                    self.preview_vector()
                    return
        except (ValueError, TypeError) as exc:
            self.status.setText(f"Cannot apply: {exc}. No geometry was silently repaired.")
            return
        # Future trace choices are authoring preferences, so saving them does not
        # reprocess the image or replace the already accepted native outline.
        self._settings["trace"] = self._trace_options()
        super().accept()

    def _finish(self, *_args):
        self._closed = True
        self._invalidate_trace()

