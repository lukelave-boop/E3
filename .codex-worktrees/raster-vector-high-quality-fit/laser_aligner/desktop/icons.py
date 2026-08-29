from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .qt import require_qt

QtCore, QtGui, _QtWidgets = require_qt()


_VIEWBOX = 24.0
_DEFAULT_FOREGROUND = "#E4E7E9"
_DEFAULT_ACCENT = "#55C7B2"


def _path(*commands: tuple[Any, ...]) -> QtGui.QPainterPath:
    path = QtGui.QPainterPath()
    for command in commands:
        operation, *values = command
        if operation == "M":
            path.moveTo(float(values[0]), float(values[1]))
        elif operation == "L":
            path.lineTo(float(values[0]), float(values[1]))
        elif operation == "C":
            path.cubicTo(*(float(value) for value in values))
        elif operation == "Q":
            path.quadTo(*(float(value) for value in values))
        elif operation == "Z":
            path.closeSubpath()
        else:  # pragma: no cover - only module-owned drawing commands use this
            raise ValueError(f"Unsupported icon path operation: {operation}")
    return path


class _GlyphPainter:
    def __init__(
        self,
        painter: QtGui.QPainter,
        foreground: QtGui.QColor,
        accent: QtGui.QColor,
    ) -> None:
        self.painter = painter
        self.foreground = foreground
        self.accent = accent
        self.pen(foreground)
        self.painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

    def pen(
        self,
        color: QtGui.QColor | None = None,
        width: float = 1.75,
        style: QtCore.Qt.PenStyle = QtCore.Qt.PenStyle.SolidLine,
    ) -> None:
        pen = QtGui.QPen(color or self.foreground, width)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        pen.setStyle(style)
        self.painter.setPen(pen)

    def no_pen(self) -> None:
        self.painter.setPen(QtCore.Qt.PenStyle.NoPen)

    def brush(self, color: QtGui.QColor | None = None) -> None:
        self.painter.setBrush(QtGui.QBrush(color or self.foreground))

    def no_brush(self) -> None:
        self.painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)


def _page(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 6, 3),
            ("L", 14.5, 3),
            ("L", 19, 7.5),
            ("L", 19, 21),
            ("L", 6, 21),
            ("Z",),
            ("M", 14.5, 3),
            ("L", 14.5, 7.5),
            ("L", 19, 7.5),
        )
    )


def _draw_new(glyph: _GlyphPainter) -> None:
    _page(glyph)
    glyph.pen(glyph.accent, 2.0)
    glyph.painter.drawLine(QtCore.QPointF(8, 14.5), QtCore.QPointF(14, 14.5))
    glyph.painter.drawLine(QtCore.QPointF(11, 11.5), QtCore.QPointF(11, 17.5))


def _draw_open(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 3, 7),
            ("L", 10, 7),
            ("L", 12, 9),
            ("L", 21, 9),
            ("L", 18.5, 19),
            ("L", 4.5, 19),
            ("Z",),
            ("M", 3, 7),
            ("L", 3, 17),
        )
    )


def _draw_save(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 4, 3.5),
            ("L", 17.5, 3.5),
            ("L", 20.5, 6.5),
            ("L", 20.5, 20.5),
            ("L", 3.5, 20.5),
            ("L", 3.5, 3.5),
            ("Z",),
            ("M", 7, 3.5),
            ("L", 7, 9),
            ("L", 16.5, 9),
            ("L", 16.5, 3.5),
            ("M", 7, 20.5),
            ("L", 7, 13),
            ("L", 17, 13),
            ("L", 17, 20.5),
        )
    )


def _draw_import(glyph: _GlyphPainter) -> None:
    _page(glyph)
    glyph.pen(glyph.accent, 2.0)
    glyph.painter.drawPath(
        _path(
            ("M", 3, 15),
            ("L", 12, 15),
            ("M", 8.5, 11.5),
            ("L", 12, 15),
            ("L", 8.5, 18.5),
        )
    )


