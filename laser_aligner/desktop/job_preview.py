from __future__ import annotations

import bisect
import html
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..gcode.job_plan import JobPlan, PlannedMove
from .qt import require_qt

if TYPE_CHECKING:
    from ..calibration.support import HoneycombCoordinateFrame

QtCore, QtGui, QtWidgets = require_qt()


_DEFAULT_DIALOG_WIDTH = 1120
_DEFAULT_DIALOG_HEIGHT = 760
_MIN_DIALOG_WIDTH = 700
_MIN_DIALOG_HEIGHT = 520
_SCREEN_WIDTH_RESERVE = 32
_SCREEN_HEIGHT_RESERVE = 48
_MIN_CANVAS_WIDTH = 360
_MIN_CANVAS_HEIGHT = 240
_MIN_SIDEBAR_WIDTH = 300
_DEFAULT_SIDEBAR_WIDTH = 340

RenderKey = tuple[str, str, float | None]


@dataclass(slots=True, frozen=True)
class PreviewLayerRow:
    id: str
    name: str
    color: str
    mode: str
    distance: float
    seconds: float
    feeds: tuple[float, ...]
    vector_power_correction: float
    raster_power_correction: float
    power: float


@dataclass(slots=True, frozen=True)
class PreparedJobPreview:
    """Qt-free indexes that are safe to construct in a worker thread."""

    move_ends: tuple[float, ...]
    layer_rows: tuple[PreviewLayerRow, ...]


def prepare_job_preview(plan: JobPlan) -> PreparedJobPreview:
    rows: dict[str, dict[str, object]] = {}
    for move in plan.moves:
        if not move.layer_id and not move.layer_name:
            continue
        key = move.layer_id or move.layer_name
        row = rows.setdefault(
            key,
            {
                "id": key,
                "name": move.layer_name,
                "color": move.layer_color,
                "mode": move.layer_mode,
                "distance": 0.0,
                "seconds": 0.0,
                "feeds": set(),
                "vector_power_correction": move.vector_power_correction,
                "raster_power_correction": move.raster_power_correction,
                "power": 0.0,
            },
        )
        if move.laser_on:
            row["distance"] = float(row["distance"]) + move.distance_mm
            row["seconds"] = float(row["seconds"]) + move.duration_seconds
            feeds = row["feeds"]
            assert isinstance(feeds, set)
            feeds.add(float(move.feed_mm_min))
            row["power"] = max(float(row["power"]), move.power)
    return PreparedJobPreview(
        move_ends=tuple(move.end_seconds for move in plan.moves),
        layer_rows=tuple(
            PreviewLayerRow(
                id=str(row["id"]),
                name=str(row["name"]),
                color=str(row["color"]),
                mode=str(row["mode"]),
                distance=float(row["distance"]),
                seconds=float(row["seconds"]),
                feeds=tuple(sorted(float(value) for value in row["feeds"])),
                vector_power_correction=float(row["vector_power_correction"]),
                raster_power_correction=float(row["raster_power_correction"]),
                power=float(row["power"]),
            )
            for row in rows.values()
        ),
    )


