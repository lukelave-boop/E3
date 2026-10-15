"""Offline review of an image's non-destructive background cleanup."""
from __future__ import annotations

import numpy as np

from ..project.raster_asset import RasterAssetPayload, decode_raster_payload_grayscale
from ..project.raster_cleanup import background_mask, cleanup_settings
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def source_image(payload: RasterAssetPayload) -> QtGui.QImage:
    buffer = QtCore.QBuffer()
    buffer.setData(QtCore.QByteArray(payload.encoded))
    buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly)
    reader = QtGui.QImageReader(buffer)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull() or (image.width(), image.height()) != (
        payload.identity.width, payload.identity.height
    ):
        raise ValueError("Could not decode the exact background-cleanup source")
    # Flatten onto white consistently with the engraving decoder.
    output = QtGui.QImage(image.size(), QtGui.QImage.Format.Format_RGB32)
    output.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(output)
    painter.drawImage(0, 0, image)
    painter.end()
    return output


def cleaned_image(original: QtGui.QImage, mask: np.ndarray) -> QtGui.QImage:
    image = original.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(image.bits(), dtype=np.uint8).reshape(
        image.height(), image.bytesPerLine()
    )[:, :image.width() * 4].reshape(image.height(), image.width(), 4)
    pixels[mask] = 255
    return image


def cleanup_preview_image(payload: RasterAssetPayload, tolerance: int) -> QtGui.QImage:
    gray = decode_raster_payload_grayscale(payload)
    return cleaned_image(source_image(payload), background_mask(gray, tolerance))


class _ImageView(QtWidgets.QGraphicsView):
    zoomRequested = QtCore.Signal(float)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        self.zoomRequested.emit(1.25 if event.angleDelta().y() > 0 else 0.8)
        event.accept()


class BackgroundCleanupDialog(QtWidgets.QDialog):
    def __init__(self, payload: RasterAssetPayload, geometry: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clean image background")
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.resize(1020, 700)
        self.gray = decode_raster_payload_grayscale(payload)
        self.original_image = source_image(payload)
        self.after_image = self.original_image
        enabled, tolerance = cleanup_settings(geometry)
        layout = QtWidgets.QVBoxLayout(self)
        description = QtWidgets.QLabel(
            "Whiten light pixels connected to an outside edge. Enclosed highlights stay intact. "
            "Higher tolerance includes darker pixels. The original file is never changed."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        controls = QtWidgets.QHBoxLayout()
        self.enabled = QtWidgets.QCheckBox("Clean background")
        self.enabled.setChecked(enabled)
        controls.addWidget(self.enabled)
        controls.addWidget(QtWidgets.QLabel("Tolerance:"))
        self.tolerance = QtWidgets.QSpinBox()
        self.tolerance.setRange(0, 64)
        self.tolerance.setValue(tolerance)
        self.tolerance.setToolTip("Distance from pure white: 5 includes grayscale values 250–255")
        controls.addWidget(self.tolerance)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 64)
        self.slider.setValue(tolerance)
        controls.addWidget(self.slider)
        self.slider.valueChanged.connect(self.tolerance.setValue)
        self.tolerance.valueChanged.connect(self.slider.setValue)
        layout.addLayout(controls)
        previews = QtWidgets.QHBoxLayout()
        self.views = []
        self.pixmaps = []
        for title in ("Before — original", "After — cleanup"):
            column = QtWidgets.QVBoxLayout()
            column.addWidget(QtWidgets.QLabel(title))
            view = _ImageView()
            view.setMinimumSize(240, 220)
            view.setBackgroundBrush(QtGui.QColor("white"))
            view.setDragMode(QtWidgets.QGraphicsView.DragMode.ScrollHandDrag)
            scene = QtWidgets.QGraphicsScene(view)
            view.setScene(scene)
            item = scene.addPixmap(QtGui.QPixmap.fromImage(self.original_image))
            scene.setSceneRect(item.boundingRect())
            column.addWidget(view)
            previews.addLayout(column)
            self.views.append(view)
            self.pixmaps.append(item)
            view.zoomRequested.connect(self._zoom)
        for a, b in ((self.views[0], self.views[1]), (self.views[1], self.views[0])):
            a.horizontalScrollBar().valueChanged.connect(b.horizontalScrollBar().setValue)
            a.verticalScrollBar().valueChanged.connect(b.verticalScrollBar().setValue)
        layout.addLayout(previews, 1)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        actions = QtWidgets.QHBoxLayout()
        for text, callback in (("Fit", self._fit), ("100%", self._actual_size)):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Apply
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        actions.addWidget(self.buttons)
        layout.addLayout(actions)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self._update_preview)
        self.enabled.toggled.connect(self._schedule_preview)
        self.tolerance.valueChanged.connect(self._schedule_preview)
        self._update_preview()
        QtCore.QTimer.singleShot(0, self._fit)

    @property
    def settings(self) -> dict:
        return {"enabled": self.enabled.isChecked(), "tolerance": self.tolerance.value()}

    def _schedule_preview(self, *_args) -> None:
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).setEnabled(False)
        self.summary.setText("Updating preview…")
        self.timer.start()

    def _update_preview(self) -> None:
        self.timer.stop()
        enabled, tolerance = self.enabled.isChecked(), self.tolerance.value()
        self.tolerance.setEnabled(enabled)
        self.slider.setEnabled(enabled)
        changed = 0
        self.after_image = self.original_image
        if enabled:
            mask = background_mask(self.gray, tolerance)
            changed = int(np.count_nonzero(mask & (self.gray < 255)))
            self.after_image = cleaned_image(self.original_image, mask)
        self.pixmaps[1].setPixmap(QtGui.QPixmap.fromImage(self.after_image))
        self.summary.setText(
            f"{changed:,} pixels whitened ({changed / self.gray.size:.2%}). "
            f"Eligible brightness: {255 - tolerance}–255. Scroll to zoom; drag to pan both views."
            if enabled else "Cleanup is off. The original image will be used."
        )
        self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).setEnabled(True)

    def accept(self) -> None:
        if self.timer.isActive():
            return
        super().accept()

    def _zoom(self, factor: float) -> None:
        for view in self.views:
            if 0.02 <= view.transform().m11() * factor <= 32:
                view.scale(factor, factor)

    def _fit(self) -> None:
        for view in self.views:
            view.fitInView(view.sceneRect(), QtCore.Qt.AspectRatioMode.KeepAspectRatio)

    def _actual_size(self) -> None:
        for view in self.views:
            view.resetTransform()
