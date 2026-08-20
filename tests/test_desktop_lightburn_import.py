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

from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.project import CommandStack, ProjectDocument, SceneObject


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


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

    def activate_selection_tool(*, show_message: bool = True) -> None:
        selection_tool_activations.append(show_message)
        authoring_state.update({"tool": "select", "point_pick": "cancelled"})

    return SimpleNamespace(
        document=document,
        history=history,
        active_layer_id=document.active_layer_id,
        workspace=SimpleNamespace(select_objects=select_objects, selection=selection),
        _document_center=lambda: (110.0, 110.0),
        _activate_selection_tool=activate_selection_tool,
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


def test_desktop_import_adds_one_undoable_output_disabled_project(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "dad-project.lbrn2"
    filename.write_text(
        """
        <LightBurnProject AppVersion="1.7.08">
          <CutSetting type="Cut"><index Value="0"/><speed Value="10"/><maxPower Value="80"/></CutSetting>
          <Shape Type="Rect" CutIndex="0" W="20" H="10"><XForm>1 0 0 1 40 30</XForm></Shape>
        </LightBurnProject>
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
    events: list[str] = []
    real_scan = main_window_module.scan_lightburn_file
    real_load = main_window_module.load_lightburn_project

    def scan(path: str):
        events.append("scan")
        return real_scan(path)

    def review(manifest, parent) -> bool:
        events.append("review")
        assert parent is harness
        assert manifest.ready_for_parse
        assert manifest.layers
        assert manifest.coordinate_facts
        assert manifest.warnings or manifest.approximations
        return True

    def load(path: str, *, center: tuple[float, float]):
        events.append("load")
        return real_load(path, center=center)

    monkeypatch.setattr(main_window_module, "scan_lightburn_file", scan)
    monkeypatch.setattr(main_window_module, "review_import_manifest", review)
    monkeypatch.setattr(main_window_module, "load_lightburn_project", load)
    initial_layer_ids = [layer.id for layer in harness.document.layers]
    initial_active_layer_id = harness.active_layer_id

    E3MainWindow.import_lightburn(harness)

    assert events == ["scan", "review", "load"]
    assert harness.errors == []
    assert len(harness.document.objects) == 1
    imported_layers = [layer for layer in harness.document.layers if layer.id not in initial_layer_ids]
    assert len(imported_layers) == 1
    assert imported_layers[0].output_enabled is False
    assert harness.document.objects[0].layer_id == imported_layers[0].id
    assert harness.history.can_undo
    assert harness.history.depth == 1
    assert harness.history.undo_text == "Import LightBurn project"
    assert "review every setting" in harness.notices[-1]
    assert harness.workspace.selection == [harness.document.objects[0].id]
    assert harness.selection_tool_activations == [False]
    imported_active_layer_id = imported_layers[0].id
    assert harness.active_layer_id == imported_active_layer_id

    harness.history.undo()
    assert harness.document.objects == []
    assert [layer.id for layer in harness.document.layers] == initial_layer_ids
    assert harness.active_layer_id == initial_active_layer_id
    assert harness.history.redo_text == "Import LightBurn project"

    harness.history.redo()
    assert len(harness.document.objects) == 1
    assert len(harness.document.layers) == len(initial_layer_ids) + 1
    assert harness.active_layer_id == imported_active_layer_id
    assert harness.history.depth == 1


def test_desktop_lightburn_blocker_never_reaches_strict_import_and_changes_nothing(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "unsupported.lbrn"
    filename.write_text(
        '<LightBurnProject><Shape Type="Bitmap" CutIndex="0" W="2" H="2"/></LightBurnProject>',
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
        # Defense in depth: even a programmatic Accepted result cannot bypass
        # the manifest's blocked state.
        return True

    def unexpected_load(*_args, **_kwargs):
        raise AssertionError("Blocked LightBurn content must not reach strict import")

    monkeypatch.setattr(main_window_module, "review_import_manifest", review)
    monkeypatch.setattr(main_window_module, "load_lightburn_project", unexpected_load)
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_lightburn(harness)

    assert len(reviewed) == 1
    assert not reviewed[0].ready_for_parse
    assert any("vector-only" in item for item in reviewed[0].unsupported_features)
    assert _state_snapshot(harness) == before
    assert harness.errors == []
    assert harness.notices == []


def test_desktop_lightburn_review_cancel_preserves_complete_authoring_state(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "cancel.lbrn2"
    filename.write_text(
        """
        <LightBurnProject>
          <CutSetting type="Cut"><index Value="0"/></CutSetting>
          <Shape Type="Rect" CutIndex="0" W="20" H="10"/>
        </LightBurnProject>
        """,
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
        raise AssertionError("Cancelled LightBurn content must not reach strict import")

    monkeypatch.setattr(main_window_module, "review_import_manifest", cancel_review)
    monkeypatch.setattr(main_window_module, "load_lightburn_project", unexpected_load)
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_lightburn(harness)

    assert len(reviewed) == 1
    assert reviewed[0].ready_for_parse
    assert _state_snapshot(harness) == before
    assert harness.errors == []
    assert harness.notices == []


def test_desktop_lightburn_strict_import_remains_authoritative_after_approval(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    filename = tmp_path / "strict-shape.lbrn2"
    filename.write_text(
        """
        <LightBurnProject>
          <CutSetting type="Cut"><index Value="0"/></CutSetting>
          <Shape Type="Rect" CutIndex="0" W="not-a-number" H="10"/>
        </LightBurnProject>
        """,
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
    harness = _harness(with_existing_object=True)
    before = _state_snapshot(harness)

    E3MainWindow.import_lightburn(harness)

    assert _state_snapshot(harness) == before
    assert len(harness.errors) == 1
    assert "width" in harness.errors[0].casefold()
