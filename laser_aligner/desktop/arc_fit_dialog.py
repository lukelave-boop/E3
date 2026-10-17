"""Review a circular replacement of an explicitly selected vector span."""

from __future__ import annotations

from ..project import NativePathGeometry, PathCubicSegment, PathSubpath, SceneObject
from ..project.arc_fit import (
    MAX_ARC_FIT_SEGMENTS,
    ArcFitResult,
    arc_fit_vertices,
    fit_arc_span,
)
from .controls import NumericDoubleSpinBox, NumericSpinBox
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class ArcFitView(QtWidgets.QGraphicsView):
    vertexPicked = QtCore.Signal(int, int)

    def __init__(self, source: SceneObject, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setMinimumSize(480, 320)
        self.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QtGui.QColor("#171D25"))
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._shape_transform = QtGui.QTransform()
        self._shape_transform.rotate(-source.transform.rotation_deg)
        self._shape_transform.scale(
            -source.transform.width_mm if source.transform.mirror_x else source.transform.width_mm,
            source.transform.height_mm if source.transform.mirror_y else -source.transform.height_mm,
        )
        self.geometry = source.path_geometry()
        self.vertices = tuple(arc_fit_vertices(part) for part in self.geometry.subpaths)
        self._vertex_positions = tuple(tuple(self._point(point) for point in vertices) for vertices in self.vertices)
        self._original = self._add_path(self.geometry.subpaths, "#8394A8", 1.5)
        self._selected = self._add_path((), "#FFB454", 3.0)
        self._proposal = self._add_path((), "#00DBEE", 2.0)
        self._markers: list = []
        self._active_outline = 0
        self._press_position = None
        self._fitted = False
        self._bounds = self._original.path().boundingRect()
        padding = max(self._bounds.width(), self._bounds.height(), 1.0) * 0.08
        self.setSceneRect(self._bounds.adjusted(-padding, -padding, padding, padding))

    def _point(self, point):
        return self._shape_transform.map(QtCore.QPointF(*point))

    def _path(self, subpaths) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        for subpath in subpaths:
            path.moveTo(self._point(subpath.start))
            for segment in subpath.segments:
                if isinstance(segment, PathCubicSegment):
                    path.cubicTo(
                        self._point(segment.control_1),
                        self._point(segment.control_2),
                        self._point(segment.to),
                    )
                else:
                    path.lineTo(self._point(segment.to))
            if subpath.closed:
                path.closeSubpath()
        return path

    def _add_path(self, subpaths, color: str, width: float):
        pen = QtGui.QPen(QtGui.QColor(color), width)
        pen.setCosmetic(True)
        return self.scene().addPath(self._path(subpaths), pen)

    def fit_all(self) -> None:
        self.fitInView(self.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        self._fitted = True

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._fitted:
            self.fit_all()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        if 0.01 <= self.transform().m11() * factor <= 100000:
            self.scale(factor, factor)
        event.accept()

    def drawForeground(self, painter, rect) -> None:  # noqa: N802 - Qt override
        del rect
        painter.save()
        painter.resetTransform()
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#C3CCD7"))
        previous = None
        for point in self._vertex_positions[self._active_outline]:
            position = self.mapFromScene(point)
            if not self.viewport().rect().contains(position):
                continue
            # Dense traces become individually selectable as the operator zooms.
            if previous is None or (position - previous).manhattanLength() >= 9:
                painter.drawEllipse(QtCore.QPointF(position), 1.8, 1.8)
                previous = position
        painter.restore()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._press_position = event.position() if event.button() == QtCore.Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        pressed = self._press_position
        self._press_position = None
        super().mouseReleaseEvent(event)
        if pressed is None or (event.position() - pressed).manhattanLength() > 5:
            return
        nearest = None
        best_distance = 18.0**2
        for subpath_index, vertices in enumerate(self._vertex_positions):
            for vertex_index, point in enumerate(vertices):
                offset = QtCore.QPointF(self.mapFromScene(point)) - event.position()
                distance = offset.x() ** 2 + offset.y() ** 2
                if distance < best_distance:
                    best_distance = distance
                    nearest = (subpath_index, vertex_index)
        if nearest is not None:
            self.vertexPicked.emit(*nearest)

    def set_span(self, subpath_index: int, start: int | None, end: int | None) -> None:
        self._active_outline = subpath_index
        self.viewport().update()
        for marker in self._markers:
            self.scene().removeItem(marker)
        self._markers.clear()
        self._selected.setPath(QtGui.QPainterPath())
        for vertex, label in ((start, "Start"), (end, "End")):
            if vertex is None:
                continue
            point = self._vertex_positions[subpath_index][vertex]
            marker = self.scene().addEllipse(
                -5, -5, 10, 10, QtGui.QPen(QtGui.QColor("#FFFFFF")), QtGui.QBrush(QtGui.QColor("#FFB454"))
            )
            marker.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            marker.setPos(point)
            text = self.scene().addSimpleText(label)
            text.setBrush(QtGui.QColor("#FFFFFF"))
            text.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            text.setPos(point)
            self._markers.extend((marker, text))
        if start is None or end is None or start == end:
            return
        subpath = self.geometry.subpaths[subpath_index]
        if not subpath.closed and start > end:
            start, end = end, start
        path = QtGui.QPainterPath()
        path.moveTo(self._vertex_positions[subpath_index][start])
        index = start
        count = len(self.vertices[subpath_index])
        while index != end:
            if index < len(subpath.segments):
                segment = subpath.segments[index]
                if isinstance(segment, PathCubicSegment):
                    path.cubicTo(
                        self._point(segment.control_1), self._point(segment.control_2), self._point(segment.to)
                    )
                else:
                    path.lineTo(self._point(segment.to))
            else:
                path.lineTo(self._point(subpath.start))
            index = (index + 1) % count
        self._selected.setPath(path)

    def set_proposal(self, subpath: PathSubpath | None) -> None:
        self._proposal.setPath(self._path((subpath,)) if subpath else QtGui.QPainterPath())


class ArcFitDialog(QtWidgets.QDialog):
    def __init__(self, source: SceneObject, parent=None) -> None:
        super().__init__(parent)
        # Freeze the source; preview never owns a reference to mutable project geometry.
        self.source = SceneObject.from_dict(source.to_dict())
        self.geometry: NativePathGeometry = self.source.path_geometry()
        if self.geometry.segment_count > MAX_ARC_FIT_SEGMENTS:
            raise ValueError("This vector has too many edge segments; simplify it before fitting an arc")
        self.proposal: ArcFitResult | None = None
        self.accepted_fit: ArcFitResult | None = None
        self.setWindowTitle(f"Fit arc — {source.name}")
        self.resize(960, 710)
        layout = QtWidgets.QVBoxLayout(self)
        instruction = QtWidgets.QLabel(
            "Choose the start and end of the curved edge. Click near an existing point, "
            "or use the point numbers. Scroll to zoom; drag to pan."
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        controls = QtWidgets.QHBoxLayout()
        layout.addLayout(controls)
        controls.addWidget(QtWidgets.QLabel("Outline"))
        self.outline_combo = QtWidgets.QComboBox()
        for index, subpath in enumerate(self.geometry.subpaths):
            self.outline_combo.addItem(f"{index + 1} · {'Closed' if subpath.closed else 'Open'}")
        controls.addWidget(self.outline_combo)
        controls.addStretch(1)
        self.fit_view_button = QtWidgets.QPushButton("Fit view")
        controls.addWidget(self.fit_view_button)
        endpoints = QtWidgets.QHBoxLayout()
        layout.addLayout(endpoints)
        self.pick_start = QtWidgets.QPushButton("Pick start")
        self.pick_end = QtWidgets.QPushButton("Pick end")
        self._pick_group = QtWidgets.QButtonGroup(self)
        for button in (self.pick_start, self.pick_end):
            button.setCheckable(True)
            self._pick_group.addButton(button)
        self.pick_start.setChecked(True)
        self.start_spin = NumericSpinBox()
        self.end_spin = NumericSpinBox()
        for label, button, spin in (
            ("Start point", self.pick_start, self.start_spin),
            ("End point", self.pick_end, self.end_spin),
        ):
            prefix = label.split()[0]
            spin.setPrefix(f"{prefix}: ")
            spin.setSpecialValueText(f"{prefix}: choose")
            spin.setAccessibleName(label)
            endpoints.addWidget(button)
            endpoints.addWidget(spin)
        self.other_side = QtWidgets.QPushButton("Use other side")
        self.other_side.setToolTip("For a closed outline, select the other span between these endpoints.")
        endpoints.addWidget(self.other_side)
        endpoints.addStretch(1)
        self.view = ArcFitView(self.source, self)
        layout.addWidget(self.view, 1)
        legend = QtWidgets.QLabel("Gray: original outline     Amber: selected edge     Cyan: proposed circular arc")
        legend.setWordWrap(True)
        layout.addWidget(legend)
        options = QtWidgets.QHBoxLayout()
        layout.addLayout(options)
        options.addWidget(QtWidgets.QLabel("Edge variation"))
        self.tolerance_spin = NumericDoubleSpinBox()
        self.tolerance_spin.setRange(0.001, 5.0)
        self.tolerance_spin.setDecimals(3)
        self.tolerance_spin.setSingleStep(0.025)
        self.tolerance_spin.setSuffix(" mm")
        self.tolerance_spin.setValue(0.1)
        self.tolerance_spin.setToolTip(
            "Allowed variation along the main edge. Small isolated defects may be up "
            "to three times this height, which is the maximum allowed edge movement; "
            "broad notches and corners are rejected."
        )
        options.addWidget(self.tolerance_spin)
        options.addStretch(1)
        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(44)
        layout.addWidget(self.status_label)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Apply | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_button = self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText("Apply arc")
        self.apply_button.setEnabled(False)
        self.apply_button.setDefault(False)
        layout.addWidget(self.buttons)
        self.buttons.rejected.connect(self.reject)
        self.apply_button.clicked.connect(self.accept)
        self.fit_view_button.clicked.connect(self.view.fit_all)
        self.view.vertexPicked.connect(self._pick_vertex)
        self.outline_combo.currentIndexChanged.connect(self._outline_changed)
        self.start_spin.valueChanged.connect(self._refresh)
        self.end_spin.valueChanged.connect(self._refresh)
        self.tolerance_spin.valueChanged.connect(self._refresh)
        self.other_side.clicked.connect(self._swap_endpoints)
        self._outline_changed()

    def _outline_changed(self, *_args) -> None:
        index = self.outline_combo.currentIndex()
        count = len(self.view.vertices[index])
        for spin in (self.start_spin, self.end_spin):
            blocker = QtCore.QSignalBlocker(spin)
            spin.setRange(0, count)
            spin.setValue(0)
            del blocker
        self.other_side.setEnabled(self.geometry.subpaths[index].closed)
        self.pick_start.setChecked(True)
        self._refresh()

    def _pick_vertex(self, subpath_index: int, vertex_index: int) -> None:
        if subpath_index != self.outline_combo.currentIndex():
            self.outline_combo.setCurrentIndex(subpath_index)
        if self.pick_start.isChecked():
            self.start_spin.setValue(vertex_index + 1)
            self.pick_end.setChecked(True)
        else:
            self.end_spin.setValue(vertex_index + 1)

    def _swap_endpoints(self) -> None:
        start, end = self.start_spin.value(), self.end_spin.value()
        blocker = QtCore.QSignalBlocker(self.start_spin)
        self.start_spin.setValue(end)
        del blocker
        self.end_spin.setValue(start)
        self._refresh()

    def _refresh(self, *_args) -> None:
        self.proposal = None
        self.accepted_fit = None
        self.apply_button.setEnabled(False)
        self.view.set_proposal(None)
        index = self.outline_combo.currentIndex()
        start, end = self.start_spin.value() - 1, self.end_spin.value() - 1
        self.view.set_span(index, start if start >= 0 else None, end if end >= 0 else None)
        if start < 0 or end < 0:
            self.status_label.setText("Choose two endpoints to preview an arc. Nothing changes until you apply it.")
            return
        if not self.geometry.subpaths[index].closed and start > end:
            start, end = end, start
        try:
            result = fit_arc_span(
                self.geometry,
                index,
                start,
                end,
                width_mm=self.source.transform.width_mm,
                height_mm=self.source.transform.height_mm,
                tolerance_mm=self.tolerance_spin.value(),
            )
        except ValueError as exc:
            self.status_label.setText(f"Arc unavailable: {exc}")
            return
        self.proposal = result
        self.view.set_proposal(result.arc_subpath)
        self.status_label.setText(
            f"Radius {result.radius_mm:.3f} mm  ·  Sweep {abs(result.sweep_degrees):.1f}°  ·  "
            f"Largest edge change {result.maximum_deviation_mm:.3f} mm\n"
            "Review the cyan arc before applying. Undo restores the original edge."
        )
        self.apply_button.setEnabled(True)

    def accept(self) -> None:
        # Commit pending typed values before accepting, including a tolerance edit
        # that has not lost focus yet. Recompute so an older preview cannot escape.
        for spin in (self.start_spin, self.end_spin, self.tolerance_spin):
            spin.interpretText()
        self._refresh()
        if self.proposal is None:
            return
        self.accepted_fit = self.proposal
        super().accept()

    def reject(self) -> None:
        self.accepted_fit = None
        super().reject()
