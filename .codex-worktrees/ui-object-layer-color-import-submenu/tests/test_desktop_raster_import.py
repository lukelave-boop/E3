from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.project import (
    AddObjectCommand,
    CommandStack,
    LayerMode,
    ObjectKind,
    ProjectDocument,
    SceneObject,
)


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _write_image(path: Path, *, width: int, height: int, value: int) -> None:
    pixels = np.full((height, width, 3), value, dtype=np.uint8)
    assert cv2.imwrite(str(path), pixels)


def _harness(*, with_existing_object: bool = False) -> SimpleNamespace:
    document = ProjectDocument.new()
    history = CommandStack()
    notices: list[str] = []
    errors: list[str] = []
    selection: list[str] = []
    if with_existing_object:
        existing = SceneObject.rectangle(
            document.active_layer_id,
            name="Existing selection",
            center=(25.0, 30.0),
        )
        document.add_object(existing)
        selection.append(existing.id)

    authoring_state = {"tool": "rectangle", "point_pick": "pending"}
    selection_tool_activations: list[bool] = []

    def select_objects(object_ids: list[str]) -> None:
        selection[:] = object_ids

    def add_object(item: SceneObject, description: str) -> None:
        history.execute(AddObjectCommand(document, item, description=description))
        select_objects([item.id])

    return SimpleNamespace(
        document=document,
        history=history,
        active_layer_id=document.active_layer_id,
        workspace=SimpleNamespace(select_objects=select_objects, selection=selection),
        _document_center=lambda: (110.0, 110.0),
        _add_object=add_object,
        show_notice=notices.append,
        show_error=errors.append,
        notices=notices,
        errors=errors,
        authoring_state=authoring_state,
        selection_tool_activations=selection_tool_activations,
    )


def _state_snapshot(harness: SimpleNamespace) -> dict[str, object]:
    return {
        "document": harness.document.to_dict(),
        "active_layer_id": harness.active_layer_id,
        "history": (
            harness.history.depth,
            harness.history.can_undo,
            harness.history.can_redo,
            harness.history.undo_text,
            harness.history.redo_text,
            harness.history.is_clean,
        ),
        "selection": list(harness.workspace.selection),
        "authoring": dict(harness.authoring_state),
        "selection_tool_activations": list(harness.selection_tool_activations),
    }


