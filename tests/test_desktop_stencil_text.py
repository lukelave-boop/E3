from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from laser_aligner.desktop.qt import QtCore, QtGui, QtWidgets
from laser_aligner.desktop.text_geometry import (
    TextVectorOptions,
    _plain_text_path,
    _stencil_path,
    automatic_bridge_width,
    build_vector_text_path,
    create_vector_text_object,
    painter_path_polylines,
)
from laser_aligner.project import ObjectKind


@pytest.fixture(scope="module", autouse=True)
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _usable_test_font() -> str:
    """Return a real outline font even under Qt's Windows offscreen backend."""
    resolved = QtGui.QFontInfo(QtGui.QFont("Arial")).family().strip()
    if resolved:
        return resolved

    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    font_dir = windows / "Fonts"

    for filename in ("arial.ttf", "segoeui.ttf", "calibri.ttf"):
        path = font_dir / filename
        if not path.is_file():
            continue

        font_id = QtGui.QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue

        families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]

    pytest.skip("No real outline font is available to Qt for stencil geometry testing")


def test_auto_bridge_width_scales_with_text_height() -> None:
    assert automatic_bridge_width(5.0) == pytest.approx(0.8)
    assert automatic_bridge_width(25.0) == pytest.approx(2.0)
    assert automatic_bridge_width(100.0) == pytest.approx(4.0)


def test_stencil_text_bridges_enclosed_letter_counters() -> None:
    options = TextVectorOptions(
        text="OAR8",
        font_family=_usable_test_font(),
        height_mm=25.0,
        mode="stencil",
    )

    path, bridge_width, bridge_count = build_vector_text_path(options)
    polylines = painter_path_polylines(path)

    assert bridge_width == pytest.approx(2.0)
    assert bridge_count >= 4
    assert polylines


def test_created_stencil_text_is_normal_output_path_geometry() -> None:
    item = create_vector_text_object(
        "layer-1",
        TextVectorOptions(
            text="OPEN",
            font_family=_usable_test_font(),
            height_mm=20.0,
            mode="stencil",
        ),
        center=(50.0, 40.0),
    )

    assert item.kind is ObjectKind.PATH
    assert item.is_output_geometry
    assert item.metadata["text_vector_mode"] == "stencil"
    assert item.metadata["text_bridge_width_auto"] is True
    assert item.metadata["text_bridge_count"] >= 2


@pytest.mark.parametrize("letter", ["O", "D", "B", "8"])
def test_real_font_counters_have_aligned_top_and_bottom_bridges(letter: str) -> None:
    original = _plain_text_path(letter, _usable_test_font())
    bounds = original.boundingRect()
    width = bounds.height() * 0.04
    output, count = _stencil_path(original, width)
    assert count == (2 if letter in "B8" else 1)
    # A vertical material corridor must run through both ends of the glyph.
    # Find it within the central half, away from whitespace outside the letter.
    corridors = []
    for index in range(25, 76):
        x = bounds.left() + bounds.width() * index / 100
        band = QtGui.QPainterPath()
        band.addRect(QtCore.QRectF(x - width / 4, bounds.top(), width / 2, bounds.height()))
        if not original.intersected(band).isEmpty() and output.intersected(band).isEmpty():
            corridors.append(x)
    assert corridors
    assert not output.isEmpty()


def _rectangular_counters(*holes: tuple[float, float, float, float]) -> QtGui.QPainterPath:
    path = QtGui.QPainterPath()
    path.setFillRule(QtCore.Qt.FillRule.OddEvenFill)
    path.addRect(QtCore.QRectF(0, 0, 20, 30))
    for hole in holes:
        path.addRect(QtCore.QRectF(*hole))
    return path