def _duration(value: float) -> str:
    seconds = max(0, int(round(float(value))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def _speed_text(
    feeds: tuple[float, ...],
    *,
    maximum_feed_mm_min: float | None = None,
    detailed: bool = False,
) -> str:
    if not feeds:
        return "—"
    maximum = (
        float(maximum_feed_mm_min)
        if maximum_feed_mm_min is not None and maximum_feed_mm_min > 0
        else None
    )
    values: list[str] = []
    for feed in feeds:
        speed = feed / 60.0
        text = f"{speed:.2f} mm/s"
        if maximum is not None:
            text += f" · {feed / maximum * 100.0:.1f}%"
            if detailed:
                text += f" of configured {maximum / 60.0:.2f} mm/s work limit"
        values.append(text)
    return ", ".join(values)


def _display_color(value: object, fallback: str = "#E35D6A") -> str:
    color = QtGui.QColor(str(value))
    if not color.isValid():
        color = QtGui.QColor(fallback)
    return color.name(QtGui.QColor.NameFormat.HexRgb)


class _ElidedLabel(QtWidgets.QLabel):
    """Single-line label that keeps its complete value available as a tooltip."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

    @property
    def full_text(self) -> str:
        return self._full_text

    def set_full_text(self, value: object) -> None:
        self._full_text = str(value)
        self.setAccessibleDescription(self._full_text)
        self._refresh_text()

    def _refresh_text(self) -> None:
        width = max(0, self.contentsRect().width())
        rendered = self.fontMetrics().elidedText(
            self._full_text,
            # Keep the move, layer, power, and speed visible.  Coordinates
            # are still available through ``full_text`` and the tooltip when
            # the tail must be elided on compact or large-font layouts.
            QtCore.Qt.TextElideMode.ElideRight,
            width,
        )
        super().setText(rendered)
        self.setToolTip(
            html.escape(self._full_text, quote=True)
            if rendered != self._full_text
            else ""
        )

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_text()

    def minimumSizeHint(self) -> QtCore.QSize:
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint


class JobPreviewCanvas(QtWidgets.QGraphicsView):
    buildProgress = QtCore.Signal(int, int)
    buildFinished = QtCore.Signal()
    buildFailed = QtCore.Signal(str)

    def __init__(
        self,
        plan: JobPlan,
        work_area: tuple[float, float, float, float],
        parent: QtWidgets.QWidget | None = None,
        *,
        move_ends: tuple[float, ...] | None = None,
        defer_render: bool = False,
        coordinate_frame: HoneycombCoordinateFrame | None = None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self.work_area = tuple(float(value) for value in work_area)
        self.coordinate_frame = coordinate_frame
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.TextAntialiasing
        )
        self.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QtWidgets.QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(
            QtWidgets.QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        self._show_travel = True
        self._hidden_layers: set[str] = set()
        self._shade_power = False
        self._inverted = False
        self._elapsed = plan.total_seconds
        self._move_ends = (
            tuple(move_ends)
            if move_ends is not None
            else tuple(move.end_seconds for move in plan.moves)
        )
        self._rendered_count = 0
        self._paths: dict[RenderKey, QtGui.QPainterPath] = {}
        self._items: dict[RenderKey, QtWidgets.QGraphicsPathItem] = {}
        self._representative_moves: dict[RenderKey, PlannedMove] = {}
        self._active_item = QtWidgets.QGraphicsLineItem()
        active_pen = QtGui.QPen(QtGui.QColor("#FFD54F"))
        active_pen.setWidthF(1.1)
        active_pen.setCosmetic(True)
        self._active_item.setPen(active_pen)
        self._active_item.setZValue(30.0)
        self._scene.addItem(self._active_item)
        self._head_item = QtWidgets.QGraphicsEllipseItem(-1.2, -1.2, 2.4, 2.4)
        head_pen = QtGui.QPen(QtGui.QColor("#00D4FF"))
        head_pen.setCosmetic(True)
        head_pen.setWidthF(1.2)
        self._head_item.setPen(head_pen)
        self._head_item.setBrush(QtGui.QColor("#062E37"))
        self._head_item.setZValue(40.0)
        self._scene.addItem(self._head_item)
        self._defer_render = bool(defer_render)
        self._building = False
        self._initial_build = False
        self._build_index = 0
        self._build_target = 0
        self._full_paths: dict[RenderKey, QtGui.QPainterPath] = {}
        self._timeline_async_ready = False
        self._build_timer = QtCore.QTimer(self)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._build_slice)
        self._draw_bed()
        if defer_render:
            self._active_item.hide()
            self._head_item.hide()
        else:
            self.set_elapsed(plan.total_seconds)
        self._timeline_async_ready = True

    def _draw_bed(self) -> None:
        x_min, x_max, y_min, y_max = self.work_area
        plan_x_min, plan_y_min, plan_x_max, plan_y_max = self.plan.bounds_mm
        display_points = tuple(
            self._display_point(x, y)
            for x, y in (
                (plan_x_min, plan_y_min),
                (plan_x_max, plan_y_min),
                (plan_x_max, plan_y_max),
                (plan_x_min, plan_y_max),
            )
        )
        content_x_min = min([x_min, *(point[0] for point in display_points)])
        content_x_max = max([x_max, *(point[0] for point in display_points)])
        content_y_min = min([y_min, *(point[1] for point in display_points)])
        content_y_max = max([y_max, *(point[1] for point in display_points)])
        margin = max(
            content_x_max - content_x_min,
            content_y_max - content_y_min,
        ) * 0.06
        scene_rect = QtCore.QRectF(
            content_x_min - margin,
            -(content_y_max + margin),
            content_x_max - content_x_min + 2 * margin,
            content_y_max - content_y_min + 2 * margin,
        )
        self._scene.setSceneRect(scene_rect)
        bed = QtWidgets.QGraphicsRectItem(
            QtCore.QRectF(x_min, -y_max, x_max - x_min, y_max - y_min)
        )
        pen = QtGui.QPen(QtGui.QColor("#39B878"))
        pen.setWidthF(1.2)
        pen.setCosmetic(True)
        bed.setPen(pen)
        bed.setBrush(QtGui.QColor("#F4F5F2"))
        bed.setZValue(-20.0)
        self._scene.addItem(bed)
        self.setBackgroundBrush(QtGui.QColor("#202326"))

    def _display_point(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        if self.coordinate_frame is None:
            return float(x_mm), float(y_mm)
        return self.coordinate_frame.machine_to_local(x_mm, y_mm)

    @staticmethod
    def _key(move: PlannedMove) -> RenderKey:
        if move.rapid:
            return "travel", "", None
        if not move.laser_on:
            return "unpowered", move.layer_id or move.layer_name, None
        return "powered", move.layer_id or move.layer_name, float(move.power)

    def _pen(self, key: RenderKey, move: PlannedMove) -> QtGui.QPen:
        role, _, _ = key
        if role == "travel":
            pen = QtGui.QPen(QtGui.QColor("#E45A5A"))
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            pen.setWidthF(0.55)
        elif role == "unpowered":
            pen = QtGui.QPen(QtGui.QColor("#E7B55C"))
            pen.setWidthF(0.55)
        elif self._shade_power:
            ratio = max(0.0, min(1.0, move.power / max(1, self.plan.power_max)))
            value = int(round(225 - ratio * 190))
            if self._inverted:
                value = 255 - value
            pen = QtGui.QPen(QtGui.QColor(value, value, value))
            pen.setWidthF(0.75)
        else:
            pen = QtGui.QPen(QtGui.QColor(_display_color(move.layer_color, "#101010")))
            pen.setWidthF(0.75)
        pen.setCosmetic(True)
        return pen

    def _ensure_item(
        self,
        key: RenderKey,
        move: PlannedMove,
    ) -> QtWidgets.QGraphicsPathItem:
        item = self._items.get(key)
        if item is None:
            item = QtWidgets.QGraphicsPathItem()
            item.setPen(self._pen(key, move))
            item.setZValue(10.0 if key[0] == "powered" else 5.0)
            self._scene.addItem(item)
            self._items[key] = item
            self._paths[key] = QtGui.QPainterPath()
            self._representative_moves[key] = move
        return item

    def _reset_paths(self) -> None:
        for key, item in self._items.items():
            self._paths[key] = QtGui.QPainterPath()
            item.setPath(QtGui.QPainterPath())
        self._rendered_count = 0

    def _append_through(self, count: int, *, commit: bool = True) -> None:
        changed: set[RenderKey] = set()
        for move in self.plan.moves[self._rendered_count : count]:
            key = self._key(move)
            self._ensure_item(key, move)
            path = self._paths[key]
            start_x, start_y = self._display_point(move.start_x, move.start_y)
            end_x, end_y = self._display_point(move.end_x, move.end_y)
            path.moveTo(start_x, -start_y)
            path.lineTo(end_x, -end_y)
            changed.add(key)
        if commit:
            for key in changed:
                self._items[key].setPath(self._paths[key])
        self._rendered_count = count

    def start_deferred_render(self) -> None:
        if not self._defer_render or self._building:
            return
        self._building = True
        self._initial_build = True
        self._build_index = 0
        self._build_target = len(self.plan.moves)
        if not self.plan.moves:
            self._finish_path_build()
            return
        self._build_timer.start()

    def cancel_deferred_render(self) -> None:
        self._build_timer.stop()
        self._building = False

    def _start_timeline_render(self, count: int) -> None:
        self._build_timer.stop()
        self._building = True
        self._initial_build = False
        self._build_index = 0
        self._build_target = max(0, min(int(count), len(self.plan.moves)))
        self._reset_paths()
        if self._build_target == 0:
            self._finish_path_build()
        else:
            self._build_timer.start()

    @QtCore.Slot()
    def _build_slice(self) -> None:
        if not self._building:
            return
        try:
            timer = QtCore.QElapsedTimer()
            timer.start()
            count = self._build_index
            while count < self._build_target and count - self._build_index < 1_000:
                move = self.plan.moves[count]
                key = self._key(move)
                self._ensure_item(key, move)
                path = self._paths[key]
                start_x, start_y = self._display_point(move.start_x, move.start_y)
                end_x, end_y = self._display_point(move.end_x, move.end_y)
                path.moveTo(start_x, -start_y)
                path.lineTo(end_x, -end_y)
                count += 1
                if timer.elapsed() >= 8:
                    break
            self._build_index = count
            self._rendered_count = count
            if self._initial_build:
                self.buildProgress.emit(count, self._build_target)
            if count >= self._build_target:
                self._finish_path_build()
        except Exception as exc:
            self._fail_path_build(str(exc))

    def _finish_path_build(self) -> None:
        try:
            self._build_timer.stop()
            for key, path in self._paths.items():
                self._items[key].setPath(path)
            self._rendered_count = self._build_target
            initial = self._initial_build
            if initial:
                self._full_paths = {
                    key: QtGui.QPainterPath(path)
                    for key, path in self._paths.items()
                }
            self._building = False
            self._initial_build = False
            self._update_item_visibility()
            if initial:
                self._defer_render = False
                self.set_elapsed(self.plan.total_seconds)
                self.buildFinished.emit()
        except Exception as exc:
            self._fail_path_build(str(exc))

    def _fail_path_build(self, message: str) -> None:
        self._build_timer.stop()
        self._building = False
        self._initial_build = False
        self.buildFailed.emit(str(message))

    def _restore_full_paths(self) -> bool:
        if not self._full_paths:
            return False
        self._build_timer.stop()
        self._building = False
        self._initial_build = False
        self._paths = {
            key: QtGui.QPainterPath(path)
            for key, path in self._full_paths.items()
        }
        for key, path in self._paths.items():
            self._items[key].setPath(path)
        self._rendered_count = len(self.plan.moves)
        return True

    def set_elapsed(self, seconds: float) -> None:
        self._elapsed = max(0.0, min(float(seconds), self.plan.total_seconds))
        completed = bisect.bisect_right(self._move_ends, self._elapsed)
        if self._timeline_async_ready:
            if completed == len(self.plan.moves) and self._restore_full_paths():
                pass
            elif self._building or completed < self._rendered_count:
                self._start_timeline_render(completed)
            elif completed - self._rendered_count > 1_000:
                self._start_timeline_render(completed)
            else:
                self._append_through(completed)
        else:
            if completed < self._rendered_count:
                self._reset_paths()
            self._append_through(completed)
        self._update_item_visibility()
        self._update_position()

    def _update_item_visibility(self) -> None:
        for key, item in self._items.items():
            item.setVisible(
                (self._show_travel or key[0] != "travel")
                and (not key[1] or key[1] not in self._hidden_layers)
            )

    def _update_position(self) -> None:
        active = self.move_at(self._elapsed)
        if active is None:
            self._active_item.hide()
            if self.plan.moves:
                final = self.plan.moves[-1]
                end_x, end_y = self._display_point(final.end_x, final.end_y)
                self._head_item.setPos(end_x, -end_y)
                self._head_item.show()
            else:
                self._head_item.hide()
            return
        fraction = (
            1.0
            if active.duration_seconds <= 1e-9
            else (self._elapsed - active.start_seconds) / active.duration_seconds
        )
        fraction = max(0.0, min(1.0, fraction))
        x = active.start_x + (active.end_x - active.start_x) * fraction
        y = active.start_y + (active.end_y - active.start_y) * fraction
        start_x, start_y = self._display_point(active.start_x, active.start_y)
        x, y = self._display_point(x, y)
        self._active_item.setLine(start_x, -start_y, x, -y)
        self._active_item.show()
        self._head_item.setPos(x, -y)
        self._head_item.show()

    def move_at(self, seconds: float) -> PlannedMove | None:
        if not self.plan.moves or seconds >= self.plan.total_seconds:
            return None
        index = bisect.bisect_right(self._move_ends, max(0.0, float(seconds)))
        return self.plan.moves[min(index, len(self.plan.moves) - 1)]

    def set_show_travel(self, enabled: bool) -> None:
        self._show_travel = bool(enabled)
        self.set_elapsed(self._elapsed)

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        if visible:
            self._hidden_layers.discard(str(layer_id))
        else:
            self._hidden_layers.add(str(layer_id))
        self.set_elapsed(self._elapsed)

    def set_power_shading(self, enabled: bool) -> None:
        self._shade_power = bool(enabled)
        for key, item in self._items.items():
            item.setPen(self._pen(key, self._representative_moves[key]))

    def set_inverted(self, enabled: bool) -> None:
        self._inverted = bool(enabled)
        self.setBackgroundBrush(QtGui.QColor("#F0F0F0" if enabled else "#202326"))
        for item in self._scene.items():
            if isinstance(item, QtWidgets.QGraphicsRectItem) and item.zValue() < 0:
                item.setBrush(QtGui.QColor("#17191B" if enabled else "#F4F5F2"))
        self.set_power_shading(self._shade_power)

    def fit_job(self) -> None:
        self.fitInView(self._scene.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        factor = 1.18 if event.angleDelta().y() > 0 else 1.0 / 1.18
        self.scale(factor, factor)
        event.accept()

    def render_image(self, path: Path, width: int = 1600) -> None:
        rect = self._scene.sceneRect()
        height = max(1, int(round(width * rect.height() / max(rect.width(), 1e-9))))
        image = QtGui.QImage(
            width,
            height,
            QtGui.QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(self.backgroundBrush().color())
        painter = QtGui.QPainter(image)
        self._scene.render(painter, QtCore.QRectF(0, 0, width, height), rect)
        painter.end()
        if not image.save(str(path)):
            raise OSError(f"Could not save preview image to {path}")


class JobPreviewDialog(QtWidgets.QDialog):
    startHereRequested = QtCore.Signal(int)
    renderProgress = QtCore.Signal(int, int)
    renderFinished = QtCore.Signal()
    renderCancelled = QtCore.Signal()
    renderFailed = QtCore.Signal(str)

    def __init__(
        self,
        plan: JobPlan,
        work_area: tuple[float, float, float, float],
        job_name: str,
        parent: QtWidgets.QWidget | None = None,
        *,
        prepared: PreparedJobPreview | None = None,
        defer_render: bool = False,
        max_work_feed_mm_min: float | None = None,
        max_travel_feed_mm_min: float | None = None,
        coordinate_frame: HoneycombCoordinateFrame | None = None,
    ) -> None:
        super().__init__(parent)
        self.plan = plan
        self.prepared = prepared or prepare_job_preview(plan)
        self.coordinate_frame = coordinate_frame
        self.max_work_feed_mm_min = max_work_feed_mm_min
        self.max_travel_feed_mm_min = max_travel_feed_mm_min
        self._deferred_render = bool(defer_render)
        self._render_completed = not self._deferred_render
        self.setWindowTitle(f"Job Preview — {job_name}")
        self.setModal(False)
        minimum_size, initial_size = self._screen_limited_sizes()
        self.setMinimumSize(minimum_size)
        self.resize(initial_size)
        self._elapsed = plan.total_seconds
        self._playback_speed = 1.0
        self._current_move_index: int | None = None
        self._clock = QtCore.QElapsedTimer()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._playback_tick)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.heading = QtWidgets.QLabel(
            f"Exact generated job · {plan.planner_mode} · preview controls do not "
            "alter machine output"
        )
        self.heading.setObjectName("panelHeading")
        self.heading.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        self.heading.setMinimumWidth(0)
        layout.addWidget(self.heading)
        completion_note = QtWidgets.QLabel(
            "The cyan head marker is the end of the generated G-code stream. "
            "On a successful powered hardware run, configured Home / park and "
            "motor-release completion runs afterward and is not drawn here."
        )
        completion_note.setObjectName("mutedLabel")
        completion_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        completion_note.setWordWrap(True)
        completion_note.setMinimumWidth(0)
        layout.addWidget(completion_note)

        self.render_progress = QtWidgets.QProgressBar()
        self.render_progress.setObjectName("jobPreviewBuildProgress")
        self.render_progress.setRange(0, max(1, len(plan.moves)))
        self.render_progress.setValue(0)
        self.render_progress.setFormat("Building exact preview %p%")
        self.render_progress.setVisible(self._deferred_render)
        layout.addWidget(self.render_progress)

        self.body_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.body_splitter.setObjectName("jobPreviewBody")
        self.body_splitter.setChildrenCollapsible(False)
        layout.addWidget(self.body_splitter, 1)

        self.canvas = JobPreviewCanvas(
            plan,
            work_area,
            self,
            move_ends=self.prepared.move_ends,
            defer_render=self._deferred_render,
            coordinate_frame=coordinate_frame,
        )
        self.canvas.setObjectName("jobPreviewCanvas")
        self.canvas.setMinimumSize(_MIN_CANVAS_WIDTH, _MIN_CANVAS_HEIGHT)
        self.body_splitter.addWidget(self.canvas)

        self.sidebar = QtWidgets.QScrollArea()
        self.sidebar.setObjectName("jobPreviewSidebar")
        self.sidebar.setWidgetResizable(True)
        self.sidebar.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.sidebar.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.sidebar.setMinimumWidth(_MIN_SIDEBAR_WIDTH)
        side_page = QtWidgets.QWidget()
        side_page.setObjectName("jobPreviewSidebarPage")
        side_page.setMinimumWidth(0)
        side_page.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        side_layout = QtWidgets.QVBoxLayout(side_page)
        side_layout.setContentsMargins(4, 0, 4, 0)
        side_layout.setSpacing(6)

        self.layer_tree = self._build_layer_tree()
        side_layout.addWidget(self.layer_tree)

        display_group = QtWidgets.QGroupBox("Display")
        display_layout = QtWidgets.QGridLayout(display_group)
        display_layout.setContentsMargins(6, 8, 6, 6)
        display_layout.setHorizontalSpacing(8)
        display_layout.setVerticalSpacing(4)
        self.travel_check = QtWidgets.QCheckBox("Show traversal moves")
        self.travel_check.setChecked(True)
        self.power_check = QtWidgets.QCheckBox("Shade according to power")
        self.invert_check = QtWidgets.QCheckBox("Invert dark material")
        self.legend_check = QtWidgets.QCheckBox("Show legend")
        self.legend_check.setChecked(True)
        self.fit_button = QtWidgets.QPushButton("Fit bed")
        display_layout.addWidget(self.travel_check, 0, 0)
        display_layout.addWidget(self.power_check, 1, 0)
        display_layout.addWidget(self.invert_check, 2, 0)
        display_layout.addWidget(self.legend_check, 3, 0)
        display_layout.addWidget(self.fit_button, 4, 0)
        side_layout.addWidget(display_group)

        self.statistics = QtWidgets.QLabel(
            f"Cut {plan.cut_distance_mm:.1f} mm ({_duration(plan.cut_seconds)}) · "
            f"Rapid/off {plan.travel_distance_mm:.1f} mm "
            f"({_duration(plan.travel_seconds)}) · Total {_duration(plan.total_seconds)} · "
            f"Max power {plan.maximum_power / plan.power_max * 100:.1f}% / "
            f"S{plan.maximum_power:g}"
        )
        if plan.source_order_travel_mm is not None and plan.planner_mode != "source order":
            self.statistics.setText(
                self.statistics.text()
                + f" · Planner saved {plan.planner_savings_mm:.1f} mm rapid travel"
            )
        self.statistics.setObjectName("jobPreviewStatistics")
        self.statistics.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.statistics.setWordWrap(True)
        self.statistics.setMinimumWidth(0)
        self.statistics.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        side_layout.addWidget(self.statistics)

        self.legend = QtWidgets.QLabel(self._legend_html())
        self.legend.setObjectName("jobPreviewLegend")
        self.legend.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.legend.setWordWrap(True)
        self.legend.setMinimumWidth(0)
        self.legend.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.legend_check.toggled.connect(self.legend.setVisible)
        side_layout.addWidget(self.legend)

        self.warning_label: QtWidgets.QLabel | None = None
        if plan.warnings:
            self.warning_label = QtWidgets.QLabel(
                "Warnings: " + " · ".join(plan.warnings)
            )
            self.warning_label.setObjectName("warningLabel")
            self.warning_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            self.warning_label.setWordWrap(True)
            self.warning_label.setMinimumWidth(0)
            self.warning_label.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored,
                QtWidgets.QSizePolicy.Policy.Preferred,
            )
            side_layout.addWidget(self.warning_label)
        side_layout.addStretch(1)
        self.sidebar.setWidget(side_page)
        self.body_splitter.addWidget(self.sidebar)
        self.body_splitter.setStretchFactor(0, 1)
        self.body_splitter.setStretchFactor(1, 0)
        self.body_splitter.setSizes(
            [
                max(
                    _MIN_CANVAS_WIDTH,
                    initial_size.width() - _DEFAULT_SIDEBAR_WIDTH - 20,
                ),
                _DEFAULT_SIDEBAR_WIDTH,
            ]
        )

        self.travel_check.toggled.connect(self.canvas.set_show_travel)
        self.power_check.toggled.connect(self.canvas.set_power_shading)
        self.invert_check.toggled.connect(self.canvas.set_inverted)
        self.fit_button.clicked.connect(self.canvas.fit_job)
        self.canvas.buildProgress.connect(self._render_progressed)
        self.canvas.buildFinished.connect(self._render_finished)
        self.canvas.buildFailed.connect(self._render_failed)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setObjectName("jobPreviewTimeline")
        self.slider.setRange(0, 10_000)
        self.slider.setValue(10_000)
        self.slider.valueChanged.connect(self._slider_changed)
        layout.addWidget(self.slider)

        playback = QtWidgets.QGridLayout()
        playback.setHorizontalSpacing(6)
        playback.setVerticalSpacing(2)
        self.reset_button = QtWidgets.QPushButton("⏮ Start")
        self.play_button = QtWidgets.QPushButton("▶ Play")
        self.speed_combo = QtWidgets.QComboBox()
        for value in (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 40.0):
            self.speed_combo.addItem(f"{value:g}×", value)
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(1.0))
        self.speed_combo.currentIndexChanged.connect(
            lambda: setattr(self, "_playback_speed", float(self.speed_combo.currentData()))
        )
        self.time_label = QtWidgets.QLabel()
        self.time_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.move_label = _ElidedLabel()
        self.move_label.setObjectName("jobPreviewMove")
        self.move_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        speed_label = QtWidgets.QLabel("Speed")
        speed_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        playback.addWidget(self.reset_button, 0, 0)
        playback.addWidget(self.play_button, 0, 1)
        playback.addWidget(speed_label, 0, 2)
        playback.addWidget(self.speed_combo, 0, 3)
        playback.addWidget(self.time_label, 0, 4)
        playback.setColumnStretch(5, 1)
        playback.addWidget(self.move_label, 1, 0, 1, 6)
        layout.addLayout(playback)
        self.reset_button.clicked.connect(lambda: self.set_elapsed(0.0))
        self.play_button.clicked.connect(self._toggle_playback)

        keyboard_help = QtWidgets.QLabel(
            "Space play/pause · Home/End timeline · Left/Right step one second"
        )
        keyboard_help.setObjectName("mutedLabel")
        keyboard_help.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        keyboard_help.setWordWrap(True)
        keyboard_help.setMinimumWidth(0)
        layout.addWidget(keyboard_help)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.setObjectName("jobPreviewButtons")
        self.close_button = buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        self.save_button = buttons.addButton(
            "Save preview image…",
            QtWidgets.QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.start_here_button = buttons.addButton(
            "Prepare Start Here…",
            QtWidgets.QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.start_here_button.setToolTip(
            "Prepare a new guarded job beginning at the currently reviewed move; "
            "this does not start the machine"
        )
        self.start_here_button.clicked.connect(self._request_start_here)
        self.save_button.clicked.connect(self._save_image)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        if self._deferred_render:
            self.close_button.setEnabled(False)
            self.close_button.setToolTip(
                "Wait for exact preview construction to finish before closing"
            )
            for control in (
                self.slider,
                self.reset_button,
                self.play_button,
                self.speed_combo,
                self.start_here_button,
            ):
                control.setEnabled(False)
            self.time_label.setText("Building exact preview…")
            self.move_label.set_full_text("Preview construction in progress")
            QtCore.QTimer.singleShot(0, self.canvas.start_deferred_render)
        else:
            self.set_elapsed(plan.total_seconds)
            QtCore.QTimer.singleShot(0, self.canvas.fit_job)

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

    def _layer_rows(self) -> tuple[PreviewLayerRow, ...]:
        return self.prepared.layer_rows

    def _build_layer_tree(self) -> QtWidgets.QTreeWidget:
        tree = QtWidgets.QTreeWidget()
        tree.setObjectName("previewLayers")
        tree.setHeaderLabels(
            ("Show", "Operation", "Cut", "Time", "Speed", "Correction", "Max power")
        )
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setMinimumWidth(0)
        tree.setMinimumHeight(130)
        tree.setMaximumHeight(220)
        tree.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        tree.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        tree.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        for row in self._layer_rows():
            item = QtWidgets.QTreeWidgetItem(
                [
                    "",
                    f"{row.name} · {row.mode.title()}",
                    f"{row.distance:.1f} mm",
                    _duration(row.seconds),
                    _speed_text(
                        row.feeds,
                        maximum_feed_mm_min=self.max_work_feed_mm_min,
                    ),
                    f"V {row.vector_power_correction:+g} / "
                    f"R {row.raster_power_correction:+g}",
                    f"{row.power / self.plan.power_max * 100:.1f}% / "
                    f"S{row.power:g}",
                ]
            )
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, row.id)
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.CheckState.Checked)
            swatch = QtGui.QPixmap(12, 12)
            swatch.fill(QtGui.QColor(_display_color(row.color)))
            item.setIcon(1, QtGui.QIcon(swatch))
            details = " · ".join(
                (
                    _speed_text(
                        row.feeds,
                        maximum_feed_mm_min=self.max_work_feed_mm_min,
                        detailed=True,
                    )
                    if column == 4
                    else item.text(column)
                )
                for column in range(1, 7)
            )
            for column in range(tree.columnCount()):
                item.setToolTip(column, html.escape(details, quote=True))
            tree.addTopLevelItem(item)
        tree.itemChanged.connect(self._layer_visibility_changed)
        header = tree.header()
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(28)
        for column in range(tree.columnCount()):
            header.setSectionResizeMode(
                column,
                QtWidgets.QHeaderView.ResizeMode.Interactive,
            )
        for column, width in enumerate((28, 48, 34, 30, 44, 48, 70)):
            header.resizeSection(column, width)
        return tree

    def _layer_visibility_changed(
        self,
        item: QtWidgets.QTreeWidgetItem,
        column: int,
    ) -> None:
        if column != 0:
            return
        layer_id = str(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
        self.canvas.set_layer_visible(
            layer_id,
            item.checkState(0) == QtCore.Qt.CheckState.Checked,
        )

    def _legend_html(self) -> str:
        layers = " · ".join(
            "<span style='color:"
            + _display_color(row.color)
            + "'>— "
            + html.escape(row.name, quote=True)
            + "</span>"
            for row in self._layer_rows()
        )
        prefix = f"{layers} · " if layers else ""
        return (
            prefix
            + "<span style='color:#E45A5A'>-- Travel</span> · "
            "<span style='color:#E7B55C'>— Laser-off feed</span> · "
            "<span style='color:#00D4FF'>● Head</span>"
        )

    def set_elapsed(self, seconds: float) -> None:
        self._elapsed = max(0.0, min(float(seconds), self.plan.total_seconds))
        ratio = 0.0 if self.plan.total_seconds <= 0 else self._elapsed / self.plan.total_seconds
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(ratio * 10_000)))
        self.slider.blockSignals(False)
        self.canvas.set_elapsed(self._elapsed)
        self.time_label.setText(
            f"{_duration(self._elapsed)} / {_duration(self.plan.total_seconds)}"
        )
        move = self.canvas.move_at(self._elapsed)
        if move is None:
            self._current_move_index = None
            self.move_label.set_full_text("Complete")
        else:
            self._current_move_index = move.index
            power = move.power / self.plan.power_max * 100.0
            role = (
                "RAPID · laser off"
                if move.rapid
                else (
                    f"POWER {power:.1f}% / S{move.power:g}"
                    if move.laser_on
                    else "FEED · laser off"
                )
            )
            coordinate_text = f"Machine X{move.end_x:.3f} Y{move.end_y:.3f}"
            if self.coordinate_frame is not None:
                local_x, local_y = self.coordinate_frame.machine_to_local(
                    move.end_x,
                    move.end_y,
                )
                coordinate_text = (
                    f"Honeycomb X{local_x:.3f} Y{local_y:.3f} · "
                    + coordinate_text
                )
            self.move_label.set_full_text(
                f"Move {move.index + 1}/{len(self.plan.moves)} · {move.layer_name} · "
                f"pass {move.pass_index}/{move.pass_count} · {role} · "
                f"{_speed_text((move.feed_mm_min,), maximum_feed_mm_min=(self.max_travel_feed_mm_min if move.rapid else self.max_work_feed_mm_min))} "
                f"· {coordinate_text}"
            )
        self.start_here_button.setEnabled(self._current_move_index is not None)

    def _slider_changed(self, value: int) -> None:
        self.set_elapsed(self.plan.total_seconds * value / 10_000.0)

    @QtCore.Slot(int, int)
    def _render_progressed(self, completed: int, total: int) -> None:
        self.render_progress.setMaximum(max(1, total))
        self.render_progress.setValue(completed)
        self.renderProgress.emit(completed, total)

    @QtCore.Slot()
    def _render_finished(self) -> None:
        self._render_completed = True
        self._deferred_render = False
        self.render_progress.hide()
        self.close_button.setEnabled(True)
        self.close_button.setToolTip("")
        for control in (
            self.slider,
            self.reset_button,
            self.play_button,
            self.speed_combo,
        ):
            control.setEnabled(True)
        self.set_elapsed(self.plan.total_seconds)
        QtCore.QTimer.singleShot(0, self.canvas.fit_job)
        self.renderFinished.emit()

    @QtCore.Slot(str)
    def _render_failed(self, message: str) -> None:
        self._deferred_render = False
        self.renderFailed.emit(str(message))

    def _toggle_playback(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self.play_button.setText("▶ Play")
            return
        if self._elapsed >= self.plan.total_seconds:
            self.set_elapsed(0.0)
        self._clock.restart()
        self._timer.start()
        self.play_button.setText("⏸ Pause")

    def _playback_tick(self) -> None:
        delta = self._clock.restart() / 1000.0 * self._playback_speed
        self.set_elapsed(self._elapsed + delta)
        if self._elapsed >= self.plan.total_seconds:
            self._timer.stop()
            self.play_button.setText("▶ Play")

    def _save_image(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save job preview",
            "job-preview.png",
            "PNG image (*.png)",
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != ".png":
            path = path.with_suffix(".png")
        try:
            self.canvas.render_image(path)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Job Preview", str(exc))

    def _request_start_here(self) -> None:
        if self._current_move_index is not None:
            self.startHereRequested.emit(self._current_move_index)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._timer.stop()
        cancelled = self._deferred_render and not self._render_completed
        self.canvas.cancel_deferred_render()
        self._deferred_render = False
        if cancelled:
            self.renderCancelled.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        if key == QtCore.Qt.Key.Key_Space:
            self._toggle_playback()
        elif key == QtCore.Qt.Key.Key_Home:
            self.set_elapsed(0.0)
        elif key == QtCore.Qt.Key.Key_End:
            self.set_elapsed(self.plan.total_seconds)
        elif key == QtCore.Qt.Key.Key_Left:
            self.set_elapsed(self._elapsed - 1.0)
        elif key == QtCore.Qt.Key.Key_Right:
            self.set_elapsed(self._elapsed + 1.0)
        else:
            super().keyPressEvent(event)
            return
        event.accept()
