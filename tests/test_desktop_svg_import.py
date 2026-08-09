from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from types import SimpleNamespace

import numpy as np
import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.project.toolpath import object_polylines


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _bounds(polylines) -> tuple[float, float, float, float]:
    points = np.vstack([line.points for line in polylines])
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    return minimum[0], minimum[1], maximum[0], maximum[1]


def _import_harness() -> tuple[SimpleNamespace, list[tuple[object, str]], list[str]]:
    added: list[tuple[object, str]] = []
    errors: list[str] = []
    harness = SimpleNamespace(
        active_layer_id="layer-1",
        _document_center=lambda: (95.0, 95.0),
        _add_object=lambda item, description: added.append((item, description)),
        show_error=errors.append,
        show_notice=lambda _message: None,
    )
    return harness, added, errors


@pytest.mark.parametrize(
    ("dimensions", "expected_width", "expected_height"),
    [
        ('width="50.8mm" height="25.4mm"', 15.24, 10.16),
        ('width="5.08cm" height="2.54cm"', 15.24, 10.16),
        ('width="2in" height="1in"', 15.24, 10.16),
        ('width="192px" height="96px"', 15.24, 10.16),
        ("", 15.875, 10.583),
    ],
)
def test_desktop_import_applies_physical_size_and_preserves_placement(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    dimensions: str,
    expected_width: float,
    expected_height: float,
) -> None:
    filename = tmp_path / "physical.svg"
    filename.write_text(
        f"""
        <svg xmlns="http://www.w3.org/2000/svg" {dimensions}
             viewBox="0 0 200 100">
          <g transform="translate(7,11) scale(1.5,2)">
            <rect x="10" y="20" width="40" height="20"/>
          </g>
        </svg>
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    harness, added, errors = _import_harness()

    E3MainWindow.import_svg(harness)

    assert errors == []
    assert len(added) == 1
    item, description = added[0]
    assert description == "Import SVG"
    assert item.transform.x_mm == pytest.approx(95.0, abs=0.01)
    assert item.transform.y_mm == pytest.approx(95.0, abs=0.01)
    assert item.transform.width_mm == pytest.approx(expected_width, abs=0.01)
    assert item.transform.height_mm == pytest.approx(expected_height, abs=0.01)
    assert _bounds(object_polylines(item)) == pytest.approx(
        (
            95.0 - expected_width / 2.0,
            95.0 - expected_height / 2.0,
            95.0 + expected_width / 2.0,
            95.0 + expected_height / 2.0,
        ),
        abs=0.01,
    )
    assert item.metadata["source_name"] == "physical.svg"


@pytest.mark.parametrize(
    "unsupported_content",
    [
        '<text x="2" y="4">not converted</text>',
        '<image href="part.png" width="5" height="5"/>',
    ],
)
def test_desktop_import_rejects_lossy_warnings_before_adding_an_object(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    unsupported_content: str,
) -> None:
    filename = tmp_path / "incomplete.svg"
    filename.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect width="10" height="10"/>'
        f"{unsupported_content}</svg>",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    harness, added, errors = _import_harness()

    E3MainWindow.import_svg(harness)

    assert added == []
    assert len(errors) == 1
    assert "SVG import stopped because conversion would be incomplete" in errors[0]
    assert "Ignored unsupported elements" in errors[0]


def test_desktop_import_rejects_css_before_adding_an_object(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "styled.svg"
    filename.write_text(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
          <style>.cut { transform: scale(0.5); }</style>
          <rect class="cut" width="10" height="10"/>
        </svg>
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    harness, added, errors = _import_harness()

    E3MainWindow.import_svg(harness)

    assert added == []
    assert len(errors) == 1
    assert "unsupported rendering semantics" in errors[0]
    assert "CSS <style> rules" in errors[0]