def test_stacked_counters_share_one_full_width_bridge_without_cancellation() -> None:
    original = _rectangular_counters((4, 3, 10, 9), (5, 17, 11, 9))
    output, count = _stencil_path(original, 2)
    assert count == 2
    # Shared counter span is 5..14, so the material bridge is exactly 8.5..10.5.
    for y in (1, 14, 28):
        assert not output.contains(QtCore.QPointF(8.6, y))
        assert not output.contains(QtCore.QPointF(10.4, y))
        assert output.contains(QtCore.QPointF(8.4, y))
        assert output.contains(QtCore.QPointF(10.6, y))
    assert output.boundingRect() == original.boundingRect()


def test_side_by_side_counters_each_connect_to_outside() -> None:
    original = _rectangular_counters((2, 5, 5, 20), (13, 5, 5, 20))
    output, count = _stencil_path(original, 2)
    assert count == 2
    for x in (4.5, 15.5):
        for y in (1, 15, 29):
            assert not output.contains(QtCore.QPointF(x, y))
    assert output.contains(QtCore.QPointF(10, 15))


def test_stencil_leaves_neighbour_without_counter_unchanged() -> None:
    original = _rectangular_counters((4, 4, 12, 22))
    # Above the bridged letter, on the same X axis (e.g. another text line).
    neighbour = QtGui.QPainterPath()
    neighbour.addRect(QtCore.QRectF(6, -12, 8, 8))
    original.addPath(neighbour)
    output, count = _stencil_path(original, 2)
    assert count == 1
    assert output.intersected(neighbour) == neighbour


def test_oversized_bridge_rejects_erased_letter_even_when_neighbour_survives() -> None:
    original = _rectangular_counters((4, 4, 12, 22))
    original.addRect(QtCore.QRectF(25, 0, 4, 30))
    with pytest.raises(ValueError, match="smaller width"):
        _stencil_path(original, 50)


@pytest.mark.parametrize("text", ["E", "III", " "])
def test_path_without_counters_is_unchanged(text: str) -> None:
    original = QtGui.QPainterPath() if text == " " else _plain_text_path(text, _usable_test_font())
    output, count = _stencil_path(original, 2)
    assert count == 0
    assert output == original


@pytest.mark.parametrize("width", [0, -1, float("nan"), float("inf")])
def test_invalid_bridge_width_is_rejected(width: float) -> None:
    with pytest.raises(ValueError, match="Bridge width must be positive"):
        TextVectorOptions("O", "Arial", 25, mode="stencil", bridge_width_mm=width)


def test_dialog_previews_aligned_bridges_and_blocks_erased_letter() -> None:
    from laser_aligner.desktop.text_dialog import VectorTextDialog

    dialog = VectorTextDialog()
    dialog.font_combo.setCurrentFont(QtGui.QFont(_usable_test_font()))
    dialog.text_edit.setPlainText("OD")
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("stencil"))
    ok = dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled()
    assert "aligned top and bottom" in dialog.preview_status.text()
    dialog.auto_bridge.setChecked(False)
    dialog.bridge_spin.setValue(50)
    assert not ok.isEnabled()
    assert "smaller width" in dialog.preview_status.text()
    dialog.bridge_spin.setValue(1)
    assert ok.isEnabled()
    dialog.close()
    dialog.deleteLater()


@pytest.mark.parametrize("letter", list("ABDO PQRabdegopq04689".replace(" ", "")))
def test_stencil_has_no_enclosed_material_islands(letter: str) -> None:
    import cv2
    import numpy as np

    path, _, count = build_vector_text_path(
        TextVectorOptions(letter, _usable_test_font(), 25, mode="stencil")
    )
    assert count >= 1
    image = QtGui.QImage(384, 384, QtGui.QImage.Format.Format_Grayscale8)
    image.fill(255)
    painter = QtGui.QPainter(image)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtGui.QColor("black"))
    bounds = path.boundingRect()
    painter.translate(192, 192)
    scale = 340 / max(bounds.width(), bounds.height())
    painter.scale(scale, scale)
    painter.drawPath(path)
    painter.end()
    pixels = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(384, image.bytesPerLine())
    components, _ = cv2.connectedComponents((pixels[:, :384] > 127).astype(np.uint8))
    # Label 0 is the cut geometry; every retained white area joins the exterior.
    assert components == 2
