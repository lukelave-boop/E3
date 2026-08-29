from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ..project import SceneObject
from .qt import require_qt

QtCore, QtGui, _QtWidgets = require_qt()

TextVectorMode = Literal["outline", "stencil"]


@dataclass(frozen=True, slots=True)
class TextVectorOptions:
    text: str
    font_family: str
    height_mm: float
    mode: TextVectorMode = "outline"
    bridge_width_mm: float | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Text cannot be empty")
        if not self.font_family.strip():
            raise ValueError("Choose a font")
        if not math.isfinite(self.height_mm) or self.height_mm <= 0:
            raise ValueError("Text height must be positive")
        if self.mode not in {"outline", "stencil"}:
            raise ValueError(f"Unsupported text mode: {self.mode}")
        if self.bridge_width_mm is not None and (
            not math.isfinite(self.bridge_width_mm)
            or self.bridge_width_mm <= 0
        ):
            raise ValueError("Bridge width must be positive")


def automatic_bridge_width(height_mm: float) -> float:
    return max(0.8, min(4.0, float(height_mm) * 0.08))


def _plain_text_path(
    text: str,
    font_family: str,
) -> QtGui.QPainterPath:
    font = QtGui.QFont(font_family)
    font.setPixelSize(1000)
    metrics = QtGui.QFontMetricsF(font)
    lines = text.splitlines() or [text]
    path = QtGui.QPainterPath()
    line_advance = metrics.lineSpacing() * 1.08
    baseline = metrics.ascent()
    widths = [metrics.horizontalAdvance(line or " ") for line in lines]
    widest = max(widths, default=1.0)
    for index, line in enumerate(lines):
        if not line:
            continue
        x = (widest - widths[index]) / 2.0
        y = baseline + index * line_advance
        path.addText(QtCore.QPointF(x, y), font, line)
    path.setFillRule(QtCore.Qt.FillRule.OddEvenFill)
    if path.isEmpty():
        raise ValueError("The selected font produced no vector outlines")
    return path


def _signed_polygon_area(points: list[QtCore.QPointF]) -> float:
    if len(points) < 3:
        return 0.0
    return 0.5 * sum(
        start.x() * end.y() - end.x() * start.y()
        for start, end in zip(points, points[1:] + points[:1], strict=False)
    )


def _polygon_sample(
    polygon: QtGui.QPolygonF,
) -> QtCore.QPointF:
    points = [QtCore.QPointF(point) for point in polygon]
    if len(points) > 1 and QtCore.QLineF(points[0], points[-1]).length() <= 1e-7:
        points.pop()
    area = _signed_polygon_area(points)
    if abs(area) > 1e-9:
        x = 0.0
        y = 0.0
        factor = 0.0
        for start, end in zip(points, points[1:] + points[:1], strict=False):
            cross = start.x() * end.y() - end.x() * start.y()
            x += (start.x() + end.x()) * cross
            y += (start.y() + end.y()) * cross
            factor += cross
        centroid = QtCore.QPointF(x / (3.0 * factor), y / (3.0 * factor))
        if polygon.containsPoint(
            centroid,
            QtCore.Qt.FillRule.OddEvenFill,
        ):
            return centroid
    bounds = polygon.boundingRect()
    center = bounds.center()
    if polygon.containsPoint(center, QtCore.Qt.FillRule.OddEvenFill):
        return center
    for x_index in range(1, 10):
        for y_index in range(1, 10):
            point = QtCore.QPointF(
                bounds.left() + bounds.width() * x_index / 10.0,
                bounds.top() + bounds.height() * y_index / 10.0,
            )
            if polygon.containsPoint(point, QtCore.Qt.FillRule.OddEvenFill):
                return point
    return points[0] if points else center


def _bridge_rectangle(
    hole: QtGui.QPolygonF,
    parent: QtGui.QPolygonF,
    width: float,
) -> QtCore.QRectF:
    hole_bounds = hole.boundingRect()
    parent_bounds = parent.boundingRect()
    center = _polygon_sample(hole)
    candidates = (
        (
            max(0.0, hole_bounds.left() - parent_bounds.left()),
            QtCore.QRectF(
                parent_bounds.left(),
                center.y() - width / 2.0,
                center.x() - parent_bounds.left(),
                width,
            ),
        ),
        (
            max(0.0, parent_bounds.right() - hole_bounds.right()),
            QtCore.QRectF(
                center.x(),
                center.y() - width / 2.0,
                parent_bounds.right() - center.x(),
                width,
            ),
        ),
        (
            max(0.0, hole_bounds.top() - parent_bounds.top()),
            QtCore.QRectF(
                center.x() - width / 2.0,
                parent_bounds.top(),
                width,
                center.y() - parent_bounds.top(),
            ),
        ),
        (
            max(0.0, parent_bounds.bottom() - hole_bounds.bottom()),
            QtCore.QRectF(
                center.x() - width / 2.0,
                center.y(),
                width,
                parent_bounds.bottom() - center.y(),
            ),
        ),
    )
    return min(candidates, key=lambda item: item[0])[1].normalized()


