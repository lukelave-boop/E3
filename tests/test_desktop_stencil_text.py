from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from laser_aligner.desktop.qt import QtWidgets
from laser_aligner.desktop.text_geometry import (
    TextVectorOptions,
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


def test_auto_bridge_width_scales_with_text_height() -> None:
    assert automatic_bridge_width(5.0) == pytest.approx(0.8)
    assert automatic_bridge_width(25.0) == pytest.approx(2.0)
    assert automatic_bridge_width(100.0) == pytest.approx(4.0)


def test_stencil_text_bridges_enclosed_letter_counters() -> None:
    options = TextVectorOptions(
        text="OAR8",
        font_family="Arial",
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
            font_family="Arial",
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
