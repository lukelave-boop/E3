from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.project import CommandStack, OperationLayer, ProjectDocument


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _harness() -> SimpleNamespace:
    document = ProjectDocument.new()
    history = CommandStack()
    notices: list[str] = []
    errors: list[str] = []
    selected: list[list[str]] = []
    return SimpleNamespace(
        document=document,
        history=history,
        active_layer_id=document.active_layer_id,
        workspace=SimpleNamespace(select_objects=selected.append),
        _document_center=lambda: (110.0, 110.0),
        _activate_selection_tool=lambda **_kwargs: None,
        show_notice=notices.append,
        show_error=errors.append,
        notices=notices,
        errors=errors,
        selected=selected,
    )


def test_desktop_gcode_import_is_one_undoable_output_disabled_operation(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "laser-job.gc"
    filename.write_text(
        """
        ; S-value max: 1000
        G21 G90 M4
        G1 F1200 S500 X20 Y0
        Y10
        M5
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *_args, **_kwargs: None)
    harness = _harness()
    initial_layer_ids = [layer.id for layer in harness.document.layers]

    E3MainWindow.import_gcode(harness)

    assert harness.errors == []
    imported_layers = [
        layer for layer in harness.document.layers if layer.id not in initial_layer_ids
    ]
    assert len(imported_layers) == 1
    assert imported_layers[0].output_enabled is False
    assert len(harness.document.objects) == 1
    assert harness.document.objects[0].layer_id == imported_layers[0].id
    assert harness.history.can_undo
    assert "output-disabled" in harness.notices[-1]
    assert harness.selected[-1] == [harness.document.objects[0].id]

    harness.history.undo()
    assert harness.document.objects == []
    assert [layer.id for layer in harness.document.layers] == initial_layer_ids

    harness.history.redo()
    assert len(harness.document.objects) == 1
    assert len(harness.document.layers) == len(initial_layer_ids) + 1


def test_desktop_gcode_import_failure_changes_nothing(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "unsafe.gc"
    filename.write_text("G21 G90\nG92 X0\n", encoding="utf-8")
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    harness = _harness()
    original_layers = [
        OperationLayer.from_dict(layer.to_dict()) for layer in harness.document.layers
    ]

    E3MainWindow.import_gcode(harness)

    assert harness.document.objects == []
    assert [layer.to_dict() for layer in harness.document.layers] == [
        layer.to_dict() for layer in original_layers
    ]
    assert len(harness.errors) == 1
    assert "G92" in harness.errors[0]
    assert not harness.history.can_undo