def _stencil_path(
    path: QtGui.QPainterPath,
    bridge_width_units: float,
) -> tuple[QtGui.QPainterPath, int]:
    polygons = [
        polygon
        for polygon in path.toSubpathPolygons()
        if polygon.size() >= 3
    ]
    if not polygons:
        return path, 0
    samples = [_polygon_sample(polygon) for polygon in polygons]
    areas = [abs(_signed_polygon_area(list(polygon))) for polygon in polygons]
    bridges = QtGui.QPainterPath()
    bridge_count = 0
    for index, polygon in enumerate(polygons):
        containers = [
            parent_index
            for parent_index, parent in enumerate(polygons)
            if parent_index != index
            and areas[parent_index] > areas[index]
            and parent.containsPoint(
                samples[index],
                QtCore.Qt.FillRule.OddEvenFill,
            )
        ]
        if len(containers) % 2 == 0:
            continue
        parent_index = min(containers, key=lambda candidate: areas[candidate])
        parent_path = QtGui.QPainterPath()
        parent_path.addPolygon(polygons[parent_index])
        parent_path.closeSubpath()
        bridge_path = QtGui.QPainterPath()
        bridge_path.addRect(
            _bridge_rectangle(
                polygon,
                polygons[parent_index],
                bridge_width_units,
            )
        )
        bridges.addPath(bridge_path.intersected(parent_path))
        bridge_count += 1
    if bridge_count == 0:
        return path, 0
    output = path.subtracted(bridges)
    output.setFillRule(QtCore.Qt.FillRule.OddEvenFill)
    return output, bridge_count


def build_vector_text_path(
    options: TextVectorOptions,
) -> tuple[QtGui.QPainterPath, float, int]:
    path = _plain_text_path(options.text, options.font_family)
    initial_bounds = path.boundingRect()
    if initial_bounds.height() <= 1e-9:
        raise ValueError("The selected font produced zero-height text")
    scale = options.height_mm / initial_bounds.height()
    bridge_width_mm = (
        automatic_bridge_width(options.height_mm)
        if options.bridge_width_mm is None
        else options.bridge_width_mm
    )
    bridge_count = 0
    if options.mode == "stencil":
        path, bridge_count = _stencil_path(
            path,
            bridge_width_mm / scale,
        )
        if path.isEmpty():
            raise ValueError(
                "The bridge width removed all usable text geometry; choose a smaller width"
            )
    bounds = path.boundingRect()
    center = bounds.center()
    transform = QtGui.QTransform(
        scale,
        0.0,
        0.0,
        scale,
        -center.x() * scale,
        -center.y() * scale,
    )
    path = transform.map(path)
    return path, bridge_width_mm, bridge_count


def painter_path_polylines(
    path: QtGui.QPainterPath,
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for polygon in path.toSubpathPolygons():
        points = [
            [float(point.x()), float(-point.y())]
            for point in polygon
        ]
        if len(points) < 3:
            continue
        if math.dist(points[0], points[-1]) <= 1e-7:
            points.pop()
        if len(points) < 3:
            continue
        output.append({"points": points, "closed": True})
    if not output:
        raise ValueError("The selected text produced no usable vector contours")
    return output


def create_vector_text_object(
    layer_id: str,
    options: TextVectorOptions,
    *,
    center: tuple[float, float],
) -> SceneObject:
    path, bridge_width_mm, bridge_count = build_vector_text_path(options)
    item = SceneObject.path(
        layer_id,
        painter_path_polylines(path),
        name=(options.text.strip().splitlines()[0][:40] or "Text"),
        center=center,
        source_name="E3 vector text",
    )
    item.metadata.update(
        {
            "text_source": options.text,
            "text_font_family": options.font_family,
            "text_vector_mode": options.mode,
            "text_height_mm": options.height_mm,
            "text_bridge_width_mm": bridge_width_mm,
            "text_bridge_width_auto": options.bridge_width_mm is None,
            "text_bridge_count": bridge_count,
        }
    )
    return item