def _draw_undo(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 9, 7),
            ("L", 4.5, 10.5),
            ("L", 9, 14),
            ("M", 5, 10.5),
            ("L", 13.5, 10.5),
            ("C", 18.5, 10.5, 20, 14, 19, 18),
        )
    )


def _draw_redo(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 15, 7),
            ("L", 19.5, 10.5),
            ("L", 15, 14),
            ("M", 19, 10.5),
            ("L", 10.5, 10.5),
            ("C", 5.5, 10.5, 4, 14, 5, 18),
        )
    )


def _draw_delete(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 5, 7),
            ("L", 19, 7),
            ("M", 9, 7),
            ("L", 9.5, 4),
            ("L", 14.5, 4),
            ("L", 15, 7),
            ("M", 7, 8),
            ("L", 8, 20),
            ("L", 16, 20),
            ("L", 17, 8),
            ("M", 10.5, 10),
            ("L", 10.8, 17),
            ("M", 13.5, 10),
            ("L", 13.2, 17),
        )
    )


def _draw_duplicate(glyph: _GlyphPainter) -> None:
    glyph.painter.drawRect(QtCore.QRectF(7, 7, 13, 13))
    glyph.painter.drawPath(
        _path(
            ("M", 4, 16),
            ("L", 4, 4),
            ("L", 16, 4),
        )
    )


def _draw_select(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 5, 3),
            ("L", 18, 12),
            ("L", 12.5, 13),
            ("L", 16, 20),
            ("L", 12.5, 21.5),
            ("L", 9, 14.5),
            ("L", 5, 18),
            ("Z",),
        )
    )


def _draw_rectangle(glyph: _GlyphPainter) -> None:
    glyph.painter.drawRect(QtCore.QRectF(3.5, 5, 17, 14))


def _draw_ellipse(glyph: _GlyphPainter) -> None:
    glyph.painter.drawEllipse(QtCore.QRectF(3, 5, 18, 14))


def _draw_line(glyph: _GlyphPainter) -> None:
    glyph.painter.drawLine(QtCore.QPointF(4, 19), QtCore.QPointF(20, 5))
    glyph.brush(glyph.accent)
    glyph.no_pen()
    glyph.painter.drawEllipse(QtCore.QPointF(4, 19), 2, 2)
    glyph.painter.drawEllipse(QtCore.QPointF(20, 5), 2, 2)


def _draw_text(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 5, 5),
            ("L", 19, 5),
            ("M", 12, 5),
            ("L", 12, 20),
            ("M", 8, 20),
            ("L", 16, 20),
        )
    )


def _draw_grid(glyph: _GlyphPainter) -> None:
    for row in range(2):
        for column in range(3):
            glyph.painter.drawRoundedRect(
                QtCore.QRectF(3.5 + column * 6.2, 5 + row * 7.2, 4.5, 5.2),
                0.8,
                0.8,
            )


def _draw_trace(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 4, 13),
            ("C", 5, 5, 14, 3.5, 17, 9),
            ("C", 20, 14, 14, 18, 8, 16),
        )
    )
    glyph.pen(glyph.accent, 2.0)
    glyph.painter.drawEllipse(QtCore.QRectF(13, 13, 6, 6))
    glyph.painter.drawLine(QtCore.QPointF(18, 18), QtCore.QPointF(21, 21))


def _draw_template(glyph: _GlyphPainter) -> None:
    _draw_grid(glyph)
    glyph.pen(glyph.accent, 1.7)
    glyph.painter.drawEllipse(QtCore.QRectF(13, 12.5, 8, 8))
    glyph.painter.drawLine(QtCore.QPointF(17, 11), QtCore.QPointF(17, 22))
    glyph.painter.drawLine(QtCore.QPointF(11.5, 16.5), QtCore.QPointF(22.5, 16.5))


def _draw_fit(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 9, 4),
            ("L", 4, 4),
            ("L", 4, 9),
            ("M", 15, 4),
            ("L", 20, 4),
            ("L", 20, 9),
            ("M", 4, 15),
            ("L", 4, 20),
            ("L", 9, 20),
            ("M", 20, 15),
            ("L", 20, 20),
            ("L", 15, 20),
        )
    )


