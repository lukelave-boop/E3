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

from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.errors import SvgError
from laser_aligner.project import (
    AddObjectCommand,
    CommandStack,
    ProjectDocument,
    SceneObject,
)
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
        _document_center=lambda: (95.0, 95.0),
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
def test_desktop_svg_review_preserves_physical_result_and_one_step_undo(
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
    harness = _harness()
    events: list[str] = []
    reviewed_sha256: list[str] = []
    real_scan = main_window_module.scan_svg_file
    real_load = main_window_module.load_svg_project

    def scan(path: str):
        events.append("scan")
        return real_scan(path)

    def review(manifest, parent) -> bool:
        events.append("review")
        assert parent is harness
        assert manifest.ready_for_parse
        assert len(manifest.source_sha256) == 64
        assert manifest.layers
        assert manifest.source_facts
        assert manifest.coordinate_facts
        reviewed_sha256.append(manifest.source_sha256)
        return True

    def load(path: str, *, expected_source_sha256: str):
        events.append("load")
        assert expected_source_sha256 == reviewed_sha256[-1]
        return real_load(path, expected_source_sha256=expected_source_sha256)

    monkeypatch.setattr(main_window_module, "scan_svg_file", scan)
    monkeypatch.setattr(main_window_module, "review_import_manifest", review)
    monkeypatch.setattr(main_window_module, "load_svg_project", load)
    initial_active_layer_id = harness.active_layer_id

    E3MainWindow.import_svg(harness)

    assert events == ["scan", "review", "load"]
    assert harness.errors == []
    assert len(harness.document.objects) == 1
    item = harness.document.objects[0]
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
    assert item.metadata["source_svg"] == filename.read_bytes().decode("utf-8")
    assert harness.active_layer_id == initial_active_layer_id
    assert harness.history.depth == 1
    assert harness.history.undo_text == "Import SVG"
    assert harness.workspace.selection == [item.id]
    assert harness.authoring_state == {
        "tool": "rectangle",
        "point_pick": "pending",
    }

    assert harness.history.undo()
    assert harness.document.objects == []
    assert harness.active_layer_id == initial_active_layer_id
    assert harness.history.redo_text == "Import SVG"

    assert harness.history.redo()
    assert len(harness.document.objects) == 1
    assert harness.document.objects[0].id == item.id
    assert harness.history.depth == 1


@pytest.mark.parametrize(
    "unsupported_content",
    [
        '<text x="2" y="4">not converted</text>',
        '<image href="part.png" width="5" height="5"/>',
        '<style>.cut { transform: scale(0.5); }</style>',
    ],
)
def test_desktop_svg_blocker_never_reaches_strict_import_and_changes_nothing(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    unsupported_content: str,
) -> None:
    filename = tmp_path / "blocked.svg"
    filename.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect class="cut" width="10" height="10"/>'
        f"{unsupported_content}</svg>",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    reviewed = []

    def review(manifest, _parent) -> bool:
        reviewed.append(manifest)
        return True

    def unexpected_load(*_args, **_kwargs):
        raise AssertionError("Blocked SVG must not reach strict import")

    monkeypatch.setattr(main_window_module, "review_import_manifest", review)
    monkeypatch.setattr(main_window_module, "load_svg_project", unexpected_load)
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_svg(harness)

    assert len(reviewed) == 1
    assert not reviewed[0].ready_for_parse
    assert reviewed[0].unsupported_features or reviewed[0].errors
    assert _state_snapshot(harness) == before
    assert harness.errors == []
    assert harness.notices == []


def test_desktop_svg_review_cancel_preserves_complete_authoring_state(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "cancel.svg"
    filename.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect width="10" height="10"/></svg>',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )
    reviewed = []

    def cancel_review(manifest, _parent) -> bool:
        reviewed.append(manifest)
        return False

    def unexpected_load(*_args, **_kwargs):
        raise AssertionError("Cancelled SVG must not reach strict import")

    monkeypatch.setattr(main_window_module, "review_import_manifest", cancel_review)
    monkeypatch.setattr(main_window_module, "load_svg_project", unexpected_load)
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_svg(harness)

    assert len(reviewed) == 1
    assert reviewed[0].ready_for_parse
    assert _state_snapshot(harness) == before
    assert harness.errors == []
    assert harness.notices == []


def test_desktop_svg_source_changed_after_approval_preserves_all_state(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "changed-after-review.svg"
    original = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10">'
        '<rect width="10" height="10"/></svg>'
    )
    changed = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10">'
        '<rect width="20" height="10"/></svg>'
    )
    assert len(original.encode("utf-8")) == len(changed.encode("utf-8"))
    filename.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        QtWidgets.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(filename), ""),
    )

    def replace_source_after_approval(manifest, _parent) -> bool:
        assert manifest.ready_for_parse
        assert len(manifest.source_sha256) == 64
        filename.write_text(changed, encoding="utf-8")
        return True

    monkeypatch.setattr(
        main_window_module,
        "review_import_manifest",
        replace_source_after_approval,
    )
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_svg(harness)

    assert _state_snapshot(harness) == before
    assert harness.notices == []
    assert len(harness.errors) == 1
    assert "changed after import review" in harness.errors[0].casefold()


def test_desktop_svg_strict_parser_remains_authoritative_after_approval(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "strict.svg"
    filename.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect width="10" height="10"/></svg>',
        encoding="utf-8",
    )
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
        raise SvgError("Strict SVG parser sentinel rejection")

    monkeypatch.setattr(main_window_module, "load_svg_project", reject_strictly)
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_svg(harness)

    assert _state_snapshot(harness) == before
    assert harness.notices == []
    assert len(harness.errors) == 1
    assert "strict svg parser sentinel rejection" in harness.errors[0].casefold()
