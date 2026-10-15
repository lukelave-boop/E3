"""Qt adapter for the shared, source-verified engraving image pipeline."""
from __future__ import annotations

import numpy as np

from ..project.image_edit import process_image
from ..project.raster_asset import RasterAssetPayload, decode_raster_payload_planes
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def verify_edit_source(payload: RasterAssetPayload, settings: dict) -> None:
    if (settings["source_sha256"] != payload.identity.sha256
            or settings["source_size"] != [payload.identity.width, payload.identity.height]):
        raise ValueError("The original image changed; reopen the original source before using its edits")


def grayscale_image(gray: np.ndarray) -> QtGui.QImage:
    pixels = np.ascontiguousarray(gray, dtype=np.uint8)
    return QtGui.QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0],
                        QtGui.QImage.Format.Format_Grayscale8).copy()


def edited_preview_image(payload: RasterAssetPayload, geometry: dict) -> QtGui.QImage:
    verify_edit_source(payload, geometry["image_edit"])
    original, source_allowed = decode_raster_payload_planes(payload)
    gray, _allowed = process_image(original, geometry, source_allowed=source_allowed)
    return grayscale_image(gray)