def _draw_fit_selection(glyph: _GlyphPainter) -> None:
    glyph.pen(style=QtCore.Qt.PenStyle.DashLine)
    glyph.painter.drawRect(QtCore.QRectF(5, 5, 14, 14))
    glyph.pen(glyph.accent)
    for point in ((5, 5), (19, 5), (5, 19), (19, 19)):
        glyph.brush(glyph.accent)
        glyph.painter.drawRect(QtCore.QRectF(point[0] - 1, point[1] - 1, 2, 2))
        glyph.no_brush()


def _draw_zoom(glyph: _GlyphPainter, plus: bool) -> None:
    glyph.painter.drawEllipse(QtCore.QRectF(4, 4, 12, 12))
    glyph.painter.drawLine(QtCore.QPointF(14.5, 14.5), QtCore.QPointF(20.5, 20.5))
    glyph.painter.drawLine(QtCore.QPointF(7, 10), QtCore.QPointF(13, 10))
    if plus:
        glyph.painter.drawLine(QtCore.QPointF(10, 7), QtCore.QPointF(10, 13))


def _draw_snap(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 5, 4),
            ("L", 5, 13),
            ("C", 5, 22, 19, 22, 19, 13),
            ("L", 19, 4),
            ("L", 14, 4),
            ("L", 14, 12),
            ("C", 14, 16, 10, 16, 10, 12),
            ("L", 10, 4),
            ("Z",),
        )
    )
    glyph.pen(glyph.accent)
    glyph.painter.drawLine(QtCore.QPointF(5, 8), QtCore.QPointF(10, 8))
    glyph.painter.drawLine(QtCore.QPointF(14, 8), QtCore.QPointF(19, 8))


def _draw_group(glyph: _GlyphPainter, broken: bool = False) -> None:
    glyph.painter.drawRoundedRect(QtCore.QRectF(3, 4, 10, 10), 1.5, 1.5)
    glyph.painter.drawRoundedRect(QtCore.QRectF(11, 10, 10, 10), 1.5, 1.5)
    glyph.pen(glyph.accent, 2.0)
    if broken:
        glyph.painter.drawLine(QtCore.QPointF(8, 17), QtCore.QPointF(11, 14))
        glyph.painter.drawLine(QtCore.QPointF(13, 12), QtCore.QPointF(16, 9))
    else:
        glyph.painter.drawLine(QtCore.QPointF(8, 17), QtCore.QPointF(16, 9))


def _draw_align(glyph: _GlyphPainter) -> None:
    glyph.pen(glyph.accent)
    glyph.painter.drawLine(QtCore.QPointF(4, 3), QtCore.QPointF(4, 21))
    glyph.pen()
    glyph.painter.drawRect(QtCore.QRectF(7, 5, 12, 5))
    glyph.painter.drawRect(QtCore.QRectF(7, 14, 8, 5))


def _draw_distribute(glyph: _GlyphPainter) -> None:
    glyph.pen(glyph.accent)
    glyph.painter.drawLine(QtCore.QPointF(3, 4), QtCore.QPointF(21, 4))
    glyph.painter.drawLine(QtCore.QPointF(3, 20), QtCore.QPointF(21, 20))
    glyph.pen()
    glyph.painter.drawRect(QtCore.QRectF(5, 7, 4, 4))
    glyph.painter.drawRect(QtCore.QRectF(15, 13, 4, 4))


def _draw_order(glyph: _GlyphPainter) -> None:
    glyph.painter.drawRect(QtCore.QRectF(4, 8, 10, 10))
    glyph.painter.drawRect(QtCore.QRectF(10, 4, 10, 10))
    glyph.pen(glyph.accent)
    glyph.painter.drawPath(
        _path(
            ("M", 16, 17),
            ("L", 20, 17),
            ("L", 20, 13),
            ("M", 20, 17),
            ("L", 15.5, 12.5),
        )
    )


