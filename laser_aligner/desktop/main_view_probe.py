"""Explicit probe selection on the existing corrected workspace camera image."""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import time
from typing import Any

import numpy as np

from ..errors import LaserAlignerError
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()

STALE_SECONDS = 2.0
_BINDING_FIELDS = (
    "width", "height", "source_width", "source_height", "source_mode",
    "camera_settings", "review_signature", "source_generation", "lens_model_id",
    "pixels_per_mm", "camera_image_area", "corrected_width", "corrected_height",
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


class MainViewProbe(QtCore.QObject):
    """Adapt the main view to the focus selection API without another camera owner.

    Evidence accompanies each displayed corrected frame. Project/canvas pixels
    are mapped through that frame's rigid support pose, inverse bed map (including
    its residual mesh), then lens distortion, back into original raw pixels.
    AppContext's existing focus mapper revalidates those pixels before any move.
    """

    pointSelected = QtCore.Signal(dict)
    selectionInvalidated = QtCore.Signal(str)

    def __init__(self, controller: Any, workspace: Any, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.workspace = workspace
        self._context = controller.runtime.context
        self._active = False
        self._selection_enabled = False
        self._frame_metadata: dict[str, Any] | None = None
        self._frame_binding: str | None = None
        self._frame_geometry: tuple[Any, ...] | None = None
        self._last_frame_fresh = False
        self._selection: dict[str, Any] | None = None
        self._selection_rejected = False
        self._left_pressed = False
        self._marker = QtWidgets.QGraphicsPathItem()
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QPointF(), 6.0, 6.0)
        path.moveTo(-12.0, 0.0)
        path.lineTo(12.0, 0.0)
        path.moveTo(0.0, -12.0)
        path.lineTo(0.0, 12.0)
        self._marker.setPath(path)
        self._marker.setFlag(QtWidgets.QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self._marker.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
        self._marker.setZValue(1_000_000.0)
        self._marker.hide()
        workspace.workspace_scene.addItem(self._marker)
        workspace.cameraImageChanged.connect(self.update_frame)
        self._age_timer = QtCore.QTimer(self)
        self._age_timer.setInterval(250)
        self._age_timer.timeout.connect(self._check_selection)

    def begin(self) -> None:
        if self._active:
            return
        self._active = True
        self.workspace.viewport().installEventFilter(self)
        self._age_timer.start()

    def end(self) -> None:
        was_active = self._active
        self._active = False
        self._last_frame_fresh = False
        self.workspace.viewport().removeEventFilter(self)
        self._age_timer.stop()
        self.set_selection_enabled(False)
        self._invalidate_selection("Main camera view selection stopped", force_notify=was_active)

    def set_selection_enabled(self, enabled: bool) -> None:
        self._selection_enabled = bool(enabled)
        self._left_pressed = False
        if enabled:
            self.workspace.cancel_shape_draft()
            self.workspace.cancel_point_pick()
            self.workspace.viewport().setCursor(QtCore.Qt.CursorShape.CrossCursor)
        else:
            self.workspace.viewport().unsetCursor()

    def clear_selection(self) -> None:
        self._invalidate_selection("Selected point cleared", notify=False)

    def reject_selection(self) -> None:
        if self._selection is not None:
            self._selection_rejected = True
            self._paint_marker()

    def _invalidate_selection(self, reason: str, *, notify: bool = True, force_notify: bool = False) -> None:
        selected = self._selection is not None
        self._selection = None
        self._selection_rejected = False
        self._marker.hide()
        if (selected or force_notify) and notify:
            self.selectionInvalidated.emit(reason)

    def _geometry(self) -> tuple[Any, ...]:
        item = self.workspace._camera_item
        transform = item.sceneTransform()
        return (
            item.pixmap().width(), item.pixmap().height(), item.pixmap().devicePixelRatio(),
            item.offset().x(), item.offset().y(),
            *(getattr(transform, f"m{row}{column}")() for row in range(1, 4) for column in range(1, 4)),
        )

    @staticmethod
    def _binding(metadata: dict[str, Any]) -> str:
        return json.dumps(
            {key: metadata.get(key) for key in _BINDING_FIELDS},
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        )

    def update_frame(self, metadata: object) -> None:
        """Accept only evidence delivered with this exact displayed camera frame."""
        previous_binding, previous_geometry = self._frame_binding, self._frame_geometry
        self._frame_metadata = copy.deepcopy(metadata) if isinstance(metadata, dict) else None
        self._frame_geometry = self._geometry()
        try:
            self._frame_binding = None if self._frame_metadata is None else self._binding(self._frame_metadata)
        except (TypeError, ValueError):
            self._frame_metadata = None
            self._frame_binding = None
        frame_changed = previous_binding is not None and (
            previous_binding != self._frame_binding or previous_geometry != self._frame_geometry
        )
        if frame_changed:
            # Dispatch clears the marker before its queued worker runs. Losing
            # image evidence must still cancel that worker's camera generation.
            self._last_frame_fresh = False
            self._invalidate_selection(
                "Main camera image, source or calibration changed; select the point again",
                force_notify=self._active,
            )
        self._check_selection()

    def _frame_age(self) -> float | None:
        metadata = self._frame_metadata
        if metadata is None:
            return None
        received = _finite(metadata.get("received_monotonic"))
        age = _finite(metadata.get("frame_age_seconds"))
        now = time.monotonic()
        if received is None or age is None or not 0 <= received <= now or age < 0:
            return None
        return age + now - received

    def current_frame_fresh(self) -> bool:
        try:
            return self._frame_fresh()
        except (AttributeError, KeyError, IndexError, TypeError, ValueError, RuntimeError, LaserAlignerError):
            return False

    def _frame_fresh(self) -> bool:
        metadata = self._frame_metadata
        item = self.workspace._camera_item
        age = self._frame_age()
        if (not self._active or not self.workspace.isVisible() or metadata is None
                or age is None or not 0 <= age < STALE_SECONDS
                or not item.isVisible() or item.effectiveOpacity() <= 0.0 or item.pixmap().isNull()
                or self._geometry() != self._frame_geometry):
            return False
        if any(type(metadata.get(key)) is not int or not 0 < metadata[key] <= 30000
               for key in ("width", "height", "source_width", "source_height", "corrected_width", "corrected_height")):
            return False
        if ((metadata["width"], metadata["height"]) != (metadata["source_width"], metadata["source_height"])
                or metadata.get("source_mode") is not None
                or (metadata["corrected_width"], metadata["corrected_height"])
                != (item.pixmap().width(), item.pixmap().height())):
            return False
        if (type(metadata.get("source_generation")) is not int
                or metadata["source_generation"] != self.controller._camera_source_generation
                or self.controller._camera_review_active()
                or not self.controller.review_signature_is_current(metadata.get("review_signature"))):
            return False
        signature = metadata["review_signature"]
        if signature[0] not in ("machine", "honeycomb_local"):
            return False
        camera_settings = self._context.camera.settings
        if (not dataclasses.is_dataclass(camera_settings)
                or metadata.get("camera_settings") != dataclasses.asdict(camera_settings)):
            return False
        lens = self._context.lens.model
        if (lens is None or metadata.get("lens_model_id") != lens.model_id
                or (metadata["width"], metadata["height"]) != lens.image_size):
            return False
        ppm = _finite(metadata.get("pixels_per_mm"))
        area = self.workspace._camera_image_area
        return bool(ppm is not None and ppm > 0.0 and area is not None
                    and ppm == self.controller.runtime.settings.calibration.bed.pixels_per_mm
                    and metadata.get("camera_image_area") == {
                        key: float(getattr(area, key)) for key in ("x_min", "x_max", "y_min", "y_max")
                    })

    def _check_selection(self) -> None:
        fresh = self.current_frame_fresh()
        was_fresh, self._last_frame_fresh = self._last_frame_fresh, fresh
        if not fresh and (self._selection is not None or was_fresh):
            self._invalidate_selection(
                "Main camera image is stale or unavailable; refresh it and select a point again",
                force_notify=self._active and was_fresh,
            )

    def selection_snapshot(self) -> dict[str, Any] | None:
        if self._selection is None:
            return None
        snapshot = copy.deepcopy(self._selection)
        snapshot["frame_age_seconds"] = self._frame_age()
        snapshot["received_monotonic"] = (self._frame_metadata or {}).get("received_monotonic")
        snapshot["snapshot_monotonic"] = time.monotonic()
        snapshot["fresh"] = self.current_frame_fresh()
        snapshot["main_view"] = True
        return snapshot

    def _paint_marker(self) -> None:
        color = "#ef5350" if self._selection_rejected else "#ffcf40"
        self._marker.setPen(QtGui.QPen(QtGui.QColor(color), 2.0))
        self._marker.show()

    def _select_point(self, viewport_point: QtCore.QPointF) -> None:
        if not self._selection_enabled or not self.current_frame_fresh():
            return
        inverse, valid = self.workspace.viewportTransform().inverted()
        if not valid:
            return
        scene_point = inverse.map(viewport_point)
        item = self.workspace._camera_item
        pixel = item.mapFromScene(scene_point)
        if not item.boundingRect().contains(pixel):
            return
        metadata = self._frame_metadata
        assert metadata is not None
        area = metadata["camera_image_area"]
        ppm = metadata["pixels_per_mm"]
        x, y = area["x_min"] + pixel.x() / ppm, area["y_max"] - pixel.y() / ppm
        try:
            if metadata["review_signature"][0] == "honeycomb_local":
                frame = self._context.current_honeycomb_coordinate_frame()
                if frame is None or tuple(frame.provenance_signature) != tuple(metadata["review_signature"][1]):
                    raise ValueError("The honeycomb reference changed; select the point again")
                x, y = frame.local_to_machine(x, y)
            corrected = self._context.bed.mm_to_image(x, y)
            mapped_back = np.asarray(self._context.bed.image_to_mm(*corrected), dtype=np.float64)
            if (not np.isfinite(mapped_back).all()
                    or np.max(np.abs(mapped_back - np.asarray([x, y]))) > 0.01):
                raise ValueError("Camera mapping did not converge at this point; select another point")
            raw = np.asarray(self._context.lens.model.distort_points(
                np.asarray([corrected], dtype=np.float64)
            ), dtype=np.float64).reshape(2)
            if (not np.isfinite(raw).all() or not 0 <= raw[0] < metadata["width"]
                    or not 0 <= raw[1] < metadata["height"]):
                raise ValueError("Select a point inside the calibrated camera image")
            if not self.current_frame_fresh():
                raise ValueError("The main camera image changed; select the point again")
            selection = copy.deepcopy(metadata)
            selection.update(image_x=float(raw[0]), image_y=float(raw[1]),
                             selected_monotonic=time.monotonic())
            self._selection = selection
            self._selection_rejected = False
            self._marker.setPos(scene_point)
            self._paint_marker()
            snapshot = self.selection_snapshot()
            assert snapshot is not None
            self.pointSelected.emit(snapshot)
        except (AttributeError, KeyError, IndexError, TypeError, ValueError, RuntimeError, LaserAlignerError) as exc:
            self._invalidate_selection(str(exc), notify=False)
            self.selectionInvalidated.emit(str(exc))

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:  # noqa: N802
        if (getattr(self, "_active", False) and self._selection_enabled
                and watched is self.workspace.viewport()):
            kind = event.type()
            if kind in (QtCore.QEvent.Type.MouseButtonPress, QtCore.QEvent.Type.MouseButtonDblClick):
                if event.button() == QtCore.Qt.MouseButton.LeftButton and not self.workspace._space_pan:
                    self._left_pressed = True
                    self._select_point(event.position())
                    event.accept()
                    return True
            elif kind == QtCore.QEvent.Type.MouseMove and self._left_pressed:
                event.accept()
                return True
            elif (kind == QtCore.QEvent.Type.MouseButtonRelease and self._left_pressed
                  and event.button() == QtCore.Qt.MouseButton.LeftButton):
                self._left_pressed = False
                event.accept()
                return True
        return super().eventFilter(watched, event)


__all__ = ["MainViewProbe"]
