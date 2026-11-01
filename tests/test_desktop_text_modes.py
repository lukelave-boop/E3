from __future__ import annotations

import copy
import math
import string
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.desktop.qt import QtCore, QtWidgets
from laser_aligner.desktop.text_creation import CreateTextCommand
from laser_aligner.desktop.text_dialog import VectorTextDialog
from laser_aligner.desktop.text_geometry import (
    TextVectorOptions,
    build_vector_text_path,
    create_vector_text_object,
)
from laser_aligner.errors import SafetyError
from laser_aligner.machine.service import MachineService
from laser_aligner.project import (
    CommandStack,
    LayerMode,
    ProjectDocument,
    SceneObject,
    generate_project_gcode,
    load_project,
    save_project,
)
from laser_aligner.project.stroke_font import STROKE_FONT_NAME, stroke_text_lines
from laser_aligner.project.toolpath import _scanline_rows, object_polylines


@pytest.fixture(scope="module", autouse=True)
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # Offscreen Windows does not always discover installed fonts automatically.
    from test_desktop_stencil_text import _usable_test_font

    _usable_test_font()
    yield app


def options(mode="single_line", text="E3", height=20):
    return TextVectorOptions(text, "Arial", height, mode=mode)


@pytest.mark.parametrize("character", string.ascii_letters + string.digits + string.punctuation)
def test_every_stroke_glyph_produces_finite_nonzero_segments(character):
    strokes = stroke_text_lines(character)
    assert strokes
    item = create_vector_text_object("line", options(text=character), center=(50, 50))
    assert item.metadata["text_font_family"] == STROKE_FONT_NAME
    for path in object_polylines(item):
        assert all(math.dist(a, b) > 0 for a, b in zip(path.points, path.points[1:], strict=False))
    for stroke in strokes:
        assert len(stroke) >= 2
        assert all(start != end for start, end in zip(stroke, stroke[1:], strict=False))


def test_single_line_e_has_no_implicit_closing_or_double_outline():
    item = create_vector_text_object("line", options(text="E", height=6), center=(0, 0))
    paths = object_polylines(item)
    assert len(paths) == 2
    assert all(not path.closed for path in paths)
    assert sum(math.dist(a, b) for path in paths for a, b in zip(path.points, path.points[1:], strict=False)) == pytest.approx(17)


def test_multiline_stroke_text_centers_each_line_and_preserves_case():
    paths = stroke_text_lines("AB\na")
    assert min(x for x, y in paths[-2] if y >= 10) == 3
    assert stroke_text_lines("a") != stroke_text_lines("A")
    assert stroke_text_lines("A\tB") == stroke_text_lines("A   B")


@pytest.mark.parametrize("text", ["", " \t\n", "Café", "E3🔥", "A\x00"])
def test_invalid_stroke_text_leaves_document_and_history_untouched(text):
    document = ProjectDocument.new()
    before = document.to_dict()
    history = CommandStack()
    with pytest.raises(ValueError):
        history.execute(CreateTextCommand(document, document.active_layer_id, options(text=text), center=(50, 50)))
    assert document.to_dict() == before
    assert history.depth == 0


@pytest.mark.parametrize("mode", ["filled", "outline_filled"])
def test_fill_reuses_exact_outline_and_keeps_counter_empty(mode):
    outline, _, _ = build_vector_text_path(options("outline", "O"))
    path, _, count = build_vector_text_path(options(mode, "O"))
    assert path == outline
    assert count == 0
    assert not path.contains(QtCore.QPointF(0, 0))
    document = ProjectDocument.new()
    command = CreateTextCommand(document, document.active_layer_id, options(mode, "O"), center=(50, 50))
    command.redo()
    raster = document.get_layer(command.items[0].layer_id)
    rows = _scanline_rows(command.items[0], raster)
    middle = min(rows, key=lambda row: abs(row.points[0, 1] - 50))
    assert len(middle.spans) == 2
    assert all(not (min(span.points[:, 0]) < 50 < max(span.points[:, 0])) for span in middle.spans)


def test_combined_text_is_grouped_ordered_atomic_and_persistent(tmp_path):
    document = ProjectDocument.new()
    active = document.layers[0]
    active.priority = 37
    existing = SceneObject.rectangle(active.id, center=(150, 150), width_mm=10, height_mm=10)
    document.add_object(existing)
    original_layers = [layer.to_dict() for layer in document.layers]
    original_objects = [item.to_dict() for item in document.objects]
    command = CreateTextCommand(document, active.id, options("outline_filled"), center=(50, 50))
    assert [layer.to_dict() for layer in document.layers] == original_layers
    history = CommandStack()
    history.execute(command)
    assert history.depth == 1
    assert [layer.mode for layer in command.layers] == [LayerMode.RASTER, LayerMode.LINE]
    assert [layer.priority for layer in command.layers] == [38, 39]
    assert all(not layer.output_enabled and layer.power_percent == 0 for layer in command.layers)
    fill, outline = command.items
    assert fill.group_id and fill.group_id == outline.group_id
    assert fill.geometry == outline.geometry
    assert fill.transform == outline.transform
    save_project(document, tmp_path / "text.e3laser")
    loaded = load_project(tmp_path / "text.e3laser")
    assert [item.to_dict() for item in loaded.objects] == [item.to_dict() for item in document.objects]
    assert [layer.to_dict() for layer in loaded.layers] == [layer.to_dict() for layer in document.layers]
    history.undo()
    assert [layer.to_dict() for layer in document.layers] == original_layers
    assert [item.to_dict() for item in document.objects] == original_objects
    history.redo()
    assert document.group_members(fill.group_id) == command.items