def _draw_generate(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 4, 17),
            ("C", 7, 5, 14, 19, 20, 6),
        )
    )
    glyph.brush(glyph.accent)
    glyph.no_pen()
    for point in ((4, 17), (11, 12), (20, 6)):
        glyph.painter.drawEllipse(QtCore.QPointF(*point), 1.8, 1.8)


def _draw_preview(glyph: _GlyphPainter) -> None:
    """Draw a monitor containing a small toolpath and playback head."""

    glyph.painter.drawRoundedRect(QtCore.QRectF(2.5, 3.5, 19, 14), 1.8, 1.8)
    glyph.painter.drawLine(QtCore.QPointF(9, 21), QtCore.QPointF(15, 21))
    glyph.painter.drawLine(QtCore.QPointF(12, 17.5), QtCore.QPointF(12, 21))
    glyph.pen(glyph.accent, 1.6)
    glyph.painter.drawPath(
        _path(
            ("M", 5.5, 14),
            ("L", 8.5, 8),
            ("L", 12, 12),
            ("L", 18.5, 6.5),
        )
    )
    glyph.brush(glyph.accent)
    glyph.no_pen()
    glyph.painter.drawEllipse(QtCore.QPointF(18.5, 6.5), 1.5, 1.5)


def _draw_frame(glyph: _GlyphPainter) -> None:
    glyph.pen(style=QtCore.Qt.PenStyle.DashLine)
    glyph.painter.drawRect(QtCore.QRectF(4, 5, 16, 14))
    glyph.pen(glyph.accent)
    glyph.painter.drawPath(
        _path(
            ("M", 9.5, 9),
            ("L", 15.5, 12),
            ("L", 9.5, 15),
            ("Z",),
        )
    )


def _draw_run(glyph: _GlyphPainter) -> None:
    glyph.pen(glyph.accent, 1.9)
    glyph.brush(glyph.accent)
    glyph.painter.drawPath(
        _path(
            ("M", 7, 4),
            ("L", 20, 12),
            ("L", 7, 20),
            ("Z",),
        )
    )


def _draw_stop(glyph: _GlyphPainter) -> None:
    danger = QtGui.QColor("#FF6673")
    glyph.pen(danger, 2.0)
    glyph.painter.drawRoundedRect(QtCore.QRectF(4, 4, 16, 16), 3, 3)
    glyph.painter.drawLine(QtCore.QPointF(8, 8), QtCore.QPointF(16, 16))
    glyph.painter.drawLine(QtCore.QPointF(16, 8), QtCore.QPointF(8, 16))


def _draw_camera(glyph: _GlyphPainter) -> None:
    glyph.painter.drawRoundedRect(QtCore.QRectF(3, 7, 18, 12), 2, 2)
    glyph.painter.drawPath(_path(("M", 7, 7), ("L", 9, 4.5), ("L", 15, 4.5), ("L", 17, 7)))
    glyph.painter.drawEllipse(QtCore.QRectF(8, 9, 8, 8))


def _draw_refresh(glyph: _GlyphPainter) -> None:
    glyph.painter.drawPath(
        _path(
            ("M", 19, 9),
            ("L", 19, 4),
            ("L", 14, 4),
            ("M", 18.5, 5),
            ("C", 12, 0.5, 4, 5, 4, 12),
            ("M", 5, 15),
            ("L", 5, 20),
            ("L", 10, 20),
            ("M", 5.5, 19),
            ("C", 12, 23.5, 20, 19, 20, 12),
        )
    )


