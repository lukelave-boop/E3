from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtSvg

_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(svg_path: Path, size: int) -> bytes:
    renderer = QtSvg.QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise ValueError(f"Could not load SVG icon: {svg_path}")
    image = QtGui.QImage(
        size,
        size,
        QtGui.QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    renderer.render(painter, QtCore.QRectF(0, 0, size, size))
    painter.end()

    payload = QtCore.QByteArray()
    buffer = QtCore.QBuffer(payload)
    if not buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
        raise OSError("Could not allocate the icon PNG buffer")
    if not image.save(buffer, "PNG"):
        raise OSError(f"Could not encode the {size} px icon")
    return bytes(payload)


def build_ico(svg_path: Path, output: Path) -> None:
    images = [(size, render_png(svg_path, size)) for size in _SIZES]
    header_size = 6 + (16 * len(images))
    offset = header_size
    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    payload = bytearray()
    for size, image in images:
        dimension = 0 if size == 256 else size
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        payload.extend(image)
        offset += len(image)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bytes(directory + payload))


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the E3 DEV TEST Windows icon")
    parser.add_argument("--svg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    application = QtGui.QGuiApplication.instance() or QtGui.QGuiApplication(
        [sys.argv[0]]
    )
    build_ico(arguments.svg.resolve(), arguments.output.resolve())
    application.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
