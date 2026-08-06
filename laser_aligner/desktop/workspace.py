from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ..gcode.preview import parse_gcode_segments
from ..project import (
    Bounds,
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    SceneObject,
    Transform,
)
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class WorkspaceScene(QtWidgets.QGraphicsScene):
    def __init__(self, work_area: Bounds, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.work_area = work_area
        self.setItemIndexMethod(QtWidgets.QGraphicsScene.ItemIndexMethod.BspTreeIndex)
        self.set_work_area(work_area)

    def set_work_area(self, work_area: Bounds) -> None:
        self.work_area = work_area
        # Keep a generous non-machine margin around the work area.
        # QGraphicsView cannot scroll when its scene is smaller than the
        # viewport, which made Space+drag appear to do nothing.
        pan_margin = max(
            120.0,
            max(work_area.width, work_area.height) * 0.75,
        )
        self.setSceneRect(
            work_area.x_min - pan_margin,
            -work_area.y_max - pan_margin,
            work_area.width + pan_margin * 2.0,
            work_area.height + pan_margin * 2.0,
        )
        self.invalidate(self.sceneRect(), QtWidgets.QGraphicsScene.SceneLayer.BackgroundLayer)

    @staticmethod
    def machine_to_scene(x_mm: float, y_mm: float) -> QtCore.QPointF:
        return QtCore.QPointF(float(x_mm), -float(y_mm))

    @staticmethod
    def scene_to_machine(point: QtCore.QPointF) -> tuple[float, float]:
        return float(point.x()), float(-point.y())

    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        painter.fillRect(rect, QtGui.QColor("#091015"))
        work_rect = QtCore.QRectF(
            self.work_area.x_min,
            -self.work_area.y_max,
            self.work_area.width,
            self.work_area.height,
        )
        painter.fillRect(work_rect, QtGui.QColor("#111A20"))

        transform = painter.worldTransform()
        scale = max(abs(transform.m11()), 1e-6)
        if scale >= 4.0:
            minor_step = 1.0
        elif scale >= 1.5:
            minor_step = 5.0
        else:
            minor_step = 10.0
        major_step = 10.0

        clipped = rect.intersected(work_rect)
        if clipped.isEmpty():
            return

        def positions(start: float, end: float, step: float) -> list[float]:
            first = math.floor(start / step) * step
            count = int(math.ceil((end - first) / step)) + 1
            return [first + index * step for index in range(max(0, count))]

        minor_pen = QtGui.QPen(QtGui.QColor("#1B272F"))
        minor_pen.setCosmetic(True)
        major_pen = QtGui.QPen(QtGui.QColor("#2C3A44"))
        major_pen.setCosmetic(True)

        for x in positions(clipped.left(), clipped.right(), minor_step):
            if x < self.work_area.x_min - 1e-9 or x > self.work_area.x_max + 1e-9:
                continue
            is_major = abs(x / major_step - round(x / major_step)) < 1e-6
            painter.setPen(major_pen if is_major else minor_pen)
            painter.drawLine(
                QtCore.QPointF(x, -self.work_area.y_max),
                QtCore.QPointF(x, -self.work_area.y_min),
            )

        machine_y_min = -clipped.bottom()
        machine_y_max = -clipped.top()
        for y in positions(machine_y_min, machine_y_max, minor_step):
            if y < self.work_area.y_min - 1e-9 or y > self.work_area.y_max + 1e-9:
                continue
            is_major = abs(y / major_step - round(y / major_step)) < 1e-6
            painter.setPen(major_pen if is_major else minor_pen)
            painter.drawLine(
                QtCore.QPointF(self.work_area.x_min, -y),
                QtCore.QPointF(self.work_area.x_max, -y),
            )

        border = QtGui.QPen(QtGui.QColor("#4FC3A1"))
        border.setWidthF(0.35)
        border.setCosmetic(True)
        painter.setPen(border)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRect(work_rect)

        origin_pen = QtGui.QPen(QtGui.QColor("#E7B55C"))
        origin_pen.setWidthF(0.5)
        origin_pen.setCosmetic(True)
        painter.setPen(origin_pen)
        origin = self.machine_to_scene(self.work_area.x_min, self.work_area.y_min)
        painter.drawLine(origin + QtCore.QPointF(0, -5), origin + QtCore.QPointF(0, 5))
        painter.drawLine(origin + QtCore.QPointF(-5, 0), origin + QtCore.QPointF(5, 0))


class ObjectGraphicsItem(QtWidgets.QGraphicsPathItem):
    def __init__(
        self,
        scene_object: SceneObject,
        layer: OperationLayer,
        move_callback: Any | None = None,
        parent: QtWidgets.QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self.object_id = scene_object.id
        self._move_callback = move_callback
        self._start_position = QtCore.QPointF()
        self._syncing = False
        self.setFlags(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.apply_model(scene_object, layer)

    @staticmethod
    def _path_for_object(scene_object: SceneObject) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        kind = scene_object.kind
        if kind == ObjectKind.RECTANGLE:
            width = scene_object.transform.width_mm
            height = scene_object.transform.height_mm
            radius = min(
                float(scene_object.geometry.get("corner_radius_mm", 0.0)),
                width / 2.0,
                height / 2.0,
            )
            path.addRoundedRect(
                QtCore.QRectF(-width / 2.0, -height / 2.0, width, height),
                radius,
                radius,
            )
        elif kind == ObjectKind.ELLIPSE:
            width = scene_object.transform.width_mm
            height = scene_object.transform.height_mm
            path.addEllipse(QtCore.QRectF(-width / 2.0, -height / 2.0, width, height))
        elif kind == ObjectKind.LINE:
            points = scene_object.geometry.get("points", [[-0.5, 0.0], [0.5, 0.0]])
            width = scene_object.transform.width_mm
            height = scene_object.transform.height_mm
            start = QtCore.QPointF(points[0][0] * width, -points[0][1] * height)
            end = QtCore.QPointF(points[1][0] * width, -points[1][1] * height)
            path.moveTo(start)
            path.lineTo(end)
        elif kind in {ObjectKind.PATH, ObjectKind.POLYGON}:
            width = scene_object.transform.width_mm
            height = scene_object.transform.height_mm
            for line in scene_object.geometry.get("polylines", []):
                points = line.get("points", [])
                if len(points) < 2:
                    continue
                first = points[0]
                path.moveTo(first[0] * width, -first[1] * height)
                for point in points[1:]:
                    path.lineTo(point[0] * width, -point[1] * height)
                if line.get("closed", False):
                    path.closeSubpath()
        elif kind == ObjectKind.TEXT:
            font = QtGui.QFont(str(scene_object.geometry.get("font_family", "Sans Serif")))
            font.setPointSizeF(max(1.0, scene_object.transform.height_mm * 2.2))
            path.addText(
                QtCore.QPointF(
                    -scene_object.transform.width_mm / 2.0,
                    scene_object.transform.height_mm / 3.0,
                ),
                font,
                str(scene_object.geometry.get("text", "Text")),
            )
        return path

    def apply_model(self, scene_object: SceneObject, layer: OperationLayer) -> None:
        self._syncing = True
        try:
            self.setPath(self._path_for_object(scene_object))
            color = QtGui.QColor(layer.color)
            pen = QtGui.QPen(color)
            pen.setWidthF(0.35)
            pen.setCosmetic(True)
            self.setPen(pen)
            if layer.mode == LayerMode.FILL:
                fill = QtGui.QColor(color)
                fill.setAlpha(65)
                self.setBrush(fill)
            else:
                self.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            self.setVisible(scene_object.visible and layer.visible)
            self.setEnabled(not scene_object.locked)
            self.setPos(scene_object.transform.x_mm, -scene_object.transform.y_mm)
            self.setRotation(-scene_object.transform.rotation_deg)
            transform = QtGui.QTransform()
            transform.scale(
                -1.0 if scene_object.transform.mirror_x else 1.0,
                -1.0 if scene_object.transform.mirror_y else 1.0,
            )
            self.setTransform(transform)
            self.setToolTip(
                f"{scene_object.name}\n"
                f"X {scene_object.transform.x_mm:.2f}  Y {scene_object.transform.y_mm:.2f}\n"
                f"{scene_object.transform.width_mm:.2f} × {scene_object.transform.height_mm:.2f} mm"
            )
        finally:
            self._syncing = False

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self._start_position = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        if self.pos() != self._start_position:
            before = WorkspaceScene.scene_to_machine(self._start_position)
            after = WorkspaceScene.scene_to_machine(self.pos())
            if self._move_callback is not None:
                self._move_callback(self.object_id, before, after)


class _TemplatePreviewDragSurface(QtWidgets.QGraphicsPathItem):
    """Transparent hit target that moves one rigid template preview."""

    def __init__(
        self,
        path: QtGui.QPainterPath,
        owner: "_TemplatePreviewGraphicsItem",
    ) -> None:
        super().__init__(path, owner)
        self._owner = owner
        self.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))
        self.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0, 0)))
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.LeftButton)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to move the entire template preview")
        self.setZValue(1000.0)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            self._owner.begin_drag(event.scenePos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self._owner.update_drag(event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._owner.finish_drag(event.scenePos())
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _TemplateRotationHandle(QtWidgets.QGraphicsEllipseItem):
    """Fixed-pixel handle that rotates its template around the reviewed center."""

    _RADIUS_PX = 7.0

    def __init__(self, owner: "_TemplatePreviewGraphicsItem") -> None:
        radius = self._RADIUS_PX
        super().__init__(QtCore.QRectF(-radius, -radius, radius * 2.0, radius * 2.0), owner)
        self._owner = owner
        self.setFlag(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        pen = QtGui.QPen(QtGui.QColor("#DDF8FF"))
        pen.setWidthF(1.5)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QtGui.QColor("#45D7FF"))
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.LeftButton)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setToolTip("Drag to rotate the entire template preview")
        self.setZValue(1002.0)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._owner.begin_rotation(event.scenePos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self._owner.update_rotation(event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._owner.finish_rotation(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _TemplatePreviewGraphicsItem(QtWidgets.QGraphicsItemGroup):
    """Interactive parent for every cyan path in one reviewed placement."""

    _HANDLE_OFFSET_PX = 24.0

    def __init__(
        self,
        objects: list[SceneObject],
        *,
        center_x_mm: float,
        center_y_mm: float,
        rotation_deg: float,
        edited_callback: Callable[[float, float, float], None],
        committed_callback: Callable[[float, float, float], None],
    ) -> None:
        super().__init__()
        # Rotation and drag children must receive their own events instead of
        # QGraphicsItemGroup redirecting every child event to this parent.
        self.setHandlesChildEvents(False)
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self.setZValue(280.0)
        self._initial_rotation_deg = Transform.normalized_rotation(rotation_deg)
        self._center_local = WorkspaceScene.machine_to_scene(center_x_mm, center_y_mm)
        self._edited_callback = edited_callback
        self._committed_callback = committed_callback
        self._drag_start_scene: QtCore.QPointF | None = None
        self._drag_start_position = QtCore.QPointF()
        self._drag_moved = False
        self._rotation_last_angle_deg: float | None = None
        self._rotation_start_group_deg = 0.0
        self._rotation_accumulated_deg = 0.0
        self._rotation_moved = False
        self.visual_items: list[QtWidgets.QGraphicsPathItem] = []

        hit_path = QtGui.QPainterPath()
        color = QtGui.QColor("#45D7FF")
        for scene_object in objects:
            path = ObjectGraphicsItem._path_for_object(scene_object)
            if path.isEmpty():
                continue
            item = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(color)
            pen.setWidthF(0.55)
            pen.setCosmetic(True)
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            item.setPen(pen)
            fill = QtGui.QColor(color)
            fill.setAlpha(12)
            item.setBrush(fill)
            item.setPos(scene_object.transform.x_mm, -scene_object.transform.y_mm)
            item.setRotation(-scene_object.transform.rotation_deg)
            transform = QtGui.QTransform()
            transform.scale(
                -1.0 if scene_object.transform.mirror_x else 1.0,
                -1.0 if scene_object.transform.mirror_y else 1.0,
            )
            item.setTransform(transform)
            item.setZValue(280.0)
            item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            item.setToolTip(f"Template preview: {scene_object.name}")
            self.addToGroup(item)
            hit_path.addPath(item.mapToParent(item.path()))
            self.visual_items.append(item)

        self._geometry_bounds = self.childrenBoundingRect()
        self.setTransformOriginPoint(self._center_local)

        outline_pen = QtGui.QPen(QtGui.QColor(69, 215, 255, 150))
        outline_pen.setWidthF(0.35)
        outline_pen.setCosmetic(True)
        outline_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        self._outline_item = QtWidgets.QGraphicsRectItem(self._geometry_bounds, self)
        self._outline_item.setPen(outline_pen)
        self._outline_item.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        self._outline_item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self._outline_item.setZValue(999.0)

        # Include a modest stroked area so open paths and narrow outlines are
        # still practical drag targets at normal workspace zoom levels.
        stroker = QtGui.QPainterPathStroker()
        stroker.setWidth(1.5)
        hit_path.addPath(stroker.createStroke(hit_path))
        self._drag_surface = _TemplatePreviewDragSurface(hit_path, self)

        self._handle_connector = QtWidgets.QGraphicsLineItem(self)
        connector_pen = QtGui.QPen(QtGui.QColor("#45D7FF"))
        connector_pen.setWidthF(0.45)
        connector_pen.setCosmetic(True)
        self._handle_connector.setPen(connector_pen)
        self._handle_connector.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self._handle_connector.setZValue(1001.0)
        self.rotation_handle = _TemplateRotationHandle(self)
        self.set_view_scale(1.0)

    @property
    def has_geometry(self) -> bool:
        return bool(self.visual_items)

    def placement(self) -> tuple[float, float, float]:
        center_scene = self.mapToScene(self._center_local)
        center_x, center_y = WorkspaceScene.scene_to_machine(center_scene)
        rotation = Transform.normalized_rotation(
            self._initial_rotation_deg - self.rotation()
        )
        return center_x, center_y, rotation

    def set_view_scale(self, scale: float) -> None:
        scale = max(abs(float(scale)), 1e-6)
        anchor = QtCore.QPointF(self._center_local.x(), self._geometry_bounds.top())
        handle_position = anchor + QtCore.QPointF(0.0, -self._HANDLE_OFFSET_PX / scale)
        self._handle_connector.setLine(QtCore.QLineF(anchor, handle_position))
        self.rotation_handle.setPos(handle_position)

    def begin_drag(self, scene_position: QtCore.QPointF) -> None:
        self._drag_start_scene = QtCore.QPointF(scene_position)
        self._drag_start_position = self.pos()
        self._drag_moved = False

    def update_drag(self, scene_position: QtCore.QPointF) -> None:
        if self._drag_start_scene is None:
            return
        delta = QtCore.QPointF(scene_position) - self._drag_start_scene
        if not delta.isNull():
            self._drag_moved = True
        self.setPos(self._drag_start_position + delta)
        if self._drag_moved:
            self._emit_edited()

    def finish_drag(self, scene_position: QtCore.QPointF) -> None:
        if self._drag_start_scene is None:
            return
        self.update_drag(scene_position)
        moved = self._drag_moved
        self._drag_start_scene = None
        self._drag_moved = False
        if not moved:
            return
        self._emit_committed()

    def begin_rotation(self, scene_position: QtCore.QPointF) -> None:
        self._rotation_last_angle_deg = self._pointer_angle(scene_position)
        self._rotation_start_group_deg = self.rotation()
        self._rotation_accumulated_deg = 0.0
        self._rotation_moved = False

    def update_rotation(self, scene_position: QtCore.QPointF) -> None:
        if self._rotation_last_angle_deg is None:
            return
        angle = self._pointer_angle(scene_position)
        increment = Transform.normalized_rotation(
            angle - self._rotation_last_angle_deg
        )
        self._rotation_last_angle_deg = angle
        if abs(increment) > 1e-12:
            self._rotation_moved = True
            self._rotation_accumulated_deg += increment
            self.setRotation(
                self._rotation_start_group_deg - self._rotation_accumulated_deg
            )
            self._emit_edited()

    def finish_rotation(self, scene_position: QtCore.QPointF) -> None:
        if self._rotation_last_angle_deg is None:
            return
        self.update_rotation(scene_position)
        moved = self._rotation_moved
        self._rotation_last_angle_deg = None
        self._rotation_moved = False
        if moved:
            self._emit_committed()

    def _pointer_angle(self, scene_position: QtCore.QPointF) -> float:
        center_scene = self.mapToScene(self._center_local)
        delta_x = scene_position.x() - center_scene.x()
        delta_y = -(scene_position.y() - center_scene.y())
        return math.degrees(math.atan2(delta_y, delta_x))

    def _emit_edited(self) -> None:
        self._edited_callback(*self.placement())

    def _emit_committed(self) -> None:
        self._committed_callback(*self.placement())



class RulerWidget(QtWidgets.QWidget):
    def __init__(
        self,
        view: "WorkspaceView",
        orientation: QtCore.Qt.Orientation,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.view = view
        self.orientation = orientation
        if orientation == QtCore.Qt.Orientation.Horizontal:
            self.setFixedHeight(28)
        else:
            self.setFixedWidth(36)
        self.view.zoomChanged.connect(lambda _: self.update())
        self.view.horizontalScrollBar().valueChanged.connect(lambda _: self.update())
        self.view.verticalScrollBar().valueChanged.connect(lambda _: self.update())

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#111920"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#31424D")))
        if self.orientation == QtCore.Qt.Orientation.Horizontal:
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        else:
            painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())

        visible = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        scale = max(abs(self.view.transform().m11()), 1e-6)
        if scale >= 8:
            minor = 1.0
        elif scale >= 2:
            minor = 5.0
        else:
            minor = 10.0
        major = minor * 5.0
        painter.setFont(QtGui.QFont(painter.font().family(), 7))
        tick_pen = QtGui.QPen(QtGui.QColor("#73828C"))
        text_pen = QtGui.QPen(QtGui.QColor("#AAB7C0"))

        if self.orientation == QtCore.Qt.Orientation.Horizontal:
            start = math.floor(visible.left() / minor) * minor
            value = start
            while value <= visible.right() + minor:
                screen = self.view.mapFromScene(QtCore.QPointF(value, 0)).x()
                is_major = abs(value / major - round(value / major)) < 1e-6
                painter.setPen(tick_pen)
                length = 10 if is_major else 5
                painter.drawLine(screen, self.height() - 1, screen, self.height() - 1 - length)
                if is_major:
                    painter.setPen(text_pen)
                    painter.drawText(screen + 2, 11, f"{value:g}")
                value += minor
        else:
            machine_y_min = -visible.bottom()
            machine_y_max = -visible.top()
            value = math.floor(machine_y_min / minor) * minor
            while value <= machine_y_max + minor:
                screen = self.view.mapFromScene(QtCore.QPointF(0, -value)).y()
                is_major = abs(value / major - round(value / major)) < 1e-6
                painter.setPen(tick_pen)
                length = 10 if is_major else 5
                painter.drawLine(self.width() - 1, screen, self.width() - 1 - length, screen)
                if is_major:
                    painter.save()
                    painter.setPen(text_pen)
                    painter.translate(10, screen - 2)
                    painter.rotate(-90)
                    painter.drawText(0, 0, f"{value:g}")
                    painter.restore()
                value += minor


class WorkspaceFrame(QtWidgets.QWidget):
    def __init__(
        self,
        view: "WorkspaceView",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.view = view
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        corner = QtWidgets.QWidget()
        corner.setFixedSize(36, 28)
        corner.setStyleSheet("background: #111920; border: 0;")
        layout.addWidget(corner, 0, 0)
        layout.addWidget(RulerWidget(view, QtCore.Qt.Orientation.Horizontal), 0, 1)
        layout.addWidget(RulerWidget(view, QtCore.Qt.Orientation.Vertical), 1, 0)
        layout.addWidget(view, 1, 1)

class WorkspaceView(QtWidgets.QGraphicsView):
    cursorPositionChanged = QtCore.Signal(float, float)
    selectionIdsChanged = QtCore.Signal(list)
    objectMoveCommitted = QtCore.Signal(str, object, object)
    templatePlacementEdited = QtCore.Signal(float, float, float)
    templatePlacementCommitted = QtCore.Signal(float, float, float)
    deleteRequested = QtCore.Signal()
    zoomChanged = QtCore.Signal(float)
    pointPicked = QtCore.Signal(float, float)

    def __init__(self, work_area: Bounds, parent: QtWidgets.QWidget | None = None) -> None:
        self.workspace_scene = WorkspaceScene(work_area)
        super().__init__(self.workspace_scene, parent)
        self.setObjectName("workspaceFrame")
        self.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.TextAntialiasing
            | QtGui.QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(
            QtWidgets.QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate
        )
        self.setTransformationAnchor(
            QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.RubberBandDrag)
        self.setMouseTracking(True)
        self.setBackgroundBrush(QtGui.QColor("#091015"))
        self._document: ProjectDocument | None = None
        self._items_by_id: dict[str, ObjectGraphicsItem] = {}
        self._camera_item = QtWidgets.QGraphicsPixmapItem()
        self._camera_item.setZValue(-500.0)
        self._camera_item.setOpacity(0.60)
        self._camera_item.setVisible(False)
        self.workspace_scene.addItem(self._camera_item)
        self._toolpath_items: list[QtWidgets.QGraphicsLineItem] = []
        self._trace_items: list[QtWidgets.QGraphicsItem] = []
        self._template_items: list[QtWidgets.QGraphicsItem] = []
        self._template_preview_item: _TemplatePreviewGraphicsItem | None = None
        self._template_rotation_handle: _TemplateRotationHandle | None = None
        self._point_pick_active = False
        self.snap_enabled = True
        self.snap_step_mm = 1.0
        self._panning = False
        self._pan_start = QtCore.QPoint()
        self._space_pan = False
        self._pan_button = QtCore.Qt.MouseButton.NoButton
        self.workspace_scene.selectionChanged.connect(self._emit_selection)
        self.zoomChanged.connect(self._template_zoom_changed)
        self.fit_work_area()

    @property
    def camera_opacity(self) -> float:
        return self._camera_item.opacity()

    def set_camera_opacity(self, value: float) -> None:
        self._camera_item.setOpacity(max(0.0, min(1.0, float(value))))

    def set_work_area(self, work_area: Bounds, *, fit: bool = True) -> None:
        self.workspace_scene.set_work_area(work_area)
        if fit:
            self.fit_work_area()

    def fit_work_area(self) -> None:
        area = self.workspace_scene.work_area
        rect = QtCore.QRectF(area.x_min, -area.y_max, area.width, area.height)
        self.fitInView(rect.adjusted(-5, -5, 5, 5), QtCore.Qt.AspectRatioMode.KeepAspectRatio)
        self.zoomChanged.emit(abs(self.transform().m11()))

    def fit_selection(self) -> None:
        selected = [
            item
            for item in self.workspace_scene.selectedItems()
            if isinstance(item, ObjectGraphicsItem)
        ]
        if not selected:
            self.fit_work_area()
            return
        rect = QtCore.QRectF()
        for item in selected:
            rect = rect.united(item.sceneBoundingRect())
        if rect.isEmpty():
            self.fit_work_area()
            return
        padding = max(3.0, min(rect.width(), rect.height()) * 0.12)
        self.fitInView(
            rect.adjusted(-padding, -padding, padding, padding),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        )
        self.zoomChanged.emit(abs(self.transform().m11()))

    def zoom_by(self, factor: float) -> None:
        current = abs(self.transform().m11())
        target = current * float(factor)
        if 0.08 <= target <= 80.0:
            self.scale(float(factor), float(factor))
            self.zoomChanged.emit(abs(self.transform().m11()))

    def zoom_in(self) -> None:
        self.zoom_by(1.18)

    def zoom_out(self) -> None:
        self.zoom_by(1.0 / 1.18)

    def set_camera_image(self, image: QtGui.QImage | None) -> None:
        if image is None or image.isNull():
            self._camera_item.setVisible(False)
            return
        pixmap = QtGui.QPixmap.fromImage(image)
        area = self.workspace_scene.work_area
        self._camera_item.setPixmap(pixmap)
        self._camera_item.setPos(area.x_min, -area.y_max)
        transform = QtGui.QTransform()
        transform.scale(area.width / pixmap.width(), area.height / pixmap.height())
        self._camera_item.setTransform(transform)
        self._camera_item.setVisible(True)



    def begin_point_pick(self) -> None:
        self._point_pick_active = True
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def cancel_point_pick(self) -> None:
        self._point_pick_active = False
        if not self._panning and not self._space_pan:
            self.unsetCursor()

    def clear_trace_preview(self) -> None:
        for item in self._trace_items:
            self.workspace_scene.removeItem(item)
        self._trace_items.clear()

    def set_trace_preview(
        self,
        detections: list[dict[str, Any]],
        selected_ids: list[str] | set[str] | None = None,
    ) -> None:
        self.clear_trace_preview()
        selected = set(selected_ids or [])
        for detection in detections:
            points = detection.get("contour_mm") or detection.get("box_mm") or []
            if len(points) < 2:
                continue
            path = QtGui.QPainterPath()
            first = self.workspace_scene.machine_to_scene(*points[0])
            path.moveTo(first)
            for point in points[1:]:
                path.lineTo(self.workspace_scene.machine_to_scene(*point))
            path.closeSubpath()
            item = QtWidgets.QGraphicsPathItem(path)
            is_selected = detection.get("id") in selected
            is_inferred = detection.get("source") == "inferred"
            if is_selected and not is_inferred:
                color = QtGui.QColor("#4FE36F")
            elif is_selected and is_inferred:
                color = QtGui.QColor("#E7B55C")
            elif is_inferred:
                color = QtGui.QColor("#D98A45")
            else:
                color = QtGui.QColor("#8998A3")
            pen = QtGui.QPen(color)
            pen.setWidthF(0.55 if is_selected else 0.35)
            pen.setCosmetic(True)
            if is_inferred:
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            item.setPen(pen)
            fill = QtGui.QColor(color)
            fill.setAlpha(28 if is_selected else 10)
            item.setBrush(fill)
            item.setZValue(260.0)
            item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            self.workspace_scene.addItem(item)
            self._trace_items.append(item)

            center = detection.get("center_mm")
            if center and len(center) == 2:
                label = QtWidgets.QGraphicsSimpleTextItem(str(detection.get("index", "")))
                label.setBrush(color)
                label.setFlag(
                    QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                    True,
                )
                position = self.workspace_scene.machine_to_scene(*center)
                label.setPos(position + QtCore.QPointF(1.5, -1.5))
                label.setZValue(261.0)
                label.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
                self.workspace_scene.addItem(label)
                self._trace_items.append(label)

    def clear_template_preview(self) -> None:
        if self._template_preview_item is not None:
            if self._template_preview_item.scene() is self.workspace_scene:
                self.workspace_scene.removeItem(self._template_preview_item)
            self._template_preview_item = None
            self._template_rotation_handle = None
        for item in self._template_items:
            if item.scene() is self.workspace_scene:
                self.workspace_scene.removeItem(item)
        self._template_items.clear()

    def set_template_preview(
        self,
        objects: list[SceneObject],
        detections: list[dict[str, Any]] | None = None,
        *,
        center_x_mm: float | None = None,
        center_y_mm: float | None = None,
        rotation_deg: float = 0.0,
    ) -> None:
        """Draw reviewed template geometry without adding it to the project."""

        self.clear_template_preview()
        observed_color = QtGui.QColor("#E7B55C")
        for detection in detections or []:
            points = detection.get("contour_mm") or detection.get("box_mm") or []
            if len(points) < 2:
                continue
            path = QtGui.QPainterPath()
            path.moveTo(self.workspace_scene.machine_to_scene(*points[0]))
            for point in points[1:]:
                path.lineTo(self.workspace_scene.machine_to_scene(*point))
            path.closeSubpath()
            item = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(observed_color)
            pen.setWidthF(0.35)
            pen.setCosmetic(True)
            item.setPen(pen)
            fill = QtGui.QColor(observed_color)
            fill.setAlpha(8)
            item.setBrush(fill)
            item.setZValue(279.0)
            item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            item.setToolTip("Observed camera feature")
            self.workspace_scene.addItem(item)
            self._template_items.append(item)

        if not objects:
            return
        bounds = objects[0].bounds()
        for scene_object in objects[1:]:
            bounds = bounds.union(scene_object.bounds())
        derived_x, derived_y = bounds.center
        preview = _TemplatePreviewGraphicsItem(
            objects,
            center_x_mm=derived_x if center_x_mm is None else float(center_x_mm),
            center_y_mm=derived_y if center_y_mm is None else float(center_y_mm),
            rotation_deg=float(rotation_deg),
            edited_callback=self.templatePlacementEdited.emit,
            committed_callback=self.templatePlacementCommitted.emit,
        )
        if not preview.has_geometry:
            return
        self.workspace_scene.addItem(preview)
        self._template_preview_item = preview
        self._template_rotation_handle = preview.rotation_handle
        self._template_items.extend(preview.visual_items)
        preview.set_view_scale(abs(self.transform().m11()))

    def _template_zoom_changed(self, scale: float) -> None:
        if self._template_preview_item is not None:
            self._template_preview_item.set_view_scale(scale)

    def clear_toolpath_preview(self) -> None:
        for item in self._toolpath_items:
            self.workspace_scene.removeItem(item)
        self._toolpath_items.clear()

    def set_toolpath_preview(self, gcode: str) -> None:
        self.clear_toolpath_preview()
        for segment in parse_gcode_segments(gcode):
            start = self.workspace_scene.machine_to_scene(segment.start_x, segment.start_y)
            end = self.workspace_scene.machine_to_scene(segment.end_x, segment.end_y)
            item = QtWidgets.QGraphicsLineItem(QtCore.QLineF(start, end))
            if segment.rapid:
                pen = QtGui.QPen(QtGui.QColor("#5CA9E7"))
                pen.setStyle(QtCore.Qt.PenStyle.DashLine)
                pen.setWidthF(0.30)
            elif segment.laser_on:
                pen = QtGui.QPen(QtGui.QColor("#E35D6A"))
                pen.setWidthF(0.50)
            else:
                pen = QtGui.QPen(QtGui.QColor("#E7B55C"))
                pen.setWidthF(0.35)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(300.0)
            item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            self.workspace_scene.addItem(item)
            self._toolpath_items.append(item)

    def set_document(self, document: ProjectDocument) -> None:
        selected = set(self.selected_object_ids())
        for item in self._items_by_id.values():
            self.workspace_scene.removeItem(item)
        self._items_by_id.clear()
        self._document = document
        area_changed = document.work_area != self.workspace_scene.work_area
        if area_changed:
            self.set_work_area(document.work_area, fit=True)
        layers = {layer.id: layer for layer in document.layers}
        for z_index, scene_object in enumerate(document.objects):
            layer = layers[scene_object.layer_id]
            item = ObjectGraphicsItem(
                scene_object,
                layer,
                move_callback=self._item_move_finished,
            )
            item.setZValue(float(z_index))
            self.workspace_scene.addItem(item)
            self._items_by_id[scene_object.id] = item
            if scene_object.id in selected:
                item.setSelected(True)

    def refresh_object(self, object_id: str) -> None:
        if self._document is None:
            return
        item = self._items_by_id.get(object_id)
        if item is None:
            self.set_document(self._document)
            return
        scene_object = self._document.get_object(object_id)
        layer = self._document.get_layer(scene_object.layer_id)
        item.apply_model(scene_object, layer)


    def set_snap_enabled(self, enabled: bool) -> None:
        self.snap_enabled = bool(enabled)

    def set_snap_step(self, step_mm: float) -> None:
        self.snap_step_mm = max(0.001, float(step_mm))

    def _item_move_finished(
        self,
        object_id: str,
        before: tuple[float, float],
        after: tuple[float, float],
    ) -> None:
        if self.snap_enabled:
            step = self.snap_step_mm
            after = (
                round(after[0] / step) * step,
                round(after[1] / step) * step,
            )
            item = self._items_by_id.get(object_id)
            if item is not None:
                item.setPos(after[0], -after[1])
        self.objectMoveCommitted.emit(object_id, before, after)

    def selected_object_ids(self) -> list[str]:
        return [
            item.object_id
            for item in self.workspace_scene.selectedItems()
            if isinstance(item, ObjectGraphicsItem)
        ]

    def select_objects(self, object_ids: list[str]) -> None:
        wanted = set(object_ids)
        for object_id, item in self._items_by_id.items():
            item.setSelected(object_id in wanted)

    def _emit_selection(self) -> None:
        # Qt may deliver a final selectionChanged while tearing the owned
        # scene down; its Python wrapper can already outlive the C++ scene.
        try:
            selected = self.selected_object_ids()
        except RuntimeError:
            return
        self.selectionIdsChanged.emit(selected)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        self.zoom_by(1.18 if event.angleDelta().y() > 0 else 1.0 / 1.18)
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
        if (
            self._point_pick_active
            and event.button() == QtCore.Qt.MouseButton.LeftButton
        ):
            scene_point = self.mapToScene(event.position().toPoint())
            x_mm, y_mm = self.workspace_scene.scene_to_machine(scene_point)
            area = self.workspace_scene.work_area
            if area.contains(x_mm, y_mm):
                self._point_pick_active = False
                self.unsetCursor()
                self.pointPicked.emit(x_mm, y_mm)
            event.accept()
            return
        should_pan = (
            event.button() == QtCore.Qt.MouseButton.MiddleButton
            or (
                event.button() == QtCore.Qt.MouseButton.LeftButton
                and self._space_pan
            )
        )
        if should_pan:
            self._panning = True
            self._pan_button = event.button()
            self._pan_start = event.position().toPoint()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        scene_point = self.mapToScene(event.position().toPoint())
        x_mm, y_mm = self.workspace_scene.scene_to_machine(scene_point)
        self.cursorPositionChanged.emit(x_mm, y_mm)
        if self._panning:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._panning and event.button() == self._pan_button:
            self._panning = False
            self._pan_button = QtCore.Qt.MouseButton.NoButton
            self.setCursor(
                QtCore.Qt.CursorShape.OpenHandCursor
                if self._space_pan
                else QtCore.Qt.CursorShape.ArrowCursor
            )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan = True
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.key() in {QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace}:
            self.deleteRequested.emit()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key.Key_F:
            self.fit_work_area()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan = False
            if not self._panning:
                self.unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)