_DRAWERS: dict[str, Callable[[_GlyphPainter], None]] = {
    "new": _draw_new,
    "open": _draw_open,
    "save": _draw_save,
    "save_as": _draw_save,
    "save_template": _draw_save,
    "import_svg": _draw_import,
    "import_lightburn": _draw_import,
    "import_image": _draw_import,
    "undo": _draw_undo,
    "redo": _draw_redo,
    "delete": _draw_delete,
    "duplicate": _draw_duplicate,
    "select_tool": _draw_select,
    "rectangle": _draw_rectangle,
    "ellipse": _draw_ellipse,
    "line": _draw_line,
    "text": _draw_text,
    "grid_template_designer": _draw_grid,
    "trace_objects": _draw_trace,
    "template_alignment": _draw_template,
    "fit": _draw_fit,
    "fit_selection": _draw_fit_selection,
    "zoom_in": lambda glyph: _draw_zoom(glyph, True),
    "zoom_out": lambda glyph: _draw_zoom(glyph, False),
    "snap": _draw_snap,
    "group": _draw_group,
    "ungroup": lambda glyph: _draw_group(glyph, True),
    "align": _draw_align,
    "distribute": _draw_distribute,
    "order": _draw_order,
    "generate": _draw_generate,
    "preview": _draw_preview,
    "frame": _draw_frame,
    "run": _draw_run,
    "stop": _draw_stop,
    "camera": _draw_camera,
    "refresh_camera": _draw_refresh,
}


ACTION_ICON_NAMES: Mapping[str, str] = {
    **{name: name for name in _DRAWERS},
    "align_left": "align",
    "align_center_x": "align",
    "align_right": "align",
    "align_bottom": "align",
    "align_center_y": "align",
    "align_top": "align",
    "distribute_h": "distribute",
    "distribute_v": "distribute",
    "bring_front": "order",
    "raise": "order",
    "lower": "order",
    "send_back": "order",
    "preview_job": "preview",
}


def available_icon_names() -> tuple[str, ...]:
    """Return the stable public glyph names understood by :func:`make_icon`."""

    return tuple(sorted(_DRAWERS))


def make_icon(
    name: str,
    *,
    size: int = 20,
    foreground: str | QtGui.QColor = _DEFAULT_FOREGROUND,
    accent: str | QtGui.QColor = _DEFAULT_ACCENT,
) -> QtGui.QIcon:
    """Create an original, theme-friendly drafting glyph.

    The returned icon contains no project branding or third-party artwork. A
    fresh image is rendered for every call so callers may choose a foreground
    color for dark, light, enabled, or disabled surfaces.
    """

    normalized = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        drawer = _DRAWERS[normalized]
    except KeyError as exc:
        raise KeyError(f"Unknown desktop icon: {name!r}") from exc
    if size < 8:
        raise ValueError("Desktop icons must be at least 8 pixels")

    foreground_color = QtGui.QColor(foreground)
    accent_color = QtGui.QColor(accent)
    if not foreground_color.isValid() or not accent_color.isValid():
        raise ValueError("Icon colors must be valid Qt colors")

    image = QtGui.QImage(
        int(size),
        int(size),
        QtGui.QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    painter.scale(float(size) / _VIEWBOX, float(size) / _VIEWBOX)
    drawer(_GlyphPainter(painter, foreground_color, accent_color))
    painter.end()
    return QtGui.QIcon(QtGui.QPixmap.fromImage(image))


def action_icon(action_key: str, *, size: int = 20) -> QtGui.QIcon:
    """Return the glyph assigned to a main-window action key."""

    try:
        icon_name = ACTION_ICON_NAMES[action_key]
    except KeyError as exc:
        raise KeyError(f"No icon mapping for desktop action: {action_key!r}") from exc
    return make_icon(icon_name, size=size)


def apply_action_icons(
    actions: Mapping[str, QtGui.QAction],
    *,
    size: int = 20,
) -> tuple[str, ...]:
    """Apply known glyphs to an action mapping and return the updated keys.

    Unknown actions are intentionally left alone, allowing menus to retain
    text-only entries without treating every command as an icon requirement.
    """

    updated: list[str] = []
    for action_key, icon_name in ACTION_ICON_NAMES.items():
        action = actions.get(action_key)
        if action is None:
            continue
        action.setIcon(make_icon(icon_name, size=size))
        updated.append(action_key)
    return tuple(updated)


__all__ = [
    "ACTION_ICON_NAMES",
    "action_icon",
    "apply_action_icons",
    "available_icon_names",
    "make_icon",
]