def test_desktop_raster_review_preserves_result_and_existing_undo_semantics(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    filename = tmp_path / "engraving.png"
    _write_image(filename, width=12, height=6, value=83)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    harness = _harness()
    events: list[str] = []
    reviewed_sha256: list[str] = []
    real_scan = main_window_module.scan_raster_file
    real_read = main_window_module.read_raster_asset_payload

    def scan(path: str):
        events.append("scan")
        return real_scan(path)

    def review(manifest, parent) -> bool:
        events.append("review")
        assert parent is harness
        assert manifest.ready_for_parse
        assert len(manifest.source_sha256) == 64
        assert manifest.source_facts
        reviewed_sha256.append(manifest.source_sha256)
        return True

    def read(path: str, *, expected_source_sha256: str):
        events.append("load")
        assert expected_source_sha256 == reviewed_sha256[-1]
        return real_read(path, expected_source_sha256=expected_source_sha256)

    monkeypatch.setattr(main_window_module, "scan_raster_file", scan)
    monkeypatch.setattr(main_window_module, "review_import_manifest", review)
    monkeypatch.setattr(main_window_module, "read_raster_asset_payload", read)
    initial_layer_ids = [layer.id for layer in harness.document.layers]

    E3MainWindow.import_image(harness)

    assert events == ["scan", "review", "load"]
    assert harness.errors == []
    imported_layers = [
        layer for layer in harness.document.layers if layer.id not in initial_layer_ids
    ]
    assert len(imported_layers) == 1
    assert imported_layers[0].mode == LayerMode.RASTER
    assert imported_layers[0].output_enabled is False
    assert harness.active_layer_id == imported_layers[0].id
    assert len(harness.document.objects) == 1
    item = harness.document.objects[0]
    assert item.kind == ObjectKind.IMAGE
    assert item.layer_id == imported_layers[0].id
    assert item.transform.x_mm == pytest.approx(110.0)
    assert item.transform.y_mm == pytest.approx(110.0)
    assert item.transform.width_mm == pytest.approx(80.0)
    assert item.transform.height_mm == pytest.approx(40.0)
    assert item.geometry["asset"] == str(filename.resolve())
    assert harness.workspace.selection == [item.id]
    assert harness.authoring_state == {
        "tool": "rectangle",
        "point_pick": "pending",
    }
    assert harness.selection_tool_activations == []
    assert harness.history.depth == 2
    assert harness.history.undo_text == "Import raster image"
    assert "deterministic ordered dithering" in harness.notices[-1]

    assert harness.history.undo()
    assert harness.document.objects == []
    assert len(harness.document.layers) == len(initial_layer_ids) + 1
    assert harness.history.undo_text == "Add raster layer"

    assert harness.history.undo()
    assert [layer.id for layer in harness.document.layers] == initial_layer_ids
    assert harness.history.redo_text == "Add raster layer"

    assert harness.history.redo()
    assert len(harness.document.layers) == len(initial_layer_ids) + 1
    assert harness.document.objects == []
    assert harness.history.redo_text == "Import raster image"

    assert harness.history.redo()
    assert len(harness.document.objects) == 1
    assert harness.document.objects[0].id == item.id
    assert harness.history.depth == 2


def test_desktop_raster_blocker_never_reaches_strict_probe_and_changes_nothing(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    filename = tmp_path / "invalid.png"
    filename.write_bytes(b"not a supported raster image")
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    reviewed = []

    def review(manifest, _parent) -> bool:
        reviewed.append(manifest)
        return True

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("Blocked raster must not reach strict probe")

    monkeypatch.setattr(main_window_module, "review_import_manifest", review)
    monkeypatch.setattr(
        main_window_module,
        "read_raster_asset_payload",
        unexpected_read,
    )
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_image(harness)

    assert len(reviewed) == 1
    assert not reviewed[0].ready_for_parse
    assert reviewed[0].errors or reviewed[0].unsupported_features
    assert _state_snapshot(harness) == before
    assert harness.errors == []
    assert harness.notices == []


def test_desktop_raster_review_cancel_preserves_complete_authoring_state(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    filename = tmp_path / "cancel.png"
    _write_image(filename, width=8, height=5, value=127)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    reviewed = []

    def cancel_review(manifest, _parent) -> bool:
        reviewed.append(manifest)
        return False

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("Cancelled raster must not reach strict probe")

    monkeypatch.setattr(main_window_module, "review_import_manifest", cancel_review)
    monkeypatch.setattr(
        main_window_module,
        "read_raster_asset_payload",
        unexpected_read,
    )
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_image(harness)

    assert len(reviewed) == 1
    assert reviewed[0].ready_for_parse
    assert _state_snapshot(harness) == before
    assert harness.errors == []
    assert harness.notices == []


def test_desktop_raster_source_changed_after_approval_preserves_all_state(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    filename = tmp_path / "changed-after-review.bmp"
    replacement = tmp_path / "replacement.bmp"
    _write_image(filename, width=9, height=7, value=20)
    _write_image(replacement, width=9, height=7, value=220)
    changed = replacement.read_bytes()
    assert len(filename.read_bytes()) == len(changed)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )

    def replace_source_after_approval(manifest, _parent) -> bool:
        assert manifest.ready_for_parse
        assert len(manifest.source_sha256) == 64
        filename.write_bytes(changed)
        return True

    monkeypatch.setattr(
        main_window_module,
        "review_import_manifest",
        replace_source_after_approval,
    )
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_image(harness)

    assert _state_snapshot(harness) == before
    assert harness.notices == []
    assert len(harness.errors) == 1
    assert "changed after import review" in harness.errors[0].casefold()


def test_desktop_raster_strict_probe_remains_authoritative_after_approval(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    filename = tmp_path / "strict.png"
    _write_image(filename, width=8, height=5, value=64)
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    monkeypatch.setattr(
        main_window_module,
        "review_import_manifest",
        lambda manifest, _parent: manifest.ready_for_parse,
    )

    def reject_strictly(*_args, **_kwargs):
        raise ValueError("Strict raster probe sentinel rejection")

    monkeypatch.setattr(
        main_window_module,
        "read_raster_asset_payload",
        reject_strictly,
    )
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_image(harness)

    assert _state_snapshot(harness) == before
    assert harness.notices == []
    assert len(harness.errors) == 1
    assert "strict raster probe sentinel rejection" in harness.errors[0].casefold()
