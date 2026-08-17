from __future__ import annotations

import math
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..gcode.job_plan import JobPlan
from ..gcode.preview import parse_gcode_segments
from ..project import (
    Bounds,
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    SceneObject,
    Transform,
    read_raster_asset_payload,
)
from .qt import require_qt
from .theme import DEFAULT_CAMERA_OVERLAY_OPACITY, DRAFTING_COLORS

QtCore, QtGui, QtWidgets = require_qt()


class _TraceIndexBadge(QtWidgets.QGraphicsItem):
    """Fixed-screen-size detection number that remains legible over camera pixels."""

    def __init__(self, text: str, accent: QtGui.QColor) -> None:
        super().__init__()
        self.text = str(text)
        self.accent = QtGui.QColor(accent)
        self.setFlag(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(-15.0, -12.0, 30.0, 24.0)

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        del option, widget
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        fill = QtGui.QColor("#151A1E")
        fill.setAlpha(235)
        pen = QtGui.QPen(self.accent)
        pen.setWidthF(2.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(self.boundingRect(), 5.0, 5.0)

        font = painter.font()
        font.setBold(True)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QtGui.QColor("#FFFFFF"))
        painter.drawText(
            self.boundingRect(),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            self.text,
        )


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
        painter.fillRect(rect, QtGui.QColor(DRAFTING_COLORS["outside"]))
        work_rect = QtCore.QRectF(
            self.work_area.x_min,
            -self.work_area.y_max,
            self.work_area.width,
            self.work_area.height,
        )
        painter.fillRect(work_rect, QtGui.QColor(DRAFTING_COLORS["bed"]))

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

        minor_pen = QtGui.QPen(QtGui.QColor(DRAFTING_COLORS["minor_grid"]))
        minor_pen.setCosmetic(True)
        major_pen = QtGui.QPen(QtGui.QColor(DRAFTING_COLORS["major_grid"]))
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

        border = QtGui.QPen(QtGui.QColor("#555B60"))
        border.setWidthF(0.35)
        border.setCosmetic(True)
        painter.setPen(border)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawRect(work_rect)

        origin_pen = QtGui.QPen(QtGui.QColor("#D23A3A"))
        origin_pen.setWidthF(0.5)
        origin_pen.setCosmetic(True)
        painter.setPen(origin_pen)
        origin = self.machine_to_scene(self.work_area.x_min, self.work_area.y_min)
        painter.drawLine(origin + QtCore.QPointF(0, -5), origin + QtCore.QPointF(0, 5))
        painter.drawLine(origin + QtCore.QPointF(-5, 0), origin + QtCore.QPointF(5, 0))


class ObjectGraphicsItem(QtWidgets.QGraphicsPathItem):
    _IMAGE_CACHE_LIMIT = 8
    _IMAGE_CACHE_MAX_BYTES = 64 * 1024 * 1024
    _IMAGE_PREVIEW_MAX_PIXELS = 4_000_000
    _project_raster_source_count = 1
    _image_cache: OrderedDict[
        tuple[str, str], QtGui.QImage
    ] = OrderedDict()

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
        self._raster_image: QtGui.QImage | None = None
        self._raster_source = ""
        self._raster_identity_sha: str | None = None
        self._raster_source_size: tuple[int, int] | None = None
        self._raster_asset_reference: str | None = None
        self.setFlags(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.apply_model(scene_object, layer)

    @classmethod
    def set_project_raster_source_count(cls, count: int) -> None:
        cls._project_raster_source_count = max(1, int(count))

    @classmethod
    def _preview_pixel_budget(cls) -> int:
        source_bucket = 1 << (cls._project_raster_source_count - 1).bit_length()
        project_share = (
            cls._IMAGE_CACHE_MAX_BYTES
            // source_bucket
            // 4
        )
        return max(1, min(cls._IMAGE_PREVIEW_MAX_PIXELS, project_share))

    @classmethod
    def _prune_image_cache(cls) -> None:
        while cls._image_cache and (
            len(cls._image_cache)
            > max(cls._IMAGE_CACHE_LIMIT, cls._project_raster_source_count)
            or sum(item.sizeInBytes() for item in cls._image_cache.values())
            > cls._IMAGE_CACHE_MAX_BYTES
        ):
            cls._image_cache.popitem(last=False)

    @classmethod
    def rebudget_raster_items(
        cls,
        items: list[ObjectGraphicsItem],
    ) -> None:
        groups: dict[tuple[str, str], list[ObjectGraphicsItem]] = {}
        for item in items:
            identity = item.raster_preview_identity
            if identity is not None and item._raster_image is not None:
                groups.setdefault(identity, []).append(item)
        pixel_budget = cls._preview_pixel_budget()
        for key, group in groups.items():
            image = group[0]._raster_image
            if image is None or image.width() * image.height() <= pixel_budget:
                cls._image_cache[key] = image
                continue
            scale = math.sqrt(pixel_budget / (image.width() * image.height()))
            resized = image.scaled(
                max(1, int(math.floor(image.width() * scale))),
                max(1, int(math.floor(image.height() * scale))),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            cls._image_cache[key] = resized
            for item in group:
                item._raster_image = resized
                item.update()
        cls._prune_image_cache()

    @classmethod
    def retain_item_cache(
        cls,
        items: list[ObjectGraphicsItem],
    ) -> None:
        retained: OrderedDict[tuple[str, str], QtGui.QImage] = OrderedDict()
        for item in items:
            identity = item.raster_preview_identity
            if identity is not None and item._raster_image is not None:
                retained[identity] = item._raster_image
        cls._image_cache = retained
        cls._prune_image_cache()

    @classmethod
    def _load_raster_snapshot(
        cls,
        asset: Any,
        *,
        use_cache: bool = True,
    ) -> tuple[
        str,
        QtGui.QImage | None,
        str | None,
        tuple[int, int] | None,
    ]:
        source = str(asset or "").strip()
        if not source:
            return "", None, None, None
        try:
            payload = read_raster_asset_payload(source)
        except ValueError:
            return source, None, None, None
        identity = payload.identity
        metadata = payload.metadata
        resolved = identity.path
        source_size_px = (identity.width, identity.height)
        key = (resolved, identity.sha256)
        cached = cls._image_cache.get(key) if use_cache else None
        if cached is not None:
            cls._image_cache.move_to_end(key)
            return resolved, cached, identity.sha256, source_size_px

        encoded = QtCore.QByteArray(payload.encoded)
        buffer = QtCore.QBuffer()
        buffer.setData(encoded)
        if not buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly):
            return resolved, None, None, source_size_px
        reader = QtGui.QImageReader(buffer)
        reader.setAutoTransform(True)
        source_size = QtCore.QSize(metadata.raw_width, metadata.raw_height)
        source_pixels = identity.width * identity.height
        preview_pixel_budget = cls._preview_pixel_budget()
        if source_pixels > preview_pixel_budget:
            scale = math.sqrt(preview_pixel_budget / source_pixels)
            reader.setScaledSize(
                QtCore.QSize(
                    max(1, int(math.floor(source_size.width() * scale))),
                    max(1, int(math.floor(source_size.height() * scale))),
                )
            )
        image = reader.read()
        if image.isNull():
            return resolved, None, None, source_size_px
        image = image.convertToFormat(
            QtGui.QImage.Format.Format_ARGB32_Premultiplied
        )
        cls._image_cache[key] = image
        cls._image_cache.move_to_end(key)
        cls._prune_image_cache()
        return resolved, image, identity.sha256, source_size_px

    @classmethod
    def _load_raster_image(cls, asset: Any) -> tuple[str, QtGui.QImage | None]:
        source, image, _identity_sha, _source_size = cls._load_raster_snapshot(
            asset
        )
        return source, image

    @property
    def raster_preview_identity(self) -> tuple[str, str] | None:
        if not self._raster_source or self._raster_identity_sha is None:
            return None
        return self._raster_source, self._raster_identity_sha

    def refresh_raster_snapshot(self) -> bool:
        reference = self._raster_asset_reference
        if reference is None:
            return False
        (
            self._raster_source,
            self._raster_image,
            self._raster_identity_sha,
            self._raster_source_size,
        ) = self._load_raster_snapshot(reference)
        self.update()
        return self._raster_image is not None

    @staticmethod
    def _path_for_object(
        scene_object: SceneObject,
        transform: Transform | None = None,
    ) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        kind = scene_object.kind
        object_transform = transform or scene_object.transform
        if kind == ObjectKind.RECTANGLE:
            width = object_transform.width_mm
            height = object_transform.height_mm
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
            width = object_transform.width_mm
            height = object_transform.height_mm
            path.addEllipse(QtCore.QRectF(-width / 2.0, -height / 2.0, width, height))
        elif kind == ObjectKind.LINE:
            points = scene_object.geometry.get("points", [[-0.5, 0.0], [0.5, 0.0]])
            width = object_transform.width_mm
            height = object_transform.height_mm
            start = QtCore.QPointF(points[0][0] * width, -points[0][1] * height)
            end = QtCore.QPointF(points[1][0] * width, -points[1][1] * height)
            path.moveTo(start)
            path.lineTo(end)
        elif kind in {ObjectKind.PATH, ObjectKind.POLYGON}:
            width = object_transform.width_mm
            height = object_transform.height_mm
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
            font.setPointSizeF(max(1.0, object_transform.height_mm * 2.2))
            path.addText(
                QtCore.QPointF(
                    -object_transform.width_mm / 2.0,
                    object_transform.height_mm / 3.0,
                ),
                font,
                str(scene_object.geometry.get("text", "Text")),
            )
        elif kind == ObjectKind.IMAGE:
            path.addRect(
                QtCore.QRectF(
                    -object_transform.width_mm / 2.0,
                    -object_transform.height_mm / 2.0,
                    object_transform.width_mm,
                    object_transform.height_mm,
                )
            )
        return path

    def apply_model(self, scene_object: SceneObject, layer: OperationLayer) -> None:
        self._apply_visual(scene_object, layer, scene_object.transform)
        self.setVisible(scene_object.visible and layer.visible)
        self.setEnabled(not scene_object.locked)

    def preview_transform(
        self,
        scene_object: SceneObject,
        layer: OperationLayer,
        transform: Transform,
    ) -> None:
        """Render an uncommitted transform without changing the project model."""

        self._apply_visual(scene_object, layer, transform)

    def _apply_visual(
        self,
        scene_object: SceneObject,
        layer: OperationLayer,
        transform: Transform,
    ) -> None:
        self._syncing = True
        try:
            if scene_object.kind == ObjectKind.IMAGE:
                asset_reference = str(
                    scene_object.geometry.get("asset") or ""
                ).strip()
                if asset_reference != self._raster_asset_reference:
                    (
                        self._raster_source,
                        self._raster_image,
                        self._raster_identity_sha,
                        self._raster_source_size,
                    ) = (
                        self._load_raster_snapshot(asset_reference)
                    )
                    self._raster_asset_reference = asset_reference
            else:
                self._raster_source = ""
                self._raster_image = None
                self._raster_identity_sha = None
                self._raster_source_size = None
                self._raster_asset_reference = None
            self.setPath(self._path_for_object(scene_object, transform))
            color = QtGui.QColor(layer.color)
            pen = QtGui.QPen(color)
            pen.setWidthF(0.35)
            pen.setCosmetic(True)
            self.setPen(pen)
            if scene_object.kind == ObjectKind.IMAGE:
                self.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            elif layer.mode in {LayerMode.FILL, LayerMode.RASTER}:
                fill = QtGui.QColor(color)
                fill.setAlpha(65 if layer.mode == LayerMode.FILL else 28)
                self.setBrush(fill)
            else:
                self.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            self.setPos(transform.x_mm, -transform.y_mm)
            self.setRotation(-transform.rotation_deg)
            item_transform = QtGui.QTransform()
            item_transform.scale(
                -1.0 if transform.mirror_x else 1.0,
                -1.0 if transform.mirror_y else 1.0,
            )
            self.setTransform(item_transform)
            self.setToolTip(
                f"{scene_object.name}\n"
                f"X {transform.x_mm:.2f}  Y {transform.y_mm:.2f}\n"
                f"{transform.width_mm:.2f} × {transform.height_mm:.2f} mm"
                + (
                    f"\n{self._raster_source}"
                    if self._raster_image is not None
                    else "\nRaster source is missing or unreadable"
                    if scene_object.kind == ObjectKind.IMAGE
                    else ""
                )
            )
        finally:
            self._syncing = False

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        if self._raster_image is not None and not self._raster_image.isNull():
            painter.save()
            painter.setRenderHint(
                QtGui.QPainter.RenderHint.SmoothPixmapTransform,
                True,
            )
            painter.drawImage(self.path().boundingRect(), self._raster_image)
            painter.restore()
        super().paint(painter, option, widget)

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


class _ObjectResizeHandle(QtWidgets.QGraphicsEllipseItem):
    """Fixed-pixel corner handle for one selected project object."""

    _RADIUS_PX = 5.5

    def __init__(
        self,
        owner: _ObjectTransformOverlay,
        corner: str,
        cursor: QtCore.Qt.CursorShape,
    ) -> None:
        radius = self._RADIUS_PX
        super().__init__(
            QtCore.QRectF(-radius, -radius, radius * 2.0, radius * 2.0),
            owner,
        )
        self._owner = owner
        self.corner = corner
        self.setFlag(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        pen = QtGui.QPen(QtGui.QColor("#152027"))
        pen.setWidthF(1.25)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QtGui.QColor("#F4F7F9"))
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.LeftButton)
        self.setCursor(cursor)
        self.setToolTip("Drag to resize the selected object")
        self.setZValue(2.0)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._owner.begin_resize(self.corner, event.scenePos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self._owner.update_resize(event.scenePos())
        event.accept()

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._owner.finish_resize(event.scenePos())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ObjectRotationHandle(QtWidgets.QGraphicsEllipseItem):
    """Fixed-pixel rotation handle for one selected project object."""

    _RADIUS_PX = 6.5

    def __init__(self, owner: _ObjectTransformOverlay) -> None:
        radius = self._RADIUS_PX
        super().__init__(
            QtCore.QRectF(-radius, -radius, radius * 2.0, radius * 2.0),
            owner,
        )
        self._owner = owner
        self.setFlag(
            QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        pen = QtGui.QPen(QtGui.QColor("#FFF2C9"))
        pen.setWidthF(1.25)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QtGui.QColor("#E7B55C"))
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.LeftButton)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setToolTip("Drag to rotate; hold Shift to snap to 15°")
        self.setZValue(2.0)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._owner.begin_rotation(event.scenePos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        self._owner.update_rotation(event.scenePos(), event.modifiers())
        event.accept()

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._owner.finish_rotation(event.scenePos(), event.modifiers())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ObjectTransformOverlay(QtWidgets.QGraphicsRectItem):
    """Selection outline and direct transform handles for one project object."""

    _HANDLE_OFFSET_PX = 24.0
    _MINIMUM_SIZE_MM = 0.1
    _ROTATION_SNAP_DEG = 15.0
    _CORNER_SIGNS = {
        "top_left": (-1.0, 1.0),
        "top_right": (1.0, 1.0),
        "bottom_right": (1.0, -1.0),
        "bottom_left": (-1.0, -1.0),
    }

    def __init__(
        self,
        view: WorkspaceView,
        object_id: str,
        transform: Transform,
    ) -> None:
        super().__init__()
        self._view = view
        self.object_id = object_id
        self._display_transform = transform.copy()
        self._interaction_before: Transform | None = None
        self._active_corner: str | None = None
        self._resize_start_scene: QtCore.QPointF | None = None
        self._resize_moved = False
        self._rotation_start_pointer_deg: float | None = None
        self._changed = False
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self.setZValue(400.0)
        outline_pen = QtGui.QPen(QtGui.QColor("#168A79"))
        outline_pen.setWidthF(0.8)
        outline_pen.setCosmetic(True)
        outline_pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        self.setPen(outline_pen)
        self.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        self.resize_handles = {
            "top_left": _ObjectResizeHandle(
                self,
                "top_left",
                QtCore.Qt.CursorShape.SizeFDiagCursor,
            ),
            "top_right": _ObjectResizeHandle(
                self,
                "top_right",
                QtCore.Qt.CursorShape.SizeBDiagCursor,
            ),
            "bottom_right": _ObjectResizeHandle(
                self,
                "bottom_right",
                QtCore.Qt.CursorShape.SizeFDiagCursor,
            ),
            "bottom_left": _ObjectResizeHandle(
                self,
                "bottom_left",
                QtCore.Qt.CursorShape.SizeBDiagCursor,
            ),
        }
        self._rotation_connector = QtWidgets.QGraphicsLineItem(self)
        connector_pen = QtGui.QPen(QtGui.QColor("#E7B55C"))
        connector_pen.setWidthF(0.7)
        connector_pen.setCosmetic(True)
        self._rotation_connector.setPen(connector_pen)
        self._rotation_connector.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self._rotation_connector.setZValue(1.0)
        self.rotation_handle = _ObjectRotationHandle(self)
        self.apply_transform(transform)
        self.set_view_scale(abs(view.transform().m11()))

    @property
    def display_transform(self) -> Transform:
        return self._display_transform.copy()

    def apply_transform(self, transform: Transform) -> None:
        self._display_transform = transform.copy()
        half_width = transform.width_mm / 2.0
        half_height = transform.height_mm / 2.0
        self.setRect(-half_width, -half_height, transform.width_mm, transform.height_mm)
        self.setPos(transform.x_mm, -transform.y_mm)
        self.setRotation(-transform.rotation_deg)
        self.resize_handles["top_left"].setPos(-half_width, -half_height)
        self.resize_handles["top_right"].setPos(half_width, -half_height)
        self.resize_handles["bottom_right"].setPos(half_width, half_height)
        self.resize_handles["bottom_left"].setPos(-half_width, half_height)
        self.set_view_scale(abs(self._view.transform().m11()))

    def set_view_scale(self, scale: float) -> None:
        scale = max(abs(float(scale)), 1e-6)
        anchor = QtCore.QPointF(0.0, self.rect().top())
        handle_position = anchor + QtCore.QPointF(0.0, -self._HANDLE_OFFSET_PX / scale)
        self._rotation_connector.setLine(QtCore.QLineF(anchor, handle_position))
        self.rotation_handle.setPos(handle_position)

    def begin_resize(self, corner: str, scene_position: QtCore.QPointF) -> None:
        if corner not in self._CORNER_SIGNS:
            return
        self._interaction_before = self._display_transform.copy()
        self._active_corner = corner
        self._resize_start_scene = QtCore.QPointF(scene_position)
        self._resize_moved = False
        self._changed = False

    def update_resize(self, scene_position: QtCore.QPointF) -> None:
        before = self._interaction_before
        corner = self._active_corner
        if before is None or corner is None:
            return
        if self._resize_start_scene is not None:
            movement = QtCore.QLineF(self._resize_start_scene, scene_position).length()
            if movement > 1e-9:
                self._resize_moved = True
        if not self._resize_moved:
            return
        pointer_x, pointer_y = WorkspaceScene.scene_to_machine(scene_position)
        if self._view.snap_enabled:
            step = self._view.snap_step_mm
            pointer_x = round(pointer_x / step) * step
            pointer_y = round(pointer_y / step) * step

        angle = math.radians(before.rotation_deg)
        cosine = math.cos(angle)
        sine = math.sin(angle)
        delta_x = pointer_x - before.x_mm
        delta_y = pointer_y - before.y_mm
        pointer_local_x = delta_x * cosine + delta_y * sine
        pointer_local_y = -delta_x * sine + delta_y * cosine
        sign_x, sign_y = self._CORNER_SIGNS[corner]
        fixed_x = -sign_x * before.width_mm / 2.0
        fixed_y = -sign_y * before.height_mm / 2.0
        if sign_x > 0.0:
            moving_x = max(pointer_local_x, fixed_x + self._MINIMUM_SIZE_MM)
        else:
            moving_x = min(pointer_local_x, fixed_x - self._MINIMUM_SIZE_MM)
        if sign_y > 0.0:
            moving_y = max(pointer_local_y, fixed_y + self._MINIMUM_SIZE_MM)
        else:
            moving_y = min(pointer_local_y, fixed_y - self._MINIMUM_SIZE_MM)

        local_center_x = (fixed_x + moving_x) / 2.0
        local_center_y = (fixed_y + moving_y) / 2.0
        center_x = before.x_mm + local_center_x * cosine - local_center_y * sine
        center_y = before.y_mm + local_center_x * sine + local_center_y * cosine
        updated = before.copy(
            x_mm=center_x,
            y_mm=center_y,
            width_mm=abs(moving_x - fixed_x),
            height_mm=abs(moving_y - fixed_y),
        )
        self._preview(updated)

    def finish_resize(self, scene_position: QtCore.QPointF) -> None:
        if self._interaction_before is None:
            return
        self.update_resize(scene_position)
        self._finish_interaction()

    def begin_rotation(self, scene_position: QtCore.QPointF) -> None:
        self._interaction_before = self._display_transform.copy()
        self._rotation_start_pointer_deg = self._pointer_angle(scene_position)
        self._active_corner = None
        self._resize_start_scene = None
        self._resize_moved = False
        self._changed = False

    def update_rotation(
        self,
        scene_position: QtCore.QPointF,
        modifiers: QtCore.Qt.KeyboardModifier = QtCore.Qt.KeyboardModifier.NoModifier,
    ) -> None:
        before = self._interaction_before
        start_angle = self._rotation_start_pointer_deg
        if before is None or start_angle is None:
            return
        pointer_angle = self._pointer_angle(scene_position)
        delta = Transform.normalized_rotation(pointer_angle - start_angle)
        rotation = Transform.normalized_rotation(before.rotation_deg + delta)
        if modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier:
            rotation = Transform.normalized_rotation(
                round(rotation / self._ROTATION_SNAP_DEG) * self._ROTATION_SNAP_DEG
            )
        self._preview(before.copy(rotation_deg=rotation))

    def finish_rotation(
        self,
        scene_position: QtCore.QPointF,
        modifiers: QtCore.Qt.KeyboardModifier = QtCore.Qt.KeyboardModifier.NoModifier,
    ) -> None:
        if self._interaction_before is None:
            return
        self.update_rotation(scene_position, modifiers)
        self._finish_interaction()

    def _pointer_angle(self, scene_position: QtCore.QPointF) -> float:
        pointer_x, pointer_y = WorkspaceScene.scene_to_machine(scene_position)
        return math.degrees(
            math.atan2(
                pointer_y - self._display_transform.y_mm,
                pointer_x - self._display_transform.x_mm,
            )
        )

    def _preview(self, transform: Transform) -> None:
        before = self._interaction_before
        if before is None:
            return
        self._changed = transform.to_dict() != before.to_dict()
        self.apply_transform(transform)
        self._view._preview_object_transform(self.object_id, transform)

    def _finish_interaction(self) -> None:
        before = self._interaction_before
        after = self._display_transform.copy()
        changed = self._changed
        self._interaction_before = None
        self._active_corner = None
        self._resize_start_scene = None
        self._resize_moved = False
        self._rotation_start_pointer_deg = None
        self._changed = False
        if before is not None and changed:
            self._view.objectTransformCommitted.emit(self.object_id, before, after)


class _TemplatePreviewDragSurface(QtWidgets.QGraphicsPathItem):
    """Transparent hit target that moves one rigid template preview."""

    def __init__(
        self,
        path: QtGui.QPainterPath,
        owner: _TemplatePreviewGraphicsItem,
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

    def __init__(self, owner: _TemplatePreviewGraphicsItem) -> None:
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
            pen.setWidthF(1.5)
            pen.setCosmetic(True)
            pen.setStyle(QtCore.Qt.PenStyle.SolidLine)
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
        outline_pen.setStyle(QtCore.Qt.PenStyle.DotLine)
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
        view: WorkspaceView,
        orientation: QtCore.Qt.Orientation,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.view = view
        self.orientation = orientation
        if orientation == QtCore.Qt.Orientation.Horizontal:
            self.setFixedHeight(24)
        else:
            self.setFixedWidth(30)
        self.view.zoomChanged.connect(lambda _: self.update())
        self.view.horizontalScrollBar().valueChanged.connect(lambda _: self.update())
        self.view.verticalScrollBar().valueChanged.connect(lambda _: self.update())

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.fillRect(
            self.rect(), QtGui.QColor(DRAFTING_COLORS["ruler_background"])
        )
        painter.setPen(QtGui.QPen(QtGui.QColor(DRAFTING_COLORS["ruler_border"])))
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
        tick_pen = QtGui.QPen(QtGui.QColor("#8E959A"))
        text_pen = QtGui.QPen(QtGui.QColor(DRAFTING_COLORS["ruler_text"]))

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
        view: WorkspaceView,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.view = view
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        corner = QtWidgets.QWidget()
        corner.setFixedSize(30, 24)
        corner.setStyleSheet(
            "background: #F5F5F5; border-right: 1px solid #B7BDC1; "
            "border-bottom: 1px solid #B7BDC1;"
        )
        layout.addWidget(corner, 0, 0)
        layout.addWidget(RulerWidget(view, QtCore.Qt.Orientation.Horizontal), 0, 1)
        layout.addWidget(RulerWidget(view, QtCore.Qt.Orientation.Vertical), 1, 0)
        layout.addWidget(view, 1, 1)


class _WorkspaceOverlayLegend(QtWidgets.QWidget):
    """Small on-canvas key for transient review geometry."""

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self._entries: list[tuple[str, str, QtCore.Qt.PenStyle]] = []
        self._drag_offset: QtCore.QPoint | None = None
        self._preferred_position: QtCore.QPoint | None = None
        self._user_positioned = False
        self.setObjectName("workspaceOverlayLegend")
        self.setAccessibleName("Canvas overlay key")
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self.hide()

    @property
    def entries(self) -> tuple[tuple[str, str, QtCore.Qt.PenStyle], ...]:
        return tuple(self._entries)

    def set_entries(
        self,
        entries: list[tuple[str, str, QtCore.Qt.PenStyle]],
    ) -> None:
        self._entries = list(entries)
        labels = "\n".join(label for label, _, _ in self._entries)
        self.setToolTip(f"Drag to reposition.\n{labels}".rstrip())
        self.resize(self.sizeHint())
        self.setVisible(bool(self._entries))
        self.update()

    def position_in_parent(self, default: QtCore.QPoint) -> None:
        """Keep a dragged key visible, or place an untouched key at its default."""

        target = (
            self._preferred_position
            if self._user_positioned and self._preferred_position is not None
            else default
        )
        bounded = self._bounded_position(target)
        self._preferred_position = bounded if self._user_positioned else None
        self.move(bounded)

    def _bounded_position(self, target: QtCore.QPoint) -> QtCore.QPoint:
        parent = self.parentWidget()
        if parent is None:
            return target
        return QtCore.QPoint(
            max(0, min(target.x(), parent.width() - self.width())),
            max(0, min(target.y(), parent.height() - self.height())),
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_offset = event.position().toPoint()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if (
            self._drag_offset is not None
            and event.buttons() & QtCore.Qt.MouseButton.LeftButton
        ):
            parent = self.parentWidget()
            if parent is not None:
                pointer = parent.mapFromGlobal(event.globalPosition().toPoint())
                target = self._bounded_position(pointer - self._drag_offset)
                self._preferred_position = target
                self.move(target)
                self._user_positioned = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and self._drag_offset is not None
        ):
            self._drag_offset = None
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def sizeHint(self) -> QtCore.QSize:
        metrics = self.fontMetrics()
        labels = ["Overlay key", *(label for label, _, _ in self._entries)]
        text_width = max(metrics.horizontalAdvance(label) for label in labels)
        return QtCore.QSize(max(164, text_width + 58), 29 + len(self._entries) * 21)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        if not self._entries:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        panel = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QtGui.QPen(QtGui.QColor(90, 101, 108, 225), 1.0))
        painter.setBrush(QtGui.QColor(24, 29, 33, 235))
        painter.drawRoundedRect(panel, 3.0, 3.0)

        heading_font = QtGui.QFont(self.font())
        heading_font.setBold(True)
        painter.setFont(heading_font)
        painter.setPen(QtGui.QColor("#F3F5F6"))
        painter.drawText(
            QtCore.QRect(9, 4, self.width() - 18, 18),
            QtCore.Qt.AlignmentFlag.AlignLeft
            | QtCore.Qt.AlignmentFlag.AlignVCenter,
            "Overlay key",
        )

        painter.setFont(self.font())
        for index, (label, color, style) in enumerate(self._entries):
            y = 29 + index * 21
            pen = QtGui.QPen(QtGui.QColor(color), 2.0)
            pen.setStyle(style)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(10, y + 7, 39, y + 7)
            painter.setPen(QtGui.QColor("#E8ECEE"))
            painter.drawText(
                QtCore.QRect(47, y - 2, self.width() - 56, 18),
                QtCore.Qt.AlignmentFlag.AlignLeft
                | QtCore.Qt.AlignmentFlag.AlignVCenter,
                label,
            )


class WorkspaceView(QtWidgets.QGraphicsView):
    cursorPositionChanged = QtCore.Signal(float, float)
    selectionIdsChanged = QtCore.Signal(list)
    objectMoveCommitted = QtCore.Signal(str, object, object)
    objectTransformCommitted = QtCore.Signal(str, object, object)
    templatePlacementEdited = QtCore.Signal(float, float, float)
    templatePlacementCommitted = QtCore.Signal(float, float, float)
    deleteRequested = QtCore.Signal()
    zoomChanged = QtCore.Signal(float)
    pointPicked = QtCore.Signal(float, float)
    creationToolChanged = QtCore.Signal(str)
    rectangleDraftChanged = QtCore.Signal(object)
    rectangleDrawCommitted = QtCore.Signal(float, float, float, float)

    _MINIMUM_DRAW_SIZE_MM = 0.1
    _DRAW_SIZE_EPSILON_MM = 1e-9

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
        # LightBurn-style drafting uses direct pan/zoom instead of permanent
        # canvas scroll bars. The underlying bars remain available to the pan
        # implementation even while their chrome is hidden.
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setBackgroundBrush(QtGui.QColor(DRAFTING_COLORS["outside"]))
        self._document: ProjectDocument | None = None
        self._items_by_id: dict[str, ObjectGraphicsItem] = {}
        self._object_transform_overlay: _ObjectTransformOverlay | None = None
        self._syncing_document = False
        self._camera_item = QtWidgets.QGraphicsPixmapItem()
        # OpenCV and BedMapper coordinates address pixel centers, while Qt's
        # default pixmap bounds place the first center at (0.5, 0.5). Keep the
        # displayed raster registered to the same machine coordinates used by
        # vision results instead of introducing a half-pixel right/down shift.
        self._camera_item.setOffset(-0.5, -0.5)
        self._camera_item.setZValue(-500.0)
        self._camera_item.setOpacity(DEFAULT_CAMERA_OVERLAY_OPACITY)
        self._camera_item.setVisible(False)
        self.workspace_scene.addItem(self._camera_item)
        self._camera_backing_item = QtWidgets.QGraphicsRectItem()
        self._camera_backing_item.setPen(QtCore.Qt.PenStyle.NoPen)
        self._camera_backing_item.setBrush(
            QtGui.QColor(DRAFTING_COLORS["outside"])
        )
        self._camera_backing_item.setZValue(-501.0)
        self._camera_backing_item.setVisible(False)
        self.workspace_scene.addItem(self._camera_backing_item)
        self._camera_image_area: Bounds | None = None
        self._test_frame_badge = QtWidgets.QLabel("TEST IMAGE · FROZEN", self.viewport())
        self._test_frame_badge.setObjectName("testImageBadge")
        self._test_frame_badge.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        self._test_frame_badge.setToolTip("Frozen corrected simulation frame")
        self._test_frame_badge.adjustSize()
        self._test_frame_badge.hide()
        self._overlay_legend = _WorkspaceOverlayLegend(self.viewport())
        self._overlay_entries: dict[
            str,
            list[tuple[str, str, QtCore.Qt.PenStyle]],
        ] = {"trace": [], "template": [], "toolpath": []}
        self._toolpath_items: list[QtWidgets.QGraphicsPathItem] = []
        self._toolpath_build_timer = QtCore.QTimer(self)
        self._toolpath_build_timer.setInterval(0)
        self._toolpath_build_timer.timeout.connect(self._toolpath_build_slice)
        self._toolpath_build_moves: tuple[Any, ...] = ()
        self._toolpath_build_index = 0
        self._toolpath_coordinate_frame: Any | None = None
        self._toolpath_build_paths: dict[str, QtGui.QPainterPath] = {}
        self._toolpath_build_roles: set[str] = set()
        self._toolpath_build_progress: Callable[[int, int], None] | None = None
        self._toolpath_build_finished: Callable[[bool], None] | None = None
        self._toolpath_build_failed: Callable[[str], None] | None = None
        self._raster_restore_timer = QtCore.QTimer(self)
        self._raster_restore_timer.setInterval(0)
        self._raster_restore_timer.timeout.connect(
            self._raster_restore_slice
        )
        self._raster_restore_queue: list[
            tuple[str, tuple[str, ...], int]
        ] = []
        self._trace_items: list[QtWidgets.QGraphicsItem] = []
        self._template_items: list[QtWidgets.QGraphicsItem] = []
        self._template_preview_item: _TemplatePreviewGraphicsItem | None = None
        self._template_rotation_handle: _TemplateRotationHandle | None = None
        self._creation_tool = ""
        self._creation_color = QtGui.QColor("#B96592")
        self._rectangle_anchor_mm: tuple[float, float] | None = None
        self._rectangle_current_mm: tuple[float, float] | None = None
        self._rectangle_preview_item: QtWidgets.QGraphicsPathItem | None = None
        self._point_pick_active = False
        self.snap_enabled = True
        self.snap_step_mm = 1.0
        self._panning = False
        self._pan_start = QtCore.QPoint()
        self._space_pan = False
        self._pan_button = QtCore.Qt.MouseButton.NoButton
        self._fit_to_work_area = True
        self.workspace_scene.selectionChanged.connect(self._emit_selection)
        self.zoomChanged.connect(self._template_zoom_changed)
        self.zoomChanged.connect(self._object_transform_zoom_changed)
        self.fit_work_area()

    def drawBackground(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
    ) -> None:
        """Render the machine bed through the view's actual paint path.

        ``QGraphicsScene.render`` calls the scene override directly, but the
        interactive ``QGraphicsView`` uses its own background hook. Delegating
        here keeps native/on-screen painting consistent with exported scene
        renders and prevents the camera raster from becoming the only visible
        source of bed/grid pixels.
        """

        self.workspace_scene.drawBackground(painter, rect)

    @property
    def camera_opacity(self) -> float:
        return self._camera_item.opacity()

    def set_camera_opacity(self, value: float) -> None:
        self._camera_item.setOpacity(max(0.0, min(1.0, float(value))))

    def set_work_area(self, work_area: Bounds, *, fit: bool = True) -> None:
        self.cancel_shape_draft()
        self.workspace_scene.set_work_area(work_area)
        if fit:
            self.fit_work_area()

    def fit_work_area(self) -> None:
        self._fit_to_work_area = True
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
        self._fit_to_work_area = False
        self.zoomChanged.emit(abs(self.transform().m11()))

    def zoom_by(self, factor: float) -> None:
        current = abs(self.transform().m11())
        target = current * float(factor)
        if 0.08 <= target <= 80.0:
            self._fit_to_work_area = False
            self.scale(float(factor), float(factor))
            self.zoomChanged.emit(abs(self.transform().m11()))

    def zoom_in(self) -> None:
        self.zoom_by(1.18)

    def zoom_out(self) -> None:
        self.zoom_by(1.0 / 1.18)

    def set_camera_image(
        self,
        image: QtGui.QImage | None,
        *,
        pixels_per_mm: float | None = None,
        image_area: Bounds | None = None,
    ) -> None:
        if image is None or image.isNull():
            self._camera_item.setVisible(False)
            self._camera_backing_item.setVisible(False)
            self._camera_image_area = None
            return
        pixmap = QtGui.QPixmap.fromImage(image)
        area = self.workspace_scene.work_area if image_area is None else image_area
        if area.width <= 0.0 or area.height <= 0.0:
            raise ValueError("Camera image area must have positive dimensions")
        transform = QtGui.QTransform()
        if pixels_per_mm is None:
            scale_x = area.width / pixmap.width()
            scale_y = area.height / pixmap.height()
        else:
            pixels_per_mm = float(pixels_per_mm)
            if not math.isfinite(pixels_per_mm) or pixels_per_mm <= 0.0:
                raise ValueError("Camera pixels_per_mm must be a positive finite value")
            expected_width = max(1, int(round(area.width * pixels_per_mm)))
            expected_height = max(1, int(round(area.height * pixels_per_mm)))
            if (pixmap.width(), pixmap.height()) != (
                expected_width,
                expected_height,
            ):
                raise ValueError(
                    "Corrected camera raster dimensions do not match the work "
                    f"area and pixels_per_mm: received {pixmap.width()} x "
                    f"{pixmap.height()}, expected {expected_width} x "
                    f"{expected_height}"
                )
            scale_x = scale_y = 1.0 / pixels_per_mm
        transform.scale(scale_x, scale_y)
        self._camera_item.setPixmap(pixmap)
        self._camera_item.setPos(area.x_min, -area.y_max)
        self._camera_item.setTransform(transform)
        self._camera_backing_item.setRect(
            QtCore.QRectF(area.x_min, -area.y_max, area.width, area.height)
        )
        self._camera_backing_item.setVisible(True)
        self._camera_item.setVisible(True)
        self._camera_image_area = area

    def fit_camera_image(self) -> None:
        area = self._camera_image_area
        if area is None or not self._camera_item.isVisible():
            self.fit_work_area()
            return
        rect = QtCore.QRectF(area.x_min, -area.y_max, area.width, area.height)
        padding = max(3.0, min(area.width, area.height) * 0.025)
        self.fitInView(
            rect.adjusted(-padding, -padding, padding, padding),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
        )
        self._fit_to_work_area = False
        self.zoomChanged.emit(abs(self.transform().m11()))

    def set_test_frame_source(self, active: bool, label: str = "") -> None:
        """Keep the frozen-source warning visible even when inspector docks are hidden."""

        description = str(label).strip() or "Frozen corrected simulation frame"
        self._test_frame_badge.setToolTip(description)
        self._test_frame_badge.setVisible(bool(active))
        if active:
            self._test_frame_badge.adjustSize()
            self._position_test_frame_badge()
            self._test_frame_badge.raise_()

    def _position_test_frame_badge(self) -> None:
        badge = self._test_frame_badge
        badge.move(max(12, self.viewport().width() - badge.width() - 12), 12)

    def _position_overlay_legend(self) -> None:
        if not hasattr(self, "_overlay_legend"):
            return
        legend = self._overlay_legend
        legend.position_in_parent(QtCore.QPoint(12, 12))
        legend.raise_()

    def _refresh_overlay_legend(self) -> None:
        entries: list[tuple[str, str, QtCore.Qt.PenStyle]] = []
        seen: set[str] = set()
        for source in ("trace", "template", "toolpath"):
            for entry in self._overlay_entries[source]:
                if entry[0] in seen:
                    continue
                seen.add(entry[0])
                entries.append(entry)
        self._overlay_legend.set_entries(entries)
        self._position_overlay_legend()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._position_test_frame_badge()
        self._position_overlay_legend()
        # Initial construction and dock restoration can resize the viewport
        # after the first fit. Refit on the next event turn while the user is
        # still in Fit Work Area mode; manual zooming or panning opts out.
        if getattr(self, "_fit_to_work_area", False):
            QtCore.QTimer.singleShot(0, self._refit_after_resize)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Keep viewport overlays fixed when QGraphicsView scrolls its children."""

        super().scrollContentsBy(dx, dy)
        self._position_test_frame_badge()
        self._position_overlay_legend()

    def _refit_after_resize(self) -> None:
        if self._fit_to_work_area and self.viewport().width() > 0:
            self.fit_work_area()

    def begin_point_pick(self) -> None:
        self.set_creation_tool(None)
        self._point_pick_active = True
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    @property
    def point_pick_active(self) -> bool:
        return self._point_pick_active

    def cancel_point_pick(self) -> None:
        self._point_pick_active = False
        if not self._panning and not self._space_pan:
            self._restore_interaction_cursor()

    @property
    def creation_tool(self) -> str:
        return self._creation_tool

    def set_creation_tool(
        self,
        tool: str | None,
        *,
        color: str | QtGui.QColor | None = None,
    ) -> None:
        normalized = "" if tool is None else str(tool).strip().lower()
        if normalized not in {"", "rectangle"}:
            raise ValueError(f"Unsupported workspace creation tool: {tool}")
        if color is not None:
            self.set_creation_color(color)
        changed = normalized != self._creation_tool
        self.cancel_shape_draft()
        if normalized:
            self._point_pick_active = False
        self._creation_tool = normalized
        self.setDragMode(
            QtWidgets.QGraphicsView.DragMode.NoDrag
            if normalized
            else QtWidgets.QGraphicsView.DragMode.RubberBandDrag
        )
        if not self._panning and not self._space_pan:
            self._restore_interaction_cursor()
        self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)
        if changed:
            self.creationToolChanged.emit(normalized)

    def set_creation_color(self, color: str | QtGui.QColor) -> None:
        resolved = QtGui.QColor(color)
        if not resolved.isValid():
            raise ValueError(f"Invalid creation preview color: {color}")
        self._creation_color = resolved
        if self._rectangle_preview_item is not None:
            pen = self._rectangle_preview_item.pen()
            pen.setColor(resolved)
            self._rectangle_preview_item.setPen(pen)

    def cancel_shape_draft(self) -> None:
        had_draft = (
            self._rectangle_anchor_mm is not None
            or self._rectangle_preview_item is not None
        )
        preview = self._rectangle_preview_item
        self._rectangle_preview_item = None
        self._rectangle_anchor_mm = None
        self._rectangle_current_mm = None
        if preview is not None and preview.scene() is self.workspace_scene:
            self.workspace_scene.removeItem(preview)
        if had_draft:
            self.rectangleDraftChanged.emit(None)

    def _restore_interaction_cursor(self) -> None:
        if self._space_pan:
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        elif self._creation_tool:
            self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)
        else:
            self.unsetCursor()

    def _snap_and_clamp_machine_point(
        self,
        point: tuple[float, float],
    ) -> tuple[float, float]:
        x_mm, y_mm = point
        if self.snap_enabled:
            step = self.snap_step_mm
            x_mm = round(x_mm / step) * step
            y_mm = round(y_mm / step) * step
        area = self.workspace_scene.work_area
        return (
            max(area.x_min, min(area.x_max, x_mm)),
            max(area.y_min, min(area.y_max, y_mm)),
        )

    def _rectangle_bounds(self) -> Bounds | None:
        anchor = self._rectangle_anchor_mm
        current = self._rectangle_current_mm
        if anchor is None or current is None:
            return None
        return Bounds(
            min(anchor[0], current[0]),
            min(anchor[1], current[1]),
            max(anchor[0], current[0]),
            max(anchor[1], current[1]),
        )

    def _rectangle_bounds_are_drawable(self, bounds: Bounds) -> bool:
        minimum = self._MINIMUM_DRAW_SIZE_MM - self._DRAW_SIZE_EPSILON_MM
        return bounds.width >= minimum and bounds.height >= minimum

    def _update_rectangle_draft(self, point: tuple[float, float]) -> None:
        if self._rectangle_anchor_mm is None:
            return
        self._rectangle_current_mm = self._snap_and_clamp_machine_point(point)
        bounds = self._rectangle_bounds()
        if bounds is None:
            return
        if self._rectangle_preview_item is None:
            preview = QtWidgets.QGraphicsPathItem()
            pen = QtGui.QPen(self._creation_color)
            pen.setWidthF(1.5)
            pen.setCosmetic(True)
            preview.setPen(pen)
            preview.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            preview.setZValue(450.0)
            preview.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            self.workspace_scene.addItem(preview)
            self._rectangle_preview_item = preview
        path = QtGui.QPainterPath()
        path.addRect(
            QtCore.QRectF(
                bounds.x_min,
                -bounds.y_max,
                bounds.width,
                bounds.height,
            )
        )
        self._rectangle_preview_item.setPath(path)
        self.rectangleDraftChanged.emit(bounds)

    def _creation_hit_is_direct_edit(self, viewport_position: QtCore.QPoint) -> bool:
        for hit_item in self.items(viewport_position):
            item: QtWidgets.QGraphicsItem | None = hit_item
            while item is not None:
                if isinstance(item, ObjectGraphicsItem):
                    if item.isSelected() and item.isEnabled():
                        return True
                    break
                if isinstance(
                    item,
                    (
                        _ObjectResizeHandle,
                        _ObjectRotationHandle,
                        _TemplatePreviewDragSurface,
                        _TemplateRotationHandle,
                    ),
                ):
                    return True
                item = item.parentItem()
        return False

    def clear_trace_preview(self) -> None:
        for item in self._trace_items:
            self.workspace_scene.removeItem(item)
        self._trace_items.clear()
        self._overlay_entries["trace"] = []
        self._refresh_overlay_legend()

    def set_trace_preview(
        self,
        detections: list[dict[str, Any]],
        selected_ids: list[str] | set[str] | None = None,
        support_reference: dict[str, Any] | None = None,
        output_work_area: dict[str, Any] | None = None,
        output_polygon: list[list[float]] | None = None,
    ) -> None:
        self.clear_trace_preview()
        selected = set(selected_ids or [])
        has_output_boundary = False
        output = output_work_area or {}
        if output_polygon and len(output_polygon) >= 3:
            output_path = QtGui.QPainterPath()
            output_path.moveTo(
                self.workspace_scene.machine_to_scene(*output_polygon[0])
            )
            for point in output_polygon[1:]:
                output_path.lineTo(self.workspace_scene.machine_to_scene(*point))
            output_path.closeSubpath()
            output_item = QtWidgets.QGraphicsPathItem(output_path)
            output_pen = QtGui.QPen(QtGui.QColor("#67E05C"))
            output_pen.setWidthF(1.4)
            output_pen.setCosmetic(True)
            output_pen.setStyle(QtCore.Qt.PenStyle.DashDotLine)
            output_item.setPen(output_pen)
            output_item.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            output_item.setZValue(258.0)
            output_item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            output_item.setToolTip(
                "Configured machine-output boundary in honeycomb coordinates"
            )
            self.workspace_scene.addItem(output_item)
            self._trace_items.append(output_item)
            has_output_boundary = True
        try:
            output_values = (
                float(output["x_min"]),
                float(output["x_max"]),
                float(output["y_min"]),
                float(output["y_max"]),
            )
        except (KeyError, TypeError, ValueError):
            output_values = ()
        if (
            len(output_values) == 4
            and all(math.isfinite(value) for value in output_values)
            and output_values[1] > output_values[0]
            and output_values[3] > output_values[2]
        ):
            x_min, x_max, y_min, y_max = output_values
            output_path = QtGui.QPainterPath()
            output_path.moveTo(self.workspace_scene.machine_to_scene(x_min, y_min))
            output_path.lineTo(self.workspace_scene.machine_to_scene(x_max, y_min))
            output_path.lineTo(self.workspace_scene.machine_to_scene(x_max, y_max))
            output_path.lineTo(self.workspace_scene.machine_to_scene(x_min, y_max))
            output_path.closeSubpath()
            output_item = QtWidgets.QGraphicsPathItem(output_path)
            output_pen = QtGui.QPen(QtGui.QColor("#67E05C"))
            output_pen.setWidthF(1.4)
            output_pen.setCosmetic(True)
            output_pen.setStyle(QtCore.Qt.PenStyle.DashDotLine)
            output_item.setPen(output_pen)
            output_item.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            output_item.setZValue(258.0)
            output_item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            output_item.setToolTip(
                "Guarded laser-output boundary; paths outside it remain blocked"
            )
            self.workspace_scene.addItem(output_item)
            self._trace_items.append(output_item)
            has_output_boundary = True
        has_support_boundary = False
        support = support_reference or {}
        support_corners = (
            support.get("corners_local_mm")
            or support.get("corners_machine_mm")
            or []
        )
        if bool(support.get("bed_map_current")) and len(support_corners) >= 3:
            path = QtGui.QPainterPath()
            first = self.workspace_scene.machine_to_scene(*support_corners[0])
            path.moveTo(first)
            for point in support_corners[1:]:
                path.lineTo(self.workspace_scene.machine_to_scene(*point))
            path.closeSubpath()
            support_item = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(QtGui.QColor("#CD5FDC"))
            pen.setWidthF(1.4)
            pen.setCosmetic(True)
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            support_item.setPen(pen)
            support_item.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            support_item.setZValue(259.0)
            support_item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            self.workspace_scene.addItem(support_item)
            self._trace_items.append(support_item)
            has_support_boundary = True
        has_selected_direct = False
        has_inferred = False
        has_unselected_direct = False
        has_outside = False
        has_damaged = False
        has_likely_open = False
        for detection in detections:
            contours = detection.get("vector_contours_mm") or [[
                *(
                    detection.get("vector_contour_mm")
                    or detection.get("contour_mm")
                    or detection.get("box_mm")
                    or []
                )
            ]]
            if not any(len(points) >= 2 for points in contours):
                continue
            path = QtGui.QPainterPath()
            for points in contours:
                if len(points) < 2:
                    continue
                first = self.workspace_scene.machine_to_scene(*points[0])
                path.moveTo(first)
                for point in points[1:]:
                    path.lineTo(self.workspace_scene.machine_to_scene(*point))
                path.closeSubpath()
            item = QtWidgets.QGraphicsPathItem(path)
            is_selected = detection.get("id") in selected
            is_inferred = detection.get("source") == "inferred"
            diagnostics = detection.get("diagnostics") or {}
            is_outside = not bool(diagnostics.get("within_work_area", True))
            is_cropped = bool(diagnostics.get("touches_image_edge", False))
            is_damaged = bool(diagnostics.get("damage_suspected", False))
            is_likely_open = bool(diagnostics.get("likely_open_cell", False))
            is_flagged = is_outside or is_cropped or is_damaged or is_likely_open
            has_outside = has_outside or is_outside or is_cropped
            has_damaged = has_damaged or is_damaged
            has_likely_open = has_likely_open or is_likely_open
            has_selected_direct = has_selected_direct or (
                is_selected and not is_inferred and not is_flagged
            )
            has_inferred = has_inferred or (is_inferred and not is_flagged)
            has_unselected_direct = has_unselected_direct or (
                not is_selected and not is_inferred and not is_flagged
            )
            if is_outside or is_cropped:
                color = QtGui.QColor("#E06666")
            elif is_likely_open:
                color = QtGui.QColor("#45D7FF")
            elif is_damaged:
                color = QtGui.QColor("#E7B55C")
            elif is_selected and not is_inferred:
                color = QtGui.QColor("#4FE36F")
            elif is_selected and is_inferred:
                color = QtGui.QColor("#E7B55C")
            elif is_inferred:
                color = QtGui.QColor("#D98A45")
            else:
                color = QtGui.QColor("#8998A3")
            pen = QtGui.QPen(color)
            pen.setWidthF(1.4 if is_selected else 1.0)
            pen.setCosmetic(True)
            if is_flagged:
                pen.setStyle(QtCore.Qt.PenStyle.DashDotLine)
            elif is_inferred:
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
            index_text = str(detection.get("index", ""))
            if center and len(center) == 2 and index_text:
                label = _TraceIndexBadge(index_text, color)
                position = self.workspace_scene.machine_to_scene(*center)
                label.setPos(position)
                label.setZValue(261.0)
                self.workspace_scene.addItem(label)
                self._trace_items.append(label)

        trace_entries: list[tuple[str, str, QtCore.Qt.PenStyle]] = []
        if has_support_boundary:
            trace_entries.append(
                (
                    "Approx. honeycomb reference (magenta)",
                    "#CD5FDC",
                    QtCore.Qt.PenStyle.DashLine,
                )
            )
        if has_output_boundary:
            trace_entries.append(
                (
                    "Guarded laser output (green)",
                    "#67E05C",
                    QtCore.Qt.PenStyle.DashDotLine,
                )
            )
        if has_damaged:
            trace_entries.append(
                ("Damage suspected (amber)", "#E7B55C", QtCore.Qt.PenStyle.DashDotLine)
            )
        if has_likely_open:
            trace_entries.append(
                ("Likely already cut/open (cyan)", "#45D7FF", QtCore.Qt.PenStyle.DashDotLine)
            )
        if has_selected_direct:
            trace_entries.append(
                ("Selected trace (green)", "#4FE36F", QtCore.Qt.PenStyle.SolidLine)
            )
        if has_inferred:
            trace_entries.append(
                ("Inferred trace (amber)", "#E7B55C", QtCore.Qt.PenStyle.DashLine)
            )
        if has_unselected_direct:
            trace_entries.append(
                ("Unselected detection (gray)", "#8998A3", QtCore.Qt.PenStyle.SolidLine)
            )
        if has_outside:
            trace_entries.append(
                (
                    "Cropped / outside output (red)",
                    "#E06666",
                    QtCore.Qt.PenStyle.DashDotLine,
                )
            )
        self._overlay_entries["trace"] = trace_entries
        self._refresh_overlay_legend()

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
        self._overlay_entries["template"] = []
        self._refresh_overlay_legend()

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
        excluded_color = QtGui.QColor("#E06666")
        has_observed = False
        has_excluded = False
        for detection in detections or []:
            # Alignment review should show the same proposed fitted boundary
            # that Trace shows. The camera pixels remain visible underneath;
            # falling back to the simplified raw contour preserves support for
            # older/non-rounded detection payloads.
            contours = detection.get("vector_contours_mm") or [[
                *(
                    detection.get("vector_contour_mm")
                    or detection.get("contour_mm")
                    or detection.get("box_mm")
                    or []
                )
            ]]
            if not any(len(points) >= 2 for points in contours):
                continue
            path = QtGui.QPainterPath()
            for points in contours:
                if len(points) < 2:
                    continue
                path.moveTo(self.workspace_scene.machine_to_scene(*points[0]))
                for point in points[1:]:
                    path.lineTo(self.workspace_scene.machine_to_scene(*point))
                path.closeSubpath()
            item = QtWidgets.QGraphicsPathItem(path)
            diagnostics = detection.get("diagnostics") or {}
            is_outside = not bool(diagnostics.get("within_work_area", True))
            is_cropped = bool(diagnostics.get("touches_image_edge", False))
            is_excluded = is_outside or is_cropped
            color = excluded_color if is_excluded else observed_color
            pen = QtGui.QPen(color)
            pen.setWidthF(1.5)
            pen.setCosmetic(True)
            pen.setStyle(
                QtCore.Qt.PenStyle.DashDotLine
                if is_excluded
                else QtCore.Qt.PenStyle.DashLine
            )
            item.setPen(pen)
            fill = QtGui.QColor(color)
            fill.setAlpha(8)
            item.setBrush(fill)
            # Draw camera evidence over the exact cut line. When both agree,
            # amber dashes alternate with the cyan line instead of disappearing
            # underneath it; any disagreement remains geometrically honest.
            item.setZValue(282.0)
            item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            if is_excluded:
                reasons = []
                if is_outside:
                    reasons.append("outside the guarded output area")
                if is_cropped:
                    reasons.append("cropped at the corrected image boundary")
                item.setToolTip(
                    "Excluded camera feature: " + " and ".join(reasons)
                )
            else:
                item.setToolTip("Observed camera feature")
            self.workspace_scene.addItem(item)
            self._template_items.append(item)
            has_excluded = has_excluded or is_excluded
            has_observed = has_observed or not is_excluded

        template_entries: list[tuple[str, str, QtCore.Qt.PenStyle]] = []
        if has_observed:
            template_entries.append(
                ("Camera edge (amber)", "#E7B55C", QtCore.Qt.PenStyle.DashLine)
            )
        if has_excluded:
            template_entries.append(
                (
                    "Cropped / outside output (red)",
                    "#E06666",
                    QtCore.Qt.PenStyle.DashDotLine,
                )
            )
        if not objects:
            self._overlay_entries["template"] = template_entries
            self._refresh_overlay_legend()
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
            self._overlay_entries["template"] = template_entries
            self._refresh_overlay_legend()
            return
        self.workspace_scene.addItem(preview)
        self._template_preview_item = preview
        self._template_rotation_handle = preview.rotation_handle
        self._template_items.extend(preview.visual_items)
        preview.set_view_scale(abs(self.transform().m11()))
        template_entries.append(
            ("Aligned template cut (cyan)", "#45D7FF", QtCore.Qt.PenStyle.SolidLine)
        )
        self._overlay_entries["template"] = template_entries
        self._refresh_overlay_legend()

    def _template_zoom_changed(self, scale: float) -> None:
        if self._template_preview_item is not None:
            self._template_preview_item.set_view_scale(scale)

    def clear_toolpath_preview(self) -> None:
        self._cancel_toolpath_build(notify=True)
        for item in self._toolpath_items:
            self.workspace_scene.removeItem(item)
        self._toolpath_items.clear()
        self._overlay_entries["toolpath"] = []
        self._refresh_overlay_legend()

    def _cancel_toolpath_build(self, *, notify: bool) -> None:
        active = self._toolpath_build_timer.isActive()
        self._toolpath_build_timer.stop()
        callback = self._toolpath_build_finished
        self._toolpath_build_moves = ()
        self._toolpath_build_paths = {}
        self._toolpath_build_roles = set()
        self._toolpath_build_progress = None
        self._toolpath_build_finished = None
        self._toolpath_build_failed = None
        self._toolpath_build_index = 0
        self._toolpath_coordinate_frame = None
        if active and notify and callback is not None:
            callback(False)

    def start_toolpath_preview(
        self,
        plan: JobPlan,
        *,
        coordinate_frame: Any | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_finished: Callable[[bool], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        """Build the workspace overlay in bounded GUI-thread slices."""

        self.clear_toolpath_preview()
        self._toolpath_build_moves = tuple(plan.moves)
        self._toolpath_coordinate_frame = coordinate_frame
        self._toolpath_build_paths = {
            "rapid": QtGui.QPainterPath(),
            "powered": QtGui.QPainterPath(),
            "unpowered": QtGui.QPainterPath(),
        }
        self._toolpath_build_roles = set()
        self._toolpath_build_progress = on_progress
        self._toolpath_build_finished = on_finished
        self._toolpath_build_failed = on_failed
        if not self._toolpath_build_moves:
            self._finish_toolpath_build()
            return
        self._toolpath_build_timer.start()

    @QtCore.Slot()
    def _toolpath_build_slice(self) -> None:
        try:
            moves = self._toolpath_build_moves
            if not moves:
                self._finish_toolpath_build()
                return
            timer = QtCore.QElapsedTimer()
            timer.start()
            start = self._toolpath_build_index
            count = start
            while count < len(moves) and count - start < 1_500:
                move = moves[count]
                role = (
                    "rapid"
                    if move.rapid
                    else "powered"
                    if move.laser_on
                    else "unpowered"
                )
                path = self._toolpath_build_paths[role]
                start_x, start_y = move.start_x, move.start_y
                end_x, end_y = move.end_x, move.end_y
                if self._toolpath_coordinate_frame is not None:
                    start_x, start_y = self._toolpath_coordinate_frame.machine_to_local(
                        start_x,
                        start_y,
                    )
                    end_x, end_y = self._toolpath_coordinate_frame.machine_to_local(
                        end_x,
                        end_y,
                    )
                start_point = self.workspace_scene.machine_to_scene(
                    start_x,
                    start_y,
                )
                end_point = self.workspace_scene.machine_to_scene(
                    end_x,
                    end_y,
                )
                path.moveTo(start_point)
                path.lineTo(end_point)
                self._toolpath_build_roles.add(role)
                count += 1
                if timer.elapsed() >= 8:
                    break
            self._toolpath_build_index = count
            if self._toolpath_build_progress is not None:
                self._toolpath_build_progress(count, len(moves))
            if count >= len(moves):
                self._finish_toolpath_build()
        except Exception as exc:
            self._fail_toolpath_build(str(exc))

    def _finish_toolpath_build(self) -> None:
        self._toolpath_build_timer.stop()
        paths = self._toolpath_build_paths
        roles = self._toolpath_build_roles
        callback = self._toolpath_build_finished
        failed_callback = self._toolpath_build_failed
        self._toolpath_build_moves = ()
        self._toolpath_build_paths = {}
        self._toolpath_build_roles = set()
        self._toolpath_build_progress = None
        self._toolpath_build_finished = None
        self._toolpath_build_failed = None
        self._toolpath_build_index = 0
        self._toolpath_coordinate_frame = None
        try:
            self._commit_toolpath_paths(paths, roles)
        except Exception as exc:
            self.clear_toolpath_preview()
            if failed_callback is not None:
                failed_callback(str(exc))
            return
        if callback is not None:
            callback(True)

    def _fail_toolpath_build(self, message: str) -> None:
        self._toolpath_build_timer.stop()
        callback = self._toolpath_build_failed
        self._toolpath_build_moves = ()
        self._toolpath_build_paths = {}
        self._toolpath_build_roles = set()
        self._toolpath_build_progress = None
        self._toolpath_build_finished = None
        self._toolpath_build_failed = None
        self._toolpath_build_index = 0
        self._toolpath_coordinate_frame = None
        for item in self._toolpath_items:
            self.workspace_scene.removeItem(item)
        self._toolpath_items.clear()
        if callback is not None:
            callback(str(message))

    def set_toolpath_preview(self, gcode: str) -> None:
        self.clear_toolpath_preview()
        paths = {
            "rapid": QtGui.QPainterPath(),
            "powered": QtGui.QPainterPath(),
            "unpowered": QtGui.QPainterPath(),
        }
        roles: set[str] = set()
        for segment in parse_gcode_segments(gcode):
            start = self.workspace_scene.machine_to_scene(segment.start_x, segment.start_y)
            end = self.workspace_scene.machine_to_scene(segment.end_x, segment.end_y)
            if segment.rapid:
                role = "rapid"
            elif segment.laser_on:
                role = "powered"
            else:
                role = "unpowered"
            roles.add(role)
            path = paths[role]
            path.moveTo(start)
            path.lineTo(end)

        self._commit_toolpath_paths(paths, roles)

    def _commit_toolpath_paths(
        self,
        paths: dict[str, QtGui.QPainterPath],
        roles: set[str],
    ) -> None:

        styles = (
            (
                "rapid",
                "#5CA9E7",
                0.30,
                QtCore.Qt.PenStyle.DashLine,
            ),
            (
                "powered",
                "#E35D6A",
                0.50,
                QtCore.Qt.PenStyle.SolidLine,
            ),
            (
                "unpowered",
                "#E7B55C",
                0.35,
                QtCore.Qt.PenStyle.SolidLine,
            ),
        )
        for key, color, width, style in styles:
            path = paths[key]
            if path.isEmpty():
                continue
            item = QtWidgets.QGraphicsPathItem(path)
            pen = QtGui.QPen(QtGui.QColor(color))
            pen.setStyle(style)
            pen.setWidthF(width)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(300.0)
            item.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            self.workspace_scene.addItem(item)
            self._toolpath_items.append(item)
        toolpath_entries: list[tuple[str, str, QtCore.Qt.PenStyle]] = []
        if "rapid" in roles:
            toolpath_entries.append(
                ("Rapid travel", "#5CA9E7", QtCore.Qt.PenStyle.DashLine)
            )
        if "powered" in roles:
            toolpath_entries.append(
                ("Powered toolpath", "#E35D6A", QtCore.Qt.PenStyle.SolidLine)
            )
        if "unpowered" in roles:
            toolpath_entries.append(
                ("Laser-off move", "#E7B55C", QtCore.Qt.PenStyle.SolidLine)
            )
        self._overlay_entries["toolpath"] = toolpath_entries
        self._refresh_overlay_legend()

    def set_document(self, document: ProjectDocument) -> None:
        self.cancel_shape_draft()
        selected = set(self.selected_object_ids())
        self._clear_object_transform_overlay()
        reuse_items = self._document is document
        self._syncing_document = True
        try:
            raster_sources = {
                str(item.geometry.get("asset", "")).strip()
                for item in document.objects
                if item.kind == ObjectKind.IMAGE
                and str(item.geometry.get("asset", "")).strip()
            }
            previous_pixel_budget = getattr(
                self,
                "_raster_preview_pixel_budget",
                ObjectGraphicsItem._preview_pixel_budget(),
            )
            ObjectGraphicsItem.set_project_raster_source_count(
                len(raster_sources)
            )
            current_pixel_budget = ObjectGraphicsItem._preview_pixel_budget()
            self._raster_preview_pixel_budget = current_pixel_budget
            if not reuse_items or current_pixel_budget < previous_pixel_budget:
                self._cancel_raster_preview_quality_restore()
            desired_ids = {item.id for item in document.objects}
            if reuse_items:
                for object_id in set(self._items_by_id) - desired_ids:
                    removed = self._items_by_id.pop(object_id)
                    self.workspace_scene.removeItem(removed)
                retained_items = list(self._items_by_id.values())
                ObjectGraphicsItem.rebudget_raster_items(retained_items)
                ObjectGraphicsItem.retain_item_cache(retained_items)
                existing_items = dict(self._items_by_id)
            else:
                for item in self._items_by_id.values():
                    self.workspace_scene.removeItem(item)
                self._items_by_id = {}
                ObjectGraphicsItem.retain_item_cache([])
                existing_items = {}
            next_items: dict[str, ObjectGraphicsItem] = {}
            self._document = document
            area_changed = document.work_area != self.workspace_scene.work_area
            if area_changed:
                self.set_work_area(document.work_area, fit=True)
            layers = {layer.id: layer for layer in document.layers}
            for z_index, scene_object in enumerate(document.objects):
                layer = layers[scene_object.layer_id]
                item = existing_items.pop(scene_object.id, None)
                if item is None:
                    item = ObjectGraphicsItem(
                        scene_object,
                        layer,
                        move_callback=self._item_move_finished,
                    )
                    self.workspace_scene.addItem(item)
                else:
                    item.apply_model(scene_object, layer)
                item.setZValue(float(z_index))
                next_items[scene_object.id] = item
                if scene_object.id in selected:
                    item.setSelected(True)
            for item in existing_items.values():
                self.workspace_scene.removeItem(item)
            self._items_by_id = next_items
            ObjectGraphicsItem.retain_item_cache(list(next_items.values()))
            if reuse_items and current_pixel_budget > previous_pixel_budget:
                self._schedule_raster_preview_quality_restore(
                    list(next_items.values()),
                    current_pixel_budget,
                )
        finally:
            self._syncing_document = False
        self._update_object_transform_overlay()

    def _cancel_raster_preview_quality_restore(self) -> None:
        self._raster_restore_timer.stop()
        self._raster_restore_queue = []

    def _schedule_raster_preview_quality_restore(
        self,
        items: list[ObjectGraphicsItem],
        pixel_budget: int,
    ) -> None:
        self._cancel_raster_preview_quality_restore()
        groups: dict[str, list[str]] = {}
        for item in items:
            source_size = item._raster_source_size
            image = item._raster_image
            reference = item._raster_asset_reference
            if source_size is None or image is None or not reference:
                continue
            desired_pixels = min(
                source_size[0] * source_size[1],
                pixel_budget,
            )
            if image.width() * image.height() >= desired_pixels:
                continue
            groups.setdefault(reference, []).append(item.object_id)
        self._raster_restore_queue = [
            (reference, tuple(object_ids), pixel_budget)
            for reference, object_ids in groups.items()
        ]
        if self._raster_restore_queue:
            self._raster_restore_timer.start()

    def _raster_restore_slice(self) -> None:
        if not self._raster_restore_queue:
            self._raster_restore_timer.stop()
            return
        reference, object_ids, expected_budget = self._raster_restore_queue.pop(0)
        if (
            expected_budget != self._raster_preview_pixel_budget
            or expected_budget != ObjectGraphicsItem._preview_pixel_budget()
        ):
            self._cancel_raster_preview_quality_restore()
            return
        group = [
            item
            for object_id in object_ids
            if (item := self._items_by_id.get(object_id)) is not None
            and item._raster_asset_reference == reference
        ]
        if group:
            (
                source,
                image,
                sha256,
                source_size,
            ) = ObjectGraphicsItem._load_raster_snapshot(
                reference,
                use_cache=False,
            )
            if (
                image is not None
                and sha256 is not None
                and source_size is not None
                and expected_budget == self._raster_preview_pixel_budget
            ):
                for item in group:
                    if self._items_by_id.get(item.object_id) is not item:
                        continue
                    if item._raster_asset_reference != reference:
                        continue
                    if item._raster_identity_sha != sha256:
                        continue
                    item._raster_source = source
                    item._raster_image = image
                    item._raster_identity_sha = sha256
                    item._raster_source_size = source_size
                    item.update()
                ObjectGraphicsItem.retain_item_cache(
                    list(self._items_by_id.values())
                )
        if not self._raster_restore_queue:
            self._raster_restore_timer.stop()

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
        if (
            self._object_transform_overlay is not None
            and self._object_transform_overlay.object_id == object_id
        ):
            self._object_transform_overlay.apply_transform(scene_object.transform)

    def raster_preview_mismatches(
        self,
        identities: tuple[Any, ...],
    ) -> tuple[str, ...]:
        displayed: dict[str, set[str]] = {}
        for item in self._items_by_id.values():
            identity = item.raster_preview_identity
            if identity is None:
                continue
            path, sha256 = identity
            displayed.setdefault(path, set()).add(sha256)
        expected: dict[str, set[str]] = {}
        for identity in identities:
            expected.setdefault(str(identity.path), set()).add(
                str(identity.sha256)
            )
        return tuple(
            sorted(
                {
                    path
                    for path, hashes in expected.items()
                    if displayed.get(path, set()) != hashes
                }
            )
        )

    def refresh_raster_previews(self, paths: tuple[str, ...]) -> bool:
        pending = set(paths)
        by_path: dict[str, list[ObjectGraphicsItem]] = {}
        for item in self._items_by_id.values():
            identity = item.raster_preview_identity
            candidate = (
                identity[0]
                if identity is not None
                else str(
                    Path(item._raster_asset_reference or "")
                    .expanduser()
                    .absolute()
                )
            )
            if candidate in pending:
                by_path.setdefault(candidate, []).append(item)
        refreshed = set()
        for path, items in by_path.items():
            primary = items[0]
            if not primary.refresh_raster_snapshot():
                continue
            refreshed.add(path)
            for item in items[1:]:
                item._raster_source = primary._raster_source
                item._raster_image = primary._raster_image
                item._raster_identity_sha = primary._raster_identity_sha
                item._raster_source_size = primary._raster_source_size
                item.update()
        ObjectGraphicsItem.retain_item_cache(
            list(self._items_by_id.values())
        )
        return refreshed == pending

    def _preview_object_transform(self, object_id: str, transform: Transform) -> None:
        """Update canvas geometry while leaving the document untouched."""

        if self._document is None:
            return
        item = self._items_by_id.get(object_id)
        if item is None:
            return
        scene_object = self._document.get_object(object_id)
        layer = self._document.get_layer(scene_object.layer_id)
        item.preview_transform(scene_object, layer, transform)

    def _clear_object_transform_overlay(self) -> None:
        overlay = self._object_transform_overlay
        self._object_transform_overlay = None
        if overlay is not None and overlay.scene() is self.workspace_scene:
            self.workspace_scene.removeItem(overlay)

    def _update_object_transform_overlay(self) -> None:
        self._clear_object_transform_overlay()
        if self._syncing_document or self._document is None:
            return
        selected = self.selected_object_ids()
        if len(selected) != 1:
            return
        object_id = selected[0]
        try:
            scene_object = self._document.get_object(object_id)
        except KeyError:
            return
        item = self._items_by_id.get(object_id)
        if (
            scene_object.locked
            or not scene_object.visible
            or item is None
            or item.scene() is not self.workspace_scene
            or item.path().isEmpty()
        ):
            return
        overlay = _ObjectTransformOverlay(self, object_id, scene_object.transform)
        self.workspace_scene.addItem(overlay)
        self._object_transform_overlay = overlay

    def _object_transform_zoom_changed(self, scale: float) -> None:
        if self._object_transform_overlay is not None:
            self._object_transform_overlay.set_view_scale(scale)


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
        if (
            self._object_transform_overlay is not None
            and self._object_transform_overlay.object_id == object_id
        ):
            transform = self._object_transform_overlay.display_transform.copy(
                x_mm=after[0],
                y_mm=after[1],
            )
            self._object_transform_overlay.apply_transform(transform)
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
        self._update_object_transform_overlay()

    def _emit_selection(self) -> None:
        # Qt may deliver a final selectionChanged while tearing the owned
        # scene down; its Python wrapper can already outlive the C++ scene.
        try:
            selected = self.selected_object_ids()
        except RuntimeError:
            return
        if not self._syncing_document:
            self._update_object_transform_overlay()
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
            self._fit_to_work_area = False
            self._panning = True
            self._pan_button = event.button()
            self._pan_start = event.position().toPoint()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._creation_tool == "rectangle":
            if event.button() == QtCore.Qt.MouseButton.RightButton:
                self.set_creation_tool(None)
                event.accept()
                return
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                viewport_position = event.position().toPoint()
                if self._creation_hit_is_direct_edit(viewport_position):
                    super().mousePressEvent(event)
                    return
                scene_point = self.mapToScene(viewport_position)
                point = self.workspace_scene.scene_to_machine(scene_point)
                if self.workspace_scene.work_area.contains(*point):
                    self.select_objects([])
                    snapped = self._snap_and_clamp_machine_point(point)
                    self._rectangle_anchor_mm = snapped
                    self._rectangle_current_mm = snapped
                    self._update_rectangle_draft(snapped)
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
        if self._creation_tool == "rectangle" and self._rectangle_anchor_mm is not None:
            self._update_rectangle_draft((x_mm, y_mm))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._panning and event.button() == self._pan_button:
            self._panning = False
            self._pan_button = QtCore.Qt.MouseButton.NoButton
            self._restore_interaction_cursor()
            event.accept()
            return
        if (
            self._creation_tool == "rectangle"
            and self._rectangle_anchor_mm is not None
            and event.button() == QtCore.Qt.MouseButton.LeftButton
        ):
            scene_point = self.mapToScene(event.position().toPoint())
            point = self.workspace_scene.scene_to_machine(scene_point)
            self._update_rectangle_draft(point)
            bounds = self._rectangle_bounds()
            self.cancel_shape_draft()
            if bounds is not None and self._rectangle_bounds_are_drawable(bounds):
                center_x, center_y = bounds.center
                self.rectangleDrawCommitted.emit(
                    center_x,
                    center_y,
                    bounds.width,
                    bounds.height,
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
                self._restore_interaction_cursor()
            event.accept()
            return
        super().keyReleaseEvent(event)