@pytest.mark.parametrize("mode,layer_mode", [("single_line", LayerMode.LINE), ("filled", LayerMode.RASTER)])
def test_matching_active_operation_is_preserved(mode, layer_mode):
    document = ProjectDocument.new()
    document.layers[0].mode = layer_mode
    before = document.layers[0].to_dict()
    command = CreateTextCommand(document, document.active_layer_id, options(mode), center=(50, 50))
    command.redo()
    assert not command.layers
    assert command.items[0].name == "E3"
    assert document.layers[0].to_dict() == before
    assert command.items[0].layer_id == document.active_layer_id


def test_stroke_on_raster_layer_gets_separate_line_operation():
    document = ProjectDocument.new()
    document.layers[0].mode = LayerMode.RASTER
    before = document.layers[0].to_dict()
    command = CreateTextCommand(document, document.active_layer_id, options(), center=(50, 50))
    command.redo()
    assert document.layers[0].to_dict() == before
    assert document.get_layer(command.items[0].layer_id).mode is LayerMode.LINE


@pytest.mark.parametrize("mode", ["single_line", "filled", "outline_filled"])
def test_text_program_uses_existing_guards_and_rejects_out_of_bounds(mode):
    document = ProjectDocument.new()
    command = CreateTextCommand(document, document.active_layer_id, options(mode), center=(50, 50))
    command.redo()
    for layer in document.layers:
        layer.output_enabled = True
        layer.power_percent = 10
    laser = LaserSettings(power_max=1000)
    job = generate_project_gcode(document, laser)
    assert "G21" in job.text and "G90" in job.text
    assert "M4" in job.text
    if mode == "outline_filled":
        assert job.text.index("; Layer E3 fill") < job.text.index("; Layer E3 line")
    service = MachineService(MachineSettings(backend="serial", work_area=document.work_area), laser)
    with pytest.raises(SafetyError, match="machine.allow_motion"):
        service.validate_program(job.text)
    # Pure program validation: no connect or hardware command is performed.
    service = MachineService(
        MachineSettings(backend="serial", allow_motion=True, work_area=document.work_area),
        laser,
        hardware_enabled=True,
    )
    service.validate_program(job.text)
    for item in command.items:
        item.transform.x_mm = -100
    with pytest.raises(SafetyError, match="work area"):
        generate_project_gcode(document, laser)


def test_open_single_line_cannot_be_silently_raster_filled():
    document = ProjectDocument.new()
    command = CreateTextCommand(document, document.active_layer_id, options(text="E"), center=(50, 50))
    command.redo()
    document.layers[0].mode = LayerMode.RASTER
    with pytest.raises(ValueError, match="closed vector geometry"):
        generate_project_gcode(document, LaserSettings())


def test_text_dialog_switches_fonts_bridges_and_rejects_unsupported_strokes():
    dialog = VectorTextDialog()
    ok = dialog.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("single_line"))
    assert ok.isEnabled()
    assert not dialog.font_combo.isEnabled()
    assert not dialog.auto_bridge.isEnabled()
    assert "E3 Single Stroke" in dialog.preview_status.text()
    assert dialog.options().font_family == STROKE_FONT_NAME
    dialog.text_edit.setPlainText("Café")
    assert not ok.isEnabled()
    assert "Unsupported" in dialog.preview_status.text()
    dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("outline_filled"))
    assert ok.isEnabled() and dialog.font_combo.isEnabled()
    assert "Raster first" in dialog.preview_status.text()
    dialog.text_edit.setPlainText("E3")
    for mode in ("outline", "stencil", "single_line", "filled", "outline_filled"):
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData(mode))
        assert ok.isEnabled()
        assert dialog.preview.pixmap() and not dialog.preview.pixmap().isNull()
    dialog.close()
    dialog.deleteLater()


def test_layer_insertion_failure_rolls_back_added_layers(monkeypatch):
    document = ProjectDocument.new()
    command = CreateTextCommand(document, document.active_layer_id, options("outline_filled"), center=(50, 50))
    layers = copy.deepcopy(document.layers)

    def fail():
        raise ValueError("injected failure")

    monkeypatch.setattr(command._commands[-1], "redo", fail)
    with pytest.raises(ValueError, match="injected failure"):
        command.redo()
    assert document.layers == layers
    assert not document.objects


@pytest.mark.parametrize("mode", ["single_line", "filled", "outline_filled"])
@pytest.mark.parametrize("accept", [False, True])
def test_main_window_text_action_commits_only_on_accept(monkeypatch, mode, accept):
    import laser_aligner.desktop.main_window as window

    document = ProjectDocument.new()
    selected, notices, errors = [], [], []
    harness = SimpleNamespace(
        document=document,
        active_layer_id=document.active_layer_id,
        history=CommandStack(),
        _activate_selection_tool=lambda **kwargs: None,
        _document_center=lambda: (50, 50),
        workspace=SimpleNamespace(select_objects=selected.extend),
        show_notice=notices.append,
        show_error=errors.append,
    )
    dialog = SimpleNamespace(
        exec=lambda: QtWidgets.QDialog.DialogCode.Accepted if accept else QtWidgets.QDialog.DialogCode.Rejected,
        options=lambda: options(mode),
    )
    monkeypatch.setattr(window, "VectorTextDialog", lambda parent: dialog)
    before = document.to_dict()
    window.E3MainWindow.add_text(harness)
    assert not errors
    if accept:
        assert selected == [item.id for item in document.objects]
        assert harness.history.depth == 1
        assert len(selected) == (2 if mode == "outline_filled" else 1)
    else:
        assert document.to_dict() == before
        assert harness.history.depth == 0
        assert not selected
